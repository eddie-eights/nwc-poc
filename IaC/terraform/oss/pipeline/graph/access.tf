# ---------------------------------------------------------------- access for the roles of IaC/terraform/aws-managed/base/core
# マネージド版の access.tf の OSS 版。Neo4j は IAM で絞れない（Bolt のユーザーとパスワード。届く相手は SG の通信の表で絞る）ので、
# neptune-graph の行は無く、Web の EC2 とチャットの Runtime が SSM の /<接頭辞>/* を読む行だけ残す
# （app/agentcore/graph.py が neo4j-uri と neo4j-password を引く。パスワードは SecureString で、AWS 管理の aws/ssm キーなので kms:Decrypt は要らない）。
# 読み書きの区別も無い（Neo4j のユーザーは neo4j 1 つ。マネージド版で Runtime が読むだけなのは IAM で絞っていたから）
resource "aws_iam_role_policy" "graph_access" {
  for_each = local.reader_role_ids

  name = "${local.name_prefix}-graph-access"
  role = each.value

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Parameters"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/${local.name_prefix}/*"
      },
    ]
  })
}
