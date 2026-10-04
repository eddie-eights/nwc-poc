# マネージド版と OSS 版

## 方針

- **いまの構成は、できる限り AWS のマネージドサービスで作る。**自分で立てるのは、マネージドに相当するものが無いか、このアカウントで使えないものだけ（下の表の「自分で立てているもの」）。
- **最終的には、いまの構成とは別に、マネージドの部分を OSS に置き換えた版も作る。**いまの構成を書き換えるのではなく、並べて持つ。
- OSS 版はまだ作っていない。置き換え先は 2026-10-04 に決めた（下の表）。設計は [cycles/005-oss-on-ecs/design.md](cycles/005-oss-on-ecs/design.md)。
- **OSS にするのは、下の表で置き換え先を書いた 5 つだけ。**ほかはマネージドのまま使う。
- **5 つ以外の道具は、商用で使えるライセンスなら OSS でなくてよい。**

## OSS 版を作る目的

マネージド版と OSS 版を同じ用途で並べて、違いを確かめる。

1. **AWS マネージドでできて、OSS ではできないことを調べる。**逆（OSS でできて、マネージドではできないこと）も記録する。
2. **費用の違いを検証する。**待機の時間課金と、使った分の課金、機器が増えたときの伸び方。
3. **メンテナンス性の違いを検証する。**版上げ、パッチ、バックアップ、障害時の復旧、監視、権限の管理、作る・消すの手間。

## いまの構成での分け方

| 役割 | いま（AWS マネージド） | OSS 版 |
|---|---|---|
| エージェントの実行と入口 | Bedrock AgentCore（Runtime、Gateway） | 変えない（マネージドのまま） |
| モデルとガードレール | Bedrock（Amazon Nova 2 Lite、ガードレール） | 変えない（マネージドのまま） |
| 手順書の検索 | Bedrock のナレッジベース + OpenSearch Serverless | 変えない（マネージドのまま） |
| Kafka | MSK | Apache Kafka（KRaft）を ECS に 3 台。データは EFS |
| Kafka の監視の画面 | MSK のコンソールと CloudWatch | Kafbat UI（Apache 2.0）を ECS に 1 台。ブローカー、トピック、メッセージ、コンシューマーの遅れを見る。トピックの追加とメッセージの送信も画面からできる |
| ストリーム処理 | EMR Serverless（Spark） | Apache Spark を ECS に（格納先ごとに 1 タスク） |
| 生データの表 | S3 Tables（Iceberg） | 変えない（マネージドのまま） |
| ログの検索 | OpenSearch Serverless | OpenSearch を ECS に 3 台（データ 2 台でレプリカ 1、まとめ役だけの小さい 1 台）。データは EFS。候補として VictoriaLogs を残す |
| メトリクス | Amazon Managed Service for Prometheus | VictoriaMetrics のクラスターを ECS に（vminsert 1、vmselect 1、vmstorage 3、複製数 2）。データは EFS |
| トポロジのグラフ | Neptune Analytics（openCypher。中心性と連結成分は `neptune.algo.*`） | Neo4j Community Edition を ECS に 1 台。アルゴリズムは GDS（動かなければ NetworkX） |
| Nautobot の DB | RDS | 変えない（マネージドのまま） |
| コンテナの実行 | ECS Fargate | 変えない（マネージドのまま） |
| アラートの配送 | SNS、SQS、Lambda | 変えない（マネージドのまま） |
| ログ | CloudWatch Logs | 変えない（マネージドのまま） |
| 設定とシークレット、踏み台 | SSM（パラメータ、Session Manager） | 変えない（マネージドのまま） |
| 閉域 | VPC エンドポイントと `aws:SourceVpc` の Deny | 変えない（マネージドのまま） |

**Neo4j のクラスターについての注意**

- クラスターは Neo4j Enterprise Edition だけの機能で、Community Edition では組めない。OSS 版の中で、Neo4j だけは 1 台で動く。
- 止まっているあいだは、トポロジの表示、status の更新、エージェントのトポロジの検索ができない。
- データはタスクの一時領域にある（Neo4j は NFS を非対応と明記している）。タスクが入れ替わると消えるので、Nautobot と lab の定義から同期し直す。

自分で立てているもの（マネージド版でも OSS か自前のコンテナ）: Telegraf、Grafana、Temporal、Nautobot、containerlab（lab）、Splunk（OSS ではないが、VPC の中の ECS に自分で立てている）。
Amazon Managed Grafana は、このアカウントに IAM Identity Center が無くて使えないので Grafana OSS にしている（[deploy.md](deploy.md)）。

## いまの構成を作るときに気をつけること

- **マネージドに固有の機能は避けずに使う。**「マネージドでできること」を調べるのが目的なので、OSS へ移しやすいように機能を削ることはしない。
- **固有の機能に頼った箇所は、どこで何に頼ったかを docs に書く。**OSS 版で同じことができるか、何で代えるかを比べる材料になる。
- **費用と手間は、分かった時点で記録する。**時間課金は [README](../README.md) の「作るもの」と [deploy.md](deploy.md)、はまった点は [troubleshooting.md](troubleshooting.md)。

## マネージドに固有の機能に頼った箇所

| 箇所 | 頼ったもの | OSS 版で比べること |
|---|---|---|
| エージェントのツール `centrality`（[agent/graph.py](../agent/graph.py) の `centrality()`） | Neptune Analytics のグラフアルゴリズム `neptune.algo.degree` / `closenessCentrality` / `wcc`（openCypher の `CALL`） | グラフ DB の側で計算できるか、NetworkX などへ読み出して計算するか。機器が増えたときの計算時間 |
| グラフへの問い合わせ全部（[agent/graph.py](../agent/graph.py) の `query()`、[workflow/awsio.py](../workflow/awsio.py) の `cypher()`） | boto3 の `neptune-graph` の `execute_query`（SigV4、VPC エンドポイント `neptune-graph-data`）。頂点の id は Neptune の `~id` | openCypher は OSS のグラフ DB でも使えるものがある。クライアント（Bolt など）と、id の持ち方・認証を差し替える |
