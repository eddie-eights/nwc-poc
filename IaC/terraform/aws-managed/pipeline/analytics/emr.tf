# ---------------------------------------------------------------- EMR Serverless (Spark)
# アプリケーションは器だけで、ジョブが動いていなければ課金されない（pre-initialized capacity は持たない）。
# ストリーミングのジョブは ops/up.sh が start-job-run で起こす（Terraform にジョブのリソースは無い。ジョブは格納先で 3 つ。手打ちは output の job_driver_json_<iceberg|splunk|http> / configuration_overrides_json）。
# arm64 なのは web の EC2 と同じ理由（単価が x86 より約 20% 低い。lab の EC2 は TRex が amd64 だけなので x86_64）
resource "aws_emrserverless_application" "spark" {
  name          = "${local.name_prefix}-spark"
  release_label = var.emr_release_label
  type          = "spark"
  architecture  = "ARM64"

  auto_start_configuration {
    enabled = true
  }

  auto_stop_configuration {
    enabled              = true
    idle_timeout_minutes = var.idle_timeout_minutes
  }

  maximum_capacity {
    cpu    = var.max_cpu
    memory = var.max_memory
  }

  # SG は IaC/terraform/aws-managed/base/core の spark（network.tf）。サブネットは var.emr_az_num の AZ（a から）
  network_configuration {
    subnet_ids         = slice(local.subnet_ids, 0, var.emr_az_num)
    security_group_ids = [local.spark_sg_id]
  }

  # scheduler_configuration（max_concurrent_runs / queue_timeout_minutes）は書かない。API が既定値（15 / 360）を返すので、
  # 書かないと provider（aws 6.64）が毎回 plan に差分を出し、書くと queue_timeout_minutes がアプリ STARTED 中は更新できず apply が 400 で落ちる
  # （2026-09-17 に Mac で両方実測。ジョブが動いていると stop-application もできない）。ignore_changes で差分そのものを見ないようにする
  lifecycle {
    ignore_changes = [scheduler_configuration]

    precondition {
      condition     = local.msk_cluster_arn != "" && local.bootstrap != ""
      error_message = "IaC/terraform/aws-managed/pipeline/stream の state（IaC/terraform/aws-managed/pipeline/stream/terraform.tfstate）から msk_cluster_arn / bootstrap_brokers が読めない。IaC/terraform/aws-managed/pipeline/stream を先に apply する。"
    }
  }

  tags = { Name = "${local.name_prefix}-spark" }
}

# ジョブのドライバーのログ。start-job-run の monitoringConfiguration で指す（output の configuration_overrides_json）
resource "aws_cloudwatch_log_group" "emr" {
  name              = local.log_group
  retention_in_days = var.log_retention_days
}
