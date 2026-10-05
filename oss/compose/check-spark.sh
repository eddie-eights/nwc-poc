#!/usr/bin/env bash
# 005 ステップ 1 の 5: EMR を使わない Spark 3.5 が、compose の Kafka から読んで OpenSearch と vminsert に書けるか（spark/sink_check.py）。
# Kafka 3 台、OpenSearch 3 台、VictoriaMetrics のクラスター、Spark（外に出られないネットワークだけ）を一緒に起こす。
# 使い方: oss/compose/check-spark.sh（終わってもコンテナは残す。片付けは docker compose --profile '*' down -v）
set -euo pipefail
cd "$(dirname "$0")"

dc() { docker compose --profile spark "$@"; }
OS=https://127.0.0.1:19201
OS_AUTH='admin:NwcOss-Trial-2026!'   # compose.yaml の試し用の値

wait_for() {   # wait_for <秒> <説明> <コマンド…>
  local limit=$1 what=$2; shift 2
  for _ in $(seq 1 "$limit"); do
    if "$@" >/dev/null 2>&1; then echo "  ok: $what"; return 0; fi
    sleep 1
  done
  echo "  タイムアウト: $what" >&2; return 1
}
os_green() { curl -skf -u "$OS_AUTH" "$OS/_cluster/health?wait_for_status=green&timeout=1s" | grep -q '"status":"green"'; }
vm_connected() { [ "$(curl -sf http://127.0.0.1:18480/metrics | grep -c '^vm_tcpdialer_conns{name="vminsert_metric_rows".* 1$')" = 3 ]; }

echo "== 起こす（Spark のイメージを作る）"
dc up -d --build
wait_for 120 "Kafka の quorum が答える" dc exec -T kafka-1 /opt/kafka/bin/kafka-metadata-quorum.sh --bootstrap-server kafka-1:9092 describe --status
wait_for 180 "OpenSearch が green" os_green
wait_for 60 "vminsert が vmstorage 3 台につないだ（vm_tcpdialer_conns）" vm_connected
wait_for 60 "vmselect が答える" curl -sf http://127.0.0.1:18481/health

echo "== Spark のイメージ"
docker image ls nwc-oss-spark:check --format '{{.Repository}}:{{.Tag}} {{.Size}}'
dc exec -T spark ls /opt/spark/jars | grep -E 'kafka|iceberg|s3-tables|commons-pool2'

echo "== spark-submit（local[2]、driver 1g）。動いているあいだ docker stats を取り続ける（全部の出力は SPARK_LOG があればそこへ）"
STATS=$(mktemp)
( while :; do docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' | grep nwc-oss >> "$STATS"; sleep 2; done ) &
SAMPLER=$!
dc exec -T spark /opt/spark/bin/spark-submit --master 'local[2]' --driver-memory 1g /opt/oss/sink_check.py 2>&1 \
  | tee "${SPARK_LOG:-/dev/null}" | grep -E '^\[(sink_check|snmp_sinks)\]|Exception|Error' || true
kill "$SAMPLER" 2>/dev/null || true

echo "== コンテナごとの最大（$(awk '{print $1}' "$STATS" | sort | uniq -c | awk 'NR==1 {print $1}') 回取った）: CPU% とメモリ（MiB）"
awk '{
  cpu = $2; sub(/%/, "", cpu)
  mem = $3; mib = mem + 0
  if (mem ~ /GiB/) mib *= 1024; else if (mem ~ /KiB/) mib /= 1024; else if (mem ~ /B$/ && mem !~ /iB/) mib /= 1048576
  if (cpu + 0 > c[$1]) c[$1] = cpu + 0
  if (mib > m[$1]) m[$1] = mib
} END { for (k in m) printf "  %-26s CPU %6.1f%%  メモリ %7.1f MiB\n", k, c[k], m[k] }' "$STATS" | sort
rm -f "$STATS"
echo "== 終わり"
