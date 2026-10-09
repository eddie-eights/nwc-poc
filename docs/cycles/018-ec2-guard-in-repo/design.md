# EC2 のインスタンスタイプのガードを nwc-poc で持つ（018）

設計: PM(fable-5.1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

AWS のアカウントには、IAM ユーザー `eito-private-operator` が属するグループ `netops-always-on-operators` に付いた IAM ポリシー `netops-always-on-guard` がある。Sid `DenyLargeInstanceTypes` が `ec2:RunInstances` を、インスタンスタイプが許可リストに無ければ拒否する。2026-10-09 の AWS の動作確認は、このガードが Web の `t4g.medium`（`IaC/terraform/aws-managed/base/core/variables.tf:80`）を拒否して止まった。lab の `m6i.xlarge`（`pipeline/lab/variables.tf:35`）も許可リストに無い。

このポリシーは別リポジトリ `eddie-eights/aws_sandbox` の Terraform（`infra/stacks/always-on/iam-guard.tf`、`var.allowed_instance_types`）が作ったもので、ユーザーは aws_sandbox をもう使わない（2026-10-02）。この PC に aws_sandbox のチェックアウトは無い。

ユーザーの決定（2026-10-09）:

- **ガードは nwc-poc で定義し直す。** aws_sandbox の always-on のうち、nwc-poc に関係があるのは EC2 のインスタンスタイプのガードだけ。それ以外（operators グループと MFA のインラインポリシー、admin ロール、GitHub OIDC の plan ロール、Budgets、Cost Anomaly、SNS）は nwc-poc に混ぜず、aws_sandbox が作ったままにする。
- **名前は `nwc` に揃える**（`netops` を使わない）。
- **いまの `netops-always-on-guard` は、先に aws_sandbox 側で消す**（ユーザーが行う）。消してから nwc-poc で新しいガードを apply する。そのあいだはガードが無い。

### 調査で分かった事実（2026-10-09、`docs/cycle-006-design` 06a1e97）

- aws_sandbox の `iam-guard.tf`（GitHub の `main`）: `data.aws_iam_policy_document` に Sid `DenyLargeInstanceTypes`（`effect = "Deny"`、`actions = ["ec2:RunInstances"]`、`resources = ["arn:${partition}:ec2:*:*:instance/*"]`、`condition { test = "StringNotEquals"; variable = "ec2:InstanceType"; values = var.allowed_instance_types }`）。ほかに Neptune の作成の拒否（`var.allow_neptune` が false のとき）と `sagemaker:CreateNotebookInstance` の拒否がある。`aws_iam_policy` と `aws_iam_group_policy_attachment` でグループに付ける。
- `var.allowed_instance_types` の git 上の既定値は `["t3.micro", "t3.small", "t4g.micro", "t4g.small"]`。AWS にある v3（2026-09-26）は `t4g.large` と `t4g.xlarge` も許す（gitignore の `terraform.tfvars` で足したもの）。
- nwc-poc が EC2 に使うタイプは 2 つの変数の `validation` で決まっている。Web: `t4g.micro` / `t4g.small` / `t4g.medium`（`base/core/variables.tf:76-84`）。lab: `m6i.xlarge` / `m6i.2xlarge` / `c6i.2xlarge` / `t3.xlarge` / `t3.2xlarge`（`pipeline/lab/variables.tf:29-39`）。デバッグ用の EC2（`IaC/cloudformation/lab-debug.yaml`）の `InstanceType` の既定値は lab と同じ（`tests/test_lab_debug.py:90` が検査）。
- マネージドの 9 ルートは全部ローカルの state（`.gitignore:22-25` が `.terraform/` と `*.tfstate*` を除く）。ルートの一覧は `ops/check.sh:14` の `ROOTS`、`tests/test_oss_ops.py` の `ROOTS`、`ops/up.sh` / `ops/down.sh` が持つ。`ops/down.sh` はこの一覧のルートを destroy する。
- provider は `hashicorp/aws ~> 6.0`、`required_version >= 1.11.0, < 2.0.0`（`base/core/versions.tf`）。`default_tags` は `Project` と `owner`（`base/core/providers.tf`）。
- AWS MCP は使えるが、AWS CLI のセッションは切れている（`aws login` が要る）。**IAM の変更の apply は Claude がしない（ユーザーが行う）。**

## 設計方針

1. **新しい Terraform のルート `IaC/terraform/aws-managed/guard/` を作る。** `ops/up.sh` / `ops/down.sh` / `ops/check.sh` の `ROOTS` には**入れない**（環境を立てるたびに作り直すものではなく、アカウントに置きっぱなしにするガード）。state はほかのルートと同じローカル。
2. **中身は EC2 のインスタンスタイプの拒否だけ。** aws_sandbox にある Neptune と SageMaker の拒否は持ち込まない（nwc-poc に関係ない）。Sid は `DenyLargeInstanceTypes` のまま。
   - `aws_iam_policy "guard"`: name `nwc-poc-ec2-guard`。description は英語で「Deny ec2:RunInstances unless the instance type is one nwc-poc uses」の趣旨。
   - `aws_iam_group_policy_attachment "guard"`: `group = var.operators_group`（既定 `netops-always-on-operators`。グループは aws_sandbox のもので、名前は変えない。**グループの資源は nwc-poc では作らない**。`data "aws_iam_group"` で存在を確かめてから付ける）。
   - 許可リスト `var.allowed_instance_types` の既定値は、**Web と lab の `validation` の和集合**: `["t4g.micro", "t4g.small", "t4g.medium", "m6i.xlarge", "m6i.2xlarge", "c6i.2xlarge", "t3.xlarge", "t3.2xlarge"]`。古いガードにあった `t3.micro` / `t3.small` / `t4g.large` / `t4g.xlarge` は nwc-poc が使わないので入れない。
3. **ずれを機械で見張る。** `tests/test_lab_debug.py` と同じやり方で、`guard/variables.tf` の `allowed_instance_types` の既定値 = `base/core` の `instance_type` の `validation` の集合 ∪ `pipeline/lab` の `instance_type` の `validation` の集合、を検査する。`ROOTS` に `guard` が無いこと（down.sh が消さないこと）も検査する。
4. **apply と、古いガードの削除はユーザーが行う。** `docs/deploy.md` に手順を書く（下の「運用手順」）。`ops/` にスクリプトは足さない。
5. **ファイルの構成はほかのルートに合わせる**: `versions.tf`（同じ `required_version` と provider）、`providers.tf`（`region`、`default_tags` は `Project = "nwc-poc"` と `ManagedBy = "IaC/terraform/aws-managed/guard"`。owner は環境ごとではないので付けない）、`variables.tf`、`main.tf`、`outputs.tf`（policy の ARN）、`terraform.tfvars.example`、`.terraform.lock.hcl`（`terraform init` で作って追跡する。ほかのルートと同じ）。

### 運用手順（`docs/deploy.md` に書く内容）

1. aws_sandbox を置いている PC で `infra/stacks/always-on/` の `iam-guard.tf` と `variables.tf` の `allowed_instance_types` / `allow_neptune` を消して `terraform apply`（plan に `aws_iam_policy.guard` と `aws_iam_group_policy_attachment.guard` の destroy だけが出ることを確かめる）。aws_sandbox をもう触らないなら、代わりに AWS の管理コンソール（または `aws iam detach-group-policy` と `delete-policy`）で `netops-always-on-guard` を消してもよい。
2. nwc-poc で `terraform -chdir=IaC/terraform/aws-managed/guard init` と `apply`（plan に policy と attachment の create だけ。2 つ）。
3. 確かめ方: `aws iam list-attached-group-policies --group-name netops-always-on-operators` に `nwc-poc-ec2-guard` だけが出る。
4. `ops/down.sh` はこのルートを消さない。消すときは `terraform -chdir=IaC/terraform/aws-managed/guard destroy`。

## 変更対象ファイル

- 新規: `IaC/terraform/aws-managed/guard/{versions.tf,providers.tf,variables.tf,main.tf,outputs.tf,terraform.tfvars.example,.terraform.lock.hcl}`
- `tests/test_lab_debug.py`（または新しい `tests/test_guard.py`。既存の `read` / `check` の部品を使う）: 設計方針 3 の 2 つの検査
- `docs/deploy.md`: 運用手順と、`ops/down.sh` が消さないルートがあること
- `docs/architecture/README.md` の構成表に `guard/` の行
- `docs/cycles/BACKLOG.md` の TRex の行（m6i.xlarge をガードに足す件）は PM が書き換える。実装者は触らない

## 再利用するもの

- `base/core/versions.tf` / `providers.tf` の形
- `tests/test_lab_debug.py` の `read` / `check` と、`validation` の集合を正規表現で取る書き方（`:90` の周り）
- aws_sandbox の `iam-guard.tf` のポリシー文書の形（上の「調査で分かった事実」に書いたもの。リポジトリは見ない）

## 実装ステップ

1. `guard/` の 7 ファイルを書く。`terraform -chdir=IaC/terraform/aws-managed/guard init -backend=false` と `validate`（AWS には触らない。`init` で `.terraform.lock.hcl` を作る）
2. テストを足す（先に赤を確かめる: `allowed_instance_types` から 1 つ消すと落ちること）
3. docs
4. セルフレビュー（`/cycle-build` 手順 6）。AWS の apply はしない

## 検証方法

- `terraform -chdir=IaC/terraform/aws-managed/guard validate` が `Success`
- `terraform -chdir=IaC/terraform/aws-managed/guard plan` は**ユーザーが apply するときに見る**（実装者は走らせない。AWS CLI のセッションが要る）
- `uv run pytest`（または `python tests/test_lab_debug.py`）が通り、`allowed_instance_types` から `m6i.xlarge` を消すと落ちる（赤→緑を build.md に貼る）
- `grep -rn guard ops/check.sh ops/up.sh ops/down.sh` が 0（`ROOTS` に入っていない）
- `tests/test_oss_ops.py` の `ROOTS` を使う検査が、`guard/` を足しても変わらず通る

## 未確定事項とリスク

1. **古いガードを消してから新しいガードを apply するまで、ガードが無い。** ユーザーの決定。その間に `ops/up.sh` を打たない。
2. **ユーザーが apply する IAM ユーザーに `iam:CreatePolicy` / `AttachGroupPolicy` の権限があるか。** aws_sandbox の always-on は admin ロール `netops-always-on-admin` を使っていたはず。nwc-poc の `terraform.tfvars.example` には `profile` を書かず、環境変数（`AWS_PROFILE`）で切り替える前提にする。未確認。
3. **`data "aws_iam_group"` はグループが無いと plan で止まる。** aws_sandbox 側で always-on を丸ごと destroy した場合は、グループも消えるので、このルートは付け先を失う。そのときは `var.operators_group` を変えるか、グループを nwc-poc で作る別サイクルになる。
4. **lab-debug の CloudFormation の `InstanceType` の `AllowedValues`** が lab の `validation` と同じかは `tests/test_lab_debug.py:90` が既定値しか見ていない。違うタイプを許していても、ガードの既定値は Terraform 側の 2 つの和集合に固定する（CloudFormation で別のタイプを選ぶとガードに拒否される。これは意図どおり）。
