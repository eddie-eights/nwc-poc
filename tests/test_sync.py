"""Neptune へのトポロジ同期の模擬テスト（AWS に触れない）。
lab/lab_topology.py が lab の定義（splab.clab.yml.in + srlinux/*.cli。lab/gen_lab.py が作る）から作る機器・回線・上の層が agent/data の静的データと同じであること
（PyYAML があるときと無いときの両方）、graph/status_handler.py が Grafana と Splunk のアラート（SNS。firing / resolved）を graph.set_status / set_layer_status に正しく写すこと、
terraform/pipeline/graph の sync.tf がその配線を持つこと。実行は uv run --group dev python tests/test_sync.py"""
import builtins, importlib.util, json, os, re, subprocess, sys, tempfile, types

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "workflow"))   # status Lambda の zip は workflow/rules.py を rules.py として同梱する
passed = 0


def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


# ---- lab → トポロジ
lt = load("lab/lab_topology.py", "lab_topology")
topology = load("agent/topology.py", "topology")   # graph は未配備（環境変数もパラメータも無い）なので静的データ
static_devices, static_links = topology.load_static()
DEV_KEYS = ("hostname", "site", "role", "asn", "mgmt_ip", "enabled")


def same(devices, links):
    sd = {d["device_id"]: d for d in static_devices}
    if {d["device_id"] for d in devices} != set(sd):
        return False
    for d in devices:
        if any(d.get(k) != sd[d["device_id"]].get(k) for k in DEV_KEYS):
            return False
    key = lambda l: (l["a"], l["a_if"], l["b"], l["b_if"], l["kind"], l.get("role"), l.get("bandwidth_mbps"))
    return sorted(map(key, links)) == sorted(map(key, static_links))


devices, links, layers = lt.load(os.path.join(ROOT, "lab"))
check("lab の定義から 8 台と 12 本（Leaf-SW 2 + Spine 2 + Leaf 2 + VM 2）", len(devices) == 8 and len(links) == 12)
check("機器（hostname / site / role / asn / mgmt_ip / enabled）が agent/data の静的データと同じ", same(devices, static_links))
check("回線（両端の IF / 種別 / 主副 / 帯域）が agent/data の静的データと同じ", same(static_devices, links))
check("回線は a < b に正規化", all(l["a"] < l["b"] for l in links))
check("監視対象は SNMP（trap-group）の設定を持つ SR Linux の 6 台（VM は対象外）", all(d["enabled"] == (d["role"] in ("leafsw", "spine", "leaf")) for d in devices) and sum(d["enabled"] for d in devices) == 6)
check("帯域は SR Linux の interface description の 1G / 100M / 10G から", lt.bandwidth_mbps("core 10G") == 10000 and lt.bandwidth_mbps("WAN 100M") == 100
      and lt.bandwidth_mbps("LAN") is None and lt.bandwidth_mbps("to pe-01 2.5G") == 2500 and lt.bandwidth_mbps("fabric to dc1-spine-01 25G") == 25000)
check("主副は description の primary / secondary から", lt.link_role("WAN secondary to x") == "secondary" and lt.link_role("dc1-leaf-01 primary access") == "primary" and lt.link_role("LAN") is None)
check("SR Linux の設定（set / の行）から asn と interface の description、SNMP の有無",
      lt.parse_srl('set / interface ethernet-1/1 description "a 1G"\nset / interface ethernet-1/1 admin-state enable\n'
                   'set / network-instance default protocols bgp autonomous-system 65001\nset / system snmp trap-group t admin-state enable\n')
      == {"asn": 65001, "interfaces": {"ethernet-1/1": {"description": "a 1G"}}, "snmp": True, "subinterfaces": {},
          "isis": {"instance": None, "interfaces": {}}, "bgp": {"router_id": None, "groups": {}, "neighbors": {}}, "evpn": {}, "vxlan": {}, "es": {}})

# PyYAML が無い PC（ops/up.sh を打つ手元の python3）でも同じになる
real_import = builtins.__import__
def no_yaml(name, *a, **k):
    if name == "yaml":
        raise ImportError("no yaml")
    return real_import(name, *a, **k)
builtins.__import__ = no_yaml
try:
    d2, l2, y2 = lt.load(os.path.join(ROOT, "lab"))
finally:
    builtins.__import__ = real_import
leaf = next(d for d in devices if d["device_id"] == "dc1-leaf-01")
check("インタフェースはリンクの両端だけでなく全部（管理の mgmt0 が先頭、SR Linux / exec のアドレス付き）",
      [(i["name"], i["address"]) for i in leaf["interfaces"]][:2] == [("mgmt0", "203.0.113.31"), ("ethernet-1/1", "172.16.0.5")]
      and all({"name", "address"} <= set(i) for d in devices for i in d["interfaces"])
      and all(any(i["name"] == (l["a_if"] if l["a"] == d["device_id"] else l["b_if"]) for i in d["interfaces"])
              for l in links for d in devices if d["device_id"] in (l["a"], l["b"])))
check("別名は device_id / hostname / 管理 IP / 全インタフェースのアドレスを小文字で", {"dc1-leaf-01", "203.0.113.31", "172.16.0.5"} <= set(leaf["aliases"])
      and all(a == a.lower() for d in devices for a in d["aliases"]))
dm = lt.parse_device_map(lt.device_map(devices)) if hasattr(lt, "parse_device_map") else dict(x.split("=", 1) for x in lt.device_map(devices).split(","))
check("device map は別名 → device_id（device_id 自身は省く）で、全機器の管理 IP を含む",
      dm["172.16.0.5"] == "dc1-leaf-01" and "dc1-leaf-01" not in dm and all(dm.get(d["mgmt_ip"]) == d["device_id"] for d in devices if d["mgmt_ip"]))
try:
    lt.device_map([{"device_id": "a", "aliases": ["10.0.0.1"]}, {"device_id": "b", "aliases": ["10.0.0.1"]}])
    dup = False
except ValueError:
    dup = True
check("1 つの別名が 2 台を指していたら device map を作らずに止める", dup)
check("snmp agents は監視対象（enabled）の管理 IP だけ", lt.snmp_agents(devices).count("udp://") == sum(1 for d in devices if d["enabled"])
      and lt.snmp_agents([{"enabled": True, "mgmt_ip": "203.0.113.9"}, {"enabled": False, "mgmt_ip": "203.0.113.8"}]) == '"udp://203.0.113.9:161"')
check("SR Linux の host-name と subinterface の ipv4 address も読む（SNMP 無しなら snmp は False）",
      lt.parse_srl("set / system name host-name R1\nset / interface ethernet-1/1 subinterface 0 ipv4 address 10.0.0.1/30\n")
      == {"asn": None, "hostname": "R1", "interfaces": {"ethernet-1/1": {"address": "10.0.0.1"}}, "snmp": False,
          "subinterfaces": {"ethernet-1/1.0": {"interface": "ethernet-1/1", "address": "10.0.0.1", "prefix_length": 30, "network_instance": None}},
          "isis": {"instance": None, "interfaces": {}}, "bgp": {"router_id": None, "groups": {}, "neighbors": {}}, "evpn": {}, "vxlan": {}, "es": {}})
check("gnmi targets は監視対象（enabled）の管理 IP:57400（Telegraf の inputs.gnmi の addresses）", lt.gnmi_targets(devices).count(":57400") == 6
      and lt.gnmi_targets([{"enabled": True, "mgmt_ip": "203.0.113.9"}, {"enabled": False, "mgmt_ip": "203.0.113.8"}]) == '"203.0.113.9:57400"')
# 上の層（IP 層 / EVPN・BGP 層）。物理層の頂点 <機器>#<IF> を interface_id / ip_interface_id で指す
lv = {v["id"]: v for v in layers["vertices"]}
if_ids = {f'{d["device_id"]}#{i["name"]}' for d in devices for i in d["interfaces"]}
check("上の層は 62 頂点・84 辺で、頂点の id は機器ごとに一意", len(lv) == 62 == len(layers["vertices"]) and len(layers["edges"]) == 84)
check("ip_interface は物理層の interface を interface_id で指し、over の辺でつながる（ループバック system0.0 は物理層に無いので空。IS-IS の隣接は ip_interface_id も）",
      all((v["interface_id"] in if_ids) == (not v["name"].startswith("system0")) for v in lv.values() if v["label"] == "ip_interface")
      and all(v["interface_id"] == "" for v in lv.values() if v["label"] == "ip_interface" and v["name"].startswith("system0"))
      and all(v["ip_interface_id"] in lv and v["interface_id"] in if_ids for v in lv.values() if v["label"] == "isis_adjacency")
      and all(e["to"] in if_ids or e["to"] in lv for e in layers["edges"] if e["label"] == "over"))
check("EVPN・BGP 層は loopback の ip_interface を ip_interface_id で指し、ES は lag の interface を指す",
      all(v["ip_interface_id"].endswith("#system0.0") and v["ip_interface_id"] in lv for v in lv.values() if v["label"] in ("bgp_session", "evpn_instance"))
      and all(v["interface_id"].endswith("#lag1") and v["interface_id"] in if_ids for v in lv.values() if v["label"] == "ethernet_segment"))
check("iBGP EVPN は Leaf-SW / Leaf から Spine 2 台へ（Spine は RR。AS 65100）",
      {(v["device_id"], v["peer_device"]) for v in lv.values() if v["label"] == "bgp_session" and v["role"] == "client"}
      == {(f"dc1-{r}-0{i}", f"dc1-spine-0{s}") for r in ("leafsw", "leaf") for i in (1, 2) for s in (1, 2)}
      and all(v["asn"] == 65100 == v["peer_as"] for v in lv.values() if v["label"] == "bgp_session"))
check("IS-IS の隣接は fabric の 8 本の両端（peer の辺）", sum(1 for v in lv.values() if v["label"] == "isis_adjacency") == 16
      and sum(1 for e in layers["edges"] if e["label"] == "peer" and e["from"].split("#")[1] == "isis") == 8)
check("EVI 100 は 4 台で同じ RT / VNI、ES は Leaf-SW の組と Leaf の組で同じ ESI",
      {(v["evi"], v["vni"], v["route_target"]) for v in lv.values() if v["label"] == "evpn_instance"} == {(100, 100, "target:65100:100")}
      and len({v["esi"] for v in lv.values() if v["label"] == "ethernet_segment" and v["device_id"].startswith("dc1-leaf-")}) == 1
      and len({v["esi"] for v in lv.values() if v["label"] == "ethernet_segment"}) == 2)
check("agent/data/layers.json は lab の定義から作った上の層と同じ", topology.load_static_layers() == layers)
# lab の定義は lab/gen_lab.py の出力そのもの（手で直さない。台数を変えるときは gen_lab.py を回す）
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run([sys.executable, os.path.join(ROOT, "lab", "gen_lab.py"), "--out", tmp], check=True, capture_output=True)
    gen = {}
    for dp, _, fns in os.walk(tmp):
        for fn in fns:
            p = os.path.join(dp, fn)
            gen[os.path.relpath(p, tmp)] = open(p, encoding="utf-8").read()
check("lab/splab.clab.yml.in と lab/srlinux/*.cli は lab/gen_lab.py の出力と同じ（既定の leaf 2・spine 2）",
      set(gen) == {"splab.clab.yml.in"} | {f"srlinux/{n}.cli" for n in ("dc1-leafsw-01", "dc1-leafsw-02", "dc1-spine-01", "dc1-spine-02", "dc1-leaf-01", "dc1-leaf-02")}
      and all(read("lab", *rel.split("/")) == text for rel, text in gen.items()))
check("PyYAML が無くても同じ結果（自前の読み取り）", d2 == devices and l2 == links and y2 == layers)
check("自前の YAML 読み取りはコメント・引用符・真偽値・数値・flow list を読む",
      lt.load_yaml('a: "x # y"  # c\nb: [p, "q"]\nc:\n  - d: 1\n    e: true\n  - f\n') == {"a": "x # y", "b": ["p", "q"], "c": [{"d": 1, "e": True}, "f"]})
check("CLI は --device-map / --snmp-agents / --gnmi-targets / --layers を受ける", '"--device-map", "--snmp-agents", "--gnmi-targets", "--layers"' in read("lab", "lab_topology.py"))
check("CLI は {devices, links, layers} の JSON を出す", "json.dump" in read("lab", "lab_topology.py") and '"devices": devices, "links": links, "layers": lyr' in read("lab", "lab_topology.py"))

# ---- ops/up.sh 7-3b と ops/sync-graph.sh は lab から作って base64 で渡す
up = read("ops", "up.sh"); sync = read("ops", "sync-graph.sh"); seed = read("ops", "seed_graph.py")
check("up.sh 7-3b は lab/lab_topology.py の出力を LAB_TOPOLOGY_B64 で seed_graph.py に渡す", "lab/lab_topology.py lab | base64" in up and "LAB_TOPOLOGY_B64=$LAB_TOPOLOGY_B64 /usr/bin/python3.13 -" in up)
check("sync-graph.sh は --replace で GRAPH_REPLACE=1、--dry-run は Neptune に触らない", "GRAPH_REPLACE=${REPLACE:-0}" in sync and "--replace) REPLACE=1" in sync and 'if [ -n "$DRY" ]; then printf' in sync)
check("seed_graph.py は LAB_TOPOLOGY_B64 を読み、GRAPH_REPLACE=1 のときだけ入れ直す", 'os.environ.get("LAB_TOPOLOGY_B64")' in seed and 'os.environ.get("GRAPH_REPLACE") != "1"' in seed)
check("seed_graph.py は layers も渡す（lab からは JSON の layers、静的データは data/layers.json）", 'lab.get("layers")' in seed and "topology.load_static_layers()" in seed and "graph.seed(devices, links, layers)" in seed)
check("up.sh は gNMI の購読先も lab の定義から作って stream の gnmi_targets に渡し、gateway.tf は data/layers.json を tools の zip に入れる",
      "lab/lab_topology.py lab --gnmi-targets" in up and '-var "gnmi_targets=$GNMI_TARGETS"' in up
      and '"../../agent/data/layers.json"' in read("terraform", "workflow", "gateway.tf"))

# ---- status Lambda（graph.set_status を差し替えて呼び出しを見る）
calls = []
fake_graph = types.ModuleType("graph")
fake_graph.set_status = lambda dev, ifn="", status="DOWN", only_if="": (calls.append((dev, ifn, status) + ((only_if,) if only_if else ())) or {"updated": 1})
layer_calls = []
fake_graph.set_layer_status = lambda dev, kind, target, status="DOWN": (layer_calls.append((dev, kind, target, status)) or {"updated": 1})
fake_graph._cache = {"client": None}   # status_handler._neptune が NEPTUNE_CONFIG のクライアントを入れる置き場
sys.modules["graph"] = fake_graph
h = load("graph/status_handler.py", "status_handler")
_neptune_client = object()
h._cache["neptune"] = _neptune_client   # 資格情報を探しに行かない（作り方は下の NEPTUNE_CONFIG の検査で見る）
def ev(status, source="grafana", **a):
    """SNS が Lambda に渡すイベント（Records[].Sns.Message に共通の形の JSON）"""
    return {"Records": [{"EventSource": "aws:sns", "Sns": {"Message": json.dumps({"source": source, "alerts": [dict(a, status=status)]})}}]}


h.handler(ev("firing", device_id="dc1-leaf-01", kind="link_down", target="eth1"))
check("firing の link_down は機器の IF の回線を DOWN", calls[-1] == ("dc1-leaf-01", "eth1", "DOWN"))
h.handler(ev("resolved", device_id="dc1-leaf-01", kind="link_down", target="eth1"))
check("resolved は同じ回線を UP", calls[-1] == ("dc1-leaf-01", "eth1", "UP"))
h.handler(ev("firing", "splunk", device_id="dc1-leaf-01", kind="trap", target=".1.3.6.1.6.3.1.1.5.1"))
check("それ以外の trap は機器を ALARM", calls[-1] == ("dc1-leaf-01", "", "ALARM"))
h.handler(ev("resolved", "splunk", device_id="dc1-leaf-01", kind="trap", target="x"))
check("trap の解消は機器が ALARM のときだけ UP（linkDown の DOWN は上書きしない）", calls[-1] == ("dc1-leaf-01", "", "UP", "ALARM"))
h.handler(ev("firing", "splunk", device_id="dc1-leaf-01", kind="link_down", target="?"))
check("IF が分からない linkDown は機器に付ける", calls[-1] == ("dc1-leaf-01", "", "DOWN"))
n = len(calls)
h.handler(ev("firing", "splunk", device_id="dc1-leaf-01", kind="bgp_down", target="10.255.0.1"))
check("bgp_down（gNMI）は BGP のセッションの頂点を set_layer_status で DOWN（set_status は呼ばない）", layer_calls[-1] == ("dc1-leaf-01", "bgp", "10.255.0.1", "DOWN") and len(calls) == n)
h.handler(ev("resolved", "splunk", device_id="dc1-leaf-01", kind="bgp_down", target="10.255.0.1"))
check("bgp_down の解消は同じ頂点を UP", layer_calls[-1] == ("dc1-leaf-01", "bgp", "10.255.0.1", "UP"))
h.handler(ev("firing", "splunk", device_id="dc1-leaf-01", kind="isis_down", target="ethernet-1/1.0"))
check("isis_down は IS-IS の隣接の頂点（target = サブインタフェース）", layer_calls[-1] == ("dc1-leaf-01", "isis", "ethernet-1/1.0", "DOWN"))
m = len(layer_calls)
check("target の無い bgp_down / isis_down は何もしない",
      "ignored" in h.apply({"status": "firing", "device_id": "dc1-leaf-01", "kind": "isis_down", "target": "?"})
      and "ignored" in h.apply({"status": "firing", "device_id": "dc1-leaf-01", "kind": "bgp_down", "target": ""}) and len(layer_calls) == m and len(calls) == n)
check("機器が無い・firing でも resolved でもない status は何もしない",
      "ignored" in h.apply({"status": "firing", "device_id": "?", "kind": "link_down", "target": "eth1"})
      and "ignored" in h.apply({"status": "firing", "device_id": "", "kind": "link_down", "target": "eth1"})
      and "ignored" in h.apply({"status": "pending", "device_id": "dc1-leaf-01", "kind": "link_down", "target": "eth1"}) and len(calls) == n)
# 機器名は受け手（rules.alerts_from_message）が短い小文字の名前に揃える。Grafana は sysName、Splunk は DEVICE_MAP を通した名前で来る
h.handler(ev("firing", device_id="DC1-LEAF-02.lab.example", kind="link_down", target="ethernet-1/2"))
check("機器名は FQDN でも大文字でも、短い小文字の名前で Neptune を引く", calls[-1] == ("dc1-leaf-02", "ethernet-1/2", "DOWN"))
n = len(calls)
two = {"Records": [{"Sns": {"Message": json.dumps({"source": "grafana", "alerts": [
    {"status": "firing", "device_id": "dc1-leaf-01", "kind": "link_down", "target": "eth1"},
    {"status": "resolved", "device_id": "dc1-leaf-02", "kind": "link_down", "target": "eth2"}]})}},
    {"Sns": {"Message": json.dumps({"source": "splunk", "alerts": [{"status": "firing", "device_id": "dc1-spine-01", "kind": "trap", "target": "x"}]})}}]}
check("1 通に何件か入っていても、Records が何件あっても、全部を順に書く（Grafana はグループごとに 1 通）",
      h.handler(two) == [{"updated": 1}] * 3 and calls[n:] == [("dc1-leaf-01", "eth1", "DOWN"), ("dc1-leaf-02", "eth2", "UP"), ("dc1-spine-01", "", "ALARM")])
n = len(calls)
check("読めないメッセージ（JSON でない・alerts が無い・Records が無い）は捨てて例外にしない（再試行しても読めない）",
      h.handler({"Records": [{"Sns": {"Message": "not json"}}, {"Sns": {"Message": json.dumps({"source": "grafana"})}}, {}]}) == []
      and h.handler({}) == [] and h.handler({"detail-type": "AnomalyOpened", "detail": {"device_id": "dc1-leaf-01", "kind": "link_down", "target": "eth1"}}) == []
      and len(calls) == n)


def _boom(*a, **k):
    raise OSError("neptune unreachable")


fake_graph.set_status = _boom
try:
    h.handler(ev("firing", device_id="dc1-leaf-01", kind="link_down", target="eth1")); raised = ""
except RuntimeError as e:
    raised = str(e)
check("Neptune に書けなければ最後に RuntimeError で落とす（Lambda の非同期の再試行に任せる。やり直しの合間に後の通知が来ると古い値に戻る）",
      "neptune" in raised and "OSError: neptune unreachable" in raised)

# ---- アラートの通知の履歴（Firehose → S3 Tables の alert_events。ALERT_STREAM が空なら送らない）
class FakeFirehose:
    """put_record_batch の偽物。calls は例外の回も数える。fails は呼び出しごとの届かない行の番号の集合（先頭から使う）、boom は毎回投げる例外"""
    def __init__(self):
        self.batches, self.fails, self.boom, self.calls = [], [], None, 0
    def put_record_batch(self, DeliveryStreamName, Records):
        self.calls += 1
        if self.boom:
            raise self.boom
        rows = [json.loads(r["Data"].decode()) for r in Records]
        self.batches.append((DeliveryStreamName, rows))
        bad = self.fails.pop(0) if self.fails else set()
        return {"FailedPutCount": len(bad), "RequestResponses": [
            {"ErrorCode": "ServiceUnavailableException", "ErrorMessage": "slow down"} if i in bad else {"RecordId": f"r{i}"} for i in range(len(rows))]}


fh = FakeFirehose()
h._cache["firehose"] = fh
waits = []
_saved_time = h.time
h.time = types.SimpleNamespace(time=_saved_time.time, sleep=waits.append)   # 送り直しの待ちを数えるだけにする（time モジュールそのものは替えない）
import logging
class _Cap(logging.Handler):
    def __init__(self):
        super().__init__(); self.records = []
    def emit(self, record):
        self.records.append(record)
    def at(self, level):
        return [r for r in self.records if r.levelno == level]
cap = _Cap(); h.log.addHandler(cap)
fake_graph.set_status = lambda dev, ifn="", status="DOWN", only_if="": (calls.append((dev, ifn, status) + ((only_if,) if only_if else ())) or {"updated": 1})
os.environ.pop("ALERT_STREAM", None)
h.handler(ev("firing", device_id="dc1-leaf-01", kind="link_down", target="ethernet-1/1", starts_at=1790000000))
check("ALERT_STREAM が空なら Firehose に送らない（alert_history=false の配備）", fh.batches == [])
os.environ["ALERT_STREAM"] = "nwc-alert-events"
pair = {"Records": [{"Sns": {"Message": json.dumps({"source": "grafana", "alerts": [
    {"status": "firing", "device_id": "dc1-leaf-01", "kind": "link_down", "target": "ethernet-1/1", "detail": "down", "starts_at": 1790000000},
    {"status": "resolved", "device_id": "dc1-leaf-01", "kind": "link_down", "target": "ethernet-1/1", "detail": "up", "starts_at": 1790000000}]})}}]}
h.handler(pair)
_ids = ["dc1-leaf-01#link_down#ethernet-1/1#grafana#firing#1790000000", "dc1-leaf-01#link_down#ethernet-1/1#grafana#resolved#1790000000"]
check("Grafana の firing と resolved（starts_at は同じ）は 1 回の put_record_batch に 2 行、event_id は status で分かれる",
      len(fh.batches) == 1 and fh.batches[0][0] == "nwc-alert-events" and [r["event_id"] for r in fh.batches[0][1]] == _ids)
check("行の列は rules.ALERT_EVENT_COLUMNS と同じ、時刻は ISO 8601 の UTC（starts_at は通知のまま、received_at は受けた時刻）",
      all(list(r) == [n for n, _ in h.rules.ALERT_EVENT_COLUMNS] for r in fh.batches[0][1])
      and fh.batches[0][1][0]["starts_at"] == "2026-09-21T14:13:20.000000Z"
      and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", fh.batches[0][1][0]["received_at"]) is not None)
h.handler(pair)
check("同じ通知をもう一度受けても Lambda では落とさず、同じ event_id の行をまた送る（重複は読む側が event_id で落とす）",
      len(fh.batches) == 2 and [r["event_id"] for r in fh.batches[1][1]] == _ids)
n = len(calls)
r = h.handler(ev("firing", device_id="?", kind="trap", target="?", starts_at=1790000000))
check("Neptune で無視した通知（機器の無いもの）も履歴には 1 行送る",
      len(fh.batches) == 3 and len(fh.batches[2][1]) == 1 and fh.batches[2][1][0]["device_id"] == "?" and "ignored" in r[0] and len(calls) == n)
check("送り切れたら待たず、ERROR も出さない", waits == [] and cap.at(logging.ERROR) == [])
# ---- Firehose の失敗（design.md の決定 6: Firehose の失敗では落とさない。届かなかった行だけを合わせて 3 回まで送り直し、残りは 1 行ずつ ERROR）
fake_graph.set_status = _boom
c = fh.calls
try:
    h.handler(pair); raised = ""
except RuntimeError as e:
    raised = str(e)
check("Neptune に書けなくても Firehose には送り（put_record_batch は呼ばれる）、Neptune の失敗だけで最後に RuntimeError",
      fh.calls == c + 1 and len(fh.batches[-1][1]) == 2 and "neptune" in raised and "OSError: neptune unreachable" in raised and "firehose" not in raised.lower())
fake_graph.set_status = lambda dev, ifn="", status="DOWN", only_if="": (calls.append((dev, ifn, status) + ((only_if,) if only_if else ())) or {"updated": 1})
fh.fails, c, n = [{1}], fh.calls, len(calls)
cap.records.clear()   # 上の Neptune の失敗は log.exception（ERROR）で出ている
r = h.handler(pair)
check("1 回目に 2 行中 1 行（2 行目）が届かなければ、2 回目はその 1 行だけを送る。0.2 秒待ち、例外にも ERROR にもしない",
      fh.calls == c + 2 and [len(b[1]) for b in fh.batches[-2:]] == [2, 1] and fh.batches[-1][1][0]["event_id"] == _ids[1]
      and waits == [0.2] and r == [{"updated": 1}] * 2 and len(calls) == n + 2 and cap.at(logging.ERROR) == [])
fh.boom, c, n = OSError("firehose unreachable"), fh.calls, len(calls)
waits.clear()
r = h.handler(pair)
lost = cap.at(logging.ERROR)
check("Firehose が毎回例外なら put_record_batch は 3 回、待ちは 0.2 秒と 0.4 秒、行ごとに ERROR を 1 つ出して例外にしない（Neptune は書く）",
      fh.calls == c + 3 and waits == [0.2, 0.4] and r == [{"updated": 1}] * 2 and len(calls) == n + 2 and len(lost) == 2)
check("ERROR は ALERT_EVENT_LOST のあとに行の JSON そのまま（Logs Insights で拾って戻せる）",
      all(x.getMessage().startswith("ALERT_EVENT_LOST {") for x in lost)
      and [json.loads(x.getMessage().split(" ", 1)[1])["event_id"] for x in lost] == _ids
      and all(list(json.loads(x.getMessage().split(" ", 1)[1])) == [n for n, _ in h.rules.ALERT_EVENT_COLUMNS] for x in lost))
fh.boom, fh.fails, c = None, [{0, 1}, {1}, {1}], fh.calls
waits.clear(); cap.records.clear()
h.handler(pair)
lost = cap.at(logging.ERROR)
check("3 回目まで届かなかった行だけを ERROR にする（届いた行は出さない）",
      fh.calls == c + 3 and [len(b[1]) for b in fh.batches[-3:]] == [2, 2, 1] and len(lost) == 1 and _ids[1] in lost[0].getMessage())


class _ShortAnswer(FakeFirehose):
    """FailedPutCount はあるのに RequestResponses の数が Records と合わない応答"""
    def put_record_batch(self, DeliveryStreamName, Records):
        r = super().put_record_batch(DeliveryStreamName, Records)
        return dict(r, RequestResponses=r["RequestResponses"][:1]) if r["FailedPutCount"] else r


short = _ShortAnswer()
short.fails = [{0}]   # 返る 1 件は 1 行目の失敗。数の合わない応答を順に当てると 1 行目だけを送り直してしまう
h._cache["firehose"] = short
waits.clear(); cap.records.clear()
h.handler(pair)
check("RequestResponses の数が合わなければどの行が落ちたか分からないので、全部を送り直す",
      [len(b[1]) for b in short.batches] == [2, 2] and waits == [0.2] and cap.at(logging.ERROR) == [])
h._cache["firehose"] = fh
_many = {"Records": [{"Sns": {"Message": json.dumps({"source": "splunk", "alerts": [
    {"status": "firing", "device_id": f"dc1-leaf-{i:02d}", "kind": "link_down", "target": "ethernet-1/1", "starts_at": 1790000000} for i in range(50)]})}}] * 11}
fh.batches.clear()
h.handler(_many)
check("1 回の呼び出しで 500 件を超えたら put_record_batch を 500 件ずつに分ける（API の上限）", [len(b[1]) for b in fh.batches] == [500, 50])
# ---- 捨てた通知（design.md の決定 2: alerts_from_message が捨てたものは行にせず、件数を WARNING に 1 回）
fh.batches.clear(); cap.records.clear()
c = fh.calls
h.handler(ev("firing", kind="link_down", target="eth1", starts_at=1790000000))
warns = cap.at(logging.WARNING)
check("device_id の無いアラートは put_record_batch に送らず、WARNING を 1 回（ALERT_DROPPED と件数）",
      fh.calls == c and len(warns) == 1 and "ALERT_DROPPED" in warns[0].getMessage() and "1 件" in warns[0].getMessage())
cap.records.clear()
h.handler({"Records": [{"Sns": {"Message": json.dumps({"source": "grafana", "alerts": [
    {"status": "firing", "device_id": "dc1-leaf-01", "kind": "link_down", "target": "eth1", "starts_at": 1790000000},
    {"status": "firing", "device_id": "dc1-leaf-01", "target": "eth1"},
    {"status": "pending", "device_id": "dc1-leaf-01", "kind": "link_down", "target": "eth1"}, "x"]})}}]})
warns = cap.at(logging.WARNING)
check("形の合わない要素（kind が無い・status が pending・dict でない）は数えて WARNING 1 回に、正しい 1 件だけ行にする",
      [len(b[1]) for b in fh.batches] == [1] and fh.batches[0][1][0]["kind"] == "link_down"
      and len(warns) == 1 and "ALERT_DROPPED" in warns[0].getMessage() and "3 件" in warns[0].getMessage())
fake_graph.set_status = lambda dev, ifn="", status="DOWN", only_if="": (calls.append((dev, ifn, status) + ((only_if,) if only_if else ())) or {"updated": 0, "unregistered": True})
fh.batches.clear()
h.handler(ev("firing", device_id="zz-ce-09", kind="link_down", target="eth1"))
check("未登録の機器・IF の異常は WARNING で UNREGISTERED をログに出す", calls[-1] == ("zz-ce-09", "eth1", "DOWN")
      and cap.records[-1].levelno == logging.WARNING and "UNREGISTERED" in cap.records[-1].getMessage())
check("未登録の機器の通知も履歴には 1 行送る", [len(b[1]) for b in fh.batches] == [1] and fh.batches[0][1][0]["device_id"] == "zz-ce-09")
fake_graph.set_status = lambda dev, ifn="", status="DOWN", only_if="": (calls.append((dev, ifn, status) + ((only_if,) if only_if else ())) or {"updated": 1})
h.handler(ev("resolved", device_id="dc1-leaf-01", kind="link_down", target="eth1"))
check("登録済みなら INFO（どの送り手のどのアラートかをログに残す）", cap.records[-1].levelno == logging.INFO and '"source": "grafana"' in cap.records[-1].getMessage())
cap.records.clear()
h.handler({"Records": [{"Sns": {"Message": "not json"}}]})
check("読めないメッセージは WARNING でログに出す（ALERT_DROPPED ではない）",
      len(cap.records) == 1 and cap.records[-1].levelno == logging.WARNING and "読めない" in cap.records[-1].getMessage()
      and "ALERT_DROPPED" not in cap.records[-1].getMessage())
check("Neptune には status_handler が NEPTUNE_CONFIG で作ったクライアントを graph._cache に入れてから書く", fake_graph._cache["client"] is _neptune_client)
# ---- Lambda の残り時間（design.md Round 2: 残りが Firehose の取り分 + Neptune 1 回の最大より少なければ、その通知から先は Neptune に書かない）
class _Ctx:
    """Lambda の context の偽物。get_remaining_time_in_millis は呼ばれるたびに left を先頭から返す（最後の値は使い回す）"""
    def __init__(self, *left):
        self.left, self.asked = list(left), 0
    def get_remaining_time_in_millis(self):
        self.asked += 1
        return self.left.pop(0) if len(self.left) > 1 else self.left[0]


_four = {"Records": [{"Sns": {"Message": json.dumps({"source": "splunk", "alerts": [
    {"status": "firing", "device_id": f"dc1-leaf-0{i}", "kind": "link_down", "target": "ethernet-1/1", "starts_at": 1790000000} for i in (1, 2, 3, 4)]})}}]}
fh.batches.clear(); cap.records.clear()
n = len(calls)
try:
    h.handler(_four, _Ctx(h.NEPTUNE_BUDGET_MS - 1)); raised = ""
except RuntimeError as e:
    raised = str(e)
skips = [x for x in cap.at(logging.WARNING) if "NEPTUNE_SKIPPED" in x.getMessage()]
check("Lambda の残りが 33 秒を切っていたら Neptune を呼ばず、4 行を Firehose に送り、WARNING の NEPTUNE_SKIPPED を 1 回出して最後に RuntimeError",
      len(calls) == n and [len(b[1]) for b in fh.batches] == [4] and len(skips) == 1
      and raised.startswith("neptune: Lambda の残り時間が足りず 4 件を書かなかった"))
fh.batches.clear(); cap.records.clear()
ctx = _Ctx(60000, h.NEPTUNE_BUDGET_MS, h.NEPTUNE_BUDGET_MS - 1, 60000)
try:
    h.handler(_four, ctx); raised = ""
except RuntimeError as e:
    raised = str(e)
skips = [x for x in cap.at(logging.WARNING) if "NEPTUNE_SKIPPED" in x.getMessage()]
check("途中で 33 秒を切ったら、その通知から先だけを飛ばす（ちょうど 33 秒は書く。飛ばしたあとは残り時間を見直さず、WARNING も 1 回）。行は 4 行とも送る",
      [c[0] for c in calls[n:]] == ["dc1-leaf-01", "dc1-leaf-02"] and ctx.asked == 3 and [len(b[1]) for b in fh.batches] == [4]
      and len(skips) == 1 and "dc1-leaf-03" in skips[0].getMessage() and "2 件を書かなかった" in raised)
fake_graph.set_status = _boom
fh.batches.clear(); cap.records.clear()
try:
    h.handler(_four, _Ctx(60000, h.NEPTUNE_BUDGET_MS - 1)); raised = ""
except RuntimeError as e:
    raised = str(e)
check("Neptune の失敗と打ち切りが重なっても、行は全部送り、例外の文の先頭に打ち切った件数を置く（後ろの切り詰めで消えない）",
      [len(b[1]) for b in fh.batches] == [4] and raised.startswith("neptune: Lambda の残り時間が足りず 3 件を書かなかった; neptune {")
      and "OSError: neptune unreachable" in raised)
fake_graph.set_status = lambda dev, ifn="", status="DOWN", only_if="": (calls.append((dev, ifn, status) + ((only_if,) if only_if else ())) or {"updated": 1})
n = len(calls)
check("残りが十分なら全部を書いて例外にしない", h.handler(_four, _Ctx(60000)) == [{"updated": 1}] * 4 and len(calls) == n + 4)
h.log.removeHandler(cap)
h.time = _saved_time
# 送る経路が FIREHOSE_CONFIG のクライアントを使うか（偽物を置き場に入れるだけだと、toolkit.client("firehose") に替えても通ってしまう）
_saved_boto3, _made, _fh2, _nep2 = h.boto3, [], FakeFirehose(), object()
h.boto3 = types.SimpleNamespace(client=lambda service, **kw: _made.append((service, kw)) or (_fh2 if service == "firehose" else _nep2))   # 資格情報を探しに行かない
h._cache["firehose"] = h._cache["neptune"] = None
h.toolkit._clients.pop("firehose", None)
h.handler(pair); h.handler(pair)
_cfg, _ncfg = h.FIREHOSE_CONFIG, h.NEPTUNE_CONFIG
_timeout = int(re.search(r"^\s*timeout\s*=\s*(\d+)", read("terraform", "pipeline", "graph", "sync.tf"), re.M).group(1))
check(f"Firehose へは FIREHOSE_CONFIG で 1 つだけ作ったクライアントで送り、botocore の再試行を切る（1 回）。3 回の接続と読みの待ち + 送り直しの待ちが Firehose の取り分（{h.FIREHOSE_SHARE} 秒）に収まる",
      [m for m in _made if m[0] == "firehose"] == [("firehose", {"region_name": h.toolkit.REGION, "config": _cfg})] and [len(b[1]) for b in _fh2.batches] == [2, 2]
      and "firehose" not in h.toolkit._clients and _cfg.retries.get("total_max_attempts") == 1
      and 3 * (_cfg.connect_timeout + _cfg.read_timeout) + sum(h.RETRY_WAITS) < h.FIREHOSE_SHARE)
check("Neptune へは NEPTUNE_CONFIG（接続 3 秒・読み 10 秒・再試行なし）で 1 つだけ作ったクライアントを graph._cache に入れて使い、agent/graph.py の既定（接続 10 秒・読み 60 秒）は変えない",
      [m for m in _made if m[0] == "neptune-graph"] == [("neptune-graph", {"region_name": h.toolkit.REGION, "config": _ncfg})] and fake_graph._cache["client"] is _nep2
      and (_ncfg.connect_timeout, _ncfg.read_timeout, _ncfg.retries) == (3, 10, {"total_max_attempts": 1, "mode": "standard"})
      and 'config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 2}))' in read("agent", "graph.py"))
check(f"Lambda graph-status の timeout は 60 秒で、打ち切りの閾値（Firehose の取り分 + Neptune 1 回の最大 = {h.NEPTUNE_BUDGET_MS} ミリ秒）より長い",
      _timeout == 60 and h.NEPTUNE_BUDGET_MS == (h.FIREHOSE_SHARE + _ncfg.connect_timeout + _ncfg.read_timeout) * 1000 == 33000 and h.NEPTUNE_BUDGET_MS < _timeout * 1000)
def _no_neptune(service, **kw):
    if service == "neptune-graph":
        raise OSError("neptune-graph のクライアントを作れない")
    return _fh2
h.boto3 = types.SimpleNamespace(client=_no_neptune)
h._cache["neptune"] = None
_fh2.batches.clear(); cap.records.clear(); h.log.addHandler(cap)
try:
    h.handler(pair); raised = ""
except RuntimeError as e:
    raised = str(e)
h.log.removeHandler(cap)
check("Neptune のクライアントを作るところで落ちても（_neptune() は通知ごとの try の中）、行は Firehose に送ってから RuntimeError で落とす",
      [len(b[1]) for b in _fh2.batches] == [2] and raised.count("OSError: neptune-graph のクライアントを作れない") == 2 and len(cap.at(logging.ERROR)) == 2)
h.boto3 = _saved_boto3
h._cache["firehose"] = None
h._cache["neptune"] = _neptune_client
fh.batches.clear()
os.environ.pop("ALERT_STREAM", None)

# ---- terraform/pipeline/graph の配線
tf = read("terraform", "pipeline", "graph", "sync.tf")
check("sync.tf は status_handler.py を index.py、agent/graph.py を graph.py で zip にする", 'graph/status_handler.py")' in tf and 'filename = "index.py"' in tf and 'agent/graph.py")' in tf and 'filename = "graph.py"' in tf)
# zip に入れ忘れても apply も plan も通り、実行時に ModuleNotFoundError で初めて分かる。だから「含まれている」ではなく「足りていない
# ものが無い」を見る: graph.py が import する agent/ のモジュール（いまは toolkit）が全部 source に並んでいるか
zipped = set(re.findall(r'filename = "(\w+)\.py"', tf))
needed = {m for m in re.findall(r"^import (\w+)$", read("agent", "graph.py"), re.M) if os.path.exists(os.path.join(ROOT, "agent", m + ".py"))}
check(f"status.zip は graph.py が import する agent/ のモジュールを全部入れる（足りない: {sorted(needed - zipped)}）", needed and not (needed - zipped))
# status_handler.py と rules.py が import するのは、zip の中のモジュールと標準ライブラリだけ（Lambda の実行環境に無いものを読むと起動で落ちる）。
# boto3 / botocore は Lambda の Python のランタイムに入っている（zip の toolkit.py も読む）
_imports = lambda text: set(re.findall(r"^(?:import|from) (\w+)", text, re.M))
check("status.zip は workflow/rules.py を rules.py で入れ、status_handler.py と rules.py は zip の中と標準ライブラリ（と boto3）しか import しない",
      'workflow/rules.py")' in tf and "rules" in zipped
      and _imports(read("graph", "status_handler.py")) - zipped <= {"json", "logging", "os", "time", "boto3", "botocore"}
      and _imports(read("workflow", "rules.py")) <= {"json", "re", "datetime"})
_loc = read("terraform", "pipeline", "graph", "locals.tf")
check("EventBridge のルールは無く、土台（base/core）のトピック <接頭辞>-alerts を Lambda が購読する（接頭辞ごとのトピックなので他の人のアラートを拾わない）",
      "aws_cloudwatch_event_" not in tf and "events.amazonaws.com" not in tf
      and re.search(r'resource "aws_sns_topic_subscription" "status" \{\s*topic_arn = local\.alerts_topic_arn\s*protocol  = "lambda"\s*endpoint  = aws_lambda_function\.status\.arn', tf) is not None
      and 'alerts_topic_arn = try(data.terraform_remote_state.main.outputs.alerts_topic_arn, "")' in _loc)
check("古い土台（alerts_topic_arn の出力が無い）では、購読の precondition が plan を止める（空の ARN で apply して API のエラーにしない）",
      'condition     = local.alerts_topic_arn != ""' in tf and "depends_on = [aws_lambda_permission.status]" in tf)
check("Lambda は VPC の中で NEPTUNE_GRAPH_ID を環境変数で持ち、ロググループは retention 付き",
      "vpc_config" in tf and "NEPTUNE_GRAPH_ID = aws_neptunegraph_graph.graph.id" in tf and "NEPTUNE_ENDPOINT" not in tf and "retention_in_days = var.log_retention_days" in tf)
_nep = read("terraform", "pipeline", "graph", "neptune.tf")
_core_sg = read("terraform", "base", "core", "security_groups.tf")
check("グラフは Neptune Analytics（公開しない・レプリカ無し）で、ID を SSM の neptune-graph-id に書く。Neptune Database のクラスタはもう無い（2026-10-04）",
      re.search(r'resource "aws_neptunegraph_graph" "graph" \{', _nep) is not None and "public_connectivity = false" in _nep and "replica_count       = 0" in _nep
      and "provisioned_memory  = var.provisioned_memory" in _nep and 'name        = "/${local.name_prefix}/neptune-graph-id"' in _nep
      and "aws_neptune_cluster" not in _nep + tf and not os.path.exists(os.path.join(ROOT, "terraform", "pipeline", "graph", "network.tf")))
check("Lambda は base/core の lambda の SG を使い、graph は SG もルールも作らない。Neptune へは土台の neptune-graph-data のエンドポイント（443）で届くので、neptune の SG と 8182 の行は無い",
      "security_group_ids = [local.lambda_sg_id]" in tf and "aws_vpc_security_group_egress_rule" not in tf and "aws_vpc_security_group_ingress_rule" not in tf
      and "neptune_sg_id" not in _loc + _nep and 'to = "neptune"' not in _core_sg and "port = 8182" not in _core_sg
      and '"neptune-graph-data"' in read("terraform", "base", "core", "variables.tf")
      and "pipeline/graph) add_endpoints neptune-graph-data\n" in read("ops", "up.sh") and read("ops", "up.sh").count("    pipeline/graph)") == 1)
check("Lambda のロールは neptune-graph の Read / Write / Delete をこのグラフにだけ（他のサービスは持たない）",
      all(f'"neptune-graph:{a}DataViaQuery"' in tf for a in ("Read", "Write", "Delete")) and "neptune-graph:*" not in tf and "neptune-db" not in tf
      and "resources = [aws_neptunegraph_graph.graph.arn]" in tf)
_acc = read("terraform", "pipeline", "graph", "access.tf")
check("Runtime と Web のロールにも neptune-graph の読み書きをこのグラフにだけ付ける",
      all(f'"neptune-graph:{a}DataViaQuery"' in _acc for a in ("Read", "Write", "Delete")) and "Resource = aws_neptunegraph_graph.graph.arn" in _acc and "neptune-db" not in _acc)
check("SNS から Lambda を呼ぶ permission（呼べるのは土台のトピックだけ）", 'principal     = "sns.amazonaws.com"' in tf and "source_arn    = local.alerts_topic_arn" in tf)
_var = read("terraform", "pipeline", "graph", "variables.tf")
check("variables.tf に log_retention_days と provisioned_memory（既定 16 m-NCU）。Neptune Database の instance_class / engine_version は無い",
      'variable "log_retention_days"' in _var and re.search(r'variable "provisioned_memory" \{[^}]*default     = 16', _var) is not None
      and "instance_class" not in _var and "engine_version" not in _var)
# アラートの通知の履歴: alert_history = false なら ALERT_STREAM は空で firehose の権限も無い。true なら送り先の 1 本だけ
check("variables.tf の alert_history は bool で既定 false（analytics を作らない回は送らない）",
      re.search(r'variable "alert_history" \{[^}]*type\s*=\s*bool\s*default\s*=\s*false', _var) is not None)
check("alert_history が false なら ALERT_STREAM は空、true なら <接頭辞>-alert-events（analytics の Firehose と同じ名前）",
      'ALERT_STREAM     = var.alert_history ? local.alert_stream : ""' in tf and 'alert_stream = "${local.name_prefix}-alert-events"' in tf
      and 'name        = "${local.name_prefix}-alert-events"' in read("terraform", "pipeline", "analytics", "history.tf"))
_ah = re.search(r'dynamic "statement" \{\s*for_each = var\.alert_history \? \[1\] : \[\]\s*content \{(.*?)\n    \}', tf, re.S)
check("firehose の権限は alert_history が true のときだけで、PutRecordBatch を <接頭辞>-alert-events の ARN だけに",
      _ah is not None and 'actions   = ["firehose:PutRecordBatch"]' in _ah.group(1)
      and 'resources = ["arn:${local.partition}:firehose:${var.region}:${local.account_id}:deliverystream/${local.alert_stream}"]' in _ah.group(1)
      and tf.count("firehose:") == 2 and "firehose:*" not in tf)
check("up.sh は analytics を作る回（SKIP_ANALYTICS が空）だけ graph に -var alert_history=true を渡す",
      'if [ -z "$SKIP_ANALYTICS" ]; then GRAPH_VARS=(-var alert_history=true); else GRAPH_VARS=(); fi' in up
      and '( tf_apply_only pipeline/graph ${GRAPH_VARS[@]+"${GRAPH_VARS[@]}"} )' in up)
check("graph-status のロールにも閉域の Deny を付ける（firehose を持つので、VPC の外から履歴の行を書かせない）。NETWORK_PERIMETER=0 か古い土台なら付けない",
      re.search(r'resource "aws_iam_role_policy_attachment" "status_perimeter" \{\s*count = local\.perimeter_policy_arn != "" \? 1 : 0\s*'
                r'role\s*= aws_iam_role\.status\.name\s*policy_arn = local\.perimeter_policy_arn\s*\}', tf) is not None
      and 'perimeter_policy_arn = try(data.terraform_remote_state.main.outputs.network_perimeter_policy_arn, "")' in _loc)
print(f"通過 {passed} / 失敗 0")
