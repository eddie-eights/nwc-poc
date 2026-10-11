# 049 build: check_sg_descriptions の OSS 版の判定を Terraform に聞く

## Round 1

実装モデル: opus-5.5 / effort: high（PM のサブエージェント。セルフレビューも high のまま）

ベース: `feat/162-sg-guard-oss-mode-console` の e8a52a5。design.md の実装ステップ 1〜6。AWS では動かしていない。

### 変更ファイル

- `ops/up-common.sh`
  - `check_sg_descriptions`:
    - 頭で `${!TF_CLI_ARGS*}` が空でなければ die する。
    - `tf base/core console -var "owner=$OWNER" <<<'local.oss'` を打ち、stdout がちょうど `true` / `false` でなければ die する。die の前に、読んだ stdout を stderr に出す（セルフレビューで足した）。
    - `sg_descriptions_in_code "$oss"` に渡す。
  - `sg_descriptions_in_code <0|1>`:
    - 引数を検査する。
    - `oss.auto.tfvars` の grep を消した。
    - awk に `oss_replaced` の行の規則を足した（完全一致。`=` の前の空白は 1 つ以上。ちょうど 1 行。無ければ「行が無い」、2 行以上なら「行が N 行ある」）。NG は workload の NG に続けて出す。
- `IaC/terraform/aws-managed/base/core/oss.tf`: コメント 2 行（`local.oss` と `oss_replaced` の上）。
- `tests/test_workflow.py`
  - `_csd_run`:
    - 偽 `tf` に console を足した（stdin を `$D/console-stdin` に保存。`console=(rc, stdout, stderr)` で差し替え）。
    - `real=True` で本物の `terraform console` を打てるようにした（`*.tf` と lock をコピーし、`.terraform` はリンク。AWS の認証情報は外す）。
    - `OWNER=tester` を入れた。`TF_CLI_ARGS*` / `TF_VAR_*` は環境から外し、`env=` で渡したものだけ入れる。戻り値は 5 つにした。
    - `oss.auto.tfvars` は書かない。
  - `_sdc_tmp(…, arg=None)`: 引数で渡す。tfvars は書かない。
  - 既存の check の calls の期待を `_CSD_CALLS` / `_CSD_CON` にした。
  - 049 T-1〜T-8 と T-3 (g) を足した。
- `tests/test_oss_ops.py`
  - `FAKE_TF` に console を足した。stdin をログに残す。`local.oss` は `-chdir` に `/oss/` があれば `true`、無ければ `false`。ほかの式は `sys.exit("fake terraform: console unknown expr")`。
  - `_CSD_OSS_SH` に `OWNER=x` と、console の答えを足した。
  - 049 T-9 を 2 つ足した。
- `docs/architecture/core.md`、`docs/architecture/resources/vpc-perimeter.md`: 049 の一言。

### 設計からの逸脱

1. **T-3(f) の期待を反転した（設計の事実の誤り）。**
   - Terraform v1.16.0 では、`*.auto.tfvars` / `terraform.tfvars` が `TF_VAR_*` より強い（環境変数がいちばん弱い）。
   - そのため `TF_VAR_project=nwc-poc` + `oss.auto.tfvars` は OSS 版になり、マネージド版の state は spark で止まる（rc=1）。
   - design.md の背景の 3 つ目（「環境変数は tfvars より強い」）も同じ誤り。
   - 実装はこの順位に依存しない（Terraform に聞く）。確かめたもの（スクラッチパッドの prec.sh。base/core のコピーで、AWS の認証情報なし）:
     ```
     --- 既定
     false
     rc=0
     --- TF_VAR_project=nwc-oss
     true
     rc=0
     --- oss.auto.tfvars=nwc-oss + TF_VAR_project=nwc-poc
     true
     rc=0
     --- terraform.tfvars=nwc-oss + TF_VAR_project=nwc-poc
     true
     rc=0
     Terraform v1.16.0
     ```
2. **`sg_descriptions_in_code` の引数の NG の文言**は design と少し違う。`NG: sg_descriptions_in_code の引数（OSS 版なら 1、マネージド版なら 0）が無いか 0 / 1 でない（いま $# 個「$*」）`。T-7 は「引数」を見る。
3. **console の die の文言を変えた。**
   - `local.oss が読めない（terraform console の失敗か、stdout が true / false の 1 行でない。上の出力。Warning も stdout に出る）`。die の前に、読んだ stdout を stderr に出す。
   - 理由はセルフレビューの S-1。テストは「local.oss が読めない（terraform console の失敗」の前方を見る。
4. `oss.tf:11` の上のコメントは「`ops/up.sh` の `check_sg_descriptions` は…」とした。design の文言には `ops/up.sh の` が無い。セルフレビューの N-3。

### 変異（ops/up-common.sh を 1 つずつ書き換えて `tests/test_workflow.py` を走らせた）

check を落ちても続けるように書き換えたコピー（`tests/_mut_test_workflow.py`。終わったら消した）で、落ちた check を全部数えた。各変異の後に `ops/up-common.sh` は元に戻した（`git diff --stat` で確認）。

```
== M1 console を打たず oss.auto.tfvars の grep に戻す: rc=0 FAIL 15
  （既存の check 7 本と）049 T-1 / T-2 / T-3 / T-3 (g) / T-4 / T-6 ×2 / T-8
== M2 console の出力の検査を外す: rc=0 FAIL 2
  049 T-3 (g) / 049 T-4
== M3 TF_CLI_ARGS* の die を消す: rc=0 FAIL 1
  049 T-5
== M4 oss_replaced の規則を消す: rc=0 FAIL 2
  049 T-6（完全一致でなければ止める）/ 049 T-6（揃え・コメント行は通る、2 行は止める）
== M5 console を show の後に打つ: rc=0 FAIL 24
  （既存の check 16 本と）049 T-1 / T-2 / T-3 / T-3 (g) / T-4 / T-6 ×2 / T-8
== M6 oss_replaced の完全一致を前方一致にする: rc=0 FAIL 1
  049 T-6（完全一致でなければ止める）
== M7 読んだ console の stdout を stderr に出さない（セルフレビューで足した）: rc=0 FAIL 2
  049 T-3 (g) / 049 T-4
```

`tests/test_oss_ops.py` では M1 で `049 T-9 up.sh（通し）` が落ちる（AssertionError: 049 T-9 up.sh（通し）: check_sg_descriptions が terraform console を IaC/terraform/oss/base/core でちょうど 1 回打ち…）。

### 検証（最後の編集の後に取り直した）

| # | 結果 |
| :--- | :--- |
| T-1〜T-8 | `tests/test_workflow.py` の `ok 049 T-1`〜`ok 049 T-8`。T-3 は本物の terraform console で走った（skip の行なし） |
| T-9 | `tests/test_oss_ops.py` の `ok 049 T-9` ×2。`_CSD_OSS_SH` の既存の check も ok |
| 変異 M1〜M6（と M7） | 上の表 |
| 全テスト 16 本 | 下 |
| `bash -n`（1 本ずつ） | 下 |
| `ops/check.sh` | 下（`terraform fmt -check` は「差分なし」） |

```
tests/test_agentcore.py rc=0 | 通過 168 / 失敗 0
tests/test_alerts.py rc=0 | 通過 168 / 失敗 0
tests/test_analytics.py rc=0 | 通過 549 / 失敗 0
tests/test_collectors.py rc=0 | 通過 79 / 失敗 0
tests/test_dashboard_config.py rc=0 | 通過 3 / 失敗 0
tests/test_graph.py rc=0 | 通過 78 / 失敗 0
tests/test_kb_index.py rc=0 | 通過 7 / 失敗 0
tests/test_lab_debug.py rc=0 | 通過 121 / 失敗 0
tests/test_local_compose.py rc=0 | 通過 145 / 失敗 0
tests/test_nautobot.py rc=0 | 68 項目すべて通過
tests/test_oss.py rc=0 | 通過 177 / 失敗 0
tests/test_oss_ops.py rc=0 | 通過 211 / 失敗 0
tests/test_oss_roll.py rc=0 | 通過 66 / 失敗 0
tests/test_stream.py rc=0 | 通過 119 / 失敗 0
tests/test_sync.py rc=0 | 通過 104 / 失敗 0
tests/test_workflow.py rc=0 | 通過 507 / 失敗 0

bash -n ops/up-common.sh rc=0
bash -n ops/up.sh rc=0
bash -n ops/oss/up.sh rc=0

ops/check.sh:
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（20 行とも OK）
== 3. スクリプトの構文
bash -n: 33 本
構文エラーなし
== 4. 模擬テスト
（16 本とも 失敗 0）
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
```

### セルフレビュー

**体制**
- 自分: opus-5.5 / effort high（サブエージェントの中なので effort は切り替えられない）。`/robust` で回した。
- 反対弁護人: opus / effort xhigh。文脈を渡し、読み取り専用で頼んだ。終わった後、worktree の `git status --porcelain -uall` は自分の 7 件のままだった。

**集計**: Must fix 0 / Should fix 1（直した。ただし設計側の残りを差し戻し事項として報告） / Nit 4

#### S-1（Should fix、直した）[runtime bugs]

**場所**: `ops/up-common.sh:143-147`

**破綻シナリオ**
- 本物の `terraform console` は Warning を **stdout** に出す。stderr ではない。
- 例:
  - base/core の `terraform.tfvars` に宣言の無い変数がある。
  - provider で非推奨の属性を参照している。
- このとき stdout が `false` の 1 行でなくなり、守りは「local.oss が読めない…上のエラー」で止まる。ところが Warning は `$mode` に吸われ、何も表示されない。
- design の T-4 は「Warning が stderr に出て rc=0 なら通る」前提で、本物とは違う。design.md:36-37 の `TF_VAR_project=bogus` の観察も本物と違う（反対弁護人の実験 K: rc=1、stdout に Warning の枠だけ）。

**確かめたもの**（スクラッチパッドの warn.sh。base/core のコピーに `terraform.tfvars` で `undeclared_x = 1`）
```
--- 宣言の無い変数（terraform.tfvars）
（stdout に色付きの「Warning: Value for undeclared variable」の枠、その後に）
false
rc=0
--- stderr:
（空）
```

**直した内容**
- die の前に、読んだ stdout を stderr に出す。
- die の文言に「Warning も stdout に出る」を足した。
- T-3 (g)（本物の console、宣言の無い変数）を足した。
- T-4 に「stdout に Warning + false」を足した。
- 変異 M7（出さない）で T-3 (g) と T-4 が落ちることを確かめた。

**残り（差し戻し事項。PM の判断）**
- 守りは止まる側のまま。049 より前は通っていた up.sh が、base/core に Warning があると手順 0 の後で止まる。
- apply は `-compact-warnings` で流して進む。
- 起きる経路:
  - `terraform.tfvars` に古い変数（agent へ移した変数など）が残っている。
  - provider を更新して非推奨の属性が出た（反対弁護人の実験 B: `data.aws_region.*.name` で「Deprecated value used」が stdout）。
- 直し方の案:
  - 案 1: `-no-color` を付け、式を `"local.oss=${local.oss}"` にして、その完全一致の行がちょうど 1 つあれば受ける。反対弁護人の実験 H・I で、console でも `-no-color` が効き、最後の行が `"local.oss=false"` になることを確認した。
  - 案 2: 止める挙動のまま、design の方針 1 と T-4 を直し、troubleshooting に手順を足す。
- design.md の方針 1（stdout がちょうど true / false）と T-4（Warning なら通る）が本物では両立しないので、実装で吸収せず差し戻す。
- いまのコードでは Warning は出ない。本物の T-3 (a) が rc=0 で通る。メインのチェックアウトの base/core にも `terraform.tfvars` は無い（`ls` で確認）。なので、いまの運用は止まらない。

#### N-1（Nit）[docs]

**場所**: `design.md:10`（背景の 3 つ目）と T-3(f)

- 変数の優先順位が逆に書いてある（逸脱 1）。
- 背景に挙げた「`TF_VAR_project=nwc-poc` + `oss.auto.tfvars` で守りと Terraform がずれる」経路は、048 の grep でも起きていなかった。ほかの経路（既定値・terraform.tfvars・TF_VAR_project=nwc-oss・`local.oss` の書き換え）は本物の T-3 (b)(c)(d) が縛る。
- 直さない（design は PM の担当）。

#### N-2（Nit）[守りの穴]

**場所**: `ops/up-common.sh:74-77`

**破綻シナリオ**
1. `oss.tf` の `oss_replaced` の行を `/* */` で囲む。
2. 本当の定義を `locals.tf` に移す。
3. 行の形の守りを通り抜ける（反対弁護人の実験 4: `sg_descriptions_in_code 1` の出力は基準と cmp で一致。Terraform では web が外れる）。

- 意図した書き換えでしか起きない。048 の merge / description の行の守りも同じ手で外れる。
- 行単位の守りの限界。広い案（QUEUE の候補）の担当。

#### N-3（Nit）[docs]

**場所**: `IaC/terraform/aws-managed/base/core/oss.tf:11,15`

- コメントは「`ops/up.sh` の `check_sg_descriptions`」と書いている。関数は `ops/up-common.sh` にあり、`ops/oss/up.sh` からも呼ばれる。
- 15 行目は design の文言のまま（048 の merge のコメントと同じ形）。

#### N-4（Nit）[過剰な停止]

**場所**: `ops/up-common.sh:138-141`

- `TF_CLI_ARGS_init` のような、版の判定に関係しない名前や空の値でも止まる。
- design の「未確定事項」3 で受け入れ済み。

#### 問題なしとした観点と根拠

- **本物の state がある場所での console**
  - 反対弁護人がメインのチェックアウトの `aws-managed/base/core` と `oss/base/core`（シンボリックリンクの `.tf` と、実ファイルの `oss.auto.tfvars`）で打った。
  - 認証情報なしで rc=0。stdout はちょうど `false\n` / `true\n`。ファイルは増えない（lock も残らない）。
- **apply 中の lock**: `.terraform.tfstate.lock.info` がある状態で console は rc=0 `false`（warn.sh）。
- **壊れた state**: console は rc=1 で止まる（warn.sh。守りは die）。
- **`set -euo pipefail` と `$()` と `||`**（読んだだけ。反対弁護人も同じ結論）
  - `local` を先に宣言しているので rc は保たれる。
  - 失敗時も `mode` に代入され、`${mode:-}` で `set -u` に当たらない。
  - die は `{ }` の中で、サブシェルではない。
- **console と apply で var.project がずれる経路**: apply も同じ `tf()` を通る。`-var project` / `-var-file` は ops に無い（T-8 と反対弁護人の grep）。
- **`sg_descriptions_in_code` の呼び出し元**: `check_sg_descriptions` だけ（反対弁護人の grep）。
- **`TF_VAR_kafka_task_memory` のような宣言の無い環境変数**: Warning は出ず、`false` だけ（反対弁護人の実験 F）。過去の運用は止まらない。
