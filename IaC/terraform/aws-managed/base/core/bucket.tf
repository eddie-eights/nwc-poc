# ---------------------------------------------------------------- assets bucket（cycle 035 で kb から改名）
# ops/up.sh が置く配布物と Spark の checkpoint。プレフィックスは部品名で切る:
#   web/             画面のコードと wheel（Web の EC2 の user_data が読む）
#   knowledge-base/  KB の取り込み元（IaC/terraform/aws-managed/agent の create_knowledge_base = true のとき。agent/kb.tf の inclusion_prefixes）
#   lab/             containerlab の rpm とトポロジ（lab の EC2 の user_data が読む）
#   spark/           Spark のスクリプト（snmp_sinks.py）と jars/、checkpoint/<MSK の uuid>/（Spark 自身が読み書きする。OSS 版も同じパス）
# Firehose が書けなかった行は別のバケット（base/logs の <prefix>-logs-<アカウント>。7 日で消え、ops/down.sh で消さない）。
# force_destroy = true なので、中身が残っていても terraform destroy で消える
resource "aws_s3_bucket" "assets" {
  bucket        = "${local.name_prefix}-assets-${local.account_id}"
  force_destroy = true

  tags = { Name = "${local.name_prefix}-assets" }
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "assets" {
  bucket = aws_s3_bucket.assets.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "assets" {
  bucket = aws_s3_bucket.assets.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_policy" "assets" {
  bucket = aws_s3_bucket.assets.id

  # DenyOutsideVpc は perimeter.tf の資源側。バケットポリシーの読み書きだけは外す（デプロイする人が変わって締め出されても、
  # その人が aws s3api delete-bucket-policy で戻せる。docs/troubleshooting.md）
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.assets.arn, "${aws_s3_bucket.assets.arn}/*"]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
      }], var.network_perimeter ? [{
      Sid       = "DenyOutsideVpc"
      Effect    = "Deny"
      Principal = "*"
      NotAction = ["s3:GetBucketPolicy", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy"]
      Resource  = [aws_s3_bucket.assets.arn, "${aws_s3_bucket.assets.arn}/*"]
      Condition = {
        StringNotEqualsIfExists = { "aws:SourceVpc" = aws_vpc.this.id }
        BoolIfExists            = { "aws:ViaAWSService" = "false" }
        Bool                    = { "aws:PrincipalIsAWSService" = "false" }
        ArnNotLike              = { "aws:PrincipalArn" = local.perimeter_exempt_principals }
      }
    }] : [])
  })

  depends_on = [aws_s3_bucket_public_access_block.assets]
}
