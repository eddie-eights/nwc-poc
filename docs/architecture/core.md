# 構成: 土台（core）

← [構成](README.md)

`IaC/terraform/aws-managed/base/core` と `IaC/terraform/aws-managed/base/ecr`。どの機能を作るときも必ず作る。

- VPC（プライベートサブネットだけ。NAT Gateway も IGW も無い）、VPC エンドポイント、閉域の Deny、SG、フローログ、バケット、ロール、ECR。
- Web の EC2: Gradio の画面（チャット / トポロジ / 承認）。
  - パブリック IP も受信ルールも無く、SSM Agent が内側から ssmmessages へつなぐ。PC からは SSM のポートフォワーディングで `localhost:8080` に開く。
  - Web は EC2 のまま。Grafana・Splunk・Temporal UI・Nautobot の踏み台も兼ねる（ECS にするとタスクの IP が変わり、踏み台にしにくい）。
  - stream を作る回は Kafbat UI も Docker で同居する（`127.0.0.1:8082`。Kafbat UI を Web の EC2 に同居させる（010））。
- アラートの SNS トピック `<prefix>-alerts`（`alerts.tf`）: Grafana と Splunk が publish し、graph の Lambda（[pipeline.md](pipeline.md)）と workflow の SQS（[workflow.md](workflow.md)）へ配る。

## 閉域

AWS の API へは全部 VPC エンドポイントから行き、この VPC を通らない呼び出しを拒む。VPC にインターネットへの経路は無い（NAT Gateway も IGW も作らない。Splunk も analytics の ECS に立て、AWS の外へは送らない）。

| 層 | 何をする | どこ |
|---|---|---|
| 経路 | インターフェース型エンドポイント（private DNS）。`ops/up.sh` の `endpoints_for` が機能から選ぶ（表の下）。S3 は gateway 型（無料）、OpenSearch Serverless は専用の 1 本（`CREATE_KB` か `STORES` の grafana のとき） | `IaC/terraform/aws-managed/base/core/endpoints.tf`、`ops/up.sh` の `endpoints_for` |
| エンドポイントポリシー | このアカウントのプリンシパルだけ（盗んだ他のアカウントの鍵で VPC から持ち出す経路を塞ぐ）。S3 の gateway は付けない（dnf と ECR のレイヤーが止まる） | 同上 |
| IAM の Deny | ワークロードのロール全部に `<prefix>-network-perimeter` を付け、`aws:SourceVpc` がこの VPC でなければ拒む（ロールと呼び出しは表の下） | `IaC/terraform/aws-managed/base/core/perimeter.tf`、各ルートの attachment、`IaC/cloudformation/lab-debug.yaml` |
| リソースポリシーの Deny | バケット、S3 Tables のテーブルバケット、SNS のトピック（`sns:Publish`）、SQS（本体と DLQ）、AgentCore の Runtime と Gateway。同じ条件で、どのプリンシパルからでも VPC の外なら拒む | `bucket.tf`、`alerts.tf` ほか（表の下） |

- 経路: `endpoints_for` が機能ごとに選ぶエンドポイント。
  - 土台: ssm / ssmmessages
  - AGENT: bedrock-runtime / bedrock-agentcore / ecr.api / ecr.dkr / logs（KB で bedrock-agent-runtime）
  - lab: ecr.api / ecr.dkr
  - stream: ecr.api / ecr.dkr / logs / secretsmanager（Telegraf・gnmic・syslog-ng・GoFlow2 の ECS）
    - secretsmanager は syslog-ng と GoFlow2 と gnmic のタスクが起動時に MSK の SCRAM の資格情報を読むため。
    - Web の EC2 の Kafbat UI も土台の ssm と、stream が足す ecr.api / ecr.dkr を使う。
  - analytics: s3tables / logs（Prometheus で aps-workspaces、Grafana か ECS の Splunk で ecr.api / ecr.dkr / sns）
  - graph: neptune-graph-data（analytics があるときは kinesis-firehose も。アラートの通知の履歴）
  - nautobot: ecr.api / ecr.dkr / logs / ecs（Job が gnmic のサービスを作り直す）
  - WORKFLOW: sqs / s3tables / ecr.api / ecr.dkr / logs / bedrock-agentcore / bedrock-agentcore.gateway（analytics があるとき athena）
- IAM の Deny:
  - 付けるロールは Web、Runtime、lab、EMR、ECS（Temporal / Telegraf / gnmic / syslog-ng / GoFlow2 / Grafana / Splunk / Nautobot）、tools Lambda、graph-status の Lambda。
  - 拒むのは s3 / s3tables / sqs / sns / ssm / bedrock / aps / athena / firehose / AgentCore の呼び出し。
  - デバッグ用の EC2（CloudFormation の `<prefix>-lab-debug`）のロールは、同じ Action と条件の Deny を自分のスタックの VPC に向けてインラインで持つ。
- リソースポリシーの Deny のファイル:
  - `bucket.tf`、`alerts.tf`
  - `IaC/terraform/aws-managed/pipeline/analytics/tables.tf`
  - `IaC/terraform/aws-managed/workflow/events.tf`、`IaC/terraform/aws-managed/workflow/gateway.tf`
  - `IaC/terraform/aws-managed/agent/runtime.tf`

- **拒まないもの**:
  - apply した人（`terraform` を打つ PC は VPC の外なので。PoC の割り切り）
  - AWS のサービス自身（`aws:PrincipalIsAWSService`）とサービスが代わりに呼ぶもの（`aws:ViaAWSService`。SNS → SQS / Lambda、Bedrock → S3 など）
  - KB のロール `<prefix>-kb`（取り込みは Bedrock のサービス側で動く）
- S3 Tables の Iceberg REST は、S3 Tables が裏で呼ぶ API に元の VPC が付かないので `aws:CalledViaLast = s3tables.amazonaws.com` を外してある。
- MSK の IAM 認証にはこの条件キーが無いので Deny に入れない（VPC の中にしか口が無い）。
- Neptune Analytics（`neptune-graph`）も Deny に入れていない（リクエストに `aws:SourceVpc` が付くか未確認。グラフは `public_connectivity = false` で、公開の口が無い）。
- Prometheus のワークスペースはリソースポリシーの Deny を確かめていないので IAM の側だけ。
- apply する人が替わったら、その人が `ops/up.sh` を打ち直す（外すプリンシパルが入れ替わる）。
  前の人の設定のままバケットに入れないときは [troubleshooting.md](../troubleshooting.md) の「閉域」。
- 本番では、apply も VPC の中（CI のランナーなど）から打ち、外す人を無くす。
  AWS の外（Splunk Cloud など）へ送る必要が出て NAT Gateway を足すなら、出る先を Network Firewall のドメインの許可リストで絞る（この PoC には無い）。

## SG

ワークロードごとに SG を 1 つと、VPC エンドポイント用の `endpoints`。

- SG もルールも `IaC/terraform/aws-managed/base/core/security_groups.tf` にまとめ、ルールは通信の表（`local.sg_flows`）から作る。表に無い通信は受信も送信も通らない。
- ほかのルートは土台の output `security_group_ids` から自分の SG を読んで付けるだけで、ルールは作らない。
  SG とルールに時間課金は無いので、機能を作らないときもそろえて作る。

| 送る側 | 受ける側 | ポート | 何のため |
|---|---|---|---|
| web / lab / telegraf_dialout / gnmic / syslog_ng / goflow2 / spark / grafana / splunk / nautobot / lambda / workflow / runtime | endpoints / S3（プレフィックスリスト） | 443/tcp | AWS の API（インターフェース型）と S3（gateway 型。ECR のレイヤーと dnf も） |
| web | grafana / splunk / workflow / nautobot | 3000 / 8000 / 8233 / 8080（tcp） | SSM のポートフォワーディング（Grafana / Splunk Web / Temporal UI / Nautobot。Kafbat UI は Web の EC2 の中なので SG を通らない） |
| nautobot | nautobot_db | 5432/tcp | Nautobot の PostgreSQL（RDS） |
| workflow | nautobot_db | 5432/tcp | Temporal の履歴（同じ RDS の DB `temporal` / `temporal_visibility`。cycle 036） |
| workflow | workflow | 6933〜6939 / 7233〜7239（tcp） | Temporal のサーバーの 5 つのサービスどうしの membership と gRPC（タスクの IP を広告し合う。同じタスクの中は SG を通らないので、効くのはデプロイ中に新旧 2 タスクが同じ DB で 1 つのクラスターになるときのタスクどうし。自分宛てだけ） |
| telegraf_dialout / spark / web | msk | 9098/tcp | Kafka（IAM 認証。web は Kafbat UI） |
| syslog_ng / goflow2 / gnmic | msk | 9096/tcp | Kafka（SASL/SCRAM。syslog-ng と GoFlow2 は MSK の IAM 認証を喋れない。gnmic も同じ形） |
| msk | msk | 9092〜9098/tcp | ブローカー同士 |
| spark | spark | 全部の tcp | 1 つのジョブのドライバとエグゼキュータ |
| spark | splunk | 8088/tcp | HEC（`STORES` の `splunk`） |
| splunk | splunk | 8089 / 9887 / 9997（tcp） | Splunk のクラスター（`SPLUNK_AZ_NUM` が 2 か 3）の管理と検索・indexer 間の複製・内部ログの転送。1 台のときも作る（相手がいない） |
| telegraf_dialout_nlb | telegraf_dialout | 1162/udp、8080/tcp | trap の転送と、NLB のヘルスチェック（Telegraf の `outputs.health`） |
| telegraf_dialout_nlb | syslog_ng | 5140/udp、5140/tcp | syslog の転送と、NLB のヘルスチェック（TCP） |
| telegraf_dialout_nlb | goflow2 | 2055/udp、6343/udp、8081/tcp | NetFlow・sFlow の転送と、NLB のヘルスチェック（`/__health`） |
| lab の管理ネットワーク（203.0.113.0/24） | telegraf_dialout_nlb | 162/udp、5140/udp、2055/udp、6343/udp | 機器の trap・syslog・NetFlow・sFlow（lab の EC2 が DNAT するので送り元は機器の IP のまま） |
| lab | telegraf_dialout_nlb | 2055/udp、6343/udp、5140/udp | lab の EC2 のホストが自分の IP から試しに送る NetFlow・sFlow・syslog（`ops/netflow_send.py`、`logger`）。NLB は trap（162/udp）を lab の SG からは受けない（lab の SG の 162 は送る側だけ。NLB の 162 が受けるのは送り元が機器の管理 IP のままの DNAT だけ） |
| gnmic | lab の管理ネットワーク | 57400/tcp | gnmic からの gNMI の購読（VPC のルートで lab の EC2 へ） |

OSS 版（`IaC/terraform/oss/`）では `IaC/terraform/aws-managed/base/core/oss.tf` が SG と表を差し替える（msk とその行を外し、kafka / efs / opensearch / victoriametrics / neo4j の SG と行を足す）。

Neptune Analytics に SG は無い。VPC の中の口を持たず、インターフェース型エンドポイント `neptune-graph-data`（443、SigV4。表の 1 行目）で openCypher を送る。

- lab の EC2 が転送する流れは、SG が見る IP が lab の EC2 ではなく機器の管理 IP になる。
  そこで、相手の ENI の IP が見える側だけを SG の参照で書き（lab の送信は telegraf_dialout_nlb へ、lab の受信は gnmic から）、反対側は管理ネットワークの CIDR で書く。
  例外は EC2 自身が試しに送る NetFlow / sFlow / syslog（2055 / 6343 / 5140 の udp）。両側に書く（telegraf_dialout_nlb は lab の SG からも受ける。NLB は trap（162/udp）を lab の SG からは受けない。lab の SG の 162 は送る側だけ）。
- 開けていないもの:
  - Temporal の gRPC 7233〜7239 と membership 6933〜6939 をタスクの外へ（開けるのは workflow の SG の自分宛てだけ。UI とワーカーは同じタスクの `127.0.0.1` / `localhost`）
  - Splunk の管理 API 8089 の外から（splunk の SG どうしだけ開ける）
- インターネットからの受信は、SG の前に経路が無い。
- DNS（VPC の +2）・IMDS・ECS のタスクメタデータ・Time Sync は SG の対象外なので、表に無くても届く。
- `endpoints` は表の 443 だけを受け、外へは出さない。
- VPC の全 ENI の通信は VPC フローログ（`IaC/terraform/aws-managed/base/core/flow_logs.tf`）でロググループ `/<prefix>/vpc-flow-logs` に残る（保存 7 日。集約 60 秒）。
  表の漏れで拒んだ通信は `action = REJECT` で出る（問い合わせは [troubleshooting.md](../troubleshooting.md)）。
- SG の説明（description）を変えると作り直しになり、ENI が付いていると消えない。変えるときは先に `ops/down.sh` を打つ。
- 2026-09-29 より前の state（全部で共有する `internal` 1 つ）が残っていると、`ops/up.sh` は手順 0 の後で止まる。
  ほかのルートは土台の `security_group_ids` を読むが、無ければ apply の前に止まり（`terraform_remote_state` の postcondition）、destroy は古い state のままでも通る（SG の ID は `try` で読む）。

## 経緯

- 2026-10-04: Neptune を Neptune Database から Neptune Analytics に置き換えた。
- 2026-10-08（012）: 本番の Cisco の MDT の dial-out の行（`MDT_SOURCE_CIDRS` の CIDR → telegraf_dialout_nlb の 57000/tcp）を受け口ごと外した（戻し方は [collection.md](../collection.md)）。
- 2026-10-09（013）: 取りにいく側の Telegraf（`telegraf_dialin`）を gnmic に置き換え、SNMP のポーリング（161/udp）の行は外した。
- 2026-10-10（037）: lab の EC2 から NLB（telegraf_dialout_nlb）の 5140/udp の受信を足した（EC2 自身が `logger` で試せるように。機器の syslog は前から CIDR で受ける）。
