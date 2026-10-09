# ---------------------------------------------------------------- syslog-ng と GoFlow2（ECS on Fargate、cycle 012）
# 機器から送ってくる syslog と NetFlow / sFlow を受けて Kafka に書く 2 つのサービス。どちらも telegraf.tf の NLB の後ろ（collector_listeners の表）で、
# クラスタも Telegraf と同じ <接頭辞>-telegraf（Kafbat UI もここ）。どちらも MSK の IAM 認証を喋れないので、MSK へは SASL/SCRAM（9096、TLS）で書く:
#   syslog-ng  udp 5140 → logs トピック。Telegraf の inputs.syslog と同じ device_log の形（app/syslog-ng/syslog-ng.conf.in）。
#              イメージは docker/images/syslog-ng/Dockerfile（AxoSyslog に設定のテンプレートと入口を足したもの）で、ops/up.sh が ECR の <接頭辞>-syslog-ng に置く
#   GoFlow2    udp 2055（NetFlow v5 / v9 / IPFIX）と udp 6343（sFlow）→ flows トピック。GoFlow2 の JSON のまま書き、Spark が共通の形に読み替える
#              （app/spark/snmp_sinks.py）。イメージは netsampler/goflow2 を ECR の <接頭辞>-goflow2 に写したもの（設定は全部フラグなので Dockerfile は無い）
# ブローカー・認証・資格情報・実行ロールの権限は msk.tf / OSS 版の kafka.tf の kafka_collector_* の locals が渡す（マネージド版は SCRAM で、ユーザー名と
# パスワードは Secrets Manager の AmazonMSK_<接頭辞>-syslog-ng / -goflow2 を ECS の secrets で入れる。コレクターごとに別のユーザー（User:syslog-ng /
# User:goflow2 / User:gnmic。cycle 031）。OSS 版は認証なしの 9092）。Terraform は secret の値に触らない。
# SG（syslog_ng / goflow2）のルールは IaC/terraform/aws-managed/base/core の security_groups.tf（OSS 版は oss.tf）の通信の表:
#   NLB の SG から udp 5140 / tcp 5140（syslog-ng）、udp 2055 / 6343 / tcp 8081（GoFlow2）を受け（tcp はヘルスチェック）、MSK の 9096（OSS 版は Kafka の 9092）と
#   エンドポイント（Secrets Manager・ECR・CloudWatch Logs）へ送る
# どちらも 1 タスク（機器から送ってくるものを受けるだけなので、増やしても同じものを 2 回書かない。いまは 1 つで足りる）。
# OSS 版（cycle 005。IaC/terraform/oss/pipeline/stream）はこのファイルをシンボリックリンクで使う

locals {
  syslog_ng_repository_url = try(data.terraform_remote_state.ecr.outputs.syslog_ng_repository_url, "")
  goflow2_repository_url   = try(data.terraform_remote_state.ecr.outputs.goflow2_repository_url, "")
  syslog_ng_log_group      = "/ecs/${local.name_prefix}-syslog-ng"
  goflow2_log_group        = "/ecs/${local.name_prefix}-goflow2"
  collector_scram          = local.kafka_collector_auth == "scram"

  # GoFlow2 の引数（イメージの ENTRYPOINT は ./goflow2）。SCRAM なら TLS と SCRAM-SHA-512 を足す（ユーザー名とパスワードは環境変数 KAFKA_SASL_USER / KAFKA_SASL_PASS）。
  # -addr の 8081 は /metrics と /__health（NLB のヘルスチェック。telegraf.tf の collector_health_checks）
  goflow2_command = concat(
    [
      "-listen=netflow://:2055,sflow://:6343",
      "-transport=kafka",
      "-transport.kafka.brokers=${local.kafka_collector_brokers}",
      "-transport.kafka.topic=flows",
      "-format=json",
      "-addr=:8081",
    ],
    local.collector_scram ? ["-transport.kafka.tls", "-transport.kafka.sasl=scram-sha512"] : [],
  )
}

resource "aws_cloudwatch_log_group" "syslog_ng" {
  name              = local.syslog_ng_log_group
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "goflow2" {
  name              = local.goflow2_log_group
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------- syslog-ng
resource "aws_ecs_task_definition" "syslog_ng" {
  family                   = "${local.name_prefix}-syslog-ng"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.syslog_ng_execution.arn
  task_role_arn            = aws_iam_role.syslog_ng_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "syslog-ng"
      image     = "${local.syslog_ng_repository_url}:${var.syslog_ng_image_tag}"
      essential = true
      portMappings = [
        # 機器の syslog（NLB の 5140 から）。NLB のヘルスチェックは tcp の 5140 に来る（tcp で来た syslog も書く）が、tcp の行は書かない:
        # load_balancer の付いたサービスでは同じ containerPort を 2 行書くと CreateService が InvalidParameterException で断る（2026-10-09 の AWS 検証）。
        # awsvpc では portMappings に無いポートにも SG が通せば届く
        { containerPort = 5140, protocol = "udp" },
      ]
      environment = [
        { name = "KAFKA_BROKERS", value = local.kafka_collector_brokers },
        { name = "KAFKA_AUTH", value = local.kafka_collector_auth },
        { name = "SYSLOG_STANDARD", value = var.syslog_standard },
      ]
      secrets = local.kafka_collector_secrets["syslog-ng"]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.syslog_ng.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "syslog-ng"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = local.syslog_ng_repository_url != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state から syslog_ng_repository_url が読めない（cycle 012 より前の ECR）。IaC/terraform/aws-managed/base/ecr を先に apply する（ops/up.sh の手順 1）。"
    }
  }
}

resource "aws_ecs_service" "syslog_ng" {
  name            = "${local.name_prefix}-syslog-ng"
  cluster         = aws_ecs_cluster.telegraf.id
  task_definition = aws_ecs_task_definition.syslog_ng.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # aws ecs execute-command でタスクの中に入れる（/tmp/syslog-ng.conf を見る程度）
  enable_execute_command = true

  # 新しいタスクが NLB のヘルスチェックを通ってから古いタスクを外す（入れ替えのあいだも syslog を落とさない）
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = local.telegraf_dialout_subnet_ids
    security_groups  = [local.syslog_ng_sg_id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = { for k, v in local.collector_listeners : k => v if v.service == "syslog-ng" }
    content {
      target_group_arn = aws_lb_target_group.telegraf_dialout[load_balancer.key].arn
      container_name   = "syslog-ng"
      container_port   = load_balancer.value.container
    }
  }

  lifecycle {
    precondition {
      condition     = local.syslog_ng_sg_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に syslog_ng の SG が無い（cycle 012 より前の土台）。IaC/terraform/aws-managed/base/core を先に apply する（ops/up.sh なら手順 1 で apply される）。"
    }
  }

  depends_on = [
    aws_lb_listener.telegraf_dialout,
    aws_iam_role_policy.syslog_ng_task,
    aws_iam_role_policy.syslog_ng_execution,
    aws_iam_role_policy_attachment.syslog_ng_execution,
  ]
}

# ---------------------------------------------------------------- GoFlow2
resource "aws_ecs_task_definition" "goflow2" {
  family                   = "${local.name_prefix}-goflow2"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.goflow2_execution.arn
  task_role_arn            = aws_iam_role.goflow2_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "goflow2"
      image     = "${local.goflow2_repository_url}:${var.goflow2_image_tag}"
      essential = true
      command   = local.goflow2_command
      portMappings = [
        { containerPort = 2055, protocol = "udp" }, # NetFlow v5 / v9 / IPFIX（NLB の 2055 から）
        { containerPort = 6343, protocol = "udp" }, # sFlow（NLB の 6343 から）
        { containerPort = 8081, protocol = "tcp" }, # /__health（NLB のヘルスチェック）と /metrics
      ]
      secrets = local.kafka_collector_secrets["goflow2"]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.goflow2.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "goflow2"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = local.goflow2_repository_url != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state から goflow2_repository_url が読めない（cycle 012 より前の ECR）。IaC/terraform/aws-managed/base/ecr を先に apply する（ops/up.sh の手順 1）。"
    }
  }
}

resource "aws_ecs_service" "goflow2" {
  name            = "${local.name_prefix}-goflow2"
  cluster         = aws_ecs_cluster.telegraf.id
  task_definition = aws_ecs_task_definition.goflow2.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  enable_execute_command = true

  # 新しいタスクが NLB のヘルスチェックを通ってから古いタスクを外す
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = local.telegraf_dialout_subnet_ids
    security_groups  = [local.goflow2_sg_id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = { for k, v in local.collector_listeners : k => v if v.service == "goflow2" }
    content {
      target_group_arn = aws_lb_target_group.telegraf_dialout[load_balancer.key].arn
      container_name   = "goflow2"
      container_port   = load_balancer.value.container
    }
  }

  lifecycle {
    precondition {
      condition     = local.goflow2_sg_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に goflow2 の SG が無い（cycle 012 より前の土台）。IaC/terraform/aws-managed/base/core を先に apply する（ops/up.sh なら手順 1 で apply される）。"
    }
  }

  depends_on = [
    aws_lb_listener.telegraf_dialout,
    aws_iam_role_policy.goflow2_task,
    aws_iam_role_policy.goflow2_execution,
    aws_iam_role_policy_attachment.goflow2_execution,
  ]
}

# ---------------------------------------------------------------- IAM
# 実行ロール: ECR・CloudWatch Logs と、マネージド版では SCRAM の secret（kafka_collector_execution_statements。OSS 版は空なのでポリシーを作らない）
resource "aws_iam_role" "syslog_ng_execution" {
  name               = "${local.name_prefix}-syslog-ng-exec"
  description        = "ECS task execution role of the syslog-ng task (ECR pull, CloudWatch Logs, MSK SCRAM secret in the managed build)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "syslog_ng_execution" {
  role       = aws_iam_role.syslog_ng_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "syslog_ng_execution" {
  count = length(local.kafka_collector_execution_statements) > 0 ? 1 : 0

  name = "${local.name_prefix}-syslog-ng-exec"
  role = aws_iam_role.syslog_ng_execution.name

  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = local.kafka_collector_execution_statements
  })
}

resource "aws_iam_role" "goflow2_execution" {
  name               = "${local.name_prefix}-goflow2-exec"
  description        = "ECS task execution role of the GoFlow2 task (ECR pull, CloudWatch Logs, MSK SCRAM secret in the managed build)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "goflow2_execution" {
  role       = aws_iam_role.goflow2_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "goflow2_execution" {
  count = length(local.kafka_collector_execution_statements) > 0 ? 1 : 0

  name = "${local.name_prefix}-goflow2-exec"
  role = aws_iam_role.goflow2_execution.name

  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = local.kafka_collector_execution_statements
  })
}

# タスクロール: ECS Exec だけ（Kafka へは SCRAM か認証なしなので、IAM の Kafka の権限は要らない）
resource "aws_iam_role" "syslog_ng_task" {
  name               = "${local.name_prefix}-syslog-ng-task"
  description        = "syslog-ng task - write device syslog to Kafka (MSK SASL/SCRAM or plain Kafka), ECS Exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "syslog_ng_task" {
  name   = "${local.name_prefix}-syslog-ng-task"
  role   = aws_iam_role.syslog_ng_task.name
  policy = local.collector_task_policy
}

resource "aws_iam_role" "goflow2_task" {
  name               = "${local.name_prefix}-goflow2-task"
  description        = "GoFlow2 task - write NetFlow and sFlow to Kafka (MSK SASL/SCRAM or plain Kafka), ECS Exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "goflow2_task" {
  name   = "${local.name_prefix}-goflow2-task"
  role   = aws_iam_role.goflow2_task.name
  policy = local.collector_task_policy
}

locals {
  collector_task_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
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
resource "aws_iam_role_policy_attachment" "syslog_ng_execution_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.syslog_ng_execution.name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "syslog_ng_task_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.syslog_ng_task.name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "goflow2_execution_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.goflow2_execution.name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "goflow2_task_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.goflow2_task.name
  policy_arn = local.perimeter_policy_arn
}
