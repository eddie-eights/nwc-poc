#!/usr/bin/env bash
# deploy.env の PIPELINE / AGENT / WORKFLOW で選んだ機能を 1 本で起こす。機能は互いに独立で、要るものだけ作る（費用を抑えるため）。
#   土台（必ず作る）  base/ecr + base/core（VPC / Web の EC2 / バケット / ロール。インターネットへの経路は無い）。約 $0.02/h + エンドポイント。
#   AGENT（既定 0）   agent での分析。terraform/agent（AgentCore Runtime + ガードレール。CREATE_KB=1 なら Knowledge Base も）。
#                     Web の「チャット」タブが使える
#   PIPELINE          データパイプライン。lab（containerlab）→ stream（MSK と Telegraf（ECS））→ analytics（Spark on EMR Serverless → S3 Tables / OpenSearch / Prometheus / Splunk。
#                     Grafana（ECS）で Prometheus と OpenSearch を見る。検知は Grafana のアラートルールと Splunk の保存済みサーチで、SNS のトピック <接頭辞>-alerts へ出す）と
#                     graph（Neptune のトポロジと投入。アラートが届くと機器・回線の status を書き換える）。Web の「トポロジ」タブが動く
#   WORKFLOW          Temporal での実行。workflow（Temporal on ECS Fargate のワーカー + AgentCore Gateway（MCP）+ SNS → SQS）。
#                     Grafana / Splunk のアラートが SNS → SQS で届き、エージェントが Neptune / OpenSearch / Prometheus を見て原因を調べて修復案を出し、
#                     Web の「承認」タブで人が承認すると Temporal が lab で直す。AGENT と PIPELINE（lab / stream / analytics / graph）と、
#                     アラートの送り手（STORES の splunk、または STORES の grafana と SNMP_POLL=1）が要る
# 毎日全部消す運用向け。何度打っても同じ状態に収束する（できているものは Terraform が差分なしで飛ばし、ECR にあるタグはビルドしない）。
# あとから別の機能を 1 にして打ち直せば、その機能だけ足される（土台と他の機能は作り直さない）。
# Terraform の state はこの PC の展開したフォルダの中（terraform/<ルート>/terraform.tfstate）に置く。消すのは ops/down.sh。
#
# 使い方（展開したフォルダの直下で。先に AWS CLI の認証を通しておく。IAM ユーザーなら長期キーのまま打つ）:
#   cp deploy.env.example deploy.env  # 初回だけ。デプロイする人の名前 OWNER（必須）とどの機能を作るかを deploy.env に書く（機能を書かなければ土台だけ）
#   ops/up.sh                         # deploy.env のとおりに作る。最後にポートフォワーディングを開いたまま止まる（Ctrl+C で閉じる）
#   PIPELINE=1 ops/up.sh              # その回だけ変える（環境変数は deploy.env より優先）。初回は PIPELINE で 40〜60 分（MSK の作成が長い）
#   DEPLOY_ENV_FILE=<パス> ops/up.sh  # 別の設定ファイルを読む
#
# どの機能も時間課金（目安は README の「作るもの」）。使い終わったら当日中に ops/down.sh を打つ。
# 機能を 0 にして打っても、前に作ったルートは消さない（消すのは ops/down.sh）。
#
# 設定できるキー（deploy.env か環境変数。**OWNER だけ必須**で、ほかは任意。意味は deploy.env.example、読み方は ops/deploy-env.sh）:
#   OWNER                   **必須。**デプロイする人の名前。英小文字で始まる 14 文字までの英小文字・数字・ハイフン（連続と末尾は不可）。
#                           リソース名の接頭辞と Project タグの値は <owner>-nwc-poc になり、owner タグには OWNER がそのまま入る。
#                           1 つの AWS アカウントを何人かで使うときに、自分の名前で自分のリソースを探せるようにするための値
#                           （AgentCore Runtime の名前はハイフンが使えないので、- を _ にした <接頭辞>_agent になる）。
#                           **作ったあとで変えると、Terraform は名前の違うリソースを作り直す**（先に ops/down.sh で消す）
#   AGENT=1                 agent での分析（既定 0）。terraform/agent を作る。Web の「チャット」タブを使うなら書く
#   PIPELINE=1              データパイプライン（既定 0）。lab / stream / analytics / graph を作る（SKIP_* で減らせる）
#   WORKFLOW=1              Temporal での実行（既定 0）。workflow を作る。AGENT と PIPELINE が要り、SKIP_LAB / SKIP_STREAM / SKIP_ANALYTICS / SKIP_GRAPH は書けない。
#                           アラートの送り手も要る（STORES の splunk、または STORES の grafana と SNMP_POLL=1（Grafana のアラート）。既定のままでは送り手が無いので止まる）
#   CREATE_KB=1             AGENT=1 で Knowledge Base も作る（既定 0。+$0.35/h = OpenSearch Serverless の OCU $0.33 + 土台の VPC エンドポイント $0.01（STORES の grafana の OpenSearch と共用）
#                           + bedrock-agent-runtime のエンドポイント $0.01）。コレクションは公開せず、そのエンドポイントと Bedrock からだけ届く
#   SKIP_LAB=1              PIPELINE=1 で lab を作らない（ほかは lab が無くても作れる。stream を作れば Telegraf の取りにいく側は lab の定義の機器を
#                           探しに行き、届かないのでエラーをログに出して 10 秒ごとに繋ぎ直す（タスクは落ちない）。受ける側は送り手がいなければ何も来ない）
#   SKIP_STREAM=1           PIPELINE=1 で stream と analytics（stream の Kafka を読む）を作らない
#   SKIP_ANALYTICS=1        PIPELINE=1 で analytics（Spark → S3 Tables / OpenSearch / Prometheus / Splunk と、検知する Grafana / Splunk）を作らない
#   STORES=s3,grafana,splunk
#                           analytics の格納先を 3 つのまとまりで選ぶ（カンマで並べる。順番と重複は問わない）。既定は s3,grafana。
#                           書かなかったまとまりは作らない（前に作っていればその格納先はデータごと消える）。格納先を選ぶのはこれだけ
#                           （SINK_S3 / SINK_OPENSEARCH / SINK_PROMETHEUS / SINK_SPLUNK / GRAFANA は 2026-10-04 になくし、書いてあれば止まる）。
#                             s3      = 全トピック → S3 Tables（Iceberg）
#                             grafana = traps と logs（機器の syslog）→ OpenSearch Serverless、metrics と gnmi → Amazon Managed Service for Prometheus と、
#                                       その 2 つを見る Grafana OSS（ECS。+$0.02/h）。Grafana のアラートルール（IF の ifOperStatus → link_down）も入り、
#                                       SNS へ出す（grafana/provisioning/alerting）。ifOperStatus は SNMP のポーリングの値なので、SNMP_POLL=1 でなければ
#                                       ルールは発火しない。web の EC2 を踏み台にした SSM のポートフォワードで開く（コマンドは最後に出る）
#                             splunk  = 全トピック → Splunk の HTTP Event Collector。Splunk Enterprise（公式イメージ・試用ライセンス）を analytics の ECS で
#                                       立てて VPC の中で送る（+$0.12/h。起動時に Splunk のライセンスと Splunk General Terms に同意する。index はタスクと
#                                       一緒に消える）。イメージは公式イメージに検知のアプリ（splunk/netops_alerts。trap と gNMI の BGP / IS-IS を保存済み
#                                       サーチで見て SNS へ出す）を足したもの。管理者のパスワードと HEC の token は手順 7-4 で SSM の SecureString に作る
#                                       （値は出さない。見るコマンドを最後に出す）。SPLUNK_INDEX（既定は空 = token の既定の index）は任意。
#                                       AWS の外の Splunk へ NAT Gateway で送る道は 2026-09-28 にやめた（VPC から AWS の外へ出る経路は作らない）
#                           terraform/pipeline/analytics の var.sinks（iceberg / opensearch / prometheus / splunk）と create_grafana に組んで渡す。
#   SYSLOG_STANDARD         stream の Telegraf が受ける機器の syslog の形式。RFC3164（既定。本番の Cisco IOS の BSD 形式）か RFC5424。
#                           lab の SR Linux は RFC 5424 で送る（ops/lab-common.sh の LAB_SYSLOG_STANDARD）ので、lab のログの項目まで見るなら RFC5424。
#                           デバッグ用の EC2（ops/lab-debug.sh。up.sh とは別に作る）の Telegraf はこの値を使わず、lab/lab.sh の LOG_STANDARD（RFC5424）
#   SNMP_POLL=1             stream の Telegraf で SNMP もポーリングする（10 秒ごとに ifTable → metrics トピック）。既定 0 で、SNMP は trap だけ受ける。
#                           Grafana のアラートルール link_down と IF のグラフ、エージェントの IF のメトリクスはこのポーリングを見るので、0 では空になる
#                           （IF の up / down は STORES の splunk の Splunk が trap から link_down を出す）。stream の変数 snmp_poll に渡す
#   （Nautobot）            PIPELINE=1 なら Nautobot（terraform/pipeline/nautobot。ECS Fargate の web + Celery worker + Redis と、RDS の PostgreSQL。+$0.13/h と ecs のエンドポイント）を**いつも作る**（切り替える変数は無い）。
#                           機器の一覧とケーブルの正を Nautobot にする。最初だけ lab の定義から入り、あとは Nautobot で機器・Service（gnmi / snmp）・ケーブルを変えるたびに、
#                           Job が Telegraf の取りにいく側（dialin）の機器の一覧（SSM）を書き換えてサービスを作り直し、Neptune の物理層を openCypher で合わせる。
#                           Job の書き先が要るので、SKIP_STREAM と SKIP_GRAPH の両方があるときだけ作らない。管理者のパスワード・SECRET_KEY・DB のパスワードは SSM の SecureString に作る（値は出さない）。
#                           web の EC2 を踏み台にした SSM のポートフォワードで開く（コマンドは最後に出る）。ops/down.sh で DB ごと消える（編集した内容は残らない）。
#                           デバッグ用の EC2（ops/lab-debug.sh）は Nautobot を使わず、今までどおり lab の定義の一覧
#   SKIP_GRAPH=1            PIPELINE=1 で graph（Neptune）を作らない。「トポロジ」は使えず、アラートが届いても status を書く先が無い
#   IMAGE_TAG               エージェント（WORKFLOW=1 ではワーカーも）のイメージのタグ。既定 v1。ECR にそのタグが無いときだけ PC の docker buildx でビルドして push する（タグは上書きできない）
#   VPC_CIDR                terraform/base/core の vpc_cidr（社内と重なるとき）
#   MDT_SOURCE_CIDRS        Cisco の MDT（dial-out。tcp 57000）を stream の Telegraf の NLB へ送ってよい機器の CIDR（カンマで。例 10.10.0.0/16,10.20.0.0/16）。
#                           terraform/base/core の mdt_source_cidrs。既定は空で、どこからも受けない（lab の SR Linux は MDT を送れない）
#   HTTP_SEND=executor      analytics の Spark のジョブが HTTP の格納先（opensearch / prometheus / splunk）へ executor から送る（foreachPartition）。既定 driver（driver に集めて送る）
#   MAX_OFFSETS_PER_TRIGGER Spark の 1 つのクエリが Kafka の 1 回のトリガー（60 秒）に読む件数の上限（全パーティションの合計。maxOffsetsPerTrigger）。既定 10000、0 で上限なし
#   MAX_OFFSETS_PER_TRIGGER_ICEBERG / _SPLUNK / _OPENSEARCH / _PROMETHEUS
#                           その格納先のクエリだけ上の値を上書きする（0 でそのクエリだけ上限なし）。既定は空で、上の値を使う
#   LOCAL_PORT              PC 側のポート。既定 8080
#   NO_DASHBOARD_PORTFORWARD=1  最後の Web へのポートフォワーディング（手順 10）を開かずに終わる（2026-10-04 に NO_PORTFORWARD から名前を変えた。前の名前が残っていると止まる）
#   AWS_PROFILE / AWS_CA_BUNDLE  AWS CLI と terraform がそのまま読む
# ---- 冗長化用（既定は 1 AZ。MSK と Runtime だけ既定 2 AZ。本番の形を試すときに書く） ----
#   <リソース>_AZ_NUM で、そのリソースを何 AZ に置くかをリソースごとに選ぶ（まとめて切り替えるキーは無い）。base/core はサブネット a / b / c を
#   いつも作り、各リソースは先頭から AZ_NUM 個を使う。増やした分は下の費用の目安に入る（AZ をまたぐ転送料 $0.01/GB は入らない）。
#   範囲の外の値は何も作る前に止まる
#   ENDPOINTS_AZ_NUM=1      インターフェース型エンドポイントと OpenSearch Serverless の VPC エンドポイント。1〜3。エンドポイントの費用が AZ の数の倍。
#                           ほかのキーを書いて 2 以上にしたのにこれがそれより小さいと注意を出す（エンドポイントの無い AZ が残ると、a の AZ が止まったとき
#                           b / c のものも AWS の API に届かない）。ENDPOINTS_MULTI_AZ は 2026-10-04 にこれへ変わった（書いてあると止まる）
#   MSK_AZ_NUM=2            MSK のブローカー（1 AZ に 1 台。+$0.27/h ずつ）。2〜3。**1 にはできない**（MSK はブローカーを 2 か 3 の AZ にしか置けない）。
#                           2 で複製 2 / min.insync.replicas 1、3 で 3 / 2。変えるとクラスタを作り直す（トピックの中身は消える）
#   RUNTIME_AZ_NUM=2        AgentCore Runtime の ENI。2〜3。**1 にはできない**（AWS の文書が高可用性のため 2 AZ 以上を勧めているので、2 以上に決めた。
#                           2026-10-04 のユーザー決定。API そのものは 1 サブネットでも受け付ける）。費用は変わらない
#   EMR_AZ_NUM=1            Spark（EMR Serverless）のジョブが動けるサブネット。1〜3。ジョブは 1 つのサブネットで動き、その AZ が止まれば次は別の AZ で起こせる。
#                           費用は変わらない。変えるときアプリが動いていれば、ジョブとアプリを止めてから変える（7-5 で起こし直す）
#   LAMBDA_AZ_NUM=1         VPC の Lambda（KB の索引・グラフの状態・Gateway の tools の 3 つ）。1〜3。費用は変わらない
#   NEPTUNE_AZ_NUM=1        Neptune Analytics のグラフ。1〜3（2 以上は別の AZ の待機系のレプリカを 値 - 1 個。1 つ +$0.58/h）
#   OPENSEARCH_AZ_NUM=1     OpenSearch Serverless（KB と logs のコレクション）。1〜2（2 はスタンバイのレプリカ。OCU が倍。3 という形は無い）。
#                           変えるとコレクションを作り直す（索引は消える）
#   NAUTOBOT_DB_AZ_NUM=1    Nautobot の RDS。1〜2（2 は Multi-AZ で、別の AZ に同期の待機系。約 2 倍。3 は Multi-AZ DB クラスタで、作っていない）
#   TELEGRAF_AZ_NUM=1       stream の Telegraf の受ける側（dialout）。1〜3。NLB のサブネットとタスクの数（1 AZ に 1 つ。+$0.01/h ずつ。2 以上は NLB が AZ をまたいで配る）。
#                           取りにいく側（dialin）はいつも 1 つ（2 つにすると同じ機器を 2 重にポーリング・購読する）
#   1 台でしか成り立たないのでキーを作らないもの（どれもサブネット a に 1 つ）:
#     Web の EC2（SSM のポートフォワードは 1 台を名指しでつなぐので、2 台にしても切り替える先が無い）、
#     lab の EC2（containerlab の 1 台の中に全部の機器がある）、
#     Grafana（ECS。アラートルールの評価もタスクの中なので、2 つにするとアラートを 2 重に出す）、
#     Splunk（ECS。index がタスクの中にあるので、2 つにすると index が 2 つに分かれ、保存済みサーチが半分ずつしか見ない）、
#     Nautobot（ECS。Redis と Celery を同じタスクに入れているので、2 つにするとキャッシュとキューが別々になる）、
#     workflow（ECS。Temporal の開発用サーバーがタスクの中にあるので、2 つにすると別々の Temporal になり、承認待ちが片方にしか無い）
# ---- デバッグ用（ふだんは書かない） ----
#   NETWORK_PERIMETER=0     AccessDenied の切り分け。VPC の外からの AWS の API を拒む Deny（terraform/base/core の perimeter.tf）を外す。既定 1
#   TF_VERBOSE=1            terraform の失敗・遅さの切り分け。出力を全部画面に出す（既定は進みと結果だけ。全文は ops/logs/tf-<ルート>-apply.log）
# AGENT / PIPELINE / WORKFLOW / CREATE_KB / SKIP_* / SNMP_POLL / NO_DASHBOARD_PORTFORWARD / NETWORK_PERIMETER / TF_VERBOSE は 1 / 0 のほか true / false、yes / no でも書ける（ops/down.sh の KEEP_ECR は 1 か 0 だけ）。
#
# 利用者への権限は人に渡す作業なので入れていない（docs/deploy.md の「利用者に画面を渡す」）。
set -euo pipefail

REGION=ap-northeast-1
# デプロイする人の名前 OWNER は deploy.env に書くので、OWNER と接頭辞 PREFIX=<owner>-nwc-poc が確定するのは
# load_deploy_env のあと（手順 0 の resolve_name_prefix。必須なので、無ければそこで止まる。形の検査も ops/deploy-env.sh）
# lab と Telegraf の版（SRLINUX_TAG / MULTITOOL_TAG / CONTAINERLAB_VERSION / TELEGRAF_VERSION）と作り方は ops/lab-common.sh
# （デバッグ用の EC2 の ops/lab-debug.sh と共通）。GRAFANA_VERSION / SPLUNK_VERSION は grafana/ と splunk/ の Dockerfile の ARG の
# 既定値に合わせてある。変えるときは両方を変える
. "$(dirname "$0")/lab-common.sh"
GRAFANA_VERSION=13.2.2
SPLUNK_VERSION=10.4.3   # splunk/splunk は amd64 だけ（ECS のタスクは X86_64）
# Nautobot と、同じタスクで動かす Redis。nautobot/Dockerfile の ARG と terraform/pipeline/nautobot の redis_image_tag の既定値に合わせてある
NAUTOBOT_VERSION=3.2.6
REDIS_TAG=7.4.2-alpine
# analytics の Spark ジョブに足す jar（Maven Central。2026-09-17 に 6 本とも取れることを確認）。EMR Serverless 7.13.0 の Spark 3.5.6 に合わせてある。
# terraform/pipeline/analytics の emr_release_label を変えるときは spark-sql-kafka とその依存（kafka-clients / commons-pool2 は spark-sql-kafka の pom の版）も変える
JARS_DIR=jars
MAVEN=https://repo1.maven.org/maven2
SPARK_VERSION=3.5.6
JAR_URLS=(
  "$MAVEN/org/apache/spark/spark-sql-kafka-0-10_2.12/$SPARK_VERSION/spark-sql-kafka-0-10_2.12-$SPARK_VERSION.jar"
  "$MAVEN/org/apache/spark/spark-token-provider-kafka-0-10_2.12/$SPARK_VERSION/spark-token-provider-kafka-0-10_2.12-$SPARK_VERSION.jar"
  "$MAVEN/org/apache/kafka/kafka-clients/3.4.1/kafka-clients-3.4.1.jar"
  "$MAVEN/org/apache/commons/commons-pool2/2.11.1/commons-pool2-2.11.1.jar"
  "$MAVEN/software/amazon/msk/aws-msk-iam-auth/2.3.2/aws-msk-iam-auth-2.3.2-all.jar"
  "$MAVEN/software/amazon/s3tables/s3-tables-catalog-for-iceberg-runtime/0.1.8/s3-tables-catalog-for-iceberg-runtime-0.1.8.jar"
)
SPARK_SCRIPT=spark/snmp_sinks.py

. "$(dirname "$0")/deploy-env.sh"
resolve_deploy_env_file  # DEPLOY_ENV_FILE の相対パスは、下の cd の前の場所から見る
cd "$(dirname "$0")/.."

log()  { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mNG: %s\033[0m\n' "$*" >&2; exit 1; }
# terraform に渡す認証情報。もとは terraform/agent の opensearch provider（古い AWS SDK の Go v1）が `aws login` で入ったプロファイル
# （login_session）を読めずに NoCredentialProviders で落ちたための回避（provider は 2026-09-28 に外した。aws provider が
# login_session を読めるかは確かめていないので残す）。鍵が環境変数に無い（= プロファイルから読む）ときは、AWS CLI から
# 資格情報を受け取る credential_process だけのプロファイルを一時ファイルに書き、terraform にはそちらを読ませる
# （AWS CLI ユーザーガイド「Sharing Login credentials as process credentials」の形。15 分ごとの更新は CLI が続ける）
TF_AWS_CONFIG=""
TF_AWS_ENV=()
tf_use_cli_credentials() {
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ]; then  # 鍵が環境変数にあるときはそのまま渡す
    echo "terraform の認証情報: 環境変数の鍵"
    return 0
  fi
  local profile_opt=""
  if [ -n "${AWS_PROFILE:-}" ]; then profile_opt="--profile $(printf '%q' "$AWS_PROFILE")"; fi
  TF_AWS_CONFIG=$(mktemp "${TMPDIR:-/tmp}/$PREFIX-aws-config.XXXXXX") || die "一時ファイルを作れない（TMPDIR）"
  printf '[profile %s-terraform]\ncredential_process = env -u AWS_PROFILE AWS_CONFIG_FILE=%q aws configure export-credentials %s --format process\n' \
    "$PREFIX" "${AWS_CONFIG_FILE:-$HOME/.aws/config}" "$profile_opt" >"$TF_AWS_CONFIG"
  TF_AWS_ENV=(AWS_CONFIG_FILE="$TF_AWS_CONFIG" AWS_PROFILE="$PREFIX-terraform")
  echo "terraform の認証情報: プロファイル ${AWS_PROFILE:-（既定）} を AWS CLI 経由（credential_process）で渡す"
}
tf() {  # tf <ルート> <terraform のサブコマンドと引数…>
  local root="$1"; shift
  env ${TF_AWS_ENV[@]+"${TF_AWS_ENV[@]}"} terraform -chdir="terraform/$root" "$@"
}
tf_init() {  # tf_init <ルート>
  tf "$1" init -input=false >/dev/null \
    || die "terraform/$1 の init に失敗した（provider の取得。社内 PC は docs/setup.md「社内 PC の CA」）"
}
tf_apply_only() {  # tf_apply_only <ルート> [-var 名前=値 …]  init 済みのルートを apply する
  local root="$1"; shift
  tf_logged "$root" apply -input=false -auto-approve -var "owner=$OWNER" "$@" \
    || die "terraform/$root の apply に失敗した（上のエラー。全文は $(tf_log_file "$root" apply)。docs/troubleshooting.md の「うまくいかないとき」。直したらもう一度 ops/up.sh）"
}
tf_apply() {  # tf_apply <ルート> [-var 名前=値 …]
  tf_init "$1"
  tf_apply_only "$@"
}
has_resources() {  # has_resources <ルート>  state があり、リソースが 1 つ以上載っている（init 済みが前提）
  [ -f "terraform/$1/terraform.tfstate" ] && [ -n "$(tf "$1" state list 2>/dev/null)" ]
}
wait_ssm_online() {  # wait_ssm_online <インスタンス ID>
  local i
  for i in $(seq 1 60); do
    if [ "$(aws ssm describe-instance-information --region "$REGION" \
          --filters "Key=InstanceIds,Values=$1" \
          --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)" = Online ]; then
      return 0
    fi
    sleep 10
  done
  die "$1 が 10 分たっても Session Manager に Online にならない（docs/troubleshooting.md の「画面に入れない」）"
}
ssm_run() {  # ssm_run <インスタンス ID> <コマンド…>  cloud-init（user_data）が終わるのを待ってから打ち、標準出力を出す。失敗なら 1
  # コマンドは JSON の文字列に埋めるので、ダブルクォートとバックスラッシュを含めない
  local id="$1"; shift
  local cmd_id status
  cmd_id=$(aws ssm send-command --region "$REGION" --instance-ids "$id" \
    --document-name AWS-RunShellScript --timeout-seconds 900 \
    --parameters "{\"commands\":[\"cloud-init status --wait >/dev/null || true\",\"$*\"]}" \
    --query Command.CommandId --output text)
  while :; do
    status=$(aws ssm get-command-invocation --region "$REGION" --command-id "$cmd_id" --instance-id "$id" \
      --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in
      Success)
        aws ssm get-command-invocation --region "$REGION" --command-id "$cmd_id" --instance-id "$id" \
          --query StandardOutputContent --output text | sed '/^$/d'
        return 0 ;;
      Pending|InProgress|Delayed) sleep 10 ;;
      *) aws ssm get-command-invocation --region "$REGION" --command-id "$cmd_id" --instance-id "$id" \
           --query '[StandardOutputContent,StandardErrorContent]' --output text >&2
         echo "インスタンス上のコマンドが $status" >&2
         return 1 ;;
    esac
  done
}
run_on_instance() {  # run_on_instance <インスタンス ID> <コマンド…>  ssm_run の失敗で止まる版
  ssm_run "$@" || die "インスタンス $1 の上のコマンドが失敗した（上の出力）"
}
ensure_secret() {  # ensure_secret <SSM のパラメータ名> <password|uuid|token> <説明>  無ければ乱数の SecureString を作る。値は画面にもログにも出さない
  local name="$1" kind="$2" desc="$3" type
  type=$(aws ssm describe-parameters --region "$REGION" --parameter-filters "Key=Name,Values=$name" \
    --query 'Parameters[0].Type' --output text 2>/dev/null || echo "")
  [ "$type" != None ] || type=""   # 無いときの --output text は None
  case "$type" in
    SecureString) echo "$name はある（作り直さない）"; return 0 ;;
    "") ;;
    *) die "$name が SecureString でない（${type}）。消してから打ち直す: aws ssm delete-parameter --region $REGION --name $name" ;;
  esac
  # 値は Python が作って本人だけが読める一時ファイルに書き、AWS CLI に file:// で渡す（コマンドラインにも変数にも載せない）。
  # 標準入力（file:///dev/stdin）は AWS CLI v2 が読めず Invalid JSON になる。ops/down.sh は ManagedBy のタグで見分けて消す
  local input rc=0
  input=$(umask 077; mktemp "${TMPDIR:-/tmp}/nwc-secret.XXXXXX") || die "一時ファイルを作れなかった"
  "${PY[@]}" -c 'import json, secrets, sys, uuid
name, kind, desc, prefix, owner, path = sys.argv[1:]
value = str(uuid.uuid4()) if kind == "uuid" else secrets.token_hex(20) if kind == "token" else secrets.token_urlsafe(24)  # token = Nautobot の API トークン（40 桁の 16 進）
with open(path, "w", encoding="utf-8") as f:
    json.dump({"Name": name, "Type": "SecureString", "Value": value, "Description": desc,
               "Tags": [{"Key": "ManagedBy", "Value": "ops/up.sh"}, {"Key": "Project", "Value": prefix}, {"Key": "owner", "Value": owner}]}, f)' \
    "$name" "$kind" "$desc" "$PREFIX" "$OWNER" "$input" \
    && aws ssm put-parameter --region "$REGION" --cli-input-json "file://$input" >/dev/null || rc=$?
  rm -f -- "${input:?}"
  [ "$rc" -eq 0 ] || die "SSM に $name を作れなかった（上のエラー）"
  echo "$name を作った（値は出さない。見るコマンドは最後に出る）"
}
# アラートの通知の履歴（Firehose → S3 Tables の alert_events、Athena で読む）は、Glue の S3 Tables 連携のカタログ s3tablescatalog を通る。
# アカウントとリージョンに 1 つで、ほかの OWNER の環境と共有するので、無いときだけ作り、ops/down.sh では消さない（消し方は docs/deploy.md）
S3TABLES_CATALOG_INPUT='{"FederatedCatalog": {"Identifier": "arn:aws:s3tables:__REGION__:__ACCOUNT__:bucket/*", "ConnectionName": "aws:s3tables"},
 "CreateDatabaseDefaultPermissions": [{"Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"}, "Permissions": ["ALL"]}],
 "CreateTableDefaultPermissions": [{"Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"}, "Permissions": ["ALL"]}],
 "AllowFullTableExternalDataAccess": "True"}'
ensure_s3tables_catalog() {  # 無ければ作る。あれば設定が想定（IAM だけで読み書きできる）と違うときに警告だけ出す
  local out conn ext perms
  if ! out=$(aws glue get-catalog --region "$REGION" --catalog-id s3tablescatalog --query Catalog.Name --output text 2>&1); then
    case "$out" in *EntityNotFoundException*) ;; *) die "Glue のカタログ s3tablescatalog を確かめられない: $out" ;; esac
    echo "Glue のカタログ s3tablescatalog を作る（S3 Tables 連携。アカウントとリージョンで共有し、ops/down.sh では消さない）"
    aws glue create-catalog --region "$REGION" --name s3tablescatalog \
      --catalog-input "$(printf '%s' "$S3TABLES_CATALOG_INPUT" | sed "s/__REGION__/$REGION/; s/__ACCOUNT__/$ACCOUNT_ID/")" >/dev/null \
      || die "Glue のカタログ s3tablescatalog を作れなかった（上のエラー。docs/deploy.md の「アラートの通知の履歴」）"
    return 0
  fi
  conn=$(aws glue get-catalog --region "$REGION" --catalog-id s3tablescatalog --query Catalog.FederatedCatalog.ConnectionName --output text)
  ext=$(aws glue get-catalog --region "$REGION" --catalog-id s3tablescatalog --query Catalog.AllowFullTableExternalDataAccess --output text)
  perms=$(aws glue get-catalog --region "$REGION" --catalog-id s3tablescatalog --query 'Catalog.CreateTableDefaultPermissions[].Principal.DataLakePrincipalIdentifier' --output text)
  case "$conn|$ext|$perms" in
    "aws:s3tables|True|"*IAM_ALLOWED_PRINCIPALS*) echo "Glue のカタログ s3tablescatalog はある（作り直さない）" ;;
    *) printf '\033[1;33m%s\033[0m\n' "Glue のカタログ s3tablescatalog は既にあるが、設定が想定（aws:s3tables / True / IAM_ALLOWED_PRINCIPALS）と違う（${conn} / ${ext} / ${perms}）。ほかの人が Lake Formation で管理しているかもしれない。そのまま使うので、Firehose と Athena が alert_events に届かないことがある（docs/deploy.md の「アラートの通知の履歴」）" ;;
  esac
}
ensure_fixed_secret() {  # ensure_fixed_secret <SSM のパラメータ名> <値> <説明>  無ければ決まった値の SecureString を作る。あれば触らない（書き換えた値を残す）
  local name="$1" value="$2" desc="$3" type
  type=$(aws ssm describe-parameters --region "$REGION" --parameter-filters "Key=Name,Values=$name" \
    --query 'Parameters[0].Type' --output text 2>/dev/null || echo "")
  [ "$type" != None ] || type=""
  case "$type" in
    SecureString) echo "$name はある（作り直さない）"; return 0 ;;
    "") ;;
    *) die "$name が SecureString でない（${type}）。消してから打ち直す: aws ssm delete-parameter --region $REGION --name $name" ;;
  esac
  # ensure_secret と同じく、本人だけが読める一時ファイルに書いて file:// で渡す（値は環境変数で Python に渡し、コマンドラインに載せない）
  local input rc=0
  input=$(umask 077; mktemp "${TMPDIR:-/tmp}/nwc-secret.XXXXXX") || die "一時ファイルを作れなかった"
  FIXED_SECRET_VALUE="$value" "${PY[@]}" -c 'import json, os, sys
name, desc, prefix, owner, path = sys.argv[1:]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"Name": name, "Type": "SecureString", "Value": os.environ["FIXED_SECRET_VALUE"], "Description": desc,
               "Tags": [{"Key": "ManagedBy", "Value": "ops/up.sh"}, {"Key": "Project", "Value": prefix}, {"Key": "owner", "Value": owner}]}, f)' \
    "$name" "$desc" "$PREFIX" "$OWNER" "$input" \
    && aws ssm put-parameter --region "$REGION" --cli-input-json "file://$input" >/dev/null || rc=$?
  rm -f -- "${input:?}"
  [ "$rc" -eq 0 ] || die "SSM に $name を作れなかった（上のエラー）"
  echo "$name を作った（値は出さない）"
}
nautobot_context() {  # nautobot_context <空のディレクトリ>  Nautobot のイメージのビルドの context を集める（nautobot/Dockerfile の頭の説明）
  # nautobot/ の中身に、Neptune Analytics へ openCypher で書く agent/graph.py と agent/toolkit.py、最初の seed にする lab の定義を足す。
  # タグはこのディレクトリの中身から作る（dir_tag）ので、graph.py や lab の定義を変えてもイメージが作り直される
  cp -R nautobot/. "$1/" && cp agent/graph.py agent/toolkit.py "$1/" || return 1
  find "$1" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
  find "$1" -name .DS_Store -delete 2>/dev/null
  "${PY[@]}" lab/lab_topology.py lab >"$1/lab_seed.json"
}
NAUTOBOT_CTX=""
GRAPH_PID=""
GRAPH_LOG=ops/logs/graph-apply.log
on_exit() {  # 途中で止まっても、バックグラウンドの graph の apply は終わるまで待つ（打ち直したときに state のロックでぶつからないように）
  if [ -n "$GRAPH_PID" ] && kill -0 "$GRAPH_PID" 2>/dev/null; then
    printf '\n%s\n' "terraform/pipeline/graph の apply がまだ動いているので、終わるまで待つ（ログ: ${GRAPH_LOG}）。このターミナルは閉じない" >&2
    wait "$GRAPH_PID" || true
  fi
  if [ -n "$TF_AWS_CONFIG" ]; then rm -f "$TF_AWS_CONFIG"; fi
  if [ -n "$NAUTOBOT_CTX" ]; then rm -rf -- "$NAUTOBOT_CTX"; fi
}
trap on_exit EXIT

# ---- 0. 道具と認証 -------------------------------------------------------------
log "0. 設定と道具と認証を確かめる"
load_deploy_env
resolve_name_prefix  # OWNER（必須。terraform の -var owner にそのまま渡す。下の tf_apply_only）と接頭辞 PREFIX=<owner>-nwc-poc
# terraform の出力を絞るか（ops/deploy-env.sh の tf_logged）。TF_VERBOSE=0 / false / no を空にそろえる（そろえないと、0 を書いても「空でない」で全部出してしまう）
flag_value TF_VERBOSE
log "   デプロイする人の名前: ${OWNER}（リソース名の接頭辞と Project タグは ${PREFIX}）"
IMAGE_TAG="${IMAGE_TAG:-v1}"
LOCAL_PORT="${LOCAL_PORT:-8080}"
# 格納先は STORES だけで選ぶ（2026-10-04 から。SINK_S3 / SINK_OPENSEARCH / SINK_PROMETHEUS / SINK_SPLUNK / GRAFANA はなくした）。
# 前の deploy.env（か環境変数）にこの 5 つが残っていると、黙って STORES の既定に替わって格納先が変わり、データごと消えることもあるので止め、
# その値に当たる STORES の書き方を出す。書いていない変数は前の既定（SINK_SPLUNK だけ 0、ほかは 1）で埋めて読む。
# OpenSearch・Prometheus・Grafana は STORES の grafana でまとめて作る・作らないので、その 3 つがそろわない値は当たる STORES が無い
OLD_STORE_KEYS=""; OLD_STORE_VALUES=""
for v in SINK_S3 SINK_OPENSEARCH SINK_PROMETHEUS SINK_SPLUNK GRAFANA; do
  if [ -n "${!v:-}" ]; then OLD_STORE_KEYS="$OLD_STORE_KEYS $v"; OLD_STORE_VALUES="$OLD_STORE_VALUES $v=${!v}"; fi
done
if [ -n "$OLD_STORE_KEYS" ]; then
  OLD_STORE_KEYS="${OLD_STORE_KEYS# }"; OLD_STORE_KEYS="${OLD_STORE_KEYS// / / }"  # SINK_S3 / GRAFANA の形にする
  OLD_STORE_WHAT="$OLD_STORE_KEYS はなくなった（2026-10-04 から格納先は STORES だけで選ぶ。s3 / grafana / splunk をカンマで並べ、既定は s3,grafana）"
  OLD_STORE_HOW="deploy.env と環境変数から $OLD_STORE_KEYS を消す${STORES:+（STORES=${STORES} も書いてあるので、消せばそちらが効く）}。まだ何も作っていない"
  OLD_STORE_BAD=""
  for v in SINK_S3 SINK_OPENSEARCH SINK_PROMETHEUS SINK_SPLUNK GRAFANA; do
    val="${!v:-}"
    if [ -z "$val" ]; then if [ "$v" = SINK_SPLUNK ]; then val=0; else val=1; fi; fi
    case "$val" in
      1|true|yes) printf -v "OLD_$v" '%s' 1 ;;
      0|false|no) printf -v "OLD_$v" '%s' '' ;;
      *) OLD_STORE_BAD="$OLD_STORE_BAD $v=${val}" ;;
    esac
  done
  [ -z "$OLD_STORE_BAD" ] || die "$OLD_STORE_WHAT。いまの値の${OLD_STORE_BAD} は 1 / 0 でないので、当たる STORES を決められない。$OLD_STORE_HOW"
  OLD_STORES=""
  if [ -n "$OLD_SINK_S3" ]; then OLD_STORES="s3"; fi
  OLD_STORES_NO_GRAFANA="$OLD_STORES${OLD_SINK_SPLUNK:+${OLD_STORES:+,}splunk}"
  OLD_STORES_GRAFANA="$OLD_STORES${OLD_STORES:+,}grafana${OLD_SINK_SPLUNK:+,splunk}"
  if [ -n "$OLD_SINK_OPENSEARCH$OLD_SINK_PROMETHEUS" ] \
    && { [ -z "$OLD_SINK_OPENSEARCH" ] || [ -z "$OLD_SINK_PROMETHEUS" ] || [ -z "$OLD_GRAFANA" ]; }; then
    if [ -z "$OLD_GRAFANA" ]; then OLD_STORE_COMBO="Grafana を作らずに OpenSearch か Prometheus を作る"
    elif [ -z "$OLD_SINK_OPENSEARCH" ]; then OLD_STORE_COMBO="Prometheus だけを作る（OpenSearch は作らない）"
    else OLD_STORE_COMBO="OpenSearch だけを作る（Prometheus は作らない）"; fi
    die "$OLD_STORE_WHAT。いまの値（${OLD_STORE_VALUES# }）は ${OLD_STORE_COMBO}組み合わせで、その組み合わせはもう選べない（OpenSearch・Prometheus・Grafana は STORES の grafana でまとめて作るか作らないか）。近いのは STORES=$OLD_STORES_GRAFANA（3 つとも作る）か、$(if [ -n "$OLD_STORES_NO_GRAFANA" ]; then echo "STORES=$OLD_STORES_NO_GRAFANA（3 つとも作らない）"; else echo "格納先が残らないので analytics ごと作らない SKIP_ANALYTICS=1"; fi)。$OLD_STORE_HOW"
  fi
  if [ -n "$OLD_SINK_OPENSEARCH" ]; then OLD_STORES="$OLD_STORES_GRAFANA"; else OLD_STORES="$OLD_STORES_NO_GRAFANA"; fi
  [ -n "$OLD_STORES" ] || die "$OLD_STORE_WHAT。いまの値（${OLD_STORE_VALUES# }）は格納先が 1 つも無く、STORES ではそう書けない（空なら既定の s3,grafana）。analytics ごと要らないなら SKIP_ANALYTICS=1 を書く。$OLD_STORE_HOW"
  if [ "$OLD_STORES" = s3,grafana ]; then
    die "$OLD_STORE_WHAT。いまの値（${OLD_STORE_VALUES# }）は STORES=s3,grafana と同じ（STORES を書かないときの既定）。$OLD_STORE_HOW"
  fi
  die "$OLD_STORE_WHAT。いまの値（${OLD_STORE_VALUES# }）は STORES=$OLD_STORES と書く。$OLD_STORE_HOW"
fi
# 格納先を 3 つのまとまりで選ぶ（STORES=s3,grafana,splunk の形。カンマで並べ、順番と重複は問わない）。既定は s3,grafana。
#   s3      = 全トピック → S3 Tables（Iceberg）。Athena はサイクル 001「アラートの履歴を残す」が main に入ったらここに足す
#   grafana = traps と logs → OpenSearch Serverless、metrics と gnmi → Amazon Managed Service for Prometheus と、その 2 つを見る Grafana（ECS）
#   splunk  = 全トピック → Splunk の HTTP Event Collector（analytics の ECS に Splunk を立てる。SPLUNK_ON_ECS）
# 書かなかったまとまりは作らない（前に作っていれば、その格納先はデータごと消える）。空は書いていないのと同じで既定になる
# （deploy.env の空の値は書いていないのと同じ（ops/deploy-env.sh）なのに合わせる）。
# 下の SINK_S3 / SINK_OPENSEARCH / SINK_PROMETHEUS / SINK_SPLUNK は STORES から導いた中の値（1 か空）で、deploy.env には書けない。
# terraform/pipeline/analytics の var.sinks（list）に渡すので、["iceberg","opensearch"] の形に組む（SINKS_TF）
STORES_DEFAULT=""
if [ -z "${STORES:-}" ]; then STORES=s3,grafana; STORES_DEFAULT=1; fi
STORE_S3=""; STORE_GRAFANA=""; STORE_SPLUNK=""
rest="$STORES,"
while [ -n "$rest" ]; do
  item="${rest%%,*}"; rest="${rest#*,}"
  item="${item#"${item%%[![:space:]]*}"}"; item="${item%"${item##*[![:space:]]}"}"
  case "$item" in
    s3) STORE_S3=1 ;;
    grafana) STORE_GRAFANA=1 ;;
    splunk) STORE_SPLUNK=1 ;;
    '') die "STORES に空の要素がある（いまは「${STORES}」）。使えるのは s3 / grafana / splunk で、カンマで並べる。まだ何も作っていない" ;;
    *) die "STORES の「${item}」は無いまとまり（いまは「${STORES}」）。使えるのは s3 / grafana / splunk で、カンマで並べる。まだ何も作っていない" ;;
  esac
done
SINK_S3="$STORE_S3"; SINK_OPENSEARCH="$STORE_GRAFANA"; SINK_PROMETHEUS="$STORE_GRAFANA"; SINK_SPLUNK="$STORE_SPLUNK"
SPLUNK_INDEX="${SPLUNK_INDEX:-}"
SINKS=""
if [ -n "$SINK_S3" ]; then SINKS="iceberg"; fi
if [ -n "$SINK_OPENSEARCH" ]; then SINKS="$SINKS${SINKS:+,}opensearch"; fi
if [ -n "$SINK_PROMETHEUS" ]; then SINKS="$SINKS${SINKS:+,}prometheus"; fi
if [ -n "$SINK_SPLUNK" ]; then SINKS="$SINKS${SINKS:+,}splunk"; fi
# AWS の外の Splunk（NAT Gateway から出る）は 2026-09-28 にやめた。前の deploy.env で黙って ECS の Splunk に替わらないよう止める
[ -z "${SPLUNK_HEC_URL:-}" ] || die "SPLUNK_HEC_URL は 2026-09-28 から使わない（AWS の外の Splunk には送らず、NAT Gateway も作らない）。deploy.env から消す。STORES に splunk を入れれば Splunk を analytics の ECS で立てる。まだ何も作っていない"
SPLUNK_ON_ECS="$SINK_SPLUNK"
SINKS_TF="\"$(printf '%s' "$SINKS" | sed 's/,/","/g')\""
# Spark のジョブが HTTP の格納先（opensearch / prometheus / splunk）へ送る所。既定 driver（マイクロバッチを driver に集めて送る）、
# executor ならパーティションごとに executor が送る（foreachPartition）。terraform/pipeline/analytics の var.http_send に渡す
HTTP_SEND="${HTTP_SEND:-driver}"
case "$HTTP_SEND" in driver | executor) ;; *) die "HTTP_SEND は driver か executor（小文字）: $HTTP_SEND。まだ何も作っていない" ;; esac
# Spark の 1 つのクエリが Kafka の 1 回のトリガー（60 秒）に読む件数の上限（全パーティションの合計。maxOffsetsPerTrigger）。既定 10000、0 で上限なし。
# MAX_OFFSETS_PER_TRIGGER_<格納先>（格納先は --sinks の呼び名: ICEBERG（STORES の s3）/ SPLUNK / OPENSEARCH / PROMETHEUS）が空でなければ、
# その格納先のクエリだけそちらを使う。terraform/pipeline/analytics の var.max_offsets_per_trigger と var.max_offsets_per_trigger_by_sink に渡す
MAX_OFFSETS_PER_TRIGGER="${MAX_OFFSETS_PER_TRIGGER:-10000}"
MAX_OFFSETS_BY_SINK=""   # splunk=2000,prometheus=5000 の形（HCL の map の中身）
for v in MAX_OFFSETS_PER_TRIGGER MAX_OFFSETS_PER_TRIGGER_ICEBERG MAX_OFFSETS_PER_TRIGGER_SPLUNK MAX_OFFSETS_PER_TRIGGER_OPENSEARCH MAX_OFFSETS_PER_TRIGGER_PROMETHEUS; do
  val="${!v:-}"
  [ -n "$val" ] || continue
  case "$val" in *[!0-9]* | 0?* | ??????????*) die "$v は 0 以上の整数（0 で上限なし。9 桁まで、先頭に 0 を付けない）: $val。まだ何も作っていない" ;; esac
  [ "$v" = MAX_OFFSETS_PER_TRIGGER ] || MAX_OFFSETS_BY_SINK="$MAX_OFFSETS_BY_SINK${MAX_OFFSETS_BY_SINK:+,}$(printf '%s' "${v#MAX_OFFSETS_PER_TRIGGER_}" | tr 'A-Z' 'a-z')=$val"
done
# NO_PORTFORWARD は 2026-10-04 に NO_DASHBOARD_PORTFORWARD へ名前を変えた。前の deploy.env か環境変数に残っていると、
# 黙って無視すれば開かないつもりのポートフォワーディングを開いて止まらないので、書き換えてもらう
[ -z "${NO_PORTFORWARD:-}" ] || die "NO_PORTFORWARD は NO_DASHBOARD_PORTFORWARD に変わった（2026-10-04。意味は同じで、1 なら最後の Web へのポートフォワーディングを開かずに終わる）。deploy.env と環境変数の NO_PORTFORWARD=${NO_PORTFORWARD} を NO_DASHBOARD_PORTFORWARD=${NO_PORTFORWARD} に書き換える。まだ何も作っていない"
flag_value SKIP_LAB; flag_value SKIP_STREAM; flag_value SKIP_ANALYTICS; flag_value SKIP_GRAPH; flag_value NO_DASHBOARD_PORTFORWARD
# stream の Telegraf の syslog の形式。既定は本番の Cisco に合わせた RFC3164（stream の変数の既定と同じ）
SYSLOG_STANDARD="${SYSLOG_STANDARD:-RFC3164}"
case "$SYSLOG_STANDARD" in RFC3164 | RFC5424) ;; *) die "SYSLOG_STANDARD は RFC3164 か RFC5424（大文字）: $SYSLOG_STANDARD。まだ何も作っていない" ;; esac
# stream の Telegraf の SNMP のポーリング。既定 0（trap だけ受ける。stream の変数 snmp_poll の既定と同じ）
flag_value SNMP_POLL
# どの機能を作るか（既定は土台だけ。AGENT / PIPELINE / WORKFLOW は 1 を書いたものだけ作る。AGENT の既定は 2026-10-04 に 1 → 0）
AGENT="${AGENT:-0}"
flag_value AGENT; flag_value PIPELINE; flag_value WORKFLOW; flag_value CREATE_KB
if [ -n "$WORKFLOW" ]; then
  if [ -z "$AGENT" ]; then
    die "WORKFLOW は AGENT が要る（ワーカーがエージェントの Runtime を呼ぶ。terraform/workflow は terraform/agent の state から ARN を読む）。deploy.env に AGENT=1 を書く（AGENT の既定は 0）。まだ何も作っていない"
  fi
  if [ -z "$PIPELINE" ]; then
    die "WORKFLOW は PIPELINE が要る（analytics の Grafana / Splunk がアラートを SNS に出し、ワーカーが Neptune のトポロジと修復案を読み書きし、lab の EC2 で直す）。PIPELINE=1 にする。まだ何も作っていない"
  fi
  if [ -n "$SKIP_LAB" ] || [ -n "$SKIP_STREAM" ] || [ -n "$SKIP_ANALYTICS" ] || [ -n "$SKIP_GRAPH" ]; then
    die "WORKFLOW は lab と stream と analytics と graph が要る。SKIP_LAB / SKIP_STREAM / SKIP_ANALYTICS / SKIP_GRAPH を外す。まだ何も作っていない"
  fi
fi
# lab は単独で外せる（2026-10-04 まで、stream を作るなら lab も要るとして止めていた）。stream の Telegraf の取りにいく側の機器の一覧は
# lab が無くても lab の定義（と、それを最初の seed にする Nautobot）から作る
if [ -n "$PIPELINE" ]; then
  if [ -n "$SKIP_LAB" ] && [ -z "$SKIP_STREAM" ]; then
    echo "SKIP_LAB=1 なので lab は作らない。stream の Telegraf の取りにいく側は lab の定義の機器を探しに行き、届かないので gNMI / SNMP のエラーをログに出して繋ぎ直し続ける（タスクは落ちない）。受ける側（trap / syslog / MDT）は送り手がいなければ何も来ない"
    # trap と syslog を NLB へ送れるのは lab の管理ネットワーク（lab の EC2 の DNAT）だけ、MDT は MDT_SOURCE_CIDRS だけ（terraform/base/core の security_groups.tf）
    if [ -z "${MDT_SOURCE_CIDRS:-}" ]; then
      printf '\033[1;33m%s\033[0m\n' "注意: lab が無く MDT_SOURCE_CIDRS も空なので、この stream には何も届かない（trap と syslog は lab の EC2 からしか来ず、MDT を送ってよい機器も無い）。届けるなら SKIP_LAB を外すか、MDT_SOURCE_CIDRS に機器の CIDR を書く"
    fi
  fi
  if [ -n "$SKIP_STREAM" ] && [ -z "$SKIP_ANALYTICS" ]; then
    echo "SKIP_STREAM=1 なので analytics も作らない（読む Kafka が無い）"
    SKIP_ANALYTICS=1
  fi
  if [ -n "$SKIP_LAB" ] && [ -n "$SKIP_STREAM" ] && [ -n "$SKIP_GRAPH" ]; then
    echo "SKIP_LAB と SKIP_STREAM と SKIP_GRAPH があるので、PIPELINE=1 でも土台だけになる"
  fi
else
  SKIP_LAB=1; SKIP_STREAM=1; SKIP_ANALYTICS=1; SKIP_GRAPH=1
fi
# Nautobot（terraform/pipeline/nautobot）は機器の一覧とケーブルの正なので、PIPELINE=1 ならいつも作る（切り替える変数は無い。2026-10-04）。
# Job の書き先（Telegraf の dialin の一覧 = stream、Neptune の物理層 = graph）が両方無いときだけ作らない。
# 前の deploy.env で止まらないよう NAUTOBOT は読むだけ読み、0 が書いてあれば注意を出す
case "${NAUTOBOT:-}" in
  '') ;;
  0|false|no) echo "注意: NAUTOBOT=0 は効かない。Nautobot は PIPELINE=1 ならいつも作る（deploy.env から消してよい）" ;;
  *) echo "注意: NAUTOBOT は使わない。Nautobot は PIPELINE=1 ならいつも作る（deploy.env から消してよい）" ;;
esac
NAUTOBOT=""
if [ -n "$PIPELINE" ] && { [ -z "$SKIP_STREAM" ] || [ -z "$SKIP_GRAPH" ]; }; then NAUTOBOT=1; fi
# Grafana（analytics の ECS）は STORES に grafana があればいつも作る（切り替える変数は無い。GRAFANA は 2026-10-04 になくした）。
# ここから下の GRAFANA は導いた値（作るなら 1）で、Grafana のアラート・エンドポイント・費用・terraform の create_grafana に使う
GRAFANA=""
if [ -z "$SKIP_ANALYTICS" ] && { [ -n "$SINK_PROMETHEUS" ] || [ -n "$SINK_OPENSEARCH" ]; }; then GRAFANA=1; fi
if [ -n "$SKIP_ANALYTICS" ]; then SPLUNK_ON_ECS=""; fi
log "   格納先（STORES=${STORES}${STORES_DEFAULT:+。既定}）:${STORE_S3:+ s3（S3 Tables）}${STORE_GRAFANA:+ grafana（OpenSearch・Prometheus・Grafana）}${STORE_SPLUNK:+ splunk（Splunk）}${SKIP_ANALYTICS:+。analytics を作らないので、どれも作らない}"
# アラート（SNS のトピック <接頭辞>-alerts）の送り手。Grafana のアラートルールは Prometheus のメトリクスを見るので SINK_PROMETHEUS（STORES の grafana）が要る
# （grafana/start.sh は PROMETHEUS_URL があるときだけルールを入れる）。ルール link_down が見る ifOperStatus は SNMP のポーリングの値なので、
# SNMP_POLL=1 でなければ発火しない（送り手に数えず、sns のエンドポイントも足さない）。Splunk は保存済みサーチが trap と gNMI を見る
GRAFANA_ALERTS=""
if [ -n "$GRAFANA" ] && [ -n "$SINK_PROMETHEUS" ] && [ -n "$SNMP_POLL" ]; then GRAFANA_ALERTS=1; fi
if [ -n "$WORKFLOW" ] && [ -z "$GRAFANA_ALERTS$SPLUNK_ON_ECS" ]; then
  die "WORKFLOW はアラートの送り手が要る（ワークフローを起こすのは Grafana か Splunk のアラート）。STORES に splunk を入れる（trap と gNMI から検知する）か、STORES に grafana を入れたまま SNMP_POLL=1 にする（Grafana のアラートルールが SNMP のポーリングから検知する）。まだ何も作っていない"
fi
if [ -n "$GRAFANA" ] && [ -n "$SINK_PROMETHEUS" ] && [ -z "$SNMP_POLL" ] && [ -z "$SPLUNK_ON_ECS" ]; then
  echo "注意: SNMP_POLL=0（既定）なので Grafana のアラートルール link_down は発火せず、IF の up / down を知らせるものが無い。trap から知らせるなら STORES に splunk を入れ、ポーリングで知らせるなら SNMP_POLL=1"
fi
if [ -z "$AGENT" ] && [ -n "$CREATE_KB" ]; then
  echo "AGENT=0 なので CREATE_KB は効かない（Knowledge Base は agent の一部。作るなら AGENT=1 も書く。AGENT の既定は 0）"
  CREATE_KB=""
fi
# 閉域（terraform/base/core の endpoints.tf と perimeter.tf）。AWS の API は全部インターフェース型エンドポイントを通し、通らない呼び出しを拒む
NETWORK_PERIMETER="${NETWORK_PERIMETER:-1}"
flag_value NETWORK_PERIMETER
# 冗長化用（2026-10-04 のユーザー決定）。<リソース>_AZ_NUM でそのリソースを何 AZ に置くか選ぶ（base/core のサブネット a / b / c の先頭から）。
# まとめて切り替えるキーは作らない。既定は 1 で、MSK と Runtime だけ 2。値は何も作る前にここで確かめる
case "${ENDPOINTS_MULTI_AZ:-}" in
  '') ;;
  0|false|no) die "ENDPOINTS_MULTI_AZ は ENDPOINTS_AZ_NUM に変わった（2026-10-04。AZ の数で書く）。ENDPOINTS_MULTI_AZ=${ENDPOINTS_MULTI_AZ} は既定（ENDPOINTS_AZ_NUM=1）と同じなので、deploy.env と環境変数から消す。まだ何も作っていない" ;;
  *) die "ENDPOINTS_MULTI_AZ は ENDPOINTS_AZ_NUM に変わった（2026-10-04。AZ の数で書く）。deploy.env と環境変数の ENDPOINTS_MULTI_AZ=${ENDPOINTS_MULTI_AZ} を ENDPOINTS_AZ_NUM=2 と書き換える。まだ何も作っていない" ;;
esac
AZ_NUM_SET=""   # deploy.env か環境変数に書いてあった *_AZ_NUM（ENDPOINTS_AZ_NUM と比べる）
az_num() {  # az_num <キー> <既定> <最小> <最大> <範囲の理由>  書いてなければ既定。範囲の外なら止める
  local k=$1 v="${!1:-}"
  if [ -n "$v" ]; then AZ_NUM_SET="$AZ_NUM_SET $k"; else v=$2; fi
  case "$v" in *[!0-9]* | '') die "$k は $3〜$4 の数で書く（いまは $k=$v）。まだ何も作っていない" ;; esac
  v=$((10#$v))
  if [ "$v" -lt "$3" ] || [ "$v" -gt "$4" ]; then die "$k=$v は書けない。$3〜$4 で書く（$5）。まだ何も作っていない"; fi
  printf -v "$k" '%s' "$v"
}
az_num ENDPOINTS_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"
az_num MSK_AZ_NUM 2 2 3 "MSK はブローカーを 2 か 3 の AZ にしか置けない。1 AZ にはできない"
az_num RUNTIME_AZ_NUM 2 2 3 "AgentCore Runtime は AWS の文書の勧めに合わせて 2 AZ 以上に置く（2026-10-04 のユーザー決定）"
az_num EMR_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"
az_num LAMBDA_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"
az_num NEPTUNE_AZ_NUM 1 1 3 "Neptune Analytics のレプリカは 2 つまで"
az_num OPENSEARCH_AZ_NUM 1 1 2 "OpenSearch Serverless はスタンバイのレプリカの有無だけを選べる。2 = あり"
az_num NAUTOBOT_DB_AZ_NUM 1 1 2 "RDS の Multi-AZ（待機系 1 台）が 2。3 は Multi-AZ DB クラスタで、作っていない"
az_num TELEGRAF_AZ_NUM 1 1 3 "サブネットは a / b / c の 3 つ"
AZ_NUM_OVER=""
for k in $AZ_NUM_SET; do
  if [ "$k" != ENDPOINTS_AZ_NUM ] && [ "${!k}" -gt "$ENDPOINTS_AZ_NUM" ]; then AZ_NUM_OVER="$AZ_NUM_OVER $k=${!k}"; fi
done
if [ -n "$AZ_NUM_OVER" ]; then
  echo "注意:${AZ_NUM_OVER} に対して ENDPOINTS_AZ_NUM=$ENDPOINTS_AZ_NUM。エンドポイントはサブネット a から $ENDPOINTS_AZ_NUM つにしか無いので、その AZ が止まると、ほかの AZ に置いたものも AWS の API に届かない（止めずに進む。そろえるなら ENDPOINTS_AZ_NUM も同じ数にする）"
fi
if [ -z "$NETWORK_PERIMETER" ]; then echo "NETWORK_PERIMETER=0: VPC の外からの呼び出しを拒む Deny を外す（エンドポイントは作る。切り分けが済んだら 1 に戻して打ち直す）"; fi
if [ -z "$AGENT$PIPELINE$WORKFLOW" ]; then
  echo "機能が全部 0（既定）なので土台（base/ecr + base/core）だけ作る（Web は開けるが「チャット」は「配備されていない」と返す。チャットを使うなら AGENT=1 を書く）"
fi
command -v aws >/dev/null || die "aws CLI が無い（docs/setup.md「Terraform を打つ PC 側」）"
command -v terraform >/dev/null || die "terraform が無い（docs/setup.md「Terraform を打つ PC 側」。1.11 以上）"
if command -v python3 >/dev/null; then PY=(python3)
elif command -v uv >/dev/null; then PY=(uv run --python 3.13 python)
else die "python3 も uv も無い（docs/setup.md「Terraform を打つ PC 側」）"; fi
if [ -z "$SKIP_LAB" ] || [ -z "$SKIP_ANALYTICS" ]; then command -v curl >/dev/null || die "curl が無い（lab の containerlab の rpm と analytics の jar を取るのに使う。sudo apt install curl）"; fi
# docker はイメージ（agent / lab の 2 つ / telegraf / grafana / splunk / nautobot / redis / worker / temporal）を ECR に置くときだけ要る。土台だけなら要らない
NEED_DOCKER="$AGENT$WORKFLOW$GRAFANA$SPLUNK_ON_ECS$NAUTOBOT"; if [ -z "$SKIP_LAB" ] || [ -z "$SKIP_STREAM" ]; then NEED_DOCKER=1; fi
if [ -n "$NEED_DOCKER" ]; then
  command -v docker >/dev/null || die "docker が無い（イメージのビルドに使う。docs/setup.md「Terraform を打つ PC 側」）"
  docker buildx version >/dev/null 2>&1 || die "docker buildx が無い（Ubuntu の docker.io には入っていない。docs/setup.md「Terraform を打つ PC 側」）"
fi
# 最後のポートフォワーディング（手順 10）で要る。40〜60 分かけた後で落ちないよう、ここで見る
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
# 2026-09-28 に KB のコレクションを閉じ、索引は VPC の中の Lambda が作るようにした。PC から OpenSearch につながないので、
# apply する人をデータアクセスポリシーに入れる ADMIN_ARN も、PC の社内 CA を渡す OPENSEARCH_CACERT_FILE も要らない
# SPLUNK_SKIP_TLS_VERIFY も同日から使わない（ECS の Splunk は自己署名なので常に検証しない）
for k in ADMIN_ARN OPENSEARCH_CACERT_FILE SPLUNK_SKIP_TLS_VERIFY; do
  if [ -n "${!k:-}" ]; then echo "注意: $k は 2026-09-28 から使わない（deploy.env から消してよい）"; fi
done
# デバッグ用の EC2 は 2026-10-04 から ops/up.sh / ops/down.sh で作らない（ops/lab-debug.sh up / down だけで扱う CloudFormation のスタック）
case "${LAB_DEBUG:-}" in ''|0|false|no) ;; *) echo "注意: LAB_DEBUG は使わない。デバッグ用の EC2 は ops/lab-debug.sh up / down で作る・消す（deploy.env から消してよい）" ;; esac
tf_use_cli_credentials
ROOTS="base/ecr base/core"
if [ -n "$AGENT" ]; then ROOTS="$ROOTS agent"; fi
if [ -z "$SKIP_LAB" ]; then ROOTS="$ROOTS pipeline/lab"; fi
if [ -z "$SKIP_STREAM" ]; then ROOTS="$ROOTS pipeline/stream"; fi
if [ -z "$SKIP_ANALYTICS" ]; then ROOTS="$ROOTS pipeline/analytics"; fi
if [ -z "$SKIP_GRAPH" ]; then ROOTS="$ROOTS pipeline/graph"; fi
if [ -n "$NAUTOBOT" ]; then ROOTS="$ROOTS pipeline/nautobot"; fi
if [ -n "$WORKFLOW" ]; then ROOTS="$ROOTS workflow"; fi
echo "ACCOUNT_ID=$ACCOUNT_ID"
echo "CALLER_ARN=$CALLER_ARN"
echo "IMAGE_TAG=$IMAGE_TAG"
echo "AGENT=${AGENT:-0} PIPELINE=${PIPELINE:-0} WORKFLOW=${WORKFLOW:-0} CREATE_KB=${CREATE_KB:-0}"
echo "作るルート: $ROOTS"
# インターフェース型エンドポイント（terraform/base/core の var.interface_endpoints）。ルートが呼ぶ AWS の API ごとに 1 本。
# 手順 3 で、今回作らなくても state にリソースが残っているルートの分を足す（外すとそのルートの呼び出しがどこにも出られず接続のタイムアウトになる）
ENDPOINTS=""
ANALYTICS_LEFT=""   # 手順 3 で、今回作らない analytics が state に残っていれば 1
analytics_on() { [ -z "$SKIP_ANALYTICS" ] || [ -n "$ANALYTICS_LEFT" ]; }   # アラートの通知の履歴（Firehose と Athena）があるか
add_endpoints() {  # add_endpoints <サービス名…>  重複は足さない
  local s
  for s in "$@"; do
    case " $ENDPOINTS " in *" $s "*) ;; *) ENDPOINTS="${ENDPOINTS:+$ENDPOINTS }$s" ;; esac
  done
}
endpoints_for() {  # endpoints_for <ルート>  そのルートが呼ぶ AWS の API
  case "$1" in
    # Runtime（VPC モード）はイメージを ECR から引き、ログを CloudWatch に書く。モデルとガードレールは bedrock-runtime
    agent) add_endpoints bedrock-runtime bedrock-agentcore ecr.api ecr.dkr logs ;;
    # lab の EC2 はイメージを ECR から引く（SSM は土台の分）
    pipeline/lab) add_endpoints ecr.api ecr.dkr ;;
    # Telegraf（ECS）: イメージを ECR から引き、ログを CloudWatch に書く。MSK は VPC の中
    pipeline/stream) add_endpoints ecr.api ecr.dkr logs ;;
    # Spark: S3 Tables の API、ドライバのログ（MSK は VPC の中で、S3 は gateway）
    pipeline/analytics) add_endpoints s3tables logs ;;
    # Neptune Analytics のデータ API（openCypher のクエリ）。グラフは公開しないので、Web / Runtime / Lambda / ワーカー / Nautobot はここからしか届かない。
    # status の Lambda はアラートの通知の履歴を Firehose に送る（analytics があるときだけ）
    pipeline/graph) add_endpoints neptune-graph-data
                    if analytics_on; then add_endpoints kinesis-firehose; fi ;;
    # Nautobot（ECS）: イメージとログ。Job が Telegraf の dialin のサービスを作り直すのに ecs の API を呼ぶ（SSM は土台の分。RDS は VPC の中で、Neptune は graph の分）
    pipeline/nautobot) add_endpoints ecr.api ecr.dkr logs ecs ;;
    # ワーカー: SQS（アラートは SNS → SQS で届く。SNS からの配信はエンドポイントを通らない）、S3 Tables（修復案の証跡）、ECR、ログ、Runtime、Gateway。
    # ツールの Lambda の query_history が Athena を呼ぶ（analytics があるときだけ）
    workflow) add_endpoints sqs s3tables ecr.api ecr.dkr logs bedrock-agentcore bedrock-agentcore.gateway
              if analytics_on; then add_endpoints athena; fi ;;
  esac
}
add_endpoints ssm ssmmessages   # 土台: Web の EC2 の SSM Agent とポートフォワーディング、SSM パラメータ
for r in $ROOTS; do endpoints_for "$r"; done
if [ -n "$AGENT" ] && [ -n "$CREATE_KB" ]; then add_endpoints bedrock-agent-runtime; fi   # KB の Retrieve
if [ -z "$SKIP_ANALYTICS" ] && [ -n "$SINK_PROMETHEUS" ]; then add_endpoints aps-workspaces; fi   # remote write とツールと Grafana の query
if [ -n "$GRAFANA$SPLUNK_ON_ECS" ]; then add_endpoints ecr.api ecr.dkr; fi   # analytics の ECS（Grafana / Splunk）のイメージ。secrets は ssm
if [ -n "$GRAFANA_ALERTS$SPLUNK_ON_ECS" ]; then add_endpoints sns; fi   # Grafana / Splunk のタスクがアラートを SNS のトピックへ publish する
endpoint_count() { set -- $ENDPOINTS; echo $#; }
echo "インターフェース型エンドポイント（$(endpoint_count) 本 × ${ENDPOINTS_AZ_NUM} AZ）: $ENDPOINTS"
# 待機時の 1 時間あたりの目安（セント。東京リージョンの税抜。単価は 2026-09-14〜15 に Price List API で確認。README の「作るもの」と docs/deploy.md の金額はここから出している）。
# 土台 = 2（Web の EC2 の t4g.small 2.2。NAT Gateway は 2026-09-28 から作らない）、
# インターフェース型エンドポイント = 1 本 1.4 × ENDPOINTS_AZ_NUM（ENDPOINTS。土台の ssm / ssmmessages 2 本と、ルートごとの分。同じサービスはルートをまたいで 1 本。
#   2026-09-26〜28 は NAT Gateway だけで AWS の API へも出ていたが、閉域（aws:SourceVpc で拒む）にするため戻した。データ処理 $0.01/GB は別）、
# agent = 0（Runtime は使った分だけ）
#   + CREATE_KB なら 33 × OPENSEARCH_AZ_NUM（OpenSearch Serverless の OCU。2 はスタンバイのレプリカで OCU が倍）、
# OpenSearch Serverless の VPC エンドポイント = 1.4 × ENDPOINTS_AZ_NUM（2026-10-04 までは 2 AZ 固定で 3。公表単価からで Price List API では確かめていない。
#   KB と logs のコレクションを公開しないために作り、両方で 1 本を共用する。NEED_AOSS のときだけ）、
# lab = 17（EC2 の t4g.xlarge 17.28。2026-10-04 に公開の料金ファイルで確認。それまでの 9 は t4g.large の単価だった）、graph = 58（Neptune Analytics の 16 m-NCU で 58.1。2026-10-04 に料金のページで確認。Price List API では確かめていない。
#   2026-10-04 までの Neptune Database の db.t4g.medium は 14 だった。レプリカも同じ単価なので × NEPTUNE_AZ_NUM）、
#   stream = MSK 57（ブローカー 2 台。MSK_AZ_NUM=3 で 1 台 27 を足す）+ Telegraf 5（Fargate ARM 0.25 vCPU / 0.5 GB で 1.2 のタスクが、受ける側 1 つと
#   取りにいく側 TELEGRAF_AZ_NUM 個（2026-10-04 に分けた）と内部 NLB 2.43。
#   NLB は 2026-09-28 から。どちらも公表単価からで、Price List API では確かめていない）、
# analytics = Spark のジョブ 1 つにつき 21（ストリーミングのジョブが動いている間の EMR Serverless の 3 vCPU（driver 1 + executor 2。1 vCPU のワーカー 1 台で約 7）。単価は 2026-09-17 に確認。
#   executor は 2026-10-04 に 1 → 2（Kafka のパーティション 2 つを並列に読む）。ジョブは 2026-10-04 に格納先で 3 つに分けた（7-5）:
#   STORES の s3 で sinks-s3iceberg、splunk で sinks-splunk、grafana で sinks-grafana（名前は 7-5 の job_name）。3 つとも動けば 63。
#   S3 Tables のテーブルは無料）
#   + STORES の grafana なら、Prometheus は 0（取り込みのサンプル課金は別）
#     と OpenSearch の 33 × OPENSEARCH_AZ_NUM（logs コレクションの OCU。KB のコレクションと共有されるか確認できていないので最大値で数える。
#     共有されれば 0 に近づく）
#     と Grafana の 2（Fargate ARM 0.5 vCPU / 1 GB で 2.5）
#   + STORES の splunk なら 12（ECS の Splunk。Fargate x86 2 vCPU / 4 GB で 12.3。エフェメラルストレージの 20 GB 超えの分は 0.3 未満。
#     Grafana と Splunk の単価も公表単価からで、Price List API では確かめていない）、
# nautobot = 13（Fargate ARM 2 vCPU / 4 GB のタスク 1 つ 9.9 + RDS の db.t4g.micro 2.5 と gp3 20 GB 0.4。公表単価からで、Price List API では確かめていない。
#   NAUTOBOT_DB_AZ_NUM=2 は Multi-AZ で RDS の 2.9 が倍になり 16）、
# workflow = 5（Fargate ARM 1 vCPU / 2 GB のタスク 1 つ。Gateway と Lambda と SQS と S3 Tables への追記は使った分だけ。単価は 2026-09-17 に確認）。
# EMR Serverless・Lambda・Runtime は使った分だけなので、AZ の数では変わらない。AZ をまたぐ転送料（$0.01/GB 前後。MSK のブローカー間の複製は無料で、
#   別の AZ のブローカーへ書く・読む分と NLB のクロスゾーンの分にかかる）はどれも目安に入れていない。
# ここを変えたら README の「作るもの」と docs/deploy.md の金額も変える
COST_CENTS=2
COST_CENTS=$((COST_CENTS + ($(endpoint_count) * 14 * ENDPOINTS_AZ_NUM + 5) / 10))
if [ -n "$AGENT" ] && [ -n "$CREATE_KB" ]; then COST_CENTS=$((COST_CENTS + 33 * OPENSEARCH_AZ_NUM)); fi
if [ -z "$SKIP_LAB" ]; then COST_CENTS=$((COST_CENTS + 17)); fi
if [ -z "$SKIP_GRAPH" ]; then COST_CENTS=$((COST_CENTS + 58 * NEPTUNE_AZ_NUM)); fi
if [ -z "$SKIP_STREAM" ]; then
  # MSK は kafka.m5.large × 2 で 0.542（Kafka 4 は t3.small を受け付けない。2026-09-18）。3 AZ ならブローカーが 1 台増える
  COST_CENTS=$((COST_CENTS + 57 + 27 * (MSK_AZ_NUM - 2)))
  COST_CENTS=$((COST_CENTS + (12 * (1 + TELEGRAF_AZ_NUM) + 24 + 5) / 10))   # Telegraf（Fargate のタスク 1 + TELEGRAF_AZ_NUM 個と NLB）
fi
if [ -z "$SKIP_ANALYTICS" ]; then
  # Spark のジョブ（1 つ 21。格納先で 3 つ）
  if [ -n "$SINK_S3" ]; then COST_CENTS=$((COST_CENTS + 21)); fi
  if [ -n "$SINK_SPLUNK" ]; then COST_CENTS=$((COST_CENTS + 21)); fi
  if [ -n "$SINK_OPENSEARCH$SINK_PROMETHEUS" ]; then COST_CENTS=$((COST_CENTS + 21)); fi
  if [ -n "$SINK_OPENSEARCH" ]; then COST_CENTS=$((COST_CENTS + 33 * OPENSEARCH_AZ_NUM)); fi
  if [ -n "$GRAFANA" ]; then COST_CENTS=$((COST_CENTS + 2)); fi
  if [ -n "$SPLUNK_ON_ECS" ]; then COST_CENTS=$((COST_CENTS + 12)); fi
fi
if [ -n "$NAUTOBOT" ]; then COST_CENTS=$((COST_CENTS + 13 + 3 * (NAUTOBOT_DB_AZ_NUM - 1))); fi
if [ -n "$WORKFLOW" ]; then COST_CENTS=$((COST_CENTS + 5)); fi
if { [ -n "$AGENT" ] && [ -n "$CREATE_KB" ]; } || { [ -z "$SKIP_ANALYTICS" ] && [ -n "$SINK_OPENSEARCH" ]; }; then COST_CENTS=$((COST_CENTS + (14 * ENDPOINTS_AZ_NUM + 5) / 10)); fi
COST_NOTE=$(printf "待機だけで約 \$%d.%02d/h（約 %d 円/h${AGENT:+。チャットの分は別}）の時間課金。使い終わったら当日中に ops/down.sh を打つ" \
  $((COST_CENTS / 100)) $((COST_CENTS % 100)) $(((COST_CENTS * 150 + 50) / 100)))
printf '\033[1;33m%s\033[0m\n' "$COST_NOTE"
if [ -n "$AZ_NUM_SET" ]; then echo "AZ をまたぐ転送料（\$0.01/GB 前後。別の AZ の MSK のブローカーへ書く・読む分、NLB のクロスゾーンの分など）は上の目安に入っていない"; fi
case ",$SINKS," in
  *,opensearch,*) if [ -z "$SKIP_ANALYTICS" ]; then printf '\033[1;33m%s\033[0m\n' "STORES の grafana（既定）: OpenSearch Serverless の logs コレクションを作る。OCU が KB のコレクションと共有されなければ最大 \$0.$((33 * OPENSEARCH_AZ_NUM))/h で、上の目安はそれを含んでいる"; fi ;;
esac

# SG は 2026-09-29 にワークロードごとに分けた（terraform/base/core の security_groups.tf）。それより前の state（全部で共有する internal 1 つ）からは
# apply できない（ほかのルートのリソースと Runtime の ENI が internal を付けたままなので、消すところで DependencyViolation になる）。何か作る前に止める
if [ -f terraform/base/core/terraform.tfstate ]; then
  tf_init base/core
  if tf base/core state list 2>/dev/null | grep -qx 'aws_security_group\.internal'; then
    die "terraform/base/core の state に 2026-09-29 より前の SG（internal）が残っている。先に ops/down.sh で消す（Runtime の ENI が残るあいだは VPC・サブネットと一緒に残るので、時間をおいて打ち直す）。まだ何も作っていない"
  fi
  # Telegraf の SG の名前を 2026-10-04 に dialout / dialin にそろえた（telegraf → telegraf_dialout、telegraf_poll → telegraf_dialin、telegraf_nlb → telegraf_dialout_nlb）。
  # 古い名前の SG は stream の NLB と ECS のタスクが付けたままだと消せない（DependencyViolation）ので、stream が残っているなら先に消してもらう
  if tf base/core state list 2>/dev/null | grep -qxF 'aws_security_group.workload["telegraf"]' \
    && [ -s terraform/pipeline/stream/terraform.tfstate ] && { tf_init pipeline/stream; [ -n "$(tf pipeline/stream state list 2>/dev/null)" ]; }; then
    die "terraform/base/core の state に 2026-10-04 より前の Telegraf の SG（telegraf / telegraf_poll / telegraf_nlb）が残っていて、stream がそれを使っている。先に ops/down.sh で消す（stream だけ先に消してもよい）。まだ何も作っていない"
  fi
fi

# ---- 1. ECR --------------------------------------------------------------------
log "1. ECR リポジトリ（terraform/base/ecr）"
tf_apply base/ecr
REPO=$(tf base/ecr output -raw agent_repository_url); echo "REPO=$REPO"
REG="${REPO%%/*}"
TEMPORAL_TAG=1.9.1   # terraform/workflow の temporal_image_tag の既定値。変えるときは両方を変える

# ---- 2. イメージ ----------------------------------------------------------------
log "2. イメージ（ECR に無いタグだけ作る）"
NEED_AGENT=""; NEED_LAB=""; NEED_WORKER=""; NEED_TEMPORAL=""; NEED_TELEGRAF=""; NEED_GRAFANA=""; NEED_SPLUNK=""; NEED_NAUTOBOT=""; NEED_REDIS=""
# telegraf / grafana / splunk は Dockerfile のあるディレクトリの中身からタグを作る（中身を変えれば次の ops/up.sh が作り直す）
TELEGRAF_TAG=""; GRAFANA_TAG=""; SPLUNK_TAG=""; NAUTOBOT_TAG=""
if [ -n "$AGENT" ]; then
  if ecr_has "$PREFIX-agent" "$IMAGE_TAG"; then echo "agent:$IMAGE_TAG はある（作り直すなら IMAGE_TAG を変える）"; else NEED_AGENT=1; fi
fi
if [ -z "$SKIP_LAB" ]; then
  if ! ecr_has "$PREFIX-lab-srlinux" "$SRLINUX_TAG" || ! ecr_has "$PREFIX-lab-multitool" "$MULTITOOL_TAG"; then NEED_LAB=1
  else echo "lab-srlinux:$SRLINUX_TAG と lab-multitool:$MULTITOOL_TAG はある"; fi
fi
if [ -n "$WORKFLOW" ]; then
  if ecr_has "$PREFIX-worker" "$IMAGE_TAG"; then echo "worker:$IMAGE_TAG はある"; else NEED_WORKER=1; fi
  if ecr_has "$PREFIX-temporal" "$TEMPORAL_TAG"; then echo "temporal:$TEMPORAL_TAG はある"; else NEED_TEMPORAL=1; fi
fi
if [ -z "$SKIP_STREAM" ]; then
  TELEGRAF_TAG=$(telegraf_tag) || die "telegraf/ のタグを作れなかった"
  if ecr_has "$PREFIX-telegraf" "$TELEGRAF_TAG"; then echo "telegraf:$TELEGRAF_TAG はある"; else NEED_TELEGRAF=1; fi
fi
if [ -n "$GRAFANA" ]; then
  GRAFANA_TAG=$(dir_tag "$GRAFANA_VERSION" grafana) || die "grafana/ のタグを作れなかった"
  if ecr_has "$PREFIX-grafana" "$GRAFANA_TAG"; then echo "grafana:$GRAFANA_TAG はある"; else NEED_GRAFANA=1; fi
fi
if [ -n "$SPLUNK_ON_ECS" ]; then
  SPLUNK_TAG=$(dir_tag "$SPLUNK_VERSION" splunk) || die "splunk/ のタグを作れなかった"
  if ecr_has "$PREFIX-splunk" "$SPLUNK_TAG"; then echo "splunk:$SPLUNK_TAG はある"; else NEED_SPLUNK=1; fi
fi
if [ -n "$NAUTOBOT" ]; then
  NAUTOBOT_CTX=$(mktemp -d "${TMPDIR:-/tmp}/$PREFIX-nautobot.XXXXXX") || die "一時ディレクトリを作れない（TMPDIR）"
  nautobot_context "$NAUTOBOT_CTX" || die "Nautobot のイメージの材料（nautobot/ と agent/graph.py・toolkit.py と lab の定義）を集められなかった"
  NAUTOBOT_TAG=$(dir_tag "$NAUTOBOT_VERSION" "$NAUTOBOT_CTX") || die "nautobot/ のタグを作れなかった"
  if ecr_has "$PREFIX-nautobot" "$NAUTOBOT_TAG"; then echo "nautobot:$NAUTOBOT_TAG はある"; else NEED_NAUTOBOT=1; fi
  if ecr_has "$PREFIX-redis" "$REDIS_TAG"; then echo "redis:$REDIS_TAG はある"; else NEED_REDIS=1; fi
fi
if [ -z "$NEED_AGENT$NEED_LAB$NEED_WORKER$NEED_TEMPORAL$NEED_TELEGRAF$NEED_GRAFANA$NEED_SPLUNK$NEED_NAUTOBOT$NEED_REDIS" ]; then
  echo "作るイメージは無い"
else
  docker info >/dev/null 2>&1 || die "dockerd に接続できない（WSL なら sudo service docker start。docs/setup.md「Terraform を打つ PC 側」）"
  # agent / worker / grafana / nautobot は RUN があるので、x86_64 の PC では QEMU（binfmt）が要る（lab のイメージは上流の arm64 をミラーするだけで、
  # telegraf は COPY だけ。splunk も COPY だけで amd64 なので、arm64 の PC（Apple シリコン）でもエミュレーション無しで作れる）
  # 出力は変数で受けてから探す（grep -q が先に閉じると docker が SIGPIPE で落ち、pipefail で「無い」扱いになることがある）
  BUILDX_LS=$(docker buildx ls 2>/dev/null || true)
  if [ -n "$NEED_AGENT$NEED_WORKER$NEED_GRAFANA$NEED_NAUTOBOT" ] && ! grep -q 'linux/arm64' <<<"$BUILDX_LS"; then
    die "docker buildx ls の Platforms に linux/arm64 が無い（docs/setup.md「WSL2（Ubuntu）」の binfmt の行）"
  fi
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REG"
  if [ -n "$NEED_AGENT" ]; then
    docker buildx build --platform linux/arm64 -t "$REPO:$IMAGE_TAG" --push agent/
  fi
  if [ -n "$NEED_LAB" ]; then
    # Nokia SR Linux（公開イメージ。約 1 GB）と VM の multitool。ECR にミラーして lab の EC2 が VPC の中から引けるようにする（ops/lab-common.sh）
    mirror_lab_images "$REG" "$PREFIX" || die "lab のイメージを ECR に置けなかった"
  fi
  if [ -n "$NEED_WORKER" ]; then
    docker buildx build --platform linux/arm64 -t "$REG/$PREFIX-worker:$IMAGE_TAG" --push workflow/
  fi
  if [ -n "$NEED_TEMPORAL" ]; then
    # Temporal の CLI 入りイメージ（temporal server start-dev。arm64 あり）。Fargate は ECR からしか安定して引けないのでミラーする
    docker pull --platform linux/arm64 "temporalio/temporal:$TEMPORAL_TAG"
    docker tag "temporalio/temporal:$TEMPORAL_TAG" "$REG/$PREFIX-temporal:$TEMPORAL_TAG"
    docker push "$REG/$PREFIX-temporal:$TEMPORAL_TAG"
  fi
  if [ -n "$NEED_TELEGRAF" ]; then
    build_telegraf "$REG/$PREFIX-telegraf:$TELEGRAF_TAG"
  fi
  if [ -n "$NEED_GRAFANA" ]; then
    # データソースの plugin をビルドのときに入れる（タスクは AWS の外へ出られず、起動時に grafana.com から落とせない）
    docker buildx build --platform linux/arm64 --build-arg "GRAFANA_VERSION=$GRAFANA_VERSION" -t "$REG/$PREFIX-grafana:$GRAFANA_TAG" --push grafana/
  fi
  if [ -n "$NEED_SPLUNK" ]; then
    # Splunk Enterprise の公式イメージ（amd64 だけ。約 2〜3 GB）に検知のアプリ（splunk/netops_alerts）を足す。
    # Fargate は VPC の中から ECR しか引けず、タスクは Splunkbase にも出られないので、アプリはビルドのときに入れる
    docker buildx build --platform linux/amd64 --build-arg "SPLUNK_VERSION=$SPLUNK_VERSION" -t "$REG/$PREFIX-splunk:$SPLUNK_TAG" --push splunk/
  fi
  if [ -n "$NEED_NAUTOBOT" ]; then
    # Nautobot の公式イメージ（arm64。約 1 GB）に boto3 と Job（nautobot/jobs）と対応付け（nautobot/netops + agent/graph.py）と最初の seed を足す
    docker buildx build --platform linux/arm64 --build-arg "NAUTOBOT_VERSION=$NAUTOBOT_VERSION" -t "$REG/$PREFIX-nautobot:$NAUTOBOT_TAG" --push "$NAUTOBOT_CTX"
  fi
  if [ -n "$NEED_REDIS" ]; then
    # Nautobot のタスクの中で動かす Redis（キャッシュと Celery のブローカー）。Fargate は VPC の中から ECR しか引けないのでミラーする
    mirror_image "redis:$REDIS_TAG" "$REG/$PREFIX-redis:$REDIS_TAG" || die "redis のイメージを ECR に置けなかった"
  fi
fi

# ---- 3. 本体 --------------------------------------------------------------------
log "3. 土台（terraform/base/core。VPC / Web の EC2 / バケット / ロール。初回は 3〜5 分）"
MAIN_VARS=()
if [ -n "${VPC_CIDR:-}" ];    then MAIN_VARS+=(-var "vpc_cidr=$VPC_CIDR"); fi
if [ -n "${MDT_SOURCE_CIDRS:-}" ]; then MAIN_VARS+=(-var "mdt_source_cidrs=[\"$(printf '%s' "$MDT_SOURCE_CIDRS" | tr -d ' ' | sed 's/,/","/g')\"]"); fi
# OpenSearch Serverless の VPC エンドポイントは KB と logs のコレクションで 1 本を共用する（どちらも公開しない）。
# 今回どちらも作らなくても、前に作ったコレクションが state に残っていれば外さない（外すとそのコレクションに届かなくなる）
NEED_AOSS=""
if [ -n "$CREATE_KB" ]; then NEED_AOSS=1; fi
if [ -z "$SKIP_ANALYTICS" ] && [ -n "$SINK_OPENSEARCH" ]; then NEED_AOSS=1; fi
for r in agent pipeline/analytics; do
  if [ -z "$NEED_AOSS" ] && [ -f "terraform/$r/terraform.tfstate" ]; then
    tf_init "$r"
    if tf "$r" state list 2>/dev/null | grep -q '^aws_opensearchserverless_collection\.'; then NEED_AOSS=1; fi
  fi
done
if [ -n "$NEED_AOSS" ]; then MAIN_VARS+=(-var create_opensearch_endpoint=true); fi
# 今回作らないルートでも、state にリソースが残っていればそのエンドポイントを残す。
# graph と workflow の履歴の分（kinesis-firehose / athena）は残った analytics も数えるので、analytics より後に回す
for r in agent pipeline/lab pipeline/stream pipeline/analytics pipeline/graph pipeline/nautobot workflow; do
  case " $ROOTS " in *" $r "*) continue ;; esac
  if [ -f "terraform/$r/terraform.tfstate" ]; then
    tf_init "$r"
    if has_resources "$r"; then
      endpoints_for "$r"
      if [ "$r" = pipeline/analytics ]; then ANALYTICS_LEFT=1; fi
      echo "terraform/$r は今回作らないが state にリソースが残っているので、そのエンドポイントを残す"
    fi
  fi
done
# 残った analytics を見つけたら、今回作る graph / workflow の履歴の分（kinesis-firehose / athena）もここで足す。
# 作るルートの endpoints_for の時点では ANALYTICS_LEFT がまだ分からない。graph の alert_history と workflow の Athena の環境変数は、残った analytics でも付く
if [ -n "$ANALYTICS_LEFT" ]; then for r in $ROOTS; do endpoints_for "$r"; done; fi
for pair in 'agent aws_bedrockagent_knowledge_base\.' 'pipeline/analytics aws_prometheus_workspace\.' 'pipeline/analytics aws_ecs_service\.'; do
  r=${pair%% *}
  [ -f "terraform/$r/terraform.tfstate" ] || continue
  tf_init "$r"
  if tf "$r" state list 2>/dev/null | grep -q "^${pair#* }"; then
    case "$pair" in agent*) add_endpoints bedrock-agent-runtime ;; *prometheus*) add_endpoints aps-workspaces ;; *) add_endpoints ecr.api ecr.dkr sns ;; esac
  fi
done
MAIN_VARS+=(-var "interface_endpoints=[\"$(printf '%s' "$ENDPOINTS" | sed 's/ /","/g')\"]")
MAIN_VARS+=(-var "network_perimeter=$([ -n "$NETWORK_PERIMETER" ] && echo true || echo false)")
MAIN_VARS+=(-var "endpoints_az_num=$ENDPOINTS_AZ_NUM")
echo "エンドポイント: $ENDPOINTS"
# SG が internal 1 つだった頃（2026-09-26〜29）の state は手順 0 の後で止めている（先に ops/down.sh）。
# それより前（7c42b0f まで、ルートごとにエンドポイントと SG を持っていた頃）の state が残っていれば、同じく先に ops/down.sh で消す
tf_apply base/core ${MAIN_VARS[@]+"${MAIN_VARS[@]}"}
INSTANCE_ID=$(tf base/core output -raw web_instance_id)
KB_BUCKET=$(tf base/core output -raw kb_bucket_name)
echo "INSTANCE_ID=$INSTANCE_ID KB_BUCKET=$KB_BUCKET"

# graph は base/core の state しか読まないので、ここで裏で始めて待ち時間を重ねる（Neptune Analytics のグラフは作るのに数分〜十数分。実測はまだ無い）
if [ -z "$SKIP_GRAPH" ]; then
  log "3-2. graph（Neptune Analytics）の apply を裏で始める（数分〜十数分。待たずに次へ進む）"
  mkdir -p ops/logs
  tf_init pipeline/graph   # init は前で済ませる（provider のキャッシュを 2 つの init で同時に触らない）
  # analytics がある回（今回作るか、手順 3 で state に残っていると分かった）は、status の Lambda がアラートの通知の履歴を
  # analytics の Firehose（名前は固定）に送る。kinesis-firehose のエンドポイントを足す条件（analytics_on）と揃える
  GRAPH_VARS=(-var "neptune_az_num=$NEPTUNE_AZ_NUM" -var "lambda_az_num=$LAMBDA_AZ_NUM")
  if analytics_on; then GRAPH_VARS+=(-var alert_history=true); fi
  ( tf_apply_only pipeline/graph "${GRAPH_VARS[@]}" ) >"$GRAPH_LOG" 2>&1 &
  GRAPH_PID=$!
  echo "進み具合: tail -f $GRAPH_LOG"
fi

# ---- 3-3. agent ----------------------------------------------------------------
KB_ID=""; DS_ID=""; LOG_GROUP=""
if [ -n "$AGENT" ]; then
  if [ -n "$CREATE_KB" ]; then
    log "3-3. agent（terraform/agent。Runtime + ガードレール + Knowledge Base。初回は 10〜20 分。OpenSearch Serverless の作成が長い）"
  else
    log "3-3. agent（terraform/agent。Runtime + ガードレール + bedrock のエンドポイント。初回は 5〜10 分）"
  fi
  AGENT_VARS=(-var "agent_image_tag=$IMAGE_TAG" -var "runtime_az_num=$RUNTIME_AZ_NUM" -var "lambda_az_num=$LAMBDA_AZ_NUM" -var "opensearch_az_num=$OPENSEARCH_AZ_NUM")
  if [ -n "$CREATE_KB" ];       then AGENT_VARS+=(-var create_knowledge_base=true); fi
  tf_apply agent "${AGENT_VARS[@]}"
  LOG_GROUP=$(tf agent output -raw runtime_log_group_name)
  if [ -n "$CREATE_KB" ]; then
    KB_ID=$(tf agent output -raw knowledge_base_id)
    DS_ID=$(tf agent output -raw data_source_id)
    echo "KB_ID=$KB_ID DS_ID=$DS_ID"
  fi
  echo "Runtime の ARN は SSM の $(tf agent output -raw runtime_arn_parameter_name) に置いた（Web は 60 秒以内に拾う）"
fi

# ---- 4. Web の部品と手順書 ------------------------------------------------------------
log "4-1. wheel（arm64 / cp313）"
if [ -z "$(ls wheels/*.whl 2>/dev/null)" ]; then
  if command -v uv >/dev/null; then PIP="uv run --python 3.13 --with pip python -m pip"; else PIP="python3 -m pip"; fi
  $PIP download --only-binary=:all: \
    --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 --platform manylinux_2_28_aarch64 \
    --python-version 3.13 --implementation cp --abi cp313 --abi none \
    -d wheels -r web/requirements.txt
else
  echo "wheels/ に $(ls wheels/*.whl | wc -l | tr -d ' ') 個ある。取り直すなら wheels/ を消す"
fi

log "4-2. Web の部品を s3://$KB_BUCKET/web/ に置く"
# app.py が import する web/ の .py（chat / config / incident_view / topology_view）も全部置く。app.py だけだと Web が起動のたびに落ちる
for f in web/*.py; do aws s3 cp --only-show-errors "$f" "s3://$KB_BUCKET/web/${f#web/}"; done
aws s3 cp --only-show-errors web/requirements.txt "s3://$KB_BUCKET/web/requirements.txt"
for f in toolkit topology graph proposals; do aws s3 cp --only-show-errors "agent/$f.py" "s3://$KB_BUCKET/web/$f.py"; done
aws s3 cp --only-show-errors agent/data/ "s3://$KB_BUCKET/web/data/" --recursive
aws s3 sync --only-show-errors wheels/ "s3://$KB_BUCKET/web/wheels/"

if [ -n "$CREATE_KB" ]; then
log "4-3. 手順書を置いて取り込む（CREATE_KB=1）"
aws s3 cp --only-show-errors kb-docs/ "s3://$KB_BUCKET/docs/" --recursive --exclude "*" --include "*.md"
# 索引を作った直後は StartIngestionJob が「no such index」の ValidationException を返す（OpenSearch Serverless 側の反映待ち。
# 2026-09-17 に索引の置き換えの 2 秒後で実測）。10 秒おきに最大 12 回（2 分）まで打ち直す
JOB_ID=""
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  JOB_ID=$(aws bedrock-agent start-ingestion-job --region "$REGION" \
    --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" \
    --query ingestionJob.ingestionJobId --output text 2>"${TMPDIR:-/tmp}/ingest-err.$$") && break
  if grep -q "no such index" "${TMPDIR:-/tmp}/ingest-err.$$"; then
    echo "索引がまだ見えない（${i} 回目）。10 秒待つ"; sleep 10; JOB_ID=""
  else
    cat "${TMPDIR:-/tmp}/ingest-err.$$" >&2; rm -f "${TMPDIR:-/tmp}/ingest-err.$$"; die "取り込みジョブを開始できない"
  fi
done
rm -f "${TMPDIR:-/tmp}/ingest-err.$$"
[ -n "$JOB_ID" ] || die "索引が 2 分たっても見えない。aws opensearchserverless で kb コレクションの状態を確かめる"
while :; do
  ST=$(aws bedrock-agent get-ingestion-job --region "$REGION" \
    --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" --ingestion-job-id "$JOB_ID" \
    --query 'ingestionJob.[status,statistics.numberOfDocumentsFailed]' --output text)
  case "$ST" in
    COMPLETE*) echo "取り込み ${ST}（status failed）"; [ "${ST#COMPLETE}" = $'\t0' ] || die "取り込みに失敗した md がある。get-ingestion-job の failureReasons を見る"; break ;;
    FAILED*|STOPPED*) die "取り込みジョブが $ST" ;;
    *) sleep 10 ;;
  esac
done
fi

log "4-4. EC2 を再起動して Web を立てる（初回の apply 時点では web/ が無いため）"
wait_ssm_online "$INSTANCE_ID"
run_on_instance "$INSTANCE_ID" "true"          # 初回の user_data が終わるのを待ってから再起動する
aws ec2 reboot-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
sleep 30
wait_ssm_online "$INSTANCE_ID"
# 起動の失敗（環境変数や依存の不足）は数秒後に落ちる。is-active は落ちて再起動するまでの数秒も active と読むので、Gradio が 8080 を
# 聞いているかで見る。2 分待って聞いていなければ status と journald を出して止まる
WEB_ACTIVE="for i in \$(seq 1 24); do ss -ltn 'sport = :8080' | grep -q LISTEN && exit 0; sleep 5; done; systemctl --no-pager status $PREFIX-web.service; journalctl --no-pager -u $PREFIX-web.service -n 50; exit 1"
run_on_instance "$INSTANCE_ID" "$WEB_ACTIVE"
echo "Web が動いている"

# ---- 5. lab と stream の材料 --------------------------------------------------------
# lab の EC2 は起動のたびに s3://<バケット>/lab/ を読む。apply より前に置けば、最初の起動で入る（再起動が要らない）
# Telegraf（stream の ECS）への転送（terraform/pipeline/lab の forward_to_telegraf）は stream を作るときに付ける。SKIP_STREAM=1 でも stream が残っていれば残す
LAB_VARS=(-var forward_to_telegraf=false)
if [ -z "$SKIP_LAB" ]; then
  if [ -z "$SKIP_STREAM" ]; then
    LAB_VARS=(-var forward_to_telegraf=true)
  elif [ -f terraform/pipeline/stream/terraform.tfstate ]; then
    tf_init pipeline/stream
    if has_resources pipeline/stream; then
      echo "SKIP_STREAM=1 だが terraform/pipeline/stream が残っているので、Telegraf への転送は残す"
      LAB_VARS=(-var forward_to_telegraf=true)
    fi
  fi
  log "5-1. lab の材料（containerlab の rpm とトポロジ）を s3://$KB_BUCKET/lab/ に置く"
  upload_lab "$KB_BUCKET" || die "lab の材料を s3://$KB_BUCKET/lab/ に置けなかった"
fi
if [ -z "$SKIP_ANALYTICS" ]; then
  log "5-2. Spark のスクリプトと jar（Kafka / MSK IAM / S3 Tables カタログ）を s3://$KB_BUCKET/analytics/ に置く"
  mkdir -p "$JARS_DIR"
  for url in "${JAR_URLS[@]}"; do
    fetch "$url" "$JARS_DIR/${url##*/}" || die "jar が取れない: $url （社内 PC なら docs/setup.md「社内 PC の CA」）"
  done
  "${PY[@]}" -c 'import ast, sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$SPARK_SCRIPT" || die "$SPARK_SCRIPT が Python として読めない"
  aws s3 cp --only-show-errors "$SPARK_SCRIPT" "s3://$KB_BUCKET/analytics/"
  aws s3 sync --only-show-errors "$JARS_DIR/" "s3://$KB_BUCKET/analytics/jars/" --exclude "*" --include "*.jar"
fi

# ---- 6. lab ---------------------------------------------------------------------
LAB_INSTANCE_ID=""; LAB_WARN=""
LAB_NODES=$(grep -cE '^ *kind: (nokia_srlinux|linux)$' lab/splab.clab.yml.in)   # containerlab のノードの数（8。SR Linux 6 + VM 2）
if [ -z "$SKIP_LAB" ]; then
  log "6. lab（terraform/pipeline/lab。EC2 の中でトポロジが上がるまで 10 分ほど（SR Linux 6 台の起動）。${LAB_VARS[1]}）"
  tf_apply pipeline/lab "${LAB_VARS[@]}"
  LAB_INSTANCE_ID=$(tf pipeline/lab output -raw lab_instance_id); echo "LAB_INSTANCE_ID=$LAB_INSTANCE_ID"
fi

# ---- 7. stream ------------------------------------------------------------------
if [ -z "$SKIP_STREAM" ]; then
  log "7. stream（terraform/pipeline/stream。MSK の作成に 20〜30 分。Telegraf は ECS のタスク）"
  # Telegraf のポーリング先と gNMI の購読先は lab の定義から作る（機器の一覧を lab の定義 1 か所にする。telegraf/telegraf.sh が
  # タスクの環境変数から telegraf.conf.in の __SNMP_AGENTS__ / __GNMI_TARGETS__ を埋める）。
  # ポーリング先は SNMP_POLL=0 でも作って渡す（stream の snmp_agents は必須。telegraf.sh は SNMP_POLL=1 のときだけ使う）
  SNMP_AGENTS=$("${PY[@]}" lab/lab_topology.py lab --snmp-agents) || die "lab/lab_topology.py が lab の定義からポーリング先を作れなかった"
  GNMI_TARGETS=$("${PY[@]}" lab/lab_topology.py lab --gnmi-targets) || die "lab/lab_topology.py が lab の定義から gNMI の購読先を作れなかった"
  if [ -n "$SNMP_POLL" ]; then
    SNMP_POLL_TF=true
    echo "Telegraf の SNMP: trap を受け、ポーリングもする（SNMP_POLL=1）。ポーリング先: $SNMP_AGENTS"
  else
    SNMP_POLL_TF=false
    echo "Telegraf の SNMP: trap だけ受ける（ポーリングしない。する場合は SNMP_POLL=1）"
  fi
  echo "Telegraf の gNMI の購読先: $GNMI_TARGETS"
  # syslog の形式は SYSLOG_STANDARD（既定は本番の Cisco の RFC3164）。lab の SR Linux は ops/lab-common.sh の LAB_SYSLOG_STANDARD（RFC5424）で送る
  echo "Telegraf の syslog の形式: $SYSLOG_STANDARD"
  if [ -z "$SKIP_LAB" ] && [ "$SYSLOG_STANDARD" != "$LAB_SYSLOG_STANDARD" ]; then
    echo "注意: lab の SR Linux は $LAB_SYSLOG_STANDARD で送るので、SYSLOG_STANDARD=$SYSLOG_STANDARD では lab のログの項目（ホスト名・本文など）が崩れる。lab のログまで見るなら SYSLOG_STANDARD=$LAB_SYSLOG_STANDARD"
  fi
  # 機器の認証情報は SSM の SecureString に置き、取りにいく側のタスクが ECS の secrets で受ける（Terraform の state に載せない）。
  # 最初の値は lab の公開既定値（ops/lab-common.sh）。もうあれば触らないので、実機を足すときは SSM の値を書き換えてサービスを作り直す
  ensure_fixed_secret "/$PREFIX/telegraf-dialin/gnmi-username" "$LAB_GNMI_USERNAME" "gNMI username of the Telegraf dial-in task (created by ops/up.sh with the containerlab default)"
  ensure_fixed_secret "/$PREFIX/telegraf-dialin/gnmi-password" "$LAB_GNMI_PASSWORD" "gNMI password of the Telegraf dial-in task (created by ops/up.sh with the containerlab default)"
  ensure_fixed_secret "/$PREFIX/telegraf-dialin/snmp-community" "$LAB_SNMP_COMMUNITY" "SNMP community of the Telegraf dial-in task (created by ops/up.sh with the containerlab default)"
  # 取りにいく側の一覧は Nautobot の Job が書き換える SSM のパラメータ（…/telegraf-dialin/nautobot/*）から受ける（stream を作るなら Nautobot もいつも作る）。
  # Terraform が書くのは最初の値（上の lab の一覧。Nautobot の最初の seed も lab なので同じ）だけ
  DIALIN_FROM_NAUTOBOT=true
  echo "Telegraf の取りにいく側の機器の一覧: Nautobot の Job が書く（上の一覧は最初の値）"
  tf_apply pipeline/stream -var "telegraf_image_tag=$TELEGRAF_TAG" -var "snmp_agents=$SNMP_AGENTS" -var "gnmi_targets=$GNMI_TARGETS" \
    -var "syslog_standard=$SYSLOG_STANDARD" -var "snmp_poll=$SNMP_POLL_TF" -var "dialin_targets_from_nautobot=$DIALIN_FROM_NAUTOBOT" \
    -var "msk_az_num=$MSK_AZ_NUM" -var "telegraf_az_num=$TELEGRAF_AZ_NUM"
fi

# ---- 7-2. lab と Telegraf の中を確かめる ------------------------------------------------
if [ -n "$LAB_INSTANCE_ID" ]; then
  # lab の EC2 の中を見る。ユニットの有無だけで見ると、イメージが取れずにトポロジが上がっていなくても「入っている」と読む（2026-09-17）
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
  if [ "${LAB_VARS[1]}" = forward_to_telegraf=true ]; then
    # lab の EC2 の起動時の forward は、stream（Telegraf の NLB のアドレスを SSM に置く）より前だと宛先を読めていない。
    # 何度打っても同じ規則になるので毎回打つ（トポロジが上がっていなければ lab.sh の up がまた打つ）
    log "7-2b. lab の EC2 から Telegraf（stream の ECS）へ SNMP / gNMI / trap / syslog を通す（lab forward）"
    ssm_run "$LAB_INSTANCE_ID" "[ ! -x /usr/local/bin/lab ] || /usr/local/bin/lab forward" \
      || printf '\033[1;33m%s\033[0m\n' "lab forward が失敗した。lab の EC2 で sudo lab forward-status を見る（docs/pipeline.md）"
  fi
fi
if [ -z "$SKIP_STREAM" ]; then
  log "7-2c. Telegraf の ECS のサービス 2 つ（受ける側と取りにいく側）が安定するのを待つ（イメージの取得と NLB のヘルスチェック。1〜3 分）"
  TG_CLUSTER=$(tf pipeline/stream output -raw telegraf_cluster_name); TG_DIALOUT_SERVICE=$(tf pipeline/stream output -raw telegraf_dialout_service_name)
  TG_DIALIN_SERVICE=$(tf pipeline/stream output -raw telegraf_dialin_service_name)
  if aws ecs wait services-stable --region "$REGION" --cluster "$TG_CLUSTER" --services "$TG_DIALOUT_SERVICE" "$TG_DIALIN_SERVICE"; then
    echo "Telegraf は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/stream output -raw telegraf_log_group_name) --follow。ストリームは受ける側が dialout/、取りにいく側が dialin/）"
  else
    printf '\033[1;33m%s\033[0m\n' "Telegraf のサービスが 10 分たっても安定しない。受ける側は $(tf pipeline/stream output -raw telegraf_dialout_list_tasks_command)、取りにいく側は $(tf pipeline/stream output -raw telegraf_dialin_list_tasks_command) とロググループ $(tf pipeline/stream output -raw telegraf_log_group_name) を見る（docs/troubleshooting.md）"
  fi
fi

# ---- 7-3. graph と投入 --------------------------------------------------------------
# 検知（7-4 の Grafana / Splunk）より先に graph（status の Lambda とトピックの購読）とトポロジを入れる。後だと、購読が無いうちのアラートは
# Lambda に届かず（次に同じ状態の知らせが来るまで、画面の status が UP のまま）、トポロジが空のうちのアラートは「未登録」の頂点になって
# UNREGISTERED の警告が出る（status は投入のときに引き継ぐので消えないが、登録漏れと見分けが付かない）。graph は 3-2 から裏で走っていて、
# stream（MSK に 20〜30 分）の方が長いので、ここで待ってもたいてい待たない
if [ -n "$GRAPH_PID" ]; then
  log "7-3. graph の apply が終わるのを待つ"
  rc=0; wait "$GRAPH_PID" || rc=$?; GRAPH_PID=""
  if [ "$rc" -ne 0 ]; then
    tail -n 40 "$GRAPH_LOG" >&2
    die "terraform/pipeline/graph の apply に失敗した（全文: ${GRAPH_LOG}）。直したらもう一度 ops/up.sh"
  fi
  tail -n 3 "$GRAPH_LOG"
  # lab を作らない（SKIP_LAB=1）なら入れない。Neptune には Nautobot の Job（7-3c）が書く物理層だけが入る
  if [ -z "$SKIP_LAB" ]; then
    log "7-3b. Neptune が空なら lab の定義からトポロジを入れる（初期ロード。入っていれば何もしない。入れ直すのは ops/sync-graph.sh --replace）"
    # lab/lab_topology.py が lab/splab.clab.yml.in と lab/srlinux/*.cli から機器と回線（と IP 層 / EVPN・BGP 層）を作り（手元で打つ）、ops/seed_graph.py を Web の EC2 の上で
    # Web と同じ環境変数と依存で動かして Neptune に入れる。コマンドに記号を入れないよう、スクリプトもトポロジも base64 で渡す
    LAB_TOPOLOGY_B64=$("${PY[@]}" lab/lab_topology.py lab | base64 | tr -d '\n') || die "lab/lab_topology.py が lab の定義を読めなかった"
    run_on_instance "$INSTANCE_ID" "echo $(base64 < ops/seed_graph.py | tr -d '\n') | base64 -d | NAME_PREFIX=$PREFIX LAB_TOPOLOGY_B64=$LAB_TOPOLOGY_B64 /usr/bin/python3.13 -"
  else
    echo "7-3b は飛ばす（SKIP_LAB=1。lab の定義からトポロジを入れない）"
  fi
fi

# ---- 7-3c. Nautobot -----------------------------------------------------------------
# stream（dialin の一覧の SSM パラメータとサービス）と graph（Neptune）の state を読むので、その 2 つの後。Neptune には 7-3b で lab の全層が入っていて、
# Nautobot の Job は物理層だけを Nautobot に合わせる（最初は Nautobot も lab から入るので差分は無い。SKIP_LAB=1 なら 7-3b が無く、Job が書く物理層だけになる）
NAUTOBOT_WARN=""
if [ -n "$NAUTOBOT" ]; then
  log "7-3c. Nautobot（terraform/pipeline/nautobot。RDS の作成に 5〜10 分、初回の起動（DB の migrate）に 5〜10 分）"
  # Django の SECRET_KEY・画面の管理者のパスワード・RDS のマスターユーザーのパスワードは SSM に乱数で作る（Terraform の state に載せない）
  ensure_secret "/$PREFIX/nautobot/secret-key" password "Nautobot SECRET_KEY (created by ops/up.sh)"
  ensure_secret "/$PREFIX/nautobot/admin-password" password "Nautobot admin password (created by ops/up.sh)"
  ensure_secret "/$PREFIX/nautobot/db-password" password "Nautobot database password (created by ops/up.sh)"
  # Web の「トポロジ」タブがリンクの追加・削除を Nautobot の REST API に書くためのトークン（bootstrap.py が同じ値でユーザー netops-web のトークンを作る）
  ensure_secret "/$PREFIX/nautobot/api-token" token "Nautobot API token of the web UI (created by ops/up.sh)"
  tf_apply pipeline/nautobot -var "nautobot_image_tag=$NAUTOBOT_TAG" -var "redis_image_tag=$REDIS_TAG" -var "nautobot_db_az_num=$NAUTOBOT_DB_AZ_NUM"
  echo "Nautobot の Job の書き先: $(tf pipeline/nautobot output -json sync_targets)"
  NB_CLUSTER=$(tf pipeline/nautobot output -raw cluster_name); NB_SERVICE=$(tf pipeline/nautobot output -raw service_name)
  # services-stable は 1 回で最大 10 分。初回は migrate のあいだタスクが RUNNING にならない（worker が web の HEALTHY を待つ）ので 2 回まで待つ
  if aws ecs wait services-stable --region "$REGION" --cluster "$NB_CLUSTER" --services "$NB_SERVICE" 2>/dev/null \
    || aws ecs wait services-stable --region "$REGION" --cluster "$NB_CLUSTER" --services "$NB_SERVICE"; then
    echo "Nautobot は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/nautobot output -raw log_group_name) --follow。起動時の seed と同期は web/ のストリームの bootstrap の行）"
  else
    NAUTOBOT_WARN="Nautobot のサービスが 20 分たっても安定しない。$(tf pipeline/nautobot output -raw list_tasks_command) とロググループ $(tf pipeline/nautobot output -raw log_group_name) を見る（docs/troubleshooting.md）"
    printf '\033[1;33m%s\033[0m\n' "$NAUTOBOT_WARN"
  fi
fi

# ---- 7-4. analytics ------------------------------------------------------------------
if [ -z "$SKIP_ANALYTICS" ]; then
  log "7-4. analytics（terraform/pipeline/analytics。EMR Serverless と格納先: ${SINKS}。数分）"
  # ドライバーのログは CloudWatch Logs へ出す（terraform/base/core の logs のエンドポイントで届く）
  # 上限を変えるとジョブの引数が変わり、7-5 でそのジョブだけ起こし直す（格納先ごとの値は、その格納先のジョブにだけ渡る）
  ANALYTICS_VARS=(-var "sinks=[$SINKS_TF]" -var "max_offsets_per_trigger=$MAX_OFFSETS_PER_TRIGGER" -var "max_offsets_per_trigger_by_sink={$MAX_OFFSETS_BY_SINK}")
  ANALYTICS_VARS+=(-var "opensearch_az_num=$OPENSEARCH_AZ_NUM")
  ensure_s3tables_catalog   # alert_events への Firehose はこのカタログ越しにテーブルを引く（無いと配信の作成か書き込みで落ちる）
  if [ -n "$GRAFANA" ]; then
    # Grafana の admin のパスワードは SSM に乱数で作る（Terraform の state に載せない。タスクが起動時に実行ロールで読む）
    ensure_secret "/$PREFIX/grafana/admin-password" password "Grafana admin password (created by ops/up.sh)"
    ANALYTICS_VARS+=(-var create_grafana=true -var "grafana_image_tag=$GRAFANA_TAG")
  fi
  if [ -n "$SPLUNK_ON_ECS" ]; then
    # Splunk を ECS で立てる。管理者のパスワードと HEC の token は SSM に乱数で作る（token は Splunk が GUID の形を求める）。
    # Splunk のタスクが起動時に読んで設定し、Spark のジョブも同じ token を読む
    echo "Splunk Enterprise（splunk/splunk:$SPLUNK_VERSION・試用ライセンス）を立てる。Splunk のライセンスと Splunk General Terms に同意して起動する"
    # 検知の device map（別名=機器名,...）も lab の定義から作る。trap と gNMI のイベントには sysName が無いので、
    # Splunk のアラートアクションが送り元の IP から機器名を引くのに要る（タスクの環境変数 DEVICE_MAP。変わればタスクが入れ替わる）
    DEVICE_MAP=$("${PY[@]}" lab/lab_topology.py lab --device-map) || die "lab/lab_topology.py が lab の定義から device map を作れなかった"
    ensure_secret "/$PREFIX/splunk/admin-password" password "Splunk admin password (created by ops/up.sh)"
    ensure_secret "/$PREFIX/splunk/hec-token" uuid "Splunk HEC token (created by ops/up.sh)"
    ANALYTICS_VARS+=(-var "splunk_image_tag=$SPLUNK_TAG" -var "splunk_index=$SPLUNK_INDEX" -var "device_map=$DEVICE_MAP")
  fi
  # EMR Serverless のアプリの上限（maximum_capacity。terraform/pipeline/analytics の max_cpu / max_memory の既定値と同じ値。tests/test_analytics.py が検査）
  EMR_MAX_CPU="12 vCPU"; EMR_MAX_MEMORY="48 GB"
  ANALYTICS_VARS+=(-var "max_cpu=$EMR_MAX_CPU" -var "max_memory=$EMR_MAX_MEMORY" -var "emr_az_num=$EMR_AZ_NUM")
  # アプリは STOPPED か CREATED のときしか更新できない（UpdateApplication の API リファレンス）。動いている（STARTED の）まま上限やサブネット
  # （networkConfiguration。EMR_AZ_NUM で数が変わる）を変えると tf_apply が失敗し、打ち直しても同じところで止まる。ジョブが動いていると
  # stop-application も効かない（2026-09-17 に実測）。そこで上限かサブネットの数が変わるときだけ、先にジョブを全部止めてからアプリを止める
  # （ops/down.sh と同じ手順）。止めたジョブは 7-5 が checkpoint から起こし直す
  if [ -f terraform/pipeline/analytics/terraform.tfstate ] && { tf_init pipeline/analytics; has_resources pipeline/analytics; }; then
    APP_ID=$(tf pipeline/analytics output -raw application_id 2>/dev/null || true)
    APP_NOW=""
    if [ -n "$APP_ID" ]; then
      APP_NOW=$(aws emr-serverless get-application --region "$REGION" --application-id "$APP_ID" \
        --query 'application.[state,maximumCapacity.cpu,maximumCapacity.memory,length(networkConfiguration.subnetIds || `[]`)]' --output text 2>/dev/null || true)
    fi
    APP_STATE=""; APP_CPU=""; APP_MEMORY=""; APP_SUBNETS=""
    if [ -n "$APP_NOW" ]; then IFS=$'\t' read -r APP_STATE APP_CPU APP_MEMORY APP_SUBNETS <<<"$APP_NOW"; fi
    same_capacity() { [ "$(printf '%s' "$1" | tr -d ' ' | tr '[:upper:]' '[:lower:]')" = "$(printf '%s' "$2" | tr -d ' ' | tr '[:upper:]' '[:lower:]')" ]; }
    APP_CHANGE=""
    if ! { same_capacity "$APP_CPU" "$EMR_MAX_CPU" && same_capacity "$APP_MEMORY" "$EMR_MAX_MEMORY"; }; then
      APP_CHANGE="上限を $APP_CPU / $APP_MEMORY から $EMR_MAX_CPU / $EMR_MAX_MEMORY に"
    fi
    if [ "$APP_SUBNETS" != "$EMR_AZ_NUM" ]; then
      APP_CHANGE="${APP_CHANGE:+$APP_CHANGE、}サブネットを ${APP_SUBNETS:-?} つから $EMR_AZ_NUM つに"
    fi
    if [ -n "$APP_STATE" ] && [ -n "$APP_CHANGE" ]; then
      case "$APP_STATE" in
        STOPPED | CREATED) echo "EMR Serverless のアプリの${APP_CHANGE}変える（アプリは $APP_STATE）" ;;
        *)
          echo "EMR Serverless のアプリの${APP_CHANGE}変える。アプリが $APP_STATE なので、ジョブを全部止めてからアプリを止める（ジョブは 7-5 で起こし直す）"
          RUNS=$(aws emr-serverless list-job-runs --region "$REGION" --application-id "$APP_ID" \
            --states SUBMITTED PENDING SCHEDULED RUNNING QUEUED --query 'jobRuns[].id' --output text)
          for id in $RUNS; do
            [ "$id" != None ] || continue
            echo "Spark のジョブ $id を止める"
            aws emr-serverless cancel-job-run --region "$REGION" --application-id "$APP_ID" --job-run-id "$id" >/dev/null
          done
          LEFT=""
          for i in $(seq 1 36); do  # 止まるまで最大 3 分
            LEFT=$(aws emr-serverless list-job-runs --region "$REGION" --application-id "$APP_ID" \
              --states SUBMITTED PENDING SCHEDULED RUNNING QUEUED CANCELLING --query 'jobRuns[].id' --output text)
            [ "$LEFT" != None ] || LEFT=""
            [ -n "$LEFT" ] || break
            sleep 5
          done
          [ -z "$LEFT" ] || die "アプリの設定を変える前に止めた Spark のジョブ（$LEFT）が 3 分たっても止まらない。$(tf pipeline/analytics output -raw list_job_runs_command) で見て、止まってから打ち直す"
          for i in $(seq 1 36); do  # STOPPED になるまで最大 3 分（STARTING から STARTED になったものにも stop-application を打ち直す）
            APP_STATE=$(aws emr-serverless get-application --region "$REGION" --application-id "$APP_ID" --query application.state --output text)
            case "$APP_STATE" in
              STOPPED | CREATED) break ;;
              STARTED) aws emr-serverless stop-application --region "$REGION" --application-id "$APP_ID" >/dev/null 2>&1 || true ;;
            esac
            sleep 5
          done
          case "$APP_STATE" in
            STOPPED | CREATED) echo "アプリ（$APP_ID）を止めた" ;;
            *) die "EMR Serverless のアプリ（$APP_ID）が 3 分たっても止まらない（$APP_STATE）。aws emr-serverless get-application --region $REGION --application-id $APP_ID で STOPPED になってから打ち直す" ;;
          esac
          ;;
      esac
    fi
  fi
  tf_apply pipeline/analytics "${ANALYTICS_VARS[@]}" -var "http_send=$HTTP_SEND"   # http_send（driver / executor）を変えるとジョブの引数が変わり、7-5 で起こし直す
  APP_ID=$(tf pipeline/analytics output -raw application_id); echo "APP_ID=$APP_ID"
  if [ -n "$SPLUNK_ON_ECS" ]; then
    # Spark のジョブは起動してすぐ HEC に送るので、Splunk が受けられるようになってから起こす（初回の起動は設定の展開で 5〜10 分）。
    # タスクのヘルスチェック（/sbin/checkstate.sh）が HEALTHY になるのを待つ
    log "7-4b. Splunk（ECS）が起動するのを待つ（最大 20 分）"
    AN_CLUSTER=$(tf pipeline/analytics output -raw analytics_cluster_name); SP_SERVICE=$(tf pipeline/analytics output -raw splunk_service_name)
    SP_HEALTH=""
    for i in $(seq 1 80); do
      SP_TASK=$(aws ecs list-tasks --region "$REGION" --cluster "$AN_CLUSTER" --service-name "$SP_SERVICE" --desired-status RUNNING \
        --query 'taskArns[0]' --output text 2>/dev/null || echo "")
      if [ -n "$SP_TASK" ] && [ "$SP_TASK" != None ]; then
        SP_HEALTH=$(aws ecs describe-tasks --region "$REGION" --cluster "$AN_CLUSTER" --tasks "$SP_TASK" \
          --query 'tasks[0].healthStatus' --output text 2>/dev/null || echo "")
        [ "$SP_HEALTH" = HEALTHY ] && break
      fi
      sleep 15
    done
    if [ "$SP_HEALTH" = HEALTHY ]; then
      echo "Splunk は起動した"
    else
      printf '\033[1;33m%s\033[0m\n' "Splunk が 20 分たっても HEALTHY にならない（いまは「${SP_HEALTH:-タスク無し}」）。ロググループ /ecs/$PREFIX-splunk を見る。Spark のジョブはこのまま起こす（届かない間の行は HEC への送信で失敗し、ジョブの再試行に任せる）"
    fi
  fi
  # ジョブのキー（terraform の output job_driver_json_<キー>。spark_jobs のキー）から EMR Serverless のジョブ名を引く。ジョブ名はここでだけ決める。
  # 名前はジョブが書く格納先で付ける（キーと checkpoint の <checkpoint>/iceberg/ などは変えない。変えると Kafka を読み直す）
  job_name() {
    case "$1" in
      iceberg) echo sinks-s3iceberg ;;  # S3 Tables（Iceberg）
      splunk) echo sinks-splunk ;;      # ECS の Splunk の HEC
      http) echo sinks-grafana ;;       # Prometheus と OpenSearch（Grafana が読む 2 つ。STORES の grafana と同じ）
      *) die "job_name: 知らないジョブのキー $1" ;;
    esac
  }
  log "7-5. Spark のストリーミングジョブを格納先ごとに起こす（$(job_name iceberg) / $(job_name splunk) / $(job_name http)。同じスクリプトと引数で動いていれば何もしない）"
  JOB_OVERRIDES=$(tf pipeline/analytics output -raw configuration_overrides_json)
  # スクリプトと引数（格納先・checkpoint など）のハッシュをジョブのタグ SpecHash に付けておき、動いているジョブと違えば
  # 止めて起こし直す。STREAMING のジョブは起動したときの引数のまま動き続けるので、比べないと格納先を変えても古い引数のまま
  # （checkpoint から続きを読むので、止めて起こし直してもデータは落ちない）。ジョブごとに比べ、変わったジョブだけ起こし直す
  job_spec() {
    "${PY[@]}" -c 'import hashlib, sys; h = hashlib.sha256(open(sys.argv[1], "rb").read()); [h.update(a.encode()) for a in sys.argv[2:]]; print(h.hexdigest()[:16])' \
      "$SPARK_SCRIPT" "$1" "$JOB_OVERRIDES"
  }
  # 動いている（起動中を含む）ジョブを「名前 id」の行で持ち、名前で id を引く
  ACTIVE=$(aws emr-serverless list-job-runs --region "$REGION" --application-id "$APP_ID" \
    --states SUBMITTED PENDING SCHEDULED RUNNING QUEUED --query 'jobRuns[].[name,id]' --output text)
  runs_named() { printf '%s\n' "$ACTIVE" | awk -v n="$1" '$1 == n {printf "%s ", $2}'; }
  STOP=""; START=""
  # 古い名前のジョブ。2026-10-04 に 1 つのジョブ snmp-sinks（全部の格納先へ書いていた）を格納先ごとの snmp-sinks-<キー> に分け、
  # 同じ日に SNMP 以外のトピックもあるので上の job_name の名前に改めた。格納先ごとの checkpoint（<checkpoint>/iceberg/ など）はジョブの名前によらず
  # 新しいジョブがそのまま引き継ぐので、同じ checkpoint を 2 つのジョブが読み書きしないよう、止まってから新しいジョブを起こす
  # （runs_named も下の待ちも名前の完全一致なので、sinks-splunk と snmp-sinks-splunk は取り違えない）
  OLD_NAMES="snmp-sinks snmp-sinks-iceberg snmp-sinks-splunk snmp-sinks-http"
  for name in $OLD_NAMES; do
    OLD=$(runs_named "$name")
    if [ -n "$OLD" ]; then
      echo "古い名前のジョブ $name（${OLD% }）を止める。止まってから新しい名前のジョブを起こす"
      STOP="$STOP $OLD"
    fi
  done
  for JOB in iceberg splunk http; do
    JOB_DRIVER=$(tf pipeline/analytics output -raw "job_driver_json_$JOB")
    NAME=$(job_name "$JOB")
    RUNNING=$(runs_named "$NAME")
    if [ -z "$JOB_DRIVER" ]; then  # このジョブの格納先のまとまりが STORES に無い
      if [ -n "$RUNNING" ]; then
        echo "$NAME（${RUNNING% }）は格納先が無くなったので止める"
        STOP="$STOP $RUNNING"
      fi
      continue
    fi
    JOB_SPEC=$(job_spec "$JOB_DRIVER")
    printf -v "JOB_DRIVER_$JOB" '%s' "$JOB_DRIVER"; printf -v "JOB_SPEC_$JOB" '%s' "$JOB_SPEC"
    KEEP=""; STALE=""
    for id in $RUNNING; do  # SpecHash が同じものを 1 つだけ残す（同じ名前の 2 つ目は同じ checkpoint を使うので止める）
      spec=$(aws emr-serverless get-job-run --region "$REGION" --application-id "$APP_ID" --job-run-id "$id" --query 'jobRun.tags.SpecHash' --output text 2>/dev/null || echo "")
      if [ -z "$KEEP" ] && [ "$spec" = "$JOB_SPEC" ]; then KEEP="$id"; else STALE="$STALE $id"; fi
    done
    if [ -n "$STALE" ]; then
      echo "$NAME（${STALE# }）はスクリプトか引数が違う（今は SpecHash=$JOB_SPEC）か 2 つ目なので止める"
      STOP="$STOP $STALE"
    fi
    if [ -n "$KEEP" ]; then
      echo "$NAME は同じスクリプトと引数で動いている（${KEEP}。SpecHash=$JOB_SPEC）"
    else
      START="$START $JOB"
    fi
  done
  for id in $STOP; do
    aws emr-serverless cancel-job-run --region "$REGION" --application-id "$APP_ID" --job-run-id "$id" >/dev/null
  done
  if [ -n "$START" ]; then
    # 起こすジョブと古い名前のジョブが止まるまで最大 3 分待つ（止めている途中の CANCELLING も待つ。前の up.sh が待ちきれずに終わった分も含む）。
    # 起こすジョブが無ければ待たない（同じ checkpoint を新しく読み始めるジョブが無い）
    WAIT_NAMES=" $OLD_NAMES "
    for JOB in $START; do WAIT_NAMES="${WAIT_NAMES}$(job_name "$JOB") "; done
    LEFT=""
    for i in $(seq 1 36); do
      LEFT=$(aws emr-serverless list-job-runs --region "$REGION" --application-id "$APP_ID" \
        --states SUBMITTED PENDING SCHEDULED RUNNING QUEUED CANCELLING --query 'jobRuns[].[name,id]' --output text \
        | awk -v names="$WAIT_NAMES" 'index(names, " " $1 " ") {printf "%s ", $2}')
      [ -n "$LEFT" ] || break
      sleep 5
    done
    [ -z "$LEFT" ] || die "Spark のジョブ（${LEFT% }）が 3 分たっても止まらない。$(tf pipeline/analytics output -raw list_job_runs_command) で見て、止まってから打ち直す"
    RUNTIME_ROLE=$(tf pipeline/analytics output -raw runtime_role_arn)
    for JOB in $START; do
      v="JOB_DRIVER_$JOB"; JOB_DRIVER="${!v}"; v="JOB_SPEC_$JOB"; JOB_SPEC="${!v}"; NAME=$(job_name "$JOB")
      JOB_RUN_ID=$(aws emr-serverless start-job-run --region "$REGION" --application-id "$APP_ID" \
        --execution-role-arn "$RUNTIME_ROLE" \
        --name "$NAME" --mode STREAMING \
        --job-driver "$JOB_DRIVER" \
        --configuration-overrides "$JOB_OVERRIDES" \
        --tags "Project=$PREFIX,owner=$OWNER,SpecHash=$JOB_SPEC" \
        --query jobRunId --output text)
      echo "$NAME: JOB_RUN_ID=$JOB_RUN_ID"
    done
    echo "起動に 2〜5 分。様子は: $(tf pipeline/analytics output -raw list_job_runs_command)"
  fi
fi

# ---- 8-3. Web ---------------------------------------------------------------------
if [ -z "$SKIP_STREAM" ] || [ -z "$SKIP_GRAPH" ] || [ -n "$NAUTOBOT" ]; then
  log "8-3. Web を再起動する（起動時に SSM から Neptune のグラフの ID と Nautobot の有無を読むため）"
  run_on_instance "$INSTANCE_ID" "systemctl restart $PREFIX-web.service; $WEB_ACTIVE"
  echo "Web が動いている"
fi

# ---- 8-5. workflow（機能 WORKFLOW）--------------------------------------------------------
if [ -n "$WORKFLOW" ]; then
  log "8-5. workflow（terraform/workflow。Temporal のワーカーと AgentCore Gateway。数分）"
  tf_apply workflow -var "worker_image_tag=$IMAGE_TAG" -var "lambda_az_num=$LAMBDA_AZ_NUM"
  WF_CLUSTER=$(tf workflow output -raw cluster_name); WF_SERVICE=$(tf workflow output -raw service_name)
  echo "ECS のサービスが安定するのを待つ（イメージの取得と Temporal の起動。1〜3 分）"
  aws ecs wait services-stable --region "$REGION" --cluster "$WF_CLUSTER" --services "$WF_SERVICE"
  WF_TASK=$(aws ecs list-tasks --region "$REGION" --cluster "$WF_CLUSTER" --service-name "$WF_SERVICE" --query 'taskArns[0]' --output text)
  WF_TASK_IP=$(aws ecs describe-tasks --region "$REGION" --cluster "$WF_CLUSTER" --tasks "$WF_TASK" \
    --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value | [0]' --output text)
  echo "WF_TASK=${WF_TASK##*/} WF_TASK_IP=$WF_TASK_IP"
  echo "ワーカーのログ: $(tf workflow output -raw worker_logs_command)"
  echo "Temporal の UI（Web の EC2 経由でタスクの 8233 へ。PC の http://localhost:8233/ ）:"
  echo "  aws ssm start-session --region $REGION --target $INSTANCE_ID --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{\"host\":[\"$WF_TASK_IP\"],\"portNumber\":[\"8233\"],\"localPortNumber\":[\"8233\"]}'"
  log "8-6. Web を再起動する（Neptune の接続先を SSM から読み直すため。エージェントは Gateway を 5 分以内に拾う）"
  run_on_instance "$INSTANCE_ID" "systemctl restart $PREFIX-web.service; $WEB_ACTIVE"
  echo "Web が動いている"
fi

# ---- 9. Runtime のロググループ -------------------------------------------------------
# AgentCore が最初の呼び出しで作るもので、Terraform の管理外。保持期間とタグだけ付け、ops/down.sh が消す
if [ -n "$AGENT" ]; then
  log "9. ロググループ ${LOG_GROUP}（保持 7 日 + タグ）"
  if ! aws logs put-retention-policy --region "$REGION" --log-group-name "$LOG_GROUP" --retention-in-days 7 2>/dev/null; then
    aws logs create-log-group --region "$REGION" --log-group-name "$LOG_GROUP"
    aws logs put-retention-policy --region "$REGION" --log-group-name "$LOG_GROUP" --retention-in-days 7
  fi
  aws logs tag-resource --region "$REGION" \
    --resource-arn "arn:aws:logs:$REGION:$ACCOUNT_ID:log-group:$LOG_GROUP" \
    --tags "Project=$PREFIX,owner=$OWNER"
fi

# ---- 10. ポートフォワーディング -------------------------------------------------------------
log "できた（${ROOTS}）。利用者に配るコマンド:"
tf base/core output -raw start_session_command; echo
if [ -n "$LAB_INSTANCE_ID" ]; then
  echo "lab に入るコマンド:"
  tf pipeline/lab output -raw start_session_command; echo
fi
if [ -z "$SKIP_STREAM" ]; then
  echo "Telegraf（ECS の取りにいく側）に入るコマンド（TASK_ID は下の 1 行目で出る ARN の最後。中で tg gnmi。SNMP_POLL=1 なら tg test でポーリングも見られる）:"
  tf pipeline/stream output -raw telegraf_dialin_list_tasks_command; echo
  tf pipeline/stream output -raw telegraf_exec_command; echo
fi
if [ -n "$GRAFANA" ]; then
  echo "Grafana（http://localhost:3000/ 。ユーザー admin）を開くポートフォワード（web の EC2 を踏み台にする）と admin のパスワード:"
  tf pipeline/analytics output -raw grafana_port_forward_command; echo
  tf pipeline/analytics output -raw grafana_password_command; echo
fi
if [ -n "$SPLUNK_ON_ECS" ]; then
  echo "Splunk（http://localhost:8000/ 。ユーザー admin）を開くポートフォワード（web の EC2 を踏み台にする）と admin のパスワード:"
  tf pipeline/analytics output -raw splunk_port_forward_command; echo
  tf pipeline/analytics output -raw splunk_password_command; echo
fi
if [ -n "$NAUTOBOT" ]; then
  echo "Nautobot（http://localhost:8081/ 。ユーザー admin）を開くポートフォワード（web の EC2 を踏み台にする）と admin のパスワード:"
  tf pipeline/nautobot output -raw port_forward_command; echo
  tf pipeline/nautobot output -raw password_command; echo
fi
if [ -n "$LAB_WARN" ]; then printf '\033[1;33m%s\033[0m\n' "$LAB_WARN"; fi
if [ -n "$NAUTOBOT_WARN" ]; then printf '\033[1;33m%s\033[0m\n' "$NAUTOBOT_WARN"; fi
printf '\033[1;33m%s\033[0m\n' "$COST_NOTE"
if [ -n "$NO_DASHBOARD_PORTFORWARD" ]; then exit 0; fi
log "10. ポートフォワーディング（http://localhost:$LOCAL_PORT/ 。Ctrl+C で閉じる）"
trap - EXIT
if [ -n "$TF_AWS_CONFIG" ]; then rm -f "$TF_AWS_CONFIG"; fi
exec aws ssm start-session --region "$REGION" --target "$INSTANCE_ID" \
  --document-name AWS-StartPortForwardingSession \
  --parameters "{\"portNumber\":[\"8080\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}"
