# ---------------------------------------------------------------- neptune analytics
# グラフは Neptune Analytics（メモリ上のグラフ。openCypher と neptune.algo.* のアルゴリズム）。2026-10-04 に Neptune Database（Gremlin、
# db.t4g.medium 1 台）から置き換えた: 機器が増えたときに影響範囲や中心性をグラフの側で計算したい、がユーザーの理由。グラフ DB はこの 1 つだけ。
# 公開のエンドポイントは閉じ（public_connectivity = false）、VPC の中からは terraform/base/core のインターフェース型エンドポイント
# neptune-graph-data（private DNS で <graph-id>.<region>.neptune-graph.amazonaws.com がそこへ向く）で届く。認証は IAM（SigV4）だけ。
# サブネットグループも SG も持たない（VPC の中に置くものが無い）。この経路だけで届かなければ aws_neptunegraph_private_graph_endpoint を足す
# （AWS では未確認。docs/troubleshooting.md）。
# 料金は確保したメモリ（m-NCU）の時間課金で、止めておく手段は無い。使わない日は ops/down.sh で消す
resource "aws_neptunegraph_graph" "graph" {
  graph_name          = "${local.name_prefix}-graph"
  provisioned_memory  = var.provisioned_memory
  public_connectivity = false
  replica_count       = 0 # その日に消す使い捨て。レプリカは同じ m-NCU の料金がもう 1 つ分かかる
  deletion_protection = var.deletion_protection
}

# topology.py / graph.py はここからグラフの ID を読む（環境変数 PARAM_PREFIX = /<接頭辞>。terraform/base/core が Runtime と Web に渡す）
resource "aws_ssm_parameter" "graph_id" {
  name        = "/${local.name_prefix}/neptune-graph-id"
  type        = "String"
  value       = aws_neptunegraph_graph.graph.id
  description = "Neptune Analytics graph id (g-xxxxxxxxxx) for the chat runtime and the web (terraform/pipeline/graph)"
}
