# nwc-poc - workflow root module (feature "workflow"). One ECS on Fargate task (ARM64, 1 vCPU / 2 GB) runs the Temporal server
# (history in the Nautobot RDS, cycle 036), the Temporal UI and a Python worker in the VPC of IaC/terraform/aws-managed/base/core. The Grafana alert rules and the Splunk saved searches of IaC/terraform/aws-managed/pipeline/analytics
# publish alerts to the SNS topic of IaC/terraform/aws-managed/base/core; events.tf subscribes an SQS queue to it and the worker starts one workflow per anomaly
# (and signals it when the alert resolves). The workflow asks the
# chat runtime (AgentCore) for a cause and a fix (the runtime looks at Neptune / OpenSearch / Prometheus through the MCP tools),
# writes the proposal as one row per step to S3 Tables (proposal_events), waits for a human decision (web tab "承認", sent through the
# decision queue of events.tf), applies the fix on the lab EC2 (IaC/terraform/aws-managed/pipeline/lab) through SSM Run Command and waits for the resolved alert. Temporal runs on ECS now (EKS later - 2026-09-17 user decision).
# The AgentCore Gateway (MCP) exposes the agent tools through a Lambda in the VPC so the runtime can read Neptune, the logs
# collection and the metrics workspace over MCP. Costs about 0.05 USD per hour while it exists (Fargate) - destroy it the same day.

# リソース名の接頭辞であり Project タグの値。デプロイする人の名前（var.owner）から作るので、
# 1 つの AWS アカウントを何人かで使っても、自分の名前で自分のリソースを探せる
locals {
  # 末尾は var.project（IaC/terraform/aws-managed/ は既定の nwc-poc、OSS 版の IaC/terraform/oss/ は oss.auto.tfvars の nwc-oss。cycle 005）
  name_prefix = "${var.owner}-${var.project}"
}

# リポジトリの根（Lambda の zip に入れるソースをここから読む）。IaC/terraform/aws-managed/<このルート> と IaC/terraform/oss/<このルート>（ファイルごとの
# シンボリックリンク。cycle 005）は同じ深さなので、path.module（リンクを置いたフォルダ）から 4 つ上がどちらでも根（cycle 007 から）
locals {
  repo_root = "${path.module}/../../../.."
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# VPC / サブネット / SG / ロール名は IaC/terraform/aws-managed/base/core、Runtime ARN は IaC/terraform/aws-managed/agent、lab EC2 は IaC/terraform/aws-managed/pipeline/lab、
# Neptune（トポロジと status。OSS 版は Neo4j）は IaC/terraform/aws-managed/pipeline/graph、修復案の S3 Tables と OpenSearch / Prometheus は IaC/terraform/aws-managed/pipeline/analytics の state から読む。
# ワーカーは Neptune（事前チェック）と proposal_events が無いと動かないので graph と analytics は必須（ecs.tf の precondition）
data "terraform_remote_state" "main" {
  backend = "local"

  config = {
    path = "${path.module}/../base/core/terraform.tfstate"
  }

  # SG の ID（security_group_ids）は 2026-09-29 から。それより前の state（全部で共有する internal 1 つ）なら apply の前に止める。
  # destroy ではこの条件を見ないので、locals の SG の try と合わせて古い state のまま ops/down.sh で消せる（Terraform 1.16 で確認）
  lifecycle {
    postcondition {
      condition     = can(self.outputs.security_group_ids)
      error_message = "IaC/terraform/aws-managed/base/core の state に security_group_ids が無い（2026-09-29 より前の SG）。先に ops/down.sh で消してから ops/up.sh を打ち直す"
    }
  }
}

data "terraform_remote_state" "agent" {
  backend = "local"

  config = {
    path = "${path.module}/../agent/terraform.tfstate"
  }
}

data "terraform_remote_state" "lab" {
  backend = "local"

  config = {
    path = "${path.module}/../pipeline/lab/terraform.tfstate"
  }
}

data "terraform_remote_state" "ecr" {
  backend = "local"

  config = {
    path = "${path.module}/../base/ecr/terraform.tfstate"
  }
}

data "terraform_remote_state" "graph" {
  backend = "local"

  config = {
    path = "${path.module}/../pipeline/graph/terraform.tfstate"
  }
}

data "terraform_remote_state" "analytics" {
  backend = "local"

  config = {
    path = "${path.module}/../pipeline/analytics/terraform.tfstate"
  }
}

# Temporal の履歴（DB temporal / temporal_visibility）は Nautobot の RDS for PostgreSQL に置く（cycle 036）。nautobot が無ければ空で、ecs.tf の precondition が止める
data "terraform_remote_state" "nautobot" {
  backend = "local"

  config = {
    path = "${path.module}/../pipeline/nautobot/terraform.tfstate"
  }
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  vpc_id    = data.terraform_remote_state.main.outputs.vpc_id
  subnet_id = data.terraform_remote_state.main.outputs.instance_subnet_id # サブネット a（Web の EC2 と同じ）
  # サブネット a / b / c（この順）。tools Lambda は先頭から var.lambda_az_num 個を使う
  subnet_ids = data.terraform_remote_state.main.outputs.subnet_ids
  # SG は古い state の destroy でも評価できるように try（空のまま apply に進まないよう remote_state の postcondition で止める）
  workflow_sg_id = try(data.terraform_remote_state.main.outputs.security_group_ids["workflow"], "")
  lambda_sg_id   = try(data.terraform_remote_state.main.outputs.security_group_ids["lambda"], "") # gateway.tf の tools Lambda
  # IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない AWS の API を拒む）。NETWORK_PERIMETER=0 か古い state なら空
  perimeter_policy_arn = try(data.terraform_remote_state.main.outputs.network_perimeter_policy_arn, "")
  # リソースポリシーの Deny から外すプリンシパル（デプロイする人と KB のロール）
  perimeter_exempt_principals = try(data.terraform_remote_state.main.outputs.perimeter_exempt_principals, [])
  # アラートの SNS トピック（IaC/terraform/aws-managed/base/core の alerts.tf）。events.tf のキューが購読する。古い state なら空で、購読の precondition が止める
  alerts_topic_arn = try(data.terraform_remote_state.main.outputs.alerts_topic_arn, "")
  # agent が無いとワークフローが原因を聞く先が無い。下の precondition で「agent を先に」と出す
  runtime_arn = try(data.terraform_remote_state.agent.outputs.agent_runtime_arn, "")

  # SSM を読む 2 つのロール（チャットの Runtime と Web の EC2）。Gateway を呼ぶのは Runtime だけ（proposals.tf の reader_access）。
  # Neptune の読み書きは IaC/terraform/aws-managed/pipeline/graph の access.tf が付ける。
  # 決定のキューへの送信と Athena での proposal_events の読み取りは Web だけ（proposals.tf）
  runtime_role_name = data.terraform_remote_state.main.outputs.runtime_role_name
  web_role_name     = data.terraform_remote_state.main.outputs.web_role_name
  reader_role_names = toset([local.runtime_role_name, local.web_role_name])

  # lab が無ければ Apply の段は打つ先が無い（ワーカーは修復案を failed にする）
  lab_instance_id = try(data.terraform_remote_state.lab.outputs.lab_instance_id, "")

  # Neptune（トポロジと status。ワーカーの事前チェック）。graph が無ければ空で、ecs.tf の precondition が「graph を先に」と出す
  neptune_graph_id = try(data.terraform_remote_state.graph.outputs.graph_id, "")
  neptune_data_arn = try(data.terraform_remote_state.graph.outputs.graph_arn, "")
  # OSS 版（IaC/terraform/oss/pipeline/graph。cycle 005）は Neptune の代わりに Neo4j で、state に neo4j_uri がある。あれば Worker を
  # GRAPH_BACKEND=neo4j で Neo4j に向け（ecs.tf）、パスワード（SSM の SecureString）を ECS の secrets で渡す（iam.tf の実行ロール）。
  # マネージド版の graph の state には無いので空で、ここから下はマネージド版では何も変えない
  neo4j_uri          = try(data.terraform_remote_state.graph.outputs.neo4j_uri, "")
  graph_neo4j        = local.neo4j_uri != ""
  neo4j_password_arn = local.graph_neo4j ? "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${data.terraform_remote_state.graph.outputs.neo4j_password_parameter}" : ""
  # tools Lambda（gateway.tf）に付ける Neo4j のドライバのレイヤー（OSS 版の graph が status Lambda 用に作るものを共用する。Lambda の Python に neo4j は無い）。
  # OSS 版でも 2026-10-06 より前の graph の state には無いので空で、gateway.tf の precondition が「graph を apply し直す」と出す
  neo4j_layer_arn = try(data.terraform_remote_state.graph.outputs.neo4j_layer_arn, "")

  # 修復案（S3 Tables の proposal_events。2026-10-05 から修復案の置き場はここだけ）。analytics が無ければ空で、ecs.tf の precondition が「analytics を先に」と出す
  audit_bucket_arn           = try(data.terraform_remote_state.analytics.outputs.table_bucket_arn, "")
  audit_namespace            = try(data.terraform_remote_state.analytics.outputs.table_namespace, "")
  proposal_events_table_name = try(data.terraform_remote_state.analytics.outputs.proposal_events_table_name, "")
  proposal_events_table_arn  = try(data.terraform_remote_state.analytics.outputs.proposal_events_table_arn, "")

  # アラートの通知の履歴（alert_events。graph の status Lambda → Firehose が書く）を query_history が、修復案（proposal_events）を
  # list_proposals と Web の承認タブが Athena で読む。analytics が無いか 2026-10-04 より前の analytics なら空で、ツールと画面は「未配備」を返す
  athena_workgroup        = try(data.terraform_remote_state.analytics.outputs.athena_workgroup, "")
  athena_catalog          = try(data.terraform_remote_state.analytics.outputs.athena_catalog, "")
  alert_events_table_name = try(data.terraform_remote_state.analytics.outputs.alert_events_table_name, "")
  alert_events_table_arn  = try(data.terraform_remote_state.analytics.outputs.alert_events_table_arn, "")

  # Athena で履歴を読む 4 文。tools の Lambda（gateway.tf）と Web の EC2（proposals.tf）に同じものを付ける。
  # Athena のクエリはワークグループだけで打ち、結果は Athena の管理ストレージ。Athena は呼び手の権限で Glue のカタログ（s3tablescatalog）と
  # S3 Tables を読む。閉域の Deny（s3tables:*）は Athena が代わりに出す呼び出し（aws:ViaAWSService）には効かない前提
  # （docs/cycles/001-alert-history-firehose/design.md のリスク 3）。そのぶん VPC の外からの athena:* は閉域の Deny で止め、
  # 読めるテーブルは alert_events と proposal_events だけにする（raw_telemetry は読ませない）。
  # テーブルの ARN は名前ではなくテーブルの ID で終わるので、analytics の出力から受ける。ワークグループが無ければ 1 文も付けない
  history_table_arns = compact([local.alert_events_table_arn, local.proposal_events_table_arn])
  history_read_statements = [for s in [
    {
      sid       = "HistoryQuery"
      actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution"]
      resources = ["arn:${local.partition}:athena:${var.region}:${local.account_id}:workgroup/${local.athena_workgroup}"]
    },
    {
      sid     = "HistoryCatalog"
      actions = ["glue:GetCatalog", "glue:GetDatabase", "glue:GetTable"]
      resources = [
        "arn:${local.partition}:glue:${var.region}:${local.account_id}:catalog",
        "arn:${local.partition}:glue:${var.region}:${local.account_id}:catalog/s3tablescatalog",
        "arn:${local.partition}:glue:${var.region}:${local.account_id}:catalog/s3tablescatalog/*",
        "arn:${local.partition}:glue:${var.region}:${local.account_id}:database/*",
        "arn:${local.partition}:glue:${var.region}:${local.account_id}:table/*/*",
      ]
    },
    {
      sid       = "HistoryBucket"
      actions   = ["s3tables:GetTableBucket", "s3tables:GetNamespace"]
      resources = [local.audit_bucket_arn]
    },
    {
      sid       = "HistoryTable"
      actions   = ["s3tables:GetTable", "s3tables:GetTableData", "s3tables:GetTableMetadataLocation"]
      resources = local.history_table_arns
    },
  ] : s if local.athena_workgroup != "" && length(compact(s.resources)) > 0]

  opensearch_collection_name = try(data.terraform_remote_state.analytics.outputs.opensearch_collection_name, "")
  opensearch_collection_arn  = try(data.terraform_remote_state.analytics.outputs.opensearch_collection_arn, "")
  opensearch_endpoint        = try(data.terraform_remote_state.analytics.outputs.opensearch_collection_endpoint, "")
  opensearch_index           = try(data.terraform_remote_state.analytics.outputs.opensearch_index, "snmp-logs")
  prometheus_workspace_arn   = try(data.terraform_remote_state.analytics.outputs.prometheus_workspace_arn, "")
  prometheus_query_url       = try(data.terraform_remote_state.analytics.outputs.prometheus_query_url, "")
  # OSS 版（IaC/terraform/oss/pipeline/analytics。cycle 005）は OpenSearch Serverless と AMP の代わりに ECS の OpenSearch と VictoriaMetrics で、
  # state にコレクションとワークスペースの ARN が無く、OpenSearch の admin のパスワードの SSM の名前（opensearch_password_parameter）がある。
  # そのときだけ tools Lambda の evidence.py を Basic 認証（OPENSEARCH_AUTH=basic）と署名なし（PROMETHEUS_AUTH=none）に切り替える（gateway.tf）。
  # マネージド版の analytics の state にはこの output が無いので false のまま
  analytics_oss = try(data.terraform_remote_state.analytics.outputs.opensearch_password_parameter, "") != ""

  worker_repository_url      = try(data.terraform_remote_state.ecr.outputs.worker_repository_url, "")
  temporal_repository_url    = try(data.terraform_remote_state.ecr.outputs.temporal_repository_url, "")
  temporal_ui_repository_url = try(data.terraform_remote_state.ecr.outputs.temporal_ui_repository_url, "")

  worker_image      = "${local.worker_repository_url}:${var.worker_image_tag}"
  temporal_image    = "${local.temporal_repository_url}:${var.temporal_image_tag}"
  temporal_ui_image = "${local.temporal_ui_repository_url}:${var.temporal_ui_image_tag}"

  # Temporal の履歴を置く RDS（IaC/terraform/aws-managed/pipeline/nautobot の database.tf。cycle 036）。一回きりのタスク <接頭辞>-workflow-init
  # （docker/images/temporal-server/init.sh。ecs.tf の aws_ecs_task_definition.init）が master（nautobot）でロール temporal と DB を作ってスキーマを入れ、
  # サーバー（temporal のコンテナ）はロール temporal で読み書きする（cycle 042）。
  # パスワードは 2 つとも SSM の SecureString（ops/up.sh の ensure_nautobot_secrets と ensure_temporal_secrets が作る）を ECS の secrets で渡す（iam.tf の execution_db_passwords）。
  # master のパスワードは init のタスクにだけ渡す。nautobot が無いか 2026-10-10 より前の state なら空で、ecs.tf の precondition が止める
  nautobot_db_address      = try(data.terraform_remote_state.nautobot.outputs.db_address, "")
  nautobot_db_port         = try(data.terraform_remote_state.nautobot.outputs.db_port, 5432)
  nautobot_db_password_arn = try("arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${data.terraform_remote_state.nautobot.outputs.db_password_parameter}", "")
  temporal_db_password_arn = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/${local.name_prefix}/temporal/db-password"
  # サーバー（temporal のコンテナ）と init のタスクの両方に渡す DB の env（ecs.tf。docker/images/temporal-server/common.sh が読む。cycle 042）
  temporal_db_env = [
    { name = "POSTGRES_SEEDS", value = local.nautobot_db_address },
    { name = "DB_PORT", value = tostring(local.nautobot_db_port) },
    { name = "POSTGRES_USER", value = "temporal" },
    { name = "DBNAME", value = "temporal" },
    { name = "VISIBILITY_DBNAME", value = "temporal_visibility" },
    { name = "SQL_TLS_ENABLED", value = "true" }, # RDS PostgreSQL 15 以降は rds.force_ssl=1 が既定
    # true にするには RDS の CA をイメージに入れて SQL_CA を渡す（SQL_HOST_NAME は任意。無ければ接続先のホスト名）。
    # false のあいだは psql（require）も temporal-server（InsecureSkipVerify）も temporal-sql-tool も CA を確かめない
    { name = "SQL_HOST_VERIFICATION", value = "false" },
  ]

  param_prefix = "/${local.name_prefix}"
  log_group    = "/ecs/${local.name_prefix}-workflow"

  # Nautobot の中だけのシークレット（api-token を除く 3 つ。IaC/terraform/aws-managed/base/core の locals.tf の同名と同じ ARN）。
  # tools の Lambda のロール（gateway.tf）で Deny する（cycle 044）。nautobot の state が無くても Deny は要るので、state の出力からは組まない。
  # prefix を問わない（parameter/*/nautobot/<名前>）のは base/core と lab の Deny と同じ ARN にそろえるため
  nautobot_secret_parameter_arns = [
    "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/*/nautobot/secret-key",
    "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/*/nautobot/admin-password",
    "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/*/nautobot/db-password",
  ]
}
