output "graph_id" {
  description = "Neptune Analytics graph id (g-xxxxxxxxxx). The workloads pass it to boto3 neptune-graph execute_query as graphIdentifier (env NEPTUNE_GRAPH_ID)."
  value       = aws_neptunegraph_graph.graph.id
}

output "graph_arn" {
  description = "Used as the Resource of the neptune-graph IAM statements (IaC/terraform/aws-managed/pipeline/nautobot, IaC/terraform/aws-managed/workflow)"
  value       = aws_neptunegraph_graph.graph.arn
}

output "graph_id_parameter_name" {
  description = "SSM parameter the runtime and the web read at start"
  value       = aws_ssm_parameter.graph_id.name
}

output "next_step" {
  description = "Run on the web EC2 after apply (SSM session), then use the topology tab \"リンクを編集\" to seed the static data"
  value       = "sudo systemctl restart ${local.name_prefix}-web"
}

output "status_function_name" {
  description = "Lambda that writes the firing / resolved alerts of the SNS topic into Neptune as the status property"
  value       = aws_lambda_function.status.function_name
}

output "status_log_group_name" {
  description = "Log group of the status Lambda (one line per alert: what it received and how many elements it updated)"
  value       = aws_cloudwatch_log_group.status.name
}
