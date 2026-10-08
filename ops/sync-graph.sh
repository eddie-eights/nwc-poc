#!/usr/bin/env bash
# lab の定義（app/containerlab/splab.clab.yml.in + app/containerlab/srlinux/*.cli）から作ったトポロジをグラフ DB（マネージド版は Neptune、OSS 版は Neo4j）に入れる。
# ops/up.sh（OSS 版は ops/oss/up.sh）の 7-3b と同じ処理を単独で打つ版。
# 設計の「静的なトポロジ構成の同期（初期 & 定期ロード）」の、定期ロードのほう。lab を変えたら打つ（cron で回してもよい）。
#
# 使い方（展開したフォルダの直下で。ops/up.sh と同じ deploy.env と AWS の認証情報）:
#   ops/sync-graph.sh                  # 空のときだけ入れる（初期ロード。入っていれば何もしない）
#   ops/sync-graph.sh --replace        # 入っていても入れ直す（Web で編集した内容と、アラートで付いた status は消えて lab の定義に戻る）
#   ops/sync-graph.sh --dry-run        # グラフ DB には触らず、lab から作ったトポロジ（JSON）を出すだけ
#   ops/sync-graph.sh --oss [--replace]  # OSS 版（cycle 005。接頭辞 <owner>-nwc-oss、IaC/terraform/oss/ の state、Neo4j）に入れる。
#                                      # Neo4j はタスクが入れ替わるとグラフが空に戻る。起こし直したあとは 2 段で戻す:
#                                      #   1. これ（--oss）で lab の定義から物理層・IP 層・EVPN と BGP 層を入れる（変更履歴は戻らない）
#                                      #   2. Nautobot の Job「Telegraf とグラフ DB に同期」を打って、変更履歴と Nautobot で足した機器と回線を戻す
#                                      #   順番はこの順。先に Job を打つと機器が入って空でなくなり、--replace なしのこれは何もしない
#                                      #   （2026-10-08 に AWS で確認。1 のあと変更履歴は 0 件のまま、2 で 19 件に戻った）
#
# 物理層の正は Nautobot（IaC/terraform/aws-managed/pipeline/nautobot。PIPELINE=1 ならいつも立つ）。--replace は lab の定義で上書きするので、Nautobot で足した機器と回線は
# グラフ DB から消える（Nautobot の Job「Telegraf とグラフ DB に同期」を打てば戻る。OSS 版は同じ Job が Neo4j に書く（2026-10-08 に AWS で確認）。
# IP 層と EVPN・BGP 層は Nautobot に無いので lab からだけ入る）。
#
# base/core（Web の EC2）と pipeline/graph（Neptune か Neo4j）が出来ていることが前提。Web の EC2 の上で ops/seed_graph.py を SSM Run Command で動かす
# （どちらに入れるかは Web の環境変数 GRAPH_BACKEND で決まり、ops/seed_graph.py が app/agentcore/graph.py 経由で切り替える）。
set -uo pipefail

cd "$(dirname "$0")/.."
REPLACE=""; DRY=""; OSS=""
for a in "$@"; do
  case "$a" in
    --replace) REPLACE=1 ;;
    --dry-run) DRY=1 ;;
    --oss) OSS=1 ;;
    *) echo "使い方: ops/sync-graph.sh [--oss] [--replace] [--dry-run]" >&2; exit 2 ;;
  esac
done

die() { echo "NG: $*" >&2; exit 1; }
# shellcheck source=ops/deploy-env.sh
. ops/deploy-env.sh
load_deploy_env >&2   # 読んだファイルとキーの案内は stderr へ（--dry-run の stdout を JSON だけにして jq に渡せるようにする）
# PREFIX（<owner>-nwc-poc。--oss なら <owner>-nwc-oss）。Web の EC2 の中での置き場（/opt/<接頭辞>-web）を ops/seed_graph.py に教える
if [ -n "$OSS" ]; then resolve_name_prefix nwc-oss; TF_DIR=IaC/terraform/oss; else resolve_name_prefix; TF_DIR=IaC/terraform/aws-managed; fi
REGION=ap-northeast-1
if command -v python3 >/dev/null; then PY=(python3)
elif command -v uv >/dev/null; then PY=(uv run --python 3.13 python)
else echo "python3 か uv が要る（app/containerlab/lab_topology.py を動かす）" >&2; exit 1; fi

TOPO_JSON=$("${PY[@]}" app/containerlab/lab_topology.py app/containerlab) || die "app/containerlab/lab_topology.py が lab の定義を読めなかった"
if [ -n "$DRY" ]; then printf '%s\n' "$TOPO_JSON"; exit 0; fi

if [ -n "${AWS_PROFILE:-}" ]; then export AWS_PROFILE; fi
INSTANCE_ID=$(terraform -chdir="$TF_DIR/base/core" output -raw web_instance_id 2>/dev/null) || die "$TF_DIR/base/core の出力 web_instance_id が読めない（apply 済みか、認証情報があるか）"
[ -n "$INSTANCE_ID" ] || die "$TF_DIR/base/core の出力 web_instance_id が空"

# ops/up.sh の ssm_run と同じ。コマンドは JSON の文字列に埋めるので、ダブルクォートとバックスラッシュを含めない
CMD="echo $(base64 < ops/seed_graph.py | tr -d '\n') | base64 -d | NAME_PREFIX=$PREFIX LAB_TOPOLOGY_B64=$(printf '%s' "$TOPO_JSON" | base64 | tr -d '\n') GRAPH_REPLACE=${REPLACE:-0} /usr/bin/python3.13 -"
CMD_ID=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript --timeout-seconds 900 \
  --parameters "{\"commands\":[\"$CMD\"]}" --query Command.CommandId --output text) || die "SSM Run Command を送れなかった"
while :; do
  STATUS=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
    --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in
    Success)
      aws ssm get-command-invocation --region "$REGION" --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
        --query StandardOutputContent --output text | sed '/^$/d'
      echo "Web の画面は「再読み込み」で新しいトポロジになる（チャットは 60 秒以内に読み直す）"
      exit 0 ;;
    Pending|InProgress|Delayed) sleep 10 ;;
    *) aws ssm get-command-invocation --region "$REGION" --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
         --query '[StandardOutputContent,StandardErrorContent]' --output text >&2
       die "Web の EC2 の上のコマンドが $STATUS" ;;
  esac
done
