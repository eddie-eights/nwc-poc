# ---------------------------------------------------------------- S3 Tables (Iceberg)
# テーブルバケット名はハイフン可、namespace とテーブル名はアンダースコアだけ（S3 Tables の命名規則、2026-09-17 確認）。
# テーブルは Terraform で作る（terraform destroy でバケットまで消せるように。テーブルが残るとバケットは消えない）。
# 列は Telegraf の JSON（{"fields":{…},"name":"…","tags":{…},"timestamp":秒}）をそのまま持つ。
# tags と fields は JSON 文字列のまま入れる（機器やメトリクスが増えても列を変えないため。列の型は Iceberg のプリミティブだけ）
# テーブルバケットと namespace はいつも作る（証跡の proposal_events が入る。2026-09-24）。
# 生データの raw_telemetry だけは var.sinks に iceberg があるときだけ作る（deploy.env の STORES の s3）
# raw_telemetry の一意の番号と Kafka の位置の列（event_id / kafka_topic / kafka_partition / kafka_offset。2026-10-04）はここに書かない:
# aws_s3tables_table は metadata の schema を変えるとテーブルを作り直す（RequiresReplace）ので、いまある行が消える。
# 列は Spark の iceberg のジョブが起動時に ALTER TABLE ADD COLUMNS で後ろに足す（app/spark/snmp_sinks.py の ICEBERG_ADDED_COLUMNS）。
# provider はメタデータを読み直さないので、足した列で plan に差分は出ない
resource "aws_s3tables_table_bucket" "tables" {
  name = local.table_bucket
}

# この VPC のエンドポイントを通らない呼び出しを拒む（IaC/terraform/aws-managed/base/core の perimeter.tf の資源側）。
# Iceberg REST の中の呼び出しは元の VPC を引き継がないので aws:CalledViaLast = s3tables.amazonaws.com を外す。
# 表の保守（compaction など）は S3 Tables 自身が出すので aws:PrincipalIsAWSService で外れる。
# ポリシーの読み書きは外す（デプロイする人が変わって締め出されても、その人が delete-table-bucket-policy で戻せる）
resource "aws_s3tables_table_bucket_policy" "tables" {
  count = local.perimeter_policy_arn != "" ? 1 : 0

  table_bucket_arn = aws_s3tables_table_bucket.tables.arn
  resource_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyOutsideVpc"
      Effect    = "Deny"
      Principal = "*"
      NotAction = ["s3tables:GetTableBucketPolicy", "s3tables:PutTableBucketPolicy", "s3tables:DeleteTableBucketPolicy"]
      Resource  = [aws_s3tables_table_bucket.tables.arn, "${aws_s3tables_table_bucket.tables.arn}/*"]
      Condition = {
        StringNotEqualsIfExists = { "aws:SourceVpc" = local.vpc_id, "aws:CalledViaLast" = "s3tables.amazonaws.com" }
        BoolIfExists            = { "aws:ViaAWSService" = "false" }
        Bool                    = { "aws:PrincipalIsAWSService" = "false" }
        ArnNotLike              = { "aws:PrincipalArn" = local.perimeter_exempt_principals }
      }
    }]
  })
}

resource "aws_s3tables_namespace" "netops" {
  namespace        = var.namespace
  table_bucket_arn = aws_s3tables_table_bucket.tables.arn
}

# 2026-09-24 まではバケットと namespace も iceberg のときだけ（count）だった。state の [0] をそのまま引き継ぐ
moved {
  from = aws_s3tables_table_bucket.tables[0]
  to   = aws_s3tables_table_bucket.tables
}

moved {
  from = aws_s3tables_namespace.netops[0]
  to   = aws_s3tables_namespace.netops
}

# 2026-10-04 に snmp_metrics から改名した（全トピックが入るので、SNMP のメトリクスだけに見えない名前に。いまは metrics / gnmi / traps / logs / flows。mdt は cycle 012 で外した）。
# moved で state のアドレスを引き継ぐ。テーブルの名前（var.table_name）も変わるので、古いテーブルを残したまま apply すると
# 作り直し（中身は消える）になるかもしれない（AWS では未確認。この PoC はその日に消すので構わない）
moved {
  from = aws_s3tables_table.snmp_metrics
  to   = aws_s3tables_table.raw_telemetry
}

resource "aws_s3tables_table" "raw_telemetry" {
  count = local.sink_iceberg ? 1 : 0

  name             = var.table_name
  namespace        = aws_s3tables_namespace.netops.namespace
  table_bucket_arn = aws_s3tables_table_bucket.tables.arn
  format           = "ICEBERG"

  metadata {
    iceberg {
      schema {
        field {
          name     = "ts"
          type     = "timestamp"
          required = true
        }
        field {
          name     = "topic"
          type     = "string"
          required = true
        }
        field {
          name     = "measurement"
          type     = "string"
          required = false
        }
        field {
          name     = "agent_host"
          type     = "string"
          required = false
        }
        field {
          name     = "host"
          type     = "string"
          required = false
        }
        field {
          name     = "tags_json"
          type     = "string"
          required = false
        }
        field {
          name     = "fields_json"
          type     = "string"
          required = false
        }
        field {
          name     = "ingested_at"
          type     = "timestamp"
          required = true
        }
      }
    }
  }
}

# ---------------------------------------------------------------- 証跡（2026-09-24）
# 異常が開いた・閉じたの履歴（anomaly_events）は 2026-10-02 にやめた（書いていた Spark の detect をなくした）。
# 障害の履歴は下の alert_events（アラートの通知の履歴。2026-10-04）に置く。
# 修復案の置き場はこのテーブルだけ（Neptune の頂点 proposal は 2026-10-05 にやめた）。作成・承認・却下・適用・確認を 1 行ずつ足し、どの行も修復案の全項目を持つ。
# 書くのは IaC/terraform/aws-managed/workflow の worker だけ（app/temporal/awsio.py の append_proposal_events、PyIceberg）。読むのは Web の承認タブとエージェントの list_proposals（Athena）。
# 修復案の「いま」は proposal_id ごとに seq が最大の行。event は created / approved / rejected / expired / obsolete / applied / failed / verified / ignored
# （ignored は効いた決定のあとに届いた中身の違う決定。status は直前の行のまま）。
# event_id = <proposal_id>#<event>（ignored だけは <proposal_id>#ignored#<届いた決定の種類>#<届いた決定の時刻>#<名前>）。列は app/temporal/rules.py の PROPOSAL_EVENT_COLUMNS と同じ順・同じ型。
# schema を変えるとテーブルは作り直しになり、いまある行は消える（上の raw_telemetry の注記と同じ RequiresReplace。
# 2026-10-05 に 12 列から 28 列にした。12 列の state に plan を打つと must be replaced になることは、AWS に触らずに確かめた
# （provider 6.64.0、偽の鍵と -refresh=false。docs/cycles/003-proposals-in-s3tables/review.md の Round 1）。
# 2026-10-05 に AWS で 28 列のテーブルを新しく作り、worker が created → approved → applied → verified の行を書き、Web の承認タブが Athena で読めた。
# 12 列のテーブルが残っている state からの作り直しは、AWS では見ていない（未確認））
resource "aws_s3tables_table" "proposal_events" {
  name             = "proposal_events"
  namespace        = aws_s3tables_namespace.netops.namespace
  table_bucket_arn = aws_s3tables_table_bucket.tables.arn
  format           = "ICEBERG"

  metadata {
    iceberg {
      schema {
        field {
          name     = "event_id"
          type     = "string"
          required = false
        }
        field {
          name     = "proposal_id"
          type     = "string"
          required = false
        }
        field {
          name     = "anomaly_id"
          type     = "string"
          required = false
        }
        field {
          name     = "seq"
          type     = "int"
          required = false
        }
        field {
          name     = "event"
          type     = "string"
          required = false
        }
        field {
          name     = "status"
          type     = "string"
          required = false
        }
        field {
          name     = "device_id"
          type     = "string"
          required = false
        }
        field {
          name     = "kind"
          type     = "string"
          required = false
        }
        field {
          name     = "target"
          type     = "string"
          required = false
        }
        field {
          name     = "first_seen"
          type     = "timestamptz"
          required = false
        }
        field {
          name     = "source"
          type     = "string"
          required = false
        }
        field {
          name     = "alert_detail"
          type     = "string"
          required = false
        }
        field {
          name     = "cause"
          type     = "string"
          required = false
        }
        field {
          name     = "action"
          type     = "string"
          required = false
        }
        field {
          name     = "command"
          type     = "string"
          required = false
        }
        field {
          name     = "reason"
          type     = "string"
          required = false
        }
        field {
          name     = "agent_response"
          type     = "string"
          required = false
        }
        field {
          name     = "precheck"
          type     = "string"
          required = false
        }
        field {
          name     = "precheck_verdict"
          type     = "string"
          required = false
        }
        field {
          name     = "decided_by"
          type     = "string"
          required = false
        }
        field {
          name     = "decided_at"
          type     = "timestamptz"
          required = false
        }
        field {
          name     = "apply_output"
          type     = "string"
          required = false
        }
        field {
          name     = "verify_note"
          type     = "string"
          required = false
        }
        field {
          name     = "detail"
          type     = "string"
          required = false
        }
        field {
          name     = "workflow_id"
          type     = "string"
          required = false
        }
        field {
          name     = "run_id"
          type     = "string"
          required = false
        }
        field {
          name     = "created_at"
          type     = "timestamptz"
          required = false
        }
        field {
          name     = "event_time"
          type     = "timestamptz"
          required = false
        }
      }
    }
  }
}

# ---------------------------------------------------------------- アラートの通知の履歴（2026-10-04）
# Grafana / Splunk のアラートの通知（SNS の <接頭辞>-alerts）を 1 件 1 行で持つ。書くのは IaC/terraform/aws-managed/pipeline/graph の status の Lambda
# （app/graph/status_handler.py）で、history.tf の Firehose がこのテーブルに追記する。読むのはエージェントの query_history（Athena）。
# 重複（Grafana の 4 時間ごとの送り直し、Grafana と Splunk の両方、Lambda の再試行）はそのまま入る。読む側が event_id で落とす。
# event_id = <anomaly_id>#<source>#<status>#<starts_at の epoch 秒>。starts_at は Grafana では発火した時刻（resolved も同じ）、
# Splunk ではその状態の最後の時刻（resolved では解消した時刻）。列は app/temporal/rules.py の ALERT_EVENT_COLUMNS と同じ順・同じ型
resource "aws_s3tables_table" "alert_events" {
  name             = "alert_events"
  namespace        = aws_s3tables_namespace.netops.namespace
  table_bucket_arn = aws_s3tables_table_bucket.tables.arn
  format           = "ICEBERG"

  metadata {
    iceberg {
      schema {
        field {
          name     = "event_id"
          type     = "string"
          required = false
        }
        field {
          name     = "anomaly_id"
          type     = "string"
          required = false
        }
        field {
          name     = "source"
          type     = "string"
          required = false
        }
        field {
          name     = "status"
          type     = "string"
          required = false
        }
        field {
          name     = "device_id"
          type     = "string"
          required = false
        }
        field {
          name     = "kind"
          type     = "string"
          required = false
        }
        field {
          name     = "target"
          type     = "string"
          required = false
        }
        field {
          name     = "detail"
          type     = "string"
          required = false
        }
        field {
          name     = "starts_at"
          type     = "timestamptz"
          required = false
        }
        field {
          name     = "received_at"
          type     = "timestamptz"
          required = false
        }
      }
    }
  }
}
