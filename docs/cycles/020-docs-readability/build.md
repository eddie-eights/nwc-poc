# docs を読みやすくする（020）の実装

## Round 1

実装モデル: opus-5.5 / effort: 既定のまま（自分のセッションの effort は変えられない）。エンジニア1。ブランチ `docs/020-docs-readability`（origin/main 9fb0616 から）。

### ステップ 0: 着手前の実測（9fb0616）

```
$ ls README.md docs/*.md docs/architecture/*.md docs/architecture/resources/*.md docker/compose/README.md | grep -v faq-fukuda | xargs awk 'length>300 && !/^\|/' | wc -l
     350
$ ls README.md docs/*.md docs/architecture/*.md docs/architecture/resources/*.md docker/compose/README.md | grep -v faq-fukuda | xargs grep -hoE '20[0-9]{2}-[0-9]{2}-[0-9]{2}' | wc -l
     421
$ grep -c '\$' README.md
8
$ grep -o '\$' README.md | wc -l
      18
$ grep -c '^### ' docs/faq-fukuda-nwc-poc.md; grep -c '^- \[' docs/faq-fukuda-nwc-poc.md
80
12
$ uv run --group dev --group web python tests/test_analytics.py 2>&1 | tail -1
通過 513 / 失敗 0
$ uv run --group dev --group web python tests/test_oss.py 2>&1 | tail -1
通過 173 / 失敗 0
$ uv run --group dev --group web python tests/test_lab_debug.py 2>&1 | tail -1
通過 97 / 失敗 0
```

- `grep -c '\$' README.md` は行数を数えるので 8（design.md の「17」は `$` の個数に近い。個数は 18）。検証 3 は行数（`grep -c`）で 5 以下を見る。
- 検証 1・2 の対象には、変えないファイル（`docs/ai-dev-flow.md` の長い行 2、`docs/hearing.md` の日付 2、`docs/GLOSSARY.md`）も入っている。

### ステップ 1: README の「作るもの」と deploy.md の費用（d3de6b1、07cea2a）

エンジニア1。README の「作るもの」を方針 3 の形にし、費用の表・エンドポイントの単価・`STORES` ごとの単価などを `docs/deploy.md` の `## 費用` へ移した。deploy.md の本文に方針 1・2 を当てた（007 の節は触らない）。

### ステップ 2: docs/*.md（a1d37ce）

ここからエンジニア2 が引き継いだ。

- `README.md`、`docs/` の 11 本（`deploy` `pipeline` `collection` `data-stores` `workflow` `troubleshooting` `setup` `development` `oss-variant` `alert-comparison` `nautobot`）に方針 1・2 を当てた。
- 書き換えはファイルごとにサブエージェントに分け、規則（awk は C ロケールでバイトを数える、セルは文字で数える、日付を残してよい 5 種）を同じ紙で渡した。
- 消した経緯のうち、無いと読者が驚くものは各ファイルの末尾の `## 経緯` に 1 行 1 件で置いた。
- `docs/deploy.md` の DependencyViolation の行（元の :234）は、2 文の箇条書きにし、理由を入れ子に出した。
- `docs/development.md`:
  - 7 行目は design のとおり残した。
  - 12 行目（017 で根を片付けた話）は日付だけ外した。
  - テストの件数は「（16 本）」にし、日付を外した。

### ステップ 3: docs/architecture/** と docker/compose/README.md（d63b162）

- `docs/architecture/` の 5 本と `resources/` の 20 本、`docker/compose/README.md` に同じ手順を当てた。
- `*.deck.md` と `architecture/README.md:7`（pptx の注。021 の持ち分）は触っていない。

### ステップ 4: BACKLOG の 5 件（3dc4d73）

| BACKLOG | 直したところ |
|---|---|
| 95 | `app/temporal/rules.py:75-78` のコメント（コードは変えない）。`docs/workflow.md:47-48` と `docs/architecture/resources/temporal.md:106` の「警告は出ない」を、`heal-main` = `dc1-a-leaf-01` の `ethernet-1/1` の `link_up` と、トポロジが割れているときの「危険」の誤報に直した |
| 101 | `docs/architecture/resources/ecr.md:64-65`。コメントだけの変更でも `dir_tag` のハッシュが変わり、次の `ops/up.sh` が作り直す（`telegraf_tag` は `app/telegraf` と `docker/images/telegraf/Dockerfile` を渡す） |
| 104 | `ecr.md:81-85` に `-amd64` の説明が既にあるのを確かめた（変更なし） |
| 106 | `IaC/terraform/aws-managed/base/ecr/outputs.tf:7,12,17` の description のタグを `26.7.2-amd64` / `v0.10.0-amd64` / `2.41-amd64` にし、出どころ（`ops/lab-common.sh` の `*_ECR_TAG`、`ops/up.sh` の手順 2）を書いた |
| 120 | `app/containerlab/trex/README.md` の af_packet の項目を「lab の EC2（m6i.xlarge）で確かめたこと」に移し、`docs/verification/20261009-aws-managed.md` の D へつないだ。冒頭（:3）の「起動するかも確かめていない」はステップ 6 で直した |

同じ commit で、PM の指示に従って次の 2 つも直した。

- `docs/oss-variant.md` の検証の表（:129）と箇条書き（:134）の `oss/ops/down.sh` を `ops/oss/down.sh` にした。
- 200 字を超えて残っていた 2 つのセルを割った:
  - `msk.md:20`: 内部トピックの話を表の下へ移した。
  - `temporal.md:106`: パスを短くした。

### ステップ 5: FAQ の目次（2ef8481）

- 12 の `##` の直下（見出しの次の空行の次）に、その節の `###` を `- [Q. …](#q-…)` で並べた（80 行）。アンカーは GitHub の規則で作った。
- `tests/test_analytics.py` の check（`_gh_slug` / `_faq_toc_ok`）は 3 つを見る:
  - 各節の目次がその節の `###` と同じ順で並んでいる。
  - 節が 12 ある。
  - FAQ の中のアンカーが全部見出しに当たる。
- 赤→緑（`mutate_faq.py` で FAQ を一時的に壊し、最後に戻す）:

  ```
  == 目次を 1 行消す: rc=1
     AssertionError: FAQ: 12 の節の直下に、その節の質問（###）を順に並べた目次があり、FAQ の中のアンカーが全部見出しに当たる
  == アンカーを 1 文字変える: rc=1
     AssertionError: FAQ: 12 の節の直下に、その節の質問（###）を順に並べた目次があり、FAQ の中のアンカーが全部見出しに当たる
  == 元に戻す: rc=0
  ```

- FAQ の本文（元の :838）の「（README の表）」は、費用が README から移ったので `[deploy.md の費用](deploy.md#費用)` に向け直した（PM の指示。下の「迷った行」）。

### origin/main の取り込み

- ops の小さな直しを片付ける（023）が先にマージされたので、origin/main を取り込んだ。
- 衝突は `docs/development.md` の `ops/check.sh` の中身の段落だけで、次のように解いた:
  - 020 の割った形に、023 の「旧名の grep」の項目を足した。
  - 件数は取り込んだあとの実測にそろえた（`test_analytics` 515、`test_oss` 174、`test_oss_ops` 199）。
- design の「通過 514」は、023 が足した 1 項目と 020 の 1 項目で 515 になる。

### ステップ 6: 検証とセルフレビュー

#### 検証

```
$ ls README.md docs/*.md docs/architecture/*.md docs/architecture/resources/*.md docker/compose/README.md | grep -v faq-fukuda | xargs awk 'length>300 && !/^\|/' | wc -l
      18
$ ls README.md docs/*.md docs/architecture/*.md docs/architecture/resources/*.md docker/compose/README.md | grep -v faq-fukuda | xargs grep -hoE '20[0-9]{2}-[0-9]{2}-[0-9]{2}' | wc -l
     315
$ grep -c '\$' README.md
1
$ grep -n '^## 費用' docs/deploy.md
168:## 費用
$ grep -c '^### ' docs/faq-fukuda-nwc-poc.md
80
$ grep -c '^- \[' docs/faq-fukuda-nwc-poc.md
92
$ uv run --group dev --group web python tests/test_analytics.py 2>&1 | tail -1
通過 515 / 失敗 0
$ uv run --group dev --group web python tests/test_oss.py 2>&1 | tail -1
通過 174 / 失敗 0
$ uv run --group dev --group web python tests/test_lab_debug.py 2>&1 | tail -1
通過 97 / 失敗 0
$ bash ops/check.sh 2>&1 | tail -3
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）
すべて通過
$ terraform -chdir=IaC/terraform/aws-managed/base/ecr validate -no-color
Success! The configuration is valid.
$ git diff origin/main --name-only -- app IaC tests
IaC/terraform/aws-managed/base/ecr/outputs.tf
app/containerlab/trex/README.md
app/temporal/rules.py
tests/test_analytics.py
```

`ops/check.sh` の 16 本は全部 失敗 0:

- `test_agentcore` 161、`test_graph` 78、`test_stream` 106、`test_sync` 103、`test_analytics` 515、`test_workflow` 327、`test_alerts` 168、`test_kb_index` 7
- `test_lab_debug` 97、`test_nautobot` 68、`test_oss` 174、`test_oss_ops` 199、`test_oss_roll` 66、`test_local_compose` 138、`test_collectors` 79、`test_dashboard_config` 3

| 検証 | 着手前 | 目標 | いま | 判定 |
|---|---|---|---|---|
| 1. 表でない 300 バイト超の行 | 350 | 35 以下 | 18 | 達成 |
| 2. 日付の出現回数 | 421 | 120 以下 | 315 | **未達**（下） |
| 3. README の `$` の行 / `## 費用` | 8 / なし | 5 以下 / 1 行 | 1 / deploy.md:168 | 達成 |
| 4. FAQ の `###` / `- [` | 80 / 12 | 80 / 92 | 80 / 92 | 達成 |
| 5. テスト | 513・173・97 | 514・173・97、check.sh 失敗 0 | 515・174・97、16 本 失敗 0 | 達成（件数の差は 023 の取り込み） |
| 6. heal-main の 3 か所 | 食い違い | 同じ処置 | 3 か所とも `dc1-a-leaf-01` の `ethernet-1/1` を上げる。lab は spine 2・leaf 4（a 2、s 2）・TRex 1 で合う | 達成 |
| 7. ecr の validate | - | Success | Success | 達成 |
| 8. diff の範囲 | - | app は rules.py と trex/README.md、IaC は outputs.tf | 同じ（tests は test_analytics.py） | 達成 |

残る長い行 18 本の理由:

| 行 | 理由 |
|---|---|
| `docs/ai-dev-flow.md:3`、`:95` | 変えないファイル |
| `docs/architecture/README.md:7` | pptx の注。021 の持ち分で触らない |
| `docs/architecture/architecture-managed.deck.md:69`、`architecture-oss.deck.md:38` `55` `56` `95` `151` `169` `174` | `*.deck.md` は変えない |
| `docs/collection.md:60`、`:61` | コードブロックの中（syslog の例） |
| `docs/deploy.md:293` | mermaid |
| `docs/deploy.md:380` `382` `384` `402` | 007 の節（意図して古いパスを書く節。触らない） |

日付が 120 に届かなかった理由:

- 残っている 315 個の分類（`dates.py`。行の位置と言葉で機械的に分けた）:

  | 分類 | 数 |
  |---|---|
  | 確かめた範囲の印（確かめた・未確認・既知・AWS で など） | 131 |
  | 末尾の `## 経緯` | 108 |
  | `docs/verification/` へのリンクの文 | 21 |
  | 変えないファイル（deck・hearing） | 14 |
  | 古い環境の条件（「2026-10-09 より前に立てたものは」） | 10 |
  | 見出しの日付（文言を変えるとアンカーが変わる） | 8 |
  | コードブロックの中の例 | 5 |
  | 007 の節 / development.md:7 | 2 |
  | その他 | 16 |
- design は経緯を `## 経緯` に移してよいとしているが、120 という目標はその移した分を数に入れていなかった。`## 経緯` の 108 を除くと 207 になる。
- 残りの多くは確かめた範囲の印で、消すと何をいつ確かめたかが落ちる。
- その他の 16 は、alert-comparison の回の呼び名（「2026-10-05 ほどの差は出なかった」）、deploy.md の実測値の日付、`resources/README.md:6` の突き合わせた日などで、消すかは PM が決める（Should fix 1）。
- 無作為の 20 行（`dates.py --sample 20`、seed 20）:

  | 分類 | 行 |
  |---|---|
  | 経緯 | `architecture/resources/lab-ec2.md:132`、`resources/splunk.md:185`、`architecture/pipeline.md:82`、`resources/prometheus.md:78`、`resources/neptune-analytics.md:126`、`deploy.md:528` |
  | 確かめた範囲の印 | `resources/agentcore-bedrock.md:122` `:123`、`resources/msk.md:123`、`deploy.md:307` `:328`、`resources/firehose.md:70`、`collection.md:26` `:5` |
  | verification | `pipeline.md:265` |
  | design の 5 種に入らないもの | `collection.md:54`（コードブロックの syslog の例の時刻）、`alert-comparison.md:215`（比べた回の呼び名）、`oss-variant.md:78`（古い環境の条件）、`collection.md:75` `:220`（見出し） |
  - 20 行のうち 15 行が design の 5 種に入った。
  - 入らなかった 5 行は経緯でなく、消すと意味が落ちる。
  - `collection.md:5` の「2026-10-04 時点」は、確かめた範囲の印と経緯の境目にある。

古い名前の残り（`docs/cycles/` と `docs/verification/` を除く）:

| 名前 | 件数 | 残っている所と理由 |
|---|---|---|
| `netops` | 0 | |
| `terraform/`（`IaC/` の下でないもの） | 3 | 3 つとも意図して古いパスを書く所: `deploy.md:380` と `:392`（007 の節と、その節の移し方のスクリプト）、`development.md:9`（読み替えの注） |
| `oss/terraform/` | 3 | 3 つとも意図して古いパスを書く所: `deploy.md:380` と `:393`（007 の節と、その節の移し方のスクリプト）、`development.md:9`（読み替えの注） |
| `oss/ops/` | 3 | `architecture-oss.deck.md:150`（変えないファイル）、`deploy.md:382`（007 の節）、`oss-variant.md:78`（下の「迷った行」） |
| `kb-docs/` | 0 | |

相対リンクとアンカー（`links.py`。README・docs・architecture・compose・trex の README の全リンクを、ファイルの有無と見出しのアンカーで見る。origin/main からの見出しの消失も見る）: 切れなし、消えた見出しなし。

#### セルフレビュー

方法:

- 読むだけのサブエージェント 2 体に、`origin/main` との diff を半分ずつ渡した。
  - 1 体目: README と docs/*.md。
  - 2 体目: architecture、compose、app、IaC、tests、FAQ。
- 消えた行が別の場所（`## 経緯`、表の下、リンク先）に移っているかを、1 行ずつ照らしてもらった。

Must fix（0 にした）:

1. `app/containerlab/trex/README.md:3`:
   - 冒頭が「起動するかも確かめていない」のままで、BACKLOG 120 で移した「m6i.xlarge で起動を確かめた」と食い違っていた。
   - 「起動は確かめた。撃って届くかは確かめていない」に直した（ステップ 6）。
2. `docs/architecture/resources/ecr.md:66`:
   - ステップ 4 で入れ子の箇条書きを足したため、次の「出典:」の行が入れ子の続きとして描かれていた。
   - `  - 出典:` にした（ステップ 6）。
3. レビュアーが挙げた `docs/oss-variant.md:129,134` は Must fix に数えなかった。
   - 指摘の中身: 2026-10-08 に打ったのは `oss/ops/down.sh` なので、記録の書き換えになる。
   - 数えなかった理由: PM の指示で `ops/oss/down.sh` にした所なので、下の「迷った行」に回した。

Should fix（直していない）:

1. **日付が目標の 120 に届かない（315）。**
   理由は上。その他の 16 行と、見出しの日付 8 個（アンカーが変わるのでリンク元も直す必要がある）を消すかは PM が決める。
2. **`docs/deploy.md:376` が「override は要らないはず」とぼかした。**
   元は「override は要らない。ただし `try()` にしてからの import は AWS で未確認」。
   - 同じ行で、元の「`pipeline` の 6 本が `Error: Invalid index` で落ちたので一時的な override で通した」も消えている。
3. **経緯に移さずに消えた履歴が 12 か所ある。**
   どれも、無くても今の構成を読み違えないので、そのままにした。
   - `development.md:22`（Dockerfile を 007 で移し、syslog-ng は 012、gnmic は 013 で足した）
   - `development.md:91`、`:94`（Grafana は 2026-09-28 から、`alert_events` は 2026-10-04 から）
   - `oss-variant.md:9`、`:35`（005 の compose を消した、Kafbat UI は 010 から Web の EC2）
   - `alert-comparison.md:52`、`:284`（2026-10-08 までは 50 × 20）
   - `nautobot.md:24`、`:258`、`:84`（修復案は 2026-10-05 から S3 Tables、007 で `app/nautobot/` から移した）
   - `data-stores.md:552`（`tg gnmi` / `tg test` は 2026-10-09 にやめた）
   - `data-stores.md` の「前の `<prefix>-stream-produce` の `Bootstrap` は消した」
4. **`tests/test_analytics.py` の `_gh_slug` と GitHub の規則（github-slugger）がずれている。**
   いまの FAQ の見出しはどれにも当たらない。ずれは次のとおり:
   - 丸数字などの `No` を残す。
   - `_` 以外の `Pc` を消す。
   - 重複の `-1` が既存の `foo-1` とぶつかる場合を避けない。
   - `~~~` と字下げしたフェンスを見ない。
   - 見出しの中のリンクや強調の記法を外さない。
   - 目次は見出しの次の空行が 1 行である前提で読む。
5. **サブエージェントが自分で言い換えを申告した箇所を、PM が目で見る。**
   - `resources/msk.md` の `## 経緯`
   - `data-stores.md` の表の行の割り方
   - `docker/compose/README.md:217` に足した 1 文
   - `architecture/agent.md:24`
   - `pipeline.md` から落ちた「cycle 002 でそろえた」
   - README の「lab → 収集 → Kafka」の縮め方（ステップ 1）

#### 迷った行

1. **`docs/oss-variant.md:129`、`:134`**
   2026-10-08 の検証の記録の `oss/ops/down.sh` を、今のパス `ops/oss/down.sh` にした（PM の指示）。
   - 記録として当時のパスを残す考え方もある（レビュアーの指摘）。
2. **`docs/oss-variant.md:78`**
   「2026-10-09 より前に `oss/ops/up.sh` で立てたものは、そのときの commit の `oss/ops/down.sh` で消す」は残した。
   - 古い環境の消し方なので、古いパスでないと意味が無い。
3. **`docs/deploy.md:382`（007 の節）**
   「OSS 版は `oss/ops/down.sh`」は残した。
   - 007 をマージする前の配置では正しい。
4. **`docs/faq-fukuda-nwc-poc.md:882`**
   「（README の表）」を `[deploy.md の費用](deploy.md#費用)` にした（PM の指示）。
   - 方針 4 は FAQ の本文を変えないとしているが、README から費用の表を外したので、元の参照は行き先を失う。
5. **`README.md:166`**
   architecture の行のセルは 336 字のまま（origin/main の 021 の形。PM の指示）。
6. **`docs/development.md`**
   テストの件数の日付「（2026-10-09 で 16 本）」は外した。
   - 件数は取り込み後の実測に合わせたので、いつの数かは git で分かる。
