# ---------------------------------------------------------------- 土台と stream の state、共有の locals
# マネージド版の locals.tf と同じ名前の locals を、OSS 版で作れるものだけ持つ（マネージド版の locals.tf は MSK・EMR・
# OpenSearch Serverless・AMP を引くのでリンクできない）。リンクしている tables.tf・history.tf・ecs.tf・splunk.tf と、
# spark.tf・opensearch.tf・victoriametrics.tf・grafana.tf はここの locals を読む（OpenSearch と VictoriaMetrics の宛先とパスワードの locals はその .tf が持つ）。
# Splunk は OSS 版でも変えない（設計 005）ので、マネージド版の splunk.tf をそのままリンクし、splunk の locals もマネージド版と同じ値にする

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# VPC / サブネット / SG / バケット / アラートの SNS トピックは IaC/terraform/oss/base/core、Kafka は IaC/terraform/oss/pipeline/stream の state から読む
data "terraform_remote_state" "main" {
  backend = "local"

  config = {
    path = "${path.module}/../../base/core/terraform.tfstate"
  }

  # マネージド版の locals.tf と同じ（SG の ID の無い古い state なら apply の前に止める）
  lifecycle {
    postcondition {
      condition     = can(self.outputs.security_group_ids)
      error_message = "IaC/terraform/aws-managed/base/core の state に security_group_ids が無い（2026-09-29 より前の SG）。先に ops/down.sh で消してから ops/up.sh を打ち直す"
    }
  }
}

data "terraform_remote_state" "stream" {
  backend = "local"

  config = {
    path = "${path.module}/../stream/terraform.tfstate"
  }
}

# EMR のログと Firehose が書けなかった行の置き場（logs のバケット）は IaC/terraform/oss/base/logs。ops/oss/up.sh が base/core より先に作り、
# ops/oss/down.sh は消さない（中身は 7 日で消える）ので、analytics を apply する時点で必ずある（try で包まない）
data "terraform_remote_state" "logs" {
  backend = "local"

  config = {
    path = "${path.module}/../../base/logs/terraform.tfstate"
  }
}

# Spark（docker/images/spark/Dockerfile。ops/oss/up.sh が作って <接頭辞>-spark に push する）と OpenSearch・VictoriaMetrics（ops/oss/up.sh が
# ECR に写す）のイメージは IaC/terraform/oss/base/ecr
data "terraform_remote_state" "ecr" {
  backend = "local"

  config = {
    path = "${path.module}/../../base/ecr/terraform.tfstate"
  }
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  vpc_id = data.terraform_remote_state.main.outputs.vpc_id
  # サブネット a / b / c（この順）。Spark のタスクは先頭から var.emr_az_num 個のどれかに置く
  subnet_ids = data.terraform_remote_state.main.outputs.subnet_ids
  # web の EC2 と同じサブネット（SSM のポートフォワードで画面を開くタスク向け）
  instance_subnet_id = data.terraform_remote_state.main.outputs.instance_subnet_id
  web_instance_id    = try(data.terraform_remote_state.main.outputs.web_instance_id, "")
  # SG は古い state の destroy でも評価できるように try（空のまま apply に進まないよう spark.tf の precondition で止める）
  spark_sg_id   = try(data.terraform_remote_state.main.outputs.security_group_ids["spark"], "")
  grafana_sg_id = try(data.terraform_remote_state.main.outputs.security_group_ids["grafana"], "")
  splunk_sg_id  = try(data.terraform_remote_state.main.outputs.security_group_ids["splunk"], "")
  bucket        = data.terraform_remote_state.main.outputs.assets_bucket_name
  bucket_arn    = "arn:${local.partition}:s3:::${local.bucket}"
  # Firehose が書けなかった行（リンクした history.tf）。OSS 版の Spark のログは CloudWatch（spark.tf の awslogs）なので、ここに落ちるのはそれだけ
  logs_bucket     = data.terraform_remote_state.logs.outputs.logs_bucket_name
  logs_bucket_arn = "arn:${local.partition}:s3:::${local.logs_bucket}"
  # IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない AWS の API を拒む）。NETWORK_PERIMETER=0 か古い state なら空
  perimeter_policy_arn        = try(data.terraform_remote_state.main.outputs.network_perimeter_policy_arn, "")
  perimeter_exempt_principals = try(data.terraform_remote_state.main.outputs.perimeter_exempt_principals, [])
  # Kafka の 3 台（kafka-N.<接頭辞>-stream.internal:9092、PLAINTEXT）。stream が無いと空で、spark.tf の precondition で止める
  bootstrap = try(data.terraform_remote_state.stream.outputs.bootstrap_brokers, "")
  # アラートの SNS トピック（IaC/terraform/aws-managed/base/core の alerts.tf）
  alerts_topic_arn = try(data.terraform_remote_state.main.outputs.alerts_topic_arn, "")
  # SSM のパラメータの ARN の頭（後ろに /<接頭辞>/… を付ける）
  ssm_parameter_arn = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter"

  # checkpoint の置き場はマネージド版と同じ s3://<assets のバケット>/spark/checkpoint/（Spark は S3A で読み書きする。spark.tf）
  s3_prefix        = "spark"
  checkpoint       = "${local.s3_prefix}/checkpoint"
  catalog_name     = "s3tables"
  table_bucket     = "${local.name_prefix}-tables"
  iceberg_table    = "${local.catalog_name}.${var.namespace}.${var.table_name}"
  table_bucket_arn = aws_s3tables_table_bucket.tables.arn

  # 格納先とジョブの分け方はマネージド版の locals.tf と同じ（iceberg / splunk / http = opensearch と prometheus）
  sink_iceberg    = contains(var.sinks, "iceberg")
  sink_opensearch = contains(var.sinks, "opensearch")
  sink_prometheus = contains(var.sinks, "prometheus")
  sink_splunk     = contains(var.sinks, "splunk")
  spark_jobs = { for job, sinks in { iceberg = ["iceberg"], splunk = ["splunk"], http = ["opensearch", "prometheus"] } :
  job => [for s in sinks : s if contains(var.sinks, s)] }
  max_offsets_by_job = { for job, sinks in local.spark_jobs :
  job => join(",", [for s in sinks : "${s}=${var.max_offsets_per_trigger_by_sink[s]}" if contains(keys(var.max_offsets_per_trigger_by_sink), s)]) }

  # splunk: マネージド版と同じ Splunk Enterprise を ECS で立てる（リンクした splunk.tf。HEC は VPC の中の splunk.<名前空間>:8088、
  # クラスターなら splunk-idx.<名前空間>:8088）。値はマネージド版の locals.tf と同じ。
  # HEC の token は Splunk のタスクも Spark のタスク（spark.tf）も ECS の secrets で同じ SSM の SecureString から SPLUNK_HEC_TOKEN として受ける
  splunk_on_ecs              = local.sink_splunk
  splunk_hec_url             = local.splunk_cluster ? "https://splunk-idx.${local.service_namespace}:8088" : "https://splunk.${local.service_namespace}:8088"
  splunk_skip_tls_verify     = true
  splunk_token_parameter     = var.splunk_hec_token_parameter != "" ? var.splunk_hec_token_parameter : "/${local.name_prefix}/splunk/hec-token"
  splunk_token_parameter_arn = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.splunk_token_parameter}"
  splunk_password_parameter  = "/${local.name_prefix}/splunk/admin-password"

  # Grafana（grafana.tf）。作るかどうかと admin のパスワードの名前はマネージド版の locals.tf と同じ（create_grafana は ops/up.sh が true にする）
  grafana_password_parameter = "/${local.name_prefix}/grafana/admin-password"
  create_grafana             = var.create_grafana && (local.sink_prometheus || local.sink_opensearch)

  # ECS のクラスタと Cloud Map の名前空間（ecs.tf）。OSS 版では Spark がいつも ECS で動くので、いつも作る
  create_ecs        = true
  service_namespace = "${local.name_prefix}.internal"

  metric_topics    = join(",", var.metric_topics)
  log_topics       = join(",", var.log_topics)
  opensearch_index = "snmp-logs" # app/spark/snmp_sinks.py の OPENSEARCH_INDEX と同じ
}
