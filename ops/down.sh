#!/usr/bin/env bash
# 作ったものをまとめて消す（docs/deploy.md の「ops/down.sh がすること」）。Terraform のルートを依存の逆順に destroy し、消え終わるまで待つ。
# state（terraform/<ルート>/terraform.tfstate）にリソースが載っているルートだけを消す。作っていないルートは飛ばす。
#
# 使い方（展開したフォルダの直下で。先に AWS CLI の認証を通しておく。IAM ユーザーなら長期キーのまま打つ）:
#   ops/down.sh              # 全部消す（workflow → analytics → nautobot → graph → stream → lab → agent → base/core → ecr → Runtime のロググループ → ops/up.sh が作った SSM のパラメータ）。KEEP_ECR=0 と同じ
#   KEEP_ECR=1 ops/down.sh   # ECR（イメージ）だけ残す。翌日の ops/up.sh でビルドを飛ばせる（保管料は月数円）
#
# ops/up.sh と同じ deploy.env（DEPLOY_ENV_FILE=<パス> で別のファイル）を読む。環境変数はファイルより優先。
# ここで使うキー（OWNER だけ必須で、ほかは任意）:
#   OWNER      **必須。**デプロイした人の名前。リソース名の接頭辞と Project タグの <owner>-nwc-poc もここから作る。
#              **作ったときの ops/up.sh と同じ値にする**（deploy.env を書き換えずに打てば自動で揃う）。違う値だと Terraform が消す相手を取り違える
#   KEEP_ECR   ECR を残すか。1 = 残す、0 = 消す（既定）。それ以外の値は何も消さずに止まる
#   AWS_PROFILE / AWS_CA_BUNDLE  ops/up.sh と同じ
#
# PIPELINE / AGENT / WORKFLOW / SKIP_* / CREATE_KB は見ない。機能の設定に関係なく、state にリソースが載っているルートを全部消す
# （作っていないルートは飛ばす）。analytics は Spark のジョブを止めてから消す。
#
# 社内の SSL 検査がある PC では ops/up.sh と同じく AWS_CA_BUNDLE を入れてから打つ。
# KB のベクトルインデックスはコレクションごと消える（PC から OpenSearch にはつながない。2026-09-28 にコレクションを閉じてから）。
# それより前の agent の state（opensearch_index.kb が載っている）は、いまの terraform/agent では消せない（opensearch provider を外した）。
# その state が残っているなら、コミット f7b1688 の terraform/agent で destroy してから、この版に上げる
set -uo pipefail

REGION=ap-northeast-1
# OWNER は deploy.env に書くので、OWNER と接頭辞 PREFIX=<owner>-nwc-poc が確定するのは load_deploy_env のあと（手順 0 の resolve_name_prefix。必須なので、無ければそこで止まる）
. "$(dirname "$0")/deploy-env.sh"
resolve_deploy_env_file  # DEPLOY_ENV_FILE の相対パスは、下の cd の前の場所から見る
cd "$(dirname "$0")/.."

. ops/common.sh       # log / die / tf と terraform の認証情報（OSS 版の oss/ops/down.sh と同じものを読む）
. ops/down-common.sh  # destroy_root / destroy_agent / destroy_base_core / report_leftovers など
trap 'if [ -n "$TF_AWS_CONFIG" ]; then rm -f "$TF_AWS_CONFIG"; fi' EXIT  # tf_use_cli_credentials の一時ファイル

log "0. 設定と道具と認証"
load_deploy_env
resolve_name_prefix  # OWNER と接頭辞 PREFIX。作ったときの ops/up.sh と同じ値でないと、Terraform が別のリソースを消しにいく（deploy.env を変えずに打つ）
# terraform の出力を絞るか（ops/deploy-env.sh の tf_logged）。TF_VERBOSE=0 / false / no を空にそろえる（そろえないと、0 を書いても「空でない」で全部出してしまう）
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
tf_use_cli_credentials

log "1. workflow → analytics → nautobot → graph → stream（workflow は analytics と graph を、analytics は stream の Kafka を、nautobot は graph と stream を読むので、この順）"
# EMR Serverless のアプリケーションは、ジョブが動いているか STARTED のままだと destroy が落ちる。先にジョブを止め、アプリケーションを止める
if has_resources pipeline/analytics; then
  APP_ID=$(tf pipeline/analytics output -raw application_id 2>/dev/null || true)
  if [ -n "$APP_ID" ]; then
    RUNNING=$(aws emr-serverless list-job-runs --region "$REGION" --application-id "$APP_ID" \
      --states SUBMITTED PENDING SCHEDULED RUNNING QUEUED --query 'jobRuns[].id' --output text 2>/dev/null || true)
    if [ -n "$RUNNING" ] && [ "$RUNNING" != None ]; then
      for id in $RUNNING; do
        echo "Spark のジョブ $id を止める"
        aws emr-serverless cancel-job-run --region "$REGION" --application-id "$APP_ID" --job-run-id "$id" >/dev/null || true
      done
      for i in $(seq 1 24); do  # 止まるまで最大 2 分
        LEFT=$(aws emr-serverless list-job-runs --region "$REGION" --application-id "$APP_ID" \
          --states SUBMITTED PENDING SCHEDULED RUNNING QUEUED CANCELLING --query 'jobRuns[].id' --output text 2>/dev/null || true)
        if [ -z "$LEFT" ] || [ "$LEFT" = None ]; then break; fi
        sleep 5
      done
    fi
    aws emr-serverless stop-application --region "$REGION" --application-id "$APP_ID" >/dev/null 2>&1 || true
    for i in $(seq 1 24); do  # STOPPED になるまで最大 2 分
      STATE=$(aws emr-serverless get-application --region "$REGION" --application-id "$APP_ID" --query application.state --output text 2>/dev/null || echo "")
      case "$STATE" in STOPPED|CREATED|"") break ;; esac
      sleep 5
    done
  fi
fi
# workflow の worker_image_tag は必須変数だが destroy では使われないので、何でもよい値を渡す
destroy_lambda_root workflow "$PREFIX-tools" -var "worker_image_tag=${IMAGE_TAG:-destroy}"
destroy_root pipeline/analytics
# Nautobot（ECS と RDS）。RDS は最後のスナップショット無しで消すので、Nautobot で編集した内容は残らない（5〜10 分）
destroy_root pipeline/nautobot
destroy_lambda_root pipeline/graph "$PREFIX-graph-status"
# stream の snmp_agents / gnmi_targets も必須変数だが destroy では使われないので、形だけ合う値を渡す
destroy_root pipeline/stream -var 'snmp_agents="udp://0.0.0.0:161"' -var 'gnmi_targets="0.0.0.0:57400"'

log "2. lab"
destroy_root pipeline/lab

log "3. agent（Runtime / ガードレール / KB。terraform/base/core のロールにポリシーを付けているので base/core より先）"
destroy_agent

log "3-2. 土台（terraform/base/core。VPC / Web の EC2 / バケット（中身ごと消える）/ ロール）"
# Runtime の ENI が残っているあいだは VPC・サブネット・runtime の SG を残し、それ以外を消す（ops/down-common.sh の destroy_base_core）
destroy_base_core

if [ "$KEEP_ECR" = 1 ]; then
  log "4. ECR は残す（KEEP_ECR=1）"
else
  log "4. ECR（イメージごと消える）"
  destroy_root base/ecr
fi

log "5. Runtime のロググループ（AgentCore が作るもので Terraform の管理外）"
delete_runtime_log_groups

log "5-2. ops/up.sh が作った SSM のパラメータ（Grafana / Splunk / Nautobot の admin のパスワード、ECS の Splunk の HEC の token、Nautobot の SECRET_KEY と DB のパスワード など）"
# タグ ManagedBy=ops/up.sh の付いたものだけ消す（手で入れたパラメータは消さない）。値は読まない。
# terraform/pipeline/nautobot が消えなかったときは、その secrets を残す（ops/down-common.sh の delete_up_ssm_params）
delete_up_ssm_params

log "6. 残っていないか（Project=$PREFIX のタグ）"
report_leftovers
echo "（残り 0 件なら全部消えている。ecr を残したときはリポジトリが出る。消した直後の数分は消えたものが出ることがある）"
if [ "$MAIN_LEFT" = 1 ]; then
  echo "terraform/base/core の VPC・サブネット・runtime の SG は残した（Runtime の ENI 待ち。時間課金は無い）。"
  echo "すぐ使うなら ops/up.sh がそのまま使い回す。消し切るなら数時間おいて ops/down.sh を打ち直す"
fi
if [ -n "$FAILED_ROOTS" ]; then
  echo
  echo "NG: 消えなかったルート:${FAILED_ROOTS}（全文は ops/logs/tf-*-destroy.log）"
  echo "これ以外は消してあるので、時間課金が残っているのは上のルートだけ。上に出た RequesterManaged=True の ENI が残っているなら"
  echo "AWS 側が片付けるのを待つしかない（MSK は数分〜十数分、AgentCore Runtime は最大 8 時間）。待って ops/down.sh を打ち直す"
  exit 1
fi
