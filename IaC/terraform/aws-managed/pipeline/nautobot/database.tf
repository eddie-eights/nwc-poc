# ---------------------------------------------------------------- PostgreSQL (RDS)
# Nautobot の DB。1 台・バックアップ無し・最後のスナップショット無しで、ops/down.sh で消える（Nautobot で編集した内容も一緒に消え、
# 次の ops/up.sh でまた lab の定義から入る）。VPC の中の nautobot の SG からしか届かない（terraform/base/core の security_groups.tf）。
# var.nautobot_db_az_num が 2 なら Multi-AZ（別の AZ に同期の控え）

resource "aws_db_subnet_group" "nautobot" {
  name       = "${local.name_prefix}-nautobot"
  subnet_ids = local.subnet_ids
}

# マスターユーザーのパスワードは ops/up.sh が SSM の SecureString に作ったもの。ephemeral で読んで write-only の引数に渡すので、
# 値は plan にも state にも残らない。タスクは同じパラメータを ECS の secrets で受ける（nautobot.tf）
ephemeral "aws_ssm_parameter" "db_password" {
  arn = local.secret_arns["db-password"]
}

resource "aws_db_instance" "nautobot" {
  identifier     = "${local.name_prefix}-nautobot"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name             = "nautobot"
  username            = "nautobot"
  password_wo         = ephemeral.aws_ssm_parameter.db_password.value
  password_wo_version = var.db_password_version

  db_subnet_group_name   = aws_db_subnet_group.nautobot.name
  vpc_security_group_ids = [local.nautobot_db_sg_id]
  publicly_accessible    = false

  # 2 = Multi-AZ の DB インスタンス（待機系 1 台。読めない）。3 AZ にはここではできない: 3 AZ は Multi-AZ DB クラスター
  # （書き込み 1 台 + 読める待機系 2 台）で、別のリソース（aws_rds_cluster）。しかも使えるクラスに db.t4g.micro が無い
  # （db.m6gd / r6gd などのローカル NVMe 付きだけ）。出典: Amazon RDS User Guide
  # 「Configuring and managing a Multi-AZ deployment for Amazon RDS」（https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html）と
  # 「Multi-AZ DB cluster deployments for Amazon RDS」（https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/multi-az-db-clusters-concepts.html）、2026-10-04 確認
  multi_az = var.nautobot_db_az_num == 2

  backup_retention_period    = 0
  skip_final_snapshot        = true
  delete_automated_backups   = true
  deletion_protection        = var.deletion_protection
  auto_minor_version_upgrade = false
  apply_immediately          = true
}
