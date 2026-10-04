# nwc-poc - PIPELINE graph root module. One Neptune Analytics graph (IAM auth, 16 m-NCU, no public endpoint) holds the network topology
# (device vertices, link edges, their dynamic status) and the repair proposals of terraform/workflow. The chat runtime and the web read it through boto3 neptune-graph (openCypher); the web can also edit it.
# Without this root module both fall back to the static data in agent/data/. Costs about 0.58 USD per hour while it exists (16 m-NCU, Tokyo) - destroy it the same day.

# リソース名の接頭辞であり Project タグの値。デプロイする人の名前（var.owner）から作るので、
# 1 つの AWS アカウントを何人かで使っても、自分の名前で自分のリソースを探せる
locals {
  name_prefix = "${var.owner}-nwc-poc"
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# VPC / サブネット / SG / ロール名は terraform/base/core の state から読む
data "terraform_remote_state" "main" {
  backend = "local"

  config = {
    path = "${path.module}/../../base/core/terraform.tfstate"
  }

  # SG の ID（security_group_ids）は 2026-09-29 から。それより前の state（全部で共有する internal 1 つ）なら apply の前に止める。
  # destroy ではこの条件を見ないので、locals の SG の try と合わせて古い state のまま ops/down.sh で消せる（Terraform 1.16 で確認）
  lifecycle {
    postcondition {
      condition     = can(self.outputs.security_group_ids)
      error_message = "terraform/base/core の state に security_group_ids が無い（2026-09-29 より前の SG）。先に ops/down.sh で消してから ops/up.sh を打ち直す"
    }
  }
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  vpc_id = data.terraform_remote_state.main.outputs.vpc_id
  # サブネット a / b / c（この順）。status Lambda は先頭から var.lambda_az_num 個を使う
  subnet_ids = data.terraform_remote_state.main.outputs.subnet_ids
  # SG は古い state の destroy でも評価できるように try（空のまま apply に進まないよう remote_state の postcondition で止める）
  lambda_sg_id    = try(data.terraform_remote_state.main.outputs.security_group_ids["lambda"], "") # sync.tf の status Lambda
  reader_role_ids = toset([data.terraform_remote_state.main.outputs.runtime_role_name, data.terraform_remote_state.main.outputs.web_role_name])
  # アラートの SNS トピック（terraform/base/core の alerts.tf）。sync.tf の status Lambda が購読する。古い state なら空で、購読の precondition が止める
  alerts_topic_arn = try(data.terraform_remote_state.main.outputs.alerts_topic_arn, "")
  # terraform/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない AWS の API を拒む）。NETWORK_PERIMETER=0 か古い state なら空
  perimeter_policy_arn = try(data.terraform_remote_state.main.outputs.network_perimeter_policy_arn, "")
}
