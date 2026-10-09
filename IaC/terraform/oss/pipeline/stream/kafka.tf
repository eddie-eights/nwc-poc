# nwc-oss - PIPELINE stream root module of the OSS build (cycle 005). The managed build (IaC/terraform/aws-managed/pipeline/stream) uses MSK (its msk.tf);
# this one runs Kafka on ECS on Fargate instead (KRaft, 3 nodes, data on the EFS of IaC/terraform/aws-managed/base/core). Everything else of the root -
# locals.tf, telegraf.tf, kafka_ui.tf, access.tf, outputs.tf and the variables - is the managed build's file through a symbolic link,
# and what differs between the two Kafkas comes from the kafka_* locals below (msk.tf defines the same names).

# ---------------------------------------------------------------- Kafka（ECS on Fargate、KRaft）
# apache/kafka を 3 台。どの台も broker と controller を兼ねる（KRaft の combined。ZooKeeper は無い）。
# 台ごとに ECS のサービスを分ける（kafka-1〜3）。どの台も自分の番号（KAFKA_NODE_ID）と自分の EFS のアクセスポイント（/kafka-N）を持つ。
# 1 つのサービスで 3 タスクにすると、番号と置き場をタスクごとに固定できない。台 N はサブネットの N 番目（a / b / c。AZ ごとに 1 台）。
# 名前は Cloud Map の kafka-N.<接頭辞>-stream.internal（名前空間もこのファイル。cycle 010 で Kafbat UI が Web の EC2 に移り、マネージド版から名前空間が無くなった）。
# 認証は無い（クライアントは PLAINTEXT の 9092、controller は 9093）。
# 届くのは SG で絞った相手だけ（IaC/terraform/aws-managed/base/core の oss.tf の通信の表: Telegraf・gnmic・syslog-ng・GoFlow2・Spark・Web の EC2（Kafbat UI）→ 9092、
# Kafka どうし 9092〜9093、Kafka → EFS 2049）。
# イメージは apache/kafka を ECR の <接頭辞>-kafka に写したもの（閉域で Docker Hub に届かない。ops/oss/up.sh が写す）。
# CLUSTER_ID は 3 台で同じ値で、ops/oss/up.sh が 1 回だけ作って SSM の /<接頭辞>/kafka/cluster-id（String か SecureString）に置く。
# ECS の secrets で渡すので、Terraform の state には入らない。
# terraform apply でタスク定義が変わると 3 つのサービスが同時に入れ替わり、そのあいだ controller の過半数が無い（データは EFS に残るので戻る）。
# ops/oss/up.sh は、apply の前に変わる台を plan で調べて 1 台ずつ -target で入れ替え、間で controller と複製がそろうのを待つ
# （ops/oss/roll-nodes.sh。設計の未確定事項 4。OSS_ROLL=0 で一度に入れ替える）

variable "kafka_image_tag" {
  description = "Tag of the Kafka image in the <prefix>-kafka repository (apache/kafka copied to ECR by ops/oss/up.sh). Same version as ops/oss/oss-images.sh."
  type        = string
  default     = "4.3.1"
}

variable "kafka_task_cpu" {
  description = "Fargate CPU units of each Kafka task (ARM64)."
  type        = number
  default     = 1024

  validation {
    condition     = contains([512, 1024, 2048], var.kafka_task_cpu)
    error_message = "kafka_task_cpu must be 512, 1024 or 2048."
  }
}

variable "kafka_task_memory" {
  description = "Fargate memory (MiB) of each Kafka task. Half of it is the JVM heap, the rest is left to the page cache. Must be a valid pair with kafka_task_cpu (1024 takes 2048-8192)."
  type        = number
  default     = 2048

  validation {
    condition     = contains([1024, 2048, 4096, 8192], var.kafka_task_memory)
    error_message = "kafka_task_memory must be 1024, 2048, 4096 or 8192."
  }
}

locals {
  kafka_image     = "${try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["kafka"], "")}:${var.kafka_image_tag}"
  kafka_log_group = "/ecs/${local.name_prefix}-kafka"
  # Cloud Map の名前空間（Kafka の kafka-1〜3 が名前を登録する）
  stream_service_namespace = "${local.name_prefix}-stream.internal"
  # 土台（IaC/terraform/aws-managed/base/core の oss.tf）の SG と EFS。マネージド版の土台や古い state では無いので try にして、precondition で止める
  kafka_sg_id         = try(data.terraform_remote_state.main.outputs.security_group_ids["kafka"], "")
  efs_file_system_id  = try(data.terraform_remote_state.main.outputs.efs_file_system_id, "")
  efs_file_system_arn = "arn:${local.partition}:elasticfilesystem:${var.region}:${local.account_id}:file-system/${local.efs_file_system_id}"

  # 台の番号（node.id）→ サブネット（1 台目は a）
  kafka_nodes = { for i in range(3) : tostring(i + 1) => try(local.subnet_ids[i], "") }
  kafka_hosts = { for n, _ in local.kafka_nodes : n => "kafka-${n}.${local.stream_service_namespace}" }

  kafka_cluster_id_parameter = "/${local.name_prefix}/kafka/cluster-id"
  kafka_cluster_id_arn       = "${local.ssm_parameter_arn}${local.kafka_cluster_id_parameter}"

  # 公式イメージは KAFKA_* の環境変数を server.properties に変える。1 つでも書くと既定のファイルは使われないので、要るものは全部書く
  # （005 の手元の compose で確かめた値。手元の docker/compose/compose.yaml とは、ホスト名が Cloud Map の名前なのと、ヒープ・保持期間と、
  #  EXTERNAL リスナーの有無が違う。tests/test_local_compose.py が突き合わせる）
  kafka_environment = [
    { name = "KAFKA_PROCESS_ROLES", value = "broker,controller" },
    { name = "KAFKA_LISTENERS", value = "PLAINTEXT://:9092,CONTROLLER://:9093" },
    { name = "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP", value = "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT" },
    { name = "KAFKA_INTER_BROKER_LISTENER_NAME", value = "PLAINTEXT" },
    { name = "KAFKA_CONTROLLER_LISTENER_NAMES", value = "CONTROLLER" },
    # 固定の voter（controller.quorum.voters）。公式イメージは storage の format を --standalone / --initial-controllers なしで打つので、
    # 設計の controller.quorum.bootstrap.servers（動的な voter）では組めない（005 の手元の compose で確かめた）
    { name = "KAFKA_CONTROLLER_QUORUM_VOTERS", value = join(",", [for n, h in local.kafka_hosts : "${n}@${h}:9093"]) },
    { name = "KAFKA_LOG_DIRS", value = "/var/lib/kafka/data" },
    # 複製は 3 台全部、書けるのは 2 台がそろっているあいだ（1 台止まっても acks=all で書ける）
    { name = "KAFKA_DEFAULT_REPLICATION_FACTOR", value = "3" },
    { name = "KAFKA_MIN_INSYNC_REPLICAS", value = "2" },
    { name = "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR", value = "3" },
    { name = "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", value = "3" },
    { name = "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", value = "2" },
    { name = "KAFKA_SHARE_COORDINATOR_STATE_TOPIC_REPLICATION_FACTOR", value = "3" },
    { name = "KAFKA_SHARE_COORDINATOR_STATE_TOPIC_MIN_ISR", value = "2" },
    # 内部トピック（__consumer_offsets・__transaction_state・__share_group_state）。既定 50。PoC では 1（MSK は configuration に項目が無く変えられない）。
    # __consumer_offsets が 1 だと group coordinator が 1 台に寄る。consumer group が増える構成に変えるときは、トピックを作り直して増やす（既定は 50）
    { name = "KAFKA_OFFSETS_TOPIC_NUM_PARTITIONS", value = "1" },
    { name = "KAFKA_TRANSACTION_STATE_LOG_NUM_PARTITIONS", value = "1" },
    { name = "KAFKA_SHARE_COORDINATOR_STATE_TOPIC_NUM_PARTITIONS", value = "1" },
    # ここから 3 つは MSK の configuration（IaC/terraform/aws-managed/pipeline/stream/msk.tf の server_properties）と同じ
    { name = "KAFKA_NUM_PARTITIONS", value = "2" },
    { name = "KAFKA_LOG_RETENTION_HOURS", value = "24" },
    { name = "KAFKA_AUTO_CREATE_TOPICS_ENABLE", value = "true" },
    { name = "KAFKA_HEAP_OPTS", value = "-Xms${floor(var.kafka_task_memory / 2)}m -Xmx${floor(var.kafka_task_memory / 2)}m" },
  ]

  # ---- Kafka の差し替え口（マネージド版は msk.tf が同じ名前で定義する）
  # 共有のファイル（telegraf.tf・kafka_ui.tf・outputs.tf）が読む
  kafka_bootstrap_brokers = join(",", [for n, h in local.kafka_hosts : "${h}:9092"])
  # Kafbat UI はプロトコルで選ぶ（kafka_ui.tf）。認証が無いので PLAINTEXT だけ（oss.auto.tfvars の kafka_ui_security_protocol）
  kafka_bootstrap_by_protocol = { PLAINTEXT = local.kafka_bootstrap_brokers }
  # syslog-ng と GoFlow2（collectors.tf）と gnmic（gnmic.tf）の口。マネージド版の SCRAM（secret・KMS・association）は OSS 版には無く、認証なしの 9092 に書く。
  # kafka_collector_secrets はマネージド版と同じ形（コレクター名 → ECS の secrets）で、どれも空
  kafka_collector_brokers              = local.kafka_bootstrap_brokers
  kafka_collector_auth                 = "none"
  kafka_collector_secrets              = { "syslog-ng" = [], "goflow2" = [], "gnmic" = [] }
  kafka_collector_execution_statements = []
  # Telegraf のタスクの環境変数に足す。telegraf.sh が outputs.kafka の IAM 認証の行を消し、aws_config も書かない
  kafka_client_environment = [{ name = "KAFKA_AUTH", value = "none" }]
  # Telegraf のタスクロールに足す Kafka の権限。認証が無いので無い
  telegraf_kafka_statements = []
  # Kafbat UI が動く Web の EC2 のロールに足す権限（kafka_ui.tf）。認証が無いので要るものは無いが、ポリシーは空にできないので MSK を拒む Deny だけ置く
  kafka_ui_kafka_statements = [{
    Sid      = "NoMsk"
    Effect   = "Deny"
    Action   = ["kafka-cluster:*"]
    Resource = "*"
  }]
  # IAM ロールと Cloud Map の名前空間の description（名前空間の description は変えると作り直しになるので、cycle 010 より前のまま）
  kafka_descriptions = {
    namespace     = "Kafka and Kafbat UI of ${local.name_prefix} (IaC/terraform/oss/pipeline/stream)"
    telegraf_task = "Telegraf dial-out task - write SNMP traps to Kafka (PLAINTEXT, no IAM), ECS Exec"
  }
}

# Telegraf と分ける（aws ecs list-services で Kafka の 3 台だけが見える）
resource "aws_ecs_cluster" "kafka" {
  name = "${local.name_prefix}-kafka"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "kafka" {
  name              = local.kafka_log_group
  retention_in_days = var.log_retention_days
}

resource "aws_service_discovery_private_dns_namespace" "stream" {
  name        = local.stream_service_namespace
  description = local.kafka_descriptions.namespace
  vpc         = local.vpc_id
}

resource "aws_service_discovery_service" "kafka" {
  for_each = local.kafka_nodes

  name = "kafka-${each.key}"
  # タスクの登録が残っていても destroy できるようにする
  force_destroy = true

  # タスクを作り直すと IP が変わる。TTL を短くして、ほかの台とクライアントが早く新しい IP を引くようにする
  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.stream.id
    routing_policy = "MULTIVALUE"

    dns_records {
      ttl  = 10
      type = "A"
    }
  }
}

# 台ごとの置き場。posix_user でどのタスクの読み書きも uid / gid 1000（公式イメージの appuser）にそろえ、/kafka-N をそのユーザーで作る
resource "aws_efs_access_point" "kafka" {
  for_each = local.kafka_nodes

  file_system_id = local.efs_file_system_id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/kafka-${each.key}"

    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0750"
    }
  }

  tags = { Name = "${local.name_prefix}-kafka-${each.key}" }

  lifecycle {
    precondition {
      condition     = local.efs_file_system_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に efs_file_system_id が無いか空（マネージド版の土台か、oss.tf より前の土台）。IaC/terraform/oss/base/core を先に apply する。"
    }
  }
}

# ヘルスチェックは付けない。LB が無いので入れ替えの判断にしか使われず、ポートが開くかだけでは中身が分からない。
# EFS からログを戻すのに時間がかかると、まだ起動中の台を ECS が止めてしまう
resource "aws_ecs_task_definition" "kafka" {
  for_each = local.kafka_nodes

  family                   = "${local.name_prefix}-kafka-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.kafka_task_cpu
  memory                   = var.kafka_task_memory
  execution_role_arn       = aws_iam_role.kafka_execution.arn
  task_role_arn            = aws_iam_role.kafka_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # EFS のポリシー（IaC/terraform/aws-managed/base/core の oss.tf）が TLS と IAM の無いマウントを拒むので、両方を有効にする
  volume {
    name = "data"

    efs_volume_configuration {
      file_system_id     = local.efs_file_system_id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.kafka[each.key].id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name         = "kafka"
      image        = local.kafka_image
      essential    = true
      portMappings = [{ containerPort = 9092, protocol = "tcp" }, { containerPort = 9093, protocol = "tcp" }]
      environment = concat(local.kafka_environment, [
        { name = "KAFKA_NODE_ID", value = each.key },
        { name = "KAFKA_ADVERTISED_LISTENERS", value = "PLAINTEXT://${local.kafka_hosts[each.key]}:9092" },
      ])
      secrets     = [{ name = "CLUSTER_ID", valueFrom = local.kafka_cluster_id_arn }]
      mountPoints = [{ sourceVolume = "data", containerPath = "/var/lib/kafka/data", readOnly = false }]
      # 止めるときにログを閉じ、リーダーを渡す時間（Fargate の上限）
      stopTimeout = 120
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.kafka.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "kafka-${each.key}"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["kafka"], "") != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state に kafka のリポジトリが無い（project = nwc-oss でない ECR か、古い ECR）。IaC/terraform/oss/base/ecr を先に apply する。"
    }
  }
}

resource "aws_ecs_service" "kafka" {
  for_each = local.kafka_nodes

  name            = "${local.name_prefix}-kafka-${each.key}"
  cluster         = aws_ecs_cluster.kafka.id
  task_definition = aws_ecs_task_definition.kafka[each.key].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # aws ecs execute-command でタスクの中に入れる（kafka-topics.sh などを打つ）
  enable_execute_command = true

  # 同じ番号の台を 2 つ同時に立てない（同じ置き場と node.id を 2 つのタスクが使うと壊れる）。入れ替えでは古いタスクを止めてから新しいタスクを起こす
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = [each.value]
    security_groups  = [local.kafka_sg_id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.kafka[each.key].arn
  }

  lifecycle {
    precondition {
      condition     = local.kafka_sg_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に kafka の SG が無い（マネージド版の土台か、oss.tf より前の土台）。IaC/terraform/oss/base/core を先に apply する。"
    }
    precondition {
      condition     = each.value != ""
      error_message = "IaC/terraform/aws-managed/base/core の state のサブネットが 3 つ無い（Kafka は AZ ごとに 1 台で 3 つ要る）。"
    }
  }

  depends_on = [
    aws_iam_role_policy.kafka_task,
    aws_iam_role_policy.kafka_execution,
    aws_iam_role_policy_attachment.kafka_execution,
  ]
}

# ---------------------------------------------------------------- IAM（3 台で同じロール）
resource "aws_iam_role" "kafka_execution" {
  name               = "${local.name_prefix}-kafka-exec"
  description        = "ECS task execution role of the Kafka tasks (ECR pull, CloudWatch Logs, CLUSTER_ID from SSM)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "kafka_execution" {
  role       = aws_iam_role.kafka_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# secrets の CLUSTER_ID（SecureString でも AWS 管理の aws/ssm キーなので kms:Decrypt は要らない）
resource "aws_iam_role_policy" "kafka_execution" {
  name = "${local.name_prefix}-kafka-exec"
  role = aws_iam_role.kafka_execution.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ClusterId"
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = local.kafka_cluster_id_arn
    }]
  })
}

resource "aws_iam_role" "kafka_task" {
  name               = "${local.name_prefix}-kafka-task"
  description        = "Kafka task role - mount the Kafka access points of the EFS (IAM authorization), ECS Exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "kafka_task" {
  name = "${local.name_prefix}-kafka-task"
  role = aws_iam_role.kafka_task.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # EFS の IAM 認可（タスク定義の authorization_config の iam）。Kafka の 3 つのアクセスポイントからだけ
        Sid      = "Efs"
        Effect   = "Allow"
        Action   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource = local.efs_file_system_arn
        Condition = {
          StringEquals = { "elasticfilesystem:AccessPointArn" = [for ap in aws_efs_access_point.kafka : ap.arn] }
        }
      },
      {
        # ECS Exec（aws ecs execute-command）
        Sid    = "EcsExec"
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel",
        ]
        Resource = "*"
      },
    ]
  })
}

# IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない呼び出しを拒む）。実行ロールとタスクロールの両方に付ける
resource "aws_iam_role_policy_attachment" "kafka_execution_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.kafka_execution.name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "kafka_task_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.kafka_task.name
  policy_arn = local.perimeter_policy_arn
}

# ---------------------------------------------------------------- outputs（OSS 版だけ。共通の output は outputs.tf）
output "kafka_ecs_cluster_name" {
  description = "ECS cluster of the Kafka tasks (one service per node)"
  value       = aws_ecs_cluster.kafka.name
}

output "kafka_service_names" {
  description = "ECS service of each Kafka node, keyed by node.id (1 to 3). Replace them one at a time - the controllers need two of the three. ops/oss/up.sh does it (ops/oss/roll-nodes.sh)."
  value       = { for n, s in aws_ecs_service.kafka : n => s.name }
}

output "kafka_cluster_id_parameter" {
  description = "SSM parameter holding the CLUSTER_ID of Kafka (the same for the three nodes). ops/oss/up.sh creates it once, before this root is applied."
  value       = local.kafka_cluster_id_parameter
}

output "kafka_log_group_name" {
  description = "CloudWatch Logs group of the Kafka tasks (stream prefix kafka-N)"
  value       = aws_cloudwatch_log_group.kafka.name
}
