# docs の道具と FAQ の見出しの検査を片付ける（028）の実装記録

## Round 1

実装モデル: fable-5.1 / effort: high

A と B のコード（afc47f2 / ce5c3f1 / fd41595）は前の実装者（opus-5.5 / effort: high）が書いた。利用上限で止まったので、fable-5.1 が引き継いで検証を全部打ち直し、この記録とセルフレビューを書いた。

ブランチ feat/028-docs-tools-faq-slug（origin/main 1dc9645 から）。commit は A afc47f2 / B ce5c3f1 / A の続き fd41595（check.sh）。AWS には触っていない（AWS に触れる変更は無い。**AWS では未確認**）。My Repo は読んだだけ。

### 変更ファイル

- A
  - `docs/architecture/render_pptx.py`: My Repo の `deck/render_pptx.py`（834 行）を写した。違いは docstring の 2 行（`:6-7`）だけ（`diff` で確かめた）
  - `pyproject.toml:23-27`: `docs = ["python-pptx==1.0.2"]` と版を固定する理由のコメント
  - `uv.lock`: `uv lock` の結果（+54 行。lxml 6.1.3 / python-pptx 1.0.2 / xlsxwriter 3.2.9。Pillow と typing-extensions は web のグループで既に入っていた）
  - `docs/architecture/README.md:10`: 設計の文のとおり
  - `tests/test_analytics.py:1638-1647`: check 1 つ（519 → 520）
  - `ops/check.sh:53`: 構文検査の `find` に `docs` を足した（下の「設計との違い」2）
- B
  - `tests/test_analytics.py:1561-1564`: `_gh_slug` の上のコメント
  - `tests/test_analytics.py:1568-1585`: フェンスの判定を `_faq_heads` に切り出して直した（`_faq_toc_ok` はこれを呼ぶ）
  - `tests/test_analytics.py:1614-1637`: `_faq_heads_ok` と check 3 つ（520 → 523）。実物の FAQ への縛り、写しで落ちること、フェンス

### 設計との違い

1. **`_` はコードの外でも、ASCII の英数字に挟まれていれば許す。**
   設計は「コードの外に `_` が無い」と書き、「いまの 93 本は縛りを満たしている」としている。実際は 2 本がコードの外で `_` を使っている（`docs/faq-fukuda-nwc-poc.md:1204` `Q. raw_telemetry（旧 snmp_metrics）って何？`、`:1448` `…番号（event_id）が…`）。設計の文言どおりに縛ると実物の FAQ で落ちる（下の注入 M7）。FAQ は変えない約束なので、強調にならない形（GFM では英数字に挟まれた `_` は強調の区切りにならない）だけ許した。全角の `＿` などは Pc で落ちる。
2. **`ops/check.sh:53` の `find` に `docs` を足した（設計の変更対象ファイルに無い）。**
   `docs/architecture/render_pptx.py` を追跡したので、`tests/test_alerts.py:1446-1450` の「check.sh の構文検査は .py のあるディレクトリを全部見る」が落ちた（下の check.sh の赤）。検査を緩めずに `docs` を足した。
3. **A は 2 commit になった（設計は 1 commit）。**
   check.sh の直しは A を commit した後に分かった。rebase で畳まずに続きの commit にした。
4. **`~~~` の確かめ方は「見出しに数えない」で縛った。**
   設計の赤→緑の「`~~~` の中の `# …` を入れて落ちる」は、直す前のコードに対しての話と読んだ。写しに `~~~` の中の `## 偽の節` を足し、直す前は落ち（下の注入 M0）、直した後は見出しの並びが実物と同じになることを check にした。
5. **写しで落とす例を設計の 3 つ（丸数字・リンク・同名）より増やした。**
   強調（`_強調_` と `*強調*`）、HTML（`<br>`）、全角の `＿`（Pc）。縛りのどれを外しても check が落ちるようにするため（下の注入 M1〜M8）。
6. **閉じるフェンスは「開いたのと同じ文字だけで、開いた長さ以上」の行にした。**
   設計の「同じ文字の並びで閉じる」を GFM に寄せて読んだ。info string の付いた行（```` ```python ````）では閉じない。

### 赤（直す前に落ちることを見た。2026-10-10 に fable-5.1 が打ち直した）

A: check の条件を origin/main 1dc9645 のファイル（`git show origin/main:…`）に当てた（scratchpad の `red_a.py`。リポジトリのファイルは変えない）

```
origin/main に render_pptx.py がある: False
origin/main の pyproject の docs: None
origin/main の README に uv run … がある: False
origin/main の README に Documents/repo がある: True
AssertionError: 構成の pptx は docs/architecture/render_pptx.py で描き、pyproject の docs のグループ（python-pptx==1.0.2）で打つ。README は個人リポジトリの道具を指さない（028）
```

B: 下の注入 M0（フェンスの判定を直す前の `line.startswith("```")` に戻す）が、フェンスの check で落ちる。

check.sh: `tests/test_alerts.py:1446-1450` の条件を origin/main の `ops/check.sh`（`find` に `docs` が無い）に当てた（scratchpad の `alerts_red.py`）

```
origin/main の check.sh の find: ['app', 'docker', 'ops', 'tests']
追跡している .py の先頭ディレクトリ: ['app', 'docs', 'ops', 'tests']
AssertionError: check.sh の構文検査は .py のあるディレクトリを全部見る（app/splunk/ のアラートアクションと app/graph/ の Lambda も。どちらも app/ の下）
```

### 注入（縛りを 1 つずつ外すと check が落ちる。2026-10-10 に fable-5.1 が打ち直した）

scratchpad の `mut028.py`。`tests/test_analytics.py` の `:1553` と `:1561-1647` を写し（ROOT は worktree の絶対パス）、1 か所ずつ書き換えて写しを打つ（リポジトリのファイルは変えない）。

```
M- 写しそのまま（縛りを外さない）: rc=0 通過 5 / 失敗 0
M0 直す前のフェンスの判定（line.startswith の切り替えだけ）: rc=1 AssertionError: FAQ: 字下げしたフェンスと ~~~ のフェンスの中の # の行は見出しに数えない。開いたのと同じ文字で、開いた長さ以上の並びだけの行で閉じる（028）
M1 No を許す: rc=1 AssertionError: FAQ の見出しの縛りは、写しに足した丸数字・[リンク](#x)・同名の見出し・強調・HTML・全角の＿をそれぞれ落とす（028）
M2 _ 以外の Pc を許す: rc=1 AssertionError: FAQ の見出しの縛りは、写しに足した丸数字・[リンク](#x)・同名の見出し・強調・HTML・全角の＿をそれぞれ落とす（028）
M3 [ ] を許す: rc=1 AssertionError: FAQ の見出しの縛りは、写しに足した丸数字・[リンク](#x)・同名の見出し・強調・HTML・全角の＿をそれぞれ落とす（028）
M4 * を許す: rc=1 AssertionError: FAQ の見出しの縛りは、写しに足した丸数字・[リンク](#x)・同名の見出し・強調・HTML・全角の＿をそれぞれ落とす（028）
M5 < > を許す: rc=1 AssertionError: FAQ の見出しの縛りは、写しに足した丸数字・[リンク](#x)・同名の見出し・強調・HTML・全角の＿をそれぞれ落とす（028）
M6 _ を全部許す: rc=1 AssertionError: FAQ の見出しの縛りは、写しに足した丸数字・[リンク](#x)・同名の見出し・強調・HTML・全角の＿をそれぞれ落とす（028）
M7 _ を全部禁じる（設計の文言どおり）: rc=1 AssertionError: FAQ: 見出しに丸数字（No）、_ 以外の Pc、コードの外の [ ] * < > と英数字に挟まれていない _ を使わず、_gh_slug のアンカーが重ならない（028。GitHub と差が出る書き方を縛る）
M8 同名の見出しを許す: rc=1 AssertionError: FAQ の見出しの縛りは、写しに足した丸数字・[リンク](#x)・同名の見出し・強調・HTML・全角の＿をそれぞれ落とす（028）
M9 閉じる文字を問わない: rc=1 AssertionError: FAQ: 字下げしたフェンスと ~~~ のフェンスの中の # の行は見出しに数えない。開いたのと同じ文字で、開いた長さ以上の並びだけの行で閉じる（028）
M10 閉じる長さを問わない: rc=1 AssertionError: FAQ: 字下げしたフェンスと ~~~ のフェンスの中の # の行は見出しに数えない。開いたのと同じ文字で、開いた長さ以上の並びだけの行で閉じる（028）
M11 閉じる行に info を許す: rc=1 AssertionError: FAQ: 字下げしたフェンスと ~~~ のフェンスの中の # の行は見出しに数えない。開いたのと同じ文字で、開いた長さ以上の並びだけの行で閉じる（028）
M12 字下げしたフェンスを見ない: rc=1 AssertionError: FAQ: 字下げしたフェンスと ~~~ のフェンスの中の # の行は見出しに数えない。開いたのと同じ文字で、開いた長さ以上の並びだけの行で閉じる（028）
M13 コードの中も記法の文字を禁じる: rc=1 AssertionError: FAQ: 見出しに丸数字（No）、_ 以外の Pc、コードの外の [ ] * < > と英数字に挟まれていない _ を使わず、_gh_slug のアンカーが重ならない（028。GitHub と差が出る書き方を縛る）
M14 ~~~ を見ない: rc=1 AssertionError: FAQ: 字下げしたフェンスと ~~~ のフェンスの中の # の行は見出しに数えない。開いたのと同じ文字で、開いた長さ以上の並びだけの行で閉じる（028）
```

実物の FAQ の見出しは、直す前と後のフェンスの判定で同じ（scratchpad の `heads_cmp.py`）:

```
直す前 93 本 / 直した後 93 本 / 同じ: True
_ を含む見出し: [(1204, 'Q. raw_telemetry（旧 snmp_metrics）って何？'), (1448, 'Q. Spark のジョブが 3 つに分かれているので、同じイベントでも番号（event_id）が変わることはある？')]
No / Pc の文字を含む見出し: []
コードの外に [ ] * < > を含む見出し: []
```

### 検証（HEAD fd41595。2026-10-10 に fable-5.1 が打ち直した。設計の「検証方法」の 1〜6）

1. 描き直し（終了コード 0）

```
$ uv run --group docs python docs/architecture/render_pptx.py docs/architecture/architecture-managed.deck.md -o <scratch>/r/managed.pptx
書き出した: <scratch>/r/managed.pptx
rc=0
$ uv run --group docs python docs/architecture/render_pptx.py docs/architecture/architecture-oss.deck.md -o <scratch>/r/oss.pptx
書き出した: <scratch>/r/oss.pptx
rc=0
```

2. unzip して `ppt/slides/slide*.xml` を 1 枚ずつ cmp（scratchpad の `cmp_unzip.sh`）。pptx は commit していない（描き直しても同じものしか出ない）

```
managed: commit 済み 10 枚 / 描き直し 10 枚 / cmp 一致 10 枚
oss: commit 済み 10 枚 / 描き直し 10 枚 / cmp 一致 10 枚
不一致 0
rc=0
```

zip のメンバー数も同じ（`unzip -l`: どちらも 77 files / 244415 バイト）。

3. `uv run --group dev --group web python tests/test_analytics.py`（028 の行と末尾だけ抜いた）

```
ok FAQ: 12 の節の直下に、その節の質問（###）を順に並べた目次があり、FAQ の中のアンカーが全部見出しに当たる
ok FAQ: 見出しに丸数字（No）、_ 以外の Pc、コードの外の [ ] * < > と英数字に挟まれていない _ を使わず、_gh_slug のアンカーが重ならない（028。GitHub と差が出る書き方を縛る）
ok FAQ の見出しの縛りは、写しに足した丸数字・[リンク](#x)・同名の見出し・強調・HTML・全角の＿をそれぞれ落とす（028）
ok FAQ: 字下げしたフェンスと ~~~ のフェンスの中の # の行は見出しに数えない。開いたのと同じ文字で、開いた長さ以上の並びだけの行で閉じる（028）
ok 構成の pptx は docs/architecture/render_pptx.py で描き、pyproject の docs のグループ（python-pptx==1.0.2）で打つ。README は個人リポジトリの道具を指さない（028）
通過 523 / 失敗 0
```

519 → 523（A で 1、B で 3）。

4. `bash ops/check.sh`（rc=0。模擬テストの途中の INFO / ERROR のログ行は省いた）

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
…（18 ルート全部 OK）
IaC/terraform/oss/workflow  OK
== 3. スクリプトの構文
bash -n: 28 本
構文エラーなし
== 4. 模擬テスト
通過 161 / 失敗 0    (test_agentcore)
通過 168 / 失敗 0    (test_alerts)
通過 523 / 失敗 0    (test_analytics)
通過 79 / 失敗 0     (test_collectors)
通過 3 / 失敗 0      (test_dashboard_config)
通過 78 / 失敗 0     (test_graph)
通過 7 / 失敗 0      (test_kb_index)
通過 110 / 失敗 0    (test_lab_debug)
通過 138 / 失敗 0    (test_local_compose)
68 項目すべて通過    (test_nautobot)
通過 174 / 失敗 0    (test_oss)
通過 200 / 失敗 0    (test_oss_ops)
通過 66 / 失敗 0     (test_oss_roll)
通過 108 / 失敗 0    (test_stream)
通過 103 / 失敗 0    (test_sync)
通過 327 / 失敗 0    (test_workflow)
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）
すべて通過
```

（括弧のファイル名は並び順から足した。出力そのものには無い）。test_analytics 以外の件数は origin/main 1dc9645 と同じ。

5. `grep -n "Documents/repo" docs/architecture/README.md`

```
rc=1
```

（0 行）

6. `uv lock --check`

```
Resolved 89 packages in 5ms
rc=0
```

設計のリスク 2: python-pptx 1.0.2 は Python 3.13 の uv の環境に入り（`uv sync --group dev --group web --group docs` で lxml 6.1.3 / python-pptx 1.0.2 / xlsxwriter 3.2.9 が入った）、上の 1 で 2 本とも描けた。
設計のリスク 4: `uv export --group dev --group web` に python-pptx は出ない（0 行）。`uv export --group docs` には lxml / pillow / python-pptx / typing-extensions / xlsxwriter が出る。

### セルフレビュー

自分: fable-5.1 / effort: high。反対弁護人: `general-purpose` を `model: fable` で 1 回（文脈を渡した。読み取り専用。返ってきたあと `git status --porcelain -uall` は build.md の `??` だけ）。`/robust` の読み替えで 1 ラウンド + アンチテーゼ + ジンテーゼ。

要望リスト: 実装を design.md に照らす ✅ / 検証方法 1〜6 を打ち直す ✅ / 反対弁護人に文脈を渡す ✅ / 修正は設計の範囲内だけ ✅（Must fix 0 で修正なし）/ サマリーは build.md に短く ✅。

**自分のラウンド（観点と根拠）**

- 設計整合性: 変更ファイルは設計の 5 つ + `ops/check.sh`（違い 2）。`render_pptx.py` は My Repo と `diff` で docstring 2 行だけ違う。README の文は設計のとおり（上の検証 5）。
- correctness（テストが縛っているか）: 上の注入 M0〜M14 を自分で打ち直して全部落ちた。赤（A / check.sh）も origin/main のファイルに当てて落ちた。
- runtime: 描き直しは別の cwd（scratchpad）から絶対パスで打っても rc=0 で、`slide10.xml` は commit 済みと一致（zip 全体の `cmp` は docProps の時刻で違う。設計のとおり比べない）。
- 依存: `uv export --group dev --group web` に python-pptx は 0 行（リスク 4）。
- 問題なしの根拠が「読んだだけ」のもの: `uv.lock` の差分の中身（反対弁護人が `importlib.metadata.requires` で過不足なしを確かめた）。

**反対弁護人の指摘と再現（scratchpad の `anti/probe.py` `anti/probe2.py` を自分で打ち直した）**

1. Should fix [correctness] `tests/test_analytics.py:1621` — コードを消した**あと**の文字列で `_` の隣を見るので、コード（`` ` ``）に隣接した `_` が「英数字に挟まれている」と誤判定されて通る。GFM では `` `a` ``の直後の `_` は強調の始まりになる。
   再現: `### Q. x`a`_b_`c`y` → `heads_ok=True`、描画は `x<code>a</code><em>b</em><code>c</code>y`、`_gh_slug='q-xa_b_cy'` / GitHub 相当 `'q-xabcy'`。
   破綻: この形の見出しを書くと検査は緑のまま GitHub で TOC のリンクが死ぬ。実物の 93 本には無く、作為的な入力なので Must ではない。直し方: `re.sub(r"`[^`]*`", " ", h)`（空白に置き換える）。**直していない**（PM の指示: Should fix は残して報告）。
2. Should fix [correctness] `tests/test_analytics.py:1621` — `` ` `` の対応を「次の `` ` `` と組む」で取るので、GFM の「同じ長さの列と組む」とずれ、奇数個や `` `` `` を含む見出しで強調が通る。
   再現: `### Q. `a _x_ ``` → `heads_ok=True`、描画は `` `a <em>x</em> `` ``、`_gh_slug='q-a-_x_-'` / GitHub 相当 `'q-a-x-'`。
   破綻: 1 と同じ。作為的。直し方: `` ` `` の数が奇数、または `` `` `` を含む見出しを落とす。**直していない**。
3. Should fix（低）[設計整合性] `tests/test_analytics.py:1575` — 箇条書きの中のフェンス（2 空白）を、字下げの無い列 0 の `` ``` `` で閉じたと見なす。GFM では閉じず新しいフェンスが開き、以後の見出しが全部コードになる。
   再現: `- 項目\n  ```\n  code\n```\n### B\n` → `_faq_heads` は `[(3, 'B')]`、markdown-it は fence 2 つで B は見出しにならない。
   破綻: FAQ の `:61` `:194` `:215` の閉じを列 0 に書き間違えると検査は緑のまま GitHub の後半が全部コードになる（目立つので人が気づく）。直し方: 閉じの字下げが開きより浅い行は閉じと見なさない。**直していない**。
4. Should fix [記録] build.md の検証 4 がプレースホルダのままだった → **直した**（上の 4 に check.sh の出力を貼った。反対弁護人が自分で打った結果も `すべて通過`）。
5. Nit [correctness] `tests/test_analytics.py:1582` — ATX の閉じ `#`（`### Q. 同じ？ ##`）を見出しの文字に含める。`_gh_slug` は `q-同じ-`、GitHub は `q-同じ`。再現: `heads_ok: True`。実物に無く動機も薄い。直していない。
6. Nit [correctness] `tests/test_analytics.py:1578` — 開きの `^\s*` は GFM とずれる（4 空白以上、U+3000、info string の `` ` ``）が、飲み込まれた `###` の TOC の行が残るので `_faq_toc_ok` で落ちる。静かに弱まることはない。再現: 4 空白の `` ``` `` のあとの `### B` が飲み込まれる（heads `[(2,'A')]`）。直していない。
7. Nit [correctness] `tests/test_analytics.py:1621` — 日本語の文字に挟まれた `_`（`日本_語`）を落とすのは過剰（GFM では強調にならない。再現: `heads_ok=False`、描画 `Q. 日本_語`、slug 同じ）だが安全側。直すなら lookaround を `\w` に。直していない。
8. Nit [設計整合性] design.md の変更対象に `ops/check.sh` が無い（違い 2 に理由。design.md は書き換えない）。
9. Nit [missing tests] `tests/test_analytics.py:1643` は `render_pptx.py` が描けることを縛らない。設計 A.4 の求める範囲はファイルの存在と文字列まで。描けることは検証 1〜2 で手で確かめる形。直していない。
10. Nit [docs] `docs/architecture/README.md:10` は、コマンドをリポジトリの根で打つこと（`resolve_image` が cwd 基準）を書いていない。2 本の `.deck.md` に `![` は 0 件。直していない。
11. Nit [simplification] `tests/test_analytics.py:1590-1593` の `-1` `-2` を付ける処理は、`_faq_heads_ok` が同名を禁じたので到達しない。直していない。

反対弁護人が「指摘なし」とした観点（写しの作り方 (e)、`_faq_fenced` (f)、`find docs` (h)、`uv` のグループ (j)、`uv.lock` の過不足 (k)、My Repo との diff）は上の自分のラウンドと一致。

**ジンテーゼ（アンチテーゼ前からの差分）**

- 「縛りのどれを外しても check が落ちる（M1〜M14）」は正しいが、それは**縛りの実装が GFM と同じ境界を引いている**ことを意味しない。1〜3 と 5〜7 のとおり、コードの隣接・奇数の `` ` ``・列 0 の閉じ・ATX の閉じ `#` では GFM とずれる。実物の 93 本では差が出ない（上の `heads_cmp.py`）ので、このサイクルの縛りは「いまの FAQ と、設計が挙げた書き方（丸数字・Pc・リンク・強調・HTML・同名・`~~~`・字下げ）」に対しては効き、**作為的な見出しに対しては完全ではない**、に確信度を下げた。設計のリスク 3（GitHub の規則に揃えたことにはならない）の範囲として扱う。
- Must fix: 0。Should fix 1〜3 は PM の指示で直さず残した（直すなら 1 と 2 は `:1621` の 1 行、3 は `:1573-1577` の数行で、設計の範囲内）。

### 未確認

- **GitHub での描画は未確認**（設計のリスク 3）。縛りの外の差（Nl の文字、絵文字、全角の記号、上のセルフレビュー 1〜3 と 5〜7 の作為的な見出し）は残る。
- **AWS では未確認**（AWS に触れる変更は無い）。
