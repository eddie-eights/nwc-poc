# architecture の pptx 2 本を作り直す（021）

設計: PM(fable-5.1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

`docs/architecture-managed.pptx` と `docs/architecture-oss.pptx`（各 10 枚。2026-10-08 に 561ebd0 で作った）は、その後の 007（ディレクトリの並べ直し）、010（Kafbat UI を Web の EC2 へ）、011（lab を IS-IS + TRex に）、012 / 013（コレクターを gnmic / syslog-ng / GoFlow2 に、telegraf-dialin を外す）、017（`oss/ops/` → `ops/oss/`）、019（`netops` → `nwc`）を反映していない。スライドの中に `terraform/`・`oss/terraform/`・`oss/ops/up.sh` が当時のまま残る（017 の cold review の Should fix S3。BACKLOG 112）。`docs/architecture/README.md:7` にはその旨の注がある。

元のデッキは生成の元（ソース）が無く、python-pptx で直に組んだ。直すたびに同じ手間になるので、**Markdown のソースから描く形に変え、ソースをリポジトリに入れる。**

### 調査で分かった事実（2026-10-09、origin/main 4513ecd）

- 元のデッキの構成（python-pptx で読んだ）。各 10 枚、スライドの型は 表紙 / 本文（タイトル + 箇条書き or 表 or 絵）。
  - マネージド版: 1 表紙、2 データの流れ（**絵**）、3 9 つの Terraform ルート（表 2 つ）、4 6 段の処理（**絵**）、5 収集から格納まで（表）、6 検知から修復まで（箇条書き）、7 SG（表）、8 費用（表）、9 消す順（箇条書き）、10 画面の開き方（表）。
  - OSS 版: 1 表紙、2 置き換えた 5 つ（**絵**）、3 1 対 1 の対応（表）、4 Fargate のタスク 15 個（表）、5 ルート（表）、6 6 段の処理（**絵**）、7 SG（表）、8 `oss/ops/up.sh`（箇条書き）、9 2026-10-07 の AWS の結果（表）、10 未確認 4 つ（表）。
  - 絵は 4 枚（PICTURE 型の shape。中の PNG は `shape.image.blob` で取れる）。この Mac には mermaid / graphviz / ImageMagick が無いので、絵を**描き直す手段は無い**。
- 描く道具: My Repo の `~/Documents/repo/bin/render-pptx <file>.deck.md [-o out.pptx]`（`deck/render_pptx.py`。python-pptx 1.0.2。初回は `.venv` を作る）。記法は `pptx-deck` スキル（`~/.claude/skills/pptx-deck/SKILL.md`）: front matter（title / subtitle / date / author / source）、`# ` 節の区切り、`## ` スライド、`- ` 箇条書き（入れ子 1 段）、表、`> ` 引用、コードフェンス、`![caption](path.png)`（パスは**カレントディレクトリ基準**。`resolve_image` は `Path.cwd() / p`）、`::: columns … --- … :::`、`::: notes`。本文の文字の大きさは 20 → 18 → 16 → 14 → 12 pt と自動で落ちる（18 以下に落ちたら中身が多すぎる）。
- My Repo の決まりでは `.deck.md` は My Repo の `artifacts/<category>/` に置き、`.pptx` は gitignore する。**このプロジェクトは `docs/` に `.pptx` を追跡している**（`README.md:159` と `docs/architecture/README.md:7` がリンク）ので、ここではプロジェクトの側に揃える（下の方針 1）。
- 現在の正しい内容の出どころ: `README.md`（作るもの、手順、GUI の一覧）、`docs/architecture/README.md` と `core.md` / `agent.md` / `pipeline.md` / `workflow.md`、`docs/architecture/resources/*.md`（SG のポートは `vpc-perimeter.md`、`msk.md`、`web-ec2.md`、`lab-ec2.md`）、`docs/deploy.md`（`ops/up.sh` / `ops/down.sh` の順、費用）、`docs/oss-variant.md`（置き換えた 5 つ、タスク数、AWS の結果、未確認）、`docs/verification/20261008-oss-aws.md` と `20261009-aws-managed.md`。**020（docs を読みやすくする）が同時に走っていて README と deploy.md の費用の置き場が動く**ので、費用の数字は `ops/up.sh` の `COST_CENTS` の式と `docs/deploy.md` を正にする。

## 設計方針

1. **ソースは nwc-poc に置き、描いた pptx も今の場所に置く。**
   - `docs/architecture/architecture-managed.deck.md` と `docs/architecture/architecture-oss.deck.md`（追跡する）。
   - 出力は今と同じ `docs/architecture-managed.pptx` / `docs/architecture-oss.pptx`（追跡する。リンクを変えない）。
   - 描き方は `docs/architecture/README.md` に 3 行で書く: 「ソースは `*.deck.md`。描くのは `~/Documents/repo/bin/render-pptx docs/architecture/architecture-managed.deck.md -o docs/architecture-managed.pptx`（個人リポジトリの道具。無ければ pptx をそのまま使う）。`.deck.md` を直したら pptx も描き直して一緒に commit する」。`:7` の「当時のまま」の注は外す。
2. **枚数と主張（タイトル）は元の 10 枚を引き継ぎ、中身を今の構成に合わせる。** タイトルは断定文のまま（例「9 つの Terraform ルートを ops/up.sh が順に作る」）。数字が変わっていればタイトルも直す（Fargate のタスク数、費用、未確認の数）。
   - マネージド版で変わる所: ルートのパス（`IaC/terraform/aws-managed/<root>`）、収集（gnmic / Telegraf（trap）/ syslog-ng / GoFlow2 の 4 種、MSK は SCRAM + IAM、トピック 5 つ `metrics` / `gnmi` / `traps` / `logs` / `flows`）、lab（IS-IS の spine 2 + leaf 4 + TRex 1、x86 の `m6i.xlarge`）、画面（Kafbat UI は Web の EC2 の Docker、`containerlab graph` は lab の EC2）、費用（`docs/deploy.md`）、消す順（`ops/down.sh`。Runtime の ENI）。
   - OSS 版で変わる所: `ops/oss/up.sh` / `down.sh` / `roll-nodes.sh`、`IaC/terraform/oss/<root>`、Fargate のタスク数（`docs/oss-variant.md` を数え直す）、Kafka の内部トピックのパーティション（022 が同時に走る。022 がマージ済みなら 1、まだなら触れない）、AWS の結果は 2026-10-08 の 2 回目（`docs/verification/20261008-oss-aws.md`）、未確認の一覧は `docs/oss-variant.md` の今の表。
3. **絵は 4 枚とも元の PNG を取り出して使う。描き直さない。**
   - `docs/architecture/img/managed-flow.png`（マネージド 2）、`managed-stages.png`（マネージド 4）、`oss-replaced.png`（OSS 2）、`oss-stages.png`（OSS 6）に取り出す（`shape.image.blob` を書き出す）。
   - 取り出したら 4 枚を**目で見て**（`Read` ツールで PNG を開く）、絵の中に古いパスや外した部品（`telegraf-dialin`、`oss/ops`、`terraform/`、Fargate の `kafka-ui`）が**描かれていないか**確かめる。描かれていれば、その絵は使わず、同じ内容を `::: columns` の 2 列の箇条書きか表で書く（この Mac に描く道具が無いため）。使わなかった PNG は入れない。
   - 絵を使うスライドの `![…](docs/architecture/img/….png)` のパスはリポジトリの根からの相対にし、`render-pptx` はリポジトリの根で打つ（`resolve_image` がカレントディレクトリ基準）。
4. **1 スライド 1 メッセージ、箇条書き 6 行以内、表 5 行 × 4 列以内**（`pptx-deck` スキルの決まり）。表が 5 行を超える所（ルート 9 つ、SG）は、元のデッキと同じく 2 つの表か `::: columns` に割る。自動の文字サイズが 18 pt 以下に落ちたら中身を減らす。
5. **やらないこと。** スライドの追加（手元の compose のスライドはユーザーが不要と決めた。BACKLOG 34）、デザインの変更、動画・アニメーション、PowerPoint でしか確かめられないこと。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `docs/architecture/architecture-managed.deck.md` | 新規。10 枚 |
| `docs/architecture/architecture-oss.deck.md` | 新規。10 枚 |
| `docs/architecture/img/*.png` | 新規。元の pptx から取り出した絵（使うものだけ） |
| `docs/architecture-managed.pptx` `docs/architecture-oss.pptx` | 描き直し（上書き） |
| `docs/architecture/README.md:7` | 注を外し、ソースと描き方を 3 行で書く |
| `README.md:159` | リンクは変えない。文言に「10 枚」などの数字があれば合わせる |

## 再利用するもの

- 元のデッキのタイトル 20 本と構成（上の調査）。絵 4 枚。
- `~/Documents/repo/bin/render-pptx` と `deck/render_pptx.py`（My Repo）。`pptx-deck` スキルの記法と確かめ方。
- 内容の正: `docs/architecture/**`、`docs/deploy.md`、`docs/oss-variant.md`、`docs/verification/*.md`、`ops/up.sh` の費用の式。

## 実装ステップ

1. 元の 2 本から絵を取り出し（`.venv` の python-pptx か `~/Documents/repo/.venv/bin/python`）、`docs/architecture/img/` に置き、`Read` で見て使えるか決める。結果（4 枚それぞれ 使う / 使わない と理由）を `build.md` に書く。
2. `architecture-managed.deck.md` を書く。各スライドの数字と名前は出どころのファイルと行を `::: notes` に残す（レビューで照合するため。pptx のノートに入るだけで本文には出ない）。
3. `render-pptx … -o docs/architecture-managed.pptx` で描き、下の検証 1〜3 を回す。18 pt 以下に落ちたスライドは中身を減らす。
4. OSS 版も同じく。
5. `docs/architecture/README.md` と `README.md:159` を直す。
6. 検証を全部回して `build.md` に出力を貼る。セルフレビュー（`/robust`。観点はドキュメント: 正確性、網羅性、一貫性）。

## 検証方法（期待出力まで）

1. 古い名前が 1 つも無い。pptx の全文字を取り出して grep する。
   ```
   ~/Documents/repo/.venv/bin/python - <<'EOF'
   from pptx import Presentation
   import re
   for f in ("docs/architecture-managed.pptx", "docs/architecture-oss.pptx"):
       t = "\n".join(sh.text_frame.text for s in Presentation(f).slides for sh in s.shapes if sh.has_text_frame)
       t += "\n".join(c.text for s in Presentation(f).slides for sh in s.shapes if sh.has_table for r in sh.table.rows for c in r.cells)
       bad = [w for w in ("oss/terraform", "oss/ops", "telegraf-dialin", "netops", "kafka-ui のタスク") if w in t]
       bad += re.findall(r"(?<![A-Za-z/])terraform/", t)   # IaC/terraform/ 以外の terraform/
       print(f, "NG" if bad else "OK", bad)
   EOF
   ```
   期待: 2 行とも `OK []`。
2. 枚数と絵の数。各 10 枚。PICTURE の shape の数は、ステップ 1 で「使う」と決めた枚数と同じ（`build.md` の数と突き合わせる）。
3. 形が収まっている（`pptx-deck` スキルの確かめ方）。全 shape の `left + width <= 13.333 in`、`top + height <= 7.5 in`。本文の run の最小の文字サイズが 14 pt 以上（`run.font.size.pt`）。期待: 「はみ出し 0、最小 14 pt 以上」を 2 本とも。
4. 見た目。`qlmanage -t -s 1400 -o <scratchpad> docs/architecture-managed.pptx`（1 枚目だけ出る）で表紙を見る。本文のスライドは 3 と、`Read` で `img/*.png` を見ることで代える（PowerPoint は無い）。
5. 中身が正しい。各スライドの数字（ルートの数、トピックの数、タスクの数、費用、未確認の数、SG のポート）を `::: notes` に書いた出どころと 1 つずつ突き合わせ、`build.md` に「スライド N: 値 / 出どころ」の表で書く。費用は `uv run --group dev --group web python tests/test_analytics.py` の費用の check が見る `ops/up.sh` の式と同じ値。
6. docs のリンクが切れていない。`grep -n 'architecture-.*\.pptx' README.md docs/architecture/README.md` の 4 つのリンク先が存在する。`docs/architecture/README.md` に「当時のまま」の文が無い（`grep -c 当時のまま docs/architecture/README.md` → 0）。
7. ソースから再現できる。`render-pptx` をもう 1 回打って、`git status` で pptx が変わらない（変わるなら、python-pptx が書く日時などの差で、`build.md` にそう書く。中身の差ではないことを 1 で確かめる）。

## 未確定事項とリスク

1. **絵 4 枚の中身は取り出して見るまで分からない。** 古い部品が描かれていれば表や 2 列の箇条書きで代える（方針 3）。その場合、元の「データは左から右へ流れる」という絵の訴求は弱くなる。描き直すなら mermaid が入った別の PC か、ユーザーに頼む。
2. **`render-pptx` は個人リポジトリの道具で、会社のリポジトリに移ると無い。** pptx を追跡しているので読むだけなら困らない。描き直す道具が要るときは `deck/render_pptx.py`（python-pptx 1.0.2 だけに依存）を `docs/architecture/` に写す案があるが、このサイクルではやらない（BACKLOG に足す）。
3. **020 と同時に走るので、README と deploy.md の費用の置き場が動く。** 数字は `ops/up.sh` の式を正にし、リンクは張らない（スライドにファイルパスだけ書く）。マージの順はどちらでもよい（触るファイルが `docs/architecture/README.md` で重なるが、020 は `:7` を触らない）。
4. **PowerPoint で開いた見た目は Mac では確かめられない。** 形の数値（検証 3）とサムネイル（検証 4）で代える。ユーザーが Windows で開いて崩れていたら直す。
5. **022 の内部トピックの値を OSS 版のスライドに書くかは、マージの順で決まる。** 022 が先なら Kafka の行に「内部トピック 1 パーティション」を書く。後なら書かない（スライドは Kafka の台数と EFS だけ）。
