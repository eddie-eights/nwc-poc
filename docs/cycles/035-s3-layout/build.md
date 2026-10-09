# S3 の置き場を整える（035）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high

ブランチ feat/035-s3-layout（origin/main 3d497de から）。AWS には触っていない（`ops/up.sh` は打っていない。AWS 側の確かめは設計の「検証方法」の AWS の節のとおり PM の 1 回の検証に回す）。

### テストの件数（実装の前と後）

実装の前は `git archive 3d497de` を scratch に展開して（`git init` してから）打った。後はこのブランチのステップ 5 の commit で打った。

| テスト | 実装の前（3d497de） | ステップ 5 のあと | 差 |
|---|---|---|---|
| `tests/test_analytics.py` | 535 | 544 | +9 |
| `tests/test_oss.py` | 177 | 177 | 0 |
| `tests/test_oss_ops.py` | 206 | 206 | 0 |
| `tests/test_workflow.py` | 333 | 333 | 0 |
| `tests/test_lab_debug.py` | 110 | 110 | 0 |
| `tests/test_agentcore.py` | 168 | 168 | 0 |

どれも `失敗 0`。`test_analytics.py` に足した 9 つ:

1. analytics が logs の state（`base/logs`）を `backend = "local"` で読み、`logs_bucket` / `logs_bucket_arn` を作る
2. `base/logs` に output `logs_bucket_name` がある
3. EMR の `logUri` が logs のバケットの `emr/` で、実行ロールの `LogsBucket` / `LogsBucketList` がそこだけを許す（古い `logs_prefix` が無い）
4. `base/logs` の git の中身が 5 つの `.tf` と `terraform.tfvars.example`（と `.terraform.lock.hcl`）
5. logs のバケットの名前と `force_destroy`、ライフサイクルの `days = 7` と `days_after_initiation = 7`
6. logs のバケットのポリシーの Sid が `DenyInsecureTransport` だけ（`DenyOutsideVpc` と `aws:SourceVpc` が無い）、PAB / SSE / OwnershipControls がある
7. `ops/up.sh` と `ops/oss/up.sh` で `tf_apply base/ecr` < `tf_apply base/logs` < `tf_apply base/core`、`ROOTS` と `ops/check.sh` の `ROOTS` に `base/logs`
8. `ops/down.sh` と `ops/oss/down.sh` に `base/logs` の destroy が無く、残すことと消し方を出す
9. git の管理下の `IaC` `ops` `app` `tests` `README.md` `CLAUDE.md` に古い名前（kb のバケットの変数と output、`analytics/` の下の `logs` / `jars` / `checkpoint`）が無い（このファイル自身に載らないよう、パターンは文字列をつないで作る）

件数の変わらない 3 本は、既存の check を新しい名前に直した:

- `tests/test_oss_ops.py`: `ROOTS` に `base/logs` を足し、destroy を見る check は `DOWN_ROOTS`（`base/logs` を除いた 9 つ）で見る。ルートの数の文言（9 → 10）、`init` の数（9 → 10）、`$KB_BUCKET` → `$ASSETS_BUCKET`
- `tests/test_workflow.py`: up.sh 4-2 の `$KB_BUCKET` → `$ASSETS_BUCKET`
- `tests/test_oss.py`: Splunk のジョブの引数のフィクスチャ `s3a://b/analytics/checkpoint` → `s3a://b/spark/checkpoint`（`TF_ROOTS` と「10 ルート」はステップ 1 で直してあった）

既存の check の直し（`test_analytics.py`）: output 名 `assets_bucket_name`、checkpoint と jar の prefix（`s3_prefix = "spark"`、`checkpoint = "${local.s3_prefix}/checkpoint"`）、Firehose の書けなかった行の `bucket_arn = local.logs_bucket_arn` とロールの Resource、5-2 の `s3://$ASSETS_BUCKET/spark/jars/`、checkpoint のフィクスチャ 5 か所。

足した check 7〜9 は、scratch の写しに退行を 1 つずつ入れて落ちることを確かめた（`ops/down.sh` に `destroy_root base/logs` を足す、`ops/up.sh` の `tf_apply base/logs` を末尾へ動かす、`ops/check.sh` に古い変数名のコメントを足す。3 つとも狙った check で `AssertionError`）。
