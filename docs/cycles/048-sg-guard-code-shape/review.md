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
