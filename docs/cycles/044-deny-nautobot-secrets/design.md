# Nautobot の内部のシークレットを Web と Runtime と tools の Lambda のロールで読めなくする（044）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-11。ベースは main の `2e79558`。Round 2（2026-10-11。Round 1 のセルフレビューの Should 1〜3 を取り込んだ。Round 1 の実装 `d6dd0ab` の上に差分で直す）。

## 背景

- QUEUE 148（042 の cold review Round 1 の Nit 2）。042 で master のパスワード `/<prefix>/nautobot/db-password` を temporal のタスクのロールだけ Deny した（`IaC/terraform/aws-managed/workflow/iam.tf:148-154` の `DenyNautobotParameters`。`/<prefix>/nautobot/*` を丸ごと）。同じ値を読める Allow が、ほかの 3 つのロールに残っている。
- 守るのは `db-password` だけではない。`ops/up-common.sh:407-413` の `ensure_nautobot_secrets` が作る 4 つの SecureString のうち、`secret-key`（Django の SECRET_KEY）、`admin-password`（画面の管理者）、`db-password`（RDS の master）の 3 つは Nautobot の中だけのもので、使うのは Nautobot のタスクの実行ロール（`pipeline/nautobot/access.tf` の `exec_secrets`。`ssm:GetParameters` を名前で）と、Temporal の init のタスクの実行ロール（`workflow/iam.tf` の `execution_db_passwords`。`db-password` だけ）だけ。残る `api-token` と String の `url` は Nautobot の API の入口で、Web の EC2 がいま読んでいる。
- QUEUE の文面は「workflow のロール」だが、Deny を置く場所は **ロールを作っているルート**にする（下の設計方針 1）。Web と Runtime のロールは `base/core` のもので、`/<prefix>/*` の Allow は base/core（Web 自身）・stream・graph・workflow の 4 か所から付く。workflow に Deny を置くと `WORKFLOW=0` の環境（`PIPELINE=1` だけ。Nautobot と 4 つのシークレットはある）では効かない。IAM は同じプリンシパルに付くどのポリシーの Deny も全部の Allow に勝つので、ロールの土台のルートに 1 つ置けばほかのルートが足す Allow にも効く。
- **Round 1 のセルフレビュー（`build.md` の Round 1）で分かったこと（Round 2 で取り込む）**:
  - Web の EC2 のロール（`base/core/web.tf:22-25`）と lab の EC2 のロール（`pipeline/lab/iam.tf:17-20`）と lab-debug のロール（`IaC/cloudformation/lab-debug.yaml` の `Role`）は `AmazonSSMManagedInstanceCore` を持ち、このマネージドポリシーは `ssm:GetParameter` と `ssm:GetParameters` を Resource `*` で許す。自分の prefix の 3 つだけ Deny しても、**同じアカウントのほかの環境（別の OWNER、OSS 版を並べたとき）の Nautobot のシークレットは読める**し、**lab の EC2 のロールは自分の環境の 3 つも読める**。temporal のタスクのロールは lab へ `ssm:SendCommand`（`workflow/iam.tf:157-173`）できるので、042 の「temporal のタスクから master のパスワードを引けない」は lab 経由の穴が残っていた。
  - 版やラベルの指定（`GetParameter --name …/db-password:1`）を IAM がどの ARN で判定するかは、AWS の文書「How Parameter Store authorizes parameter retrieval」（`docs.aws.amazon.com/systems-manager/latest/userguide/ps-retrieval-authorization.html`。2026-10-11 に PM が読んだ）が「`GetParameter` は **the requested parameter** に対して判定する」とだけ書き、版やラベルで ARN が変わるとは書いていない。IAM の `parameter` のリソース型の ARN（`parameter/${ParameterNameWithoutLeadingSlash}`）にも版の部分は無い。設計は名前の完全一致のまま、AWS の実機（QUEUE 147）で `:1` とラベルを打って確かめる（未確定事項 6）。

### 現物で確認した事実（2026-10-11 に PM が読んだ。`2e79558` と `d6dd0ab`）

- `/<prefix>/*` を `ssm:GetParameter` で読める Allow（Deny 無し）: `base/core/web.tf:53-57`（Web。自分のインラインポリシー）、`pipeline/stream/access.tf:9-21`（`parameters_read`。Runtime と Web）、`pipeline/graph/access.tf:23-28`（Runtime と Web。OSS 版 `IaC/terraform/oss/pipeline/graph/access.tf:19` も同じ）、`workflow/proposals.tf:18-42`（`reader_access`。Runtime と Web。`aws_iam_role_policy` の名前は `<prefix>-workflow-access`）、`workflow/gateway.tf:82-88`（tools の Lambda のロール `<prefix>-tools`。`data.aws_iam_policy_document.tools` の `Parameters`）。どれも actions は `ssm:GetParameter` 1 つで、`GetParametersByPath` は無い（親のパスを Deny しなくても、パスでまとめて読む経路は無い）。
- `*` を読める Allow: `AmazonSSMManagedInstanceCore`（Web の `web.tf:22-25`、lab の `pipeline/lab/iam.tf:17-20`、lab-debug の `lab-debug.yaml` の `ManagedPolicyArns`）。lab のインラインポリシー `lab_assets`（`pipeline/lab/iam.tf:22-68`）の SSM は `TelegrafAddress`（`telegraf-address` / `telegraf-source-cidr` の 2 つ）だけ。lab-debug の `lab-assets` は `tests/test_lab_debug.py:118-125` が「Sid は iam.tf と同じ（`TelegrafAddress` を除く）で、`ssm:GetParameter` を含まない」と見ている。`IaC/terraform/oss/pipeline/lab/` の `iam.tf` / `locals.tf` はマネージド版へのシンボリックリンク。
- Runtime のロールは `base/core/runtime.tf:4-22`（ロールだけ。Round 1 で Deny の `aws_iam_role_policy` を足した）。Web のロールは `base/core/web.tf:6-`、インラインポリシーの Statement は 53-57 行が SSM、末尾に Round 1 の Deny。`IaC/terraform/oss/base/core/web.tf` と `runtime.tf`、`IaC/terraform/oss/workflow/gateway.tf` はマネージド版へのシンボリックリンク（1 か所の変更で両方に効く。`tests/test_oss.py:928-960` がリンクを検査する）。
- 4 つの名前は `ops/up-common.sh:407-413`（`ensure_secret "/$PREFIX/nautobot/secret-key"` / `admin-password` / `db-password` / `api-token`）と `pipeline/nautobot/locals.tf:108-112`（`secret_parameters`）。`url` は `pipeline/nautobot/nautobot.tf` が String で書く。
- 読む側のコード: Web の `app/dashboard/nautobot_api.py:16-17`（`toolkit.Param("NAUTOBOT_URL", "nautobot/url")` と `toolkit.Param("NAUTOBOT_API_TOKEN", "nautobot/api-token", decrypt=True)`）。Runtime（`app/agentcore/`）が読むのは `neptune-graph-id` / `neo4j-uri` / `neo4j-password` / `gateway-url` / `athena-*` / `history-namespace` / `proposal-events-table` / `decision-queue-url` / `opensearch-password`。tools の Lambda（`app/gateway/`）と lab（`app/containerlab/`、`ops/lab.sh`）は `nautobot/` を読まない（Round 1 の `grep -rn "nautobot/\(admin-password\|secret-key\|db-password\)"` で読むのは pipeline/nautobot の locals と up-common.sh の作成だけ）。
- 既存のテスト: `tests/test_stream.py` が `base/core/web.tf` のインラインポリシーの SSM の Allow を正規表現で見る（Round 1 で Deny の check 4 つを足した。118）。`tests/test_workflow.py` が temporal のタスクの `DenyNautobotParameters` を見る（Round 1 で 4 つ足した。462）。`tests/test_lab_debug.py` 110（`:118-125` がロールの Sid と Action を iam.tf と突き合わせ、`tf_actions` の正規表現は `(?:ecr|s3|ssm):\w+` で `*` を含む Action に合わない）。test_oss_ops 209。
- docs でロールの SSM の範囲を書いている行: `docs/architecture/resources/web-ec2.md:21`、`ssm-parameter-store.md:42` / `:55` / 知見（Round 1 で「lab の EC2 のロールはまだ読める」と書いた行）、`nautobot.md:19`（同じく「lab はまだ読める」）、`temporal.md:63` と `:119`、`docs/nautobot.md:59`、`lab-ec2.md:39`（lab のロールの SSM の行。`telegraf-address` / `telegraf-source-cidr`）。

## 設計方針

1. **Deny は、ロールを作っているルートに置く。** Web と Runtime は `base/core`、tools の Lambda は `workflow`（`gateway.tf`）、**lab の EC2 は `pipeline/lab`（`iam.tf` の `lab_assets`）、lab-debug の EC2 は `IaC/cloudformation/lab-debug.yaml`（`Role` の `lab-assets`）**。`workflow/proposals.tf` の `reader_access` と `iam.tf` の temporal の `DenyNautobotParameters`（`nautobot/*` を丸ごと。こちらは URL も token も要らないので、より狭いままでよい）は変えない。`reader_access` のコメント 1 行（Round 1）はそのまま。
2. **名前で絞り、prefix は問わない。Deny するのは 3 つの名前**: `nautobot/secret-key`、`nautobot/admin-password`、`nautobot/db-password`。Resource は `arn:${partition}:ssm:${region}:${account_id}:parameter/*/nautobot/<名前>`（`/<prefix>/` ではなく `/*/`）。理由は背景の 4 つ目: `AmazonSSMManagedInstanceCore` を持つロール（Web・lab・lab-debug）は Resource `*` の Allow で同じアカウントのほかの環境の 3 つも読めるので、自分の prefix だけ Deny しても塞がらない。Runtime と tools の Lambda の Allow は `/<prefix>/*` だけなので `*` にしても意味は変わらないが、4 か所で同じ ARN にする。`nautobot/url` と `nautobot/api-token` は Deny しない（Web が読む。Runtime と tools の Lambda と lab はいま読まないが、Nautobot の API の入口なので同じ扱いにする）。
3. **Deny の形は 042 の `DenyNautobotParameters` と同じ**: `Effect = "Deny"`、Action は `ssm:GetParameter*`（`GetParameter` / `GetParameters` / `GetParameterHistory` / `GetParametersByPath` を一度に）、Resource は方針 2 の 3 つの ARN、Sid は `DenyNautobotSecrets`。
   - base/core（Round 1 のまま、ARN だけ `/*/` に）: `base/core/locals.tf` の `nautobot_secret_parameter_arns`（3 行のリテラル）を Web のインラインポリシー（`web.tf` の末尾の Deny）と Runtime の `aws_iam_role_policy.runtime_deny_nautobot_secrets`（`runtime.tf`）で使う。コメントの「読んでよいのは…」は「使うのは Nautobot のタスクの実行ロールと Temporal の init の実行ロール。prefix を問わないのは AmazonSSMManagedInstanceCore（Resource `*`）に勝たせるため」に直す。
   - workflow（Round 1 のまま、ARN だけ `/*/` に）: `workflow/locals.tf` の `nautobot_secret_parameter_arns`（`local.param_prefix` を使わない 3 行）、`gateway.tf` の `tools` の Deny の statement。
   - lab（新規）: `pipeline/lab/locals.tf` に `nautobot_secret_parameter_arns`（同じ 3 行。`local.partition` / `var.region` / `local.account_id`）、`pipeline/lab/iam.tf` の `lab_assets` の Statement の末尾に `DenyNautobotSecrets`（Sid の書き方は同じファイルの既存の `Sid      = "…"` に揃える。`TelegrafAddress` の Allow はそのまま）。コメントに「temporal のタスクが Run Command で lab のシェルを使えるので、lab のロールでも Deny しないと 042 の Deny が lab 経由で抜けられる」を書く。
   - lab-debug（新規）: `lab-debug.yaml` の `Role` の `lab-assets` の Statement の末尾に `Sid: DenyNautobotSecrets`、`Effect: Deny`、`Action: ssm:GetParameter*`、`Resource` は `!Sub "arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter/*/nautobot/secret-key"` ほか 3 つのリスト。コメントは iam.tf と同じ趣旨を 1 行。
4. **コードは読む名前を変えない。** Web の `nautobot/url` / `nautobot/api-token`、Runtime と tools の Lambda の読む名前はそのまま。アプリ（`app/`）には触らない。
5. **docs は「読めるもの」の行を直す。** Round 1 の行（`web-ec2.md:21`、`ssm-parameter-store.md:42` / `:55` / 知見、`nautobot.md:19`、`temporal.md:63` / `:119`、`docs/nautobot.md:59`）の「lab の EC2 のロールはまだ読める」を消して「lab の EC2 と lab-debug のロールも Deny」にし、「prefix を問わない（ほかの環境の Nautobot のシークレットも読めない。`AmazonSSMManagedInstanceCore` の Resource `*` に勝たせるため）」を知見の行に足す。`temporal.md:63` の「ECS Exec のシェルから引くこともできない」に「lab へ Run Command で打っても lab のロールが Deny（cycle 044）」を足す。`lab-ec2.md:39` の SSM の行に「`nautobot/{secret-key,admin-password,db-password}` は Deny（cycle 044）」。FAQ・troubleshooting・全体設計 HTML は触らない（構成要素・流れ・配置は変わらない）。
6. **AWS の実機は QUEUE 147 でまとめて確かめる**（PM が QUEUE 147 の文面に足す。エンジニアは QUEUE を書かない）。手元では `terraform validate`（`init -backend=false`。AWS の認証は要らない）とテストで確かめる。

## 変更対象ファイル

| ファイル | 変更 |
| :--- | :--- |
| `IaC/terraform/aws-managed/base/core/locals.tf` | `nautobot_secret_parameter_arns` の ARN を `parameter/*/nautobot/<名前>` に。コメントを方針 3 に |
| `IaC/terraform/aws-managed/base/core/web.tf`、`runtime.tf` | Round 1 のまま（Deny は locals を参照。コメントに「prefix を問わない」の一言があってもよい） |
| `IaC/terraform/aws-managed/workflow/locals.tf` | `nautobot_secret_parameter_arns` の ARN を `parameter/*/nautobot/<名前>` に（`param_prefix` を使わない） |
| `IaC/terraform/aws-managed/workflow/gateway.tf`、`proposals.tf` | Round 1 のまま |
| `IaC/terraform/aws-managed/pipeline/lab/locals.tf` | `nautobot_secret_parameter_arns`（同じ 3 行） |
| `IaC/terraform/aws-managed/pipeline/lab/iam.tf` | `lab_assets` の Statement の末尾に `DenyNautobotSecrets` |
| `IaC/cloudformation/lab-debug.yaml` | `Role` の `lab-assets` の Statement の末尾に `DenyNautobotSecrets`（`!Sub` の 3 つ） |
| `tests/test_stream.py` | Round 1 の locals の check を `parameter/*/nautobot/` に（`${local.name_prefix}` が無いことも見る） |
| `tests/test_workflow.py` | Round 1 の locals の check を `parameter/*/nautobot/` に（`param_prefix` が無いことも見る）。名前の一致の check はそのまま |
| `tests/test_lab_debug.py` | `:118-125` の Sid / Action の突き合わせを Deny 込みに直し（`tf_actions` の正規表現を `[\w*]+` に、「`ssm:GetParameter` を含まない」は「Allow の Statement に `ssm:GetParameter` が無い」に）、lab の Deny の check を足す（検証 3） |
| `docs/architecture/resources/web-ec2.md`、`ssm-parameter-store.md`、`nautobot.md`、`temporal.md`、`lab-ec2.md`、`docs/nautobot.md` | 方針 5 の行 |
| `docs/cycles/044-deny-nautobot-secrets/build.md` | エンジニアが `## Round 2` を追記 |

OSS 版（`IaC/terraform/oss/base/core/{locals,web,runtime}.tf`、`IaC/terraform/oss/workflow/{locals,gateway,proposals}.tf`、`IaC/terraform/oss/pipeline/lab/{locals,iam}.tf`）はシンボリックリンクなので、ファイルは足さない・変えない。

## 再利用するもの

- Round 1 の `d6dd0ab`（locals・Deny の statement・テスト・docs の行。差分で直す）
- `workflow/iam.tf:148-154` の `DenyNautobotParameters`（Deny の書き方）
- `tests/test_lab_debug.py:118-125`（iam.tf と CFn の Sid / Action の突き合わせ。ここに Deny を乗せる）
- `ops/up-common.sh:407-413` の `ensure_nautobot_secrets`（名前の正）、`pipeline/nautobot/locals.tf:108-112` の `secret_parameters`

## 実装ステップ

1. `base/core/locals.tf` と `workflow/locals.tf` の 3 つの ARN を `parameter/*/nautobot/<名前>` に直し、コメントを方針 3 に。
2. `pipeline/lab/locals.tf` に `nautobot_secret_parameter_arns`、`pipeline/lab/iam.tf` の `lab_assets` に Deny。`lab-debug.yaml` の `lab-assets` に Deny。
3. `terraform fmt -recursive`（base/core、workflow、pipeline/lab）。`terraform -chdir=<ルート> init -backend=false` → `validate` を base/core・workflow・pipeline/lab（OSS 版の 3 つも）で（検証 4）。`init` が作る `.terraform/` は消す（`.terraform.lock.hcl` は変えない）。
4. テストを直す・足す（検証 1〜3）。変異: `base/core/locals.tf` の ARN を `${local.name_prefix}` に戻す → test_stream が落ちる。`pipeline/lab/iam.tf` の Deny を消す → test_lab_debug が落ちる。`lab-debug.yaml` の Deny の Resource から `db-password` を抜く → test_lab_debug が落ちる。
5. docs（方針 5）。
6. `/cycle-build` 手順6 のセルフレビュー。`build.md` に `## Round 2` を追記（実装モデル・テストの件数・`validate` の出力・逸脱・変異）。

## 検証方法

1. `uv run --group dev --group web python tests/test_stream.py` の末尾が `通過 118 / 失敗 0` 以上。`base/core/locals.tf` の `nautobot_secret_parameter_arns` が `parameter/*/nautobot/secret-key` / `admin-password` / `db-password` の 3 つで、`${local.name_prefix}` と `api-token` と `url` を含まない。Round 1 の Deny の check（`web.tf` / `runtime.tf` / OSS のリンク）はそのまま通る。
2. `uv run --group dev --group web python tests/test_workflow.py` の末尾が `通過 462 / 失敗 0` 以上。`workflow/locals.tf` の 3 つが `parameter/*/nautobot/` + 名前で `param_prefix` を含まない。名前の一致の check（`ensure_nautobot_secrets` / `secret_parameters` / base/core / workflow）に `pipeline/lab/locals.tf` も足す。
3. `uv run --group dev --group web python tests/test_lab_debug.py` の末尾が `通過 112 / 失敗 0` 以上（いま 110。足す check は 2 つ以上）: `pipeline/lab/iam.tf` の `lab_assets` に `DenyNautobotSecrets`（Deny、`ssm:GetParameter*`、`local.nautobot_secret_parameter_arns`）があり `TelegrafAddress` の Allow はそのまま。`lab-debug.yaml` の `lab-assets` の `DenyNautobotSecrets` が `Effect: Deny`、`Action: ssm:GetParameter*`、Resource が `!Sub` の 3 つで名前が iam.tf の locals と同じ。既存の「Sid は iam.tf と同じ」「Sid ごとの Action は同じ」が Deny 込みで通る。
4. `terraform -chdir=<ルート> validate` が `Success! The configuration is valid.`: `IaC/terraform/aws-managed/{base/core,workflow,pipeline/lab}` と `IaC/terraform/oss/{base/core,workflow,pipeline/lab}` の 6 つ。`terraform fmt -check -recursive` の rc が 0。
5. `git status --short IaC/terraform/oss` が空。`git diff --stat -- app/` が空。`uv run --group dev --group web python tests/test_oss_ops.py` と `tests/test_oss.py` と `tests/test_nautobot.py` が失敗 0。
6. AWS の実機（QUEUE 147 で PM が打つ。このサイクルでは打たない）: `aws iam simulate-principal-policy` を `<prefix>-web` / `<prefix>-runtime` / `<prefix>-tools` / `<prefix>-lab` の 4 つのロールで `ssm:GetParameter` × `…parameter/<prefix>/nautobot/db-password` → `explicitDeny`、`<prefix>-web` と `<prefix>-lab` × `…parameter/other-nwc/nautobot/db-password`（別 prefix）→ `explicitDeny`、`<prefix>-web` × `…/nautobot/api-token` → `allowed`。Web の EC2 のシェルから `aws ssm get-parameter --name /<prefix>/nautobot/db-password --with-decryption --query Parameter.Name` → `AccessDeniedException`、`--name /<prefix>/nautobot/db-password:1` → `AccessDeniedException`（版の指定でも Deny）、`--name /<prefix>/nautobot/api-token` → 名前が出る（値は出さない）。

## 未確定事項とリスク

1. **`web.tf` のインラインポリシーに Deny を足すと、Web の EC2 のロールは in-place で変わる**（`aws_iam_role_policy` の更新。EC2 の作り直しは無い）。lab も同じ（`lab_assets` の更新。インスタンスの作り直しは無い）。043 の `check_sg_descriptions` には掛からない。
2. **Runtime のロールに Deny のポリシーが 1 つ増える。** Runtime は IAM のポリシーを起動時に読むだけなので、Runtime の作り直しは無い。AWS で確かめるのは 147。
3. **Allow が `ssm:GetParameter` 1 つなのに Deny を `ssm:GetParameter*` にしている**のは 042 と揃えるため。`GetParametersByPath` で親のパス `/<prefix>/nautobot` を読む Allow はどのルートにも無く、`AmazonSSMManagedInstanceCore` にも無い（`GetParameter` と `GetParameters` だけ）ので、パスの Deny は足さない。将来 `GetParametersByPath` の Allow を足すときは、このサイクルの Deny では守れない（`ssm-parameter-store.md` の知見の行はそのまま）。
4. **tools の Lambda が OSS 版で `neo4j-password` / `opensearch-password` を読む経路**は `/<prefix>/` の直下で、`nautobot/` の下ではないので Deny に掛からない。
5. **Deny の場所を QUEUE の文面（workflow）から base/core に変えた。** 147 の実機で `WORKFLOW=1` の環境しか見ないので、`WORKFLOW=0` で Deny が効くことは `simulate-principal-policy`（検証 6）で見る。
6. **版やラベルの指定が Deny をすり抜けないことは、文書では「the requested parameter に対して判定する」までしか確かめていない**（背景の 5 つ目）。147 の `:1` で実測する。すり抜けたら Resource の末尾を `*`（`parameter/*/nautobot/db-password*`）にする再設計。
7. **`AmazonSSMManagedInstanceCore` そのものは残る**（Web・lab・lab-debug が SSM の管理ノードであるために要る）。このマネージドポリシーで同じアカウントの Nautobot 以外のパラメータ（ほかの環境の `neo4j-password` など）を読めるのは、このサイクルの範囲外（PM が QUEUE に候補を足す）。
