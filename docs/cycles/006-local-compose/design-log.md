# Cycle 006 local-compose 設計の経緯

## Round 0（2026-10-08）要件を詰めた

会話で合意した範囲（置き場所 `local/compose/`、Telegraf 1、Kafka 3 + Kafbat UI 1、Spark 1 イメージ 2 ジョブ、OpenSearch 1、Prometheus 1、Splunk 1、Grafana 1、Nautobot / Neo4j / Temporal / Web / エージェントは入れない、containerlab は compose の外、named volume）を前提に、残りの 5 問を出した。ユーザーの答えは「判断は一旦君に任せる。違うところが出てきたらまた修正依頼する」。以下は PM が置いた決定。

| 質問 | 推奨 | 決定 |
|---|---|---|
| Q1 Telegraf の置き方 | 最初の案は compose のネットワークに置いて DNAT | **host ネットワークに変更。** デバッグ用の EC2（`cloudformation/lab-debug.yaml`）と同じ形になり、`lab/lab.sh forward` の `TELEGRAF_IMAGE` 分岐（trap の REDIRECT だけ）がそのまま使える。DNAT 案は stream（ECS の NLB）向けの規則を手元用に書き直すことになる |
| Q2 lab のイメージの取り方 | ghcr.io から直接 | **採用。** `pull` は `REGISTRY` が無ければ ECR の login を飛ばす。SR Linux と multitool の版は `ops/lab-common.sh` と同値 |
| Q3 Kafka と host の Telegraf のつなぎ | EXTERNAL リスナーを足す | **採用。** 各ブローカーに `EXTERNAL://:909N` を足し host へ publish、advertised は `localhost:909N` |
| Q4 Prometheus の版 | `prom/prometheus:v3.15.0`（最新の安定版） | **採用。** `--web.enable-remote-write-receiver`。VictoriaMetrics を手元でも使う案は、OSS 版と同じになって「マネージド版に近い形」を手元で見られなくなるので採らない |
| Q5 シークレットの渡し方 | `.env` に試し用の固定値 | **採用。** `oss/compose` と同じ流儀。本物のシークレットは入れない |

### 却下した案

- **`oss/compose/compose.yaml` を `include` で取り込む。** 最初はそのつもりだったが、`oss/compose` の Kafka は `profiles` 付きで EXTERNAL リスナーが無く、`include` では service の環境変数を足せない（上書きは `extends` でも profile と networks の扱いが変わる）。写して EXTERNAL を足す方が短い。`oss/compose` は変えない。
- **Telegraf を EC2 と同じく `docker run`（`lab/lab.sh telegraf run`）で動かす。** compose の外に出ると `up.sh` / `down.sh` が 2 系統になる。compose の `network_mode: host` で同じ結果になる。
- **Splunk を入れない / Grafana だけ。** ユーザーが「grafana と splunk も 1 つずつ」「spark も入れる」と決めている。
- **Iceberg（S3 Tables）を MinIO で代替。** 手元で見たいのは Grafana と Splunk までの流れで、Iceberg は Athena から読むためのもの。入れると MinIO + REST カタログが増える。入れない。
- **Nautobot / Neo4j / Temporal / Web を入れる。** ユーザー決定で入れない（「compose は nautobot 要らない」）。
