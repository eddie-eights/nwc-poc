# ---------------------------------------------------------------- naming
variable "region" {
  description = "AWS region. Same value as terraform/base/core."
  type        = string
  default     = "ap-northeast-1"
}

variable "owner" {
  description = "Required. Name of the person who deploys this copy. Resource names and the Project tag are <owner>-nwc-poc (<owner>-nwc-oss in oss/terraform, see project), so each person can find their own resources in the console. Same value in every root module."
  type        = string

  validation {
    # 接頭辞は <owner>-nwc-poc。OpenSearch Serverless の data access policy 名が 32 文字までで、一番長い接尾辞は terraform/workflow の <接頭辞>-logs-read（10 文字）なので接頭辞は 22 文字まで。-nwc-poc の 8 文字（OSS 版の -nwc-oss も 8 文字）を引いて owner は 14 文字まで
    # ハイフンの連続と末尾のハイフンも弾く（ECR のリポジトリ名が受け付けない）
    condition     = can(regex("^[a-z][a-z0-9]*(-[a-z0-9]+)*$", var.owner)) && length(var.owner) <= 14
    error_message = "owner must be 1-14 lowercase letters, digits and single hyphens, starting with a letter and not ending with one."
  }
}

variable "project" {
  description = "Second half of the resource name prefix and of the Project tag (<owner>-<project>). nwc-poc is the managed-services build (terraform/); nwc-oss is the build that runs open-source services on ECS instead (oss/terraform/, cycle 005), whose oss.auto.tfvars sets it. Same value in every root module."
  type        = string
  default     = "nwc-poc"

  validation {
    condition     = contains(["nwc-poc", "nwc-oss"], var.project)
    error_message = "project must be nwc-poc (terraform/) or nwc-oss (oss/terraform/)."
  }
}

# ---------------------------------------------------------------- nautobot (ECS)
variable "nautobot_image_tag" {
  description = "Tag of the Nautobot image in the ECR repository <prefix>-nautobot. ops/up.sh builds nautobot/ and passes <Nautobot version>-<hash of the build context>."
  type        = string
  default     = "3.2.6"
}

variable "redis_image_tag" {
  description = "Tag of the Redis image in the ECR repository <prefix>-redis (mirrored by ops/up.sh, REDIS_TAG)."
  type        = string
  default     = "8.10.2-alpine"
}

variable "task_cpu" {
  description = "CPU units of the task (web + Celery worker + Redis). The database migration of the first start is the heavy part."
  type        = number
  default     = 2048
}

variable "task_memory" {
  description = "Memory (MiB) of the task"
  type        = number
  default     = 4096
}

variable "admin_user" {
  description = "Name of the Nautobot superuser that the task creates at start (the password is the SSM parameter /<prefix>/nautobot/admin-password)."
  type        = string
  default     = "admin"
}

variable "log_retention_days" {
  description = "Retention of the log group /ecs/<prefix>-nautobot."
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 7, 14, 30], var.log_retention_days)
    error_message = "log_retention_days must be one of 1, 3, 7, 14, 30."
  }
}

# ---------------------------------------------------------------- database (RDS)
variable "db_instance_class" {
  description = "RDS instance class of the Nautobot database (about 0.03 USD per hour for db.t4g.micro in Tokyo - list price, not verified with the pricing API)."
  type        = string
  default     = "db.t4g.micro"
}

variable "nautobot_db_az_num" {
  description = "1 or 2. 2 makes the database a Multi-AZ DB instance (multi_az = true: a synchronous standby in another AZ, about twice the instance price). 3 is not possible here: three AZs is a Multi-AZ DB cluster (a writer and two readable standbys in three AZs), a different resource (aws_rds_cluster) whose instance classes do not include db.t4g.micro (Amazon RDS User Guide, Configuring and managing a Multi-AZ deployment for Amazon RDS: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html, and Multi-AZ DB cluster deployments for Amazon RDS: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/multi-az-db-clusters-concepts.html, checked 2026-10-04). Changing it modifies the instance in place. ops/up.sh passes NAUTOBOT_DB_AZ_NUM."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2], var.nautobot_db_az_num)
    error_message = "nautobot_db_az_num must be 1 or 2 (3 would need a Multi-AZ DB cluster, which this root does not build)."
  }
}

variable "db_engine_version" {
  description = "PostgreSQL major version (RDS picks the minor). Nautobot 3 supports PostgreSQL 12.0 and later."
  type        = string
  default     = "17"
}

variable "db_password_version" {
  description = "Raise by one to make Terraform send the password of /<prefix>/nautobot/db-password to RDS again (the password is write-only, so Terraform cannot see that the parameter changed)."
  type        = number
  default     = 1
}

variable "deletion_protection" {
  description = "Keep false so terraform destroy can delete the database the same day."
  type        = bool
  default     = false
}
