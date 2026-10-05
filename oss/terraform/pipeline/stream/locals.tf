# nwc-oss - PIPELINE stream root module of the OSS build (cycle 005). The managed build (terraform/pipeline/stream) uses MSK;
# this one runs Kafka on ECS on Fargate instead (KRaft, 3 nodes, data on the EFS of terraform/base/core).
# Telegraf and Kafbat UI use the same files as the managed build (symbolic links).
# 骨組み（設計の 3）: まだリソースが無い。Kafka は設計の 4 で足す

locals {
  # 末尾は var.project（oss.auto.tfvars の nwc-oss）
  name_prefix = "${var.owner}-${var.project}"
}
