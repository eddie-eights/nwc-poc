# nwc-oss - PIPELINE graph root module of the OSS build (cycle 005). The managed build (IaC/terraform/aws-managed/pipeline/graph) uses Neptune Analytics
# (its neptune.tf); this one runs Neo4j Community Edition with the Graph Data Science library as one ECS task on Fargate instead.
# locals.tf and the variables are the managed build's files through a symbolic link. access.tf, sync.tf and outputs.tf are real files
# here: they name the Neptune graph in the managed build (IAM statements, NEPTUNE_GRAPH_ID), and the Neo4j build passes a URI and a password instead.

# ---------------------------------------------------------------- Neo4j（ECS on Fargate、1 台）
# イメージはリポジトリの app/neo4j/（公式の neo4j:<版>-community に GDS の jar を焼き込んだもの。OSS 版の ops/up.sh がビルドして ECR の <接頭辞>-neo4j に置く）。
# データはタスクの一時領域（Fargate のエフェメラルストレージ）。EFS は使わない（Neo4j は NFS の上のデータを支えない。設計 005）。
# タスクが入れ替わる（terraform apply でタスク定義が変わる・タスクが落ちる）とグラフは空に戻るので、ops/sync-graph.sh --oss で入れ直し、
# そのあと Nautobot の Job「Telegraf とグラフ DB に同期」で変更履歴を戻す（status は全部 UP に戻る。docs/oss-variant.md の「Neo4j を起こし直したあとの戻し方」）。頂点の id はプロパティ id で、一意制約はアプリ（app/agentcore/graph.py の _neo4j_schema）が最初のクエリの前に作る。
# 届くのは SG で絞った相手だけ（IaC/terraform/aws-managed/base/core の oss.tf の通信の表: Web・Runtime・Lambda・Worker・Nautobot → 7687、Web → 7474）。
# 名前は Cloud Map の neo4j.<接頭辞>-graph.internal。アプリは bolt://（ルーティングしない直結。1 台なので要らない）でつなぐ。
# 認証はユーザー neo4j とパスワード。パスワードは OSS 版の ops/up.sh が SSM の /<接頭辞>/neo4j-password（SecureString）に 1 回だけ作り、
# ECS の secrets で渡す（state にも環境変数の値にも書かない）。8 文字以上で / を含まないこと（公式の entrypoint の決まり。app/neo4j/entrypoint.sh）。

variable "neo4j_image_tag" {
  description = "Tag of the Neo4j image in the <prefix>-neo4j repository (built from app/neo4j/ by the OSS ops/up.sh: <Neo4j version>-<hash of app/neo4j/ and docker/images/neo4j/Dockerfile>). Same Neo4j version as oss/compose."
  type        = string
  default     = "2026.09.0"
}

variable "neo4j_task_cpu" {
  description = "Fargate CPU units of the Neo4j task (ARM64). GDS on Community Edition runs its algorithms on at most 4 cores."
  type        = number
  default     = 1024

  validation {
    condition     = contains([1024, 2048, 4096], var.neo4j_task_cpu)
    error_message = "neo4j_task_cpu must be 1024, 2048 or 4096."
  }
}

variable "neo4j_task_memory" {
  description = "Fargate memory (MiB) of the Neo4j task. 40% is the JVM heap (GDS projects the graph there), 25% the page cache, the rest is left to the OS and the native memory. Must be a valid pair with neo4j_task_cpu (1024 takes 2048-8192)."
  type        = number
  default     = 4096

  validation {
    condition     = contains([2048, 4096, 8192, 16384], var.neo4j_task_memory)
    error_message = "neo4j_task_memory must be 2048, 4096, 8192 or 16384."
  }
}

data "terraform_remote_state" "ecr" {
  backend = "local"

  config = {
    path = "${path.module}/../../base/ecr/terraform.tfstate"
  }
}

locals {
  neo4j_image     = "${try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["neo4j"], "")}:${var.neo4j_image_tag}"
  neo4j_log_group = "/ecs/${local.name_prefix}-neo4j"
  # 土台（IaC/terraform/aws-managed/base/core の oss.tf）の SG。マネージド版の土台や古い state では無いので try にして、precondition で止める
  neo4j_sg_id = try(data.terraform_remote_state.main.outputs.security_group_ids["neo4j"], "")
  # 踏み台（Neo4j Browser のポートフォワード。outputs.tf）
  web_instance_id = try(data.terraform_remote_state.main.outputs.web_instance_id, "")

  ssm_parameter_arn        = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter"
  neo4j_password_parameter = "/${local.name_prefix}/neo4j-password"
  neo4j_password_arn       = "${local.ssm_parameter_arn}${local.neo4j_password_parameter}"

  graph_service_namespace = "${local.name_prefix}-graph.internal"
  neo4j_host              = "neo4j.${local.graph_service_namespace}"
  # app/agentcore/graph.py と app/temporal/awsio.py の NEO4J_URI（Web と Runtime は SSM の /<接頭辞>/neo4j-uri、Lambda と Worker は環境変数）
  neo4j_uri = "bolt://${local.neo4j_host}:7687"

  # 公式イメージは NEO4J_ で始まる環境変数を neo4j.conf に書き写す（NEO4J_server_memory_heap_max__size → server.memory.heap.max_size）。
  # 知らない名前を書くと起動しない（server.config.strict_validation.enabled）。ヒープは初期値と最大を同じにする（Neo4j の勧め）
  neo4j_heap_mb      = floor(var.neo4j_task_memory * 2 / 5)
  neo4j_pagecache_mb = floor(var.neo4j_task_memory / 4)
  neo4j_environment = [
    { name = "NEO4J_server_memory_heap_initial__size", value = "${local.neo4j_heap_mb}m" },
    { name = "NEO4J_server_memory_heap_max__size", value = "${local.neo4j_heap_mb}m" },
    { name = "NEO4J_server_memory_pagecache_size", value = "${local.neo4j_pagecache_mb}m" },
    # 閉域から外へ利用状況を送ろうとしない
    { name = "NEO4J_dbms_usage__report_enabled", value = "false" },
  ]
}

resource "aws_ecs_cluster" "graph" {
  name = "${local.name_prefix}-graph"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "neo4j" {
  name              = local.neo4j_log_group
  retention_in_days = var.log_retention_days
}

resource "aws_service_discovery_private_dns_namespace" "graph" {
  name        = local.graph_service_namespace
  description = "Neo4j of ${local.name_prefix} (IaC/terraform/oss/pipeline/graph)"
  vpc         = local.vpc_id
}

resource "aws_service_discovery_service" "neo4j" {
  name = "neo4j"
  # タスクの登録が残っていても destroy できるようにする
  force_destroy = true

  # タスクを作り直すと IP が変わる。TTL を短くして、アプリが早く新しい IP を引くようにする
  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.graph.id
    routing_policy = "MULTIVALUE"

    dns_records {
      ttl  = 10
      type = "A"
    }
  }
}

# ヘルスチェックは Bolt（7687）が開いているかだけを見る（ECS の healthStatus で、起動してからアプリがつなげるまでが見える）。
# UNHEALTHY になると ECS はタスクを入れ替え、データ（一時領域）は消える。そのため重い GDS や GC で数分詰まっても入れ替えないよう、
# 30 秒おきに 10 回続けて（5 分）開かないときだけ UNHEALTHY にする。起動の 3 分は数えない
resource "aws_ecs_task_definition" "neo4j" {
  family                   = "${local.name_prefix}-neo4j"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.neo4j_task_cpu
  memory                   = var.neo4j_task_memory
  execution_role_arn       = aws_iam_role.neo4j_execution.arn
  task_role_arn            = aws_iam_role.neo4j_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # host も EFS も書かないボリュームは、タスクのエフェメラルストレージ（既定 20 GiB）に置かれ、タスクと一緒に消える
  volume {
    name = "data"
  }

  container_definitions = jsonencode([
    {
      name         = "neo4j"
      image        = local.neo4j_image
      essential    = true
      portMappings = [{ containerPort = 7687, protocol = "tcp" }, { containerPort = 7474, protocol = "tcp" }]
      environment  = local.neo4j_environment
      # NEO4J_ で始まらない名前で渡す（NEO4J_ で始まると neo4j.conf に平文で書かれ、知らない設定として起動も止まる）。
      # イメージの入口（app/neo4j/entrypoint.sh）が NEO4J_AUTH=neo4j/<パスワード> に直して消す
      secrets     = [{ name = "GRAPH_PASSWORD", valueFrom = local.neo4j_password_arn }]
      mountPoints = [{ sourceVolume = "data", containerPath = "/data", readOnly = false }]
      # 公式イメージ（Debian）に curl と nc は無く、bash と wget はある。2026-10-08 に neo4j:2026.09.0-community で、起動前は失敗し起動後に通るのを確かめた
      healthCheck = {
        command     = ["CMD", "bash", "-c", "</dev/tcp/127.0.0.1/7687"]
        interval    = 30
        timeout     = 5
        retries     = 10
        startPeriod = 180
      }
      # 止めるときにトランザクションログを閉じる時間（Fargate の上限）
      stopTimeout = 120
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.neo4j.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "neo4j"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["neo4j"], "") != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state に neo4j のリポジトリが無い（project = nwc-oss でない ECR か、古い ECR）。IaC/terraform/oss/base/ecr を先に apply する。"
    }
  }
}

resource "aws_ecs_service" "neo4j" {
  name            = "${local.name_prefix}-neo4j"
  cluster         = aws_ecs_cluster.graph.id
  task_definition = aws_ecs_task_definition.neo4j.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # aws ecs execute-command でタスクの中に入れる（cypher-shell を打つ）
  enable_execute_command = true

  # 2 台を同時に立てない（Cloud Map の名前が 2 つの IP を返し、アプリが空のほうにつなぐことがある）。入れ替えは止めてから起こす
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  # サブネット a（1 台。データはタスクの中にしか無いので AZ を分けても戻らない）
  network_configuration {
    subnets          = [local.subnet_ids[0]]
    security_groups  = [local.neo4j_sg_id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.neo4j.arn
  }

  lifecycle {
    precondition {
      condition     = local.neo4j_sg_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に neo4j の SG が無い（マネージド版の土台か、oss.tf より前の土台）。IaC/terraform/oss/base/core を先に apply する。"
    }
  }

  depends_on = [
    aws_iam_role_policy.neo4j_task,
    aws_iam_role_policy.neo4j_execution,
    aws_iam_role_policy_attachment.neo4j_execution,
  ]
}

# Web と Runtime の app/agentcore/graph.py は NEO4J_URI を SSM の <PARAM_PREFIX>/neo4j-uri から読む（マネージド版の neptune-graph-id の代わり。
# 読む権限は access.tf）。Lambda と Worker には環境変数で渡す
resource "aws_ssm_parameter" "neo4j_uri" {
  name        = "/${local.name_prefix}/neo4j-uri"
  type        = "String"
  value       = local.neo4j_uri
  description = "Bolt URI of the Neo4j task for the chat runtime and the web (IaC/terraform/oss/pipeline/graph)"
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

resource "aws_iam_role" "neo4j_execution" {
  name               = "${local.name_prefix}-neo4j-exec"
  description        = "ECS task execution role of the Neo4j task (ECR pull, CloudWatch Logs, the password from SSM)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "neo4j_execution" {
  role       = aws_iam_role.neo4j_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# secrets の GRAPH_PASSWORD（SecureString でも AWS 管理の aws/ssm キーなので kms:Decrypt は要らない）
resource "aws_iam_role_policy" "neo4j_execution" {
  name = "${local.name_prefix}-neo4j-exec"
  role = aws_iam_role.neo4j_execution.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "Password"
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = local.neo4j_password_arn
    }]
  })
}

resource "aws_iam_role" "neo4j_task" {
  name               = "${local.name_prefix}-neo4j-task"
  description        = "Neo4j task role - ECS Exec only (the database calls no AWS API)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "neo4j_task" {
  name = "${local.name_prefix}-neo4j-task"
  role = aws_iam_role.neo4j_task.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
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
    }]
  })
}

# IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない呼び出しを拒む）。実行ロールとタスクロールの両方に付ける
resource "aws_iam_role_policy_attachment" "neo4j_execution_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.neo4j_execution.name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "neo4j_task_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.neo4j_task.name
  policy_arn = local.perimeter_policy_arn
}
