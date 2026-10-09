# Firehose

← [リソースごとの知見](README.md)

## ひとことで

アラートの通知を、S3 Tables の `alert_events` に追記するための配送ストリーム（Amazon Data Firehose。宛先は Iceberg）。
Lambda が 1 件ずつ渡した行を、60 秒か 1 MiB ごとにまとめて書く。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| ストリーム | `<prefix>-alert-events`。宛先 `iceberg`。1 つだけ | `IaC/terraform/aws-managed/pipeline/analytics/history.tf` の `aws_kinesis_firehose_delivery_stream.alert_events` |
| 宛先のテーブル | namespace `nwc` の `alert_events`。Glue のカタログ `s3tablescatalog/<テーブルバケット>` 越し | `history.tf`、`IaC/terraform/aws-managed/pipeline/analytics/tables.tf` |
| まとめ方 | 60 秒か 1 MiB の早いほう | `history.tf` の `buffering_interval`、`buffering_size` |
| 書けなかった行 | 土台のバケットの `firehose-errors/alert_events/`（`s3_backup_mode = FailedDataOnly`） | `history.tf` の `local.alert_errors` |
| ロール | `<prefix>-alert-firehose`。S3 Tables、Glue、エラー用のプレフィックス、ログ | `history.tf` の `aws_iam_role.alert_firehose` |
| ログ | `/aws/kinesisfirehose/<prefix>-alert-events`（ストリーム `DestinationDelivery`） | `history.tf` |
| 送る側のスイッチ | graph の変数 `alert_history = true`。`ops/up.sh` が analytics がある回（今回作るか、state に残っている）にだけ渡す | `ops/up.sh`、`IaC/terraform/aws-managed/pipeline/graph/sync.tf` |
| AZ | 選ぶものが無い（サービス側で動く）。Lambda からの入口は `kinesis-firehose` のエンドポイント（`ENDPOINTS_AZ_NUM`） | `ops/up.sh` の `endpoints_for` |
| 費用 | 取り込んだ量の課金（PoC の量なら月に数セント）。エンドポイント 1 本が 1.4 セント/時 × AZ | `history.tf` の先頭のコメント、[deploy.md](../../deploy.md) の「アラートの通知の履歴」 |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| Lambda `<prefix>-graph-status` | Lambda → Firehose | `kinesis-firehose` のエンドポイント、`firehose:PutRecordBatch`（このストリームだけ）。500 件ずつ |
| S3 Tables の `alert_events` | Firehose → テーブル | Firehose がロールを引き受けて、サービス側（VPC の外）から書く。IAM（Lake Formation は使わない） |
| 土台のバケット | Firehose → `firehose-errors/alert_events/` | 同じロール |
| CloudWatch Logs | Firehose → ロググループ | 同じロール |

## 知見

- **Firehose のロールは、閉域の Deny の例外に入れてある。**
  Firehose はロールを引き受けて自分の側（VPC の外）から書くので、`aws:SourceVpc` が付かない。資源側の Deny の例外（`perimeter_exempt_principals`）に入れ、IAM 側の Deny も付けない。
  出典: `IaC/terraform/aws-managed/pipeline/analytics/history.tf` の先頭のコメント、`IaC/terraform/aws-managed/base/core/perimeter.tf`。
- **ストリームの名前は固定で、graph は analytics を待たない。**
  graph は analytics より先に apply するが、名前が決まっているので Lambda の環境変数 `ALERT_STREAM` に先に書ける。ストリームができるまでのあいだの送信は失敗し、行はログに残る。
  出典: [pipeline.md](../../pipeline.md) の「アラートの履歴」、アラートの履歴を残す（001）の設計の「未確定事項とリスク」の 5。
- **Lambda は Neptune より先に Firehose へ送る。**
  Neptune が遅くても応答しなくても履歴は残る。
  - Firehose に使うのは長くて 15.6 秒（3 回 ×（接続 2 秒 + 読み 3 秒）+ 待ち 0.6 秒）。エンドポイントが 2 AZ なら長くて 21.6 秒。
  - 出典: [pipeline.md](../../pipeline.md) の「アラートの履歴」。
- **Firehose の失敗では Lambda を落とさない。**
  `status` の正しさを履歴より優先する。
  - 届かなかった行だけを 3 回まで送り直し、残った行は 1 行ずつ JSON のまま `ALERT_EVENT_LOST` の ERROR でログに書く。
  - 探すのは Logs Insights の `filter @message like /ALERT_EVENT_LOST/`。
  - 出典: 同上。
- **Lambda は重複を落とさない。**
  Grafana の 4 時間ごとの送り直しも、Grafana と Splunk の両方から来た分も行になる。読む側が `event_id` で落とす。
  出典: 同上。
- **時刻は ISO 8601 の UTC で送っている。**
  Firehose が受け付ける書式は文書で確かめきれていない。1970 年や NULL になったら epoch ミリ秒に切り替える、という手当てが設計に書いてある。
  出典: アラートの履歴を残す（001）の設計の「未確定事項とリスク」の 1。
- **Lake Formation は使わず、IAM だけで許している。**
  `s3tablescatalog` を `IAM_ALLOWED_PRINCIPALS` と `AllowFullTableExternalDataAccess` で作る前提。通らなければ `lakeformation:GetDataAccess` を足す。
  出典: `history.tf` のロールのポリシーのコメント、同じ設計のリスクの 2。
- **`ops/down.sh` の途中でも送信は失敗する。**
  analytics を消してから graph を消すまでのあいだ。`status` の更新は止まらない。
  出典: 同じ設計のリスクの 5。
- **ストリームはロールの IAM の反映を 30 秒待ってから作る。**
  ロールとポリシーを作った直後に作ると、Firehose がロールを引き受けられずに `InvalidArgumentException: The security token included in the request is invalid. Ensure that the provided IAM role associated with firehose is not deleted.` で止まることがある（2026-10-09 の AWS で実測。同じ引数の打ち直しで通った）。
  - cycle 026 から `history.tf` の `time_sleep.alert_firehose_iam`（`create_duration = "30s"`。ロールを作り直したら待ちも作り直す）を挟み、ストリームが `depends_on` で待つ。30 秒は推定で、2026-10-10 の AWS では 1 回で通った（`docs/verification/20261010-aws-managed.md`）。
  - それでも同じエラーで止まるなら、同じ引数で `ops/up.sh` を打ち直す。続くなら `create_duration` を延ばす。
  - 出典: `history.tf` の `time_sleep` のコメント、[troubleshooting.md](../../troubleshooting.md) の「`ops/up.sh` / Terraform」の表。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| Firehose から S3 Tables への書き込みが IAM だけで通るか | 2026-10-05 に AWS で確かめた（`alert_events` に `firing` と `resolved` の行が入った） |
| 時刻の書式（ISO 8601 の UTC）を Firehose が受け付けるか | 2026-10-05 に AWS で確かめた（同じ行を Athena のワークグループ `<prefix>-history` で読めた） |
| 閉域の Deny に当たらないか | 2026-10-05 に AWS で確かめた（行が Athena で読めた）。確かめ方は `firehose-errors/alert_events/` にオブジェクトが無いことと、Athena で行が読めること（[deploy.md](../../deploy.md)） |
| Splunk が起動の直後に `resolved` をまとめて送る | 既知（2026-10-05）。`alert_events` の行が増える（[troubleshooting.md](../../troubleshooting.md) の「既知の不具合」） |
| ロールの権限 | 広い（S3 Tables は `table/*`、Glue は `database/*` と `table/*/*`）。まず通すことを優先した（同じ設計のリスクの 13） |

## 関連

- [s3-tables-athena.md](s3-tables-athena.md)、[sns-sqs-lambda.md](sns-sqs-lambda.md)
- [pipeline.md](../../pipeline.md): 「アラートの履歴」
- [deploy.md](../../deploy.md): 「アラートの通知の履歴（Firehose と Athena）」
- [アラートの履歴を残す（001）の設計](../../cycles/001-alert-history-firehose/design.md)
