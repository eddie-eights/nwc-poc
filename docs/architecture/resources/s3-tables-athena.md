# S3 Tables（Iceberg）と Athena

← [リソースごとの知見](README.md)

## ひとことで

履歴と証跡の置き場。追記だけで、上書きしない。「いま」の状態は Neptune に置き、変わったことの記録をここに 1 行ずつ足す。
Athena は、ここのテーブルをエージェントが SQL で読むための入口（ワークグループ 1 つ）。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| テーブルバケット | `<prefix>-tables`。analytics を作る回はいつも作る（`STORES` に `s3` が無くても） | `terraform/pipeline/analytics/tables.tf` |
| namespace | `netops`（アンダースコアだけ。ハイフンは使えない） | `tables.tf` |
| テーブルの作り方 | Terraform が作る（`ops/down.sh` の destroy でバケットごと消せるように） | `tables.tf` |
| Glue のカタログ | `s3tablescatalog`。アカウントとリージョンに 1 つ。無いときだけ `ops/up.sh` が作り、`ops/down.sh` では消さない | `ops/up.sh` の `ensure_s3tables_catalog`、[deploy.md](../../deploy.md) の「アラートの通知の履歴」 |
| Athena のワークグループ | `<prefix>-history`。設定を強制、1 回のスキャンは 1 GiB で打ち切り、結果は Athena の管理ストレージ | `terraform/pipeline/analytics/history.tf` |
| Athena から見たカタログ名 | `s3tablescatalog/<テーブルバケット>`（analytics の output `athena_catalog`） | `history.tf` の `local.athena_catalog` |
| 閉域 | テーブルバケットのポリシーに、この VPC の外からの呼び出しの Deny | `tables.tf`、[vpc-perimeter.md](vpc-perimeter.md) |
| スイッチ | `STORES` の `s3`（`raw_telemetry` と Spark のジョブ `sinks-s3iceberg` だけがこれに従う）、`SKIP_ANALYTICS=1` | `deploy.env.example` |
| 費用 | テーブルは時間課金なし。`STORES` の `s3` は Spark のジョブ 1 つ分で +$0.21/h。Athena はスキャンした量の課金（PoC の量なら月に数セント） | `ops/up.sh` の先頭のコメント、`deploy.env.example`、`history.tf` のコメント |

テーブルと中身:

| テーブル | 入っているもの | 列 | 書く | 読む |
|---|---|---|---|---|
| `raw_telemetry` | 機器から来た生データの全部（metrics / gnmi / mdt / traps / logs）。up か down かを判断せず、そのまま | Terraform の 8 列（`ts`、`topic`、`measurement`、`agent_host`、`host`、`tags_json`、`fields_json`、`ingested_at`）+ Spark が足す 4 列（`event_id`、`kafka_topic`、`kafka_partition`、`kafka_offset`） | Spark の `iceberg`（ジョブ `sinks-s3iceberg`） | まだ読む側が無い |
| `alert_events` | アラートの通知 1 件が 1 行（発火と解消） | 10 列（`event_id`、`anomaly_id`、`source`、`status`、`device_id`、`kind`、`target`、`detail`、`starts_at`、`received_at`） | Lambda `<prefix>-graph-status` → Firehose | エージェントの `query_history`（Athena） |
| `proposal_events` | 修復案の証跡（作成・承認・却下・適用・確認）。1 段ごとに 1 行 | 12 列（`event_id`、`proposal_id`、`anomaly_id`、`event`、`status`、`device_id`、`action`、`cause`、`command`、`decided_by`、`detail`、`event_time`） | Temporal の worker（PyIceberg） | まだ読む側が無い（証跡） |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| Spark（EMR Serverless） | Spark → `raw_telemetry` | s3tables のエンドポイント（Iceberg REST）、IAM |
| Firehose | Firehose → `alert_events` | Glue の `s3tablescatalog` 越し。ロール `<prefix>-alert-firehose`（サービス側から書くので閉域の Deny の例外） |
| Temporal の worker | worker → `proposal_events` | s3tables のエンドポイント、PyIceberg、タスクロール |
| tools の Lambda（`query_history`） | Lambda → Athena → `alert_events` | athena のエンドポイント、IAM。Athena が呼び手に代わって S3 Tables を読む |

## 知見

- **「いま」と証跡を分けている。**
  Neptune の頂点は書き換わるので、それだけでは「いつ誰が承認したか」を後から追えない。変わるたびにここへ 1 行足し、上書きしない。
  出典: [data-stores.md](../../data-stores.md) の「3. なぜこの分け方か」。
- **生データは、メトリクスもログも同じ 1 つのテーブルに入る。**
  項目がまったく違うので項目ごとの列は作らず、JSON の文字列 2 列（`tags_json`、`fields_json`）に丸ごと入れる。読むときは `topic` で絞ってから JSON を取り出す。
  出典: FAQ「S3 Tables には 1 つのテーブルしかない？ メトリクスもログも 1 つの同じテーブル？」。
- **`raw_telemetry` は 2026-10-04 に `snmp_metrics` から改名した。**
  SNMP のメトリクスだけではないため。
  出典: FAQ「raw_telemetry（旧 snmp_metrics）って何？」。
- **`event_id` などの 4 列は、Terraform ではなく Spark が起動時に `ALTER TABLE` で足す。**
  Terraform のテーブルの定義で列を変えると、テーブルの作り直しになるため。
  出典: `spark/snmp_sinks.py` の `ICEBERG_ADDED_COLUMNS` のコメント。
- **Spark から Iceberg は「ちょうど 1 回」。**
  同じマイクロバッチは 1 回しか確定しない。ただし Telegraf が Kafka に 2 回入れた分は 2 行になる。
  出典: [data-stores.md](../../data-stores.md) の「届け方の保証」。
- **`alert_events` と `proposal_events` は二重に入ることがある。読むときに `event_id` で落とす。**
  `alert_events` は Lambda のやり直しと、Grafana の 4 時間ごとの送り直し、Grafana と Splunk の両方から来た分。`proposal_events` はアクティビティの再試行の分。`query_history` は `event_id` で重複を落とし、新しい順に最大 50 件を返す。
  出典: [data-stores.md](../../data-stores.md) の「4. 気を付けること」、[pipeline.md](../../pipeline.md) の「アラートの履歴」。
- **`alert_events` の `starts_at` は送り手で意味が違う。**
  Grafana は発火した時刻（`resolved` の行も同じ）。Splunk は保存済みサーチの `latest(_time)`（その状態を最後に見た時刻）。届いた時刻は `received_at`。
  出典: 同上。
- **S3 Tables の Iceberg REST には、閉域の Deny に例外が要る。**
  S3 Tables が裏で呼ぶ API には元の VPC が付かないので、`aws:CalledViaLast = s3tables.amazonaws.com` を外してある。
  出典: [core.md](../core.md) の「閉域」。
- **Glue のカタログ `s3tablescatalog` は、ほかの OWNER の環境と共有する。**
  Firehose と Athena は S3 Tables をこのカタログ越しに引く。もうあって設定が違うときは、`ops/up.sh` が注意を出してそのまま使う。消してもテーブルバケットの中身は消えない。
  出典: [deploy.md](../../deploy.md) の「アラートの通知の履歴（Firehose と Athena）」。
- **`ops/down.sh` は証跡も消す。**
  テーブルバケットごと消える。`STORES` から `s3` を外しても `raw_telemetry` は消える。残したいときは消す前に書き出す。
  出典: [data-stores.md](../../data-stores.md) の「4. 気を付けること」。
- **2026-09-24 に DynamoDB をやめて、修復案の証跡をここに寄せた。**
  出典: [data-stores.md](../../data-stores.md) の「5. 経緯: DynamoDB をやめた（2026-09-24）」。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| Firehose と Athena が IAM だけで S3 Tables に届くか、閉域の Deny に当たらないか | AWS の上ではまだ通していない（[deploy.md](../../deploy.md) の「アラートの通知の履歴」） |
| `raw_telemetry` と `proposal_events` | 書くだけで、読む側がまだ無い |
| Web の画面 | Iceberg を読まない（読むのはエージェントの `query_history` だけ） |

このあと変わる予定: 修復案の「いま」も Neptune からここへ移す。[修復案を S3 Tables にまとめる（003）の設計](../../cycles/003-proposals-in-s3tables/design.md)。

## 関連

- [firehose.md](firehose.md)、[emr-serverless.md](emr-serverless.md)、[temporal.md](temporal.md)、[neptune-analytics.md](neptune-analytics.md)
- [data-stores.md](../../data-stores.md): 「データの置き場」「届け方の保証」
- [pipeline.md](../../pipeline.md): 「アラートの履歴」（Athena の SQL の例）
- [alert-comparison.md](../../alert-comparison.md): Splunk と Grafana のアラートを比べる（002）で `alert_events` を集計した結果
- [アラートの履歴を残す（001）の設計](../../cycles/001-alert-history-firehose/design.md)
