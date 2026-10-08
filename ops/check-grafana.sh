#!/usr/bin/env bash
# Grafana のアラートルールが評価でエラーになっていないかを確かめる。ops/up.sh（OSS 版は oss/ops/up.sh）の 9-2 と同じ確かめを単独で打つ版。
# ルールは execErrState: KeepLast なので、評価がエラーでもアラートは出ず、画面のルールも Normal に見える。up.sh の確かめは打ったときの結果だけなので、
# データソースや格納先を直したあと、Grafana のタスクが入れ替わったあと、アラートが来ないと思ったときに打つ。
#
# 使い方（展開したフォルダの直下で。ops/up.sh と同じ deploy.env と AWS の認証情報）:
#   ops/check-grafana.sh         # マネージド版（STORES に grafana があるとき）
#   ops/check-grafana.sh --oss   # OSS 版（cycle 005。接頭辞 <owner>-nwc-oss、IaC/terraform/oss/ の state）
#
# base/core（Web の EC2）と pipeline/analytics の Grafana が出来ていることが前提。Web の EC2 の上で ops/grafana_rules_check.py を SSM Run Command で動かす
# （ops/up-common.sh の grafana_rules_check）。全部のルールが 1 回評価されるまで最大 5 分待つ。出力の最後の行が「判定: OK / NG / 未確認 …」。
# 終了コードは 0（OK）/ 1（NG か未確認。どちらかは「判定:」の行）/ 2（使い方の誤り）。
# NG の理由（KeepLast はエラーの文を API に残さない）は Grafana のログを見る（下で出すコマンド）。
# Grafana のタスクが入れ替わる途中だと、Cloud Map の名前が前のタスクを指していることがある。そのときは入れ替わりが終わってから打ち直す
set -uo pipefail

cd "$(dirname "$0")/.."
OSS=""
for a in "$@"; do
  case "$a" in
    --oss) OSS=1 ;;
    *) echo "使い方: ops/check-grafana.sh [--oss]" >&2; exit 2 ;;
  esac
done

REGION=ap-northeast-1
# shellcheck source=ops/deploy-env.sh
. ops/deploy-env.sh
# shellcheck source=ops/common.sh
. ops/common.sh      # log / die
# shellcheck source=ops/up-common.sh
. ops/up-common.sh   # ssm_run / grafana_rules_check
load_deploy_env >&2
if [ -n "$OSS" ]; then resolve_name_prefix nwc-oss; TF_DIR=IaC/terraform/oss; else resolve_name_prefix; TF_DIR=IaC/terraform/aws-managed; fi
if [ -n "${AWS_PROFILE:-}" ]; then export AWS_PROFILE; fi

INSTANCE_ID=$(terraform -chdir="$TF_DIR/base/core" output -raw web_instance_id 2>/dev/null) || die "$TF_DIR/base/core の出力 web_instance_id が読めない（apply 済みか、認証情報があるか）"
[ -n "$INSTANCE_ID" ] || die "$TF_DIR/base/core の出力 web_instance_id が空"
GRAFANA_SERVICE=$(terraform -chdir="$TF_DIR/pipeline/analytics" output -raw grafana_service_name 2>/dev/null) || GRAFANA_SERVICE=""
[ -n "$GRAFANA_SERVICE" ] || die "$TF_DIR/pipeline/analytics に Grafana が無い（出力 grafana_service_name が読めないか空。マネージド版は STORES に grafana を入れて ops/up.sh を打つ）"

log "Grafana（$GRAFANA_SERVICE）のアラートルールを Web の EC2（$INSTANCE_ID）から読む（最大 5 分）"
if grafana_rules_check "$INSTANCE_ID"; then exit 0; fi
echo "評価のエラーの理由は Grafana のログ: aws logs tail /ecs/$PREFIX-grafana --region $REGION --since 1h --filter-pattern '\"Failed to evaluate rule\"'" >&2
exit 1
