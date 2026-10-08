# ---------------------------------------------------------------- Spark（ECS on Fargate、local モード）
# マネージド版の EMR Serverless のジョブ（emr.tf と outputs.tf の job_drivers）の代わり。分け方は同じで、格納先ごとに 1 サービス
# （iceberg = 全トピックを S3 Tables、splunk = 全トピックを Splunk の HEC、http = opensearch と prometheus。
# var.sinks に無い格納先は外し、空になったジョブは作らない）。
# どのタスクも spark-submit --master local[*]（driver も executor も 1 つの JVM）。EMR の STREAMING モードの起こし直しの代わりに、
# サービス（1 台）がタスクの終わりを見て起こし直す。台数は作るときは 0 で、OSS 版の ops/up.sh が書き先が上がってから 1 にする。
# イメージは docker/images/spark/Dockerfile（apache/spark:3.5.9-java17-python3 に Kafka・Iceberg・S3 Tables・S3A の jar と app/spark/snmp_sinks.py を焼き込む。
# 閉域で Maven に届かないので、起動時に jar を取りに行かない）。OSS 版の ops/up.sh が作って ECR の <接頭辞>-spark に push する。
# checkpoint はマネージド版と同じバケットの analytics/checkpoint/ に S3A（s3a://）で書く（EMR の s3:// は EMRFS で、素の Spark には無い）。
# Kafka は PLAINTEXT（KAFKA_AUTH=none）、OpenSearch は Basic 認証（OPENSEARCH_AUTH=basic）、vminsert は署名なし（PROMETHEUS_AUTH=none）。
# 宛先とパスワードは opensearch.tf・victoriametrics.tf の locals をそのまま使う（同じ値を別の名前で持たない）。
# Splunk はマネージド版と同じ（リンクした splunk.tf のタスク）で、送り方もマネージド版の splunk の格納先と同じ（HEC に自己署名の TLS で POST）。
# 違うのは HEC の token の受け取り方だけで、EMR のジョブが起動時に SSM から読む代わりに、Splunk のタスクと同じく
# ECS の secrets で SSM の SecureString を SPLUNK_HEC_TOKEN として受ける（値は Terraform も state もタスク定義も持たない）。
# Splunk が起きる前（起動に数分）は HEC への POST が再試行の後に落ちてタスクが終わり、サービスが起こし直す（checkpoint の続きから読む）

variable "spark_image_tag" {
  description = "Tag of the app/spark/ image in the <prefix>-spark repository (apache/spark with the jars and app/spark/snmp_sinks.py). The OSS ops/up.sh builds it as <Spark version>-<hash of app/spark/ and docker/images/spark/Dockerfile>."
  type        = string
  default     = "3.5.9"

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.spark_image_tag))
    error_message = "spark_image_tag は ECR のタグの形（英数字と _ . -、128 文字まで）。"
  }
}

variable "spark_task_cpu" {
  description = "Fargate CPU units of each Spark task (ARM64). local[*] uses all the vCPUs of the task."
  type        = number
  default     = 2048

  validation {
    condition     = contains([1024, 2048, 4096], var.spark_task_cpu)
    error_message = "spark_task_cpu must be 1024, 2048 or 4096."
  }
}

variable "spark_task_memory" {
  description = "Fargate memory (MiB) of each Spark task. Half of it is the driver JVM heap (--driver-memory), the rest is left to the Python process and the JVM off-heap. Must be a valid pair with spark_task_cpu (2048 takes 4096-16384)."
  type        = number
  default     = 4096

  validation {
    condition     = contains([2048, 4096, 8192, 16384], var.spark_task_memory)
    error_message = "spark_task_memory must be 2048, 4096, 8192 or 16384."
  }
}

locals {
  spark_image     = "${try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["spark"], "")}:${var.spark_image_tag}"
  spark_log_group = "/ecs/${local.name_prefix}-spark"

  # ジョブ → 格納先（空のジョブは作らない）
  spark_services = { for job, sinks in local.spark_jobs : job => sinks if length(sinks) > 0 }

  # checkpoint は Kafka の offset を持つ。マネージド版は MSK のクラスタの uuid をパスに入れる（locals.tf の checkpoint_uri）。
  # OSS 版の Kafka のデータは IaC/terraform/aws-managed/base/core の EFS にあるので、EFS が作り直されたら checkpoint も新しくする
  spark_checkpoint_uri = "s3a://${local.bucket}/${local.checkpoint}/${try(data.terraform_remote_state.main.outputs.efs_file_system_id, "none")}/"

  # 宛先（local.opensearch_endpoint・local.prometheus_remote_write_url）は opensearch.tf と victoriametrics.tf が Cloud Map の名前で持つ
  # （マネージド版の locals.tf と同じ名前）。OpenSearch の REST は TLS なしの HTTP（snmp_sinks.py に自己署名の証明書を飛ばす設定が無いため）。
  # 格納先ごとに ECS の secrets で受ける SSM の SecureString（名前は app/spark/snmp_sinks.py が読む環境変数）。
  # OPENSEARCH_PASSWORD は OpenSearch のタスクが admin のパスワードにするのと同じパラメータ（opensearch.tf の opensearch_password_arn）、
  # SPLUNK_HEC_TOKEN は splunk.tf の Splunk のタスクが HEC の token を作るのと同じパラメータ（network.tf の splunk_token_parameter_arn）
  spark_secrets = {
    opensearch = { name = "OPENSEARCH_PASSWORD", valueFrom = local.opensearch_password_arn }
    splunk     = { name = "SPLUNK_HEC_TOKEN", valueFrom = local.splunk_token_parameter_arn }
  }

  # spark-submit の引数。カタログの設定はマネージド版の sparkSubmitParameters と同じ（iceberg を選ばなければ S3 Tables の API は呼ばない）
  spark_submit = concat(
    ["/opt/spark/bin/spark-submit", "--master", "local[*]", "--driver-memory", "${floor(var.spark_task_memory / 2)}m",
      "--conf", "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
      "--conf", "spark.sql.catalog.${local.catalog_name}=org.apache.iceberg.spark.SparkCatalog",
      "--conf", "spark.sql.catalog.${local.catalog_name}.catalog-impl=software.amazon.s3tables.iceberg.S3TablesCatalog",
    "--conf", "spark.sql.catalog.${local.catalog_name}.warehouse=${local.table_bucket_arn}"],
    # checkpoint の S3A。リージョンのエンドポイント（S3 の gateway エンドポイントを通る）と、タスクロールの認証情報（ECS のコンテナの認証情報）
    ["--conf", "spark.hadoop.fs.s3a.endpoint=s3.${var.region}.amazonaws.com",
      "--conf", "spark.hadoop.fs.s3a.endpoint.region=${var.region}",
    "--conf", "spark.hadoop.fs.s3a.aws.credentials.provider=com.amazonaws.auth.EC2ContainerCredentialsProviderWrapper"],
    ["/opt/nwc/snmp_sinks.py"],
  )

  # snmp_sinks.py の引数。マネージド版の job_drivers の entryPointArguments と同じ並び
  # （splunk は --splunk-token-parameter だけを渡さない。token は環境変数 SPLUNK_HEC_TOKEN で受ける）
  spark_arguments = { for job, sinks in local.spark_services : job => concat(
    ["--bootstrap", local.bootstrap, "--checkpoint", local.spark_checkpoint_uri, "--sinks", join(",", sinks), "--region", var.region,
    "--metric-topics", local.metric_topics, "--log-topics", local.log_topics],
    ["--max-offsets-per-trigger", tostring(var.max_offsets_per_trigger)],
    [for a in ["--max-offsets-per-trigger-by-sink", local.max_offsets_by_job[job]] : a if local.max_offsets_by_job[job] != ""],
    [for a in ["--http-send", var.http_send] : a if var.http_send != "driver" && job != "iceberg"],
    [for a in ["--iceberg-table", local.iceberg_table] : a if contains(sinks, "iceberg")],
    [for a in ["--opensearch-endpoint", local.opensearch_endpoint, "--opensearch-index", local.opensearch_index] : a if contains(sinks, "opensearch")],
    [for a in ["--prometheus-url", local.prometheus_remote_write_url] : a if contains(sinks, "prometheus")],
    [for a in ["--splunk-hec-url", local.splunk_hec_url, "--splunk-index", var.splunk_index] : a if contains(sinks, "splunk")],
    [for a in ["--splunk-skip-verify"] : a if contains(sinks, "splunk") && local.splunk_skip_tls_verify],
    [for a in ["--device-map", var.device_map] : a if var.device_map != "" && (contains(sinks, "prometheus") || contains(sinks, "opensearch"))],
  ) }
}

resource "aws_cloudwatch_log_group" "spark" {
  name              = local.spark_log_group
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "spark" {
  for_each = local.spark_services

  family                   = "${local.name_prefix}-spark-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.spark_task_cpu
  memory                   = var.spark_task_memory
  execution_role_arn       = aws_iam_role.spark_execution.arn
  task_role_arn            = aws_iam_role.spark_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "spark"
      image     = local.spark_image
      essential = true
      # イメージの /opt/entrypoint.sh は driver / executor 以外のコマンドをそのまま実行する
      command = concat(local.spark_submit, local.spark_arguments[each.key])
      environment = concat(
        [
          { name = "KAFKA_AUTH", value = "none" },
          { name = "AWS_REGION", value = var.region },
        ],
        [for e in [{ name = "OPENSEARCH_AUTH", value = "basic" }] : e if contains(each.value, "opensearch")],
        [for e in [{ name = "PROMETHEUS_AUTH", value = "none" }] : e if contains(each.value, "prometheus")],
      )
      # OpenSearch の admin のパスワード（ユーザーは snmp_sinks.py の既定の admin）と Splunk の HEC の token。そのジョブの格納先の分だけ
      secrets = [for sink, s in local.spark_secrets : s if contains(each.value, sink)]
      # 止めるときに今の micro-batch を終える時間（Fargate の上限）。checkpoint は batch ごとに書くので、途中で切れても次は続きから読む
      stopTimeout = 120
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.spark.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "spark-${each.key}"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["spark"], "") != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state に spark のリポジトリが無い（project = nwc-oss でない ECR か、古い ECR）。IaC/terraform/oss/base/ecr を先に apply する。"
    }
  }
}

resource "aws_ecs_service" "spark" {
  for_each = local.spark_services

  name            = "${local.name_prefix}-spark-${each.key}"
  cluster         = aws_ecs_cluster.analytics[0].id
  task_definition = aws_ecs_task_definition.spark[each.key].arn
  # 作るときは 0 台。OSS 版の ops/up.sh が OpenSearch・VictoriaMetrics・Splunk が上がるのを待ってから 1 にする
  # （先に起こすと、書き先に届かずに落ちては起こし直されるのを繰り返す）。1 にした後の apply で 0 に戻さないよう、台数は Terraform が見ない
  desired_count = 0
  launch_type   = "FARGATE"

  # aws ecs execute-command でタスクの中に入れる
  enable_execute_command = true

  # 同じ checkpoint を 2 つのタスクが同時に使わない（入れ替えでは古いタスクを止めてから新しいタスクを起こす）
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  # マネージド版の EMR の AZ と同じ数（var.emr_az_num）のサブネットのどれか
  network_configuration {
    subnets          = slice(local.subnet_ids, 0, var.emr_az_num)
    security_groups  = [local.spark_sg_id]
    assign_public_ip = false
  }

  lifecycle {
    ignore_changes = [desired_count]
    precondition {
      condition     = local.spark_sg_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に spark の SG が無い。IaC/terraform/oss/base/core を先に apply する。"
    }
    precondition {
      condition     = local.bootstrap != ""
      error_message = "IaC/terraform/oss/pipeline/stream の state に bootstrap_brokers が無い。IaC/terraform/oss/pipeline/stream を先に apply する。"
    }
  }

  depends_on = [
    aws_iam_role_policy.spark_task,
    aws_iam_role_policy.spark_execution,
    aws_iam_role_policy_attachment.spark_execution,
  ]
}

# ---------------------------------------------------------------- IAM（どのジョブも同じロール）
resource "aws_iam_role" "spark_execution" {
  name               = "${local.name_prefix}-spark-exec"
  description        = "ECS task execution role of the Spark tasks (ECR pull, CloudWatch Logs, the OpenSearch password and the Splunk HEC token from SSM)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "spark_execution" {
  role       = aws_iam_role.spark_execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# secrets の OPENSEARCH_PASSWORD と SPLUNK_HEC_TOKEN（SecureString でも AWS 管理の aws/ssm キーなので kms:Decrypt は要らない）。
# 選んだ格納先の分だけ。opensearch も splunk も選ばないときは読むパラメータが無いので、ポリシーごと作らない
resource "aws_iam_role_policy" "spark_execution" {
  count = local.sink_opensearch || local.sink_splunk ? 1 : 0

  name = "${local.name_prefix}-spark-exec"
  role = aws_iam_role.spark_execution.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [for sink, sid in { opensearch = "OpenSearchPassword", splunk = "SplunkHecToken" } : {
      Sid      = sid
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = local.spark_secrets[sink].valueFrom
    } if contains(var.sinks, sink)]
  })
}

resource "aws_iam_role" "spark_task" {
  name               = "${local.name_prefix}-spark-task"
  description        = "Spark task role - checkpoint in the asset bucket (S3A), S3 Tables catalog, ECS Exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "spark_task" {
  name = "${local.name_prefix}-spark-task"
  role = aws_iam_role.spark_task.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        # checkpoint を読み書きする（マネージド版の EMR の実行ロールの AssetBucket と同じ範囲）
        Sid      = "AssetBucket"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${local.bucket_arn}/${local.s3_prefix}/*"
      },
      {
        Sid      = "AssetBucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = local.bucket_arn
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
      ],
      # Iceberg のカタログ操作（マネージド版の S3TablesCatalog と同じ）。「cond ? [..] : []」は両辺の型が揃わず validate が落ちるので for … if で絞る
      [for s in [{
        Sid    = "S3TablesCatalog"
        Effect = "Allow"
        Action = [
          "s3tables:GetTableBucket",
          "s3tables:ListNamespaces",
          "s3tables:GetNamespace",
          "s3tables:ListTables",
          "s3tables:GetTable",
          "s3tables:GetTableMetadataLocation",
          "s3tables:UpdateTableMetadataLocation",
          "s3tables:GetTableData",
          "s3tables:PutTableData",
        ]
        Resource = [
          aws_s3tables_table_bucket.tables.arn,
          "${aws_s3tables_table_bucket.tables.arn}/table/*",
        ]
      }] : s if local.sink_iceberg],
    )
  })
}

# IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない呼び出しを拒む）。実行ロールとタスクロールの両方に付ける
resource "aws_iam_role_policy_attachment" "spark_execution_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.spark_execution.name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "spark_task_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.spark_task.name
  policy_arn = local.perimeter_policy_arn
}

# ---------------------------------------------------------------- outputs（OSS 版だけ）
output "spark_service_names" {
  description = "ECS service of each Spark job, keyed iceberg / splunk / http (the same split as the EMR Serverless jobs of the managed build). No key for a job whose sinks are not in var.sinks"
  value       = { for j, s in aws_ecs_service.spark : j => s.name }
}

output "spark_image" {
  description = "Spark image the tasks run (docker/images/spark/Dockerfile in the <prefix>-spark repository)"
  value       = local.spark_image
}

output "spark_checkpoint_uri" {
  description = "Where the Spark jobs keep their checkpoints (S3A). Each sink adds its own <sink>/ under it"
  value       = local.spark_checkpoint_uri
}

output "spark_log_group_name" {
  description = "CloudWatch Logs group of the Spark tasks (stream prefix spark-<job>)"
  value       = aws_cloudwatch_log_group.spark.name
}

output "spark_list_tasks_commands" {
  description = "List the running task of each Spark job (aws ecs execute-command --task <arn> --container spark --interactive --command bash to go in)"
  value       = { for j, s in aws_ecs_service.spark : j => "aws ecs list-tasks --region ${var.region} --cluster ${aws_ecs_cluster.analytics[0].name} --service-name ${s.name} --query taskArns --output text" }
}
