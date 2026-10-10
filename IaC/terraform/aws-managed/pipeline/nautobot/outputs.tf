output "cluster_name" {
  description = "ECS cluster of the Nautobot task"
  value       = aws_ecs_cluster.nautobot.name
}

output "service_name" {
  description = "ECS service of the Nautobot task (web + Celery worker + Redis)"
  value       = aws_ecs_service.nautobot.name
}

output "log_group_name" {
  description = "Log group of the task. Streams: web/ (migration, bootstrap, uwsgi), worker/ (the jobs), redis/"
  value       = aws_cloudwatch_log_group.nautobot.name
}

output "url" {
  description = "URL of Nautobot inside the VPC (also in SSM /<prefix>/nautobot/url)"
  value       = local.url
}

output "sync_targets" {
  description = "What the NWC job writes: gnmic (the gNMI targets in SSM) and / or neptune (neo4j in the OSS variant). Empty when neither stream nor graph is there."
  value       = compact([local.gnmic_from_nautobot ? "gnmic" : "", local.neptune_graph_id != "" ? "neptune" : "", local.graph_neo4j ? "neo4j" : ""])
}

# IaC/terraform/aws-managed/workflow が読む（cycle 036）。Temporal の履歴（DB temporal / temporal_visibility）もこの RDS に置く。
# temporal のサーバーのコンテナが起動時に master（nautobot）でロール temporal と DB を作るので、master のパスワードのパラメータ名も渡す（値は出さない）
output "db_address" {
  description = "Hostname of the RDS for PostgreSQL instance (Nautobot, and the Temporal history of the workflow root)"
  value       = aws_db_instance.nautobot.address
}

output "db_port" {
  description = "Port of the RDS for PostgreSQL instance"
  value       = aws_db_instance.nautobot.port
}

output "db_password_parameter" {
  description = "SSM parameter name of the master password (SecureString created by ops/up.sh; the value is not in the state)"
  value       = local.secret_parameters["db-password"]
}

output "port_forward_command" {
  description = "Opens Nautobot at http://localhost:8081/ through the web EC2 (SSM port forwarding)"
  value       = "aws ssm start-session --region ${var.region} --target ${local.web_instance_id} --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{\"host\":[\"nautobot.${local.service_namespace}\"],\"portNumber\":[\"8080\"],\"localPortNumber\":[\"8081\"]}'"
}

output "password_command" {
  description = "Prints the password of the Nautobot superuser (var.admin_user)"
  value       = "aws ssm get-parameter --region ${var.region} --name ${local.secret_parameters["admin-password"]} --with-decryption --query Parameter.Value --output text"
}

output "list_tasks_command" {
  description = "Lists the running task (the last part of the ARN is TASK_ID of exec_command)"
  value       = "aws ecs list-tasks --region ${var.region} --cluster ${aws_ecs_cluster.nautobot.name} --service-name ${aws_ecs_service.nautobot.name}"
}

output "exec_command" {
  description = "Opens a shell in the web container (ECS Exec). Inside: nautobot-server nbshell"
  value       = "aws ecs execute-command --region ${var.region} --cluster ${aws_ecs_cluster.nautobot.name} --task TASK_ID --container web --interactive --command /bin/bash"
}
