# S3 の置き場を整える（035）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

S3 の汎用バケットは土台（`IaC/terraform/aws-managed/base/core/bucket.tf`）の 1 本 `<prefix>-kb-<アカウント>` だけで、そこに性質の違うものが同居している。2026-10-10 の棚卸しで次の 4 点が分かり、ユーザーが「logs はバケットを分ける、残りは assets でよい」「logs は `ops/down.sh` で消さなくてよく、7 日で消えるようにしたい」と決めた。

| # | いまの状態 | 問題 |
|---|---|---|
| 1 | プレフィックスが `web/`（画面の部品）`docs/`（KB の取り込み元）`lab/`（containerlab の材料）`analytics/`（Spark のスクリプト・jar・EMR のログ）`spark/checkpoint/`（Spark の checkpoint）`firehose-errors/`（Firehose が書けなかった行） | 軸が混ざっている。`web/` `lab/` は部品名、`analytics/` は Terraform のルート名、`spark/` は部品名、`firehose-errors/` は種類名。Spark のものが `analytics/` と `spark/` に割れている |
| 2 | バケット名が `kb` | KB（Bedrock のナレッジベース）を作らなくても使う。`web/` と `lab/` が入っているのに名前が中身を表さない |
| 3 | EMR のログ（`analytics/logs/`）と Firehose のエラー行に期限が無い | 1 回の起動で EMR のログが数十 MB たまり、`ops/down.sh` が `force_destroy` で消すまで残る。消したあとに調べたいときに読めない |
| 4 | `telegraf/` は `bucket.tf` と `outputs.tf` のコメントにだけある | 実体の無い参照 |

AWS の S3 のベストプラクティスは「ライフサイクル・アクセス権・暗号化の要件が違うデータは別のバケットに置く」で、ログと配布物はその例に当たる。ログだけ別のバケットにすると、ログに 7 日の期限を付けられ、`ops/down.sh` で消さずに残せ（VPC より長生きするので閉域の Deny を付けない）、配布物のバケットは今までどおり `terraform destroy` で消せる。

今日 pm/aws-verify で先に手を付けた `analytics/checkpoint → spark/checkpoint` の改名（9 ファイル、未コミット）は、このサイクルに吸収する（下の `spark/` の節）。その差分は捨て、エンジニアが新しい配置で書く。

## 設計方針

### バケットは 2 本

| バケット | 名前 | 作る場所 | 中身 | 消し方 |
|---|---|---|---|---|
| assets | `<prefix>-assets-<アカウント>` | `base/core`（いまの `kb` の改名） | `ops/up.sh` が置く配布物と、Spark の checkpoint。部品名でプレフィックスを切る（下の表） | `ops/down.sh`（`force_destroy = true`。いままでと同じ） |
| logs | `<prefix>-logs-<アカウント>` | 新しいルート `base/logs` | EMR のログと Firehose が書けなかった行。種類名でプレフィックスを切る | `ops/down.sh` は消さない。中身は 7 日で消え、空のバケットは無料。消すなら `terraform -chdir=IaC/terraform/aws-managed/base/logs destroy` |

assets のプレフィックス（部品名）:

| プレフィックス | いま | 置く側 | 読む側 |
|---|---|---|---|
| `web/` | `web/`（変えない） | `ops/up.sh` 4-2、`ops/oss/up.sh` 4-2、`base/core/outputs.tf` の `upload_web_command` | Web の EC2 の user_data（`base/core`） |
| `kb/` | `docs/` | `ops/up.sh` 4-3（`app/resources/*.md`）、`agent/outputs.tf` の `upload_docs_command` | Bedrock KB のデータソース（`agent/kb.tf` の `inclusion_prefixes`） |
| `lab/` | `lab/`（変えない） | `ops/lab-common.sh` の `upload_lab`、`pipeline/lab/outputs.tf` の `upload_lab_command` | lab の EC2 の user_data（`pipeline/lab`） |
| `spark/snmp_sinks.py` `spark/jars/` | `analytics/snmp_sinks.py` `analytics/jars/` | `ops/up.sh` 5-2 | EMR のジョブ（`pipeline/analytics/outputs.tf` の `job_drivers`） |
| `spark/checkpoint/<MSK の uuid>/` | `analytics/checkpoint/<uuid>/` | Spark 自身（`app/spark/snmp_sinks.py`） | Spark 自身。OSS 版も同じパス（`s3a://`） |

logs のプレフィックス（種類名）:

| プレフィックス | いま | 書く側 |
|---|---|---|
| `emr/`（EMR がその下に `applications/<id>/jobs/<id>/…` を作る） | assets の `analytics/logs/` | EMR Serverless（`pipeline/analytics/outputs.tf` の `logUri`） |
| `firehose-errors/alert_events/` | assets の同名 | Firehose（`pipeline/analytics/history.tf`）。`history.tf` は OSS 版の analytics にもリンクされているので、OSS 版の logs バケットにも同じものが落ちる |

`telegraf/` の参照（`base/core/bucket.tf:2,4`、`base/core/outputs.tf:73`）は消す。

### logs バケット（新しいルート `base/logs`）

- `IaC/terraform/aws-managed/base/logs/` を `base/ecr` と同じ構成で作る: `main.tf` `outputs.tf` `providers.tf` `variables.tf` `versions.tf` `terraform.tfvars.example`。変数は `region` / `owner` / `project` の 3 つだけ（`base/ecr/variables.tf` から validation ごと写す。`create_*` は無い）。`providers.tf` / `versions.tf` は `base/ecr` と同じ。
- `locals { name_prefix = "${var.owner}-${var.project}" }` と `data "aws_caller_identity"` で `account_id` を取る。
- `main.tf` の資源: `aws_s3_bucket.logs`（`bucket = "${local.name_prefix}-logs-${local.account_id}"`、`force_destroy = true`、`tags = { Name = "${local.name_prefix}-logs" }`）、`aws_s3_bucket_public_access_block`（4 つとも true）、`aws_s3_bucket_server_side_encryption_configuration`（AES256）、`aws_s3_bucket_ownership_controls`（BucketOwnerEnforced）、`aws_s3_bucket_lifecycle_configuration`（下）、`aws_s3_bucket_policy`（`DenyInsecureTransport` だけ。`bucket.tf` の同名の文と同じ形）。
- ライフサイクルは 1 ルール（`id = "expire-7-days"`、`status = "Enabled"`、`filter {}` でバケット全体、`expiration { days = 7 }`、`abort_incomplete_multipart_upload { days_after_initiation = 7 }`）。バージョニングは付けない（期限で消えたものを戻す要件は無い）。
- **`DenyOutsideVpc` は付けない。** 理由: (1) `base/logs` は `base/core` より先に apply し、`ops/down.sh` で消さないので、VPC の id を知らない・VPC より長生きする。(2) 書くのは AWS のサービス（EMR Serverless、Firehose）で、Firehose は VPC の外から書く（`history.tf` 先頭のコメント。いまも `perimeter_exempt_principals` で例外にしている）。IAM 側の Deny（`access.tf` の `emr_perimeter`）はそのまま（EMR のジョブは VPC の中から書くので `aws:SourceVpc` が合う。いまも assets の `DenyOutsideVpc` の下で書けている）。
- `outputs.tf`: `logs_bucket_name`（`aws_s3_bucket.logs.bucket`）と `logs_bucket_arn`（`.arn`）。description に「`pipeline/analytics` が EMR のログと Firehose のエラー行の置き場として読む。`ops/down.sh` は消さない」と書く。
- OSS 版: `IaC/terraform/oss/base/logs/` に、上の 6 ファイルと `.terraform.lock.hcl` の 7 本への相対シンボリックリンク（`../../../../terraform/aws-managed/base/logs/<file>`。`IaC/terraform/oss/base/ecr/` と同じ段数）と、実ファイル `oss.auto.tfvars`（`base/ecr` のものを写し、`project = "nwc-oss"`）。`.gitignore` の `!IaC/terraform/oss/**/oss.auto.tfvars` に当たるので追加の例外は要らない。
- ルートの一覧に足す: `ops/oss/up.sh:161` の `ROOTS`、`ops/check.sh:15` の `ROOTS`、`tests/test_oss.py:907` の `TF_ROOTS`（`"base/ecr", "base/logs", "base/core", …` の順）。`ops/oss/down.sh:13` のコメントの「9 つのルート」は「`base/logs` は消さない」と書き足す。

### 書く側を logs バケットに向ける（`pipeline/analytics`）

- `locals.tf`: `data "terraform_remote_state" "logs"`（`backend = "local"`、`path = "${path.module}/../../base/logs/terraform.tfstate"`。マネージド版も OSS 版も `pipeline/analytics/` から `../../base/logs/` で自分の側の state に当たる）。`logs_bucket = data.terraform_remote_state.logs.outputs.logs_bucket_name`、`logs_bucket_arn = "arn:${local.partition}:s3:::${local.logs_bucket}"`。`try` で包まない（`base/logs` が無いまま analytics を apply する経路は無い。`ops/up.sh` が先に作る）。
- `locals.tf` の `s3_prefix = "spark"`、`script_key = "${local.s3_prefix}/snmp_sinks.py"`、`jars_prefix = "${local.s3_prefix}/jars"`、`checkpoint = "${local.s3_prefix}/checkpoint"`、`emr_logs_prefix = "emr"`（`logs_prefix` は消す）。
- `outputs.tf:97` の `logUri` を `s3://${local.logs_bucket}/${local.emr_logs_prefix}/` に。description の「worker logs to the asset bucket」を「to the logs bucket」に。
- `access.tf` の EMR の実行ロール: `AssetBucket` の `Resource` を `"${local.bucket_arn}/${local.s3_prefix}/*"` の 1 本に戻し（スクリプトと jar を読み、checkpoint を読み書きする）、`LogsBucket`（`s3:PutObject` `s3:GetObject`、`"${local.logs_bucket_arn}/${local.emr_logs_prefix}/*"`）と `LogsBucketList`（`s3:ListBucket` `s3:GetBucketLocation`、`local.logs_bucket_arn`）を足す。
- `history.tf` の Firehose: `s3_configuration.bucket_arn = local.logs_bucket_arn`。`error_output_prefix = local.alert_errors`（`firehose-errors/alert_events/`）はそのまま。IAM の `ErrorBucket` を `"${local.logs_bucket_arn}/firehose-errors/*"`、`ErrorBucketList` を `local.logs_bucket_arn` に。
- `emr.tf` に logs の方のロールやアプリの設定で `local.bucket` を使っている箇所があれば（`grep -n 'bucket' emr.tf` で確かめる）、ログに関わるものだけ logs に向ける。
- OSS 版 `IaC/terraform/oss/pipeline/analytics/network.tf`: `s3_prefix = "spark"`、`checkpoint = "${local.s3_prefix}/checkpoint"`、マネージド版と同じ `data.terraform_remote_state.logs` と `logs_bucket` / `logs_bucket_arn`（リンクされた `history.tf` が参照する）。`spark.tf` の `AssetBucket` の `Resource` を `"${local.bucket_arn}/${local.s3_prefix}/*"` の 1 本に。OSS 版の Spark のログは CloudWatch（`spark.tf` の awslogs）のまま。
- `app/spark/snmp_sinks.py` の checkpoint の help / 既定値を `s3://<バケット>/spark/checkpoint/` に揃える（いまは `analytics/checkpoint`）。

### assets バケット（`base/core` の改名）

- `bucket.tf`: `aws_s3_bucket.kb` と付属の 4 資源（`public_access_block` / `server_side_encryption_configuration` / `ownership_controls` / `bucket_policy`）の Terraform 名を `assets`、バケット名を `${local.name_prefix}-assets-${local.account_id}`、タグ `Name` を `${local.name_prefix}-assets`。先頭のコメントを上の表の中身（`web/` `kb/` `lab/` `spark/`）に書き直し、「名前は kb のまま」と `telegraf/` の行を消す。ポリシーの中身は変えない（`DenyInsecureTransport` + `var.network_perimeter` のとき `DenyOutsideVpc`）。
- state の移動は要らない（2026-10-10 時点で AWS のリソースは全部消えていて、次の `ops/up.sh` が新しい名前で作る）。`terraform state mv` の手順は書かない。
- `outputs.tf`: `kb_bucket_name` → `assets_bucket_name`、`kb_bucket_arn` → `assets_bucket_arn`。description を「`agent`（`kb/`）、`pipeline/lab`（`lab/`）、`pipeline/analytics`（`spark/`）が読む」に。`upload_web_command` の `web/` はそのまま。
- 読む側の変数名を揃える: `agent/locals.tf:65-66`、`pipeline/lab/locals.tf:47`、`pipeline/lab/variables.tf:75` の description、`pipeline/analytics/locals.tf:74`、`oss/pipeline/analytics/network.tf:59`。
- `ops/up.sh`: `KB_BUCKET` → `ASSETS_BUCKET`（:764-765、:805-815、:869-870、:890-895）。4-3 の `docs/` を `kb/`、5-2 の `analytics/` を `spark/`（ログの文言も「`s3://$ASSETS_BUCKET/spark/` に置く」）。`ops/oss/up.sh` の :291-329 も同じ。`ops/lab-common.sh` の `upload_lab` は引数で受けるので変えない。
- `agent/kb.tf:400,407` の `docs/` → `kb/`、`agent/outputs.tf:49` の `upload_docs_command` の `docs/` → `kb/`。資源名（`aws_bedrockagent_data_source.docs`、`-docs`）は変えない。

### `ops/up.sh` / `ops/down.sh`

- `ops/up.sh:610` の `tf_apply base/ecr` の直後に `tf_apply base/logs`（手順 1 の中。`base/core` の前）。`ops/oss/up.sh:174` も同じ。2 回目以降は `No changes` で通る。
- `ops/down.sh` / `ops/oss/down.sh` は `base/logs` を**消さない**。`destroy_root base/logs` を書かない。最後のまとめの echo（`ops/down.sh:126` 付近）に「logs のバケット `<prefix>-logs-<アカウント>` は残す（中身は 7 日で消える。消すなら `terraform -chdir=IaC/terraform/aws-managed/base/logs destroy`）」を 1 行足す。`report_leftovers` の一覧に毎回出ることになるので、docs にそう書く。
- `KEEP_ECR` のような切り替えは作らない（残すのが既定で、消す手段は terraform の 1 コマンド）。

### テスト

機能ごとのファイルに足す（`tests/README.md` の方針）。件数は実装の前に `uv run --frozen python3 tests/<name>.py` で測って `build.md` に書く。

- `tests/test_analytics.py`: :53,66 の output 名を `assets_bucket_name`、:410-425 の firehose-errors を logs バケット（`local.logs_bucket_arn`）、:1416 の 5-2 を `spark/jars/`、checkpoint の検査を `spark/checkpoint`。新規: (a) `base/logs` の 6 ファイルがあり、`aws_s3_bucket_lifecycle_configuration` に `days = 7` と `days_after_initiation = 7`、ポリシーに `DenyOutsideVpc` が無く `DenyInsecureTransport` がある。(b) `analytics/locals.tf` に `terraform_remote_state.logs` があり、`logUri` が `local.logs_bucket` を使い、Firehose の `bucket_arn = local.logs_bucket_arn`。(c) `ops/up.sh` と `ops/oss/up.sh` で `tf_apply base/logs` が `tf_apply base/ecr` のあと `tf_apply base/core` の前にある。(d) `ops/down.sh` と `ops/oss/down.sh` に `base/logs` の destroy が無い。(e) `IaC/` `ops/` `tests/` `docs/` `app/` に `kb_bucket` / `KB_BUCKET` / `analytics/logs` / `analytics/jars` / `analytics/checkpoint` が残っていない（`docs/cycles/` と `docs/verification/` は記録なので除く）。
- `tests/test_oss.py`: `TF_ROOTS` に `base/logs`、:959 の「9 ルート」→「10 ルート」、:967 の「9 ルート」も同じ。OSS 側の symlink の検査は `UNCHANGED` に自動で入る。
- `tests/test_oss_ops.py:1209,1219`、`tests/test_workflow.py:909,925` の `KB_BUCKET` → `ASSETS_BUCKET`。
- `tests/test_agentcore.py:83-85` のフィクスチャの `s3://b/docs/` はテストデータなので変えない。`tests/test_lab_debug.py` の `lab/` は CloudFormation の `<prefix>-lab-debug-<アカウント>` の話なので変えない。

### docs

- 新規 `docs/architecture/resources/s3-buckets.md`（`resources/README.md` の一覧に 1 行足す）: 2 本のバケットと上の 2 つのプレフィックスの表、ライフサイクル、ポリシーの違い（logs に `DenyOutsideVpc` を付けない理由）、消し方。
- `docs/data-stores.md` の「1. 置き場は 2 つ」の末尾に「配布物とログの S3 のバケットは [s3-buckets.md](architecture/resources/s3-buckets.md)」の 1 行。
- `docs/deploy.md`: 「`ops/down.sh` がすること」の mermaid と本文に `base/logs` は消さないことを足し、「消したあとに残るもの」の表に logs バケットの行（残る理由: 意図して残す。費用: 7 日ぶんのログで数十 MB、月 1 円未満。次の `ops/up.sh`: そのまま使う）。`ops/up.sh` の手順の説明に `base/logs` を足す。
- `docs/pipeline.md:514`、`docs/troubleshooting.md:177`、`docs/architecture/resources/firehose.md:17,30,74` の「土台のバケットの `firehose-errors/…`」→「logs のバケット `<prefix>-logs-<アカウント>` の `firehose-errors/…`」。
- `docs/architecture/resources/emr-serverless.md:21,97` の `analytics/checkpoint/` → `spark/checkpoint/`、:22 のログの行に S3 の `emr/` を足す。`docs/pipeline.md:662` は `spark/checkpoint/`。
- `docs/architecture/resources/web-ec2.md:20` の `<prefix>-kb-<アカウント>` → `<prefix>-assets-<アカウント>`。
- `docs/architecture/resources/vpc-perimeter.md:28` の「Deny を定義している場所」に「`base/logs` のバケットには付けない（理由は s3-buckets.md）」。
- `docs/faq-fukuda-nwc-poc.md` に「S3 のバケットは何本あるか」を 1 問（節の目次にも足す。見出しの文字は `tests/test_analytics.py` の `_faq_heads_ok` の範囲で）。
- `README.md` と `CLAUDE.md` にバケット名の記述があれば揃える（`grep -n 'kb-\|バケット' README.md CLAUDE.md` で確かめる。2026-10-10 の grep では無かった）。

### 追加（2026-10-10、ユーザー指示）: EMR の managed storage を有効に戻す

- ユーザー指示「managed storage は無料なら有効にして」。`pipeline/analytics/outputs.tf` の `configuration_overrides_json` の `managedPersistenceMonitoringConfiguration` を `enabled = true` にする。
- 理由: 終わったジョブの Spark UI（Spark History Server）は managed storage のイベントログしか読まない。managed storage は無料で 30 日保持。
- S3 の `logUri`（logs の `emr/`）と CloudWatch の driver ログは今のまま残す（その後、下の節で S3 は落とした）。`SPARK_EXECUTOR` は CloudWatch に足さない。
- 閉域の S3 gateway endpoint（`base/core/endpoints.tf`）にはポリシーが無いので、endpoint 側は変えない見込み（AWS では未確認）。

### 追加（2026-10-10、ユーザー指示）: EMR のログの S3 を落とす

- 決定: EMR Serverless のログの置き先は 2 つだけにする。CloudWatch Logs（driver の stdout / stderr）と、EMR の managed storage（Spark UI。無料・30 日保持）。S3（logs バケットの `emr/`）には出さない。
- 理由: managed storage が S3 と同じもの（driver と executor の stdout / stderr、イベントログ）を持ち、終わったジョブの Spark UI が読むのも managed storage だけなので、S3 は二重に残るだけになる。
- logs バケットは残す。Firehose が書けなかった行（`firehose-errors/alert_events/`）の置き場として使う。
- 変える場所:
  - `pipeline/analytics/outputs.tf` の `configuration_overrides_json` から `s3MonitoringConfiguration` を消す。
  - `pipeline/analytics/access.tf` の実行ロールから logs バケットへの 2 つの文（`LogsBucket` / `LogsBucketList`）を消す。
  - `pipeline/analytics/locals.tf` の `emr_logs_prefix` を消す（`logs_bucket` / `logs_bucket_arn` は Firehose が使うので残す）。
  - `base/logs` と `base/core` の description とコメント、`ops/up.sh` / `ops/oss/up.sh` のコメントを「Firehose の行だけ」に揃える。
  - `tests/test_analytics.py` の検査を「S3 に出さない」に置き換える。
  - docs（FAQ、`s3-buckets.md`、`emr-serverless.md`、resources の `README.md`、`pipeline.md`、`deploy.md`）を揃える。
- ついでに、`base/core/outputs.tf` の `assets_bucket_arn` の description を、読むのが agent だけであることに合わせて直す（cold review の Nit）。
- FAQ に 2 問足す: 「`terraform apply` で Spark のジョブも登録される？」と「S3 Tables と Athena はある？」。
- OSS 版は変えない（EMR Serverless が無い）。`IaC/terraform/oss/pipeline/analytics/network.tf` のコメントだけ「Firehose が書けなかった行」に揃える。
- `configuration_overrides_json` はジョブのタグ `SpecHash` に入るので、次の `ops/up.sh` で 3 つのジョブとも checkpoint の続きから起こし直しになる（データは落ちない）。

## 変更対象ファイル

| 区分 | ファイル |
|---|---|
| 新規 | `IaC/terraform/aws-managed/base/logs/{main,outputs,providers,variables,versions}.tf`、`terraform.tfvars.example`、`IaC/terraform/oss/base/logs/`（7 symlink + `oss.auto.tfvars`）、`docs/architecture/resources/s3-buckets.md` |
| base/core | `bucket.tf`、`outputs.tf` |
| agent | `locals.tf`、`kb.tf`、`outputs.tf` |
| pipeline/lab | `locals.tf`、`variables.tf` |
| pipeline/analytics | `locals.tf`、`access.tf`、`history.tf`、`outputs.tf`、（`emr.tf` は確かめて必要なら） |
| oss/pipeline/analytics | `network.tf`、`spark.tf` |
| app | `app/spark/snmp_sinks.py` |
| ops | `ops/up.sh`、`ops/oss/up.sh`、`ops/down.sh`、`ops/oss/down.sh`、`ops/check.sh` |
| tests | `tests/test_analytics.py`、`tests/test_oss.py`、`tests/test_oss_ops.py`、`tests/test_workflow.py` |
| docs | `docs/architecture/resources/README.md`、`docs/data-stores.md`、`docs/deploy.md`、`docs/pipeline.md`、`docs/troubleshooting.md`、`docs/faq-fukuda-nwc-poc.md`、`docs/architecture/resources/{firehose,emr-serverless,web-ec2,vpc-perimeter}.md` |
| 追加（managed storage） | `IaC/terraform/aws-managed/pipeline/analytics/outputs.tf`、`tests/test_analytics.py`、`docs/architecture/resources/emr-serverless.md`（`docs/pipeline.md` と `docs/faq-fukuda-nwc-poc.md` の該当箇所は main にだけあるので、035 を main に入れたあとで直す） |
| 追加（EMR のログの S3 を落とす） | `IaC/terraform/aws-managed/pipeline/analytics/{outputs,access,locals}.tf`、`IaC/terraform/aws-managed/base/logs/{main,outputs}.tf`、`IaC/terraform/aws-managed/base/core/{bucket,outputs}.tf`、`IaC/terraform/oss/pipeline/analytics/network.tf`（コメントだけ）、`ops/up.sh`、`ops/oss/up.sh`（コメントだけ）、`tests/test_analytics.py`、`docs/faq-fukuda-nwc-poc.md`、`docs/architecture/resources/{s3-buckets,emr-serverless,README}.md`、`docs/pipeline.md`、`docs/deploy.md` |
| 変えない | `IaC/terraform/aws-managed/base/core/endpoints.tf`（S3 の gateway endpoint にポリシーは無いので logs バケットのための変更は無い）、`perimeter.tf`、`pipeline/analytics/tables.tf`（S3 Tables は別物）、`IaC/cloudformation/`（lab-debug のバケット）、`docs/cycles/`、`docs/verification/` |

## 再利用するもの

- `IaC/terraform/aws-managed/base/ecr/` のルートの骨格（`variables.tf` の `owner` の validation、`providers.tf` の `default_tags`、`versions.tf`）。
- `base/core/bucket.tf` の PAB / SSE / OwnershipControls / `DenyInsecureTransport` の書き方。
- `pipeline/analytics/locals.tf` の `terraform_remote_state`（`backend = "local"`、相対 path）。
- `IaC/terraform/oss/base/ecr/` の symlink の段数と `oss.auto.tfvars`。
- `tests/test_oss.py` の `links_to_managed` / `UNCHANGED`（`TF_ROOTS` に足せば OSS 側の symlink の検査が付いてくる）。
- 既存の cycle の `build.md` の型（`docs/cycles/034-compose-unverified/build.md`）。

## 実装ステップ

commit はステップごとに分ける（レビューで差分を追えるように）。

1. **`base/logs` を作る。** マネージド側の 6 ファイル、OSS 側の symlink と `oss.auto.tfvars`、`ROOTS` / `TF_ROOTS` への追加、`terraform validate`（managed / oss の `base/logs`）。
2. **assets に改名する。** `bucket.tf` / `outputs.tf` の改名、読む側の変数名、`ops/up.sh` と `ops/oss/up.sh` の `ASSETS_BUCKET`、`kb/` と `spark/` のプレフィックス、`agent/kb.tf` と `agent/outputs.tf`、`app/spark/snmp_sinks.py`。`terraform validate`（base/core、agent、pipeline/lab、pipeline/analytics、oss/pipeline/analytics）。
3. **ログを logs に向ける。** `pipeline/analytics` の `locals.tf` / `outputs.tf` / `access.tf` / `history.tf`、OSS の `network.tf` / `spark.tf`。`terraform validate`（managed / oss の pipeline/analytics）。
4. **ops を揃える。** `tf_apply base/logs`、`down.sh` の echo、`check.sh` の `ROOTS`。`bash -n`。
5. **テストを直して足す。** 上の「テスト」。実装の前に測った件数との差を `build.md` に書く。
6. **docs を直す。** 上の「docs」。
7. **セルフレビュー**（`/cycle-build` 手順 6）。`/robust` と反対弁護人は PM が回す。

## 検証方法（期待出力まで）

手元（エンジニア）:

- `uv run --frozen python3 tests/test_analytics.py` → `通過 N / 失敗 0`（N は実装前の 535 より増える。足した検査の数を `build.md` に書く）。`tests/test_oss.py`（実装前 177）、`tests/test_oss_ops.py`（206）、`tests/test_workflow.py`、`tests/test_lab_debug.py`、`tests/test_agentcore.py` も `失敗 0`。
- `terraform -chdir=IaC/terraform/aws-managed/<root> validate -no-color` が `base/logs` `base/core` `agent` `pipeline/lab` `pipeline/analytics` の 5 つで、`terraform -chdir=IaC/terraform/oss/<root> validate -no-color` が `base/logs` `pipeline/analytics` の 2 つで `Success! The configuration is valid.`（`init -backend=false` を先に打つ）。
- `bash -n ops/up.sh ops/down.sh ops/oss/up.sh ops/oss/down.sh ops/check.sh` が無言。
- `grep -rn 'kb_bucket\|KB_BUCKET\|analytics/logs\|analytics/jars\|analytics/checkpoint\|/docs/' IaC ops app tests docs README.md CLAUDE.md | grep -v 'docs/cycles/\|docs/verification/\|tests/test_agentcore.py'` が 0 行（`/docs/` は `s3://…/docs/` の意味で残っていないこと。`docs/*.md` へのリンクの `docs/` は先頭が `/` でないので当たらない。当たったら 1 行ずつ読んで判断する）。
- `ls -l IaC/terraform/oss/base/logs/` が 7 本の symlink（6 ファイルと `.terraform.lock.hcl`）と `oss.auto.tfvars`。
- `grep -n 'tf_apply base/' ops/up.sh ops/oss/up.sh` が `base/ecr` → `base/logs` → `base/core` の順。`grep -n 'base/logs' ops/down.sh ops/oss/down.sh` に `destroy_root` の行が無い。

AWS（PM が次の 1 回の検証で見る。エンジニアは打たない）:

- `ops/up.sh` のあと `aws s3api get-bucket-lifecycle-configuration --bucket efukuda-nwc-poc-logs-493116771193` の `Rules[0].Expiration.Days` が 7。`aws s3api get-bucket-policy` の Statement が `DenyInsecureTransport` の 1 つ。
- Spark のジョブが 1 回動いたあと `aws s3 ls s3://efukuda-nwc-poc-logs-493116771193/` に `emr/` が無い（追加: EMR のログの S3 を落とした）。assets の `analytics/` と `docs/` が無く、`spark/snmp_sinks.py` `spark/jars/` `spark/checkpoint/<uuid>/` `web/` `lab/` がある（`aws s3 ls s3://efukuda-nwc-poc-assets-493116771193/`）。
- KB を作っているなら（`CREATE_KB=1`）、取り込みが `kb/` から成功する（`start_ingestion_command` のあと `COMPLETE`）。
- 追加（managed storage）: 終わったジョブ（例: cancel したジョブ）の Spark UI が、コンソールの View application UIs から開ける。S3 を落としたあとも開ける（読むのは managed storage だけ）。
- `ops/down.sh` のあと `aws s3api head-bucket --bucket efukuda-nwc-poc-assets-493116771193` が 404、`…-logs-…` が 200。`report_leftovers` の一覧に logs バケットの ARN が出る（想定どおり）。

## 未確定事項とリスク

1. **EMR Serverless が logs バケットに書けるか（AWS で未確認）。** いまは assets（`DenyOutsideVpc` あり）に書けているので、Deny の無い logs に書けないことは考えにくいが、IAM の `LogsBucket` の Action が足りなければジョブは `Unable to push logs` 相当で FAILED になる。AWS の文書は実行ロールに `s3:PutObject` `s3:GetObject` `s3:ListBucket` を求めている（上の設計はこの 3 つ + `GetBucketLocation`）。PM の 1 回の検証で見る。
   追加（2026-10-10）: EMR のログを S3 に出すのをやめたので、このリスクは無くなった。
2. **Firehose の `FailedDataOnly` の書き先を別バケットにしてもストリームが作れるか（AWS で未確認）。** `s3_configuration.bucket_arn` はストリーム作成時にロールの権限を確かめるので、IAM の `ErrorBucket` / `ErrorBucketList` が logs を向いていれば通るはず。通らなければ `history.tf` の `time_sleep` と同じ `InvalidArgumentException` が出る。
3. **`ops/down.sh` の `report_leftovers` に毎回 logs バケットが出る。** 「消えたかはサービスごとの API で見る」運用なので害は無いが、docs に書いておかないと消し忘れに見える。
4. **KB の取り込み元を `docs/` → `kb/` にすると、既存の KB は再取り込みが要る。** AWS は全部消えているので、次の `ops/up.sh` で新しく作る。state の無いところへの影響は無い。
5. **OSS 版の logs バケット（`<owner>-nwc-oss-logs-…`）はマネージド版と別の 1 本になる**（接頭辞が違う）。OSS 版で中身は Firehose のエラー行だけ。空のバケットは無料なので放置でよいが、2 本あることは docs に書く。
6. **assets の改名で、古い `kb` バケットの名前の state が残っている PC では `terraform apply` が置き換え（destroy + create）になる。** メインのチェックアウトの state は 2026-10-10 に全部 destroy 済みなので当たらない。他の PC で古い state を持っているなら `ops/down.sh` を先に打つ、と docs/deploy.md に 1 行。

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261010-cycle-035-s3-layout-design.html -->
