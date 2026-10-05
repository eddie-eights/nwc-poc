"""cycle 005（マネージドを OSS に置き換えた環境を作る）の切り替えの模擬テスト。AWS にも Neo4j にも触れない。
  1. 環境変数が無いとき、agent/graph.py と workflow/awsio.py が出す openCypher とパラメータが、切り替えを入れる前と 1 文字も違わない
     （tests/golden/neptune_cypher.json と比べる。golden は切り替えを入れる前のコードで --write-golden を付けて作った）
  2. GRAPH_BACKEND=neo4j のとき、出す Cypher に neptune.algo・`~id`・id( が無く、同じグラフの中身から同じ結果を返す
  3. Spark（spark/snmp_sinks.py）・agent/evidence.py・grafana/start.sh の認証の切り替え（KAFKA_AUTH / OPENSEARCH_AUTH / PROMETHEUS_AUTH）。
     環境変数が無いときは今のまま（MSK の IAM 認証・SigV4・マネージド版のデータソース）
  4. oss/terraform（設計の 3）: 変えないルートは terraform/ のファイルへのシンボリックリンク、変える 3 ルート（stream / analytics / graph）に
     マネージドのサービス（MSK / EMR Serverless / AMP / Neptune Analytics / OpenSearch Serverless）が無い、接頭辞は var.project、
     土台の OSS の SG と EFS はマネージド版では作らない
実行は python3 tests/test_oss.py。graph.py のクエリを意図して変えたときだけ --write-golden で golden を作り直す
（作り直すと 1. はその時点のコードを正とする。差分は git diff tests/golden で見る）。"""
import base64, copy, importlib.util, io, json, os, re, subprocess, sys, tempfile, types, urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
AGENT, WORKFLOW, SPARK = (os.path.join(ROOT, d) for d in ("agent", "workflow", "spark"))
GOLDEN = os.path.join(ROOT, "tests", "golden", "neptune_cypher.json")
sys.path[:0] = [AGENT, WORKFLOW]

for k in ("GRAPH_BACKEND", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE", "KAFKA_AUTH", "OPENSEARCH_AUTH",
          "PROMETHEUS_AUTH", "OPENSEARCH_USER", "OPENSEARCH_PASSWORD", "PARAM_PREFIX"):
    os.environ.pop(k, None)


class ClientError(Exception):
    pass


class BotoCoreError(Exception):
    pass


state = {"calls": [], "answer": {}, "ssm": []}


def answer(q):
    """先に書いた鍵が勝つ（鍵は id( と `~id` を含まない書き方にして、Neptune と Neo4j の両方のクエリに当てる）"""
    for key, rows in state["answer"].items():
        if key in q:
            return copy.deepcopy(rows)
    return [{"n": 1}]


class FakeClient:
    def __init__(self, name, **kw):
        self.name = name

    def execute_query(self, graphIdentifier, queryString, language, parameters=None):
        assert language == "OPEN_CYPHER" and graphIdentifier == "g-abc1234567"
        state["calls"].append([queryString, parameters or {}])
        return {"payload": io.BytesIO(json.dumps({"results": answer(queryString)}).encode())}

    def get_parameter(self, Name, WithDecryption=False):   # toolkit.Param が SSM を引くとき（evidence の OpenSearch のパスワード）
        state["ssm"].append((Name, WithDecryption))
        return {"Parameter": {"Value": "pw-ssm"}}


boto3 = types.ModuleType("boto3"); boto3.client = lambda name, **kw: FakeClient(name, **kw)
botocore = types.ModuleType("botocore"); exc = types.ModuleType("botocore.exceptions")
exc.ClientError = ClientError; exc.BotoCoreError = BotoCoreError; botocore.exceptions = exc
cfg = types.ModuleType("botocore.config"); cfg.Config = lambda **kw: kw; botocore.config = cfg
sys.modules.update({"boto3": boto3, "botocore": botocore, "botocore.exceptions": exc, "botocore.config": cfg})


# ---- Neo4j のドライバの偽物（neo4j.GraphDatabase.driver → execute_query）。答えは Neptune の形で書き、頂点と辺をドライバの Node / Relationship にして返す
class Node(dict):
    def __init__(self, labels, props):
        super().__init__(props)
        self.labels = frozenset(labels)


class Relationship(dict):
    def __init__(self, rtype, props):
        super().__init__(props)
        self.type = rtype
        self.start_node = self.end_node = None


class Record:
    def __init__(self, row):
        self._row = row

    def items(self):
        return list(self._row.items())


def to_driver(v):
    if isinstance(v, dict) and v.get("~entityType") == "node":
        return Node(v["~labels"], {**v["~properties"], "id": v["~id"]})
    if isinstance(v, dict) and v.get("~entityType") == "relationship":
        return Relationship(v["~type"], v["~properties"])
    return v


class FakeDriver:
    made = []

    def __init__(self, uri, auth=None, **config):
        self.uri, self.auth, self.config = uri, auth, config
        FakeDriver.made.append(self)

    def execute_query(self, query_, parameters_=None, database_=None, **kw):
        assert not kw and database_ == os.environ.get("NEO4J_DATABASE", "neo4j"), (kw, database_)
        state["calls"].append([query_, parameters_ or {}])
        rows = answer(query_)
        if isinstance(rows, Exception):
            raise rows
        return types.SimpleNamespace(records=[Record({k: to_driver(v) for k, v in r.items()}) for r in rows], keys=[])


neo4j = types.ModuleType("neo4j"); neo4j.GraphDatabase = types.SimpleNamespace(driver=lambda uri, auth=None, **kw: FakeDriver(uri, auth, **kw))
sys.modules["neo4j"] = neo4j

passed = 0


def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m


def node(v):
    return {"~id": v["id"], "~entityType": "node", "~labels": [v["label"]], "~properties": {k: x for k, x in v.items() if k not in ("id", "label")}}


def nodes(vs):
    return [{"n": node(v)} for v in vs]


def edges(es):
    return [{"a": e["a"], "b": e["b"], "l": {"~entityType": "relationship", "~type": "link", "~start": e["a"], "~end": e["b"],
                                             "~properties": {k: x for k, x in e.items() if k not in ("a", "b")}}} for e in es]


# ---- 決まった答えで graph.py の関数を全部呼ぶ（出るクエリとパラメータ、関数の戻り値を集める）
DEV, IFS = "MATCH (n:`device`) RETURN n", "MATCH (n:`interface`) RETURN n"
LINKS = "MATCH (a)-[l:link]->(b) RETURN"
LAYER_E = "RETURN type(e) AS label"
SET_ST = "SET n.status = $st RETURN n.registered AS registered"
REG = "RETURN n.registered AS registered"
UNREG = "WHERE n.registered = false RETURN n"
devs = [{"id": "a-ce-01", "label": "device", "hostname": "a-ce-01", "site": "a", "role": "leaf", "asn": 65001, "mgmt_ip": "203.0.113.11", "enabled": True},
        {"id": "b-ce-01", "label": "device", "hostname": "b-ce-01", "site": "b", "role": "leaf", "enabled": False, "status": "DOWN"},
        {"id": "zz-ce-09", "label": "device", "hostname": "zz-ce-09", "site": "?", "role": "unknown", "registered": False, "status": "ALARM"}]
ifs = [{"id": "a-ce-01#eth1", "label": "interface", "device_id": "a-ce-01", "name": "eth1", "address": "172.16.1.2", "status": "DOWN"},
       {"id": "b-ce-01#eth1", "label": "interface", "device_id": "b-ce-01", "name": "eth1"}]
links = [{"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth1", "b_if": "eth1", "kind": "l2", "role": "primary", "bandwidth_mbps": 1000, "status": "DOWN"},
         {"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth2", "b_if": "eth2", "kind": "l2"},
         {"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth2", "b_if": "eth2", "kind": "l2"},
         {"a": "a-ce-01", "b": "gone-01", "a_if": "eth9", "b_if": "eth9"}]
lv_ip = [{"id": "a-ce-01#eth1.0", "label": "ip_interface", "layer": "ip", "device_id": "a-ce-01", "interface_id": "a-ce-01#eth1", "name": "eth1.0"}]
lv_bgp = [{"id": "a-ce-01#bgp#10.255.0.9", "label": "bgp_session", "layer": "evpn", "device_id": "a-ce-01", "peer_address": "10.255.0.9",
           "status": "DOWN", "registered": False}]
le = [{"label": "over", "from": "a-ce-01#eth1.0", "to": "a-ce-01#eth1"}]
want_devs = [{"device_id": "a-ce-01", "hostname": "a-ce-01", "site": "a", "role": "spine", "asn": 65001, "enabled": True,
              "interfaces": [{"name": "eth1", "address": "172.16.1.2"}, {"name": "eth3"}]},
             {"device_id": "c-ce-01", "hostname": "c-ce-01", "site": "c", "role": "leaf", "interfaces": [{"name": "eth1"}]},
             {"device_id": "zz-ce-09", "hostname": "zz-ce-09", "site": "z", "role": "leaf"}]
want_links = [{"a": "c-ce-01", "a_if": "eth1", "b": "a-ce-01", "b_if": "eth3", "kind": "l2", "bandwidth_mbps": 100},
              {"a": "a-ce-01", "a_if": "eth2", "b": "b-ce-01", "b_if": "eth2"}, {"a": "a-ce-01", "a_if": "eth5", "b": "x-ce-01", "b_if": "eth5"}]
layers = {"vertices": lv_ip + [{"id": "a-ce-01#bgp#10.255.0.9", "label": "bgp_session", "layer": "evpn", "device_id": "a-ce-01", "peer_address": "10.255.0.9"},
                               {"id": "bad", "label": "nope"}],
          "edges": le + [{"label": "peer", "from": "a-ce-01#bgp#10.255.0.9", "to": "nowhere"}]}
changes = [{"change_id": "change#keep", "time": 1}, {"change_id": "change#new", "time": 2, "user": "admin", "action": "update",
                                                     "object_type": "device", "object": "a", "device_id": "a", "detail": ""}]


def scenario(graph, with_algo=True):
    """graph の読み書きの関数を全部、決まった答えで呼ぶ。[(呼んだもの, 戻り値)]"""
    out = []

    def run(name, f, **ans):
        state["answer"] = ans
        out.append([name, f()])
    topo = {DEV: nodes(devs), IFS: nodes(ifs), LINKS: edges(links[:2]), "MATCH (n:`ip_interface`) RETURN n": nodes(lv_ip),
            "MATCH (n:`bgp_session`) RETURN n": nodes(lv_bgp), LAYER_E: le, "RETURN n": []}
    run("load_topology", graph.load_topology, **topo)
    run("load_layers", graph.load_layers, **topo)
    run("count", graph.count, **{"count(": [{"n": 2}]})
    run("seed", lambda: graph.seed(want_devs, want_links, layers),
        **{"MATCH (n:`bgp_session`) WHERE n.registered = false RETURN n": nodes(lv_bgp), UNREG: nodes([devs[2]]), "RETURN n": [],
           SET_ST: [{"registered": None}], "count(": [{"n": 1}]})
    run("seed_layers", lambda: graph.seed_layers(layers), **{"MATCH (n:interface) RETURN": [{"id": "a-ce-01#eth1"}], "RETURN n": [], "count(": [{"n": 1}]})
    run("sync_physical", lambda: graph.sync_physical(want_devs, want_links),
        **{DEV: nodes(devs), IFS: nodes(ifs), LINKS: edges(links), "IN $ids RETURN": [{"id": "a-ce-01"}], "count(l)": [{"n": 0}],
           SET_ST: [{"registered": None}], "count(": [{"n": 1}]})
    run("sync_changes", lambda: graph.sync_changes(changes), **{"MATCH (n:change) RETURN": [{"id": "change#old"}, {"id": "change#keep"}]})
    run("add_device", lambda: graph.add_device("c-ce-01", "c", "leaf", "203.0.113.15", 65003), **{REG: []})
    run("add_device_replace", lambda: graph.add_device("zz-ce-09", "zz", "ce"), **{REG: [{"registered": False}], "RETURN n.status AS status": [{"status": "ALARM"}]})
    run("remove_device", lambda: graph.remove_device("a-ce-01"), **{REG: [{"registered": None}]})
    run("add_link", lambda: graph.add_link("b-ce-01", "eth2", "a-ce-01", "eth3", "fabric", "secondary", 100),
        **{"IN $ids RETURN": [{"id": "a-ce-01"}, {"id": "b-ce-01"}], "count(l)": [{"n": 0}]})
    run("remove_link", lambda: graph.remove_link("b-ce-01", "a-ce-01", "eth3"), **{"count(l)": [{"n": 1}]})
    run("remove_link_all", lambda: graph.remove_link("a-ce-01", "b-ce-01"), **{"count(l)": [{"n": 2}]})
    run("set_status_if", lambda: graph.set_status("a-ce-01", "eth1", "down"), **{"count(l)": [{"n": 1}], SET_ST: [{"registered": None}]})
    run("set_status_if_unregistered", lambda: graph.set_status("zz-ce-09", "eth7", "DOWN"), **{"count(l)": [{"n": 0}], SET_ST: []})
    run("set_status_device", lambda: graph.set_status("a-ce-01", "", "ALARM"), **{SET_ST: [{"registered": False}]})
    run("set_status_only_if", lambda: graph.set_status("a-ce-01", "", "UP", only_if="alarm"), **{SET_ST: []})
    run("set_status_unregistered", lambda: graph.set_status("zz-ce-10", "", "ALARM"), **{SET_ST: []})
    run("set_layer_status", lambda: graph.set_layer_status("a-ce-01", "bgp", "10.255.9.9", "DOWN"), **{SET_ST: []})
    run("set_layer_status_isis", lambda: graph.set_layer_status("a-ce-01", "isis", "ethernet-1/1.0", "up"), **{SET_ST: [{"registered": None}]})
    run("list_records", lambda: graph.list_records("change", "change_id", "time", status="done", device_id="a-ce-01", limit=5),
        **{"RETURN n": nodes([{"id": "c-1", "label": "change", "status": "done", "device_id": "a-ce-01", "time": 10}])})
    if with_algo:
        run("centrality", lambda: graph.centrality(limit=2),
            **{"neptune.algo.degree": [{"id": "a", "degree": 2}, {"id": "b", "degree": 1}, {"id": "c", "degree": 0}],
               "neptune.algo.closenessCentrality": [{"id": "a", "score": 0.61234}, {"id": "b", "score": 0.4}, {"id": "c", "score": 0}],
               "neptune.algo.wcc": [{"id": "a", "component": 2357352929951779}, {"id": "b", "component": 2357352929951779}, {"id": "c", "component": 9}]})
    return out


def awsio_scenario(awsio):
    state["answer"] = {"MATCH (n:device) RETURN": [{"id": "a-ce-01", "status": "DOWN", "maintenance": True}, {"id": "b-ce-01", "status": None}],
                       "MATCH (a)-[l:link]->(b) RETURN": [{"a": "a-ce-01", "b": "b-ce-01", "a_if": "eth1", "b_if": "eth1", "status": "DOWN"}]}
    return awsio.read_topology()


def run_neptune():
    os.environ["NEPTUNE_GRAPH_ID"] = "g-abc1234567"
    state["calls"] = []
    graph = load(os.path.join(AGENT, "graph.py"), "graph")
    results = scenario(graph)
    graph_calls = state["calls"]
    state["calls"] = []
    awsio = load(os.path.join(WORKFLOW, "awsio.py"), "awsio")
    topo = awsio_scenario(awsio)
    return {"graph": graph_calls, "awsio": state["calls"]}, results, topo


if "--write-golden" in sys.argv:
    calls, _, _ = run_neptune()
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with open(GOLDEN, "w", encoding="utf-8") as f:
        json.dump(calls, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"golden を書いた: {GOLDEN}（graph {len(calls['graph'])} 本 / awsio {len(calls['awsio'])} 本）")
    sys.exit(0)

# ---- 1. 環境変数が無いとき（マネージド版）
with open(GOLDEN, encoding="utf-8") as f:
    golden = json.load(f)
calls, neptune_results, neptune_topo = run_neptune()
check("環境変数が無いとき、graph.py が出す openCypher とパラメータは切り替えを入れる前と 1 文字も違わない（golden と同じ）", calls["graph"] == golden["graph"])
check("環境変数が無いとき、workflow/awsio.py の read_topology の openCypher も同じ", calls["awsio"] == golden["awsio"])
check("golden は関数を一通り通している（id( と `~id` と neptune.algo の全部の形が入っている）",
      len(golden["graph"]) > 80 and any("`~id`: r.id" in q for q, _ in golden["graph"]) and any("MERGE" in q for q, _ in golden["graph"])
      and sum("neptune.algo." in q for q, _ in golden["graph"]) == 3)
check("環境変数が無いとき、graph.BACKEND は neptune、awsio.GRAPH_ENV は NEPTUNE_GRAPH_ID（worker.py が起動時に見る変数も今のまま）",
      sys.modules["graph"].BACKEND == "neptune" and sys.modules["graph"].configured() and sys.modules["awsio"].GRAPH_ENV == "NEPTUNE_GRAPH_ID")


def with_env(env, f):
    """環境変数を env にして f() を呼び、元に戻す（値が None の鍵は消す）"""
    saved = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        return f()
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def raises(exc, f):
    try:
        f()
    except exc:
        return True
    return False


# ---- 2. GRAPH_BACKEND=neo4j（OSS 版）
NEO4J = {"GRAPH_BACKEND": "neo4j", "NEO4J_URI": "bolt://neo4j.nwc-oss.internal:7687", "NEO4J_PASSWORD": "pw-neo4j"}
os.environ.pop("NEPTUNE_GRAPH_ID")
os.environ.update(NEO4J)
state["calls"] = []
FakeDriver.made.clear()
graph4 = load(os.path.join(AGENT, "graph.py"), "graph")
results4 = scenario(graph4, with_algo=False)
calls4 = state["calls"]
labels = ("device", "interface", "change") + graph4.LAYER_LABELS
schema = [q for q, _ in calls4 if q.startswith("CREATE CONSTRAINT")]
body4 = [c for c in calls4 if not c[0].startswith("CREATE CONSTRAINT")]
n4 = len(body4)
check("neo4j: 送るのは golden（Neptune の openCypher）を _dialect で直したものだけで、パラメータも順番も同じ（centrality より前の全関数）",
      [[graph4._dialect(q), p] for q, p in golden["graph"][:n4]] == body4 and len(golden["graph"]) - n4 == 3
      and all("neptune.algo." in q for q, _ in golden["graph"][n4:]))
check("neo4j: Cypher に neptune.algo・`~id`・id(…) と、予約語のままの AS from / AS to が無い",
      not any("neptune.algo" in q or "`~id`" in q or re.search(r"\bid\(", q) or re.search(r"\bAS (from|to)\b", q) for q, _ in calls4))
check("neo4j: _dialect は id(x) を x.id に、`~id` を id に、AS from / to を `from` / `to` に直し、ほかは変えない",
      graph4._dialect("MATCH (n) WHERE id(n) = $id MERGE (m {`~id`: $x}) RETURN id(m) AS a, valid(n) AS from, n.to AS to, 1 AS fromage")
      == "MATCH (n) WHERE n.id = $id MERGE (m {id: $x}) RETURN m.id AS a, valid(n) AS `from`, n.to AS `to`, 1 AS fromage")
check("neo4j: 頂点の id の一意制約を、最初のクエリの前に 1 度だけ、ラベルごとに作る（IF NOT EXISTS）",
      [q for q, _ in calls4[:len(schema)]] == schema and len(schema) == len(labels)
      and all(f"FOR (n:`{l}`) REQUIRE n.id IS UNIQUE" in q and "IF NOT EXISTS" in q for l, q in zip(labels, schema)))
check("neo4j: 同じグラフの中身（Neptune の答えと同じもの）から、centrality 以外の全関数が Neptune と同じ結果を返す",
      results4 == [r for r in neptune_results if r[0] != "centrality"])
_d = FakeDriver.made
check("neo4j: ドライバは 1 つだけ。NEO4J_URI に NEO4J_USER（既定 neo4j）/ NEO4J_PASSWORD で、接続 10 秒・やり直し 15 秒",
      len(_d) == 1 and _d[0].uri == NEO4J["NEO4J_URI"] and _d[0].auth == ("neo4j", "pw-neo4j")
      and _d[0].config == {"connection_timeout": 10, "max_transaction_retry_time": 15} and graph4.configured() and graph4.BACKEND == "neo4j")

# centrality は GDS（射影 → 3 つの stream → 射影を消す）。Neptune と同じ数を返せば、同じ結果になる
ALGO = {"count(n)": [{"n": 3}], "gds.graph.project": [{"graph": "x"}],
        "gds.degree": [{"id": "a", "degree": 2}, {"id": "b", "degree": 1}, {"id": "c", "degree": 0}],
        "gds.closeness": [{"id": "a", "score": 0.61234}, {"id": "b", "score": 0.4}, {"id": "c", "score": 0}],
        "gds.wcc": [{"id": "a", "component": 2357352929951779}, {"id": "b", "component": 2357352929951779}, {"id": "c", "component": 9}],
        "gds.graph.drop": [{"graphName": "x"}]}
state["answer"], state["calls"] = ALGO, []
cent4 = graph4.centrality(limit=2)
_gds = [(q, p) for q, p in state["calls"] if "gds." in q]
check("neo4j の centrality: GDS（gds.degree / gds.closeness / gds.wcc）が Neptune の neptune.algo と同じ数を返せば、結果も同じ",
      cent4 == dict(neptune_results)["centrality"])
check("neo4j の centrality: 呼ぶたびに別の名前で射影し、3 つを読んだあと同じ名前で消す",
      [k for q, _ in _gds for k in ALGO if k in q] == ["gds.graph.project", "gds.degree", "gds.closeness", "gds.wcc", "gds.graph.drop"]
      and len({p["g"] for _, p in _gds}) == 1 and _gds[0][1]["g"].startswith("nwc-centrality-")
      and (graph4.centrality() or True) and state["calls"][-1][1]["g"] != _gds[0][1]["g"])
state["answer"], state["calls"] = dict(ALGO, **{"gds.closeness": RuntimeError("closeness に失敗")}), []
check("neo4j の centrality: 途中で失敗しても射影は消し、失敗は上げる",
      raises(RuntimeError, graph4.centrality) and "gds.graph.drop" in state["calls"][-1][0])
state["answer"], state["calls"] = {"count(n)": [{"n": 0}]}, []
_empty = graph4.centrality()
check("neo4j の centrality: 機器が無ければ射影しない（GDS は空のグラフを射影できない）",
      _empty["device_count"] == 0 and not any("gds." in q for q, _ in state["calls"]))

state["calls"] = []
FakeDriver.made.clear()
awsio4 = load(os.path.join(WORKFLOW, "awsio.py"), "awsio")
topo4 = awsio_scenario(awsio4)
check("neo4j: awsio.read_topology は同じ中身から Neptune と同じトポロジを返し、送るのは golden の id(x) を x.id に直したもの",
      topo4 == neptune_topo and state["calls"] == [[re.sub(r"\bid\((\w+)\)", r"\1.id", q), p] for q, p in golden["awsio"]]
      and not any(re.search(r"\bid\(", q) for q, _ in state["calls"]))
check("neo4j: awsio のドライバも NEO4J_USER / NEO4J_PASSWORD で、worker.py が起動時に見る変数は NEO4J_URI",
      awsio4.GRAPH_ENV == "NEO4J_URI" and len(FakeDriver.made) == 1 and FakeDriver.made[0].auth == ("neo4j", "pw-neo4j")
      and FakeDriver.made[0].uri == NEO4J["NEO4J_URI"])

_bad = []
for _path, _name in ((os.path.join(AGENT, "graph.py"), "graph_bad"), (os.path.join(WORKFLOW, "awsio.py"), "awsio_bad")):
    _bad.append(with_env({"GRAPH_BACKEND": "neptune-analytics"}, lambda: raises(ValueError, lambda: load(_path, _name))))
check("GRAPH_BACKEND が neptune / neo4j 以外なら、graph.py も awsio.py も import で止まる（綴り違いで黙って Neptune を読まない）", _bad == [True, True])

# status Lambda（graph/status_handler.py）は、graph.py の Neo4j のドライバを短い設定で先に作る
state["calls"] = []
FakeDriver.made.clear()
graph5 = load(os.path.join(AGENT, "graph.py"), "graph")
status = load(os.path.join(ROOT, "graph", "status_handler.py"), "status_handler")
status._neptune()
status._neptune()
state["answer"] = {"count(": [{"n": 2}]}
_n = graph5.count()
check("neo4j の status Lambda: Neo4j のドライバを接続 3 秒・やり直し 5 秒で 1 つだけ作り、graph.py はそれで送る（neptune-graph のクライアントは作らない）",
      len(FakeDriver.made) == 1 and FakeDriver.made[0].config == {"connection_timeout": 3, "max_transaction_retry_time": 5}
      and graph5._cache["driver"] is FakeDriver.made[0] and status._cache["neptune"] is None and graph5._cache["client"] is None
      and state["calls"] and _n is not None)
for k in NEO4J:
    os.environ.pop(k)


# ---- 3. Spark（spark/snmp_sinks.py）の認証の切り替え。pyspark は何でも受ける偽物、readStream は option を順に覚える
class AnyObj:
    def __getattr__(self, k): return self
    def __call__(self, *a, **kw): return self
    def __getitem__(self, k): return self


class Reader:
    def __init__(self): self.opts = []
    def format(self, f): return self
    def option(self, k, v): self.opts.append((k, v)); return self
    def load(self): return AnyObj()


class Spark:
    def __init__(self): self.readers = []

    @property
    def readStream(self):
        self.readers.append(Reader())
        return self.readers[-1]


class Props(dict):
    def put(self, k, v): self[k] = v


_sql = types.ModuleType("pyspark.sql")
_sql.functions = _sql.types = AnyObj()
sys.modules["pyspark"], sys.modules["pyspark.sql"] = types.ModuleType("pyspark"), _sql
sinks = load(os.path.join(SPARK, "snmp_sinks.py"), "snmp_sinks")


def kafka_opts(**env):
    sp = Spark()
    with_env(env, lambda: sinks.read_rows(sp, "b:9098", "metrics", 500))
    return sp.readers[0].opts


def admin_props(**env):
    made = []
    ns = types.SimpleNamespace
    admin = ns(listTopics=lambda: ns(names=lambda: ns(get=lambda: ["metrics"])), close=lambda: None)
    jvm = ns(java=ns(util=ns(Properties=Props)),
             org=ns(apache=ns(kafka=ns(clients=ns(admin=ns(AdminClient=ns(create=lambda p: made.append(dict(p)) or admin)))))))
    with_env(env, lambda: sinks.ensure_topics(ns(_jvm=jvm), "b:9098", ["metrics"]))
    return made[0]


BASE = [("kafka.bootstrap.servers", "b:9098"), ("subscribe", "metrics"), ("startingOffsets", "earliest")]
IAM = [("kafka.security.protocol", "SASL_SSL"), ("kafka.sasl.mechanism", "AWS_MSK_IAM"),
       ("kafka.sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;"),
       ("kafka.sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler")]
check("環境変数が無いとき、Spark の Kafka の読み取りは MSK の IAM 認証の 4 項目のまま（option の順番も同じ）",
      kafka_opts() == BASE + IAM + [("maxOffsetsPerTrigger", "500")] and kafka_opts(KAFKA_AUTH="iam") == kafka_opts())
check("KAFKA_AUTH=none: PLAINTEXT だけ（SASL の項目は付けない）",
      kafka_opts(KAFKA_AUTH="none") == BASE + [("kafka.security.protocol", "PLAINTEXT"), ("maxOffsetsPerTrigger", "500")])
check("環境変数が無いとき、トピックを作る AdminClient も MSK の IAM 認証のまま。KAFKA_AUTH=none なら PLAINTEXT",
      admin_props() == {"bootstrap.servers": "b:9098", **sinks.KAFKA_IAM_PROPS}
      and admin_props(KAFKA_AUTH="none") == {"bootstrap.servers": "b:9098", "security.protocol": "PLAINTEXT"})

rec = {"ts": 1700000000.5, "topic": "metrics", "measurement": "interface", "agent_host": "r1", "host": "h",
       "tags": {"agent_host": "r1", "ifName": "Gi0/1"}, "fields": {"ifInOctets": "123", "ifOperStatus": 1}}
posts, sigs = [], []
sinks.http_post = lambda url, body, headers, context=None: (posts.append((url, headers)), (200, '{"errors":false}'))[1]
sinks.sigv4_headers = lambda method, url, body, service, region, headers: (sigs.append((method, url, service, region)),
                                                                             dict(headers, Authorization=f"SIGV4 {service}"))[1]
PROM_HEADERS = {"Content-Type": "application/x-protobuf", "Content-Encoding": "snappy", "X-Prometheus-Remote-Write-Version": "0.1.0"}


def send(kind, **env):
    """sender を作って rec を 1 件送る（作るのも送るのも env の中）。(sender, POST の [(url, headers)], 署名の [(…)])"""
    posts.clear(); sigs.clear()

    def go():
        f = (sinks.make_opensearch_sender("https://o", "snmp-logs", "ap-northeast-1") if kind == "opensearch"
             else sinks.make_prometheus_sender("https://p/api/v1/remote_write", "ap-northeast-1"))
        f([rec])
        return f
    f = with_env(env, go)
    return f, list(posts), list(sigs)


_f, _p, _s = send("opensearch")
check("環境変数が無いとき、Spark の OpenSearch への _bulk は aoss の SigV4 のまま",
      _s == [("POST", "https://o/snmp-logs/_bulk", "aoss", "ap-northeast-1")] and _p[0][1] == {"Content-Type": "application/x-ndjson", "Authorization": "SIGV4 aoss"})
_f, _p, _s = send("prometheus")
check("環境変数が無いとき、Spark の Prometheus への remote write は aps の SigV4 のまま",
      _s == [("POST", "https://p/api/v1/remote_write", "aps", "ap-northeast-1")] and _p[0][1] == dict(PROM_HEADERS, Authorization="SIGV4 aps"))
_f, _p, _s = send("opensearch", OPENSEARCH_AUTH="basic", OPENSEARCH_PASSWORD="pw-os")
check("OPENSEARCH_AUTH=basic: 署名せず Basic 認証（ユーザーは既定 admin）",
      _s == [] and _p == [("https://o/snmp-logs/_bulk", {"Content-Type": "application/x-ndjson",
                                                        "Authorization": "Basic " + base64.b64encode(b"admin:pw-os").decode()})])
check("OPENSEARCH_AUTH=basic: executor へ運ぶ sender が持つのは文字列と辞書（と None）だけで、パスワードは持たない（送るたびに環境変数から読む）",
      all(isinstance(c.cell_contents, (str, dict, type(None))) for c in _f.__closure__) and "pw-os" not in repr([c.cell_contents for c in _f.__closure__]))
_f, _p, _s = send("opensearch", OPENSEARCH_AUTH="basic", OPENSEARCH_PASSWORD="pw-os", OPENSEARCH_USER="netops")
check("OPENSEARCH_USER でユーザーを変えられる", _p[0][1]["Authorization"] == "Basic " + base64.b64encode(b"netops:pw-os").decode())
check("OPENSEARCH_AUTH=basic で OPENSEARCH_PASSWORD が無ければ、sender を作るとき（ジョブの起動時）に ValueError",
      raises(ValueError, lambda: send("opensearch", OPENSEARCH_AUTH="basic")))
_f, _p, _s = send("prometheus", PROMETHEUS_AUTH="none")
check("PROMETHEUS_AUTH=none: 署名しない（VictoriaMetrics の vminsert）。ヘッダーは remote write の 3 つだけ",
      _s == [] and _p == [("https://p/api/v1/remote_write", PROM_HEADERS)])
check("KAFKA_AUTH / OPENSEARCH_AUTH / PROMETHEUS_AUTH の綴り違いは ValueError（黙ってマネージド版にしない）",
      raises(ValueError, lambda: kafka_opts(KAFKA_AUTH="plaintext")) and raises(ValueError, lambda: admin_props(KAFKA_AUTH="sasl"))
      and raises(ValueError, lambda: send("opensearch", OPENSEARCH_AUTH="aws")) and raises(ValueError, lambda: send("prometheus", PROMETHEUS_AUTH="basic")))


# ---- agent/evidence.py の切り替え（botocore の署名と urlopen は偽物）
class SigV4Auth:
    def __init__(self, creds, service, region): self.service, self.region = service, region
    def add_auth(self, req): req.headers["Authorization"] = f"AWS4-HMAC-SHA256 {self.service} {self.region}"


class AWSRequest:
    def __init__(self, method, url, data=None, headers=None): self.headers = dict(headers or {})


class Res:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return b'{"status": "success", "data": {"result": []}, "hits": {"hits": [{"_source": {"x": 1}}]}}'


_auth, _awsreq = types.ModuleType("botocore.auth"), types.ModuleType("botocore.awsrequest")
_auth.SigV4Auth, _awsreq.AWSRequest = SigV4Auth, AWSRequest
sys.modules.update({"botocore.auth": _auth, "botocore.awsrequest": _awsreq})
boto3.Session = lambda **kw: types.SimpleNamespace(get_credentials=lambda: "creds")
evidence = load(os.path.join(AGENT, "evidence.py"), "evidence")
evidence.OPENSEARCH_ENDPOINT, evidence.PROMETHEUS_QUERY_URL = "https://logs.example", "https://prom.example/api/v1/query"
sent = []


def ev(**env):
    """search_logs と query_metrics を 1 回ずつ。(2 つの戻り値, 送ったものの [{method, url, auth}])"""
    sent.clear()
    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: (sent.append({"method": req.get_method(), "url": req.full_url,
                                                                     "auth": req.get_header("Authorization")}), Res())[1]
    try:
        return with_env(env, lambda: (evidence.search_logs("a-ce-01"), evidence.query_metrics("up"))), list(sent)
    finally:
        urllib.request.urlopen = orig


(_logs, _met), _s = ev()
check("環境変数が無いとき、evidence は OpenSearch に aoss、Prometheus に aps の SigV4 で送る（今のまま）",
      [x["auth"] for x in _s] == [f"AWS4-HMAC-SHA256 aoss {evidence.REGION}", f"AWS4-HMAC-SHA256 aps {evidence.REGION}"]
      and _s[0]["method"] == "POST" and _s[0]["url"] == f"https://logs.example/{evidence.OPENSEARCH_INDEX}/_search"
      and _s[1]["method"] == "GET" and _s[1]["url"].startswith("https://prom.example/api/v1/query_range?") and _logs["count"] == 1 and _met["count"] == 0)
(_logs, _met), _s = ev(OPENSEARCH_AUTH="basic", PROMETHEUS_AUTH="none", OPENSEARCH_PASSWORD="pw-os")
check("OPENSEARCH_AUTH=basic / PROMETHEUS_AUTH=none: OpenSearch は Basic 認証（ユーザーは既定 admin）、Prometheus（vmselect）は署名なし",
      [x["auth"] for x in _s] == ["Basic " + base64.b64encode(b"admin:pw-os").decode(), None] and _logs["count"] == 1 and "error" not in _met)
(_logs, _met), _s = ev(OPENSEARCH_AUTH="basic")
check("OPENSEARCH_AUTH=basic でパスワードが無ければ、OpenSearch には送らずに error（hits は空）",
      "OPENSEARCH_PASSWORD" in _logs["error"] and _logs["hits"] == [] and [x["url"] for x in _s if "_search" in x["url"]] == [])
sys.modules["toolkit"].PARAM_PREFIX = "/nwc-oss"
(_logs, _met), _s = ev(OPENSEARCH_AUTH="basic")
sys.modules["toolkit"].PARAM_PREFIX = ""
check("OPENSEARCH_AUTH=basic のパスワードは、環境変数が無ければ SSM の <PARAM_PREFIX>/opensearch-password（SecureString を復号して読む）",
      state["ssm"] == [("/nwc-oss/opensearch-password", True)] and _s[0]["auth"] == "Basic " + base64.b64encode(b"admin:pw-ssm").decode())
(_logs, _met), _s = ev(OPENSEARCH_AUTH="iam", PROMETHEUS_AUTH="basic")
check("evidence: 綴り違いは送らずに error（使える値を書く）",
      "OPENSEARCH_AUTH は sigv4 / basic" in _logs["error"] and "PROMETHEUS_AUTH は sigv4 / none" in _met["error"] and _s == [])


# ---- grafana/start.sh のデータソース（一時ディレクトリに向けて走らせる。tests/test_alerts.py の start_sh と同じやり方）
def start_sh(**env):
    """(終了コード, 並んだデータソースの {ファイル名: 中身}, 最後の行, stderr)"""
    with tempfile.TemporaryDirectory() as tmp:
        sh = open(os.path.join(ROOT, "grafana", "start.sh"), encoding="utf-8").read()
        sh = sh.replace("SRC=/etc/grafana/netops", f"SRC={os.path.join(ROOT, 'grafana', 'provisioning')}")
        sh = sh.replace("/tmp/grafana-", f"{tmp}/grafana-").replace('exec /run.sh "$@"', 'echo "user=${OPENSEARCH_USER:-}"')
        assert "/etc/grafana" not in sh and "exec " not in sh
        r = subprocess.run(["sh", "-c", sh], capture_output=True, text=True,
                           env={"PATH": os.environ["PATH"], "PROMETHEUS_URL": "http://p", "OPENSEARCH_URL": "http://o", **env})
        ds = f"{tmp}/grafana-provisioning/datasources"
        files = {n: open(os.path.join(ds, n), encoding="utf-8").read() for n in sorted(os.listdir(ds))} if r.returncode == 0 else None
        return r.returncode, files, (r.stdout.strip().splitlines() or [""])[-1], r.stderr


def provisioning(d, n):
    return open(os.path.join(ROOT, "grafana", "provisioning", d, n), encoding="utf-8").read()


def uids(text):
    return re.findall(r"^\s+uid: (\S+)$", text, re.M)


_rc, _files, _last, _ = start_sh()
check("環境変数が無いとき、Grafana のデータソースはマネージド版（AMP / OpenSearch Serverless の SigV4）の定義のまま",
      _rc == 0 and _files == {"opensearch.yaml": provisioning("datasources", "opensearch.yaml"), "prometheus.yaml": provisioning("datasources", "prometheus.yaml")}
      and _last == "user=")
_rc, _files, _last, _ = start_sh(PROMETHEUS_AUTH="none", OPENSEARCH_AUTH="basic")
check("PROMETHEUS_AUTH=none / OPENSEARCH_AUTH=basic: datasources-oss の定義を同じファイル名で並べ、OPENSEARCH_USER の既定は admin",
      _rc == 0 and _files == {"opensearch.yaml": provisioning("datasources-oss", "opensearch.yaml"), "prometheus.yaml": provisioning("datasources-oss", "prometheus.yaml")}
      and _last == "user=admin")
_rc, _files, _last, _ = start_sh(PROMETHEUS_AUTH="none")
check("片方だけ切り替えられる（PROMETHEUS_AUTH=none だけなら OpenSearch はマネージド版のまま）",
      _rc == 0 and _files["prometheus.yaml"] == provisioning("datasources-oss", "prometheus.yaml") and _files["opensearch.yaml"] == provisioning("datasources", "opensearch.yaml"))
_rc, _files, _last, _err = start_sh(OPENSEARCH_AUTH="sigv5")
check("start.sh: 綴り違いは起動しない（使える値を出す）", _rc != 0 and "OPENSEARCH_AUTH は sigv4 / basic" in _err)
_oss_prom, _oss_os = provisioning("datasources-oss", "prometheus.yaml"), provisioning("datasources-oss", "opensearch.yaml")
check("OSS 版のデータソースの uid はマネージド版と同じ amp / aoss-logs（ダッシュボードとアラートのルールをそのまま使う）",
      uids(_oss_prom) == uids(provisioning("datasources", "prometheus.yaml")) == ["amp"]
      and uids(_oss_os) == uids(provisioning("datasources", "opensearch.yaml")) == ["aoss-logs"])
check("OSS 版のデータソースは SigV4 を使わず、Prometheus は素の prometheus、OpenSearch は Basic 認証でパスワードは環境変数（値を書かない）",
      "sigV4" not in _oss_prom + _oss_os and "serverless" not in _oss_os and re.search(r"^\s+type: prometheus$", _oss_prom, re.M) is not None
      and "basicAuth: true" in _oss_os and "basicAuthUser: ${OPENSEARCH_USER}" in _oss_os and "basicAuthPassword: ${OPENSEARCH_PASSWORD}" in _oss_os)

# ---- 4. oss/terraform の木（設計の 3）。ファイルを読むだけ（terraform validate は ops/check.sh が両方の木に打つ）
TF_ROOTS = ("base/ecr", "base/core", "agent", "pipeline/lab", "pipeline/stream", "pipeline/analytics", "pipeline/graph", "pipeline/nautobot", "workflow")
OSS_REAL = ("pipeline/stream", "pipeline/analytics", "pipeline/graph")
UNCHANGED = tuple(r for r in TF_ROOTS if r not in OSS_REAL)


def git_files(path):
    """git が持つ（か、持つはずの）ファイル。.terraform/・state・.build/ のような無視するものは入らない"""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", path], capture_output=True, text=True, cwd=ROOT, check=True).stdout
    return sorted(os.path.relpath(p, path) for p in out.split())


def tf_text(base, root):
    d = os.path.join(ROOT, base, *root.split("/"))
    return {n: open(os.path.join(d, n), encoding="utf-8").read() for n in sorted(os.listdir(d)) if n.endswith(".tf")}


def links_to_managed(root, names):
    """oss/terraform/<root>/<name> が相対のシンボリックリンクで、terraform/<root>/<name> と同じファイルを指す"""
    bad = []
    for n in names:
        link = os.path.join(ROOT, "oss", "terraform", *root.split("/"), n)
        if not (os.path.islink(link) and not os.path.isabs(os.readlink(link))
                and os.path.realpath(link) == os.path.realpath(os.path.join(ROOT, "terraform", *root.split("/"), n))):
            bad.append(f"{root}/{n}")
    return bad


_bad = [b for r in UNCHANGED for b in links_to_managed(r, git_files(f"terraform/{r}"))]
_extra = {r: sorted(set(git_files(f"oss/terraform/{r}")) - set(git_files(f"terraform/{r}"))) for r in UNCHANGED}
check(f"oss/terraform の変えない 6 ルートは terraform/ の同じルートのファイル全部への相対リンクで、実ファイルは oss.auto.tfvars だけ（違う: {_bad} {_extra}）",
      not _bad and all(e == ["oss.auto.tfvars"] for e in _extra.values()))
_shared = ("versions.tf", "providers.tf", "variables.tf", ".terraform.lock.hcl", "terraform.tfvars.example")
_cross = [f"{r}/{n}" for r in OSS_REAL for n in git_files(f"oss/terraform/{r}")
          if os.path.islink(os.path.join(ROOT, "oss", "terraform", *r.split("/"), n)) and links_to_managed(r, [n])]
check(f"変える 3 ルート（{', '.join(OSS_REAL)}）も共通のファイル（{', '.join(_shared)}）はリンクで、リンクは同じルートの同じ名前だけを指す（違う: {_cross}）",
      not [b for r in OSS_REAL for b in links_to_managed(r, _shared)] and not _cross)
_tfstate = {}
for r in TF_ROOTS:
    for n, s in tf_text("oss/terraform", r).items():
        for m in re.finditer(r'"\$\{path\.module\}/([^"]*terraform\.tfstate)"', s):
            _tfstate.setdefault(r, set()).add(os.path.normpath(os.path.join("oss/terraform", r, m.group(1))))
_outside = {r: sorted(p for p in v if not any(p == f"oss/terraform/{t}/terraform.tfstate" for t in TF_ROOTS)) for r, v in _tfstate.items()}
check(f"remote_state が読む state は oss/terraform の中のルートのもの（terraform/ と oss/terraform/ の state は混ざらない。外を指すもの: { {k: v for k, v in _outside.items() if v} }）",
      _tfstate and not any(_outside.values()))

_forbidden = re.compile(r"\baws_(msk|emrserverless|prometheus|neptunegraph|opensearchserverless)_\w+")
_hits = {f"{r}/{n}": sorted({m.group(0) for m in _forbidden.finditer(re.sub(r"(?m)^\s*#.*$", "", s))})
         for r in OSS_REAL for n, s in tf_text("oss/terraform", r).items()}
check(f"OSS 版の stream / analytics / graph に MSK / EMR Serverless / AMP / Neptune Analytics / OpenSearch Serverless のリソースも参照も無い（{ {k: v for k, v in _hits.items() if v} }）",
      _hits and not any(_hits.values()))

_auto = {r: os.path.join(ROOT, "oss", "terraform", *r.split("/"), "oss.auto.tfvars") for r in TF_ROOTS}
check("oss/terraform の 9 ルートに oss.auto.tfvars（project = nwc-oss）があり、実ファイルで git が無視しない（.gitignore の *.tfvars の例外）",
      all(os.path.isfile(p) and not os.path.islink(p) and re.search(r'^project = "nwc-oss"$', open(p, encoding="utf-8").read(), re.M)
          and subprocess.run(["git", "check-ignore", "-q", os.path.relpath(p, ROOT)], cwd=ROOT).returncode == 1 for p in _auto.values())
      and subprocess.run(["git", "check-ignore", "-q", "oss/terraform/workflow/.build/tools.zip"], cwd=ROOT).returncode == 0)

_vars = {r: open(os.path.join(ROOT, "terraform", *r.split("/"), "variables.tf"), encoding="utf-8").read() for r in TF_ROOTS}
_prefix = {r: "\n".join(tf_text("terraform", r).values()) for r in TF_ROOTS}
_prefix.update({f"oss:{r}": "\n".join(tf_text("oss/terraform", r).values()) for r in OSS_REAL})
check("9 ルートに var.project（既定 nwc-poc、nwc-poc と nwc-oss だけ受ける）があり、接頭辞はどれも <owner>-<project>（-nwc-poc の書き込みは無い）",
      all(re.search(r'variable "project" \{[^}]*?default\s*=\s*"nwc-poc"[\s\S]*?condition\s*=\s*contains\(\["nwc-poc", "nwc-oss"\], var\.project\)', v) for v in _vars.values())
      and all([v for v in re.findall(r"^\s*name_prefix\s*=\s*(.+)$", s, re.M) if v != "local.name_prefix"] == ['"${var.owner}-${var.project}"'] for s in _prefix.values()))

_bad_reads = {}
for r in UNCHANGED:
    for n, s in tf_text("terraform", r).items():
        for line in s.splitlines():
            if re.search(r"\$\{path\.module\}/\.\.", line) and not line.strip().startswith("#") \
                    and not re.search(r'terraform\.tfstate"', line) and not line.strip().startswith("repo_root ="):
                _bad_reads.setdefault(f"{r}/{n}", []).append(line.strip())
_repo_root = 'repo_root = fileexists("${path.module}/../../pyproject.toml") ? "${path.module}/../.." : "${path.module}/../../.."'
check(f"リンクで使うルートはリポジトリのファイルを local.repo_root から読む（path.module から上るのは state と repo_root の定義だけ: {_bad_reads}）",
      not _bad_reads and all(_repo_root in tf_text("terraform", r)["locals.tf"] for r in ("agent", "workflow"))
      and 'file("${local.repo_root}/agent/kb_index.py")' in tf_text("terraform", "agent")["kb.tf"])

_oss_tf = tf_text("terraform", "base/core")["oss.tf"]
_oss_sg = re.search(r"oss_security_groups = \{(.*?)\n  \}", _oss_tf, re.S)
_oss_sg = set(re.findall(r"^\s+(\w+)\s+=\s+\"", _oss_sg.group(1), re.M)) if _oss_sg else set()
_managed_sg = re.search(r"security_groups = \{(.*?)\n  \}", tf_text("terraform", "base/core")["security_groups.tf"], re.S)
_managed_sg = set(re.findall(r"^\s+(\w+)\s+=\s+\"", _managed_sg.group(1), re.M))
_oss_rows = re.findall(r'\{ from = ("?\w+"?), to = "(\w+)", protocol = "\w+", port = \d+', _oss_tf)
_known = (_managed_sg - {"msk"}) | _oss_sg | {"endpoints", "s3"}
check(f"OSS 版の SG（{sorted(_oss_sg)}）と通信の行は土台の oss.tf にあり、msk を外して足す。行の両端は知っている SG で、msk の行は無い",
      _oss_sg == {"kafka", "efs", "opensearch", "victoriametrics", "neo4j", "spark"} and "oss = var.project == \"nwc-oss\"" in _oss_tf
      and 'oss_replaced = local.oss ? ["msk"] : []' in _oss_tf and _oss_rows
      and all(f.strip('"') in _known | {"sg"} and t in _known for f, t in _oss_rows))
check("OSS 版の SG・行・EFS はマネージド版（project = nwc-poc）では作らない（どれも local.oss で絞る。SG とルールは security_groups.tf の for_each が作る）",
      "{ for k, v in local.oss_security_groups : k => v if local.oss }" in _oss_tf and "[for f in local.oss_flows : f if local.oss]" in _oss_tf
      and "[for f in local.sg_flows : f if !contains(local.oss_replaced, f.from) && !contains(local.oss_replaced, f.to)]" in _oss_tf
      and re.findall(r'resource "(\w+)" "\w+" \{\n  count = local\.oss \?', _oss_tf) == ["aws_efs_file_system", "aws_efs_mount_target", "aws_efs_file_system_policy"]
      and _oss_tf.count('resource "') == 3
      and "for_each = local.workload_security_groups" in tf_text("terraform", "base/core")["security_groups.tf"]
      and "for f in local.active_sg_flows :" in tf_text("terraform", "base/core")["security_groups.tf"])
check("EFS は暗号化し、TLS でない接続を拒み、このアカウントの IAM がマウントターゲット経由で読み書きするのだけを許す",
      "encrypted        = true" in _oss_tf and '"aws:SecureTransport" = "false"' in _oss_tf
      and '"elasticfilesystem:AccessedViaMountTarget" = "true"' in _oss_tf and 'security_groups = [local.sg_ids["efs"]]' in _oss_tf)
_ecr = tf_text("terraform", "base/ecr")["main.tf"]
check("OSS 版のイメージのリポジトリは project = nwc-oss のときだけ（kafka / opensearch / vminsert / vmselect / vmstorage / spark / neo4j）",
      'oss_repositories = var.project == "nwc-oss" ? toset(["kafka", "opensearch", "vminsert", "vmselect", "vmstorage", "spark", "neo4j"]) : toset([])' in _ecr
      and re.search(r'resource "aws_ecr_repository" "oss" \{\n  for_each = local\.oss_repositories\n', _ecr) is not None)
_chk = open(os.path.join(ROOT, "ops", "check.sh"), encoding="utf-8").read()
check("ops/check.sh は terraform/ と oss/terraform/ の両方に fmt と validate を打つ",
      "TF_BASES=(terraform oss/terraform)" in _chk and 'terraform fmt -check -recursive "$base"' in _chk and 'terraform -chdir="$base/$r" validate' in _chk)

print(f"通過 {passed} / 失敗 0")
