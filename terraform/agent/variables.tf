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

# ---------------------------------------------------------------- agent
variable "agent_image_tag" {
  description = "Tag pushed to the agent repository of terraform/base/ecr (step 2 of ops/up.sh). The repository URL is read from terraform/base/ecr/terraform.tfstate."
  type        = string
  default     = "v1"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]{1,128}$", var.agent_image_tag))
    error_message = "agent_image_tag must match ^[A-Za-z0-9._-]{1,128}$."
  }
}

variable "agent_image_uri" {
  description = "Optional. Leave empty to use <terraform/base/ecr repository>:<agent_image_tag>. Set only to run an image from another repository, with tag, built for linux/arm64 (e.g. 123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/<owner>-nwc-poc-agent:v1)."
  type        = string
  default     = ""
}

variable "runtime_name" {
  description = "Optional. AgentCore Runtime name (letters, digits and underscore only. Hyphens are not allowed). Leave empty to derive it from the prefix <owner>-nwc-poc: hyphens become underscores and _agent is appended (owner netops -> netops_nwc_poc_agent)."
  type        = string
  default     = ""

  validation {
    # 空なら locals.tf が接頭辞から作る（接頭辞は ^[a-z][a-z0-9-]{1,22}$ なので必ずこの形に収まる）
    condition     = var.runtime_name == "" || can(regex("^[a-zA-Z][a-zA-Z0-9_]{0,47}$", var.runtime_name))
    error_message = "runtime_name must match ^[a-zA-Z][a-zA-Z0-9_]{0,47}$."
  }
}

variable "model_id" {
  description = "Bedrock model or inference profile ID (jp.amazon.nova-2-lite-v1:0 runs in Tokyo and Osaka)."
  type        = string
  default     = "jp.amazon.nova-2-lite-v1:0"
}

# ---------------------------------------------------------------- redundancy (ops/up.sh の「冗長化用」)
variable "runtime_az_num" {
  description = "Number of AZs (subnets a, b, c of terraform/base/core from the front) the AgentCore Runtime puts its ENIs in. 1-3, default 1 (user decision 2026-10-05). With 2 or more, ops/up.sh also raises the interface endpoints (endpoints_az_num of terraform/base/core) to the same number, or stops when ENDPOINTS_AZ_NUM is set lower: with the endpoints in subnet a only, the Runtime loses the AWS APIs when a's AZ is down, so the extra AZs would be in name only. The API takes 1-16 subnets (AgentCore Control API Reference, VpcConfig: https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_VpcConfig.html, checked 2026-10-05); the AgentCore VPC guide recommends at least two AZs for high availability but does not forbid one (Amazon Bedrock AgentCore Developer Guide, Configure Amazon Bedrock AgentCore Runtime and tools for VPC, Best practices: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html, checked 2026-10-05). One subnet is not tried on AWS. ops/up.sh passes RUNTIME_AZ_NUM."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2, 3], var.runtime_az_num)
    error_message = "runtime_az_num must be 1, 2 or 3."
  }
}

variable "lambda_az_num" {
  description = "Number of AZs (subnets a, b, c from the front) of the knowledge base index Lambda (kb.tf). 1, 2 or 3; a VPC Lambda costs nothing more per subnet. ops/up.sh passes LAMBDA_AZ_NUM (also to terraform/pipeline/graph and terraform/workflow)."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2, 3], var.lambda_az_num)
    error_message = "lambda_az_num must be 1, 2 or 3."
  }
}

variable "opensearch_az_num" {
  description = "1 or 2. 2 turns on standby replicas (standby_replicas = ENABLED) of the knowledge base collection: a copy in another AZ, and the minimum capacity doubles (these two effects are not found in the current OpenSearch Service Developer Guide - unverified). 3 is not possible: StandbyReplicas is ENABLED or DISABLED only, and the collection has no AZ or subnet setting; changing it requires replacement (CloudFormation reference AWS::OpenSearchServerless::Collection, StandbyReplicas: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-opensearchserverless-collection.html, checked 2026-10-04). Changing it recreates the collection (run the ingestion again). ops/up.sh passes OPENSEARCH_AZ_NUM (also to terraform/pipeline/analytics for the logs collection)."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2], var.opensearch_az_num)
    error_message = "opensearch_az_num must be 1 or 2 (OpenSearch Serverless standby replicas are only on or off)."
  }
}

# ---------------------------------------------------------------- knowledge base (optional) and guardrail
variable "create_knowledge_base" {
  description = "Create the Bedrock Knowledge Base (S3 -> Titan Embeddings v2 -> OpenSearch Serverless) and pass it to the runtime. Off by default: the collection costs about 0.33 USD per hour (1 OCU with standby disabled). Without it the agent answers from the model, the topology tools and the MCP tools only."
  type        = bool
  default     = false
}

variable "guardrail_profile_id" {
  description = "System-defined guardrail profile for cross-Region inference (required by the Standard tier). apac.guardrail.v1:0 for Tokyo."
  type        = string
  default     = "apac.guardrail.v1:0"

  validation {
    condition     = can(regex("^[a-z0-9-]+[.]guardrail[.]v[0-9:]+$", var.guardrail_profile_id))
    error_message = "guardrail_profile_id must look like apac.guardrail.v1:0."
  }
}

variable "number_of_results" {
  description = "Candidate chunks retrieved by the hybrid search per question (before reranking). Used only with create_knowledge_base = true."
  type        = number
  default     = 20

  validation {
    condition     = var.number_of_results >= 1 && var.number_of_results <= 100
    error_message = "number_of_results must be between 1 and 100."
  }
}

variable "rerank_model_id" {
  description = "Reranker model ID (amazon.rerank-v1:0). Leave empty to disable reranking. Used only with create_knowledge_base = true."
  type        = string
  default     = "amazon.rerank-v1:0"

  validation {
    condition     = can(regex("^$|^[a-z0-9-]+[.]rerank-v[0-9-]+:[0-9]+$", var.rerank_model_id))
    error_message = "rerank_model_id must be empty or look like amazon.rerank-v1:0."
  }
}

variable "number_of_reranked_results" {
  description = "Chunks kept after reranking and passed to the model (ignored when rerank_model_id is empty)."
  type        = number
  default     = 5

  validation {
    condition     = var.number_of_reranked_results >= 1 && var.number_of_reranked_results <= 100
    error_message = "number_of_reranked_results must be between 1 and 100."
  }
}

