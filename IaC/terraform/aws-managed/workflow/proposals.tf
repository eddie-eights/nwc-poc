# ---------------------------------------------------------------- proposals（修復案）
# 修復案の置き場は S3 Tables の proposal_events だけ（terraform/pipeline/analytics の tables.tf。Neptune の頂点 proposal は 2026-10-05 にやめた）。
# 作成・承認・却下・適用・確認を 1 行ずつ足し、どの行も全項目を持つ。修復案の「いま」は proposal_id ごとに seq が最大の行。
# status: pending → approved / rejected（人）→ applied → verified / failed（ワーカー）, expired（時間切れ）, obsolete（承認のあいだに異常が閉じた）。
# 書くのは worker だけ（PyIceberg）。読むのは Web の承認タブと tools の Lambda の list_proposals（Athena。locals.tf の history_read_statements）。
# 承認・却下は Web が決定のキュー（events.tf の decisions）に送り、worker がワークフローにシグナルを送る。
# 以前は DynamoDB のテーブルだった（2026-09-24 に Neptune と S3 Tables に寄せた）。
#
# 「チャットからは承認できない」（HITL）は IAM で守る: 決定のキューに送れるのは Web の EC2 のロールだけで、Runtime のロールには
# sqs:SendMessage も Athena も付けない（reader_role_names には Runtime が入っているので、下の web_access は web_role_name にだけ付ける）。
# コードでも、agent/proposals.py の decide を呼ぶのは Web の承認タブだけで、チャットのツール（TOOL_SPECS）には decide が無い。

# ---------------------------------------------------------------- access for the chat runtime and the web EC2 (terraform/base/core roles)
# SSM は 2 つとも読む。Gateway を呼ぶのはチャットの Runtime だけ（agent/mcp_client.py。Web のコードは Gateway を呼ばない）。
# ロールごとに文書を分けるので、Runtime のポリシーは前と同じ中身のまま
data "aws_iam_policy_document" "reader_access" {
  for_each = local.reader_role_names

  statement {
    sid       = "Parameters"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.param_prefix}/*"]
  }

  dynamic "statement" {
    for_each = var.create_gateway && each.value == local.runtime_role_name ? [1] : []
    content {
      sid       = "Gateway"
      actions   = ["bedrock-agentcore:InvokeGateway"]
      resources = [aws_bedrockagentcore_gateway.tools[0].gateway_arn]
    }
  }
}

resource "aws_iam_role_policy" "reader_access" {
  for_each = local.reader_role_names

  name   = "${local.name_prefix}-workflow-access"
  role   = each.value
  policy = data.aws_iam_policy_document.reader_access[each.key].json
}

# ---------------------------------------------------------------- access for the web EC2 only (approve tab)
# 決定のキューへの送信（agent/proposals.py の decide）と、Athena での proposal_events の読み取り（list_proposals / get_proposal）
data "aws_iam_policy_document" "web_access" {
  statement {
    sid       = "DecisionQueue"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.decisions.arn]
  }

  dynamic "statement" {
    for_each = local.history_read_statements
    content {
      sid       = statement.value.sid
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
}

resource "aws_iam_role_policy" "web_access" {
  name   = "${local.name_prefix}-workflow-web"
  role   = local.web_role_name
  policy = data.aws_iam_policy_document.web_access.json
}

# Web の設定は SSM のパラメータで渡す（agent/proposals.py の toolkit.Param が <prefix>/<名前> を引く。Web の EC2 の環境変数は terraform/base/core が
# 決め、workflow の出力を知らないため。tools の Lambda は同じ名前の環境変数が先に効く）。SSM の値は空にできないので、空のものは作らない（読む側は空を「未配備」と読む）。
# キューの URL は apply まで決まらないので別の資源にする（for_each のキーに未確定の値を使えない）
resource "aws_ssm_parameter" "decision_queue_url" {
  name  = "${local.param_prefix}/decision-queue-url"
  type  = "String"
  value = aws_sqs_queue.decisions.url
}

resource "aws_ssm_parameter" "proposals_read" {
  for_each = { for k, v in {
    "athena-workgroup"      = local.athena_workgroup
    "athena-catalog"        = local.athena_catalog
    "history-namespace"     = local.athena_workgroup == "" ? "" : local.audit_namespace
    "proposal-events-table" = local.proposal_events_table_name
  } : k => v if v != "" }

  name  = "${local.param_prefix}/${each.key}"
  type  = "String"
  value = each.value
}
