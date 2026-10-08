# ---------------------------------------------------------------- access for the roles of terraform/base/core
# Web の EC2 は読み書き（ops/up.sh の 7-3b と ops/sync-graph.sh が Web の EC2 の上で ops/seed_graph.py を走らせ、トポロジタブの
# seed / add_link / remove_link も書く）。チャットの Runtime は読むだけ（修復案の頂点をやめた 2026-10-05 から、Runtime が書くものは無い）
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
        Action = concat(
          ["neptune-graph:ReadDataViaQuery"],
          each.value == local.graph_writer_role ? ["neptune-graph:WriteDataViaQuery", "neptune-graph:DeleteDataViaQuery"] : [],
          ["neptune-graph:GetQueryStatus", "neptune-graph:CancelQuery"],
        )
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
