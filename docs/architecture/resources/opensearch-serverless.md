# OpenSearch Serverless

← [リソースごとの知見](README.md)

## ひとことで

コレクションを 2 つ使っている。機器の trap と syslog（と NetFlow / sFlow）を検索するためのログ用（TIMESERIES 型）と、Bedrock の Knowledge Base のベクトル検索用（VECTORSEARCH 型）。
どちらも公開せず、土台の OpenSearch Serverless の VPC エンドポイントからだけ届く。ログ用は正本ではなく、見るための写し。

## このプロジェクトでの使い方

| 項目 | ログ用 | Knowledge Base 用 | 定義している場所 |
|---|---|---|---|
| コレクション | `<prefix>-logs`（TIMESERIES） | `<prefix>-kb`（VECTORSEARCH） | `IaC/terraform/aws-managed/pipeline/analytics/sinks.tf`、`IaC/terraform/aws-managed/agent/kb.tf` |
| index と中身 | `snmp-logs`。MSK の `traps` と `logs` と `flows` のドキュメント（trap と syslog と NetFlow / sFlow）。`event_id` と Kafka の位置も項目として持つ | `kb-index`。`app/resources/` の手順書を Titan v2（1024 次元、faiss）でベクトルにしたもの | `app/spark/snmp_sinks.py`、`IaC/terraform/aws-managed/agent/locals.tf` |
| index を作るもの | Spark の最初の `_bulk` | VPC の中の Lambda `<prefix>-kb-index`（apply のときに 1 回呼ぶ） | `sinks.tf` の `aws_opensearchserverless_access_policy.logs`、`kb.tf` |
| スイッチ | `STORES` の `grafana`（Prometheus と一緒に作る） | `CREATE_KB=1` | `deploy.env.example` |
| 暗号 | AWS 所有の鍵 | AWS 所有の鍵 | 各 `security_policy`（encryption） |
| ネットワーク | `AllowFromPublic = false`、`SourceVPCEs` に土台の VPC エンドポイントだけ | 同じ | 各 `security_policy`（network） |
| 控え | `OPENSEARCH_AZ_NUM`（既定 1、1〜2）。2 で `standby_replicas = ENABLED` | 同じキー | 変数 `opensearch_az_num` |
| 費用 | 33 セント/時 × AZ（公表単価。検索の負荷で増える） | +$0.35/h（OCU 0.33 + VPC エンドポイント 0.01 + bedrock-agent-runtime 0.01。2 AZ で OCU が倍） | `ops/up.sh` の費用の目安（524〜584 行） |

VPC エンドポイント（`aws_opensearchserverless_vpc_endpoint.aoss`）は土台（`IaC/terraform/aws-managed/base/core/endpoints.tf`）にあり、KB か `STORES` の `grafana` があるときだけ作る。

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| Spark（EMR Serverless） | Spark → `snmp-logs` | 443、SigV4、`_bulk`。データアクセスポリシーは実行ロールだけ |
| Grafana | Grafana → `snmp-logs` | 443、SigV4（タスクロール）。ダッシュボードとルール `trap` |
| tools の Lambda（`search_logs`） | Lambda → `snmp-logs` | 443、SigV4。データアクセスポリシー `<prefix>-logs-read` |
| Bedrock の Knowledge Base | Bedrock → `kb-index` | サービス側から。ロール `<prefix>-kb`（閉域の Deny の例外） |
| Lambda `<prefix>-kb-index` | Lambda → `kb-index` | 443、SigV4。index を作る権限だけ |

## 知見

- **ログ用のコレクションは、ドキュメントの ID を付けられない。**
  - TIMESERIES 型は追記だけで、ID での上書きができない。Spark が送り直すと、同じドキュメントが 2 つ残る。
  - SEARCH 型にすれば ID を付けられるが、ログの置き場としては TIMESERIES のほうが安く合っているので替えない。
  - 出典: FAQ「OpenSearch と Prometheus でも、重複を防げる？ Grafana の側で落とすべき？」、`IaC/terraform/aws-managed/pipeline/analytics/sinks.tf` のコメント。
- **重複で数字が変わるのは、件数と合計のパネルだけ。**
  - Grafana で一律に落とす設定は無い。必要なパネルだけ、一意の番号の種類数（Unique Count）で数える。
  - 重複が起きるのは送信の失敗でやり直したときだけ。
  - 出典: 同じ FAQ。
- **コレクションは、ポリシー 3 つ（暗号・ネットワーク・データアクセス）を先に作ってから作る。**
  `depends_on` で順番を固定してある。
  出典: `sinks.tf`、`IaC/terraform/aws-managed/agent/kb.tf`。
- **VPC エンドポイントが無いと、コレクションの apply が precondition で止まる。**
  土台を `create_opensearch_endpoint=true` で apply し直す（`ops/up.sh` は `STORES` に `grafana` があるか `CREATE_KB=1` のとき自動で渡す）。
  出典: `sinks.tf` と `kb.tf` の precondition。
- **`standby_replicas` は ENABLED / DISABLED の 2 値だけ。3 AZ にはできない。**
  コレクションには AZ やサブネットの指定が無い。値を変えるとコレクションを作り直す（索引済みのログは消える）。
  出典: `sinks.tf` のコメント（CloudFormation のリファレンス `AWS::OpenSearchServerless::Collection`、https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-opensearchserverless-collection.html 、2026-10-04 確認）。
- **Grafana のデータソースの「Save & test」は、中身の無い ERROR を返すことがある。**
  ダッシュボードでは読める（2026-09-28 に確認）。
  出典: [pipeline.md](../../pipeline.md) の「Grafana と Splunk を開く」、[alert-comparison.md](../../alert-comparison.md) の「AWS で未確認のこと」。
- **device map に無い送り元の trap は、IP がそのまま `tags.sysName` に入る。**
  Grafana のルール `trap` が機器ごとにまとめる鍵が要るため。
  出典: [alert-comparison.md](../../alert-comparison.md) の「Spark の整形の副作用」。
- **KB の index を作り直すときは、コレクションごと作り直す。**
  `-replace=aws_opensearchserverless_collection.kb[0]` のあと、取り込みをやり直す。
  出典: `IaC/terraform/aws-managed/agent/kb.tf` のコメント。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| ログ用と KB 用で OCU が共有されるか | 未確認（2026-09-17。共有されなければ最小の OCU が別に掛かる。`sinks.tf` のコメント） |
| 「2 で別の AZ に控え」「最小 OCU が倍」 | いまの Developer Guide では見つけられていない（未確認。`sinks.tf` のコメント） |
| VPC エンドポイントを 1 サブネットで作れるか | 未確認（FAQ「もう 2 AZ に置いてあるものは、1 AZ にできるか」） |
| Grafana が OpenSearch Serverless を SigV4 でルールの評価に使えるか | 未確認（[alert-comparison.md](../../alert-comparison.md)） |

このあと変わる予定: OSS 版では OpenSearch を ECS に立てる。[マネージドを OSS に置き換えた環境を作る（005）の設計](../../cycles/005-oss-on-ecs/design.md)。

## 関連

- [emr-serverless.md](emr-serverless.md)、[grafana.md](grafana.md)、[agentcore-bedrock.md](agentcore-bedrock.md)、[vpc-perimeter.md](vpc-perimeter.md)
- [data-stores.md](../../data-stores.md): 「1. 置き場は 2 つ（+ 見るための写し 2 つ）」「届け方の保証」
- [pipeline.md](../pipeline.md): パイプラインの構成
