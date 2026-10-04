output "application_id" {
  description = "EMR Serverless application id (ops/up.sh starts the streaming job on it)"
  value       = aws_emrserverless_application.spark.id
}

output "runtime_role_arn" {
  description = "Execution role passed to start-job-run"
  value       = aws_iam_role.emr.arn
}

output "table_bucket_arn" {
  description = "S3 Tables table bucket (the Iceberg warehouse of the Spark catalog; terraform/workflow appends proposal_events here)"
  value       = local.table_bucket_arn
}

output "table_arn" {
  description = "The Iceberg table the streaming job appends to. Empty unless sinks has iceberg"
  value       = local.sink_iceberg ? aws_s3tables_table.snmp_metrics[0].arn : ""
}

output "table_identifier" {
  description = "How Spark SQL names the table (catalog.namespace.table). Empty unless sinks has iceberg"
  value       = local.sink_iceberg ? local.iceberg_table : ""
}

output "script_s3_uri" {
  description = "Where ops/up.sh puts spark/snmp_sinks.py"
  value       = "s3://${local.bucket}/${local.script_key}"
}

output "jars_s3_prefix" {
  description = "Where ops/up.sh puts the Kafka / MSK IAM / S3 Tables jars"
  value       = "s3://${local.bucket}/${local.jars_prefix}/"
}

# start-job-run の引数。Spark のジョブは格納先で 3 つ（local.spark_jobs: iceberg / splunk / http = opensearch と prometheus）。
# ops/up.sh はジョブごとにこの出力をそのまま --job-driver に渡す（手順 7-5）。そのジョブの格納先が var.sinks に無ければ空文字で、ジョブを起こさない
locals {
  job_drivers = { for job, sinks in local.spark_jobs : job => length(sinks) == 0 ? "" : jsonencode({
    sparkSubmit = {
      entryPoint = "s3://${local.bucket}/${local.script_key}"
      # 「cond ? [..] : []」は両辺の型が揃わず validate が落ちるので for … if で絞る
      entryPointArguments = concat(
        ["--bootstrap", local.bootstrap, "--checkpoint", local.checkpoint_uri, "--sinks", join(",", sinks), "--region", var.region,
        "--metric-topics", local.metric_topics, "--log-topics", local.log_topics],
        # Kafka の 1 回のトリガーに 1 つのクエリが読む件数の上限。共通の値はどのジョブにも渡し、格納先ごとの値はそのジョブの格納先の分だけ渡す
        ["--max-offsets-per-trigger", tostring(var.max_offsets_per_trigger)],
        [for a in ["--max-offsets-per-trigger-by-sink", local.max_offsets_by_job[job]] : a if local.max_offsets_by_job[job] != ""],
        # HTTP の格納先へ送る所。既定の driver では渡さない（ジョブの引数が変わらず、ops/up.sh が起こし直さない）。iceberg のジョブには要らない
        [for a in ["--http-send", var.http_send] : a if var.http_send != "driver" && job != "iceberg"],
        [for a in ["--iceberg-table", local.iceberg_table] : a if contains(sinks, "iceberg")],
        [for a in ["--opensearch-endpoint", local.opensearch_endpoint, "--opensearch-index", local.opensearch_index] : a if contains(sinks, "opensearch")],
        [for a in ["--prometheus-url", local.prometheus_remote_write_url] : a if contains(sinks, "prometheus")],
        # token の値は渡さない（SSM のパラメータ名だけ。ジョブが起動時に読む）
        [for a in ["--splunk-hec-url", local.splunk_hec_url, "--splunk-token-parameter", local.splunk_token_parameter, "--splunk-index", var.splunk_index] : a if contains(sinks, "splunk")],
        [for a in ["--splunk-skip-verify"] : a if contains(sinks, "splunk") && local.splunk_skip_tls_verify],
      )
      # Iceberg のカタログの設定はいつも渡す（カタログは最初に使うときに開くので、iceberg を選ばなければ S3 Tables の API を呼ばない）
      sparkSubmitParameters = join(" ", concat(
        ["--conf spark.jars=s3://${local.bucket}/${local.jars_prefix}/*.jar",
          "--conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
          "--conf spark.sql.catalog.${local.catalog_name}=org.apache.iceberg.spark.SparkCatalog",
          "--conf spark.sql.catalog.${local.catalog_name}.catalog-impl=software.amazon.s3tables.iceberg.S3TablesCatalog",
        "--conf spark.sql.catalog.${local.catalog_name}.warehouse=${local.table_bucket_arn}"],
        ["--conf spark.driver.cores=1",
          "--conf spark.driver.memory=2g",
          "--conf spark.executor.cores=1",
          "--conf spark.executor.memory=2g",
          "--conf spark.executor.instances=2",
        "--conf spark.dynamicAllocation.enabled=false"],
      ))
    }
  }) }
}

output "job_driver_json_iceberg" {
  description = "jobDriver of the job sinks-s3iceberg (all topics to S3 Tables). Empty unless sinks has iceberg"
  value       = local.job_drivers["iceberg"]
}

output "job_driver_json_splunk" {
  description = "jobDriver of the job sinks-splunk (all topics to the Splunk HEC). Empty unless sinks has splunk"
  value       = local.job_drivers["splunk"]
}

output "job_driver_json_http" {
  description = "jobDriver of the job sinks-grafana (log topics to OpenSearch, metric topics to Prometheus; only those in sinks). Empty unless sinks has opensearch or prometheus"
  value       = local.job_drivers["http"]
}

output "configuration_overrides_json" {
  description = "configurationOverrides for start-job-run (driver logs to CloudWatch, worker logs to the asset bucket, no EMR managed storage)"
  value = jsonencode({
    monitoringConfiguration = {
      s3MonitoringConfiguration                 = { logUri = "s3://${local.bucket}/${local.logs_prefix}/" }
      managedPersistenceMonitoringConfiguration = { enabled = false }
      # CloudWatch Logs へは terraform/base/core の logs のエンドポイントを通る（無い VPC で NAT も無いとジョブが
      # 「Unable to push logs ... Connect timeout on endpoint URL: https://logs...」で FAILED になる。2026-09-17 に実測）
      cloudWatchLoggingConfiguration = {
        enabled      = var.cloudwatch_logging
        logGroupName = aws_cloudwatch_log_group.emr.name
        logTypes     = { SPARK_DRIVER = ["stdout", "stderr"] }
      }
    }
  })
}

output "list_job_runs_command" {
  description = "See whether the streaming job is running"
  value       = "aws emr-serverless list-job-runs --region ${var.region} --application-id ${aws_emrserverless_application.spark.id} --query 'jobRuns[].[id,state,name]' --output table"
}

output "list_tables_command" {
  description = "See the tables (snmp_metrics, proposal_events) in S3 Tables"
  value       = "aws s3tables list-tables --region ${var.region} --table-bucket-arn ${local.table_bucket_arn} --namespace ${var.namespace}"
}

output "log_group_name" {
  description = "CloudWatch log group of the job driver"
  value       = aws_cloudwatch_log_group.emr.name
}

output "sinks" {
  description = "Where the streaming job stores the messages (var.sinks: iceberg = all topics, opensearch = log topics, prometheus = metric topics, splunk = all topics)"
  value       = var.sinks
}

output "splunk_hec_url" {
  description = "HTTP Event Collector the splunk sink posts every topic to (empty unless sinks has splunk). https://splunk.<namespace>:8088 of the Splunk on ECS (splunk.tf)"
  value       = local.sink_splunk ? local.splunk_hec_url : ""
}

output "splunk_token_parameter" {
  description = "SSM SecureString parameter the job (and the Splunk task) reads the HEC token from (empty unless sinks has splunk). ops/up.sh creates it. Terraform never reads the value"
  value       = local.sink_splunk ? local.splunk_token_parameter : ""
}

output "analytics_cluster_name" {
  description = "ECS cluster of the Grafana / Splunk tasks (empty when neither runs)"
  value       = local.create_ecs ? aws_ecs_cluster.analytics[0].name : ""
}

output "splunk_service_name" {
  description = "ECS service of the Splunk task (empty unless the Splunk runs on ECS)"
  value       = local.splunk_on_ecs ? aws_ecs_service.splunk[0].name : ""
}

output "splunk_port_forward_command" {
  description = "Open the Splunk Web UI at http://localhost:8000 through the web EC2 (SSM port forward; user admin, password from splunk_password_command). Empty unless the Splunk runs on ECS"
  value       = local.splunk_on_ecs ? "aws ssm start-session --region ${var.region} --target ${local.web_instance_id} --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{\"host\":[\"splunk.${local.service_namespace}\"],\"portNumber\":[\"8000\"],\"localPortNumber\":[\"8000\"]}'" : ""
}

output "splunk_password_command" {
  description = "Print the Splunk admin password (SSM SecureString created by ops/up.sh). Empty unless the Splunk runs on ECS"
  value       = local.splunk_on_ecs ? "aws ssm get-parameter --region ${var.region} --name ${local.splunk_password_parameter} --with-decryption --query Parameter.Value --output text" : ""
}

output "grafana_service_name" {
  description = "ECS service of the Grafana task (empty unless create_grafana with prometheus or opensearch in sinks)"
  value       = local.create_grafana ? aws_ecs_service.grafana[0].name : ""
}

output "grafana_port_forward_command" {
  description = "Open Grafana at http://localhost:3000 through the web EC2 (SSM port forward; user admin, password from grafana_password_command). Empty unless Grafana runs"
  value       = local.create_grafana ? "aws ssm start-session --region ${var.region} --target ${local.web_instance_id} --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{\"host\":[\"grafana.${local.service_namespace}\"],\"portNumber\":[\"3000\"],\"localPortNumber\":[\"3000\"]}'" : ""
}

output "grafana_password_command" {
  description = "Print the Grafana admin password (SSM SecureString created by ops/up.sh). Empty unless Grafana runs"
  value       = local.create_grafana ? "aws ssm get-parameter --region ${var.region} --name ${local.grafana_password_parameter} --with-decryption --query Parameter.Value --output text" : ""
}

output "opensearch_collection_endpoint" {
  description = "OpenSearch Serverless collection the log topics go to (empty unless sinks has opensearch). Reachable only from inside the VPC"
  value       = local.opensearch_endpoint
}

output "prometheus_workspace_id" {
  description = "Amazon Managed Service for Prometheus workspace the metric topics go to (empty unless sinks has prometheus)"
  value       = local.sink_prometheus ? aws_prometheus_workspace.metrics[0].id : ""
}

output "prometheus_remote_write_url" {
  description = "remote write URL the job posts to (empty unless sinks has prometheus)"
  value       = local.prometheus_remote_write_url
}

output "table_namespace" {
  description = "S3 Tables namespace of the tables (terraform/workflow appends proposal_events in it)"
  value       = aws_s3tables_namespace.netops.namespace
}

output "proposal_events_table_name" {
  description = "Audit trail of proposals (created / approved / rejected / applied / verified ...), written by the terraform/workflow worker"
  value       = aws_s3tables_table.proposal_events.name
}

output "proposal_events_table_arn" {
  description = "ARN of proposal_events (terraform/workflow lets the worker append to it)"
  value       = aws_s3tables_table.proposal_events.arn
}

output "opensearch_collection_name" {
  description = "Name of the OpenSearch Serverless collection (terraform/workflow adds a read-only data access policy for the tools). Empty unless sinks has opensearch"
  value       = local.sink_opensearch ? aws_opensearchserverless_collection.logs[0].name : ""
}

output "opensearch_collection_arn" {
  description = "ARN of the OpenSearch Serverless collection (aoss:APIAccessAll for the tools Lambda). Empty unless sinks has opensearch"
  value       = local.sink_opensearch ? aws_opensearchserverless_collection.logs[0].arn : ""
}

output "opensearch_index" {
  description = "Index the log topics go to"
  value       = local.opensearch_index
}

output "prometheus_workspace_arn" {
  description = "ARN of the Prometheus workspace (aps:QueryMetrics for the tools Lambda). Empty unless sinks has prometheus"
  value       = local.sink_prometheus ? aws_prometheus_workspace.metrics[0].arn : ""
}

output "prometheus_query_url" {
  description = "Query endpoint of the workspace (PromQL over HTTP with SigV4, or Grafana data source). Empty unless sinks has prometheus"
  value       = local.sink_prometheus ? "${aws_prometheus_workspace.metrics[0].prometheus_endpoint}api/v1/query" : ""
}
