# ---------------------------------------------------------------- OSS 版（cycle 005「マネージドを OSS に置き換えた環境を作る」）
# oss/terraform のルート（var.project = nwc-oss。oss.auto.tfvars が入れる）だけが使う。マネージド版（terraform/。既定の nwc-poc）では
# ここの SG・通信・EFS はどれも作らず、security_groups.tf の SG と表がそのまま使われる（local.workload_security_groups = local.security_groups、
# local.active_sg_flows = local.sg_flows）。
# OSS 版は MSK の SG（msk）とその行を外し、ECS で動かす OSS の SG と行を足す。キーと行の書き方は security_groups.tf と同じで、
# ルールも security_groups.tf の for_each が作る（ここにはルールのリソースを置かない）。
# Kafka（oss/terraform/pipeline/stream）と VictoriaMetrics（oss/terraform/pipeline/analytics）のデータは下の EFS に置く。
# OpenSearch はタスクのエフェメラルストレージに置く（OpenSearch の公式がネットワークファイルシステムを避けるよう書いている）。
# Neo4j は NFS 上のデータを支えない（設計 005）ので EFS を使わない（oss/terraform/pipeline/graph が置き場を決める）
locals {
  oss = var.project == "nwc-oss"

  # OSS 版で外すマネージドの SG（その SG が from か to の行も外れる）
  oss_replaced = local.oss ? ["msk"] : []

  # OSS 版で足す SG と、OSS 版で description を替える SG（spark: EMR Serverless でなく ECS のタスク 1 つ）
  oss_security_groups = {
    kafka           = "Kafka brokers and controllers, KRaft on ECS (oss/terraform/pipeline/stream)"
    efs             = "EFS mount targets - Kafka and VictoriaMetrics data (oss/terraform/base/core)"
    opensearch      = "OpenSearch ECS tasks (oss/terraform/pipeline/analytics)"
    victoriametrics = "VictoriaMetrics vminsert, vmselect and vmstorage ECS tasks (oss/terraform/pipeline/analytics)"
    neo4j           = "Neo4j ECS task (oss/terraform/pipeline/graph)"
    spark           = "Spark ECS task, local mode (oss/terraform/pipeline/analytics)"
  }

  # Fargate のタスクは ECR のイメージ・SSM のシークレット・ログをタスクの ENI で取りに行く
  oss_api_clients = ["kafka", "opensearch", "victoriametrics", "neo4j"]

  oss_flows = flatten([
    [for sg in local.oss_api_clients : [
      { from = sg, to = "endpoints", protocol = "tcp", port = 443, why = "AWS APIs through the interface endpoints" },
      { from = sg, to = "s3", protocol = "tcp", port = 443, why = "S3 through the gateway endpoint" },
    ]],
    [
      # Kafka（KRaft の 3 ノードがブローカーとコントローラーを兼ねる。9092 がクライアント、9093 がコントローラー）。
      # 認証なしの PLAINTEXT なので、絞り込みは SG だけ（MSK の 9098 の IAM 認証の代わり）
      { from = "telegraf_dialout", to = "kafka", protocol = "tcp", port = 9092, why = "Kafka - Telegraf dial-out writes" },
      { from = "telegraf_dialin", to = "kafka", protocol = "tcp", port = 9092, why = "Kafka - Telegraf dial-in writes" },
      { from = "spark", to = "kafka", protocol = "tcp", port = 9092, why = "Kafka - Spark reads" },
      { from = "kafka_ui", to = "kafka", protocol = "tcp", port = 9092, why = "Kafka - Kafbat UI" },
      { from = "kafka", to = "kafka", protocol = "tcp", port = 9092, to_port = 9093, why = "Kafka brokers and KRaft controllers talk to each other" },

      # EFS（NFS。TLS はマウントヘルパーが 2049 の上でかける。下のファイルシステムポリシーが TLS でない接続を拒む）
      { from = "kafka", to = "efs", protocol = "tcp", port = 2049, why = "NFS - Kafka log directories" },
      { from = "victoriametrics", to = "efs", protocol = "tcp", port = 2049, why = "NFS - vmstorage data" },

      # OpenSearch（9200 が REST、9300 がノードどうし）。書くのは Spark、読むのは Grafana とエージェントの道具（runtime / lambda）
      { from = "spark", to = "opensearch", protocol = "tcp", port = 9200, why = "OpenSearch REST - Spark writes the logs" },
      { from = "grafana", to = "opensearch", protocol = "tcp", port = 9200, why = "OpenSearch REST - Grafana logs datasource" },
      { from = "runtime", to = "opensearch", protocol = "tcp", port = 9200, why = "OpenSearch REST - agent evidence" },
      { from = "lambda", to = "opensearch", protocol = "tcp", port = 9200, why = "OpenSearch REST - MCP tools evidence" },
      { from = "opensearch", to = "opensearch", protocol = "tcp", port = 9300, why = "OpenSearch transport between the nodes" },

      # VictoriaMetrics のクラスター版（vminsert 8480 が書き込み、vmselect 8481 が読み出し、vmstorage は 8400 で vminsert から、8401 で vmselect から受ける）。
      # 3 つとも同じ SG
      { from = "spark", to = "victoriametrics", protocol = "tcp", port = 8480, why = "vminsert - Spark remote write" },
      { from = "grafana", to = "victoriametrics", protocol = "tcp", port = 8481, why = "vmselect - Grafana Prometheus datasource" },
      { from = "runtime", to = "victoriametrics", protocol = "tcp", port = 8481, why = "vmselect - agent evidence" },
      { from = "lambda", to = "victoriametrics", protocol = "tcp", port = 8481, why = "vmselect - MCP tools evidence" },
      { from = "victoriametrics", to = "victoriametrics", protocol = "tcp", port = 8400, to_port = 8401, why = "vminsert and vmselect to vmstorage" },

      # Neo4j（Bolt 7687）。マネージド版で Neptune Analytics に届くのと同じ顔ぶれ（web / runtime / lambda / workflow / nautobot）。
      # 7474 は Neo4j Browser の HTTP（Web の EC2 からの SSM のポートフォワード）
      { from = "web", to = "neo4j", protocol = "tcp", port = 7687, why = "Neo4j Bolt - chat web and graph seeding" },
      { from = "runtime", to = "neo4j", protocol = "tcp", port = 7687, why = "Neo4j Bolt - agent topology" },
      { from = "lambda", to = "neo4j", protocol = "tcp", port = 7687, why = "Neo4j Bolt - graph status and MCP tools" },
      { from = "workflow", to = "neo4j", protocol = "tcp", port = 7687, why = "Neo4j Bolt - workflow pre-checks" },
      { from = "nautobot", to = "neo4j", protocol = "tcp", port = 7687, why = "Neo4j Bolt - Nautobot sync" },
      { from = "web", to = "neo4j", protocol = "tcp", port = 7474, why = "Neo4j Browser through SSM port forwarding" },
    ],
  ])

  # security_groups.tf が作る SG と通信の表。マネージド版では security_groups / sg_flows と同じ
  workload_security_groups = merge(
    { for k, v in local.security_groups : k => v if !contains(local.oss_replaced, k) },
    { for k, v in local.oss_security_groups : k => v if local.oss },
  )
  active_sg_flows = concat(
    [for f in local.sg_flows : f if !contains(local.oss_replaced, f.from) && !contains(local.oss_replaced, f.to)],
    [for f in local.oss_flows : f if local.oss],
  )
}

# ---------------------------------------------------------------- EFS（OSS 版のデータ）
# 1 つのファイルシステムを Kafka と VictoriaMetrics（vmstorage）で分けて使う（アクセスポイントはそれぞれのルートが作る）。
# マウントターゲットはサブネット a / b / c に 1 つずつ（Kafka の 3 ノードと vmstorage の 3 台が AZ ごとに 1 つ）。スループットは使った分だけの elastic。
# 時間課金は無く、容量（GB 月）と読み書きの量に課金される。ops/down.sh がルートごと消す（自動バックアップは API で作ると無効のまま）
resource "aws_efs_file_system" "oss" {
  count = local.oss ? 1 : 0

  creation_token   = "${local.name_prefix}-data"
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"

  tags = { Name = "${local.name_prefix}-data" }
}

resource "aws_efs_mount_target" "oss" {
  count = local.oss ? length(local.subnet_ids) : 0

  file_system_id  = aws_efs_file_system.oss[0].id
  subnet_id       = local.subnet_ids[count.index]
  security_groups = [local.sg_ids["efs"]]
}

# TLS でない接続を拒み、このアカウントの IAM（タスクロール）がマウントターゲット経由で読み書きするのだけを許す。
# 匿名のマウントは許さないので、タスク定義の EFS ボリュームは transit_encryption と IAM 認可（authorization_config の iam）を有効にし、
# タスクロールに elasticfilesystem:ClientMount / ClientWrite（アクセスポイントで絞る）を付ける
resource "aws_efs_file_system_policy" "oss" {
  count = local.oss ? 1 : 0

  file_system_id = aws_efs_file_system.oss[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyWithoutTls"
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action    = "*"
        Resource  = aws_efs_file_system.oss[0].arn
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "AccountThroughMountTargets"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource  = aws_efs_file_system.oss[0].arn
        Condition = { Bool = { "elasticfilesystem:AccessedViaMountTarget" = "true" } }
      },
    ]
  })
}

output "efs_file_system_id" {
  description = "OSS build only (oss/terraform, cycle 005): EFS for the Kafka and VictoriaMetrics (vmstorage) data, with mount targets in subnets a, b and c (SG efs). Each root makes its own access point. Empty in the managed build."
  value       = try(aws_efs_file_system.oss[0].id, "")
}
