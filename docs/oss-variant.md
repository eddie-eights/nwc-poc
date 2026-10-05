# マネージド版と OSS 版

## 方針

- **いまの構成は、できる限り AWS のマネージドサービスで作る。**自分で立てるのは、マネージドに相当するものが無いか、このアカウントで使えないものだけ（下の表の「自分で立てているもの」）。
- **最終的には、いまの構成とは別に、マネージドの部分を OSS に置き換えた版も作る。**いまの構成を書き換えるのではなく、並べて持つ。
- OSS 版は、手元のコンテナ（`oss/compose/`）での確認と、terraform と ops の実装（`oss/`）まで済んだ。AWS ではまだ 1 回も立てていない（未確認）。置き換え先は下の表。設計は [cycles/005-oss-on-ecs/design.md](cycles/005-oss-on-ecs/design.md)。
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
- データはタスクの一時領域にある（Neo4j は NFS を非対応と明記している）。タスクが入れ替わると消えるので、Nautobot と lab の定義から同期し直す。

自分で立てているもの（マネージド版でも OSS か自前のコンテナ）: Telegraf、Grafana、Temporal、Nautobot、containerlab（lab）、Splunk（OSS ではないが、VPC の中の ECS に自分で立てている）。
Amazon Managed Grafana は、このアカウントに IAM Identity Center が無くて使えないので Grafana OSS にしている（[deploy.md](deploy.md)）。

## OSS 版の実装の置き場と、いまの状態

| 置き場 | 中身 |
|---|---|
| `oss/terraform/` | OSS 版の terraform。変えないルートは `terraform/` へのシンボリックリンク。state は別 |
| `oss/ops/up.sh`、`oss/ops/down.sh` | OSS 版の作る・消す。接頭辞は `<owner>-nwc-oss` で、マネージド版と並べて立てられる |
| `oss/ops/oss-images.sh` | イメージの名前と版（1 か所）。正は `oss/compose/` |
| `oss/compose/` | 手元の確認用の compose と、確認の手順 |
| `spark/Dockerfile`、`neo4j/Dockerfile` | ECS 向けの Spark と Neo4j（GDS 入り）のイメージ |
| `ops/common.sh`、`ops/up-common.sh`、`ops/down-common.sh` | マネージド版と OSS 版の共通の関数 |

- **`oss/ops/up.sh` が作るのは、lab から Neo4j までの 6 つのルート。**
  `base/ecr`、`base/core`、`pipeline/lab`、`pipeline/stream`、`pipeline/analytics`、`pipeline/graph`。
- **まだつないでいないもの。**
  Nautobot、エージェント、workflow、Grafana、Web の部品、Neo4j への同期。
- **Splunk は OSS 版でも変えない。**
  マネージド版と同じ Splunk を立てる。Spark は Splunk の token を、ECS の secrets（SSM の SecureString）から環境変数 `SPLUNK_HEC_TOKEN` で受ける（マネージド版は、ジョブが SSM から読む）。

## 手元のコンテナで確かめたこと（AWS では未確認）

| OSS | 確かめたこと | 残っている未確認 |
|---|---|---|
| Kafka | 固定の voter（`controller.quorum.voters`）で 3 台が組めた。1 台止めても、書いた 1000 件を全部読めて、書けた。動的な voter は、公式イメージが必要な初期化をしないので組めなかった | EFS に置いてよいか（Kafka の文書に NFS / EFS の記述は無い） |
| OpenSearch | 3 台のどれを止めても検索できた | Fargate で起動するか（手元は `vm.max_map_count` が 262144 の環境だった） |
| VictoriaMetrics | vminsert が vmstorage の 3 台につないだあとに書いたデータは、1 台止めても全部読めた。つなぐ前に書いた行は 1 台にしか入らず、その台を止めると、欠けたことを示さずに値が抜ける。だから vmstorage が上がってから vminsert を起動する | ECS の上で、この起動の順が守られるか |
| Neo4j + GDS | Community Edition で GDS が動いた。中心性は定義どおりの値と一致、島の数は 1 | Neptune の結果と同じ並びになるか |
| Spark | EMR なしの Spark 3.5 で、Kafka から OpenSearch、vminsert、Iceberg、Splunk（HEC）に書けた | S3 Tables に書けるか（AWS でしか確かめられない） |

## GDS のライセンス（法的な助言ではない）

- **Neo4j が配る GDS の jar は GPLv3。**
  jar の中の `NOTICE.txt` と `LICENSE.txt` で確かめた。https://neo4j.com/licensing/ も Community Edition を GPL v3 としている。
- **GPLv3 は、商用利用そのものを制限しない。**
  義務が生じるのは、イメージを第三者に配るとき。自社の ECS と private の ECR で動かすだけなら配布に当たらない、という読み。本番で使う前に、法務か契約の担当に確かめる。
- **Community 版の GDS の制限は、並列 4 コアまで、モデル 3 つまで。**
  出典は https://neo4j.com/docs/graph-data-science/current/introduction/ 。

## いまの構成を作るときに気をつけること

- **マネージドに固有の機能は避けずに使う。**「マネージドでできること」を調べるのが目的なので、OSS へ移しやすいように機能を削ることはしない。
- **固有の機能に頼った箇所は、どこで何に頼ったかを docs に書く。**OSS 版で同じことができるか、何で代えるかを比べる材料になる。
- **費用と手間は、分かった時点で記録する。**時間課金は [README](../README.md) の「作るもの」と [deploy.md](deploy.md)、はまった点は [troubleshooting.md](troubleshooting.md)。

## マネージドに固有の機能に頼った箇所

| 箇所 | 頼ったもの | OSS 版で比べること |
|---|---|---|
| エージェントのツール `centrality`（[agent/graph.py](../agent/graph.py) の `centrality()`） | Neptune Analytics のグラフアルゴリズム `neptune.algo.degree` / `closenessCentrality` / `wcc`（openCypher の `CALL`） | GDS の `gds.degree` / `gds.closeness` / `gds.wcc` で計算する（手元では、定義どおりの値と一致した）。Neptune の結果と同じ並びになるかと、機器が増えたときの計算時間は未確認 |
| グラフへの問い合わせ全部（[agent/graph.py](../agent/graph.py) の `query()`、[workflow/awsio.py](../workflow/awsio.py) の `cypher()`） | boto3 の `neptune-graph` の `execute_query`（SigV4、VPC エンドポイント `neptune-graph-data`）。頂点の id は Neptune の `~id` | Neo4j の Python ドライバ（Bolt）とパスワードに差し替えた（`GRAPH_BACKEND=neo4j`）。id はプロパティ `id` と一意制約で持つ |
