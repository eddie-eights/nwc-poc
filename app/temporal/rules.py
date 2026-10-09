"""ワーカーの「判断」の部分。AWS にも Temporal にも触らない純粋な関数だけを置く。

ここにあるのは 8 つ:
  - build_prompt        エージェント（AgentCore Runtime）に投げる質問文
  - parse_agent_json    返ってきた文から JSON を取り出す
  - normalize_action    lab EC2 で打ってよいコマンドの許可リスト
  - alerts_from_message / should_start / maintenance_hold / workflow_id / proposal_id  アラート（SNS → SQS）の読み取りと、どれでワークフローを起こすかの判定と id
  - proposal_event      修復案（S3 Tables の proposal_events）の 1 行。どの行も全項目を持つ
  - decision_from_message / anomaly_of  Web の承認・却下（決定のキュー）の読み取りと、送る先のワークフローの id
  - alert_event         アラートの通知の履歴（S3 Tables の alert_events）の 1 行
  - impact / precheck   処置を打つ前の事前チェック（その処置でグラフがどう変わり、孤立や冗長切れが出るか）

AWS も Temporal も要らないので、tests/test_workflow.py はこのファイルの関数を直接呼んで確かめられる。
逆に言うと、ここに boto3 や temporalio を持ち込むとテストが動かなくなる。入れない。
"""

import json
import re
from datetime import datetime, timedelta, timezone

# lab EC2 で打ってよいのはこれだけ（app/containerlab/lab.sh のサブコマンド）。エージェントが他を言っても none 扱いにする
ALLOWED_ACTIONS = {"heal-main": "sudo lab heal-main", "check": "sudo lab check"}
NO_ACTION = "none"
JST = timezone(timedelta(hours=9))


def jst(epoch) -> str:
    """epoch 秒を日本時間の「2026-09-18 12:34:56」に（app/agentcore/toolkit.py の jst と同じ形）。空や 0 なら空文字"""
    return datetime.fromtimestamp(int(epoch), JST).strftime("%Y-%m-%d %H:%M:%S") if epoch else ""


def build_prompt(anomaly: dict) -> str:
    """エージェントに投げる質問。答えは JSON 1 個だけにさせる"""
    return (
        "あなたはネットワーク運用の一次切り分け担当です。次の異常について、ツールで状況を確かめてから、原因と処置を JSON で 1 つだけ返してください。"
        "まず root_cause（Neptune。UP でない要素を層をまたいで下へ辿り、根本原因ごとにまとめる）で、この異常が根本原因なのか、別の原因の結果なのかを確かめてください。"
        "トポロジと影響範囲は neighbors / blast_radius（Neptune。回線や機器の status が DOWN / ALARM なら他にも落ちている）、その機器のログは search_logs（OpenSearch）、"
        "メトリクスの推移は query_metrics（Prometheus）、アラートの履歴は query_history、直前の構成変更は recent_changes（Nautobot の変更履歴）で見て、見えた事実だけを根拠に原因を書いてください。"
        "説明文や Markdown は付けないでください。\n"
        f"異常: device_id={anomaly.get('device_id', '')} kind={anomaly.get('kind', '')} target={anomaly.get('target', '')} "
        f"detail={anomaly.get('detail', '')} first_seen_jst={jst(anomaly.get('first_seen'))}\n"
        '返す形: {"cause": "原因（日本語 1〜2 文）", "action": "heal-main | check | none", "reason": "その処置を選んだ理由"}\n'
        "action は、Leaf dc1-a-leaf-01 の ethernet-1/1（dc1-spine-01 との fabric）が落ちている（link_down か、その上の isis_down）なら heal-main、状況を見るだけでよいなら check、"
        "人が別の手で直すべきなら none。"
    )


def parse_agent_json(text: str) -> dict:
    """応答の中の最初の {...} を JSON として読む。読めなければ action=none で理由に生の文を入れる"""
    text = text or ""
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return {
                    "cause": str(data.get("cause", ""))[:1000],
                    "action": str(data.get("action", NO_ACTION)).strip(),
                    "reason": str(data.get("reason", ""))[:1000],
                }
        except ValueError:
            pass
    return {"cause": "", "action": NO_ACTION, "reason": text[:1000]}


def normalize_action(action: str) -> tuple[str, str]:
    """(action, command)。許可リストに無いものは none（コマンド空）"""
    action = (action or "").strip()
    if action in ALLOWED_ACTIONS:
        return action, ALLOWED_ACTIONS[action]
    return NO_ACTION, ""


# ---------------------------------------------------------------- 事前チェック（処置を打つ前に、孤立と冗長切れを見る。2026-10-04）
# 処置がトポロジをどう変えるか（app/containerlab/lab.sh のサブコマンドの中身）。処置を ALLOWED_ACTIONS に足すときはここにも足す（無い処置は「確認できず」になる）。
# いまの 2 つは dc1-a-leaf-01 の ethernet-1/1 を上げる（heal-main）か見るだけ（check）。落とす処置（機器の再起動・回線の切り離し）を足したときに効く。
ACTION_CHANGES = {"heal-main": [{"op": "link_up", "target": "dc1-a-leaf-01#ethernet-1/1"}], "check": []}
PRECHECK_JA = {"ok": "問題なし", "warn": "注意", "danger": "危険", "unknown": "確認できず"}
# 端の役割（つながりの中継にしない機器）。app/agentcore/topology.py の END_ROLES と同じ
END_ROLES = ("trex",)


def impact(devices: list, links: list, changes: list) -> dict:
    """回線・機器を落とした / 上げたと仮定して、孤立する機器と冗長が切れる機器を出す（修復を打つ前の事前チェック）。
    devices = [{device_id, status, role}]、links = [{a, a_if, b, b_if, status}]、changes = [{op, target}]。
    op は link_down / link_up（target = <機器>#<IF>。どちらの端でもよい）か device_down / device_up（target = 機器名）。
    つながりは DOWN でない回線と機器だけで見て、変更前のかたまりのうち、変更後にいちばん大きい断片から外れた機器を「孤立」とする。同点ならどれも本流にしない
    （つながり直す機器は、変更後の本流にいて、変更前はその中のいちばん大きい断片にいなかった機器。断片が同点なら変更前に次数 0 だった機器だけ。isolated_after は変更後のいちばん大きいかたまりに入っていない機器）。
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
    # W が同点で決まらないときは、変更前に次数 0 だった（孤立か DOWN の）機器だけ（同点のかたまりどうしをつないでも全部が載らない）。
    # 変更前の本流と比べないのは、回線を落として別のかたまりが本流に繰り上がったとき、そのかたまりを「つながり直す」に載せないため
    main1 = largest(comps1)
    w0 = largest([main1 & c for c in comps0])
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


def precheck(action: str, devices: list, links: list) -> dict:
    """{verdict, text}。処置（normalize_action を通したもの）をいまのトポロジに重ねた結果。none なら空"""
    if action == NO_ACTION:
        return {"verdict": "", "text": ""}
    if action not in ACTION_CHANGES:
        return {"verdict": "unknown", "text": f"【{PRECHECK_JA['unknown']}】処置 {action} がトポロジをどう変えるかが決めていない（rules.ACTION_CHANGES）"}
    changes = ACTION_CHANGES[action]
    if not changes:
        return {"verdict": "ok", "text": f"【{PRECHECK_JA['ok']}】状況を見るだけの処置で、回線も機器も変えない"}
    r = impact(devices, links, changes)
    what = "、".join(f"{c['target']} を{ {'link_up': '上げる', 'link_down': '落とす', 'device_up': '上げる', 'device_down': '落とす'}[c['op']] }" for c in changes)
    return {"verdict": r["verdict"], "text": f"【{PRECHECK_JA[r['verdict']]}】{what}と仮定: {r['summary']}"[:1000]}


# ---------------------------------------------------------------- アラート（Grafana / Splunk → SNS → SQS）
# SNS に publish する JSON は送り手（app/grafana/provisioning/alerting と app/splunk/nwc_alerts）で形を揃えてある:
#   {"source": "grafana" | "splunk",
#    "alerts": [{"status": "firing" | "resolved", "device_id": "dc1-a-leaf-01", "kind": "link_down", "target": "ethernet-1/1",
#                "detail": "ethernet-1/1 is down", "starts_at": 1790000000}]}
# 同じ形を app/graph/status_handler.py（トポロジの status を書く Lambda）も読む。形を変えるときは 4 か所を一緒に変える
ALERT_STATUSES = ("firing", "resolved")
# ワークフローを起こす異常の種類。trap（linkDown / linkUp 以外の通知）は「見えた」印で、打つ処置も無いので起こさない
START_KINDS = {"link_down"}
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def device_name(name) -> str:
    """機器名をトポロジの device_id（小文字の短い名前）に揃える。FQDN はドメインを落とす（IPv4 はそのまま）"""
    name = str(name or "").strip().lower()
    return name if IPV4_RE.match(name) else name.split(".", 1)[0]


def anomaly_id(device_id: str, kind: str, target: str) -> str:
    """異常の id。送り手が違っても同じ機器・種類・対象なら同じ id（Splunk と Grafana の両方が同じ障害を知らせても 1 つにまとまる）"""
    return f"{device_id}#{kind}#{target}"


def _epoch(v) -> int:
    try:
        return max(int(float(v or 0)), 0)
    except (TypeError, ValueError, OverflowError):   # OverflowError: 1e400 / "inf" / "Infinity"（float が無限大になる）
        return 0


def _payload(body: str) -> dict | None:
    """メッセージ本文の JSON（{"source", "alerts": [...]}）。読めない・alerts が list でなければ None。SNS の封筒は開ける"""
    try:
        data = json.loads(body or "")
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("Type") == "Notification" and isinstance(data.get("Message"), str):
        try:
            data = json.loads(data["Message"])
        except ValueError:
            return None
    if not isinstance(data, dict) or not isinstance(data.get("alerts"), list):
        return None
    return data


def alert_count(body: str) -> int:
    """本文の alerts に入っている要素の数（形の合わないものも数える）。alerts_from_message の戻りの長さとの差が、捨てた件数"""
    data = _payload(body)
    return len(data["alerts"]) if data else 0


def alerts_from_message(body: str, now: int = 0) -> list:
    """SQS のメッセージ本文（SNS に publish された JSON そのまま。購読は raw message delivery）からアラートの list を取る。
    1 件 = {anomaly_id, device_id, kind, target, detail, status, first_seen, source}。first_seen は starts_at（無ければ now）。
    読めない本文は []、形の合わない要素（機器か種類が無い・status が firing / resolved でない）は捨てる。
    raw でない配り方（{"Type": "Notification", "Message": "…"} の封筒）でも中身を読む"""
    data = _payload(body)
    if data is None:
        return []
    out = []
    for a in data["alerts"]:
        if not isinstance(a, dict):
            continue
        dev, kind = device_name(a.get("device_id")), str(a.get("kind") or "").strip()
        target, status = str(a.get("target") or "").strip(), str(a.get("status") or "").strip().lower()
        if not dev or not kind or status not in ALERT_STATUSES:
            continue
        out.append({
            "anomaly_id": anomaly_id(dev, kind, target), "device_id": dev, "kind": kind, "target": target,
            "detail": str(a.get("detail") or "")[:1000], "status": status,
            "first_seen": _epoch(a.get("starts_at")) or int(now or 0), "source": str(data.get("source") or "")[:40],
        })
    return out


def should_start(alert: dict, existing: dict | None) -> bool:
    """firing の link_down で、同じ発生（anomaly_id + first_seen）の修復案がまだ無ければ起こす。
    Grafana は同じ発生の通知を repeat_interval ごとに同じ starts_at で送り直すので、修復案があればその発生はもう扱っている"""
    if not alert.get("anomaly_id") or alert.get("kind") not in START_KINDS or alert.get("status") != "firing":
        return False
    return not existing


def maintenance_hold(alert: dict, devices: list, links: list) -> list:
    """このアラートに関わる保守中の機器（Nautobot で Status を Maintenance にしたもの。Neptune の maintenance = true）。空でなければワークフローを起こさない。
    見るのはアラートの機器と、target のインタフェースにつながる回線の相手（相手を保守で止めても、こちらの回線が落ちる）"""
    dev, target = alert.get("device_id", ""), alert.get("target", "")
    involved = {dev}
    for l in links:
        if (l.get("a"), l.get("a_if")) == (dev, target):
            involved.add(l.get("b"))
        elif (l.get("b"), l.get("b_if")) == (dev, target):
            involved.add(l.get("a"))
    return sorted(d["device_id"] for d in devices if d.get("maintenance") and d["device_id"] in involved)


def workflow_id(anomaly_id: str) -> str:
    """ワークフローの id は異常ごと（発生の時刻を入れない）。送り手ごとに starts_at が違うので、入れると Splunk と Grafana の
    同じ障害が別のワークフローになる。同じ id が走っているあいだ、Temporal が二重起動を弾く"""
    return f"investigate-{anomaly_id}"


def proposal_id(anomaly_id: str, first_seen) -> str:
    """修復案の id は発生ごと。anomaly_id だけだと、閉じて開き直した次の発生が前の修復案に重なる（2026-09-24）"""
    return f"{anomaly_id}#{int(first_seen or 0)}"


# ---------------------------------------------------------------- 修復案（S3 Tables の proposal_events。2026-09-24、全項目の行にしたのは 2026-10-05）
# 修復案の置き場はこのテーブルだけ（Neptune の頂点 proposal は 2026-10-05 にやめた）。どの行も修復案の全項目を持ち、
# 修復案の「いま」は proposal_id ごとに seq が最大の行（event_time は秒なので順番に使わない）。
# 列は IaC/terraform/aws-managed/pipeline/analytics/tables.tf の proposal_events と同じ順・同じ型。時刻は epoch 秒で組み、awsio が書くときに tz 付きにする
PROPOSAL_EVENT_COLUMNS = (
    ("event_id", "string"), ("proposal_id", "string"), ("anomaly_id", "string"), ("seq", "int"), ("event", "string"),
    ("status", "string"), ("device_id", "string"), ("kind", "string"), ("target", "string"), ("first_seen", "timestamptz"),
    ("source", "string"), ("alert_detail", "string"), ("cause", "string"), ("action", "string"), ("command", "string"),
    ("reason", "string"), ("agent_response", "string"), ("precheck", "string"), ("precheck_verdict", "string"),
    ("decided_by", "string"), ("decided_at", "timestamptz"), ("apply_output", "string"), ("verify_note", "string"),
    ("detail", "string"), ("workflow_id", "string"), ("run_id", "string"), ("created_at", "timestamptz"), ("event_time", "timestamptz"),
)
# created は pending で置いたとき。ignored は効かなかった決定（status は変えない）。ほかは status の移り変わりそのもの
PROPOSAL_EVENTS = ("created", "approved", "rejected", "expired", "obsolete", "applied", "failed", "verified", "ignored")
DECISIONS = ("approved", "rejected")
DECISION_JA = {"approved": "承認", "rejected": "却下"}
TEXT_MAX = 4000  # 文字列の列は 1 項目この字数で切る（agent_response が長い。Temporal の受け渡しと行の大きさを抑える）
# 行ごとに決まる列（ほかの列は修復案の項目で、前の行から持ち越す）
_EVENT_ONLY = ("event_id", "event", "status", "seq", "detail", "event_time")


def proposal_event(event: str, proposal: dict, now: int, detail: str = "", fields: dict | None = None) -> dict:
    """proposal_events の 1 行（全項目）。proposal は修復案の辞書（前の行そのものでよい）で、fields をその上に重ねる。
    seq は created が 1、ほかは proposal の seq + 1。status は created が pending、ignored が proposal のまま、ほかは event と同じ。
    event_id = <proposal_id>#<event>（1 つの修復案で同じ出来事は 1 回だけ。アクティビティの再試行で二重に入ったら event_id で重複を落とす。
    ignored だけは 1 つの修復案に何度もありうるので ignored_event が付け直す）"""
    if event not in PROPOSAL_EVENTS:
        raise ValueError(f"unknown proposal event: {event}")
    p = {**proposal, **(fields or {})}
    pid = str(p.get("proposal_id") or "")
    row = {}
    for name, typ in PROPOSAL_EVENT_COLUMNS:
        if name in _EVENT_ONLY:
            continue
        v = p.get(name)
        if typ == "timestamptz":
            row[name] = _epoch(v) or None
        else:
            row[name] = str(v if v is not None else "")[:TEXT_MAX]
    row.update({
        "event_id": f"{pid}#{event}", "event": event,
        "status": "pending" if event == "created" else str(p.get("status") or "") if event == "ignored" else event,
        "seq": 1 if event == "created" else _seq(p.get("seq")) + 1, "detail": str(detail or "")[:TEXT_MAX], "event_time": int(now),
    })
    return {name: row[name] for name, _ in PROPOSAL_EVENT_COLUMNS}


def decision_key(decision: dict) -> tuple:
    """決定の中身（decision, decided_by, decided_at）。同じなら同じ決定（SQS の重複配達）として 1 つに扱う"""
    return (str(decision.get("decision") or ""), str(decision.get("decided_by") or "").strip()[:64], _epoch(decision.get("decided_at")))


def ignored_event(proposal: dict, decision: dict, effective: dict, now: int) -> dict:
    """効かなかった決定（先に effective が効いたあとで届いた、中身の違う decision）の行。status とほかの項目（効いた決定の
    decided_by / decided_at を含む）は proposal（直前の行）のまま、seq だけ進め、detail に効かなかった決定を書く。
    event_id = <proposal_id>#ignored#<decision>#<decided_at の epoch 秒>#<decided_by>（同じ人が同じ秒に承認と却下を送っても別の行）"""
    kind, by, at = decision_key(decision)
    detail = (f"{DECISION_JA.get(kind, kind)}（{by or '-'}、{jst(at) or '-'}）が届いたが、"
              f"先に{DECISION_JA.get(effective.get('decision'), str(effective.get('decision') or '-'))}が決まっていた")
    row = proposal_event("ignored", proposal, now, detail)
    row["event_id"] = f"{row['proposal_id']}#ignored#{kind}#{at}#{by}"
    return row


def _seq(v) -> int:
    try:
        return max(int(v or 0), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def latest_proposals(rows: list) -> dict:
    """proposal_events の行の list を {proposal_id: 最新の行} にする。最新は seq が最大の行（同じ seq が再試行で 2 つあれば event_time が遅いほう。
    app/agentcore/proposals.py の Athena のクエリと同じ並べ方）"""
    out = {}
    for r in rows:
        pid = str(r.get("proposal_id") or "")
        cur = out.get(pid)
        if cur is None or (_seq(r.get("seq")), _epoch(r.get("event_time"))) > (_seq(cur.get("seq")), _epoch(cur.get("event_time"))):
            out[pid] = r
    return out


def anomaly_of(proposal_id: str) -> str:
    """proposal_id（<anomaly_id>#<first_seen>）から anomaly_id を出す（右端の # から後ろを外す）"""
    return str(proposal_id or "").rsplit("#", 1)[0]


def decision_from_message(body: str, now: int = 0) -> dict | None:
    """決定のキュー（<prefix>-decisions。Web の承認タブが送る）のメッセージ本文を、シグナル decide に渡す辞書にする。
    本文は {"type": "decision", "proposal_id", "decision", "decided_by", "sent_at"}。読めない・type が decision でない・
    decision が approved / rejected でない・proposal_id が <anomaly_id>#<first_seen> の形でないときは None。decided_at は sent_at（無ければ now）"""
    try:
        data = json.loads(body or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("type") != "decision":
        return None
    pid, decision = str(data.get("proposal_id") or "").strip(), str(data.get("decision") or "").strip()
    if decision not in DECISIONS or "#" not in pid or not anomaly_of(pid):
        return None
    return {"proposal_id": pid, "decision": decision, "decided_by": str(data.get("decided_by") or "").strip()[:64],
            "decided_at": _epoch(data.get("sent_at")) or int(now or 0)}


# ---------------------------------------------------------------- アラートの通知の履歴（S3 Tables の alert_events。2026-10-04）
# 書くのは IaC/terraform/aws-managed/pipeline/graph の status Lambda（app/graph/status_handler.py）で、Firehose → S3 Tables。読むのはエージェントの query_history（Athena）。
# 列は IaC/terraform/aws-managed/pipeline/analytics/tables.tf の alert_events と同じ順・同じ型。時刻は ISO 8601 の UTC で送り、Firehose が timestamptz にする
ALERT_EVENT_COLUMNS = (
    ("event_id", "string"), ("anomaly_id", "string"), ("source", "string"), ("status", "string"), ("device_id", "string"),
    ("kind", "string"), ("target", "string"), ("detail", "string"), ("starts_at", "timestamptz"), ("received_at", "timestamptz"),
)


def iso_utc(epoch) -> str:
    """epoch 秒を Firehose に渡す UTC の時刻「2026-10-04T07:00:00.000000Z」に"""
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def alert_event(alert: dict, received_at) -> dict:
    """alert_events の 1 行（alerts_from_message の 1 件 = 届いた通知 1 件）。重複は落とさない（Grafana の送り直しも、両方の送り手から来た分も行にする）。
    event_id = <anomaly_id>#<source>#<status>#<starts_at の epoch 秒>。Lambda のやり直しで二重に入った分は、読む側が event_id で落とす。
    starts_at の意味は送り手で違う（Grafana は発火した時刻のまま resolved も来る。Splunk はその状態の latest(_time)）。無ければ 0 で、列は空"""
    starts_at = int(alert.get("first_seen") or 0)
    status, source = str(alert.get("status") or ""), str(alert.get("source") or "")
    return {
        "event_id": f"{alert.get('anomaly_id') or ''}#{source}#{status}#{starts_at}", "anomaly_id": str(alert.get("anomaly_id") or ""),
        "source": source, "status": status, "device_id": str(alert.get("device_id") or ""), "kind": str(alert.get("kind") or ""),
        "target": str(alert.get("target") or ""), "detail": str(alert.get("detail") or ""),
        "starts_at": iso_utc(starts_at) if starts_at else None, "received_at": iso_utc(received_at),
    }
