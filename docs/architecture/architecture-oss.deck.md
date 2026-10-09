---
title: nwc-poc の構成（OSS 版）
subtitle: マネージドの 5 つを ECS の OSS に置き換え、ほかはマネージド版と同じにする
date: 2026-10-09
source: docs/oss-variant.md、docs/cycles/005-oss-on-ecs/design.md、IaC/terraform/oss/、IaC/terraform/aws-managed/base/core/oss.tf
---

## 置き換えたのは 5 つだけで、データの流れはマネージド版と同じ

接頭辞も state も別なので、マネージド版と名前はぶつからない。

::: columns

**置き換えた 5 つ（ECS）**

- Kafka
- Spark
- OpenSearch
- VictoriaMetrics
- Neo4j + GDS

---

**そのまま**

- lab と収集 4 種
- S3 Tables・Splunk
- SNS・SQS・Temporal
- エージェントと Web
- Nautobot と RDS

:::

::: notes
- 元の 2 枚目は絵だったが、外した部品（ECS の Kafbat UI、Telegraf の dialin）と古い収集が描かれていたので 2 列の箇条書きにした（docs/cycles/021-architecture-decks/build.md）。
- 接頭辞は <owner>-nwc-oss（マネージド版は <owner>-nwc-poc）。Terraform のルートと state も IaC/terraform/oss/ の下で別（docs/oss-variant.md）。
- 何を何に替えたかは次の 3 枚目。収集 4 種は gnmic・Telegraf（trap）・syslog-ng・GoFlow2 で、マネージド版と同じ。
- Nautobot の Job から Neo4j への同期は、元のデッキ（2026-10-08 版）では未接続だったが、いまはつながっている。2026-10-08 に AWS で、手で打つ Job と JobHook の両方が Neo4j に書いた（docs/oss-variant.md、docs/verification/20261008-oss-aws.md）。
:::

## 5 つのマネージドを、ECS で動かす OSS に 1 対 1 で替えた

| 役割 | マネージド版 | OSS 版 |
|---|---|---|
| Kafka | MSK | Kafka（KRaft） |
| ストリーム処理 | EMR Serverless | Spark 3.5 |
| ログの検索 | OpenSearch Serverless | OpenSearch |
| メトリクス | AMP（Prometheus） | VictoriaMetrics |
| トポロジのグラフ | Neptune Analytics | Neo4j CE + GDS |

そのほか（AgentCore・S3 Tables・SNS など）は変えない。

::: notes
- 正: docs/oss-variant.md の対応表。OSS 版の正式な名前は Apache Kafka（KRaft）、Apache Spark 3.5、OpenSearch、VictoriaMetrics のクラスター（vminsert・vmselect・vmstorage）、Neo4j Community Edition + GDS。
- 変えないもの（マネージドのまま）: AgentCore・Bedrock・S3 Tables・RDS・ECS Fargate・SNS・SQS・Lambda・CloudWatch Logs・SSM・閉域。元のデッキは本文に並べていたが、文字を 20 pt に保つため本文は代表の 3 つにして、全部をここに書いた。
- 手順書の検索（Bedrock のナレッジベース + OpenSearch Serverless）はマネージドのまま変えないが、ops/oss/up.sh はナレッジベースを作らない（CREATE_KB も読まない）。元のデッキは「変えないもの」に KB を入れていたが、OSS 版では作られないので外して、ここに書いた。
- マネージド版の固有機能に頼った箇所: centrality（neptune.algo → GDS の gds.degree / gds.closeness / gds.wcc）、グラフへの問い合わせ（neptune-graph の execute_query → Bolt のドライバ、GRAPH_BACKEND=neo4j）。
- GDS の jar は GPLv3。Community 版の GDS は並列 4 コアまで、モデル 3 つまで。
:::

## 置き換えの 5 つは Fargate のタスク 15 個で動く

数字はタスク 1 つの vCPU / メモリの既定値。

| OSS | タスク | 大きさ | データ |
|---|---|---|---|
| Kafka | 3 | 1 / 2 GB | EFS |
| Spark | 3 | 2 / 4 GB | S3 |
| OpenSearch | 3 | 1 / 4 GB ほか | 一時領域 |
| VictoriaMetrics | 5 | 0.5 / 1 GB | EFS |
| Neo4j + GDS | 1 | 1 / 4 GB | 一時領域 |

::: notes
- タスクの数: Kafka は AZ に 1 つで 3、Spark はジョブ（iceberg・splunk・http）に 1 つで 3、OpenSearch はデータ 2・まとめ役 1、VictoriaMetrics は vminsert 1・vmselect 1・vmstorage 3（複製数 2）、Neo4j は単独 1。3 + 3 + 3 + 5 + 1 = 15。
- 大きさは各ルートの variables の既定値: Kafka 1024 / 2048、Spark 2048 / 4096、OpenSearch のデータ 1024 / 4096・まとめ役 512 / 1024、VictoriaMetrics 512 / 1024（5 つとも）、Neo4j 1024 / 4096。
- Spark の S3 は checkpoint（Kafka の offset）。OpenSearch と Neo4j はネットワークファイルシステムを避けるので EFS を使わず、タスクの一時領域に置く。OpenSearch のデータの台は一時領域 30 GiB。
- イメージ（ops/oss/oss-images.sh）: apache/kafka:4.3.1、apache/spark:3.5.9（自前）、opensearchproject/opensearch:3.9.0、victoriametrics/*:v1.153.0-cluster、neo4j:2026.09.0-community + GDS。
- 元のデッキの「これに Kafbat UI がもう 1 個」は外した。Kafbat UI（v1.5.0）は 010 から Web の EC2 の Docker で動き、Fargate のタスクではない（docs/oss-variant.md）。
:::

## ルートはマネージド版と同じ 10 で、自分のファイルを持つのは 3 つ

置き場は IaC/terraform/oss/。ほかの 7 つは aws-managed/ へのリンクだけ。

| ルート | OSS 版で替えたもの | 数 |
|---|---|---|
| pipeline/stream | MSK → Kafka | 66 |
| pipeline/graph | Neptune → Neo4j | 24 |
| pipeline/analytics | EMR・OpenSearch・AMP → ECS | 80 |
| base/core | oss.tf の SG・EFS | 39 |

::: notes
- 数は tf の resource ブロックの数（ルートの *.tf をつないで grep -c '^resource "'。シンボリックリンクの先も数える。2026-10-10、feat/035-s3-layout b39c543）。count・for_each で増える実数ではない。
- マネージド版の数は stream 57、graph 11、analytics 54、base/core 39。
- 2026-10-09 の数え（graph 23・analytics 79、マネージド版 10・53）から 1 つずつ増えた。元のデッキの stream 46 は、013 で収集 4 種（gnmic・syslog-ng・GoFlow2）が入って 66 になった。
- 自分のファイル: stream は kafka.tf、graph は access・neo4j・outputs・sync、analytics は grafana・locals・network・opensearch・outputs・spark・victoriametrics。残りはマネージド版のファイルへのシンボリックリンクと oss.auto.tfvars（project = "nwc-oss"）。
- ほかの 7 つ（base/ecr・base/logs・base/core・agent・workflow・pipeline/lab・pipeline/nautobot）は IaC/terraform/aws-managed/ の同じルートへのシンボリックリンクに oss.auto.tfvars を足しただけ。
- base/core の oss.tf はマネージド版と共通のファイル（IaC/terraform/aws-managed/base/core/oss.tf）。var.project が nwc-oss のときだけ SG と EFS を作る。
:::

## データの流れも 6 段のままで、格納とグラフの置き場だけが替わる

- 1 収集: 4 種のコレクターが Kafka のトピック 5 つへ書く
- 2 格納: ECS の Spark 3 つが格納先 4 つへ書き分ける
- 3 可視化: Grafana がログとメトリクス、Splunk が全部を見せる
- 4 アラート: 同じ 4 種を SNS へ出し、Neo4j の status を変える
- 5 ワークフロー: link_down だけが人の承認を経て lab を直す
- 6 エージェント: MCP ツールで調べ、Neo4j と GDS で答える

::: notes
- 元の 6 枚目は絵だったが、1 段目に古い収集（Telegraf が trap・syslog を受け gNMI・SNMP を取る）が描かれていたので、マネージド版の 4 枚目と同じ形の箇条書きにした（build.md）。
- 1: gnmic・Telegraf（trap）・syslog-ng・GoFlow2 が Kafka の 9092 へ書く。トピックは metrics・gnmi・traps・logs・flows で、マネージド版と同じ。
- 2: Spark のジョブは iceberg（S3 Tables）・splunk（HEC）・http（OpenSearch と VictoriaMetrics の vminsert）。格納先 4 つは S3 Tables・OpenSearch・VictoriaMetrics・Splunk で、ops/oss/up.sh はいつも 4 つとも作る。
- 3: Grafana のデータソースは OpenSearch と vmselect（PromQL）。
- 4・5: SNS → status の Lambda が Neo4j の status を書く。link_down は SQS → Temporal → Web の承認タブ → SSM Run Command（マネージド版と同じ）。
- 6: エージェントの centrality は GDS、root_cause と topology_graph の source は neo4j（docs/oss-variant.md）。
:::

## OSS への通信は SG だけで絞り、Kafka は認証の無い 9092 で受ける

| 送り元 | 宛先 | ポート |
|---|---|---|
| 収集 4 種・spark・web | kafka | 9092（認証なし） |
| kafka・victoriametrics | efs | 2049（NFS・TLS） |
| spark・grafana・runtime・lambda | opensearch | 9200（REST） |
| spark・grafana・runtime・lambda | victoriametrics | 8480・8481 |
| web・runtime・lambda・workflow・nautobot | neo4j | 7687（Bolt） |

::: notes
- MSK の SG とその行を外し、表の行を足した。ほかの行はマネージド版のまま（元のデッキのリード文。文字を 20 pt に保つためここへ移した）。
- 正: IaC/terraform/aws-managed/base/core/oss.tf の SG の行。
- 9092 の収集 4 種は telegraf_dialout・gnmic・syslog_ng・goflow2。web は Web の EC2 の Kafbat UI（元のデッキの kafka_ui の SG は 010 で無くなった）。
- victoriametrics: spark が書く 8480（vminsert）、grafana・runtime・lambda が読む 8481（vmselect）。
- EFS の policy は TLS でない接続を拒む。
- ノードどうし: kafka 9092〜9093、opensearch 9300、victoriametrics 8400〜8401。
- web → neo4j 7474 は Neo4j Browser（SSM のポートフォワード）。
- 新しい SG（kafka・opensearch・victoriametrics・neo4j など）はエンドポイントと S3 へ 443。
:::

## ops/oss/up.sh は同じ 10 ルートを作り、down.sh が base/logs を残して逆の順に消す

- 格納先は 4 つ全部と Grafana をいつも作る。KB は作らない
- 打ち直しでは Kafka と OpenSearch を 1 台ずつ入れ替える
- 消すのは接頭辞 `<owner>-nwc-oss` のものだけ
- `KEEP_ECR=1` なら ECR を残す
- 10-08 は 39 分で立ち、時間課金は約 $1.55/h の見積もり
- Fargate の vCPU を 21.5 使う（上限 30。10-08 に測った値）

::: notes
- 作る順（ROOTS は ops/oss/up.sh:161）: base/ecr → base/logs → base/core → agent → lab → stream → graph → nautobot → analytics → workflow。
- ops/oss/down.sh はこの逆の順に 9 ルートを消し、base/logs（logs のバケット）は残す（ops/oss/down.sh:88-91）。
- 元のタイトルの oss/ops/up.sh は、017 で ops/oss/up.sh に移ったので直した（中身の主張は同じ）。
- 1 台ずつの入れ替えは ops/oss/roll-nodes.sh。stream と analytics の apply の前に、変わる台をリーダーでない方から 1 台ずつ apply し、間で健全に戻るのを ECS Exec で待つ。OSS_ROLL=0 で一度に入れ替える（docs/oss-variant.md）。2026-10-08 の 2 回目で Kafka の 3 台を入れ替えて rc=0（13 分 57 秒）。標準入力が端末でないときに付ける疑似端末のうち、Linux の script -q -c の形は AWS では未確認。
- 39 分 05 秒（rc=0）と約 $1.55/h は docs/verification/20261008-oss-aws.md（見積もりは公開の価格表から。請求書では確かめていない）。マネージド版（約 $2.92/h）の約半分。
- 21.5 vCPU は 10-08 の 22 サービスで測った値（ARM 19.5 + Splunk の x86 2）。その後に外れた部品があるので、いまの構成での値は測っていない。
- 元のデッキの「EFS は $0.36 / GB 月。時間課金の合計と所要時間は docs にまだ無い」は、10-08 の記録で合計と所要時間が出たので置き換えた。EFS の単価は docs/cycles/005-oss-on-ecs/design.md。
:::

## 2026-10-08 に AWS で 2 回目を立て、5 つとも動いて端から端までつながった

| OSS | 確かめたこと |
|---|---|
| Kafka | 1 台止めても受け続け、1 台ずつ入れ替えられた |
| OpenSearch | 1 台止めると yellow で検索でき、green に戻った |
| VictoriaMetrics | 396 系列。vmstorage を 1 台止めても欠けない |
| Neo4j + GDS | Job が Neo4j に書き、止めても 2 段で戻った |
| Spark | S3 Tables に 59,991 行。Splunk ほかにも入った |

::: notes
- 正: docs/verification/20261008-oss-aws.md。1 回目は 2026-10-07（元のデッキの 9 枚目）。
- 全体: lab でリンクを落とすと、アラート → Temporal のワークフロー → 修復案 → 承認 → 修復 → verified まで一続きで通った。異常の検知は約 2 分、承認から verified まで 76 秒。エージェントの centrality・search_logs・query_metrics が答えた。
- 1 台ずつ止めた: kafka-2・vmstorage-2・opensearch-2。Kafka は metrics が増え続け、OpenSearch は yellow で 33 件を検索でき、VictoriaMetrics の最新の値の古さは 8.9 秒。21:37:54（UTC）に green・under-replicated 0 に戻った。
- Kafka の入れ替え: 1 回目は端末の無いシェルで ECS Exec が EOF で切れて止まり、疑似端末を付けた 2 回目で rc=0。
- Neo4j: Nautobot の Job と JobHook が書いた。止めたあと sync-graph --oss と Job の 2 段で戻った（変更履歴 19 件）。
- Spark: S3 Tables 59,991 行（21:28）。Splunk は gnmi 352・logs 14・metrics 66,946・traps 5（120 分の窓）。
- down.sh は rc=1（1 時間 41 分 49 秒）。時間課金のあるものは全部消え、Runtime の ENI に掴まれた VPC 一式、SSM のパラメータ 1 つ、KEEP_ECR=1 の ECR 18 個が残った（どれも時間課金なし）。rc=1 は同じ名前の VPC が 2 つあったための不具合（記録の「不具合」5）。
:::

## 未確認は 4 つで、2 つはマネージド版と並べないと確かめられない

| 項目 | 分かっていないこと |
|---|---|
| Kafka（EFS） | 日単位で流したときの遅さやロック |
| VictoriaMetrics | vminsert だけが起き直したとき |
| Neo4j + GDS | Neptune と同じ並びになるか（並べる要） |
| 並べて立てる | vCPU の上限 30 に OSS 版だけで 21.5（並べる要） |

::: notes
- 正: docs/oss-variant.md の「AWS で確かめたこと」の表の右の列。
- 設計のリスクから: OpenSearch はデータの 2 台が同時に落ちるとデータが消える。
- 元のデッキの「Kafka と OpenSearch の入れ替え（ローリング）は未実装」「Nautobot の Job → Neo4j の同期は未接続」は、どちらも 2026-10-08 までに入って AWS で確かめたので外した。
- そのほか未確認: ops/oss/roll-nodes.sh の Linux の script -q -c の形、21.5 vCPU のいまの構成での値。
:::
