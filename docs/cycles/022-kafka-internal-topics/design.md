# OSS 版の Kafka の内部トピックのパーティションを絞る（022）

設計: PM(fable-5.1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

ユーザーの指摘（2026-10-09）: 「OSS の Kafka がデフォルトでトピックが 50 とかになってた気がする。Kafka のメトリクス用な気がする。PoC では最小限でいい」。

Kafbat UI で見える「50」は、Kafka が自分のために作る**内部トピック**のパーティション数。`__consumer_offsets`（consumer group の offset の置き場。`offsets.topic.num.partitions`、既定 50）、`__transaction_state`（`transaction.state.log.num.partitions`、既定 50）、Kafka 4.x の share coordinator の `__share_group_state`（`share.coordinator.state.topic.num.partitions`、既定 50）。メトリクスのトピックではない。機器のデータのトピック（`metrics` / `gnmi` / `traps` / `logs` / `flows`）は `num.partitions=2` で作られていて、こちらは問題ない。

PoC で consumer group を使うのは Spark の 3 ジョブと Kafbat UI くらいで、Spark の Structured Streaming は offset を自分の checkpoint に持ち `__consumer_offsets` にはほとんど書かない。トランザクションと share group は使っていない。**3 つとも 1 パーティションに絞る。** 3 台で 50 × 3 = 150 のパーティションのレプリカが減り、Kafbat UI の表示と起動時のメタデータが軽くなる。

### 調査で分かった事実（2026-10-09、origin/main 4513ecd）

- OSS 版の Kafka は `apache/kafka` の公式イメージ（手元 `docker/compose/compose.yaml:144` は `apache/kafka:4.3.1`、ECS は ECR への写しで `var.kafka_image_tag`）。設定は `KAFKA_*` の環境変数が `server.properties` になる。**1 つでも書くと既定のファイルは使われないので、要るものは全部書く**という方式で、今は `IaC/terraform/oss/pipeline/stream/kafka.tf:66-92` の `kafka_environment` と `docker/compose/compose.yaml:18-43` の `x-kafka-env` が同じ値を持つ。
- どちらにも `*_NUM_PARTITIONS`（内部トピックの分）は**無い**（`grep -rn 'OFFSETS_TOPIC_NUM_PARTITIONS\|__consumer_offsets' IaC docker app ops tests` → 0 件）。既定の 50 が効いている。
- `tests/test_local_compose.py:124-150` が kafka.tf の `kafka_environment` を正規表現 `\{\s*name\s*=\s*"(\w+)",\s*value\s*=\s*"([^"]*)"\s*\}` で読み、`KAFKA_LISTENERS` / `KAFKA_LISTENER_SECURITY_PROTOCOL_MAP` / `KAFKA_LOG_RETENTION_HOURS` と `${` を含む値を除いた **13 個**が compose の kafka-1〜3 と一致することを見る（`check("kafka.tf の kafka_environment から比べる値を 13 個読めた…", len(_same) == 13 …)`）。環境変数を 3 つ足すと 16 になる。2026-10-09 の実測は 通過 138 / 失敗 0。
- MSK（マネージド版）は configuration の `server_properties` に `offsets.topic.num.partitions` を**書けない**（MSK の custom configuration で変えられる項目の一覧に無い。2026-10-09 に AWS の文書で確認）。マネージド版は 50 のまま。
- 内部トピックは**最初に使われたときに作られる**（`__consumer_offsets` は consumer group が最初に参加したとき）。既に作られたクラスターでは、設定を変えても既存のトピックのパーティション数は変わらない。OSS 版のデータは EFS（`kafka.tf:2`）、手元は named volume（`compose.yaml:150,159,168`）に残るので、効かせるには消して作り直す。2026-10-09 時点で AWS の環境は消えている。
- compose の Kafka 3 台だけなら Mac でも立つはず（`apache/kafka` は multi-arch）。ただし `compose.yaml` のほかのサービスが `.env` の値を要求するので、`docker compose --env-file` で `.env.example` の写しを渡す。**Mac で立つことは未確認。**

## 設計方針

1. **値は 3 つとも 1。** `offsets.topic.num.partitions=1`、`transaction.state.log.num.partitions=1`、`share.coordinator.state.topic.num.partitions=1`。レプリケーション係数（3）と min ISR（2）は今のまま。
2. **kafka.tf と compose.yaml の両方に同じ値を書く**（今の方式どおり。テストが突き合わせる）。
   - `kafka.tf` の `kafka_environment` に `{ name = "KAFKA_OFFSETS_TOPIC_NUM_PARTITIONS", value = "1" }`、`{ name = "KAFKA_TRANSACTION_STATE_LOG_NUM_PARTITIONS", value = "1" }`、`{ name = "KAFKA_SHARE_COORDINATOR_STATE_TOPIC_NUM_PARTITIONS", value = "1" }` を、`*_REPLICATION_FACTOR` の隣に足す。テストの正規表現に合う形（`name` と `value` が文字列）で書く。コメントに「内部トピック。既定 50。PoC では 1（MSK は項目に無く変えられない）」を 1 行。
   - `compose.yaml` の `x-kafka-env` に同じ 3 つを足す。
3. **テストの数を 13 → 16 にする。** `tests/test_local_compose.py` の check の文言と条件を直す。新しい 3 つが compose と kafka.tf で同じ値であることは、既存の突き合わせがそのまま見る。
4. **docs は 1 行ずつ。** `docs/oss-variant.md` の Kafka の行（`:26`）に「内部トピック（`__consumer_offsets` 等）は 1 パーティション」、`docker/compose/README.md` の Kafka の説明に同じ 1 行、`docs/faq-fukuda-nwc-poc.md:699` の表の下に「OSS 版と手元の compose では内部トピックも 1 に絞る。MSK は configuration に項目が無く既定の 50」を 1 文。`docs/architecture/resources/msk.md` は MSK の説明なので「MSK は変えられない」を 1 文だけ。
5. **やらないこと。** `num.partitions`（データのトピック。2）の変更、MSK 側の変更、既存クラスターの内部トピックの作り直しの手順（環境は消して作る前提）、Kafka の版上げ。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `IaC/terraform/oss/pipeline/stream/kafka.tf:66-92` | `kafka_environment` に 3 つ足す + コメント 1 行 |
| `docker/compose/compose.yaml:18-43` | `x-kafka-env` に同じ 3 つ |
| `tests/test_local_compose.py:124-150` | 13 → 16 |
| `docs/oss-variant.md` `docker/compose/README.md` `docs/faq-fukuda-nwc-poc.md` `docs/architecture/resources/msk.md` | 各 1 行 |

## 再利用するもの

- `tests/test_local_compose.py` の kafka.tf と compose の突き合わせ（新しい値も自動で比べる）。
- `docker/compose/.env.example`（compose を Mac で立てるときの `--env-file` の元）。

## 実装ステップ

1. kafka.tf と compose.yaml に 3 つ足す。`terraform -chdir=IaC/terraform/oss/pipeline/stream validate`（AWS には触らない。`-lockfile=readonly` で init が要るなら `ops/check.sh` の形に合わせる）。
2. `tests/test_local_compose.py` を 16 にする。先に 13 のまま走らせて落ちること（赤）を見てから直す（緑）。
3. compose で Kafka 3 台だけ立てて、内部トピックのパーティションを見る（下の検証 3）。Mac で立たなければ、立たなかったログを `build.md` に貼って「未確認」にする（WSL の通し検証はユーザー）。
4. docs の 4 行。
5. 検証を全部回して `build.md` に出力を貼る。セルフレビュー（`/robust`）。

## 検証方法（期待出力まで）

1. テスト。
   ```
   uv run --group dev --group web python tests/test_local_compose.py   # 通過 138 / 失敗 0（check の数は変えず、13 → 16 の条件だけ変える）
   uv run --group dev --group web python tests/test_oss.py             # 通過 173 / 失敗 0
   ```
2. kafka.tf と compose の値が同じ。`grep -c 'NUM_PARTITIONS' IaC/terraform/oss/pipeline/stream/kafka.tf docker/compose/compose.yaml` が **4 と 4**（既存の `KAFKA_NUM_PARTITIONS` + 3）。`terraform -chdir=IaC/terraform/oss/pipeline/stream validate` が Success。
3. 実際に 1 になる（手元の compose）。
   ```
   cp docker/compose/.env.example <scratchpad>/compose.env
   docker compose -f docker/compose/compose.yaml --env-file <scratchpad>/compose.env -p nwc-022 up -d kafka-1 kafka-2 kafka-3
   docker compose -f docker/compose/compose.yaml --env-file <scratchpad>/compose.env -p nwc-022 exec kafka-1 /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka-1:9092 --create --topic t022 --partitions 1 --replication-factor 3
   docker compose … exec kafka-1 sh -c 'echo x | /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server kafka-1:9092 --topic t022'
   docker compose … exec kafka-1 /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka-1:9092 --topic t022 --group g022 --from-beginning --timeout-ms 10000
   docker compose … exec kafka-1 /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka-1:9092 --describe --topic __consumer_offsets | head -1
   ```
   期待: 最後の行に `PartitionCount: 1` と `ReplicationFactor: 3`（着手前の同じ手順では `PartitionCount: 50`。着手前にも 1 回打って `build.md` に貼る）。終わったら `docker compose … -p nwc-022 down -v` で volume ごと消し、`docker volume ls | grep nwc-022` が空。
4. docs。`grep -n '__consumer_offsets' docs/oss-variant.md docker/compose/README.md docs/faq-fukuda-nwc-poc.md docs/architecture/resources/msk.md` が 4 ファイルとも 1 行以上。
5. AWS では確かめない（このサイクルの範囲外。次に OSS 版を AWS で立てたときに Kafbat UI で `__consumer_offsets` の Partitions が 1 であることを見る。`docs/oss-variant.md` の未確認の表に 1 行足す）。

## 未確定事項とリスク

1. **Mac で compose の Kafka 3 台が立つかは未確認。** 立たなければ検証 3 は WSL でユーザーに頼む。設定の正しさは 1・2 で担保する。
2. **`KAFKA_SHARE_COORDINATOR_STATE_TOPIC_NUM_PARTITIONS` の環境変数名は Kafka の `share.coordinator.state.topic.num.partitions` から公式イメージの規則（`KAFKA_` + 大文字 + `.` → `_`）で導いた。** 既存の `KAFKA_SHARE_COORDINATOR_STATE_TOPIC_REPLICATION_FACTOR` が効いているので規則は同じはず。検証 3 の `--describe` のあとに `kafka-configs.sh --describe --entity-type brokers --entity-name 1 --all | grep num.partitions` で 3 つとも 1 になっていることを見る。
3. **`__consumer_offsets` が 1 パーティションだと、group coordinator が 1 台に寄る。** PoC の consumer group は数個なので問題にしない。台数を増やす構成に変えるときは 3 に戻す（コメントに書く）。
4. **既に立っている OSS 版や手元の volume には効かない。** 新しく作るときだけ。`docs/oss-variant.md` の 1 行に「作り直したときから」と添える。
