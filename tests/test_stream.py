"""取り込みの経路（lab の機器 → Telegraf（ECS）→ MSK → Spark）の模擬テスト。lab の機器の設定・Telegraf の設定・terraform/pipeline/stream と、
Spark（spark/snmp_sinks.py）が異常の検知をしなくなったこと（2026-10-02。検知は Grafana のアラートルールと Splunk の保存済みサーチ → tests/test_alerts.py）を確かめる。
実行は python3 tests/test_stream.py（pyspark も boto3 も要らない。snmp_sinks.py は pyspark を関数の中で import する）。"""
import importlib.util, ipaddress, json, os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "spark", "snmp_sinks.py")

passed = 0
def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)

# ---- terraform/pipeline/stream: 検知の資源（detector Lambda・DynamoDB の異常テーブル）は持たない
TF_DIR = os.path.join(ROOT, "terraform", "pipeline", "stream")
tf = ""
for name in sorted(os.listdir(TF_DIR)):
    if name.endswith(".tf"):
        with open(os.path.join(TF_DIR, name), encoding="utf-8") as f:
            tf += f.read() + "\n"
check("stream/detector.py は無い（検知は Grafana と Splunk）", not os.path.exists(os.path.join(ROOT, "stream", "detector.py")))
check("terraform/pipeline/stream に detector の Lambda が無い", '"detector"' not in tf and "stream/detector.py" not in tf and "archive_file" not in tf)
check("terraform/pipeline/stream に lambda のエンドポイントが無い", '.lambda"' not in tf)
check("terraform/pipeline/stream に MSK Connect の S3 sink が無い（Spark が S3 Tables に入れるので 2026-09-26 に削除。sts のエンドポイントも一緒に）",
      not os.path.exists(os.path.join(TF_DIR, "sink.tf")) and "mskconnect" not in tf and "create_s3_sink" not in tf
      and "kafkaconnect" not in tf and "create_sts_endpoint" not in tf and "aws_vpc_endpoint" not in tf)
check("terraform/pipeline/stream に DynamoDB が無い（2026-09-24）",
      "aws_dynamodb" not in tf and "anomaly_table" not in tf and "dynamodb:" not in tf and ".dynamodb" not in tf
      and not os.path.exists(os.path.join(TF_DIR, "anomalies.tf")))
check("detector_logs の output は無い", "detector_logs" not in tf)
check("MSK は Kafka 4 以上の KRaft（kafka_version の既定が N.N.x.kraft で、検査が .kraft を強いる）",
      re.search(r'variable "kafka_version" \{[^}]*default\s*=\s*"[4-9]\.\d+\.x\.kraft"', tf) is not None and "x\\\\.kraft$" in tf)
check("ブローカーは Kafka 4 が受け付ける m5 / m7g（t3.small は Unsupported InstanceType。2026-09-18）",
      re.search(r'variable "broker_instance_type" \{[^}]*default\s*=\s*"kafka\.m5\.large"', tf) is not None
      and '"kafka.t3.small"' not in tf)

# ---- spark/snmp_sinks.py を pyspark 無しで読む
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


# ---- ログの経路: SR Linux の system logging remote-server（udp）→ lab の EC2（203.0.113.1:5140 を Telegraf の NLB へ DNAT）→ Telegraf（ECS）の inputs.syslog
# → Kafka の logs → Spark（2026-09-26。FRR + rsyslog をやめた）
def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()
srl_dir = os.path.join(ROOT, "lab", "srlinux")
srl_nodes = sorted(n[:-4] for n in os.listdir(srl_dir) if n.endswith(".cli"))
srl_cfg = {n: _read("lab", "srlinux", n + ".cli") for n in srl_nodes}
clab = _read("lab", "splab.clab.yml.in")
labsh = _read("lab", "lab.sh")
tele = _read("telegraf", "telegraf.conf.in")
tgsh = _read("telegraf", "telegraf.sh")
lab_locals = _read("terraform", "pipeline", "lab", "locals.tf")
stream_tg = _read("terraform", "pipeline", "stream", "telegraf.tf")
core_sg = _read("terraform", "base", "core", "security_groups.tf")
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
check("containerlab の VM 2 台は linux で、leaf の組へ 2 本（bond）", len(re.findall(r"^\s*kind: linux$", clab, re.M)) == 2 and "bond0" in clab)
check("lab.sh forward は syslog の LOG_PORT も trap の 162 と同じ仕組みで DNAT する（rsyslog は無い）",
      re.search(r'-p udp --dport "\$LOG_PORT" "\$\{c\[@\]\}" -j DNAT --to-destination "\$t:\$LOG_PORT"', labsh) is not None
      and "rsyslog" not in labsh and "LOG_DIR" not in labsh and re.search(r"^\s*logs\)", labsh, re.M) is not None)
# ログのポートは 5 か所で同じ（lab.sh / telegraf.sh / telegraf.conf.in / stream の NLB / 土台の SG の通信の表）。trap は NLB の 162 → タスクの 1162（非 root）
check("syslog のポートが lab.sh・telegraf.sh・telegraf.conf.in・stream の NLB・土台の通信の表で同じで、trap は NLB の 162 をタスクの 1162 で受ける",
      re.search(rf"^LOG_PORT={log_port}$", tgsh, re.M) is not None and re.search(rf'^\s*server = "udp://:{log_port}"$', tele, re.M) is not None
      and re.search(r'^\s*service_address = "udp://:1162"$', tele, re.M) is not None and re.search(r"^TRAP_PORT=1162$", tgsh, re.M) is not None
      and all(re.search(rf'\{{ from = "{a}", to = "{b}", protocol = "udp", port = {pt},', core_sg) is not None
              for a, b, pt in (("lab_mgmt", "telegraf_dialout_nlb", log_port), ("lab", "telegraf_dialout_nlb", log_port), ("telegraf_dialout_nlb", "telegraf_dialout", log_port),
                               ("lab_mgmt", "telegraf_dialout_nlb", 162), ("lab", "telegraf_dialout_nlb", 162), ("telegraf_dialout_nlb", "telegraf_dialout", 1162)))
      and "log_port" not in lab_locals
      and re.search(rf'syslog = \{{ listener = {log_port}, container = {log_port}, protocol = "UDP" \}}', stream_tg) is not None
      and re.search(r'trap\s+= \{ listener = 162, container = 1162, protocol = "UDP" \}', stream_tg) is not None)
# MDT の dial-out は 4 か所で同じポート（telegraf.sh / telegraf.conf.in / stream の NLB とタスク / 土台の通信の表）で TCP。NLB は TCP の送り元を残さない
check("MDT は tcp 57000 で受ける（inputs.cisco_telemetry_mdt・telegraf.sh・NLB の TCP のリスナー・タスクの portMappings・NLB → タスクの SG）",
      re.search(r'^MDT_PORT=57000$', tgsh, re.M) is not None
      and re.search(r'\[\[inputs\.cisco_telemetry_mdt\]\]\s*\n\s*transport = "grpc"\s*\n\s*service_address = ":57000"', tele) is not None
      and re.search(r'mdt\s+= \{ listener = 57000, container = 57000, protocol = "TCP" \}', stream_tg) is not None
      and "protocol    = each.value.protocol" in stream_tg and "protocol          = each.value.protocol" in stream_tg
      and 'preserve_client_ip = each.value.protocol == "UDP"' in stream_tg
      and '{ containerPort = 57000, protocol = "tcp" }' in stream_tg
      and re.search(r'\{ from = "telegraf_dialout_nlb", to = "telegraf_dialout", protocol = "tcp", port = 57000,', core_sg) is not None
      and "57000" not in lab_locals and "57000" not in labsh)
# 管理ネットワークは 4 か所で同じ（containerlab の mgmt / lab.sh / lab の locals の VPC ルート / 土台の SG の lab_mgmt）
mgmt = re.search(r"^MGMT=(\S+)$", labsh, re.M).group(1)
check("管理ネットワークが containerlab・lab.sh・lab の locals・土台の SG の lab_mgmt_cidr で同じ",
      re.search(rf"^\s*ipv4-subnet: {re.escape(mgmt)}$", clab, re.M) is not None
      and re.search(rf'^\s*mgmt_cidr\s*=\s*"{re.escape(mgmt)}"$', lab_locals, re.M) is not None
      and re.search(rf'^\s*lab_mgmt_cidr\s*=\s*"{re.escape(mgmt)}"$', core_sg, re.M) is not None)
# ポーリング先は lab の定義から作る（lab/lab_topology.py --snmp-agents → up.sh が stream の snmp_agents → タスクの SNMP_AGENTS → telegraf.sh render が埋める）
_lt_spec = importlib.util.spec_from_file_location("lab_topology", os.path.join(ROOT, "lab", "lab_topology.py"))
lt = importlib.util.module_from_spec(_lt_spec); _lt_spec.loader.exec_module(lt)
_lab_devices, _, _ = lt.load(os.path.join(ROOT, "lab"))
_agents_line = lt.snmp_agents(_lab_devices)
_agents = re.findall(r"udp://([\d.]+):161", _agents_line)
check("Telegraf のポーリング先は lab の監視対象（enabled）の管理 IP で、全部管理ネットワークの中（VPC のルートで lab の EC2 へ行く）",
      len(_agents) == 6 and sorted(_agents) == sorted(d["mgmt_ip"] for d in _lab_devices if d["enabled"])
      and all(ipaddress.ip_address(a) in ipaddress.ip_network(mgmt) for a in _agents))
check("telegraf.conf.in の agents は __SNMP_AGENTS__ を telegraf.sh render がタスクの SNMP_AGENTS で埋める（形を確かめてから）",
      re.search(r"^\s*agents = \[__SNMP_AGENTS__\]$", tele, re.M) is not None and 's#__SNMP_AGENTS__#$agents#' in tgsh
      and 'agents="${SNMP_AGENTS:-}"' in tgsh and '{ name = "SNMP_AGENTS", valueFrom = "${local.ssm_parameter_arn}${local.dialin_target_names["snmp-agents"]}" }' in stream_tg and '"snmp-agents" = var.snmp_agents' in stream_tg
      and re.fullmatch(r'"udp://[0-9.]+:[0-9]+"(, *"udp://[0-9.]+:[0-9]+")*', _agents_line) is not None)
_gnmi_line = lt.gnmi_targets(_lab_devices)
check("gNMI の購読先は同じ 6 台の管理 IP:57400 で、telegraf.conf.in の __GNMI_TARGETS__ を telegraf.sh render がタスクの GNMI_TARGETS で埋める",
      re.findall(r"([\d.]+):57400", _gnmi_line) == _agents and re.search(r"^\s*addresses = \[__GNMI_TARGETS__\]$", tele, re.M) is not None
      and 's#__GNMI_TARGETS__#$gnmi#' in tgsh and 'gnmi="${GNMI_TARGETS:-}"' in tgsh and '{ name = "GNMI_TARGETS", valueFrom = "${local.ssm_parameter_arn}${local.dialin_target_names["gnmi-targets"]}" }' in stream_tg and '"gnmi-targets" = var.gnmi_targets' in stream_tg
      and re.fullmatch(r'"[0-9.]+:[0-9]+"(, *"[0-9.]+:[0-9]+")*', _gnmi_line) is not None)
gnmi_blk = tele.split("[[inputs.gnmi]]", 1)[1].split("# ----", 1)[0]
check("inputs.gnmi は TLS（自己署名）で bgp_neighbor / isis_interface（IS-IS の IF の oper-state。隣接そのものは消えるので取らない）を on_change、evpn_es / mac_table を 1 分の sample で購読する",
      re.search(r'^\s*tls_enable = true$', gnmi_blk, re.M) is not None and re.search(r'^\s*enable_tls', gnmi_blk, re.M) is None and 'insecure_skip_verify = true' in gnmi_blk and 'encoding = "json_ietf"' in gnmi_blk
      and re.search(r'name = "bgp_neighbor"\s*\n\s*path = "/network-instance\[name=default\]/protocols/bgp/neighbor\[peer-address=\*\]/session-state"\s*\n\s*subscription_mode = "on_change"', gnmi_blk)
      and re.search(r'name = "isis_interface"\s*\n\s*path = "/network-instance\[name=default\]/protocols/isis/instance\[name=main\]/interface\[interface-name=\*\]/oper-state"\s*\n\s*subscription_mode = "on_change"', gnmi_blk)
      and 'name = "isis_adjacency"' not in gnmi_blk
      and gnmi_blk.count('subscription_mode = "sample"') == 2 and gnmi_blk.count('sample_interval = "60s"') == 2)
check("gNMI の 4 つは gnmi トピックへ（metrics には混ざらない）",
      re.search(r'topic = "gnmi"[\s\S]*?namepass = \["bgp_neighbor", "isis_interface", "evpn_es", "mac_table"\]', tele) is not None
      and re.search(r'topic = "metrics"[\s\S]*?namepass = \["device_cpu", "device_memory", "if_stats", "sessions", "circuits", "system", "interface"\]', tele) is not None)
# ---- lab の性能メトリクス（2 つめの inputs.gnmi → Starlark で共通の形（仮）へ → metrics トピック）
LAB_SUBS = {
    "lab_cpu": "/platform/control[slot=*]/cpu[index=*]/total/instant",
    "lab_memory": "/platform/control[slot=*]/memory",
    "lab_if_counters": "/interface[name=*]/statistics",
    "lab_port_speed": "/interface[name=*]/ethernet/port-speed",
    "lab_lag_speed": "/interface[name=*]/lag/lag-speed",
    "lab_ni_mac_active": "/network-instance[name=*]/bridge-table/statistics/active-entries",
    "lab_ni_mac_limit": "/network-instance[name=*]/bridge-table/mac-limit",
    "lab_subif_mac_active": "/interface[name=*]/subinterface[index=*]/bridge-table/statistics/active-entries",
    "lab_subif_mac_limit": "/interface[name=*]/subinterface[index=*]/bridge-table/mac-limit",
    "lab_subif_type": "/interface[name=*]/subinterface[index=*]/type",
    "lab_if_oper": "/interface[name=*]/oper-state",
}
_gnmi_blocks = tele.split("[[inputs.gnmi]]")[1:]
lab_blk = _gnmi_blocks[1].split("# ----", 1)[0] if len(_gnmi_blocks) == 2 else ""
_lab_subs = dict(re.findall(r'name = "(lab_\w+)"\s*\n\s*path = "([^"]+)"\s*\n\s*subscription_mode = "sample"\s*\n\s*sample_interval = "60s"', lab_blk))
check("性能メトリクスは 2 つめの inputs.gnmi（知らないパスで BGP / IS-IS の購読を巻き込まない）で、lab_* の 11 本を 1 分の sample。宛先・認証・TLS は 1 つめと同じ",
      len(_gnmi_blocks) == 2 and _lab_subs == LAB_SUBS and lab_blk.count("[[inputs.gnmi.subscription]]") == len(LAB_SUBS)
      and "name = \"lab_" not in gnmi_blk
      and all(l in lab_blk for l in ("addresses = [__GNMI_TARGETS__]", 'encoding = "json_ietf"', "tls_enable = true", "insecure_skip_verify = true", 'username = "${GNMI_USERNAME}"', 'password = "${GNMI_PASSWORD}"')))
_star_proc = re.search(r'\[\[processors\.starlark\]\]\s*\n\s*namepass = \[([^\]]*)\]\s*\n\s*script = "/etc/telegraf/lab_gnmi\.star"', tele)
_star_aggr = re.search(r'\[\[aggregators\.starlark\]\]\s*\n\s*namepass = \[([^\]]*)\]\s*\n\s*period = "60s"\s*\n\s*grace = "\d+s"\s*\n\s*drop_original = true\s*\n\s*script = "/etc/telegraf/lab_circuits\.star"', tele)
_dockerfile = _read("telegraf", "Dockerfile")
check("lab_* は processors.starlark（lab_gnmi.star）と aggregators.starlark（lab_circuits.star）で全部受け、Kafka のどの出力にも lab_* を載せない。.star はイメージの /etc/telegraf",
      _star_proc is not None and _star_aggr is not None
      and sorted(re.findall(r'"(\w+)"', _star_proc.group(1)) + re.findall(r'"(\w+)"', _star_aggr.group(1))) == sorted(LAB_SUBS)
      and re.findall(r'"(\w+)"', _star_aggr.group(1)) == ["lab_subif_type", "lab_if_oper"]
      and not any("lab_" in blk.split("# <<< sink", 1)[0] for blk in tele.split("[[outputs.kafka]]")[1:])
      and re.search(r"^COPY telegraf\.conf\.in lab_gnmi\.star lab_circuits\.star /etc/telegraf/$", _dockerfile, re.M) is not None)
check("MDT は collector タグで mdt トピックへだけ（measurement の名前は機器で変わるので namepass でなく tagpass）",
      re.search(r'\[\[inputs\.cisco_telemetry_mdt\]\][\s\S]*?\[inputs\.cisco_telemetry_mdt\.tags\]\s*\n\s*collector = "mdt"', tele) is not None
      and re.search(r'topic = "mdt"[\s\S]*?\[outputs\.kafka\.tagpass\]\s*\n\s*collector = \["mdt"\]\s*\n# <<< sink kafka', tele) is not None
      and tele.count('topic = "mdt"') == 1)
check("lab.sh forward は gNMI の GNMI_PORT/tcp も SNMP の 161/udp と同じく Telegraf から管理ネットワークへ通す",
      re.search(r'-p tcp --dport "\$GNMI_PORT" "\$\{c\[@\]\}" -j ACCEPT', labsh) is not None and re.search(r"^GNMI_PORT=57400$", labsh, re.M) is not None)
_up = _read("ops", "up.sh")
check("syslog の形式は stream の syslog_standard（既定 RFC3164 = 本番の Cisco）→ タスクの SYSLOG_STANDARD → telegraf.conf.in の __SYSLOG_STANDARD__。up.sh も deploy.env の SYSLOG_STANDARD（既定 RFC3164。lab の SR Linux は RFC5424）を渡す",
      re.search(r'^\s*syslog_standard = "__SYSLOG_STANDARD__"$', tele, re.M) is not None and 's#__SYSLOG_STANDARD__#$SYSLOG_STANDARD#' in tgsh
      and '{ name = "SYSLOG_STANDARD", value = var.syslog_standard }' in stream_tg
      and re.search(r'variable "syslog_standard" \{[^}]*default\s*=\s*"RFC3164"', _read("terraform", "pipeline", "stream", "variables.tf")) is not None
      and '-var "syslog_standard=$SYSLOG_STANDARD"' in _up and 'SYSLOG_STANDARD="${SYSLOG_STANDARD:-RFC3164}"' in _up and '[ "$SYSLOG_STANDARD" != "$LAB_SYSLOG_STANDARD" ]' in _up
      and "case \"$SYSLOG_STANDARD\" in RFC3164 | RFC5424) ;;" in _up
      and re.search(r"\bSYSLOG_STANDARD\b", _read("ops", "deploy-env.sh").split("DEPLOY_ENV_KEYS=", 1)[1].split('"')[1]) is not None
      and re.search(r"^#SYSLOG_STANDARD=RFC5424$", _read("deploy.env.example"), re.M) is not None and re.search(r"^LAB_SYSLOG_STANDARD=RFC5424\b", _read("ops", "lab-common.sh"), re.M) is not None)
check("SNMP のポーリングは既定でする（cycle 002。Grafana の link_down と Splunk の netops_poll が見る）: stream の snmp_poll（bool、既定 true）→ タスクの SNMP_POLL（1 / 0）→ "
      "telegraf.sh が「>>> snmp_poll」の区間を残すか消す。up.sh は deploy.env の SNMP_POLL（既定 1）を渡す",
      re.search(r'variable "snmp_poll" \{[^}]*type\s*=\s*bool[^}]*default\s*=\s*true', _read("terraform", "pipeline", "stream", "variables.tf")) is not None
      and '{ name = "SNMP_POLL", value = var.snmp_poll ? "1" : "0" }' in stream_tg
      and re.search(r"^SNMP_POLL=\$\{SNMP_POLL:-1\}$", tgsh, re.M) is not None and '/^# >>> snmp_poll/,/^# <<< snmp_poll/d' in tgsh
      and re.search(r"^# >>> snmp_poll[\s\S]*?^\[\[inputs\.snmp\]\][\s\S]*?^# <<< snmp_poll", tele, re.M) is not None
      and 'SNMP_POLL="${SNMP_POLL:-1}"; flag_value SNMP_POLL' in _up and '-var "snmp_poll=$SNMP_POLL_TF"' in _up
      and re.search(r"\bSNMP_POLL\b", _read("ops", "deploy-env.sh").split("DEPLOY_ENV_KEYS=", 1)[1].split('"')[1]) is not None
      and re.search(r"^#SNMP_POLL=0$", _read("deploy.env.example"), re.M) is not None)
check("Telegraf に入るコマンドの既定は tg gnmi（tg test はポーリングを止めていると何も取らない）",
      "--command 'tg gnmi'" in _read("terraform", "pipeline", "stream", "outputs.tf"))
check("up.sh は lab の定義からポーリング先と gNMI の購読先を作り、stream の snmp_agents / gnmi_targets に渡す（S3 には置かない）",
      'lab/lab_topology.py lab --snmp-agents' in _up and 'lab/lab_topology.py lab --gnmi-targets' in _up
      and '-var "snmp_agents=$SNMP_AGENTS" -var "gnmi_targets=$GNMI_TARGETS"' in _up and "/telegraf/" not in _up)
check("lab.sh up は毎回 forward を呼び、forward / forward-status がある",
      '"$SELF" forward' in labsh and re.search(r"^\s*forward\)", labsh, re.M) is not None and re.search(r"^\s*forward-status\)", labsh, re.M) is not None)
check("forward の iptables の規則は全部目印付き（unforward で消せる）",
      all("${c[@]}" in l for l in labsh.splitlines() if re.match(r"\s*iptables .*-I ", l)))
check("Telegraf はポーリングの IF の鍵を ifName（タグ）にする（SR Linux の ifDescr は description 付き）",
      re.search(r'name = "ifName"\s*\n\s*oid = "\.1\.3\.6\.1\.2\.1\.31\.1\.1\.1\.1"\s*\n\s*is_tag = true', tele) is not None)
check("Telegraf は機器の syslog を inputs.syslog（udp）で受け、device_log として logs トピックに出す",
      'name_override = "device_log"' in tele and "[[inputs.tail]]" not in tele and "[[inputs.socket_listener]]" not in tele
      and re.search(r'topic = "logs"[\s\S]*?namepass = \["device_log"\]|namepass = \["device_log"\][\s\S]*?topic = "logs"', tele) is not None)
check("metrics / traps / mdt の出力に device_log が混ざらない（namepass / namedrop / tagpass）",
      all(re.search(r"name(pass|drop)|tagpass", blk) for blk in tele.split("[[outputs.kafka]]")[1:]))
check("syslog の hostname を sysName のタグに付け替える（metrics / traps と同じ機器名のタグ）",
      re.search(r'\[\[processors\.rename\]\]\s*\n\s*namepass = \["device_log"\]\s*\n\s*\[\[processors\.rename\.replace\]\]\s*\n\s*tag = "hostname"\s*\n\s*dest = "sysName"', tele) is not None)
check("Spark の既定は gnmi / mdt トピックも読む（iceberg / prometheus は metrics,gnmi,mdt、opensearch は traps,logs）", mod.METRIC_TOPICS == "metrics,gnmi,mdt"
      and mod.sink_topics("iceberg", mod.METRIC_TOPICS, mod.LOG_TOPICS) == "metrics,gnmi,mdt,traps,logs" and mod.sink_topics("prometheus", mod.METRIC_TOPICS, mod.LOG_TOPICS) == "metrics,gnmi,mdt")
_access = _read("terraform", "pipeline", "stream", "access.tf")
_lab_tg = _read("terraform", "pipeline", "lab", "telegraf.tf")
# Kafka による違いは msk.tf の kafka_* の locals（OSS 版は oss/terraform/pipeline/stream/kafka.tf。cycle 005）
_msk = _read("terraform", "pipeline", "stream", "msk.tf")
_msk_code = "\n".join(l for l in _msk.splitlines() if not l.lstrip().startswith("#"))
_tg_kafka = _msk_code[_msk_code.index("telegraf_kafka_statements = ["):_msk_code.index("kafka_ui_kafka_statements = [")]
check("Telegraf は stream の ECS で、MSK への書き込みはタスクロール（lab の state のロールに頼らない。2026-09-28）。権限は msk.tf の telegraf_kafka_statements",
      'resource "aws_iam_role" "telegraf_task"' in stream_tg and "kafka-cluster:WriteData" in _tg_kafka
      and "Resource = [aws_msk_cluster.stream.arn, local.topic_arns]" in _tg_kafka
      and "Statement = concat(local.telegraf_kafka_statements, [" in stream_tg and "kafka-cluster" not in stream_tg
      and "stream_produce" not in _access and "telegraf_role_name" not in _access
      and 'resource "aws_iam_role"' not in _lab_tg and 'resource "aws_instance"' not in _lab_tg)
check("lab と stream は SG も SG のルールも作らない（ポーリング・trap・syslog のルールは土台の通信の表。2026-09-29）",
      all('resource "aws_security_group"' not in t and "aws_vpc_security_group_" not in t
          for t in (_lab_tg, lab_locals, stream_tg, _access, _read("terraform", "pipeline", "stream", "msk.tf"), _read("terraform", "pipeline", "lab", "instance.tf")))
      and 'security_groups = [local.telegraf_dialout_nlb_sg_id]' in stream_tg and 'security_groups  = [local.telegraf_dialout_sg_id]' in stream_tg)
_td = {k: m.group(0) for k in ("telegraf_dialout", "telegraf_dialin") if (m := re.search(r'resource "aws_ecs_task_definition" "' + k + r'" \{[\s\S]*?^\}', stream_tg, re.M))}
_svc = {k: m.group(0) for k in ("telegraf_dialout", "telegraf_dialin") if (m := re.search(r'resource "aws_ecs_service" "' + k + r'" \{[\s\S]*?^\}', stream_tg, re.M))}
check("Telegraf は受ける側（dialout。NLB の後ろ、SG telegraf_dialout）と取りにいく側（dialin。1 タスク固定、NLB なし、SG telegraf_dialin）の 2 サービス（2026-10-04 ユーザー決定）",
      len(_td) == 2 and len(_svc) == 2
      and '{ name = "TELEGRAF_ROLE", value = "dialout" }' in _td["telegraf_dialout"] and '{ name = "TELEGRAF_ROLE", value = "dialin" }' in _td["telegraf_dialin"]
      and "portMappings = [" in _td["telegraf_dialout"] and "portMappings = [" not in _td["telegraf_dialin"]
      and all(v not in _td["telegraf_dialout"] for v in ("SNMP_AGENTS", "GNMI_TARGETS", "SNMP_POLL"))
      and all(v in _td["telegraf_dialin"] for v in ("SNMP_AGENTS", "GNMI_TARGETS", "SNMP_POLL")) and "SYSLOG_STANDARD" not in _td["telegraf_dialin"]
      and 'awslogs-stream-prefix = "dialout"' in _td["telegraf_dialout"] and 'awslogs-stream-prefix = "dialin"' in _td["telegraf_dialin"]
      and "load_balancer" in _svc["telegraf_dialout"] and "load_balancer" not in _svc["telegraf_dialin"]
      and "security_groups  = [local.telegraf_dialin_sg_id]" in _svc["telegraf_dialin"]
      # 購読を二重にしない: 入れ替えでも 1 タスクを超えない
      and re.search(r"desired_count\s+= 1\b", _svc["telegraf_dialin"]) is not None
      and "deployment_minimum_healthy_percent = 0" in _svc["telegraf_dialin"] and "deployment_maximum_percent         = 100" in _svc["telegraf_dialin"]
      and "enable_execute_command = true" in _svc["telegraf_dialin"]
      and re.search(r'telegraf_dialin_sg_id\s*=\s*try\(data\.terraform_remote_state\.main\.outputs\.security_group_ids\["telegraf_dialin"\], ""\)', _read("terraform", "pipeline", "stream", "locals.tf")) is not None
      and '"$TG_DIALOUT_SERVICE" "$TG_DIALIN_SERVICE"' in _up and "telegraf_dialin_list_tasks_command" in _up)
_exec_pol = re.search(r'resource "aws_iam_role_policy" "telegraf_execution" \{[\s\S]*?^\}', stream_tg, re.M)
check("取りにいく側は機器の一覧と認証情報を SSM から ECS の secrets で受ける（environment に載せない）。認証情報の SecureString は up.sh が stream の apply の前に作り、実行ロールが読めるのは telegraf-dialin の下だけ",
      "secrets" not in _td["telegraf_dialout"] and "secrets = concat(" in _td["telegraf_dialin"]
      and not re.search(r'name = "(SNMP_AGENTS|GNMI_TARGETS|GNMI_USERNAME|GNMI_PASSWORD|SNMP_COMMUNITY)", value =', stream_tg)
      and 'dialin_credentials = { GNMI_USERNAME = "gnmi-username", GNMI_PASSWORD = "gnmi-password", SNMP_COMMUNITY = "snmp-community" }' in stream_tg
      and 'dialin_parameter_prefix = "/${local.name_prefix}/telegraf-dialin"' in stream_tg
      and all(f'ensure_fixed_secret "/$PREFIX/telegraf-dialin/{leaf}" "$LAB_{var}"' in _up and _up.index(f'ensure_fixed_secret "/$PREFIX/telegraf-dialin/{leaf}"') < _up.index("tf_apply pipeline/stream ")
              for leaf, var in (("gnmi-username", "GNMI_USERNAME"), ("gnmi-password", "GNMI_PASSWORD"), ("snmp-community", "SNMP_COMMUNITY")))
      and _exec_pol is not None and "ssm:GetParameters" in _exec_pol.group(0) and "${local.dialin_parameter_prefix}/*" in _exec_pol.group(0)
      and "var.gnmi" not in _exec_pol.group(0))
check("up.sh は base/core の state に古い Telegraf の SG（telegraf）があり stream が残っていれば、ECR より前に止める（SG のキーを変えると作り直しで、付けたままでは消せない）",
      """grep -qxF 'aws_security_group.workload["telegraf"]'""" in _up and "[ -s terraform/pipeline/stream/terraform.tfstate ]" in _up
      and _up.index("""'aws_security_group.workload["telegraf"]'""") < _up.index('log "1. ECR リポジトリ'))
_down = _read("ops", "down.sh")
check("down.sh は stream の必須変数（snmp_agents / gnmi_targets）に形だけ合う値を渡して destroy する（telegraf.sh の形の検査と同じ）",
      re.search(r"destroy_root pipeline/stream -var 'snmp_agents=\"udp://[0-9.]+:161\"' -var 'gnmi_targets=\"[0-9.]+:57400\"'", _down) is not None)
check("Spark の既定は logs も読む", mod.LOG_TOPICS == "traps,logs"
      and mod.sink_topics("opensearch", mod.METRIC_TOPICS, mod.LOG_TOPICS) == "traps,logs")

# ---- lab_gnmi.star / lab_circuits.star を Python で動かす（Python と Starlark の両方で動く書き方にしてある。Telegraf の Starlark は Metric と state を入れる）
class _NoLen(dict):   # Telegraf の Metric の tags / fields は len も真偽値も持たない（len(m.fields) は Telegraf で落ちた）
    def __len__(self):
        raise TypeError("Telegraf の tags / fields に len は無い")

class FakeMetric:
    def __init__(self, name, tags=None, fields=None, time=0):
        self.name, self.tags, self.fields, self.time = name, _NoLen(tags or {}), _NoLen(fields or {}), time

def _star(name):
    g = {"Metric": FakeMetric, "state": {}}
    exec(compile(_read("telegraf", name), name, "exec"), g)
    return g

_g = _star("lab_gnmi.star")
_apply = _g["apply"]
S = "203.0.113.11"
_m = _apply(FakeMetric("lab_cpu", {"source": S, "slot": "A", "index": "all"}, {"instant": 7}, time=123))
check("lab_cpu: index all だけを device_cpu（used_pct は float、component は slot、time と source を引き継ぐ）にし、コアごとは落とす",
      _m.name == "device_cpu" and _m.tags == {"source": S, "component": "A"} and _m.fields == {"used_pct": 7.0} and _m.time == 123
      and _apply(FakeMetric("lab_cpu", {"source": S, "slot": "A", "index": "0"}, {"instant": 9})) is None)
_m = _apply(FakeMetric("lab_memory", {"source": S, "slot": "A"}, {"physical": "8000000000", "free": "6000000000", "reserved": "1", "srl_nokia-platform-control:utilization": 25}))
_m2 = _apply(FakeMetric("lab_memory", {"source": S, "slot": "A"}, {"memory/physical": "1000", "memory/free": "250"}))
check("lab_memory: uint64 の文字列を数にし、utilization が無ければ (total - free) / total。名前空間の接頭辞とパスの前置きを外して比べる",
      _m.name == "device_memory" and _m.fields == {"total_bytes": 8000000000, "free_bytes": 6000000000, "used_pct": 25.0}
      and _m2.fields == {"total_bytes": 1000, "free_bytes": 250, "used_pct": 75.0})
_none = _apply(FakeMetric("lab_port_speed", {"source": S, "name": "ethernet-1/1"}, {"port_speed": "25G"}))
_none2 = _apply(FakeMetric("lab_lag_speed", {"source": S, "name": "lag1"}, {"lag_speed": "50000"}))
_m = _apply(FakeMetric("lab_if_counters", {"source": S, "name": "ethernet-1/1"},
                       {"in_octets": "123456789012345678", "out_octets": "5", "in_discarded_packets": "1", "out_discarded_packets": "2",
                        "in_error_packets": "3", "out_error_packets": "4", "in_unicast_packets": "9"}))
_m2 = _apply(FakeMetric("lab_if_counters", {"source": S, "name": "lag1"}, {"in_octets": 1.0}))
_m3 = _apply(FakeMetric("lab_if_counters", {"source": S, "name": "mgmt0"}, {"in_octets": "1"}))
check("lab_if_counters: if_stats（discarded / error の名前を共通の形に）。速度は先に届いた port-speed（25G）/ lag-speed（Mbps）を覚えて speed_bps に付け、速度の metric は落とす",
      _none is None and _none2 is None and _m.name == "if_stats" and _m.tags == {"source": S, "if_name": "ethernet-1/1"}
      and _m.fields == {"in_octets": 123456789012345678, "out_octets": 5, "in_discards": 1, "out_discards": 2, "in_errors": 3, "out_errors": 4, "speed_bps": 25000000000}
      and _m2.fields == {"in_octets": 1, "speed_bps": 50000000000} and _m3.fields == {"in_octets": 1})
_none = _apply(FakeMetric("lab_ni_mac_limit", {"source": S, "name": "mac-vrf-1"}, {"maximum_entries": 250, "warning_threshold_pct": 95}))
_m = _apply(FakeMetric("lab_ni_mac_active", {"source": S, "name": "mac-vrf-1"}, {"active_entries": "10"}))
_none2 = _apply(FakeMetric("lab_subif_mac_limit", {"source": S, "name": "ethernet-1/1", "index": "1"}, {"maximum_entries": "100"}))
_m2 = _apply(FakeMetric("lab_subif_mac_active", {"source": S, "name": "ethernet-1/1", "index": "1"}, {"active_entries": 5}))
_m3 = _apply(FakeMetric("lab_subif_mac_active", {"source": "203.0.113.12", "name": "ethernet-1/1", "index": "1"}, {"active_entries": 5}))
check("MAC の数: sessions（kind mac、scope network_instance / subinterface、owner は mac-vrf かサブ IF）。上限は機器と owner ごとに覚えて limit / warning_pct / used_pct に",
      _none is None and _none2 is None
      and _m.name == "sessions" and _m.tags == {"source": S, "kind": "mac", "scope": "network_instance", "owner": "mac-vrf-1"}
      and _m.fields == {"active": 10, "limit": 250, "used_pct": 4.0, "warning_pct": 95}
      and _m2.tags == {"source": S, "kind": "mac", "scope": "subinterface", "owner": "ethernet-1/1.1"} and _m2.fields == {"active": 5, "limit": 100, "used_pct": 5.0}
      and _m3.fields == {"active": 5})
_raw = [FakeMetric("lab_cpu", {"source": S, "index": "all"}, {"instant": "n/a"}), FakeMetric("lab_memory", {"source": S}, {"x": 1}),
        FakeMetric("lab_if_counters", {"source": S}, {"in_octets": 1}), FakeMetric("lab_port_speed", {"source": S, "name": "e"}, {"port_speed": "fast"}),
        FakeMetric("lab_ni_mac_active", {"source": S, "name": "v"}, {"other": 1}), FakeMetric("other", {}, {"v": 1})]
check("変換できないものは lab_* のまま返す（Kafka には載らず、デバッグ用の EC2 の標準出力で見える）",
      all(_apply(m) is m for m in _raw))
check("数の文字列: 負・小数・桁あふれ（19 桁以上は float）・数でないもの", _g["_num"]("-3") == -3 and _g["_num"]("2.5") == 2.5 and _g["_num"](".5") == 0.5
      and _g["_num"]("12345678901234567890") == 12345678901234567890.0 and _g["_num"]("1e3") is None and _g["_num"]("") is None and _g["_num"](None) is None
      and _g["_speed_bps"]("2.5G") == 2500000000 and _g["_speed_bps"]("100M") == 100000000 and _g["_speed_bps"]("G") is None)

_c = _star("lab_circuits.star")
def _circuit_round(metrics):
    for m in metrics:
        _c["add"](m)
    out = _c["push"]()
    _c["reset"]()
    return {m.tags["source"]: m.fields for m in out}
def _subif(src, name, idx, kind):
    return FakeMetric("lab_subif_type", {"source": src, "name": name, "index": idx}, {"type": kind})
def _oper(src, name, st):
    return FakeMetric("lab_if_oper", {"source": src, "name": name}, {"oper_state": st})
_r1 = _circuit_round([_oper(S, "ethernet-1/1", "up"), _oper(S, "ethernet-1/2", "down"), _oper(S, "ethernet-1/3", "up"), _oper(S, "lag1", "up"), _oper(S, "mgmt0", "up"),
                      _subif(S, "ethernet-1/1", "0", "routed"), _subif(S, "ethernet-1/2", "1", "srl_nokia-interfaces:bridged"),
                      _subif(S, "ethernet-1/2", "2", "bridged"), _subif(S, "lag1", "1", "bridged"),
                      _oper("203.0.113.12", "ethernet-1/1", "up")])
check("circuits: 機器ごとに active（bridged のサブ IF を持つ IF。lag も数える）/ up（そのうち oper up）/ capacity（ethernet-*）/ used_pct",
      _r1 == {S: {"active": 2, "up": 1, "capacity": 3, "used_pct": 2 * 100.0 / 3}, "203.0.113.12": {"active": 0, "up": 0, "capacity": 1, "used_pct": 0.0}})
_r2 = _circuit_round([_oper(S, "ethernet-1/1", "up")])
_r3 = _circuit_round([_oper(S, "ethernet-1/1", "up")])
_r4 = _circuit_round([_oper(S, "ethernet-1/1", "up")])
check("circuits: 届かなくなった IF / サブ IF / 機器は EXPIRE（3）回の push で数えなくなる（gNMI の delete は Telegraf が載せない）",
      _c["EXPIRE"] == 3 and _r2 == _r1 and _r3 == _r1 and _r4 == {S: {"active": 0, "up": 0, "capacity": 1, "used_pct": 0.0}}
      and [_circuit_round([]) for _ in range(3)] == [_r4, _r4, {}] and _c["state"]["devices"] == {})
check("circuits: source や name の無いもの、type / oper-state の無いものは数えない",
      _circuit_round([FakeMetric("lab_if_oper", {"name": "ethernet-1/1"}, {"oper_state": "up"}), FakeMetric("lab_subif_type", {"source": S, "name": "ethernet-1/1"}, {"type": "bridged"}),
                      FakeMetric("lab_if_oper", {"source": S, "name": "ethernet-1/1"}, {"other": "up"})]) == {})


# ---- Kafbat UI（terraform/pipeline/stream/kafka_ui.tf。2026-10-05）: MSK の画面。見るだけにせず、画面からトピックを足せる。stream を作る回はいつも作る
kui =_read("terraform", "pipeline", "stream", "kafka_ui.tf")
kui_code = "\n".join(l for l in kui.splitlines() if not l.lstrip().startswith("#"))   # コメントを除いた中身
stream_vars = _read("terraform", "pipeline", "stream", "variables.tf")
stream_out = _read("terraform", "pipeline", "stream", "outputs.tf")
up = _read("ops", "up.sh")
denv = _read("ops", "deploy-env.sh")
ecr_tf = _read("terraform", "base", "ecr", "main.tf")
check("Kafbat UI の資源 12 個は stream の root にいつもある（切り替える変数は無い。2026-10-05 のユーザー決定。count は閉域の Deny の 2 つが perimeter の有無で使うだけ）",
      len(re.findall(r'^resource "', kui, re.M)) == 12
      and re.findall(r'^  count = (.*)$', kui, re.M) == ['local.perimeter_policy_arn != "" ? 1 : 0'] * 2
      and "create_kafka_ui" not in tf and "[0]" not in kui_code)
check("Kafbat UI は stream の ECS クラスタの Fargate ARM64 に 1 タスク。Cloud Map の kafka-ui で引き、LB は置かない",
      re.search(r'resource "aws_ecs_service" "kafka_ui" \{[\s\S]*?cluster\s*=\s*aws_ecs_cluster\.telegraf\.id[\s\S]*?desired_count\s*=\s*1\n[\s\S]*?launch_type\s*=\s*"FARGATE"', kui) is not None
      and 'cpu_architecture        = "ARM64"' in kui and 'portMappings = [{ containerPort = 8080, protocol = "tcp" }]' in kui
      and re.search(r'resource "aws_service_discovery_service" "kafka_ui" \{[^}]*name = "kafka-ui"', kui) is not None
      and "registry_arn = aws_service_discovery_service.kafka_ui.arn" in kui
      and "load_balancer" not in kui and "aws_lb" not in kui)
_kenv = kui[kui.index("environment = concat("):kui.index("secrets = [")]
_ksec = kui[kui.index("secrets = ["):kui.index("healthCheck = {")]
check("admin のパスワードは environment（平文）でなく secrets で SSM の SecureString から渡す。実行ロールはそのパラメータだけ読める",
      "PASSWORD" not in _kenv and re.findall(r'name = "(\w+)"', _ksec) == ["SPRING_SECURITY_USER_PASSWORD"]
      and "valueFrom = local.kafka_ui_password_arn" in _ksec
      and 'kafka_ui_password_parameter = "/${local.name_prefix}/kafka-ui/admin-password"' in kui
      and re.search(r'Sid\s*=\s*"AdminPassword"[\s\S]*?Action\s*=\s*\["ssm:GetParameters"\]\s*Resource\s*=\s*local\.kafka_ui_password_arn', kui) is not None)
check("画面はログインフォーム（AUTH_TYPE=LOGIN_FORM、ユーザー admin）で、GitHub に版を聞きにいかない。READONLY は付けず、DYNAMIC_CONFIG_ENABLED は既定（false）のまま",
      '{ name = "AUTH_TYPE", value = "LOGIN_FORM" }' in _kenv and '{ name = "SPRING_SECURITY_USER_NAME", value = "admin" }' in _kenv
      and '{ name = "GITHUB_RELEASE_INFO_ENABLED", value = "false" }' in _kenv
      and "READONLY" not in kui_code and "DYNAMIC_CONFIG" not in kui_code and "READONLY" not in up)
check("MSK へは IAM 認証（SASL_SSL / AWS_MSK_IAM / IAMClientCallbackHandler / IAMLoginModule required;）で、認証情報はタスクロール（awsRoleArn も STS も使わない）",
      re.search(r'variable "kafka_ui_security_protocol" \{[^}]*default\s*=\s*"SASL_SSL"', stream_vars) is not None
      and '{ name = "KAFKA_CLUSTERS_0_PROPERTIES_SASL_MECHANISM", value = "AWS_MSK_IAM" }' in kui
      and '{ name = "KAFKA_CLUSTERS_0_PROPERTIES_SASL_CLIENT_CALLBACK_HANDLER_CLASS", value = "software.amazon.msk.auth.iam.IAMClientCallbackHandler" }' in kui
      and '{ name = "KAFKA_CLUSTERS_0_PROPERTIES_SASL_JAAS_CONFIG", value = "software.amazon.msk.auth.iam.IAMLoginModule required;" }' in kui
      and "awsRoleArn" not in kui_code and "sts:" not in kui_code and "task_role_arn            = aws_iam_role.kafka_ui_task.arn" in kui)
check("認証の違いは kafka_ui_security_protocol だけで切り替わる（SASL_SSL は IAM のブートストラップと SASL の 3 つ、PLAINTEXT は平文のブートストラップで SASL は無し。cycle 005 の OSS 版で使い回す）",
      re.search(r'variable "kafka_ui_security_protocol" \{[\s\S]*?contains\(\["SASL_SSL", "PLAINTEXT"\], var\.kafka_ui_security_protocol\)', stream_vars) is not None
      and 'kafka_ui_iam               = var.kafka_ui_security_protocol == "SASL_SSL"' in kui
      and 'kafka_ui_bootstrap_servers = lookup(local.kafka_bootstrap_by_protocol, var.kafka_ui_security_protocol, "")' in kui
      and re.search(r"kafka_bootstrap_by_protocol = \{\s*SASL_SSL\s*=\s*aws_msk_cluster\.stream\.bootstrap_brokers_sasl_iam\s*"
                    r"PLAINTEXT\s*=\s*aws_msk_cluster\.stream\.bootstrap_brokers\s*\}", _msk) is not None
      and "condition     = local.kafka_ui_bootstrap_servers != \"\"" in kui
      and '{ name = "KAFKA_CLUSTERS_0_NAME", value = local.kafka_cluster_name }' in _kenv
      and "kafka_cluster_name = aws_msk_cluster.stream.cluster_name" in _msk
      and "kafka_ui_iam_environment = local.kafka_ui_iam ? [" in kui and "] : []" in kui
      and '{ name = "KAFKA_CLUSTERS_0_PROPERTIES_SECURITY_PROTOCOL", value = var.kafka_ui_security_protocol }' in _kenv
      and "local.kafka_ui_iam_environment," in _kenv and "SASL" not in _kenv)
# タスクロールのポリシーは msk.tf の kafka_ui_kafka_statements をそのまま使う
_kpol = _msk_code[_msk_code.index("kafka_ui_kafka_statements = ["):_msk_code.index("kafka_descriptions = {")]
_kact = lambda sid: re.findall(r'"kafka-cluster:(\w+)"', re.search(r'Sid\s*=\s*"' + sid + r'"[\s\S]*?Resource', _kpol).group(0))
check("タスクロールは Kafbat UI が使う Kafka の操作だけ（クラスターの Connect / Describe、トピックの作成・変更・削除・設定の読み書き・データの読み書き、グループの Describe）",
      _kact("KafkaCluster") == ["Connect", "DescribeCluster", "DescribeClusterDynamicConfiguration", "WriteDataIdempotently"]
      and _kact("KafkaTopics") == ["DescribeTopic", "CreateTopic", "AlterTopic", "DeleteTopic", "DescribeTopicDynamicConfiguration",
                                   "AlterTopicDynamicConfiguration", "ReadData", "WriteData"]
      and _kact("KafkaGroups") == ["DescribeGroup"]
      and "kafka-cluster:*" not in _kpol and all(a not in kui_code + _kpol for a in ("AlterCluster", "AlterGroup", "DeleteGroup"))
      and "Resource = aws_msk_cluster.stream.arn" in _kpol and "Resource = local.topic_arns" in _kpol and "Resource = local.group_arns" in _kpol
      and 'group_arns = "${replace(aws_msk_cluster.stream.arn, ":cluster/", ":group/")}/*"' in _msk
      and re.search(r'resource "aws_iam_role_policy" "kafka_ui_task" \{\n  name = "\$\{local\.name_prefix\}-kafka-ui-task"\n  role = aws_iam_role\.kafka_ui_task\.name\n\n  policy = jsonencode\(\{\n    Version   = "2012-10-17"\n    Statement = local\.kafka_ui_kafka_statements\n', kui) is not None
      and "kafka-cluster" not in kui_code)
check("実行ロールにもタスクロールにも閉域の Deny（perimeter）を付ける（古い土台で ARN が無ければ付けない）",
      all(re.search(r'resource "aws_iam_role_policy_attachment" "kafka_ui_' + r + r'_perimeter" \{\n  count = local\.perimeter_policy_arn != "" \? 1 : 0\n\n  role\s*=\s*aws_iam_role\.kafka_ui_' + r + r'\.name', kui)
          for r in ("execution", "task")))
_sg_rows = core_sg
_api_clients = re.findall(r'"(\w+)"', re.search(r'aws_api_clients = \[([^\]]*)\]', _sg_rows).group(1))
check("SG は土台（base/core）の kafka_ui。通信の表に Web の EC2 → 8080、Kafbat UI → MSK の 9098（IAM）、エンドポイントの 443（aws_api_clients）がある。stream は SG を作らない",
      re.search(r'^\s+kafka_ui\s+=\s+"', _sg_rows, re.M) is not None and "kafka_ui" in _api_clients
      and re.search(r'\{ from = "web", to = "kafka_ui", protocol = "tcp", port = 8080, why = "[^"]*" \}', _sg_rows) is not None
      and re.search(r'\{ from = "kafka_ui", to = "msk", protocol = "tcp", port = 9098, why = "[^"]*" \}', _sg_rows) is not None
      and not re.search(r'to = "kafka_ui"[^}]*port = (?!8080)', _sg_rows) and not re.search(r'from = "kafka_ui", to = "(?!msk")', _sg_rows)
      and 'kafka_ui_sg_id = try(data.terraform_remote_state.main.outputs.security_group_ids["kafka_ui"], "")' in _read("terraform", "pipeline", "stream", "locals.tf")
      and "security_groups  = [local.kafka_ui_sg_id]" in kui and "aws_security_group" not in tf)
check("開き方は Grafana と同じ（web の EC2 を踏み台にした SSM のポートフォワード）。PC 側は 8082（Web 8080 と Nautobot 8081 とぶつけない）",
      'AWS-StartPortForwardingSessionToRemoteHost --parameters \'{\\"host\\":[\\"kafka-ui.${local.stream_service_namespace}\\"],\\"portNumber\\":[\\"8080\\"],\\"localPortNumber\\":[\\"8082\\"]}\'' in stream_out
      and "--target ${local.web_instance_id}" in stream_out
      and re.search(r'\nif \[ -z "\$SKIP_STREAM" \]; then\n  echo "Kafbat UI（http://localhost:8082/[^\n]*\n  tf pipeline/stream output -raw kafka_ui_port_forward_command; echo\n'
                    r'  tf pipeline/stream output -raw kafka_ui_password_command; echo\nfi\n', up) is not None)
_kui_tag = re.search(r'^KAFKA_UI_TAG=(\S+)$', up, re.M)
check("イメージは ghcr.io/kafbat/kafka-ui を ECR の <接頭辞>-kafka-ui に写す。版は固定で、up.sh の KAFKA_UI_TAG と kafka_ui_image_tag の既定が同じ",
      _kui_tag is not None and _kui_tag.group(1) not in ("latest", "main") and re.match(r"^v\d+\.\d+\.\d+$", _kui_tag.group(1))
      and re.search(r'variable "kafka_ui_image_tag" \{[^}]*default\s*=\s*"' + re.escape(_kui_tag.group(1)) + '"', stream_vars) is not None
      and '"kafka-ui"' in re.search(r'pipeline_repositories = toset\(\[([^\]]*)\]\)', ecr_tf).group(1)
      and 'mirror_image "ghcr.io/kafbat/kafka-ui:$KAFKA_UI_TAG" "$REG/$PREFIX-kafka-ui:$KAFKA_UI_TAG"' in up
      and 'ecr_has "$PREFIX-kafka-ui" "$KAFKA_UI_TAG"' in up and '"kafka_ui_image_tag=$KAFKA_UI_TAG"' in up)
check("Kafbat UI が呼ぶ AWS の API（ECR・ログ・SSM）は、stream を作る回のエンドポイントで足りる",
      "pipeline/stream) add_endpoints ecr.api ecr.dkr logs ;;" in up and "add_endpoints ssm ssmmessages" in up)
# stream を作る回だけ（if [ -z "$SKIP_STREAM" ] の中）にあるか。その if より後ろで、間に閉じる fi が無い
def _in_stream_block(marker):
    i = up.index(marker)
    j = up.rindex('\nif [ -z "$SKIP_STREAM" ]; then\n', 0, i)
    return "\nfi\n" not in up[j:i]
# KAFKA_UI というキー（と作る・作らないの変数 create_kafka_ui）がどこにも無い: ops/ のシェル・deploy.env.example・terraform/ の .tf
_no_switch_files = ["deploy.env.example"] + [os.path.join("ops", n) for n in sorted(os.listdir(os.path.join(ROOT, "ops"))) if n.endswith(".sh")] + [
    os.path.relpath(os.path.join(d, n), ROOT) for d, _, ns in os.walk(os.path.join(ROOT, "terraform")) if ".terraform" not in d for n in ns if n.endswith(".tf")]
_has_switch = [p for p in _no_switch_files if re.search(r"\bKAFKA_UI\b|create_kafka_ui", _read(p))]
check(f"スイッチは無い: KAFKA_UI というキーと create_kafka_ui は ops/ と deploy.env.example と terraform/ のどこにも無い（{_has_switch}）。up.sh の冒頭は「stream に入る」と費用の目安だけ",
      len(_no_switch_files) > 20 and "ops/deploy-env.sh" in _no_switch_files and _has_switch == []
      and re.search(r"^#   （Kafbat UI）\s+stream を作る回は Kafbat UI[^\n]*\+\$0\.02/h[^\n]*\*\*いつも作る\*\*（切り替える変数は無い[^\n]*\n#   （", up, re.M) is not None
      and "Kafbat UI（約 $0.02/h）もいつも入る" in _read("deploy.env.example"))
check("stream を作る回はいつも作る: イメージを ECR に写し、パスワードを SSM に作り、stream の apply に版を渡し、最後に開き方を出す（どれも SKIP_STREAM が空の if の中）",
      _in_stream_block('  if ecr_has "$PREFIX-kafka-ui" "$KAFKA_UI_TAG"; then')
      and _in_stream_block('  ensure_secret "/$PREFIX/kafka-ui/admin-password" password ')
      and _in_stream_block('  tf_apply pipeline/stream ')
      and up.index('ensure_secret "/$PREFIX/kafka-ui/admin-password"') < up.index("  tf_apply pipeline/stream ")
      and '-var "kafka_ui_image_tag=$KAFKA_UI_TAG"' in up[up.index("  tf_apply pipeline/stream "):].split("\n", 1)[0]
      and _in_stream_block('  echo "Kafbat UI（http://localhost:8082/'))
check("ops/down.sh は Kafbat UI のパスワード（ManagedBy=ops/up.sh のタグ）も消す。stream の destroy に Kafbat UI の変数は要らない",
      "Tags" in up[up.index("ensure_secret() {"):up.index("ensure_secret() {") + 2500] and "Key=tag:ManagedBy,Values=ops/up.sh" in _read("ops", "down.sh")
      and "kafka_ui" not in _read("ops", "down.sh"))

print(f"通過 {passed} / 失敗 0")
