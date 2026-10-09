---
title: nwc-poc の構成（マネージド版）
subtitle: 機器のデータを集めて貯め、異常を AI が調べ、人の承認で直す
date: 2026-10-09
source: docs/architecture/、README.md、docs/deploy.md、ops/up.sh・down.sh、IaC/terraform/aws-managed/
---

## データは左から右へ流れ、修復だけが lab へ戻る

閉域の中で、できる限りマネージドサービスで組む。

::: columns

**行き: データ**

- lab の SR Linux 6 台
- 収集 4 種（ECS）→ MSK
- Spark → 格納先 4 つ
- アラート → SNS

---

**戻り: 修復**

- SQS → Temporal
- エージェントが修復を提案
- 人が Web で承認
- SSM で lab を直す

:::

::: notes
- 元の 2 枚目は絵だったが、外した部品（ECS の Kafbat UI、Telegraf の 2 サービス）が描かれていたので 2 列の箇条書きにした（docs/cycles/021-architecture-decks/build.md）。
- 東京リージョン（ap-northeast-1）の閉域（VPC エンドポイント + Deny）で組む（docs/architecture/core.md）。
- lab: docs/architecture/resources/lab-ec2.md。x86 の m6i.xlarge に SR Linux 6 台（s-leaf 2・spine 2・a-leaf 2。IS-IS + iBGP の EVPN-VXLAN）と TRex 1 台（dc1-trex-01）。
- 収集 4 種は gnmic・Telegraf（trap）・syslog-ng・GoFlow2（README.md の「作るもの」、docs/architecture/pipeline.md）。
- Spark は EMR Serverless。格納先 4 つは S3 Tables・OpenSearch Serverless・AMP・Splunk で、STORES（既定は s3,grafana,splunk の 3 つとも）で選ぶ（docs/deploy.md の STORES）。
- アラートは Grafana と Splunk が出す。戻りは SNS → SQS → Temporal → エージェント → Web の承認タブ → SSM Run Command（docs/architecture/workflow.md）。
- Neptune Analytics はトポロジと status を持ち、Web・エージェント・Temporal が読む。Nautobot は機器とケーブルの正。
:::

## 10 の Terraform ルートを ops/up.sh が順に作る

::: columns

| ルート（数） | 中身 |
|---|---|
| base/ecr（6） | ECR |
| base/logs（6） | logs のバケット |
| base/core（39） | VPC・閉域・SG・Web・SNS |
| agent（22） | Runtime・ガードレール・KB |
| workflow（35） | Temporal・MCP・SQS |

数は tf の resource ブロックの数（実数ではない）。

---

| pipeline/（数） | 中身 |
|---|---|
| lab（7） | lab の EC2 |
| stream（57） | MSK・収集 4 種・NLB |
| graph（11） | Neptune・status Lambda |
| nautobot（16） | Nautobot・RDS |
| analytics（54） | EMR・格納先・Grafana・Splunk |

:::

::: notes
- ルートは IaC/terraform/aws-managed/<ルート>。
- 数え方: ルートの *.tf をつないで grep -c '^resource "'（2026-10-10、feat/035-s3-layout b39c543）。count・for_each で増える実数ではない。元のデッキ（2026-10-08）から変わったのは agent 21 → 22 と stream 37 → 57（013 で gnmic・syslog-ng・GoFlow2 が入り、telegraf-dialin が外れた）。
- 2026-10-09 の数えからは、base/logs（6）が 035 で増え、graph 10 → 11、analytics 53 → 54 になった。
- stream の 57 の内訳: telegraf.tf 15、collectors.tf 20（syslog-ng・GoFlow2）、gnmic.tf 12、msk.tf 5、kafka_ui.tf 4（Web の EC2 で動く Kafbat UI の接続先。ECS のタスクではない）、access.tf 1。
- workflow の MCP は AgentCore Gateway。analytics の格納先は S3 Tables・OpenSearch Serverless・AMP。
- ops/up.sh の順（ROOTS は ops/up.sh:470）: base/ecr → base/logs → base/core → agent → lab → stream → graph → nautobot → analytics → workflow。graph は base/core のあと裏で始め、stream のあとで待つ。
- agent は AGENT=1、workflow は WORKFLOW=1、pipeline の 5 つは PIPELINE=1。接頭辞は <owner>-nwc-poc。
- lab-debug は別の CloudFormation スタックで、ここには入らない（ops/lab-debug.sh）。
:::

## データは収集から 6 段で進み、エージェントは最後に呼ばれる

- 1 収集: 4 種のコレクターが MSK のトピック 5 つへ書く
- 2 格納: Spark が STORES の格納先へ書き分ける
- 3 可視化: Grafana がログとメトリクス、Splunk が全部を見せる
- 4 アラート: 同じ 4 種を SNS へ出し、Neptune の status を変える
- 5 ワークフロー: link_down だけが人の承認を経て lab を直す
- 6 エージェント: MCP ツールで調べ、原因と修復案を返す

::: notes
- 元の 4 枚目は絵だったが、1 段目に古い収集（Telegraf が trap・syslog を受け gNMI・SNMP を取る）が描かれていたので箇条書きにした（build.md）。
- 1: gnmic・Telegraf（trap）・syslog-ng・GoFlow2。トピックは metrics・gnmi・traps・logs・flows（docs/architecture/resources/msk.md）。
- 2: 格納先は S3 Tables・OpenSearch Serverless・AMP・Splunk（5 枚目）。
- 4: アラートの 4 種は link_down・bgp_down・isis_down・trap。Grafana のルールと Splunk の保存済みサーチの両方が出す（docs/architecture/pipeline.md:34）。
- 5: SQS → Temporal → Web の承認タブ → SSM Run Command。承認を押してから承認タブに出るまで数秒〜20 秒（docs/workflow.md:27）。
- 6: AgentCore Runtime。ツールは workflow の Gateway（MCP）の先の Lambda。ワークフローのほか、Web のチャットからも呼べる（docs/architecture/agent.md、workflow.md）。
:::

## 収集から格納まで: Spark が STORES のまとまりごとに書き分ける

gnmic は取りに行き、ほかの 3 種は内部 NLB の裏で受ける。

| STORES | 格納先 | トピック |
|---|---|---|
| s3 | S3 Tables（raw_telemetry） | 全部 |
| grafana | OpenSearch Serverless | traps・logs・flows |
| grafana | AMP（Prometheus） | metrics・gnmi |
| splunk | Splunk の HEC | 全部 |

::: notes
- 受ける側の 3 種は Telegraf（trap）・syslog-ng・GoFlow2。gnmic は機器の gNMI（57400/tcp）を購読する。
- MSK のトピックは 5 つ: metrics（gnmic のカウンター）、gnmi（gnmic の状態。on-change）、traps（Telegraf）、logs（syslog-ng）、flows（GoFlow2）（docs/architecture/resources/msk.md）。
- MSK の認証: Telegraf・Spark・Web（Kafbat UI）は IAM の 9098、syslog-ng・GoFlow2・gnmic は SASL/SCRAM の 9096（docs/architecture/core.md の SG の表）。
- 受け口: trap 162/udp、syslog 5140/udp、NetFlow 2055/udp、sFlow 6343/udp（lab の管理網 → NLB）。
- STORES とトピックの対応、ジョブはまとまりごとに 1 つで 1 つ $0.21/h は docs/deploy.md の STORES の行。
:::

## 検知から修復まで: アラートは SNS に集まり、link_down だけが承認つきの修復へ進む

- Grafana のルール 4 つと Splunk の保存済みサーチ 3 つが、同じ 4 種を SNS へ出す
- SNS から status Lambda が Neptune の status を書き、Firehose が `alert_events` に残す
- link_down だけが SQS から Temporal のワークフローを起こす
- エージェントが原因を調べて修復を提案し、人が Web の承認タブで承認する
- 承認すると SSM Run Command で lab を直す。提案の履歴は worker だけが書く

::: notes
- アラートの集め方: docs/architecture/pipeline.md:34。SNS のトピックは <prefix>-alerts（土台）。
- 障害の履歴: Lambda graph-status が Firehose で S3 Tables の alert_events に追記（docs/architecture/pipeline.md:35）。
- 修復案は S3 Tables の proposal_events だけに置き、書くのは worker だけ（docs/architecture/workflow.md:21）。
:::

## SG は用途ごとのポートだけを開け、外からの受信は経路ごと無い

| 送り元 | 宛先 | ポート |
|---|---|---|
| 全ワークロード | エンドポイント・S3 | 443 |
| web（踏み台） | grafana・splunk・workflow・nautobot | 3000・8000・8233・8080 |
| 収集 4 種・spark・web | msk | 9098（IAM）・9096（SCRAM） |
| lab の管理網 | NLB → 受ける側の 3 種 | 162・5140・2055・6343/udp |
| gnmic | lab の管理網 | 57400（gNMI） |

::: notes
- 正: docs/architecture/core.md の SG の表（IaC/terraform/aws-managed/base/core/security_groups.tf の local.sg_flows）。
- 9098 は telegraf_dialout・spark・web（Kafbat UI）、9096 は syslog_ng・goflow2・gnmic。
- 受ける側の 3 種は telegraf_dialout・syslog_ng・goflow2。NLB → 各タスクは 1162/udp と 8080、5140/udp と tcp、2055/udp・6343/udp と 8081（ヘルスチェック）。lab の管理網は 203.0.113.0/24。
- ほかの行: spark → splunk 8088（HEC）、nautobot → nautobot_db 5432、msk どうし 9092〜9098、spark どうし全 TCP、Splunk のクラスター内 8089・9887・9997。
- 開けていないもの: Temporal の gRPC 7233、Splunk の管理 API 8089 の外からの受信。Kafbat UI は Web の EC2 の中なので SG を通らない。
- Neptune Analytics には SG が無く、neptune-graph-data のエンドポイント（443）で届く。
:::

## PIPELINE を既定のまま 1 か月置くと約 $2,100 になる

| 機能 | 時間課金 | 作る時間 |
|---|---|---|
| 土台（必ず） | 約 $0.07/h | 記載なし |
| AGENT=1 | 約 $0.07/h（KB +$0.35） | 10〜15 分 |
| PIPELINE=1 | 約 $2.85/h | 40〜60 分 |
| WORKFLOW=1 | 約 $0.09/h | 記載なし |

PIPELINE と土台で約 $2.92/h、STORES=s3 だけなら約 $1.99/h。使い終わったら当日中に `ops/down.sh` で消す。

::: notes
- 金額は東京の待機の時間課金で、AZ の数が既定のとき（ops/up.sh の費用の目安）。
- 正は ops/up.sh の COST_CENTS の式（ops/up.sh:556〜）。機能ごとに式を切り出して計算した値: 土台 7 セント、AGENT +7、KB +35、PIPELINE +285（STORES 既定）、WORKFLOW +9、PIPELINE と土台 292、STORES=s3 だけ 199。README.md の「作るもの」と同じ。
- 1 か月: $2.92 × 730 h ≈ $2,132（約 32 万円）。元のデッキ（$2.80/h、約 $2,000）から 013 の収集 4 種などで上がった。
- PIPELINE の内訳: Neptune Analytics $0.58、STORES の grafana 約 $0.60、splunk 約 $0.34、Nautobot $0.13 と ecs のエンドポイント $0.014（docs/deploy.md の STORES と Nautobot の行）。
- 作る時間は README.md の手順。PIPELINE の 40〜60 分のうち MSK だけで約 30 分（2026-10-09 の AWS で Creation complete after 30m0s）。
- インターフェース型エンドポイントは 1 本 $0.014/h（1 AZ のとき）。
:::

## ops/down.sh は作った逆の順に消し、Runtime の ENI だけが最大 8 時間残る

- base/logs を除く 9 つのルートを workflow から base/ecr まで逆の順に消す
- 続けて Runtime のロググループ、SSM のパラメータ、MSK の SCRAM の secret を消す
- KMS の鍵は削除を予約する（7 日後に消える）
- `KEEP_ECR=1` なら ECR を残す
- Runtime の ENI が外れるまで VPC・サブネット・runtime の SG が残る（課金なし）
- Lambda の ENI は 20〜40 分残り、裏で消す
- logs のバケットは意図して残す（中身は 7 日で消える）

::: notes
- 正: docs/deploy.md の「消す順」（ops/down.sh）。順は workflow → analytics → nautobot → graph → stream → lab → agent → base/core → base/ecr → Runtime のロググループ → SSM のパラメータ → MSK の SCRAM の secret と KMS の鍵。
- analytics では Spark のジョブを cancel してから消す。nautobot の RDS は最後のスナップショットを取らない。
- SSM のパラメータはタグ ManagedBy=ops/up.sh のものだけ。secret は Secrets Manager の AmazonMSK_<prefix>-syslog-ng / -goflow2 / -gnmic（コレクターごと）、鍵は alias/<prefix>-msk-scram。値は読まない。
- Runtime の ENI が残るあいだは VPC 一式を残して他を消し、終了コード 0 で終わる。次の ops/up.sh が使い回す。
- 20〜40 分残る ENI は graph・workflow・KB（<prefix>-kb-index）の Lambda のもの。
- KEEP_ECR=1 の ECR は 7.39 GB で月 約 110 円（docs/deploy.md の「消したあとに残るもの」）。
- base/logs（logs のバケット <prefix>-logs-<アカウント>）は消さない（ops/down.sh:129-132）。Firehose が書けなかった行を消したあとでも読むため。空のバケットは無料。
:::

## 画面は Web の EC2 を踏み台に localhost で開き、lab の図だけは lab の EC2 から開く

::: columns

**Web の EC2 を踏み台に**

- Web: 8080
- Kafbat UI: 8082
- Nautobot: 8081
- Grafana: 3000
- Splunk: 8000
- Temporal UI: 8233

---

**lab の EC2 から**

- lab の図: 50080
- 先に `sudo lab graph`

:::

::: notes
- どれも SSM のポートフォワードを打ってから開く。正: README.md の「GUI の一覧」。開くコマンドは各ルートの terraform output（例 IaC/terraform/aws-managed/base/core の start_session_command）。
- 要る機能: Web（チャット・トポロジ・承認）は土台、Kafbat UI は PIPELINE（stream。Web の EC2 の Docker。010 で ECS から移った）、Nautobot は PIPELINE、Grafana は PIPELINE の grafana、Splunk は PIPELINE の splunk、Temporal UI は WORKFLOW。
- Splunk の cluster manager は 8001（SPLUNK_AZ_NUM が 2 か 3 のとき）。
- lab の図（containerlab graph）: lab の EC2 で sudo lab graph が 127.0.0.1:50080 に出し、IaC/terraform/aws-managed/pipeline/lab の output graph_port_forward_command で localhost:50080 に開く（docs/pipeline.md）。AWS では未確認。
- 元のタイトル「画面はどれも Web の EC2 を踏み台に localhost で開く」は、lab の図が加わって事実が変わったので直した。
:::
