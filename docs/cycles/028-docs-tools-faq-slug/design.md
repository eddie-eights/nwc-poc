# docs の道具と FAQ の見出しの検査を片付ける（028）

設計: PM(opus-5.5) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

020 と 021 のセルフレビューで出て BACKLOG に残っていた、docs まわりの 3 件を片付ける。2 件は直し、1 件は要らないと決めて閉じる。

| # | BACKLOG の行 | 出所 | 扱い |
|---|---|---|---|
| A | architecture の pptx を描く道具をリポジトリに写す | 021 のセルフレビューの Should fix 3 | 直す |
| B | `tests/test_analytics.py` の `_gh_slug` を GitHub の見出しアンカーの作り方に揃えるか、FAQ の見出しに使える文字を縛る | 020 のセルフレビューの Should fix 4 | 直す |
| — | 020 で本文から落とした経緯 12 件を各 docs の `## 経緯` に戻すか、要らないと決める | 020 のセルフレビューの Should fix 3 | 要らないと決める（BACKLOG で閉じる。PM が書く） |

経緯 12 件を戻さない理由: 文は git の 3a06428 より前に残っていて、いつでも引ける。docs は「いまどうなっているか」を書く場所で、経緯はサイクルの `design.md` / `review.md` が正本になっている（020 で本文から落としたのもこの方針）。

### 調査で分かった事実（2026-10-09、origin/main 3e06e4c）

**A. pptx を描く道具**

- `docs/architecture/README.md:9-11`。
  ```
  ソースは `*.deck.md`。
  描くのは `~/Documents/repo/bin/render-pptx docs/architecture/architecture-managed.deck.md -o docs/architecture-managed.pptx`（個人リポジトリの道具。無ければ pptx をそのまま使う）。
  `.deck.md` を直したら pptx も描き直して一緒に commit する。
  ```
- 道具の本体は My Repo の `/Users/eight/Documents/repo/deck/render_pptx.py`（834 行）。依存は python-pptx だけ。画像は `resolve_image` が cwd からの相対で読むが、nwc-poc の 2 本の `.deck.md` は画像を使っていない。
- 包む `bin/render-pptx` は、My Repo の `.venv` に `python-pptx==1.0.2` を入れて打つ。版を固定する理由はコメントのとおり「render_pptx.py は python-pptx の私有 API (run._r / p._pPr / cell._tc / table._tbl) を触るのでバージョンを固定する。上げるときは実際に .pptx を開いて確認すること」。
- `pyproject.toml:9-22` の `[dependency-groups]` は `dev` と `web` の 2 つ。`[tool.uv]` は `package = false`。
- 描く対象は `docs/architecture/architecture-managed.deck.md` → `docs/architecture-managed.pptx` と、`architecture-oss.deck.md` → `docs/architecture-oss.pptx`。
- `render-pptx` / `render_pptx` を見るテストは無い（`grep -rn` で 0 件）。

**B. FAQ の見出しのアンカー**

- `tests/test_analytics.py:1553` が `docs/faq-fukuda-nwc-poc.md` を読む。`:1561-1564` の `_gh_slug` は、小文字にして、`-` `_` 空白と Unicode の L / N / M 以外を消し、空白を `-` にする。`:1565-1590` の `_faq_toc_ok` は見出しのアンカーを作り（同じ名前は `-1`, `-2`）、12 の節の直下の目次とアンカーが当たるかを見る。
- `_faq_toc_ok` のコードブロックの判定は `line.startswith("```")` だけ。字下げしたフェンスと `~~~` のフェンスを見ないので、その中の `#` で始まる行を見出しに数えてしまう。
- 実測（2026-10-09、scratchpad の `faq_heads.py`）
  - 見出しは 93 本。`_gh_slug` で作ったアンカーの重なりは 0。
  - 丸数字などの `No` の文字、`_` 以外の `Pc` の文字は 0。
  - 記法の文字を含む見出しは 4 本で、どれもコード（`` ` ``）の中だけ（`:206` `logging host <IPアドレス | ホスト名>`、`:330`、`:687`、`:768`）。コードの中の文字は GitHub でもそのまま文字として扱われ、`` ` `` は `_gh_slug` でも GitHub でも消えるので差は出ない。
  - 字下げしたフェンスは 6 行（`:61` `:63` `:194` `:196` `:215` `:219`。箇条書きの中のコードブロック）。中に `#` で始まる行は無い。`~~~` は 0。
- BACKLOG の行が挙げる GitHub との差は、丸数字、`Pc` の文字、同名の見出しの `-1`、`~~~` のフェンス、リンクと強調の記法。いまの FAQ では差が出ない。GitHub での描画は未確認。

## 設計方針

**A. 道具を `docs/architecture/render_pptx.py` に写し、`uv run` で打つ**

1. `/Users/eight/Documents/repo/deck/render_pptx.py` を `docs/architecture/render_pptx.py` にそのまま写す（中身は変えない）。docstring の「リポジトリ直下の .venv に入っている」と「design-system/document.css と揃えてある」は nwc-poc に合わないので、この 2 行だけ「依存は python-pptx だけ（pyproject の docs のグループ）」「デザイントークンは My Repo の design-system/document.css から写した」に直す。
2. `pyproject.toml` の `[dependency-groups]` に `docs = ["python-pptx==1.0.2"]` を足し、上に版を固定する理由をコメントで書く（私有 API を触る。上げるときは pptx を開いて確かめる）。`uv lock` で `uv.lock` を更新する。
3. `docs/architecture/README.md:10` を次に替える。
   ```
   描くのは `uv run --group docs python docs/architecture/render_pptx.py docs/architecture/architecture-managed.deck.md -o docs/architecture-managed.pptx`（OSS 版は `architecture-oss`）。
   ```
4. テストを 1 つ足す（`tests/test_analytics.py` の docs の検査の近く）: `docs/architecture/render_pptx.py` があり、`pyproject.toml` の docs のグループが `python-pptx==1.0.2` で、README が `uv run --group docs python docs/architecture/render_pptx.py` を書き、`~/Documents/repo` を書いていない。
5. 2 本を描き直し、`ppt/slides/*.xml` を今 commit してある pptx と突き合わせる（下の検証）。一致すれば pptx は commit しない（描き直しても同じものしか出ない）。違えば止めて報告する。

**B. FAQ の見出しに使える文字を縛り、フェンスの判定を GitHub に寄せる**

`_gh_slug` を GitHub の規則に完全に揃えるのはやらない（GitHub の実装は公開の仕様が無く、追いかけると壊れやすい）。代わりに、差が出る書き方を FAQ で使えなくする。

1. `_faq_toc_ok` のフェンスの判定を、字下げと `~~~` も見る形にする: `re.match(r"^\s*(`{3,}|~{3,})", line)` で開き、同じ文字の並びで閉じる。
   - 閉じの字下げが開きより浅い行は閉じと見なさない（箇条書きの中のフェンスは列 0 の `` ``` `` では閉じない。GFM と同じ）（cold review の Should fix を受けた PM の指示で追記）。
2. 見出しの検査を 1 つ足す（`_faq_toc_ok` の検査の隣）。FAQ の全部の見出しで次を満たす。
   - `No` の文字（丸数字など）が無い。
   - `_` 以外の `Pc` の文字が無い。
   - コード（`` `…` ``）の外に `[` `]` `*` `_` `<` `>` が無い（リンク、強調、HTML の記法を使わない）。
     - コードの外でも ASCII 英数字に挟まれた `_` は許す（`:1204` `raw_telemetry`、`:1448` `event_id` が該当。強調にならない形だけ許す）（cold review の Should fix を受けた PM の指示で追記）。
     - `_` の両隣を見るときは、コードを空文字ではなく空白に置き換えて消す（コードに接した `_` を英数字に挟まれたと見なさない）（cold review の Should fix を受けた PM の指示で追記）。
     - `` ` `` が奇数個の見出しと、`` `` `` を含む見出しは使わない（`` ` `` の対応を GFM と同じに取れない）（セルフレビュー 2 を受けた PM の指示で追記）。
   - `_gh_slug` で作ったアンカーが重ならない（同名の見出しの `-1` を使わない）。
3. `_gh_slug`（`:1561-1564`）の上のコメントに、揃えきらずに縛ることにした理由と、縛っている文字を書く（上の 2 を指す）。
4. 赤→緑: 足す検査は、FAQ の写しに丸数字の見出し、`[リンク](#x)` の見出し、同名の見出し、`~~~` の中の `# …` を 1 つずつ入れて落ちることを、テストの中の小さな検査で確かめる（実物の FAQ は書き換えない）。

**やらないこと。**

- My Repo の `deck/render_pptx.py` と `bin/render-pptx` は消さない・変えない（ほかの資料が使っている）。
- `.deck.md` の中身と pptx は変えない。
- FAQ の本文と見出しは変えない（いまの 93 本は縛りを満たしている）。
- 経緯 12 件は戻さない（背景に理由）。

## 変更対象ファイル

| ファイル | 件 | 何を |
|---|---|---|
| `docs/architecture/render_pptx.py` | A | 新しく写す（docstring の 2 行だけ直す） |
| `pyproject.toml` | A | `docs` のグループと理由のコメント |
| `uv.lock` | A | `uv lock` の結果 |
| `docs/architecture/README.md` | A | `:10` |
| `tests/test_analytics.py` | A / B | 道具の検査、フェンスの判定、見出しの縛りの検査、`_gh_slug` のコメント |
| `ops/check.sh` | A | `:53` の `.py` の構文検査の `find` に `docs` を足す（足さないと `tests/test_alerts.py:1446-1450` が落ちる）（cold review の Should fix を受けた PM の指示で追記） |

## 再利用するもの

- My Repo の `deck/render_pptx.py`（そのまま写す）と、`bin/render-pptx` の版を固定する理由のコメント。
- `tests/test_analytics.py` の `_faq`、`_gh_slug`、`_faq_toc_ok`、`check`。

## 実装ステップ

1. A: 道具を写し、pyproject と uv.lock、README を直し、検査を足す。描き直して突き合わせる。1 commit。
2. B: 検査を足して赤を確かめ、フェンスの判定とコメントを直して緑にする。1 commit。
3. `build.md` を書く（頭は `実装モデル: opus-5.5 / effort: high`。突き合わせの出力、赤と緑の出力、セルフレビュー）。

## 検証方法（期待出力まで）

- `uv run --group docs python docs/architecture/render_pptx.py docs/architecture/architecture-managed.deck.md -o <scratch>/managed.pptx` が終了コード 0。OSS 版も同じ。
- 描いた pptx と commit してある pptx を unzip し、`ppt/slides/slide*.xml` を 1 枚ずつ `cmp` して全部一致（各 10 枚）。`docProps/core.xml` の時刻は比べない。
- `uv run --group dev --group web python tests/test_analytics.py` が「通過 N / 失敗 0」（2026-10-09 は 519。A で 1、B で 1 以上増える）。
- `bash ops/check.sh` が全部通る（ほかのテストの件数は変わらない）。
- `grep -n "Documents/repo" docs/architecture/README.md` が 0 行。
- `uv lock --check` が通る。

## 未確定事項とリスク

1. 描き直した pptx が commit してあるものと一致しないかもしれない（commit した時点の My Repo の `render_pptx.py` が今と違えばずれる）。ずれたら原因（道具の版か、`.deck.md` の直し忘れか）を `build.md` に書いて止める。
2. python-pptx 1.0.2 が今の Python（`requires-python = ">=3.13,<3.14"`）で入るかは未確認。入らなければ止めて報告する。
3. 縛りは GitHub の規則に揃えたことにはならない。縛りの外の差（たとえば絵文字や全角の記号の扱い）は残る。GitHub での描画は未確認のまま。
4. `uv.lock` に python-pptx と依存（lxml、Pillow、XlsxWriter、typing-extensions）が入る。docs のグループを使わない `uv run --group dev` には入らない。
