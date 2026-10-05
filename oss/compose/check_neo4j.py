"""005 ステップ 1 の 4: Neo4j Community Edition + GDS（jar はイメージに焼き込み。起動時のダウンロードなし）。

agent/data の静的データ（ops/seed_graph.py が Neptune に入れるのと同じ機器と回線）を入れ、agent/graph.py の centrality() と同じ
3 つ（degree は両向き、closeness、wcc）を GDS で出す。島の数が 1 になるか。
同じグラフを素の Python（幅優先探索）でも計算し、順位と島の数を突き合わせる（Neptune の値は手元に無いので、定義どおりの値と比べる）。

使い方: python3 oss/compose/check_neo4j.py（終わってもコンテナは残す。片付けは docker compose --profile '*' down -v）
"""
import collections
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "..", "agent", "data")
PASSWORD = "NwcOss-Trial-2026"   # compose.yaml の試し用の値


def dc(*args, stdin=None, check=True):
    return subprocess.run(["docker", "compose", "--profile", "neo4j", *args], cwd=HERE, input=stdin, text=True,
                          capture_output=True, check=check)


def cypher(q):
    r = dc("exec", "-T", "neo4j", "cypher-shell", "-u", "neo4j", "-p", PASSWORD, "--format", "plain", stdin=q, check=False)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def load():
    """topology.json の機器と回線（agent/topology.py の load_static と同じ。devices.yaml の機器は topology.json の nodes と同じ顔ぶれ）"""
    with open(os.path.join(DATA, "topology.json"), encoding="utf-8") as f:
        topo = json.load(f)
    devices = [n["device_id"] for n in topo["nodes"]]
    links = sorted({tuple(sorted((l["a"], l["b"]))) for l in topo["links"] if l["a"] != l["b"]})
    return devices, links


def reference(devices, links):
    """定義どおりの値。degree = 付いている回線の数、closeness = (到達できる台数) / (距離の合計)、島 = 弱連結成分"""
    adj = collections.defaultdict(list)
    for a, b in links:
        adj[a].append(b)
        adj[b].append(a)
    out = {}
    for d in devices:
        dist = {d: 0}
        q = collections.deque([d])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if v not in dist:
                    dist[v] = dist[u] + 1
                    q.append(v)
        far = sum(dist.values())
        out[d] = {"degree": len(adj[d]), "closeness": round((len(dist) - 1) / far, 4) if far else 0.0, "reach": frozenset(dist)}
    return out, len({v["reach"] for v in out.values()})


def main():
    print("== イメージを作って起こす（ネットワークは外に出られない isolated だけ）")
    dc("build", "neo4j")
    dc("up", "-d", "neo4j")
    for _ in range(120):
        try:
            cypher("RETURN 1;")
            break
        except RuntimeError:
            time.sleep(1)
    logs = dc("logs", "neo4j").stdout
    print("  起動ログのうち plugin とダウンロードに関わる行:",
          [l.strip() for l in logs.splitlines() if any(k in l.lower() for k in ("plugin", "download", "graphdatascience", "fetching"))] or "なし")
    print("  plugins/:", dc("exec", "-T", "neo4j", "ls", "/var/lib/neo4j/plugins").stdout.split())
    print("  版:", cypher("CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition;").splitlines()[1:])
    print("  GDS:", cypher("RETURN gds.version() AS gds;").splitlines()[1:], cypher("CALL gds.license.state() YIELD isLicensed, details RETURN isLicensed, details;").splitlines()[1:])

    devices, links = load()
    print(f"== 静的データを入れる（{len(devices)} 台、{len(links)} 本。id はプロパティ id に入れ、一意制約を付ける）")
    rows = "[" + ", ".join(f"{{a: {json.dumps(a)}, b: {json.dumps(b)}}}" for a, b in links) + "]"   # Cypher の map の並び
    cypher("MATCH (n) DETACH DELETE n;")
    cypher("CREATE CONSTRAINT device_id IF NOT EXISTS FOR (n:device) REQUIRE n.id IS UNIQUE;")
    cypher(f"UNWIND {json.dumps(devices)} AS id CREATE (:device {{id: id}});")
    cypher(f"UNWIND {rows} AS r MATCH (a:device {{id: r.a}}), (b:device {{id: r.b}}) CREATE (a)-[:link]->(b);")
    print("  ", cypher("MATCH (n:device) WITH count(n) AS devices MATCH ()-[l:link]->() RETURN devices, count(l) AS links;").splitlines()[1:])

    print("== GDS（agent/graph.py の centrality と同じ 3 つ。回線は向きなしで投影）")
    cypher("CALL gds.graph.drop('net', false) YIELD graphName RETURN graphName;")
    cypher("CALL gds.graph.project('net', 'device', {link: {orientation: 'UNDIRECTED'}}) YIELD nodeCount, relationshipCount RETURN nodeCount, relationshipCount;")
    got = {}
    for line in cypher("CALL gds.degree.stream('net') YIELD nodeId, score RETURN gds.util.asNode(nodeId).id AS id, score;").splitlines()[1:]:
        k, v = line.split(", ")
        got.setdefault(k.strip('"'), {})["degree"] = int(float(v))
    for line in cypher("CALL gds.closeness.stream('net') YIELD nodeId, score RETURN gds.util.asNode(nodeId).id AS id, score;").splitlines()[1:]:
        k, v = line.split(", ")
        got.setdefault(k.strip('"'), {})["closeness"] = round(float(v), 4)
    comps = {}
    for line in cypher("CALL gds.wcc.stream('net') YIELD nodeId, componentId RETURN gds.util.asNode(nodeId).id AS id, componentId;").splitlines()[1:]:
        k, v = line.split(", ")
        comps[k.strip('"')] = int(v)
    for k, v in comps.items():
        got.setdefault(k, {})["component"] = v
    order = sorted(got, key=lambda d: (-got[d]["closeness"], -got[d]["degree"], d))
    print(f"  島の数（wcc）: {len(set(comps.values()))}")
    for d in order:
        print(f"  {d:16s} degree {got[d]['degree']}  closeness {got[d]['closeness']}")

    ref, islands = reference(devices, links)
    ref_order = sorted(ref, key=lambda d: (-ref[d]["closeness"], -ref[d]["degree"], d))
    same_values = all(ref[d]["degree"] == got[d]["degree"] and ref[d]["closeness"] == got[d]["closeness"] for d in devices)
    print(f"== 定義どおりの値と比べる: 順位が同じ {order == ref_order}、degree と closeness が全部同じ {same_values}、島の数 {islands}")

    print("== Neo4j が外へ送る設定（使用状況の報告）")
    print("  ", cypher("SHOW SETTINGS YIELD name, value WHERE name CONTAINS 'usage_report' RETURN name, value;").splitlines()[1:])
    print("== 終わり")


if __name__ == "__main__":
    main()
