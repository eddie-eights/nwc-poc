terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
    # status の Lambda を IAM の反映を待ってから作る（sync.tf の status_iam）
    time = {
      source  = "hashicorp/time"
      version = "~> 0.13"
    }
  }
}
