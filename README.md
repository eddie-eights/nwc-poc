# nwc-poc — ネットワーク運用の PoC（Terraform）

ブラウザのチャットから AgentCore Runtime のエージェントに聞くと、Amazon Nova 2 Lite がトポロジのツール（と任意の手順書の検索）を使って答える。
lab（containerlab の Nokia SR Linux で組んだ Spine-Leaf）の機器の gNMI・SNMP の trap・syslog を Kafka → Spark で格納先に流し、Grafana と Splunk のアラートで異常を見つける。
そこから SNS → SQS で Temporal のワークフローを起こして原因を調べて修復案を出し、人が承認したら直す、までを試せる。
全部を**プライベートサブネット**に作り、**AWS の API へは VPC エンドポイントだけを通す閉域**にする。
この VPC のエンドポイントを通らない呼び出しは、IAM とリソースポリシーの Deny（`aws:SourceVpc`）で拒む（鍵が漏れても VPC の外からは使えない）。
VPC にインターネットへの経路は無い（NAT Gateway も IGW も作らない。Splunk も VPC の中の ECS に立てる）。PC からは SSM のポートフォワーディングで入り、インターネットからの受信ルールは無い。

```mermaid
flowchart LR
  PC["利用者の PC<br/>localhost:8080"] -->|"SSM ポートフォワーディング"| WEB["Web の EC2<br/>Gradio"]
  WEB -->|"invoke_agent_runtime"| RT["AgentCore Runtime<br/>Nova 2 Lite + ガードレール"]
  RT --> KB["ナレッジベース<br/>CREATE_KB=1"]
  RT --> TOOLS["ツール<br/>Neptune / OpenSearch / Prometheus"]
  LAB["lab の EC2<br/>containerlab"] --> TG["gnmic / Telegraf / syslog-ng / GoFlow2<br/>ECS Fargate"] --> MSK["MSK"] --> SPARK["Spark<br/>EMR Serverless"]
  SPARK --> STORE["S3 Tables / OpenSearch / Prometheus<br/>（+ Splunk）"]
  PC -->|"SSM ポートフォワーディング<br/>（Web の EC2 を踏み台）"| GRAF["Grafana<br/>ECS Fargate"] -->|"Prometheus / OpenSearch を見る"| STORE
  GRAF -->|"Grafana のアラート<br/>（link_down / BGP / IS-IS / trap）"| SNS["SNS<br/>prefix-alerts"]
  STORE -.->|"Splunk のアラート<br/>（link_down / trap / BGP / IS-IS）"| SNS
  SNS -->|"Lambda"| NEP["Neptune<br/>トポロジ（status）"]
  SNS -->|"SQS"| WF["Temporal<br/>ECS Fargate"]
  WF -->|"修復案（状態と証跡）"| PAUDIT["S3 Tables<br/>proposal_events"]
  WEB -->|"承認・却下<br/>SQS（prefix-decisions）"| WF
  WEB -.->|"Athena で読む"| PAUDIT
  WF -->|"調査"| RT
  WF -->|"承認後に修復"| LAB
```

## 作るもの

作り方は 3 つある。このページの手順はマネージド版。

- **マネージド版**（`IaC/terraform/aws-managed/`、`ops/up.sh` / `ops/down.sh`）
  できる限り AWS のマネージドサービスで作る。`deploy.env` で要る機能だけ `1` にし、何も書かなければ土台だけを作る。
- **OSS 版**（`IaC/terraform/oss/`、`ops/oss/up.sh` / `ops/oss/down.sh`）
  マネージドの部分を OSS（Kafka・Neo4j・OpenSearch・VictoriaMetrics・Spark）にして ECS で動かす。できること・費用・メンテナンス性の比較は [oss-variant.md](docs/oss-variant.md)。
  - AWS で 2 回立てて確かめた。マネージド版と同じアカウントに並べて立てるのは未確認。
- **手元の compose**（`docker/compose/`）
  AWS を使わず、WSL2 の中だけでパイプライン（lab → 収集 → Kafka → Spark → OpenSearch / Prometheus / Splunk → Grafana）を一周させる。
  - SNS・Neptune・Nautobot・ワークフロー・エージェントは無い。
  - 手順は [docker/compose/README.md](docker/compose/README.md)（WSL での通しは未確認）。

マネージド版の機能:

| 機能 | できること |
|---|---|
| 土台（必ず） | VPC、SSM のエンドポイント 2 本、Web の EC2（t4g.medium。stream を作る回は Kafbat UI も同居）、S3、ECR |
| `AGENT=1` | チャット（Runtime + ガードレール）。`CREATE_KB=1` で手順書の検索も |
| `PIPELINE=1` | lab → 収集（gnmic・Telegraf・syslog-ng・GoFlow2。ECS）→ MSK → Spark → 格納先（`STORES` で選ぶ）。Grafana と Splunk のアラート → SNS、Neptune のトポロジ（アラートで status が変わる）、Nautobot（機器の一覧とケーブルの正） |
| `WORKFLOW=1` | アラート（SNS → SQS）で Temporal を起こし、調査 → 承認 → 修復。AGENT と PIPELINE と、アラートの送り手（Grafana か Splunk）が要る |

`PIPELINE=1` だけで約 $2.87/h（AWS の料金表から算出。`ops/up.sh` の目安では $2.92/h）、既定のまま 1 か月置くと約 $2,100（約 31 万円）になるので、**使い終わったら当日中に消す。** 内訳とデプロイのパターンごとの値は [deploy.md の費用](docs/deploy.md#費用)。

デバッグ用の EC2（lab + Telegraf を 1 台。MSK / ECS を作らずに機器と Telegraf の設定を確かめる）は `deploy.env` の機能ではない。
`ops/lab-debug.sh up` / `down` で作る・消す別のスタックで、`ops/down.sh` では消えない（[pipeline.md](docs/pipeline.md)）。

## 手順

コマンドは bash 用で、Mac と WSL2 で同じ。**展開したフォルダの直下で打つ。**

1. 道具を入れる: AWS CLI v2、Terraform 1.11 以上、Docker buildx（arm64）、Session Manager plugin、python3 か uv、curl（[setup.md](docs/setup.md)）。
2. AWS に入る: `aws login --profile <プロファイル>`、`aws configure sso`、長期キーのどれか。`sts get-session-token` の一時セッションでは止まる。
3. GitHub の「Download ZIP」で取った zip を Linux 側のホームに展開して入る（WSL2 は `/mnt/c` を使わない）。`git clone https://github.com/eddie-eights/nwc-poc.git` なら `~/nwc-poc` に入る。

```bash
unzip ~/nwc-poc-main.zip -d ~ && cd ~/nwc-poc-main
```

4. 設定を写し、空の `OWNER=` に自分の名前を書き、要る機能を `1` にする。

```bash
cp deploy.env.example deploy.env
```

   手で書くファイルはこの `deploy.env` だけ。`IaC/terraform/aws-managed/<ルート>/terraform.tfvars.example` と `.env.example` は写さなくてよい。
   この 2 つは terraform を手で打つとき、Web を手元で動かすときにだけ使う（[docs/development.md](docs/development.md)）。

5. 作る。終わると `http://localhost:8080` へのポートフォワーディングが開く。

```bash
ops/up.sh
```

6. 使い終わったら消す。

```bash
ops/down.sh
```

- 所要時間は AGENT だけで 10〜15 分、PIPELINE で 40〜60 分（MSK だけで約 30 分。[2026-10-09 の記録](docs/verification/20261009-aws-managed.md)で `Creation complete after 30m0s`）。
- `ops/up.sh` はできているものを飛ばすので、途中で落ちたら打ち直せばよい。
- リージョンは東京（`ap-northeast-1`）で固定。

## GUI の一覧

`<prefix>` は `<OWNER>-nwc-poc`。リージョンは東京（`ap-northeast-1`）。

### 自分で立てている GUI（PC の localhost で開く）

VPC の中にあるので、どれも SSM のポートフォワードを打ってから開く（Web の EC2 が踏み台）。コマンドは `ops/up.sh` の最後に出る。あとから出すなら下の「開くコマンド」。

| GUI | URL | 要る機能 | ログイン | 開くコマンド（打ったままにする） |
|---|---|---|---|---|
| Web（チャット / トポロジ / 承認） | http://localhost:8080/ | 土台（必ず） | 無し | `terraform -chdir=IaC/terraform/aws-managed/base/core output -raw start_session_command` |
| Nautobot（機器とケーブルの台帳、Job の結果） | http://localhost:8081/ | `PIPELINE=1` | `admin` / SSM のパスワード | `terraform -chdir=IaC/terraform/aws-managed/pipeline/nautobot output -raw port_forward_command` |
| Grafana（ダッシュボード、アラート） | http://localhost:3000/ | `PIPELINE=1`（`STORES` の `grafana`。既定で入っている） | `admin` / SSM のパスワード | `terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw grafana_port_forward_command` |
| Splunk（ログの検索、アラート） | http://localhost:8000/ | `PIPELINE=1`（`STORES` の `splunk`。既定で入っている） | `admin` / SSM のパスワード | `terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw splunk_port_forward_command` |
| Splunk の cluster manager（indexer のクラスターの状態） | http://localhost:8001/ | `PIPELINE=1` で `SPLUNK_AZ_NUM` が 2 か 3 | `admin` / Splunk と同じパスワード | `terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw splunk_cm_port_forward_command`（`ops/up.sh` の最後には出ない） |
| Kafbat UI（Kafka のトピックと中身） | http://localhost:8082/ | `PIPELINE=1`（stream） | `admin` / SSM のパスワード | `terraform -chdir=IaC/terraform/aws-managed/pipeline/stream output -raw kafka_ui_port_forward_command` |
| Temporal UI（ワークフローの実行の履歴） | http://localhost:8233/ | `WORKFLOW=1` | 無し | `ops/up.sh` の手順 8-5 が出す（タスクの IP が要る。[workflow.md](docs/workflow.md)） |

- `terraform ... output -raw ...` は「打つコマンド」を表示するだけなので、出てきた `aws ssm start-session ...` をそのまま打つ。
- パスワードを出すコマンドは、同じルートの出力 `password_command`（Nautobot）/ `grafana_password_command` / `splunk_password_command` / `kafka_ui_password_command`（Kafbat UI）。
- Nautobot でよく見る場所: Devices → Devices（機器）、Devices → Cables（ケーブル）、Jobs → Job Results（Neptune / gnmic への同期の結果）。使い方は [nautobot.md](docs/nautobot.md)。

### AWS のマネージドサービス（AWS マネジメントコンソールで見る）

専用の画面は無く、コンソールの各サービスのページで見る。コンソールの右上のリージョンを東京にする。

| 見たいもの | サービス | コンソールでの行き方 |
|---|---|---|
| エージェント | Bedrock AgentCore | Amazon Bedrock AgentCore → Agent Runtime / Gateways |
| ガードレール、手順書の検索 | Bedrock | Amazon Bedrock → ガードレール / ナレッジベース |
| Kafka | MSK | Amazon MSK → クラスター → `<prefix>-stream`（コンソールではトピックの中身は見られない。モニタリングと設定だけ。中身は上の Kafbat UI で見る） |
| Spark のジョブ | EMR Serverless | Amazon EMR → EMR Serverless → EMR Studio を開く → アプリケーション → `<prefix>-spark` → ジョブ実行 → 「Spark UI」 |
| 生データの表 | S3 Tables | Amazon S3 → テーブルバケット → `<prefix>-tables`（中身を引くのは Athena。カタログ `s3tablescatalog`） |
| ログの検索先 | OpenSearch Serverless | Amazon OpenSearch Service → サーバーレス → コレクション。**OpenSearch Dashboards は開けない**（コレクションは VPC エンドポイントからだけ届く）。中身は Grafana で見る |
| メトリクス | Managed Service for Prometheus | Amazon Prometheus → ワークスペース。グラフの画面は無いので Grafana で見る |
| トポロジのグラフ | Neptune Analytics | Amazon Neptune → Analytics → グラフ。グラフを見る画面は無いので、Web の「トポロジ」タブで見る |
| Nautobot の DB | RDS | Amazon RDS → データベース |
| コンテナ | ECS | Amazon ECS → クラスター → サービス → タスク → 「ログ」 |
| アラートの流れ | SNS / SQS / Lambda | Amazon SNS → トピック、Amazon SQS → キュー、AWS Lambda → 関数 → 「モニタリング」 |
| ログ | CloudWatch Logs | CloudWatch → ロググループ → `<prefix>` で絞る |
| EC2（Web、lab） | EC2 / Systems Manager | EC2 → インスタンス。中に入るのは Systems Manager → セッションマネージャー（画面は無い。シェルだけ） |

## よく使うキー

| キー | 何 |
|---|---|
| `OWNER` | **必須。**自分の名前（英小文字で始まる 14 文字まで）。リソース名と `Project` タグが `<owner>-nwc-poc` になる。作ったあとで変えない |
| `AGENT` / `PIPELINE` / `WORKFLOW` / `CREATE_KB` | 作る機能 |
| `SKIP_LAB` / `SKIP_STREAM` / `SKIP_ANALYTICS` / `SKIP_GRAPH` | PIPELINE の一部を外す |
| `STORES` | analytics の格納先。`s3`（S3 Tables）/ `grafana`（OpenSearch + Prometheus + Grafana）/ `splunk`（Splunk）をカンマで並べる。既定は 3 つとも（`s3,grafana,splunk`）。外したまとまりは作らない（前に作っていればデータごと消える） |
| `SNMP_POLL` | 使わない（IF の状態とカウンターは gnmic が gNMI で取る）。書いてあると `ops/up.sh` が注意を出すだけなので、消してよい |
| `*_AZ_NUM` | 冗長化用。リソースごとに何 AZ に置くか（`ENDPOINTS_AZ_NUM` / `MSK_AZ_NUM` / `RUNTIME_AZ_NUM` / `SPLUNK_AZ_NUM` など 10 個）。既定は 1（MSK だけ 2）。`SPLUNK_AZ_NUM` を 2 か 3 にすると Splunk が indexer のクラスターになる。ふだんの検証では書かない |
| `IMAGE_TAG` | `app/agentcore/` や `app/temporal/` を変えたら `v2` などに上げる |
| `HTTP_SEND` | Spark が HTTP の格納先（OpenSearch / Prometheus / Splunk）へ送る所。既定 `driver`。量が増えたら `executor`（費用は変わらない） |
| `MAX_OFFSETS_PER_TRIGGER` | Spark の 1 つのクエリが Kafka の 1 回のトリガー（60 秒）に読む件数の上限（全パーティションの合計）。既定 `10000`、`0` で上限なし。格納先ごとに `MAX_OFFSETS_PER_TRIGGER_ICEBERG` / `_SPLUNK` / `_OPENSEARCH` / `_PROMETHEUS` で上書きできる（既定は空 = 共通の値） |
| `SYSLOG_STANDARD` | stream の syslog-ng が受ける syslog の形式。既定 `RFC3164`（本番の Cisco IOS）。lab の SR Linux のログまで見るなら `RFC5424` |
| `KEEP_ECR` | `1` で `ops/down.sh` が ECR を残す |

ほかのキーと、`ops/up.sh` / `ops/down.sh` が何をするかは [deploy.md](docs/deploy.md)。その回だけ変えるなら `PIPELINE=1 ops/up.sh` のように環境変数で渡す。

## 注意

- `IaC/terraform/aws-managed/<ルート>/terraform.tfstate` を消さない。消すと `ops/down.sh` が消せず、課金が残る。
- up と down は同じ PC で打つ。
- 機能を `0` に戻して打っても、前に作ったものは消えない。消すのは `ops/down.sh` だけ。
- デバッグ用の EC2 を作ったなら `ops/lab-debug.sh down` も打つ（`ops/down.sh` は消さない）。

## ドキュメント

| ファイル | 中身 |
|---|---|
| [architecture/](docs/architecture/README.md) | 構成図（スライドはマネージド版 [architecture-managed.pptx](docs/architecture-managed.pptx) と OSS 版 [architecture-oss.pptx](docs/architecture-oss.pptx)）、どのファイルがどこで動くか、名前とタグ、ログ。中身は [core](docs/architecture/core.md)（閉域・SG）/ [agent](docs/architecture/agent.md) / [pipeline](docs/architecture/pipeline.md) / [workflow](docs/architecture/workflow.md) に分けてある |
| [setup.md](docs/setup.md) | 前提（AWS の権限、ネットワーク、Mac / WSL2、社内 PC の CA） |
| [deploy.md](docs/deploy.md) | `deploy.env` の全キー、費用、`ops/up.sh` / `ops/down.sh` の中身、利用者に渡す権限、試す質問 |
| [collection.md](docs/collection.md) | 機器から集めるデータ（syslog・trap・telemetry・性能メトリクス・NetFlow / sFlow）、集める側（gnmic・Telegraf・syslog-ng・GoFlow2）、Cisco MDT の方針、lab での取り方、未決定事項 |
| [cml-sandbox.md](docs/cml-sandbox.md) | DevNet の CML サンドボックスで NX-OS 9000v のファブリックを組み、MDT・gNMI・syslog・trap の形を SR Linux と見比べる手順（未実施） |
| [pipeline.md](docs/pipeline.md) | lab、gnmic・Telegraf・syslog-ng・GoFlow2、デバッグ用の EC2（`ops/lab-debug.sh`）、Spark、Grafana と Splunk のアラート、Neptune のトポロジの使い方 |
| [nautobot.md](docs/nautobot.md) | Nautobot: コンテナと部品の構成、起動から同期まで、使い方、Neptune と組み合わせた使いどころ |
| [workflow.md](docs/workflow.md) | 承認の流れと Temporal UI |
| [alert-comparison.md](docs/alert-comparison.md) | Splunk と Grafana のアラートの比較: 4 種類のアラートを両方で書けたか、障害を入れる手順、遅れと取りこぼしを出す Athena のクエリ、結果 |
| [troubleshooting.md](docs/troubleshooting.md) | うまくいかないとき |
| [oss-variant.md](docs/oss-variant.md) | マネージドの部分を OSS にした版: 目的、マネージドと OSS の置き換え先の対応、AWS で確かめたことと未確認のこと |
| [docker/compose/README.md](docker/compose/README.md) | 手元の docker compose（WSL2）: 前提、立てて障害を入れて見るまでの手順、見る場所、ぶつかりやすいポート、消し方 |
| [hearing.md](docs/hearing.md) | ヒアリング項目: PoC の設計を決めるために相手に確かめたいことと答え |
| [development.md](docs/development.md) | 手元のテスト、変更するときの決まり、Web を手元で動かす |
| [ai-dev-flow.md](docs/ai-dev-flow.md) | AI 開発フロー: PM とエンジニアの AI セッションがサイクル（設計 → 実装 → レビュー）を回す流れ、役割の分担、成果物の置き場。**別の PC で使うときは、[claude-settings](https://github.com/eddie-eights/claude-settings) を clone して `bin/install-skills` を実行する**（`/cycle-*` / `/waiting` と SessionStart フックのリンクを作る。更新のたびに実行し直す） |
| [faq-fukuda-nwc-poc.md](docs/faq-fukuda-nwc-poc.md) | FAQ: ほかの開発者に説明するときに出る質問と答え（収集、Spark、Nautobot、Neptune、格納先、Splunk、OSS への置き換え、AWS の基礎など） |
| [data-stores.md](docs/data-stores.md) | 勉強会メモ: データの置き場、コンテナイメージのアーキテクチャの選び方、Neptune Analytics の基礎、MSK のブートストラップサーバーの受け渡し |
| [GLOSSARY.md](docs/GLOSSARY.md) | 用語集: この PoC で使う言葉（障害とアラートの通知など）の意味と、避ける言い方 |
