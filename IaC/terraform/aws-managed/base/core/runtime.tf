# ---------------------------------------------------------------- AgentCore Runtime execution role
# ロールと、Nautobot の内部のシークレットの Deny（下の runtime_deny_nautobot_secrets。cycle 044）だけをここに置く。Runtime 本体とその実行ポリシーは IaC/terraform/aws-managed/agent（機能 agent）。IaC/terraform/aws-managed/pipeline/stream / IaC/terraform/aws-managed/pipeline/graph は
# このロール名を state から読み、Neptune などを読むポリシーを足すので、ロールは agent より長生きする土台に置く
resource "aws_iam_role" "runtime" {
  name        = "${local.name_prefix}-runtime"
  description = "Execution role for the ${local.name_prefix} AgentCore Runtime"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:${local.partition}:bedrock-agentcore:${var.region}:${local.account_id}:*" }
      }
    }]
  })

  tags = { Name = "${local.name_prefix}-runtime" }
}

# Nautobot の中だけのシークレット（secret-key / admin-password / db-password。locals.tf）を Runtime に読ませない（cycle 044）。
# このロールは自分では Allow を持たないが、stream / graph / workflow が足す /<prefix>/* の ssm:GetParameter に勝たせるため、ロールの土台に置く
resource "aws_iam_role_policy" "runtime_deny_nautobot_secrets" {
  name = "${local.name_prefix}-runtime-deny-nautobot-secrets"
  role = aws_iam_role.runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DenyNautobotSecrets"
        Effect   = "Deny"
        Action   = "ssm:GetParameter*"
        Resource = local.nautobot_secret_parameter_arns
      },
    ]
  })
}
