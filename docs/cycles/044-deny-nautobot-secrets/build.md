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

## Round 2

実装モデル: claude-opus-5-5 / effort: high（PM のサブエージェント。設計モデル fable-5-1 は上位なのでそのまま実装）。ベース `66bfda8`

### 変更ファイル

- `IaC/terraform/aws-managed/base/core/locals.tf`: `nautobot_secret_parameter_arns` の 3 つを `parameter/*/nautobot/<名前>` に。コメントを方針 3 に（使うのは Nautobot と Temporal の init、prefix を問わない理由）
- `IaC/terraform/aws-managed/base/core/web.tf`: Deny の前のコメントだけ（AmazonSSMManagedInstanceCore にも勝つ、prefix を問わない）
- `IaC/terraform/aws-managed/workflow/locals.tf`: `nautobot_secret_parameter_arns` の 3 つを `parameter/*/nautobot/<名前>` に（`param_prefix` を使わない）。コメント
- `IaC/terraform/aws-managed/pipeline/lab/locals.tf`: `nautobot_secret_parameter_arns`（同じ 3 行）を末尾の locals に
- `IaC/terraform/aws-managed/pipeline/lab/iam.tf`: `lab_assets` の Statement の末尾に `DenyNautobotSecrets`（Deny、`ssm:GetParameter*`、`local.nautobot_secret_parameter_arns`。:66-73）
- `IaC/cloudformation/lab-debug.yaml`: `Role` の `lab-assets` の末尾に `DenyNautobotSecrets`（`!Sub` の 3 つ。:363-370）
- `tests/test_stream.py`: locals の check を `parameter/*/nautobot/` にし、`${local.name_prefix}` が無いことも見る（件数は 118 のまま）
- `tests/test_workflow.py`: workflow の locals の check を `parameter/*/nautobot/` にし `param_prefix` / `name_prefix` が無いことも見る。名前の一致の check に `pipeline/lab/locals.tf` を足し、base/core・workflow・lab の 3 つの ARN のリストが同じことを見る（件数は 462 のまま）
- `tests/test_lab_debug.py`: `tf_actions` の正規表現を `[\w*]+` に。「`ssm:GetParameter` を含まない」を「Allow の Statement に `ssm:` の Action が無い」に。check を 3 つ足した（lab の locals、lab の `lab_assets` の Deny と `TelegrafAddress` の Allow、lab-debug の Deny が末尾で Resource が lab の locals と同じ名前）。110 → 113
- `tests/test_nautobot.py`: :303-309（下の逸脱）
- docs: `docs/architecture/resources/nautobot.md:19`、`web-ec2.md:21`、`ssm-parameter-store.md:42` / `:55` / 知見（:85-88）、`temporal.md:63` / `:119`、`lab-ec2.md:39`、`docs/nautobot.md:59`

### 設計からの逸脱

- `tests/test_nautobot.py:303` の「デバッグ用の EC2 は Nautobot を使わない」（`lab-debug.yaml` に `nautobot` の文字列が無い）が、設計どおり `lab-debug.yaml` に Deny を入れると落ちた（`AssertionError: デバッグ用の EC2 は Nautobot を使わない`）。設計の変更対象に test_nautobot.py は無いが、検証 5 は失敗 0 を求める。元の期待値は「Nautobot を使わない」の代わりに「文字列が無い」を見ていたので、Deny を足すと意図に反せず落ちる。直し: `DenyNautobotSecrets` の塊（直前のコメント 1 行と、字下げ 16 桁以上の続く行）を正規表現で除いた残りに `nautobot` が無いこと、塊が実際にあって `Effect: Deny` であることを見る形に狭めた（変異 M11〜M13 で落ちることを確認）
- `ssm-parameter-store.md:55` の Deny の一覧に lab-debug は書いていない（この表は `/<prefix>/` のパラメータを読む相手の表で、lab-debug は相手に無いため）

### 変異（実装ステップ 4 と追加）

`scratchpad/mutate2.py` / `mutate3.py` で 1 つずつ入れてテストを打ち、元に戻した（戻したあとの `git status --short` は実装の変更だけ）。

```
M1 base/core の locals の ARN を ${local.name_prefix} に戻す: test_stream.py rc=1 -> AssertionError: base/core の locals.tf の nautobot_secret_parameter_arns は parameter/*/nautobot/ の …
M1 base/core の locals の ARN を ${local.name_prefix} に戻す: test_workflow.py rc=1 -> AssertionError: Deny する 3 つの名前は、ops/up-common.sh の ensure_nautobot_secrets …
M2 pipeline/lab/iam.tf の DenyNautobotSecrets を消す: test_lab_debug.py rc=1 -> AssertionError: ロールの Sid は iam.tf と同じ …
M3 lab-debug.yaml の Deny の Resource から db-password を抜く: test_lab_debug.py rc=1 -> AssertionError: lab-debug のロールの lab-assets の末尾に DenyNautobotSecrets …
M4 workflow の locals の ARN を ${local.param_prefix} に戻す: test_workflow.py rc=1 -> AssertionError: workflow の locals.tf の nautobot_secret_parameter_arns は parameter/*/nautobot/ …
M5 pipeline/lab の locals の db-password を api-token にする: test_lab_debug.py rc=1 -> AssertionError: pipeline/lab の locals.tf の nautobot_secret_parameter_arns は …
M5 pipeline/lab の locals の db-password を api-token にする: test_workflow.py rc=1 -> AssertionError: Deny する 3 つの名前は、…
M6 lab-debug.yaml の Deny を Allow にする: test_lab_debug.py rc=1 -> AssertionError: ロールの Sid は iam.tf と同じ …（Allow の Statement に ssm: の Action は無い）
M7 lab-debug.yaml の Deny の Action を ssm:GetParameter にする: test_lab_debug.py rc=1 -> AssertionError: Sid ごとの Action は iam.tf と同じ
M8 pipeline/lab/iam.tf の Deny の Action を ssm:GetParameter にする: test_lab_debug.py rc=1 -> AssertionError: Sid ごとの Action は iam.tf と同じ
M9 pipeline/lab の locals の ARN を ${local.name_prefix} にする: test_lab_debug.py rc=1 / test_workflow.py rc=1
M10 lab-debug.yaml の Deny の Resource を ${NamePrefix} にする: test_lab_debug.py rc=1 -> AssertionError: lab-debug のロールの lab-assets の末尾に DenyNautobotSecrets …
M11 lab-debug.yaml の Role の Description に nautobot を足す: test_nautobot.py rc=1 -> AssertionError: デバッグ用の EC2 は Nautobot を使わない …
M12 lab-debug.yaml に Nautobot の Allow を足す（Deny の後ろに別 Statement）: test_nautobot.py rc=1、test_lab_debug.py rc=1
M13 lab-debug.yaml の DenyNautobotSecrets の Effect を Allow にする: test_nautobot.py rc=1 -> AssertionError: デバッグ用の EC2 は Nautobot を使わない …
```

### 検証（最後の編集のあとに取り直した出力）

1 / 2 / 3 / 5. テスト（`uv run --group dev --group web python tests/<file>.py` の末尾）

```
test_stream.py rc=0: 通過 118 / 失敗 0
test_workflow.py rc=0: 通過 462 / 失敗 0
test_lab_debug.py rc=0: 通過 113 / 失敗 0
test_oss_ops.py rc=0: 通過 209 / 失敗 0
test_oss.py rc=0: 通過 177 / 失敗 0
test_nautobot.py rc=0: 68 項目すべて通過
test_graph.py rc=0: 通過 78 / 失敗 0
test_agentcore.py rc=0: 通過 168 / 失敗 0
```

4. `terraform fmt -recursive`（base/core、workflow、pipeline/lab）→ `fmt -check -recursive IaC/terraform/aws-managed`、`init -backend=false` → `validate`（Terraform v1.16.0。`TF_DATA_DIR` はスクラッチパッドなので `.terraform/` はリポジトリに作っていない。`.terraform.lock.hcl` は変わっていない）

```
fmt -check rc=0
== IaC/terraform/aws-managed/base/core
init rc=0
Success! The configuration is valid.
== IaC/terraform/aws-managed/workflow
init rc=0
Success! The configuration is valid.
== IaC/terraform/aws-managed/pipeline/lab
init rc=0
Success! The configuration is valid.
== IaC/terraform/oss/base/core
init rc=0
Success! The configuration is valid.
== IaC/terraform/oss/workflow
init rc=0
Success! The configuration is valid.
== IaC/terraform/oss/pipeline/lab
init rc=0
Success! The configuration is valid.
```

5. `git status --short IaC/terraform/oss` → 空。`git diff --stat -- app/` → 空。`git status --porcelain -uall` に `M` 以外の行は無い。

6. AWS の実機: 未実行（QUEUE 147 で PM が打つ）。

### セルフレビュー

- 自分: claude-opus-5-5 / effort high（サブエージェントの中では切り替えられない）
- 反対弁護人: `Agent`（general-purpose、model opus、effort xhigh）。方針・逸脱・不安な箇所（`*` の一致範囲、test_nautobot の狭め方、check の誤って通る形、docs の事実）を渡し、読み取り専用。終了後の `git status --porcelain -uall` は実装の 16 件だけ（新しいファイル無し）

#### 指摘と片付け

1. Should fix [missing tests] `tests/test_lab_debug.py:137-150`、`tests/test_workflow.py:2197-2200`（Round 1 から）、`tests/test_nautobot.py:306` — Deny の Statement に Condition を足して効かなくする変異がテストを通る。lab-debug の check は Effect / Action / Resource の値だけ見てほかのキーを見ない、lab の check は `Sid` の行からの固定文字列なので Sid の前に足した Condition を見ない、tools の `_gw_stmt` は `"\n  }"` で切るので中の `condition {}` を見ない。test_nautobot の正規表現は字下げ 16 桁以上の行をまとめて消すので Condition の行も消える。Web と Runtime（test_stream）は `{` から `},` まで丸ごと照合するので当たらない。再現（`scratchpad/mutate4.py`。実ファイルに入れて戻した）:
   ```
   M-A lab-debug.yaml の DenyNautobotSecrets に Condition（aws:ViaAWSService=true）を足す: test_lab_debug.py rc=0、test_nautobot.py rc=0
   M-B pipeline/lab/iam.tf の DenyNautobotSecrets の Sid の前に Condition を足す: test_lab_debug.py rc=0、test_workflow.py rc=0
   M-C gateway.tf の tools の DenyNautobotSecrets に condition ブロックを足す: test_workflow.py rc=0
   ```
   片付け: PM の指示（Should fix は直さない）により直さず **PM へ**。直し方の案: lab-debug は `set(_cfn_nb) == {"Sid", "Effect", "Action", "Resource"}`、lab はオブジェクトの `{` から `},` を切り出して丸ごと照合、tools は `"condition" not in _gw_stmt("DenyNautobotSecrets")`
2. Nit [コメントの正確さ] `IaC/terraform/aws-managed/base/core/locals.tf:28`、`pipeline/lab/locals.tf:59`、`tests/test_lab_debug.py:129` — 「AmazonSSMManagedInstanceCore（ssm:GetParameter* が Resource *）」は不正確（許すのは `GetParameter` と `GetParameters`。設計 :11 と `ssm-parameter-store.md:86` は正しい）。「ほかの環境」は同じリージョンに限る（ARN が `${var.region}`。IGW / NAT が無く別リージョンへ届かないので実害なし。反対弁護人の grep）。直さない
3. Nit [docs の言い過ぎ] `pipeline/lab/iam.tf:68`、`docs/architecture/resources/temporal.md:63` / `:119` — 「lab 経由で 042 の Deny が抜けられる」「lab へ Run Command で打っても Deny」は `nautobot/*` 全部を塞いだように読めるが、lab の Deny は 3 つだけで、lab のシェルからは `nautobot/api-token` / `url` を読める（temporal のタスクには Deny がある）。Nautobot の 8080 に入れるのは Web だけ（`security_groups.tf:74`）で実害は小さい。設計のリスク 7 にもこの点が無い。読んだだけ。直さない（PM へ。文面を「3 つは lab 経由でも読めない」に絞るか）
4. Nit [docs の正確さ] `docs/nautobot.md:59`、`docs/architecture/resources/nautobot.md:19` — temporal のタスクの Deny は cycle 042 で自分の prefix だけだが、「cycle 044。prefix を問わない」と同じ括弧に入っている（temporal のタスクには他 prefix の Allow が無いので結果は正しい）。`ssm-parameter-store.md:55` の Deny の一覧に worker（temporal のタスク）が無い（Round 1 から）。読んだだけ。直さない
5. Nit [記録] build.md に Round 2 が無い — この節を書いたので解消
6. 情報 — 版やラベルの指定は設計の未確定事項 6 のまま（QUEUE 147）

#### 問題なしとした観点と根拠

- `parameter/*/nautobot/<名前>` の当たる範囲: IAM の `*` は `/` をまたぐので `/<prefix>/nautobot/db-password` に当たり、`/<prefix>/temporal/db-password`・`nautobot/api-token`・`nautobot/url` には当たらない（反対弁護人。読んだだけ。AWS では 147）
- 読む必要のあるロールに Deny が付いていない: Nautobot のタスクの実行ロール（`pipeline/nautobot/access.tf` の `exec_secrets`）と Temporal の init の実行ロール（`execution_db_passwords`）は変えていない（`git diff --stat` に無い）。Web が読む `nautobot/url` / `api-token`、lab の `telegraf-*` は Deny の名前に入らない（M5 と test の check）
- ほかの抜け道: `GetParametersByPath` / `ecs:RunTask` / `ecs:ExecuteCommand` / `iam:PassRole` の Allow は対象のロールに無い（反対弁護人の grep）。temporal の Run Command は lab のインスタンスにしか打てないので lab-debug は相手にならない
- OSS 版: `pipeline/lab/{iam,locals}.tf` はシンボリックリンクで、`IaC/terraform/oss/pipeline/lab` の validate が通る
- テストが退行を縛ること: M1〜M13 が全部落ちる（上の出力）。落ちない形は指摘 1

#### 未解消

- Must fix: 0
- Should fix（PM へ）: 1（Deny に Condition を足す変異を lab / lab-debug / tools の check が見ない）
- Nit: 2、3、4（5 は解消、6 は情報）
