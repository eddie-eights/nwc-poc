# ---------------------------------------------------------------- Splunk Enterprise（ECS on Fargate）
# sinks に splunk があるとき、Splunk の公式イメージ（splunk/splunk）にアラートの app を足したもの（splunk/Dockerfile）を 1 タスク立て、
# Spark の splunk の格納先が VPC の中の HEC（https://splunk.<名前空間>:8088）へ書く。AWS の外へは出ない（2026-09-28 まであった外の Splunk へ NAT で出る道はやめた）。
# イメージは amd64 しか無いので X86_64 のタスクにし、ops/up.sh の手順 2 が ECR の <接頭辞>-splunk に入れる（VPC から AWS の外へ出る経路が無いので Docker Hub から引けない）。
# 検知は app netops_alerts の保存済みサーチ（リンク・BGP・IS-IS・trap）で、アラートアクション netops_sns が土台の SNS トピック
# （terraform/base/core の alerts.tf）へ publish する。認証はタスクロール（アクセスキーは置かない）で、sns のエンドポイントを通る。
# ライセンスは Splunk Enterprise の試用（60 日、1 日 500 MB まで）。SPLUNK_START_ARGS / SPLUNK_GENERAL_TERMS で起動時に Splunk の
# ライセンスと Splunk General Terms に同意する（イメージがこの 2 つ無しでは起きない）ので、デプロイする人が同意したことになる。
# index はタスクのエフェメラルストレージにあり、タスクと一緒に消える（PoC。残すなら EFS が要る）。
# 管理者のパスワードと HEC の token は ops/up.sh が作る SSM の SecureString を secrets で受ける（値は Terraform も state も持たない）。
# UI は output splunk_port_forward_command（web の EC2 を踏み台にした SSM のポートフォワード。PoC 用）
#
# var.splunk_az_num が 2 か 3 のときはクラスター（Splunk をクラスターにする（004）。docs/cycles/004-splunk-indexer-cluster/design.md）:
#   - cluster manager 1（splunk-cm）: indexer の登録と複製の指図
#   - indexer が AZ ごとに 1（splunk-idx）: HEC で受け、互いに複製を送る。複製の数と検索できる複製の数はどちらも indexer の数
#   - search head 1（いまの splunk のサービス）: 保存済みサーチと UI。indexer を検索する
# 役割は上流の入口（docker-splunk）の SPLUNK_ROLE で分け、同じイメージを使う。保存済みサーチは search head だけで動く（splunk/entrypoint.sh）。
# indexer が入れ替わって前と同じ IP をもらうと search head が古い GUID のままになるので、search head のヘルスチェックに
# 突き合わせ（splunk/peers_check.py）を足し、食い違いが続けば ECS に search head を入れ替えさせる

locals {
  splunk_image        = "${try(data.terraform_remote_state.ecr.outputs.splunk_repository_url, "")}:${var.splunk_image_tag}"
  splunk_log_group    = "/ecs/${local.name_prefix}-splunk"
  splunk_password_arn = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.splunk_password_parameter}"

  # どの役割のタスクも同じ値を受ける
  splunk_secrets = [
    { name = "SPLUNK_PASSWORD", valueFrom = local.splunk_password_arn },
    # イメージがこの値で HEC の token（既定の index は main）を作る。Spark は同じ SSM の値を読んで送る
    { name = "SPLUNK_HEC_TOKEN", valueFrom = local.splunk_token_parameter_arn },
  ]
  splunk_health_check = {
    interval    = 30
    timeout     = 10
    retries     = 10
    startPeriod = 300
  }

  splunk_cluster = var.splunk_az_num > 1
  # manager と indexer と search head が互いを確かめる合言葉（pass4SymmKey）。ops/up.sh が乱数で作る SecureString
  splunk_idxc_secret_arn = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/${local.name_prefix}/splunk/idxc-secret"
  # 上流の入口が読む。manager は名前だけ渡す（https:// と 8089 は上流が足す）
  splunk_cluster_environment = [
    { name = "SPLUNK_CLUSTER_MASTER_URL", value = "splunk-cm.${local.service_namespace}" },
    { name = "SPLUNK_IDXC_REPLICATION_FACTOR", value = tostring(var.splunk_az_num) },
    { name = "SPLUNK_IDXC_SEARCH_FACTOR", value = tostring(var.splunk_az_num) },
  ]
  splunk_cluster_secrets = [
    { name = "SPLUNK_IDXC_PASS4SYMMKEY", valueFrom = local.splunk_idxc_secret_arn },
  ]
  # manager と indexer のコンテナ（役割の SPLUNK_ROLE とログの接頭辞は各タスク定義で足す）。保存済みサーチは動かさないので、
  # SNS の変数とタスクロールは要らない
  splunk_cluster_container = {
    name      = "splunk"
    image     = local.splunk_image
    essential = true
    portMappings = [
      { containerPort = 8000, protocol = "tcp" }, # Web UI（manager の画面は output splunk_cm_port_forward_command）
      { containerPort = 8088, protocol = "tcp" }, # HEC（indexer）
      { containerPort = 8089, protocol = "tcp" }, # 管理 API と検索
      { containerPort = 9887, protocol = "tcp" }, # 複製（indexer どうし）
      { containerPort = 9997, protocol = "tcp" }, # 転送の受け口（indexer。search head と manager が自分の _internal と _audit を送る）
    ]
    secrets     = concat(local.splunk_secrets, local.splunk_cluster_secrets)
    healthCheck = merge({ command = ["CMD-SHELL", "/sbin/checkstate.sh"] }, local.splunk_health_check)
  }
}

resource "aws_cloudwatch_log_group" "splunk" {
  count = local.splunk_on_ecs ? 1 : 0

  name              = local.splunk_log_group
  retention_in_days = var.log_retention_days
}

resource "aws_service_discovery_service" "splunk" {
  count = local.splunk_on_ecs ? 1 : 0

  name          = "splunk"
  force_destroy = true

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.analytics[0].id
    routing_policy = "MULTIVALUE"

    dns_records {
      ttl  = 10
      type = "A"
    }
  }
}

resource "aws_ecs_task_definition" "splunk" {
  count = local.splunk_on_ecs ? 1 : 0

  family                   = "${local.name_prefix}-splunk"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.splunk_task_cpu
  memory                   = var.splunk_task_memory
  execution_role_arn       = aws_iam_role.splunk_execution[0].arn
  # アラートアクション（splunk/netops_alerts/bin/netops_sns.py）が SNS へ publish する
  task_role_arn = aws_iam_role.splunk_task[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  ephemeral_storage {
    size_in_gib = var.splunk_ephemeral_storage_gib
  }

  container_definitions = jsonencode([
    {
      name      = "splunk"
      image     = local.splunk_image
      essential = true
      portMappings = [
        { containerPort = 8000, protocol = "tcp" }, # Web UI（http）
        { containerPort = 8088, protocol = "tcp" }, # HEC（https、イメージの自己署名）
        { containerPort = 8089, protocol = "tcp" }, # 管理 API（https）
      ]
      environment = concat([
        { name = "SPLUNK_START_ARGS", value = "--accept-license" },
        { name = "SPLUNK_GENERAL_TERMS", value = "--accept-sgt-current-at-splunk-com" },
        # アラートアクションが読む（splunk/entrypoint.sh がファイルに写す。splunkd の子プロセスはコンテナの環境変数を引き継がない）
        { name = "AWS_REGION", value = var.region },
        { name = "ALERTS_TOPIC_ARN", value = local.alerts_topic_arn },
        # gNMI と trap のイベントは機器の名前でなく管理 IP を持つ。アラートアクションがこの表で名前に直す
        { name = "DEVICE_MAP", value = var.device_map },
        ], local.splunk_cluster ? concat([
          { name = "SPLUNK_ROLE", value = "splunk_search_head" },
      ], local.splunk_cluster_environment) : [])
      secrets = concat(local.splunk_secrets, local.splunk_cluster ? local.splunk_cluster_secrets : [])
      # 起動（Ansible での初期設定）に数分かかる。ops/up.sh はこれが HEALTHY になってから Spark のジョブを出す
      # （ジョブの HEC への POST は再試行の後に落ちるので、Splunk が起きる前に出すとジョブが止まる）。
      # クラスターのときは突き合わせ（splunk/peers_check.py）も足す。食い違いが retries の回数（約 5 分）続くと ECS が入れ替える
      healthCheck = merge({
        command = ["CMD-SHELL", local.splunk_cluster ? "/sbin/checkstate.sh && /sbin/nwc-peers-check.py" : "/sbin/checkstate.sh"]
      }, local.splunk_health_check)
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.splunk[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "splunk"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.splunk_repository_url, "") != ""
      error_message = "terraform/base/ecr の state から splunk_repository_url が読めない。terraform/base/ecr を先に apply する（ops/up.sh の手順 1）。"
    }
    precondition {
      condition     = local.alerts_topic_arn != ""
      error_message = "terraform/base/core の state から alerts_topic_arn が読めない（2026-10-02 より前の土台）。terraform/base/core を先に apply する。"
    }
  }
}

resource "aws_ecs_service" "splunk" {
  count = local.splunk_on_ecs ? 1 : 0

  name            = "${local.name_prefix}-splunk"
  cluster         = aws_ecs_cluster.analytics[0].id
  task_definition = aws_ecs_task_definition.splunk[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # index はタスクの中にしか無いので 2 つ同時に立てない
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = [local.instance_subnet_id]
    security_groups  = [local.splunk_sg_id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.splunk[0].arn
  }

  depends_on = [
    aws_iam_role_policy.splunk_execution,
    aws_iam_role_policy_attachment.splunk_execution,
    aws_iam_role_policy.splunk_task,
  ]
}

# ---------------------------------------------------------------- クラスターの manager と indexer（var.splunk_az_num が 2 か 3 のときだけ）
resource "aws_service_discovery_service" "splunk_cm" {
  count = local.splunk_on_ecs && local.splunk_cluster ? 1 : 0

  name          = "splunk-cm"
  force_destroy = true

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.analytics[0].id
    routing_policy = "MULTIVALUE"

    dns_records {
      ttl  = 10
      type = "A"
    }
  }
}

# HEC の宛先（local.splunk_hec_url）。ECS のヘルスチェックが HEALTHY でない indexer（起動中・クラスターに入る前）を DNS から外す
resource "aws_service_discovery_service" "splunk_idx" {
  count = local.splunk_on_ecs && local.splunk_cluster ? 1 : 0

  name          = "splunk-idx"
  force_destroy = true

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.analytics[0].id
    routing_policy = "MULTIVALUE"

    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  # 中身は書かない（failure_threshold は AWS が受けなくなり、いつも 1）
  health_check_custom_config {}
}

resource "aws_ecs_task_definition" "splunk_cm" {
  count = local.splunk_on_ecs && local.splunk_cluster ? 1 : 0

  family                   = "${local.name_prefix}-splunk-cm"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.splunk_task_cpu
  memory                   = var.splunk_task_memory
  execution_role_arn       = aws_iam_role.splunk_execution[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  ephemeral_storage {
    size_in_gib = var.splunk_ephemeral_storage_gib
  }

  container_definitions = jsonencode([merge(local.splunk_cluster_container, {
    environment = concat([
      { name = "SPLUNK_START_ARGS", value = "--accept-license" },
      { name = "SPLUNK_GENERAL_TERMS", value = "--accept-sgt-current-at-splunk-com" },
      { name = "SPLUNK_ROLE", value = "splunk_cluster_master" },
    ], local.splunk_cluster_environment)
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.splunk[0].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "splunk-cm"
      }
    }
  })])
}

resource "aws_ecs_task_definition" "splunk_idx" {
  count = local.splunk_on_ecs && local.splunk_cluster ? 1 : 0

  family                   = "${local.name_prefix}-splunk-idx"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.splunk_task_cpu
  memory                   = var.splunk_task_memory
  execution_role_arn       = aws_iam_role.splunk_execution[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  ephemeral_storage {
    size_in_gib = var.splunk_ephemeral_storage_gib
  }

  container_definitions = jsonencode([merge(local.splunk_cluster_container, {
    environment = concat([
      { name = "SPLUNK_START_ARGS", value = "--accept-license" },
      { name = "SPLUNK_GENERAL_TERMS", value = "--accept-sgt-current-at-splunk-com" },
      { name = "SPLUNK_ROLE", value = "splunk_indexer" },
    ], local.splunk_cluster_environment)
    # 止まるまでに手元で 47 秒かかった（Fargate の既定 30 秒では途中で強制終了になる）
    stopTimeout = 120
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.splunk[0].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "splunk-idx"
      }
    }
  })])
}

resource "aws_ecs_service" "splunk_cm" {
  count = local.splunk_on_ecs && local.splunk_cluster ? 1 : 0

  name            = "${local.name_prefix}-splunk-cm"
  cluster         = aws_ecs_cluster.analytics[0].id
  task_definition = aws_ecs_task_definition.splunk_cm[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = [local.instance_subnet_id]
    security_groups  = [local.splunk_sg_id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.splunk_cm[0].arn
  }

  depends_on = [
    aws_iam_role_policy.splunk_execution,
    aws_iam_role_policy_attachment.splunk_execution,
  ]
}

resource "aws_ecs_service" "splunk_idx" {
  count = local.splunk_on_ecs && local.splunk_cluster ? 1 : 0

  name            = "${local.name_prefix}-splunk-idx"
  cluster         = aws_ecs_cluster.analytics[0].id
  task_definition = aws_ecs_task_definition.splunk_idx[0].arn
  # AZ ごとに 1 つ（subnets a, b, c の前から splunk_az_num 個）。Fargate のサービスはタスクを AZ に散らして置くが、保証はしない
  desired_count = var.splunk_az_num
  launch_type   = "FARGATE"

  # 入れ替えは 1 台ずつ（止めてから立てる。index はタスクの中にあり、同時に 2 台を止めると複製が足りなくなる）
  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 100
  # AZ の偏りを直す機能は maximumPercent 100 と一緒に使えない
  availability_zone_rebalancing = "DISABLED"

  network_configuration {
    subnets          = slice(local.subnet_ids, 0, var.splunk_az_num)
    security_groups  = [local.splunk_sg_id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.splunk_idx[0].arn
  }

  depends_on = [
    aws_iam_role_policy.splunk_execution,
    aws_iam_role_policy_attachment.splunk_execution,
  ]
}

# ---------------------------------------------------------------- IAM
resource "aws_iam_role" "splunk_execution" {
  count = local.splunk_on_ecs ? 1 : 0

  name               = "${local.name_prefix}-splunk-exec"
  description        = "ECS task execution role of the Splunk task (ECR pull, CloudWatch Logs, admin password and HEC token from SSM)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "splunk_execution" {
  count = local.splunk_on_ecs ? 1 : 0

  role       = aws_iam_role.splunk_execution[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# secrets の SecureString（AWS 管理の aws/ssm キーなので kms:Decrypt は要らない）
resource "aws_iam_role_policy" "splunk_execution" {
  count = local.splunk_on_ecs ? 1 : 0

  name = "${local.name_prefix}-splunk-exec"
  role = aws_iam_role.splunk_execution[0].name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "Secrets"
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = concat([local.splunk_password_arn, local.splunk_token_parameter_arn], local.splunk_cluster ? [local.splunk_idxc_secret_arn] : [])
    }]
  })
}

resource "aws_iam_role_policy_attachment" "splunk_execution_perimeter" {
  count = local.splunk_on_ecs && local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.splunk_execution[0].name
  policy_arn = local.perimeter_policy_arn
}

# アラートアクションのロール。できるのは土台のトピックへの publish だけ（トピックの鍵は AWS 管理の aws/sns なので kms の許可は要らない）
resource "aws_iam_role" "splunk_task" {
  count = local.splunk_on_ecs ? 1 : 0

  name               = "${local.name_prefix}-splunk-task"
  description        = "Splunk task - the netops_sns alert action publishes alerts to the SNS topic"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "splunk_task" {
  count = local.splunk_on_ecs ? 1 : 0

  name = "${local.name_prefix}-splunk-task"
  role = aws_iam_role.splunk_task[0].name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "PublishAlerts"
      Effect   = "Allow"
      Action   = ["sns:Publish"]
      Resource = local.alerts_topic_arn
    }]
  })
}

resource "aws_iam_role_policy_attachment" "splunk_task_perimeter" {
  count = local.splunk_on_ecs && local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.splunk_task[0].name
  policy_arn = local.perimeter_policy_arn
}
