# nwc-oss - VictoriaMetrics of the OSS build (cycle 005). The managed build makes an Amazon Managed Service for Prometheus
# workspace (IaC/terraform/aws-managed/pipeline/analytics/sinks.tf); this one runs the cluster version of VictoriaMetrics on ECS on Fargate
# instead: vminsert 1, vmselect 1 and vmstorage 3 (one ECS service and one EFS access point each), replication factor 2.

# ---------------------------------------------------------------- VictoriaMetrics のクラスター（ECS on Fargate）
# sinks に prometheus があるとき、5 つのサービスを立てる。
#   - vminsert（vminsert:8480）: Spark の remote write（/insert/0/prometheus/api/v1/write）を受け、vmstorage の 2 台に書く（複製数 2）
#   - vmselect（vmselect:8481）: Grafana とエージェントの道具の PromQL（/select/0/prometheus/api/v1/...）を受け、vmstorage の 3 台から読んで重複を落とす
#   - vmstorage-1〜3（8400 で vminsert から、8401 で vmselect から受ける。8482 は自分の HTTP）: 台ごとに ECS のサービスと EFS のアクセスポイント（/vmstorage-N）。
#     台 N はサブネットの N 番目（a / b / c。AZ ごとに 1 台）。VictoriaMetrics の公式は EFS（NFS）に置けると書いている
# 複製数 2 は vmstorage が 2N−1 = 3 台なら 1 台止まっても書けて、読んだ結果も欠けない。
# 名前は Cloud Map の <名前>.<接頭辞>.internal（名前空間は ecs.tf）。認証は無い（マネージド版の SigV4 の代わり。Spark は PROMETHEUS_AUTH=none）。
# 届くのは SG で絞った相手だけ（IaC/terraform/aws-managed/base/core の oss.tf の通信の表: Spark → 8480、Grafana・AgentCore・Lambda → 8481、
# 3 つの部品どうし 8400〜8401、VictoriaMetrics → EFS 2049）。
# vminsert は起動したとき vmstorage につなぎに行き、3 台につながる前に受けた行は 1 台にしか入らない（エンジニア3 が 005 の手元の compose で確かめた）。
# そこで vminsert のタスクに待ちのコンテナ（wait-vmstorage）を置き、3 台が 8400 で受けるまで vminsert を起こさない（ECS のコンテナの dependsOn）。
# vmstorage の台が止まったままなら、待つのは vmstorage_wait_seconds 秒まで（それを過ぎたら起こす。止まっている台を外して残りの 2 台に書く）。
# イメージは victoriametrics/{vminsert,vmselect,vmstorage} を ECR の <接頭辞>-<名前> に写したもの（閉域で Docker Hub に届かない。OSS 版の ops/up.sh が写す）

variable "victoriametrics_image_tag" {
  description = "Tag of the vminsert, vmselect and vmstorage images in the <prefix>-vminsert / -vmselect / -vmstorage repositories (victoriametrics/* copied to ECR by the OSS ops/up.sh). Same version as oss/ops/oss-images.sh."
  type        = string
  default     = "v1.153.0-cluster"
}

variable "victoriametrics_task_cpu" {
  description = "Fargate CPU units of each VictoriaMetrics task - vminsert, vmselect and each vmstorage (ARM64)."
  type        = number
  default     = 512

  validation {
    condition     = contains([256, 512, 1024], var.victoriametrics_task_cpu)
    error_message = "victoriametrics_task_cpu must be 256, 512 or 1024."
  }
}

variable "victoriametrics_task_memory" {
  description = "Fargate memory (MiB) of each VictoriaMetrics task. Must be a valid pair with victoriametrics_task_cpu (512 takes 1024-4096)."
  type        = number
  default     = 1024

  validation {
    condition     = contains([512, 1024, 2048, 4096], var.victoriametrics_task_memory)
    error_message = "victoriametrics_task_memory must be 512, 1024, 2048 or 4096."
  }
}

variable "vmstorage_wait_seconds" {
  description = "How long the vminsert task waits for the three vmstorage nodes to accept on 8400 before starting vminsert anyway (a node still down after that is left out until it comes back)."
  type        = number
  default     = 300

  validation {
    condition     = var.vmstorage_wait_seconds >= 0 && var.vmstorage_wait_seconds <= 3600
    error_message = "vmstorage_wait_seconds は 0〜3600。"
  }
}

locals {
  vm_images    = { for c in ["vminsert", "vmselect", "vmstorage"] : c => "${try(data.terraform_remote_state.ecr.outputs.oss_repository_urls[c], "")}:${var.victoriametrics_image_tag}" }
  vm_log_group = "/ecs/${local.name_prefix}-victoriametrics"
  # 土台（IaC/terraform/aws-managed/base/core の oss.tf）の SG と EFS。マネージド版の土台や古い state では無いので try にして、precondition で止める
  vm_sg_id               = try(data.terraform_remote_state.main.outputs.security_group_ids["victoriametrics"], "")
  vm_efs_file_system_id  = try(data.terraform_remote_state.main.outputs.efs_file_system_id, "")
  vm_efs_file_system_arn = "arn:${local.partition}:elasticfilesystem:${var.region}:${local.account_id}:file-system/${local.vm_efs_file_system_id}"

  # vmstorage の台の番号 → サブネット（1 台目は a）
  vmstorage_nodes = local.sink_prometheus ? { for i in range(3) : tostring(i + 1) => try(local.subnet_ids[i], "") } : {}
  vmstorage_hosts = [for i in range(3) : "vmstorage-${i + 1}.${local.service_namespace}"]
  # 複製数。vmstorage が 2N−1 台なら 1 台止まっても書けて、読んだ結果も欠けない（設計 005）
  vm_replication_factor = 2

  # Cloud Map の名前（vminsert・vmselect と、vmstorage の台ごと）
  vm_service_names = local.sink_prometheus ? toset(concat(["vminsert", "vmselect"], [for n in range(3) : "vmstorage-${n + 1}"])) : toset([])

  # 待ちのコンテナ（vminsert のイメージの busybox の sh と nc。手元の Docker で、3 台がそろうと 0 で終わるのを確かめた）。
  # 期限を過ぎても 0 で終わる（vminsert の dependsOn は SUCCESS = 0 で終わること）
  vmstorage_wait_command = join(" ", [
    "end=$(( $(date +%s) + ${var.vmstorage_wait_seconds} ));",
    "for h in ${join(" ", local.vmstorage_hosts)}; do",
    "until nc -z -w 2 $h 8400; do",
    "if [ $(date +%s) -ge $end ]; then echo \"gave up waiting for $h:8400\"; break; fi;",
    "echo \"waiting for $h:8400\"; sleep 2;",
    "done;",
    "done;",
    "echo \"vmstorage wait done\"; exit 0",
  ])
}

resource "aws_cloudwatch_log_group" "victoriametrics" {
  count = local.sink_prometheus ? 1 : 0

  name              = local.vm_log_group
  retention_in_days = var.log_retention_days
}

resource "aws_service_discovery_service" "victoriametrics" {
  for_each = local.vm_service_names

  name = each.key
  # タスクの登録が残っていても destroy できるようにする
  force_destroy = true

  # タスクを作り直すと IP が変わる。TTL を短くして、ほかの部品とクライアントが早く新しい IP を引くようにする
  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.analytics[0].id
    routing_policy = "MULTIVALUE"

    dns_records {
      ttl  = 10
      type = "A"
    }
  }
}

# ---------------------------------------------------------------- vmstorage（3 台）
# 台ごとの置き場。posix_user でどのタスクの読み書きも uid / gid 1000 にそろえ（イメージは root で動くが、NFS の上ではこのユーザーになる）、
# /vmstorage-N をそのユーザーで作る
resource "aws_efs_access_point" "vmstorage" {
  for_each = local.vmstorage_nodes

  file_system_id = local.vm_efs_file_system_id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/vmstorage-${each.key}"

    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0750"
    }
  }

  tags = { Name = "${local.name_prefix}-vmstorage-${each.key}" }

  lifecycle {
    precondition {
      condition     = local.vm_efs_file_system_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に efs_file_system_id が無いか空（マネージド版の土台か、oss.tf より前の土台）。IaC/terraform/oss/base/core を先に apply する。"
    }
  }
}

# ヘルスチェックは付けない（LB が無いので入れ替えの判断にしか使われない）
resource "aws_ecs_task_definition" "vmstorage" {
  for_each = local.vmstorage_nodes

  family                   = "${local.name_prefix}-vmstorage-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.victoriametrics_task_cpu
  memory                   = var.victoriametrics_task_memory
  execution_role_arn       = aws_iam_role.victoriametrics_execution[0].arn
  task_role_arn            = aws_iam_role.vmstorage_task[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # EFS のポリシー（IaC/terraform/aws-managed/base/core の oss.tf）が TLS と IAM の無いマウントを拒むので、両方を有効にする
  volume {
    name = "storage"

    efs_volume_configuration {
      file_system_id     = local.vm_efs_file_system_id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.vmstorage[each.key].id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "vmstorage"
      image     = local.vm_images["vmstorage"]
      essential = true
      portMappings = [
        { containerPort = 8400, protocol = "tcp" }, # vminsert から
        { containerPort = 8401, protocol = "tcp" }, # vmselect から
        { containerPort = 8482, protocol = "tcp" }, # 自分の HTTP（/metrics など）
      ]
      # 005 の手元の compose で確かめた値
      command     = ["-storageDataPath=/storage", "-retentionPeriod=30d"]
      mountPoints = [{ sourceVolume = "storage", containerPath = "/storage", readOnly = false }]
      # 止めるときにメモリの中の行を EFS に書き出す時間（Fargate の上限）
      stopTimeout = 120
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.victoriametrics[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "vmstorage-${each.key}"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["vmstorage"], "") != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state に vmstorage のリポジトリが無い（project = nwc-oss でない ECR か、古い ECR）。IaC/terraform/oss/base/ecr を先に apply する。"
    }
  }
}

resource "aws_ecs_service" "vmstorage" {
  for_each = local.vmstorage_nodes

  name            = "${local.name_prefix}-vmstorage-${each.key}"
  cluster         = aws_ecs_cluster.analytics[0].id
  task_definition = aws_ecs_task_definition.vmstorage[each.key].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # aws ecs execute-command でタスクの中に入れる
  enable_execute_command = true

  # 同じ置き場を 2 つのタスクが同時に使わない（vmstorage は置き場をロックする）。入れ替えでは古いタスクを止めてから新しいタスクを起こす
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = [each.value]
    security_groups  = [local.vm_sg_id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.victoriametrics["vmstorage-${each.key}"].arn
  }

  lifecycle {
    precondition {
      condition     = local.vm_sg_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に victoriametrics の SG が無い（マネージド版の土台か、oss.tf より前の土台）。IaC/terraform/oss/base/core を先に apply する。"
    }
    precondition {
      condition     = each.value != ""
      error_message = "IaC/terraform/aws-managed/base/core の state のサブネットが 3 つ無い（vmstorage は AZ ごとに 1 台で 3 つ要る）。"
    }
  }

  depends_on = [
    aws_iam_role_policy.vmstorage_task,
    aws_iam_role_policy_attachment.victoriametrics_execution,
  ]
}

# ---------------------------------------------------------------- vminsert と vmselect（1 台ずつ。状態を持たない）
resource "aws_ecs_task_definition" "vminsert" {
  count = local.sink_prometheus ? 1 : 0

  family                   = "${local.name_prefix}-vminsert"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.victoriametrics_task_cpu
  memory                   = var.victoriametrics_task_memory
  execution_role_arn       = aws_iam_role.victoriametrics_execution[0].arn
  task_role_arn            = aws_iam_role.victoriametrics_task[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      # vmstorage の 3 台が 8400 で受けるまで待って 0 で終わる（上の説明）。SUCCESS を待たれるコンテナは essential にできない
      name       = "wait-vmstorage"
      image      = local.vm_images["vminsert"]
      essential  = false
      entryPoint = ["/bin/sh", "-c"]
      command    = [local.vmstorage_wait_command]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.victoriametrics[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "vminsert-wait"
        }
      }
    },
    {
      name         = "vminsert"
      image        = local.vm_images["vminsert"]
      essential    = true
      dependsOn    = [{ containerName = "wait-vmstorage", condition = "SUCCESS" }]
      portMappings = [{ containerPort = 8480, protocol = "tcp" }]
      command = [
        "-storageNode=${join(",", [for h in local.vmstorage_hosts : "${h}:8400"])}",
        "-replicationFactor=${local.vm_replication_factor}",
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.victoriametrics[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "vminsert"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["vminsert"], "") != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state に vminsert のリポジトリが無い（project = nwc-oss でない ECR か、古い ECR）。IaC/terraform/oss/base/ecr を先に apply する。"
    }
  }
}

resource "aws_ecs_task_definition" "vmselect" {
  count = local.sink_prometheus ? 1 : 0

  family                   = "${local.name_prefix}-vmselect"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.victoriametrics_task_cpu
  memory                   = var.victoriametrics_task_memory
  execution_role_arn       = aws_iam_role.victoriametrics_execution[0].arn
  task_role_arn            = aws_iam_role.victoriametrics_task[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name         = "vmselect"
      image        = local.vm_images["vmselect"]
      essential    = true
      portMappings = [{ containerPort = 8481, protocol = "tcp" }]
      command = [
        "-storageNode=${join(",", [for h in local.vmstorage_hosts : "${h}:8401"])}",
        "-replicationFactor=${local.vm_replication_factor}",
        # 複製の 2 つ目を読んだ結果から落とす（公式がクラスターで複製するときに付けるよう書いている）
        "-dedup.minScrapeInterval=1ms",
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.victoriametrics[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "vmselect"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.oss_repository_urls["vmselect"], "") != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state に vmselect のリポジトリが無い（project = nwc-oss でない ECR か、古い ECR）。IaC/terraform/oss/base/ecr を先に apply する。"
    }
  }
}

resource "aws_ecs_service" "vminsert" {
  count = local.sink_prometheus ? 1 : 0

  name            = "${local.name_prefix}-vminsert"
  cluster         = aws_ecs_cluster.analytics[0].id
  task_definition = aws_ecs_task_definition.vminsert[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  enable_execute_command = true

  # 状態を持たないので、入れ替えでは新しいタスクを先に起こす
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [local.vm_sg_id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.victoriametrics["vminsert"].arn
  }

  lifecycle {
    precondition {
      condition     = local.vm_sg_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に victoriametrics の SG が無い（マネージド版の土台か、oss.tf より前の土台）。IaC/terraform/oss/base/core を先に apply する。"
    }
  }

  # 作る順も vmstorage の後にする（起動の順は上の待ちのコンテナが守る。ECS のサービスはタスクが起きる前に作り終わる）
  depends_on = [
    aws_ecs_service.vmstorage,
    aws_iam_role_policy.victoriametrics_task,
    aws_iam_role_policy_attachment.victoriametrics_execution,
  ]
}

resource "aws_ecs_service" "vmselect" {
  count = local.sink_prometheus ? 1 : 0

  name            = "${local.name_prefix}-vmselect"
  cluster         = aws_ecs_cluster.analytics[0].id
  task_definition = aws_ecs_task_definition.vmselect[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  enable_execute_command = true

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [local.vm_sg_id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.victoriametrics["vmselect"].arn
  }

  lifecycle {
    precondition {
      condition     = local.vm_sg_id != ""
      error_message = "IaC/terraform/aws-managed/base/core の state に victoriametrics の SG が無い（マネージド版の土台か、oss.tf より前の土台）。IaC/terraform/oss/base/core を先に apply する。"
    }
  }

  depends_on = [
    aws_iam_role_policy.victoriametrics_task,
    aws_iam_role_policy_attachment.victoriametrics_execution,
  ]
}

# ---------------------------------------------------------------- IAM（実行ロールは 5 つで同じ。タスクロールは EFS を使う vmstorage とそれ以外で分ける）
resource "aws_iam_role" "victoriametrics_execution" {
  count = local.sink_prometheus ? 1 : 0

  name               = "${local.name_prefix}-victoriametrics-exec"
  description        = "ECS task execution role of the VictoriaMetrics tasks (ECR pull, CloudWatch Logs)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "victoriametrics_execution" {
  count = local.sink_prometheus ? 1 : 0

  role       = aws_iam_role.victoriametrics_execution[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "vmstorage_task" {
  count = local.sink_prometheus ? 1 : 0

  name               = "${local.name_prefix}-vmstorage-task"
  description        = "vmstorage task role - mount the vmstorage access points of the EFS (IAM authorization), ECS Exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "vmstorage_task" {
  count = local.sink_prometheus ? 1 : 0

  name = "${local.name_prefix}-vmstorage-task"
  role = aws_iam_role.vmstorage_task[0].name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # EFS の IAM 認可（タスク定義の authorization_config の iam）。vmstorage の 3 つのアクセスポイントからだけ
        Sid      = "Efs"
        Effect   = "Allow"
        Action   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource = local.vm_efs_file_system_arn
        Condition = {
          StringEquals = { "elasticfilesystem:AccessPointArn" = [for ap in aws_efs_access_point.vmstorage : ap.arn] }
        }
      },
      {
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
      },
    ]
  })
}

resource "aws_iam_role" "victoriametrics_task" {
  count = local.sink_prometheus ? 1 : 0

  name               = "${local.name_prefix}-victoriametrics-task"
  description        = "vminsert and vmselect task role - ECS Exec only (no AWS API is called)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "victoriametrics_task" {
  count = local.sink_prometheus ? 1 : 0

  name = "${local.name_prefix}-victoriametrics-task"
  role = aws_iam_role.victoriametrics_task[0].name

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

# IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない呼び出しを拒む）。実行ロールとタスクロールの全部に付ける
resource "aws_iam_role_policy_attachment" "victoriametrics_perimeter" {
  for_each = local.sink_prometheus && local.perimeter_policy_arn != "" ? {
    execution = aws_iam_role.victoriametrics_execution[0].name
    vmstorage = aws_iam_role.vmstorage_task[0].name
    task      = aws_iam_role.victoriametrics_task[0].name
  } : {}

  role       = each.value
  policy_arn = local.perimeter_policy_arn
}

# ---------------------------------------------------------------- Prometheus の差し替え口（マネージド版は locals.tf・grafana.tf・outputs.tf が AMP の値で持つ）
locals {
  # Spark のジョブの remote write の書き先（app/spark/snmp_sinks.py が PROMETHEUS_AUTH=none でそのまま POST する）
  prometheus_remote_write_url = local.sink_prometheus ? "http://vminsert.${local.service_namespace}:8480/insert/0/prometheus/api/v1/write" : ""
  # PromQL の入口（Grafana の PROMETHEUS_URL。app/grafana/provisioning/datasources-oss/prometheus.yaml の http://<vmselect>:8481/select/0/prometheus）
  prometheus_select_url = local.sink_prometheus ? "http://vmselect.${local.service_namespace}:8481/select/0/prometheus" : ""
  # エージェントの道具（app/agentcore/evidence.py の PROMETHEUS_QUERY_URL）。IaC/terraform/aws-managed/workflow が output prometheus_query_url で読む
  prometheus_query_url = local.sink_prometheus ? "${local.prometheus_select_url}/api/v1/query" : ""
}

# ---------------------------------------------------------------- outputs
# IaC/terraform/aws-managed/workflow が読む名前はマネージド版の outputs.tf と同じ。prometheus_workspace_arn / _id は出さない
# （IaC/terraform/aws-managed/workflow は空のとき AMP の IAM を作らない）
output "prometheus_remote_write_url" {
  description = "Remote write URL the job posts to - vminsert, no authentication (empty unless sinks has prometheus)"
  value       = local.prometheus_remote_write_url
}

output "prometheus_query_url" {
  description = "PromQL instant query URL of vmselect, no authentication (empty unless sinks has prometheus). Same output name as the managed build, which IaC/terraform/aws-managed/workflow reads"
  value       = local.prometheus_query_url
}

output "prometheus_select_url" {
  description = "Prometheus API root of vmselect for the Grafana datasource (PROMETHEUS_URL; empty unless sinks has prometheus)"
  value       = local.prometheus_select_url
}

output "victoriametrics_service_names" {
  description = "ECS services of VictoriaMetrics, keyed by vminsert, vmselect and vmstorage-N. Replace the vmstorage nodes one at a time - two of the three must be up to write"
  value = merge(
    { for s in aws_ecs_service.vminsert : "vminsert" => s.name },
    { for s in aws_ecs_service.vmselect : "vmselect" => s.name },
    { for n, s in aws_ecs_service.vmstorage : "vmstorage-${n}" => s.name },
  )
}

output "victoriametrics_log_group_name" {
  description = "CloudWatch Logs group of the VictoriaMetrics tasks (stream prefix vminsert, vminsert-wait, vmselect and vmstorage-N)"
  value       = local.sink_prometheus ? aws_cloudwatch_log_group.victoriametrics[0].name : ""
}
