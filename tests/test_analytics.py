"""terraform/pipeline/analytics と spark/snmp_sinks.py の模擬テスト（AWS に触れない）。
terraform/pipeline/analytics が main と stream の state を読み、S3 Tables のテーブルと EMR Serverless と格納先（sinks）を作ること、
Spark のスクリプトが Kafka（MSK の IAM 認証）を格納先ごとに読んで Iceberg / OpenSearch Serverless / Prometheus に流すこと、
テーブルの列がスクリプトと一致すること、remote write の protobuf と snappy が手で復号できることを見る。
実行は python3 tests/test_analytics.py（依存は無い。pyspark も botocore も要らない。スクリプトは import するが pyspark は関数の中で読む）。"""
import ast, importlib.util, inspect, io, json, os, re, ssl, struct, sys, zlib

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "spark", "snmp_sinks.py")
TF_DIR = os.path.join(ROOT, "terraform", "pipeline", "analytics")
UP = os.path.join(ROOT, "ops", "up.sh")
DOWN = os.path.join(ROOT, "ops", "down.sh")
CHECK = os.path.join(ROOT, "ops", "check.sh")
ENV_EXAMPLE = os.path.join(ROOT, "deploy.env.example")

passed = 0
def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)

# terraform/pipeline/analytics は関心ごとにファイルが分かれているので、ルートの .tf を全部つないで見る
tf = ""
tf_files = sorted(n for n in os.listdir(TF_DIR) if n.endswith(".tf"))
for name in tf_files:
    with open(os.path.join(TF_DIR, name), encoding="utf-8") as f:
        tf += f.read() + "\n"
with open(SRC, encoding="utf-8") as f:
    src = f.read()
with open(UP, encoding="utf-8") as f:
    up = f.read()
with open(DOWN, encoding="utf-8") as f:
    down = f.read()
# up.sh / down.sh は、読んでいる共通の関数（ops/common.sh と ops/up-common.sh / down-common.sh。OSS 版の oss/ops/ と共通）とつないで見る
def _ops_common(kind):
    return "".join(open(os.path.join(ROOT, "ops", n), encoding="utf-8").read() for n in ("common.sh", f"{kind}-common.sh"))
up = _ops_common("up") + up
down = _ops_common("down") + down
with open(CHECK, encoding="utf-8") as f:
    checksh = f.read()
with open(ENV_EXAMPLE, encoding="utf-8") as f:
    env_example = f.read()

# ---- 他のルートとのつながり
check("ファイルは versions / providers / variables / locals / network / tables / emr / sinks / access / outputs / ecs / grafana / splunk / history",
      set(tf_files) == {"versions.tf", "providers.tf", "variables.tf", "locals.tf", "network.tf", "tables.tf", "emr.tf", "sinks.tf", "access.tf", "outputs.tf",
                        "ecs.tf", "grafana.tf", "splunk.tf", "history.tf"})
check("main の state をローカルから読む", re.search(r'data "terraform_remote_state" "main"[\s\S]*?backend\s*=\s*"local"', tf, re.S) is not None
      and '"${path.module}/../../base/core/terraform.tfstate"' in tf)
check("stream の state をローカルから読む", re.search(r'data "terraform_remote_state" "stream"[\s\S]*?backend\s*=\s*"local"', tf, re.S) is not None
      and '"${path.module}/../stream/terraform.tfstate"' in tf)
for out in ("vpc_id", "subnet_ids", "security_group_ids", "opensearch_vpc_endpoint_id", "kb_bucket_name"):
    check(f"main の output {out} を使う", f"data.terraform_remote_state.main.outputs.{out}" in tf)
for out in ("msk_cluster_arn", "bootstrap_brokers"):
    check(f"stream の output {out} を try で読む（無ければ precondition で止める）",
          re.search(r'try\(data\.terraform_remote_state\.stream\.outputs\.' + out + r',\s*""\)', tf) is not None)
# 2026-10-02: Spark は検知をやめたので、analytics は graph（Neptune）を読まない。検知は Grafana と Splunk で、土台の SNS のトピックへ publish する
check("graph の state は読まない（Spark は Neptune に書かない。SKIP_GRAPH=1 でも analytics は作れる）",
      'terraform_remote_state" "graph"' not in tf and not any(w in tf for w in ("neptune_host", "neptune_endpoint", "neptune_resource_id", "neptune-db")))
check("アラートのトピックの ARN は main の state から try で読む（古い土台では空）",
      'alerts_topic_arn = try(data.terraform_remote_state.main.outputs.alerts_topic_arn, "")' in tf)
check("stream が無いときは「terraform/pipeline/stream を先に apply する」と出る",
      re.search(r'precondition\s*\{[\s\S]*?msk_cluster_arn\s*!=\s*""[\s\S]*?terraform/pipeline/stream を先に apply する', tf, re.S) is not None)
# main / stream の .tf に本当にその output があるか（stream の msk_cluster_arn は MSK だけのものなので msk.tf にある。cycle 005）
for root, outs in (("base/core", ("vpc_id", "subnet_ids", "security_group_ids", "opensearch_vpc_endpoint_id", "kb_bucket_name", "alerts_topic_arn")),
                   ("pipeline/stream", ("msk_cluster_arn", "bootstrap_brokers"))):
    _dir = os.path.join(ROOT, "terraform", root)
    other = ""
    for name in sorted(n for n in os.listdir(_dir) if n.endswith(".tf")):
        with open(os.path.join(_dir, name), encoding="utf-8") as f:
            other += f.read() + "\n"
    for out in outs:
        check(f"terraform/{root} に output {out} がある", re.search(r'^output "' + out + r'"', other, re.M) is not None)

# ---- ネットワーク（SG はワークロードごとに土台の security_groups.tf にあり、ルールはそこの通信の表から作る。2026-09-29。
#      AWS の API は土台のインターフェース型エンドポイントを通し、NAT Gateway は無い）
_core = "".join(open(os.path.join(ROOT, "terraform", "base", "core", n), encoding="utf-8").read() for n in sorted(os.listdir(os.path.join(ROOT, "terraform", "base", "core"))) if n.endswith(".tf"))
_sg_tf = open(os.path.join(ROOT, "terraform", "base", "core", "security_groups.tf"), encoding="utf-8").read()
check("analytics は SG も SG のルールもインターフェース型エンドポイントも作らない（S3 Tables / events / aps / logs の API は土台のエンドポイントを通る）",
      'resource "aws_security_group"' not in tf and 'resource "aws_vpc_endpoint"' not in tf and "aws_vpc_security_group_" not in tf
      and not any(k in tf for k in ("msk_sg_id", "neptune_sg_id", "emr_self", "msk_from_emr", "endpoints_from_emr")))
check("EMR / Grafana / Splunk は土台の spark / grafana / splunk の SG を使う",
      re.search(r'network_configuration\s*\{[\s\S]*?security_group_ids\s*=\s*\[local\.spark_sg_id\]', tf, re.S) is not None
      and "security_groups  = [local.grafana_sg_id]" in tf and "security_groups  = [local.splunk_sg_id]" in tf
      and all(re.search(k + r'_sg_id\s*=\s*try\(data\.terraform_remote_state\.main\.outputs\.security_group_ids\["' + k + r'"\], ""\)', tf) for k in ("spark", "grafana", "splunk")))

_sg_keys = re.search(r'security_groups = \{(.*?)\n  \}', _sg_tf, re.S)
_sg_keys = set(re.findall(r'^\s+(\w+)\s+=\s+"', _sg_keys.group(1), re.M)) if _sg_keys else set()
SG_KEYS = {"web", "lab", "telegraf_dialout", "telegraf_dialin", "telegraf_dialout_nlb", "msk", "spark", "grafana", "splunk", "nautobot", "nautobot_db", "lambda", "workflow", "runtime", "kafka_ui"}
check(f"土台の SG はワークロードごとの 15 個と endpoints（{sorted(_sg_keys)}）",
      _sg_keys == SG_KEYS and re.findall(r'resource "aws_security_group" "(\w+)"', _core) == ["workload", "endpoints"]
      and re.search(r'resource "aws_security_group" "workload" \{\n\s*for_each = local\.workload_security_groups\n', _sg_tf) is not None)
# 通信の表を読む（from = sg の行は aws_api_clients に展開する）
_clients = re.search(r'aws_api_clients = \[([^\]]*)\]', _sg_tf)
_clients = re.findall(r'"(\w+)"', _clients.group(1)) if _clients else []
_flows = set()
for _m in re.finditer(r'\{ from = ("?\w+"?), to = "(\w+)", protocol = "(\w+)", port = (\d+)(?:, to_port = (\d+))?(?:, only = "(\w+)")?, why = "([^"]*)" \}', _sg_tf):
    for _from in (_clients if _m.group(1) == "sg" else [_m.group(1).strip('"')]):
        _flows.add((_from, _m.group(2), _m.group(3), int(_m.group(4)), int(_m.group(5) or _m.group(4)), _m.group(6) or ""))
EXPECTED_FLOWS = {(c, t, "tcp", 443, 443, "") for c in ("web", "lab", "telegraf_dialout", "telegraf_dialin", "spark", "grafana", "splunk", "nautobot", "lambda", "workflow", "runtime", "kafka_ui") for t in ("endpoints", "s3")} | {
    # Nautobot（terraform/pipeline/nautobot。2026-10-04）: 画面は Web の EC2 からのポートフォワード、DB は RDS。
    # Neptune は Neptune Analytics にしたので SG が無く、行も無い（neptune-graph-data のエンドポイントの 443 で届く。2026-10-04）
    ("web", "nautobot", "tcp", 8080, 8080, ""), ("nautobot", "nautobot_db", "tcp", 5432, 5432, ""),
    ("web", "grafana", "tcp", 3000, 3000, ""), ("web", "splunk", "tcp", 8000, 8000, ""), ("web", "workflow", "tcp", 8233, 8233, ""),
    ("telegraf_dialout", "msk", "tcp", 9098, 9098, ""), ("telegraf_dialin", "msk", "tcp", 9098, 9098, ""), ("spark", "msk", "tcp", 9098, 9098, ""), ("msk", "msk", "tcp", 9092, 9098, ""),
    # Kafbat UI（terraform/pipeline/stream。2026-10-05）: 画面は Web の EC2 からのポートフォワード、MSK へは IAM の 9098
    ("web", "kafka_ui", "tcp", 8080, 8080, ""), ("kafka_ui", "msk", "tcp", 9098, 9098, ""),
    ("spark", "spark", "tcp", 0, 65535, ""), ("spark", "splunk", "tcp", 8088, 8088, ""),
    # Splunk のクラスター（「Splunk をクラスターにする（004）」）: manager・indexer・search head の間だけ
    ("splunk", "splunk", "tcp", 8089, 8089, ""), ("splunk", "splunk", "tcp", 9887, 9887, ""), ("splunk", "splunk", "tcp", 9997, 9997, ""),
    ("telegraf_dialout_nlb", "telegraf_dialout", "udp", 1162, 1162, ""), ("telegraf_dialout_nlb", "telegraf_dialout", "udp", 5140, 5140, ""), ("telegraf_dialout_nlb", "telegraf_dialout", "tcp", 57000, 57000, ""), ("telegraf_dialout_nlb", "telegraf_dialout", "tcp", 8080, 8080, ""),
    ("lab_mgmt", "telegraf_dialout_nlb", "udp", 162, 162, ""), ("lab_mgmt", "telegraf_dialout_nlb", "udp", 5140, 5140, ""),
    ("lab", "telegraf_dialout_nlb", "udp", 162, 162, "egress"), ("lab", "telegraf_dialout_nlb", "udp", 5140, 5140, "egress"),
    # ポーリングと gNMI は取りにいく側（telegraf_dialin）だけ。受ける側（telegraf_dialout）は機器へ出ない（2026-10-04 に分けた）
    ("telegraf_dialin", "lab_mgmt", "udp", 161, 161, ""), ("telegraf_dialin", "lab_mgmt", "tcp", 57400, 57400, ""),
    ("telegraf_dialin", "lab", "udp", 161, 161, "ingress"), ("telegraf_dialin", "lab", "tcp", 57400, 57400, "ingress"),
}
check(f"通信の表は決めた流れだけ（多い: {sorted(_flows - EXPECTED_FLOWS)} 足りない: {sorted(EXPECTED_FLOWS - _flows)}）",
      _flows == EXPECTED_FLOWS and _sg_tf.count("{ from = ") == len(EXPECTED_FLOWS) - 2 * len(_clients) + 2 + 1)
_core_vars = open(os.path.join(ROOT, "terraform", "base", "core", "variables.tf"), encoding="utf-8").read()
check("MDT の送り元は変数 mdt_source_cidrs の CIDR から NLB の 57000/tcp だけ（受信だけ。既定は空、0.0.0.0/0 と重複とネットワークアドレスでない書き方を拒む）",
      re.search(r'\[for c in var\.mdt_source_cidrs :\s*\{ from = "cidr:\$\{c\}", cidr = c, to = "telegraf_dialout_nlb", protocol = "tcp", port = 57000, why = "[^"]*" \}\s*\]', _sg_tf) is not None
      and "cidr     = try(f.cidr, null)" in _sg_tf
      and re.search(r'variable "mdt_source_cidrs" \{[\s\S]*?type\s*=\s*list\(string\)\s*default\s*=\s*\[\][\s\S]*?cidrsubnet\(c, 0, 0\) == c[\s\S]*?length\(distinct\(var\.mdt_source_cidrs\)\) == length\(var\.mdt_source_cidrs\)'
                    r'[\s\S]*?!contains\(var\.mdt_source_cidrs, "0\.0\.0\.0/0"\)', _core_vars) is not None
      and 'MAIN_VARS+=(-var "mdt_source_cidrs=' in open(os.path.join(ROOT, "ops", "up.sh"), encoding="utf-8").read()
      and "MDT_SOURCE_CIDRS" in open(os.path.join(ROOT, "ops", "deploy-env.sh"), encoding="utf-8").read())
check("Temporal の gRPC 7233（workflow）と Splunk の管理 API 8089（splunk。クラスターの splunk どうしは除く）は開けず、CIDR のルールは lab の管理ネットワーク・MDT の送り元・endpoints の送信なしだけ（EMR Serverless は 0.0.0.0/0 の inbound を拒否する）",
      not any(t == "workflow" and p <= 7233 <= q or t == "splunk" and f != "splunk" and p <= 8089 <= q for f, t, _, p, q, _ in _flows)
      and sorted(re.findall(r'cidr_ipv4\s*=\s*(.+)', _sg_tf)) == sorted(['each.value.to == "lab_mgmt" ? local.lab_mgmt_cidr : null', 'each.value.from == "lab_mgmt" ? local.lab_mgmt_cidr : each.value.cidr', '"127.0.0.1/32"'])
      and "var.vpc_cidr" not in _sg_tf and "cidr_ipv6" not in _sg_tf)
check("ルールは表から for_each で作る。送信は from の SG、受信は to の SG で、相手は SG の参照・S3 のプレフィックスリスト・lab の管理ネットワークのどれか 1 つ",
      re.search(r'resource "aws_vpc_security_group_egress_rule" "flow" \{\n\s*for_each = \{ for k, r in local\.sg_rules : k => r if contains\(local\.sg_keys, r\.from\) && r\.only != "ingress" \}', _sg_tf) is not None
      and re.search(r'resource "aws_vpc_security_group_ingress_rule" "flow" \{\n\s*for_each = \{ for k, r in local\.sg_rules : k => r if contains\(local\.sg_keys, r\.to\) && r\.only != "egress" \}', _sg_tf) is not None
      and 'prefix_list_id               = each.value.to == "s3" ? data.aws_ec2_managed_prefix_list.s3.id : null' in _sg_tf
      and 'name = "com.amazonaws.${var.region}.s3"' in _sg_tf
      and _core.count("resource \"aws_vpc_security_group_") == 3)
_lab_locals = open(os.path.join(ROOT, "terraform", "pipeline", "lab", "locals.tf"), encoding="utf-8").read()
_lab_sh = open(os.path.join(ROOT, "lab", "lab.sh"), encoding="utf-8").read()
_mgmt = re.search(r'lab_mgmt_cidr = "([^"]+)"', _sg_tf)
check("土台の lab_mgmt_cidr は terraform/pipeline/lab の mgmt_cidr と lab.sh の MGMT と同じ",
      _mgmt is not None and f'mgmt_cidr = "{_mgmt.group(1)}"' in _lab_locals and re.search(r'^MGMT=' + re.escape(_mgmt.group(1)) + r'$', _lab_sh, re.M) is not None)
check("土台の endpoints SG は表の 443 だけ受け、外へ出さない（インターフェース型と OpenSearch Serverless の VPC エンドポイント用）",
      re.search(r'"endpoints_none"[\s\S]*?security_group_id\s*=\s*aws_security_group\.endpoints\.id[\s\S]*?cidr_ipv4\s*=\s*"127\.0\.0\.1/32"', _sg_tf, re.S) is not None
      and not any(t == "endpoints" and (p, q) != (443, 443) for _, t, _, p, q, _ in _flows)
      and not any(f == "endpoints" for f, *_ in _flows))
check("土台の output security_group_ids は SG のキーと endpoints の map", re.search(r'output "security_group_ids" \{[\s\S]*?value\s*=\s*local\.sg_ids', _core) is not None
      and 'sg_ids  = merge({ for k, sg in aws_security_group.workload : k => sg.id }, { endpoints = aws_security_group.endpoints.id })' in _sg_tf
      and "internal_security_group_id" not in _core)

_fl = open(os.path.join(ROOT, "terraform", "base", "core", "flow_logs.tf"), encoding="utf-8").read()
check("VPC フローログ: 土台の VPC の全通信を 60 秒の集約で CloudWatch Logs へ。ロググループは /<prefix>/vpc-flow-logs で保持期間つき",
      re.search(r'resource "aws_flow_log" "vpc" \{[\s\S]*?vpc_id\s*=\s*aws_vpc\.this\.id[\s\S]*?traffic_type\s*=\s*"ALL"[\s\S]*?log_destination_type\s*=\s*"cloud-watch-logs"'
                r'[\s\S]*?log_destination\s*=\s*aws_cloudwatch_log_group\.flow_logs\.arn[\s\S]*?max_aggregation_interval\s*=\s*60', _fl) is not None
      and 'name              = "/${local.name_prefix}/vpc-flow-logs"' in _fl and "retention_in_days = var.flow_log_retention_days" in _fl
      and re.search(r'output "flow_log_group_name" \{[\s\S]*?aws_cloudwatch_log_group\.flow_logs\.name', _core) is not None
      and _core.count('resource "aws_flow_log"') == 1)
check("VPC フローログのロール: 信頼は自アカウントの vpc-flow-log だけ、書けるのはそのロググループだけで CreateLogGroup は無く、閉域の Deny は付けない",
      re.search(r'Principal = \{ Service = "vpc-flow-logs\.amazonaws\.com" \}[\s\S]*?"aws:SourceAccount" = local\.account_id[\s\S]*?"aws:SourceArn" = "arn:\$\{local\.partition\}:ec2:\$\{var\.region\}:\$\{local\.account_id\}:vpc-flow-log/\*"', _fl) is not None
      and 'Resource = "${aws_cloudwatch_log_group.flow_logs.arn}:*"' in _fl and '"logs:CreateLogGroup"' not in _fl
      and "aws_iam_role.flow_logs" not in open(os.path.join(ROOT, "terraform", "base", "core", "perimeter.tf"), encoding="utf-8").read())

# ---- S3 Tables のテーブル（列はスクリプトと同じでなければ append が落ちる）
TABLE_COLUMNS = ["ts", "topic", "measurement", "agent_host", "host", "tags_json", "fields_json", "ingested_at"]
schema = re.search(r'resource "aws_s3tables_table" "raw_telemetry"(.*?)\n\}\n', tf, re.S)
check("aws_s3tables_table raw_telemetry がある", schema is not None)
fields = re.findall(r'field\s*\{\s*name\s*=\s*"([a-z_]+)"\s*type\s*=\s*"([a-z]+)"\s*required\s*=\s*(true|false)', schema.group(1))
check("テーブルの列は ts / topic / measurement / agent_host / host / tags_json / fields_json / ingested_at の順", [f[0] for f in fields] == TABLE_COLUMNS)
coltypes = dict((f[0], f[1]) for f in fields)
check("ts と ingested_at は timestamp、それ以外は string",
      coltypes["ts"] == "timestamp" and coltypes["ingested_at"] == "timestamp" and all(coltypes[c] == "string" for c in TABLE_COLUMNS if c not in ("ts", "ingested_at")))
check("必須は ts / topic / ingested_at だけ", sorted(f[0] for f in fields if f[2] == "true") == ["ingested_at", "topic", "ts"])
check("format は ICEBERG", re.search(r'format\s*=\s*"ICEBERG"', schema.group(1)) is not None)
check("namespace とテーブル名はアンダースコアだけ（ハイフン不可）",
      re.search(r'variable "namespace"[\s\S]*?regex\("\^\[a-z0-9\]\[a-z0-9_\]', tf, re.S) is not None
      and re.search(r'variable "table_name"[\s\S]*?regex\("\^\[a-z0-9\]\[a-z0-9_\]', tf, re.S) is not None)
check("既定のテーブル名は raw_telemetry（2026-10-04 に snmp_metrics から改名）", re.search(r'variable "table_name"[\s\S]*?default\s*=\s*"raw_telemetry"', tf, re.M) is not None)
check("snmp_metrics から raw_telemetry へ moved で state を引き継ぐ（ほかに snmp_metrics の名前は残らない）",
      re.search(r'moved \{\n  from = aws_s3tables_table\.snmp_metrics\n  to   = aws_s3tables_table\.raw_telemetry\n\}', tf) is not None
      and tf.count("snmp_metrics") == 2)

# ---- EMR Serverless（器だけ。ジョブは ops/up.sh が起こす）
check("EMR Serverless は spark / ARM64", re.search(r'aws_emrserverless_application" "spark"[\s\S]*?type\s*=\s*"spark"[\s\S]*?architecture\s*=\s*"ARM64"', tf, re.S) is not None)
check("使わなければ止まる（auto_stop）", re.search(r'auto_stop_configuration\s*\{\s*enabled\s*=\s*true', tf) is not None)
check("release は emr-7.5 以上（S3 Tables の下限）", re.search(r'default\s*=\s*"emr-7\.(5|[6-9]|1[0-9])\.[0-9]+"', tf) is not None)
check("ロググループに retention がある", re.search(r'aws_cloudwatch_log_group" "emr"[\s\S]*?retention_in_days', tf, re.S) is not None)
check("runtime role は emr-serverless から assume（SourceAccount の条件付き）",
      re.search(r'"emr-serverless\.amazonaws\.com"', tf) is not None and '"aws:SourceAccount"' in tf)
for act in ("s3tables:GetTableMetadataLocation", "s3tables:UpdateTableMetadataLocation", "s3tables:PutTableData", "kafka-cluster:ReadData", "kafka-cluster:Connect", "kafka-cluster:CreateTopic"):
    check(f"runtime role に {act}", f'"{act}"' in tf)
check("Kafka のトピック ARN は cluster → topic の置き換え", 'replace(local.msk_cluster_arn, ":cluster/", ":topic/")' in tf)

# ---- 格納先（var.sinks。Kafka から 4 つに分ける。splunk は Spark から HEC に書く。2026-09-26 に MSK Connect をやめた）
check("variable sinks は list、既定 3 つ（iceberg / opensearch / prometheus。2026-09-17 ユーザー決定）、validation は splunk を入れた 4 つ",
      re.search(r'variable "sinks"[\s\S]*?type\s*=\s*list\(string\)[\s\S]*?default\s*=\s*\["iceberg",\s*"opensearch",\s*"prometheus"\][\s\S]*?validation', tf, re.S) is not None
      and re.search(r'variable "sinks"[\s\S]*?validation[\s\S]*?\["iceberg",\s*"opensearch",\s*"prometheus",\s*"splunk"\]', tf, re.S) is not None)
check("variable metric_topics / log_topics（既定 metrics / traps + logs、空を拒否）",
      re.search(r'variable "metric_topics"[\s\S]*?default\s*=\s*\["metrics",\s*"gnmi",\s*"mdt"\][\s\S]*?validation', tf, re.S) is not None
      and re.search(r'variable "log_topics"[\s\S]*?default\s*=\s*\["traps",\s*"logs"\][\s\S]*?validation', tf, re.S) is not None)
_splunk_tf = open(os.path.join(TF_DIR, "splunk.tf"), encoding="utf-8").read()
_grafana_tf = open(os.path.join(TF_DIR, "grafana.tf"), encoding="utf-8").read()
_ecs_tf = open(os.path.join(TF_DIR, "ecs.tf"), encoding="utf-8").read()
check("Splunk のリソースは splunk.tf だけで、全部 splunk_on_ecs（sinks に splunk があるとき）だけ作る",
      all(n == "splunk.tf" for n in tf_files if re.search(r'resource "[^"]*" "splunk', open(os.path.join(TF_DIR, n), encoding="utf-8").read()))
      and len(re.findall(r'^resource "', _splunk_tf, re.M)) == len(re.findall(r'count\s*=\s*local\.splunk_on_ecs\b', _splunk_tf))
      and re.search(r'splunk_on_ecs\s*=\s*local\.sink_splunk\n', tf) is not None)
check("ECS の Splunk の HEC は Cloud Map の splunk.<prefix>.internal:8088（クラスターは indexer の splunk-idx）で、自己署名なので検証しない",
      re.search(r'splunk_hec_url\s*=\s*local\.splunk_cluster \? "https://splunk-idx\.\$\{local\.service_namespace\}:8088" : "https://splunk\.\$\{local\.service_namespace\}:8088"\n', tf) is not None
      and re.search(r'splunk_skip_tls_verify\s*=\s*true\n', tf) is not None)
check("Splunk のパスワードと HEC の token はタスク定義の secrets（SSM の ARN）で渡し、environment に値を書かない",
      re.search(r'name = "SPLUNK_PASSWORD", valueFrom = local\.splunk_password_arn', _splunk_tf) is not None
      and re.search(r'name = "SPLUNK_HEC_TOKEN", valueFrom = local\.splunk_token_parameter_arn', _splunk_tf) is not None
      and "--accept-license" in _splunk_tf and "SPLUNK_GENERAL_TERMS" in _splunk_tf)
check("Splunk のヘルスチェックは checkstate.sh で、startPeriod は上限の 300",
      "/sbin/checkstate.sh" in _splunk_tf and re.search(r'startPeriod\s*=\s*300', _splunk_tf) is not None)
def _sp_res(kind, name):  # splunk.tf の resource "<kind>" "<name>" の中身（無ければ空）
    m = re.search(r'resource "' + kind + r'" "' + name + r'" \{(.*?)\n\}', _splunk_tf, re.S)
    return m.group(1) if m else ""
check("Splunk のクラスター（splunk_az_num が 2 か 3。「Splunk をクラスターにする（004）」）: 同じイメージを SPLUNK_ROLE で manager（splunk-cm）・"
      "indexer（splunk-idx。AZ ごとに 1、1 台ずつ入れ替え、止まるまで 120 秒、HEALTHY でなければ DNS から外す）・search head（splunk。突き合わせを足す）に分け、"
      "合言葉は SSM の idxc-secret。ヘルスチェックの retries は 10",
      all(f'{{ name = "SPLUNK_ROLE", value = "{r}" }}' in _splunk_tf for r in ("splunk_cluster_master", "splunk_indexer", "splunk_search_head"))
      and re.search(r'name\s*=\s*"splunk-cm"\n', _sp_res("aws_service_discovery_service", "splunk_cm")) is not None
      and re.search(r'name\s*=\s*"splunk-idx"\n', _sp_res("aws_service_discovery_service", "splunk_idx")) is not None
      and "health_check_custom_config {}" in _sp_res("aws_service_discovery_service", "splunk_idx")
      and re.search(r'deployment_minimum_healthy_percent = 50\n\s*deployment_maximum_percent\s*= 100\n[\s\S]*availability_zone_rebalancing = "DISABLED"',
                    _sp_res("aws_ecs_service", "splunk_idx")) is not None
      and re.search(r"stopTimeout = 120\n", _sp_res("aws_ecs_task_definition", "splunk_idx")) is not None
      and "stopTimeout" not in _sp_res("aws_ecs_task_definition", "splunk_cm")
      and re.search(r"subnets\s*=\s*\[local\.instance_subnet_id\]", _sp_res("aws_ecs_service", "splunk_cm")) is not None
      and '"/sbin/checkstate.sh && /sbin/nwc-peers-check.py"' in _sp_res("aws_ecs_task_definition", "splunk")
      and re.search(r"retries\s*=\s*10\n", _splunk_tf) is not None
      and 'name = "SPLUNK_IDXC_PASS4SYMMKEY", valueFrom = local.splunk_idxc_secret_arn' in _splunk_tf
      and 'parameter/${local.name_prefix}/splunk/idxc-secret"' in _splunk_tf
      and "local.splunk_cluster ? [local.splunk_idxc_secret_arn] : []" in _sp_res("aws_iam_role_policy", "splunk_execution")
      and 'ensure_secret "/$PREFIX/splunk/idxc-secret" password' in up
      and re.search(r"SPLUNK_INDEXER_URL|indexes\.conf", _splunk_tf + up) is None)
check("Grafana は create_grafana（Prometheus か OpenSearch があるとき）だけで、admin のパスワードは secrets",
      re.search(r'create_grafana\s*=\s*var\.create_grafana && \(local\.sink_prometheus \|\| local\.sink_opensearch\)', tf) is not None
      and len(re.findall(r'^resource "', _grafana_tf, re.M)) == len(re.findall(r'count\s*=\s*local\.create_grafana\b', _grafana_tf))
      and re.search(r'name = "GF_SECURITY_ADMIN_PASSWORD", valueFrom = local\.grafana_password_arn', _grafana_tf) is not None)
check("Grafana のタスクロールは読むだけ（aps:QueryMetrics などと aoss:APIAccessAll。書き込みの aps:RemoteWrite は無い）",
      "aps:QueryMetrics" in _grafana_tf and "aps:RemoteWrite" not in _grafana_tf and "aoss:APIAccessAll" in _grafana_tf
      and "aoss:ReadDocument" in _grafana_tf and "aoss:WriteDocument" not in _grafana_tf)
check("ECS のロールは全部 perimeter の Deny を付け、信頼ポリシーは aws:SourceAccount で絞る",
      len(re.findall(r'resource "aws_iam_role" ', _grafana_tf + _splunk_tf)) == len(re.findall(r'policy_arn\s*=\s*local\.perimeter_policy_arn', _grafana_tf + _splunk_tf))
      and '"aws:SourceAccount"' in _ecs_tf)
check("up.sh は Grafana / Splunk のパスワードと HEC の token を ensure_secret で SSM に作り（値は Python が本人だけ読める一時ファイルに書いて file:// で渡し、すぐ消す）、down.sh は ManagedBy のタグで消す",
      'ensure_secret "/$PREFIX/grafana/admin-password" password' in up and 'ensure_secret "/$PREFIX/splunk/admin-password" password' in up
      and 'ensure_secret "/$PREFIX/splunk/hec-token" uuid' in up and '--cli-input-json "file://$input"' in up and "umask 077" in up
      and 'rm -f -- "${input:?}"' in up and "file:///dev/stdin" not in up.replace("（file:///dev/stdin）", "")
      and '"Key=tag:ManagedBy,Values=$OPS_DIR/up.sh"' in down and "aws ssm delete-parameter" in down)
check("up.sh は ECS の Splunk が HEALTHY になってから Spark のジョブを起こす", up.index('log "7-4b.') < up.index('log "7-5.') and "healthStatus" in up)
check("variable splunk_hec_token_parameter（既定は空。/ で始まる）/ splunk_index。外の Splunk の splunk_hec_url / splunk_skip_tls_verify は無い（2026-09-28）",
      re.search(r'variable "splunk_hec_token_parameter"[\s\S]*?default\s*=\s*""[\s\S]*?validation', tf, re.S) is not None
      and re.search(r'variable "splunk_index"[\s\S]*?default\s*=\s*""', tf, re.S) is not None
      and 'variable "splunk_hec_url"' not in tf and 'variable "splunk_skip_tls_verify"' not in tf)
check("locals に sink_iceberg / sink_opensearch / sink_prometheus / sink_splunk",
      all(re.search(r'sink_' + s + r'\s*=\s*contains\(var\.sinks,\s*"' + s + r'"\)', tf) for s in ("iceberg", "opensearch", "prometheus", "splunk")))
check("HEC の token は SSM の SecureString /<prefix>/splunk/hec-token（変数で変えられる）。runtime role は splunk のときだけ ssm:GetParameter をそのパラメータに限って持つ",
      re.search(r'splunk_token_parameter\s*=\s*var\.splunk_hec_token_parameter != "" \? var\.splunk_hec_token_parameter : "/\$\{local\.name_prefix\}/splunk/hec-token"', tf) is not None
      and re.search(r'splunk_token_parameter_arn\s*=\s*"arn:\$\{local\.partition\}:ssm:\$\{var\.region\}:\$\{local\.account_id\}:parameter\$\{local\.splunk_token_parameter\}"', tf) is not None
      and re.search(r'Sid\s*=\s*"SplunkHecToken"[\s\S]*?"ssm:GetParameter"[\s\S]*?local\.splunk_token_parameter_arn[\s\S]*?if local\.sink_splunk', tf, re.S) is not None)
check("Terraform は token の値を読まない（data aws_ssm_parameter が無い）", 'data "aws_ssm_parameter"' not in tf)
check("HEC のポートごとのエグレスは analytics に無い（spark から splunk の 8088 は土台の通信の表。ECS の Splunk は VPC の中）",
      "splunk_hec_port" not in tf and "emr_splunk" not in tf)
check("OpenSearch Serverless は TIMESERIES のコレクション <prefix>-logs（count で作る）",
      re.search(r'resource "aws_opensearchserverless_collection" "logs"[\s\S]*?count\s*=\s*local\.sink_opensearch \? 1 : 0[\s\S]*?type\s*=\s*"TIMESERIES"', tf, re.S) is not None
      and re.search(r'logs_collection\s*=\s*"\$\{local\.name_prefix\}-logs"', tf) is not None)
check("OpenSearch のコレクションは公開せず、土台の VPC エンドポイント（KB と共用。2026-09-28）からだけ。無ければ precondition で止まる",
      "aws_opensearchserverless_vpc_endpoint" not in tf
      and re.search(r'aoss_vpce_id\s*=\s*try\(data\.terraform_remote_state\.main\.outputs\.opensearch_vpc_endpoint_id,\s*""\)', tf) is not None
      and re.search(r'"logs_network"[\s\S]*?AllowFromPublic\s*=\s*false[\s\S]*?SourceVPCEs\s*=\s*\[local\.aoss_vpce_id\][\s\S]*?precondition\s*\{[\s\S]*?local\.aoss_vpce_id\s*!=\s*""', tf, re.S) is not None)
check("土台の OpenSearch Serverless の VPC エンドポイントは create_opensearch_endpoint（既定 false）の count で 1 本、SG は endpoints",
      re.search(r'resource "aws_opensearchserverless_vpc_endpoint" "aoss" \{\n\s*count\s*=\s*var\.create_opensearch_endpoint \? 1 : 0[\s\S]*?security_group_ids\s*=\s*\[aws_security_group\.endpoints\.id\]', _core, re.S) is not None
      and re.search(r'variable "create_opensearch_endpoint"[\s\S]*?default\s*=\s*false', _core) is not None
      and _core.count('resource "aws_opensearchserverless_vpc_endpoint"') == 1)
check("OpenSearch のデータアクセスは EMR の実行ロールだけ、snmp-logs のインデックスに WriteDocument / CreateIndex",
      re.search(r'resource "aws_opensearchserverless_access_policy" "logs"[\s\S]*?"aoss:CreateIndex"[\s\S]*?"aoss:WriteDocument"[\s\S]*?Principal\s*=\s*\[aws_iam_role\.emr\.arn\]', tf, re.S) is not None
      and re.search(r'opensearch_index\s*=\s*"snmp-logs"', tf) is not None)
check("Prometheus はワークスペース <prefix>-metrics（remote write は土台の aps-workspaces のエンドポイントを通る。analytics はエンドポイントを持たない）",
      re.search(r'resource "aws_prometheus_workspace" "metrics"[\s\S]*?count\s*=\s*local\.sink_prometheus \? 1 : 0[\s\S]*?alias\s*=\s*local\.metrics_workspace', tf, re.S) is not None
      and re.search(r'metrics_workspace\s*=\s*"\$\{local\.name_prefix\}-metrics"', tf) is not None
      and 'resource "aws_vpc_endpoint" "aps"' not in tf)
check("runtime role に aoss:APIAccessAll と aps:RemoteWrite（格納先を選んだときだけ。for-if で count 0 のときの index を避ける）",
      re.search(r'"aoss:APIAccessAll"[\s\S]*?local\.sink_opensearch \? aws_opensearchserverless_collection\.logs\[0\]\.arn : ""[\s\S]*?\] : s if local\.sink_opensearch\]', tf, re.S) is not None
      and re.search(r'"aps:RemoteWrite"[\s\S]*?local\.sink_prometheus \? aws_prometheus_workspace\.metrics\[0\]\.arn : ""[\s\S]*?\] : s if local\.sink_prometheus\]', tf, re.S) is not None)
check("remote write の URL は prometheus_endpoint + api/v1/remote_write",
      re.search(r'prometheus_remote_write_url\s*=\s*local\.sink_prometheus \? "\$\{aws_prometheus_workspace\.metrics\[0\]\.prometheus_endpoint\}api/v1/remote_write" : ""', tf) is not None)

# ---- output（ops/up.sh がそのまま使う）
for out in ("application_id", "runtime_role_arn", "table_identifier", "job_driver_json_iceberg", "job_driver_json_splunk", "job_driver_json_http", "configuration_overrides_json", "list_job_runs_command", "list_tables_command",
            "sinks", "opensearch_collection_endpoint", "prometheus_workspace_id", "prometheus_remote_write_url", "prometheus_query_url",
            "table_bucket_arn", "table_namespace", "proposal_events_table_name", "proposal_events_table_arn",
            "alert_events_stream_name", "alert_events_table_name", "alert_events_table_arn", "athena_workgroup", "athena_catalog",
            "opensearch_collection_name", "opensearch_collection_arn", "opensearch_index", "prometheus_workspace_arn",
            "splunk_hec_url", "splunk_token_parameter", "analytics_cluster_name", "splunk_service_name", "splunk_port_forward_command",
            "splunk_cm_service_name", "splunk_idx_service_name", "splunk_cm_port_forward_command",
            "splunk_password_command", "grafana_service_name", "grafana_port_forward_command", "grafana_password_command"):
    check(f"output {out} がある", re.search(r'^output "' + out + r'"', tf, re.M) is not None)
check("job_driver は S3 Tables のカタログを spark-submit の --conf で渡す",
      "software.amazon.s3tables.iceberg.S3TablesCatalog" in tf and "org.apache.iceberg.spark.SparkCatalog" in tf
      and "IcebergSparkSessionExtensions" in tf)
args_block = re.search(r'entryPointArguments\s*=\s*concat\((.*?)\n\s*\)\n', tf, re.S)
check("job_driver の引数は concat（共通 + 格納先ごとの for-if）", args_block is not None)
for a in ("--bootstrap", "--checkpoint", "--sinks", "--region", "--metric-topics", "--log-topics"):
    check(f"job_driver の共通の引数に {a}", f'"{a}"' in args_block.group(1))
check("job_driver に検知の引数（--neptune-endpoint / --anomaly-events-table / --event-bus）は無く、スクリプトが受ける引数だけを渡す",
      not any(a in tf for a in ('"--neptune-endpoint"', '"--anomaly-events-table"', '"--event-bus"', '"--event-source"'))
      and set(re.findall(r'"(--[a-z-]+)"', args_block.group(1))) <= set(re.findall(r'add_argument\("(--[a-z-]+)"', src)))
check("job_driver の格納先の引数は、そのジョブの格納先にあるときだけ（for a in [...] : a if contains(sinks, ...)）",
      re.search(r'\["--iceberg-table",\s*local\.iceberg_table\] : a if contains\(sinks, "iceberg"\)', args_block.group(1)) is not None
      and re.search(r'\["--opensearch-endpoint",\s*local\.opensearch_endpoint,\s*"--opensearch-index",\s*local\.opensearch_index\] : a if contains\(sinks, "opensearch"\)', args_block.group(1)) is not None
      and re.search(r'\["--prometheus-url",\s*local\.prometheus_remote_write_url\] : a if contains\(sinks, "prometheus"\)', args_block.group(1)) is not None
      and re.search(r'\["--splunk-hec-url",\s*local\.splunk_hec_url,\s*"--splunk-token-parameter",\s*local\.splunk_token_parameter,\s*"--splunk-index",\s*var\.splunk_index\] : a if contains\(sinks, "splunk"\)', args_block.group(1)) is not None
      and re.search(r'\["--splunk-skip-verify"\] : a if contains\(sinks, "splunk"\) && local\.splunk_skip_tls_verify', args_block.group(1)) is not None
      and "local.sink_" not in args_block.group(1))
check("job_driver は device map が空でなく、そのジョブに prometheus か opensearch があるときだけ --device-map を渡す（cycle 002。sysName の無い gNMI と trap に機器名を足す）",
      re.search(r'\["--device-map",\s*var\.device_map\] : a if var\.device_map != "" && \(contains\(sinks, "prometheus"\) \|\| contains\(sinks, "opensearch"\)\)', args_block.group(1)) is not None)
check("job_driver の引数に token の値は無い（SSM のパラメータ名だけ）", "hec-token" not in args_block.group(1) and "splunk_hec_token" not in args_block.group(1))
check("--sinks はそのジョブの格納先（var.sinks にあるものだけ）をカンマでつなぐ", '"--sinks", join(",", sinks)' in args_block.group(1) and "var.sinks" not in args_block.group(1))
check("--checkpoint は s3://<バケット>/analytics/checkpoint/<MSK の uuid>/（MSK を作り直したら checkpoint も新しく。格納先ごとに下を切るのはスクリプト）",
      '"--checkpoint", local.checkpoint_uri' in args_block.group(1)
      and re.search(r'msk_cluster_uuid\s*=\s*try\(element\(split\("/", local\.msk_cluster_arn\), 2\)', tf) is not None
      and re.search(r'checkpoint_uri\s*=\s*"s3://\$\{local\.bucket\}/\$\{local\.checkpoint\}/\$\{local\.msk_cluster_uuid\}/"', tf) is not None)
check("job_driver は jars を s3://<バケット>/analytics/jars/ から読む", "spark.jars=s3://${local.bucket}/${local.jars_prefix}/*.jar" in tf
      and re.search(r'jars_prefix\s*=\s*"\$\{local\.s3_prefix\}/jars"', tf) is not None and re.search(r's3_prefix\s*=\s*"analytics"', tf) is not None)
check("ジョブは 1 つ 3 vCPU（driver 1 + executor 2。Kafka のパーティション 2 つを並列に読む。動的割り当て無し）。sparkSubmitParameters は 3 つのジョブで共通",
      "spark.driver.cores=1" in tf and "spark.executor.cores=1" in tf and "spark.executor.instances=2" in tf and "spark.dynamicAllocation.enabled=false" in tf
      and tf.count("spark.executor.instances=") == 1 and tf.count("sparkSubmitParameters") == 1)
check("max_cpu は 12 vCPU（3 つのジョブで 9 vCPU）、max_memory は 48 GB（vCPU あたり 4 GB）",
      re.search(r'variable "max_cpu" \{[^}]*default\s*=\s*"12 vCPU"', tf) is not None
      and re.search(r'variable "max_memory" \{[^}]*default\s*=\s*"48 GB"', tf) is not None
      and re.search(r'maximum_capacity \{\s*cpu\s*=\s*var\.max_cpu\s*memory\s*=\s*var\.max_memory', tf) is not None)
check("ジョブは格納先で 3 つ（iceberg / splunk / http = opensearch と prometheus）。var.sinks に無い格納先は外し、空のジョブの job_driver は空文字",
      re.search(r'spark_jobs = \{ for job, sinks in \{ iceberg = \["iceberg"\], splunk = \["splunk"\], http = \["opensearch", "prometheus"\] \} :\s*'
                r'job => \[for s in sinks : s if contains\(var\.sinks, s\)\] \}', tf) is not None
      and re.search(r'job_drivers = \{ for job, sinks in local\.spark_jobs : job => length\(sinks\) == 0 \? "" : jsonencode\(', tf) is not None
      and all(re.search(r'output "job_driver_json_' + j + r'" \{[^}]*value\s*=\s*local\.job_drivers\["' + j + r'"\]', tf) for j in ("iceberg", "splunk", "http"))
      and re.search(r'^output "job_driver_json" ', tf, re.M) is None)
check("ドライバーのログは CloudWatch、EMR の managed storage は使わない",
      re.search(r'cloudWatchLoggingConfiguration\s*=\s*\{\s*enabled\s*=\s*var\.cloudwatch_logging', tf) is not None
      and re.search(r'managedPersistenceMonitoringConfiguration\s*=\s*\{\s*enabled\s*=\s*false', tf) is not None)

# ---- Spark のスクリプト（読み書きの形は文字列で見る。pyspark は関数の中で import するので、モジュールは pyspark 無しで読める）
tree = ast.parse(src, SRC)
funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
check("parse_args / sink_topics / read_rows / build / main がある", {"parse_args", "sink_topics", "read_rows", "build", "main"} <= set(funcs))
check("検知の関数（device / events / anomaly_key / make_detect_sender）はもう無い（検知は Grafana と Splunk。2026-10-02。parse_device_map は cycle 002 で sysName を足すのに戻した）",
      not ({"device", "events", "anomaly_key", "anomaly_detail", "make_detect_sender"} & set(funcs)) and {"parse_device_map", "with_sysname"} <= set(funcs))
check("DynamoDB を使わない（2026-09-24）", "dynamodb" not in tf.lower() and "anomaly_table_name" not in tf)
check("runtime role に Neptune と EventBridge の許可は無い（Spark は格納先に書くだけ）",
      "neptune-db" not in tf and "events:PutEvents" not in tf and "event_bus" not in tf and "NeptuneAnomalies" not in tf)
check("EMR と Neptune の間の SG のルールは analytics にも土台にも無い", "emr_neptune" not in tf and "neptune_from_emr" not in tf
      and re.search(r'from = "spark", to = "neptune"', _sg_tf) is None)
# 証跡のテーブルは修復案の proposal_events だけ（異常の履歴 anomaly_events は 2026-10-02 に消した。置き場所は保留）
_rules_tree = ast.parse(open(os.path.join(ROOT, "workflow", "rules.py"), encoding="utf-8").read())
_pec = next(ast.literal_eval(n.value) for n in _rules_tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "PROPOSAL_EVENT_COLUMNS")
_blk = re.search(r'resource "aws_s3tables_table" "proposal_events" \{(.*?)\n\}\n', tf, re.S)
check("証跡のテーブル proposal_events をいつも作り（count 無し）、列はどれも required = false（PyArrow の列は nullable）",
      _blk is not None and "count" not in _blk.group(1) and "required = true" not in _blk.group(1).replace(" ", "").replace("required=true", "required = true"))
check("proposal_events の列と順は workflow/rules.py の PROPOSAL_EVENT_COLUMNS と同じ",
      re.findall(r'name\s*=\s*"(\w+)"\s*\n\s*type\s*=\s*"(\w+)"', _blk.group(1)) == [tuple(c) for c in _pec])
check("異常の履歴のテーブル anomaly_events は無い（S3 Tables のテーブルは raw_telemetry と proposal_events と alert_events だけ）",
      re.findall(r'resource "aws_s3tables_table" "(\w+)"', tf) == ["raw_telemetry", "proposal_events", "alert_events"] and '"anomaly_events' not in tf and "anomaly_events_table" not in tf
      and "ANOMALY_EVENT_COLUMNS" not in src)
# アラートの通知の履歴（2026-10-04）。書くのは graph の status の Lambda → Firehose（history.tf）、読むのは query_history（Athena）
_aec = next(ast.literal_eval(n.value) for n in _rules_tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ALERT_EVENT_COLUMNS")
_ablk = re.search(r'resource "aws_s3tables_table" "alert_events" \{(.*?)\n\}\n', tf, re.S)
check("alert_events をいつも作り（count 無し）、列はどれも required = false",
      _ablk is not None and "count" not in _ablk.group(1) and re.search(r'required\s*=\s*true', _ablk.group(1)) is None)
check("alert_events の列と順は workflow/rules.py の ALERT_EVENT_COLUMNS と同じ",
      re.findall(r'name\s*=\s*"(\w+)"\s*\n\s*type\s*=\s*"(\w+)"', _ablk.group(1)) == [tuple(c) for c in _aec])
_hist = open(os.path.join(ROOT, "terraform", "pipeline", "analytics", "history.tf"), encoding="utf-8").read()
_fh = re.search(r'resource "aws_kinesis_firehose_delivery_stream" "alert_events" \{(.*?)\n\}\n', _hist, re.S)
check("Firehose <接頭辞>-alert-events は iceberg で s3tablescatalog/<テーブルバケット> の alert_events に書き、バッファは 60 秒 / 1 MiB",
      _fh is not None and 'name        = "${local.name_prefix}-alert-events"' in _fh.group(1) and 'destination = "iceberg"' in _fh.group(1)
      and re.search(r'catalog_arn\s*=\s*"\$\{local\.glue_catalog\}/\$\{local\.athena_catalog\}"', _fh.group(1)) is not None
      and 'athena_catalog = "s3tablescatalog/${local.table_bucket}"' in _hist
      and 'glue_catalog   = "arn:${local.partition}:glue:${var.region}:${local.account_id}:catalog"' in _hist
      and re.search(r'database_name\s*=\s*aws_s3tables_namespace\.netops\.namespace', _fh.group(1)) is not None
      and re.search(r'table_name\s*=\s*aws_s3tables_table\.alert_events\.name', _fh.group(1)) is not None
      and re.search(r'buffering_interval\s*=\s*60\b', _fh.group(1)) is not None and re.search(r'buffering_size\s*=\s*1\b', _fh.group(1)) is not None)
check("書けなかった行は土台のバケットの firehose-errors/alert_events/ に落とし（FailedDataOnly）、CloudWatch のログを付ける",
      re.search(r's3_backup_mode\s*=\s*"FailedDataOnly"', _fh.group(1)) is not None
      and re.search(r'bucket_arn\s*=\s*local\.bucket_arn', _fh.group(1)) is not None
      and re.search(r'error_output_prefix\s*=\s*local\.alert_errors', _fh.group(1)) is not None and 'alert_errors   = "firehose-errors/alert_events/"' in _hist
      and re.search(r'cloudwatch_logging_options \{\s*enabled\s*=\s*true', _fh.group(1)) is not None)
_fhpol = re.search(r'resource "aws_iam_role_policy" "alert_firehose" \{(.*?)\n\}\n', _hist, re.S).group(1)
check("Firehose のロール <接頭辞>-alert-firehose: 信頼は firehose.amazonaws.com を aws:SourceAccount で絞り、閉域の IAM 側の Deny は付けない",
      'alert_firehose    = "${local.name_prefix}-alert-firehose"' in _hist
      and re.search(r'Principal\s*=\s*\{\s*Service\s*=\s*"firehose\.amazonaws\.com"\s*\}', _hist) is not None
      and '"aws:SourceAccount" = local.account_id' in _hist and "perimeter_policy_arn" not in _hist)
check("Firehose のロールの許可は S3 Tables（テーブルバケットと /table/*）と Glue の s3tablescatalog と firehose-errors/* とログだけ",
      sorted(re.findall(r'"(s3tables:\w+)"', _fhpol)) == sorted(["s3tables:GetTableBucket", "s3tables:GetNamespace", "s3tables:GetTable", "s3tables:GetTableData",
                                                              "s3tables:GetTableMetadataLocation", "s3tables:PutTableData", "s3tables:UpdateTableMetadataLocation"])
      and sorted(re.findall(r'"(glue:\w+)"', _fhpol)) == sorted(["glue:GetCatalog", "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables", "glue:UpdateTable"])
      and '"${local.table_bucket_arn}/table/*"' in _fhpol and '"${local.glue_catalog}/s3tablescatalog/*"' in _fhpol
      and 'Resource = "${local.bucket_arn}/firehose-errors/*"' in _fhpol
      and "s3:*" not in _fhpol and "s3tables:*" not in _fhpol and "glue:*" not in _fhpol and '"*"' not in _fhpol)
_wg = re.search(r'resource "aws_athena_workgroup" "history" \{(.*?)\n\}\n', _hist, re.S)
check("Athena のワークグループ <接頭辞>-history: 結果は管理ストレージ、ワークグループの設定を強制し、スキャン量で打ち切り、force_destroy",
      _wg is not None and 'history_workgroup = "${local.name_prefix}-history"' in _hist
      and re.search(r'managed_query_results_configuration \{\s*enabled\s*=\s*true', _wg.group(1)) is not None
      and re.search(r'enforce_workgroup_configuration\s*=\s*true', _wg.group(1)) is not None
      and re.search(r'bytes_scanned_cutoff_per_query\s*=\s*\d+', _wg.group(1)) is not None
      and re.search(r'force_destroy\s*=\s*true', _wg.group(1)) is not None and "output_location" not in _wg.group(1))
check("analytics に events / sns のエンドポイントは無い（SNS へは土台の sns のエンドポイント。ops/up.sh が足す）", 'resource "aws_vpc_endpoint"' not in tf and "events_endpoint_id" not in tf)
check("build の引数は spark / args（格納先ごとに Kafka を読む）", [a.arg for a in funcs["build"].args.args] == ["spark", "args"])
check("pyspark はモジュールの先頭で import しない（テストと引数の検査を pyspark 無しで動かすため）",
      not any(isinstance(n, (ast.Import, ast.ImportFrom)) and "pyspark" in ast.dump(n) for n in tree.body))
check("既定のトピックは metrics / gnmi / mdt（メトリクス。gnmi は Telegraf の inputs.gnmi、mdt は inputs.cisco_telemetry_mdt）と traps / logs（ログ。logs は機器の syslog）", re.search(r'^METRIC_TOPICS\s*=\s*"metrics,gnmi,mdt"', src, re.M) is not None
      and re.search(r'^LOG_TOPICS\s*=\s*"traps,logs"', src, re.M) is not None)
check("SINKS は iceberg / opensearch / prometheus / splunk（Terraform の validation と同じ）", re.search(r'^SINKS\s*=\s*\("iceberg", "opensearch", "prometheus", "splunk"\)', src, re.M) is not None)
check("Kafka を readStream で読み、購読は引数（格納先ごと）", '.readStream.format("kafka")' in src and '.option("subscribe", topics)' in src)
for k, v in (("kafka.security.protocol", "SASL_SSL"), ("kafka.sasl.mechanism", "AWS_MSK_IAM"),
             ("kafka.sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;"),
             ("kafka.sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler")):
    check(f"MSK の IAM 認証: {k}", f'.option("{k}", "{v}")' in src)
check("Iceberg に append で書き、toTable で名前を渡す", '.writeStream.queryName("iceberg").format("iceberg")' in src and '.outputMode("append")' in src and ".toTable(table)" in src)
check("checkpoint は格納先ごと（iceberg/ と <name>/）", '.option("checkpointLocation", checkpoint + "iceberg/")' in src
      and '.option("checkpointLocation", checkpoint + name + "/")' in src)
check("60 秒ごとのマイクロバッチ", re.search(r'^TRIGGER\s*=\s*"60 seconds"', src, re.M) is not None and src.count("processingTime=TRIGGER") == 2)
check("HTTP の格納先は foreachBatch で、既定は driver が collect して送り、--http-send executor なら foreachPartition で executor が送る",
      '.foreachBatch({"driver": each_batch, "executor": each_batch_on_executors}[http_send])' in src and "batch_df.collect()" in src
      and "batch_df.foreachPartition(" in src)
check("SigV4 は botocore（EMR の実行ロールの認証情報）", "from botocore.auth import SigV4Auth" in src and "from botocore.awsrequest import AWSRequest" in src and 'h["x-amz-content-sha256"] = hashlib.sha256(body).hexdigest()' in src)
check("OpenSearch は _bulk に aoss の SigV4、Prometheus は remote write に aps の SigV4",
      re.search(r'sigv4_headers\("POST", url, body, "aoss", region', src) is not None
      and re.search(r'sigv4_headers\("POST", url, body, "aps", region', src) is not None
      and '"Content-Encoding": "snappy"' in src and '"X-Prometheus-Remote-Write-Version": "0.1.0"' in src)
# select の各行は「….alias("列")」か、そのままの列名「F.col("topic")」
block = src.split("rows = parsed.select(")[1].split(").where(")[0]
aliases = [a or b for a, b in re.findall(r'(?:\.alias\("([a-z_]+)"\)|^\s*F\.col\("([a-z_]+)"\)),\s*$', block, re.M)]
ADDED_COLUMNS = ["event_id", "kafka_topic", "kafka_partition", "kafka_offset"]
check("スクリプトの列は tables.tf の列と同じ順で、そのあとに event_id / kafka_topic / kafka_partition / kafka_offset（ジョブが ALTER TABLE で足す列）",
      aliases == TABLE_COLUMNS + ADDED_COLUMNS)
check("tables.tf の schema には足す列を書かない（schema を変えるとテーブルを作り直して行が消える）。コメントで ICEBERG_ADDED_COLUMNS を指す",
      not any(c in [f[0] for f in fields] for c in ADDED_COLUMNS) and "ICEBERG_ADDED_COLUMNS" in open(os.path.join(TF_DIR, "tables.tf"), encoding="utf-8").read())
_parsed = src.split("parsed = raw.select(")[1].split("rows = parsed.select(")[0]
check("event_id は from_json の前の value（binary のまま。cast しない）の SHA-256 の 16 進（F.sha2(…, 256)）、Kafka の partition / offset もここで取る",
      'F.sha2(F.col("value"), 256).alias("event_id")' in _parsed and 'F.col("partition").alias("kafka_partition")' in _parsed
      and 'F.col("offset").alias("kafka_offset")' in _parsed and "sha2(F.col(\"value\").cast" not in src)
check("重複は落とさない（.dropDuplicates( を呼ばない。落とすのは読む側）", ".dropDuplicates(" not in src and ".dropDuplicatesWithinWatermark(" not in src)
check("timestamp が無い行は捨てる", '.where(F.col("ts").isNotNull())' in src)
check("tags / fields は JSON 文字列のまま", 'F.to_json(F.col("m.tags")).alias("tags_json")' in src and 'F.to_json(F.col("m.fields")).alias("fields_json")' in src)

# ---- 純粋な関数を本当に動かす（引数の検査、トピックの振り分け、名前の規則、protobuf と snappy の手組み）
spec = importlib.util.spec_from_file_location("snmp_sinks", SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# マネージド版の動き（token は SSM から読む）を見る。OSS 版の SPLUNK_HEC_TOKEN は tests/test_oss.py
os.environ.pop("SPLUNK_HEC_TOKEN", None)

def parse_error(argv):
    """argparse の p.error は SystemExit(2)。使い方の表示は捨てる"""
    saved = sys.stderr
    sys.stderr = io.StringIO()
    try:
        mod.parse_args(argv)
    except SystemExit as e:
        return e.code
    finally:
        sys.stderr = saved
    return None

base = ["--bootstrap", "b:9098", "--checkpoint", "s3://bucket/analytics/checkpoint"]
a = mod.parse_args(base + ["--sinks", "iceberg", "--iceberg-table", "s3tablesbucket.ns.t"])
check("parse_args: 既定は metrics / traps,logs、checkpoint に / を足す、sinks はリスト",
      a.metric_topics == "metrics,gnmi,mdt" and a.log_topics == "traps,logs" and a.checkpoint == "s3://bucket/analytics/checkpoint/" and a.sinks == ["iceberg"])
a = mod.parse_args(base + ["--sinks", "iceberg, prometheus ,opensearch", "--iceberg-table", "t", "--prometheus-url", "https://p/api/v1/remote_write",
                           "--opensearch-endpoint", "https://o", "--metric-topics", " metrics , cpu ", "--log-topics", "traps,logs"])
check("parse_args: 空白を除いて 3 つ、トピックも空白を除く", a.sinks == ["iceberg", "prometheus", "opensearch"] and a.metric_topics == "metrics,cpu" and a.log_topics == "traps,logs"
      and a.opensearch_index == "snmp-logs")
check("parse_args: iceberg なのに --iceberg-table が無ければ 2 で止まる", parse_error(base + ["--sinks", "iceberg"]) == 2)
check("parse_args: opensearch / prometheus も同じ", parse_error(base + ["--sinks", "opensearch"]) == 2 and parse_error(base + ["--sinks", "prometheus"]) == 2)
check("parse_args: 知らない格納先と空の --sinks は 2", parse_error(base + ["--sinks", "kinesis"]) == 2 and parse_error(base + ["--sinks", " , "]) == 2)
check("parse_args: splunk は --splunk-hec-url と --splunk-token-parameter が要る（index と skip-verify は任意）",
      parse_error(base + ["--sinks", "splunk"]) == 2 and parse_error(base + ["--sinks", "splunk", "--splunk-hec-url", "https://s:8088"]) == 2
      and (lambda a: a.sinks == ["splunk"] and a.splunk_index == "" and a.splunk_skip_verify is False)(
          mod.parse_args(base + ["--sinks", "splunk", "--splunk-hec-url", "https://s:8088", "--splunk-token-parameter", "/p/splunk/hec-token"]))
      and mod.parse_args(base + ["--sinks", "splunk", "--splunk-hec-url", "https://s:8088", "--splunk-token-parameter", "/p/t", "--splunk-index", "netops", "--splunk-skip-verify"]).splunk_skip_verify is True)
check("parse_args: 空の --metric-topics は 2", parse_error(base + ["--sinks", "iceberg", "--iceberg-table", "t", "--metric-topics", ","]) == 2)

check("sink_topics: iceberg は全部（重複無し）、prometheus はメトリクス、opensearch はログ",
      mod.sink_topics("iceberg", "metrics,cpu", "traps,logs") == "metrics,cpu,traps,logs"
      and mod.sink_topics("iceberg", "a,b", "b") == "a,b"
      and mod.sink_topics("prometheus", "metrics", "traps") == "metrics"
      and mod.sink_topics("opensearch", "metrics", "traps") == "traps")
check("sink_topics: splunk は iceberg と同じく全部", mod.sink_topics("splunk", "metrics,cpu", "traps,logs") == "metrics,cpu,traps,logs")
try:
    mod.sink_topics("kinesis", "m", "l")
    bad_sink = False
except ValueError:
    bad_sink = True
check("sink_topics: 知らない格納先は ValueError", bad_sink)

check("metric_name: snmp_<measurement>_<field>、使えない字は _、先頭の数字は _ を足す",
      mod.metric_name("interface", "ifInOctets") == "snmp_interface_ifInOctets"
      and mod.metric_name("if-x", "in.octets") == "snmp_if_x_in_octets"
      and mod.metric_name("1x", "y") == "snmp_1x_y" and mod.metric_name("", "") == "snmp__")
check("label_name: 使えない字は _、空と先頭の数字は _ を足す、__ 始まりは _ 1 つに",
      mod.label_name("agent_host") == "agent_host" and mod.label_name("if-name") == "if_name"
      and mod.label_name("") == "_" and mod.label_name("1a") == "_1a" and mod.label_name("__meta") == "_meta")

rec = {"ts": 1700000000.5, "topic": "metrics", "measurement": "interface", "agent_host": "r1", "host": "h",
       "tags": {"agent_host": "r1", "ifName": "Gi0/1", "empty": "", "none": None}, "fields": {"ifInOctets": "123", "ifOperStatus": 1, "descr": "up", "flag": True}}
series = mod.prometheus_series([rec])
check("prometheus_series: 数値の field だけ（文字列は落とす、bool は 1/0）、ms は ts × 1000、空のタグは落とす",
      len(series) == 3 and all(ms == 1700000000500 for _, _, ms in series)
      and sorted(v for _, v, _ in series) == [1.0, 1.0, 123.0]
      and all(dict(l)["agent_host"] == "r1" and "empty" not in dict(l) and "none" not in dict(l) for l, _, _ in series)
      and sorted(dict(l)["__name__"] for l, _, _ in series) == ["snmp_interface_flag", "snmp_interface_ifInOctets", "snmp_interface_ifOperStatus"])
check("prometheus_series: トピックでは絞らない（購読で絞っている）", len(mod.prometheus_series([dict(rec, topic="cpu")])) == 3)
check("prometheus_series: labels は名前順のリスト（Prometheus はソート済みを要求する）", all(l == sorted(l) for l, _, _ in series))
_ord = mod.prometheus_series([dict(rec, ts=1700000002.0, fields={"a": 2}), dict(rec, ts=1700000001.0, fields={"a": 1, "b": 1}), dict(rec, ts=1700000002.0, fields={"a": 3})])
check("prometheus_series: サンプルは時刻の順（同じ系列が 1 バッチに逆順で来ても AMP が out-of-order で拒まない）。同じ時刻なら元の順",
      [(dict(l)["__name__"][-1], v, ms) for l, v, ms in _ord] == [("a", 1.0, 1700000001000), ("b", 1.0, 1700000001000), ("a", 2.0, 1700000002000), ("a", 3.0, 1700000002000)])
# cycle 002: gNMI の BGP / IS-IS の文字列の状態を 1 / 0 にし、sysName の無いレコードに device map で機器名を足す
_dm = mod.parse_device_map(" 203.0.113.31 = dc1-leaf-01 ,203.0.113.32=dc1-leaf-02,bad,=x,y=")
check("parse_device_map: 別名=機器名,… を {別名（小文字）: 機器名}。= の無い要素と空の側は捨てる",
      _dm == {"203.0.113.31": "dc1-leaf-01", "203.0.113.32": "dc1-leaf-02"} and mod.parse_device_map("") == {} and mod.parse_device_map(None) == {}
      and mod.parse_device_map("Leaf1=dc1-leaf-01") == {"leaf1": "dc1-leaf-01"})
_t = {"source": "203.0.113.31", "peer_address": "10.255.0.1"}
check("with_sysname: sysName が無ければ source を引いて足した写し。表に無い・sysName がある・表が空ならそのまま",
      mod.with_sysname(_t, _dm) == dict(_t, sysName="dc1-leaf-01") and "sysName" not in _t
      and mod.with_sysname({"source": "203.0.113.99"}, _dm) == {"source": "203.0.113.99"}
      and mod.with_sysname({"source": "203.0.113.31", "sysName": "x"}, _dm)["sysName"] == "x"
      and mod.with_sysname({"source": "203.0.113.31", "sysName": ""}, _dm)["sysName"] == "dc1-leaf-01"
      and mod.with_sysname(_t, {}) is _t and mod.with_sysname(_t, None) is _t
      and mod.with_sysname({"source": " 203.0.113.31 "}, _dm)["sysName"] == "dc1-leaf-01")
check("with_sysname(fallback_source=True): 表に無い（表が空・無いときも）source はそのまま sysName にする。source も無ければそのまま",
      mod.with_sysname({"source": " 203.0.113.99 "}, _dm, fallback_source=True) == {"source": " 203.0.113.99 ", "sysName": "203.0.113.99"}
      and mod.with_sysname(_t, {}, fallback_source=True) == dict(_t, sysName="203.0.113.31")
      and mod.with_sysname(_t, None, fallback_source=True) == dict(_t, sysName="203.0.113.31")
      and mod.with_sysname(_t, _dm, fallback_source=True)["sysName"] == "dc1-leaf-01"
      and mod.with_sysname({"source": "203.0.113.31", "sysName": "x"}, _dm, fallback_source=True)["sysName"] == "x"
      and mod.with_sysname({"agent_host": "r1"}, _dm, fallback_source=True) == {"agent_host": "r1"} and "sysName" not in _t)
def _gnmi(meas, field, value, **tags):
    return {"ts": 1700000000.0, "topic": "gnmi", "measurement": meas, "agent_host": "", "host": "h", "tags": dict({"source": "203.0.113.31"}, **tags), "fields": {field: value}}
_bgp = mod.prometheus_series([_gnmi("bgp_neighbor", "session_state", "established", peer_address="10.255.0.1"),
                              _gnmi("bgp_neighbor", "session_state", "active", peer_address="10.255.0.2")], _dm)
check("prometheus_series: bgp_neighbor の session_state は snmp_bgp_neighbor_session_up（established が 1、ほかは 0）で、sysName が機器名",
      [(dict(l)["__name__"], dict(l)["peer_address"], v) for l, v, _ in _bgp]
      == [("snmp_bgp_neighbor_session_up", "10.255.0.1", 1.0), ("snmp_bgp_neighbor_session_up", "10.255.0.2", 0.0)]
      and all(dict(l)["sysName"] == "dc1-leaf-01" and dict(l)["source"] == "203.0.113.31" for l, _, _ in _bgp))
_isis = mod.prometheus_series([_gnmi("isis_interface", "oper_state", v, interface_name="ethernet-1/1.0") for v in ("up", "DOWN", " Up ")], _dm)
check("prometheus_series: isis_interface の oper_state は snmp_isis_interface_oper_up（up が 1、ほかは 0。大文字小文字と前後の空白は見ない）",
      [(dict(l)["__name__"], v) for l, v, _ in _isis] == [("snmp_isis_interface_oper_up", 1.0), ("snmp_isis_interface_oper_up", 0.0), ("snmp_isis_interface_oper_up", 1.0)]
      and all(dict(l)["interface_name"] == "ethernet-1/1.0" for l, _, _ in _isis))
check("prometheus_series: 表に無い文字列の field（ほかの measurement の session_state、evpn_es の oper_state）は系列にならない",
      mod.prometheus_series([_gnmi("evpn_es", "oper_state", "up"), _gnmi("isis_interface", "session_state", "up"), _gnmi("bgp_neighbor", "oper_state", "up")], _dm) == [])
check("prometheus_series: 表の field でも文字列でなければ表を引かない（数値はそのまま）",
      [(dict(l)["__name__"], v) for l, v, _ in mod.prometheus_series([_gnmi("bgp_neighbor", "session_state", 6)], _dm)] == [("snmp_bgp_neighbor_session_state", 6.0)])
check("prometheus_series: 表に無い IP では sysName を足さない。devmap を渡さなければ今までどおり",
      all("sysName" not in dict(l) for l, _, _ in mod.prometheus_series([_gnmi("bgp_neighbor", "session_state", "established", source="203.0.113.99")], _dm))
      and all("sysName" not in dict(l) for l, _, _ in mod.prometheus_series([_gnmi("bgp_neighbor", "session_state", "established")]))
      and mod.prometheus_series([rec], _dm) == series)

# ---- トピックを起動時に作る（無いトピックを購読すると offset 読みで落ちる。2026-09-27）
check("all_topics: 格納先が読むトピックの和（重複なし、引数の順）",
      mod.all_topics(mod.parse_args(base + ["--sinks", "opensearch", "--opensearch-endpoint", "https://o"])) == ["traps", "logs"]
      and mod.all_topics(mod.parse_args(base + ["--sinks", "prometheus,opensearch", "--prometheus-url", "https://p/api/v1/remote_write", "--opensearch-endpoint", "https://o",
                                                 "--metric-topics", "metrics,gnmi", "--log-topics", "gnmi,traps,logs"])) == ["metrics", "gnmi", "traps", "logs"]
      and mod.all_topics(mod.parse_args(base + ["--sinks", "prometheus", "--prometheus-url", "https://p/api/v1/remote_write"])) == ["metrics", "gnmi", "mdt"])


class _Fut:
    def __init__(self, v=None, err=None): self.v, self.err = v, err
    def get(self):
        if self.err: raise Exception(self.err)
        return self.v


class _Admin:
    made = []
    def __init__(self, have, err=None): self.have, self.err, self.closed = have, err, False
    def listTopics(self): return type("R", (), {"names": lambda _s: _Fut(self.have)})()
    def createTopics(self, lst):
        _Admin.made.extend(t.name for t in lst)
        return type("R", (), {"all": lambda _s: _Fut(None, self.err)})()
    def close(self): self.closed = True


class _JVM:
    def __init__(self, admin):
        self._admin = admin
        j = self
        class Props(dict):
            def put(self, k, v): self[k] = v
        class ArrayList(list):
            def add(self, x): self.append(x)
        class NewTopic:
            def __init__(self, name, p, r): self.name = name
        self.java = type("J", (), {"util": type("U", (), {"Properties": Props, "ArrayList": ArrayList, "Optional": type("O", (), {"empty": staticmethod(lambda: None)})})})()
        self.org = type("O", (), {"apache": type("A", (), {"kafka": type("K", (), {"clients": type("C", (), {"admin": type("Ad", (), {
            "AdminClient": type("AC", (), {"create": staticmethod(lambda props: (setattr(j, "props", props), admin)[1])}), "NewTopic": NewTopic})()})()})()})()})()


_admin = _Admin({"metrics", "gnmi"})
_spark = type("S", (), {"_jvm": _JVM(_admin)})()
check("ensure_topics: 無いものだけ作って名前を返す。AdminClient は SASL_SSL / AWS_MSK_IAM で bootstrap に繋ぎ、終わったら close",
      mod.ensure_topics(_spark, "b-1:9098", ["metrics", "gnmi", "traps", "logs"]) == ["traps", "logs"] and _Admin.made == ["traps", "logs"] and _admin.closed
      and _spark._jvm.props["bootstrap.servers"] == "b-1:9098" and _spark._jvm.props["security.protocol"] == "SASL_SSL" and _spark._jvm.props["sasl.mechanism"] == "AWS_MSK_IAM")
_Admin.made = []
_admin2 = _Admin({"metrics", "gnmi", "traps", "logs"})
check("ensure_topics: 全部あれば作らない（createTopics を呼ばない）",
      mod.ensure_topics(type("S", (), {"_jvm": _JVM(_admin2)})(), "b", ["traps", "logs"]) == [] and _Admin.made == [] and _admin2.closed)
_admin3 = _Admin(set(), err="org.apache.kafka.common.errors.TopicExistsException: Topic 'traps' already exists.")
check("ensure_topics: 同時に作られて TopicExistsException になっても先へ進む", mod.ensure_topics(type("S", (), {"_jvm": _JVM(_admin3)})(), "b", ["traps"]) == ["traps"])
_admin4 = _Admin(set(), err="org.apache.kafka.common.errors.TopicAuthorizationException: Not authorized")
try:
    mod.ensure_topics(type("S", (), {"_jvm": _JVM(_admin4)})(), "b", ["traps"]); _raised = False
except Exception: _raised = True
check("ensure_topics: TopicExists 以外の失敗は上げる（権限が無いのを黙って通さない）。close はする", _raised and _admin4.closed)

# ---- Splunk HEC（Spark から直接。2026-09-26）
check("splunk_hec_url: 末尾の / を除き、/services/collector/event を足す（すでに付いていればそのまま、/services/collector なら /event を足す）",
      mod.splunk_hec_url("https://s:8088") == "https://s:8088/services/collector/event"
      and mod.splunk_hec_url("https://s:8088/") == "https://s:8088/services/collector/event"
      and mod.splunk_hec_url("https://s:8088/services/collector") == "https://s:8088/services/collector/event"
      and mod.splunk_hec_url("https://s:8088/services/collector/event/") == "https://s:8088/services/collector/event")
_ev = [json.loads(x) for x in mod.splunk_events([rec, dict(rec, host="", agent_host="", measurement="", topic="traps")], "netops")]
check("splunk_events: 1 レコードが 1 行の JSON。time は ts、host は host → agent_host → unknown、source は telegraf:<measurement>、sourcetype は netops:<topic>、index は渡したとき",
      len(_ev) == 2 and _ev[0]["time"] == 1700000000.5 and _ev[0]["host"] == "h" and _ev[0]["source"] == "telegraf:interface"
      and _ev[0]["sourcetype"] == "netops:metrics" and _ev[0]["index"] == "netops"
      and _ev[1]["host"] == "unknown" and _ev[1]["source"] == "telegraf:unknown" and _ev[1]["sourcetype"] == "netops:traps")
check("splunk_events: event に topic / measurement / agent_host / tags / fields。数値の文字列は数値に、それ以外はそのまま",
      _ev[0]["event"]["topic"] == "metrics" and _ev[0]["event"]["measurement"] == "interface" and _ev[0]["event"]["agent_host"] == "r1"
      and _ev[0]["event"]["tags"]["ifName"] == "Gi0/1" and _ev[0]["event"]["fields"]["ifInOctets"] == 123 and _ev[0]["event"]["fields"]["descr"] == "up"
      and _ev[0]["event"]["fields"]["flag"] is True)
check("splunk_events: index を渡さなければ index キーが無い（token の既定の index）", "index" not in json.loads(mod.splunk_events([rec])[0]))
check("splunk_events: 1 行に改行が無い（HEC は連結した JSON を受ける）", all("\n" not in x for x in mod.splunk_events([rec])))
_posts = []
_orig_post = mod.http_post
mod.http_post = lambda url, body, headers, context=None: (_posts.append((url, body, headers, context)), (200, "ok"))[1]
try:
    _send = mod.make_splunk_sender("https://s:8088/", "tok", "netops")
    _ok_dropped = _send([rec] * (mod.BULK_SIZE + 1))
finally:
    mod.http_post = _orig_post
check("make_splunk_sender: HEC の URL に Authorization: Splunk <token> で POST し、BULK_SIZE ごとに分ける、TLS は既定で検証（context 無し）",
      len(_posts) == 2 and all(u == "https://s:8088/services/collector/event" for u, _, _, _ in _posts)
      and all(h["Authorization"] == "Splunk tok" and h["Content-Type"] == "application/json" for _, _, h, _ in _posts)
      and _posts[0][1].count(b"\n") == mod.BULK_SIZE - 1 and _posts[1][1].count(b"\n") == 0
      and all(c is None for _, _, _, c in _posts) and _ok_dropped == 0)
check("make_splunk_sender: skip_verify なら検証しない SSL context を渡す", (lambda: (
    setattr(mod, "http_post", lambda url, body, headers, context=None: (_posts.append((url, body, headers, context)), (200, "ok"))[1]),
    _posts.clear(), mod.make_splunk_sender("https://s:8088", "tok", skip_verify=True)([rec]), setattr(mod, "http_post", _orig_post),
    len(_posts) == 1 and _posts[0][3] is not None and _posts[0][3].verify_mode == ssl.CERT_NONE))()[-1])
mod.http_post = lambda url, body, headers, context=None: (400, '{"text":"Invalid token"}')
try:
    _dropped = mod.make_splunk_sender("https://s:8088", "tok")([rec] * 3)
finally:
    mod.http_post = _orig_post
check("make_splunk_sender: HEC が 4xx を返したらそのまとまりを捨てて続け（例外にしない。ジョブを止めない）、捨てた件数を返す", _dropped == 3)
check("build: splunk は起動時に SSM から token を読み（WithDecryption。環境変数 SPLUNK_HEC_TOKEN が無ければ）、make_splunk_sender で http_query に流す",
      re.search(r'elif s == "splunk":\s*\n(\s*#[^\n]*\n)*\s*token = splunk_token\(args\.splunk_token_parameter, args\.region\)\s*\n\s*queries\.append\(http_query\(rows, s, args\.checkpoint, make_splunk_sender\(args\.splunk_hec_url, token, args\.splunk_index, args\.splunk_skip_verify\)\)\)', src) is not None
      and re.search(r'def splunk_token\(parameter, region\):[\s\S]*?return os\.environ\.get\("SPLUNK_HEC_TOKEN"\) or read_ssm_parameter\(parameter, region\)\n', src) is not None
      and re.search(r'def read_ssm_parameter\(name, region\):[\s\S]*?get_parameter\(Name=name, WithDecryption=True\)', src) is not None)
check("http_post は context（SSL）を urlopen に渡せる", re.search(r'def http_post\(url, body, headers, context=None\)', src) is not None and "context=context" in src)

# ---- HTTP の格納先へ送る所（--http-send。既定 driver = collect して driver が送る、executor = foreachPartition で executor が送る。2026-10-04）
import subprocess
check("HTTP_SEND は driver / executor", mod.HTTP_SEND == ("driver", "executor"))
_sp = ["--sinks", "prometheus", "--prometheus-url", "https://p/api/v1/remote_write"]
check("parse_args: --http-send の既定は driver（引数を渡さなければ今のまま）", mod.parse_args(base + _sp).http_send == "driver")
check("parse_args: --http-send executor を受ける", mod.parse_args(base + _sp + ["--http-send", "executor"]).http_send == "executor")
check("parse_args: --http-send は driver / executor だけ（大文字、他の値、空は 2）",
      all(parse_error(base + _sp + ["--http-send", v]) == 2 for v in ("foo", "Executor", "DRIVER", "")))
check("variable http_send は既定 driver で、driver / executor だけ通す",
      re.search(r'variable "http_send" \{\s*description[^\n]*\n\s*type\s*=\s*string\s*\n\s*default\s*=\s*"driver"\s*\n\s*validation \{\s*\n'
                r'\s*condition\s*=\s*contains\(\["driver", "executor"\], var\.http_send\)', tf) is not None)
check("job_driver は executor のときだけ --http-send を渡す（既定の driver ではジョブの引数が変わらず、up.sh が起こし直さない）。iceberg のジョブには渡さない",
      re.search(r'\[for a in \["--http-send",\s*var\.http_send\] : a if var\.http_send != "driver" && job != "iceberg"\]', args_block.group(1)) is not None)
check("up.sh は HTTP_SEND（既定 driver）を何かを作る前に確かめ、http_send で analytics に渡す",
      'HTTP_SEND="${HTTP_SEND:-driver}"' in up
      and up.index('case "$HTTP_SEND" in driver | executor) ;;') < up.index("\ntf_apply base/ecr")
      and '\n  tf_apply pipeline/analytics "${ANALYTICS_VARS[@]}" -var "http_send=$HTTP_SEND"   #' in up)
# HTTP_SEND の判定の 2 行を up.sh から切り出して、bash で実際に動かす
_hsblk = "\n".join(up[up.index('HTTP_SEND="${HTTP_SEND:-driver}"'):].split("\n")[:2]) + "\n"
def _http_send(**env):
    r = subprocess.run(["bash", "-c", 'die() { echo "DIE: $*"; exit 1; }\n' + _hsblk + 'echo "OUT: $HTTP_SEND"'], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], **env})
    return r.returncode, r.stdout.strip()
check("HTTP_SEND が無いか空なら driver、driver / executor はそのまま",
      _http_send() == (0, "OUT: driver") and _http_send(HTTP_SEND="") == (0, "OUT: driver")
      and _http_send(HTTP_SEND="driver") == (0, "OUT: driver") and _http_send(HTTP_SEND="executor") == (0, "OUT: executor"))
check("HTTP_SEND が driver / executor 以外（大文字、1、他の値）なら止まる",
      all(_http_send(HTTP_SEND=v)[0] == 1 and "HTTP_SEND は driver か executor" in _http_send(HTTP_SEND=v)[1] for v in ("Executor", "DRIVER", "1", "both")))
check("deploy-env.sh は HTTP_SEND を読めるキーに持ち、deploy.env.example は既定の #HTTP_SEND=driver を書く",
      re.search(r'(?<![A-Z_])HTTP_SEND(?![A-Z_])', open(os.path.join(ROOT, "ops", "deploy-env.sh"), encoding="utf-8").read()) is not None
      and re.search(r"^#HTTP_SEND=driver$", env_example, re.M) is not None)

def _row(ts, v=1):
    """Spark の Row の代わり（row_to_record は asDict が無ければ dict(row) で読む）"""
    return {"ts": ts, "topic": "metrics", "measurement": "interface", "agent_host": "r1", "host": "h",
            "tags_json": '{"agent_host":"r1","ifName":"Gi0/1"}', "fields_json": json.dumps({"ifInOctets": v})}

def _stderr(fn):
    """fn() を呼んで (戻り値, stderr) を返す"""
    saved = sys.stderr
    sys.stderr = io.StringIO()
    try:
        return fn(), sys.stderr.getvalue()
    finally:
        sys.stderr = saved

check("partition_id: pyspark が無いかタスクの外なら -1", mod.partition_id() == -1)
_got = []
_n, _err = _stderr(lambda: mod.send_partition("prometheus", _got.append, 7, iter([_row(3.0), _row(1.0), _row(2.0)])))
check("send_partition: 行のイテレータを row_to_record にし、ts の順に並べて sender に 1 回で渡し、(行数, 捨てた数) を返す（sender が None を返せば 0）",
      _n == (3, 0) and len(_got) == 1 and [r["ts"] for r in _got[0]] == [1.0, 2.0, 3.0] and _got[0][0]["fields"] == {"ifInOctets": 1})
check("send_partition: パーティションごとに batch / partition / 行数をログに出す", "[snmp_sinks] prometheus: batch 7 partition -1 で 3 行を送った" in _err)
_got.clear()
_n, _err = _stderr(lambda: mod.send_partition("prometheus", _got.append, 7, iter([])))
check("send_partition: 空のパーティションは送らず、ログも出さず (0, 0)", _n == (0, 0) and _got == [] and _err == "")

# executor で Prometheus に送る: BULK_SIZE ごとのまとまり、4xx は捨てて続ける、ts の順（http_post と SigV4 は差し替える）
_posts = []
_seen = []
_orig_sig, _orig_series = mod.sigv4_headers, mod.prometheus_series
mod.sigv4_headers = lambda method, url, body, service, region, headers: dict(headers, Authorization=f"AWS4-HMAC-SHA256 {service} {region}")
mod.prometheus_series = lambda records, *a: (_seen.append([r["ts"] for r in records]), _orig_series(records, *a))[1]
mod.http_post = lambda url, body, headers, context=None: (_posts.append((url, body, headers)), (400, b"out of order sample") if len(_posts) == 1 else (200, b""))[1]
try:
    _rows = [_row(1700000000.0 + i, i) for i in range(mod.BULK_SIZE + 1)][::-1]
    _n, _err = _stderr(lambda: mod.send_partition("prometheus", mod.make_prometheus_sender("https://p/api/v1/remote_write", "ap-northeast-1"), 1, iter(_rows)))
finally:
    mod.http_post, mod.sigv4_headers, mod.prometheus_series = _orig_post, _orig_sig, _orig_series
check("send_partition + prometheus: 並べてから系列にし、BULK_SIZE サンプルごとに aps の SigV4 で remote write に送る",
      _n == (mod.BULK_SIZE + 1, mod.BULK_SIZE) and _seen == [sorted(1700000000.0 + i for i in range(mod.BULK_SIZE + 1))]
      and len(_posts) == 2 and all(u == "https://p/api/v1/remote_write" and h["Authorization"] == "AWS4-HMAC-SHA256 aps ap-northeast-1"
                                   and h["Content-Encoding"] == "snappy" for u, _, h in _posts))
check("send_partition + prometheus: 400 のまとまりは捨てて次を送り（例外にしない。タスクを落とさない）、捨てたサンプルの数をログに出して返す",
      f"prometheus: remote write が 400 を返した。{mod.BULK_SIZE} サンプルを捨てる" in _err
      and f"partition -1 で {mod.BULK_SIZE + 1} 行を送り、{mod.BULK_SIZE} サンプルを捨てた（4xx など）" in _err)

# executor で Splunk に送る: token は executor が送るたびに SSM から読む（driver から運ばない）
_ssm = []
_orig_ssm = mod.read_ssm_parameter
mod.read_ssm_parameter = lambda name, region: (_ssm.append((name, region)), "tok-from-ssm")[1]
mod.http_post = lambda url, body, headers, context=None: (_posts.append((url, body, headers, context)), (403, b'{"text":"Invalid token","code":4}'))[1]
_posts.clear()
try:
    _send = mod.make_splunk_sender_on_executor("https://s:8088", "/p/splunk/hec-token", "ap-northeast-1", "netops", True)
    _ssm_at_make = list(_ssm)
    _cells = [c.cell_contents for c in (_send.__closure__ or ())]
    _n, _err = _stderr(lambda: mod.send_partition("splunk", _send, 3, iter([_row(2.0), _row(1.0)])))
finally:
    mod.http_post, mod.read_ssm_parameter = _orig_post, _orig_ssm
check("make_splunk_sender_on_executor: 作るときは SSM を読まず、持つのは URL / パラメータ名 / region / index / skip_verify の文字列と bool だけ（token も SSL の context も executor へ運ばない）",
      _ssm_at_make == [] and sorted(map(repr, _cells)) == sorted(map(repr, ["https://s:8088", "/p/splunk/hec-token", "ap-northeast-1", "netops", True])))
check("make_splunk_sender_on_executor: 送るときに SSM から token を読み、Authorization: Splunk <token> で HEC に送る。index と skip_verify（検証しない context）も今の sender と同じ",
      _ssm == [("/p/splunk/hec-token", "ap-northeast-1")] and len(_posts) == 1 and _posts[0][0] == "https://s:8088/services/collector/event"
      and _posts[0][2]["Authorization"] == "Splunk tok-from-ssm" and _posts[0][3] is not None and _posts[0][3].verify_mode == ssl.CERT_NONE
      and [json.loads(x)["time"] for x in _posts[0][1].decode().split("\n")] == [1.0, 2.0] and json.loads(_posts[0][1].decode().split("\n")[0])["index"] == "netops")
check("make_splunk_sender_on_executor: 4xx は捨てて続け（例外にしない）、ログに token の値を出さない",
      _n == (2, 2) and "splunk: HEC が 403 を返した" in _err and "partition -1 で 2 行を送り、2 件を捨てた（4xx など）" in _err and "tok-from-ssm" not in _err)
check("executor へ運ぶ opensearch / prometheus の sender が持つのは文字列と辞書（と None）だけ（pickle できないものを持たない）",
      all(isinstance(c.cell_contents, (str, dict, type(None))) for f in (mod.make_opensearch_sender("https://o", "snmp-logs", "ap-northeast-1"),
                                                             mod.make_prometheus_sender("https://p/api/v1/remote_write", "ap-northeast-1"))
          for c in (f.__closure__ or ())))

# opensearch の sender: 4xx のまとまりと、_bulk の応答で入らなかった（errors）ドキュメントを捨てた数として返す
_os_res = json.dumps({"errors": True, "items": [{"index": {"status": 400, "error": {"type": "mapper_parsing_exception"}}},
                                                 {"index": {"status": 201}}, {"index": {"status": 400, "error": {"type": "x"}}}]})
mod.sigv4_headers = lambda method, url, body, service, region, headers: dict(headers)
mod.http_post = lambda url, body, headers, context=None: (_posts.append(url), (400, b"bad") if len(_posts) == 1 else (200, _os_res.encode()))[1]
_posts.clear()
try:
    _dropped, _err = _stderr(lambda: mod.make_opensearch_sender("https://o", "snmp-logs", "ap-northeast-1")([rec] * (mod.BULK_SIZE + 3)))
finally:
    mod.http_post, mod.sigv4_headers = _orig_post, _orig_sig
check("make_opensearch_sender: 4xx のまとまり（BULK_SIZE 件）と、応答の errors で入らなかった 2 件を、捨てた数として返す",
      len(_posts) == 2 and _dropped == mod.BULK_SIZE + 2 and "opensearch: 2 件が入らなかった" in _err)

# http_query を Spark 無しで動かす（writeStream の鎖と、マイクロバッチの DataFrame を差し替える）
class _Writer:
    def __init__(self): self.calls = []
    def __getattr__(self, k): return lambda *a, **kw: (self.calls.append((k, a)), self)[1]


class _Rows:
    def __init__(self): self.writeStream = _Writer()


class _Acc:
    def __init__(self, v): self.value = v
    def add(self, n): self.value += n


class _ExecutorBatch:
    """foreachPartition はパーティションごとに行のイテレータで f を呼ぶ。collect は呼ばれたら落とす。
    repartition(n, 列…) は列の値が同じ行を同じパーティションに集め直す（Spark のハッシュ分割の代わり。どこに入るかは crc32）"""
    def __init__(self, parts, repartitioned=None):
        self.parts = parts
        self.repartitioned = repartitioned
        self.rdd = type("RDD", (), {"getNumPartitions": lambda _self: len(parts)})()
        self.sparkSession = type("SS", (), {"sparkContext": type("SC", (), {"accumulator": staticmethod(lambda v: _Acc(v))})()})()
    def collect(self): raise AssertionError("executor の分岐で collect が呼ばれた")
    def repartition(self, n, *cols):
        out = [[] for _ in range(n)]
        for p in self.parts:
            for r in p:
                out[zlib.crc32(json.dumps([r[c] for c in cols]).encode()) % n].append(r)
        return _ExecutorBatch(out, (n,) + cols)
    def foreachPartition(self, f):
        for p in self.parts:
            f(iter(p))


class _DriverBatch:
    def __init__(self, rows): self.rows = rows
    def collect(self): return self.rows
    def foreachPartition(self, f): raise AssertionError("driver の分岐で foreachPartition が呼ばれた")


def _query(*http_send, name="prometheus", sender=None):
    r = _Rows()
    mod.http_query(r, name, "s3://b/analytics/checkpoint/u/", sender or _got.append, *http_send)
    return r.writeStream.calls


def _row_s(ts, port):
    """系列（ifName）を変えた行"""
    return dict(_row(ts), tags_json=json.dumps({"agent_host": "r1", "ifName": port}, separators=(",", ":")))


_got.clear()
_calls = _query("executor", name="opensearch")
_fb = [a[0] for k, a in _calls if k == "foreachBatch"][0]
_b = _ExecutorBatch([[_row(3.0), _row(1.0)], [], [_row(2.0)]])
_b.repartition = lambda *a: (_ for _ in ()).throw(AssertionError("opensearch で repartition が呼ばれた"))
_, _err = _stderr(lambda: _fb(_b, 5))
check("http_query executor（opensearch）: collect せず、分け直さずに foreachPartition でパーティションごとに sender を呼ぶ（中は ts の順、空のパーティションは呼ばない）",
      [[r["ts"] for r in c] for c in _got] == [[1.0, 3.0], [2.0]])
check("http_query executor: 送った行数を accumulator で driver に戻してログに出す", "[snmp_sinks] opensearch: batch 5 で 3 行を executor から送った" in _err)
# prometheus: Kafka の 2 つのパーティションに同じ系列（Gi0/1 と Gi0/2）が時刻の前後したまま散らばっている
_got.clear()
_calls = _query("executor")
_fb = [a[0] for k, a in _calls if k == "foreachBatch"][0]
_seen_b = []
_orig_rep = _ExecutorBatch.repartition
_ExecutorBatch.repartition = lambda self, n, *cols: (_seen_b.append((n,) + cols), _orig_rep(self, n, *cols))[1]
try:
    _, _err = _stderr(lambda: _fb(_ExecutorBatch([[_row_s(3.0, "Gi0/1"), _row_s(1.0, "Gi0/2")], [_row_s(1.0, "Gi0/1"), _row_s(2.0, "Gi0/2")],
                                                  [_row_s(2.0, "Gi0/1")]]), 8))
finally:
    _ExecutorBatch.repartition = _orig_rep
_by_series = {}
for _c in _got:
    for _r in _c:
        _by_series.setdefault(_r["tags"]["ifName"], set()).add(id(_c))
check("http_query executor（prometheus）: 送る前に measurement と tags_json で、元のパーティション数のまま分け直す",
      _seen_b == [(3, "measurement", "tags_json")] and tuple(mod.SERIES_COLUMNS) == ("measurement", "tags_json"))
check("http_query executor（prometheus）: 同じ系列の行は 1 回の sender にまとまり、ts の順に並ぶ（2 つのタスクが別々に送らない）",
      all(len(v) == 1 for v in _by_series.values()) and set(_by_series) == {"Gi0/1", "Gi0/2"}
      and all([r["ts"] for r in c] == sorted(r["ts"] for r in c) for c in _got)
      and sorted(r["ts"] for c in _got for r in c if r["tags"]["ifName"] == "Gi0/1") == [1.0, 2.0, 3.0]
      and "prometheus: batch 8 で 5 行を executor から送った" in _err)
# 4xx で捨てた数: sender が返した数を accumulator で driver に戻し、driver のログに出す
_fb = [a[0] for k, a in _query("executor", sender=lambda recs: 2) if k == "foreachBatch"][0]
_, _err = _stderr(lambda: _fb(_ExecutorBatch([[_row(3.0), _row(1.0)], [], [_row(2.0)]]), 5))
check("http_query executor: 4xx などで捨てた数も accumulator で driver に戻し、driver のログ（CloudWatch Logs）に出す",
      "[snmp_sinks] prometheus: batch 5 で 3 行を executor から送り、2 サンプルを捨てた（4xx など）。理由は executor の stderr（S3 の logs）" in _err)
_fb = [a[0] for k, a in _query("driver", name="splunk", sender=lambda recs: 1) if k == "foreachBatch"][0]
_, _err = _stderr(lambda: _fb(_DriverBatch([_row(3.0), _row(1.0)]), 6))
check("http_query driver: 捨てた数もログに出す（splunk / opensearch は件）", "[snmp_sinks] splunk: batch 6 で 2 行を送り、1 件を捨てた（4xx など）" in _err)
check("http_query executor: クエリの名前、checkpoint、トリガーは driver のときと同じ",
      _calls[0] == ("queryName", ("prometheus",)) and ("option", ("checkpointLocation", "s3://b/analytics/checkpoint/u/prometheus/")) in _calls
      and [k for k, _ in _calls] == [k for k, _ in _query()])
for _hs in ((), ("driver",)):
    _got.clear()
    _fb = [a[0] for k, a in _query(*_hs) if k == "foreachBatch"][0]
    _, _err = _stderr(lambda: _fb(_DriverBatch([_row(3.0), _row(1.0)]), 6))
    check(f"http_query {'既定' if not _hs else 'driver'}: collect して ts の順に並べ、1 回で sender に渡す",
          [[r["ts"] for r in c] for c in _got] == [[1.0, 3.0]] and "[snmp_sinks] prometheus: batch 6 で 2 行を送った" in _err)
_inner = {n.name: n for n in ast.walk(funcs["http_query"]) if isinstance(n, ast.FunctionDef)}
_attrs = lambda f: {n.attr for n in ast.walk(f) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(f) if isinstance(n, ast.Name)}
check("executor の分岐（each_batch_on_executors と send_partition）は行を driver に集めない（collect / toPandas / toLocalIterator / take / head を呼ばない）",
      not ({"collect", "toPandas", "toLocalIterator", "take", "head"} & (_attrs(_inner["each_batch_on_executors"]) | _attrs(funcs["send_partition"])))
      and "foreachPartition" in _attrs(_inner["each_batch_on_executors"]) and "collect" in _attrs(_inner["each_batch"]))

# build: どの HTTP の格納先にも http_send を渡す。splunk は executor のとき token を持たない sender にする
_hq = []
_orig_build = (mod.read_rows, mod.read_ssm_parameter, mod.http_query)
mod.read_rows = lambda spark, bootstrap, topics, *a: _Rows()
mod.read_ssm_parameter = lambda name, region: (_ssm.append(name), "tok-from-ssm")[1]
mod.http_query = lambda rows, name, checkpoint, sender, http_send="driver": (_hq.append((name, sender, http_send)), name)[1]
_ba = base + ["--sinks", "splunk,prometheus,opensearch", "--splunk-hec-url", "https://s:8088", "--splunk-token-parameter", "/p/t",
              "--prometheus-url", "https://p/api/v1/remote_write", "--opensearch-endpoint", "https://o"]
try:
    _ssm.clear()
    mod.build(None, mod.parse_args(_ba + ["--http-send", "executor"]))
    _exec, _ssm_exec = list(_hq), list(_ssm)
    _hq.clear(), _ssm.clear()
    mod.build(None, mod.parse_args(_ba))
    _drv, _ssm_drv = list(_hq), list(_ssm)
finally:
    mod.read_rows, mod.read_ssm_parameter, mod.http_query = _orig_build
check("build executor: splunk / prometheus / opensearch に executor を渡す。splunk は起動時に SSM を 1 回読んで確かめる（読めなければ起動で落ちる）が、sender は token を持たない",
      [(n, h) for n, _, h in _exec] == [("splunk", "executor"), ("prometheus", "executor"), ("opensearch", "executor")] and _ssm_exec == ["/p/t"]
      and "tok-from-ssm" not in repr([c.cell_contents for c in (_exec[0][1].__closure__ or ())]))
check("build driver（既定）: 今のまま（splunk は起動時に読んだ token を持つ sender、http_send は driver）",
      [(n, h) for n, _, h in _drv] == [("splunk", "driver"), ("prometheus", "driver"), ("opensearch", "driver")] and _ssm_drv == ["/p/t"]
      and "Splunk tok-from-ssm" in repr([c.cell_contents for c in (_drv[0][1].__closure__ or ())]))
check("main は HTTP の送信先（driver / executor）を起動時のログに出す", '+ f"。HTTP の送信: {args.http_send}"' in src)

# ---- Kafka の 1 回のトリガーに 1 つのクエリが読む件数の上限（--max-offsets-per-trigger と格納先ごとの --max-offsets-per-trigger-by-sink。2026-10-04）
check("MAX_OFFSETS_PER_TRIGGER は 10000", mod.MAX_OFFSETS_PER_TRIGGER == 10000)
_a = mod.parse_args(base + _sp)
check("parse_args: 上限の既定は 10000、格納先ごとの値は無し（どの格納先も 10000）",
      _a.max_offsets_per_trigger == 10000 and _a.max_offsets_per_trigger_by_sink == {}
      and all(mod.max_offsets(_a, s) == 10000 for s in mod.SINKS))
check("parse_args: --max-offsets-per-trigger 0（上限なし）と 2500 を受ける",
      mod.parse_args(base + _sp + ["--max-offsets-per-trigger", "0"]).max_offsets_per_trigger == 0
      and mod.parse_args(base + _sp + ["--max-offsets-per-trigger", "2500"]).max_offsets_per_trigger == 2500)
_a = mod.parse_args(base + _sp + ["--max-offsets-per-trigger-by-sink", "splunk=2000, prometheus = 5000,opensearch=0,"])
check("parse_args: --max-offsets-per-trigger-by-sink は <格納先>=<件数> のカンマ区切り（空白と空の項目は無視）。書いた格納先だけ上書きし、0 はその格納先だけ上限なし",
      _a.max_offsets_per_trigger_by_sink == {"splunk": 2000, "prometheus": 5000, "opensearch": 0}
      and [mod.max_offsets(_a, s) for s in ("iceberg", "splunk", "prometheus", "opensearch")] == [10000, 2000, 5000, 0])
_a = mod.parse_args(base + _sp + ["--max-offsets-per-trigger", "0", "--max-offsets-per-trigger-by-sink", "splunk=5"])
check("max_offsets: 共通 0 でも格納先ごとの値があればそれを使う", mod.max_offsets(_a, "splunk") == 5 and mod.max_offsets(_a, "iceberg") == 0)
check("parse_args: 上限は 0 以上の整数だけ（負、小数、文字、空は 2）",
      all(parse_error(base + _sp + ["--max-offsets-per-trigger", v]) == 2 for v in ("-1", "1.5", "abc", "", "1e4")))
check("parse_args: 格納先ごとの値は知っている格納先と 0 以上の整数だけ（s3 は名前が違う、= が無い、負、小数、大文字は 2）",
      all(parse_error(base + _sp + ["--max-offsets-per-trigger-by-sink", v]) == 2
          for v in ("s3=1", "splunk", "splunk=-2", "splunk=1.5", "Splunk=1", "=5", "splunk=", "splunk=1,s3=2")))

# read_rows と build を本当に動かす（pyspark の functions / types は何でも受ける偽物、spark.readStream は option を覚える偽物）
class _Any:
    def __getattr__(self, k): return self
    def __call__(self, *a, **kw): return self
    def __getitem__(self, k): return self


class _Reader:
    def __init__(self): self.opts = {}
    def format(self, f): return self
    def option(self, k, v): self.opts[k] = v; return self
    def load(self): return _Any()


class _Spark:
    """readStream は option を覚え、table(名前).schema.fields は have の列、sql は文を覚える（ensure_iceberg_columns 用）"""
    def __init__(self, have=None):
        self.readers, self.sqls, self.tables = [], [], []
        self.have = list(TABLE_COLUMNS if have is None else have)
    @property
    def readStream(self):
        self.readers.append(_Reader())
        return self.readers[-1]
    def table(self, name):
        self.tables.append(name)
        cols = [type("F", (), {"name": n})() for n in self.have]
        return type("T", (), {"schema": type("Sc", (), {"fields": cols})()})()
    def sql(self, q): self.sqls.append(q)


import types as _types
_fake_sql = _types.ModuleType("pyspark.sql")
_fake_sql.functions, _fake_sql.types = _Any(), _Any()
_saved_mods = {k: sys.modules.get(k) for k in ("pyspark", "pyspark.sql")}
_orig_mo = (mod.iceberg_query, mod.http_query, mod.read_ssm_parameter)
_b4 = base + ["--sinks", "iceberg,splunk,opensearch,prometheus", "--iceberg-table", "s3tables.netops.raw_telemetry",
              "--splunk-hec-url", "https://s:8088", "--splunk-token-parameter", "/p/t",
              "--prometheus-url", "https://p/api/v1/remote_write", "--opensearch-endpoint", "https://o"]
def _reads(*extra):
    """4 つの格納先で build し、格納先ごとに Kafka の読み取りに付いた maxOffsetsPerTrigger（無ければ None）を返す"""
    sp, args = _Spark(TABLE_COLUMNS + ADDED_COLUMNS), mod.parse_args(_b4 + list(extra))
    mod.build(sp, args)
    assert len(sp.readers) == len(args.sinks) and all(
        r.opts["subscribe"] == mod.sink_topics(s, args.metric_topics, args.log_topics) for s, r in zip(args.sinks, sp.readers))
    return {s: r.opts.get("maxOffsetsPerTrigger") for s, r in zip(args.sinks, sp.readers)}
sys.modules["pyspark"], sys.modules["pyspark.sql"] = _types.ModuleType("pyspark"), _fake_sql
mod.iceberg_query = lambda rows, table, checkpoint: "iceberg"
mod.http_query = lambda rows, name, checkpoint, sender, http_send="driver": name
mod.read_ssm_parameter = lambda name, region: "tok-from-ssm"
try:
    _sp1 = _Spark()
    mod.read_rows(_sp1, "b:9098", "metrics")
    _r_default_arg = _sp1.readers[0].opts
    _r_def = _reads()
    _r_zero = _reads("--max-offsets-per-trigger", "0")
    _r_splunk = _reads("--max-offsets-per-trigger", "10000", "--max-offsets-per-trigger-by-sink", "splunk=2000")
    _r_prom0 = _reads("--max-offsets-per-trigger-by-sink", "prometheus=0")
    _r_mixed = _reads("--max-offsets-per-trigger", "0", "--max-offsets-per-trigger-by-sink", "opensearch=300,iceberg=50000")
    # 起動時の ALTER: iceberg のクエリを組む前に足す（iceberg_query が呼ばれた時点の sql の数を覚える）
    _alter_rec = []
    mod.iceberg_query = lambda rows, table, checkpoint: _alter_rec.append(len(_sp_old.sqls)) or "iceberg"
    _sp_old = _Spark()
    _alter_out = _stderr(lambda: mod.build(_sp_old, mod.parse_args(_b4)))[1]
    _alter_at = _alter_rec[:]  # 次の build でも呼ばれるので、ここまでの分を残す
    _sp_new = _Spark(TABLE_COLUMNS + ADDED_COLUMNS)
    _new_out = _stderr(lambda: mod.build(_sp_new, mod.parse_args(_b4)))[1]
    _sp_http = _Spark()
    mod.build(_sp_http, mod.parse_args(base + ["--sinks", "opensearch,prometheus", "--prometheus-url", "https://p/api/v1/remote_write", "--opensearch-endpoint", "https://o"]))
finally:
    for _k, _v in _saved_mods.items():
        if _v is None:
            sys.modules.pop(_k, None)
        else:
            sys.modules[_k] = _v
    mod.iceberg_query, mod.http_query, mod.read_ssm_parameter = _orig_mo
check("read_rows: 4 つ目を渡さなければ maxOffsetsPerTrigger を付けない（ほかの option は今のまま）",
      "maxOffsetsPerTrigger" not in _r_default_arg and _r_default_arg["startingOffsets"] == "earliest" and _r_default_arg["subscribe"] == "metrics")
check("build 既定: どのクエリの Kafka の読み取りにも maxOffsetsPerTrigger 10000 が付く",
      _r_def == {"iceberg": "10000", "splunk": "10000", "opensearch": "10000", "prometheus": "10000"})
check("build --max-offsets-per-trigger 0: どのクエリにも maxOffsetsPerTrigger が付かない",
      _r_zero == {"iceberg": None, "splunk": None, "opensearch": None, "prometheus": None})
check("build 共通 10000 + splunk=2000: Splunk のクエリは 2000、ほかは 10000",
      _r_splunk == {"iceberg": "10000", "splunk": "2000", "opensearch": "10000", "prometheus": "10000"})
check("build prometheus=0: Prometheus のクエリだけ付かず、同じジョブの OpenSearch は 10000（クエリが別なので別の値が効く）",
      _r_prom0 == {"iceberg": "10000", "splunk": "10000", "opensearch": "10000", "prometheus": None})
check("build 共通 0 + opensearch=300,iceberg=50000: 書いた格納先だけ付き、ほかは付かない",
      _r_mixed == {"iceberg": "50000", "splunk": None, "opensearch": "300", "prometheus": None})
check("main は格納先ごとの上限（0 なら上限なし）を起動時のログに出す", "1 回 {max_offsets(args, s) or '上限なし'} 件まで" in src)
check("build iceberg: 列の足りない表（tables.tf の 8 列）には iceberg のクエリを組む前に ALTER TABLE を 1 回出し、足した列をログに出す",
      _sp_old.tables == ["s3tables.netops.raw_telemetry"] and _alter_at == [1]
      and _sp_old.sqls == ["ALTER TABLE s3tables.netops.raw_telemetry ADD COLUMNS (event_id string, kafka_topic string, kafka_partition int, kafka_offset bigint)"]
      and "iceberg: s3tables.netops.raw_telemetry に列 event_id, kafka_topic, kafka_partition, kafka_offset を足した（いまある行は null）" in _alter_out)
check("build iceberg: 列がそろっていれば ALTER も列のログも出さない（2 回目の起動から）", _sp_new.sqls == [] and "を足した" not in _new_out)
check("build: iceberg が無ければ表を見ない（HTTP の格納先のジョブは S3 Tables に触らない）", _sp_http.tables == [] and _sp_http.sqls == [])
check("ICEBERG_ADDED_COLUMNS は event_id string / kafka_topic string / kafka_partition int / kafka_offset bigint（Kafka の partition は int、offset は long）",
      mod.ICEBERG_ADDED_COLUMNS == (("event_id", "string"), ("kafka_topic", "string"), ("kafka_partition", "int"), ("kafka_offset", "bigint"))
      and [n for n, _ in mod.ICEBERG_ADDED_COLUMNS] == ADDED_COLUMNS)
_sp_part = _Spark(TABLE_COLUMNS + ["event_id", "kafka_topic"])
check("ensure_iceberg_columns: 一部だけあれば無い列だけを順に足して名前を返す",
      mod.ensure_iceberg_columns(_sp_part, "c.n.t") == ["kafka_partition", "kafka_offset"]
      and _sp_part.sqls == ["ALTER TABLE c.n.t ADD COLUMNS (kafka_partition int, kafka_offset bigint)"])
_sp_all = _Spark(TABLE_COLUMNS + ADDED_COLUMNS)
check("ensure_iceberg_columns: 全部あれば何もしない（空のリスト）", mod.ensure_iceberg_columns(_sp_all, "c.n.t") == [] and _sp_all.sqls == [])

def read_varint(b, i):
    n = shift = 0
    while True:
        c = b[i]; i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7

def decode_fields(b):
    """protobuf の (field, wire, value) を順に返す。wire 0 = varint、1 = 8 バイト、2 = 長さ付き"""
    i, out = 0, []
    while i < len(b):
        key, i = read_varint(b, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            v, i = read_varint(b, i)
        elif wire == 1:
            v = struct.unpack("<d", b[i:i + 8])[0]; i += 8
        elif wire == 2:
            n, i = read_varint(b, i)
            v = b[i:i + n]; i += n
        else:
            raise AssertionError(wire)
        out.append((num, wire, v))
    return out

wr = mod.encode_write_request([([("__name__", "snmp_x_y"), ("host", "h")], 1.5, 1700000000500)])
top = decode_fields(wr)
check("encode_write_request: WriteRequest.timeseries(1) が 1 本", [(n, w) for n, w, _ in top] == [(1, 2)])
ts_fields = decode_fields(top[0][2])
labels = [decode_fields(v) for n, w, v in ts_fields if n == 1]
samples = [decode_fields(v) for n, w, v in ts_fields if n == 2]
check("encode_write_request: labels(1) は name(1) / value(2) の文字列、samples(2) は value(1) double / timestamp(2) varint",
      [(l[0][2], l[1][2]) for l in labels] == [(b"__name__", b"snmp_x_y"), (b"host", b"h")]
      and len(samples) == 1 and samples[0] == [(1, 1, 1.5), (2, 0, 1700000000500)])
check("_varint: 0 / 127 / 128 / 300", mod._varint(0) == b"\x00" and mod._varint(127) == b"\x7f" and mod._varint(128) == b"\x80\x01" and mod._varint(300) == b"\xac\x02")
check("_field_varint: 負の int64 は 2^64 を足して 10 バイト", len(mod._field_varint(2, -1)) == 1 + 10)

def snappy_decompress(b):
    n, i = read_varint(b, 0)
    out = bytearray()
    while i < len(b):
        tag = b[i]; i += 1
        assert tag & 3 == 0, "リテラル以外は出さない"
        ln = tag >> 2
        if ln < 60:
            ln += 1
        elif ln == 60:
            ln = b[i] + 1; i += 1
        elif ln == 61:
            ln = struct.unpack("<H", b[i:i + 2])[0] + 1; i += 2
        else:
            raise AssertionError(ln)
        out += b[i:i + ln]; i += ln
    assert len(out) == n
    return bytes(out)

small = b"x" * 10
big = bytes(range(256)) * 300  # 76800 > chunk 65536 → 2 要素
check("snappy_compress: 短いリテラルは 1 バイトのタグ、前置きは非圧縮長", mod.snappy_compress(small) == b"\x0a" + bytes([9 << 2]) + small and snappy_decompress(mod.snappy_compress(small)) == small)
check("snappy_compress: 60 以上は 61 のタグ + 2 バイトの長さ、chunk で分ける。復号すると元に戻る",
      snappy_decompress(mod.snappy_compress(big)) == big and mod.snappy_compress(big)[3] == 61 << 2 and snappy_decompress(mod.snappy_compress(b"")) == b"")

docs = mod.opensearch_docs([rec])
check("opensearch_docs: action 行と document 行の対、@timestamp は ISO の Z、数値の field は数値、文字列はそのまま",
      docs[0] == '{"index":{}}' and len(docs) == 2
      and json.loads(docs[1])["@timestamp"] == "2023-11-14T22:13:20.500000Z"
      and json.loads(docs[1])["fields"] == {"ifInOctets": 123.0, "ifOperStatus": 1.0, "descr": "up", "flag": 1.0}
      and json.loads(docs[1])["tags"]["ifName"] == "Gi0/1")
_trap = {"ts": 1700000000.0, "topic": "traps", "measurement": "snmp_trap", "agent_host": "", "host": "h",
         "tags": {"source": "203.0.113.31", "oid": ".1.3.6.1.6.3.1.1.5.3", "name": "linkDown"}, "fields": {"sysUpTimeInstance": 1}}
_tdocs = mod.opensearch_docs([_trap, dict(_trap, tags=dict(_trap["tags"], source="203.0.113.99"))], mod.parse_device_map("203.0.113.31=dc1-leaf-01"))
check("opensearch_docs: sysName の無い snmp_trap は tags.sysName に機器名が入る。表に無い IP では IP をそのまま入れる（Grafana の trap のルールの集計に出す）。元のレコードは変えない",
      json.loads(_tdocs[1])["tags"] == dict(_trap["tags"], sysName="dc1-leaf-01")
      and json.loads(_tdocs[3])["tags"] == dict(_trap["tags"], source="203.0.113.99", sysName="203.0.113.99")
      and "sysName" not in _trap["tags"])
check("opensearch_docs: devmap を渡さなくても sysName の無い trap は source を入れる。sysName のあるレコード（ポーリング）と source の無いレコードは変えない",
      json.loads(mod.opensearch_docs([_trap])[1])["tags"]["sysName"] == "203.0.113.31" and mod.opensearch_docs([rec], mod.parse_device_map("203.0.113.31=x")) == docs
      and json.loads(mod.opensearch_docs([dict(_trap, tags={"source": "203.0.113.31", "sysName": "x"})], mod.parse_device_map("203.0.113.31=dc1-leaf-01"))[1])["tags"]["sysName"] == "x")
check("splunk_events は device map を受けず、sysName を足さない（Splunk のアラートアクションが DEVICE_MAP で引く。cycle 002 でも出力は変えない）",
      list(inspect.signature(mod.splunk_events).parameters) == ["records", "index"]
      and "sysName" not in json.loads(mod.splunk_events([_trap])[0])["event"]["tags"])
check("build は device map を prometheus と opensearch の sender にだけ渡す",
      "devmap = parse_device_map(args.device_map)" in src
      and re.search(r'make_prometheus_sender\([^)]*devmap\)', src) is not None and re.search(r'make_opensearch_sender\([^)]*devmap\)', src) is not None
      and re.search(r'make_splunk_sender\([^)]*devmap', src) is None)

import datetime as dt
r = mod.row_to_record({"ts": dt.datetime(2023, 11, 14, 22, 13, 20, tzinfo=dt.timezone.utc), "topic": "traps", "measurement": "snmp_trap",
                       "agent_host": "r1", "host": "h", "tags_json": '{"a":"b"}', "fields_json": "not json"})
check("row_to_record: ts は epoch 秒、tags は辞書、壊れた JSON は空の辞書",
      r["ts"] == 1700000000.0 and r["tags"] == {"a": "b"} and r["fields"] == {} and r["topic"] == "traps")
check("row_to_record: naive な datetime は UTC とみなす", mod.row_to_record({"ts": dt.datetime(2023, 11, 14, 22, 13, 20)})["ts"] == 1700000000.0)
check("_number: 数値の文字列は float、それ以外は None", mod._number("1.5") == 1.5 and mod._number("up") is None and mod._number(None) is None and mod._number(False) == 0.0)

# ---- 一意の番号 event_id と Kafka の位置（2026-10-04）: 同じ value なら Splunk と OpenSearch の本文で同じ、1 バイト違えば違う。Prometheus には出ない
import hashlib
def _kafka_row(value, offset, partition=0, topic="metrics"):
    """Kafka の 1 メッセージを read_rows と同じ列の行にする（event_id は F.sha2(value, 256) と同じ SHA-256 の 16 進。from_json は json.loads で代える）"""
    m = json.loads(value)
    return {"ts": dt.datetime.fromtimestamp(m["timestamp"], dt.timezone.utc), "topic": topic, "measurement": m["name"],
            "agent_host": m["tags"].get("agent_host"), "host": m["tags"].get("host"),
            "tags_json": json.dumps(m["tags"]), "fields_json": json.dumps(m["fields"]),
            "event_id": hashlib.sha256(value).hexdigest(), "kafka_topic": topic, "kafka_partition": partition, "kafka_offset": offset}
_val = b'{"fields":{"ifInOctets":123,"ifOperStatus":1},"name":"interface","tags":{"agent_host":"r1","host":"h","ifName":"Gi0/1"},"timestamp":1700000000}'
_val1 = _val.replace(b'"ifInOctets":123', b'"ifInOctets":124')
check("（前提）1 バイトだけ違う value", len(_val1) == len(_val) and sum(a != b for a, b in zip(_val, _val1)) == 1)
# 同じ value が 2 回（Telegraf が Kafka に入れ直した = 別の offset）と、1 バイト違う value が 1 回
_recs = [mod.row_to_record(_kafka_row(_val, 10)), mod.row_to_record(_kafka_row(_val, 11, partition=1)), mod.row_to_record(_kafka_row(_val1, 12))]
check("row_to_record: event_id / kafka_topic / kafka_partition / kafka_offset をそのまま運ぶ（無ければ None）",
      _recs[1]["event_id"] == hashlib.sha256(_val).hexdigest() and _recs[1]["kafka_topic"] == "metrics"
      and _recs[1]["kafka_partition"] == 1 and _recs[1]["kafka_offset"] == 11
      and all(r[k] is None for k in ADDED_COLUMNS))
_bodies = {}
_orig_sig = mod.sigv4_headers
mod.sigv4_headers = lambda method, url, body, service, region, headers: dict(headers)
mod.http_post = lambda url, body, headers, context=None: (_bodies.setdefault(url, []).append(body), (200, '{"errors":false}'))[1]
try:
    mod.make_splunk_sender("https://s:8088", "tok")(_recs)
    mod.make_opensearch_sender("https://o", "snmp-logs", "ap-northeast-1")(_recs)
    mod.make_prometheus_sender("https://p/api/v1/remote_write", "ap-northeast-1")(_recs)
finally:
    mod.http_post, mod.sigv4_headers = _orig_post, _orig_sig
_sp_ev = [json.loads(x)["event"] for x in _bodies["https://s:8088/services/collector/event"][0].decode().split("\n")]
_os_doc = [json.loads(x) for x in _bodies["https://o/snmp-logs/_bulk"][0].decode().splitlines()[1::2]]
_eid = hashlib.sha256(_val).hexdigest()
check("event_id は value の SHA-256 の 16 進 64 文字（小文字）", re.fullmatch(r"[0-9a-f]{64}", _eid) is not None)
for _name, _got_ev in (("Splunk の event", _sp_ev), ("OpenSearch のドキュメント", _os_doc)):
    check(f"{_name}: 同じ value の 2 行は event_id が同じ（offset が違っても）、1 バイト違う value は違う",
          len(_got_ev) == 3 and _got_ev[0]["event_id"] == _got_ev[1]["event_id"] == _eid and _got_ev[2]["event_id"] != _eid
          and _got_ev[2]["event_id"] == hashlib.sha256(_val1).hexdigest())
    check(f"{_name}: Kafka の位置（kafka_topic / kafka_partition / kafka_offset）を元のメッセージを追うために入れる",
          [(e["kafka_topic"], e["kafka_partition"], e["kafka_offset"]) for e in _got_ev] == [("metrics", 0, 10), ("metrics", 1, 11), ("metrics", 0, 12)])
check("Splunk の event: tags / fields は今のまま（保存済みサーチの spath が読む tags.* / fields.* は変わらない）",
      _sp_ev[0]["tags"]["ifName"] == "Gi0/1" and _sp_ev[0]["fields"] == {"ifInOctets": 123, "ifOperStatus": 1}
      and not any(k.startswith(("event_id", "kafka_")) for e in _sp_ev for k in list(e["tags"]) + list(e["fields"])))
_prom_raw = b"".join(snappy_decompress(b) for b in _bodies["https://p/api/v1/remote_write"])
_prom_labels = [decode_fields(v) for _n1, _w1, ts in decode_fields(_prom_raw) for n, w, v in decode_fields(ts) if n == 1]
check("Prometheus の remote write: 本文に event_id / kafka_ / 番号の値が無い（ラベルは tags と __name__ だけ）",
      len(_prom_labels) > 0 and b"event_id" not in _prom_raw and b"kafka_" not in _prom_raw
      and _eid.encode() not in _prom_raw and hashlib.sha256(_val1).hexdigest().encode() not in _prom_raw
      and {l[0][2] for l in _prom_labels} == {b"__name__", b"agent_host", b"host", b"ifName"})
check("prometheus_series: ラベルの名前に event_id / kafka_* が出ない（1 サンプルごとに別の系列にしない）",
      all(not n.startswith(("event_id", "kafka_")) for l, _, _ in mod.prometheus_series(_recs) for n, _ in l)
      and len({tuple(l) for l, _, _ in mod.prometheus_series(_recs[:2])}) == 2)

# ---- ops/up.sh / ops/down.sh / ops/check.sh / deploy.env.example とのつながり
check("up.sh は 6 本の jar を置く", len(re.findall(r'^\s*"\$MAVEN/', up, re.M)) == 6)
for jar in ("spark-sql-kafka-0-10_2.12", "spark-token-provider-kafka-0-10_2.12", "kafka-clients", "commons-pool2", "aws-msk-iam-auth", "s3-tables-catalog-for-iceberg-runtime"):
    check(f"up.sh の jar に {jar}", jar in up)
check("up.sh の SPARK_VERSION は emr_release_label の Spark（3.5.6）", re.search(r'^SPARK_VERSION=3\.5\.6$', up, re.M) is not None
      and "7.13.0 = Spark 3.5.6" in tf)
check("up.sh のスクリプトは spark/snmp_sinks.py", re.search(r'^SPARK_SCRIPT=spark/snmp_sinks\.py$', up, re.M) is not None and "snmp_to_iceberg" not in up)
check("up.sh は SPLUNK_INDEX（既定は空）を読み、STORES に splunk があれば（既定）ECS の Splunk の token を SSM に作ってから渡す（値は読まない）。外の Splunk の変数は渡さない",
      re.search(r'^SPLUNK_INDEX="\$\{SPLUNK_INDEX:-\}"$', up, re.M) is not None
      and "get-parameter" not in up and "splunk_hec_url=" not in up and "splunk_skip_tls_verify" not in up and "SPLUNK_TOKEN_PARAM" not in up
      and 'ANALYTICS_VARS+=(-var "splunk_image_tag=$SPLUNK_TAG" -var "splunk_index=$SPLUNK_INDEX" -var "splunk_az_num=$SPLUNK_AZ_NUM")' in up
      and 'ensure_secret "/$PREFIX/splunk/hec-token" uuid' in up[up.index("ensure_splunk_secrets() {"):]
      and up.index('ANALYTICS_VARS=(-var "sinks=[$SINKS_TF]"') < up.index('    ensure_splunk_secrets "$SPLUNK_AZ_NUM"') < up.index('tf_apply pipeline/analytics "${ANALYTICS_VARS[@]}"'))
check("up.sh は STORES（既定 s3,grafana,splunk）から導いた格納先を terraform/pipeline/analytics の sinks に組んで渡す",
      re.search(r'^if \[ -z "\$\{STORES:-\}" \]; then STORES=s3,grafana,splunk; STORES_DEFAULT=1; fi$', up, re.M) is not None
      and re.search(r'^SINK_S3="\$STORE_S3"; SINK_OPENSEARCH="\$STORE_GRAFANA"; SINK_PROMETHEUS="\$STORE_GRAFANA"; SINK_SPLUNK="\$STORE_SPLUNK"$', up, re.M) is not None
      and 'ANALYTICS_VARS=(-var "sinks=[$SINKS_TF]" ' in up and 'tf_apply pipeline/analytics "${ANALYTICS_VARS[@]}"' in up)
check("up.sh は STORES の splunk に関わらず device map を lab の定義から作って渡す（lab/lab_topology.py --device-map。trap と gNMI には sysName が無い。Splunk の DEVICE_MAP と Spark の --device-map。cycle 002）",
      re.search(r'  ANALYTICS_VARS=\(-var "sinks=\[\$SINKS_TF\]" [^\n]*\n(?:  ANALYTICS_VARS\+=\(-var "opensearch_az_num=\$OPENSEARCH_AZ_NUM"\)\n)?(?:  ensure_s3tables_catalog [^\n]*\n)?(?:  #[^\n]*\n)*  DEVICE_MAP=\$\("\$\{PY\[@\]\}" lab/lab_topology\.py lab --device-map\) \|\| die [^\n]*\n  ANALYTICS_VARS\+=\(-var "device_map=\$DEVICE_MAP"\)\n  if \[ -n "\$GRAFANA" \]', up) is not None
      and up.count("lab_topology.py lab --device-map") == 1 and up.count('-var "device_map=$DEVICE_MAP"') == 1)
check("up.sh は AGENT=0 でも CloudWatch へのログを切らない（CloudWatch Logs へは土台の logs のエンドポイントで届く）",
      "cloudwatch_logging=false" not in up and re.search(r'variable "cloudwatch_logging" \{[^}]*default\s*=\s*true', tf) is not None)
# 2026-09-26〜28 は NAT Gateway だけで AWS の API に出ていた。2026-09-28 に閉域（エンドポイント + aws:SourceVpc の Deny）にした
_s3ep = re.search(r'resource "aws_vpc_endpoint" "s3" \{(.*?)\n\}', _core, re.S)
check("土台の S3 は Gateway 型で、ポリシーは付けない（2026-09-15 / 17 の障害）",
      _s3ep is not None and 'vpc_endpoint_type = "Gateway"' in _s3ep.group(1) and "policy" not in _s3ep.group(1)
      and not any(k in _core for k in ("create_shared_endpoints", "create_ssm_endpoints", "client_cidr")))
_ifep = re.search(r'resource "aws_vpc_endpoint" "interface" \{(.*?)\n\}', _core, re.S)
check("インターフェース型エンドポイントは var.interface_endpoints の for_each 1 か所だけ（private DNS、endpoints SG、このアカウントだけのポリシー）",
      _core.count('resource "aws_vpc_endpoint"') == 2 and _ifep is not None
      and "for_each = toset(var.interface_endpoints)" in _ifep.group(1) and "private_dns_enabled = true" in _ifep.group(1)
      and "security_group_ids  = [aws_security_group.endpoints.id]" in _ifep.group(1)
      and re.search(r'Sid\s*=\s*"OwnAccountOnly"[\s\S]*?"aws:PrincipalAccount"\s*=\s*local\.account_id', _ifep.group(1)) is not None
      and re.search(r'variable "interface_endpoints"[\s\S]*?default\s*=\s*\["ssm", "ssmmessages"\]', _core) is not None
      and re.search(r'subnet_ids\s*=\s*local\.endpoint_subnet_ids', _ifep.group(1)) is not None
      and re.search(r'endpoint_subnet_ids\s*=\s*slice\(local\.subnet_ids, 0, var\.endpoints_az_num\)', _core) is not None)
# 2026-10-04: AZ の数はリソースごとに <リソース>_az_num で選ぶ。base/core はサブネット a / b / c をいつも作り、outputs.subnet_ids で順に渡す
_az_c = re.search(r'variable "az_id_c" \{(.*?)\n\}', _core, re.S)
check("base/core はサブネット c も作り（a / b と別の AZ ID。既定 apne1-az2）、subnet_ids = [a, b, c] を output する。endpoints_multi_az と runtime_subnet_ids は無い",
      'resource "aws_subnet" "c"' in _core and 'resource "aws_route_table_association" "c"' in _core
      and re.search(r'subnet_ids\s*=\s*\[aws_subnet\.a\.id, aws_subnet\.b\.id, aws_subnet\.c\.id\]', _core) is not None
      and re.search(r'output "subnet_ids" \{[^}]*value\s*=\s*local\.subnet_ids', _core) is not None
      and _az_c is not None and 'default     = "apne1-az2"' in _az_c.group(1)
      and "var.az_id_c != var.az_id_a" in _az_c.group(1) and "var.az_id_c != var.az_id_b" in _az_c.group(1)
      and "endpoints_multi_az" not in _core and 'output "runtime_subnet_ids"' not in _core)
_aoss_ep = re.search(r'resource "aws_opensearchserverless_vpc_endpoint" "aoss" \{(.*?)\n\}', _core, re.S)
check("OpenSearch Serverless の VPC エンドポイントも ENDPOINTS_AZ_NUM に従う（2026-10-04 までは a / b の 2 つで固定）",
      _aoss_ep is not None and re.search(r'subnet_ids\s*=\s*local\.endpoint_subnet_ids', _aoss_ep.group(1)) is not None)
# 各ルートの <リソース>_az_num。既定と使える値は ops/up.sh の検査（az_num）と同じ
_AZ_VARS = {  # (ルート, 変数): (既定, 使える値)
    ("base/core", "endpoints_az_num"): (1, [1, 2, 3]), ("pipeline/stream", "msk_az_num"): (2, [2, 3]),
    ("pipeline/stream", "telegraf_az_num"): (1, [1, 2, 3]), ("agent", "runtime_az_num"): (1, [1, 2, 3]),
    ("agent", "lambda_az_num"): (1, [1, 2, 3]), ("agent", "opensearch_az_num"): (1, [1, 2]),
    ("pipeline/analytics", "emr_az_num"): (1, [1, 2, 3]), ("pipeline/analytics", "opensearch_az_num"): (1, [1, 2]),
    ("pipeline/analytics", "splunk_az_num"): (1, [1, 2, 3]),
    ("pipeline/graph", "neptune_az_num"): (1, [1, 2, 3]), ("pipeline/graph", "lambda_az_num"): (1, [1, 2, 3]),
    ("pipeline/nautobot", "nautobot_db_az_num"): (1, [1, 2]), ("workflow", "lambda_az_num"): (1, [1, 2, 3]),
}
def _root_tf(root):
    d = os.path.join(ROOT, "terraform", *root.split("/"))
    return "".join(open(os.path.join(d, n), encoding="utf-8").read() for n in sorted(os.listdir(d)) if n.endswith(".tf"))
def _az_var_ok(root, name, default, allowed):
    m = re.search(r'variable "' + name + r'" \{(.*?)\n\}', _root_tf(root), re.S)
    return (m is not None and re.search(r"default\s*=\s*" + str(default) + r"\n", m.group(1)) is not None
            and f"contains([{', '.join(map(str, allowed))}], var.{name})" in m.group(1))
check("各ルートの <リソース>_az_num は既定と使える値が決めたとおり（MSK は既定 2 で 1 を受け付けない。Runtime は既定 1。OpenSearch と Nautobot の DB は 2 まで）",
      all(_az_var_ok(r, n, d, a) for (r, n), (d, a) in _AZ_VARS.items()))
_AZ_USES = {
    "agent": (r"subnets\s*=\s*slice\(local\.subnet_ids, 0, var\.runtime_az_num\)", r"subnet_ids\s*=\s*slice\(local\.subnet_ids, 0, var\.lambda_az_num\)",
              r'standby_replicas\s*=\s*var\.opensearch_az_num == 2 \? "ENABLED" : "DISABLED"'),
    "pipeline/stream": (r"broker_subnet_ids\s*=\s*slice\(local\.subnet_ids, 0, var\.msk_az_num\)", r"number_of_broker_nodes\s*=\s*var\.msk_az_num\n",
                        r"default\.replication\.factor=\$\{var\.msk_az_num\}\n", r"min\.insync\.replicas=\$\{var\.msk_az_num - 1\}\n",
                        r"telegraf_dialout_subnet_ids\s*=\s*slice\(local\.subnet_ids, 0, var\.telegraf_az_num\)", r"desired_count\s*=\s*var\.telegraf_az_num\n"),
    "pipeline/analytics": (r"subnet_ids\s*=\s*slice\(local\.subnet_ids, 0, var\.emr_az_num\)", r'standby_replicas\s*=\s*var\.opensearch_az_num == 2 \? "ENABLED" : "DISABLED"',
                           r"subnets\s*=\s*slice\(local\.subnet_ids, 0, var\.splunk_az_num\)", r"desired_count\s*=\s*var\.splunk_az_num\n"),
    "pipeline/graph": (r"replica_count\s*=\s*var\.neptune_az_num - 1\n", r"subnet_ids\s*=\s*slice\(local\.subnet_ids, 0, var\.lambda_az_num\)"),
    "pipeline/nautobot": (r"multi_az\s*=\s*var\.nautobot_db_az_num == 2\n", r"subnet_ids\s*=\s*data\.terraform_remote_state\.main\.outputs\.subnet_ids\n"),
    "workflow": (r"subnet_ids\s*=\s*slice\(local\.subnet_ids, 0, var\.lambda_az_num\)",),
}
check("各ルートは base/core の subnet_ids の先頭から AZ_NUM 個を使う（MSK は複製 = AZ_NUM、min.insync = AZ_NUM - 1。Neptune のレプリカ = AZ_NUM - 1。RDS は 2 で Multi-AZ）",
      all(all(re.search(u, _root_tf(r)) for u in us) for r, us in _AZ_USES.items())
      and not any("runtime_subnet_ids" in _root_tf(r) for r in _AZ_USES))
# 注意書き（範囲の制限と、ENDPOINTS_AZ_NUM がほかより小さいとき）は、変数の description・リソースのそば・ops/up.sh の検査の 3 か所に
# 出典の URL と確認日を残す（2026-10-04 のユーザー指示）。AWS で試していないことは「未確認」/ "not tried" と書く。
# 確認日は 2026-10-04。Runtime だけは既定を 1 にしたとき（2026-10-05）に確かめ直した
_AZ_SRC = {  # (ルート, 変数, リソースのファイル): 出典の URL
    ("pipeline/stream", "msk_az_num", "msk.tf"): "https://docs.aws.amazon.com/msk/1.0/apireference/clusters.html",
    ("agent", "runtime_az_num", "runtime.tf"): "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html",
    ("agent", "opensearch_az_num", "kb.tf"): "https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-opensearchserverless-collection.html",
    ("pipeline/analytics", "opensearch_az_num", "sinks.tf"): "https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-opensearchserverless-collection.html",
    ("pipeline/nautobot", "nautobot_db_az_num", "database.tf"): "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/multi-az-db-clusters-concepts.html",
    ("base/core", "endpoints_az_num", "endpoints.tf"): "https://docs.aws.amazon.com/vpc/latest/privatelink/privatelink-access-aws-services.html",
    ("pipeline/graph", "neptune_az_num", "neptune.tf"): "https://docs.aws.amazon.com/neptune-analytics/latest/apiref/API_CreateGraph.html",
}
def _az_src_ok(root, name, fname, url):
    day = "2026-10-05" if name == "runtime_az_num" else "2026-10-04"
    m = re.search(r'variable "' + name + r'" \{\n\s*description\s*=\s*"([^"\n]*)"', _root_tf(root))
    res = open(os.path.join(ROOT, "terraform", *root.split("/"), fname), encoding="utf-8").read()
    return (m is not None and url in m.group(1) and f"checked {day}" in m.group(1)
            and re.search(r"^\s*#.*" + re.escape(url), res, re.M) is not None and f"{day} 確認" in res
            and re.search(r"^#.*" + re.escape(url), up, re.M) is not None)
check("注意書きの付く *_az_num（MSK / Runtime / OpenSearch 2 か所 / Nautobot の DB / エンドポイント / Neptune）は、description とリソースのそばと up.sh の検査に出典の URL と確認日がある",
      all(_az_src_ok(r, n, f, u) for (r, n, f), u in _AZ_SRC.items()))
check("確かめ切れていないことは「未確認」と書く（Runtime の 2 サブネット以上、OpenSearch のスタンバイの AZ と OCU、エンドポイントの AZ 障害の振る舞い）。"
      "Runtime の 1 サブネット（既定）は 2026-10-05 に AWS で確かめたので、未確認の文を残さない",
      "1 つ（既定）で作って動くことは 2026-10-05 に AWS で確かめた。2 つ以上は AWS で未確認" in _root_tf("agent")
      and "One subnet (the default) worked on AWS on 2026-10-05; two or more are not tried on AWS" in _root_tf("agent")
      and "1 つ（既定）で作って動くことは 2026-10-05 に AWS で確かめた。2 つ以上は AWS で未確認" in up
      and not any(w in t for t in (_root_tf("agent"), up) for w in ("1 つで作るのは AWS で未確認", "One subnet is not tried on AWS"))
      and all("（未確認）" in _root_tf(r) and "unverified" in _root_tf(r) for r in ("agent", "pipeline/analytics"))
      and "AZ が落ちたときの振る舞いは AWS で未確認" in _root_tf("base/core") and "not tried on AWS" in _root_tf("base/core")
      and all(w in up for w in ("「最小 OCU が倍」は今の Developer Guide に見つけられなかった（未確認）", "AZ が落ちたときの振る舞いは AWS で未確認")))
# Runtime の既定は 1（2026-10-05 のユーザー決定）。前の決定（1 を拒む）の文は残さない
_faq = open(os.path.join(ROOT, "docs", "faq-fukuda-nwc-poc.md"), encoding="utf-8").read()
check("Runtime の 1 AZ を拒んでいた前の決定（2026-10-04）の文が、up.sh・deploy.env.example・terraform・FAQ に残っていない",
      not any(w in t for t in (up, env_example, _root_tf("agent"), _root_tf("base/core"),
                               open(os.path.join(ROOT, "terraform", "agent", "terraform.tfvars.example"), encoding="utf-8").read(), _faq)
              for w in ("AWS の制約ではなくユーザーの決定", "not by AWS", "refused on purpose", "kept at two", "2 AZ 以上に置く",
                        "AWS の文書が高可用性のため 2 AZ 以上を勧めている", "MSK と Runtime だけ", "MSK と Runtime は", "Runtime needs two AZs"))
      and "既定 1（2026-10-05 のユーザー決定）" in up and "1 つを禁じる記述は無い" in up and "2026-10-05 確認" in up
      and "1 つを禁じてはいない" in _faq)
_SINGLE = {  # (ルート, リソースの見出し): 理由に書く言葉
    ("base/core", 'resource "aws_instance" "web"'): "SSM のポートフォワード",
    ("pipeline/lab", 'resource "aws_instance" "lab"'): "containerlab の 1 台の中に全部の機器",
    ("pipeline/analytics", 'resource "aws_ecs_service" "grafana"'): "https://grafana.com/docs/grafana/latest/alerting/set-up/configure-high-availability/",
    ("pipeline/nautobot", 'resource "aws_ecs_service" "nautobot"'): "Redis と Celery のワーカーが同じタスク",
    ("workflow", 'resource "aws_ecs_service" "workflow"'): "Temporal の開発用サーバー",
    ("pipeline/stream", 'resource "aws_ecs_service" "telegraf_dialin"'): "TELEGRAF_AZ_NUM に従わない理由",
}
def _single_ok(root, head, word):
    t = _root_tf(root)
    i = t.find(head)
    if i < 0:
        return False
    j = t.find("\n}\n", i)
    near = t[max(0, t.rfind("\n\n", 0, i)):j]  # 見出しの直前のコメントから、そのリソースの終わりまで
    return word in near and ("AWS では未確認（2026-10-04）" in near or "2026-10-04 確認" in near)
check("1 台でしか成り立たないリソース（Web / lab / Grafana / Nautobot / workflow / Telegraf の dialin）のそばに、AZ の数のキーを作らない理由と確かめ方（出典か「未確認」）を書く",
      all(_single_ok(r, h, w) for (r, h), w in _SINGLE.items()))
# ---- 閉域の Deny（terraform/base/core/perimeter.tf と、analytics が付けるもの）
_perim = open(os.path.join(ROOT, "terraform", "base", "core", "perimeter.tf"), encoding="utf-8").read()
check("perimeter.tf: aws:SourceVpc がこの VPC でなく、AWS のサービス経由でもない呼び出しを拒む（S3 Tables が裏で呼ぶ分は外す）",
      re.search(r'StringNotEqualsIfExists\s*=\s*\{\s*"aws:SourceVpc"\s*=\s*aws_vpc\.this\.id,\s*"aws:CalledViaLast"\s*=\s*"s3tables\.amazonaws\.com"\s*\}', _perim) is not None
      and re.search(r'BoolIfExists\s*=\s*\{\s*"aws:ViaAWSService"\s*=\s*"false"\s*\}', _perim) is not None
      and all(f'"{a}"' in _perim for a in ("s3:*", "s3tables:*", "sqs:*", "ssm:*", "bedrock:*", "sns:*", "aps:*", "bedrock-agentcore:InvokeAgentRuntime")) and '"events:*"' not in _perim
      and "neptune-" not in _perim.split("perimeter_denied_actions = [")[1].split("]")[0] and "kafka-cluster" not in _perim.split("perimeter_denied_actions = [")[1].split("]")[0])
check("perimeter.tf: ポリシーはいつも作り、NETWORK_PERIMETER=0 では何も拒まない中身にする（他のルートが付けたままでも base/core の apply が落ちない）",
      re.search(r'resource "aws_iam_policy" "network_perimeter" \{\n\s*name', _perim) is not None
      and 'var.network_perimeter ? aws_iam_policy.network_perimeter.arn : ""' in _core)
check("analytics: EMR のロールに perimeter を付け、テーブルバケットにも VPC の外を拒むポリシーを付ける（ポリシーの操作とデプロイする人は外す）",
      re.search(r'resource "aws_iam_role_policy_attachment" "emr_perimeter"[\s\S]*?count\s*=\s*local\.perimeter_policy_arn != "" \? 1 : 0', tf) is not None
      and re.search(r'resource "aws_s3tables_table_bucket_policy" "tables"[\s\S]*?"s3tables:DeleteTableBucketPolicy"[\s\S]*?"aws:CalledViaLast"\s*=\s*"s3tables\.amazonaws\.com"[\s\S]*?"aws:PrincipalIsAWSService"\s*=\s*"false"[\s\S]*?local\.perimeter_exempt_principals', tf) is not None
      and re.search(r'perimeter_policy_arn\s*=\s*try\(data\.terraform_remote_state\.main\.outputs\.network_perimeter_policy_arn,\s*""\)', tf) is not None)
# up.sh のエンドポイントの選び方を切り出して bash で動かす
_epblk = up[up.index('ENDPOINTS=""'):up.index('echo "インターフェース型エンドポイント')]
def _endpoints(roots, **env):
    r = subprocess.run(["bash", "-c", f'ROOTS="{roots}"\n' + _epblk + 'echo "OUT: $ENDPOINTS | $(endpoint_count)"'],
                       capture_output=True, text=True, env={"PATH": os.environ["PATH"], **env})
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr
import subprocess
check("土台だけなら ssm / ssmmessages の 2 本", _endpoints("base/ecr base/core") == "OUT: ssm ssmmessages | 2")
check("AGENT は bedrock-runtime / bedrock-agentcore / ecr / logs を足し、KB で bedrock-agent-runtime",
      _endpoints("base/ecr base/core agent", AGENT="1") == "OUT: ssm ssmmessages bedrock-runtime bedrock-agentcore ecr.api ecr.dkr logs | 7"
      and _endpoints("base/ecr base/core agent", AGENT="1", CREATE_KB="1").startswith("OUT: ssm ssmmessages bedrock-runtime bedrock-agentcore ecr.api ecr.dkr logs bedrock-agent-runtime | 8"))
_ALL = "base/ecr base/core agent pipeline/lab pipeline/stream pipeline/analytics pipeline/graph workflow"
check("全部なら 16 本で重複しない（ecr / logs / s3tables / bedrock-agentcore は 1 本ずつ。graph は Neptune Analytics の neptune-graph-data）。events は無く、アラートの送り手がいれば sns",
      _endpoints(_ALL, AGENT="1", CREATE_KB="1", SINK_PROMETHEUS="1", GRAFANA="1")
      == "OUT: ssm ssmmessages bedrock-runtime bedrock-agentcore ecr.api ecr.dkr logs s3tables neptune-graph-data kinesis-firehose sqs bedrock-agentcore.gateway athena bedrock-agent-runtime aps-workspaces sns | 16")
check("sns のエンドポイントは Grafana か Splunk があるときだけ（Grafana はいつもアラートルールを持つ。どちらも無ければ 15 本。Splunk だけでも足す）",
      _endpoints(_ALL, AGENT="1", CREATE_KB="1", SINK_PROMETHEUS="1").endswith("aps-workspaces | 15")
      and _endpoints("base/ecr base/core pipeline/lab pipeline/stream pipeline/analytics", SPLUNK_ON_ECS="1") == "OUT: ssm ssmmessages ecr.api ecr.dkr logs s3tables sns | 7"
      and "events" not in _epblk.replace("events の", ""))
check("kinesis-firehose（graph の履歴）と athena（workflow の query_history）は analytics を作る回か、analytics が state に残っているときだけ",
      _endpoints("base/ecr base/core pipeline/lab pipeline/graph", SKIP_ANALYTICS="1") == "OUT: ssm ssmmessages ecr.api ecr.dkr neptune-graph-data | 5"
      and _endpoints("base/ecr base/core pipeline/lab pipeline/analytics pipeline/graph") == "OUT: ssm ssmmessages ecr.api ecr.dkr s3tables logs neptune-graph-data kinesis-firehose | 8"
      and subprocess.run(["bash", "-c", 'ROOTS="base/ecr base/core"\n' + _epblk + 'ANALYTICS_LEFT=1; endpoints_for pipeline/graph; endpoints_for workflow; echo "OUT: $ENDPOINTS"'],
                         capture_output=True, text=True, env={"PATH": os.environ["PATH"], "SKIP_ANALYTICS": "1"}).stdout.strip().splitlines()[-1]
      == "OUT: ssm ssmmessages neptune-graph-data kinesis-firehose sqs s3tables ecr.api ecr.dkr logs bedrock-agentcore bedrock-agentcore.gateway athena")
check("up.sh: state に残った analytics を見つけたら ANALYTICS_LEFT=1 にし、残った graph / workflow の履歴の分はその後で数える",
      re.search(r'for r in agent pipeline/lab pipeline/stream pipeline/analytics pipeline/graph pipeline/nautobot workflow; do', up) is not None
      and re.search(r'if has_resources "\$r"; then\n\s*endpoints_for "\$r"\n\s*if \[ "\$r" = pipeline/analytics \]; then ANALYTICS_LEFT=1; fi', up) is not None)
check("perimeter.tf: Firehose のロール <接頭辞>-alert-firehose を資源側の Deny の例外に入れる（-kb と同じ。サービスが VPC の外から書く）",
      '"arn:${local.partition}:iam::${local.account_id}:role/${local.name_prefix}-alert-firehose",' in _perim
      and len([l for l in re.search(r'perimeter_exempt_principals = \[(.*?)\n\s*\]', _perim, re.S).group(1).splitlines() if l.strip() and not l.strip().startswith("#")]) == 3
      and "kinesis-firehose" in _core and '"athena"' in _core)
_denied = re.findall(r'"([\w-]+:[\w*]+)"', _perim.split("perimeter_denied_actions = [")[1].split("]")[0])
check("perimeter.tf: athena:* と firehose:* も IAM 側で拒む（Athena が代わりに読む S3 Tables は s3tables:* の Deny で止まらない。履歴の行を VPC の外から書かせない）",
      {"athena:*", "firehose:*"} <= set(_denied)
      and re.search(r'output "alert_events_table_arn" \{[^}]*value\s*=\s*aws_s3tables_table\.alert_events\.arn', tf) is not None)
# s3tablescatalog の用意（ensure_s3tables_catalog）も切り出し、aws を偽物にして動かす
import tempfile
_catblk = up[up.index("S3TABLES_CATALOG_INPUT="):up.index("\n}\n", up.index("ensure_s3tables_catalog() {")) + 3]   # ops/up-common.sh（OSS 版と共通）
_fakeaws = r'''die() { echo "DIE: $1"; exit 1; }
aws() {
  echo "AWS $1 $2" >>"$CALLS"
  if [ "$2" = create-catalog ]; then   # 中身の JSON に下の --query の名前が入っているので、先に分ける
    while [ $# -gt 0 ]; do if [ "$1" = --catalog-input ]; then printf '%s' "$2" >"$CALLS.input"; fi; shift; done
    return 0
  fi
  case "$*" in
    *"get-catalog"*"--query Catalog.Name "*)
      case "$SCEN" in
        missing) echo "An error occurred (EntityNotFoundException) when calling the GetCatalog operation: Catalog not found" >&2; return 254 ;;
        denied) echo "An error occurred (AccessDeniedException) when calling the GetCatalog operation" >&2; return 254 ;;
        *) echo s3tablescatalog ;;
      esac ;;
    *"FederatedCatalog.ConnectionName"*) if [ "$SCEN" = ok ]; then echo aws:s3tables; else echo None; fi ;;
    *"AllowFullTableExternalDataAccess"*) if [ "$SCEN" = ok ]; then echo True; else echo False; fi ;;
    *"CreateTableDefaultPermissions"*) if [ "$SCEN" = ok ]; then echo IAM_ALLOWED_PRINCIPALS; else echo arn:aws:iam::123456789012:role/lf-admin; fi ;;
  esac
}
'''
_catdir = tempfile.mkdtemp()
def _catalog(scen):
    calls = os.path.join(_catdir, scen)
    r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + _fakeaws + _catblk + "ensure_s3tables_catalog\necho END"], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "SCEN": scen, "CALLS": calls, "REGION": "ap-northeast-1", "ACCOUNT_ID": "123456789012", "OPS_DIR": "ops"})
    made = open(calls, encoding="utf-8").read().count("AWS glue create-catalog") if os.path.exists(calls) else 0
    sent = json.loads(open(calls + ".input", encoding="utf-8").read()) if os.path.exists(calls + ".input") else None
    return r.stdout, made, sent
_out, _made, _sent = _catalog("missing")
check("up.sh: s3tablescatalog が無ければ（EntityNotFoundException）create-catalog を 1 回打ち、FederatedCatalog はこのリージョンとアカウントの bucket/*、既定の権限は IAM_ALLOWED_PRINCIPALS",
      _made == 1 and _out.rstrip().endswith("END") and _sent is not None
      and _sent["FederatedCatalog"] == {"Identifier": "arn:aws:s3tables:ap-northeast-1:123456789012:bucket/*", "ConnectionName": "aws:s3tables"}
      and all(_sent[k] == [{"Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"}, "Permissions": ["ALL"]}]
              for k in ("CreateDatabaseDefaultPermissions", "CreateTableDefaultPermissions"))
      and _sent["AllowFullTableExternalDataAccess"] == "True")
_out, _made, _ = _catalog("denied")
check("up.sh: get-catalog がそれ以外のエラー（権限など）なら作らずに止まる", _made == 0 and "DIE: Glue のカタログ s3tablescatalog を確かめられない" in _out and "END" not in _out)
_out, _made, _ = _catalog("ok")
check("up.sh: 想定どおりの s3tablescatalog があれば作らず、警告も出さない", _made == 0 and "はある（作り直さない）" in _out and "想定" not in _out and _out.rstrip().endswith("END"))
_out, _made, _ = _catalog("other")
check("up.sh: 設定の違う s3tablescatalog があれば作り直さず、警告だけ出して先へ進む", _made == 0 and "設定が想定" in _out and _out.rstrip().endswith("END"))
check("up.sh: ensure_s3tables_catalog は analytics を作る回（7-4）に、analytics の apply より前に呼ぶ",
      up.index('log "7-4.') < up.index("  ensure_s3tables_catalog   #") < up.index("tf_apply pipeline/analytics", up.index('log "7-4.')))
# link_down の送り手の決め方（LINK_DOWN_SENDERS）と「WORKFLOW は送り手が要る」も切り出して動かす（ワークフローを起こすのは link_down だけ）
_sndblk = up[up.index('LINK_DOWN_SENDERS=""'):up.index('if [ -z "$AGENT" ] && [ -n "$CREATE_KB" ]; then')]
def _senders(**env):
    r = subprocess.run(["bash", "-c", 'die() { echo "DIE: $1"; exit 1; }\n' + _sndblk + 'echo "OUT: ${LINK_DOWN_SENDERS:-none}"'],
                       capture_output=True, text=True, env={"PATH": os.environ["PATH"], **env})
    return r.stdout.strip().splitlines()[-1][:40] if r.stdout.strip() else r.stderr
check("Grafana が link_down の送り手になるのは（STORES の grafana から導いた）GRAFANA と SINK_PROMETHEUS と SNMP_POLL があるときだけ（link_down はポーリングの ifOperStatus を見る）。Splunk は SPLUNK_ON_ECS（STORES の splunk）で",
      _senders(GRAFANA="1", SINK_PROMETHEUS="1", SNMP_POLL="1") == "OUT: grafana" and _senders(GRAFANA="1", SINK_PROMETHEUS="1") == "OUT: none"
      and _senders(GRAFANA="1", SNMP_POLL="1") == "OUT: none" and _senders(SINK_PROMETHEUS="1", SNMP_POLL="1") == "OUT: none"
      and _senders(SPLUNK_ON_ECS="1") == "OUT: splunk" and _senders(GRAFANA="1", SINK_PROMETHEUS="1", SNMP_POLL="1", SPLUNK_ON_ECS="1") == "OUT: grafana,splunk")
check("up.sh の WORKFLOW=1 は link_down の送り手（Grafana か Splunk）が 1 つも無ければ、何も作る前に止まる（SNMP_POLL=0 では Grafana は数えない）",
      _senders(WORKFLOW="1", GRAFANA="1").startswith("DIE: WORKFLOW はアラートの送り手が要る") and _senders(WORKFLOW="1").startswith("DIE: WORKFLOW はアラートの送り手が要る")
      and _senders(WORKFLOW="1", GRAFANA="1", SINK_PROMETHEUS="1").startswith("DIE: WORKFLOW はアラートの送り手が要る")
      and _senders(WORKFLOW="1", GRAFANA="1", SINK_PROMETHEUS="1", SNMP_POLL="1") == "OUT: grafana" and _senders(WORKFLOW="1", SPLUNK_ON_ECS="1") == "OUT: splunk"
      and up.index('die "WORKFLOW はアラートの送り手が要る') < up.index("ENDPOINTS=\"\""))
_r = subprocess.run(["bash", "-c", 'die() { echo "DIE: $1"; exit 1; }\n' + _sndblk], capture_output=True, text=True,
                    env={"PATH": os.environ["PATH"], "GRAFANA": "1", "SINK_PROMETHEUS": "1", "SPLUNK_ON_ECS": "1"})
check("SNMP_POLL=0 で Grafana の link_down が黙るときは注意を出す（Splunk があれば trap からだけ知らせると言う）",
      "注意: SNMP_POLL=0 なので Grafana のアラートルール link_down と Splunk の netops_poll は発火しない" in _r.stdout)
check("up.sh は base/core に interface_endpoints / network_perimeter / endpoints_az_num を渡し、state に残るルートの分も足す",
      'MAIN_VARS+=(-var "interface_endpoints=[' in up and 'MAIN_VARS+=(-var "network_perimeter=' in up
      and 'MAIN_VARS+=(-var "endpoints_az_num=$ENDPOINTS_AZ_NUM")' in up and "endpoints_multi_az" not in up
      and re.search(r'if has_resources "\$r"; then\n\s*endpoints_for "\$r"', up) is not None
      and 'NETWORK_PERIMETER="${NETWORK_PERIMETER:-1}"' in up
      and all(k in open(os.path.join(ROOT, "ops", "deploy-env.sh"), encoding="utf-8").read() for k in ("NETWORK_PERIMETER", "ENDPOINTS_MULTI_AZ")))
check("費用の目安にエンドポイント（1 本 1.4 セント × ENDPOINTS_AZ_NUM）を足す",
      "COST_CENTS=$((COST_CENTS + ($(endpoint_count) * 14 * ENDPOINTS_AZ_NUM + 5) / 10))" in up and "ENDPOINT_AZS" not in up)
check("NAT Gateway / IGW / EIP / パブリックサブネット / 既定ルートは作らない（2026-09-28。VPC から AWS の外へ出る経路が無い）。up.sh も create_nat_gateway を渡さない",
      not any(f'resource "{t}"' in _core for t in ("aws_nat_gateway", "aws_internet_gateway", "aws_eip", "aws_route"))
      and 'resource "aws_subnet" "public"' not in _core and "create_nat_gateway" not in _core and 'output "nat_gateway"' not in _core
      and "create_nat_gateway" not in up and "CREATE_NAT" not in up and "nat_gateway" not in tf)
check("費用の目安: 土台は Web の EC2 の 2 セント（NAT Gateway は無い）。ECS の Splunk はタスク 1 つ 12.3（SPLUNK_TASKS 個）、Grafana は 2",
      "COST_CENTS=2\n" in up and "# NAT Gateway\n" not in up and "COST_CENTS=8" not in up
      and 'if [ -n "$GRAFANA" ]; then COST_CENTS=$((COST_CENTS + 2)); fi' in up and 'if [ -n "$SPLUNK_ON_ECS" ]; then COST_CENTS=$((COST_CENTS + 123 * SPLUNK_TASKS / 10)); fi' in up)
check("lab.sh は containerlab の版の確かめ（GitHub へ出る）をしない", "export CLAB_VERSION_CHECK=disable" in open(os.path.join(ROOT, "lab", "lab.sh"), encoding="utf-8").read())
check("up.sh / deploy-env.sh に共用のエンドポイントと CLIENT_CIDR の扱いは無い",
      not any(k in up for k in ("SHARED_ENDPOINTS", "create_shared_endpoints", 'aws_vpc_endpoint.runtime["ecr-api"]', "CLIENT_CIDR"))
      and "CLIENT_CIDR" not in open(os.path.join(ROOT, "ops", "deploy-env.sh"), encoding="utf-8").read())
# 格納先（STORES）の判定ブロックを up.sh から切り出して、bash で実際に動かす（die と flag_value は up.sh / deploy-env.sh と同じ意味の最小版）。
# 前のキー（SINK_* / GRAFANA）の検査 → STORES の読み取り → SINKS_TF までと、導いた GRAFANA と格納先のログの行
import subprocess
_blk = up[up.index('OLD_STORE_KEYS=""'):up.index('SINKS_TF="\\"$(printf')]
_blk += up[up.index('SINKS_TF="\\"$(printf'):].split("\n", 1)[0] + "\n"
_g3 = up[up.index('GRAFANA=""\nif [ -z "$SKIP_ANALYTICS" ]'):up.index("# アラート（SNS のトピック")]
_pre = ('die() { echo "DIE: $*"; exit 1; }\n'
        'flag_value() { local name="$1" v; v="${!name:-}"; case "$v" in 1|true|yes) printf -v "$name" %s 1 ;; ""|0|false|no) printf -v "$name" %s "" ;; *) die "$name は 1 か 0" ;; esac; }\n')
_ST_OUT = 'echo "OUT: $SINKS | G=${GRAFANA:-0} ECS=${SPLUNK_ON_ECS:-0}"'
def _stores(tail=_ST_OUT, **env):
    r = subprocess.run(["bash", "-c", _pre + 'log() { echo "LOG: $*"; }\n' + _blk + _g3 + tail], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], **env})
    return r.returncode, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "", r.stdout
check("STORES は deploy.env を読んだあと、前のキーの検査のすぐ後で読み、Grafana・WORKFLOW の送り手・費用の検査の前に格納先の変数へ写す",
      up.index("load_deploy_env\n") < up.index('OLD_STORE_KEYS=""') < up.index('STORES_DEFAULT=""') < up.index('SINKS_TF="\\"$(printf')
      < up.index('GRAFANA=""\nif [ -z "$SKIP_ANALYTICS" ]') < up.index('die "WORKFLOW はアラートの送り手が要る') < up.index("COST_CENTS=2\n"))
check("up.sh は SINK_* / GRAFANA をスイッチとして読まない（既定値・flag_value・STORES とのぶつかりの検査が無い）",
      not any(k in up for k in ('SINK_S3="${SINK_S3:-1}"', 'SINK_SPLUNK="${SINK_SPLUNK:-0}"', "flag_value SINK_", 'GRAFANA="${GRAFANA:-1}"',
                                "flag_value GRAFANA", 'case "${GRAFANA:-}" in', "を一緒に書いている", "SINK_SPLUNK が全部 0")))
check("STORES が無ければ既定の s3,grafana,splunk（S3 Tables と OpenSearch・Prometheus・Grafana と Splunk。splunk は cycle 002 で既定に入れた）。空も既定",
      _stores()[:2] == (0, "OUT: iceberg,opensearch,prometheus,splunk | G=1 ECS=1") and _stores(STORES="")[:2] == _stores()[:2])
check("STORES=s3,grafana,splunk で 4 つの格納先がそろい、Grafana も ECS の Splunk も作る",
      _stores(STORES="s3,grafana,splunk")[:2] == (0, "OUT: iceberg,opensearch,prometheus,splunk | G=1 ECS=1")
      and _stores('echo "OUT: $SINKS_TF"', STORES="s3,grafana,splunk")[1] == 'OUT: "iceberg","opensearch","prometheus","splunk"')
check("STORES は書かなかったまとまりを作らない（s3 / grafana / splunk のそれぞれ 1 つだけ）",
      _stores(STORES="s3")[:2] == (0, "OUT: iceberg | G=0 ECS=0")
      and _stores(STORES="grafana")[:2] == (0, "OUT: opensearch,prometheus | G=1 ECS=0")
      and _stores(STORES="splunk")[:2] == (0, "OUT: splunk | G=0 ECS=1")
      and _stores(STORES="s3,splunk")[:2] == (0, "OUT: iceberg,splunk | G=0 ECS=1")
      and _stores('echo "OUT: $SINKS_TF"', STORES="s3")[1] == 'OUT: "iceberg"')
check("STORES の順番・重複・前後の空白は問わない",
      _stores(STORES="splunk, s3 ,s3")[:2] == _stores(STORES="s3,splunk")[:2]
      and _stores(STORES="grafana,s3,grafana")[:2] == _stores(STORES="s3,grafana")[:2])
check("格納先のログはいつも 1 行出す（STORES の値、既定かどうか、有効なまとまり。SKIP_ANALYTICS ならどれも作らないと書く）",
      _stores()[2].count("LOG:") == 1
      and "LOG:    格納先（STORES=s3,grafana,splunk。既定）: s3（S3 Tables） grafana（OpenSearch・Prometheus・Grafana） splunk（Splunk）\n" in _stores()[2]
      and "LOG:    格納先（STORES=splunk,s3）: s3（S3 Tables） splunk（Splunk）\n" in _stores(STORES="splunk,s3")[2]
      and "LOG:    格納先（STORES=grafana）: grafana（OpenSearch・Prometheus・Grafana）。analytics を作らないので、どれも作らない\n"
      in _stores(STORES="grafana", SKIP_ANALYTICS="1")[2])
check("STORES の知らない名前は止まり、使える名前を出す（大文字や空白区切りも知らない名前）",
      all(_stores(STORES=v)[0] == 1 and "使えるのは s3 / grafana / splunk" in _stores(STORES=v)[1] for v in ("s3,elastic", "S3", "s3 grafana", "opensearch"))
      and "STORES の「elastic」は無いまとまり" in _stores(STORES="s3,elastic")[1])
check("STORES の空の要素は止まる（s3,,grafana / 末尾や先頭のカンマ / 空白だけ）",
      all(_stores(STORES=v)[0] == 1 and "STORES に空の要素がある" in _stores(STORES=v)[1] for v in ("s3,,grafana", "s3,", ",s3", " ", "s3, ,splunk")))
_rc, _out, _ = _stores(STORES="splunk", SPLUNK_HEC_URL="https://s:8088")
_rc2, _out2, _ = _stores(STORES="s3", SPLUNK_HEC_URL="https://s:8088")
check("SPLUNK_HEC_URL（2026-09-28 にやめた外の Splunk）が書いてあれば、STORES に splunk が無くても、黙って ECS の Splunk に替えずに止まる",
      _rc == 1 and "SPLUNK_HEC_URL は 2026-09-28 から使わない" in _out and _rc2 == 1 and "deploy.env から消す" in _out2)
check("SPLUNK_SKIP_TLS_VERIFY は使わない（書いてあれば注意だけ）", "for k in ADMIN_ARN OPENSEARCH_CACERT_FILE SPLUNK_SKIP_TLS_VERIFY; do" in up)
_denv_src = open(os.path.join(ROOT, "ops", "deploy-env.sh"), encoding="utf-8").read()
check("deploy-env.sh は STORES と、なくした SINK_* / GRAFANA を読めるキーに持つ（前の deploy.env で知らないキーとして止めず、up.sh が書き換え方を出す）",
      all(re.search(rf'(?<![A-Z_]){k}(?![A-Z_])', _denv_src.split("DEPLOY_ENV_KEYS=")[1].split('"')[1])
          for k in ("STORES", "SINK_S3", "SINK_OPENSEARCH", "SINK_PROMETHEUS", "SINK_SPLUNK", "GRAFANA", "SPLUNK_HEC_URL", "SPLUNK_INDEX", "SPLUNK_SKIP_TLS_VERIFY")))
# なくしたキー（SINK_S3 / SINK_OPENSEARCH / SINK_PROMETHEUS / SINK_SPLUNK / GRAFANA）が残っていれば、当たる STORES を出して止まる
_OLD_WHAT = "はなくなった（2026-10-04 から格納先は STORES だけで選ぶ。s3 / grafana / splunk をカンマで並べ、既定は s3,grafana,splunk）。"
def _old(**env):
    rc, last, out = _stores(**env)
    return last if rc == 1 else f"rc={rc} {last}"
check("SINK_S3=0 は STORES=grafana と書くと言って止まる（どのキーを消すか、まだ何も作っていないことも言う）",
      _old(SINK_S3="0") == "DIE: SINK_S3 " + _OLD_WHAT + "いまの値（SINK_S3=0）は STORES=grafana と書く。deploy.env と環境変数から SINK_S3 を消す。まだ何も作っていない")
check("前の既定（SINK_SPLUNK は 0）と同じ GRAFANA=1 は STORES=s3,grafana と書くと言う（いまの既定は splunk も入るので、既定とは言わない）。yes / no / false も 1 / 0 と同じに読む",
      _old(GRAFANA="1").endswith("いまの値（GRAFANA=1）は STORES=s3,grafana と書く。deploy.env と環境変数から GRAFANA を消す。まだ何も作っていない")
      and "は STORES=s3,grafana と書く" in _old(SINK_SPLUNK="no") and "は STORES=grafana と書く" in _old(SINK_S3="no")
      and "は STORES=grafana と書く" in _old(SINK_S3="false"))
check("SINK_OPENSEARCH=0 と SINK_PROMETHEUS=0 は STORES=s3、SINK_* が全部 0 で SINK_SPLUNK=1 なら STORES=splunk。キーは / で並べる",
      _old(SINK_OPENSEARCH="0", SINK_PROMETHEUS="0") == "DIE: SINK_OPENSEARCH / SINK_PROMETHEUS " + _OLD_WHAT
      + "いまの値（SINK_OPENSEARCH=0 SINK_PROMETHEUS=0）は STORES=s3 と書く。deploy.env と環境変数から SINK_OPENSEARCH / SINK_PROMETHEUS を消す。まだ何も作っていない"
      and "は STORES=splunk と書く" in _old(SINK_S3="0", SINK_OPENSEARCH="0", SINK_PROMETHEUS="0", SINK_SPLUNK="1"))
check("いまの既定と同じ値（SINK_SPLUNK=1 など。3 つとも作る）も止まり、STORES を書かないときの既定と同じと言う",
      all("は STORES=s3,grafana,splunk と同じ（STORES を書かないときの既定）。" in _old(**e) and _old(**e).endswith("まだ何も作っていない")
          for e in ({"SINK_SPLUNK": "1"}, {"SINK_S3": "1", "SINK_SPLUNK": "yes"}, {"GRAFANA": "true", "SINK_SPLUNK": "1"})))
check("格納先が 1 つも残らない値は、STORES では書けないので analytics ごと作らない SKIP_ANALYTICS=1 を出す",
      "は格納先が 1 つも無く、STORES ではそう書けない（空なら既定の s3,grafana,splunk）。analytics ごと要らないなら SKIP_ANALYTICS=1 を書く。"
      in _old(SINK_S3="0", SINK_OPENSEARCH="0", SINK_PROMETHEUS="0"))
check("GRAFANA=0（Grafana を作らずに OpenSearch / Prometheus を作る）はもう選べないと言い、近い 2 つの STORES を出す",
      _old(GRAFANA="0") == "DIE: GRAFANA " + _OLD_WHAT + "いまの値（GRAFANA=0）は Grafana を作らずに OpenSearch か Prometheus を作る組み合わせで、"
      "その組み合わせはもう選べない（OpenSearch・Prometheus・Grafana は STORES の grafana でまとめて作るか作らないか）。"
      "近いのは STORES=s3,grafana（3 つとも作る）か、STORES=s3（3 つとも作らない）。deploy.env と環境変数から GRAFANA を消す。まだ何も作っていない")
check("Prometheus だけ・OpenSearch だけも選べないと言う。STORES の grafana を外すと格納先が残らないときは SKIP_ANALYTICS=1 を出す",
      "は Prometheus だけを作る（OpenSearch は作らない）組み合わせで、その組み合わせはもう選べない" in _old(SINK_OPENSEARCH="0")
      and "は OpenSearch だけを作る（Prometheus は作らない）組み合わせで" in _old(SINK_PROMETHEUS="0")
      and "近いのは STORES=s3,grafana,splunk（3 つとも作る）か、STORES=s3,splunk（3 つとも作らない）" in _old(SINK_PROMETHEUS="0", SINK_SPLUNK="1")
      and "近いのは STORES=grafana（3 つとも作る）か、格納先が残らないので analytics ごと作らない SKIP_ANALYTICS=1。" in _old(SINK_S3="0", GRAFANA="0")
      and _old(SINK_S3="0", GRAFANA="0").startswith("DIE: SINK_S3 / GRAFANA はなくなった"))
check("なくしたキーに 1 / 0 以外が書いてあれば、当たる STORES を決められないと言って止まる",
      "いまの値の SINK_S3=2 GRAFANA=x は 1 / 0 でないので、当たる STORES を決められない。" in _old(SINK_S3="2", GRAFANA="x"))
check("STORES と一緒になくしたキーが書いてあっても止まり、消せば STORES が効くと言う",
      _old(STORES="s3", SINK_S3="1").endswith("deploy.env と環境変数から SINK_S3 を消す（STORES=s3 も書いてあるので、消せばそちらが効く）。まだ何も作っていない")
      and all(_stores(STORES="splunk", **{k: "0"})[0] == 1 for k in ("SINK_S3", "SINK_OPENSEARCH", "SINK_PROMETHEUS", "SINK_SPLUNK", "GRAFANA")))
check("なくしたキーの空の値（SINK_S3= / GRAFANA=）は書いていないのと同じ（deploy.env の空の値と同じ扱い）",
      _stores(STORES="s3", SINK_S3="", GRAFANA="")[:2] == (0, "OUT: iceberg | G=0 ECS=0") and _stores(SINK_SPLUNK="")[:2] == _stores()[:2])
check("STORES で選んだあとも今の検査が効く（grafana だけでは SNMP_POLL=1 でないと WORKFLOW の送り手が無く止まる。splunk なら通る）",
      _stores(_sndblk + _ST_OUT, STORES="s3,grafana", WORKFLOW="1", SNMP_POLL="")[1].startswith("DIE: WORKFLOW はアラートの送り手が要る")
      and _stores(_sndblk + _ST_OUT, STORES="s3,grafana", WORKFLOW="1", SNMP_POLL="1")[:2] == (0, "OUT: iceberg,opensearch,prometheus | G=1 ECS=0")
      and _stores(_sndblk + _ST_OUT, STORES="splunk", WORKFLOW="1")[:2] == (0, "OUT: splunk | G=0 ECS=1"))
# 既定（deploy.env に何も書かない）で WORKFLOW=1 にすると、link_down の送り手は Grafana と Splunk の両方になる（設計の検証。cycle 002）
_snmp_line = up[up.index('SNMP_POLL="${SNMP_POLL:-1}"'):].split("\n", 1)[0] + "\n"
check("既定のまま WORKFLOW=1 なら送り手は grafana,splunk、格納先は 4 つ（STORES は s3,grafana,splunk、SNMP_POLL は 1 が既定）",
      _stores(_snmp_line + _sndblk + 'echo "OUT: ${LINK_DOWN_SENDERS:-none} | $SINKS"', WORKFLOW="1")[:2]
      == (0, "OUT: grafana,splunk | iceberg,opensearch,prometheus,splunk"))
check("SKIP_ANALYTICS=1 なら STORES に grafana / splunk があっても Grafana と ECS の Splunk は作らない",
      _stores(STORES="grafana,splunk", SKIP_ANALYTICS="1")[:2] == (0, "OUT: opensearch,prometheus,splunk | G=0 ECS=0"))
import shutil, tempfile
def _stores_file(text, **env):  # deploy.env から読ませる（ops/deploy-env.sh の load_deploy_env を通す）
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "deploy.env"), "w") as f:
            f.write(text)
        r = subprocess.run(["bash", "-c", '. "$DENV"\n' + 'log() { echo "LOG: $*"; }\ndie() { echo "DIE: $*"; exit 1; }\nload_deploy_env >/dev/null\n'
                            + _blk + _g3 + _ST_OUT], capture_output=True, text=True,
                           env={"PATH": os.environ["PATH"], "DENV": os.path.join(ROOT, "ops", "deploy-env.sh"), "DEPLOY_ENV_FILE": os.path.join(d, "deploy.env"), **env})
        return r.returncode, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr
    finally:
        shutil.rmtree(d)
check("deploy.env の STORES を読む（値の後ろのコメントも外す）。STORES= の空は既定",
      _stores_file("OWNER=a\nSTORES=grafana  # コメント\n") == (0, "OUT: opensearch,prometheus | G=1 ECS=0")
      and _stores_file("OWNER=a\nSTORES=\n") == (0, "OUT: iceberg,opensearch,prometheus,splunk | G=1 ECS=1"))
check("前の deploy.env の SINK_* / GRAFANA は読めて（知らないキーで止まらず）、up.sh が当たる STORES を出して止まる。環境変数でも同じ。空の値は書いていないのと同じ",
      _stores_file("OWNER=a\nGRAFANA=0\n")[1].startswith("DIE: GRAFANA はなくなった")
      and _stores_file("OWNER=a\nSINK_SPLUNK=1\n")[1].endswith("は STORES=s3,grafana,splunk と同じ（STORES を書かないときの既定）。deploy.env と環境変数から SINK_SPLUNK を消す。まだ何も作っていない")
      and "（STORES=s3 も書いてあるので、消せばそちらが効く）" in _stores_file("OWNER=a\nSTORES=s3\nSINK_S3=1\n")[1]
      and _stores_file("OWNER=a\nSTORES=splunk\n", SINK_S3="0")[1].startswith("DIE: SINK_S3 はなくなった")
      and _stores_file("OWNER=a\nSTORES=s3\nSINK_S3=\nGRAFANA=\n") == (0, "OUT: iceberg | G=0 ECS=0"))
_envx = open(ENV_EXAMPLE, encoding="utf-8").read()
_envx_st = _envx[_envx.index("# analytics の格納先を 3 つのまとまりで選ぶ"):_envx.index("#STORES=s3,grafana,splunk")]
check("deploy.env.example は STORES を既定の s3,grafana,splunk で書き、その前にまとまりごとの中身・外すと無くなるもの・費用と、外すとデータごと消えることを書く",
      re.search(r"^#STORES=s3,grafana,splunk$", _envx, re.M) is not None and _envx.count("#STORES=") == 1
      and all(re.search(rf"^#   {g} +全トピック|^#   {g} +traps と logs", _envx_st, re.M) for g in ("s3", "grafana", "splunk"))
      and all(k in _envx_st for k in ("**既定は s3,grafana,splunk = 3 つとも**", "+$0.21/h", "約 +$0.60/h", "約 +$0.34/h", "データごと消える",
                                      "SINK_S3 / SINK_OPENSEARCH / SINK_PROMETHEUS / SINK_SPLUNK / GRAFANA は 2026-10-04 になくした"))
      and _envx_st.count("#            外すと") == 3
      and "OpenSearch Serverless の $0.01 と、aps-workspaces と sns の $0.014 ずつ" in _envx_st)
# deploy.env.example の PIPELINE=1 の金額を、up.sh のエンドポイントの選び方と費用の目安を切り出して出した値と比べる（*_AZ_NUM は既定。
# OpenSearch Serverless の VPC エンドポイントは 2026-10-04 から ENDPOINTS_AZ_NUM に従うので、既定の 1 AZ で $0.01）
_fullcost = up[up.index("# ここを変えたら README"):up.index("COST_NOTE=$(")]
def _pipeline_cents(stores="s3,grafana,splunk", skip=()):
    sk = set(skip) | ({"analytics"} if "stream" in skip else set())   # stream を作らなければ analytics も作らない
    st = set(stores.split(","))
    an = "analytics" not in sk
    roots = "base/ecr base/core " + " ".join(f"pipeline/{r}" for r in ("lab", "stream", "analytics", "graph") if r not in sk) + " pipeline/nautobot"
    env = {"PATH": os.environ["PATH"], "PIPELINE": "1", "NAUTOBOT": "1", "SNMP_POLL": "1",
           **{f"SKIP_{r.upper()}": "1" if r in sk else "" for r in ("lab", "stream", "analytics", "graph")},
           "SINK_S3": "1" if "s3" in st else "", "SINK_OPENSEARCH": "1" if "grafana" in st else "", "SINK_PROMETHEUS": "1" if "grafana" in st else "",
           "SINK_SPLUNK": "1" if "splunk" in st else "", "GRAFANA": "1" if an and "grafana" in st else "", "SPLUNK_ON_ECS": "1" if an and "splunk" in st else "",
           "ENDPOINTS_AZ_NUM": "1", "MSK_AZ_NUM": "2", "TELEGRAF_AZ_NUM": "1", "NEPTUNE_AZ_NUM": "1", "OPENSEARCH_AZ_NUM": "1", "NAUTOBOT_DB_AZ_NUM": "1",
           "SPLUNK_TASKS": "1"}
    r = subprocess.run(["bash", "-c", f'ROOTS="{roots}"\n' + _epblk + _fullcost + 'echo "OUT: $COST_CENTS"'], capture_output=True, text=True, env=env)
    return int(r.stdout.split("OUT: ")[1]) if "OUT: " in r.stdout else r.stderr
_usd = lambda c: f"${c // 100}.{c % 100:02d}/h"
_pc = _pipeline_cents()
check("deploy.env.example の PIPELINE=1 の金額（既定の STORES / STORES=s3 / SKIP_STREAM / SKIP_ANALYTICS で下がる分）が up.sh の費用の目安と同じ",
      isinstance(_pc, int)
      and f"約 {_usd(_pc)}（STORES が既定の s3,grafana,splunk のとき。STORES=s3 なら約 {_usd(_pipeline_cents('s3'))}" in _envx
      and re.search(rf"^#SKIP_STREAM=1 .*約 {re.escape(_usd(_pc - _pipeline_cents(skip=('stream',))))} 下がる（STORES が既定のとき）", _envx, re.M) is not None
      and re.search(rf"^#SKIP_ANALYTICS=1 .*約 {re.escape(_usd(_pc - _pipeline_cents(skip=('analytics',))))} 下がる（STORES が既定のとき）", _envx, re.M) is not None)
check("deploy.env.example に SINK_* / GRAFANA のキーの行は無い。SPLUNK_INDEX は STORES のあとに空で書く。SPLUNK_HEC_URL / SPLUNK_SKIP_TLS_VERIFY も書かない（2026-09-28 にやめた）",
      re.search(r"^#?\s*(SINK_[A-Z0-9]+|GRAFANA|SINKS)=", _envx, re.M) is None
      and re.search(r"^#SPLUNK_INDEX=$", _envx, re.M) is not None and _envx.index("#STORES=s3,grafana,splunk") < _envx.index("#SPLUNK_INDEX=")
      and "SPLUNK_HEC_URL" not in _envx and "SPLUNK_SKIP_TLS_VERIFY" not in _envx)
check("MSK Connect の Splunk は書いていない（2026-09-26 に Spark から書くことにした）",
      "MSK Connect で後回し" not in tf and "MSK Connect で後回し" not in src and "MSK Connect で後回し" not in up)
check("up.sh は analytics を stream の後に apply し、ジョブを格納先ごとに STREAMING で起こす（名前は job_name の sinks-s3iceberg / sinks-splunk / sinks-grafana。snmp は付けない）",
      up.index("tf_apply pipeline/stream") < up.index("tf_apply pipeline/analytics") < up.index('--name "$NAME" --mode STREAMING')
      and "--name snmp-sinks" not in up and '--name "snmp-sinks' not in up and 'for JOB in iceberg splunk http; do' in up
      and 'JOB_DRIVER=$(tf pipeline/analytics output -raw "job_driver_json_$JOB")' in up)
_jn = up[up.index("  job_name() {"):up.index("\n  }\n", up.index("  job_name() {")) + 5]
def _job_name(key):
    r = subprocess.run(["bash", "-c", 'die() { echo "DIE: $*"; exit 1; }\n' + _jn + f'job_name "{key}"'], capture_output=True, text=True)
    return r.stdout.strip()
check("up.sh: ジョブのキーからジョブ名を引くのは job_name の 1 か所（iceberg → sinks-s3iceberg、splunk → sinks-splunk、http → sinks-grafana。知らないキーは止まる）",
      [_job_name(k) for k in ("iceberg", "splunk", "http")] == ["sinks-s3iceberg", "sinks-splunk", "sinks-grafana"]
      and _job_name("opensearch").startswith("DIE: job_name: 知らないジョブのキー")
      and up.count("job_name() {") == 1 and up.index("  job_name() {") < up.index('log "7-5.')
      and '"sinks-$JOB"' not in up and re.search(r"(?<!snmp-)sinks-(iceberg|http)", up) is None
      and up.count('NAME=$(job_name "$JOB")') == 2 and 'WAIT_NAMES="${WAIT_NAMES}$(job_name "$JOB") "' in up)
check("up.sh は analytics に Spark のジョブ 1 つにつき 21 セント（S3 / Splunk / OpenSearch か Prometheus）と opensearch の OCU を足し、opensearch は analytics を作るときだけ OCU の注意を出す",
      'if [ -n "$SINK_S3" ]; then COST_CENTS=$((COST_CENTS + 21)); fi\n  if [ -n "$SINK_SPLUNK" ]; then COST_CENTS=$((COST_CENTS + 21)); fi\n'
      '  if [ -n "$SINK_OPENSEARCH$SINK_PROMETHEUS" ]; then COST_CENTS=$((COST_CENTS + 21)); fi\n' in up
      and up.count("COST_CENTS + 21") == 3 and "COST_CENTS=2\n" in up
      and re.search(r'\*,opensearch,\*\) if \[ -z "\$SKIP_ANALYTICS" \]; then printf', up) is not None)
check("up.sh はジョブごとに同じ SpecHash のものが動いていれば起こさない", "--states SUBMITTED PENDING SCHEDULED RUNNING QUEUED --query 'jobRuns[].[name,id]'" in up
      and "jobRun.tags.SpecHash" in up and 'if [ -z "$KEEP" ] && [ "$spec" = "$JOB_SPEC" ]; then KEEP="$id"; else STALE="$STALE $id"; fi' in up)
check("up.sh は SpecHash が違うジョブを cancel し、止まるのを待ってから、SpecHash のタグを付けて起こし直す（スクリプトや引数の変更を反映する）",
      re.search(r'cancel-job-run[\s\S]*CANCELLING[\s\S]*--name "\$NAME" --mode STREAMING[\s\S]*--tags "[^"]*SpecHash=\$JOB_SPEC"', up) is not None)
check("up.sh は PIPELINE=1 で SKIP_STREAM=1 なら analytics も飛ばす", re.search(r'SKIP_STREAM=1 なので analytics も作らない[^\n]*\n\s*SKIP_ANALYTICS=1', up) is not None)
check("down.sh は名前で絞らずに動いているジョブを全部 cancel する（sinks-s3iceberg / sinks-splunk / sinks-grafana も、古い名前の snmp-sinks / snmp-sinks-<キー> も止まる）",
      re.search(r"list-job-runs [^\n]*\\\n\s*--states SUBMITTED PENDING SCHEDULED RUNNING QUEUED --query 'jobRuns\[\]\.id'", down) is not None
      and "jobRuns[?name" not in down and "sinks-" not in down and "snmp-sinks" not in down)
check("down.sh は job を cancel → stop-application → destroy analytics → destroy graph の順",
      down.index("cancel-job-run") < down.index("stop-application") < down.index("destroy_root pipeline/analytics") < down.index("destroy_lambda_root pipeline/graph") < down.index("destroy_root pipeline/stream"))
# .py は名指しで並べず find で全部見る（名指しだとファイルを足したときに構文検査から漏れる）
check("check.sh は spark/ の .py を構文検査に入れ、spark/snmp_sinks.py がある",
      re.search(r"find [\w /]*\bspark\b [^\n]*-name '\*\.py'", checksh) is not None
      and os.path.isfile(os.path.join(ROOT, "spark", "snmp_sinks.py")) and "snmp_to_iceberg" not in checksh)
check("up.sh の WORKFLOW=1 は SKIP_ANALYTICS があれば止まる（Grafana / Splunk のアラートが無いとワーカーが起きない）",
      re.search(r'if \[ -n "\$WORKFLOW" \]; then\n[\s\S]*?-n "\$SKIP_ANALYTICS"[\s\S]*?-n "\$SKIP_GRAPH"[\s\S]*?die "WORKFLOW は lab と stream と analytics と graph が要る', up) is not None)
check("up.sh は SKIP_GRAPH=1 でも analytics を作る（Spark は Neptune に書かない。2026-10-02）", "analytics は graph が要る" not in up)
check("up.sh は PIPELINE=0 なら lab / stream / analytics / graph を全部飛ばす",
      re.search(r'else\n\s*SKIP_LAB=1; SKIP_STREAM=1; SKIP_ANALYTICS=1; SKIP_GRAPH=1\n', up) is not None)
# lab は単独で外せる（2026-10-04 まで、SKIP_LAB=1 で stream を作ると止まっていた）。WORKFLOW と PIPELINE の判定を切り出して動かす
_skblk = up[up.index('if [ -n "$WORKFLOW" ]; then\n  if [ -z "$AGENT" ]'):up.index("# Nautobot（terraform/pipeline/nautobot）は機器の一覧とケーブルの正")]
def _skip(**env):
    r = subprocess.run(["bash", "-c", _pre + "flag_value SKIP_LAB; flag_value SKIP_STREAM; flag_value SKIP_ANALYTICS; flag_value SKIP_GRAPH\n" + _skblk
                        + 'echo "OUT: L=${SKIP_LAB:-0} S=${SKIP_STREAM:-0} A=${SKIP_ANALYTICS:-0} G=${SKIP_GRAPH:-0}"'], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], **env})
    return r.stdout.strip()
check("up.sh は PIPELINE=1 で SKIP_LAB=1 だけなら止まらず、lab 以外を作る（Telegraf の取りにいく側が届かないことを言う）",
      _skip(PIPELINE="1", SKIP_LAB="1").endswith("OUT: L=1 S=0 A=0 G=0") and "SKIP_LAB=1 なので lab は作らない" in _skip(PIPELINE="1", SKIP_LAB="1")
      and "タスクは落ちない" in _skip(PIPELINE="1", SKIP_LAB="1") and "DIE" not in _skip(PIPELINE="1", SKIP_LAB="1")
      and "stream は lab が要る" not in up)
check("up.sh は SKIP_LAB=1 でも stream を作らないなら lab の注意を出さず、lab を作るときも出さない",
      "lab は作らない" not in _skip(PIPELINE="1", SKIP_LAB="1", SKIP_STREAM="1") and "lab は作らない" not in _skip(PIPELINE="1")
      and _skip(PIPELINE="1") == "OUT: L=0 S=0 A=0 G=0")
check("up.sh は lab が無く MDT_SOURCE_CIDRS も空で stream を作るなら「この stream には何も届かない」と注意を出して続ける",
      "この stream には何も届かない" in _skip(PIPELINE="1", SKIP_LAB="1") and _skip(PIPELINE="1", SKIP_LAB="1").endswith("OUT: L=1 S=0 A=0 G=0")
      and "何も届かない" not in _skip(PIPELINE="1", SKIP_LAB="1", MDT_SOURCE_CIDRS="10.10.0.0/16")
      and "何も届かない" not in _skip(PIPELINE="1", MDT_SOURCE_CIDRS="") and "何も届かない" not in _skip(PIPELINE="1", SKIP_LAB="1", SKIP_STREAM="1"))
check("up.sh の「土台だけになる」は SKIP_LAB と SKIP_STREAM と SKIP_GRAPH が全部あるときだけ（lab と graph だけ外しても stream は作る）",
      "土台だけになる" in _skip(PIPELINE="1", SKIP_LAB="1", SKIP_STREAM="1", SKIP_GRAPH="1")
      and _skip(PIPELINE="1", SKIP_LAB="1", SKIP_STREAM="1", SKIP_GRAPH="1").endswith("OUT: L=1 S=1 A=1 G=1")
      and "土台だけ" not in _skip(PIPELINE="1", SKIP_LAB="1", SKIP_GRAPH="1")
      and _skip(PIPELINE="1", SKIP_LAB="1", SKIP_GRAPH="1").endswith("OUT: L=1 S=0 A=0 G=1")
      and "土台だけ" not in _skip(PIPELINE="1", SKIP_LAB="1", SKIP_STREAM="1"))
check("up.sh の WORKFLOW=1 は SKIP_LAB=1 を受け付けないまま",
      "DIE: WORKFLOW は lab と stream と analytics と graph が要る" in _skip(WORKFLOW="1", AGENT="1", PIPELINE="1", SKIP_LAB="1")
      and "DIE" not in _skip(WORKFLOW="1", AGENT="1", PIPELINE="1"))
check("up.sh は lab が無ければ lab の syslog の注意・7-3b のトポロジの投入・lab の費用・転送・最後の lab の案内を飛ばす",
      'if [ -z "$SKIP_LAB" ] && [ "$SYSLOG_STANDARD" != "$LAB_SYSLOG_STANDARD" ]; then' in up
      and re.search(r'\n  if \[ -z "\$SKIP_LAB" \]; then\n    log "7-3b\.[^\n]*\n(    [^\n]*\n)*?    LAB_TOPOLOGY_B64=[^\n]*\n    run_on_instance [^\n]*LAB_TOPOLOGY_B64[^\n]*\n  else\n', up) is not None
      and up.count("LAB_TOPOLOGY_B64=$(") == 1
      and 'if [ -z "$SKIP_LAB" ]; then COST_CENTS=$((COST_CENTS + 17)); fi' in up
      and re.search(r'LAB_VARS=\(-var forward_to_telegraf=false\)\nif \[ -z "\$SKIP_LAB" \]; then\n', up) is not None
      and re.search(r'\nif \[ -n "\$LAB_INSTANCE_ID" \]; then\n  echo "lab に入るコマンド:"', up) is not None
      and re.search(r'\nif \[ -n "\$LAB_INSTANCE_ID" \]; then\n  # lab の EC2 の中を見る', up) is not None)
check("up.sh と deploy.env.example の SKIP_LAB の説明は、SKIP_STREAM=1 が要るとも止まるとも言わない",
      "stream は lab が要るので SKIP_STREAM=1 も要る" not in up
      and re.search(r"^#SKIP_LAB=1 [^\n]*\n#\s+#[^\n]*Nautobot の Job が書く物理層だけ", env_example, re.M) is not None
      and "しないと止まる" not in env_example and "タスクは落ちない" in env_example)
# NO_PORTFORWARD は 2026-10-04 に NO_DASHBOARD_PORTFORWARD へ名前を変えた。前の名前が残っていれば止まる
_pfblk = up[up.index('[ -z "${NO_PORTFORWARD:-}" ] || die'):up.index("flag_value NO_DASHBOARD_PORTFORWARD\n") + len("flag_value NO_DASHBOARD_PORTFORWARD\n")]
def _pf(**env):
    r = subprocess.run(["bash", "-c", _pre + _pfblk + 'echo "OUT: ${NO_DASHBOARD_PORTFORWARD:-0}"'], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], **env})
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr
def _pf_file(text, **env):  # deploy.env から読ませる（ops/deploy-env.sh の load_deploy_env を通す）
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "deploy.env"), "w") as f:
            f.write(text)
        r = subprocess.run(["bash", "-c", '. "$DENV"\n' + 'log() { echo "LOG: $*"; }\ndie() { echo "DIE: $*"; exit 1; }\nload_deploy_env >/dev/null\n'
                            + _pfblk + 'echo "OUT: ${NO_DASHBOARD_PORTFORWARD:-0}"'], capture_output=True, text=True,
                           env={"PATH": os.environ["PATH"], "DENV": os.path.join(ROOT, "ops", "deploy-env.sh"), "DEPLOY_ENV_FILE": os.path.join(d, "deploy.env"), **env})
        return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr
    finally:
        shutil.rmtree(d)
check("NO_DASHBOARD_PORTFORWARD は 1 / 0（true / yes も）で、既定 0（ポートフォワーディングを開く）",
      _pf() == "OUT: 0" and _pf(NO_DASHBOARD_PORTFORWARD="1") == "OUT: 1" and _pf(NO_DASHBOARD_PORTFORWARD="yes") == "OUT: 1"
      and _pf(NO_DASHBOARD_PORTFORWARD="0") == "OUT: 0")
check("前の名前 NO_PORTFORWARD が環境変数にあれば、0 でも止まって書き換え方を出す（何も作る前）",
      _pf(NO_PORTFORWARD="1") == "DIE: NO_PORTFORWARD は NO_DASHBOARD_PORTFORWARD に変わった（2026-10-04。意味は同じで、1 なら最後の Web へのポートフォワーディングを開かずに終わる）。"
      "deploy.env と環境変数の NO_PORTFORWARD=1 を NO_DASHBOARD_PORTFORWARD=1 に書き換える。まだ何も作っていない"
      and "NO_PORTFORWARD=0 を NO_DASHBOARD_PORTFORWARD=0 に書き換える" in _pf(NO_PORTFORWARD="0")
      and _pf(NO_PORTFORWARD="1", NO_DASHBOARD_PORTFORWARD="1").startswith("DIE: NO_PORTFORWARD は NO_DASHBOARD_PORTFORWARD に変わった")
      and up.index('[ -z "${NO_PORTFORWARD:-}" ] || die') < up.index("CALLER_ARN=$(aws sts get-caller-identity"))
check("前の deploy.env の NO_PORTFORWARD は読めて（知らないキーで止まらず）止まる。空の値は書いていないのと同じ。新しい名前は deploy.env から読める",
      _pf_file("OWNER=a\nNO_PORTFORWARD=1\n").startswith("DIE: NO_PORTFORWARD は NO_DASHBOARD_PORTFORWARD に変わった")
      and _pf_file("OWNER=a\nNO_PORTFORWARD=\n") == "OUT: 0" and _pf_file("OWNER=a\nNO_DASHBOARD_PORTFORWARD=1\n") == "OUT: 1")
check("up.sh は NO_DASHBOARD_PORTFORWARD で Session Manager plugin の検査と最後のポートフォワーディングを飛ばし、NO_PORTFORWARD は止めるためだけに見る",
      'if [ -z "$NO_DASHBOARD_PORTFORWARD" ]; then\n  command -v session-manager-plugin' in up and "開かないなら NO_DASHBOARD_PORTFORWARD=1）" in up
      and 'if [ -n "$NO_DASHBOARD_PORTFORWARD" ]; then exit 0; fi\nlog "10. ポートフォワーディング' in up
      and [l for l in up.split("set -euo pipefail", 1)[1].splitlines() if re.search(r"(?<![A-Z_])NO_PORTFORWARD(?![A-Z_])", l) and not l.startswith("#")]
      == [l for l in up.splitlines() if l.startswith('[ -z "${NO_PORTFORWARD:-}" ] || die')]
      and "flag_value NO_PORTFORWARD" not in up)
check("deploy.env.example は NO_DASHBOARD_PORTFORWARD を書き、前の名前のキーの行は無い。deploy-env.sh は両方を読めるキーに持つ",
      re.search(r"^#NO_DASHBOARD_PORTFORWARD=1$", env_example, re.M) is not None and re.search(r"^#?\s*NO_PORTFORWARD=", env_example, re.M) is None
      and all(re.search(rf"(?<![A-Z_]){k}(?![A-Z_])", open(os.path.join(ROOT, "ops", "deploy-env.sh"), encoding="utf-8").read().split("DEPLOY_ENV_KEYS=", 1)[1].split('"')[1])
              for k in ("NO_DASHBOARD_PORTFORWARD", "NO_PORTFORWARD")))
# ふだん書かないキーは deploy.env.example と up.sh のヘッダーの最後の 2 節（冗長化用 → デバッグ用）にまとめる（2026-10-04）。並べ替えただけで、読み方と既定は変えない
_RED_H = "# ---- 冗長化用（既定は 1 AZ。MSK だけ既定 2 AZ。本番の形を試すときに書く） ----"
_DBG_H = "# ---- デバッグ用（ふだんは書かない） ----"
def _key_sections(text, key_re):  # 見出しで ふだん / 冗長化用 / デバッグ用 に切り、各節に出てくるキーの名前を出てくる順に返す
    if text.count(_RED_H) != 1 or text.count(_DBG_H) != 1 or text.index(_RED_H) > text.index(_DBG_H):
        return None
    a, b = text.index(_RED_H), text.index(_DBG_H)
    return [re.findall(key_re, t, re.M) for t in (text[:a], text[a:b], text[b:])]
_env_secs = _key_sections(env_example, r"^#?([A-Z][A-Z0-9_]*)=")
_up_hdr = up[up.index("# 設定できるキー（"):up.index("# AGENT / PIPELINE / WORKFLOW / CREATE_KB / SKIP_* /")]
_up_secs = _key_sections(_up_hdr, r"^#   ([A-Z][A-Z0-9_]*)")
_AZ_KEYS = ["ENDPOINTS_AZ_NUM", "MSK_AZ_NUM", "RUNTIME_AZ_NUM", "EMR_AZ_NUM", "LAMBDA_AZ_NUM", "NEPTUNE_AZ_NUM", "OPENSEARCH_AZ_NUM",
            "NAUTOBOT_DB_AZ_NUM", "TELEGRAF_AZ_NUM", "SPLUNK_AZ_NUM"]
check("deploy.env.example と up.sh のヘッダーは、冗長化用の節に 10 の *_AZ_NUM、最後のデバッグ用の節に NETWORK_PERIMETER と TF_VERBOSE だけを置く（ENDPOINTS_MULTI_AZ のキーの行は無い）",
      _env_secs is not None and _env_secs[1:] == [_AZ_KEYS, ["NETWORK_PERIMETER", "TF_VERBOSE"]]
      and _up_secs is not None and _up_secs[1:] == [_AZ_KEYS, ["NETWORK_PERIMETER", "TF_VERBOSE"]]
      and not any(k in _env_secs[0] or k in _up_secs[0] for k in _AZ_KEYS + ["ENDPOINTS_MULTI_AZ", "NETWORK_PERIMETER", "TF_VERBOSE"]))
_red_env = env_example[env_example.index(_RED_H):env_example.index(_DBG_H)]
_red_up = _up_hdr[_up_hdr.index(_RED_H):_up_hdr.index(_DBG_H)]
check("冗長化用の節: MSK だけ「1 にはできない」を理由つきで書き、Runtime は 2 以上でエンドポイントをそろえると書く。"
      "1 台でしか成り立たない 5 つ（Web / lab / Grafana / Nautobot / workflow）も理由つきで 1 行ずつ。"
      "Splunk はそこに入れず、「Splunk をクラスターにする（004）」の SPLUNK_AZ_NUM（2 か 3 でクラスター）を書く",
      all("**1 にはできない**（MSK はブローカーを 2 か 3 の AZ にしか置けない）" in t and t.count("1 にはできない") == 1
          and "2 以上にするとエンドポイントも同じ数にそろえる" in t and "これより小さく書いてあれば止まる" in t
          and "1 台でしか成り立たないのでキーを作らないもの" in t
          and all(re.search(r"^#\s+" + w + r"（[^\n]+）、?$", t, re.M) for w in ("Web の EC2", "lab の EC2", "Grafana", "Nautobot", "workflow"))
          and "キーがまだ無いもの" not in t and "「Splunk をクラスターにする（004）」" in t
          and t.index("SPLUNK_AZ_NUM=") < t.index("1 台でしか成り立たないのでキーを作らないもの")
          for t in (_red_env, _red_up))
      and all(re.search(r"^#" + k + r"=[23]$", _red_env, re.M) for k in _AZ_KEYS)
      and all(re.search(r"^#   " + k + "=" + d + r" ", _red_up, re.M) for k, d in zip(_AZ_KEYS, "1211111111")))
check("切り分けに使わないキー（HTTP_SEND / MAX_OFFSETS_PER_TRIGGER* / KEEP_ECR / NO_DASHBOARD_PORTFORWARD / LOCAL_PORT / IMAGE_TAG / AWS_*）はふだんの節のまま",
      _env_secs is not None and _up_secs is not None
      and all(k in _env_secs[0] for k in ("HTTP_SEND", "MAX_OFFSETS_PER_TRIGGER", "MAX_OFFSETS_PER_TRIGGER_ICEBERG", "MAX_OFFSETS_PER_TRIGGER_SPLUNK",
                                          "MAX_OFFSETS_PER_TRIGGER_OPENSEARCH", "MAX_OFFSETS_PER_TRIGGER_PROMETHEUS", "KEEP_ECR",
                                          "NO_DASHBOARD_PORTFORWARD", "LOCAL_PORT", "IMAGE_TAG", "AWS_PROFILE", "AWS_CA_BUNDLE"))
      and all(k in _up_secs[0] for k in ("HTTP_SEND", "MAX_OFFSETS_PER_TRIGGER", "NO_DASHBOARD_PORTFORWARD", "LOCAL_PORT", "IMAGE_TAG", "AWS_PROFILE")))
check("deploy.env.example のキーの行は 1 つのキーにつき 1 行で、デバッグ用の節がファイルの最後（後ろにキーの行も別の節も無い）",
      (lambda ks: len(ks) == len(set(ks)))(re.findall(r"^#?([A-Z][A-Z0-9_]*)=", env_example, re.M))
      and env_example.rstrip("\n").endswith("#TF_VERBOSE=0") and "# ---- " not in env_example[env_example.index(_DBG_H) + len(_DBG_H):])
check("デバッグ用のキーは、それぞれ何の切り分けに使うかを 1 行目に書き、見本の値は既定の逆（NETWORK_PERIMETER=0）か既定（TF_VERBOSE=0）のまま",
      re.search(r"^# AccessDenied の切り分け: [^\n]*\n(?:#[^\n]*\n)*?#NETWORK_PERIMETER=0$", env_example, re.M) is not None
      and re.search(r"^# terraform の失敗・遅さの切り分け: [^\n]*\n(?:#[^\n]*\n)*?#TF_VERBOSE=0$", env_example, re.M) is not None
      and re.search(r"^#   NETWORK_PERIMETER=0 +AccessDenied の切り分け。", _up_hdr, re.M) is not None
      and re.search(r"^#   TF_VERBOSE=1 +terraform の失敗・遅さの切り分け。", _up_hdr, re.M) is not None
      and re.search(r"^#ENDPOINTS_MULTI_AZ=", env_example, re.M) is None)
check("DEPLOY_ENV_KEYS に 10 の *_AZ_NUM と、止めるために読む ENDPOINTS_MULTI_AZ、NETWORK_PERIMETER / TF_VERBOSE がある。up.sh の NETWORK_PERIMETER の既定は 1 のまま",
      all(re.search(rf"(?<![A-Z_]){k}(?![A-Z_])", open(os.path.join(ROOT, "ops", "deploy-env.sh"), encoding="utf-8").read().split("DEPLOY_ENV_KEYS=", 1)[1].split('"')[1])
          for k in _AZ_KEYS + ["ENDPOINTS_MULTI_AZ", "NETWORK_PERIMETER", "TF_VERBOSE"])
      and 'NETWORK_PERIMETER="${NETWORK_PERIMETER:-1}"\nflag_value NETWORK_PERIMETER\n' in up and "flag_value ENDPOINTS_MULTI_AZ" not in up)
# AZ_NUM の検査を up.sh から切り出して動かす（何も作る前に止まる・注意を出す）
_azblk = up[up.index('case "${ENDPOINTS_MULTI_AZ:-}" in'):up.index('if [ -z "$NETWORK_PERIMETER" ]; then echo "NETWORK_PERIMETER=0')]
# az_num と AZ_NUM_SET は ops/up-common.sh にある（上の up は先頭にそれをつないである）ので、切り出しの前に置く
_azfn = up[up.index('AZ_NUM_SET=""'):up.index("\n}\n", up.index("az_num() {")) + 3]
def _aznum(**env):   # 既定は graph も analytics も作らない回（PIPELINE=0 と同じ）。graph-status の待ちの注意を見るときは SKIP_GRAPH="" と SKIP_ANALYTICS="" を渡す
    r = subprocess.run(["bash", "-uc", 'die() { echo "DIE: $*"; exit 1; }\n' + _azfn + _azblk + 'echo "OUT: ' + " ".join("$" + k for k in _AZ_KEYS) + '"'],
                       capture_output=True, text=True, env={"PATH": os.environ["PATH"], **_AZ_ENV, **env})
    return r.stdout.strip() or r.stderr
_AZ_ENV = {"SKIP_GRAPH": "1", "SKIP_ANALYTICS": "1", "SPLUNK_ON_ECS": "1", "STORES": "s3,grafana,splunk", "SPLUNK_INDEX": ""}   # SPLUNK_AZ_NUM の組み合わせの検査は SKIP_ANALYTICS="" を渡す
check("AZ_NUM: 書かなければ ENDPOINTS 1 / MSK 2 / ほか 1（Runtime も 1）で、注意は出ない（既定の MSK の 2 は数えない）",
      _aznum() == "OUT: 1 2 1 1 1 1 1 1 1 1")
check("AZ_NUM: 範囲の中はそのまま使い、先頭の 0 は外す（08 も 8 進数にしない）",
      _aznum(ENDPOINTS_AZ_NUM="3", MSK_AZ_NUM="3", RUNTIME_AZ_NUM="3", EMR_AZ_NUM="3", LAMBDA_AZ_NUM="3", NEPTUNE_AZ_NUM="3",
             OPENSEARCH_AZ_NUM="2", NAUTOBOT_DB_AZ_NUM="2", TELEGRAF_AZ_NUM="3", SPLUNK_AZ_NUM="3") == "OUT: 3 3 3 3 3 3 2 2 3 3"
      and _aznum(ENDPOINTS_AZ_NUM="02") == "OUT: 2 2 1 1 1 1 1 1 1 1"
      and _aznum(ENDPOINTS_AZ_NUM="08").startswith("DIE: ENDPOINTS_AZ_NUM=8 は書けない。1〜3 で書く"))
_AZ_RANGE = {"ENDPOINTS_AZ_NUM": (1, 3), "MSK_AZ_NUM": (2, 3), "RUNTIME_AZ_NUM": (1, 3), "EMR_AZ_NUM": (1, 3), "LAMBDA_AZ_NUM": (1, 3),
             "NEPTUNE_AZ_NUM": (1, 3), "OPENSEARCH_AZ_NUM": (1, 2), "NAUTOBOT_DB_AZ_NUM": (1, 2), "TELEGRAF_AZ_NUM": (1, 3),
             "SPLUNK_AZ_NUM": (1, 3)}
check("AZ_NUM: 範囲の外（下限 - 1 と上限 + 1）は範囲を出して「まだ何も作っていない」で止まる。範囲と既定は terraform の変数と同じ",
      all(_aznum(**{k: str(v)}).startswith(f"DIE: {k}={v} は書けない。{lo}〜{hi} で書く（") and _aznum(**{k: str(v)}).endswith("まだ何も作っていない")
          for k, (lo, hi) in _AZ_RANGE.items() for v in (lo - 1, hi + 1))
      and all(_AZ_RANGE[k.upper()] == (min(a), max(a)) and _aznum().split()[1 + _AZ_KEYS.index(k.upper())] == str(d)
              for (_r, k), (d, a) in _AZ_VARS.items()))
check("AZ_NUM: MSK の 1 は理由つきで止まる。Runtime の 1 は受け付ける（2026-10-05 から既定）",
      "MSK はブローカーを 2 か 3 の AZ にしか置けない" in _aznum(MSK_AZ_NUM="1")
      and _aznum(RUNTIME_AZ_NUM="1") == "OUT: 1 2 1 1 1 1 1 1 1 1")
# Runtime を 2 AZ 以上にするときはエンドポイントもそろえる（2026-10-05 のユーザー決定）
_rt2 = _aznum(RUNTIME_AZ_NUM="2").splitlines()
_rt3 = _aznum(RUNTIME_AZ_NUM="3").splitlines()
check("Runtime: ENDPOINTS_AZ_NUM を書いていなければ Runtime の数まで上げ、上げたこととエンドポイントの費用が増えることを 1 行出す（注意は出ない）",
      len(_rt2) == 2 and _rt2[0].startswith("RUNTIME_AZ_NUM=2 に合わせて ENDPOINTS_AZ_NUM を 1 から 2 に上げる（") and "エンドポイントの費用" in _rt2[0]
      and _rt2[1] == "OUT: 2 2 2 1 1 1 1 1 1 1"
      and len(_rt3) == 2 and _rt3[0].startswith("RUNTIME_AZ_NUM=3 に合わせて ENDPOINTS_AZ_NUM を 1 から 3 に上げる（") and _rt3[1] == "OUT: 3 2 3 1 1 1 1 1 1 1"
      and "  ENDPOINTS_AZ_NUM=$RUNTIME_AZ_NUM\n" in _azblk)
check("Runtime: ENDPOINTS_AZ_NUM を Runtime より小さく書いてあれば、理由を出して「まだ何も作っていない」で止まる",
      all(_aznum(RUNTIME_AZ_NUM=r, ENDPOINTS_AZ_NUM=e).startswith(f"DIE: ENDPOINTS_AZ_NUM={e} が RUNTIME_AZ_NUM={r} より小さい。")
          and "見かけだけになる" in _aznum(RUNTIME_AZ_NUM=r, ENDPOINTS_AZ_NUM=e) and _aznum(RUNTIME_AZ_NUM=r, ENDPOINTS_AZ_NUM=e).endswith("まだ何も作っていない")
          for r, e in (("2", "1"), ("3", "1"), ("3", "2"))))
check("Runtime: ENDPOINTS_AZ_NUM が Runtime 以上ならそのまま（何も出さない）。上げたあとも、ほかのキーの注意は上げた数と比べる",
      _aznum(RUNTIME_AZ_NUM="2", ENDPOINTS_AZ_NUM="2") == "OUT: 2 2 2 1 1 1 1 1 1 1"
      and _aznum(RUNTIME_AZ_NUM="2", ENDPOINTS_AZ_NUM="3") == "OUT: 3 2 2 1 1 1 1 1 1 1"
      and (lambda o: len(o) == 3 and o[0].startswith("RUNTIME_AZ_NUM=2 に合わせて") and o[1].startswith("注意: NEPTUNE_AZ_NUM=3 に対して ENDPOINTS_AZ_NUM=2。")
           and o[2] == "OUT: 2 2 2 1 1 3 1 1 1 1")(_aznum(RUNTIME_AZ_NUM="2", NEPTUNE_AZ_NUM="3").splitlines()))
check("AZ_NUM: 数でない値（two / -1 / 1.5 / 空白入り）は止まる",
      all(_aznum(LAMBDA_AZ_NUM=v).startswith(f"DIE: LAMBDA_AZ_NUM は 1〜3 の数で書く（いまは LAMBDA_AZ_NUM={v}）") for v in ("two", "-1", "1.5", "1 2")))
check("SPLUNK_AZ_NUM: 2 か 3 は STORES に splunk が要り、SPLUNK_INDEX と一緒には書けない（どちらも何も作る前に止まる）。SKIP_ANALYTICS=1 なら見ない。"
      "Splunk のタスクは 1 か SPLUNK_AZ_NUM + 2",
      _aznum(SPLUNK_AZ_NUM="2", ENDPOINTS_AZ_NUM="2", SKIP_ANALYTICS="").endswith("OUT: 2 2 1 1 1 1 1 1 1 2")
      and _aznum(SPLUNK_AZ_NUM="2", SPLUNK_ON_ECS="", STORES="s3", SKIP_ANALYTICS="").startswith("DIE: SPLUNK_AZ_NUM=2 は Splunk のクラスターで、STORES に splunk が要る（いまは STORES=s3）")
      and _aznum(SPLUNK_AZ_NUM="3", SPLUNK_INDEX="netops", SKIP_ANALYTICS="").startswith("DIE: SPLUNK_AZ_NUM=3（Splunk のクラスター）では index は main だけで、SPLUNK_INDEX は書けない")
      and _aznum(SPLUNK_AZ_NUM="2", SPLUNK_ON_ECS="", STORES="s3", SPLUNK_INDEX="netops", SKIP_ANALYTICS="1").endswith(" 2")
      and all(subprocess.run(["bash", "-uc", 'die() { exit 1; }\n' + _azfn + _azblk + 'echo "$SPLUNK_TASKS"'], capture_output=True, text=True,
                             env={"PATH": os.environ["PATH"], **_AZ_ENV, "SKIP_ANALYTICS": "", "SPLUNK_AZ_NUM": a, "ENDPOINTS_AZ_NUM": "3"}).stdout.strip() == t
              for a, t in (("", "1"), ("1", "1"), ("2", "4"), ("3", "5"))))
# クラスターの全タスク待ちのあと（splunk_cluster_check）を切り出し、aws を偽物にして動かす。indexer の AZ の注意（止めない）と、
# search head の突き合わせの判定の行（CloudWatch Logs）を ok まで待つ・無い / 食い違いなら止まる
_sccblk = up[up.index("\nsplunk_cluster_check() {") + 1:up.index("\n}\n", up.index("\nsplunk_cluster_check() {")) + 3]   # ops/up-common.sh（OSS 版と共通）
_scc_aws = r"""aws() {
  echo "AWS $*" >>"$CALLS"
  case "$*" in
    "ecs list-tasks "*"--service-name t-nwc-poc-splunk-idx "*) printf 'arn:aws:ecs:ap-northeast-1:1:task/c/idx1\tarn:aws:ecs:ap-northeast-1:1:task/c/idx2\n' ;;
    "ecs list-tasks "*"--service-name t-nwc-poc-splunk "*) echo arn:aws:ecs:ap-northeast-1:1:task/c/sh1 ;;
    "ecs describe-tasks "*"--tasks arn:aws:ecs:ap-northeast-1:1:task/c/idx1 arn:aws:ecs:ap-northeast-1:1:task/c/idx2") printf '%s\n' "$AZS" ;;
    "logs filter-log-events "*"--log-group-name /ecs/t-nwc-poc-splunk --log-stream-names splunk/splunk/sh1 --filter-pattern \"nwc-peer-check\" "*) printf '%s\n' "$LINES" ;;
    *) return 254 ;;
  esac
}
sleep() { :; }
die() { echo "DIE: $*"; exit 1; }
"""
_sccdir = tempfile.mkdtemp()
def _scc(azs, lines, az_num="2"):  # 戻り値は (出力, aws logs を読んだ回数)
    calls = os.path.join(_sccdir, "calls")
    open(calls, "w").close()
    r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + _scc_aws + _sccblk + "splunk_cluster_check t-nwc-poc-splunk t-nwc-poc-splunk-cm t-nwc-poc-splunk-idx\necho END"],
                       capture_output=True, text=True, env={"PATH": os.environ["PATH"], "CALLS": calls, "AZS": azs, "LINES": "\t".join(lines),
                                                            "REGION": "ap-northeast-1", "AN_CLUSTER": "c", "PREFIX": "t-nwc-poc", "SPLUNK_AZ_NUM": az_num})
    return r.stdout + r.stderr, open(calls, encoding="utf-8").read().count("AWS logs filter-log-events")
_OK2 = ["nwc-peer-check state=ok reason=peers_up:1", "nwc-peer-check state=mismatch reason=lost:idx-b", "nwc-peer-check state=ok reason=peers_up:2"]
_scc_runs = {"ok": _scc("ap-northeast-1a\tap-northeast-1c", _OK2), "same_az": _scc("ap-northeast-1c\tap-northeast-1c", _OK2),
             "none": _scc("ap-northeast-1a\tap-northeast-1c", []), "few": _scc("ap-northeast-1a\tap-northeast-1c", _OK2[:1]),
             "mismatch": _scc("ap-northeast-1a\tap-northeast-1c", _OK2[:2]), "three": _scc("ap-northeast-1a\tap-northeast-1b\tap-northeast-1c", _OK2[:1] + ["nwc-peer-check state=ok reason=peers_up:3"], "3"),
             "degraded": _scc("ap-northeast-1a\tap-northeast-1c", _OK2[2:] + ["nwc-peer-check state=degraded reason=peers_up:1/2"])}
check("up.sh（クラスター）: 全タスクが HEALTHY になったら splunk_cluster_check に search head・manager・indexer のサービスを渡す。1 台のときは呼ばない",
      'SP_SERVICES="$SP_SERVICES $(tf pipeline/analytics output -raw splunk_cm_service_name) $(tf pipeline/analytics output -raw splunk_idx_service_name)"' in up
      and re.search(r'echo "Splunk は起動した"\n\s+if \[ "\$SPLUNK_AZ_NUM" -gt 1 \]; then splunk_cluster_check \$SP_SERVICES; fi\n', up) is not None
      and up.index("\nsplunk_cluster_check() {") < up.index('log "7-4b.'))
check("up.sh（クラスター）: indexer のタスクの AZ が重なっていれば注意を 1 行出して止めずに進む（Fargate の振り分けは保証でない）。重ならなければ出さない",
      "注意:" not in _scc_runs["ok"][0] and _scc_runs["ok"][0].rstrip().endswith("END")
      and _scc_runs["same_az"][0].count("注意: indexer のタスクが同じ AZ に 2 台いる（ap-northeast-1c ap-northeast-1c）") == 1 and _scc_runs["same_az"][0].rstrip().endswith("END")
      and "注意:" not in _scc_runs["three"][0])
check("up.sh（クラスター）: いまの search head のタスクのログストリーム（splunk/splunk/<タスク ID>）で接頭辞 nwc-peer-check の最新の行を読み、"
      "state=ok で Up の peer が indexer の数（SPLUNK_AZ_NUM）なら進む（その前の ok や mismatch の行は見ない）",
      _scc_runs["ok"][1] == 1 and "search head は indexer を全部（2 台）同じ GUID で検索できる（nwc-peer-check state=ok reason=peers_up:2）" in _scc_runs["ok"][0]
      and _scc_runs["three"][1] == 1 and _scc_runs["three"][0].rstrip().endswith("END"))
check("up.sh（クラスター）: 判定の行が 1 つも無ければ成功にせず、24 回（15 秒おき、6 分）待ってから「まだ 1 回も突き合わせていない」と言って止まる",
      _scc_runs["none"][1] == 24 and "END" not in _scc_runs["none"][0]
      and "DIE: search head のタスク（sh1）は、突き合わせ（splunk/peers_check.py）をまだ 1 回もしていない" in _scc_runs["none"][0])
check("up.sh（クラスター）: 最新の判定が mismatch、または ok でも Up の peer が indexer の数に足りなければ、6 分待ってから最新の判定を出して止まる",
      all(_scc_runs[k][1] == 24 and "END" not in _scc_runs[k][0] and "DIE: search head の突き合わせ（splunk/peers_check.py）が 6 分たっても ok（Up の indexer が 2 台）にならない" in _scc_runs[k][0]
          for k in ("few", "mismatch"))
      and "最新の判定は「nwc-peer-check state=mismatch reason=lost:idx-b」" in _scc_runs["mismatch"][0]
      and "最新の判定は「nwc-peer-check state=ok reason=peers_up:1」" in _scc_runs["few"][0])
check("up.sh（クラスター）: 最新の判定が degraded（Up の indexer が足りない。peers_check.py は 0 で終わる）なら、その前の ok では進まず、"
      "6 分待ってから最新の判定と degraded の意味を出して止まる",
      _scc_runs["degraded"][1] == 24 and "END" not in _scc_runs["degraded"][0]
      and "最新の判定は「nwc-peer-check state=degraded reason=peers_up:1/2」（degraded: manager が Up と言う indexer が足りない。reason=peers_up:<Up の数>/<あるはずの数>。" in _scc_runs["degraded"][0])
check("ENDPOINTS_MULTI_AZ が残っていると止まる（1 / true / yes は ENDPOINTS_AZ_NUM=2 に書き換え、0 / false / no は消す）",
      all(_aznum(ENDPOINTS_MULTI_AZ=v).startswith("DIE: ") and "を ENDPOINTS_AZ_NUM=2 と書き換える。まだ何も作っていない" in _aznum(ENDPOINTS_MULTI_AZ=v)
          for v in ("1", "true", "yes"))
      and all(_aznum(ENDPOINTS_MULTI_AZ=v).startswith("DIE: ") and "既定（ENDPOINTS_AZ_NUM=1）と同じなので、deploy.env と環境変数から消す" in _aznum(ENDPOINTS_MULTI_AZ=v)
              for v in ("0", "false", "no")))
_w = _aznum(NEPTUNE_AZ_NUM="2", TELEGRAF_AZ_NUM="3")
check("書いたキーが ENDPOINTS_AZ_NUM より大きいと注意を 1 行出して進む（そろえれば出ない。書いた MSK の 2 も数える）",
      _w.count("注意:") == 1 and _w.startswith("注意: NEPTUNE_AZ_NUM=2 TELEGRAF_AZ_NUM=3 に対して ENDPOINTS_AZ_NUM=1。") and _w.endswith("OUT: 1 2 1 1 1 2 1 1 3 1")
      and _aznum(NEPTUNE_AZ_NUM="2", ENDPOINTS_AZ_NUM="2") == "OUT: 2 2 1 1 1 2 1 1 1 1"
      and _aznum(NEPTUNE_AZ_NUM="3", ENDPOINTS_AZ_NUM="2").startswith("注意: NEPTUNE_AZ_NUM=3 に対して ENDPOINTS_AZ_NUM=2。")
      and _aznum(MSK_AZ_NUM="2").startswith("注意: MSK_AZ_NUM=2 に対して")
      and "注意" not in _aznum(LAMBDA_AZ_NUM="1"))
# グラフの状態の Lambda（graph-status）の待ちの上限が timeout を超える ENDPOINTS_AZ_NUM で注意を出す（2026-10-05 のユーザー決定）。
# 上限は graph/status_handler.py の FIREHOSE_CONFIG・RETRY_WAITS・NEPTUNE_CONFIG と sync.tf の timeout から計算し、up.sh の数と比べる
_sh_vals = {n.targets[0].id: n.value for n in ast.parse(open(os.path.join(ROOT, "graph", "status_handler.py"), encoding="utf-8").read()).body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
_fhc, _npc = ({k.arg: ast.literal_eval(k.value) for k in _sh_vals[c].keywords} for c in ("FIREHOSE_CONFIG", "NEPTUNE_CONFIG"))
_rwaits = ast.literal_eval(_sh_vals["RETRY_WAITS"])
_gs_timeout = int(re.search(r'resource "aws_lambda_function" "status" \{\n(?:  .*\n)*?  timeout\s*=\s*(\d+)',
                            open(os.path.join(ROOT, "terraform", "pipeline", "graph", "sync.tf"), encoding="utf-8").read()).group(1))
def _gs_wait(n, firehose=True):   # n = エンドポイントの IP の数（AZ ごとに 1 つ）。接続の待ちは IP ごとにかかる
    nep = _npc["retries"]["total_max_attempts"] * (n * _npc["connect_timeout"] + _npc["read_timeout"]) + 1   # 再試行の前の待ちは 1 秒まで
    fh = (1 + len(_rwaits)) * _fhc["retries"]["total_max_attempts"] * (n * _fhc["connect_timeout"] + _fhc["read_timeout"]) + sum(_rwaits)
    return round(nep + (fh if firehose else 0), 1)
_GS_ON = {"SKIP_GRAPH": "", "SKIP_ANALYTICS": ""}
_gs = {n: _aznum(ENDPOINTS_AZ_NUM=str(n), **_GS_ON) for n in (1, 2, 3)}
check(f"graph-status: 待ちの上限（status_handler.py と sync.tf から {_gs_wait(1)} / {_gs_wait(2)} / {_gs_wait(3)} 秒）が timeout の {_gs_timeout} 秒を超えるのは "
      "ENDPOINTS_AZ_NUM=3 だけで、up.sh はそのときだけ注意を 1 行出して進む（秒は計算と同じ）",
      _gs_timeout == 60 and [n for n in (1, 2, 3) if _gs_wait(n) > _gs_timeout] == [3]
      and all((_gs[n].count("注意:") == 1) == (_gs_wait(n) > _gs_timeout) for n in (1, 2, 3))
      and _gs[3].startswith("注意: ENDPOINTS_AZ_NUM=3 だと、グラフの状態の Lambda（graph-status）がエンドポイントに届かないときに待つ時間の上限が "
                            f"{_gs_wait(3)} 秒（")
      and f"Lambda の timeout の {_gs_timeout} 秒を超える。" in _gs[3] and "止めずに進む" in _gs[3]
      and _gs[3].endswith("OUT: 3 2 1 1 1 1 1 1 1 1") and len(_gs[3].splitlines()) == 2)
check("graph-status: 既定（ENDPOINTS_AZ_NUM=1）と 2 では注意を出さない",
      _aznum(**_GS_ON) == "OUT: 1 2 1 1 1 1 1 1 1 1" and _gs[1] == "OUT: 1 2 1 1 1 1 1 1 1 1" and _gs[2] == "OUT: 2 2 1 1 1 1 1 1 1 1")
check("graph-status: graph を作らない回（PIPELINE=0 / SKIP_GRAPH）と、analytics が無く Firehose に送らない回（Neptune だけで 3 AZ でも "
      f"{_gs_wait(3, firehose=False)} 秒）は 3 でも出さない",
      _gs_wait(3, firehose=False) <= _gs_timeout
      and _aznum(ENDPOINTS_AZ_NUM="3") == "OUT: 3 2 1 1 1 1 1 1 1 1"
      and _aznum(ENDPOINTS_AZ_NUM="3", SKIP_ANALYTICS="") == "OUT: 3 2 1 1 1 1 1 1 1 1"
      and _aznum(ENDPOINTS_AZ_NUM="3", SKIP_GRAPH="") == "OUT: 3 2 1 1 1 1 1 1 1 1")
check("graph-status: RUNTIME_AZ_NUM=3 で ENDPOINTS_AZ_NUM を 3 に上げたときも、上げた数で注意を出す",
      (lambda o: len(o) == 3 and o[0].startswith("RUNTIME_AZ_NUM=3 に合わせて ENDPOINTS_AZ_NUM を 1 から 3 に上げる（")
       and o[1].startswith("注意: ENDPOINTS_AZ_NUM=3 だと、グラフの状態の Lambda") and o[2] == "OUT: 3 2 3 1 1 1 1 1 1 1")(_aznum(RUNTIME_AZ_NUM="3", **_GS_ON).splitlines()))
check("graph-status: up.sh と deploy.env.example の ENDPOINTS_AZ_NUM の説明に、3 で注意が出ることを書く（上限の秒は計算と同じ）",
      all(f"待つ時間の上限（{_gs_wait(3)} 秒）が timeout の {_gs_timeout} 秒を超える" in t for t in (_red_up, _red_env)))
check("AZ_NUM の検査は deploy.env を読んだあと、aws を呼ぶ前・費用の目安より前（何も作る前）",
      up.index("\nload_deploy_env\n") < up.index(_azblk) < up.index("command -v aws >/dev/null") < up.index("COST_CENTS=2\n"))
check("各ルートに *_AZ_NUM を -var で渡す",
      'GRAPH_VARS=(-var "neptune_az_num=$NEPTUNE_AZ_NUM" -var "lambda_az_num=$LAMBDA_AZ_NUM")\n  if analytics_on; then GRAPH_VARS+=(-var alert_history=true); fi\n'
      '  ( tf_apply_only pipeline/graph "${GRAPH_VARS[@]}" )' in up
      and re.search(r'tf_apply pipeline/nautobot [^\n]*-var "nautobot_db_az_num=\$NAUTOBOT_DB_AZ_NUM"\n', up) is not None
      and re.search(r'tf_apply workflow [^\n]*-var "lambda_az_num=\$LAMBDA_AZ_NUM"\n', up) is not None
      and re.search(r'tf_apply pipeline/stream (?:[^\n]*\\\n)+\s*-var "msk_az_num=\$MSK_AZ_NUM" -var "telegraf_az_num=\$TELEGRAF_AZ_NUM"\n', up) is not None
      and 'AGENT_VARS=(-var "agent_image_tag=$IMAGE_TAG" -var "runtime_az_num=$RUNTIME_AZ_NUM" -var "lambda_az_num=$LAMBDA_AZ_NUM" -var "opensearch_az_num=$OPENSEARCH_AZ_NUM")' in up
      and 'ANALYTICS_VARS+=(-var "opensearch_az_num=$OPENSEARCH_AZ_NUM")' in up and '-var "emr_az_num=$EMR_AZ_NUM")' in up)
# 費用の目安の全体を切り出して AZ_NUM ごとに動かす（endpoint_count は 2 本に固定。機能は全部切ってから 1 つずつ入れる）
_costall = up[up.index("COST_CENTS=2\n"):up.index("COST_NOTE=$(printf")]
_COST_OFF = {k: "" for k in ("AGENT", "CREATE_KB", "SINK_S3", "SINK_OPENSEARCH", "SINK_PROMETHEUS", "SINK_SPLUNK", "GRAFANA",
                             "SPLUNK_ON_ECS", "NAUTOBOT", "WORKFLOW")}
_COST_OFF.update(SKIP_LAB="1", SKIP_GRAPH="1", SKIP_STREAM="1", SKIP_ANALYTICS="1", **dict(zip(_AZ_KEYS, "1211111111")))
def _costaz(**env):
    r = subprocess.run(["bash", "-uc", "endpoint_count() { echo 2; }\n" + _costall + 'echo "OUT: $COST_CENTS"'], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], **_COST_OFF, **env})
    return int(r.stdout.split("OUT: ")[1]) if "OUT: " in r.stdout else r.stderr
check("費用: エンドポイントは 1.4 × 本数 × ENDPOINTS_AZ_NUM（2 本で 1 AZ 3、3 AZ 8）",
      _costaz() == 2 + 3 and _costaz(ENDPOINTS_AZ_NUM="3") == 2 + 8)
check("費用: MSK は 2 AZ で 57、3 AZ で +27。Telegraf は受ける側のタスクが AZ ごとに増える（1 AZ 5、3 AZ 7）。Kafbat UI は stream を作る回はいつも 2",
      _costaz(SKIP_STREAM="") == 5 + 57 + 5 + 2 and _costaz(SKIP_STREAM="", MSK_AZ_NUM="3") == 5 + 84 + 5 + 2
      and _costaz(SKIP_STREAM="", TELEGRAF_AZ_NUM="3") == 5 + 57 + 7 + 2
      and "\n  COST_CENTS=$((COST_CENTS + 2))   # Kafbat UI（Fargate のタスク 1）\nfi\n" in _costall)
check("費用: Neptune は 58 × NEPTUNE_AZ_NUM、Nautobot は Multi-AZ で 13 → 16",
      _costaz(SKIP_GRAPH="") == 5 + 58 and _costaz(SKIP_GRAPH="", NEPTUNE_AZ_NUM="3") == 5 + 174
      and _costaz(NAUTOBOT="1") == 5 + 13 and _costaz(NAUTOBOT="1", NAUTOBOT_DB_AZ_NUM="2") == 5 + 16)
check("費用: OpenSearch の OCU は 33 × OPENSEARCH_AZ_NUM（KB も logs も）、OpenSearch Serverless のエンドポイントは 1.4 × ENDPOINTS_AZ_NUM（1 AZ 1、2 AZ 3）",
      _costaz(AGENT="1", CREATE_KB="1") == 5 + 33 + 1 and _costaz(AGENT="1", CREATE_KB="1", OPENSEARCH_AZ_NUM="2") == 5 + 66 + 1
      and _costaz(AGENT="1", CREATE_KB="1", ENDPOINTS_AZ_NUM="2") == 2 + 6 + 33 + 3
      and _costaz(SKIP_ANALYTICS="", SINK_OPENSEARCH="1", OPENSEARCH_AZ_NUM="2") == 5 + 21 + 66 + 1)
check("費用: EMR / Lambda / Runtime の AZ_NUM では変わらない。AZ をまたぐ転送料は入れず、AZ_NUM を書いたときに 1 行出す",
      _costaz(EMR_AZ_NUM="3", LAMBDA_AZ_NUM="3", RUNTIME_AZ_NUM="3") == _costaz()
      and 'if [ -n "$AZ_NUM_SET" ]; then echo "AZ をまたぐ転送料（' in up)
# iceberg を外しても、証跡があるのでテーブルバケット・namespace・カタログはいつも作る。生データの raw_telemetry だけ外す
check('resource "aws_s3tables_table" "raw_telemetry" は sink_iceberg の count',
      re.search(r'resource "aws_s3tables_table" "raw_telemetry" \{\n  count = local\.sink_iceberg \? 1 : 0\n', tf) is not None)
for _res in ('resource "aws_s3tables_table_bucket" "tables"', 'resource "aws_s3tables_namespace" "netops"'):
    check(f"{_res} はいつも作る（count 無し）", re.search(re.escape(_res) + r' \{\n  count', tf) is None and _res in tf)
check("count を外したバケット・namespace は moved で state の [0] を引き継ぐ（作り直さない）",
      all(re.search(r'moved \{\n\s*from = ' + re.escape(r) + r'\[0\]\n\s*to\s*= ' + re.escape(r) + r'\n', tf) for r in
          ("aws_s3tables_table_bucket.tables", "aws_s3tables_namespace.netops")))
check("実行ロールの S3TablesCatalog は iceberg を選んだときだけ（Spark は証跡に書かなくなった。2026-10-02）、Spark のカタログの設定はいつも入る",
      re.search(r'Sid\s*=\s*"S3TablesCatalog"', tf) is not None and "}] : s if local.sink_iceberg]" in tf.split('Sid    = "S3TablesCatalog"')[1].split("OpenSearchCollection")[0]
      and "warehouse=${local.table_bucket_arn}" in tf and "c if local.sink_iceberg" not in tf)

# outputs の JSON が本当に JSON になる形か（jsonencode の中身の構造を軽く見る）
check("job_driver_json は sparkSubmit の 3 キー", all(k in tf for k in ("entryPoint ", "entryPointArguments", "sparkSubmitParameters")))
# ---- Spark のジョブを格納先で 3 つに分けた（2026-10-04）。outputs.tf の for-if を Python で評価してジョブごとの引数を組み、スクリプトの parse_args に通す
_jobs_def = {j: [x.strip(' "') for x in v.split(",")] for j, v in
             re.findall(r'(\w+) = \[([^\]]*)\]', re.search(r'spark_jobs = \{ for job, sinks in \{(.*?)\} :', tf).group(1))}
check("spark_jobs: iceberg / splunk / http（opensearch と prometheus）", _jobs_def == {"iceberg": ["iceberg"], "splunk": ["splunk"], "http": ["opensearch", "prometheus"]})
_VALS = {"local.bootstrap": "b:9098", "local.checkpoint_uri": "s3://bucket/analytics/checkpoint/u/", "var.region": "ap-northeast-1",
         "local.metric_topics": "metrics,gnmi", "local.log_topics": "traps,logs", "local.iceberg_table": "s3tables.netops.raw_telemetry",
         "local.opensearch_endpoint": "https://c.aoss.amazonaws.com", "local.opensearch_index": "snmp-logs",
         "local.prometheus_remote_write_url": "https://aps/api/v1/remote_write", "local.splunk_hec_url": "https://splunk.p.internal:8088",
         "local.splunk_token_parameter": "/p/splunk/hec-token", "var.splunk_index": ""}
_args_body = "\n".join(l for l in args_block.group(1).splitlines() if not l.strip().startswith("#"))
def _job_args(job, var_sinks, http_send="driver", max_offsets=10000, by_sink=None, device_map=""):
    """var.sinks / var.http_send / var.max_offsets_per_trigger / var.max_offsets_per_trigger_by_sink / var.device_map のときに job の entryPointArguments になるもの
    （そのジョブの格納先が無ければ None = job_driver は空文字）。local.max_offsets_by_job は locals.tf と同じ組み方を Python でする"""
    sinks = [s for s in _jobs_def[job] if s in var_sinks]
    if not sinks:
        return None
    by_sink = by_sink or {}
    by_job = ",".join(f"{s}={by_sink[s]}" for s in sinks if s in by_sink)
    def val(tok):
        if tok.startswith('"'):
            return tok.strip('"')
        if tok == 'join(",", sinks)':
            return ",".join(sinks)
        if tok == "local.max_offsets_by_job[job]":
            return by_job
        if tok == "tostring(var.max_offsets_per_trigger)":
            return str(max_offsets)
        if tok == "var.device_map":
            return device_map
        return http_send if tok == "var.http_send" else _VALS[tok]
    toks = r'"[^"]*"|join\(",", sinks\)|local\.max_offsets_by_job\[job\]|tostring\(var\.max_offsets_per_trigger\)|[a-z_]+\.[a-z_]+'
    out = [val(t) for t in re.findall(toks, _args_body.split("[for a in")[0])]
    for items, cond in re.findall(r'^\s*\[for a in \[(.*)\] : a if (.*)\],?\s*$', _args_body, re.M):
        py = re.sub(r'contains\(sinks, "(\w+)"\)', r'("\1" in sinks)', cond).replace("&&", "and").replace("||", "or")
        py = py.replace("var.http_send", "http_send").replace("local.splunk_skip_tls_verify", "True").replace("local.max_offsets_by_job[job]", "by_job").replace("var.device_map", "device_map")
        if eval(py, {}, {"sinks": sinks, "job": job, "http_send": http_send, "by_job": by_job, "device_map": device_map}):
            out += [val(t) for t in re.findall(toks, items)]
    return out
check("_job_args は outputs.tf の for-if を全部読む（1 行に 1 つ。読めない書き方が増えたら数が合わなくなる）",
      len(re.findall(r'^\s*\[for a in \[(.*)\] : a if (.*)\],?\s*$', _args_body, re.M)) == _args_body.count("[for a in"))
_COMMON = {"--bootstrap", "--checkpoint", "--sinks", "--region", "--metric-topics", "--log-topics", "--max-offsets-per-trigger"}
_ALL4 = ["iceberg", "opensearch", "prometheus", "splunk"]
_flags = lambda argv: set(a for a in argv if a.startswith("--"))
_val = lambda argv, k: argv[argv.index(k) + 1]
_ji, _js, _jh = (_job_args(j, _ALL4) for j in ("iceberg", "splunk", "http"))
check("iceberg のジョブ: --sinks iceberg と --iceberg-table だけ（ほかの格納先の引数は無い）",
      _val(_ji, "--sinks") == "iceberg" and _flags(_ji) == _COMMON | {"--iceberg-table"})
check("splunk のジョブ: --sinks splunk と Splunk の引数だけ（token は SSM のパラメータ名）",
      _val(_js, "--sinks") == "splunk" and _flags(_js) == _COMMON | {"--splunk-hec-url", "--splunk-token-parameter", "--splunk-index", "--splunk-skip-verify"}
      and _val(_js, "--splunk-token-parameter") == "/p/splunk/hec-token")
check("http のジョブ: --sinks opensearch,prometheus と両方の引数だけ",
      _val(_jh, "--sinks") == "opensearch,prometheus" and _flags(_jh) == _COMMON | {"--opensearch-endpoint", "--opensearch-index", "--prometheus-url"})
check("3 つのジョブは同じ --checkpoint の親を渡す（格納先ごとの下のディレクトリはスクリプトが切るので、分けても checkpoint のパスは変わらない）",
      {_val(j, "--checkpoint") for j in (_ji, _js, _jh)} == {"s3://bucket/analytics/checkpoint/u/"})
_pa = [mod.parse_args(j) for j in (_ji, _js, _jh)]
check("どのジョブの引数もスクリプトの parse_args を通る（格納先に要る引数がそろう）",
      [a.sinks for a in _pa] == [["iceberg"], ["splunk"], ["opensearch", "prometheus"]] and all(a.http_send == "driver" for a in _pa))
_ex = {j: _job_args(j, _ALL4, "executor") for j in ("iceberg", "splunk", "http")}
check("http_send=executor: splunk と http のジョブにだけ --http-send executor を渡す（iceberg は HTTP で送らない）",
      "--http-send" not in _ex["iceberg"] and _val(_ex["splunk"], "--http-send") == "executor" and _val(_ex["http"], "--http-send") == "executor"
      and mod.parse_args(_ex["http"]).http_send == "executor" and mod.parse_args(_ex["iceberg"]).http_send == "driver")
check("格納先を減らす: iceberg と prometheus だけなら splunk のジョブは無く、http のジョブは prometheus だけ（OpenSearch の引数も無い）",
      _job_args("splunk", ["iceberg", "prometheus"]) is None
      and _val(_job_args("http", ["iceberg", "prometheus"]), "--sinks") == "prometheus"
      and _flags(_job_args("http", ["iceberg", "prometheus"])) == _COMMON | {"--prometheus-url"})
check("格納先を減らす: splunk だけなら iceberg と http のジョブは無い。opensearch だけなら http のジョブは opensearch だけ",
      _job_args("iceberg", ["splunk"]) is None and _job_args("http", ["splunk"]) is None and _job_args("splunk", ["splunk"]) is not None
      and mod.parse_args(_job_args("http", ["opensearch"])).sinks == ["opensearch"])
check("既定: どのジョブにも --max-offsets-per-trigger 10000 を渡し、--max-offsets-per-trigger-by-sink は渡さない",
      all(_val(j, "--max-offsets-per-trigger") == "10000" and "--max-offsets-per-trigger-by-sink" not in j for j in (_ji, _js, _jh))
      and all(mod.max_offsets(a, s) == 10000 for a in _pa for s in a.sinks))
_mo = {j: _job_args(j, _ALL4, by_sink={"splunk": 2000}) for j in ("iceberg", "splunk", "http")}
check("splunk=2000: splunk のジョブにだけ --max-offsets-per-trigger-by-sink splunk=2000 を渡す（ほかのジョブの引数は既定と同じで、up.sh は splunk だけ起こし直す）",
      _val(_mo["splunk"], "--max-offsets-per-trigger-by-sink") == "splunk=2000"
      and _mo["iceberg"] == _ji and _mo["http"] == _jh and mod.max_offsets(mod.parse_args(_mo["splunk"]), "splunk") == 2000)
_mo = {j: _job_args(j, _ALL4, by_sink={"prometheus": 5000, "opensearch": 0, "iceberg": 50000}) for j in ("iceberg", "splunk", "http")}
_mh = mod.parse_args(_mo["http"])
check("prometheus=5000, opensearch=0, iceberg=50000: http のジョブは opensearch=0,prometheus=5000、iceberg のジョブは iceberg=50000、splunk のジョブは渡さない",
      _val(_mo["http"], "--max-offsets-per-trigger-by-sink") == "opensearch=0,prometheus=5000"
      and _val(_mo["iceberg"], "--max-offsets-per-trigger-by-sink") == "iceberg=50000" and _mo["splunk"] == _js
      and [mod.max_offsets(_mh, s) for s in _mh.sinks] == [0, 5000])
check("格納先ごとの値は、その格納先を選んでいなければ渡さない（opensearch を外して opensearch=5 を書いても、http のジョブは prometheus だけで引数は変わらない）",
      _job_args("http", ["prometheus"], by_sink={"opensearch": 5}) == _job_args("http", ["prometheus"]))
check("共通 0: どのジョブにも --max-offsets-per-trigger 0 を渡し、スクリプトは上限なしになる",
      all(_val(_job_args(j, _ALL4, max_offsets=0), "--max-offsets-per-trigger") == "0" and mod.parse_args(_job_args(j, _ALL4, max_offsets=0)).max_offsets_per_trigger == 0
          for j in ("iceberg", "splunk", "http")))
_dmap = "203.0.113.31=dc1-leaf-01,203.0.113.21=dc1-spine-01"
_dj = {j: _job_args(j, _ALL4, device_map=_dmap) for j in ("iceberg", "splunk", "http")}
check("device map があれば http のジョブ（opensearch / prometheus）にだけ --device-map を渡し、スクリプトはそれを読む。iceberg と splunk のジョブ、device map が空のときは渡さない（cycle 002）",
      _val(_dj["http"], "--device-map") == _dmap and mod.parse_args(_dj["http"]).device_map == _dmap
      and _dj["iceberg"] == _ji and _dj["splunk"] == _js and "--device-map" not in _jh
      and _val(_job_args("http", ["prometheus"], device_map=_dmap), "--device-map") == _dmap)
check("variable max_offsets_per_trigger は number で既定 10000、0 以上の整数だけ通す",
      re.search(r'variable "max_offsets_per_trigger" \{\s*description[^\n]*\n\s*type\s*=\s*number\s*\n\s*default\s*=\s*10000\s*\n\s*validation \{\s*\n'
                r'\s*condition\s*=\s*var\.max_offsets_per_trigger >= 0 && floor\(var\.max_offsets_per_trigger\) == var\.max_offsets_per_trigger\n', tf) is not None)
check("variable max_offsets_per_trigger_by_sink は map(number) で既定 {}、キーは 4 つの格納先、値は 0 以上の整数だけ通す",
      re.search(r'variable "max_offsets_per_trigger_by_sink" \{\s*description[^\n]*\n\s*type\s*=\s*map\(number\)\s*\n\s*default\s*=\s*\{\}\s*\n\s*validation \{\s*\n'
                r'\s*condition\s*=\s*alltrue\(\[for k, v in var\.max_offsets_per_trigger_by_sink : contains\(\["iceberg", "opensearch", "prometheus", "splunk"\], k\) && v >= 0 && floor\(v\) == v\]\)', tf) is not None)
check("locals の max_offsets_by_job は、ジョブの格納先のうち値のあるものだけを <格納先>=<件数> にしてカンマでつなぐ（_job_args と同じ組み方）",
      re.search(r'max_offsets_by_job = \{ for job, sinks in local\.spark_jobs :\s*\n\s*job => join\(",", \[for s in sinks : "\$\{s\}=\$\{var\.max_offsets_per_trigger_by_sink\[s\]\}" if contains\(keys\(var\.max_offsets_per_trigger_by_sink\), s\)\]\) \}', tf) is not None)
check("job_driver は --max-offsets-per-trigger をいつも渡し、--max-offsets-per-trigger-by-sink はそのジョブの分があるときだけ渡す",
      '["--max-offsets-per-trigger", tostring(var.max_offsets_per_trigger)],' in args_block.group(1)
      and '[for a in ["--max-offsets-per-trigger-by-sink", local.max_offsets_by_job[job]] : a if local.max_offsets_by_job[job] != ""],' in args_block.group(1))

# up.sh の上限の判定を切り出して、bash で実際に動かす
_moblk = up[up.index('MAX_OFFSETS_PER_TRIGGER="${MAX_OFFSETS_PER_TRIGGER:-10000}"'):]
_moblk = _moblk[:_moblk.index("\ndone\n") + len("\ndone\n")]
def _up_offsets(**env):
    r = subprocess.run(["bash", "-c", 'die() { echo "DIE: $*"; exit 1; }\n' + _moblk + 'echo "OUT: $MAX_OFFSETS_PER_TRIGGER {$MAX_OFFSETS_BY_SINK}"'],
                       capture_output=True, text=True, env={"PATH": os.environ["PATH"], **env})
    return r.returncode, r.stdout.strip()
check("up.sh: 何も書かなければ共通 10000、格納先ごとは {}（空の値は書かなかったのと同じ）",
      _up_offsets() == (0, "OUT: 10000 {}") and _up_offsets(MAX_OFFSETS_PER_TRIGGER="", MAX_OFFSETS_PER_TRIGGER_SPLUNK="") == (0, "OUT: 10000 {}"))
check("up.sh: 共通 0 はそのまま 0（上限なし）",  _up_offsets(MAX_OFFSETS_PER_TRIGGER="0") == (0, "OUT: 0 {}"))
check("up.sh: MAX_OFFSETS_PER_TRIGGER_SPLUNK=2000 は {splunk=2000}",
      _up_offsets(MAX_OFFSETS_PER_TRIGGER_SPLUNK="2000") == (0, "OUT: 10000 {splunk=2000}"))
check("up.sh: 4 つの格納先の名前は小文字にして HCL の map にする（0 もそのまま渡す）",
      _up_offsets(MAX_OFFSETS_PER_TRIGGER="20000", MAX_OFFSETS_PER_TRIGGER_ICEBERG="50000", MAX_OFFSETS_PER_TRIGGER_SPLUNK="2000",
                  MAX_OFFSETS_PER_TRIGGER_OPENSEARCH="0", MAX_OFFSETS_PER_TRIGGER_PROMETHEUS="5000")
      == (0, "OUT: 20000 {iceberg=50000,splunk=2000,opensearch=0,prometheus=5000}"))
check("up.sh: 共通も格納先ごとも、負・小数・文字・先頭の 0・10 桁・空白入りなら何かを作る前に止まる（どの変数かを言う）",
      all((lambda r: r[0] == 1 and f"DIE: {k} は 0 以上の整数" in r[1])(_up_offsets(**{k: v}))
          for k in ("MAX_OFFSETS_PER_TRIGGER", "MAX_OFFSETS_PER_TRIGGER_ICEBERG", "MAX_OFFSETS_PER_TRIGGER_SPLUNK",
                    "MAX_OFFSETS_PER_TRIGGER_OPENSEARCH", "MAX_OFFSETS_PER_TRIGGER_PROMETHEUS")
          for v in ("-1", "1.5", "abc", "010", "1234567890", " 5", "1e4")))
check("up.sh: 上限の判定は何かを作る前にあり、analytics に max_offsets_per_trigger と max_offsets_per_trigger_by_sink を渡す",
      up.index('MAX_OFFSETS_PER_TRIGGER="${MAX_OFFSETS_PER_TRIGGER:-10000}"') < up.index("\ntf_apply base/ecr")
      and 'ANALYTICS_VARS=(-var "sinks=[$SINKS_TF]" -var "max_offsets_per_trigger=$MAX_OFFSETS_PER_TRIGGER" -var "max_offsets_per_trigger_by_sink={$MAX_OFFSETS_BY_SINK}")' in up)
_mo_keys = ["MAX_OFFSETS_PER_TRIGGER"] + [f"MAX_OFFSETS_PER_TRIGGER_{s}" for s in ("ICEBERG", "SPLUNK", "OPENSEARCH", "PROMETHEUS")]
_denv = open(os.path.join(ROOT, "ops", "deploy-env.sh"), encoding="utf-8").read()
check("deploy-env.sh は上限の 5 つのキーを読めるキーに持つ",
      all(re.search(r'(?<![A-Z_])' + k + r'(?![A-Z_])', _denv) for k in _mo_keys))
check("deploy.env.example は #MAX_OFFSETS_PER_TRIGGER=10000 と、格納先ごとの 4 つを空で書く",
      re.search(r"^#MAX_OFFSETS_PER_TRIGGER=10000$", env_example, re.M) is not None
      and all(re.search(rf"^#{k}=$", env_example, re.M) for k in _mo_keys[1:]))
_docs = {n: open(os.path.join(ROOT, *n.split("/")), encoding="utf-8").read() for n in ("README.md", "docs/deploy.md")}
check("README と docs/deploy.md のキーの表に MAX_OFFSETS_PER_TRIGGER と格納先ごとの値がある",
      all(re.search(r"^\| `MAX_OFFSETS_PER_TRIGGER` \|", d, re.M) and "MAX_OFFSETS_PER_TRIGGER_ICEBERG" in d for d in _docs.values()))

# 費用の analytics の部分を up.sh から切り出して動かす（Spark のジョブ 1 つ 21。3 つで 63）
_costblk = up[up.index('if [ -z "$SKIP_ANALYTICS" ]; then\n  # Spark のジョブ'):up.index('if [ -n "$NAUTOBOT" ]; then COST_CENTS')]
def _cost(**env):
    base_env = {k: "" for k in ("SKIP_ANALYTICS", "SINK_S3", "SINK_OPENSEARCH", "SINK_PROMETHEUS", "SINK_SPLUNK", "GRAFANA", "SPLUNK_ON_ECS")}
    base_env.update(OPENSEARCH_AZ_NUM="1", SPLUNK_TASKS="1")
    r = subprocess.run(["bash", "-uc", "COST_CENTS=0\n" + _costblk + 'echo "OUT: $COST_CENTS"'], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], **base_env, **env})
    return int(r.stdout.split("OUT: ")[1]) if "OUT: " in r.stdout else r.stderr
check("費用: Spark のジョブは S3 / Splunk / OpenSearch か Prometheus で 1 つずつ 21（3 つで 63）",
      _cost(SINK_S3="1") == 21 and _cost(SINK_PROMETHEUS="1") == 21 and _cost(SINK_S3="1", SINK_PROMETHEUS="1") == 42
      and _cost(SINK_S3="1", SINK_SPLUNK="1", SINK_PROMETHEUS="1") == 63)
check("費用: OpenSearch と Prometheus は 1 つのジョブ（OpenSearch の OCU 33 は別）。Splunk は ECS の 12 も足す。SKIP_ANALYTICS なら 0",
      _cost(SINK_OPENSEARCH="1", SINK_PROMETHEUS="1") == 21 + 33 and _cost(SINK_SPLUNK="1", SPLUNK_ON_ECS="1") == 21 + 12
      and _cost(SINK_S3="1", SINK_OPENSEARCH="1", SINK_PROMETHEUS="1", SINK_SPLUNK="1", SPLUNK_ON_ECS="1", GRAFANA="1") == 63 + 33 + 12 + 2
      and _cost(SKIP_ANALYTICS="1", SINK_S3="1", SINK_SPLUNK="1", SINK_PROMETHEUS="1") == 0)
check("費用: Splunk のクラスター（SPLUNK_AZ_NUM が 2 / 3 でタスク 4 / 5）は ECS の Splunk が 49 / 61（タスク 1 つ 12.3）",
      _cost(SINK_SPLUNK="1", SPLUNK_ON_ECS="1", SPLUNK_TASKS="4") == 21 + 49 and _cost(SINK_SPLUNK="1", SPLUNK_ON_ECS="1", SPLUNK_TASKS="5") == 21 + 61)
_COST_TAIL = "COST_CENTS=0\nOPENSEARCH_AZ_NUM=1\nSPLUNK_TASKS=1\n" + _costblk + 'echo "OUT: $COST_CENTS"'
check("費用の Grafana の 2 は導いた値で数える（STORES=grafana なら 21 + 33 + 2、STORES=s3 なら Grafana は無く 21、3 つとも入れると 63 + 33 + 12 + 2）",
      _stores(_COST_TAIL, STORES="grafana")[:2] == (0, "OUT: 56") and _stores(_COST_TAIL, STORES="s3")[:2] == (0, "OUT: 21")
      and _stores(_COST_TAIL, STORES="s3,grafana,splunk")[:2] == (0, "OUT: 110"))

# 7-5 を up.sh から切り出し、偽の aws（状態を JSON に持ち、呼ばれた順を記録する）で動かす
import copy, hashlib, shutil, tempfile
_e75 = up.index("\n  fi\n", up.index('echo "起動に 2〜5 分。')) + len("\n  fi\n")
_b75 = up[up.index("  job_name() {"):_e75]
_FAKE_AWS = "#!" + sys.executable + r'''
import json, os, sys
st_path = os.environ["FAKE_STATE"]
st = json.load(open(st_path))
a = sys.argv[1:]
opt = lambda k: a[a.index(k) + 1]
def opts(k):
    i, out = a.index(k) + 1, []
    while i < len(a) and not a[i].startswith("--"):
        out.append(a[i]); i += 1
    return out
cmd = a[1]
if cmd == "list-job-runs":  # 呼ばれるたびに時間が進む: 止めている途中のものは left 回で止まる
    for r in st["runs"]:
        if r["state"] == "CANCELLING":
            r["left"] -= 1
            if r["left"] <= 0:
                r["state"] = "CANCELLED"
    hits = [r for r in st["runs"] if r["state"] in opts("--states")]
    st["log"].append("list " + " ".join(f'{r["name"]}:{r["id"]}:{r["state"]}' for r in hits))
    assert opt("--query") == "jobRuns[].[name,id]", opt("--query")
    print("\n".join(f'{r["name"]}\t{r["id"]}' for r in hits))
elif cmd == "get-job-run":
    print(next(r for r in st["runs"] if r["id"] == opt("--job-run-id")).get("spec") or "None")
elif cmd == "cancel-job-run":
    r = next(r for r in st["runs"] if r["id"] == opt("--job-run-id"))
    r["state"], r["left"] = "CANCELLING", st["cancel_delay"]
    st["log"].append(f'cancel {r["name"]}:{r["id"]}')
elif cmd == "start-job-run":  # 同じ checkpoint を使うジョブ（同じ名前、古い名前の snmp-sinks-<キー>、全部を書いていた snmp-sinks）がまだ動いて（止めて）いれば CONFLICT
    name, tags = opt("--name"), dict(t.split("=", 1) for t in opt("--tags").split(","))
    old = {"sinks-s3iceberg": "snmp-sinks-iceberg", "sinks-splunk": "snmp-sinks-splunk", "sinks-grafana": "snmp-sinks-http"}[name]
    busy = [r for r in st["runs"] if r["state"] != "CANCELLED" and r["name"] in (name, old, "snmp-sinks")]
    rid = f'j{len(st["runs"]) + 1}'
    st["runs"].append({"id": rid, "name": name, "state": "SUBMITTED", "spec": tags["SpecHash"], "driver": opt("--job-driver")})
    st["log"].append(f"start {name}" + (" CONFLICT" if busy else "") + f' {opt("--mode")}')
    print(rid)
else:
    sys.exit("unknown " + cmd)
json.dump(st, open(st_path, "w"))
'''
_OVR = '{"o":1}'
def _spec(driver):
    h = hashlib.sha256(open(SRC, "rb").read()); h.update(driver.encode()); h.update(_OVR.encode())
    return h.hexdigest()[:16]
def _run75(runs, drivers, cancel_delay=2):
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "aws"), "w") as f:
            f.write(_FAKE_AWS)
        os.chmod(os.path.join(d, "aws"), 0o755)
        state = os.path.join(d, "state.json")
        with open(state, "w") as f:
            json.dump({"runs": copy.deepcopy(runs), "log": [], "cancel_delay": cancel_delay}, f)
        pre = (f'set -euo pipefail\nREGION=r; APP_ID=app; PREFIX=p; OWNER=o; SPARK_SCRIPT="{SRC}"; PY=("{sys.executable}")\n'
               'die() { echo "DIE: $*"; exit 1; }\nlog() { echo "LOG: $*"; }\nsleep() { :; }\n'
               "tf() { case \"$4\" in configuration_overrides_json) printf %s '" + _OVR + "' ;; "
               'job_driver_json_*) v="DRV_${4#job_driver_json_}"; printf %s "${!v:-}" ;; runtime_role_arn) echo arn:role ;; '
               'list_job_runs_command) echo LIST ;; *) echo "tf? $*" >&2; exit 9 ;; esac; }\n')
        r = subprocess.run(["bash", "-c", pre + _b75], capture_output=True, text=True,
                           env={"PATH": d + os.pathsep + os.environ["PATH"], "FAKE_STATE": state, **{f"DRV_{k}": v for k, v in drivers.items()}})
        with open(state) as f:
            st = json.load(f)
        return r.returncode, r.stdout + r.stderr, st
    finally:
        shutil.rmtree(d)
_acts = lambda st: [l for l in st["log"] if l.startswith(("cancel", "start"))]
_D = {"iceberg": '{"j":"iceberg"}', "splunk": "", "http": '{"j":"http"}'}
_rc, _out, _st = _run75([{"id": "old", "name": "snmp-sinks", "state": "RUNNING", "spec": "x"}], _D, cancel_delay=3)
_i = _st["log"].index("cancel snmp-sinks:old")
check("7-5（移行）: 1 つにまとめていた頃の snmp-sinks を cancel し、止まる（CANCELLED）まで待ってから sinks-s3iceberg と sinks-grafana を起こす（splunk は格納先が無いので起こさない）",
      _rc == 0 and _acts(_st) == ["cancel snmp-sinks:old", "start sinks-s3iceberg STREAMING", "start sinks-grafana STREAMING"]
      and any("snmp-sinks:old:CANCELLING" in l for l in _st["log"][_i:]) and "古い名前のジョブ snmp-sinks（old）を止める" in _out)
check("7-5（移行）: 新しいジョブには自分の job_driver と SpecHash（スクリプト + その job_driver + overrides）を付ける",
      {r["name"]: (r["spec"], r["driver"]) for r in _st["runs"] if r["name"] != "snmp-sinks"}
      == {"sinks-s3iceberg": (_spec(_D["iceberg"]), _D["iceberg"]), "sinks-grafana": (_spec(_D["http"]), _D["http"])})
_old3 = [{"id": "oi", "name": "snmp-sinks-iceberg", "state": "RUNNING", "spec": _spec(_D["iceberg"])},
         {"id": "oh", "name": "snmp-sinks-http", "state": "QUEUED", "spec": _spec(_D["http"])},
         {"id": "os", "name": "snmp-sinks-splunk", "state": "RUNNING", "spec": "s"}]
_rc, _out, _st = _run75(_old3, _D, cancel_delay=3)
_i = max(_st["log"].index(f"cancel {n}") for n in ("snmp-sinks-iceberg:oi", "snmp-sinks-http:oh", "snmp-sinks-splunk:os"))
check("7-5（改名）: snmp-sinks-<キー> は SpecHash が同じでも古い名前として 3 つとも cancel し、止まってから sinks-s3iceberg と sinks-grafana を起こす（CONFLICT しない）",
      _rc == 0 and sorted(_acts(_st)[:3]) == ["cancel snmp-sinks-http:oh", "cancel snmp-sinks-iceberg:oi", "cancel snmp-sinks-splunk:os"]
      and _acts(_st)[3:] == ["start sinks-s3iceberg STREAMING", "start sinks-grafana STREAMING"]
      and any("snmp-sinks-iceberg:oi:CANCELLING" in l for l in _st["log"][_i:])
      and all(f"古い名前のジョブ {n}を止める" in _out for n in ("snmp-sinks-iceberg（oi）", "snmp-sinks-http（oh）", "snmp-sinks-splunk（os）")))
_now = [{"id": "a", "name": "sinks-s3iceberg", "state": "RUNNING", "spec": _spec(_D["iceberg"])},
        {"id": "b", "name": "sinks-grafana", "state": "RUNNING", "spec": _spec(_D["http"])}]
_rc, _out, _st = _run75(_now + [{"id": "oh", "name": "snmp-sinks-http", "state": "CANCELLED", "spec": "x"}], _D)
check("7-5: どのジョブも SpecHash が同じなら何も止めず、何も起こさない（止まった古い名前のジョブは見ない。sinks-grafana を snmp-sinks-http と取り違えない）",
      _rc == 0 and _acts(_st) == [] and _out.count("同じスクリプトと引数で動いている") == 2 and "古い名前" not in _out)
_rc, _out, _st = _run75(_now + [{"id": "oh", "name": "snmp-sinks-http", "state": "RUNNING", "spec": _spec(_D["http"])}], _D)
check("7-5: 新しい名前が動いていても古い名前のジョブ（snmp-sinks-http）が動いていれば止める。起こすジョブが無いので待たない",
      _rc == 0 and _acts(_st) == ["cancel snmp-sinks-http:oh"] and _out.count("同じスクリプトと引数で動いている") == 2
      and "古い名前のジョブ snmp-sinks-http（oh）を止める" in _out)
_rc, _out, _st = _run75(_now, dict(_D, http='{"j":"http","new":1}'))
check("7-5: 引数が変わったジョブ（http）だけ止めて起こし直し、iceberg はそのまま",
      _rc == 0 and _acts(_st) == ["cancel sinks-grafana:b", "start sinks-grafana STREAMING"])
_rc, _out, _st = _run75(_now + [{"id": "c", "name": "sinks-splunk", "state": "RUNNING", "spec": "s"}], _D)
check("7-5: 格納先が無くなったジョブ（STORES から splunk を外した）は止めるだけで起こさない",
      _rc == 0 and _acts(_st) == ["cancel sinks-splunk:c"] and "sinks-splunk（c）は格納先が無くなったので止める" in _out)
_rc, _out, _st = _run75(_now + [{"id": "d", "name": "sinks-s3iceberg", "state": "RUNNING", "spec": _spec(_D["iceberg"])}], _D)
check("7-5: 同じ名前のジョブが 2 つ動いていれば 1 つだけ残して止め、起こし直さない（同じ checkpoint を 2 つで使わない）",
      _rc == 0 and _acts(_st) == ["cancel sinks-s3iceberg:d"])
_rc, _out, _st = _run75([{"id": "e", "name": "sinks-s3iceberg", "state": "CANCELLING", "left": 3, "spec": "x"}], dict(_D, http=""))
_rc2, _out2, _st2 = _run75([{"id": "e", "name": "snmp-sinks-iceberg", "state": "CANCELLING", "left": 3, "spec": "x"}], dict(_D, http=""))
check("7-5: 前の up.sh が止めきれなかった（CANCELLING の）同じ名前か古い名前のジョブがあれば、止まるまで待ってから起こす",
      _rc == 0 and _acts(_st) == ["start sinks-s3iceberg STREAMING"] and any("sinks-s3iceberg:e:CANCELLING" in l for l in _st["log"])
      and _rc2 == 0 and _acts(_st2) == ["start sinks-s3iceberg STREAMING"] and any("snmp-sinks-iceberg:e:CANCELLING" in l for l in _st2["log"]))
_rc, _out, _st = _run75([{"id": "old", "name": "snmp-sinks", "state": "RUNNING", "spec": "x"}], _D, cancel_delay=1000)
_rc2, _out2, _st2 = _run75([{"id": "oh", "name": "snmp-sinks-http", "state": "RUNNING", "spec": "x"}], _D, cancel_delay=1000)
check("7-5: 古い名前のジョブ（snmp-sinks / snmp-sinks-http）が 3 分たっても止まらなければ、新しいジョブを起こさずに止まる",
      _rc == 1 and "DIE: Spark のジョブ（old）が 3 分たっても止まらない" in _out and _acts(_st) == ["cancel snmp-sinks:old"]
      and _rc2 == 1 and "DIE: Spark のジョブ（oh）が 3 分たっても止まらない" in _out2 and _acts(_st2) == ["cancel snmp-sinks-http:oh"])
_rc, _out, _st = _run75([{"id": "q", "name": "sinks-grafana", "state": "QUEUED", "spec": "x"}], dict(_D, iceberg=""))
check("7-5: 待っている（QUEUED の）ジョブも動いているものとして扱い、SpecHash が違えば止めてから起こす",
      _rc == 0 and _acts(_st) == ["cancel sinks-grafana:q", "start sinks-grafana STREAMING"])
# 7-4 の前の「上限が変わるときだけジョブとアプリを止める」を up.sh から切り出し、偽の aws（アプリの状態も持つ）で動かす
_b74 = up[up.index("  # EMR Serverless のアプリの上限（maximum_capacity。"):up.index('  tf_apply pipeline/analytics "${ANALYTICS_VARS[@]}"')]
_tfv = open(os.path.join(ROOT, "terraform", "pipeline", "analytics", "variables.tf"), encoding="utf-8").read()
_m_cpu = re.search(r'variable "max_cpu" \{[^}]*default\s*=\s*"([^"]+)"', _tfv)
_m_mem = re.search(r'variable "max_memory" \{[^}]*default\s*=\s*"([^"]+)"', _tfv)
check("up.sh の EMR_MAX_CPU / EMR_MAX_MEMORY は variables.tf の max_cpu / max_memory の既定値と同じで、tf_apply の前に止める判断がある",
      f'EMR_MAX_CPU="{_m_cpu.group(1)}"; EMR_MAX_MEMORY="{_m_mem.group(1)}"' in _b74
      and 'ANALYTICS_VARS+=(-var "max_cpu=$EMR_MAX_CPU" -var "max_memory=$EMR_MAX_MEMORY" -var "emr_az_num=$EMR_AZ_NUM")' in _b74
      and up.index("ANALYTICS_VARS=(-var") < up.index(_b74) < up.index('tf_apply pipeline/analytics "${ANALYTICS_VARS[@]}"'))
_FAKE_AWS74 = "#!" + sys.executable + r"""
import json, os, sys
st_path = os.environ["FAKE_STATE"]
st = json.load(open(st_path))
a = sys.argv[1:]
opt = lambda k: a[a.index(k) + 1]
def opts(k):
    i, out = a.index(k) + 1, []
    while i < len(a) and not a[i].startswith("--"):
        out.append(a[i]); i += 1
    return out
cmd, app = a[1], st["app"]
if cmd == "get-application":
    if app is None:
        sys.exit("ResourceNotFoundException")
    q = opt("--query")
    if q == "application.[state,maximumCapacity.cpu,maximumCapacity.memory,length(networkConfiguration.subnetIds || `[]`)]":
        print("\t".join([app["state"], app["cpu"], app["memory"], str(app.get("subnets", 1))]))
    else:
        assert q == "application.state", q
        if app["state"] == "STOPPING":
            app["left"] -= 1
            if app["left"] <= 0:
                app["state"] = "STOPPED"
        print(app["state"])
elif cmd == "list-job-runs":  # 呼ばれるたびに時間が進む: 止めている途中のものは left 回で止まる
    for r in st["runs"]:
        if r["state"] == "CANCELLING":
            r["left"] -= 1
            if r["left"] <= 0:
                r["state"] = "CANCELLED"
    assert opt("--query") == "jobRuns[].id", opt("--query")
    hits = [r for r in st["runs"] if r["state"] in opts("--states")]
    st["log"].append("list " + " ".join(f'{r["id"]}:{r["state"]}' for r in hits))
    print("\t".join(r["id"] for r in hits))
elif cmd == "cancel-job-run":
    r = next(r for r in st["runs"] if r["id"] == opt("--job-run-id"))
    r["state"], r["left"] = "CANCELLING", st["cancel_delay"]
    st["log"].append(f'cancel {r["id"]}')
elif cmd == "stop-application":  # ジョブが残っていれば断る（実物も止まらない）
    if any(r["state"] not in ("CANCELLED", "SUCCESS", "FAILED") for r in st["runs"]):
        st["log"].append("stop REFUSED")
        json.dump(st, open(st_path, "w"))
        sys.exit("ValidationException: jobs are running")
    st["log"].append("stop")
    if app["state"] == "STARTED":
        app["state"], app["left"] = "STOPPING", st["stop_delay"]
else:
    sys.exit("unknown " + cmd)
json.dump(st, open(st_path, "w"))
"""
def _run74(app, runs=(), cancel_delay=2, stop_delay=2, state_file=True, emr_az_num=1):
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "aws"), "w") as f:
            f.write(_FAKE_AWS74)
        os.chmod(os.path.join(d, "aws"), 0o755)
        if state_file:
            os.makedirs(os.path.join(d, "terraform", "pipeline", "analytics"))
            open(os.path.join(d, "terraform", "pipeline", "analytics", "terraform.tfstate"), "w").close()
        state = os.path.join(d, "state.json")
        with open(state, "w") as f:
            json.dump({"app": copy.deepcopy(app), "runs": copy.deepcopy(list(runs)), "log": [],
                       "cancel_delay": cancel_delay, "stop_delay": stop_delay}, f)
        pre = (f'set -euo pipefail\nREGION=r; ANALYTICS_VARS=(-var x=1); EMR_AZ_NUM={emr_az_num}\n'
               'die() { echo "DIE: $*"; exit 1; }\nsleep() { :; }\ntf_init() { :; }\nhas_resources() { return 0; }\n'
               'tf() { case "$4" in application_id) echo app ;; list_job_runs_command) echo LIST ;; *) echo "tf? $*" >&2; exit 9 ;; esac; }\n')
        r = subprocess.run(["bash", "-c", pre + _b74 + '\nprintf "VARS:%s\\n" "${ANALYTICS_VARS[@]}"'], capture_output=True, text=True, cwd=d,
                           env={"PATH": d + os.pathsep + os.environ["PATH"], "FAKE_STATE": state})
        with open(state) as f:
            st = json.load(f)
        return r.returncode, r.stdout + r.stderr, st
    finally:
        shutil.rmtree(d)
_acts74 = lambda st: [l for l in st["log"] if l.startswith(("cancel", "stop"))]
_old_app = {"state": "STARTED", "cpu": "4 vCPU", "memory": "16 GB"}
_runs74 = [{"id": "a", "name": "snmp-sinks", "state": "RUNNING"}, {"id": "b", "name": "sinks-grafana", "state": "QUEUED"},
           {"id": "c", "name": "sinks-s3iceberg", "state": "SUCCESS"}]
_rc, _out, _st = _run74(_old_app, _runs74, cancel_delay=3)
check("7-4 の前（上限が変わる）: 動いている・待っている（QUEUED）ジョブを全部 cancel し、止まってから stop-application、STOPPED を待つ",
      _rc == 0 and _acts74(_st) == ["cancel a", "cancel b", "stop"] and _st["app"]["state"] == "STOPPED"
      and all(r["state"] in ("CANCELLED", "SUCCESS") for r in _st["runs"])
      and "上限を 4 vCPU / 16 GB から 12 vCPU / 48 GB に変える。アプリが STARTED なので" in _out and "アプリ（app）を止めた" in _out
      and "VARS:max_cpu=12 vCPU" in _out and "VARS:max_memory=48 GB" in _out)
_rc, _out, _st = _run74(dict(_old_app, cpu="12 vCPU", memory="48 GB"), _runs74)
_rc2, _out2, _st2 = _run74(dict(_old_app, cpu="12vCPU", memory="48 gb"), _runs74)
check("7-4 の前（上限が変わらない）: ジョブもアプリも止めない（空白と大文字小文字の違いは同じとみる）",
      _rc == 0 and _acts74(_st) == [] and "上限を" not in _out and _rc2 == 0 and _acts74(_st2) == [] and _st2["app"]["state"] == "STARTED")
_rc, _out, _st = _run74(_old_app, _runs74, cancel_delay=1000)
check("7-4 の前（ジョブが止まらない）: 3 分待って止まり、stop-application も tf_apply もしない",
      _rc == 1 and "DIE: アプリの設定を変える前に止めた Spark のジョブ（a\tb）が 3 分たっても止まらない" in _out
      and _acts74(_st) == ["cancel a", "cancel b"] and _st["app"]["state"] == "STARTED")
_rc, _out, _st = _run74(_old_app, _runs74, stop_delay=1000)
check("7-4 の前（アプリが止まらない）: 3 分待って止まる（STOPPING のまま tf_apply に進まない）",
      _rc == 1 and "DIE: EMR Serverless のアプリ（app）が 3 分たっても止まらない（STOPPING）" in _out and _acts74(_st)[-1] == "stop")
_rc, _out, _st = _run74(dict(_old_app, state="STOPPED"), [])
_rc2, _out2, _st2 = _run74(None, [])
_rc3, _out3, _st3 = _run74(_old_app, _runs74, state_file=False)
check("7-4 の前: アプリが止まっていれば上限が変わっても何も止めない。アプリが無いか state が無ければ何もしない（terraform に任せる）",
      _rc == 0 and _acts74(_st) == [] and not any(l.startswith("list") for l in _st["log"]) and "（アプリは STOPPED）" in _out
      and _rc2 == 0 and _st2["log"] == [] and _rc3 == 0 and _st3["log"] == [] and _st3["app"]["state"] == "STARTED")
_new_app = dict(_old_app, cpu="12 vCPU", memory="48 GB")
_rc, _out, _st = _run74(dict(_new_app, subnets=2), _runs74)
_rc2, _out2, _st2 = _run74(dict(_new_app, subnets=3), _runs74, emr_az_num=3)
check("7-4 の前（EMR_AZ_NUM でサブネットの数が変わる。2026-10-04 までの 2 つ → 既定 1 つも）: 上限が同じでもジョブとアプリを止める。同じ数なら止めない",
      _rc == 0 and _acts74(_st) == ["cancel a", "cancel b", "stop"] and _st["app"]["state"] == "STOPPED"
      and "EMR Serverless のアプリのサブネットを 2 つから 1 つに変える。アプリが STARTED なので" in _out and "上限を" not in _out
      and "VARS:emr_az_num=1" in _out and _rc2 == 0 and _acts74(_st2) == [] and "VARS:emr_az_num=3" in _out2)
_rc, _out, _st = _run74(dict(_old_app, subnets=2), _runs74)
check("7-4 の前（上限もサブネットの数も変わる）: 1 回だけ止めて、両方を 1 行で出す",
      _rc == 0 and _acts74(_st) == ["cancel a", "cancel b", "stop"]
      and "EMR Serverless のアプリの上限を 4 vCPU / 16 GB から 12 vCPU / 48 GB に、サブネットを 2 つから 1 つに変える。" in _out)
print(f"通過 {passed} / 失敗 0")
