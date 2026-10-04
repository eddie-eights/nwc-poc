#!/bin/bash
# Splunk のコンテナ（splunk/Dockerfile）の入口。アラートアクション（netops_alerts/bin/netops_sns.py）が要る環境変数をファイルに写してから、上流の入口を起こす。
# splunkd は上流の Ansible が sudo で splunk ユーザーとして起こすので、コンテナの環境変数を引き継がない（アラートアクションは splunkd の子）。
# ECS のタスク定義の環境変数（terraform/pipeline/analytics の splunk.tf）:
#   AWS_REGION        SNS のリージョン
#   ALERTS_TOPIC_ARN  アラートを publish する SNS のトピック（terraform/base/core の alerts.tf）
#   DEVICE_MAP        管理 IP などの別名 → 機器名（ops/up.sh が lab/lab_topology.py --device-map から作る。gNMI と trap は機器名でなく IP を持つ）
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
exec /sbin/entrypoint.sh "$@"
