terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    # Firehose を IAM の反映を待ってから作る（history.tf の alert_firehose_iam）
    time = {
      source  = "hashicorp/time"
      version = "~> 0.13"
    }
  }
}
