# ops/up.sh と OSS 版（005）の ops/oss/up.sh が読む共通の関数（terraform の apply、Session Manager でのコマンド、Web の wheel、SSM のシークレット、
# Glue の s3tablescatalog、Splunk のイメージと SSM のパラメータ、Agent・worker・Temporal・Nautobot・syslog-ng のイメージと Nautobot の SSM のパラメータ、
# マネージド版の MSK の SCRAM の secret と KMS の鍵、Grafana のアラートルールの評価の確かめ）。ops/check-grafana.sh も Grafana のルールの確かめのために読む。
# 先に ops/common.sh と ops/deploy-env.sh を読む（log / die / tf / tf_logged を使う）。Splunk のイメージは ops/lab-common.sh の dir_tag / ecr_has を使う。
# REGION / PY / PREFIX / OWNER（s3tablescatalog は ACCOUNT_ID も）は呼ぶ前に決める。
# 環境変数 SSM_RUN_WAIT（秒。既定 1800）は ssm_run が SSM Run Command の結果を待つ長さ（deploy.env のキーではない。docs/deploy.md）。読んだときに確かめる
SSM_RUN_WAIT=${SSM_RUN_WAIT:-1800}
[[ "$SSM_RUN_WAIT" =~ ^[1-9][0-9]*$ ]] || die "SSM_RUN_WAIT は SSM Run Command の結果を待つ秒数（1 以上の整数。既定 1800）。いまは「${SSM_RUN_WAIT}」"
tf_init() {  # tf_init <ルート>
  tf_init_root "$1"  # ops/common.sh。OSS 版は -lockfile=readonly が付く
}
tf_apply_only() {  # tf_apply_only <ルート> [-var 名前=値 …]  init 済みのルートを apply する
  local root="$1"; shift
  tf_logged "$root" apply -input=false -auto-approve -var "owner=$OWNER" "$@" \
    || die "$TF_DIR/$root の apply に失敗した（上のエラー。全文は $(tf_log_file "$root" apply)。docs/troubleshooting.md の「うまくいかないとき」。直したらもう一度 $OPS_DIR/up.sh）"
}
tf_apply() {  # tf_apply <ルート> [-var 名前=値 …]
  tf_init "$1"
  tf_apply_only "$@"
}
has_resources() {  # has_resources <ルート>  state があり、リソースが 1 つ以上載っている（init 済みが前提）
  [ -f "$TF_DIR/$1/terraform.tfstate" ] && [ -n "$(tf "$1" state list 2>/dev/null)" ]
}
tf_output() {  # tf_output <ルート> <出力名>  出力を 1 つ読んで出す。読めない・空なら die（赤い NG: の行）。$( ) の中の die はサブシェルだけを抜けるので、
  # 止めるかどうかは呼ぶ側が決める（止めるなら X=$(tf_output …) || exit 1、止めないなら if X=$(tf_output …); then …）
  local v
  v=$(tf "$1" output -raw "$2") || die "$TF_DIR/$1 の出力 $2 が読めない（上のエラー）"
  [ -n "$v" ] || die "$TF_DIR/$1 の出力 $2 が空"
  printf '%s' "$v"
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
ssm_run() {  # ssm_run <インスタンス ID> <コマンド…>  cloud-init（user_data）が終わるのを待ってから打ち、標準出力を出す。失敗なら 1、SSM_RUN_WAIT 秒たっても結果が分からなければ 2
  # コマンドは JSON の文字列に埋めるので、ダブルクォートとバックスラッシュを含めない
  local id="$1"; shift
  local cmd_id status deadline
  # 送れなければ 1 を返す（if や $( ) の中で呼ばれると set -e が効かず、空の cmd_id で下の Pending を待ち続ける。grafana_rules_step がそう呼ぶ）
  cmd_id=$(aws ssm send-command --region "$REGION" --instance-ids "$id" \
    --document-name AWS-RunShellScript --timeout-seconds 900 \
    --parameters "{\"commands\":[\"cloud-init status --wait >/dev/null || true\",\"$*\"]}" \
    --query Command.CommandId --output text) || { echo "SSM Run Command を送れなかった（上のエラー）" >&2; return 1; }
  # 締め切りで返しても、インスタンスの上のコマンドは止めない（cancel-command は打たない。案内する get-command-invocation で結果を見る）
  deadline=$((SECONDS + SSM_RUN_WAIT))
  while :; do
    # 送った直後は get-command-invocation がまだ失敗することがある。そのあいだは「読めない」として読み直す
    status=$(aws ssm get-command-invocation --region "$REGION" --command-id "$cmd_id" --instance-id "$id" \
      --query Status --output text 2>/dev/null || echo 読めない)
    case "$status" in
      Success)
        aws ssm get-command-invocation --region "$REGION" --command-id "$cmd_id" --instance-id "$id" \
          --query StandardOutputContent --output text | sed '/^$/d'
        return 0 ;;
      Pending|InProgress|Delayed|読めない)
        if [ "$SECONDS" -ge "$deadline" ]; then
          echo "SSM Run Command（${cmd_id}）の結果が $SSM_RUN_WAIT 秒たっても分からない（最後の状態: ${status}）。あとで見るのは aws ssm get-command-invocation --region $REGION --command-id $cmd_id --instance-id ${id}（待つ秒数は SSM_RUN_WAIT）" >&2
          return 2
        fi
        sleep 10 ;;
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
AZ_NUM_SET=""   # deploy.env か環境変数に書いてあった *_AZ_NUM（ENDPOINTS_AZ_NUM と比べる）
# Web の EC2 が入れる wheel（arm64 / cp313）。EC2 はインターネットに出ないので PC で取って S3 に置き、user_data が pip install --no-index で入れる
# （IaC/terraform/aws-managed/base/core の web_user_data.sh.tftpl）。requirements（-r で読み込まれる側も）と pip の引数 WHEEL_ARGS のハッシュを <置き場>/.requirements.sha256 に残し、
# 同じで .whl があれば取り直さない。違えば置き場を消して取り直す（.whl が 1 つでもあれば飛ばすと、版を上げても古い wheel のまま進む。005 のレビュー Nit 5）。
# 置き場を S3 に上げるときは --delete と --exclude .requirements.sha256 を付ける（古い版を EC2 に残さない）。PY を使う
WHEEL_ARGS=(--only-binary=:all: --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 --platform manylinux_2_28_aarch64
  --python-version 3.13 --implementation cp --abi cp313 --abi none)
fetch_wheels() {  # fetch_wheels <置き場> <pip に -r で渡す requirements> [その中から -r で読まれる requirements …]
  local dir=$1 req=$2 sha pip
  sha=$( { cat "${@:2}"; echo "${WHEEL_ARGS[*]}"; } | "${PY[@]}" -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')
  if [ -n "$(ls "$dir"/*.whl 2>/dev/null)" ] && [ "$(cat "$dir/.requirements.sha256" 2>/dev/null)" = "$sha" ]; then
    echo "$dir/ に $(ls "$dir"/*.whl | wc -l | tr -d ' ') 個ある（${*:2} は変わっていない）"
    return 0
  fi
  if command -v uv >/dev/null; then pip="uv run --python 3.13 --with pip python -m pip"; else pip="python3 -m pip"; fi
  rm -rf "$dir"
  $pip download "${WHEEL_ARGS[@]}" -d "$dir" -r "$req" || die "Web の wheel（${req}）を $dir/ に取れなかった"
  printf '%s\n' "$sha" > "$dir/.requirements.sha256"
}
az_num() {  # az_num <キー> <既定> <最小> <最大> <範囲の理由>  書いてなければ既定。範囲の外なら止める
  local k=$1 v="${!1:-}"
  if [ -n "$v" ]; then AZ_NUM_SET="$AZ_NUM_SET $k"; else v=$2; fi
  case "$v" in *[!0-9]* | '') die "$k は $3〜$4 の数で書く（いまは $k=${v}）。まだ何も作っていない" ;; esac
  v=$((10#$v))
  if [ "$v" -lt "$3" ] || [ "$v" -gt "$4" ]; then die "$k=$v は書けない。$3〜$4 で書く（$5）。まだ何も作っていない"; fi
  printf -v "$k" '%s' "$v"
}
SECRET_INPUT=""  # ensure_secret / ensure_fixed_secret が値を書く一時ファイル。put-parameter の最中に止まっても ops/up.sh と ops/oss/up.sh の EXIT の trap（on_exit）が消す
# ensure_secret <SSM のパラメータ名> <password|strong-password|uuid|token|kafka-cluster-id> <説明>  無ければ乱数の SecureString を作る。値は画面にもログにも出さない
#   strong-password は password と同じ 32 文字に、大文字・小文字・数字・記号（- か _）が 1 つ以上ずつ入るまで引き直したもの
#   （OSS 版の OpenSearch の OPENSEARCH_INITIAL_ADMIN_PASSWORD。2.12 から弱いパスワードでは起動しない）。
#   kafka-cluster-id は KRaft の CLUSTER_ID（OSS 版の Kafka。kafka-storage.sh random-uuid と同じ形 = 16 バイトの乱数の base64url で 22 文字。
#   Kafka の Uuid.randomUuid と同じく、先頭が「-」のものは引き直す）
ensure_secret() {
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
  # 標準入力（file:///dev/stdin）は AWS CLI v2 が読めず Invalid JSON になる。down.sh は ManagedBy のタグで見分けて消す
  local rc=0
  SECRET_INPUT=$(umask 077; mktemp "${TMPDIR:-/tmp}/nwc-secret.XXXXXX") || die "一時ファイルを作れなかった"
  "${PY[@]}" -c 'import base64, json, secrets, sys, uuid
name, kind, desc, prefix, owner, managed_by, path = sys.argv[1:]
def kafka_cluster_id():
    while True:
        v = base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b"=").decode()
        if not v.startswith("-"):
            return v
def strong_password():
    while True:
        v = secrets.token_urlsafe(24)
        if any(c.isupper() for c in v) and any(c.islower() for c in v) and any(c.isdigit() for c in v) and any(c in "-_" for c in v):
            return v
value = (str(uuid.uuid4()) if kind == "uuid" else secrets.token_hex(20) if kind == "token"  # token = Nautobot の API トークン（40 桁の 16 進）
         else kafka_cluster_id() if kind == "kafka-cluster-id" else strong_password() if kind == "strong-password"
         else secrets.token_urlsafe(24))
with open(path, "w", encoding="utf-8") as f:
    json.dump({"Name": name, "Type": "SecureString", "Value": value, "Description": desc,
               "Tags": [{"Key": "ManagedBy", "Value": managed_by}, {"Key": "Project", "Value": prefix}, {"Key": "owner", "Value": owner}]}, f)' \
    "$name" "$kind" "$desc" "$PREFIX" "$OWNER" "$OPS_DIR/up.sh" "$SECRET_INPUT" \
    && aws ssm put-parameter --region "$REGION" --cli-input-json "file://$SECRET_INPUT" >/dev/null || rc=$?
  rm -f -- "${SECRET_INPUT:?}"; SECRET_INPUT=""
  [ "$rc" -eq 0 ] || die "SSM に $name を作れなかった（上のエラー）"
  echo "$name を作った（値は出さない。見るコマンドは最後に出る）"
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
  local rc=0
  SECRET_INPUT=$(umask 077; mktemp "${TMPDIR:-/tmp}/nwc-secret.XXXXXX") || die "一時ファイルを作れなかった"
  FIXED_SECRET_VALUE="$value" "${PY[@]}" -c 'import json, os, sys
name, desc, prefix, owner, managed_by, path = sys.argv[1:]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"Name": name, "Type": "SecureString", "Value": os.environ["FIXED_SECRET_VALUE"], "Description": desc,
               "Tags": [{"Key": "ManagedBy", "Value": managed_by}, {"Key": "Project", "Value": prefix}, {"Key": "owner", "Value": owner}]}, f)' \
    "$name" "$desc" "$PREFIX" "$OWNER" "$OPS_DIR/up.sh" "$SECRET_INPUT" \
    && aws ssm put-parameter --region "$REGION" --cli-input-json "file://$SECRET_INPUT" >/dev/null || rc=$?
  rm -f -- "${SECRET_INPUT:?}"; SECRET_INPUT=""
  [ "$rc" -eq 0 ] || die "SSM に $name を作れなかった（上のエラー）"
  echo "$name を作った（値は出さない）"
}
# アラートの通知の履歴（Firehose → S3 Tables の alert_events、Athena で読む）は、Glue の S3 Tables 連携のカタログ s3tablescatalog を通る。
# アカウントとリージョンに 1 つで、ほかの OWNER の環境（と、マネージド版と OSS 版）で共有するので、無いときだけ作り、$OPS_DIR/down.sh では消さない（消し方は docs/deploy.md）
S3TABLES_CATALOG_INPUT='{"FederatedCatalog": {"Identifier": "arn:aws:s3tables:__REGION__:__ACCOUNT__:bucket/*", "ConnectionName": "aws:s3tables"},
 "CreateDatabaseDefaultPermissions": [{"Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"}, "Permissions": ["ALL"]}],
 "CreateTableDefaultPermissions": [{"Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"}, "Permissions": ["ALL"]}],
 "AllowFullTableExternalDataAccess": "True"}'
ensure_s3tables_catalog() {  # 無ければ作る。あれば設定が想定（IAM だけで読み書きできる）と違うときに警告だけ出す。REGION と ACCOUNT_ID を使う
  local out conn ext perms
  if ! out=$(aws glue get-catalog --region "$REGION" --catalog-id s3tablescatalog --query Catalog.Name --output text 2>&1); then
    case "$out" in *EntityNotFoundException*) ;; *) die "Glue のカタログ s3tablescatalog を確かめられない: $out" ;; esac
    echo "Glue のカタログ s3tablescatalog を作る（S3 Tables 連携。アカウントとリージョンで共有し、$OPS_DIR/down.sh では消さない）"
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
# Splunk Enterprise（analytics の ECS。IaC/terraform/aws-managed/pipeline/analytics の splunk.tf）。マネージド版と OSS 版（設計 005: Splunk は OSS 版でも変えない）が
# 同じイメージの作り方と同じ SSM のパラメータを使う。SPLUNK_VERSION は docker/images/splunk/Dockerfile の ARG の既定値に合わせてある
# （変えるときは両方を変える。tests/check_splunk_image.py で、その版の Python の boto3 でアラートを送れるかも確かめる）
SPLUNK_VERSION=10.4.4   # splunk/splunk は amd64 だけ（ECS のタスクは X86_64）
splunk_image_check() {  # SPLUNK_TAG を app/splunk/ の中身と docker/images/splunk/Dockerfile から作り、ECR の <接頭辞>-splunk に無ければ NEED_SPLUNK=1。PREFIX を使う
  SPLUNK_TAG=$(dir_tag "$SPLUNK_VERSION" app/splunk docker/images/splunk/Dockerfile) || die "app/splunk/ のタグを作れなかった"
  if ecr_has "$PREFIX-splunk" "$SPLUNK_TAG"; then echo "splunk:$SPLUNK_TAG はある"; else NEED_SPLUNK=1; fi
}
build_splunk() {  # docker login 済みで呼ぶ。REG / PREFIX / SPLUNK_TAG（splunk_image_check）を使う
  # Splunk Enterprise の公式イメージ（amd64 だけ。約 2〜3 GB）に検知のアプリ（app/splunk/nwc_alerts）を足す。
  # Fargate は VPC の中から ECR しか引けず、タスクは Splunkbase にも出られないので、アプリはビルドのときに入れる
  # （COPY だけなので、arm64 の PC（Apple シリコン）でもエミュレーション無しで作れる）
  docker buildx build --platform linux/amd64 --build-arg "SPLUNK_VERSION=$SPLUNK_VERSION" -t "$REG/$PREFIX-splunk:$SPLUNK_TAG" --push -f docker/images/splunk/Dockerfile app/splunk/
}
ensure_splunk_secrets() {  # ensure_splunk_secrets <SPLUNK_AZ_NUM>  analytics の apply より前に呼ぶ。値は出さない
  # 管理者のパスワードと HEC の token は SSM に乱数で作る（token は Splunk が GUID の形を求める）。
  # Splunk のタスクが起動時に読んで設定し、Spark（マネージド版は EMR Serverless のジョブ、OSS 版は ECS のタスク）も同じ token を読む
  ensure_secret "/$PREFIX/splunk/admin-password" password "Splunk admin password (created by $OPS_DIR/up.sh)"
  ensure_secret "/$PREFIX/splunk/hec-token" uuid "Splunk HEC token (created by $OPS_DIR/up.sh)"
  # クラスター（SPLUNK_AZ_NUM が 2 か 3）は、manager と indexer と search head が互いを確かめる合言葉（pass4SymmKey）も作る
  if [ "$1" -gt 1 ]; then ensure_secret "/$PREFIX/splunk/idxc-secret" password "Splunk indexer cluster key (created by $OPS_DIR/up.sh)"; fi
}
# Splunk のクラスター（SPLUNK_AZ_NUM が 2 か 3）は、全タスクの HEALTHY のあとに 2 つ見る。引数はサービス（search head、manager、indexer の順。SP_SERVICES）。
# REGION / AN_CLUSTER（analytics の ECS のクラスター）/ PREFIX / SPLUNK_AZ_NUM を使う。マネージド版と OSS 版（005）が同じものを呼ぶ。
# 1. indexer の AZ。AZ に 1 台ずつは Fargate の振り分けに任せている（保証ではない）ので、同じ AZ に 2 台いたら注意だけ出す。
# 2. search head の突き合わせ（app/splunk/peers_check.py）の判定。判定が変わるたびに PID 1 の stdout に書く行「nwc-peer-check state=… reason=…」を、
#    いまの search head のタスクのログストリーム（splunk/splunk/<タスク ID>）から読む。state=ok で Up の peer が indexer の数になるまで待ち
#    （最大 6 分）、ならなければ止まる。行が 1 つも無いのも成功にしない
splunk_cluster_check() {
  local azs sh_task="" line="" i
  azs=$(aws ecs describe-tasks --region "$REGION" --cluster "$AN_CLUSTER" --query 'tasks[].availabilityZone' --output text --tasks \
    $(aws ecs list-tasks --region "$REGION" --cluster "$AN_CLUSTER" --service-name "$3" --desired-status RUNNING --query 'taskArns' --output text) 2>/dev/null || true)
  azs=$(echo $azs | tr ' ' '\n' | sort)
  if [ -n "$(echo "$azs" | uniq -d)" ]; then
    printf '\033[1;33m%s\033[0m\n' "注意: indexer のタスクが同じ AZ に 2 台いる（$(echo $azs)）。その AZ が落ちると、その 2 台にある複製が一緒に無くなる。止めずに進む（AZ に 1 台ずつは Fargate の振り分けに任せていて、保証ではない）"
  fi
  for i in $(seq 1 24); do
    sh_task=$(aws ecs list-tasks --region "$REGION" --cluster "$AN_CLUSTER" --service-name "$1" --desired-status RUNNING --query 'taskArns[0]' --output text 2>/dev/null || true)
    line=$(aws logs filter-log-events --region "$REGION" --log-group-name "/ecs/$PREFIX-splunk" --log-stream-names "splunk/splunk/${sh_task##*/}" \
      --filter-pattern '"nwc-peer-check"' --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n' | grep '^nwc-peer-check ' | tail -n 1 || true)
    case "$line" in
      "nwc-peer-check state=ok reason=peers_up:"*)
        if [ "${line##*:}" -ge "$SPLUNK_AZ_NUM" ]; then echo "search head は indexer を全部（${SPLUNK_AZ_NUM} 台）同じ GUID で検索できる（${line}）"; return 0; fi ;;
    esac
    sleep 15
  done
  [ -n "$line" ] || die "search head のタスク（${sh_task##*/}）は、突き合わせ（app/splunk/peers_check.py）をまだ 1 回もしていない（6 分待っても判定の行「nwc-peer-check …」がロググループ /ecs/$PREFIX-splunk の splunk/splunk/${sh_task##*/} に無い）。search head が入れ替わったばかりなら、HEALTHY になってから打ち直す"
  die "search head の突き合わせ（app/splunk/peers_check.py）が 6 分たっても ok（Up の indexer が ${SPLUNK_AZ_NUM} 台）にならない。最新の判定は「${line}」（degraded: manager が Up と言う indexer が足りない。reason=peers_up:<Up の数>/<あるはずの数>。mismatch: search head が古い GUID の indexer を持っている。続けば ECS が search head を入れ替える。skip: manager に聞けない。error: search head の peers を読めない）。ロググループ /ecs/$PREFIX-splunk を見る"
}
# Agent（Runtime）・worker・Temporal・Nautobot と Redis のイメージ。マネージド版と OSS 版（005）が同じ作り方をする。どれも docker login 済みで呼び、REG / PREFIX を使う。
# NAUTOBOT_VERSION は docker/images/nautobot/Dockerfile の ARG、REDIS_TAG は IaC/terraform/aws-managed/pipeline/nautobot の redis_image_tag、
# TEMPORAL_TAG は IaC/terraform/aws-managed/workflow の temporal_image_tag の既定値に合わせてある（変えるときは両方を変える）
NAUTOBOT_VERSION=3.2.6
GRAFANA_VERSION=13.2.3   # docker/images/grafana/Dockerfile の ARG の既定値に合わせてある（変えるときは両方を変える）
REDIS_TAG=8.10.2-alpine   # 8 系は AGPLv3 も選べる（7.4 は RSALv2 / SSPL だけ）。公式のイメージは Search・JSON などのモジュールを読み込んで起きる
TEMPORAL_TAG=1.9.1
# 機器の syslog と NetFlow / sFlow の受け口（cycle 012）。SYSLOG_NG_VERSION は docker/images/syslog-ng/Dockerfile の ARG の既定値、GOFLOW2_TAG は netsampler/goflow2 のタグ。
# docker/compose/compose.yaml も同じ値（tests/test_local_compose.py が照合する。変えるときは全部を変える）。どちらも arm64 のイメージがあることを確かめてある
SYSLOG_NG_VERSION=4.29.0
GOFLOW2_TAG=v2.2.7
# 機器の gNMI の購読（cycle 013）。docker/images/gnmic/Dockerfile の ARG の既定値と docker/compose/compose.yaml のタグに合わせてある（tests/test_local_compose.py が照合する）。
# 公式イメージ ghcr.io/openconfig/gnmic に arm64 があることを確かめてある
GNMIC_VERSION=0.49.0
nautobot_context() {  # nautobot_context <空のディレクトリ>  Nautobot のイメージのビルドの context を集める（docker/images/nautobot/Dockerfile の頭の説明）
  # app/nautobot/ の中身に、グラフへ openCypher で書く app/agentcore/graph.py と app/agentcore/toolkit.py、最初の seed にする lab の定義を足す。
  # タグはこのディレクトリの中身と docker/images/nautobot/Dockerfile から作る（dir_tag）ので、graph.py や lab の定義や Dockerfile を変えてもイメージが作り直される
  cp -R app/nautobot/. "$1/" && cp app/agentcore/graph.py app/agentcore/toolkit.py "$1/" || return 1
  find "$1" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
  find "$1" -name .DS_Store -delete 2>/dev/null
  "${PY[@]}" app/containerlab/lab_topology.py app/containerlab >"$1/lab_seed.json"
}
build_agent() {  # build_agent <リポジトリの URL>:<タグ> [requirements のファイル名]  Runtime のコンテナ（arm64）。OSS 版は requirements-oss.txt（neo4j のドライバー入り）
  docker buildx build --platform linux/arm64 --build-arg "REQUIREMENTS=${2:-requirements.txt}" -t "$1" --push -f docker/images/agentcore/Dockerfile app/agentcore/
}
build_grafana() {  # REG / PREFIX / GRAFANA_TAG（dir_tag "$GRAFANA_VERSION" app/grafana docker/images/grafana/Dockerfile）を使う。Grafana OSS（arm64）
  # データソースの plugin をビルドのときに入れる（タスクは AWS の外へ出られず、起動時に grafana.com から落とせない）
  docker buildx build --platform linux/arm64 --build-arg "GRAFANA_VERSION=$GRAFANA_VERSION" -t "$REG/$PREFIX-grafana:$GRAFANA_TAG" --push -f docker/images/grafana/Dockerfile app/grafana/
}
build_gnmic() {  # REG / PREFIX / GNMIC_TAG（dir_tag "$GNMIC_VERSION" app/gnmic docker/images/gnmic/Dockerfile）を使う。公式イメージ（arm64）に設定のひな形と入口を COPY するだけ
  docker buildx build --platform linux/arm64 --build-arg "GNMIC_VERSION=$GNMIC_VERSION" -t "$REG/$PREFIX-gnmic:$GNMIC_TAG" --push -f docker/images/gnmic/Dockerfile app/gnmic/
}
build_syslog_ng() {  # REG / PREFIX / SYSLOG_NG_TAG（dir_tag "$SYSLOG_NG_VERSION" app/syslog-ng docker/images/syslog-ng/Dockerfile）を使う。AxoSyslog（arm64）に設定を COPY するだけなので、エミュレーション無しで作れる
  docker buildx build --platform linux/arm64 --build-arg "SYSLOG_NG_VERSION=$SYSLOG_NG_VERSION" -t "$REG/$PREFIX-syslog-ng:$SYSLOG_NG_TAG" --push -f docker/images/syslog-ng/Dockerfile app/syslog-ng/
}
# Grafana のアラートルールは execErrState: KeepLast なので、評価がエラーでもアラートは出ず、ルールの health も ok のまま（008 で実測）。
# エラーはルールの API の alerts[].state（「Normal (Error, KeepLast)」）にだけ出るので、Web の EC2 の上で ops/grafana_rules_check.py に読ませる
# （Web と同じ環境変数と boto3 で、SSM の admin のパスワードを読む。値は出さない）。最後の行が「判定: OK / NG / 未確認 …」。PREFIX を使う。
# ssm_run の出力（標準エラーも）を受けてから標準出力に出し、最後の「判定:」の行を GRAFANA_VERDICT に置く。返すのは ops/check-grafana.sh の終了コードと同じ
# 0（OK）/ 1（NG: 評価がエラーのルールがある）/ 2（未確認: 判定: 未確認、判定の行が無い、SSM Run Command が送れない・失敗・締め切り）。表は docs/troubleshooting.md
grafana_rules_check() {  # grafana_rules_check <Web のインスタンス ID>
  local out rc=0
  out=$(ssm_run "$1" "echo $(base64 < ops/grafana_rules_check.py | tr -d '\n') | base64 -d | NAME_PREFIX=$PREFIX /usr/bin/python3.13 -" 2>&1) || rc=$?
  printf '%s\n' "$out"
  # 失敗のときの ssm_run は標準出力と標準エラーをタブでつないで出すので、タブの手前まで
  GRAFANA_VERDICT=$(printf '%s\n' "$out" | grep -o '判定: [^[:cntrl:]]*' | tail -1 || true)
  case "$rc:$GRAFANA_VERDICT" in
    "0:判定: OK"*) return 0 ;;
    *":判定: NG"*) return 1 ;;
  esac
  return 2
}
grafana_rules_step() {  # grafana_rules_step <Web のインスタンス ID> <analytics の ECS のクラスター> <Grafana のサービス> <確かめ直すコマンド>
  # ops/up.sh と ops/oss/up.sh の最後に打つ。OK でなければ GRAFANA_WARN に警告を入れて黄色で出す（up.sh は止めない。呼ぶ側が最後にもう一度出す）。
  # NG は評価のエラーの理由を見る Grafana のログを、未確認は確かめ直すコマンドを案内する（未確認は評価のエラーとは限らないので、ログは案内しない）
  local rc=0
  GRAFANA_WARN=""
  # 打ち直しで Grafana のタスクが入れ替わる途中だと、Cloud Map の名前が前のタスクを指していることがある。入れ替わりが終わってから見る
  if ! aws ecs wait services-stable --region "$REGION" --cluster "$2" --services "$3"; then
    GRAFANA_WARN="Grafana のサービス（$3）が 10 分たっても安定しないので、アラートルールの評価を確かめていない。aws ecs list-tasks --region $REGION --cluster $2 --desired-status STOPPED を見て、直ったら $4"
  else
    grafana_rules_check "$1" || rc=$?
    case "$rc" in
      0) ;;
      1) GRAFANA_WARN="Grafana のアラートルールの評価を確かめた結果が OK ではない（${GRAFANA_VERDICT}）。評価のエラーの理由は Grafana のログ（aws logs tail /ecs/$PREFIX-grafana --region $REGION --since 1h --filter-pattern '\"Failed to evaluate rule\"'）。直したら $4" ;;
      *) GRAFANA_WARN="Grafana のアラートルールの評価を確かめられなかった（${GRAFANA_VERDICT:-判定の行が無い。上の出力}）。理由は上の出力。確かめ直すのは $4" ;;
    esac
  fi
  if [ -n "$GRAFANA_WARN" ]; then printf '\033[1;33m%s\033[0m\n' "$GRAFANA_WARN"; fi
}
grafana_skip_warn() {  # grafana_skip_warn <確かめ直すコマンド>
  # 9-2 で analytics の state の一覧か Grafana のクラスター・サービスの名前が読めないとき。止めず（最後の案内まで届かせる）、空の名前で aws ecs wait に進まず、
  # 確かめていないことを GRAFANA_WARN に入れて黄色で出す（呼ぶ側が最後にもう一度出す）。どれが読めないかは、その前の terraform のエラーと tf_output の NG: の行
  GRAFANA_WARN="$TF_DIR/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは $1"
  printf '\033[1;33m%s\033[0m\n' "$GRAFANA_WARN"
}
build_worker() {  # build_worker <タグ> [requirements のファイル名]  Temporal の worker（arm64）。OSS 版は requirements-oss.txt（neo4j のドライバー入り）
  docker buildx build --platform linux/arm64 --build-arg "REQUIREMENTS=${2:-requirements.txt}" -t "$REG/$PREFIX-worker:$1" --push -f docker/images/temporal/Dockerfile app/temporal/
}
mirror_temporal() {  # Temporal の CLI 入りイメージ（temporal server start-dev。arm64 あり）。Fargate は ECR からしか安定して引けないのでミラーする
  docker pull --platform linux/arm64 "temporalio/temporal:$TEMPORAL_TAG"
  docker tag "temporalio/temporal:$TEMPORAL_TAG" "$REG/$PREFIX-temporal:$TEMPORAL_TAG"
  docker push "$REG/$PREFIX-temporal:$TEMPORAL_TAG"
}
build_nautobot() {  # build_nautobot <タグ> <context のディレクトリ> [requirements のファイル名]  Nautobot の公式イメージ（arm64。約 1 GB）に boto3 と Job と対応付けと最初の seed を足す。
  # OSS 版は requirements-oss.txt（neo4j のドライバー入り。Job が Neo4j に書く）。どちらの requirements も context（app/nautobot/ の写し）にあるので、タグ（dir_tag）は両方の中身で決まる
  docker buildx build --platform linux/arm64 --build-arg "NAUTOBOT_VERSION=$NAUTOBOT_VERSION" --build-arg "REQUIREMENTS=${3:-requirements.txt}" \
    -t "$REG/$PREFIX-nautobot:$1" --push -f docker/images/nautobot/Dockerfile "$2"
}
ensure_nautobot_secrets() {  # pipeline/nautobot の apply より前に呼ぶ。値は出さない
  # Django の SECRET_KEY・画面の管理者のパスワード・RDS のマスターユーザーのパスワードは SSM に乱数で作る（Terraform の state に載せない）
  ensure_secret "/$PREFIX/nautobot/secret-key" password "Nautobot SECRET_KEY (created by $OPS_DIR/up.sh)"
  ensure_secret "/$PREFIX/nautobot/admin-password" password "Nautobot admin password (created by $OPS_DIR/up.sh)"
  ensure_secret "/$PREFIX/nautobot/db-password" password "Nautobot database password (created by $OPS_DIR/up.sh)"
  # Web の「トポロジ」タブがリンクの追加・削除を Nautobot の REST API に書くためのトークン（bootstrap.py が同じ値でユーザー nwc-web のトークンを作る）
  ensure_secret "/$PREFIX/nautobot/api-token" token "Nautobot API token of the web UI (created by $OPS_DIR/up.sh)"
}

# ---- MSK の SASL/SCRAM の資格情報（cycle 012。マネージド版の ops/up.sh だけが呼ぶ。OSS 版の Kafka は認証なしの 9092）
# syslog-ng と GoFlow2 と gnmic（cycle 013）が MSK に書くときのユーザー名とパスワード。コレクターごとに別の secret とユーザー（cycle 031。
# app/spark/snmp_sinks.py の SCRAM_USERS がユーザーごとに書けるトピックを絞る）。MSK の SCRAM は Secrets Manager の secret（名前が AmazonMSK_ で始まる）しか受けず、
# その secret は自分で作った KMS の鍵で暗号化しないといけない（AWS が管理する aws/secretsmanager の鍵は使えない）。鍵は 3 本の secret で 1 本。
# 値を Terraform の state に入れないよう、両方ここで作る。IaC/terraform/aws-managed/pipeline/stream/msk.tf は同じ名前の data source で ARN だけ引く
# （名前が揃っていることは tests/test_stream.py が見る）。消すのは ops/down.sh（ops/down-common.sh の delete_msk_scram）。
# 費用は鍵が月 $1、secret が 1 本月 $0.40（どちらも日割り）。1 時間あたり 0.2 セントに満たないので、ops/up.sh の時間あたりの目安には入れていない
ensure_msk_scram_key() {  # 鍵（alias/<PREFIX>-msk-scram）が無ければ作る。MSK_SCRAM_KEY_ARN に鍵の ARN を入れる
  local alias="alias/$PREFIX-msk-scram" out arn state
  if ! out=$(aws kms describe-key --region "$REGION" --key-id "$alias" --query 'KeyMetadata.[Arn,KeyState]' --output text 2>&1); then
    case "$out" in *NotFoundException*) out="" ;; *) die "$alias を確かめられない: $out" ;; esac
  fi
  arn=${out%%$'\t'*}; state=${out##*$'\t'}
  case "$state" in
    Enabled) echo "$alias はある（作り直さない）" ;;
    PendingDeletion | Disabled)
      # 直前の ops/down.sh が削除を予約したあと alias を外せなかったときなど。予約を取り消して使い直す（取り消すと、待った日数も課金される）
      if [ "$state" = PendingDeletion ]; then
        aws kms cancel-key-deletion --region "$REGION" --key-id "$arn" >/dev/null || die "$alias の鍵の削除の予約を取り消せなかった（上のエラー）"
      fi
      aws kms enable-key --region "$REGION" --key-id "$arn" || die "$alias の鍵を有効に戻せなかった（上のエラー）"
      echo "$alias は $state だったので有効に戻した" ;;
    "")
      arn=$(aws kms create-key --region "$REGION" --description "MSK SCRAM secret key of $PREFIX (created by $OPS_DIR/up.sh)" \
        --tags "TagKey=ManagedBy,TagValue=$OPS_DIR/up.sh" "TagKey=Project,TagValue=$PREFIX" "TagKey=owner,TagValue=$OWNER" \
        --query KeyMetadata.Arn --output text) || die "KMS の鍵を作れなかった（上のエラー）"
      aws kms create-alias --region "$REGION" --alias-name "$alias" --target-key-id "$arn" \
        || die "$alias を付けられなかった（上のエラー）。作った鍵は名前なしで残るので、削除を予約する: aws kms schedule-key-deletion --region $REGION --key-id $arn --pending-window-in-days 7"
      echo "$alias を作った" ;;
    *) die "$alias の鍵が使えない状態（${state}）" ;;
  esac
  MSK_SCRAM_KEY_ARN=$arn
}
MSK_SCRAM_INPUT=""  # ensure_msk_scram_secret が値を書く一時ファイル。create-secret の最中に止まっても ops/up.sh の EXIT の trap（on_exit）が消す
ensure_msk_scram_secret() {  # ensure_msk_scram_secret <コレクター>: secret（AmazonMSK_<PREFIX>-<コレクター>、username は <コレクター>）が無ければ作る。
  # 先に ensure_msk_scram_key を呼ぶ（MSK_SCRAM_KEY_ARN で暗号化する）。値は出さない
  local collector=${1:?ensure_msk_scram_secret にコレクター名を渡す}
  local name="AmazonMSK_$PREFIX-$collector" out kms deleted
  # 中身（ユーザー名とパスワード）は読まない。describe-secret はメタデータ（暗号化の鍵と削除の予約）だけを返す
  if out=$(aws secretsmanager describe-secret --region "$REGION" --secret-id "$name" --query '[KmsKeyId,DeletedDate]' --output text 2>&1); then
    kms=${out%%$'\t'*}; deleted=${out##*$'\t'}
    if [ "$deleted" != None ]; then
      aws secretsmanager restore-secret --region "$REGION" --secret-id "$name" >/dev/null || die "削除を予約された $name を戻せなかった（上のエラー）"
      echo "$name は削除の予約中だったので戻した"
    fi
    # 別の鍵（作り直す前の鍵や aws/secretsmanager）で暗号化した secret は MSK が受けないので止める
    case "$kms" in
      "$MSK_SCRAM_KEY_ARN" | "${MSK_SCRAM_KEY_ARN##*/}" | "alias/$PREFIX-msk-scram" | *":alias/$PREFIX-msk-scram") ;;
      *) die "$name の暗号化の鍵（${kms}）が alias/$PREFIX-msk-scram の鍵と違う。消してから打ち直す: aws secretsmanager delete-secret --region $REGION --secret-id $name --force-delete-without-recovery" ;;
    esac
    echo "$name はある（作り直さない）"
    return
  fi
  case "$out" in *ResourceNotFoundException*) ;; *) die "$name を確かめられない: $out" ;; esac
  local rc=0
  MSK_SCRAM_INPUT=$(umask 077; mktemp "${TMPDIR:-/tmp}/nwc-secret.XXXXXX") || die "一時ファイルを作れなかった"
  # パスワードは乱数（英数字と - _ だけ）。値はコマンドラインにも画面にも出さず、一時ファイル（自分だけが読める）から渡してすぐ消す
  "${PY[@]}" -c 'import json, secrets, sys
name, desc, key, prefix, owner, managed_by, path, user = sys.argv[1:]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"Name": name, "Description": desc, "KmsKeyId": key,
               "SecretString": json.dumps({"username": user, "password": secrets.token_urlsafe(24)}),
               "Tags": [{"Key": "ManagedBy", "Value": managed_by}, {"Key": "Project", "Value": prefix}, {"Key": "owner", "Value": owner}]}, f)' \
    "$name" "MSK SCRAM credentials of $collector (created by $OPS_DIR/up.sh)" "$MSK_SCRAM_KEY_ARN" "$PREFIX" "$OWNER" "$OPS_DIR/up.sh" "$MSK_SCRAM_INPUT" "$collector" \
    && aws secretsmanager create-secret --region "$REGION" --cli-input-json "file://$MSK_SCRAM_INPUT" >/dev/null || rc=$?
  rm -f -- "${MSK_SCRAM_INPUT:?}"; MSK_SCRAM_INPUT=""
  [ "$rc" -eq 0 ] || die "Secrets Manager に $name を作れなかった（上のエラー）。直前の $OPS_DIR/down.sh で消したばかりなら、数分おいて打ち直す"
  echo "$name を作った（値は出さない）"
}
