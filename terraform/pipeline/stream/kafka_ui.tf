# ---------------------------------------------------------------- Kafbat UI（ECS on Fargate）
# MSK には Kafka の中（トピック・パーティション・メッセージ・consumer group）を見る画面が無いので、Kafbat UI（OSS の Kafka の Web コンソール）を
# 1 タスク立てる（2026-10-05 のユーザー決定）。見るだけにはしない（READONLY を付けない）: 画面からトピックの追加・設定の変更・削除と、メッセージの送信ができる。
# イメージは ghcr.io/kafbat/kafka-ui を ECR に写したもの（ops/up.sh の手順 2。AWS の外へ出る経路が無いので ghcr.io から直接は引けない）。
# MSK へは IAM 認証（var.kafka_ui_security_protocol = SASL_SSL。aws-msk-iam-auth は api.jar に入っている）で、認証情報はタスクロール（既定の
# 認証情報チェーンの ECS コンテナの分。awsRoleArn は使わないので STS も要らない）。PLAINTEXT は認証の無い Kafka（cycle 005 の OSS 版）向けで、この root の MSK は受け付けない。
# 開き方は Grafana と同じで、output kafka_ui_port_forward_command（web の EC2 を踏み台にした SSM のポートフォワード）。画面はログインフォーム（AUTH_TYPE=LOGIN_FORM）で、
# admin のパスワードは ops/up.sh が作る SSM の SecureString（output kafka_ui_password_command）。設定は環境変数だけ（DYNAMIC_CONFIG_ENABLED は既定の false）で、
# 画面からクラスターの設定は変えられない

locals {
  kafka_ui_image     = "${try(data.terraform_remote_state.ecr.outputs.kafka_ui_repository_url, "")}:${var.kafka_ui_image_tag}"
  kafka_ui_log_group = "/ecs/${local.name_prefix}-kafka-ui"
  # Cloud Map の名前空間（stream で ECS のサービスを DNS で引くのは Kafbat UI だけ）
  stream_service_namespace    = "${local.name_prefix}-stream.internal"
  kafka_ui_password_parameter = "/${local.name_prefix}/kafka-ui/admin-password"
  kafka_ui_password_arn       = "${local.ssm_parameter_arn}${local.kafka_ui_password_parameter}"

  # 認証の違いはここだけ。SASL_SSL は MSK の IAM のポート（9098）、PLAINTEXT は平文のポート
  kafka_ui_iam               = var.kafka_ui_security_protocol == "SASL_SSL"
  kafka_ui_bootstrap_servers = local.kafka_ui_iam ? aws_msk_cluster.stream.bootstrap_brokers_sasl_iam : aws_msk_cluster.stream.bootstrap_brokers
  # Kafbat UI の文書「AWS IAM」（https://ui.docs.kafbat.io/configuration/authentication/for-kafka/aws-iam 、2026-10-05 確認）の 4 つ
  kafka_ui_iam_environment = local.kafka_ui_iam ? [
    { name = "KAFKA_CLUSTERS_0_PROPERTIES_SASL_MECHANISM", value = "AWS_MSK_IAM" },
    { name = "KAFKA_CLUSTERS_0_PROPERTIES_SASL_CLIENT_CALLBACK_HANDLER_CLASS", value = "software.amazon.msk.auth.iam.IAMClientCallbackHandler" },
    { name = "KAFKA_CLUSTERS_0_PROPERTIES_SASL_JAAS_CONFIG", value = "software.amazon.msk.auth.iam.IAMLoginModule required;" },
  ] : []
}

resource "aws_cloudwatch_log_group" "kafka_ui" {
  name              = local.kafka_ui_log_group
  retention_in_days = var.log_retention_days
}

resource "aws_service_discovery_private_dns_namespace" "stream" {
  name        = local.stream_service_namespace
  description = "Kafbat UI of ${local.name_prefix} (terraform/pipeline/stream)"
  vpc         = local.vpc_id
}

resource "aws_service_discovery_service" "kafka_ui" {
  name = "kafka-ui"
  # タスクの登録が残っていても destroy できるようにする
  force_destroy = true

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.stream.id
    routing_policy = "MULTIVALUE"

    dns_records {
      ttl  = 10
      type = "A"
    }
  }
}

resource "aws_ecs_task_definition" "kafka_ui" {
  family                   = "${local.name_prefix}-kafka-ui"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.kafka_ui_task_cpu
  memory                   = var.kafka_ui_task_memory
  execution_role_arn       = aws_iam_role.kafka_ui_execution.arn
  task_role_arn            = aws_iam_role.kafka_ui_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name         = "kafka-ui"
      image        = local.kafka_ui_image
      essential    = true
      portMappings = [{ containerPort = 8080, protocol = "tcp" }]
      environment = concat(
        [
          { name = "KAFKA_CLUSTERS_0_NAME", value = aws_msk_cluster.stream.cluster_name },
          { name = "KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS", value = local.kafka_ui_bootstrap_servers },
          { name = "KAFKA_CLUSTERS_0_PROPERTIES_SECURITY_PROTOCOL", value = var.kafka_ui_security_protocol },
        ],
        local.kafka_ui_iam_environment,
        [
          # ログインフォーム。AUTH_TYPE が無いと誰でも開ける（2026-10-05 に手元の Docker で確認）
          { name = "AUTH_TYPE", value = "LOGIN_FORM" },
          { name = "SPRING_SECURITY_USER_NAME", value = "admin" },
          # 新しい版を GitHub に聞きにいかない（閉域で届かない）
          { name = "GITHUB_RELEASE_INFO_ENABLED", value = "false" },
          # イメージの CMD（java ... $JAVA_OPTS -jar api.jar）が読む。タスクのメモリの 75% をヒープにする
          { name = "JAVA_OPTS", value = "-XX:MaxRAMPercentage=75" },
        ],
      )
      secrets = [
        { name = "SPRING_SECURITY_USER_PASSWORD", valueFrom = local.kafka_ui_password_arn },
      ]
      # /actuator/health はログイン無しで開いていて、Kafka に届かなくても UP を返す（2026-10-05 に手元の Docker で確認）。wget はイメージの busybox
      healthCheck = {
        command     = ["CMD-SHELL", "wget -q -O /dev/null http://localhost:8080/actuator/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 120
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.kafka_ui.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "kafka-ui"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.kafka_ui_repository_url, "") != ""
      error_message = "terraform/base/ecr の state から kafka_ui_repository_url が読めない（2026-10-05 より前の ECR）。terraform/base/ecr を先に apply する（ops/up.sh の手順 1）。"
    }
    precondition {
      condition     = local.kafka_ui_bootstrap_servers != ""
      error_message = "MSK に kafka_ui_security_protocol（${var.kafka_ui_security_protocol}）のブートストラップが無い。この root の MSK は IAM（SASL_SSL）だけを受け付ける。"
    }
  }
}

resource "aws_ecs_service" "kafka_ui" {
  name            = "${local.name_prefix}-kafka-ui"
  cluster         = aws_ecs_cluster.telegraf.id
  task_definition = aws_ecs_task_definition.kafka_ui.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # 1 タスクだけ（サブネット a。web の EC2 と同じ）。状態を持たないので、作り直すときに古いタスクを待たない
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = [local.telegraf_subnet_id]
    security_groups  = [local.kafka_ui_sg_id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.kafka_ui.arn
  }

  lifecycle {
    precondition {
      condition     = local.kafka_ui_sg_id != ""
      error_message = "terraform/base/core の state に kafka_ui の SG が無い（2026-10-05 より前の土台）。terraform/base/core を先に apply する（ops/up.sh なら手順 1 で apply される）。"
    }
  }

  depends_on = [
    aws_iam_role_policy.kafka_ui_task,
    aws_iam_role_policy.kafka_ui_execution,
    aws_iam_role_policy_attachment.kafka_ui_execution,
  ]
}

# ---------------------------------------------------------------- IAM
resource "aws_iam_role" "kafka_ui_execution" {
  name               = "${local.name_prefix}-kafka-ui-exec"
  description        = "ECS task execution role of the Kafbat UI task (ECR pull, CloudWatch Logs, admin password from SSM)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "kafka_ui_execution" {
  role       = aws_iam_role.kafka_ui_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# secrets の SecureString（AWS 管理の aws/ssm キーなので kms:Decrypt は要らない）
resource "aws_iam_role_policy" "kafka_ui_execution" {
  name = "${local.name_prefix}-kafka-ui-exec"
  role = aws_iam_role.kafka_ui_execution.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "AdminPassword"
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = local.kafka_ui_password_arn
    }]
  })
}

resource "aws_iam_role" "kafka_ui_task" {
  name               = "${local.name_prefix}-kafka-ui-task"
  description        = "Kafbat UI task - browse the MSK cluster, create / alter / delete topics, read and write messages (MSK IAM)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

# Kafbat UI が使う Kafka の操作だけ。ブローカーの設定の変更（AlterClusterDynamicConfiguration）と consumer group の変更・削除（AlterGroup / DeleteGroup）は付けない
# （画面のその操作は権限エラーになる）。メッセージを読むときの consumer は group を使わない（assign）
resource "aws_iam_role_policy" "kafka_ui_task" {
  name = "${local.name_prefix}-kafka-ui-task"
  role = aws_iam_role.kafka_ui_task.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
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
  })
}

# terraform/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない呼び出しを拒む）。実行ロールとタスクロールの両方に付ける
resource "aws_iam_role_policy_attachment" "kafka_ui_execution_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.kafka_ui_execution.name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "kafka_ui_task_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.kafka_ui_task.name
  policy_arn = local.perimeter_policy_arn
}
