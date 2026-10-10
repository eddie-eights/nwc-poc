# S3 のバケット（assets と logs）

← [リソースごとの知見](README.md)

## ひとことで

S3 の汎用バケットは 2 本。`ops/up.sh` が置く配布物と Spark の checkpoint を入れる **assets** と、Firehose が書けなかった行を 7 日だけ置く **logs**。
EMR Serverless のログは S3 に出さない（CloudWatch Logs と EMR の managed storage。[emr-serverless.md](emr-serverless.md)）。
assets は `ops/down.sh` で消え、logs は消さずに残す。
S3 Tables（Iceberg のテーブルバケット）は別物で、[s3-tables-athena.md](s3-tables-athena.md)。

## このプロジェクトでの使い方

| 項目 | assets | logs |
|---|---|---|
| 名前 | `<prefix>-assets-<アカウント>`（cycle 035 で `<prefix>-kb-<アカウント>` から改名） | `<prefix>-logs-<アカウント>` |
| terraform のルート | `IaC/terraform/aws-managed/base/core` の `bucket.tf` | `IaC/terraform/aws-managed/base/logs` の `main.tf` |
| 作る順 | `ops/up.sh` の手順 3（base/core。手順 1 の base/ecr と base/logs の後） | `ops/up.sh` の手順 1（base/ecr の次） |
| 消し方 | `ops/down.sh`（base/core と一緒。`force_destroy = true`） | `ops/down.sh` は消さない。消すなら `terraform -chdir=IaC/terraform/aws-managed/base/logs destroy -var owner=<OWNER>`（`force_destroy = true`） |
| 中身の寿命 | ライフサイクル無し（バケットごと消える） | ライフサイクルで 7 日で消す（未完了のマルチパートも 7 日）。バージョニング無し |
| ポリシー | `DenyInsecureTransport` と `DenyOutsideVpc`（`NETWORK_PERIMETER=0` で外れる） | `DenyInsecureTransport` だけ |
| PAB・SSE・所有 | 全部ブロック、SSE-S3（AES256）、`BucketOwnerEnforced` | 同じ |
| 読む output | `assets_bucket_name`（agent、pipeline/lab、pipeline/analytics）、`assets_bucket_arn`（agent だけ） | `logs_bucket_name`（pipeline/analytics の Firehose。`logs_bucket_arn` を読むルートは無い） |

プレフィックスは、assets は部品名で、logs は中身の種類（`firehose-errors/`）で切る:

| バケット | プレフィックス | 中身 | 書く | 読む |
|---|---|---|---|---|
| assets | `web/` | 画面のコード（`app/dashboard/*.py` と、Web が使う `app/agentcore/` の一部とデータ）、`requirements.txt`、`wheels/` | `ops/up.sh` の手順 4-2 | Web の EC2 の user_data |
| assets | `knowledge-base/` | KB の取り込み元の手順書（`CREATE_KB=1` のとき） | `ops/up.sh` の手順 4-3 | Bedrock の KB（ロール `<prefix>-kb`。`agent/kb.tf` の `kb_prefix` が `inclusion_prefixes` と、ロールが読める範囲の両方を決める） |
| assets | `lab/` | containerlab の rpm とトポロジ | `ops/up.sh` の手順 5-1 | lab の EC2 の user_data |
| assets | `spark/` | `snmp_sinks.py`、`jars/`、`checkpoint/<MSK の uuid>/` | `ops/up.sh` の手順 5-2（スクリプトと jar）、Spark（checkpoint） | EMR Serverless（OSS 版は ECS の Spark） |
| logs | `firehose-errors/alert_events/` | Firehose が S3 Tables に書けなかった行 | Firehose（ロール `<prefix>-alert-firehose`） | 人（[troubleshooting.md](../../troubleshooting.md)） |

OSS 版（`IaC/terraform/oss/`）も同じ 2 本で、接頭辞が `<owner>-nwc-oss` になる（マネージド版とは別のバケット）。
OSS 版の logs に入るのは Firehose の書けなかった行だけ（EMR Serverless が無い）。

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| デプロイする人（`ops/up.sh`） | PC → assets | VPC の外から。`DenyOutsideVpc` の例外（`perimeter_exempt_principals`） |
| Web・lab の EC2、Spark | VPC → assets | S3 の gateway エンドポイント。各ロールの IAM |
| Bedrock の KB | KB → assets の `knowledge-base/` | ロール `<prefix>-kb` を引き受けて VPC の外から読む。`DenyOutsideVpc` の例外。読めるのは `knowledge-base/` の下だけ（`s3:ListBucket` は `s3:prefix` の条件、`s3:GetObject` はオブジェクトの ARN で絞る。`web/` `lab/` `spark/` は読めない） |
| Firehose | Firehose → logs の `firehose-errors/` | ロール `<prefix>-alert-firehose` の `ErrorBucket` / `ErrorBucketList`（`pipeline/analytics/history.tf`） |

## 知見

- **KB の取り込み元のプレフィックスは `knowledge-base/`。** 2026-10-10 に `kb` から改名した（略語で分かりづらいため。ロール名 `<prefix>-kb` と index 名 `kb-index` と `agent/kb.tf` はそのまま）。`ops/up.sh` の手順 4 が新しいプレフィックスに置き直すので、古いプレフィックスの下のオブジェクトが残っていても `ops/down.sh` が assets ごと消す。
- **バケットを分けたのは寿命が違うから。**
  assets は `ops/up.sh` がいつでも置き直せるので `ops/down.sh` で消す。ログは環境を消したあとに読みたいので、VPC より長生きさせ、7 日で自然に消す。空のバケットは課金されない。
  出典: S3 の置き場を整える（035）の設計の「設計方針」。
- **logs に `DenyOutsideVpc` を付けない。**
  1. base/logs は base/core より先に作り、`ops/down.sh` でも消さないので、VPC の id を知らず、VPC より長生きする。
  2. 書くのは AWS のサービスで、Firehose はロールを引き受けて VPC の外から書く。
  EMR Serverless は logs に書かない（cycle 035 の追加で S3 のログを落とした）。
  出典: `IaC/terraform/aws-managed/base/logs/main.tf` のポリシーのコメント、[vpc-perimeter.md](vpc-perimeter.md)。
- **`ops/down.sh` の「残っていないか」の一覧に logs のバケットが毎回出る。**
  意図して残しているので想定どおり。`ops/down.sh` も一覧の後にそう出す（[deploy.md](../../deploy.md) の「消したあとに残るもの」）。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| EMR Serverless のログ | S3 には出さない（cycle 035 の追加で logs の `emr/` を落とした。CloudWatch Logs と EMR の managed storage だけ） |
| Firehose が書けなかった行を logs に落とせるか | 宛先の設定は確認済み（2026-10-10 の [AWS 検証](../../verification/20261010-aws-managed-035.md)で `S3BackupMode=FailedDataOnly`、宛先が logs の `firehose-errors/alert_events/`）。実際に落ちた行はまだ見ていない（落ちる行を作っていない） |
| 古い kb のバケットの state を持つ PC | `terraform apply` が置き換え（destroy + create）になる。035 の `ops/down.sh` ではその state を消し切れない（analytics の destroy が止まる）。消し方は [deploy.md](../../deploy.md) の「`ops/up.sh` がすること」にある 2 つ（035 より前のコードで down するか、先に base/logs を apply する） |

## 関連

- [vpc-perimeter.md](vpc-perimeter.md)、[emr-serverless.md](emr-serverless.md)、[firehose.md](firehose.md)、[web-ec2.md](web-ec2.md)、[lab-ec2.md](lab-ec2.md)、[agentcore-bedrock.md](agentcore-bedrock.md)
- [data-stores.md](../../data-stores.md)（データの置き場の全体）
