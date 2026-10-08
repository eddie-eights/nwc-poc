# ---------------------------------------------------------------- MSK
# マネージド版の Kafka。MSK（var.msk_az_num 台、IAM 認証）。あるあいだ 1 時間に約 0.61 USD かかる - 作った日のうちに消す。
# OSS 版（cycle 005。IaC/terraform/oss/pipeline/stream）にこのファイルは無く、代わりに kafka.tf（ECS の Kafka）がある。どちらも下の kafka_* の locals を
# 同じ名前で定義し、2 つの版で共通のファイル（locals.tf・telegraf.tf・kafka_ui.tf・access.tf・outputs.tf。OSS 版はシンボリックリンク）は MSK のリソースでなくそれを使う

locals {
  # SG は locals.tf のほかの SG と同じく try（古い state の destroy でも評価できる）
  msk_sg_id = try(data.terraform_remote_state.main.outputs.security_group_ids["msk"], "")
  # 1 AZ に 1 台（ブローカーの数 = サブネットの数 = var.msk_az_num）
  broker_subnet_ids = slice(local.subnet_ids, 0, var.msk_az_num)

  # arn:aws:kafka:<region>:<account>:cluster/<name>/<uuid> → topic/<name>/<uuid>/*
  topic_arns = "${replace(aws_msk_cluster.stream.arn, ":cluster/", ":topic/")}/*"
  group_arns = "${replace(aws_msk_cluster.stream.arn, ":cluster/", ":group/")}/*"

  # ---- Kafka の差し替え口（OSS 版は kafka.tf が同じ名前で定義する）
  # Telegraf の KAFKA_BROKERS（IAM の口。9098）と output bootstrap_brokers
  kafka_bootstrap_brokers = aws_msk_cluster.stream.bootstrap_brokers_sasl_iam
  # Kafbat UI が var.kafka_ui_security_protocol で引く。この MSK は平文を受け付けないので PLAINTEXT は空になり、kafka_ui.tf の precondition で止まる
  kafka_bootstrap_by_protocol = {
    SASL_SSL  = aws_msk_cluster.stream.bootstrap_brokers_sasl_iam
    PLAINTEXT = aws_msk_cluster.stream.bootstrap_brokers
  }
  # Kafbat UI の画面に出るクラスタの名前
  kafka_cluster_name = aws_msk_cluster.stream.cluster_name
  # Telegraf の 2 つのタスクに足す環境変数。MSK は telegraf.sh の既定（KAFKA_AUTH=iam）のままなので何も足さない
  kafka_client_environment = []
  # Telegraf のタスクロールの Kafka の権限（ECS Exec の分は telegraf.tf）
  telegraf_kafka_statements = [
    {
      Sid    = "Kafka"
      Effect = "Allow"
      Action = [
        "kafka-cluster:Connect",
        "kafka-cluster:DescribeCluster",
        "kafka-cluster:WriteData",
        "kafka-cluster:WriteDataIdempotently",
        "kafka-cluster:DescribeTopic",
        "kafka-cluster:CreateTopic",
      ]
      Resource = [aws_msk_cluster.stream.arn, local.topic_arns]
    },
  ]
  # Kafbat UI のタスクロールの権限。Kafbat UI が使う Kafka の操作だけ。ブローカーの設定の変更（AlterClusterDynamicConfiguration）と
  # consumer group の変更・削除（AlterGroup / DeleteGroup）は付けない（画面のその操作は権限エラーになる）。メッセージを読むときの consumer は group を使わない（assign）
  kafka_ui_kafka_statements = [
    {
      # DescribeClusterDynamicConfiguration: ブローカーの設定（画面の Brokers と、起動時に版を調べる describeConfigs）。
      # WriteDataIdempotently: 画面からメッセージを送る producer（Kafka 3.x からの既定で冪等）
      Sid    = "KafkaCluster"
      Effect = "Allow"
      Action = [
        "kafka-cluster:Connect",
        "kafka-cluster:DescribeCluster",
        "kafka-cluster:DescribeClusterDynamicConfiguration",
        "kafka-cluster:WriteDataIdempotently",
      ]
      Resource = aws_msk_cluster.stream.arn
    },
    {
      Sid    = "KafkaTopics"
      Effect = "Allow"
      Action = [
        "kafka-cluster:DescribeTopic",
        "kafka-cluster:CreateTopic",
        "kafka-cluster:AlterTopic",
        "kafka-cluster:DeleteTopic",
        "kafka-cluster:DescribeTopicDynamicConfiguration",
        "kafka-cluster:AlterTopicDynamicConfiguration",
        "kafka-cluster:ReadData",
        "kafka-cluster:WriteData",
      ]
      Resource = local.topic_arns
    },
    {
      Sid      = "KafkaGroups"
      Effect   = "Allow"
      Action   = ["kafka-cluster:DescribeGroup"]
      Resource = local.group_arns
    },
  ]
  # IAM ロールと Cloud Map の名前空間の description（名前空間の description は変えると作り直しになるので、今のまま）
  kafka_descriptions = {
    namespace     = "Kafbat UI of ${local.name_prefix} (IaC/terraform/aws-managed/pipeline/stream)"
    telegraf_task = "Telegraf task - write SNMP / gNMI / trap / syslog / MDT to MSK (IAM auth), ECS Exec"
    kafka_ui_task = "Kafbat UI task - browse the MSK cluster, create / alter / delete topics, read and write messages (MSK IAM)"
  }
}

# KRaft モード（var.kafka_version の末尾の .kraft）。ZooKeeper のノードは無く、メタデータは MSK が持つコントローラーに載る（追加料金なし）
# 複製の数はブローカーの数（var.msk_az_num）。min.insync.replicas はその 1 つ下: 2 台なら 1（1 台止まっても acks=all で書ける）、
# 3 台なら 2（1 台止まっても書け、書いたものは 2 台にある）
resource "aws_msk_configuration" "stream" {
  name           = "${local.name_prefix}-stream"
  kafka_versions = [var.kafka_version]

  server_properties = <<-EOT
    auto.create.topics.enable=true
    default.replication.factor=${var.msk_az_num}
    min.insync.replicas=${var.msk_az_num - 1}
    num.partitions=2
    log.retention.hours=24
  EOT
}

resource "aws_cloudwatch_log_group" "msk" {
  name              = "/${local.name_prefix}/msk"
  retention_in_days = var.log_retention_days
}

resource "aws_msk_cluster" "stream" {
  cluster_name           = "${local.name_prefix}-stream"
  kafka_version          = var.kafka_version
  number_of_broker_nodes = var.msk_az_num

  broker_node_group_info {
    # client_subnets: 1 AZ（1 台）にはできない。clientSubnets は別々の AZ のサブネットを 2 つか 3 つ（us-west-1 だけ 2 つ）しか受け付けない。
    # 出典: Amazon MSK API Reference「Clusters」の BrokerNodeGroupInfo.clientSubnets
    # （https://docs.aws.amazon.com/msk/1.0/apireference/clusters.html、2026-10-04 確認）。AWS で 1 つを渡して試したことは無い（未確認）
    instance_type   = var.broker_instance_type
    client_subnets  = local.broker_subnet_ids
    security_groups = [local.msk_sg_id]

    storage_info {
      ebs_storage_info {
        volume_size = 10
      }
    }
  }

  client_authentication {
    unauthenticated = false

    sasl {
      iam = true
    }
  }

  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.stream.arn
    revision = aws_msk_configuration.stream.latest_revision
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.msk.name
      }
    }
  }

  tags = { Name = "${local.name_prefix}-stream" }
}

# ブローカーのアドレスはクラスタ作成後にしか分からない。Telegraf のタスク（telegraf.tf）は環境変数で直接受け取るので、ここは手で調べるときと
# 手動構築のために置く（2026-09-28 までは Telegraf の EC2 が起動時にここから読んでいた）
resource "aws_ssm_parameter" "bootstrap" {
  name        = "/${local.name_prefix}/msk-bootstrap"
  type        = "String"
  value       = aws_msk_cluster.stream.bootstrap_brokers_sasl_iam
  description = "MSK bootstrap brokers (SASL/IAM, 9098). The Telegraf task gets them as an environment variable; kept for manual checks."
}

# IaC/terraform/aws-managed/pipeline/analytics（EMR Serverless の Spark）が読む。OSS 版には無い
output "msk_cluster_arn" {
  description = "MSK cluster ARN"
  value       = aws_msk_cluster.stream.arn
}
