# ---------------------------------------------------------------- SSM Agent (cycle 047)
# Web（web.tf）と lab（IaC/terraform/aws-managed/pipeline/lab の iam.tf）の EC2 を SSM の管理ノードにし、Run Command と Session Manager を受けるための許可。
# AWS のマネージドポリシー AmazonSSMManagedInstanceCore の v2（2026-10-11 に取得）の 3 つの Statement から、ssm:GetParameter と ssm:GetParameters
# （Resource * で同じアカウントのほかの環境のパラメータまで読める）だけを抜いた写し。ほかの Action は観測より広いが、ec2messages は CloudTrail に出ず、
# GetDocument などは Run Command・関連付け・インベントリで Agent が使うので、マネージドポリシーの字面どおり残す。
# パラメータは各ロールが自分で絞って許す（Web は web.tf の /<prefix>/*、lab は telegraf-address と telegraf-source-cidr の 2 つ）。
# 自前の写しなので AWS がマネージドポリシーを v3 にしても追従しない。管理ノードの登録が落ちたら（PingStatus が Online にならない）まずここを疑う。
# IaC/cloudformation/lab-debug.yaml の ssm-agent は同じ中身の写し（tests/test_lab_debug.py が一致を見る）。
# lab は remote state の ssm_agent_policy_arn（outputs.tf）で付ける。ops/down.sh は lab を先に消すので DeleteConflict にならない
resource "aws_iam_policy" "ssm_agent" {
  name        = "${local.name_prefix}-ssm-agent"
  description = "SSM Agent actions of the AWS managed policy for SSM managed instances (v2) without ssm:GetParameter and ssm:GetParameters (attached to the web and lab EC2 roles)"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SsmAgent"
        Effect = "Allow"
        Action = [
          "ssm:DescribeAssociation",
          "ssm:GetDeployablePatchSnapshotForInstance",
          "ssm:GetDocument",
          "ssm:DescribeDocument",
          "ssm:GetManifest",
          "ssm:ListAssociations",
          "ssm:ListInstanceAssociations",
          "ssm:PutInventory",
          "ssm:PutComplianceItems",
          "ssm:PutConfigurePackageResult",
          "ssm:UpdateAssociationStatus",
          "ssm:UpdateInstanceAssociationStatus",
          "ssm:UpdateInstanceInformation",
        ]
        Resource = "*"
      },
      {
        Sid    = "SessionChannels"
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel",
        ]
        Resource = "*"
      },
      {
        Sid    = "Ec2Messages"
        Effect = "Allow"
        Action = [
          "ec2messages:AcknowledgeMessage",
          "ec2messages:DeleteMessage",
          "ec2messages:FailMessage",
          "ec2messages:GetEndpoint",
          "ec2messages:GetMessages",
          "ec2messages:SendReply",
        ]
        Resource = "*"
      },
    ]
  })
}
