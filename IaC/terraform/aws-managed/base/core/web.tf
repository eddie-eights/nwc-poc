# ---------------------------------------------------------------- chat web EC2
data "aws_ssm_parameter" "al2023" {
  name = var.ami_ssm_parameter
}

resource "aws_iam_role" "web" {
  name        = "${local.name_prefix}-web"
  description = "${local.name_prefix} chat web EC2 - SSM managed node, reads web assets from S3 and the runtime ARN from SSM, pulls the Kafbat UI image from ECR"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { Name = "${local.name_prefix}-web" }
}

resource "aws_iam_role_policy_attachment" "web_ssm" {
  role       = aws_iam_role.web.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# 画面のコード・静的データ・wheel は同じバケットの web/ に置く（ops/up.sh の手順 4）。docs/ は読ませない。
# Runtime の ARN は IaC/terraform/aws-managed/agent が /<接頭辞>/runtime-arn に書き、app/dashboard/app.py が 60 秒ごとに読む（agent を後から入れ替えても再起動が要らない）。
# InvokeAgentRuntime の許可は IaC/terraform/aws-managed/agent がこのロールに足す。
# Kafbat UI（cycle 010。templates/web_user_data.sh.tftpl の <接頭辞>-kafka-ui.service）のイメージは ECR の <接頭辞>-kafka-ui から引く
# （ops/up.sh の手順 2 が ghcr.io から写す）。接続先とパスワードも /<接頭辞>/kafka-ui/ の SSM のパラメータで、下の ssm:GetParameter で読める。
# MSK の権限は IaC/terraform/aws-managed/pipeline/stream の kafka_ui.tf がこのロールに足す
resource "aws_iam_role_policy" "web_assets" {
  name = "web-assets"
  role = aws_iam_role.web.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.kb.arn}/web/*"
      },
      {
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.kb.arn
        Condition = {
          StringLike = { "s3:prefix" = "web/*" }
        }
      },
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/${local.name_prefix}/*"
      },
      {
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = "arn:${local.partition}:ecr:${var.region}:${local.account_id}:repository/${local.name_prefix}-kafka-ui"
      },
    ]
  })
}

resource "aws_iam_instance_profile" "web" {
  name = "${local.name_prefix}-web"
  role = aws_iam_role.web.name
}

# user_data を変えると Terraform はインスタンスを作り直す（user_data_replace_on_change）。
# 先頭の cloud-config で起動のたびにスクリプトを流すので、S3 の web/ を置き直して再起動すれば画面も更新される
# 1 台だけ（サブネット a）。AZ の数のキー（*_AZ_NUM）を作らない理由: SSM のポートフォワード（ops/up.sh の手順 10）は 1 台のインスタンス ID を
# 名指しでつなぐので、2 台にしても a の AZ が止まったときに切り替える先（ロードバランサーや名前）が無い。
# コードから確かめた理由で、AWS では未確認（2026-10-04）
resource "aws_instance" "web" {
  ami                         = data.aws_ssm_parameter.al2023.insecure_value
  instance_type               = var.instance_type
  iam_instance_profile        = aws_iam_instance_profile.web.name
  subnet_id                   = aws_subnet.a.id
  vpc_security_group_ids      = [aws_security_group.workload["web"].id]
  associate_public_ip_address = false

  # graph_backend: OSS 版（IaC/terraform/oss、cycle 005）だけ neo4j。Web の環境変数に GRAPH_BACKEND=neo4j を足し、app/agentcore/graph.py が
  # Neptune の代わりに Neo4j を読む（URI とパスワードは SSM の neo4j-uri / neo4j-password。IaC/terraform/oss/pipeline/graph）。
  # 依存は app/dashboard/requirements-oss.txt（マネージド版の依存 + Neo4j のドライバ）で入れる。
  # マネージド版は空で、テンプレートはどちらも出さない（user_data は前と 1 文字も変わらず、インスタンスも作り直さない）。
  # テンプレートの中に説明を書かないのは、コメントも user_data に入って、マネージド版のインスタンスが作り直されるから
  user_data = templatefile("${path.module}/templates/web_user_data.sh.tftpl", {
    name_prefix   = local.name_prefix
    region        = var.region
    bucket        = aws_s3_bucket.kb.bucket
    graph_backend = local.oss ? "neo4j" : ""
  })
  user_data_replace_on_change = true

  # hop limit 2: Kafbat UI のコンテナ（Docker の bridge。cycle 010）が MSK の IAM 認証にこのインスタンスロールを IMDSv2 から取る。
  # bridge を越えると IP の TTL が 1 つ減るので、1 のままだと PUT /latest/api/token の応答がコンテナに届かない
  # （AWS の文書「Use the Instance Metadata Service to access instance metadata」のコンテナの記述）。
  # lab の EC2（pipeline/lab/instance.tf）が 1 のままなのは、containerlab のコンテナが IMDS を使わないから
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  # 16 GB: Docker と Kafbat UI のイメージ（展開して約 640 MB）の分。8 GB でも入るが余裕が無い
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 16
    encrypted             = true
    delete_on_termination = true

    tags = {
      Name    = "${local.name_prefix}-web"
      Project = local.name_prefix
      owner   = var.owner
    }
  }

  tags = { Name = "${local.name_prefix}-web" }

  # 起動スクリプトが S3 gateway（web/ と dnf）と ssm / ssmmessages のエンドポイント（SSM Agent の登録）を使うので、エンドポイントと SG のルールを先に作らせる。
  # network_perimeter の Deny もエンドポイントより先に効くと、エンドポイントができるまでの SSM の呼び出しが届かない（NAT が無いのでタイムアウトになる）
  depends_on = [
    aws_iam_role_policy_attachment.web_ssm,
    aws_iam_role_policy.web_assets,
    aws_vpc_endpoint.s3,
    aws_vpc_endpoint.interface,
    aws_vpc_security_group_egress_rule.flow,
    aws_vpc_security_group_ingress_rule.flow,
  ]
}
