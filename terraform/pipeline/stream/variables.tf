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

# ---------------------------------------------------------------- MSK
variable "kafka_version" {
  description = "MSK provisioned Kafka version, KRaft mode only (the .kraft suffix selects KRaft; Kafka 4 has no ZooKeeper mode). 4.1.x is the newest for Standard brokers, 4.2.x is Express brokers only (list-kafka-versions and the MSK supported versions page, checked 2026-09-18)."
  type        = string
  default     = "4.1.x.kraft"

  validation {
    condition     = can(regex("^([4-9]|[1-9][0-9])\\.[0-9]+\\.x\\.kraft$", var.kafka_version))
    error_message = "kafka_version must be 4.0.x.kraft or newer (KRaft mode), e.g. 4.1.x.kraft."
  }
}

variable "broker_instance_type" {
  description = "Smallest Standard broker that Kafka 4.x (KRaft) accepts. kafka.t3.small is rejected by CreateCluster with 4.1.x.kraft (Unsupported InstanceType, seen 2026-09-18); it is only for 3.x. kafka.m5.large is 0.271 USD per hour per broker in Tokyo, so 0.542 for 2 brokers and 0.813 for 3 (msk_az_num; Price List API, 2026-09-18)."
  type        = string
  default     = "kafka.m5.large"

  validation {
    condition     = contains(["kafka.m5.large", "kafka.m7g.large"], var.broker_instance_type)
    error_message = "broker_instance_type must be kafka.m5.large or kafka.m7g.large (Kafka 4.x does not accept kafka.t3.small)."
  }
}

variable "msk_az_num" {
  description = "Number of AZs (subnets a, b, c of terraform/base/core from the front) of the MSK cluster, one broker per AZ. 2 or 3; 1 is not possible because MSK takes client subnets in two or three AZs only (Amazon MSK API Reference, Clusters, BrokerNodeGroupInfo.clientSubnets: https://docs.aws.amazon.com/msk/1.0/apireference/clusters.html, checked 2026-10-04). 2 keeps replication factor 2 / min.insync.replicas 1, 3 uses 3 / 2. Each broker is about 0.271 USD/h (kafka.m5.large). Changing it on a live cluster recreates the cluster (the topics are lost). ops/up.sh passes MSK_AZ_NUM."
  type        = number
  default     = 2

  validation {
    condition     = contains([2, 3], var.msk_az_num)
    error_message = "msk_az_num must be 2 or 3 (MSK puts its brokers in two or three AZs; one AZ is not possible)."
  }
}

variable "log_retention_days" {
  description = "Retention of the broker log group."
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 7, 14, 30], var.log_retention_days)
    error_message = "log_retention_days must be one of 1, 3, 7, 14, 30."
  }
}

# ---------------------------------------------------------------- Telegraf (telegraf.tf)
variable "telegraf_image_tag" {
  description = "Tag of the Telegraf image in the <prefix>-telegraf repository (telegraf/Dockerfile). ops/up.sh builds it as <telegraf version>-<hash of telegraf/> and passes it."
  type        = string
  default     = "1.40.0"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]{1,128}$", var.telegraf_image_tag))
    error_message = "telegraf_image_tag must be a valid ECR tag (letters, digits, . _ -)."
  }
}

variable "snmp_agents" {
  description = "SNMP polling targets of the Telegraf dial-in task, as the inside of a TOML list (\"udp://<IP>:161\", ...). ops/up.sh makes it from the lab definition (python3 lab/lab_topology.py lab --snmp-agents). Goes to the SSM parameter .../telegraf-dialin/lab/snmp-agents (or only the first value of .../nautobot/snmp-agents with dialin_targets_from_nautobot)."
  type        = string

  validation {
    condition     = can(regex("^\"udp://[0-9.]+:[0-9]+\"(, *\"udp://[0-9.]+:[0-9]+\")*$", var.snmp_agents))
    error_message = "snmp_agents must look like \"udp://203.0.113.11:161\", \"udp://203.0.113.12:161\" (python3 lab/lab_topology.py lab --snmp-agents)."
  }
}

variable "gnmi_targets" {
  description = "gNMI subscription targets of the Telegraf dial-in task, as the inside of a TOML list (\"<IP>:57400\", ...). ops/up.sh makes it from the lab definition (python3 lab/lab_topology.py lab --gnmi-targets). Goes to the SSM parameter .../telegraf-dialin/lab/gnmi-targets (or only the first value of .../nautobot/gnmi-targets with dialin_targets_from_nautobot)."
  type        = string

  validation {
    condition     = can(regex("^\"[0-9.]+:[0-9]+\"(, *\"[0-9.]+:[0-9]+\")*$", var.gnmi_targets))
    error_message = "gnmi_targets must look like \"203.0.113.11:57400\", \"203.0.113.12:57400\" (python3 lab/lab_topology.py lab --gnmi-targets)."
  }
}

variable "dialin_targets_from_nautobot" {
  description = "Whether the Nautobot job (terraform/pipeline/nautobot) owns the dial-in targets. true moves them to /<prefix>/telegraf-dialin/nautobot/* (Terraform writes only the first value, from snmp_agents / gnmi_targets, and ignores later changes); false keeps them in /<prefix>/telegraf-dialin/lab/* from the variables. ops/up.sh sets it from NAUTOBOT."
  type        = bool
  default     = false
}

variable "syslog_standard" {
  description = "Format of the device syslog that Telegraf parses (inputs.syslog syslog_standard). RFC3164 is the BSD format of Cisco IOS, the production devices. ops/up.sh passes SYSLOG_STANDARD from deploy.env (default RFC3164). The SR Linux lab sends RFC5424 (LAB_SYSLOG_STANDARD in ops/lab-common.sh)."
  type        = string
  default     = "RFC3164"

  validation {
    condition     = contains(["RFC3164", "RFC5424"], var.syslog_standard)
    error_message = "syslog_standard must be RFC3164 or RFC5424."
  }
}

variable "snmp_poll" {
  description = "Whether Telegraf polls SNMP (inputs.snmp, ifTable every 10 seconds) and writes it to the metrics topic. On by default: the Grafana rule link_down and the Splunk saved search netops_poll read the polled ifOperStatus. With false, SNMP comes in as traps only and link down is seen only by the Splunk saved search on traps (splunk in STORES of deploy.env). ops/up.sh passes SNMP_POLL from deploy.env. Becomes SNMP_POLL (1 / 0) of the task."
  type        = bool
  default     = true
}

variable "telegraf_az_num" {
  description = "Number of AZs (subnets a, b, c from the front) of the Telegraf dial-out side: the NLB subnets and the number of dial-out tasks (one per AZ). 1, 2 or 3. The dial-in task stays one in subnet a (two would poll and subscribe twice). SSM /<prefix>/telegraf-address stays the NLB address in subnet a (the lab DNATs to it); real devices should send to output telegraf_dialout_dns_name. With 2 or 3 the NLB balances across zones, so subnet a's address still reaches the tasks in b / c. ops/up.sh passes TELEGRAF_AZ_NUM."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2, 3], var.telegraf_az_num)
    error_message = "telegraf_az_num must be 1, 2 or 3."
  }
}

variable "telegraf_task_cpu" {
  description = "Fargate CPU units of the Telegraf task (ARM64). 256 (0.25 vCPU) is enough for 6 SNMP agents, 6 gNMI subscriptions, traps and syslog."
  type        = number
  default     = 256

  validation {
    condition     = contains([256, 512, 1024], var.telegraf_task_cpu)
    error_message = "telegraf_task_cpu must be 256, 512 or 1024."
  }
}

variable "telegraf_task_memory" {
  description = "Fargate memory (MiB) of the Telegraf task. Must be a valid pair with telegraf_task_cpu (256 takes 512-2048)."
  type        = number
  default     = 512

  validation {
    condition     = contains([512, 1024, 2048], var.telegraf_task_memory)
    error_message = "telegraf_task_memory must be 512, 1024 or 2048."
  }
}

# ---------------------------------------------------------------- Kafbat UI (kafka_ui.tf)
# Always created with this root (no switch, user decision of 2026-10-05). Opened through an SSM port forward via the web EC2
# (output kafka_ui_port_forward_command), with a login form whose admin password is an SSM SecureString created by ops/up.sh
variable "kafka_ui_image_tag" {
  description = "Tag of the Kafbat UI image in the ECR repository <prefix>-kafka-ui. ops/up.sh mirrors ghcr.io/kafbat/kafka-ui:<KAFKA_UI_TAG> with the same tag and passes it."
  type        = string
  default     = "v1.5.0"

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.kafka_ui_image_tag))
    error_message = "kafka_ui_image_tag must be a valid ECR tag (letters, digits, _ . -, up to 128 characters)."
  }
}

variable "kafka_ui_security_protocol" {
  description = "How Kafbat UI talks to Kafka. SASL_SSL adds the MSK IAM settings (AWS_MSK_IAM with the task role, port 9098 - this root's MSK). PLAINTEXT adds none, for a Kafka without authentication (the OSS Kafka of cycle 005)."
  type        = string
  default     = "SASL_SSL"

  validation {
    condition     = contains(["SASL_SSL", "PLAINTEXT"], var.kafka_ui_security_protocol)
    error_message = "kafka_ui_security_protocol must be SASL_SSL (MSK IAM) or PLAINTEXT."
  }
}

variable "kafka_ui_task_cpu" {
  description = "Fargate CPU units of the Kafbat UI task (ARM64). 512 (0.5 vCPU) started in about 14 seconds in a local Docker test limited to 0.5 CPU (2026-10-05)."
  type        = number
  default     = 512

  validation {
    condition     = contains([256, 512, 1024], var.kafka_ui_task_cpu)
    error_message = "kafka_ui_task_cpu must be 256, 512 or 1024."
  }
}

variable "kafka_ui_task_memory" {
  description = "Fargate memory (MiB) of the Kafbat UI task. Must be a valid pair with kafka_ui_task_cpu. The JVM takes 75% of it (JAVA_OPTS); about 210 MiB was used when idle in a local Docker test (2026-10-05)."
  type        = number
  default     = 1024

  validation {
    condition     = contains([512, 1024, 2048], var.kafka_ui_task_memory)
    error_message = "kafka_ui_task_memory must be 512, 1024 or 2048."
  }
}
