# docs を読みやすくする（020）

設計: PM(fable-5.1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

docs は 2026-10-09 に最新の構成へ揃えた（BACKLOG 111。PR #10 / #12）が、**内容は正しくても読みにくい**。ユーザーの指摘（2026-10-09）: 「readme や docs も読みやすくなってる？」→ 読み直して、次の 4 つが原因だと分かった。

1. **経緯が本文に混ざっている。** 「2026-09-28 に … から移した。… 2026-10-08 にやめた」のような、いまの構成を知るのに要らない文が、現在形の説明と同じ箇条書きの中にある。README と docs（FAQ を除く 41 ファイル）に日付が 421 回出る（39 ファイル）。
2. **1 箇条書きが長い。** 表でない行で 300 字を超えるものが 350 行（FAQ を除く）。`docs/pipeline.md:29-36` は 1 行が 400〜700 字で、説明・例外・経緯・未確認が 1 つの箇条書きに入っている。
3. **README の「作るもの」が費用の内訳で埋まっている。** 費用の表（PIPELINE の行だけで 707 字）、エンドポイントの単価の段落 2 つ、`$2,100` の警告、AZ の注が「作るもの」の節にあり、何を作るかが読めない。
4. **FAQ が長い。** 2,200 行、質問（`###`）80 本。先頭に節（`##` 12 本）の目次はあるが、節の中の質問の一覧は無く、目当ての質問に届かない。

ユーザーの決定（2026-10-09）: 「その他はお願い」→ 上の 4 つを 1 サイクルで直す。手元の compose のスライド（BACKLOG 34）は作らない。

### 調査で分かった事実（2026-10-09、origin/main 4513ecd）

- 対象ファイルの一覧（FAQ を除く 41 本）は `ls README.md docs/*.md docs/architecture/*.md docs/architecture/resources/*.md docker/compose/README.md | grep -v faq-fukuda`。
- 表でない 300 字超の行: 350。日付（`20YY-MM-DD`）の出現: 421 回 / 39 ファイル。FAQ: `##` 12、`###` 80、先頭の目次は `##` の 12 行だけ（`docs/faq-fukuda-nwc-poc.md:5-16`）。
- テストが docs を読む所（壊さない）: `tests/test_analytics.py:1532` は FAQ に「1 つを禁じてはいない」があることと、FAQ・`ops/up.sh` 等に前の決定の文（「AWS の制約ではなくユーザーの決定」など 9 語）が**無い**ことを見る。`tests/test_analytics.py:2369` は README と `docs/deploy.md` の**両方**に `| \`MAX_OFFSETS_PER_TRIGGER\` |` の表の行と `MAX_OFFSETS_PER_TRIGGER_ICEBERG` があることを見る（README の「よく使うキー」の表はそのまま残す）。
- `docs/deploy.md:181-210`「007 で並べ直したとき（state の移し方）」は、古いパス（`terraform/`、`oss/terraform/`）を**意図して**書いている（移す手順そのもの）。`docs/development.md:7` も同じ性質の注。
- BACKLOG の docs だけで閉じられる行（このサイクルに入れる）:
  - 95: `docs/workflow.md:30`・`app/temporal/rules.py:76`（コメント）・`docs/architecture/resources/temporal.md:102` の処置の説明が互いに食い違う。正は `app/temporal/rules.py:75` の `ACTION_CHANGES`（`heal-main` は `dc1-a-leaf-01#ethernet-1/1` を `link_up`、`check` は何もしない）と lab の IS-IS 構成（spine 2 + a-leaf 2 + s-leaf 2 + TRex。`app/containerlab/`）。
  - 101: `TELEGRAF_TAG` は `ops/lab-common.sh:53` の `dir_tag` が Dockerfile を含むディレクトリの中身のハッシュを混ぜるので、コメントの変更でもタグが変わり次の `up.sh` がイメージを作り直す。**docs に 1 行書く**（ハッシュの入力は絞らない）。書き先は `docs/architecture/resources/ecr.md:63` の近く。
  - 104: ECR の lab の版の `-amd64` の付け方は `docs/architecture/resources/ecr.md:30-32,78` に既にある。読み直して、足りなければ 1 行足し、閉じる。
  - 106: `IaC/terraform/aws-managed/base/ecr/outputs.tf:7/12/17` の description の `with tag 26.7.2` 等を、実際に置くタグ `26.7.2-amd64` 等（`ops/lab-common.sh:16-18` の `*_ECR_TAG`）に合わせる。description だけで資源は変わらない。
  - 120: `app/containerlab/trex/README.md:111-115`「確かめていないこと」の 1 つ目（TRex 2.41 が SR Linux と同じホストで af_packet で起きるか）は 2026-10-09 の AWS 検証で確かめた（`docs/verification/20261009-aws-managed.md` の D。`net_af_packet`、`Number of ports found: 4`）。確かめた側へ移す。
- 変えないもの: `docs/cycles/**`、`docs/verification/**`、`docs/GLOSSARY.md`、`docs/hearing.md`、`docs/ai-dev-flow.md`（記録と対話のメモ。読み物としての整形の対象にしない）。FAQ の本文（質問の目次を足すだけ。本文の書き換えは次のサイクル）。

## 設計方針

**内容（事実）は変えない。読む順番と文の長さを変える。** 事実を直すのは BACKLOG の 5 行（95 / 101 / 104 / 106 / 120）だけ。

1. **経緯を本文から外す。**
   - 現在の構成を説明する文から、日付と「〜から移した」「〜をやめた」「以前は〜」を消す。いまの状態だけを現在形で書く。
   - 残してよい日付は 3 種類だけ: **未確認の印**（「AWS では未確認」「2026-10-05 に 1 回確かめた」のような、確かめた範囲を示すもの）、**`docs/verification/` へのリンクの文**、**意図して古いパスを書く節**（`docs/deploy.md:181-210`、`docs/development.md:7`）。
   - 消す経緯のうち「なぜ今こうなっているか」が無いと読者が驚くもの（例: NAT と外の Splunk を消して閉域にした、telegraf-dialin をやめて gnmic にした）は、そのファイルの**末尾**に `## 経緯` の節を 1 つ作り、1 行 1 件で置く。驚かないもの（単なる改名、版上げ）は消す。git に残っているので正本は失わない。
2. **1 箇条書きは 2 文まで。**
   - 3 文以上の箇条書きは、入れ子の箇条書き（1 段）、表、または小見出し（`###`）+ 段落に割る。太字の見出し付きの箇条書き（`- **見出し。**`）は、見出しの行と本文の行を分ける（本文は次の行に字下げして続ける）。
   - 表のセルも同じ。1 セルが 200 字を超えるなら、セルには要点だけ残し、詳細は表の下の箇条書きか対応する `docs/architecture/resources/*.md` へ送る（リンク）。
   - 目安: 表でない行は 300 字以内。例外は mermaid・コードブロック・URL の行。
3. **README の「作るもの」は、何を作るかだけにする。**
   - 残す: マネージド版と OSS 版と手元の compose の 3 つの作り方があること（各 1〜2 文）、機能の表（`機能 | できること` の 2 列。費用の列を外す）、費用の合計 1 行（「`PIPELINE=1` だけで約 $2.92/h、既定のまま 1 か月置くと約 $2,100。内訳は [deploy.md の費用](docs/deploy.md#費用)」）。
   - `docs/deploy.md` に `## 費用` の節を新しく作り（「`ops/up.sh` がすること」の前）、README から外した費用の表（3 列）、エンドポイントの単価の段落、OpenSearch Serverless のエンドポイント、`STORES` ごとの時間単価、AZ の注、デバッグ用の EC2 の単価を移す。移すときに方針 1 と 2 も適用する。
   - README の「よく使うキー」の表と `MAX_OFFSETS_PER_TRIGGER` の行は残す（テストが見る）。
4. **FAQ は各節の頭に質問の目次を置く。**
   - 12 本の `##` のそれぞれの直下に、その節の `###` の質問を `- [質問](#アンカー)` で並べる（合計 80 行）。先頭の節の目次（`:5-16`）はそのまま。
   - アンカーは GitHub の規則で作る: 見出しの文字を小文字にし、英数字・ひらがな・カタカナ・漢字・ハイフン・空白以外の記号（`.` `（` `）` `・` `?` `？` `/` `:` `、` `。` など）を消し、空白を `-` にする。既存の 12 行（例: `#2-収集の設定syslog-ngtelegrafgnmic-と本番の-cisco`）がこの規則で出来ている。
   - `tests/test_analytics.py` の FAQ を読む所（`_faq`）の近くに、**目次の行の数が `###` の数と同じで、各リンクのアンカーが見出しから規則どおりに作れる**ことを見る check を 1 つ足す（規則を Python で書き、全 `##` / `###` 見出しのアンカー集合に、目次の全リンクが含まれること）。
5. **やらないこと。** 文体の統一、用語の言い換え、新しい説明の追加、FAQ 本文の書き換え、図の追加。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `README.md` | 「作るもの」を方針 3 の形にする。「ドキュメント」の表（`:155-174`）のセルを方針 2 で短くする（詳細はリンク先に任せる）。経緯を外す |
| `docs/deploy.md` | `## 費用` を新設して README から移す。全体に方針 1・2。`:181-210` は触らない |
| `docs/pipeline.md` `docs/collection.md` `docs/data-stores.md` `docs/workflow.md` `docs/troubleshooting.md` `docs/setup.md` `docs/development.md` `docs/oss-variant.md` `docs/alert-comparison.md` `docs/nautobot.md` | 方針 1・2。`docs/workflow.md:30` は BACKLOG 95 |
| `docs/architecture/README.md` `agent.md` `core.md` `pipeline.md` `workflow.md` | 方針 1・2。`README.md:7` の pptx の注は 021 が直すので触らない |
| `docs/architecture/resources/*.md`（19 本） | 方針 1・2。`ecr.md` に BACKLOG 101 の 1 行、104 の確認。`temporal.md:102` は BACKLOG 95 |
| `docker/compose/README.md` | 方針 1・2 |
| `docs/faq-fukuda-nwc-poc.md` | 方針 4 だけ |
| `app/temporal/rules.py:72-76` | コメントだけ（BACKLOG 95。コードは変えない） |
| `app/containerlab/trex/README.md:111-120` | BACKLOG 120 |
| `IaC/terraform/aws-managed/base/ecr/outputs.tf:7,12,17` | description だけ（BACKLOG 106） |
| `tests/test_analytics.py` | FAQ の目次の check を 1 つ足す（方針 4） |

## 再利用するもの

- 先頭の節の目次（`docs/faq-fukuda-nwc-poc.md:5-16`）のアンカーの形。
- `docs/architecture/resources/*.md` の「資源 1 つ 1 ファイル、表 + 注意の箇条書き」の構成。長いセルの詳細の送り先にする。
- `docs/verification/*.md`。確かめた事実の正本。経緯を消すときの代わりのリンク先。

## 実装ステップ

0. 着手前に実測して `build.md` の冒頭に書く: 表でない 300 字超の行数（下の検証 1 のコマンド）、日付の出現回数（検証 2）、`uv run --group dev --group web python tests/test_analytics.py` の末尾（2026-10-09 は 通過 513 / 失敗 0）。
1. README: 「作るもの」を書き直し、費用を `docs/deploy.md` の `## 費用` へ移す。「ドキュメント」の表を短くする。
2. `docs/*.md`（FAQ 以外）を 1 本ずつ: 日付と経緯の文を拾って消すか `## 経緯` へ移し、3 文以上の箇条書きと 200 字超のセルを割る。commit はファイルごとか、近いものをまとめて数本（1 commit で全部にしない。レビューで追えなくなる）。
3. `docs/architecture/**` と `docker/compose/README.md` を同じ手順で。
4. BACKLOG 95 / 101 / 104 / 106 / 120 の 5 件を直す（1 commit）。
5. FAQ の各節に質問の目次を足し、`tests/test_analytics.py` に check を足す。check は、目次を 1 行消す・アンカーを 1 文字変えると落ちることを確かめる（赤→緑）。
6. 検証を全部回して `build.md` に出力を貼る。セルフレビュー（`/robust`）。

## 検証方法（期待出力まで）

1. 表でない長い行が 1/10 以下になる。
   ```
   ls README.md docs/*.md docs/architecture/*.md docs/architecture/resources/*.md docker/compose/README.md | grep -v faq-fukuda | xargs awk 'length>300 && !/^\|/' | wc -l
   ```
   着手前 350 → **35 以下**。残る行は mermaid・コードブロック・URL だけで、1 本ずつ `build.md` に理由を書く。
2. 日付が本文から消える。
   ```
   ls README.md docs/*.md docs/architecture/*.md docs/architecture/resources/*.md docker/compose/README.md | grep -v faq-fukuda | xargs grep -hoE '20[0-9]{2}-[0-9]{2}-[0-9]{2}' | wc -l
   ```
   着手前 421 → **120 以下**。残る日付は「未確認の印」「`docs/verification/` の参照」「`## 経緯`」「`docs/deploy.md:181-210`」「`docs/development.md:7`」のどれかに入っている（`grep -n` の結果から無作為に 20 行選んで分類を `build.md` に書く）。
3. README の「作るもの」に `$/h` の表が無く、費用の合計の 1 行と `docs/deploy.md#費用` へのリンクがある。`docs/deploy.md` に `## 費用` があり、`| 機能 |` の表と「インターフェース型エンドポイント」の段落がそこにある。
   ```
   grep -c '\$' README.md        # 着手前 17 → 5 以下
   grep -n '^## 費用' docs/deploy.md   # 1 行出る
   ```
4. FAQ の目次。`grep -c '^### ' docs/faq-fukuda-nwc-poc.md` が 80 のまま、`grep -c '^- \[' docs/faq-fukuda-nwc-poc.md` が 12 → **92**。GitHub で描いたときにリンクが飛ぶかは、`uv run --group dev --group web python tests/test_analytics.py` の新しい check が代わりに見る（通過 514 / 失敗 0。着手前 513）。
5. テストが壊れていない。
   ```
   uv run --group dev --group web python tests/test_analytics.py   # 通過 514 / 失敗 0
   uv run --group dev --group web python tests/test_oss.py          # 通過 173 / 失敗 0
   uv run --group dev --group web python tests/test_lab_debug.py    # 着手前と同じ件数 / 失敗 0（outputs.tf の description を見ている可能性がある）
   bash ops/check.sh 2>&1 | tail -3                                   # 全部のテストが 失敗 0。terraform validate は AWS を使わない
   ```
6. BACKLOG 95: `grep -n 'heal-main' docs/workflow.md app/temporal/rules.py docs/architecture/resources/temporal.md` の 3 か所が同じ処置（`dc1-a-leaf-01` の `ethernet-1/1` を上げる）を言い、`spine`・`leaf` の台数が `app/containerlab/` の IS-IS 構成（spine 2、leaf 4、TRex 1）と合う。
7. BACKLOG 106: `terraform -chdir=IaC/terraform/aws-managed/base/ecr validate` が Success。description の変更は state を変えない（AWS には触らない）。
8. 事実を変えていない。`git diff origin/main --stat` が上の変更対象だけで、`app/` は `rules.py` のコメントと `trex/README.md` だけ、`IaC/` は `outputs.tf` の description だけ。

## 未確定事項とリスク

1. **「経緯」と「未確認の印」の線引きは手で判断する。** 機械では分けられない。判断に迷った行は消さずに残し、`build.md` に列挙して PM が見る。
2. **文を割ると意味が変わることがある。** 特に「〜だが、〜」の逆接を 2 文に割るときに条件が落ちる。割った箇条書きは、元の文と並べて読み直す（セルフレビューで差分の前後を 1 件ずつ）。
3. **README の費用を deploy.md へ移すと、README だけ読む人が費用を見落とす。** 合計の 1 行と `$2,100` の警告は README に残す（方針 3）。
4. **FAQ のアンカーの規則が GitHub と違うと目次が飛ばない。** 既存の 12 行と同じ規則で作り、テストで見る。実際の GitHub の描画は、マージ後に PM が GitHub で 3 本クリックして確かめる。
5. **長い行の数は読みやすさの代わりの指標でしかない。** 300 字以内でも 2 文を超えていれば割る。数字が目標に届いても、`docs/pipeline.md` と `README.md` と `docs/deploy.md` の 3 本は、頭から通して読み直す。
