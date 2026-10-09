# S3 の置き場を整える（035）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high

ブランチ feat/035-s3-layout（origin/main 3d497de から）。commit はステップ 1 92babbd / 2 a37481e / 3 2c5b2bf / 4 aa10450 / 5 ccb92c3 / 6 e130bd9 / 7 はこの build.md を入れる commit。AWS には触っていない（`ops/up.sh` は打っていない。AWS 側の確かめは設計の「検証方法」の AWS の節のとおり PM の 1 回の検証に回す）。

### 変更ファイル

- ステップ 1（92babbd）
  - 新規 `IaC/terraform/aws-managed/base/logs/{main,outputs,providers,variables,versions}.tf`、`terraform.tfvars.example`、`.terraform.lock.hcl`（aws 6.64.0。ほかのルートの多くと同じ）
  - 新規 `IaC/terraform/oss/base/logs/`: 7 本の symlink と `oss.auto.tfvars`
  - `ops/check.sh` の `ROOTS`、`tests/test_oss.py` の `TF_ROOTS` と「10 ルート」
- ステップ 2（a37481e）
  - `base/core/bucket.tf` / `outputs.tf` / `perimeter.tf` / `web.tf`: `kb` → `assets`、output `assets_bucket_name` / `assets_bucket_arn`、`telegraf/` のコメントを消した
  - `agent/locals.tf` / `kb.tf` / `outputs.tf`: `inclusion_prefixes = ["kb/"]`、`upload_docs_command` の `kb/`
  - `pipeline/lab/locals.tf` / `variables.tf`、`pipeline/analytics/locals.tf`（`s3_prefix = "spark"`）、OSS の `spark.tf` のコメント
  - `ops/up.sh` / `ops/oss/up.sh`: `ASSETS_BUCKET`、`kb/` と `spark/`
  - `app/spark/snmp_sinks.py`: `--checkpoint` の help
- ステップ 3（2c5b2bf）
  - `pipeline/analytics/locals.tf`: `terraform_remote_state.logs`、`logs_bucket` / `logs_bucket_arn`、`emr_logs_prefix = "emr"`
  - `pipeline/analytics/outputs.tf`（`logUri`）、`access.tf`（`LogsBucket` / `LogsBucketList`）、`history.tf`（`ErrorBucket` / `ErrorBucketList` と `s3_configuration.bucket_arn`）
  - `IaC/terraform/oss/pipeline/analytics/network.tf`: 同じ `terraform_remote_state.logs`
- ステップ 4（aa10450）
  - `ops/up.sh:615` / `ops/oss/up.sh:179` の `tf_apply base/logs`、`ROOTS`
  - `ops/down.sh` / `ops/oss/down.sh`: 先頭のコメントと、`report_leftovers` のあとの「logs のバケットは残す」の echo
- ステップ 5（ccb92c3）: 下の「テストの件数」。`ops/down.sh:130` と `ops/oss/down.sh:89` の `$OWNER）` を `${OWNER}）` に（ステップ 4 の bash 3.2 の罠。`test_oss_ops.py` の検査が見つけた）
- ステップ 6（e130bd9）
  - 新規 `docs/architecture/resources/s3-buckets.md`。`README.md` の表と `docs/data-stores.md` から辿れる
  - `docs/deploy.md`: up.sh の手順 1・4・5、down.sh の mermaid と本文、「消したあとに残るもの」の表、035 より前の state の注意
  - `docs/architecture/resources/{emr-serverless,firehose,web-ec2,lab-ec2,vpc-perimeter,agentcore-bedrock}.md`、`docs/pipeline.md`、`docs/troubleshooting.md`
  - `docs/faq-fukuda-nwc-poc.md`: 9 章に「S3 のバケットは何本ある？ 何に使い分けている？」と目次
  - `agent/kb.tf:3` のコメント（S3 の `docs/` のままだった）
  - `tests/test_analytics.py`: 古い名前の検査に `docs` を足した（`docs/cycles/` と `docs/verification/` を除く）
- ステップ 7: `agent/kb.tf:2` のコメント（「web/ lab/ stream/ と共用」→ assets と `kb/`）、`s3-buckets.md:65` の古い名前、`design.md` の symlink の本数（下の設計との差 4）、この build.md

### 設計との差

1. **`s3_prefix = "spark"` をステップ 3 でなくステップ 2 で入れた。**
   ステップ 2 で `ops/up.sh` の 5-2 を `spark/` に置くようにしたので、読む側（`job_drivers` の `script_key` / `jars_prefix` と `checkpoint`）も同じ commit で揃えないと、ステップ 2 の時点で置き場と読み場がずれる。
2. **`ops/down.sh` の消し方の echo に `-var owner=${OWNER}` を付けた。**
   設計は `terraform -chdir=… destroy`。`owner` は既定が無い必須の変数なので、付けないと terraform が対話で聞いてくる。docs にも `-var owner=<OWNER>` で書いた。
3. **ステップ 4 の echo の bash 3.2 の罠（`$OWNER）`）はステップ 5 の commit で直した。**
   ステップ 4 のときに気付かず、ステップ 5 で `test_oss_ops.py` の既存の検査が落ちて見つかった。
4. **OSS 版の `base/logs` の symlink は 6 本でなく 7 本。**
   `IaC/terraform/oss/base/ecr/` と同じく `.terraform.lock.hcl` もリンクする（`tests/test_oss.py` の `UNCHANGED` の検査も lock を含めて見る）。設計の事実の誤りなので `design.md` の 3 か所を直し、`design-log.md` に 1 行。
5. **設計の docs の一覧に無い `docs/architecture/resources/lab-ec2.md:17`、`agentcore-bedrock.md:19`、`emr-serverless.md:45`（「つながり」の S3 の行）も直した。**
   どれも `kb` のバケットや S3 の `docs/` の古い名前が残っていた。
6. **古い名前の検査（足した check 9）は `README.md` と `CLAUDE.md` も見る。**
   設計の (e) は `IaC/` `ops/` `tests/` `docs/` `app/`。根の 2 つにも古い名前が入りうるので足した（いまは当たらない）。
7. **`agent/kb.tf` の先頭のコメント 2 行は、ステップ 2 の取りこぼしをステップ 6・7 で直した。**
   検査 9 のパターン（`kb_bucket` など）には当たらない書き方（「docs/ に置き」「web/ lab/ stream/ と共用」）だった。

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

### テスト（2026-10-10、ステップ 7 の直しのあとに打ち直した）

全部の `tests/test_*.py`（1 本ずつ。`uv run --frozen python3 tests/<name>.py 2>&1 | tail -1`）:

```
tests/test_agentcore.py: 通過 168 / 失敗 0
tests/test_alerts.py: 通過 168 / 失敗 0
tests/test_analytics.py: 通過 544 / 失敗 0
tests/test_collectors.py: 通過 79 / 失敗 0
tests/test_dashboard_config.py: 通過 3 / 失敗 0
tests/test_graph.py: 通過 78 / 失敗 0
tests/test_kb_index.py: 通過 7 / 失敗 0
tests/test_lab_debug.py: 通過 110 / 失敗 0
tests/test_local_compose.py: 通過 145 / 失敗 0
tests/test_nautobot.py: 68 項目すべて通過
tests/test_oss.py: 通過 177 / 失敗 0
tests/test_oss_ops.py: 通過 206 / 失敗 0
tests/test_oss_roll.py: 通過 66 / 失敗 0
tests/test_stream.py: 通過 112 / 失敗 0
tests/test_sync.py: 通過 104 / 失敗 0
tests/test_workflow.py: 通過 333 / 失敗 0
```

`test_nautobot.py` は `--group web` が無いと `ModuleNotFoundError: No module named 'gradio'`。docstring の打ち方（`uv run --frozen --group dev --group web python3 tests/test_nautobot.py`）で `68 項目すべて通過`。035 の変更とは関係しない。

### 検証方法との突き合わせ（手元）

- `terraform validate -no-color`（どれも先に `init -backend=false`）: マネージドの `base/logs` `base/core` `agent` `pipeline/lab` `pipeline/analytics`、OSS の `base/logs` `pipeline/analytics` の 7 つとも `Success! The configuration is valid.`。init で lock は変わっていない（`git status` に出ない）。作った `.terraform/` は消した
- `terraform fmt -check -recursive IaC/terraform/aws-managed`: 無言
- `bash -n ops/up.sh ops/down.sh ops/oss/up.sh ops/oss/down.sh ops/check.sh`: 無言
- 設計の grep（`kb_bucket\|KB_BUCKET\|analytics/logs\|analytics/jars\|analytics/checkpoint\|/docs/`）: 当たったのは https の URL の `/docs/` だけ（`grafana.tf:115`、`tests/test_analytics.py:1776`、`docs/oss-variant.md:146`、`docs/faq-fukuda-nwc-poc.md` の 6 行、`docs/collection.md:160`、`nautobot.md:64`、`grafana.md:58`）。どれも Web の文書の URL で、S3 のパスではない。1 つ当たった `s3-buckets.md:65` の `analytics/logs/`（移した元を書いた行）は書き換えた
- `ls -l IaC/terraform/oss/base/logs/`: 7 本の symlink（`main.tf` `outputs.tf` `providers.tf` `terraform.tfvars.example` `variables.tf` `versions.tf` `.terraform.lock.hcl`）と実ファイルの `oss.auto.tfvars`
- `grep -n 'tf_apply base/' ops/up.sh ops/oss/up.sh`: `ops/up.sh` 610 base/ecr → 615 base/logs → 765 base/core、`ops/oss/up.sh` 174 → 179 → 292
- `grep -n 'base/logs' ops/down.sh ops/oss/down.sh`: コメントと echo だけで、`destroy_root` の行は無い

### セルフレビュー

- 自分: opus-5.5 / effort: high。`/robust` と反対弁護人は PM が回す（指示どおり。ここではしていない）
- 見た範囲: `git diff main...HEAD`（56 ファイル）の IaC、ops、app の全部と、docs の書き換えた行

#### 退行の注入（実測）

- ステップ 5 で、足した check 7〜9 を scratch の写しで確かめた（上）
- ステップ 7 で、`docs` を足した check 9 を確かめた: `docs/pipeline.md` の末尾に古い名前（`analytics` + `/jars`）を 1 行足すと `AssertionError: IaC / ops / app / tests / docs（cycles と verification を除く）…`。scratch に取った写しで戻し、`git status` に `docs/pipeline.md` が出ないことを見た

#### 指摘と片付け

1. **Should fix 候補（PM の判断）**: [runtime] `pipeline/analytics` の `terraform_remote_state.logs` は `try` で包んでいない
   - 破綻シナリオ: 035 より前に analytics を立てた state が残り、`base/logs` の state が無い PC で `ops/down.sh` を打つと、analytics の destroy が `logs_bucket_name` の属性が無いと言って落ちる
   - 片付け: **直していない。** 設計が「analytics を apply する時点で必ずある（try で包まない）」と決めており、2026-10-10 の時点でメインのチェックアウトの state は destroy 済み（設計のリスク 6）。古い state を持つ PC は先に旧コードの `ops/down.sh` を打つ、と `docs/deploy.md` に書いた
2. **Nit**: [security / 範囲外] KB のロール（`agent/kb.tf:344`）の `s3:GetObject` が `${local.bucket_arn}/*` のままで、assets の `web/` `lab/` `spark/` も読める
   - 片付け: **直していない。** 035 の前から同じ（前も同じバケットの全部を読めた）で、設計の範囲外。`kb/*` に絞るのは BACKLOG の候補にする（最終報告に回す）
3. **Nit**: [docs] `ops/down.sh` の「logs のバケットは残す」の echo は、`base/logs` を作る前に `ops/up.sh` が止まった環境でも出る
   - 片付け: **直していない。** そのときはバケットが無いだけで、`terraform destroy` を打っても何も消さないので害は無い
4. **Nit**: [docs] `agent/kb.tf:2-3` のコメントが古いままだった → **直した**（上の設計との差 7）
5. **Nit**: [docs] `s3-buckets.md:65` に古い名前（検査 9 は、ステップ 6 の時点で未追跡だったので見ていなかった） → **直した**。いまは追跡されていて検査 9 が見る

#### 「問題なし」とした観点と根拠

- IAM の範囲: EMR の `LogsBucket` は `logs/emr/*` だけ、Firehose の `ErrorBucket` は `logs/firehose-errors/*` だけ。assets 側の `AssetBucket` は checkpoint の読み書きに要るので Action は変えず、コメントの「ログを書く」を消した
- 閉域: logs には `DenyOutsideVpc` を付けず、`perimeter_exempt_principals` は変えていない（Firehose は logs の資源側の Deny に当たらない。assets の Deny の例外のままでも害は無い。KB のロールは assets の `kb/` を読むのでそのまま要る）
- 順序: `pipeline/analytics` が読む `base/logs` の state は、`ops/up.sh` の手順 1 で `base/core` より前に作る。`ops/down.sh` は消さないので、analytics の destroy の時点でもある
- OSS 版: `IaC/terraform/oss/base/logs` は `project = "nwc-oss"` なので、マネージド版とは別のバケットになる。OSS の analytics は symlink の `history.tf` と自前の `network.tf` の `terraform_remote_state.logs` で同じ形
- bash 3.2: 足した echo の `$PREFIX-logs-$ACCOUNT_ID は` は変数のあとが ASCII（`-` と空白）、`${OWNER}）` は波括弧で区切った
- security: `.env` 系は読んでいない。AWS は叩いていない
- Must fix: 0

### 未確認

- **AWS での動き全部（設計のリスク 1 と 2）。** EMR Serverless が logs の `emr/` に書けるか、Firehose のストリームが logs を書けなかった行の置き場にして作れるか、ライフサイクルとポリシーが設計どおりに入るか、KB の取り込みが `kb/` から通るか、`ops/down.sh` のあと assets が消え logs が残るか。設計の「検証方法」の AWS の節のとおり、PM の 1 回の検証で見る
- **古い state からの移り方（リスク 6）。** state の無いところでは当たらない。古い kb のバケットを持つ state で base/core を apply したときの置き換えは打っていない
- **OSS 版の `ops/oss/up.sh` を通した `base/logs` の apply。** validate と静的な検査だけ

## コールドレビュー後の修正

Must fix は 0。PM の判断で、Should fix 1 件と Nit 2 件をこの 1 commit で直した（Nit 1 の KB のロールの範囲と Nit 3 の up.sh の旧 kb の guard は直さない。Nit 1 は PM が BACKLOG に積む）。

- **Should fix（`docs/deploy.md` の 035 より前の state の消し方）:** 「先に `ops/down.sh` で消す」を、035 のコードの `ops/down.sh` では `base/logs` の state が無いと analytics の destroy が止まることと、2 つの消し方（035 より前のコードで消す／先に `base/logs` を apply する）に書き直した。PM の文面から 2 点変えた。戻すのは `ops` だけでなく `IaC` も（`try` 無しで読むのは analytics の terraform のコード）。戻し先は `git checkout HEAD -- ops IaC`（`git checkout -- ops` はインデックスから戻すので、3d497de の版のまま）
- **Nit 2（`ops/down.sh` と `ops/oss/down.sh` の「logs のバケットは残す」）:** echo を `if has_resources base/logs; then … fi` で囲み、state が無いか空なら出さないようにした。`tests/test_analytics.py` の検査も、囲みの中で出すことを見るように直した
- **Nit 4（`docs/architecture/resources/s3-buckets.md` の assets の「作る順」）:** `docs/deploy.md` の手順の表と同じく「`ops/up.sh` の手順 3（base/core。手順 1 の base/ecr と base/logs の後）」にした
- 確かめたこと: `uv run --frozen python3 tests/test_analytics.py` 通過 544 / 失敗 0、`tests/test_oss_ops.py` 通過 206 / 失敗 0、`tests/test_oss.py` 通過 177 / 失敗 0、`tests/test_workflow.py` 通過 333 / 失敗 0。`bash -n` は 2 本とも無言。`ops/oss/down.sh` の囲みを `if true; then` にすると、直した検査が AssertionError で落ちる
