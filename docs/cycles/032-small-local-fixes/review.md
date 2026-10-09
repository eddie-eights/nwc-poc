# 手元の小さな直しを片付ける（032）のレビュー

## Round 1

実行モデル: cold reviewer は opus（初回ビルド直後に依頼した。Must fix 0 なので完了判定のラウンドも兼ね、2 回目は呼ばない）。確認は PM（fable-5-1）。実装は opus（build.md）。

対象: `4d3d071..948d954`（origin/feat/032-small-local-fixes）。

### cold reviewer の結果

## サマリ

対象: `git diff 4d3d071 HEAD`（HEAD = 948d954、origin/feat/032-small-local-fixes）。8 ファイル、+205 / -17（うち build.md が +102）。コードの実変更は `app/temporal/rules.py` と `app/agentcore/topology.py`（同一の impact の reconnected、各 +10 行）、`docker/compose/up.sh`（1 行）、`docs/architecture/README.md`（1 行）、テスト 3 本。

全体の評価: A〜D の 4 件とも design.md の意図どおりに入っている。A・C・D は design.md の方針にほぼそのまま沿う。B は design.md の式（変更前に次数 0 ∩ 変更後の本流）でも、リスク 1 の代替の式でもなく、第 3 の定義（変更後の本流の中の断片で比べ、同点なら変更前の本流、それも同点なら次数 0）になっている。build.md の「設計との差」に理由（既存の `["a"]` / `["e"]` が赤、本流の繰り上がり、断片の同点）と再現の check が書かれており、design.md の「同点で全機器が載ることは無い」という意図は満たしている。壊れる箇所や危険な箇所は見つからなかったので Must fix は 0。正本の design.md に最終の定義が残っていない点を Should fix に置いた。

### 見た観点 / 見ていない観点

見た観点:

- design.md との整合性
  - A〜D の各項目と「変更対象ファイル」「やらないこと」を diff と 1 つずつ突き合わせた。FAQ 本文（`docs/faq-fukuda-nwc-poc.md`）、`render_pptx.py`、verdict の式は diff に無い（変わっていない）
  - `diff <(sed -n 335,430p app/agentcore/topology.py) <(sed -n 80,175p app/temporal/rules.py)` が無言で、2 つの impact は同一
- correctness
  - B: `uv run --frozen python3 -c` で rules.py の impact に小さなトポロジを直接与えた。結果は次のとおり
    - design の例: 同点 a–b / c–d に b–c を上げると `[]`。a 孤立で b–c が本流のとき a–b を上げると `['a']`
    - 同点 + 孤立の z が片側につながると `['z']`
    - 同点で a に TRex が付いた状態で b–c を上げると `[]`
    - TRex の付け替え（本流の中で a→b）は `[]`
    - 本流でないかたまりにいた TRex が本流の機器につながると `['t']`
    - 本流でないかたまり（x–y）が孤立の z を取り込むと `[]`（main の lost() ベースでは z が載っていた。design の「変更後で本流にいる機器」どおりの狭め）
    - 変更 2 件（本流 a–f を 3/3 に割る + 本流でない x–y と p–q をつなぐ）は `['p','q','x','y']`（下の Nit 1）
    - 変更なしは `[]`、DOWN の機器を上げて本流に入れば `['c']`
  - C: `_faq_heads` / `_gh_slug` の差分を CommonMark の規則（見出しの 0〜3 空白、tab stop 4、閉じのフェンスの字下げ）と照らして読んだ
  - A: up.sh の `set -euo pipefail` の下で `grep -F -- … >/dev/null` が入力を読み切る（`-q` の早期終了による SIGPIPE を起こさない）ことを読んだ
- runtime bugs: B の新しい集合演算で、deg1 に無い機器が reconnected に入らないこと（main1 と端はどちらも adj1 のキー）を読んで確かめた。D の check が `render_pptx.main(argv) -> int` の戻り値を `max(...)` に渡すことを `docs/architecture/render_pptx.py:817-830` で確かめた
- API compatibility: impact の戻り値のキーと型は変わっていない。reconnected の消費者を grep した結果は summary の文と `tools.json` / `topology.py:539` の説明文だけ（説明文「上げたときに戻る機器」とも矛盾しない）
- missing tests: B に 6 つ、C に 5 つ、A に振る舞いの check 1 つ、D に描画の check 1 つが足されている
- テストは自分で打った。結果は次のとおり
  - `uv run --frozen python3 tests/test_local_compose.py` → `通過 143 / 失敗 0`
  - `uv run --frozen python3 tests/test_agentcore.py` → `通過 168 / 失敗 0`
  - `uv run --frozen python3 tests/test_analytics.py` → `通過 534 / 失敗 0`（D の描画の check を含む）
  - `uv run --frozen python3 tests/test_workflow.py` → `通過 333 / 失敗 0`（rules.py と topology.py の一致の check を含む）

見ていない観点:

- A を実機（WSL2 の `ip`）で動かしてはいない。偽の `ip` を使うテストと、コードを読んだ結果だけで判断した
- D の check を、uv のキャッシュが無いオフラインの環境では打っていない。手元のキャッシュがある環境で通ることだけを見た
- C の GitHub 側の実際のアンカーの生成（github.com での描画）は見ていない。CommonMark の仕様と照らしただけ
- security / data loss: 変更はテストの補助関数、シェルの比較 1 行、impact の戻り値の中身だけ。外部入力の経路や削除操作は増えていないことを読んで確かめた（動的な検査はしていない）
- type safety: 静的型検査は打っていない（repo に型検査の設定があるかも見ていない）

## Must fix

None

## Should fix

- [design.md との整合性] `docs/cycles/032-small-local-fixes/design.md` の「B. reconnected を「つながり直した機器」だけに」と、`app/temporal/rules.py:152-163` / `app/agentcore/topology.py:407-418` の食い違い。design.md は `deg0.get(i, 0) == 0 and i in main1` の式を示し、リスク 1 で「変更前の本流（同点なら空）にいなかった機器 ∩ 変更後の本流。変更前が同点なら次数 0 だけ」に広げてよいとしている。実装はそのどちらでもない。実装の定義は「変更後の本流の中で、変更前のかたまりで切った唯一いちばん大きい断片 W にいなかった機器。W が同点なら変更前の本流を W にする。それも同点なら次数 0 だけ」。加えて端（TRex）の規則がある。build.md の「設計との差」には理由と再現があり、動作は design.md の意図（同点で全機器が載らない、既存の 2 つは緑）を満たしている。ただし design.md は仕様の正本で、そこに残っているのは採らなかった式になっている。後のサイクルで impact を触る人は design.md を読み、実装と違う定義を前提にする。
  - 分類の理由: 今の挙動は壊れていないので Must ではない。ただ正本が実装と食い違ったままだと次の変更者が誤った前提から始めるので、review.md か design.md に最終の定義を残すべき

## Nit

- [correctness] `app/temporal/rules.py:158`（topology.py も同じ）: 変更を 2 件以上まとめて与えたときの扱い。たとえば次の場合
  - 変更前: 本流 a–f（6 台の鎖）と、本流でない x–y・p–q
  - 変更: c–d を落とす + y–p を上げる
  - 結果: 断片 {x,y}・{p,q} が同点なので、W が変更前の本流 a–f になり、reconnected = `['p','q','x','y']`
  - 一方、同じコードのコメント（rules.py:157）は「回線を落として別のかたまりが本流に繰り上がったとき、そのかたまりを『つながり直す』に載せない」としている。上の例は、繰り上がったかたまりを載せている点でその方針と一貫しない
  - design.md の広い定義（変更前の本流にいなかった ∩ 変更後の本流）には合っている。また、1 件の変更では起きない（ツールの説明は「回線か機器を 1 つ」）。分類の理由: 実際の呼び出しで起きにくく、verdict にも影響しないので Nit
- [missing tests] `tests/test_agentcore.py:266-295`: main の定義（lost() を後→前で使う）からは挙動が狭まっている。本流でないかたまりが孤立の機器を取り込んだとき、main では reconnected に載っていたが今は `[]` になる（上のプローブで確かめた）。design.md の「変更後で本流にいる機器」どおりの変化だが、それを固定する check が無い。分類の理由: 意図どおりの変化で、戻っても verdict は変わらないので Nit
- [correctness] `tests/test_analytics.py:1580`: `_gh_slug` のコードの区切り `` (`[^`]*`) `` は、バッククォート 1 つのコードスパンしか見ない。``` ``a`b`` ``` のような 2 つのバッククォートのスパンの中の実体参照は、外とみなして unescape する。FAQ に該当する見出しは無い。分類の理由: 今の FAQ では差が出ないので Nit（build.md の N1・N2 と同じ「028 から残る GitHub との差」の系統）
- [runtime bugs] `tests/test_analytics.py:1697-1707`: D の check の `subprocess.run(..., timeout=120)` は、タイムアウトすると `TimeoutExpired` を投げる。そのため check の失敗として出ず、テスト全体が例外で止まり、`_pptx_dir` も消されない。build.md の N4 と同じ。分類の理由: 赤にはなるので見逃しは起きないため Nit

## 良かった点

- A を文字列の check だけでなく、偽の `ip` に `203a0a113a1`（`.` を任意の 1 文字と読むと当たる番地）を出させる振る舞いの check にしている。正規表現に戻すと実際に赤になる形
- B で design.md の式が既存の check を壊すことを実装で見つけ、design.md のリスク 1 の手順どおりに広げた。さらにセルフレビューで「本流の繰り上がり」「断片の同点」の 2 つの退行を見つけ、それぞれに戻すと落ちる check を足している。rules.py と topology.py が一字一句同じで、test_workflow の一致の check も通る
- C の `_gh_slug` で、コードスパンの中の実体参照を解かない（GitHub と同じ）ところまで詰めている（design.md の「1 回 unescape」より正確）
- D の check が、uv が無い環境で skip せずに落とす（design.md の指示どおり）。追跡中の pptx を書き換えないよう scratch に描いている
- build.md に、設計との差、検証の出力、退行の注入の結果が具体的に書かれていて、レビューで再現しやすい

## ユーザーへの質問

- B の reconnected の最終の定義（断片の W を先に見て、同点なら変更前の本流、それも同点なら次数 0）を、このサイクルの仕様として design.md に書き戻すか、review.md に記録するだけにするか。正本をどちらに置くかの判断が要る


### PM の確認

**再現したもの**

- B の定義を `app/temporal/rules.py` の impact に直接当てた（`uv run --frozen python3 -c`）。同点 a–b / c–d に b–c を上げる → `[]`。a 孤立で b–c が本流のとき a–b を上げる → `['a']`。本流でない x–y が孤立の z を取り込む → `[]`。cold review のプローブと同じ。
- テスト（PM、design.md を直したあと）: test_local_compose `通過 143 / 失敗 0`、test_agentcore `通過 168 / 失敗 0`、test_analytics `通過 534 / 失敗 0`、test_workflow `通過 333 / 失敗 0`。

**直したもの**

- Should（design.md の B が採らなかった式のまま）: 設計を正本にする方針なので、`docs/cycles/032-small-local-fixes/design.md` の設計方針 B を実装の最終の定義（K の中の断片 W → 同点なら変更前の本流 → それも同点なら次数 0。端の規則。コード 4 行）に書き換え、当初の式を採らなかった理由と、変更 2 件以上の繰り上がりを範囲外にした旨を書いた。未確定事項 1 も確定に書き換えた。cold reviewer の質問（design.md か review.md か）はこれで design.md。

**直さなかったもの（最終報告へ）**

- Nit 1（変更 2 件以上で繰り上がったかたまりが載る。`rules.py:157` のコメントと一貫しない）: ツールの説明は「回線か機器を 1 つ」。design.md に範囲外と書いた。
- Nit 2（本流でないかたまりが孤立を取り込むと `[]` になる変化を固定する check が無い）: PM のプローブで `[]` を確かめた。check は足していない。
- Nit 3（`_gh_slug` のコードスパンの区切りがバッククォート 1 つだけ）: FAQ に該当する見出しが無い。028 から残る GitHub との差の系統。
- Nit 4（D の check の `subprocess.run(timeout=120)` が TimeoutExpired で止まり `_pptx_dir` が残る）: 赤にはなる。build.md の N4 と同じ。
- build.md の N1〜N5 は実装のセルフレビューの判断のまま。

**未確認**

- A を実機（WSL2 の `ip`）で動かしていない。偽の `ip` のテストと読みだけ。
- C の GitHub 側の実際のアンカー生成は見ていない。
