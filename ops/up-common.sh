# ops/up.sh と OSS 版（005）の oss/ops/up.sh が読む共通の関数（terraform の apply、Session Manager でのコマンド、SSM のシークレット、
# Glue の s3tablescatalog、Splunk のイメージと SSM のパラメータ）。
# 先に ops/common.sh と ops/deploy-env.sh を読む（log / die / tf / tf_logged を使う）。Splunk のイメージは ops/lab-common.sh の dir_tag / ecr_has を使う。
# REGION / PY / PREFIX / OWNER（s3tablescatalog は ACCOUNT_ID も）は呼ぶ前に決める。
tf_init() {  # tf_init <ルート>
  tf "$1" init -input=false >/dev/null \
    || die "$TF_DIR/$1 の init に失敗した（provider の取得。社内 PC は docs/setup.md「社内 PC の CA」）"
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
AZ_NUM_SET=""   # deploy.env か環境変数に書いてあった *_AZ_NUM（ENDPOINTS_AZ_NUM と比べる）
az_num() {  # az_num <キー> <既定> <最小> <最大> <範囲の理由>  書いてなければ既定。範囲の外なら止める
  local k=$1 v="${!1:-}"
  if [ -n "$v" ]; then AZ_NUM_SET="$AZ_NUM_SET $k"; else v=$2; fi
  case "$v" in *[!0-9]* | '') die "$k は $3〜$4 の数で書く（いまは $k=$v）。まだ何も作っていない" ;; esac
  v=$((10#$v))
  if [ "$v" -lt "$3" ] || [ "$v" -gt "$4" ]; then die "$k=$v は書けない。$3〜$4 で書く（$5）。まだ何も作っていない"; fi
  printf -v "$k" '%s' "$v"
}
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
  local input rc=0
  input=$(umask 077; mktemp "${TMPDIR:-/tmp}/nwc-secret.XXXXXX") || die "一時ファイルを作れなかった"
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
    "$name" "$kind" "$desc" "$PREFIX" "$OWNER" "$OPS_DIR/up.sh" "$input" \
    && aws ssm put-parameter --region "$REGION" --cli-input-json "file://$input" >/dev/null || rc=$?
  rm -f -- "${input:?}"
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
  local input rc=0
  input=$(umask 077; mktemp "${TMPDIR:-/tmp}/nwc-secret.XXXXXX") || die "一時ファイルを作れなかった"
  FIXED_SECRET_VALUE="$value" "${PY[@]}" -c 'import json, os, sys
name, desc, prefix, owner, managed_by, path = sys.argv[1:]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"Name": name, "Type": "SecureString", "Value": os.environ["FIXED_SECRET_VALUE"], "Description": desc,
               "Tags": [{"Key": "ManagedBy", "Value": managed_by}, {"Key": "Project", "Value": prefix}, {"Key": "owner", "Value": owner}]}, f)' \
    "$name" "$desc" "$PREFIX" "$OWNER" "$OPS_DIR/up.sh" "$input" \
    && aws ssm put-parameter --region "$REGION" --cli-input-json "file://$input" >/dev/null || rc=$?
  rm -f -- "${input:?}"
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
# Splunk Enterprise（analytics の ECS。terraform/pipeline/analytics の splunk.tf）。マネージド版と OSS 版（設計 005: Splunk は OSS 版でも変えない）が
# 同じイメージの作り方と同じ SSM のパラメータを使う。SPLUNK_VERSION は splunk/ の Dockerfile の ARG の既定値に合わせてある
# （変えるときは両方を変える。tests/check_splunk_image.py で、その版の Python の boto3 でアラートを送れるかも確かめる）
SPLUNK_VERSION=10.4.3   # splunk/splunk は amd64 だけ（ECS のタスクは X86_64）
splunk_image_check() {  # SPLUNK_TAG を splunk/ の中身から作り、ECR の <接頭辞>-splunk に無ければ NEED_SPLUNK=1。PREFIX を使う
  SPLUNK_TAG=$(dir_tag "$SPLUNK_VERSION" splunk) || die "splunk/ のタグを作れなかった"
  if ecr_has "$PREFIX-splunk" "$SPLUNK_TAG"; then echo "splunk:$SPLUNK_TAG はある"; else NEED_SPLUNK=1; fi
}
build_splunk() {  # docker login 済みで呼ぶ。REG / PREFIX / SPLUNK_TAG（splunk_image_check）を使う
  # Splunk Enterprise の公式イメージ（amd64 だけ。約 2〜3 GB）に検知のアプリ（splunk/netops_alerts）を足す。
  # Fargate は VPC の中から ECR しか引けず、タスクは Splunkbase にも出られないので、アプリはビルドのときに入れる
  # （COPY だけなので、arm64 の PC（Apple シリコン）でもエミュレーション無しで作れる）
  docker buildx build --platform linux/amd64 --build-arg "SPLUNK_VERSION=$SPLUNK_VERSION" -t "$REG/$PREFIX-splunk:$SPLUNK_TAG" --push splunk/
}
ensure_splunk_secrets() {  # ensure_splunk_secrets <SPLUNK_AZ_NUM>  analytics の apply より前に呼ぶ。値は出さない
  # 管理者のパスワードと HEC の token は SSM に乱数で作る（token は Splunk が GUID の形を求める）。
  # Splunk のタスクが起動時に読んで設定し、Spark（マネージド版は EMR Serverless のジョブ、OSS 版は ECS のタスク）も同じ token を読む
  ensure_secret "/$PREFIX/splunk/admin-password" password "Splunk admin password (created by $OPS_DIR/up.sh)"
  ensure_secret "/$PREFIX/splunk/hec-token" uuid "Splunk HEC token (created by $OPS_DIR/up.sh)"
  # クラスター（SPLUNK_AZ_NUM が 2 か 3）は、manager と indexer と search head が互いを確かめる合言葉（pass4SymmKey）も作る
  if [ "$1" -gt 1 ]; then ensure_secret "/$PREFIX/splunk/idxc-secret" password "Splunk indexer cluster key (created by $OPS_DIR/up.sh)"; fi
}
