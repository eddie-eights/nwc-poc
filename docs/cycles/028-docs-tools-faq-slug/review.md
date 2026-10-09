# docs の道具と FAQ の見出しの検査を片付ける（028）のレビュー記録

## Round 1

cold review に依頼した（opus）。対象は fb409cf。以下は返ってきた本文をそのまま連結したもの。

## サマリ

対象は、docs の道具と FAQ の見出しの検査を片付ける（028）。チェックアウトは review-028（detached fb409cf）で、merge-base は 1dc9645。4 commit、7 ファイル、+1185 / -13 行。うち 834 行は `docs/architecture/render_pptx.py` を写したもので、229 行は build.md。

全体の評価: 設計の A（道具をリポジトリに写す）と B（FAQ の見出しを縛り、フェンスの判定を GitHub に寄せる）は、どちらも設計どおりに入っている。描き直した pptx は、commit 済みのものと zip の中身まで一致した。テストは自分で走らせて緑だった。Must fix は 0 件。B の縛りには、見出しを作為的に書いたときに検査が緑のまま GitHub とずれる穴が残る。これは実装者のセルフレビューでも挙がっていて、まだ直っていない（Should fix 1・2）。設計の文言と実装のずれが 2 点あり、build.md には書いてあるが design.md には反映されていない（Should fix 3）。

### 見た観点 / 見ていない観点

見た観点

- **design.md との整合性**
  - 変更ファイルを、設計の「変更対象ファイル」と突き合わせた（`git diff origin/main...fb409cf --stat`）。
  - `diff /Users/eight/Documents/repo/deck/render_pptx.py docs/architecture/render_pptx.py` を自分で打った。違いは `:6-7` の docstring 2 行だけで、文言は設計 A.1 のとおりだった。
  - README `:10`、pyproject の docs のグループとコメント、`_gh_slug` の上のコメントも、設計の文と照らした。
- **correctness（A）**
  - 2 本の `.deck.md` を、スクラッチの venv で描き直した。
    - `uv run --frozen --group docs python docs/architecture/render_pptx.py docs/architecture/architecture-{managed,oss}.deck.md -o <scratch>/…`
    - どちらも rc=0。
  - unzip して `diff -rq` で比べた。commit 済みの pptx と比べて、差は 0 件だった（slide*.xml 各 10 枚だけでなく、docProps を含む全部のメンバーが一致）。
  - 別の cwd（scratch）から絶対パスで打っても rc=0 だった。
  - `uv lock --check` は rc=0（Resolved 89 packages）。
- **correctness（B）**
  - `_faq_heads` / `_faq_heads_ok` / `_gh_slug` を、テストファイルから抜き出して probe した（scratch の `rv/probe.py`）。
    - 実物の FAQ は 93 本で `ok=True` だった。
    - 最初の `###` は `:34` で、コードブロックの外にある。
  - 端の入力を 6 つ当てた（下の Should fix と Nit に結果）。
- **テスト（自分で実行した結果）**
  - `UV_PROJECT_ENVIRONMENT=<scratch>/rv/.venv uv run --frozen --group dev --group web --group docs python tests/test_analytics.py` → `通過 523 / 失敗 0`。028 の 4 件は全部 ok。
  - `… --group dev --group web python tests/test_alerts.py` → `通過 168 / 失敗 0`。「check.sh の構文検査は .py のあるディレクトリを全部見る」も ok だった。
  - `python3`（3.14.7）で `ast.parse(docs/architecture/render_pptx.py)` が通ることを確かめた（check.sh の手順 3 に当たる）。
- **今の origin/main（44279f4。026 が入った後）とのマージ**
  - `git merge-tree --write-tree origin/main fb409cf` はコンフリクトなしだった。
  - できた木（96cf4c4）を `git archive` でスクラッチに展開し、`tests/test_analytics.py` を走らせた。結果は `通過 525 / 失敗 0`。
- **security / data loss**
  - 道具は手元で打つ CLI で、入力はコマンドラインの引数だけ。外部への送信もシークレットも無い。
  - 書き出し先は `-o` で、README は commit 済みの pptx への上書きを意図して書いている。
- **API compatibility**
  - `_faq_toc_ok` の挙動を確かめた。呼び出し元は check 1 つで、実物の FAQ に対して見出しは 93 本のまま、check は ok のまま。

見ていない観点

- **`bash ops/check.sh` の全体は走らせていない。** terraform の validate が、チェックアウトの中に `.terraform` を作る恐れがあり、「チェックアウトに何も書かない」に反するため。手順 3（構文）と test_alerts / test_analytics は個別に確かめた。それ以外のテストの件数が変わらないことは、build.md の記録に頼っている。
- **GitHub 上での FAQ の描画とアンカー**は見ていない（ネットワークを使っていない）。GFM の挙動の判断は、仕様の理解によるもの。
- **描いた pptx を PowerPoint / Keynote で開いての目視**はしていない。XML の一致で代えた。
- **type safety** は対象外（型注釈の変更は無い）。

## Must fix

None

## Should fix

- **S1** [correctness] `tests/test_analytics.py:1621`（`_faq_heads_ok` の `re.sub(r"`[^`]*`", "", h)`）
  - **何が起きるか**: コードを空文字で消してから `_` の両隣を見ている。そのため、コードに接した `_` が「英数字に挟まれている」と判定されて通る。
  - **再現**（scratch の `rv/probe.py`）: `### Q. x`a`_b_`c`y` を当てると、`ok=True` で `_gh_slug='q-xa_b_cy'` になる。GFM では `` `a` `` の直後の `_b_` は強調として描かれ、GitHub のアンカーからは `_` が落ちる。
  - **破綻シナリオ**: この形の見出しを書くと、検査は緑のまま、GitHub で目次のリンクが当たらなくなる。設計 B.2 は「強調の記法を使わない」を縛るつもりでいるので、その縛りに穴が開いている。
  - **直し方**: 置換先を空白にする（`re.sub(…, " ", h)`）。直すのは 1 行で、設計の範囲内。
  - **Should にした理由**: 今の FAQ の 93 本には該当する見出しが無く、作為的な書き方をしないと起きない。マージしても今は壊れないので Must ではない。ただ、縛りの意図に穴があるのは直すべき。実装者のセルフレビュー 1 と同じ指摘で、まだ直っていない。
- **S2** [correctness] `tests/test_analytics.py:1573-1577`（`_faq_heads` の閉じの判定）
  - **何が起きるか**: 閉じの行を `line.strip()` で見ているので、字下げの深さを見ていない。箇条書きの中で開いたフェンス（2 空白）を、列 0 の `` ``` `` で閉じたと見なす。
  - **再現**: `- item\n  ```\n  code\n```\n### Q. B\n` を当てると、`heads=[(3, 'Q. B')]` で `ok=True` になる。GFM では列 0 の `` ``` `` は箇条書きの外で新しいフェンスを開く。そのため `### Q. B` 以降はコードになる。
  - **破綻シナリオ**: FAQ の `:61` `:194` `:215`（箇条書きの中のフェンス）の閉じを列 0 に書き間違えると、検査は緑のまま、GitHub では後半がコードになり、アンカーが消える。設計 B.1 の「フェンスの判定を GitHub に寄せる」の意図とずれる。
  - **Should にした理由**: 起きれば描画が大きく崩れるので、人が気づきやすい。今の FAQ でも起きていない。実装者のセルフレビュー 3 と同じで、まだ直っていない。
- **S3** [design.md との整合性] `docs/cycles/028-docs-tools-faq-slug/design.md:65` と `:79-85`
  - **何がずれているか**: 実装は、design.md の文言から 2 点外れている。
    - 1 点目: `_` を「英数字に挟まれていれば許す」にした（design は「コードの外に `_` が無い」）。
    - 2 点目: `ops/check.sh:53` を変えた（design の変更対象ファイルに無い）。
  - **ずれの妥当性**: どちらも理由はもっともで、build.md の「設計との違い 1・2」に記録がある。
    - 1 点目: 設計の前提「今の 93 本は縛りを満たしている」は事実として誤り。`:1204` と `:1448` の 2 本が、コードの外で `_` を使っている。
    - 2 点目: 足さないと `tests/test_alerts.py:1446-1450` が落ちる。私の実行でも、足した状態で ok だった。
  - **残る問題**: 仕様の正本は design.md なのに、design.md 本文の B.2 と「変更対象ファイル」が実装と食い違ったまま残る。
  - **直し方**: review.md で差を確定させるか、design.md に追記する（どちらにするかは PM の判断）。
  - **Should にした理由**: コードの挙動は壊れない。ただ、後から design.md だけを読んだ人が「`_` は全面禁止のはず」と誤読する。

## Nit

- **N1** [correctness] `_faq_heads_ok` の縛りの外に、検査が緑のまま GitHub とずれる書き方がほかにもある。設計のリスク 3（縛りの外の差は残る）の範囲なので、Nit とした。
  - **HTML の実体参照**: `### Q. A &amp; B` を当てると `ok=True` で、`_gh_slug='q-a-amp-b'` になる。GitHub は `&` に描いてから落とすので、`q-a--b` になるはず。
  - **1〜3 空白の字下げをした ATX の見出し**: `  ### Q. hidden` は `_faq_heads` に数えられない。GitHub では見出しになり、同名の見出しと `-1` がずれる。
- **N2** [correctness] `tests/test_analytics.py:1578`
  - **何が起きるか**: 行頭の `` ```x``` ``（info string に `` ` `` を含むので、GFM ではフェンスではなくインラインのコード）をフェンスの開きと見なす。以後の見出しが全部消える。
  - **再現**: `heads=[]`。
  - **Nit にした理由**: この場合は `_faq_toc_ok` の節の数（12）の条件で落ちるので、黙って弱まることはない。
- **N3** [docs] `docs/architecture/README.md:10` の `uv run --group docs …`
  - **何が起きるか**: uv の既定の `default-groups` は `["dev"]`。そのため、このコマンドは dev のグループ（strands-agents など）も一緒に入れる。描くことはできる。
  - **代案**: pptx を描くだけなら、`--only-group docs` の方が軽い。好みの範囲なので Nit とした。
- **N4** [missing tests] `tests/test_analytics.py:1643-1647`
  - **何が見ていないか**: この検査は、ファイルがあることと文字列を見るだけで、道具が描けること（import できる、2 本が描ける）は見ていない。
  - **Nit にした理由**: 設計 A.4 が求める範囲どおりで、描けることは検証 1〜2 で手で確かめる設計になっている。
- **N5** [記録] `docs/cycles/028-docs-tools-faq-slug/build.md:5`
  - **何がずれているか**: 冒頭が `実装モデル: fable-5.1 / effort: high` になっていて、設計の実装ステップ 3（頭は `実装モデル: opus-5.5 / effort: high`）と違う。
  - **Nit にした理由**: 引き継いだ経緯が `:7` に書いてあるので、読み手は困らない。
- **N6** [simplification] `tests/test_analytics.py:1591-1593`
  - **何が起きるか**: `-1` `-2` を付ける分岐は、`_faq_heads_ok` が同名の見出しを禁じたので、実物の FAQ では通らなくなった。
  - **Nit にした理由**: 残しても害は無い。

## 良かった点

- **道具の写し**: 設計の言うとおり、docstring の 2 行だけを変えて写した（My Repo と `diff` して確かめた）。描き直しは、commit 済みの pptx と zip の全メンバーで一致した。pptx を commit しない判断の根拠が確かだった。
- **縛りの効き方を確かめる仕組み**: 縛りを 1 つずつ外すと落ちることを、写しへの注入（M0〜M14）で確かめてある。実物の FAQ は書き換えず、写しに足す形（`_faq_with`、`_faq_fenced`）で赤を作っている。
- **設計からの逸脱の扱い**: 逸脱を build.md に理由付きで明記してある。`_` の緩め方も「FAQ を変えない」約束を守りながら、強調にならない形だけを許す、筋の通った線引きになっている。
- **check.sh**: 構文検査に `docs` を足すとき、既存の検査（test_alerts）を緩めずに直している。
- **依存の管理**: python-pptx を docs のグループに分け、dev / web の環境に混ざらないようにしてある。私有 API を触るので版を固定する理由も、pyproject のコメントに残してある。

## ユーザーへの質問

None

### PM の判定

- Must fix: 0 件。
- Should fix: 3 件とも直した（cfca5fa と次の記録の commit）。
  - S1: `_faq_heads_ok` でコードを空白に置き換えて消す。
  - S2: `_faq_heads` で、閉じの字下げが開きより浅い行は閉じと見なさない。
  - S3: design.md を正本として実装に揃えた（B.1 / B.2 と「変更対象ファイル」に「PM の指示で追記」の行を足した。他の部分は変えていない）。
- セルフレビュー 2（cold review では挙がっていない）も直した: `` ` `` が奇数個の見出しと `` `` `` を含む見出しを `_faq_heads_ok` で落とす。
- Nit: 6 件とも直さない（N3 の `--only-group docs`、N5 の build.md のモデル行、N6 の `-1` の分岐を含む）。
- 1〜3 にはそれぞれ写しに注入する check を足した（`tests/test_analytics.py` 523 → 526）。実物の FAQ は変えていない。

各 Should の再現が直した後に落ちること（scratchpad の `v1010/028/r2/probe.py`。直す前は `git show fb409cf:tests/test_analytics.py` の写しの関数、直した後は cfca5fa の関数に当てた）:

```
== 直す前（fb409cf）
実物の FAQ: 見出し 93 本（### 80 本） / ok=True
S1: '### Q. x`a`_b_`c`y' -> ok=True
SR2 奇数かつ ``: '### Q. `a _x_ ``' -> ok=True
SR2 奇数だけ: '### Q. `a_b` `' -> ok=True
SR2 `` だけ: '### Q. `` a_b ``' -> ok=True
S2: '- item\n  ```\n  code\n```\n### Q. B\n' -> heads=[(3, 'Q. B')]
S2 対照（同じ字下げで閉じる）: '- item\n  ```\n  code\n  ```\n### Q. B\n' -> heads=[(3, 'Q. B')]
== 直した後（作業ツリー）
実物の FAQ: 見出し 93 本（### 80 本） / ok=True
S1: '### Q. x`a`_b_`c`y' -> ok=False
SR2 奇数かつ ``: '### Q. `a _x_ ``' -> ok=False
SR2 奇数だけ: '### Q. `a_b` `' -> ok=False
SR2 `` だけ: '### Q. `` a_b ``' -> ok=False
S2: '- item\n  ```\n  code\n```\n### Q. B\n' -> heads=[]
S2 対照（同じ字下げで閉じる）: '- item\n  ```\n  code\n  ```\n### Q. B\n' -> heads=[(3, 'Q. B')]
```

足した check の条件を同じ 2 つに当てた結果（scratchpad の `v1010/028/r2/red_checks.py`）:

```
== 直す前（fb409cf）
S1 の check: False
セルフレビュー 2 の check: False
S2 の check: False
== 直した後（作業ツリー）
S1 の check: True
セルフレビュー 2 の check: True
S2 の check: True
```

直した後の `tests/test_analytics.py` は `通過 526 / 失敗 0`、`tests/test_alerts.py` は `通過 168 / 失敗 0`、`bash ops/check.sh` は rc=0 で `すべて通過`（出力は build.md の Round 2）。
