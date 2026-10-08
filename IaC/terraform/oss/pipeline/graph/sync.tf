# ---------------------------------------------------------------- dynamic status: SNS (<prefix>-alerts) -> Lambda -> Neo4j
# マネージド版の sync.tf の OSS 版。同じ Lambda（graph/status_handler.py + agent/graph.py + agent/toolkit.py + workflow/rules.py）を、
# 環境変数 GRAPH_BACKEND=neo4j で Neo4j（neo4j.tf）に向ける。違うのは次の 4 つだけで、ほかはマネージド版と同じ形にそろえる
# （マネージド版の sync.tf を直したら、ここも同じように直す）。
#   - Neptune の IAM の行（OpenCypher）が無く、代わりに SSM の /<接頭辞>/neo4j-password を読む行（graph.py が SecureString を復号して読む）
#   - 環境変数は NEPTUNE_GRAPH_ID でなく GRAPH_BACKEND・NEO4J_URI・PARAM_PREFIX（パスワードは環境変数に書かない。state に残るので）
#   - Neo4j の Python ドライバをレイヤーで足す（Lambda の Python に入っていない。中身は graph/requirements-oss.txt）
#   - リポジトリのファイルは local.repo_root から読む（このルートは terraform/ より 1 つ深い）
# Lambda は Neo4j の 7687 へ SG の通信の表（terraform/base/core の oss.tf の lambda → neo4j）で届き、SSM と Firehose はエンドポイントを通る。

locals {
  repo_root    = "${path.module}/../../../.."
  alert_stream = "${local.name_prefix}-alert-events" # oss/terraform/pipeline/analytics の history.tf（マネージド版へのリンク）の Firehose と同じ名前
  # Neo4j のドライバのレイヤーの中身。OSS 版の ops/up.sh が apply の前に作る（arm64 の Lambda 用。ドライバは純 Python なので、どの PC でも同じものができる）:
  #   pip install --target oss/terraform/pipeline/graph/.build/neo4j-layer/python --only-binary=:all: --platform manylinux2014_aarch64 \
  #     --python-version 3.13 -r graph/requirements-oss.txt
  neo4j_layer_dir = "${path.module}/.build/neo4j-layer"
}

data "archive_file" "status" {
  type        = "zip"
  output_path = "${path.module}/.build/status.zip"

  source {
    content  = file("${local.repo_root}/graph/status_handler.py")
    filename = "index.py"
  }

  source {
    content  = file("${local.repo_root}/agent/graph.py")
    filename = "graph.py"
  }

  # graph.py が import する共通部品（リージョン・SSM パラメータ・boto3 クライアント）。入れ忘れると
  # apply も plan も通るのに、実行時に ModuleNotFoundError で status が一度も書かれない
  source {
    content  = file("${local.repo_root}/agent/toolkit.py")
    filename = "toolkit.py"
  }

  # アラートの JSON の読み方（alerts_from_message）は worker と同じものを使う。標準ライブラリしか読まない
  source {
    content  = file("${local.repo_root}/workflow/rules.py")
    filename = "rules.py"
  }
}

data "archive_file" "neo4j_layer" {
  type        = "zip"
  source_dir  = local.neo4j_layer_dir
  output_path = "${path.module}/.build/neo4j-layer.zip"

  lifecycle {
    precondition {
      condition     = fileexists("${local.neo4j_layer_dir}/python/neo4j/__init__.py")
      error_message = "Neo4j のドライバのレイヤー（.build/neo4j-layer/python/neo4j）が無い。graph/requirements-oss.txt を pip install --target で入れてから apply する（sync.tf の locals の注記）。"
    }
  }
}

resource "aws_lambda_layer_version" "neo4j" {
  layer_name               = "${local.name_prefix}-neo4j-driver"
  description              = "Neo4j Python driver for the status Lambda (graph/requirements-oss.txt)"
  filename                 = data.archive_file.neo4j_layer.output_path
  source_code_hash         = data.archive_file.neo4j_layer.output_base64sha256
  compatible_runtimes      = ["python3.13"]
  compatible_architectures = ["arm64"]
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "status" {
  name               = "${local.name_prefix}-graph-status"
  description        = "Status Lambda of oss/terraform/pipeline/graph - writes the dynamic status of devices and links into Neo4j"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "status" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/${local.name_prefix}-graph-status:*"]
  }

  # VPC の中で動くので ENI を作る（AWSLambdaVPCAccessExecutionRole と同じ中身。マネージドポリシーは付けない）
  statement {
    sid       = "VpcEni"
    actions   = ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"]
    resources = ["*"]
  }

  # Neo4j のパスワード（graph.py の NEO4J_PASSWORD。環境変数に無いので SSM の <PARAM_PREFIX>/neo4j-password を復号して読む。
  # AWS 管理の aws/ssm キーなので kms:Decrypt は要らない）。URI は環境変数で渡すので読まない
  statement {
    sid       = "Neo4jPassword"
    actions   = ["ssm:GetParameter"]
    resources = [local.neo4j_password_arn]
  }

  # アラートの通知の履歴（alert_history = true のときだけ）。送り先は oss/terraform/pipeline/analytics の Firehose 1 本だけ
  dynamic "statement" {
    for_each = var.alert_history ? [1] : []
    content {
      sid       = "AlertHistory"
      actions   = ["firehose:PutRecordBatch"]
      resources = ["arn:${local.partition}:firehose:${var.region}:${local.account_id}:deliverystream/${local.alert_stream}"]
    }
  }
}

resource "aws_iam_role_policy" "status" {
  name   = "${local.name_prefix}-graph-status"
  role   = aws_iam_role.status.name
  policy = data.aws_iam_policy_document.status.json
}

# terraform/base/core の perimeter.tf の Deny。SSM（パスワード）は ssm、firehose（履歴）は kinesis-firehose のエンドポイントを通る。
# ログと ENI は Lambda のサービスがこのロールで出すので、Deny の対象に入っていない
resource "aws_iam_role_policy_attachment" "status_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.status.name
  policy_arn = local.perimeter_policy_arn
}

resource "aws_cloudwatch_log_group" "status" {
  name              = "/aws/lambda/${local.name_prefix}-graph-status"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "status" {
  function_name    = "${local.name_prefix}-graph-status"
  role             = aws_iam_role.status.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "index.handler"
  filename         = data.archive_file.status.output_path
  source_code_hash = data.archive_file.status.output_base64sha256
  layers           = [aws_lambda_layer_version.neo4j.arn]
  timeout          = 60  # マネージド版と同じ（行は Neo4j より先に Firehose へ送る。Neo4j の途中で切れたら非同期の再試行に任せる）
  memory_size      = 256 # マネージド版と同じ。128 MB では AWS で Max Memory Used が 111 MB（Neo4j のドライバのレイヤー込み）で、余裕が無かった

  # VPC の中に置く（Neo4j は VPC の中にしか無い）。SG は terraform/base/core の lambda（Neo4j の 7687 とエンドポイントの 443 へ出られる）
  vpc_config {
    subnet_ids         = slice(local.subnet_ids, 0, var.lambda_az_num)
    security_group_ids = [local.lambda_sg_id]
  }

  environment {
    variables = {
      GRAPH_BACKEND = "neo4j"
      NEO4J_URI     = local.neo4j_uri
      PARAM_PREFIX  = "/${local.name_prefix}"                     # graph.py が SSM の <PARAM_PREFIX>/neo4j-password を読む
      ALERT_STREAM  = var.alert_history ? local.alert_stream : "" # 空なら status_handler.py は履歴を送らない
    }
  }

  depends_on = [aws_cloudwatch_log_group.status, aws_iam_role_policy.status]
}

# SNS は Lambda を非同期で呼ぶ。Lambda の側の失敗は Lambda が 2 回まで再試行し、SNS の側の配信の失敗は SNS が再試行する
resource "aws_lambda_permission" "status" {
  statement_id  = "AllowSns"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.status.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = local.alerts_topic_arn
}

resource "aws_sns_topic_subscription" "status" {
  topic_arn = local.alerts_topic_arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.status.arn

  depends_on = [aws_lambda_permission.status]

  lifecycle {
    precondition {
      condition     = local.alerts_topic_arn != ""
      error_message = "terraform/base/core の state から alerts_topic_arn が読めない（2026-10-02 より前の土台）。oss/terraform/base/core を apply し直す（OSS 版の ops/up.sh）。"
    }
  }
}
