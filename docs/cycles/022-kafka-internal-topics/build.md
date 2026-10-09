# OSS 版の Kafka の内部トピックのパーティションを絞る（022）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high（エンジニア2）。ブランチ feat/022-kafka-internal-topics（origin/main 9fb0616 から）。

### 変更ファイル

- `IaC/terraform/oss/pipeline/stream/kafka.tf`: `kafka_environment` に 3 つ（`KAFKA_OFFSETS_TOPIC_NUM_PARTITIONS` / `KAFKA_TRANSACTION_STATE_LOG_NUM_PARTITIONS` / `KAFKA_SHARE_COORDINATOR_STATE_TOPIC_NUM_PARTITIONS` = `"1"`）とコメント 2 行
- `docker/compose/compose.yaml`: `x-kafka-env` に同じ 3 つとコメント 1 行
- `tests/test_local_compose.py`: 13 → 16（check の数は変えない）
- docs 4 ファイルに 1 行ずつ（`docs/oss-variant.md` は Kafka の行と未確認の表の 2 か所）

設計からの逸脱: 無し。

### 検証 1（テスト）

先に 13 のまま走らせた（赤）:

```
AssertionError: kafka.tf の kafka_environment から比べる値を 13 個読めた（読めずに素通りしない）
```

16 に直したあと（緑）:

```
ok kafka.tf の kafka_environment から比べる値を 16 個読めた（読めずに素通りしない。内部トピックのパーティション数 3 つは cycle 022）
通過 138 / 失敗 0
$ tests/test_oss.py
通過 173 / 失敗 0
```

### 検証 2（値と validate）

```
$ grep -c 'NUM_PARTITIONS' IaC/terraform/oss/pipeline/stream/kafka.tf docker/compose/compose.yaml
IaC/terraform/oss/pipeline/stream/kafka.tf:4
docker/compose/compose.yaml:4
$ terraform -chdir=IaC/terraform/oss/pipeline/stream init -backend=false -lockfile=readonly && terraform … validate
Success! The configuration is valid.
$ terraform fmt -check IaC/terraform/oss/pipeline/stream   # rc=0
```

### 検証 3（手元の compose。Mac の Docker 29.4.2 で Kafka 3 台が立った）

着手前は `git show origin/main:docker/compose/compose.yaml` を `-p nwc-022b`、着手後は今の `compose.yaml` を `-p nwc-022` で、design.md の手順のとおり打った（`--env-file` は `.env.example` の写し）。

```
## nwc-022b (docker/compose/.compose-before-022.yaml)
 Container nwc-022b-kafka-2-1 Started 
 Container nwc-022b-kafka-1-1 Started 
 Container nwc-022b-kafka-3-1 Started 
Created topic t022.
org.apache.kafka.common.errors.TimeoutException
Processed a total of 1 messages
Topic: __consumer_offsets	TopicId: g1Dsg-nZRWqIkgCCc5OFbA	PartitionCount: 50	ReplicationFactor: 3	Configs: compression.type=producer,min.insync.replicas=2,cleanup.policy=compact,segment.bytes=104857600
  offsets.topic.num.partitions=50 sensitive=false synonyms={DEFAULT_CONFIG:offsets.topic.num.partitions=50}
  share.coordinator.state.topic.num.partitions=50 sensitive=false synonyms={DEFAULT_CONFIG:share.coordinator.state.topic.num.partitions=50}
  transaction.state.log.num.partitions=50 sensitive=false synonyms={DEFAULT_CONFIG:transaction.state.log.num.partitions=50}
 Network nwc-local Removed 
volume: なし
## nwc-022 (docker/compose/compose.yaml)
 Container nwc-022-kafka-1-1 Started 
 Container nwc-022-kafka-2-1 Started 
 Container nwc-022-kafka-3-1 Started 
Created topic t022.
org.apache.kafka.common.errors.TimeoutException
Processed a total of 1 messages
Topic: __consumer_offsets	TopicId: fueYPibHSDSU1hNiTabujQ	PartitionCount: 1	ReplicationFactor: 3	Configs: compression.type=producer,min.insync.replicas=2,cleanup.policy=compact,segment.bytes=104857600
  offsets.topic.num.partitions=1 sensitive=false synonyms={STATIC_BROKER_CONFIG:offsets.topic.num.partitions=1, DEFAULT_CONFIG:offsets.topic.num.partitions=50}
  share.coordinator.state.topic.num.partitions=1 sensitive=false synonyms={STATIC_BROKER_CONFIG:share.coordinator.state.topic.num.partitions=1, DEFAULT_CONFIG:share.coordinator.state.topic.num.partitions=50}
  transaction.state.log.num.partitions=1 sensitive=false synonyms={STATIC_BROKER_CONFIG:transaction.state.log.num.partitions=1, DEFAULT_CONFIG:transaction.state.log.num.partitions=50}
 Network nwc-local Removed 
volume: なし
```

- 着手前は `PartitionCount: 50`、着手後は `PartitionCount: 1` / `ReplicationFactor: 3`。3 つの設定は着手後 `STATIC_BROKER_CONFIG` で 1（環境変数の名前の規則は合っていた。未確定 2 は解消）。
- `down -v` のあと `docker volume ls` に `nwc-022` は無い。`TimeoutException` は consumer の `--timeout-ms` で抜けたときのもの（1 件読めている）。

### 検証 4（docs）

```
$ grep -c '__consumer_offsets' docs/oss-variant.md docker/compose/README.md docs/faq-fukuda-nwc-poc.md docs/architecture/resources/msk.md
docker/compose/README.md:1
docs/oss-variant.md:2
docs/faq-fukuda-nwc-poc.md:1
docs/architecture/resources/msk.md:1
```

### 検証 5（AWS）

未実行（範囲外）。`docs/oss-variant.md` の未確認の表の Kafka の行に足した。

### セルフレビュー

- 自分: opus-5.5 / effort high（`/robust`）。反対弁護人: opus（general-purpose、文脈あり、読み取り専用。返ったあと `git status --porcelain -uall` で増えたファイル無し）
- 変異（テストが縛っているかを実測）:
  - compose の `KAFKA_SHARE_COORDINATOR_STATE_TOPIC_NUM_PARTITIONS` を 2 → `AssertionError: kafka-1: EXTERNAL 以外の KAFKA_*（…）は kafka.tf と同じ値`
  - compose の `KAFKA_TRANSACTION_STATE_LOG_NUM_PARTITIONS` の行を消す → 同じ AssertionError
  - compose.yaml を戻して 通過 138 / 失敗 0

| # | 分類 | 観点 | 場所 | 破綻シナリオ | 片付け |
| :-- | :-- | :-- | :-- | :-- | :-- |
| S1 | Should（data loss） | data loss | `docker/compose/README.md:82`、`docs/oss-variant.md:26` | 「前の volume には効かないので `down -v`」を読んだ人が、Splunk・OpenSearch・Grafana・Prometheus・Spark の checkpoint まで消す。consumer group は空（`docs/verification/20261008-oss-aws.md:196`）なので、ふつうは `__consumer_offsets` がまだ無く、消さなくても 1 で作られる | 直した。「最初に作られたときに決まる」「`kafka-topics.sh --describe` で 50 で出来ているか見る」「そのときだけ `docker/compose/down.sh -v`、全部消える」と書いた |
| S2 | Should | design 整合性 | `docs/oss-variant.md:99` | AWS で consumer group を作らないと `__consumer_offsets` が出来ず、未確認の項目を確かめられない（`kafka-configs --describe --all` で STATIC_BROKER_CONFIG=1 を見る手もある） | 直さず PM に報告 |
| S3 | Should | 読みやすさ | `docs/faq-fukuda-nwc-poc.md:702` | 足した行が「つまり、いまはパーティション 2 つを…」の前に入り、「つまり」が何を受けるか崩れる | 直さず PM に報告 |
| N1 | Nit | コメント | `kafka.tf:88` | 「3 に戻す」は元の 50 と合わない。あとから増やすときは `--alter` では group の割り当てが崩れるので作り直しになる | 報告のみ |
| N2 | Nit | runtime | `ops/oss/roll-nodes.sh` | 入れ替えの途中に初めて group を使うと、50 と 1 のブローカーが混ざって NOT_COORDINATOR になりうる。PoC では group が無い | 報告のみ |
| N3 | Nit | missing tests | `tests/test_local_compose.py:132` | 数（16）と tf→compose の一致だけ。両方 50 にしても通る | 報告のみ |
| N4 | Nit | 検証手順 | design.md 検証 3 | `-p nwc-022` でもネットワーク名は `nwc-local` 固定で、手元の本物の stack と 9094〜9096 がぶつかる | 報告のみ |
| N5 | Nit | 体裁 | `docker/compose/README.md:82` | URL の表の直後で見つけにくい | 報告のみ |

- Must fix 0
- 反対弁護人への答え（問題なしとした観点）: coordinator が 1 台に寄る件は group が無いので影響なし（S1 の行の根拠と同じ）。MSK で変えられない件は AWS の docs で確かめた（MSK の「既定 50」は推定で実測していない）。Kafbat UI と Spark は内部トピックの数に依らない（読んだだけ）
- S1 を直したあと取り直し:

```
$ uv run --group dev --group web python tests/test_local_compose.py
ok kafka.tf の kafka_environment から比べる値を 16 個読めた（読めずに素通りしない。内部トピックのパーティション数 3 つは cycle 022）
通過 138 / 失敗 0
$ uv run --group dev --group web python tests/test_oss.py
通過 173 / 失敗 0
```

### PM の指摘で直したもの（PR #18 のマージ前）

- S3: FAQ の 1 行を箇条書きの末尾へ動かし、2 文にした（「つまり」が表を受ける並びに戻した）
- S2: `docs/oss-variant.md:99` の未確認を「`kafka-configs.sh --describe --entity-type brokers --entity-name 1 --all`（または Kafbat UI の Brokers）で `offsets.topic.num.partitions` が STATIC_BROKER_CONFIG の 1」に書き換えた
- N1: `kafka.tf` のコメント「3 に戻す」を「作り直して増やす（既定は 50）」にした
- `docker/compose/README.md` の段落を、太字の見出し行と箇条書き 4 つに分けた（中身は S1 のまま）

```
$ uv run --group dev --group web python tests/test_local_compose.py
ok kafka.tf の kafka_environment から比べる値を 16 個読めた（読めずに素通りしない。内部トピックのパーティション数 3 つは cycle 022）
通過 138 / 失敗 0
$ uv run --group dev --group web python tests/test_analytics.py
通過 513 / 失敗 0
$ uv run --group dev --group web python tests/test_oss.py
通過 173 / 失敗 0
$ terraform fmt -check IaC/terraform/oss/pipeline/stream   # rc=0
```
