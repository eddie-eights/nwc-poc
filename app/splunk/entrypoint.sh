#!/bin/bash
# Splunk のコンテナ（docker/images/splunk/Dockerfile）の入口。アラートアクション（netops_alerts/bin/netops_sns.py）が要る環境変数をファイルに写してから、上流の入口を起こす。
# splunkd は上流の Ansible が sudo で splunk ユーザーとして起こすので、コンテナの環境変数を引き継がない（アラートアクションは splunkd の子）。
# ECS のタスク定義の環境変数（IaC/terraform/aws-managed/pipeline/analytics の splunk.tf）:
#   AWS_REGION        SNS のリージョン
#   ALERTS_TOPIC_ARN  アラートを publish する SNS のトピック（IaC/terraform/aws-managed/base/core の alerts.tf）
#   DEVICE_MAP        管理 IP などの別名 → 機器名（ops/up.sh が app/containerlab/lab_topology.py --device-map から作る。gNMI と trap は機器名でなく IP を持つ）
#   AWS_CONTAINER_CREDENTIALS_RELATIVE_URI  ECS が入れる。タスクロールの一時的な認証情報の取り出し口（鍵そのものではない）
# AWS_CONTAINER_CREDENTIALS_FULL_URI と AWS_ENDPOINT_URL_SNS はローカルで偽の SNS に向けて試すときだけ使う（AWS SDK と同じ名前）
set -eu
ENV_FILE="${NETOPS_ALERTS_ENV:-/opt/container_artifact/nwc-alerts.env}"
umask 022
: > "$ENV_FILE"
for k in AWS_REGION ALERTS_TOPIC_ARN DEVICE_MAP AWS_CONTAINER_CREDENTIALS_RELATIVE_URI AWS_CONTAINER_CREDENTIALS_FULL_URI AWS_ENDPOINT_URL_SNS; do
  printf '%s=%s\n' "$k" "${!k:-}" >> "$ENV_FILE"
done
# クラスター（SPLUNK_AZ_NUM が 2 か 3。上流の SPLUNK_ROLE で役割を分ける）のとき、保存済みサーチ（app netops_alerts）は search head だけで動かす。
# manager と indexer でも動くと、同じアラートが台の数だけ SNS に出る。上流の入口が /opt/splunk-etc を /opt/splunk/etc へ写す前に消す
# （/opt/splunk-etc は splunk ユーザーのもの。入口は ansible ユーザーで動き、sudo できる）
case "${SPLUNK_ROLE:-splunk_standalone}" in
  splunk_standalone|splunk_search_head) ;;
  *) sudo -n -u splunk rm -rf /opt/splunk-etc/apps/netops_alerts ;;
esac
# クラスターの indexer は、止められる（ECS・docker stop の SIGTERM）と先に splunk offline を打つ。manager が bucket の primary を残りの indexer へ
# 付け替えてから splunkd が止まるので、search head の検索が欠けない（「Splunk をクラスターにする（004）」のリスク 11。いきなり止めると、manager が
# 次の世代を約 5 分確定できず、search head は止まった indexer を primary に持つ古い世代のまま 0 件を返す）。そのため indexer だけは上流の入口を
# exec せず子として起こし、SIGTERM をここで受ける。手元では offline は 45〜47 秒で終わった（付け替えと世代の確定は最初の数秒で、残りは splunkd が
# 止まる時間）。OFFLINE_TIMEOUT 秒で終わらなければ打ち切る。どちらでも最後に上流の入口へ SIGTERM を回し、いつもどおり splunk stop させる（offline で
# 止まっていればすぐ終わる）。ECS の stopTimeout は 120 秒（splunk.tf）で、過ぎると SIGKILL。打ち切ったあとの splunk stop（手元で 47 秒）も収まる値にする。
# admin のパスワードは offline の引数で渡す（CLI は標準入力から読まない。見えるのはこのコンテナの中の ps だけ）
OFFLINE_TIMEOUT=60
if [ "${SPLUNK_ROLE:-}" = splunk_indexer ]; then
  child=""
  on_term() {
    trap '' TERM INT
    local t0=$SECONDS rc=0
    echo "nwc-offline: start"
    timeout "$OFFLINE_TIMEOUT" sudo -n -u splunk /opt/splunk/bin/splunk offline -auth "admin:${SPLUNK_PASSWORD:-}" < /dev/null || rc=$?
    echo "nwc-offline: rc=$rc $((SECONDS - t0))s"
    [ -n "$child" ] || exit 143
    kill -TERM "$child" 2> /dev/null || true
    rc=0
    wait "$child" || rc=$?
    exit "$rc"
  }
  trap on_term TERM INT
  /sbin/entrypoint.sh "$@" &
  child=$!
  rc=0
  wait "$child" || rc=$?
  exit "$rc"
fi
exec /sbin/entrypoint.sh "$@"
