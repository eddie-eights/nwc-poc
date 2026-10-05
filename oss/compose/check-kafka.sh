#!/usr/bin/env bash
# 005 ステップ 1 の 1: Kafka 3 台（KRaft、どの台も broker と controller）が公式イメージの環境変数だけで組めるか。
# 複製数 3（min.insync.replicas 2）のトピックに 1000 件入れ、controller のリーダーの台を止めても 1000 件全部読めるか。
# Kafbat UI（profile kafka-ui）も一緒に起こし、ブローカーの台数（3 → 2）、トピック、lag、API からのトピック作成を見る。
# 使い方: oss/compose/check-kafka.sh（終わってもコンテナは残す。片付けは docker compose --profile '*' down -v）
set -euo pipefail
cd "$(dirname "$0")"

dc() { docker compose --profile kafka-ui "$@"; }
kbin() { local node=$1; shift; dc exec -T "$node" "/opt/kafka/bin/$@"; }
BOOT=kafka-1:9092,kafka-2:9092,kafka-3:9092
UI=http://127.0.0.1:18080/api/clusters/nwc

wait_for() {   # wait_for <秒> <説明> <コマンド…>
  local limit=$1 what=$2; shift 2
  for _ in $(seq 1 "$limit"); do
    if "$@" >/dev/null 2>&1; then echo "  ok: $what"; return 0; fi
    sleep 1
  done
  echo "  タイムアウト: $what" >&2; return 1
}

echo "== 3 台を起こす"
dc up -d kafka-1 kafka-2 kafka-3 kafka-ui
wait_for 120 "quorum が答える" kbin kafka-1 kafka-metadata-quorum.sh --bootstrap-server "$BOOT" describe --status
kbin kafka-1 kafka-metadata-quorum.sh --bootstrap-server "$BOOT" describe --status
kbin kafka-1 kafka-metadata-quorum.sh --bootstrap-server "$BOOT" describe --replication
echo "== 書き出された server.properties（kafka-1）"
dc exec -T kafka-1 grep -E "^(process.roles|controller.quorum|node.id|default.replication|min.insync|log.dirs)" /opt/kafka/config/server.properties

echo "== トピック t1000（3 パーティション、複製数 3、min.insync.replicas 2）に 1000 件"
kbin kafka-1 kafka-topics.sh --bootstrap-server "$BOOT" --create --if-not-exists --topic t1000 --partitions 3 --replication-factor 3 --config min.insync.replicas=2
seq 1 1000 | kbin kafka-1 kafka-console-producer.sh --bootstrap-server "$BOOT" --topic t1000 --producer-property acks=all
kbin kafka-1 kafka-get-offsets.sh --bootstrap-server "$BOOT" --topic t1000

LEADER=$(kbin kafka-1 kafka-metadata-quorum.sh --bootstrap-server "$BOOT" describe --status | awk -F': *' '/^LeaderId/ {print $2}' | tr -d '[:space:]')
STOP=kafka-$LEADER
ALIVE=$(echo "$BOOT" | tr ',' '\n' | grep -v "^$STOP:" | paste -sd, -)
ALIVE_NODE=$(echo "$ALIVE" | cut -d: -f1)
echo "== controller のリーダーは $LEADER。$STOP を止める"
echo "== 止める前の Kafbat UI（ブローカー）"
wait_for 90 "Kafbat UI がブローカー 3 台を返す" bash -c "curl -sf $UI/brokers | jq -e 'length == 3'"
curl -sf "$UI/brokers" | jq -c '[.[] | {id, host, port}]'
dc stop "$STOP"
wait_for 60 "新しいリーダーが選ばれる" bash -c "docker compose exec -T $ALIVE_NODE /opt/kafka/bin/kafka-metadata-quorum.sh --bootstrap-server $ALIVE describe --status | grep -v \"^LeaderId: *$LEADER\$\" | grep -q '^LeaderId'"
kbin "$ALIVE_NODE" kafka-metadata-quorum.sh --bootstrap-server "$ALIVE" describe --status | grep -E "LeaderId|LeaderEpoch|CurrentVoters|CurrentObservers"
kbin "$ALIVE_NODE" kafka-topics.sh --bootstrap-server "$ALIVE" --describe --topic t1000

TMP=$(mktemp)
echo "== 1 台止めたまま、1000 件を最初から読む（コンシューマーグループ check-1000）"
kbin "$ALIVE_NODE" kafka-console-consumer.sh --bootstrap-server "$ALIVE" --topic t1000 --from-beginning --group check-1000 --timeout-ms 30000 > "$TMP" 2>/dev/null || true
echo "  読めた行: $(wc -l < "$TMP" | tr -d ' ')、重複を除くと $(sort -un "$TMP" | wc -l | tr -d ' ')、1..1000 との差: $(comm -3 <(seq 1 1000 | sort) <(sort -u "$TMP") | wc -l | tr -d ' ')"
rm -f "$TMP"

echo "== 1 台止めたまま書けるか（acks=all、ISR 2 = min.insync.replicas 2）: 50 件足して lag を作る"
seq 1001 1050 | kbin "$ALIVE_NODE" kafka-console-producer.sh --bootstrap-server "$ALIVE" --topic t1000 --producer-property acks=all
kbin "$ALIVE_NODE" kafka-consumer-groups.sh --bootstrap-server "$ALIVE" --describe --group check-1000

echo "== 止めているあいだの Kafbat UI"
wait_for 120 "Kafbat UI がブローカー 2 台を返す" bash -c "curl -sf $UI/brokers | jq -e 'length == 2'"
curl -sf "$UI/brokers" | jq -c '[.[] | {id, host, port}]'
curl -sf http://127.0.0.1:18080/api/clusters | jq -c '[.[] | {name, status, brokerCount, onlinePartitionCount, topicCount}]'

echo "== $STOP を戻す"
dc start "$STOP"
wait_for 120 "t1000 の ISR が 3 台に戻る" bash -c "docker compose exec -T kafka-1 /opt/kafka/bin/kafka-topics.sh --bootstrap-server $BOOT --describe --topic t1000 --under-replicated-partitions | grep -c Partition | grep -qx 0"
kbin kafka-1 kafka-metadata-quorum.sh --bootstrap-server "$BOOT" describe --status | grep -E "LeaderId|CurrentVoters"

echo "== Kafbat UI: トピック、lag、API（画面が使うのと同じ）からのトピック作成"
wait_for 120 "Kafbat UI がブローカー 3 台に戻る" bash -c "curl -sf $UI/brokers | jq -e 'length == 3'"
curl -sf "$UI/topics?page=1&perPage=50&showInternal=false" | jq -c '[.topics[] | {name, partitionCount, replicationFactor, underReplicatedPartitions}]'
curl -sf "$UI/consumer-groups/paged?page=1&perPage=25" | jq -c '[.consumerGroups[] | {groupId, state, members, consumerLag, messagesBehind}]'
curl -sf -X POST -H 'Content-Type: application/json' "$UI/topics" -d '{"name":"ui-created","partitions":3,"replicationFactor":3,"configs":{"min.insync.replicas":"2"}}' | jq -c '{name, partitionCount, replicationFactor}'
kbin kafka-1 kafka-topics.sh --bootstrap-server "$BOOT" --describe --topic ui-created
echo "  API から送る: $(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' "$UI/topics/ui-created/messages" -d '{"partition":0,"key":"k1","value":"from-ui","keySerde":"String","valueSerde":"String"}')"
curl -sf "$UI/topics/ui-created/messages/v2?mode=EARLIEST&limit=5" | grep '"type":"MESSAGE"' | jq -Rc 'sub("^data:";"") | fromjson | .message | {offset, key, value}'

echo "== Kafbat UI を認証なしで開けるか（AUTH_TYPE を書いていない）"
echo "  / : $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/)、/api/clusters : $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/api/clusters)"
echo "== 終わり"
