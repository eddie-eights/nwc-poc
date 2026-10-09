# nwc-poc - PIPELINE nautobot root module. Nautobot (source of truth of the devices and cables) on ECS Fargate with PostgreSQL on RDS.
# One task runs three containers: web (UI / API on 8080), a Celery worker (runs the jobs) and Redis (cache and Celery broker).
# The NWC jobs of the image (app/nautobot/jobs) read the devices and cables of Nautobot and bring two things in line with them:
#   1. the gNMI targets of the gnmic task - an SSM parameter of IaC/terraform/aws-managed/pipeline/stream, then a new deployment of the gnmic service
#   2. the physical layer of the topology in Neptune (IaC/terraform/aws-managed/pipeline/graph), written with openCypher (app/agentcore/graph.py sync_physical).
#      The OSS variant (IaC/terraform/oss, cycle 005) writes the same into Neo4j instead: its graph state has neo4j_uri, not graph_id
# A job hook runs the job on every change of a device / interface / cable / IP address / service, and the task runs it once at start.
# The first start seeds Nautobot from the lab definition (lab_seed.json in the image), so the lab works without typing anything.
# Costs about 0.13 USD per hour (Fargate ARM 2 vCPU / 4 GB + RDS db.t4g.micro) - ops/down.sh destroys this root, and what was edited in Nautobot goes with it.

# リソース名の接頭辞であり Project タグの値。デプロイする人の名前（var.owner）から作るので、
# 1 つの AWS アカウントを何人かで使っても、自分の名前で自分のリソースを探せる
locals {
  # 末尾は var.project（IaC/terraform/aws-managed/ は既定の nwc-poc、OSS 版の IaC/terraform/oss/ は oss.auto.tfvars の nwc-oss。cycle 005）
  name_prefix = "${var.owner}-${var.project}"
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# VPC / サブネット / SG は IaC/terraform/aws-managed/base/core の state から読む
data "terraform_remote_state" "main" {
  backend = "local"

  config = {
    path = "${path.module}/../../base/core/terraform.tfstate"
  }

  # Nautobot の SG（nautobot / nautobot_db）は 2026-10-04 から。それより前の state なら apply の前に止める。
  # destroy ではこの条件を見ないので、locals の SG の try と合わせて古い state のまま ops/down.sh で消せる
  lifecycle {
    postcondition {
      condition     = can(self.outputs.security_group_ids["nautobot_db"])
      error_message = "IaC/terraform/aws-managed/base/core の state に Nautobot の SG（nautobot / nautobot_db）が無い（2026-10-04 より前の土台）。先に IaC/terraform/aws-managed/base/core を apply する（ops/up.sh の手順 3）"
    }
  }
}

# Nautobot と Redis のイメージは IaC/terraform/aws-managed/base/ecr（ops/up.sh の手順 1 と 2）
data "terraform_remote_state" "ecr" {
  backend = "local"

  config = {
    path = "${path.module}/../../base/ecr/terraform.tfstate"
  }
}

# gnmic の購読先の一覧（SSM）とサービスは IaC/terraform/aws-managed/pipeline/stream。無ければ Job は一覧を触らない
data "terraform_remote_state" "stream" {
  backend = "local"

  config = {
    path = "${path.module}/../stream/terraform.tfstate"
  }
}

# Neptune（OSS 版は Neo4j）は IaC/terraform/aws-managed/pipeline/graph。無ければ Job はグラフを触らない
data "terraform_remote_state" "graph" {
  backend = "local"

  config = {
    path = "${path.module}/../graph/terraform.tfstate"
  }
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  vpc_id = data.terraform_remote_state.main.outputs.vpc_id
  # サブネット a / b / c（この順）。DB のサブネットグループは 3 つとも入れる（1 台なら RDS がどれかに置き、Multi-AZ なら別の AZ に控えを置く）
  subnet_ids = data.terraform_remote_state.main.outputs.subnet_ids
  # ECS のタスクを置くサブネット（web の EC2 と同じ。SSM のポートフォワードは web の EC2 から届く）
  instance_subnet_id = data.terraform_remote_state.main.outputs.instance_subnet_id
  # SSM のポートフォワードの踏み台（Nautobot の UI。outputs.tf のコマンド）
  web_instance_id = try(data.terraform_remote_state.main.outputs.web_instance_id, "")
  # SG は古い state の destroy でも評価できるように try（空のまま apply に進まないよう remote_state の postcondition で止める）
  nautobot_sg_id    = try(data.terraform_remote_state.main.outputs.security_group_ids["nautobot"], "")
  nautobot_db_sg_id = try(data.terraform_remote_state.main.outputs.security_group_ids["nautobot_db"], "")
  # IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない AWS の API を拒む）。NETWORK_PERIMETER=0 なら空
  perimeter_policy_arn = try(data.terraform_remote_state.main.outputs.network_perimeter_policy_arn, "")

  image       = "${try(data.terraform_remote_state.ecr.outputs.nautobot_repository_url, "")}:${var.nautobot_image_tag}"
  redis_image = "${try(data.terraform_remote_state.ecr.outputs.redis_repository_url, "")}:${var.redis_image_tag}"
  log_group   = "/ecs/${local.name_prefix}-nautobot"
  # analytics の名前空間（<接頭辞>.internal）とは別にする（このルートは analytics が無くても作れる）
  service_namespace = "${local.name_prefix}-nautobot.internal"
  url               = "http://nautobot.${local.service_namespace}:8080"

  # stream の gnmic（Nautobot が一覧を持つ形 = gnmi_targets_from_nautobot で apply されているときだけ。cycle 013 で Telegraf の dialin から替えた）。
  # 違えば空で、Job は一覧を触らない
  gnmic_from_nautobot     = try(data.terraform_remote_state.stream.outputs.gnmic_targets_source, "") == "nautobot"
  gnmic_targets_parameter = local.gnmic_from_nautobot ? data.terraform_remote_state.stream.outputs.gnmic_target_parameter : ""
  telegraf_cluster        = local.gnmic_from_nautobot ? data.terraform_remote_state.stream.outputs.telegraf_cluster_name : ""
  gnmic_service           = local.gnmic_from_nautobot ? data.terraform_remote_state.stream.outputs.gnmic_service_name : ""

  # Neptune Analytics（graph が無ければ空）。app/agentcore/graph.py はグラフの ID（g-xxxxxxxxxx）を受ける
  neptune_graph_id  = try(data.terraform_remote_state.graph.outputs.graph_id, "")
  neptune_graph_arn = try(data.terraform_remote_state.graph.outputs.graph_arn, "")
  # OSS 版（IaC/terraform/oss/pipeline/graph。cycle 005）は Neptune の代わりに Neo4j で、state に neo4j_uri がある。あれば Job を
  # GRAPH_BACKEND=neo4j で Neo4j に向け（nautobot.tf）、パスワード（SSM の SecureString）を ECS の secrets で渡す（access.tf の実行ロール）。
  # IaC/terraform/aws-managed/workflow の locals.tf と同じ読み方
  neo4j_uri          = try(data.terraform_remote_state.graph.outputs.neo4j_uri, "")
  graph_neo4j        = local.neo4j_uri != ""
  neo4j_password_arn = local.graph_neo4j ? "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${data.terraform_remote_state.graph.outputs.neo4j_password_parameter}" : ""

  # ops/up.sh が無ければ乱数で作る SecureString（値は Terraform の state に載せない。ops/down.sh が消す）
  secret_parameters = {
    secret-key     = "/${local.name_prefix}/nautobot/secret-key"     # Django の SECRET_KEY
    admin-password = "/${local.name_prefix}/nautobot/admin-password" # 画面の管理者（app/nautobot/nwc/bootstrap.py が作る）
    db-password    = "/${local.name_prefix}/nautobot/db-password"    # RDS のマスターユーザー
    api-token      = "/${local.name_prefix}/nautobot/api-token"      # Web（app/dashboard/nautobot_api.py）が REST API に使うトークン（bootstrap.py が同じ値で作る）
  }
  secret_arns = { for k, v in local.secret_parameters : k => "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${v}" }
}
