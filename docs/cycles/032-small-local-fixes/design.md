# 手元の小さな直しを片付ける（032）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

| # | BACKLOG の行 | 出所 | 扱い |
|---|---|---|---|
| A | docker/compose/up.sh の MGMT_GW の判定を check.sh と同じ `grep -F --` にする | 2026-10-10 の 028 の cold review の Nit | 直す |
| B | heal-main の precheck の reconnected を、実際につながり直した機器だけに絞る | 2026-10-10 に 027 の cold review の Nit 2 | 直す |
| C | FAQ の見出しの検査（`_faq_heads`）で残した GitHub との差を埋める | 2026-10-10 に 028 の cold review の Nit 1・2 とセルフレビュー | 直す |
| D | docs/architecture/README.md の pptx の描き方を `uv run --only-group docs` にし、検査に「描ける」ことを足す | 2026-10-10 に 028 の cold review の Nit 3・4 | 直す |

4 件とも AWS が要らず互いに独立。1 サイクルにまとめ、commit は 1 件 1 つに分ける。

### 調査で分かった事実（2026-10-10、origin/main 8a0f40a）

- A: `docker/compose/up.sh:13-18`
  ```sh
  MGMT_GW=203.0.113.1
  if ip -o -4 addr show 2>/dev/null | grep -q " $MGMT_GW/"; then
    TELEGRAF_BIND=$MGMT_GW
  else
    TELEGRAF_BIND=
    echo "WARNING: …" >&2
  ```
  `docker/compose/check.sh:112` は `grep -F -- " $MGMT_GW/" >/dev/null` の形。`.` が正規表現のメタ文字なので `-F` が正しい。`tests/test_local_compose.py:686-695` が check.sh の文字列を見ているが up.sh の `grep -q` の行を見る check は無い（実装で `grep -n MGMT_GW tests/test_local_compose.py` で確かめる）。
- B: `app/temporal/rules.py:150-160` と `app/agentcore/topology.py:405-415`（同一のコード）
  ```python
  "reconnected": sorted(i for i in lost(adj1, comps1, adj0, comps0) if i in deg1),
  "newly_isolated": sorted(lost(adj0, comps0, adj1, comps1) - targets),
  ```
  `lost(adjA, compsA, adjB, compsB)` は「A の本流（`largest()`）にいて B の本流にいない機器」。`largest()` は同点のかたまりがあると空を返す（`rules.py:95-130` の `view` / `largest` / `lost`）。変更前（0）が同点で割れていると `largest(comps0)` が空になり、変更後（1）の本流の全機器が `lost(…1, …0)` に入って reconnected に載る。verdict は ok のままで壊れはしない（027 の cold review の Nit 2 の文言どおり）。
  `tests/test_agentcore.py:250-266` の impact の check が `reconnected == ["a"]` と `["e"]` を期待している（既存の挙動は壊さない）。
- C: `tests/test_analytics.py:1575-1671` の `_gh_slug` / `_faq_heads` / `_faq_heads_ok`。028 で残した GitHub との差は 4 つ: (1) HTML の実体参照（`&amp;` など）を slug で解決していない、(2) 1〜3 空白で字下げした `#` 見出しを見出しとして拾っていない、(3) ```` ```x ```` の info string つきのフェンスを開きと認識しているか、(4) 列 0 で開いたフェンスを 4 空白以上で字下げした ```` ``` ```` で閉じたと見なしている（GitHub は閉じない）、tab を字下げ 1 文字と数えている（GitHub は tab 1 つ = 4 空白相当で、フェンスの中では別）。「いまの FAQ では差が出ない」ので、直したあとも FAQ の件数は変わらない。
- D: `docs/architecture/README.md:10` が `uv run --group docs python docs/architecture/render_pptx.py`。`--group docs` は dev のグループも入る（`uv` の `--group` は default groups に足す）。`--only-group docs` は docs だけ。`tests/test_analytics.py` の pptx の check は `"uv run --group docs python docs/architecture/render_pptx.py" in _arch_readme` の文字列だけで、描けることは見ていない。`render_pptx.py` の描画関数の名前と本数は実装で読む（「2 本の描画」）。`pyproject.toml` の `[dependency-groups] docs = [python-pptx …]` の有無も実装で確かめる。

## 設計方針

**A. `grep -F --`**

`docker/compose/up.sh:15` を `if ip -o -4 addr show 2>/dev/null | grep -F -- " $MGMT_GW/" >/dev/null; then` に。`tests/test_local_compose.py` に `'grep -F -- " $MGMT_GW/" >/dev/null' in _up_sh` の check を足す（check.sh の check の隣）。

**B. reconnected を「つながり直した機器」だけに**

`reconnected` の定義（実装で確定した正本。`app/temporal/rules.py` と `app/agentcore/topology.py` は同一のコードで、`tests/test_workflow.py` が一致を縛る）:

- 変更後の本流 K（`largest(comps1)`。唯一いちばん大きいかたまり。同点なら空）にいて、変更前は W にいなかった機器。W は「変更前のかたまりで K を切った断片のうち、唯一いちばん大きいもの」。
- W が同点で決まらないときは、変更前の本流（`largest(comps0)`）を W にする。
- それも同点なら、変更前に次数 0 だった（孤立か DOWN の）機器だけ。同点のかたまりどうしをつないでも全部が載らない。
- 端（TRex）は、変更後に K につながり、変更前は W につながっていなかったものを足す。

```python
main1 = largest(comps1)
w0 = largest([main1 & c for c in comps0]) or largest(comps0)
back = (main1 - w0) | {e for e in ends & set(adj1) if any(o in main1 for o in adj1[e]) and not any(o in w0 for o in adj0.get(e, []))}
"reconnected": sorted(i for i in back if w0 or deg0.get(i, 0) == 0),
```

変更前の本流を先に使わないのは、回線を落として別のかたまりが本流に繰り上がったとき、そのかたまりを「つながり直す」に載せないため。当初の案（変更前に次数 0 だった機器 ∩ 変更後の本流）は、既存の check（`["a"]` / `["e"]`。別のかたまりから本流へ戻る形）を落とすので採らなかった（経緯は build.md の「設計との差」）。

期待する振る舞い（`tests/test_agentcore.py` が縛る）: 同点 a–b / c–d に b–c を足すと `[]`。a 孤立で b–c が本流のとき a–b を足すと `["a"]`。本流でないかたまりが孤立の機器を取り込んでも `[]`（変更後の本流にいないので）。既存の `["a"]` / `["e"]` は緑のまま。

範囲外: 変更を 2 件以上まとめて与えたとき（本流を割る + 本流でないかたまりどうしをつなぐ）は、繰り上がったかたまりが載る。ツールの説明は「回線か機器を 1 つ」なので、このサイクルでは扱わない（032 の cold review の Nit）。

**C. `_faq_heads` の 5 点**

1. 実体参照: `html.unescape()` を slug の前に 1 回。
2. 字下げ: 行頭の 0〜3 個の空白を許す（`re.match(r" {0,3}(#{1,6})\s", line)`）。4 個以上は字下げコードなので拾わない。
3. info string: 開きのフェンスは ```` ``` ```` の後に何が続いてもよい（いまそうなら変えない）。
4. 閉じのフェンス: 開きの字下げ（0〜3）以下の字下げの ```` ``` ```` だけを閉じと見なす。4 個以上の字下げでは閉じない。
5. tab: 行頭の tab は 4 空白に展開してから 2・4 を判定する（`line.expandtabs(4)` を行頭の判定にだけ使う）。
   それぞれに最小のテストの文字列（`_faq_heads` に食わせる 3〜5 行の Markdown）で check を足す。`docs/faq-fukuda-nwc-poc.md` の見出しの件数と slug は変わらない。

**D. pptx**

1. `docs/architecture/README.md:10` を `uv run --only-group docs python docs/architecture/render_pptx.py` に。
2. `tests/test_analytics.py` の check を `"uv run --only-group docs python docs/architecture/render_pptx.py" in _arch_readme` に。
3. 「描ける」check を足す: `subprocess.run(["uv", "run", "--frozen", "--only-group", "docs", "python", "-c", "import pptx; import docs.architecture.render_pptx as m; …"], timeout=120)` で import と描画関数 2 本を scratch（`tempfile.mkdtemp()`）へ描いて `returncode == 0` と出力ファイルの存在。`uv` が無い環境では `shutil.which("uv") is None` で skip ではなく check を落とす（この repo は uv 前提）。`render_pptx.py` に出力先を引数で受ける口が無ければ、`-c` の中で `os.chdir(scratch)` してから呼ぶ。

**やらないこと。** FAQ の本文は変えない。`render_pptx.py` の描画の中身は変えない。`heal-main` の verdict の式は変えない。

## 変更対象ファイル

| ファイル | 件 | 何を |
|---|---|---|
| `docker/compose/up.sh` | A | 1 行 |
| `tests/test_local_compose.py` | A | check 1 つ |
| `app/temporal/rules.py` / `app/agentcore/topology.py` | B | `reconnected` |
| `tests/test_agentcore.py` | B | check 1 つ（既存 2 つは緑のまま） |
| `tests/test_analytics.py` | C, D | `_faq_heads` の 5 点と check、pptx の check |
| `docs/architecture/README.md` | D | 1 行 |

## 再利用するもの

- `docker/compose/check.sh:112` の `grep -F --` の形。
- `rules.py` の `view` / `largest` / `lost`。
- `tests/test_analytics.py` の `_faq_heads` / `_gh_slug` と check の部品。

## 実装ステップ

1. A。`bash -n docker/compose/up.sh`、test_local_compose 緑。1 commit。
2. B。test_agentcore に赤の check → rules.py と topology.py を直して緑。1 commit。
3. C。5 点それぞれ赤 → 緑。1 commit。
4. D。README と check。描画の check は `uv run --only-group docs` が手元で通ることを実測。1 commit。
5. `build.md`（頭は `実装モデル: opus-5.5 / effort: high`。テストの出力、セルフレビュー）。

## 検証方法（期待出力まで）

- `uv run --frozen python3 tests/test_local_compose.py` / `test_agentcore.py` / `test_analytics.py` が `失敗 0`（件数は build の冒頭で実測。test_analytics は 528 + 足したぶん）。
- `bash -n docker/compose/up.sh` が無言。
- B: 手元で `python3 -c` で `rules.py` の impact 関数に「a-b / c-d の 2 かたまり（同点）→ b-c を足す」を食わせ、`reconnected == []`。「a 孤立 → a-b を足す」で `reconnected == ["a"]`。
- C: `_faq_heads` に `"  ## &amp; x"` を食わせて slug が `-x`（GitHub は `&` を落とす。`_gh_slug` の実装に合わせる）、`"    ## x"` は拾わない、```` ```py ```` の中の `#` は拾わない、列 0 の ```` ``` ```` の中で `    ``` ` が来ても閉じない。
- D: `uv run --frozen --only-group docs python docs/architecture/render_pptx.py` が通り pptx が出来る（出力先は README のとおり。git に入れない）。

## 未確定事項とリスク

1. B の定義は実装で確定した（設計方針 B。当初の「孤立から復帰」の式は既存の check を落とした）。残る差は変更 2 件以上のときの繰り上がりで、範囲外にした。
2. D の描画 check は python-pptx の import に数秒、描画に数秒。test_analytics が 10 秒ほど遅くなる。
3. C の tab の扱いは GitHub の仕様（CommonMark の tab stop 4）に合わせるが、FAQ に tab は無いので実害は無い。
