"""エージェントのツール（topology.py / evidence.py / proposals.py）が共有する小物。

同じコードが何本ものファイルに写してあったのを 1 か所に集めたもの。中身は 6 つ:

  runner()              TOOLS（ツール名 → 関数）から run_tool を作る。4 モジュールが同じものを持っていた
  client() / session()  boto3 のクライアントとセッションをプロセスに 1 つだけ作って使い回す
  Param                 「環境変数が先、無ければ SSM」の設定値（Neptune の接続先・Gateway の URL・Runtime の ARN）
  athena_rows()         Athena でクエリを 1 本打って結果の行を読む（evidence.py の query_history と proposals.py。2026-10-05）
  brief_error()         AWS の例外を画面に出す短い文言に（例外の名前と短い理由。ARN とアカウント ID は伏せる。2026-10-05）
  jst()                 epoch 秒を日本時間の文字列に（proposals.py）

import のときには boto3 のクライアントを作らない（client() を呼んだときに作る）。Web の EC2 では、
修復案を配備していない構成でも proposals.py を import して「まだ配備されていない」を返すため。

このファイルは 5 か所で動く。AgentCore Runtime のコンテナ（agent/Dockerfile）、tools Lambda の zip
（terraform/workflow/gateway.tf の archive_file）、Web の EC2（terraform/base/core の出力 upload_web_command）、
status Lambda の zip（terraform/pipeline/graph/sync.tf の archive_file。graph.py 経由で使う）、
Nautobot のコンテナ（nautobot/Dockerfile。Job が graph.py 経由で使う。ops/up.sh の nautobot_context が集める）。
agent/ のモジュールを増やしたら、この 5 か所の一覧にも足す。zip に入れ忘れると apply も plan も通ったまま、
実行時に ModuleNotFoundError で初めて分かる（tests/test_sync.py と tests/test_workflow.py が zip の中身を見ている）。
"""

import os
import re
import time
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# SSM のパラメータ名の頭（/<接頭辞>）。Terraform が環境変数で渡す。空なら SSM は引かない
PARAM_PREFIX = os.environ.get("PARAM_PREFIX", "")
REGION = os.environ.get("AWS_REGION") or os.environ.get("BEDROCK_REGION") or None
JST = timezone(timedelta(hours=9))
TTL = 60  # 設定値を SSM から引き直す間隔（秒）。まだ配備していないときに毎回叩かないため

_clients: dict = {}
_sessions: list = []


# ---------------------------------------------------------------- ツールの呼び出し
def runner(tools: dict, before=None):
    """TOOLS から run_tool(name, args) を作る。

    モデルが渡してくる toolUse の引数には仕様に無い名前が混ざることがあるので、関数が受け取れる名前だけを通す。
    before は毎回ツールの前にやること（topology だけが使う。元データを読み直す）。
    """

    def run_tool(name: str, args: dict) -> dict:
        fn = tools.get(name)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        if before is not None:
            before()
        # co_varnames は引数のあとにローカル変数も並ぶので、引数の数（co_argcount）で切る。
        # 切らないと、ローカル変数と同じ名前の余計な引数が素通りして TypeError になる
        names = fn.__code__.co_varnames[: fn.__code__.co_argcount]
        try:
            return fn(**{k: v for k, v in (args or {}).items() if k in names})
        except (TypeError, ValueError) as e:
            return {"error": str(e)}

    return run_tool


# ---------------------------------------------------------------- boto3（作り直さない）
def client(service: str):
    """boto3 のクライアントはプロセスに 1 つだけ作る。

    1 つ目は 100 ミリ秒ほどかかり、2 つ目からは 1 ミリ秒（手元での実測）。Runtime も Lambda も一度起きたら
    使い回されるので、呼び出しのたびに作るとその分をまるごと捨てることになる。
    """
    if service not in _clients:
        _clients[service] = boto3.client(service, region_name=REGION)
    return _clients[service]


def session():
    """boto3.Session も 1 つだけ（SigV4 の署名に使う）。

    セッションは取ってきた認証情報を自分の中に持つので、使い回せば毎回の取り直しが消える。
    期限が切れたぶんの更新はセッションの中の仕組みがやる。
    """
    if not _sessions:
        _sessions.append(boto3.Session(region_name=REGION))
    return _sessions[0]


# ---------------------------------------------------------------- 配備で決まる設定値
class Param:
    """環境変数 <env> が先で、無ければ SSM の <PARAM_PREFIX>/<param> から引いて覚えておく設定値。

    Terraform が apply したときに決まるもの（Neptune の接続先・Gateway の URL・Runtime の ARN）を、
    どのモジュールも同じ手順で読むためのもの。どちらも無ければ空文字を返し、呼ぶ側はそれを見て
    「まだ配備されていない」と案内する（その機能をまだ作っていない構成でも落ちないため）。
    引けなかったときは ttl 秒のあいだ SSM を引き直さない（まだ無いものを毎回叩かない）。
    decrypt=True は SecureString（ops/up.sh が作るトークンなど）を読むとき。値はログに出さない。
    """

    def __init__(self, env: str, param: str, ttl: int = TTL, decrypt: bool = False):
        self.env = env
        self.param = param
        self.ttl = ttl
        self.decrypt = decrypt
        self.cached = ""  # 一度引けた値。プロセスが終わるまで使う
        self.checked = 0.0  # 最後に SSM を引いた時刻

    def value(self) -> str:
        env = os.environ.get(self.env, "")
        if env:
            return env
        if self.cached or time.time() - self.checked < self.ttl or not PARAM_PREFIX:
            return self.cached
        self.checked = time.time()
        try:
            extra = {"WithDecryption": True} if self.decrypt else {}
            self.cached = client("ssm").get_parameter(Name=f"{PARAM_PREFIX}/{self.param}", **extra)["Parameter"]["Value"]
        except (ClientError, BotoCoreError):
            self.cached = ""
        return self.cached


# ---------------------------------------------------------------- Athena（S3 Tables の alert_events / proposal_events を読む。2026-10-05 に evidence.py から移した）
# evidence.py ではなくここに置くのは、Web の EC2 に上がる agent のモジュールが toolkit / topology / graph / proposals だけだから
# （ops/up.sh の upload と terraform/base/core の upload_web_command）。proposals.py が evidence.py を import すると Web で落ちる
# 実行パラメータ（ExecutionParameters）に渡してよい値。Athena は値を SQL の式として読む（文字列は '…' で囲む）ので、引用符の入らない文字だけを通す。
# 機器名が通る
ATHENA_PARAM_RE = re.compile(r"^[A-Za-z0-9._:/#?-]{1,128}$")
# proposal_id（<機器>#<種類>#<対象>#<epoch 秒>）はこれより広く通す。対象はアラートの送り手が付けた文字列そのもの（Splunk なら ifName か ifDescr）で、
# 空白・[]・日本語や 128 文字を超えるものもあり、上の検査では承認も詳細もできない修復案ができる（2026-10-05）。
# '…' の中で意味を持つのは ' だけなので、' と制御文字を除いた 1000 文字まで（ExecutionParameters は 1 つ 1024 文字まで。引用符の 2 文字を足す）
ATHENA_TEXT_RE = re.compile(r"^[^'\x00-\x1f\x7f]{1,1000}\Z")
# Athena が返す timestamptz の文字列（2026-10-04 07:00:00.000000 UTC）
_ATHENA_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*(?:UTC|Z|\+00:00)?$")


def _athena_wait(athena, qid: str, timeout: float, poll: float):
    """クエリが終わるまで最大 timeout 秒待つ。終わったら Status、時間切れなら None"""
    deadline = time.monotonic() + timeout
    while True:
        status = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if status.get("State") in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return status
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def athena_rows(sql: str, workgroup: str, params=(), max_rows: int = 100, timeout: float = 20, poll: float = 0.5,
                timeout_hint: str = "", param_re=ATHENA_PARAM_RE) -> tuple[list, str]:
    """Athena で sql を 1 本打ち、(行の list, エラーの文言) を返す。行はセルの値の list（1 行目の列名は除く。NULL は None）。
    開始 → 終わるまで待つ → timeout 秒で終わらなければ止める → 結果を最大 max_rows 行読む。うまくいけばエラーは空文字。
    params は SQL の ? に順に入る値（'…' で囲んで ExecutionParameters に渡す）。param_re（既定は ATHENA_PARAM_RE）に合わない値があれば打たない。
    proposal_id を渡すときだけ ATHENA_TEXT_RE（ほかの値は呼ぶ側が先に狭く確かめる）。
    結果の置き場はワークグループの管理ストレージ（ResultConfiguration は渡さない）"""
    params = [str(v) for v in params]
    if any(not param_re.match(v) for v in params):
        return [], "使えない文字がある値は Athena に渡さない"
    req = {"QueryString": sql, "WorkGroup": workgroup}
    if params:
        req["ExecutionParameters"] = [f"'{v}'" for v in params]
    try:
        athena = client("athena")  # 作るときの失敗（リージョンが無いなど）も「呼べない」にする
        qid = athena.start_query_execution(**req)["QueryExecutionId"]
        status = _athena_wait(athena, qid, timeout, poll)
        if status is None:
            try:
                athena.stop_query_execution(QueryExecutionId=qid)
            except (ClientError, BotoCoreError):
                pass
            return [], f"Athena のクエリが {timeout} 秒で終わらなかったので止めた{timeout_hint}"
        if status["State"] != "SUCCEEDED":
            return [], f"Athena のクエリが {status['State']}: {brief(status.get('StateChangeReason', ''))}"
        result = athena.get_query_results(QueryExecutionId=qid, MaxResults=min(int(max_rows) + 1, 1000))
    except (ClientError, BotoCoreError) as e:
        return [], f"Athena を呼べない: {brief_error(e)}"
    # 1 行目は列名。NULL のセルは VarCharValue が無い
    return [[c.get("VarCharValue") for c in r.get("Data", [])] for r in (result.get("ResultSet") or {}).get("Rows", [])[1:]], ""


# AWS の例外の文言には、呼んだロールの ARN（アカウント ID 入り）やリソースの ARN がそのまま入る（AccessDenied の
# 「User: arn:aws:sts::<アカウント>:assumed-role/… is not authorized …」）。画面とチャットに返すのは例外の名前と短い理由までにして、
# ARN・12 桁のアカウント ID・アクセスキー ID は伏せる（2026-10-05）
_REDACT_RE = re.compile(r"arn:[\w-]*:[^\s'\",;]*|(?<!\d)\d{12}(?!\d)|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
ERROR_TEXT_MAX = 160


def brief(text, limit: int = ERROR_TEXT_MAX) -> str:
    """エラーの文言から ARN・アカウント ID・アクセスキー ID を *** にして、limit 文字で切る"""
    text = _REDACT_RE.sub("***", " ".join(str(text or "").split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def brief_error(e: Exception) -> str:
    """AWS の例外を「名前: 短い理由」に（ClientError は Error.Code と Error.Message、ほかは例外のクラス名と文言）"""
    err = (getattr(e, "response", None) or {}).get("Error") or {}
    name = str(err.get("Code") or type(e).__name__)
    return f"{brief(name, 60)}: {brief(err.get('Message') or e)}"


def athena_epoch(ts) -> int:
    """Athena の timestamptz（UTC）の文字列を epoch 秒に。読めなければ 0"""
    m = _ATHENA_TS_RE.match(str(ts or "").strip())
    if not m:
        return 0
    return int(datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------- 表示
def jst(epoch) -> str:
    """epoch 秒を日本時間の「2026-09-18 12:34:56」に。空や 0 なら空文字"""
    return datetime.fromtimestamp(int(epoch), JST).strftime("%Y-%m-%d %H:%M:%S") if epoch else ""
