# ---------------------------------------------------------------- Telegraf (ECS on Fargate + internal NLB)
# 機器から送ってくる SNMP trap を受けて MSK に書く Telegraf（telegraf-dialout）を、Fargate で動かす（2026-09-28 まで IaC/terraform/aws-managed/pipeline/lab の EC2）。
# syslog は syslog-ng、NetFlow / sFlow は GoFlow2 が受ける（どちらも collectors.tf。cycle 012）。MDT の受け口は cycle 012 で外した（戻し方は docs/collection.md）。
# gNMI は gnmic が取りにいく（gnmic.tf。cycle 013 で telegraf-dialin の gNMI の購読と SNMP のポーリングを置き換えた）。
# 下の NLB は 3 つのサービス（telegraf-dialout・syslog-ng・GoFlow2）の共通の受け口で、どの番号をどのサービスへ渡すかは collector_listeners の表。
# telegraf-dialout は機器の一覧を持たず、台数を増やしても同じものを 2 回書かない（名前の dialout は 2026-10-04 のユーザー決定のまま。入力を役割で分けていた
# TELEGRAF_ROLE は、取りにいく側の telegraf-dialin と一緒に cycle 013 で外した）。
# イメージは docker/images/telegraf/Dockerfile（公式の telegraf に設定のテンプレートと入口を足したもの）で、ops/up.sh が ECR の <接頭辞>-telegraf に置く。
# lab の管理ネットワーク（203.0.113.0/24）は lab の EC2 の中の docker network なので、IaC/terraform/aws-managed/pipeline/lab（forward_to_telegraf）が VPC のルートと
# lab.sh forward で届ける:
#   gNMI        gnmic → 機器の gNMI（57400/tcp）。送り元はタスクの IP で、作り直すたびに変わるので、lab.sh forward は
#               タスクのサブネットの CIDR（SSM の /<接頭辞>/telegraf-source-cidr。名前は Telegraf のときのまま）で通す
#   trap        機器 → lab の EC2 の 162/udp → DNAT → 下の NLB の 162 → タスクの 1162（非 root は 1024 未満で待てない）
#   syslog      機器 → lab の EC2 の 5140/udp → DNAT → NLB の 5140 → syslog-ng のタスクの 5140
#   NetFlow / sFlow  機器 → lab の EC2 の 2055 / 6343（udp）→ DNAT → NLB の同じ番号 → GoFlow2 のタスクの同じ番号
#               （lab の SR Linux は NetFlow を出さない。試すときは lab の EC2 のホストから ops/netflow_send.py で NLB へ直接送る。
#               syslog も同じく lab の EC2 のホストから logger で NLB へ直接送れる（AWS では未確認）。docs/troubleshooting.md の「正しい送り方」の 2 つ目）
# タスクの IP は作り直すと変わるので、DNAT の宛先は変わらない NLB の IP にする（SSM の /<接頭辞>/telegraf-address）。
# NLB は UDP の送り元の IP を残す（UDP のターゲットは client IP preservation が既定で、Spark とエージェントは送り元の IP で機器を引く）。
# SG は NLB（telegraf_dialout_nlb）とタスクごとに別々で、ルールは IaC/terraform/aws-managed/base/core の security_groups.tf の通信の表にある:
#   telegraf_dialout_nlb  管理ネットワークの CIDR から udp 162 / 5140 / 2055 / 6343 を受け（送り元が機器の管理 IP のまま）、lab の SG からも udp 5140 / 2055 / 6343 を
#                         受け（lab の EC2 自身が試しに送るぶん。NLB は trap の 162 を lab の SG からは受けない。lab の SG の 162 は送る側だけ）、
#                         telegraf_dialout へ udp 1162 と tcp 8080、syslog_ng へ udp 5140 と tcp 5140、goflow2 へ udp 2055 / 6343 と tcp 8081 を送る（tcp はどれもヘルスチェック）
#   telegraf_dialout      NLB の SG から受け（送り元の IP が残っても、NLB の SG を参照したルールで通る）、MSK の 9098・エンドポイントと S3 の 443 へ送る
#   gnmic                 gnmic.tf の注記
# OSS 版（cycle 005。IaC/terraform/oss/pipeline/stream）はこのファイルをシンボリックリンクで使う。書き先は ECS の Kafka（kafka-1〜3 の 9092、認証なし）で、
# KAFKA_BROKERS・KAFKA_AUTH=none（telegraf.sh が outputs.kafka の IAM の行を消す）・Kafka の権限は kafka.tf の kafka_* の locals が渡す。SG の行は土台の oss.tf

locals {
  telegraf_repository_url = try(data.terraform_remote_state.ecr.outputs.telegraf_repository_url, "")
  telegraf_image          = "${local.telegraf_repository_url}:${var.telegraf_image_tag}"
  telegraf_log_group      = "/ecs/${local.name_prefix}-telegraf"


  # NLB の受け口（どれも UDP）→ 渡すサービスとそのタスクのポート（app/telegraf/telegraf.conf.in の inputs.snmp_trap、app/syslog-ng/syslog-ng.conf.in の
  # s_device、GoFlow2 の -listen。cycle 012 で syslog を syslog-ng へ移し、NetFlow / sFlow を足し、MDT の 57000/tcp を外した）。
  # target group はキーごとに 1 つ（名前は <接頭辞>-<キー>）で、各サービスは自分の分だけを load_balancer に持つ
  collector_listeners = {
    trap    = { listener = 162, container = 1162, service = "telegraf-dialout" }
    syslog  = { listener = 5140, container = 5140, service = "syslog-ng" }
    netflow = { listener = 2055, container = 2055, service = "goflow2" }
    sflow   = { listener = 6343, container = 6343, service = "goflow2" }
  }
  # UDP は応答で生死を見られないので、サービスごとに別の口を見る
  collector_health_checks = {
    # Telegraf の outputs.health（telegraf.conf.in）
    "telegraf-dialout" = { protocol = "HTTP", port = "8080", path = "/" }
    # syslog の番号の tcp（syslog-ng.conf.in。つないで切るだけで、syslog-ng は何も書かない）
    "syslog-ng" = { protocol = "TCP", port = "5140", path = null }
    # GoFlow2 の -addr（collectors.tf）。/__health は 200 を返す（/ と /__ready は 404。v2.2.7 で確かめた）
    goflow2 = { protocol = "HTTP", port = "8081", path = "/__health" }
  }
}

data "aws_subnet" "telegraf" {
  id = local.telegraf_subnet_id
}

# ---------------------------------------------------------------- NLB
resource "aws_lb" "telegraf_dialout" {
  name               = "${local.name_prefix}-tg"
  internal           = true
  load_balancer_type = "network"
  subnets            = local.telegraf_dialout_subnet_ids
  # 2 AZ 以上では、サブネット a の受け口（lab の DNAT の宛先）に来たものも b / c のタスクへ振る（a のタスクが落ちても受ける）。
  # AZ をまたいだ分は転送料がかかる
  enable_cross_zone_load_balancing = var.telegraf_az_num > 1
  # NLB の SG は作るときにしか付けられない（後から足すと作り直し。付けて作った NLB なら入れ替えはできる）
  security_groups = [local.telegraf_dialout_nlb_sg_id]

  # 名前は 32 文字までなので -tg のまま（接頭辞が 22 文字まで）
  tags = { Name = "${local.name_prefix}-telegraf-dialout" }
}

# NLB のサブネット a のアドレス（NLB の ENI はサブネットごとに 1 つ。a のものだけ選ぶ）。lab.sh forward の DNAT の宛先
data "aws_network_interface" "telegraf_dialout_lb" {
  filter {
    name   = "description"
    values = ["ELB ${aws_lb.telegraf_dialout.arn_suffix}"]
  }
  filter {
    name   = "subnet-id"
    values = [local.telegraf_subnet_id]
  }
  filter {
    name   = "vpc-id"
    values = [local.vpc_id]
  }
}

# リソースの名前は telegraf_dialout のまま（syslog の target group と listener を作り直さない）。中身は 3 つのサービスの分
resource "aws_lb_target_group" "telegraf_dialout" {
  for_each = local.collector_listeners

  name        = "${local.name_prefix}-${each.key}"
  port        = each.value.container
  protocol    = "UDP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  # 送り元（機器の管理 IP）を残す
  preserve_client_ip = true
  # タスクを作り直すとき、古いタスクを長く待たない
  deregistration_delay = 10

  health_check {
    protocol            = local.collector_health_checks[each.value.service].protocol
    port                = local.collector_health_checks[each.value.service].port
    path                = local.collector_health_checks[each.value.service].path
    interval            = 10
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }

  tags = { Name = "${local.name_prefix}-telegraf-dialout-${each.key}" }
}

resource "aws_lb_listener" "telegraf_dialout" {
  for_each = local.collector_listeners

  load_balancer_arn = aws_lb.telegraf_dialout.arn
  port              = each.value.listener
  protocol          = "UDP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.telegraf_dialout[each.key].arn
  }
}

resource "aws_ssm_parameter" "telegraf_address" {
  name        = "/${local.name_prefix}/telegraf-address"
  type        = "String"
  value       = data.aws_network_interface.telegraf_dialout_lb.private_ip
  description = "Private IP of the collector NLB (Telegraf dial-out, syslog-ng, GoFlow2). Read by lab.sh forward on the lab EC2 (DNAT target)."
}

# 名前は Telegraf のときのまま（lab の EC2 の IAM と lab.sh forward が読む）。中身は gnmic のタスクのサブネット（gnmic.tf）
resource "aws_ssm_parameter" "telegraf_source_cidr" {
  name        = "/${local.name_prefix}/telegraf-source-cidr"
  type        = "String"
  value       = data.aws_subnet.telegraf.cidr_block
  description = "CIDR of the subnet of the gnmic task (its IP changes on every replacement). Read by lab.sh forward on the lab EC2 (gNMI subscription source)."
}

# ---------------------------------------------------------------- ECS
resource "aws_ecs_cluster" "telegraf" {
  name = "${local.name_prefix}-telegraf"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "telegraf" {
  name              = local.telegraf_log_group
  retention_in_days = var.log_retention_days
}

# ECS Exec（tg render で設定を見る）を使うので readonlyRootFilesystem は付けない（ECS Exec が対応していない）。telegraf.sh が書くのは /tmp だけ
resource "aws_ecs_task_definition" "telegraf_dialout" {
  family                   = "${local.name_prefix}-telegraf-dialout"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.telegraf_task_cpu
  memory                   = var.telegraf_task_memory
  execution_role_arn       = aws_iam_role.telegraf_execution.arn
  task_role_arn            = aws_iam_role.telegraf_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "telegraf"
      image     = local.telegraf_image
      essential = true
      portMappings = [
        { containerPort = 1162, protocol = "udp" }, # trap（NLB の 162 から）
        { containerPort = 8080, protocol = "tcp" }, # outputs.health（NLB のヘルスチェック）
      ]
      environment = concat(
        [
          { name = "AWS_REGION", value = var.region },
          { name = "KAFKA_BROKERS", value = local.kafka_bootstrap_brokers },
        ],
        local.kafka_client_environment,
      )
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.telegraf.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "dialout"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = local.telegraf_repository_url != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state から telegraf_repository_url が読めない。IaC/terraform/aws-managed/base/ecr を先に apply する（ops/up.sh の手順 1）。"
    }
  }
}

# 受ける側（dialout）。NLB の後ろ
resource "aws_ecs_service" "telegraf_dialout" {
  name            = "${local.name_prefix}-telegraf-dialout"
  cluster         = aws_ecs_cluster.telegraf.id
  task_definition = aws_ecs_task_definition.telegraf_dialout.arn
  # 機器から送ってくるものだけなので、増やしても同じものを 2 回書かない（NLB が振り分ける）。1 AZ に 1 つ（var.telegraf_az_num。
  # Fargate のサービスはタスクを AZ に散らして置く）
  desired_count = var.telegraf_az_num
  launch_type   = "FARGATE"

  # aws ecs execute-command でタスクの中に入れる（設定を見る程度。タスクは output telegraf_dialout_list_tasks_command で探し、--container telegraf）
  enable_execute_command = true

  # 新しいタスクが NLB のヘルスチェックを通ってから古いタスクを外す（入れ替えのあいだも trap を落とさない）
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = local.telegraf_dialout_subnet_ids
    security_groups  = [local.telegraf_dialout_sg_id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = { for k, v in local.collector_listeners : k => v if v.service == "telegraf-dialout" }
    content {
      target_group_arn = aws_lb_target_group.telegraf_dialout[load_balancer.key].arn
      container_name   = "telegraf"
      container_port   = load_balancer.value.container
    }
  }

  depends_on = [
    aws_lb_listener.telegraf_dialout,
    aws_iam_role_policy.telegraf_task,
    aws_iam_role_policy_attachment.telegraf_execution,
  ]
}

# ---------------------------------------------------------------- IAM
data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "telegraf_execution" {
  name               = "${local.name_prefix}-telegraf-exec"
  description        = "ECS task execution role of the Telegraf dial-out task (ECR pull, CloudWatch Logs)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "telegraf_execution" {
  role       = aws_iam_role.telegraf_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "telegraf_task" {
  name               = "${local.name_prefix}-telegraf-task"
  description        = local.kafka_descriptions.telegraf_task
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "telegraf_task" {
  name = "${local.name_prefix}-telegraf-task"
  role = aws_iam_role.telegraf_task.name

  # Kafka の権限は msk.tf（MSK の IAM）/ OSS 版の kafka.tf（認証なしなので無い）
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.telegraf_kafka_statements, [
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
    ])
  })
}

# IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない呼び出しを拒む）。実行ロールとタスクロールの両方に付ける
resource "aws_iam_role_policy_attachment" "telegraf_execution_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.telegraf_execution.name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "telegraf_task_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.telegraf_task.name
  policy_arn = local.perimeter_policy_arn
}
