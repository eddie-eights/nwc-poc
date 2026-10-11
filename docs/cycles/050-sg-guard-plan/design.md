# 050 SG の作り直しと削除を terraform plan で止める

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-12

QUEUE 163（workload の SG の `name` の式も作り直しの原因になるので守りを足すか決める。048 の cold review の Nit 3）と QUEUE 164（コードの表を awk で読む代わりに Terraform に聞く形にするか決める）を 1 つのサイクルで決める。

## 背景

- `aws_security_group` は `name` / `name_prefix` / `description` / `vpc_id` のどれを変えても作り直し（provider の docs。下の「現物」1）。作り直しも削除も、ほかの SG のルールが古い SG を `referenced_security_group_id` で参照したまま・ENI が付いたままだと `DependencyViolation` で止まり、通るのは `ops/down.sh` の全消しだけ（`ops/up-common.sh:24-27` の 043 / 046 の説明）。
- いまの守り `check_sg_descriptions`（`ops/up-common.sh:32-170`。043 → 046 → 048 → 049 で育てた）は **description だけ**を比べる。しかも「コードを awk で読んで Terraform の評価を真似る」型なので、真似の穴を塞ぐ守り（048 の書き方の守り、049 の `local.oss` の console、`oss_replaced` の行の守り）が積み上がっている。QUEUE 163 の `name` の式（`security_groups.tf:158` の `replace(each.key, "_", "-")`）を同じ型で足すと、`vpc_id` やまだ見ていない式の分も含めて真似がさらに増える。
- Terraform 自身が「どのリソースが作り直し・削除になるか」を知っている。`terraform plan -json` は 1 リソース 1 行の JSON で `action`（`replace` / `delete` / `create` / `update`）と `reason` を出す（下の「現物」2）。これに聞けば、name / description / vpc_id / キーの増減 / `local.oss` の判定 / 表の書き方の全部が 1 本で済み、043〜049 の awk の表読みと console と書き方の守りは要らなくなる。
- 043 の `build.md:148,159` は「plan は打っていない」「down 済みから up すると SG は新しく作られるだけで must be replaced は出ない」と書いており、plan を守りに使う案はこれまで検討していない。

## 現物で確認した事実

1. **provider の docs**（`hashicorp/terraform-provider-aws` の `website/docs/r/security_group.html.markdown`。PM が curl で取った写しは scratchpad）: `name`、`name_prefix`、`description`、`vpc_id` の 4 つに「Forces new resource」。
2. **`terraform plan -json`（Terraform v1.16.0。PM が `terraform_data` のルートで打った。`terraform_data` は組み込みの provider なので AWS の認証情報は要らない）**:
   - stdout は 1 行 1 JSON。`"type"` は `version`（最初の行。`"ui":"1.3"`）/ `planned_change` / `change_summary` / `diagnostic` / `outputs` など。**stderr は空**（Warning も Error も JSON で stdout に出る）。
   - `planned_change` の行（replace。そのまま）:
     ```
     {"@level":"info","@message":"terraform_data.sg[\"b\"]: Plan to replace","@module":"terraform.ui","@timestamp":"2026-10-12T04:16:26.532711+09:00","change":{"resource":{"addr":"terraform_data.sg[\"b\"]","module":"","resource":"terraform_data.sg[\"b\"]","implied_provider":"terraform","resource_type":"terraform_data","resource_name":"sg","resource_key":"b"},"action":"replace","reason":"tainted"},"type":"planned_change"}
     ```
     同じ plan で、`triggers_replace` を変えたキー a は `"action":"replace","reason":"cannot_update"`、for_each からキー c を外したものは `"action":"delete","reason":"delete_because_each_key"`、in-place の更新は `"action":"update"`（`reason` 無し）。変更が無いリソースの `planned_change` は出ない。
   - `change_summary` の行の `changes` は `{"add":2,"change":1,"import":0,"remove":3,"action_invocation":0,"action_fail":0,"operation":"plan"}`。変更が無い plan は `version` と `change_summary`（全部 0）だけ。
   - `terraform.tfvars` に宣言の無い変数があると `{"type":"diagnostic","diagnostic":{"severity":"warning","summary":"Value for undeclared variable", …}}` が stdout に出て、**rc は 0**（`@message` は `Warning: Value for undeclared variable`）。
   - 必須の変数（`owner`）を渡さないと **rc=1**、`severity":"error"`、`summary":"No value for required variable"`（`-json` のときは stdout の JSON、`-no-color` の文字の plan のときは stderr の `Error: No value for required variable`）。
   - `-refresh=false` と `-input=false` が付けられ、`-detailed-exitcode` 無しなら変更があっても rc=0。
   - `terraform show -json <plan file>` の `resource_changes[].change.actions` は `["delete","create"]`（replace）/ `["delete"]` / `["update"]`、`action_reason` は `replace_because_cannot_update` / `replace_because_tainted` / `delete_because_each_key`。**plan ファイルは使わない**（`-out` の分を手順 3 の apply に回すには遠すぎる。下の方針 2）。
3. **`ops/up.sh` の変数**: `base/core` で既定値の無い変数は `owner` だけ（`variables.tf`）。手順 3 の `MAIN_VARS`（`ops/up.sh:732-772`）は `vpc_cidr`（`$VPC_CIDR`。手順 0 の前から分かる）と、手順 1〜2 を経て決まる `create_opensearch_endpoint` / `interface_endpoints` / `network_perimeter` / `endpoints_az_num`。後の 4 つは `security_groups.tf` / `oss.tf` に出てこない（grep 0 件）ので、`aws_security_group` の plan には効かない。`vpc_cidr` は VPC の作り直し → `vpc_id` の変化 → SG の作り直しに効く。
4. **`base/core` の data source**（plan が読む。AWS の認証情報が要る）: `aws_caller_identity` / `aws_partition` / `aws_iam_session_context.deployer` / `aws_ec2_managed_prefix_list.s3` / `aws_ssm_parameter.al2023` / `aws_iam_policy_document.alerts_topic`。手順 0 は `tf_init base/core`（S3 backend）の後なので認証情報はもう使えている。
5. **OSS 版**: `IaC/terraform/oss/base/core` は `aws-managed/base/core` の `*.tf` へのリンクと `oss.auto.tfvars`（`project = "nwc-oss"`）。`ops/oss/up.sh:40` の `TF_DIR=IaC/terraform/oss`。守りは両方の `up.sh` が同じ関数を呼ぶ（`ops/up.sh:609`、`ops/oss/up.sh:163`）。
6. **既存の守りの並び（`ops/up.sh:588-610`）**: `if [ -f "$TF_DIR/base/core/terraform.tfstate" ]` の中で `tf_init base/core` → internal の守り → telegraf / telegraf_dialin のキー変更の守り（`die "… 先に ops/down.sh で消す（stream だけ先に消してもよい）"`。`ops/up.sh:598,604`、`ops/oss/up.sh:158`）→ `check_sg_descriptions`。`PY`（python3 か uv）は `ops/up.sh:438` / `ops/oss/up.sh:131` で、どちらも守りより前に決まる。
7. **テストの土台**: `tests/test_workflow.py:1460-2064` が 043 / 046 / 048 / 049 の節（`_csd_run` の bash の土台、`_sdc_real` / `_sdc_tmp`、本物の console の T-3）。`tests/test_oss_ops.py` は偽の terraform（`FAKE_TF`。`console` と `show` の枝）、`check_sg_descriptions` の呼び方の check（1232-1234）、OSS 版の state の runtime の SG で止める check（1235-1257）、049 の T-9（1258-）。

推測した書式は無い。`aws_security_group` の `planned_change` の行そのものは手元で作れない（AWS の認証情報と state が要る）ので、上の 2 の `terraform_data` の行の `resource_type` / `addr` を `aws_security_group` に置き換えたものをテストの見本にする（JSON の UI の形はリソースの種類によらない。未確定事項 1）。

## 設計方針

1. **`check_sg_descriptions` と `sg_descriptions_in_code` / `sg_descriptions_in_state` を消し、`check_sg_plan` 1 つにする。** `base/core` の `terraform plan -json -input=false -refresh=false -var "owner=$OWNER" "$@"` を打ち、stdout の JSON の行を `"${PY[@]}"` で読んで、`resource_type` が `aws_security_group` の `planned_change` の `action` が `replace` か `delete` なら die。`create` / `update` と、ほかのリソースの変更は通す。
   - `replace` を止める理由は 043 と同じ（name / description / vpc_id のどれでも作り直しになり、作り直しは `DependencyViolation` で通らない）。
   - `delete` も止める。SG が `up.sh` で消えるのは、この repo では意図した操作でない（キーを外すのは `oss_replaced` の msk だけで、それは state に無い）。049 が `oss_replaced` の行の守りで「キーが減る向き」を止めたのを、plan で引き継ぐ。ENI が付いた SG の削除も `DependencyViolation` で止まる。
   - これで QUEUE 163（name）は守りの対象に入り、QUEUE 164（awk の表読みを Terraform に聞く形へ）は「console で表を評価する」より広く、**表も書き方の守りも console も丸ごと不要**になる。`sg_descriptions_in_code` の書き方の守り（048 の `for_each` / `description = each.value` / merge の 2 行 / endpoints の count・for_each 無し、049 の `oss_replaced` の行）は全部消す（plan が評価した結果を見るので、書き方に依らない）。
2. **plan は手順 0 で 1 回打ち、plan ファイルは残さない。** 手順 3 の apply（`ops/up.sh:776`）は手順 1〜2（ECR、イメージ、エンドポイントの決定）の後なので、`-out` を apply に回す形にはしない（間に時間が空き、変数も増える）。守りは「作り直し・削除が無いこと」を見るだけで、apply はいまのまま（`tf_apply`）。
3. **渡す変数は `-var owner` と、手順 0 の時点で分かる `vpc_cidr` だけ。** `ops/up.sh` / `ops/oss/up.sh` の `MAIN_VARS=()` と `vpc_cidr` の行を手順 0 の `if [ -f … terraform.tfstate ]` より前に動かし、手順 3 はそれに足すだけにする。守りは `check_sg_plan ${MAIN_VARS[@]+"${MAIN_VARS[@]}"}` で呼ぶ。残りの変数は既定値で plan する（SG に効かない。現物 3）。
4. **Warning では止めない。** `diagnostic` の行は `@message` と `diagnostic.detail`（あれば）を stderr に出す（049 と同じ考え。apply も Warning では止まらない）。JSON でない行が stdout に混じったら、そのまま stderr に出す（黙って捨てない）。stderr は捕まえず、そのまま流す（049 と同じ）。
5. **止める条件と文言（die は `ops/common.sh` の `die`。全部「まだ何も作っていない」で終える）**:
   - `TF_CLI_ARGS` / `TF_CLI_ARGS_*` があれば plan を打つ前に die（049 のまま。文言の「console と apply で local.oss が違いうる」は「plan と apply で変数の与え方がずれる」に）。
   - plan の rc≠0: `NG: $TF_DIR/base/core の plan が失敗した（rc=<rc>。上の Terraform のエラー）。まだ何も作っていない`（JSON の Error は 4 で stderr に出ている）。
   - `replace` / `delete` の SG があれば: `NG: $TF_DIR/base/core の plan で SG が作り直し・削除になる: <一覧>。SG の name / description / vpc_id を変えると作り直し、表からキーを外すと削除で、ほかの SG のルールが古い SG を参照したままなので、付けるルートだけ消しても消せない（DependencyViolation）。先に $OPS_DIR/down.sh で全部消してから $OPS_DIR/up.sh。<runtime の一言>まだ何も作っていない`。一覧は `addr（action、reason）` を「、」でつなぐ（例: `aws_security_group.workload["workflow"]（replace、cannot_update）、aws_security_group.workload["web"]（delete、delete_because_each_key）`。`reason` が無ければ `（update）` のように action だけ）。`<runtime の一言>` は一覧に `aws_security_group.workload["runtime"]` があるときだけ、いまの `check_sg_descriptions` の `eni` の文（`runtime は Runtime の ENI（agentic_ai。最長 8 時間ほど残る）があるあいだ $OPS_DIR/down.sh も残すので、ENI が消えてから $OPS_DIR/down.sh を打つ。`）。
   - rc=0 なのに `change_summary` の行が無い: `NG: $TF_DIR/base/core の plan の出力に change_summary が無い（terraform plan -json の形が変わったか途中で切れた。上の出力）。まだ何も作っていない`（読んだ stdout を stderr に出してから）。
6. **python は `"${PY[@]}" -c '…'` の 1 本**（`ops/up-common.sh` の既存の呼び方と同じ）。bash の `'…'` の中なので python に `'` を書かない。plan の rc は環境変数（例 `PLAN_RC`）で渡し、python は一覧を stdout に、Warning を stderr に出して、結果を終了コードで返す（0 通す / 2 plan 失敗 / 3 作り直し・削除あり / 4 change_summary 無し）。bash 側はその終了コードで die の文言を選ぶ。
7. **呼ぶ場所と前後の守りは変えない**（手順 0、`tf_init base/core` の後、ECR の前、`if [ -f … tfstate ]` の中）。telegraf / telegraf_dialin の守りの die の「（stream だけ先に消してもよい）」は消す（stream を消しても古いキーの SG の `delete` を 050 の守りが止めるので、案内として誤りになる。`ops/up.sh:598,604`、`ops/oss/up.sh:158`。テストの期待も合わせる）。
8. **AWS では動かさない**（049 と同じ）。plan が本物の S3 backend と state で通ること・所要時間は未確定事項 2 で、QUEUE に別の行で持つ。

## 変更対象ファイル

- `ops/up-common.sh`: 24-31 の見出しのコメントを書き直し、32-170 の 3 関数を `check_sg_plan` に置き換える。
- `ops/up.sh`: `MAIN_VARS=()` と `vpc_cidr` の行（732-733）を手順 0 の前へ。606-609 のコメントと呼び出し。598 / 604 の die の文言。
- `ops/oss/up.sh`: 同じく `MAIN_VARS` の 2 行（296-297）、160-163、158。
- `IaC/terraform/aws-managed/base/core/security_groups.tf`: 14-15、25-26、154、191 のコメント（`check_sg_descriptions` → `check_sg_plan`。「description を変えると」は「name / description / vpc_id を変えると」に。048 の「2 行の形を見る」は消す）。
- `IaC/terraform/aws-managed/base/core/oss.tf`: 11、15、78 のコメント（console で聞く・行の形を見る・merge の 2 行の形を見る、を消す。`local.oss` と `oss_replaced` の説明は残す）。
- `docs/troubleshooting.md`: 26-27 の 2 行を、方針 5 の 3 つの die（作り直し・削除 / plan の失敗 / change_summary 無し）の行に置き換える（`TF_CLI_ARGS*` の行があればそのまま）。
- `docs/architecture/core.md:103`、`docs/architecture/resources/vpc-perimeter.md:98`: 「state の description がコードと違う SG が 1 つでもあると」を「`terraform plan` で作り直し・削除になる SG が 1 つでもあると（name / description / vpc_id の変更とキーの削除）」に。書き方の守りの説明を消す。
- `tests/test_workflow.py`: 1460-2064 を 050 の節に置き換える（下の検証方法）。
- `tests/test_oss_ops.py`: `FAKE_TF` の `console` の枝を消して `plan` の枝を足す、1232-1234 と 1235-1257 と 049 T-9 を 050 の形に。
- `docs/cycles/QUEUE.md` は PM が書く（エンジニアは触らない）。

## 再利用するもの

- `ops/common.sh` の `tf`（`-chdir` と `TF_AWS_ENV`）と `die`。
- `ops/up-common.sh` の `"${PY[@]}" -c '…'` の呼び方（234 / 272 / 309 行）。
- 049 の `TF_CLI_ARGS*` の die と、`eni` の一言、`tests/test_workflow.py` の `_csd_run` の bash の土台（`tf` / `tf_init` / `die` を関数で差し替え、呼び出しをファイルに残す）の作り。
- 現物 2 の `planned_change` / `diagnostic` / `change_summary` の行（テストの見本）。

## 実装ステップ

1. `ops/up-common.sh`: `check_sg_plan` を書き、3 関数と見出しのコメントを消す（方針 1・4・5・6）。
2. `ops/up.sh` / `ops/oss/up.sh`: `MAIN_VARS` の 2 行を手順 0 の前へ、呼び出しとコメント、die の文言（方針 3・7）。
3. `security_groups.tf` / `oss.tf` のコメント、`docs/troubleshooting.md`、`core.md`、`vpc-perimeter.md`。
4. `tests/test_workflow.py` の 050 の節（T-1〜T-14）と `tests/test_oss_ops.py`（T-15〜T-17）。変異 M1〜M8 が落ちることを確かめる。
5. `bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh`、全テスト、`ops/check.sh`。`grep -rn 'check_sg_descriptions\|sg_descriptions_in_' ops IaC docs tests` が `docs/cycles/` と `docs/verification/` 以外で 0 件。

## 検証方法（期待出力つき）

`tests/test_workflow.py` に土台 `_csp_run(lines=(), rc=0, args=(), env=None, real=False)` を置く: bash で `. ops/common.sh; . ops/up-common.sh`、`TF_DIR` は一時ディレクトリ、`OWNER=x`、`OPS_DIR=ops`、`PY=(python3)`、`tf` を関数で差し替えて引数を `$D/tf-args` に 1 行で残し、`lines` を 1 行ずつ stdout に出して `rc` で返す。`die` は本物。`check_sg_plan "${args[@]}"` を打ち、rc / stdout / stderr / `tf-args` を返す。見本の行は現物 2 の JSON の `resource_type` と `addr` を書き換えたもの（`_PC(addr, action, reason=None)` のような小さな作り方で）。`_SUM` は現物の `change_summary` の行、`_VER` は `version` の行、`_WARN` は現物の undeclared variable の `diagnostic` の行。

- **T-1 構造**: `ops/up.sh` と `ops/oss/up.sh` の `check_sg_plan ${MAIN_VARS[@]+"${MAIN_VARS[@]}"}` がちょうど 1 回で、`tf_init base/core` の後、`log "1. ECR` の前にあり、`MAIN_VARS=()` と `vpc_cidr` の行がその前にある。`ops/up-common.sh` に `sg_descriptions_in_code` / `sg_descriptions_in_state` / `check_sg_descriptions` が無く、`check_sg_plan` の本文（コメント除く）に `tf_init` / `state list` / `show` / `console` が無く、`plan -json -input=false -refresh=false -var "owner=$OWNER" "$@"` がある。`ops/up.sh:598,604` と `ops/oss/up.sh:158` に「stream だけ先に消してもよい」が無い。
- **T-2 変更なし**: `lines=[_VER, _SUM(全部 0)]` → rc 0、stdout 空、stderr 空。`tf-args` が `base/core plan -json -input=false -refresh=false -var owner=x` ちょうど 1 行。
- **T-3 create / update は通す**: `_PC('aws_security_group.workload["kafbat"]', "create")`、`_PC("aws_security_group.endpoints", "update")`、`_PC('aws_vpc_security_group_ingress_rule.flow["a"]', "replace", "cannot_update")`（SG でない）+ `_SUM` → rc 0、stderr 空。
- **T-4 replace で止める**: `_PC('aws_security_group.workload["workflow"]', "replace", "cannot_update")` + `_SUM` → rc 1、stderr に `NG: <TF_DIR>/base/core の plan で SG が作り直し・削除になる: aws_security_group.workload["workflow"]（replace、cannot_update）。SG の name / description / vpc_id を変えると作り直し、表からキーを外すと削除で、` と `先に ops/down.sh で全部消してから ops/up.sh。まだ何も作っていない`（runtime の一言は無い）。stdout 空。
- **T-5 delete で止める**: `_PC('aws_security_group.workload["web"]', "delete", "delete_because_each_key")` + `_SUM` → rc 1、一覧が `aws_security_group.workload["web"]（delete、delete_because_each_key）`。
- **T-6 endpoints の replace と、2 件の並び**: `_PC("aws_security_group.endpoints", "replace", "cannot_update")`、`_PC('aws_security_group.workload["web"]', "delete", "delete_because_each_key")` → 一覧が plan の行の順で `aws_security_group.endpoints（replace、cannot_update）、aws_security_group.workload["web"]（delete、delete_because_each_key）`。
- **T-7 reason 無し**: `_PC('aws_security_group.workload["web"]', "replace")`（`reason` のキー自体が無い）→ 一覧が `aws_security_group.workload["web"]（replace）`。
- **T-8 runtime の一言**: `_PC('aws_security_group.workload["runtime"]', "replace", "cannot_update")` → stderr に `runtime は Runtime の ENI（agentic_ai。最長 8 時間ほど残る）があるあいだ ops/down.sh も残すので、ENI が消えてから ops/down.sh を打つ。まだ何も作っていない`。`OPS_DIR=ops/oss` で打つと `ops/oss/down.sh`。
- **T-9 Warning は止めない**: `[_VER, _WARN, _SUM]` → rc 0、stdout 空、stderr に `Warning: Value for undeclared variable` とその `detail` の文。
- **T-10 plan の失敗**: `lines=[_VER, <severity error の diagnostic（summary "No value for required variable"）>]`、`rc=1` → rc 1、stderr に `Error: No value for required variable` と `NG: <TF_DIR>/base/core の plan が失敗した（rc=1。上の Terraform のエラー）。まだ何も作っていない`。`rc=1` で `_SUM` も replace の行もあるとき（止まる理由が 2 つ）も「plan が失敗した」のほう。
- **T-11 JSON でない行と change_summary 無し**: (a) `["not json", _VER, _SUM]` → rc 0、stderr に `not json`。(b) `[_VER]`（change_summary 無し）→ rc 1、stderr に `NG: <TF_DIR>/base/core の plan の出力に change_summary が無い（` と、読んだ stdout（`_VER` の行）。(c) stdout が空で rc 0 → 同じ die。
- **T-12 TF_CLI_ARGS**: `env={"TF_CLI_ARGS_plan": "-refresh=true"}` → rc 1、stderr に `NG: TF_CLI_ARGS / TF_CLI_ARGS_* が設定されている（TF_CLI_ARGS_plan）。` と `plan と apply で変数の与え方がずれる`、`tf-args` が無い（tf を打っていない）。空の `TF_CLI_ARGS=` も止める（049 の決定）。
- **T-13 引数の受け渡し**: `args=("-var", "vpc_cidr=10.9.0.0/16")` → `tf-args` が `base/core plan -json -input=false -refresh=false -var owner=x -var vpc_cidr=10.9.0.0/16`。
- **T-14 本物の terraform**（`terraform` が PATH に無ければ 049 T-3 と同じ形で skip。`.terraform/providers` は要らない ── `terraform_data` は組み込み）: 一時ディレクトリの `base/core` に現物 2 の `main.tf`（`variable "v"` 必須、`variable "w"` 既定 `keep`、`terraform_data.sg` の for_each `{a = var.v, b = "fixed"}` に `triggers_replace = each.value`）を書き、`terraform init -no-color` と `apply -auto-approve -var v=one -var owner=x`（`owner` の変数も宣言しておく）。`real=True` の `_csp_run` は `tf` を差し替えない（本物。`TF_AWS_ENV` は空）。
  - (a) `args=("-var", "v=two")` + `terraform.tfvars` に `undeclared_x = 1` → rc 0（`terraform_data` の replace は SG でない）、stdout 空、stderr に `Warning: Value for undeclared variable`。
  - (b) `args=()`（`v` が無い）→ rc 1、stderr に `Error: No value for required variable` と `NG: … の plan が失敗した（rc=1。`。
  - (c) `args=("-var", "v=one")`（変更なし）→ rc 0、stderr 空。
- **T-15（`tests/test_oss_ops.py`）構造**: T-1 の `ops/oss/up.sh` の分（1232-1234 の置き換え）。
- **T-16 OSS 版の runtime**: 1235-1257 を、`tf` が `base/core plan -json -input=false -refresh=false -var owner=x` に `_PC('aws_security_group.workload["runtime"]', "replace", "cannot_update")` と `_SUM` を返す形に → rc 1、stderr に `NG: IaC/terraform/oss/base/core の plan で SG が作り直し・削除になる: aws_security_group.workload["runtime"]（replace、cannot_update）。` と `先に ops/oss/down.sh で全部消してから ops/oss/up.sh。runtime は Runtime の ENI（agentic_ai。最長 8 時間ほど残る）があるあいだ ops/oss/down.sh も残すので、ENI が消えてから ops/oss/down.sh を打つ。まだ何も作っていない`。
- **T-17 偽の terraform の plan**: `FAKE_TF` の `plan` の枝は `-json` のとき `version` と `change_summary`（全部 0）の 2 行を出して rc 0、引数をログに残す。`ops/oss/up.sh` を通す既存の check（6.）が、ログに `base/core plan -json -input=false -refresh=false -var owner=<OWNER>` を 1 回だけ持ち、それが `init` の後・ECR より前。`console` の枝と 049 T-9 は消す。
- **変異（それぞれ 1 つだけ入れて、どのテストが落ちるかを `build.md` に書く）**: M1 `"delete"` を止める条件から外す → T-5 / T-6 / T-16。M2 `resource_type` の絞りを外す → T-3。M3 plan の rc を見ない → T-10 / T-14(b)。M4 `change_summary` の確認を外す → T-11(b)(c)。M5 `diagnostic` を stderr に出さない → T-9 / T-14(a)。M6 `-refresh=false` を外す → T-2 / T-13 / T-17。M7 `TF_CLI_ARGS*` の die を外す → T-12。M8 `MAIN_VARS=()` を元の手順 3 の位置に戻す → T-1 / T-15。
- 全テスト（`tests/test_*.py` 全部）失敗 0、`bash -n` 3 本 rc=0、`ops/check.sh` すべて通過。

## 未確定事項とリスク

1. **`aws_security_group` の `planned_change` の行は現物を取っていない**（AWS の認証情報と state が要る）。`-json` の UI（`"ui":"1.3"`）はリソースの種類によらず同じ形なので、`terraform_data` の行の `resource_type` / `addr` を置き換えたものを見本にする。読むキーは `type` / `change.resource.resource_type` / `change.resource.addr` / `change.action` / `change.reason` / `diagnostic.severity` / `@message` / `diagnostic.detail` / `changes` の 9 つだけで、Terraform の docs（Machine-Readable UI）にある公開の項目。
2. **本物の S3 backend と state で手順 0 の plan が通ること・所要時間は未確認**（AWS では動かさない）。`-refresh=false` でも data source 6 つは読むので数秒〜十数秒の見込み。既定値の変数（`interface_endpoints` など）で plan が rc≠0 になる筋は見つけていない（validation は既定値を通す）が、もし落ちれば `up.sh` は手順 0 で止まり、Terraform のエラーが stderr に出る（何も作っていない）。QUEUE に「050 の plan の守りを AWS で 1 回通す」を足す（PM）。
3. **止まる側に倒れる変更**: telegraf / telegraf_dialin の古いキーの state は「stream を消せば up.sh が通った」が、050 からは `down.sh` の全消しが要る（方針 7）。2026-10-04 / 10-09 より前の state の話で、PoC では down を打つ運用なので受け入れる。
4. **state の lock**: plan は既定で lock を取る（手順 0 ではほかに何も動いていない）。stale な lock があれば plan が落ちて die する（apply も同じ理由で落ちるので、守りが早く止めるだけ）。`-lock=false` は付けない。
