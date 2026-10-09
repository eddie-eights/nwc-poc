# EMR Serverless（Spark）

← [リソースごとの知見](README.md)

## ひとことで

MSK のトピックを読み、格納先（S3 Tables、OpenSearch、Prometheus、Splunk）へ 60 秒ごとに書く Spark のストリーミングジョブの実行環境。
ジョブは格納先のまとまりごとに 3 つ。Spark は書くだけで、異常の検知はしない（検知は Grafana と Splunk）。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| アプリケーション | `<prefix>-spark`。Spark、`emr-7.14.0`、ARM64。pre-initialized capacity なし | `IaC/terraform/aws-managed/pipeline/analytics/emr.tf`、変数 `emr_release_label` |
| 上限 | 12 vCPU / 48 GB。アイドル 15 分で自動停止 | 変数 `max_cpu`、`max_memory`、`idle_timeout_minutes` |
| AZ の数 | `EMR_AZ_NUM`（既定 1）。サブネットを a から渡す | `ops/up.sh`、変数 `emr_az_num` |
| ジョブ | `sinks-s3iceberg`（`STORES` の `s3`）、`sinks-splunk`（`splunk`）、`sinks-grafana`（`grafana`。OpenSearch と Prometheus）。1 つ driver 1 + executor 2 = 3 vCPU | `ops/up.sh` の手順 7-5、`app/spark/snmp_sinks.py` |
| ジョブの起こし方 | Terraform のリソースではない。`ops/up.sh` が `start-job-run` で起こす（STREAMING モード） | `ops/up.sh` の手順 7-5 |
| 周期と上限 | トリガー 60 秒。1 回に読む件数の上限 `MAX_OFFSETS_PER_TRIGGER`（既定 10000、0 で上限なし。格納先ごとの値も書ける） | `app/spark/snmp_sinks.py` の `TRIGGER`、`deploy.env.example` |
| HTTP の送り方 | `HTTP_SEND`（既定 `driver`。`executor` でパーティションごとに送る）。タイムアウト 30 秒、5xx と接続の失敗は 3 回まで、まとまりは 500 件 | `app/spark/snmp_sinks.py` の `HTTP_TIMEOUT`、`HTTP_RETRIES`、`BULK_SIZE` |
| チェックポイント | `s3://<バケット>/analytics/checkpoint/<MSK クラスタの uuid>/`。クエリごとに別 | [pipeline.md](../../pipeline.md) の「Spark を確かめる」 |
| ログ | `/aws/emr-serverless/<prefix>`、保存 7 日 | `emr.tf` の `aws_cloudwatch_log_group.emr` |
| スイッチ | `STORES`（既定 `s3,grafana,splunk`）、`SKIP_ANALYTICS=1` | `deploy.env.example` |
| 費用 | ジョブ 1 つ 21 セント/時（3 vCPU。3 つで 63）。動いているあいだだけ | `ops/up.sh` の費用の目安（手順 0 の終わりのコメントと `COST_CENTS`。単価は 2026-09-17 確認） |

クエリ（格納先）と読むトピック:

| ジョブ | クエリ | 読むトピック | 書く先 |
|---|---|---|---|
| `sinks-s3iceberg` | iceberg | metrics / gnmi / traps / logs / flows | S3 Tables の `raw_telemetry` |
| `sinks-grafana` | prometheus | metrics / gnmi | Amazon Managed Prometheus（remote write） |
| `sinks-grafana` | opensearch | traps / logs / flows | OpenSearch Serverless の index `snmp-logs` |
| `sinks-splunk` | splunk | metrics / gnmi / traps / logs / flows | Splunk の HEC |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| MSK | Spark ← MSK | 9098/tcp、SASL_SSL + AWS_MSK_IAM |
| MSK（ACL） | Spark → MSK | 9098/tcp、同じ認証。起動のたびに収集器のユーザーごとに自分のトピックの `WRITE` と `DESCRIBE`（`User:syslog-ng` → `logs`、`User:goflow2` → `flows`、`User:gnmic` → `gnmi` / `metrics`。cycle 031）を AdminClient の `createAcls` で入れる（`ensure_acls`。実行ロールの `kafka-cluster:AlterCluster`） |
| S3 Tables | Spark → テーブル | s3tables のエンドポイント（Iceberg REST）、IAM |
| Prometheus | Spark → ワークスペース | aps-workspaces のエンドポイント、remote write（protobuf + snappy を自前で組む）、SigV4 |
| OpenSearch Serverless | Spark → コレクション | OpenSearch Serverless の VPC エンドポイント、SigV4、`_bulk` |
| Splunk | Spark → HEC | 8088/tcp、`/services/collector/event`、HEC の token（SSM の SecureString） |
| S3 | Spark ↔ バケット | スクリプトとチェックポイント（`analytics/`） |

## 知見

- **ジョブを格納先ごとに 3 つに分けている。**
  1 つの格納先が遅れたり落ちたりしても、ほかの格納先の読み進みを止めないため。クエリが 1 つ止まると、そのクエリのいるジョブだけを終わらせ（exit 1）、EMR Serverless が起こし直す。
  出典: FAQ「大量のデータでは、格納先ごとに Spark のジョブを分けたほうがいい？」、[pipeline.md](../../pipeline.md) の「Spark を確かめる」。
- **同じ名前のジョブは同時に 1 本だけ。**
  同じチェックポイントを 2 本で書くと壊れる。`ops/up.sh` はスクリプトと引数のハッシュをタグ `SpecHash` に付け、同じなら何もせず、違えばそのジョブだけ止めて起こし直す。
  出典: [pipeline.md](../../pipeline.md) の「Spark を確かめる」。
- **60 秒は「溜まったら早く送る」ではなく、きっかり 60 秒ごと。**
  出典: FAQ「60 秒周期になっているけど、量が溜まったら 60 秒より前に送る？」。
- **`maxOffsetsPerTrigger` が効くのは、止めていたジョブを起こし直した直後。**
  ふだんの 60 秒分より十分大きい。HTTP の格納先は 1 回分を driver（2g）に集めて送るので、溜まった分を一度に読まないための歯止め。
  出典: `deploy.env.example` の `MAX_OFFSETS_PER_TRIGGER`、FAQ「`maxOffsetsPerTrigger` は、格納先がどのくらいの量を処理できるかで決まる？」。
- **4xx は送り直さずに捨て、数をログに出す。**
  送り直しても通らないため。5xx と接続の失敗だけ 3 回まで送り直す。
  - それでも駄目ならバッチ（`executor` ではパーティション）を頭からやり直すので、HTTP の格納先には重複が残りうる。
  - 出典: [data-stores.md](../../data-stores.md) の「届け方の保証」。
- **イベントに一意の番号 `event_id` を付けている。**
  Kafka のメッセージの中身（JSON を解く前のバイト列）の SHA-256。
  どのジョブが何回読んでも同じ値になるので、S3 Tables、OpenSearch、Splunk の同じイベントを突き合わせられる。
  - Prometheus のラベルには入れない（サンプルごとに系列ができてしまう）。
  - 出典: FAQ「Spark のジョブが 3 つに分かれているので、同じイベントでも番号（event_id）が変わることはある？」、`app/spark/snmp_sinks.py` の `ICEBERG_ADDED_COLUMNS` まわり。
- **`executor` で Prometheus に送るときは、送る前に系列で分け直す。**
  Telegraf は Kafka のキーを付けないので、そのままだと同じ系列がパーティションに散らばり、古い時刻のサンプルが拒まれる。
  出典: `deploy.env.example` の `HTTP_SEND`。
- **`scheduler_configuration` は Terraform に書かず、`ignore_changes` にしてある。**
  書かないと provider が毎回差分を出し、書くとアプリが STARTED のあいだ更新できず apply が 400 で落ちる（2026-09-17 に実測）。
  出典: `IaC/terraform/aws-managed/pipeline/analytics/emr.tf` のコメント。
- **アプリの上限を変える apply は、アプリが止まっていないと通らない。**
  `ops/up.sh` の手順 7-4 が、上限が違うときだけ先にジョブとアプリを止める。
  出典: [pipeline.md](../../pipeline.md) の「Spark を確かめる」。
- **チェックポイントは MSK クラスタごとのパス。**
  MSK を作り直すと、前のクラスタのオフセットを読まずに新しいパスから始まる。
  出典: 同上。
- **Spark UI の URL は一時的な認証を含み、約 1 時間で切れる。**
  チャットやチケットに貼らない。
  出典: 同上。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| 同じ秒に中身がまったく同じメッセージが 2 つ来たとき | `event_id` が同じになる（受け入れている） |
| 1 AZ（既定） | その AZ が止まるとジョブも止まる。AWS で確かめた記録は無い |
| `flows`（GoFlow2） | Telegraf の形ではないので、Spark が読むときに共通の形（measurement `flow`、送り元・宛先・ポートを tags、bytes / packets を fields）に読み替える。AWS の MSK から読んだ記録はまだ無い |

OSS 版（`IaC/terraform/oss/pipeline/analytics`）には EMR Serverless が無い。[oss-variant.md](../../oss-variant.md)。

- 代わりに `spark.tf` が同じ `app/spark/snmp_sinks.py` を ECS（Fargate）で `local[*]` で動かす（Spark 3.5.9）。
- ジョブの分け方は同じで、格納先ごとに 1 サービス。起こし直しは ECS のサービスがする。
- チェックポイントは同じバケットの `analytics/checkpoint/` に S3A で書く。

## 関連

- [msk.md](msk.md)、[s3-tables-athena.md](s3-tables-athena.md)、[opensearch-serverless.md](opensearch-serverless.md)、[prometheus.md](prometheus.md)、[splunk.md](splunk.md)
- [pipeline.md](../../pipeline.md): 「Spark を確かめる」
- [data-stores.md](../../data-stores.md): 「届け方の保証」
- FAQ の 5 章「Spark の動き」と 9 章「格納先とテーブル、重複」: [faq-fukuda-nwc-poc.md](../../faq-fukuda-nwc-poc.md)

## 経緯

- 2026-10-02 までは Spark が異常を検知して EventBridge に出していた。いまは書くだけ（出典: `IaC/terraform/aws-managed/pipeline/analytics/locals.tf` の先頭のコメント）。
