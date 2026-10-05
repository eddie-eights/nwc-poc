#!/usr/bin/env bash
# OSS 版（cycle 005「マネージドを OSS に置き換えた環境を作る」）を 1 本で起こす。マネージド版（ops/up.sh）と同じアカウントに並べて立てられる。
#   リソース名の接頭辞と Project タグは <owner>-nwc-oss（マネージド版は <owner>-nwc-poc）。ルートは oss/terraform/ の下で、
#   state も oss/terraform/<ルート>/terraform.tfstate に置く（マネージド版の terraform/ の state とは別）。消すのは oss/ops/down.sh。
#   作るのは base/ecr → base/core → pipeline/lab → pipeline/stream（Kafka は MSK でなく ECS の KRaft 3 台。oss/terraform/pipeline/stream/kafka.tf）
#   → pipeline/analytics（Spark・OpenSearch・VictoriaMetrics・Splunk の ECS と S3 Tables）→ pipeline/graph（Neo4j + GDS の ECS と status の Lambda）。
#   格納先はいつも iceberg / opensearch / prometheus / splunk の 4 つ（マネージド版の STORES のようには選ばない）。
#   イメージは ECR に無いタグだけ写すかビルドする（oss/ops/oss-images.sh の mirror_oss_images と、Splunk は ops/up-common.sh の build_splunk）。
# 関数は ops/ のもの（ops/common.sh・ops/up-common.sh・ops/lab-common.sh・ops/deploy-env.sh）を読み、写しを作らない。
# 何度打っても同じ状態に収束する（できているものは Terraform が差分なしで飛ばし、ECR にあるタグは写さない）。
#
# 使い方（展開したフォルダの直下で。先に AWS CLI の認証を通しておく。IAM ユーザーなら長期キーのまま打つ）:
#   oss/ops/up.sh                         # deploy.env の OWNER（必須）と下のキーを読む
#   DEPLOY_ENV_FILE=<パス> oss/ops/up.sh  # 別の設定ファイルを読む
#
# 読むキー（deploy.env か環境変数。意味は deploy.env.example）: OWNER / SYSLOG_STANDARD / SNMP_POLL / VPC_CIDR / MDT_SOURCE_CIDRS /
#   NETWORK_PERIMETER / ENDPOINTS_AZ_NUM / TELEGRAF_AZ_NUM / EMR_AZ_NUM（Spark のタスクのサブネット）/ SPLUNK_AZ_NUM / SPLUNK_INDEX /
#   TF_VERBOSE / AWS_PROFILE / AWS_CA_BUNDLE。
#   機能を選ぶキー（AGENT / PIPELINE / WORKFLOW / SKIP_* / STORES など）はマネージド版のもので、ここでは読まない（書いてあれば注意を出す）。
#   同じ deploy.env をマネージド版と共有するので、VPC_CIDR を書くと両方の VPC が同じ CIDR になる（VPC どうしをつながないので重なってよい）
set -euo pipefail
REGION=ap-northeast-1
. "$(dirname "$0")/../../ops/lab-common.sh"   # lab と telegraf の版と、ecr_has / mirror_image / dir_tag / upload_lab
. "$(dirname "$0")/oss-images.sh"             # OSS 版のイメージの名前と版（公開イメージの正は oss/compose/）と mirror_oss_images
. "$(dirname "$0")/../../ops/deploy-env.sh"
resolve_deploy_env_file   # 相対の DEPLOY_ENV_FILE を cd の前の場所で解決する
cd "$(dirname "$0")/../.."
. ops/common.sh
. ops/up-common.sh
TF_DIR=oss/terraform   # tf / tf_apply が -chdir で入るルートの親。マネージド版の terraform/ には触らない
OPS_DIR=oss/ops        # SSM のパラメータのタグ ManagedBy=oss/ops/up.sh（oss/ops/down.sh はこのタグのものだけ消す）
TF_LOG_NAME=tf-oss     # terraform のログは ops/logs/tf-oss-<ルート>-<apply|destroy>.log
trap 'if [ -n "$TF_AWS_CONFIG" ]; then rm -f "$TF_AWS_CONFIG"; fi' EXIT

# ---- 0. 道具と認証 -------------------------------------------------------------
log "0. 設定と道具と認証を確かめる（OSS 版）"
load_deploy_env
resolve_name_prefix nwc-oss   # OWNER（必須）と接頭辞 PREFIX=<owner>-nwc-oss
flag_value TF_VERBOSE
log "   デプロイする人の名前: ${OWNER}（リソース名の接頭辞と Project タグは ${PREFIX}）"
SYSLOG_STANDARD="${SYSLOG_STANDARD:-RFC3164}"
case "$SYSLOG_STANDARD" in
  RFC3164|RFC5424) ;;
  *) die "SYSLOG_STANDARD は RFC3164 か RFC5424（大文字）: $SYSLOG_STANDARD。まだ何も作っていない" ;;
esac
SNMP_POLL="${SNMP_POLL:-1}"; flag_value SNMP_POLL
NETWORK_PERIMETER="${NETWORK_PERIMETER:-1}"; flag_value NETWORK_PERIMETER
case "${ENDPOINTS_MULTI_AZ:-}" in
  '') ;;
  *) die "ENDPOINTS_MULTI_AZ はなくなった。ENDPOINTS_AZ_NUM（1〜3。既定 1）で書く。まだ何も作っていない" ;;
esac
az_num ENDPOINTS_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"
az_num TELEGRAF_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"
az_num EMR_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"   # Spark の ECS のタスクを置くサブネット（マネージド版の EMR Serverless と同じキー）
az_num SPLUNK_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"
SPLUNK_INDEX="${SPLUNK_INDEX:-}"
if [ "$SPLUNK_AZ_NUM" -gt 1 ]; then
  [ -z "$SPLUNK_INDEX" ] || die "SPLUNK_AZ_NUM=$SPLUNK_AZ_NUM（Splunk のクラスター）では index は main だけで、SPLUNK_INDEX は書けない（いまは SPLUNK_INDEX=$SPLUNK_INDEX）。SPLUNK_INDEX を消すか、SPLUNK_AZ_NUM=1 にする。まだ何も作っていない"
fi
# Kafka は 3 台を a / b / c に 1 台ずつ置く（kafka.tf。数は変えられない）。エンドポイントが a だけ（ENDPOINTS_AZ_NUM=1）でも動くが、a が止まると
# b / c の Kafka も ECR や CloudWatch Logs に届かない
IGNORED=""
for k in AGENT PIPELINE WORKFLOW CREATE_KB SKIP_LAB SKIP_STREAM SKIP_ANALYTICS SKIP_GRAPH STORES NAUTOBOT GRAFANA MSK_AZ_NUM; do
  if [ -n "${!k:-}" ]; then IGNORED="$IGNORED $k"; fi
done
if [ -n "$IGNORED" ]; then echo "注意:${IGNORED} はマネージド版（ops/up.sh）のキーで、OSS 版では読まない（ルートはいつも全部作り、格納先はいつも iceberg / opensearch / prometheus / splunk）"; fi
if [ -z "$NETWORK_PERIMETER" ]; then echo "NETWORK_PERIMETER=0: VPC の外からの呼び出しを拒む Deny を外す（エンドポイントは作る。切り分けが済んだら 1 に戻して打ち直す）"; fi
command -v aws >/dev/null || die "aws CLI が無い（docs/setup.md「Terraform を打つ PC 側」）"
command -v terraform >/dev/null || die "terraform が無い（docs/setup.md「Terraform を打つ PC 側」。1.11 以上）"
if command -v python3 >/dev/null; then PY=(python3)
elif command -v uv >/dev/null; then PY=(uv run --python 3.13 python)
else die "python3 も uv も無い（docs/setup.md「Terraform を打つ PC 側」）"; fi
command -v curl >/dev/null || die "curl が無い（lab の containerlab の rpm を取るのに使う。sudo apt install curl）"
command -v docker >/dev/null || die "docker が無い（イメージを ECR に写すのに使う。docs/setup.md「Terraform を打つ PC 側」）"
docker buildx version >/dev/null 2>&1 || die "docker buildx が無い（Ubuntu の docker.io には入っていない。docs/setup.md「Terraform を打つ PC 側」）"
CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text) || die "認証が通っていない（aws configure か aws login で入り直す）"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
case "$CALLER_ARN" in
  arn:aws:iam::*:user/*)
    if [ -n "${AWS_SESSION_TOKEN:-}" ]; then
      die "IAM ユーザーの一時セッション（get-session-token）で入っている。IAM の API が呼べないので、一時セッションを挟まず長期キーのまま入り直す"
    fi ;;
esac
tf_use_cli_credentials
echo "ACCOUNT_ID=$ACCOUNT_ID"
echo "CALLER_ARN=$CALLER_ARN"
ROOTS="base/ecr base/core pipeline/lab pipeline/stream pipeline/analytics pipeline/graph"
# 土台の SSM Agent とポートフォワーディング（ssm ssmmessages）、ECS のタスクのイメージ（ecr.api ecr.dkr）とログ（logs）。
# Kafka の CLUSTER_ID、Kafbat UI・OpenSearch・Splunk・Neo4j のパスワードと HEC の token は ECS の secrets や Lambda が SSM から受ける（ssm）。
# Spark の iceberg は S3 Tables の API（s3tables）、Splunk のアラートは SNS（sns）、status の Lambda のアラートの履歴は Firehose（kinesis-firehose。
# alert_history=true）を通る（マネージド版の analytics と graph と同じ）。Kafka のデータの EFS と、VictoriaMetrics・OpenSearch・Neo4j は
# VPC の中で直に届くので、エンドポイントは要らない
ENDPOINTS="ssm ssmmessages ecr.api ecr.dkr logs s3tables sns kinesis-firehose"

# ---- 1. ECR --------------------------------------------------------------------
log "1. ECR リポジトリ（oss/terraform/base/ecr）"
tf_apply base/ecr
REPO=$(tf base/ecr output -raw agent_repository_url); echo "REPO=$REPO"
REG="${REPO%%/*}"

# ---- 2. イメージ ----------------------------------------------------------------
log "2. イメージ（ECR に無いタグだけ写す）"
NEED_LAB=""; NEED_TELEGRAF=""; NEED_KAFKA_UI=""; NEED_OSS=""; NEED_SPLUNK=""
if ! ecr_has "$PREFIX-lab-srlinux" "$SRLINUX_TAG" || ! ecr_has "$PREFIX-lab-multitool" "$MULTITOOL_TAG"; then NEED_LAB=1
else echo "lab-srlinux:$SRLINUX_TAG と lab-multitool:$MULTITOOL_TAG はある"; fi
TELEGRAF_TAG=$(telegraf_tag) || die "telegraf/ のタグを作れなかった"
if ecr_has "$PREFIX-telegraf" "$TELEGRAF_TAG"; then echo "telegraf:$TELEGRAF_TAG はある"; else NEED_TELEGRAF=1; fi
if ecr_has "$PREFIX-kafka-ui" "$OSS_KAFKA_UI_TAG"; then echo "kafka-ui:$OSS_KAFKA_UI_TAG はある"; else NEED_KAFKA_UI=1; fi
# ルートが使う OSS のイメージ（stream の kafka、analytics の opensearch / vmstorage vminsert vmselect / spark、graph の neo4j）
OSS_NOW="kafka opensearch vmstorage vminsert vmselect spark neo4j"
for name in $OSS_NOW; do
  tag=$(oss_image_tag "$name") || die "oss/ops/oss-images.sh が $name のタグを作れなかった"
  if ecr_has "$PREFIX-$name" "$tag"; then echo "$name:$tag はある"; else NEED_OSS=1; fi
done
SPARK_TAG=$(oss_image_tag spark) || die "spark/ のタグを作れなかった"
NEO4J_TAG=$(oss_image_tag neo4j) || die "neo4j/ のタグを作れなかった"
# Splunk はマネージド版と同じイメージ（splunk/ をビルドして <接頭辞>-splunk に置く）。SPLUNK_TAG と NEED_SPLUNK（ops/up-common.sh）
splunk_image_check
if [ -z "$NEED_LAB$NEED_TELEGRAF$NEED_KAFKA_UI$NEED_OSS$NEED_SPLUNK" ]; then
  echo "写すイメージは無い"
else
  docker info >/dev/null 2>&1 || die "dockerd に接続できない（WSL なら sudo service docker start。docs/setup.md「Terraform を打つ PC 側」）"
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REG"
  if [ -n "$NEED_LAB" ]; then
    mirror_lab_images "$REG" "$PREFIX" || die "lab のイメージを ECR に置けなかった"
  fi
  if [ -n "$NEED_TELEGRAF" ]; then
    build_telegraf "$REG/$PREFIX-telegraf:$TELEGRAF_TAG"
  fi
  if [ -n "$NEED_KAFKA_UI" ]; then
    # Kafbat UI（stream の ECS）。マネージド版と同じイメージを、OSS 版の接頭辞のリポジトリに写す
    mirror_image "$OSS_KAFKA_UI_IMAGE:$OSS_KAFKA_UI_TAG" "$REG/$PREFIX-kafka-ui:$OSS_KAFKA_UI_TAG" || die "kafka-ui のイメージを ECR に置けなかった"
  fi
  if [ -n "$NEED_OSS" ]; then
    # 公開イメージ（Fargate は VPC の中から ECR しか引けないので写す。arm64）と、spark/・neo4j/ のビルド（arm64）
    # shellcheck disable=SC2086
    mirror_oss_images "$REG" "$PREFIX" $OSS_NOW || die "OSS 版のイメージ（$OSS_NOW）を ECR に置けなかった"
  fi
  if [ -n "$NEED_SPLUNK" ]; then
    build_splunk   # amd64（ops/up-common.sh。マネージド版と共通）
  fi
fi

# ---- 3. 土台 --------------------------------------------------------------------
log "3. 土台（oss/terraform/base/core。VPC / Web の EC2 / バケット / ロール / Kafka のデータの EFS。初回は 3〜5 分）"
MAIN_VARS=()
if [ -n "${VPC_CIDR:-}" ];    then MAIN_VARS+=(-var "vpc_cidr=$VPC_CIDR"); fi
if [ -n "${MDT_SOURCE_CIDRS:-}" ]; then MAIN_VARS+=(-var "mdt_source_cidrs=[\"$(printf '%s' "$MDT_SOURCE_CIDRS" | tr -d ' ' | sed 's/,/","/g')\"]"); fi
MAIN_VARS+=(-var "interface_endpoints=[\"$(printf '%s' "$ENDPOINTS" | sed 's/ /","/g')\"]")
MAIN_VARS+=(-var "network_perimeter=$([ -n "$NETWORK_PERIMETER" ] && echo true || echo false)")
MAIN_VARS+=(-var "endpoints_az_num=$ENDPOINTS_AZ_NUM")
echo "エンドポイント: $ENDPOINTS"
tf_apply base/core ${MAIN_VARS[@]+"${MAIN_VARS[@]}"}
INSTANCE_ID=$(tf base/core output -raw web_instance_id)
KB_BUCKET=$(tf base/core output -raw kb_bucket_name)
echo "INSTANCE_ID=$INSTANCE_ID KB_BUCKET=$KB_BUCKET"

# ---- 5. lab の材料 -----------------------------------------------------------------
# lab の EC2 は起動のたびに s3://<バケット>/lab/ を読む。apply より前に置けば、最初の起動で入る
log "5-1. lab の材料（containerlab の rpm とトポロジ）を s3://$KB_BUCKET/lab/ に置く"
upload_lab "$KB_BUCKET" || die "lab の材料を s3://$KB_BUCKET/lab/ に置けなかった"

# ---- 6. lab ---------------------------------------------------------------------
LAB_WARN=""
LAB_NODES=$(grep -cE '^ *kind: (nokia_srlinux|linux)$' lab/splab.clab.yml.in)   # containerlab のノードの数
log "6. lab（oss/terraform/pipeline/lab。EC2 の中でトポロジが上がるまで 10 分ほど。forward_to_telegraf=true）"
tf_apply pipeline/lab -var forward_to_telegraf=true
LAB_INSTANCE_ID=$(tf pipeline/lab output -raw lab_instance_id); echo "LAB_INSTANCE_ID=$LAB_INSTANCE_ID"

# ---- 7. stream ------------------------------------------------------------------
log "7. stream（oss/terraform/pipeline/stream。Kafka は ECS の KRaft 3 台、Telegraf と Kafbat UI も ECS のタスク）"
SNMP_AGENTS=$("${PY[@]}" lab/lab_topology.py lab --snmp-agents) || die "lab/lab_topology.py が lab の定義からポーリング先を作れなかった"
GNMI_TARGETS=$("${PY[@]}" lab/lab_topology.py lab --gnmi-targets) || die "lab/lab_topology.py が lab の定義から gNMI の購読先を作れなかった"
if [ -n "$SNMP_POLL" ]; then
  SNMP_POLL_TF=true
  echo "Telegraf の SNMP: trap を受け、ポーリングもする（SNMP_POLL=1）。ポーリング先: $SNMP_AGENTS"
else
  SNMP_POLL_TF=false
  echo "Telegraf の SNMP: trap だけ受ける（SNMP_POLL=0）"
fi
echo "Telegraf の gNMI の購読先: $GNMI_TARGETS"
echo "Telegraf の syslog の形式: $SYSLOG_STANDARD"
if [ "$SYSLOG_STANDARD" != "$LAB_SYSLOG_STANDARD" ]; then
  echo "注意: lab の SR Linux は $LAB_SYSLOG_STANDARD で送るので、SYSLOG_STANDARD=$SYSLOG_STANDARD では lab のログの項目（ホスト名・本文など）が崩れる。lab のログまで見るなら SYSLOG_STANDARD=$LAB_SYSLOG_STANDARD"
fi
# 機器の認証情報（最初の値は lab の公開既定値。ops/lab-common.sh）。もうあれば触らない
ensure_fixed_secret "/$PREFIX/telegraf-dialin/gnmi-username" "$LAB_GNMI_USERNAME" "gNMI username of the Telegraf dial-in task (created by oss/ops/up.sh with the containerlab default)"
ensure_fixed_secret "/$PREFIX/telegraf-dialin/gnmi-password" "$LAB_GNMI_PASSWORD" "gNMI password of the Telegraf dial-in task (created by oss/ops/up.sh with the containerlab default)"
ensure_fixed_secret "/$PREFIX/telegraf-dialin/snmp-community" "$LAB_SNMP_COMMUNITY" "SNMP community of the Telegraf dial-in task (created by oss/ops/up.sh with the containerlab default)"
ensure_secret "/$PREFIX/kafka-ui/admin-password" password "Kafbat UI admin password (created by oss/ops/up.sh)"
# Kafka の KRaft の CLUSTER_ID（3 台で同じ値）。stream の apply より前に 1 回だけ作り、kafka.tf がタスクの secrets で渡す。
# 作り直すと EFS に書いたデータと合わなくなるので、あれば触らない（消すのは oss/ops/down.sh）。値は画面にもログにも出さない
ensure_secret "/$PREFIX/kafka/cluster-id" kafka-cluster-id "Kafka KRaft CLUSTER_ID shared by the three nodes (created by oss/ops/up.sh)"
# 取りにいく側の機器の一覧は、OSS 版ではまだ Nautobot を作らないので Terraform が書く lab の一覧（dialin_targets_from_nautobot=false）
tf_apply pipeline/stream -var "telegraf_image_tag=$TELEGRAF_TAG" -var "kafka_ui_image_tag=$OSS_KAFKA_UI_TAG" -var "kafka_image_tag=$OSS_KAFKA_TAG" \
  -var "snmp_agents=$SNMP_AGENTS" -var "gnmi_targets=$GNMI_TARGETS" \
  -var "syslog_standard=$SYSLOG_STANDARD" -var "snmp_poll=$SNMP_POLL_TF" -var dialin_targets_from_nautobot=false \
  -var "telegraf_az_num=$TELEGRAF_AZ_NUM"

# ---- 7-2. lab と Kafka と Telegraf の中を確かめる ------------------------------------------
log "7-2. lab のトポロジを確かめる"
wait_ssm_online "$LAB_INSTANCE_ID"
LAB_STATE=$(ssm_run "$LAB_INSTANCE_ID" "echo lab=\$(systemctl is-active $PREFIX-lab.service) containers=\$(docker ps -q --filter name=clab- | wc -l)" | tail -n 1) || LAB_STATE=""
echo "lab の EC2: ${LAB_STATE:-（読めなかった）}"
case " $LAB_STATE " in
  *" lab=active containers=$LAB_NODES "*) echo "トポロジは $LAB_NODES コンテナとも動いている" ;;
  *)
    LAB_WARN="lab のトポロジが上がっていない（${LAB_STATE:-状態を読めなかった}。$LAB_NODES コンテナが動いて lab=active になるはず）。SSM セッションで入り、sudo tail -n 50 /var/log/cloud-init-output.log と sudo journalctl -u $PREFIX-lab -n 50 --no-pager を見る（docs/pipeline.md の「動かないとき」）"
    printf '\033[1;33m%s\033[0m\n' "$LAB_WARN" ;;
esac
log "7-2b. lab の EC2 から Telegraf（stream の ECS）へ SNMP / gNMI / trap / syslog を通す（lab forward）"
ssm_run "$LAB_INSTANCE_ID" "[ ! -x /usr/local/bin/lab ] || /usr/local/bin/lab forward" \
  || printf '\033[1;33m%s\033[0m\n' "lab forward が失敗した。lab の EC2 で sudo lab forward-status を見る（docs/pipeline.md）"
# Telegraf は Kafka に書くので、Kafka を先に待つ。3 台は同時に起き、2 台そろえばコントローラーの過半数になる
log "7-2c. Kafka の ECS のサービス 3 つ（kafka-1〜3）が安定するのを待つ（イメージの取得と EFS のマウント）"
KAFKA_CLUSTER=$(tf pipeline/stream output -raw kafka_ecs_cluster_name)
KAFKA_SERVICES=$(tf pipeline/stream output -json kafka_service_names \
  | "${PY[@]}" -c 'import json, sys; print(" ".join(v for _, v in sorted(json.load(sys.stdin).items())))') \
  || die "oss/terraform/pipeline/stream の output kafka_service_names を読めなかった"
# shellcheck disable=SC2086
if aws ecs wait services-stable --region "$REGION" --cluster "$KAFKA_CLUSTER" --services $KAFKA_SERVICES; then
  echo "Kafka は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/stream output -raw kafka_log_group_name) --follow）"
else
  printf '\033[1;33m%s\033[0m\n' "Kafka のサービス（$KAFKA_SERVICES）が 10 分たっても安定しない。aws ecs list-tasks --region $REGION --cluster $KAFKA_CLUSTER --desired-status STOPPED とロググループ $(tf pipeline/stream output -raw kafka_log_group_name) を見る"
fi
log "7-2d. Telegraf の ECS のサービス 2 つ（受ける側と取りにいく側）が安定するのを待つ（1〜3 分）"
TG_CLUSTER=$(tf pipeline/stream output -raw telegraf_cluster_name); TG_DIALOUT_SERVICE=$(tf pipeline/stream output -raw telegraf_dialout_service_name)
TG_DIALIN_SERVICE=$(tf pipeline/stream output -raw telegraf_dialin_service_name)
if aws ecs wait services-stable --region "$REGION" --cluster "$TG_CLUSTER" --services "$TG_DIALOUT_SERVICE" "$TG_DIALIN_SERVICE"; then
  echo "Telegraf は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/stream output -raw telegraf_log_group_name) --follow。ストリームは受ける側が dialout/、取りにいく側が dialin/）"
else
  printf '\033[1;33m%s\033[0m\n' "Telegraf のサービスが 10 分たっても安定しない。受ける側は $(tf pipeline/stream output -raw telegraf_dialout_list_tasks_command)、取りにいく側は $(tf pipeline/stream output -raw telegraf_dialin_list_tasks_command) とロググループ $(tf pipeline/stream output -raw telegraf_log_group_name) を見る（docs/troubleshooting.md）"
fi

# ---- 7-4. analytics ----------------------------------------------------------------
log "7-4. analytics（oss/terraform/pipeline/analytics。Spark・OpenSearch 3 台・VictoriaMetrics・Splunk の ECS と S3 Tables。格納先: iceberg / opensearch / prometheus / splunk）"
# OpenSearch の admin のパスワード（2.12 からの OPENSEARCH_INITIAL_ADMIN_PASSWORD は大文字・小文字・数字・記号を求める）。OpenSearch のタスクと
# Spark のタスクが ECS の secrets で受ける。値は Terraform の state にも画面にも出さない（ops/up-common.sh）
ensure_secret "/$PREFIX/opensearch-password" strong-password "OpenSearch admin password (created by oss/ops/up.sh)"
# Splunk の管理者のパスワードと HEC の token（クラスターなら合言葉も）。マネージド版と同じ関数（ops/up-common.sh）
echo "Splunk Enterprise（splunk/splunk:$SPLUNK_VERSION・試用ライセンス）を立てる。Splunk のライセンスと Splunk General Terms に同意して起動する"
ensure_splunk_secrets "$SPLUNK_AZ_NUM"
ensure_s3tables_catalog   # alert_events への Firehose（マネージド版の history.tf へのリンク）はこのカタログ越しにテーブルを引く
# device map（別名=機器名,...）。Splunk のアラートアクションと Spark の prometheus / opensearch の sysName に使う（マネージド版と同じ）
DEVICE_MAP=$("${PY[@]}" lab/lab_topology.py lab --device-map) || die "lab/lab_topology.py が lab の定義から device map を作れなかった"
tf_apply pipeline/analytics -var 'sinks=["iceberg","opensearch","prometheus","splunk"]' \
  -var "spark_image_tag=$SPARK_TAG" -var "opensearch_image_tag=$OSS_OPENSEARCH_TAG" -var "victoriametrics_image_tag=$OSS_VM_TAG" \
  -var "splunk_image_tag=$SPLUNK_TAG" -var "splunk_index=$SPLUNK_INDEX" -var "splunk_az_num=$SPLUNK_AZ_NUM" \
  -var "emr_az_num=$EMR_AZ_NUM" -var "device_map=$DEVICE_MAP"

# ---- 7-6. graph ----------------------------------------------------------------------
log "7-6. graph（oss/terraform/pipeline/graph。Neo4j + GDS の ECS と status の Lambda）"
# neo4j ユーザーのパスワード（Neo4j のタスクが ECS の secrets で受け、status の Lambda と Web が SSM から読む）。値は出さない
ensure_secret "/$PREFIX/neo4j-password" password "Neo4j password of the neo4j user (created by oss/ops/up.sh)"
# status の Lambda（arm64）のレイヤーの中身。ドライバは純 Python なので、どの PC でも同じものができる（sync.tf の locals の注記）
if command -v uv >/dev/null; then PIP="uv run --python 3.13 --with pip python -m pip"; else PIP="python3 -m pip"; fi
rm -rf oss/terraform/pipeline/graph/.build/neo4j-layer
$PIP install --quiet --target oss/terraform/pipeline/graph/.build/neo4j-layer/python --only-binary=:all: \
  --platform manylinux2014_aarch64 --python-version 3.13 -r graph/requirements-oss.txt \
  || die "Neo4j のドライバ（graph/requirements-oss.txt）を oss/terraform/pipeline/graph/.build/neo4j-layer/python に入れられなかった"
# アラートの履歴は 7-4 の analytics の Firehose に送る（analytics はいつも作るので、いつも true）
tf_apply pipeline/graph -var "neo4j_image_tag=$NEO4J_TAG" -var alert_history=true

# ---- 8. 配るコマンド -------------------------------------------------------------------
# Web の部品（wheel と手順書）はまだ置かないので、Web の EC2 は立つが画面は出ない（マネージド版の手順 4 と 10 のポートフォワーディングは無い）
log "できた（${ROOTS}。OSS 版）。利用者に配るコマンド:"
tf base/core output -raw start_session_command; echo
echo "lab に入るコマンド:"
tf pipeline/lab output -raw start_session_command; echo
echo "Telegraf（ECS の取りにいく側）に入るコマンド（TASK_ID は下の 1 行目で出る ARN の最後。中で tg gnmi。SNMP_POLL=1 なら tg test でポーリングも見られる）:"
tf pipeline/stream output -raw telegraf_dialin_list_tasks_command; echo
tf pipeline/stream output -raw telegraf_exec_command; echo
echo "Kafbat UI（http://localhost:8082/ 。ユーザー admin）を開くポートフォワード（web の EC2 を踏み台にする）と admin のパスワード:"
tf pipeline/stream output -raw kafka_ui_port_forward_command; echo
tf pipeline/stream output -raw kafka_ui_password_command; echo
echo "Splunk（http://localhost:8000/ 。ユーザー admin）を開くポートフォワードと admin のパスワード:"
tf pipeline/analytics output -raw splunk_port_forward_command; echo
tf pipeline/analytics output -raw splunk_password_command; echo
echo "OpenSearch の admin のパスワードは SSM の $(tf pipeline/analytics output -raw opensearch_password_parameter)（SecureString）"
echo "Neo4j Browser（http://localhost:7474/ 。ユーザー neo4j。パスワードは SSM の $(tf pipeline/graph output -raw neo4j_password_parameter)）を開くポートフォワード 2 本:"
tf pipeline/graph output -raw neo4j_browser_port_forward_command; echo
tf pipeline/graph output -raw neo4j_bolt_port_forward_command; echo
if [ -n "$LAB_WARN" ]; then printf '\033[1;33m%s\033[0m\n' "$LAB_WARN"; fi
printf '\033[1;33m%s\033[0m\n' "時間課金（Kafka 3 台・Telegraf・Kafbat UI・Spark・OpenSearch 3 台・VictoriaMetrics・Splunk・Neo4j の ECS、lab と Web の EC2、EFS、エンドポイント）。使い終わったら当日中に oss/ops/down.sh"
