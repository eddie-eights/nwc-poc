# ---------------------------------------------------------------- security groups
# ワークロードごとに SG を 1 つ（aws_security_group.workload）と、VPC エンドポイント用の endpoints。ルールは下の通信の表（local.sg_flows）から作り、
# 表に無い通信は受信も送信も通らない（aws_security_group は作るときに既定の全許可の送信ルールを消す）。
# SG もルールもここ（土台）にまとめる。相手の SG を参照するルールを 1 か所で書くためで、ほかのルートは outputs.security_group_ids から
# 自分の SG を読んで付けるだけ（ルールは作らない）。SG とルールに時間課金は無いので、機能を作らないときもそろえて作る。
# 相手の絞り込みは SG と IAM の両方で行う（MSK / Neptune Analytics は IAM 認証、S3 / ECR / Bedrock はロールのポリシーと perimeter.tf の VPC の外を拒む Deny）。
# DNS（VPC の +2）・IMDS・ECS のタスクメタデータ・Time Sync は SG の対象外なので表に無い。インターネットからの受信は SG 以前に経路が無い（vpc.tf）。
# EMR Serverless は 0.0.0.0/0 の受信ルールがある SG を拒むが、表に CIDR の受信は lab の管理ネットワーク（NLB の SG だけ）しか無い
# （cycle 012 で MDT の送り元の var.mdt_source_cidrs を外した）。
# 開けていないもの: Temporal の gRPC 7233（ワーカーは同じタスクの localhost。IaC/terraform/aws-managed/workflow の ecs.tf）、Splunk の管理 API 8089
# （開けるのはクラスターの splunk どうしだけ。外から使わない）。
# 2026-09-26〜09-29 は全部で internal 1 つ（VPC の中は何でも受け、送信は自由）だった。その前（7c42b0f）はルートごとに SG とルールを持っていた。
# SG の description は変えると作り直しになる（付いている ENI があると消えない）ので、変えるときは down してから。
# OSS 版（IaC/terraform/oss。var.project = nwc-oss。cycle 005）は oss.tf が SG と表を差し替える（msk を外し、kafka / efs / opensearch /
# victoriametrics / neo4j を足す）。作るのは local.workload_security_groups と local.active_sg_flows で、マネージド版では下の 2 つと同じ
locals {
  # SG を付けるワークロード。名前は <接頭辞>-<キーの _ を - に>
  security_groups = {
    web = "Chat web EC2 - SSM port forwarding to Grafana, Splunk and the Temporal UI starts here"
    lab = "Lab EC2 - containerlab, forwards the lab mgmt network"
    # Telegraf は受ける側（dialout。NLB の後ろ）のタスク（2026-10-04 にキーを dialout / dialin にそろえた。キーを変えると SG は作り直しになるので、
    # ops/up.sh は古いキーの state のまま stream があると止める）。
    # dialout と NLB の description は cycle 012 で syslog と MDT が抜けても変えない（変えると作り直し）。
    # syslog_ng と goflow2（機器の syslog と NetFlow / sFlow を受けるタスク。dialout と同じ NLB の後ろ）は cycle 012 で足した。
    # gnmic（機器の gNMI を取りにいくタスク）は cycle 013 で取りにいく側の telegraf_dialin を置き換えた（SNMP のポーリングもやめた）
    telegraf_dialout     = "Telegraf dial-out ECS task - traps, syslog and MDT behind the NLB (IaC/terraform/aws-managed/pipeline/stream)"
    gnmic                = "gnmic ECS task - gNMI subscriptions to Kafka (IaC/terraform/aws-managed/pipeline/stream)"
    telegraf_dialout_nlb = "Internal NLB in front of the Telegraf dial-out task (IaC/terraform/aws-managed/pipeline/stream)"
    syslog_ng            = "syslog-ng ECS task - device syslog behind the NLB to Kafka (IaC/terraform/aws-managed/pipeline/stream)"
    goflow2              = "GoFlow2 ECS task - NetFlow and sFlow behind the NLB to Kafka (IaC/terraform/aws-managed/pipeline/stream)"
    msk                  = "MSK brokers (IaC/terraform/aws-managed/pipeline/stream)"
    spark                = "EMR Serverless workers (IaC/terraform/aws-managed/pipeline/analytics)"
    grafana              = "Grafana ECS task (IaC/terraform/aws-managed/pipeline/analytics)"
    splunk               = "Splunk ECS task (IaC/terraform/aws-managed/pipeline/analytics)"
    nautobot             = "Nautobot ECS task - web, celery worker and redis (IaC/terraform/aws-managed/pipeline/nautobot)"
    nautobot_db          = "Nautobot PostgreSQL on RDS (IaC/terraform/aws-managed/pipeline/nautobot)"
    lambda               = "Lambda in the VPC - graph status, MCP tools, knowledge base index"
    workflow             = "Temporal dev server and worker ECS task (IaC/terraform/aws-managed/workflow)"
    runtime              = "AgentCore Runtime ENIs (IaC/terraform/aws-managed/agent)"
  }
  sg_keys = concat(keys(local.workload_security_groups), ["endpoints"])
  sg_ids  = merge({ for k, sg in aws_security_group.workload : k => sg.id }, { endpoints = aws_security_group.endpoints.id })

  # lab の管理ネットワーク。IaC/terraform/aws-managed/pipeline/lab の local.mgmt_cidr と app/containerlab/ の機器の設定と同じ値（tests/test_analytics.py が見る）
  lab_mgmt_cidr = "203.0.113.0/24"

  # AWS の API（インターフェース型と OpenSearch Serverless の VPC エンドポイント）と S3（ゲートウェイエンドポイント。S3 Tables のデータ・ECR のレイヤー・
  # AL2023 の dnf もここ）へ出るワークロード。Fargate のタスクは ECR のイメージ・SSM と Secrets Manager のシークレット・ログもタスクの ENI で取りに行く。
  # Web の EC2 は Kafbat UI（cycle 010）のイメージもここ（ECR と S3）から引く
  aws_api_clients = ["web", "lab", "telegraf_dialout", "gnmic", "syslog_ng", "goflow2", "spark", "grafana", "splunk", "nautobot", "lambda", "workflow", "runtime"]

  # 通信の表。1 行が 1 つの流れで、from が送り、to が受ける（応答は SG の接続追跡で通るので書かない）。from / to は上の SG のキーか endpoints、
  # または SG でない相手の s3（S3 のマネージドプレフィックスリスト）と lab_mgmt（local.lab_mgmt_cidr）。
  # 送り元が CIDR の行は from を "cidr:<CIDR>"（ルールの鍵を分けるため）にして cidr に CIDR を書く（受信だけ）。
  # 両端が SG なら from の送信ルールと to の受信ルールの 2 本、片方だけが SG ならその側の 1 本になる。
  # only = "egress" / "ingress" は片側だけを書く行。lab の EC2 が管理ネットワークとのあいだを転送する流れは、SG が見る IP が
  # lab の EC2 のものでなく機器の管理 IP なので、SG の参照が効く側（相手の ENI の IP が見える側）だけを SG で書き、反対側は lab_mgmt の CIDR で書く
  sg_flows = flatten([
    [for sg in local.aws_api_clients : [
      { from = sg, to = "endpoints", protocol = "tcp", port = 443, why = "AWS APIs through the interface endpoints" },
      { from = sg, to = "s3", protocol = "tcp", port = 443, why = "S3 through the gateway endpoint" },
    ]],
    [
      # Neptune Analytics（IaC/terraform/aws-managed/pipeline/graph）は VPC の中に ENI を持たない。web / runtime / lambda / workflow / nautobot は上の endpoints の 443
      # （neptune-graph-data のエンドポイント）で届くので、ここに行は無い（2026-10-04 までは Neptune Database の SG と 8182 の 5 行があった）

      # SSM のポートフォワーディング（利用者の PC → ssmmessages → Web の EC2 の SSM Agent → タスク）
      { from = "web", to = "grafana", protocol = "tcp", port = 3000, why = "Grafana UI through SSM port forwarding" },
      { from = "web", to = "splunk", protocol = "tcp", port = 8000, why = "Splunk Web through SSM port forwarding" },
      { from = "web", to = "workflow", protocol = "tcp", port = 8233, why = "Temporal UI through SSM port forwarding" },
      { from = "web", to = "nautobot", protocol = "tcp", port = 8080, why = "Nautobot UI through SSM port forwarding" },

      # Nautobot（IaC/terraform/aws-managed/pipeline/nautobot）→ RDS の PostgreSQL
      { from = "nautobot", to = "nautobot_db", protocol = "tcp", port = 5432, why = "PostgreSQL - Nautobot database" },

      # Kafka（IAM 認証の 9098）
      { from = "telegraf_dialout", to = "msk", protocol = "tcp", port = 9098, why = "Kafka IAM - Telegraf dial-out writes" },
      { from = "spark", to = "msk", protocol = "tcp", port = 9098, why = "Kafka IAM - Spark reads" },
      { from = "web", to = "msk", protocol = "tcp", port = 9098, why = "Kafka IAM - Kafbat UI on the web EC2" },
      # SASL/SCRAM の 9096（cycle 012。syslog-ng と GoFlow2 は MSK の IAM 認証を喋れない。gnmic も同じ形にした。cycle 013）
      { from = "syslog_ng", to = "msk", protocol = "tcp", port = 9096, why = "Kafka SCRAM - syslog-ng writes" },
      { from = "goflow2", to = "msk", protocol = "tcp", port = 9096, why = "Kafka SCRAM - GoFlow2 writes" },
      { from = "gnmic", to = "msk", protocol = "tcp", port = 9096, why = "Kafka SCRAM - gnmic writes" },
      { from = "msk", to = "msk", protocol = "tcp", port = 9092, to_port = 9098, why = "Brokers talk to each other" },

      # Spark
      { from = "spark", to = "spark", protocol = "tcp", port = 0, to_port = 65535, why = "Driver and executors of one job" },
      { from = "spark", to = "splunk", protocol = "tcp", port = 8088, why = "Splunk HTTP Event Collector - splunk sink" },

      # Splunk のクラスター（IaC/terraform/aws-managed/pipeline/analytics の splunk.tf。splunk_az_num が 2 か 3）: manager・indexer・search head が同じ SG。
      # ここは splunk_az_num を知らないのでいつも作る（1 台のときは相手がいない）
      { from = "splunk", to = "splunk", protocol = "tcp", port = 8089, why = "Splunk management and search - cluster manager, indexers and search head" },
      { from = "splunk", to = "splunk", protocol = "tcp", port = 9887, why = "Splunk replication between the indexers" },
      { from = "splunk", to = "splunk", protocol = "tcp", port = 9997, why = "Splunk forwarding - search head and manager send their internal logs to the indexers" },

      # NLB → タスク（IaC/terraform/aws-managed/pipeline/stream の telegraf.tf の collector_listeners）。NLB が送り元の IP を残しても、タスクの受信は NLB の SG の参照で通る。
      # NLB の送信ルールは転送とヘルスチェックの両方に効く。cycle 012 で syslog を syslog-ng へ移し、NetFlow / sFlow を足し、MDT の 57000/tcp を外した
      { from = "telegraf_dialout_nlb", to = "telegraf_dialout", protocol = "udp", port = 1162, why = "SNMP traps - NLB 162 to the task 1162" },
      { from = "telegraf_dialout_nlb", to = "telegraf_dialout", protocol = "tcp", port = 8080, why = "NLB health check - Telegraf outputs.health" },
      { from = "telegraf_dialout_nlb", to = "syslog_ng", protocol = "udp", port = 5140, why = "syslog - NLB 5140 to the syslog-ng task 5140" },
      { from = "telegraf_dialout_nlb", to = "syslog_ng", protocol = "tcp", port = 5140, why = "NLB health check - syslog-ng tcp 5140" },
      { from = "telegraf_dialout_nlb", to = "goflow2", protocol = "udp", port = 2055, why = "NetFlow - NLB 2055 to the GoFlow2 task 2055" },
      { from = "telegraf_dialout_nlb", to = "goflow2", protocol = "udp", port = 6343, why = "sFlow - NLB 6343 to the GoFlow2 task 6343" },
      { from = "telegraf_dialout_nlb", to = "goflow2", protocol = "tcp", port = 8081, why = "NLB health check - GoFlow2 /__health" },

      # trap・syslog・NetFlow・sFlow: 機器 → lab の EC2（lab.sh forward の DNAT）→ NLB。送り元は機器の管理 IP のままなので、NLB は管理ネットワークの CIDR から受け、
      # lab の EC2 は NLB の SG へ送る
      { from = "lab_mgmt", to = "telegraf_dialout_nlb", protocol = "udp", port = 162, why = "SNMP traps from the switches - DNAT on the lab EC2" },
      { from = "lab_mgmt", to = "telegraf_dialout_nlb", protocol = "udp", port = 5140, why = "syslog from the switches - DNAT on the lab EC2" },
      { from = "lab_mgmt", to = "telegraf_dialout_nlb", protocol = "udp", port = 2055, why = "NetFlow from the switches - DNAT on the lab EC2" },
      { from = "lab_mgmt", to = "telegraf_dialout_nlb", protocol = "udp", port = 6343, why = "sFlow from the switches - DNAT on the lab EC2" },
      { from = "lab", to = "telegraf_dialout_nlb", protocol = "udp", port = 162, only = "egress", why = "SNMP traps forwarded for the switches" },
      { from = "lab", to = "telegraf_dialout_nlb", protocol = "udp", port = 5140, only = "egress", why = "syslog forwarded for the switches" },
      # NetFlow / sFlow は両側（lab の SR Linux は NetFlow を送れないので、試すときは lab の EC2 のホストが自分の IP から ops/netflow_send.py で送る）
      { from = "lab", to = "telegraf_dialout_nlb", protocol = "udp", port = 2055, why = "NetFlow - forwarded for the switches, or ops/netflow_send.py on the lab EC2" },
      { from = "lab", to = "telegraf_dialout_nlb", protocol = "udp", port = 6343, why = "sFlow - forwarded for the switches, or a test sender on the lab EC2" },

      # 取りにいく: gnmic のタスク → 機器の gNMI（VPC のルートで lab の EC2 へ。IaC/terraform/aws-managed/pipeline/lab の telegraf.tf）。SNMP のポーリング（udp 161）は
      # cycle 013 でやめた。タスクは管理ネットワークの CIDR へ送り、lab の EC2 はタスクの SG から受ける。受ける側のタスク（telegraf_dialout など）は機器へ出ない
      { from = "gnmic", to = "lab_mgmt", protocol = "tcp", port = 57400, why = "gNMI subscription to the switches" },
      { from = "gnmic", to = "lab", protocol = "tcp", port = 57400, only = "ingress", why = "gNMI forwarded to the switches" },
    ],
  ])

  sg_rules = { for f in local.active_sg_flows : "${f.from}-${f.to}-${f.protocol}-${f.port}" => {
    from     = f.from
    to       = f.to
    protocol = f.protocol
    port     = f.port
    to_port  = try(f.to_port, f.port)
    only     = try(f.only, "")
    cidr     = try(f.cidr, null)
    why      = f.why
  } }
}

data "aws_ec2_managed_prefix_list" "s3" {
  name = "com.amazonaws.${var.region}.s3"
}

resource "aws_security_group" "workload" {
  for_each = local.workload_security_groups

  name        = "${local.name_prefix}-${replace(each.key, "_", "-")}"
  description = each.value
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name_prefix}-${replace(each.key, "_", "-")}" }
}

resource "aws_vpc_security_group_egress_rule" "flow" {
  for_each = { for k, r in local.sg_rules : k => r if contains(local.sg_keys, r.from) && r.only != "ingress" }

  security_group_id            = local.sg_ids[each.value.from]
  description                  = each.value.why
  ip_protocol                  = each.value.protocol
  from_port                    = each.value.port
  to_port                      = each.value.to_port
  referenced_security_group_id = contains(local.sg_keys, each.value.to) ? local.sg_ids[each.value.to] : null
  prefix_list_id               = each.value.to == "s3" ? data.aws_ec2_managed_prefix_list.s3.id : null
  cidr_ipv4                    = each.value.to == "lab_mgmt" ? local.lab_mgmt_cidr : null
}

resource "aws_vpc_security_group_ingress_rule" "flow" {
  for_each = { for k, r in local.sg_rules : k => r if contains(local.sg_keys, r.to) && r.only != "egress" }

  security_group_id            = local.sg_ids[each.value.to]
  description                  = each.value.why
  ip_protocol                  = each.value.protocol
  from_port                    = each.value.port
  to_port                      = each.value.to_port
  referenced_security_group_id = contains(local.sg_keys, each.value.from) ? local.sg_ids[each.value.from] : null
  cidr_ipv4                    = each.value.from == "lab_mgmt" ? local.lab_mgmt_cidr : each.value.cidr
}

# インターフェース型と OpenSearch Serverless の VPC エンドポイント（endpoints.tf）に付ける。受信は表の 443 だけ
resource "aws_security_group" "endpoints" {
  name        = "${local.name_prefix}-endpoints"
  description = "VPC endpoints - HTTPS from the workload SGs, no outbound"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name_prefix}-endpoints" }
}

resource "aws_vpc_security_group_egress_rule" "endpoints_none" {
  security_group_id = aws_security_group.endpoints.id
  description       = "No outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "127.0.0.1/32"
}
