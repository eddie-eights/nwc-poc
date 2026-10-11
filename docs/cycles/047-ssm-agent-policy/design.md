# 047 EC2 のロールの `AmazonSSMManagedInstanceCore` を自前の SSM Agent の許可に置き換える

設計者: PM(fable-5-1) / effort: high

QUEUE 160。044 のセルフレビュー Round 1 の Should 2（`docs/cycles/044-deny-nautobot-secrets/build.md:84`）。

## 背景

Web（`base/core/web.tf:22-25`）・lab（`pipeline/lab/iam.tf:17-20`）・lab-debug（`IaC/cloudformation/lab-debug.yaml:338-339`）の EC2 のロールは、AWS のマネージドポリシー `AmazonSSMManagedInstanceCore` を付けている。SSM Agent が管理ノードとして登録し、Run Command と Session Manager を受けるための許可だが、同じ Statement に `ssm:GetParameter` / `ssm:GetParameters` が Resource `*` で入っている。そのため 3 つのロールは**同じアカウントのほかの環境の SSM のパラメータ（`/<ほかの prefix>/neo4j-password` など）を読めるまま**になっている。044 は Nautobot の内部のシークレット 3 つだけを prefix を問わない Deny で塞いだが、根本はこの `*` の Allow。

### マネージドポリシーの実物（推測ではなく、AWS から取った）

`arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore` の DefaultVersionId **v2**（2026-10-11 に `iam GetPolicyVersion` で取得）。Statement は 3 つで、Resource は全部 `*`。

| Sid 無し | Action |
|---|---|
| ssm（15） | `ssm:DescribeAssociation`, `ssm:GetDeployablePatchSnapshotForInstance`, `ssm:GetDocument`, `ssm:DescribeDocument`, `ssm:GetManifest`, **`ssm:GetParameter`**, **`ssm:GetParameters`**, `ssm:ListAssociations`, `ssm:ListInstanceAssociations`, `ssm:PutInventory`, `ssm:PutComplianceItems`, `ssm:PutConfigurePackageResult`, `ssm:UpdateAssociationStatus`, `ssm:UpdateInstanceAssociationStatus`, `ssm:UpdateInstanceInformation` |
| ssmmessages（4） | `ssmmessages:CreateControlChannel`, `ssmmessages:CreateDataChannel`, `ssmmessages:OpenControlChannel`, `ssmmessages:OpenDataChannel` |
| ec2messages（6） | `ec2messages:AcknowledgeMessage`, `ec2messages:DeleteMessage`, `ec2messages:FailMessage`, `ec2messages:GetEndpoint`, `ec2messages:GetMessages`, `ec2messages:SendReply` |

### いま各ロールが実際に使っている SSM の API（CloudTrail、2026-10-09〜11、`ssm.amazonaws.com`）

- `efukuda-nwc-poc-web`: `UpdateInstanceInformation`、`ListInstanceAssociations`（SSM Agent）。`GetParameter` は全部 `/efukuda-nwc-poc/` 配下（`kafka-ui/*`、`runtime-arn`、`neptune-graph-id`、`nautobot/url`、`nautobot/api-token`、`grafana/admin-password`）。`GetParameters` 1 回と `GetParametersByPath` 1 回はどちらも AccessDenied（045 の G-2 の検証）
- `efukuda-nwc-poc-lab`: `UpdateInstanceInformation`、`ListInstanceAssociations`。`GetParameter` は `telegraf-address` / `telegraf-source-cidr`（`app/containerlab/lab.sh:310-311` の `lab forward`）だけ。2026-10-11 00:34 と 03:40 の `nautobot/url` / `nautobot/api-token` の読みは 045 の G-3（lab の Run Command から Deny を確かめた。`docs/verification/20261011-aws-managed-045.md:85`）で、コードに読む箇所は無い（`app/containerlab/` と `app/temporal/awsio.py` の `run_on_lab` が打つのは `sudo lab <cmd>` だけ）
- Session Manager（`OpenDataChannel` / `CreateDataChannel`）と `RegisterManagedInstance`（`aws:ec2-instance`）はこの 2 つの外で記録される。`ec2messages` は CloudTrail に出ないので、Run Command が通ったこと（`ops/up.sh` の `ssm_run`）で使っていると判断する

### いまの Allow と Deny（読んだファイル）

- Web `base/core/web.tf` の `web_assets`: `ssm:GetParameter` を `parameter/${local.name_prefix}/*` に Allow、末尾に `DenyNautobotSecrets`（`ssm:GetParameter*` を `local.nautobot_secret_parameter_arns` = `parameter/*/nautobot/{secret-key,admin-password,db-password}` で Deny）。stream / graph / workflow もこのロールに `/<prefix>/*` の `ssm:GetParameter` を足す（044 の design.md:11-17）
- lab `pipeline/lab/iam.tf` の `lab_assets`: `TelegrafAddress`（`ssm:GetParameter` を `telegraf-address` / `telegraf-source-cidr` の 2 つに Allow）と `DenyNautobotSecrets`。`lab_perimeter` は `local.perimeter_policy_arn`（remote state の `network_perimeter_policy_arn`、`try(…, "")`）を `count` で付ける
- lab-debug `lab-debug.yaml` の `lab-assets`: `EcrPull` / `EcrToken` / `S3Read` / `S3List` / `DenyNautobotSecrets`。SSM の Allow は無い（UserData は `aws s3 sync` だけ。`tests/test_lab_debug.py:118-125` が「Allow の Statement に ssm: の Action は無い」を固定）。`Policies[1]` は `!If [HasPerimeter, network-perimeter, AWS::NoValue]`
- base/core の `perimeter.tf` は `aws_iam_policy.network_perimeter`（`<prefix>-network-perimeter`）を作り、`outputs.tf:57` の `network_perimeter_policy_arn` でほかのルートに渡す。lab はそれを remote state で読んで attachment にする。**この形をそのまま写す**
- 古い state への備えの形: `pipeline/lab/locals.tf:30-36` の remote state の `postcondition`（`can(self.outputs.security_group_ids)`。destroy では見ない）と `:45-46` の `try`
- `ops/down.sh` は lab（80 行目）を base/core（87 行目）より先に消すので、base/core が持つポリシーを lab が付けていても DeleteConflict にならない（perimeter と同じ）
- OSS 版の `IaC/terraform/oss/base/core/*.tf` と `oss/pipeline/lab/*.tf` はマネージド版への相対シンボリックリンク（`tests/test_oss.py:928-934` が「ルートの git のファイル全部がリンク」を見る）。**新しい `.tf` を足したら同じ名前のリンクも足す**
- Terraform の state は local（backend ブロック無し。メインのチェックアウト `IaC/terraform/aws-managed/<ルート>/` にある）。`ops/up.sh` / `ops/down.sh` はメインのチェックアウトで打つ
- いま AWS の環境は **down**（`efukuda-nwc-poc-*` の EC2 0 台、`DescribeInstanceInformation` 0 件、`efukuda-nwc-poc*` の IAM ロール無し。2026-10-11 に確認）

## 設計方針

1. **自前のポリシー `<prefix>-ssm-agent` を base/core に 1 つ作り、Web と lab に付ける。** 中身は上の v2 の 3 つの Statement から `ssm:GetParameter` と `ssm:GetParameters` の 2 つを抜いたもの（ssm 13 + ssmmessages 4 + ec2messages 6。Resource は `*` のまま）。ほかの Action は観測した範囲より多いが、`ec2messages` は CloudTrail に出ず、`GetDocument` 等は Run Command / 関連付け / インベントリで要るので、**マネージドポリシーの字面どおり残す**（勝手に削って Run Command や Session Manager を壊さない。削るのは QUEUE の 2 つだけ）
   - `IaC/terraform/aws-managed/base/core/ssm_agent.tf`（新規）: `aws_iam_policy.ssm_agent`。`name = "${local.name_prefix}-ssm-agent"`、`description` は「SSM Agent actions of the AWS managed policy for SSM managed instances (v2) without ssm:GetParameter and ssm:GetParameters (attached to the web and lab EC2 roles)」の趣旨（マネージドポリシーの名前は書かない ── T-1 がコメント以外の行で 0 を見る。em dash を使わない）。Statement の Sid は `SsmAgent` / `SessionChannels` / `Ec2Messages`、Action は上の表の順のまま
   - `outputs.tf` に `output "ssm_agent_policy_arn"`（`value = aws_iam_policy.ssm_agent.arn`。空になることは無い）
   - `IaC/terraform/oss/base/core/ssm_agent.tf` を相対リンク `../../../../terraform/aws-managed/base/core/ssm_agent.tf` で足す
2. **Web: `aws_iam_role_policy_attachment.web_ssm` の `policy_arn` を `aws_iam_policy.ssm_agent.arn` にする。** リソース名は変えない。`web_assets` の `/<prefix>/*` の `ssm:GetParameter` と `DenyNautobotSecrets` はそのまま。`ssm:GetParameters` は足さない（コードも UserData も `get-parameter` しか呼ばない。045 の G-2 の `GetParameters` は検証）
3. **lab: `aws_iam_role_policy_attachment.lab_ssm` の `policy_arn` を `local.ssm_agent_policy_arn` にする。** `locals.tf` に `ssm_agent_policy_arn = try(data.terraform_remote_state.main.outputs.ssm_agent_policy_arn, "")` を足し（`lab_sg_id` と同じ理由: 古い state のまま `ops/down.sh` で消せる）、remote state の `lifecycle.postcondition` に `can(self.outputs.ssm_agent_policy_arn)` を 1 つ足す（error_message は「base/core の state に ssm_agent_policy_arn が無い（047 より前）。先に base/core を apply する（ops/up.sh の順）」の趣旨）。attachment に `count` は付けない（perimeter と違い、無いと管理ノードにならないので黙って飛ばさない）。`lab_assets` の `TelegrafAddress`（2 つの名前）と `DenyNautobotSecrets` はそのまま。**lab に `/<prefix>/*` は足さない**（QUEUE の文言より狭い。lab が読むのは 2 つだけで、CloudTrail でもそれしか無い）
4. **lab-debug: `ManagedPolicyArns` を消し、`Policies` に `PolicyName: ssm-agent` のインラインポリシーを足す。** 中身は方針 1 と同じ 3 つの Statement（Sid・Action・順も同じ、Resource `*`）。位置は `Policies[1]`（`lab-assets` の次、`!If` の境界の前）。スタックは土台と独立なので base/core のポリシーを参照しない（写しを持つ。ずれはテストで止める）。`lab-assets` に SSM の Allow は足さない（デバッグ用の EC2 はパラメータを読まない。既存の「Allow の Statement に ssm: の Action は無い」はそのまま効く）
5. **`DenyNautobotSecrets` は 3 つとも残す。** stream / graph / workflow が Web と Runtime に `/<prefix>/*` の Allow を足すので要る。prefix を問わない Resource もそのまま（将来 `*` の Allow が付いても塞がる）。**「`AmazonSSMManagedInstanceCore`（Resource `*`）に勝つ」と書いたコメント・docs・テストのコメントは、この理由に書き換える**（`base/core/web.tf:69`、`base/core/locals.tf:28`、`pipeline/lab/iam.tf:67-68`、`pipeline/lab/locals.tf:59`、`lab-debug.yaml:363`、`tests/test_lab_debug.py:129`、`tests/test_stream.py:891`、`tests/test_nautobot.py:303`）。コードの `.tf` / `.yaml` / `.sh` のコメント以外の行に `AmazonSSMManagedInstanceCore` を残さない
6. **`ops/up.sh` / `ops/down.sh` は変えない。** 順（base/core → lab、down は lab → base/core）はそのままで足りる。立っている環境に apply すると attachment が差し替わる間だけ SSM Agent の呼び出しが落ちるが、Agent は再試行して戻る（いまは環境が down なので起きない）

## 変更対象ファイル

- `IaC/terraform/aws-managed/base/core/ssm_agent.tf`（新規）、`outputs.tf`（output 追加）、`web.tf`（attachment の policy_arn、コメント）、`locals.tf`（コメント）
- `IaC/terraform/oss/base/core/ssm_agent.tf`（新規のシンボリックリンク）
- `IaC/terraform/aws-managed/pipeline/lab/locals.tf`（local と postcondition、コメント）、`iam.tf`（attachment の policy_arn、コメント）
- `IaC/cloudformation/lab-debug.yaml`（`ManagedPolicyArns` を消し `ssm-agent` のインラインポリシー、コメント）
- `tests/test_lab_debug.py`（T-1〜T-8。既存の `ManagedPolicyArns` の check と `Policies[1]` の添字を直す）、`tests/test_stream.py`（Web の attachment の check、コメント）、`tests/test_nautobot.py`（コメント）
- docs: `docs/architecture/resources/web-ec2.md:21`、`docs/architecture/resources/lab-ec2.md:39` とロールの行、`docs/architecture/resources/ssm-parameter-store.md:86`、ほか `grep -rn 'AmazonSSMManagedInstanceCore\|network-perimeter' docs/architecture` で見つかる箇所（base/core のリソース一覧に `<prefix>-network-perimeter` が載っていれば、その隣に `<prefix>-ssm-agent` を足す）。`docs/cycles/` は触らない

## 再利用するもの

- `base/core/perimeter.tf` の `aws_iam_policy` + `outputs.tf` の ARN の output + lab の remote state からの attachment（形をそのまま写す。違いは `try` の後ろで `count` にしない点だけ）
- `pipeline/lab/locals.tf:30-36` の `postcondition`（古い state を apply で止め、destroy では通す）
- `tests/test_lab_debug.py` の `tf_actions()` / `as_list()`（iam.tf と YAML の Sid ごとの Action を比べる道具。ssm_agent.tf の Statement にも同じ書き方で使えるよう、`Sid      = "…"` の揃え方を lab_assets と同じにする）
- `ops/check.sh`（fmt、10 ルートの `init -backend=false` + `validate`、bash -n、テスト全部）

## 実装ステップ

1. `ssm_agent.tf` と output、OSS 版のリンクを足す。`terraform fmt`
2. Web と lab の attachment を差し替え、lab の local と postcondition を足す
3. `lab-debug.yaml` のロールを直す
4. テスト T-1〜T-8 を `tests/test_lab_debug.py` に足し（Web の T-5 は `tests/test_stream.py` の `web_perimeter` の check の隣）、既存の check を直す。変異 M1〜M7 が全部落ちることを確かめる
5. コメントと docs を方針 5 のとおりに直す
6. `ops/check.sh` を打つ（rc=0）。AWS の `cloudformation ValidateTemplate` を `lab-debug.yaml` に打つ（AWS MCP。TemplateBody は 51,200 バイト未満なので直接渡せる。実リソースは作らない）
7. セルフレビュー（`/cycle-build` 手順6）

## 検証方法

テストの合格条件（期待出力まで）:

| # | 見るもの | 期待 |
|---|---|---|
| T-1 | `IaC/` と `ops/` の `.tf` / `.yaml` / `.sh` のコメント以外の行 | `AmazonSSMManagedInstanceCore` が 0 行 |
| T-2 | `ssm_agent.tf` | `aws_iam_policy.ssm_agent`、`name = "${local.name_prefix}-ssm-agent"`、Statement は Sid `SsmAgent` / `SessionChannels` / `Ec2Messages` の 3 つ、Action はテストに書き下した 13 / 4 / 6 のリストと順も含めて一致、Resource は 3 つとも `"*"`、Effect は全部 Allow |
| T-3 | `ssm_agent.tf` | `ssm:GetParameter` / `ssm:GetParameters` / `ssm:GetParametersByPath` / `ssm:GetParameterHistory` / `ssm:*` / `ssm:Get*` が無い |
| T-4 | `base/core/outputs.tf` | `output "ssm_agent_policy_arn"` の value が `aws_iam_policy.ssm_agent.arn` |
| T-5 | `base/core/web.tf` | `aws_iam_role_policy_attachment.web_ssm` の role が `aws_iam_role.web.name`、policy_arn が `aws_iam_policy.ssm_agent.arn`。`web_assets` の `/<prefix>/*` の `ssm:GetParameter` と末尾の `DenyNautobotSecrets` は既存の check のまま通る |
| T-6 | `pipeline/lab/locals.tf` / `iam.tf` | `ssm_agent_policy_arn = try(data.terraform_remote_state.main.outputs.ssm_agent_policy_arn, "")`、postcondition に `can(self.outputs.ssm_agent_policy_arn)`、`lab_ssm` の policy_arn が `local.ssm_agent_policy_arn` で `count` が無い。`TelegrafAddress` と `DenyNautobotSecrets` は既存の check のまま |
| T-7 | `lab-debug.yaml` | `Role` に `ManagedPolicyArns` が無い。`Policies[1].PolicyName == "ssm-agent"` で、Statement の Sid・Action・順が `ssm_agent.tf` から読んだものと一致、Resource は全部 `"*"`。`Policies[2]` が `!If [HasPerimeter, …, AWS::NoValue]`（既存の check の添字を直す） |
| T-8 | Web（`base/core/web.tf`）・lab（`pipeline/lab/iam.tf`）・lab-debug（`lab-debug.yaml` の `Role`）のロールに付くポリシー | `ssm:GetParameters` の Allow が無い（複数形はこの 3 つのロールでは誰も使わない。ECS のタスクの実行ロールは `secrets` の注入で正当に使うので `IaC/` 全体では見ない） |
| T-9 | `IaC/` の `.tf` と `.yaml` の Allow の Statement 全部 | パラメータを読める ssm の Action（`ssm:GetParameter*` と `*` を含むもの）が Resource `"*"` を持たない（別のポリシーに `*` の Allow が付いて 047 が戻るのをテストで止める） |

変異（1 つずつ入れて、該当のテストが落ちることを `build.md` に書く）: M1 `SsmAgent` から `ssm:UpdateInstanceInformation` を抜く → T-2。M2 `ssm_agent.tf` に `ssm:GetParameter` を足す → T-3。M3 `web_ssm` をマネージドの ARN に戻す → T-1 と T-5。M4 `lab_ssm` に `count = local.ssm_agent_policy_arn != "" ? 1 : 0` を足す → T-6。M5 YAML の `ssm-agent` から `ec2messages:GetMessages` を抜く → T-7。M6 YAML に `ManagedPolicyArns` を戻す → T-7。M7 output を消す → T-4。M14 `web_assets` に `ssm:GetParameter` の Resource `"*"` の Statement を足す → T-9（番号は build.md の Round 1 の M14 に合わせた。M8〜M13 は実装が足した変異）。

ほか:

- `ops/check.sh` が rc=0（fmt / 10 ルートの validate / bash -n / 全テスト）。全テスト 16 本が失敗 0
- `cloudformation ValidateTemplate`（AWS MCP）が `lab-debug.yaml` で通る（エラー無し）
- **AWS で動かす（PM。マージ後にメインのチェックアウトから。state がそこにあるため）。** `AGENT=0 PIPELINE=0 ops/up.sh`（土台だけ。約 $0.15/h）で Web の EC2 が立ち、`ops/up.sh` が `PingStatus = Online` の待ち（`ops/up-common.sh:118`）と `ssm_run` を通って完走する = 新しいポリシーで SSM Agent が登録し Run Command が通る。続けて AWS MCP で (a) `ListAttachedRolePolicies` の `efukuda-nwc-poc-web` が `efukuda-nwc-poc-ssm-agent` と `efukuda-nwc-poc-network-perimeter` だけ、(b) `PutParameter` で `/efukuda-nwc-poc/probe-047` と `/efukuda-nwc-poc-other/probe-047`（String）を作り、Web への Run Command で `aws ssm get-parameter --name /efukuda-nwc-poc/probe-047 --query Parameter.Name` が名前を返し、`--name /efukuda-nwc-poc-other/probe-047` と `--name /efukuda-nwc-poc/nautobot/db-password` が `AccessDeniedException`、`aws ssm get-parameters --names /efukuda-nwc-poc/probe-047` も `AccessDeniedException`。(c) 2 つの probe を `DeleteParameter` で消し、`ops/down.sh` で全部消して、EC2 0 台・ロール無しを確かめてから報告する。lab は土台だけでは立たないが、付けるポリシーは Web と同じ 1 つなので Web で代表させる（lab-debug は ValidateTemplate と T-7 まで）

## 未確定事項とリスク

1. **`ssm_agent.tf` の Action は観測より広い。** `DescribeAssociation` 以下の 11 個（UpdateInstanceInformation と ListInstanceAssociations 以外）は 3 日の CloudTrail に出ていないが、`ec2messages` は CloudTrail に記録されず、Run Command（`GetDocument`）や関連付け・インベントリで Agent が使う。QUEUE のとおりパラメータの 2 つだけを抜き、残りは削らない。削る判断は別のサイクル（ログで裏が取れたとき）
2. **マネージドポリシーが将来 v3 になっても追従しない。** 自前の写しなので、AWS が Agent に新しい API を要求したときは手で足す。Agent の登録が落ちたら（`PingStatus` が Online にならない）まずここを疑う、と docs に 1 行書く
3. **古い base/core の state に新しい lab の code で apply すると postcondition で止まる**（設計どおり。`ops/up.sh` は base/core を先に apply するので通常は起きない）。destroy は通る（`try`）
4. **立っている環境への apply は attachment の差し替えの瞬間に SSM Agent の呼び出しが落ちる。** Agent は再試行する。いまは環境が down なので今回は当たらない
5. **AWS での確認は Web だけ**（土台だけの up.sh）。lab と lab-debug の実機は見ない（同じポリシーの写しで、字面はテストで一致を見る）。Session Manager（ポートフォワーディング）は Run Command と同じ `ssmmessages` の 4 つで、字面はマネージドポリシーと同じなので動くはずだが、実機では見ない
6. 検証に作る probe のパラメータ（`/efukuda-nwc-poc/probe-047`、`/efukuda-nwc-poc-other/probe-047`）は確認後に消す。`ops/down.sh` は `ManagedBy=ops/up.sh` のタグが無いものは消さないので、PM が `DeleteParameter` で消してから down する
7. **lab の EC2 で `app/containerlab/trex/kafka_load.sh` を `BOOTSTRAP` 無しで打つと `/<prefix>/msk-bootstrap` の読み取りが AccessDenied になる**（いままではマネージドポリシーで読めていた）。この道具は lab のロールに MSK の許可が無く、いまも動かない（`app/containerlab/trex/README.md` の「前提」1 がこの読み取りの不足も含めて書いている）。lab の Allow には足さない（`BOOTSTRAP` を手で渡す）
