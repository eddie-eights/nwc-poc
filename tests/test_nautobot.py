"""Nautobot 連携（app/nautobot/、IaC/terraform/aws-managed/pipeline/nautobot、ops/up.sh が PIPELINE=1 でいつも作る）の模擬テスト。AWS にも Nautobot にも触れない。
- 対応付け（app/nautobot/nwc/nb_map.py）: lab の定義 → seed_plan → Nautobot → to_graph / targets と一周すると、lab と同じ機器・回線・gnmic の購読先に戻る
- 同期（nb_sync.push_targets）: 変わったときだけ SSM を書いて gnmic を作り直す。空の一覧は書かない
- Web: Nautobot があるあいだは、リンクの追加・削除を Nautobot の REST API に書く（app/dashboard/topology_view.py、app/dashboard/nautobot_api.py）。静的データの投入は止める
- 配線: Dockerfile・Terraform・ops/up.sh・ops/down.sh の名前と順序がそろっている
実行は uv run --group dev --group web python tests/test_nautobot.py（app/dashboard/topology_view.py が gradio と pandas を読む）。"""
import json, logging, os, re, subprocess, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [os.path.join(ROOT, "app", "agentcore"), os.path.join(ROOT, "app", "nautobot", "nwc"), os.path.join(ROOT, "app", "containerlab")]

passed = 0
def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)

def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()

def read_ops(name):  # ops/up.sh / down.sh は、読んでいる共通の関数（ops/common.sh と ops/<name>-common.sh。OSS 版の ops/oss/ と共通）とつないで見る
    return read("ops", "common.sh") + read("ops", f"{name}-common.sh") + read("ops", f"{name}.sh")

def lab_cli(*flags):
    return subprocess.run([sys.executable, os.path.join(ROOT, "app", "containerlab", "lab_topology.py"), os.path.join(ROOT, "app", "containerlab"), *flags],
                          check=True, capture_output=True, text=True).stdout.strip()

import nb_map

lab = json.loads(lab_cli())
plan = nb_map.seed_plan(lab)

# ---- seed_plan（lab → Nautobot に作るもの）
check("seed: 機器は lab と同じ台数で、site と role が全部挙がる",
      len(plan["devices"]) == len(lab["devices"]) and set(plan["sites"]) == {d["site"] for d in lab["devices"]}
      and set(plan["roles"]) == {d["role"] for d in lab["devices"]})
check("seed: 監視対象（enabled）の機器だけが gnmi と snmp の Service を持つ",
      all((d["services"] == [nb_map.GNMI_SERVICE, nb_map.SNMP_SERVICE]) == bool(l["enabled"]) and (d["services"] == []) != bool(l["enabled"])
          for d, l in zip(plan["devices"], lab["devices"])))
check("seed: LAG の親がメンバーより先に並び、親の type は lag",
      all([i["name"] for i in d["interfaces"]].index(i["lag"]) < n and
          next(p for p in d["interfaces"] if p["name"] == i["lag"])["type"] == "lag"
          for d in plan["devices"] for n, i in enumerate(d["interfaces"]) if i["lag"]))
check("seed: 管理 IP を持つ機器は、その IP のインタフェースがちょうど 1 つ mgmt",
      all(sum(i["mgmt"] for i in d["interfaces"]) == (1 if d["mgmt_ip"] else 0) for d in plan["devices"]))
check("seed: インタフェースの IP は全部、親の Prefix に入る",
      all(nb_map.parent_prefix(i["address"]) in plan["prefixes"] for d in plan["devices"] for i in d["interfaces"] if i["address"]))
check("parent_prefix: v4 は /24、v6 は /64", nb_map.parent_prefix("203.0.113.11") == "203.0.113.0/24"
      and nb_map.parent_prefix("2001:db8:1:2::5") == "2001:db8:1:2::/64")
check("interface_type", nb_map.interface_type("lag1", {"lag1"}) == "lag" and nb_map.interface_type("ethernet-1/1", set()) == "25gbase-x-sfp28"
      and nb_map.interface_type("mgmt0", set()) == "1000base-t")

# ---- 一周（seed したとおりに Nautobot が持っているとして、nb_sync.read() が返す形に直す）
def as_rows(plan):
    return [{"name": d["name"], "site": d["site"], "role": d["role"], "mgmt_ip": d["mgmt_ip"], "asn": d["asn"],
             "interfaces": [{"name": i["name"], "address": i["address"], "lag": i["lag"]} for i in d["interfaces"]],
             "services": [{"name": s[0], "protocol": s[1], "ports": [s[2]]} for s in d["services"]]} for d in plan["devices"]]
rows = as_rows(plan)
devices, links, warnings = nb_map.to_graph(rows, plan["cables"])
key = lambda l: (l["a"], l["b"], l["a_if"])
strip = lambda d: {k: (sorted(v, key=lambda i: i["name"]) if k == "interfaces" else v) for k, v in d.items() if k != "aliases"}
check("一周: 警告が出ない", warnings == [])
check("一周: 機器が lab と同じ（device_id / site / role / mgmt_ip / asn / enabled / interfaces）",
      devices == sorted((strip(d) for d in lab["devices"]), key=lambda d: d["device_id"]))
check("一周: 回線が lab と同じ（端・kind・role・bandwidth_mbps）", links == sorted(lab["links"], key=key))
t = nb_map.targets(rows)
check("一周: gNMI の一覧が app/containerlab/lab_topology.py --gnmi-targets と同じ文字列（最初の同期で gnmic を作り直さない）",
      t["gnmi-targets"] == lab_cli("--gnmi-targets"))
check("targets のキーは gNMI の 1 つだけ（SNMP のポーリングは cycle 013 でやめた）で、IaC/terraform/aws-managed/pipeline/stream の gnmic.tf の SSM パラメータの名前の末尾",
      tuple(t) == nb_map.TARGET_KEYS == ("gnmi-targets",)
      and all(f'/{k}"' in read("IaC", "terraform", "aws-managed", "pipeline", "stream", "gnmic.tf") for k in t))

# ---- to_graph / targets の規則
R = lambda name, role="a-leaf", ip="", services=(), ifs=(): {"name": name, "site": "s", "role": role, "mgmt_ip": ip, "asn": "",
    "interfaces": [{"name": n, "address": "", "lag": lag} for n, lag in ifs], "services": [{"name": s, "protocol": p, "ports": [port]} for s, p, port in services]}
check("kind: spine が入れば fabric、スイッチどうしは l2、TRex（VM_ROLES）は LAG のメンバーなら lag・そうでなければ l2",
      (nb_map.link_kind("a-leaf", "spine", "", ""), nb_map.link_kind("a-leaf", "s-leaf", "", ""), nb_map.link_kind("trex", "a-leaf", "lag1", ""),
       nb_map.link_kind("a-leaf", "trex", "", "")) == ("fabric", "l2", "lag", "l2"))
d2, l2, w2 = nb_map.to_graph(
    [R("b", ifs=[("e1", "")]), R("a", "spine", ifs=[("e1", ""), ("e2", "")]), R("a"), R("")],
    [{"a": "b", "a_if": "e1", "b": "a", "b_if": "e1"}, {"a": "a", "a_if": "e1", "b": "b", "b_if": "e9"},
     {"a": "a", "a_if": "e1", "b": "a", "b_if": "e2"}, {"a": "a", "a_if": "e2", "b": "zz", "b_if": "e1"}])
check("to_graph: a < b にそろえ、同じ機器どうし・知らない機器・重なった回線・名前の無い機器と重複した機器を落として警告する",
      [d["device_id"] for d in d2] == ["a", "b"] and l2 == [{"a": "a", "a_if": "e1", "b": "b", "b_if": "e1", "kind": "fabric", "role": None, "bandwidth_mbps": None}]
      and len(w2) == 5)
check("to_graph: asn の空文字は None、Service が無ければ enabled でない", d2[0]["asn"] is None and d2[0]["enabled"] is False)
t2 = nb_map.targets([R("z", ip="10.0.0.2", services=[("gNMI", "tcp", 6030)]), R("y", ip="10.0.0.1", services=[("snmp", "udp", 161), ("gnmi", "udp", 1)]),
                     R("x", services=[("gnmi", "tcp", 57400)]), R("w", ip="10.0.0.9")])
check("targets: 名前の順、Service の名前は大文字小文字を問わず protocol は合わせる、ポートは Service の値、管理 IP の無い機器と snmp だけの機器は入らない",
      t2 == {"gnmi-targets": '"10.0.0.2:6030"'})
check("targets: 機器が無ければ空文字（nb_sync が書かない）", nb_map.targets([]) == {"gnmi-targets": ""})
try:
    nb_map.targets([R("bad", ip='1.1.1.1", "x', services=[("gnmi", "tcp", 1)])])
    bad = False
except ValueError:
    bad = True
check("targets: IP でない管理 IP は通さない（gnmic の設定に入るので）", bad)

# ---- nb_sync.push_targets（boto3 を差し替える）
import toolkit
class Ssm:
    def __init__(self, values): self.values, self.puts = dict(values), []
    def get_parameter(self, Name): return {"Parameter": {"Value": self.values[Name]}}
    def put_parameter(self, **kw): self.puts.append(kw); self.values[kw["Name"]] = kw["Value"]
class Ecs:
    def __init__(self): self.calls = []
    def update_service(self, **kw): self.calls.append(kw)
import nb_sync
log = logging.getLogger("test")
log.setLevel(logging.CRITICAL)
def push(values, rows, force=False, env=True):
    ssm, ecs = Ssm(values), Ecs()
    toolkit.client = lambda name: {"ssm": ssm, "ecs": ecs}[name]
    for k, v in {"GNMI_TARGETS_PARAMETER": "/p/gnmi", "TELEGRAF_CLUSTER": "c", "GNMIC_SERVICE": "s"}.items():
        os.environ[k] = v if env else ""
    return nb_sync.push_targets(rows, log, force), ssm, ecs
out, ssm, ecs = push({"/p/gnmi": t["gnmi-targets"]}, rows)
check("push_targets: 同じなら SSM も ECS も触らない", out == {"changed": [], "redeployed": False} and not ssm.puts and not ecs.calls)
out, ssm, ecs = push({"/p/gnmi": "old"}, rows)
check("push_targets: 変わったら String で上書きし、gnmic を 1 回作り直す",
      out == {"changed": ["gnmi-targets"], "redeployed": True}
      and ssm.puts == [{"Name": "/p/gnmi", "Value": t["gnmi-targets"], "Type": "String", "Overwrite": True}]
      and ecs.calls == [{"cluster": "c", "service": "s", "forceNewDeployment": True}])
out, ssm, ecs = push({"/p/gnmi": "old"}, [])
check("push_targets: 空の一覧は書かない（gnmic が起動できなくなる）", not ssm.puts and not ecs.calls)
out, ssm, ecs = push({"/p/gnmi": t["gnmi-targets"]}, rows, force=True)
check("push_targets: force_redeploy なら変わっていなくても作り直す", not ssm.puts and len(ecs.calls) == 1)
out, ssm, ecs = push({}, rows, env=False)
check("push_targets: 書き先が渡されていなければ何もしない", out == {"skipped": True} and not ecs.calls)

# ---- nb_sync.sync（Django の cache.lock と Nautobot の読みを差し替える）
import contextlib, types
import graph
fake = types.ModuleType("django.core.cache")
fake.cache = types.SimpleNamespace(lock=lambda *a, **kw: contextlib.nullcontext())
sys.modules.update({"django": types.ModuleType("django"), "django.core": types.ModuleType("django.core"), "django.core.cache": fake})
synced = []
graph.configured = lambda: True
graph.sync_physical = lambda devices, links: synced.append((len(devices), len(links))) or {"devices": len(devices)}
toolkit.client = lambda name: {"ssm": Ssm({"/p/gnmi": "old"}), "ecs": Ecs()}[name]
_changes = [{"id": "u1", "time": 1790000000, "user": "admin", "action": "update", "object_type": "device", "object": "DC1-A-Leaf-01", "device": "DC1-A-Leaf-01",
             "differences": {"removed": {"status": {"name": "Active"}, "last_updated": "a"}, "added": {"status": {"name": "Maintenance"}, "last_updated": "b"}}},
            {"id": "u2", "time": 1790000100, "user": "nwc-web", "action": "delete", "object_type": "cable", "object": "x <> y", "device": "", "differences": None},
            {"id": "", "time": 1}]
changes_synced = []
nb_sync.read_changes = lambda: list(_changes)
graph.sync_changes = lambda rows: changes_synced.append(rows) or {"kept": len(rows)}
nb_sync.read = lambda: ([], [])
out = nb_sync.sync(log)
check("sync: 機器が 1 台も無いときは Neptune を触らない（空で合わせると物理層が全部消える）", synced == [] and out["neptune"] == {"skipped": True})
nb_sync.read = lambda: (rows, plan["cables"])
out = nb_sync.sync(log)
check("sync: 機器があれば Neptune の物理層を合わせる", synced == [(len(lab["devices"]), len(lab["links"]))])
# ---- 保守中と変更履歴（2026-10-04）
check("sync: 変更履歴を Neptune に写す（新しい順。id の無い行は落とす。機器が無くても写す）",
      len(changes_synced) == 2 and out["changes"] == {"kept": 2} and [r["change_id"] for r in changes_synced[-1]] == ["change#u2", "change#u1"])
check("change_rows: 機器名は小文字、差分は「項目: 前 → 後」（last_updated は出さない）、削除は「削除」、差分が無ければ空",
      changes_synced[-1][1] == {"change_id": "change#u1", "time": 1790000000, "user": "admin", "action": "update", "object_type": "device",
                                "object": "DC1-A-Leaf-01", "device_id": "dc1-a-leaf-01", "detail": "status: Active → Maintenance"}
      and changes_synced[-1][0]["detail"] == "削除" and nb_map.change_detail({"removed": {}, "added": {"name": "x"}}) == "name: - → x"
      and nb_map.change_detail(None, "update") == nb_map.change_detail({"removed": {}, "added": {}}, "update") == "")
# 2026-10-08 の OSS 版の検証の「不具合」4: seed で作った機器の最初の変更は prechange が無く、get_snapshots() の差分が {"removed": None, "added": 全部の項目}。
# 並べると「asset_tag: - → -、clusters: - → []、…」になる
_full = {"asset_tag": None, "clusters": [], "comments": "", "name": "dc1-a-leaf-01", "status": {"name": "Maintenance"}, "last_updated": "b"}
check("change_detail: 作成と削除は項目を並べず「作成」「削除」だけ（差分が全部の項目でも）",
      nb_map.change_detail({"removed": None, "added": _full}, "create") == "作成" and nb_map.change_detail({"removed": _full, "added": None}, "delete") == "削除")
check("change_detail: update で prechange が無ければ項目を並べず、前の値が無いことと今の status だけを出す（status が無い物は前の値が無いことだけ）",
      nb_map.change_detail({"removed": None, "added": _full}, "update") == nb_map.NO_PRECHANGE + "。今の status: Maintenance"
      and nb_map.change_detail({"removed": None, "added": {"name": "ethernet-1/1", "lag": None}}, "update") == nb_map.NO_PRECHANGE
      and "asset_tag" not in nb_map.change_detail({"removed": None, "added": _full}) and "→" not in nb_map.change_detail({"removed": None, "added": _full}))
check("change_rows: action を change_detail に渡す（prechange の無い update の行は前の値が無いことだけ。作成の行は「作成」）",
      [r["detail"] for r in nb_map.change_rows([{"id": "a", "time": 2, "action": "update", "differences": {"removed": None, "added": _full}},
                                                {"id": "b", "time": 1, "action": "create", "differences": {"removed": None, "added": _full}}])]
      == [nb_map.NO_PRECHANGE + "。今の status: Maintenance", "作成"])
check("change_rows: 新しい順に CHANGES_KEEP 件まで",
      len(nb_map.change_rows([{"id": str(i), "time": i} for i in range(nb_map.CHANGES_KEEP + 5)])) == nb_map.CHANGES_KEEP
      and nb_map.change_rows([{"id": "a", "time": 1}, {"id": "b", "time": 2}])[0]["change_id"] == "change#b")
dm, _, _ = nb_map.to_graph([{**rows[0], "status": "Maintenance"}, {**rows[1], "status": "Active"}], [])
check("to_graph: Status が Maintenance の機器だけ maintenance = true を持つ（ほかはキーごと無い）", dm[0].get("maintenance") is True and "maintenance" not in dm[1])
# ---- OSS 版（cycle 005）: GRAPH_BACKEND=neo4j なら同じ Job が app/agentcore/graph.py の Neo4j 側で書く。戻り値の鍵と文言だけが変わる
import importlib
class Log:
    def __init__(self): self.lines = []
    def info(self, msg, *a): self.lines.append(msg % a)
    warning = info
def oss_sync(uri, devices):
    saved = {k: os.environ.get(k) for k in ("GRAPH_BACKEND", "NEO4J_URI", "NEPTUNE_GRAPH_ID")}
    os.environ.update({"GRAPH_BACKEND": "neo4j", "NEO4J_URI": uri, "NEPTUNE_GRAPH_ID": ""})
    try:
        importlib.reload(graph)
        importlib.reload(nb_sync)   # GRAPH_NAME / GRAPH_SETTING は import のときに graph.BACKEND から決まる
        written = []
        graph.sync_physical = lambda d, l: written.append((len(d), len(l))) or {"devices": len(d)}
        graph.sync_changes = lambda rows: {"kept": len(rows)}
        nb_sync.read, nb_sync.read_changes = (lambda: (devices, plan["cables"] if devices else [])), (lambda: list(_changes))
        lg = Log()
        return nb_sync.sync(lg), written, lg.lines, graph.configured(), (nb_sync.GRAPH_NAME, nb_sync.GRAPH_SETTING)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(graph)
        importlib.reload(nb_sync)
out, written, lines, conf, names = oss_sync("bolt://neo4j.o-nwc-oss-graph.internal:7687", rows)
check("sync（OSS 版）: NEO4J_URI があれば Neo4j の物理層と変更履歴を合わせ、結果は neo4j の鍵に入る（neptune の鍵は出さない）",
      conf and names == ("Neo4j", "NEO4J_URI") and written == [(len(lab["devices"]), len(lab["links"]))]
      and out["neo4j"] == {"devices": len(lab["devices"])} and "neptune" not in out and out["changes"] == {"kept": 2}
      and any(l.startswith("Neo4j の物理層を合わせた") for l in lines))
out, written, lines, conf, _ = oss_sync("", rows)
check("sync（OSS 版）: NEO4J_URI が空ならグラフを触らず、NEPTUNE_GRAPH_ID でなく NEO4J_URI を名指しする",
      not conf and written == [] and "neo4j" not in out and "changes" not in out and "NEO4J_URI が無い。Neo4j は触らない" in lines)
out, written, lines, _, _ = oss_sync("bolt://neo4j.o-nwc-oss-graph.internal:7687", [])
check("sync（OSS 版）: 機器が 1 台も無いときは Neo4j を触らない", written == [] and out["neo4j"] == {"skipped": True}
      and "Nautobot に機器が 1 台も無い。Neo4j は触らない" in lines)
check("sync（OSS 版）のあと、graph と nb_sync はマネージド版（Neptune）に戻る", graph.BACKEND == "neptune" and nb_sync.GRAPH_NAME == "Neptune")
graph.configured = lambda: True
ns = read("app", "nautobot", "nwc", "nb_sync.py")
check("nb_sync.read は機器の Status を読み、read_changes は ObjectChange を新しい順に読む",
      '"status": d.status.name if d.status else ""' in ns and 'ObjectChange.objects.select_related("changed_object_type", "related_object_type").order_by("-time")[:limit]' in ns)
boot = read("app", "nautobot", "nwc", "bootstrap.py")
check("bootstrap: seed が失敗したら起動時の同期を飛ばし、JobHook は起動のたびに決まった形へ戻す",
      "steps_skip.add(first_sync)" in boot and "JobHook.objects.update_or_create" in boot)
check("bootstrap: Maintenance を機器の Status に選べるようにする（seed より前）",
      "for step in (superuser, api_user, custom_fields, statuses, seed, jobs, first_sync)" in boot and "status.content_types.add(device_ct)" in boot)
# ---- Job の名前（2026-10-08 の OSS 版の検証の「docs のずれ」3。前は OSS 版でも「Telegraf と Neptune に同期」。cycle 013 で Telegraf → gnmic）
import importlib.util
def load_jobs(graph_name):
    """app/nautobot/jobs/nwc_jobs.py を、Nautobot の Job の基底と nb_sync（GRAPH_NAME だけ）を差し替えて読む。{クラス名: (name, description)}"""
    stub = types.ModuleType("nautobot.apps.jobs")
    stub.Job, stub.JobHookReceiver, stub.BooleanVar, stub.register_jobs = type("Job", (), {}), type("JobHookReceiver", (), {}), (lambda **kw: kw), (lambda *c: None)
    mods = {"nautobot": types.ModuleType("nautobot"), "nautobot.apps": types.ModuleType("nautobot.apps"), "nautobot.apps.jobs": stub,
            "nb_sync": types.SimpleNamespace(GRAPH_NAME=graph_name)}
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("nwc_jobs", os.path.join(ROOT, "app", "nautobot", "jobs", "nwc_jobs.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return {c: (getattr(m, c).Meta.name, getattr(m, c).Meta.description) for c in ("SyncTopology", "SyncOnChange")}
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
_jobs_nep, _jobs_neo = load_jobs("Neptune"), load_jobs("Neo4j")
check("Job の名前は両方の版で同じ「gnmic とグラフ DB に同期」「変更のたびに gnmic とグラフ DB に同期」で、Neptune とも Neo4j とも書かない",
      {k: v[0] for k, v in _jobs_nep.items()} == {k: v[0] for k, v in _jobs_neo.items()}
      == {"SyncTopology": "gnmic とグラフ DB に同期", "SyncOnChange": "変更のたびに gnmic とグラフ DB に同期"})
check("Job の説明は書き先の名前（nb_sync.GRAPH_NAME）を出す。マネージド版は Neptune、OSS 版は Neo4j",
      all("Neptune" in v[1] and "Neo4j" not in v[1] for v in _jobs_nep.values()) and all("Neo4j" in v[1] and "Neptune" not in v[1] for v in _jobs_neo.values())
      and "「gnmic とグラフ DB に同期」と同じ" in _jobs_nep["SyncOnChange"][1])
_old_name = subprocess.run(["git", "grep", "-n", "-e", "Telegraf と Neptune に同期", "-e", "Telegraf とグラフ DB に同期", "--", "app", "IaC", "ops",
                            "docs/nautobot.md", "docs/oss-variant.md", "docs/pipeline.md", "docs/architecture"], cwd=ROOT, capture_output=True, text=True).stdout
check(f"Job は名前でなくクラスの場所で引く（bootstrap の JOBS と JobHook の job）ので、名前を変えても外れない。古い名前はコードと docs に残らない（{_old_name.strip()}）",
      'JOBS = ("nwc_jobs.SyncTopology", "nwc_jobs.SyncOnChange")' in boot and '"job": models[JOBS[1]]' in boot and _old_name == "")

# ---- 配線
docker = read("docker", "images", "nautobot", "Dockerfile")
up, down, chk = read_ops("up"), read_ops("down"), read("ops", "check.sh")
tf = {n: read("IaC", "terraform", "aws-managed", "pipeline", "nautobot", n) for n in os.listdir(os.path.join(ROOT, "IaC", "terraform", "aws-managed", "pipeline", "nautobot")) if n.endswith(".tf")}
nb_tf, all_tf = tf["nautobot.tf"], "\n".join(tf.values())
ver = re.search(r"^NAUTOBOT_VERSION=(\S+)", up, re.M).group(1)
redis = re.search(r"^REDIS_TAG=(\S+)", up, re.M).group(1)
check("版: ops/up.sh の NAUTOBOT_VERSION = Dockerfile の ARG", f"ARG NAUTOBOT_VERSION={ver}\n" in docker)
check("版: ops/up.sh の REDIS_TAG = terraform の redis_image_tag の既定値",
      re.search(r'variable "redis_image_tag" \{[\s\S]*?default\s*=\s*"%s"' % re.escape(redis), tf["variables.tf"]) is not None)
check("Dockerfile が COPY するものを ops/up.sh の nautobot_context が集める",
      "nwc/ graph.py toolkit.py lab_seed.json /opt/nautobot/nwc/" in docker and "jobs/ /opt/nautobot/jobs/" in docker
      and 'cp -R app/nautobot/. "$1/" && cp app/agentcore/graph.py app/agentcore/toolkit.py "$1/"' in up and 'app/containerlab/lab_topology.py app/containerlab >"$1/lab_seed.json"' in up)
check("lab_seed.json はリポジトリに置かない（毎回 lab の定義から作る）", not os.path.exists(os.path.join(ROOT, "app", "nautobot", "nwc", "lab_seed.json")))
check("Job が import するモジュールが PYTHONPATH の先にそろう", "PYTHONPATH=/opt/nautobot/nwc" in docker
      and all(os.path.exists(os.path.join(ROOT, *p)) for p in (("app", "nautobot", "nwc", "nb_sync.py"), ("app", "nautobot", "nwc", "bootstrap.py"), ("app", "agentcore", "graph.py"), ("app", "agentcore", "toolkit.py"))))
envs = set(re.findall(r'os\.environ\.get\("([A-Z_]+)"', read("app", "nautobot", "nwc", "nb_sync.py")))
check("nb_sync が読む環境変数を Terraform がコンテナに渡す", envs and all(e in nb_tf for e in envs | {"NEPTUNE_GRAPH_ID"}) and "NEPTUNE_ENDPOINT" not in nb_tf)
check("シークレットは SSM の SecureString から（タスク定義の secrets と RDS の write-only）。Terraform の変数に値を持たない",
      all(s in nb_tf for s in ("NAUTOBOT_SECRET_KEY", "NAUTOBOT_DB_PASSWORD", "NAUTOBOT_SUPERUSER_PASSWORD", "NAUTOBOT_API_TOKEN"))
      and "password_wo " in tf["database.tf"].replace("=", " =").replace("  ", " ") and 'ephemeral "aws_ssm_parameter"' in tf["database.tf"]
      and not re.search(r"sensitive\s*=\s*true", tf["variables.tf"]))
names = set(re.findall(r'ensure_secret "/\$PREFIX/nautobot/([a-z-]+)"', up))
check("ops/up.sh が作る SSM のシークレット = Terraform が読む名前", names == {"secret-key", "admin-password", "db-password", "api-token"}
      and all(f'"{n}"' in all_tf for n in names))
check("SG とロールは base/core の nautobot / nautobot_db を使う", '"nautobot"' in all_tf and "nautobot_db" in all_tf)
check("Web が Nautobot の有無を読むパラメータ（<接頭辞>/nautobot/url）を Terraform が置く",
      "/nautobot/url" in nb_tf and 'toolkit.Param("NAUTOBOT_URL", "nautobot/url")' in read("app", "dashboard", "nautobot_api.py"))
order = [m.start() for m in (re.search(p, down, re.M) for p in (r"^destroy_root pipeline/analytics", r"^destroy_root pipeline/nautobot",
                                                           r"^destroy_lambda_root pipeline/graph", r"^destroy_root pipeline/stream")) for m in [m] if m]
check("ops/down.sh は nautobot を graph と stream より先に消す（state を読む相手が残っているうちに）", len(order) == 4 and order == sorted(order))
check("ops/down.sh は nautobot が消えなかったとき、その secrets を残す", 'case "$n" in "/$PREFIX/nautobot/"*)' in down and "pipeline/nautobot" in down.split("5-2.")[1])
check("ops/up.sh: stream に一覧の持ち主を渡し、nautobot は graph の seed（7-3b）の後・analytics（7-4）の前",
      '-var "gnmi_targets_from_nautobot=$GNMI_FROM_NAUTOBOT"' in up
      and up.index('log "7-3b.') < up.index("# ---- 7-3c. Nautobot") < up.index("# ---- 7-4. analytics"))
check("ops/up.sh: エンドポイントに ecs（Job が gnmic を作り直す）", "pipeline/nautobot) add_endpoints ecr.api ecr.dkr logs ecs ;;" in up)
check("ops/check.sh が nautobot のルートとこのテストを回す（モックの検査は tests/test_*.py のグロブ）", "pipeline/nautobot" in chk and "for t in tests/test_*.py; do" in chk)
check("Nautobot は PIPELINE=1 ならいつも作る（切り替える変数は無い）",
      'if [ -n "$PIPELINE" ] && { [ -z "$SKIP_STREAM" ] || [ -z "$SKIP_GRAPH" ]; }; then NAUTOBOT=1; fi' in up
      and "flag_value NAUTOBOT" not in up and "NAUTOBOT=1" not in read("deploy.env.example") and "GNMI_FROM_NAUTOBOT=false" not in up)
check("前の deploy.env の NAUTOBOT で止まらない（読むだけ読んで注意を出す）", "NAUTOBOT" in read("ops", "deploy-env.sh").split() and "注意: NAUTOBOT=0 は効かない" in up)
# lab-debug.yaml のロールは Nautobot の内部のシークレットを Deny する（cycle 044 Round 2。AmazonSSMManagedInstanceCore の Resource * に勝たせる）。
# その Deny の Statement（直前のコメント 1 行と Sid: DenyNautobotSecrets の塊）を除けば Nautobot の名前が出てこない
_dbg_yaml = read("IaC", "cloudformation", "lab-debug.yaml")
_dbg_wo_deny = re.sub(r"^ {14}# [^\n]*\n {14}- Sid: DenyNautobotSecrets\n(?: {16,}\S.*\n)+", "", _dbg_yaml, flags=re.M)
check("デバッグ用の EC2 は Nautobot を使わない（lab-debug.yaml で Nautobot が出てくるのはロールの DenyNautobotSecrets だけ）",
      "nautobot" not in read("ops", "lab-debug.sh").lower() and "nautobot" not in _dbg_wo_deny.lower()
      and _dbg_wo_deny != _dbg_yaml and "Effect: Deny" in _dbg_yaml.split("- Sid: DenyNautobotSecrets\n")[1].split("\n")[0])

# ---- Web のゲート
import types
sys.path.insert(0, os.path.join(ROOT, "app", "dashboard"))
sys.modules["config"] = types.ModuleType("config")   # app/dashboard/config.py は .env を読むので差し替える（topology_view が使うのは log だけ）
sys.modules["config"].log = log
import graph
import topology_view as tv
called = []
tv.refresh_topology = lambda a, b: ("fig", "info")
tv._graph_call = lambda *a, **k: (called.append(a) or ("done", "fig", "info"))
graph.configured = lambda: True
import nautobot_api as nb
nb.URL.value = lambda: ""
check("Web: Nautobot が無ければ今までどおり Neptune を編集できる", tv.can_edit() and tv.can_seed() and tv.edit_note() == "" and tv.seed_graph("a", "b")[0] == "done"
      and tv.add_link("x", "e1", "y", "e1", "l2", "", "")[0] == "done" and len(called) == 2)
nb.URL.value = lambda: "http://nautobot.example:8080"
nb.TOKEN.value = lambda: "t" * 40
http, state = [], {"cable": None}
def fake_call(method, path, query=None, body=None):   # Nautobot の REST API の代わり（形は 3.2.6 で確かめたもの）
    http.append((method, path, query, body))
    if method == "GET":
        if query["name"] == "new0":
            return {"results": []}
        return {"results": [{"id": f'{query["device"]}:{query["name"]}', "cable": state["cable"], "cable_peer": {"device": {"name": "y"}}}]}
    return {"id": "c1"} if path == "dcim/cables/" else {"id": f'{body["device"]["name"]}:{body["name"]}', "cable": None} if method == "POST" else {}
nb._call = fake_call
r = tv.seed_graph("a", "b")
check("Web: Nautobot があれば静的データの投入は止める（Job が上書きするので）", tv.can_edit() and not tv.can_seed() and tv.edit_note() == tv.NAUTOBOT_NOTE
      and r[0] == tv.NAUTOBOT_SEED_MSG and len(called) == 2 and not http)
r = tv.add_link("x", "new0", "y", "e1", "l2", "primary", 1000)
posts = [h for h in http if h[0] == "POST"]
check("Web: Nautobot があればリンクの追加は Nautobot に書く（無いインタフェースを作ってからケーブル。Neptune には書かない）",
      len(called) == 2 and "Nautobot に追加" in r[0] and nb.SYNC_NOTE in r[0] and [h[1] for h in posts] == ["dcim/interfaces/", "dcim/cables/"]
      and posts[0][3] == {"device": {"name": "x"}, "name": "new0", "type": "1000base-t", "status": "Active"}
      and posts[1][3] == {"termination_a_type": "dcim.interface", "termination_a_id": "x:new0", "termination_b_type": "dcim.interface", "termination_b_id": "y:e1",
                          "status": "Connected", "custom_fields": {"link_role": "primary", "bandwidth_mbps": 1000}})
state["cable"] = {"id": "c9"}
http.clear()
r = [tv.add_link("x", "e1", "y", "e1", "l2", "", ""), tv.remove_link("x|e1|z", "a", "b"), tv.remove_link("x|e1|y", "a", "b")]
check("Web: ケーブルのあるインタフェースには足さず、削除は相手の機器を確かめてからケーブルだけ消す",
      "もうケーブルがある" in r[0][0] and "y（z ではない）" in r[1][0] and "Nautobot から削除" in r[2][0] and len(called) == 2
      and [h[:2] for h in http if h[0] != "GET"] == [("DELETE", "dcim/cables/c9/")])
check("Web: トークンは SecureString として読み（decrypt）、API のユーザーは bootstrap が同じ環境変数から作る",
      nb.TOKEN.decrypt and nb.TOKEN.param == "nautobot/api-token" and not tv.toolkit.Param("X", "y").decrypt
      and '{"WithDecryption": True} if self.decrypt else {}' in read("app", "agentcore", "toolkit.py")
      and 'os.environ.get("NAUTOBOT_API_TOKEN", "")' in read("app", "nautobot", "nwc", "bootstrap.py") and "api_user, custom_fields" in read("app", "nautobot", "nwc", "bootstrap.py")
      and 'ensure_secret "/$PREFIX/nautobot/api-token" token' in up and "secrets.token_hex(20)" in up)
check("API のトークンは worker のコンテナには渡さない", '!contains(["NAUTOBOT_SUPERUSER_PASSWORD", "NAUTOBOT_API_TOKEN"], s.name)' in nb_tf)
graph.configured = lambda: False
check("Web: Neptune が無いときの案内は今までどおり", "未配備" in tv.edit_note())
tv.topology.reload(force=True)
_rows = {}
for _y, _dev in re.findall(r'<rect x="[^"]*" y="(-?\d+)"[^>]*><title>(\S+) ', tv.topology_svg()):
    _rows.setdefault(int(_y), []).append(_dev)
check("Web の図（静的データ）の段は上から Spine / Leaf（s-leaf を a-leaf と同じ段に置く。ROW_OF）/ TRex",
      [sorted(v) for _, v in sorted(_rows.items())]
      == [["dc1-spine-01", "dc1-spine-02"], ["dc1-a-leaf-01", "dc1-a-leaf-02", "dc1-s-leaf-01", "dc1-s-leaf-02"], ["dc1-trex-01"]])

print(f"\n{passed} 項目すべて通過")
