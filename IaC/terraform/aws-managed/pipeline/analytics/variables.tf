variable "region" {
  description = "Region. IaC/terraform/aws-managed/base/core and IaC/terraform/aws-managed/pipeline/stream must be in the same region"
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

variable "emr_release_label" {
  description = "EMR Serverless release. 7.14.0 = Spark 3.5.8 (checked 2026-10-08). The Kafka jars that ops/up.sh uploads are pinned to this Spark version - change both together"
  type        = string
  default     = "emr-7.14.0"

  validation {
    condition     = can(regex("^emr-7\\.(5|[6-9]|1[0-9])\\.[0-9]+$", var.emr_release_label))
    error_message = "S3 Tables は EMR 7.5.0 以上（AWS ドキュメント、2026-09-17 確認）。emr-7.5.0〜emr-7.19.x の形で書く。"
  }
}

variable "namespace" {
  description = "S3 Tables namespace (lowercase letters, digits, underscores - no hyphens)"
  type        = string
  default     = "netops"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9_]{0,254}$", var.namespace)) && !startswith(var.namespace, "aws")
    error_message = "namespace は小文字英数字とアンダースコア、1〜255 文字、aws で始めない（S3 Tables の命名規則。ハイフンは使えない）。"
  }
}

variable "table_name" {
  description = "S3 Tables table that the Spark job appends the Telegraf messages to (lowercase letters, digits, underscores)"
  type        = string
  default     = "raw_telemetry"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9_]{0,254}$", var.table_name))
    error_message = "table_name は小文字英数字とアンダースコア、1〜255 文字（S3 Tables の命名規則。ハイフンは使えない）。"
  }
}

# ops/up.sh は EMR_MAX_CPU / EMR_MAX_MEMORY（同じ値）を渡し、アプリの上限が変わるときは apply の前にジョブとアプリを止める（動いているアプリは更新できない）
variable "max_cpu" {
  description = "Upper bound of vCPU the application may use at once (EMR Serverless maximumCapacity). The streaming jobs (up to 3: sinks-s3iceberg / sinks-splunk / sinks-grafana) ask for 3 each (driver 1 + executor 2), 9 in all"
  type        = string
  default     = "12 vCPU"
}

variable "max_memory" {
  description = "Upper bound of memory the application may use at once (4 GB per vCPU of max_cpu)"
  type        = string
  default     = "48 GB"
}

variable "emr_az_num" {
  description = "Number of AZs (subnets a, b, c of IaC/terraform/aws-managed/base/core from the front) the EMR Serverless application may start its workers in. 1, 2 or 3. More AZs do not cost more by themselves (workers are billed by vCPU and memory), but traffic to MSK and the endpoints in another AZ is cross-AZ. ops/up.sh passes EMR_AZ_NUM."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2, 3], var.emr_az_num)
    error_message = "emr_az_num must be 1, 2 or 3."
  }
}

variable "idle_timeout_minutes" {
  description = "Minutes without a running job before the application stops itself (it restarts on the next start-job-run)"
  type        = number
  default     = 15
}

variable "log_retention_days" {
  description = "Retention of the CloudWatch log group the job driver writes to"
  type        = number
  default     = 7
}

variable "cloudwatch_logging" {
  description = "Send the driver stdout / stderr to CloudWatch Logs (through the logs interface endpoint of the base root). false keeps the logs only in the asset bucket"
  type        = bool
  default     = true
}

variable "opensearch_az_num" {
  description = "1 or 2. 2 turns on standby replicas (standby_replicas = ENABLED) of the logs collection: OpenSearch Serverless keeps a copy in another AZ and the minimum capacity doubles (these two effects are not found in the current OpenSearch Service Developer Guide - unverified). 3 is not possible: StandbyReplicas is ENABLED or DISABLED only, and the collection has no AZ or subnet setting; changing it requires replacement (CloudFormation reference AWS::OpenSearchServerless::Collection, StandbyReplicas: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-opensearchserverless-collection.html, checked 2026-10-04). Changing it recreates the collection (the indexed logs are lost). ops/up.sh passes OPENSEARCH_AZ_NUM (also to IaC/terraform/aws-managed/agent for the knowledge base)."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2], var.opensearch_az_num)
    error_message = "opensearch_az_num must be 1 or 2 (OpenSearch Serverless standby replicas are only on or off)."
  }
}

variable "sinks" {
  description = "Where the Spark job stores the Telegraf messages: iceberg (all topics to S3 Tables, tables.tf), opensearch (log topics to an OpenSearch Serverless TIMESERIES collection made here), prometheus (metric topics to an Amazon Managed Service for Prometheus workspace made here), splunk (all topics to the HTTP Event Collector of the Splunk Enterprise on ECS made here in splunk.tf). One streaming query per entry. splunk is not in the default because the Splunk on ECS costs about 0.12 USD/h and accepts the Splunk license"
  type        = list(string)
  default     = ["iceberg", "opensearch", "prometheus"]

  validation {
    condition     = length(var.sinks) > 0 && length(setsubtract(var.sinks, ["iceberg", "opensearch", "prometheus", "splunk"])) == 0
    error_message = "sinks は iceberg / opensearch / prometheus / splunk のリスト（1 つ以上）。"
  }
}

variable "http_send" {
  description = "Where the Spark job sends to the HTTP sinks (opensearch / prometheus / splunk): driver = collect each micro-batch and send from the driver, executor = foreachPartition, each executor sends its own partitions. ops/up.sh passes HTTP_SEND. The job gets --http-send only when it is executor, so the default leaves the job arguments as they were"
  type        = string
  default     = "driver"

  validation {
    condition     = contains(["driver", "executor"], var.http_send)
    error_message = "http_send は driver か executor。"
  }
}

variable "max_offsets_per_trigger" {
  description = "Most Kafka records one streaming query reads per trigger (60 s), summed over the partitions (Spark maxOffsetsPerTrigger). 0 = no limit. Every job gets --max-offsets-per-trigger. ops/up.sh passes MAX_OFFSETS_PER_TRIGGER"
  type        = number
  default     = 10000

  validation {
    condition     = var.max_offsets_per_trigger >= 0 && floor(var.max_offsets_per_trigger) == var.max_offsets_per_trigger
    error_message = "max_offsets_per_trigger は 0 以上の整数（0 で上限なし）。"
  }
}

variable "max_offsets_per_trigger_by_sink" {
  description = "Per-sink override of max_offsets_per_trigger (keys iceberg / opensearch / prometheus / splunk; 0 = no limit for that sink's query). Sinks not in the map use max_offsets_per_trigger. A job gets --max-offsets-per-trigger-by-sink only for its own sinks. ops/up.sh passes MAX_OFFSETS_PER_TRIGGER_<SINK>"
  type        = map(number)
  default     = {}

  validation {
    condition     = alltrue([for k, v in var.max_offsets_per_trigger_by_sink : contains(["iceberg", "opensearch", "prometheus", "splunk"], k) && v >= 0 && floor(v) == v])
    error_message = "max_offsets_per_trigger_by_sink のキーは iceberg / opensearch / prometheus / splunk、値は 0 以上の整数（0 でその格納先だけ上限なし）。"
  }
}

# ---------------------------------------------------------------- splunk (only when sinks has splunk)
variable "splunk_hec_token_parameter" {
  description = "Name of the SSM SecureString parameter that holds the HEC token. The job reads it at start with the runtime role (ssm:GetParameter through the ssm endpoint of IaC/terraform/aws-managed/base/core); Terraform never reads the value. Empty = /<prefix>/splunk/hec-token. ops/up.sh generates it and the Splunk task on ECS makes the HEC token from it"
  type        = string
  default     = ""

  validation {
    condition     = var.splunk_hec_token_parameter == "" || can(regex("^/[A-Za-z0-9_.\\-/]+$", var.splunk_hec_token_parameter))
    error_message = "splunk_hec_token_parameter は / で始まる SSM のパラメータ名（英数字と _ . - /）。"
  }
}

variable "splunk_index" {
  description = "Splunk index the events go to. Empty = the default index of the HEC token"
  type        = string
  default     = ""
}

variable "splunk_image_tag" {
  description = "Tag of the app/splunk/ image (splunk/splunk plus the netops_alerts app) in the ECR repository <prefix>-splunk. ops/up.sh builds it as <Splunk version>-<hash of app/splunk/> (amd64 only, so the task is X86_64). Used only when sinks has splunk"
  type        = string
  default     = "10.4.3"

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.splunk_image_tag))
    error_message = "splunk_image_tag は ECR のタグの形（英数字と _ . -、128 文字まで）。"
  }
}

variable "splunk_task_cpu" {
  description = "vCPU units of the Splunk task (Splunk asks for 2 vCPU or more even for a trial)"
  type        = number
  default     = 2048

  validation {
    condition     = contains([2048, 4096], var.splunk_task_cpu)
    error_message = "splunk_task_cpu は 2048 か 4096。"
  }
}

variable "splunk_task_memory" {
  description = "Memory (MiB) of the Splunk task"
  type        = number
  default     = 4096

  validation {
    condition     = var.splunk_task_memory >= 4096 && var.splunk_task_memory <= 16384 && var.splunk_task_memory % 1024 == 0
    error_message = "splunk_task_memory は 4096〜16384 の 1024 の倍数。"
  }
}

variable "splunk_az_num" {
  description = "Number of AZs of the Splunk on ECS (splunk.tf). 1 = one standalone task (as before). 2 or 3 = an indexer cluster: one cluster manager (splunk-cm), one indexer per AZ (subnets a, b, c from the front; splunk-idx, which the HEC URL points to) replicating every event to each other, and one search head (splunk, the saved searches and the UI). Fargate spreads the indexers across the AZs on a best-effort basis. ops/up.sh passes SPLUNK_AZ_NUM"
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2, 3], var.splunk_az_num)
    error_message = "splunk_az_num must be 1, 2 or 3."
  }
}

variable "splunk_ephemeral_storage_gib" {
  description = "Ephemeral storage of the Splunk task (GiB, 21-200). Indexes live here and vanish with the task; Splunk stops indexing below 5 GB free"
  type        = number
  default     = 40

  validation {
    condition     = var.splunk_ephemeral_storage_gib >= 21 && var.splunk_ephemeral_storage_gib <= 200
    error_message = "splunk_ephemeral_storage_gib は 21〜200。"
  }
}

# ---------------------------------------------------------------- grafana (grafana.tf)
variable "create_grafana" {
  description = "Run Grafana OSS on ECS (grafana.tf) with the Prometheus workspace and the OpenSearch logs collection as data sources. Opened through an SSM port forward via the web EC2. ops/up.sh sets it whenever STORES in deploy.env has grafana. Needs prometheus or opensearch in sinks"
  type        = bool
  default     = false
}

variable "grafana_image_tag" {
  description = "Tag of the app/grafana/ image in the ECR repository <prefix>-grafana. ops/up.sh builds it as <Grafana version>-<hash of app/grafana/>"
  type        = string
  default     = "13.2.2"

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.grafana_image_tag))
    error_message = "grafana_image_tag は ECR のタグの形（英数字と _ . -、128 文字まで）。"
  }
}

variable "grafana_task_cpu" {
  description = "vCPU units of the Grafana task"
  type        = number
  default     = 512

  validation {
    condition     = contains([256, 512, 1024], var.grafana_task_cpu)
    error_message = "grafana_task_cpu は 256 / 512 / 1024。"
  }
}

variable "grafana_task_memory" {
  description = "Memory (MiB) of the Grafana task"
  type        = number
  default     = 1024

  validation {
    condition     = contains([512, 1024, 2048], var.grafana_task_memory)
    error_message = "grafana_task_memory は 512 / 1024 / 2048。"
  }
}

# ---------------------------------------------------------------- alerts (Grafana / Splunk -> SNS topic of IaC/terraform/aws-managed/base/core)
variable "device_map" {
  description = "alias=device_id,... (management IPs, interface and loopback addresses, hostnames). Used where a record has no sysName tag (traps and gNMI carry the management IP in source): the Splunk task gets it as DEVICE_MAP for its alert action (app/splunk/netops_alerts), and the Spark job gets it as --device-map to add sysName to the prometheus and opensearch sinks (the Grafana rules group by sysName). ops/up.sh always generates it from the lab definition with app/containerlab/lab_topology.py --device-map, so the device list lives in one place. Empty means such alerts keep the raw IP as device_id (they do not match a device in Neptune). ops/up.sh restarts the streaming job when the arguments change."
  type        = string
  default     = ""
}

variable "metric_topics" {
  description = "Kafka topics that carry metrics (Telegraf inputs.snmp and the common shape converted from the lab gNMI -> metrics, inputs.gnmi -> gnmi, inputs.cisco_telemetry_mdt -> mdt). Read by the iceberg, prometheus and splunk sinks"
  type        = list(string)
  default     = ["metrics", "gnmi", "mdt"]

  validation {
    condition     = length(var.metric_topics) > 0
    error_message = "metric_topics は 1 つ以上。"
  }
}

variable "log_topics" {
  description = "Kafka topics that carry logs (traps = Telegraf inputs.snmp_trap, logs = syslog of the SR Linux routers, DNATed by the lab EC2 to the Telegraf NLB). Read by the iceberg and opensearch sinks"
  type        = list(string)
  default     = ["traps", "logs"]

  validation {
    condition     = length(var.log_topics) > 0
    error_message = "log_topics は 1 つ以上。"
  }
}
