# review: `check_sg_descriptions` に Terraform の書き方の守りを足す（048）

## Round 1

レビュー: PM(fable-5-1) / effort: high。cold reviewer: opus / effort xhigh（初回ビルド直後なので依頼した）。対象 `2bef299..bd7f6e3`（`ops/up-common.sh`、`tests/test_workflow.py`、docs 2 本）。

### PM の確認

- 全テスト 16 本を取り直し（`uv run -q --group dev --group web python tests/test_*.py`）: 全部失敗 0（`test_workflow.py` 通過 490 / 失敗 0、`test_oss_ops.py` 通過 209 / 失敗 0）。`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` rc=0
- エンジニアのセルフレビューの Should fix 1（`oss.tf:76-79` の `local.workload_security_groups = merge(…)` を書き換えても通る）を一時コピーで再現（`sg_descriptions_in_code` を実物の 2 本のコピーで、1 行目を `k => "pre ${v}"` に）: マネージド版 `rc=0` で 17 行、OSS 版（`oss.auto.tfvars` あり）`rc=0` で 22 行、出力は実物と同じ。**分類: correctness の穴（Must と同じ扱いで直す）。** 根本原因は design.md の事実の誤り（`local.workload_security_groups` を `security_groups.tf:142-145` と書いたが、実物は `oss.tf:76-79`）と、そこから来た方針 6（`oss.tf` を見ない）。Round 2 で design.md を直し、`oss.tf` をマネージド版でも常に読んで merge のブロックの 2 行を見る
- 逸脱 1（workload の `description` の `=` の前の空白を 1 つ以上許す）: 妥当（terraform fmt の揃え）。design.md の方針 2・実装ステップ 1 を Round 2 で正本に揃える
- cold review の Should fix 2 件は上の 2 件と同じ指摘。Nit 1（endpoints のタブ字下げの `count` は通る）は残す（build.md の Nit 2 と同じ。`ops/check.sh` の `terraform fmt -check` で捕まり、字下げを緩めると入れ子の `dynamic` を誤検知する）。Nit 2（workload の NG に理由が無い）は残す（方針 7 の文言のまま）。Nit 3（`security_groups.tf` の workload の resource に up.sh が見る旨のコメントが無い）は Round 2 の変更対象に足す
- 見た観点: design 整合性 / correctness / missing tests（自分で再現・テスト取り直し）。security / data loss / API compatibility は cold reviewer の記録に依る。runtime は AWS では動かさない（方針 8）

### cold review r01

## サマリ

`git diff 2bef299 bd7f6e3` の 4 ファイルをレビューした（`ops/up-common.sh` +23/-3 行、`tests/test_workflow.py` +71 行、`docs/architecture/core.md` と `docs/architecture/resources/vpc-perimeter.md` は 1 行ずつ。`build.md` は記録なので中身の正しさだけ参照）。

実装は design.md の方針 1〜5・7 のとおりで、変更は `sg_descriptions_in_code` の awk とヘッダのコメント 1 行に閉じている。`check_sg_descriptions` の本文・die の文言、`sg_descriptions_in_state` は diff に出てこない（変わっていない）。T-1〜T-8 は全部通り、変異を入れると落ちる。Must fix は無い。

残っている問題は 2 つ。1 つ目は、このサイクルが塞ごうとした「表の値が description になる経路」のうち、`oss.tf` の `local.workload_security_groups` の内包表記がまだ守られていないこと。これは design.md の事実の誤り（`workload_security_groups` の場所）から来ている。2 つ目は、description の `=` の前の空白を緩めた逸脱が design.md（正本）に反映されていないこと。

### 見た観点 / 見ていない観点

見た観点

- design.md との整合性
  - design.md を全部読み、方針 1〜8・変更対象・T-1〜T-8・M1〜M5 と diff を 1 つずつ照らした
  - `check_sg_descriptions` / `sg_descriptions_in_state` が変わっていないことは diff の hunk の範囲で確かめた
- correctness
  - `sg_descriptions_in_code` を実物の `security_groups.tf` / `oss.tf` を書き換えた一時コピー（`tempfile.TemporaryDirectory`）で打った。awk は `/usr/bin/awk`（macOS の BSD awk）
  - 打った入力: oss.tf の内包表記の書き換え 2 種、CRLF、endpoints にタブ字下げの `count`、`=` を揃えた `count`、workload に `lifecycle {}` の入れ子、description の隣に属性、endpoints に `provider`、`} # workload`、endpoints に 4 空白の入れ子の `dynamic` の `for_each`、OSS 版の土台
- missing tests
  - M2（description の前方一致）、M4（endpoints の count 規則を消す）、M5（コメント行も規則に掛ける）、MX（「endpoints[0]」の一言を消す）の 4 つを、関数の本文を文字列で置き換えて打った
  - T-4 / T-6 / T-7 の入力で、どれも rc か「endpoints[0]」の有無が変わる（= 対応する check が落ちる）ことを確かめた
- テスト
  - `uv run -q --group dev --group web python tests/test_workflow.py` → `通過 490 / 失敗 0`（048 の 11 件はすべて ok）
  - `uv run -q --group dev --group web python tests/test_oss_ops.py` → `通過 209 / 失敗 0`（本物の `check_sg_descriptions` の 2 件を含む）
  - `bash -n` を `ops/up-common.sh` / `ops/up.sh` / `ops/oss/up.sh` に 1 本ずつ打って、3 本とも rc=0
- runtime bugs / data loss
  - 呼び出し元は `ops/up.sh:609` と `ops/oss/up.sh:163` の 2 か所だけ（grep）。どちらも何かを作る前に止まる経路で、down.sh からは呼ばれない
  - 新しい `exit 1` の経路が消す操作を止めることは無い
- API compatibility
  - `sg_descriptions_in_code` の出力形式（`<キー>\t<文言>` と最後の endpoints）は変わっていない。増えたのは stderr の NG と `exit 1` の経路だけ

見ていない観点

- `ops/check.sh` は打っていない（terraform の init / validate が手元に作業ディレクトリを作るため）。fmt / validate の通過は build.md の記録だけが根拠
- `tests/` のほかのテストファイルは打っていない（`test_workflow.py` と `test_oss_ops.py` だけ）
- gawk / mawk は手元に無い（`which` で見つからない）。BSD awk でしか確かめていない（design.md の未確定事項 3 と同じ範囲）
- AWS では動かしていない（design.md の方針 8）。oss.tf の書き換えで本当に SG の作り直しになるかは terraform plan を打っておらず、HCL を読んだだけ
- security は対象がほぼ無い（外部入力は無く、入力はリポジトリ内の .tf だけ）。stderr に出るのは .tf の行とパスだけで、機密は出ない

## Must fix

None

## Should fix

- [design.md との整合性 + correctness] `IaC/terraform/aws-managed/base/core/oss.tf:76-79`（`local.workload_security_groups = merge(...)`）が守りの外に残っている。design.md の事実の誤りが原因
  - **design.md の誤り**: 「現物で確認した事実」は `local.workload_security_groups`（`security_groups.tf:142-145`）と書いているが、実物の `security_groups.tf:138-147` は `sg_rules`。`workload_security_groups` の定義は `oss.tf:76-79` にある
  - **方針 6 の見落とし**: 方針 6 は「`oss.tf` に workload / endpoints の resource は無いので見ない」としていて、表 → `workload_security_groups` → `for_each` → `each.value` の経路の 1 段目を見落としている
  - **破綻シナリオ**: `oss.tf:77` を `k => "pre ${v}"` に書き換える。マネージド版でも oss.tf はモジュールの一部として読まれる。このため apply すると workload の SG 全部の description が変わり、全部作り直しになる（そうなる理由は HCL を読んだだけ。plan は打っていない）。それなのに `sg_descriptions_in_code` は書き換え前と同じ出力で rc=0 を返す。そのため `check_sg_descriptions` も今と同じく通す。キーを `"x_${k}" => v` に変えた場合も同じで、background の 4 つ目（キーが合わず全部飛ばして通る）と同じ穴になる
  - **守るときに要ること**: マネージド版では awk に `oss.tf` を渡していない（`ops/up-common.sh:34` で OSS 版のときだけ `oss` を立てる）。守るなら、両方の版で oss.tf を読む形に変える必要がある
  - **Should にした理由**: このサイクルの前より守りが弱くなったわけではない（マージしても今より悪くならない）。ただ、このサイクルの目的「表が description になる経路を見る」と同じ種類の穴が 1 段残っていて、その原因が design.md の誤った事実にある。直すなら design.md の事実と方針 6 から（このサイクルに足すか QUEUE に回すかは PM の判断）
  - **再現**: リポジトリ直下で、実物の 2 ファイルを一時ディレクトリへコピーし、oss.tf の 1 行を置き換えて `sg_descriptions_in_code` を打った（マネージド版。oss.auto.tfvars なし）

    ```
    置き換え: { for k, v in local.security_groups : k => v if !contains(local.oss_replaced, k) },
          →   { for k, v in local.security_groups : k => "pre ${v}" if !contains(local.oss_replaced, k) },
    base rc 0 mutated rc 0 stdout identical True stderr ''
    P1b oss.tf x_${k} => v (managed): 0 same stdout: True ''
    ```

- [design.md との整合性] `ops/up-common.sh:46` の `^  description += each\.value$` と design.md の方針 2・実装ステップ 1 が食い違う
  - **食い違い**: design.md は「ちょうど `  description = each.value`」（完全一致）と書いている。実装は `=` の前の空白を 1 つ以上許している
  - **逸脱自体の扱い**: build.md の「設計からの逸脱 1」に理由付きで書かれている。理由は、terraform fmt が `=` を揃えるので、隣に長い属性を足すと完全一致では正しいコードで止まること。逸脱自体は妥当で、広がるのは空白の数だけ。式・行末のコメント・タブは引き続き NG（T-4 で確かめた）
  - **Should にした理由**: 正本の design.md が実装と違ったまま残る。次のサイクルで design.md を根拠に「完全一致に戻す」直しが入ると、正しいコードで up.sh が止まるようになる。直すのは design.md の方針 2・実装ステップ 1（と T 表に「揃え」の check があること）の文言だけで、コードは変えなくてよい
  - **再現**: `uv run -q --group dev --group web python tests/test_workflow.py` の該当行

    ```
    ok check_sg_descriptions: workload の description / for_each の = の前の空白が揃えで増えても通る（cycle 048。いま: rc=0 ''）
    ```

    design.md 側は読んだだけ（方針 2「ちょうど `  description = each.value` の行があること」、実装ステップ 1「`  description = each.value` の完全一致で `wdesc = 1`」）

## Nit

- [correctness（fmt 前提）] `ops/up-common.sh:58`。endpoints のブロックに、タブで字下げした `count = 1` を足すと通る
  - **前提との関係**: `ops/up.sh` / `ops/oss/up.sh` は terraform fmt を打たない（grep で `fmt` は `ops/check.sh` だけ）。この穴が塞がるのは、`ops/check.sh` を通してから up.sh を打つ運用のときだけ
  - **Nit にした理由**: タブで字下げした HCL は fmt で必ず直され、このリポジトリの運用では入りにくい。build.md のセルフレビューの Nit 2 と同じで、字下げを緩めると入れ子の `dynamic` を誤検知するので、今の形でよい
  - **再現**: 一時コピーで `'resource "aws_security_group" "endpoints" {\n\tcount = 1\n'` に置き換えた → `P4 tab count in endpoints: 0 same: True`

- [保守性] `ops/up-common.sh:85` の workload の NG と、`ops/up-common.sh:114` の die の組み合わせが、守りを緩める方向に読める
  - **何が起きるか**: workload の NG は「書き方が読めない」と行を出すだけで、each.value 以外にすると全部作り直しになる、という理由が無い。続く die は「`ops/up-common.sh` の `sg_descriptions_in_code` を直す」と案内する。わざと description の式を変えた人は、awk を緩める方へ進みかねない
  - **Nit にした理由**: 止まる側に倒れているので壊れない。design.md の方針 7 が文言をこの形に決めている（セルフレビューの Nit 3 と同じ）
  - **再現**: 一時コピーで `  description = "pre ${each.value}"` に置き換えて打った stderr（パスは伏せた）

    ```
    NG: aws_security_group.workload の書き方が読めない:
      <tmp>/base/core/security_groups.tf:   description = "pre ${each.value}"
    ```

- [保守性] `IaC/terraform/aws-managed/base/core/security_groups.tf:154` の `resource "aws_security_group" "workload"` に、`for_each` / `description` の 2 行の形を up.sh が見ている、というコメントが無い
  - **比べた先**: endpoints には `:189-190` に「ops/up.sh の check_sg_descriptions が見る（046）」がある
  - **Nit にした理由**: 動作には関係しない。design.md の変更対象にも入っていない。読んだだけ

## 良かった点

- 新しい守りを既存の「読めない → NG → `exit 1` → die（`terraform show` の前）」の経路に乗せている。`check_sg_descriptions` と `sg_descriptions_in_state` には手を入れていない（方針 1 のとおり）。T-2 / T-5 / T-6 で `tf base/core show -no-color` が呼ばれていないことまで確かめている
- endpoints の「description の行が無い」の条件を、`epbad == ""` から `!epdbad` に変えている。count の行と description の欠落が同時にあっても両方出るようになり、それを確かめる check もある
- コメント行（`  # …`）と、ほかのブロック（`endpoints_none`、`flow` のルール、4 空白の入れ子の `dynamic`）を誤検知しない。T-7 / T-8 と、私が打った P5 / P9 でも rc=0
- 変異を設計の 5 つに加えて MA〜MD まで当てて、テストが変異を捕まえることを確かめている。私が別に当てた MX（「endpoints[0]」の一言を消す）も、T-6 の条件で落ちる
- docs の 2 か所は、oss.tf まで守るとは読めない書き方になっていて、守りの範囲を言い過ぎていない

## ユーザーへの質問

- `oss.tf:76-79` の `local.workload_security_groups` の内包表記（Should fix の 1 つ目）を、このサイクルの守りに足すか、QUEUE に回すか。足すなら、マネージド版でも awk に oss.tf を渡す形に変わる（`ops/up-common.sh:34` の `oss` の扱い）

## Round 2

レビュー: PM(fable-5-1) / effort: high。cold reviewer: opus / effort xhigh（完了判定の直前で、実装が `ops/up-common.sh` ほかで変わっているので依頼した。1 サイクル 2 回目）。対象 `06a4cc7..7cc7da1`（`ops/up-common.sh`、`tests/test_workflow.py`、`tests/test_oss_ops.py`、`.tf` 2 本のコメント、docs 2 本）。

### PM の確認

- Round 1 の Should fix 1（`oss.tf:76-79` の merge の 1 行目を `k => "pre ${v}"` にしても通る）を同じ手順（`repro-oss.sh`）で取り直した。いまは止まる:

```
managed rc=1 lines=3
NG: aws_security_group.workload の書き方が読めない:
  …/base/core/oss.tf:     { for k, v in local.security_groups : k => "pre ${v}" if !contains(local.oss_replaced, k) },
  …/base/core/oss.tf:     { for k, v in local.oss_security_groups : k => v if local.oss },
oss rc=1 lines=3
```

- 全テスト 16 本を取り直し: 全部失敗 0（`test_workflow.py` 通過 497 / 失敗 0、`test_oss_ops.py` 通過 209 / 失敗 0）。`bash -n`（up-common.sh / up.sh / oss/up.sh）rc=0。`./ops/check.sh` は「すべて通過」rc=0（fmt、20 ルートの validate、構文、模擬テスト、旧名）
- エンジニアの逸脱 1（`tests/test_oss_ops.py` の偽の REPO に `oss.tf` を足した）: `oss.tf` を必須にした方針 6 の帰結で妥当。既存の検査の意味は変わらない（cold reviewer も同じ判断）
- cold review の Nit 1 = エンジニアの Nit 1（`oss.tf:11` の `local.oss` の定義を `oss = true` にすると通る）: PM が再現（`repro-nit1.sh`。マネージド版 `rc=0` 17 行、出力は基準と同じ）。**このサイクルでは直さず QUEUE に回す。** 理由: 版の切り替えそのものを書き換える経路で、`local.oss` の行だけ守っても `var.project` の既定値（`variables.tf:23`）、ほかの tfvars、`TF_VAR_project` の環境変数で守りの判定（`oss.auto.tfvars`）と Terraform の判定がずれる（読んだだけ。Terraform の変数の優先順）。まとめて見る別サイクルにする。design.md:125 の「3 段とも閉じる」は言い過ぎなので文言を直した
- cold review の Nit 3（workload の SG の `name` の式も作り直しの原因）: 設計の外（description の経路に絞っている）。QUEUE に回す
- cold review の Nit 2（`/* */` の中の偽物の merge）: 残す。行で読む方式の前提で 043 の表の読み方と同じ限界。リポジトリの `.tf` に `/* */` のコメントは無い（cold reviewer の grep）
- cold review の Nit 4 = エンジニアの Nit 3（原因が `oss.tf` でも die は `security_groups.tf` と案内）: 残す（方針 1・7 で die は変えない。直前の NG の行で場所は分かる）
- cold review の Nit 5 = エンジニアの Nit 2（出口が `  )` ちょうどでないと関係ない行まで NG に並ぶ）: 残す（止まる側に倒れる）
- 見た観点: design 整合性 / correctness / missing tests（自分で再現・テスト取り直し・check.sh）。security / data loss / API compatibility は cold reviewer の記録に依る。runtime は AWS では動かさない（方針 8）
- 未解消の Must fix: 0。Should fix: 0。サイクル完了

### cold review r02

## サマリ

対象は `06a4cc7..7cc7da1`（1 commit。ブランチ `feat/161-sg-guard-code-shape`）。design.md の Round 2 の実装ステップ 4〜8 を見た。差分は 8 ファイル +247 / -18（`git diff --stat`）で、うち 152 行は `build.md` の追記。レビュー対象のコードは次のとおり。

- `ops/up-common.sh`: +22 / -8
- `tests/test_workflow.py`: 69 行（ほぼ追加）
- `tests/test_oss_ops.py`: +5 / -3
- `.tf` 2 本と docs 2 本: 1 行ずつ

全体として設計どおり。方針 6・7・9 はどれもコードに入っている。

- 方針 6: `oss.tf` を常に渡し、OSS 版は `-v oss` で判定する。`oss.tf` が無ければ awk の前に NG を出して `return 1` する。
- 方針 9: merge のブロックの 2 行を、順番どおり完全一致で見る。コメント行と空行は飛ばし、ほかの行と見出しの欠けは NG にする。
- 方針 7: workload の NG にまとめ、ブロックの行のあとに merge の行を出す。

T-9〜T-13 も検証表どおり。全テストを自分で取り直して失敗 0。設計の変異 M6〜M8 と、自分で足した変異 7 件は、全部テストで落ちた。

守りが通ってしまう書き方は 2 つ再現した。

- `local.oss` の定義を変える
- `/* */` の中に偽物の merge を置く

どちらも設計の範囲（3 段の経路）の外で、エンジニアのセルフレビューも同じものを挙げている。Must fix と Should fix は無し。

### 見た観点 / 見ていない観点

見た観点:

- **design.md との整合性**
  - 方針 6・7・9 と実装ステップ 4〜8 を `ops/up-common.sh:29-105` と突き合わせた。
  - 変更対象のファイル（up-common.sh、test_workflow.py、docs 2 本、`.tf` のコメント 2 行）を確かめた。
  - 設計に無い変更は `tests/test_oss_ops.py:536-540`（偽の REPO に `oss.tf` を足した）だけ。`oss.tf` を必須にしたので必要になった変更で、`build.md` の逸脱 1 に書いてある。
  - `_sdc_tmp(sg_tf, oss_tf=None, oss=None)`（`tests/test_workflow.py:1702`）と `_csd_run(…, remove=())`（同 1516）は、引数を足しただけ。既存の呼び出しは同じ意味のまま（`oss` の既定は `oss_tf is not None`、同 1705-1706）。
- **correctness**
  - awk の merge の規則を読んだ。
    - 入口は `^  workload_security_groups += merge\($`、出口は `$0 == "  )"`。
    - `m1` / `m2` で順番を見る状態機械と、END の `mblk` / `m1 && m2` の処理も見た。
  - そのうえで、手で壊した入力を一時ディレクトリで `sg_descriptions_in_code` に当てた（scratchpad の `r048_probe.py`。中身は下の Nit 1・2・5）。
  - 実物はマネージド版が rc=0 で 17 行、OSS 版が rc=0 で 22 行を出す。
- **missing tests（変異で確かめた）**
  - scratchpad の `r048_mut.py` を使った。`ops/up-common.sh` 自体は書き換えず、`builtins.open` を差し替えて、`tests/test_workflow.py` に変異した文面を読ませた。
  - 変異なしは `通過 497 / 失敗 0`。
  - 設計の変異:
    - M6（merge の規則を全部消す）は T-9 で落ちる。
    - M7（`ARGC > 2` に戻す）は、046 の「全キー同じなら通る」の check で先に落ちる（spark の不一致）。
    - M8（順を見ない）は T-10 で落ちる。
  - 自分で足した変異（どれも落ちた）:
    - shell の `[ -f oss.tf ]` を消す: T-11 で落ちる。
    - `#` を含む行を全部飛ばす: T-10 で落ちる。
    - 出口を前方一致にする: T-10 で落ちる。
    - `wbad = wbad mbad` を消す: T-9 で落ちる。
    - 「2 行が無い」の NG を消す: T-10 で落ちる。
    - 見出しの `$` を外す: T-10 で落ちる。
    - shell の `oss` を常に 1 にする: 046 の check で落ちる。
- **テストの実行**
  - `tests/test_*.py` の 16 本を、`.venv/bin/python` で 1 本ずつ打った。
  - 結果は test_workflow が 497 / 0、test_oss_ops が 209 / 0、test_nautobot が「68 項目すべて通過」。ほかの 13 本も全部失敗 0。
- **構文と整形**
  - `bash -n` を `ops/up-common.sh`、`ops/up.sh`、`ops/oss/up.sh` に打った。どれも rc=0。
  - `terraform fmt -check -diff` を `oss.tf` と `security_groups.tf` に打った。rc=0 で差分なし。
- **API compatibility**
  - `sg_descriptions_in_code` を呼ぶのは `check_sg_descriptions` だけ（grep で確かめた）。出力の形は変わっていない。
  - 新しく `return 1` になる経路（`oss.tf` が無い）は、既存の die に乗る。
  - テストのうち `check_sg_descriptions` を通しで打つのは、test_workflow と test_oss_ops だけ（grep で確かめた）。
- **security**
  - awk に入るのは、`-v oss=0/1` とリポジトリの中のファイルのパスだけ。外から入力は来ない。シークレットにも触れない。
- **data loss**
  - 変更は守りを止める側に倒すものだけ。消す処理も上書きする処理も足していない。

見ていない観点:

- `ops/check.sh` の 1〜2（20 ルートの `terraform validate`）と 5 は打っていない。`build.md` の生ログを読んだだけ。
- gawk / mawk での `-v oss=0` の扱いは見ていない。手元は macOS の awk だけ（design.md の未確定事項 3）。
- AWS の実物の state では見ていない（設計の方針 8 で対象外）。
  - Nit 1 の「apply で spark の SG が作り直しになる」は、2 つの表の文言の差から読んだだけ。`terraform plan` は打っていない。
- Terraform の override ファイル（`override.tf` / `*_override.tf`）で local や resource を上書きする経路は、再現していない。
  - 守りは `security_groups.tf` と `oss.tf` の 2 本しか読まないので、その経路は原理的に見えない。
  - 043 からの前提なので、指摘にはしない。
- type safety は対象外（シェルと awk）。

## Must fix

None

## Should fix

None

## Nit

- **[correctness / design.md との整合性] `oss.tf:11` の `local.oss` の定義を変えると、守りが通る（design.md:125 の「経路の守りはこれで 3 段とも閉じる」は言い過ぎ）**
  - 守りが文字列として見ているのは、merge の 2 行目の `if local.oss` と、`oss_replaced = local.oss ? ["msk"] : []` の 2 か所。`local.oss` の値を決める行（`  oss = var.project == "nwc-oss"`）は、守りの外にある。
  - マネージド版でこの行を `  oss = true` にした場合:
    - Terraform 側: workload の SG の spark の description が変わる。`"EMR Serverless workers (…)"`（`security_groups.tf:36`）から `"Spark ECS task, local mode (…)"`（`oss.tf` の `oss_security_groups`）になる。つまり作り直しになり、`DependencyViolation` に当たる。
    - 守り側: shell の `oss=0` のまま `security_groups.tf` の表を出すので、state と一致して通る。
  - OSS 版でこの行を `false` にした場合も、逆向きに同じことが起きる。
  - Nit にした理由: 版の切り替えそのものを書き換える操作で、偶然に起きることはまず無い。`var.project` から変えると `name_prefix` も変わって全部が作り直しになり、description の守りの出番ではない。エンジニアの build.md の Nit 1 と同じ件。
  - 直し方の候補は 2 つ。
    - awk で `  oss = var.project == "nwc-oss"` と `  oss_replaced = local.oss ? ["msk"] : []` の完全一致を見る。規則 2 行で済む。
    - 直さないなら、design.md の未確定事項 6 の文を「3 段（`local.oss` の定義を除く）」に直す。
  - 再現: scratchpad の `r048_probe.py` で、`oss.tf` の `  oss = var.project == "nwc-oss"` を置き換えて `sg_descriptions_in_code` を打った。
    ```
    base managed rc 0 lines 17 | base oss rc 0 lines 22
    P1 local.oss flip (managed): rc=0 stdout_same_as_real=True stderr_lines=0 first=[]
    P1b local.oss flip (oss ed): rc=0 stdout_same_as_real=True stderr_lines=0 first=[]
    ```

- **[correctness] `/* */` の中に元の merge のブロックを残し、その下に本物の定義を別の形で書くと、守りが通る**
  - 例えば次のように書く。
    ```
    /*
    <元の merge のブロック>
    */
    workload_security_groups = { for k, v in local.security_groups : k => "pre ${v}" }
    ```
  - awk は、コメントの中の見出しと 2 行で `mblk` / `m1` / `m2` を立てる。本物の行は見出しの正規表現に当たらないので、見ない。
  - 結果として、方針 9 の「merge( 以外にした → NG」がこの形では効かない。
  - Nit にした理由: わざとの細工に近い。
    - リポジトリの `.tf` には `/* */` のコメントが 1 つも無い。`grep -rn --include='*.tf' '/\*' IaC` で当たるのは、ARN や S3 の prefix の文字列（`…/*"`）とコメントの中の `parameter/*/` だけ。
    - 043 からの表の読み方（`  security_groups = {` の塊）にも、同じ限界がある。
    - 行で読む方式に共通の前提で、エンジニアの「範囲外として残したもの」と同じ件。
  - 再現（`r048_probe.py`）:
    ```
    P2 block-comment decoy (managed): rc=0 stdout_same_as_real=True stderr_lines=0 first=[]
    P2b block-comment decoy (oss ed): rc=0 stdout_same_as_real=True stderr_lines=0 first=[]
    ```

- **[correctness / 範囲外] workload の SG の `name` の式も作り直しの原因になるが、この系統の守りは見ていない**
  - `aws_security_group` の `name` も、`description` と同じく、変えると作り直しになる（Forces new resource）。
  - 例えば `security_groups.tf:158` の `name        = "${local.name_prefix}-${replace(each.key, "_", "-")}"` から `replace` を外すと、`_` を含むキーの SG が作り直しになる。それでも `check_sg_descriptions` は description しか比べないので、守りは通る。
  - Nit にした理由: 048 の設計は description の経路に絞っているので、設計の外。同じ `DependencyViolation` の型なので、QUEUE の候補として書いておく。
  - 読んだだけ。再現手順:
    - `_csd_run(edits=[("security_groups.tf", 'replace(each.key, "_", "-")}"\n  description', 'each.key}"\n  description')])` を打ち、rc=0 になることを見る。
    - `tags` の行（162）にも同じ式があるので、158 行だけに当たる前の文字列を選んでいる。

- **[文言] `oss.tf` が無いときや merge の NG のときも、die は「security_groups.tf から SG の description が読めない（local.security_groups の形が変わったなら …）」と案内する**
  - 原因の場所は、直前の NG の行で分かる。ただ、die の括弧の中の案内が原因と食い違う。
  - Nit にした理由: die の文言は方針 1 と 7 で「変えない」と決めてあり、T-11 がこの文言を固定している。設計どおりの挙動で、読む人が少し迷うだけ。エンジニアの Nit 3 と同じ件。
  - 再現（`tests/test_workflow.py` の T-11 の「いま:」の出力）:
    ```
    rc=1 'NG: …/tf/base/core/oss.tf が無い\nDIE: …/tf/base/core/security_groups.tf から SG の description が読めない（local.security_groups の形が変わったなら ops/up-common.sh の sg_descriptions_in_code を直す）。まだ何も作っていない'
    ```

- **[runtime / 体裁] merge の出口が `  )` ちょうどでないと、関係ない行まで NG に並ぶ**
  - 出口の行に行末の空白や `# end` が付くと、`inm` が閉じない。その場合、次の `  )`（`active_sg_flows` の閉じ）までの行が NG に並ぶ。
  - 止まる側に倒れるので、黙って通ることは無い。行末の空白は `terraform fmt` が消すが、`# end` は残る。
  - Nit にした理由: 体裁だけの問題で、守りとしては正しく止まる。
  - 再現（`r048_probe.py`。出口を `  ) ` にした）:
    ```
    P4 closing paren trailing space: rc=1 stdout_same_as_real=False stderr_lines=5 first=['NG: aws_security_group.workload の書き方が読めない:', '  …/base/core/oss.tf:   ) ']
    ```

## 良かった点

- OSS 版の判定を `ARGC > 2` から `-v oss` に替えている。`oss.tf` を常に渡す変更に合わせたもの。
  - 引数の数に頼る判定を残すと、M7 のようにマネージド版でも spark が OSS の文言になる。変異を当てて、実際にそうなることを確かめた。
  - マネージド版で `oss_security_groups` の表を読まないことは、T-12 で押さえてある。
- merge の 2 行は、完全一致に加えて順番も見ている（`m1` / `m2`）。T-10 には、出口、見出しの `$`、行末に続く式、`#` を含む行まで入っている。完全一致を緩める変異は、どれもテストで落ちる。
- `oss.tf` が無いときは、awk のエラーに任せず、shell 側で名前の付いた NG を出している。T-11 がこの区別を見ている。
- 土台の check で 2 つを固定している。テストの前提が崩れたときにすぐ分かる。
  - 実物の merge のブロックが 2 行だけであること。
  - `security_groups.tf` に `workload_security_groups` の定義が無いこと。

## ユーザーへの質問

None
