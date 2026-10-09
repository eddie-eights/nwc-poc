# nwc-poc - optional lab root module. One EC2 (Amazon Linux 2023 x86_64) runs Docker + containerlab with the splab topology
# (Spine-Leaf: 6 Nokia SR Linux switches with IS-IS + iBGP EVPN-VXLAN, one TRex with a port on each leaf, all fictional addresses. app/containerlab/gen_lab.py). Reached with SSM Session Manager.
# Images come from ECR (IaC/terraform/aws-managed/base/ecr), the containerlab rpm and configs from the S3 bucket of IaC/terraform/aws-managed/base/core. Stop the instance when not in use.
# With forward_to_telegraf (telegraf.tf), the VPC routes the lab mgmt network to this EC2 so gnmic (an ECS task of IaC/terraform/aws-managed/pipeline/stream)
# can subscribe to the switches over gNMI, and lab.sh forward sends their traps and syslog to the Telegraf NLB.

# リソース名の接頭辞であり Project タグの値。デプロイする人の名前（var.owner）から作るので、
# 1 つの AWS アカウントを何人かで使っても、自分の名前で自分のリソースを探せる
locals {
  # 末尾は var.project（IaC/terraform/aws-managed/ は既定の nwc-poc、OSS 版の IaC/terraform/oss/ は oss.auto.tfvars の nwc-oss。cycle 005）
  name_prefix = "${var.owner}-${var.project}"
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_ssm_parameter" "al2023" {
  name = var.ami_ssm_parameter
}

# VPC / subnet / SG / bucket は IaC/terraform/aws-managed/base/core の state から読む
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
      error_message = "IaC/terraform/aws-managed/base/core の state に security_group_ids が無い（2026-09-29 より前の SG）。先に ops/down.sh で消してから ops/up.sh を打ち直す"
    }
  }
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  vpc_id    = data.terraform_remote_state.main.outputs.vpc_id
  subnet_id = data.terraform_remote_state.main.outputs.instance_subnet_id
  # SG は古い state の destroy でも評価できるように try（空のまま apply に進まないよう remote_state の postcondition で止める）
  lab_sg_id = try(data.terraform_remote_state.main.outputs.security_group_ids["lab"], "")
  bucket    = data.terraform_remote_state.main.outputs.kb_bucket_name
  # IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない AWS の API を拒む）。NETWORK_PERIMETER=0 か古い state なら空
  perimeter_policy_arn = try(data.terraform_remote_state.main.outputs.network_perimeter_policy_arn, "")
  # 1 本（IaC/terraform/aws-managed/base/core の private）。gnmic のタスクから lab の管理ネットワークへの経路を足す
  route_table_ids = data.terraform_remote_state.main.outputs.route_table_ids

  # containerlab の管理ネットワーク（app/containerlab/splab.clab.yml.in の mgmt、app/containerlab/lab.sh の MGMT、IaC/terraform/aws-managed/base/core の security_groups.tf の local.lab_mgmt_cidr と同じ）。
  # EC2 の中の docker network で、forward_to_telegraf のときだけ VPC のルートで lab の EC2 に向ける（gnmic のタスクから機器の gNMI を引くため）
  mgmt_cidr = "203.0.113.0/24"
}
