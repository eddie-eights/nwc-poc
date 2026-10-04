# リソースごとの知見

← [構成](../README.md)

AWS のリソース 1 つにつき 1 ファイル。FAQ、設計の記録、Terraform と `ops/up.sh` のコメントに散らばっていた事実を、リソースごとに集め直したもの。新しい事実は足していない。書いてあることはコード（`terraform/`、`ops/`、各プログラム）と突き合わせてある（2026-10-05）。

- どのファイルも見出しは同じ: ひとことで / このプロジェクトでの使い方 / つながり / 知見 / 制約と未確認 / 関連。
- `<prefix>` は `deploy.env` の `OWNER` から作る接頭辞 `<owner>-nwc-poc`。リージョンは東京（ap-northeast-1）。
- 費用は `ops/up.sh` の先頭のコメントの数字（セント/時、東京）。リポジトリに書いてある数字だけを載せた。
- 「未確認」は、AWS の上で動かして確かめていないこと。

| リソース | terraform のルート | ひとことの役割 | ファイル |
|---|---|---|---|
| VPC と閉域（VPC エンドポイント + Deny、SG） | `base/core` | インターネットへの経路が無い VPC。AWS の API へはエンドポイントだけで行き、VPC の外からの呼び出しを拒む | [vpc-perimeter.md](vpc-perimeter.md) |
| Web の EC2 | `base/core` | Gradio の画面（チャット / トポロジ / 承認）と、Grafana などを開くための踏み台 | [web-ec2.md](web-ec2.md) |
| MSK | `pipeline/stream` | 機器のデータを 5 つのトピックで 24 時間ためる Kafka | [msk.md](msk.md) |
| Telegraf（ECS） | `pipeline/stream` | 機器から trap・syslog・gNMI・SNMP を集めて MSK に書く | [telegraf.md](telegraf.md) |
| EMR Serverless（Spark） | `pipeline/analytics` | MSK を読み、格納先ごとのジョブで 60 秒ごとに書く | [emr-serverless.md](emr-serverless.md) |
| S3 Tables（Iceberg）と Athena | `pipeline/analytics` | 生データ、修復案の証跡、アラートの通知の履歴の置き場と、それを読む SQL | [s3-tables-athena.md](s3-tables-athena.md) |
| Firehose | `pipeline/analytics` | アラートの通知を 1 件 1 行で S3 Tables に追記する | [firehose.md](firehose.md) |
| OpenSearch Serverless | `pipeline/analytics`、`agent` | trap と syslog の検索（logs）と、手順書のベクトル検索（KB） | [opensearch-serverless.md](opensearch-serverless.md) |
| Amazon Managed Prometheus | `pipeline/analytics` | メトリクスの時系列の置き場 | [prometheus.md](prometheus.md) |
| Grafana（ECS） | `pipeline/analytics` | Prometheus と OpenSearch を見る画面と、アラートルール 4 本 | [grafana.md](grafana.md) |
| Splunk（ECS） | `pipeline/analytics` | 全トピックを入れる検索基盤と、保存済みサーチ 4 本のアラート | [splunk.md](splunk.md) |
| Neptune Analytics | `pipeline/graph` | トポロジのグラフと、機器・回線のいまの `status` | [neptune-analytics.md](neptune-analytics.md) |
| SNS・SQS・Lambda | `base/core`、`pipeline/graph`、`workflow` | アラートを配る（トピック → graph-status の Lambda と、ワーカーの SQS） | [sns-sqs-lambda.md](sns-sqs-lambda.md) |
| AgentCore と Bedrock | `agent`、`workflow` | エージェントの実行環境（Runtime）、ツールの入口（Gateway）、モデル、ガードレール、ナレッジベース | [agentcore-bedrock.md](agentcore-bedrock.md) |
| Temporal（ECS） | `workflow` | 調査 → 修復案 → 承認 → 適用 → 確認 を進めるワークフロー | [temporal.md](temporal.md) |
| Nautobot（ECS + RDS） | `pipeline/nautobot` | 機器・IF・ケーブルの台帳（正本）。変更を Neptune と Telegraf に映す | [nautobot.md](nautobot.md) |
| lab の EC2（containerlab） | `pipeline/lab` | SR Linux 6 台の検証用ネットワーク | [lab-ec2.md](lab-ec2.md) |
| SSM Parameter Store | 各ルートと `ops/up.sh` | ルートをまたいで渡す値（String）と、シークレット（SecureString） | [ssm-parameter-store.md](ssm-parameter-store.md) |
| ECR | `base/ecr` | コンテナイメージ 10 個の置き場 | [ecr.md](ecr.md) |

## まだ main に入っていないもの

ここには書いていない。入ったら該当するファイルを書き直す。

| サイクル | 変わるファイル | 設計 |
|---|---|---|
| 修復案を S3 Tables にまとめる（003） | S3 Tables、Neptune Analytics、Temporal、SNS・SQS・Lambda | [design.md](../../cycles/003-proposals-in-s3tables/design.md) |
| Splunk をクラスターにする（004） | Splunk | [design.md](../../cycles/004-splunk-indexer-cluster/design.md) |
| マネージドを OSS に置き換えた環境を作る（005）と Kafbat UI | MSK、OpenSearch Serverless、Prometheus、Neptune Analytics | [design.md](../../cycles/005-oss-on-ecs/design.md) |
