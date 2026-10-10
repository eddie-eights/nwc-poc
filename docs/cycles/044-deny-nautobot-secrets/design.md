# Nautobot の内部のシークレットを Web と Runtime と tools の Lambda のロールで読めなくする（044）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-11。ベースは main の `2e79558`。

## 背景

- QUEUE 148（042 の cold review Round 1 の Nit 2）。042 で master のパスワード `/<prefix>/nautobot/db-password` を temporal のタスクのロールだけ Deny した（`IaC/terraform/aws-managed/workflow/iam.tf:148-154` の `DenyNautobotParameters`。`/<prefix>/nautobot/*` を丸ごと）。同じ値を読める Allow が、ほかの 3 つのロールに残っている。
- 守るのは `db-password` だけではない。`ops/up-common.sh:407-413` の `ensure_nautobot_secrets` が作る 4 つの SecureString のうち、`secret-key`（Django の SECRET_KEY）、`admin-password`（画面の管理者）、`db-password`（RDS の master）の 3 つは Nautobot の中だけのもので、読んでよいのは Nautobot のタスクの実行ロール（`pipeline/nautobot/access.tf` の `exec_secrets`。`ssm:GetParameters` を名前で）と、Temporal の init のタスクの実行ロール（`workflow/iam.tf` の `execution_db_passwords`。`db-password` だけ）だけ。残る `api-token` と String の `url` は Nautobot の API の入口で、Web の EC2 がいま読んでいる。
- QUEUE の文面は「workflow のロール」だが、Deny を置く場所は **ロールを作っているルート**にする（下の設計方針 1）。Web と Runtime のロールは `base/core` のもので、`/<prefix>/*` の Allow は base/core（Web 自身）・stream・graph・workflow の 4 か所から付く。workflow に Deny を置くと `WORKFLOW=0` の環境（`PIPELINE=1` だけ。Nautobot と 4 つのシークレットはある）では効かない。IAM は同じプリンシパルに付くどのポリシーの Deny も全部の Allow に勝つので、ロールの土台のルートに 1 つ置けばほかのルートが足す Allow にも効く。

### 現物で確認した事実（2026-10-11 に PM が読んだ。`2e79558`）

- `/<prefix>/*` を `ssm:GetParameter` で読める Allow（Deny 無し）: `base/core/web.tf:53-57`（Web。自分のインラインポリシー）、`pipeline/stream/access.tf:9-21`（`parameters_read`。Runtime と Web）、`pipeline/graph/access.tf:23-28`（Runtime と Web。OSS 版 `IaC/terraform/oss/pipeline/graph/access.tf:19` も同じ）、`workflow/proposals.tf:18-42`（`reader_access`。Runtime と Web。`aws_iam_role_policy` の名前は `<prefix>-workflow-access`）、`workflow/gateway.tf:82-88`（tools の Lambda のロール `<prefix>-tools`。`data.aws_iam_policy_document.tools` の `Parameters`）。どれも actions は `ssm:GetParameter` 1 つで、`GetParametersByPath` は無い（親のパスを Deny しなくても、パスでまとめて読む経路は無い）。
- Runtime のロールは `base/core/runtime.tf:4-22`（ロールだけ。インラインポリシーは無い。コメント「ロールだけをここに置く。実行ポリシーは agent」）。Web のロールは `base/core/web.tf:6-`、インラインポリシーの Statement は 53-57 行が SSM。`IaC/terraform/oss/base/core/web.tf` と `runtime.tf`、`IaC/terraform/oss/workflow/gateway.tf` はマネージド版へのシンボリックリンク（1 か所の変更で両方に効く。`tests/test_oss.py:928-960` がリンクを検査する）。
- 4 つの名前は `ops/up-common.sh:407-413`（`ensure_secret "/$PREFIX/nautobot/secret-key"` / `admin-password` / `db-password` / `api-token`）と `pipeline/nautobot/locals.tf:108-112`（`secret_parameters`）。`url` は `pipeline/nautobot/nautobot.tf` が String で書く。
- 読む側のコード: Web の `app/dashboard/nautobot_api.py:16-17`（`toolkit.Param("NAUTOBOT_URL", "nautobot/url")` と `toolkit.Param("NAUTOBOT_API_TOKEN", "nautobot/api-token", decrypt=True)`）。Runtime（`app/agentcore/`）が読むのは `neptune-graph-id` / `neo4j-uri` / `neo4j-password` / `gateway-url` / `athena-*` / `history-namespace` / `proposal-events-table` / `decision-queue-url` / `opensearch-password`（`graph.py` / `mcp_client.py` / `proposals.py` / `evidence.py`）。tools の Lambda（`app/gateway/`）は `nautobot/` を読まない（`grep -rn 'nautobot' app/agentcore app/gateway --include='*.py'` で Param は `app/dashboard/nautobot_api.py` だけ）。
- 既存のテスト: `tests/test_stream.py:497-520` が `base/core/web.tf` のインラインポリシーの SSM の Allow を正規表現で見る。`tests/test_workflow.py:617-621` が temporal のタスクの `DenyNautobotParameters`（`effect = "Deny"`、`actions = ["ssm:GetParameter*"]`、`parameter${local.param_prefix}/nautobot/*`）を見る。いまの通過は test_stream 114、test_workflow 458、test_oss_ops 209。
- docs でロールの SSM の範囲を書いている行: `docs/architecture/resources/web-ec2.md:21`（「`/<prefix>/*` の `ssm:GetParameter`」）、`docs/architecture/resources/ssm-parameter-store.md:42`（4 つのシークレットの行。読む側は「Nautobot のタスク、RDS」）と `:55`（つながりの表「Web の EC2、Runtime、Lambda、worker、lab の EC2 … `/<prefix>/*` か、名前を絞った許可」）、`docs/architecture/resources/nautobot.md:19`、`docs/architecture/resources/temporal.md:63` と `:119`（「master のパスワードは init のタスクにしか渡らない」）、`docs/nautobot.md:59` と `:176`。

## 設計方針

1. **Deny は、ロールを作っているルートに置く。** Web と Runtime は `base/core`、tools の Lambda は `workflow`（`gateway.tf`）。`workflow/proposals.tf` の `reader_access` と `iam.tf` の temporal の `DenyNautobotParameters`（`nautobot/*` を丸ごと。こちらは URL も token も要らないので、より狭いままでよい）は変えない。`reader_access` には「Nautobot の内部のシークレットの Deny は base/core にある」のコメントを 1 行足す。
2. **名前で絞る。Deny するのは 3 つ**: `/<prefix>/nautobot/secret-key`、`/<prefix>/nautobot/admin-password`、`/<prefix>/nautobot/db-password`。`nautobot/url` と `nautobot/api-token` は Deny しない（Web が読む。Runtime と tools の Lambda はいま読まないが、Nautobot の API の入口なので同じ扱いにする。3 つのロールで差を付けない）。
3. **Deny の形は 042 の `DenyNautobotParameters` と同じ**: `Effect = "Deny"`、Action は `ssm:GetParameter*`（`GetParameter` / `GetParameters` / `GetParameterHistory` を一度に。Allow が `GetParameter` だけでも、ほかのルートがあとで広げたときに備える）、Resource は 3 つの ARN `arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/${local.name_prefix}/nautobot/<名前>`。Sid は `DenyNautobotSecrets`（temporal の `DenyNautobotParameters` と区別する。あちらは `nautobot/*`）。
   - base/core: 3 つの ARN を `base/core/locals.tf` の `locals` に `nautobot_secret_parameter_arns`（list）として 1 回だけ書き、Web のインラインポリシー（`web.tf` の Statement の末尾に Deny の Statement を 1 つ足す）と Runtime の新しい `aws_iam_role_policy`（`runtime.tf`。名前は `${local.name_prefix}-runtime-deny-nautobot-secrets`。Statement は Deny 1 つ）の両方で使う。Runtime のロールは Allow を持たないが、ほかのルート（stream / graph / workflow）が足す `/<prefix>/*` の Allow に勝たせるための Deny なので、ロールの土台に置く。`runtime.tf` の冒頭のコメント（「ロールだけをここに置く」）を「ロールと、Nautobot の内部のシークレットの Deny だけ」に直す。
   - workflow: `gateway.tf` の `data.aws_iam_policy_document.tools` の `Parameters` の直後に Deny の `statement` を足す。ARN は `local.param_prefix` から組む（nautobot の state の `db_password_parameter` は使わない。state が無くても Deny は要る）。3 つの名前は `workflow/locals.tf` に `nautobot_secret_parameter_arns` として書く（base/core と同じ 3 つ。テストで揃える）。
4. **コードは読む名前を変えない。** Web の `nautobot/url` / `nautobot/api-token`、Runtime と tools の Lambda の読む名前はそのまま。アプリ（`app/`）には触らない。
5. **docs は「読めるもの」の行を直す。** `web-ec2.md:21` のロールの行に「`/<prefix>/nautobot/{secret-key,admin-password,db-password}` は Deny」、`ssm-parameter-store.md:42` の読む側に「Web / Runtime / tools の Lambda / temporal のタスクのロールは Deny（`api-token` は Web が読む）」、`:55` の「名前を絞った許可」の列に Deny の一言、`nautobot.md`（resources）の `:19` のシークレットの行に「3 つは Nautobot のタスクと Temporal の init だけが読める」、`temporal.md:63` と `:119` の「init のタスクにしか渡らない」に「Web / Runtime / tools の Lambda のロールも Deny（cycle 044）」、`docs/nautobot.md:59` のシークレットの行に同じ一言。FAQ・troubleshooting・全体設計 HTML は触らない（構成要素・流れ・配置は変わらない）。
6. **AWS の実機は QUEUE 147 でまとめて確かめる**（PM が QUEUE 147 の文面に足す。エンジニアは QUEUE を書かない）。手元では `terraform validate`（`init -backend=false`。AWS の認証は要らない）とテストで確かめる。

## 変更対象ファイル

| ファイル | 変更 |
| :--- | :--- |
| `IaC/terraform/aws-managed/base/core/locals.tf` | `nautobot_secret_parameter_arns`（3 つの ARN の list）。コメントに「読んでよいのは Nautobot のタスクの実行ロールと Temporal の init の実行ロールだけ」 |
| `IaC/terraform/aws-managed/base/core/web.tf` | インラインポリシーの Statement の末尾に `DenyNautobotSecrets`（Deny、`ssm:GetParameter*`、`local.nautobot_secret_parameter_arns`） |
| `IaC/terraform/aws-managed/base/core/runtime.tf` | `aws_iam_role_policy.runtime_deny_nautobot_secrets`（同じ Deny 1 つ）。冒頭のコメントを直す |
| `IaC/terraform/aws-managed/workflow/locals.tf` | `nautobot_secret_parameter_arns`（`local.param_prefix` から組む 3 つ） |
| `IaC/terraform/aws-managed/workflow/gateway.tf` | `data.aws_iam_policy_document.tools` に `DenyNautobotSecrets` の statement |
| `IaC/terraform/aws-managed/workflow/proposals.tf` | `reader_access` にコメント 1 行（Deny は base/core） |
| `tests/test_stream.py` | `web.tf` / `runtime.tf` / `base/core/locals.tf` の check（検証 1） |
| `tests/test_workflow.py` | `gateway.tf` / `workflow/locals.tf` の check と、base/core と workflow と `ops/up-common.sh` と `pipeline/nautobot/locals.tf` の名前が揃う check（検証 2） |
| `docs/architecture/resources/web-ec2.md`、`ssm-parameter-store.md`、`nautobot.md`、`temporal.md`、`docs/nautobot.md` | 方針 5 の行 |
| `docs/cycles/044-deny-nautobot-secrets/build.md` | エンジニアが書く |

OSS 版（`IaC/terraform/oss/base/core/{locals,web,runtime}.tf`、`IaC/terraform/oss/workflow/{locals,gateway,proposals}.tf`）はシンボリックリンクなので、ファイルは足さない・変えない。

## 再利用するもの

- `workflow/iam.tf:148-154` の `DenyNautobotParameters`（Deny の書き方。`effect = "Deny"`、`actions = ["ssm:GetParameter*"]`）
- `tests/test_workflow.py:617-621` の check の形（policy document を `split` で切り出して文字列を見る）、`tests/test_stream.py:497-520`（`web.tf` の Statement を正規表現で見る）
- `ops/up-common.sh:407-413` の `ensure_nautobot_secrets`（名前の正）、`pipeline/nautobot/locals.tf:108-112` の `secret_parameters`

## 実装ステップ

1. `base/core/locals.tf` に `nautobot_secret_parameter_arns` を足し、`web.tf` の Statement の末尾に Deny を、`runtime.tf` に `aws_iam_role_policy.runtime_deny_nautobot_secrets` を足す。`runtime.tf` の冒頭のコメントを直す。
2. `workflow/locals.tf` に `nautobot_secret_parameter_arns` を足し、`gateway.tf` の `tools` に Deny の statement を足す。`proposals.tf` の `reader_access` にコメント 1 行。
3. `terraform fmt -recursive IaC/terraform/aws-managed/base/core IaC/terraform/aws-managed/workflow`。`terraform -chdir=IaC/terraform/aws-managed/base/core init -backend=false` → `validate`、workflow も同じ（検証 3）。`init` が作る `.terraform/` は消す（`.terraform.lock.hcl` は変えない。変わっていたら `git checkout` で戻す）。
4. テストを足す（検証 1 / 2）。足す前に 1 つ check を外して落ちることを確かめる（変異: `web.tf` の Deny の Resource から `db-password` を抜く → 検証 1 の check が落ちる）。
5. docs（方針 5）。
6. `/cycle-build` 手順6 のセルフレビュー。`build.md` に実装モデル・テストの件数・`validate` の出力・逸脱を書く。

## 検証方法

1. `uv run --group dev --group web python tests/test_stream.py` の末尾が `通過 118 / 失敗 0` 以上（いま 114。足す check は 4 つ以上）。足す check:
   - `web.tf` のインラインポリシーに `Sid = "DenyNautobotSecrets"`、`Effect = "Deny"`、`Action = "ssm:GetParameter*"`、`Resource = local.nautobot_secret_parameter_arns` があり、既存の `/<prefix>/*` の Allow（`test_stream.py:519` の正規表現）はそのまま
   - `runtime.tf` に `resource "aws_iam_role_policy" "runtime_deny_nautobot_secrets"`（`role = aws_iam_role.runtime.id` か `.name`）があり、Statement は Deny 1 つで Allow が無い
   - `base/core/locals.tf` の `nautobot_secret_parameter_arns` が `parameter/${local.name_prefix}/nautobot/secret-key` / `admin-password` / `db-password` の 3 つで、`api-token` と `url` を含まない
   - OSS 版の `IaC/terraform/oss/base/core/{locals,web,runtime}.tf` がマネージド版へのリンクのまま（`os.path.islink`）
2. `uv run --group dev --group web python tests/test_workflow.py` の末尾が `通過 462 / 失敗 0` 以上（いま 458。足す check は 4 つ以上）。足す check:
   - `gateway.tf` の `data.aws_iam_policy_document.tools` に `sid = "DenyNautobotSecrets"`、`effect = "Deny"`、`actions = ["ssm:GetParameter*"]`、`resources = local.nautobot_secret_parameter_arns` があり、`Parameters` の Allow はそのまま
   - `workflow/locals.tf` の `nautobot_secret_parameter_arns` が `parameter${local.param_prefix}/nautobot/` + 3 つの名前
   - 3 つの名前が、`ops/up-common.sh` の `ensure_nautobot_secrets` の `ensure_secret` の名前から `api-token` を除いたものと一致し、`pipeline/nautobot/locals.tf` の `secret_parameters` のキーから `api-token` を除いたものとも一致する（base/core と workflow の両方で）
   - `proposals.tf` の `reader_access` と `iam.tf` の `DenyNautobotParameters` は変わっていない（`DenyNautobotParameters` の既存 check がそのまま通る）
3. `terraform -chdir=IaC/terraform/aws-managed/base/core validate` と `terraform -chdir=IaC/terraform/aws-managed/workflow validate` が `Success! The configuration is valid.`（`init -backend=false` のあと。OSS 版 `IaC/terraform/oss/base/core` と `IaC/terraform/oss/workflow` も同じ 2 行）。`terraform fmt -check -recursive IaC/terraform/aws-managed/base/core IaC/terraform/aws-managed/workflow` の rc が 0。
4. `git status --short IaC/terraform/oss` が空（OSS 版にファイルを足していない）。`git diff --stat -- app/` が空。
5. `uv run --group dev --group web python tests/test_oss_ops.py` と `tests/test_oss.py` が失敗 0（リンクの検査が通る）。
6. AWS の実機（QUEUE 147 で PM が打つ。このサイクルでは打たない）: `aws iam simulate-principal-policy` を `<prefix>-web` / `<prefix>-runtime` / `<prefix>-tools` の 3 つのロールで `ssm:GetParameter` × `…parameter/<prefix>/nautobot/db-password` → `EvalDecision` が `explicitDeny`、`<prefix>-web` × `…/nautobot/api-token` → `allowed`。Web の EC2 のシェルから `aws ssm get-parameter --name /<prefix>/nautobot/db-password --with-decryption --query Parameter.Name` → `AccessDeniedException`、`--name /<prefix>/nautobot/api-token` → 名前が出る（値は出さない）。

## 未確定事項とリスク

1. **`web.tf` のインラインポリシーに Deny を足すと、Web の EC2 のロールは in-place で変わる**（`aws_iam_role_policy` の更新。EC2 の作り直しは無い）。043 の `check_sg_descriptions` には掛からない（SG の description は変えない）。
2. **Runtime のロールに Deny のポリシーが 1 つ増える。** Runtime は IAM のポリシーを起動時に読むだけなので、Runtime の作り直しは無い。AWS で確かめるのは 147。
3. **Allow が `ssm:GetParameter` 1 つなのに Deny を `ssm:GetParameter*` にしている**のは 042 と揃えるため。`GetParametersByPath` で親のパス `/<prefix>/nautobot` を読む Allow はどのルートにも無い（事実の 1 つ目）ので、パスの Deny は足さない。将来 `GetParametersByPath` の Allow を足すときは、このサイクルの Deny では守れない（`ssm-parameter-store.md` の知見に 1 行書く）。
4. **tools の Lambda が OSS 版で `neo4j-password` / `opensearch-password` を読む経路**（`gateway.tf:82-83` のコメント）は `/<prefix>/` の直下で、`nautobot/` の下ではないので Deny に掛からない。
5. **Deny の場所を QUEUE の文面（workflow）から base/core に変えた**（背景の 3 つ目）。147 の実機で `WORKFLOW=1` の環境しか見ないので、`WORKFLOW=0` で Deny が効くことは `simulate-principal-policy`（検証 6）で見る（ロールのポリシーは workflow が無くても同じ）。
