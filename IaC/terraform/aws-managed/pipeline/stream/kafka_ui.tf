# ---------------------------------------------------------------- Kafbat UI（Web の EC2 の Docker）
# MSK には Kafka の中（トピック・パーティション・メッセージ・consumer group）を見る画面が無いので、Kafbat UI（OSS の Kafka の Web コンソール）を
# 立てる（2026-10-05 のユーザー決定）。見るだけにはしない（READONLY を付けない）: 画面からトピックの追加・設定の変更・削除と、メッセージの送信ができる。
# 2026-10-08 のユーザー決定（cycle 010）で ECS のタスクと Cloud Map をやめ、Web の EC2（IaC/terraform/aws-managed/base/core の web.tf）の
# Docker で動かす。コンテナを起こすのは Web の EC2 の systemd ユニット（user_data の templates/web_user_data.sh.tftpl）で、このファイルは
# その接続先を SSM の String のパラメータに書き、Web の EC2 のロールに Kafka の権限を足すだけ。base/core（EC2）が先、stream（接続先）が後の順で作るので、
# EC2 の初回の起動ではパラメータがまだ無く、ユニットは 1 回で止まる（終了コード 75。RestartPreventExitStatus で起こし直さない。cycle 014）。
# stream の apply のあと ops/up.sh の手順 8-3（OSS 版は 7-5）の Web の restart が、Web のユニットの Wants= で起こす。
# 無いのではなく読めない（AccessDenied・SSM に届かない など）ときは 69 で終わり、30 秒ごとに起こし直す。
# イメージは ghcr.io/kafbat/kafka-ui を ECR に写したもの（ops/up.sh の手順 2。AWS の外へ出る経路が無いので ghcr.io から直接は引けない）。
# MSK へは IAM 認証（var.kafka_ui_security_protocol = SASL_SSL。aws-msk-iam-auth は api.jar に入っている）で、認証情報は既定の認証情報チェーンの
# インスタンスロール（IMDSv2。Docker の bridge 越しなので web.tf の hop limit は 2。awsRoleArn は使わないので STS も要らない）。
# PLAINTEXT は認証の無い Kafka（cycle 005 の OSS 版）向けで、この root の MSK は受け付けない。
# 開き方は output kafka_ui_port_forward_command（Web の EC2 の 127.0.0.1:8082 への SSM のポートフォワード）。画面はログインフォーム（AUTH_TYPE=LOGIN_FORM。
# AUTH_TYPE が無いと誰でも開ける。2026-10-05 に手元の Docker で確認）で、admin のパスワードは ops/up.sh が作る SSM の SecureString
# （output kafka_ui_password_command）。設定は環境変数だけ（DYNAMIC_CONFIG_ENABLED は既定の false）で、画面からクラスターの設定は変えられない。
# OSS 版（cycle 005。IaC/terraform/oss/pipeline/stream）はこのファイルをシンボリックリンクで使い、ECS の Kafka（kafka.tf）に PLAINTEXT で繋ぐ（oss.auto.tfvars）。
# Kafka による違い（ブートストラップ・ロールの権限）は msk.tf / kafka.tf の kafka_* の locals

locals {
  kafka_ui_image              = "${try(data.terraform_remote_state.ecr.outputs.kafka_ui_repository_url, "")}:${var.kafka_ui_image_tag}"
  kafka_ui_parameter_prefix   = "/${local.name_prefix}/kafka-ui"
  kafka_ui_password_parameter = "${local.kafka_ui_parameter_prefix}/admin-password"

  # ブートストラップは Kafka の側（msk.tf / OSS 版の kafka.tf）がプロトコルごとに渡す
  # （MSK は SASL_SSL = IAM のポート 9098、OSS 版の Kafka は PLAINTEXT = 9092）。SASL の環境変数は Web の EC2 のスクリプトがプロトコルを見て足す
  # （Kafbat UI の文書「AWS IAM」https://ui.docs.kafbat.io/configuration/authentication/for-kafka/aws-iam 、2026-10-05 確認）
  kafka_ui_bootstrap_servers = lookup(local.kafka_bootstrap_by_protocol, var.kafka_ui_security_protocol, "")
}

# Web の EC2 の /usr/local/bin/<接頭辞>-kafka-ui が読む（名前は templates/web_user_data.sh.tftpl と同じにする）
resource "aws_ssm_parameter" "kafka_ui_image" {
  name        = "${local.kafka_ui_parameter_prefix}/image"
  type        = "String"
  value       = local.kafka_ui_image
  description = "Kafbat UI image in ECR (ghcr.io/kafbat/kafka-ui mirrored by ops/up.sh). Read by the Kafbat UI unit of the web EC2."

  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.ecr.outputs.kafka_ui_repository_url, "") != ""
      error_message = "IaC/terraform/aws-managed/base/ecr の state から kafka_ui_repository_url が読めない（2026-10-05 より前の ECR）。IaC/terraform/aws-managed/base/ecr を先に apply する（ops/up.sh の手順 1）。"
    }
  }
}

resource "aws_ssm_parameter" "kafka_ui_bootstrap_servers" {
  name        = "${local.kafka_ui_parameter_prefix}/bootstrap-servers"
  type        = "String"
  value       = local.kafka_ui_bootstrap_servers
  description = "Kafka bootstrap servers of Kafbat UI for kafka_ui_security_protocol. Read by the Kafbat UI unit of the web EC2."

  lifecycle {
    precondition {
      condition     = local.kafka_ui_bootstrap_servers != ""
      error_message = "Kafka に kafka_ui_security_protocol（${var.kafka_ui_security_protocol}）のブートストラップが無い。マネージド版の MSK は IAM（SASL_SSL）だけ、OSS 版の Kafka は PLAINTEXT だけを受け付ける。"
    }
  }
}

resource "aws_ssm_parameter" "kafka_ui_security_protocol" {
  name        = "${local.kafka_ui_parameter_prefix}/security-protocol"
  type        = "String"
  value       = var.kafka_ui_security_protocol
  description = "SASL_SSL (MSK IAM with the web EC2 role) or PLAINTEXT. Read by the Kafbat UI unit of the web EC2."
}

# 権限は Kafka の側: msk.tf の kafka_ui_kafka_statements（Kafbat UI が使う MSK の操作だけ）/ OSS 版の kafka.tf（認証なしなので MSK を拒む Deny だけ。
# OSS 版の Web は Kafka の権限を使わないので害は無い）。stream から Web のロールにポリシーを足すのは access.tf と同じ形
resource "aws_iam_role_policy" "kafka_ui_web" {
  name = "${local.name_prefix}-kafka-ui"
  role = local.web_role_name

  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = local.kafka_ui_kafka_statements
  })
}
