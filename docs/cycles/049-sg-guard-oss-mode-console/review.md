# 049 レビュー

## Round 1

レビュー: cold reviewer（opus-5.5 / effort: xhigh）に依頼した（初回ビルド直後）。対象は `891974d`。PM(fable-5-1) が指摘を再現した。

### cold reviewer の結果（review-r01.md をそのまま連結）

# 049 cold review Round 1（check_sg_descriptions の OSS 版の判定を Terraform に聞く）

対象: `891974d`（base `e8a52a5`）。build の文脈なしで読んだ。

## サマリ

- Must fix 0 / Should fix 2 / Nit 4。
- 実装は design.md の方針 1〜6 を満たしている。
  - `oss.auto.tfvars` の grep は無くなった。
  - TF_CLI_ARGS の確認、console、コードの読み取り、show の順に止まる側へ倒している。
  - `oss_replaced` は完全一致でちょうど 1 行を見ている。
  - 引数は `0|1` で受ける。
- 自分で走らせたテストの結果。
  - `uv run -q --group dev --group web python tests/test_workflow.py` → 「通過 507 / 失敗 0」。049 T-1〜T-8 と T-3 (g) は ok で、T-3 は skip せず本物の terraform console を通った。
  - `uv run -q --group dev --group web python tests/test_oss_ops.py` → 「通過 211 / 失敗 0」。049 T-9 の 2 件も ok。
  - `bash -n ops/up-common.sh && bash -n ops/up.sh && bash -n ops/oss/up.sh` → rc=0。
  - `terraform fmt -check -diff IaC/terraform/aws-managed/base/core/oss.tf` → rc=0。
- 残っている問題は 2 つで、どちらも PM の判断が要る。
  - 本物の console は Warning を stdout に出す。そのため design の方針 1 と T-4 の前提（Warning があっても通る）が成り立たない。
  - design.md の変数の優先順位の記述が逆になっている。
- エンジニアは 2 つとも build.md で差し戻している。実装側で黙って吸収はしていない。

### 見た観点 / 見ていない観点

- 見た観点
  - design.md 整合: 方針 1〜9、T-1〜T-9、変異の一覧と実装・テストを突き合わせた。
  - correctness: `set -euo pipefail` の下での `$(...) && {...} || {...}` の止まり方を見た。`${!TF_CLI_ARGS*}` の展開（`set -u` で未設定でも空）も見た。console の出力の判定と `oss_replaced` の awk の規則も見た。
  - security: die が出すのは TF_CLI_ARGS の名前だけで、値は出さない。console の stdout を stderr に出す経路に変数の値は乗らない（未宣言の変数の Warning は名前とファイル名だけ）。
  - runtime bugs
    - ここの `/bin/bash` は 3.2.57 だった（`python3 -c` の subprocess で `$BASH_VERSION` を出して確かめた）。bash 5.1 より前は here-string が一時ファイルになり、console の stdin はパイプではない。それでも T-3 (a)〜(f) は本物の console で rc=0 / 期待どおりに通った。この環境では問題にならない。
    - 呼び出し位置も見た。`ops/up.sh` と `ops/oss/up.sh` から、state がある場合だけ `tf_init base/core` の後に呼ばれる。
  - data loss: 守りは apply の前に止まるだけで、何も消さない。
  - API compatibility: `sg_descriptions_in_code` の引数が必須になった。呼び出し元は `check_sg_descriptions` とテストだけ。
  - missing tests: T-3 は本物の console、T-4 は偽の出力の形、T-5 は TF_CLI_ARGS、T-6 は `oss_replaced`、T-7 は引数、T-8 は静的な確認、T-9 は OSS 版の通しを見ている。
  - docs: `docs/architecture/core.md:103` と `docs/architecture/resources/vpc-perimeter.md:98` の 049 の一言を見た。
- 見ていない観点
  - 変異 M1〜M7 は自分で打ち直していない（build.md の結果は読んだだけ）。
  - `ops/check.sh` は走らせていない（init で `.terraform` を書くため）。
  - 中身のある本物の state に対する console は試していない（provider の schema の移行が要る state など）。
  - AWS では何も動かしていない。
  - Terraform v1.16.0 以外の版での console の Warning の出先は見ていない。

## Must fix

None

## Should fix

- [design.md 整合 / runtime bugs] 本物の `terraform console` は Warning を stdout に出す。そのため Warning が 1 つでもあると `ops/up.sh` / `ops/oss/up.sh` は手順 0 の後に「local.oss が読めない」で止まる（`ops/up-common.sh:144-146`）。
  - design との食い違い
    - design.md の方針 1（`design.md:62`）は「stderr は捨てずにそのまま流す（validation の Warning …）」と書いている。
    - T-4（`design.md:104`）は「`false` + stderr に Warning + rc=0 → 通る」と書いている。
    - 背景の実験（`design.md:37`）も Warning は stderr に出る前提で書かれている。
    - Terraform v1.16.0 の console は Warning を stdout に出す。T-4 の通る側（`tests/test_workflow.py` の `_csd_run(console=(0, "false\n", "Warning: …"))`）は、本物が出さない形を偽物で通しているだけになっている。
  - 止まるきっかけ
    - いまは何も止まらない（T-3 (a) は rc=0。`terraform.tfvars` は置かれていない）。
    - `IaC/terraform/aws-managed/base/core/terraform.tfvars` に宣言の無い変数が 1 つ残っていると止まる（ほかのルート用の値を写したなど）。
    - provider の非推奨の Warning が console で出る版に上がっても止まる。
    - 止めた理由は design のどこにも書かれておらず、`docs/troubleshooting.md` にも行が無い。
  - stdout に ANSI の色のコードが混ざる。die の前に stderr へ出すときもそのまま出る（下の出力の `\x1b[33m`）。
  - 分類の理由: 止まる側に倒れているので Must ではない。ただし design の方針と T-4 の前提に反する挙動なので、方針を PM が決めて design と実装をそろえる必要がある。
  - 再現（自分で実行した。T-3 (g) は `terraform.tfvars` に `undeclared_x = 1` を置いて本物の console を通す）
    ```
    $ uv run -q --group dev --group web python tests/test_workflow.py 2>&1 | grep -n "049 T-3 (g)"
    502:ok 049 T-3 (g) check_sg_descriptions: … （いま: rc=1 '[0m \x1b[0mreduce the verbosity of these warnings, use the -compact-warnings option.\n\x1b[33m╵\x1b[0m\x1b[0m\n\x1b[0m\x1b[0m\nfalse\nDIE: …/tf/base/core の local.oss が読めない（terraform console の失敗か、stdout が true / false の 1 行でない。上の出力。Warning も stdout に出る）。まだ何も作っていない' ['tf base/core console -var owner=tester']）
    ```
  - 直し方の候補（どちらにするかは PM が決める）
    1. console に `-no-color` を付け、式を `"local.oss=${local.oss}"` のような目印付きにする。stdout のうち `"local.oss=true"` / `"local.oss=false"` にちょうど一致する行が 1 行だけあることを見る（Warning は読み飛ばす）。
    2. Warning で止まる挙動は残す。design.md の方針 1・T-4・背景の実験を直し、`docs/troubleshooting.md` に「local.oss が読めない」の行を足す（`terraform.tfvars` の宣言の無い変数を消す、など）。
- [design.md 整合] design.md の背景（`design.md:10`）は「`oss.auto.tfvars` があっても `TF_VAR_project=nwc-poc` が勝つ（環境変数は tfvars より強い）」と書いている。T-3 (f)（`design.md:103`）も「環境変数が勝つ → rc=0」と書いている。どちらも Terraform の優先順位と逆である。
  - 環境変数がいちばん弱く、`terraform.tfvars`、`*.auto.tfvars`、`-var` の順に強くなる。
  - 実装とテストは正しい側にそろっている。エンジニアは T-3 (f) の期待を rc=1 に反転し、build.md に逸脱として書いている。
  - 分類の理由: 動きへの影響は無いので Must ではない。ただし仕様の正本である design.md に事実の誤りが残り、テストと矛盾する。QUEUE 164 の広い案など、次のサイクルがこの記述を前提にすると誤る。
  - 再現（自分で実行した。T-3 の (f) は `TF_VAR_project=nwc-poc` と `oss.auto.tfvars` の両方を置き、マネージド版の state を見せる）
    ```
    $ uv run -q --group dev --group web python tests/test_workflow.py 2>&1 | grep -n "049 T-3 check"
    501:ok 049 T-3 check_sg_descriptions: … '(f) TF_VAR_project=nwc-poc + oss.auto.tfvars': (1, ['tf base/core console -var owner=tester', 'tf base/core show -no-color'], 'ipeline/analytics)」）。description を変えると SG は作り直しで、… 先に ops/down.sh で全部消してから ops/up.sh。まだ何も作っていない')}）
    ```
    (f) は rc=1 で、spark の違いで止まった。console が `true`（OSS 版）を返したので、`oss.auto.tfvars` が `TF_VAR_project` に勝っている。
  - 直し方: PM が design.md の 10 行目と T-3 (f) の期待を直す。

## Nit

- [correctness] `oss_replaced` の行の守り（`ops/up-common.sh:74-78`、`:102-103`）は、わざと避ける書き方なら通る。いずれも「読んだだけ。再現手順: `oss.tf` を下のどれかに書き換えて `sg_descriptions_in_code 0` を打つ」。
  - 完全一致の行を `/* … */` の中に残し、本当の定義を `  oss_replaced\t= ["msk", "web"]` のように `=` の前をタブにして書く。awk はコメントを区別せずに 1 行と数え、タブの行は `/^  oss_replaced[ =]/` に当たらない。
  - 完全一致の行をコメントにして、定義を `locals.tf` や `*_override.tf` へ移す。awk は `oss.tf` しか見ない。
  - console は本当の値を見るので、守りが通しても Terraform の判定はずれない。ずれるのは表のキーの守りだけ。
  - 分類の理由: どれも誤って入る書き換えではない。コードの形を文字列で守る方式（043〜048）に共通する限界で、QUEUE 164 の広い案（Terraform に聞く範囲を広げる）で閉じる話になる。
- [design.md 整合] TF_CLI_ARGS の die（`ops/up-common.sh:138-141`）は、値が空のまま export された `TF_CLI_ARGS=` でも止まる（`${!TF_CLI_ARGS*}` は空の値の変数も名前を返す。読んだだけ）。
  - design.md のリスク 3 は「`-no-color` のような無害な使い方も止める」までしか書いていない。
  - 分類の理由: 止まる側で、メッセージに名前が出るので外し方は分かる。
- [design.md 整合] die と NG の文言が design.md の方針 1・2 と少し違う。
  - 実装は「terraform console の失敗か、stdout が true / false の 1 行でない。上の出力。Warning も stdout に出る」と「引数（OSS 版なら 1、マネージド版なら 0）が無いか 0 / 1 でない」。
  - build.md の逸脱 2・3 に書かれている。
  - 分類の理由: テストは「local.oss が読めない（terraform console の失敗」「引数」の部分一致で見ている。挙動は同じで、Should fix 1 の決着のときに design 側の文言もそろえれば足りる。
- [docs] `docs/troubleshooting.md` に、049 で増えた 2 つの die の行が無い。
  - 2 つの die は「local.oss が読めない」と「TF_CLI_ARGS / TF_CLI_ARGS_* が設定されている」。
  - design.md の変更するファイルにも入っていない。
  - 分類の理由: TF_CLI_ARGS の文言は外し方まで言っているので要らない。「local.oss が読めない」の行が要るかは Should fix 1 の決め方次第。

## 良かった点

- 止まる順序が明確で、どこで止まっても「まだ何も作っていない」になっている。
  1. TF_CLI_ARGS を確かめる。
  2. console で `local.oss` を聞く。
  3. コードの description を読む。
  4. `terraform show` で state を読む。
- console を `tf()` 経由で打つので、`-chdir` と `TF_AWS_ENV` が apply と同じになる。`-var owner` も apply と同じで、差を作らない。
- T-3 は本物の console を通し、048 の Nit 1 の経路（`oss = true`、`TF_VAR_project`、`oss.auto.tfvars`）が全部 spark の違いで止まることを実物で示している。
  - AWS の認証情報と `TF_VAR_*` / `TF_CLI_ARGS*` を外している。
  - `terraform.tfvars` は写さないので、手元の値を拾わない。
- エンジニアは本物の console を当てて 2 つの誤りを自分で見つけ、build.md で差し戻している（design の前提を黙って吸収していない）。
  - Warning は stdout に出る。
  - 変数の優先順位が design と逆。
  - 黙って止まっていた点（Warning が `$mode` に吸われて何も出ない）は、stdout を stderr に出して直した。
- `oss_replaced` の守りは、キーが減る向き・増える向き・三項を外す・行末のコメント・`=` の前後の空白とタブ・重複を全部 T-6 で落としている。どちらの版でも同じ行を見る。
- TF_CLI_ARGS の die は名前だけを出し、値を出さない。
- test_oss_ops.py の偽 terraform は、知らない式で `sys.exit` する。console が `local.oss` 以外を聞くように変わったら落ちる。

## ユーザーへの質問

- 本物の `terraform console` は Warning を stdout に出す。`terraform.tfvars` に宣言の無い変数があるような無害な Warning でも `ops/up.sh` を止めるか、PM に決めてほしい（Should fix 1）。
  1. Warning は読み飛ばして値だけ見る（目印付きの式と `-no-color`）。
  2. 止める挙動を残し、design と troubleshooting を直す。


### PM の確認（2026-10-12）

- 全テスト 16 本を pm-042 の `891974d` で取り直し: 失敗 0（`r046/alltests.sh`）。`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` rc=0
- **Should fix 1（console は Warning を stdout に出す）: 再現した。** `warn-probe.sh`（本物の Terraform v1.16.0、`.terraform` はリンク、認証情報なし）:
  - `terraform.tfvars` に `undeclared_x = 1` → `console -var owner=probe <<<'local.oss'` は rc=0、**stdout** = 色付き（`ESC[33m`）の「Warning: Value for undeclared variable」の枠 + `false`、stderr = 空。いまの実装（最後の 1 行が `false` でないと die）はこの無害な Warning で `up.sh` を止める。049 より前は止まらなかったので退行
  - `-no-color` + 式 `"local.oss=${local.oss}"` → rc=0、stdout = 色なしの Warning の枠、空行、最後の行がちょうど `"local.oss=false"`（od -c で `" l o c a l . o s s = f a l s e " \n`）、stderr = 空
  - `TF_VAR_project=bogus` → **rc=1**、stdout = 「Warning: Due to the problems above …」の枠だけ、stderr = 「Error: Invalid value for variable」「Error: Reference to uninitialized local value」。設計の「rc=0」は誤り
  - 根本原因: PM が console の Warning の出力先を現物で確かめずに stderr と書いた（Round 1 の probe は `2>&1` で見ていて区別していなかった）
  - 判断: **案 1 を採る**（`-no-color` + 目印付きの式、stdout の最後の行がちょうど `"local.oss=true"` / `"local.oss=false"` で、その形の行が 1 行だけのとき受ける。Warning の行は stderr にそのまま出す）。Round 2 の設計へ。分類は Should fix のまま（correctness。止まる側に倒れているので Must ではない）
- **Should fix 2（変数の優先順位が逆）: 再現した。** 同じ probe で `oss.auto.tfvars=nwc-oss` + `TF_VAR_project=nwc-poc` → `true`（tfvars が勝つ）、`TF_VAR_project=nwc-oss` + `-var project=nwc-poc` → `false`（-var が勝つ）。設計の背景と T-3(f) を Round 2 で直す（実装は Round 1 でエンジニアが T-3(f) を反転済み）
- Nit 1（`/* */` や `locals.tf` への移動で `oss_replaced` の行の守りを抜けられる）: 直さない。行の守りは 048 と同じ型で、コードの表を console で評価する広い案（QUEUE 164）で丸ごと置き換わる
- Nit 2（空の `TF_CLI_ARGS=` も止める）: 受け入れる（止まる側）。design の未確定事項 3 に書く
- Nit 3（die / NG の文言が design と違う）: Round 2 で design の文言を実装に揃える（実装の文言のほうが具体的）
- Nit 4（`docs/troubleshooting.md` に新しい die の行が無い）: Round 2 で足す

## Round 2

レビュー: cold reviewer（opus-5.5 / effort: xhigh）に依頼した（完了判定の直前。2 回目）。対象は `db704e1`（Round 2。751f07f..db704e1）。PM(fable-5-1) が確認した。

### cold reviewer の結果（review-r02.md をそのまま連結）

# 049 cold review Round 2（check_sg_descriptions の OSS 版の判定を Terraform に聞く）

対象: `db704e1`。Round 2 の差分は `751f07f..db704e1`、全体は `b490486..db704e1`。build の文脈なしで読んだ。

## サマリ

- Must fix 0 / Should fix 0 / Nit 1。
- Round 1 の指摘は、review.md の Round 2 の設計と design.md の決着どおりに直っている。
  - console は `-no-color` と目印付きの式 `"local.oss=${local.oss}"` で聞く。受けるのは 3 つの条件がそろうときだけ（`ops/up-common.sh:146-154`）。
    - rc=0。
    - 目印の形の行が stdout 全体でちょうど 1 行。
    - 最後の行が目印そのもの。
  - 受けるときは、最後の行より前（Warning）を stderr に出す。止めるときは、stdout を全部 stderr に出してから die する（Should 1）。
  - T-3 (f) の期待は、Terraform の本当の優先順位（`TF_VAR_` がいちばん弱い）に合わせてある（Should 2）。T-3 (h) では、`TF_VAR_project=bogus` で rc=1 になることを本物の console で見ている。
  - `oss.tf` の 2 行のコメントは「`ops/up-common.sh` の `check_sg_descriptions`」になった（Nit 3）。`docs/troubleshooting.md:27` に行が足された（Nit 4）。
- 自分で走らせたテストの結果（HEAD `db704e1`、作業ツリーは clean）。
  - `uv run -q --group dev --group web python tests/test_workflow.py` の結果は「通過 508 / 失敗 0」。
    - 049 T-1〜T-8、T-3 (g)、T-3 (h)、T-6 ×2 は ok。
    - T-3 の skip 行は出ず、本物の terraform console（v1.16.0）を通った。
  - `uv run -q --group dev --group web python tests/test_oss_ops.py` の結果は「通過 211 / 失敗 0」。049 T-9 の 2 件は ok。
  - 上の 2 本は grep にパイプしたので、終了コードは取っていない。判定の根拠は最後の集計行。
  - `bash -n ops/up-common.sh && bash -n ops/up.sh && bash -n ops/oss/up.sh` は rc=0。
- design.md の「console は state の lock を取らない・待たない」を現物で確かめた。手順は良かった点に書いた。
- エンジニアのセルフレビューの Nit（build.md Round 2 の N-1〜N-6）は読んで、どれにも同意する。ここには重ねて書かない。

### 見た観点 / 見ていない観点

- 見た観点
  - design.md 整合
    - 書き直した方針 1 と方針 2〜9、T-1〜T-9（T-3 (g)(h) と、書き直した T-4 を含む）を、実装とテストに突き合わせた。
    - die の文言は方針 1 と一字一句同じ。
  - correctness
    - `out=$(...) || rc=$?` から、`${out##*$'\n'}`、awk で数える目印の数、3 条件の判定までの流れを見た。
    - `${out%$'\n'*}` で Warning を stderr に出す流れも見た。値の行しかないとき（`$out = $last`）は、余計な行を出さない。
    - `${!TF_CLI_ARGS*}` の確認が console より前にあることを見た。
  - security
    - here-string は単一引用符なので、`${local.oss}` を bash が展開しない。
    - stderr に出すのは console の stdout だけ。TF_CLI_ARGS の die が出すのは変数の名前だけで、値は出さない。
  - runtime bugs
    - console が state の lock を取るかを現物で確かめた（良かった点）。
    - 目印の行が 2 行あり、最後の行は正しいときの die の文言を確かめた。`check_sg_descriptions` を切り出し、bash で動かした（Nit 1）。
  - data loss: 守りは apply より前に止まるだけで、何も消さない・書かない。
  - API compatibility: `sg_descriptions_in_code` の引数（0 か 1 が必須）は Round 1 から変わっていない。呼び出し元は `check_sg_descriptions` とテストだけ。
  - missing tests
    - T-4 の止まる 7 形と通る 3 形を読んだ。
    - T-3 (g) は、Warning が stderr に出て色のコード（`\x1b[`）が無いことを見ている。
    - T-3 (h) の rc=1 と、T-9 の OSS 版の偽 terraform も読んだ。
  - docs: `docs/troubleshooting.md:27` と、`oss.tf` の 2 行のコメントを見た。
- 見ていない観点
  - 変異 M1〜M10 は打ち直していない。build.md の結果を読んだだけ。
  - `ops/check.sh` は走らせていない。init で `.terraform` を書くため。
  - resources が入った本物の state に対しては console を試していない。lock の確かめも、resources が空の state で行った。
  - Terraform v1.16.0 以外の版は見ていない。
  - AWS では何も動かしていない。

## Must fix

None

## Should fix

None

## Nit

- [design.md 整合 / docs] 目印の行が 2 行以上あり、最後の行は正しく rc=0 のとき、die の文言が理由を取り違える（`ops/up-common.sh:149-152`。文言は design.md 方針 1 の指定どおり）。
  - 文言は「terraform console の失敗か、stdout の最後の行が "local.oss=true" / "local.oss=false" でない」と言う。この場合はどちらにも当たらない。
  - 本当の理由（目印の行が 1 行でない）は文言に出てこない。
  - 分類の理由: 止まる側の挙動は正しく、上に stdout も出るので、運用者は読み解ける。起きるのは目印と同じ行が Warning に入るときだけで、原理的にありうるだけ（エンジニアの N-2）。だから Nit。
  - 再現の手順（自分）: `check_sg_descriptions` を `ops/up-common.sh` から切り出した。`tf` は、`"local.oss=true"` を 2 行出して rc=0 で返す偽物にした。`die` は「DIE: …」を stderr に出して exit 1 にした。それを python の subprocess から bash で動かした。
    ```
    rc= 1
    stderr: "local.oss=true"
    "local.oss=true"
    DIE: /x/base/core の local.oss が読めない（terraform console の失敗か、stdout の最後の行が "local.oss=true" / "local.oss=false" でない。上の出力）。まだ何も作っていない
    ```
  - T-4 は「local.oss が読めない（terraform console の失敗」の部分一致で見ている（`tests/test_workflow.py:1994`）。括弧の中に一言足しても落ちない見込みだが、確かめていない。

## 良かった点

- design.md の「console は state の lock を取らない・待たない」が正しいことを現物で確かめた。
  - 一時ディレクトリを用意した。
    - base/core の `.tf` と `.terraform.lock.hcl` をコピーし、`.terraform` はリンクにした。
    - AWS の環境変数を外し、`AWS_CONFIG_FILE=/dev/null` にした。
    - resources が空の `terraform.tfstate` を置いた。
  - その state を、別プロセス（python）から `fcntl.lockf` で排他ロックした。
  - 対照として `terraform state rm -no-color aws_vpc.nonexistent` を打った。rc=1 で、`Error: Error acquiring the state lock` と `Error message: resource temporarily unavailable` が出た。プローブのロックは Terraform のロックとぶつかる。
  - 同じロックを持ったまま `terraform console -no-color -var owner=probe` を打った（stdin は `"local.oss=${local.oss}"`）。
    - rc=0、stdout は `"local.oss=false"`、stderr は空。
    - かかった時間は約 1.1 秒で、ロックが無いときと同じだった。
  - したがって、別の terraform が base/core のロックを持っていても、この守りは「local.oss が読めない」では止まらない。
- 3 条件（rc・目印の数・最後の行）がそろうときだけ受ける形にした。これで Round 1 の Should 1（無害な Warning で止まる）が直り、値を取り違えたときも止まる側に倒れる。T-3 (g) は本物の console で、「Warning を stderr に出して進み、色のコードが無い」ことを見ている。
- T-3 (f) の期待を Terraform の本当の優先順位に合わせた。T-3 (h) で、`TF_VAR_project=bogus` が rc=1 になることを本物で固定した。troubleshooting の行が原因に挙げる「`TF_VAR_project` や tfvars」とも食い違わない。
- 変更は design.md の範囲に収まっている（`up-common.sh`、`oss.tf` のコメント 2 行、troubleshooting の 1 行、テスト）。範囲外の古いコメントには触らず、N-4 として報告している。

## ユーザーへの質問

None


### PM の確認（2026-10-12）

- 全テスト 16 本を pm-042 の `db704e1` で取り直し: 失敗 0（`test_workflow.py` 508 / `test_oss_ops.py` 211 ほか）。`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` rc=0。`ops/check.sh` はエンジニアが Round 2 で rc=0（`build.md`）
- Round 1 の Should fix 1（Warning で止まる）の元の再現をいまのコードで: `test_workflow.py` の本物の `terraform console` の check が ok ── 「ok 049 T-3 (g) check_sg_descriptions: 本物の terraform console は Warning（宣言の無い変数の値）を stdout に出すが、値は最後の行に出…」（rc=0 で console と show、stderr に色なしの Warning）、「ok 049 T-3 (h) … TF_VAR_project=bogos（validation で rc=1）なら Terraform のエラーを流して「local.oss が読めない」」。解消
- Round 1 の Should fix 2（優先順位）: `design.md` の背景と T-3(f) を直し、T-3(f) は「tfvars が勝つ → rc=1 で spark」で ok（上の T-3 の行）。解消
- Nit 1（目印の行が 2 行以上で最後の行は正しいとき、die の文言が理由を言わない）: 直さない。止まる側で stdout も上に出る。Warning に目印と同じ行が入るときにしか起きない（エンジニアの N-2 と同じ）
- エンジニアのセルフレビュー Round 2 の Nit 6 件（N-1 project 以外の validation の Error では console が rc=0 で進む（`local.oss` は `var.project` だけで決まるので正しい）、N-2 `_W049` の字下げが本物と違う、N-3 stdin のパイプと here-string、N-4 046 / 048 のコメントの「`ops/up.sh` の」が 3 行残る、N-5 troubleshooting の「run: terraform init」の字面と `TF_CLI_ARGS*` の行、N-6 T-3 が `TF_DATA_DIR` を外さない）: どれも直さない。最終報告に載せる
- サイクル完了。QUEUE 162 を完了に。全体設計 HTML は触らない（`up.sh` の守りの中の判定の仕方が変わっただけで、構成要素・データの流れ・配置は変わらない）
