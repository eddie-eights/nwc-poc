# ---------------------------------------------------------------- gnmic（ECS on Fargate、cycle 013）
# 機器の gNMI を subscribe して Kafka に書く（2026-10-08 のユーザー決定。それまでの telegraf-dialin の gNMI の subscribe と SNMP のポーリングを置き換える）。
# 状態（ON_CHANGE: interface_state・bgp_neighbor・isis_interface）は gnmi トピック、カウンター（SAMPLE: interface_stats・system）は metrics トピックへ、
# gnmic の event の形のまま書き、Spark が Telegraf の形に読み替える（app/spark/snmp_sinks.py の gnmic_message と read_rows）。設定は app/gnmic/gnmic.yaml.in。
# イメージは docker/images/gnmic/Dockerfile（公式の gnmic に設定のテンプレートと入口を足したもの）で、ops/up.sh が ECR の <接頭辞>-gnmic に置く。
# クラスタは Telegraf と同じ <接頭辞>-telegraf。NLB には付けない（こちらから取りにいくので、受け口が無い）。
# 機器の一覧を持ち、2 つ立てると同じ機器から 2 回取って Kafka に 2 回書くので 1 つ。いつもサブネット a に置く（lab.sh forward が通すのは
# このサブネットの CIDR。SSM の /<接頭辞>/telegraf-source-cidr。telegraf.tf）。
# ブローカー・認証・資格情報・実行ロールの権限は collectors.tf と同じ kafka_collector_* の locals（マネージド版は MSK の SASL/SCRAM の 9096、OSS 版は
# 認証なしの 9092）。マネージド版の SCRAM はコレクターごとに別のユーザー（User:syslog-ng / User:goflow2 / User:gnmic。cycle 031）で、User:gnmic には
# Kafka の ACL が要り、Spark のジョブが起動時に gnmi と metrics の WRITE・DESCRIBE を入れる（app/spark/snmp_sinks.py の ensure_acls）。入るまでに書いた値は落ちる（cycle 013 の design.md の未確定 7）。
# 機器の一覧と gNMI の資格情報は SSM パラメータから ECS の secrets で渡す（タスクを起こすときに読むので、変えたらサービスを作り直す）:
#   /<接頭辞>/gnmic/<出どころ>/gnmi-targets   String（"IP:57400", ...）。出どころは lab（var.gnmi_targets。Terraform が書く）か
#                     nautobot（var.gnmi_targets_from_nautobot。最初の値だけ Terraform が書き、あとは IaC/terraform/aws-managed/pipeline/nautobot の Job が書き換えて
#                     サービスを作り直す。Terraform は値の変化を見ない）
#   /<接頭辞>/gnmic/gnmi-username・gnmi-password   SecureString。ops/up.sh が作る（値を state に入れない）
# SG（gnmic）のルールは IaC/terraform/aws-managed/base/core の security_groups.tf（OSS 版は oss.tf）の通信の表: 何も受けない。管理ネットワークの tcp 57400、
# MSK の 9096（OSS 版は Kafka の 9092）、エンドポイント（SSM・Secrets Manager・ECR・CloudWatch Logs）へ送る。
# OSS 版（cycle 005。IaC/terraform/oss/pipeline/stream）はこのファイルをシンボリックリンクで使う

locals {
  gnmic_repository_url = try(data.terraform_remote_state.ecr.outputs.gnmic_repository_url, "")
  gnmic_log_group      = "/ecs/${local.name_prefix}-gnmic"

  # SSM パラメータ（上の注記）。機器の一覧は出どころでパスを分ける（切り替えると Terraform が古い方を消し、タスクの参照先も変わる）
  gnmic_parameter_prefix = "/${local.name_prefix}/gnmic"
  gnmic_target_source    = var.gnmi_targets_from_nautobot ? "nautobot" : "lab"
  gnmic_targets_name     = "${local.gnmic_parameter_prefix}/${local.gnmic_target_source}/gnmi-targets"
  # ops/up.sh の ensure_fixed_secret が作る SecureString（タスクの環境変数名 → パラメータの名前の最後）
  gnmic_credentials = { GNMI_USERNAME = "gnmi-username", GNMI_PASSWORD = "gnmi-password" }
  ssm_parameter_arn = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter"
}

resource "aws_cloudwatch_log_group" "gnmic" {
  name              = local.gnmic_log_group
  retention_in_days = var.log_retention_days
}

# 機器の一覧（lab から）。Terraform が値を持つ
resource "aws_ssm_parameter" "gnmic_targets_lab" {
  count = var.gnmi_targets_from_nautobot ? 0 : 1

  name        = local.gnmic_targets_name
  type        = "String"
  value       = var.gnmi_targets
  description = "gNMI targets of the gnmic task, from the lab definition (python3 app/containerlab/lab_topology.py app/containerlab --gnmi-targets). ECS secrets of the task."
}

# 機器の一覧（Nautobot から）。最初の値は lab から（Nautobot の最初の seed も lab なので同じ）で、あとは IaC/terraform/aws-managed/pipeline/nautobot の Job
# （app/nautobot/nb_sync.py）が書き換えてサービスを作り直す。Terraform は値の変化を見ない
resource "aws_ssm_parameter" "gnmic_targets_nautobot" {
  count = var.gnmi_targets_from_nautobot ? 1 : 0

  name        = local.gnmic_targets_name
  type        = "String"
  value       = var.gnmi_targets
  description = "gNMI targets of the gnmic task, written by the Nautobot job (IaC/terraform/aws-managed/pipeline/nautobot). ECS secrets of the task."

  lifecycle {
    ignore_changes = [value]
  }
}

# ECS Exec（gn render / gn get）を使うので readonlyRootFilesystem は付けない（ECS Exec が対応していない）。gnmic.sh が書くのは /tmp だけ
resource "aws_ecs_task_definition" "gnmic" {
  family                   = "${local.name_prefix}-gnmic"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.gnmic_execution.arn
  task_role_arn            = aws_iam_role.gnmic_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # 受け口は無い（portMappings なし）
  container_definitions = jsonencode([
    {
      name      = "gnmic"
      image     = "${local.gnmic_repository_url}:${var.gnmic_image_tag}"
      essential = true
      environment = [
        { name = "KAFKA_BROKERS", value = local.kafka_collector_brokers },
        { name = "KAFKA_AUTH", value = local.kafka_collector_auth },
      ]
      # Kafka の SCRAM の資格情報（マネージド版だけ）と、機器の一覧（String）と gNMI の資格情報（SecureString）。
      # gnmic.sh が GNMI_TARGETS を埋め、資格情報は gnmic が設定を読むときに ${…} を展開する（/tmp/gnmic.yaml に値を書かない）
      secrets = concat(
        local.kafka_collector_secrets["gnmic"],
        [{ name = "GNMI_TARGETS", valueFrom = "${local.ssm_parameter_arn}${local.gnmic_targets_name}" }],
        [for env, leaf in local.gnmic_credentials : { name = env, valueFrom = "${local.ssm_parameter_arn}${local.gnmic_parameter_prefix}/${leaf}" }],
      )
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.gnmic.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "gnmic"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = local.gnmic_repository_url != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state から gnmic_repository_url が読めない（cycle 013 より前の ECR）。IaC/terraform/aws-managed/base/ecr を先に apply する（ops/up.sh の手順 1）。"
    }
  }
}

# NLB に付けない。telegraf_az_num に従わず、いつもサブネット a に 1 つ。IaC/terraform/aws-managed/pipeline/nautobot の Job が機器の一覧を書き換えたあと、
# このサービスを作り直す（force-new-deployment）
resource "aws_ecs_service" "gnmic" {
  name            = "${local.name_prefix}-gnmic"
  cluster         = aws_ecs_cluster.telegraf.id
  task_definition = aws_ecs_task_definition.gnmic.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # aws ecs execute-command でタスクの中に入れる（gn render / gn get。コマンドは output gnmic_exec_command）
  enable_execute_command = true

  # 2 つ同時に立てない（同じ機器を 2 回 subscribe して Kafka に 2 回書かない）。入れ替えでは古いタスクを止めてから新しいタスクを起こす。
  # TELEGRAF_AZ_NUM に従わない理由も同じ: どのタスクも機器の一覧の全部を取りにいき、タスクのあいだで機器を分け合う仕組みを使っていない
  # （gnmic の clustering は Consul などの locker が要る）。コードから確かめた理由で、AWS では未確認（2026-10-04）。
  # 2026-10-09 に取りにいく側の Telegraf（telegraf_dialin）から引き継いだ
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = [local.telegraf_subnet_id]
    security_groups  = [local.gnmic_sg_id]
    assign_public_ip = false
  }

  lifecycle {
    precondition {
      condition     = local.gnmic_sg_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に gnmic の SG が無い（cycle 013 より前の土台）。IaC/terraform/aws-managed/base/core を先に apply する（ops/up.sh なら手順 1 で apply される）。"
    }
  }

  depends_on = [
    aws_iam_role_policy.gnmic_task,
    aws_iam_role_policy.gnmic_execution,
    aws_iam_role_policy_attachment.gnmic_execution,
    aws_ssm_parameter.gnmic_targets_lab,
    aws_ssm_parameter.gnmic_targets_nautobot,
  ]
}

# ---------------------------------------------------------------- IAM
# 実行ロール: ECR・CloudWatch Logs と、SSM の機器の一覧と gNMI の資格情報（AWS 管理の aws/ssm キーなので kms:Decrypt は要らない）、
# マネージド版では SCRAM の secret（kafka_collector_execution_statements。OSS 版は空）
resource "aws_iam_role" "gnmic_execution" {
  name               = "${local.name_prefix}-gnmic-exec"
  description        = "ECS task execution role of the gnmic task (ECR pull, CloudWatch Logs, gNMI targets and credentials from SSM, MSK SCRAM secret in the managed build)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "gnmic_execution" {
  role       = aws_iam_role.gnmic_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "gnmic_execution" {
  name = "${local.name_prefix}-gnmic-exec"
  role = aws_iam_role.gnmic_execution.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [{
        Sid      = "GnmicParameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameters"]
        Resource = "${local.ssm_parameter_arn}${local.gnmic_parameter_prefix}/*"
      }],
      local.kafka_collector_execution_statements,
    )
  })
}

# タスクロール: ECS Exec だけ（Kafka へは SCRAM か認証なしなので、IAM の Kafka の権限は要らない。collectors.tf の collector_task_policy）
resource "aws_iam_role" "gnmic_task" {
  name               = "${local.name_prefix}-gnmic-task"
  description        = "gnmic task - write gNMI subscriptions to Kafka (MSK SASL/SCRAM or plain Kafka), ECS Exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "gnmic_task" {
  name   = "${local.name_prefix}-gnmic-task"
  role   = aws_iam_role.gnmic_task.name
  policy = local.collector_task_policy
}

# IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない呼び出しを拒む）。実行ロールとタスクロールの両方に付ける
resource "aws_iam_role_policy_attachment" "gnmic_execution_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.gnmic_execution.name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "gnmic_task_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.gnmic_task.name
  policy_arn = local.perimeter_policy_arn
}
