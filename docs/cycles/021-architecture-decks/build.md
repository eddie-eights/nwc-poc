# build — architecture の pptx 2 本を作り直す（021）

## Round 1

実装モデル: opus-5.5。2026-10-09。

基点は origin/main の 9fb0616。ブランチは `docs/021-architecture-decks`（worktree `.claude/worktrees/cycle-006-design`）。

### 変更ファイル

| ファイル | 変更 |
|---|---|
| `docs/architecture/architecture-managed.deck.md` | 新規。10 枚 |
| `docs/architecture/architecture-oss.deck.md` | 新規。10 枚 |
| `docs/architecture-managed.pptx` `docs/architecture-oss.pptx` | `render-pptx` で描き直し（上書き） |
| `docs/architecture/README.md:7` | 「当時のまま」の注を外し、ソースと描き方を 3 行で足した（ほかの行は触っていない） |
| `docs/cycles/021-architecture-decks/build.md` | これ |

触らなかったもの:

- `README.md:159`。「10 枚」などの数字が無いので変えない（設計の変更対象の表のとおり）。
- `docs/architecture/img/`。絵は 4 枚とも使わないので置かない（下）。
- 022 はまだマージされていないので、OSS 版の Kafka に「内部トピック 1 パーティション」は書かない（設計の未確定事項 5）。

### 絵 4 枚の判断（実装ステップ 1）

元の 2 本から `shape.image.blob` で PNG を取り出し（scratchpad。リポジトリには入れていない）、`Read` で開いて見た。**4 枚とも使わない。** 設計の方針 3 のとおり、古い部品が描かれているので `::: columns` の 2 列か箇条書きで書き直した。

| 絵 | 判断 | 理由（絵の中に描かれていたもの） | 代わり |
|---|---|---|---|
| マネージド 2（データの流れ） | 使わない | 収集が「Telegraf / ECS・2 サービス」だけ（dialin を含む古い形。gnmic・syslog-ng・GoFlow2 が無い）。MSK の下に「Kafbat UI / ECS・Kafka の画面」（010 で Web の EC2 へ移った） | 2 列の箇条書き（行き: データ / 戻り: 修復） |
| マネージド 4（6 段の処理） | 使わない | 1 段目が「Telegraf（ECS）/ trap・syslog を受け / gNMI・SNMP を取る」（013 より前の収集） | 6 行の箇条書き |
| OSS 2（置き換えた 5 つ） | 使わない | マネージド 2 と同じ古い収集と ECS の Kafbat UI。さらに Nautobot → Neo4j が「Job の同期は未接続 / sync-graph --oss で代える」の点線（2026-10-08 に接続して AWS で確かめ済み） | 2 列の箇条書き（置き換えた 5 つ / そのまま） |
| OSS 6（6 段の処理） | 使わない | マネージド 4 と同じ古い 1 段目（Telegraf が trap・syslog を受け gNMI・SNMP を取る） | 6 行の箇条書き |

このため PICTURE の shape は 2 本とも 0 が期待値（検証 2）。

### タイトルを変えたスライド

方針 2（主張は引き継ぎ、事実や数字が変わった所だけ直す）による。

| スライド | 元 | いま | 理由 |
|---|---|---|---|
| マネージド 8 | PIPELINE を既定のまま 1 か月置くと約 $2,000 になる | …約 $2,100 になる | `ops/up.sh` の式で PIPELINE と土台が 292 セント/h。× 730 h ≈ $2,132 |
| マネージド 10 | 画面はどれも Web の EC2 を踏み台に localhost で開く | 画面は Web の EC2 を踏み台に localhost で開き、lab の図だけは lab の EC2 から開く | `containerlab graph`（`sudo lab graph`、50080）が加わり「どれも」が成り立たない |
| OSS 8 | oss/ops/up.sh は同じ 9 ルートを作り、… | ops/oss/up.sh は同じ 9 ルートを作り、… | 017 でパスが移った |
| OSS 9 | 2026-10-07 に AWS で 1 回立て、5 つとも動いて… | 2026-10-08 に AWS で 2 回目を立て、5 つとも動いて… | 結果を 2 回目（`docs/verification/20261008-oss-aws.md`）に替えた |

ほかの 16 本は元のタイトルのまま（OSS 4 の「タスク 15 個」と OSS 10 の「未確認は 4 つ」は数え直して同じだった）。

### 本文を 20 pt に保つために動かしたもの

最初に描いたマネージド版は 7 枚で本文が 14〜18 pt に落ち（表の文字は最小 11.47 pt）、OSS 版の 1 回目は 3 枚目と 7 枚目が 16 pt（表の文字 13.12 pt）だった。中身を減らして全スライドを本文 20 pt にした。減らした文はノートに移し、消してはいない。

- マネージド 3・8・10、OSS 7 のリード文をノート（またはその列の段落）へ移した。
- 表のセルからコードを外し、セルを 1〜2 行に縮めた（例 OSS 3 の「Apache Kafka（KRaft）」→「Kafka（KRaft）」。正式名はノート）。
- OSS 3 の「変えないもの」の列挙は本文を代表の 3 つにし、全部をノートに書いた。KB は OSS 版では作らない（`ops/oss/up.sh` は `CREATE_KB` を読まない）ので、本文の「変えないもの」から外した。

### 検証

#### 1. 古い名前が無い

設計の検証 1 のスクリプトをそのまま、worktree の根で打った。

```
$ ~/Documents/repo/.venv/bin/python - <<'EOF' … EOF
docs/architecture-managed.pptx OK []
docs/architecture-oss.pptx OK []
```

参考（設計の検証の外）: ソースのノートまで同じ語で grep すると 2 行出る。どちらも経緯の説明で、本文ではない。

```
$ grep -n -E 'oss/terraform|oss/ops|telegraf-dialin|netops|kafka-ui のタスク|(^|[^A-Za-z/])terraform/' docs/architecture/*.deck.md
docs/architecture/architecture-oss.deck.md:150:- 元のタイトルの oss/ops/up.sh は、017 で ops/oss/up.sh に移ったので直した（中身の主張は同じ）。
docs/architecture/architecture-managed.deck.md:69:- 数え方: … 013 で gnmic・syslog-ng・GoFlow2 が入り、telegraf-dialin が外れた）。
```

#### 2・3. 枚数・絵の数・はみ出し・文字の大きさ

scratchpad の `fit.py`（python-pptx。全 shape の `left + width <= 13.333`・`top + height <= 7.5` を見て、本文の run の `font.size.pt` の最小を出す。表紙の 1 枚目とページ番号「N / 10」の shape は本文でないので除く）。

```
$ uv run --with python-pptx python fit.py docs/architecture-managed.pptx docs/architecture-oss.pptx
docs/architecture-managed.pptx slides 10 pictures 0 はみ出し 0 最小 pt 16.39 10〜15 分
   s1:[]
   s2:[20.0, 24.0]
   s3:[16.39, 20.0, 24.0]
   s4:[20.0, 24.0]
   s5:[16.39, 20.0, 24.0]
   s6:[18.79, 20.0, 24.0]
   s7:[16.39, 24.0]
   s8:[16.39, 18.79, 20.0, 24.0]
   s9:[18.79, 20.0, 24.0]
   s10:[18.79, 20.0, 24.0]
docs/architecture-oss.pptx slides 10 pictures 0 はみ出し 0 最小 pt 16.39 0.5 / 1 GB
   s1:[]
   s2:[20.0, 24.0]
   s3:[16.39, 20.0, 24.0]
   s4:[16.39, 20.0, 24.0]
   s5:[16.39, 20.0, 24.0]
   s6:[20.0, 24.0]
   s7:[16.39, 24.0]
   s8:[18.79, 20.0, 24.0]
   s9:[16.39, 24.0]
   s10:[16.39, 24.0]
```

1 行目の末尾は、最小の大きさで描かれた run の文字の例。`sN:` はそのスライドに出てくる文字の大きさ（表紙の s1 は数えない）。

- 2: 各 10 枚。PICTURE 0（「使う」と決めた絵の数 0 と一致）。
- 3: はみ出し 0。本文の最小は 16.39 pt（本文 20 pt のときの表の文字。20 × 0.82）。18.79 pt は本文 20 pt の中のインラインコード。24 pt はタイトル。全スライドが本文 20 pt で描けた（18 以下に落ちたスライドは無い）。

#### 4. 見た目

この Mac には soffice・Keynote・PowerPoint が無いので、macOS の Quick Look（`qlmanage -t`）で PNG にして `Read` で見た。Quick Look が描くのは pptx の 1 枚目だけ。

```
$ mkdir -p thumb && qlmanage -t -s 1400 -o thumb docs/architecture-managed.pptx
$ qlmanage -t -s 1400 -o thumb split/architecture-managed-02.pptx … split/architecture-oss-10.pptx
```

- 表紙 2 枚（マネージド・OSS）: 見た。タイトル・サブタイトル・日付と出どころの行が重ならず、タイトルは 1 行に収まる。OSS 版の出どころの行は 2 行に折り返すが、ほかと重ならない。
- 2〜10 枚目: **未確認。** 1 枚ずつの pptx に分けて Quick Look にかけたが、19 本のうち描けたのは OSS の 1 枚目だけで、残りは 10 分以上たっても PNG が出ず、止めた。
- 代わりに、2〜10 枚目は検証 2・3 の数値（全 shape がスライドの内側、本文 20 pt、表 16.39 pt）で見ている。タイトルの折り返しと、shape 同士の重なりは目では見ていない。

#### 5. 中身が正しい（スライド N: 値 / 出どころ）

数字はどれも 2026-10-09 の 9fb0616 で出どころを開いて突き合わせた。

| スライド | 値 | 出どころ |
|---|---|---|
| M2 | lab の SR Linux 6 台、収集 4 種、格納先 4 つ | `docs/architecture/resources/lab-ec2.md`、`README.md` の「作るもの」、`docs/deploy.md` の STORES |
| M3 | ルート 9 つ。resource の数 6 / 39 / 22 / 35 / 7 / 57 / 10 / 16 / 53 | `IaC/terraform/aws-managed/<ルート>/*.tf` の `grep -c '^resource "'`。順は `ops/up.sh` の `tf_apply`（609〜1251 行） |
| M4 | トピック 5 つ、アラート 4 種、承認から承認タブまで数秒〜20 秒 | `docs/architecture/resources/msk.md`、`docs/architecture/pipeline.md:34`、`docs/workflow.md:27` |
| M5 | STORES とトピックの対応 4 行、ジョブ 1 つ $0.21/h | `docs/deploy.md` の STORES の行 |
| M6 | Grafana のルール 4・Splunk のサーチ 4、`alert_events`、`proposal_events` は worker だけ | `docs/architecture/pipeline.md:34`・`:35`、`docs/architecture/workflow.md:21` |
| M7 | 443、3000・8000・8233・8080、9098・9096、162・5140・2055・6343/udp、57400 | `docs/architecture/core.md` の SG の表（`base/core/security_groups.tf` の `local.sg_flows`） |
| M8 | 土台 $0.07、AGENT $0.07（KB +$0.35）、PIPELINE $2.85、WORKFLOW $0.09、合計 $2.92、s3 だけ $1.99、1 か月 約 $2,100 | `ops/up.sh` の `COST_CENTS` の式（下の cost.py の出力）。作る時間は `README.md:84` |
| M9 | 消す順 9 ルートの逆、KMS 7 日、Runtime の ENI 最大 8 時間、Lambda の ENI 20〜40 分 | `docs/deploy.md` の「消す順」・`:125`・`:126`・`:137` |
| M10 | 8080・8082・8081・3000・8000・8233、lab の図 50080 | `README.md` の「GUI の一覧」、`docs/pipeline.md:80`・`:85` |
| O3 | 1 対 1 の 5 行 | `docs/oss-variant.md` の対応表（20〜37 行） |
| O4 | タスク 3 + 3 + 3 + 5 + 1 = 15、大きさの既定値 | `IaC/terraform/oss/pipeline/stream/kafka.tf`（`kafka_nodes` 3）、`analytics/spark.tf`（ジョブ 3）、`analytics/opensearch.tf`、`analytics/victoriametrics.tf`（storage 3 + insert + select）、Neo4j 1。大きさは各ルートの variables |
| O5 | 66 / 23 / 79 / 39。自分のファイルを持つのは stream・graph・analytics の 3 つ | scratchpad の `roots.py` で数えた（下） |
| O7 | 9092、2049、9200、8480・8481、7687 | `IaC/terraform/aws-managed/base/core/oss.tf` |
| O8 | 9 ルート（`ops/oss/up.sh:155`）、39 分、約 $1.55/h、21.5 vCPU / 30 | `docs/verification/20261008-oss-aws.md:9`・`:42`・`:354` |
| O9 | 396 系列、59,991 行、gnmi 352・logs 14・metrics 66,946・traps 5、検知 約 2 分・76 秒、13 分 57 秒 | `docs/verification/20261008-oss-aws.md:12`・`:138`・`:191〜195`・`:232`（13 分 57 秒） |
| O10 | 未確認 4 つ | `docs/oss-variant.md:99`〜`:104` の右の列 |

費用（M8）は `tests/test_analytics.py` の費用の check と同じ切り出し（`ops/common.sh` + `ops/up-common.sh` + `ops/up.sh` の `ENDPOINTS=""` から `COST_NOTE=$(` まで）を bash で動かした。AWS には触っていない。

```
$ python3 cost.py <worktree>
base: 7 cents
base+AGENT: 14 cents
base+AGENT+KB: 49 cents
base+PIPELINE(s3,grafana,splunk): 292 cents
base+PIPELINE(s3): 199 cents
base+PIPELINE+WORKFLOW: 302 cents
base+AGENT+PIPELINE: 295 cents
base+AGENT+PIPELINE+WORKFLOW: 304 cents
AGENT 分: 7  KB 分: 35
PIPELINE 分: 285
WORKFLOW 分（PIPELINE の上）: 10
WORKFLOW 分（AGENT と PIPELINE の上。WORKFLOW は両方が要る）: 9
PIPELINE+土台 1 か月（730 h）: $2,132
```

```
$ uv run --group dev --group web python tests/test_analytics.py
…
通過 513 / 失敗 0
```

OSS 版のルート（O5）:

```
$ python3 roots.py IaC/terraform/oss
base/ecr: links=5 own=[] res=6
base/core: links=15 own=[] res=39
agent: links=7 own=[] res=22
workflow: links=10 own=[] res=35
pipeline/lab: links=8 own=[] res=7
pipeline/nautobot: links=8 own=[] res=16
pipeline/stream: links=10 own=['kafka.tf'] res=66
pipeline/graph: links=4 own=['access.tf', 'neo4j.tf', 'outputs.tf', 'sync.tf'] res=23
pipeline/analytics: links=7 own=['grafana.tf', 'locals.tf', 'network.tf', 'opensearch.tf', 'outputs.tf', 'spark.tf', 'victoriametrics.tf'] res=79
```

（どのルートにも `oss.auto.tfvars` がある。）

#### 6. docs のリンク

```
$ grep -n 'architecture-.*\.pptx' README.md docs/architecture/README.md
docs/architecture/README.md:7:… [architecture-managed.pptx](../architecture-managed.pptx) … [architecture-oss.pptx](../architecture-oss.pptx) …
docs/architecture/README.md:10:描くのは `~/Documents/repo/bin/render-pptx docs/architecture/architecture-managed.deck.md -o docs/architecture-managed.pptx`（…）。
README.md:159:… [architecture-managed.pptx](docs/architecture-managed.pptx) と OSS 版 [architecture-oss.pptx](docs/architecture-oss.pptx) …
$ ls -la docs/architecture-managed.pptx docs/architecture-oss.pptx
-rw-r--r--  1 eight  staff  65246 Oct  9 14:37 docs/architecture-managed.pptx
-rw-r--r--  1 eight  staff  66267 Oct  9 14:47 docs/architecture-oss.pptx
$ grep -c 当時のまま docs/architecture/README.md
0
```

リンク 4 つ（`docs/architecture/README.md:7` の 2 つ、`README.md:159` の 2 つ）の先はどちらも在る。`:10` は描き方のコマンドで、リンクではない。

#### 7. ソースから再現できる

2 本を描き直すと、ファイルのバイト列は変わる（`shasum` が変わる）。zip の中の部品を 1 つずつ比べると、中身の違う部品は 0。違いは zip の各部品の日時だけ。

```
$ shasum docs/architecture-*.pptx   # 描き直す前
d49bb199a6acc1984cc2b571ea7f6784db2a940b  docs/architecture-managed.pptx
f9c4877523e93207064998ee0986eb71d6fe02f5  docs/architecture-oss.pptx
$ render-pptx …managed… && render-pptx …oss…
$ shasum docs/architecture-*.pptx   # 描き直した後
97e36b7fa10e603b53c9c035b6c9837053f6f2fe  docs/architecture-managed.pptx
2f0dc31883f04cc30db675c9aec1011b7e718387  docs/architecture-oss.pptx
$ python3 zipdiff.py <描き直す前の写し> docs/architecture-managed.pptx
片方だけの部品: []
中身が違う部品: []
$ python3 zipdiff.py <描き直す前の写し> docs/architecture-oss.pptx
片方だけの部品: []
中身が違う部品: []
$ git status --short
 M docs/architecture-managed.pptx
 M docs/architecture-oss.pptx
 M docs/architecture/README.md
?? docs/architecture/architecture-managed.deck.md
?? docs/architecture/architecture-oss.deck.md
```

そのため、ソースを変えずに描き直しても `git status` には pptx が「変わった」と出る。中身の差ではないことは上の比べ方と検証 1 で確かめた。

### 走らせていないもの・確かめていないもの

- PowerPoint（Windows）で開いた見た目。Mac に PowerPoint が無い（設計の未確定事項 4）。
- 2〜10 枚目を絵にして目で見ること（検証 4）。Quick Look が描けなかった。数値の検査（検証 2・3）だけ通っている。
- AWS には触っていない。スライドの AWS の結果は、すでにある記録（`docs/verification/20261008-oss-aws.md` など）をそのまま書いた。
- `containerlab graph`（lab の図）は AWS で未確認（`docs/pipeline.md:85`）。M10 のノートにそう書いた。
- 21.5 vCPU は 2026-10-08 の構成で測った値。いまの構成では測っていない。

### セルフレビュー

観点はドキュメント（正確性、網羅性、一貫性）。

**Must fix: 0**

**Should fix**（直していない）

1. OSS の 21.5 vCPU は古いかもしれない。2026-10-08 に 22 サービスで測った値で、その後に外れた部品があると見ている。O8・O10 と `docs/oss-variant.md:104` が同じ値を使うので、スライドは docs に合わせた。測り直すには OSS 版を AWS で立てる必要がある。
2. `docs/oss-variant.md:104`（全体の行）に `oss/ops/down.sh` が残る。017 より前のパス。スライドには写していない。直すのはこのサイクルの範囲外。
3. `render-pptx` は個人リポジトリ（My Repo）の道具で、会社のリポジトリに移ると無い（設計の未確定事項 2）。`deck/render_pptx.py` を `docs/architecture/` に写すかどうかは BACKLOG の候補（このサイクルでは BACKLOG を触らない）。

**Nit**（直していない）

1. 描き直すたびに pptx のバイト列が変わり（zip の日時）、`git status` に出る。中身は同じ。
2. 本文を 20 pt に保つため、マネージド 3・8・10 と OSS 7 のリード文をノートへ移した。元のデッキより本文の説明が少し減った。
3. 絵 4 枚を箇条書きにしたので、「左から右へ流れる」という絵の訴求は弱くなった（設計の未確定事項 1）。描き直すには mermaid などの道具が要る。
4. OSS 9 のノートで引く `docs/verification/20261008-oss-aws.md` の中には、`oss/ops/` と `netops.raw_telemetry` の古い名前が残る（記録なので当時の名前のまま。スライドには写していない）。
5. `ops/oss/roll-nodes.sh` の Linux の `script -q -c` の形は AWS で未確認（O8 のノートに書いた）。

### 設計からのずれ

- 絵は 4 枚とも使わなかった（方針 3 の「描かれていれば使わない」に当たる）。`docs/architecture/img/` は作っていない。
- タイトルを 4 本変えた（上の表）。
- いくつかのリード文をノートへ移した（上）。
- 022 の「内部トピック 1 パーティション」は書いていない（022 が未マージ）。
- commit の `Co-Authored-By` は、PM の指示の Fable 5.1 ではなく、このセッションの実際のモデル（Opus 5.5）にした。
