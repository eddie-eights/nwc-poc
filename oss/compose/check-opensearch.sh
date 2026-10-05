#!/usr/bin/env bash
# 005 ステップ 1 の 2: OpenSearch 3.9.0（データ 2 台 + まとめ役だけの 1 台、レプリカ 1）が node.store.allow_mmap=false で 3 台のクラスターになるか。
# 1000 件入れ、1 台ずつ止めて（データの台 → まとめ役の台 → もう 1 つのデータの台）、そのたびに選挙が通り、1000 件全部が検索できるか。
# 手元の Docker Desktop の VM は vm.max_map_count が元から大きい（下で値を出す）。Fargate と同じ条件にはなっていない。
# 使い方: oss/compose/check-opensearch.sh（終わってもコンテナは残す。片付けは docker compose --profile '*' down -v）
set -euo pipefail
cd "$(dirname "$0")"

dc() { docker compose --profile opensearch "$@"; }
AUTH=admin:NwcOss-Trial-2026!   # compose.yaml の試し用の値
port() { case $1 in opensearch-1) echo 19201 ;; opensearch-2) echo 19202 ;; opensearch-cm) echo 19203 ;; esac; }   # macOS の bash 3.2 には連想配列が無い
NODES=(opensearch-2 opensearch-cm opensearch-1)

os() {   # os <ノード> <メソッド> <パス> [本文]
  local node=$1 method=$2 path=$3
  if [ $# -ge 4 ]; then
    curl -sk -u "$AUTH" -X "$method" -H 'Content-Type: application/x-ndjson' "https://127.0.0.1:$(port "$node")$path" --data-binary "$4"
  else
    curl -sk -u "$AUTH" -X "$method" "https://127.0.0.1:$(port "$node")$path"
  fi
}

wait_for() {   # wait_for <秒> <説明> <コマンド…>
  local limit=$1 what=$2; shift 2
  for _ in $(seq 1 "$limit"); do
    if "$@" >/dev/null 2>&1; then echo "  ok: $what（${SECONDS}s）"; return 0; fi
    sleep 1
  done
  echo "  タイムアウト: $what" >&2; return 1
}
health_is() { os "$1" GET /_cluster/health | jq -e --arg s "$2" --argjson n "$3" '.status == $s and .number_of_nodes == $n'; }
count_1000() { os "$1" GET /t1000/_count | jq -e '.count == 1000'; }

echo "== Docker Desktop の VM の vm.max_map_count（Fargate は変えられない。既定は 65530）"
docker run --rm alpine cat /proc/sys/vm/max_map_count

echo "== 3 台を起こす"
dc up -d opensearch-1 opensearch-2 opensearch-cm
wait_for 240 "green、ノード 3" health_is opensearch-1 green 3
os opensearch-1 GET '/_cat/nodes?v&h=name,node.role,cluster_manager,heap.max'
os opensearch-1 GET '/_nodes/settings?filter_path=nodes.*.name,nodes.*.settings.node.store' | jq -c '[.nodes[] | {name, allow_mmap: .settings.node.store.allow_mmap}]'
echo "  起動時の検査（bootstrap checks）を強制したか:"
dc logs opensearch-1 | grep -ho "bound or publishing to a non-loopback address, enforcing bootstrap checks" | head -1

echo "== t1000（シャード 2、レプリカ 1）に 1000 件"
os opensearch-1 PUT /t1000 '{"settings":{"number_of_shards":2,"number_of_replicas":1}}' | jq -c .
BULK="$(for i in $(seq 1 1000); do printf '{"index":{"_id":"%d"}}\n{"n":%d,"msg":"doc %d"}\n' "$i" "$i" "$i"; done)"$'\n'   # _bulk は最後に改行が要る
os opensearch-1 POST '/t1000/_bulk?refresh=true' "$BULK" | jq -c '{errors, items: (.items | length), error}'
os opensearch-1 GET /t1000/_count | jq -c .
# 複製（replica）を作り終わる前に primary の台を止めると、そのシャードは red になって戻らない（1 回目に踏んだ。ECS の入れ替えも green を待ってから次へ）
SECONDS=0
wait_for 120 "green（複製を作り終わった）" health_is opensearch-1 green 3
os opensearch-1 GET '/_cat/shards/t1000?v&h=index,shard,prirep,state,node'

echo "== 1 台ずつ止める"
for stop in "${NODES[@]}"; do
  ask=$(for n in "${NODES[@]}"; do if [ "$n" != "$stop" ]; then echo "$n"; fi; done | head -1)
  before=$(os "$ask" GET '/_cat/cluster_manager?h=node' | tr -d '[:space:]')
  echo "-- $stop を止める（止める前のまとめ役: $before）"
  SECONDS=0
  dc stop "$stop" >/dev/null 2>&1
  if [ "$stop" = opensearch-cm ]; then want=green; else want=yellow; fi
  wait_for 180 "$want、ノード 2（$ask に聞く）" health_is "$ask" "$want" 2 || os "$ask" GET /_cluster/health | jq -c .
  echo "  まとめ役: $(os "$ask" GET '/_cat/cluster_manager?h=node' | tr -d '[:space:]')"
  os "$ask" GET /_cluster/health | jq -c '{status, number_of_nodes, number_of_data_nodes, active_shards, unassigned_shards}'
  echo "  _count: $(os "$ask" GET /t1000/_count | jq -c .count)、検索のヒット: $(os "$ask" GET '/t1000/_search?track_total_hits=true&size=0' | jq -c .hits.total.value)、n の合計: $(os "$ask" POST '/t1000/_search?size=0' '{"aggs":{"s":{"sum":{"field":"n"}}}}' | jq -c .aggregations.s.value)（1..1000 の合計は 500500）"
  echo "-- $stop を戻す"
  dc start "$stop" >/dev/null 2>&1
  wait_for 240 "green、ノード 3" health_is "$ask" green 3
done

echo "== mmap の数（allow_mmap=false。Fargate の既定 65530 と比べる）"
for n in opensearch-1 opensearch-2 opensearch-cm; do
  echo "  $n: java の /proc/<pid>/maps の行数 $(docker compose exec -T "$n" bash -c 'for p in /proc/[0-9]*; do grep -qa org.opensearch.bootstrap.OpenSearch $p/cmdline 2>/dev/null && wc -l < $p/maps; done' | head -1 | tr -d ' ')"
done
echo "== 終わり"
