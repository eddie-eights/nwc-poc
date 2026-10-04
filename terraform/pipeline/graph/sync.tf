# ---------------------------------------------------------------- dynamic status: SNS (<prefix>-alerts) -> Lambda -> Neptune
# The Grafana alert rules and the Splunk saved searches of terraform/pipeline/analytics publish firing / resolved alerts to the SNS topic of
# terraform/base/core (alerts.tf) when a link goes down / comes back. The subscription below sends them to a small Lambda in the VPC
# (graph/status_handler.py + agent/graph.py + workflow/rules.py) that sets the property "status" (DOWN / UP, ALARM for other traps) on the
# link edge or the device vertex. The web draws DOWN in red and the chat tools return it. Neptune holds the topology and this status only -
# the alerts themselves are not stored here (2026-10-02; until then the Spark job put AnomalyOpened / AnomalyResolved on EventBridge).
# With alert_history = true (ops/up.sh sets it when analytics is deployed - in this run or left in its state) the same Lambda also sends every alert, one row each, to the
# Firehose stream <prefix>-alert-events of terraform/pipeline/analytics (history.tf), which appends it to the S3 Tables table alert_events
# (2026-10-04). The Lambda reaches Firehose through the kinesis-firehose interface endpoint of terraform/base/core.
# The static topology itself comes from lab/ (ops/up.sh 7-3b and ops/sync-graph.sh seed it through the web EC2) - not from here.
# Cost: the subscription is free, the Lambda is a few invocations per alert (free tier), nothing else
# (the Lambda reaches Neptune Analytics through the neptune-graph-data interface endpoint of terraform/base/core; the Lambda service writes its logs without going through the VPC).
# The Firehose stream and its endpoint are counted in terraform/pipeline/analytics and terraform/base/core.

locals {
  alert_stream = "${local.name_prefix}-alert-events" # terraform/pipeline/analytics/history.tf の aws_kinesis_firehose_delivery_stream.alert_events と同じ名前
}

data "archive_file" "status" {
  type        = "zip"
  output_path = "${path.module}/.build/status.zip"

  source {
    content  = file("${path.module}/../../../graph/status_handler.py")
    filename = "index.py"
  }

  source {
    content  = file("${path.module}/../../../agent/graph.py")
    filename = "graph.py"
  }

  # graph.py が import する共通部品（リージョン・SSM パラメータ・boto3 クライアント）。入れ忘れると
  # apply も plan も通るのに、実行時に ModuleNotFoundError で status が一度も書かれない
  source {
    content  = file("${path.module}/../../../agent/toolkit.py")
    filename = "toolkit.py"
  }

  # アラートの JSON の読み方（alerts_from_message）は worker と同じものを使う。標準ライブラリしか読まない
  source {
    content  = file("${path.module}/../../../workflow/rules.py")
    filename = "rules.py"
  }
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
  description        = "Status Lambda of terraform/pipeline/graph - writes the dynamic status of devices and links into Neptune"
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

  # status の書き換え（SET）と「未登録」の頂点の片付け（DELETE / REMOVE）。Neptune Database のときは property の上書きにも
  # DeleteDataViaQuery が要った（2026-09-18 実機）。Neptune Analytics で SET だけなら Write で足りるかは未確認なので、3 つとも付ける
  statement {
    sid       = "OpenCypher"
    actions   = ["neptune-graph:ReadDataViaQuery", "neptune-graph:WriteDataViaQuery", "neptune-graph:DeleteDataViaQuery", "neptune-graph:GetQueryStatus"]
    resources = [aws_neptunegraph_graph.graph.arn]
  }

  # アラートの通知の履歴（alert_history = true のときだけ）。送り先は terraform/pipeline/analytics の Firehose 1 本だけ
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

# terraform/base/core の perimeter.tf の Deny。firehose（履歴）は kinesis-firehose のエンドポイントを通る。
# ログと ENI は Lambda のサービスがこのロールで出し、neptune-graph はグラフの public_connectivity = false で閉じているので、Deny の対象に入っていない
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
  timeout          = 60 # 行は Neptune より先に Firehose へ送る（長くて 22 秒ほど。status_handler.py の FIREHOSE_CONFIG）。Neptune の途中で切れたら非同期の再試行に任せる
  memory_size      = 128

  # VPC の中に置く（グラフは公開していないので、土台の neptune-graph-data のエンドポイントからしか届かない）。SG は terraform/base/core の lambda
  # （エンドポイントの 443 へ出られる。SSM は引かない。グラフの ID は環境変数で渡す）
  vpc_config {
    subnet_ids         = local.subnet_ids
    security_group_ids = [local.lambda_sg_id]
  }

  environment {
    variables = {
      NEPTUNE_GRAPH_ID = aws_neptunegraph_graph.graph.id             # graph.py はこれがあれば SSM を引かない
      ALERT_STREAM     = var.alert_history ? local.alert_stream : "" # 空なら status_handler.py は履歴を送らない
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
      error_message = "terraform/base/core の state から alerts_topic_arn が読めない（2026-10-02 より前の土台）。terraform/base/core を apply し直す（ops/up.sh）。"
    }
  }
}
