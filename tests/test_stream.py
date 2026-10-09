"""取り込みの経路（lab の機器 → Telegraf（ECS）→ MSK → Spark）の模擬テスト。lab の機器の設定・Telegraf の設定・IaC/terraform/aws-managed/pipeline/stream と、
Spark（app/spark/snmp_sinks.py）が異常の検知をしなくなったこと（2026-10-02。検知は Grafana のアラートルールと Splunk の保存済みサーチ → tests/test_alerts.py）を確かめる。
実行は python3 tests/test_stream.py（pyspark も boto3 も要らない。snmp_sinks.py は pyspark を関数の中で import する）。"""
import importlib.util, ipaddress, json, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "app", "spark", "snmp_sinks.py")

passed = 0
def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)

# ---- IaC/terraform/aws-managed/pipeline/stream: 検知の資源（detector Lambda・DynamoDB の異常テーブル）は持たない
TF_DIR = os.path.join(ROOT, "IaC", "terraform", "aws-managed", "pipeline", "stream")
tf = ""
for name in sorted(os.listdir(TF_DIR)):
    if name.endswith(".tf"):
        with open(os.path.join(TF_DIR, name), encoding="utf-8") as f:
            tf += f.read() + "\n"
check("stream/detector.py は無い（検知は Grafana と Splunk）", not os.path.exists(os.path.join(ROOT, "stream", "detector.py")))
check("IaC/terraform/aws-managed/pipeline/stream に detector の Lambda が無い", '"detector"' not in tf and "stream/detector.py" not in tf and "archive_file" not in tf)
check("IaC/terraform/aws-managed/pipeline/stream に lambda のエンドポイントが無い", '.lambda"' not in tf)
check("IaC/terraform/aws-managed/pipeline/stream に MSK Connect の S3 sink が無い（Spark が S3 Tables に入れるので 2026-09-26 に削除。sts のエンドポイントも一緒に）",
      not os.path.exists(os.path.join(TF_DIR, "sink.tf")) and "mskconnect" not in tf and "create_s3_sink" not in tf
      and "kafkaconnect" not in tf and "create_sts_endpoint" not in tf and "aws_vpc_endpoint" not in tf)
check("IaC/terraform/aws-managed/pipeline/stream に DynamoDB が無い（2026-09-24）",
      "aws_dynamodb" not in tf and "anomaly_table" not in tf and "dynamodb:" not in tf and ".dynamodb" not in tf
      and not os.path.exists(os.path.join(TF_DIR, "anomalies.tf")))
check("detector_logs の output は無い", "detector_logs" not in tf)
check("MSK は Kafka 4 以上の KRaft（kafka_version の既定が N.N.x.kraft で、検査が .kraft を強いる）",
      re.search(r'variable "kafka_version" \{[^}]*default\s*=\s*"[4-9]\.\d+\.x\.kraft"', tf) is not None and "x\\\\.kraft$" in tf)
check("ブローカーは Kafka 4 が受け付ける m5 / m7g（t3.small は Unsupported InstanceType。2026-09-18）",
      re.search(r'variable "broker_instance_type" \{[^}]*default\s*=\s*"kafka\.m5\.large"', tf) is not None
      and '"kafka.t3.small"' not in tf)

# ---- app/spark/snmp_sinks.py を pyspark 無しで読む
spec = importlib.util.spec_from_file_location("snmp_sinks", SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
with open(SRC, encoding="utf-8") as f:
    src = f.read()
# 2026-10-02: Spark の検知（detect のクエリ・Neptune の anomaly の頂点・S3 Tables の anomaly_events・EventBridge への put_events）をやめた。
# 検知は Grafana のアラートルール（ポーリング）と Splunk の保存済みサーチ（trap と gNMI）が行い、SNS のトピックへ publish する
_code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#")).split('"""', 2)[2]   # 先頭の docstring とコメント行を除いたコード
# --device-map は cycle 002 で機器名（sysName）を足すために戻した（検知には使わない。test_analytics が見る）
check("Spark のジョブに検知の引数（--neptune-endpoint / --anomaly-events-table / --event-bus / --event-source）は無い",
      not any(f'"--{a}"' in src for a in ("neptune-endpoint", "anomaly-events-table", "anomaly-table", "event-bus", "event-source")))
check("Spark のジョブは Neptune にも EventBridge にも触らず、boto3 を読むのは SSM の HEC token だけ",
      not any(w in _code for w in ("put_events", "gremlin", "neptune", "anomaly", '"detect"', 'client("events")', 'client("dynamodb")'))
      and re.findall(r'boto3\.client\("(\w+)"', _code) == ["ssm"])
check("検知の関数と定数（events / device / NeptuneAnomalies / make_detect_sender / EVENT_SOURCE / TRAP_TTL）はもう無い（parse_device_map は cycle 002 で機器名を足すために戻した）",
      not any(hasattr(mod, n) for n in ("events", "device", "anomaly_key", "anomaly_detail", "NeptuneAnomalies", "make_detect_sender",
                                        "make_history_writer", "gremlin_literal", "graphson", "EVENT_SOURCE", "EVENT_DETAIL_TYPE", "TRAP_TTL", "ANOMALY_EVENT_COLUMNS")))
BASE = ["--bootstrap", "b", "--checkpoint", "c", "--sinks", "iceberg", "--iceberg-table", "cat.ns.t"]
check("格納先は iceberg / opensearch / prometheus / splunk の 4 つで、マイクロバッチは 60 秒（アラートが届くまでの遅れの一部）",
      mod.SINKS == ("iceberg", "opensearch", "prometheus", "splunk") and mod.TRIGGER == "60 seconds" and mod.parse_args(BASE).sinks == ["iceberg"])
check("空のマイクロバッチでは sender を呼ばない（検知の見回りのための例外は無くなった）",
      re.search(r"\n\s*if records:\n\s*dropped = sender\(records\)", src) is not None and 'name == "detect"' not in src)


# ---- ログの経路: SR Linux の system logging remote-server（udp）→ lab の EC2（203.0.113.1:5140 を NLB へ DNAT）→ syslog-ng（cycle 012。それまでは Telegraf の inputs.syslog）
# → Kafka の logs → Spark（2026-09-26。FRR + rsyslog をやめた）
def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()

def read_ops(name):  # ops/up.sh / down.sh は、読んでいる共通の関数（ops/common.sh と ops/<name>-common.sh。OSS 版の ops/oss/ と共通）とつないで見る
    return _read("ops", "common.sh") + _read("ops", f"{name}-common.sh") + _read("ops", f"{name}.sh")
srl_dir = os.path.join(ROOT, "app", "containerlab", "srlinux")
srl_nodes = sorted(n[:-4] for n in os.listdir(srl_dir) if n.endswith(".cli"))
srl_cfg = {n: _read("app", "containerlab", "srlinux", n + ".cli") for n in srl_nodes}
clab = _read("app", "containerlab", "splab.clab.yml.in")
labsh = _read("app", "containerlab", "lab.sh")
tele = _read("app", "telegraf", "telegraf.conf.in")
tgsh = _read("app", "telegraf", "telegraf.sh")
lab_locals = _read("IaC", "terraform", "aws-managed", "pipeline", "lab", "locals.tf")
stream_tg = _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "telegraf.tf")
stream_col = _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "collectors.tf")
core_sg = _read("IaC", "terraform", "aws-managed", "base", "core", "security_groups.tf")
check("SR Linux の 6 台の設定は set / の行だけ（containerlab が候補に流し込んで commit する。enter candidate / commit を書くと二重になる）",
      len(srl_nodes) == 6 and all(all(re.match(r"^(set / |#|\s*$)", l) for l in c.splitlines()) for c in srl_cfg.values()))
check("containerlab は 6 台とも nokia_srlinux で srlinux/<機器名>.cli を startup-config にする",
      len(re.findall(r"^\s*kind: nokia_srlinux$", clab, re.M)) == 6
      and all(f"startup-config: srlinux/{n}.cli" in clab for n in srl_nodes) and "snmpd" not in clab and "binds:" not in clab)
mgmt_gw = re.search(r"^MGMT_GW=(\S+)$", labsh, re.M).group(1)
log_port = re.search(r"^LOG_PORT=(\d+)$", labsh, re.M).group(1)
check("6 台とも syslog を lab.sh の MGMT_GW:LOG_PORT/udp に送る（forward が Telegraf へ DNAT する）",
      all(f"set / system logging remote-server {mgmt_gw} transport udp" in c and f"set / system logging remote-server {mgmt_gw} remote-port {log_port}" in c
          and "set / system logging network-instance mgmt" in c for c in srl_cfg.values()))
check("6 台とも syslog のファシリティは local7（本番の Cisco の既定に合わせる。SR Linux の既定は local6）",
      all("set / system logging subsystem-facility local7" in c for c in srl_cfg.values()))
check("trap の宛先は 6 台とも lab.sh の MGMT_GW:162（Spine-Leaf の全部が監視対象）",
      all(f"destination telegraf address {mgmt_gw}" in srl_cfg[n] and "trap-group telegraf admin-state enable" in srl_cfg[n] for n in srl_nodes))
check("6 台とも IS-IS（instance main）と iBGP EVPN（AS 65100）を持ち、Spine だけ route-reflector",
      all("protocols isis instance main" in c and "protocols bgp autonomous-system 65100" in c and "afi-safi evpn admin-state enable" in c for c in srl_cfg.values())
      and all(("route-reflector client true" in srl_cfg[n]) == ("-spine-" in n) for n in srl_nodes))
check("containerlab の TRex 1 台は linux で、各 leaf の e1-3 へ 1 本ずつ（bond は組まない）", len(re.findall(r"^\s*kind: linux$", clab, re.M)) == 1 and "bond" not in clab
      and all(f'"dc1-trex-01:eth{i}", "dc1-{l}:e1-3"' in clab for i, l in enumerate(("s-leaf-01", "s-leaf-02", "a-leaf-01", "a-leaf-02"), 1)))
check("lab.sh forward は syslog の LOG_PORT も trap の 162 と同じ仕組みで DNAT する（rsyslog は無い）",
      re.search(r'-p udp --dport "\$LOG_PORT" "\$\{c\[@\]\}" -j DNAT --to-destination "\$t:\$LOG_PORT"', labsh) is not None
      and "rsyslog" not in labsh and "LOG_DIR" not in labsh and re.search(r"^\s*logs\)", labsh, re.M) is not None)
# NLB の受け口は stream の collector_listeners の表（cycle 012）。受け口 → サービスとそのタスクのポートで、どれも UDP。trap は NLB の 162 → タスクの 1162（非 root）。
# 同じ番号が lab.sh（syslog の LOG_PORT と NetFlow / sFlow の DNAT）と土台の SG の通信の表にもある
_cl_blk = re.search(r"^  collector_listeners = \{\n(.*?)^  \}\n", stream_tg, re.M | re.S)
_cl = {k: (int(lp), int(cp), sv) for k, lp, cp, sv in re.findall(r'^\s*(\w+)\s*= \{ listener = (\d+), container = (\d+), service = "([\w-]+)" \}$', _cl_blk.group(1), re.M)} if _cl_blk else {}
_hc_blk = re.search(r"^  collector_health_checks = \{\n(.*?)^  \}\n", stream_tg, re.M | re.S)
_hc_rows = re.findall(r'^\s*"?([\w-]+)"?\s*= \{ protocol = "(\w+)", port = "(\d+)", path = ("[^"]*"|null) \}$', _hc_blk.group(1), re.M) if _hc_blk else []
_hc = {k: (pr, int(pt)) for k, pr, pt, _ in _hc_rows}
_hc_path = {k: pa for k, _, _, pa in _hc_rows}
_svc_sg = {"telegraf-dialout": "telegraf_dialout", "syslog-ng": "syslog_ng", "goflow2": "goflow2"}
def _sg_row(a, b, proto, pt):
    return re.search(rf'\{{ from = "{a}", to = "{b}", protocol = "{proto}", port = {pt},', core_sg) is not None
check("NLB の受け口（collector_listeners）は trap 162→1162（telegraf-dialout）・syslog は lab.sh の LOG_PORT（syslog-ng）・netflow 2055 と sflow 6343（goflow2）の 4 つで、"
      "target group も listener も UDP（MDT の 57000/tcp と telegraf_ports は cycle 012 で外した）",
      _cl == {"trap": (162, 1162, "telegraf-dialout"), "syslog": (int(log_port), int(log_port), "syslog-ng"), "netflow": (2055, 2055, "goflow2"), "sflow": (6343, 6343, "goflow2")}
      and stream_tg.count("for_each = local.collector_listeners") == 2 and 'protocol    = "UDP"' in stream_tg and 'protocol          = "UDP"' in stream_tg
      and "preserve_client_ip = true" in stream_tg and "telegraf_ports" not in stream_tg
      and "57000" not in "\n".join(l for l in (stream_tg + stream_col).splitlines() if not l.lstrip().startswith("#"))
      and re.search(r'^\s*service_address = "udp://__BIND__:__TRAP_PORT__"$', tele, re.M) is not None and re.search(r"^TRAP_PORT=\$\{TRAP_PORT:-1162\}$", tgsh, re.M) is not None
      and "log_port" not in lab_locals)
check("土台の SG の通信の表に、受け口ごとの 3 本（管理ネットワーク → NLB、lab の EC2 → NLB、NLB → サービスの SG のタスクのポート）と、サービスごとのヘルスチェックの tcp がある",
      len(_cl) == 4 and sorted(_hc) == sorted(_svc_sg)
      and all(_sg_row("lab_mgmt", "telegraf_dialout_nlb", "udp", lp) and _sg_row("lab", "telegraf_dialout_nlb", "udp", lp)
              and _sg_row("telegraf_dialout_nlb", _svc_sg[sv], "udp", cp) for lp, cp, sv in _cl.values())
      and all(_sg_row("telegraf_dialout_nlb", _svc_sg[sv], "tcp", pt) for sv, (_, pt) in _hc.items())
      and _hc == {"telegraf-dialout": ("HTTP", 8080), "syslog-ng": ("TCP", int(log_port)), "goflow2": ("HTTP", 8081)}
      and _hc_path == {"telegraf-dialout": '"/"', "syslog-ng": "null", "goflow2": '"/__health"'}   # GoFlow2 の / は 404（200 は /__health だけ）
      and not re.search(r'from = "telegraf_dialout_nlb", to = "telegraf_dialout", protocol = "(udp", port = 5140|tcp", port = 57000)', core_sg))
_col_td = {k: m.group(0) for k in ("syslog_ng", "goflow2")
           if (m := re.search(r'^resource "aws_ecs_task_definition" "' + k + r'" \{\n(?:.*\n)*?^\}\n', stream_col, re.M))}
check("syslog-ng と GoFlow2 のタスク定義は、それぞれの実行ロールで local.kafka_collector_secrets（SCRAM のユーザー名とパスワード）を入れ、"
      "実行ロールのポリシーは local.kafka_collector_execution_statements。syslog-ng は KAFKA_BROKERS / KAFKA_AUTH、GoFlow2 は引数でブローカーと SCRAM を受ける",
      sorted(_col_td) == ["goflow2", "syslog_ng"]
      and all(f"  execution_role_arn       = aws_iam_role.{k}_execution.arn\n" in b and "\n      secrets = local.kafka_collector_secrets\n" in b for k, b in _col_td.items())
      and all(re.search(r'^resource "aws_iam_role_policy" "' + k + r'_execution" \{\n(?:(?!^\}).*\n)*?\s*Statement = local\.kafka_collector_execution_statements\n', stream_col, re.M)
              for k in _col_td)
      and '{ name = "KAFKA_BROKERS", value = local.kafka_collector_brokers },' in _col_td["syslog_ng"]
      and '{ name = "KAFKA_AUTH", value = local.kafka_collector_auth },' in _col_td["syslog_ng"]
      and "      command   = local.goflow2_command\n" in _col_td["goflow2"]
      and '"-transport.kafka.brokers=${local.kafka_collector_brokers}",' in stream_col
      and 'local.collector_scram ? ["-transport.kafka.tls", "-transport.kafka.sasl=scram-sha512"] : [],' in stream_col
      and '  collector_scram          = local.kafka_collector_auth == "scram"\n' in stream_col)
_svc_lb = {k: m.group(1) for k in ("telegraf_dialout", "syslog_ng", "goflow2")
           if (m := re.search(r'resource "aws_ecs_service" "' + k + r'" \{[^\n]*\n(?:(?!^\}).*\n)*?\s*dynamic "load_balancer" \{\n\s*for_each = (.+)\n', stream_tg + "\n" + stream_col, re.M))}
check("3 つのサービスは collector_listeners のうち自分の分だけを load_balancer に持つ（同じ target group に 2 つのサービスが入らない）",
      _svc_lb == {k: f'{{ for k, v in local.collector_listeners : k => v if v.service == "{sv}" }}' for k, sv in (("telegraf_dialout", "telegraf-dialout"), ("syslog_ng", "syslog-ng"), ("goflow2", "goflow2"))})
# MDT と syslog の受け口は Telegraf から外した（cycle 012。MDT は使う機器が無い、syslog は syslog-ng）
check("Telegraf（telegraf.conf.in / telegraf.sh）に MDT と syslog の受け口が無い（inputs.cisco_telemetry_mdt / inputs.syslog / MDT_PORT / LOG_PORT / SYSLOG_STANDARD）",
      "cisco_telemetry_mdt" not in tele and "inputs.syslog" not in tele and "__MDT_PORT__" not in tele and "__LOG_PORT__" not in tele and "__SYSLOG_STANDARD__" not in tele
      and not any(k in tgsh for k in ("MDT_PORT", "LOG_PORT", "SYSLOG_STANDARD"))
      and "57000" not in lab_locals and "57000" not in labsh
      and not re.search(r"containerPort = (5140|57000)", stream_tg) and "SYSLOG_STANDARD" not in stream_tg)
# 管理ネットワークは 4 か所で同じ（containerlab の mgmt / lab.sh / lab の locals の VPC ルート / 土台の SG の lab_mgmt）
mgmt = re.search(r"^MGMT=(\S+)$", labsh, re.M).group(1)
check("管理ネットワークが containerlab・lab.sh・lab の locals・土台の SG の lab_mgmt_cidr で同じ",
      re.search(rf"^\s*ipv4-subnet: {re.escape(mgmt)}$", clab, re.M) is not None
      and re.search(rf'^\s*mgmt_cidr\s*=\s*"{re.escape(mgmt)}"$', lab_locals, re.M) is not None
      and re.search(rf'^\s*lab_mgmt_cidr\s*=\s*"{re.escape(mgmt)}"$', core_sg, re.M) is not None)
# gNMI の購読先は lab の定義から作る（app/containerlab/lab_topology.py --gnmi-targets → up.sh が stream の gnmi_targets → SSM のパラメータ → gnmic のタスクの
# GNMI_TARGETS → gnmic.sh render が埋める）。SNMP のポーリング先（--snmp-agents）は cycle 013 でやめた
_lt_spec = importlib.util.spec_from_file_location("lab_topology", os.path.join(ROOT, "app", "containerlab", "lab_topology.py"))
lt = importlib.util.module_from_spec(_lt_spec); _lt_spec.loader.exec_module(lt)
_lab_devices, _, _ = lt.load(os.path.join(ROOT, "app", "containerlab"))
_gnmi_line = lt.gnmi_targets(_lab_devices)
_gnmi_ips = re.findall(r"([\d.]+):57400", _gnmi_line)
stream_gn = _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "gnmic.tf")
check("gNMI の購読先は lab の監視対象（enabled）6 台の管理 IP:57400 で、全部管理ネットワークの中（VPC のルートで lab の EC2 へ行く）。形は gnmic.sh の検査と同じ",
      len(_gnmi_ips) == 6 and sorted(_gnmi_ips) == sorted(d["mgmt_ip"] for d in _lab_devices if d["enabled"])
      and all(ipaddress.ip_address(a) in ipaddress.ip_network(mgmt) for a in _gnmi_ips)
      and re.fullmatch(r'"[0-9.]+:[0-9]+"(, *"[0-9.]+:[0-9]+")*', _gnmi_line) is not None)
check("SNMP のポーリング先は作らない（lab_topology.py に snmp_agents も --snmp-agents も無い。Telegraf の設定にも __SNMP_AGENTS__ / __GNMI_TARGETS__ が無い）",
      not hasattr(lt, "snmp_agents") and "--snmp-agents" not in lt.FLAGS
      and not any(k in tele + tgsh for k in ("__SNMP_AGENTS__", "__GNMI_TARGETS__", "SNMP_AGENTS", "GNMI_TARGETS")))
check("gnmic のタスクの GNMI_TARGETS は SSM のパラメータ（/<接頭辞>/gnmic/<lab|nautobot>/gnmi-targets）から ECS の secrets で受け、lab のときは stream の gnmi_targets を Terraform が書く",
      '[{ name = "GNMI_TARGETS", valueFrom = "${local.ssm_parameter_arn}${local.gnmic_targets_name}" }],' in stream_gn
      and 'gnmic_targets_name     = "${local.gnmic_parameter_prefix}/${local.gnmic_target_source}/gnmi-targets"' in stream_gn
      and 'gnmic_target_source    = var.gnmi_targets_from_nautobot ? "nautobot" : "lab"' in stream_gn
      and stream_gn.count("value       = var.gnmi_targets\n") == 2 and "ignore_changes = [value]" in stream_gn)
_dockerfile = _read("docker", "images", "telegraf", "Dockerfile")
check("Telegraf は trap だけ受ける: inputs は snmp_trap だけ（inputs.snmp / inputs.gnmi / Starlark は cycle 013 で外した）。.star はリポジトリにもイメージにも無い",
      re.findall(r"^\[\[(inputs\.\w+)\]\]", tele, re.M) == ["inputs.snmp_trap"] and "starlark" not in tele
      and not os.path.exists(os.path.join(ROOT, "app", "telegraf", "lab_gnmi.star")) and not os.path.exists(os.path.join(ROOT, "app", "telegraf", "lab_circuits.star"))
      and re.findall(r"^COPY .*$", _dockerfile, re.M) == ["COPY telegraf.conf.in /etc/telegraf/", "COPY --chmod=0755 telegraf.sh /usr/local/bin/tg"])
check("Telegraf の Kafka の出力は traps の 1 つ（mdt と logs は cycle 012、metrics と gnmi は cycle 013 で外した。metrics / gnmi は gnmic が書く）",
      re.findall(r'^\s*topic = "(\w+)"', tele, re.M) == ["traps"] and "tagpass" not in tele)
check("lab.sh forward は gNMI の GNMI_PORT/tcp を stream（gnmic）から管理ネットワークへ通し、SNMP の 161/udp は通さない（ポーリングは cycle 013 でやめた）",
      re.search(r'-p tcp --dport "\$GNMI_PORT" "\$\{c\[@\]\}" -j ACCEPT', labsh) is not None and re.search(r"^GNMI_PORT=57400$", labsh, re.M) is not None
      and "--dport 161" not in labsh)
_up = read_ops("up")
_td_sng = re.search(r'resource "aws_ecs_task_definition" "syslog_ng" \{[\s\S]*?^\}', stream_col, re.M)
check("syslog の形式は stream の syslog_standard（既定 RFC3164 = 本番の Cisco）→ syslog-ng のタスクの SYSLOG_STANDARD（cycle 012。それまでは Telegraf）。"
      "up.sh も deploy.env の SYSLOG_STANDARD（既定 RFC3164。lab の SR Linux は RFC5424）を渡す",
      _td_sng is not None and '{ name = "SYSLOG_STANDARD", value = var.syslog_standard }' in _td_sng.group(0) and "syslog_standard" not in stream_tg
      and re.search(r'variable "syslog_standard" \{[^}]*default\s*=\s*"RFC3164"', _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "variables.tf")) is not None
      and '-var "syslog_standard=$SYSLOG_STANDARD"' in _up and 'SYSLOG_STANDARD="${SYSLOG_STANDARD:-RFC3164}"' in _up and '[ "$SYSLOG_STANDARD" != "$LAB_SYSLOG_STANDARD" ]' in _up
      and "case \"$SYSLOG_STANDARD\" in RFC3164 | RFC5424) ;;" in _up
      and re.search(r"\bSYSLOG_STANDARD\b", _read("ops", "deploy-env.sh").split("DEPLOY_ENV_KEYS=", 1)[1].split('"')[1]) is not None
      and re.search(r"^#SYSLOG_STANDARD=RFC5424$", _read("deploy.env.example"), re.M) is not None and re.search(r"^LAB_SYSLOG_STANDARD=RFC5424\b", _read("ops", "lab-common.sh"), re.M) is not None)
check("SNMP_POLL は cycle 013 から読まない: stream に snmp_poll の変数もタスクの SNMP_POLL も無く、telegraf.sh にも telegraf.conf.in にも無い。"
      "deploy.env に書いてあると up.sh は注意を出すだけ（前の deploy.env で止まらないよう deploy-env.sh は読む）",
      "snmp_poll" not in _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "variables.tf") and "SNMP_POLL" not in stream_tg + stream_gn
      and "SNMP_POLL" not in "\n".join(l for l in tgsh.splitlines() if not l.lstrip().startswith("#")) and "snmp_poll" not in tele
      and 'if [ -n "${SNMP_POLL:-}" ]; then\n  echo "注意: SNMP_POLL は使わない' in _up and "snmp_poll=" not in _up
      and re.search(r"\bSNMP_POLL\b", _read("ops", "deploy-env.sh").split("DEPLOY_ENV_KEYS=", 1)[1].split('"')[1]) is not None
      and re.search(r"^#?SNMP_POLL=", _read("deploy.env.example"), re.M) is None)
_stream_out = _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "outputs.tf")
check("機器の状態を 1 回取って見るのは gnmic のタスクの gn get（stream の output の gnmic_exec_command）。Telegraf の tg test / gnmi と取りにいく側の output は無い",
      "--container gnmic --interactive --command 'gn get'" in _stream_out and "'tg gnmi'" not in _stream_out and "'tg test'" not in _stream_out
      and "telegraf_dialin" not in _stream_out and 'output "gnmic_list_tasks_command"' in _stream_out
      and re.search(r"^  test \| gnmi\)\n.*cycle 013 でやめた.*\n\s*exit 1$", tgsh, re.M) is not None)
check("up.sh は lab の定義から gNMI の購読先を作り、stream の gnmi_targets に渡す（S3 には置かない。SNMP のポーリング先は作らない）",
      'app/containerlab/lab_topology.py app/containerlab --gnmi-targets' in _up and "--snmp-agents" not in _up and "snmp_agents" not in _up
      and '-var "gnmi_targets=$GNMI_TARGETS"' in _up and "/telegraf/" not in _up.replace("app/telegraf/", ""))
check("lab.sh up は毎回 forward を呼び、forward / forward-status がある",
      '"$SELF" forward' in labsh and re.search(r"^\s*forward\)", labsh, re.M) is not None and re.search(r"^\s*forward-status\)", labsh, re.M) is not None)
check("forward の iptables の規則は全部目印付き（unforward で消せる）",
      all("${c[@]}" in l for l in labsh.splitlines() if re.match(r"\s*iptables .*-I ", l)))
check("Telegraf は ifName の OID も SNMP のポーリングも持たない（inputs.snmp は cycle 013 で外した。trap の ifName は Spark が varbind から取る）",
      "[[inputs.snmp]]" not in tele and "1.3.6.1.2.1.31.1.1.1.1" not in tele)
check("Telegraf は機器の syslog を受けない（syslog-ng が logs トピックに出す。cycle 012）: device_log / processors.rename / inputs.tail / inputs.socket_listener が無い",
      not any(re.search(rf"^[^#\n]*{re.escape(k)}", tele, re.M) for k in ("device_log", "processors.rename", "[[inputs.tail]]", "[[inputs.socket_listener]]")))   # コメントの行は数えない
check("traps の出力は namepass で分ける（ほかの measurement が混ざらない）",
      all(re.search(r"name(pass|drop)", blk) for blk in tele.split("[[outputs.kafka]]")[1:]))
check("Spark の既定は gnmi トピックも読む（iceberg は metrics,gnmi,traps,logs,flows、prometheus は metrics,gnmi。mdt は cycle 012 で外した）", mod.METRIC_TOPICS == "metrics,gnmi"
      and mod.sink_topics("iceberg", mod.METRIC_TOPICS, mod.LOG_TOPICS) == "metrics,gnmi,traps,logs,flows" and mod.sink_topics("prometheus", mod.METRIC_TOPICS, mod.LOG_TOPICS) == "metrics,gnmi")
_access = _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "access.tf")
_lab_tg = _read("IaC", "terraform", "aws-managed", "pipeline", "lab", "telegraf.tf")
# Kafka による違いは msk.tf の kafka_* の locals（OSS 版は IaC/terraform/oss/pipeline/stream/kafka.tf。cycle 005）
_msk = _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "msk.tf")
_msk_code = "\n".join(l for l in _msk.splitlines() if not l.lstrip().startswith("#"))
_tg_kafka = _msk_code[_msk_code.index("telegraf_kafka_statements = ["):_msk_code.index("kafka_ui_kafka_statements = [")]
check("Telegraf は stream の ECS で、MSK への書き込みはタスクロール（lab の state のロールに頼らない。2026-09-28）。権限は msk.tf の telegraf_kafka_statements",
      'resource "aws_iam_role" "telegraf_task"' in stream_tg and "kafka-cluster:WriteData" in _tg_kafka
      and "Resource = [aws_msk_cluster.stream.arn, local.topic_arns]" in _tg_kafka
      and "Statement = concat(local.telegraf_kafka_statements, [" in stream_tg and "kafka-cluster" not in stream_tg
      and "stream_produce" not in _access and "telegraf_role_name" not in _access
      and 'resource "aws_iam_role"' not in _lab_tg and 'resource "aws_instance"' not in _lab_tg)
check("lab と stream は SG も SG のルールも作らない（trap・syslog・gNMI のルールは土台の通信の表。2026-09-29）",
      all('resource "aws_security_group"' not in t and "aws_vpc_security_group_" not in t
          for t in (_lab_tg, lab_locals, stream_tg, stream_col, stream_gn, _access, _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "msk.tf"), _read("IaC", "terraform", "aws-managed", "pipeline", "lab", "instance.tf")))
      and 'security_groups = [local.telegraf_dialout_nlb_sg_id]' in stream_tg and 'security_groups  = [local.telegraf_dialout_sg_id]' in stream_tg
      and 'security_groups  = [local.gnmic_sg_id]' in stream_gn)
_td = {k: m.group(0) for k, t in (("telegraf_dialout", stream_tg), ("gnmic", stream_gn)) if (m := re.search(r'resource "aws_ecs_task_definition" "' + k + r'" \{[\s\S]*?^\}', t, re.M))}
_svc = {k: m.group(0) for k, t in (("telegraf_dialout", stream_tg), ("gnmic", stream_gn)) if (m := re.search(r'resource "aws_ecs_service" "' + k + r'" \{[\s\S]*?^\}', t, re.M))}
check("Telegraf は受ける側（dialout。NLB の後ろ、SG telegraf_dialout）の 1 サービスだけで、取りにいく側（telegraf_dialin）は gnmic に置き換えた（cycle 013。"
      "gnmic は 1 タスク固定、NLB なし、SG gnmic）。TELEGRAF_ROLE は無い",
      len(_td) == 2 and len(_svc) == 2 and stream_tg.count('resource "aws_ecs_task_definition"') == 1 and stream_tg.count('resource "aws_ecs_service"') == 1
      and "telegraf_dialin" not in "\n".join(l for l in stream_tg.splitlines() if not l.lstrip().startswith("#")) and "TELEGRAF_ROLE" not in _td["telegraf_dialout"]
      and "portMappings = [" in _td["telegraf_dialout"] and "portMappings" not in _td["gnmic"].replace("portMappings なし", "")
      and all(v not in _td["telegraf_dialout"] for v in ("SNMP_AGENTS", "GNMI_TARGETS", "SNMP_POLL"))
      and 'awslogs-stream-prefix = "dialout"' in _td["telegraf_dialout"] and 'awslogs-stream-prefix = "gnmic"' in _td["gnmic"]
      and "load_balancer" in _svc["telegraf_dialout"] and "load_balancer" not in _svc["gnmic"]
      # 購読を二重にしない: 入れ替えでも 1 タスクを超えない
      and re.search(r"desired_count\s+= 1\b", _svc["gnmic"]) is not None
      and "deployment_minimum_healthy_percent = 0" in _svc["gnmic"] and "deployment_maximum_percent         = 100" in _svc["gnmic"]
      and "enable_execute_command = true" in _svc["gnmic"] and "subnets          = [local.telegraf_subnet_id]" in _svc["gnmic"]
      and re.search(r'gnmic_sg_id\s*=\s*try\(data\.terraform_remote_state\.main\.outputs\.security_group_ids\["gnmic"\], ""\)', _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "locals.tf")) is not None
      and '"$TG_DIALOUT_SERVICE" "$GNMIC_SERVICE"' in _up and "TG_DIALIN" not in _up and "telegraf_dialin_list_tasks_command" not in _up)
_exec_pol = re.search(r'resource "aws_iam_role_policy" "gnmic_execution" \{[\s\S]*?^\}', stream_gn, re.M)
check("gnmic は機器の一覧と gNMI の資格情報を SSM から ECS の secrets で受ける（environment に載せない）。資格情報の SecureString は up.sh が stream の apply の前に作り、"
      "実行ロールが読めるのは /<接頭辞>/gnmic の下だけ。Telegraf のタスクは secrets を持たない（SNMP の community も無い）",
      "secrets" not in _td["telegraf_dialout"] and "secrets = concat(" in _td["gnmic"]
      and not re.search(r'name = "(GNMI_TARGETS|GNMI_USERNAME|GNMI_PASSWORD|SNMP_COMMUNITY)", value =', stream_tg + stream_gn) and "SNMP_COMMUNITY" not in stream_tg + stream_gn
      and 'gnmic_credentials = { GNMI_USERNAME = "gnmi-username", GNMI_PASSWORD = "gnmi-password" }' in stream_gn
      and 'gnmic_parameter_prefix = "/${local.name_prefix}/gnmic"' in stream_gn
      and all(f'ensure_fixed_secret "/$PREFIX/gnmic/{leaf}" "$LAB_{var}"' in _up and _up.index(f'ensure_fixed_secret "/$PREFIX/gnmic/{leaf}"') < _up.index("tf_apply pipeline/stream ")
              for leaf, var in (("gnmi-username", "GNMI_USERNAME"), ("gnmi-password", "GNMI_PASSWORD")))
      and "ensure_fixed_secret \"/$PREFIX/telegraf-dialin/" not in _up and "LAB_SNMP_COMMUNITY" not in _up + _read("ops", "lab-common.sh")
      and _exec_pol is not None and "ssm:GetParameters" in _exec_pol.group(0) and "${local.gnmic_parameter_prefix}/*" in _exec_pol.group(0)
      and "local.kafka_collector_execution_statements" in _exec_pol.group(0))
check("up.sh は base/core の state に古い Telegraf の SG（telegraf、2026-10-04 より前。telegraf_dialin、2026-10-09 より前）があり stream が残っていれば、ECR より前に止める"
      "（SG のキーを変えると作り直しで、付けたままでは消せない）",
      all(f"""grep -qxF 'aws_security_group.workload["{k}"]'""" in _up and _up.index(f"""'aws_security_group.workload["{k}"]'""") < _up.index('log "1. ECR リポジトリ')
          for k in ("telegraf", "telegraf_dialin"))
      and _up.count('[ -s "$TF_DIR/pipeline/stream/terraform.tfstate" ] && { tf_init pipeline/stream;') >= 2)
check("stream は base/core の state に gnmic の SG が無ければ（cycle 013 より前の state）apply の前に止める（remote_state の postcondition が見るキーが gnmic）",
      'condition     = can(self.outputs.security_group_ids["gnmic"])' in _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "locals.tf"))
_down = _read("ops", "down.sh")
check("down.sh は stream の必須変数（gnmi_targets）に形だけ合う値を渡して destroy する（variables.tf の形の検査と同じ。snmp_agents は cycle 013 で無くした）",
      re.search(r"destroy_root pipeline/stream -var 'gnmi_targets=\"[0-9.]+:57400\"'$", _down, re.M) is not None and "snmp_agents" not in _down)
check("Spark の既定は logs（syslog-ng）と flows（GoFlow2）も読む", mod.LOG_TOPICS == "traps,logs,flows"
      and mod.sink_topics("opensearch", mod.METRIC_TOPICS, mod.LOG_TOPICS) == "traps,logs,flows")

# ---- gnmic（cycle 013）: event → Telegraf の形の読み替え（gnmic_message。read_rows の gnmic_struct と同じ表。test_analytics が gnmic_struct を見る）
# 入力の event はソースから組んだ形（gnmic v0.49.0 の event の形。実物ではない。design.md の未確定 1、AWS で実物と照合する = 検証 7）。実物は _ge_real（下）
_ge = {"name": "interface_state", "timestamp": 1700000000123456789,
       "tags": {"interface_name": "ethernet-1/1", "source": "203.0.113.31", "subscription-name": "interface_state"},
       "values": {"/srl_nokia-interfaces:interface/oper-state": "down"}}
_gm = mod.gnmic_message(_ge)
check("gnmic_message: interface_state の event → timestamp は秒（ns を切り捨て）、name は interface、tags の interface_name は ifName（source / subscription-name は残す）、"
      "values のキーは最後の要素で接頭辞を落とし - を _ に（oper_state）",
      _gm == {"timestamp": 1700000000, "name": "interface", "tags": {"ifName": "ethernet-1/1", "source": "203.0.113.31", "subscription-name": "interface_state"},
              "fields": {"oper_state": "down"}})


def _grec(m):   # gnmic_message の答えを read_rows → row_to_record のあとの形にする（agent_host の列は tags.source）
    return {"ts": float(m["timestamp"]), "topic": "gnmi", "measurement": m["name"], "agent_host": m["tags"].get("source", ""), "host": "", "tags": m["tags"], "fields": m["fields"]}


def _prom_text(series):   # prometheus_series の答えを Prometheus の字面（name{label="v",…} 値）にする
    return ["%s{%s} %g" % (dict(l)["__name__"], ",".join(f'{k}="{x}"' for k, x in l if k != "__name__"), v) for l, v, _ in series]


_gdm = mod.parse_device_map("203.0.113.31=dc1-a-leaf-01")
check("gnmic の interface_state を prometheus_series（device map 203.0.113.31 → dc1-a-leaf-01）に通すと snmp_interface_oper_up が 0 で、sysName が機器名（Grafana の link_down が読む）",
      _prom_text(mod.prometheus_series([_grec(_gm)], _gdm))
      == ['snmp_interface_oper_up{ifName="ethernet-1/1",source="203.0.113.31",subscription_name="interface_state",sysName="dc1-a-leaf-01"} 0'])
_gst = [mod.gnmic_message(dict(_ge, values={k: v})) for k, v in (("/interface/oper-state", "UP"), ("/srl_nokia-interfaces:interface/admin-state", "enable"),
                                                                    ("/interface/admin-state", "disable"))]
check("gnmic の oper-state / admin-state は snmp_interface_oper_up（up が 1。大文字小文字は見ない）と snmp_interface_admin_up（enable が 1、disable が 0）",
      [(dict(l)["__name__"], v) for l, v, _ in mod.prometheus_series([_grec(m) for m in _gst], _gdm)]
      == [("snmp_interface_oper_up", 1.0), ("snmp_interface_admin_up", 1.0), ("snmp_interface_admin_up", 0.0)])
_gstats = mod.gnmic_message({"name": "interface_stats", "timestamp": 1700000060000000000, "tags": {"interface_name": "ethernet-1/1", "source": "203.0.113.31"},
                             "values": {"/srl_nokia-interfaces:interface/statistics/in-octets": "123456789012", "/interface/statistics/out-error-packets": 3}})
_gsys = mod.gnmic_message({"name": "system", "timestamp": 1700000060000000000, "tags": {"control_slot": "A", "cpu_index": "all", "source": "203.0.113.31"},
                           "values": {"/srl_nokia-platform:platform/control/srl_nokia-platform-cpu:cpu/total/instant": "7",
                                      "/platform/control/memory/utilization": 41}})
check("gnmic の interface_stats / system（metrics トピック）: measurement は interface / system、control_slot は slot（cpu_index は表に無いので残す）、"
      "数は文字列のまま（64 bit の整数は json_ietf で文字列。数で来たら JSON の字面）",
      _gstats == {"timestamp": 1700000060, "name": "interface", "tags": {"ifName": "ethernet-1/1", "source": "203.0.113.31"},
                  "fields": {"in_octets": "123456789012", "out_error_packets": "3"}}
      and _gsys == {"timestamp": 1700000060, "name": "system", "tags": {"slot": "A", "cpu_index": "all", "source": "203.0.113.31"},
                    "fields": {"instant": "7", "utilization": "41"}})
check("gnmic の interface_stats / system を prometheus_series に通すと、ダッシュボードが読む系列名（snmp_interface_in_octets / out_error_packets、snmp_system_instant / utilization）",
      sorted((dict(l)["__name__"], v, dict(l)["sysName"]) for l, v, _ in mod.prometheus_series([_grec(_gstats), _grec(_gsys)], _gdm))
      == [("snmp_interface_in_octets", 123456789012.0, "dc1-a-leaf-01"), ("snmp_interface_out_error_packets", 3.0, "dc1-a-leaf-01"),
          ("snmp_system_instant", 7.0, "dc1-a-leaf-01"), ("snmp_system_utilization", 41.0, "dc1-a-leaf-01")])
check("gnmic_message: bgp_neighbor / isis_interface は name のまま、neighbor_peer-address は peer_address、interface_interface-name は interface_name"
      "（Grafana の bgp_down / isis_down と Splunk の nwc_gnmi が読む名前）。表に無いタグ（network-instance_name など）は残す",
      mod.gnmic_message({"name": "bgp_neighbor", "timestamp": 1, "tags": {"network-instance_name": "default", "neighbor_peer-address": "10.255.0.1", "source": "s"},
                         "values": {"/network-instance/protocols/bgp/neighbor/session-state": "established"}})
      == {"timestamp": 0, "name": "bgp_neighbor", "tags": {"network-instance_name": "default", "peer_address": "10.255.0.1", "source": "s"},
          "fields": {"session_state": "established"}}
      and mod.gnmic_message({"name": "isis_interface", "timestamp": 1, "tags": {"instance_name": "main", "interface_interface-name": "ethernet-1/1.0"},
                             "values": {"/srl_nokia-network-instance:network-instance/protocols/srl_nokia-isis:isis/instance/interface/oper-state": "up"}})["tags"]
      == {"instance_name": "main", "interface_name": "ethernet-1/1.0"})
check("gnmic_message: タグのキーの接頭辞（origin:）も落としてから表を引く。値の null は null のまま、入れ子は JSON の字面",
      mod.gnmic_message(dict(_ge, tags={"srl_nokia:interface_name": "ethernet-1/2"}, values={"/interface/description": None, "/interface/x": {"a": 1}}))
      == {"timestamp": 1700000000, "name": "interface", "tags": {"ifName": "ethernet-1/2"}, "fields": {"description": None, "x": '{"a":1}'}})
check("gnmic_message: 読み替えたキーが重なったら後勝ち（build の spark.sql.mapKeyDedupPolicy=LAST_WIN と同じ）",
      mod.gnmic_message(dict(_ge, tags={"interface_name": "a", "ifName": "b"}, values={"/interface/oper-state": "up", "/srl_nokia-interfaces:interface/oper-state": "down"}))
      == {"timestamp": 1700000000, "name": "interface", "tags": {"ifName": "b"}, "fields": {"oper_state": "down"}})
check("gnmic_message: 消えた event（deletes だけ）・values が dict でない・timestamp が整数でない（文字列、小数、真偽値、無い）は None（read_rows では ts が null で捨てる）",
      all(mod.gnmic_message(e) is None for e in (
          {"name": "interface_state", "timestamp": 1700000000123456789, "tags": _ge["tags"], "deletes": ["/interface/oper-state"]},
          dict(_ge, values=None), dict(_ge, values=["down"]), dict(_ge, timestamp="1700000000123456789"), dict(_ge, timestamp=1.7e18),
          dict(_ge, timestamp=True), {k: v for k, v in _ge.items() if k != "timestamp"})))
check("gnmic_message: 入力の event を書き換えない", _ge["tags"] == {"interface_name": "ethernet-1/1", "source": "203.0.113.31", "subscription-name": "interface_state"}
      and list(_ge["values"]) == ["/srl_nokia-interfaces:interface/oper-state"])
# cycle 025: 実物の event（2026-10-09 の AWS の metrics。docs/verification/20261009-aws-managed-2.md 不具合 1。Kafbat UI で取った字面のまま）。
# values も deletes も無い（400 件中 359 件がこの形）。read_rows がこれを Telegraf の行として読み、sinks が落ちていた
_ge_real = {"name": "interface_stats", "timestamp": 1791534762523125553, "tags": {"interface_name": "ethernet-1/7", "source": "203.0.113.11", "subscription-name": "interface_stats"}}
check("gnmic_message: values のキーそのものが無い実物の event（2026-10-09 の AWS）は None（捨てる）", mod.gnmic_message(_ge_real) is None)
check("gnmic_message: 実物の event に values を足すと、timestamp は秒（1791534762）、name は interface、interface_name は ifName、fields は in_octets",
      mod.gnmic_message(dict(_ge_real, values={"/srl_nokia-interfaces:interface/statistics/in-octets": "12345"}))
      == {"timestamp": 1791534762, "name": "interface", "tags": {"ifName": "ethernet-1/7", "source": "203.0.113.11", "subscription-name": "interface_stats"},
          "fields": {"in_octets": "12345"}})

# ---- gnmic（cycle 013）: gnmic.sh render が gnmic.yaml.in から作る設定（手元の sh で回す。イメージの中は alpine の sh。検証 5 は docker run）
import yaml   # PyYAML（uv の dev グループ。ops/check.sh が入れる）

GNMIC_DIR = os.path.join(ROOT, "app", "gnmic")
_GNMIC_ENV = {"GNMI_TARGETS": '"203.0.113.31:57400", "203.0.113.32:57400"', "KAFKA_BROKERS": "b-1.example:9096,b-2.example:9096",
              "GNMI_USERNAME": "fake-gnmi-user", "GNMI_PASSWORD": "fake-gnmi-pass", "KAFKA_SASL_USER": "fake-sasl-user", "KAFKA_SASL_PASS": "fake-sasl-pass"}


def _gnmic_render(**env):
    """gnmic.sh render を偽の環境変数で回す。(終了コード, 標準出力 + 標準エラー, 作った設定の字面 or None)"""
    with tempfile.TemporaryDirectory() as d:
        conf = os.path.join(d, "gnmic.yaml")
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GNMIC_TEMPLATE": os.path.join(GNMIC_DIR, "gnmic.yaml.in"), "GNMIC_CONF": conf}
        e.update({k: v for k, v in dict(_GNMIC_ENV, **env).items() if v is not None})
        p = subprocess.run(["sh", os.path.join(GNMIC_DIR, "gnmic.sh"), "render"], env=e, capture_output=True, text=True)
        text = open(conf, encoding="utf-8").read() if os.path.exists(conf) else None
        return p.returncode, p.stdout + p.stderr, text, sorted(os.listdir(d))


_rc, _out, _gtext, _gfiles = _gnmic_render()
_gy = yaml.safe_load(_gtext) if _gtext else {}
_GSUBS = {"interface_state": (["/interface[name=*]/oper-state", "/interface[name=*]/admin-state"], "on-change", "gnmi"),
          "interface_stats": (["/interface[name=*]/statistics"], "sample", "metrics"),
          "bgp_neighbor": (["/network-instance[name=default]/protocols/bgp/neighbor[peer-address=*]/session-state"], "on-change", "gnmi"),
          "isis_interface": (["/network-instance[name=default]/protocols/isis/instance[name=main]/interface[interface-name=*]/oper-state"], "on-change", "gnmi"),
          "system": (["/platform/control[slot=*]/cpu[index=all]/total", "/platform/control[slot=*]/memory"], "sample", "metrics")}
check("gnmic.sh render（KAFKA_AUTH=scram が既定）: 設定を作り、作業のファイル（.targets）を残さない。全体は json_ietf・skip-verify・port 57400",
      _rc == 0 and _gtext is not None and _gfiles == ["gnmic.yaml"] and "kafka auth: SASL/SCRAM-SHA-512" in _out
      and _gy.get("encoding") == "json_ietf" and _gy.get("skip-verify") is True and _gy.get("port") == 57400)
check("gnmic.yaml: subscription は 5 つだけ（evpn_es / mac_table / lab_* は無い）。パスとモードと出力のトピックは design.md の表のとおり、sample は 60s、on-change に heartbeat は無い",
      set(_gy.get("subscriptions", {})) == set(_GSUBS)
      and all(_gy["subscriptions"][n].get("paths") == p and _gy["subscriptions"][n].get("mode") == "stream" and _gy["subscriptions"][n].get("stream-mode") == sm
              and _gy["subscriptions"][n].get("outputs") == [o] and "heartbeat-interval" not in _gy["subscriptions"][n]
              and (_gy["subscriptions"][n].get("sample-interval") == "60s") == (sm == "sample")
              for n, (p, sm, o) in _GSUBS.items()))
check("gnmic.yaml: 出力は gnmi / metrics の 2 つ（Kafka、同じブローカー、format event、split-events）。scram では SASL/SCRAM-SHA-512 と TLS（イメージの CA の束）",
      set(_gy.get("outputs", {})) == {"gnmi", "metrics"}
      and all(o["type"] == "kafka" and o["address"] == _GNMIC_ENV["KAFKA_BROKERS"] and o["topic"] == n and o["format"] == "event" and o["split-events"] is True
              and o.get("sasl") == {"user": "${KAFKA_SASL_USER}", "password": "${KAFKA_SASL_PASS}", "mechanism": "SCRAM-SHA-512"}
              and o.get("tls") == {"ca-file": "/etc/ssl/certs/ca-certificates.crt"}
              for n, o in _gy.get("outputs", {}).items()))
check("gnmic.yaml: target の名前は IP（tags.source = device map のキー）、address は GNMI_TARGETS の host:port、資格情報は target ごとに ${…} のまま",
      _gy.get("targets") == {ip: {"address": f"{ip}:57400", "username": "${GNMI_USERNAME}", "password": "${GNMI_PASSWORD}"} for ip in ("203.0.113.31", "203.0.113.32")})
_gcode = "\n".join(l for l in _gtext.splitlines() if not l.lstrip().startswith("#"))   # コメントの行（ひな形の説明に __X__ がある）を除いたもの
check("gnmic.sh render: 資格情報の値を設定にも標準出力にも書かない。印の行（>>> / <<< kafka_auth）は消え、__X__ は全部埋まる",
      not any(v in _gtext + _out for k, v in _GNMIC_ENV.items() if k.startswith(("GNMI_USER", "GNMI_PASS", "KAFKA_SASL")))
      and "kafka_auth" not in _gtext and not re.search(r"__[A-Z_]+__", _gcode))
_rc_n, _out_n, _gtext_n, _ = _gnmic_render(KAFKA_AUTH="none", KAFKA_SASL_USER=None, KAFKA_SASL_PASS=None, KAFKA_BROKERS="kafka-0.nwc:9092")
_gy_n = yaml.safe_load(_gtext_n) if _gtext_n else {}
check("gnmic.sh render（KAFKA_AUTH=none。OSS 版と手元）: SCRAM の資格情報が無くても作れ、出力に sasl も tls も無い（ほかは scram と同じ）",
      _rc_n == 0 and "kafka auth: none" in _out_n and "sasl" not in _gtext_n and "tls" not in _gtext_n and "kafka_auth" not in _gtext_n
      and all(o["address"] == "kafka-0.nwc:9092" and set(o) == {"type", "address", "topic", "format", "split-events", "buffer-size", "timeout", "event-processors"}
              for o in _gy_n.get("outputs", {}).values())
      and _gy_n.get("subscriptions") == _gy.get("subscriptions") and _gy_n.get("targets") == _gy.get("targets"))
# cycle 030: 購読直後の初期同期（on-change の全状態）を producer（TLS + SCRAM）が出来るまで抱える（既定の buffer-size 0 / timeout 5s は黙って捨てる）。
# values の無い event（metrics の 9 割、deletes だけの event）は output 側の event-drop で捨てる。scram と none の両方で同じ
check("gnmic.yaml（cycle 030）: gnmi / metrics の両方が buffer-size 10000・timeout 60s・event-processors [drop-empty]（scram と none の両方）",
      all(set(y.get("outputs", {})) == {"gnmi", "metrics"}
          and all(o.get("buffer-size") == 10000 and o.get("timeout") == "60s" and o.get("event-processors") == ["drop-empty"] for o in y["outputs"].values())
          for y in (_gy, _gy_n)))
check("gnmic.yaml（cycle 030）: processor は drop-empty の 1 つだけで、event-drop の condition は values が null か空のとき（scram と none の両方）",
      all(y.get("processors") == {"drop-empty": {"event-drop": {"condition": ".values == null or (.values | length) == 0"}}} for y in (_gy, _gy_n)))
_gbad = {"GNMI_TARGETS が無い": dict(GNMI_TARGETS=None), "GNMI_TARGETS の形が違う": dict(GNMI_TARGETS="203.0.113.31:57400"),
         "GNMI_TARGETS に同じ IP が 2 回": dict(GNMI_TARGETS='"203.0.113.31:57400", "203.0.113.31:57401"'),
         "KAFKA_BROKERS の形が違う": dict(KAFKA_BROKERS="b-1.example:9096;rm"), "GNMI_PASSWORD が無い": dict(GNMI_PASSWORD=None),
         "scram で KAFKA_SASL_PASS が無い": dict(KAFKA_SASL_PASS=None), "KAFKA_AUTH が scram / none のどちらでもない": dict(KAFKA_AUTH="plain")}
_gbad_res = {k: _gnmic_render(**v) for k, v in _gbad.items()}
check("gnmic.sh render: " + " / ".join(_gbad) + " は 0 以外で止まり、設定を作らない（作業のファイルも残さない）",
      all(rc != 0 and text is None and files == [] for rc, _, text, files in _gbad_res.values()))
check("gnmic.sh render: 資格情報が無いときの案内に出どころ（SSM の SecureString / Secrets Manager の AmazonMSK_<接頭辞>-collectors）を書く",
      "SSM の SecureString" in _gbad_res["GNMI_PASSWORD が無い"][1] and "AmazonMSK_<接頭辞>-collectors" in _gbad_res["scram で KAFKA_SASL_PASS が無い"][1])
_gdock = _read("docker", "images", "gnmic", "Dockerfile")
check("gnmic の Dockerfile: 公式イメージ（版は ARG GNMIC_VERSION）に gnmic.yaml.in と gnmic.sh（/usr/local/bin/gn）を足し、nobody で gn run を起こす",
      re.search(r"^ARG GNMIC_VERSION=\d+\.\d+\.\d+$", _gdock, re.M) is not None and "FROM ghcr.io/openconfig/gnmic:${GNMIC_VERSION}\n" in _gdock
      and "COPY gnmic.yaml.in /etc/gnmic/\n" in _gdock and "COPY --chmod=0755 gnmic.sh /usr/local/bin/gn\n" in _gdock
      and "USER 65534:65534\n" in _gdock and 'ENTRYPOINT ["/usr/local/bin/gn"]' in _gdock and 'CMD ["run"]' in _gdock)
check("gnmic.sh の既定の置き場はイメージの COPY 先と同じ（/etc/gnmic/gnmic.yaml.in、書くのは /tmp）。run は render のあと gnmic を exec する",
      "TEMPLATE=${GNMIC_TEMPLATE:-/etc/gnmic/gnmic.yaml.in}" in _read("app", "gnmic", "gnmic.sh") and "CONF=${GNMIC_CONF:-/tmp/gnmic.yaml}" in _read("app", "gnmic", "gnmic.sh")
      and re.search(r'run\)\n\s*render\n\s*exec /app/gnmic --config "\$CONF" subscribe\n', _read("app", "gnmic", "gnmic.sh")) is not None)


def _gnmic_get(*paths):
    """gnmic.sh get を偽の gnmic（引数を 1 行ずつ出す）で回す。(終了コード, 標準出力の行, 標準エラー, 期待する --config 以下の頭)"""
    with tempfile.TemporaryDirectory() as d:
        fake, sh, conf = os.path.join(d, "gnmic"), os.path.join(d, "gn"), os.path.join(d, "gnmic.yaml")
        with open(fake, "w", encoding="utf-8") as f:
            f.write('#!/bin/sh\nfor a in "$@"; do echo "$a"; done\n')
        os.chmod(fake, 0o755)
        with open(sh, "w", encoding="utf-8") as f:
            f.write(_read("app", "gnmic", "gnmic.sh").replace("exec /app/gnmic ", f"exec {fake} "))
        e = dict(_GNMIC_ENV, PATH=os.environ.get("PATH", "/usr/bin:/bin"), GNMIC_TEMPLATE=os.path.join(GNMIC_DIR, "gnmic.yaml.in"), GNMIC_CONF=conf)
        p = subprocess.run(["sh", sh, "get", *paths], env=e, capture_output=True, text=True)
        return p.returncode, p.stdout.splitlines(), p.stderr, ["--config", conf, "get", "--type", "STATE", "--format", "event"]


_get_state = [p for k in ("interface_state", "bgp_neighbor", "isis_interface") for p in _GSUBS[k][0]]
_grc, _gout, _gerr, _ghead = _gnmic_get()
_grc2, _gout2, _gerr2, _ghead2 = _gnmic_get("/system/name", "/interface[name=mgmt0]/oper-state")
check("gnmic.sh get（gn get。stream の output gnmic_exec_command）: 設定を作り（render の出力は標準エラーへ）、gnmic get --type STATE --format event を 1 回打つ。"
      "パスを渡さなければ状態の 4 つ（subscribe の interface_state / bgp_neighbor / isis_interface と同じパス）、渡せばそれだけ。標準出力は gnmic の出力だけで、資格情報の値を出さない",
      len(_get_state) == 4 and _grc == 0 and _gout == _ghead + [a for p in _get_state for a in ("--path", p)]
      and _grc2 == 0 and _gout2 == _ghead2 + ["--path", "/system/name", "--path", "/interface[name=mgmt0]/oper-state"]
      and not any(v in _gerr + _gerr2 + "".join(_gout + _gout2) for v in ("fake-gnmi-pass", "fake-sasl-pass")))

# ---- Kafbat UI（IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf。2026-10-05）: MSK の画面。見るだけにせず、画面からトピックを足せる。stream を作る回はいつも作る。
# cycle 010（2026-10-08 のユーザー決定）から ECS のタスクと Cloud Map をやめ、Web の EC2（base/core）の Docker で動く。stream は接続先を SSM のパラメータに書き、
# Web のロールに Kafka の権限を足すだけ
kui =_read("IaC", "terraform", "aws-managed", "pipeline", "stream", "kafka_ui.tf")
kui_code = "\n".join(l for l in kui.splitlines() if not l.lstrip().startswith("#"))   # コメントを除いた中身
stream_vars = _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "variables.tf")
stream_out = _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "outputs.tf")
stream_locals = _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "locals.tf")
up = read_ops("up")
denv = _read("ops", "deploy-env.sh")
ecr_tf = _read("IaC", "terraform", "aws-managed", "base", "ecr", "main.tf")
web_tf = _read("IaC", "terraform", "aws-managed", "base", "core", "web.tf")
core_vars = _read("IaC", "terraform", "aws-managed", "base", "core", "variables.tf")
web_ud = _read("IaC", "terraform", "aws-managed", "base", "core", "templates", "web_user_data.sh.tftpl")
_kscript = web_ud[web_ud.index("<<'__KAFKA_UI__' || return 1\n"):web_ud.index("\n__KAFKA_UI__\n")]          # /usr/local/bin/<接頭辞>-kafka-ui
_kunit = web_ud[web_ud.index("<<__KAFKA_UI_UNIT__ || return 1\n"):web_ud.index("\n__KAFKA_UI_UNIT__\n") + 1]  # <接頭辞>-kafka-ui.service（最後の改行まで）
check("Kafbat UI の資源は stream の root の SSM の String 3 本（image / bootstrap-servers / security-protocol）と Web のロールへの Kafka の権限 1 つだけ。"
      "ECS・Cloud Map・ロググループ・ロールは持たず、切り替える変数も count も無い（cycle 010。2026-10-05 のユーザー決定でいつも作る）",
      re.findall(r'^resource "(\w+)" "(\w+)"', kui, re.M) == [("aws_ssm_parameter", "kafka_ui_image"), ("aws_ssm_parameter", "kafka_ui_bootstrap_servers"),
                                                             ("aws_ssm_parameter", "kafka_ui_security_protocol"), ("aws_iam_role_policy", "kafka_ui_web")]
      and re.findall(r'name\s*=\s*"\$\{local\.kafka_ui_parameter_prefix\}/([\w-]+)"', kui) == ["image", "bootstrap-servers", "security-protocol"]
      and len(re.findall(r'^  type\s*=\s*"String"$', kui, re.M)) == 3 and "SecureString" not in kui_code
      and 'kafka_ui_parameter_prefix   = "/${local.name_prefix}/kafka-ui"' in kui
      and 'kafka_ui_image              = "${try(data.terraform_remote_state.ecr.outputs.kafka_ui_repository_url, "")}:${var.kafka_ui_image_tag}"' in kui
      and all(w not in kui_code for w in ("aws_ecs", "service_discovery", "aws_cloudwatch_log_group", 'resource "aws_iam_role" ', "FARGATE", "count"))
      and all(w not in tf for w in ("create_kafka_ui", "service_discovery", "stream_service_namespace", "kafka_ui_sg_id", "kafka_ui_task", "kafka_cluster_name",
                                    "kafka_ui_service_name"))
      and "[0]" not in kui_code)
_kparams = re.findall(r"\$\(param ([\w-]+)", _kscript)
check("Web の EC2 のスクリプトが読むパラメータは stream が書く 3 本と up.sh が作る admin のパスワードで、名前（/<接頭辞>/kafka-ui/…）がそろう。Web のロールの ssm:GetParameter（/<接頭辞>/*）で読める",
      _kparams == ["image", "bootstrap-servers", "security-protocol", "admin-password"] and "PARAM=/${name_prefix}/kafka-ui\n" in _kscript
      and 'kafka_ui_password_parameter = "${local.kafka_ui_parameter_prefix}/admin-password"' in kui
      and 'ensure_secret "/$PREFIX/kafka-ui/admin-password" password ' in up
      and re.search(r'Action\s*=\s*"ssm:GetParameter"\s*\n\s*Resource\s*=\s*"arn:\$\{local\.partition\}:ssm:\$\{var\.region\}:\$\{local\.account_id\}:parameter/\$\{local\.name_prefix\}/\*"', web_tf) is not None)
check("admin のパスワードは --with-decryption で読み、/run（tmpfs）の env ファイル（umask 077）にだけ書いて docker に --env-file で渡す。止まったら消し（読めなかったときの AWS CLI のエラーも）、値を echo しない",
      "PASSWORD=$(param admin-password --with-decryption)" in _kscript and _kscript.count("--with-decryption") == 1
      and "ENV_FILE=/run/${name_prefix}-kafka-ui.env\n" in _kscript and _kscript.index("umask 077") < _kscript.index('} > "$ENV_FILE"')
      and '"SPRING_SECURITY_USER_PASSWORD=$PASSWORD"' in _kscript and '--env-file "$ENV_FILE"' in _kscript
      and "set -x" not in _kscript and not re.search(r"echo[^\n]*\$\{?(PASSWORD|SERVERS|IMAGE)", _kscript)
      and "ERR_FILE=/run/${name_prefix}-kafka-ui.err\n" in _kscript and _kscript.index("umask 077") < _kscript.index('2>"$ERR_FILE"')
      and "ExecStopPost=/bin/rm -f /run/${name_prefix}-kafka-ui.env /run/${name_prefix}-kafka-ui.err\n" in _kunit)
check("画面はログインフォーム（AUTH_TYPE=LOGIN_FORM、ユーザー admin）で、GitHub に版を聞きにいかない。READONLY は付けず、DYNAMIC_CONFIG_ENABLED は既定（false）のまま。JVM はメモリの半分まで",
      all(f'"{e}"' in _kscript for e in ("AUTH_TYPE=LOGIN_FORM", "SPRING_SECURITY_USER_NAME=admin", "GITHUB_RELEASE_INFO_ENABLED=false", "JAVA_OPTS=-XX:MaxRAMPercentage=50"))
      and all("READONLY" not in s and "DYNAMIC_CONFIG" not in s for s in (_kscript, kui_code)) and "READONLY" not in up)
check("MSK へは IAM 認証（SASL_SSL / AWS_MSK_IAM / IAMClientCallbackHandler / IAMLoginModule required;）で、認証情報は Web の EC2 のインスタンスロール"
      "（awsRoleArn も STS も使わない。Docker の bridge 越しに IMDSv2 へ届くよう hop limit 2）",
      re.search(r'variable "kafka_ui_security_protocol" \{[^}]*default\s*=\s*"SASL_SSL"', stream_vars) is not None
      and '"KAFKA_CLUSTERS_0_PROPERTIES_SASL_MECHANISM=AWS_MSK_IAM"' in _kscript
      and '"KAFKA_CLUSTERS_0_PROPERTIES_SASL_CLIENT_CALLBACK_HANDLER_CLASS=software.amazon.msk.auth.iam.IAMClientCallbackHandler"' in _kscript
      and '"KAFKA_CLUSTERS_0_PROPERTIES_SASL_JAAS_CONFIG=software.amazon.msk.auth.iam.IAMLoginModule required;"' in _kscript
      and "awsRoleArn" not in _kscript + kui_code and "sts:" not in kui_code
      and re.search(r"^\s*http_put_response_hop_limit\s*=\s*2$", web_tf, re.M) is not None
      and re.search(r'^\s*http_tokens\s*=\s*"required"$', web_tf, re.M) is not None)   # hop limit を広げても IMDSv1（トークン無しの GET）は開けない
check("認証の違いは kafka_ui_security_protocol だけで切り替わる（SASL_SSL は IAM のブートストラップ、PLAINTEXT は平文。パラメータに書き、Web の EC2 のスクリプトが SASL_SSL のときだけ SASL の 3 つを足す。cycle 005 の OSS 版で使い回す）",
      re.search(r'variable "kafka_ui_security_protocol" \{[\s\S]*?contains\(\["SASL_SSL", "PLAINTEXT"\], var\.kafka_ui_security_protocol\)', stream_vars) is not None
      and 'kafka_ui_bootstrap_servers = lookup(local.kafka_bootstrap_by_protocol, var.kafka_ui_security_protocol, "")' in kui
      and re.search(r"kafka_bootstrap_by_protocol = \{\s*SASL_SSL\s*=\s*aws_msk_cluster\.stream\.bootstrap_brokers_sasl_iam\s*"
                    r"PLAINTEXT\s*=\s*aws_msk_cluster\.stream\.bootstrap_brokers\s*\}", _msk) is not None
      and 'condition     = local.kafka_ui_bootstrap_servers != ""' in kui
      and "value       = local.kafka_ui_bootstrap_servers\n" in kui and "value       = var.kafka_ui_security_protocol\n" in kui
      and 'if [ "$PROTOCOL" = SASL_SSL ]; then' in _kscript and _kscript.count("SASL_") == 4
      and '"KAFKA_CLUSTERS_0_NAME=${name_prefix}"' in _kscript and '"KAFKA_CLUSTERS_0_PROPERTIES_SECURITY_PROTOCOL=$PROTOCOL"' in _kscript)
# Web のロールのポリシーは msk.tf の kafka_ui_kafka_statements をそのまま使う
_kpol = _msk_code[_msk_code.index("kafka_ui_kafka_statements = ["):_msk_code.index("kafka_descriptions = {")]
_kact = lambda sid: re.findall(r'"kafka-cluster:(\w+)"', re.search(r'Sid\s*=\s*"' + sid + r'"[\s\S]*?Resource', _kpol).group(0))
check("Web のロールに足すのは Kafbat UI が使う Kafka の操作だけ（クラスターの Connect / Describe、トピックの作成・変更・削除・設定の読み書き・データの読み書き、グループの Describe）。"
      "stream から Web のロールに足す（access.tf と同じ形）",
      _kact("KafkaCluster") == ["Connect", "DescribeCluster", "DescribeClusterDynamicConfiguration", "WriteDataIdempotently"]
      and _kact("KafkaTopics") == ["DescribeTopic", "CreateTopic", "AlterTopic", "DeleteTopic", "DescribeTopicDynamicConfiguration",
                                   "AlterTopicDynamicConfiguration", "ReadData", "WriteData"]
      and _kact("KafkaGroups") == ["DescribeGroup"]
      and "kafka-cluster:*" not in _kpol and all(a not in kui_code + _kpol for a in ("AlterCluster", "AlterGroup", "DeleteGroup"))
      and "Resource = aws_msk_cluster.stream.arn" in _kpol and "Resource = local.topic_arns" in _kpol and "Resource = local.group_arns" in _kpol
      and 'group_arns = "${replace(aws_msk_cluster.stream.arn, ":cluster/", ":group/")}/*"' in _msk
      and re.search(r'resource "aws_iam_role_policy" "kafka_ui_web" \{\n  name = "\$\{local\.name_prefix\}-kafka-ui"\n  role = local\.web_role_name\n\n  policy = jsonencode\(\{\n    Version   = "2012-10-17"\n    Statement = local\.kafka_ui_kafka_statements\n', kui) is not None
      and "web_role_name     = data.terraform_remote_state.main.outputs.web_role_name" in stream_locals
      and "kafka-cluster" not in kui_code)
check("閉域の Deny（perimeter）は base/core が Web のロールに付けたもので Kafbat UI にも効く（stream は付けない）",
      re.search(r'resource "aws_iam_role_policy_attachment" "web_perimeter" \{[^}]*role\s*=\s*aws_iam_role\.web\.name', _read("IaC", "terraform", "aws-managed", "base", "core", "perimeter.tf")) is not None
      and "perimeter" not in kui_code)
_api_clients = re.findall(r'"(\w+)"', re.search(r'aws_api_clients = \[([^\]]*)\]', core_sg).group(1))
check("SG は Web の EC2 のもの（kafka_ui の SG は無い）。通信の表に Web → MSK の 9098（IAM）があり、Web の EC2 の中の 127.0.0.1:8082 には SG の行が要らない。stream は SG を作らない",
      re.search(r"\bkafka_ui\b", core_sg) is None and "web" in _api_clients
      and re.search(r'\{ from = "web", to = "msk", protocol = "tcp", port = 9098, why = "[^"]*" \}', core_sg) is not None
      and "aws_security_group" not in tf)
check("Web の EC2 は t4g.medium（Gradio 約 400 MB + Kafbat UI の JVM）でルートボリューム 16 GB。ECR は <接頭辞>-kafka-ui だけ引ける（書けない）",
      re.search(r'variable "instance_type" \{[^}]*default\s*=\s*"t4g\.medium"', core_vars) is not None
      and re.search(r"^\s*volume_size\s*=\s*16$", web_tf, re.M) is not None
      and re.search(r'Action\s*=\s*"ecr:GetAuthorizationToken"\s*\n\s*Resource\s*=\s*"\*"', web_tf) is not None
      and re.search(r'Action\s*=\s*\["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"\]\s*\n\s*'
                    r'Resource\s*=\s*"arn:\$\{local\.partition\}:ecr:\$\{var\.region\}:\$\{local\.account_id\}:repository/\$\{local\.name_prefix\}-kafka-ui"', web_tf) is not None
      and not re.search(r'"ecr:(Put|Upload|Initiate|Complete|Delete)', web_tf))
check("Kafbat UI は Web の EC2 の systemd のユニットが Docker のコンテナを 127.0.0.1:8082 に出す（stream は base/core より後に作られる）。"
      "パラメータが無い（標準エラーに (ParameterNotFound)）と 75 で終わって起こし直さず（RestartPreventExitStatus。cycle 014）、それ以外で読めないと 69 で終わって 30 秒ごとに起こし直す。"
      "75 は成功の扱いにして failed に数えない（SuccessExitStatus。degraded にしない。cycle 026）",
      'exec docker run --rm --name ${name_prefix}-kafka-ui --env-file "$ENV_FILE" -p 127.0.0.1:8082:8080 "$IMAGE"' in _kscript
      and "\n  exit 75\n" in _kscript and "\n    exit 69\n" in _kscript and """  if ! grep -qF '(ParameterNotFound)' "$ERR_FILE"; then\n""" in _kscript
      and all(f"{v}=$(param {n}) || exit $?\n" in _kscript for v, n in (("IMAGE", "image"), ("SERVERS", "bootstrap-servers"), ("PROTOCOL", "security-protocol"),
                                                                     ("PASSWORD", "admin-password --with-decryption")))
      and all(l + "\n" in _kunit for l in ("After=docker.service network-online.target", "Wants=network-online.target", "Requires=docker.service",
                                           "ExecStart=/usr/local/bin/${name_prefix}-kafka-ui",
                                           "Restart=always", "RestartSec=30", "RestartPreventExitStatus=75", "SuccessExitStatus=75",
                                           "TimeoutStartSec=0", "WantedBy=multi-user.target"))
      and re.findall(r"^RestartPreventExitStatus=.*$", _kunit, re.M) == ["RestartPreventExitStatus=75"]
      and re.findall(r"^SuccessExitStatus=.*$", _kunit, re.M) == ["SuccessExitStatus=75"])
_wunit = web_ud[web_ud.index("<<__UNIT__\n"):web_ud.index("\n__UNIT__\n") + 1]  # <接頭辞>-web.service
check("75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8-3（OSS 版は 7-5）の systemctl restart <接頭辞>-web が起こす。"
      "弱い依存だけにして、Kafbat UI が落ちても Web を止めない（Requires / BindsTo / PartOf / Requisite にしない。cycle 014）",
      "Wants=network-online.target\nWants=${name_prefix}-kafka-ui.service\n" in _wunit
      and _wunit.index("Wants=${name_prefix}-kafka-ui.service") < _wunit.index("[Service]")
      and re.findall(r"^\w+=.*kafka-ui.*$", _wunit, re.M) == ["Wants=${name_prefix}-kafka-ui.service"])
# その restart は stream の apply より後で、stream を作る回はいつも通る: ops/up.sh の 8-3 の if に SKIP_STREAM が空の条件、OSS 版の 7-5 は if の外
_up_sh, _oss_up_sh = _read("ops", "up.sh"), _read("ops", "oss", "up.sh")
def _between(text, start, end):  # start から end の手前まで。どちらかが無ければ ""（.index() の ValueError ではなく、それを使う check の名前で落ちる。cycle 016）
    i = text.find(start)
    j = text.find(end, i + len(start)) if i >= 0 else -1
    return text[i:j] if j >= 0 else ""
_s83 = _between(_up_sh, "\n# ---- 8-3. Web ", "\n# ---- 8-5. ")
_s75 = _between(_oss_up_sh, '\nlog "7-5. ', "\n# ---- 8. workflow ")
# 8-3 は全体を 1 本で fullmatch する（cycle 023）: 見出しの次が字下げなしの if、本文は 2 スペース字下げの行（空行も可）だけで restart を含み、
# 字下げなしの fi で閉じて、あとは空行だけ。&& { で続ける形・字下げした if・関数で包む形は落ちる。_s83 は 8-5 の見出しの前の \n を含まないので、
# fi の直後に空行が無いと末尾は fi で切れる（fi\n\n* でなく fi\n*）
_s83_re = re.compile(
    r'\n# ---- 8-3\. Web -*\n'
    r'if \[ -z "\$SKIP_STREAM" \] \|\| [^\n]*; then\n'
    r'(?:(?:  [^\n]*)?\n)*?'
    r'  run_on_instance "\$INSTANCE_ID" "systemctl restart \$PREFIX-web\.service; \$WEB_ACTIVE"\n'
    r'(?:(?:  [^\n]*)?\n)*?'
    r'fi\n*')
check("Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z \"$SKIP_STREAM\" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）",
      -1 < _up_sh.find("\n  tf_apply pipeline/stream ") < _up_sh.find("\n# ---- 8-3. Web ")
      and _s83_re.fullmatch(_s83) is not None
      and -1 < _oss_up_sh.find('\ntf_apply pipeline/stream "${STREAM_VARS[@]}"\n') < _oss_up_sh.find('\nlog "7-5. ')
      and re.fullmatch(r'\nlog "7-5\. [^"\n]*"\n(?:[ \t]*(?:#[^\n]*)?\n)*run_on_instance "\$INSTANCE_ID" "systemctl restart \$PREFIX-web\.service; \$WEB_ACTIVE"(?:\n[ \t]*(?:#[^\n]*)?)*\n?', _s75) is not None
      and re.findall(r"^(?:if|fi)\b.*$", _between(_oss_up_sh, "\n# ---- 7-4c. ", "\n# ---- 8. workflow "), re.M)
          == ['if [ -n "$STORE_WARN" ]; then', "fi"])
check("Docker と Kafbat UI の節は、画面のコードの取得（aws s3 sync）より後、S3 に web/ が無くて exit 0 する所より前で、関数 kafka_ui_setup にまとめて"
      " 1 行ずつ || return 1 で繋ぎ、|| echo で呼ぶ（落ちても user_data を止めない。cycle 014）",
      web_ud.index("\naws s3 sync --delete ") < web_ud.index("\nkafka_ui_setup() {\n") < web_ud.index("\nkafka_ui_setup || echo ") < web_ud.index("\n  exit 0\n")
      and web_ud.count("kafka_ui_setup") == 2 and "command -v docker" not in web_ud[:web_ud.index("\nkafka_ui_setup() {\n")]
      and "\nkafka_ui_setup() {\n  command -v docker >/dev/null || dnf install -y docker || return 1\n  systemctl enable --now docker || return 1\n"
          "  cat > /usr/local/bin/${name_prefix}-kafka-ui <<'__KAFKA_UI__' || return 1\n" in web_ud
      and "\n__KAFKA_UI__\n  chmod 0755 /usr/local/bin/${name_prefix}-kafka-ui || return 1\n"
          "  cat > /etc/systemd/system/${name_prefix}-kafka-ui.service <<__KAFKA_UI_UNIT__ || return 1\n" in web_ud
      and "\n__KAFKA_UI_UNIT__\n  systemctl daemon-reload || return 1\n  systemctl enable --now ${name_prefix}-kafka-ui.service\n}\n" in web_ud
      and re.search(r'\nkafka_ui_setup \|\| echo "\$\{name_prefix\}-kafka-ui: setup failed [^"\n]*" >&2\n', web_ud) is not None
      and "logger" not in "\n".join(l for l in web_ud.splitlines() if not l.lstrip().startswith("#")))

# user_data を描いて（テンプレートの変数を仮置き）、シェルの部分を bash -n にかけ、Kafbat UI のスクリプトを偽の aws / docker で動かす
def _render_web_ud(graph_backend=""):
    s = web_ud.replace("${name_prefix}", "x-nwc-poc").replace("${region}", "ap-northeast-1").replace("${bucket}", "x-bucket")
    s = s.replace('%{ if graph_backend != "" }-oss%{ endif }', "-oss" if graph_backend else "")
    s = re.sub(r'%\{ if graph_backend != "" ~\}\n(.*?)%\{ endif ~\}\n', (lambda m: m.group(1)) if graph_backend else "", s, flags=re.S)
    return s.replace("${graph_backend}", graph_backend).replace("$${", "${")
_ud_rc = {}
for _gb in ("", "neo4j"):
    _sh = _render_web_ud(_gb)
    _sh = _sh[_sh.index("#!/bin/bash\n"):_sh.index("--==BOUNDARY==--")]
    _ud_rc[_gb] = (subprocess.run(["bash", "-n"], input=_sh, capture_output=True, text=True).returncode, "%{" in _sh, "${graph_backend}" in _sh)
check(f"user_data のシェルの部分は描いたあと bash -n が通る（マネージド版と OSS 版の両方。{_ud_rc}）", _ud_rc == {"": (0, False, False), "neo4j": (0, False, False)})

_kbin = tempfile.mkdtemp(prefix="kafka-ui-")
for _n, _body in (("aws", """#!/bin/bash
echo "aws $*" >> "$FAKE_LOG"
case "$1 $2" in
  "ssm get-parameter")
    name=""; for a in "$@"; do [ "$prev" = --name ] && name=$a; prev=$a; done
    var="FAKE_$(printf '%s' "${name##*/}" | tr 'a-z-' 'A-Z_')"
    # 読めないときの AWS CLI の形（aws-cli 2.36.34 を偽のエンドポイントに当てて実測。標準エラーは空行から始まる）。pnf-old は [ERROR] の前置きが無い古い版
    [ -n "${!var:-}" ] || case "${FAKE_SSM_ERROR:-pnf}" in
      pnf) printf '\\naws: [ERROR]: An error occurred (ParameterNotFound) when calling the GetParameter operation (reached max retries: 0): \\n' >&2; exit 254 ;;
      pnf-old) printf '\\nAn error occurred (ParameterNotFound) when calling the GetParameter operation: \\n' >&2; exit 254 ;;
      deny) printf '\\naws: [ERROR]: An error occurred (AccessDeniedException) when calling the GetParameter operation (reached max retries: 0): User: arn:aws:sts::123456789012:assumed-role/x-nwc-poc-web/i-0 is not authorized to perform: ssm:GetParameter on resource: arn:aws:ssm:ap-northeast-1:123456789012:parameter%s\\n' "$name" >&2; exit 254 ;;
      unreachable) printf '\\naws: [ERROR]: Could not connect to the endpoint URL: "https://ssm.ap-northeast-1.amazonaws.com/"\\n' >&2; exit 255 ;;
      nocreds) printf '\\naws: [ERROR]: An error occurred (NoCredentials): Unable to locate credentials. You can configure credentials by running "aws login".\\n' >&2; exit 253 ;;
    esac
    printf '%s\\n' "${!var}" ;;
  "ecr get-login-password") [ -z "${FAKE_ECR_FAIL:-}" ] || exit 255; echo ecr-token ;;
  *) exit 2 ;;
esac
"""), ("docker", """#!/bin/bash
echo "docker $*" >> "$FAKE_LOG"
if [ "$1" = login ]; then cat > "$FAKE_LOG.stdin"; fi
[ "$1" != "${FAKE_DOCKER_FAIL:-}" ] || { echo "docker $1 failed" >&2; exit 1; }   # FAKE_DOCKER_FAIL=pull なら pull が落ちる
""")):
    with open(os.path.join(_kbin, _n), "w") as f:
        f.write(_body)
    os.chmod(os.path.join(_kbin, _n), 0o755)
_krendered = _render_web_ud()
# 上の _wunit（描く前のテンプレート）の Wants= の検査の、描いたあとの側（${name_prefix} が描けて、Web のユニットに残っていること）
_kwunit = _between(_krendered, "cat > /etc/systemd/system/x-nwc-poc-web.service <<__UNIT__\n", "\n__UNIT__\n")
check("描いた user_data の Web のユニットにも Wants=x-nwc-poc-kafka-ui.service があり、kafka-ui を含む行はその 1 行だけ（cycle 016）",
      "\nWants=x-nwc-poc-kafka-ui.service\n" in _kwunit and [l for l in _kwunit.splitlines() if "kafka-ui" in l] == ["Wants=x-nwc-poc-kafka-ui.service"])
# user_data のうちスクリプトを書く所（cat > … <<'__KAFKA_UI__' からユニットを書く手前まで。chmod を含む）を、置き場所だけ差し替えてそのまま打つ
_kwrite = _krendered[_krendered.index("cat > /usr/local/bin/x-nwc-poc-kafka-ui <<'__KAFKA_UI__' || return 1\n"):_krendered.index("cat > /etc/systemd/system/x-nwc-poc-kafka-ui.service")]
_krun_src = _krendered[_krendered.index("<<'__KAFKA_UI__' || return 1\n") + len("<<'__KAFKA_UI__' || return 1\n"):_krendered.index("\n__KAFKA_UI__\n")]
_kmodes = []
def _krun(**params):
    d = tempfile.mkdtemp(prefix="kafka-ui-run-", dir=_kbin)
    env_file, err_file, log = os.path.join(d, "kafka-ui.env"), os.path.join(d, "kafka-ui.err"), os.path.join(d, "log")
    script = os.path.join(d, "kafka-ui")
    w = subprocess.run(["bash", "-c", "umask 022\n" + _kwrite.replace("/usr/local/bin/x-nwc-poc-kafka-ui", script)
                        .replace("ENV_FILE=/run/x-nwc-poc-kafka-ui.env\n", f"ENV_FILE={env_file}\n")
                        .replace("ERR_FILE=/run/x-nwc-poc-kafka-ui.err\n", f"ERR_FILE={err_file}\n")], capture_output=True, text=True)
    _kmodes.append((w.returncode, oct(os.stat(script).st_mode & 0o777) if os.path.exists(script) else None))
    env = {"PATH": _kbin + os.pathsep + os.environ["PATH"], "FAKE_LOG": log}
    env.update({"FAKE_" + k.upper(): v for k, v in params.items()})
    # systemd と同じく、bash を挟まずにスクリプトを直に起こす（shebang と実行権が要る）
    try:
        p = subprocess.run([script], env=env, capture_output=True, text=True)
    except OSError as e:
        p = subprocess.CompletedProcess([script], f"exec failed: {e}", "", "")
    read = lambda q: open(q).read() if os.path.exists(q) else None
    return p, read(env_file), (oct(os.stat(env_file).st_mode & 0o777) if os.path.exists(env_file) else None), (read(log) or "").splitlines(), read(log + ".stdin")
_KIMG = "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/x-nwc-poc-kafka-ui:v1.5.0"
_KCOMMON = dict(image=_KIMG, admin_password="pw-Secret_1")
_p, _env, _mode, _log, _stdin = _krun(bootstrap_servers="b-1:9098,b-2:9098", security_protocol="SASL_SSL", **_KCOMMON)
_env_file = [l for l in _log if l.startswith("docker run ")][0].split("--env-file ")[1].split(" ")[0] if _log else ""
check("SASL_SSL: env は名前・ブートストラップ・SASL の 3 つ・ログインフォーム・admin のパスワード・JAVA_OPTS（0600）。ECR にログインしてから pull し、古いコンテナを消して 127.0.0.1:8082 で動かす。値は標準出力にもエラーにも出ない",
      _p.returncode == 0 and _mode == "0o600" and _env.splitlines() == [
          "KAFKA_CLUSTERS_0_NAME=x-nwc-poc", "KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS=b-1:9098,b-2:9098", "KAFKA_CLUSTERS_0_PROPERTIES_SECURITY_PROTOCOL=SASL_SSL",
          "KAFKA_CLUSTERS_0_PROPERTIES_SASL_MECHANISM=AWS_MSK_IAM",
          "KAFKA_CLUSTERS_0_PROPERTIES_SASL_CLIENT_CALLBACK_HANDLER_CLASS=software.amazon.msk.auth.iam.IAMClientCallbackHandler",
          "KAFKA_CLUSTERS_0_PROPERTIES_SASL_JAAS_CONFIG=software.amazon.msk.auth.iam.IAMLoginModule required;",
          "AUTH_TYPE=LOGIN_FORM", "SPRING_SECURITY_USER_NAME=admin", "SPRING_SECURITY_USER_PASSWORD=pw-Secret_1",
          "GITHUB_RELEASE_INFO_ENABLED=false", "JAVA_OPTS=-XX:MaxRAMPercentage=50"]
      and [l for l in _log if l.startswith("docker ")] == [
          "docker login --username AWS --password-stdin 123456789012.dkr.ecr.ap-northeast-1.amazonaws.com", f"docker pull --quiet {_KIMG}",
          "docker rm --force x-nwc-poc-kafka-ui", f"docker run --rm --name x-nwc-poc-kafka-ui --env-file {_env_file} -p 127.0.0.1:8082:8080 {_KIMG}"]
      and _stdin == "ecr-token\n" and "aws ecr get-login-password --region ap-northeast-1" in _log
      and [l for l in _log if "--with-decryption" in l] == ["aws ssm get-parameter --region ap-northeast-1 --name /x-nwc-poc/kafka-ui/admin-password --query Parameter.Value --output text --with-decryption"]
      and all(v not in _p.stdout + _p.stderr for v in ("pw-Secret_1", "b-1:9098")))
_p, _env, _mode, _log, _ = _krun(bootstrap_servers="kafka-1.x:9092", security_protocol="PLAINTEXT", **_KCOMMON)
check("PLAINTEXT（OSS 版の Kafka）: SASL の行を書かない", _p.returncode == 0 and "SASL" not in _env
      and "KAFKA_CLUSTERS_0_PROPERTIES_SECURITY_PROTOCOL=PLAINTEXT" in _env.splitlines() and "SPRING_SECURITY_USER_PASSWORD=pw-Secret_1" in _env.splitlines())
_KLEAVES = ("image", "bootstrap_servers", "security_protocol", "admin_password")  # スクリプトが読む順
def _kunreadable(leaf, **ps):
    """leaf のパラメータだけ読めない（FAKE_SSM_ERROR の形で落ちる）ときの (終了コード, env, docker の呼び出し, 読んだ数, 値が出たか, 標準エラー)"""
    _ps = dict(_KCOMMON, bootstrap_servers="b-1:9098", security_protocol="SASL_SSL")
    del _ps[leaf]
    _p, _env, _, _log, _ = _krun(**_ps, **ps)
    return (_p.returncode, _env, [l for l in _log if l.startswith("docker ")], len([l for l in _log if l.startswith("aws ssm ")]),
            "pw-Secret_1" in _p.stdout + _p.stderr or "b-1:9098" in _p.stdout + _p.stderr, _p.stderr)
_kmiss = {}
for _leaf in _KLEAVES:
    for _style in ("pnf", "pnf-old"):
        *_r, _err = _kunreadable(_leaf, ssm_error=_style)
        _kmiss[_leaf, _style] = (*_r, _err.count("\n") == 1 and f"/x-nwc-poc/kafka-ui/{_leaf.replace('_', '-')} does not exist" in _err
                                 and "Not retrying" in _err and "'sudo systemctl start x-nwc-poc-kafka-ui'" in _err and "Retrying in 30 s" not in _err)
check(f"パラメータが無い（ParameterNotFound。stream がまだ無い・SKIP_STREAM=1）と、どれか 1 つでもそこで 75 で終わり（systemd は起こし直さない）、"
      f"env も書かず docker も呼ばず、後ろのパラメータを読まない。標準エラーは 1 行で「無い・再試行しない・起こし方」（古い AWS CLI の形でも同じ。{_kmiss}）",
      _kmiss == {(l, st): (75, None, [], i + 1, False, True) for i, l in enumerate(_KLEAVES) for st in ("pnf", "pnf-old")})
_kerr = {}
for _leaf in _KLEAVES:
    for _style, _aws in (("deny", "(AccessDeniedException)"), ("unreachable", "Could not connect to the endpoint URL"), ("nocreds", "(NoCredentials)")):
        *_r, _err = _kunreadable(_leaf, ssm_error=_style)
        _kerr[_leaf, _style] = (*_r, _err.count("\n") == 1 and f"Cannot read /x-nwc-poc/kafka-ui/{_leaf.replace('_', '-')} (not a missing parameter" in _err
                                and "Retrying in 30 s. AWS CLI: aws: [ERROR]: " in _err and _aws in _err and "Not retrying" not in _err)
check(f"ParameterNotFound 以外で読めない（AccessDenied 254・エンドポイント不達 255・認証情報がまだ無い 253）と 69 で終わり（systemd が 30 秒後に起こし直す）、"
      f"「stream が無い」とは言わずに AWS CLI のエラー文を 1 行に添える。env も書かず docker も呼ばない（{_kerr}）",
      _kerr == {(l, st): (69, None, [], i + 1, False, True) for i, l in enumerate(_KLEAVES) for st in ("deny", "unreachable", "nocreds")})
_kfail = {}
for _what, _ps in (("ECR のログイン", dict(ecr_fail="1")), ("pull", dict(docker_fail="pull")), ("login", dict(docker_fail="login"))):
    _p, _, _, _log, _ = _krun(bootstrap_servers="b-1:9098", security_protocol="SASL_SSL", **_KCOMMON, **_ps)
    _kfail[_what] = (_p.returncode != 0, [l.split()[1] for l in _log if l.startswith("docker ")])
check(f"ECR のログイン（aws ecr get-login-password と docker login のどちらか）や pull が落ちたら、古いイメージのまま docker run せずに非 0 で終わる（set -euo pipefail。systemd が 30 秒後に起こし直す。{_kfail}）",
      _kfail == {"ECR のログイン": (True, ["login"]), "pull": (True, ["login", "pull"]), "login": (True, ["login"])})
check(f"user_data はスクリプトを #!/bin/bash と set -euo pipefail で始めて 0755 にし、systemd のように直に起こせる（書いた結果と mode: {sorted(set(_kmodes))}）",
      _krun_src.startswith("#!/bin/bash\nset -euo pipefail\n") and set(_kmodes) == {(0, "0o755")})
# Docker と Kafbat UI の節（関数 kafka_ui_setup と、それを || echo で呼ぶ行）を set -euo pipefail の下で打ち、どの段で落ちても user_data が先へ進むことを見る。
# PATH には cat と偽の dnf / systemctl / chmod（と、docker がある場合の偽の docker）だけを置く
_ksetup = _krendered[_krendered.index("\nkafka_ui_setup() {\n") + 1:]
_ksetup = _ksetup[:_ksetup.index("\n", _ksetup.index("\nkafka_ui_setup || echo ") + 1) + 1]
_ksbin = tempfile.mkdtemp(prefix="kafka-ui-setup-", dir=_kbin)
os.symlink(shutil.which("cat"), os.path.join(_ksbin, "cat"))
for _n, _body in (("dnf", '[ "$FAIL" != dnf ] || exit 1\n'),
                  ("chmod", '[ "$FAIL" != chmod ] || exit 1\nexec ' + shutil.which("chmod") + ' "$@"\n'),
                  ("systemctl", 'case "$*" in "enable --now docker") k=enable-docker ;; daemon-reload) k=daemon-reload ;; '
                                '"enable --now x-nwc-poc-kafka-ui.service") k=enable-kafka-ui ;; *) k=other ;; esac\n[ "$FAIL" != "$k" ] || exit 1\n')):
    with open(os.path.join(_ksbin, _n), "w") as f:
        f.write(f'#!/bin/bash\necho "{_n} $*" >> "$FAKE_LOG"\n' + _body)
    os.chmod(os.path.join(_ksbin, _n), 0o755)
def _ksetup_run(fail="", docker=False, bad_dir=""):
    d = tempfile.mkdtemp(prefix="run-", dir=_ksbin)
    bindir = os.path.join(d, "bin")
    os.mkdir(bindir)
    if docker:
        with open(os.path.join(bindir, "docker"), "w") as f:
            f.write("#!/bin/bash\nexit 0\n")
        os.chmod(os.path.join(bindir, "docker"), 0o755)
    script, unit, log = os.path.join(d, "kafka-ui"), os.path.join(d, "kafka-ui.service"), os.path.join(d, "log")
    if bad_dir == "script":
        script = os.path.join(d, "missing", "kafka-ui")
    if bad_dir == "unit":
        unit = os.path.join(d, "missing", "kafka-ui.service")
    src = _ksetup.replace("/usr/local/bin/x-nwc-poc-kafka-ui", script).replace("/etc/systemd/system/x-nwc-poc-kafka-ui.service", unit)
    p = subprocess.run(["/bin/bash", "-c", "set -euo pipefail\n" + src + "echo REACHED\n"], capture_output=True, text=True,
                       env={"PATH": bindir + os.pathsep + _ksbin, "FAKE_LOG": log, "FAIL": fail})
    calls = [l.split(" ", 1)[0] if l.startswith(("dnf", "chmod")) else l for l in (open(log).read().splitlines() if os.path.exists(log) else [])]
    return (p.returncode, p.stdout, "x-nwc-poc-kafka-ui: setup failed" in p.stderr, calls,
            os.path.exists(script), os.path.exists(unit))
_KSTEPS = ["dnf", "systemctl enable --now docker", "chmod", "systemctl daemon-reload", "systemctl enable --now x-nwc-poc-kafka-ui.service"]
_ksres = {"ok": _ksetup_run(), "ok-docker": _ksetup_run(docker=True)}
for _fail, _n in (("dnf", 1), ("enable-docker", 2), ("chmod", 3), ("daemon-reload", 4), ("enable-kafka-ui", 5)):
    _ksres[_fail] = _ksetup_run(fail=_fail)
_ksres["script-write"] = _ksetup_run(bad_dir="script")
_ksres["unit-write"] = _ksetup_run(bad_dir="unit")
check(f"Docker と Kafbat UI の節は、どの段（dnf・docker の起動・スクリプトの書き込み・chmod・ユニットの書き込み・daemon-reload・enable）で落ちても、"
      f"そこで止めて後ろの段を打たず、「setup failed」を 1 行出して user_data の先（Gradio）へ進む。docker があれば dnf を呼ばない（{_ksres}）",
      _ksres == {"ok": (0, "REACHED\n", False, _KSTEPS, True, True),
                 "ok-docker": (0, "REACHED\n", False, _KSTEPS[1:], True, True),
                 "dnf": (0, "REACHED\n", True, _KSTEPS[:1], False, False),
                 "enable-docker": (0, "REACHED\n", True, _KSTEPS[:2], False, False),
                 "chmod": (0, "REACHED\n", True, _KSTEPS[:3], True, False),
                 "daemon-reload": (0, "REACHED\n", True, _KSTEPS[:4], True, True),
                 "enable-kafka-ui": (0, "REACHED\n", True, _KSTEPS, True, True),
                 "script-write": (0, "REACHED\n", True, _KSTEPS[:2], False, False),
                 "unit-write": (0, "REACHED\n", True, _KSTEPS[:3], True, False)})
shutil.rmtree(_kbin)

check("開き方は Web の EC2 の 127.0.0.1:8082 への SSM のポートフォワード（AWS-StartPortForwardingSession。ToRemoteHost ではない）。PC 側も 8082（Web 8080 と Nautobot 8081 とぶつけない）",
      'value       = "aws ssm start-session --region ${var.region} --target ${local.web_instance_id} --document-name AWS-StartPortForwardingSession --parameters portNumber=8082,localPortNumber=8082"' in stream_out
      and "ToRemoteHost" not in stream_out + kui
      and 'web_instance_id   = try(data.terraform_remote_state.main.outputs.web_instance_id, "")' in stream_locals
      and re.search(r'\nif \[ -z "\$SKIP_STREAM" \]; then\n  echo "Kafbat UI（http://localhost:8082/[^\n]*Web の EC2 の Docker[^\n]*\n  tf pipeline/stream output -raw kafka_ui_port_forward_command; echo\n'
                    r'  tf pipeline/stream output -raw kafka_ui_password_command; echo\nfi\n', up) is not None)
_kui_tag = re.search(r'^KAFKA_UI_TAG=(\S+)$', up, re.M)
check("イメージは ghcr.io/kafbat/kafka-ui を ECR の <接頭辞>-kafka-ui に写す。版は固定で、up.sh の KAFKA_UI_TAG と kafka_ui_image_tag の既定が同じ",
      _kui_tag is not None and _kui_tag.group(1) not in ("latest", "main") and re.match(r"^v\d+\.\d+\.\d+$", _kui_tag.group(1))
      and re.search(r'variable "kafka_ui_image_tag" \{[^}]*default\s*=\s*"' + re.escape(_kui_tag.group(1)) + '"', stream_vars) is not None
      and '"kafka-ui"' in re.search(r'pipeline_repositories = toset\(\[([^\]]*)\]\)', ecr_tf).group(1)
      and 'mirror_image "ghcr.io/kafbat/kafka-ui:$KAFKA_UI_TAG" "$REG/$PREFIX-kafka-ui:$KAFKA_UI_TAG"' in up
      and 'ecr_has "$PREFIX-kafka-ui" "$KAFKA_UI_TAG"' in up and '"kafka_ui_image_tag=$KAFKA_UI_TAG"' in up)
check("Web の EC2 の Kafbat UI が呼ぶ AWS の API（ECR・SSM）と、syslog-ng と GoFlow2 の SCRAM の secret（Secrets Manager。cycle 012）は、stream を作る回のエンドポイントで足りる（イメージの層は S3 のゲートウェイ）",
      "pipeline/stream) add_endpoints ecr.api ecr.dkr logs secretsmanager ;;" in up and "add_endpoints ssm ssmmessages" in up)
# stream を作る回だけ（if [ -z "$SKIP_STREAM" ] の中）にあるか。その if より後ろで、間に閉じる fi が無い
def _in_stream_block(marker):
    i = up.index(marker)
    j = up.rindex('\nif [ -z "$SKIP_STREAM" ]; then\n', 0, i)
    return "\nfi\n" not in up[j:i]
# KAFKA_UI というキー（と作る・作らないの変数 create_kafka_ui）がどこにも無い: ops/ のシェル・deploy.env.example・IaC/terraform/aws-managed/ の .tf
_no_switch_files = ["deploy.env.example"] + [os.path.join("ops", n) for n in sorted(os.listdir(os.path.join(ROOT, "ops"))) if n.endswith(".sh")] + [
    os.path.relpath(os.path.join(d, n), ROOT) for d, _, ns in os.walk(os.path.join(ROOT, "IaC", "terraform", "aws-managed")) if ".terraform" not in d for n in ns if n.endswith(".tf")]
_has_switch = [p for p in _no_switch_files if re.search(r"\bKAFKA_UI\b|create_kafka_ui", _read(p))]
check(f"スイッチは無い: KAFKA_UI というキーと create_kafka_ui は ops/ と deploy.env.example と IaC/terraform/aws-managed/ のどこにも無い（{_has_switch}）。"
      "up.sh の冒頭と deploy.env.example は「いつも作る」と「Web の EC2 に同居」だけで、Kafbat UI の分の費用（Fargate の $0.02/h）はもう書かない",
      len(_no_switch_files) > 20 and "ops/deploy-env.sh" in _no_switch_files and _has_switch == []
      and re.search(r"^#   （Kafbat UI）\s+stream を作る回は Kafbat UI[^\n]*\*\*いつも作る\*\*（切り替える変数は無い[^\n]*Web の EC2 の Docker[^\n]*\n#   （", up, re.M) is not None
      and "Fargate" not in [l for l in up.splitlines() if l.startswith("#   （Kafbat UI）")][0] and "Kafbat UI（約 $0.02/h）" not in _read("deploy.env.example")
      and "Kafbat UI は Web の EC2 に同居（追加の費用は t4g.small → t4g.medium の差 約 $0.02/h）" in _read("deploy.env.example"))
check("stream を作る回はいつも作る: イメージを ECR に写し、パスワードを SSM に作り、stream の apply に版を渡し、最後に開き方を出す（どれも SKIP_STREAM が空の if の中）",
      _in_stream_block('  if ecr_has "$PREFIX-kafka-ui" "$KAFKA_UI_TAG"; then')
      and _in_stream_block('  ensure_secret "/$PREFIX/kafka-ui/admin-password" password ')
      and _in_stream_block('  tf_apply pipeline/stream ')
      and up.index('ensure_secret "/$PREFIX/kafka-ui/admin-password"') < up.index("  tf_apply pipeline/stream ")
      and '-var "kafka_ui_image_tag=$KAFKA_UI_TAG"' in up[up.index("  tf_apply pipeline/stream "):].split("\n", 1)[0]
      and _in_stream_block('  echo "Kafbat UI（http://localhost:8082/'))
# ---- MSK の SCRAM の secret と KMS の鍵（cycle 012）。値は ops/up.sh が作り（Terraform の state に入れない）、msk.tf は同じ名前の data source で引く
_msk_tf = _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "msk.tf")
_upc, _downc = _read("ops", "up-common.sh"), _read("ops", "down-common.sh")
_up_sh, _down_sh, _oss_up, _oss_down = _read("ops", "up.sh"), _read("ops", "down.sh"), _read("ops", "oss", "up.sh"), _read("ops", "oss", "down.sh")
check("SCRAM の secret と鍵の名前は、ops/up-common.sh（作る）・ops/down-common.sh（消す）・msk.tf（data source で引く）で同じ（接頭辞は owner-nwc-poc）",
      'data "aws_secretsmanager_secret" "msk_scram" {\n  name = "AmazonMSK_${local.name_prefix}-collectors"\n}' in _msk_tf
      and 'data "aws_kms_alias" "msk_scram" {\n  name = "alias/${local.name_prefix}-msk-scram"\n}' in _msk_tf
      and 'local alias="alias/$PREFIX-msk-scram" out arn state' in _upc and 'local name="AmazonMSK_$PREFIX-collectors" out kms deleted' in _upc
      and 'local name="AmazonMSK_$PREFIX-collectors" alias="alias/$PREFIX-msk-scram" out arn state' in _downc
      and 'name_prefix = "${var.owner}-${var.project}"' in _read("IaC", "terraform", "aws-managed", "pipeline", "stream", "locals.tf")
      and 'PREFIX="$OWNER-${1:-nwc-poc}"' in _read("ops", "deploy-env.sh"))
_sm_read = [f for f in ([os.path.join("ops", n) for n in sorted(os.listdir(os.path.join(ROOT, "ops"))) if n.endswith(".sh")]
                        + [os.path.join("ops", "oss", n) for n in sorted(os.listdir(os.path.join(ROOT, "ops", "oss"))) if n.endswith(".sh")])
            if re.search(r"get-secret-value|batch-get-secret-value", "\n".join(l for l in _read(f).splitlines() if not l.lstrip().startswith("#")))]
check(f"ops/ と ops/oss/ のシェルは Secrets Manager の secret の中身を読まない（get-secret-value / batch-get-secret-value を打たない。{_sm_read}）", _sm_read == [])
check("ops/up.sh は stream を作る回だけ、鍵 → secret → stream の apply の順に呼ぶ（msk.tf の data source が apply の時に引く）",
      _in_stream_block("\n  ensure_msk_scram_key\n")
      and "\n  ensure_msk_scram_key\n  ensure_msk_scram_secret\n  tf_apply pipeline/stream " in _up_sh
      and _up_sh.count("ensure_msk_scram_key") == 1 and _up_sh.count("ensure_msk_scram_secret") == 1)
check("ops/down.sh は 5-3. で delete_msk_scram を呼ぶ（destroy と SSM のパラメータのあと、残りの一覧の前）。OSS 版（MSK が無い）の up.sh / down.sh は呼ばない",
      re.search(r"^delete_up_ssm_params\n(?:.*\n)*?^delete_msk_scram\n(?:.*\n)*?^report_leftovers$", _down_sh, re.M) is not None
      and _down_sh.index("\ndestroy_root pipeline/stream ") < _down_sh.index("\ndelete_msk_scram\n")
      and not re.search(r"msk_scram", _oss_up + _oss_down))
_lab_flow = re.search(r'^\s*for p in ([\d ]+); do\n\s*iptables -t nat -I PREROUTING 1 -s "\$MGMT" -d "\$MGMT_GW" -p udp --dport "\$p" "\$\{c\[@\]\}" -j DNAT --to-destination "\$t:\$p"$', labsh, re.M)
check("lab.sh forward の NetFlow / sFlow の DNAT のポートは、NLB の受け口（collector_listeners）の netflow / sflow と同じ（cycle 012）",
      _lab_flow is not None and sorted(map(int, _lab_flow.group(1).split())) == sorted(_cl[k][0] for k in ("netflow", "sflow")))
check("ops/down.sh は Kafbat UI のパスワード（ManagedBy=ops/up.sh のタグ）も消す。stream の destroy に Kafbat UI の変数は要らない",
      "Tags" in up[up.index("ensure_secret() {"):up.index("ensure_secret() {") + 2500] and "Key=tag:ManagedBy,Values=$OPS_DIR/up.sh" in read_ops("down") and 'OPS_DIR="${OPS_DIR:-ops}"' in _read("ops", "common.sh")
      and "kafka_ui" not in read_ops("down"))

print(f"通過 {passed} / 失敗 0")
