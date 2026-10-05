# 土台（terraform/base/core）と ECR（terraform/base/ecr）の state、OpenSearch・VictoriaMetrics（opensearch.tf・victoriametrics.tf）と
# ecs.tf（マネージド版へのリンク）が読む locals。マネージド版の locals.tf は EMR・AMP・OpenSearch Serverless のリソースを読むのでリンクにできず、
# 要るものだけをマネージド版と同じ名前・同じ値でここに書く。
# Spark と Neo4j の枝（feat/oss-spark-neo4j）も同じ名前の network.tf を持ち、そちらはこの全部を含む（stream の state、バケット、格納先など）。
# 2 つをマージするときは、そちらの network.tf をそのまま採る（名前と値はそちらに合わせてある）

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "terraform_remote_state" "main" {
  backend = "local"

  config = {
    path = "${path.module}/../../base/core/terraform.tfstate"
  }

  # マネージド版の locals.tf と同じ（SG の ID が無い古い state なら apply の前に止める）
  lifecycle {
    postcondition {
      condition     = can(self.outputs.security_group_ids)
      error_message = "terraform/base/core の state に security_group_ids が無い（2026-09-29 より前の SG）。先に ops/down.sh で消してから ops/up.sh を打ち直す"
    }
  }
}

# OpenSearch・VictoriaMetrics のイメージ（OSS 版の ops/up.sh が ECR に写す）
data "terraform_remote_state" "ecr" {
  backend = "local"

  config = {
    path = "${path.module}/../../base/ecr/terraform.tfstate"
  }
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  vpc_id = data.terraform_remote_state.main.outputs.vpc_id
  # サブネット a / b / c（この順）。3 台のものは台 N を N 番目に置く
  subnet_ids = data.terraform_remote_state.main.outputs.subnet_ids
  # terraform/base/core の perimeter.tf の Deny。NETWORK_PERIMETER=0 か古い state なら空
  perimeter_policy_arn = try(data.terraform_remote_state.main.outputs.network_perimeter_policy_arn, "")

  # 格納先（var.sinks。マネージド版と同じ名前）。opensearch があれば OpenSearch、prometheus があれば VictoriaMetrics を作る
  sink_opensearch = contains(var.sinks, "opensearch")
  sink_prometheus = contains(var.sinks, "prometheus")

  # ECS のクラスタと Cloud Map の名前空間（ecs.tf）は OSS 版では常に作る（OpenSearch・VictoriaMetrics・Spark が載る）
  create_ecs        = true
  service_namespace = "${local.name_prefix}.internal"

  opensearch_index  = "snmp-logs" # spark/snmp_sinks.py の OPENSEARCH_INDEX と同じ
  ssm_parameter_arn = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter"
}
