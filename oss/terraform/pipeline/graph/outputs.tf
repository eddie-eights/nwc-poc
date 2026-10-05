# マネージド版の outputs.tf の OSS 版。graph_id と graph_arn（Neptune）の代わりに、Neo4j の URI と SSM のパラメータの名前を出す。
# terraform/workflow（Worker）は neo4j_uri と neo4j_password_parameter をこの state から読む
output "neo4j_uri" {
  description = "Bolt URI of the Neo4j task (Cloud Map name). The workloads pass it to agent/graph.py and workflow/awsio.py as NEO4J_URI with GRAPH_BACKEND=neo4j."
  value       = local.neo4j_uri
}

output "neo4j_uri_parameter_name" {
  description = "SSM parameter the runtime and the web read at start (instead of neptune-graph-id)"
  value       = aws_ssm_parameter.neo4j_uri.name
}

output "neo4j_password_parameter" {
  description = "SSM SecureString with the password of the neo4j user (created by the OSS ops/up.sh, not by Terraform). The workloads read it as NEO4J_PASSWORD."
  value       = local.neo4j_password_parameter
}

output "neo4j_layer_arn" {
  description = "Lambda layer with the Neo4j Python driver (graph/requirements-oss.txt, python3.13 arm64). The tools Lambda of terraform/workflow attaches the same layer so agent/graph.py can import neo4j."
  value       = aws_lambda_layer_version.neo4j.arn
}

output "graph_cluster_name" {
  description = "ECS cluster of the Neo4j task"
  value       = aws_ecs_cluster.graph.name
}

output "neo4j_service_name" {
  description = "ECS service of the Neo4j task. The graph is empty again after the task is replaced: run ops/sync-graph.sh"
  value       = aws_ecs_service.neo4j.name
}

output "neo4j_log_group_name" {
  description = "Log group of the Neo4j task"
  value       = aws_cloudwatch_log_group.neo4j.name
}

output "next_step" {
  description = "Run on the web EC2 after apply (SSM session) so the web reads neo4j-uri, then load the topology with ops/sync-graph.sh (also after every replacement of the Neo4j task: its data lives in the task)"
  value       = "sudo systemctl restart ${local.name_prefix}-web"
}

output "neo4j_browser_port_forward_command" {
  description = "Open Neo4j Browser at http://localhost:7474 through the web EC2 (SSM port forward). The browser also needs neo4j_bolt_port_forward_command in another terminal; connect to bolt://localhost:7687 as neo4j with the password of neo4j_password_parameter"
  value       = "aws ssm start-session --region ${var.region} --target ${local.web_instance_id} --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{\"host\":[\"${local.neo4j_host}\"],\"portNumber\":[\"7474\"],\"localPortNumber\":[\"7474\"]}'"
}

output "neo4j_bolt_port_forward_command" {
  description = "Forward Bolt (7687) of the Neo4j task to localhost:7687 through the web EC2 (for Neo4j Browser and cypher-shell on the user's PC)"
  value       = "aws ssm start-session --region ${var.region} --target ${local.web_instance_id} --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{\"host\":[\"${local.neo4j_host}\"],\"portNumber\":[\"7687\"],\"localPortNumber\":[\"7687\"]}'"
}

output "status_function_name" {
  description = "Lambda that writes the firing / resolved alerts of the SNS topic into Neo4j as the status property"
  value       = aws_lambda_function.status.function_name
}

output "status_log_group_name" {
  description = "Log group of the status Lambda (one line per alert: what it received and how many elements it updated)"
  value       = aws_cloudwatch_log_group.status.name
}
