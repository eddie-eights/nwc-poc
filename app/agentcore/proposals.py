"""修復案（S3 Tables の proposal_events。IaC/terraform/aws-managed/workflow のワーカーが書く）を画面に出し、人の承認・却下をワーカーへ送る。

2026-09-24 までは DynamoDB の表、2026-10-05 までは Neptune の頂点 proposal だった。いまの置き場は proposal_events だけで、
ワーカーが作成・承認・却下・適用・確認を 1 行ずつ足し、どの行も修復案の全項目を持つ（列は app/temporal/rules.py の PROPOSAL_EVENT_COLUMNS）。
修復案の「いま」は proposal_id ごとに seq が最大の行で、ここは Athena でそれを読む（toolkit.athena_rows。query_history と同じ作り）。
Athena の設定（ワークグループ・カタログ・namespace・テーブル）と決定のキューの URL は toolkit.Param（環境変数が先、無ければ SSM。
IaC/terraform/aws-managed/workflow の proposals.tf が置く）。どれかが無ければ「まだ配備されていない」を返して、WORKFLOW を作っていない構成でも落ちない。
修復案の項目: proposal_id（= <anomaly_id>#<first_seen>。発生ごとに 1 件。閉じて開き直した次の発生は別の修復案）, anomaly_id, device_id, kind, target,
first_seen（異常の発生時刻）, status（pending → approved / rejected（人）→ applied → verified / failed（ワーカー）、expired（時間切れ）、
obsolete（承認のあいだに異常が閉じた・開き直したので打たなかった））,
cause, action（heal-main / check / none）, command, precheck / precheck_verdict（処置を打つ前の孤立・冗長切れのチェック）, reason, agent_response, workflow_id,
created_at / updated_at（= その行の event_time）/ decided_at（epoch 秒）, decided_by, apply_output, verify_note。

承認・却下（decide）は行を書かない。決定のキュー（IaC/terraform/aws-managed/workflow の events.tf の decisions）に送り、worker がワークフローにシグナルを送る。
pending かどうかは送る前にも見るが（早く気づかせるため）、最後に決めるのはワークフロー（先に届いた 1 回だけが効く）。
チャットから承認させない（HITL）のは IAM とコードの両方で守る: 決定のキューに送れるのは Web の EC2 のロールだけ（Runtime には付けない）で、
decide をエージェントのツール（TOOL_SPECS）に出さない。
"""

import json
import time

from botocore.exceptions import BotoCoreError, ClientError

import toolkit

STATUSES = ("pending", "approved", "rejected", "applied", "verified", "failed", "expired", "obsolete")
QUERYABLE = STATUSES + ("all",)  # 一覧で指定できる値（all は全部）
DECISIONS = ("approved", "rejected")  # 人が決められるのはこの 2 つだけ
NOT_DEPLOYED = "修復案はまだ配備されていない（IaC/terraform/aws-managed/pipeline/analytics と IaC/terraform/aws-managed/workflow を apply すると使える）"

ATHENA_WORKGROUP = toolkit.Param("ATHENA_WORKGROUP", "athena-workgroup")
ATHENA_CATALOG = toolkit.Param("ATHENA_CATALOG", "athena-catalog")  # s3tablescatalog/<テーブルバケット>
HISTORY_NAMESPACE = toolkit.Param("HISTORY_NAMESPACE", "history-namespace")
PROPOSAL_EVENTS_TABLE = toolkit.Param("PROPOSAL_EVENTS_TABLE", "proposal-events-table")
DECISION_QUEUE_URL = toolkit.Param("DECISION_QUEUE_URL", "decision-queue-url")
TIMEOUT = 20
POLL = 0.5  # Athena のクエリの状態を見に行く間隔（秒）

# 列は app/temporal/rules.py の PROPOSAL_EVENT_COLUMNS と同じ順（tests/test_app.py が突き合わせる）。時刻の列は epoch 秒に直す
COLUMNS = ("event_id", "proposal_id", "anomaly_id", "seq", "event", "status", "device_id", "kind", "target", "first_seen",
           "source", "alert_detail", "cause", "action", "command", "reason", "agent_response", "precheck", "precheck_verdict",
           "decided_by", "decided_at", "apply_output", "verify_note", "detail", "workflow_id", "run_id", "created_at", "event_time")
TIME_COLUMNS = ("first_seen", "decided_at", "created_at", "event_time")


def _settings() -> tuple:
    """(ワークグループ, カタログ, namespace, テーブル)。どれかが空なら None（未配備）"""
    values = (ATHENA_WORKGROUP.value(), ATHENA_CATALOG.value(), HISTORY_NAMESPACE.value(), PROPOSAL_EVENTS_TABLE.value())
    return values if all(values) else None


def proposals_sql(catalog: str, namespace: str, table: str, by_id: bool = False, by_device: bool = False, by_status: bool = False,
                  limit: int = 100) -> str:
    """proposal_id ごとに最新の行（seq が最大。同じ seq が再試行で 2 つあれば event_time が遅いほう。rules.latest_proposals と同じ並べ方）を、
    新しい順（event_time）に最大 limit 件。値は ? にして ExecutionParameters で渡す（SQL に埋め込まない）。? の順は proposal_id、device_id、status。
    proposal_id と device_id は修復案のどの行でも同じなので内側で絞り、status は最新の行のものなので外側で絞る"""
    inner = [c for c, on in (("proposal_id = ?", by_id), ("device_id = ?", by_device)) if on]
    cols = ", ".join(COLUMNS)
    return (f"SELECT {cols} FROM (SELECT {cols}, row_number() OVER (PARTITION BY proposal_id ORDER BY seq DESC, event_time DESC) AS rn "
            f'FROM "{catalog}"."{namespace}"."{table}"' + (f" WHERE {' AND '.join(inner)}" if inner else "") + ") "
            f"WHERE rn = 1{' AND status = ?' if by_status else ''} ORDER BY event_time DESC LIMIT {int(limit)}")


def _row(cells: list) -> dict:
    """Athena の 1 行（COLUMNS の順のセル）を、画面とツールに返す辞書にする（今までの頂点と同じキー）。
    updated_at はその行の event_time、detail はアラートの detail（行ごとの detail は event_detail に移す）"""
    r = dict(zip(COLUMNS, cells))
    for k in TIME_COLUMNS:
        r[k] = toolkit.athena_epoch(r.get(k)) or None
    try:
        r["seq"] = int(r.get("seq") or 0)
    except (TypeError, ValueError):
        r["seq"] = 0
    for k in COLUMNS:
        if r.get(k) is None and k not in TIME_COLUMNS:
            r[k] = ""
    r["event_detail"] = r.pop("detail", "")
    r["detail"] = r.get("alert_detail", "")
    r["updated_at"] = r.get("event_time")
    return _decorate(r)


def _decorate(p: dict) -> dict:
    """epoch 秒の項目に、読める形（JST）を並べて足す"""
    for k in ("created_at", "updated_at", "decided_at", "first_seen"):
        p[f"{k}_jst"] = toolkit.jst(p.get(k))
    return p


def _query(by_id: str = "", device_id: str = "", status: str = "", limit: int = 100) -> tuple[list, str]:
    """(修復案の list, エラーの文言)。未配備なら NOT_DEPLOYED"""
    settings = _settings()
    if settings is None:
        return [], NOT_DEPLOYED
    workgroup, catalog, namespace, table = settings
    params = [v for v in (by_id, device_id, status) if v]
    sql = proposals_sql(catalog, namespace, table, by_id=bool(by_id), by_device=bool(device_id), by_status=bool(status), limit=limit)
    # proposal_id のために広い検査で渡す。device_id は list_proposals が ATHENA_PARAM_RE で、status は QUERYABLE で先に確かめている
    cells_list, error = toolkit.athena_rows(sql, workgroup, params, max_rows=limit, timeout=TIMEOUT, poll=POLL,
                                            param_re=toolkit.ATHENA_TEXT_RE)
    if error:
        return [], error
    return [_row(cells) for cells in cells_list], ""


def list_proposals(status: str = "pending", limit: int = 50, device_id: str = "") -> dict:
    """status の修復案を新しい順に（最新の行の event_time = updated_at）。status が all なら全部。device_id があればその機器だけ（クエリの中で絞る）"""
    status = status if status in QUERYABLE else "pending"
    limit = max(1, min(int(limit), 100))
    device_id = str(device_id or "").strip()
    if device_id and not toolkit.ATHENA_PARAM_RE.match(device_id):
        return {"error": "device_id に使えない文字がある（英数字と . _ : / # ? - だけ）", "proposals": []}
    items, error = _query(device_id=device_id, status="" if status == "all" else status, limit=limit)
    if error:
        return {"error": error if error == NOT_DEPLOYED else f"修復案を読めない: {error}", "proposals": []}
    return {"status": status, "count": len(items), "proposals": items}


def get_proposal(proposal_id: str) -> dict:
    """1 件だけ引く（画面の承認タブが、詳細を出すときと決める直前に使う）。無い・読めない・未配備なら空の辞書"""
    proposal_id = str(proposal_id or "").strip()
    if not proposal_id or not toolkit.ATHENA_TEXT_RE.match(proposal_id):
        return {}
    items, error = _query(by_id=proposal_id, limit=1)
    return items[0] if items and not error else {}


def decide(proposal_id: str, decision: str, decided_by: str = "web") -> dict:
    """pending の修復案への承認・却下を決定のキューに送る。pending でなければ送らない（誰かが先に決めた・ワーカーが進めた）。
    行は書かない（ワーカーがシグナルを受けて approved / rejected の行を足す。画面に出るまで数秒〜20 秒）"""
    queue_url = DECISION_QUEUE_URL.value()
    if not queue_url or _settings() is None:
        return {"error": NOT_DEPLOYED}
    if decision not in DECISIONS:
        return {"error": f"decision は {' / '.join(DECISIONS)} のどれか"}
    proposal_id = str(proposal_id or "").strip()
    if not proposal_id:
        return {"error": "proposal_id が空"}
    if not toolkit.ATHENA_TEXT_RE.match(proposal_id):
        return {"error": "proposal_id に使えない文字がある（' と制御文字。長さは 1000 文字まで）"}
    items, error = _query(by_id=proposal_id, limit=1)
    if error:
        return {"error": f"修復案を読めない: {error}"}
    if not items or items[0].get("status") != "pending":
        return {"error": f"{proposal_id} は pending ではない（先に決まったか、ワーカーが進めた）"}
    now = int(time.time())
    decided_by = str(decided_by or "")[:64]
    body = {"type": "decision", "proposal_id": proposal_id, "decision": decision, "decided_by": decided_by, "sent_at": now}
    try:
        toolkit.client("sqs").send_message(QueueUrl=queue_url, MessageBody=json.dumps(body, ensure_ascii=False))
    except (ClientError, BotoCoreError) as e:
        return {"error": f"送れない: {toolkit.brief_error(e)}"}
    return {"proposal_id": proposal_id, "status": "sent", "decision": decision, "decided_by": decided_by, "sent_at": now}


# ---------------------------------------------------------------- エージェントのツール（読むだけ）
# 承認・却下（decide）はツールにしない。人が画面の承認タブで決めるのが HITL の線で、チャットからは決めさせない（2026-09-18）
def _tool_list_proposals(status: str = "all", limit: int = 20, device_id: str = "") -> dict:
    """list_proposals の既定値だけを変えたもの。画面は pending が既定だが、チャットで聞かれるのはたいてい履歴なので all"""
    return list_proposals(status=status, limit=limit, device_id=device_id)


TOOL_SPECS = [
    {"toolSpec": {
        "name": "list_proposals",
        "description": "AI が出した修復案と、その後の履歴（状態、原因、打ったコマンド、決めた人、実行結果、確認結果）。"
                       "「修復履歴は」「何を直した」「承認待ちは」と聞かれたらこれを呼ぶ。"
                       "状態は pending（承認待ち）→ approved / rejected（人が決めた）→ applied（実行した）→ verified（直ったのを確かめた）/ failed、expired（時間切れ）、obsolete（承認のあいだに異常が閉じたので打たなかった）。"
                       "承認や却下はこのツールではできない（人が画面の承認タブで決める）。",
        "inputSchema": {"json": {"type": "object", "properties": {
            "status": {"type": "string", "description": "all（全部、既定）か pending / approved / rejected / applied / verified / failed / expired / obsolete のどれか"},
            "limit": {"type": "integer", "description": "件数の上限（既定 20、最大 100）"},
            "device_id": {"type": "string", "description": "機器名（例 dc1-leaf-01）で絞る。空なら全機器"},
        }}},
    }},
]
TOOLS = {"list_proposals": _tool_list_proposals}
run_tool = toolkit.runner(TOOLS)
