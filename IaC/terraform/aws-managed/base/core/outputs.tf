output "web_instance_id" {
  description = "Target of the SSM port forwarding session"
  value       = aws_instance.web.id
}

output "start_session_command" {
  description = "Run on the user's PC (AWS CLI v2 + Session Manager plugin, macOS / Linux quoting), keep it running, then open chat_url (Gradio - chat tab and topology tab)"
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.web.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"8080\"],\"localPortNumber\":[\"8080\"]}'"
}

output "chat_url" {
  description = "Open in the browser while the session above is running"
  value       = "http://localhost:8080/"
}

# ---------------------------------------------------------------- read by IaC/terraform/aws-managed/agent, IaC/terraform/aws-managed/pipeline/lab, IaC/terraform/aws-managed/pipeline/stream, IaC/terraform/aws-managed/pipeline/analytics, IaC/terraform/aws-managed/pipeline/graph, IaC/terraform/aws-managed/workflow (terraform_remote_state)
output "vpc_id" {
  description = "Read by IaC/terraform/aws-managed/agent / IaC/terraform/aws-managed/pipeline/lab / IaC/terraform/aws-managed/pipeline/stream / IaC/terraform/aws-managed/pipeline/graph / IaC/terraform/aws-managed/workflow"
  value       = aws_vpc.this.id
}

output "subnet_ids" {
  description = "Private subnets a, b, c in this order. Each root takes the first <resource>_az_num of them: IaC/terraform/aws-managed/agent (runtime ENIs, KB index Lambda), IaC/terraform/aws-managed/pipeline/stream (MSK brokers, Telegraf dialout tasks and NLB), IaC/terraform/aws-managed/pipeline/graph (status Lambda), IaC/terraform/aws-managed/pipeline/analytics (EMR), IaC/terraform/aws-managed/pipeline/nautobot (DB subnet group) and IaC/terraform/aws-managed/workflow (tools Lambda). Replaced runtime_subnet_ids (a, b) on 2026-10-04"
  value       = local.subnet_ids
}

output "instance_subnet_id" {
  description = "Read by IaC/terraform/aws-managed/pipeline/lab (the lab EC2 sits next to the chat web)"
  value       = aws_subnet.a.id
}

output "route_table_ids" {
  description = "Read by IaC/terraform/aws-managed/pipeline/lab (route of the lab management network to the lab EC2)"
  value       = [aws_route_table.private.id]
}

output "vpc_cidr" {
  description = "CIDR of the VPC (read by IaC/terraform/aws-managed/pipeline/analytics for the OpenSearch Serverless network policy comment and by anyone who needs an inside-the-VPC rule)"
  value       = aws_vpc.this.cidr_block
}

output "security_group_ids" {
  description = "SG of each workload (security_groups.tf), keyed web / lab / telegraf_dialout / telegraf_dialin / telegraf_dialout_nlb / syslog_ng / goflow2 / msk / spark / grafana / splunk / nautobot / nautobot_db / lambda / workflow / runtime / endpoints. In the OSS build (IaC/terraform/oss, oss.tf) msk is replaced by kafka / efs / opensearch / victoriametrics / neo4j. Read by IaC/terraform/aws-managed/agent (runtime, lambda), IaC/terraform/aws-managed/pipeline/lab (lab), IaC/terraform/aws-managed/pipeline/stream (telegraf_dialout, telegraf_dialin, telegraf_dialout_nlb, syslog_ng, goflow2, msk), IaC/terraform/aws-managed/pipeline/nautobot (nautobot, nautobot_db), IaC/terraform/aws-managed/pipeline/graph (lambda), IaC/terraform/aws-managed/pipeline/analytics (spark, grafana, splunk) and IaC/terraform/aws-managed/workflow (workflow, lambda)"
  value       = local.sg_ids
}

output "opensearch_vpc_endpoint_id" {
  description = "ID of the OpenSearch Serverless VPC endpoint, empty unless create_opensearch_endpoint. Read by IaC/terraform/aws-managed/agent (knowledge base network policy) and IaC/terraform/aws-managed/pipeline/analytics (logs network policy)"
  value       = try(aws_opensearchserverless_vpc_endpoint.aoss[0].id, "")
}

output "network_perimeter" {
  description = "True when the Deny outside aws:SourceVpc is on (perimeter.tf). Read by the other roots to add their resource policy Deny statements"
  value       = var.network_perimeter
}

output "network_perimeter_policy_arn" {
  description = "IAM policy that denies AWS API calls not coming through a VPC endpoint, empty unless network_perimeter. Attached by IaC/terraform/aws-managed/pipeline/lab, IaC/terraform/aws-managed/pipeline/stream, IaC/terraform/aws-managed/pipeline/analytics, IaC/terraform/aws-managed/pipeline/graph, IaC/terraform/aws-managed/pipeline/nautobot and IaC/terraform/aws-managed/workflow to their workload roles (IaC/terraform/aws-managed/agent only checks it is not empty before adding the runtime resource policy)"
  value       = var.network_perimeter ? aws_iam_policy.network_perimeter.arn : ""
}

output "perimeter_exempt_principals" {
  description = "Principals the resource policy Deny statements leave out (the deployer and the knowledge base role). Used by the alerts topic here (alerts.tf) and read by IaC/terraform/aws-managed/pipeline/analytics (S3 Tables) and IaC/terraform/aws-managed/workflow (SQS)"
  value       = local.perimeter_exempt_principals
}

output "alerts_topic_arn" {
  description = "SNS topic the Grafana alert rules and the Splunk saved searches publish to (alerts.tf). Read by IaC/terraform/aws-managed/pipeline/analytics (publish), IaC/terraform/aws-managed/workflow (SQS subscription) and IaC/terraform/aws-managed/pipeline/graph (Lambda subscription)"
  value       = aws_sns_topic.alerts.arn
}

output "kb_bucket_name" {
  description = "Read by IaC/terraform/aws-managed/agent (docs/), IaC/terraform/aws-managed/pipeline/lab (lab/ telegraf/) and IaC/terraform/aws-managed/pipeline/analytics (analytics/)"
  value       = aws_s3_bucket.kb.bucket
}

output "kb_bucket_arn" {
  description = "ARN of the bucket (read by IaC/terraform/aws-managed/agent / IaC/terraform/aws-managed/pipeline/lab / IaC/terraform/aws-managed/pipeline/stream for IAM policies)"
  value       = aws_s3_bucket.kb.arn
}

output "runtime_role_name" {
  description = "Read by IaC/terraform/aws-managed/agent (execution policy), IaC/terraform/aws-managed/pipeline/stream (SSM parameters) and IaC/terraform/aws-managed/pipeline/graph (read policy for Neptune)"
  value       = aws_iam_role.runtime.name
}

output "runtime_role_arn" {
  description = "Read by IaC/terraform/aws-managed/agent (role_arn of the AgentCore Runtime)"
  value       = aws_iam_role.runtime.arn
}

output "web_role_name" {
  description = "Read by IaC/terraform/aws-managed/agent (InvokeAgentRuntime) / IaC/terraform/aws-managed/pipeline/stream / IaC/terraform/aws-managed/pipeline/graph"
  value       = aws_iam_role.web.name
}

output "flow_log_group_name" {
  description = "VPC flow logs (flow_logs.tf). Query REJECT with CloudWatch Logs Insights (docs/troubleshooting.md)"
  value       = aws_cloudwatch_log_group.flow_logs.name
}

# ---------------------------------------------------------------- commands
output "upload_web_command" {
  description = "Run in this repository after \"pip download\" into wheels/ (step 4 of ops/up.sh). Copies every app/dashboard/*.py (app / config / chat / topology_view / incident_view) plus the agent modules the web UI shares (toolkit / topology / graph / proposals). The instance pulls the S3 prefix web/ on every boot. The OSS build (IaC/terraform/oss) also copies app/dashboard/requirements-oss.txt (the Neo4j driver), which its user_data installs."
  value       = "aws s3 cp app/dashboard/ s3://${aws_s3_bucket.kb.bucket}/web/ --recursive --exclude '*' --include '*.py' --include 'requirements.txt'${local.oss ? " --include 'requirements-oss.txt'" : ""} && for f in toolkit topology graph proposals; do aws s3 cp app/agentcore/$f.py s3://${aws_s3_bucket.kb.bucket}/web/$f.py; done && aws s3 cp app/agentcore/data/ s3://${aws_s3_bucket.kb.bucket}/web/data/ --recursive && aws s3 sync wheels/ s3://${aws_s3_bucket.kb.bucket}/web/wheels/"
}
