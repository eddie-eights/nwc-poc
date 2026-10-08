# nwc-poc - PIPELINE stream root module. Kafka receives SNMP polls, gNMI state, traps and syslog from Telegraf (telegraf.tf: two ECS on Fargate tasks - a receiver
# behind an internal NLB and a poller, split on 2026-10-04; it was an EC2 of terraform/pipeline/lab until 2026-09-28),
# and the Spark job of terraform/pipeline/analytics reads them (raw messages go to S3 Tables, Prometheus, OpenSearch and Splunk; detection is done by Grafana and Splunk since 2026-10-02). The MSK Connect S3 sink that also copied the raw messages to the asset bucket was removed on 2026-09-26
# (Spark already stores every topic in S3 Tables).
# Kafka is MSK in this root (msk.tf). The OSS build of cycle 005 (oss/terraform/pipeline/stream) runs Kafka on ECS instead (its kafka.tf) and uses this file,
# telegraf.tf, kafka_ui.tf, access.tf and outputs.tf through symbolic links; what differs between the two Kafkas comes from the kafka_* locals of msk.tf / kafka.tf.

# リソース名の接頭辞であり Project タグの値。デプロイする人の名前（var.owner）から作るので、
# 1 つの AWS アカウントを何人かで使っても、自分の名前で自分のリソースを探せる
locals {
  # 末尾は var.project（terraform/ は既定の nwc-poc、OSS 版の oss/terraform/ は oss.auto.tfvars の nwc-oss。cycle 005）
  name_prefix = "${var.owner}-${var.project}"
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# VPC / サブネット / ロール名は terraform/base/core、Telegraf のイメージのリポジトリは terraform/base/ecr の state から読む
data "terraform_remote_state" "main" {
  backend = "local"

  config = {
    path = "${path.module}/../../base/core/terraform.tfstate"
  }

  # SG の ID（security_group_ids）は 2026-09-29 から、Telegraf の SG のキーが dialout / dialin になったのは 2026-10-04 から。
  # それより前の state なら apply の前に止める。destroy ではこの条件を見ないので、locals の SG の try と合わせて古い state のまま ops/down.sh で消せる（Terraform 1.16 で確認）
  lifecycle {
    postcondition {
      condition     = can(self.outputs.security_group_ids["telegraf_dialin"])
      error_message = "terraform/base/core の state に telegraf_dialin の SG が無い（2026-10-04 より前の SG）。先に ops/down.sh で消してから ops/up.sh を打ち直す"
    }
  }
}

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
  # サブネット a / b / c（この順）。各リソースは先頭から <リソース>_az_num 個を使う
  subnet_ids = data.terraform_remote_state.main.outputs.subnet_ids
  # SG は古い state の destroy でも評価できるように try（空のまま apply に進まないよう remote_state の postcondition で止める）
  telegraf_dialout_sg_id     = try(data.terraform_remote_state.main.outputs.security_group_ids["telegraf_dialout"], "")
  telegraf_dialin_sg_id      = try(data.terraform_remote_state.main.outputs.security_group_ids["telegraf_dialin"], "")
  telegraf_dialout_nlb_sg_id = try(data.terraform_remote_state.main.outputs.security_group_ids["telegraf_dialout_nlb"], "")
  # Kafbat UI（kafka_ui.tf）。2026-10-05 より前の土台には無いので、kafka_ui.tf の precondition で止める
  kafka_ui_sg_id = try(data.terraform_remote_state.main.outputs.security_group_ids["kafka_ui"], "")
  # Kafbat UI のポートフォワードの踏み台
  web_instance_id   = try(data.terraform_remote_state.main.outputs.web_instance_id, "")
  reader_role_names = toset([data.terraform_remote_state.main.outputs.runtime_role_name, data.terraform_remote_state.main.outputs.web_role_name])

  # Telegraf の dialin のタスクと、dialout の NLB の lab 向けのアドレスはサブネット a（lab の EC2 と Web の EC2 と同じ）。
  # dialout の NLB とタスクは var.telegraf_az_num の AZ（a から）
  telegraf_subnet_id          = data.terraform_remote_state.main.outputs.instance_subnet_id
  telegraf_dialout_subnet_ids = slice(local.subnet_ids, 0, var.telegraf_az_num)
  # terraform/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない AWS の API を拒む）。NETWORK_PERIMETER=0 か古い state なら空
  perimeter_policy_arn = try(data.terraform_remote_state.main.outputs.network_perimeter_policy_arn, "")
}
