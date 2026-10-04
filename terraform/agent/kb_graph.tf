# ---------------------------------------------------------------- knowledge base on Neptune Analytics (GraphRAG)
# kb_graphrag = true（ops/up.sh は KB_GRAPHRAG=1）のとき、ベクトルの置き場を OpenSearch Serverless でなく Neptune Analytics のグラフにする。
# 取り込みのときに Bedrock がチャンクから実体（機器・プロトコル・コマンドなど）を抜き出して、チャンクと実体をつなぐグラフを作る。
# Retrieve はベクトルで当たったチャンクから、同じ実体につながる別のチャンクも辿って返す（手順書をまたぐ質問に効く）。
# terraform/pipeline/graph の Neptune（トポロジ。Gremlin）とは別のエンジンで、中身も混ざらない。
# OpenSearch Serverless のコレクション・VPC エンドポイント・索引の Lambda は作らない
resource "aws_neptunegraph_graph" "kb" {
  count = local.kb_graph ? 1 : 0

  graph_name = "${local.name_prefix}-kb"
  # 最小の 16 m-NCU（東京で $0.581/h。2026-10-04 に公開の料金ファイルで確認）。自動では増減しない
  provisioned_memory = var.kb_graph_memory
  replica_count      = 0
  # 公開しない。プライベートエンドポイントも作らない（Bedrock がサービス側からつなぐ。Runtime はグラフを直接呼ばない）
  public_connectivity = false
  deletion_protection = false

  # 作るときにしか決められない。Titan Embeddings v2 の 1024 次元
  vector_search_configuration {
    vector_search_dimension = 1024
  }

  tags = { Name = "${local.name_prefix}-kb" }
}

locals {
  # jp. / apac. / global. などで始まる ID は推論プロファイル、そうでなければ基盤モデル
  kb_graph_model_id = var.kb_graph_model_id != "" ? var.kb_graph_model_id : var.model_id
  kb_graph_model_arn = (
    can(regex("^(jp|apac|us|eu|au|global)[.]", local.kb_graph_model_id))
    ? "arn:${local.partition}:bedrock:${var.region}:${local.account_id}:inference-profile/${local.kb_graph_model_id}"
    : "arn:${local.partition}:bedrock:${var.region}::foundation-model/${local.kb_graph_model_id}"
  )
  kb_data_source_name = "${local.name_prefix}-docs"

  # KB のサービスロールに足す分（kb.tf の aws_iam_role_policy.kb）
  kb_graph_statements = [for s in [
    {
      Sid      = "NeptuneGraph"
      Effect   = "Allow"
      Action   = ["neptune-graph:GetGraph", "neptune-graph:ReadDataViaQuery", "neptune-graph:WriteDataViaQuery", "neptune-graph:DeleteDataViaQuery"]
      Resource = [local.kb_graph ? aws_neptunegraph_graph.kb[0].arn : ""]
    },
    {
      # 取り込みのときに実体を抜き出すモデル。推論プロファイルは行き先のリージョンのモデルを呼ぶので、リージョンは * にする
      Sid    = "GraphBuildModel"
      Effect = "Allow"
      Action = ["bedrock:InvokeModel"]
      Resource = [
        "arn:${local.partition}:bedrock:*::foundation-model/*",
        "arn:${local.partition}:bedrock:${var.region}:${local.account_id}:inference-profile/*",
      ]
    },
  ] : s if local.kb_graph]
}

# aws provider（6.64）の aws_bedrockagent_data_source には contextEnrichmentConfiguration（実体の抜き出し = GraphRAG の指定）が無いので、
# apply を打つ PC の AWS CLI で作る（同じ名前がもうあれば作らない）。destroy のときは KB より先に消す。
# ID は state に残らないので、ops/up.sh は名前（output data_source_name）から引く。
# RETAIN なのでグラフの中身は消さない（グラフごと消す）
resource "terraform_data" "kb_graph_data_source" {
  count = local.kb_graph ? 1 : 0

  # KB を作り直したとき・モデルを替えたときに作り直す
  triggers_replace = [aws_bedrockagent_knowledge_base.kb[0].id, local.kb_graph_model_arn]

  input = {
    region = var.region
    kb_id  = aws_bedrockagent_knowledge_base.kb[0].id
    name   = local.kb_data_source_name
  }

  provisioner "local-exec" {
    interpreter = ["/bin/sh", "-c"]
    command     = <<-EOT
      set -eu
      id=$(aws bedrock-agent list-data-sources --region "$REGION" --knowledge-base-id "$KB_ID" \
        --query "dataSourceSummaries[?name=='$NAME'].dataSourceId | [0]" --output text)
      if [ -z "$id" ] || [ "$id" = "None" ]; then
        aws bedrock-agent create-data-source --region "$REGION" --cli-input-json "$INPUT" \
          --query dataSource.dataSourceId --output text
      else
        echo "data source $NAME already exists: $id"
      fi
    EOT
    environment = {
      REGION = var.region
      KB_ID  = aws_bedrockagent_knowledge_base.kb[0].id
      NAME   = local.kb_data_source_name
      INPUT = jsonencode({
        knowledgeBaseId    = aws_bedrockagent_knowledge_base.kb[0].id
        name               = local.kb_data_source_name
        description        = "Markdown files under s3://<bucket>/docs/ (GraphRAG)"
        dataDeletionPolicy = "RETAIN"
        dataSourceConfiguration = {
          type = "S3"
          s3Configuration = {
            bucketArn         = local.bucket_arn
            inclusionPrefixes = ["docs/"]
          }
        }
        vectorIngestionConfiguration = {
          contextEnrichmentConfiguration = {
            type = "BEDROCK_FOUNDATION_MODEL"
            bedrockFoundationModelConfiguration = {
              modelArn                        = local.kb_graph_model_arn
              enrichmentStrategyConfiguration = { method = "CHUNK_ENTITY_EXTRACTION" }
            }
          }
        }
      })
    }
  }

  # 消えるのを待ってから KB の削除へ進む（最大 2 分）
  provisioner "local-exec" {
    when        = destroy
    interpreter = ["/bin/sh", "-c"]
    command     = <<-EOT
      set -eu
      id=$(aws bedrock-agent list-data-sources --region "$REGION" --knowledge-base-id "$KB_ID" \
        --query "dataSourceSummaries[?name=='$NAME'].dataSourceId | [0]" --output text 2>/dev/null || true)
      if [ -z "$id" ] || [ "$id" = "None" ]; then exit 0; fi
      aws bedrock-agent delete-data-source --region "$REGION" --knowledge-base-id "$KB_ID" --data-source-id "$id" >/dev/null
      for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24; do
        aws bedrock-agent get-data-source --region "$REGION" --knowledge-base-id "$KB_ID" --data-source-id "$id" >/dev/null 2>&1 || exit 0
        sleep 5
      done
      echo "data source $id is still being deleted" >&2
      exit 1
    EOT
    environment = {
      REGION = self.input.region
      KB_ID  = self.input.kb_id
      NAME   = self.input.name
    }
  }
}
