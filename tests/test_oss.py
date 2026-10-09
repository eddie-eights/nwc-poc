"""cycle 005（マネージドを OSS に置き換えた環境を作る）の切り替えの模擬テスト。AWS にも Neo4j にも触れない。
  1. 環境変数が無いとき、app/agentcore/graph.py と app/temporal/awsio.py が出す openCypher とパラメータが、切り替えを入れる前と 1 文字も違わない
     （tests/golden/neptune_cypher.json と比べる。golden は切り替えを入れる前のコードで --write-golden を付けて作った）
  2. GRAPH_BACKEND=neo4j のとき、出す Cypher に neptune.algo・`~id`・id( が無く、同じグラフの中身から同じ結果を返す。
     id で頂点を引くところには一意制約と同じラベルが付く（送った Cypher と、app/agentcore/・app/temporal/・app/graph/ のソースの文字列の両方を見る）
  3. Spark（app/spark/snmp_sinks.py）・app/agentcore/evidence.py・app/grafana/start.sh の認証の切り替え（KAFKA_AUTH / OPENSEARCH_AUTH / PROMETHEUS_AUTH / SPLUNK_HEC_TOKEN）。
     環境変数が無いときは今のまま（MSK の IAM 認証・SigV4・マネージド版のデータソース）
  4. IaC/terraform/oss（設計の 3）: 変えないルートは IaC/terraform/aws-managed/ のファイルへのシンボリックリンク、変える 3 ルート（stream / analytics / graph）に
     マネージドのサービス（MSK / EMR Serverless / AMP / Neptune Analytics / OpenSearch Serverless）が無い、接頭辞は var.project、
     土台の OSS の SG と EFS はマネージド版では作らない
  5. OSS 版の Kafka（設計の 4）: stream は msk.tf だけを kafka.tf（ECS on Fargate の KRaft 3 台、EFS、Cloud Map の名前）に替え、
     共有のファイルは Kafka の差し替え口の locals だけを読む。Telegraf は KAFKA_AUTH=none で IAM の行を消す（描画は tests/test_lab_debug.py）
  6. OSS 版の OpenSearch と VictoriaMetrics（設計の 5）: analytics の opensearch.tf（データ 2 台 + まとめ役 1 台、インデックスはタスクの
     エフェメラルストレージ）と victoriametrics.tf（vminsert 1・vmselect 1・vmstorage 3、複製数 2、vminsert は vmstorage 3 台を待つ）。
     Spark・Grafana・evidence の接続先と IaC/terraform/aws-managed/workflow が読む output が、Cloud Map の名前・コンテナのポート・土台の SG の行と合う
  7. OSS 版の Spark と Neo4j（005 の 2）: Spark は格納先の組ごとに ECS のタスク、Neo4j は ECS に 1 台。status の Lambda・Worker・Web・Nautobot の Job は Neo4j に向く
  8. AWS で動かす前の点検（005）: マネージド版と OSS 版を同じアカウントに並べても名前が重ならない（アカウントに 1 つのものを作らない）、
     サブネットは AZ の数の設定によらず 3 つで、3 台の Kafka・OpenSearch・vmstorage はサブネットが足りなければ plan で止まる、
     OSS 版の bootstrap_brokers は kafka-1〜3 の PLAINTEXT、Neo4j・OpenSearch・VictoriaMetrics を使う側の SG の行がそろう
  9. エージェントを OSS 版につなぐ（005）: 道具の Lambda（IaC/terraform/aws-managed/workflow の gateway.tf）と AgentCore の Runtime（IaC/terraform/aws-managed/agent の runtime.tf）は
     OSS 版だけ GRAPH_BACKEND・OPENSEARCH_AUTH・PROMETHEUS_AUTH と Neo4j のドライバを受け、パスワードは環境変数に置かない。
     OSS 版の graph・analytics はマネージドの口（graph_arn・コレクション・ワークスペース）を output しないので、Neptune・aoss・aps の IAM の行は 1 つも付かない。
     OSS 版の環境変数で動かすと、エージェント・道具・Worker・Web のコードは neptune-graph のクライアントも SigV4 の署名も作らず、Kafka（MSK）は読まない
 10. OSS 版の Grafana（005 の 3）: analytics の grafana.tf はマネージド版と同じイメージ・ダッシュボード・アラートのルールを使い（写しを作らない）、
     データソースは vmselect（署名なし）と自前の OpenSearch（Basic 認証）。アラートはマネージド版と同じ SNS のトピックから status の Lambda と
     ワークフローに届き、タスクロールは SNS の publish だけ
実行は python3 tests/test_oss.py。graph.py のクエリを意図して変えたときだけ --write-golden で golden を作り直す
（作り直すと 1. はその時点のコードを正とする。差分は git diff tests/golden で見る）。"""
import base64, copy, importlib.util, io, json, logging, os, re, subprocess, sys, tempfile, types, urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
AGENT, WORKFLOW, SPARK = (os.path.join(ROOT, "app", d) for d in ("agentcore", "temporal", "spark"))
GOLDEN = os.path.join(ROOT, "tests", "golden", "neptune_cypher.json")
sys.path[:0] = [AGENT, WORKFLOW]

for k in ("GRAPH_BACKEND", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE", "KAFKA_AUTH", "OPENSEARCH_AUTH",
          "PROMETHEUS_AUTH", "OPENSEARCH_USER", "OPENSEARCH_PASSWORD", "SPLUNK_HEC_TOKEN", "PARAM_PREFIX"):
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
SET_L = "SET l.status = $st"   # set_status の辺（Neo4j は count(DISTINCT l) で数える。graph.set_status）
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


def unreg_count(n):
    """count() の未登録の頂点の数を n にする答え（n >= 1）。Neptune はラベル無しの 1 本で n、Neo4j はラベルごとに分けて送る
    （graph._unregistered）ので device に n - 1・bgp_session に 1・ほかのラベルは 0 にして、足すと同じ n になるようにする"""
    return {"MATCH (n) WHERE n.registered = false RETURN count": [{"n": n}],
            "MATCH (n:`device`) WHERE n.registered = false RETURN count": [{"n": n - 1}],
            "MATCH (n:`bgp_session`) WHERE n.registered = false RETURN count": [{"n": 1}],
            "WHERE n.registered = false RETURN count": [{"n": 0}]}


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
    run("count", graph.count, **{**unreg_count(2), "count(": [{"n": 2}]})
    run("seed", lambda: graph.seed(want_devs, want_links, layers),
        **{"MATCH (n:`bgp_session`) WHERE n.registered = false RETURN n": nodes(lv_bgp),
           "MATCH (n:`interface`) WHERE n.registered = false RETURN n": [], UNREG: nodes([devs[2]]), "RETURN n": [],
           SET_ST: [{"registered": None}], **unreg_count(1), "count(": [{"n": 1}]})
    run("seed_layers", lambda: graph.seed_layers(layers),
        **{"MATCH (n:interface) RETURN": [{"id": "a-ce-01#eth1"}], "RETURN n": [], **unreg_count(1), "count(": [{"n": 1}]})
    run("sync_physical", lambda: graph.sync_physical(want_devs, want_links),
        **{DEV: nodes(devs), IFS: nodes(ifs), LINKS: edges(links), "IN $ids RETURN": [{"id": "a-ce-01"}], "count(l)": [{"n": 0}],
           SET_ST: [{"registered": None}], **unreg_count(1), "count(": [{"n": 1}]})
    run("sync_changes", lambda: graph.sync_changes(changes), **{"MATCH (n:change) RETURN": [{"id": "change#old"}, {"id": "change#keep"}]})
    run("add_device", lambda: graph.add_device("c-ce-01", "c", "leaf", "203.0.113.15", 65003), **{REG: []})
    run("add_device_replace", lambda: graph.add_device("zz-ce-09", "zz", "ce"), **{REG: [{"registered": False}], "RETURN n.status AS status": [{"status": "ALARM"}]})
    run("remove_device", lambda: graph.remove_device("a-ce-01"), **{REG: [{"registered": None}]})
    run("add_link", lambda: graph.add_link("b-ce-01", "eth2", "a-ce-01", "eth3", "fabric", "secondary", 100),
        **{"IN $ids RETURN": [{"id": "a-ce-01"}, {"id": "b-ce-01"}], "count(l)": [{"n": 0}]})
    run("remove_link", lambda: graph.remove_link("b-ce-01", "a-ce-01", "eth3"), **{"count(l)": [{"n": 1}]})
    run("remove_link_all", lambda: graph.remove_link("a-ce-01", "b-ce-01"), **{"count(l)": [{"n": 2}]})
    run("set_status_if", lambda: graph.set_status("a-ce-01", "eth1", "down"), **{SET_L: [{"n": 1}], SET_ST: [{"registered": None}]})
    run("set_status_if_unregistered", lambda: graph.set_status("zz-ce-09", "eth7", "DOWN"), **{SET_L: [{"n": 0}], SET_ST: []})
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
    state["answer"] = {"MATCH (n:device) RETURN": [{"id": "a-ce-01", "status": "DOWN", "maintenance": True, "role": "ce"}, {"id": "b-ce-01", "status": None}],
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
check("環境変数が無いとき、app/temporal/awsio.py の read_topology の openCypher も同じ", calls["awsio"] == golden["awsio"])
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
index = [q for q, _ in calls4 if q.startswith("CREATE INDEX")]
body4 = [c for c in calls4 if not c[0].startswith(("CREATE CONSTRAINT", "CREATE INDEX"))]
n4 = len(body4)
_LBL = re.compile(r"\((\w+):`(\w+)`\)")   # ラベルだけのノード (v:`label`)（_lbl が付けるもの）
# Neo4j だけ書き方を変えた文（Neptune の文 → Neo4j で送る文）。a 側か b 側かの OR が 2 つの頂点にまたがると、ラベルがあっても
# Neo4j は id の索引を使えず device を全部読むので、機器を id で 1 つ引いてから辺の向きを見る（graph.set_status のコメント）
_REWRITE4 = {
    "MATCH (a)-[l:link]->(b) WHERE (id(a) = $dev AND l.a_if = $ifn) OR (id(b) = $dev AND l.b_if = $ifn) SET l.status = $st RETURN count(l) AS n":
    "MATCH (d:`device`)-[l:link]-(:`device`) WHERE d.id = $dev AND ((startNode(l) = d AND l.a_if = $ifn) OR (endNode(l) = d AND l.b_if = $ifn)) "
    "SET l.status = $st RETURN count(DISTINCT l) AS n"}
# Neo4j だけラベルごとに分けて送る文（Neptune の 1 文 → Neo4j の何文か。ラベル無しの MATCH (n) は registered の索引を使えないので、
# graph._unregistered が分ける。seed が読むのは置き換える機器とインタフェースだけ）
_SPLIT4 = {"MATCH (n) WHERE n.registered = false RETURN count(n) AS n":
           [f"MATCH (n:`{x}`) WHERE n.registered = false RETURN count(n) AS n" for x in labels],
           "MATCH (n) WHERE n.registered = false RETURN n":
           [f"MATCH (n:`{x}`) WHERE n.registered = false RETURN n" for x in ("device", "interface")]}
golden4 = [(q, p, q4) for q, p in golden["graph"] for q4 in _SPLIT4.get(q, [None])]   # q4 は分けた文（分けない文は None）
check("neo4j: 送るのは golden（Neptune の openCypher）を _dialect で直し、id で引く頂点にラベル（_lbl）を足したものだけで、"
      "パラメータも順番も同じ（centrality より前の全関数。_REWRITE4 の 1 文だけは決めた書き方に替え、_SPLIT4 の 2 文はラベルごとに分ける）",
      len(golden4) - n4 == 3 and all("neptune.algo." in q for q, _, _ in golden4[n4:])
      and sum(q in _REWRITE4 for q, _ in golden["graph"]) >= 2 and {q for q, _ in golden["graph"]} >= set(_SPLIT4)
      and all((q4 == split if split else q4 == _REWRITE4[q] if q in _REWRITE4 else _LBL.sub(r"(\1)", q4) == _LBL.sub(r"(\1)", graph4._dialect(q))
               and set(_LBL.findall(graph4._dialect(q))) <= set(_LBL.findall(q4))) and p4 == p
              for (q, p, split), (q4, p4) in zip(golden4, body4)))
check("neo4j: ラベル無しで未登録の頂点を読む文（MATCH (n) WHERE n.registered）を送らない（全部の頂点を読む）",
      not any(q.startswith("MATCH (n) WHERE n.registered") for q, _ in calls4) and any(q.startswith("MATCH (n) WHERE n.registered") for q, _ in golden["graph"]))

# ラベル無しの id 検索（BACKLOG の「Neo4j の id 検索にラベルを付ける」）。Neo4j の一意制約と索引はラベルごとなので、
# (n) WHERE n.id = $id のようにラベルが無いと索引を使えず、全部の頂点を読む。Neptune の ~id はグラフ全体で一意なので要らない
_ID_CMP = re.compile(r"\bid\((\w+)\)\s*(?:=|IN\b)|\b(\w+)\.id\s*(?:=|IN\b)")   # id(v) = … / v.id IN …
_ID_PROP = re.compile(r"\((\w+)\s*\{\s*(?:`~id`|id)\s*:")                        # (v {id: …})（ラベル無しの MATCH / MERGE）


def id_seeks(q, hole=None):
    """q の中で id で頂点を引いている変数と、そのノードのラベル（無ければ None。hole は f-string の {…} の印で、そこはラベルとみなす）"""
    out = []
    for m in _ID_CMP.finditer(q):
        v = m.group(1) or m.group(2)
        lab = re.search(rf"\({v}:`?(\w+)`?[\s)]", q) or (hole and re.search(rf"\({v}({re.escape(hole)})", q))
        out.append((v, lab and lab.group(1)))
    return out + [(m.group(1), None) for m in _ID_PROP.finditer(q)]


_seeks4 = [(q, v, lab) for q, _ in calls4 for v, lab in id_seeks(q)]
check("neo4j: id で頂点を引くところには、必ず一意制約と同じラベル（device / interface / change / 上の層の 5 つ）が付く（ラベル無しの id 検索が無い）",
      len(_seeks4) > 40 and all(lab in labels for _, _, lab in _seeks4))
check("neo4j: ラベル無しの id 検索を見分ける（見落としで上が素通りしない）",
      id_seeks("MATCH (n) WHERE n.id = $id") == [("n", None)] and id_seeks("MATCH (a), (b:`device`) WHERE a.id = r.f AND b.id IN $ids")
      == [("a", None), ("b", "device")] and id_seeks("MERGE (n {id: $x})") == [("n", None)] and id_seeks("MATCH (n:`device`) RETURN n.id AS id") == []
      and id_seeks("MATCH (n<?>) WHERE id(n) = $id", "<?>") == [("n", "<?>")] and id_seeks("MATCH (n) WHERE id(n) IN $ids", "<?>") == [("n", None)])


def source_seeks(paths):
    """ソースの文字列（f-string の {…} は <?> にする。docstring は除く）のうち、id で頂点を引くのにラベルが無いもの [(ファイル, 行, 文字列)]"""
    import ast
    bad = []
    for path in paths:
        tree = ast.parse(open(path, encoding="utf-8").read())
        inner = {id(c) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for c in n.values}
        docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.JoinedStr):
                s = "".join(c.value if isinstance(c, ast.Constant) else "<?>" for c in n.values)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in inner | docs:
                s = n.value
            else:
                continue
            if any(lab is None for _, lab in id_seeks(s, "<?>")):
                bad.append((os.path.relpath(path, ROOT), n.lineno, s))
    return bad


_src = [os.path.join(ROOT, d, f) for d in (os.path.join("app", "agentcore"), os.path.join("app", "temporal"), os.path.join("app", "graph"))
        for f in sorted(os.listdir(os.path.join(ROOT, d))) if f.endswith(".py")]
_bad_src = source_seeks(_src)
check("app/agentcore/・app/temporal/・app/graph/ のソースに、ラベル（:label か f-string の {_lbl(…)}）の無い id 検索の Cypher が無い",
      not _bad_src and len(_src) >= 12 and os.path.join(ROOT, "app", "agentcore", "graph.py") in _src)
if _bad_src:
    print("   ラベル無し:", _bad_src)
with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as _f:
    _f.write('def f(label):\n    """MATCH (n) WHERE id(n) = $id は docstring なので見ない"""\n'
             '    query(f"MATCH (n{_lbl(label)}) WHERE id(n) = $id")\n    query(f"MATCH (n) WHERE id(n) IN $ids AND n.x = {label}")\n')
check("ソースの検査は、ラベル無しの id 検索を f-string でも見つけ、docstring とラベルの {…} 付きのものは通す",
      [(line, s) for _, line, s in source_seeks([_f.name])] == [(4, "MATCH (n) WHERE id(n) IN $ids AND n.x = <?>")])
os.unlink(_f.name)

# Neo4j では消す頂点と張る辺をラベルごとに分けて送る。Neptune は今まで通り 1 本（golden の scenario はラベルが 1 つずつなので、ここで混ぜる）
_sent, _query = [], graph4.query
graph4.query = lambda c, **p: (_sent.append([c, p]), [{"n": 0}] if "count(" in c else [])[1]
_mixed = {"vertices": [{"id": "x#ip#e1", "label": "ip_interface"}, {"id": "x#isis#e1", "label": "isis_adjacency"},
                       {"id": "x#isis#e2", "label": "isis_adjacency"}],
          "edges": [{"label": "over", "from": "x#ip#e1", "to": "x#e1"}, {"label": "over", "from": "x#isis#e1", "to": "x#ip#e1"},
                    {"label": "over", "from": "x#isis#e2", "to": "x#ip#e1"}]}


def _split(backend):
    graph4.BACKEND, _sent[:] = backend, []
    graph4._delete_ids([("d1", "device"), ("i1", "interface"), ("d2", "device")])
    graph4.seed_layers(_mixed, {"x#e1"})
    return [c for c in _sent if "DETACH DELETE n" in c[0] and "IN $ids" in c[0] or "CREATE (a)-" in c[0]]


try:
    _split4, _split1 = _split("neo4j"), _split("neptune")
finally:
    graph4.BACKEND, graph4.query = "neo4j", _query
_E = "UNWIND $rows AS r MATCH (a{}), (b{}) WHERE id(a) = r.f AND id(b) = r.t CREATE (a)-[:`over`]->(b)"
check("neo4j: 消す頂点はラベルごとに 1 本、辺は型と両端のラベルの組ごとに 1 本で送り、ラベルは頂点の種類（物理層の id はインタフェース）",
      _split4 == [["MATCH (n:`device`) WHERE id(n) IN $ids DETACH DELETE n", {"ids": ["d1", "d2"]}],
                  ["MATCH (n:`interface`) WHERE id(n) IN $ids DETACH DELETE n", {"ids": ["i1"]}],
                  [_E.format(":`ip_interface`", ":`interface`"), {"rows": [{"f": "x#ip#e1", "t": "x#e1"}]}],
                  [_E.format(":`isis_adjacency`", ":`ip_interface`"), {"rows": [{"f": "x#isis#e1", "t": "x#ip#e1"}, {"f": "x#isis#e2", "t": "x#ip#e1"}]}]])
check("Neptune: 同じものを、今まで通り消すのは 1 本・辺は型ごとに 1 本で、ラベルを付けずに送る",
      _split1 == [["MATCH (n) WHERE id(n) IN $ids DETACH DELETE n", {"ids": ["d1", "i1", "d2"]}],
                  [_E.format("", ""), {"rows": [{"f": "x#ip#e1", "t": "x#e1"}, {"f": "x#isis#e1", "t": "x#ip#e1"}, {"f": "x#isis#e2", "t": "x#ip#e1"}]}]])
check("neo4j: Cypher に neptune.algo・`~id`・id(…) と、予約語のままの AS from / AS to が無い",
      not any("neptune.algo" in q or "`~id`" in q or re.search(r"\bid\(", q) or re.search(r"\bAS (from|to)\b", q) for q, _ in calls4))
check("neo4j: _dialect は id(x) を x.id に、`~id` を id に、AS from / to を `from` / `to` に直し、ほかは変えない",
      graph4._dialect("MATCH (n) WHERE id(n) = $id MERGE (m {`~id`: $x}) RETURN id(m) AS a, valid(n) AS from, n.to AS to, 1 AS fromage")
      == "MATCH (n) WHERE n.id = $id MERGE (m {id: $x}) RETURN m.id AS a, valid(n) AS `from`, n.to AS `to`, 1 AS fromage")
check("neo4j: 頂点の id の一意制約を、最初のクエリの前に 1 度だけ、ラベルごとに作る（IF NOT EXISTS）",
      [q for q, _ in calls4[:len(schema)]] == schema and len(schema) == len(labels)
      and all(f"FOR (n:`{l}`) REQUIRE n.id IS UNIQUE" in q and "IF NOT EXISTS" in q for l, q in zip(labels, schema)))
check("neo4j: 制約のすぐあとに、全ラベルの registered と interface の device_id の索引を 1 度だけ張る（IF NOT EXISTS）",
      [q for q, _ in calls4[len(schema):len(schema) + len(index)]] == index
      and index == [f"CREATE INDEX nwc_{l}_registered IF NOT EXISTS FOR (n:`{l}`) ON (n.registered)" for l in labels]
      + ["CREATE INDEX nwc_interface_device_id IF NOT EXISTS FOR (n:`interface`) ON (n.device_id)"])
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
state["answer"], state["calls"] = dict(ALGO, **{"gds.closeness": RuntimeError("closeness に失敗"), "gds.graph.drop": RuntimeError("drop も失敗")}), []
def _catch(f):
    try:
        f()
    except Exception as e:
        return e
_err = _catch(graph4.centrality)
check("neo4j の centrality: 失敗したあとの drop まで失敗しても、上がるのは元の失敗（GDS が無い等を drop の失敗で隠さない）",
      isinstance(_err, RuntimeError) and str(_err) == "closeness に失敗" and "gds.graph.drop" in state["calls"][-1][0])
state["answer"], state["calls"] = dict(ALGO, **{"gds.graph.project": RuntimeError("GDS が無い")}), []
_err = _catch(graph4.centrality)
check("neo4j の centrality: 射影がドライバの包まない失敗（RuntimeError 等）で止まったら、写しは無いので drop を打たずにそのまま上げる",
      isinstance(_err, RuntimeError) and str(_err) == "GDS が無い" and not any("gds.graph.drop" in q for q, _ in state["calls"]))

# ドライバの例外の偽物（neo4j.exceptions）。graph.errors() が足す型と、射影の失敗の種類で drop を打つかの検査に使い、最後に外す
_exc = types.ModuleType("neo4j.exceptions")
class Neo4jError(Exception): pass
class DriverError(Exception): pass
_exc.Neo4jError, _exc.DriverError = Neo4jError, DriverError
_e_none = graph4.errors()
sys.modules["neo4j.exceptions"] = _exc; neo4j.exceptions = _exc
_e4 = graph4.errors()
graph4.BACKEND = "neptune"; _e1 = graph4.errors(); graph4.BACKEND = "neo4j"
check("neo4j: graph.errors() はドライバの neo4j.exceptions があれば Neo4jError / DriverError を足し、無ければ boto の 2 つ。Neptune のままなら boto の 2 つだけ",
      _e_none == (ClientError, BotoCoreError) and _e4 == (ClientError, BotoCoreError, Neo4jError, DriverError) and _e1 == (ClientError, BotoCoreError))
state["answer"], state["calls"] = dict(ALGO, **{"gds.graph.project": Neo4jError("A graph with name x already exists")}), []
_err = _catch(graph4.centrality)
check("neo4j の centrality: 射影がサーバーの失敗（Neo4jError。ドライバのやり直しで 2 度目が already exists になる等）なら、残っているかもしれない写しを同じ名前で消してから上げる",
      isinstance(_err, Neo4jError) and "gds.graph.drop" in state["calls"][-1][0] and state["calls"][-1][1]["g"] == state["calls"][-2][1]["g"]
      and "gds.graph.project" in state["calls"][-2][0])
state["answer"], state["calls"] = dict(ALGO, **{"gds.graph.project": DriverError("つながらない")}), []
_err = _catch(graph4.centrality)
check("neo4j の centrality: 射影がドライバの失敗（DriverError。つながらない・切れた）なら、送っても届かないので drop を打たずに上げる",
      isinstance(_err, DriverError) and not any("gds.graph.drop" in q for q, _ in state["calls"]))

# app/agentcore/topology.py を Neo4j の設定で読み込み、graph.errors() の型が実際に受け止められることを見る（文字列の検査だけにしない）
state["answer"], state["calls"] = {"": DriverError("切れた")}, []   # 空の鍵は全部のクエリに当たる（最初の読み込みが落ちる）
_topo_log = logging.getLogger("topology")
_topo_warns = []
_grab = type("Grab", (logging.Handler,), {"emit": lambda self, r: _topo_warns.append(r.getMessage())})()
_topo_log.addHandler(_grab)
topology4 = load(os.path.join(AGENT, "topology.py"), "topology")
_topo_log.removeHandler(_grab)
check("neo4j の topology.py: 読み込みが DriverError で落ちたら 500 にせず静的データに戻る（SOURCE が static、機器は data/ の 7 台）",
      topology4.SOURCE == "static" and len(topology4.DEVICES) == 7 and any("device" in q for q, _ in state["calls"]))
check("neo4j の topology.py: 読めなかったときの WARNING は graph の名前（neo4j）で出し、neptune とは書かない",
      _topo_warns == ["neo4j read failed, using static data: 切れた"])
_cent_err = {}
for _name, _e in (("DriverError", DriverError("切れた")), ("Neo4jError", Neo4jError("There is no procedure with the name gds.closeness.stream"))):
    state["answer"], state["calls"] = dict(ALGO, **{"gds.closeness": _e}), []
    _cent_err[_name] = topology4.centrality()
check("neo4j の topology.py: centrality の途中の DriverError / Neo4jError は「中心性を計算できない: …」の答えになり、射影は消す",
      all(v["error"].startswith("中心性を計算できない: ") and v["devices"] == [] for v in _cent_err.values())
      and "切れた" in _cent_err["DriverError"]["error"] and "gds.closeness" in _cent_err["Neo4jError"]["error"]
      and "gds.graph.drop" in state["calls"][-1][0])
state["answer"], state["calls"] = ALGO, []
check("neo4j の topology.py: 失敗しなければ GDS の結果に note を付けて返す",
      topology4.centrality(limit=2)["note"].startswith("degree は回線の数") and "gds.graph.drop" in state["calls"][-1][0])
state["answer"], state["calls"] = dict(ALGO, **{"gds.closeness": RuntimeError("ドライバが包まない失敗")}), []
check("neo4j の topology.py: ドライバが包まない失敗（RuntimeError 等）は graph.errors() の範囲外なので、そのまま上がる（握りつぶさない）",
      raises(RuntimeError, topology4.centrality))
# 元データの名前（root_cause などの source）は書き先の名前。前は OSS 版でも neptune（2026-10-08 の OSS 版の検証の「docs のずれ」3）
state["answer"], state["calls"] = {DEV: nodes(devs), IFS: nodes(ifs), LINKS: edges(links[:2]), LAYER_E: [], "RETURN n": []}, []
_src_full = topology4.reload(force=True)
_src_rc, _src_graph = topology4.root_cause()["source"], topology4.topology_graph()["source"]
state["answer"] = {LINKS: [], LAYER_E: [], "RETURN n": []}
_src_empty = topology4.reload(force=True)
check(f"neo4j の topology.py: 元データは neo4j（root_cause と topology_graph の source も）、Neo4j が空なら neo4j-empty（{_src_full} / {_src_rc} / {_src_empty}）",
      _src_full == _src_rc == _src_graph == "neo4j" and _src_empty == "neo4j-empty" and len(topology4.DEVICES) == 7)
_tv = open(os.path.join(ROOT, "app", "dashboard", "topology_view.py"), encoding="utf-8").read()
check("Web のトポロジの元データの表示は neo4j / neo4j-empty も Neo4j と書く（静的データの案内に落ちない）",
      '"neo4j": "Neo4j（IaC/terraform/oss/pipeline/graph）"' in _tv and '"neo4j-empty": "Neo4j は空。' in _tv)

# 一意制約の張り直し（005 のレビューの Nit 1）。Neo4j のタスクが入れ替わると制約も消えるので、時間がたつか、ドライバが失敗したら張り直す。
# 時計は graph4.time を差し替えて進め、WARNING は graph4.log を差し替えて拾う
_clock, _warns = [1000.0], []
_real_time, graph4.time = graph4.time, types.SimpleNamespace(monotonic=lambda: _clock[0])
_real_log, graph4.log = graph4.log, types.SimpleNamespace(warning=lambda msg, *a: _warns.append(msg % a))
_q = "MATCH (n:device) RETURN n.id AS id"
def _schema_sent():
    state["calls"] = []
    graph4.query(_q)
    return [q for q, _ in state["calls"] if q.startswith("CREATE CONSTRAINT")]
state["answer"] = {}
graph4._cache["schema"] = None
_first = _schema_sent()
_clock[0] += graph4.SCHEMA_TTL - 1
_within = _schema_sent()
_clock[0] += 1
_after = _schema_sent()
check("neo4j: 一意制約は張ってから SCHEMA_TTL 秒（60）は張り直さず、過ぎたら次のクエリの前にまた全ラベルに張る（入れ替わった Neo4j に制約を戻す）",
      graph4.SCHEMA_TTL == 60 and len(_first) == len(labels) and _within == [] and _after == _first)
state["answer"] = {_q: DriverError("切れた")}
_err = _catch(lambda: graph4.query(_q))
state["answer"] = {}
check("neo4j: クエリがドライバの失敗（DriverError）で落ちたら、時間がたっていなくても次のクエリの前に制約を張り直す",
      isinstance(_err, DriverError) and _schema_sent() == _first and _schema_sent() == [])
_clock[0] += graph4.SCHEMA_TTL
state["answer"], state["calls"] = {"CREATE CONSTRAINT nwc_interface_id": Neo4jError("Unable to create Constraint( name='nwc_interface_id' ): Both Node(1) and Node(2) have the label `interface`")}, []
_rows = graph4.query(_q)
check("neo4j: 重複があって張れない制約（Neo4jError）は WARNING に出して先に進み、ほかのラベルの制約とクエリは打つ（seed で重複を消せるように）",
      [q for q, _ in state["calls"] if q.startswith("CREATE CONSTRAINT")] == _first and state["calls"][-1][0] == _q
      and _rows == [{"n": 1}] and len(_warns) == 1 and "interface" in _warns[0] and "60 秒後" in _warns[0])
_clock[0] += graph4.SCHEMA_TTL
state["answer"], state["calls"] = {"CREATE CONSTRAINT": DriverError("つながらない")}, []
_err = _catch(lambda: graph4.query(_q))
_sent = state["calls"]
state["answer"] = {}
check("neo4j: 制約を張る途中のドライバの失敗（DriverError）はそのまま上げてクエリを打たず、張ったことにしない（次のクエリでまた張る）",
      isinstance(_err, DriverError) and len(_sent) == 1 and _sent[0][0].startswith("CREATE CONSTRAINT") and _schema_sent() == _first)
_clock[0] += graph4.SCHEMA_TTL
_warns.clear()
state["answer"], state["calls"] = {"CREATE INDEX nwc_interface_device_id": Neo4jError("Index already exists with different name")}, []
_rows = graph4.query(_q)
check("neo4j: 張れない索引（Neo4jError）も WARNING に出して先に進み、制約・ほかの索引・クエリは打つ",
      [q for q, _ in state["calls"]] == schema + index + [_q] and _rows == [{"n": 1}]
      and len(_warns) == 1 and "interface.device_id" in _warns[0] and "60 秒後" in _warns[0])
_clock[0] += graph4.SCHEMA_TTL
state["answer"], state["calls"] = {"CREATE INDEX": DriverError("切れた")}, []
_err = _catch(lambda: graph4.query(_q))
_sent_idx = state["calls"]
state["answer"] = {}
check("neo4j: 索引を張る途中のドライバの失敗（DriverError）もそのまま上げてクエリを打たず、張ったことにしない（次のクエリでまた張る）",
      isinstance(_err, DriverError) and [q for q, _ in _sent_idx] == schema + index[:1] and _schema_sent() == _first)
graph4.time, graph4.log = _real_time, _real_log
del sys.modules["neo4j.exceptions"]; del neo4j.exceptions; del sys.modules["topology"]
with open(os.path.join(AGENT, "topology.py"), encoding="utf-8") as f:
    _topo_src = f.read()
with open(os.path.join(ROOT, "app", "dashboard", "topology_view.py"), encoding="utf-8") as f:
    _web_src = f.read()
check("app/agentcore/topology.py の読み込み・変更履歴・中心性と、app/dashboard/topology_view.py の編集（_graph_call）の except は graph.errors() で受ける（OSS 版で Neo4j の失敗が 500 にならない）",
      _topo_src.count("except graph.errors() as e:") == 2 and "except (*graph.errors(), KeyError, ValueError, TypeError) as e:" in _topo_src
      and "except (ClientError, BotoCoreError" not in _topo_src
      and "except (*graph.errors(), KeyError, ValueError, TypeError) as e:" in _web_src
      and "except (ClientError, BotoCoreError" not in _web_src and "from botocore.exceptions import" not in _web_src)

state["calls"] = []
FakeDriver.made.clear()
awsio4 = load(os.path.join(WORKFLOW, "awsio.py"), "awsio")
topo4 = awsio_scenario(awsio4)
check("neo4j: awsio.read_topology は同じ中身から Neptune と同じトポロジを返し、送るのは golden の id(x) を x.id に直したもの",
      topo4 == neptune_topo and state["calls"] == [[re.sub(r"\bid\((\w+)\)", r"\1.id", q), p] for q, p in golden["awsio"]]
      and not any(re.search(r"\bid\(", q) for q, _ in state["calls"]))
_samples = [q for q, _ in golden["graph"] + golden["awsio"]] + [
    "MATCH (n) WHERE id(n) = $id MERGE (m {`~id`: $x}) RETURN id(m) AS a, valid(n) AS from, n.to AS to, 1 AS fromage"]
check("neo4j: awsio._dialect は app/agentcore/graph.py の _dialect の写しで、golden の全クエリと `~id`・AS from / to を含む文で同じ答えを返す（005 のレビューの Nit 2）",
      all(awsio4._dialect(q) == graph4._dialect(q) for q in _samples)
      and awsio4._dialect(_samples[-1]) != _samples[-1] and "`~id`" not in awsio4._dialect(_samples[-1]))
check("neo4j: awsio のドライバも NEO4J_USER / NEO4J_PASSWORD で、worker.py が起動時に見る変数は NEO4J_URI",
      awsio4.GRAPH_ENV == "NEO4J_URI" and len(FakeDriver.made) == 1 and FakeDriver.made[0].auth == ("neo4j", "pw-neo4j")
      and FakeDriver.made[0].uri == NEO4J["NEO4J_URI"])

_bad = []
for _path, _name in ((os.path.join(AGENT, "graph.py"), "graph_bad"), (os.path.join(WORKFLOW, "awsio.py"), "awsio_bad")):
    _bad.append(with_env({"GRAPH_BACKEND": "neptune-analytics"}, lambda: raises(ValueError, lambda: load(_path, _name))))
check("GRAPH_BACKEND が neptune / neo4j 以外なら、graph.py も awsio.py も import で止まる（綴り違いで黙って Neptune を読まない）", _bad == [True, True])

# status Lambda（app/graph/status_handler.py）は、graph.py の Neo4j のドライバを短い設定で先に作る
state["calls"] = []
FakeDriver.made.clear()
graph5 = load(os.path.join(AGENT, "graph.py"), "graph")
status = load(os.path.join(ROOT, "app", "graph", "status_handler.py"), "status_handler")
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


# ---- 3. Spark（app/spark/snmp_sinks.py）の認証の切り替え。pyspark は何でも受ける偽物、readStream は option を順に覚える
class AnyObj:
    def __getattr__(self, k): return self
    def __call__(self, *a, **kw): return self
    def __getitem__(self, k): return self
    def __truediv__(self, o): return self   # flows の time_received_ns（ナノ秒）を秒にする割り算（snmp_sinks.read_rows）
    def __or__(self, o): return self        # gnmic の event かを見る条件の組み立て（snmp_sinks.read_rows。cycle 013）
    def __and__(self, o): return self


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
check("KAFKA_AUTH=none: SASL/SCRAM の収集器の ACL は入れない（OSS 版の Kafka に authorizer は無い。jvm に触らず [] を返す）",
      with_env({"KAFKA_AUTH": "none"}, lambda: sinks.ensure_acls(types.SimpleNamespace(), "kafka-1:9092")) == [])

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
_f, _p, _s = send("opensearch", OPENSEARCH_AUTH="basic", OPENSEARCH_PASSWORD="pw-os", OPENSEARCH_USER="nwc")
check("OPENSEARCH_USER でユーザーを変えられる", _p[0][1]["Authorization"] == "Basic " + base64.b64encode(b"nwc:pw-os").decode())
check("OPENSEARCH_AUTH=basic で OPENSEARCH_PASSWORD が無ければ、sender を作るとき（ジョブの起動時）に ValueError",
      raises(ValueError, lambda: send("opensearch", OPENSEARCH_AUTH="basic")))
_f, _p, _s = send("prometheus", PROMETHEUS_AUTH="none")
check("PROMETHEUS_AUTH=none: 署名しない（VictoriaMetrics の vminsert）。ヘッダーは remote write の 3 つだけ",
      _s == [] and _p == [("https://p/api/v1/remote_write", PROM_HEADERS)])
check("KAFKA_AUTH / OPENSEARCH_AUTH / PROMETHEUS_AUTH の綴り違いは ValueError（黙ってマネージド版にしない）",
      raises(ValueError, lambda: kafka_opts(KAFKA_AUTH="plaintext")) and raises(ValueError, lambda: admin_props(KAFKA_AUTH="sasl"))
      and raises(ValueError, lambda: send("opensearch", OPENSEARCH_AUTH="aws")) and raises(ValueError, lambda: send("prometheus", PROMETHEUS_AUTH="basic")))

# Splunk の HEC の token: OSS 版は ECS の secrets が SSM の SecureString を SPLUNK_HEC_TOKEN に入れる。無ければマネージド版のまま SSM から読む
_ssm_reads = []
sinks.read_ssm_parameter = lambda name, region: (_ssm_reads.append(name), "tok-ssm")[1]
_SPLUNK_ARGS = ["--bootstrap", "b:9092", "--checkpoint", "s3a://b/spark/checkpoint", "--sinks", "splunk", "--splunk-hec-url", "https://s:8088"]


def parse_ok(argv, **env):
    saved = sys.stderr
    sys.stderr = io.StringIO()
    try:
        return with_env(env, lambda: sinks.parse_args(argv)) is not None
    except SystemExit:
        return False
    finally:
        sys.stderr = saved


def splunk_send(make, **env):
    """splunk の sender を作って rec を 1 件送る。(sender, 作った時点の SSM の読み, POST の [(url, headers)], 全体の SSM の読み)"""
    posts.clear(); _ssm_reads.clear()

    def go():
        f = make()
        at_make = list(_ssm_reads)
        f([rec])
        return f, at_make
    f, at_make = with_env(env, go)
    return f, at_make, list(posts), list(_ssm_reads)


check("環境変数が無いとき、--sinks splunk は --splunk-token-parameter が要る（マネージド版のまま）。SPLUNK_HEC_TOKEN があれば要らない",
      not parse_ok(_SPLUNK_ARGS) and parse_ok(_SPLUNK_ARGS, SPLUNK_HEC_TOKEN="tok-env")
      and parse_ok(_SPLUNK_ARGS + ["--splunk-token-parameter", "/p/splunk/hec-token"]))
check("splunk_token: 環境変数が無いときは SSM から読み、SPLUNK_HEC_TOKEN があれば SSM を読まずにそれを使う",
      (_ssm_reads.clear(), sinks.splunk_token("/p/splunk/hec-token", "r"), _ssm_reads == ["/p/splunk/hec-token"])[-1]
      and (_ssm_reads.clear(), with_env({"SPLUNK_HEC_TOKEN": "tok-env"}, lambda: sinks.splunk_token("", "r")) == "tok-env" and _ssm_reads == [])[-1])
_f, _at, _p, _r = splunk_send(lambda: sinks.make_splunk_sender_on_executor("https://s:8088", "", "r", "", True), SPLUNK_HEC_TOKEN="tok-env")
check("SPLUNK_HEC_TOKEN: executor へ運ぶ splunk の sender は token を持たず（作るときも送るときも SSM を読まない）、送るたびに環境変数から読んで Authorization: Splunk <token> で送る",
      _at == [] and _r == [] and "tok-env" not in repr([c.cell_contents for c in _f.__closure__])
      and _p == [("https://s:8088/services/collector/event", {"Authorization": "Splunk tok-env", "Content-Type": "application/json"})])
_f, _at, _p, _r = splunk_send(lambda: sinks.make_splunk_sender_on_executor("https://s:8088", "/p/splunk/hec-token", "r"))
check("環境変数が無いとき、executor の splunk の sender は送るときに SSM から token を読む（マネージド版のまま）",
      _at == [] and _r == ["/p/splunk/hec-token"] and _p[0][1]["Authorization"] == "Splunk tok-ssm")


# ---- app/agentcore/evidence.py の切り替え（botocore の署名と urlopen は偽物）
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


# ---- app/grafana/start.sh のデータソース（一時ディレクトリに向けて走らせる。tests/test_alerts.py の start_sh と同じやり方）
def start_sh(**env):
    """(終了コード, 並んだデータソースの {ファイル名: 中身}, 最後の行, stderr)"""
    with tempfile.TemporaryDirectory() as tmp:
        sh = open(os.path.join(ROOT, "app", "grafana", "start.sh"), encoding="utf-8").read()
        sh = sh.replace("SRC=/etc/grafana/nwc", f"SRC={os.path.join(ROOT, 'app', 'grafana', 'provisioning')}")
        sh = sh.replace("/tmp/grafana-", f"{tmp}/grafana-").replace('exec /run.sh "$@"', 'echo "user=${OPENSEARCH_USER:-}"')
        assert "/etc/grafana" not in sh and "exec " not in sh
        r = subprocess.run(["sh", "-c", sh], capture_output=True, text=True,
                           env={"PATH": os.environ["PATH"], "PROMETHEUS_URL": "http://p", "OPENSEARCH_URL": "http://o", **env})
        ds = f"{tmp}/grafana-provisioning/datasources"
        files = {n: open(os.path.join(ds, n), encoding="utf-8").read() for n in sorted(os.listdir(ds))} if r.returncode == 0 else None
        return r.returncode, files, (r.stdout.strip().splitlines() or [""])[-1], r.stderr


def provisioning(d, n):
    return open(os.path.join(ROOT, "app", "grafana", "provisioning", d, n), encoding="utf-8").read()


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

# ---- 4. IaC/terraform/oss の木（設計の 3）。ファイルを読むだけ（terraform validate は ops/check.sh が両方の木に打つ）
TF_ROOTS = ("base/ecr", "base/logs", "base/core", "agent", "pipeline/lab", "pipeline/stream", "pipeline/analytics", "pipeline/graph", "pipeline/nautobot", "workflow")
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
    """IaC/terraform/oss/<root>/<name> が相対のシンボリックリンクで、IaC/terraform/aws-managed/<root>/<name> と同じファイルを指す"""
    bad = []
    for n in names:
        link = os.path.join(ROOT, "IaC", "terraform", "oss", *root.split("/"), n)
        if not (os.path.islink(link) and not os.path.isabs(os.readlink(link))
                and os.path.realpath(link) == os.path.realpath(os.path.join(ROOT, "IaC", "terraform", "aws-managed", *root.split("/"), n))):
            bad.append(f"{root}/{n}")
    return bad


_bad = [b for r in UNCHANGED for b in links_to_managed(r, git_files(f"IaC/terraform/aws-managed/{r}"))]
_extra = {r: sorted(set(git_files(f"IaC/terraform/oss/{r}")) - set(git_files(f"IaC/terraform/aws-managed/{r}"))) for r in UNCHANGED}
check(f"IaC/terraform/oss の変えない 7 ルートは IaC/terraform/aws-managed/ の同じルートのファイル全部への相対リンクで、実ファイルは oss.auto.tfvars だけ（違う: {_bad} {_extra}）",
      not _bad and all(e == ["oss.auto.tfvars"] for e in _extra.values()))
_shared = ("versions.tf", "providers.tf", "variables.tf", ".terraform.lock.hcl", "terraform.tfvars.example")
_cross = [f"{r}/{n}" for r in OSS_REAL for n in git_files(f"IaC/terraform/oss/{r}")
          if os.path.islink(os.path.join(ROOT, "IaC", "terraform", "oss", *r.split("/"), n)) and links_to_managed(r, [n])]
check(f"変える 3 ルート（{', '.join(OSS_REAL)}）も共通のファイル（{', '.join(_shared)}）はリンクで、リンクは同じルートの同じ名前だけを指す（違う: {_cross}）",
      not [b for r in OSS_REAL for b in links_to_managed(r, _shared)] and not _cross)
_tfstate = {}
for r in TF_ROOTS:
    for n, s in tf_text("IaC/terraform/oss", r).items():
        for m in re.finditer(r'"\$\{path\.module\}/([^"]*terraform\.tfstate)"', s):
            _tfstate.setdefault(r, set()).add(os.path.normpath(os.path.join("IaC/terraform/oss", r, m.group(1))))
_outside = {r: sorted(p for p in v if not any(p == f"IaC/terraform/oss/{t}/terraform.tfstate" for t in TF_ROOTS)) for r, v in _tfstate.items()}
check(f"remote_state が読む state は IaC/terraform/oss の中のルートのもの（IaC/terraform/aws-managed/ と IaC/terraform/oss/ の state は混ざらない。外を指すもの: { {k: v for k, v in _outside.items() if v} }）",
      _tfstate and not any(_outside.values()))

_forbidden = re.compile(r"\baws_(msk|emrserverless|prometheus|neptunegraph|opensearchserverless)_\w+")
_hits = {f"{r}/{n}": sorted({m.group(0) for m in _forbidden.finditer(re.sub(r"(?m)^\s*#.*$", "", s))})
         for r in OSS_REAL for n, s in tf_text("IaC/terraform/oss", r).items()}
check(f"OSS 版の stream / analytics / graph に MSK / EMR Serverless / AMP / Neptune Analytics / OpenSearch Serverless のリソースも参照も無い（{ {k: v for k, v in _hits.items() if v} }）",
      _hits and not any(_hits.values()))

_auto = {r: os.path.join(ROOT, "IaC", "terraform", "oss", *r.split("/"), "oss.auto.tfvars") for r in TF_ROOTS}
check("IaC/terraform/oss の 10 ルートに oss.auto.tfvars（project = nwc-oss）があり、実ファイルで git が無視しない（.gitignore の *.tfvars の例外）",
      all(os.path.isfile(p) and not os.path.islink(p) and re.search(r'^project = "nwc-oss"$', open(p, encoding="utf-8").read(), re.M)
          and subprocess.run(["git", "check-ignore", "-q", os.path.relpath(p, ROOT)], cwd=ROOT).returncode == 1 for p in _auto.values())
      and subprocess.run(["git", "check-ignore", "-q", "IaC/terraform/oss/workflow/.build/tools.zip"], cwd=ROOT).returncode == 0)

_vars = {r: open(os.path.join(ROOT, "IaC", "terraform", "aws-managed", *r.split("/"), "variables.tf"), encoding="utf-8").read() for r in TF_ROOTS}
_prefix = {r: "\n".join(tf_text("IaC/terraform/aws-managed", r).values()) for r in TF_ROOTS}
_prefix.update({f"oss:{r}": "\n".join(tf_text("IaC/terraform/oss", r).values()) for r in OSS_REAL})
check("10 ルートに var.project（既定 nwc-poc、nwc-poc と nwc-oss だけ受ける）があり、接頭辞はどれも <owner>-<project>（-nwc-poc の書き込みは無い）",
      all(re.search(r'variable "project" \{[^}]*?default\s*=\s*"nwc-poc"[\s\S]*?condition\s*=\s*contains\(\["nwc-poc", "nwc-oss"\], var\.project\)', v) for v in _vars.values())
      and all([v for v in re.findall(r"^\s*name_prefix\s*=\s*(.+)$", s, re.M) if v != "local.name_prefix"] == ['"${var.owner}-${var.project}"'] for s in _prefix.values()))

_bad_reads = {}
for r in UNCHANGED:
    for n, s in tf_text("IaC/terraform/aws-managed", r).items():
        for line in s.splitlines():
            if re.search(r"\$\{path\.module\}/\.\.", line) and not line.strip().startswith("#") \
                    and not re.search(r'terraform\.tfstate"', line) and not line.strip().startswith("repo_root ="):
                _bad_reads.setdefault(f"{r}/{n}", []).append(line.strip())
_repo_root = 'repo_root = "${path.module}/../../../.."'
_up_to_root = lambda base, r, n: os.path.realpath(os.path.join(ROOT, base, *r.split("/"), *[".."] * n)) == os.path.realpath(ROOT)
check(f"リンクで使うルートはリポジトリのファイルを local.repo_root から読む（path.module から上るのは state と repo_root の定義だけ: {_bad_reads}）。"
      "repo_root は固定の式で、マネージド版と OSS 版のどちらのルートからでも根を指す（cycle 007 で深さがそろった）",
      not _bad_reads and all(_repo_root in tf_text(b, r)["locals.tf"] and _up_to_root(b, r, 4)
                             for b in ("IaC/terraform/aws-managed", "IaC/terraform/oss") for r in ("agent", "workflow"))
      and 'repo_root    = "${path.module}/../../../../.."' in tf_text("IaC/terraform/oss", "pipeline/graph")["sync.tf"]
      and _up_to_root("IaC/terraform/oss", "pipeline/graph", 5)
      and 'file("${local.repo_root}/app/agentcore/kb_index.py")' in tf_text("IaC/terraform/aws-managed", "agent")["kb.tf"])

_oss_tf = tf_text("IaC/terraform/aws-managed", "base/core")["oss.tf"]
_oss_sg = re.search(r"oss_security_groups = \{(.*?)\n  \}", _oss_tf, re.S)
_oss_sg = set(re.findall(r"^\s+(\w+)\s+=\s+\"", _oss_sg.group(1), re.M)) if _oss_sg else set()
_managed_sg = re.search(r"security_groups = \{(.*?)\n  \}", tf_text("IaC/terraform/aws-managed", "base/core")["security_groups.tf"], re.S)
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
      and "for_each = local.workload_security_groups" in tf_text("IaC/terraform/aws-managed", "base/core")["security_groups.tf"]
      and "for f in local.active_sg_flows :" in tf_text("IaC/terraform/aws-managed", "base/core")["security_groups.tf"])
check("EFS は暗号化し、TLS でない接続を拒み、このアカウントの IAM がマウントターゲット経由で読み書きするのだけを許す",
      "encrypted        = true" in _oss_tf and '"aws:SecureTransport" = "false"' in _oss_tf
      and '"elasticfilesystem:AccessedViaMountTarget" = "true"' in _oss_tf and 'security_groups = [local.sg_ids["efs"]]' in _oss_tf)
_ecr = tf_text("IaC/terraform/aws-managed", "base/ecr")["main.tf"]
check("OSS 版のイメージのリポジトリは project = nwc-oss のときだけ（kafka / opensearch / vminsert / vmselect / vmstorage / spark / neo4j）",
      'oss_repositories = var.project == "nwc-oss" ? toset(["kafka", "opensearch", "vminsert", "vmselect", "vmstorage", "spark", "neo4j"]) : toset([])' in _ecr
      and re.search(r'resource "aws_ecr_repository" "oss" \{\n  for_each = local\.oss_repositories\n', _ecr) is not None)
# state を失って残った ECR を 1 本ずつ import すると、まだ state に無いキーを引いた output が Invalid index で落ちる（2026-10-08 の OSS 版の検証の「不具合」3）
_ecr_each = set(re.findall(r'resource "aws_ecr_repository" "(\w+)" \{\n  for_each = ', _ecr))
_ecr_out = {m.group(1): m.group(2).strip() for m in re.finditer(r'output "(\w+)" \{[^}]*?\n  value\s+= (.+)\n', tf_text("IaC/terraform/oss", "base/ecr")["outputs.tf"])}
_ecr_bare = sorted(n for n, v in _ecr_out.items() if re.search(r"aws_ecr_repository\.(\w+)\[", v)
                   and not re.fullmatch(r'try\(aws_ecr_repository\.\w+\["[\w-]+"\]\.repository_url, ""\)', v))
check(f"ECR の output は for_each のリポジトリ（{sorted(_ecr_each)}）をキーで引くとき try(…, \"\") で包む（import の途中でも評価できる。包んでいない: {_ecr_bare}）",
      _ecr_each == {"lab", "workflow", "pipeline", "oss"} and not _ecr_bare
      and sum(f'try(aws_ecr_repository.pipeline["{k}"].repository_url, "")' in v for v in _ecr_out.values()
              for k in ("telegraf", "kafka-ui", "grafana", "splunk", "nautobot", "redis")) == 6)
_chk =open(os.path.join(ROOT, "ops", "check.sh"), encoding="utf-8").read()
check("ops/check.sh は IaC/terraform/aws-managed/ と IaC/terraform/oss/ の両方に fmt と validate を打つ",
      "TF_BASES=(IaC/terraform/aws-managed IaC/terraform/oss)" in _chk and 'terraform fmt -check -recursive "$base"' in _chk and 'terraform -chdir="$base/$r" validate' in _chk)
check("ops/check.sh は IaC/terraform/oss のルートを -lockfile=readonly で init する（lock はマネージド版へのシンボリックリンク。書くと実ファイルになる。005 のレビュー Nit 6）",
      'LOCK=""; if [ "$base" = IaC/terraform/oss ]; then LOCK=-lockfile=readonly; fi' in _chk
      and 'terraform -chdir="$base/$r" init -backend=false -input=false ${LOCK:+"$LOCK"} >/dev/null' in _chk)

# ---- 5. OSS 版の Kafka と Telegraf（設計の 4）。stream は MSK の msk.tf だけを kafka.tf に替え、ほかはマネージド版のファイルへのリンク
_STREAM = "pipeline/stream"
_stream_files = git_files(f"IaC/terraform/oss/{_STREAM}")
_stream_links = sorted(n for n in _stream_files if os.path.islink(os.path.join(ROOT, "IaC", "terraform", "oss", *_STREAM.split("/"), n)))
_stream_shared = ("locals.tf", "telegraf.tf", "collectors.tf", "gnmic.tf", "kafka_ui.tf", "access.tf", "outputs.tf")
check("OSS 版の stream の実ファイルは kafka.tf と oss.auto.tfvars だけで、マネージド版のファイルのうち msk.tf 以外は全部リンク（collectors.tf は cycle 012、gnmic.tf は cycle 013 で足した）",
      sorted(set(_stream_files) - set(_stream_links)) == ["kafka.tf", "oss.auto.tfvars"]
      and _stream_links == sorted(_shared + _stream_shared) and not links_to_managed(_STREAM, _stream_links)
      and sorted(set(git_files(f"IaC/terraform/aws-managed/{_STREAM}")) - set(_stream_links)) == ["msk.tf"])


def _locals_keys(s):
    """locals { ... } の直下（2 字下げ）で定義する名前"""
    return {k for blk in re.findall(r"^locals \{\n(.*?)^\}\n", s, re.M | re.S) for k in re.findall(r"^  (\w+)\s*=", blk, re.M)}


def _code(s):
    return re.sub(r"(?m)^\s*#.*$", "", s)


_m_stream, _o_stream = tf_text("IaC/terraform/aws-managed", _STREAM), tf_text("IaC/terraform/oss", _STREAM)
_msk_tf, _kafka_tf = _m_stream["msk.tf"], _o_stream["kafka.tf"]
_IFACE = {"kafka_bootstrap_brokers", "kafka_bootstrap_by_protocol", "kafka_client_environment",
          "telegraf_kafka_statements", "kafka_ui_kafka_statements", "kafka_descriptions",
          # syslog-ng と GoFlow2（collectors.tf）の口（cycle 012）
          "kafka_collector_brokers", "kafka_collector_auth", "kafka_collector_secrets", "kafka_collector_execution_statements"}
_shared_code = "\n".join(_code(_m_stream[n]) for n in _stream_shared)
_shared_defs = set().union(*(_locals_keys(_m_stream[n]) for n in _stream_shared))
_shared_refs = set(re.findall(r"\blocal\.(\w+)", _shared_code))
check(f"Kafka の差し替え口（{', '.join(sorted(_IFACE))}）は msk.tf と kafka.tf の両方が定義し、共有のファイルは MSK のリソースを直接読まない"
      f"（共有のファイルが読む local のうち、共有のファイルにも差し替え口にも無いもの: {sorted(_shared_refs - _shared_defs - _IFACE)}）",
      _IFACE <= _locals_keys(_msk_tf) and _IFACE <= _locals_keys(_kafka_tf) and not (_IFACE & _shared_defs)
      and _IFACE <= _shared_refs and not (_shared_refs - _shared_defs - _IFACE)
      and not re.search(r"\baws_msk_\w+", _shared_code))
check("マネージド版の msk.tf の差し替え口は今と同じ値（Telegraf の環境変数は足さない、ブートストラップは IAM の SASL_SSL）",
      "kafka_client_environment = []" in _msk_tf and "kafka_bootstrap_brokers = aws_msk_cluster.stream.bootstrap_brokers_sasl_iam" in _msk_tf
      and 'output "msk_cluster_arn"' in _msk_tf and 'output "msk_cluster_arn"' not in _m_stream["outputs.tf"])
check("syslog-ng と GoFlow2 と gnmic の口: マネージド版は MSK の SCRAM（9096 のブートストラップ、secret はコレクターごとの AmazonMSK_<接頭辞>-<コレクター>（cycle 031）を "
      "data source で引いて ECS の secrets でユーザー名とパスワードを入れ、実行ロールに secret と KMS の復号）。OSS 版は認証なしの 9092 で secret も権限も無い（形はマネージド版と同じ表）",
      "kafka_collector_brokers = aws_msk_cluster.stream.bootstrap_brokers_sasl_scram" in _msk_tf and 'kafka_collector_auth    = "scram"' in _msk_tf
      and re.search(r'data "aws_secretsmanager_secret" "msk_scram" \{\n  for_each = toset\(local\.scram_collectors\)\n  name     = "AmazonMSK_\$\{local\.name_prefix\}-\$\{each\.key\}"\n\}', _msk_tf) is not None
      and re.search(r'data "aws_kms_alias" "msk_scram" \{\n  name = "alias/\$\{local\.name_prefix\}-msk-scram"\n\}', _msk_tf) is not None
      and "  kafka_collector_secrets = { for c in local.scram_collectors : c => [\n" in _msk_tf
      and '{ name = "KAFKA_SASL_USER", valueFrom = "${data.aws_secretsmanager_secret.msk_scram[c].arn}:username::" }' in _msk_tf
      and '{ name = "KAFKA_SASL_PASS", valueFrom = "${data.aws_secretsmanager_secret.msk_scram[c].arn}:password::" }' in _msk_tf
      and re.search(r'Sid      = "ScramSecret"\n\s*Effect   = "Allow"\n\s*Action   = \["secretsmanager:GetSecretValue"\]\n\s*Resource = \[for c in local\.scram_collectors : data\.aws_secretsmanager_secret\.msk_scram\[c\]\.arn\]\n', _msk_tf) is not None
      and re.search(r'Sid      = "ScramSecretKey"\n\s*Effect   = "Allow"\n\s*Action   = \["kms:Decrypt"\]\n\s*Resource = data\.aws_kms_alias\.msk_scram\.target_key_arn\n', _msk_tf) is not None
      and re.search(r"sasl \{\n\s*iam\s*= true\n\s*scram = true\n\s*\}", _msk_tf) is not None
      and re.search(r'resource "aws_msk_scram_secret_association" "collectors" \{\n  cluster_arn     = aws_msk_cluster\.stream\.arn\n  secret_arn_list = \[for c in local\.scram_collectors : data\.aws_secretsmanager_secret\.msk_scram\[c\]\.arn\]\n\}', _msk_tf) is not None
      and 'resource "aws_secretsmanager_secret"' not in _msk_tf and "aws_secretsmanager_secret_version" not in _msk_tf   # 値は Terraform の state に入れない（ops/up.sh が作る）
      and "kafka_collector_brokers              = local.kafka_bootstrap_brokers" in _kafka_tf and 'kafka_collector_auth                 = "none"' in _kafka_tf
      and 'kafka_collector_secrets              = { "syslog-ng" = [], "goflow2" = [], "gnmic" = [] }' in _kafka_tf and "kafka_collector_execution_statements = []" in _kafka_tf
      and "secretsmanager" not in _code(_kafka_tf))
_tg_envs = re.findall(r"^      environment = concat\(\n        \[\n[\s\S]*?^        \],\n        local\.kafka_client_environment,\n      \)\n",
                      _m_stream["telegraf.tf"], re.M)
check("Telegraf のタスク（dialout の 1 つ。dialin は cycle 013 で gnmic に替えた）の環境変数は local.kafka_client_environment を足し、gnmic は collectors.tf と同じ kafka_collector_* を使う",
      len(_tg_envs) == 1 == _m_stream["telegraf.tf"].count("local.kafka_client_environment")
      and "kafka_client_environment" not in _m_stream["gnmic.tf"]
      and '{ name = "KAFKA_BROKERS", value = local.kafka_collector_brokers }' in _m_stream["gnmic.tf"]
      and '{ name = "KAFKA_AUTH", value = local.kafka_collector_auth }' in _m_stream["gnmic.tf"])

_k = _code(_kafka_tf)
check("OSS 版の Kafka は 3 台（kafka_nodes の 1〜3）で、台ごとに ECS のサービス・タスク定義・Cloud Map の名前・EFS のアクセスポイントを持つ",
      "kafka_nodes = { for i in range(3) : tostring(i + 1) => try(local.subnet_ids[i], \"\") }" in _k
      and all(re.search(r'resource "' + t + r'" "kafka" \{\n  for_each = local\.kafka_nodes\n', _k)
              for t in ("aws_ecs_service", "aws_ecs_task_definition", "aws_service_discovery_service", "aws_efs_access_point"))
      and 'name = "kafka-${each.key}"' in _k and 'path = "/kafka-${each.key}"' in _k
      and "access_point_id = aws_efs_access_point.kafka[each.key].id" in _k and "subnets          = [each.value]" in _k
      and "registry_arn = aws_service_discovery_service.kafka[each.key].arn" in _k)
check("KRaft の固定の voter は Cloud Map の名前の 9093、広告するのは自分の名前の 9092、番号は台の番号",
      '{ name = "KAFKA_CONTROLLER_QUORUM_VOTERS", value = join(",", [for n, h in local.kafka_hosts : "${n}@${h}:9093"]) }' in _k
      and 'kafka_hosts = { for n, _ in local.kafka_nodes : n => "kafka-${n}.${local.stream_service_namespace}" }' in _k
      and '{ name = "KAFKA_ADVERTISED_LISTENERS", value = "PLAINTEXT://${local.kafka_hosts[each.key]}:9092" }' in _k
      and '{ name = "KAFKA_NODE_ID", value = each.key }' in _k and '"broker,controller"' in _k
      and "controller.quorum.bootstrap.servers" not in _k.lower().replace("_", "."))
check("データは EFS（TLS と IAM の認可）、CLUSTER_ID は SSM から ECS の secrets で渡す（state にも環境変数にも値を書かない）",
      'transit_encryption = "ENABLED"' in _k and 'iam             = "ENABLED"' in _k
      and 'containerPath = "/var/lib/kafka/data"' in _k and '{ name = "KAFKA_LOG_DIRS", value = "/var/lib/kafka/data" }' in _k
      and 'secrets     = [{ name = "CLUSTER_ID", valueFrom = local.kafka_cluster_id_arn }]' in _k
      and 'kafka_cluster_id_parameter = "/${local.name_prefix}/kafka/cluster-id"' in _k
      and '"elasticfilesystem:AccessPointArn" = [for ap in aws_efs_access_point.kafka : ap.arn]' in _k
      and not re.search(r'name = "CLUSTER_ID", value =', _k))
check("同じ番号の台を 2 つ同時に立てない（入れ替えは止めてから起こす）。SG は土台の kafka",
      "deployment_minimum_healthy_percent = 0" in _k and "deployment_maximum_percent         = 100" in _k
      and 'kafka_sg_id         = try(data.terraform_remote_state.main.outputs.security_group_ids["kafka"], "")' in _k
      and "security_groups  = [local.kafka_sg_id]" in _k)
check("OSS 版の差し替え口: 認証なし（Telegraf に KAFKA_AUTH=none、Kafbat UI は PLAINTEXT だけ、Kafka の IAM の権限は無く MSK を拒む）",
      '[{ name = "KAFKA_AUTH", value = "none" }]' in _k and "kafka_bootstrap_by_protocol = { PLAINTEXT = local.kafka_bootstrap_brokers }" in _k
      and 'kafka_bootstrap_brokers = join(",", [for n, h in local.kafka_hosts : "${h}:9092"])' in _k
      and "telegraf_kafka_statements = []" in _k and re.search(r'Sid\s*=\s*"NoMsk"\s*Effect\s*=\s*"Deny"\s*Action\s*=\s*\["kafka-cluster:\*"\]', _k)
      and re.search(r'^kafka_ui_security_protocol = "PLAINTEXT"$', open(_auto[_STREAM], encoding="utf-8").read(), re.M))
check("kafka.tf は接頭辞を作らず（locals.tf の local.name_prefix を使う）、MSK の変数（kafka_version など）も読まない",
      not re.search(r"^\s*name_prefix\s*=", _k, re.M) and "var.kafka_version" not in _k and "var.msk_" not in _k)

# ---- 6. OSS 版の OpenSearch と VictoriaMetrics（設計の 5）。analytics の opensearch.tf・victoriametrics.tf と、Spark・Grafana・evidence の接続先
_AN = "pipeline/analytics"
_an_files = git_files(f"IaC/terraform/oss/{_AN}")
_an_real = {n for n in _an_files if not os.path.islink(os.path.join(ROOT, "IaC", "terraform", "oss", *_AN.split("/"), n))}
_m_an, _o_an = tf_text("IaC/terraform/aws-managed", _AN), tf_text("IaC/terraform/oss", _AN)
_os_tf, _vm_tf = _code(_o_an["opensearch.tf"]), _code(_o_an["victoriametrics.tf"])
_an_code = "\n".join(_code(s) for s in _o_an.values())
check("OSS 版の analytics は opensearch.tf・victoriametrics.tf・network.tf が実ファイル（マネージド版の network.tf はコメントだけ）で、"
      "ecs.tf（ECS のクラスタと Cloud Map の名前空間）はマネージド版へのリンク",
      {"opensearch.tf", "victoriametrics.tf", "network.tf"} <= _an_real and "ecs.tf" in _an_files and not links_to_managed(_AN, ["ecs.tf"])
      and not {"opensearch.tf", "victoriametrics.tf"} & set(_m_an) and not _code(_m_an["network.tf"]).strip()
      and re.search(r"^\s+name\s+= local\.service_namespace$", _m_an["ecs.tf"], re.M) is not None)


def _tf_locals(s):
    """locals { ... } の直下（2 字下げ）に 1 行で書いた定義 {名前: 式}（行末のコメントは外す。複数行の式は最初の行だけ）"""
    return {k: re.sub(r"\s+#.*$", "", v).strip() for blk in re.findall(r"^locals \{\n(.*?)^\}\n", s, re.M | re.S)
            for k, v in re.findall(r"^  (\w+)\s*=\s*(.+)$", blk, re.M)}


def _block(s, kind, name):
    """resource / data / output の 1 つのブロックの中身（閉じるのは行頭の }）"""
    m = re.search(rf'^{kind} "{name}" \{{\n(.*?)^\}}\n' if kind == "output" else rf'^{kind} "{name.split(".")[0]}" "{name.split(".")[1]}" \{{\n(.*?)^\}}\n',
                  s, re.M | re.S)
    return m.group(1) if m else ""


def _squeeze(s):
    """空行（コメントを外した跡を含む）を詰める"""
    return re.sub(r"\n\s*\n+", "\n", s).strip()


_net =_tf_locals(_code(_o_an["network.tf"]))
_m_loc = _tf_locals(_code(_m_an["locals.tf"]))
_net_diff = {k: (v, _m_loc[k]) for k, v in _net.items() if k in _m_loc and v != _m_loc[k] and k != "create_ecs"}
check(f"network.tf の locals はマネージド版の locals.tf と同じ名前・同じ値（違うのは create_ecs = true だけ。違う: {_net_diff}）で、"
      "接頭辞は作らない。remote_state の main / ecr もマネージド版と同じ state を読む",
      not _net_diff and _net["create_ecs"] == "true" and set(_net) - set(_m_loc) == {"ssm_parameter_arn"} and "name_prefix" not in _net
      and _net["ssm_parameter_arn"][:-1] + '${local.splunk_token_parameter}"' == _m_loc["splunk_token_parameter_arn"]
      and all(_squeeze(_block(_code(_o_an["network.tf"]), "data", f"terraform_remote_state.{d}"))
              == _squeeze(_block(_code(_m_an["locals.tf"]), "data", f"terraform_remote_state.{d}")) != "" for d in ("main", "ecr")))

# OpenSearch
_os_nodes = re.findall(r'^\s+"(\w+)"\s+= \{ subnet = try\(local\.subnet_ids\[(\d)\], ""\), data = (true|false) \}$',
                       re.search(r"opensearch_nodes = \{\n(.*?)\n  \}", _os_tf, re.S).group(1), re.M)
_os_env = dict(re.findall(r'\{ name = "([\w.]+)", value = "([^"]*)" \}', _os_tf))
_os_td, _os_svc = _block(_os_tf, "resource", "aws_ecs_task_definition.opensearch"), _block(_os_tf, "resource", "aws_ecs_service.opensearch")
check("OpenSearch は 3 台（データ 1・2 とまとめ役 cm）で台ごとに ECS のサービスとタスク定義、台 N はサブネットの N 番目。最初の投票の顔ぶれは 3 台の node.name と同じ",
      _os_nodes == [("1", "0", "true"), ("2", "1", "true"), ("cm", "2", "false")]
      and _os_td.startswith("  for_each = local.sink_opensearch ? local.opensearch_nodes : {}\n")
      and _os_svc.startswith("  for_each = local.sink_opensearch ? local.opensearch_nodes : {}\n")
      and "subnets          = [each.value.subnet]" in _os_svc and "desired_count   = 1" in _os_svc
      and '{ name = "node.name", value = "opensearch-${each.key}" }' in _os_td
      and _os_env["cluster.initial_cluster_manager_nodes"].split(",") == [f"opensearch-{n}" for n, _, _ in _os_nodes])
check("まとめ役だけ node.roles = cluster_manager。インデックスはデータの台のエフェメラルストレージで、OpenSearch は EFS を使わない",
      re.search(r'each\.value\.data \? \[\] : \[\n\s+\{ name = "node\.roles", value = "cluster_manager" \},\n\s+\]\)', _os_td) is not None
      and "for_each = each.value.data ? [var.opensearch_ephemeral_storage_gib] : []" in _os_td and "size_in_gib = ephemeral_storage.value" in _os_td
      and not re.search(r"\baws_efs_|efs_volume_configuration|elasticfilesystem|mountPoints", _os_tf))
check("mmap を使わない（node.store.allow_mmap=false）。vm.max_map_count は変えない（OSS 版の analytics に sysctl も systemControls も無い）",
      _os_env["node.store.allow_mmap"] == "false" and not re.search(r"max_map_count|sysctl|systemControls", _an_code))
check("REST（9200）は TLS なしの HTTP、台どうし（9300）の TLS とセキュリティプラグイン（Basic 認証）は切らない",
      _os_env["plugins.security.ssl.http.enabled"] == "false" and not re.search(r"plugins\.security\.disabled|ssl\.transport\.enabled", _os_tf)
      and 'portMappings = [{ containerPort = 9200, protocol = "tcp" }, { containerPort = 9300, protocol = "tcp" }]' in _os_td)
check("Cloud Map はデータ 2 台が入る opensearch と、まとめ役の opensearch-cm（ECS のサービスは Cloud Map のサービスを 1 つしか持てない）。"
      "seed_hosts はその 2 つの名前で、同じ node.name の台を 2 つ同時に立てない",
      _block(_os_tf, "resource", "aws_service_discovery_service.opensearch").startswith(
          '  for_each = local.sink_opensearch ? toset(["opensearch", "opensearch-cm"]) : toset([])\n')
      and 'registry_arn = aws_service_discovery_service.opensearch[each.value.data ? "opensearch" : "opensearch-cm"].arn' in _os_svc
      and _os_env["discovery.seed_hosts"] == "${local.opensearch_host},${local.opensearch_cm_host}"
      and "deployment_minimum_healthy_percent = 0" in _os_svc and "deployment_maximum_percent         = 100" in _os_svc)
check("admin のパスワードは SSM の SecureString を secrets で受け、実行ロールはその 1 つだけ読める（環境変数に値を書かない）",
      'secrets = [{ name = "OPENSEARCH_INITIAL_ADMIN_PASSWORD", valueFrom = local.opensearch_password_arn }]' in _os_td
      and re.search(r'Action\s+= \["ssm:GetParameters"\]\n\s+Resource = local\.opensearch_password_arn\n', _os_tf) is not None
      and "OPENSEARCH_INITIAL_ADMIN_PASSWORD" not in _os_env)

# VictoriaMetrics
_vs_td, _vs_svc = _block(_vm_tf, "resource", "aws_ecs_task_definition.vmstorage"), _block(_vm_tf, "resource", "aws_ecs_service.vmstorage")
_vi_td, _vi_svc = _block(_vm_tf, "resource", "aws_ecs_task_definition.vminsert"), _block(_vm_tf, "resource", "aws_ecs_service.vminsert")
_vq_td, _vq_svc = _block(_vm_tf, "resource", "aws_ecs_task_definition.vmselect"), _block(_vm_tf, "resource", "aws_ecs_service.vmselect")
check("VictoriaMetrics は vmstorage 3 台（台ごとに ECS のサービス・タスク定義・EFS のアクセスポイント /vmstorage-N・Cloud Map の vmstorage-N）と vminsert・vmselect 1 台ずつ",
      'vmstorage_nodes = local.sink_prometheus ? { for i in range(3) : tostring(i + 1) => try(local.subnet_ids[i], "") } : {}' in _vm_tf
      and all(b.startswith("  for_each = local.vmstorage_nodes\n") for b in (_vs_td, _vs_svc, _block(_vm_tf, "resource", "aws_efs_access_point.vmstorage")))
      and 'path = "/vmstorage-${each.key}"' in _vm_tf and "access_point_id = aws_efs_access_point.vmstorage[each.key].id" in _vs_td
      and 'registry_arn = aws_service_discovery_service.victoriametrics["vmstorage-${each.key}"].arn' in _vs_svc
      and 'vmstorage_hosts = [for i in range(3) : "vmstorage-${i + 1}.${local.service_namespace}"]' in _vm_tf
      and 'vm_service_names = local.sink_prometheus ? toset(concat(["vminsert", "vmselect"], [for n in range(3) : "vmstorage-${n + 1}"])) : toset([])' in _vm_tf
      and all(b.startswith("  count = local.sink_prometheus ? 1 : 0\n") and "desired_count   = 1" in b for b in (_vi_svc, _vq_svc))
      and 'registry_arn = aws_service_discovery_service.victoriametrics["vminsert"].arn' in _vi_svc
      and 'registry_arn = aws_service_discovery_service.victoriametrics["vmselect"].arn' in _vq_svc)
check("複製数 2: vminsert は vmstorage 3 台の 8400、vmselect は 8401 につなぎ、どちらも -replicationFactor=2。vmselect は複製の重複を落とす",
      "vm_replication_factor = 2" in _vm_tf
      and '"-storageNode=${join(",", [for h in local.vmstorage_hosts : "${h}:8400"])}"' in _vi_td
      and '"-storageNode=${join(",", [for h in local.vmstorage_hosts : "${h}:8401"])}"' in _vq_td
      and all('"-replicationFactor=${local.vm_replication_factor}"' in b for b in (_vi_td, _vq_td))
      and '"-dedup.minScrapeInterval=1ms"' in _vq_td
      and re.findall(r"containerPort = (\d+)", _vs_td) == ["8400", "8401", "8482"])
check("vmstorage の置き場は EFS（TLS と IAM の認可）で、タスクロールは vmstorage の 3 つのアクセスポイントからだけマウントできる。同じ台を 2 つ同時に立てない",
      'transit_encryption = "ENABLED"' in _vs_td and 'iam             = "ENABLED"' in _vs_td
      and 'mountPoints = [{ sourceVolume = "storage", containerPath = "/storage", readOnly = false }]' in _vs_td
      and '"-storageDataPath=/storage"' in _vs_td
      and '"elasticfilesystem:AccessPointArn" = [for ap in aws_efs_access_point.vmstorage : ap.arn]' in _vm_tf
      and "deployment_minimum_healthy_percent = 0" in _vs_svc and "deployment_maximum_percent         = 100" in _vs_svc)
check("vminsert のタスクは待ちのコンテナ（essential でない wait-vmstorage）が 0 で終わってから vminsert を起こし（dependsOn の SUCCESS）、サービスも vmstorage の後に作る",
      re.search(r'name\s+= "wait-vmstorage"\n\s+image\s+= local\.vm_images\["vminsert"\]\n\s+essential\s+= false\n'
                r'\s+entryPoint = \["/bin/sh", "-c"\]\n\s+command\s+= \[local\.vmstorage_wait_command\]\n', _vi_td) is not None
      and 'dependsOn    = [{ containerName = "wait-vmstorage", condition = "SUCCESS" }]' in _vi_td
      and re.search(r"depends_on = \[\n\s+aws_ecs_service\.vmstorage,\n", _vi_svc) is not None)


def _wait_command(secs, hosts):
    """victoriametrics.tf の vmstorage_wait_command を Terraform と同じように 1 行にする（期限の秒と vmstorage の名前を入れる）"""
    lines = re.search(r'vmstorage_wait_command = join\(" ", \[\n(.*?)\n  \]\)', _vm_tf, re.S).group(1).splitlines()
    cmd = " ".join(re.fullmatch(r'\s*"(.*)",?', ln).group(1) for ln in lines).replace('\\"', '"')
    cmd = cmd.replace("${var.vmstorage_wait_seconds}", str(secs)).replace('${join(" ", local.vmstorage_hosts)}', " ".join(hosts))
    assert "${" not in cmd and "%{" not in cmd, cmd
    return cmd


_NC = """#!/bin/sh
# 偽物の nc。引数を覚え、NC_MODE で答える（ok: いつも受ける、fail: いつも受けない、flaky: vmstorage-2 だけ 2 回受けない）
echo "$*" >> "$NC_LOG"
case "$NC_MODE" in
  ok) exit 0 ;;
  fail) exit 1 ;;
  flaky) case "$4" in vmstorage-2.*) [ "$(grep -c -- "$4" "$NC_LOG")" -gt 2 ]; exit $? ;; esac; exit 0 ;;
esac
exit 2
"""


def run_wait(secs, mode, hosts):
    """待ちのコマンドを sh で走らせる（nc と sleep は偽物）。(終了コード, 出た行, nc に渡した引数の行)"""
    with tempfile.TemporaryDirectory() as tmp:
        for name, body in (("nc", _NC), ("sleep", "#!/bin/sh\nexit 0\n")):
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                f.write(body)
            os.chmod(os.path.join(tmp, name), 0o755)
        log = os.path.join(tmp, "nc.log")
        r = subprocess.run(["sh", "-c", _wait_command(secs, hosts)], capture_output=True, text=True, timeout=60,
                           env={"PATH": f"{tmp}:{os.environ['PATH']}", "NC_LOG": log, "NC_MODE": mode})
        return r.returncode, r.stdout.splitlines(), (open(log, encoding="utf-8").read().splitlines() if os.path.exists(log) else [])


_PREFIX = "o-nwc-oss"   # var.owner = o、var.project = nwc-oss のときの接頭辞
_NS = f"{_PREFIX}.internal"
_VS = [f"vmstorage-{i}.{_NS}" for i in (1, 2, 3)]
_rc, _out, _nc = run_wait(300, "ok", _VS)
check("待ちのコマンド: 3 台が受けていれば vmstorage-1〜3 の 8400 を 1 回ずつ確かめて 0 で終わる",
      _rc == 0 and _out == ["vmstorage wait done"] and _nc == [f"-z -w 2 {h} 8400" for h in _VS])
_rc, _out, _nc = run_wait(300, "flaky", _VS)
check("待ちのコマンド: まだ受けない台は受けるまで待ち（2 秒おき）、3 台そろってから 0 で終わる",
      _rc == 0 and _out == [f"waiting for {_VS[1]}:8400"] * 2 + ["vmstorage wait done"]
      and _nc == [f"-z -w 2 {h} 8400" for h in (_VS[0], _VS[1], _VS[1], _VS[1], _VS[2])])
_rc, _out, _nc = run_wait(0, "fail", _VS)
check("待ちのコマンド: 期限（vmstorage_wait_seconds）を過ぎたらあきらめて、それでも 0 で終わる（dependsOn の SUCCESS で vminsert を起こす）",
      _rc == 0 and _out == [f"gave up waiting for {h}:8400" for h in _VS] + ["vmstorage wait done"] and len(_nc) == 3
      and re.search(r'variable "vmstorage_wait_seconds" \{[^}]*default\s+= 300\n', _vm_tf) is not None)

# 土台（IaC/terraform/aws-managed/base/core の oss.tf）の SG の行とコンテナのポート
_sg_rows = [(f, t, int(p), int(q or p)) for f, t, p, q in
            re.findall(r'\{ from = "(\w+)", to = "(\w+)", protocol = "tcp", port = (\d+)(?:, to_port = (\d+))?, why', _oss_tf)]
_cports = {"opensearch": {int(p) for p in re.findall(r"containerPort = (\d+)", _os_tf)},
           "victoriametrics": {int(p) for p in re.findall(r"containerPort = (\d+)", _vm_tf)}}
_opened = {t: sorted((f, p, q) for f, tt, p, q in _sg_rows if tt == t) for t in _cports}


def _from(to, port):
    return {f for f, t, p, q in _sg_rows if t == to and p <= port <= q}


check(f"土台の SG の行が OpenSearch / VictoriaMetrics に開けるポートは、どれもコンテナのポート（{_opened}）。"
      "OpenSearch から EFS への行は無く、EFS の SG の説明にも OpenSearch は無い",
      all(_opened[t] and all(set(range(p, q + 1)) <= _cports[t] for _, p, q in _opened[t]) for t in _cports)
      and not _from("efs", 2049) - {"kafka", "victoriametrics"} and "victoriametrics" in _from("efs", 2049)
      and "OpenSearch" not in re.search(r'^\s+efs\s+= "([^"]*)"', _oss_tf, re.M).group(1)
      and _from("opensearch", 9300) == {"opensearch"} and _from("victoriametrics", 8400) == _from("victoriametrics", 8401) == {"victoriametrics"})


def _render(name, defs):
    """locals の 1 行の式を、sinks が全部あるとして文字列にする（${local.X} は defs から引く。接頭辞は _PREFIX）"""
    v = re.sub(r'^local\.sink_\w+ \? (.*) : ""$', r"\1", defs[name])
    if re.fullmatch(r"local\.\w+", v):
        return _render(v[len("local."):], defs)
    assert v.startswith('"') and v.endswith('"'), (name, v)
    s = re.sub(r"\$\{local\.(\w+)\}", lambda m: _render(m.group(1), defs), v[1:-1])
    s = s.replace("${var.owner}-${var.project}", _PREFIX)
    assert "${" not in s, (name, s)
    return s


_L = {k: v for s in _o_an.values() for k, v in _tf_locals(_code(s)).items()}
OS_URL, WRITE_URL, SELECT_URL, QUERY_URL = (_render(n, _L) for n in ("opensearch_endpoint", "prometheus_remote_write_url", "prometheus_select_url", "prometheus_query_url"))
_host = lambda u: urllib.parse.urlsplit(u).hostname
_port = lambda u: urllib.parse.urlsplit(u).port
check(f"接続先は Cloud Map の名前（<名前>.<接頭辞>.internal）とコンテナのポート: OpenSearch {OS_URL}、vminsert {WRITE_URL}、vmselect {SELECT_URL}",
      OS_URL == f"http://opensearch.{_NS}:9200" and WRITE_URL == f"http://vminsert.{_NS}:8480/insert/0/prometheus/api/v1/write"
      and SELECT_URL == f"http://vmselect.{_NS}:8481/select/0/prometheus" and QUERY_URL == SELECT_URL + "/api/v1/query"
      and _render("service_namespace", _L) == _NS and _render("opensearch_cm_host", _L) == f"opensearch-cm.{_NS}"
      and _port(OS_URL) in {int(p) for p in re.findall(r"containerPort = (\d+)", _os_td)}
      and _port(WRITE_URL) in {int(p) for p in re.findall(r"containerPort = (\d+)", _vi_td)}
      and _port(SELECT_URL) in {int(p) for p in re.findall(r"containerPort = (\d+)", _vq_td)})
check("接続先のポートは使う相手から土台の SG で開いている（9200 は Spark・Grafana・AgentCore・Lambda、vminsert の 8480 は Spark だけ、vmselect の 8481 は Grafana・AgentCore・Lambda）",
      _from("opensearch", _port(OS_URL)) >= {"spark", "grafana", "runtime", "lambda"} and _from("victoriametrics", _port(WRITE_URL)) == {"spark"}
      and _from("victoriametrics", _port(SELECT_URL)) >= {"grafana", "runtime", "lambda"})
_https = re.findall(r"https://(?:\$\{local\.(?:opensearch_host|opensearch_cm_host|service_namespace)\}|(?:opensearch|vminsert|vmselect|vmstorage)[\w-]*\.)", _an_code)
check(f"OSS 版の analytics から OpenSearch・VictoriaMetrics へは https で行かない（REST は HTTP。https で書いたところ: {_https}）", not _https)
# 道は 005 の手元の compose（check_vm.py。2026-10-08 に compose ごと消した）で、vminsert に書いて vmselect で読めたもの
check("vminsert の書き込みと vmselect の読み出しの道は、005 の手元の compose で確かめたもの（/insert/0/prometheus/api/v1/write・/select/0/prometheus/api/v1/）と同じ",
      urllib.parse.urlsplit(WRITE_URL).path == "/insert/0/prometheus/api/v1/write" and urllib.parse.urlsplit(SELECT_URL).path + "/api/v1/" == "/select/0/prometheus/api/v1/")

# Spark・Grafana・evidence を OSS 版の接続先に向ける
_index = _render("opensearch_index", _L)
posts.clear(); sigs.clear()
with_env({"OPENSEARCH_AUTH": "basic", "OPENSEARCH_PASSWORD": "pw-os", "PROMETHEUS_AUTH": "none"},
         lambda: (sinks.make_opensearch_sender(OS_URL, _index, "ap-northeast-1")([rec]), sinks.make_prometheus_sender(WRITE_URL, "ap-northeast-1")([rec])))
check("Spark: OPENSEARCH_AUTH=basic / PROMETHEUS_AUTH=none で、OpenSearch の <接続先>/snmp-logs/_bulk に Basic 認証、vminsert の remote write に署名なしで送る",
      sigs == [] and [u for u, _ in posts] == [f"{OS_URL}/snmp-logs/_bulk", WRITE_URL]
      and posts[0][1]["Authorization"] == "Basic " + base64.b64encode(b"admin:pw-os").decode() and posts[1][1] == PROM_HEADERS
      and _index == sinks.OPENSEARCH_INDEX == evidence.OPENSEARCH_INDEX == "snmp-logs")
_rc, _files, _last, _ = start_sh(PROMETHEUS_AUTH="none", OPENSEARCH_AUTH="basic", PROMETHEUS_URL=SELECT_URL, OPENSEARCH_URL=OS_URL)
_hint = re.search(r"PROMETHEUS_URL は (http://\S+?)（", _oss_prom).group(1)
check("Grafana: PROMETHEUS_URL に vmselect の根、OPENSEARCH_URL に OpenSearch の接続先で datasources-oss を並べる。vmselect の根はデータソースに書いた形（http://<vmselect>:8481/select/0/prometheus）",
      _rc == 0 and _files == {"opensearch.yaml": _oss_os, "prometheus.yaml": _oss_prom}
      and "url: ${PROMETHEUS_URL}" in _oss_prom and "url: ${OPENSEARCH_URL}" in _oss_os and "database: ${OPENSEARCH_INDEX}" in _oss_os
      and re.fullmatch(re.escape(_hint).replace(re.escape("<vmselect>"), r"[\w.-]+"), SELECT_URL) is not None
      and _hint.replace("<vmselect>", f"vmselect.{_NS}") == SELECT_URL)
_saved = evidence.OPENSEARCH_ENDPOINT, evidence.PROMETHEUS_QUERY_URL
evidence.OPENSEARCH_ENDPOINT, evidence.PROMETHEUS_QUERY_URL = OS_URL, QUERY_URL
evidence.OPENSEARCH_PASSWORD.cached, evidence.OPENSEARCH_PASSWORD.checked = "", 0.0
state["ssm"].clear()
sys.modules["toolkit"].PARAM_PREFIX = "/" + _PREFIX   # IaC/terraform/aws-managed/workflow・agent の param_prefix = "/${local.name_prefix}"
try:
    (_logs, _met), _s = ev(OPENSEARCH_AUTH="basic", PROMETHEUS_AUTH="none")
finally:
    sys.modules["toolkit"].PARAM_PREFIX = ""
    evidence.OPENSEARCH_ENDPOINT, evidence.PROMETHEUS_QUERY_URL = _saved
check("evidence: OpenSearch の <接続先>/snmp-logs/_search と vmselect の query_range に送り、パスワードは SSM の <param_prefix>/opensearch-password"
      "（opensearch.tf が admin に渡すのと同じ名前）",
      [x["url"].split("?")[0] for x in _s] == [f"{OS_URL}/snmp-logs/_search", f"{SELECT_URL}/api/v1/query_range"]
      and _s[0]["auth"] == "Basic " + base64.b64encode(b"admin:pw-ssm").decode() and _s[1]["auth"] is None and _logs["count"] == 1
      and state["ssm"] == [(_render("opensearch_password_parameter", _L), True)] == [(f"/{_PREFIX}/opensearch-password", True)]
      and all('param_prefix = "/${local.name_prefix}"' in tf_text("IaC/terraform/aws-managed", r)["locals.tf"] for r in ("workflow", "agent")))
_wf_reads = set(re.findall(r"data\.terraform_remote_state\.analytics\.outputs\.(\w+)", tf_text("IaC/terraform/aws-managed", "workflow")["locals.tf"]))
_an_outs = set(re.findall(r'^output "(\w+)"', _an_code, re.M))
_gw = tf_text("IaC/terraform/aws-managed", "workflow")["gateway.tf"]
check("IaC/terraform/aws-managed/workflow が読む output: 接続先（opensearch_collection_endpoint・opensearch_index・prometheus_query_url）は同じ名前で出し、"
      "OpenSearch Serverless と AMP の名前・ARN は出さない（workflow は空なら aoss / aps の IAM を作らない）。道具の Lambda にはそのまま渡る",
      {"opensearch_collection_endpoint", "opensearch_index", "prometheus_query_url"} <= _wf_reads & _an_outs
      and {"opensearch_collection_name", "opensearch_collection_arn", "prometheus_workspace_arn"} <= _wf_reads
      and not {"opensearch_collection_name", "opensearch_collection_arn", "prometheus_workspace_arn", "prometheus_workspace_id"} & _an_outs
      and "value       = local.opensearch_endpoint" in _block(_os_tf, "output", "opensearch_collection_endpoint")
      and "value       = local.opensearch_index" in _block(_os_tf, "output", "opensearch_index")
      and "value       = local.prometheus_query_url" in _block(_vm_tf, "output", "prometheus_query_url")
      and "OPENSEARCH_ENDPOINT  = local.opensearch_endpoint" in _gw and "PROMETHEUS_QUERY_URL = local.prometheus_query_url" in _gw)

# ---- 7. OSS 版の Spark と Neo4j（005 の 2）。analytics は EMR Serverless を spark.tf の ECS に、graph は Neptune Analytics を neo4j.tf の ECS に替える
_dock = {n: open(os.path.join(ROOT, *n.split("/")), encoding="utf-8").read()
         for n in ("docker/images/spark/Dockerfile", "docker/images/neo4j/Dockerfile")}
_SD, _ND = _dock["docker/images/spark/Dockerfile"], _dock["docker/images/neo4j/Dockerfile"]


def _arg(text, name):
    m = re.search(rf"^ARG {name}=(\S+)$", text, re.M)
    return m.group(1) if m else None


_versions = (_arg(_SD, "SPARK_VERSION"), _arg(_SD, "ICEBERG_VERSION"), _arg(_ND, "NEO4J_VERSION"))
_neo4j_from = re.findall(r"^FROM (\S+)", _ND, re.M)
check(f"Spark・Iceberg・Neo4j の版は Dockerfile の ARG の既定値に 1 か所で持ち（{_versions}）、Neo4j の FROM はその ARG を使い、"
      "GDS の jar はイメージに焼き込む（起動時に取りに行かない）",
      None not in _versions and _neo4j_from == ["neo4j:${NEO4J_VERSION}-community"]
      and "cp /var/lib/neo4j/products/neo4j-graph-data-science-*.jar /var/lib/neo4j/plugins/" in _ND
      and "NEO4J_PLUGINS" not in _code(_ND) and "--packages" not in _code(_SD))
_jars = re.findall(r"^ {6}(\S+?):(\S+\.jar)(?:; do)? \\$", _SD, re.M)
check("Spark の jar は sha256 を書いて取り、合わなければビルドを止める（005 のレビュー Nit 4）。005 の手元の compose で確かめた 7 本"
      "（Kafka・Iceberg・S3 Tables のカタログ。Spark と Iceberg の jar は ARG の版）に、S3A の 2 本を足した 9 本",
      len(_jars) == 9 and all(re.fullmatch(r"[0-9a-f]{64}", h) for h, _ in _jars)
      and [p.split("/")[-3] for _, p in _jars] == ["spark-sql-kafka-0-10_2.12", "spark-token-provider-kafka-0-10_2.12", "kafka-clients", "commons-pool2",
                                                  "iceberg-spark-runtime-3.5_2.12", "iceberg-aws-bundle", "s3-tables-catalog-for-iceberg-runtime",
                                                  "hadoop-aws", "aws-java-sdk-bundle"]
      and all(p.split("/")[-2] == "$SPARK_VERSION" for _, p in _jars[:2]) and all(p.split("/")[-2] == "$ICEBERG_VERSION" for _, p in _jars[4:6])
      and len({h for h, _ in _jars}) == 9
      and 'p=${e#*:}; curl -fsSLO "$MAVEN/$p" && echo "${e%%:*}  ${p##*/}" | sha256sum -c --quiet - || exit 1; \\' in _SD)
check("Iceberg 1.12 のクラスは Java 17 向けなので、Spark のイメージは Java 17 のもの（apache/spark:<版>-java17-python3）",
      re.search(r"^FROM apache/spark:\S+-java17-python3$", _SD, re.M))
_spark = _code(tf_text("IaC/terraform/oss", "pipeline/analytics")["spark.tf"])
check("Spark はマネージド版の spark_jobs と同じ分け方で格納先の組ごとに 1 タスク（iceberg / splunk / http）。checkpoint は S3A で、同じ checkpoint を 2 つのタスクが同時に使わない",
      "spark_services = { for job, sinks in local.spark_jobs : job => sinks if length(sinks) > 0 }" in _spark
      and all(re.search(r'resource "' + t + r'" "spark" \{\n  for_each = local\.spark_services\n', _spark) for t in ("aws_ecs_task_definition", "aws_ecs_service"))
      and 'spark_checkpoint_uri = "s3a://${local.bucket}/${local.checkpoint}/' in _spark
      and "deployment_minimum_healthy_percent = 0" in _spark and "deployment_maximum_percent         = 100" in _spark)


def _tf_values(text, kind, names):
    """locals の 1 行の定義（kind = local）か output の value（kind = output）を名前ごとに返す（空白をつめる）"""
    pat = {"local": r"^\s+{n}\s*=\s*(.+)$", "output": r'^output "{n}" \{{[\s\S]*?^\s+value\s*=\s*(.+)$'}[kind]
    return {n: (lambda m: m and re.sub(r"\s+", " ", m.group(1).strip()))(re.search(pat.format(n=n), text, re.M)) for n in names}


_SPLUNK_LOCALS = ("splunk_sg_id", "splunk_on_ecs", "splunk_hec_url", "splunk_skip_tls_verify", "splunk_token_parameter", "splunk_token_parameter_arn",
                  "splunk_password_parameter")
_SPLUNK_OUTPUTS = ("splunk_hec_url", "splunk_token_parameter", "splunk_service_name", "splunk_port_forward_command", "splunk_cm_service_name",
                   "splunk_idx_service_name", "splunk_cm_port_forward_command", "splunk_password_command")
_ana = {b: tf_text(b, "pipeline/analytics") for b in ("IaC/terraform/aws-managed", "IaC/terraform/oss")}
_loc = {b: _tf_values(_code(_ana[b]["network.tf" if b == "IaC/terraform/oss" else "locals.tf"]), "local", _SPLUNK_LOCALS) for b in _ana}
_out = {b: _tf_values(_ana[b]["outputs.tf"], "output", _SPLUNK_OUTPUTS) for b in _ana}
check(f"Splunk は OSS 版でも変えない（設計 005）: splunk.tf はマネージド版へのリンクで、splunk の locals と output はマネージド版と同じ値"
      f"（locals の違い: { {k: v for k, v in _loc['IaC/terraform/oss'].items() if v != _loc['IaC/terraform/aws-managed'][k]} }、output の違い: { {k: v for k, v in _out['IaC/terraform/oss'].items() if v != _out['IaC/terraform/aws-managed'][k]} }）",
      not links_to_managed("pipeline/analytics", ["splunk.tf"])
      and None not in _loc["IaC/terraform/aws-managed"].values() and _loc["IaC/terraform/oss"] == _loc["IaC/terraform/aws-managed"]
      and None not in _out["IaC/terraform/aws-managed"].values() and _out["IaC/terraform/oss"] == _out["IaC/terraform/aws-managed"])
check("Spark の splunk のタスクは HEC の token を ECS の secrets（SPLUNK_HEC_TOKEN。Splunk のタスクと同じ SSM の SecureString）で受け、"
      "--splunk-token-parameter は渡さない。実行ロールが読めるのは選んだ格納先のパラメータだけ",
      'splunk     = { name = "SPLUNK_HEC_TOKEN", valueFrom = local.splunk_token_parameter_arn }' in _spark
      and "secrets = [for sink, s in local.spark_secrets : s if contains(each.value, sink)]" in _spark
      and '[for a in ["--splunk-hec-url", local.splunk_hec_url, "--splunk-index", var.splunk_index] : a if contains(sinks, "splunk")]' in _spark
      and '[for a in ["--splunk-skip-verify"] : a if contains(sinks, "splunk") && local.splunk_skip_tls_verify]' in _spark
      and "--splunk-token-parameter" not in _spark
      and 'name = "SPLUNK_HEC_TOKEN", valueFrom = local.splunk_token_parameter_arn' in _code(_ana["IaC/terraform/aws-managed"]["splunk.tf"])
      and re.search(r'count = local\.sink_opensearch \|\| local\.sink_splunk \? 1 : 0[\s\S]*?\{ opensearch = "OpenSearchPassword", splunk = "SplunkHecToken" \}'
                    r'[\s\S]*?Action\s+= \["ssm:GetParameters"\]\s*\n\s*Resource = local\.spark_secrets\[sink\]\.valueFrom\s*\n\s*\} if contains\(var\.sinks, sink\)\]', _spark))
_graph_files = git_files("IaC/terraform/oss/pipeline/graph")
_graph_real = sorted(n for n in _graph_files if not os.path.islink(os.path.join(ROOT, "IaC", "terraform", "oss", "pipeline", "graph", n)))
_neo = _code(tf_text("IaC/terraform/oss", "pipeline/graph")["neo4j.tf"])
check(f"OSS 版の graph の実ファイルは Neo4j の分だけで（{_graph_real}）、Neo4j は ECS に 1 台。パスワードは SSM から secrets（GRAPH_PASSWORD）で、頂点の id は一意制約",
      _graph_real == ["access.tf", "neo4j.tf", "oss.auto.tfvars", "outputs.tf", "sync.tf"]
      and "desired_count   = 1" in _neo and 'secrets     = [{ name = "GRAPH_PASSWORD", valueFrom = local.neo4j_password_arn }]' in _neo
      and "REQUIRE n.id IS UNIQUE" in open(os.path.join(ROOT, "app", "agentcore", "graph.py"), encoding="utf-8").read())
check("Neo4j のタスクの healthCheck は Bolt（7687）が開いているかを bash で見て（公式イメージに curl と nc は無い）、"
      "5 分（30 秒 × 10 回）続けて開かないときだけ UNHEALTHY にする（入れ替えるとデータが消えるので、詰まった程度では入れ替えない）",
      re.search(r'healthCheck = \{\s*command\s*= \["CMD", "bash", "-c", "</dev/tcp/127\.0\.0\.1/7687"\]\s*interval\s*= 30\s*timeout\s*= 5\s*'
                r'retries\s*= 10\s*startPeriod = 180\s*\}', _neo) is not None and _neo.count("healthCheck") == 1)
_tpl = open(os.path.join(ROOT, "IaC", "terraform", "aws-managed", "base", "core", "templates", "web_user_data.sh.tftpl"), encoding="utf-8").read()
_wf = {n: _code(s) for n, s in tf_text("IaC/terraform/aws-managed", "workflow").items()}
check("status の Lambda・Worker・Web は環境変数で Neo4j に向く（Lambda は OSS 版の sync.tf、Worker は graph の state の neo4j_uri、Web は project = nwc-oss）。"
      "マネージド版のテンプレートは GRAPH_BACKEND も requirements-oss も出さない",
      re.search(r'^\s+GRAPH_BACKEND = "neo4j"\n\s+NEO4J_URI     = local\.neo4j_uri\n', _code(tf_text("IaC/terraform/oss", "pipeline/graph")["sync.tf"]), re.M)
      and 'graph_neo4j        = local.neo4j_uri != ""' in _wf["locals.tf"] and '{ name = "GRAPH_BACKEND", value = "neo4j" },' in _wf["ecs.tf"]
      and 'graph_backend = local.oss ? "neo4j" : ""' in tf_text("IaC/terraform/aws-managed", "base/core")["web.tf"]
      and _tpl.count("GRAPH_BACKEND") == 1 and '%{ if graph_backend != "" ~}\nGRAPH_BACKEND=${graph_backend}\n%{ endif ~}\n' in _tpl
      and '-r $APP/src/requirements%{ if graph_backend != "" }-oss%{ endif }.txt' in _tpl)
_ms = {t: re.findall(r"^\s*memory_size\s*=\s*(\d+)", tf_text(t, "pipeline/graph")["sync.tf"], re.M) for t in ("IaC/terraform/aws-managed", "IaC/terraform/oss")}
check(f"status の Lambda のメモリはマネージド版と OSS 版で同じ 256 MB（{_ms}。AWS で 128 MB のうち 111 MB を使った。Neo4j のドライバのレイヤー込み）",
      _ms == {"IaC/terraform/aws-managed": ["256"], "IaC/terraform/oss": ["256"]})
_req = {n: open(os.path.join(ROOT, "app", n, "requirements-oss.txt"), encoding="utf-8").read() for n in ("graph", "dashboard", "temporal", "nautobot")}
_pins = {n: re.findall(r"^neo4j==\S+$", s, re.M) for n, s in _req.items()}
check(f"Neo4j のドライバの版は Lambda の層・Web・Worker・Nautobot でそろえ（{_pins}）、Web と Worker と Nautobot はマネージド版の依存に足す"
      "（Worker と Nautobot の既定のビルドは今のまま）",
      all(len(p) == 1 for p in _pins.values()) and len({p[0] for p in _pins.values()}) == 1
      and all(re.search(r"^-r requirements\.txt$", _req[n], re.M) for n in ("dashboard", "temporal", "nautobot"))
      and all("ARG REQUIREMENTS=requirements.txt" in open(os.path.join(ROOT, "docker", "images", d, "Dockerfile"), encoding="utf-8").read() for d in ("temporal", "nautobot")))
# Nautobot の Job（app/nautobot/nwc/nb_sync.py）は app/agentcore/graph.py をそのまま使うので、OSS 版は Worker と同じ読み方（graph の state の neo4j_uri）で Neo4j に向ける
_nb = {n: _code(s) for n, s in tf_text("IaC/terraform/aws-managed", "pipeline/nautobot").items()}
_nb_up = {t: open(os.path.join(ROOT, *t, "up.sh"), encoding="utf-8").read() for t in (("ops",), ("ops", "oss"))}
_nb_dock = open(os.path.join(ROOT, "docker", "images", "nautobot", "Dockerfile"), encoding="utf-8").read()
check("Nautobot: OSS 版（graph の state に neo4j_uri がある）だけ NEPTUNE_GRAPH_ID の代わりに GRAPH_BACKEND=neo4j と NEO4J_URI を受け、パスワードは "
      "secrets で web と worker の両方に渡し（環境変数に値を置かない）、実行ロールはそのパラメータを足して読む。イメージは OSS 版の up.sh だけ "
      "requirements-oss.txt でビルドし、マネージド版の依存（app/nautobot/requirements.txt）に Neo4j のドライバは無い",
      'graph_neo4j        = local.neo4j_uri != ""' in _nb["locals.tf"]
      and re.search(r'local\.graph_neo4j \? \[\n\s+\{ name = "GRAPH_BACKEND", value = "neo4j" \},\n\s+\{ name = "NEO4J_URI", value = local\.neo4j_uri \},\n'
                    r'\s+\] : \[\n\s+\{ name = "NEPTUNE_GRAPH_ID", value = local\.neptune_graph_id \},\n\s+\]', _nb["nautobot.tf"])
      and _nb["nautobot.tf"].count("NEPTUNE_GRAPH_ID") == 1
      and re.search(r'local\.graph_neo4j \? \[\n\s+\{ name = "NEO4J_PASSWORD", valueFrom = local\.neo4j_password_arn \},\n\s+\] : \[\]\)', _nb["nautobot.tf"])
      and '!contains(["NAUTOBOT_SUPERUSER_PASSWORD", "NAUTOBOT_API_TOKEN"], s.name)' in _nb["nautobot.tf"]
      and "Resource = concat(values(local.secret_arns), local.graph_neo4j ? [local.neo4j_password_arn] : [])" in _nb["access.tf"]
      and re.search(r'^\s+build_nautobot "\$NAUTOBOT_TAG" "\$NAUTOBOT_CTX" requirements-oss\.txt\s', _nb_up[("ops", "oss")], re.M)
      and re.search(r'^\s+build_nautobot "\$NAUTOBOT_TAG" "\$NAUTOBOT_CTX"$', _nb_up[("ops",)], re.M)
      and '--build-arg "REQUIREMENTS=${3:-requirements.txt}"' in open(os.path.join(ROOT, "ops", "up-common.sh"), encoding="utf-8").read()
      and '-r "/tmp/nwc-requirements/$REQUIREMENTS"' in _nb_dock and "COPY requirements*.txt /tmp/nwc-requirements/" in _nb_dock
      and "neo4j" not in open(os.path.join(ROOT, "app", "nautobot", "requirements.txt"), encoding="utf-8").read())

# ---- 8. AWS で動かす前の点検（005）。ファイルを読むだけ（plan はしない）
# マネージド版と OSS 版を同じアカウントに並べたときの名前。接頭辞は var.owner = o のとき
_TREES = {"IaC/terraform/aws-managed": "o-nwc-poc", "IaC/terraform/oss": _PREFIX}
# アカウント（とリージョン）で一意な名前を持つ属性（resource の直下）
_NAME_KEYS = ("name", "name_prefix", "bucket", "function_name", "family", "creation_token", "graph_name", "identifier", "layer_name", "cluster_identifier")
# 名前が親のリソースの中でだけ一意なもの（親の名前に接頭辞がある）: ロールのインラインポリシー、Cloud Map のサービス（名前空間の中）、
# S3 Tables のテーブルと名前空間（テーブルバケットの中）、ロググループのストリーム、Gateway のターゲット、KB のデータソース
_SCOPED = {"aws_iam_role_policy", "aws_service_discovery_service", "aws_s3tables_table", "aws_s3tables_namespace", "aws_cloudwatch_log_stream",
           "aws_bedrockagentcore_gateway_target", "aws_bedrockagent_data_source"}
# アカウントかリージョンに 1 つしか無い設定（どちらかの木が作ると、もう片方の apply / destroy が上書きする）
_SINGLETON = re.compile(r"^aws_(\w*account\w*|iam_service_linked_role|default_\w+|ebs_encryption_by_default|ebs_default_kms_key|ecr_registry_\w+"
                        r"|ecr_replication_configuration|bedrock_model_invocation_logging_configuration|lakeformation_\w+|glue_\w+"
                        r"|guardduty_detector|config_configuration_recorder|ssm_service_setting|ec2_serial_console_access|ec2_image_block_public_access"
                        r"|vpc_block_public_access_options|xray_encryption_config|inspector2_enabler|cloudwatch_log_resource_policy)$")
_RES = re.compile(r'^resource "([a-z0-9_]+)" "([^"]+)" \{\n(.*?)^\}\n', re.M | re.S)


def _defs(base, root):
    """ルートの全ファイルの locals（1 行の定義）"""
    return {k: v for s in tf_text(base, root).values() for k, v in _tf_locals(_code(s)).items()}


def _reaches_prefix(expr, defs, seen=()):
    """名前の式が locals をたどって接頭辞（local.name_prefix か "${var.owner}-${var.project}"）に届く"""
    if "name_prefix" in expr or "${var.owner}-${var.project}" in expr:
        return True
    return any(k in defs and k not in seen and _reaches_prefix(defs[k], defs, seen + (k,)) for k in re.findall(r"\blocal\.(\w+)", expr))


def _resolve(expr, defs, prefix):
    """1 行の文字列の式（"…${local.X}…" か local.X）を、locals を展開して文字列にする"""
    v = expr.strip()
    if re.fullmatch(r"local\.\w+", v):
        return _resolve(defs[v[len("local."):]], defs, prefix)
    assert v.startswith('"') and v.endswith('"'), v
    s = re.sub(r"\$\{local\.(\w+)\}", lambda m: _resolve(defs[m.group(1)], defs, prefix), v[1:-1]).replace("${var.owner}-${var.project}", prefix)
    assert "${" not in s, (expr, s)
    return s


_named, _unprefixed, _types, _ns = {}, {}, {}, {}
for _b, _p in _TREES.items():
    for _r in TF_ROOTS:
        _d = _defs(_b, _r)
        for _n, _s in tf_text(_b, _r).items():
            for _t, _nm, _body in _RES.findall(_code(_s)):
                _types.setdefault(_b, set()).add(_t)
                if _t == "aws_service_discovery_private_dns_namespace":
                    _ns.setdefault(_b, []).append(_resolve(re.search(r"^  name\s+= (.+)$", _body, re.M).group(1), _d, _p))
                if _b == "IaC/terraform/oss" and os.path.islink(os.path.join(ROOT, _b, *_r.split("/"), _n)):
                    continue   # リンクのファイルの名前はマネージド版の木で見る
                for _key, _v in re.findall(r"^  (" + "|".join(_NAME_KEYS) + r")\s*=\s*(.+)$", _body, re.M):
                    _v = re.sub(r"\s+#.*$", "", _v).strip()
                    if _t in _SCOPED or re.match(r"^(aws_\w+|data\.\w+)\.\w+", _v):
                        continue   # 親の中の名前か、ほかのリソースの名前をそのまま使う
                    _named[_b] = _named.get(_b, 0) + 1
                    if not _reaches_prefix(_v, _d):
                        _unprefixed.setdefault(_b, []).append(f"{_r}/{_n}: {_t}.{_nm}.{_key} = {_v}")
_projects = re.findall(r'"(nwc-\w+)"', re.search(r"condition\s*=\s*contains\((\[[^\]]*\]), var\.project\)", _vars["base/core"]).group(1))
check(f"マネージド版と OSS 版を同じアカウントに並べても名前が重ならない: アカウントで一意な名前（マネージド版 {_named.get('IaC/terraform/aws-managed')} 個・"
      f"OSS 版の実ファイル {_named.get('IaC/terraform/oss')} 個）はどれも locals をたどると接頭辞 <owner>-<project> に届き（届かない: {_unprefixed}）、"
      f"project の 2 つの値（{_projects}）は同じ長さ（名前の長さの上限で切れ方が変わらない）",
      all(_named.get(b) for b in _TREES) and not _unprefixed and sorted(_projects) == ["nwc-oss", "nwc-poc"] and len({len(p) for p in _projects}) == 1)
_single = {b: sorted(t for t in v if _SINGLETON.match(t)) for b, v in _types.items()}
_down = {n: _code(open(os.path.join(ROOT, *n.split("/")), encoding="utf-8").read()) for n in ("ops/down.sh", "ops/down-common.sh", "ops/oss/down.sh")}
_ensure = re.search(r"^ensure_s3tables_catalog\(\) \{.*?^\}$", open(os.path.join(ROOT, "ops", "up-common.sh"), encoding="utf-8").read(), re.M | re.S)
check(f"どちらの木もアカウントかリージョンに 1 つの設定を Terraform で作らない（{ {b: v for b, v in _single.items() if v} }）。"
      "Glue のカタログ s3tablescatalog は ops/up-common.sh が無いときだけ作り、どちらの down も消さない（テーブルバケットの名前は接頭辞つき）",
      len(_types) == 2 and not any(_single.values()) and _ensure is not None
      and "*EntityNotFoundException*) ;;" in _ensure.group(0) and _ensure.group(0).count("aws glue create-catalog") == 1
      and not any(re.search(r"glue (delete|update)-catalog|s3tablescatalog", s) for s in _down.values()))
check(f"Cloud Map の名前空間は木の中で重ならず、マネージド版と OSS 版でも重ならない（マネージド版 {sorted(_ns.get('IaC/terraform/aws-managed', []))}、"
      f"OSS 版 {sorted(_ns.get('IaC/terraform/oss', []))}）",
      # マネージド版の stream の名前空間は Kafbat UI のためだけにあったので cycle 010 で無くなった（OSS 版の stream は Kafka の台ごとの名前に使う）
      sorted(_ns["IaC/terraform/aws-managed"]) == sorted(f"o-nwc-poc{s}.internal" for s in ("", "-nautobot"))
      and sorted(_ns["IaC/terraform/oss"]) == sorted(f"{_PREFIX}{s}.internal" for s in ("", "-stream", "-nautobot", "-graph"))
      and all(len(set(v)) == len(v) for v in _ns.values()) and not set(_ns["IaC/terraform/aws-managed"]) & set(_ns["IaC/terraform/oss"]))

# AZ の数（ENDPOINTS_AZ_NUM など）とサブネット a / b / c
_core = {n: _code(s) for n, s in tf_text("IaC/terraform/aws-managed", "base/core").items()}
check("サブネットは AZ の数の設定によらず a / b / c の 3 つを必ず作り（count も for_each も無い）、3 つの AZ ID が違うことは変数の検査（plan の前）で止める。"
      "3 つとも同じルートテーブルで、S3 の gateway エンドポイントはそのルートテーブルに付く。土台は 3 つを a / b / c の順に output する",
      all(re.search(rf'^resource "aws_subnet" "{z}" \{{\n  vpc_id\s+= aws_vpc\.this\.id\n  availability_zone_id\s+= var\.az_id_{z}\n', _core["vpc.tf"], re.M)
          for z in "abc")
      and "  subnet_ids = [aws_subnet.a.id, aws_subnet.b.id, aws_subnet.c.id]\n" in _core["vpc.tf"]
      and all(re.search(rf'^resource "aws_route_table_association" "{z}" \{{\n  subnet_id\s+= aws_subnet\.{z}\.id\n  route_table_id = aws_route_table\.private\.id\n',
                        _core["vpc.tf"], re.M) for z in "abc")
      and "route_table_ids   = [aws_route_table.private.id]" in _core["endpoints.tf"]
      and "var.az_id_b != var.az_id_a" in _vars["base/core"] and "var.az_id_c != var.az_id_a && var.az_id_c != var.az_id_b" in _vars["base/core"]
      and "value       = local.subnet_ids" in _block(_core["outputs.tf"], "output", "subnet_ids"))
_az_refs = {b: {f"{r}/{n}": c for r in TF_ROOTS for n, s in tf_text(b, r).items()
                if (c := len(re.findall(r"\b(?:var\.endpoints_az_num|local\.endpoint_subnet_ids)\b", _code(s))))} for b in _TREES}
check(f"ENDPOINTS_AZ_NUM が変えるのはインターフェース型と OpenSearch Serverless の VPC エンドポイントの ENI を置くサブネット（a から n 個）だけで"
      f"（参照: {_az_refs['IaC/terraform/oss']}）、サブネットの数・EFS のマウントターゲット（3 つのサブネット全部）・3 台のサービスの置き場には効かない",
      all(v == {"base/core/endpoints.tf": 3, "base/core/variables.tf": 1} for v in _az_refs.values())
      and "  endpoint_subnet_ids = slice(local.subnet_ids, 0, var.endpoints_az_num)\n" in _core["endpoints.tf"]
      and len(re.findall(r"^  subnet_ids\s+= local\.endpoint_subnet_ids$", _core["endpoints.tf"], re.M)) == 2
      and "count = local.oss ? length(local.subnet_ids) : 0" in _oss_tf and "subnet_id       = local.subnet_ids[count.index]" in _oss_tf)
_kafka_svc = _block(_k, "resource", "aws_ecs_service.kafka")
_need3 = 'precondition {{\n      condition     = {} != ""\n      error_message = "IaC/terraform/aws-managed/base/core の state のサブネットが 3 つ無い'
check("Kafka・OpenSearch・vmstorage は AZ ごとに 1 台でサブネットを 0〜2 番目まで使い、土台の state のサブネットが 3 つ無ければ ECS のサービスの precondition で "
      "plan のうちに止まる（足りない台のサブネットは空になる）",
      'kafka_nodes = { for i in range(3) : tostring(i + 1) => try(local.subnet_ids[i], "") }' in _k
      and "subnets          = [each.value]" in _kafka_svc and _need3.format("each.value") in _kafka_svc
      and [s for _, s, _ in _os_nodes] == ["0", "1", "2"] and _need3.format("each.value.subnet") in _os_svc
      and 'vmstorage_nodes = local.sink_prometheus ? { for i in range(3) : tostring(i + 1) => try(local.subnet_ids[i], "") } : {}' in _vm_tf
      and "subnets          = [each.value]" in _vs_svc and _need3.format("each.value") in _vs_svc
      and all("subnet_ids = data.terraform_remote_state.main.outputs.subnet_ids" in _code(s)
              for s in (_o_stream["locals.tf"], _o_an["network.tf"])))

# OSS 版の stream の outputs.tf（マネージド版へのリンク）の bootstrap_brokers
_sd = _defs("IaC/terraform/oss", _STREAM)
_kns = _resolve("local.stream_service_namespace", _sd, _PREFIX)
_brokers = ",".join(f"kafka-{i}.{_kns}:9092" for i in (1, 2, 3))   # Terraform は map を鍵の順（"1"〜"3"）に回す
check(f"OSS 版の bootstrap_brokers は {_brokers}（PLAINTEXT）: outputs.tf は差し替え口の kafka_bootstrap_brokers を出し、kafka.tf はそれを"
      "台ごとの Cloud Map の名前（kafka-N）・advertised listener・コンテナのポート 9092 と同じ名前で作る。Kafbat UI は PLAINTEXT を選ぶ",
      not links_to_managed(_STREAM, ["outputs.tf"]) and "value       = local.kafka_bootstrap_brokers" in _block(_m_stream["outputs.tf"], "output", "bootstrap_brokers")
      and 'kafka_hosts = { for n, _ in local.kafka_nodes : n => "kafka-${n}.${local.stream_service_namespace}" }' in _k
      and 'kafka_bootstrap_brokers = join(",", [for n, h in local.kafka_hosts : "${h}:9092"])' in _k
      and "kafka_bootstrap_by_protocol = { PLAINTEXT = local.kafka_bootstrap_brokers }" in _k
      and 'name = "kafka-${each.key}"' in _block(_k, "resource", "aws_service_discovery_service.kafka")
      and '{ name = "KAFKA_ADVERTISED_LISTENERS", value = "PLAINTEXT://${local.kafka_hosts[each.key]}:9092" }' in _k
      and "{ containerPort = 9092, protocol = \"tcp\" }" in _k and _kns == f"{_PREFIX}-stream.internal"
      and re.search(r'^kafka_ui_security_protocol = "PLAINTEXT"$', open(_auto[_STREAM], encoding="utf-8").read(), re.M) is not None)
check("OSS 版の Spark は stream の state の bootstrap_brokers を --bootstrap で受け、KAFKA_AUTH=none で読む（空なら precondition で止まる）。"
      "Kafka の 9092 には Spark・Telegraf（dial-out）・gnmic（cycle 013）・Web（cycle 010 から Kafbat UI が Web の EC2 に同居）の SG の行がある",
      'bootstrap = try(data.terraform_remote_state.stream.outputs.bootstrap_brokers, "")' in _code(_o_an["network.tf"])
      and re.search(r'"--bootstrap",\s*local\.bootstrap', _spark) is not None
      and '{ name = "KAFKA_AUTH", value = "none" }' in _spark and 'condition     = local.bootstrap != ""' in _spark
      and {"spark", "telegraf_dialout", "gnmic", "web"} <= _from("kafka", 9092) and "kafka_ui" not in _from("kafka", 9092) and "telegraf_dialin" not in _from("kafka", 9092))


# Neo4j・OpenSearch・VictoriaMetrics を使う側の SG
def _sg_keys(base, root, name):
    """ファイルが ENI に付ける SG の、土台の security_group_ids の鍵"""
    d, keys = _defs(base, root), set()
    for v in re.findall(r"^\s+(?:vpc_)?security_groups?(?:_ids)?\s+= \[(.+)\]$", _code(tf_text(base, root)[name]), re.M):
        m = re.fullmatch(r'aws_security_group\.workload\["(\w+)"\]\.id', v) \
            or re.fullmatch(r'try\(data\.terraform_remote_state\.main\.outputs\.security_group_ids\["(\w+)"\], ""\)', d.get(v.removeprefix("local."), ""))
        keys.add(m.group(1) if m else v)
    return keys


_users = {("IaC/terraform/oss", "pipeline/graph", "sync.tf"): "lambda", ("IaC/terraform/aws-managed", "workflow", "gateway.tf"): "lambda",
          ("IaC/terraform/aws-managed", "workflow", "ecs.tf"): "workflow", ("IaC/terraform/aws-managed", "base/core", "web.tf"): "web",
          ("IaC/terraform/aws-managed", "agent", "runtime.tf"): "runtime", ("IaC/terraform/aws-managed", "pipeline/nautobot", "nautobot.tf"): "nautobot",
          ("IaC/terraform/oss", "pipeline/graph", "neo4j.tf"): "neo4j"}
_got = {f"{b}/{r}/{n}": _sg_keys(b, r, n) for b, r, n in _users}
check(f"status の Lambda と道具の Lambda は lambda、Worker は workflow、Web は web、Runtime は runtime、Nautobot は nautobot、Neo4j は neo4j の SG を付ける（{_got}）",
      all(_got[f"{b}/{r}/{n}"] == {k} for (b, r, n), k in _users.items()))
_need = {("neo4j", 7687): {"lambda", "workflow", "web", "runtime", "nautobot"}, ("opensearch", 9200): {"lambda", "runtime", "spark"},
         ("victoriametrics", 8481): {"lambda", "runtime"}, ("victoriametrics", 8480): {"spark"}}
_lack = {f"{t}:{p}": sorted(c - _from(t, p)) for (t, p), c in _need.items() if c - _from(t, p)}
_api = set(re.findall(r'"(\w+)"', re.search(r"^  aws_api_clients = \[(.*)\]$", tf_text("IaC/terraform/aws-managed", "base/core")["security_groups.tf"], re.M).group(1)))
_oss_api = set(re.findall(r'"(\w+)"', re.search(r"^  oss_api_clients = \[(.*)\]$", _oss_tf, re.M).group(1)))
check(f"土台の SG の行: Neo4j の 7687 には status の Lambda・道具の Lambda（lambda）・Worker・Web・Runtime・Nautobot、OpenSearch の 9200 と vmselect の 8481 には "
      f"lambda と Runtime（9200 と vminsert の 8480 には Spark）から行があり（足りない: {_lack}）、Neo4j のコンテナは 7687 で受ける。"
      "使う側も Neo4j・OpenSearch・VictoriaMetrics のタスクも AWS の API（VPC エンドポイントの 443）と S3（443）へ出られる",
      not _lack and "{ containerPort = 7687, protocol = \"tcp\" }" in _neo and 'neo4j_uri = "bolt://${local.neo4j_host}:7687"' in _neo
      and {"lambda", "workflow", "web", "runtime", "nautobot", "spark"} <= _api and {"kafka", "opensearch", "victoriametrics", "neo4j"} <= _oss_api
      and all(re.search(rf'\[for sg in local\.{v} : \[\n\s+\{{ from = sg, to = "endpoints", protocol = "tcp", port = 443, why = "[^"]*" \}},\n'
                        rf'\s+\{{ from = sg, to = "s3", protocol = "tcp", port = 443, why = "[^"]*" \}},\n', s)
              for v, s in (("aws_api_clients", tf_text("IaC/terraform/aws-managed", "base/core")["security_groups.tf"]), ("oss_api_clients", _oss_tf))))

# ---- 9. エージェントを OSS 版につなぐ（005）。道具の Lambda と Runtime の Terraform を読み、OSS 版の環境変数でコードを動かす
_gw, _wfl, _wiam = (_code(tf_text("IaC/terraform/aws-managed", "workflow")[n]) for n in ("gateway.tf", "locals.tf", "iam.tf"))
_fn = _block(_gw, "resource", "aws_lambda_function.tools")
check("道具の Lambda: OSS 版（graph の state に neo4j_uri がある）だけ GRAPH_BACKEND=neo4j と NEO4J_URI、analytics が OSS 版（state に OpenSearch の "
      "パスワードの名前がある）だけ OPENSEARCH_AUTH=basic と PROMETHEUS_AUTH=none を足す。マネージド版では足す map は空で、環境変数は今のまま",
      'local.graph_neo4j ? { GRAPH_BACKEND = "neo4j", NEO4J_URI = local.neo4j_uri } : {},' in _fn
      and 'local.analytics_oss ? { OPENSEARCH_AUTH = "basic", PROMETHEUS_AUTH = "none" } : {},' in _fn
      and 'analytics_oss = try(data.terraform_remote_state.analytics.outputs.opensearch_password_parameter, "") != ""' in _wfl
      and all(_gw.count(k) == 1 for k in ("GRAPH_BACKEND", "NEO4J_URI", "OPENSEARCH_AUTH", "PROMETHEUS_AUTH")))
_oss_graph = "".join(_code(v) for v in tf_text("IaC/terraform/oss", "pipeline/graph").values())
_oss_an = "".join(_code(v) for v in tf_text("IaC/terraform/oss", "pipeline/analytics").values())
check("道具の Lambda: Neo4j のドライバは graph のルートが作るレイヤー（output neo4j_layer_arn）を OSS 版だけ付け、state に無ければ plan で止まる。"
      "マネージド版は layers を付けない（null）",
      "layers = local.graph_neo4j ? [local.neo4j_layer_arn] : null" in _fn
      and 'condition     = !local.graph_neo4j || local.neo4j_layer_arn != ""' in _fn
      and 'neo4j_layer_arn = try(data.terraform_remote_state.graph.outputs.neo4j_layer_arn, "")' in _wfl
      and re.search(r'output "neo4j_layer_arn" \{[^}]*value\s+= aws_lambda_layer_version\.neo4j\.arn', _oss_graph)
      and 'output "opensearch_password_parameter"' in _oss_an
      and "neo4j_layer_arn" not in "".join(tf_text("IaC/terraform/aws-managed", "pipeline/graph").values()))
_rt, _al = (_code(tf_text("IaC/terraform/aws-managed", "agent")[n]) for n in ("runtime.tf", "locals.tf"))
check("Runtime: OSS 版（var.project = nwc-oss。IaC/terraform/oss/agent の oss.auto.tfvars）だけ GRAPH_BACKEND=neo4j・OPENSEARCH_AUTH=basic・"
      "PROMETHEUS_AUTH=none を足す。マネージド版では空の map",
      'local.oss ? { GRAPH_BACKEND = "neo4j", OPENSEARCH_AUTH = "basic", PROMETHEUS_AUTH = "none" } : {},' in _rt
      and 'oss = var.project == "nwc-oss"' in _al and _rt.count("GRAPH_BACKEND") == 1
      and re.search(r'^project = "nwc-oss"$', open(os.path.join(ROOT, "IaC/terraform/oss/agent/oss.auto.tfvars"), encoding="utf-8").read(), re.M)
      and re.search(r'^project = "nwc-oss"$', open(os.path.join(ROOT, "IaC/terraform/oss/workflow/oss.auto.tfvars"), encoding="utf-8").read(), re.M))
check("パスワードは Lambda と Runtime の環境変数に置かない（コードが SSM の <PARAM_PREFIX>/neo4j-password・opensearch-password を引く。"
      "道具の Lambda は接頭辞の下の ssm:GetParameter を持ち、Runtime の実行ロールには OSS 版の graph の access.tf が付ける）",
      not re.search(r"NEO4J_PASSWORD|OPENSEARCH_PASSWORD", _gw + _rt)
      and 'resources = ["arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.param_prefix}/*"]' in _gw
      and 'Action   = "ssm:GetParameter"' in _oss_graph and "neptune-graph:" not in _oss_graph)
_areq = open(os.path.join(ROOT, "app", "agentcore", "requirements-oss.txt"), encoding="utf-8").read()
_adock = open(os.path.join(ROOT, "docker", "images", "agentcore", "Dockerfile"), encoding="utf-8").read()
check("Runtime のイメージ: app/agentcore/requirements-oss.txt はマネージド版の依存に Neo4j のドライバ（ほかと同じ版）を足し、Dockerfile の既定は requirements.txt のまま",
      re.findall(r"^neo4j==\S+$", _areq, re.M) == _pins["temporal"] and re.search(r"^-r requirements\.txt$", _areq, re.M)
      and "ARG REQUIREMENTS=requirements.txt" in _adock and '-r "$REQUIREMENTS"' in _adock
      and "neo4j" not in open(os.path.join(ROOT, "app", "agentcore", "requirements.txt"), encoding="utf-8").read())

# マネージドの口（opensearch_collection_endpoint は名前だけ引き継いだ差し替え口で、OSS 版では OpenSearch の URL が入る）。workflow の locals はこの output が state にあるときだけ値を持ち、IAM の行はその値があるときだけ付く
_MANAGED_OUT = ("graph_id", "graph_arn", "opensearch_collection_name", "opensearch_collection_arn",
                "prometheus_workspace_id", "prometheus_workspace_arn")
_has = [o for o in _MANAGED_OUT if f'output "{o}"' in _oss_graph + _oss_an]
check(f"OSS 版の graph・analytics は、Neptune・OpenSearch Serverless・AMP の output を 1 つも出さない（出している: {_has}）", not _has)


def _guard(text, action):
    """action を含む行を囲む dynamic "statement" の for_each（無ければ None）"""
    out = []
    for m in re.finditer(re.escape(action), text):
        head = text[:m.start()]
        i = head.rfind('dynamic "statement"')
        j = head.rfind("\n  statement {")
        g = re.search(r"for_each = ([^\n]+)", head[i:]) if i > j else None
        out.append(g.group(1).strip() if g else None)
    return out


_guards = {"gateway neptune": _guard(_gw, '"neptune-graph:ReadDataViaQuery"'), "gateway aoss": _guard(_gw, '"aoss:APIAccessAll"'),
           "gateway aps": _guard(_gw, '"aps:QueryMetrics"'), "worker neptune": _guard(_wiam, '"neptune-graph:ReadDataViaQuery"')}
check(f"workflow の IAM: Neptune・aoss・aps の行は、マネージドの output があるとき（OSS 版では無い）だけ付く（{_guards}）",
      _guards == {"gateway neptune": ['local.neptune_data_arn != "" ? [1] : []'], "gateway aoss": ['local.opensearch_collection_arn != "" ? [1] : []'],
                  "gateway aps": ['local.prometheus_workspace_arn != "" ? [1] : []'], "worker neptune": ["local.graph_neo4j ? [] : [1]"]}
      and all(f'{k} = try(data.terraform_remote_state.{r}.outputs.{o}, "")' in re.sub(r" +", " ", _wfl)
              for k, r, o in (("neptune_data_arn", "graph", "graph_arn"), ("opensearch_collection_arn", "analytics", "opensearch_collection_arn"),
                              ("opensearch_collection_name", "analytics", "opensearch_collection_name"),
                              ("prometheus_workspace_arn", "analytics", "prometheus_workspace_arn")))
      and 'count = var.create_gateway && local.opensearch_collection_name != "" ? 1 : 0' in _block(_gw, "resource", "aws_opensearchserverless_access_policy.tools"))
_wf_all = "".join(_code(v) for v in tf_text("IaC/terraform/aws-managed", "workflow").values())
_ag = {n: _code(v) for n, v in tf_text("IaC/terraform/aws-managed", "agent").items()}
_kb_res = [f"{t}.{n}" for t, n, body in re.findall(r'^(?:resource|data) "([^"]+)" "([^"]+)" \{\n(.*?)^\}\n', _ag["kb.tf"], re.M | re.S)
           if re.search(r"aoss|opensearchserverless", t + body) and not body.startswith("  count = local.kb ? 1 : 0\n")]
check("ほかにマネージドの行は無い: workflow の neptune-graph・aoss・aps の行は上の 4 か所とアクセスポリシーだけ、MSK（kafka）の行は無い。"
      f"agent のルートの aoss は kb.tf だけで、kb.tf は create_knowledge_base（既定 false）のときだけ作る（count の無いもの: {_kb_res}）",
      len(re.findall(r'"neptune-graph:ReadDataViaQuery"', _wf_all)) == 2 and len(re.findall(r'"aps:QueryMetrics"', _wf_all)) == 1
      and len(re.findall(r'"aoss:\w+"', _wf_all)) == 4 and not re.search(r'"kafka(-cluster)?:|aws_msk', _wf_all)
      and all(not re.search(r'"aoss:|neptune-graph|"aps:|kafka', v) for n, v in _ag.items() if n != "kb.tf")
      and not _kb_res and re.search(r'variable "create_knowledge_base" \{[^}]*default\s+= false', _ag["variables.tf"]))

# コードを OSS 版の環境変数で動かし、boto3 のクライアントの名前と、送ったものの署名を集める
_made = []
_plain_client = boto3.client
boto3.client = lambda name, **kw: (_made.append(name), _plain_client(name, **kw))[1]
_OSS_ENV = dict(NEO4J, NEPTUNE_GRAPH_ID=None, OPENSEARCH_AUTH="basic", PROMETHEUS_AUTH="none", OPENSEARCH_PASSWORD="pw-os")


def _oss_run():
    state["calls"] = []
    g = load(os.path.join(AGENT, "graph.py"), "graph")
    scenario(g, with_algo=False)
    a = load(os.path.join(WORKFLOW, "awsio.py"), "awsio")
    awsio_scenario(a)
    return g.BACKEND, a.GRAPH_ENV


try:
    _backend = with_env(_OSS_ENV, _oss_run)
    _ncalls = len(state["calls"])
    (_logs, _met), _s = ev(**{k: v for k, v in _OSS_ENV.items() if v is not None})
finally:
    boto3.client = _plain_client
check(f"OSS 版の環境変数（道具の Lambda・Runtime・Worker・Web に渡すもの）で graph.py・awsio.py・evidence.py を動かすと、neptune-graph の"
      f"クライアントを 1 つも作らず（作ったもの: {sorted(set(_made))}）、OpenSearch と vmselect へ SigV4（aoss / aps）の署名を付けない",
      _backend == ("neo4j", "NEO4J_URI") and "neptune-graph" not in _made and _ncalls > 0
      and len(_s) == 2 and not any("AWS4-HMAC-SHA256" in (x["auth"] or "") for x in _s) and _logs["count"] == 1 and "error" not in _met)
_py = {f"{d}/{n}": open(os.path.join(ROOT, d, n), encoding="utf-8").read() for d in ("app/agentcore", "app/dashboard", "app/temporal")
       for n in sorted(os.listdir(os.path.join(ROOT, d))) if n.endswith(".py")}
_np = sorted(n for n, t in _py.items() if re.search(r"""["']neptune-graph["']""", _code(t)))
_sig = sorted(n for n, t in _py.items() if re.search(r"SigV4Auth\((?![^\n]*\"bedrock-agentcore\")", _code(t)))   # Gateway（bedrock-agentcore）への署名は両方の版で同じ
_kafka = sorted(n for n, t in _py.items() if re.search(r"kafka|\bmsk\b", _code(t), re.I))
check(f"エージェント・Web・Worker のコードで、neptune-graph のクライアントを作るのは切り替えのある graph.py と awsio.py だけ（{_np}）、SigV4 で送るのは "
      f"切り替えのある evidence.py と、KB を作るときだけ呼ぶ kb_index.py だけ（{_sig}）、Kafka（MSK）を読むコードは無い（{_kafka}）",
      _np == ["app/agentcore/graph.py", "app/temporal/awsio.py"] and _sig == ["app/agentcore/evidence.py", "app/agentcore/kb_index.py"] and not _kafka)
_ecs, _web = _code(tf_text("IaC/terraform/aws-managed", "workflow")["ecs.tf"]), _code(tf_text("IaC/terraform/aws-managed", "base/core")["web.tf"])
check("Worker のタスク定義は OSS 版では NEPTUNE_GRAPH_ID を渡さず（GRAPH_BACKEND と NEO4J_URI に替える）、Web は OSS 版で GRAPH_BACKEND=neo4j を受ける",
      re.search(r'local\.graph_neo4j \? \[\n\s+\{ name = "GRAPH_BACKEND", value = "neo4j" \},\n\s+\{ name = "NEO4J_URI", value = local\.neo4j_uri \},\n'
                r'\s+\] : \[\n\s+\{ name = "NEPTUNE_GRAPH_ID", value = local\.neptune_graph_id \},\n\s+\]', _ecs)
      and _ecs.count("NEPTUNE_GRAPH_ID") == 1 and 'graph_backend = local.oss ? "neo4j" : ""' in _web)

# ---- 10. OSS 版の Grafana（005 の 3）。analytics の grafana.tf は実ファイル（マネージド版の grafana.tf は AMP と OpenSearch Serverless を引くのでリンクできない）。
# イメージ（app/grafana/）・ダッシュボード・アラートのルールはマネージド版と同じものを使い、違うのはデータソースの宛先と認証（環境変数）とタスクロールだけ
_g = {b: _code(_ana[b]["grafana.tf"]) for b in _ana}
_g_res = {b: {f"{t}.{n}": _squeeze(body) for t, n, body in _RES.findall(_g[b])} for b in _g}
_g_names = {b: {k: m.group(1) for k, v in _g_res[b].items() if (m := re.search(r"^\s*(?:name|family)\s+= (.+)$", v, re.M))} for b in _g}
_g_m_hits = sorted({m.group(0) for m in _forbidden.finditer(_g["IaC/terraform/aws-managed"])})
_G_SAME = ("aws_cloudwatch_log_group.grafana", "aws_service_discovery_service.grafana", "aws_iam_role_policy_attachment.grafana_execution",
           "aws_iam_role_policy_attachment.grafana_execution_perimeter", "aws_iam_role_policy_attachment.grafana_task_perimeter")
check(f"OSS 版の analytics の grafana.tf は実ファイルで、マネージド版の grafana.tf（{_g_m_hits} を引く）にはリンクしない。リソースはマネージド版から OpenSearch Serverless の"
      "データアクセスポリシーを抜いたもので、名前（name・family）は同じ。ロググループ・Cloud Map・実行ロールの管理ポリシー・閉域の Deny の付け方はマネージド版と同じ",
      "grafana.tf" in _an_real and links_to_managed(_AN, ["grafana.tf"]) and _g_m_hits
      and set(_g_res["IaC/terraform/oss"]) == set(_g_res["IaC/terraform/aws-managed"]) - {"aws_opensearchserverless_access_policy.grafana"}
      and _g_names["IaC/terraform/oss"] == {k: v for k, v in _g_names["IaC/terraform/aws-managed"].items() if k in _g_res["IaC/terraform/oss"]}
      and all(_g_res["IaC/terraform/oss"][k] == _g_res["IaC/terraform/aws-managed"][k] for k in _G_SAME))
check("ECS のサービス（1 タスク・サブネット a・Grafana の SG・Cloud Map）はマネージド版と同じで、depends_on から OpenSearch Serverless のポリシーが抜けるだけ",
      _g_res["IaC/terraform/oss"]["aws_ecs_service.grafana"]
      == _g_res["IaC/terraform/aws-managed"]["aws_ecs_service.grafana"].replace("\n    aws_opensearchserverless_access_policy.grafana,", ""))
_G_LOCALS = ("grafana_image", "grafana_log_group", "grafana_password_arn")
_G_OUTPUTS = ("grafana_service_name", "grafana_port_forward_command", "grafana_password_command")
_g_loc = {b: _tf_locals(_g[b]) for b in _g}
check(f"Grafana の locals（network.tf の grafana_sg_id・grafana_password_parameter・create_grafana と grafana.tf の {', '.join(_G_LOCALS)}）と "
      f"output（{', '.join(_G_OUTPUTS)}）はマネージド版と同じ（作るかどうかは同じ var.create_grafana、パスワードは同じ名前の SSM のパラメータ）",
      {"grafana_sg_id", "grafana_password_parameter", "create_grafana"} <= set(_net)
      and all(_g_loc["IaC/terraform/oss"][n] == _g_loc["IaC/terraform/aws-managed"][n] for n in _G_LOCALS)
      and all(_squeeze(_block(_ana["IaC/terraform/oss"]["outputs.tf"], "output", n)) == _squeeze(_block(_ana["IaC/terraform/aws-managed"]["outputs.tf"], "output", n)) != ""
              for n in _G_OUTPUTS))

# タスク定義の環境変数と secrets
_g_td = {b: _block(_g[b], "resource", "aws_ecs_task_definition.grafana") for b in _g}
_g_env = {b: dict(re.findall(r'^\s+\{ name = "(\w+)", value = (.+?) \},?$', _g_td[b], re.M)) for b in _g}
_G_OSS_ENV = {"AWS_REGION": "var.region", "PROMETHEUS_URL": "local.prometheus_select_url", "OPENSEARCH_URL": "local.opensearch_endpoint",
              "OPENSEARCH_INDEX": "local.opensearch_index", "PROMETHEUS_AUTH": '"none"', "OPENSEARCH_AUTH": '"basic"', "ALERTS_TOPIC_ARN": "local.alerts_topic_arn"}
_g_rest = {b: _squeeze(re.sub(r"(?s)environment = \[.*?\n      \]\n|secrets = (?:\[.*?\n      \]|local\.grafana_secrets)\n|error_message = [^\n]*\n", "", _g_td[b]))
           for b in _g}
check(f"タスク定義の環境変数（{_g_env['IaC/terraform/oss']}）: PROMETHEUS_URL は vmselect の根、OPENSEARCH_URL は自前の OpenSearch、PROMETHEUS_AUTH=none と "
      "OPENSEARCH_AUTH=basic で datasources-oss を選ぶ。ほかはマネージド版と同じ値で、環境変数と secrets のほか（イメージ・ポート・CPU・ログ）もマネージド版と同じ",
      _g_env["IaC/terraform/oss"] == _G_OSS_ENV
      and {k: v for k, v in _G_OSS_ENV.items() if k not in ("PROMETHEUS_AUTH", "OPENSEARCH_AUTH", "PROMETHEUS_URL")}
      == {k: v for k, v in _g_env["IaC/terraform/aws-managed"].items() if k != "PROMETHEUS_URL"}
      and _g_rest["IaC/terraform/oss"] == _g_rest["IaC/terraform/aws-managed"] and "environment" not in _g_rest["IaC/terraform/aws-managed"])
_g_sec = re.findall(r'\{ name = "(\w+)", valueFrom = (local\.\w+) \}', _g["IaC/terraform/oss"])
check(f"パスワードは環境変数に書かず ECS の secrets（{_g_sec}）: admin はマネージド版と同じ SSM のパラメータ、OpenSearch は OpenSearch のタスクと Spark と同じ "
      "SecureString（OpenSearch を作るときだけ）。実行ロールが読めるのは secrets に並べたものだけ",
      _g_sec == [("GF_SECURITY_ADMIN_PASSWORD", "local.grafana_password_arn"), ("OPENSEARCH_PASSWORD", "local.opensearch_password_arn")]
      and not [k for k in _g_env["IaC/terraform/oss"] if "PASSWORD" in k]
      and "      secrets = local.grafana_secrets\n" in _g_td["IaC/terraform/oss"]
      and '[for s in [{ name = "OPENSEARCH_PASSWORD", valueFrom = local.opensearch_password_arn }] : s if local.sink_opensearch],' in _g["IaC/terraform/oss"]
      and 'name = "GF_SECURITY_ADMIN_PASSWORD", valueFrom = local.grafana_password_arn' in _g["IaC/terraform/aws-managed"]
      and 'secrets = [{ name = "OPENSEARCH_INITIAL_ADMIN_PASSWORD", valueFrom = local.opensearch_password_arn }]' in _os_tf
      and 'opensearch = { name = "OPENSEARCH_PASSWORD", valueFrom = local.opensearch_password_arn }' in _spark
      and re.search(r'Action\s+= \["ssm:GetParameters"\]\n\s+Resource = \[for s in local\.grafana_secrets : s\.valueFrom\]\n',
                    _block(_g["IaC/terraform/oss"], "resource", "aws_iam_role_policy.grafana_execution")) is not None)
_g_task = {b: _block(_g[b], "resource", "aws_iam_role_policy.grafana_task") for b in _g}
_g_publish = re.compile(r'Sid\s+= "PublishAlerts"\n\s+Effect\s+= "Allow"\n\s+Action\s+= \["sns:Publish"\]\n\s+Resource = local\.alerts_topic_arn\n')
check("タスクロールはアラートの SNS のトピックへの publish だけ（マネージド版の PublishAlerts と同じ行）。AMP（aps）と OpenSearch Serverless（aoss）の許可は無い",
      re.findall(r'Sid\s+= "(\w+)"', _g_task["IaC/terraform/oss"]) == ["PublishAlerts"]
      and _g_publish.search(_g_task["IaC/terraform/oss"]) and _g_publish.search(_g_task["IaC/terraform/aws-managed"])
      and not re.search(r'"(?:aps|aoss):', _g["IaC/terraform/oss"]) and re.search(r'"(?:aps|aoss):', _g["IaC/terraform/aws-managed"]))


# app/grafana/start.sh を OSS 版のタスク定義の環境変数で走らせ、並ぶファイルをマネージド版と比べる
def grafana_files(**env):
    """app/grafana/start.sh が並べるファイル {datasources|alerting|dashboards/<名前>: 中身}（start_sh と同じやり方。環境変数は渡したものだけ）"""
    with tempfile.TemporaryDirectory() as tmp:
        sh = open(os.path.join(ROOT, "app", "grafana", "start.sh"), encoding="utf-8").read()
        sh = sh.replace("SRC=/etc/grafana/nwc", f"SRC={os.path.join(ROOT, 'app', 'grafana', 'provisioning')}")
        sh = sh.replace("/tmp/grafana-", f"{tmp}/grafana-").replace('exec /run.sh "$@"', "true")
        assert "/etc/grafana" not in sh and "exec " not in sh
        subprocess.run(["sh", "-c", sh], capture_output=True, text=True, check=True, env={"PATH": os.environ["PATH"], **env})
        dirs = {"datasources": f"{tmp}/grafana-provisioning/datasources", "alerting": f"{tmp}/grafana-provisioning/alerting", "dashboards": f"{tmp}/grafana-dashboards"}
        return {f"{k}/{n}": open(os.path.join(d, n), encoding="utf-8").read() for k, d in dirs.items() for n in sorted(os.listdir(d))}


_TOPIC = f"arn:aws:sns:ap-northeast-1:123456789012:{_PREFIX}-alerts"
_g_val = {"var.region": "ap-northeast-1", "local.alerts_topic_arn": _TOPIC}
_g_oss_env = {k: v[1:-1] if v.startswith('"') else _g_val.get(v) or _render(v[len("local."):], _L) for k, v in _g_env["IaC/terraform/oss"].items()}
_g_oss = grafana_files(**_g_oss_env)
_g_m = grafana_files(AWS_REGION="ap-northeast-1", PROMETHEUS_URL="https://aps-workspaces.ap-northeast-1.amazonaws.com/workspaces/ws-x",
                     OPENSEARCH_URL="https://x.ap-northeast-1.aoss.amazonaws.com", OPENSEARCH_INDEX="snmp-logs", ALERTS_TOPIC_ARN=_TOPIC)
# ルールとダッシュボードのデータソースの参照を持つファイルは app/grafana/provisioning の中だけ（docs と tests は除く）
_g_copies = subprocess.run(["git", "grep", "-l", "--untracked", "-e", "datasourceUid", "-e", '"uid": "amp"', "-e", '"uid": "aoss-logs"',
                            "--", ".", ":!docs", ":!tests", ":!app/grafana/provisioning"], capture_output=True, text=True, cwd=ROOT).stdout.split()
_G_FILES = {"datasources/prometheus.yaml", "datasources/opensearch.yaml", "dashboards/metrics.json", "dashboards/logs.json", "dashboards/flows.json",
            "alerting/nwc.yaml", "alerting/nwc-prometheus.yaml", "alerting/nwc-opensearch.yaml"}
check(f"OSS 版の環境変数（PROMETHEUS_URL={_g_oss_env['PROMETHEUS_URL']}、OPENSEARCH_URL={_g_oss_env['OPENSEARCH_URL']}）で start.sh は datasources-oss の 2 つと、"
      "マネージド版と同じダッシュボード 3 つ・アラートの定義 3 つ（app/grafana/provisioning のファイルそのもの）を並べる。ダッシュボードとルールの写しはリポジトリに無い",
      set(_g_oss) == set(_g_m) == _G_FILES
      and _g_oss_env["PROMETHEUS_URL"] == SELECT_URL and _g_oss_env["OPENSEARCH_URL"] == OS_URL and _g_oss_env["OPENSEARCH_INDEX"] == _index
      and all(_g_oss[f"datasources/{n}"] == provisioning("datasources-oss", n) for n in ("prometheus.yaml", "opensearch.yaml"))
      and all(_g_oss[f] == _g_m[f] == provisioning(*f.split("/")) for f in _G_FILES if not f.startswith("datasources/"))
      and sorted(f for f in git_files("app/grafana") if f.endswith((".json", ".yaml")) and not f.startswith("provisioning/datasources"))
      == sorted("provisioning/" + f for f in _G_FILES if not f.startswith("datasources/")) + ["provisioning/dashboards/nwc.yaml"]
      and not _g_copies)


def _ds_refs(o):
    """ダッシュボードの JSON の中の "datasource": {"uid": …} を全部"""
    if isinstance(o, dict):
        return ([o["datasource"]["uid"]] if isinstance(o.get("datasource"), dict) else []) + [u for v in o.values() for u in _ds_refs(v)]
    return [u for v in o for u in _ds_refs(v)] if isinstance(o, list) else []


_g_have = {u for f, s in _g_oss.items() if f.startswith("datasources/") for u in uids(s)}
_g_want = {f: set(_ds_refs(json.loads(s))) if f.endswith(".json") else set(re.findall(r"^\s+datasourceUid: (\S+)$", s, re.M)) - {"__expr__"}
           for f, s in _g_oss.items() if f.startswith(("dashboards/", "alerting/nwc-"))}
check(f"ダッシュボードとアラートのルールが引くデータソースの uid（{ {f: sorted(v) for f, v in _g_want.items()} }）は、どれも OSS 版で並べたデータソース（{sorted(_g_have)}）にある",
      _g_have == {"amp", "aoss-logs"} and all(_g_want.values()) and set().union(*_g_want.values()) <= _g_have)
_g_vars = set(re.findall(r"\$\{(\w+)\}", "".join(s for f, s in _g_oss.items() if f.endswith(".yaml"))))
check(f"並べた定義が起動時に読む環境変数（{sorted(_g_vars)}）は、どれもタスク定義の環境変数か secrets か start.sh の既定（OPENSEARCH_USER）で入る",
      _g_vars and _g_vars <= set(_g_env["IaC/terraform/oss"]) | {n for n, _ in _g_sec} | {"OPENSEARCH_USER"})

# flows（GoFlow2）のダッシュボード（cycle 033）。logs.json と同じデータソース・同じ index（topic: flows）で、名前は snmp_sinks.py の FLOW_TAGS / FLOW_FIELDS
_fl = json.loads(provisioning("dashboards", "flows.json"))
_fl_t = [t for p in _fl["panels"] for t in p["targets"]]
_fl_aggs = [a.get("field") for t in _fl_t for a in t["metrics"] + t["bucketAggs"] if a.get("field")]
_sinks_spec = importlib.util.spec_from_file_location("snmp_sinks_033", os.path.join(ROOT, "app", "spark", "snmp_sinks.py"))
_sinks = importlib.util.module_from_spec(_sinks_spec)
_sinks_spec.loader.exec_module(_sinks)   # 標準ライブラリだけで読める（pyspark は関数の中で import する）
_fl_tags = {n for n, _ in _sinks.FLOW_TAGS}
_fl_fields = {n for n, _ in _sinks.FLOW_FIELDS}
check(f"flows.json: uid nwc-flows、panel 6 つ、どの panel と query もデータソース aoss-logs（logs.json と同じ）、query は lucene の topic:flows、timeField は @timestamp。"
      f"集計のフィールド（{sorted(set(_fl_aggs))}）は snmp_sinks.py の flow の tags（文字列なので .keyword）と fields の名前（cycle 033）",
      _fl["uid"] == "nwc-flows" and _fl["title"] == "nwc / flows" and _fl["editable"] is False and len(_fl["panels"]) == 6 == len(_fl_t)
      and len({p["id"] for p in _fl["panels"]}) == 6
      and all(p["datasource"] == t["datasource"] == {"type": "grafana-opensearch-datasource", "uid": "aoss-logs"} for p in _fl["panels"] for t in p["targets"])
      and all(t["queryType"] == "lucene" and t["query"] == "topic:flows" and t["timeField"] == "@timestamp" for t in _fl_t)
      and {"tags.src.keyword", "tags.dst.keyword", "tags.proto.keyword", "tags.sampler.keyword", "fields.bytes"} <= set(_fl_aggs)
      and all(f == "@timestamp" or f in {f"tags.{n}.keyword" for n in _fl_tags} | {f"fields.{n}" for n in _fl_fields} for f in _fl_aggs)
      and {"src", "dst", "proto", "sampler"} <= _fl_tags and "bytes" in _fl_fields)
# panel ごとの中身は設計の表どおり: (型, title に入る語, bucketAggs の (type, field) の並び)。metric は 1〜5 が sum(fields.bytes)、6 が logs の 100 件。
# terms は size 10・降順で、並べる基準（orderBy）は同じ query の sum の id。値の単位は decbytes（bytes の合計。/s に直さない）
_FL_SPEC = [("timeseries", "bytes", [("date_histogram", "@timestamp")]),
            ("table", "送信元", [("terms", "tags.src.keyword")]),
            ("table", "宛先", [("terms", "tags.dst.keyword")]),
            ("piechart", "プロトコル", [("terms", "tags.proto.keyword")]),
            ("timeseries", "sampler", [("terms", "tags.sampler.keyword"), ("date_histogram", "@timestamp")]),
            ("logs", "生の flow", [])]
def _fl_panel_ok(p, spec):
    typ, word, aggs = spec
    (t,) = p["targets"]
    terms_ok = all(a["settings"]["size"] == "10" and a["settings"]["order"] == "desc" and a["settings"]["orderBy"] == t["metrics"][0]["id"]
                   for a in t["bucketAggs"] if a["type"] == "terms")
    metric_ok = (t["metrics"] == [{"id": "1", "type": "logs", "settings": {"limit": "100"}}] if typ == "logs"
                 else t["metrics"] == [{"id": "1", "type": "sum", "field": "fields.bytes"}] and p["fieldConfig"]["defaults"]["unit"] == "decbytes")
    return p["type"] == typ and word in p["title"] and [(a["type"], a["field"]) for a in t["bucketAggs"]] == aggs and terms_ok and metric_ok
check("flows.json の panel ごとの中身は設計の表どおり: 1 bytes の合計 × 時間、2 / 3 送信元 / 宛先の上位 10（table）、4 プロトコル別（piechart）、"
      "5 sampler 別 × 時間、6 生の flow（logs、100 件）。1〜5 は sum(fields.bytes) で単位 decbytes、terms は sum の降順（cycle 033）",
      [p["id"] for p in _fl["panels"]] == [1, 2, 3, 4, 5, 6] and all(_fl_panel_ok(p, s) for p, s in zip(_fl["panels"], _FL_SPEC)))
check("flows.json は start.sh が OPENSEARCH_URL のあるときだけ並べる（Prometheus だけなら metrics.json だけ、OpenSearch だけなら logs.json と flows.json だけ。cycle 033）",
      "dashboards/flows.json" in _g_m and {f for f in grafana_files(AWS_REGION="ap-northeast-1", PROMETHEUS_URL="https://x") if f.startswith("dashboards/")} == {"dashboards/metrics.json"}
      and {f for f in grafana_files(AWS_REGION="ap-northeast-1", OPENSEARCH_URL="https://x") if f.startswith("dashboards/")} == {"dashboards/logs.json", "dashboards/flows.json"})

# アラートの経路: Grafana の連絡先 → 土台の SNS のトピック → status の Lambda（OSS 版の graph の sync.tf）と SQS → ワークフロー（IaC/terraform/aws-managed/workflow の events.tf）
_nets = _g_oss["alerting/nwc.yaml"]
_sub = {f"{b}/{r}": _block(_code(tf_text(b, r)[n]), "resource", f"aws_sns_topic_subscription.{s}")
        for b, r, n, s in (("IaC/terraform/oss", "pipeline/graph", "sync.tf", "status"), ("IaC/terraform/oss", "workflow", "events.tf", "anomalies"))}
_topic_expr = 'alerts_topic_arn = try(data.terraform_remote_state.main.outputs.alerts_topic_arn, "")'
check("アラートの経路はマネージド版と同じ: 連絡先は SNS（タスクロールの SigV4。鍵は書かない）で topic_arn は ALERTS_TOPIC_ARN、Grafana・status の Lambda・ワークフローは"
      "どれも OSS 版の土台の state の alerts_topic_arn を読み、status の Lambda（lambda）と SQS（sqs）がそのトピックを購読する",
      re.search(r"^\s+type: sns\n[\s\S]*?^\s+topic_arn: \$\{ALERTS_TOPIC_ARN\}\n\s+sigv4:\n\s+region: \$\{AWS_REGION\}\n", _nets, re.M) is not None
      and not re.search(r"access_key|secret_key|assume_role", _nets) and re.search(r"^\s+receiver: nwc-sns$", _nets, re.M) is not None
      and all(re.search(r"^\s+topic_arn\s+= local\.alerts_topic_arn\n", s, re.M) for s in _sub.values())
      and 'protocol  = "lambda"' in _sub["IaC/terraform/oss/pipeline/graph"] and re.search(r'protocol\s+= "sqs"', _sub["IaC/terraform/oss/workflow"]) is not None
      and not links_to_managed("workflow", ["events.tf", "locals.tf"])
      and all(_topic_expr in _code(tf_text("IaC/terraform/oss", r)[n]) for r, n in (("pipeline/analytics", "network.tf"), ("pipeline/graph", "locals.tf"), ("workflow", "locals.tf"))))

# 土台の SG と VPC エンドポイント
_ep = set(re.search(r'^ENDPOINTS="([^"]*)"$', open(os.path.join(ROOT, "ops", "oss", "up.sh"), encoding="utf-8").read(), re.M).group(1).split())
_ecr_repos = re.search(r"pipeline_repositories = toset\(\[(.*)\]\)", tf_text("IaC/terraform/aws-managed", "base/ecr")["main.tf"]).group(1)
check(f"Grafana のタスクは grafana の SG（{_sg_keys('IaC/terraform/oss', _AN, 'grafana.tf')}）を付け、土台の SG で OpenSearch の 9200・vmselect の 8481・AWS の API へ出られ、"
      f"web の EC2 から 3000 で開ける。ops/oss/up.sh が作る VPC エンドポイント（{sorted(_ep)}）に ECR・ログ・SSM（secrets）・SNS（アラート）がある。イメージの置き場は土台の ECR の grafana",
      _sg_keys("IaC/terraform/oss", _AN, "grafana.tf") == _sg_keys("IaC/terraform/aws-managed", _AN, "grafana.tf") == {"grafana"}
      and "grafana" in _from("opensearch", _port(OS_URL)) and "grafana" in _from("victoriametrics", _port(SELECT_URL)) and "grafana" in _api
      and '{ from = "web", to = "grafana", protocol = "tcp", port = 3000,' in tf_text("IaC/terraform/aws-managed", "base/core")["security_groups.tf"]
      and "portMappings = [{ containerPort = 3000, protocol = \"tcp\" }]" in _g_td["IaC/terraform/oss"]
      and {"ecr.api", "ecr.dkr", "logs", "ssm", "sns"} <= _ep and '"grafana"' in _ecr_repos)

# ---- パスワードはタスク定義の environment（平文）に置かない
_neo_env = re.findall(r'\{\s*name\s*=\s*"(\w+)",\s*value\s*=', _neo)
_neo_pw = [n for n in re.findall(r'\bname\s*=\s*"(\w+)"', _neo) if any(w in n for w in ("NEO4J_AUTH", "NEO4J_PASSWORD", "PASSWORD"))]
check("Neo4j のタスク定義: パスワード系の名前は secrets の GRAPH_PASSWORD（valueFrom = local.neo4j_password_arn）だけで、environment（value =）に NEO4J_AUTH・NEO4J_PASSWORD・*PASSWORD* は無い",
      _neo_env and _neo_pw == ["GRAPH_PASSWORD"]
      and not [n for n in _neo_env if any(w in n for w in ("NEO4J_AUTH", "NEO4J_PASSWORD", "PASSWORD"))]
      and 'secrets     = [{ name = "GRAPH_PASSWORD", valueFrom = local.neo4j_password_arn }]' in _neo)
_oss_env = {r: [n for t in tf_text("IaC/terraform/oss", r).values() for n in re.findall(r'\{\s*name\s*=\s*"(\w+)",\s*value\s*=', _code(t))] for r in OSS_REAL}
check("OSS 版の 3 つの実体ルート（stream / analytics / graph）の .tf に、environment の { name = \"…PASSWORD… / …TOKEN… / …SECRET…\", value = … } は 0 件（KAFKA_AUTH などの方式名は数えない）",
      all(_oss_env[r] for r in OSS_REAL)
      and not [n for ns in _oss_env.values() for n in ns if any(w in n for w in ("PASSWORD", "TOKEN", "SECRET"))])

# ---- 土台の SG の表は、いまの oss.tf から起こした 33 行と完全に一致する（増えても減っても気づく）
_sg_expected = {(sg, to, 443, 443) for sg in ("kafka", "opensearch", "victoriametrics", "neo4j") for to in ("endpoints", "s3")} | {
    ("telegraf_dialout", "kafka", 9092, 9092), ("gnmic", "kafka", 9092, 9092), ("spark", "kafka", 9092, 9092),   # gnmic は cycle 013 で telegraf_dialin に替えた
    ("syslog_ng", "kafka", 9092, 9092), ("goflow2", "kafka", 9092, 9092),   # syslog-ng と GoFlow2（cycle 012。OSS 版は認証なしの 9092）
    ("web", "kafka", 9092, 9092), ("kafka", "kafka", 9092, 9093),
    ("kafka", "efs", 2049, 2049), ("victoriametrics", "efs", 2049, 2049),
    ("spark", "opensearch", 9200, 9200), ("grafana", "opensearch", 9200, 9200), ("runtime", "opensearch", 9200, 9200),
    ("lambda", "opensearch", 9200, 9200), ("opensearch", "opensearch", 9300, 9300),
    ("spark", "victoriametrics", 8480, 8480), ("grafana", "victoriametrics", 8481, 8481), ("runtime", "victoriametrics", 8481, 8481),
    ("lambda", "victoriametrics", 8481, 8481), ("victoriametrics", "victoriametrics", 8400, 8401),
    ("web", "neo4j", 7687, 7687), ("runtime", "neo4j", 7687, 7687), ("lambda", "neo4j", 7687, 7687), ("workflow", "neo4j", 7687, 7687),
    ("nautobot", "neo4j", 7687, 7687), ("web", "neo4j", 7474, 7474)}
_sg_actual = set(_sg_rows) | {(sg, to, 443, 443) for sg in _oss_api for to in ("endpoints", "s3")}
check("土台の SG の表（IaC/terraform/aws-managed/base/core/oss.tf の oss_flows）は oss_api_clients 4 × 2（endpoints / s3）+ kafka 7（syslog-ng と GoFlow2 は cycle 012 で足した）+ efs 2 + opensearch 5 + victoriametrics 5 + neo4j 6（7474 の web→neo4j を含む）= 33 行と完全に一致し、重複は無い",
      len(_sg_expected) == 33 and _sg_actual == _sg_expected and len(_sg_rows) == 25 == len(set(_sg_rows))
      and _oss_api == {"kafka", "opensearch", "victoriametrics", "neo4j"})

# ---- ops/check.sh は OSS 版のスクリプトと検査を漏らさない
_chk_bash_n = re.search(r"^SH=\$\(git ls-files '\*\.sh'\)$", _chk, re.M)
_chk_sh = set(subprocess.run(["git", "ls-files", "*.sh"], capture_output=True, text=True, cwd=ROOT, check=True).stdout.split())
_chk_find = re.search(r"^find (.*?) -name '\*\.py'", _chk, re.M)
check("ops/check.sh: bash -n は git ls-files '*.sh' の全部で、そこに ops/oss の 4 つ（oss-images.sh / up.sh / down.sh / roll-nodes.sh）があり、.py の find に ops があり、モックの検査は tests/test_*.py のグロブで回す（名前を 1 つずつ並べない）",
      _chk_bash_n and {"ops/oss/oss-images.sh", "ops/oss/up.sh", "ops/oss/down.sh", "ops/oss/roll-nodes.sh"} <= _chk_sh
      and _chk_find and "ops" in _chk_find.group(1).split()
      and re.search(r"^\s*for t in tests/test_\*\.py; do$", _chk, re.M) is not None
      and "for t in tests/test_agentcore.py" not in _chk)
# 旧名は字面で書かない（書くとこのファイルが ops/check.sh の 5. に掛かる）
_chk_old = _chk[_chk.find('\nlog "5. '):_chk.find("\nprintf '\\nすべて通過")]
check("ops/check.sh: 5. で旧名の grep を git ls-files に打ち、docs/cycles と docs/verification を除き、許すのは tables.tf（aws-managed と oss のリンク）と tests/test_analytics.py の 5 行だけ（cycle 023）",
      "OLD_NAME='net''ops'\n" in _chk and _chk.find("OLD_NAME=") < _chk.find('\nlog "5. ') < _chk.find("\nprintf '\\nすべて通過")
      and "git ls-files -z | grep -z -v -e '^docs/cycles/' -e '^docs/verification/' | xargs -0 grep -l -i \"$OLD_NAME\"" in _chk_old
      and all(p in _chk_old for p in ("IaC/terraform/aws-managed/pipeline/analytics/tables.tf", "IaC/terraform/oss/pipeline/analytics/tables.tf", "tests/test_analytics.py"))
      and '[ "$n" -eq 5 ]' in _chk_old and "LC_ALL=C sort" in _chk_old)

# ---- app/neo4j/entrypoint.sh: GRAPH_PASSWORD を NEO4J_AUTH に直してから公式の entrypoint を呼ぶ（偽物の tini と entrypoint で通す）
def neo4j_entrypoint(**env):
    """(終了コード, 偽物の entrypoint が受けた {env, args}, stderr)"""
    with tempfile.TemporaryDirectory() as tmp:
        b = os.path.join(tmp, "bin")
        os.makedirs(b)
        with open(os.path.join(b, "tini"), "w", encoding="utf-8") as f:   # tini -g -- <cmd> … をそのまま exec する
            f.write('#!/bin/sh\nwhile [ $# -gt 0 ] && [ "$1" != "--" ]; do shift; done\n[ "${1:-}" = "--" ] && shift\nexec "$@"\n')
        fake_ep = os.path.join(tmp, "docker-entrypoint.sh")
        with open(fake_ep, "w", encoding="utf-8") as f:   # 受けた環境変数と引数を JSON で出す
            f.write(f'#!{sys.executable}\nimport json, os, sys\nprint(json.dumps({{"env": dict(os.environ), "args": sys.argv[1:]}}))\n')
        for p in (os.path.join(b, "tini"), fake_ep):
            os.chmod(p, 0o755)
        sh = open(os.path.join(ROOT, "app", "neo4j", "entrypoint.sh"), encoding="utf-8").read()
        assert sh.count("/startup/docker-entrypoint.sh") >= 1
        sh = sh.replace("/startup/docker-entrypoint.sh", fake_ep)
        ep = os.path.join(tmp, "entrypoint.sh")
        with open(ep, "w", encoding="utf-8") as f:
            f.write(sh)
        r = subprocess.run(["bash", ep, "neo4j", "console"], capture_output=True, text=True, env={"PATH": b + os.pathsep + os.environ["PATH"], **env})
        got = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
        return r.returncode, got, r.stderr

_rc1, _got1, _ = neo4j_entrypoint(GRAPH_PASSWORD="s3cret-pass")
_rc2, _got2, _ = neo4j_entrypoint(NEO4J_AUTH="neo4j/given-pass")
_rc3, _got3, _err3 = neo4j_entrypoint()
check("app/neo4j/entrypoint.sh: GRAPH_PASSWORD があれば NEO4J_AUTH=neo4j/<値> にして公式の entrypoint（tini -g -- …）へ渡し、GRAPH_PASSWORD は消す。引数はそのまま",
      _rc1 == 0 and _got1 and _got1["env"].get("NEO4J_AUTH") == "neo4j/s3cret-pass" and "GRAPH_PASSWORD" not in _got1["env"]
      and _got1["args"] == ["neo4j", "console"])
check("app/neo4j/entrypoint.sh: NEO4J_AUTH だけなら、そのまま通す",
      _rc2 == 0 and _got2 and _got2["env"].get("NEO4J_AUTH") == "neo4j/given-pass" and "GRAPH_PASSWORD" not in _got2["env"])
check("app/neo4j/entrypoint.sh: どちらも無ければ公式の entrypoint を呼ばずに終了コード 1 で止まり、stderr で GRAPH_PASSWORD を名指しする",
      _rc3 == 1 and _got3 is None and "GRAPH_PASSWORD" in _err3)

print(f"通過 {passed} / 失敗 0")
