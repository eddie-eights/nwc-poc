# build: Nautobot の内部のシークレットを Web と Runtime と tools の Lambda のロールで読めなくする（044）

## Round 1

実装モデル: claude-opus-5-5 / effort: high（PM のサブエージェント。設計モデル fable-5-1 は上位なのでそのまま実装）

### 変更ファイル

- `IaC/terraform/aws-managed/base/core/locals.tf`: `nautobot_secret_parameter_arns`（`parameter/${local.name_prefix}/nautobot/` の secret-key / admin-password / db-password。3 行のリテラル）
- `IaC/terraform/aws-managed/base/core/web.tf`: `web_assets` の Statement の末尾に `DenyNautobotSecrets`（Deny、`ssm:GetParameter*`、`local.nautobot_secret_parameter_arns`）
- `IaC/terraform/aws-managed/base/core/runtime.tf`: `aws_iam_role_policy.runtime_deny_nautobot_secrets`（`${local.name_prefix}-runtime-deny-nautobot-secrets`、`role = aws_iam_role.runtime.id`、Deny 1 つ）。冒頭のコメントを直した
- `IaC/terraform/aws-managed/workflow/locals.tf`: `nautobot_secret_parameter_arns`（`parameter${local.param_prefix}/nautobot/` + 3 つ）
- `IaC/terraform/aws-managed/workflow/gateway.tf`: `data.aws_iam_policy_document.tools` の `Parameters` の直後に `DenyNautobotSecrets`
- `IaC/terraform/aws-managed/workflow/proposals.tf`: `reader_access` の前のコメントに 1 行（Deny は base/core）
- `tests/test_stream.py`: check 4 つ（locals の 3 つ、web の Deny と既存の Allow、runtime の Deny だけのポリシー、OSS 版のリンク）
- `tests/test_workflow.py`: check 4 つ（gateway の Deny と Parameters の Allow、workflow の locals、名前が up-common.sh と nautobot の secret_parameters と base/core と揃う、reader_access に Deny が無い）
- docs: `docs/architecture/resources/web-ec2.md`（:21）、`ssm-parameter-store.md`（:42、:55、知見に 1 項目）、`nautobot.md`（:19）、`temporal.md`（:63、:119）、`docs/nautobot.md`（:59）

### 設計からの逸脱

- `nautobot.md:19` の文言: 設計の「3 つは Nautobot のタスクと Temporal の init だけが読める」は事実と違う（lab の EC2 のロールは `AmazonSSMManagedInstanceCore` で `ssm:GetParameter*` が `*`。`pipeline/lab/iam.tf:17-20`）。「使うのは Nautobot のタスクと Temporal の init。Web / Runtime / tools の Lambda / temporal のタスクは Deny。lab の EC2 のロールはまだ読める」に書いた。`ssm-parameter-store.md` の知見にも lab の 1 行を足した
- ARN のリストは for 式ではなく 3 行のリテラル（設計は「list」とだけ書いている）

### 変異（実装ステップ 4）

`/private/tmp/.../scratchpad/mutate.py` で 1 つずつ入れ、テストを打って戻した（`git status` で戻ったことを確認）。

```
M1 base/core の locals から db-password を抜く（= web.tf の Deny の Resource から db-password が抜ける）: test_stream.py rc=1 -> AssertionError: base/core の locals.tf の nautobot_secret_parameter_arns は …
M2 web.tf の Deny を Allow にする: test_stream.py rc=1 -> AssertionError: Web のインラインポリシー（web_assets）の Statement の末尾に DenyNautobotSecrets …
M3 runtime.tf の Deny の Action を ssm:GetParameter にする: test_stream.py rc=1 -> AssertionError: Runtime のロールに aws_iam_role_policy.runtime_deny_nautobot_secrets …
M4 runtime.tf のポリシーの role を web にする: test_stream.py rc=1 -> AssertionError: Runtime のロールに aws_iam_role_policy.runtime_deny_nautobot_secrets …
M5 gateway.tf の Deny の effect を消す: test_workflow.py rc=1 -> AssertionError: tools の Lambda のロールの文書（gateway.tf の tools）に DenyNautobotSecrets …
M6 workflow の locals の db-password を api-token にする: test_workflow.py rc=1 -> AssertionError: workflow の locals.tf の nautobot_secret_parameter_arns は …
M7 base/core の locals から db-password を抜く（workflow 側の名前の一致 check）: test_workflow.py rc=1 -> AssertionError: Deny する 3 つの名前は、ops/up-common.sh の ensure_nautobot_secrets …
M8 reader_access に Deny を足す: test_workflow.py rc=1 -> AssertionError: proposals.tf の reader_access（Runtime と Web）は Parameters の Allow のままで Deny を持たない …
```

### 検証（最後の編集のあとに取り直した出力）

1 / 2 / 5. テスト（`uv run --group dev --group web python tests/<file>.py` の末尾）

```
test_stream.py rc=0: 通過 118 / 失敗 0
test_workflow.py rc=0: 通過 462 / 失敗 0
test_oss_ops.py rc=0: 通過 209 / 失敗 0
test_oss.py rc=0: 通過 177 / 失敗 0
test_nautobot.py rc=0: 68 項目すべて通過
test_graph.py rc=0: 通過 78 / 失敗 0
test_agentcore.py rc=0: 通過 168 / 失敗 0
test_lab_debug.py rc=0: 通過 110 / 失敗 0
```

3. `terraform init -backend=false` → `validate`（Terraform v1.16.0。`TF_DATA_DIR` をスクラッチパッドに置いたので `.terraform/` はリポジトリに作っていない。`.terraform.lock.hcl` は変わっていない）

```
== IaC/terraform/aws-managed/base/core
init rc=0
Success! The configuration is valid.
== IaC/terraform/aws-managed/workflow
init rc=0
Success! The configuration is valid.
== IaC/terraform/oss/base/core
init rc=0
Success! The configuration is valid.
== IaC/terraform/oss/workflow
init rc=0
Success! The configuration is valid.
fmt -check rc=0
```

4. `git status --short IaC/terraform/oss` → 空。`git diff --stat -- app/` → 空。

6. AWS の実機: 未実行（QUEUE 147 で PM が打つ）。

### セルフレビュー

- 自分: claude-opus-5-5 / effort high（サブエージェントの中では切り替えられない）
- 反対弁護人: `Agent`（general-purpose、model opus、effort xhigh）。文脈（方針・迷った点）を渡し、読み取り専用。終了後 `git status --porcelain -uall` で新しいファイルが無いことを確認

#### 指摘と片付け

1. Should fix [docs の正確さ] `docs/architecture/resources/nautobot.md:19` — 「3 つは Nautobot のタスクと Temporal の init だけが読める」は誤り。lab の EC2 のロール（`pipeline/lab/iam.tf:17-20` の `AmazonSSMManagedInstanceCore`）と lab-debug のロール（`IaC/cloudformation/lab-debug.yaml:339`）は 3 つとも読める。temporal のタスクのロールは lab へ `ssm:SendCommand`（`workflow/iam.tf:157-173`）できるので、lab 経由で master のパスワードを引く経路が 042 から残っている（`temporal.md:63` の「ECS Exec のシェルから引くこともできない」はこの経路を書いていない）。再現: 上の 2 ファイルを読んで確認（AWS では未確認）。片付け: 044 で書いた文言は直した（逸脱の 1 つ目）。lab と lab-debug のロールへの Deny と、`temporal.md:63` の文言は範囲外なので **PM へ**（QUEUE の候補）
2. Should fix [security / 範囲外] `base/core/web.tf:22-25` — Web のロールは `AmazonSSMManagedInstanceCore` で全 prefix の `ssm:GetParameter*` を持つ。Deny は自分の prefix の 3 つだけなので、同じアカウントのほかの環境（別 OWNER、OSS 版と並べたとき）の Nautobot のシークレットは読める。自分の prefix には Deny が勝つ。再現: `web.tf:22-25` を読んだだけ。片付け: 設計の範囲外（prefix をまたぐ Deny は設計に無い）。**PM へ**
3. Should fix [security / 未確認] `base/core/locals.tf:30-34`、`workflow/locals.tf:235-239` — `GetParameter --name /<prefix>/nautobot/db-password:1`（版やラベルの指定）を IAM がどの ARN で判定するか文書で確かめられなかった（WebFetch で `sysman-paramstore-access.html` を見たが本文が取れず）。版の指定を含む ARN で判定されるなら完全一致の Deny をすり抜ける。格下げの根拠を実測できないので Should fix のまま。片付け: Resource の末尾を `*` にするのは設計（3 つの ARN の完全一致）の変更なので実装で吸収しない。**PM へ**（QUEUE 147 の実機確認に `--name …/db-password:1` → `AccessDeniedException` を足すか、設計を変えるか）
4. Nit [テストの脆さ] `tests/test_stream.py`（`_nb_deny_block`）、`tests/test_workflow.py`（`sid       = "…"` で切る）— `terraform fmt` の桁揃えに依存する。意味の同じ別の書き方で誤って落ちる方向だけで、誤って通る形は反対弁護人も見つけていない。直さない

#### 問題なしとした観点と根拠

- インラインポリシーの合計（10,240 字の上限）: ソースから概算（自分）、反対弁護人が prefix を入れて python で数えて Web 約 4,641 / Runtime 約 3,500
- 3 つの名前を正規に読む処理が Web / Runtime / tools の Lambda に無い: `grep -rn "nautobot/\(admin-password\|secret-key\|db-password\)"` を `app/`・`ops/`・`IaC/` に打ち、読むのは pipeline/nautobot の locals と up-common.sh の作成だけ。`app/dashboard/nautobot_api.py` は `nautobot/url` と `nautobot/api-token` だけ
- OSS 版の prefix の一致とリンク: test_stream の check と test_oss（177 通過）、反対弁護人が `ops/oss/up.sh:56` と `oss.auto.tfvars` を照合
- `GetParametersByPath` の Allow はどのロールにも無い（反対弁護人の grep）
- テストが退行を縛ること: 変異 M1〜M8 が全部落ちる（上の出力）

#### 未解消

- Must fix: 0
- Should fix（PM へ）: 1（lab / lab-debug のロールと temporal.md:63 の文言）、2（prefix をまたぐ読み取り）、3（版指定の ARN の判定。147 で確かめるか設計を変える）
- Nit: 4
