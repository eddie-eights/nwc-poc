#!/usr/bin/env bash
# OSS 版（cycle 005「マネージドを OSS に置き換えた環境を作る」）で作ったものをまとめて消す。oss/ops/up.sh の逆。
# 消す相手は接頭辞 <owner>-nwc-oss のもので、oss/terraform/<ルート>/terraform.tfstate にリソースが載っているルートだけ。
# 同じアカウントのマネージド版（<owner>-nwc-poc。terraform/ の state と ops/up.sh が作った SSM のパラメータ）には触らない。
# 消し方はマネージド版の ops/down.sh と同じ関数（ops/common.sh・ops/down-common.sh）を、ルートの親を oss/terraform にして使う。
#
# 使い方（展開したフォルダの直下で。先に AWS CLI の認証を通しておく。IAM ユーザーなら長期キーのまま打つ）:
#   oss/ops/down.sh              # 全部消す（workflow → analytics → nautobot → graph → stream → lab → agent → base/core → ecr
#                                #   → Runtime のロググループ → oss/ops/up.sh が作った SSM のパラメータ）。KEEP_ECR=0 と同じ
#   KEEP_ECR=1 oss/ops/down.sh   # ECR（イメージ）だけ残す。翌日の oss/ops/up.sh で写すのを飛ばせる（保管料は月数円）
#
# oss/ops/up.sh と同じ deploy.env（DEPLOY_ENV_FILE=<パス> で別のファイル）を読む。使うキーは OWNER（必須。作ったときと同じ値）、
# KEEP_ECR、AWS_PROFILE / AWS_CA_BUNDLE。いま oss/ops/up.sh が作るのは base/ecr・base/core・pipeline/lab・pipeline/stream だが、
# oss/terraform/ にあるほかのルートも、state にリソースが載っていれば消す（作っていないルートは飛ばす）
set -uo pipefail

REGION=ap-northeast-1
. "$(dirname "$0")/../../ops/deploy-env.sh"
resolve_deploy_env_file  # DEPLOY_ENV_FILE の相対パスは、下の cd の前の場所から見る
cd "$(dirname "$0")/../.."

. ops/common.sh       # log / die / tf と terraform の認証情報（マネージド版の ops/down.sh と同じもの）
. ops/down-common.sh  # destroy_root / destroy_agent / destroy_base_core / report_leftovers など
TF_DIR=oss/terraform  # destroy するルートの親。マネージド版の terraform/ の state には触らない
OPS_DIR=oss/ops       # 消す SSM のパラメータはタグ ManagedBy=oss/ops/up.sh のものだけ（マネージド版の ManagedBy=ops/up.sh は残る）
TF_LOG_NAME=tf-oss    # terraform のログは ops/logs/tf-oss-<ルート>-destroy.log
trap 'if [ -n "$TF_AWS_CONFIG" ]; then rm -f "$TF_AWS_CONFIG"; fi' EXIT  # tf_use_cli_credentials の一時ファイル

log "0. 設定と道具と認証（OSS 版）"
load_deploy_env
resolve_name_prefix nwc-oss  # OWNER と接頭辞 PREFIX=<owner>-nwc-oss。作ったときの oss/ops/up.sh と同じ値でないと、Terraform が別のリソースを消しにいく
flag_value TF_VERBOSE
KEEP_ECR="${KEEP_ECR:-0}"
case "$KEEP_ECR" in
  0) echo "KEEP_ECR=0: ECR もイメージごと消す（残すなら KEEP_ECR=1）" ;;
  1) echo "KEEP_ECR=1: ECR（イメージ）は残す" ;;
  *) die "KEEP_ECR は 1（ECR を残す）か 0（消す。既定）（いまは「${KEEP_ECR}」）。まだ何も消していない" ;;
esac
command -v aws >/dev/null || die "aws CLI が無い"
command -v terraform >/dev/null || die "terraform が無い"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text) || die "認証が通っていない（aws configure か aws login で入り直す）"
echo "ACCOUNT_ID=$ACCOUNT_ID"
echo "消す相手: 接頭辞と Project タグが $PREFIX のもの（oss/terraform/ の state）。マネージド版（$OWNER-nwc-poc）には触らない"
tf_use_cli_credentials

log "1. workflow → analytics → nautobot → graph → stream（oss/terraform/ にできていて、state にリソースがあるものだけ）"
# workflow の worker_image_tag は必須変数だが destroy では使われないので、何でもよい値を渡す
destroy_lambda_root workflow "$PREFIX-tools" -var "worker_image_tag=${IMAGE_TAG:-destroy}"
destroy_root pipeline/analytics
destroy_root pipeline/nautobot
destroy_lambda_root pipeline/graph "$PREFIX-graph-status"
# stream（Kafka の ECS 3 台・Telegraf・Kafbat UI）。snmp_agents / gnmi_targets は必須変数だが destroy では使われないので、形だけ合う値を渡す
destroy_root pipeline/stream -var 'snmp_agents="udp://0.0.0.0:161"' -var 'gnmi_targets="0.0.0.0:57400"'

log "2. lab"
destroy_root pipeline/lab

log "3. agent（oss/terraform/agent に state があるときだけ。base/core のロールにポリシーを付けるので base/core より先）"
destroy_agent

log "3-2. 土台（oss/terraform/base/core。VPC / Web の EC2 / バケット（中身ごと消える）/ ロール / Kafka のデータの EFS（中身ごと消える））"
destroy_base_core

if [ "$KEEP_ECR" = 1 ]; then
  log "4. ECR は残す（KEEP_ECR=1）"
else
  log "4. ECR（イメージごと消える）"
  destroy_root base/ecr
fi

log "5. Runtime のロググループ（agent を作っていたときだけある）"
delete_runtime_log_groups

log "5-2. oss/ops/up.sh が作った SSM のパラメータ（Kafka の CLUSTER_ID、Kafbat UI の admin のパスワード、Telegraf が機器に入る認証情報）"
# タグ ManagedBy=oss/ops/up.sh の付いたものだけ消す（マネージド版と手で入れたパラメータは消さない）。値は読まない。
# stream か base/core が消えなかったときは Kafka の CLUSTER_ID を残す（ops/down-common.sh の delete_up_ssm_params）
delete_up_ssm_params

log "6. 残っていないか（Project=$PREFIX のタグ）"
report_leftovers
echo "（残り 0 件なら全部消えている。ecr を残したときはリポジトリが出る。消した直後の数分は消えたものが出ることがある）"
if [ "$MAIN_LEFT" = 1 ]; then
  echo "oss/terraform/base/core の VPC・サブネット・runtime の SG は残した（Runtime の ENI 待ち。時間課金は無い）。"
  echo "すぐ使うなら oss/ops/up.sh がそのまま使い回す。消し切るなら数時間おいて oss/ops/down.sh を打ち直す"
fi
if [ -n "$FAILED_ROOTS" ]; then
  echo
  echo "NG: 消えなかったルート:${FAILED_ROOTS}（全文は ops/logs/tf-oss-*-destroy.log）"
  echo "これ以外は消してあるので、時間課金が残っているのは上のルートだけ。上に出た RequesterManaged=True の ENI が残っているなら"
  echo "AWS 側が片付けるのを待つしかない。待って oss/ops/down.sh を打ち直す"
  exit 1
fi
