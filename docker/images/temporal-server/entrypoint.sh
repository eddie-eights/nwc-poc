#!/bin/sh
# Temporal のサーバーの入口（cycle 036。docker/images/temporal-server/Dockerfile の ENTRYPOINT）。
# ロールと DB とスキーマは一回きりのタスク <接頭辞>-workflow-init（init-rds.sh。ops/up.sh の 8-5 が run-task する）が入れ、ここは master の
# パスワード（NAUTOBOT_DB_PASSWORD）を持たない（タスク定義の env にも secrets にも無い。ECS Exec / healthCheck / /proc/1/environ から見えない。cycle 042）。
# 全部べき等で、2 回目以降の起動（タスクの入れ替え、再起動）でも同じ道を通る:
#   1. DB の 5432 が開くまで待つ（common-rds.sh の wait_for_db）
#   2. ロール POSTGRES_USER で、両方の DB の schema_version.curr_version がイメージの versioned/ の最新の版に揃うまで待つ（init のタスクが終わるのを待つ。
#      ロールが無い・DB が無い・表が無い・版が古い、は全部「まだ」。apply と run-task の順序に依存しない。cycle 042）
#   3. 背景で namespace DEFAULT_NAMESPACE を無いときだけ作る（/etc/temporal/namespace-rds.sh。サーバーが上がるのを待ってから。cycle 039）
#   4. tini（PID 1）の子として公式の /etc/temporal/entrypoint.sh へ exec（temporal-server start）。背景の namespace-rds.sh も tini の子になり、抜けたら tini が回収する（cycle 039）
# exec tini までは PID 1 のこの sh が走る。ECS の停止（SIGTERM）は common-rds.sh の trap で受け、走っている手順（psql / sleep）が返った区切りで exit 143 で抜ける（cycle 040）
# TLS（psql）はサーバー本体と同じ SQL_TLS_ENABLED / SQL_HOST_VERIFICATION / SQL_CA（ecs.tf）から common-rds.sh が導く（cycle 039）。
set -eu

LOG_NAME=entrypoint-rds
. /etc/temporal/common-rds.sh
: "${DEFAULT_NAMESPACE:=default}"
: "${DEFAULT_NAMESPACE_RETENTION:=72h}"

TEMPORAL_ADDRESS_LOCAL=127.0.0.1:7233

# 1. DB が受け付けるまで待つ
step=1
wait_for_db

# 2. init のタスクがスキーマをイメージの版まで上げるのを待つ。版はディレクトリ名（v1.19）の v を除いた最大（1.19。schema_version の curr_version と同じ形）
schema_want() {  # schema_want <temporal|visibility>
  ls "$SCHEMA_DIR/$1/versioned" | sed -n 's/^v//p' | sort -t. -k1,1n -k2,2n | tail -n 1
}
schema_have() {  # schema_have <DB 名>  ロール POSTGRES_USER で読む。読めなければ空（ロール・DB・表が無い）
  PGPASSWORD="$POSTGRES_PWD" psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -tAc \
    "SELECT curr_version FROM schema_version WHERE version_partition = 0" 2>/dev/null || true
}
step=2
want_t=$(schema_want temporal); want_v=$(schema_want visibility)
if [ -z "$want_t" ] || [ -z "$want_v" ]; then log "イメージの $SCHEMA_DIR/{temporal,visibility}/versioned に版が無い（Dockerfile の COPY）"; exit 1; fi
i=0
while :; do
  have_t=$(schema_have "$DBNAME"); have_v=$(schema_have "$VISIBILITY_DBNAME")
  if [ "$have_t" = "$want_t" ] && [ "$have_v" = "$want_v" ]; then break; fi
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    log "初期化のタスク（<接頭辞>-workflow-init）が 60 回待っても終わらない。ops/up.sh（OSS 版は ops/oss/up.sh）を打ち直すか、init のログ（terraform output init_logs_command）を見る"; exit 1
  fi
  log "初期化のタスク（ops/up.sh の 8-5 が run-task する <接頭辞>-workflow-init）を待つ（${i}/60。スキーマの版 $DBNAME=${have_t:-無し}/$want_t $VISIBILITY_DBNAME=${have_v:-無し}/${want_v}）"
  sleep 10
done
log "スキーマは $DBNAME=$have_t / $VISIBILITY_DBNAME=${have_v}（イメージの版と同じ）"

# 3. namespace を背景で作る（公式 create-namespace.sh と同じ流れ）。失敗しても log を出して exit 0 で抜けるだけで、終了コードは誰も待たない（healthCheck が namespace を見るので ECS 側で UNHEALTHY になる）。
# 別の実行ファイルを exec させる（fork だけのサブシェルは /proc/<pid>/environ に起動時の env を持ち続ける。cycle 039）
step=3
/etc/temporal/namespace-rds.sh "$TEMPORAL_ADDRESS_LOCAL" "$DEFAULT_NAMESPACE" "$DEFAULT_NAMESPACE_RETENTION" &

# 4. 公式の入口（BIND_ON_IP=0.0.0.0 なら TEMPORAL_BROADCAST_ADDRESS を getent hosts $(hostname) で埋めて temporal-server start）。
# PID 1 を tini にする: 背景の namespace-rds.sh は tini の子になり、抜けたら tini が回収する。tini は SIGTERM を temporal-server へ渡す（cycle 039）
exec /sbin/tini -- /etc/temporal/entrypoint.sh
