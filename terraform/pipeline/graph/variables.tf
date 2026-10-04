# ---------------------------------------------------------------- naming
variable "region" {
  description = "AWS region. Same value as terraform/base/core."
  type        = string
  default     = "ap-northeast-1"
}

variable "owner" {
  description = "Required. Name of the person who deploys this copy. Resource names and the Project tag are <owner>-nwc-poc, so each person can find their own resources in the console. Same value in every root module."
  type        = string

  validation {
    # 接頭辞は <owner>-nwc-poc。OpenSearch Serverless の data access policy 名が 32 文字までで、一番長い接尾辞は terraform/workflow の <接頭辞>-logs-read（10 文字）なので接頭辞は 22 文字まで。-nwc-poc の 8 文字を引いて owner は 14 文字まで
    # ハイフンの連続と末尾のハイフンも弾く（ECR のリポジトリ名が受け付けない）
    condition     = can(regex("^[a-z][a-z0-9]*(-[a-z0-9]+)*$", var.owner)) && length(var.owner) <= 14
    error_message = "owner must be 1-14 lowercase letters, digits and single hyphens, starting with a letter and not ending with one."
  }
}

# ---------------------------------------------------------------- neptune
variable "provisioned_memory" {
  description = "Memory of the Neptune Analytics graph in m-NCU (1 m-NCU = 1 GiB). 16 is the smallest and costs about 0.58 USD per hour in Tokyo (checked 2026-10-04). The lab topology is tens of vertices; raise it when the device count grows (changing it resizes the graph in place)."
  type        = number
  default     = 16

  validation {
    condition     = contains([16, 32, 64, 128, 256], var.provisioned_memory)
    error_message = "provisioned_memory must be one of 16, 32, 64, 128, 256."
  }
}

variable "deletion_protection" {
  description = "Keep false so terraform destroy can delete the graph the same day."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------- status Lambda
variable "log_retention_days" {
  description = "Retention of the status Lambda log group /aws/lambda/<prefix>-graph-status."
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 7, 14, 30], var.log_retention_days)
    error_message = "log_retention_days must be one of 1, 3, 7, 14, 30."
  }
}

variable "alert_history" {
  description = "true sends every alert the status Lambda receives to the Firehose stream <prefix>-alert-events of terraform/pipeline/analytics (S3 Tables alert_events). ops/up.sh sets it only when analytics is deployed (in this run or left in its state), so the stream exists."
  type        = bool
  default     = false
}
