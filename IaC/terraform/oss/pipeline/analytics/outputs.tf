# マネージド版の outputs.tf のうち、ほかのルート（workflow・graph・agent）と ops が読む共通の output（名前と中身を同じにする）。
# EMR Serverless・OpenSearch Serverless・AMP の output は無い。Spark の output は spark.tf、OpenSearch と VictoriaMetrics の output はその .tf に置く
# （共通の opensearch_index も、opensearch.tf が出すのでここには置かない）。Splunk（リンクした splunk.tf）と Grafana（grafana.tf）の output はマネージド版と同じ

output "table_bucket_arn" {
  description = "S3 Tables table bucket (the Iceberg warehouse of the Spark catalog; IaC/terraform/aws-managed/workflow appends proposal_events here)"
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
  description = "Where the streaming jobs store the messages (var.sinks: iceberg = all topics, opensearch = log topics, prometheus = metric topics, splunk = all topics)"
  value       = var.sinks
}

output "splunk_hec_url" {
  description = "HTTP Event Collector the splunk sink posts every topic to (empty unless sinks has splunk). https://splunk.<namespace>:8088 of the Splunk on ECS (splunk.tf)"
  value       = local.sink_splunk ? local.splunk_hec_url : ""
}

output "splunk_token_parameter" {
  description = "SSM SecureString parameter holding the HEC token (empty unless sinks has splunk). The Spark task and the Splunk task both get it as SPLUNK_HEC_TOKEN through the ECS secrets. ops/up.sh creates it. Terraform never reads the value"
  value       = local.sink_splunk ? local.splunk_token_parameter : ""
}

output "analytics_cluster_name" {
  description = "ECS cluster of the Spark tasks (and the OpenSearch / VictoriaMetrics / Splunk tasks)"
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

output "splunk_cm_service_name" {
  description = "ECS service of the Splunk cluster manager (empty unless splunk_az_num is 2 or 3)"
  value       = local.splunk_on_ecs && local.splunk_cluster ? aws_ecs_service.splunk_cm[0].name : ""
}

output "splunk_idx_service_name" {
  description = "ECS service of the Splunk indexers, one task per AZ (empty unless splunk_az_num is 2 or 3)"
  value       = local.splunk_on_ecs && local.splunk_cluster ? aws_ecs_service.splunk_idx[0].name : ""
}

output "splunk_cm_port_forward_command" {
  description = "Open the Web UI of the Splunk cluster manager (indexer clustering status) at http://localhost:8001 through the web EC2 (SSM port forward; same admin password). Empty unless splunk_az_num is 2 or 3"
  value       = local.splunk_on_ecs && local.splunk_cluster ? "aws ssm start-session --region ${var.region} --target ${local.web_instance_id} --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{\"host\":[\"splunk-cm.${local.service_namespace}\"],\"portNumber\":[\"8000\"],\"localPortNumber\":[\"8001\"]}'" : ""
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

output "service_namespace" {
  description = "Cloud Map private DNS namespace of the tasks of this root (<name>.<namespace>)"
  value       = local.service_namespace
}

output "table_namespace" {
  description = "S3 Tables namespace of the tables (IaC/terraform/aws-managed/workflow appends proposal_events in it)"
  value       = aws_s3tables_namespace.nwc.namespace
}

output "proposal_events_table_name" {
  description = "Audit trail of proposals (created / approved / rejected / applied / verified ...), written by the IaC/terraform/aws-managed/workflow worker"
  value       = aws_s3tables_table.proposal_events.name
}

output "proposal_events_table_arn" {
  description = "ARN of proposal_events (IaC/terraform/aws-managed/workflow lets the worker append to it)"
  value       = aws_s3tables_table.proposal_events.arn
}

output "alert_events_stream_name" {
  description = "Firehose stream the status Lambda of IaC/terraform/aws-managed/pipeline/graph sends the alert notifications to (history.tf; graph builds the same fixed name)"
  value       = aws_kinesis_firehose_delivery_stream.alert_events.name
}

output "alert_events_table_name" {
  description = "History of the alert notifications (Grafana / Splunk, firing and resolved), appended by the Firehose stream"
  value       = aws_s3tables_table.alert_events.name
}

output "alert_events_table_arn" {
  description = "ARN of alert_events (IaC/terraform/aws-managed/workflow lets the query_history tool read only this table)"
  value       = aws_s3tables_table.alert_events.arn
}

output "athena_workgroup" {
  description = "Athena workgroup the query_history and list_proposals of IaC/terraform/aws-managed/workflow and the web approve tab run their queries in (results in the Athena managed storage)"
  value       = aws_athena_workgroup.history.name
}

output "athena_catalog" {
  description = "How Athena names the table bucket (s3tablescatalog/<table bucket>). SQL: \"<athena_catalog>\".\"<table_namespace>\".\"alert_events\" (or proposal_events)"
  value       = local.athena_catalog
}
