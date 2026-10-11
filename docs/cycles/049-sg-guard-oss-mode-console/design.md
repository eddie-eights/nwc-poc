# `check_sg_descriptions` の OSS 版の判定を Terraform に聞く（`terraform console` の `local.oss`。`oss_replaced` の行も守る）（049）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-12。ベースは main の `b490486`。Round 2（2026-10-12。Round 1 の `891974d` の上に）: `terraform console` は Warning を **stdout** に出すので、方針 1 を「目印付きの式と `-no-color` で、最後の行だけを見る」に変えた。変数の優先順位の誤り（背景・T-3(f)）も直した。

## 背景

- QUEUE 162（048 の cold review Round 2 の Nit 1 = エンジニアの Nit 1）。043 の守り `check_sg_descriptions`（`ops/up-common.sh:125-146`）は、コード側の文言を `sg_descriptions_in_code`（`ops/up-common.sh:31-105`）で読み、**OSS 版かどうかを `oss.auto.tfvars` に `project = "nwc-oss"` があるかで自分で決めている**（`ops/up-common.sh:37`）。Terraform 側の判定は `oss.tf:11` の `oss = var.project == "nwc-oss"` で、`var.project` は既定値（`variables.tf:23` の `"nwc-poc"`）・`terraform.tfvars`・`*.auto.tfvars`・`TF_VAR_project`・`-var`・`TF_CLI_ARGS*` で決まる。**守りの判定と Terraform の判定が別の場所にあるので、ずれる経路が複数ある**
  - マネージド版で `oss.tf:11` を `oss = true` に書き換える: Terraform は OSS の表を merge して spark の description が「Spark ECS task, local mode (IaC/terraform/oss/pipeline/analytics)」（`oss.tf:23`）になり、state の「EMR Serverless workers (IaC/terraform/aws-managed/pipeline/analytics)」（`security_groups.tf:36`）と違うので spark の SG が作り直し（ほかの SG のルールが参照したままで `DependencyViolation`）。守りは `oss.auto.tfvars` が無いのでマネージド版の表を読み、通す（048 の `review.md` Round 2 で PM が再現: rc=0 17 行、出力は基準と同じ）
  - マネージド版で `TF_VAR_project=nwc-oss` を環境に置く、`IaC/terraform/aws-managed/base/core/terraform.tfvars` に `project = "nwc-oss"` を書く（`docs/development.md:25` が example から写してよいと案内している正規の経路）: 同じく Terraform は OSS、守りはマネージド
  - 優先順位は環境変数 `TF_VAR_*` がいちばん弱く、`terraform.tfvars` → `*.auto.tfvars` → `-var` の順に強い（現物で確認。Round 1 の設計は逆に書いていた）。なので `oss.auto.tfvars` がある OSS 版のルートでは `TF_VAR_project=nwc-poc` を置いても Terraform は OSS のまま。守りが Terraform に聞けばこの順位を真似る必要が無い
- 根本原因は「守りが Terraform の判定を**真似ている**こと」。真似をやめて **Terraform 自身に `local.oss` の値を聞く**（`terraform console`）のがこのサイクル。`local.oss` の定義の行を文字列で守る案（048 と同じ型）は、`var.project` の決まり方のずれを塞げないので採らない
- `oss.tf:14` の `oss_replaced = local.oss ? ["msk"] : []` は OSS 版でマネージドの SG を外す表。これを `["msk", "web"]` のようにすると OSS 版で web の SG が消える（apply は `DependencyViolation`）のに、守りは通す: `sg_descriptions_in_code` の awk は `oss_replaced` を引かずに表のキー全部を出し、`check_sg_descriptions` は state のキーがコードに無ければ飛ばすだけで、**キーが減る向きの守りがどこにも無い**（`ops/up.sh:591-606` の既存のキーの守りは internal / telegraf / telegraf_dialin の名指し）。QUEUE の文言どおりこの行も守りに入れる。こちらは「行の完全一致」でよい（値は定数で、Terraform に聞くまでもない。048 の merge の 2 行と同じ型）

## 現物で確認した事実（推測ではない）

Terraform v1.16.0。`IaC/terraform/aws-managed/base/core` の `*.tf` と `.terraform.lock.hcl` を一時ディレクトリに写し、`.terraform` は実物へのシンボリックリンク（`ops/check.sh` の手順 2 の `init -backend=false` が作ったもの）、`AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null` で AWS の認証情報を一切渡さず、`AWS_PROFILE` ほかも unset で打った（PM が 2026-10-12 に実行。スクリプトは PM のスクラッチパッド `r049/console-probe.sh` / `console-probe2.sh`）:

```
$ terraform -chdir=$d console -var owner=probe <<<'local.oss'
false
（rc=0。real 1.07 s。stdout は od -c で「f a l s e \n」の 6 バイトちょうど。AWS の認証情報は要らない）

$ TF_VAR_project=nwc-oss terraform -chdir=$d console -var owner=probe <<<'local.oss'
true

$ printf 'project = "nwc-oss"\n' > $d/oss.auto.tfvars; terraform -chdir=$d console -var owner=probe <<<'local.oss'
true

$ （oss.tf:11 を「  oss = true」に書き換えて）terraform -chdir=$d console -var owner=probe <<<'local.oss'
true

$ TF_CLI_ARGS_console='-var=project=nwc-oss' terraform -chdir=$d console -var owner=probe <<<'local.oss'
true
（TF_CLI_ARGS_<サブコマンド> はそのサブコマンドだけに効く。TF_CLI_ARGS_apply に同じものがあれば apply だけが OSS になり、console とずれる）

$ TF_VAR_project=bogus terraform -chdir=$d console -no-color -var owner=probe <<<'"local.oss=${local.oss}"'
（rc=1。stderr に「Error: Invalid value for variable … var.project is "bogus"」と「Error: Reference to uninitialized local value」。stdout には「Warning: Due to the problems above, some expressions may produce unexpected results.」の枠だけで、値の行は無い。Round 1 の設計は rc=0 と書いていた。誤り）

$ printf 'undeclared_x = 1\n' > $d/terraform.tfvars; terraform -chdir=$d console -var owner=probe <<<'local.oss' >out 2>err; echo rc=$?
rc=0
（out = 色付き（ESC[33m）の「Warning: Value for undeclared variable … "undeclared_x" … in file "terraform.tfvars" …」の枠のあとに「false」。err = 空。**console は Warning を stdout に出す**。エンジニアが Round 1 で見つけ、PM が再現した）

$ terraform -chdir=$d console -no-color -var owner=probe <<<'"local.oss=${local.oss}"' >out 2>err; echo rc=$?   （terraform.tfvars の undeclared_x はそのまま）
rc=0
（out = 色なしの Warning の枠（「Warning: Value for undeclared variable」〜「use the -compact-warnings option.」）、空行、最後の行が「"local.oss=false"」。err = 空。`-no-color` は console にも効く。式を文字列にすると値は `"` で囲まれて出る。最後の行は od -c で「" l o c a l . o s s = f a l s e " \n」）

$ TF_VAR_project=nwc-oss terraform -chdir=$d console -no-color -var owner=probe -var project=nwc-poc <<<'local.oss'
false   （-var は環境変数より強い）

$ printf 'project = "nwc-oss"\n' > $d/oss.auto.tfvars; TF_VAR_project=nwc-poc terraform -chdir=$d console -var owner=probe <<<'local.oss'
true    （*.auto.tfvars は環境変数より強い。エンジニアは terraform.tfvars でも同じ true を確認した）

$ terraform -chdir=$d console -var owner=probe <<<'local.nope'
Error: Reference to undeclared local value … A local value with the name "nope" has not been declared.
（rc=1。stdout 空）

$ （.terraform のリンクを消して）terraform -chdir=$d console -var owner=probe <<<'local.oss'
Error: … run: terraform init
（rc=1）

$ terraform -chdir=$d console -var owner=probe <<<'local.oss_replaced'
tolist([])                 ← マネージド版
tolist([
  "msk",
])                         ← TF_VAR_project=nwc-oss
```

ほかに確認したこと: `-var owner` を渡さなくても `false` rc=0（`local.oss` は owner を参照しないので聞かれない）。空の state と古い `.terraform.tfstate.lock.info` を置いても `false` rc=0（console は state の lock を取らない・待たない）。`terraform console` は `.terraform.tfstate.lock.info` を残さない。

`check_sg_descriptions` を呼ぶ場所（`ops/up.sh:609`、`ops/oss/up.sh:163`）は、どちらも `if [ -f "$TF_DIR/base/core/terraform.tfstate" ]` の中で `tf_init base/core` の後（init 済みなので console が打てる）。`OWNER` は手順 0 の `resolve_name_prefix`（`ops/up.sh:171`、`ops/oss/up.sh:56`）で必須として決まっている。apply は `-var owner=$OWNER` を渡す（`tf_apply_only`。base/core はさらに `MAIN_VARS` の `-var vpc_cidr=…`（`ops/up.sh:731-733`）。`project` は `-var` で渡していない。`grep -rn 'var.project\|var=project\|TF_VAR_\|TF_CLI_ARGS' ops/ docs/` は 0 件）。`tf()`（`ops/common.sh:32-35`）は `env "${TF_AWS_ENV[@]}" terraform -chdir="$TF_DIR/$root" "$@"`。OSS 版のルート `IaC/terraform/oss/base/core/` は `.tf` がマネージド版へのリンクで、実ファイルの `oss.auto.tfvars`（`project = "nwc-oss"`）を持つ。

テストの土台: `tests/test_workflow.py` の `_csd_run(...)`（1516 行。偽の `tf` は `base/core show -no-color` にだけ答え、ほかは `return 9`。`set -euo pipefail`）と `_sdc_tmp(sg_tf, oss_tf=None, oss=None)`（1702 行。`oss` なら `oss.auto.tfvars` を書く）。`tests/test_oss_ops.py` の偽 terraform `FAKE_TF`（334-415 行。`state show` / `show` / `apply` / `output` / `destroy` 以外は `sys.exit("fake terraform: unknown …")`）と `_CSD_OSS_SH`（1228-1243 行。本物の `check_sg_descriptions` を `tf` だけ差し替えて OSS 版の `TF_DIR` で打つ。`OWNER` は設定していない）。`check(name, cond)` は `assert` で、skip の仕組みは無い。

## 設計方針

1. **OSS 版かどうかは `check_sg_descriptions` が `terraform console` で Terraform に聞く。** `sg_descriptions_in_code` を呼ぶ前に `mode=$(tf base/core console -no-color -var "owner=$OWNER" <<<'"local.oss=${local.oss}"')` を打つ（`tf` 経由なので `-chdir` と `TF_AWS_ENV` は apply と同じ。`-var owner` は apply と同じ変数を渡して差を作らないため。`vpc_cidr` は `local.oss` に関係なく、`MAIN_VARS` はこの時点で未定義なので渡さない）。**console は Warning を stdout に出す**（現物）ので、値は目印付きの式 `"local.oss=${local.oss}"` で聞き、`-no-color` で色のコードを消して、**stdout の最後の行がちょうど `"local.oss=true"` か `"local.oss=false"`**（`"` を含む 16 / 17 文字）で、**その形の行が stdout 全体でちょうど 1 行**のときだけ受ける（console は Warning の枠のあとに値を最後に出す。現物）。rc≠0、最後の行が違う、目印の行が 0 行か 2 行以上、のどれでも die: `"$TF_DIR/base/core の local.oss が読めない（terraform console の失敗か、stdout の最後の行が \"local.oss=true\" / \"local.oss=false\" でない。上の出力）。まだ何も作っていない"`。die の前に読んだ stdout を全部 stderr に出す（Round 1 と同じ）。受けるときも、最後の行以外（Warning の枠）があればそのまま stderr に出す（Warning を黙って捨てない。`terraform.tfvars` の宣言の無い変数のような無害な Warning で `up.sh` を止めない ── 049 より前の `up.sh` は止まらなかったし、apply も Warning では止まらない）。console の stderr は捨てずにそのまま流す（validation の Error や init のエラーが「上の出力」）
2. **`sg_descriptions_in_code` は OSS 版かどうかを引数で受ける**: `sg_descriptions_in_code <0|1>`。`oss.auto.tfvars` を grep する行（`ops/up-common.sh:37`）は消す。引数が `0` / `1` 以外（無しも）なら `NG: sg_descriptions_in_code の引数（OSS 版なら 1、マネージド版なら 0）が無いか 0 / 1 でない（いま $# 個「$*」）` を stderr に出して `return 1`（既存の die に乗る。Round 1 の文言をそのまま正本にした）。awk の `-v oss=` と中身は 048 のまま
3. **順番**: `TF_CLI_ARGS*` の検査（方針 4）→ console → `sg_descriptions_in_code` → `terraform show`。console が読めなければ show は打たない。コードが読めなければ show は打たない（既存どおり）
4. **`TF_CLI_ARGS` / `TF_CLI_ARGS_*` が環境にあれば die。** bash の `"${!TF_CLI_ARGS@}"` で名前を集め、1 つでもあれば `"TF_CLI_ARGS / TF_CLI_ARGS_* が設定されている（<名前を空白区切り>）。terraform のサブコマンドごとに変数の与え方がずれる（console と apply で local.oss が違いうる）ので、外してから打ち直す。まだ何も作っていない"`。console も show も打たない。ops/ と docs/ に `TF_CLI_ARGS` の使い方は無いので、正規の利用を止めることはない
5. **`oss.tf` の `oss_replaced` の行は完全一致で守る**（awk。048 の merge の 2 行と同じ型）: `  oss_replaced = local.oss ? ["msk"] : []`（`=` の前の空白は 1 つ以上）の行が `oss.tf` にちょうど 1 行あること。`  oss_replaced` で始まる行で完全一致でないものはその行を NG に出す（`FILENAME: 行`。行末のコメント・タブも NG）。無ければ `oss.tf に「  oss_replaced = local.oss ? ["msk"] : []」の行が無い`。NG の文言は 048 の `NG: aws_security_group.workload の書き方が読めない:` に続けて出す（同じ「表が SG になる経路」の守り）。マネージド版でも OSS 版でも見る（マネージド版は `local.oss` が false なので値は空になるが、行の形は版に依らない）
6. **`local.oss` の定義の行（`oss.tf:11`）は文字列では守らない。** 方針 1 で Terraform に聞くので、`oss = true` と書いても `TF_VAR_project` を置いても、console の答え（true）どおりに OSS の表を読み、マネージド版の state と比べて spark で止まる（これが Nit 1 の閉じ方）。`oss_replaced` の守りに `local.oss` の文字列が含まれるのは副作用で、定義の行の守りではない
7. `check_sg_descriptions` の die の文言（「state の SG の description がコードと違う」「読めない」）と `sg_descriptions_in_state` は変えない。`ops/up.sh` / `ops/oss/up.sh` の呼び方（引数無しで 1 回）も変えない
8. **AWS では動かさない。** state は `tests/test_workflow.py` の `_csd_show` の見本。**本物の terraform は使う**（方針 9。`ops/check.sh` の手順 2 が init するので手順 4 のテストで必ず走る）
9. **本物の `terraform console` を通すテストを 1 本持つ**（T-3）。`IaC/terraform/aws-managed/base/core` の `*.tf` と `.terraform.lock.hcl` を一時ディレクトリに写し、`.terraform` は実物へのシンボリックリンク、`AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null`、`AWS_PROFILE` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` / `TF_VAR_*` / `TF_CLI_ARGS*` は環境から外す。`tf` は console だけ本物の `terraform -chdir="$TF_DIR/$1"` に渡し、`show` は見本を返す。`terraform` が PATH に無い・`.terraform/providers` が無いときは `print("skip: …")` して `check` を呼ばない（`passed` は増やさない。check.sh では init のあとなので必ず走る）。`terraform.tfvars` は写さない（実物のディレクトリに利用者が書いたものを拾わない）

## 変更対象ファイル

- `ops/up-common.sh` — `check_sg_descriptions`（方針 1・3・4）、`sg_descriptions_in_code`（方針 2・5。ヘッダのコメント `ops/up-common.sh:32` の「OSS 版（oss.auto.tfvars が …）」を「OSS 版（引数 1。`check_sg_descriptions` が `terraform console` の `local.oss` で決める。cycle 049）」に）。上のブロックコメント（`ops/up-common.sh:29-30`）に 049 の 1 行（「OSS 版かは `terraform console` で Terraform に聞く。`oss_replaced` の行も見る」）
- `tests/test_workflow.py` — `_csd_run` の偽 `tf` に `console` を足す（`base/core console -no-color -var owner=tester` を記録し、stdin を `$D/console-stdin` に保存、既定は `oss` なら `"local.oss=true"` / `"local.oss=false"` の 1 行。新しい引数 `console=(rc, stdout, stderr)` で差し替え）。スクリプトに `OWNER=tester`。`oss.auto.tfvars` は書かない（守りが読まないことを変異 M1 で示すため）。`_sdc_tmp(sg_tf, oss_tf=None, oss=None, arg=None)` は `oss.auto.tfvars` を書かず `sg_descriptions_in_code <0|1>` を引数で渡す（`arg` を渡したらその文字列をそのまま引数に。無しも試せるように）。T-1〜T-9。`_sdc_real`（実物で出す件数）など既存の `_sdc_tmp` の呼び出し 8 件は `oss=` の意味が変わらないのでそのまま通ること
- `tests/test_oss_ops.py` — `FAKE_TF` に `console` を足す（stdin を読み、`"local.oss=${local.oss}"` なら `-chdir` の値に `/oss/` が含まれれば `"local.oss=true"`、含まれなければ `"local.oss=false"`。ほかの式は `sys.exit("fake terraform: console unknown expr")`。ログに `stdin` も残す）。`_CSD_OSS_SH` に `OWNER=x` と、偽 `tf` の `console` の答え（`base/core console -var owner=x` → `true`）。偽 REPO の up.sh の通し（base/core の state がある経路）が console を 1 回打つこと
- `docs/architecture/core.md:103` と `docs/architecture/resources/vpc-perimeter.md:98` — 048 の一言のあとに「OSS 版かどうかは `terraform console` の `local.oss` で Terraform に聞く（`oss.auto.tfvars` ではない）。`oss.tf` の `oss_replaced` の行も見る（049）」（Round 1 で済み）
- `docs/troubleshooting.md` の「`ops/up.sh` / Terraform」の表に 1 行（Round 2。cold review の Nit 4）: 症状「手順 0 の後に「… の local.oss が読めない（terraform console の失敗か …）」で止まる」。原因と直し方: `check_sg_descriptions` が OSS 版かどうかを `terraform console` で `local.oss` に聞いて読めなかった。上に出た Terraform のエラーを読む（`var.project` の値が `nwc-poc` / `nwc-oss` 以外（`TF_VAR_project` や tfvars）、`.terraform` が無い（`tf_init` の失敗）、state が壊れている）。Warning だけでは止まらない。まだ何も作っていない
- `IaC/terraform/aws-managed/base/core/oss.tf` のコメント 2 行（Round 1 で済み）。Round 2 で「`ops/up.sh` の `check_sg_descriptions`」を「`ops/up-common.sh` の `check_sg_descriptions`（`ops/up.sh` と `ops/oss/up.sh` が呼ぶ）」に直す（エンジニアの Nit 3。関数は `up-common.sh` にある）。`terraform fmt -check` が通ること
- `docs/cycles/QUEUE.md` — PM が書く（エンジニアは触らない）

## 再利用するもの

- `tests/test_workflow.py` の `_csd_run(edits=…, remove=…)` と `_sdc_tmp`、`_csd_show`、`_SG_CODE_DESC` / `_SG_CODE_DESC_OSS`、`_sg_tf_core` / `_oss_tf_core`
- awk の既存の `wbad` と END の `NG: aws_security_group.workload の書き方が読めない:`（方針 5 の行をここに足す）
- `ops/common.sh` の `tf()`（console もこれで打つ）
- 048 の `oss.tf` のコメントの形（`oss.tf:76`）

## 実装ステップ

Round 2 は Round 1 の `891974d` の上に差分で入れる（やり直さない）。変えるのは方針 1 の console の呼び方と stdout の読み方、T-1 / T-3(g)(h) / T-4 / T-8 / T-9 の期待、docs 2 件、`oss.tf` のコメントの文言。

1. `ops/up-common.sh`: `check_sg_descriptions` の頭に `TF_CLI_ARGS*` の die（方針 4）と console（方針 1。Round 2: `-no-color` と目印付きの式、最後の行とその形の行の数で判定、Warning の行は stderr へ）。`sg_descriptions_in_code "$oss"` に引数で渡す
2. `sg_descriptions_in_code`: 引数の検査（方針 2）、tfvars の grep を消す、awk に `oss_replaced` の行の規則（方針 5）
3. `tests/test_workflow.py`: 偽 `tf` の console、`OWNER`、`_sdc_tmp` の引数、T-1〜T-9。変異 M1〜M10 がそれぞれのテストで落ちることを確かめる（`build.md` に変異ごとに落ちた check の名前を書く）
4. `tests/test_oss_ops.py`: `FAKE_TF` の console、`_CSD_OSS_SH`
5. docs 2 か所（Round 1 で済み）、`docs/troubleshooting.md` の行（Round 2）、`oss.tf` のコメント 2 行（Round 2 で文言を直す）、ヘッダのコメント
6. 全テスト（`tests/test_*.py` 16 本）、`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh`（1 本ずつ）、`ops/check.sh`

## 検証方法（期待出力つき）

| # | 何を | 期待 |
| :--- | :--- | :--- |
| T-1 | 土台（偽 `tf`）: マネージド版、console が `"local.oss=false"` | `_csd_run()` が rc=0、`DONE`、calls がちょうど `["tf base/core console -no-color -var owner=tester", "tf base/core show -no-color"]` の順、`console-stdin` が `"local.oss=${local.oss}"`（改行 1 つ）、stderr は空（Warning が無ければ何も出さない） |
| T-2 | 偽 `tf` の console が `"local.oss=true"` | (a) OSS 版の state（`_SG_CODE_DESC_OSS`）→ rc=0。(b) マネージド版の state → rc=1、stderr に `spark（state「EMR Serverless workers (IaC/terraform/aws-managed/pipeline/analytics)」/ コード「Spark ECS task, local mode (IaC/terraform/oss/pipeline/analytics)」）` と「先に ops/down.sh で全部消してから」。`oss.auto.tfvars` は無い（守りはそれを見ない） |
| T-3 | 本物の `terraform console`（方針 9。`tf` の show は偽物） | (a) そのまま → rc=0 で calls に console と show。(b) `oss.tf` の `  oss = var.project == "nwc-oss"` → `  oss = true`（048 の Nit 1 の再現）→ rc=1、stderr に T-2(b) の spark の違い。(c) `TF_VAR_project=nwc-oss` → 同じ rc=1 で spark。(d) `oss.auto.tfvars` に `project = "nwc-oss"` → 同じ。(e) OSS 版の state + `TF_VAR_project=nwc-oss` → rc=0。(f) `TF_VAR_project=nwc-poc` + `oss.auto.tfvars` あり + マネージド版の state → rc=1 で spark（tfvars が環境変数に勝つ。守りも同じ答えを使う）。(g) `terraform.tfvars` に `undeclared_x = 1`（宣言の無い変数）+ マネージド版の state → **rc=0**、calls に console と show、stderr に「Warning: Value for undeclared variable」（色のコード `\x1b[` は無い）。(h) `TF_VAR_project=bogus` → rc=1、stderr に「Invalid value for variable」と「local.oss が読めない」、show は打たない。`terraform` が無い・`.terraform/providers` が無ければ `skip:` を出して check を呼ばない |
| T-4 | console の出力の形（偽 `tf`。`console=(rc, stdout, stderr)`） | 止まるもの（どれも rc=1、stderr に読んだ stdout と「local.oss が読めない」「まだ何も作っていない」、show は打たない = calls は console だけ）: `false`（目印の無い裸の値。Round 1 の形）/ `"local.oss=True"` / 空 / `"local.oss=false"\n"local.oss=false"`（目印が 2 行）/ `"local.oss=false"\nfoo`（最後の行が目印でない）/ `"local.oss=false" `（末尾に空白）/ `"local.oss=false"` を出して rc=1。通るもの（rc=0、show も打つ）: `"local.oss=false"` だけ / Warning の枠 3 行 + 空行 + `"local.oss=false"` → stderr に Warning の 3 行がそのまま出て、`"local.oss=false"` の行は stderr に出ない / `"local.oss=true"` + OSS 版の state |
| T-5 | `TF_CLI_ARGS*` | 環境に `TF_CLI_ARGS=-no-color` / `TF_CLI_ARGS_apply=-var=project=nwc-oss` / `TF_CLI_ARGS_console=-var=project=nwc-oss` のどれか 1 つ → rc=1、stderr にその名前と「外してから打ち直す」「まだ何も作っていない」、calls は空（console も show も打たない）。無ければ T-1 のとおり |
| T-6 | `oss_replaced` の行 | `_csd_run(edits=[("oss.tf", …)])` で `["msk"]` → `["msk", "web"]` / 行を消す / `local.oss ? ["msk"] : ["web"]` / 行末に ` # memo` / `oss_replaced = ["msk"]`（三項を外す）→ どれも rc=1、stderr に「aws_security_group.workload の書き方が読めない」とその行（消したものは「行が無い」）、「security_groups.tf から SG の description が読めない」、show は打たない（calls は console だけ）。マネージド版と OSS 版（console `"local.oss=true"`）の両方。`_sdc_tmp` も rc=1 で stdout 空 |
| T-7 | `sg_descriptions_in_code` の引数 | `_sdc_tmp(arg="")`（引数無し）/ `arg="2"` → rc=1、stdout 空、stderr に「引数」。`arg="1"` と `oss_tf` の組は 048 までの OSS 版の check と同じ出力（`oss.auto.tfvars` は書かない） |
| T-8 | 静的 | `ops/up.sh` / `ops/oss/up.sh` / `ops/up-common.sh` に `var.project` / `var=project` / `var project` / `TF_VAR_` の文字列が無い。`sg_descriptions_in_code` の本文に `oss.auto.tfvars` が無く、`check_sg_descriptions` の本文に `console -no-color` が `sg_descriptions_in_code` より前、`show -no-color` より前にある |
| T-9 | `tests/test_oss_ops.py` | `_CSD_OSS_SH` の既存の check（古い runtime の SG で止まる）がそのまま通る。`FAKE_TF` に `console` を打つと `-chdir=…/oss/base/core` で `"local.oss=true"`、`…/aws-managed/base/core` で `"local.oss=false"`、`local.nope` で rc≠0。偽 REPO の `ops/oss/up.sh` の通しで `console` のログがちょうど 1 件（`-chdir` が base/core、stdin が `"local.oss=${local.oss}"`） |

変異（それぞれ T が落ちること。`build.md` に落ちた check の名前を書く）: M1 console を打たず `oss.auto.tfvars` の grep に戻す → T-1（calls）/ T-2(b) / T-3(b)(c)。M2 console の出力の検査を外す（空や `True` も通す）→ T-4。M3 `TF_CLI_ARGS*` の die を消す → T-5。M4 `oss_replaced` の規則を消す → T-6。M5 console を show の後に打つ → T-4（calls に show が混ざる）/ T-8。M6 `oss_replaced` の完全一致を前方一致にする → T-6（行末コメント・`["msk", "web"]`）。M7 読んだ stdout（Warning）を stderr に出さない → T-3(g) / T-4（Warning の 3 行が stderr に無い）。M8 最後の行でなく最初の行を見る → T-3(g) / T-4（Warning + 値で止まる）。M9 目印の行の数を見ない → T-4（目印が 2 行で通る）。M10 `-no-color` を外す → T-8 / T-3(g)（stderr に `\x1b[`）。

ほかに: 全テスト（`tests/test_*.py`）が失敗 0。`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` rc=0。`ops/check.sh` 通過（`terraform fmt -check` に `oss.tf` のコメントが通ること）。

## 未確定事項とリスク

1. `terraform console` は `.terraform`（init 済み）が要る。`check_sg_descriptions` は `tf_init base/core` の後にしか呼ばれない（`ops/up.sh:590,609`、`ops/oss/up.sh`）ので満たす。将来 init の前に動かすと「run: terraform init」の die になる（読めない側に倒れる）
2. console は AWS の認証情報を使わない（現物で確認: 認証情報なしで rc=0）。`tf()` 経由で `TF_AWS_ENV` が渡るが、プロファイルの `credential_process` は呼ばれない。state に data source があっても refresh しない（読んだだけ。probe は空の state）。本物の state で console を打つ検証は AWS では行わない（方針 8）
3. `TF_CLI_ARGS*` の die は `-no-color` のような無害な使い方も、`TF_CLI_ARGS=`（空の値で export）や `TF_CLI_ARGS_init` のような判定に関係しない名前も止める。ops/ と docs/ に使い方が無いので受け入れる（止まる側に倒す。die が名前を言うので外し方は分かる）。`TF_VAR_*` は console と apply の両方に同じに効くので止めない（それが「Terraform に聞く」の意味）
4. console と apply のあいだに利用者が tfvars や環境を変えれば当然ずれる。守りは「`up.sh` を打った時点の Terraform の判定」を使うだけ
5. `oss_replaced` の行の完全一致は、OSS 版で外す SG を足す正規の変更（例: `["msk", "opensearch_serverless"]`）でも `up.sh` を止める。NG の行が直す場所（`ops/up-common.sh` の `sg_descriptions_in_code`）を言うので受け入れる（048 の merge の 2 行と同じ倒し方）。外す SG を足すこと自体は state の SG が消える変更なので、止めるのが正しい
6. `terraform console` の所要は約 1 秒（現物）。`up.sh` の手順 0 の中なので気にしない
7. T-3 は `.terraform` のシンボリックリンクに依る。実物へのリンクで動くことは現物で確認した（rc=0、1.07 s）。provider のバージョンが lock と食い違う環境では console が止まり T-3 が落ちる（`ops/check.sh` の手順 2 の init で揃う）
8. 最後の行の判定は「console が値を最後に出す」ことに依る（Terraform v1.16.0 の現物。Warning は値の前）。将来 console が値のあとに何か出す版になれば「読めない」で止まる（止まる側）。Warning の中に目印とちょうど同じ行が入ることは、Terraform の Warning の枠が `│` で始まるか `-no-color` では `  ` の字下げになるので無い（現物の枠の行は空行以外すべて先頭が `Warning:` か文章か空白）。目印の行の数を見るのは念のため
9. 広い案（console で `local.workload_security_groups` の map 全体を評価し、043/046/048 の awk の表の読み方と形の守りをまるごと置き換える）はこのサイクルでは採らない。`tests/test_workflow.py` の土台 61 か所の書き換えになる。QUEUE に候補として足す（PM）
