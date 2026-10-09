"""トポロジをエージェントのツールとして出す。

元データは 2 通り。Neptune（IaC/terraform/aws-managed/pipeline/graph。graph.configured() が真）があればそこから読み、無ければ
静的データ（data/devices.yaml と data/topology.json と data/layers.json。tools Lambda では devices.json）。どちらも中身はローカル lab
（app/containerlab/splab.clab.yml。Spine-Leaf の SR Linux 6 台と TRex 1 台）そのもので、すべて架空のアドレス。SNMP や lab には触らない。読み取りだけなので、モデルが何度呼んでも副作用は無い。
Neptune のときは TTL 秒ごとに読み直す（画面で編集した結果が次の質問に効く）。

物理層（機器と回線）のほかに、IP 層（ip_interface / isis_adjacency）と EVPN・BGP 層（bgp_session / evpn_instance / ethernet_segment）を
layers ツールで出す（頂点の id と、下の層を指す interface_id / ip_interface_id で層をまたいで追える。app/agentcore/graph.py の docstring）。

Converse の toolConfig に渡す仕様（TOOL_SPECS）と、toolUse を受けて実行する run_tool() を持つ。
"""

import json
import logging
import os
import time
from collections import deque

from botocore.exceptions import BotoCoreError, ClientError   # except は graph.errors() で受ける（tests/test_agentcore.py がこの名前を使う）

import graph
import toolkit

DATA_DIR = os.environ.get("TOPOLOGY_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
# 段の順（上から）。Web の図の段もこれ（a-leaf と s-leaf は図では同じ段）。unknown は検知が先に来た未登録の機器（graph.set_status が作る）
ROLE_ORDER = ["spine", "a-leaf", "s-leaf", "trex", "unknown"]
LAYER_NAMES = ("ip", "evpn")
MAX_HOPS = 6
TTL = int(os.environ.get("TOPOLOGY_TTL", "60"))
log = logging.getLogger("topology")


def load_static() -> tuple[list[dict], list[dict]]:
    """data/ の静的データ。devices の各行に topology.json の asn を足して返す（Neptune の seed にも使う）"""
    # tools Lambda（IaC/terraform/aws-managed/workflow）には PyYAML が無いので、Terraform が JSON にした devices.json を先に見る
    devices_json = os.path.join(DATA_DIR, "devices.json")
    if os.path.exists(devices_json):
        with open(devices_json, encoding="utf-8") as f:
            devices = json.load(f)["devices"]
    else:
        import yaml  # noqa: PLC0415 - Runtime イメージだけが持つ

        with open(os.path.join(DATA_DIR, "devices.yaml"), encoding="utf-8") as f:
            devices = yaml.safe_load(f)["devices"]
    with open(os.path.join(DATA_DIR, "topology.json"), encoding="utf-8") as f:
        topo = json.load(f)
    asn = {n["device_id"]: n.get("asn") for n in topo["nodes"]}
    return [{**d, "asn": asn.get(d["device_id"])} for d in devices], topo["links"]


def load_static_layers() -> dict:
    """data/layers.json（app/containerlab/lab_topology.py --layers の出力）。無ければ空"""
    path = os.path.join(DATA_DIR, "layers.json")
    if not os.path.exists(path):
        return {"vertices": [], "edges": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _build(devices: list[dict], links: list[dict]):
    # links は a < b で正規化してあるが、探索は無向で見る
    adj: dict[str, list[dict]] = {d["device_id"]: [] for d in devices}
    for l in links:
        for me, peer, my_if, peer_if in ((l["a"], l["b"], l["a_if"], l["b_if"]), (l["b"], l["a"], l["b_if"], l["a_if"])):
            adj.setdefault(me, []).append({
                "device_id": peer, "local_if": my_if, "remote_if": peer_if,
                "kind": l["kind"], "role": l.get("role") or "", "bandwidth_mbps": l.get("bandwidth_mbps"),
                "status": l.get("status") or "UP",   # Neptune の辺の動的な状態（graph.set_status）。静的データには無いので UP
            })
    return devices, {d["device_id"]: d for d in devices}, links, adj


DEVICES: list[dict] = []
NODES: dict[str, dict] = {}
LINKS: list[dict] = []
ADJ: dict[str, list[dict]] = {}
DEVICE_BY_ID: dict[str, dict] = {}
LAYERS: dict = {"vertices": [], "edges": []}
SOURCE = "static"
_loaded_at = 0.0


def reload(force: bool = False) -> str:
    """グラフ DB（Neptune。OSS 版は Neo4j）があればそこから、無ければ静的データから組み直す。
    戻り値は使った元（graph.BACKEND の neptune / neo4j、空なら neptune-empty / neo4j-empty、無ければ static）。root_cause などの source にも出る"""
    global DEVICES, NODES, LINKS, ADJ, DEVICE_BY_ID, LAYERS, SOURCE, _loaded_at
    if not force and _loaded_at and (SOURCE == "static" or time.time() - _loaded_at < TTL):
        return SOURCE
    source, devices, links, layers = "static", None, None, None
    if graph.configured():
        try:
            devices, links = graph.load_topology()
            source = graph.BACKEND if devices else f"{graph.BACKEND}-empty"  # 空なら静的データを見せる（画面の「投入」で入れる）
            if devices:
                layers = graph.load_layers()
        except (*graph.errors(), KeyError, ValueError, TypeError) as e:
            log.warning("%s read failed, using static data: %s", graph.BACKEND, str(e)[:200])   # OSS 版は neo4j と出す
            devices = None
    if not devices:
        devices, links = load_static()
        layers = load_static_layers()
    SOURCE = source
    DEVICES, NODES, LINKS, ADJ = _build(devices, links)
    DEVICE_BY_ID = NODES
    LAYERS = layers or {"vertices": [], "edges": []}
    _loaded_at = time.time()
    return SOURCE


reload(force=True)


def _public(d: dict) -> dict:
    """SNMP のコミュニティなど、答えに出す必要のない項目を落とす"""
    return {
        "device_id": d["device_id"], "site": d["site"], "role": d["role"], "mgmt_ip": d.get("mgmt_ip"),
        "asn": d.get("asn"), "monitored": bool(d.get("enabled")), "status": d.get("status") or "UP",
        "registered": d.get("registered") is not False, "maintenance": bool(d.get("maintenance")),
    }


def list_devices(site: str = "", role: str = "") -> dict:
    rows = [_public(d) for d in DEVICES if (not site or d["site"] == site) and (not role or d["role"] == role)]
    rows.sort(key=lambda r: (ROLE_ORDER.index(r["role"]) if r["role"] in ROLE_ORDER else 99, r["device_id"]))
    return {"count": len(rows), "devices": rows}


def neighbors(device_id: str) -> dict:
    if device_id not in DEVICE_BY_ID:
        return {"error": f"{device_id} はトポロジに無い", "known": sorted(DEVICE_BY_ID)}
    return {"device_id": device_id, "neighbors": ADJ.get(device_id, [])}


def blast_radius(device_id: str, max_hops: int = 2) -> dict:
    """device_id が落ちたとき、そこから max_hops 以内にある機器。上流を経由しないと届かない拠点の目安"""
    if device_id not in DEVICE_BY_ID:
        return {"error": f"{device_id} はトポロジに無い", "known": sorted(DEVICE_BY_ID)}
    max_hops = max(1, min(int(max_hops), MAX_HOPS))
    seen = {device_id: 0}
    q = deque([device_id])
    while q:
        cur = q.popleft()
        if seen[cur] >= max_hops:
            continue
        for nb in ADJ.get(cur, []):
            if nb["device_id"] not in seen:
                seen[nb["device_id"]] = seen[cur] + 1
                q.append(nb["device_id"])
    affected = [{"device_id": k, "hops": v, "site": DEVICE_BY_ID[k]["site"], "role": DEVICE_BY_ID[k]["role"],
                 "status": DEVICE_BY_ID[k].get("status") or "UP"}
                for k, v in seen.items() if k != device_id]
    affected.sort(key=lambda r: (r["hops"], r["device_id"]))
    return {"device_id": device_id, "max_hops": max_hops, "affected": affected}


def topology_graph() -> dict:
    return {"source": SOURCE, "nodes": [_public(d) for d in DEVICES], "links": LINKS}


def layers(device_id: str = "", layer: str = "") -> dict:
    """物理層より上の頂点と辺。device_id があればその機器のもの（辺は片端がその機器のもの）、layer（ip / evpn）があればその層だけ。
    頂点の status は動的な状態（gNMI の検知。無ければ UP）"""
    if device_id and device_id not in DEVICE_BY_ID:
        return {"error": f"{device_id} はトポロジに無い", "known": sorted(DEVICE_BY_ID)}
    if layer and layer not in LAYER_NAMES:
        return {"error": f"layer は {' / '.join(LAYER_NAMES)} のどれか"}
    rows = [{**v, "status": v.get("status") or "UP"} for v in LAYERS.get("vertices") or []
            if (not device_id or v.get("device_id") == device_id) and (not layer or v.get("layer") == layer)]
    ids = {v["id"] for v in rows}
    edges = [e for e in LAYERS.get("edges") or [] if e.get("from") in ids or e.get("to") in ids]
    return {"device_id": device_id, "layer": layer, "count": len(rows), "vertices": rows, "edges": edges}


# ---------------------------------------------------------------- 根本原因（層をまたいで下へ辿る）
# UP でない要素（機器・回線・インタフェース・上の層の頂点）を集め、それぞれが「乗っている」下の要素へ辿る。
# 下に DOWN の要素があればそれが原因で、無ければその要素自身が根本原因（下の層は生きているので、その層の設定やプロセスを疑う）。
#   上の層の頂点 → 辺 over の先と interface_id / ip_interface_id（サブ IF → IF）
#   bgp_session  → 相手の機器。ループバック同士のセッションなので、fabric（IS-IS の underlay）の UP の回線だけで相手に届かなければ、切れ目の DOWN の回線も
#   interface    → その IF が付く回線と機器。LAG（lag1）ならメンバーの IF
#   回線         → 両端の機器
# 途中の UP の要素は通り抜けて下まで見る。原因として数えるのは DOWN だけ（ALARM は trap が見えた印で、落ちた印ではない。ALARM の要素はそれ自身を根本原因として並べる）
FAULTS = ("DOWN", "ALARM")
PHYSICAL = "physical"
LAYER_ORDER = {PHYSICAL: 0, "ip": 1, "evpn": 2}
MAX_LISTED = 20


def link_id(l: dict) -> str:
    return f'{l["a"]}#{l["a_if"]}--{l["b"]}#{l["b_if"]}'


def _underlay_cut(device_id: str, peer: str) -> list[str]:
    """fabric の UP の回線だけで device_id から peer に届くか。届かなければ、届く範囲の縁にある DOWN の回線と機器の id（届くなら空）"""
    seen, cut, q = {device_id}, [], deque([device_id])
    while q:
        cur = q.popleft()
        for l in LINKS:
            if l.get("kind") != "fabric" or cur not in (l["a"], l["b"]):
                continue
            other = l["b"] if l["a"] == cur else l["a"]
            if (l.get("status") or "UP") == "DOWN":
                cut.append(link_id(l))
            elif ((DEVICE_BY_ID.get(other) or {}).get("status") or "UP") == "DOWN":
                cut.append(other)
            elif other not in seen:
                seen.add(other)
                q.append(other)
    return [] if peer in seen else sorted(set(cut))


def elements() -> dict:
    """id → {id, type, layer, status, devices, deps}。物理層（機器・回線・インタフェース）と上の層の頂点を 1 つの表にする"""
    out = {}
    for d in DEVICES:
        out[d["device_id"]] = {"id": d["device_id"], "type": "device", "layer": PHYSICAL, "status": d.get("status") or "UP",
                               "devices": [d["device_id"]], "deps": [], "registered": d.get("registered") is not False}
    if_links: dict[str, list[str]] = {}
    for l in LINKS:
        lid = link_id(l)
        out[lid] = {"id": lid, "type": "link", "layer": PHYSICAL, "status": l.get("status") or "UP", "devices": [l["a"], l["b"]],
                    "deps": [l["a"], l["b"]], "kind": l.get("kind") or ""}
        for dev, name in ((l["a"], l["a_if"]), (l["b"], l["b_if"])):
            if name:
                if_links.setdefault(f"{dev}#{name}", []).append(lid)
    for d in DEVICES:
        known = {i.get("name"): i for i in d.get("interfaces") or [] if i.get("name")}
        names = set(known) | {i.split("#", 1)[1] for i in if_links if i.split("#", 1)[0] == d["device_id"]}
        for name in names:
            iid = f'{d["device_id"]}#{name}'
            members = [f'{d["device_id"]}#{n}' for n, i in known.items() if i.get("lag") == name]
            out[iid] = {"id": iid, "type": "interface", "layer": PHYSICAL, "status": (known.get(name) or {}).get("status") or "UP",
                        "devices": [d["device_id"]], "deps": if_links.get(iid, []) + members + [d["device_id"]],
                        "registered": (known.get(name) or {}).get("registered") is not False}
    over: dict[str, list[str]] = {}
    for e in LAYERS.get("edges") or []:
        if e.get("label") == "over":
            over.setdefault(e.get("from"), []).append(e.get("to"))
    for v in LAYERS.get("vertices") or []:
        deps = list(over.get(v["id"], [])) + [v.get(k) for k in ("ip_interface_id", "interface_id") if v.get(k)]
        if v.get("label") == "bgp_session" and v.get("peer_device"):
            deps.append(v["peer_device"])
            if (v.get("status") or "UP") != "UP":
                deps += _underlay_cut(v.get("device_id"), v["peer_device"])
        out[v["id"]] = {"id": v["id"], "type": v.get("label") or "", "layer": v.get("layer") or "", "status": v.get("status") or "UP",
                        "devices": [x for x in (v.get("device_id"), v.get("peer_device")) if x], "deps": list(dict.fromkeys(deps)),
                        "registered": v.get("registered") is not False}
    for e in out.values():
        e["deps"] = [x for x in e["deps"] if x in out and x != e["id"]]
    return out


def _below(eid: str, els: dict) -> set:
    """eid が乗っている要素を下まで全部（途中の UP の要素も通る。回線だけ DOWN でインタフェースの頂点は UP のままのことがある）"""
    seen, q = set(), deque([eid])
    while q:
        for x in els[q.popleft()]["deps"]:
            if x not in seen and x != eid:
                seen.add(x)
                q.append(x)
    return seen


def _roots(eid: str, els: dict) -> set:
    """eid の下にある DOWN の要素のうち、そのまた下に DOWN が無いもの。下に DOWN が 1 つも無ければ自分自身"""
    down = {x for x in _below(eid, els) if els[x]["status"] == "DOWN"}
    if not down:
        return {eid}
    return {x for x in down if not any(els[y]["status"] == "DOWN" for y in _below(x, els))}


def _brief(e: dict) -> dict:
    return {"id": e["id"], "type": e["type"], "layer": e["layer"], "status": e["status"]}


def root_cause(device_id: str = "") -> dict:
    """UP でない要素を層をまたいで下へ辿り、根本原因（下の層に DOWN が無い要素）ごとにまとめる。
    explains はその原因で説明できる UP でない要素、also_on_it はまだ UP のままその上に乗っている要素（検知が遅れているだけかもしれない）。
    device_id があれば、その機器に関わる原因だけ"""
    if device_id and device_id not in DEVICE_BY_ID:
        return {"error": f"{device_id} はトポロジに無い", "known": sorted(DEVICE_BY_ID)}
    els = elements()
    faults = [e for e in els.values() if e["status"] in FAULTS]
    explains: dict[str, list[str]] = {}
    for e in faults:
        for r in _roots(e["id"], els):
            explains.setdefault(r, [])
            if r != e["id"]:
                explains[r].append(e["id"])
    above: dict[str, list[str]] = {}
    for e in els.values():
        for x in e["deps"]:
            above.setdefault(x, []).append(e["id"])
    rows = []
    for rid, ids in explains.items():
        root = els[rid]
        on_it, q = [], deque([rid])
        seen = {rid}
        while q:  # この要素の上に乗っているもの（回線の両端の機器のように下へ向かう辺は逆に辿らない）
            for up in above.get(q.popleft(), []):
                if up not in seen:
                    seen.add(up)
                    q.append(up)
                    if els[up]["status"] == "UP" and root["status"] == "DOWN":  # ALARM は落ちた印ではないので、上を巻き込まない
                        on_it.append(up)
        related = set(root["devices"]) | {d for i in ids for d in els[i]["devices"]}
        if device_id and device_id not in related:
            continue
        counts: dict[str, int] = {}
        for i in ids:
            counts[els[i]["type"]] = counts.get(els[i]["type"], 0) + 1
        said = "、".join(f"{k} {n}" for k, n in sorted(counts.items()))
        lower_ok = root["layer"] != PHYSICAL and bool(root["deps"])
        note = f'{rid} が {root["status"]}。' + (f"UP でない {len(ids)} 個（{said}）はこれで説明できる。" if ids else "")
        if lower_ok:
            note += "下の層（IP・回線・機器）は UP なので、この層の設定やプロセスを疑う。"
        if root.get("registered") is False:
            note += "トポロジに未登録の要素（検知だけが来た）。"
        maint = sorted(d for d in root["devices"] if (DEVICE_BY_ID.get(d) or {}).get("maintenance"))
        if maint:
            note += f"保守中の機器（{', '.join(maint)}）に関わるので、作業による停止かもしれない。"
        rows.append({**_brief(root), "devices": root["devices"], "lower_layers_up": lower_ok,
                     "explains": [_brief(els[i]) for i in sorted(ids, key=lambda i: (LAYER_ORDER.get(els[i]["layer"], 9), i))][:MAX_LISTED],
                     "explains_count": len(ids), "also_on_it": sorted(on_it)[:MAX_LISTED], "maintenance": maint, "note": note})
    rows.sort(key=lambda r: (LAYER_ORDER.get(r["layer"], 9), -r["explains_count"], r["id"]))
    out = {"source": SOURCE, "device_id": device_id, "fault_count": len(faults), "root_cause_count": len(rows), "root_causes": rows}
    if not rows:
        out["note"] = "UP でない要素は無い" if not faults else f"{device_id} に関わる原因は無い（ほかに UP でない要素が {len(faults)} 個ある）"
    return out


# ---------------------------------------------------------------- 事前チェック（what-if。2026-10-04）
WHAT_IF_OPS = ("link_down", "link_up", "device_down", "device_up")
# 端の役割（つながりの中継にしない機器）。app/temporal/rules.py の END_ROLES と同じ（tests/test_workflow.py が一致を検査）
END_ROLES = ("trex",)


def impact(devices: list, links: list, changes: list) -> dict:
    """回線・機器を落とした / 上げたと仮定して、孤立する機器と冗長が切れる機器を出す（修復を打つ前の事前チェック）。
    devices = [{device_id, status, role}]、links = [{a, a_if, b, b_if, status}]、changes = [{op, target}]。
    op は link_down / link_up（target = <機器>#<IF>。どちらの端でもよい）か device_down / device_up（target = 機器名）。
    つながりは DOWN でない回線と機器だけで見て、変更前のかたまりのうち、変更後にいちばん大きい断片から外れた機器を「孤立」とする。同点ならどれも本流にしない
    （つながり直す機器は、変更後の本流にいて、変更前はその中のいちばん大きい断片（同点なら変更前の本流）にいなかった機器。それも同点なら変更前に次数 0 だった機器だけ。isolated_after は変更後のいちばん大きいかたまりに入っていない機器）。
    role が END_ROLES の機器（TRex。4 台の leaf につながるが転送しない）は端として扱い、ほかの機器どうしをつなぐ中継にしない。
    端は、つながる相手がかたまりに入っていればかたまりに入る。冗長の本数も、端でない機器は端への回線を数えない（leaf は Spine への本数）。
    role が無ければ全部を中継として見る（ワーカーの awsio.read_topology も role を読んで渡す）。
    app/agentcore/topology.py と app/temporal/rules.py に同じものを置く（ワーカーのイメージには app/agentcore/ が入らない。tests/test_workflow.py が一致を検査）"""
    dev_down = {d["device_id"] for d in devices if (d.get("status") or "UP") == "DOWN"}
    link_down = {n for n, l in enumerate(links) if (l.get("status") or "UP") == "DOWN"}
    ids = {d["device_id"] for d in devices}
    ends = {d["device_id"] for d in devices if d.get("role") in END_ROLES}

    def view(dd: set, ld: set):
        adj = {i: [] for i in sorted(ids - dd)}
        for n, l in enumerate(links):
            if n not in ld and l["a"] in adj and l["b"] in adj:
                adj[l["a"]].append(l["b"])
                adj[l["b"]].append(l["a"])
        seen, comps = set(), []
        for start in adj:
            if start in seen or start in ends:
                continue
            comp, stack = {start}, [start]
            while stack:
                for o in adj[stack.pop()]:
                    if o not in comp and o not in ends:
                        comp.add(o)
                        stack.append(o)
            seen |= comp
            comps.append(comp)
        main = max(comps, key=len, default=set())
        main = main | {i for i in ends & set(adj) if any(o in main for o in adj[i])}
        return adj, comps, set(adj) - main, {i: sum(1 for o in v if i in ends or o not in ends) for i, v in adj.items()}

    def largest(parts: list) -> set:
        # 唯一いちばん大きい断片。同点か空なら空（どれも本流にしない）
        parts = sorted((p for p in parts if p), key=len, reverse=True)
        return parts[0] if parts and (len(parts) == 1 or len(parts[0]) > len(parts[1])) else set()

    def lost(adj_a: dict, comps_a: list, adj_b: dict, comps_b: list) -> set:
        # a のかたまり K ごとに、生き残り（b でも生きている機器）を b のかたまりで分け、唯一いちばん大きい断片 W(K) から外れた機器。
        # 端は、a で隣接するかたまりのうち唯一いちばん大きい K* があり、b で W(K*) のどれにも隣接していなければ外れる
        out = set()
        for k in comps_a:
            out |= (k & set(adj_b)) - largest([k & c for c in comps_b])
        for e in ends & set(adj_a) & set(adj_b):
            k = largest([c for c in comps_a if any(o in c for o in adj_a[e])])
            if k and not any(o in largest([k & c for c in comps_b]) for o in adj_b[e]):
                out.add(e)
        return out

    adj0, comps0, _, deg0 = view(dev_down, link_down)
    dd, ld, unknown, targets = set(dev_down), set(link_down), [], set()
    for c in changes:
        op, target = str(c.get("op") or ""), str(c.get("target") or "")
        if op in ("device_down", "device_up") and target in ids:
            (dd.add if op == "device_down" else dd.discard)(target)
            targets.add(target)
            continue
        hit = [n for n, l in enumerate(links) if target in (f'{l["a"]}#{l["a_if"]}', f'{l["b"]}#{l["b_if"]}')]
        if op in ("link_down", "link_up") and hit:
            for n in hit:
                (ld.add if op == "link_down" else ld.discard)(n)
        else:
            unknown.append(f"{op} {target}".strip())
    adj1, comps1, iso1, deg1 = view(dd, ld)
    # つながり直す機器: 変更後の本流 K（唯一いちばん大きいかたまり）にいて、変更前は K の中の唯一いちばん大きい断片 W にいなかった機器
    # （孤立、別のかたまり、DOWN）。端は、変更後に K につながり、変更前は W につながっていなかったもの。
    # W が同点で決まらないときは変更前の本流を W にする（本流でない同じ大きさのかたまりどうしがつながって本流を抜いたときは、その全部）。
    # 変更前の本流も同点なら、変更前に次数 0 だった（孤立か DOWN の）機器だけ（同点のかたまりどうしをつないでも全部が載らない）。
    # 変更前の本流を先に使わないのは、回線を落として別のかたまりが本流に繰り上がったとき、そのかたまりを「つながり直す」に載せないため
    main1 = largest(comps1)
    w0 = largest([main1 & c for c in comps0]) or largest(comps0)
    back = (main1 - w0) | {e for e in ends & set(adj1) if any(o in main1 for o in adj1[e]) and not any(o in w0 for o in adj0.get(e, []))}
    out = {
        "changes": [{"op": str(c.get("op") or ""), "target": str(c.get("target") or "")} for c in changes], "unknown": unknown,
        "newly_isolated": sorted(lost(adj0, comps0, adj1, comps1) - targets),
        "reconnected": sorted(i for i in back if w0 or deg0.get(i, 0) == 0),
        "redundancy_lost": sorted(i for i in deg1 if deg1[i] == 1 and deg0.get(i, 0) >= 2 and i not in iso1),
        "redundancy_restored": sorted(i for i in deg1 if deg1[i] >= 2 and deg0.get(i, 0) <= 1 and i not in iso1),
        "isolated_after": sorted(iso1),
    }
    out["verdict"] = "unknown" if unknown else "danger" if out["newly_isolated"] else "warn" if out["redundancy_lost"] else "ok"
    parts = []
    if unknown:
        parts.append("トポロジに無い対象: " + ", ".join(unknown))
    if out["newly_isolated"]:
        parts.append("孤立する機器: " + ", ".join(out["newly_isolated"]))
    if out["redundancy_lost"]:
        parts.append("冗長が切れる機器（残りの回線が 1 本）: " + ", ".join(out["redundancy_lost"]))
    if not out["newly_isolated"] and not out["redundancy_lost"] and not unknown:
        parts.append("孤立する機器も、冗長が切れる機器も無い")
    if out["reconnected"]:
        parts.append("つながり直す機器: " + ", ".join(out["reconnected"]))
    if out["redundancy_restored"]:
        parts.append("冗長が戻る機器: " + ", ".join(out["redundancy_restored"]))
    out["summary"] = "。".join(parts)
    return out


def what_if(op: str, target: str) -> dict:
    """回線か機器を 1 つ落とした / 上げたと仮定したときの影響（いまの status に重ねて見る）"""
    if op not in WHAT_IF_OPS:
        return {"error": f"op は {' / '.join(WHAT_IF_OPS)} のどれか"}
    return {"source": SOURCE, **impact(DEVICES, LINKS, [{"op": op, "target": (target or "").strip()}])}


# ---------------------------------------------------------------- Nautobot の変更履歴（Job が Neptune に写したもの。2026-10-04）
CHANGES_NOT_DEPLOYED = "変更履歴はまだ無い（Nautobot と Neptune が要る。Nautobot の Job が変更のたびに Neptune に写す）"


def recent_changes(device_id: str = "", limit: int = 20) -> dict:
    """Nautobot で直近に変えたもの（新しい順）。device_id があれば、その機器か、名前にその機器を含むもの（ケーブルなど）だけ"""
    if not graph.configured():
        return {"error": CHANGES_NOT_DEPLOYED, "changes": []}
    limit = max(1, min(int(limit), 50))
    try:
        rows = graph.list_records("change", "change_id", "time", limit=50)
    except graph.errors() as e:
        return {"error": f"変更履歴を読めない: {str(e)[:200]}", "changes": []}
    if device_id:
        rows = [r for r in rows if r.get("device_id") == device_id or device_id in str(r.get("object") or "")]
    changes = [{"time_jst": toolkit.jst(r.get("time")), "time": r.get("time"), "user": r.get("user") or "", "action": r.get("action") or "",
                "object_type": r.get("object_type") or "", "object": r.get("object") or "", "device_id": r.get("device_id") or "",
                "detail": r.get("detail") or ""} for r in rows[:limit]]
    return {"device_id": device_id, "count": len(changes), "changes": changes,
            "note": "Nautobot（機器と回線の正）での変更だけ。機器に直接打った設定変更は入らない（ログを search_logs で見る）"}


CENTRALITY_NOT_DEPLOYED = "中心性は Neptune Analytics のグラフアルゴリズムで計算する。グラフ（IaC/terraform/aws-managed/pipeline/graph）がまだ無い"


def centrality(limit: int = 10) -> dict:
    """機器の中心性（次数・近接）と、つながりの島（弱連結成分）。計算は Neptune Analytics の neptune.algo.*（graph.centrality）。
    静的データでは計算しない（OSS 版では NetworkX などに置き換える。docs/oss-variant.md）"""
    if not graph.configured():
        return {"error": CENTRALITY_NOT_DEPLOYED, "devices": []}
    limit = max(1, min(int(limit), 50))
    try:
        out = graph.centrality(limit)
    except graph.errors() as e:   # OSS 版は Neo4j のドライバの失敗（GDS が無い等）もここで受ける
        return {"error": f"中心性を計算できない: {str(e)[:200]}", "devices": []}
    out["note"] = "degree は回線の数、closeness は大きいほど中心、component は島の番号。components が 2 以上なら分断している"
    return out


def interfaces(device_id: str) -> list[str]:
    """device_id のインタフェース名（Web の編集画面の選択肢）。Neptune に lab の定義から入れた一覧があればそれと、
    つながるリンクに出てくるその機器側の名前（静的データには一覧が無いので、いま使われているものだけ）"""
    names = {l["a_if"] if l["a"] == device_id else l["b_if"] for l in LINKS if device_id in (l["a"], l["b"])}
    names |= {i.get("name") for i in (DEVICE_BY_ID.get(device_id) or {}).get("interfaces") or []}
    return sorted((n for n in names if n), key=lambda n: (len(n), n))


def link_choices() -> list[tuple[str, str]]:
    """Web の削除用。(表示, 値) の並びで、値は "a|a_if|b"（graph.remove_link の引数に戻す。機器名と IF 名に | は無い）"""
    out = []
    for l in LINKS:
        extra = l.get("kind") or ""
        if l.get("role"):
            extra += " " + l["role"]
        out.append((f'{l["a"]} {l["a_if"]} - {l["b"]} {l["b_if"]}  [{extra}]', f'{l["a"]}|{l["a_if"] or ""}|{l["b"]}'))
    return out


TOOL_SPECS = [
    {"toolSpec": {
        "name": "list_devices",
        "description": "監視対象ネットワークの機器一覧（拠点 site、役割 role、管理 IP、AS 番号、いまの状態 status = UP / DOWN / ALARM、maintenance = true は Nautobot で保守中にしてある機器、registered = false はトポロジに未登録で検知だけが来た機器 role=unknown）。site や role で絞れる。",
        "inputSchema": {"json": {"type": "object", "properties": {
            "site": {"type": "string", "description": "拠点名で絞る（dc1）。空なら全部"},
            "role": {"type": "string", "description": "役割で絞る（spine / a-leaf / s-leaf（どちらも Leaf）/ trex = 負荷をかける TRex / unknown）。空なら全部"},
        }}},
    }},
    {"toolSpec": {
        "name": "neighbors",
        "description": "機器の隣接（接続先の機器、両端のインタフェース名、回線の種別 fabric = Spine と Leaf のあいだ / l2 = TRex と Leaf のあいだ / lag = LACP（いまの lab には無い）、主副、帯域、回線のいまの状態 status = UP / DOWN）。",
        "inputSchema": {"json": {"type": "object", "required": ["device_id"], "properties": {
            "device_id": {"type": "string", "description": "機器名（例 dc1-a-leaf-01）"},
        }}},
    }},
    {"toolSpec": {
        "name": "blast_radius",
        "description": "機器が停止したときに影響が及ぶ範囲（指定ホップ数以内の機器と拠点。各機器のいまの状態 status 付き）。",
        "inputSchema": {"json": {"type": "object", "required": ["device_id"], "properties": {
            "device_id": {"type": "string", "description": "停止を想定する機器名"},
            "max_hops": {"type": "integer", "description": "何ホップ先まで見るか（既定 2、最大 6）"},
        }}},
    }},
    {"toolSpec": {
        "name": "root_cause",
        "description": "いま UP でない要素（機器・回線・インタフェース・IS-IS の隣接・BGP のセッションなど）を、層をまたいで下へ辿って根本原因ごとにまとめる。root_causes の各行が原因（下の層に DOWN が無い要素）で、explains はその原因で説明できる異常、also_on_it はまだ UP のままその上に乗っている要素、lower_layers_up = true は下の層が生きているのにその層だけ落ちている（設定やプロセスを疑う）。アラートが何本も出ているときや「原因は」「なぜ落ちた」と聞かれたら、まずこれを使う。",
        "inputSchema": {"json": {"type": "object", "properties": {
            "device_id": {"type": "string", "description": "機器名（例 dc1-a-leaf-01）。その機器に関わる原因だけにする。空なら全部"},
        }}},
    }},
    {"toolSpec": {
        "name": "what_if",
        "description": "回線か機器を 1 つ落とした / 上げたと仮定して、いまの状態に重ねたときの影響を出す（作業や処置の前の事前チェック）。newly_isolated = 孤立する機器、redundancy_lost = 残りの回線が 1 本になる機器、reconnected / redundancy_restored = 上げたときに戻る機器、verdict = danger（孤立が出る）/ warn（冗長が切れる）/ ok / unknown（対象がトポロジに無い）。実際には何も変えない。",
        "inputSchema": {"json": {"type": "object", "required": ["op", "target"], "properties": {
            "op": {"type": "string", "description": "link_down / link_up / device_down / device_up"},
            "target": {"type": "string", "description": "回線なら <機器>#<インタフェース>（例 dc1-a-leaf-01#ethernet-1/1。どちらの端でもよい）、機器なら機器名"},
        }}},
    }},
    {"toolSpec": {
        "name": "topology_graph",
        "description": "ネットワーク全体のノードとリンクの一覧（物理層）。全体像を説明するときに使う。",
        "inputSchema": {"json": {"type": "object", "properties": {}}},
    }},
    {"toolSpec": {
        "name": "layers",
        "description": "物理層より上の情報。IP 層（ip_interface = サブインタフェースのアドレス、isis_adjacency = IS-IS の隣接）と EVPN・BGP 層（bgp_session = iBGP EVPN のセッション（Spine がルートリフレクタ）、evpn_instance = EVI / VNI / VTEP、ethernet_segment = LACP の multihoming の ESI。いまの lab には無い）。各頂点は interface_id / ip_interface_id で下の層の頂点を指し、辺 over / peer / tunnel / attach / segment でつながる。各頂点のいまの状態 status = UP / DOWN 付き。「BGP のセッションは」「IS-IS の隣接は」「EVPN は」と聞かれたら使う。",
        "inputSchema": {"json": {"type": "object", "properties": {
            "device_id": {"type": "string", "description": "機器名（例 dc1-a-leaf-01）。空なら全機器"},
            "layer": {"type": "string", "description": "ip か evpn。空なら両方"},
        }}},
    }},
    {"toolSpec": {
        "name": "recent_changes",
        "description": "Nautobot（機器と回線の正）で直近に変えたもの（新しい順。いつ time_jst、誰が user、create / update / delete、何を object_type と object、どう変えたか detail = 「status: Active → Maintenance」の形）。異常の直前に構成を変えていないかを確かめるのに使う。機器に直接打った設定変更は入らない。",
        "inputSchema": {"json": {"type": "object", "properties": {
            "device_id": {"type": "string", "description": "機器名（例 dc1-a-leaf-01）。その機器に関わる変更だけにする。空なら全部"},
            "limit": {"type": "integer", "description": "件数（既定 20、最大 50）"},
        }}},
    }},
    {"toolSpec": {
        "name": "centrality",
        "description": "機器の中心性と、つながりの島（Neptune Analytics のグラフアルゴリズム）。degree = 付いている回線の数、closeness = ほかの全機器への近さ（大きいほど中心にあり、落ちたときに影響が広い）、component = 回線でつながっている島の番号。components が 2 以上なら、どの回線でも届かない機器の組がある。「どの機器が要か」「単一障害点になりそうな機器は」「ネットワークが分断していないか」に使う。devices は closeness の大きい順",
        "inputSchema": {"json": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "件数（既定 10、最大 50）"},
        }}},
    }},
]
TOOLS = {"list_devices": list_devices, "neighbors": neighbors, "blast_radius": blast_radius, "root_cause": root_cause, "what_if": what_if, "topology_graph": topology_graph, "layers": layers, "recent_changes": recent_changes, "centrality": centrality}
# ツールを呼ぶ前に reload()（TTL を過ぎていれば Neptune を読み直す。画面での編集が次の質問に効く）
run_tool = toolkit.runner(TOOLS, before=reload)
