"""Neptune Analytics（terraform/pipeline/graph）に置いたトポロジの読み書き。boto3 の neptune-graph で openCypher を送る（IAM 認証の署名は boto3 が付ける）。
2026-10-04 に Neptune（データベース。Gremlin）から移した。Neptune Analytics は openCypher だけで、アルゴリズム（neptune.algo.*）をグラフの上でそのまま回せる。

グラフの ID（g-xxxxxxxxxx）は環境変数 NEPTUNE_GRAPH_ID、無ければ SSM の <PARAM_PREFIX>/neptune-graph-id（terraform/pipeline/graph が書く）。
どちらも無ければ configured() が False で、topology.py は data/ の静的データを使う（graph を作っていなくても動く）。
接続先は boto3 がグラフの ID とリージョンから決める（https://<グラフの ID>.<region>.neptune-graph.amazonaws.com。VPC の中では
インターフェース型エンドポイント neptune-graph-data の private DNS がこの名前を引き受ける）。

値は全部クエリのパラメータ（$name）で渡す（文字列を埋め込まないので、修復案の本文や承認者の名前に何が入っても壊れない）。
ラベルと property の名前はパラメータにできないので、このファイルの定数か、英数字と _ だけと確かめたもの（_ident）だけを埋め込む。
辺の id は Neptune Analytics が振り、自分では決められない（振り直しもある）ので、辺は id で引かず「両端の機器 + a_if」で引く。

グラフの形は data/topology.json に、インタフェースの頂点を足したもの:
  頂点 label=device, id=device_id。property: hostname, site, role, asn, mgmt_ip, enabled, status
  頂点 label=interface, id=<device_id>#<IF 名>。property: device_id, name, address, status（機器とは辺でなく device_id でつなぐ）
  辺   label=link, a → b（a < b）。property: a_if, b_if, kind, role, bandwidth_mbps, status
インタフェースは lab/lab_topology.py が lab の定義から作る全部（管理の mgmt0 やリンクに出ない IF も）。agent/data の静的データには無い。

物理層より上（IP 層 / EVPN・BGP 層。data/layers.json、lab/lab_topology.py の layers）は、機器やインタフェースとは別の頂点で、
下の層の頂点の id を property に持つ（interface_id / ip_interface_id。層をまたぐ紐づけの鍵）:
  頂点 label=ip_interface（id <機器>#<IF>.<n>）、isis_adjacency（<機器>#isis#<IF>.<n>）、bgp_session（<機器>#bgp#<相手の IP>）、
       evpn_instance（<機器>#evi#<EVI>）、ethernet_segment（<機器>#es#<名前>）。property は lab_topology.py の docstring の通り + status + layer（ip / evpn）
  辺   over（上の層 → 下の層の頂点）、peer（IS-IS の隣接 / BGP のセッションの両端）、tunnel（同じ EVI 同士）、attach（EVI → LAG の IF）、segment（同じ ESI 同士）
status は物理層と同じ動的な状態で、gNMI の検知（bgp_down / isis_down）を受けた graph/status_handler.py が set_layer_status() で書く。

status（UP / DOWN / ALARM）は動的な状態で、Grafana / Splunk のアラート（firing / resolved。SNS のトピック）を受けた graph/status_handler.py（terraform/pipeline/graph の Lambda）が
set_status() で書く。無ければ UP。seed() で入れ直すと消える（静的な構成だけを入れる）。
Nautobot を正にしているとき（terraform/pipeline/nautobot）は、Nautobot の Job が sync_physical() で物理層だけを差分で合わせる（status と上の層は残る）。

トポロジに無い機器やインタフェースの異常は捨てずに「未登録」の頂点（property registered=false。機器は role=unknown）として残し、
set_status() の戻り値に unregistered を付ける（Lambda が WARNING でログに出す。登録漏れの印）。登録済みの頂点は registered を持たない。
あとから seed() / add_device() で登録すると未登録の頂点は置き換わり、UP でない status は引き継ぐ。
"""

import json
import re

import boto3
from botocore.config import Config

import toolkit

REGION = toolkit.REGION
GRAPH_ID = toolkit.Param("NEPTUNE_GRAPH_ID", "neptune-graph-id")  # グラフの ID（環境変数か SSM）
CHUNK = 500  # UNWIND で 1 本のクエリに載せる行数（seed）
_cache = {"client": None}


def graph_id() -> str:
    return GRAPH_ID.value()


def configured() -> bool:
    """Neptune Analytics を配備してあるか（無ければ topology.py は data/ の静的データに戻る）"""
    return bool(graph_id())


def _client():
    """neptune-graph のクライアント（接続先はクエリごとにグラフの ID から決まるので 1 つを使い回す）"""
    if _cache["client"] is None:
        # 既定（接続 60 秒 × 再試行）だと SG で落とされたときに 1 回の呼び出しが数分かかり、
        # ops/up.sh の 7-3b が何十分も黙る。接続は 10 秒・再試行 2 回（max_attempts は再試行の数で、最初と合わせて 3 回）で早く諦める。
        # graph-status の Lambda はここを使わず、status_handler.py の NEPTUNE_CONFIG に差し替える
        _cache["client"] = boto3.client(
            "neptune-graph", region_name=REGION,
            config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 2}))
    return _cache["client"]


def query(cypher: str, **params) -> list:
    """openCypher を 1 本打ち、結果の行（RETURN の名前 → 値の dict）の list を返す"""
    kw = {"parameters": params} if params else {}
    res = _client().execute_query(graphIdentifier=graph_id(), queryString=cypher, language="OPEN_CYPHER", **kw)
    return json.loads(res["payload"].read()).get("results", [])


def _node(n: dict) -> dict:
    """結果の頂点（{"~id", "~labels", "~properties"}）を {id, label, property…} の dict に"""
    return {**(n.get("~properties") or {}), "id": n.get("~id"), "label": (n.get("~labels") or [None])[0]}


def _ident(name: str) -> str:
    """クエリに埋め込むラベル / property の名前。英数字と _ だけを通す（パラメータにできない場所に値を入れないため）"""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
        raise ValueError(f"名前に使えない文字がある: {name!r}")
    return f"`{name}`"


def _props(d: dict, keys) -> dict:
    """d から keys の値を拾った dict（SET n += $props に渡す）。None と空文字は書かない。入れ子の値は文字列にする"""
    return {k: (d[k] if isinstance(d[k], (str, bool, int, float)) else str(d[k]))
            for k in keys if d.get(k) is not None and d.get(k) != ""}


def _chunks(rows: list):
    for i in range(0, len(rows), CHUNK):
        yield rows[i:i + CHUNK]


# maintenance = Nautobot で保守中（status が Maintenance）の機器だけ true（Job が同期する。保守が明ければ property ごと消える。2026-10-04）
DEVICE_KEYS = ("hostname", "site", "role", "asn", "mgmt_ip", "enabled", "maintenance", "status")
CHANGE_KEYS = ("time", "user", "action", "object_type", "object", "device_id", "detail")   # label change（Nautobot の変更履歴の写し）
IF_KEYS = ("device_id", "name", "address", "lag", "status")
LINK_KEYS = ("a_if", "b_if", "kind", "role", "bandwidth_mbps", "status")
STATUSES = ("UP", "DOWN", "ALARM")
LAYER_LABELS = ("ip_interface", "isis_adjacency", "bgp_session", "evpn_instance", "ethernet_segment")
LAYER_EDGES = ("over", "peer", "tunnel", "attach", "segment")
LAYER_KIND = {"bgp": "bgp_session", "isis": "isis_adjacency"}   # set_layer_status の kind（id の真ん中）→ label
_LAYER_EDGE_TYPES = "|".join(LAYER_EDGES)   # 辺の型の「どれか」（[e:over|peer|…]）
_BY_ID = "MATCH (n) WHERE id(n) = $id"
_LINK = "MATCH (a:device)-[l:link]->(b:device) WHERE id(a) = $a AND id(b) = $b AND l.a_if = $a_if"


def _if_id(device_id: str, if_name: str) -> str:
    return f"{device_id}#{if_name}"


def _nodes(label: str, where: str = "") -> list:
    """label の頂点の一覧（_node の形）"""
    return [_node(r["n"]) for r in query(f"MATCH (n:{_ident(label)}){' WHERE ' + where if where else ''} RETURN n")]


def _links() -> list:
    """回線の辺の一覧（property に a / b = 両端の機器の id を足した dict）"""
    return [{**(r["l"].get("~properties") or {}), "a": r["a"], "b": r["b"]}
            for r in query("MATCH (a)-[l:link]->(b) RETURN id(a) AS a, id(b) AS b, l")]


def load_topology() -> tuple[list[dict], list[dict]]:
    """(devices, links)。topology.py が data/ の代わりに使う形（devices は asn を含む）。
    devices の各行には interfaces（[{name, address, status, registered}]）と registered（未登録の頂点なら False）が付く"""
    devices = {}
    for m in _nodes("device"):
        d = {k: m.get(k) for k in DEVICE_KEYS}
        d["device_id"] = m.get("id")
        d["enabled"] = bool(d.get("enabled"))
        d["maintenance"] = bool(d.get("maintenance"))
        d["registered"] = m.get("registered") is not False
        d["interfaces"] = []
        devices[d["device_id"]] = d
    for m in _nodes("interface"):
        d = devices.get(m.get("device_id"))
        if d is not None:
            d["interfaces"].append({"name": m.get("name"), "address": m.get("address"), "lag": m.get("lag") or "",
                                    "status": m.get("status"), "registered": m.get("registered") is not False})
    links = []
    for m in _links():
        l = {k: m.get(k) for k in LINK_KEYS}
        l["a"], l["b"] = m["a"], m["b"]
        links.append(l)
    for d in devices.values():
        d["interfaces"].sort(key=lambda i: str(i["name"] or ""))
    links.sort(key=lambda l: (l["a"], l["b"], l["a_if"] or ""))
    return sorted(devices.values(), key=lambda d: d["device_id"]), links


def _count(cypher: str) -> int:
    return int(query(cypher)[0]["n"])


def count() -> dict:
    """登録済みの機器・インタフェース・回線の数、上の層の頂点と辺の数、未登録の頂点の数（ops/seed_graph.py は devices が 0 なら空とみなす）"""
    return {"devices": _count("MATCH (n:device) WHERE n.registered IS NULL RETURN count(n) AS n"),
            "interfaces": _count("MATCH (n:interface) WHERE n.registered IS NULL RETURN count(n) AS n"),
            "links": _count("MATCH ()-[l:link]->() RETURN count(l) AS n"),
            "layers": sum(_count(f"MATCH (n:{_ident(x)}) WHERE n.registered IS NULL RETURN count(n) AS n") for x in LAYER_LABELS),
            "layer_edges": _count(f"MATCH ()-[e:{_LAYER_EDGE_TYPES}]->() RETURN count(e) AS n"),
            "unregistered": _count("MATCH (n) WHERE n.registered = false RETURN count(n) AS n")}


def load_layers() -> dict:
    """上の層の頂点と辺（lab_topology.py の layers と同じ形。頂点には status と registered が付く）"""
    vertices = []
    for label in LAYER_LABELS:
        for m in _nodes(label):
            v = {k: x for k, x in m.items() if k != "registered"}
            v["registered"] = m.get("registered") is not False
            vertices.append(v)
    edges = [{"label": r["label"], "from": r["from"], "to": r["to"]}
             for r in query(f"MATCH (a)-[e:{_LAYER_EDGE_TYPES}]->(b) RETURN type(e) AS label, id(a) AS from, id(b) AS to")]
    vertices.sort(key=lambda v: str(v.get("id")))
    edges.sort(key=lambda e: (e["label"], str(e["from"]), str(e["to"])))
    return {"vertices": vertices, "edges": edges}


def _create(label: str, rows: list) -> None:
    """頂点をまとめて作る。rows は [{id, props}]。id は自分で決める（`~id`）"""
    for part in _chunks(rows):
        query(f"UNWIND $rows AS r CREATE (n:{_ident(label)} {{`~id`: r.id}}) SET n += r.props", rows=part)


def _if_rows(device_id: str, interfaces) -> list:
    return [{"id": _if_id(device_id, i["name"]),
             "props": _props({"device_id": device_id, "name": i["name"], "address": i.get("address"), "lag": i.get("lag") or ""}, IF_KEYS[:-1])}
            for i in interfaces or [] if i.get("name")]


def _link_row(l: dict) -> dict:
    """回線 1 本を {a, b, props}（a < b に揃える）に"""
    a, a_if, b, b_if = l["a"], l["a_if"], l["b"], l["b_if"]
    if a > b:
        a, a_if, b, b_if = b, b_if, a, a_if
    props = {"a_if": a_if, "b_if": b_if, "kind": l.get("kind") or "l2", "role": l.get("role") or "",
             "bandwidth_mbps": int(l["bandwidth_mbps"]) if l.get("bandwidth_mbps") else None, "status": l.get("status")}
    return {"a": a, "b": b, "props": _props(props, LINK_KEYS)}


def _create_links(rows: list) -> None:
    for part in _chunks(rows):
        query("UNWIND $rows AS r MATCH (a:device), (b:device) WHERE id(a) = r.a AND id(b) = r.b "
              "CREATE (a)-[l:link]->(b) SET l += r.props", rows=part)


def seed(devices: list[dict], links: list[dict], layers: dict | None = None) -> dict:
    """静的データで置き換える（登録済みを全部消してから入れる）。devices は lab/lab_topology.py が lab の定義から作ったもの
    （interfaces 付き）、または devices.yaml の行に topology.json の asn を足したもの（interfaces 無し）。layers は同じく lab_topology.py の
    layers（または data/layers.json）で、None なら上の層は触らない。
    status は入れない（入れ直したら全部 UP に戻る）。ただし未登録の頂点のうち今回登録されるものは置き換え、UP でない status を引き継ぐ。
    登録されないままの未登録の頂点は残す（登録漏れの印を入れ直しで消さない）。
    機器が増えても往復が増えないよう、頂点と辺は UNWIND でまとめて作る（CHUNK 行ずつ）。両端の機器が一覧に無い回線、
    両端が同じ機器の回線、同じ「両端 + a_if」の 2 本目は入れない"""
    dev_ids = {d["device_id"] for d in devices}
    if_ids = {_if_id(d["device_id"], i["name"]) for d in devices for i in d.get("interfaces") or [] if i.get("name")}
    carry, replaced = [], []
    for r in query("MATCH (n) WHERE n.registered = false RETURN n"):
        m = _node(r["n"])
        vid = m.get("id")
        if vid in dev_ids and m.get("label") == "device":
            replaced.append(vid)
            carry.append((vid, "", m.get("status")))
        elif vid in if_ids and m.get("label") == "interface":
            replaced.append(vid)
            carry.append((m.get("device_id"), m.get("name"), m.get("status")))
    for label in ("device", "interface"):
        query(f"MATCH (n:{label}) WHERE n.registered IS NULL DETACH DELETE n")
    if replaced:
        query("MATCH (n) WHERE id(n) IN $ids DETACH DELETE n", ids=replaced)
    _create("device", [{"id": d["device_id"], "props": _props(d, DEVICE_KEYS[:-1])} for d in devices])
    _create("interface", [r for d in devices for r in _if_rows(d["device_id"], d.get("interfaces"))])
    rows, seen = [], set()
    for l in links:
        row = _link_row({**l, "status": None})
        key = (row["a"], row["b"], row["props"].get("a_if"))
        if row["a"] != row["b"] and {row["a"], row["b"]} <= dev_ids and key not in seen:
            seen.add(key)
            rows.append(row)
    _create_links(rows)
    for dev, ifn, st in carry:
        if st and st != "UP":
            set_status(dev, ifn, st)
    out = count()
    if layers is not None:
        out.update(seed_layers(layers, if_ids))
    return out


def seed_layers(layers: dict, known_ids: set | None = None) -> dict:
    """上の層（ip_interface / isis_adjacency / bgp_session / evpn_instance / ethernet_segment と、その辺）を置き換える。
    seed() と同じく、未登録の頂点のうち今回登録されるものは UP でない status を引き継ぐ。辺は両端の頂点があるときだけ張り、
    無いものは skipped_edges に数える（known_ids は物理層の頂点の id。None なら Neptune のインタフェースの id を読む）"""
    vertices, edges = layers.get("vertices") or [], layers.get("edges") or []
    ids = {v["id"] for v in vertices if v.get("id")}
    if known_ids is None:
        known_ids = {r["id"] for r in query("MATCH (n:interface) RETURN id(n) AS id")}
    carry = {}
    for label in LAYER_LABELS:
        for m in _nodes(label, "n.registered = false"):
            if m.get("id") in ids:
                carry[m["id"]] = m.get("status")
    if carry:
        query("MATCH (n) WHERE id(n) IN $ids DETACH DELETE n", ids=sorted(carry))
    for label in LAYER_LABELS:
        query(f"MATCH (n:{label}) WHERE n.registered IS NULL DETACH DELETE n")
    by_label = {}
    for v in vertices:
        if not v.get("id") or v.get("label") not in LAYER_LABELS:
            continue
        keys = [k for k in v if k not in ("id", "label", "status", "registered")]
        st = carry.get(v["id"])
        props = {**_props(v, keys), **({"status": st} if st and st != "UP" else {})}
        by_label.setdefault(v["label"], []).append({"id": v["id"], "props": props})
    for label, rows in by_label.items():
        _create(label, rows)
    skipped, by_type = 0, {}
    for e in edges:
        if e.get("label") not in LAYER_EDGES or not ({e.get("from"), e.get("to")} <= (ids | known_ids)):
            skipped += 1
            continue
        by_type.setdefault(e["label"], []).append({"f": e["from"], "t": e["to"]})
    for label, rows in by_type.items():
        for part in _chunks(rows):
            query(f"UNWIND $rows AS r MATCH (a), (b) WHERE id(a) = r.f AND id(b) = r.t CREATE (a)-[:{_ident(label)}]->(b)", rows=part)
    out = {k: v for k, v in count().items() if k in ("layers", "layer_edges", "unregistered")}
    if skipped:
        out["skipped_edges"] = skipped
    return out


def _diff_props(cur: dict, new: dict, keys) -> tuple[dict, list]:
    """cur（いまの property）を new に合わせる差分。(上書きする値の dict, 消す property の名前の list)。
    無くなった値（None / ""）は property ごと消す。同じならどちらも空"""
    put, drop = {}, []
    for k in keys:
        v = new.get(k)
        if v is None or v == "":
            if cur.get(k) is not None:
                drop.append(k)
        elif cur.get(k) != v:
            put[k] = v
    return put, drop


def _apply_diff(match: str, var: str, put: dict, drop: list, **params) -> None:
    """match で引いた要素（var）に差分を書く（SET と REMOVE を 1 本のクエリで）"""
    q = match + (f" SET {var} += $put" if put else "") + (" REMOVE " + ", ".join(f"{var}.{_ident(k)}" for k in drop) if drop else "")
    query(q, **params, **({"put": put} if put else {}))


def sync_physical(devices: list[dict], links: list[dict]) -> dict:
    """物理層（機器・インタフェース・回線）を、渡した一覧に差分で合わせる。Nautobot の Job（nautobot/jobs）が、Nautobot を変えるたびに呼ぶ。
    devices / links の形は seed() と同じ。seed() と違って消して入れ直さないので、残る頂点と辺の status（アラートが付けた動的な状態）と、
    上の層（IP 層 / EVPN・BGP 層。lab の定義から seed_layers() で入れたもの）の頂点と辺はそのまま残る。
      - 一覧に無い登録済みの機器・インタフェース・回線は消す（機器を消すと付いていた辺も消える）
      - 一覧にあって Neptune に無いものは足す。未登録の頂点（検知が先に来たもの）は置き換え、UP でない status を引き継ぐ（seed() と同じ）
      - どちらにもあるものは、変わった property だけ上書きし、無くなった値は property ごと消す
    一覧に入らない未登録の頂点は残す。戻り値は count() に added / updated / removed（書いた要素の数）を足したもの。
    回線の端の機器が一覧に無いなどで張れなかった回線は skipped に理由を並べる"""
    want = {"device": {d["device_id"]: d for d in devices}, "interface": {}}
    for d in devices:
        for i in d.get("interfaces") or []:
            if i.get("name"):
                want["interface"][_if_id(d["device_id"], i["name"])] = {
                    "device_id": d["device_id"], "name": i["name"], "address": i.get("address"), "lag": i.get("lag") or ""}
    want_links = {}
    for l in links:
        row = _link_row({**l, "status": None})
        want_links[(row["a"], row["b"], row["props"].get("a_if"))] = {
            "b_if": row["props"].get("b_if"), "kind": l.get("kind") or "l2", "role": l.get("role") or "",
            "bandwidth_mbps": int(l["bandwidth_mbps"]) if l.get("bandwidth_mbps") else None}
    current = {m.get("id"): m for label in ("device", "interface") for m in _nodes(label)}
    edges = _links()
    stats = {"added": 0, "updated": 0, "removed": 0}
    gone, carry = set(), []   # gone = 消した / 作り直した機器（付いていた辺も一緒に消えている）
    for label, keys in (("device", DEVICE_KEYS[:-1]), ("interface", IF_KEYS[:-1])):
        for vid, m in current.items():
            if m.get("label") == label and vid not in want[label] and m.get("registered") is not False:
                query(f"{_BY_ID} DETACH DELETE n", id=vid)
                stats["removed"] += 1
                if label == "device":
                    gone.add(vid)
        for vid, w in want[label].items():
            m = current.get(vid)
            if m is not None and m.get("label") == label and m.get("registered") is not False:
                put, drop = _diff_props(m, w, keys)
                if put or drop:
                    _apply_diff(_BY_ID, "n", put, drop, id=vid)
                    stats["updated"] += 1
                continue
            if m is not None:   # 未登録の頂点は置き換える
                query(f"{_BY_ID} DETACH DELETE n", id=vid)
                carry.append((vid, "", m.get("status")) if label == "device" else (w["device_id"], w["name"], m.get("status")))
                if label == "device":
                    gone.add(vid)
            _create(label, [{"id": vid, "props": _props(w, keys)}])
            stats["added"] += 1
    seen = {}
    for m in edges:   # 同じ「両端 + a_if」の辺をまとめる（2 本以上あれば重複）
        if m["a"] not in gone and m["b"] not in gone:
            seen.setdefault((m["a"], m["b"], m.get("a_if")), []).append(m)
    kept = set()
    for key, ms in seen.items():
        a, b, a_if = key
        if key not in want_links:   # 一覧に無い回線
            query(f"{_LINK} DELETE l", a=a, b=b, a_if=a_if)
            stats["removed"] += len(ms)
            continue
        if len(ms) > 1:   # 同じ回線が 2 本以上。辺を id で 1 本だけ消せないので、全部消して 1 本を作り直す（status は 1 本目のものを引き継ぐ）
            query(f"{_LINK} DELETE l", a=a, b=b, a_if=a_if)
            _create_links([{"a": a, "b": b, "props": _props({**want_links[key], "a_if": a_if, "status": ms[0].get("status")}, LINK_KEYS)}])
            stats["removed"] += len(ms) - 1
            kept.add(key)
            continue
        kept.add(key)
        put, drop = _diff_props(ms[0], want_links[key], LINK_KEYS[1:-1])
        if put or drop:
            _apply_diff(_LINK, "l", put, drop, a=a, b=b, a_if=a_if)
            stats["updated"] += 1
    skipped = []
    for (a, b, a_if), w in want_links.items():
        if (a, b, a_if) in kept:
            continue
        r = add_link(a, a_if, b, w["b_if"], w["kind"], w["role"], w["bandwidth_mbps"])
        if "error" in r:
            skipped.append(r["error"])
        else:
            stats["added"] += 1
    for dev, ifn, st in carry:
        if st and st != "UP":
            set_status(dev, ifn, st)
    return {**count(), **stats, **({"skipped": skipped} if skipped else {})}


def sync_changes(changes: list[dict]) -> dict:
    """Nautobot の変更履歴（直近の何件か。nb_map.change_rows の形）を label change の頂点に写す。エージェントが「直前に何を変えたか」を
    Nautobot に届かなくても読めるようにするため（Runtime と tools Lambda から Nautobot への経路と権限を足さない）。
    一覧に無い古い頂点は消す。変更履歴は書き換わらないので、もうある id は触らない"""
    have = {r["id"] for r in query("MATCH (n:change) RETURN id(n) AS id")}
    want = {c["change_id"]: c for c in changes if c.get("change_id")}
    _create("change", [{"id": vid, "props": _props(want[vid], CHANGE_KEYS)} for vid in sorted(set(want) - have)])
    old = sorted(have - set(want))
    if old:
        query("MATCH (n:change) WHERE id(n) IN $ids DETACH DELETE n", ids=old)
    return {"added": len(set(want) - have), "removed": len(old), "kept": len(want)}


def _registered(vid: str) -> list:
    """[] = 無い、[True] = 登録済み、[False] = 未登録の頂点"""
    return [r["registered"] is not False for r in query(f"{_BY_ID} RETURN n.registered AS registered", id=vid)]


def add_device(device_id: str, site: str, role: str, mgmt_ip: str = "", asn=None, enabled: bool = False) -> dict:
    """機器を足す。未登録の頂点（検知が先に来たもの）があれば置き換え、その status を引き継ぐ"""
    reg = _registered(device_id)
    if reg and reg[0] is not False:
        return {"error": f"{device_id} はもうある"}
    status = None
    if reg:
        status = (query(f"{_BY_ID} RETURN n.status AS status", id=device_id) or [{}])[0].get("status")
        query(f"{_BY_ID} DETACH DELETE n", id=device_id)
    d = {"hostname": device_id, "site": site, "role": role, "mgmt_ip": mgmt_ip, "asn": asn, "enabled": enabled, "status": status}
    _create("device", [{"id": device_id, "props": _props(d, DEVICE_KEYS)}])
    return {"added": device_id, **({"replaced_unregistered": True} if reg else {})}


def remove_device(device_id: str) -> dict:
    if not _registered(device_id):
        return {"error": f"{device_id} は無い"}
    query(f"{_BY_ID} DETACH DELETE n", id=device_id)  # つながる辺も消える
    query("MATCH (n:interface) WHERE n.device_id = $id DETACH DELETE n", id=device_id)  # インタフェースは辺でつないでいないので別に消す
    return {"removed": device_id}


def add_link(a: str, a_if: str, b: str, b_if: str, kind: str = "l2", role: str = "", bandwidth_mbps=None) -> dict:
    if a == b:
        return {"error": "両端が同じ機器"}
    row = _link_row({"a": a, "a_if": a_if, "b": b, "b_if": b_if, "kind": kind, "role": role, "bandwidth_mbps": bandwidth_mbps})
    a, b, a_if, b_if = row["a"], row["b"], row["props"].get("a_if"), row["props"].get("b_if")
    have = {r["id"] for r in query("MATCH (n) WHERE id(n) IN $ids RETURN id(n) AS id", ids=[a, b])}
    missing = [x for x in (a, b) if x not in have]
    if missing:
        return {"error": f"機器が無い: {', '.join(missing)}"}
    if _count_p(f"{_LINK} RETURN count(l) AS n", a=a, b=b, a_if=a_if):
        return {"error": f"{a} {a_if} - {b} のリンクはもうある"}
    query("MATCH (a), (b) WHERE id(a) = $a AND id(b) = $b CREATE (a)-[l:link]->(b) SET l += $props", a=a, b=b, props=row["props"])
    return {"added": f"{a} {a_if} - {b} {b_if}"}


def _count_p(cypher: str, **params) -> int:
    return int(query(cypher, **params)[0]["n"])


def remove_link(a: str, b: str, a_if: str = "") -> dict:
    if a > b:
        a, b = b, a
    match = "MATCH (a)-[l:link]->(b) WHERE id(a) = $a AND id(b) = $b" + (" AND l.a_if = $a_if" if a_if else "")
    params = {"a": a, "b": b, **({"a_if": a_if} if a_if else {})}
    n = _count_p(f"{match} RETURN count(l) AS n", **params)
    if not n:
        return {"error": f"{a} - {b} のリンクは無い"}
    query(f"{match} DELETE l", **params)
    return {"removed": n}


def _upsert_unregistered(vid: str, label: str, props: dict) -> None:
    """未登録の頂点を作る（あれば何もしない）。Lambda が同時に 2 つ動いても同じ id を 2 回作らないよう MERGE で"""
    query(f"MERGE (n:{_ident(label)} {{`~id`: $id}}) ON CREATE SET n += $props, n.registered = false", id=vid, props=_props(props, props))


def _set_vertex_status(vid: str, status: str, label: str = "", only_if: str = "") -> list:
    """頂点の status を書き、書いた頂点ごとに「登録済みか」を返す（[] = 頂点が無い / 条件に合わない）。
    条件と書き込みが 1 本のクエリなので、読んでから書くあいだに別の書き手が割り込まない"""
    match = f"MATCH (n{':' + _ident(label) if label else ''}) WHERE id(n) = $id" + (" AND n.status = $only" if only_if else "")
    params = {"id": vid, "st": status, **({"only": only_if} if only_if else {})}
    return [r["registered"] is not False for r in query(f"{match} SET n.status = $st RETURN n.registered AS registered", **params)]


def set_status(device_id: str, if_name: str = "", status: str = "DOWN", only_if: str = "") -> dict:
    """動的な状態を書く。if_name があればその機器のそのインタフェースが付く辺（a 側でも b 側でも）とインタフェースの頂点、無ければ機器の頂点。
    戻り値の updated は書いた要素の数。トポロジに無ければ未登録の頂点を作って unregistered: True を返す（UP に戻すだけのときは作らない）。
    未登録の頂点に書いたときも unregistered: True。
    only_if（機器の頂点だけ）を渡すと、今の status がそれのときだけ書く（trap の解消で ALARM を UP に戻すとき、
    IF の分からない linkDown が付けた DOWN まで UP に上書きしないため）。合わなければ updated: 0 で何も作らない"""
    status = str(status).upper()
    if status not in STATUSES:
        return {"error": f"status は {' / '.join(STATUSES)} のどれか"}
    if if_name:
        n = _count_p("MATCH (a)-[l:link]->(b) WHERE (id(a) = $dev AND l.a_if = $ifn) OR (id(b) = $dev AND l.b_if = $ifn) "
                     "SET l.status = $st RETURN count(l) AS n", dev=device_id, ifn=if_name, st=status)
        reg = _set_vertex_status(_if_id(device_id, if_name), status)
        out = {"device_id": device_id, "if_name": if_name, "status": status, "updated": int(n) + len(reg)}
        if not n and not reg and status != "UP":
            _upsert_unregistered(device_id, "device", {"hostname": device_id, "site": "?", "role": "unknown", "enabled": False})
            _upsert_unregistered(_if_id(device_id, if_name), "interface", {"device_id": device_id, "name": if_name})
            _set_vertex_status(_if_id(device_id, if_name), status)
            out["unregistered"] = True
        elif any(r is False for r in reg):
            out["unregistered"] = True
        return out
    reg = _set_vertex_status(device_id, status, only_if=str(only_if).upper() if only_if else "")
    out = {"device_id": device_id, "status": status, "updated": len(reg)}
    if not reg and status != "UP" and not only_if:
        _upsert_unregistered(device_id, "device", {"hostname": device_id, "site": "?", "role": "unknown", "enabled": False})
        _set_vertex_status(device_id, status)
        out["unregistered"] = True
    elif any(r is False for r in reg):
        out["unregistered"] = True
    return out


def set_layer_status(device_id: str, kind: str, target: str, status: str = "DOWN") -> dict:
    """上の層の動的な状態を書く。kind は bgp（target = 相手の IP）か isis（target = サブインタフェース ethernet-1/1.0）で、
    頂点の id は <機器>#<kind>#<target>。無ければ（トポロジに無いセッション）未登録の頂点を作って unregistered: True を返す
    （UP に戻すだけのときは作らない）"""
    status = str(status).upper()
    if status not in STATUSES:
        return {"error": f"status は {' / '.join(STATUSES)} のどれか"}
    label = LAYER_KIND.get(str(kind))
    if not label:
        return {"error": f"kind は {' / '.join(LAYER_KIND)} のどれか"}
    vid = f"{device_id}#{kind}#{target}"
    reg = _set_vertex_status(vid, status, label=label)
    out = {"device_id": device_id, "kind": kind, "target": target, "status": status, "updated": len(reg)}
    if not reg and status != "UP":
        props = {"device_id": device_id, "layer": "evpn" if kind == "bgp" else "ip",
                 ("peer_address" if kind == "bgp" else "name"): target}
        _upsert_unregistered(vid, label, props)
        _set_vertex_status(vid, status)
        out["unregistered"] = True
    elif any(r is False for r in reg):
        out["unregistered"] = True
    return out


# ---------------------------------------------------------------- アルゴリズム（Neptune Analytics に固有。neptune.algo.*）
# グラフを取り出さずに、Neptune Analytics の中で回す。機器が増えても、エージェントやワーカーが全部の頂点と辺を読んで手元で数えなくて済む。
# 見るのは物理層の構造（機器と回線）だけで、status は見ない（DOWN の回線も「つながっている」と数える。障害の影響は topology.py の what_if）。
# OSS のグラフ DB に移すときは同じ名前のプロシージャが無いので、ここを置き換える（docs/oss-variant.md）
_ALGO = '{edgeLabels: ["link"], vertexLabel: "device"'   # 設定の先頭（回線の辺と機器の頂点だけを見る）。続きを足して } で閉じる


def centrality(limit: int = 10) -> dict:
    """機器の中心性と、つながりの島。
      degree     付いている回線の数（次数中心性。neptune.algo.degree）
      closeness  ほかの全機器への近さ（近接中心性。neptune.algo.closenessCentrality。大きいほど中心にあり、落ちたときに影響が広い）
      component  回線でつながっている島の番号（弱連結成分。neptune.algo.wcc）。components が 2 以上なら、どの回線でも届かない機器の組がある
    devices は closeness の大きい順に limit 件"""
    rows = {}
    for r in query('MATCH (n:device) CALL neptune.algo.degree(n, {edgeLabels: ["link"], vertexLabels: ["device"], traversalDirection: "both"}) '
                   "YIELD degree RETURN id(n) AS id, degree"):
        rows.setdefault(r["id"], {"device_id": r["id"]})["degree"] = r["degree"]
    for r in query("MATCH (n:device) CALL neptune.algo.closenessCentrality(n, " + _ALGO + ', traversalDirection: "both", numSources: 8192}) '
                   "YIELD score RETURN id(n) AS id, score"):
        rows.setdefault(r["id"], {"device_id": r["id"]})["closeness"] = round(float(r["score"]), 4)
    for r in query("MATCH (n:device) CALL neptune.algo.wcc(n, " + _ALGO + "}) YIELD component RETURN id(n) AS id, component"):
        rows.setdefault(r["id"], {"device_id": r["id"]})["component"] = r["component"]
    names = {c: i + 1 for i, c in enumerate(sorted({d.get("component") for d in rows.values() if d.get("component") is not None}))}
    for d in rows.values():   # 島の番号は内部の大きな数なので、1 からの連番に直す
        if d.get("component") is not None:
            d["component"] = names[d["component"]]
    ordered = sorted(rows.values(), key=lambda d: (-(d.get("closeness") or 0), -(d.get("degree") or 0), d["device_id"]))
    return {"devices": ordered[:max(1, int(limit))], "device_count": len(rows), "components": len(names)}


# ---------------------------------------------------------------- 修復案の頂点（2026-09-24 に DynamoDB から移した）
# 修復案（label proposal）は terraform/workflow のワーカー（workflow/awsio.py）が書く。トポロジの頂点とは辺でつながず、device_id で引く。
# ここは読むのと、承認タブの decide だけ（agent/proposals.py）。異常（label anomaly）の頂点は 2026-10-02 にやめた（書いていた Spark の detect をなくした）
def _record(m: dict, id_key: str) -> dict:
    """_node() の 1 件を、id を id_key（proposal_id / change_id）に置き換えた dict に"""
    d = {k: v for k, v in m.items() if k not in ("id", "label")}
    d[id_key] = m.get("id")
    return d


def list_records(label: str, id_key: str, order_by: str, status: str = "", device_id: str = "", limit: int = 20) -> list:
    """label の頂点を order_by（epoch 秒）の新しい順に limit 件。status / device_id があればその値だけ（絞ってから数える）"""
    cond = [c for c, v in (("n.status = $status", status), ("n.device_id = $device_id", device_id)) if v]
    params = {k: v for k, v in (("status", status), ("device_id", device_id)) if v}
    q = (f"MATCH (n:{_ident(label)})" + (" WHERE " + " AND ".join(cond) if cond else "")
         + f" RETURN n ORDER BY n.{_ident(order_by)} DESC LIMIT {int(limit)}")
    return [_record(_node(r["n"]), id_key) for r in query(q, **params)]


def get_record(label: str, id_key: str, vid: str) -> dict:
    rows = query(f"MATCH (n:{_ident(label)}) WHERE id(n) = $id RETURN n", id=vid)
    return _record(_node(rows[0]["n"]), id_key) if rows else {}


def update_record(label: str, vid: str, fields: dict, only_status: str = "") -> bool:
    """頂点の fields を書き換える。only_status なら今の status がそれのときだけ。書けたら True。
    条件と書き込みが 1 本のクエリなので、読んでから書くあいだに別の書き手が割り込まない"""
    match = f"MATCH (n:{_ident(label)}) WHERE id(n) = $id" + (" AND n.status = $only" if only_status else "")
    params = {"id": vid, "fields": _props(fields, fields), **({"only": only_status} if only_status else {})}
    return bool(query(f"{match} SET n += $fields RETURN id(n) AS id", **params))
