# OSS 版（cycle 005「マネージドを OSS に置き換えた環境を作る」）のしるし。リソース名の接頭辞と Project タグが <owner>-nwc-oss になり、
# terraform/base/core の oss.tf（SG と通信の表、EFS）と terraform/base/ecr の OSS のリポジトリが有効になる。秘密は書かない
project = "nwc-oss"

# Kafbat UI は OSS 版の Kafka（kafka.tf）に PLAINTEXT で繋ぐ（認証なし。マネージド版の既定は MSK の IAM の SASL_SSL）
kafka_ui_security_protocol = "PLAINTEXT"
