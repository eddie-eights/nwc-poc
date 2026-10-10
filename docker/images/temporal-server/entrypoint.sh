#!/bin/sh
# Temporal のサーバーの入口（cycle 036。docker/images/temporal-server/Dockerfile の ENTRYPOINT）。
# 公式の setup-postgres.sh と create-namespace.sh（temporalio/docker-builds）を 1 本に畳み、ロールと DB の作成だけ psql で足した。
# 全部べき等で、2 回目以降の起動（タスクの入れ替え、再起動）でも同じ道を通る:
#   1. Nautobot の RDS の master（NAUTOBOT_DB_USER）で、ロール POSTGRES_USER と DB の DBNAME / VISIBILITY_DBNAME を無いときだけ作る（owner はロール）
#   2. スキーマをロール POSTGRES_USER で入れる（setup-schema は初回だけ。update-schema は毎回）
#   3. 背景で namespace DEFAULT_NAMESPACE を無いときだけ作る（サーバーが上がるのを待ってから）
#   4. 公式の /etc/temporal/entrypoint.sh へ exec（temporal-server start）
# パスワードは env（ECS の secrets）から psql の \getenv と PGPASSWORD / SQL_PASSWORD で渡し、コマンドラインにもログにも出さない。
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
: "${DEFAULT_NAMESPACE:=default}"
: "${DEFAULT_NAMESPACE_RETENTION:=72h}"
export DB_PORT DBNAME VISIBILITY_DBNAME SQL_TLS_ENABLED POSTGRES_PWD

SCHEMA_DIR=/etc/temporal/schema/postgresql/v12
TEMPORAL_ADDRESS_LOCAL=127.0.0.1:7233

log() { echo "entrypoint-rds: $*" >&2; }

# psql の接続先（パスワードは呼ぶたびに PGPASSWORD で渡す）。RDS PostgreSQL 15 以降は rds.force_ssl=1 が既定なので、TLS 有効なら require
export PGHOST="$POSTGRES_SEEDS" PGPORT="$DB_PORT" PGCONNECT_TIMEOUT=10
if [ "$SQL_TLS_ENABLED" = "true" ]; then export PGSSLMODE=require; else export PGSSLMODE=prefer; fi

# 1. DB が受け付けるまで待つ（RDS が起きる前にタスクが上がることがあるので、公式の 1 回だけでなく回す）
i=0
until nc -z -w 10 "$POSTGRES_SEEDS" "$DB_PORT"; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then log "$POSTGRES_SEEDS:$DB_PORT に繋がらない（30 回）"; exit 1; fi
  log "$POSTGRES_SEEDS:$DB_PORT を待つ（$i/30）"
  sleep 5
done

# 2. ロールと DB を master で作る（無いときだけ。パスワードは毎回揃える）。CREATE DATABASE ... OWNER には master がそのロールのメンバーである必要があるので先に GRANT
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
sql_tool() {  # sql_tool <DB 名> <サブコマンドと引数...>
  db=$1; shift
  SQL_TLS="$SQL_TLS_ENABLED" SQL_TLS_DISABLE_HOST_VERIFICATION=true SQL_PASSWORD="$POSTGRES_PWD" \
    temporal-sql-tool --plugin postgres12 --ep "$POSTGRES_SEEDS" -p "$DB_PORT" -u "$POSTGRES_USER" --db "$db" "$@"
}
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

# 4. namespace を背景で作る（公式 create-namespace.sh と同じ流れ）。失敗しても本体は落とさない（healthCheck が namespace を見るので ECS 側で UNHEALTHY になる）
(
  n=0
  until nc -z -w 10 127.0.0.1 7233; do
    n=$((n + 1)); if [ "$n" -ge 30 ]; then log "namespace: frontend の 7233 が開かない（30 回）。作らずに抜ける"; exit 0; fi
    sleep 5
  done
  n=0
  until temporal operator cluster health --address "$TEMPORAL_ADDRESS_LOCAL" >/dev/null 2>&1; do
    n=$((n + 1)); if [ "$n" -ge 30 ]; then log "namespace: cluster health が通らない（30 回）。作らずに抜ける"; exit 0; fi
    sleep 5
  done
  n=0
  while :; do
    if temporal operator namespace describe -n "$DEFAULT_NAMESPACE" --address "$TEMPORAL_ADDRESS_LOCAL" >/dev/null 2>&1; then
      log "namespace: $DEFAULT_NAMESPACE がある"; exit 0
    fi
    if temporal operator namespace create -n "$DEFAULT_NAMESPACE" --retention "$DEFAULT_NAMESPACE_RETENTION" --address "$TEMPORAL_ADDRESS_LOCAL" >/dev/null 2>&1; then
      log "namespace: $DEFAULT_NAMESPACE を作った（retention ${DEFAULT_NAMESPACE_RETENTION}）"; exit 0
    fi
    n=$((n + 1)); if [ "$n" -ge 30 ]; then log "namespace: $DEFAULT_NAMESPACE を作れない（30 回）"; exit 0; fi
    sleep 5
  done
) &

# 5. 公式の入口（BIND_ON_IP=0.0.0.0 なら TEMPORAL_BROADCAST_ADDRESS を getent hosts $(hostname) で埋めて temporal-server start）
exec /etc/temporal/entrypoint.sh
