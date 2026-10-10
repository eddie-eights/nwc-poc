#!/usr/bin/env bash
# Spark（EMR Serverless）のストリーミングジョブだけを止める（docs/deploy.md の「Spark のジョブだけ止めて起こし直す」）。
# 動いている・待っているジョブを全部 cancel し、止まる（CANCELLING も抜ける）まで待って終わる。EMR Serverless のアプリケーションも
# Terraform のリソースも触らない。起こし直すのはこのスクリプトではなく ops/up.sh（手順 7-5 が、動いているジョブの無い格納先のジョブを
# checkpoint から起こす）。
#
# 使い方（展開したフォルダの直下で。先に AWS CLI の認証を通しておく。IAM ユーザーなら長期キーのまま打つ）:
#   ops/stop-spark.sh
#
# ops/up.sh と同じ deploy.env（DEPLOY_ENV_FILE=<パス> で別のファイル）を読む。環境変数はファイルより優先。
# ここで使うキーは OWNER（**必須。作ったときの ops/up.sh と同じ値**。接頭辞 <owner>-nwc-poc を作る）と AWS_PROFILE / AWS_CA_BUNDLE / TF_VERBOSE だけ。
# アプリケーションの ID は IaC/terraform/aws-managed/pipeline/analytics の state（output application_id）から引く。
# 止める塊は ops/common.sh の emr_cancel_jobs（ops/up.sh の手順 7-4 と ops/down.sh も同じ関数で止める）
set -euo pipefail

REGION=ap-northeast-1
# OWNER は deploy.env に書くので、OWNER と接頭辞 PREFIX=<owner>-nwc-poc が確定するのは load_deploy_env のあと（手順 0 の resolve_name_prefix）
. "$(dirname "$0")/deploy-env.sh"
resolve_deploy_env_file  # DEPLOY_ENV_FILE の相対パスは、下の cd の前の場所から見る
cd "$(dirname "$0")/.."

. ops/common.sh       # log / die / tf / emr_cancel_jobs と terraform の認証情報
. ops/down-common.sh  # has_resources（init 込み）
trap 'if [ -n "$TF_AWS_CONFIG" ]; then rm -f "$TF_AWS_CONFIG"; fi' EXIT  # tf_use_cli_credentials の一時ファイル

log "0. 設定と道具と認証"
load_deploy_env
resolve_name_prefix  # OWNER と接頭辞 PREFIX。作ったときの ops/up.sh と同じ値でないと、別の state を見にいく（deploy.env を変えずに打つ）
flag_value TF_VERBOSE
command -v aws >/dev/null || die "aws CLI が無い"
command -v terraform >/dev/null || die "terraform が無い"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text) || die "認証が通っていない（aws configure か aws login で入り直す）"
echo "ACCOUNT_ID=$ACCOUNT_ID"
tf_use_cli_credentials

log "1. EMR Serverless のアプリケーション（IaC/terraform/aws-managed/pipeline/analytics の state から）"
has_resources pipeline/analytics \
  || die "IaC/terraform/aws-managed/pipeline/analytics の state にリソースが無い（analytics を作っていないか、作った PC の state でない）。止めるジョブは無い"
APP_ID=$(tf pipeline/analytics output -raw application_id 2>/dev/null) || die "IaC/terraform/aws-managed/pipeline/analytics の出力 application_id が読めない（上のエラー）"
[ -n "$APP_ID" ] || die "IaC/terraform/aws-managed/pipeline/analytics の出力 application_id が空"
echo "APP_ID=$APP_ID"

log "2. Spark のジョブを全部止める（止まるまで最大 3 分。アプリケーションは止めない）"
emr_cancel_jobs "$APP_ID" 36 \
  || die "Spark のジョブ（${EMR_JOBS_LEFT}）が 3 分たっても止まらない。$(tf pipeline/analytics output -raw list_job_runs_command) で見て、止まってからもう一度打つ"
echo "Spark のジョブは全部止まった。起こし直すのは ops/up.sh（手順 7-5 が、同じ格納先の checkpoint から続きを読む。できているものは飛ばす）"
