# マネージド版と OSS 版

## 方針

- **いまの構成は、できる限り AWS のマネージドサービスで作る。**
  自分で立てるのは、マネージドに相当するものが無いか、このアカウントで使えないものだけ（下の表の「自分で立てているもの」）。
- **最終的には、いまの構成とは別に、マネージドの部分を OSS に置き換えた版も作る。**
  いまの構成を書き換えるのではなく、並べて持つ。
- OSS 版は、手元のコンテナでの確認と、terraform と ops の実装（`IaC/terraform/oss/` と `ops/oss/`）が済み、2026-10-07 と 10-08 に AWS で 1 回ずつ立てて動作を確かめた（結果は下の「AWS で確かめたこと」）。
  - 手元で動かすのは [`docker/compose/`](../docker/compose/)。
  - 置き換え先は下の表。設計は [cycles/005-oss-on-ecs/design.md](cycles/005-oss-on-ecs/design.md)。
- **OSS にするのは、下の表で置き換え先を書いた 5 つだけ。**
  ほかはマネージドのまま使う。
- **5 つ以外の道具は、商用で使えるライセンスなら OSS でなくてよい。**

## OSS 版を作る目的

マネージド版と OSS 版を同じ用途で並べて、違いを確かめる。

1. **AWS マネージドでできて、OSS ではできないことを調べる。**
   逆（OSS でできて、マネージドではできないこと）も記録する。
2. **費用の違いを検証する。**
   待機の時間課金と、使った分の課金、機器が増えたときの伸び方。待機の時間課金は料金表から出した（[待機の時間課金を料金表から出す](#待機の時間課金を料金表から出す目的-2-の待機の時間課金2026-10-10)）。
3. **メンテナンス性の違いを検証する。**
   版上げ、パッチ、バックアップ、障害時の復旧、監視、権限の管理、作る・消すの手間。

## いまの構成での分け方

| 役割 | いま（AWS マネージド） | OSS 版 |
|---|---|---|
| エージェントの実行と入口 | Bedrock AgentCore（Runtime、Gateway） | 変えない（マネージドのまま） |
| モデルとガードレール | Bedrock（Amazon Nova 2 Lite、ガードレール） | 変えない（マネージドのまま） |
| 手順書の検索 | Bedrock のナレッジベース + OpenSearch Serverless | 変えない（マネージドのまま）。ただし `ops/oss/up.sh` はナレッジベースを作らない（OpenSearch Serverless を使うので OSS 版には入れない。`CREATE_KB` も読まない） |
| Kafka | MSK | Apache Kafka（KRaft）を ECS に 3 台。データは EFS。内部トピック（`__consumer_offsets` 等）は 1 パーティション（MSK は既定の 50。トピックが最初に作られたときに決まるので、50 で出来ている EFS では消して作り直すまで 50 のまま） |
| Kafka の監視の画面 | MSK のコンソールと CloudWatch | Kafbat UI（Apache 2.0）を Web の EC2 の Docker に 1 つ。ブローカー、トピック、メッセージを見る。トピックの追加とメッセージの送信も画面からできる。コンシューマーの遅れは出ない（Spark は consumer group を作らず、offset を checkpoint に持つ） |
| ストリーム処理 | EMR Serverless（Spark） | Apache Spark 3.5 を ECS に（ジョブごとに 1 タスクで 3 つ。`iceberg`、`splunk`、OpenSearch と VictoriaMetrics に書く `http`）。Splunk へはマネージド版と同じ HEC に書く |
| 生データの表 | S3 Tables（Iceberg） | 変えない（マネージドのまま） |
| ログの検索 | OpenSearch Serverless | OpenSearch を ECS に 3 台（データ 2 台でレプリカ 1、まとめ役だけの小さい 1 台）。データは 3 台ともタスクの一時領域（公式の文書がネットワークファイルシステムを避けるよう書いている）。データの 2 台が同時に落ちると消える。候補として VictoriaLogs を残す |
| メトリクス | Amazon Managed Service for Prometheus | VictoriaMetrics のクラスターを ECS に（vminsert 1、vmselect 1、vmstorage 3、複製数 2）。データは EFS |
| トポロジのグラフ | Neptune Analytics（openCypher。中心性と連結成分は `neptune.algo.*`） | Neo4j Community Edition を ECS に 1 台。アルゴリズムは GDS（Community Edition で動くことを手元で確かめた） |
| Nautobot の DB | RDS | 変えない（マネージドのまま） |
| コンテナの実行 | ECS Fargate | 変えない（マネージドのまま） |
| アラートの配送 | SNS、SQS、Lambda | 変えない（マネージドのまま） |
| ログ | CloudWatch Logs | 変えない（マネージドのまま） |
| 設定とシークレット、踏み台 | SSM（パラメータ、Session Manager） | 変えない（マネージドのまま） |
| 閉域 | VPC エンドポイントと `aws:SourceVpc` の Deny | 変えない（マネージドのまま） |

**Neo4j のクラスターについての注意**

- クラスターは Neo4j Enterprise Edition だけの機能で、Community Edition では組めない。OSS 版の中で、Neo4j だけは 1 台で動く。
- 止まっているあいだは、トポロジの表示、status の更新、エージェントのトポロジの検索ができない。
- データはタスクの一時領域にある（Neo4j は NFS を非対応と明記している）。タスクが入れ替わると消えるので、下の「Neo4j を起こし直したあとの戻し方」の 2 段で lab の定義と Nautobot から同期し直す。

**Neo4j を起こし直したあとの戻し方**

タスクが HEALTHY になったら（止めてから起こし直すと 1 分ほど）、次の順に打つ。2026-10-08 に AWS で確かめた手順（[verification/20261008-oss-aws.md](verification/20261008-oss-aws.md) の「Neo4j を止める」）。

1. `OWNER=<owner> ops/sync-graph.sh --oss`
   - lab の定義から、物理層（機器・インタフェース・回線）と IP 層、EVPN・BGP 層を入れる。中心性もこれで答えるようになる。
   - 変更履歴（`change` の頂点）は入らず、0 件のまま。
2. Nautobot の Job「gnmic とグラフ DB に同期」を手で打つ
   - Nautobot の変更履歴と、Nautobot で足した機器と回線を Neo4j に書く。10-08 は変更履歴が 0 件から 19 件に戻った。

順番はこの順にする。先に Job を打つと機器が入ってグラフが空でなくなり、`--replace` なしの `ops/sync-graph.sh` は何もしない。`--replace` を付けると lab の定義で上書きするので、Job で入れた Nautobot の機器と回線が消える。

自分で立てているもの（マネージド版でも OSS か自前のコンテナ）:

- Telegraf、gnmic、syslog-ng（AxoSyslog）、GoFlow2、Grafana、Temporal、Nautobot（Redis と一緒）、Kafbat UI、containerlab（lab）
- Splunk（OSS ではないが、VPC の中の ECS に自分で立てている）

Amazon Managed Grafana は、このアカウントに IAM Identity Center が無くて使えないので Grafana OSS にしている（[deploy.md](deploy.md)）。

## OSS 版の実装の置き場と、いまの状態

| 置き場 | 中身 |
|---|---|
| `IaC/terraform/oss/` | OSS 版の terraform。ルートごとのディレクトリに、変えないファイルは `IaC/terraform/aws-managed/` のファイルへのシンボリックリンク、OSS 版だけのファイル（`kafka.tf`、`opensearch.tf` など）と `oss.auto.tfvars`（`project = "nwc-oss"`）を置く。state は別 |
| `ops/oss/up.sh`、`ops/oss/down.sh` | OSS 版の作る・消す。接頭辞は `<owner>-nwc-oss` で、マネージド版と並べて立てられる。SSM のパラメータのタグは `ManagedBy=ops/oss/up.sh`（2026-10-09 より前に `oss/ops/up.sh` で立てたものは、そのときの commit の `oss/ops/down.sh` で消す） |
| `ops/oss/oss-images.sh` | イメージの名前と版の正（1 か所） |
| `ops/oss/roll-nodes.sh`、`ops/oss/roll_health.py` | Kafka と OpenSearch を 1 台ずつ入れ替える。`ops/oss/up.sh` が stream と analytics の apply の前に打つ |
| `docker/images/spark/Dockerfile`、`docker/images/neo4j/Dockerfile` | ECS 向けの Spark と Neo4j（GDS 入り）のイメージ |
| `ops/common.sh`、`ops/up-common.sh`、`ops/down-common.sh` | マネージド版と OSS 版の共通の関数 |

- **`ops/oss/up.sh` が作るルートは、マネージド版の `ops/up.sh` と同じ 10。**
  `base/ecr`、`base/logs`、`base/core`、`agent`、`pipeline/lab`、`pipeline/stream`、`pipeline/graph`、`pipeline/nautobot`、`pipeline/analytics`、`workflow`。
  Grafana、Web の部品、エージェント、workflow、Neo4j への同期までつないである（何がどう動くかは [cycles/005-oss-on-ecs/design.md](cycles/005-oss-on-ecs/design.md) の「実装の状態」）。
- **機能と格納先は選ばない。**
  `AGENT` / `PIPELINE` / `WORKFLOW` / `STORES` などのキーは読まず、ルートはいつも全部、格納先はいつも `iceberg` / `opensearch` / `prometheus` / `splunk` の 4 つ、Grafana もいつも作る。
  Grafana のダッシュボード（`metrics.json` / `logs.json` / `flows.json`）とアラートのルールはマネージド版と同じファイル（`app/grafana/provisioning`。データソースの uid を `amp` / `aoss-logs` に揃えてある）。
- **Nautobot の Job は Neo4j に書く（2026-10-08 に AWS で確かめた。手で打つ Job と JobHook の両方。[verification/20261008-oss-aws.md](verification/20261008-oss-aws.md)）。**
  - Neo4j のタスクが入れ替わると変更履歴も消え、`ops/sync-graph.sh --oss` では戻らないので、そのあと Job「gnmic とグラフ DB に同期」を打ち直す（上の「Neo4j を起こし直したあとの戻し方」）。
  - Job の名前はマネージド版と同じで、説明に Neo4j と出る。エージェントの `root_cause` と `topology_graph` の `source` は `neo4j`（空なら `neo4j-empty`）
  - graph の state に `neo4j_uri` があるので、`IaC/terraform/aws-managed/pipeline/nautobot` が `GRAPH_BACKEND=neo4j`・`NEO4J_URI` と secrets の `NEO4J_PASSWORD` を渡す。
  - `ops/oss/up.sh` が Neo4j のドライバー入りのイメージ（`app/nautobot/requirements-oss.txt`）を作る。
- **打ち直しで Kafka か OpenSearch のタスク定義が変わると、1 台ずつ入れ替える。**
  terraform だけで apply すると、変わった台が同時に入れ替わる（Kafka は controller の過半数を、OpenSearch はインデックスを失う）。
  - `ops/oss/up.sh` は変わる台を plan で拾い、リーダーでない台から 1 台ずつ `-target` で apply する。
    - 間でクラスターが健全に戻るのを ECS Exec で待つ（手元に Session Manager plugin が要る）。
  - 止まったら `ops/oss/up.sh` を打ち直せば残りの台だけ入れ替える。`OSS_ROLL=0` で一度に入れ替える。
  - 2026-10-08 に AWS で打った:
    - 端末の無いシェルからは ECS Exec が `Cannot perform start session: EOF` で切れて止まった（何も入れ替えない）。
    - `script -q /dev/null` で疑似端末を付けた 2 回目は Kafka の 3 台をリーダーでない 1 → 2 → 3 の順に入れ替えて rc=0（13 分 57 秒）。
  - そのため標準入力が端末でないときは、`ops/oss/roll-nodes.sh` が ECS Exec を `script` で包んで疑似端末を付ける。
    - Linux の util-linux の `script -q -c` と macOS の `script -q /dev/null` を見分ける。Linux の形は AWS では未確認。
  - `script` も打てなければ、何も入れ替えずに「端末から打つか `OSS_ROLL=0`」と出して止まる（手順は [cycles/005-oss-on-ecs/design.md](cycles/005-oss-on-ecs/design.md) の「Kafka と OpenSearch を 1 台ずつ入れ替える」）。
- **Splunk は OSS 版でも変えない。**
  マネージド版と同じ Splunk を立てる。Spark は Splunk の token を、ECS の secrets（SSM の SecureString）から環境変数 `SPLUNK_HEC_TOKEN` で受ける（マネージド版は、ジョブが SSM から読む）。

## 手元のコンテナで確かめたこと

| OSS | 確かめたこと |
|---|---|
| Kafka | 固定の voter（`controller.quorum.voters`）で 3 台が組めた。1 台止めても、書いた 1000 件を全部読めて、書けた。動的な voter は、公式イメージが必要な初期化をしないので組めなかった |
| OpenSearch | 3 台のどれを止めても検索できた |
| VictoriaMetrics | vminsert が vmstorage の 3 台につないだあとに書いたデータは、1 台止めても全部読めた。つなぐ前に書いた行は 1 台にしか入らず、その台を止めると、欠けたことを示さずに値が抜ける。だから vmstorage が上がってから vminsert を起動する |
| Neo4j + GDS | Community Edition で GDS が動いた。中心性は定義どおりの値と一致、島の数は 1 |
| Spark | EMR なしの Spark 3.5 で、Kafka から OpenSearch、vminsert、Iceberg、Splunk（HEC）に書けた |

## AWS で確かめたこと（2026-10-07 と 10-08。どちらも OSS 版だけを立てた）

10-08 の記録は [verification/20261008-oss-aws.md](verification/20261008-oss-aws.md)。

| OSS | 確かめたこと | 残っている未確認 |
|---|---|---|
| Kafka | EFS に置いた 3 台が組めて、1 時間流して 5 つのトピックに入った。1 台止めても残り 2 台で受け続け、戻ると 3 分以内に under-replicated が 0 に戻った。10-08 は、タスク定義を変えて打ち直した `ops/oss/up.sh` が 3 台を 1 台ずつ入れ替え、最後に under-replicated が 0 だった（記録の「1 台ずつ入れ替える」） | 日単位で流したときの遅さやロック。内部トピックのパーティション数の設定が 1 になっていること（下） |
| OpenSearch | 3.9.0 の 3 台が Fargate で `node.store.allow_mmap=false` で起動し、green。trap が入り、1 台止めても yellow で検索できた。まとめ役（1 GB）は OOM で落ちなかった | なし |
| VictoriaMetrics | 6 台分 396 系列が入り、Grafana に出た。vmstorage を 1 台止めて戻しても値は抜けなかった | vminsert だけが起き直したとき |
| Neo4j + GDS | status の Lambda とエージェントの `centrality` が Neo4j を読み書きした。タスクを止めると Web は静的データに落ちて 200 のまま、起こし直して `ops/sync-graph.sh --oss`（物理層と IP 層）と Nautobot の Job（変更履歴）の 2 段で戻った | Neptune の結果と同じ並びになるか（並べて立てる計画は 2026-10-10 に取り下げ。比べるなら片方ずつ立てて同じ入力で取る） |
| Spark | EMR なしで S3 Tables に書けた（1 時間で 115,719 行）。OpenSearch、vminsert、Splunk にも入った | なし |
| 全体 | lab でリンクを落とすと、Grafana のアラート → SNS → Lambda → Neo4j の status → Web のトポロジまでつながった。エージェントの `centrality`、`search_logs`、`query_metrics` が答えた。`ops/oss/down.sh` で消した（下） | マネージド版と並べて立つか（Fargate の vCPU の上限 30 に OSS 版だけで 21.5。それぞれ単独で稼働するので並べて立てないと 2026-10-10 にユーザーが判断） |

- Kafka の内部トピックのパーティション数（「OSS 版の Kafka の内部トピックのパーティションを絞る（022）」）:
  - consumer group が無いと `__consumer_offsets` は作られないので、トピックではなく設定を見る。
  - Kafbat UI の Brokers のブローカー設定か `kafka-configs.sh --bootstrap-server … --describe --entity-type brokers --entity-name 1 --all` で、`offsets.topic.num.partitions` が STATIC_BROKER_CONFIG の 1 になっていること。
- 全体の `ops/oss/down.sh`:
  - 接頭辞 `efukuda-nwc-oss` のリソースが消えた。
  - 設計どおり残るのは、Runtime の ENI が消えるまでの VPC・サブネット・runtime の SG と、`KEEP_ECR=1` の ECR。どちらも時間課金は無い。

## 待機の時間課金を料金表から出す（目的 2 の「待機の時間課金」。2026-10-10）

実測ではなく、Terraform の既定値と AWS の公式の料金表（東京、USD）から算出した（2026-10-10 のユーザー判断。実測だと使った分の課金が混ざり、2 つを同時に立てられない）。

- **比べる構成。**
  どちらも 10 の root を全部立てた状態。マネージド版は `ops/up.sh` の `AGENT=1 PIPELINE=1 WORKFLOW=1`、`STORES` は既定（`s3,grafana,splunk`）、`CREATE_KB=0`、`*_AZ_NUM` は全部既定。OSS 版は `ops/oss/up.sh`（いつも全部立て、Knowledge Base は作らず、Grafana を立てる）。両方に立つものを「共通」、Kafka・OpenSearch・Prometheus・グラフ DB・Spark を「置き換えた 5 つ」として分ける。
- **「待機」の意味。**
  タスク・ジョブ・クラスターは起きていて、lab の telemetry は止まっている状態。使った分の課金（リクエスト、転送、取り込みの GB、Bedrock のトークン）は ≈0 として合計に入れない（節末の別表）。
- **単価の取り方。**
  料金ページ（`aws.amazon.com/jp/<service>/pricing/`）は表を JavaScript で描くので、そのページが読んでいる公開の料金データ（AWS Price List）を 2026-10-10 に取り、東京（`APN1` / `ap-northeast-1`）の値を使った。URL は節末の「単価の出典」。

### 合計

| | マネージド版 | OSS 版 | 差 |
|---|---|---|---|
| 1 時間 | 2.99 USD | 1.66 USD | -1.33 USD（OSS 版は 56%） |
| 1 日（参考） | 71.7 USD | 39.9 USD | -31.9 USD |
| 30 日（参考） | 2,152 USD | 1,196 USD | -956 USD |

- 内訳は、共通 0.893 + マネージド固有 2.096 = 2.989、共通 0.893 + OSS 固有 0.769 = 1.662。
- `ops/up.sh` が表示する見積もり（2026-09-14 の Price List をセント単位に丸めたもの）は同じ構成で約 3.05 USD/h で、2% 以内で合う。README の「`PIPELINE=1` だけで約 2.92 USD/h」は AGENT と WORKFLOW を含まない値。

### 共通の部分（両方の版に立つもの。0.893 USD/h）

| リソース | 数量 | 単価（USD） | USD/h |
|---|---|---|---|
| web の EC2 t4g.medium | 1 | 0.0432 /h | 0.0432 |
| web の EBS gp3 16 GB | 16 GB | 0.096 /GB-月 | 0.0021 |
| lab の EC2 m6i.xlarge | 1 | 0.248 /h | 0.2480 |
| lab の EBS gp3 24 GB | 24 GB | 0.096 /GB-月 | 0.0032 |
| VPC の interface endpoint（共通の 14 本。1 AZ） | 14 | 0.014 /本/AZ/h | 0.1960 |
| Fargate ARM 0.25 vCPU / 0.5 GB（Telegraf、gnmic、syslog-ng、GoFlow2） | 4 | 0.0123 /タスク/h | 0.0493 |
| Fargate ARM 0.5 vCPU / 1 GB（Grafana） | 1 | 0.0246 /タスク/h | 0.0246 |
| Fargate ARM 2 vCPU / 4 GB（Nautobot + Redis） | 1 | 0.0986 /タスク/h | 0.0986 |
| Fargate ARM 1 vCPU / 2 GB（Temporal worker） | 1 | 0.0493 /タスク/h | 0.0493 |
| Fargate x86 2 vCPU / 4 GB + 一時領域 40 GiB（Splunk） | 1 | 0.1232 + 0.0027 /タスク/h | 0.1259 |
| 内部 NLB（Telegraf の dial-out） | 1 | 0.0243 /h | 0.0243 |
| RDS for PostgreSQL db.t4g.micro（Nautobot。Single-AZ） | 1 | 0.025 /h | 0.0250 |
| RDS の gp3 20 GB | 20 GB | 0.138 /GB-月 | 0.0038 |
| **小計** | | | **0.8932** |

- 共通の 14 本の endpoint: `ssm` `ssmmessages` `ecr.api` `ecr.dkr` `logs` `s3tables` `sns` `kinesis-firehose` `bedrock-runtime` `bedrock-agentcore` `ecs` `sqs` `bedrock-agentcore.gateway` `athena`（`ops/oss/up.sh` の `ENDPOINTS`。`ops/up.sh` も同じ 14 本に、下の 3 本を足す）。S3 の gateway endpoint は無料。
- Fargate の単価（東京）: ARM は vCPU 0.04045 + GB 0.00442、x86 は vCPU 0.05056 + GB 0.00553（USD/h）。一時領域は 20 GiB を超えた分だけ 0.000133 USD/GB-h。
- Kafbat UI は web の EC2 の中の Docker で動くので、追加の時間課金は無い。

### マネージド版に固有の部分（置き換えた 5 つ。2.096 USD/h）

| 置き換えた部分 | リソース | 数量 | 単価（USD） | USD/h |
|---|---|---|---|---|
| Kafka | MSK kafka.m5.large のブローカー | 2 | 0.271 /h | 0.5420 |
| Kafka | MSK のブローカーストレージ 10 GB × 2 | 20 GB | 0.12 /GB-月 | 0.0033 |
| Kafka | KMS の顧客管理の鍵（SCRAM の暗号化） | 1 | 1.00 /月 | 0.0014 |
| Kafka | Secrets Manager のシークレット（SCRAM。コレクターごと） | 3 | 0.40 /月 | 0.0016 |
| Kafka | interface endpoint `secretsmanager` | 1 | 0.014 /h | 0.0140 |
| OpenSearch | OpenSearch Serverless の OCU（最小 1 OCU = indexing 0.5 + search 0.5） | 1 OCU | 0.334 /OCU-h | 0.3340 |
| OpenSearch | OpenSearch Serverless の VPC endpoint | 1 | 0.014 /h | 0.0140 |
| Prometheus | Amazon Managed Service for Prometheus のワークスペース | 1 | 時間課金なし | 0.0000 |
| Prometheus | interface endpoint `aps-workspaces` | 1 | 0.014 /h | 0.0140 |
| グラフ DB | Neptune Analytics 16 m-NCU（レプリカ 0） | 1 | 0.581 /h | 0.5810 |
| グラフ DB | interface endpoint `neptune-graph-data` | 1 | 0.014 /h | 0.0140 |
| Spark | EMR Serverless のストリーミングジョブ（driver 1 vCPU / 2 GB + executor 1 vCPU / 2 GB × 2。ARM） | 3 | 0.1922 /ジョブ/h | 0.5767 |
| **小計** | | | | **2.0960** |

- EMR Serverless の単価（東京、ARM）: vCPU 0.052585 + GB 0.005746（USD/h）。1 ジョブ = 3 vCPU + 6 GB。ワーカー 1 つにつき 20 GB のストレージは単価に含まれる。
- ストリーミングジョブは待機中も走り続けるので、`idle_timeout_minutes`（15 分）で止まるのはジョブが無いときのアプリケーションだけ。

### OSS 版に固有の部分（置き換えた 5 つ。0.769 USD/h）

| 置き換えた部分 | リソース | 数量 | 単価（USD） | USD/h |
|---|---|---|---|---|
| Kafka | Fargate ARM 1 vCPU / 2 GB（KRaft のノード。EFS に保存） | 3 | 0.0493 /タスク/h | 0.1479 |
| OpenSearch | Fargate ARM 1 vCPU / 4 GB + 一時領域 30 GiB（data ノード） | 2 | 0.0581 + 0.0013 /タスク/h | 0.1189 |
| OpenSearch | Fargate ARM 0.5 vCPU / 1 GB（cluster manager） | 1 | 0.0246 /タスク/h | 0.0246 |
| Prometheus | Fargate ARM 0.5 vCPU / 1 GB（VictoriaMetrics の vmstorage × 3、vminsert、vmselect） | 5 | 0.0246 /タスク/h | 0.1232 |
| グラフ DB | Fargate ARM 1 vCPU / 4 GB（Neo4j Community + GDS） | 1 | 0.0581 /タスク/h | 0.0581 |
| Spark | Fargate ARM 2 vCPU / 4 GB（ジョブ `iceberg` `splunk` `http`） | 3 | 0.0986 /タスク/h | 0.2957 |
| 共通の土台 | EFS（elastic。Kafka と vmstorage の保存先。マウントターゲットは無料） | 1 | 保存量と読み書きの分だけ | 0.0000 |
| **小計** | | | | **0.7685** |

### 差（置き換えた部分ごと）

| 置き換えた部分 | マネージド版 USD/h | OSS 版 USD/h | 差 USD/h |
|---|---|---|---|
| Kafka | 0.5623 | 0.1479 | -0.4144 |
| OpenSearch | 0.3480 | 0.1436 | -0.2044 |
| Prometheus | 0.0140 | 0.1232 | +0.1092 |
| グラフ DB | 0.5950 | 0.0581 | -0.5369 |
| Spark | 0.5767 | 0.2957 | -0.2810 |
| 共通 | 0.8932 | 0.8932 | 0 |
| **合計** | **2.9892** | **1.6618** | **-1.3275** |

- 差の 4 割はグラフ DB。Neptune Analytics は 16 m-NCU の最小構成でも 0.58 USD/h で、Neo4j の 1 タスク（0.06 USD/h）の 10 倍。次が Kafka で、MSK は 2 ブローカーの m5.large（0.54 USD/h）を Fargate の 3 タスク（0.15 USD/h）に置き換えた分。
- Prometheus だけは OSS 版が高い。AMP は待機の時間課金が無く、VictoriaMetrics のクラスターは 5 タスク（0.12 USD/h）が常に立つ。
- OpenSearch と Spark は、マネージドの最小単位（1 OCU、ジョブごとに 3 vCPU + 6 GB）より小さい Fargate のタスクで動かしているので安くなる。これは性能を落とした分でもあるので、取り込みの量を増やしたときの伸び方（目的 2 の後半）は別に確かめる。
- 共通の部分 0.89 USD/h のうち lab の EC2 が 0.25、endpoint の 14 本が 0.20、Splunk が 0.13 で、置き換えの対象ではないが OSS 版の合計の半分以上を占める。

### 使った分の課金（待機中は ≈0 として合計に入れない）

| 項目 | 単価（東京） | 待機中 |
|---|---|---|
| VPC endpoint の処理量 | 0.01 USD/GB（1 PB まで） | ≈0 |
| NLB の LCU | 0.006 USD/LCU-h | ≈0（接続が無い） |
| CloudWatch Logs の取り込み・保存 | 0.76 USD/GB、0.033 USD/GB-月 | コンテナのログが少し出る（1 時間に数 MB） |
| VPC Flow Logs（CloudWatch Logs へ。vended） | 0.76 USD/GB（10 TB まで） | ≈0（流量が無い） |
| OpenSearch Serverless の保存 | 0.026 USD/GB-月 | ≈0 |
| AMP の取り込み・保存 | 0.90 USD/1,000 万サンプル、0.03 USD/GB-月（10 GB まで無料） | ≈0 |
| EFS の保存・読み書き（OSS 版） | 0.36 USD/GB-月、読み 0.04 USD/GB、書き 0.07 USD/GB | ≈0（Kafka と vmstorage のメタデータだけ） |
| Firehose（Iceberg 宛） | 0.056 USD/GB | ≈0 |
| S3、S3 Tables | 保存量とオブジェクト数、リクエスト | ≈0 |
| Athena | 走査量 | ≈0 |
| Lambda、SQS、SNS | 回数 | ≈0 |
| Bedrock AgentCore Runtime | 0.0895 USD/vCPU-h、0.00945 USD/GB-h（呼ばれている間だけ） | 0 |
| Bedrock AgentCore Gateway | 0.005 USD/1,000 呼び出し、ツールの索引 0.02 USD/100 ツール/月 | ≈0 |
| Bedrock のモデル（Nova 2 Lite） | トークン | 0 |
| ECR の保存 | 保存量（単価は未確認） | ≈0 |
| KMS、Secrets Manager の API 呼び出し | 0.03 USD/1 万回、0.05 USD/1 万回 | ≈0 |

### 前提と、数えなかったもの

- **Fargate の ARM と x86。**
  ARM は x86 より約 20% 安い（vCPU 0.04045 対 0.05056）。Splunk だけが x86（イメージが x86 のみ）。OSS 固有のタスクを全部 x86 にすると +0.19 USD/h。
- **GB-月と月額を時間に直す係数。**
  730 時間/月で割った（EBS、RDS のストレージ、MSK のストレージ、KMS の鍵 1 USD/月、Secrets Manager の 0.40 USD/シークレット/月）。
- **OpenSearch Serverless の最小 OCU。**
  `standby_replicas = "DISABLED"`（`OPENSEARCH_AZ_NUM=1`）の collection は indexing 0.5 + search 0.5 の 1 OCU が最小で、アカウントで最初の collection に掛かる（2024 年 6 月の発表。collection group を使わない classic collection）。`OPENSEARCH_AZ_NUM=2` なら 2 OCU（+0.334 USD/h）。
- **EMR Serverless のメモリ。**
  ワーカーの設定値（2 GB）で数えた。Spark の `memoryOverhead`（10%、最小 384 MiB）を EMR Serverless が請求に含めるなら 3 ジョブで +0.02 USD/h。
- **OSS 版の Spark。**
  Terraform の `desired_count` は 0 で、`ops/oss/up.sh` が 3 つのサービスを 1 にするので、3 タスクで数えた。
- **Knowledge Base。**
  `CREATE_KB=0` なので入れていない。作ると vector の collection の 1 OCU（0.334 USD/h）と endpoint `bedrock-agent-runtime`（0.014 USD/h）が足される。
- **数えなかったもの。**
  コンテナと Flow Logs の CloudWatch Logs（待機中は 1 時間に数 MB）、ECR と S3 と Terraform の state の保存、AZ 間の転送、SSM Parameter Store（標準は無料）、Secrets Manager と KMS の API 呼び出し、EFS の保存量。どれも待機中は合計の 1% に届かない。

### 数量の出どころ（Terraform の既定値）

- web の EC2: `IaC/terraform/aws-managed/base/core/variables.tf` の `instance_type`（`t4g.medium`）、`web.tf` の `volume_size = 16`（gp3）。
- lab の EC2: `IaC/terraform/aws-managed/pipeline/lab/variables.tf` の `instance_type`（`m6i.xlarge`）と `volume_size`（24）。
- endpoint: `ops/up.sh` の endpoint の選び方（`agent` `lab` `stream` `analytics` `graph` `nautobot` `workflow` と `prometheus` `grafana/splunk` で足す分。`SINK_OPENSEARCH` のとき `create_opensearch_endpoint=true`）と `ops/oss/up.sh` の `ENDPOINTS`。`base/core/variables.tf` の `endpoints_az_num`（1）。
- コレクター: `IaC/terraform/aws-managed/pipeline/stream/collectors.tf`（syslog-ng、GoFlow2）、`gnmic.tf`、`telegraf.tf`（`telegraf_task_cpu` 256 / `telegraf_task_memory` 512、`telegraf_az_num` 1、NLB `aws_lb.telegraf_dialout`）。
- Grafana: `IaC/terraform/aws-managed/pipeline/analytics/variables.tf` の `grafana_task_cpu` 512 / `grafana_task_memory` 1024（`create_grafana` は up.sh が `GRAFANA=1` で true にする）。
- Splunk: 同 `splunk_task_cpu` 2048 / `splunk_task_memory` 4096 / `splunk_ephemeral_storage_gib` 40 / `splunk_az_num` 1、`splunk.tf` の `X86_64`。
- Nautobot: `IaC/terraform/aws-managed/pipeline/nautobot/variables.tf` の `task_cpu` 2048 / `task_memory` 4096 / `db_instance_class` `db.t4g.micro` / `nautobot_db_az_num` 1、`database.tf` の `allocated_storage = 20`（gp3）。
- Temporal worker: `IaC/terraform/aws-managed/workflow/variables.tf` の `task_cpu` 1024 / `task_memory` 2048 / `desired_count` 1。
- MSK: `IaC/terraform/aws-managed/pipeline/stream/variables.tf` の `broker_instance_type`（`kafka.m5.large`）と `msk_az_num`（2 = ブローカー数）、`msk.tf` の `volume_size = 10`。SCRAM の鍵とシークレット 3 つは `ops/up.sh` が作る。
- OpenSearch Serverless: `IaC/terraform/aws-managed/pipeline/analytics/sinks.tf` の `standby_replicas`（`opensearch_az_num` 1 で `DISABLED`）、`base/core/endpoints.tf` の AOSS endpoint。
- Neptune Analytics: `IaC/terraform/aws-managed/pipeline/graph/variables.tf` の `provisioned_memory`（16）と `neptune_az_num`（1 → `replica_count` 0）。
- EMR Serverless: `IaC/terraform/aws-managed/pipeline/analytics/outputs.tf` の `spark.driver.cores=1` / `spark.driver.memory=2g` / `spark.executor.cores=1` / `spark.executor.memory=2g` / `spark.executor.instances=2`、`locals.tf` の `spark_jobs`（`iceberg` `splunk` `http` の 3 つ）、`emr.tf` の `ARM64`。
- OSS 版の Kafka: `IaC/terraform/oss/pipeline/stream/kafka.tf` の `kafka_task_cpu` 1024 / `kafka_task_memory` 2048、`kafka_nodes` の 3 ノード。
- OSS 版の OpenSearch: `IaC/terraform/oss/pipeline/analytics/opensearch.tf` の `opensearch_data_task_cpu` 1024 / `opensearch_data_task_memory` 4096（data × 2）、`opensearch_cm_task_cpu` 512 / `opensearch_cm_task_memory` 1024（cluster manager × 1）、`opensearch_ephemeral_storage_gib` 30（data だけ）。
- OSS 版の VictoriaMetrics: `IaC/terraform/oss/pipeline/analytics/victoriametrics.tf` の `victoriametrics_task_cpu` 512 / `victoriametrics_task_memory` 1024、vmstorage × 3 + vminsert + vmselect。
- OSS 版の Neo4j: `IaC/terraform/oss/pipeline/graph/neo4j.tf` の `neo4j_task_cpu` 1024 / `neo4j_task_memory` 4096、`desired_count = 1`。
- OSS 版の Spark: `IaC/terraform/oss/pipeline/analytics/spark.tf` の `spark_task_cpu` 2048 / `spark_task_memory` 4096、`spark_services` の 3 つ。
- OSS 版の EFS: `IaC/terraform/aws-managed/base/core/oss.tf`（`generalPurpose`、`elastic`。マウントターゲットは 3 サブネット）。

### 単価の出典（2026-10-10 取得。東京、USD）

料金ページは表を JavaScript で描くので、ページが読む公開の料金データから取った。左がページ、右がデータ。

| サービス | 料金ページ | 読んだデータ |
|---|---|---|
| Fargate | https://aws.amazon.com/jp/fargate/pricing/ | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/ap-northeast-1/index.json（`APN1-Fargate-*`） |
| EC2 | https://aws.amazon.com/jp/ec2/pricing/on-demand/ | https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/Asia%20Pacific%20(Tokyo)/Linux/index.json |
| EBS | https://aws.amazon.com/jp/ebs/pricing/ | https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ebs.json |
| MSK | https://aws.amazon.com/jp/msk/pricing/ | https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/msk/USD/current/msk.json |
| Neptune Analytics | https://aws.amazon.com/jp/neptune/pricing/ | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonNeptune/current/ap-northeast-1/index.json（`MemoryOptimizedGraphUsage:16-m-NCU`） |
| OpenSearch Serverless | https://aws.amazon.com/jp/opensearch-service/pricing/ 、最小 OCU は https://aws.amazon.com/about-aws/whats-new/2024/06/amazon-opensearch-serverless-entry-cost-half-collection-types/ | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonES/current/ap-northeast-1/index.json（`APN1-IndexingOCU` `APN1-SearchOCU`） |
| EMR Serverless | https://aws.amazon.com/jp/emr/pricing/ | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/ElasticMapReduce/current/ap-northeast-1/index.json（`APN1-EMR-SERVERLESS-ARM-*`） |
| PrivateLink | https://aws.amazon.com/jp/privatelink/pricing/ | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonVPC/current/ap-northeast-1/index.json（`APN1-VpcEndpoint-Hours`） |
| NLB | https://aws.amazon.com/jp/elasticloadbalancing/pricing/ | https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/elb/USD/current/elb.json |
| RDS for PostgreSQL | https://aws.amazon.com/jp/rds/postgresql/pricing/ | https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/rds/USD/current/rds-postgresql-ondemand.json |
| EFS | https://aws.amazon.com/jp/efs/pricing/ | https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/efs/USD/current/efs.json |
| KMS | https://aws.amazon.com/jp/kms/pricing/ | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/awskms/current/ap-northeast-1/index.json |
| Secrets Manager | https://aws.amazon.com/jp/secrets-manager/pricing/ | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSSecretsManager/current/ap-northeast-1/index.json |
| AMP | https://aws.amazon.com/jp/prometheus/pricing/ | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonPrometheus/current/ap-northeast-1/index.json |
| CloudWatch Logs | https://aws.amazon.com/jp/cloudwatch/pricing/ | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/ap-northeast-1/index.json |
| Firehose | https://aws.amazon.com/jp/firehose/pricing/ | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonKinesisFirehose/current/ap-northeast-1/index.json |
| Bedrock AgentCore | https://aws.amazon.com/bedrock/agentcore/pricing/（ページから直接） | |
| S3 Tables、Athena、Lambda、SQS、SNS、ECR | 使った分の課金だけなので単価は取っていない | |

## GDS のライセンス（法的な助言ではない）

- **Neo4j が配る GDS の jar は GPLv3。**
  jar の中の `NOTICE.txt` と `LICENSE.txt` で確かめた。https://neo4j.com/licensing/ も Community Edition を GPL v3 としている。
- **GPLv3 は、商用利用そのものを制限しない。**
  義務が生じるのは、イメージを第三者に配るとき。自社の ECS と private の ECR で動かすだけなら配布に当たらない、という読み。本番で使う前に、法務か契約の担当に確かめる。
- **Community 版の GDS の制限は、並列 4 コアまで、モデル 3 つまで。**
  出典は https://neo4j.com/docs/graph-data-science/current/introduction/ 。

## いまの構成を作るときに気をつけること

- **マネージドに固有の機能は避けずに使う。**
  「マネージドでできること」を調べるのが目的なので、OSS へ移しやすいように機能を削ることはしない。
- **固有の機能に頼った箇所は、どこで何に頼ったかを docs に書く。**
  OSS 版で同じことができるか、何で代えるかを比べる材料になる。
- **費用と手間は、分かった時点で記録する。**
  時間課金は [README](../README.md) の「作るもの」と [deploy.md](deploy.md)、マネージド版と OSS 版の比較はこの文書の「待機の時間課金を料金表から出す」、はまった点は [troubleshooting.md](troubleshooting.md)。

## マネージドに固有の機能に頼った箇所

| 箇所 | 頼ったもの | OSS 版で比べること |
|---|---|---|
| エージェントのツール `centrality`（[app/agentcore/graph.py](../app/agentcore/graph.py) の `centrality()`） | Neptune Analytics のグラフアルゴリズム `neptune.algo.degree` / `closenessCentrality` / `wcc`（openCypher の `CALL`） | GDS の `gds.degree` / `gds.closeness` / `gds.wcc` で計算する（手元では定義どおりの値と一致し、AWS でもエージェントから答えが返った）。Neptune の結果と同じ並びになるかと、機器が増えたときの計算時間は未確認 |
| グラフへの問い合わせ全部（[app/agentcore/graph.py](../app/agentcore/graph.py) の `query()`、[app/temporal/awsio.py](../app/temporal/awsio.py) の `cypher()`） | boto3 の `neptune-graph` の `execute_query`（SigV4、VPC エンドポイント `neptune-graph-data`）。頂点の id は Neptune の `~id` | Neo4j の Python ドライバ（Bolt）とパスワードに差し替えた（`GRAPH_BACKEND=neo4j`）。id はプロパティ `id` と一意制約で持つ。一意制約とその索引はラベルごとなので、id で頂点を引くクエリには Neo4j でだけラベルを付ける（`_lbl`。下） |

- `_lbl`: 無いと全部の頂点を読む。Neptune の `~id` はグラフ全体で一意なので、送る openCypher は変えない。
