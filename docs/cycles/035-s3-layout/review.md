# S3 の置き場を整える（035）のレビュー

## Round 1

実行モデル: cold reviewer は fable 5.1（`Agent` に model を渡さず親を継承。worktree `agent-a313aa0b99a64d5cb`、HEAD 75a879a）。PM の確認も fable 5.1。実装は opus 5.5 / high（build.md）。
対象: `main...feat/035-s3-layout` の 75a879a まで（56 ファイル、+955 / -148）。cold reviewer に依頼した（初回ビルド直後）。
注: この回の cold reviewer は `review-r01.md` を書かず返り値に本文を返した。下の「cold reviewer の結果」はその返り値をそのまま貼ったもの。また依頼時に build.md のパスも渡してしまった（スキルは渡さないと定めている。2 回目は design.md と変更ファイル一覧だけにする）。

### cold reviewer の結果

### cold review: S3 の置き場を整える（035）`feat/035-s3-layout`（main..75a879a、7 commits）

#### サマリ

Must fix は無し。`kb` → `assets` の改名は remote_state の参照・output 名・IAM の ARN・ops の変数名まで食い違い無く揃っていて、`base/logs` も design.md のとおり（名前、7 日のライフサイクル、`DenyInsecureTransport` だけ、`ops/down.sh` が消さない）。OSS 版の 7 リンク + `oss.auto.tfvars`、`up.sh` の ecr → logs → core の順、`terraform validate`（managed / oss の base/logs と pipeline/analytics の 4 ルート）、`terraform fmt -check`、主要テスト 8 本の通過を手元で確かめた。Should fix は 1 件で、build.md が自己申告している「`terraform_remote_state.logs` を `try` で包まない」の扱い（コードではなく deploy.md の手順の正確さの問題として挙げる）。

#### 見た観点 / 見ていない観点

見た: 差分全体（IaC / ops / app / tests / docs）、`base/core` の 5 リソースの改名と参照元（agent / pipeline/lab / pipeline/analytics / oss analytics）、`base/logs` の全ファイルと `oss/base/logs` の `ls -la`、EMR 実行ロールと Firehose ロールの S3 文（`bucket` と `bucket/<prefix>/*` の両方）、`ops/up.sh` / `ops/oss/up.sh` の apply 順と `ASSETS_BUCKET`、`ops/down.sh` / `ops/oss/down.sh` の echo で使う `PREFIX` / `ACCOUNT_ID` / `OWNER` の定義位置（どちらも echo より前）、`ops/check.sh` の ROOTS、旧名の grep（`kb_bucket|KB_BUCKET|analytics/logs|analytics/jars|analytics/checkpoint|logs_prefix|土台のバケット`）、tests の新しい 9 項目の中身、docs 8 ファイルの差分。
見ていない（未確認）: AWS 上の動作すべて（EMR が `emr/` に書けるか、Firehose が `firehose-errors/` に落とせるか、`filter {}` のライフサイクルが実際に 7 日で消すか）、旧 state からの置き換え（destroy + create）の実挙動、`ops/oss/up.sh` の通し実行。`.env` / シークレットは読んでいない。AWS は叩いていない。

#### Must fix

無し。

#### Should fix

1. **[docs / 運用] `docs/deploy.md:240-241` の「先に `ops/down.sh` で消してから」は、035 を取り込んだチェックアウトでは通らない。**
   `IaC/terraform/aws-managed/pipeline/analytics/locals.tf:50-57`（OSS は `IaC/terraform/oss/pipeline/analytics/network.tf:37-43`）の `terraform_remote_state.logs` は `try` 無しで、`base/logs/terraform.tfstate` が無いと analytics の plan / destroy が止まる。シナリオ: 035 より前に `up.sh` を打った state（analytics にリソースあり、`base/logs` の state 無し）を持つチェックアウトで `git pull` 後に新しい `ops/down.sh` を打つ → `has_resources pipeline/analytics` は state list だけなので通り、`destroy_root pipeline/analytics` の `terraform destroy` が logs の state を読めずに失敗 → analytics を残したまま base/core の destroy へ進み、旧 `kb` バケットが EMR のジョブの下で消える。
   直し方は 2 つのどちらか（PM の判断）: (a) `ops/down.sh` / `ops/oss/down.sh` に「analytics にリソースがあり `base/logs` の state が空なら、先に `tf_apply base/logs` する（か die して手順を出す）」を足す。`up.sh` の 596-606 行目に同種の旧 state の guard があるので型は揃う。(b) deploy.md:240 を「035 より前のコードの `ops/down.sh` で消す（`git checkout 3d497de -- ops` 等）か、先に `terraform -chdir=IaC/terraform/aws-managed/base/logs apply -var owner=<OWNER>` を打つ」と正確に書く。
   注: state はメインのチェックアウトにしか無く、AWS は 2026-10-10 に全部消えている（design.md のリスク 6）ので、いま現実に踏む人はいない見込み。コード側（`try` 無し）は「必ずある前提」として妥当で、`try(..., "")` にすると IAM の ARN が `arn:aws:s3:::/emr/*` になって別の壊れ方をするので、コードを変える案は推さない。

#### Nit

1. **[IAM] `IaC/terraform/aws-managed/agent/kb.tf:341-348` の `S3Read` が `${local.bucket_arn}/*` のまま。** KB のロールが `web/` `lab/` `spark/`（checkpoint も）を読める。`inclusion_prefixes = ["kb/"]`（kb.tf:407）に合わせて `${local.bucket_arn}/kb/*` と、`S3List` に `s3:prefix` の条件を付けると、035 で分けた意味が IAM にも出る。035 の設計の範囲外（build.md も Nit と認識）なので BACKLOG 候補。
2. **[ops] `ops/down.sh:129-130` / `ops/oss/down.sh:88-89` の「logs のバケットは残す」は、`base/logs` の state が空（035 の `up.sh` を一度も打っていない PC）でも出る。** 実害は無い（文言が事実と違うだけ）。`has_resources base/logs` で囲めば正確になる。
3. **[ops] `ops/up.sh` に旧 `aws_s3_bucket.kb` の state を止める guard が無い。** 596-606 行目は Telegraf の SG の旧 state を `die` で止めているが、`aws_s3_bucket.kb` が state にあっても止めず、`base/core` の apply が中身ごと置き換える（`force_destroy`）。design.md のリスク 6 で「置き換えでよい」と決めており、deploy.md:240 にも書いてあるので Nit。Should fix 1 を (a) で直すなら同じ guard をここにも置くと揃う。
4. **[docs] `docs/architecture/resources/s3-buckets.md:88` の「作る順」の assets 列「base/ecr → base/logs → base/core（`ops/up.sh` の手順 1 の後）」が読みにくい。** 手順 3 で作るので「手順 3（logs の後）」の方が deploy.md:195 の表と一致する。
5. **[tests] `tests/test_analytics.py:1456` の旧名の走査は `docs/` の `.md` も見るが、`土台のバケット` のような日本語の旧表現は見ていない。** いまは残っていない（grep 済み）ので指摘だけ。

#### 良かった点

- 改名が output 名（`assets_bucket_name/arn`）→ 読む側 3 ルート → ops の変数（`ASSETS_BUCKET`）→ tests（`test_workflow.py:925` まで）→ docs まで一貫していて、旧名の grep が cycles / verification 以外で 0 件。
- `base/logs` を `base/core` より前に、`ops/down.sh` の外に置いた設計の理由（VPC の id を知らない、VPC より長生き）がコードのコメント・s3-buckets.md・FAQ で同じ言葉になっている。
- EMR / Firehose の S3 文がどちらも `bucket`（List 系）と `bucket/<prefix>/*`（オブジェクト系）の 2 文に分かれていて、prefix を越えない。Firehose の `ErrorBucket` は AWS が要求する 6 アクション（Put/Get/AbortMultipart + List/ListMultipart/GetBucketLocation）を満たしている。
- `tests/test_oss_ops.py:437` の `DOWN_ROOTS` で「up は 10、down は 9」を明示し、`destroy_root base/logs` が無いことを正規表現で止めている。
- `IaC/terraform/oss/base/logs/oss.auto.tfvars` のコメントが「OSS のバケットはマネージドと別の 1 本」と書いていて、両版の state が混ざらないことを `test_oss.py` の remote_state の走査が守っている。
- `terraform validate` が 4 ルートとも通り、`filter {}` 付きのライフサイクルは provider 6.64 の構文として受かる（7 日で消える実挙動は未確認）。

#### ユーザーへの質問

- Should fix 1 を (a) down.sh の guard で直すか、(b) deploy.md の手順を正確にするだけで済ませるか。実際に旧 state を持つチェックアウトが無いなら (b) で十分。
- Nit 1（KB ロールの `kb/*` 絞り込み）を BACKLOG に積むか。

#### 打ったテスト（worktree `/Users/eight/Documents/Dev/sandbox/nwc-poc/.claude/worktrees/agent-a313aa0b99a64d5cb`、HEAD 75a879a、作業ツリーは clean）

```
bash -n ops/up.sh ops/down.sh ops/oss/up.sh ops/oss/down.sh ops/check.sh
bash -n OK

uv run --frozen python3 tests/test_analytics.py
通過 544 / 失敗 0

uv run --frozen python3 tests/test_oss.py
通過 177 / 失敗 0

uv run --frozen python3 tests/test_oss_ops.py
通過 206 / 失敗 0

uv run --frozen python3 tests/test_workflow.py
通過 333 / 失敗 0

uv run --frozen python3 tests/test_lab_debug.py
通過 110 / 失敗 0

uv run --frozen python3 tests/test_agentcore.py
通過 168 / 失敗 0

uv run --frozen --group dev --group web python3 tests/test_nautobot.py
68 項目すべて通過

terraform fmt -check -recursive IaC/terraform/aws-managed/{base/logs,pipeline/analytics,base/core,agent,pipeline/lab}
fmt OK

TF_DATA_DIR=<scratchpad> terraform -chdir=IaC/terraform/aws-managed/base/logs init -backend=false && validate
Success! The configuration is valid.
TF_DATA_DIR=<scratchpad> terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics init -backend=false && validate
Success! The configuration is valid.
TF_DATA_DIR=<scratchpad> terraform -chdir=IaC/terraform/oss/pipeline/analytics init -backend=false -lockfile=readonly && validate
Success! The configuration is valid.
TF_DATA_DIR=<scratchpad> terraform -chdir=IaC/terraform/oss/base/logs init -backend=false -lockfile=readonly && validate
Success! The configuration is valid.
```

build.md の件数（analytics 535 → 544、ほか据え置き）と一致。`terraform init` は `TF_DATA_DIR` をスクラッチに向けたので、worktree に `.terraform/` は残っていない。commit / push / AWS の操作はしていない。

### PM の確認と分類（93db6f4 で修正）

- **Should fix 1（`docs/deploy.md` の 035 より前の state の消し方）** → Should fix のまま、(b) 手順を正確に書く方で解消。`IaC/terraform/aws-managed/pipeline/analytics/locals.tf:50` の `terraform_remote_state.logs` に `try` が無いことは読んで確認（読んだだけ。旧 state を持つチェックアウトは無く、再現はしていない）。コードは変えない（`try(..., "")` にすると EMR / Firehose の IAM の ARN が `arn:aws:s3:::/emr/*` になる、という reviewer の指摘に同意）。直した文面は `docs/deploy.md:244-245`（戻すのは `ops IaC`、戻し先は `git checkout HEAD -- ops IaC`）
- **Nit 1（KB のロールが assets 全体を読める）** → 直さない。BACKLOG に「KB のロールが読める S3 の範囲を assets の kb/ に絞る」で積む
- **Nit 2（down.sh の「logs のバケットは残す」が state 無しでも出る）** → 直した。`ops/down.sh:129-132` / `ops/oss/down.sh:88-91` を `if has_resources base/logs; then … fi` で囲み、`tests/test_analytics.py` の検査も囲みを見る
- **Nit 3（up.sh に旧 `aws_s3_bucket.kb` の guard が無い）** → 直さない。設計のリスク 6 で「置き換えでよい」と決めている
- **Nit 4（s3-buckets.md の「作る順」）** → 直した。「`ops/up.sh` の手順 3（base/core。手順 1 の base/ecr と base/logs の後）」
- **Nit 5（旧名の走査が日本語の旧表現を見ない）** → 直さない。reviewer の grep で残っていないことは確認済み

### テスト（PM が 93db6f4 で打ち直し。worktree `agent-a310d7ae208642cd2`）

```
for t in test_analytics test_oss_ops test_oss test_workflow; do uv run --frozen python3 tests/$t.py 2>&1 | tail -1; done
通過 544 / 失敗 0
通過 206 / 失敗 0
通過 177 / 失敗 0
通過 333 / 失敗 0
```

## Round 2

実行モデル: cold reviewer は fable 5.1（`Agent` に model を渡さず親を継承。この worktree の HEAD 6aba1c9 を読んだ）。PM の確認も fable 5.1。
対象: `main...feat/035-s3-layout` の 6aba1c9 まで（93db6f4 の直しと managed storage の有効化を含む）。cold reviewer に依頼した（完了判定の直前）。結果は `review-r02.md` から連結。

# S3 の置き場を整える（035） cold review Round 2

- 対象: ブランチ `feat/035-s3-layout`、HEAD `6aba1c9`（比較元 `main`）。worktree `.claude/worktrees/agent-a310d7ae208642cd2` の中で確認した
- 正本: `docs/cycles/035-s3-layout/design.md`（「追加（managed storage）」の節を含む）
- 前ラウンド（`review.md`、75a879a 時点）の未解消 Must fix: 無し。Round 1 の Should fix 1 件（deploy.md の古い state の消し方）と Nit 2・4 は直っていることを確認した。Nit 1（KB のロールの `S3Read` が `/*`）と Nit 3（`ops/up.sh` に古い `aws_s3_bucket.kb` の state の番が無い）は意図して残した扱いなので、この回では数え直さない

## サマリ

Must fix 0 件、Should fix 0 件、Nit 1 件。design.md との食い違いは見つからなかった。

- 改名（`kb` → `assets`、`analytics/` → `spark/`、`docs/` → `kb/`）は base/core・agent・pipeline/lab・pipeline/analytics・OSS の analytics・`ops/*.sh`・`app/spark/snmp_sinks.py` の全部で揃っている。`main` 側の古い名前は IaC / ops / app / tests / docs に残っていない（残っているのは `s3-buckets.md:15` と `deploy.md:246` の「改名した」という説明、`telegraf/` の docker のパス、`-kb-index` の Lambda の名前だけで、いずれも意図どおり）
- 新しいルート `IaC/terraform/aws-managed/base/logs` は design のとおり: 名前 `<prefix>-logs-<account>`、`force_destroy`、PAB 全部 true、SSE-S3、`BucketOwnerEnforced`、ライフサイクル `filter {}` + `expiration 7 日` + `abort_incomplete_multipart_upload 7 日`、ポリシーは `DenyInsecureTransport` だけ（`DenyOutsideVpc` 無し、`aws:SourceVpc` の文字列も無し）、output `logs_bucket_name` / `logs_bucket_arn`。`variables.tf` / `providers.tf` / `versions.tf` は base/ecr と同じ骨格（variables は lab/workflow のリポジトリの 2 変数だけ無い）
- IAM の絞り方は design のとおり: EMR の実行ロールは `LogsBucket`（`emr/*` に PutObject / GetObject）と `LogsBucketList`（バケットに ListBucket / GetBucketLocation）、Firehose のロールは `ErrorBucket` / `ErrorBucketList` が logs を向き、`s3_configuration.bucket_arn = local.logs_bucket_arn`。`emr_perimeter` の付け方は変えていない。KB のロールの `S3List` に `s3:prefix` の条件が無いので `inclusion_prefixes = ["kb/"]` はそのまま通る
- `ops/up.sh` / `ops/oss/up.sh` は `tf_apply base/ecr` → `tf_apply base/logs` → `tf_apply base/core` の順で、`ops/down.sh` / `ops/oss/down.sh` に `destroy_root base/logs` は無く、残す旨の echo は `has_resources base/logs` で囲んである（`PREFIX` / `OWNER` / `ACCOUNT_ID` はその前に定義済み: `ops/down.sh:50`、`ops/oss/down.sh:45`）
- OSS 版 `IaC/terraform/oss/base/logs` は 7 本の相対シンボリックリンク（`.terraform.lock.hcl` を含む）と実ファイル `oss.auto.tfvars`（`project = "nwc-oss"`）で、`ls -L` で全部解決する
- managed storage の追加: `outputs.tf` の `managedPersistenceMonitoringConfiguration = { enabled = true }` と、test_analytics の検査、`emr-serverless.md` の「AWS では未確認」の注記が揃っている

### 見た観点

- design.md 整合性（設計方針・変更一覧・検証の節・リスク 1〜6 と、実装・テスト・docs の対応）
- correctness: 改名の取りこぼし、`terraform_remote_state.logs` のパス、`tf_apply` の順、down.sh の分岐、OSS のリンクの解決
- security: logs のバケットのポリシー・PAB・SSE、EMR と Firehose のロールの Resource の範囲、KB のロールの prefix
- runtime bugs: `set -uo pipefail` の下で未定義変数を踏まないか（`PREFIX` / `OWNER` / `ACCOUNT_ID`）
- data loss: 古い `kb` のバケットの state を持つ PC で置き換えになる件の docs（`deploy.md:246-253`）
- missing tests: design の「検証」に挙げたテストが tests/ に入っているか（test_analytics の 9 項目 + managed storage の 1 項目、test_oss の TF_ROOTS と「10 ルート」、test_oss_ops の `DOWN_ROOTS`、test_workflow の `ASSETS_BUCKET`）
- 自分で実行した検証（全部 worktree の中。`git status --short` は実行後も空）
  - `uv run --frozen python3 tests/test_analytics.py` → `通過 544 / 失敗 0`（design の「535 より増える」を満たす）
  - `tests/test_oss.py` → `通過 177 / 失敗 0`、`tests/test_oss_ops.py` → `通過 206 / 失敗 0`、`tests/test_workflow.py` → `通過 333 / 失敗 0`、`tests/test_lab_debug.py` → `通過 110 / 失敗 0`、`tests/test_agentcore.py` → `通過 168 / 失敗 0`
  - `terraform validate`（`TF_DATA_DIR` を scratchpad に向けて init）: aws-managed の `base/logs` / `base/core` / `agent` / `pipeline/lab` / `pipeline/analytics` と oss の `base/logs` / `pipeline/analytics` の 7 ルート全部 `Success! The configuration is valid.`
  - `terraform fmt -check -recursive IaC/terraform/aws-managed` → 差分無し
  - `bash -n ops/up.sh ops/down.sh ops/check.sh ops/oss/up.sh ops/oss/down.sh` → 無言
  - 古い名前の grep（`kb_bucket` / `KB_BUCKET` / `analytics/logs` / `analytics/jars` / `analytics/checkpoint` / `docs/` の取り込み）→ 上に書いた意図どおりの箇所以外 0 行

### 見ていない観点

- AWS での動作。EMR Serverless が logs の `emr/` に書けるか、Firehose が `firehose-errors/` に落とせるか、managed storage の Spark UI が開くか、ライフサイクルの `filter {}` が全オブジェクトに効くか（design のリスク 1・2 と「追加」の節が「未確認」としている通り）。`terraform apply` / `plan` も打っていない
- 古い `kb` の state を持つ PC で `deploy.md:250` の手順 1（`git checkout 3d497de -- ops IaC` → `ops/down.sh` → `git checkout HEAD -- ops IaC`）が通るか。コードを読んだだけ
- `ops/up.sh` を通しで動かす偽物の検査（test_oss_ops にある OSS 版の通しは走らせたが、マネージド版の `ops/up.sh` の通しの検査は無い）
- Bedrock の KB が `kb/` から再取り込みされるか（リスク 3）
- `docs/pipeline.md` と FAQ の managed storage の記述（build.md のとおり main にしか無いので、マージ後に別途）

## Must fix

None

## Should fix

None

## Nit

- [design.md 整合性] `IaC/terraform/aws-managed/base/core/outputs.tf:78` の `assets_bucket_arn` の description が「read by agent / pipeline/lab / pipeline/stream for IAM policies」のままで、読み手が実態と違う。`main` の `kb_bucket_arn` から文面を引き継いだもので、同じファイルの `assets_bucket_name`（73 行目）は今回の改名に合わせて `(kb/)` `(lab/)` `(spark/)` と直してある。ARN を読んでいるのは `IaC/terraform/aws-managed/agent/locals.tf:66` だけで（grep で確認。`pipeline/lab` と `pipeline/analytics` と OSS の analytics は `assets_bucket_name` だけを読み、`pipeline/stream` の `.tf` にバケットの参照は無い）、`docs/architecture/resources/s3-buckets.md` の「読む output」の行も `assets_bucket_arn` を agent・lab・analytics が読むように読める。Nit にした理由: 動作に影響せず、design の変更一覧にも無い説明文の範囲だから。直すなら description を「Read by IaC/terraform/aws-managed/agent (KB role)」程度にし、s3-buckets.md の行は name と arn で読み手を分けるか、arn の読み手を agent だけにする

## 良かった点

- Round 1 の Should fix を、手順の追加（`deploy.md:248-253` の「消し方は 2 つ」。`IaC` も戻す理由と `git checkout HEAD --` にする理由まで）で直してあり、同じ落とし穴を踏む PC が無くなる
- test_analytics の末尾の「古い名前が残っていない」検査が、自分のファイルに文字列を載せない作り（`"kb" + "_bucket"`）になっていて、検査自身が検査に引っかからない
- `ops/down.sh` の残す旨の echo を `has_resources base/logs` で囲んだので、base/logs を一度も作っていない PC で意味の無い案内が出ない
- `docs/architecture/resources/s3-buckets.md` が「なぜ 2 本に分けたか」「なぜ logs に `DenyOutsideVpc` を付けないか」を出典付きで書いていて、FAQ・firehose.md・emr-serverless.md・vpc-perimeter.md が全部そこを指している（正本が 1 つ）
- managed storage の追加が、design の節・`outputs.tf`・test_analytics の検査（`enabled = true` が 1 回だけ）・`emr-serverless.md` の「AWS では未確認」の 4 か所で揃っている

## ユーザーへの質問

None

### PM の確認と分類

- Must fix 0 / Should fix 0。Nit 1（`base/core/outputs.tf:78` の `assets_bucket_arn` の description が旧文面）は、このあとの「EMR のログの S3 を落とす」commit で一緒に直す（次のセッション）

## Round 3

実行モデル: PM の確認は fable 5.1。実装（ec5f504〜d99b856）は opus 5.5。docs の整合性レビューと修正も opus 5.5（別のサブエージェント、文脈無し）。
対象: `main...feat/035-s3-layout` の af40dc3（main 取り込み）〜 fe3eb0d（13 ファイル、pptx 2 本も描き直し）。cold reviewer は依頼していない（1 サイクル 2 回を Round 1・2 で使い切り。このラウンドのレビューは `build.md` の「追加の修正（EMR のログの S3 を落とす）」のセルフレビューと、下の docs 整合性レビューに依る）。

### 前ラウンドの未解消

- Round 2 Nit（`base/core/outputs.tf:78` の `assets_bucket_arn` の description）→ 解消。`sed -n '77,80p'` で「Read only by IaC/terraform/aws-managed/agent (IAM policy of the knowledge base role)」。

### 追加の変更（EMR のログの S3 を落とす。ユーザー決定 2026-10-10）の確認

- `grep -rn "emr_logs_prefix\|s3MonitoringConfiguration\|LogsBucketList\|logs_bucket.*emr" IaC/ tests/ ops/` → 0 行。
- テスト（worktree で実行。HEAD b39c543）: test_analytics 544 / 0、test_oss 177 / 0、test_oss_ops 206 / 0、test_workflow 333 / 0、test_lab_debug 110 / 0、test_agentcore 168 / 0。
- `terraform validate`・`fmt -check`・`bash -n` は build.md の追加ラウンドのとおり（PM は打ち直していない。読んだだけ）。

### セルフレビューの未解消（build.md 追加ラウンド）の分類

- [runtime bugs] `pipeline/analytics/access.tf:20`: 手順 7-4 の apply で logs への権限が先に消え、7-5 で起こし直すまでの旧構成のジョブが S3 にログを書けない → **Should fix → 対象外（移行時だけ、いま環境が無い）**。
  根拠: AWS MCP で `emr-serverless ListApplications`（ap-northeast-1）→ 0 件、`s3 ListBuckets` に nwc / efukuda の名前 → 0 件（2026-10-10）。旧構成のジョブは存在せず、次の `ops/up.sh` は新構成で最初から起こす。QUEUE の「Spark のジョブだけ止めて起こし直す手順」で、止めてから apply する順を書く。
- [確認のみ] `outputs.tf:93` の `configuration_overrides_json` が `SpecHash` に入るので次の up.sh で 3 ジョブとも起こし直し → checkpoint の続きから読むのでデータは落ちない。対応なし。

### docs の整合性レビュー（README / docs とコードの突き合わせ。ユーザー指示「整合性レビュー・修正まで」）

結果ファイル: scratchpad の `docs-consistency-review.md`（リポジトリには入れない）。Must 2 / Should 20 / Nit 5。

- Must 1 `s3-buckets.md:66`: 古い kb の state の消し方が「先に ops/down.sh」のまま（035 の down.sh では消し切れない。deploy.md:242-253 と食い違い）→ 直した。
- Must 2 `deploy.md:338, 352-393`: logs のバケットは down.sh で残るので、state を失うと次の up.sh の base/logs の apply がぶつかる（東京では BucketAlreadyOwnedByYou。S3 の仕様から推した、AWS 未確認）。import の手順が無い → ECR と同じ形の import 6 リソースと、`aws s3 rb --force` の代案を書いた。
- Should 20 件: ルート数 9 → 10（architecture/README.md ×4、deck ×4、oss-variant、deploy、development）、ROOTS の並びに base/logs（README、deck ×2、oss-variant、troubleshooting）、ECR 15 → 14（resources/README、deploy ×2）、Splunk の保存済みサーチ 4 → 3（deck）、`emr-serverless.md:22` の定義場所 → 全部直した。
- Nit 5 件: deck の resource 数（graph 11 / analytics 54）、development.md のテストの数、troubleshooting.md の順、s3-buckets.md:25 の「部品名で切る」、README の索引に GLOSSARY.md → 直した（pptx は索引に載せない）。
- 修正の commit: fe3eb0d（13 ファイル、pptx 2 本も描き直し）。修正後のテスト: test_oss 177 / 0、test_analytics 544 / 0（PM が打ち直した）、test_oss_ops 206 / 0（エンジニア報告）。古い数の grep は意図した 3 行だけ。

### 見た観点 / 見ていない観点

- 見た: design 整合性、correctness（古い名前の grep）、runtime bugs（移行時の権限）、missing tests（差し替えた検査が 3 通りの退行で落ちることは build.md）、docs とコードの整合。
- 見ていない: AWS での動作（この後の検証で、logs のライフサイクル 7 日、ポリシーが DenyInsecureTransport のみ、assets の `spark/` `web/` `lab/`、logs に `emr/` が無い、Firehose のストリーム、終わったジョブの Spark UI、KB の取り込み）。閉域から managed storage に書けるか。

## Round 4（AWS 検証。2026-10-10）

- 実行モデル: fable 5.1（PM）。cold reviewer は呼んでいない（1 サイクル 2 回を Round 1・2 で使い切り。実装ファイルは Round 3 から不変）。
- design の「検証方法」の AWS の節を `ops/up.sh`（`PIPELINE=1 AGENT=1 CREATE_KB=1`）で 1 回確かめた。全項目が通った。記録は [verification/20261010-aws-managed-035.md](../../verification/20261010-aws-managed-035.md)。
  - logs: ライフサイクル `Expiration.Days=7`、ポリシーの Sid は `DenyInsecureTransport` のみ、`emr/` 無し（空）
  - assets: `kb/ lab/ spark/ web/` の 4 つ。`spark/snmp_sinks.py`、`spark/jars/`、`spark/checkpoint/<uuid>/`
  - Firehose: `FailedDataOnly` の宛先が logs の `firehose-errors/alert_events/`（リスク 2 は当たらず、1 回で作れた）
  - EMR: 3 ジョブとも `managedPersistenceMonitoringConfiguration.enabled=true`、`s3MonitoringConfiguration` 無し。cancel したジョブの `GetDashboardForJobRun` が URL を返した（画面は見ていない）
  - KB: `inclusionPrefixes=["kb/"]`、取り込み `COMPLETE`（3 本、失敗 0）
  - `ops/down.sh` のあと assets が 404、logs が 200、EMR / MSK / ECS / KB ほか 0 件
- Round 3 の「見ていない観点」のうち AWS の分は解消。残るのは、7 日後に実際に消えること、Firehose が実際に落とす行、Spark UI の画面、古い `kb` の state を持つ PC の手順。

<!-- artifact: /Users/eight/Documents/repo/artifacts/projects/nwc-poc-architecture.html -->
