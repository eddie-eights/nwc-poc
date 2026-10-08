output "agent_repository_url" {
  description = "Push the agent image here (step 2 of ops/up.sh). IaC/terraform/aws-managed/base/core reads it from this state."
  value       = aws_ecr_repository.agent.repository_url
}

output "srlinux_repository_url" {
  description = "Push ghcr.io/nokia/srlinux:26.7.2 (arm64) here with tag 26.7.2 (step 2 of ops/up.sh). Empty when create_lab_repositories is false."
  value       = try(aws_ecr_repository.lab["srlinux"].repository_url, "")
}

output "multitool_repository_url" {
  description = "Push ghcr.io/srl-labs/network-multitool:v0.10.0 here with tag v0.10.0 (step 2 of ops/up.sh)."
  value       = try(aws_ecr_repository.lab["multitool"].repository_url, "")
}

output "worker_repository_url" {
  description = "Build app/temporal/ and push it here with the same tag as the agent image (step 2 of ops/up.sh). Empty when create_workflow_repositories is false."
  value       = try(aws_ecr_repository.workflow["worker"].repository_url, "")
}

output "temporal_repository_url" {
  description = "Push temporalio/temporal:1.9.1 here with tag 1.9.1 (step 2 of ops/up.sh)."
  value       = try(aws_ecr_repository.workflow["temporal"].repository_url, "")
}

# pipeline の 6 つも lab / workflow と同じく try() で包む。state を失って残った ECR を 1 本ずつ terraform import すると、まだ state に無いキーを
# 引いた output が Invalid index で落ちる（2026-10-08 の OSS 版の検証の「不具合」3）。読む側は try() で空を受け、precondition で止まる
output "telegraf_repository_url" {
  description = "Build app/telegraf/ (arm64) and push it here with tag <Telegraf version>-<hash of app/telegraf/> (step 2 of ops/up.sh). IaC/terraform/aws-managed/pipeline/stream runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["telegraf"].repository_url, "")
}

output "kafka_ui_repository_url" {
  description = "Push ghcr.io/kafbat/kafka-ui:<KAFKA_UI_TAG of ops/up.sh> (arm64) here with the same tag (step 2, whenever stream is built). IaC/terraform/aws-managed/pipeline/stream runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["kafka-ui"].repository_url, "")
}

output "grafana_repository_url" {
  description = "Build app/grafana/ (arm64) and push it here with tag <Grafana version>-<hash of app/grafana/> (step 2 of ops/up.sh, when STORES has grafana). IaC/terraform/aws-managed/pipeline/analytics runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["grafana"].repository_url, "")
}

output "splunk_repository_url" {
  description = "ops/up.sh builds app/splunk/ (splunk/splunk plus the netops_alerts app, amd64 only) and pushes it here as <Splunk version>-<hash of app/splunk/> (step 2, when STORES has splunk). IaC/terraform/aws-managed/pipeline/analytics runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["splunk"].repository_url, "")
}

output "nautobot_repository_url" {
  description = "ops/up.sh builds app/nautobot/ (networktocode/nautobot plus the NetOps jobs, arm64) and pushes it here as <Nautobot version>-<hash of the build context> (step 2, whenever PIPELINE=1). IaC/terraform/aws-managed/pipeline/nautobot runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["nautobot"].repository_url, "")
}

output "redis_repository_url" {
  description = "Push redis:<REDIS_TAG of ops/up.sh> (arm64) here with the same tag (step 2, whenever PIPELINE=1). The Redis sidecar of the Nautobot task (cache and Celery broker)."
  value       = try(aws_ecr_repository.pipeline["redis"].repository_url, "")
}

output "oss_repository_urls" {
  description = "OSS build only (IaC/terraform/oss, cycle 005): repository URL per image - kafka / opensearch / vminsert / vmselect / vmstorage (mirrored public images) and spark / neo4j (built by ops). Empty map in the managed build."
  value       = { for k, r in aws_ecr_repository.oss : k => r.repository_url }
}
