# ---------------------------------------------------------------- runtime role of the Spark job
resource "aws_iam_role" "emr" {
  name        = "${local.name_prefix}-emr-runtime"
  description = "EMR Serverless job runtime - reads MSK, writes the sinks (S3 Tables, OpenSearch Serverless, Prometheus, Splunk HEC with the token from SSM), reads the script and jars from the asset bucket, writes its driver logs to CloudWatch"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "emr-serverless.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:${local.partition}:emr-serverless:${var.region}:${local.account_id}:/applications/*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "emr" {
  name = "${local.name_prefix}-emr-runtime"
  role = aws_iam_role.emr.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        # スクリプトと jar を読む。checkpoint を読み書きする
        Sid      = "AssetBucket"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${local.bucket_arn}/${local.s3_prefix}/*"
      },
      {
        Sid      = "AssetBucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = local.bucket_arn
      },
      {
        # Kafka を読む（consumer group は spark-kafka-source-* で Spark が付ける）。
        # AlterCluster: SASL/SCRAM の収集器（syslog-ng / GoFlow2 / gnmic）のユーザーに logs / flows / gnmi / metrics の ACL を入れる（app/spark/snmp_sinks.py の ensure_acls の createAcls。cycle 012、gnmic は 013）。
        # ACL の作成に要る権限はこれだけで、Kafka の ALTER CLUSTER と同じ幅（どの主体・資源への ACL の作成と削除、パーティションの再配置、
        # リーダー選出、SCRAM の資格情報の変更 等。MSK でどれが効くかは未確認）を許す（docs/architecture/resources/msk.md）
        Sid      = "KafkaCluster"
        Effect   = "Allow"
        Action   = ["kafka-cluster:Connect", "kafka-cluster:DescribeCluster", "kafka-cluster:AlterCluster"]
        Resource = local.msk_cluster_arn
      },
      {
        Sid    = "KafkaTopics"
        Effect = "Allow"
        # CreateTopic: Spark が起動時に無いトピックを作る（app/spark/snmp_sinks.py の ensure_topics。Telegraf が最初の trap を出すまで traps が無く、
        # SASL/SCRAM の syslog-ng / GoFlow2 には CREATE の ACL を付けないので、AWS の文書どおりなら logs / flows を自動で作れない）
        Action   = ["kafka-cluster:DescribeTopic", "kafka-cluster:ReadData", "kafka-cluster:CreateTopic"]
        Resource = local.topic_arns
      },
      {
        Sid      = "KafkaGroups"
        Effect   = "Allow"
        Action   = ["kafka-cluster:DescribeGroup", "kafka-cluster:AlterGroup"]
        Resource = local.group_arns
      },
      {
        Sid      = "DriverLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = "${aws_cloudwatch_log_group.emr.arn}:*"
      },
      {
        Sid      = "DriverLogsGroups"
        Effect   = "Allow"
        Action   = "logs:DescribeLogGroups"
        Resource = "*"
      },
      ],
      # ---- 格納先ごと（sinks.tf。選んだものだけ）
      # 「cond ? [..] : []」は両辺の型が揃わず validate が落ちるので for … if で絞る
      [for s in [{
        # Iceberg のカタログ操作（S3 Tables の API）。テーブルは Terraform が作るが、Spark はメタデータの場所を読み書きする。
        # 2026-10-02 までは検知が証跡の anomaly_events に書いていたので、iceberg を選ばなくても付けていた
        Sid    = "S3TablesCatalog"
        Effect = "Allow"
        Action = [
          "s3tables:GetTableBucket",
          "s3tables:ListNamespaces",
          "s3tables:GetNamespace",
          "s3tables:ListTables",
          "s3tables:GetTable",
          "s3tables:GetTableMetadataLocation",
          "s3tables:UpdateTableMetadataLocation",
          "s3tables:GetTableData",
          "s3tables:PutTableData",
        ]
        Resource = [
          aws_s3tables_table_bucket.tables.arn,
          "${aws_s3tables_table_bucket.tables.arn}/table/*",
        ]
      }] : s if local.sink_iceberg],
      [for s in [{
        # コレクションの API（中身の権限はデータアクセスポリシー aws_opensearchserverless_access_policy.logs）
        Sid      = "OpenSearchCollection"
        Effect   = "Allow"
        Action   = "aoss:APIAccessAll"
        Resource = local.sink_opensearch ? aws_opensearchserverless_collection.logs[0].arn : ""
      }] : s if local.sink_opensearch],
      [for s in [{
        Sid      = "PrometheusRemoteWrite"
        Effect   = "Allow"
        Action   = "aps:RemoteWrite"
        Resource = local.sink_prometheus ? aws_prometheus_workspace.metrics[0].arn : ""
      }] : s if local.sink_prometheus],
      [for s in [{
        # HEC の token（SSM の SecureString）を起動時に読む（ssm の API へは IaC/terraform/aws-managed/base/core の ssm のエンドポイントを通る）。
        # 復号は SSM の AWS 管理キー aws/ssm のキーポリシーが ssm 経由の呼び出しに許しているので kms:Decrypt は要らない
        # （自分の KMS キーで暗号化したパラメータなら、そのキーに kms:Decrypt を足す）
        Sid      = "SplunkHecToken"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = local.splunk_token_parameter_arn
      }] : s if local.sink_splunk],
    )
  })
}

# IaC/terraform/aws-managed/base/core の perimeter.tf の Deny（VPC エンドポイントを通らない呼び出しを拒む）。
# スクリプト・jar・checkpoint は S3 gateway、S3 Tables は s3tables のエンドポイント、remote write は aps-workspaces、
# token は ssm のエンドポイントを通る。Splunk の HEC は VPC の中（ECS）
resource "aws_iam_role_policy_attachment" "emr_perimeter" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  role       = aws_iam_role.emr.name
  policy_arn = local.perimeter_policy_arn
}
