#!/usr/bin/env bash
# OSS 版（cycle 005「マネージドを OSS に置き換えた環境を作る」）で作ったものをまとめて消す。ops/oss/up.sh の逆。
# 消す相手は接頭辞 <owner>-nwc-oss のもので、IaC/terraform/oss/<ルート>/terraform.tfstate にリソースが載っているルートだけ。
# 同じアカウントのマネージド版（<owner>-nwc-poc。IaC/terraform/aws-managed/ の state と ops/up.sh が作った SSM のパラメータ）には触らない。
# 消し方はマネージド版の ops/down.sh と同じ関数（ops/common.sh・ops/down-common.sh）を、ルートの親を IaC/terraform/oss にして使う。
#
# 使い方（展開したフォルダの直下で。先に AWS CLI の認証を通しておく。IAM ユーザーなら長期キーのまま打つ）:
#   ops/oss/down.sh              # 全部消す（workflow → analytics → nautobot → graph → stream → lab → agent → base/core → ecr
#                                #   → Runtime のロググループ → ops/oss/up.sh が作った SSM のパラメータ）。KEEP_ECR=0 と同じ
#   KEEP_ECR=1 ops/oss/down.sh   # ECR（イメージ）だけ残す。翌日の ops/oss/up.sh で写すのを飛ばせる（保管料は 14.9 GB で月 約 220 円。2026-10-08 の実測）
#
# ops/oss/up.sh と同じ deploy.env（DEPLOY_ENV_FILE=<パス> で別のファイル）を読む。使うキーは OWNER（必須。作ったときと同じ値）、
# KEEP_ECR、AWS_PROFILE / AWS_CA_BUNDLE。ops/oss/up.sh はいつも 10 のルート（base/ecr・base/logs・base/core・agent・pipeline/lab・pipeline/stream・
# pipeline/graph・pipeline/nautobot・pipeline/analytics・workflow）を作る。base/logs（logs のバケット。中身は 7 日で消える）だけは消さず、
# 残りの 9 つを消す（途中で止まって作っていないルートは飛ばす）。
# PC に残るもの（wheels-oss/ と IaC/terraform/oss/pipeline/graph/.build/）は消さない（次の ops/oss/up.sh が使い回すか作り直す）。
# Glue のカタログ s3tablescatalog はアカウントで 1 つをマネージド版と共有するので、マネージド版の ops/down.sh と同じく消さない
set -uo pipefail

REGION=ap-northeast-1
. "$(dirname "$0")/../deploy-env.sh"
resolve_deploy_env_file  # DEPLOY_ENV_FILE の相対パスは、下の cd の前の場所から見る
cd "$(dirname "$0")/../.."

. ops/common.sh       # log / die / tf と terraform の認証情報（マネージド版の ops/down.sh と同じもの）
. ops/down-common.sh  # destroy_root / destroy_agent / destroy_base_core / report_leftovers など
TF_DIR=IaC/terraform/oss  # destroy するルートの親。マネージド版の IaC/terraform/aws-managed/ の state には触らない
OPS_DIR=ops/oss       # 消す SSM のパラメータはタグ ManagedBy=ops/oss/up.sh のものだけ（マネージド版の ManagedBy=ops/up.sh は残る）
TF_LOG_NAME=tf-oss    # terraform のログは ops/logs/tf-oss-<ルート>-destroy.log
TF_INIT_LOCKFILE=readonly  # init は lock を書き換えない（lock はマネージド版へのシンボリックリンク。ops/common.sh の tf_init_root）
trap 'if [ -n "$TF_AWS_CONFIG" ]; then rm -f "$TF_AWS_CONFIG"; fi' EXIT  # tf_use_cli_credentials の一時ファイル

log "0. 設定と道具と認証（OSS 版）"
load_deploy_env
resolve_name_prefix nwc-oss  # OWNER と接頭辞 PREFIX=<owner>-nwc-oss。作ったときの ops/oss/up.sh と同じ値でないと、Terraform が別のリソースを消しにいく
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
echo "消す相手: 接頭辞と Project タグが $PREFIX のもの（IaC/terraform/oss/ の state）。マネージド版（$OWNER-nwc-poc）には触らない"
tf_use_cli_credentials

log "1. workflow → analytics → nautobot → graph → stream（IaC/terraform/oss/ にできていて、state にリソースがあるものだけ）"
# workflow の worker_image_tag は必須変数だが destroy では使われないので、何でもよい値を渡す
destroy_lambda_root workflow "$PREFIX-tools" -var "worker_image_tag=${IMAGE_TAG:-destroy}"
destroy_root pipeline/analytics
destroy_root pipeline/nautobot
destroy_lambda_root pipeline/graph "$PREFIX-graph-status"
# stream（Kafka の ECS 3 台・Telegraf・gnmic と、Web の EC2 の Kafbat UI が読む SSM のパラメータ）。gnmi_targets は必須変数だが destroy では使われないので、形だけ合う値を渡す
destroy_root pipeline/stream -var 'gnmi_targets="0.0.0.0:57400"'

log "2. lab"
destroy_root pipeline/lab

log "3. agent（IaC/terraform/oss/agent に state があるときだけ。base/core のロールにポリシーを付けるので base/core より先）"
destroy_agent

log "3-2. 土台（IaC/terraform/oss/base/core。VPC / Web の EC2 / バケット（中身ごと消える）/ ロール / Kafka のデータの EFS（中身ごと消える））"
destroy_base_core

if [ "$KEEP_ECR" = 1 ]; then
  log "4. ECR は残す（KEEP_ECR=1）"
else
  log "4. ECR（イメージごと消える）"
  destroy_root base/ecr
fi

log "5. Runtime のロググループ（AgentCore が作るもので、agent の destroy では消えない）"
delete_runtime_log_groups

log "5-2. ops/oss/up.sh が作った SSM のパラメータ（Kafka の CLUSTER_ID、Kafbat UI・OpenSearch・Splunk・Grafana・Neo4j・Nautobot のパスワードや token、gnmic が機器に入る認証情報）"
# タグ ManagedBy=ops/oss/up.sh の付いたものだけ消す（マネージド版と手で入れたパラメータは消さない）。値は読まない。
# stream か base/core が消えなかったときは Kafka の CLUSTER_ID を、nautobot が消えなかったときは /<接頭辞>/nautobot/ の下を残す
# （ops/down-common.sh の delete_up_ssm_params）
delete_up_ssm_params

log "6. 残っていないか（Project=$PREFIX のタグ）"
report_leftovers
echo "（この一覧では消えたかを決めない。タグの API は消えたリソースも返す。"
echo " 消えたかはサービスごとの API で見る。KEEP_ECR=1 なら ECR のリポジトリは実際に残っている。docs/deploy.md の「消したあとに残るもの」）"
echo "logs のバケット $PREFIX-logs-$ACCOUNT_ID は残す（IaC/terraform/oss/base/logs。中身は 7 日で消え、空のバケットは無料。上の一覧に出るのは想定どおり。"
echo " 消すなら terraform -chdir=IaC/terraform/oss/base/logs destroy -var owner=${OWNER}）"
if [ "$MAIN_LEFT" = 1 ]; then
  echo "IaC/terraform/oss/base/core の VPC・サブネット・runtime の SG は残した（Runtime の ENI 待ち。時間課金は無い）。"
  echo "そのままでよい。次の ops/oss/up.sh が使い回す（ops/oss/up.sh を打ったのと同じチェックアウトから打つとき。state はここにしか無い）。"
  echo "消し切るときだけ、ENI が外れてから（最大 8 時間）同じチェックアウトで ops/oss/down.sh を打ち直す"
fi
if [ -n "$FAILED_ROOTS" ]; then
  echo
  echo "NG: 消えなかったルート:${FAILED_ROOTS}（全文は ops/logs/tf-oss-*-destroy.log）"
  echo "これ以外は消してあるので、時間課金が残っているのは上のルートだけ。上に出た RequesterManaged=True の ENI が残っているなら"
  echo "AWS 側が片付けるのを待つしかない。待って ops/oss/down.sh を打ち直す"
  exit 1
fi
