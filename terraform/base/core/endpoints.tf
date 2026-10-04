# ---------------------------------------------------------------- VPC endpoints
# AWS の API へは全部 VPC エンドポイントから行く。VPC にはほかに出口が無い（NAT Gateway も IGW も無い）。
# 経路を VPC エンドポイントに寄せると、リクエストに aws:SourceVpc（この VPC）が付くので、perimeter.tf の拒否（IAM とリソースポリシー）で
# 「この VPC の外からの呼び出し」を止められる。NAT から出た呼び出しには aws:SourceVpc が付かない（2026-09-28 ユーザー決定: 閉域で高セキュアに。同日、NAT もやめた）。
# エンドポイントが無いサービスは、接続のタイムアウトになる（docs/troubleshooting.md）。
#
# S3 の gateway エンドポイントは無料（S3 / S3 Tables のデータ / ECR のレイヤー / AL2023 の dnf リポジトリ）。
# S3 のエンドポイントポリシーは付けない: dnf のリポジトリは匿名の GET で、ECR のレイヤーは ECR が署名した URL なので、
# aws:PrincipalAccount やバケットで絞ると止まる。ポリシーで絞っていた頃は 2026-09-15（ListBucket）と 2026-09-17（S3 Tables の metadata.json）に止まった。
# バケットの側（bucket.tf、terraform/pipeline/analytics の S3 Tables）で aws:SourceVpc を要求する
resource "aws_vpc_endpoint" "s3" {
  count = var.create_s3_gateway_endpoint ? 1 : 0

  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = { Name = "${local.name_prefix}-s3" }
}

# OpenSearch Serverless のコレクション（terraform/agent の KB、terraform/pipeline/analytics の logs）はどちらも公開せず、
# ネットワークポリシーの SourceVPCEs にこのエンドポイントだけを書く。VPC の外（公開の経路）から届かせると公開扱いになり、
# ネットワークポリシーには IP の許可リストが無いので、VPC の中から閉じて届く経路はこれしかない。
# 1 つの VPC に 1 本あれば全コレクションに届き（AWS の文書「You only need one OpenSearch Serverless VPC endpoint in a VPC」）、
# 作ると AOSS が *.<region>.aoss.amazonaws.com の private hosted zone を VPC に付ける。2 本目を作らないよう、ここに 1 本だけ置く。
# ops/up.sh は CREATE_KB か STORES の grafana（OpenSearch）のとき create_opensearch_endpoint=true で apply する。
# ENI はインターフェース型と同じ endpoints_az_num の AZ（1 本 1.4 セント/h × AZ。2026-10-04 までは 2 AZ 固定）。
# SG は endpoints（ワークロードの SG からの 443 だけ。security_groups.tf）
resource "aws_opensearchserverless_vpc_endpoint" "aoss" {
  count = var.create_opensearch_endpoint ? 1 : 0

  name               = "${local.name_prefix}-aoss"
  vpc_id             = aws_vpc.this.id
  subnet_ids         = local.endpoint_subnet_ids
  security_group_ids = [aws_security_group.endpoints.id]
}

# インターフェース型エンドポイント。どれを作るかは ops/up.sh が機能から決めて var.interface_endpoints で渡す
# （土台は ssm / ssmmessages: Web の EC2 の SSM Agent とポートフォワーディング、SSM パラメータ）。
# 同じサービスの private DNS 付きエンドポイントは 1 つの VPC に 1 本しか作れないので、ルートごとに持たずここに集める
# （2026-09-26 まではルートごとに持っていた。7c42b0f）。
# エンドポイントポリシーは「このアカウントのプリンシパルだけ」: 盗んだ他のアカウントの鍵でこの VPC から外へ持ち出す経路を塞ぐ。
# 1 本 1.4 セント/h × AZ。endpoints_az_num の数だけサブネット a / b / c の先頭から置く（既定 1 はサブネット a だけ。
# b / c のワークロードも private DNS で a の ENI に届くが、a の AZ が落ちると AWS の API に届かなくなる）。
# ENI は指定したサブネットごとに 1 つ（1 AZ に 1 サブネットまで）。1 AZ だけに置くと他の AZ からもその AZ の ENI に解決され、
# その AZ が傷むと届かなくなる。本番は 2 AZ 以上が AWS の勧め。出典: AWS PrivateLink Guide
# 「Access AWS services through AWS PrivateLink」の Subnets and Availability Zones
# （https://docs.aws.amazon.com/vpc/latest/privatelink/privatelink-access-aws-services.html）と
# 「Access an AWS service using an interface VPC endpoint」（https://docs.aws.amazon.com/vpc/latest/privatelink/create-interface-endpoint.html）、
# 2026-10-04 確認。AZ が落ちたときの振る舞いは AWS で未確認。
# ENDPOINTS_AZ_NUM を他の <リソース>_AZ_NUM より小さくすると、ops/up.sh が警告（AZ_NUM_OVER）を出す（止めはしない）
locals {
  endpoint_subnet_ids = slice(local.subnet_ids, 0, var.endpoints_az_num)
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(var.interface_endpoints)

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = local.endpoint_subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "OwnAccountOnly"
      Effect    = "Allow"
      Principal = "*"
      Action    = "*"
      Resource  = "*"
      Condition = {
        StringEquals = { "aws:PrincipalAccount" = local.account_id }
      }
    }]
  })

  tags = { Name = "${local.name_prefix}-${replace(each.key, ".", "-")}" }
}
