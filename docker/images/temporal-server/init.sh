#!/bin/sh
# Temporal の初期化（cycle 042。一回きりのタスク <接頭辞>-workflow-init の入口。Dockerfile が /etc/temporal/init-rds.sh に置き、
# IaC/terraform/aws-managed/workflow/ecs.tf の aws_ecs_task_definition.init が entryPoint にする。ops/up.sh の 8-5（OSS 版は ops/oss/up.sh の 8）が
# terraform apply の直後に run-task で起こし、止まるまで待って終了コード 0 を確かめる。ops/up-common.sh の run_temporal_init）。
# Nautobot の RDS の master のパスワード（NAUTOBOT_DB_PASSWORD）を持つのはこのタスクだけで、サーバーのタスク定義には渡さない（cycle 036 のリスク 7）。
# 公式の setup-postgres.sh（temporalio/docker-builds）に、ロールと DB の作成を psql で足した。全部べき等で、up.sh を打つたびに同じ道を通る:
#   1. DB の 5432 が開くまで待つ（common-rds.sh の wait_for_db）
#   2. master（NAUTOBOT_DB_USER）で、ロール POSTGRES_USER と DB の DBNAME / VISIBILITY_DBNAME を無いときだけ作る（owner はロール。パスワードは毎回揃える）。btree_gin も
#   3. スキーマをロール POSTGRES_USER で入れる（setup-schema は初回だけ。update-schema は毎回。サーバーの entrypoint-rds.sh はこの版に揃うのを待つ）
# 停止（SIGTERM）は common-rds.sh の trap で受け、走っている手順（psql / temporal-sql-tool）が返った区切りで exit 143 で抜ける（cycle 040）
set -eu

LOG_NAME=init-rds
. /etc/temporal/common-rds.sh
: "${NAUTOBOT_DB_USER:?NAUTOBOT_DB_USER（RDS の master）が無い}"
: "${NAUTOBOT_DB_PASSWORD:?NAUTOBOT_DB_PASSWORD（RDS の master のパスワード）が無い}"
: "${NAUTOBOT_DB_NAME:=nautobot}"

# 1. DB が受け付けるまで待つ
step=1
wait_for_db

# 2. ロールと DB を master で作る（無いときだけ。パスワードは毎回揃える）。CREATE DATABASE ... OWNER には master がそのロールのメンバーである必要があるので先に GRANT
step=2
log "ロール $POSTGRES_USER と DB $DBNAME / $VISIBILITY_DBNAME を確かめる"
PGPASSWORD="$NAUTOBOT_DB_PASSWORD" psql -X -q -v ON_ERROR_STOP=1 -U "$NAUTOBOT_DB_USER" -d "$NAUTOBOT_DB_NAME" \
  -v role="$POSTGRES_USER" -v db="$DBNAME" -v vdb="$VISIBILITY_DBNAME" <<'SQL'
\getenv pw POSTGRES_PWD
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'pw')
  WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'role', :'pw') \gexec
-- PostgreSQL 16 以降、CREATEROLE で作った側（RDS の master）は ADMIN だけ持ち SET が無い（MEMBER は真になる）ので SET で見る
SELECT format('GRANT %I TO CURRENT_USER', :'role')
  WHERE NOT pg_has_role(CURRENT_USER, :'role', 'SET') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'vdb', :'role')
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'vdb') \gexec
SQL
# visibility のスキーマが使う拡張。RDS の master は rds_superuser なので作れる（ロールでは作れないことがあるので master で先に作る）
PGPASSWORD="$NAUTOBOT_DB_PASSWORD" psql -X -q -v ON_ERROR_STOP=1 -U "$NAUTOBOT_DB_USER" -d "$VISIBILITY_DBNAME" \
  -c 'CREATE EXTENSION IF NOT EXISTS btree_gin'

# 3. スキーマをロールで入れる。setup-schema -v 0.0 は schema_version を 0.0 に戻すので、schema_version が無い初回だけ打つ。update-schema はべき等なので毎回
sql_tool() {  # sql_tool <DB 名> <サブコマンドと引数...>。env はサブシェルの中だけで export する（CA とサーバー名は値があるときだけ渡す）
  db=$1; shift
  (
    export SQL_TLS="$SQL_TLS_ENABLED" SQL_TLS_DISABLE_HOST_VERIFICATION="$sql_tool_skip_host_verify" SQL_PASSWORD="$POSTGRES_PWD"
    if [ -n "${SQL_CA:-}" ]; then export SQL_TLS_CA_FILE="$SQL_CA"; fi
    if [ -n "${SQL_HOST_NAME:-}" ]; then export SQL_TLS_SERVER_NAME="$SQL_HOST_NAME"; fi
    exec temporal-sql-tool --plugin postgres12 --ep "$POSTGRES_SEEDS" -p "$DB_PORT" -u "$POSTGRES_USER" --db "$db" "$@"
  )
}
step=3
for pair in "$DBNAME:temporal" "$VISIBILITY_DBNAME:visibility"; do
  db=${pair%%:*}; dir=${pair#*:}
  has=$(PGPASSWORD="$POSTGRES_PWD" psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db" -tAc "SELECT to_regclass('schema_version') IS NOT NULL")
  if [ "$has" = "f" ]; then
    log "$db: 初回なので setup-schema -v 0.0"
    sql_tool "$db" setup-schema -v 0.0
  fi
  log "$db: update-schema"
  sql_tool "$db" update-schema -d "$SCHEMA_DIR/$dir/versioned"
done

log "初期化が終わった"
exit 0
