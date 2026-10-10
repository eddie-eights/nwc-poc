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
