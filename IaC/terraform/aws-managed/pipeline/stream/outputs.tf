# msk_cluster_arn は msk.tf（OSS 版には無い）
output "bootstrap_brokers" {
  description = "Bootstrap brokers of Kafka (local.kafka_bootstrap_brokers): the MSK SASL/IAM ones (9098, also in SSM /<prefix>/msk-bootstrap) in the managed build, kafka-1..3 PLAINTEXT (9092) in the OSS build"
  value       = local.kafka_bootstrap_brokers
}

output "telegraf_cluster_name" {
  description = "ECS cluster of the Telegraf tasks (dialout and dialin), syslog-ng and GoFlow2 (and Kafbat UI)"
  value       = aws_ecs_cluster.telegraf.name
}

output "telegraf_dialout_service_name" {
  description = "ECS service of the Telegraf dial-out task (traps behind the NLB)"
  value       = aws_ecs_service.telegraf_dialout.name
}

output "telegraf_dialin_service_name" {
  description = "ECS service of the Telegraf dial-in task (gNMI subscriptions and SNMP polling). The Nautobot job forces a new deployment of it after it rewrites the targets."
  value       = aws_ecs_service.telegraf_dialin.name
}

output "telegraf_dialin_target_parameters" {
  description = "SSM parameters (String) holding the targets of the dial-in task, keyed gnmi-targets / snmp-agents. Under .../nautobot/ when dialin_targets_from_nautobot (the Nautobot job writes them), else .../lab/."
  value       = local.dialin_target_names
}

output "telegraf_dialin_targets_source" {
  description = "Where the dial-in targets come from: lab or nautobot"
  value       = local.dialin_target_source
}

output "telegraf_address" {
  description = "Private IP of the collector NLB - Telegraf dial-out, syslog-ng and GoFlow2 (also in SSM /<prefix>/telegraf-address; the DNAT target of lab.sh forward)"
  value       = data.aws_network_interface.telegraf_dialout_lb.private_ip
}

output "telegraf_dialout_dns_name" {
  description = "DNS name of the Telegraf dial-out NLB (internal; resolves to its address in each of the telegraf_az_num subnets). Point real devices (trap / syslog / NetFlow / sFlow) at this name - the lab keeps using telegraf_address in subnet a"
  value       = aws_lb.telegraf_dialout.dns_name
}

output "telegraf_log_group_name" {
  description = "CloudWatch Logs group of the Telegraf tasks (streams dialout/... and dialin/...)"
  value       = aws_cloudwatch_log_group.telegraf.name
}

output "telegraf_dialout_list_tasks_command" {
  description = "Prints the ARN of the running Telegraf dial-out task"
  value       = "aws ecs list-tasks --region ${var.region} --cluster ${aws_ecs_cluster.telegraf.name} --service-name ${aws_ecs_service.telegraf_dialout.name} --query taskArns --output text"
}

output "syslog_ng_service_name" {
  description = "ECS service of syslog-ng (device syslog behind the NLB, udp 5140, to the logs topic; cycle 012)"
  value       = aws_ecs_service.syslog_ng.name
}

output "goflow2_service_name" {
  description = "ECS service of GoFlow2 (NetFlow udp 2055 and sFlow udp 6343 behind the NLB, to the flows topic; cycle 012)"
  value       = aws_ecs_service.goflow2.name
}

output "syslog_ng_log_group_name" {
  description = "CloudWatch Logs group of the syslog-ng task"
  value       = aws_cloudwatch_log_group.syslog_ng.name
}

output "goflow2_log_group_name" {
  description = "CloudWatch Logs group of the GoFlow2 task"
  value       = aws_cloudwatch_log_group.goflow2.name
}

output "syslog_ng_list_tasks_command" {
  description = "Prints the ARN of the running syslog-ng task"
  value       = "aws ecs list-tasks --region ${var.region} --cluster ${aws_ecs_cluster.telegraf.name} --service-name ${aws_ecs_service.syslog_ng.name} --query taskArns --output text"
}

output "goflow2_list_tasks_command" {
  description = "Prints the ARN of the running GoFlow2 task"
  value       = "aws ecs list-tasks --region ${var.region} --cluster ${aws_ecs_cluster.telegraf.name} --service-name ${aws_ecs_service.goflow2.name} --query taskArns --output text"
}

output "telegraf_dialin_list_tasks_command" {
  description = "Prints the ARN of the running Telegraf dial-in task (use its last part as TASK_ID of telegraf_exec_command)"
  value       = "aws ecs list-tasks --region ${var.region} --cluster ${aws_ecs_cluster.telegraf.name} --service-name ${aws_ecs_service.telegraf_dialin.name} --query taskArns --output text"
}

output "telegraf_exec_command" {
  description = "Run on the user's PC (AWS CLI v2 + Session Manager plugin) with TASK_ID of the dial-in task. tg gnmi subscribes for 20 seconds, tg test polls SNMP once (only with snmp_poll = true); neither writes to Kafka"
  value       = "aws ecs execute-command --region ${var.region} --cluster ${aws_ecs_cluster.telegraf.name} --task TASK_ID --container telegraf --interactive --command 'tg gnmi'"
}

output "kafka_ui_port_forward_command" {
  description = "Open Kafbat UI at http://localhost:8082 (SSM port forward to 127.0.0.1:8082 of the web EC2, where its Docker container listens; user admin, password from kafka_ui_password_command). Port 8082 because the web uses 8080 and Nautobot 8081"
  value       = "aws ssm start-session --region ${var.region} --target ${local.web_instance_id} --document-name AWS-StartPortForwardingSession --parameters portNumber=8082,localPortNumber=8082"
}

output "kafka_ui_password_command" {
  description = "Print the Kafbat UI admin password (SSM SecureString created by ops/up.sh)"
  value       = "aws ssm get-parameter --region ${var.region} --name ${local.kafka_ui_password_parameter} --with-decryption --query Parameter.Value --output text"
}
