# VPC と閉域（VPC エンドポイント + Deny、SG）

← [リソースごとの知見](README.md)

## ひとことで

インターネットへの経路を持たない VPC。AWS の API へは VPC エンドポイントだけで行き、この VPC を通らない呼び出しは IAM とリソースポリシーの Deny で拒む。
VPC の中の通信は、SG の通信の表に書いたものだけが通る。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| VPC | `10.0.0.0/16`。IGW も NAT Gateway も無い | `terraform/base/core/vpc.tf`、変数 `vpc_cidr` |
| サブネット | プライベートが 3 つ（a / b / c。AZ ID は apne1-az1 / az4 / az2）。1 AZ のものは a に置く | `terraform/base/core/vpc.tf` |
| S3 のエンドポイント | gateway 型。エンドポイントポリシーは付けない | `terraform/base/core/endpoints.tf` の `aws_vpc_endpoint.s3` |
| インターフェース型エンドポイント | 作る機能から `ops/up.sh` が選んで渡す。private DNS あり。ポリシーは「このアカウントのプリンシパルだけ」 | `ops/up.sh` の `endpoints_for`、`terraform/base/core/endpoints.tf` の `aws_vpc_endpoint.interface` |
| OpenSearch Serverless の VPC エンドポイント | KB か `STORES` の `grafana` があるとき、または前に作ったコレクションが agent か analytics の state に残っているときだけ作る（`create_opensearch_endpoint`。`ops/up.sh` の `NEED_AOSS`） | `terraform/base/core/endpoints.tf` の `aws_opensearchserverless_vpc_endpoint.aoss` |
| エンドポイントを置く AZ の数 | `ENDPOINTS_AZ_NUM`（既定 1、1〜3） | `ops/up.sh`、`deploy.env.example` |
| IAM の Deny | 管理ポリシー `<prefix>-network-perimeter`。ワークロードのロール全部に付ける | `terraform/base/core/perimeter.tf` と各ルートの attachment |
| リソースポリシーの Deny | バケット、S3 Tables のテーブルバケット、SNS、SQS（本体と DLQ）、AgentCore の Runtime と Gateway | `bucket.tf`、`alerts.tf`、`pipeline/analytics/tables.tf`、`workflow/events.tf`、`workflow/gateway.tf`、`agent/runtime.tf` |
| Deny から外すプリンシパル | apply した人、KB のロール `<prefix>-kb`、Firehose のロール `<prefix>-alert-firehose` | `terraform/base/core/perimeter.tf` の `perimeter_exempt_principals` |
| SG | ワークロードごとに 1 つと `endpoints`。ルールは通信の表から作る | `terraform/base/core/security_groups.tf` の `local.sg_flows` |
| フローログ | VPC の全 ENI。ロググループ `/<prefix>/vpc-flow-logs`、保存 7 日、集約 60 秒 | `terraform/base/core/flow_logs.tf` |
| スイッチ | `NETWORK_PERIMETER=0` で Deny を一時的に外す（切り分け用） | `ops/up.sh`、[setup.md](../../setup.md) の「閉域を一時的に外すとき」 |
| 費用 | インターフェース型エンドポイント 1 つ 1.4 セント/時 × `ENDPOINTS_AZ_NUM`（データは別に $0.01/GB）。OpenSearch Serverless の VPC エンドポイントも 1.4 セント/時 × AZ（公表単価）。SG、ルール、gateway 型は時間課金なし | `ops/up.sh` の費用の目安（526〜583 行） |

機能ごとのエンドポイント（`ops/up.sh` の `endpoints_for`）:

| 機能 | エンドポイント |
|---|---|
| 土台 | ssm、ssmmessages（`endpoints_for` の外で足す） |
| agent | bedrock-runtime、bedrock-agentcore、ecr.api、ecr.dkr、logs（`CREATE_KB=1` で bedrock-agent-runtime。`endpoints_for` の外で足す） |
| lab | ecr.api、ecr.dkr |
| stream | ecr.api、ecr.dkr、logs |
| analytics | s3tables、logs（`STORES` の `grafana` で aps-workspaces、Grafana か Splunk で ecr.api / ecr.dkr / sns。この 3 つは `endpoints_for` の外で足す） |
| graph | neptune-graph-data（analytics があるとき kinesis-firehose） |
| nautobot | ecr.api、ecr.dkr、logs、ecs |
| workflow | sqs、s3tables、ecr.api、ecr.dkr、logs、bedrock-agentcore、bedrock-agentcore.gateway（analytics があるとき athena） |

## つながり

SG の通信の表（`local.sg_flows`）。表に無い通信は受信も送信も通らない。

| 送る側 | 受ける側 | ポート | 何のため |
|---|---|---|---|
| AWS の API を呼ぶワークロードの SG（`local.aws_api_clients`。web、lab、telegraf_dialout、telegraf_dialin、kafka_ui、spark、grafana、splunk、nautobot、lambda、workflow、runtime） | endpoints、S3（プレフィックスリスト） | 443/tcp | AWS の API と S3 |
| web | grafana / splunk / workflow / nautobot / kafka_ui | 3000 / 8000 / 8233 / 8080 / 8080（tcp） | SSM のポートフォワーディングで画面を開く |
| nautobot | nautobot_db | 5432/tcp | PostgreSQL |
| telegraf_dialout / telegraf_dialin / spark / kafka_ui | msk | 9098/tcp | Kafka（IAM 認証） |
| msk | msk | 9092〜9098/tcp | ブローカー同士 |
| spark | spark | 全部の tcp | 1 つのジョブの driver と executor |
| spark | splunk | 8088/tcp | HEC |
| telegraf_dialout_nlb | telegraf_dialout | 1162/udp、5140/udp、57000/tcp、8080/tcp | trap・syslog・MDT の転送と NLB のヘルスチェック |
| lab の管理ネットワーク（203.0.113.0/24）、lab | telegraf_dialout_nlb | 162/udp、5140/udp | 機器の trap と syslog（lab の EC2 が DNAT する） |
| `MDT_SOURCE_CIDRS` の CIDR（既定は空） | telegraf_dialout_nlb | 57000/tcp | 本番の Cisco の MDT の dial-out |
| telegraf_dialin | lab の管理ネットワーク | 161/udp、57400/tcp | SNMP のポーリングと gNMI |

## 知見

- **閉域は「経路」「エンドポイントポリシー」「IAM の Deny」「リソースポリシーの Deny」の 4 層で作る。**
  経路だけだと、盗まれた鍵で VPC の外から呼べる。Deny の条件は `aws:SourceVpc` がこの VPC でないこと。
  出典: [core.md](../core.md) の「閉域」、`terraform/base/core/perimeter.tf`。
- **S3 の gateway 型エンドポイントにはポリシーを付けない。**
  付けると dnf と ECR のレイヤー（どちらも AWS が持つバケット）が止まる。
  出典: [core.md](../core.md) の「閉域」の表。
- **エンドポイントが無いサービスを呼ぶと、エラーではなく接続のタイムアウトになる。**
  インターネットへの経路が無いので、どこにも出られない。足すのは `ops/up.sh` の `endpoints_for` と、`terraform/base/core` の `interface_endpoints` の validation の両方。
  出典: [troubleshooting.md](../../troubleshooting.md) の「閉域（`explicit deny`）」。
- **機能を外しても、その機能の state が残っているあいだはエンドポイントを残す。**
  先に消すと、残っているワークロードが接続のタイムアウトになる。
  出典: `ops/up.sh` の `endpoints_for` のコメント。
- **Deny から外すものは 3 種類ある。**
  apply した人（`terraform` を打つ PC は VPC の外。PoC の割り切り）、AWS のサービス自身とサービスが代わりに呼ぶもの（`aws:PrincipalIsAWSService`、`aws:ViaAWSService`）、サービス側で動くロール（KB の `<prefix>-kb`、Firehose の `<prefix>-alert-firehose`）。
  出典: [core.md](../core.md) の「閉域」、`terraform/pipeline/analytics/history.tf` の先頭のコメント。
- **S3 Tables の Iceberg REST は `aws:CalledViaLast = s3tables.amazonaws.com` を Deny から外してある。**
  S3 Tables が裏で呼ぶ API には、元の VPC が付かない。
  出典: [core.md](../core.md) の「閉域」。
- **MSK と Neptune Analytics は Deny に入れていない。**
  MSK の IAM 認証には `aws:SourceVpc` の条件キーが無い（口は VPC の中にしか無い）。Neptune Analytics はリクエストに `aws:SourceVpc` が付くか未確認（グラフは `public_connectivity = false`）。
  出典: [core.md](../core.md) の「閉域」。
- **apply する人が替わると、前の人の Deny でバケットに入れなくなる。**
  外すプリンシパルが前の人のままだから。直し方は [troubleshooting.md](../../troubleshooting.md) の「閉域」の「apply する人が替わり…」の行。
- **SG はワークロードごとに分け、ルールは土台の 1 か所で作る（2026-09-29）。**
  それまでは全部で共有する `internal` が 1 つだった。ほかのルートは土台の output `security_group_ids` から自分の SG を読んで付けるだけ。SG とルールに時間課金は無いので、機能を作らないときもそろえて作る。
  出典: [core.md](../core.md) の「SG」、[troubleshooting.md](../../troubleshooting.md) の「2026-09-29 より前の SG（internal）が残っている」の行。
- **SG の説明（description）を変えると作り直しになる。**
  ENI が付いていると消えないので、変える前に `ops/down.sh` を打つ。
  出典: [core.md](../core.md) の「SG」。
- **lab が転送する通信は、片側を CIDR で書く。**
  SG が見る送り元は lab の EC2 ではなく機器の管理 IP になるため、相手の ENI が見える側だけ SG の参照で書く。
  出典: [core.md](../core.md) の「SG」。
- **拒んだ通信はフローログで探す。**
  Logs Insights で `filter action = "REJECT"` を打つ（1〜2 分遅れて出る）。
  出典: [troubleshooting.md](../../troubleshooting.md) の「閉域」の「VPC の中の相手への接続がタイムアウトする」の行。
- **2026-09-28 に、NAT Gateway と AWS の外の Splunk をコードごと消してこの形にした。**
  出典: `terraform/base/core/perimeter.tf` のコメント、[core.md](../core.md) の「閉域」。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| Neptune Analytics のリクエストに `aws:SourceVpc` が付くか | 未確認（Deny に入れていない） |
| Prometheus のワークスペースのリソースポリシーの Deny | 確かめていない。IAM の側だけ |
| OpenSearch Serverless の VPC エンドポイントを 1 サブネットで作れるか | 未確認（FAQ「もう 2 AZ に置いてあるものは、1 AZ にできるか」） |
| エンドポイントを 1 AZ にしたとき、その AZ が止まった場合の動き | 未確認 |
| apply した人を Deny から外している | PoC の割り切り。本番は apply も VPC の中から打ち、外す人を無くす |
| AZ 間の転送料 | 費用の数字に入れていない |

## 関連

- [core.md](../core.md): 閉域と SG の表
- [setup.md](../../setup.md): 閉域を一時的に外すとき
- [troubleshooting.md](../../troubleshooting.md): 「閉域（`explicit deny`）」
- [ssm-parameter-store.md](ssm-parameter-store.md)、[ecr.md](ecr.md): エンドポイント経由で読むもの
