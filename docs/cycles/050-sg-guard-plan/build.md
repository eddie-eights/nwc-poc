# 050 build: SG の作り直しと削除を terraform plan で止める

## Round 1

実装モデル: opus-5.5 / effort: high（PM のサブエージェント。セルフレビューも high のまま、反対弁護人は opus / xhigh）

ベース: `feat/163-sg-guard-plan` の 52a39c6。design.md の実装ステップ 1〜5。AWS では動かしていない。

### 変更ファイル

- `ops/up-common.sh`
  - `sg_descriptions_in_code` / `sg_descriptions_in_state` / `check_sg_descriptions` と見出しのコメントを消した。
  - `check_sg_plan [-var …]` を置いた（方針 1・4・5・6）。
    - `${!TF_CLI_ARGS*}` が空でなければ die する。
    - `tf base/core plan -json -input=false -refresh=false -var "owner=$OWNER" "$@"` を打つ。
    - stdout を `"${PY[@]}" -c` で読む。plan の rc は `PLAN_RC` で渡す。終了コードは 0 通す / 2 plan 失敗 / 3 作り直し・削除 / 4 change_summary 無し。
    - 一覧 `addr（action、reason）` を「、」でつなぐのと、runtime の一言を足すのは bash 側。
- `ops/up.sh`、`ops/oss/up.sh`
  - `MAIN_VARS=()` と `vpc_cidr` の行を手順 0 の `if [ -f …terraform.tfstate ]` の前へ動かした（手順 3 は足すだけ）。
  - 呼び出しを `check_sg_plan ${MAIN_VARS[@]+"${MAIN_VARS[@]}"}` にし、コメントを直した。
  - telegraf / telegraf_dialin の die から「（stream だけ先に消してもよい）」を消した。
- `IaC/terraform/aws-managed/base/core/security_groups.tf`、`oss.tf`: コメントだけ（`check_sg_descriptions` → `check_sg_plan`、書き方の守りの説明を消した）。
- `docs/troubleshooting.md`: description の行と local.oss の行の 2 行を、3 つの die（作り直し・削除 / plan の失敗 / change_summary 無し）の 3 行に置き換えた。
- `docs/architecture/core.md:103`、`docs/architecture/resources/vpc-perimeter.md:98`: 守りの説明を plan の形に直した。
- `tests/test_workflow.py`
  - 043〜049 の節（1460-2044）を 050 の節に置き換えた。
    - 土台は `_csp_run`。
    - 見本の行は `_PC` / `_VER` / `_SUM` / `_WARN` / `_ERR`。
    - T-1〜T-14 と T-18 を置いた（T-18 はセルフレビューで足した。下の逸脱 7）。
  - T-14 は本物の Terraform v1.16.0 の `terraform_data` で動かす（skip していない）。
- `tests/test_oss_ops.py`
  - `FAKE_TF`
    - console の枝 2 つと show の枝（`check_sg_descriptions` 専用）を消した。
    - `plan -json` の枝（`version` と全部 0 の `change_summary`）を足した。
  - REPO に `security_groups.tf` / `oss.tf` を写す行を消した（`check_sg_plan` はコードを読まない）。
  - T-15 / T-16 / T-17 を置いた。049 T-9 は 2 つとも消した。

### 設計からの逸脱

1. **`tests/test_workflow.py` の置き換え範囲は 1460-2044 にした。**
   - 設計は 1460-2064 と書いている。
   - 2045 行目からは 043〜049 と関係の無い既存の check（`_sg_roots = ("agent",` 以降）なので残した。
2. **`_csp_run` に引数を 2 つ足した。**
   - `tf_dir`（T-14 の本物の terraform の一時ルート）。
   - `ops_dir`（T-8 の `OPS_DIR=ops/oss`）。
3. **`ops/up-common.sh` の die の `rc=$rc。` / `rc=$prc。` を `${rc}` / `${prc}` にした。**
   - 原因: macOS の bash 3.2 は `LC_CTYPE=C.UTF-8` のとき、高位のバイトを識別子の文字に数える。`$rc。` が変数 `rc\xe3` になり、`set -u` で `unbound variable` になる。
   - T-10 が最初これで落ちた。直したあと、up-common.sh / up.sh / oss/up.sh で「`$` + 名前の直後に ASCII でない文字」は grep で 0 件。
   - テストの土台は、出力を `errors="replace"` で読む。
4. **troubleshooting に `TF_CLI_ARGS*` の行は足していない。** 設計は「あればそのまま」としていて、元から無い。
5. **変異 M1 で T-16 は落ちない（設計の期待は T-5 / T-6 / T-16）。**
   - T-16 は設計どおり runtime の replace を見る check で、delete を含まない。
   - M1 は T-5 と T-6 が落とす。
6. **変異で、設計に書いてあるより多くの check が落ちた。** 内容は下の表。
   - M2 は T-14 も落ちる（本物の `terraform_data` の replace を止める）。
   - M5 は T-10 も落ちる（Error の diagnostic が出ない）。
   - M6 は T-1 と T-3〜T-10、T-16 も落ちる（偽の `tf` が引数を完全一致で見る。T-1 は本文の文字列を見る）。
7. **T-18 と変異 M9 を足した（設計に無い）。**
   - 反対弁護人の Should fix 1。`check_sg_plan` の `*)`（python の失敗）の枝を縛る check が無かった。
   - T-18: planned_change の `change` が dict でない行で python が例外で落ちたら、Traceback を stderr に出して「python の失敗。rc=1。」で止める。
   - M9（`*) die …` の前に `return 0;`）は T-18 だけが落とす。

### 検証（最後の編集（T-18 を足した）のあとに取った）

全テスト（`uv run --group dev --group web python <file>`。ops/check.sh と同じ打ち方）:

```
tests/test_agentcore.py rc=0 通過 168 / 失敗 0
tests/test_alerts.py rc=0 通過 168 / 失敗 0
tests/test_analytics.py rc=0 通過 549 / 失敗 0
tests/test_collectors.py rc=0 通過 79 / 失敗 0
tests/test_dashboard_config.py rc=0 通過 3 / 失敗 0
tests/test_graph.py rc=0 通過 78 / 失敗 0
tests/test_kb_index.py rc=0 通過 7 / 失敗 0
tests/test_lab_debug.py rc=0 通過 121 / 失敗 0
tests/test_local_compose.py rc=0 通過 145 / 失敗 0
tests/test_nautobot.py rc=0 （最後の行は「68 項目すべて通過」）
tests/test_oss.py rc=0 通過 177 / 失敗 0
tests/test_oss_ops.py rc=0 通過 210 / 失敗 0
tests/test_oss_roll.py rc=0 通過 66 / 失敗 0
tests/test_stream.py rc=0 通過 119 / 失敗 0
tests/test_sync.py rc=0 通過 104 / 失敗 0
tests/test_workflow.py rc=0 通過 457 / 失敗 0
```

`bash -n`:

```
bash -n ops/up-common.sh rc=0
bash -n ops/up.sh rc=0
bash -n ops/oss/up.sh rc=0
```

`ops/check.sh`（rc=0。各節の行だけ抜いた）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（20 ルート OK）
== 3. スクリプトの構文
bash -n: 33 本
構文エラーなし
== 4. 模擬テスト
（16 本とも上と同じ件数で失敗 0）
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
すべて通過
```

050 の check は check.sh の中で 19 個とも `ok`（T-1 が 2 つ、T-2〜T-18 が 1 つずつ）。T-14 は本物の Terraform v1.16.0 で動いた。

`grep -rln 'check_sg_descriptions\|sg_descriptions_in_\|local.oss=${local.oss}' ops IaC docs tests app .claude`（`docs/cycles/` と `docs/verification/` を除く）:

```
tests/test_workflow.py
tests/test_oss_ops.py
```

どちらも「無いこと」を確かめる check の中だけ（`test_workflow.py:1485-1486` の T-1、`test_oss_ops.py:1222` の T-15）。

### 変異（1 つずつ入れ、`check` を止めずに FAIL を数える版で test_workflow / test_oss_ops を打った。打ったあとは元に戻した）

| 変異 | 入れたもの | 落ちた check |
| :--- | :--- | :--- |
| M1 | `("replace", "delete")` → `("replace",)` | T-5、T-6 |
| M2 | `resource_type == "aws_security_group" and` を外す | T-3、T-14 |
| M3 | `if os.environ["PLAN_RC"] != "0":` → `if False:` | T-10、T-14 |
| M4 | `if not summary:` → `if False:` | T-11 |
| M5 | diagnostic の `@message` / detail を出さない | T-9、T-10、T-14 |
| M6 | `-refresh=false` を外す | test_workflow の T-1〜T-10 と T-13（11 個）、test_oss_ops の T-16、T-17 |
| M7 | `if [ -n "$cli_args" ]; then` → `if false; then` | T-12 |
| M8a | `ops/up.sh` の `MAIN_VARS=()` と vpc_cidr の行を手順 3 の log の直後へ戻す | T-1 |
| M8b | `ops/oss/up.sh` の同じ 2 行を手順 3 の log の直後へ戻す | T-15 |
| M9 | `*) die "$TF_DIR/base/core の plan の出力が読めない…` の前に `return 0;` | T-18 |

M1〜M9 のどれも、少なくとも 1 つの check を落とした（M9 は T-18 を足したあとに打った。M1〜M8 は T-18 を足す前の結果で、T-18 は M1〜M8 の変異箇所に触らない）。

### セルフレビュー

- 自分: opus-5.5 / effort high（サブエージェントなので切り替えられない）。入力は design.md と変更したコード。
- 反対弁護人: opus / effort xhigh（`general-purpose`、読み取り専用、文脈を渡した）。返ってきたあとの `git status --porcelain -uall` は変更 10 件と `build.md` だけ（増えていない）。

#### 指摘

1. **Should fix [missing tests] `ops/up-common.sh:86`: `*)`（python の失敗）の枝を縛る check が無い。**
   - 破綻シナリオ: `*)` を `return 0` に変えても全テストが通る。将来 Terraform の JSON の形が変わって python が例外で落ちると、守りが通してしまい、apply で SG の作り直しが DependencyViolation になる。
   - 再現: `grep -rn "python の失敗" tests ops` は `ops/up-common.sh:86` だけ（tests に 0 件）。
   - 片付け: T-18 を足して直した。M9 は T-18 を落とす（上の表）。全テスト・`bash -n`・`ops/check.sh` は取り直した（上の検証）。
2. **Nit [runtime bugs] `ops/up-common.sh:41`・`:83`: PY が uv のときに uv が rc=2 で落ちると、「plan が失敗した（rc=0。上の Terraform のエラー）」と出る。**
   - 止まる側（fail closed）なので壊れない。文言が不正確なだけ。
   - 再現は反対弁護人（`da050/pyfail.sh`。uv の rc=2 で rc=0 の文言）。自分では再現していない。
   - 片付け: 直していない。最終報告に回す。
3. **Nit [missing tests] `tests/test_workflow.py:1470-1484`（T-1）、`tests/test_oss_ops.py` の T-15: 順番を `find` で見るだけで、`check_sg_plan` が `if [ -f …terraform.tfstate ]` の中にあることは縛っていない。**
   - 破綻シナリオ: 呼び出しを `fi` の外へ動かすと、state の無い初回の up.sh が手順 0 の plan で止まる。test_oss_ops の REPO は全ルートに `terraform.tfstate` を置くので、どのテストも落ちない。
   - いまのコードは中にある（読んだだけ）。片付け: 直していない。最終報告に回す。
4. **Nit [missing tests]: SG の `remove`（`removed` ブロックで state から外す）・`move`・`resource_drift` の行を通す check が無い。**
   - 破綻シナリオ: 照合を `resource_drift` まで広げる変更が入ると、手で消えた SG で up.sh が止まる。いまは planned_change の replace / delete だけを見るので通る。
   - 反対弁護人が本物の v1.16.0 で「remove」の形を確かめた。片付け: 直していない。最終報告に回す。
5. **Nit [設計整合性] `design.md:30`・`:53`・`:113`: 「S3 backend」と書いているが、base/core に backend の宣言が無く local state。** `design.md:115` の「stale な lock」と `docs/troubleshooting.md:27` の「state の lock が残っている」は local state では弱い（プロセスが死ねば外れる。残っているなら別の terraform が走っている）。
   - 読んだだけ。片付け: design.md は PM の持ち物なので直していない。最終報告に回す。
6. **Nit [設計整合性] `design.md:107`: 変異の表が M1 で T-16 も落ちると書いている。** 逸脱 5 に記録済み（T-5 / T-6 が落とすので穴ではない）。最終報告に回す。
7. **Nit [ドキュメント] `docs/troubleshooting.md:28`: 手で打つ plan の例が、up.sh の打ち方（`tf()` の認証、`MAIN_VARS` の vpc_cidr、`-input=false`）と違う。** 手で打つと認証で落ちるか、既定の vpc_cidr で違う差分が出る。読んだだけ。最終報告に回す。

#### 問題なしとした観点と根拠

- 省いた 4 つの変数（interface_endpoints / network_perimeter / endpoints_az_num / create_opensearch_endpoint）は SG の name / description / vpc_id に効かず、既定値で validation を通る（反対弁護人が base/core の .tf を読んだ。読んだだけ）。
- `-refresh=false` で作り直しは隠れない。作り直しの属性はコードから決まり、外で消えた SG は apply で create になるだけ（読んだだけ）。M6 で `-refresh=false` を外すと T-1〜T-10・T-13・T-16・T-17 が落ちる（上の表）。
- plan -json の形（本物の v1.16.0、反対弁護人の `da050/shapes.sh`）: create_before_destroy も `replace`、キーを外すと `delete` / `delete_because_each_key`、tainted は `replace` / `tainted`、deposed は reason の無い `delete`。どれも止まる。自分では T-14 で本物の `terraform_data` の replace と Warning と必須変数の Error を確かめた。
- macOS の bash 3.2 と `LC_CTYPE=C.UTF-8`: 変更の無い plan は通り、runtime の replace と endpoints の delete は両方を並べて ENI の一言を足し、空の `TF_CLI_ARGS_plan` で止まる（反対弁護人の `da050/bash32.sh`。自分では T-1〜T-13 を `/bin/bash` 3.2 で打っている）。
- PY が uv のとき（`uvpy.sh`）: delete は rc=1、ASCII でない detail の Warning は通り、plan の rc=1 は「plan が失敗した」で止まる。
- OSS 版: plan も `IaC/terraform/oss/base/core/oss.auto.tfvars` の `project = "nwc-oss"` を読むので apply と同じ（反対弁護人が読んだ）。T-17 で引数と順番（init → plan → base/ecr）を縛った。
- 「（stream だけ先に消してもよい）」を消したのは正しい。stream だけ消しても、古いキーの SG の delete を `check_sg_plan` が止める（読んだだけ）。
- docs: `docs/architecture/core.md:103`、`docs/architecture/resources/vpc-perimeter.md:98`、`docs/troubleshooting.md` の 3 行はコードと合う（読んだ）。旧名の grep は上の検証。

#### 件数

Must fix 0 / Should fix 1（直した）/ Nit 6（直していない）。未解消の Must fix は無い。
