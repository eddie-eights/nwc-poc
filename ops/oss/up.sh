#!/usr/bin/env bash
# OSS 版（cycle 005「マネージドを OSS に置き換えた環境を作る」）を 1 本で起こす。マネージド版（ops/up.sh）と同じアカウントに並べて立てられる。
#   リソース名の接頭辞と Project タグは <owner>-nwc-oss（マネージド版は <owner>-nwc-poc）。ルートは IaC/terraform/oss/ の下で、
#   state も IaC/terraform/oss/<ルート>/terraform.tfstate に置く（マネージド版の IaC/terraform/aws-managed/ の state とは別）。消すのは ops/oss/down.sh。
#   作るのはマネージド版の AGENT=1 PIPELINE=1 WORKFLOW=1 と同じ範囲で、いつも全部: base/ecr → base/core → agent（AgentCore Runtime）→ Web の部品
#   → pipeline/lab → pipeline/stream（Kafka は MSK でなく ECS の KRaft 3 台。IaC/terraform/oss/pipeline/stream/kafka.tf）
#   → pipeline/graph（Neo4j + GDS の ECS と status の Lambda。上がったら lab の定義からトポロジを入れる）→ pipeline/nautobot
#   → pipeline/analytics（Spark・OpenSearch・VictoriaMetrics・Splunk の ECS と S3 Tables）→ workflow（Temporal のワーカーと AgentCore Gateway）。
#   Spark のタスクは、OpenSearch・VictoriaMetrics が安定し Splunk が HEALTHY になってから起こす。最後に Web（8080）へのポートフォワーディングを開く。
#   格納先はいつも iceberg / opensearch / prometheus / splunk の 4 つ（マネージド版の STORES のようには選ばない）。Grafana もいつも作る。
#   イメージは ECR に無いタグだけ写すかビルドする（ops/oss/oss-images.sh の mirror_oss_images と、ops/up-common.sh の build_splunk / build_grafana / build_agent /
#   build_worker / mirror_temporal / build_nautobot / build_syslog_ng / build_gnmic）。
# 関数は ops/ のもの（ops/common.sh・ops/up-common.sh・ops/lab-common.sh・ops/deploy-env.sh）を読み、写しを作らない。
# 何度打っても同じ状態に収束する（できているものは Terraform が差分なしで飛ばし、ECR にあるタグは写さない）。
#
# 使い方（展開したフォルダの直下で。先に AWS CLI の認証を通しておく。IAM ユーザーなら長期キーのまま打つ）:
#   ops/oss/up.sh                         # deploy.env の OWNER（必須）と下のキーを読む
#   DEPLOY_ENV_FILE=<パス> ops/oss/up.sh  # 別の設定ファイルを読む
#
# 読むキー（deploy.env か環境変数。意味は deploy.env.example）: OWNER / SYSLOG_STANDARD / VPC_CIDR /
#   NETWORK_PERIMETER / ENDPOINTS_AZ_NUM / TELEGRAF_AZ_NUM / EMR_AZ_NUM（Spark のタスクのサブネット）/ SPLUNK_AZ_NUM / SPLUNK_INDEX /
#   RUNTIME_AZ_NUM / LAMBDA_AZ_NUM / NAUTOBOT_DB_AZ_NUM / IMAGE_TAG（agent と worker のイメージのタグ。既定 v1）/ HTTP_SEND /
#   MAX_OFFSETS_PER_TRIGGER と MAX_OFFSETS_PER_TRIGGER_<格納先> / LOCAL_PORT（既定 8080）/ NO_DASHBOARD_PORTFORWARD /
#   TF_VERBOSE / AWS_PROFILE / AWS_CA_BUNDLE。
#   OSS_ROLL（環境変数だけ。deploy.env には書かない。既定 1）: 打ち直しで Kafka か OpenSearch のタスク定義が変わるとき、台を 1 台ずつ入れ替え、
#   間でクラスターが健全に戻るのを ECS Exec で確かめる（ops/oss/roll-nodes.sh）。OSS_ROLL=0 なら待たずに、変わる台を apply で一度に入れ替える
#   機能を選ぶキー（AGENT / PIPELINE / WORKFLOW / SKIP_* / STORES など）と、OSS 版に相手がいない NEPTUNE_AZ_NUM / OPENSEARCH_AZ_NUM / MSK_AZ_NUM は
#   マネージド版のもので、ここでは読まない（書いてあれば注意を出す）。Knowledge Base（CREATE_KB）は OSS 版では作らない。
#   同じ deploy.env をマネージド版と共有するので、VPC_CIDR を書くと両方の VPC が同じ CIDR になる（VPC どうしをつながないので重なってよい）
set -euo pipefail
REGION=ap-northeast-1
. "$(dirname "$0")/../lab-common.sh"          # lab と telegraf の版と、ecr_has / mirror_image / dir_tag / upload_lab
. "$(dirname "$0")/oss-images.sh"             # OSS 版のイメージの名前と版（版の正はここ）と mirror_oss_images
. "$(dirname "$0")/../deploy-env.sh"
resolve_deploy_env_file   # 相対の DEPLOY_ENV_FILE を cd の前の場所で解決する
cd "$(dirname "$0")/../.."
. ops/common.sh
. ops/up-common.sh
. ops/oss/roll-nodes.sh   # Kafka と OpenSearch の台を 1 台ずつ入れ替える（roll_nodes。手順 7-2 と 7-4）
TF_DIR=IaC/terraform/oss   # tf / tf_apply が -chdir で入るルートの親。マネージド版の IaC/terraform/aws-managed/ には触らない
OPS_DIR=ops/oss        # SSM のパラメータのタグ ManagedBy=ops/oss/up.sh（ops/oss/down.sh はこのタグのものだけ消す）
TF_LOG_NAME=tf-oss     # terraform のログは ops/logs/tf-oss-<ルート>-<apply|destroy>.log
TF_INIT_LOCKFILE=readonly  # init は lock を書き換えない（lock はマネージド版へのシンボリックリンク。ops/common.sh の tf_init_root）
NAUTOBOT_CTX=""        # Nautobot のイメージの材料を集める一時ディレクトリ（手順 2）。終わるときに消す
on_exit() {  # 途中で止まっても一時ファイルを消す（ops/up.sh の on_exit と同じ。OSS 版はバックグラウンドの apply を持たないので待ちは無い）
  if [ -n "$TF_AWS_CONFIG" ]; then rm -f "$TF_AWS_CONFIG"; fi
  if [ -n "$NAUTOBOT_CTX" ]; then rm -rf -- "$NAUTOBOT_CTX"; fi
  if [ -n "$ROLL_PLAN" ]; then rm -f "$ROLL_PLAN"; fi
  if [ -n "$SECRET_INPUT" ]; then rm -f -- "$SECRET_INPUT"; fi  # SSM の SecureString の値を書いた一時ファイル（ops/up-common.sh の ensure_secret / ensure_fixed_secret が書く）
}
trap on_exit EXIT

# ---- 0. 道具と認証 -------------------------------------------------------------
log "0. 設定と道具と認証を確かめる（OSS 版）"
load_deploy_env
resolve_name_prefix nwc-oss   # OWNER（必須）と接頭辞 PREFIX=<owner>-nwc-oss
flag_value TF_VERBOSE
log "   デプロイする人の名前: ${OWNER}（リソース名の接頭辞と Project タグは ${PREFIX}）"
IMAGE_TAG="${IMAGE_TAG:-v1}"       # agent と worker のイメージのタグ（作り直すなら変える。マネージド版と同じキー）
LOCAL_PORT="${LOCAL_PORT:-8080}"   # 最後のポートフォワーディングで PC 側に開くポート
# Spark のタスクが HTTP の格納先（opensearch / prometheus / splunk）へ送る所（driver か executor）と、1 回のトリガーに読む件数の上限。
# 意味と書き方はマネージド版（ops/up.sh）と同じで、analytics の var.http_send / max_offsets_per_trigger / max_offsets_per_trigger_by_sink に渡す
HTTP_SEND="${HTTP_SEND:-driver}"
case "$HTTP_SEND" in driver | executor) ;; *) die "HTTP_SEND は driver か executor（小文字）: ${HTTP_SEND}。まだ何も作っていない" ;; esac
MAX_OFFSETS_PER_TRIGGER="${MAX_OFFSETS_PER_TRIGGER:-10000}"
MAX_OFFSETS_BY_SINK=""   # splunk=2000,prometheus=5000 の形（HCL の map の中身）
for v in MAX_OFFSETS_PER_TRIGGER MAX_OFFSETS_PER_TRIGGER_ICEBERG MAX_OFFSETS_PER_TRIGGER_SPLUNK MAX_OFFSETS_PER_TRIGGER_OPENSEARCH MAX_OFFSETS_PER_TRIGGER_PROMETHEUS; do
  val="${!v:-}"
  [ -n "$val" ] || continue
  case "$val" in *[!0-9]* | 0?* | ??????????*) die "$v は 0 以上の整数（0 で上限なし。9 桁まで、先頭に 0 を付けない）: ${val}。まだ何も作っていない" ;; esac
  [ "$v" = MAX_OFFSETS_PER_TRIGGER ] || MAX_OFFSETS_BY_SINK="$MAX_OFFSETS_BY_SINK${MAX_OFFSETS_BY_SINK:+,}$(printf '%s' "${v#MAX_OFFSETS_PER_TRIGGER_}" | tr 'A-Z' 'a-z')=$val"
done
[ -z "${NO_PORTFORWARD:-}" ] || die "NO_PORTFORWARD は NO_DASHBOARD_PORTFORWARD に変わった（意味は同じで、1 なら最後の Web へのポートフォワーディングを開かずに終わる）。deploy.env と環境変数を書き換える。まだ何も作っていない"
flag_value NO_DASHBOARD_PORTFORWARD
SYSLOG_STANDARD="${SYSLOG_STANDARD:-RFC3164}"
case "$SYSLOG_STANDARD" in
  RFC3164|RFC5424) ;;
  *) die "SYSLOG_STANDARD は RFC3164 か RFC5424（大文字）: ${SYSLOG_STANDARD}。まだ何も作っていない" ;;
esac
# SNMP_POLL は 2026-10-09 から使わない（cycle 013 で SNMP のポーリングをやめた）。マネージド版と同じく、書いてあれば注意を出すだけ
if [ -n "${SNMP_POLL:-}" ]; then
  echo "注意: SNMP_POLL は使わない（2026-10-09 に SNMP のポーリングをやめ、IF の状態は gnmic が gNMI で取る。Grafana の link_down も gNMI から出る）。deploy.env から消してよい"
fi
if [ -n "${MDT_SOURCE_CIDRS:-}" ]; then echo "注意: MDT_SOURCE_CIDRS は 2026-10-08 から使わない（cycle 012 で Cisco の MDT の受け口を外した。戻し方は docs/collection.md。deploy.env から消してよい）"; fi
OSS_ROLL="${OSS_ROLL:-1}"; flag_value OSS_ROLL   # 0 なら Kafka と OpenSearch の台を 1 台ずつ入れ替えない（ops/oss/roll-nodes.sh）
NETWORK_PERIMETER="${NETWORK_PERIMETER:-1}"; flag_value NETWORK_PERIMETER
case "${ENDPOINTS_MULTI_AZ:-}" in
  '') ;;
  *) die "ENDPOINTS_MULTI_AZ はなくなった。ENDPOINTS_AZ_NUM（1〜3。既定 1）で書く。まだ何も作っていない" ;;
esac
az_num ENDPOINTS_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"
az_num TELEGRAF_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"
az_num EMR_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"   # Spark の ECS のタスクを置くサブネット（マネージド版の EMR Serverless と同じキー）
az_num SPLUNK_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"
# マネージド版が agent・graph・workflow・nautobot に渡している数（OSS 版も同じルートを当てるので、同じキーで読んで渡す）
az_num RUNTIME_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"   # AgentCore Runtime の ENI
az_num LAMBDA_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"    # agent・graph（status）・workflow（tools）の Lambda の ENI
az_num NAUTOBOT_DB_AZ_NUM 1 1 2 "RDS の Multi-AZ（待機系 1 台）が 2。3 は Multi-AZ DB クラスタで、作っていない"
SPLUNK_INDEX="${SPLUNK_INDEX:-}"
if [ "$SPLUNK_AZ_NUM" -gt 1 ]; then
  [ -z "$SPLUNK_INDEX" ] || die "SPLUNK_AZ_NUM=${SPLUNK_AZ_NUM}（Splunk のクラスター）では index は main だけで、SPLUNK_INDEX は書けない（いまは SPLUNK_INDEX=${SPLUNK_INDEX}）。SPLUNK_INDEX を消すか、SPLUNK_AZ_NUM=1 にする。まだ何も作っていない"
fi
SPLUNK_TASKS=1   # Splunk のタスクの数（7-4b の待ち）。クラスターは manager 1 + indexer SPLUNK_AZ_NUM + search head 1
if [ "$SPLUNK_AZ_NUM" -gt 1 ]; then SPLUNK_TASKS=$((SPLUNK_AZ_NUM + 2)); fi
# ENDPOINTS_AZ_NUM を書いていなければ Runtime の数まで上げ、Runtime より小さく書いてあれば止める（マネージド版と同じ。見かけだけの複数 AZ にしない）
if [ "$ENDPOINTS_AZ_NUM" -lt "$RUNTIME_AZ_NUM" ]; then
  case " $AZ_NUM_SET " in
    *" ENDPOINTS_AZ_NUM "*)
      die "ENDPOINTS_AZ_NUM=$ENDPOINTS_AZ_NUM が RUNTIME_AZ_NUM=$RUNTIME_AZ_NUM より小さい。エンドポイントの無い AZ の Runtime は AWS の API に届かない。ENDPOINTS_AZ_NUM を $RUNTIME_AZ_NUM 以上にする（書かなければ自動でそろえる）。まだ何も作っていない" ;;
  esac
  echo "RUNTIME_AZ_NUM=$RUNTIME_AZ_NUM に合わせて ENDPOINTS_AZ_NUM を $ENDPOINTS_AZ_NUM から $RUNTIME_AZ_NUM に上げる（エンドポイントの費用が $RUNTIME_AZ_NUM 倍になる）"
  ENDPOINTS_AZ_NUM=$RUNTIME_AZ_NUM
fi
AZ_NUM_OVER=""
for k in $AZ_NUM_SET; do
  if [ "$k" != ENDPOINTS_AZ_NUM ] && [ "${!k}" -gt "$ENDPOINTS_AZ_NUM" ]; then AZ_NUM_OVER="$AZ_NUM_OVER $k=${!k}"; fi
done
if [ -n "$AZ_NUM_OVER" ]; then
  echo "注意:${AZ_NUM_OVER} に対して ENDPOINTS_AZ_NUM=${ENDPOINTS_AZ_NUM}。エンドポイントはサブネット a から $ENDPOINTS_AZ_NUM つにしか無いので、その AZ が止まると、ほかの AZ に置いたものも AWS の API に届かない（止めずに進む）"
fi
# Kafka は 3 台を a / b / c に 1 台ずつ置く（kafka.tf。数は変えられない）。エンドポイントが a だけ（ENDPOINTS_AZ_NUM=1）でも動くが、a が止まると
# b / c の Kafka も ECR や CloudWatch Logs に届かない
IGNORED=""
for k in AGENT PIPELINE WORKFLOW CREATE_KB SKIP_LAB SKIP_STREAM SKIP_ANALYTICS SKIP_GRAPH STORES NAUTOBOT GRAFANA MSK_AZ_NUM NEPTUNE_AZ_NUM OPENSEARCH_AZ_NUM; do
  if [ -n "${!k:-}" ]; then IGNORED="$IGNORED $k"; fi
done
if [ -n "$IGNORED" ]; then echo "注意:${IGNORED} はマネージド版（ops/up.sh）のキーで、OSS 版では読まない（ルートはいつも全部作り、格納先はいつも iceberg / opensearch / prometheus / splunk で、Grafana もいつも作る。Knowledge Base は作らない）"; fi
if [ -z "$NETWORK_PERIMETER" ]; then echo "NETWORK_PERIMETER=0: VPC の外からの呼び出しを拒む Deny を外す（エンドポイントは作る。切り分けが済んだら 1 に戻して打ち直す）"; fi
command -v aws >/dev/null || die "aws CLI が無い（docs/setup.md「Terraform を打つ PC 側」）"
command -v terraform >/dev/null || die "terraform が無い（docs/setup.md「Terraform を打つ PC 側」。1.11 以上）"
if command -v python3 >/dev/null; then PY=(python3)
elif command -v uv >/dev/null; then PY=(uv run --python 3.13 python)
else die "python3 も uv も無い（docs/setup.md「Terraform を打つ PC 側」）"; fi
command -v curl >/dev/null || die "curl が無い（lab の containerlab の rpm を取るのに使う。sudo apt install curl）"
command -v docker >/dev/null || die "docker が無い（イメージを ECR に写すのに使う。docs/setup.md「Terraform を打つ PC 側」）"
docker buildx version >/dev/null 2>&1 || die "docker buildx が無い（Ubuntu の docker.io には入っていない。docs/setup.md「Terraform を打つ PC 側」）"
# 最後のポートフォワーディング（手順 10）で要る。全部作った後で落ちないよう、ここで見る
if [ -z "$NO_DASHBOARD_PORTFORWARD" ]; then
  command -v session-manager-plugin >/dev/null || die "Session Manager plugin が無い（手順 10 のポートフォワーディングに使う。docs/setup.md「Terraform を打つ PC 側」。開かないなら NO_DASHBOARD_PORTFORWARD=1）"
fi
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
# 取りにいく側の Telegraf の SG（telegraf_dialin）は 2026-10-09 に gnmic へ替えた（cycle 013。base/core はマネージド版と同じ security_groups.tf）。
# 古い SG は stream の取りにいく側のタスクが付けたままだと消せない（DependencyViolation）ので、stream が残っているなら先に消してもらう
if [ -f "$TF_DIR/base/core/terraform.tfstate" ]; then
  tf_init base/core
  if tf base/core state list 2>/dev/null | grep -qxF 'aws_security_group.workload["telegraf_dialin"]' \
    && [ -s "$TF_DIR/pipeline/stream/terraform.tfstate" ] && { tf_init pipeline/stream; [ -n "$(tf pipeline/stream state list 2>/dev/null)" ]; }; then
    die "IaC/terraform/oss/base/core の state に 2026-10-09 より前の取りにいく側の Telegraf の SG（telegraf_dialin）が残っていて、stream がそれを使っている。先に ops/oss/down.sh で消す（stream だけ先に消してもよい）。まだ何も作っていない"
  fi
fi
ROOTS="base/ecr base/logs base/core agent pipeline/lab pipeline/stream pipeline/graph pipeline/nautobot pipeline/analytics workflow"
# 土台の SSM Agent とポートフォワーディング（ssm ssmmessages）、ECS のタスクのイメージ（ecr.api ecr.dkr）とログ（logs）。
# Kafka の CLUSTER_ID、Kafbat UI・OpenSearch・Splunk・Neo4j のパスワードと HEC の token は ECS の secrets や Lambda が SSM から受ける（ssm）。
# Spark の iceberg は S3 Tables の API（s3tables）、Splunk のアラートは SNS（sns）、status の Lambda のアラートの履歴は Firehose（kinesis-firehose。
# alert_history=true）を通る（マネージド版の analytics と graph と同じ）。Kafka のデータの EFS と、VictoriaMetrics・OpenSearch・Neo4j は
# VPC の中で直に届くので、エンドポイントは要らない（マネージド版の neptune-graph-data と aps-workspaces は OSS 版には相手がいない）。
# agent の Runtime はモデルとガードレール（bedrock-runtime）と AgentCore（bedrock-agentcore）、Nautobot の Job は gnmic の
# サービスを起こし直す ECS の API（ecs）、workflow のワーカーは SQS（sqs）と Gateway（bedrock-agentcore.gateway）、ツールの Lambda の
# 履歴の検索は Athena（athena）を呼ぶ（マネージド版の ops/up.sh がルートごとに足しているものと同じ割り当て）
ENDPOINTS="ssm ssmmessages ecr.api ecr.dkr logs s3tables sns kinesis-firehose bedrock-runtime bedrock-agentcore ecs sqs bedrock-agentcore.gateway athena"

# ---- 1. ECR --------------------------------------------------------------------
log "1. ECR リポジトリ（IaC/terraform/oss/base/ecr）"
tf_apply base/ecr
REPO=$(tf base/ecr output -raw agent_repository_url); echo "REPO=$REPO"
REG="${REPO%%/*}"

# ---- 2. イメージ ----------------------------------------------------------------
log "2. イメージ（ECR に無いタグだけ写すかビルドする）"
NEED_LAB=""; NEED_TELEGRAF=""; NEED_KAFKA_UI=""; NEED_OSS=""; NEED_OSS_BUILD=""; NEED_SPLUNK=""
NEED_AGENT=""; NEED_WORKER=""; NEED_TEMPORAL=""; NEED_NAUTOBOT=""; NEED_REDIS=""; NEED_GRAFANA=""; NEED_SYSLOG_NG=""; NEED_GOFLOW2=""; NEED_GNMIC=""
if ! ecr_has "$PREFIX-lab-srlinux" "$SRLINUX_ECR_TAG" \
  || ! ecr_has "$PREFIX-lab-trex" "$TREX_ECR_TAG"; then NEED_LAB=1
else echo "lab-srlinux:$SRLINUX_ECR_TAG と lab-trex:$TREX_ECR_TAG はある"; fi
TELEGRAF_TAG=$(telegraf_tag) || die "app/telegraf/ のタグを作れなかった"
if ecr_has "$PREFIX-telegraf" "$TELEGRAF_TAG"; then echo "telegraf:$TELEGRAF_TAG はある"; else NEED_TELEGRAF=1; fi
if ecr_has "$PREFIX-kafka-ui" "$OSS_KAFKA_UI_TAG"; then echo "kafka-ui:$OSS_KAFKA_UI_TAG はある"; else NEED_KAFKA_UI=1; fi
# 機器の syslog と NetFlow / sFlow の受け口（cycle 012）。マネージド版と同じイメージ（版は ops/up-common.sh の SYSLOG_NG_VERSION / GOFLOW2_TAG）
SYSLOG_NG_TAG=$(dir_tag "$SYSLOG_NG_VERSION" app/syslog-ng docker/images/syslog-ng/Dockerfile) || die "app/syslog-ng/ のタグを作れなかった"
if ecr_has "$PREFIX-syslog-ng" "$SYSLOG_NG_TAG"; then echo "syslog-ng:$SYSLOG_NG_TAG はある"; else NEED_SYSLOG_NG=1; fi
if ecr_has "$PREFIX-goflow2" "$GOFLOW2_TAG"; then echo "goflow2:$GOFLOW2_TAG はある"; else NEED_GOFLOW2=1; fi
# 機器の gNMI の購読（cycle 013）。マネージド版と同じイメージ（版は ops/up-common.sh の GNMIC_VERSION）
GNMIC_TAG=$(dir_tag "$GNMIC_VERSION" app/gnmic docker/images/gnmic/Dockerfile) || die "app/gnmic/ のタグを作れなかった"
if ecr_has "$PREFIX-gnmic" "$GNMIC_TAG"; then echo "gnmic:$GNMIC_TAG はある"; else NEED_GNMIC=1; fi
# ルートが使う OSS のイメージ（ops/oss/oss-images.sh の OSS_IMAGES。stream の kafka、analytics の opensearch / vmstorage vminsert vmselect / spark、graph の neo4j）
for name in $OSS_IMAGES; do
  tag=$(oss_image_tag "$name") || die "ops/oss/oss-images.sh が $name のタグを作れなかった"
  if ecr_has "$PREFIX-$name" "$tag"; then
    echo "$name:$tag はある"
  else
    NEED_OSS=1
    case "$name" in spark | neo4j) NEED_OSS_BUILD=1 ;; esac   # この 2 つは写すのでなく app/spark/・app/neo4j/ をビルドする（arm64）
  fi
done
SPARK_TAG=$(oss_image_tag spark) || die "app/spark/ のタグを作れなかった"
NEO4J_TAG=$(oss_image_tag neo4j) || die "app/neo4j/ のタグを作れなかった"
# Splunk はマネージド版と同じイメージ（app/splunk/ をビルドして <接頭辞>-splunk に置く）。SPLUNK_TAG と NEED_SPLUNK（ops/up-common.sh）
splunk_image_check
# Grafana もマネージド版と同じイメージ（app/grafana/ をビルドして <接頭辞>-grafana に置く。タグは <版>-<app/grafana/ と Dockerfile のハッシュ>）。OSS 版はいつも作る
GRAFANA_TAG=$(dir_tag "$GRAFANA_VERSION" app/grafana docker/images/grafana/Dockerfile) || die "app/grafana/ のタグを作れなかった"
if ecr_has "$PREFIX-grafana" "$GRAFANA_TAG"; then echo "grafana:$GRAFANA_TAG はある"; else NEED_GRAFANA=1; fi
# agent・worker・Temporal・Nautobot・Redis はマネージド版と同じ中身を、OSS 版の接頭辞のリポジトリに置く（関数は ops/up-common.sh）。
# agent と worker は依存が違う（app/agentcore/・app/temporal/ の requirements-oss.txt。Neo4j のドライバー入り）
if ecr_has "$PREFIX-agent" "$IMAGE_TAG"; then echo "agent:$IMAGE_TAG はある（作り直すなら IMAGE_TAG を変える）"; else NEED_AGENT=1; fi
if ecr_has "$PREFIX-worker" "$IMAGE_TAG"; then echo "worker:$IMAGE_TAG はある"; else NEED_WORKER=1; fi
if ecr_has "$PREFIX-temporal" "$TEMPORAL_TAG"; then echo "temporal:$TEMPORAL_TAG はある"; else NEED_TEMPORAL=1; fi
# Nautobot のタグは、イメージに入る材料（app/nautobot/ と app/agentcore/graph.py・toolkit.py と lab の定義）の中身と docker/images/nautobot/Dockerfile から作る
NAUTOBOT_CTX=$(mktemp -d "${TMPDIR:-/tmp}/$PREFIX-nautobot.XXXXXX") || die "一時ディレクトリを作れない（TMPDIR）"
nautobot_context "$NAUTOBOT_CTX" || die "Nautobot のイメージの材料（app/nautobot/ と app/agentcore/graph.py・toolkit.py と lab の定義）を集められなかった"
NAUTOBOT_TAG=$(dir_tag "$NAUTOBOT_VERSION" "$NAUTOBOT_CTX" docker/images/nautobot/Dockerfile) || die "app/nautobot/ のタグを作れなかった"
if ecr_has "$PREFIX-nautobot" "$NAUTOBOT_TAG"; then echo "nautobot:$NAUTOBOT_TAG はある"; else NEED_NAUTOBOT=1; fi
if ecr_has "$PREFIX-redis" "$REDIS_TAG"; then echo "redis:$REDIS_TAG はある"; else NEED_REDIS=1; fi
if [ -z "$NEED_LAB$NEED_TELEGRAF$NEED_KAFKA_UI$NEED_OSS$NEED_SPLUNK$NEED_GRAFANA$NEED_AGENT$NEED_WORKER$NEED_TEMPORAL$NEED_NAUTOBOT$NEED_REDIS$NEED_SYSLOG_NG$NEED_GOFLOW2$NEED_GNMIC" ]; then
  echo "写すイメージもビルドするイメージも無い"
else
  docker info >/dev/null 2>&1 || die "dockerd に接続できない（WSL なら sudo service docker start。docs/setup.md「Terraform を打つ PC 側」）"
  # agent / worker / nautobot / grafana / spark / neo4j は arm64 で RUN があるので、x86_64 の PC では QEMU（binfmt）が要る（写すだけのものと、COPY だけの
  # telegraf と syslog-ng と gnmic、amd64 の splunk は要らない）。出力は変数で受けてから探す（grep -q が先に閉じると docker が SIGPIPE で落ちることがある）
  BUILDX_LS=$(docker buildx ls 2>/dev/null || true)
  if [ -n "$NEED_AGENT$NEED_WORKER$NEED_NAUTOBOT$NEED_GRAFANA$NEED_OSS_BUILD" ] && ! grep -q 'linux/arm64' <<<"$BUILDX_LS"; then
    die "docker buildx ls の Platforms に linux/arm64 が無い（docs/setup.md「WSL2（Ubuntu）」の binfmt の行）"
  fi
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REG"
  if [ -n "$NEED_LAB" ]; then
    mirror_lab_images "$REG" "$PREFIX" || die "lab のイメージを ECR に置けなかった"
  fi
  if [ -n "$NEED_TELEGRAF" ]; then
    build_telegraf "$REG/$PREFIX-telegraf:$TELEGRAF_TAG"
  fi
  if [ -n "$NEED_KAFKA_UI" ]; then
    # Kafbat UI（Web の EC2 の Docker。cycle 010）。マネージド版と同じイメージを、OSS 版の接頭辞のリポジトリに写す
    mirror_image "$OSS_KAFKA_UI_IMAGE:$OSS_KAFKA_UI_TAG" "$REG/$PREFIX-kafka-ui:$OSS_KAFKA_UI_TAG" || die "kafka-ui のイメージを ECR に置けなかった"
  fi
  if [ -n "$NEED_SYSLOG_NG" ]; then
    build_syslog_ng   # arm64 で COPY だけ（ops/up-common.sh。マネージド版と共通）
  fi
  if [ -n "$NEED_GNMIC" ]; then
    build_gnmic   # 機器の gNMI の購読。arm64 で COPY だけ（ops/up-common.sh。マネージド版と共通）
  fi
  if [ -n "$NEED_GOFLOW2" ]; then
    mirror_image "netsampler/goflow2:$GOFLOW2_TAG" "$REG/$PREFIX-goflow2:$GOFLOW2_TAG" || die "goflow2 のイメージを ECR に置けなかった"
  fi
  if [ -n "$NEED_OSS" ]; then
    # 公開イメージ（Fargate は VPC の中から ECR しか引けないので写す。arm64）と、app/spark/・app/neo4j/ のビルド（arm64）
    # shellcheck disable=SC2086
    mirror_oss_images "$REG" "$PREFIX" $OSS_IMAGES || die "OSS 版のイメージ（${OSS_IMAGES}）を ECR に置けなかった"
  fi
  if [ -n "$NEED_SPLUNK" ]; then
    build_splunk   # amd64（ops/up-common.sh。マネージド版と共通）
  fi
  if [ -n "$NEED_GRAFANA" ]; then
    build_grafana   # arm64（ops/up-common.sh。マネージド版と共通）
  fi
  if [ -n "$NEED_AGENT" ]; then
    build_agent "$REPO:$IMAGE_TAG" requirements-oss.txt   # Runtime は app/agentcore/graph.py を GRAPH_BACKEND=neo4j で使うので、Neo4j のドライバーを入れる
  fi
  if [ -n "$NEED_WORKER" ]; then
    build_worker "$IMAGE_TAG" requirements-oss.txt   # ワーカーは app/agentcore/graph.py を GRAPH_BACKEND=neo4j で使うので、Neo4j のドライバーを入れる
  fi
  if [ -n "$NEED_TEMPORAL" ]; then
    mirror_temporal
  fi
  if [ -n "$NEED_NAUTOBOT" ]; then
    build_nautobot "$NAUTOBOT_TAG" "$NAUTOBOT_CTX" requirements-oss.txt   # Job は app/agentcore/graph.py を GRAPH_BACKEND=neo4j で使うので、Neo4j のドライバーを入れる
  fi
  if [ -n "$NEED_REDIS" ]; then
    mirror_image "redis:$REDIS_TAG" "$REG/$PREFIX-redis:$REDIS_TAG" || die "redis のイメージを ECR に置けなかった"
  fi
fi

# ---- 3. 土台 --------------------------------------------------------------------
log "3. 土台（IaC/terraform/oss/base/core。VPC / Web の EC2 / バケット / ロール / Kafka のデータの EFS。初回は 3〜5 分）"
MAIN_VARS=()
if [ -n "${VPC_CIDR:-}" ];    then MAIN_VARS+=(-var "vpc_cidr=$VPC_CIDR"); fi
MAIN_VARS+=(-var "interface_endpoints=[\"$(printf '%s' "$ENDPOINTS" | sed 's/ /","/g')\"]")
MAIN_VARS+=(-var "network_perimeter=$([ -n "$NETWORK_PERIMETER" ] && echo true || echo false)")
MAIN_VARS+=(-var "endpoints_az_num=$ENDPOINTS_AZ_NUM")
echo "エンドポイント: $ENDPOINTS"
tf_apply base/core ${MAIN_VARS[@]+"${MAIN_VARS[@]}"}
INSTANCE_ID=$(tf base/core output -raw web_instance_id)
KB_BUCKET=$(tf base/core output -raw kb_bucket_name)
echo "INSTANCE_ID=$INSTANCE_ID KB_BUCKET=$KB_BUCKET"

# ---- 3-3. agent ----------------------------------------------------------------
# マネージド版と同じルート（IaC/terraform/oss/agent は IaC/terraform/aws-managed/agent へのリンク）。Knowledge Base は作らない（OpenSearch Serverless を使うので OSS 版には入れない）
log "3-3. agent（IaC/terraform/oss/agent。AgentCore Runtime + ガードレール。初回は 5〜10 分）"
tf_apply agent -var "agent_image_tag=$IMAGE_TAG" -var "runtime_az_num=$RUNTIME_AZ_NUM" -var "lambda_az_num=$LAMBDA_AZ_NUM"
LOG_GROUP=$(tf agent output -raw runtime_log_group_name)
echo "Runtime の ARN は SSM の $(tf agent output -raw runtime_arn_parameter_name) に置いた（Web は 60 秒以内に拾う）"

# ---- 4. Web の部品 ------------------------------------------------------------------
# OSS 版の Web は app/dashboard/requirements-oss.txt（マネージド版の依存 + Neo4j のドライバー）で入れる（IaC/terraform/aws-managed/base/core の web.tf の graph_backend）。
# wheel の置き場はマネージド版の wheels/ と分ける（同じフォルダから両方の up.sh を打っても混ざらない）
log "4-1. wheel（arm64 / cp313。app/dashboard/requirements-oss.txt）"
fetch_wheels wheels-oss app/dashboard/requirements-oss.txt app/dashboard/requirements.txt   # requirements-oss.txt は -r で requirements.txt を読むので、両方の版を見る

log "4-2. Web の部品を s3://$KB_BUCKET/web/ に置く"
for f in app/dashboard/*.py; do aws s3 cp --only-show-errors "$f" "s3://$KB_BUCKET/web/${f#app/dashboard/}"; done
aws s3 cp --only-show-errors app/dashboard/requirements.txt "s3://$KB_BUCKET/web/requirements.txt"   # requirements-oss.txt が -r で読む
aws s3 cp --only-show-errors app/dashboard/requirements-oss.txt "s3://$KB_BUCKET/web/requirements-oss.txt"
for f in toolkit topology graph proposals; do aws s3 cp --only-show-errors "app/agentcore/$f.py" "s3://$KB_BUCKET/web/$f.py"; done
aws s3 cp --only-show-errors app/agentcore/data/ "s3://$KB_BUCKET/web/data/" --recursive
aws s3 sync --only-show-errors --delete --exclude .requirements.sha256 wheels-oss/ "s3://$KB_BUCKET/web/wheels/"

log "4-4. EC2 を再起動して Web を立てる（初回の apply 時点では app/dashboard/ が無いため）"
wait_ssm_online "$INSTANCE_ID"
run_on_instance "$INSTANCE_ID" "true"          # 初回の user_data が終わるのを待ってから再起動する
aws ec2 reboot-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
sleep 30
wait_ssm_online "$INSTANCE_ID"
# Gradio が 8080 を聞いているかで見る（is-active は落ちて再起動するまでの数秒も active と読む）。2 分待って聞いていなければ status と journald を出して止まる
WEB_ACTIVE="for i in \$(seq 1 24); do ss -ltn 'sport = :8080' | grep -q LISTEN && exit 0; sleep 5; done; systemctl --no-pager status $PREFIX-web.service; journalctl --no-pager -u $PREFIX-web.service -n 50; exit 1"
run_on_instance "$INSTANCE_ID" "$WEB_ACTIVE"
echo "Web が動いている"

# ---- 5. lab の材料 -----------------------------------------------------------------
# lab の EC2 は起動のたびに s3://<バケット>/lab/ を読む。apply より前に置けば、最初の起動で入る
log "5-1. lab の材料（containerlab の rpm とトポロジ）を s3://$KB_BUCKET/lab/ に置く"
upload_lab "$KB_BUCKET" || die "lab の材料を s3://$KB_BUCKET/lab/ に置けなかった"

# ---- 6. lab ---------------------------------------------------------------------
LAB_WARN=""
LAB_NODES=$(grep -cE '^ *kind: (nokia_srlinux|linux)$' app/containerlab/splab.clab.yml.in)   # containerlab のノードの数
log "6. lab（IaC/terraform/oss/pipeline/lab。EC2 の中でトポロジが上がるまで 10 分ほど。forward_to_telegraf=true）"
tf_apply pipeline/lab -var forward_to_telegraf=true
LAB_INSTANCE_ID=$(tf pipeline/lab output -raw lab_instance_id); echo "LAB_INSTANCE_ID=$LAB_INSTANCE_ID"

# ---- 7. stream ------------------------------------------------------------------
log "7. stream（IaC/terraform/oss/pipeline/stream。Kafka は ECS の KRaft 3 台、Telegraf・gnmic も ECS のタスク。Kafbat UI は Web の EC2 の Docker で、ここで接続先を SSM に書く）"
# gnmic の gNMI の購読先は lab の定義から作る（マネージド版と同じ）。SNMP のポーリング先は cycle 013 でなくした（SNMP は trap だけ受ける）
GNMI_TARGETS=$("${PY[@]}" app/containerlab/lab_topology.py app/containerlab --gnmi-targets) || die "app/containerlab/lab_topology.py が lab の定義から gNMI の購読先を作れなかった"
echo "gnmic の gNMI の購読先: $GNMI_TARGETS"
echo "syslog-ng の syslog の形式: $SYSLOG_STANDARD"
if [ "$SYSLOG_STANDARD" != "$LAB_SYSLOG_STANDARD" ]; then
  echo "注意: lab の SR Linux は $LAB_SYSLOG_STANDARD で送るので、SYSLOG_STANDARD=$SYSLOG_STANDARD では lab のログの項目（ホスト名・本文など）が崩れる。lab のログまで見るなら SYSLOG_STANDARD=$LAB_SYSLOG_STANDARD"
fi
# 機器の認証情報（最初の値は lab の公開既定値。ops/lab-common.sh）。もうあれば触らない
ensure_fixed_secret "/$PREFIX/gnmic/gnmi-username" "$LAB_GNMI_USERNAME" "gNMI username of the gnmic task (created by ops/oss/up.sh with the containerlab default)"
ensure_fixed_secret "/$PREFIX/gnmic/gnmi-password" "$LAB_GNMI_PASSWORD" "gNMI password of the gnmic task (created by ops/oss/up.sh with the containerlab default)"
ensure_secret "/$PREFIX/kafka-ui/admin-password" password "Kafbat UI admin password (created by ops/oss/up.sh)"
# Kafka の KRaft の CLUSTER_ID（3 台で同じ値）。stream の apply より前に 1 回だけ作り、kafka.tf がタスクの secrets で渡す。
# 作り直すと EFS に書いたデータと合わなくなるので、あれば触らない（消すのは ops/oss/down.sh）。値は画面にもログにも出さない
ensure_secret "/$PREFIX/kafka/cluster-id" kafka-cluster-id "Kafka KRaft CLUSTER_ID shared by the three nodes (created by ops/oss/up.sh)"
# gnmic の購読先の一覧は Nautobot の Job が書く（7-3c。マネージド版と同じ gnmi_targets_from_nautobot=true。Terraform は最初の値として
# lab の一覧を置き、あとは触らない）
STREAM_VARS=(-var "telegraf_image_tag=$TELEGRAF_TAG" -var "kafka_ui_image_tag=$OSS_KAFKA_UI_TAG" -var "kafka_image_tag=$OSS_KAFKA_TAG"
  -var "gnmi_targets=$GNMI_TARGETS" -var gnmi_targets_from_nautobot=true -var "gnmic_image_tag=$GNMIC_TAG"
  -var "syslog_standard=$SYSLOG_STANDARD"
  -var "telegraf_az_num=$TELEGRAF_AZ_NUM" -var "syslog_ng_image_tag=$SYSLOG_NG_TAG" -var "goflow2_image_tag=$GOFLOW2_TAG")
# 打ち直しで Kafka のタスク定義が変わるなら、先に 1 台ずつ入れ替える（3 台が同時に止まると controller の過半数が無くなる）
roll_nodes kafka pipeline/stream "${STREAM_VARS[@]}"
tf_apply pipeline/stream "${STREAM_VARS[@]}"

# ---- 7-2. lab と Kafka と Telegraf・gnmic の中を確かめる ------------------------------------------
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
log "7-2b. lab の EC2 と stream の ECS（Telegraf・gnmic・syslog-ng・GoFlow2）のあいだに gNMI / trap / syslog / NetFlow / sFlow を通す（lab forward）"
ssm_run "$LAB_INSTANCE_ID" "[ ! -x /usr/local/bin/lab ] || /usr/local/bin/lab forward" \
  || printf '\033[1;33m%s\033[0m\n' "lab forward が失敗した。lab の EC2 で sudo lab forward-status を見る（docs/pipeline.md）"
# Telegraf と gnmic は Kafka に書くので、Kafka を先に待つ。3 台は同時に起き、2 台そろえばコントローラーの過半数になる
log "7-2c. Kafka の ECS のサービス 3 つ（kafka-1〜3）が安定するのを待つ（イメージの取得と EFS のマウント）"
KAFKA_CLUSTER=$(tf pipeline/stream output -raw kafka_ecs_cluster_name)
KAFKA_SERVICES=$(tf pipeline/stream output -json kafka_service_names \
  | "${PY[@]}" -c 'import json, sys; print(" ".join(v for _, v in sorted(json.load(sys.stdin).items())))') \
  || die "IaC/terraform/oss/pipeline/stream の output kafka_service_names を読めなかった"
# shellcheck disable=SC2086
if aws ecs wait services-stable --region "$REGION" --cluster "$KAFKA_CLUSTER" --services $KAFKA_SERVICES; then
  echo "Kafka は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/stream output -raw kafka_log_group_name) --follow）"
else
  printf '\033[1;33m%s\033[0m\n' "Kafka のサービス（${KAFKA_SERVICES}）が 10 分たっても安定しない。aws ecs list-tasks --region $REGION --cluster $KAFKA_CLUSTER --desired-status STOPPED とロググループ $(tf pipeline/stream output -raw kafka_log_group_name) を見る"
fi
log "7-2d. Telegraf（受ける側）と gnmic の ECS のサービスが安定するのを待つ（1〜3 分）"
TG_CLUSTER=$(tf pipeline/stream output -raw telegraf_cluster_name); TG_DIALOUT_SERVICE=$(tf pipeline/stream output -raw telegraf_dialout_service_name)
GNMIC_SERVICE=$(tf pipeline/stream output -raw gnmic_service_name)
if aws ecs wait services-stable --region "$REGION" --cluster "$TG_CLUSTER" --services "$TG_DIALOUT_SERVICE" "$GNMIC_SERVICE"; then
  echo "Telegraf と gnmic は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/stream output -raw telegraf_log_group_name) --follow と $(tf pipeline/stream output -raw gnmic_log_group_name)）"
else
  printf '\033[1;33m%s\033[0m\n' "Telegraf か gnmic のサービスが 10 分たっても安定しない。Telegraf は $(tf pipeline/stream output -raw telegraf_dialout_list_tasks_command)、gnmic は $(tf pipeline/stream output -raw gnmic_list_tasks_command) と、ロググループ $(tf pipeline/stream output -raw telegraf_log_group_name) と $(tf pipeline/stream output -raw gnmic_log_group_name) を見る（docs/troubleshooting.md）"
fi
log "7-2e. syslog-ng と GoFlow2 の ECS のサービス（機器の syslog と NetFlow / sFlow の受け口。cycle 012）が安定するのを待つ（1〜3 分）"
SYSLOG_NG_SERVICE=$(tf pipeline/stream output -raw syslog_ng_service_name); GOFLOW2_SERVICE=$(tf pipeline/stream output -raw goflow2_service_name)
if aws ecs wait services-stable --region "$REGION" --cluster "$TG_CLUSTER" --services "$SYSLOG_NG_SERVICE" "$GOFLOW2_SERVICE"; then
  echo "syslog-ng と GoFlow2 は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/stream output -raw syslog_ng_log_group_name) --follow と $(tf pipeline/stream output -raw goflow2_log_group_name)）"
else
  printf '\033[1;33m%s\033[0m\n' "syslog-ng か GoFlow2 のサービスが 10 分たっても安定しない。syslog-ng は $(tf pipeline/stream output -raw syslog_ng_list_tasks_command)、GoFlow2 は $(tf pipeline/stream output -raw goflow2_list_tasks_command) とロググループ $(tf pipeline/stream output -raw syslog_ng_log_group_name)・$(tf pipeline/stream output -raw goflow2_log_group_name) を見る（docs/troubleshooting.md）"
fi

# ---- 7-3. graph ----------------------------------------------------------------------
# Nautobot と workflow が graph の output（neo4j_uri）を読むので、その 2 つより前に当てる（マネージド版の Neptune と同じ位置）
log "7-3. graph（IaC/terraform/oss/pipeline/graph。Neo4j + GDS の ECS と status の Lambda）"
# neo4j ユーザーのパスワード（Neo4j のタスクが ECS の secrets で受け、status の Lambda と Web が SSM から読む）。値は出さない
ensure_secret "/$PREFIX/neo4j-password" password "Neo4j password of the neo4j user (created by ops/oss/up.sh)"
# status の Lambda（arm64）のレイヤーの中身。ドライバは純 Python なので、どの PC でも同じものができる（sync.tf の locals の注記）。
# app/graph/requirements-oss.txt と pip に渡す platform / python の版のハッシュを .build/neo4j-layer.sha256 に残し、同じなら作り直さない
# （毎回 pip を回さない。zip の中身は変わらない。版を変えたらハッシュが変わって作り直す）。
# platform と python の版は、pip に渡すのもハッシュに入れるのもこの 2 つの変数（別々に書くと片方だけ変えてスタンプが合ったままになる）。
# LAYER_PYVER は sync.tf の Lambda の runtime（python3.13）と同じにする（tests/test_oss_ops.py が突き合わせる）。片方だけ変えると読めないレイヤーになる
LAYER_PLATFORM=manylinux2014_aarch64; LAYER_PYVER=3.13
LAYER_SHA=$( { cat app/graph/requirements-oss.txt; echo "$LAYER_PLATFORM $LAYER_PYVER"; } \
  | "${PY[@]}" -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')
if [ -d IaC/terraform/oss/pipeline/graph/.build/neo4j-layer/python/neo4j ] \
   && [ "$(cat IaC/terraform/oss/pipeline/graph/.build/neo4j-layer.sha256 2>/dev/null)" = "$LAYER_SHA" ]; then
  echo "Neo4j のドライバのレイヤー（IaC/terraform/oss/pipeline/graph/.build/neo4j-layer）はある（app/graph/requirements-oss.txt は変わっていない）"
else
  if command -v uv >/dev/null; then PIP="uv run --python 3.13 --with pip python -m pip"; else PIP="python3 -m pip"; fi
  rm -rf IaC/terraform/oss/pipeline/graph/.build/neo4j-layer IaC/terraform/oss/pipeline/graph/.build/neo4j-layer.sha256
  $PIP install --quiet --target IaC/terraform/oss/pipeline/graph/.build/neo4j-layer/python --only-binary=:all: \
    --platform "$LAYER_PLATFORM" --python-version "$LAYER_PYVER" -r app/graph/requirements-oss.txt \
    || die "Neo4j のドライバ（app/graph/requirements-oss.txt）を IaC/terraform/oss/pipeline/graph/.build/neo4j-layer/python に入れられなかった"
  printf '%s\n' "$LAYER_SHA" > IaC/terraform/oss/pipeline/graph/.build/neo4j-layer.sha256
fi
# アラートの履歴は 7-4 の analytics の Firehose に送る（analytics はいつも作るので、いつも true。Firehose ができるのは 7-4 で、それまでは送れない）
tf_apply pipeline/graph -var "neo4j_image_tag=$NEO4J_TAG" -var alert_history=true -var "lambda_az_num=$LAMBDA_AZ_NUM"

# サービスが安定する（タスクが RUNNING のまま落ちない）のを待つ。Neo4j の ECS の healthCheck（Bolt の 7687 が開いているか）の HEALTHY は
# ここでは待たない。Bolt に応えるかは次の 7-3b が 30 秒おきに確かめる
log "7-3a. Neo4j の ECS のサービスが安定するのを待つ（イメージの取得と起動。2〜5 分）"
GRAPH_WARN=""
GRAPH_CLUSTER=$(tf pipeline/graph output -raw graph_cluster_name); NEO4J_SERVICE=$(tf pipeline/graph output -raw neo4j_service_name)
if aws ecs wait services-stable --region "$REGION" --cluster "$GRAPH_CLUSTER" --services "$NEO4J_SERVICE"; then
  echo "Neo4j は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/graph output -raw neo4j_log_group_name) --follow）"
  # マネージド版が Neptune に入れているのと同じ中身（app/containerlab/lab_topology.py が lab の定義から作る機器・回線・IP 層 / EVPN・BGP 層）を、同じ
  # ops/seed_graph.py で入れる。Web の EC2 の上で打つ（Web の環境変数に GRAPH_BACKEND=neo4j があり、app/agentcore/graph.py が Neo4j に書く）
  log "7-3b. Neo4j が空なら lab の定義からトポロジを入れる（初期ロード。入っていれば何もしない）"
  LAB_TOPOLOGY_B64=$("${PY[@]}" app/containerlab/lab_topology.py app/containerlab | base64 | tr -d '\n') || die "app/containerlab/lab_topology.py が lab の定義を読めなかった"
  run_on_instance "$INSTANCE_ID" "echo $(base64 < ops/seed_graph.py | tr -d '\n') | base64 -d | NAME_PREFIX=$PREFIX LAB_TOPOLOGY_B64=$LAB_TOPOLOGY_B64 /usr/bin/python3.13 -"
else
  GRAPH_WARN="Neo4j のサービス（${NEO4J_SERVICE}）が 10 分たっても安定しない。トポロジは入れていない。aws ecs list-tasks --region $REGION --cluster $GRAPH_CLUSTER --desired-status STOPPED とロググループ $(tf pipeline/graph output -raw neo4j_log_group_name) を見て、直ったら ops/oss/up.sh を打ち直す"
  printf '\033[1;33m%s\033[0m\n' "$GRAPH_WARN"
fi

# ---- 7-3c. nautobot ------------------------------------------------------------------
# マネージド版と同じルート（IaC/terraform/oss/pipeline/nautobot は IaC/terraform/aws-managed/pipeline/nautobot へのリンク）。Job は gnmic の購読先の一覧（SSM）と、
# Neo4j の物理層と変更履歴を書く（graph の state に neo4j_uri があるので、ルートが GRAPH_BACKEND=neo4j・NEO4J_URI と secrets の NEO4J_PASSWORD を渡す）。
# 7-3b で lab の定義から入れたトポロジに、起動時の同期（bootstrap.py）が Nautobot の中身を差分で合わせる（status と上の層は残る）
log "7-3c. nautobot（IaC/terraform/oss/pipeline/nautobot。Aurora / RDS と ECS。初回は 15 分ほど）"
NAUTOBOT_WARN=""
ensure_nautobot_secrets
tf_apply pipeline/nautobot -var "nautobot_image_tag=$NAUTOBOT_TAG" -var "redis_image_tag=$REDIS_TAG" -var "nautobot_db_az_num=$NAUTOBOT_DB_AZ_NUM"
echo "Nautobot の Job の書き先: $(tf pipeline/nautobot output -json sync_targets)"
NB_CLUSTER=$(tf pipeline/nautobot output -raw cluster_name); NB_SERVICE=$(tf pipeline/nautobot output -raw service_name)
# services-stable は 10 分で諦めるので、2 回まで待つ（初回は DB の初期化で 10 分を超える）
if aws ecs wait services-stable --region "$REGION" --cluster "$NB_CLUSTER" --services "$NB_SERVICE" 2>/dev/null \
  || aws ecs wait services-stable --region "$REGION" --cluster "$NB_CLUSTER" --services "$NB_SERVICE"; then
  echo "Nautobot は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/nautobot output -raw log_group_name) --follow）"
else
  NAUTOBOT_WARN="Nautobot のサービスが 20 分たっても安定しない。$(tf pipeline/nautobot output -raw list_tasks_command) とロググループ $(tf pipeline/nautobot output -raw log_group_name) を見る（docs/troubleshooting.md）"
  printf '\033[1;33m%s\033[0m\n' "$NAUTOBOT_WARN"
fi

# ---- 7-4. analytics ----------------------------------------------------------------
log "7-4. analytics（IaC/terraform/oss/pipeline/analytics。Spark・OpenSearch 3 台・VictoriaMetrics・Splunk の ECS と S3 Tables。格納先: iceberg / opensearch / prometheus / splunk）"
# OpenSearch の admin のパスワード（2.12 からの OPENSEARCH_INITIAL_ADMIN_PASSWORD は大文字・小文字・数字・記号を求める）。OpenSearch のタスクと
# Spark のタスクが ECS の secrets で受ける。値は Terraform の state にも画面にも出さない（ops/up-common.sh）
ensure_secret "/$PREFIX/opensearch-password" strong-password "OpenSearch admin password (created by ops/oss/up.sh)"
# Splunk の管理者のパスワードと HEC の token（クラスターなら合言葉も）。マネージド版と同じ関数（ops/up-common.sh）
echo "Splunk Enterprise（splunk/splunk:${SPLUNK_VERSION}・試用ライセンス）を立てる。Splunk のライセンスと Splunk General Terms に同意して起動する"
ensure_splunk_secrets "$SPLUNK_AZ_NUM"
# Grafana の admin のパスワード。Grafana のタスクが ECS の secrets で受ける（grafana.tf。マネージド版と同じ名前）
ensure_secret "/$PREFIX/grafana/admin-password" password "Grafana admin password (created by ops/oss/up.sh)"
ensure_s3tables_catalog   # alert_events への Firehose（マネージド版の history.tf へのリンク）はこのカタログ越しにテーブルを引く
# device map（別名=機器名,...）。Splunk のアラートアクションと Spark の prometheus / opensearch の sysName に使う（マネージド版と同じ）
DEVICE_MAP=$("${PY[@]}" app/containerlab/lab_topology.py app/containerlab --device-map) || die "app/containerlab/lab_topology.py が lab の定義から device map を作れなかった"
# Spark のサービスは desired_count=0 で作る（spark.tf。書き先が上がる前に起こさない）。起こすのは 7-4c
ANALYTICS_VARS=(-var 'sinks=["iceberg","opensearch","prometheus","splunk"]'
  -var "spark_image_tag=$SPARK_TAG" -var "opensearch_image_tag=$OSS_OPENSEARCH_TAG" -var "victoriametrics_image_tag=$OSS_VM_TAG"
  -var "splunk_image_tag=$SPLUNK_TAG" -var "splunk_index=$SPLUNK_INDEX" -var "splunk_az_num=$SPLUNK_AZ_NUM"
  -var create_grafana=true -var "grafana_image_tag=$GRAFANA_TAG"
  -var "emr_az_num=$EMR_AZ_NUM" -var "device_map=$DEVICE_MAP" -var "http_send=$HTTP_SEND"
  -var "max_offsets_per_trigger=$MAX_OFFSETS_PER_TRIGGER" -var "max_offsets_per_trigger_by_sink={$MAX_OFFSETS_BY_SINK}")
# 打ち直しで OpenSearch のタスク定義が変わるなら、先に 1 台ずつ入れ替え、間で green に戻るのを待つ（インデックスはタスクの中にあり、
# データ 2 台が同時に入れ替わると消える）
roll_nodes opensearch pipeline/analytics "${ANALYTICS_VARS[@]}"
tf_apply pipeline/analytics "${ANALYTICS_VARS[@]}"

# ---- 7-4b. 格納先が上がるのを待つ ---------------------------------------------------------
# OpenSearch と VictoriaMetrics は ECS の healthCheck を持たないので、サービスが安定するのを待つ。Splunk は healthCheck があるので HEALTHY を待つ
STORE_WARN=""
AN_CLUSTER=$(tf_output pipeline/analytics analytics_cluster_name) || exit 1  # 9-2 の Grafana の確かめもこのクラスター
log "7-4b. OpenSearch（3 台）と VictoriaMetrics（vmstorage / vminsert / vmselect）の ECS のサービスが安定するのを待つ"
OS_SERVICES=$(tf pipeline/analytics output -json opensearch_service_names \
  | "${PY[@]}" -c 'import json, sys; print(" ".join(v for _, v in sorted(json.load(sys.stdin).items())))') \
  || die "IaC/terraform/oss/pipeline/analytics の output opensearch_service_names を読めなかった"
VM_SERVICES=$(tf pipeline/analytics output -json victoriametrics_service_names \
  | "${PY[@]}" -c 'import json, sys; print(" ".join(v for _, v in sorted(json.load(sys.stdin).items())))') \
  || die "IaC/terraform/oss/pipeline/analytics の output victoriametrics_service_names を読めなかった"
# shellcheck disable=SC2086
if aws ecs wait services-stable --region "$REGION" --cluster "$AN_CLUSTER" --services $OS_SERVICES; then
  echo "OpenSearch は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/analytics output -raw opensearch_log_group_name) --follow）"
else
  STORE_WARN="$STORE_WARN OpenSearch（${OS_SERVICES}。ロググループ $(tf pipeline/analytics output -raw opensearch_log_group_name)）"
fi
# shellcheck disable=SC2086
if aws ecs wait services-stable --region "$REGION" --cluster "$AN_CLUSTER" --services $VM_SERVICES; then
  echo "VictoriaMetrics は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/analytics output -raw victoriametrics_log_group_name) --follow）"
else
  STORE_WARN="$STORE_WARN VictoriaMetrics（${VM_SERVICES}。ロググループ $(tf pipeline/analytics output -raw victoriametrics_log_group_name)）"
fi
log "7-4b. Splunk のタスク（$SPLUNK_TASKS 個）が HEALTHY になるのを待つ（初回は 5〜10 分。クラスターはもっとかかる）"
SP_SERVICES=$(tf pipeline/analytics output -raw splunk_service_name)
if [ "$SPLUNK_AZ_NUM" -gt 1 ]; then
  SP_SERVICES="$SP_SERVICES $(tf pipeline/analytics output -raw splunk_cm_service_name) $(tf pipeline/analytics output -raw splunk_idx_service_name)"
fi
SP_HEALTHY=0; SP_HEALTH=""
for _ in $(seq 1 80); do
  SP_TASKS=""
  for svc in $SP_SERVICES; do
    SP_TASKS="$SP_TASKS $(aws ecs list-tasks --region "$REGION" --cluster "$AN_CLUSTER" --service-name "$svc" --desired-status RUNNING \
      --query 'taskArns' --output text 2>/dev/null || echo "")"
  done
  # shellcheck disable=SC2086,SC2116
  SP_TASKS=$(echo $SP_TASKS)
  if [ -n "$SP_TASKS" ] && [ "$SP_TASKS" != None ]; then
    # shellcheck disable=SC2086
    SP_HEALTH=$(aws ecs describe-tasks --region "$REGION" --cluster "$AN_CLUSTER" --tasks $SP_TASKS \
      --query 'tasks[].healthStatus' --output text 2>/dev/null || echo "")
    # shellcheck disable=SC2086
    SP_HEALTHY=$(echo $SP_HEALTH | tr ' \t' '\n\n' | grep -c '^HEALTHY$' || true)
    if [ "$SP_HEALTHY" -ge "$SPLUNK_TASKS" ]; then break; fi
  fi
  sleep 15
done
if [ "$SP_HEALTHY" -ge "$SPLUNK_TASKS" ]; then
  echo "Splunk は起動した"
  # shellcheck disable=SC2086
  if [ "$SPLUNK_AZ_NUM" -gt 1 ]; then splunk_cluster_check $SP_SERVICES; fi
else
  STORE_WARN="$STORE_WARN Splunk（HEALTHY は $SP_HEALTHY / $SPLUNK_TASKS 個。${SP_HEALTH:-タスクが無い}。ロググループ /ecs/$PREFIX-splunk）"
fi

# ---- 7-4c. Spark を起こす -----------------------------------------------------------------
# Spark の 3 本（iceberg / splunk / http）は、書き先の OpenSearch・VictoriaMetrics・Splunk が上がってから起こす（先に起こすと、つながらずに落ちて
# 起こし直しを繰り返す）。待ちが切れたときも起こす（書き先が後から上がれば、ECS が落ちたタスクを起こし直してつながる）
if [ -n "$STORE_WARN" ]; then
  STORE_WARN="格納先が 20 分たっても上がりきらない:${STORE_WARN}。Spark は起こすが、書き先が上がるまで落ちて起こし直しを繰り返す。aws ecs list-tasks --region $REGION --cluster $AN_CLUSTER --desired-status STOPPED を見る"
  printf '\033[1;33m%s\033[0m\n' "$STORE_WARN"
fi
log "7-4c. Spark の ECS のサービス（iceberg / splunk / http）を起こす（desired-count 1）"
SPARK_SERVICES=$(tf pipeline/analytics output -json spark_service_names \
  | "${PY[@]}" -c 'import json, sys; print(" ".join(v for _, v in sorted(json.load(sys.stdin).items())))') \
  || die "IaC/terraform/oss/pipeline/analytics の output spark_service_names を読めなかった"
for svc in $SPARK_SERVICES; do
  aws ecs update-service --region "$REGION" --cluster "$AN_CLUSTER" --service "$svc" --desired-count 1 --query 'service.serviceName' --output text
done
echo "Spark を起こした（ログ: aws logs tail --region $REGION $(tf pipeline/analytics output -raw spark_log_group_name) --follow）"

# Web は起動時に SSM のパラメータ（Neo4j の URI など）を読むものがあるので、ルートがそろったところで起こし直す（マネージド版の 8-3 と同じ）
log "7-5. Web を起こし直す（graph と analytics の値を読ませる）"
run_on_instance "$INSTANCE_ID" "systemctl restart $PREFIX-web.service; $WEB_ACTIVE"

# ---- 8. workflow -------------------------------------------------------------------
# マネージド版と同じルート（IaC/terraform/oss/workflow は IaC/terraform/aws-managed/workflow へのリンク）。graph の output に neo4j_uri があるので、ワーカーとツールの Lambda は
# GRAPH_BACKEND=neo4j で動く。OpenSearch Serverless と AMP の output は OSS 版の analytics に無いので、それを読むツールは値なしで作られる
log "8. workflow（IaC/terraform/oss/workflow。Temporal のワーカーの ECS と AgentCore Gateway）"
tf_apply workflow -var "worker_image_tag=$IMAGE_TAG" -var "lambda_az_num=$LAMBDA_AZ_NUM"
WF_CLUSTER=$(tf workflow output -raw cluster_name); WF_SERVICE=$(tf workflow output -raw service_name)
# services-stable は 1 回で最大 10 分。イメージの取得や Temporal の起動が遅い回に備えて 2 回まで待ち、それでも安定しなければ警告を出して先へ進む
# （set -e で up.sh ごと止まると、Web の起こし直しと最後の案内まで届かない。005 のレビュー Nit 6。マネージド版の 8-5 も同じ）
WF_WARN=""; WF_TASK_IP=""
if aws ecs wait services-stable --region "$REGION" --cluster "$WF_CLUSTER" --services "$WF_SERVICE" 2>/dev/null \
  || aws ecs wait services-stable --region "$REGION" --cluster "$WF_CLUSTER" --services "$WF_SERVICE"; then
  WF_TASK=$(aws ecs list-tasks --region "$REGION" --cluster "$WF_CLUSTER" --service-name "$WF_SERVICE" --query 'taskArns[0]' --output text)
  WF_TASK_IP=$(aws ecs describe-tasks --region "$REGION" --cluster "$WF_CLUSTER" --tasks "$WF_TASK" \
    --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value | [0]' --output text)
  echo "WF_TASK=${WF_TASK##*/} WF_TASK_IP=$WF_TASK_IP"
else
  WF_WARN="workflow のワーカーのサービス（${WF_SERVICE}）が 20 分たっても安定しない。ワーカーのログ（$(tf workflow output -raw worker_logs_command)）と aws ecs list-tasks --region $REGION --cluster $WF_CLUSTER --desired-status STOPPED を見て、直ったら ops/oss/up.sh を打ち直す"
  printf '\033[1;33m%s\033[0m\n' "$WF_WARN"
fi
log "8-2. Web を起こし直す（workflow の Gateway の値を読ませる）"
run_on_instance "$INSTANCE_ID" "systemctl restart $PREFIX-web.service; $WEB_ACTIVE"

# ---- 9. Runtime のロググループ ------------------------------------------------------------
# AgentCore が作るロググループに保持期間とタグを付ける（まだ無ければ先に作る。消すのは ops/oss/down.sh の delete_runtime_log_groups）
log "9. Runtime のロググループ（${LOG_GROUP}）の保持期間を 7 日にする"
if ! aws logs put-retention-policy --region "$REGION" --log-group-name "$LOG_GROUP" --retention-in-days 7 2>/dev/null; then
  aws logs create-log-group --region "$REGION" --log-group-name "$LOG_GROUP"
  aws logs put-retention-policy --region "$REGION" --log-group-name "$LOG_GROUP" --retention-in-days 7
fi
aws logs tag-resource --region "$REGION" \
  --resource-arn "arn:aws:logs:$REGION:$ACCOUNT_ID:log-group:$LOG_GROUP" \
  --tags "Project=$PREFIX,owner=$OWNER"

# ---- 9-2. Grafana のアラートルール ----------------------------------------------------------
# マネージド版と同じ（ops/up-common.sh の grafana_rules_step）。ルールは評価でエラーになってもアラートを出さず、画面でも Normal に見える（execErrState: KeepLast）。
# OK でなくても止めない（警告を最後にもう一度出す）。あとから確かめ直すのは ops/check-grafana.sh --oss。サービスの名前（tf_output）が読めないか空のときも止めず、
# 空の名前で aws ecs wait に進まずに、確かめていないと警告する（grafana_skip_warn）。クラスターは 7-4b の AN_CLUSTER（読めなければ 7-4b で止まっている）
log "9-2. Grafana のアラートルールが評価でエラーになっていないかを確かめる（Web の EC2 から Grafana のルールの API を読む。最大 5 分）"
if GF_SERVICE=$(tf_output pipeline/analytics grafana_service_name); then
  grafana_rules_step "$INSTANCE_ID" "$AN_CLUSTER" "$GF_SERVICE" "ops/check-grafana.sh --oss"
else
  grafana_skip_warn "ops/check-grafana.sh --oss"
fi

# ---- 配るコマンド ----------------------------------------------------------------------
log "できた（${ROOTS}。OSS 版）。利用者に配るコマンド:"
tf base/core output -raw start_session_command; echo
echo "lab に入るコマンド:"
tf pipeline/lab output -raw start_session_command; echo
echo "gnmic（ECS）に入って機器の状態を 1 回取るコマンド（TASK_ID は下の 1 行目で出る ARN の最後。gn get が Kafka に書くのと同じ event の形で出す。Kafka には書かない）:"
tf pipeline/stream output -raw gnmic_list_tasks_command; echo
tf pipeline/stream output -raw gnmic_exec_command; echo
echo "Kafbat UI（http://localhost:8082/ 。ユーザー admin。Web の EC2 の Docker で動く）を開くポートフォワードと admin のパスワード:"
tf pipeline/stream output -raw kafka_ui_port_forward_command; echo
tf pipeline/stream output -raw kafka_ui_password_command; echo
echo "Nautobot（http://localhost:8081/ 。ユーザー admin）を開くポートフォワード（web の EC2 を踏み台にする）と admin のパスワード:"
tf pipeline/nautobot output -raw port_forward_command; echo
tf pipeline/nautobot output -raw password_command; echo
echo "Splunk（http://localhost:8000/ 。ユーザー admin）を開くポートフォワードと admin のパスワード:"
tf pipeline/analytics output -raw splunk_port_forward_command; echo
tf pipeline/analytics output -raw splunk_password_command; echo
echo "Grafana（http://localhost:3000/ 。ユーザー admin）を開くポートフォワードと admin のパスワード:"
tf pipeline/analytics output -raw grafana_port_forward_command; echo
tf pipeline/analytics output -raw grafana_password_command; echo
echo "OpenSearch の admin のパスワードは SSM の $(tf pipeline/analytics output -raw opensearch_password_parameter)（SecureString）"
echo "Neo4j Browser（http://localhost:7474/ 。ユーザー neo4j。パスワードは SSM の $(tf pipeline/graph output -raw neo4j_password_parameter)）を開くポートフォワード 2 本:"
tf pipeline/graph output -raw neo4j_browser_port_forward_command; echo
tf pipeline/graph output -raw neo4j_bolt_port_forward_command; echo
echo "ワーカーのログ: $(tf workflow output -raw worker_logs_command)"
if [ -n "$WF_TASK_IP" ]; then   # ワーカーが安定しなかったときはタスクの IP が無い（WF_WARN）
  echo "Temporal の UI（Web の EC2 経由でタスクの 8233 へ。PC の http://localhost:8233/ ）:"
  echo "  aws ssm start-session --region $REGION --target $INSTANCE_ID --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{\"host\":[\"$WF_TASK_IP\"],\"portNumber\":[\"8233\"],\"localPortNumber\":[\"8233\"]}'"
fi
if [ -n "$LAB_WARN" ]; then printf '\033[1;33m%s\033[0m\n' "$LAB_WARN"; fi
if [ -n "$GRAPH_WARN" ]; then printf '\033[1;33m%s\033[0m\n' "$GRAPH_WARN"; fi
if [ -n "$NAUTOBOT_WARN" ]; then printf '\033[1;33m%s\033[0m\n' "$NAUTOBOT_WARN"; fi
if [ -n "$STORE_WARN" ]; then printf '\033[1;33m%s\033[0m\n' "$STORE_WARN"; fi
if [ -n "$WF_WARN" ]; then printf '\033[1;33m%s\033[0m\n' "$WF_WARN"; fi
if [ -n "$GRAFANA_WARN" ]; then printf '\033[1;33m%s\033[0m\n' "$GRAFANA_WARN"; fi
printf '\033[1;33m%s\033[0m\n' "時間課金（Kafka 3 台・Telegraf・gnmic・Spark・OpenSearch 3 台・VictoriaMetrics・Splunk・Grafana・Neo4j・Nautobot・Temporal のワーカーの ECS、Nautobot の DB、AgentCore Runtime、lab と Web の EC2（Web に Kafbat UI が同居）、EFS、エンドポイント 14 種）。使い終わったら当日中に ops/oss/down.sh"

# ---- 10. ポートフォワーディング ------------------------------------------------------------
if [ -n "$NO_DASHBOARD_PORTFORWARD" ]; then
  echo "NO_DASHBOARD_PORTFORWARD=1: Web へのポートフォワーディングは開かない（開くなら上の 1 行目のコマンド）"
  exit 0
fi
log "10. ポートフォワーディング（http://localhost:$LOCAL_PORT/ 。Ctrl+C で閉じる）"
# exec すると EXIT の trap が走らないので、ここで同じ on_exit を 1 回呼んで片付ける
trap - EXIT
on_exit
exec aws ssm start-session --region "$REGION" --target "$INSTANCE_ID" \
  --document-name AWS-StartPortForwardingSession \
  --parameters "{\"portNumber\":[\"8080\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}"
