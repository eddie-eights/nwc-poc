output "logs_bucket_name" {
  description = "Name of the logs bucket (<prefix>-logs-<account>). IaC/terraform/aws-managed/pipeline/analytics reads it from this state as the place of the rows Firehose could not write (firehose-errors/). Objects expire after 7 days. ops/down.sh does not destroy this root."
  value       = aws_s3_bucket.logs.bucket
}

output "logs_bucket_arn" {
  description = "ARN of the logs bucket. IaC/terraform/aws-managed/pipeline/analytics reads it from this state (Firehose error rows). ops/down.sh does not destroy this root."
  value       = aws_s3_bucket.logs.arn
}
