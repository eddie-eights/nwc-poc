# 手元の小さな直しを片付ける（032）— build

## Round 1

実装モデル: opus-5.5 / effort: high

### 変更ファイル

| ファイル | 件 | commit |
|---|---|---|
| `docker/compose/up.sh` / `tests/test_local_compose.py` | A | 086fa8a |
| `app/temporal/rules.py` / `app/agentcore/topology.py` / `tests/test_agentcore.py` | B | f28ec96、66287b4・7c740d0（セルフレビューで直した続き） |
| `tests/test_analytics.py`（`_gh_slug` / `_faq_heads` と check） | C | 1bc7731、40d8abe（セルフレビューで直した続き） |
| `docs/architecture/README.md` / `tests/test_analytics.py`（pptx の check） | D | 951c4fb |

### 検証（最後の編集のあとに取り直した出力）

```
$ uv run --frozen python3 tests/test_local_compose.py   (exit 0)
通過 143 / 失敗 0

$ uv run --frozen python3 tests/test_agentcore.py   (exit 0)
通過 168 / 失敗 0

$ uv run --frozen python3 tests/test_analytics.py   (exit 0)
通過 534 / 失敗 0

$ uv run --frozen python3 tests/test_workflow.py   (exit 0)
通過 333 / 失敗 0

$ bash -n docker/compose/up.sh   (exit 0)
(無言)

$ python3 -c （rules.py の impact に食わせる）
同点 a-b / c-d に b-c: []
a 孤立に a-b（2 台だけ）: ['a', 'b']
a 孤立、b-c が本流に a-b: ['a']

$ uv run --frozen --only-group docs python docs/architecture/render_pptx.py docs/architecture/architecture-managed.deck.md -o <scratch>/managed.pptx   (exit 0)
書き出した: <scratch>/managed.pptx
$ uv run --frozen --only-group docs python docs/architecture/render_pptx.py docs/architecture/architecture-oss.deck.md -o <scratch>/oss.pptx   (exit 0)
書き出した: <scratch>/oss.pptx
managed.pptx 65246 bytes zip 10 slides
oss.pptx 66267 bytes zip 10 slides
```

C の 5 点を、main の `_faq_heads` / `_gh_slug` と、このブランチのものに食わせた結果（関数だけを抜き出して実行）:

```
test_analytics.main.py {'1 実体参照': False, '2 字下げ': False, '3 info string': True, '4 閉じの字下げ': False, '5 tab': False}
test_analytics.py      {'1 実体参照': True, '2 字下げ': True, '3 info string': True, '4 閉じの字下げ': True, '5 tab': True}
開きのフェンスを info string 無しだけにした変異: {'1 実体参照': True, '2 字下げ': True, '3 info string': False, '4 閉じの字下げ': True, '5 tab': True}
```

3 は main ですでに満たしていた（設計の「いまそうなら変えない」）。check は退行の見張りとして置いた。
FAQ（`docs/faq-fukuda-nwc-poc.md`）の見出しの (level, 本文, 行) の一覧は、直す前と後で同じ（実装時に json で比べた）。

退行の注入（直した形を 1 か所ずつ元に戻してテストを打ち、戻したあと `git status` が空なのを確かめた）:

```
[A: up.sh を正規表現の grep に戻す] exit 1: AssertionError: up.sh: GW の判定は check.sh の dest と同じ grep -F --（…）
[D: README を --group docs に戻す] exit 1: AssertionError: 構成の pptx は docs/architecture/render_pptx.py で描き、…（--only-group。032）…
[D: render_pptx.main を壊す] exit 1: AssertionError: 構成の pptx は uv run --only-group docs で描ける（…032）
[B: or largest(comps0) を外す] exit 1: AssertionError: impact: 本流（a–c の 3 台）と別の同じ大きさのかたまり（d–e と f–g）どうしをつないで本流を抜けば、d–g がつながり直すに出る（…）
[C: コードの中も unescape する] exit 1: AssertionError: FAQ の見出し: 実体参照は解いてからアンカーにする（&amp; は & になって消える。032）
B の繰り上がり（66287b4 の前の定義）を戻す: exit 1（impact: 回線を落として本流（a–e の 5 台）が割れ、… f–i はつながり直すに出ない）
```

### 設計との差

- B の定義。設計の式（変更前に次数 0 ∩ 変更後の本流）では既存の `["a"]` / `["e"]` が赤になったため、リスク 1 の広い定義（変更前の本流にいなかった機器）にした。これはセルフレビューで「回線を落として本流が割れ、別のかたまりが本流に繰り上がると、そのかたまりを全部載せる」退行が見つかった（下の S1）。最終形は「変更後の本流 K を変更前のかたまりで分け、K の中の唯一いちばん大きい断片 W にいなかった機器（変更前に DOWN だった機器を含む）と、K につながり変更前は W につながっていなかった端。W が同点なら変更前の本流を W にし、それも同点なら変更前に次数 0 だった機器だけ」。設計の「孤立か、本流と別のかたまりにいた」を、変更前の本流ではなく変更後の本流の中で測る形。
- B の検証の例「a 孤立 → a-b で `["a"]`」は、2 台だけだと a も b も次数 0 の孤立なので `['a', 'b']` になる（設計の式でも同じ）。b-c を本流に置いた 3 台で `['a']` を確かめた。
- B の check は設計の 1 つ（同点）に加えて、本流と別のかたまりから本流へ、端（TRex）の復帰、DOWN だった機器の復帰、本流の繰り上がり、本流でない同点のかたまりどうしの合流の 5 つを足した（計 6）。
- A は文字列の check に加えて、偽の `ip` に `.` が任意の 1 文字に当たる番地（`203a0a113a1`）を出させ、TELEGRAF_BIND が空で WARNING が出ることを見る振る舞いの check にした。`-q` を使わないのは pipefail と SIGPIPE の組み合わせを避けるため（check.sh と同じ形）。
- C の 4（閉じのフェンス）は「開きの字下げ以上、開きの字下げ + 3 以下」にした。CommonMark は閉じの字下げを 0〜3 とだけ決めているが、開きを字下げした場合の 028 の S2 の挙動を保つため。FAQ では差が出ない。
- D の描画の check は、`render_pptx.py` に `-o` の口があったので `os.chdir` ではなく `m.main([deck, '-o', out])` を scratch へ向けて呼ぶ。検証も README の出力先（git の管理下の pptx）ではなく scratch に描いた（README の出力先に描くと追跡中のファイルが変わる）。

### セルフレビュー

- 自分: opus-5.5 / effort high（xhigh へ切り替える手段が無く、high のまま）。反対弁護人: opus（Agent、general-purpose、読み取り専用。終わったあと `git status --porcelain -uall` に増えたものは無かった）
- Must fix: 0

| # | 分類 | [観点] 場所 | 破綻シナリオ | 確かめたこと | 片付け |
|---|---|---|---|---|---|
| S1 | Should fix | [correctness] `app/temporal/rules.py` / `app/agentcore/topology.py` の impact | f28ec96 の定義（変更前の本流にいなかった機器）だと、回線を落として本流が割れ、別のかたまりが本流に繰り上がると、そのかたまりを全部「つながり直す」に載せる | a–e と f–i で b#1 を落とすと f–i が出た | 直した（66287b4）。check を足し、戻すと落ちる |
| S2 | Should fix（反対弁護人） | [correctness] 同上 | 本流 a–c と d–e / f–g で e–f を戻すと d–g が本流になるが、断片 d–e / f–g が同点で W が決まらず [] | 再現: 直す前 `[]`、main と設計のリスク 1 は `['d','e','f','g']`。DOWN の x が d–e と f–g をつなぐ device_up も直したあと `['d','e','f','g','x']` | 直した（7c740d0、`or largest(comps0)`）。check を足し、戻すと落ちる |
| S3 | Should fix（反対弁護人） | [correctness] `tests/test_analytics.py` の `_gh_slug` | 見出し "`&amp;` x" で、コードの中まで unescape して "-x"（GitHub は "amp-x"。main は正しかった） | 再現した | 直した（40d8abe）。check を足し、戻すと落ちる |
| N1 | Nit | [correctness] `_gh_slug` | `html.unescape` は `;` の無い実体参照（`&amp x`）も解く。GitHub（CommonMark）は解かない | 読んだだけ。FAQ に該当する見出しは無い（見出しの一覧は直す前後で同じ） | 最終報告に回した |
| N2 | Nit | [correctness] `_faq_heads` | 028 から残る差: 字下げのコードブロックの中の ```、バッククォートを含む info string、閉じの `##` を落とさない | 読んだだけ。FAQ に該当なし | 最終報告に回した |
| N3 | Nit | [correctness] `_faq_heads` | 字下げした開きのフェンスを 0 桁の閉じで閉じない（設計との差の C の 4 に書いた選択） | c_red2 の 4 で挙動を固定 | 最終報告に回した |
| N4 | Nit | [runtime] pptx の check | タイムアウトで tempdir が残る、ZipFile を閉じない、`__main__` の経路を通らない | 読んだだけ | 最終報告に回した |
| N5 | Nit | [runtime] A の check | up.sh が途中で止まると IndexError（テストは赤になる） | 読んだだけ。赤になるので見逃しにはならない | 最終報告に回した |

反対弁護人が崩せなかったもの: A の正規表現の検出（注入で赤）、B の残りの形（乱数の差分 20,000 件。main との差は同点・DOWN からの復帰・本流の無いグラフの端・TRex どうしの縁のどれか）、C の 5 点の変異、設計の例の結論（2 台では `['a','b']`）。

「問題なし」とした観点と根拠:

- 設計整合性: 実装ステップ A〜D と検証方法を 1 行ずつ上の検証の出力と突き合わせた。差は「設計との差」に書いた
- rules.py と topology.py の一致: test_workflow の一致の check（通過 333）
- FAQ の見出しの一覧が変わらない: 直す前後の (level, 本文, 行) を json で比べて同じ
- 追跡中のファイルを書き換えない: pptx は scratch に描き、検証のあと `git status` が空
- security / data loss / API compatibility: 変更は検査とシェルの比較と impact の戻り値の中身だけ。キーと型は変えていない（読んだだけ）
