# ---------------------------------------------------------------- Grafana OSS（ECS on Fargate）。OSS 版（cycle 005）
# マネージド版の grafana.tf（AMP と OpenSearch Serverless を SigV4 で読む）を、OSS 版の宛先に向けたもの。マネージド版の grafana.tf は
# aws_prometheus_workspace と aws_opensearchserverless_* を引くのでリンクできない。タスク定義・サービス・Cloud Map・実行ロール・閉域の Deny の形は同じで、違うのは次のところ:
# - データソースは VictoriaMetrics の vmselect（Prometheus 互換の口。victoriametrics.tf の prometheus_select_url。署名なし）と、
#   自前の OpenSearch（opensearch.tf の opensearch_endpoint。Basic 認証の admin で、パスワードは OpenSearch のタスクと Spark と同じ SSM の SecureString を
#   ECS の secrets で受ける）。app/grafana/start.sh が PROMETHEUS_AUTH=none / OPENSEARCH_AUTH=basic で app/grafana/provisioning/datasources-oss を並べる。
#   uid はマネージド版と同じ amp / aoss-logs なので、ダッシュボード（provisioning/dashboards）とアラートのルール（provisioning/alerting）は
#   マネージド版と同じファイルをそのまま使う（イメージも同じ docker/images/grafana/Dockerfile。写しは作らない）
# - タスクロールは SNS の publish だけ（aps と aoss の許可も、OpenSearch Serverless のデータアクセスポリシーも要らない）。
#   アラートはマネージド版と同じ連絡先（app/grafana/provisioning/alerting/netops.yaml。タスクロールの SigV4 で sns の VPC エンドポイントを通る）から
#   土台のトピック（IaC/terraform/aws-managed/base/core の alerts.tf）へ出て、status の Lambda（IaC/terraform/oss/pipeline/graph の sync.tf）とワークフロー
#   （IaC/terraform/aws-managed/workflow の events.tf）が受ける。SigV4 が要るのは SNS だけで、データソースの認証の違いは環境変数で吸収する
# - Grafana から OpenSearch の 9200 と vmselect の 8481 への SG の行は土台の oss.tf にある
# 作るかどうかはマネージド版と同じ var.create_grafana（既定 false。network.tf の local.create_grafana）。イメージを <接頭辞>-grafana に push し、
# admin のパスワードの SecureString を作ってから true にする（OSS 版の ops/up.sh の仕事）

locals {
  grafana_image        = "${try(data.terraform_remote_state.ecr.outputs.grafana_repository_url, "")}:${var.grafana_image_tag}"
  grafana_log_group    = "/ecs/${local.name_prefix}-grafana"
  grafana_password_arn = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.grafana_password_parameter}"
  # ECS の secrets。OpenSearch のパスワードは OpenSearch を作るときだけ（無ければ start.sh は OpenSearch のデータソースを並べない）
  grafana_secrets = concat(
    [{ name = "GF_SECURITY_ADMIN_PASSWORD", valueFrom = local.grafana_password_arn }],
    [for s in [{ name = "OPENSEARCH_PASSWORD", valueFrom = local.opensearch_password_arn }] : s if local.sink_opensearch],
  )
}

resource "aws_cloudwatch_log_group" "grafana" {
  count = local.create_grafana ? 1 : 0

  name              = local.grafana_log_group
  retention_in_days = var.log_retention_days
}

resource "aws_service_discovery_service" "grafana" {
  count = local.create_grafana ? 1 : 0

  name = "grafana"
  # タスクの登録が残っていても destroy できるようにする
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

resource "aws_ecs_task_definition" "grafana" {
  count = local.create_grafana ? 1 : 0

  family                   = "${local.name_prefix}-grafana"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.grafana_task_cpu
  memory                   = var.grafana_task_memory
  execution_role_arn       = aws_iam_role.grafana_execution[0].arn
  task_role_arn            = aws_iam_role.grafana_task[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # readonlyRootFilesystem は付けない（マネージド版と同じ。/var/lib/grafana と /tmp に書く）
  container_definitions = jsonencode([
    {
      name         = "grafana"
      image        = local.grafana_image
      essential    = true
      portMappings = [{ containerPort = 3000, protocol = "tcp" }]
      # app/grafana/start.sh が、値のあるデータソースとダッシュボードだけを provisioning に入れる
      environment = [
        { name = "AWS_REGION", value = var.region },
        { name = "PROMETHEUS_URL", value = local.prometheus_select_url },
        { name = "OPENSEARCH_URL", value = local.opensearch_endpoint },
        { name = "OPENSEARCH_INDEX", value = local.opensearch_index },
        # データソースを datasources-oss の定義にする（vmselect は署名なし、OpenSearch は Basic 認証。ユーザーは start.sh の既定の admin）
        { name = "PROMETHEUS_AUTH", value = "none" },
        { name = "OPENSEARCH_AUTH", value = "basic" },
        # アラートの送り先（app/grafana/start.sh はこれがあるときだけ alerting の provisioning を入れる。ルールは PROMETHEUS_URL / OPENSEARCH_URL があるほうだけ）
        { name = "ALERTS_TOPIC_ARN", value = local.alerts_topic_arn },
      ]
      secrets = local.grafana_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.grafana[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "grafana"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.grafana_repository_url, "") != ""
      error_message = "IaC/terraform/oss/base/ecr の state から grafana_repository_url が読めない。IaC/terraform/oss/base/ecr を先に apply する（OSS 版の ops/up.sh）。"
    }
    precondition {
      condition     = local.alerts_topic_arn != ""
      error_message = "IaC/terraform/oss/base/core の state から alerts_topic_arn が読めない（2026-10-02 より前の土台）。IaC/terraform/oss/base/core を先に apply する。"
    }
  }
}

resource "aws_ecs_service" "grafana" {
  count = local.create_grafana ? 1 : 0

  name            = "${local.name_prefix}-grafana"
  cluster         = aws_ecs_cluster.analytics[0].id
  task_definition = aws_ecs_task_definition.grafana[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # 1 タスクだけ（サブネット a）。2 つにするとどちらもアラートのルールを評価して通知が 2 重になる（マネージド版の grafana.tf と同じ理由）
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = [local.instance_subnet_id]
    security_groups  = [local.grafana_sg_id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.grafana[0].arn
  }

  depends_on = [
    aws_iam_role_policy.grafana_task,
    aws_iam_role_policy.grafana_execution,
    aws_iam_role_policy_attachment.grafana_execution,
  ]
}

# ---------------------------------------------------------------- IAM
resource "aws_iam_role" "grafana_execution" {
  count = local.create_grafana ? 1 : 0

  name               = "${local.name_prefix}-grafana-exec"
  description        = "ECS task execution role of the Grafana task (ECR pull, CloudWatch Logs, admin and OpenSearch passwords from SSM)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "grafana_execution" {
  count = local.create_grafana ? 1 : 0

  role       = aws_iam_role.grafana_execution[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# secrets の SecureString（AWS 管理の aws/ssm キーなので kms:Decrypt は要らない）。読めるのはタスク定義の secrets に並べたものだけ
resource "aws_iam_role_policy" "grafana_execution" {
  count = local.create_grafana ? 1 : 0

  name = "${local.name_prefix}-grafana-exec"
  role = aws_iam_role.grafana_execution[0].name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "Secrets"
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = [for s in local.grafana_secrets : s.valueFrom]
    }]
  })
}

resource "aws_iam_role" "grafana_task" {
  count = local.create_grafana ? 1 : 0

  name               = "${local.name_prefix}-grafana-task"
  description        = "Grafana task - publish alerts to the SNS topic (SigV4). VictoriaMetrics and OpenSearch are read without AWS credentials"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "grafana_task" {
  count = local.create_grafana ? 1 : 0

  name = "${local.name_prefix}-grafana-task"
  role = aws_iam_role.grafana_task[0].name

  # アラートの連絡先（SNS）だけ。トピックの鍵は AWS 管理の aws/sns なので kms の許可は要らない
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

# IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない呼び出しを拒む）。実行ロールとタスクロールの両方に付ける
resource "aws_iam_role_policy_attachment" "grafana_execution_perimeter" {
  count = local.create_grafana && local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.grafana_execution[0].name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "grafana_task_perimeter" {
  count = local.create_grafana && local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.grafana_task[0].name
  policy_arn = local.perimeter_policy_arn
}
