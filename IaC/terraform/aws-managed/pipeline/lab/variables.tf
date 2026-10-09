# ---------------------------------------------------------------- naming
variable "region" {
  description = "AWS region. Same value as IaC/terraform/aws-managed/base/core."
  type        = string
  default     = "ap-northeast-1"
}

variable "owner" {
  description = "Required. Name of the person who deploys this copy. Resource names and the Project tag are <owner>-nwc-poc (<owner>-nwc-oss in IaC/terraform/oss, see project), so each person can find their own resources in the console. Same value in every root module."
  type        = string

  validation {
    # 接頭辞は <owner>-nwc-poc。OpenSearch Serverless の data access policy 名が 32 文字までで、一番長い接尾辞は IaC/terraform/aws-managed/workflow の <接頭辞>-logs-read（10 文字）なので接頭辞は 22 文字まで。-nwc-poc の 8 文字（OSS 版の -nwc-oss も 8 文字）を引いて owner は 14 文字まで
    # ハイフンの連続と末尾のハイフンも弾く（ECR のリポジトリ名が受け付けない）
    condition     = can(regex("^[a-z][a-z0-9]*(-[a-z0-9]+)*$", var.owner)) && length(var.owner) <= 14
    error_message = "owner must be 1-14 lowercase letters, digits and single hyphens, starting with a letter and not ending with one."
  }
}

variable "project" {
  description = "Second half of the resource name prefix and of the Project tag (<owner>-<project>). nwc-poc is the managed-services build (IaC/terraform/aws-managed/); nwc-oss is the build that runs open-source services on ECS instead (IaC/terraform/oss/, cycle 005), whose oss.auto.tfvars sets it. Same value in every root module."
  type        = string
  default     = "nwc-poc"

  validation {
    condition     = contains(["nwc-poc", "nwc-oss"], var.project)
    error_message = "project must be nwc-poc (IaC/terraform/aws-managed/) or nwc-oss (IaC/terraform/oss/)."
  }
}

# ---------------------------------------------------------------- lab EC2
variable "instance_type" {
  description = "7 containers (Nokia SR Linux x6, TRex x1). x86_64 because TRex is amd64 only. Each SR Linux node takes about 1.5-2.5 GB of RAM at boot and TRex 2-4 GB, so m6i.xlarge (4 vCPU / 16 GB) is the default; use m6i.2xlarge (8 vCPU / 32 GB) for load tests."
  type        = string
  default     = "m6i.xlarge"

  validation {
    condition     = contains(["m6i.xlarge", "m6i.2xlarge", "c6i.2xlarge", "t3.xlarge", "t3.2xlarge"], var.instance_type)
    error_message = "instance_type must be m6i.xlarge, m6i.2xlarge, c6i.2xlarge, t3.xlarge or t3.2xlarge (x86_64)."
  }
}

variable "ami_ssm_parameter" {
  description = "SSM public parameter that resolves to the AMI. Amazon Linux 2023 x86_64 (SSM Agent and AWS CLI v2 are preinstalled; Docker comes from the AL2023 repository)."
  type        = string
  default     = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

variable "volume_size" {
  description = "Root volume in GB (the SR Linux image is about 1 GB; each node writes its own logs and checkpoints)."
  type        = number
  default     = 24

  validation {
    condition     = var.volume_size >= 8 && var.volume_size <= 64
    error_message = "volume_size must be between 8 and 64."
  }
}

variable "auto_start_lab" {
  description = "Deploy the topology at every boot (systemd unit). Set false to start it by hand with \"sudo lab up\"."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------- path to Telegraf (telegraf.tf)
variable "forward_to_telegraf" {
  description = "Route the lab mgmt network (203.0.113.0/24) to this EC2 and turn off its source/destination check, so gnmic (the ECS task of IaC/terraform/aws-managed/pipeline/stream) reaches the switches over gNMI. The security group rules for gNMI, traps and syslog are always there (IaC/terraform/aws-managed/base/core security_groups.tf). ops/up.sh sets true when IaC/terraform/aws-managed/pipeline/stream is made or kept."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------- assets
variable "containerlab_version" {
  description = "containerlab_<version>_linux_amd64.rpm must be uploaded to s3://<kb_bucket_name of IaC/terraform/aws-managed/base/core>/lab/"
  type        = string
  default     = "0.79.0"

  validation {
    condition     = can(regex("^[0-9]+[.][0-9]+[.][0-9]+$", var.containerlab_version))
    error_message = "containerlab_version must look like 0.79.0."
  }
}

variable "srlinux_image_tag" {
  description = "Tag pushed to <prefix>-lab-srlinux: the upstream ghcr.io/nokia/srlinux version plus -amd64 (ops/lab-common.sh SRLINUX_ECR_TAG; multi-arch upstream, ops/up.sh pulls the amd64 image for the x86_64 EC2)."
  type        = string
  default     = "26.7.2-amd64"
}

variable "trex_image_tag" {
  description = "Tag pushed to <prefix>-lab-trex: the upstream trexcisco/trex version (amd64 only) plus -amd64 (ops/lab-common.sh TREX_ECR_TAG). The traffic generator dc1-trex-01 (app/containerlab/trex/)."
  type        = string
  default     = "2.41-amd64"
}
