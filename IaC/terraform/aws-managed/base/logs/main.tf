# logs バケット（cycle 035「S3 の置き場を整える」）。EMR Serverless のログ（emr/）と、Firehose が書けなかった行（firehose-errors/）を置く。
# 書くのは IaC/terraform/aws-managed/pipeline/analytics（OSS 版は IaC/terraform/oss/pipeline/analytics にリンクされた history.tf）。
# 中身は 7 日で消える。ops/down.sh はこのルートを消さない（VPC を消したあとでもログを読める。空のバケットは無料）。
# 消すなら terraform -chdir=IaC/terraform/aws-managed/base/logs destroy（force_destroy = true なので中身ごと消える）。
# 配布物と Spark の checkpoint は base/core の assets バケット（ops/down.sh で消える）。

# リソース名の接頭辞であり Project タグの値（base/ecr と同じ）
locals {
  # 末尾は var.project（IaC/terraform/aws-managed/ は既定の nwc-poc、OSS 版の IaC/terraform/oss/ は oss.auto.tfvars の nwc-oss。cycle 005）
  name_prefix = "${var.owner}-${var.project}"
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
}

resource "aws_s3_bucket" "logs" {
  bucket        = "${local.name_prefix}-logs-${local.account_id}"
  force_destroy = true

  tags = { Name = "${local.name_prefix}-logs" }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# バケット全体を 7 日で消す。バージョニングは付けない（期限で消えたものを戻す要件は無い）
resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "expire-7-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id

  # DenyInsecureTransport だけ。base/core の assets バケットにある DenyOutsideVpc は付けない:
  # (1) このルートは base/core より先に作り、ops/down.sh で消さないので、VPC の id を知らず、VPC より長生きする。
  # (2) 書くのは AWS のサービスで、Firehose は VPC の外から書く（pipeline/analytics/history.tf の先頭）。
  # EMR のジョブの側は IAM の Deny（pipeline/analytics/access.tf の emr_perimeter）が VPC の外からの利用を止める
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.logs.arn, "${aws_s3_bucket.logs.arn}/*"]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.logs]
}
