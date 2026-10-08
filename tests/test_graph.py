"""app/agentcore/graph.py と topology.py の Neptune 経路の模擬テスト。boto3 を差し替え、送った openCypher（クエリとパラメータ）と読み替えを確かめる。
実行は python3 tests/test_graph.py（PyYAML が要る）。"""
import importlib.util, io, json, os, sys, types

AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "agentcore")
sys.path.insert(0, AGENT)

class ClientError(Exception):
    pass
class BotoCoreError(Exception):
    pass

state = {"queries": [], "params": [], "answer": {}, "fail": False, "ssm": {}}

def node(v):
    """Neptune Analytics が返す頂点の形（{id, label, property…} から）"""
    return {"~id": v["id"], "~entityType": "node", "~labels": [v["label"]], "~properties": {k: x for k, x in v.items() if k not in ("id", "label")}}

def nodes(vs):
    return [{"n": node(v)} for v in vs]

def edges(es):
    """回線の辺の行（MATCH (a)-[l:link]->(b) RETURN id(a) AS a, id(b) AS b, l）"""
    return [{"a": e["a"], "b": e["b"], "l": {"~entityType": "relationship", "~type": "link", "~start": e["a"], "~end": e["b"],
                                             "~properties": {k: x for k, x in e.items() if k not in ("a", "b")}}} for e in es]

class FakeClient:
    def __init__(self, name, **kw):
        self.name = name
        self.kw = kw
    def get_parameter(self, Name):
        if Name in state["ssm"]:
            return {"Parameter": {"Value": state["ssm"][Name]}}
        raise ClientError("ParameterNotFound")
    def execute_query(self, graphIdentifier, queryString, language, parameters=None):
        assert language == "OPEN_CYPHER" and graphIdentifier == os.environ["NEPTUNE_GRAPH_ID"]
        state["queries"].append(queryString); state["params"].append(parameters or {})
        if state["fail"]:
            raise BotoCoreError("boom")
        rows = [{"n": 0}]
        for key, val in state["answer"].items():   # 判定は辞書の順（先に書いた鍵が勝つ）
            if key in queryString:
                rows = val
                break
        return {"payload": io.BytesIO(json.dumps({"results": rows}).encode())}

boto3 = types.ModuleType("boto3"); boto3.client = lambda name, **kw: FakeClient(name, **kw)
botocore = types.ModuleType("botocore"); exc = types.ModuleType("botocore.exceptions")
exc.ClientError = ClientError; exc.BotoCoreError = BotoCoreError; botocore.exceptions = exc
cfg = types.ModuleType("botocore.config"); cfg.Config = lambda **kw: kw; botocore.config = cfg
sys.modules.update({"boto3": boto3, "botocore": botocore, "botocore.exceptions": exc, "botocore.config": cfg})

passed = 0
def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)

def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(AGENT, f"{name}.py"))
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m

def reset(**answer):
    state.update(answer=answer, queries=[], params=[])

def calls():
    return list(zip(state["queries"], state["params"]))

def created(label):
    """UNWIND で作った label の頂点の行（[{id, props}]）を全部"""
    return [r for q, p in calls() if q.startswith(f"UNWIND $rows AS r CREATE (n:`{label}`") for r in p["rows"]]

def created_links():
    return [r for q, p in calls() if "CREATE (a)-[l:link]->(b)" in q and q.startswith("UNWIND") for r in p["rows"]]

DEV, IFS, LINKS = "MATCH (n:`device`) RETURN n", "MATCH (n:`interface`) RETURN n", "MATCH (a)-[l:link]->(b) RETURN id(a)"
LAYER_E = "RETURN type(e) AS label"
SET_ST = "SET n.status = $st RETURN n.registered AS registered"
REG = "RETURN n.registered AS registered"
UNREG = "WHERE n.registered = false RETURN n"

# ---- 未配備
os.environ.pop("NEPTUNE_GRAPH_ID", None); os.environ["PARAM_PREFIX"] = "/x"; os.environ["TOPOLOGY_TTL"] = "0"
graph = load("graph")
check("SSM に無ければ未配備", not graph.configured())
topology = load("topology")
check("未配備なら静的データ", topology.SOURCE == "static" and len(topology.DEVICES) == 8)

# ---- 読み替えと埋め込む名前
check("_node は ~id / ~labels / ~properties を {id, label, property…} に",
      graph._node(node({"id": "a", "label": "device", "site": "x"})) == {"id": "a", "label": "device", "site": "x"})
def _raises(f):
    try:
        f()
    except ValueError:
        return True
    return False
check("_ident は英数字と _ の名前だけを ` で囲んで通す（クエリに値を埋め込ませない）",
      graph._ident("bgp_session") == "`bgp_session`" and _raises(lambda: graph._ident("x` DETACH DELETE n //")) and _raises(lambda: graph._ident("a b")))
check("_props は None と空文字を落とし、False と 0 は残す",
      graph._props({"a": None, "b": "", "c": False, "d": 0, "e": "x"}, ("a", "b", "c", "d", "e", "f")) == {"c": False, "d": 0, "e": "x"})

# ---- 配備あり（環境変数）
os.environ["NEPTUNE_GRAPH_ID"] = "g-abc1234567"
graph = load("graph")
check("環境変数で配備あり（neptune-graph のクライアント。接続先はグラフの ID から boto3 が決めるので endpoint_url は渡さない）",
      graph.configured() and graph._client().name == "neptune-graph" and "endpoint_url" not in graph._client().kw)
# graph-status の Lambda は status_handler._neptune が graph._cache["client"] に NEPTUNE_CONFIG のクライアントを入れる（test_sync は graph を偽物にするので、ここで本物を見る）
class _Preset(FakeClient):
    used = 0
    def execute_query(self, **kw):
        _Preset.used += 1
        return super().execute_query(**kw)
_made, _pre = graph._client(), _Preset("neptune-graph")
graph._cache["client"] = _pre
reset(); graph.query("RETURN 1")
check("_client() と query は graph._cache[\"client\"] に先に入っているクライアントを使い、作り直さない（鍵を変えると graph-status の Lambda は黙って既定の待ちに戻る）",
      graph._client() is _pre and _Preset.used == 1 and state["queries"] == ["RETURN 1"])
graph._cache["client"] = _made
devs = [{"id": "a-ce-01", "label": "device", "hostname": "a-ce-01", "site": "a", "role": "leaf", "asn": 65001, "mgmt_ip": "203.0.113.11", "enabled": True},
        {"id": "b-ce-01", "label": "device", "hostname": "b-ce-01", "site": "b", "role": "leaf", "mgmt_ip": "203.0.113.12", "enabled": False}]
ifs = [{"id": "a-ce-01#eth0", "label": "interface", "device_id": "a-ce-01", "name": "eth0", "address": "203.0.113.11"},
       {"id": "a-ce-01#eth1", "label": "interface", "device_id": "a-ce-01", "name": "eth1", "address": "172.16.1.2", "status": "DOWN"},
       {"id": "gone#eth1", "label": "interface", "device_id": "gone", "name": "eth1"}]
links = [{"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth1", "b_if": "eth1", "kind": "l2", "role": "primary", "bandwidth_mbps": 1000}]
lv_ip = [{"id": "a-ce-01#eth1.0", "label": "ip_interface", "layer": "ip", "device_id": "a-ce-01", "interface_id": "a-ce-01#eth1", "name": "eth1.0", "address": "172.16.1.2"}]
lv_bgp = [{"id": "a-ce-01#bgp#10.255.0.9", "label": "bgp_session", "layer": "evpn", "device_id": "a-ce-01", "peer_address": "10.255.0.9", "status": "DOWN", "registered": False}]
le = [{"label": "over", "from": "a-ce-01#eth1.0", "to": "a-ce-01#eth1"}]
def topo_answer():
    return {DEV: nodes(devs), IFS: nodes(ifs), LINKS: edges(links), "MATCH (n:`ip_interface`) RETURN n": nodes(lv_ip),
            "MATCH (n:`bgp_session`) RETURN n": nodes(lv_bgp), LAYER_E: le, "RETURN n": []}
reset(**topo_answer())
d, l = graph.load_topology()
y = graph.load_layers()
check("読むクエリにパラメータは無く、上の層の辺は型の「どれか」で 1 本",
      all(p == {} for p in state["params"]) and "MATCH (a)-[e:over|peer|tunnel|attach|segment]->(b) RETURN type(e) AS label, id(a) AS from, id(b) AS to" in state["queries"])
check("load_layers は上の層の頂点（registered を付ける。未登録は False）と辺（from / to は両端の id）を id の順で返す",
      [v["id"] for v in y["vertices"]] == ["a-ce-01#bgp#10.255.0.9", "a-ce-01#eth1.0"] and y["vertices"][0]["registered"] is False and y["vertices"][0]["status"] == "DOWN"
      and y["vertices"][1]["registered"] is True and y["vertices"][1]["label"] == "ip_interface" and y["edges"] == [{"label": "over", "from": "a-ce-01#eth1.0", "to": "a-ce-01#eth1"}])
check("load_topology はインタフェースの頂点を device_id で機器に付け、機器の無いものは捨てる",
      d[0]["interfaces"] == [{"name": "eth0", "address": "203.0.113.11", "lag": "", "status": None, "registered": True},
                             {"name": "eth1", "address": "172.16.1.2", "lag": "", "status": "DOWN", "registered": True}]
      and d[1]["interfaces"] == [] and d[0]["registered"] is True)
check("load_topology は device_id と a/b を組み立てる", d[0]["device_id"] == "a-ce-01" and d[0]["asn"] == 65001 and d[1]["enabled"] is False and l == [{"a_if": "eth1", "b_if": "eth1", "kind": "l2", "role": "primary", "bandwidth_mbps": 1000, "status": None, "a": "a-ce-01", "b": "b-ce-01"}])

topology = load("topology")
check("配備ありなら Neptune から組む", topology.SOURCE == "neptune" and [x["device_id"] for x in topology.DEVICES] == ["a-ce-01", "b-ce-01"])
check("隣接も Neptune の辺から", topology.neighbors("a-ce-01")["neighbors"][0]["device_id"] == "b-ce-01")
check("topology_graph の source は neptune", topology.topology_graph()["source"] == "neptune")

# ---- 読めなければ静的へ
state["fail"] = True
check("Neptune が落ちていれば静的データに戻る", topology.reload(force=True) == "static" and len(topology.DEVICES) == 8)
state["fail"] = False
reset(**{"RETURN n": [], LINKS: [], LAYER_E: []})
check("Neptune が空なら静的データを見せて neptune-empty", topology.reload(force=True) == "neptune-empty" and len(topology.DEVICES) == 8)

# ---- 書き込みの openCypher（値はパラメータ）
reset()
check("add_link は同じ機器を拒む", "error" in graph.add_link("a", "e1", "a", "e2") and not state["queries"])
reset(**{"WHERE id(n) IN $ids RETURN id(n) AS id": [{"id": "a-ce-01"}, {"id": "b-ce-01"}], "count(l)": [{"n": 0}]})
r = graph.add_link("b-ce-01", "eth2", "a-ce-01", "eth3", "fabric", "secondary", 100)
q, p = calls()[-1]
check("add_link は a < b に正規化して辺を作る（property は 1 つの map）", r.get("added") == "a-ce-01 eth3 - b-ce-01 eth2"
      and q == "MATCH (a), (b) WHERE id(a) = $a AND id(b) = $b CREATE (a)-[l:link]->(b) SET l += $props"
      and p == {"a": "a-ce-01", "b": "b-ce-01", "props": {"a_if": "eth3", "b_if": "eth2", "kind": "fabric", "role": "secondary", "bandwidth_mbps": 100}})
check("add_link は先に「同じ両端 + a_if」の辺を数える", calls()[1] == (
      "MATCH (a:device)-[l:link]->(b:device) WHERE id(a) = $a AND id(b) = $b AND l.a_if = $a_if RETURN count(l) AS n", {"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth3"}))
reset(**{"WHERE id(n) IN $ids RETURN id(n) AS id": [{"id": "a-ce-01"}, {"id": "b-ce-01"}], "count(l)": [{"n": 1}]})
check("同じ回線がもうあれば add_link は error", "もうある" in graph.add_link("a-ce-01", "eth3", "b-ce-01", "eth2")["error"] and len(state["queries"]) == 2)
reset(**{"WHERE id(n) IN $ids RETURN id(n) AS id": []})
check("機器が無ければ add_link は error", "機器が無い" in graph.add_link("x", "e", "y", "e")["error"])
reset(**{"count(l)": [{"n": 1}]})
check("remove_link は数えてから辺を消す", graph.remove_link("b-ce-01", "a-ce-01", "eth3") == {"removed": 1} and calls()[-1] == (
      "MATCH (a)-[l:link]->(b) WHERE id(a) = $a AND id(b) = $b AND l.a_if = $a_if DELETE l", {"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth3"}))
reset(**{REG: []})
check("add_device は property を map で渡し、id は `~id` に入れる", graph.add_device("c-ce-01", "c", "ce", "203.0.113.15", 65003) == {"added": "c-ce-01"}
      and calls()[-1] == ("UNWIND $rows AS r CREATE (n:`device` {`~id`: r.id}) SET n += r.props",
                          {"rows": [{"id": "c-ce-01", "props": {"hostname": "c-ce-01", "site": "c", "role": "ce", "asn": 65003, "mgmt_ip": "203.0.113.15", "enabled": False}}]}))
check("remove_device は無ければ error", "error" in graph.remove_device("zzz"))
reset(**{REG: [{"registered": None}]})
check("remove_device はインタフェースの頂点も消す（辺でつないでいない）", graph.remove_device("a-ce-01") == {"removed": "a-ce-01"}
      and calls()[1:] == [("MATCH (n) WHERE id(n) = $id DETACH DELETE n", {"id": "a-ce-01"}),
                          ("MATCH (n:interface) WHERE n.device_id = $id DETACH DELETE n", {"id": "a-ce-01"})])
check("add_device は登録済みなら error", "error" in graph.add_device("a-ce-01", "a", "ce"))
reset(**{REG: [{"registered": False}], "RETURN n.status AS status": [{"status": "ALARM"}]})
r = graph.add_device("zz-ce-09", "zz", "ce")
check("add_device は未登録の頂点を置き換え、status を引き継ぐ", r == {"added": "zz-ce-09", "replaced_unregistered": True}
      and calls()[2] == ("MATCH (n) WHERE id(n) = $id DETACH DELETE n", {"id": "zz-ce-09"})
      and state["params"][3]["rows"][0]["props"]["status"] == "ALARM" and state["params"][3]["rows"][0]["id"] == "zz-ce-09")
reset(**{"count(": [{"n": 3}]})
check("count は登録済みの機器・IF・回線、上の層の頂点と辺、未登録の頂点を数える", graph.count() == {"devices": 3, "interfaces": 3, "links": 3, "layers": 15, "layer_edges": 3, "unregistered": 3}
      and state["queries"][0] == "MATCH (n:device) WHERE n.registered IS NULL RETURN count(n) AS n" and state["queries"][-1] == "MATCH (n) WHERE n.registered = false RETURN count(n) AS n"
      and "MATCH (n:`bgp_session`) WHERE n.registered IS NULL RETURN count(n) AS n" in state["queries"] and len(state["queries"]) == 10)
spec = importlib.util.spec_from_file_location("lab_topology", os.path.join(AGENT, "..", "containerlab", "lab_topology.py"))
lt = importlib.util.module_from_spec(spec); spec.loader.exec_module(lt)
lab_devices, lab_links, lab_layers = lt.load(os.path.join(AGENT, "..", "containerlab"))
n_if = sum(len(x["interfaces"]) for x in lab_devices)
placeholders = [{"id": "dc1-leaf-01", "label": "device", "registered": False, "role": "unknown", "status": "ALARM"},
                {"id": "dc1-leaf-01#ethernet-1/1", "label": "interface", "registered": False, "device_id": "dc1-leaf-01", "name": "ethernet-1/1", "status": "DOWN"},
                {"id": "zz-ce-09", "label": "device", "registered": False, "role": "unknown", "status": "ALARM"}]
reset(**{UNREG: nodes(placeholders), SET_ST: [{"registered": None}], "count(": [{"n": 1}]})
r = graph.seed(lab_devices, lab_links)
qs = state["queries"]
check("seed は未登録の頂点を読み、登録済みを消してから、今回登録される未登録の頂点だけ消す",
      qs[0] == "MATCH (n) WHERE n.registered = false RETURN n"
      and qs[1:3] == ["MATCH (n:device) WHERE n.registered IS NULL DETACH DELETE n", "MATCH (n:interface) WHERE n.registered IS NULL DETACH DELETE n"]
      and calls()[3] == ("MATCH (n) WHERE id(n) IN $ids DETACH DELETE n", {"ids": ["dc1-leaf-01", "dc1-leaf-01#ethernet-1/1"]}))
check(f"seed は lab の 8 台と全インタフェース {n_if} 個、12 本を UNWIND でまとめて作る（機器・IF・回線で 1 本ずつ）", n_if > 20
      and len(created("device")) == 8 and len(created("interface")) == n_if and len(created_links()) == 12
      and sum(q.startswith("UNWIND") for q in qs) == 3
      and {"id": "dc1-leaf-01#mgmt0", "props": {"device_id": "dc1-leaf-01", "name": "mgmt0", "address": "203.0.113.31"}} in created("interface")
      and {"id": "dc1-leaf-01#ethernet-1/3", "props": {"device_id": "dc1-leaf-01", "name": "ethernet-1/3", "lag": "lag1"}} in created("interface")
      and not created("ip_interface"))
check("回線の行は a < b に揃えた両端の id と property", all(x["a"] < x["b"] and set(x["props"]) <= {"a_if", "b_if", "kind", "role", "bandwidth_mbps"} for x in created_links())
      and "UNWIND $rows AS r MATCH (a:device), (b:device) WHERE id(a) = r.a AND id(b) = r.b CREATE (a)-[l:link]->(b) SET l += r.props" in qs)
check("seed は status を入れず、置き換えた未登録の頂点の UP でない status だけ引き継ぐ",
      not any("status" in x["props"] for x in created("device") + created("interface") + created_links())
      and [c for c in calls() if c[1].get("st")] == [
          ("MATCH (n) WHERE id(n) = $id SET n.status = $st RETURN n.registered AS registered", {"id": "dc1-leaf-01", "st": "ALARM"}),
          ("MATCH (a)-[l:link]->(b) WHERE (id(a) = $dev AND l.a_if = $ifn) OR (id(b) = $dev AND l.b_if = $ifn) SET l.status = $st RETURN count(l) AS n",
           {"dev": "dc1-leaf-01", "ifn": "ethernet-1/1", "st": "DOWN"}),
          ("MATCH (n) WHERE id(n) = $id SET n.status = $st RETURN n.registered AS registered", {"id": "dc1-leaf-01#ethernet-1/1", "st": "DOWN"})])
reset(**{UNREG: [], "count(": [{"n": 1}]})
r = graph.seed(*topology.load_static())
check("静的データ（インタフェースの一覧が無い）でも seed は 8 台と 12 本", len(created("device")) == 8 and not created("interface") and len(created_links()) == 12
      and not any(p.get("st") for p in state["params"]))
reset(**{UNREG: [], "count(": [{"n": 1}]})
graph.seed([{"device_id": "a"}, {"device_id": "b"}], [{"a": "b", "a_if": "e1", "b": "a", "b_if": "e2"}, {"a": "a", "a_if": "e2", "b": "b", "b_if": "e1"},
                                                     {"a": "a", "a_if": "e3", "b": "zz", "b_if": "e1"}, {"a": "a", "a_if": "e4", "b": "a", "b_if": "e5"}])
check("seed は同じ回線の 2 本目・端の機器が一覧に無い回線・両端が同じ回線を入れない",
      created_links() == [{"a": "a", "b": "b", "props": {"a_if": "e2", "b_if": "e1", "kind": "l2"}}])
graph.CHUNK, _chunk = 3, graph.CHUNK
reset(**{UNREG: [], "count(": [{"n": 1}]})
graph.seed(lab_devices, [])
check("UNWIND は CHUNK 行ずつに分ける（機器が増えても 1 本のクエリが大きくなりすぎない）",
      [len(p["rows"]) for q, p in calls() if q.startswith("UNWIND $rows AS r CREATE (n:`device`")] == [3, 3, 2])
graph.CHUNK = _chunk
# 上の層（IP 層 / EVPN・BGP 層）は layers を渡したときだけ。辺は両端の頂点（物理層の interface も含む）があるものだけ張る
layer_ph = [{"id": "dc1-leaf-01#bgp#10.255.0.1", "label": "bgp_session", "registered": False, "device_id": "dc1-leaf-01", "status": "DOWN"}]
reset(**{"MATCH (n:`bgp_session`) WHERE n.registered = false RETURN n": nodes(layer_ph), UNREG: [], "RETURN n": [], "count(": [{"n": 1}]})
r = graph.seed(lab_devices, lab_links, lab_layers)
qs = state["queries"]
n_lv, n_le = len(lab_layers["vertices"]), len(lab_layers["edges"])
layer_rows = [x for lb in graph.LAYER_LABELS for x in created(lb)]
edge_rows = [x for q, p in calls() if q.startswith("UNWIND $rows AS r MATCH (a), (b)") for x in p["rows"]]
check(f"seed に layers を渡すと上の層の {n_lv} 頂点と {n_le} 辺も入れ、未登録の同じ id の頂点を消して status を引き継ぐ",
      n_lv == 62 and n_le == 84 and len(created("device")) == 8 and len(layer_rows) == n_lv and len(created_links()) == 12 and len(edge_rows) == n_le
      and ("MATCH (n) WHERE id(n) IN $ids DETACH DELETE n", {"ids": ["dc1-leaf-01#bgp#10.255.0.1"]}) in calls()
      and [x["id"] for x in layer_rows if "status" in x["props"]] == ["dc1-leaf-01#bgp#10.255.0.1"]
      and r.get("layers") == 5 and r.get("layer_edges") == 1 and "skipped_edges" not in r)
check("上の層の頂点は属性を全部 property に、id と label は付けない。辺は型ごとに 1 本のクエリ",
      {"id": "dc1-leaf-01#bgp#10.255.0.1", "props": {"layer": "evpn", "device_id": "dc1-leaf-01", "peer_address": "10.255.0.1", "status": "DOWN"}}
      in [{"id": x["id"], "props": {k: v for k, v in x["props"].items() if k in ("layer", "device_id", "peer_address", "status")}} for x in created("bgp_session")]
      and not any("id" in x["props"] or "label" in x["props"] for x in layer_rows)
      and "UNWIND $rows AS r MATCH (a), (b) WHERE id(a) = r.f AND id(b) = r.t CREATE (a)-[:`over`]->(b)" in qs)
reset(**{"RETURN n": [], "RETURN id(n) AS id": [], "count(": [{"n": 1}]})
r = graph.seed_layers({"vertices": [{"id": "x#bgp#1", "label": "bgp_session", "device_id": "x"}], "edges": [{"label": "over", "from": "x#bgp#1", "to": "x#eth0.0"}]})
check("seed_layers は片端の無い辺を張らずに skipped_edges に数える", r.get("skipped_edges") == 1 and not any("]->(b)" in q and q.startswith("UNWIND") for q in state["queries"]))

# ---- Neo4j（OSS 版）の索引と未登録の頂点の読み（2026-10-08）。Neo4j はラベルの無い property の索引を持てないので、ラベルごとに分けて送る
_q, _sent4, _ans4 = graph.query, [], {}
def _query4(cypher, **params):
    _sent4.append((cypher, params))
    for key, val in _ans4.items():   # 判定は辞書の順（先に書いた鍵が勝つ）
        if key in cypher:
            return val
    return [{"n": 0}]
UNREG4 = "MATCH (n:`{}`) WHERE n.registered = false RETURN {}"
try:
    graph.BACKEND, graph.query = "neo4j", _query4
    _ans4.update({UNREG4.format("device", "count"): [{"n": 2}], UNREG4.format("bgp_session", "count"): [{"n": 3}]})
    r = graph.count()
    unreg4 = [q for q, _ in _sent4 if "registered = false" in q]
    check("neo4j: count は未登録の頂点をラベルごとに数えて足す（ラベル無しの MATCH (n) は registered の索引を使えず全部の頂点を読む）",
          r["unregistered"] == 5 and unreg4 == [UNREG4.format(x, "count(n) AS n") for x in graph._LABELS]
          and not any(q.startswith("MATCH (n) WHERE n.registered") for q, _ in _sent4))
    _sent4.clear(); _ans4.clear()
    _ans4.update({UNREG4.format("device", "n"): nodes([placeholders[0], placeholders[2]]), UNREG4.format("interface", "n"): nodes([placeholders[1]]),
                  SET_ST: [{"registered": None}]})
    graph.seed(lab_devices, lab_links)
    qs4 = [q for q, _ in _sent4]
    check("neo4j: seed は置き換える機器とインタフェースだけ、ラベルごとに未登録の頂点を読み、置き換えるものをラベルごとに消す",
          qs4[:2] == [UNREG4.format("device", "n"), UNREG4.format("interface", "n")]
          and not any(q.startswith("MATCH (n) WHERE n.registered") for q in qs4)
          and ("MATCH (n:`device`) WHERE id(n) IN $ids DETACH DELETE n", {"ids": ["dc1-leaf-01"]}) in _sent4
          and ("MATCH (n:`interface`) WHERE id(n) IN $ids DETACH DELETE n", {"ids": ["dc1-leaf-01#ethernet-1/1"]}) in _sent4
          and sorted(p["st"] for _, p in _sent4 if p.get("st")) == ["ALARM", "DOWN", "DOWN"])
finally:
    graph.BACKEND, graph.query = "neptune", _q

class _Driver4:
    def __init__(self):
        self.sent = []
    def execute_query(self, q, database_=None, **kw):
        self.sent.append(q)
_d4 = _Driver4()
graph._cache["schema"] = None
try:
    graph._neo4j_schema(_d4)
    _first4 = list(_d4.sent)
    graph._neo4j_schema(_d4)
finally:
    graph._cache["schema"] = None
check("neo4j: スキーマは制約のあとに全ラベルの registered と interface の device_id の索引を張り（IF NOT EXISTS）、SCHEMA_TTL の内は張り直さない",
      _first4 == [f"CREATE CONSTRAINT nwc_{x}_id IF NOT EXISTS FOR (n:`{x}`) REQUIRE n.id IS UNIQUE" for x in graph._LABELS]
      + [f"CREATE INDEX nwc_{x}_registered IF NOT EXISTS FOR (n:`{x}`) ON (n.registered)" for x in graph._LABELS]
      + ["CREATE INDEX nwc_interface_device_id IF NOT EXISTS FOR (n:`interface`) ON (n.device_id)"]
      and len(_first4) == 17 and _d4.sent == _first4)

class _Neo4jError4(Exception):   # ドライバの Neo4jError の代わり（dev の依存に neo4j は無い）。botocore の ClientError とは別の型
    pass

def _seed_graph(backend, fail=False):
    """ops/seed_graph.py を Web の EC2 の代わりにここで流す（/etc の env は空の偽物、graph と topology はこのテストが読んだもの）。
    Neo4j のサーバーの失敗は本番と同じく botocore の型ではない例外で投げ、graph.errors() はそれを Neo4j のときだけ含める。
    (送ったクエリ, 標準出力)"""
    import contextlib, runpy
    out, path, conf, srv = io.StringIO(), list(sys.path), graph.configured, graph._server_errors
    def _q4(cypher, **params):
        _sent4.append((cypher, params))
        if fail and cypher.startswith("CALL db.prepareForReplanning"):
            raise _Neo4jError4("There is no procedure with the name `db.prepareForReplanning`")
        return [{"n": 0}] if "count(" in cypher else []   # 空のグラフ
    _sent4.clear()
    os.environ.update(NAME_PREFIX="t-nwc-oss", GRAPH_REPLACE="1")
    try:
        graph.BACKEND, graph.query, graph.configured = backend, _q4, lambda: True
        graph._server_errors = lambda: (_Neo4jError4,) if graph.BACKEND == "neo4j" else ()
        with contextlib.redirect_stdout(out):
            try:
                runpy.run_path(os.path.join(AGENT, "..", "..", "ops", "seed_graph.py"), init_globals={"open": lambda *a, **kw: io.StringIO("# 偽物\n")})
            except Exception as e:   # seed_graph.py が捕まえ損ねた例外は、テストを止めずに check で落とす
                print(f"例外 {type(e).__name__}: {e}")
    finally:
        graph.BACKEND, graph.query, graph.configured, graph._server_errors, sys.path[:] = "neptune", _q, conf, srv, path
        for k in ("NAME_PREFIX", "GRAPH_REPLACE"):
            os.environ.pop(k, None)
    return [q for q, _ in _sent4], out.getvalue()
qs4, out4 = _seed_graph("neo4j")
check("neo4j: ops/seed_graph.py は投入（seed）を全部送ったあとで 1 度だけ db.prepareForReplanning を呼ぶ（統計を取り直して索引を使う計画にする）",
      qs4[-1] == "CALL db.prepareForReplanning()" and qs4.count("CALL db.prepareForReplanning()") == 1
      and any(q.startswith("UNWIND $rows AS r CREATE (n:`device`") for q in qs4) and "Neo4j に" in out4 and "WARNING" not in out4 and "例外 " not in out4)
qs4, out4 = _seed_graph("neo4j", fail=True)
check("neo4j: db.prepareForReplanning が無い・失敗しても（ドライバの Neo4jError）、seed_graph.py は WARNING を出すだけで止まらない（投入は済んでいる）",
      qs4[-1] == "CALL db.prepareForReplanning()" and "WARNING: Neo4j の統計を取り直せない" in out4 and "db.prepareForReplanning" in out4
      and "例外 " not in out4)
qs4, out4 = _seed_graph("neptune")
check("Neptune では seed_graph.py は db.prepareForReplanning を呼ばない", qs4 and not any("prepareForReplanning" in q for q in qs4) and "Neptune に" in out4 and "例外 " not in out4)

# ---- 差分の同期（Nautobot の Job が呼ぶ。status と上の層は触らない）
cur_d = [{"id": "a-ce-01", "label": "device", "hostname": "a-ce-01", "site": "a", "role": "leaf", "asn": 65001, "mgmt_ip": "203.0.113.11", "enabled": True, "status": "DOWN"},
         {"id": "old-ce-01", "label": "device", "hostname": "old-ce-01", "site": "a", "role": "leaf", "enabled": False},
         {"id": "new-ce-01", "label": "device", "registered": False, "role": "unknown", "status": "ALARM"},
         {"id": "zz-ce-09", "label": "device", "registered": False, "role": "unknown", "status": "ALARM"}]
cur_i = [{"id": "a-ce-01#eth1", "label": "interface", "device_id": "a-ce-01", "name": "eth1", "address": "172.16.1.2", "status": "DOWN"},
         {"id": "a-ce-01#eth2", "label": "interface", "device_id": "a-ce-01", "name": "eth2"},
         {"id": "old-ce-01#eth1", "label": "interface", "device_id": "old-ce-01", "name": "eth1"}]
cur_e = [{"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth1", "b_if": "eth1", "kind": "l2", "role": "primary", "bandwidth_mbps": 1000, "status": "DOWN"},
         {"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth2", "b_if": "eth2", "kind": "l2"},
         {"a": "a-ce-01", "b": "old-ce-01", "a_if": "eth3", "b_if": "eth1", "kind": "l2"}]
sync_devs = [{"device_id": "a-ce-01", "hostname": "a-ce-01", "site": "a", "role": "spine", "asn": None, "mgmt_ip": "203.0.113.11", "enabled": True,
              "interfaces": [{"name": "eth1", "address": "172.16.1.2", "lag": ""}, {"name": "eth9", "address": "", "lag": "bond0"}]},
             {"device_id": "new-ce-01", "hostname": "new-ce-01", "site": "a", "role": "leaf", "asn": None, "mgmt_ip": "", "enabled": False, "interfaces": []}]
sync_links = [{"a": "b-ce-01", "a_if": "eth1", "b": "a-ce-01", "b_if": "eth1", "kind": "l2", "role": None, "bandwidth_mbps": 25000},
              {"a": "a-ce-01", "a_if": "eth9", "b": "new-ce-01", "b_if": "eth1", "kind": "fabric", "role": None, "bandwidth_mbps": None}]
BOTH = {"WHERE id(n) IN $ids RETURN id(n) AS id": [{"id": "a-ce-01"}, {"id": "new-ce-01"}], "count(l)": [{"n": 0}]}
reset(**{DEV: nodes(cur_d), IFS: nodes(cur_i), LINKS: edges(cur_e), SET_ST: [{"registered": None}], **BOTH, "count(": [{"n": 1}]})
r = graph.sync_physical(sync_devs, sync_links)
qs, cs = state["queries"], calls()
BY, LNK = "MATCH (n) WHERE id(n) = $id", "MATCH (a:device)-[l:link]->(b:device) WHERE id(a) = $a AND id(b) = $b AND l.a_if = $a_if"
check("sync_physical は今の機器・インタフェース・回線を 1 回ずつ読み、全部を消すクエリは送らない",
      qs[:3] == [DEV, IFS, "MATCH (a)-[l:link]->(b) RETURN id(a) AS a, id(b) AS b, l"] and not any("IS NULL DETACH DELETE" in q for q in qs))
check("一覧に無い登録済みの機器・インタフェースと、一覧に無い回線は消す（未登録の頂点 zz-ce-09 と、機器ごと消えた辺には触らない）",
      all((f"{BY} DETACH DELETE n", {"id": x}) in cs for x in ("old-ce-01", "a-ce-01#eth2", "old-ce-01#eth1"))
      and (f"{LNK} DELETE l", {"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth2"}) in cs
      and not any("zz-ce-09" in json.dumps(p) or p.get("a_if") == "eth3" for p in state["params"]))
check("残る機器は変わった property だけ上書きし、無くなった値は property ごと消す（status は書かない）",
      (f"{BY} SET n += $put REMOVE n.`asn`", {"id": "a-ce-01", "put": {"role": "spine"}}) in cs
      and not any(p.get("id") == "a-ce-01#eth1" for p in state["params"]))
check("残る回線は a < b に直して突き合わせ、辺の id でなく「両端 + a_if」で引いて、変わった property だけ書く（status は残る）",
      (f"{LNK} SET l += $put REMOVE l.`role`", {"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth1", "put": {"bandwidth_mbps": 25000}}) in cs)
_new = ("UNWIND $rows AS r CREATE (n:`device` {`~id`: r.id}) SET n += r.props", {"rows": [{"id": "new-ce-01", "props": {"hostname": "new-ce-01", "site": "a", "role": "leaf", "enabled": False}}]})
check("未登録の頂点が一覧にあれば置き換えて UP でない status を引き継ぎ、新しい IF と回線を足す",
      cs.index((f"{BY} DETACH DELETE n", {"id": "new-ce-01"})) < cs.index(_new)
      and created("interface") == [{"id": "a-ce-01#eth9", "props": {"device_id": "a-ce-01", "name": "eth9", "lag": "bond0"}}]
      and ("MATCH (a), (b) WHERE id(a) = $a AND id(b) = $b CREATE (a)-[l:link]->(b) SET l += $props",
           {"a": "a-ce-01", "b": "new-ce-01", "props": {"a_if": "eth9", "b_if": "eth1", "kind": "fabric"}}) in cs
      and cs.index((f"{BY} SET n.status = $st RETURN n.registered AS registered", {"id": "new-ce-01", "st": "ALARM"})) > max(i for i, q in enumerate(qs) if "CREATE" in q))
check("戻り値は count() に足した数・変えた数・消した数", r["added"] == 3 and r["updated"] == 2 and r["removed"] == 4 and r["devices"] == 1 and "skipped" not in r)
same_d = [{"id": "a-ce-01", "label": "device", "hostname": "a-ce-01", "site": "a", "role": "spine", "mgmt_ip": "203.0.113.11", "enabled": True, "status": "DOWN"}]
same_i = [{"id": "a-ce-01#eth1", "label": "interface", "device_id": "a-ce-01", "name": "eth1", "address": "172.16.1.2"}]
reset(**{DEV: nodes(same_d), IFS: nodes(same_i), LINKS: [], "count(": [{"n": 1}]})
r = graph.sync_physical([{**sync_devs[0], "interfaces": sync_devs[0]["interfaces"][:1]}], [])
check("同じ内容なら何も書かない（読む 3 本と count だけ）", r["added"] == r["updated"] == r["removed"] == 0
      and not any(x in q for q in state["queries"] for x in ("DELETE", "CREATE", "SET ", "REMOVE")))
dup_e = [dict(cur_e[0]), dict(cur_e[0], status=None, role="backup")]
reset(**{DEV: nodes([cur_d[0], {"id": "b-ce-01", "label": "device", "hostname": "b-ce-01"}]), IFS: [], LINKS: edges(dup_e), "count(": [{"n": 1}]})
r = graph.sync_physical([{k: v for k, v in cur_d[0].items() if k not in ("id", "label", "status")} | {"device_id": "a-ce-01", "interfaces": []},
                         {"device_id": "b-ce-01", "hostname": "b-ce-01", "interfaces": []}], [sync_links[0]])
check("同じ回線が 2 本あれば全部消して 1 本を作り直し、1 本目の status を引き継ぐ（辺を id で 1 本だけ消せないため）",
      (f"{LNK} DELETE l", {"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth1"}) in calls()
      and created_links() == [{"a": "a-ce-01", "b": "b-ce-01", "props": {"a_if": "eth1", "b_if": "eth1", "kind": "l2", "bandwidth_mbps": 25000, "status": "DOWN"}}]
      and r["removed"] == 1 and r["added"] == 0)
# ---- 保守中と変更履歴（2026-10-04）
_first = cur_d[0]["id"]
_row = {"device_id": _first, **{k: cur_d[0].get(k) for k in ("hostname", "site", "role", "asn", "mgmt_ip", "enabled")}, "interfaces": []}
reset(**{DEV: nodes([dict(cur_d[0], maintenance=True)]), IFS: [], LINKS: [], "count(": [{"n": 1}]})
graph.sync_physical([_row], [])
check("sync_physical: 保守が明けた機器（一覧に maintenance が無い）は property ごと消す", (f"{BY} REMOVE n.`maintenance`", {"id": _first}) in calls())
reset(**{DEV: nodes([cur_d[0]]), IFS: [], LINKS: [], "count(": [{"n": 1}]})
graph.sync_physical([{**_row, "maintenance": True}], [])
check("sync_physical: 保守中にした機器には maintenance = true を書く", (f"{BY} SET n += $put", {"id": _first, "put": {"maintenance": True}}) in calls())
reset(**{"MATCH (n:change) RETURN id(n) AS id": [{"id": "change#old"}, {"id": "change#keep"}]})
r = graph.sync_changes([{"change_id": "change#keep", "time": 1}, {"change_id": "change#new", "time": 2, "user": "admin", "action": "update", "object_type": "device", "object": "a", "device_id": "a", "detail": ""}])
check("sync_changes: 無い id だけ足し、一覧に無い古い頂点を消し、もうある id は触らない",
      r == {"added": 1, "removed": 1, "kept": 2} and calls() == [
          ("MATCH (n:change) RETURN id(n) AS id", {}),
          ("UNWIND $rows AS r CREATE (n:`change` {`~id`: r.id}) SET n += r.props",
           {"rows": [{"id": "change#new", "props": {"time": 2, "user": "admin", "action": "update", "object_type": "device", "object": "a", "device_id": "a"}}]}),
          ("MATCH (n:change) WHERE id(n) IN $ids DETACH DELETE n", {"ids": ["change#old"]})])
reset(**{DEV: [], IFS: [], LINKS: [], "WHERE id(n) IN $ids RETURN id(n) AS id": [], "count(": [{"n": 0}]})
r = graph.sync_physical([], [{"a": "x", "a_if": "e", "b": "y", "b_if": "e"}])
check("端の機器が無い回線は張らずに skipped に理由を出す", r["added"] == 0 and len(r["skipped"]) == 1 and "機器が無い" in r["skipped"][0])

# ---- 動的な状態（app/graph/status_handler.py が呼ぶ）
EDGE_ST = "MATCH (a)-[l:link]->(b) WHERE (id(a) = $dev AND l.a_if = $ifn) OR (id(b) = $dev AND l.b_if = $ifn) SET l.status = $st RETURN count(l) AS n"
V_ST = "MATCH (n) WHERE id(n) = $id SET n.status = $st RETURN n.registered AS registered"
reset(**{"count(l)": [{"n": 1}], SET_ST: [{"registered": None}]})
r = graph.set_status("dc1-leaf-01", "eth1", "down")
check("set_status は IF 付きなら、その IF が付く辺（a 側でも b 側でも）とインタフェースの頂点に書き、更新数を返す",
      r == {"device_id": "dc1-leaf-01", "if_name": "eth1", "status": "DOWN", "updated": 2}
      and calls() == [(EDGE_ST, {"dev": "dc1-leaf-01", "ifn": "eth1", "st": "DOWN"}), (V_ST, {"id": "dc1-leaf-01#eth1", "st": "DOWN"})])
reset(**{SET_ST: [{"registered": None}]})
check("set_status は IF 無しなら機器の頂点に書く",
      graph.set_status("dc1-leaf-01", "", "ALARM") == {"device_id": "dc1-leaf-01", "status": "ALARM", "updated": 1}
      and calls() == [(V_ST, {"id": "dc1-leaf-01", "st": "ALARM"})])
reset(**{SET_ST: []})
check("only_if を渡すと、今の status がそれのときだけ書き（条件と書き込みが 1 本）、合わなければ未登録の頂点も作らない",
      graph.set_status("dc1-leaf-01", "", "UP", only_if="alarm") == {"device_id": "dc1-leaf-01", "status": "UP", "updated": 0}
      and calls() == [("MATCH (n) WHERE id(n) = $id AND n.status = $only SET n.status = $st RETURN n.registered AS registered", {"id": "dc1-leaf-01", "st": "UP", "only": "ALARM"})])
reset(**{SET_ST: []})
check("only_if があれば UP 以外でも、合わなければ未登録の頂点を作らない",
      "unregistered" not in graph.set_status("zz-ce-09", "", "ALARM", only_if="DOWN") and not any("MERGE" in q for q in state["queries"]))
reset()
check("set_status は UP / DOWN / ALARM 以外を拒む", "error" in graph.set_status("dc1-leaf-01", "", "broken") and not state["queries"])
reset(**{SET_ST: []})
r = graph.set_status("zz-ce-09", "", "ALARM")
check("トポロジに無い機器の異常は捨てず、未登録の頂点（role=unknown, registered=false）を MERGE で作って status を書く",
      r == {"device_id": "zz-ce-09", "status": "ALARM", "updated": 0, "unregistered": True}
      and calls()[1] == ("MERGE (n:`device` {`~id`: $id}) ON CREATE SET n += $props, n.registered = false",
                         {"id": "zz-ce-09", "props": {"hostname": "zz-ce-09", "site": "?", "role": "unknown", "enabled": False}})
      and calls()[2] == (V_ST, {"id": "zz-ce-09", "st": "ALARM"}))
reset(**{"count(l)": [{"n": 0}], SET_ST: []})
r = graph.set_status("dc1-leaf-01", "eth9", "DOWN")
check("トポロジに無いインタフェースの異常は、機器（無ければ）とインタフェースの未登録の頂点を作る",
      r["unregistered"] is True and r["updated"] == 0 and state["queries"][2].startswith("MERGE (n:`device`")
      and calls()[3] == ("MERGE (n:`interface` {`~id`: $id}) ON CREATE SET n += $props, n.registered = false",
                         {"id": "dc1-leaf-01#eth9", "props": {"device_id": "dc1-leaf-01", "name": "eth9"}})
      and calls()[4] == (V_ST, {"id": "dc1-leaf-01#eth9", "st": "DOWN"}))
reset(**{"count(l)": [{"n": 0}], SET_ST: []})
r = graph.set_status("zz-ce-09", "eth1", "UP")
check("UP に戻すだけのときは未登録の頂点を作らない", "unregistered" not in r and not any("MERGE" in q for q in state["queries"]))
# 上の層の動的な状態（bgp_down / isis_down。app/graph/status_handler.py が set_layer_status を呼ぶ）
reset(**{SET_ST: [{"registered": None}]})
check("set_layer_status は <機器>#bgp#<相手の IP> の bgp_session の頂点に書く",
      graph.set_layer_status("dc1-leaf-01", "bgp", "10.255.0.1", "DOWN") == {"device_id": "dc1-leaf-01", "kind": "bgp", "target": "10.255.0.1", "status": "DOWN", "updated": 1}
      and calls() == [("MATCH (n:`bgp_session`) WHERE id(n) = $id SET n.status = $st RETURN n.registered AS registered", {"id": "dc1-leaf-01#bgp#10.255.0.1", "st": "DOWN"})])
reset(**{SET_ST: [{"registered": None}]})
check("isis は isis_adjacency の頂点（target = サブインタフェース）", graph.set_layer_status("dc1-leaf-01", "isis", "ethernet-1/1.0", "up")["updated"] == 1
      and calls()[0] == ("MATCH (n:`isis_adjacency`) WHERE id(n) = $id SET n.status = $st RETURN n.registered AS registered", {"id": "dc1-leaf-01#isis#ethernet-1/1.0", "st": "UP"}))
reset(**{SET_ST: []})
r = graph.set_layer_status("dc1-leaf-01", "bgp", "10.255.9.9", "DOWN")
check("トポロジに無いセッションは未登録の頂点（layer と peer_address 付き）を作って status を書く", r["unregistered"] is True and r["updated"] == 0
      and calls()[1] == ("MERGE (n:`bgp_session` {`~id`: $id}) ON CREATE SET n += $props, n.registered = false",
                         {"id": "dc1-leaf-01#bgp#10.255.9.9", "props": {"device_id": "dc1-leaf-01", "layer": "evpn", "peer_address": "10.255.9.9"}})
      and calls()[2] == (V_ST, {"id": "dc1-leaf-01#bgp#10.255.9.9", "st": "DOWN"}))
reset(**{SET_ST: []})
check("UP に戻すだけなら未登録の頂点を作らない", "unregistered" not in graph.set_layer_status("dc1-leaf-01", "isis", "x", "UP") and len(state["queries"]) == 1)
check("kind / status が違えば error", "error" in graph.set_layer_status("d", "ospf", "x") and "error" in graph.set_layer_status("d", "bgp", "x", "broken"))
reset(**{SET_ST: [{"registered": False}]})
check("未登録の頂点に書いたときも unregistered", graph.set_status("zz-ce-09", "", "UP").get("unregistered") is True and len(state["queries"]) == 1)

# ---- 変更履歴の頂点（topology.recent_changes が読む）。修復案の頂点 proposal は 2026-10-05 にやめた（get_record / update_record も消した）
chg = {"id": "c-1", "label": "change", "status": "done", "device_id": "a-ce-01", "time": 10}
reset(**{"RETURN n": nodes([chg])})
check("list_records は status / device_id で絞り、order_by の新しい順に limit 件（絞る値はパラメータ、並べる property と件数は埋め込み）",
      graph.list_records("change", "change_id", "time", status="done", device_id="a-ce-01", limit=5) == [{"status": "done", "device_id": "a-ce-01", "time": 10, "change_id": "c-1"}]
      and calls() == [("MATCH (n:`change`) WHERE n.status = $status AND n.device_id = $device_id RETURN n ORDER BY n.`time` DESC LIMIT 5", {"status": "done", "device_id": "a-ce-01"})])
check("修復案の頂点を読み書きする関数は無い", not hasattr(graph, "get_record") and not hasattr(graph, "update_record"))

# ---- アルゴリズム（Neptune Analytics の neptune.algo.*）
reset(**{"neptune.algo.degree": [{"id": "a", "degree": 2}, {"id": "b", "degree": 1}, {"id": "c", "degree": 0}],
         "neptune.algo.closenessCentrality": [{"id": "a", "score": 0.61234}, {"id": "b", "score": 0.4}, {"id": "c", "score": 0}],
         "neptune.algo.wcc": [{"id": "a", "component": 2357352929951779}, {"id": "b", "component": 2357352929951779}, {"id": "c", "component": 9}]})
r = graph.centrality(limit=2)
check("centrality は次数・近接中心性・弱連結成分をグラフの中で回し、closeness の大きい順に limit 件と島の数を返す",
      r == {"devices": [{"device_id": "a", "degree": 2, "closeness": 0.6123, "component": 2}, {"device_id": "b", "degree": 1, "closeness": 0.4, "component": 2}],
            "device_count": 3, "components": 2})
check("アルゴリズムは機器の頂点と回線の辺だけを、向きを問わずに見る",
      state["queries"] == [
          'MATCH (n:device) CALL neptune.algo.degree(n, {edgeLabels: ["link"], vertexLabels: ["device"], traversalDirection: "both"}) YIELD degree RETURN id(n) AS id, degree',
          'MATCH (n:device) CALL neptune.algo.closenessCentrality(n, {edgeLabels: ["link"], vertexLabel: "device", traversalDirection: "both", numSources: 8192}) YIELD score RETURN id(n) AS id, score',
          'MATCH (n:device) CALL neptune.algo.wcc(n, {edgeLabels: ["link"], vertexLabel: "device"}) YIELD component RETURN id(n) AS id, component'])

devs[0]["status"] = "DOWN"; links[0]["status"] = "DOWN"
devs.append({"id": "zz-ce-09", "label": "device", "hostname": "zz-ce-09", "site": "?", "role": "unknown", "enabled": False, "registered": False, "status": "ALARM"})
reset(**topo_answer())
topology.reload(force=True)
check("Neptune の上の層は topology.layers に出て、未登録の頂点は registered False のまま", topology.layers()["count"] == 2
      and next(v for v in topology.layers()["vertices"] if v["id"] == "a-ce-01#bgp#10.255.0.9")["registered"] is False)
check("Neptune の status は機器一覧・隣接・影響範囲に出て、無ければ UP",
      topology.list_devices()["devices"][0]["status"] == "DOWN" and topology.list_devices()["devices"][1]["status"] == "UP"
      and topology.neighbors("b-ce-01")["neighbors"][0]["status"] == "DOWN"
      and topology.blast_radius("b-ce-01")["affected"][0]["status"] == "DOWN")
rows = topology.list_devices()["devices"]
check("未登録の機器は registered=false・role=unknown で一覧の最後に出る", rows[-1]["device_id"] == "zz-ce-09" and rows[-1]["registered"] is False
      and all(x["registered"] for x in rows[:-1]))
check("interfaces は Neptune のインタフェースの一覧とリンクの IF 名を合わせる", topology.interfaces("a-ce-01") == ["eth0", "eth1"])
print(f"通過 {passed} / 失敗 0")
