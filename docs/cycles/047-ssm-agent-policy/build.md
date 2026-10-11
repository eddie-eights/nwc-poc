# 047 build

## Round 1

実装モデル: opus 5.5 / effort: high

### 変更ファイル

- `IaC/terraform/aws-managed/base/core/ssm_agent.tf`（新規。`aws_iam_policy.ssm_agent` = `<prefix>-ssm-agent`、Sid `SsmAgent` 13 / `SessionChannels` 4 / `Ec2Messages` 6、Resource `"*"`）
- `IaC/terraform/oss/base/core/ssm_agent.tf`（新規。相対リンク `../../../../terraform/aws-managed/base/core/ssm_agent.tf`）
- `IaC/terraform/aws-managed/base/core/outputs.tf`（`output "ssm_agent_policy_arn"`）
- `IaC/terraform/aws-managed/base/core/web.tf`（`web_ssm` の policy_arn、DenyNautobotSecrets のコメント）
- `IaC/terraform/aws-managed/base/core/locals.tf`（コメント）
- `IaC/terraform/aws-managed/pipeline/lab/locals.tf`（`ssm_agent_policy_arn` の try、remote state の postcondition 2 つ目、コメント）
- `IaC/terraform/aws-managed/pipeline/lab/iam.tf`（`lab_ssm` の policy_arn、count 無し、コメント）
- `IaC/cloudformation/lab-debug.yaml`（`ManagedPolicyArns` を消し、`Policies[1]` にインラインの `ssm-agent`。`!If` は `Policies[2]` へ。コメント）
- `tests/test_lab_debug.py`（T-1〜T-4、T-6〜T-8 を追加。セルフレビューで T-9 を追加。既存の perimeter の check を `Policies[2]` に、ManagedPolicyArns と `AmazonSSMManagedInstanceCore in iam_tf` の check を消した、コメント）
- `tests/test_stream.py`（T-5 を `web_perimeter` の check の隣に、コメント）
- `tests/test_nautobot.py`（コメント）
- `docs/architecture/resources/web-ec2.md`、`lab-ec2.md`、`ssm-parameter-store.md`、`vpc-perimeter.md`

消した既存の check 2 つ（期待値を変えた理由）: `Role に ManagedPolicyArns が AmazonSSMManagedInstanceCore` と `iam.tf に AmazonSSMManagedInstanceCore がある` は、このサイクルで外すものそのものを要求していた。T-7 と T-1 が逆向きに置き換える。

### 設計からの逸脱

1. **T-8 を設計の字面（`IaC/` 全体で `ssm:GetParameters"` が 0 行）で書けない。** ECS のタスクの実行ロールが正当に使っている（`pipeline/nautobot/access.tf:37`、`pipeline/stream/gnmic.tf:180`、`pipeline/analytics/splunk.tf:399`、`pipeline/analytics/grafana.tf:167`、`workflow/iam.tf:42` と `:60`、`oss/pipeline/graph/neo4j.tf:268`。全部 `role` に `exec` を含む `aws_iam_role_policy`。ECS の `secrets` の valueFrom は GetParameters で引く）。字面どおりだと 7 か所を壊すことになるので、T-8 は「`.tf` では `role` に `exec` を含む `aws_iam_role_policy` の中だけ許す。`.yaml` / `.yml` / `.json` には 0 行。`web.tf` / `ssm_agent.tf` / lab の `iam.tf` には 0 行」に絞った。設計の意図（Web・lab・lab-debug の EC2 のロールに複数形を Allow させない）は守る。→ 設計の T-8 の文言の直しが要る（PM 判断）
2. **ssm_agent.tf の description と output の description にマネージドポリシーの名前を書かない。** 設計方針 1 の description 例は名前を含むが、T-1（コメント以外の行に 0 行）とぶつかる。「AWS managed policy for SSM managed instances (v2)」と言い換えた
3. **M2 は T-2 で先に落ちる**（T-2 が Action のリストを完全一致で見るため、T-3 まで届かない）。T-3 が単独で効くことは M13（T-2 の check を print にした写しで GetParameter を足す）で確かめた
4. 変異を M8〜M13 に足した（postcondition の無効化、YAML への GetParameters、Resource の絞り、web_assets への GetParameters、1 行の余分な Statement、T-2 を外した T-3）
5. **設計に無い T-9 を足した**（セルフレビューの Should fix 1）。`IaC/` の Allow の Statement で、パラメータを読める ssm の Action（`GetParameter*` と `*` を含むもの）が Resource `"*"` を持たない。`.tf` は Statement を字面で切り出し、`.yaml` は CloudFormation として読む。許す例外は `perimeter.tf` の `perimeter_denied_actions`（Deny の Action の表）だけ。変異 M14〜M19

### 検証

#### ops/check.sh（T-9 を足す前。足したあとの取り直しは「セルフレビュー」）

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし

== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/logs  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/logs  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK

== 3. スクリプトの構文
bash -n: 33 本
構文エラーなし

== 4. 模擬テスト
（各テストの最終行。16 本、うち 1 本は「通過」行を出さない形式で、失敗すれば check.sh が die する）
通過 168 / 失敗 0
通過 168 / 失敗 0
通過 549 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 120 / 失敗 0
通過 145 / 失敗 0
通過 177 / 失敗 0
通過 209 / 失敗 0
通過 66 / 失敗 0
通過 119 / 失敗 0
通過 104 / 失敗 0
通過 479 / 失敗 0

== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
```

#### T-1〜T-8（`uv run python tests/test_lab_debug.py` / `tests/test_stream.py` の該当行。長い「いま:」は T-2 以外省略なし）

```
ok Policies[2] は NetworkPerimeter のときだけ境界の Deny（perimeter.tf と同じ Action と条件。SourceVpc はこのスタックの VPC）
ok T-2 ssm_agent.tf の aws_iam_policy.ssm_agent は名前 <prefix>-ssm-agent で、Statement は SsmAgent / SessionChannels / Ec2Messages の 3 つ。Action は v2 から GetParameter / GetParameters を抜いた 13 / 4 / 6 と順も一致、Effect は全部 Allow、Resource は全部 "*"（いま: …）
ok T-3 ssm_agent.tf の Action に ssm:GetParameter / GetParameters / GetParametersByPath / GetParameterHistory / ssm:* / ssm:Get* が無い（ワイルドカードも無い）
ok T-4 base/core の outputs.tf の ssm_agent_policy_arn は aws_iam_policy.ssm_agent.arn
ok T-6 lab は ssm_agent_policy_arn を remote state から try で読み（古い state のまま消せる）、remote state の postcondition が can(self.outputs.ssm_agent_policy_arn) で apply を止める。lab_ssm は role = aws_iam_role.lab.name と policy_arn = local.ssm_agent_policy_arn の 2 行だけ（count / for_each は無い）
ok T-7 lab-debug のロールに ManagedPolicyArns が無く、Policies[1] は ssm-agent で Statement の Sid・Action・順が ssm_agent.tf と一致（Effect は Allow、Resource は "*"、キーは Sid / Effect / Action / Resource だけ）。Policies[0] は lab-assets、Policies[2] は HasPerimeter の !If（いま: ['lab-assets', 'ssm-agent', ['Fn::If']]）
ok T-1 IaC/ と ops/ の .tf / .yaml / .sh のコメント以外の行に AmazonSSMManagedInstanceCore が無い（いま: []）
ok T-8 ssm:GetParameters（複数形）を Allow するのは ECS のタスクの実行ロールだけで、Web・lab・lab-debug の EC2 のロールには無い（いま外れ: []）
通過 120 / 失敗 0
---- tests/test_stream.py
ok T-5 Web のロールの SSM の管理ノードの許可は base/core の自前のポリシー（ssm_agent.tf の aws_iam_policy.ssm_agent。ssm:GetParameter / GetParameters を含まない。cycle 
ok Web のインラインポリシー（web_assets）の Statement の末尾に DenyNautobotSecrets（Deny、ssm:GetParameter*、local.nautobot_secret_parameter_arns）があり、/<prefix>/* の ssm:
通過 119 / 失敗 0
```

#### 変異（scratchpad の mutate.py。1 つずつ入れてテストを流し、戻す。T-9 を足す前の取り直し。足したあとの M1〜M19 は「セルフレビュー」）

```
M1 test_lab_debug: rc=1 AssertionError: T-2 ssm_agent.tf の aws_iam_policy.ssm_agent は名前 <prefix>-ssm-agent で、State | test_stream: rc=0 
M2 test_lab_debug: rc=1 AssertionError: T-2 ssm_agent.tf の aws_iam_policy.ssm_agent は名前 <prefix>-ssm-agent で、State | test_stream: rc=0 
M3 test_lab_debug: rc=1 AssertionError: T-1 IaC/ と ops/ の .tf / .yaml / .sh のコメント以外の行に AmazonSSMManagedInstanceCor | test_stream: rc=1 AssertionError: T-5 Web のロールの SSM の管理ノードの許可は base/core の自前のポリシー（ssm_agent.tf の aws_iam_pol
M4 test_lab_debug: rc=1 AssertionError: T-6 lab は ssm_agent_policy_arn を remote state から try で読み（古い state のまま消せる）、 | test_stream: rc=0 
M5 test_lab_debug: rc=1 AssertionError: T-7 lab-debug のロールに ManagedPolicyArns が無く、Policies[1] は ssm-agent で Statem | test_stream: rc=0 
M6 test_lab_debug: rc=1 AssertionError: T-7 lab-debug のロールに ManagedPolicyArns が無く、Policies[1] は ssm-agent で Statem | test_stream: rc=0 
M7 test_lab_debug: rc=1 AssertionError: T-4 base/core の outputs.tf の ssm_agent_policy_arn は aws_iam_policy.ssm_age | test_stream: rc=0 
M8 test_lab_debug: rc=1 AssertionError: T-6 lab は ssm_agent_policy_arn を remote state から try で読み（古い state のまま消せる）、 | test_stream: rc=0 
M9 test_lab_debug: rc=1 AssertionError: T-7 lab-debug のロールに ManagedPolicyArns が無く、Policies[1] は ssm-agent で Statem | test_stream: rc=0 
M10 test_lab_debug: rc=1 AssertionError: T-2 ssm_agent.tf の aws_iam_policy.ssm_agent は名前 <prefix>-ssm-agent で、State | test_stream: rc=0 
M12 test_lab_debug: rc=1 AssertionError: T-2 ssm_agent.tf の aws_iam_policy.ssm_agent は名前 <prefix>-ssm-agent で、State | test_stream: rc=0 
M13 test_lab_debug: rc=1 AssertionError: T-3 ssm_agent.tf の Action に ssm:GetParameter / GetParameters / GetParamete | test_stream: rc=0 
M11 test_lab_debug: rc=1 AssertionError: T-8 ssm:GetParameters（複数形）を Allow するのは ECS のタスクの実行ロールだけで、Web・lab・lab-debug | test_stream: rc=1 AssertionError: Web の EC2 のスクリプトが読むパラメータは stream が書く 3 本と up.sh が作る admin のパスワードで、名前（/<接頭辞
```

M1 `UpdateInstanceInformation` を抜く / M2 `GetParameter` を足す / M3 `web_ssm` をマネージドの ARN に / M4 `lab_ssm` に count / M5 YAML の `GetMessages` を抜く / M6 YAML に `ManagedPolicyArns` / M7 output を消す / M8 postcondition を `true` / M9 YAML に `GetParameters` / M10 `SsmAgent` の Resource を `arn:aws:ssm:*:*:*` / M11 `web_assets` に `GetParameters` / M12 1 行の余分な Statement（PutParameter） / M13 T-2 を外して `GetParameter` を足す。変異のあと `git status` は意図した変更だけ。

#### cloudformation ValidateTemplate（AWS MCP、ap-northeast-1。TemplateBody はファイルそのもの: 24509 バイト、送った本文と手元のファイルの多項式ハッシュ 42604921 が一致）

```
{"bytes":24509,"hash":42604921,"params":["NamePrefix","Owner","ContainerlabVersion","SrlinuxImageTag","CreateInstance","AzId","TelegrafImageTag","AutoStartLab","VpcCidr","ImageId","NetworkPerimeter","InstanceType","VolumeSize","TrexImageTag"],"capabilities":["CAPABILITY_NAMED_IAM"],"description":"nwc-poc lab debug EC2 (containerlab + Telegraf to stdout on one instance, in its own closed VPC)"}
api_calls: cloudformation ValidateTemplate status=success
```

#### 未実行

- AWS で動かす確認（土台だけの up.sh、Web の PingStatus / Run Command / probe の get-parameter / get-parameters、down）: PM がマージ後にメインのチェックアウトで行う（設計どおり）

### セルフレビュー

- 自分: opus 5.5 / effort high（サブエージェントの中なので xhigh に切り替えられない）。反対弁護人: opus / effort xhigh（general-purpose、文脈を渡し、読み取り専用）。返ってきたあと `git status --porcelain -uall` は増えていない
- 結果: Must 0 / Should 1（直した）/ Nit 4（最終報告へ）

#### 指摘

1. **Should（直した）** [missing tests] `tests/test_lab_debug.py`（T-3 / T-8）、`tests/test_stream.py`（T-5）
   - 破綻シナリオ: Web のロールにはほかのルート（`pipeline/stream/kafka_ui.tf:72`、`agent/runtime.tf:209`、`workflow/proposals.tf:67`）もポリシーを足す。そこか `web_assets` / lab の `lab_assets` に `ssm:GetParameter` か `ssm:*` を Resource `"*"` で足すと 047 が元に戻るが、T-1〜T-8 は `ssm_agent.tf` と `web_ssm` の字面と複数形しか見ないので通る
   - 再現: 直す前に M14（`web_assets` の `ecr:GetAuthorizationToken` に `ssm:GetParameter` を足す）を流し、test_lab_debug は T-9 が無いので通る（落ちたのは test_stream の別の check だけ）。M15（lab の `lab_assets` に `{ Effect = "Allow", Action = "ssm:*", Resource = "*" }`）は両方のテストが rc=0
   - 直した: T-9（逸脱 5）。M14〜M19 が全部 T-9 で落ちる（M16 は先に lab-debug と iam.tf の Sid / Action の一致の check で落ちるので、その 2 つを print にした写しの M19 で T-9 単独を確かめた）
2. **Nit（最終報告へ）** [挙動の変化] `app/containerlab/trex/kafka_load.sh:33`: `BOOTSTRAP` を渡さずに lab の EC2 で打つと `<PARAM_PREFIX>/msk-bootstrap` の読み取りが AccessDenied になる（これまではマネージドポリシーの `*` で読めた）。`app/containerlab/trex/README.md:107` は前から「この読み取りの許可は無い。足すか BOOTSTRAP を手で渡す」と書いており、MSK の `kafka-cluster` の許可も lab に無いので、もともと手で渡す前提。意図した変化
3. **Nit（最終報告へ）** [運用] `IaC/terraform/aws-managed/base/core/ssm_agent.tf:9`: 「ops/down.sh は lab を先に消すので DeleteConflict にならない」は lab の destroy が成功する前提。lab の destroy が失敗したまま base/core の destroy に進めば DeleteConflict になる（`network_perimeter` と同じ形で、新しい失敗の型ではない）
4. **Nit（最終報告へ）** [docs] `docs/architecture/resources/ssm-parameter-store.md:86`: 「cycle 047 までは」は 047 を含むか読み取れない。「047 より前は」が正確
5. **Nit（最終報告へ・範囲外）** [障害が見えない] `app/containerlab/lab.sh:310-311`: `2>/dev/null || t=""` で AccessDenied も「値が無い」と同じ扱い。TelegrafAddress の許可が外れても黙って転送が止まる（047 では lab の許可を変えていない）

反対弁護人が不成立とした懸念（自分でも確かめたもの）: 13 / 4 / 6 は v2 から 2 つ抜いた中身（比較相手は反対弁護人の記憶の v2。AWS 上では未照合。実装時の写しは ssm_agent.tf のコメントの日付）、Web と lab が EC2 の中で読むパラメータは全部 `/<prefix>/*`、Policies の番号に頼るのはテストだけ、OSS 版のリンク、up / down の順。

#### 問題なしとした観点と根拠

- postcondition と destroy（設計のリスク 3）: scratchpad の最小構成（`terraform_data` と local backend の remote state、Terraform 1.16.0、AWS 無し。base の output を null にして 047 より前の state を作る）で実測
```
1. base に ssm_agent_policy_arn あり → lab apply
Apply complete! Resources: 1 added, 0 changed, 0 destroyed.
  rc=0
2. base から ssm_agent_policy_arn を消す（047 より前の state）→ lab plan
Error: Resource postcondition failed
no ssm_agent_policy_arn
  rc=1
3. 同じ古い state で lab destroy
Destroy complete! Resources: 1 destroyed.
  rc=0
4. 古い state のまま lab apply（新規）
Error: Resource postcondition failed
no ssm_agent_policy_arn
  rc=1
```
- T-9 が IaC 全体で誤検知しない: 見た箇所 28、外れ 0（下の取り直し）
- 変異のハーネスの不具合を 1 つ直した: 同じファイルを 2 回書き換える変異（M19）で、戻す内容に 1 回目の書き換えが残り、`tests/test_lab_debug.py:123` の check が print のまま残った（通過が 121 → 120 に減って気づいた）。戻して、ハーネスは最初に読んだ内容だけ保存するようにし、下の M1〜M19 と check.sh を取り直した。いま `^print(` は最後の「通過」の 1 行だけ

#### 直したあとの取り直し

ops/check.sh（各テストの最終行と T-9。test_lab_debug は 121）
```
通過 168 / 失敗 0
通過 168 / 失敗 0
通過 549 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
ok T-9 IaC/ の Allow の Statement で、パラメータを読める ssm の Action（Get
通過 121 / 失敗 0
通過 145 / 失敗 0
68 項目すべて通過
通過 177 / 失敗 0
通過 209 / 失敗 0
通過 66 / 失敗 0
通過 119 / 失敗 0
通過 104 / 失敗 0
通過 479 / 失敗 0
すべて通過
```
fmt 差分なし、validate 20 ルート OK、bash -n 33 本。

変異 M1〜M19（全部どちらかのテストが rc=1）
```
M1 test_lab_debug: rc=1 AssertionError: T-2 … | test_stream: rc=0
M2 test_lab_debug: rc=1 AssertionError: T-2 … | test_stream: rc=0
M3 test_lab_debug: rc=1 AssertionError: T-1 … | test_stream: rc=1 AssertionError: T-5 …
M4 test_lab_debug: rc=1 AssertionError: T-6 … | test_stream: rc=0
M5 test_lab_debug: rc=1 AssertionError: T-7 … | test_stream: rc=0
M6 test_lab_debug: rc=1 AssertionError: T-7 … | test_stream: rc=0
M7 test_lab_debug: rc=1 AssertionError: T-4 … | test_stream: rc=0
M8 test_lab_debug: rc=1 AssertionError: T-6 … | test_stream: rc=0
M9 test_lab_debug: rc=1 AssertionError: T-7 … | test_stream: rc=0
M10 test_lab_debug: rc=1 AssertionError: T-2 … | test_stream: rc=0
M11 test_lab_debug: rc=1 AssertionError: T-8 … | test_stream: rc=1 AssertionError: Web の EC2 のスクリプトが読むパラメータは …
M12 test_lab_debug: rc=1 AssertionError: T-2 … | test_stream: rc=0
M13 test_lab_debug: rc=1 AssertionError: T-3 … | test_stream: rc=0
M14 test_lab_debug: rc=1 AssertionError: T-9 … | test_stream: rc=1 AssertionError: Web の EC2 は t4g.medium …
M15 test_lab_debug: rc=1 AssertionError: T-9 … | test_stream: rc=0
M16 test_lab_debug: rc=1 AssertionError: ロールの Sid は iam.tf と同じ … | test_stream: rc=0
M17 test_lab_debug: rc=1 AssertionError: T-9 … | test_stream: rc=0
M18 test_lab_debug: rc=1 AssertionError: T-9 … | test_stream: rc=0
M19 test_lab_debug: rc=1 AssertionError: T-9 … | test_stream: rc=0
```
M14 `web_assets` に `ssm:GetParameter`（Resource `*`）/ M15 lab の `lab_assets` に 1 行の `ssm:*` / M16 lab-debug の EcrToken に `ssm:GetParameter` / M17 `workflow/proposals.tf` の statement の resources を `["*"]` / M18 OSS 版だけの `oss/pipeline/graph/sync.tf` の resources を `["*"]` / M19 M16 から Sid・Action の一致の 2 つの check を外した写し。ValidateTemplate のあと lab-debug.yaml は変えていない。

#### ジンテーゼ

- T-9 は字面の切り出しなので、Allow の `NotAction` や、Action を locals の別名で渡す形は直接は見ない（locals の表にパラメータの Action を書けば Statement の外として外れに数える）。IAM の全体の評価は AWS での確認（PM のマージ後）が本当の決着
- 13 / 4 / 6 が v2 と一致するかは手元の写しとの比較で、AWS 上の現行版とは照合していない。v3 が出ても追従しない（ssm_agent.tf のコメントに書いた）
- 変化: 「T-1〜T-8 で 047 の退行を縛れる」から「T-1〜T-9 で、字面で書いた Allow の * は縛れる。NotAction と IAM の評価全体は AWS の確認に委ねる」に狭めた
