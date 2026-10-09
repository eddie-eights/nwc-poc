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
