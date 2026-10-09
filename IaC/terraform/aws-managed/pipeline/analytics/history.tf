# ---------------------------------------------------------------- アラートの通知の履歴: Firehose → S3 Tables（alert_events）→ Athena（2026-10-04）
# IaC/terraform/aws-managed/pipeline/graph の status の Lambda（app/graph/status_handler.py）が、SNS で受けたアラートの通知を 1 件 1 行で
# このストリームに put_record_batch する（graph の alert_history = true。ops/up.sh が analytics がある回にだけ渡す。今回作るか、state に残っている）。
# Firehose は 60 秒か 1 MiB ごとに tables.tf の alert_events に追記する（Iceberg。Glue の s3tablescatalog を通す）。
# s3tablescatalog はアカウントとリージョンに 1 つの Glue のカタログで、無ければ ops/up.sh が作る（down.sh では消さない。docs/deploy.md）。
# 書けなかった行は土台のバケットの firehose-errors/alert_events/ に落ちる。読むのはエージェントの query_history（app/agentcore/evidence.py。
# 下の Athena のワークグループ。tools の Lambda の権限は IaC/terraform/aws-managed/workflow の gateway.tf）。
# Firehose はロールを引き受けて自分の側（VPC の外）から書くので、このロールは IaC/terraform/aws-managed/base/core の perimeter.tf の資源側の Deny の例外
# （perimeter_exempt_principals）に入れてあり、IAM 側の Deny も付けない。ストリームとワークグループの名前は固定
# （graph の sync.tf と workflow の locals.tf が同じ名前を作る）。
# Cost: Firehose は取り込んだ量の課金（Iceberg 宛て。PoC の量なら月に数セント）、Athena はスキャン量の課金（1 回 1 GiB で打ち切る）。
# エンドポイント（kinesis-firehose / athena）は IaC/terraform/aws-managed/base/core で数える
locals {
  alert_stream      = "${local.name_prefix}-alert-events"
  alert_firehose    = "${local.name_prefix}-alert-firehose"
  history_workgroup = "${local.name_prefix}-history"
  # Glue の S3 Tables 連携のカタログ（ops/up.sh の ensure_s3tables_catalog）の下の、このテーブルバケットのカタログ
  athena_catalog = "s3tablescatalog/${local.table_bucket}"
  glue_catalog   = "arn:${local.partition}:glue:${var.region}:${local.account_id}:catalog"
  alert_errors   = "firehose-errors/alert_events/"
}

resource "aws_iam_role" "alert_firehose" {
  name        = local.alert_firehose
  description = "Firehose ${local.alert_stream} - appends the alert notifications to the S3 Tables table alert_events, failed rows to the asset bucket"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "firehose.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
      }
    }]
  })
}

resource "aws_iam_role_policy" "alert_firehose" {
  name = local.alert_firehose
  role = aws_iam_role.alert_firehose.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Lake Formation は使わず IAM で許す（s3tablescatalog は IAM_ALLOWED_PRINCIPALS と AllowFullTableExternalDataAccess で作る）
        Sid    = "S3Tables"
        Effect = "Allow"
        Action = [
          "s3tables:GetTableBucket",
          "s3tables:GetNamespace",
          "s3tables:GetTable",
          "s3tables:GetTableData",
          "s3tables:GetTableMetadataLocation",
          "s3tables:PutTableData",
          "s3tables:UpdateTableMetadataLocation",
        ]
        Resource = [
          local.table_bucket_arn,
          "${local.table_bucket_arn}/table/*",
        ]
      },
      {
        # Firehose はテーブルを Glue のカタログ s3tablescatalog/<テーブルバケット> の名前で引く
        Sid    = "GlueCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetCatalog",
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:UpdateTable",
        ]
        Resource = [
          local.glue_catalog,
          "${local.glue_catalog}/s3tablescatalog",
          "${local.glue_catalog}/s3tablescatalog/*",
          "arn:${local.partition}:glue:${var.region}:${local.account_id}:database/*",
          "arn:${local.partition}:glue:${var.region}:${local.account_id}:table/*/*",
        ]
      },
      {
        # 書けなかった行（s3_backup_mode = FailedDataOnly）
        Sid      = "ErrorBucket"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:AbortMultipartUpload"]
        Resource = "${local.bucket_arn}/firehose-errors/*"
      },
      {
        Sid      = "ErrorBucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:GetBucketLocation"]
        Resource = local.bucket_arn
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = "logs:PutLogEvents"
        Resource = "${aws_cloudwatch_log_group.alert_firehose.arn}:*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "alert_firehose" {
  name              = "/aws/kinesisfirehose/${local.alert_stream}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_stream" "alert_firehose" {
  name           = "DestinationDelivery"
  log_group_name = aws_cloudwatch_log_group.alert_firehose.name
}

# ロールとポリシーを作った直後は、Firehose が assume できずに作成が止まることがある（IAM の反映遅れ。
# 2026-10-09 の AWS で InvalidArgumentException: The security token included in the request is invalid … を実測し、打ち直しで通った）。
# 30 秒は推定。足りなければ延ばす。
# triggers はロールが作り直されたら待ちも作り直すため（state を残したまま接頭辞を変えたときも待つ）
resource "time_sleep" "alert_firehose_iam" {
  create_duration = "30s"
  triggers = {
    role = aws_iam_role.alert_firehose.unique_id
  }

  depends_on = [aws_iam_role.alert_firehose, aws_iam_role_policy.alert_firehose]
}

resource "aws_kinesis_firehose_delivery_stream" "alert_events" {
  name        = "${local.name_prefix}-alert-events"
  destination = "iceberg"

  iceberg_configuration {
    role_arn           = aws_iam_role.alert_firehose.arn
    catalog_arn        = "${local.glue_catalog}/${local.athena_catalog}"
    buffering_interval = 60
    buffering_size     = 1
    s3_backup_mode     = "FailedDataOnly"

    destination_table_configuration {
      database_name = aws_s3tables_namespace.nwc.namespace
      table_name    = aws_s3tables_table.alert_events.name
    }

    s3_configuration {
      role_arn            = aws_iam_role.alert_firehose.arn
      bucket_arn          = local.bucket_arn
      error_output_prefix = local.alert_errors
    }

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.alert_firehose.name
      log_stream_name = aws_cloudwatch_log_stream.alert_firehose.name
    }
  }

  # ロールの権限が付く前に作ると、Firehose がテーブルを確かめられずに作成が失敗する。ロールの反映も待つ（time_sleep はポリシーのあとに数える）
  depends_on = [time_sleep.alert_firehose_iam]
}

# query_history（app/agentcore/evidence.py）と、修復案の読み取り（app/agentcore/proposals.py の list_proposals / get_proposal。Web の承認タブと tools の Lambda。2026-10-05）が投げる。
# クエリの結果は Athena の管理ストレージに置く（結果用のバケットを作らない）
resource "aws_athena_workgroup" "history" {
  name          = local.history_workgroup
  description   = "query_history and list_proposals of the agent and the web - reads alert_events and proposal_events through s3tablescatalog"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = false
    bytes_scanned_cutoff_per_query     = 1073741824 # 1 GiB で打ち切る（走りすぎの歯止め。PoC の alert_events は数 MB）

    managed_query_results_configuration {
      enabled = true
    }
  }
}
