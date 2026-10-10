"""機能 WORKFLOW（IaC/terraform/aws-managed/workflow、app/temporal/（rules / awsio / worker）、app/agentcore/proposals.py、app/agentcore/mcp_client.py、app/gateway/）の模擬テスト。
AWS にも Temporal にも触れない。temporalio と boto3 を差し替えて 3 つのモジュールを読み、純粋な関数（プロンプト・JSON の読み取り・
許可リスト・アラート（SNS → SQS）の読み取りと起こす判定 = rules）と AWS 呼び出しの形（awsio）、ワークフローと starter の振る舞い（worker）、
proposals.decide の条件、mcp_client の応答の読み取り、
tools.json と Python の TOOL_SPECS の一致、Terraform と ops スクリプトのつながりを見る。実行は python3 tests/test_workflow.py（依存は無い）。"""
import ast, contextlib, io, json, os, re, sys, types

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TF_DIR = os.path.join(ROOT, "IaC", "terraform", "aws-managed", "workflow")

passed = 0
def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)

def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()

def read_ops(name):  # ops/up.sh / down.sh は、読んでいる共通の関数（ops/common.sh と ops/<name>-common.sh。OSS 版の ops/oss/ と共通）とつないで見る
    return read("ops", "common.sh") + read("ops", f"{name}-common.sh") + read("ops", f"{name}.sh")

# ---- 差し替え: boto3 / botocore（呼ばれた内容を記録する）
calls = []
class ClientError(Exception):
    def __init__(self, code="Err", msg=""):
        super().__init__(msg or code)
        self.response = {"Error": {"Code": code, "Message": msg}}
class BotoCoreError(Exception):
    pass
def cyrows(*r):
    """Neptune Analytics の execute_query の答え（payload は読み切りのストリームなので、呼ばれるたびに作る）"""
    return lambda **kw: {"payload": io.BytesIO(json.dumps({"results": list(r)}).encode())}
fake ={"execute_query": cyrows(), "get_parameter": ClientError("ParameterNotFound")}
clients = []  # boto3.client に渡した (name, kw)
class FakeClient:
    def __init__(self, name):
        self.name = name
    def __getattr__(self, op):
        def call(**kw):
            calls.append((self.name, op, kw))
            r = fake.get(op)
            if isinstance(r, Exception):
                raise r
            if callable(r):
                return r(**kw)
            return r if r is not None else {}
        return call
boto3 = types.ModuleType("boto3")
boto3.client = lambda name, **kw: clients.append((name, kw)) or FakeClient(name)
boto3.Session = lambda region_name=None: types.SimpleNamespace(get_credentials=lambda: None)
botocore = types.ModuleType("botocore"); botocore_exc = types.ModuleType("botocore.exceptions")
botocore_exc.ClientError = ClientError; botocore_exc.BotoCoreError = BotoCoreError
botocore_auth = types.ModuleType("botocore.auth"); botocore_auth.SigV4Auth = object
botocore_req = types.ModuleType("botocore.awsrequest"); botocore_req.AWSRequest = object
botocore_cfg = types.ModuleType("botocore.config"); botocore_cfg.Config = lambda **kw: kw
sys.modules.update({"boto3": boto3, "botocore": botocore, "botocore.exceptions": botocore_exc,
                    "botocore.auth": botocore_auth, "botocore.awsrequest": botocore_req, "botocore.config": botocore_cfg})

# ---- 差し替え: temporalio（デコレータは素通し）
def _passthrough(*a, **k):
    if len(a) == 1 and callable(a[0]) and not k:
        return a[0]
    return lambda f: f
t_activity = types.ModuleType("temporalio.activity"); t_activity.defn = _passthrough
t_workflow = types.ModuleType("temporalio.workflow")
t_workflow.defn = _passthrough; t_workflow.run = _passthrough; t_workflow.signal = _passthrough
t_workflow.execute_activity = None; t_workflow.wait_condition = None; t_workflow.now = None; t_workflow.info = None
# worker.py は自作モジュールを workflow.unsafe.imports_passed_through() で囲んで読む（Temporal のサンドボックス対策）。
# 素通しの context manager を置いておかないと import の時点で落ちる
t_workflow.unsafe = types.SimpleNamespace(imports_passed_through=contextlib.nullcontext)
t_client = types.ModuleType("temporalio.client"); t_client.Client = object; t_client.WorkflowFailureError = Exception
t_common = types.ModuleType("temporalio.common"); t_common.RetryPolicy = lambda **k: k
class ActivityError(Exception):
    def __init__(self, msg="", cause=None):
        super().__init__(msg)
        self.cause = cause
class ApplicationError(Exception):
    def __init__(self, msg="", non_retryable=False):
        super().__init__(msg)
        self.non_retryable = non_retryable
class WorkflowAlreadyStarted(Exception):
    pass
t_exc = types.ModuleType("temporalio.exceptions")
t_exc.WorkflowAlreadyStartedError = WorkflowAlreadyStarted; t_exc.ActivityError = ActivityError; t_exc.ApplicationError = ApplicationError
t_worker = types.ModuleType("temporalio.worker"); t_worker.Worker = object
class RPCError(Exception):
    def __init__(self, msg="", status=None):
        super().__init__(msg)
        self.status = status
t_service = types.ModuleType("temporalio.service"); t_service.RPCError = RPCError
t_service.RPCStatusCode = types.SimpleNamespace(NOT_FOUND="NOT_FOUND", UNAVAILABLE="UNAVAILABLE")
t_root = types.ModuleType("temporalio")
sys.modules.update({"temporalio": t_root, "temporalio.activity": t_activity, "temporalio.workflow": t_workflow,
                    "temporalio.client": t_client, "temporalio.common": t_common, "temporalio.exceptions": t_exc,
                    "temporalio.worker": t_worker, "temporalio.service": t_service})

os.environ.update({"NEPTUNE_GRAPH_ID": "g-abc1234567", "AUDIT_TABLE_BUCKET_ARN": "arn:aws:s3tables:ap-northeast-1:123456789012:bucket/audit",
                   "AUDIT_NAMESPACE": "nwc", "AGENT_RUNTIME_ARN": "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:runtime/x",
                   "LAB_INSTANCE_ID": "i-0123456789abcdef0", "PARAM_PREFIX": ""})
sys.path.insert(0, os.path.join(ROOT, "app", "temporal"))
sys.path.insert(0, os.path.join(ROOT, "app", "agentcore"))
sys.path.insert(0, os.path.join(ROOT, "app", "gateway"))
import worker  # noqa: E402 - Temporal のワークフローとアクティビティ
import awsio  # noqa: E402 - 環境変数と AWS 呼び出し
import rules  # noqa: E402 - 判断だけの純粋関数
import proposals  # noqa: E402
import mcp_client  # noqa: E402
import topology  # noqa: E402
import evidence  # noqa: E402
import handler  # noqa: E402

# ---- app/temporal/rules.py の純粋な関数
# アラート 1 件（rules.alerts_from_message が SQS の本文から作る形）
anomaly = {"anomaly_id": "hq-ce-01#link_down#eth1", "device_id": "hq-ce-01", "kind": "link_down", "target": "eth1", "status": "firing",
           "first_seen": 1700000000, "detail": "ifOperStatus down", "source": "grafana"}
prompt = rules.build_prompt(anomaly)
check("プロンプトに機器・種別・対象と、発生の時刻（JST）が入る",
      all(s in prompt for s in ("hq-ce-01", "link_down", "eth1", "first_seen_jst=2023-11-15 07:13:20")) and rules.jst(0) == "" and rules.jst(None) == "")
check("プロンプトはまず root_cause で根本原因かどうかを確かめさせる", "まず root_cause" in prompt)
# ---- 事前チェック（2026-10-04）
import asyncio, inspect  # noqa: E402
check("impact は app/agentcore/topology.py と app/temporal/rules.py で同じ（ワーカーのイメージには app/agentcore/ が入らないので 2 か所に置く）",
      inspect.getsource(rules.impact) == inspect.getsource(topology.impact) and rules.END_ROLES == topology.END_ROLES == ("trex",))
check("事前チェックの対応表は許可リストの処置を全部持つ", set(rules.ACTION_CHANGES) == set(rules.ALLOWED_ACTIONS))
_pd = [{"device_id": x, "status": None} for x in ("dc1-a-leaf-01", "dc1-spine-01", "dc1-spine-02")]
_pl = [{"a": "dc1-a-leaf-01", "a_if": "ethernet-1/1", "b": "dc1-spine-01", "b_if": "ethernet-1/3", "status": "DOWN"},
       {"a": "dc1-a-leaf-01", "a_if": "ethernet-1/2", "b": "dc1-spine-02", "b_if": "ethernet-1/3", "status": "UP"},
       {"a": "dc1-spine-01", "a_if": "ethernet-1/9", "b": "dc1-spine-02", "b_if": "ethernet-1/9", "status": None}]
pc = rules.precheck("heal-main", _pd, _pl)
check("heal-main の事前チェックは「上げる」と仮定して問題なし、冗長が戻る機器を出す",
      pc["verdict"] == "ok" and pc["text"].startswith("【問題なし】dc1-a-leaf-01#ethernet-1/1 を上げると仮定") and "冗長が戻る機器: dc1-a-leaf-01" in pc["text"])
check("check は何も変えないので問題なし、none は空", rules.precheck("check", _pd, _pl)["verdict"] == "ok" and rules.precheck("none", _pd, _pl) == {"verdict": "", "text": ""})
check("対象の回線がグラフに無ければ「確認できず」", rules.precheck("heal-main", _pd, _pl[1:])["verdict"] == "unknown" and "【確認できず】" in rules.precheck("heal-main", _pd, _pl[1:])["text"])
check("対応表に無い処置は「確認できず」", rules.precheck("reboot", _pd, _pl)["verdict"] == "unknown")
# 総当たり: 静的データ（7 台・12 本）で spine 2 台の状態 4 通り × 回線の DOWN の組み合わせ 2^12 通り。上げるだけの heal-main は孤立も冗長切れも出さない（027）
_hm_devs, _hm_links = topology.load_static()
_hm_spines = [d for d in _hm_devs if d["device_id"] in ("dc1-spine-01", "dc1-spine-02")]
_hm_seen = {}
for _hm_links_down in range(1 << len(_hm_links)):
    for _i, _x in enumerate(_hm_links):
        _x["status"] = "DOWN" if _hm_links_down >> _i & 1 else "UP"
    for _hm_spines_down in range(1 << len(_hm_spines)):
        for _i, _x in enumerate(_hm_spines):
            _x["status"] = "DOWN" if _hm_spines_down >> _i & 1 else None
        _v = rules.precheck("heal-main", _hm_devs, _hm_links)["verdict"]
        _hm_seen[_v] = _hm_seen.get(_v, 0) + 1
check("heal-main の事前チェックは、spine 2 台の状態 4 通り × 回線 12 本の DOWN の組み合わせ 2^12 通りのどれでも問題なし（危険にも注意にもならない）",
      len(_hm_links) == 12 and len(_hm_spines) == 2 and _hm_seen == {"ok": 16384})
# 孤立の規則を rules.impact で直接縛る（上げるだけの総当たりは同点を作らないので、本文の一致の検査だけに頼らない。027 のセルフレビュー）
def _toy(ids, links, changes):
    return rules.impact([{"device_id": x, "role": "trex" if x.startswith("t") else None} for x in ids],
                        [{"a": a, "a_if": ai, "b": b, "b_if": bi, "status": st} for a, ai, b, bi, st in links], changes)
_r = _toy("abcd", [("a", "1", "b", "1", "UP"), ("b", "2", "c", "1", "UP"), ("c", "2", "d", "1", "UP")], [{"op": "device_down", "target": "b"}])
check("impact: 1 本道 a–b–c–d で b を落とすと、生き残りの {a} と {c, d} のうち唯一いちばん大きい c–d が本流で、a だけが孤立（落とした b は出さない）",
      _r["verdict"] == "danger" and _r["newly_isolated"] == ["a"])
_r = _toy("abcd", [("a", "1", "b", "1", "UP"), ("b", "2", "c", "1", "UP"), ("c", "2", "d", "1", "UP")], [{"op": "link_down", "target": "b#2"}])
check("impact: a–b–c–d の真ん中の回線を落として 2–2 に割れると、同点でどちらも本流にせず 4 台とも孤立",
      _r["verdict"] == "danger" and _r["newly_isolated"] == ["a", "b", "c", "d"])
_r = _toy("abct", [("a", "1", "b", "1", "UP"), ("b", "2", "c", "1", "UP"), ("t", "1", "a", "2", "UP"), ("t", "2", "c", "2", "UP")], [{"op": "device_down", "target": "a"}])
check("impact: 端 t が a と c につながる a–b–c で a を落としても、t は本流 b–c の c につながったままなので孤立に出ない",
      _r["newly_isolated"] == [] and "t" not in _r["isolated_after"])
_r = _toy("abct", [("a", "1", "b", "1", "UP"), ("b", "2", "c", "1", "UP"), ("t", "1", "a", "2", "UP")], [{"op": "device_down", "target": "a"}])
check("impact: 端 t が a にだけつながる a–b–c で a を落とすと、t は本流 b–c のどれにもつながらないので孤立で danger",
      _r["verdict"] == "danger" and _r["newly_isolated"] == ["t"])
_r = _toy("abcdefg", [("a", "1", "b", "1", "UP"), ("b", "2", "c", "1", "UP"), ("c", "2", "d", "1", "UP"), ("d", "2", "e", "1", "UP"), ("f", "1", "g", "1", "UP")], [{"op": "link_down", "target": "f#1"}])
check("impact: 本流 a–e から切れている島 f–g の中の回線を落とすと、島が割れて同点なので f と g が孤立で danger（すでに本流に無い機器でも、かたまりが割れれば出す）",
      _r["verdict"] == "danger" and _r["newly_isolated"] == ["f", "g"])
_g = []
def _fake_cypher(q, **params):
    _g.append(q)
    if "(n:device)" in q:
        return [{"id": "dc1-a-leaf-01", "status": "ALARM", "maintenance": True, "role": "leaf"}, {"id": "dc1-spine-01", "status": None, "maintenance": None}]
    return [{"a": "dc1-a-leaf-01", "b": "dc1-spine-01", "a_if": "ethernet-1/1", "b_if": "ethernet-1/3", "status": "DOWN"}]
_gs, awsio.cypher = awsio.cypher, _fake_cypher
check("awsio.read_topology は機器（id・status・maintenance・role）と回線（両端・IF・status）を読む",
      awsio.read_topology() == ([{"device_id": "dc1-a-leaf-01", "status": "ALARM", "maintenance": True, "role": "leaf"},
                                 {"device_id": "dc1-spine-01", "status": None, "maintenance": False, "role": None}],
                                [{"a": "dc1-a-leaf-01", "b": "dc1-spine-01", "a_if": "ethernet-1/1", "b_if": "ethernet-1/3", "status": "DOWN"}])
      and _g == ["MATCH (n:device) RETURN id(n) AS id, n.status AS status, n.maintenance AS maintenance, n.role AS role",
                 "MATCH (a)-[l:link]->(b) RETURN id(a) AS a, id(b) AS b, l.a_if AS a_if, l.b_if AS b_if, l.status AS status"])
check("read_topology の機器の openCypher は role を返す（n.role AS role。rules.impact が END_ROLES の TRex を端として扱うのに使う）",
      "n.role AS role" in _g[0] and all("role" in d for d in awsio.read_topology()[0]))
# read_topology の形のまま rules.impact に渡す: TRex（role trex）が 2 台の leaf につながっていても、leaf の Spine への最後の回線を落とせば孤立と出る
_tp = {"(n:device)": [{"id": "dc1-a-leaf-01", "role": "leaf"}, {"id": "dc1-a-leaf-02", "role": "leaf"}, {"id": "dc1-spine-01", "role": "spine"}, {"id": "dc1-trex-01", "role": "trex"}],
       "link": [{"a": "dc1-a-leaf-01", "a_if": "ethernet-1/1", "b": "dc1-spine-01", "b_if": "ethernet-1/1"}, {"a": "dc1-a-leaf-02", "a_if": "ethernet-1/1", "b": "dc1-spine-01", "b_if": "ethernet-1/2"},
                {"a": "dc1-trex-01", "a_if": "eth1", "b": "dc1-a-leaf-01", "b_if": "ethernet-1/10"}, {"a": "dc1-trex-01", "a_if": "eth2", "b": "dc1-a-leaf-02", "b_if": "ethernet-1/10"}]}
awsio.cypher = lambda q, **params: _tp["(n:device)" if "(n:device)" in q else "link"]
_imp = rules.impact(*awsio.read_topology(), [{"op": "link_down", "target": "dc1-a-leaf-01#ethernet-1/1"}])
check("read_topology が返す role で、rules.impact は TRex を中継にしない（leaf の Spine への最後の回線を落とすと leaf が孤立する）",
      _imp["newly_isolated"] == ["dc1-a-leaf-01"] and _imp["verdict"] == "danger")
awsio.cypher = _gs
_ask, _rt = awsio.ask_agent, awsio.read_topology
awsio.ask_agent = lambda prompt: '{"cause": "c", "action": "heal-main", "reason": "r"}'
awsio.read_topology = lambda: (_pd, _pl)
f = asyncio.run(worker.investigate({"device_id": "dc1-a-leaf-01", "kind": "link_down", "target": "ethernet-1/1"}))
check("investigate は処置の事前チェックを finding に付ける", f["action"] == "heal-main" and f["precheck_verdict"] == "ok" and f["precheck"].startswith("【問題なし】"))
def _boom():
    raise RuntimeError("neptune down")
awsio.read_topology = _boom
f = asyncio.run(worker.investigate({"device_id": "dc1-a-leaf-01", "kind": "link_down", "target": "ethernet-1/1"}))
check("トポロジを読めなくても調査は落とさず、「確認できず」を付ける", f["action"] == "heal-main" and f["precheck_verdict"] == "unknown" and "neptune down" in f["precheck"])
awsio.ask_agent = lambda prompt: '{"cause": "c", "action": "none", "reason": "r"}'
f = asyncio.run(worker.investigate({"device_id": "x", "kind": "link_down", "target": "y"}))
check("処置が none ならトポロジを読まず、事前チェックは空", f["precheck"] == "" and f["precheck_verdict"] == "")
awsio.ask_agent, awsio.read_topology = _ask, _rt
# ---- 保守中（Nautobot の Status が Maintenance → Neptune の maintenance。2026-10-04）
_al = {"device_id": "dc1-a-leaf-01", "kind": "link_down", "target": "ethernet-1/1"}
_m = lambda *names: [dict(d, maintenance=d["device_id"] in names) for d in _pd]
check("保守中でなければ止めない", rules.maintenance_hold(_al, _m(), _pl) == [])
check("アラートの機器が保守中なら止める", rules.maintenance_hold(_al, _m("dc1-a-leaf-01"), _pl) == ["dc1-a-leaf-01"])
check("回線の相手が保守中でも止める（相手を止めればこちらの回線が落ちる）", rules.maintenance_hold(_al, _m("dc1-spine-01"), _pl) == ["dc1-spine-01"])
check("別の回線の相手が保守中なら止めない", rules.maintenance_hold(_al, _m("dc1-spine-02"), _pl) == [])
check("プロンプトは直前の構成変更を recent_changes で見させる", "recent_changes（Nautobot の変更履歴）" in prompt)
check("承認タブの詳細は事前チェックを出す", '("事前チェック", "precheck")' in read("app", "dashboard", "incident_view.py"))
check("プロンプトは JSON 1 個を求め、action の 3 択を示す", '"action"' in prompt and "heal-main | check | none" in prompt)
check("応答の中の JSON を拾う（前後に文があっても）",
      rules.parse_agent_json('確認しました。\n{"cause": "eth1 が down", "action": "heal-main", "reason": "主回線"}\n以上')
      == {"cause": "eth1 が down", "action": "heal-main", "reason": "主回線"})
check("JSON が無ければ action=none で本文を理由に残す", rules.parse_agent_json("わかりません")["action"] == "none"
      and rules.parse_agent_json("わかりません")["reason"] == "わかりません")
check("壊れた JSON でも落ちない", rules.parse_agent_json("{bad json")["action"] == "none")
check("cause は 1000 字で切る", len(rules.parse_agent_json(json.dumps({"cause": "x" * 5000}))["cause"]) == 1000)
check("heal-main は sudo lab heal-main", rules.normalize_action("heal-main") == ("heal-main", "sudo lab heal-main"))
check("check は sudo lab check", rules.normalize_action("check") == ("check", "sudo lab check"))
check("許可リストに無い処置は none でコマンド空（rm -rf / も fail-main も）",
      rules.normalize_action("rm -rf /") == ("none", "") and rules.normalize_action("fail-main") == ("none", "")
      and rules.normalize_action("") == ("none", ""))
check("ALLOWED_ACTIONS は app/containerlab/lab.sh のサブコマンド", all(f"  {a})" in read("app", "containerlab", "lab.sh") for a in rules.ALLOWED_ACTIONS))
check("firing の link_down で、同じ発生の修復案が無ければ起こす", rules.should_start(anomaly, None) and rules.should_start(anomaly, {}))
check("同じ発生の修復案がもうあれば起こさない（Grafana は同じ starts_at を repeat_interval ごとに送り直す）",
      not rules.should_start(anomaly, {"first_seen": 1700000000, "status": "verified"}))
check("anomaly_id が無ければ起こさない", not rules.should_start({}, None) and not rules.should_start({**anomaly, "anomaly_id": ""}, None))
check("link_down 以外（trap / bgp_down / isis_down）は起こさない",
      not any(rules.should_start({**anomaly, "kind": k}, None) for k in ("trap", "bgp_down", "isis_down")) and rules.START_KINDS == {"link_down"})
check("resolved と、status の無いものは起こさない",
      not rules.should_start({**anomaly, "status": "resolved"}, None) and not rules.should_start({k: v for k, v in anomaly.items() if k != "status"}, None))
# ワークフローの id に発生の時刻を入れると、starts_at の違う Grafana と Splunk の同じ障害が別のワークフローになる
check("ワークフロー id は異常ごと（investigate-<anomaly_id>）、修復案の id は発生ごと（<anomaly_id>#<first_seen>）",
      rules.workflow_id("a#b#c") == "investigate-a#b#c" and rules.proposal_id("a#b#c", 5) == "a#b#c#5"
      and rules.proposal_id("a#b#c", None) == "a#b#c#0" and rules.proposal_id("a#b#c", "7") == "a#b#c#7"
      and rules.anomaly_id("r1", "link_down", "eth1") == "r1#link_down#eth1")
_alert = {"status": "firing", "device_id": "hq-ce-01", "kind": "link_down", "target": "eth1", "detail": "ifOperStatus down", "starts_at": 1700000000}
_body = json.dumps({"source": "grafana", "alerts": [_alert]})
check("alerts_from_message は SNS に publish された JSON をアラートの list にする（anomaly_id = 機器#種類#対象、first_seen = starts_at）",
      rules.alerts_from_message(_body) == [anomaly])
check("SNS の封筒（raw message delivery でない配り方）でも中身を読む",
      rules.alerts_from_message(json.dumps({"Type": "Notification", "Message": _body})) == [anomaly]
      and rules.alerts_from_message(json.dumps({"Type": "Notification", "Message": "garbage"})) == [])
check("読めない本文・alerts が list でない本文は []",
      rules.alerts_from_message("garbage") == [] and rules.alerts_from_message("") == [] and rules.alerts_from_message("[1]") == []
      and rules.alerts_from_message(json.dumps({"alerts": "x"})) == [] and rules.alerts_from_message(None) == [])
_many = rules.alerts_from_message(json.dumps({"source": "splunk", "alerts": [
    {**_alert, "device_id": "DC1-A-Leaf-01.example.net", "status": "RESOLVED", "starts_at": "1700000001.7"},
    {**_alert, "device_id": "172.20.20.99", "kind": "trap", "target": ".1.3.6.1.4.1.1", "starts_at": None},
    {**_alert, "device_id": ""}, {**_alert, "kind": ""}, {**_alert, "status": "pending"}, "x", {**_alert, "detail": "d" * 5000}]}), now=42)
check("機器名は小文字の短い名前に（IPv4 はそのまま）、status は小文字に、starts_at が無ければ now、detail は 1000 字で切る",
      [(a["device_id"], a["status"], a["first_seen"]) for a in _many]
      == [("dc1-a-leaf-01", "resolved", 1700000001), ("172.20.20.99", "firing", 42), ("hq-ce-01", "firing", 1700000000)]
      and _many[1]["anomaly_id"] == "172.20.20.99#trap#.1.3.6.1.4.1.1" and len(_many[2]["detail"]) == 1000
      and all(a["source"] == "splunk" for a in _many))
check("形の合わない要素（機器か種類が無い・status が firing / resolved でない・dict でない）は捨てる", len(_many) == 3)
_many_body = json.dumps({"source": "splunk", "alerts": [_alert, {**_alert, "device_id": ""}, "x"]})
check("alert_count は alerts の要素を形にかかわらず数える（alerts_from_message との差が捨てた件数。封筒も開け、読めない本文は 0）",
      rules.alert_count(_many_body) == 3 and len(rules.alerts_from_message(_many_body)) == 1
      and rules.alert_count(json.dumps({"Type": "Notification", "Message": _many_body})) == 3
      and rules.alert_count("garbage") == 0 and rules.alert_count(None) == 0 and rules.alert_count(json.dumps({"alerts": "x"})) == 0)
# 送り手 2 つ（Grafana のテンプレートと Splunk のアラートアクション）が同じ形で publish しているか
_sns_py = read("app", "splunk", "nwc_alerts", "bin", "nwc_sns.py")
_gf_yaml = read("app", "grafana", "provisioning", "alerting", "nwc.yaml")
check("Splunk のアラートアクションと Grafana のテンプレートは同じ 6 つの項目を出す",
      all(f'"{k}"' in _sns_py and f'"{k}"' in _gf_yaml for k in ("status", "device_id", "kind", "target", "detail", "starts_at"))
      and '"source": "splunk"' in _sns_py and '"source":"grafana"' in _gf_yaml.replace('": "', '":"'))
# rules.py に boto3 / temporalio を持ち込むと、このテストも Temporal のサンドボックスも動かなくなる（分割の理由そのもの）
check("rules.py は標準ライブラリ（json / re / datetime）しか読まない",
      set(re.findall(r"^(?:import|from) (\w+)", read("app", "temporal", "rules.py"), re.M)) == {"json", "re", "datetime"})

# ---- app/temporal/awsio.py の AWS 呼び出し（差し替えで記録）
gq = lambda: [kw["queryString"] for n, op, kw in calls if op == "execute_query"]
gp = lambda: [kw.get("parameters", {}) for n, op, kw in calls if op == "execute_query"]
check("Gremlin の組み立て（_q / _un / gremlin）はもう無い（値は openCypher のパラメータで渡すので、エスケープが要らない）",
      not any(hasattr(awsio, n) for n in ("_q", "_un", "gremlin", "NEPTUNE_ENDPOINT")) and "execute_gremlin_query" not in read("app", "temporal", "awsio.py"))
# 修復案の頂点（label proposal）は 2026-10-05 にやめた。修復案は S3 Tables の proposal_events だけ
_awsio_src = read("app", "temporal", "awsio.py")
check("awsio は修復案を Neptune に読み書きしない（read_proposal / write_proposal / update_proposal も :proposal も無い）",
      not any(hasattr(awsio, n) for n in ("read_proposal", "write_proposal", "update_proposal")) and ":proposal" not in _awsio_src)
# Neptune はトポロジだけにする（2026-10-02）。異常の「いま」は Temporal のワークフローとシグナルが持つ
check("awsio は異常の頂点（label anomaly）を読まない",
      not hasattr(awsio, "read_anomaly") and not hasattr(awsio, "list_open_anomalies") and ":anomaly" not in _awsio_src)
calls.clear(); clients.clear(); awsio._cache.pop("neptune", None)
fake["execute_query"] = cyrows({"id": "hq-ce-01", "status": "DOWN", "maintenance": True, "role": "ce"})
awsio.read_topology()
check("Neptune Analytics は neptune-graph の execute_query（openCypher、グラフ ID 指定、endpoint_url なし）で読む",
      gq()[0] == "MATCH (n:device) RETURN id(n) AS id, n.status AS status, n.maintenance AS maintenance, n.role AS role"
      and calls[-1][2]["graphIdentifier"] == "g-abc1234567" and calls[-1][2]["language"] == "OPEN_CYPHER"
      and clients[-1][0] == "neptune-graph" and "endpoint_url" not in clients[-1][1])
fake["execute_query"] = cyrows()
# 修復案の「いま」は proposal_id ごとに seq が最大の行（PyIceberg の scan は差し替え）
_pid = "hq-ce-01#link_down#eth1#1700000000"
_scanned, _scan0 = [], awsio._scan
_srows = [{"proposal_id": _pid, "seq": 1, "status": "pending", "event_time": 10},
          {"proposal_id": _pid, "seq": 2, "status": "approved", "event_time": 20, "decided_by": "first"},
          {"proposal_id": _pid, "seq": 2, "status": "approved", "event_time": 21, "decided_by": "retry"},
          {"proposal_id": "hq-ce-01#link_down#eth1#1600000000", "seq": 4, "status": "verified", "event_time": 5}]
awsio._scan = lambda column, value: _scanned.append((column, value)) or [r for r in _srows if column != "proposal_id" or r["proposal_id"] == value]
check("latest_proposal は proposal_id で絞って読み、seq が最大の行（同じ seq なら event_time が遅いほう）を返す。無ければ {}",
      awsio.latest_proposal(_pid)["decided_by"] == "retry" and _scanned[-1] == ("proposal_id", _pid) and awsio.latest_proposal("x#1") == {})
check("anomaly_proposals は anomaly_id で絞って読み、修復案ごとの最新の行を返す",
      {k: v["status"] for k, v in awsio.anomaly_proposals("hq-ce-01#link_down#eth1").items()}
      == {_pid: "approved", "hq-ce-01#link_down#eth1#1600000000": "verified"} and _scanned[-1] == ("anomaly_id", "hq-ce-01#link_down#eth1"))
awsio._scan = _scan0
import datetime as _dt  # noqa: E402
check("from_table_rows は PyIceberg の datetime を epoch 秒に戻し、ほかはそのまま",
      awsio.from_table_rows([{"event_time": _dt.datetime(2023, 11, 14, 22, 13, 20, tzinfo=_dt.timezone.utc), "seq": 2, "decided_at": None}])
      == [{"event_time": 1700000000, "seq": 2, "decided_at": None}])
check("_scan は値を EqualTo の式で渡す（文字列に埋めない）", "row_filter=EqualTo(column, value)" in _awsio_src)
# 証跡（S3 Tables の proposal_events）
cp = awsio.catalog_properties()
check("PyIceberg は S3 Tables の Iceberg REST に SigV4（署名名 s3tables）でつなぐ",
      cp["type"] == "rest" and cp["uri"] == "https://s3tables.ap-northeast-1.amazonaws.com/iceberg" and cp["rest.signing-name"] == "s3tables"
      and cp["rest.sigv4-enabled"] == "true" and cp["warehouse"] == os.environ["AUDIT_TABLE_BUCKET_ARN"])
_item = {"proposal_id": _pid, "anomaly_id": "hq-ce-01#link_down#eth1", "device_id": "hq-ce-01", "kind": "link_down", "target": "eth1",
         "first_seen": 1700000000, "source": "grafana", "alert_detail": "ifOperStatus down", "cause": "c", "action": "heal-main",
         "command": "sudo lab heal-main", "reason": "r", "agent_response": "{}", "precheck": "【問題なし】", "precheck_verdict": "ok",
         "workflow_id": "investigate-hq-ce-01#link_down#eth1", "run_id": "run-1", "created_at": 1700000100}
created = rules.proposal_event("created", _item, 1700000100)
check("created の行は 28 列を全部（PROPOSAL_EVENT_COLUMNS の順）持ち、seq が 1、status が pending、decided_at が None",
      list(created) == [n for n, _ in rules.PROPOSAL_EVENT_COLUMNS] and len(created) == 28 and created["seq"] == 1
      and created["status"] == "pending" and created["decided_at"] is None and created["event_id"] == f"{_pid}#created")
appr = rules.proposal_event("approved", created, 1700000200, "", {"decided_by": "山田 (web)", "decided_at": 1700000190})
check("前の行から approved の行を作ると seq が 2 で、修復案の項目（kind / target / reason / precheck / first_seen / created_at）は同じ",
      appr["seq"] == 2 and appr["status"] == "approved" and appr["decided_by"] == "山田 (web)" and appr["decided_at"] == 1700000190
      and all(appr[k] == created[k] for k in ("kind", "target", "reason", "precheck", "first_seen", "created_at", "workflow_id")))
ev = rules.proposal_event("applied", appr, 1700000300, "x" * 5000)
rows = awsio.audit_rows([created, ev], rules.PROPOSAL_EVENT_COLUMNS)
check("audit_rows は timestamptz を UTC の datetime に、seq を int に、None は None のまま、ほかは文字列にする",
      rows[1]["event_time"].isoformat() == "2023-11-14T22:18:20+00:00" and rows[1]["decided_by"] == "山田 (web)"
      and rows[0]["seq"] == 1 and rows[1]["seq"] == 3 and isinstance(rows[1]["seq"], int)
      and rows[0]["decided_at"] is None and rows[1]["decided_at"].isoformat() == "2023-11-14T22:16:30+00:00"
      and list(rows[0]) == [n for n, _ in rules.PROPOSAL_EVENT_COLUMNS])
check("proposal_event の event_id は <proposal_id>#<event>、seq は前の行の seq + 1、detail は 4000 字で切る",
      ev["event_id"] == f"{_pid}#applied" and ev["status"] == "applied" and ev["seq"] == 3 and len(ev["detail"]) == 4000)
check("latest_proposals は seq を数で比べる（文字列の \"10\" と 9）",
      rules.latest_proposals([{"proposal_id": "p", "seq": "10", "event_time": 1}, {"proposal_id": "p", "seq": 9, "event_time": 2}])["p"]["seq"] == "10")
try:
    rules.proposal_event("deleted", {}, 1); bad = False
except ValueError:
    bad = True
check("知らない出来事は ValueError（証跡の event を増やすときは PROPOSAL_EVENTS に足す）", bad)
_prev = {"proposal_id": "a#1", "status": "approved", "seq": 2, "decided_by": "山田 (web)", "decided_at": 1700000300, "action": "heal-main"}
_ig = rules.ignored_event(_prev, {"decision": "rejected", "decided_by": " O'Brien (web) ", "decided_at": 1700000305}, {"decision": "approved"}, 1700000400)
check("ignored_event は status と決めた人を直前の行のまま seq だけ進め、event_id は <proposal_id>#ignored#<届いた決定の種類>#<届いた決定の時刻>#<名前>"
      "（名前の ' はそのまま、前後の空白は落として入る）",
      (_ig["event"], _ig["status"], _ig["seq"], _ig["decided_by"], _ig["decided_at"], _ig["action"], _ig["event_time"])
      == ("ignored", "approved", 3, "山田 (web)", 1700000300, "heal-main", 1700000400)
      and _ig["event_id"] == "a#1#ignored#rejected#1700000305#O'Brien (web)"
      and _ig["detail"] == "却下（O'Brien (web)、2023-11-15 07:18:25）が届いたが、先に承認が決まっていた")
_ig2 = rules.ignored_event(_ig, {"decision": "approved", "decided_by": "O'Brien (web)", "decided_at": 1700000305}, {"decision": "approved"}, 1700000401)
check("同じ人が同じ秒に送った承認と却下は、ignored の event_id が別になる（event_id で重複を落としても 2 行とも残る）",
      _ig2["event_id"] == "a#1#ignored#approved#1700000305#O'Brien (web)" and _ig2["event_id"] != _ig["event_id"] and _ig2["seq"] == 4)
check("decision_key は decision・decided_by（前後の空白を落とす）・decided_at の組で、送った時刻が違えば別の決定",
      rules.decision_key({"decision": "approved", "decided_by": "x ", "decided_at": 5}) == rules.decision_key({"decision": "approved", "decided_by": "x", "decided_at": 5, "proposal_id": "z"})
      != rules.decision_key({"decision": "approved", "decided_by": "x", "decided_at": 6}))
# 決定のキュー（Web の承認タブ → worker）のメッセージ
def _dm(**k):
    return json.dumps({"type": "decision", "proposal_id": "a#1", "decision": "approved", "decided_by": "x (web)", "sent_at": 5, **k})
check("decision_from_message は決定の本文をシグナルの辞書にする（decided_at は sent_at）",
      rules.decision_from_message(_dm()) == {"proposal_id": "a#1", "decision": "approved", "decided_by": "x (web)", "decided_at": 5})
check("sent_at が無ければ now、decided_by は 64 字で切る",
      rules.decision_from_message(_dm(sent_at=None), now=9)["decided_at"] == 9
      and len(rules.decision_from_message(_dm(decided_by="y" * 100))["decided_by"]) == 64)
check("decision が approved / rejected でない、proposal_id が空や # の無い形なら None",
      all(rules.decision_from_message(_dm(**k)) is None for k in ({"decision": "maybe"}, {"decision": "applied"}, {"proposal_id": ""}, {"proposal_id": "nohash"})))
check("type が decision でない・アラートの JSON・読めない本文は None",
      rules.decision_from_message(_dm(type="alert")) is None and rules.decision_from_message(_body) is None
      and rules.decision_from_message("garbage") is None and rules.decision_from_message(None) is None)
check("anomaly_of は proposal_id の右端の # から後ろを外す、決定の本文はアラートとして読まれない",
      rules.anomaly_of(_pid) == "hq-ce-01#link_down#eth1" and rules.alerts_from_message(_dm()) == [])
check("status に出てくる出来事は全部 PROPOSAL_EVENTS にある", set(proposals.STATUSES) - {"pending"} <= set(rules.PROPOSAL_EVENTS))
check("append_proposal_events は空なら何もしない（pyiceberg を読まない）", awsio.append_proposal_events([], rules.PROPOSAL_EVENT_COLUMNS) is None)
# アラートの通知の履歴（S3 Tables の alert_events。書くのは app/graph/status_handler.py）
al = rules.alerts_from_message(json.dumps({"source": "grafana", "alerts": [
    {"status": "firing", "device_id": "dc1-a-leaf-01", "kind": "link_down", "target": "ethernet-1/1", "detail": "oper-state down", "starts_at": 1790000000},
    {"status": "resolved", "device_id": "?", "kind": "trap", "target": "?", "detail": ""}]}))
ae = [rules.alert_event(a, 1790000123.5) for a in al]
check("alert_event の event_id は <anomaly_id>#<source>#<status>#<starts_at の epoch 秒>",
      ae[0]["event_id"] == "dc1-a-leaf-01#link_down#ethernet-1/1#grafana#firing#1790000000" and ae[0]["anomaly_id"] == "dc1-a-leaf-01#link_down#ethernet-1/1")
check("alert_event の列は ALERT_EVENT_COLUMNS と同じ順、時刻は ISO 8601 の UTC（マイクロ秒と Z）",
      list(ae[0]) == [n for n, _ in rules.ALERT_EVENT_COLUMNS]
      and ae[0]["starts_at"] == "2026-09-21T14:13:20.000000Z" and ae[0]["received_at"] == "2026-09-21T14:15:23.500000Z"
      and ae[0]["detail"] == "oper-state down" and ae[0]["source"] == "grafana" and ae[0]["status"] == "firing")
check("starts_at の無い通知は starts_at を null、event_id の末尾を 0 にする（機器の無い通知も行にする）",
      ae[1]["starts_at"] is None and ae[1]["event_id"].endswith("#grafana#resolved#0") and ae[1]["device_id"] == "?")
check("ALERT_EVENT_COLUMNS の時刻は starts_at と received_at の 2 つで timestamptz",
      [n for n, t in rules.ALERT_EVENT_COLUMNS if t == "timestamptz"] == ["starts_at", "received_at"]
      and {t for _, t in rules.ALERT_EVENT_COLUMNS} == {"string", "timestamptz"})

class FakeBody:
    def __init__(self, data): self.data = data
    def read(self): return json.dumps(self.data).encode()
calls.clear()
fake["invoke_agent_runtime"] = {"response": FakeBody({"status": "success", "response": '{"cause":"c","action":"check","reason":"r"}'})}
clients.clear()
text = awsio.ask_agent("q")
check("ask_agent は読み取り 150 秒・botocore の再送なし（やり直しは Temporal に任せる）",
      clients[-1][0] == "bedrock-agentcore" and clients[-1][1]["config"] == {"read_timeout": 150, "connect_timeout": 10, "retries": {"max_attempts": 1}})
kw = calls[-1][2]
check("Runtime を InvokeAgentRuntime（qualifier DEFAULT、JSON の prompt、33 字以上の runtimeSessionId）で呼ぶ",
      calls[-1][:2] == ("bedrock-agentcore", "invoke_agent_runtime") and kw["qualifier"] == "DEFAULT"
      and json.loads(kw["payload"]) == {"prompt": "q"} and len(kw["runtimeSessionId"]) >= 33 and "action" in text)
fake["invoke_agent_runtime"] = {"response": FakeBody({"status": "error", "message": "x"})}
try:
    awsio.ask_agent("q"); bad = False
except RuntimeError:
    bad = True
check("Runtime が error を返したら例外（Temporal が再試行する）", bad)
# 分割しても worker.py からは awsio / rules 経由で全部に届く（Temporal のサンドボックスを通すため imports_passed_through で囲む）
check("worker.py は awsio / rules を imports_passed_through で読む",
      re.search(r"with workflow\.unsafe\.imports_passed_through\(\):\n\s*import awsio\n\s*import rules", read("app", "temporal", "worker.py")) is not None)
# 2026-10-02: パッチの切り出し位置を誤って定数とアクティビティがまるごと欠けた。temporalio を入れていない環境では import の確認が走らず気づけなかったので、形を見る
check("worker.py に定数・アクティビティ 5 本・@workflow.defn の付いたワークフロー・シグナル 2 本・starter がそろっている",
      [f.__name__ for f in worker.ACTIVITIES] == ["investigate", "put_proposal", "record_event", "record_ignored", "apply_on_lab"]
      and all(isinstance(getattr(worker, k), int) for k in ("APPROVAL_TIMEOUT_MINUTES", "VERIFY_TIMEOUT", "HOLD_MINUTES"))
      and worker.HOLD_OUTCOMES == ("rejected", "expired", "failed") and worker.TASK_QUEUE and worker.TEMPORAL_ADDRESS == "localhost:7233"
      and re.search(r"^@workflow\.defn\nclass InvestigateAnomaly:", read("app", "temporal", "worker.py"), re.M) is not None
      and read("app", "temporal", "worker.py").count("@activity.defn\n") == 5
      and len(re.findall(r"^    @workflow\.signal\n    def (decide|resolved)\(", read("app", "temporal", "worker.py"), re.M)) == 2
      and all(callable(getattr(worker, f)) for f in ("start_for", "resolve_for", "handle_message", "handle_decision", "starter_queue", "starter", "connect", "main")))
check("異常の頂点を見るアクティビティ（get_anomaly / still_open / anomaly_resolved）と、表を見る starter はもう無い",
      not any(hasattr(worker, f) for f in ("get_anomaly", "still_open", "anomaly_resolved", "starter_table", "POLL_INTERVAL", "VERIFY_ATTEMPTS", "VERIFY_INTERVAL")))
check("Neptune の修復案を見に行くアクティビティ（get_decision / record_decision / set_status）と、決定を待つ間隔 DECISION_POLL はもう無い（決定はシグナルで届く）",
      not any(hasattr(worker, f) for f in ("get_decision", "record_decision", "set_status", "DECISION_POLL")))

# ---- proposals.py（S3 Tables の proposal_events を Athena で読み、決定は SQS の決定のキューに送る。Athena と SQS の振る舞いは tests/test_agentcore.py）
# このテストは Athena の設定も決定のキューの URL も置かない（PARAM_PREFIX が空なので SSM も引かない）
calls.clear()
check("Athena の設定が無ければ一覧・1 件・承認とも「未配備」を返し、AWS を呼ばない（Neptune も見ない）",
      proposals.list_proposals() == {"error": proposals.NOT_DEPLOYED, "proposals": []} and proposals.get_proposal("a#1") == {}
      and proposals.decide("a#1", "approved", "web") == {"error": proposals.NOT_DEPLOYED} and calls == [])
check("proposals.py は Neptune（app/agentcore/graph.py）を読まない", "import graph" not in read("app", "agentcore", "proposals.py") and not hasattr(proposals, "graph"))

# エージェントのツール（読むだけ。2026-09-18）
check("ツールも未配備なら案内を返す", proposals.run_tool("list_proposals", {})["error"] == proposals.NOT_DEPLOYED and calls == [])
check("承認・却下はツールに出さない（人が画面の承認タブで決める）",
      set(proposals.TOOLS) == {"list_proposals"} and "承認や却下はこのツールではできない" in proposals.TOOL_SPECS[0]["toolSpec"]["description"])

# ---- mcp_client.py
check("JSON の応答はそのまま", mcp_client.parse_response("application/json", '{"result": {"tools": []}}') == {"result": {"tools": []}})
sse = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"tools___list_devices"}]}}\n\n'
check("SSE は data: 行の JSON-RPC を採る", mcp_client.parse_response("text/event-stream", sse)["result"]["tools"][0]["name"] == "tools___list_devices")
check("空の応答は {}", mcp_client.parse_response("application/json", "") == {})
specs, names = mcp_client.to_tool_specs([{"name": "tools___neighbors", "description": "d", "inputSchema": {"type": "object", "properties": {"device_id": {"type": "string"}}, "required": ["device_id"]}}])
check("Gateway の <target>___<tool> を短い名前にして Converse の toolSpec にする",
      specs[0]["toolSpec"]["name"] == "neighbors" and names == {"neighbors": "tools___neighbors"}
      and specs[0]["toolSpec"]["inputSchema"]["json"]["required"] == ["device_id"])
mcp_client._cache.update({"url": "", "checked": 0.0, "specs": [], "names": {}, "listed": 0.0})
check("Gateway の URL が無ければツール一覧は空（app.py はコンテナ内の関数に戻す）", mcp_client.tool_specs() == [] and not mcp_client.has("neighbors"))
check("Gateway に無いツールの call はエラーの辞書", "error" in mcp_client.call("neighbors", {}))

# ---- tools.json と Python の TOOL_SPECS
tools = json.loads(read("app", "gateway", "tools.json"))
py_specs = {s["toolSpec"]["name"]: s["toolSpec"] for s in topology.TOOL_SPECS + evidence.TOOL_SPECS + proposals.TOOL_SPECS}
check("tools.json の 13 個は topology / evidence / proposals の TOOL_SPECS と同じ名前（list_anomalies は 2026-10-02 にやめた）",
      {t["name"] for t in tools} == set(py_specs) and len(tools) == 13 and "list_anomalies" not in py_specs
      and not os.path.exists(os.path.join(ROOT, "app", "agentcore", "anomalies.py")))
check("evidence のツールは search_logs / query_metrics / query_history", {s["toolSpec"]["name"] for s in evidence.TOOL_SPECS} == {"search_logs", "query_metrics", "query_history"})
# query_history が読む列と Firehose が書く列（status Lambda の rules.alert_event）がずれると、SELECT が COLUMN_NOT_FOUND で落ちる
check("evidence.HISTORY_COLUMNS は rules.ALERT_EVENT_COLUMNS の列名と同じ順",
      evidence.HISTORY_COLUMNS == tuple(n for n, _ in rules.ALERT_EVENT_COLUMNS))
check("handler は topology / evidence / proposals のツールを名前で振り分ける",
      "MODULES = (topology, evidence, proposals)" in read("app", "gateway", "handler.py"))
for t in tools:
    js = py_specs[t["name"]]["inputSchema"]["json"]
    check(f"{t['name']} の引数と必須が Python と同じ",
          set(t["inputSchema"]["properties"]) == set(js.get("properties", {})) and set(t["inputSchema"].get("required", [])) == set(js.get("required", [])))
    check(f"{t['name']} の説明が Python と同じ", t["description"] == py_specs[t["name"]]["description"])
check("tools.json の型は string / integer だけ（Gateway の inline schema が受ける形）",
      all(p["type"] in ("string", "integer") for t in tools for p in t["inputSchema"]["properties"].values()))

# ---- app/gateway/handler.py
class Ctx:
    client_context = types.SimpleNamespace(custom={"bedrockAgentCoreToolName": "tools___list_devices"})
check("Lambda は client_context のツール名から <target>___ を外す", handler.tool_name(Ctx()) == "list_devices")
out = handler.handler({"site": "dc1"}, Ctx())
check("list_devices を静的トポロジで答える（devices.json / yaml のどちらでも）", isinstance(out, dict) and out.get("count", 0) >= 1)
check("知らないツールはエラーの辞書", "error" in handler.dispatch("nope", {}))

# ---- Terraform
tf = ""
tf_files = sorted(n for n in os.listdir(TF_DIR) if n.endswith(".tf"))
for name in tf_files:
    tf += read("IaC", "terraform", "aws-managed", "workflow", name) + "\n"
check("ファイルは versions / providers / variables / locals / proposals / iam / ecs / gateway / outputs",
      set(tf_files) == {"versions.tf", "providers.tf", "variables.tf", "locals.tf", "proposals.tf", "iam.tf", "ecs.tf", "gateway.tf", "events.tf", "outputs.tf"})
check("graph と analytics の state は try で読む（無くても apply できる）",
      '"${path.module}/../pipeline/graph/terraform.tfstate"' in tf and '"${path.module}/../pipeline/analytics/terraform.tfstate"' in tf
      and re.search(r'try\(data\.terraform_remote_state\.analytics', tf) is not None)
for root in ("base/core", "pipeline/lab", "base/ecr", "agent"):
    check(f"{root} の state をローカルから読む", f'"${{path.module}}/../{root}/terraform.tfstate"' in tf)
main_out = read("IaC", "terraform", "aws-managed", "base", "core", "outputs.tf")
lab_out = read("IaC", "terraform", "aws-managed", "pipeline", "lab", "outputs.tf"); ecr_out = read("IaC", "terraform", "aws-managed", "base", "ecr", "outputs.tf"); agent_out = read("IaC", "terraform", "aws-managed", "agent", "outputs.tf")
for out in ("vpc_id", "instance_subnet_id", "security_group_ids", "runtime_role_name", "web_role_name"):
    check(f"main の出力 {out} がある", f'output "{out}"' in main_out and f"outputs.{out}" in tf)
check("agent の出力 agent_runtime_arn を try で読み、無ければ precondition で止まる（IaC/terraform/aws-managed/agent を先に apply）",
      'output "agent_runtime_arn"' in agent_out and re.search(r'try\(data\.terraform_remote_state\.agent\.outputs\.agent_runtime_arn, ""\)', tf) is not None
      and 'condition     = local.runtime_arn != ""' in tf and "IaC/terraform/aws-managed/agent を先に apply" in tf)
graph_out = read("IaC", "terraform", "aws-managed", "pipeline", "graph", "outputs.tf"); analytics_out = read("IaC", "terraform", "aws-managed", "pipeline", "analytics", "outputs.tf")
check("graph の出力 graph_id / graph_arn を読む（Neptune Analytics。届く道は土台の neptune-graph-data のエンドポイント）",
      all(f'output "{o}"' in graph_out and f"outputs.{o}" in tf for o in ("graph_id", "graph_arn")) and "cluster_endpoint" not in tf and "neptune-db" not in tf)
check("analytics の出力（テーブルバケット・namespace・proposal_events）を読む",
      all(f'output "{o}"' in analytics_out and f"outputs.{o}" in tf for o in ("table_bucket_arn", "table_namespace", "proposal_events_table_name")))
check("stream の state は読まない（異常はアラートとして SQS から届く）", "pipeline/stream/terraform.tfstate" not in tf)
check("土台の出力 alerts_topic_arn を try で読み、無ければ購読の precondition で止まる（2026-10-02 より前の土台）",
      'output "alerts_topic_arn"' in main_out and re.search(r'alerts_topic_arn = try\(data\.terraform_remote_state\.main\.outputs\.alerts_topic_arn, ""\)', tf) is not None
      and 'condition     = local.alerts_topic_arn != ""' in tf)
check("lab の出力 lab_instance_id がある", 'output "lab_instance_id"' in lab_out and "outputs.lab_instance_id" in tf)
check("ecr の出力 worker_repository_url / temporal_repository_url / temporal_ui_repository_url がある",
      all(f'output "{o}"' in ecr_out and f"outputs.{o}" in tf for o in ("worker_repository_url", "temporal_repository_url", "temporal_ui_repository_url")))
check("ECS のタスクは Fargate の ARM64", 'cpu_architecture        = "ARM64"' in tf and '"FARGATE"' in tf)
# ---- Temporal の履歴は Nautobot の RDS（cycle 036。2026-10-10 までは start-dev で SQLite がコンテナの中にあり、タスクが入れ替わると消えた）
_ecs_tf = read("IaC", "terraform", "aws-managed", "workflow", "ecs.tf")
_containers = re.findall(r'^\s*name\s*=\s*"(temporal|ui|worker)"', _ecs_tf, re.M)
check(f"タスクは temporal / ui / worker の 3 コンテナ（いま: {_containers}）", _containers == ["temporal", "ui", "worker"])
_c_temporal = _ecs_tf.split('name      = "temporal"')[1].split('name      = "ui"')[0]
_c_ui = _ecs_tf.split('name      = "ui"')[1].split('name      = "worker"')[0]
_c_worker = _ecs_tf.split('name      = "worker"')[1]
_ecs_code = "\n".join(l.split("#")[0] for l in _ecs_tf.splitlines())  # コメントを除いた本文
_c_temporal_code = "\n".join(l.split("#")[0] for l in _c_temporal.splitlines())
check("temporal は start-dev も SQLite も使わない（command 無し。docker/images/temporal-server/ の entrypoint が temporal-server を起こす）",
      "start-dev" not in _ecs_code and "--db-filename" not in _ecs_code and re.search(r"^ {6}command\s*=", _c_temporal, re.M) is None
      and "local.temporal_image" in _c_temporal)
check("temporal は portMappings を持たない（7233〜7239 / 6933〜6939 はタスクの外に出さない）", "portMappings" not in _c_temporal_code and "containerPort" not in _c_temporal_code)
# DB 系の env はサーバーと init のタスクで共通（locals.tf の temporal_db_env。二重に書かない。cycle 042）
_locals_tf = read("IaC", "terraform", "aws-managed", "workflow", "locals.tf")
_tdb_block = re.search(r"\n  temporal_db_env = \[\n([\s\S]*?)\n  \]\n", _locals_tf)
_tdb_env = dict(re.findall(r'\{ name = "(\w+)", value = ([^}]+?) \}', _tdb_block.group(1))) if _tdb_block else {}
check(f"local.temporal_db_env は Nautobot の RDS にロール temporal で TLS でつなぐ 7 つ（NAUTOBOT_DB_ で始まる名前は無い。いま: {sorted(_tdb_env)}）",
      _tdb_env == {"POSTGRES_SEEDS": "local.nautobot_db_address", "DB_PORT": "tostring(local.nautobot_db_port)", "POSTGRES_USER": '"temporal"',
                   "DBNAME": '"temporal"', "VISIBILITY_DBNAME": '"temporal_visibility"', "SQL_TLS_ENABLED": '"true"', "SQL_HOST_VERIFICATION": '"false"'})
_tenv = dict(re.findall(r'\{ name = "(\w+)", value = ([^}]+?) \}', _c_temporal))
check(f"temporal の env は postgres12 と local.temporal_db_env と接続数・shard・bind で、NAUTOBOT_DB_ で始まる名前もパスワードも無い（いま: {sorted(_tenv)}）",
      _tenv.get("DB") == '"postgres12"' and _tenv.get("NUM_HISTORY_SHARDS") == '"4"' and _tenv.get("BIND_ON_IP") == '"0.0.0.0"'
      and _tenv.get("SQL_MAX_CONNS") == "tostring(var.temporal_sql_max_conns)" and _tenv.get("SQL_VIS_MAX_CONNS") == "tostring(var.temporal_visibility_max_conns)"
      and re.search(r'environment = concat\(\[\n\s*\{ name = "DB", value = "postgres12" \},\n\s*\], local\.temporal_db_env, \[\n', _c_temporal) is not None
      and not any(k.startswith("NAUTOBOT_DB_") for k in _tenv) and "POSTGRES_PWD" not in _tenv)
_secret_names = lambda s: re.findall(r'\{ name = "(\w+)", valueFrom = ', s)
_wf_td = _ecs_tf.split('resource "aws_ecs_task_definition" "workflow" {')[1].split("\n}\n")[0] if 'resource "aws_ecs_task_definition" "workflow" {' in _ecs_tf else ""
_wf_td_code = "\n".join(l.split("#")[0] for l in _wf_td.splitlines())
check(f"サーバーの temporal のコンテナの secrets は POSTGRES_PWD の 1 つだけで、サーバーのタスク定義のどこにも NAUTOBOT_DB_ の名前と master のパスワードの ARN が無い（cycle 042。いま: {_secret_names(_c_temporal_code)}）",
      _secret_names(_c_temporal_code) == ["POSTGRES_PWD"] and '{ name = "POSTGRES_PWD", valueFrom = local.temporal_db_password_arn }' in _c_temporal_code
      and _wf_td_code != "" and re.search(r'"NAUTOBOT_DB_\w*"', _wf_td_code) is None and "valueFrom = local.nautobot_db_password_arn" not in _wf_td_code)
check("temporal の healthCheck は namespace default の describe（HEALTHY = namespace まで出来た）。startPeriod は上限の 300（init のタスクを待つ間。cycle 042）",
      '"temporal operator namespace describe -n default --address 127.0.0.1:7233 >/dev/null 2>&1 || exit 1"' in _c_temporal
      and re.findall(r"startPeriod\s*=\s*(\d+)", _c_temporal_code) == ["300"] and re.search(r"retries\s*=\s*6", _c_temporal) is not None)
# 一回きりのタスク（cycle 042）。master のパスワードを持つのはこのタスク定義だけ
_init_td = _ecs_tf.split('resource "aws_ecs_task_definition" "init" {')[1].split("\n}\n")[0] if 'resource "aws_ecs_task_definition" "init" {' in _ecs_tf else ""
_init_code = "\n".join(l.split("#")[0] for l in _init_td.splitlines())
check("init のタスク定義は family <prefix>-workflow-init の Fargate ARM64（256 / 512）で、実行ロールはサーバーと同じ、タスクロールは無い",
      _init_code != "" and 'family                   = "${local.name_prefix}-workflow-init"' in _init_code
      and 'requires_compatibilities = ["FARGATE"]' in _init_code and 'network_mode             = "awsvpc"' in _init_code
      and re.search(r"^\s*cpu\s*=\s*256\s*$", _init_code, re.M) is not None and re.search(r"^\s*memory\s*=\s*512\s*$", _init_code, re.M) is not None
      and 'cpu_architecture        = "ARM64"' in _init_code
      and re.search(r"^\s*execution_role_arn\s*=\s*aws_iam_role\.execution\.arn\s*$", _init_code, re.M) is not None
      and re.search(r"execution_role_arn\s*=\s*aws_iam_role\.execution\.arn", _wf_td_code) is not None and "task_role_arn" not in _init_code)
_init_env = re.search(r"environment = concat\(local\.temporal_db_env, \[\n([\s\S]*?)\n\s*\]\)", _init_code)
check(f"init のコンテナは init 1 つで、サーバーと同じイメージを entryPoint /etc/temporal/init-rds.sh で起こし、env は local.temporal_db_env と NAUTOBOT_DB_USER（いま: {re.findall(r'^\s*name\s*=\s*\"(\w+)\"', _init_code, re.M)}）",
      re.findall(r'^\s*name\s*=\s*"(\w+)"', _init_code, re.M) == ["init"] and re.search(r"^\s*image\s*=\s*local\.temporal_image\s*$", _init_code, re.M) is not None
      and re.search(r"^\s*essential\s*=\s*true\s*$", _init_code, re.M) is not None
      and re.search(r'^\s*entryPoint\s*=\s*\["/etc/temporal/init-rds\.sh"\]\s*$', _init_code, re.M) is not None
      and _init_env is not None and re.findall(r'\{ name = "(\w+)", value = ([^}]+?) \}', _init_env.group(1)) == [("NAUTOBOT_DB_USER", '"nautobot"')]
      and re.search(r"^\s*command\s*=", _init_code, re.M) is None)
check(f"init のタスクの secrets は POSTGRES_PWD と NAUTOBOT_DB_PASSWORD の 2 つ（SSM の ARN。いま: {_secret_names(_init_code)}）",
      _secret_names(_init_code) == ["POSTGRES_PWD", "NAUTOBOT_DB_PASSWORD"]
      and '{ name = "POSTGRES_PWD", valueFrom = local.temporal_db_password_arn }' in _init_code
      and '{ name = "NAUTOBOT_DB_PASSWORD", valueFrom = local.nautobot_db_password_arn }' in _init_code)
check("init のタスクは healthCheck も portMappings も持たず、stopTimeout 120、ログは同じロググループの stream prefix init",
      "healthCheck" not in _init_code and "portMappings" not in _init_code and "containerPort" not in _init_code and "dependsOn" not in _init_code
      and re.findall(r"^\s*stopTimeout\s*=\s*(\S+)\s*$", _init_code, re.M) == ["120"]
      and "awslogs-group         = aws_cloudwatch_log_group.workflow.name" in _init_code and re.findall(r'awslogs-stream-prefix = "(\w+)"', _init_code) == ["init"]
      and 'resource "aws_ecs_service"' not in _init_td and _ecs_tf.count("aws_ecs_task_definition.init") == 0)
_wf_out = read("IaC", "terraform", "aws-managed", "workflow", "outputs.tf")
_v2 = lambda pat, s: (re.search(pat, s) or [None, None])[1]
_out_val = lambda n: _v2(rf'output "{n}" \{{[\s\S]*?\n  value\s*=\s*(.+)\n\}}', _wf_out)
check(f"workflow の出力に init_task_definition / task_subnet_id / task_security_group_id / init_logs_command（run_temporal_init が読む。サービスと同じサブネットと SG）",
      _out_val("init_task_definition") == "aws_ecs_task_definition.init.arn" and _out_val("task_subnet_id") == "local.subnet_id"
      and _out_val("task_security_group_id") == "local.workflow_sg_id"
      and _out_val("init_logs_command") == '"aws logs tail ${aws_cloudwatch_log_group.workflow.name} --region ${var.region} --log-stream-name-prefix init"'
      and "subnets          = [local.subnet_id]" in _ecs_tf and "security_groups  = [local.workflow_sg_id]" in _ecs_tf)
check("ui は temporalio/ui のミラーで essential = false、8233 だけを出し、127.0.0.1:7233 を temporal の HEALTHY の後に読む",
      "local.temporal_ui_image" in _c_ui and "essential = false" in _c_ui and re.findall(r'containerPort\s*=\s*(\d+)', _c_ui) == ["8233"]
      and '{ name = "TEMPORAL_ADDRESS", value = "127.0.0.1:7233" }' in _c_ui and '{ name = "TEMPORAL_UI_PORT", value = "8233" }' in _c_ui
      and '{ containerName = "temporal", condition = "HEALTHY" }' in _c_ui and '"ui"' in _c_ui)
check("worker は temporal の HEALTHY の後に起き、localhost:7233 につなぐ",
      '"localhost:7233"' in _c_worker and '{ containerName = "temporal", condition = "HEALTHY" }' in _c_worker and 'condition = "START"' not in _ecs_tf)
check("サービスは新しいタスクを先に立てる（min 100 / max 200。履歴は RDS にある）",
      "deployment_minimum_healthy_percent = 100" in _ecs_tf and "deployment_maximum_percent         = 200" in _ecs_tf)
check("どのコンテナにも initProcessEnabled が無い（PID 1 は temporal-server のイメージの tini。ECS の init は孤児しか回収せず、namespace を作る背景のプロセスの親は生きている temporal-server なので回収できない。init の /proc/1/environ には master のパスワードも残る。cycle 039）",
      "initProcessEnabled" not in _ecs_code)
# 起動中（entrypoint の手順 1〜3）に停止が来ると、trap は走っている手順（update-schema を含む）が返るまで待つ。既定の 30 秒で SIGKILL させない（cycle 040）
check(f"temporal の stopTimeout は 120（Fargate の上限。初回の setup-schema + update-schema の途中で SIGKILL しない。cycle 040。いま: {re.findall(r'stopTimeout\s*=\s*(\S+)', _c_temporal_code)}）",
      re.findall(r"^\s*stopTimeout\s*=\s*(\S+)\s*$", _c_temporal_code, re.M) == ["120"])
nautobot_out = read("IaC", "terraform", "aws-managed", "pipeline", "nautobot", "outputs.tf")
check("nautobot の state（db_address / db_port / db_password_parameter）を try で読み、無ければ precondition で止まる",
      '"${path.module}/../pipeline/nautobot/terraform.tfstate"' in tf
      and all(f'output "{o}"' in nautobot_out and f"outputs.{o}" in tf for o in ("db_address", "db_port", "db_password_parameter"))
      and re.search(r'nautobot_db_address\s*=\s*try\(data\.terraform_remote_state\.nautobot\.outputs\.db_address, ""\)', tf) is not None
      and 'condition     = local.nautobot_db_address != "" && local.nautobot_db_password_arn != ""' in _ecs_tf)
check("nautobot の出力にパスワードの値は無い（パラメータ名だけ）", "aws_ssm_parameter" not in nautobot_out.split('output "db_password_parameter"')[1].split("output")[0]
      and 'local.secret_parameters["db-password"]' in nautobot_out)
_iam_tf = read("IaC", "terraform", "aws-managed", "workflow", "iam.tf")
_iam_db = _iam_tf.split('resource "aws_iam_role_policy" "execution_db_passwords"')[1].split("\nresource ")[0] if "execution_db_passwords" in _iam_tf else ""
check("実行ロールは 2 つの DB のパスワード（SSM）だけを ssm:GetParameters で読める。サービスはそのポリシーの後に作る",
      'Action   = ["ssm:GetParameters"]' in _iam_db and "compact([local.temporal_db_password_arn, local.nautobot_db_password_arn])" in _iam_db
      and "aws_iam_role_policy.execution_db_passwords" in _ecs_tf.split('resource "aws_ecs_service"')[1]
      and re.search(r'temporal_db_password_arn\s*=\s*"arn:\$\{local\.partition\}:ssm:\$\{var\.region\}:\$\{local\.account_id\}:parameter/\$\{local\.name_prefix\}/temporal/db-password"', tf) is not None)
_iam_task = _iam_tf.split('data "aws_iam_policy_document" "task"')[1].split("\nresource ")[0] if 'data "aws_iam_policy_document" "task"' in _iam_tf else ""
_iam_deny = _iam_task.split('sid       = "DenyNautobotParameters"')[1].split("\n  }")[0] if "DenyNautobotParameters" in _iam_task else ""
# Parameters は /<prefix>/* を許す。Deny が無いと temporal / worker / ui と ECS Exec のシェルが /<prefix>/nautobot/db-password（master）を GetParameter で読める
check("サーバーのタスクのロールは Nautobot の SSM（/<prefix>/nautobot/*。master のパスワードを含む）を明示的に拒む（cycle 042）",
      'effect    = "Deny"' in _iam_deny and 'actions   = ["ssm:GetParameter*"]' in _iam_deny
      and 'parameter${local.param_prefix}/nautobot/*"]' in _iam_deny
      and 'parameter${local.param_prefix}/*"]' in _iam_task.split('sid       = "Parameters"')[1].split("\n  }")[0])
# 版の正は ops/up-common.sh。イメージの ARG と Terraform の既定がずれると、ビルドした版と RDS の版・UI のタグが食い違う
_ts_df = read("docker", "images", "temporal-server", "Dockerfile")
_upc = read("ops", "up-common.sh")
_nb_vars = read("IaC", "terraform", "aws-managed", "pipeline", "nautobot", "variables.tf")
_v = lambda pat, s: (re.search(pat, s, re.M) or [None, None])[1]
check("TEMPORAL_SERVER_VERSION は docker/images/temporal-server/Dockerfile の ARG の既定と同じ",
      _v(r'^TEMPORAL_SERVER_VERSION=(\S+)', _upc) is not None and _v(r'^TEMPORAL_SERVER_VERSION=(\S+)', _upc) == _v(r'^ARG TEMPORAL_SERVER_VERSION=(\S+)', _ts_df))
check("POSTGRES_MAJOR は Dockerfile の ARG と pipeline/nautobot の db_engine_version の既定と同じ（RDS と psql の大版）",
      _v(r'^POSTGRES_MAJOR=(\S+)', _upc) is not None
      and _v(r'^POSTGRES_MAJOR=(\S+)', _upc) == _v(r'^ARG POSTGRES_MAJOR=(\S+)', _ts_df)
      == _v(r'variable "db_engine_version"[\s\S]*?default\s*=\s*"([^"]+)"', _nb_vars))
check("TEMPORAL_UI_TAG は workflow の temporal_ui_image_tag の既定と同じ。temporal_image_tag には既定が無い（up.sh がビルドしたタグを渡す）",
      _v(r'^TEMPORAL_UI_TAG=(\S+)', _upc) is not None
      and _v(r'^TEMPORAL_UI_TAG=(\S+)', _upc) == _v(r'variable "temporal_ui_image_tag"[\s\S]*?default\s*=\s*"([^"]+)"', tf)
      and "default" not in tf.split('variable "temporal_image_tag"')[1].split("variable ")[0])
check("docker/images/temporal-server/ は Dockerfile と entrypoint.sh と init.sh と common.sh と namespace.sh と dynamic config の 6 つ（cycle 042）",
      sorted(os.listdir(os.path.join(ROOT, "docker", "images", "temporal-server"))) == ["Dockerfile", "common.sh", "dynamicconfig.yaml", "entrypoint.sh", "init.sh", "namespace.sh"])
_ts_df_code = [ln.strip() for ln in _ts_df.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
check("Dockerfile は tini を apk で入れ（psql と同じ RUN）、namespace.sh を /etc/temporal/namespace-rds.sh に実行できる形で COPY する（cycle 039）",
      "RUN apk add --no-cache ${POSTGRESQL_CLIENT_PACKAGE} tini" in _ts_df_code
      and "COPY --chmod=755 namespace.sh /etc/temporal/namespace-rds.sh" in _ts_df_code)
check("Dockerfile は init.sh を /etc/temporal/init-rds.sh に実行できる形で、common.sh を /etc/temporal/common-rds.sh に COPY する。ENTRYPOINT はサーバーの entrypoint-rds.sh のまま（cycle 042）",
      "COPY --chmod=755 init.sh /etc/temporal/init-rds.sh" in _ts_df_code and "COPY common.sh /etc/temporal/common-rds.sh" in _ts_df_code
      and "COPY --chmod=755 entrypoint.sh /etc/temporal/entrypoint-rds.sh" in _ts_df_code
      and [ln for ln in _ts_df_code if ln.startswith("ENTRYPOINT")] == ['ENTRYPOINT ["/etc/temporal/entrypoint-rds.sh"]'])
# ---- 初期化は一回きりのタスク（init.sh）、サーバー（entrypoint.sh）はスキーマの版が揃うのを待つだけ。共通部分は common.sh（cycle 042）
_ts_common = read("docker", "images", "temporal-server", "common.sh")
_ts_init = read("docker", "images", "temporal-server", "init.sh")
_ts_ep = read("docker", "images", "temporal-server", "entrypoint.sh")
_ts_code_lines = lambda s: [ln for ln in s.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
# 行末の \ で続く行は 1 行にまとめる（psql の 1 回の呼び出しを 1 行として見る）
_ts_logical = lambda s: re.sub(r"\\\n", " ", "\n".join(_ts_code_lines(s))).splitlines()
_ts_common_logical, _ts_init_logical, _ts_ep_logical = _ts_logical(_ts_common), _ts_logical(_ts_init), _ts_logical(_ts_ep)
for _name, _src, _lname in (("init.sh", _ts_init, "init-rds"), ("entrypoint.sh", _ts_ep, "entrypoint-rds")):
    check(f"{_name} は #!/bin/sh → set -eu → LOG_NAME={_lname} → . /etc/temporal/common-rds.sh の順で始まる（いま: {_ts_code_lines(_src)[:3]}）",
          _src.startswith("#!/bin/sh\n") and _ts_code_lines(_src)[:3] == ["set -eu", f"LOG_NAME={_lname}", ". /etc/temporal/common-rds.sh"]
          and sum(ln.strip() == ". /etc/temporal/common-rds.sh" for ln in _ts_code_lines(_src)) == 1)
check("common.sh は読まれる側（shebang も set も exec も trap 以外の exit も無い）で、DB に触らず（psql も temporal-sql-tool も起こさない）、master のパスワードを知らない",
      not _ts_common.startswith("#!") and not any(re.match(r"\s*(set|exec)\s", ln) for ln in _ts_common_logical)
      and not any(re.search(r'(^|\s)(PGPASSWORD="[^"]*"\s+)?psql\s', ln) for ln in _ts_common_logical)
      and "temporal-sql-tool --" not in _ts_common and "NAUTOBOT_DB_" not in _ts_common
      and all(f': "${{{v}:?' in _ts_common for v in ("POSTGRES_SEEDS", "POSTGRES_USER", "POSTGRES_PWD"))
      and "SCHEMA_DIR=/etc/temporal/schema/postgresql/v12\n" in _ts_common)
check("init.sh はパスワードをコマンドラインに載せない（psql は \\getenv、temporal-sql-tool は SQL_PASSWORD）で、最後は exit 0（サーバーを起こさない）",
      "\\getenv pw POSTGRES_PWD" in _ts_init and "-v pw=" not in _ts_init and "--pw" not in _ts_init and 'SQL_PASSWORD="$POSTGRES_PWD"' in _ts_init
      and _ts_init_logical[-1].strip() == "exit 0" and "tini" not in "\n".join(_ts_init_logical) and "namespace-rds.sh" not in _ts_init
      and "/etc/temporal/entrypoint.sh" not in _ts_init)
check("init.sh はロールの SET の権限を見てから GRANT する（PG 16 以降の CREATEROLE の作ったロールは ADMIN だけで、OWNER に指定できない）",
      "pg_has_role(CURRENT_USER, :'role', 'SET')" in _ts_init and "'MEMBER'" not in _ts_init)
check("init.sh は schema_version が無いときだけ setup-schema、毎回 update-schema（スキーマを消さない）",
      "to_regclass('schema_version')" in _ts_init and "setup-schema -v 0.0" in _ts_init and 'update-schema -d "$SCHEMA_DIR/$dir/versioned"' in _ts_init
      and '"$DBNAME:temporal" "$VISIBILITY_DBNAME:visibility"' in _ts_init
      and "drop-schema" not in _ts_init and "DROP " not in _ts_init.upper().replace("DROP-", "")
      # setup-schema -v 0.0 は has = f（schema_version が無い）の if の中に 1 回だけ（外に出すと 2 回目の起動で CREATE TABLE が既存表にぶつかって落ちる）
      and "-tAc \"SELECT to_regclass('schema_version') IS NOT NULL\")" in _ts_init
      and [ln.strip() for ln in _ts_init_logical if "setup-schema -v 0.0" in ln]
      == ['log "$db: 初回なので setup-schema -v 0.0"', 'sql_tool "$db" setup-schema -v 0.0']
      and re.search(r'\n  if \[ "\$has" = "f" \]; then\n    log "\$db: 初回なので setup-schema -v 0\.0"\n    sql_tool "\$db" setup-schema -v 0\.0\n  fi\n', _ts_init) is not None)
_ts_init_master = [ln.strip() for ln in _ts_init_logical if "NAUTOBOT_DB_PASSWORD" in ln]
check(f"init.sh が master のパスワード（NAUTOBOT_DB_PASSWORD）に触るのは :? の検査と master の psql 2 回（ロールと DB / btree_gin）だけ（いま: {len(_ts_init_master)} 行）",
      len(_ts_init_master) == 3 and _ts_init_master[0].startswith(': "${NAUTOBOT_DB_PASSWORD:?')
      and all(ln.startswith('PGPASSWORD="$NAUTOBOT_DB_PASSWORD" psql -X -q -v ON_ERROR_STOP=1 -U "$NAUTOBOT_DB_USER" ') for ln in _ts_init_master[1:])
      and "CREATE EXTENSION IF NOT EXISTS btree_gin" in _ts_init_master[2]
      and not any("NAUTOBOT_DB_PASSWORD" in ln for ln in _ts_init_logical[_ts_init_logical.index(next(l for l in _ts_init_logical if "btree_gin" in l)) + 1:]))
_ts_sql_tool = _ts_init.split("sql_tool() {")[1].split("\n}")[0] if "sql_tool() {" in _ts_init else ""
check("init.sh の temporal-sql-tool と psql の TLS はサーバー本体と同じ SQL_HOST_VERIFICATION / SQL_CA / SQL_HOST_NAME から導く（導出は common.sh。ホスト名検証を決め打ちしない）",
      _ts_sql_tool != "" and "SQL_TLS_DISABLE_HOST_VERIFICATION=true" not in _ts_init + _ts_common and ': "${SQL_HOST_VERIFICATION:=false}"' in _ts_common
      and '"$SQL_HOST_VERIFICATION"' in _ts_common and 'SQL_TLS_DISABLE_HOST_VERIFICATION="$sql_tool_skip_host_verify"' in _ts_sql_tool
      and 'SQL_TLS_CA_FILE="$SQL_CA"' in _ts_sql_tool and 'SQL_TLS_SERVER_NAME="$SQL_HOST_NAME"' in _ts_sql_tool
      and "PGSSLMODE=verify-full" in _ts_common and 'PGSSLROOTCERT="$SQL_CA"' in _ts_common)
# サーバーの entrypoint は master のパスワードも、スキーマを書く道具も持たない（cycle 042。039 / 040 の unset と /proc/<pid>/environ の検査は、渡さないことで要らなくなった）
_ts_ep_code_text = "\n".join(_ts_ep_logical)
_ts_ep_bad = [w for w in ("NAUTOBOT_DB_", "NAUTOBOT", "temporal-sql-tool", "sql_tool", "setup-schema", "update-schema", "unset", "CREATE ", "GRANT ", "ALTER ", "\\getenv", "btree_gin")
              if w in _ts_ep_code_text]
check(f"entrypoint.sh は master のパスワードとユーザーに触らず、スキーマを書かない（temporal-sql-tool / CREATE / GRANT / unset が無い。いま見つかったもの: {_ts_ep_bad}）",
      _ts_ep_bad == [] and _ts_ep.rstrip().splitlines()[-1].strip() == "exec /sbin/tini -- /etc/temporal/entrypoint.sh")
_ts_ep_psql = [ln.strip() for ln in _ts_ep_logical if re.search(r"(^|\s|\()psql\s", ln.split("  # ", 1)[0])]
check(f"entrypoint.sh の psql はロール POSTGRES_USER と POSTGRES_PWD で schema_version.curr_version を読む 1 か所だけで、stderr を捨てずに受ける（2>&1。いま: {_ts_ep_psql}）",
      len(_ts_ep_psql) == 1 and _ts_ep_psql[0].startswith('_out=$(PGPASSWORD="$POSTGRES_PWD" psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -tAc')
      and _ts_ep_psql[0].endswith('"SELECT curr_version FROM schema_version WHERE version_partition = 0" 2>&1) || true')
      and "2>/dev/null" not in _ts_ep_code_text)
check("entrypoint.sh は両方の DB の版がイメージの versioned/ の版（v を除き、数で並べた最大）以上になるまで 10 秒おきに 30 回（startPeriod の 300 秒）待ち、"
      "毎回と最後の行に init のログの見方を出して、最後は exit 1（Round 1 のセルフレビューの Should 2 / 3。cycle 042）",
      "sort -t. -k1,1n -k2,2n | tail -n 1" in _ts_ep and 'want_t=$(schema_want temporal); want_v=$(schema_want visibility)' in _ts_ep
      and 'if version_ge "$have_t" "$want_t" && version_ge "$have_v" "$want_v"; then break; fi' in _ts_ep
      and "init_logs_command" in _ts_ep.split("init_hint=", 1)[-1].split("\n", 1)[0]
      and re.search(r'if \[ "\$i" -gt 30 \]; then\n\s*log "[^"]*\$\{init_hint\}[^"]*30 回（300 秒）[^"]*"; exit 1\n', _ts_ep) is not None
      and re.search(r'\n  log "[^"]*\$\{init_hint\}[^"]*（\$\{i\}/30。[^"]*"\n  sleep 10\n', _ts_ep) is not None
      and re.findall(r"startPeriod\s*=\s*(\d+)", _c_temporal_code) == ["300"])
# fork だけのサブシェル（( … ) &）は /proc/<pid>/environ に親の最初の env を持ち続ける。別の実行ファイルを exec させる（cycle 039）
check("entrypoint は namespace を /etc/temporal/namespace-rds.sh <address> <namespace> <retention> & で起こし、( … ) & のサブシェルを持たない（cycle 039）",
      '/etc/temporal/namespace-rds.sh "$TEMPORAL_ADDRESS_LOCAL" "$DEFAULT_NAMESPACE" "$DEFAULT_NAMESPACE_RETENTION" &' in [ln.strip() for ln in _ts_ep_logical]
      and not any(re.search(r"\)\s*&\s*$", ln) for ln in _ts_ep_logical))
# 待つ手順を前景で回す（trap は前景の子が返ってから走る。`… & wait $!` にすると wait が trap に割り込まれて途中で抜ける）
for _name, _lg, _want_bg in (("entrypoint.sh", _ts_ep_logical, 1), ("init.sh", _ts_init_logical, 0), ("common.sh", _ts_common_logical, 0)):
    _ts_bg = [ln.strip() for ln in _lg if re.search(r"(?<!&)&\s*$", ln)]
    _ts_wait = [ln.strip() for ln in _lg if re.search(r"\bwait\b", ln) and "wait_for_db" not in ln]
    check(f"{_name} が背景（末尾 &）で起こすのは{' namespace-rds.sh の 1 行だけ' if _want_bg else '何も無く'}、wait の行は無い（psql / temporal-sql-tool / sleep は前景。いま: & {_ts_bg} wait {_ts_wait}）",
          len(_ts_bg) == _want_bg and all(b.startswith("/etc/temporal/namespace-rds.sh ") for b in _ts_bg) and _ts_wait == [])
# 起動中の SIGTERM（cycle 040）。PID 1 の sh はハンドラの無い SIGTERM を無視するので、common.sh の trap で受けて手順の区切りで exit 143 にする
_ts_trap = [i for i, ln in enumerate(_ts_common_logical) if re.match(r"\s*trap\b", ln)]
_ts_cfirst = lambda pat: next((i for i, ln in enumerate(_ts_common_logical) if re.match(pat, ln)), -1)
check(f"common.sh は log() の後に trap を 1 つ置き TERM / INT で exit 143。init.sh と entrypoint.sh は自分で trap を持たない（いま: common {_ts_trap}）",
      len(_ts_trap) == 1 and re.search(r"\bTERM\b", _ts_common_logical[_ts_trap[0]]) is not None and re.search(r"\bINT\b", _ts_common_logical[_ts_trap[0]]) is not None
      and "exit 143" in _ts_common_logical[_ts_trap[0]] and "SIGTERM を受けたので初期化を止める" in _ts_common_logical[_ts_trap[0]]
      and 0 <= _ts_cfirst(r"log\(\) \{") < _ts_trap[0]
      and not any(re.match(r"\s*trap\b", ln) for ln in _ts_init_logical + _ts_ep_logical))
def _ts_step_order(lg, marks):
    steps = [(i, ln.strip()) for i, ln in enumerate(lg) if re.match(r"\s*step=\d+\s*$", ln)]
    pos = [next((i for i, ln in enumerate(lg) if re.match(p, ln)), -1) for p in marks]
    seq = []
    for (si, _), mi in zip(steps, pos):
        seq += [si, mi]
    ok = [st for _, st in steps] == [f"step={n}" for n in range(1, len(marks) + 1)] and -1 not in pos and seq == sorted(seq) and len(set(seq)) == len(seq)
    return ok, steps, pos
_ok, _st, _mk = _ts_step_order(_ts_init_logical, [r"\s*wait_for_db\s*$", r'\s*PGPASSWORD="\$NAUTOBOT_DB_PASSWORD" psql ', r"\s*for pair in "])
check(f"init.sh は step=1〜3 を手順 1（wait_for_db）/ 2（master の psql）/ 3（for pair）の直前に順に置く（いま: step {_st} 目印 {_mk}）", _ok)
_ok, _st, _mk = _ts_step_order(_ts_ep_logical, [r"\s*wait_for_db\s*$", r"\s*while :; do", r"\s*/etc/temporal/namespace-rds\.sh "])
check(f"entrypoint.sh は step=1〜3 を手順 1（wait_for_db）/ 2（版を待つ while）/ 3（namespace）の直前に順に置く（いま: step {_st} 目印 {_mk}）", _ok)
def _ts_trap_run(log_name="entrypoint-rds", step="step=2"):
    """common.sh を LOG_NAME 付きで sh で読み、step（空なら未設定のまま）のあとに自分へ SIGTERM を送る。rc と stdout と stderr を返す。"""
    import subprocess
    script = f"set -eu\nLOG_NAME={log_name}\n{_ts_common}\n{step}\nkill -TERM $$\necho after\n"
    env = {"PATH": os.environ.get("PATH", ""), "POSTGRES_SEEDS": "db", "POSTGRES_USER": "temporal", "POSTGRES_PWD": "pw"}
    out = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    return out.returncode, out.stdout, out.stderr
for _lname in ("entrypoint-rds", "init-rds"):
    _tr_rc, _tr_out, _tr_err = _ts_trap_run(_lname)
    check(f"common.sh の trap（{_lname}）: SIGTERM で次の行へ進まず exit 143、ログに LOG_NAME と手順の番号が出る（いま: rc={_tr_rc} {_tr_out.strip()!r} {_tr_err.strip()!r}）",
          _tr_rc == 143 and "after" not in _tr_out and f"{_lname}: SIGTERM を受けたので初期化を止める（手順 2 のあと" in _tr_err)
_tr_rc, _tr_out, _tr_err = _ts_trap_run(step="")
check(f"common.sh の trap: step が未設定（手順 1 より前）でも set -u で落ちず exit 143、手順 0 と出る（${{step:-0}}。いま: rc={_tr_rc} {_tr_err.strip()!r}）",
      _tr_rc == 143 and "after" not in _tr_out and "（手順 0 のあと" in _tr_err)
def _ts_stub_dir(d, stubs):
    for n, body in stubs.items():
        with open(os.path.join(d, n), "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(os.path.join(d, n), 0o755)
def _ts_ep_run(seq_t, seq_v, versions=("v1.0", "v1.2", "v1.9", "v1.19"), vis_versions=("v1.0", "v1.14")):
    """entrypoint.sh を、common.sh・スキーマのディレクトリ・namespace-rds.sh・tini・psql・nc・sleep を差し替えて動かす。
    psql は DB ごとの返り（seq_t / seq_v の順。ERR は失敗、EMPTY は空、尽きたら最後を繰り返す）を出す。rc と呼ばれたもの（psql / sleep / ns / tini）と stderr を返す。"""
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as d:
        for sub, vs in (("temporal", versions), ("visibility", vis_versions)):
            for v in vs:
                os.makedirs(os.path.join(d, "schema", sub, "versioned", v))
        for db, seq in (("temporal", seq_t), ("temporal_visibility", seq_v)):
            with open(os.path.join(d, f"seq_{db}"), "w") as f:
                f.write("\n".join(seq) + "\n")
        _ts_stub_dir(d, {
            "nc": "exit 0", "sleep": f'echo "sleep $*" >> {d}/calls',
            "psql": (f'db=; prev=; for a in "$@"; do [ "$prev" = "-d" ] && db=$a; prev=$a; done\n'
                     f'echo "psql $* PGPASSWORD=${{PGPASSWORD-unset}} MASTER=${{NAUTOBOT_DB_PASSWORD-unset}}" >> {d}/calls\n'
                     f'n=$(cat {d}/n_$db 2>/dev/null || echo 0); n=$((n + 1)); echo $n > {d}/n_$db\n'
                     f'v=$(sed -n "${{n}}p" {d}/seq_$db); [ -n "$v" ] || v=$(tail -n 1 {d}/seq_$db)\n'
                     'case "$v" in ERR) printf \'%s\\n\' "ERROR:  relation \\"schema_version\\" does not exist" "LINE 1: SELECT curr_version FROM schema_version" >&2; exit 3;; EMPTY) exit 0;;\n'
                     '  AUTH) printf \'%s\\n\' "psql: error: connection to server at \\"db\\" (10.0.0.1), port 5432 failed: FATAL:  password authentication failed for user \\"temporal\\"" "2 行目" >&2; exit 2;;\n'
                     '  *) echo "$v";; esac'),
            "ns": f'echo "ns $*" >> {d}/calls', "tini": f'echo "tini $*" >> {d}/calls'})
        with open(os.path.join(d, "common.sh"), "w") as f:
            f.write(_ts_common.replace("SCHEMA_DIR=/etc/temporal/schema/postgresql/v12", f"SCHEMA_DIR={d}/schema"))
        script = (_ts_ep.replace(". /etc/temporal/common-rds.sh", f". {d}/common.sh").replace("/etc/temporal/namespace-rds.sh ", f"{d}/ns ")
                  .replace("exec /sbin/tini ", f"exec {d}/tini "))
        env = {"PATH": d + os.pathsep + os.environ.get("PATH", ""), "POSTGRES_SEEDS": "db", "POSTGRES_USER": "temporal", "POSTGRES_PWD": "pw-role"}
        out = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        calls = open(os.path.join(d, "calls")).read().splitlines() if os.path.exists(os.path.join(d, "calls")) else []
    return out.returncode, calls, out.stderr
_ep_rc, _ep_calls, _ep_err = _ts_ep_run(["ERR", "EMPTY", "1.19"], ["ERR", "1.14", "1.14"])
_ep_kinds = [c.split()[0] for c in _ep_calls]
check(f"entrypoint.sh: 表が無い → 表はあるが版が無い → 1.19 / 1.14 で 2 回待ってから namespace と tini へ進む（v1.9 より v1.19 を新しいとみる。いま: rc={_ep_rc} {_ep_kinds} {_ep_err.strip()[-80:]!r}）",
      # ns は背景（&）で起こし、すぐ exec tini する。どちらが先に calls へ書くかは決まらないので、最後の 2 つは順を問わない
      _ep_rc == 0 and _ep_kinds[:8] == ["psql", "psql", "sleep", "psql", "psql", "sleep", "psql", "psql"] and sorted(_ep_kinds[8:]) == ["ns", "tini"]
      and [c for c in _ep_calls if c.startswith("sleep")] == ["sleep 10", "sleep 10"]
      and sorted(c for c in _ep_calls if c.startswith(("ns", "tini"))) == ["ns 127.0.0.1:7233 default 72h", "tini -- /etc/temporal/entrypoint.sh"]
      and "entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5（OSS 版は ops/oss/up.sh の 8）が run-task する <接頭辞>-workflow-init。ログは terraform -chdir=IaC/terraform/aws-managed/workflow output -raw init_logs_command" in _ep_err
      and "を待つ（2/30。スキーマの版 temporal=無し/1.19 temporal_visibility=1.14/1.14）" in _ep_err
      and "entrypoint-rds: スキーマは temporal=1.19 / temporal_visibility=1.14（イメージの版 1.19 / 1.14 以上）" in _ep_err)
check(f"entrypoint.sh: psql は全部ロール temporal と POSTGRES_PWD（env）で呼び、パスワードを引数に載せず、master のパスワードは env にも無い（いま: {_ep_calls[:2]}）",
      all(" -U temporal " in c and c.endswith("PGPASSWORD=pw-role MASTER=unset") and "pw-role" not in c.rsplit(" PGPASSWORD=", 1)[0]
          for c in _ep_calls if c.startswith("psql")) and any(c.startswith("psql") for c in _ep_calls))
_ep_rc, _ep_calls, _ep_err = _ts_ep_run(["1.9"], ["1.14"])
check(f"entrypoint.sh: 版が古い（1.9 < 1.19）まま 30 回待つと namespace も tini も起こさず exit 1、init のログの見方を出す（いま: rc={_ep_rc} sleep {_ep_calls.count('sleep 10')} 回 {_ep_err.strip()[-80:]!r}）",
      _ep_rc == 1 and _ep_calls.count("sleep 10") == 30 and not any(c.startswith(("ns", "tini")) for c in _ep_calls)
      and "30 回（300 秒）待っても終わらない（スキーマの版 temporal=1.9/1.19 temporal_visibility=1.14/1.14）" in _ep_err and "init_logs_command" in _ep_err.strip().splitlines()[-1])
# イメージを古い版に戻すと DB の版がイメージより新しくなる。Temporal 本体（VerifyCompatibleVersion）と同じく許す（Round 1 のセルフレビューの Should 2）
_ep_rc, _ep_calls, _ep_err = _ts_ep_run(["1.20"], ["1.15"])
check(f"entrypoint.sh: DB の版がイメージより新しい（1.20 > 1.19、1.15 > 1.14）なら待たずに namespace と tini へ進む（いま: rc={_ep_rc} {[c.split()[0] for c in _ep_calls]} {_ep_err.strip()[-60:]!r}）",
      _ep_rc == 0 and not any(c.startswith("sleep") for c in _ep_calls) and sorted(c.split()[0] for c in _ep_calls if c.startswith(("ns", "tini"))) == ["ns", "tini"]
      and "スキーマは temporal=1.20 / temporal_visibility=1.15（イメージの版 1.19 / 1.14 以上）" in _ep_err)
for _seq_t, _seq_v, _why in ((["2.0"], ["1.14"], "major が大きい"), (["1.19"], ["1.14"], "同じ")):
    _ep_rc, _ep_calls, _ep_err = _ts_ep_run(_seq_t, _seq_v)
    check(f"entrypoint.sh: DB の版がイメージの版と{_why}（{_seq_t[0]} / {_seq_v[0]}）なら待たずに進む（いま: rc={_ep_rc} sleep {_ep_calls.count('sleep 10')} 回）",
          _ep_rc == 0 and _ep_calls.count("sleep 10") == 0 and any(c.startswith("tini") for c in _ep_calls))
for _seq_t, _seq_v, _why in ((["1.18"], ["1.14"], "temporal だけ古い"), (["1.20"], ["1.13"], "visibility だけ古い"), (["0.99"], ["1.14"], "major が小さい"),
                             (["1.19.1"], ["1.14"], "形が違う（3 つ組）"), (["v1.19"], ["1.14"], "形が違う（v 付き）")):
    _ep_rc, _ep_calls, _ep_err = _ts_ep_run(_seq_t, _seq_v)
    check(f"entrypoint.sh: {_why}（{_seq_t[0]} / {_seq_v[0]}）なら 30 回待って exit 1（いま: rc={_ep_rc} sleep {_ep_calls.count('sleep 10')} 回）",
          _ep_rc == 1 and _ep_calls.count("sleep 10") == 30 and not any(c.startswith(("ns", "tini")) for c in _ep_calls))
# version_ge そのもの。schema_read の sed が版の形の行だけを渡すが、空（読めない）や形の違う値を渡されても、[ の「整数でない」で stderr を汚さずに偽を返す
import subprocess as _vsp  # noqa: E402
_vge = _ts_ep[_ts_ep.index("version_ge() {"):]
_vge = _vge[:_vge.index("\n}\n") + 3]
_vge_cases = (("1.19", "1.19", 0), ("1.20", "1.19", 0), ("2.0", "1.19", 0), ("1.9", "1.19", 1), ("0.99", "1.14", 1), ("1.18", "1.19", 1),
              ("", "1.19", 1), ("1.19.1", "1.19", 1), ("v1.19", "1.19", 1), ("1.", "1.19", 1), (".19", "1.19", 1), ("1.x", "1.1", 1), ("119", "1.19", 1),
              ("1.19", "1.20.1", 1), ("1.19", "", 1))
_vge_out = _vsp.run(["sh", "-c", _vge + "".join(f'version_ge "{a}" "{b}"; echo "{a}:$?"\n' for a, b, _ in _vge_cases)],
                    capture_output=True, text=True, timeout=30, stdin=_vsp.DEVNULL)
check(f"entrypoint.sh の version_ge: major.minor を数で比べ（1.20 ≥ 1.19、2.0 ≥ 1.19、1.9 < 1.19）、どちらかが空・3 つ組・v 付き・数でないものは stderr に何も出さず偽（いま: {_vge_out.stdout.split()} {_vge_out.stderr.strip()[:80]!r}）",
      _vge_out.stdout.split() == [f"{a}:{rc}" for a, _, rc in _vge_cases] and _vge_out.stderr == "")
# 認証や TLS の失敗を「まだ無い」と誤解させない。psql の stderr の 1 行目をそのまま log に出す（Round 1 のセルフレビューの Should 4）
_ep_rc, _ep_calls, _ep_err = _ts_ep_run(["AUTH", "1.19"], ["AUTH", "1.14"])
check(f"entrypoint.sh: psql が認証で落ちたら、待ちの行に DB ごとの stderr の 1 行目を出し（2 行目とパスワードは出さない）、版が揃えば進む（いま: rc={_ep_rc} {_ep_err.strip()[:200]!r}）",
      _ep_rc == 0 and _ep_calls.count("sleep 10") == 1
      and "（1/30。スキーマの版 temporal=無し/1.19 temporal_visibility=無し/1.14。temporal の psql: psql: error: connection to server at \"db\" (10.0.0.1), port 5432 failed: "
          "FATAL:  password authentication failed for user \"temporal\"。temporal_visibility の psql: psql: error: connection" in _ep_err
      and "2 行目" not in _ep_err and "pw-role" not in _ep_err)
_ep_rc, _ep_calls, _ep_err = _ts_ep_run(["ERR"], ["EMPTY"])
check(f"entrypoint.sh: 表が無い（psql の失敗。2 行目の LINE 1: は出さない）と行が無い（psql は成功で空）を分けて出し、最後の行にも psql の失敗を出す（いま: rc={_ep_rc} {_ep_err.strip().splitlines()[-1][-160:]!r}）",
      _ep_rc == 1 and "temporal=無し/1.19 temporal_visibility=無し/1.14。temporal の psql: ERROR:  relation \"schema_version\" does not exist）" in _ep_err.strip().splitlines()[-1]
      and "temporal_visibility の psql" not in _ep_err and "LINE 1:" not in _ep_err)
_ep_rc, _ep_calls, _ep_err = _ts_ep_run(["1.19"], ["1.14"], vis_versions=())
check(f"entrypoint.sh: イメージに versioned/ の版が無ければ待たずに exit 1（いま: rc={_ep_rc} {_ep_calls} {_ep_err.strip()[-60:]!r}）",
      _ep_rc == 1 and not any(c.startswith(("sleep", "ns", "tini")) for c in _ep_calls) and "版が無い" in _ep_err)
def _ts_init_run(has, **env):
    """init.sh を、common.sh・psql・temporal-sql-tool・nc・sleep を差し替えて動かす。schema_version の有無（has = t / f）を psql が返す。
    rc と呼ばれたもの（psql の引数と PGPASSWORD、temporal-sql-tool の引数と SQL_PASSWORD）と stderr を返す。"""
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as d:
        _ts_stub_dir(d, {
            "nc": "exit 0", "sleep": "exit 0",
            "psql": (f'first=$(head -n 1)\necho "psql $* | PGPASSWORD=${{PGPASSWORD-unset}} stdin=$first" >> {d}/calls\n'
                     f'case "$*" in *to_regclass*) echo {has};; esac'),
            "temporal-sql-tool": f'echo "tool $* | SQL_PASSWORD=${{SQL_PASSWORD-unset}} MASTER=${{NAUTOBOT_DB_PASSWORD-unset}}" >> {d}/calls'})
        with open(os.path.join(d, "common.sh"), "w") as f:
            f.write(_ts_common)
        script = _ts_init.replace(". /etc/temporal/common-rds.sh", f". {d}/common.sh")
        base = {"PATH": d + os.pathsep + os.environ.get("PATH", ""), "POSTGRES_SEEDS": "db", "POSTGRES_USER": "temporal", "POSTGRES_PWD": "pw-role",
                "NAUTOBOT_DB_USER": "nautobot", "NAUTOBOT_DB_PASSWORD": "pw-master"}
        out = subprocess.run(["sh", "-c", script], env={k: v for k, v in {**base, **env}.items() if v is not None},
                             capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        calls = open(os.path.join(d, "calls")).read().splitlines() if os.path.exists(os.path.join(d, "calls")) else []
    return out.returncode, calls, out.stderr
_SD = "/etc/temporal/schema/postgresql/v12"
_TOOL = "tool --plugin postgres12 --ep db -p 5432 -u temporal --db"
_in_rc, _in_calls, _in_err = _ts_init_run("f")
check(f"init.sh（初回。schema_version が無い）: master でロールと DB と btree_gin、ロールで setup-schema と update-schema を両方の DB に、の順で呼び exit 0（いま: rc={_in_rc} {[c.split(' | ')[0][:60] for c in _in_calls]}）",
      _in_rc == 0 and [c.split(" | ")[0] for c in _in_calls] == [
          "psql -X -q -v ON_ERROR_STOP=1 -U nautobot -d nautobot -v role=temporal -v db=temporal -v vdb=temporal_visibility",
          "psql -X -q -v ON_ERROR_STOP=1 -U nautobot -d temporal_visibility -c CREATE EXTENSION IF NOT EXISTS btree_gin",
          "psql -X -q -v ON_ERROR_STOP=1 -U temporal -d temporal -tAc SELECT to_regclass('schema_version') IS NOT NULL",
          f"{_TOOL} temporal setup-schema -v 0.0", f"{_TOOL} temporal update-schema -d {_SD}/temporal/versioned",
          "psql -X -q -v ON_ERROR_STOP=1 -U temporal -d temporal_visibility -tAc SELECT to_regclass('schema_version') IS NOT NULL",
          f"{_TOOL} temporal_visibility setup-schema -v 0.0", f"{_TOOL} temporal_visibility update-schema -d {_SD}/visibility/versioned"]
      and _in_calls[0].endswith("stdin=\\getenv pw POSTGRES_PWD") and _in_err.strip().endswith("init-rds: 初期化が終わった"))
check("init.sh: master のパスワードは master の psql 2 回の PGPASSWORD にだけ渡り、ロールの psql と temporal-sql-tool は POSTGRES_PWD を env で受け、どのパスワードも引数に出ない",
      bool(_in_calls) and all("pw-master" not in c.split(" | ")[0] and "pw-role" not in c.split(" | ")[0] for c in _in_calls)
      and [c.split(" | ")[1].split(" stdin=")[0] for c in _in_calls if c.startswith("psql")] == ["PGPASSWORD=pw-master"] * 2 + ["PGPASSWORD=pw-role"] * 2
      and all(c.endswith("| SQL_PASSWORD=pw-role MASTER=pw-master") for c in _in_calls if c.startswith("tool")))
_in_rc, _in_calls, _in_err = _ts_init_run("t")
check(f"init.sh（2 回目。schema_version がある）: setup-schema を打たず update-schema だけを両方の DB に（いま: rc={_in_rc} {[c.split(' | ')[0].split(' --db ')[-1] for c in _in_calls if c.startswith('tool')]}）",
      _in_rc == 0 and [c.split(" | ")[0] for c in _in_calls if c.startswith("tool")]
      == [f"{_TOOL} temporal update-schema -d {_SD}/temporal/versioned", f"{_TOOL} temporal_visibility update-schema -d {_SD}/visibility/versioned"]
      and sum(c.startswith("psql") for c in _in_calls) == 4)
_in_rc, _in_calls, _in_err = _ts_init_run("f", NAUTOBOT_DB_PASSWORD=None)
check(f"init.sh: NAUTOBOT_DB_PASSWORD が無ければ何も呼ばずに非 0 で止まり、名前を言う（いま: rc={_in_rc} {_in_calls} {_in_err.strip()[-60:]!r}）",
      _in_rc != 0 and _in_calls == [] and "NAUTOBOT_DB_PASSWORD" in _in_err)
_in_rc, _in_calls, _in_err = _ts_init_run("f", POSTGRES_PWD=None)
check(f"init.sh: POSTGRES_PWD が無ければ何も呼ばずに非 0 で止まる（common.sh の :?。いま: rc={_in_rc} {_in_calls}）",
      _in_rc != 0 and _in_calls == [] and "POSTGRES_PWD" in _in_err)
_ts_ns = read("docker", "images", "temporal-server", "namespace.sh")
_ts_ns_code = "\n".join(ln for ln in _ts_ns.splitlines() if not ln.lstrip().startswith("#"))
_ts_ns_exits = re.findall(r"\bexit\b[ \t]*([^\s;]*)", _ts_ns_code)
check(f"namespace.sh はパスワードを持たず（unset も NAUTOBOT_DB_PASSWORD も POSTGRES_PWD も無い）、describe → create の順で、引数不足以外はどこで抜けても exit 0（いま: exit {_ts_ns_exits}）",
      _ts_ns.startswith("#!/bin/sh\n") and re.search(r"^set -u$", _ts_ns, re.M) is not None
      and "unset" not in _ts_ns and "NAUTOBOT_DB_PASSWORD" not in _ts_ns and "POSTGRES_PWD" not in _ts_ns
      and 0 <= _ts_ns_code.find("namespace describe") < _ts_ns_code.find("namespace create")
      and bool(_ts_ns_exits) and all(c == "0" for c in _ts_ns_exits))
def _ts_ns_run(describe_ok, create_ok, *args, nc_ok=True, health_ok=True):
    """namespace.sh を、nc / temporal / sleep を差し替えて動かす。nc と temporal に渡った引数（"nc …" / "temporal …"）と stderr と rc を返す。"""
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as d:
        stubs = {"nc": f'echo "nc $*" >> {d}/calls\nexit {0 if nc_ok else 1}', "sleep": "exit 0",
                 "temporal": f'echo "temporal $*" >> {d}/calls\ncase "$3" in health) exit {0 if health_ok else 1};; describe) exit {0 if describe_ok else 1};; create) exit {0 if create_ok else 1};; esac\nexit 0'}
        for n, body in stubs.items():
            with open(os.path.join(d, n), "w") as f:
                f.write("#!/bin/sh\n" + body + "\n")
            os.chmod(os.path.join(d, n), 0o755)
        out = subprocess.run(["sh", os.path.join(ROOT, "docker", "images", "temporal-server", "namespace.sh"), *args],
                             env={"PATH": d + os.pathsep + os.environ.get("PATH", "")}, capture_output=True, text=True, timeout=30)
        calls = open(os.path.join(d, "calls")).read().splitlines() if os.path.exists(os.path.join(d, "calls")) else []
    return out.returncode, calls, out.stderr
_ns_rc, _ns_calls, _ns_err = _ts_ns_run(False, True, "10.0.0.1:7233", "ns1", "24h")
check(f"namespace.sh: 引数（address / namespace / retention）で動き、無ければ create して entrypoint-rds: の接頭辞でログを出す（いま: rc={_ns_rc} {_ns_calls} {_ns_err.strip()!r}）",
      _ns_rc == 0 and _ns_calls == ["nc -z -w 10 10.0.0.1 7233", "temporal operator cluster health --address 10.0.0.1:7233", "temporal operator namespace describe -n ns1 --address 10.0.0.1:7233",
                                 "temporal operator namespace create -n ns1 --retention 24h --address 10.0.0.1:7233"]
      and _ns_err.strip() == "entrypoint-rds: namespace: ns1 を作った（retention 24h）")
_ns_rc, _ns_calls, _ns_err = _ts_ns_run(True, False, "10.0.0.1:7233", "ns1", "24h")
check(f"namespace.sh: describe が通れば create しない（いま: rc={_ns_rc} {_ns_calls}）",
      _ns_rc == 0 and not any("namespace create" in c for c in _ns_calls) and _ns_err.strip() == "entrypoint-rds: namespace: ns1 がある")
_ns_rc, _ns_calls, _ns_err = _ts_ns_run(False, False, "10.0.0.1:7233", "ns1", "24h")
check(f"namespace.sh: create が 30 回通らなくても exit 0（本体の temporal-server は別プロセスで、この終了コードを誰も待たない。いま: rc={_ns_rc} create {sum('namespace create' in c for c in _ns_calls)} 回）",
      _ns_rc == 0 and sum("namespace create" in c for c in _ns_calls) == 30 and _ns_err.strip().endswith("entrypoint-rds: namespace: ns1 を作れない（30 回）"))
_ns_rc, _ns_calls, _ns_err = _ts_ns_run(True, True, "10.0.0.1:7233", "ns1", "24h", nc_ok=False)
check(f"namespace.sh: frontend の nc が 30 回通らなければ temporal を呼ばずに exit 0（いま: rc={_ns_rc} nc {sum(c.startswith('nc ') for c in _ns_calls)} 回 temporal {sum(c.startswith('temporal ') for c in _ns_calls)} 回 {_ns_err.strip()[-60:]!r}）",
      _ns_rc == 0 and sum(c.startswith("nc ") for c in _ns_calls) == 30 and not any(c.startswith("temporal ") for c in _ns_calls)
      and _ns_err.strip().endswith("entrypoint-rds: namespace: frontend の 7233 が開かない（30 回）。作らずに抜ける"))
_ns_rc, _ns_calls, _ns_err = _ts_ns_run(True, True, "10.0.0.1:7233", "ns1", "24h", health_ok=False)
check(f"namespace.sh: cluster health が 30 回通らなければ describe も create もせずに exit 0（いま: rc={_ns_rc} health {sum('cluster health' in c for c in _ns_calls)} 回 {_ns_err.strip()[-60:]!r}）",
      _ns_rc == 0 and sum("cluster health" in c for c in _ns_calls) == 30 and not any("namespace describe" in c or "namespace create" in c for c in _ns_calls)
      and _ns_err.strip().endswith("entrypoint-rds: namespace: cluster health が通らない（30 回）。作らずに抜ける"))
_ns_rc, _ns_calls, _ns_err = _ts_ns_run(True, True, "10.0.0.1:7233", "ns1")
check(f"namespace.sh: 引数が 2 つ（retention が無い）なら何も呼ばずに非 0 で抜け、3 つ目が無いと言う（いま: rc={_ns_rc} {_ns_calls} {_ns_err.strip()!r}）",
      _ns_rc != 0 and _ns_calls == [] and "の 3 つ目が無い" in _ns_err and "の 1 つ目が無い" not in _ns_err and "の 2 つ目が無い" not in _ns_err)
def _ts_tls(**env):
    """common.sh（SQL_ の既定値、PGSSLMODE の分岐）と init.sh の sql_tool だけを sh で動かし、psql と temporal-sql-tool（差し替え）に渡る env を返す。"""
    import subprocess, tempfile
    b = _ts_init.find("sql_tool() {"); b_mark = _ts_init.find("\n}\n", b) if b >= 0 else -1
    if -1 in (b, b_mark):
        check("init.sh の sql_tool の切り出しの目印（sql_tool() { / \\n}\\n）がある", False)
        return None, {}, {}, ""
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "temporal-sql-tool"), "w") as f:
            f.write("#!/bin/sh\nenv | grep -E '^SQL_TLS(=|_DISABLE_HOST_VERIFICATION=|_CA_FILE=|_SERVER_NAME=)' | sort\n"
                    "echo \"_ARGS=$*\"\necho \"_INNER_SQL_PASSWORD=${SQL_PASSWORD-unset}\"\n")
        os.chmod(os.path.join(d, "temporal-sql-tool"), 0o755)
        script = (f"set -eu\nLOG_NAME=init-rds\n{_ts_common}\n{_ts_init[b:b_mark + 3]}sql_tool temporal update-schema -d /x\necho PGSSLMODE=$PGSSLMODE\necho PGSSLROOTCERT=${{PGSSLROOTCERT-}}\n"
                  "echo _OUTER_SQL_PASSWORD=${SQL_PASSWORD-unset}\necho _OUTER_SQL_TLS=${SQL_TLS-unset}\n")
        base = {"PATH": d + os.pathsep + os.environ.get("PATH", ""), "POSTGRES_SEEDS": "db", "DB_PORT": "5432", "POSTGRES_USER": "temporal", "POSTGRES_PWD": "pw"}
        out = subprocess.run(["sh", "-c", script], env={**base, **env}, capture_output=True, text=True, timeout=30)
    kv = dict(ln.split("=", 1) for ln in out.stdout.splitlines() if "=" in ln)
    return out.returncode, {k: v for k, v in kv.items() if not k.startswith("_")}, {k: v for k, v in kv.items() if k.startswith("_")}, out.stderr
_rc, _tls, _ex, _ = _ts_tls(SQL_TLS_ENABLED="true", SQL_HOST_VERIFICATION="false")
# sql_tool は引数をそのまま渡し、パスワードと SQL_TLS* はサブシェルの中だけ
check(f"init.sh の sql_tool: 引数をそのまま temporal-sql-tool に渡し、SQL_PASSWORD / SQL_TLS は呼んだ側のシェルに残さない（いま: {_ex}）",
      _rc == 0 and _ex == {"_ARGS": "--plugin postgres12 --ep db -p 5432 -u temporal --db temporal update-schema -d /x",
                           "_INNER_SQL_PASSWORD": "pw", "_OUTER_SQL_PASSWORD": "unset", "_OUTER_SQL_TLS": "unset"})
check(f"TLS（ecs.tf と同じ SQL_HOST_VERIFICATION=false）: psql は require、temporal-sql-tool はホスト名を検証しない。CA とサーバー名は渡さない（いま: {_tls}）",
      _rc == 0 and _tls == {"SQL_TLS": "true", "SQL_TLS_DISABLE_HOST_VERIFICATION": "true", "PGSSLMODE": "require", "PGSSLROOTCERT": ""})
_rc, _tls, _, _ = _ts_tls(SQL_TLS_ENABLED="true")
check(f"TLS: SQL_HOST_VERIFICATION が無ければ false と同じ（いま: {_tls}）",
      _rc == 0 and _tls == {"SQL_TLS": "true", "SQL_TLS_DISABLE_HOST_VERIFICATION": "true", "PGSSLMODE": "require", "PGSSLROOTCERT": ""})
_rc, _tls, _, _ = _ts_tls(SQL_TLS_ENABLED="true", SQL_HOST_VERIFICATION="true", SQL_CA="/ca.pem", SQL_HOST_NAME="db.example")
check(f"TLS: SQL_HOST_VERIFICATION=true なら psql は verify-full で SQL_CA を根に、temporal-sql-tool はホスト名を検証し CA とサーバー名を受ける（いま: {_tls}）",
      _rc == 0 and _tls == {"SQL_TLS": "true", "SQL_TLS_DISABLE_HOST_VERIFICATION": "false", "SQL_TLS_CA_FILE": "/ca.pem", "SQL_TLS_SERVER_NAME": "db.example",
                            "PGSSLMODE": "verify-full", "PGSSLROOTCERT": "/ca.pem"})
_rc, _tls, _, _ = _ts_tls(SQL_TLS_ENABLED="false")
check(f"TLS: SQL_TLS_ENABLED=false なら psql は prefer、temporal-sql-tool は TLS 無し（いま: {_tls}）",
      _rc == 0 and _tls.get("SQL_TLS") == "false" and _tls.get("PGSSLMODE") == "prefer")
# サーバー本体の YAML は True / TRUE も真に読むが、sh の = は大文字小文字を区別する。common.sh で小文字に揃える（cycle 040）
_rc, _tls, _ex, _ = _ts_tls(SQL_TLS_ENABLED="True", SQL_HOST_VERIFICATION="TRUE", SQL_CA="/ca.pem")
check(f"TLS: SQL_TLS_ENABLED / SQL_HOST_VERIFICATION は大文字でも真（True / TRUE → verify-full、ホスト名を検証する。いま: {_tls}）",
      _rc == 0 and _tls == {"SQL_TLS": "true", "SQL_TLS_DISABLE_HOST_VERIFICATION": "false", "SQL_TLS_CA_FILE": "/ca.pem",
                            "PGSSLMODE": "verify-full", "PGSSLROOTCERT": "/ca.pem"})
_rc, _tls, _ex, _err = _ts_tls(SQL_TLS_ENABLED="true", SQL_HOST_VERIFICATION="true")
check(f"TLS: SQL_HOST_VERIFICATION=true で SQL_CA が無ければ temporal-sql-tool を呼ばずに非 0 で止まり、SQL_CA が要ると言う（いま: rc={_rc} {_ex} {_err.strip()!r}）",
      _rc not in (0, None) and "_ARGS" not in _ex and "SQL_CA" in _err)
for env in ("NEPTUNE_GRAPH_ID", "AUDIT_TABLE_BUCKET_ARN", "AUDIT_NAMESPACE", "PROPOSAL_EVENTS_TABLE", "ANOMALY_QUEUE_URL", "DECISION_QUEUE_URL", "AGENT_RUNTIME_ARN", "LAB_INSTANCE_ID", "APPROVAL_TIMEOUT_MINUTES", "VERIFY_TIMEOUT", "HOLD_MINUTES", "PARAM_PREFIX"):
    check(f"worker の環境変数 {env} を渡す", f'name = "{env}"' in tf or f'name  = "{env}"' in tf or re.search(rf'name\s*=\s*"{env}"', tf) is not None)
# 渡した名前を worker が読んでいなければ、既定値のまま動いて気づけない
_env_read = set(re.findall(r'os\.environ\.get\("(\w+)"', read("app", "temporal", "worker.py") + read("app", "temporal", "awsio.py")))
_env_passed = set(re.findall(r'\{ name = "(\w+)", value', read("IaC", "terraform", "aws-managed", "workflow", "ecs.tf").split('name      = "worker"')[1]))
check(f"worker のコンテナに渡す環境変数は全部 worker.py / awsio.py が読む（読まれない: {sorted(_env_passed - _env_read - {'PARAM_PREFIX'})}）",
      _env_passed and not (_env_passed - _env_read - {"PARAM_PREFIX"}) and not ({"POLL_INTERVAL", "VERIFY_ATTEMPTS", "VERIFY_INTERVAL"} & _env_passed))
check("タスクロールは Runtime の InvokeAgentRuntime と lab への ssm:SendCommand（AWS-RunShellScript だけ）",
      '"bedrock-agentcore:InvokeAgentRuntime"' in tf and '"ssm:SendCommand"' in tf and "document/AWS-RunShellScript" in tf)
check("DynamoDB はもう使わない（修復案の「いま」は S3 Tables の proposal_events の最新の行）",
      "aws_dynamodb" not in tf and '"dynamodb:' not in tf and "ANOMALY_TABLE" not in tf and "PROPOSAL_TABLE" not in tf)
task_doc = re.search(r'data "aws_iam_policy_document" "task" \{[\s\S]*?\n\}\n', tf)
check("タスクロールは Neptune（トポロジの読み取り）と、修復案のテーブルの PutTableData / UpdateTableMetadataLocation",
      task_doc is not None and re.search(r'sid\s*=\s*"Neptune"', task_doc.group(0)) and '"neptune-graph:ReadDataViaQuery"' in task_doc.group(0)
      and re.search(r'sid\s*=\s*"AuditTable"', task_doc.group(0)) and '"s3tables:PutTableData"' in task_doc.group(0)
      and '"s3tables:UpdateTableMetadataLocation"' in task_doc.group(0))
_neptune_task = re.search(r'sid\s*=\s*"Neptune"[\s\S]*?\n  \}', task_doc.group(0)) if task_doc else None
check("タスクロールの Neptune は読むだけ（ReadDataViaQuery と GetQueryStatus。worker は read_topology の MATCH しか投げない）",
      _neptune_task is not None
      and re.findall(r'"(neptune-graph:\w+)"', _neptune_task.group(0)) == ["neptune-graph:ReadDataViaQuery", "neptune-graph:GetQueryStatus"]
      and "WriteDataViaQuery" not in task_doc.group(0) and "DeleteDataViaQuery" not in task_doc.group(0))
_dq_task = re.search(r'sid\s*=\s*"DecisionQueue"[\s\S]*?\n  \}', task_doc.group(0)) if task_doc else None
check("タスクロールは決定のキューを受けて消すだけ（ReceiveMessage / DeleteMessage / GetQueueAttributes。送るのは Web）",
      _dq_task is not None and re.findall(r'"(sqs:\w+)"', _dq_task.group(0)) == ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
      and "resources = [aws_sqs_queue.decisions.arn]" in _dq_task.group(0) and "sqs:SendMessage" not in task_doc.group(0))
check("タスクと Lambda は土台の workflow / lambda の SG を使い、SG もルールも作らない（ルールは土台の通信の表。2026-09-29）",
      re.search(r'security_groups\s*=\s*\[local\.workflow_sg_id\]', tf) is not None and re.search(r'security_group_ids\s*=\s*\[local\.lambda_sg_id\]', tf) is not None
      and 'resource "aws_security_group"' not in tf and "aws_vpc_security_group_" not in tf and "neptune_sg_id" not in tf)
check("graph と analytics が無ければ precondition で止まる（ワーカーが起きてから Neptune / 証跡に届かず落ちるより先に）",
      'local.neptune_graph_id != ""' in tf and 'local.audit_bucket_arn != ""' in tf and 'local.proposal_events_table_name != ""' in tf)
_reader_all = re.search(r'data "aws_iam_policy_document" "reader_access" \{[\s\S]*?\n\}\n', tf)
check("Runtime と Web のロールに SSM の読み取りを付け、Gateway の権限（InvokeGateway）は Runtime だけ（Web のコードは Gateway を呼ばない）",
      _reader_all is not None and _reader_all.group(0).count("for_each = local.reader_role_names") == 1
      and re.search(r'for_each = var\.create_gateway && each\.value == local\.runtime_role_name \? \[1\] : \[\]\n\s*content \{\n\s*sid\s*=\s*"Gateway"\n\s*actions\s*=\s*\["bedrock-agentcore:InvokeGateway"\]',
                   _reader_all.group(0)) is not None
      and re.search(r'resource "aws_iam_role_policy" "reader_access" \{\n\s*for_each = local\.reader_role_names[\s\S]*?'
                    r'policy = data\.aws_iam_policy_document\.reader_access\[each\.key\]\.json', tf) is not None
      and "runtime_role_name = data.terraform_remote_state.main.outputs.runtime_role_name" in tf
      and "reader_role_names = toset([local.runtime_role_name, local.web_role_name])" in tf
      and tf.count('"bedrock-agentcore:InvokeGateway"') == 2)
# 承認・却下を書けるのはコードの上では web だけ（decide はツールにしない）。Neptune の IAM は頂点ごとに絞れないので、線はコードで引く
check("修復案を決める専用の IAM（decide_access）はもう無い", "decide_access" not in tf)
check("Gateway は AWS_IAM 認可の MCP で、2025-06-18 を話す", 'authorizer_type = "AWS_IAM"' in tf and 'protocol_type   = "MCP"' in tf and '"2025-06-18"' in tf)
check("Gateway のターゲットは tools.json から inline schema を作る", 'jsondecode(file("${local.repo_root}/app/gateway/tools.json"))' in tf and 'dynamic "inline_payload"' in tf)
check("tools Lambda は python3.13 arm64 で、handler.py / toolkit / topology / evidence / proposals / graph / data を zip にする（anomalies は入れない）",
      'runtime          = "python3.13"' in tf and 'architectures    = ["arm64"]' in tf
      and all(f'"{p}"' in tf or f'{{local.repo_root}}/{p}"' in tf for p in ("app/gateway/handler.py", "app/agentcore/toolkit.py", "app/agentcore/topology.py", "app/agentcore/evidence.py", "app/agentcore/proposals.py", "app/agentcore/graph.py", "app/agentcore/data/topology.json", "app/agentcore/data/devices.yaml", "app/agentcore/data/layers.json"))
      and "app/agentcore/anomalies.py" not in tf)
# 入れ忘れても apply も plan も通り、実行時に ModuleNotFoundError になる。だから「入っている」ではなく「足りていないものが無い」を見る:
# zip に入れたモジュールが import する app/agentcore/ のモジュールが、全部 tools_files に並んでいるか
zipped = set(re.findall(r'^\s+"app/agentcore/(\w+)\.py"\s+=', tf, re.M))
needed = set()
for src in [("app", "gateway", "handler.py")] + [("app", "agentcore", m + ".py") for m in zipped]:
    needed |= {i for i in re.findall(r"^import (\w+)$", read(*src), re.M) if os.path.exists(os.path.join(ROOT, "app", "agentcore", i + ".py"))}
check(f"tools.zip は入れたモジュールが import する app/agentcore/ のモジュールを全部入れる（足りない: {sorted(needed - zipped)}）", zipped and not (needed - zipped))
check("tools Lambda は VPC の中（Neptune / OpenSearch / Prometheus に届く）で、OPENSEARCH_ENDPOINT / PROMETHEUS_QUERY_URL を渡す",
      re.search(r'resource "aws_lambda_function" "tools"[\s\S]*?vpc_config \{', tf) is not None
      and all(v in tf for v in ("OPENSEARCH_ENDPOINT", "OPENSEARCH_INDEX", "PROMETHEUS_QUERY_URL")))
# query_history（アラートの通知の履歴。2026-10-04）。analytics の出力を try で読み、無ければ環境変数も IAM も空
# 環境変数は merge の 1 つ目の map（2 つ目からは OSS 版だけ足す切り替え。tests/test_oss.py の 9）
_tools_env = re.search(r'resource "aws_lambda_function" "tools"[\s\S]*?variables = merge\(\n      \{([\s\S]*?)\n      \},', tf)
_tools_env_keys = set(re.findall(r"^\s*([A-Z_]+)\s*=", _tools_env.group(1), re.M)) if _tools_env else set()
check("tools Lambda に ATHENA_WORKGROUP / ATHENA_CATALOG / HISTORY_NAMESPACE / ALERT_EVENTS_TABLE / PROPOSAL_EVENTS_TABLE を渡す（値は analytics の出力）",
      {"ATHENA_WORKGROUP", "ATHENA_CATALOG", "HISTORY_NAMESPACE", "ALERT_EVENTS_TABLE", "PROPOSAL_EVENTS_TABLE"} <= _tools_env_keys
      and all(f'output "{o}"' in analytics_out and re.search(rf'try\(data\.terraform_remote_state\.analytics\.outputs\.{o}, ""\)', tf)
              for o in ("athena_workgroup", "athena_catalog", "alert_events_table_name", "alert_events_table_arn", "proposal_events_table_name", "proposal_events_table_arn"))
      and re.search(r'HISTORY_NAMESPACE\s+= local\.athena_workgroup == "" \? "" : local\.audit_namespace', tf) is not None)
_agent_env_read = set()
for _m in zipped:
    _agent_env_read |= set(re.findall(r'os\.environ\.get\("(\w+)"', read("app", "agentcore", _m + ".py")))
    _agent_env_read |= set(re.findall(r'Param\("(\w+)"', read("app", "agentcore", _m + ".py")))  # 環境変数が先、無ければ SSM（toolkit.Param）
check(f"tools Lambda に渡す環境変数は全部 zip のモジュールが読む（読まれない: {sorted(_tools_env_keys - _agent_env_read)}）",
      _tools_env_keys and not (_tools_env_keys - _agent_env_read))
_hist_stmts = {sid: re.search(rf'sid\s*=\s*"{sid}"[\s\S]*?\n    \}}', tf) for sid in ("HistoryQuery", "HistoryCatalog", "HistoryBucket", "HistoryTable")}
check("履歴の IAM（locals.tf の history_read_statements）は analytics があるときだけで、tools Lambda と Web の EC2 に同じものを付ける。"
      "athena はワークグループ、s3tables のテーブルの読み取りは alert_events と proposal_events だけ（読むだけ）",
      all(_hist_stmts.values()) and tf.count("for_each = local.history_read_statements") == 2
      and "history_table_arns = compact([local.alert_events_table_arn, local.proposal_events_table_arn])" in tf
      and re.search(r'\] : s if local\.athena_workgroup != "" && length\(compact\(s\.resources\)\) > 0\]', tf) is not None
      and 'alert_events_table_arn  = try(data.terraform_remote_state.analytics.outputs.alert_events_table_arn, "")' in tf
      and re.findall(r'"(athena:\w+)"', _hist_stmts["HistoryQuery"].group(0)) == ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution"]
      and "workgroup/${local.athena_workgroup}" in _hist_stmts["HistoryQuery"].group(0)
      and re.findall(r'"(glue:\w+)"', _hist_stmts["HistoryCatalog"].group(0)) == ["glue:GetCatalog", "glue:GetDatabase", "glue:GetTable"]
      and "catalog/s3tablescatalog/*" in _hist_stmts["HistoryCatalog"].group(0)
      and re.findall(r'"(s3tables:\w+)"', _hist_stmts["HistoryBucket"].group(0)) == ["s3tables:GetTableBucket", "s3tables:GetNamespace"]
      and "resources = [local.audit_bucket_arn]" in _hist_stmts["HistoryBucket"].group(0)
      and re.findall(r'"(s3tables:\w+)"', _hist_stmts["HistoryTable"].group(0)) == ["s3tables:GetTable", "s3tables:GetTableData", "s3tables:GetTableMetadataLocation"]
      and "resources = local.history_table_arns" in _hist_stmts["HistoryTable"].group(0)
      and not any("*" == a.split(":")[-1] for s in _hist_stmts.values() for a in re.findall(r'"((?:athena|glue|s3tables):[\w*]+)"', s.group(0))))
# トポロジは読むだけ（修復案は Neptune に無く、ツールに承認・却下も無い。承認は画面の承認タブで人が決める）
neptune_read =re.search(r'sid\s*=\s*"NeptuneRead"[\s\S]*?\n  \}', tf)  # ステートメント 1 つぶん（terraform fmt の桁揃えに依存しないよう粗く取る）
check("tools Lambda のロールの Neptune は読むだけ（WriteDataViaQuery は付けない）",
      neptune_read is not None and "WriteDataViaQuery" not in neptune_read.group(0) and "DeleteDataViaQuery" not in neptune_read.group(0)
      and "neptune-graph:ReadDataViaQuery" in neptune_read.group(0))
check("tools Lambda のロールに aoss:APIAccessAll と aps:QueryMetrics、コレクションの data access policy",
      '"aoss:APIAccessAll"' in tf and '"aps:QueryMetrics"' in tf and 'resource "aws_opensearchserverless_access_policy" "tools"' in tf)
check("SQS（anomalies）は土台の SNS トピックを raw message delivery で購読し、DLQ は 5 回で（購読の配信失敗も同じ DLQ へ）",
      re.search(r'resource "aws_sns_topic_subscription" "anomalies" \{[\s\S]*?topic_arn\s*=\s*local\.alerts_topic_arn[\s\S]*?protocol\s*=\s*"sqs"'
                r'[\s\S]*?endpoint\s*=\s*aws_sqs_queue\.anomalies\.arn[\s\S]*?raw_message_delivery\s*=\s*true[\s\S]*?deadLetterTargetArn = aws_sqs_queue\.anomalies_dlq\.arn', tf) is not None
      and 'resource "aws_sqs_queue" "anomalies"' in tf and 'resource "aws_sqs_queue" "anomalies_dlq"' in tf
      and re.search(r'redrive_policy[\s\S]*?maxReceiveCount\s*=\s*5', tf) is not None
      and "depends_on = [aws_sqs_queue_policy.anomalies, aws_sqs_queue_policy.anomalies_dlq]" in tf)
check("アラートのキュー 2 つのポリシーは sns.amazonaws.com の SendMessage をそのトピックに絞る",
      tf.count('identifiers = ["sns.amazonaws.com"]') == 2 and tf.count("values   = [local.alerts_topic_arn]") == 2 and '"sqs:SendMessage"' in tf)
check("EventBridge のルールはもう無い（Spark の検知と一緒にやめた。2026-10-02）",
      "aws_cloudwatch_event_" not in tf and "events.amazonaws.com" not in tf and "AnomalyOpened" not in tf)
check("タスクロールは SQS の ReceiveMessage / DeleteMessage", '"sqs:ReceiveMessage", "sqs:DeleteMessage"' in tf)
_events_tf = read("IaC", "terraform", "aws-managed", "workflow", "events.tf")
check("決定のキュー（decisions）と DLQ（5 回）があり、SNS は購読しない（Web が直接送る）",
      'resource "aws_sqs_queue" "decisions"' in _events_tf and 'resource "aws_sqs_queue" "decisions_dlq"' in _events_tf
      and re.search(r'resource "aws_sqs_queue" "decisions" \{[\s\S]*?deadLetterTargetArn = aws_sqs_queue\.decisions_dlq\.arn[\s\S]*?maxReceiveCount\s*=\s*5', _events_tf) is not None
      and "aws_sqs_queue.decisions.arn" not in "".join(re.findall(r'resource "aws_sns_topic_subscription"[\s\S]*?\n\}', tf)))
_web_doc = re.search(r'data "aws_iam_policy_document" "web_access" \{[\s\S]*?\n\}\n', tf)
_reader_doc = re.search(r'data "aws_iam_policy_document" "reader_access" \{[\s\S]*?\n\}\n', tf)
_tools_doc = re.search(r'data "aws_iam_policy_document" "tools" \{[\s\S]*?\n\}\n', tf)
check("決定のキューに送れる（sqs:SendMessage）のは Web の EC2 のロールだけ（web_access は web_role_name に付ける。Runtime と tools Lambda には付けない = チャットから承認できない）",
      _web_doc is not None and re.search(r'sid\s*=\s*"DecisionQueue"\s*\n\s*actions\s*=\s*\["sqs:SendMessage"\]\s*\n\s*resources = \[aws_sqs_queue\.decisions\.arn\]', _web_doc.group(0)) is not None
      and "for_each = local.history_read_statements" in _web_doc.group(0)
      and re.search(r'resource "aws_iam_role_policy" "web_access" \{[\s\S]*?role\s*=\s*local\.web_role_name', tf) is not None
      and _reader_doc is not None and "sqs:" not in _reader_doc.group(0) and "athena:" not in _reader_doc.group(0)
      and _tools_doc is not None and "sqs:" not in _tools_doc.group(0))
check("Web の設定は SSM（decision-queue-url と、Athena の 4 つは空でないものだけ）",
      '"${local.param_prefix}/decision-queue-url"' in tf and "value = aws_sqs_queue.decisions.url" in tf
      and all(f'"{k}"' in tf for k in ("athena-workgroup", "athena-catalog", "history-namespace", "proposal-events-table"))
      and 'k => v if v != ""' in tf)
check("workflow はエンドポイントを持たない（SQS / S3 Tables / AgentCore へは土台のインターフェース型エンドポイント。2026-09-28）", 'resource "aws_vpc_endpoint"' not in tf and "create_sqs_endpoint" not in tf)
check("閉域: 実行ロール・タスクロール・tools Lambda に perimeter を付け、アラートのキュー 2 つ・決定のキュー 2 つ（for_each）と Gateway は VPC の外からの呼び出しを拒む",
      all(f'resource "aws_iam_role_policy_attachment" "{n}"' in tf for n in ("execution_perimeter", "task_perimeter", "tools_perimeter"))
      and tf.count('sid         = "DenyOutsideVpc"') == 3 and "not_actions = local.sqs_policy_actions" in tf
      and re.search(r'resource "aws_bedrockagentcore_resource_policy" "gateway"[\s\S]*?"bedrock-agentcore:InvokeGateway"[\s\S]*?aws_bedrockagentcore_gateway\.tools\[0\]\.gateway_arn[\s\S]*?"aws:SourceVpc"', tf) is not None
      and all(v in tf for v in ('"aws:ViaAWSService"', '"aws:PrincipalIsAWSService"', "local.perimeter_exempt_principals")))
check("output に anomaly_queue_url / anomaly_dlq_url / decision_queue_url / decision_dlq_url / tools_function_name があり、anomaly_rule_name は無い",
      all(f'output "{o}"' in tf for o in ("anomaly_queue_url", "anomaly_dlq_url", "decision_queue_url", "decision_dlq_url", "tools_function_name")) and "anomaly_rule_name" not in tf)
check("Gateway の URL を SSM の gateway-url に書く", '"${local.param_prefix}/gateway-url"' in tf)
check("aws_iam_role の description は ASCII だけ",
      all(d.isascii() for d in re.findall(r'resource "aws_iam_role"[\s\S]*?description\s*=\s*"([^"]*)"', tf)))
check("Fargate のタスクは 1 vCPU / 2 GB が既定（≒ $0.05/h）", 'default     = 1024' in tf and 'default     = 2048' in tf)
check("ログの保持期間を書く", "retention_in_days = var.log_retention_days" in tf)
check("mcp_client は SigV4 のサービス名 bedrock-agentcore で署名する", '"bedrock-agentcore"' in read("app", "agentcore", "mcp_client.py"))
check("app/agentcore/app.py は Gateway のツールを先に、無ければコンテナ内の関数を使う", "mcp_client.tool_specs() or TOOL_SPECS" in read("app", "agentcore", "app.py") and "mcp_client.has(name)" in read("app", "agentcore", "app.py"))
check("docker/images/agentcore/Dockerfile は toolkit.py / mcp_client.py / proposals.py を入れる",
      all(f"{m}.py" in read("docker", "images", "agentcore", "Dockerfile").split("COPY app.py")[1].split("\n")[0] for m in ("toolkit", "mcp_client", "proposals")))
check("docker/images/temporal/Dockerfile は非 root で worker.py を打つ", "USER worker" in read("docker", "images", "temporal", "Dockerfile") and '["python", "worker.py"]' in read("docker", "images", "temporal", "Dockerfile"))
# 1 つずつ COPY すると、足したファイルを入れ忘れて起動時に ModuleNotFoundError になる（分割で 3 本になった）
check("docker/images/temporal/Dockerfile は *.py をまとめて入れる", "COPY *.py ./" in read("docker", "images", "temporal", "Dockerfile"))
check("app/temporal/requirements.txt は temporalio / boto3 / pyiceberg[pyarrow]（証跡の append）を固定する",
      all(r in read("app", "temporal", "requirements.txt") for r in ("temporalio==", "boto3>=", "pyiceberg[pyarrow]==")))
for _f in ("worker.py", "awsio.py", "rules.py"):
    ast.parse(read("app", "temporal", _f))
ecr_tf = read("IaC", "terraform", "aws-managed", "base", "ecr", "main.tf")
check("IaC/terraform/aws-managed/base/ecr は worker / temporal / temporal-ui のリポジトリを作る", '"worker", "temporal", "temporal-ui"' in ecr_tf and 'resource "aws_ecr_repository" "workflow"' in ecr_tf)

# ---- web（app.py は画面の組み立てだけ。タブの中身は分けてある）
web = read("app", "dashboard", "app.py")
web_srcs = sorted(n for n in os.listdir(os.path.join(ROOT, "app", "dashboard")) if n.endswith(".py"))
check("web は app / config / chat / topology_view / incident_view / nautobot_api に分かれる",
      set(web_srcs) == {"app.py", "config.py", "chat.py", "topology_view.py", "incident_view.py", "nautobot_api.py"})
# user_data は $APP/src/app.py の 1 行目で置き間違いを見るので、app.py の import gradio は行頭のまま動かさない
check("app.py には行頭の import gradio がある（user_data の置き間違い検出が見ている）",
      re.search(r"^import gradio as gr$", web, re.M) is not None
      and 'grep -q "^import gradio"' in read("IaC", "terraform", "aws-managed", "base", "core", "templates", "web_user_data.sh.tftpl"))
# cloud-init は MIME の 8bit の部分を raw-unicode-escape で取り出すので、日本語は「運」がバックスラッシュ付きの u904b になり、EnvironmentFile で u904b に崩れる
web_ud = read("IaC", "terraform", "aws-managed", "base", "core", "templates", "web_user_data.sh.tftpl")
check("web の user_data はコメント以外が ASCII だけで、TITLE を渡さない（タイトルは config.py の既定値）",
      all(l.isascii() for l in web_ud.splitlines() if not l.lstrip().startswith("#"))
      and "TITLE=" not in web_ud and 'os.environ.get("TITLE", "運用管理ダッシュボード")' in read("app", "dashboard", "config.py"))
incident = read("app", "dashboard", "incident_view.py")
# 異常一覧のタブは 2026-10-02 にやめた（Neptune に異常の頂点を置かない。いまの異常はトポロジの状態と Grafana / Splunk で見る）
check("Web のタブはチャット / トポロジ / 承認の 3 つ（異常一覧は無い）",
      re.findall(r'gr\.Tab\("([^"]+)"\)', web) == ["チャット", "トポロジ", "承認"]
      and "anomalies" not in web and "import anomalies" not in incident and "anomaly_table" not in incident)
check("Web に「承認」タブがあり、名前と「読んだ」のチェックを添えて proposals.decide で approved / rejected を送る",
      'gr.Tab("承認")' in web and 'iv.decide_proposal(i, "approved", s, w, ok), [pr_id, pr_status, pr_who, pr_ok]' in web
      and 'iv.decide_proposal(i, "rejected", s, w, ok), [pr_id, pr_status, pr_who, pr_ok]' in web
      and "pr_id.change(lambda _: False, [pr_id], [pr_ok])" in web
      and "import proposals" in incident and "proposals.decide(" in incident)
check("承認・却下の結果はボタンの下の pr_result に出す（表の上の pr_msg は 30 秒ごとの描き直しが上書きし、押しても何も起きないように見える）",
      web.count("[pr_id, pr_status, pr_who, pr_ok],\n                          [pr_result, pr_table, pr_id])") == 2
      and "[pr_id, pr_status, pr_who, pr_ok], pr_out)" not in web)
# 入れ忘れても apply は通り、EC2 の起動時に ModuleNotFoundError になる（tools.zip と同じ事故）。
# app/dashboard/*.py は upload_web_command が app/dashboard/ ごと上げるので、確かめるのは app/agentcore/ から借りるモジュールの側
web_shared = set()
for n in web_srcs:
    web_shared |= {i for i in re.findall(r"^import (\w+)", read("app", "dashboard", n), re.M) if os.path.exists(os.path.join(ROOT, "app", "agentcore", i + ".py"))}
uploaded = set(re.search(r"for f in ([\w ]+); do", main_out).group(1).split())
check(f"main の upload_web_command は Web が import する agent のモジュールを全部上げる（足りない: {sorted(web_shared - uploaded)}）",
      web_shared and not (web_shared - uploaded))
# 上の検査は app/dashboard/*.py の import しか見ない。agent のモジュールどうしの import（proposals → evidence など）と、
# import のときに boto3 のクライアントを作ること（Web の EC2 は修復案を配備していなくても起動する）は、上げる分だけを別のディレクトリに写して確かめる
import shutil as _sh, subprocess as _wsp, tempfile as _tf  # noqa: E402,E401
_wd = _tf.mkdtemp()
for _m in uploaded:
    _sh.copy(os.path.join(ROOT, "app", "agentcore", _m + ".py"), _wd)
_sh.copytree(os.path.join(ROOT, "app", "agentcore", "data"), os.path.join(_wd, "data"))
_child = r'''
import sys, types
made = []
def _no(*a, **k):
    made.append(a)
    raise RuntimeError("boto3 のクライアントを import のときに作った")
boto3 = types.ModuleType("boto3"); boto3.client = boto3.Session = _no
botocore = types.ModuleType("botocore"); exc = types.ModuleType("botocore.exceptions"); cfg = types.ModuleType("botocore.config")
class ClientError(Exception): pass
class BotoCoreError(Exception): pass
exc.ClientError, exc.BotoCoreError, cfg.Config = ClientError, BotoCoreError, (lambda **k: k)
sys.modules.update({"boto3": boto3, "botocore": botocore, "botocore.exceptions": exc, "botocore.config": cfg})
sys.path.insert(0, sys.argv[1])
import proposals, toolkit, topology, graph
print("RESULT", sorted(m for m in ("evidence", "app", "mcp_client") if m in sys.modules), made,
      proposals.list_proposals() == {"error": proposals.NOT_DEPLOYED, "proposals": []})
'''
_wr = _wsp.run([sys.executable, "-I", "-c", _child, _wd], capture_output=True, text=True, cwd=_wd, env={"PATH": os.environ.get("PATH", "")}, timeout=60)
_sh.rmtree(_wd, ignore_errors=True)
check(f"Web に上げる agent のモジュール（{' '.join(sorted(uploaded))}）だけで proposals を import でき、import で boto3 のクライアントを作らず、"
      f"設定が無ければ NOT_DEPLOYED を返す（evidence などは読まない）: {(_wr.stdout + _wr.stderr).strip()[-300:]}",
      _wr.returncode == 0 and "RESULT [] [] True" in _wr.stdout)

# ---- ops
up = read_ops("up"); down = read_ops("down"); chk = read("ops", "check.sh")
_down_body = read("ops", "down.sh")
check("up.sh の WORKFLOW=1 は AGENT と PIPELINE が要り、SKIP_LAB / SKIP_STREAM / SKIP_ANALYTICS があれば止まる",
      re.search(r'if \[ -n "\$WORKFLOW" \]; then\n\s*if \[ -z "\$AGENT" \]; then[\s\S]*?if \[ -z "\$PIPELINE" \]; then[\s\S]*?SKIP_LAB[\s\S]*?SKIP_STREAM[\s\S]*?SKIP_ANALYTICS', up) is not None)
# AGENT の既定は 2026-10-04 に 1 → 0（機能を書かなければ土台だけ）。機能の判定を up.sh から切り出して動かす
import subprocess as _sp
_featblk = up[up.index('AGENT="${AGENT:-0}"'):up.index('if [ -n "$PIPELINE" ]; then\n  if [ -n "$SKIP_LAB" ]')]
_flag = ('die() { echo "DIE: $*"; exit 1; }\n'
         'flag_value() { local name="$1" v; v="${!name:-}"; case "$v" in 1|true|yes) printf -v "$name" %s 1 ;; ""|0|false|no) printf -v "$name" %s "" ;; *) die "$name は 1 か 0" ;; esac; }\n')
def _feat(**env):
    r = _sp.run(["bash", "-c", _flag + _featblk + 'echo "OUT: AGENT=${AGENT:-0}"'], capture_output=True, text=True, env={"PATH": os.environ["PATH"], **env})
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr
check("up.sh の AGENT の既定は 0（何も書かなければ agent を作らない）。1 を書けば作る",
      'AGENT="${AGENT:-0}"' in up and 'AGENT="${AGENT:-1}"' not in up
      and _feat() == "OUT: AGENT=0" and _feat(AGENT="1") == "OUT: AGENT=1" and _feat(AGENT="yes") == "OUT: AGENT=1")
check("up.sh の WORKFLOW=1 は AGENT を書かなければ（既定 0）止まり、AGENT=1 を書けと言う",
      _feat(WORKFLOW="1", PIPELINE="1").startswith("DIE: WORKFLOW は AGENT が要る") and "AGENT=1 を書く" in _feat(WORKFLOW="1", PIPELINE="1")
      and _feat(WORKFLOW="1", PIPELINE="1", AGENT="1") == "OUT: AGENT=1")
_basemsg = up[up.index('if [ -z "$AGENT$PIPELINE$WORKFLOW" ]; then'):].split("\nfi\n", 1)[0]
check("機能が全部 0（既定）のときの案内は、Web は開けてチャットは配備されていないこと、チャットには AGENT=1 を書くことを言う",
      "AGENT=1 を書く" in _basemsg and "配備されていない" in _basemsg and "（既定）" in _basemsg)
_costnote = up[up.index("COST_NOTE=$(printf"):up.index("\n", up.index("COST_CENTS * 150")) + 1]
def _note(**env):
    r = _sp.run(["bash", "-c", "COST_CENTS=123\n" + _costnote + 'echo "$COST_NOTE"'], capture_output=True, text=True, env={"PATH": os.environ["PATH"], **env})
    return r.stdout.strip()
check("費用の案内の「チャットの分は別」は AGENT があるときだけ出す（金額の $ は展開しない）",
      _note() == "待機だけで約 $1.23/h（約 185 円/h）の時間課金。使い終わったら当日中に ops/down.sh を打つ"
      and _note(AGENT="1") == "待機だけで約 $1.23/h（約 185 円/h。チャットの分は別）の時間課金。使い終わったら当日中に ops/down.sh を打つ")
check("deploy-env.sh の deploy.env が無いときの案内は、既定を土台だけと言う（AGENT=1 だけ、とは言わない）",
      "既定は AGENT=1" not in read("ops", "deploy-env.sh") and "既定は土台だけ" in read("ops", "deploy-env.sh"))
check("deploy-env.sh の読めるキーは機能の 3 つ + CREATE_KB + TF_VERBOSE で、古いキー（PHASE / SINKS / WITH_*）は持たない",
      (lambda keys: all(k in keys for k in ("PIPELINE", "AGENT", "WORKFLOW", "CREATE_KB", "TF_VERBOSE"))
       and not any(k in keys for k in ("PHASE", "SINKS", "WITH_LAB", "WITH_STREAM")))(read("ops", "deploy-env.sh").split('DEPLOY_ENV_KEYS="')[1].split('"')[0].split())
      and not re.search(r'\bPHASE\b|\bWITH_LAB\b|\bWITH_STREAM\b', up))
check("up.sh の WORKFLOW=1 は link_down のアラートの送り手（Grafana か Splunk）が無ければ止まる（ワークフローを起こすのは link_down だけ）",
      re.search(r'if \[ -n "\$WORKFLOW" \] && \[ -z "\$LINK_DOWN_SENDERS" \]; then\n\s*die "WORKFLOW はアラートの送り手が要る', up) is not None
      and up.index('LINK_DOWN_SENDERS=') < up.index('[ -z "$LINK_DOWN_SENDERS" ]'))
# ---- starter: SQS のメッセージ（SNS のトピックの購読）
check("starter はアラートと決定の 2 つのキューを 20 秒の long polling で待ち、ANOMALY_QUEUE_URL / DECISION_QUEUE_URL が無ければ起動で止まる（表を見る経路はもう無い）",
      all(hasattr(awsio, f) for f in ("receive_messages", "delete_message")) and awsio.GRAPH_ENV == "NEPTUNE_GRAPH_ID"
      and "WaitTimeSeconds=20" in read("app", "temporal", "awsio.py")
      and re.search(r'for k in \("ANOMALY_QUEUE_URL", "DECISION_QUEUE_URL", awsio\.GRAPH_ENV, "AUDIT_TABLE_BUCKET_ARN", "AUDIT_NAMESPACE", "AGENT_RUNTIME_ARN"\):\n\s*if not getattr\(awsio, k\):\n\s*raise SystemExit',
                    read("app", "temporal", "worker.py")) is not None
      and "starter(client, awsio.ANOMALY_QUEUE_URL, handle_message)" in read("app", "temporal", "worker.py")
      and "starter(client, awsio.DECISION_QUEUE_URL, handle_decision)" in read("app", "temporal", "worker.py"))
check("up.sh は workflow ルートを足し、費用に 5 セント足す（Fargate だけ。sqs のエンドポイントは無くなった）", 'ROOTS="$ROOTS workflow"' in up and 'COST_CENTS=$((COST_CENTS + 5))' in up)
check("up.sh は worker を buildx でビルドし、Temporal のサーバーを docker/images/temporal-server/ から arm64 でビルドし、temporalio/ui を ECR にミラーする",
      '--push -f docker/images/temporal/Dockerfile app/temporal/' in up
      and '--build-arg "TEMPORAL_SERVER_VERSION=$TEMPORAL_SERVER_VERSION" --build-arg "POSTGRES_MAJOR=$POSTGRES_MAJOR"' in up
      and '-t "$REG/$PREFIX-temporal:$1" --push docker/images/temporal-server/' in up
      and 'docker pull --platform linux/arm64 "temporalio/ui:$TEMPORAL_UI_TAG"' in up and '"$REG/$PREFIX-temporal-ui:$TEMPORAL_UI_TAG"' in up
      and "temporalio/temporal" not in up and "TEMPORAL_TAG=" not in up and "mirror_temporal " not in up + " ")
for _name, _src in (("ops/up.sh", read("ops", "up.sh")), ("ops/oss/up.sh", read("ops", "oss", "up.sh"))):
    check(f"{_name} は temporal のタグを docker/images/temporal-server/ の中身から作り（dir_tag）、無ければビルド、UI は TEMPORAL_UI_TAG で無ければミラーする",
          'TEMPORAL_SERVER_IMAGE_TAG=$(dir_tag "$TEMPORAL_SERVER_VERSION" docker/images/temporal-server)' in _src
          and 'ecr_has "$PREFIX-temporal" "$TEMPORAL_SERVER_IMAGE_TAG"' in _src and 'ecr_has "$PREFIX-temporal-ui" "$TEMPORAL_UI_TAG"' in _src
          and 'build_temporal_server "$TEMPORAL_SERVER_IMAGE_TAG"' in _src and "mirror_temporal_ui" in _src
          and "$NEED_TEMPORAL$NEED_TEMPORAL_UI" in _src)
    check(f"{_name} は arm64 の buildx を temporal のビルドでも確かめる（RUN apk がある）",
          re.search(r'if \[ -n "[^"]*\$NEED_TEMPORAL[^_"][^"]*" \] && ! grep -q \'linux/arm64\'|if \[ -n "[^"]*\$NEED_TEMPORAL" \] && ! grep -q \'linux/arm64\'', _src) is not None)
    check(f"{_name} は nautobot に db_engine_version=$POSTGRES_MAJOR を、workflow に temporal_image_tag を渡し、workflow の前にロール temporal のパスワードを作る",
          '-var "db_engine_version=$POSTGRES_MAJOR"' in _src and '-var "temporal_image_tag=$TEMPORAL_SERVER_IMAGE_TAG"' in _src
          and _src.index("ensure_temporal_secrets") < _src.index("tf_apply workflow") and _src.index("tf_apply pipeline/nautobot") < _src.index("tf_apply workflow"))
    # init のタスク（cycle 042）は apply の後（タスク定義ができてから）、services-stable の前（サーバーはスキーマが揃うのを待っている）
    _wf_stable = _src.find('aws ecs wait services-stable --region "$REGION" --cluster "$WF_CLUSTER"')
    _rti = [m.start() for m in re.finditer(r"^\s*run_temporal_init\s*$", _src, re.M)]
    check(f"{_name} は run_temporal_init を tf_apply workflow の直後、workflow の services-stable の前に 1 回呼ぶ（いま: {len(_rti)} 回）",
          len(_rti) == 1 and 0 <= _src.index("tf_apply workflow") < _rti[0] < _wf_stable
          and re.search(r"tf_apply workflow [^\n]*\n(\s*#[^\n]*\n)*\s*run_temporal_init\n", _src) is not None)
_upc_rti = _upc.split("run_temporal_init() {")[1].split("\n}\n")[0] if "run_temporal_init() {" in _upc else ""
_rti_pos = [_upc_rti.find(s) for s in ('aws ecs list-tasks --region "$REGION" --cluster "$cluster" --family "$family" --desired-status RUNNING ',
                                       'aws ecs wait tasks-stopped --region "$REGION" --cluster "$cluster" --tasks $prev ', "aws ecs run-task ",
                                       'aws ecs wait tasks-stopped --region "$REGION" --cluster "$cluster" --tasks "$task" ', "aws ecs describe-tasks ",
                                       '[ "${code:-}" = 0 ] || die ')]
check(f"run_temporal_init は ecs list-tasks（前の init）→ その tasks-stopped → ecs run-task → ecs wait tasks-stopped → describe-tasks の exitCode の順で、"
      f"0 以外（None を含む）で die する（Round 1 のセルフレビューの Should 5。いま: {_rti_pos}）",
      -1 not in _rti_pos and _rti_pos == sorted(_rti_pos)
      and "containers[0].exitCode" in _upc_rti and "--launch-type FARGATE" in _upc_rti and "assignPublicIp=DISABLED" in _upc_rti
      and all(f"tf_output workflow {o})" in _upc_rti for o in ("cluster_name", "init_task_definition", "task_subnet_id", "task_security_group_id", "init_logs_command")))
def _rti_run(run_out, desc_out, wait_fails=0, run_rc=0, prev_out="", list_rc=0):
    """run_temporal_init を bash で、aws（PATH の差し替え）と tf_output / die（関数）を差し替えて動かす。rc と aws の呼び出しと stdout / stderr を返す。
    list-tasks（前の init）は prev_out を返す。wait は呼ばれた順に数え、wait_fails 回目までは時間切れ（前の init の待ちも数える）。"""
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "aws"), "w") as f:
            f.write(f'#!/bin/bash\necho "aws $*" >> {d}/calls\ncase "$2" in\n'
                    f'  list-tasks) printf "%b\\n" "$PREV_OUT"; exit $LIST_RC;;\n'
                    f'  run-task) printf "%b\\n" "$RUN_OUT"; exit $RUN_RC;;\n'
                    f'  wait) echo $# >> {d}/wargc; n=$(cat {d}/w 2>/dev/null || echo 0); n=$((n + 1)); echo $n > {d}/w; [ "$n" -gt "$WAIT_FAILS" ]; exit $?;;\n'
                    f'  describe-tasks) printf "%b\\n" "$DESC_OUT";;\nesac\n')
        os.chmod(os.path.join(d, "aws"), 0o755)
        script = ("set -euo pipefail\nREGION=ap-northeast-1\ndie() { echo \"DIE: $*\" >&2; exit 1; }\n"
                  "tf_output() { case \"$2\" in cluster_name) echo c1;; init_task_definition) echo arn:aws:ecs:r:1:task-definition/p-workflow-init:3;;"
                  " task_subnet_id) echo subnet-1;; task_security_group_id) echo sg-1;; init_logs_command) echo 'aws logs tail g --log-stream-name-prefix init';; esac; }\n"
                  f"run_temporal_init() {{{_upc_rti}\n}}\nrun_temporal_init\necho DONE\n")
        env = {"PATH": d + os.pathsep + os.environ.get("PATH", ""), "RUN_OUT": run_out, "DESC_OUT": desc_out, "WAIT_FAILS": str(wait_fails), "RUN_RC": str(run_rc),
               "PREV_OUT": prev_out, "LIST_RC": str(list_rc)}
        out = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30, stdin=_vsp.DEVNULL)
        calls = open(os.path.join(d, "calls")).read().splitlines() if os.path.exists(os.path.join(d, "calls")) else []
        wargc = [int(n) for n in open(os.path.join(d, "wargc")).read().split()] if os.path.exists(os.path.join(d, "wargc")) else []
    return out.returncode, [" ".join(c.split()[1:3]) for c in calls], calls, out.stdout, out.stderr, wargc
_TASK = "arn:aws:ecs:r:1:task/c1/abc"
_r = _rti_run(f"{_TASK}\\t0\\tNone\\tNone\\tNone", "STOPPED\\t0\\tEssentialContainerExited\\tEssential container in task exited")
check(f"run_temporal_init: exitCode 0 なら run-task → wait → describe-tasks で抜け、先へ進む（サービスと同じサブネット・SG、公開 IP 無し。いま: rc={_r[0]} {_r[1]} {_r[4].strip()!r}）",
      _r[0] == 0 and "DONE" in _r[3] and _r[1] == ["ecs list-tasks", "ecs run-task", "ecs wait", "ecs describe-tasks"]
      and "--cluster c1 --family p-workflow-init --desired-status RUNNING" in _r[2][0]
      and "--task-definition arn:aws:ecs:r:1:task-definition/p-workflow-init:3 --launch-type FARGATE --count 1" in _r[2][1]
      and "awsvpcConfiguration={subnets=[subnet-1],securityGroups=[sg-1],assignPublicIp=DISABLED}" in _r[2][1]
      and f"--tasks {_TASK}" in _r[2][2] and f"--tasks {_TASK}" in _r[2][3] and "Temporal の初期化が終わった（exitCode=0）" in _r[3]
      and "前の初期化のタスク" not in _r[3])
for _desc, _what in (("STOPPED\\t1\\tEssentialContainerExited\\tEssential container in task exited", "exitCode=1"),
                     ("STOPPED\\tNone\\tTaskFailedToStart\\tCannotPullContainerError: pull image manifest has been retried", "exitCode=None")):
    _r = _rti_run(f"{_TASK}\\t0\\tNone\\tNone\\tNone", _desc)
    check(f"run_temporal_init: {_what}（{_desc.split(chr(92))[0]}…）なら die し、止まった理由と init のログの見方を出す（いま: rc={_r[0]} {_r[4].strip()[-100:]!r}）",
          _r[0] != 0 and "DONE" not in _r[3] and f"DIE: Temporal の初期化のタスク abc が失敗した（lastStatus=STOPPED {_what} " in _r[4]
          and "aws logs tail g --log-stream-name-prefix init" in _r[4])
for _out in ("None\\t1", f"{_TASK}\\t1"):
    _r = _rti_run(_out, "")
    check(f"run_temporal_init: run-task が failures を返せば（タスクの ARN があっても）待たずに die（{_out.split(chr(92))[0][-8:]}…。いま: rc={_r[0]} {_r[1]} {_r[4].strip()[-60:]!r}）",
          _r[0] != 0 and _r[1] == ["ecs list-tasks", "ecs run-task"] and "DIE: Temporal の初期化のタスクを起こせない（run-task の返り: " in _r[4])
# 偽の aws の run-task は、aws CLI の --output text が平らな --query に返す形（1 行に TAB 区切り。null は None。成功は <taskArn>\t0\tNone\tNone\tNone）を
# 返す（cycle 042 Round 3。cold review Round 2 の Should 2）。Fargate のキャパシティ不足の reason は空白を含む文なので、TAB だけで切っていないと最初の語で切れる
_r = _rti_run("None\\t1\\tarn:aws:ecs:r:1:container-instance/ci-1\\tRESOURCE:ENI\\tENI limit reached for subnet-1", "")
_r2 = _rti_run("None\\t1\\tNone\\tCapacity is unavailable at this time. Please try again later\\tNone", "")
check(f"run_temporal_init: run-task の failures があれば die の文面に failures[0] の reason / arn / detail を出す（Fargate のキャパシティ不足などは"
      f"ここにしか出ない。いま: rc={_r[0]} {_r[1]} {_r[4].strip()[-160:]!r} / rc={_r2[0]} {_r2[4].strip()[-110:]!r}）",
      _r[0] != 0 and _r[1] == ["ecs list-tasks", "ecs run-task"]
      and " --query [tasks[0].taskArn, length(failures), failures[0].arn, failures[0].reason, failures[0].detail] --output text" in (_r[2][1] if len(_r[2]) > 1 else "")
      and "reason=RESOURCE:ENI " in _r[4] and "arn=arn:aws:ecs:r:1:container-instance/ci-1 " in _r[4]
      and "detail=ENI limit reached for subnet-1）" in _r[4] and "failures=1 件" in _r[4]
      and _r2[0] != 0 and _r2[1] == ["ecs list-tasks", "ecs run-task"]
      and "reason=Capacity is unavailable at this time. Please try again later arn=None detail=None）" in _r2[4])
_r = _rti_run("", "", run_rc=255)
check(f"run_temporal_init: run-task そのものが失敗すれば待たずに die（権限の手がかりを出す。いま: rc={_r[0]} {_r[1]} {_r[4].strip()[-60:]!r}）",
      _r[0] != 0 and _r[1] == ["ecs list-tasks", "ecs run-task"] and "DIE: Temporal の初期化のタスクを起こせない" in _r[4] and "iam:PassRole" in _r[4])
_r = _rti_run(f"{_TASK}\\t0\\tNone\\tNone\\tNone", "STOPPED\\t0\\tx\\ty", wait_fails=1)
check(f"run_temporal_init: tasks-stopped が 1 回目で時間切れでも 2 回目で止まれば先へ進む（いま: rc={_r[0]} {_r[1]}）",
      _r[0] == 0 and "DONE" in _r[3] and _r[1] == ["ecs list-tasks", "ecs run-task", "ecs wait", "ecs wait", "ecs describe-tasks"])
_r = _rti_run(f"{_TASK}\\t0\\tNone\\tNone\\tNone", "STOPPED\\t0\\tx\\ty", wait_fails=2)
check(f"run_temporal_init: tasks-stopped が 2 回とも時間切れなら describe-tasks を見ずに die（いま: rc={_r[0]} {_r[1]} {_r[4].strip()[-60:]!r}）",
      _r[0] != 0 and "DONE" not in _r[3] and _r[1] == ["ecs list-tasks", "ecs run-task", "ecs wait", "ecs wait"] and "20 分たっても止まらない" in _r[4])
# 前の init（打ち直し、Ctrl-C の後）がまだ走っていれば、止まるのを待ってから起こす（2 つ同時だと新しい DB のスキーマが 0.0 に戻る。Round 1 のセルフレビューの Should 5）
_PREV1, _PREV2 = "arn:aws:ecs:r:1:task/c1/old1", "arn:aws:ecs:r:1:task/c1/old2"
_r = _rti_run(f"{_TASK}\\t0\\tNone\\tNone\\tNone", "STOPPED\\t0\\tx\\ty", prev_out=f"{_PREV1}\\t{_PREV2}")
check(f"run_temporal_init: 前の init が 2 つ走っていれば両方を別々の引数で tasks-stopped に渡して待ってから run-task する（いま: rc={_r[0]} {_r[1]} {_r[2][1:2]} 引数の数 {_r[5]}）",
      _r[0] == 0 and "DONE" in _r[3] and _r[5][:1] == [10] and _r[1] == ["ecs list-tasks", "ecs wait", "ecs run-task", "ecs wait", "ecs describe-tasks"]
      and _r[2][1].endswith(f"--cluster c1 --tasks {_PREV1} {_PREV2}") and f"前の初期化のタスク（p-workflow-init）がまだ走っているので、止まるのを待つ: {_PREV1} {_PREV2}" in _r[3])
_r = _rti_run(f"{_TASK}\\t0\\tNone\\tNone\\tNone", "STOPPED\\t0\\tx\\ty", prev_out="None")
check(f"run_temporal_init: list-tasks が None（text の空）なら前の init を待たない（いま: {_r[1]}）",
      _r[0] == 0 and _r[1] == ["ecs list-tasks", "ecs run-task", "ecs wait", "ecs describe-tasks"])
_r = _rti_run(f"{_TASK}\\t0\\tNone\\tNone\\tNone", "STOPPED\\t0\\tx\\ty", prev_out=_PREV1, wait_fails=1)
check(f"run_temporal_init: 前の init の待ちが 1 回目で時間切れでも 2 回目で止まれば run-task する（いま: rc={_r[0]} {_r[1]}）",
      _r[0] == 0 and _r[1] == ["ecs list-tasks", "ecs wait", "ecs wait", "ecs run-task", "ecs wait", "ecs describe-tasks"])
_r = _rti_run(f"{_TASK}\\t0\\tNone\\tNone\\tNone", "STOPPED\\t0\\tx\\ty", prev_out=_PREV1, wait_fails=2)
check(f"run_temporal_init: 前の init が 2 回待っても止まらなければ run-task せずに die（いま: rc={_r[0]} {_r[1]} {_r[4].strip()[-60:]!r}）",
      _r[0] != 0 and "DONE" not in _r[3] and _r[1] == ["ecs list-tasks", "ecs wait", "ecs wait"]
      and "DIE: 前の Temporal の初期化のタスク（p-workflow-init）が 20 分たっても止まらない" in _r[4])
_r = _rti_run(f"{_TASK}\\t0\\tNone\\tNone\\tNone", "", list_rc=255)
check(f"run_temporal_init: list-tasks そのものが失敗すれば run-task せずに die（いま: rc={_r[0]} {_r[1]} {_r[4].strip()[-60:]!r}）",
      _r[0] != 0 and _r[1] == ["ecs list-tasks"] and "DIE: 走っている Temporal の初期化のタスク（p-workflow-init）を確かめられない" in _r[4])
check("ensure_temporal_secrets は /<prefix>/temporal/db-password を乱数の SecureString で作る（値は出さない）",
      'ensure_secret "/$PREFIX/temporal/db-password" password' in read("ops", "up-common.sh"))
check("up.sh は workflow を apply して services-stable を待ち、Temporal UI のポートフォワーディングを案内する",
      'tf_apply workflow -var "worker_image_tag=$IMAGE_TAG"' in up and 'aws ecs wait services-stable' in up and 'AWS-StartPortForwardingSessionToRemoteHost' in up)
check("down.sh は workflow を最初に消す（必須変数 worker_image_tag と temporal_image_tag はダミーで渡す）",
      down.index('destroy_lambda_root workflow') < down.index('destroy_root pipeline/analytics') and 'worker_image_tag=${IMAGE_TAG:-destroy}' in down
      and '-var "temporal_image_tag=destroy"' in down)
# .py を名指しで並べると、ファイルを足したときに構文検査から漏れる（分割で 7 本増えた）。find に任せているかを見る
check("check.sh は workflow ルートとこのテストを見て、.py は名指しせず find で全部見る",
      "workflow)" in chk and "for t in tests/test_*.py; do" in chk
      and re.search(r"find [\w /]*\bapp\b [^\n]*-name '\*\.py'", chk) is not None and "ast.parse(" in chk)
check("deploy.env.example は AGENT=0 / PIPELINE=0 / WORKFLOW=0 を既定にし（2026-10-04 に AGENT の既定を 0 にした）、CREATE_KB を説明する（古い PHASE の行は載せない）",
      re.search(r"^AGENT=0\n^PIPELINE=0\n^WORKFLOW=0$", read("deploy.env.example"), re.M) is not None
      and "既定は AGENT=1" not in read("deploy.env.example") and "AGENT=1 だけ" not in read("deploy.env.example")
      and re.search(r"^#CREATE_KB=0$", read("deploy.env.example"), re.M) is not None and re.search(r"^#?\s*PHASE=", read("deploy.env.example"), re.M) is None
      and "workflow" in read("deploy.env.example"))
check("down.sh は VPC の Lambda を持つルート（workflow / graph）を消す間、その関数の available な ENI だけを裏で消す（2026-09-18）",
      'destroy_lambda_root workflow "$PREFIX-tools"' in down and 'destroy_lambda_root pipeline/graph "$PREFIX-graph-status"' in down
      and 'Values=AWS Lambda VPC ENI-$1-*" Name=status,Values=available' in down and "kill -0 $$" in down)
_envsh = read("ops", "deploy-env.sh")
check("terraform の出力は tf_logged で絞り、全文を ops/logs に残す。TF_VERBOSE=1 で全部出す。up.sh と down.sh の両方が通す",
      "tf_logged()" in _envsh and 'tee "$logf"' in _envsh and 'if [ -n "$TF_VERBOSE" ]' in _envsh
      and 'tf_logged "$root" apply' in up and 'tf_logged "$root" destroy' in down
      and re.search(r"^set -e?uo pipefail", down, re.M) is not None)
# TF_VERBOSE=1 のときログを残さないと、down.sh が DependencyViolation と掴んでいる SG を読めず打ち直しが効かない
check("tf_logged は TF_VERBOSE=1 の枝でも全文を ops/logs に残す", _envsh[_envsh.index("tf_logged() {"):].count('tee "$logf"') == 2)
# tf_logged は空かどうかだけを見るので、deploy.env の TF_VERBOSE=0 をそろえずに渡すと全部出してしまう（2026-10-04 に直した）
def _tfv(text, **env):
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as f:
        f.write(text)
    e = {k: v for k, v in os.environ.items() if k not in ("TF_VERBOSE", "OWNER")}
    e.update(env, DEPLOY_ENV_FILE=f.name)
    r = subprocess.run(["bash", "-c", 'die() { echo "DIE: $*"; exit 1; }; . ops/deploy-env.sh; load_deploy_env >/dev/null; flag_value TF_VERBOSE; '
                        'if [ -n "$TF_VERBOSE" ]; then echo 全部; else echo 絞る; fi'], cwd=ROOT, env=e, capture_output=True, text=True)
    os.unlink(f.name)
    return r.stdout.strip()
check("up.sh と down.sh は deploy.env を読んだ直後に TF_VERBOSE を 1 / 空にそろえる。0 / false / no と書かないときは絞り、1 / true / yes で全部出す。それ以外は止まる",
      all(sh.count("\nflag_value TF_VERBOSE\n") == 1 and sh.index("\nresolve_name_prefix  #") < sh.index("\nflag_value TF_VERBOSE\n") < sh.index('\nlog "1. ')
          for sh in (up, down))
      and [_tfv(f"OWNER=a\nTF_VERBOSE={v}\n") for v in ("0", "false", "no", "", "1", "true", "yes")] == ["絞る"] * 4 + ["全部"] * 3
      and _tfv("OWNER=a\n") == "絞る" and _tfv("OWNER=a\nTF_VERBOSE=1\n", TF_VERBOSE="0") == "絞る"
      and _tfv("OWNER=a\nTF_VERBOSE=2\n") == "DIE: TF_VERBOSE は 1 か 0（いまは「2」）")
check("down.sh は 1 ルートが消えなくても止まらず、残りを消してから最後にまとめて出す（止まると後ろの EC2 が動いたまま残る）",
      "FAILED_ROOTS=" in down and 'FAILED_ROOTS="$FAILED_ROOTS $root"' in down
      and re.search(r'if \[ -n "\$FAILED_ROOTS" \]; then[\s\S]*?exit 1', down) is not None
      and re.search(r'destroy_root\(\)[\s\S]*?\n\}', down).group(0).count("die ") == 0)
# ---- IaC/terraform/aws-managed/agent と IaC/terraform/aws-managed/base/core の分担
agent_files = set(n for n in os.listdir(os.path.join(ROOT, "IaC", "terraform", "aws-managed", "agent")) if n.endswith(".tf"))
check("IaC/terraform/aws-managed/agent のファイルは versions / providers / variables / locals / runtime / kb / outputs（network.tf は 2026-09-26 に無くなった）",
      agent_files == {"versions.tf", "providers.tf", "variables.tf", "locals.tf", "runtime.tf", "kb.tf", "outputs.tf"})
agent_tf = "".join(read("IaC", "terraform", "aws-managed", "agent", n) for n in sorted(agent_files))
main_tf = "".join(read("IaC", "terraform", "aws-managed", "base", "core", n) for n in sorted(os.listdir(os.path.join(ROOT, "IaC", "terraform", "aws-managed", "base", "core"))) if n.endswith(".tf"))
check("Runtime / ガードレール / KB は IaC/terraform/aws-managed/agent にあり、IaC/terraform/aws-managed/base/core には無い。agent にエンドポイントも SG も無く、Runtime は土台の runtime の SG を使う",
      all(r in agent_tf for r in ('resource "aws_bedrockagentcore_agent_runtime" "agent"', 'resource "aws_bedrock_guardrail" "this"', 'resource "aws_bedrockagent_knowledge_base" "kb"'))
      and 'resource "aws_vpc_endpoint"' not in agent_tf and 'resource "aws_security_group"' not in agent_tf
      and re.search(r'runtime_sg_id\s*=\s*try\(data\.terraform_remote_state\.main\.outputs\.security_group_ids\["runtime"\], ""\)', agent_tf) is not None
      and "aws_vpc_security_group_" not in agent_tf
      and not any(r in main_tf for r in ("aws_bedrockagentcore_agent_runtime", "aws_bedrock_guardrail", "aws_bedrockagent_knowledge_base", "aws_opensearchserverless_collection")))
check("KB のコレクションは公開せず、土台の VPC エンドポイントと Bedrock のサービスからだけ。人の ARN はデータアクセスポリシーに入らない（2026-09-28）",
      re.search(r'"kb_network"[\s\S]*?AllowFromPublic\s*=\s*false[\s\S]*?SourceVPCEs\s*=\s*\[local\.aoss_vpce_id\][\s\S]*?SourceServices\s*=\s*\["bedrock\.amazonaws\.com"\]', agent_tf, re.S) is not None
      and re.search(r'aoss_vpce_id\s*=\s*try\(data\.terraform_remote_state\.main\.outputs\.opensearch_vpc_endpoint_id,\s*""\)', agent_tf) is not None
      and "kb_admin_principal_arn" not in agent_tf and "aws_iam_session_context" not in agent_tf
      and "opensearch-project/opensearch" not in agent_tf and 'resource "opensearch_index"' not in agent_tf)
check("KB のベクトルインデックスは VPC の中の Lambda（app/agentcore/kb_index.py）が作り、KB はその後に作る。Lambda は CreateIndex / DescribeIndex だけ",
      re.search(r'resource "aws_lambda_function" "kb_index"[\s\S]*?vpc_config\s*\{[\s\S]*?security_group_ids\s*=\s*\[local\.lambda_sg_id\]', agent_tf, re.S) is not None
      and re.search(r'lambda_sg_id\s*=\s*try\(data\.terraform_remote_state\.main\.outputs\.security_group_ids\["lambda"\], ""\)', agent_tf) is not None
      and "app/agentcore/kb_index.py" in agent_tf and 'resource "aws_lambda_invocation" "kb_index"' in agent_tf
      and re.search(r'"aoss:CreateIndex",\s*"aoss:DescribeIndex"\][\s\S]*?Principal\s*=\s*\[aws_iam_role\.kb_index\[0\]\.arn\]', agent_tf, re.S) is not None
      and re.search(r'resource "aws_bedrockagent_knowledge_base" "kb"[\s\S]*?depends_on\s*=\s*\[aws_lambda_invocation\.kb_index', agent_tf, re.S) is not None)
check("up.sh は KB か logs のコレクションがあるときだけ base/core に create_opensearch_endpoint=true を渡す（費用 +3 セント）",
      "MAIN_VARS+=(-var create_opensearch_endpoint=true)" in up and up.index("NEED_AOSS=") < up.index("tf_apply base/core")
      and "ADMIN_ARN=" not in up and "opensearch_cacert_file" not in up and "opensearch_cacert_file" not in down)
check("KB は create_knowledge_base（既定 false）の count で作り、Runtime は KB があるときだけ KNOWLEDGE_BASE_ID を受ける",
      re.search(r'variable "create_knowledge_base"[\s\S]*?default\s*=\s*false', agent_tf) is not None and 'resource "aws_bedrockagent_knowledge_base" "kb" {\n  count = local.kb ? 1 : 0' in agent_tf
      and re.search(r'local\.kb \? \{\n\s*KNOWLEDGE_BASE_ID', agent_tf) is not None)
check("Runtime の ARN は agent が SSM に書き、web はそれを読む（main は runtime_arn を user_data に渡さない）",
      'resource "aws_ssm_parameter" "runtime_arn"' in agent_tf and 'name        = "${local.param_prefix}/runtime-arn"' in agent_tf
      and "runtime_arn" not in read("IaC", "terraform", "aws-managed", "base", "core", "templates", "web_user_data.sh.tftpl")
      and 'toolkit.Param("RUNTIME_ARN", "runtime-arn")' in read("app", "dashboard", "chat.py")
      and 'ssm:GetParameter' in main_tf)
check("agent は web のロールに InvokeAgentRuntime を付け、main の runtime ロールにポリシーを足す",
      'role = local.web_role_name' in agent_tf and 'bedrock-agentcore:InvokeAgentRuntime' in agent_tf and 'role = local.runtime_role_name' in agent_tf
      and 'output "runtime_role_arn"' in main_out and 'resource "aws_iam_role" "runtime"' in main_tf)
check("閉域: Runtime のリソースポリシーは VPC の外からの InvokeAgentRuntime を拒み、apply した人は外す（deploy.md の CLI の確認が通る）",
      re.search(r'resource "aws_bedrockagentcore_resource_policy" "runtime"[\s\S]*?"bedrock-agentcore:InvokeAgentRuntime"[\s\S]*?agent_runtime_arn[\s\S]*?"aws:SourceVpc"[\s\S]*?local\.perimeter_exempt_principals', agent_tf) is not None)
_agent_roles = set(re.findall(r'resource "aws_iam_role" "(\w+)"', agent_tf))
_agent_perim = set(re.findall(r'resource "aws_iam_role_policy_attachment" "\w+" \{\n\s*count = local\.kb && local\.perimeter_policy_arn != "" \? 1 : 0\n\s*'
                              r'role\s*= aws_iam_role\.(\w+)\[0\]\.name\n\s*policy_arn = local\.perimeter_policy_arn\n\}', agent_tf))
check("閉域: agent のロールは、サービス側で動く KB のロール（<prefix>-kb。perimeter_exempt_principals）を除いて全部 perimeter の Deny を付ける（kb-index の Lambda も）",
      _agent_roles == {"kb", "kb_index"} and _agent_perim == _agent_roles - {"kb"}
      and 'perimeter_policy_arn        = try(data.terraform_remote_state.main.outputs.network_perimeter_policy_arn, "")' in agent_tf
      and '"arn:${local.partition}:iam::${local.account_id}:role/${local.name_prefix}-kb",' in main_tf)
check("down.sh は Runtime の ENI が残るあいだ VPC・サブネット・runtime の SG を残して他を消す（aws_security_group.internal は 2026-09-29 より前の state）",
      "InterfaceType=='agentic_ai'" in down and "Name=tag:Name,Values=$PREFIX-vpc" in down and "tf base/core output -raw vpc_id" not in down and "Runtime の ENI の確認:" in down
      and '''""|data.*|aws_vpc.this|aws_subnet.*|'aws_security_group.workload["runtime"]'|aws_security_group.internal) ;;''' in down
      and "aws_security_group.runtime" not in down
      and down.index("InterfaceType=='agentic_ai'") < down.index("destroy_root base/core") < down.index("destroy_root base/ecr"))
check("up.sh は base/core の state に 2026-09-29 より前の SG（aws_security_group.internal）があれば、ECR より前に止めて先に down.sh を打たせる",
      re.search(r"grep -qx 'aws_security_group\\\.internal'", up) is not None
      and up.index("aws_security_group\\.internal") < up.index('log "1. ECR リポジトリ') and "先に ops/down.sh で消す" in up)
# ---- description を変えた SG の守り（cycle 043）: state にある workload の SG 全部の description をコードと比べ、違えば up.sh を止めて down.sh の全消しだけを案内する
_up_sh = read("ops", "up.sh")
_csd_calls = [m.start() for m in re.finditer(r"^\s*check_sg_descriptions\s*$", _up_sh, re.M)]
check(f"up.sh は check_sg_descriptions を tf_init base/core の後、log \"1. ECR リポジトリ\" の前に 1 回呼ぶ（cycle 043。いま: {len(_csd_calls)} 回）",
      len(_csd_calls) == 1 and 0 <= _up_sh.find("tf_init base/core") < _csd_calls[0] < _up_sh.find('log "1. ECR リポジトリ'))
_upc_fn = {n: (_upc.split(f"\n{n}() {{")[1].split("\n}\n")[0] if f"\n{n}() {{" in _upc else "")
           for n in ("sg_descriptions_in_code", "sg_descriptions_in_state", "check_sg_descriptions")}
_csd_body = "\n".join(l for l in _upc_fn["check_sg_descriptions"].splitlines() if not l.lstrip().startswith("#"))  # コメントの行（関数の見出しの行の説明を含む）を除く
_sdr_name = "SG_DESCRIPTION_" + "ROOTS"  # Round 1 のキーの表の名前（grep で 0 件にするため、このファイルにも綴りを置かない）
check(f"ops/up-common.sh に {_sdr_name}（Round 1 のキーの表）が無く、sg_descriptions_in_code / sg_descriptions_in_state / check_sg_descriptions の 3 関数があり、"
      "check_sg_descriptions の本文は tf_init も state list も打たない（ルートの state を見ない。cycle 043 Round 2）",
      _sdr_name not in _upc and all(_upc_fn.values()) and _csd_body.strip()
      and "tf_init" not in _csd_body and "state list" not in _csd_body and "tf base/core show -no-color" in _csd_body)
_sg_tf_core = read("IaC", "terraform", "aws-managed", "base", "core", "security_groups.tf")
_oss_tf_core = read("IaC", "terraform", "aws-managed", "base", "core", "oss.tf")
_SG_CODE_DESC = dict(re.findall(r'^    (\w+)\s+= "([^"]*)"$', _sg_tf_core.split("\n  security_groups = {\n")[1].split("\n  }\n")[0], re.M))
_SG_OSS_DESC = dict(re.findall(r'^    (\w+)\s+= "([^"]*)"$', _oss_tf_core.split("\n  oss_security_groups = {\n")[1].split("\n  }\n")[0], re.M))
_SG_CODE_DESC_OSS = {**{k: v for k, v in _SG_CODE_DESC.items() if k != "msk"}, **_SG_OSS_DESC}  # OSS の state（msk は oss_replaced で無い）
def _csd_show(sgs):
    """terraform show -no-color（state 全体）の形のサンプル。ルール → aws_vpc → SG（sgs: キー → description。None なら description の行が無い）→ ルールの順"""
    blocks = ['# aws_vpc_security_group_ingress_rule.flow["nautobot-neo4j-tcp-7687"]:\nresource "aws_vpc_security_group_ingress_rule" "flow" {\n'
              '    arn                          = "arn:aws:ec2:ap-northeast-1:1:security-group-rule/sgr-1"\n'
              '    description                  = "Neo4j Bolt - Nautobot sync"\n    from_port                    = 7687\n    id                           = "sgr-1"\n'
              '    ip_protocol                  = "tcp"\n    referenced_security_group_id = "sg-2"\n    security_group_id            = "sg-1"\n}\n',
              '# aws_vpc.this:\nresource "aws_vpc" "this" {\n    arn                                  = "arn:aws:ec2:ap-northeast-1:1:vpc/vpc-1"\n'
              '    cidr_block                           = "10.0.0.0/16"\n    id                                   = "vpc-1"\n'
              '    tags                                 = {\n        "Name" = "p-vpc"\n    }\n}\n']
    for k, v in sgs.items():
        blocks.append(f'# aws_security_group.workload["{k}"]:\nresource "aws_security_group" "workload" {{\n'
                      f'    arn                    = "arn:aws:ec2:ap-northeast-1:1:security-group/sg-{k}"\n'
                      + (f'    description            = "{v}"\n    egress                 = []\n' if v is not None
                         # description の行が無い SG は、入れ子（12 空白）の description を持たせる（4 空白ちょうどだけを読むか）
                         else '    egress                 = [\n        {\n            description      = "inline rule"\n            from_port        = 0\n        },\n    ]\n')
                      + f'    id                     = "sg-{k}"\n    ingress                = []\n'
                      f'    name                   = "p-{k.replace("_", "-")}"\n    tags                   = {{\n        "Name" = "p-{k.replace("_", "-")}"\n    }}\n}}\n')
    blocks.append('# aws_vpc_security_group_egress_rule.flow["web-workflow-tcp-8233"]:\nresource "aws_vpc_security_group_egress_rule" "flow" {\n'
                  '    description                  = "Temporal UI through SSM port forwarding"\n    id                           = "sgr-2"\n}\n')
    return "\n".join(blocks)
def _csd_run(state=None, oss=False, show_fail=False, code_broken=False, code_tail_comment=False):
    """check_sg_descriptions を bash で、tf / tf_init / die（関数）を差し替えて動かす。TF_DIR は一時ディレクトリ（base/core の security_groups.tf と oss.tf は実物のコピー。
    oss なら oss.auto.tfvars に project = "nwc-oss"）。state: terraform show に載せる workload の SG（キー → description。既定はコードと同じ全キー）。
    code_broken: コピーの security_groups.tf の「  security_groups = {」の行を崩す（コードの文言が 1 つも読めない）。
    code_tail_comment: コピーの security_groups.tf の workflow の行の末尾に「 # 041」を足す（その 1 行だけ読めない）。
    rc と stdout / stderr と tf / tf_init の呼び出しを返す。"""
    import shutil, subprocess, tempfile
    if state is None:
        state = dict(_SG_CODE_DESC_OSS if oss else _SG_CODE_DESC)
    with tempfile.TemporaryDirectory() as d:
        core = os.path.join(d, "tf", "base", "core")
        os.makedirs(core)
        for fn in ("security_groups.tf", "oss.tf"):
            shutil.copy(os.path.join(ROOT, "IaC", "terraform", "aws-managed", "base", "core", fn), core)
        if code_broken:
            with open(os.path.join(core, "security_groups.tf")) as f:
                _t = f.read()
            with open(os.path.join(core, "security_groups.tf"), "w") as f:
                f.write(_t.replace("\n  security_groups = {\n", "\n  security_groups = merge({\n"))
        if code_tail_comment:
            with open(os.path.join(core, "security_groups.tf")) as f:
                _t = f.read()
            _wl = f'    workflow             = "{_SG_CODE_DESC["workflow"]}"\n'
            assert _t.count(_wl) == 1, "security_groups.tf の workflow の行の形が変わった（テストの土台を直す）"
            with open(os.path.join(core, "security_groups.tf"), "w") as f:
                f.write(_t.replace(_wl, _wl[:-1] + " # 041\n"))
        if oss:
            with open(os.path.join(core, "oss.auto.tfvars"), "w") as f:
                f.write('# OSS 版のしるし\nproject = "nwc-oss"\n')
        with open(os.path.join(d, "show"), "w") as f:
            f.write(_csd_show(state))
        script = ("set -euo pipefail\n"
                  f"D={d}\nTF_DIR={d}/tf\nOPS_DIR=ops\nSHOW_FAIL={'1' if show_fail else ''}\n"
                  'die() { echo "DIE: $*" >&2; exit 1; }\n'
                  'tf_init() { echo "init $1" >> "$D/calls"; }\n'
                  'tf() { echo "tf $*" >> "$D/calls"\n'
                  '  if [ "$*" = "base/core show -no-color" ]; then [ -z "$SHOW_FAIL" ] || { echo "Error: fake show failure" >&2; return 1; }; cat "$D/show"; return 0; fi\n'
                  '  return 9; }\n'
                  + "".join(f"{n}() {{{b}\n}}\n" for n, b in _upc_fn.items())
                  + "check_sg_descriptions\necho DONE\n")
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, stdin=_vsp.DEVNULL)
        calls = open(os.path.join(d, "calls")).read().splitlines() if os.path.exists(os.path.join(d, "calls")) else []
    return out.returncode, out.stdout, out.stderr, calls
def _csd_no_root(calls):  # ルートの state を見ていない（init も state list も打たない）
    return not any(c.startswith("init ") or " state list" in c for c in calls)
_WF_OLD = "Temporal dev server and worker ECS task (IaC/terraform/aws-managed/workflow)"
_NLB_OLD = "Internal NLB in front of the Telegraf dial-out task (IaC/terraform/aws-managed/pipeline/stream)"
_r = _csd_run()
check(f"check_sg_descriptions: state の description が全キーともコードと同じなら通り、terraform show を 1 回だけ打ち、init も state list も打たない（いま: rc={_r[0]} {_r[2].strip()!r} {_r[3]}）",
      _r[0] == 0 and "DONE" in _r[1] and "DIE:" not in _r[2] and _r[3] == ["tf base/core show -no-color"])
_r = _csd_run(state={**_SG_CODE_DESC, "workflow": _WF_OLD})
check(f"check_sg_descriptions: workflow の description が古い（041 より前）なら、state とコードの文言を出して die し、down.sh の全消しだけを案内する"
      f"（ルートだけ消す案内は無い。init も state list も打たない。いま: rc={_r[0]} {_r[2].strip()!r} {_r[3]}）",
      _r[0] == 1 and "DONE" not in _r[1] and "DIE:" in _r[2] and "workflow（state「Temporal dev server and worker ECS task" in _r[2]
      and "コード「Temporal server, UI and worker" in _r[2] and "先に ops/down.sh で全部消してから ops/up.sh" in _r[2]
      and "だけ先に消してもよい" not in _r[2] and _csd_no_root(_r[3]))
_r = _csd_run(state={**_SG_CODE_DESC, "workflow": _WF_OLD, "telegraf_dialout_nlb": _NLB_OLD})
check(f"check_sg_descriptions: workflow と telegraf_dialout_nlb の 2 つが古ければ、die の文に両方を出す。runtime は違わないので ENI の一言は無い（いま: rc={_r[0]} {_r[2].strip()!r}）",
      _r[0] == 1 and "DIE:" in _r[2] and "workflow（" in _r[2] and "telegraf_dialout_nlb（state「Internal NLB in front of the Telegraf dial-out task" in _r[2]
      and "ENI が消えてから" not in _r[2] and "Runtime の ENI" not in _r[2])
_r = _csd_run(state={**_SG_CODE_DESC, "runtime": "AgentCore Runtime ENIs (terraform/agent)"})
_eni = "runtime は Runtime の ENI（agentic_ai。最長 8 時間ほど残る）があるあいだ ops/down.sh も残すので、ENI が消えてから ops/down.sh を打つ。"
check(f"check_sg_descriptions: runtime の description が古い（2026-10-08 より前の OSS 版の state の実物）なら、down.sh も Runtime の ENI があるあいだ runtime の SG を残すので、"
      f"「ENI が消えてから down.sh」の一言を「まだ何も作っていない」の前に足して die する（cycle 043 Round 3。いま: rc={_r[0]} {_r[2].strip()!r}）",
      _r[0] == 1 and "DONE" not in _r[1] and "DIE:" in _r[2] and "runtime（state「AgentCore Runtime ENIs (terraform/agent)」" in _r[2]
      and f"先に ops/down.sh で全部消してから ops/up.sh。{_eni}まだ何も作っていない" in _r[2] and _r[2].count("ENI が消えてから") == 1)
_r = _csd_run(state={**_SG_CODE_DESC, "workflow": _WF_OLD, "runtime": "AgentCore Runtime ENIs (terraform/agent)"})
check(f"check_sg_descriptions: runtime がほかのキーと一緒に違っても（先頭でなくても）ENI の一言を 1 回だけ足す（いま: rc={_r[0]} {_r[2].strip()!r}）",
      _r[0] == 1 and "workflow（" in _r[2] and "、runtime（" in _r[2] and _r[2].count(_eni) == 1)
_r = _csd_run(state={**_SG_CODE_DESC, "web": "Web EC2 runtime（x)"})
check(f"check_sg_descriptions: runtime 以外のキーの文言に runtime が入っていても ENI の一言は足さない（キーで判定する。いま: rc={_r[0]} {_r[2].strip()!r}）",
      _r[0] == 1 and "web（" in _r[2] and "ENI が消えてから" not in _r[2])
_r = _csd_run(state={**_SG_CODE_DESC, "workflow": None})
check(f"check_sg_descriptions: state の SG のブロックに description の行が無ければ（空）、違うものとして die する（いま: rc={_r[0]} {_r[2].strip()!r}）",
      _r[0] == 1 and "DIE:" in _r[2] and "workflow（state「」/ コード「Temporal server, UI and worker" in _r[2])
_r = _csd_run(state={**_SG_CODE_DESC, "telegraf": "Telegraf ECS task - traps, syslog and MDT behind the NLB (IaC/terraform/aws-managed/pipeline/stream)"})
check(f"check_sg_descriptions: コードに無いキー（キーを変えた古い telegraf。既存の守りの担当）は古い文言でも飛ばして通す（いま: rc={_r[0]} {_r[2].strip()!r}）",
      _r[0] == 0 and "DONE" in _r[1] and "DIE:" not in _r[2])
_r = _csd_run(state={})
check(f"check_sg_descriptions: state に workload の SG が 1 つも無い（aws_vpc とルールのブロックだけ）なら通す（いま: rc={_r[0]} {_r[2].strip()!r}）",
      _r[0] == 0 and "DONE" in _r[1] and "DIE:" not in _r[2])
_r = _csd_run(show_fail=True)
check(f"check_sg_descriptions: terraform show が失敗したら黙って通さず die する（いま: rc={_r[0]} {_r[2].strip()!r}）",
      _r[0] == 1 and "DONE" not in _r[1] and "DIE:" in _r[2] and "base/core の state が読めない（terraform show の失敗" in _r[2])
_r = _csd_run(state={**_SG_CODE_DESC, "workflow": _WF_OLD}, code_broken=True)
check(f"check_sg_descriptions: security_groups.tf から文言が 1 つも読めない（local.security_groups の形が変わった）なら、全キーを飛ばして素通りせず die する"
      f"（terraform show も打たない。いま: rc={_r[0]} {_r[2].strip()!r} {_r[3]}）",
      _r[0] == 1 and "DONE" not in _r[1] and "DIE:" in _r[2] and "security_groups.tf から SG の description が読めない" in _r[2] and _r[3] == [])
_r = _csd_run(state={**_SG_CODE_DESC, "workflow": _WF_OLD}, code_tail_comment=True)
check(f"check_sg_descriptions: security_groups.tf の 1 行だけが読めない（行末のコメントなど）なら、そのキーを黙って飛ばさず、読めない行を出して die する"
      f"（terraform show も打たない。いま: rc={_r[0]} {_r[2].strip()!r} {_r[3]}）",
      _r[0] == 1 and "DONE" not in _r[1] and "読めない行:" in _r[2] and 'workflow             = "Temporal server' in _r[2]
      and "security_groups.tf から SG の description が読めない" in _r[2] and _r[3] == [])
_r = _csd_run(oss=True)
_r2 = _csd_run(state={**_SG_CODE_DESC_OSS, "spark": _SG_CODE_DESC["spark"]}, oss=True)
_r3 = _csd_run(state={**_SG_CODE_DESC, "spark": _SG_CODE_DESC["spark"]})
check(f"check_sg_descriptions: OSS 版（oss.auto.tfvars が project = \"nwc-oss\"）は oss.tf の文言で比べる。spark が oss.tf の文言なら通り、"
      f"security_groups.tf の EMR Serverless の文言なら die する。マネージド版は EMR Serverless の文言で通る（いま: rc={_r[0]} / {_r2[0]} / {_r3[0]} {_r[2].strip()!r} {_r2[2].strip()!r}）",
      _r[0] == 0 and "DIE:" not in _r[2] and _SG_CODE_DESC_OSS["spark"].startswith("Spark ECS task, local mode")
      and _r2[0] == 1 and "spark（state「EMR Serverless workers" in _r2[2]
      and _r3[0] == 0 and "DIE:" not in _r3[2] and _SG_CODE_DESC["spark"].startswith("EMR Serverless workers"))
import subprocess as _sp043  # noqa: E402
_sds_out = _sp043.run(["bash", "-c", f"sg_descriptions_in_state() {{{_upc_fn['sg_descriptions_in_state']}\n}}\nsg_descriptions_in_state"],
                      input=_csd_show({"web": "W (x) = y", "workflow": None, "lab": "L"}), capture_output=True, text=True, timeout=30).stdout
check(f"sg_descriptions_in_state は SG のブロックの 4 空白の description だけを <キー>\\t<文言> で出し、前後のルールと VPC のブロックの description を拾わない"
      f"（description の行が無い SG は空。いま: {_sds_out!r}）",
      _sds_out == "web\tW (x) = y\nworkflow\t\nlab\tL\n")
_sds_tail = _sp043.run(["bash", "-c", f"sg_descriptions_in_state() {{{_upc_fn['sg_descriptions_in_state']}\n}}\nsg_descriptions_in_state"],
                       input='# aws_security_group.workload["workflow"]:\nresource "aws_security_group" "workload" {\n    egress = []\n}\n\n'
                             'Outputs:\n\nmeta = {\n    description = "from an output"\n}\n', capture_output=True, text=True, timeout=30).stdout
check(f"sg_descriptions_in_state は SG のブロックを行頭の }} で閉じ、後ろの Outputs: の 4 空白の description を拾わない（いま: {_sds_tail!r}）",
      _sds_tail == "workflow\t\n")
def _sg_block_lines(tf, head):  # コードの塊のうち、空行とコメント以外の行の数（テストの正規表現が 1 行を黙って落としていないか）
    return len([l for l in tf.split(f"\n  {head} = {{\n")[1].split("\n  }\n")[0].splitlines() if l.strip() and not l.strip().startswith("#")])
check(f"local.security_groups / oss_security_groups の塊の行（空行とコメント以外）は全部 <キー> = \"<文言>\" の形で、テストの正規表現が全部拾う"
      f"（いま: {_sg_block_lines(_sg_tf_core, 'security_groups')} 行 / {len(_SG_CODE_DESC)} 件、{_sg_block_lines(_oss_tf_core, 'oss_security_groups')} 行 / {len(_SG_OSS_DESC)} 件）",
      _sg_block_lines(_sg_tf_core, "security_groups") == len(_SG_CODE_DESC) and _sg_block_lines(_oss_tf_core, "oss_security_groups") == len(_SG_OSS_DESC))
def _sdc_real(tf_dir):
    out = _sp043.run(["bash", "-c", f"TF_DIR={tf_dir}\nsg_descriptions_in_code() {{{_upc_fn['sg_descriptions_in_code']}\n}}\nsg_descriptions_in_code"],
                     cwd=ROOT, capture_output=True, text=True, timeout=30, stdin=_vsp.DEVNULL).stdout
    return [tuple(l.split("\t", 1)) for l in out.splitlines()]
_sdc_m, _sdc_o = _sdc_real("IaC/terraform/aws-managed"), _sdc_real("IaC/terraform/oss")
check(f"sg_descriptions_in_code は実物の security_groups.tf の local.security_groups のキーと文言を全部出し、Python の正規表現で取ったものと一致する（キーの重複なし。いま: {len(_sdc_m)} 件）",
      dict(_sdc_m) == _SG_CODE_DESC and len(_sdc_m) == len(_SG_CODE_DESC)
      and {"workflow", "telegraf_dialout", "telegraf_dialout_nlb", "spark", "msk"} <= set(_SG_CODE_DESC))
check(f"sg_descriptions_in_code は OSS 版（IaC/terraform/oss。oss.auto.tfvars が nwc-oss）では oss.tf の local.oss_security_groups で上書きし、キーを足す"
      f"（spark は Spark ECS task、kafka がある。いま: {len(_sdc_o)} 件 spark={dict(_sdc_o).get('spark')!r}）",
      dict(_sdc_o) == {**_SG_CODE_DESC, **_SG_OSS_DESC} and len(_sdc_o) == len({**_SG_CODE_DESC, **_SG_OSS_DESC})
      and dict(_sdc_o)["spark"] == _SG_OSS_DESC["spark"] and "kafka" in dict(_sdc_o))
_ts_csd = [l for l in read("docs", "troubleshooting.md").splitlines() if "state の SG の description がコードと違う" in l]
check(f"troubleshooting.md の check_sg_descriptions の行は down.sh の全消しを案内し、runtime のときは ENI が消えてから、2026-10-08（48683dd）より前の state はパスを含む description（web / lab / lambda 以外）が違うと書く。"
      f"ルートだけ destroy する案内は無い（cycle 043 Round 3。いま: {len(_ts_csd)} 行）",
      len(_ts_csd) == 1 and "check_sg_descriptions" in _ts_csd[0] and "`ops/down.sh`（OSS 版は `ops/oss/down.sh`）で全部消してから `ops/up.sh`" in _ts_csd[0]
      and "ENI が消えてから `ops/down.sh`" in _ts_csd[0] and "2026-10-08（`48683dd`）より前の state" in _ts_csd[0] and "web / lab / lambda 以外の全部）が違う" in _ts_csd[0] and "全キーが違う" not in _ts_csd[0]
      and "worker_image_tag=destroy" not in read("docs", "troubleshooting.md") and "だけ先に消してもよい" not in _ts_csd[0])
_sg_roots = ("agent", "pipeline/analytics", "pipeline/graph", "pipeline/lab", "pipeline/stream", "workflow")
# stream の MSK の SG は msk.tf で読む（MSK だけのもの。OSS 版のルートに msk.tf は無い。cycle 005）
_sg_files = {r: ("locals.tf", "msk.tf") if r == "pipeline/stream" else ("locals.tf",) for r in _sg_roots}
_sg_locals = {r: "\n".join(read("IaC", "terraform", "aws-managed", *r.split("/"), f) for f in fs) for r, fs in _sg_files.items()}
check("SG の ID を読む 6 ルートは try で読み（古い state のまま down.sh の destroy が通る）、base/core の state に security_group_ids が無ければ apply の前に止める",
      all(re.search(r'data "terraform_remote_state" "main" \{[\s\S]*?lifecycle \{\s*postcondition \{\s*condition\s*=\s*can\(self\.outputs\.security_group_ids(\["\w+"\])?\)', s) is not None
          and re.findall(r'security_group_ids\[', s)
          # stream の postcondition はキーまで見る（Telegraf の SG のキーを 2026-10-04 に変えた）。can の中の 1 つは try の数に入れない
          and len(re.findall(r'(?<!can\(self\.outputs\.)security_group_ids\[', s)) == len(re.findall(r'= try\(data\.terraform_remote_state\.main\.outputs\.security_group_ids\["\w+"\], ""\)', s))
          for s in _sg_locals.values())
      and not any("security_group_ids[" in read("IaC", "terraform", "aws-managed", *r.split("/"), f) for r in _sg_roots
                  for f in os.listdir(os.path.join(ROOT, "IaC", "terraform", "aws-managed", *r.split("/"))) if f.endswith(".tf") and f not in _sg_files[r]))
_destroy_agent = re.search(r"\ndestroy_agent\(\) \{[\s\S]*?\n\}", down).group(0)
check("down.sh は agent を lab の後、main の前に消し（ops/down-common.sh の destroy_agent）、ロググループ名を agent の state から読む",
      _down_body.index("\ndestroy_root pipeline/lab\n") < _down_body.index("\ndestroy_agent\n") < _down_body.index("\ndestroy_base_core\n")
      and 'destroy_lambda_root agent "$PREFIX-kb-index"' in _destroy_agent and "tf agent output -raw runtime_log_group_name" in _destroy_agent)
check("up.sh は main の後に agent を apply し、CREATE_KB のときだけ手順書を取り込む",
      up.index("tf_apply base/core") < up.index('tf_apply agent "${AGENT_VARS[@]}"') < up.index("start-ingestion-job")
      and re.search(r'if \[ -n "\$CREATE_KB" \]; then\nlog "4-3\. 手順書を置いて取り込む', up) is not None and 'AGENT_VARS+=(-var create_knowledge_base=true)' in up)

# ---- 2026-09-18 実機: wait_condition の timeout は asyncio.TimeoutError で、握らないとワークフロー自体が失敗して承認が拾えない
wsrc = read("app", "temporal", "worker.py")
check("承認待ちの wait_condition は TimeoutError を握って表を見直す（漏らすとワークフロー失敗）",
      "except asyncio.TimeoutError" in wsrc and wsrc.index("wait_condition(") < wsrc.index("except asyncio.TimeoutError"))
check("承認タブの注記はワークフローが Temporal であることを言い、表は折り返し、id は表から選べる",
      "Temporal" in web and "wrap=True" in web and "pr_id = gr.Dropdown(" in web and "proposal_detail" in web)

# ---- ops/up.sh が Web を立てる手順（2026-09-19 実機: app.py だけ置いて chat が無く、起動のたびに落ちていたのに「Web が動いている」と出た）
up = read("ops", "up.sh")
web_imports = {m for m in re.findall(r"^(?:import|from) (\w+)", read("app", "dashboard", "app.py"), re.M) if os.path.exists(os.path.join(ROOT, "app", "dashboard", m + ".py"))}
check(f"up.sh 4-2 は app/dashboard/*.py を全部置く（app.py が import する {sorted(web_imports)} を含む）",
      web_imports and 'for f in app/dashboard/*.py; do aws s3 cp --only-show-errors "$f" "s3://$ASSETS_BUCKET/web/${f#app/dashboard/}"; done' in up)
check("Web の起動確認は is-active（落ちて再起動するまでの数秒も active）ではなく 8080 を聞いているかで見る",
      "ss -ltn 'sport = :8080' | grep -q LISTEN" in up and "systemctl is-active --quiet $PREFIX-web.service" not in up)
check("lab の状態の照合は 1 つの空白で区切った lab=active containers=N をそのまま探す（空白を 2 つ要る形だと合わない）",
      '*" lab=active containers=$LAB_NODES "*)' in up)

# ---- ワーカーの振る舞い（2026-09-24 のレビュー: 承認のあいだに閉じた異常・apply の失敗・SQS の消し方。
#      2026-10-02 から異常の「いま」は Neptune でなくアラートで届く: 発生は入力の dict、解消はシグナル resolved。
#      2026-10-05 から修復案の「いま」は proposal_events の最新の行で、人の判断は決定のキュー → シグナル decide で届く）
import asyncio, datetime, logging  # noqa: E402
_saved = {k: getattr(awsio, k) for k in ("latest_proposal", "anomaly_proposals", "append_proposal_events",
                                         "receive_messages", "delete_message", "read_topology")}
appended = []  # 証跡（proposal_events）への append 1 回ぶん（行の list, 列）
awsio.append_proposal_events = lambda rows, columns: appended.append((list(rows), columns))
FS = 1700000000
AID = anomaly["anomaly_id"]
PID = f"{AID}#{FS}"
NOW = FS + 600  # ワークフローの now（workflow.now）
DECIDED = {"proposal_id": PID, "decision": "approved", "decided_by": "山田 (web)", "decided_at": FS + 300}

def run_wf(script, resolve_when=None, signals=(), signal_after="put_proposal", on_timeout=None, now=None, wait=None):
    """InvestigateAnomaly.run を、アクティビティを script（名前 → 返り値 / 例外 / 関数）に差し替えて回す。(結果, 呼んだアクティビティ, 待った timeout)。
    signals は signal_after のアクティビティの直後に届く decide（順に送る）。resolve_when(名前, 引数) が真を返したアクティビティの直後に、
    解消のシグナル（resolved）を届ける。on_timeout(timeout) は待ちが時間切れになる直前に呼ぶ（時間切れと同じ瞬間に届いたシグナル）。
    now() はワークフローの now（epoch 秒。既定は NOW）。wait(条件, timeout) は条件がそろっていない待ちの中身で、真を返せば時間切れにしない。
    最後のワークフローは run_wf.wf"""
    seen, waits = [], []
    wf = run_wf.wf = worker.InvestigateAnomaly()
    async def execute_activity(fn, *a, args=None, **opts):
        params = list(args) if args is not None else list(a)
        seen.append((fn.__name__, params, opts))
        r = script[fn.__name__]
        r = r(*params) if callable(r) else r
        if fn.__name__ == signal_after:
            for s in signals:
                wf.decide(s)
        if resolve_when and resolve_when(fn.__name__, params):
            wf.resolved("grafana")
        if isinstance(r, BaseException):
            raise r
        return r
    async def wait_condition(fn, timeout=None):
        waits.append(timeout)
        if not fn():
            if wait and wait(fn, timeout):
                return
            if on_timeout:
                on_timeout(timeout)
            raise asyncio.TimeoutError
    t_workflow.execute_activity = execute_activity; t_workflow.wait_condition = wait_condition
    t_workflow.now = lambda: datetime.datetime.fromtimestamp(now() if now else NOW, datetime.timezone.utc)
    t_workflow.info = lambda: types.SimpleNamespace(workflow_id="wf-1", run_id="run-1")
    t_workflow.logger = logging.getLogger("wf")
    return asyncio.run(wf.run(anomaly)), seen, waits

finding = {"cause": "c", "action": "heal-main", "command": "sudo lab heal-main", "reason": "r", "agent_response": "{}"}
CREATED = rules.proposal_event("created", {"proposal_id": PID, "anomaly_id": AID, "device_id": "hq-ce-01", "kind": "link_down", "target": "eth1",
                                           "first_seen": FS, **finding, "workflow_id": "wf-1", "run_id": "run-1", "created_at": FS + 60}, FS + 60, "r")
base = {"investigate": finding, "put_proposal": CREATED,
        "record_event": lambda p, e, f=None: rules.proposal_event(e, p, NOW, "", f),
        "record_ignored": lambda p, d, eff: rules.ignored_event(p, d, eff, NOW), "apply_on_lab": {"status": "Success", "output": "ok"}}
APPROVAL = datetime.timedelta(minutes=worker.APPROVAL_TIMEOUT_MINUTES)
VERIFY = datetime.timedelta(seconds=worker.VERIFY_TIMEOUT)
HOLD = datetime.timedelta(seconds=worker.HOLD_MINUTES * 60)
resolved_after = lambda name: (lambda n, p: n == name)
applied = lambda n, p: n == "record_event" and p[1] == "applied"
names = lambda seen: [n for n, _, _ in seen]
events = lambda seen: [p[1] for n, p, _ in seen if n == "record_event"]
ev_args = lambda seen, e: [p for n, p, _ in seen if n == "record_event" and p[1] == e][-1]  # [修復案, 出来事, fields]
ign_args = lambda seen: [p for n, p, _ in seen if n == "record_ignored"]  # [直前の行, 効かなかった決定, 効いた決定] の list
REJECTED_SUZUKI = {**DECIDED, "decision": "rejected", "decided_by": "鈴木 (web)", "decided_at": FS + 305}

def written(script):
    """script の record_event / record_ignored が返した行を、足した順に集める（(script, 行の list)）"""
    rows = []
    wrap = lambda f: (lambda *a: rows.append(f(*a)) or rows[-1])
    return {**script, "record_event": wrap(script["record_event"]), "record_ignored": wrap(script["record_ignored"])}, rows

res, seen, waits = run_wf(base, applied, [DECIDED])
check("承認のシグナル→打つ→解消のシグナルが届いたら verified（承認は APPROVAL_TIMEOUT_MINUTES 分、確かめは VERIFY_TIMEOUT 秒まで待つ）",
      res == "verified" and names(seen) == ["investigate", "put_proposal", "record_event", "apply_on_lab", "record_event", "record_event"]
      and events(seen) == ["approved", "applied", "verified"] and waits == [APPROVAL, VERIFY])
check("approved の行には、シグナルの decided_by と decided_at（Web が送った時刻）を残す",
      ev_args(seen, "approved")[2] == {"decided_by": "山田 (web)", "decided_at": FS + 300})
check("各段は 1 つ前の段が返した行（修復案の辞書）を持ち回る（applied には seq 2 の approved、verified には seq 3 の applied）",
      ev_args(seen, "approved")[0] == CREATED
      and (ev_args(seen, "applied")[0]["status"], ev_args(seen, "applied")[0]["seq"], ev_args(seen, "applied")[0]["decided_by"]) == ("approved", 2, "山田 (web)")
      and (ev_args(seen, "verified")[0]["status"], ev_args(seen, "verified")[0]["seq"]) == ("applied", 3))
check("investigate にはアラートの dict をそのまま渡し、put_proposal にはワークフローの id と実行の id も渡す",
      seen[0][1] == [anomaly] and seen[1][1] == [anomaly, finding, "wf-1", "run-1"])
check("investigate の start_to_close は 4 分（AgentCore の読み取り 150 秒 1 回分が収まる）",
      [o for n, _, o in seen if n == "investigate"][0]["start_to_close_timeout"] == datetime.timedelta(minutes=4))
check("apply_on_lab は 1 回しか打たない（maximum_attempts=1）",
      [o for n, _, o in seen if n == "apply_on_lab"][0]["retry_policy"] == {"maximum_attempts": 1})
res, seen, waits = run_wf(base, signals=[{k: v for k, v in DECIDED.items() if k != "decided_at"}])
check("シグナルに decided_at が無ければ、ワークフローの now を decided_at にする", ev_args(seen, "approved")[2]["decided_at"] == NOW)
check("打ったあと VERIFY_TIMEOUT 秒のうちに解消のシグナルが来なければ failed を書き、そのあと解消を待って id を握る（HOLD_MINUTES 分まで）",
      res == "failed" and events(seen) == ["approved", "applied", "failed"] and waits == [APPROVAL, VERIFY, HOLD]
      and "解消の通知が届かない" in ev_args(seen, "failed")[2]["verify_note"])
res, seen, waits = run_wf(base, resolved_after("put_proposal"))
check("承認を待つあいだに解消したら（決定は無い）打たずに obsolete（approved の行は無い。握らない）",
      res == "obsolete" and names(seen) == ["investigate", "put_proposal", "record_event"] and events(seen) == ["obsolete"] and waits == [APPROVAL]
      and "解消したので打たなかった" in ev_args(seen, "obsolete")[2]["verify_note"])
res, seen, waits = run_wf(base, resolved_after("put_proposal"), [DECIDED])
check("承認と同時に解消していれば、判断は approved の行に残して打たずに obsolete",
      res == "obsolete" and "apply_on_lab" not in names(seen) and events(seen) == ["approved", "obsolete"] and HOLD not in waits)
res, seen, waits = run_wf({**base, "apply_on_lab": ActivityError("activity failed", cause=RuntimeError("SSM に届かない"))}, signals=[DECIDED])
check("apply_on_lab の失敗（ActivityError）はワークフローを落とさず failed を書く（approved のまま残さない）。確かめは待たず、id は握る",
      res == "failed" and events(seen) == ["approved", "failed"] and "SSM に届かない" in ev_args(seen, "failed")[2]["apply_output"]
      and VERIFY not in waits and waits[-1] == HOLD)
res, seen, waits = run_wf({**base, "apply_on_lab": {"status": "Failed", "output": "exit 1"}}, signals=[DECIDED])
check("コマンドが失敗を返したときも failed（確かめは待たない）",
      res == "failed" and events(seen) == ["approved", "failed"] and ev_args(seen, "failed")[2] == {"apply_output": "Failed: exit 1"} and VERIFY not in waits)
res, seen, waits = run_wf(base, signals=[{**DECIDED, "decision": "rejected", "decided_by": "鈴木 (web)"}])
check("却下なら打たない（判断は rejected の行に残す）。同じ異常の次の通知でもう一度調べないよう id は握る",
      res == "rejected" and "apply_on_lab" not in names(seen) and events(seen) == ["rejected"]
      and ev_args(seen, "rejected")[2]["decided_by"] == "鈴木 (web)" and waits == [APPROVAL, HOLD])
_s, _rows = written(base)
res, seen, waits = run_wf(_s, applied, [DECIDED, REJECTED_SUZUKI])
check("合うシグナルが 2 回（approved、rejected の順）届いたら、効くのは 1 回目（approved、decided_by も 1 回目の名前）",
      res == "verified" and events(seen) == ["approved", "applied", "verified"] and ev_args(seen, "approved")[2]["decided_by"] == "山田 (web)"
      and run_wf.wf._decision["decided_by"] == "山田 (web)")
check("承認のあとに別の名前の却下が届いたら、打ってから（applied の行のあと、apply_on_lab より後に）ignored の行が 1 つ増え"
      "（seq は次、status は直前の applied のまま、決めた人は効いた承認のまま）、detail に却下した人の名前と時刻が入る。続く行は ignored の行の seq の次",
      names(seen) == ["investigate", "put_proposal", "record_event", "apply_on_lab", "record_event", "record_ignored", "record_event"]
      and [(r["event"], r["status"], r["seq"]) for r in _rows] == [("approved", "approved", 2), ("applied", "applied", 3), ("ignored", "applied", 4), ("verified", "verified", 5)]
      and (_rows[2]["decided_by"], _rows[2]["decided_at"], _rows[2]["event_id"]) == ("山田 (web)", FS + 300, f"{PID}#ignored#rejected#{FS + 305}#鈴木 (web)")
      and _rows[2]["detail"] == "却下（鈴木 (web)、2023-11-15 07:18:25）が届いたが、先に承認が決まっていた"
      and ign_args(seen)[0][2] == DECIDED and waits == [APPROVAL, VERIFY])
_vclock = [NOW]
def _slow_ignored(p, d, eff):
    _vclock[0] += worker.VERIFY_TIMEOUT + 100  # S3 Tables への書き込みが確かめの窓より長くかかった
    return rules.ignored_event(p, d, eff, NOW)
def _resolve_in_10s(fn, timeout):
    """待ちに入って 10 秒で解消の通知が届く"""
    _vclock[0] += 10
    run_wf.wf.resolved("grafana")
    return fn()
_s, _rows = written({**base, "record_ignored": _slow_ignored})
res, seen, waits = run_wf(_s, signals=[DECIDED, REJECTED_SUZUKI], now=lambda: _vclock[0], wait=_resolve_in_10s)
check("打つ前に控えた効かなかった決定は applied の行のすぐあとに書き、書くのにかかった時間は確かめの VERIFY_TIMEOUT 秒に数えない（書き終えてから待つ）",
      res == "verified" and [r["event"] for r in _rows] == ["approved", "applied", "ignored", "verified"] and waits == [APPROVAL, VERIFY])
_s, _rows = written(base)
res, seen, waits = run_wf(_s, applied, [DECIDED, DECIDED, {**DECIDED}])
check("同じ承認（decision・decided_by・decided_at が全部同じ。SQS の重複配達）をもう一度送っても行は増えない",
      res == "verified" and "record_ignored" not in names(seen) and [r["event"] for r in _rows] == ["approved", "applied", "verified"])
res, seen, waits = run_wf(base, applied, [DECIDED, {**REJECTED_SUZUKI, "proposal_id": f"{AID}#{FS - 3600}"}])
check("効いた決定のあとでも、proposal_id の違う決定（同じ異常の前の発生への決定）は ignored の行にしない",
      res == "verified" and "record_ignored" not in names(seen) and run_wf.wf._ignored == [])
_s, _rows = written(base)
res, seen, waits = run_wf(_s, applied, [DECIDED, REJECTED_SUZUKI, REJECTED_SUZUKI, {**DECIDED, "decided_at": FS + 310}])
check("効かなかった決定の重複配達も 1 行だけ。同じ人の同じ承認でも送った時刻が違えば（2 回押した）別の決定として ignored の行にする",
      [(r["event"], r["seq"]) for r in _rows if r["event"] == "ignored"] == [("ignored", 4), ("ignored", 5)]
      and _rows[3]["detail"] == "承認（山田 (web)、2023-11-15 07:18:30）が届いたが、先に承認が決まっていた")
_s, _rows = written(base)
res, seen, waits = run_wf(_s, applied, [DECIDED, REJECTED_SUZUKI, {**REJECTED_SUZUKI, "decision": "approved"}])
_ign = [r for r in _rows if r["event"] == "ignored"]
check("効いた決定のあとに同じ人が同じ秒に却下と承認を送ると、ignored の行が 2 つでき、event_id も別（event_id で重複を落としても 2 行残る）",
      [(r["seq"], r["event_id"]) for r in _ign] == [(4, f"{PID}#ignored#rejected#{FS + 305}#鈴木 (web)"), (5, f"{PID}#ignored#approved#{FS + 305}#鈴木 (web)")]
      and len({r["event_id"] for r in _rows}) == len(_rows))
_third = {**REJECTED_SUZUKI, "decided_by": "佐藤 (web)", "decided_at": FS + 330}
_s, _rows = written({**base, "record_ignored": lambda p, d, eff: (d["decided_by"] == "鈴木 (web)" and run_wf.wf.decide(_third)) or rules.ignored_event(p, d, eff, NOW)})
res, seen, waits = run_wf(_s, applied, [DECIDED, REJECTED_SUZUKI])
check("ignored の行を書いているあいだに届いた次の決定も、同じ流れで続けて行にする（届いた順、seq は 4、5）",
      [(r["event"], r["seq"], r["detail"][:5]) for r in _rows]
      == [("approved", 2, ""), ("applied", 3, ""), ("ignored", 4, "却下（鈴木"), ("ignored", 5, "却下（佐藤"), ("verified", 6, "")])
_late = {**DECIDED, "decided_by": "山田 (web)", "decided_at": FS + 320}
_s, _rows = written({**base, "record_event": lambda p, e, f=None: (e == "rejected" and run_wf.wf.decide(_late)) or rules.proposal_event(e, p, NOW, "", f)})
res, seen, waits = run_wf(_s, signals=[REJECTED_SUZUKI])
check("却下の行を書いているあいだに届いた承認も、解消を待つ 24 時間の頭で却下の行のあとに ignored の行（status は rejected のまま）にし、残りを待って id を握る",
      res == "rejected" and [(r["event"], r["status"], r["seq"]) for r in _rows] == [("rejected", "rejected", 2), ("ignored", "rejected", 3)]
      and _rows[1]["detail"] == "承認（山田 (web)、2023-11-15 07:18:40）が届いたが、先に却下が決まっていた"
      and (_rows[1]["decided_by"], _rows[1]["decided_at"]) == ("鈴木 (web)", FS + 305) and waits == [APPROVAL, HOLD, HOLD])
_s, _rows = written(base)
res, seen, waits = run_wf(_s, lambda n, p: n == "record_event" and p[1] == "rejected", [REJECTED_SUZUKI, DECIDED])
check("却下のあとに承認が届き、却下の行のときにはもう解消していた（解消を待たずに閉じる）ときも、閉じる前に ignored の行にする",
      res == "rejected" and [(r["event"], r["status"], r["seq"]) for r in _rows] == [("rejected", "rejected", 2), ("ignored", "rejected", 3)]
      and waits == [APPROVAL])
_fourth = {**DECIDED, "decided_by": "佐藤 (web)", "decided_at": FS + 330}
_s, _rows = written({**base, "record_ignored": lambda p, d, eff: (d["decided_by"] == "山田 (web)" and run_wf.wf.decide(_fourth)) or rules.ignored_event(p, d, eff, NOW)})
res, seen, waits = run_wf(_s, lambda n, p: n == "record_event" and p[1] == "rejected", [REJECTED_SUZUKI, DECIDED])
check("閉じる前に ignored の行を書いているあいだに届いた決定も、閉じる前に続けて行にする（届いた順、seq は 3、4）",
      res == "rejected" and [(r["event"], r["seq"], r["detail"][:5]) for r in _rows] == [("rejected", 2, ""), ("ignored", 3, "承認（山田"), ("ignored", 4, "承認（佐藤")]
      and waits == [APPROVAL] and run_wf.wf._ignored == [])
_s, _rows = written(base)
res, seen, waits = run_wf(_s, signals=[REJECTED_SUZUKI], on_timeout=lambda t: t == HOLD and run_wf.wf.decide(DECIDED))
check("解消を待つ 24 時間が切れるのと同じ瞬間に届いた承認も、閉じる前に ignored の行にする",
      res == "rejected" and [(r["event"], r["status"], r["seq"]) for r in _rows] == [("rejected", "rejected", 2), ("ignored", "rejected", 3)]
      and waits == [APPROVAL, HOLD] and run_wf.wf._ignored == [])
_s, _rows = written(base)
res, seen, waits = run_wf(_s, signals=[DECIDED, REJECTED_SUZUKI], signal_after="record_event")
check("時間切れ（expired）のあとに承認を送り、続けて別の名前の却下を送っても、行は増えない（ignored の行も付かない）。その承認は効いた決定として控えない",
      res == "expired" and names(seen) == ["investigate", "put_proposal", "record_event"] and [r["event"] for r in _rows] == ["expired"]
      and run_wf.wf._decision == {} and run_wf.wf._ignored == [] and waits == [APPROVAL, HOLD])
_s, _rows = written(base)
res, seen, waits = run_wf(_s, resolved_after("put_proposal"), [DECIDED, REJECTED_SUZUKI], signal_after="record_event")
check("承認を待つあいだに解消して obsolete になったあとに届いた決定も、行にせず控えない",
      res == "obsolete" and [r["event"] for r in _rows] == ["obsolete"] and run_wf.wf._decision == {} and "record_ignored" not in names(seen))
res, seen, waits = run_wf({**base, "record_ignored": ActivityError("activity failed", cause=RuntimeError("S3 Tables に届かない"))},
                          applied, [DECIDED, REJECTED_SUZUKI])
check("ignored の行を書けなくても（ActivityError）ワークフローは止めずに打って確かめる（次の行は approved の行の seq の次）",
      res == "verified" and events(seen) == ["approved", "applied", "verified"] and ev_args(seen, "applied")[0]["seq"] == 2)
res, seen, waits = run_wf(base, signals=[{**DECIDED, "proposal_id": f"{AID}#1600000000"}, {**DECIDED, "proposal_id": "other#link_down#eth1#1700000000"}])
check("proposal_id の違うシグナル（同じ異常の前の発生・別の異常への決定）は無視して待ち続け、時間切れで expired",
      res == "expired" and run_wf.wf._decision == {} and events(seen) == ["expired"] and waits == [APPROVAL, HOLD])
res, seen, waits = run_wf(base, applied, [DECIDED], signal_after="investigate")
check("調査のあいだ（put_proposal の前）に届いた決定も受ける（proposal_id は調査より前に決まる）",
      res == "verified" and events(seen) == ["approved", "applied", "verified"])
res, seen, waits = run_wf(base)
check("時間切れは expired を書く（verify_note に時間切れ）。id は握る",
      res == "expired" and events(seen) == ["expired"] and "時間切れ" in ev_args(seen, "expired")[2]["verify_note"] and waits == [APPROVAL, HOLD])
res, seen, waits = run_wf({**base, "investigate": {**finding, "action": "none", "command": ""}}, applied, [DECIDED])
check("処置なし（action = none）は打たずに applied を書き、解消を待つ",
      res == "verified" and "apply_on_lab" not in names(seen) and events(seen) == ["approved", "applied", "verified"]
      and "処置なし" in ev_args(seen, "applied")[2]["apply_output"])
_wf = worker.InvestigateAnomaly(); _wf.decide(DECIDED)
check("走り出す前（proposal_id が決まる前）のシグナル decide は受けない", _wf._decision == {})
_wf._proposal_id = PID
for _bad in ("approved", None, {**DECIDED, "decision": "applied"}, {**DECIDED, "decision": ""}, {k: v for k, v in DECIDED.items() if k != "proposal_id"}):
    _wf.decide(_bad)
check("シグナル decide は dict で、decision が approved / rejected で、proposal_id が合うものだけを受ける", _wf._decision == {})
_wf.decide({**DECIDED, "decision": "rejected"}); _wf.decide(DECIDED)
check("受けた決定は後から来たもので上書きしない（SQS の重複配達・2 回押しても 1 回目）", _wf._decision["decision"] == "rejected")
# 解消を待つあいだ（VERIFY / HOLD）に届いた効かなかった決定は、待ち終わるのを待たずにその場で行にする（HOLD は 24 時間ある）
_wf._row = rules.proposal_event("rejected", CREATED, NOW, "", {"decided_by": "鈴木 (web)", "decided_at": FS + 305})
_hold_calls, _hold_waits = [], []
async def _hold_ea(fn, *a, args=None, **o):
    _hold_calls.append(fn.__name__)
    return base[fn.__name__](*args)
async def _hold_wait(fn, timeout=None):
    _hold_waits.append(timeout)
    if not fn():
        raise asyncio.TimeoutError
t_workflow.execute_activity, t_workflow.wait_condition = _hold_ea, _hold_wait
check("解消を待つあいだに控えた効かなかった決定は、待ちの途中で ignored の行にし、残りの時間をまた待つ",
      asyncio.run(_wf._wait_resolved(worker.HOLD_MINUTES * 60)) is False and _hold_calls == ["record_ignored"]
      and _hold_waits == [HOLD, HOLD] and (_wf._row["event"], _wf._row["status"], _wf._row["seq"]) == ("ignored", "rejected", 3) and _wf._ignored == [])
_clock = [NOW]
t_workflow.now = lambda: datetime.datetime.fromtimestamp(_clock[0], datetime.timezone.utc)
async def _clock_wait(fn, timeout=None):
    """条件がそろうまでに 1 時間たつ待ち"""
    _hold_waits.append(timeout)
    if not fn():
        raise asyncio.TimeoutError
    _clock[0] += 3600
t_workflow.wait_condition = _clock_wait
_wf = worker.InvestigateAnomaly(); _wf._proposal_id = PID
_wf.decide(REJECTED_SUZUKI); _wf.decide(DECIDED)
_wf._row = rules.proposal_event("rejected", CREATED, NOW, "", {"decided_by": "鈴木 (web)", "decided_at": FS + 305})
_hold_calls.clear(); _hold_waits.clear()
check("待ちの途中で ignored の行にしたあとは、締め切りまでの残り（24 時間 - 届くまでの 1 時間）だけ待つ（届くたびに 24 時間が延びない）",
      asyncio.run(_wf._wait_resolved(worker.HOLD_MINUTES * 60)) is False and _hold_calls == ["record_ignored"]
      and _hold_waits == [HOLD, HOLD - datetime.timedelta(hours=1)])
_wf = worker.InvestigateAnomaly(); _wf._ignored = [DECIDED]
_hold_calls.clear(); _hold_waits.clear()
check("行がまだ無い（_row が空）あいだは、控えがあっても待ちは起きない（書けないまま回り続けない）",
      asyncio.run(_wf._wait_resolved(worker.HOLD_MINUTES * 60)) is False and _hold_calls == [] and _hold_waits == [HOLD] and _wf._ignored == [DECIDED])
t_workflow.now = lambda: datetime.datetime.fromtimestamp(NOW, datetime.timezone.utc)
# S3: 同じ異常の前の発生への決定は、ワークフローが終わったあとに届いた決定と同じくログだけ
t_workflow.logger = logging.getLogger("wf")
_wlogged = []
_wcap = logging.Handler(); _wcap.emit = lambda r: _wlogged.append(r.getMessage())
t_workflow.logger.addHandler(_wcap)
worker.InvestigateAnomaly().decide(DECIDED)
_wf = worker.InvestigateAnomaly(); _wf._proposal_id = PID
_wf.decide({**DECIDED, "proposal_id": f"{AID}#1600000000", "decided_by": "Carol (web)"})
t_workflow.logger.removeHandler(_wcap)
check("同じ異常の前の発生（proposal_id が違う）への決定は控えず、ワークフローのログに 1 行出す（どの修復案への何の決定か、名前）。走り出す前のシグナルはログも出さない",
      _wf._decision == {} and _wf._ignored == []
      and _wlogged == [f"proposal {PID}: 別の修復案 {AID}#1600000000 への approved by Carol (web) が届いた（行にしない）"])

# アクティビティ（awsio を差し替え）
_old = f"{AID}#1600000000"
_latest = {}
awsio.anomaly_proposals = lambda aid: _latest
appended.clear()
row = asyncio.run(worker.put_proposal(anomaly, {**finding, "precheck": "pc", "precheck_verdict": "ok"}, "wf-1", "run-1"))
check("put_proposal は <anomaly_id>#<first_seen> の created の行（pending、seq 1、送り手・アラートの detail・実行の id・事前チェック）を 1 回の append で足し、その行を返す",
      row["proposal_id"] == PID and row["event_id"] == f"{PID}#created" and row["status"] == "pending" and row["seq"] == 1
      and row["workflow_id"] == "wf-1" and row["run_id"] == "run-1" and row["source"] == "grafana" and row["alert_detail"] == "ifOperStatus down"
      and row["detail"] == "r" and row["precheck"] == "pc" and row["precheck_verdict"] == "ok" and row["first_seen"] == FS
      and appended == [([row], rules.PROPOSAL_EVENT_COLUMNS)])
_prev_pending = rules.proposal_event("created", {"proposal_id": _old, "anomaly_id": AID, "first_seen": 1600000000, "command": "sudo lab heal-main",
                                                 "workflow_id": "wf-1", "run_id": "run-0"}, 1600000060)
_prev_done = {**_prev_pending, "proposal_id": f"{AID}#1500000000", "status": "verified", "seq": 4}
_latest = {_old: _prev_pending, f"{AID}#1500000000": _prev_done}
appended.clear()
row = asyncio.run(worker.put_proposal(anomaly, finding, "wf-1", "run-1"))
_rows = appended[0][0] if appended else []
check("同じ異常で proposal_id の違う pending があれば、expired の行（verify_note は新しい修復案ができた）を 1 つ足してから created を足す（同じ append）",
      len(appended) == 1 and [(r["proposal_id"], r["status"], r["seq"]) for r in _rows] == [(_old, "expired", 2), (PID, "pending", 1)]
      and _rows[0]["verify_note"] == worker.NEWER_PROPOSAL_NOTE and _rows[0]["detail"] == worker.NEWER_PROPOSAL_NOTE
      and _rows[0]["command"] == "sudo lab heal-main" and _rows[1] == row)
_latest = {PID: {**CREATED, "status": "approved", "seq": 2}}
appended.clear()
check("同じ proposal_id の行がこの実行の書いたもの（書けたあとで再試行）なら、書かずにその最新の行で進む",
      asyncio.run(worker.put_proposal(anomaly, finding, "wf-1", "run-1")) == _latest[PID] and appended == [])
def _put_err(existing):
    global _latest
    _latest = {PID: {**CREATED, **existing}}
    try:
        asyncio.run(worker.put_proposal(anomaly, finding, "wf-1", "run-1"))
    except ApplicationError as e:
        return e
    return None
# ワークフローの id は異常ごとなので、同じ id でも前の実行が書いた修復案でありうる
check("同じワークフロー id でも別の実行（run_id が違う）の修復案なら再試行しない失敗（書かない）",
      (lambda e: e is not None and e.non_retryable)(_put_err({"workflow_id": "wf-1", "run_id": "run-0"})) and appended == [])
check("別のワークフローの修復案なら再試行しない失敗（書かない）",
      (lambda e: e is not None and e.non_retryable)(_put_err({"workflow_id": "wf-other", "run_id": "run-1"})) and appended == [])
_r2 = asyncio.run(worker.record_event(CREATED, "approved", {"decided_by": "山田 (web)", "decided_at": FS + 300}))
_r3 = asyncio.run(worker.record_event(_r2, "applied", {"apply_output": "Success: ok"}))
check("record_event は fields を重ねて seq を 1 進めた行を 1 行ずつ足して返し、detail は apply_output / verify_note（決めた人は後の行にも残る）",
      [len(r) for r, _ in appended] == [1, 1] and appended[0][0][0] == _r2 and appended[1][0][0] == _r3
      and (_r2["event_id"], _r2["status"], _r2["seq"], _r2["decided_by"], _r2["detail"]) == (f"{PID}#approved", "approved", 2, "山田 (web)", "")
      and (_r3["status"], _r3["seq"], _r3["decided_by"], _r3["detail"], _r3["apply_output"]) == ("applied", 3, "山田 (web)", "Success: ok", "Success: ok"))
appended.clear()
_ri = asyncio.run(worker.record_ignored(_r3, {**REJECTED_SUZUKI, "decided_by": ""}, DECIDED))
check("record_ignored は効かなかった決定の行を 1 行足して返す（status・決めた人・apply_output は直前の行のまま。名前が無ければ -）",
      appended == [([_ri], rules.PROPOSAL_EVENT_COLUMNS)] and list(_ri) == [c for c, _ in rules.PROPOSAL_EVENT_COLUMNS]
      and (_ri["event"], _ri["status"], _ri["seq"], _ri["decided_by"], _ri["apply_output"], _ri["event_id"])
      == ("ignored", "applied", 4, "山田 (web)", "Success: ok", f"{PID}#ignored#rejected#{FS + 305}#")
      and _ri["detail"] == "却下（-、2023-11-15 07:18:25）が届いたが、先に承認が決まっていた")

# starter（SQS のメッセージ 1 通ずつ。アラートのキュー: firing は起こす、resolved は走っているワークフローへシグナル。決定のキュー: decide のシグナル）
class FakeTemporal:
    def __init__(self, exc=None, sig_exc=None):
        self.exc, self.sig_exc, self.started, self.signals = exc, sig_exc, [], []
    async def start_workflow(self, fn, arg=None, id=None, task_queue=None):
        self.started.append((arg, id))
        if self.exc:
            raise self.exc
    def get_workflow_handle(self, wid):
        tc = self
        class Handle:
            async def signal(self, fn, arg=None):
                tc.signals.append((wid, fn.__name__, arg))
                if tc.sig_exc:
                    raise tc.sig_exc
        return Handle()
def msg(status="firing", kind="link_down", fs=FS, source="grafana", n=1):
    return json.dumps({"source": source, "alerts": [
        {"status": status, "device_id": "hq-ce-01", "kind": kind, "target": f"eth{i + 1}", "detail": "ifOperStatus down", "starts_at": fs} for i in range(n)]})
WID = f"investigate-{AID}"
awsio.latest_proposal = lambda p: {}
awsio.read_topology = lambda: ([{"device_id": "hq-ce-01", "status": "DOWN", "maintenance": True}], [])
tc = FakeTemporal()
asyncio.run(worker.handle_message(tc, msg()))
check("保守中の機器の link_down ではワークフローを起こさない（例外にしない = 通知は消す）", tc.started == [])
awsio.read_topology = lambda: ([{"device_id": "hq-ce-01", "status": "DOWN", "maintenance": False}], [])
tc = FakeTemporal()
asyncio.run(worker.handle_message(tc, msg()))
check("firing の link_down は、異常ごとの id（investigate-<anomaly_id>）でワークフローを起こし、アラートの dict を渡す",
      tc.started == [(anomaly, WID)] and tc.signals == [])
tc = FakeTemporal()
asyncio.run(worker.handle_message(tc, msg(source="splunk", n=2)))
check("1 通に 2 件あれば 2 つ起こす（送り手も渡す）",
      [i for _, i in tc.started] == [WID, f"investigate-hq-ce-01#link_down#eth2"] and tc.started[0][0]["source"] == "splunk")
tc = FakeTemporal()
for k in ("trap", "bgp_down", "isis_down"):
    asyncio.run(worker.handle_message(tc, msg(kind=k)))
    asyncio.run(worker.handle_message(tc, msg("resolved", kind=k)))
asyncio.run(worker.handle_message(tc, "garbage"))
check("link_down 以外（trap / bgp_down / isis_down）と読めない本文は、起こさずシグナルも送らない（例外にもしない = 消す）",
      tc.started == [] and tc.signals == [])
looked = []
awsio.latest_proposal = lambda p: looked.append(p) or {"proposal_id": p, "first_seen": FS, "status": "failed"}
tc = FakeTemporal()
asyncio.run(worker.handle_message(tc, msg()))
check("同じ発生（<anomaly_id>#<first_seen>）の修復案が proposal_events にもうあれば起こさない（閉じたあとで届いた、同じ starts_at の繰り返しの通知）",
      tc.started == [] and looked == [PID])
awsio.latest_proposal = lambda p: {}
tc = FakeTemporal()
asyncio.run(worker.handle_message(tc, msg("resolved", source="splunk")))
check("resolved はそのワークフローへシグナル resolved を送り、起こさない", tc.started == [] and tc.signals == [(WID, "resolved", "splunk")])
received, deleted = [], []
awsio.delete_message = lambda q, h: deleted.append((q, h))
_inbox = []
awsio.receive_messages = lambda q: received.append(q) or list(_inbox)
_dbody = json.dumps({"type": "decision", "proposal_id": PID, "decision": "approved", "decided_by": "山田 (web)", "sent_at": FS + 300})
_inbox[:] = [{"Body": _dbody, "ReceiptHandle": "d0"}]
tc = FakeTemporal()
asyncio.run(worker.starter_queue(tc, "q-alerts", worker.handle_message))
check("アラートのキューに来た決定（{\"type\":\"decision\",…}）は、シグナルを送らず起こさずに消す（決定は決定のキューからだけ受ける）",
      tc.started == [] and tc.signals == [] and received == ["q-alerts"] and deleted == [("q-alerts", "d0")])
deleted.clear()
_inbox[:] = [{"Body": msg("resolved"), "ReceiptHandle": "r0"}]
asyncio.run(worker.starter_queue(FakeTemporal(sig_exc=RPCError("workflow not found", t_service.RPCStatusCode.NOT_FOUND)), "q-alerts", worker.handle_message))
check("resolved の相手が走っていない（NOT_FOUND）のは普通のこと（link_down 以外・もう閉じた）なので消す", deleted == [("q-alerts", "r0")])
deleted.clear()
asyncio.run(worker.starter_queue(FakeTemporal(sig_exc=RPCError("temporal に届かない", t_service.RPCStatusCode.UNAVAILABLE)), "q-alerts", worker.handle_message))
check("それ以外の RPC の失敗は消さずに残す（解消のシグナルを落とすと verify が時間切れで failed になる）", deleted == [])
_inbox[:] = [{"Body": msg(), "ReceiptHandle": "r1"}]
asyncio.run(worker.starter_queue(FakeTemporal(WorkflowAlreadyStarted()), "q-alerts", worker.handle_message))
check("もう起きている（WorkflowAlreadyStartedError = 同じ異常の繰り返し・重複配達）なら消す（残すと DLQ で本物の失敗と混ざる）", deleted == [("q-alerts", "r1")])
deleted.clear()
asyncio.run(worker.starter_queue(FakeTemporal(RuntimeError("temporal に届かない")), "q-alerts", worker.handle_message))
check("それ以外の失敗は消さずに残す（可視性タイムアウトのあとで配り直し、5 回で DLQ）", deleted == [])
awsio.latest_proposal = lambda p: (_ for _ in ()).throw(OSError("S3 Tables に届かない"))
asyncio.run(worker.starter_queue(FakeTemporal(), "q-alerts", worker.handle_message))
check("proposal_events を読めないときも消さない", deleted == [])
awsio.latest_proposal = lambda p: {}
_inbox[:] = [{"Body": "garbage", "ReceiptHandle": "r2"}, {"Body": msg(kind="trap"), "ReceiptHandle": "r3"}]
asyncio.run(worker.starter_queue(FakeTemporal(), "q-alerts", worker.handle_message))
check("読めない本文と、起こさない種類のアラートは消す", deleted == [("q-alerts", "r2"), ("q-alerts", "r3")])

# 決定のキュー（handle_decision）
deleted.clear(); appended.clear()
_inbox[:] = [{"Body": _dbody, "ReceiptHandle": "d1"}]
tc = FakeTemporal()
asyncio.run(worker.starter_queue(tc, "q-decisions", worker.handle_decision))
check("決定のキューのメッセージは investigate-<anomaly_id> にシグナル decide（decided_at は Web が送った時刻）を送って消し、行は書かない",
      tc.signals == [(WID, "decide", {"proposal_id": PID, "decision": "approved", "decided_by": "山田 (web)", "decided_at": FS + 300})]
      and tc.started == [] and deleted == [("q-decisions", "d1")] and appended == [])
_nf = lambda: FakeTemporal(sig_exc=RPCError("workflow not found", t_service.RPCStatusCode.NOT_FOUND))
deleted.clear(); looked.clear()
awsio.latest_proposal = lambda p: looked.append(p) or {**CREATED}
asyncio.run(worker.starter_queue(_nf(), "q-decisions", worker.handle_decision))
_rows = appended[0][0] if appended else []
check("ワークフローが無い（NOT_FOUND）のに修復案が pending なら、expired の行（verify_note はワークフローがもう無い）を 1 つ足して消す",
      looked == [PID] and len(appended) == 1 and [(r["proposal_id"], r["status"], r["seq"]) for r in _rows] == [(PID, "expired", 2)]
      and _rows[0]["verify_note"] == worker.NO_WORKFLOW_NOTE and appended[0][1] == rules.PROPOSAL_EVENT_COLUMNS and deleted == [("q-decisions", "d1")])
deleted.clear(); appended.clear()
for _st in ({**CREATED, "status": "approved", "seq": 2}, {}):
    awsio.latest_proposal = lambda p, _st=_st: _st
    asyncio.run(worker.starter_queue(_nf(), "q-decisions", worker.handle_decision))
check("ワークフローが無く、修復案が pending でない（もう決まった・無い）なら何も書かずに消す",
      appended == [] and deleted == [("q-decisions", "d1")] * 2)
_logged = []
_cap = logging.Handler(); _cap.emit = lambda r: _logged.append(r.getMessage())
worker.log.addHandler(_cap)
asyncio.run(worker.starter_queue(_nf(), "q-decisions", worker.handle_decision))
worker.log.removeHandler(_cap)
check("ワークフローが終わったあとに届いた決定は、行にせず worker のログに 1 行出す（決定と名前と修復案の状態）",
      appended == [] and [m for m in _logged if m.startswith(f"decide {PID}: ")] == [f"decide {PID}: approved by 山田 (web) が届いたが、ワークフローが無い（修復案は 無い。行にしない）"])
deleted.clear()
asyncio.run(worker.starter_queue(FakeTemporal(sig_exc=RPCError("temporal に届かない", t_service.RPCStatusCode.UNAVAILABLE)), "q-decisions", worker.handle_decision))
check("Temporal に届かない（NOT_FOUND 以外の RPC の失敗）なら消さない（配り直し、直らなければ DLQ）", deleted == [] and appended == [])
awsio.latest_proposal = lambda p: (_ for _ in ()).throw(OSError("S3 Tables に届かない"))
asyncio.run(worker.starter_queue(_nf(), "q-decisions", worker.handle_decision))
check("ワークフローが無く、proposal_events も読めないときは消さない", deleted == [] and appended == [])
awsio.latest_proposal = lambda p: {}
_inbox[:] = [{"Body": "garbage", "ReceiptHandle": "d2"}, {"Body": msg(), "ReceiptHandle": "d3"},
             {"Body": json.dumps({"type": "decision", "proposal_id": PID, "decision": "applied"}), "ReceiptHandle": "d4"}]
tc = FakeTemporal()
asyncio.run(worker.starter_queue(tc, "q-decisions", worker.handle_decision))
check("決定のキューの読めない本文・アラート・approved / rejected 以外の決定は、シグナルを送らず起こさずに消す",
      tc.signals == [] and tc.started == [] and deleted == [("q-decisions", "d2"), ("q-decisions", "d3"), ("q-decisions", "d4")])
for k, v in _saved.items():
    setattr(awsio, k, v)

# ---- app/dashboard/incident_view.py の承認（gradio / pandas / config は差し替えて読む。config は環境変数と env ファイルを読むので本物は使わない）
_gr = types.ModuleType("gradio"); _gr.update = lambda **k: ("update", k)
_gr.SelectData = type("SelectData", (), {})  # select_proposal の注釈。中身は types.SimpleNamespace で渡す
_pd = types.ModuleType("pandas"); _pd.DataFrame = lambda rows, columns=None: rows
_web_mods = {"gradio": _gr, "pandas": _pd, "config": types.ModuleType("config")}
_prev = {k: sys.modules.get(k) for k in _web_mods}
sys.modules.update(_web_mods)
sys.path.insert(0, os.path.join(ROOT, "app", "dashboard"))
import incident_view as iv  # noqa: E402
for k, v in _prev.items():
    if v is None:
        sys.modules.pop(k, None)
    else:
        sys.modules[k] = v
decided = []
iv.proposals.decide = lambda pid, d, decided_by="": decided.append((pid, d, decided_by)) or {"proposal_id": pid, "status": "sent", "decision": d, "decided_by": decided_by, "sent_at": 1}
iv.proposals.list_proposals = lambda status="pending", limit=100: {"proposals": [{"proposal_id": "p1", "status": status}]}
nothing = ("update", {})
check("名前が無ければ書かない（表と選択もそのまま）",
      iv.decide_proposal("p1", "rejected", "pending", "  ", True)[1:] == (nothing, nothing) and decided == [])
ab = lambda ok, name: iv.approve_button(ok, name)[1]  # テストの gr.update は ("update", kwargs) を返す
check("承認ボタンは名前とチェックがそろうまで押せず、足りないものを文字で出す",
      ab(False, "")["interactive"] is False and "名前とチェックが要る" in ab(False, "")["value"]
      and ab(True, " ")["interactive"] is False and "名前が要る" in ab(True, " ")["value"]
      and "チェックが要る" in ab(False, "yamada")["value"]
      and ab(True, "yamada") == {"value": "③ 承認して直す", "interactive": True})
check("承認のチェックは承認ボタンの真上（同じ Column）にあり、チェックと名前が変わるたびにボタンを描き直す",
      re.search(r"with gr\.Column\(scale=2\):\n\s+pr_ok = gr\.Checkbox\(label=iv\.APPROVE_CHECK_LABEL.*\n\s+pr_approve = gr\.Button\(", web) is not None
      and "pr_ok.change(iv.approve_button, [pr_ok, pr_who], [pr_approve])" in web
      and "pr_who.change(iv.approve_button, [pr_ok, pr_who], [pr_approve])" in web)
check("状態は表示だけ日本語で、ラジオの値・一覧に渡す値は英語のまま（proposal_events の status と同じ）",
      set(iv.PROPOSAL_STATUS_JA) == set(proposals.STATUSES) | {"all"} and not hasattr(iv, "ANOMALY_STATUS_JA")
      and ("承認待ち", "pending") in iv.status_choices(iv.PROPOSAL_STATUS_JA)
      and "gr.Radio(iv.status_choices(iv.PROPOSAL_STATUS_JA), value=\"pending\"" in web
      and iv.proposal_table("pending")[0].endswith("件（承認待ち）") and iv.proposal_table("pending")[1][0]["状態"] == "承認待ち")
check("承認は「読んだ」のチェックが無ければ書かない", iv.decide_proposal("p1", "approved", "pending", "yamada", False)[1:] == (nothing, nothing) and decided == [])
check("proposal_id が空なら書かない", iv.decide_proposal("", "approved", "pending", "yamada", True)[1:] == (nothing, nothing) and decided == [])
r = iv.decide_proposal("p1", "approved", "pending", "  山田   太郎 ", True)
check("名前は空白を詰めて「<名前> (web)」で decided_by に残し、選択は空に戻す（続けて押しても別の行に書かない）",
      decided == [("p1", "approved", "山田 太郎 (web)")] and r[2] == ("update", {"choices": ["p1"], "value": None}))
rj = iv.decide_proposal("p1", "rejected", "pending", "x" * 100)
check(f"却下はチェック無しで通り、名前は {iv.APPROVER_MAX} 字で切る", decided[-1] == ("p1", "rejected", "x" * iv.APPROVER_MAX + " (web)"))
check("承認も却下も、押したら「送った。反映まで少し待つ」と出す（一覧は数秒〜20 秒「承認待ち」のまま。design.md §6）",
      all("送った" in str(x[0]) and "反映まで" in str(x[0]) for x in (r, rj)))
# 表の行を押したら、その行の proposal_id をプルダウンに入れる（2026-10-05 に AWS で、行を押しても選ばれず、プルダウンから選ぶしかなかった）。
# 番号（index）ではなく押した行の中身（row_value）で引く。表は 30 秒ごとに描き直され、並べ替えもできるので、番号は押した行とずれうる
_evt = lambda row, index=(0, 0): types.SimpleNamespace(row_value=row, index=list(index), value="")
check("行を押すと、その行の 1 列目（proposal_id）をプルダウンに入れる。index がずれていても row_value の行を使う",
      iv.select_proposal(_evt(["dc1-spine-01#link_down#ethernet-1/3.0#1", "承認待ち"], index=(1, 5))) == ("update", {"value": "dc1-spine-01#link_down#ethernet-1/3.0#1"}))
check("行の中身が取れない・1 列目が空なら、選択を変えない",
      iv.select_proposal(_evt(None)) == nothing and iv.select_proposal(_evt([])) == nothing and iv.select_proposal(_evt(["  ", "x"])) == nothing)
check("表の select をプルダウンにつなぎ、詳細と「読んだ」の外しはプルダウンの change が続ける（選び方が 2 つでも同じ道を通る）",
      "pr_table.select(iv.select_proposal, None, [pr_id])" in web and "def select_proposal(evt: gr.SelectData)" in incident
      and "pr_id.change(iv.proposal_detail, [pr_id], [pr_detail])" in web and "pr_id.change(lambda _: False, [pr_id], [pr_ok])" in web)

# ---- Temporal UI（8233）: 2026-09-24 のレビューではポートごとの SG ルールの抜けで UI が開かなかった。2026-09-29 からルールは土台の通信の表にあり、
#      Web の EC2 から workflow の 8233 の 1 行で送信と受信の 2 本ができる。7233〜7239 / 6933〜6939 は cycle 036 から workflow → workflow の自分宛てだけ
_sg_tf = read("IaC", "terraform", "aws-managed", "base", "core", "security_groups.tf")
_sg_rows = re.findall(r'\{ from = "(\w+)", to = "(\w+)", protocol = "tcp", port = (\d+)(?:, to_port = (\d+))?,', _sg_tf)
check("Temporal UI（8233）は土台の通信の表の web → workflow の 1 行で、workflow にはルールも Web の SG の参照も無く、"
      "Temporal の 7233〜7239 / 6933〜6939 の行は workflow → workflow の自分宛ての 2 行だけで、RDS へは workflow → nautobot_db の 5432（cycle 036）",
      re.search(r'\{ from = "web", to = "workflow", protocol = "tcp", port = 8233,', _sg_tf) is not None
      and sorted((f, t, int(a), int(b or a)) for f, t, a, b in _sg_rows if t == "workflow" and a != "8233")
      == [("workflow", "workflow", 6933, 6939), ("workflow", "workflow", 7233, 7239)]
      and ("workflow", "nautobot_db", "5432", "") in _sg_rows
      and "task_ui_from_web" not in tf and "web_to_task_ui" not in tf and "web_sg_id" not in tf and "instance_security_group_id" not in tf
      and 'output "security_group_ids"' in main_out and 'outputs.security_group_ids["workflow"]' in tf)
_wf_sg_desc = re.search(r'^    workflow\s+= "([^"]*)"$', _sg_tf, re.M)
check("workflow の SG の description に dev server が無く Temporal server, UI and worker がある（036 で temporalio/server + RDS にした。cycle 041）",
      _wf_sg_desc is not None and "dev server" not in _wf_sg_desc.group(1).lower() and "Temporal server, UI and worker" in _wf_sg_desc.group(1))
_td_desc = re.search(r'^    telegraf_dialout\s+= "([^"]*)"$', _sg_tf, re.M)
_tn_desc = re.search(r'^    telegraf_dialout_nlb\s+= "([^"]*)"$', _sg_tf, re.M)
check("telegraf_dialout の SG の description に MDT と syslog が無く SNMP traps があり（012 で syslog_ng、013 で gnmic に移った）、"
      "telegraf_dialout_nlb の description に syslog-ng と GoFlow2 がある（NLB の後ろは 3 つ。cycle 043）",
      _td_desc is not None and "MDT" not in _td_desc.group(1) and "syslog" not in _td_desc.group(1) and "SNMP traps" in _td_desc.group(1)
      and _tn_desc is not None and "syslog-ng" in _tn_desc.group(1) and "GoFlow2" in _tn_desc.group(1))
check("修復案の status に obsolete がある（tools.json の説明も）", "obsolete" in proposals.STATUSES and all("obsolete" in t["description"] for t in tools if t["name"] == "list_proposals"))
# ---- Nautobot の内部のシークレット（secret-key / admin-password / db-password）を tools の Lambda のロールで Deny する（cycle 044）。
# Web と Runtime の Deny は base/core（ロールを作るルート）。名前は ops/up-common.sh の ensure_nautobot_secrets と pipeline/nautobot の secret_parameters が正
_gw_tf = read("IaC", "terraform", "aws-managed", "workflow", "gateway.tf")
_wf_locals = read("IaC", "terraform", "aws-managed", "workflow", "locals.tf")
_gw_tools = _gw_tf.split('data "aws_iam_policy_document" "tools"')[1].split("\n}\n")[0] if 'data "aws_iam_policy_document" "tools"' in _gw_tf else ""
_gw_stmt = lambda sid: _gw_tools.split(f'sid       = "{sid}"')[1].split("\n  }")[0] if f'"{sid}"' in _gw_tools else ""
# DenyNautobotSecrets の statement { から } まで（字下げ 2 の閉じ括弧。中の condition { } は字下げ 4 なので含まれる）を丸ごと比べる。
# sid の前後に condition などを足して効かなくする変異を落とす（cycle 044 Round 3）。同じ sid を別の文書で上書きする override_policy_documents /
# source_policy_documents が無いこと、この文書を tools のロールに付けていることも見る
_gw_nb_blocks = [b for b in re.findall(r"^  statement \{\n(.*?)^  \}$", _gw_tools, re.M | re.S) if 'sid       = "DenyNautobotSecrets"' in b]
check("tools の Lambda のロールの文書（gateway.tf の tools）に DenyNautobotSecrets（sid・effect = Deny・actions = ssm:GetParameter*・resources = local.nautobot_secret_parameter_arns の 4 行だけで、"
      "condition などは無く、override_policy_documents / source_policy_documents で上書きしない）があり、aws_iam_role_policy.tools がこの文書を tools のロールに付け、"
      f"Parameters の Allow はそのまま（いま: {_gw_nb_blocks}）",
      _gw_nb_blocks == ['    sid       = "DenyNautobotSecrets"\n    effect    = "Deny"\n    actions   = ["ssm:GetParameter*"]\n'
                        '    resources = local.nautobot_secret_parameter_arns\n']
      and "override_policy_documents" not in _gw_tools and "source_policy_documents" not in _gw_tools
      and ('  role   = aws_iam_role.tools[0].name\n  policy = data.aws_iam_policy_document.tools.json\n}'
           in _gw_tf.split('resource "aws_iam_role_policy" "tools"')[-1].split("\nresource ")[0])
      and _gw_tools.count('effect    = "Deny"') == 1
      and 'actions   = ["ssm:GetParameter"]' in _gw_stmt("Parameters") and "effect" not in _gw_stmt("Parameters")
      and 'resources = ["arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.param_prefix}/*"]' in _gw_stmt("Parameters"))
_list_arns = lambda src: (lambda m: re.findall(r'^    "(.*)",$', m.group(1), re.M) if m else [])(
    re.search(r"^  nautobot_secret_parameter_arns = \[\n((?:    .*\n)*?)  \]$", src, re.M))
_wf_nb_arns = _list_arns(_wf_locals)
_wf_nb_head = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/*/nautobot/"
_wf_nb_block = re.search(r"^  nautobot_secret_parameter_arns = \[\n((?:    .*\n)*?)  \]$", _wf_locals, re.M)
check(f"workflow の locals.tf の nautobot_secret_parameter_arns は parameter/*/nautobot/ の secret-key / admin-password / db-password で、param_prefix を含まない（いま: {_wf_nb_arns}）",
      _wf_nb_arns == [_wf_nb_head + n for n in ("secret-key", "admin-password", "db-password")]
      and _wf_nb_block is not None and "param_prefix" not in _wf_nb_block.group(1) and "name_prefix" not in _wf_nb_block.group(1))
_core_nb_arns = _list_arns(read("IaC", "terraform", "aws-managed", "base", "core", "locals.tf"))
_lab_nb_arns = _list_arns(read("IaC", "terraform", "aws-managed", "pipeline", "lab", "locals.tf"))
_nb_up_names = re.findall(r'^  ensure_secret "/\$PREFIX/nautobot/([\w-]+)" ', read("ops", "up-common.sh"), re.M)
_nb_tf_locals = read("IaC", "terraform", "aws-managed", "pipeline", "nautobot", "locals.tf")
_nb_tf_names = re.findall(r'^    ([\w-]+)\s+= "/\$\{local\.name_prefix\}/nautobot/\1"', _nb_tf_locals.split("secret_parameters = {")[1].split("\n  }")[0], re.M) if "secret_parameters = {" in _nb_tf_locals else []
_nb_deny_names = lambda arns: [a.rsplit("/nautobot/", 1)[1] for a in arns]
check(f"Deny する 3 つの名前は、ops/up-common.sh の ensure_nautobot_secrets（{_nb_up_names}）と pipeline/nautobot の secret_parameters（{_nb_tf_names}）から api-token を除いたものと、"
      "base/core と workflow と pipeline/lab の 3 つで一致し、3 つの ARN のリストは同じ",
      len(_nb_up_names) == 4 and "api-token" in _nb_up_names and sorted(_nb_up_names) == sorted(_nb_tf_names)
      and sorted(_nb_deny_names(_wf_nb_arns)) == sorted(n for n in _nb_up_names if n != "api-token")
      and sorted(_nb_deny_names(_core_nb_arns)) == sorted(n for n in _nb_up_names if n != "api-token")
      and sorted(_nb_deny_names(_lab_nb_arns)) == sorted(n for n in _nb_up_names if n != "api-token")
      and _core_nb_arns == _wf_nb_arns == _lab_nb_arns)
_proposals_tf = read("IaC", "terraform", "aws-managed", "workflow", "proposals.tf")
_reader_doc = _proposals_tf.split('data "aws_iam_policy_document" "reader_access"')[1].split("\n}\n")[0] if 'data "aws_iam_policy_document" "reader_access"' in _proposals_tf else ""
check("proposals.tf の reader_access（Runtime と Web）は Parameters の Allow のままで Deny を持たない（Deny は base/core。cycle 044）",
      'sid       = "Parameters"' in _reader_doc and "Deny" not in _reader_doc
      and 'resources = ["arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.param_prefix}/*"]' in _reader_doc)

print(f"通過 {passed} / 失敗 0")
