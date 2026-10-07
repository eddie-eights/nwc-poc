# nwc-oss - OpenSearch of the OSS build (cycle 005). The managed build makes an OpenSearch Serverless TIMESERIES collection
# (terraform/pipeline/analytics/sinks.tf); this one runs OpenSearch on ECS on Fargate instead: two data nodes and one small
# cluster manager node, one ECS service each, with the indexes on the ephemeral storage of the tasks (not on the EFS).

# ---------------------------------------------------------------- OpenSearch（ECS on Fargate）
# sinks に opensearch があるとき、opensearchproject/opensearch を 3 台立てる。
#   - データ 2 台（opensearch-1・opensearch-2）: インデックスを持ち、クライアントの読み書きを受ける。どちらも cluster manager の候補
#   - まとめ役 1 台（opensearch-cm）: node.roles を cluster_manager だけにした小さい台。データ 2 台と合わせて 3 票にし、1 台落ちても過半数が残る
# 台ごとに ECS のサービスを分ける（node.name を台ごとに固定する）。台 N はサブネットの N 番目（データ 1・2 が a・b、まとめ役が c）。
# 名前は Cloud Map の opensearch.<接頭辞>.internal（データ 2 台がどちらも入る。クライアントはここに 9200 で来る）と
# opensearch-cm.<接頭辞>.internal。ECS のサービスは Cloud Map のサービスを 1 つしか持てないので、データの台ごとの名前（opensearch-1 など）は作らない。
# インデックスはタスクのエフェメラルストレージにあり、タスクと一緒に消える（OpenSearch の公式がネットワークファイルシステムを避けるよう書いているので EFS に置かない）。
# 1 台が入れ替わっても、もう 1 台のレプリカ（OpenSearch の既定のレプリカ数 1）から戻る。データ 2 台が同時に落ちるとインデックスは消える。
# terraform apply でタスク定義が変わると 3 つのサービスが同時に入れ替わり、インデックスもクラスターの状態も消える。OSS 版の ops/up.sh は、apply の前に
# 変わる台を plan で調べて 1 台ずつ -target で入れ替え、間で green に戻るのを待つ（oss/ops/roll-nodes.sh。設計の未確定事項 2。OSS_ROLL=0 で一度に入れ替える）。
# 3 台とも cluster manager になれる（データの台は既定の役割）ので、1 台ずつなら残りの 2 台で投票の過半数が残る。
# REST（9200）は TLS なしの HTTP で、セキュリティプラグインの Basic 認証（admin）は効く。Spark（spark/snmp_sinks.py）と Grafana の
# データソース（grafana/provisioning/datasources-oss）に自己署名の証明書を飛ばす設定が無いので、デモの証明書の HTTPS にはしない。
# 台どうし（9300）はデモの証明書の TLS（どの台も同じ証明書。oss/compose で 3 台が green になるのを確かめた）。
# 届くのは SG で絞った相手だけ（terraform/base/core の oss.tf の通信の表: Spark・Grafana・AgentCore・Lambda → 9200、OpenSearch どうし 9300）。
# admin のパスワードは OSS 版の ops/up.sh が作る SSM の SecureString（/<接頭辞>/opensearch-password。agent/evidence.py が読むのと同じ名前）を
# secrets で受ける。初めて起きるときにセキュリティプラグインがこの値で admin を作る（強いパスワードでないと起きない）。
# イメージは opensearchproject/opensearch を ECR の <接頭辞>-opensearch に写したもの（閉域で Docker Hub に届かない。OSS 版の ops/up.sh が写す）

variable "opensearch_image_tag" {
  description = "Tag of the OpenSearch image in the <prefix>-opensearch repository (opensearchproject/opensearch copied to ECR by the OSS ops/up.sh). Same version as oss/compose."
  type        = string
  default     = "3.9.0"
}

variable "opensearch_data_task_cpu" {
  description = "Fargate CPU units of each OpenSearch data node task (ARM64)."
  type        = number
  default     = 1024

  validation {
    condition     = contains([1024, 2048, 4096], var.opensearch_data_task_cpu)
    error_message = "opensearch_data_task_cpu must be 1024, 2048 or 4096."
  }
}

variable "opensearch_data_task_memory" {
  description = "Fargate memory (MiB) of each OpenSearch data node task. Half of it is the JVM heap, the rest is left to the page cache. Must be a valid pair with opensearch_data_task_cpu (1024 takes 2048-8192)."
  type        = number
  default     = 4096

  validation {
    condition     = contains([2048, 4096, 8192, 16384], var.opensearch_data_task_memory)
    error_message = "opensearch_data_task_memory must be 2048, 4096, 8192 or 16384."
  }
}

variable "opensearch_cm_task_cpu" {
  description = "Fargate CPU units of the OpenSearch cluster manager task (ARM64). It holds no data."
  type        = number
  default     = 512

  validation {
    condition     = contains([512, 1024], var.opensearch_cm_task_cpu)
    error_message = "opensearch_cm_task_cpu must be 512 or 1024."
  }
}

variable "opensearch_cm_task_memory" {
  description = "Fargate memory (MiB) of the OpenSearch cluster manager task. Half of it is the JVM heap. Must be a valid pair with opensearch_cm_task_cpu (512 takes 1024-4096)."
  type        = number
  default     = 1024

  validation {
    condition     = contains([1024, 2048, 4096], var.opensearch_cm_task_memory)
    error_message = "opensearch_cm_task_memory must be 1024, 2048 or 4096."
  }
}

variable "opensearch_ephemeral_storage_gib" {
  description = "Ephemeral storage of each OpenSearch data node task (GiB, 21-200). The indexes live here and vanish with the task; the replica on the other data node brings them back. The cluster manager task keeps the Fargate default (20 GiB)."
  type        = number
  default     = 30

  validation {
    condition     = var.opensearch_ephemeral_storage_gib >= 21 && var.opensearch_ephemeral_storage_gib <= 200
    error_message = "opensearch_ephemeral_storage_gib は 21〜200。"
  }
}

locals {
  opensearch_image     = "${try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["opensearch"], "")}:${var.opensearch_image_tag}"
  opensearch_log_group = "/ecs/${local.name_prefix}-opensearch"
  # 土台（terraform/base/core の oss.tf）の SG。マネージド版の土台や古い state では無いので try にして、precondition で止める
  opensearch_sg_id = try(data.terraform_remote_state.main.outputs.security_group_ids["opensearch"], "")

  # 台（node.name の末尾）→ サブネット。データ 2 台が a・b、まとめ役が c
  opensearch_nodes = {
    "1"  = { subnet = try(local.subnet_ids[0], ""), data = true }
    "2"  = { subnet = try(local.subnet_ids[1], ""), data = true }
    "cm" = { subnet = try(local.subnet_ids[2], ""), data = false }
  }
  opensearch_host    = "opensearch.${local.service_namespace}"
  opensearch_cm_host = "opensearch-cm.${local.service_namespace}"

  # admin のパスワード。agent/evidence.py が <PARAM_PREFIX>/opensearch-password で読む名前（PARAM_PREFIX は /<接頭辞>）
  opensearch_password_parameter = "/${local.name_prefix}/opensearch-password"
  opensearch_password_arn       = "${local.ssm_parameter_arn}${local.opensearch_password_parameter}"

  # 公式イメージは小文字とドットの環境変数を opensearch -E に変える（oss/compose の compose.yaml と同じ。違うのはホスト名が Cloud Map の名前なのと、
  # REST が HTTP なのと、ヒープ）
  opensearch_environment = [
    { name = "cluster.name", value = local.name_prefix },
    # Cloud Map の名前を引いて出てきた IP を全部たずねる（opensearch はデータ 2 台の IP を両方返す）
    { name = "discovery.seed_hosts", value = "${local.opensearch_host},${local.opensearch_cm_host}" },
    # 最初に 1 回だけ使う投票の顔ぶれ（node.name）。インデックスとクラスターの状態がタスクの中にあるので、3 台そろって作り直されたときもこれで組み直す
    { name = "cluster.initial_cluster_manager_nodes", value = "opensearch-1,opensearch-2,opensearch-cm" },
    # 台どうしに知らせる自分のアドレス。タスクの ENI の VPC の IP（_site_ はプライベートのアドレス）
    { name = "network.publish_host", value = "_site_" },
    # mmap を使わない。Fargate では vm.max_map_count を上げられない（変えずに、この設定で起動時の検査を通す）
    { name = "node.store.allow_mmap", value = "false" },
    # REST を HTTP にする（上の説明）。台どうしの TLS（plugins.security.ssl.transport）はデモの設定のまま
    { name = "plugins.security.ssl.http.enabled", value = "false" },
    { name = "DISABLE_PERFORMANCE_ANALYZER_AGENT_CLI", value = "true" },
  ]
}

resource "aws_cloudwatch_log_group" "opensearch" {
  count = local.sink_opensearch ? 1 : 0

  name              = local.opensearch_log_group
  retention_in_days = var.log_retention_days
}

# データ 2 台がどちらも入る名前（opensearch）と、まとめ役の名前（opensearch-cm）
resource "aws_service_discovery_service" "opensearch" {
  for_each = local.sink_opensearch ? toset(["opensearch", "opensearch-cm"]) : toset([])

  name = each.key
  # タスクの登録が残っていても destroy できるようにする
  force_destroy = true

  # タスクを作り直すと IP が変わる。TTL を短くして、ほかの台とクライアントが早く新しい IP を引くようにする
  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.analytics[0].id
    routing_policy = "MULTIVALUE"

    dns_records {
      ttl  = 10
      type = "A"
    }
  }
}

# ヘルスチェックは付けない。LB が無いので入れ替えの判断にしか使われず、レプリカを戻しているあいだの台を ECS が止めてしまう
resource "aws_ecs_task_definition" "opensearch" {
  for_each = local.sink_opensearch ? local.opensearch_nodes : {}

  family                   = "${local.name_prefix}-opensearch-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.data ? var.opensearch_data_task_cpu : var.opensearch_cm_task_cpu
  memory                   = each.value.data ? var.opensearch_data_task_memory : var.opensearch_cm_task_memory
  execution_role_arn       = aws_iam_role.opensearch_execution[0].arn
  task_role_arn            = aws_iam_role.opensearch_task[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # インデックスの置き場（データの台だけ広げる。まとめ役は Fargate の既定の 20 GiB）
  dynamic "ephemeral_storage" {
    for_each = each.value.data ? [var.opensearch_ephemeral_storage_gib] : []

    content {
      size_in_gib = ephemeral_storage.value
    }
  }

  container_definitions = jsonencode([
    {
      name         = "opensearch"
      image        = local.opensearch_image
      essential    = true
      portMappings = [{ containerPort = 9200, protocol = "tcp" }, { containerPort = 9300, protocol = "tcp" }]
      environment = concat(local.opensearch_environment, [
        { name = "node.name", value = "opensearch-${each.key}" },
        { name = "OPENSEARCH_JAVA_OPTS", value = "-Xms${floor((each.value.data ? var.opensearch_data_task_memory : var.opensearch_cm_task_memory) / 2)}m -Xmx${floor((each.value.data ? var.opensearch_data_task_memory : var.opensearch_cm_task_memory) / 2)}m" },
        ], each.value.data ? [] : [
        { name = "node.roles", value = "cluster_manager" },
      ])
      secrets = [{ name = "OPENSEARCH_INITIAL_ADMIN_PASSWORD", valueFrom = local.opensearch_password_arn }]
      # 起動時の検査（ファイルディスクリプタが 65535 以上）
      ulimits = [{ name = "nofile", softLimit = 65535, hardLimit = 65535 }]
      # 止めるときにシャードを閉じる時間（Fargate の上限）
      stopTimeout = 120
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.opensearch[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "opensearch-${each.key}"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["opensearch"], "") != ""
      error_message = "terraform/base/ecr の state に opensearch のリポジトリが無い（project = nwc-oss でない ECR か、古い ECR）。oss/terraform/base/ecr を先に apply する。"
    }
  }
}

resource "aws_ecs_service" "opensearch" {
  for_each = local.sink_opensearch ? local.opensearch_nodes : {}

  name            = "${local.name_prefix}-opensearch-${each.key}"
  cluster         = aws_ecs_cluster.analytics[0].id
  task_definition = aws_ecs_task_definition.opensearch[each.key].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # aws ecs execute-command でタスクの中に入れる（curl で _cluster/health などを見る）
  enable_execute_command = true

  # 同じ node.name の台を 2 つ同時に立てない。入れ替えでは古いタスクを止めてから新しいタスクを起こす
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = [each.value.subnet]
    security_groups  = [local.opensearch_sg_id]
    assign_public_ip = false
  }

  # データ 2 台は同じ opensearch に入る
  service_registries {
    registry_arn = aws_service_discovery_service.opensearch[each.value.data ? "opensearch" : "opensearch-cm"].arn
  }

  lifecycle {
    precondition {
      condition     = local.opensearch_sg_id != ""
      error_message = "terraform/base/core の state に opensearch の SG が無い（マネージド版の土台か、oss.tf より前の土台）。oss/terraform/base/core を先に apply する。"
    }
    precondition {
      condition     = each.value.subnet != ""
      error_message = "terraform/base/core の state のサブネットが 3 つ無い（OpenSearch は AZ ごとに 1 台で 3 つ要る）。"
    }
  }

  depends_on = [
    aws_iam_role_policy.opensearch_task,
    aws_iam_role_policy.opensearch_execution,
    aws_iam_role_policy_attachment.opensearch_execution,
  ]
}

# ---------------------------------------------------------------- IAM（3 台で同じロール）
resource "aws_iam_role" "opensearch_execution" {
  count = local.sink_opensearch ? 1 : 0

  name               = "${local.name_prefix}-opensearch-exec"
  description        = "ECS task execution role of the OpenSearch tasks (ECR pull, CloudWatch Logs, admin password from SSM)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "opensearch_execution" {
  count = local.sink_opensearch ? 1 : 0

  role       = aws_iam_role.opensearch_execution[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# secrets の admin のパスワード（SecureString でも AWS 管理の aws/ssm キーなので kms:Decrypt は要らない）
resource "aws_iam_role_policy" "opensearch_execution" {
  count = local.sink_opensearch ? 1 : 0

  name = "${local.name_prefix}-opensearch-exec"
  role = aws_iam_role.opensearch_execution[0].name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "AdminPassword"
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = local.opensearch_password_arn
    }]
  })
}

resource "aws_iam_role" "opensearch_task" {
  count = local.sink_opensearch ? 1 : 0

  name               = "${local.name_prefix}-opensearch-task"
  description        = "OpenSearch task role - ECS Exec only (the indexes are on the ephemeral storage, no AWS API is called)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "opensearch_task" {
  count = local.sink_opensearch ? 1 : 0

  name = "${local.name_prefix}-opensearch-task"
  role = aws_iam_role.opensearch_task[0].name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      # ECS Exec（aws ecs execute-command）
      Sid    = "EcsExec"
      Effect = "Allow"
      Action = [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
      ]
      Resource = "*"
    }]
  })
}

# terraform/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない呼び出しを拒む）。実行ロールとタスクロールの両方に付ける
resource "aws_iam_role_policy_attachment" "opensearch_execution_perimeter" {
  count = local.sink_opensearch && local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.opensearch_execution[0].name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_iam_role_policy_attachment" "opensearch_task_perimeter" {
  count = local.sink_opensearch && local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.opensearch_task[0].name
  policy_arn = local.perimeter_policy_arn
}

# ---------------------------------------------------------------- OpenSearch の差し替え口（マネージド版は locals.tf が同じ名前で定義する）
locals {
  # Spark のジョブの書き先（spark/snmp_sinks.py が <これ>/<index>/_bulk に送る）と Grafana のデータソースの URL
  opensearch_endpoint = local.sink_opensearch ? "http://${local.opensearch_host}:9200" : ""
}

# ---------------------------------------------------------------- outputs
# terraform/workflow が読む名前はマネージド版の outputs.tf と同じ。opensearch_collection_name / _arn は出さない
# （terraform/workflow は空のとき OpenSearch Serverless の data access policy と IAM を作らない）
output "opensearch_collection_endpoint" {
  description = "OpenSearch on ECS the log topics go to (http, Basic authentication as admin; empty unless sinks has opensearch). Same output name as the managed build, which terraform/workflow reads"
  value       = local.opensearch_endpoint
}

output "opensearch_index" {
  description = "Index the log topics go to"
  value       = local.opensearch_index
}

output "opensearch_password_parameter" {
  description = "SSM parameter (SecureString) holding the admin password of OpenSearch. The OSS ops/up.sh creates it before this root is applied; agent/evidence.py reads the same name"
  value       = local.opensearch_password_parameter
}

output "opensearch_service_names" {
  description = "ECS service of each OpenSearch node, keyed by 1, 2 (data) and cm (cluster manager). Replace the data nodes one at a time and wait for green in between - the indexes are on the ephemeral storage. The OSS ops/up.sh does it (oss/ops/roll-nodes.sh)"
  value       = { for n, s in aws_ecs_service.opensearch : n => s.name }
}

output "opensearch_log_group_name" {
  description = "CloudWatch Logs group of the OpenSearch tasks (stream prefix opensearch-1 / opensearch-2 / opensearch-cm)"
  value       = local.sink_opensearch ? aws_cloudwatch_log_group.opensearch[0].name : ""
}
