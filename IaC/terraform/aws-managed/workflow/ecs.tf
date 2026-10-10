# ---------------------------------------------------------------- network
# タスクの SG は IaC/terraform/aws-managed/base/core の workflow。受信は Web の EC2 からの Temporal UI の 8233（SSM のポートフォワーディング。docs/workflow.md
# 「Temporal UI を開く」）だけ、送信はエンドポイント（Neptune Analytics もここ）と S3 の 443、Nautobot の RDS の 5432（Temporal の履歴。security_groups.tf の通信の表）。
# Temporal の gRPC（7233〜7239）と membership（6933〜6939）はタスクの外に出さない（portMappings に無い。SG は workflow → workflow の自分宛てだけ）。
# ECR / logs / SSM / AgentCore / SQS / s3tables へは IaC/terraform/aws-managed/base/core のインターフェース型エンドポイント（ops/up.sh が WORKFLOW のときに作らせる）を通る。
# 2026-09-26 まではここにタスクの SG と 7 本のルールがあった（7c42b0f）

# ---------------------------------------------------------------- cluster / logs
resource "aws_ecs_cluster" "workflow" {
  name = "${local.name_prefix}-workflow"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "workflow" {
  name              = local.log_group
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------- task definition (Temporal server + UI + worker in one task)
resource "aws_ecs_task_definition" "workflow" {
  family                   = "${local.name_prefix}-workflow"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "temporal"
      image     = local.temporal_image
      essential = true
      # docker/images/temporal-server/（temporalio/server + temporal-sql-tool + psql）。entrypoint が起動のたびに master（nautobot）でロール temporal と
      # DB temporal / temporal_visibility を作り（あれば何もしない）、スキーマを最新まで上げ、namespace default を背景で作ってから temporal-server を起こす。
      # 履歴は Nautobot の RDS for PostgreSQL に残る（cycle 036。2026-10-10 までは CLI の開発用サーバーで、履歴はコンテナの中のファイルにあった）。portMappings は無し
      environment = [
        { name = "DB", value = "postgres12" },
        { name = "POSTGRES_SEEDS", value = local.nautobot_db_address },
        { name = "DB_PORT", value = tostring(local.nautobot_db_port) },
        { name = "POSTGRES_USER", value = "temporal" },
        { name = "DBNAME", value = "temporal" },
        { name = "VISIBILITY_DBNAME", value = "temporal_visibility" },
        { name = "SQL_TLS_ENABLED", value = "true" }, # RDS PostgreSQL 15 以降は rds.force_ssl=1 が既定
        { name = "SQL_HOST_VERIFICATION", value = "false" },
        { name = "SQL_MAX_CONNS", value = tostring(var.temporal_sql_max_conns) },
        { name = "SQL_MAX_IDLE_CONNS", value = tostring(var.temporal_sql_max_idle_conns) },
        { name = "SQL_VIS_MAX_CONNS", value = tostring(var.temporal_visibility_max_conns) },
        { name = "SQL_VIS_MAX_IDLE_CONNS", value = tostring(var.temporal_visibility_max_idle_conns) },
        { name = "NUM_HISTORY_SHARDS", value = "4" }, # 一度決めたら変えられない（変えるなら DB を作り直す）
        { name = "BIND_ON_IP", value = "0.0.0.0" },   # ui と worker が同じタスクの中から 127.0.0.1:7233 で届く
        { name = "LOG_LEVEL", value = "warn" },
        { name = "NAUTOBOT_DB_USER", value = "nautobot" },
      ]
      # 値は state にもタスク定義にも書かない（読む権限は iam.tf の execution_db_passwords）
      secrets = [
        { name = "POSTGRES_PWD", valueFrom = local.temporal_db_password_arn },
        { name = "NAUTOBOT_DB_PASSWORD", valueFrom = local.nautobot_db_password_arn },
      ]
      # HEALTHY = namespace まで出来た。worker と ui はこれを待つ（namespace の無い frontend に繋いで落ちる競合を無くす）
      healthCheck = {
        command     = ["CMD-SHELL", "temporal operator namespace describe -n default --address 127.0.0.1:7233 >/dev/null 2>&1 || exit 1"]
        interval    = 10
        timeout     = 5
        retries     = 6
        startPeriod = 180
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.workflow.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "temporal"
        }
      }
    },
    {
      name      = "ui"
      image     = local.temporal_ui_image
      essential = false # UI が落ちてもワークフローは止めない
      dependsOn = [{ containerName = "temporal", condition = "HEALTHY" }]
      environment = [
        { name = "TEMPORAL_ADDRESS", value = "127.0.0.1:7233" },
        { name = "TEMPORAL_UI_PORT", value = "8233" },
      ]
      portMappings = [
        { containerPort = 8233, protocol = "tcp" }, # Web UI（docs/workflow.md「Temporal UI を開く」）
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.workflow.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "ui"
        }
      }
    },
    # OSS 版（local.graph_neo4j）は NEPTUNE_GRAPH_ID の代わりに GRAPH_BACKEND と NEO4J_URI を同じ位置に置き、パスワードを secrets で足す
    # （merge の右が空のマネージド版では、出来上がるタスク定義は前と 1 文字も変わらない）
    merge({
      name      = "worker"
      image     = local.worker_image
      essential = true
      dependsOn = [{ containerName = "temporal", condition = "HEALTHY" }]
      environment = concat([
        { name = "TEMPORAL_ADDRESS", value = "localhost:7233" },
        { name = "AWS_REGION", value = var.region },
        { name = "PARAM_PREFIX", value = local.param_prefix },
        { name = "ANOMALY_QUEUE_URL", value = aws_sqs_queue.anomalies.url },
        { name = "DECISION_QUEUE_URL", value = aws_sqs_queue.decisions.url },
        ], local.graph_neo4j ? [
        { name = "GRAPH_BACKEND", value = "neo4j" },
        { name = "NEO4J_URI", value = local.neo4j_uri },
        ] : [
        { name = "NEPTUNE_GRAPH_ID", value = local.neptune_graph_id },
        ], [
        { name = "AUDIT_TABLE_BUCKET_ARN", value = local.audit_bucket_arn },
        { name = "AUDIT_NAMESPACE", value = local.audit_namespace },
        { name = "PROPOSAL_EVENTS_TABLE", value = local.proposal_events_table_name },
        { name = "AGENT_RUNTIME_ARN", value = local.runtime_arn },
        { name = "LAB_INSTANCE_ID", value = local.lab_instance_id },
        { name = "APPROVAL_TIMEOUT_MINUTES", value = tostring(var.approval_timeout_minutes) },
        { name = "VERIFY_TIMEOUT", value = tostring(var.verify_timeout_seconds) },
        { name = "HOLD_MINUTES", value = tostring(var.hold_minutes) },
      ])
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.workflow.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "worker"
        }
      }
      }, local.graph_neo4j ? {
      # app/temporal/awsio.py の NEO4J_PASSWORD（SSM の SecureString。値は state にもタスク定義にも書かない。読む権限は iam.tf の execution_neo4j）
      secrets = [{ name = "NEO4J_PASSWORD", valueFrom = local.neo4j_password_arn }]
    } : {}),
  ])

  lifecycle {
    precondition {
      condition     = local.worker_repository_url != "" && local.temporal_repository_url != "" && local.temporal_ui_repository_url != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state から worker_repository_url / temporal_repository_url / temporal_ui_repository_url が読めない。IaC/terraform/aws-managed/base/ecr を create_workflow_repositories = true で apply する。"
    }
    precondition {
      condition     = local.nautobot_db_address != "" && local.nautobot_db_password_arn != ""
      error_message = "IaC/terraform/aws-managed/pipeline/nautobot の state から db_address / db_password_parameter が読めない。Temporal の履歴は Nautobot の RDS for PostgreSQL に置くので、IaC/terraform/aws-managed/pipeline/nautobot を先に apply する（cycle 036 から。deploy.env の PIPELINE=1）。"
    }
    precondition {
      condition     = local.runtime_arn != ""
      error_message = "IaC/terraform/aws-managed/agent の state から agent_runtime_arn が読めない。IaC/terraform/aws-managed/agent を先に apply する（deploy.env の AGENT=1）。"
    }
    precondition {
      condition     = (local.neptune_graph_id != "" && local.neptune_data_arn != "") || local.graph_neo4j
      error_message = "IaC/terraform/aws-managed/pipeline/graph の state から graph_id / graph_arn（OSS 版は neo4j_uri）が読めない。事前チェックと保守中の判定はトポロジ（Neptune。OSS 版は Neo4j）を読むので、IaC/terraform/aws-managed/pipeline/graph を先に apply する（2026-09-24 から）。"
    }
    precondition {
      condition     = local.audit_bucket_arn != "" && local.audit_namespace != "" && local.proposal_events_table_name != ""
      error_message = "IaC/terraform/aws-managed/pipeline/analytics の state から table_bucket_arn / table_namespace / proposal_events_table_name が読めない。修復案は S3 Tables の proposal_events に置くので、IaC/terraform/aws-managed/pipeline/analytics を先に apply する（2026-09-24 から）。"
    }
  }
}

resource "aws_ecs_service" "workflow" {
  name            = "${local.name_prefix}-workflow"
  cluster         = aws_ecs_cluster.workflow.id
  task_definition = aws_ecs_task_definition.workflow.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # aws ecs execute-command でタスクの中に入れる
  enable_execute_command = true

  # desired_count は 0 か 1 で、AZ の数のキー（*_AZ_NUM）も作らない: Temporal のサーバーと worker が同じタスクに入っていて、1 つで足りる。
  # 履歴は RDS にあるので 2 つでも壊れないが、NUM_HISTORY_SHARDS = 4 では分ける意味が薄い（理由は cycle 036 で替えた。前は開発用サーバーの履歴がタスクの中だった）。
  # コードから確かめた理由で、AWS では未確認（2026-10-04）
  # Temporal の履歴は Nautobot の RDS にあるので（cycle 036）、デプロイでは新しいタスクを先に立ててから古いタスクを止める。
  # 入れ替わりの間だけ 2 つのタスクが同じ DB の Temporal として並ぶ（shard の持ち主は DB の中で引き継がれる）
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = [local.subnet_id]
    security_groups  = [local.workflow_sg_id]
    assign_public_ip = false
  }

  depends_on = [aws_iam_role_policy.task, aws_iam_role_policy_attachment.execution, aws_iam_role_policy.execution_neo4j, aws_iam_role_policy.execution_db_passwords]
}
