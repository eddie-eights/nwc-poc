output "agent_repository_url" {
  description = "Push the agent image here (step 2 of ops/up.sh). IaC/terraform/aws-managed/base/core reads it from this state."
  value       = aws_ecr_repository.agent.repository_url
}

output "srlinux_repository_url" {
  description = "Push ghcr.io/nokia/srlinux:26.7.2 (amd64, for the x86_64 lab EC2) here with tag 26.7.2-amd64 (SRLINUX_ECR_TAG of ops/lab-common.sh; step 2 of ops/up.sh). Empty when create_lab_repositories is false."
  value       = try(aws_ecr_repository.lab["srlinux"].repository_url, "")
}

output "trex_repository_url" {
  description = "Push trexcisco/trex:2.41 (amd64 only) here with tag 2.41-amd64 (TREX_ECR_TAG of ops/lab-common.sh; step 2 of ops/up.sh). The traffic generator of the lab (app/containerlab/trex/)."
  value       = try(aws_ecr_repository.lab["trex"].repository_url, "")
}

output "worker_repository_url" {
  description = "Build app/temporal/ and push it here with the same tag as the agent image (step 2 of ops/up.sh). Empty when create_workflow_repositories is false."
  value       = try(aws_ecr_repository.workflow["worker"].repository_url, "")
}

output "temporal_repository_url" {
  description = "Build docker/images/temporal-server/ (temporalio/server + temporal-sql-tool + psql, the Temporal server that keeps its history in the Nautobot RDS) and push it here with the dir_tag tag (step 2 of ops/up.sh)."
  value       = try(aws_ecr_repository.workflow["temporal"].repository_url, "")
}

output "temporal_ui_repository_url" {
  description = "Push temporalio/ui here with the same tag (TEMPORAL_UI_TAG of ops/up-common.sh, step 2 of ops/up.sh)."
  value       = try(aws_ecr_repository.workflow["temporal-ui"].repository_url, "")
}

# pipeline の 9 つも lab / workflow と同じく try() で包む。state を失って残った ECR を 1 本ずつ terraform import すると、まだ state に無いキーを
# 引いた output が Invalid index で落ちる（2026-10-08 の OSS 版の検証の「不具合」3）。読む側は try() で空を受け、precondition で止まる
output "telegraf_repository_url" {
  description = "Build app/telegraf/ (arm64) and push it here with tag <Telegraf version>-<hash of app/telegraf/ and docker/images/telegraf/Dockerfile> (step 2 of ops/up.sh). IaC/terraform/aws-managed/pipeline/stream runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["telegraf"].repository_url, "")
}

output "kafka_ui_repository_url" {
  description = "Push ghcr.io/kafbat/kafka-ui:<KAFKA_UI_TAG of ops/up.sh> (arm64) here with the same tag (step 2, whenever stream is built). IaC/terraform/aws-managed/pipeline/stream writes <this URL>:<tag> to the SSM parameter /<prefix>/kafka-ui/image, and Docker on the Web EC2 (systemd unit <prefix>-kafka-ui) pulls it from here."
  value       = try(aws_ecr_repository.pipeline["kafka-ui"].repository_url, "")
}

output "gnmic_repository_url" {
  description = "Build app/gnmic/ (the official gnmic with the config template and the entrypoint, arm64) and push it here with tag <GNMIC_VERSION of ops/up-common.sh>-<hash of app/gnmic/ and docker/images/gnmic/Dockerfile> (step 2, whenever stream is built; cycle 013). IaC/terraform/aws-managed/pipeline/stream runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["gnmic"].repository_url, "")
}

output "syslog_ng_repository_url" {
  description = "Build app/syslog-ng/ (AxoSyslog, arm64) and push it here with tag <SYSLOG_NG_VERSION of ops/up-common.sh>-<hash of app/syslog-ng/ and docker/images/syslog-ng/Dockerfile> (step 2, whenever stream is built; cycle 012). IaC/terraform/aws-managed/pipeline/stream runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["syslog-ng"].repository_url, "")
}

output "goflow2_repository_url" {
  description = "Push netsampler/goflow2:<GOFLOW2_TAG of ops/up-common.sh> (arm64) here with the same tag (step 2, whenever stream is built; cycle 012). IaC/terraform/aws-managed/pipeline/stream runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["goflow2"].repository_url, "")
}

output "grafana_repository_url" {
  description = "Build app/grafana/ (arm64) and push it here with tag <Grafana version>-<hash of app/grafana/ and docker/images/grafana/Dockerfile> (step 2 of ops/up.sh, when STORES has grafana). IaC/terraform/aws-managed/pipeline/analytics runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["grafana"].repository_url, "")
}

output "splunk_repository_url" {
  description = "ops/up.sh builds app/splunk/ (splunk/splunk plus the nwc_alerts app, amd64 only) and pushes it here as <Splunk version>-<hash of app/splunk/ and docker/images/splunk/Dockerfile> (step 2, when STORES has splunk). IaC/terraform/aws-managed/pipeline/analytics runs it on ECS."
  value       = try(aws_ecr_repository.pipeline["splunk"].repository_url, "")
}

output "nautobot_repository_url" {
  description = "ops/up.sh builds app/nautobot/ (networktocode/nautobot plus the NWC jobs, arm64) and pushes it here as <Nautobot version>-<hash of the build context> (step 2, whenever PIPELINE=1). IaC/terraform/aws-managed/pipeline/nautobot runs it on ECS."
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
