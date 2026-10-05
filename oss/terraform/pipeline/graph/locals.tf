# nwc-oss - PIPELINE graph root module of the OSS build (cycle 005). The managed build (terraform/pipeline/graph) uses
# Neptune Analytics; this one runs Neo4j with the Graph Data Science plugin on ECS on Fargate instead (not on EFS - Neo4j does not support NFS).
# 骨組み（設計の 3）: まだリソースが無い。Neo4j は docker compose で確かめてから足す

locals {
  # 末尾は var.project（oss.auto.tfvars の nwc-oss）
  name_prefix = "${var.owner}-${var.project}"
}
