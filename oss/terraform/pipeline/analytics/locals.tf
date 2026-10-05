# nwc-oss - PIPELINE analytics root module of the OSS build (cycle 005). The managed build (terraform/pipeline/analytics) uses
# EMR Serverless, Amazon Managed Service for Prometheus and OpenSearch Serverless; this one runs Spark (local mode),
# VictoriaMetrics (cluster version) and OpenSearch on ECS on Fargate instead (data on the EFS of terraform/base/core).
# 骨組み（設計の 3）: まだリソースが無い。Spark・VictoriaMetrics・OpenSearch は docker compose で確かめてから足す

locals {
  # 末尾は var.project（oss.auto.tfvars の nwc-oss）
  name_prefix = "${var.owner}-${var.project}"
}
