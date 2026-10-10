# nwc-poc - base root module shared by the three features (pipeline / agent / workflow). VPC with 3 private subnets (a, b, c),
# no route to the internet (no NAT Gateway, no IGW, no inbound path), the S3 gateway endpoint and the interface
# endpoints for the AWS APIs, the network perimeter (perimeter.tf - deny outside aws:SourceVpc), one security group per
# workload plus one for the endpoints (security_groups.tf - rules from a flow table), VPC flow logs (flow_logs.tf), the chat web EC2 (Gradio: chat + topology figure + device table, 127.0.0.1 only, reached through SSM Session Manager
# port forwarding), the shared S3 bucket and the IAM roles the features attach policies to.
# The AgentCore Runtime, guardrail and optional knowledge base are IaC/terraform/aws-managed/agent; the lab / stream / analytics / graph
# roots are the pipeline; Temporal on ECS is IaC/terraform/aws-managed/workflow. Each of them reads this state (terraform_remote_state).

# リソース名の接頭辞であり Project タグの値。デプロイする人の名前（var.owner）から作るので、
# 1 つの AWS アカウントを何人かで使っても、自分の名前で自分のリソースを探せる
locals {
  # 末尾は var.project（IaC/terraform/aws-managed/ は既定の nwc-poc、OSS 版の IaC/terraform/oss/ は oss.auto.tfvars の nwc-oss。cycle 005）
  name_prefix = "${var.owner}-${var.project}"
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
}

# Nautobot の中だけのシークレット（ops/up-common.sh の ensure_nautobot_secrets が作る SecureString のうち api-token を除く 3 つ。
# IaC/terraform/aws-managed/pipeline/nautobot の locals.tf の secret_parameters と同じ名前）。使うのは Nautobot のタスクの実行ロールと
# Temporal の init のタスクの実行ロール（db-password だけ）。Web（web.tf）と Runtime（runtime.tf）のロールでは Deny する。
# stream / graph / workflow がこの 2 つのロールに足す /<prefix>/* の Allow にも勝つよう、ロールを作るこのルートに置く（cycle 044）。
# prefix を問わない（parameter/*/nautobot/<名前>）のは、Web のロールの AmazonSSMManagedInstanceCore（ssm:GetParameter* が Resource *）に勝たせ、
# 同じアカウントのほかの環境の 3 つも読ませないため。nautobot/url と nautobot/api-token は Nautobot の API の入口で、Web が読むので Deny しない
locals {
  nautobot_secret_parameter_arns = [
    "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/*/nautobot/secret-key",
    "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/*/nautobot/admin-password",
    "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/*/nautobot/db-password",
  ]
}
