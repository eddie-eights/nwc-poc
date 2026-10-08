# 構成

← [README](../../README.md)

`<prefix>` は `deploy.env` の `OWNER` から作る接頭辞 `<owner>-nwc-poc`。

スライドの構成図は 2 本。マネージド版が [architecture-managed.pptx](../architecture-managed.pptx)（10 枚。データの流れ、9 つの Terraform ルート、6 段の処理、収集から格納まで、検知から修復まで、SG、費用、消す順、画面の開き方）、OSS 版が [architecture-oss.pptx](../architecture-oss.pptx)（10 枚。置き換えた 5 つ、1 対 1 の対応、Fargate のタスク、ルート、6 段の処理、SG、`oss/ops/up.sh`、AWS で確かめたこと、未確認）。

構成の説明は terraform のルートに合わせて 4 つに分けてある。

| ファイル | terraform | 中身 |
|---|---|---|
| [core.md](core.md) | `base/core`、`base/ecr` | 土台: VPC、Web の EC2、アラートの SNS トピック、閉域（エンドポイント + Deny）、SG（通信の表） |
| [agent.md](agent.md) | `app/agentcore/`（`AGENT=1`） | チャットの経路: Web → AgentCore Runtime → Nova 2 Lite・ガードレール・KB・ツール |
| [pipeline.md](pipeline.md) | `pipeline/`（`PIPELINE=1`） | lab → Telegraf・syslog-ng・GoFlow2 → MSK → Spark → 格納先、Grafana と Splunk のアラート、Neptune のトポロジ、Nautobot |
| [workflow.md](workflow.md) | `app/temporal/`（`WORKFLOW=1`） | アラート（SNS → SQS）→ Temporal の調査・承認・修復、Gateway（MCP） |

リソースごとの知見（使い方、つながり、はまりどころ、制約）は [resources/README.md](resources/README.md)。

使い方は別のファイル: パイプラインは [pipeline.md](../pipeline.md)、機器から集めるデータは [collection.md](../collection.md)、承認の流れは [workflow.md](../workflow.md)、データの置き場は [data-stores.md](../data-stores.md)、Nautobot は [nautobot.md](../nautobot.md)、マネージドを OSS に置き換えた版は [oss-variant.md](../oss-variant.md)。

## どのファイルがどこで動くか

`app.py` が 2 つあり、動く場所が違う。

| ファイル | 動く場所 | 設定 |
|---|---|---|
| `app/dashboard/app.py`（と `app/dashboard/` の他のファイル） | Web の EC2（`<prefix>-web.service`） | `/etc/<prefix>-web.env`。Runtime の ARN は SSM の `/<prefix>/runtime-arn` を 60 秒キャッシュで読む |
| `app/agentcore/app.py` | AgentCore Runtime のコンテナ | `IaC/terraform/aws-managed/agent/runtime.tf` の環境変数 |

**`app/agentcore/app.py` を EC2 に置かない。**`KeyError: 'MODEL_ID'` か `404` になり、cloud-init のログに `is not app/dashboard/app.py` が出る。EC2 のロールに権限を足して直さない。

| パス | 中身 |
|---|---|
| `app/` | 動くもののソース。下の `app/<名前>/` |
| `app/agentcore/` | Runtime のエージェントとツール。`data/` は静的トポロジ |
| `app/dashboard/` | Gradio の画面 |
| `app/temporal/` | Temporal のワークフローとワーカー |
| `app/spark/` | Spark のジョブ（`snmp_sinks.py`。格納先へ流すだけで、検知はしない） |
| `app/containerlab/` | containerlab の構成、SR Linux の設定（`srlinux/*.cli`）、EC2 の支度（`setup.sh`。lab とデバッグ用の EC2 で共通）、stream の ECS（Telegraf・syslog-ng・GoFlow2）への転送（`lab forward`）、デバッグ用の EC2 の Telegraf（`lab telegraf`） |
| `app/telegraf/` | Telegraf の設定（`telegraf.conf.in`）、入口の `telegraf.sh`（イメージの中では `tg`）、lab の gNMI を共通の形に変える `lab_gnmi.star` / `lab_circuits.star`（stream の ECS のタスクで動く。デバッグ用の EC2 でも docker で `SINK=stdout`） |
| `app/syslog-ng/` | syslog-ng（AxoSyslog）の設定のテンプレート（`syslog-ng.conf.in`）と入口の `syslog-ng.sh`（イメージの中では `sng`）。機器の syslog を Telegraf と同じ `device_log` の形にしてトピック `logs` へ書く（stream の ECS のタスクで動く。2026-10-08 から。NetFlow / sFlow の GoFlow2 は上流のイメージをそのまま使うので、ここには無い） |
| `app/grafana/` | Grafana の `start.sh` と provisioning（データソース（OSS 版は `datasources-oss/`）、ダッシュボード、アラート（`alerting/` の `netops-prometheus.yaml` / `netops-opensearch.yaml` / `netops.yaml`）。analytics の ECS のタスクで動く） |
| `app/splunk/` | Splunk のアプリ `netops_alerts`（保存済みサーチと、SNS へ publish するアラートアクション。analytics の ECS のタスクで動く）、`entrypoint.sh`（役割に合わせてアプリを外す。indexer は止まる前に `splunk offline`）、`peers_check.py`（クラスターの search head が indexer を全部検索できるかの突き合わせ） |
| `app/nautobot/` | Nautobot の Job（`jobs/netops_jobs.py`）と、その中身（`netops/`。対応付け `nb_map.py`、同期 `nb_sync.py`、起動時の `bootstrap.py`）。`PIPELINE=1` ならいつも ECS で動く |
| `app/graph/` | アラート（SNS）を受けて Neptune（Neptune Analytics）の `status` を書き、通知の履歴を Firehose へ送る Lambda（`status_handler.py`） |
| `app/neo4j/` | OSS 版の Neo4j（+ GDS）の `entrypoint.sh`（OSS 版の graph の ECS のタスクで動く） |
| `app/resources/` | ナレッジベースに入れる手順書 |
| `docker/images/<名前>/Dockerfile` | イメージの `Dockerfile`（agentcore / temporal / grafana / splunk / nautobot / telegraf / syslog-ng / spark / neo4j）。ビルドのコンテキストは `app/<名前>/` で、`docker buildx build -f docker/images/<名前>/Dockerfile app/<名前>/` の形で使う。Splunk は公式イメージ + 検知のアプリ、Nautobot は公式イメージ + boto3 |
| `docker/compose/` | 手元の docker compose（WSL2 の中だけで lab から Grafana / Splunk まで一周させる。AWS は使わない。[README](../../docker/compose/README.md)） |
| `IaC/terraform/aws-managed/` | AWS にリソースを作るのはここだけ（下のツリー） |
| `IaC/terraform/oss/` | OSS 版の同じ 9 つのルート（下の段落） |
| `IaC/cloudformation/` | デバッグ用の EC2 のスタック（`lab-debug.yaml`。下の段落） |
| `tools/` | Gateway（MCP）の tools Lambda |
| `ops/` | `up.sh` / `down.sh` / `check.sh` / `lab-debug.sh` / `sync-graph.sh` など |
| `oss/` | OSS 版の操作（`oss/ops/up.sh` / `down.sh`）とイメージの版（`oss/ops/oss-images.sh`）（[oss-variant.md](../oss-variant.md)） |
| `tests/` | 模擬テスト（AWS を呼ばない） |

```
IaC/terraform/aws-managed/
├── base/
│   ├── ecr/         ECR リポジトリ
│   └── core/        VPC / VPC エンドポイント / 閉域の Deny（perimeter.tf）/ SG（ワークロードごと。通信の表は security_groups.tf）/ フローログ / バケット / アラートの SNS トピック（alerts.tf）/ ロール / Web の EC2
├── agent/         AGENT=1     Runtime / ガードレール / KB
├── pipeline/      PIPELINE=1
│   ├── lab/         containerlab の EC2（stream を作るときは Telegraf・syslog-ng・GoFlow2 への転送も）
│   ├── stream/      MSK（IAM + SASL/SCRAM）/ Telegraf・syslog-ng・GoFlow2（ECS Fargate + 内部 NLB）/ Kafbat UI の接続先（SSM）と Web の EC2 のロールへの権限（画面は Web の EC2 の Docker）
│   ├── analytics/   EMR Serverless / S3 Tables / OpenSearch / Prometheus / Grafana と Splunk（ECS Fargate）/ アラートの通知の履歴の Firehose
│   ├── graph/       Neptune Analytics のグラフ / status の Lambda（SNS の購読）
│   └── nautobot/    Nautobot（ECS Fargate）と PostgreSQL（RDS）
└── workflow/      WORKFLOW=1  Temporal on ECS / Gateway（MCP）/ SQS（SNS の購読と、承認・却下の decisions）
```

ルートは 9 つで、`ops/up.sh` の `ROOTS` と `ops/check.sh` ではこの順に並ぶ: `base/ecr` → `base/core` → `agent` → `pipeline/lab` → `pipeline/stream` → `pipeline/analytics` → `pipeline/graph` → `pipeline/nautobot` → `workflow`。apply の順は少し違い、graph は手順 3-2 で裏で始めて手順 7-3 で待ち、nautobot は analytics の前（手順 7-3c）。Nautobot は `PIPELINE=1` ならいつも作る（stream と graph を両方外したときだけ作らない）。

OSS 版（`oss/ops/up.sh`）は `IaC/terraform/oss/` に同じ 9 つのルートを持つ（多くのファイルは `IaC/terraform/aws-managed/` へのシンボリックリンクで、違いは各ルートの `oss.auto.tfvars` と OSS 版だけのファイル）。接頭辞は `<owner>-nwc-oss`、state も `IaC/terraform/oss/<ルート>/terraform.tfstate` で、マネージド版とは別（[oss-variant.md](../oss-variant.md)）。

デバッグ用の EC2（lab + Telegraf を 1 台）だけは terraform ではなく CloudFormation の `IaC/cloudformation/lab-debug.yaml`（スタック `<prefix>-lab-debug`）。作るのも消すのも `ops/lab-debug.sh up` / `down` だけで、`ops/up.sh` / `ops/down.sh` は触らない（2026-10-04 から）。土台（base/core）は使わず、自分の VPC（既定 `10.20.0.0/24`。どこともつながないので base/core と重なってよい。IGW / NAT は無い）、インターフェース型エンドポイント 4 本（ssm / ssmmessages / ecr.api / ecr.dkr）と S3 の gateway、バケット `<prefix>-lab-debug-<アカウント>`、ECR のリポジトリ 4 つ（`<prefix>-debug-lab-srlinux` / `-lab-multitool` / `-lab-trex` / `-telegraf`。スタックと一緒に消える）を持つ。

1 ディレクトリ = 1 state。state は各ルートの `terraform.tfstate`（ローカル）。変数を変えたいときは `terraform.tfvars.example` を `terraform.tfvars` に写す。

## 名前とタグ

- リソース名は `<prefix>-<何>`。Runtime だけはハイフンが使えないので `<owner>_nwc_poc_agent`。
- タグを付けられるものには全部 `Project=<prefix>` と `owner=<owner>` が付く（各ルートの `providers.tf` の `default_tags`）。
- 作ったものの一覧:

```bash
aws resourcegroupstaggingapi get-resources --region ap-northeast-1 \
  --tag-filters Key=Project,Values=<prefix> \
  --query 'ResourceTagMappingList[].ResourceARN' --output table
```

タグが付かないもの: ENI、Runtime のロググループ（`ops/up.sh` の手順 9 で付ける）、OpenSearch Serverless のポリシーとインデックス、KB のデータソース、ガードレールの版。

## ログ

| 何 | どこ |
|---|---|
| エージェントの実行ログ | CloudWatch Logs `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT`（出力 `runtime_log_group_name`、保持 7 日） |
| Web の失敗 | Web の EC2 の `journalctl -u <prefix>-web` |
| ガードレールで止めたか | Runtime のログの `stop=guardrail_intervened` |
| Telegraf | CloudWatch Logs `/ecs/<prefix>-telegraf`（stream の出力 `telegraf_log_group_name`） |
| syslog-ng / GoFlow2 | CloudWatch Logs `/ecs/<prefix>-syslog-ng` / `/ecs/<prefix>-goflow2`（stream の出力 `syslog_ng_log_group_name` / `goflow2_log_group_name`） |
| Grafana / ECS の Splunk | CloudWatch Logs `/ecs/<prefix>-grafana` / `/ecs/<prefix>-splunk` |
| Spark（EMR Serverless） | CloudWatch Logs `/aws/emr-serverless/<prefix>` |
| Kafbat UI / MSK | Web の EC2 の `journalctl -u <prefix>-kafka-ui` / CloudWatch Logs `/<prefix>/msk` |
| status の Lambda | CloudWatch Logs `/aws/lambda/<prefix>-graph-status` |
| Nautobot | CloudWatch Logs `/ecs/<prefix>-nautobot` |
| Temporal とワーカー | CloudWatch Logs `/ecs/<prefix>-workflow` |
| SG で拒んだ通信 | VPC フローログ `/<prefix>/vpc-flow-logs`（[core.md](core.md) の「SG」） |
| デバッグ用の EC2 の Telegraf | EC2 の中の `sudo lab telegraf logs`（docker logs。CloudWatch には出さない） |
| 誰がいつ入ったか | CloudTrail の `StartSession` |

ポートフォワーディングの中身は Session Manager のセッションログに残らない。会話の中身もどこにも保存しない。
