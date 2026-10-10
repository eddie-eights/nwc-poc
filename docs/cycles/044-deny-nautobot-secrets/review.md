# レビュー: Nautobot の内部のシークレットを Web と Runtime と tools の Lambda のロールで読めなくする（044）

## Round 1

レビューモデル: PM(fable-5-1) / effort: high。2026-10-11。対象は `d6dd0ab`（エンジニア Round 1。実装モデル claude-opus-5-5 / high）。

**cold reviewer: 依頼しない。** 理由: `build.md` Round 1 のセルフレビューが PM に回した Should fix 3 件がどれも設計の範囲（Deny を置くロールと ARN の形）を変えるもので、Round 2 でコードが変わる。1 サイクル 2 回の cold review を、変わる前のコードに使わない。1 回目は Round 2 の実装のあとに呼ぶ。このラウンドは `build.md` Round 1 の `### セルフレビュー` に依った。

### build.md Round 1 の Should fix の確定（PM の分類）

| # | 指摘 | 分類 | 判断 |
| :--- | :--- | :--- | :--- |
| 1 | lab / lab-debug のロールに Deny が無く、temporal のタスクが `ssm:SendCommand`（`workflow/iam.tf:157-173`）で lab のシェルから読める。`temporal.md:63` が実態より強い | **Should fix（security）→ 直す** | 設計 Round 2 の方針 1・3 で `pipeline/lab/iam.tf` の `lab_assets` と `lab-debug.yaml` の `lab-assets` に同じ Deny。読んだ根拠: `pipeline/lab/iam.tf:17-20`（`AmazonSSMManagedInstanceCore`）、`:22-68`（`lab_assets` の SSM は `TelegrafAddress` だけ）、`lab-debug.yaml` の `Role`（`ManagedPolicyArns` に同じもの）、`workflow/iam.tf:157-173`（`RunCommand`） |
| 2 | `web.tf:22-25` の `AmazonSSMManagedInstanceCore` が `ssm:GetParameter*` を Resource `*` で許し、ほかの prefix の Nautobot のシークレットを読める | **Should fix（security）→ 直す** | 設計 Round 2 の方針 2 で ARN を `parameter/*/nautobot/<名前>` に（4 か所）。マネージドポリシーの `*` 自体を自前の許可に置き換えるのは範囲外で、QUEUE に候補を足した（2026-10-11） |
| 3 | 版指定 `--name …:1` を IAM がどの ARN で判定するか未確認（`base/core/locals.tf:30-34`、`workflow/locals.tf:235-239`） | **Should fix（missing verification）→ 147 で実測** | AWS の文書 `ps-retrieval-authorization.html`（2026-10-11 に PM が読んだ）: `GetParameter` / `GetParameters` / `GetParameterHistory` は「the requested parameter」に対して判定、`GetParametersByPath` は「the requested path」。版やラベルで ARN が変わる記述は無く、IAM の `parameter` リソース型の ARN にも版は無い。設計は名前の完全一致のままにし、QUEUE 147 に `:1` とラベルの確認を足した。すり抜けたら設計のリスク 6 の再設計 |

Nit 4（テストが `terraform fmt` の桁揃えに依存）: 直さない。最終報告に載せる。

### 自分で足した確認

- `[design 整合性]` `tests/test_lab_debug.py:118-125`: Sid / Action の突き合わせの `tf_actions` の正規表現 `(?:ecr|s3|ssm):\w+` は `ssm:GetParameter*` の `*` に合わず、「`ssm:GetParameter` を含まない」の check は Deny を足すと落ちる。設計 Round 2 の変更対象に入れた（読んだだけ。実行は Round 2 の検証 3）。
- `uv run --group dev --group web python tests/test_lab_debug.py` の末尾（`d6dd0ab`。Round 2 の前の基準値）: `通過 110 / 失敗 0`。

見ていない観点: correctness / runtime bugs / data loss / API compatibility / type safety（Round 2 の cold review 1 回目で見る）。

### 判断

- Must fix: 0
- Should fix: 3 → 1 と 2 は設計 Round 2 で直す（エンジニア Round 2）、3 は QUEUE 147 で実測
- 差し戻し: 設計 Round 2（`design.md` を上書き。2026-10-11）→ エンジニア Round 2 → cold review 1 回目

## Round 2

レビューモデル: PM(fable-5-1) / effort: high。2026-10-11。対象は `1d22174`（エンジニア Round 2。実装モデル claude-opus-5-5 / high）。

**cold reviewer: 依頼した（1 回目。opus / effort xhigh。初回ビルド直後に当たる）。** 結果は下の `## サマリ` 以下（`review-r01.md` を連結）。その前に `build.md` Round 2 の `### セルフレビュー` の未解消分を確定する。

### build.md Round 2 の Should fix の確定（PM の分類）

| # | 指摘 | 分類 | 判断 |
| :--- | :--- | :--- | :--- |
| 1 | Deny に `Condition` を足す変異を check が見ない（M-A `lab-debug.yaml` の Condition → `test_lab_debug` rc=0、M-B `pipeline/lab/iam.tf` の Condition → `test_lab_debug` / `test_workflow` rc=0、M-C `gateway.tf` の tools の `condition {}` → `test_workflow` rc=0） | **Should fix（missing tests）→ 直す** | エンジニアの再現出力（3 変異とも rc=0）に同意。Condition 付きの Deny は `aws:ViaAWSService` などで素通りの条件を作れるので、名前と Action だけでなく文全体を固定する。Round 3 で `build.md` Round 2 の提案どおり（lab-debug は `set(keys) == {Sid, Effect, Action, Resource}`、lab は `{`〜`},` の塊を切り出して全体を比較、tools は `"condition" not in` の check） |

Nit 2（コメントの `ssm:GetParameter*` はマネージドポリシーの実態と違う）、Nit 3（`pipeline/lab/iam.tf:68` と `temporal.md` が lab で `nautobot/*` 全部を塞いだように読める。実際は 3 つの名前だけで `api-token` / `url` は読める）、Nit 4（`docs/nautobot.md:59` と `nautobot.md:19` が temporal の 042 の Deny（自分の prefix だけ）を 044 の「prefix を問わない」に混ぜている。`ssm-parameter-store.md:55` の Deny の一覧に temporal worker が無い）: 直さない。最終報告に載せる。
# 044 Nautobot の内部のシークレットを Web と Runtime と tools の Lambda のロールで読めなくする — cold review Round 1

対象: `feat/148-deny-nautobot-secrets` の `3183777..1d22174`（ベース main `2e79558`）。レビュー日 2026-10-11。

## サマリ

- Must fix 0、Should fix 1、Nit 6。
- 実装は design.md（Round 2）の方針 1〜5 と変更対象ファイルの表のとおり。
  - Deny を置いた場所: `base/core` の `web.tf` の末尾と `runtime.tf` の新しいポリシー、`workflow/gateway.tf` の tools、`pipeline/lab/iam.tf` の `lab_assets` の末尾、`lab-debug.yaml` の `lab-assets` の末尾。
  - Deny の中身: 名前は 3 つ、ARN は `parameter/*/nautobot/<名前>`、Sid は `DenyNautobotSecrets`、Action は `ssm:GetParameter*`。
  - `url` と `api-token` は Deny していない。
- Should fix の 1 件はテストの穴。tools の Lambda、lab、lab-debug の Deny に `Condition` を足しても、テストが落ちない。
- 自分で走らせた結果（worktree で `uv run --group dev --group web python tests/<名前>.py`）:

  | テスト | 結果 |
  | :--- | :--- |
  | test_stream | `通過 118 / 失敗 0` |
  | test_workflow | `通過 462 / 失敗 0` |
  | test_lab_debug | `通過 113 / 失敗 0` |
  | test_nautobot | `68 項目すべて通過` |
  | test_oss | `通過 177 / 失敗 0` |
  | test_oss_ops | `通過 209 / 失敗 0` |

- `terraform validate` は、scratchpad に写したコピーに `init -backend=false` してから打った。
  - 対象は `aws-managed` の `base/core` / `workflow` / `pipeline/lab` と、`oss` の同じ 3 つ。
  - 6 つとも `Success! The configuration is valid.`。
  - `terraform fmt -check -recursive aws-managed` は rc 0。

### 見た観点 / 見ていない観点

**見た観点**

- design.md conformance。方針 1〜5、変更対象ファイル、検証 1〜5 と突き合わせた。
- correctness。
  - IAM の評価: 明示的な Deny は同じプリンシパルの全部の Allow に勝つ。Resource の `*` は `/` をまたぐ。
  - SSM のパラメータの ARN の形。
  - Terraform と CloudFormation の式。
- security。
  - Allow を持つほかのロールに抜け道が無いか。
  - `AmazonSSMManagedInstanceCore` の Resource `*` に勝つか。
  - temporal のタスクから lab への `ssm:SendCommand` を使う経路。
  - シークレットを使うロール（Nautobot の `exec_secrets`、Temporal の `execution_db_passwords`）を壊していないか。
- runtime bugs。
  - Deny した名前を `app/` の Web / Runtime / tools / lab が読んでいないか。Web が読むのは `nautobot/url` と `nautobot/api-token` だけ。
  - インラインポリシーの文字数の上限。
- data loss。
  - IAM のポリシーの in-place の更新だけで、EC2 は作り直されない。`user_data` も変わらない。
- API compatibility。`app/` は差分無し。読むパラメータの名前も変わらない。
- type safety。Resource にリストを渡す形で、`validate` が通る。
- missing tests。scratchpad に `git archive HEAD` で写したコピーに変異を入れ、テストが落ちるかを見た。

**見ていない観点**

- AWS の実機。`simulate-principal-policy` と、版やラベルの指定（`:1`）で Deny をすり抜けないかは、design の検証 6 / リスク 6 のとおり QUEUE 147 に回っている。`terraform plan` も打っていない（AWS の認証を使わない）。
- `cfn-lint` は走らせていない。
- Docker は使っていない。
- `ops/check.sh` の全部は回していない。回したのは上の 6 本だけ。
- test_lab_debug に変異を入れた実行。写したコピーは git の作業ツリーではなく、`tests/test_lab_debug.py:647` の `git ls-files` が変異の有無にかかわらず `CalledProcessError` で落ちる。そのため test_lab_debug は読んで判定した。

## Must fix

None

## Should fix

- **[missing tests] tools の Lambda、lab、lab-debug の Deny に `Condition` を足しても、テストが落ちない。**
  - **該当箇所**
    - `tests/test_workflow.py:2197-2200`: `_gw_stmt` の部分一致で見ている。
    - `tests/test_lab_debug.py:138-141`: `Sid` の行から始まる部分一致で見ている。
    - `tests/test_lab_debug.py:148-151`: `Effect` / `Action` / `Resource` を `.get` で見るだけで、キーの集合を見ない。
  - **Web と Runtime との違い**
    - Web と Runtime の Deny は、`tests/test_stream.py` が塊全体を比べる。
    - そのため対照の変異 M-D は落ちる。
    - ほかの 3 か所は、条件付きで素通りする Deny（例: `aws:ViaAWSService = "true"`）に書き換えても CI が通る。
  - **なぜ Should か**: このサイクルの目的（Deny）の守りが回帰に弱い。ただし、いまのコードの Deny は正しく、壊れてはいないので Must ではない。
  - **再現（test_workflow）**。写したコピーで変異を 1 つずつ入れ、テストを走らせ、戻す。スクリプトは `/private/tmp/claude-501/-Users-eight-Documents-Dev-sandbox-nwc-poc/c2aa9a70-e341-4a13-8f89-d44fbd03961f/scratchpad/r01/mutate.py`。worktree には書かない。
    ```
    $ git -C <worktree> archive HEAD | tar -x -C <scratchpad>/r01/mut
    $ uv run --group dev --group web python <scratchpad>/r01/mutate.py <scratchpad>/r01/mut <worktree>
    M-B pipeline/lab/iam.tf の Deny に Condition: test_workflow.py rc=0 -> 通過 462 / 失敗 0
    M-C gateway.tf の tools の Deny に condition: test_workflow.py rc=0 -> 通過 462 / 失敗 0
    M-D web.tf の Deny に Condition（対照）: test_stream.py rc=1 -> AssertionError: Web のインラインポリシー（web_assets）…
    ```
    各変異の中身:
    - M-B: `pipeline/lab/iam.tf` で、`Sid      = "DenyNautobotSecrets"` の前の行に `Condition = { Bool = { "aws:ViaAWSService" = "true" } }` を足す。
    - M-C: `gateway.tf` の tools の Deny の statement に `condition { test = "Bool" variable = "aws:ViaAWSService" values = ["true"] }` を足す。
  - **test_lab_debug は読んだだけ。**
    - 再現手順:
      1. git の作業ツリーの中（使い捨ての worktree など）で、`IaC/cloudformation/lab-debug.yaml` の `- Sid: DenyNautobotSecrets` / `Effect: Deny` の下に `Condition:` / `Bool: { "aws:ViaAWSService": "true" }` を足す。
      2. `uv run --group dev --group web python tests/test_lab_debug.py` を走らせる。
      3. 同じ作業ツリーで、`pipeline/lab/iam.tf` の `Sid` の前の行に M-B と同じ Condition を足して、もう一度走らせる。
    - どちらも落ちないと読んだ理由:
      - `:148-151` には `Condition` を見る条件が無い。
      - `:140-141` の期待文字列は `Sid` の行から始まるので、その前に挟んだ行を見ない。
  - **直し方の案**
    - lab-debug: `set(_cfn_nb) == {"Sid", "Effect", "Action", "Resource"}`。
    - lab: `{` から `},` までの塊を切り出し、全体を比べる。
    - tools: `"condition" not in _gw_stmt("DenyNautobotSecrets")`。
  - **補足**: 作業ツリーの未コミットの `review.md` Round 2 に、同じ指摘を Round 3 で直すとある。ただし `1d22174` のコードではまだ直っていないので、件数に数えた。

## Nit

- **[correctness] コメントが `AmazonSSMManagedInstanceCore` の許可を「`ssm:GetParameter*` が Resource `*`」と書いている。**
  - 該当箇所: `IaC/terraform/aws-managed/base/core/locals.tf:28`、`IaC/terraform/aws-managed/pipeline/lab/locals.tf:59`、`tests/test_lab_debug.py:129`。
  - 実態は `ssm:GetParameter` と `ssm:GetParameters` の 2 つだけ。design.md の背景と リスク 3 もそう書いている。
  - Nit の理由: コメントだけの誤りで、Deny の中身は `ssm:GetParameter*` のままでよい。
- **[security] `pipeline/lab/iam.tf:67-68` は、lab のロールに Deny を足せば 042 の Deny を lab 経由で抜けられなくなる、と読める。実際には一部が抜けられる。**
  - 042 の `DenyNautobotParameters`（`workflow/iam.tf:148-153`）は `nautobot/*` を丸ごと拒む。
  - lab の Deny は 3 つの名前だけ。そのため temporal のタスクは `ssm:SendCommand` で lab のシェルを使い、`AmazonSSMManagedInstanceCore` で `/<prefix>/nautobot/api-token` と `url` を読める。
  - QUEUE 160 で自前の `/<prefix>/*` に置き換えても、この 2 つは lab から読めるまま。
  - Nit の理由: design の方針 2 が `api-token` / `url` を Deny しないと決めているので、設計どおり。直すのはコメントの言い過ぎだけ（「3 つの名前は」と限る）。
- **[correctness] temporal のタスクの Deny を、cycle 044 の「prefix を問わない」に混ぜて書いている。**
  - 該当箇所: `docs/architecture/resources/nautobot.md:19` と `docs/nautobot.md:59`。どちらも「temporal のタスク」を「cycle 044。prefix を問わない」の括弧に入れている。
  - temporal のタスクの Deny は 042 の `DenyNautobotParameters` で、自分の prefix だけ。
  - Nit の理由: temporal のタスクの Allow も自分の prefix だけなので、「読めない」という結論は合っている。
- **[correctness] `docs/architecture/resources/ssm-parameter-store.md:55` の Deny の一覧に worker が無い。**
  - 行の見出しは「Web の EC2、Runtime、Lambda、worker、lab の EC2」。
  - worker は `workflow/iam.tf` の task のロールで、042 の Deny を持つ。
  - Nit の理由: 一覧が抜けているだけで、事実と食い違ってはいない。
- **[design.md conformance] `tests/test_nautobot.py` は design.md の変更対象ファイルの表に無いのに、変えている。**
  - 変えたのは、`:302-309` の「デバッグ用の EC2 は Nautobot を使わない」を、Deny の塊を除いてから見る形にしたこと。
  - Nit の理由: design の検証 5 は test_nautobot の失敗 0 を求めている。Deny の Resource に `nautobot` が入るので、変えないと落ちる。理由は `build.md` に逸脱として書いてある。
- **[security] Deny の ARN の region が固定（`${var.region}` / `${AWS::Region}`）。**
  - `lab-debug.yaml:363` のコメントの「どの環境の…も読ませない」は、同じアカウントの別の region の環境を含まない。`AmazonSSMManagedInstanceCore` の Resource `*` は region を問わない。
  - Nit の理由: Web、lab、lab-debug には NAT も IGW も無く、自分の region の VPC エンドポイントしか通れない。そのため、いまは別の region の SSM に届かない（読んだだけ）。
  - 閉域をやめるときは、ARN の region を `*` にすれば塞がる。

## 良かった点

- **Deny をロールを作るルートに置いた。** ほかのルートが足す `/<prefix>/*` の Allow（stream / graph / workflow の `reader_access`）にも、`WORKFLOW=0` の環境にも 1 か所で効く。design の方針 1 のとおり。
- **ARN を `parameter/*/nautobot/<名前>` にした。** `AmazonSSMManagedInstanceCore` の Resource `*` に勝ち、同じアカウントのほかの環境の 3 つも塞ぐ。`Action` は 042 と揃えて `ssm:GetParameter*` にした。
- **壊していないもの**
  - シークレットを使うロールに触っていない（Nautobot の `exec_secrets`、Temporal の init の `execution_db_passwords`）。
  - `app/` に差分が無い。
  - OSS 版のシンボリックリンクは無傷。test_oss は `通過 177 / 失敗 0`。
- **テスト**
  - Web と Runtime の Deny は、test_stream が塊全体で比べる。対照の変異 M-D で落ちることを確かめた。
  - 3 つの名前の一致を 5 か所で突き合わせる: `ops/up-common.sh`、Nautobot の `secret_parameters`、`base/core`、`workflow`、`pipeline/lab`。
  - test_lab_debug は、Allow の Statement に `ssm:` が無いことまで見る。
- **docs**: lab / lab-debug と temporal の Run Command の経路まで書いた。「まだ読める」の行は残っていない。

## ユーザーへの質問

None

### cold reviewer の指摘の確定（PM の分類）

- Should fix 1 `[missing tests]`（tools / lab / lab-debug の Deny に `Condition` を足してもテストが落ちない）: **同意。Should fix（missing tests）→ Round 3 で直す。** 根拠は cold reviewer の出力（M-B / M-C で `test_workflow` rc=0、対照 M-D で `test_stream` rc=1）と、`build.md` Round 2 のセルフレビューの出力（M-A / M-B で `test_lab_debug` rc=0）。`build.md` Round 2 の Should 1 と同じ指摘で、直し方も同じ。
- Nit 6 件: 直さない。最終報告に載せる。cold の Nit 1〜4 は `build.md` Round 2 の Nit 2〜4 と重なる。Nit 5（`test_nautobot.py` が設計の表に無い）は `build.md` Round 2 に逸脱として記録済み。Nit 6（Deny の ARN の region が固定。閉域なので別 region の SSM に届かない）は設計のとおりで、閉域をやめるサイクルがあればそこで ARN の region を `*` にする。

### 判断

- Must fix: 0
- Should fix: 1（missing tests。`build.md` Round 2 の Should 1 と cold review の Should 1 は同一）→ エンジニア Round 3 で直す
- Nit: 6（直さない）
- 次: エンジニア Round 3（テストの硬化だけ。実装ファイルは触らない）→ 実装が変わらなければ cold reviewer 2 回目は呼ばず、Round 3 のセルフレビューをそのラウンドのレビューにする → 完了判定

## Round 3

レビューモデル: PM(fable-5-1) / effort: high。2026-10-11。対象は `8ae95e1`（エンジニア Round 3。実装モデル claude-opus-5-5 / high）。

**cold reviewer: 依頼しない（2 回目は呼ばない）。** 理由: `1d22174..8ae95e1` の差分は `build.md` / `review.md` / `tests/test_lab_debug.py` / `tests/test_workflow.py` だけで、実装ファイル（`IaC/` / `app/` / docs）は cold review 1 回目の対象から 1 バイトも変わっていない（テストだけのラウンド）。このラウンドは `build.md` Round 3 の `### セルフレビュー` に依った。

### build.md Round 3 のセルフレビューの確定（PM の分類）

- Should fix 1・2（`override_policy_documents` での上書き、ポリシーの付け先の差し替えをテストが見ない。反対弁護人の Nit をエンジニアが Should に上げて直した）: 直したままでよい。M-F / M-G / M-H が rc=1 の出力は `build.md` Round 3。
- Nit 4（tools の check はコメント行を許さず lab と揃っていない）、Nit 5（`tf_actions` が `IndexError` で落ちて check の名前が出ない）: 直さない。最終報告に載せる。

### 完了判定の取り直し（PM が `8ae95e1` で実行）

全テスト（`uv run --group dev --group web python tests/<名前>.py` の末尾）:

| テスト | 結果 |
| :--- | :--- |
| test_stream | `通過 118 / 失敗 0` |
| test_workflow | `通過 462 / 失敗 0` |
| test_lab_debug | `通過 113 / 失敗 0` |
| test_oss_ops | `通過 209 / 失敗 0` |
| test_oss | `通過 177 / 失敗 0` |
| test_nautobot | `68 項目すべて通過` |

Round 2 の Should fix（Deny に `Condition` を足してもテストが落ちない）の元の再現手順を、いまのコードで実行し直した（scratchpad の `mut044.py`。ファイルを書き換えてテストを走らせ、元に戻す）:

```
M-A IaC/cloudformation/lab-debug.yaml -> tests/test_lab_debug.py rc=1
    AssertionError: lab-debug のロールの lab-assets の末尾に DenyNautobotSecrets（… キーは Sid / Effect / Action / Resource …
M-B IaC/terraform/aws-managed/pipeline/lab/iam.tf -> tests/test_lab_debug.py rc=1
    AssertionError: lab のロールの lab_assets の Statement の末尾は DenyNautobotSecrets（Sid・Effect = Deny・Action = ssm:GetParameter*・Resource = local.nautobot_secret_parameter_arns …
M-B2 IaC/terraform/aws-managed/pipeline/lab/iam.tf -> tests/test_workflow.py rc=0
M-C IaC/terraform/aws-managed/workflow/gateway.tf -> tests/test_workflow.py rc=1
    AssertionError: tools の Lambda のロールの文書（gateway.tf の tools）に DenyNautobotSecrets（sid・effect = Deny・actions = ssm:GetParameter*・resources = local.nautobot_secret_parameter_arns …
```

M-B2 は lab の `iam.tf` の Condition を `test_workflow` が見ないという結果だが、lab の Statement の形は `test_lab_debug` の担当（M-B で rc=1）で、`test_workflow` が lab から見るのは `locals.tf` の名前の一致だけ。スイート全体では落ちるので解消とする。`terraform validate` と `fmt -check` は IaC が `1d22174` から不変なので取り直さない（`1d22174` での結果は `build.md` Round 2 と cold review 1 回目の 6 ルート Success / rc 0）。

### 判断

- Must fix: 0
- Should fix: 0（Round 2 の 1 件は解消。上の出力）
- Nit: 直さないもの 8 件（cold review 1 回目の 6 件、`build.md` Round 3 の Nit 4・5）。最終報告に載せる
- 全体設計 HTML: 触らない（IAM の Deny を足しただけで、構成要素・データの流れ・配置は変わらない）
- AWS の実機確認（design の検証 6）は QUEUE 147 でまとめて行う
- サイクル完了（2026-10-11）。QUEUE 148 を完了にする
