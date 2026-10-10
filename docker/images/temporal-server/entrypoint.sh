#!/bin/sh
# Temporal のサーバーの入口（cycle 036。docker/images/temporal-server/Dockerfile の ENTRYPOINT）。
# 公式の setup-postgres.sh と create-namespace.sh（temporalio/docker-builds）を 1 本に畳み、ロールと DB の作成だけ psql で足した。
# 全部べき等で、2 回目以降の起動（タスクの入れ替え、再起動）でも同じ道を通る:
#   1. Nautobot の RDS の master（NAUTOBOT_DB_USER）で、ロール POSTGRES_USER と DB の DBNAME / VISIBILITY_DBNAME を無いときだけ作る（owner はロール）。
#      master のパスワード（NAUTOBOT_DB_PASSWORD）は最後に使った直後に env から外し、この後に起こすプロセスに渡さない（cycle 039）
#   2. スキーマをロール POSTGRES_USER で入れる（setup-schema は初回だけ。update-schema は毎回）
#   3. 背景で namespace DEFAULT_NAMESPACE を無いときだけ作る（/etc/temporal/namespace-rds.sh。サーバーが上がるのを待ってから。cycle 039）
#   4. tini（PID 1）の子として公式の /etc/temporal/entrypoint.sh へ exec（temporal-server start）。背景の namespace-rds.sh も tini の子になり、抜けたら tini が回収する（cycle 039）
# exec tini までは PID 1 のこの sh が走る。ECS の停止（SIGTERM）は trap で受け、走っている手順（psql / temporal-sql-tool）が返った区切りで exit 143 で抜ける（cycle 040）
# パスワードは env（ECS の secrets）から psql の \getenv と PGPASSWORD / SQL_PASSWORD で渡し、コマンドラインにもログにも出さない。
# TLS（psql と temporal-sql-tool）はサーバー本体と同じ SQL_TLS_ENABLED / SQL_HOST_VERIFICATION / SQL_CA / SQL_HOST_NAME（ecs.tf）から導く（cycle 039）。
set -eu

: "${POSTGRES_SEEDS:?POSTGRES_SEEDS（RDS のアドレス）が無い}"
: "${POSTGRES_USER:?POSTGRES_USER（Temporal のロール名）が無い}"
: "${POSTGRES_PWD:?POSTGRES_PWD（Temporal のロールのパスワード）が無い}"
: "${NAUTOBOT_DB_USER:?NAUTOBOT_DB_USER（RDS の master）が無い}"
: "${NAUTOBOT_DB_PASSWORD:?NAUTOBOT_DB_PASSWORD（RDS の master のパスワード）が無い}"
: "${DB_PORT:=5432}"
: "${DBNAME:=temporal}"
: "${VISIBILITY_DBNAME:=temporal_visibility}"
: "${NAUTOBOT_DB_NAME:=nautobot}"
: "${SQL_TLS_ENABLED:=true}"
: "${SQL_HOST_VERIFICATION:=false}"
: "${DEFAULT_NAMESPACE:=default}"
: "${DEFAULT_NAMESPACE_RETENTION:=72h}"
export DB_PORT DBNAME VISIBILITY_DBNAME SQL_TLS_ENABLED POSTGRES_PWD

SCHEMA_DIR=/etc/temporal/schema/postgresql/v12
TEMPORAL_ADDRESS_LOCAL=127.0.0.1:7233

log() { echo "entrypoint-rds: $*" >&2; }
# PID 1 の sh はハンドラの無い SIGTERM を無視する（stopTimeout の後の SIGKILL まで止まらない）。ash は前景の子が返ってから trap を走らせるので、
# update-schema の途中では切れない。exec tini で trap は既定に戻る（cycle 040）
trap 'log "SIGTERM を受けたので初期化を止める（手順 ${step:-0} のあと。ここまでの手順はべき等なので次の起動でやり直す）"; exit 143' TERM INT

# psql の接続先（パスワードは呼ぶたびに PGPASSWORD で渡す）。RDS PostgreSQL 15 以降は rds.force_ssl=1 が既定なので、TLS 有効なら require。
# SQL_HOST_VERIFICATION=true ならサーバー本体と同じく証明書とホスト名を確かめる（verify-full。CA は SQL_CA）
export PGHOST="$POSTGRES_SEEDS" PGPORT="$DB_PORT" PGCONNECT_TIMEOUT=10
# サーバー本体の設定テンプレート（YAML）は True / TRUE も真に読むが、sh の = は大文字小文字を区別するので小文字に揃える（cycle 040）
SQL_TLS_ENABLED=$(printf '%s' "$SQL_TLS_ENABLED" | tr '[:upper:]' '[:lower:]')
SQL_HOST_VERIFICATION=$(printf '%s' "$SQL_HOST_VERIFICATION" | tr '[:upper:]' '[:lower:]')
if [ "$SQL_HOST_VERIFICATION" = "true" ] && [ -z "${SQL_CA:-}" ]; then
  log "SQL_HOST_VERIFICATION=true なのに SQL_CA（CA のパス）が無い。psql の verify-full も temporal-server も RDS の証明書を確かめられない"; exit 1
fi
if [ "$SQL_TLS_ENABLED" = "true" ]; then
  if [ "$SQL_HOST_VERIFICATION" = "true" ]; then
    export PGSSLMODE=verify-full
    if [ -n "${SQL_CA:-}" ]; then export PGSSLROOTCERT="$SQL_CA"; fi
  else
    export PGSSLMODE=require
  fi
else
  export PGSSLMODE=prefer
fi
# temporal-sql-tool はホスト名の検証を「外す」フラグ（--tls-disable-host-verification）なので、SQL_HOST_VERIFICATION の否定を渡す
# SQL_TLS_SERVER_NAME は temporal-sql-tool の env（tools/sql/main.go）、SQL_HOST_NAME はサーバー本体の env（config_template.yaml の serverName）
if [ "$SQL_HOST_VERIFICATION" = "true" ]; then sql_tool_skip_host_verify=false; else sql_tool_skip_host_verify=true; fi

# 1. DB が受け付けるまで待つ（RDS が起きる前にタスクが上がることがあるので、公式の 1 回だけでなく回す）
step=1
i=0
until nc -z -w 10 "$POSTGRES_SEEDS" "$DB_PORT"; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then log "$POSTGRES_SEEDS:$DB_PORT に繋がらない（30 回）"; exit 1; fi
  log "$POSTGRES_SEEDS:$DB_PORT を待つ（$i/30）"
  sleep 5
done

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
# master のパスワードはここで使い終わる。この後に起こすプロセス（temporal-sql-tool、namespace-rds.sh、tini、temporal-server）の /proc/<pid>/environ に残さない
unset NAUTOBOT_DB_PASSWORD

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

# 4. namespace を背景で作る（公式 create-namespace.sh と同じ流れ）。失敗しても log を出して exit 0 で抜けるだけで、終了コードは誰も待たない（healthCheck が namespace を見るので ECS 側で UNHEALTHY になる）。
# fork だけのサブシェルは /proc/<pid>/environ に起動時の env（master のパスワード入り）を持ち続けるので、別の実行ファイルを exec させる（cycle 039）
step=4
/etc/temporal/namespace-rds.sh "$TEMPORAL_ADDRESS_LOCAL" "$DEFAULT_NAMESPACE" "$DEFAULT_NAMESPACE_RETENTION" &

# 5. 公式の入口（BIND_ON_IP=0.0.0.0 なら TEMPORAL_BROADCAST_ADDRESS を getent hosts $(hostname) で埋めて temporal-server start）。
# PID 1 を tini にする: 背景の namespace-rds.sh は tini の子になり、抜けたら tini が回収する。tini は SIGTERM を temporal-server へ渡す（cycle 039）
exec /sbin/tini -- /etc/temporal/entrypoint.sh
