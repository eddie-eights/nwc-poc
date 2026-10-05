# マネージド版の outputs.tf のうち、ほかのルート（workflow・graph・agent）と ops が読む共通の output（名前と中身を同じにする）。
# EMR Serverless・OpenSearch Serverless・AMP・Splunk の output は無い。Spark の output は spark.tf、OpenSearch と VictoriaMetrics の output はその .tf に置く
# （共通の opensearch_index も、opensearch.tf が出すのでここには置かない）

output "table_bucket_arn" {
  description = "S3 Tables table bucket (the Iceberg warehouse of the Spark catalog; terraform/workflow appends proposal_events here)"
  value       = local.table_bucket_arn
}

output "table_arn" {
  description = "The Iceberg table the streaming job appends to. Empty unless sinks has iceberg"
  value       = local.sink_iceberg ? aws_s3tables_table.raw_telemetry[0].arn : ""
}

output "table_identifier" {
  description = "How Spark SQL names the table (catalog.namespace.table). Empty unless sinks has iceberg"
  value       = local.sink_iceberg ? local.iceberg_table : ""
}

output "list_tables_command" {
  description = "See the tables (raw_telemetry, proposal_events, alert_events) in S3 Tables"
  value       = "aws s3tables list-tables --region ${var.region} --table-bucket-arn ${local.table_bucket_arn} --namespace ${var.namespace}"
}

output "sinks" {
  description = "Where the streaming jobs store the messages (var.sinks: iceberg = all topics, opensearch = log topics, prometheus = metric topics)"
  value       = var.sinks
}

output "analytics_cluster_name" {
  description = "ECS cluster of the Spark tasks (and the OpenSearch / VictoriaMetrics tasks)"
  value       = local.create_ecs ? aws_ecs_cluster.analytics[0].name : ""
}

output "service_namespace" {
  description = "Cloud Map private DNS namespace of the tasks of this root (<name>.<namespace>)"
  value       = local.service_namespace
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

output "alert_events_stream_name" {
  description = "Firehose stream the status Lambda of terraform/pipeline/graph sends the alert notifications to (history.tf; graph builds the same fixed name)"
  value       = aws_kinesis_firehose_delivery_stream.alert_events.name
}

output "alert_events_table_name" {
  description = "History of the alert notifications (Grafana / Splunk, firing and resolved), appended by the Firehose stream"
  value       = aws_s3tables_table.alert_events.name
}

output "alert_events_table_arn" {
  description = "ARN of alert_events (terraform/workflow lets the query_history tool read only this table)"
  value       = aws_s3tables_table.alert_events.arn
}

output "athena_workgroup" {
  description = "Athena workgroup the query_history and list_proposals of terraform/workflow and the web approve tab run their queries in (results in the Athena managed storage)"
  value       = aws_athena_workgroup.history.name
}

output "athena_catalog" {
  description = "How Athena names the table bucket (s3tablescatalog/<table bucket>). SQL: \"<athena_catalog>\".\"<table_namespace>\".\"alert_events\" (or proposal_events)"
  value       = local.athena_catalog
}
