# マネージド版と OSS 版

## 方針

- **いまの構成は、できる限り AWS のマネージドサービスで作る。**自分で立てるのは、マネージドに相当するものが無いか、このアカウントで使えないものだけ（下の表の「自分で立てているもの」）。
- **最終的には、いまの構成とは別に、マネージドの部分を OSS に置き換えた版も作る。**いまの構成を書き換えるのではなく、並べて持つ。
- OSS 版はまだ作っていない。何に置き換えるかも未定（決めたらこのページに書く）。

## OSS 版を作る目的

マネージド版と OSS 版を同じ用途で並べて、違いを確かめる。

1. **AWS マネージドでできて、OSS ではできないことを調べる。**逆（OSS でできて、マネージドではできないこと）も記録する。
2. **費用の違いを検証する。**待機の時間課金と、使った分の課金、機器が増えたときの伸び方。
3. **メンテナンス性の違いを検証する。**版上げ、パッチ、バックアップ、障害時の復旧、監視、権限の管理、作る・消すの手間。

## いまの構成での分け方

| 役割 | いま（AWS マネージド） | OSS 版 |
|---|---|---|
| エージェントの実行と入口 | Bedrock AgentCore（Runtime、Gateway） | 未定 |
| モデルとガードレール | Bedrock（Amazon Nova 2 Lite、ガードレール） | 未定 |
| 手順書の検索 | Bedrock のナレッジベース + OpenSearch Serverless | 未定 |
| Kafka | MSK | 未定 |
| ストリーム処理 | EMR Serverless（Spark） | 未定 |
| 生データの表 | S3 Tables（Iceberg） | 未定 |
| ログの検索 | OpenSearch Serverless | 未定 |
| メトリクス | Amazon Managed Service for Prometheus | 未定 |
| トポロジのグラフ | Neptune Analytics（openCypher。中心性と連結成分は `neptune.algo.*`） | 未定（アルゴリズムは NetworkX などで置き換える） |
| Nautobot の DB | RDS | 未定 |
| コンテナの実行 | ECS Fargate | 未定 |
| アラートの配送 | SNS、SQS、Lambda | 未定 |
| ログ | CloudWatch Logs | 未定 |
| 設定とシークレット、踏み台 | SSM（パラメータ、Session Manager） | 未定 |
| 閉域 | VPC エンドポイントと `aws:SourceVpc` の Deny | 未定 |

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
