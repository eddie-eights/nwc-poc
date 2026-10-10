# entrypoint-rds.sh（サーバー）と init-rds.sh（初期化の一回きりのタスク）が `. /etc/temporal/common-rds.sh` で読む共通部分（cycle 042。Dockerfile が置く）。
# 読む前に set -eu と LOG_NAME（ログの接頭辞）を決める。ここでは env を検査して TLS を導き、関数を定めるだけで、DB には触らない:
#   - POSTGRES_SEEDS / POSTGRES_USER / POSTGRES_PWD の検査と、DB_PORT / DBNAME / VISIBILITY_DBNAME / SQL_TLS_ENABLED / SQL_HOST_VERIFICATION の既定値
#   - log()、SIGTERM の trap（cycle 040）、psql と temporal-sql-tool の TLS の導出（cycle 039 / 040）、DB の 5432 が開くまで待つ wait_for_db()
# パスワードは env（ECS の secrets）から psql の \getenv と PGPASSWORD / SQL_PASSWORD で渡し、コマンドラインにもログにも出さない。

: "${POSTGRES_SEEDS:?POSTGRES_SEEDS（RDS のアドレス）が無い}"
: "${POSTGRES_USER:?POSTGRES_USER（Temporal のロール名）が無い}"
: "${POSTGRES_PWD:?POSTGRES_PWD（Temporal のロールのパスワード）が無い}"
: "${DB_PORT:=5432}"
: "${DBNAME:=temporal}"
: "${VISIBILITY_DBNAME:=temporal_visibility}"
: "${SQL_TLS_ENABLED:=true}"
: "${SQL_HOST_VERIFICATION:=false}"
: "${LOG_NAME:=temporal-rds}"
export DB_PORT DBNAME VISIBILITY_DBNAME SQL_TLS_ENABLED POSTGRES_PWD

SCHEMA_DIR=/etc/temporal/schema/postgresql/v12

log() { echo "$LOG_NAME: $*" >&2; }
# PID 1 の sh はハンドラの無い SIGTERM を無視する（stopTimeout の後の SIGKILL まで止まらない）。ash は前景の子が返ってから trap を走らせるので、
# update-schema や sleep の途中では切れない。サーバーは exec tini で trap が既定に戻る（cycle 040）
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

# DB が受け付けるまで待つ（RDS が起きる前にタスクが上がることがあるので、公式の 1 回だけでなく回す）
wait_for_db() {
  i=0
  until nc -z -w 10 "$POSTGRES_SEEDS" "$DB_PORT"; do
    i=$((i + 1))
    if [ "$i" -ge 30 ]; then log "$POSTGRES_SEEDS:$DB_PORT に繋がらない（30 回）"; exit 1; fi
    log "$POSTGRES_SEEDS:$DB_PORT を待つ（$i/30）"
    sleep 5
  done
}
