"""WORKFLOW のワーカー（Temporal on ECS Fargate）。
Grafana と Splunk のアラート（SNS → SQS）を受けてエージェントに原因を調べさせ、修復案を出し、人が承認したら lab EC2 で直し、解消の通知が来るまで確かめる。

流れ: Grafana のアラートルール / Splunk の保存済みサーチが異常を見つける → SNS のトピック（<接頭辞>-alerts）→ SQS → ここ（Temporal のワークフロー）が
エージェントに Neptune / S3 / OpenSearch / Prometheus を見させて原因分析 → 修復の提案 → 人間の承認 → Temporal で実行。
同じタスクの中の temporal コンテナ（temporal-server。履歴は Nautobot の RDS for PostgreSQL。cycle 036）に localhost:7233 でつなぐ。
1 プロセスで 3 つを動かす:
  - starter（アラート）: SQS（ANOMALY_QUEUE_URL）を long polling（20 秒）し、届いたアラート（rules.alerts_from_message）ごとに
      firing   → investigate-<anomaly_id> のワークフローを起こす（起こすのは rules.START_KINDS（link_down）だけ）。
                 同じ id が走っていれば Temporal が二重起動を弾く（Splunk と Grafana が同じ障害を知らせても 1 つ。繰り返しの通知も同じ）
      resolved → そのワークフローにシグナル resolved を送る（走っていなければ何もしない）
    決定（type が decision）はここでは読まない（読めないメッセージとして消す）。このキューには SNS に publish できる Grafana と Splunk も送れる
  - starter（決定）: SQS（DECISION_QUEUE_URL。Web の承認タブだけが送れる）を同じ作りで待ち、承認・却下（rules.decision_from_message）を
    investigate-<anomaly_id> にシグナル decide で送る。ワークフローが走っていなければ、修復案がまだ pending なら expired にする
    どちらも、メッセージは処理し終えた・処理する理由が無い・もう起きている（WorkflowAlreadyStartedError）・送る相手がいない、のどれかなら消し、
    それ以外の失敗（Temporal や S3 Tables や Neptune に届かない）なら消さずに残して、可視性タイムアウトのあとで配り直させる
  - worker: ワークフロー InvestigateAnomaly とアクティビティを回す

異常の「いま」を置く場所は持たない（2026-10-02。Neptune はトポロジと status だけ）。発生はワークフローそのもの、解消はシグナルで持つ。
ワークフローの段: investigate（AgentCore Runtime に JSON で答えさせる）
  → put_proposal（created の行。proposal_id = <anomaly_id>#<first_seen>。同じ異常の古い承認待ちは先に expired にする）
  → 人の判断をシグナル decide で待つ（Web の「承認」タブ → 決定のキュー → starter）。先に届いた 1 回だけが効き、
    自分の proposal_id でないもの（同じ異常の前の発生への決定）は無視する。待つあいだに解消の通知が来たら、打つ相手がもういないので obsolete にして終わる
    効いたあとに届いた中身の違う決定は、効かなかった決定として ignored の行にする（status は変えない。2026-10-05）。
    承認待ちが決定なしで終わったあと（expired / obsolete）に届いた決定は、行にせずログだけ
  → 承認・却下の行（decided_by は Web で名乗った名前）
  → approved なら、打つ直前にもう一度、解消していないか確かめる（していれば obsolete にして打たない）
  → apply_on_lab（SSM Run Command で `sudo lab <cmd>`。cmd は rules.ALLOWED_ACTIONS だけ）→ applied / failed
  → verify（解消の通知を VERIFY_TIMEOUT 秒まで待つ）→ verified / failed
  APPROVAL_TIMEOUT_MINUTES 過ぎたら expired。rejected なら何もしない。
  rejected / expired / failed で終わるときは、解消の通知が来るまで（長くて HOLD_MINUTES 分）ワークフローを閉じない。
  閉じると、まだ直っていない同じ異常の次の通知（Grafana の repeat、Splunk の次のサーチ）がもう一度調査を起こす。
修復案の置き場は S3 Tables の proposal_events だけ（2026-10-05 に Neptune の頂点 proposal をやめた）。書くのはこのワーカーだけで、
段が進むたびに修復案の全項目を持つ行を 1 行足す（rules.proposal_event。seq が 1 ずつ増え、最大の行が「いま」）。
Web とエージェントは Athena で読むので、Temporal を知らなくてよい。Temporal の履歴は Nautobot の RDS にあるが（cycle 036）、
RDS は ops/down.sh で消えるので、修復案はこちらに残す。行を足す前に落ちたらアクティビティの再試行で足し直す（二重に入ったら読む側が seq で 1 つにする）。

3 ファイルに分けてある（同じディレクトリに置いて import する。Dockerfile は app/temporal/*.py を全部入れる）:
  rules.py   判断だけの純粋関数（プロンプト・JSON の読み取り・許可コマンド・アラートと決定の読み取り・起こすかどうか・修復案の行）
  awsio.py   環境変数と AWS 呼び出し（Neptune / S3 Tables / AgentCore / SSM / SQS）
  worker.py  ここ。Temporal のアクティビティ・ワークフロー・starter・main
"""

import asyncio
import logging
import os
import time
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

# Temporal はワークフローを決定的に保つため、@workflow.defn のあるこのモジュールをサンドボックスの中で再 import する。
# 自作モジュールはそのとき素通しにする（公式の作法）。素通しにしないと awsio / rules がサンドボックス用に作り直され、
# 同じ名前の別物になる
with workflow.unsafe.imports_passed_through():
    import awsio
    import rules

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = os.environ.get("TASK_QUEUE", "nwc-investigate")
APPROVAL_TIMEOUT_MINUTES = int(os.environ.get("APPROVAL_TIMEOUT_MINUTES", "120"))
# 処置のあと、解消の通知を待つ秒数。通知は「機器 → Telegraf → MSK → Spark → AMP / Splunk → ルールの評価 → SNS → SQS」を通るので分の単位で遅れる
VERIFY_TIMEOUT = int(os.environ.get("VERIFY_TIMEOUT", "300"))
# 直らないまま終わった（rejected / expired / failed）あと、解消の通知を待ってワークフローの id を握っておく長さ（分）。過ぎたら閉じ、次の通知でもう一度調べる
HOLD_MINUTES = int(os.environ.get("HOLD_MINUTES", "1440"))
HOLD_OUTCOMES = ("rejected", "expired", "failed")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker")

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5))
OPTS = {"start_to_close_timeout": timedelta(seconds=60), "retry_policy": RETRY}  # 行を足すアクティビティ
NEWER_PROPOSAL_NOTE = "同じ異常の新しい修復案ができた"
NO_WORKFLOW_NOTE = "決定が届いたが、ワークフローがもう無い"


async def _append(rows: list) -> None:
    """proposal_events に行を足す（rows の順に 1 回のコミットで）"""
    await asyncio.to_thread(awsio.append_proposal_events, rows, rules.PROPOSAL_EVENT_COLUMNS)


def _note(fields: dict) -> str:
    """行の detail（この行の出来事の一言）。実行結果か確認結果"""
    return str(fields.get("apply_output") or fields.get("verify_note") or "")


# ---------------------------------------------------------------- アクティビティ（外に触る側。awsio を別スレッドで呼ぶ）
@activity.defn
async def investigate(anomaly: dict) -> dict:
    text = await asyncio.to_thread(awsio.ask_agent, rules.build_prompt(anomaly))
    parsed = rules.parse_agent_json(text)
    action, command = rules.normalize_action(parsed["action"])
    # 事前チェック: その処置をいまのトポロジに重ねて、孤立と冗長切れを見る。読めなくても修復案は出す（人が見て決める）
    pre = {"verdict": "", "text": ""}
    if action != rules.NO_ACTION:
        try:
            devices, links = await asyncio.to_thread(awsio.read_topology)
            pre = rules.precheck(action, devices, links)
        except Exception as e:  # noqa: BLE001 - Neptune の一時的な失敗で調査をやり直さない（エージェントをもう一度呼ぶことになる）
            pre = {"verdict": "unknown", "text": f"【{rules.PRECHECK_JA['unknown']}】トポロジを読めなかった: {str(e)[:200]}"}
    return {"cause": parsed["cause"], "action": action, "command": command,
            "reason": parsed["reason"], "agent_response": text[:4000],
            "precheck": pre["text"], "precheck_verdict": pre["verdict"]}


@activity.defn
async def put_proposal(anomaly: dict, finding: dict, wf_id: str, run_id: str = "") -> dict:
    """修復案の created の行（全項目。status は pending）を足し、修復案の辞書（その行）を返す。以後の段はこの辞書を持ち回る。
    書く前に同じ異常の行を読む:
      - 同じ proposal_id の行が別の実行のもの（ワークフローの id は異常ごとなので、前の発生の実行がありうる。run_id まで比べる）なら、再試行しないエラー
      - この実行自身の行（書けたあとで応答が切れて再試行された）なら、書かずにその最新の行で進む
      - 同じ異常で proposal_id の違う修復案の最新の行が pending なら、先に expired の行を足す（worker のタスクが入れ替わって
        Temporal の履歴が消えた修復案。新しい実行は前の発生への決定を無視するので、閉じないと承認待ちのまま残る）
    この「読んでから書く」は原子的ではない。同じワークフロー id の実行は Temporal が 1 つに絞るので、重なりうるのは
    アクティビティの再試行で同じ seq の行が 2 つ入ることだけで、読む側が 1 つにする"""
    now = int(time.time())
    aid = anomaly["anomaly_id"]
    first_seen = int(anomaly.get("first_seen") or 0)
    pid = rules.proposal_id(aid, first_seen)
    latest = await asyncio.to_thread(awsio.anomaly_proposals, aid)
    mine = latest.get(pid)
    if mine:
        if mine.get("workflow_id") != wf_id or str(mine.get("run_id") or "") != run_id:
            raise ApplicationError(
                f"proposal {pid} は別の実行（{mine.get('workflow_id') or '-'} / {mine.get('run_id') or '-'}）が書いた", non_retryable=True)
        return mine
    item = {
        "proposal_id": pid, "anomaly_id": aid, "device_id": anomaly.get("device_id", ""),
        "kind": anomaly.get("kind", ""), "target": anomaly.get("target", ""), "first_seen": first_seen,
        "source": anomaly.get("source", ""), "alert_detail": anomaly.get("detail", ""),
        "cause": finding["cause"], "action": finding["action"], "command": finding["command"],
        "reason": finding["reason"], "agent_response": finding["agent_response"],
        "precheck": finding.get("precheck", ""), "precheck_verdict": finding.get("precheck_verdict", ""),
        "workflow_id": wf_id, "run_id": run_id, "created_at": now,
    }
    stale = [rules.proposal_event("expired", p, now, NEWER_PROPOSAL_NOTE, {"verify_note": NEWER_PROPOSAL_NOTE})
             for other, p in sorted(latest.items()) if other != pid and p.get("status") == "pending"]
    created = rules.proposal_event("created", item, now, finding["reason"])
    await _append(stale + [created])
    return created


@activity.defn
async def record_event(proposal: dict, event: str, fields: dict | None = None) -> dict:
    """修復案の次の段の行を 1 行足し、その行（= 修復案の辞書に fields を重ねて seq を 1 進めたもの）を返す。
    fields は decided_by / decided_at（承認・却下）、apply_output（適用）、verify_note（確認・時間切れ・不要）"""
    fields = fields or {}
    row = rules.proposal_event(event, proposal, int(time.time()), _note(fields), fields)
    await _append([row])
    return row


@activity.defn
async def record_ignored(proposal: dict, decision: dict, effective: dict) -> dict:
    """効かなかった決定の行（rules.ignored_event。status とほかの項目は proposal のまま、seq だけ進める）を 1 行足し、その行を返す"""
    row = rules.ignored_event(proposal, decision, effective, int(time.time()))
    await _append([row])
    return row


@activity.defn
async def apply_on_lab(command: str) -> dict:
    if not awsio.LAB_INSTANCE_ID:
        return {"status": "Skipped", "output": "LAB_INSTANCE_ID が無い（IaC/terraform/aws-managed/pipeline/lab が無い）"}
    status, out = await asyncio.to_thread(awsio.run_on_lab, command)
    return {"status": status, "output": out}


ACTIVITIES = [investigate, put_proposal, record_event, record_ignored, apply_on_lab]


# ---------------------------------------------------------------- ワークフロー（決定的な側。AWS には触らない）
@workflow.defn
class InvestigateAnomaly:
    def __init__(self) -> None:
        self._decision: dict = {}
        self._resolved = False
        self._proposal_id = ""
        self._row: dict = {}         # 最後に足した行（次の行はこれの seq + 1）
        self._ignored: list = []     # 効かなかった決定のうち、まだ行にしていないもの（届いた順）
        self._seen: set = set()      # 届いた決定の中身（rules.decision_key）。同じものは SQS の重複配達として捨てる
        self._closed = False         # 承認待ちが決定なしで終わった（expired / obsolete）。以後の決定はログだけ

    @workflow.signal
    def decide(self, decision: dict) -> None:
        """人の判断（starter が決定のキューから送る。{"proposal_id", "decision", "decided_by", "decided_at"}）。
        先に届いた 1 回だけが効く。あとから届いた中身の違う決定は _ignored に控え、_flush が ignored の行にする。
        中身が同じもの（SQS の重複配達）と、自分の proposal_id でないもの（同じ異常の前の発生への決定）は無視する。
        承認待ちが決定なしで終わったあとに届いた決定は、効いた決定として控えずログだけ（控えると次の決定に誤った ignored の行が付く）"""
        if not isinstance(decision, dict) or decision.get("decision") not in rules.DECISIONS:
            return
        if not self._proposal_id:
            return
        if decision.get("proposal_id") != self._proposal_id:
            # 前の発生の修復案は put_proposal が expired にしてある（ワークフローが終わったあとに届いた決定と同じく、ログだけ）
            workflow.logger.info("proposal %s: 別の修復案 %s への %s by %s が届いた（行にしない）", self._proposal_id,
                                 decision.get("proposal_id") or "-", decision["decision"], decision.get("decided_by") or "-")
            return
        if self._closed:
            workflow.logger.info("proposal %s: 承認待ちが終わったあとに %s by %s が届いた（行にしない）",
                                 self._proposal_id, decision["decision"], decision.get("decided_by") or "-")
            return
        key = rules.decision_key(decision)
        if key in self._seen:
            return
        self._seen.add(key)
        if not self._decision:
            self._decision = decision
        else:
            self._ignored.append(decision)

    @workflow.signal
    def resolved(self, source: str = "") -> None:
        """解消の通知（starter が SQS の resolved を受けて送る）"""
        self._resolved = True

    async def _wait_resolved(self, seconds: int) -> bool:
        """解消の通知を seconds 秒まで待つ。待つあいだに届いた効かなかった決定は、その場で ignored の行にする（HOLD は 24 時間ある）"""
        deadline = workflow.now() + timedelta(seconds=seconds)
        left = timedelta(seconds=seconds)
        while left > timedelta(0):
            # wait_condition は timeout に達すると asyncio.TimeoutError を投げ、漏らすとワークフロー自体が失敗する（下の承認待ちと同じ）
            try:
                await workflow.wait_condition(lambda: self._resolved or bool(self._ignored and self._row), timeout=left)
            except asyncio.TimeoutError:
                break
            await self._flush()
            if self._resolved:
                break
            left = deadline - workflow.now()
        return self._resolved

    async def _record(self, event: str, fields: dict, flush: bool = True) -> dict:
        """次の段の行を足し（record_event）、flush なら続けて控えてある効かなかった決定の行を足す。最後に足した行を返す"""
        self._row = await workflow.execute_activity(record_event, args=[self._row, event, fields], **OPTS)
        if flush:
            await self._flush()
        return self._row

    async def _flush(self) -> None:
        """控えてある効かなかった決定を、届いた順に ignored の行にする（seq は最後に足した行の次）。
        書けなくてもワークフローは止めない（打つ・確かめるほうが先。ログだけ残してその決定は捨てる）"""
        while self._ignored and self._row:
            d = self._ignored.pop(0)
            d = {**d, "decided_at": d.get("decided_at") or int(workflow.now().timestamp())}
            try:
                self._row = await workflow.execute_activity(record_ignored, args=[self._row, d, self._decision], **OPTS)
            except ActivityError as e:
                workflow.logger.warning("proposal %s: ignored の行を書けなかった（%s by %s）: %s",
                                        self._proposal_id, d["decision"], d.get("decided_by") or "-", e.cause or e)

    @workflow.run
    async def run(self, anomaly: dict) -> str:
        outcome = await self._handle(anomaly)
        # 直っていないまま閉じると、同じ異常の次の通知（Grafana の repeat、Splunk の次のサーチ）がもう一度調査を起こす。
        # 解消の通知が来るまで id を握っておく（長くて HOLD_MINUTES 分。過ぎたら閉じて、次の通知で調べ直す）
        if outcome in HOLD_OUTCOMES and not self._resolved:
            workflow.logger.info("anomaly %s: %s。解消の通知を待つ（%d 分まで）", anomaly.get("anomaly_id"), outcome, HOLD_MINUTES)
            await self._wait_resolved(HOLD_MINUTES * 60)
        # 最後の段のあとに届いた効かなかった決定も行にしてから閉じる
        await self._flush()
        return outcome

    async def _handle(self, anomaly: dict) -> str:
        # 決定のシグナルを受けるかどうかはこの id で決める。調査より前に出しておく（調査のあいだに届いた決定も受ける）
        self._proposal_id = rules.proposal_id(anomaly["anomaly_id"], anomaly.get("first_seen"))
        # AgentCore の読み取り待ちは awsio が 150 秒まで延ばしている。1 回分が start_to_close に収まるよう 4 分
        finding = await workflow.execute_activity(
            investigate, anomaly, start_to_close_timeout=timedelta(minutes=4), retry_policy=RETRY)
        info = workflow.info()
        self._row = await workflow.execute_activity(put_proposal, args=[anomaly, finding, info.workflow_id, info.run_id], **OPTS)
        pid = self._row["proposal_id"]

        workflow.logger.info("proposal %s: pending (action=%s)", pid, finding["action"])

        # 人の判断（シグナル decide）か解消の通知（シグナル resolved）か時間切れまで待つ。
        # wait_condition は timeout に達すると asyncio.TimeoutError を投げ、それをそのまま漏らすと
        # ワークフロー自体が失敗する（temporalio は TimeoutError をタスク失敗でなくワークフロー失敗にする。
        # 2026-09-18 に承認しても applied に進まない原因だった）。時間切れは「決まらなかった」なので握る
        try:
            await workflow.wait_condition(lambda: bool(self._decision) or self._resolved,
                                          timeout=timedelta(minutes=APPROVAL_TIMEOUT_MINUTES))
        except asyncio.TimeoutError:
            pass
        d = self._decision
        if not d:
            self._closed = True
            if self._resolved:
                workflow.logger.info("proposal %s: obsolete（承認を待つあいだに解消した）", pid)
                await self._record("obsolete", {"verify_note": "承認を待つあいだにこの異常が解消したので打たなかった"})
                return "obsolete"
            workflow.logger.info("proposal %s: expired", pid)
            await self._record("expired", {"verify_note": "承認待ちのまま時間切れ"})
            return "expired"
        decision = d["decision"]
        # 効かなかった決定の行はここでは書かない（打つほうが先。applied / failed の行のあと、解消を待つあいだ、閉じる前に書く）
        await self._record(decision, {"decided_by": d.get("decided_by", ""),
                                      "decided_at": d.get("decided_at") or int(workflow.now().timestamp())}, flush=False)
        workflow.logger.info("proposal %s: %s", pid, decision)
        if decision == "rejected":
            return "rejected"

        # 承認と同時に解消の通知が届いていることがある。解消した異常へ古い処置を打たない
        if self._resolved:
            workflow.logger.info("proposal %s: obsolete（承認のあいだに異常が解消した）", pid)
            await self._record("obsolete", {"verify_note": "承認のあいだにこの異常が解消したので打たなかった"})
            return "obsolete"

        # 承認された。none なら打つものが無いので applied 扱いで verify へ
        if finding["command"]:
            # 打つのは 1 回だけ（maximum_attempts=1）。SSM に届かない・時間切れはアクティビティの失敗（ActivityError）として返るので、
            # 握らないとワークフローごと失敗して修復案が approved のまま残る。握って failed を書く
            try:
                result = await workflow.execute_activity(
                    apply_on_lab, finding["command"], start_to_close_timeout=timedelta(minutes=4), retry_policy=RetryPolicy(maximum_attempts=1))
            except ActivityError as e:
                cause = e.cause or e
                result = {"status": "Error", "output": f"{type(cause).__name__}: {cause}"}
            ok = result["status"] in ("Success", "Skipped")
            workflow.logger.info("proposal %s: apply %s -> %s", pid, finding["command"], result["status"])
            await self._record("applied" if ok else "failed", {"apply_output": f"{result['status']}: {result['output']}"[:4000]})
            if not ok:
                return "failed"
        else:
            await self._record("applied", {"apply_output": "処置なし（action=none）"})

        applied_at = workflow.now()
        if await self._wait_resolved(VERIFY_TIMEOUT):
            waited = int((workflow.now() - applied_at).total_seconds())
            workflow.logger.info("proposal %s: verified (%ds)", pid, waited)
            await self._record("verified", {"verify_note": f"処置から {waited} 秒で解消の通知が届いた"})
            return "verified"
        workflow.logger.info("proposal %s: not resolved after %ds", pid, VERIFY_TIMEOUT)
        await self._record("failed", {"verify_note": f"{VERIFY_TIMEOUT} 秒待っても解消の通知が届かない"})
        return "failed"


# ---------------------------------------------------------------- starter（アラートを拾ってワークフローを起こす・解消と決定を伝える）
async def start_for(client: Client, alert: dict) -> bool:
    """firing のアラート 1 件についてワークフローを起こす（起こさない理由があれば False）。
    WorkflowAlreadyStartedError（同じ異常のワークフローが走っている）は False。それ以外の失敗は呼び手に投げる"""
    aid = alert.get("anomaly_id", "")
    if not rules.should_start(alert, None):
        return False
    # 同じ発生の修復案がもうあれば起こさない（ワークフローが閉じたあとで届いた、同じ starts_at の繰り返しの通知）
    existing = await asyncio.to_thread(awsio.latest_proposal, rules.proposal_id(aid, alert.get("first_seen")))
    if not rules.should_start(alert, existing):
        return False
    # 保守中の機器（Nautobot の Status が Maintenance）に関わる異常では起こさない。修復案も作らないので、保守が明けても落ちたままなら次の通知で起こす
    held = rules.maintenance_hold(alert, *await asyncio.to_thread(awsio.read_topology))
    if held:
        log.info("skip %s: 保守中の機器（%s）", aid, ", ".join(held))
        return False
    wid = rules.workflow_id(aid)
    try:
        await client.start_workflow(InvestigateAnomaly.run, alert, id=wid, task_queue=TASK_QUEUE)
        log.info("started %s (%s)", wid, alert.get("source") or "-")
        return True
    except WorkflowAlreadyStartedError:
        return False


async def resolve_for(client: Client, alert: dict) -> bool:
    """resolved のアラート 1 件を、走っているワークフローにシグナルで伝える。走っていなければ（NOT_FOUND）False"""
    wid = rules.workflow_id(alert.get("anomaly_id", ""))
    try:
        await client.get_workflow_handle(wid).signal(InvestigateAnomaly.resolved, alert.get("source", ""))
        log.info("resolved %s (%s)", wid, alert.get("source") or "-")
        return True
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return False
        raise


async def handle_message(client: Client, body: str) -> None:
    """アラートのキューのメッセージ 1 通（アラートが 1 件以上）。返れば消してよい、例外なら消さない（配り直させる。どちらの処理も繰り返して害が無い）。
    決定（{"type":"decision",…}）は alerts_from_message が読まないので、ここでは読めないメッセージとして消す
    （このキューは SNS の <接頭辞>-alerts を受けていて、Grafana と Splunk のタスクロールも publish できる。決定は決定のキューからだけ受ける）"""
    alerts = rules.alerts_from_message(body, int(time.time()))
    if not alerts:
        log.warning("starter: 読めないメッセージ（消す）: %s", str(body)[:200])
        return
    for a in alerts:
        if a["kind"] not in rules.START_KINDS:
            continue
        if a["status"] == "resolved":
            await resolve_for(client, a)
        else:
            await start_for(client, a)


async def handle_decision(client: Client, body: str) -> None:
    """決定のキューのメッセージ 1 通（Web の承認・却下）。investigate-<anomaly_id> にシグナル decide で送る。
    効くかどうか（自分の proposal_id か、もう決まっていないか）はワークフローが決める。
    ワークフローが走っていない（NOT_FOUND）なら、修復案がまだ pending のときだけ expired の行を足す（worker のタスクが入れ替わって
    Temporal の履歴が消えた修復案を承認待ちのまま残さない）。どちらでもメッセージは消す。Temporal や S3 Tables に届かない失敗は投げる（消さない）"""
    d = rules.decision_from_message(body, int(time.time()))
    if not d:
        log.warning("starter: 読めない決定（消す）: %s", str(body)[:200])
        return
    pid = d["proposal_id"]
    wid = rules.workflow_id(rules.anomaly_of(pid))
    try:
        await client.get_workflow_handle(wid).signal(InvestigateAnomaly.decide, d)
        log.info("decide %s: %s by %s", pid, d["decision"], d["decided_by"] or "-")
        return
    except RPCError as e:
        if e.status != RPCStatusCode.NOT_FOUND:
            raise
    latest = await asyncio.to_thread(awsio.latest_proposal, pid)
    if latest.get("status") != "pending":
        log.info("decide %s: %s by %s が届いたが、ワークフローが無い（修復案は %s。行にしない）",
                 pid, d["decision"], d["decided_by"] or "-", latest.get("status") or "無い")
        return
    log.info("decide %s: ワークフローが無いので expired にする", pid)
    await _append([rules.proposal_event("expired", latest, int(time.time()), NO_WORKFLOW_NOTE, {"verify_note": NO_WORKFLOW_NOTE})])


async def starter_queue(client: Client, queue_url: str, handle) -> None:
    """SQS を待ち、1 通ずつ handle（handle_message か handle_decision）で処理して消す。
    WorkflowAlreadyStartedError は start_for が False にする（同じ異常の重複配達で、もう走っているので消してよい。
    残すと可視性タイムアウトごとに配り直され、5 回で DLQ に落ちて「処理できなかった」ものと見分けがつかなくなる）。
    Temporal や Neptune や S3 Tables に届かないなどの失敗は消さずに残す（配り直し、直らなければ DLQ）"""
    for m in await asyncio.to_thread(awsio.receive_messages, queue_url):
        try:
            await handle(client, m.get("Body", ""))
        except Exception as e:  # noqa: BLE001 - 消さずに次へ（可視性タイムアウトのあとで配り直される）
            log.warning("starter: %s: %s（メッセージは残す）", type(e).__name__, str(e)[:300])
            continue
        await asyncio.to_thread(awsio.delete_message, queue_url, m["ReceiptHandle"])


async def starter(client: Client, queue_url: str, handle) -> None:
    while True:
        try:
            await starter_queue(client, queue_url, handle)
        except (WorkflowFailureError, RuntimeError, OSError) as e:
            log.warning("starter: %s", str(e)[:300])
            await asyncio.sleep(5)
        except Exception as e:  # noqa: BLE001 - boto の例外は種類が多いので落とさずログに出す
            log.warning("starter: %s: %s", type(e).__name__, str(e)[:300])
            await asyncio.sleep(5)


# ---------------------------------------------------------------- 起動
async def connect() -> Client:
    for i in range(60):
        try:
            return await Client.connect(TEMPORAL_ADDRESS)
        except Exception as e:  # noqa: BLE001 - temporal が起きるまで待つ
            log.info("waiting for temporal (%s): %s", TEMPORAL_ADDRESS, str(e)[:100])
            await asyncio.sleep(5)
    raise RuntimeError("temporal did not come up")


async def main() -> None:
    for k in ("ANOMALY_QUEUE_URL", "DECISION_QUEUE_URL", awsio.GRAPH_ENV, "AUDIT_TABLE_BUCKET_ARN", "AUDIT_NAMESPACE", "AGENT_RUNTIME_ARN"):
        if not getattr(awsio, k):
            raise SystemExit(f"{k} が無い")
    client = await connect()
    worker = Worker(client, task_queue=TASK_QUEUE, workflows=[InvestigateAnomaly], activities=ACTIVITIES)
    log.info("worker up: queue=%s approval_timeout=%smin verify_timeout=%ss hold=%smin lab=%s", TASK_QUEUE,
             APPROVAL_TIMEOUT_MINUTES, VERIFY_TIMEOUT, HOLD_MINUTES, awsio.LAB_INSTANCE_ID or "-")
    await asyncio.gather(worker.run(), starter(client, awsio.ANOMALY_QUEUE_URL, handle_message),
                         starter(client, awsio.DECISION_QUEUE_URL, handle_decision))


if __name__ == "__main__":
    asyncio.run(main())
