# ---------------------------------------------------------------- AgentCore Runtime
# 実行ロールそのものは terraform/base/core が持つ（terraform/pipeline/stream / terraform/pipeline/graph がロール名を読んでポリシーを付けるため）。
# ここでは Runtime が動くのに要るポリシーを足し、Runtime を作る
resource "aws_iam_role_policy" "runtime" {
  name = "runtime"
  role = local.runtime_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid      = "EcrPull"
          Effect   = "Allow"
          Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
          Resource = "arn:${local.partition}:ecr:${var.region}:${local.account_id}:repository/${local.name_prefix}-*"
        },
        {
          Sid      = "EcrToken"
          Effect   = "Allow"
          Action   = "ecr:GetAuthorizationToken"
          Resource = "*"
        },
        {
          Sid      = "LogsGroup"
          Effect   = "Allow"
          Action   = ["logs:DescribeLogStreams", "logs:CreateLogGroup"]
          Resource = "arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"
        },
        {
          Sid      = "LogsDescribe"
          Effect   = "Allow"
          Action   = "logs:DescribeLogGroups"
          Resource = "arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:*"
        },
        {
          Sid      = "LogsWrite"
          Effect   = "Allow"
          Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
          Resource = "arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
        },
        {
          Sid      = "Xray"
          Effect   = "Allow"
          Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
          Resource = "*"
        },
        {
          Sid      = "Metrics"
          Effect   = "Allow"
          Action   = "cloudwatch:PutMetricData"
          Resource = "*"
          Condition = {
            StringEquals = { "cloudwatch:namespace" = "bedrock-agentcore" }
          }
        },
        {
          Sid    = "WorkloadToken"
          Effect = "Allow"
          Action = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT"]
          Resource = [
            "arn:${local.partition}:bedrock-agentcore:${var.region}:${local.account_id}:workload-identity-directory/default",
            "arn:${local.partition}:bedrock-agentcore:${var.region}:${local.account_id}:workload-identity-directory/default/workload-identity/${local.runtime_name}-*",
          ]
        },
        {
          # jp.* の推論プロファイルは東京と大阪のモデルへ振り分けるので、リージョンは * にする
          Sid    = "BedrockInvoke"
          Effect = "Allow"
          Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
          Resource = [
            "arn:${local.partition}:bedrock:*::foundation-model/*",
            "arn:${local.partition}:bedrock:${var.region}:${local.account_id}:inference-profile/*",
          ]
        },
      ],
      # KB を作ったときだけ Retrieve を許す
      [for s in [
        {
          Sid      = "KbRetrieve"
          Effect   = "Allow"
          Action   = "bedrock:Retrieve"
          Resource = local.kb ? aws_bedrockagent_knowledge_base.kb[0].arn : ""
        },
      ] : s if local.kb],
      [
        {
          # Standard 階層の判定はプロファイルの行き先リージョンで行われるので、リージョンは * にする
          Sid    = "ApplyGuardrail"
          Effect = "Allow"
          Action = "bedrock:ApplyGuardrail"
          Resource = [
            aws_bedrock_guardrail.this.guardrail_arn,
            "arn:${local.partition}:bedrock:*:${local.account_id}:guardrail-profile/${var.guardrail_profile_id}",
          ]
        },
      ],
    )
  })
}

resource "aws_bedrockagentcore_agent_runtime" "agent" {
  agent_runtime_name = local.runtime_name
  description        = "${local.name_prefix} chat agent"
  role_arn           = local.runtime_role_arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = local.agent_image_uri
    }
  }

  network_configuration {
    network_mode = "VPC"
    network_mode_config {
      # サブネット a から var.runtime_az_num 個（1〜3。既定 1 は 2026-10-05 のユーザー決定）。2 以上のときは ops/up.sh が
      # エンドポイント（terraform/base/core の endpoints_az_num）も同じ数以上にそろえる（そろわないと 2 AZ が見かけだけになる）。
      # API はサブネット 1 つでも受け付ける（AgentCore Control API Reference「VpcConfig」の subnets は 1〜16 個、
      #   https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_VpcConfig.html、2026-10-05 確認）。
      # 手引きは高可用のために別々の AZ のサブネットを 2 つ以上と勧めるが、1 つを禁じる記述は無い
      # （Amazon Bedrock AgentCore Developer Guide「Configure Amazon Bedrock AgentCore Runtime and tools for VPC」の Best practices、
      #   https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html、2026-10-05 確認）。
      # 1 つ（既定）で作って動くことは 2026-10-05 に AWS で確かめた。2 つ以上は AWS で未確認。
      # 対応していない AZ のサブネットは作成時に失敗する。東京の a / b / c（apne1-az1 / az4 / az2）は対応している（同じ手引きで 2026-10-05 確認）
      subnets         = slice(local.subnet_ids, 0, var.runtime_az_num)
      security_groups = [local.runtime_sg_id]
    }
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  # 放置したセッションを 5 分で畳む（メモリ課金を止める）。1 セッションの寿命は最大 1 時間
  lifecycle_configuration = [{
    idle_runtime_session_timeout = 300
    max_lifetime                 = 3600
  }]

  environment_variables = merge(
    {
      MODEL_ID          = var.model_id
      BEDROCK_REGION    = var.region
      GUARDRAIL_ID      = aws_bedrock_guardrail.this.guardrail_id
      GUARDRAIL_VERSION = aws_bedrock_guardrail_version.r1.version
      # terraform/pipeline/graph が書く SSM（neptune-graph-id。トポロジ・修復案）の接頭辞。無ければ静的データで動く
      PARAM_PREFIX = local.param_prefix
    },
    # KB を作らないときは KNOWLEDGE_BASE_ID を渡さない（agent は Retrieve を飛ばしてモデルとツールだけで答える）
    local.kb ? {
      KNOWLEDGE_BASE_ID          = aws_bedrockagent_knowledge_base.kb[0].id
      NUMBER_OF_RESULTS          = tostring(var.number_of_results)
      NUMBER_OF_RERANKED_RESULTS = tostring(var.number_of_reranked_results)
    } : {},
    { for k, v in { RERANK_MODEL_ARN = local.rerank_model_arn } : k => v if local.kb && local.rerank },
    # OSS 版（cycle 005）だけ足す接続先の切り替え。マネージド版では空の map で、上の環境変数だけになる。
    # graph.py は Neptune の代わりに Neo4j を読む（URI とパスワードは PARAM_PREFIX で SSM の neo4j-uri / neo4j-password。読む権限は
    # oss/terraform/pipeline/graph の access.tf、届く経路は terraform/base/core の oss.tf の runtime → neo4j）。ドライバは agent/requirements-oss.txt。
    # evidence.py の認証も OSS 版のもの（Basic / 署名なし）にそろえる。エンドポイントはマネージド版と同じく Runtime には渡さず、
    # ログとメトリクスは Gateway の tools Lambda（terraform/workflow の gateway.tf）越しに読む
    local.oss ? { GRAPH_BACKEND = "neo4j", OPENSEARCH_AUTH = "basic", PROMETHEUS_AUTH = "none" } : {},
  )

  tags = { Name = "${local.name_prefix}-agent" }

  # イメージ取得とログ出力は AgentCore のサービスがこのロールで行う。コンテナの中から Bedrock / SSM へは terraform/base/core の
  # インターフェース型エンドポイントを通る（base が先にできている）
  depends_on = [aws_iam_role_policy.runtime]
}

# この VPC のエンドポイント（bedrock-agentcore）を通らない InvokeAgentRuntime を拒む（terraform/base/core の perimeter.tf の資源側。
# AgentCore の文書の DenyAllExceptVPC と同じ形）。呼ぶのは Web の EC2 と workflow のワーカーで、どちらも VPC の中。
# デプロイする人は外れるので、docs/deploy.md の「Runtime だけを CLI で確かめる」は PC から打てる
resource "aws_bedrockagentcore_resource_policy" "runtime" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  resource_arn = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyOutsideVpc"
      Effect    = "Deny"
      Principal = "*"
      Action    = "bedrock-agentcore:InvokeAgentRuntime"
      Resource  = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn
      Condition = {
        StringNotEqualsIfExists = { "aws:SourceVpc" = local.vpc_id }
        BoolIfExists            = { "aws:ViaAWSService" = "false" }
        ArnNotLike              = { "aws:PrincipalArn" = local.perimeter_exempt_principals }
      }
    }]
  })
}

# ---------------------------------------------------------------- hand the runtime ARN to the chat web
# web EC2 は起動時に環境変数で ARN を受け取るのではなく、この SSM パラメータを読む（60 秒キャッシュ）。
# こうすると agent を後から apply / destroy しても terraform/base/core（web EC2）を作り直さずに済む
resource "aws_ssm_parameter" "runtime_arn" {
  name        = "${local.param_prefix}/runtime-arn"
  description = "ARN of the AgentCore Runtime the chat web invokes (written by terraform/agent)"
  type        = "String"
  value       = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn

  tags = { Name = "${local.name_prefix}-runtime-arn" }
}

resource "aws_iam_role_policy" "web_invoke_runtime" {
  name = "invoke-runtime"
  role = local.web_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "bedrock-agentcore:InvokeAgentRuntime"
      Resource = [
        aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn,
        "${aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn}/*",
      ]
    }]
  })
}
