# ---------------------------------------------------------------- access for the roles of terraform/base/core
resource "aws_iam_role_policy" "graph_access" {
  for_each = local.reader_role_ids

  name = "${local.name_prefix}-graph-access"
  role = each.value

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "OpenCypher"
        Effect = "Allow"
        Action = [
          "neptune-graph:ReadDataViaQuery",
          "neptune-graph:WriteDataViaQuery",
          "neptune-graph:DeleteDataViaQuery",
          "neptune-graph:GetQueryStatus",
          "neptune-graph:CancelQuery",
        ]
        Resource = aws_neptunegraph_graph.graph.arn
      },
      {
        Sid      = "Parameters"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/${local.name_prefix}/*"
      },
    ]
  })
}
