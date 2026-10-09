"""調査の証拠を取るツール（WORKFLOW。エージェントが原因分析のために OpenSearch / Prometheus / S3 Tables を見に行く）。

Grafana / Splunk のアラート（2026-10-02 までは Spark が検知していた）を受けて、エージェントが Neptune / S3 Tables / OpenSearch / Prometheus を見に行って原因を分析する。
Neptune は graph.py / topology.py（neighbors / blast_radius）、ここは残りの 3 つ:
  search_logs    OpenSearch Serverless の logs コレクション（IaC/terraform/aws-managed/pipeline/analytics の sinks=opensearch。Spark が traps と logs（機器の syslog）を書く）を機器名で検索
  query_metrics  Amazon Managed Service for Prometheus（sinks=prometheus。Spark が metrics を remote write）に PromQL を投げる
  query_history  S3 Tables（Iceberg）の alert_events（Grafana / Splunk のアラートの通知。graph の status Lambda → Firehose が追記）を Athena で読む（2026-10-04）

エンドポイントは環境変数 OPENSEARCH_ENDPOINT（https://...aoss.amazonaws.com）/ OPENSEARCH_INDEX（既定 snmp-logs）/
PROMETHEUS_QUERY_URL（https://aps-workspaces.<region>.amazonaws.com/workspaces/<id>/api/v1/query）/
ATHENA_WORKGROUP・ATHENA_CATALOG・HISTORY_NAMESPACE・ALERT_EVENTS_TABLE（IaC/terraform/aws-managed/pipeline/analytics の history.tf の出力）。
どれも無ければ「まだ配備されていない」を返して、PIPELINE の analytics を作っていない構成でも落ちない。
署名は botocore の SigV4（サービス名 aoss / aps）。requests は使わず urllib で送る（tools Lambda は素の python3.13、依存を増やさない）。
Athena は boto3 の athena クライアント（python3.13 の Lambda に入っている）。実行の手順（開始 → 待つ → 止める → 結果）は toolkit.athena_rows
（app/agentcore/proposals.py の修復案の読み取りと共用。2026-10-05）。
tools Lambda（IaC/terraform/aws-managed/workflow）と chat runtime（app/agentcore/app.py）の両方から同じものが呼ばれる。

OSS 版（cycle 005。IaC/terraform/oss）は送り先の認証だけを環境変数で切り替える（無ければ上の SigV4 のまま）:
  OPENSEARCH_AUTH=basic   OpenSearch（自前）に Basic 認証。ユーザーは OPENSEARCH_USER（既定 admin）、パスワードは
                          環境変数 OPENSEARCH_PASSWORD か SSM の <PARAM_PREFIX>/opensearch-password（SecureString。ops/up.sh が作る）
  PROMETHEUS_AUTH=none    VictoriaMetrics の vmselect に署名せずに送る（PROMETHEUS_QUERY_URL は http://…/select/0/prometheus/api/v1/query）
"""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError

import toolkit

OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT", "").rstrip("/")
OPENSEARCH_INDEX = os.environ.get("OPENSEARCH_INDEX", "snmp-logs")
PROMETHEUS_QUERY_URL = os.environ.get("PROMETHEUS_QUERY_URL", "")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "")
ATHENA_CATALOG = os.environ.get("ATHENA_CATALOG", "")  # s3tablescatalog/<テーブルバケット>
HISTORY_NAMESPACE = os.environ.get("HISTORY_NAMESPACE", "")
ALERT_EVENTS_TABLE = os.environ.get("ALERT_EVENTS_TABLE", "")
REGION = os.environ.get("AWS_REGION") or os.environ.get("BEDROCK_REGION") or "ap-northeast-1"
# 送り先の認証（先頭が既定 = マネージド版。OSS 版だけ後ろの方。モジュールの docstring）
AUTHS = {"aoss": ("OPENSEARCH_AUTH", ("sigv4", "basic")), "aps": ("PROMETHEUS_AUTH", ("sigv4", "none"))}
OPENSEARCH_USER = os.environ.get("OPENSEARCH_USER") or "admin"
OPENSEARCH_PASSWORD = toolkit.Param("OPENSEARCH_PASSWORD", "opensearch-password", decrypt=True)
TIMEOUT = 20
POLL = 0.5  # Athena のクエリの状態を見に行く間隔（秒）


def _request(method: str, url: str, service: str, body: bytes | None = None, headers: dict | None = None) -> dict:
    """service（aoss / aps）の認証で送る。既定は _signed（SigV4）。OSS 版は OPENSEARCH_AUTH=basic / PROMETHEUS_AUTH=none（モジュールの docstring）"""
    name, choices = AUTHS[service]
    auth = (os.environ.get(name) or choices[0]).strip().lower()
    if auth not in choices:
        return {"error": f"{name} は {' / '.join(choices)} のどれか: {auth!r}"}
    if auth == "sigv4":
        return _signed(method, url, service, body, headers)
    headers = dict(headers or {})
    if auth == "basic":
        password = OPENSEARCH_PASSWORD.value()
        if not password:
            return {"error": "OPENSEARCH_AUTH=basic なのに OpenSearch のパスワードが無い（環境変数 OPENSEARCH_PASSWORD か SSM の opensearch-password）"}
        headers["Authorization"] = "Basic " + base64.b64encode(f"{OPENSEARCH_USER}:{password}".encode("utf-8")).decode("ascii")
    return _send(method, url, body, headers)


def _signed(method: str, url: str, service: str, body: bytes | None = None, headers: dict | None = None) -> dict:
    """SigV4 で署名して送る。応答の JSON を返し、届かない・拒否されたときは {"error": ...}"""
    headers = dict(headers or {})
    req = AWSRequest(method=method, url=url, data=body, headers=headers)
    try:
        # セッションは使い回す（認証情報を取り直さないため）。署名のリージョンはここで指定する
        SigV4Auth(toolkit.session().get_credentials(), service, REGION).add_auth(req)
    except (BotoCoreError, AttributeError, TypeError) as e:  # NoCredentialsError は BotoCoreError の子
        return {"error": f"署名できない（認証情報が無い）: {e}"}
    return _send(method, url, body, dict(req.headers))


def _send(method: str, url: str, body: bytes | None, headers: dict) -> dict:
    """送って応答の JSON を返す。届かない・拒否されたときは {"error": ...}"""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method=method), timeout=TIMEOUT) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"}
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return {"error": f"届かない: {e}"}


def search_logs(device_id: str = "", minutes: int = 60, limit: int = 20) -> dict:
    """直近 minutes 分の traps / ログを機器名で検索（新しい順）。device_id が空なら全機器"""
    if not OPENSEARCH_ENDPOINT:
        return {"error": "ログの検索はまだ配備されていない（IaC/terraform/aws-managed/pipeline/analytics を sinks に opensearch を入れて apply すると使える）", "hits": []}
    minutes = max(1, min(int(minutes), 24 * 60))
    limit = max(1, min(int(limit), 100))
    since = int((time.time() - minutes * 60) * 1000)
    must = [{"range": {"@timestamp": {"gte": since}}}]
    if device_id:
        # Spark は tags.sysName / tags.agent_host / tags.source を持つ（app/spark/snmp_sinks.py の opensearch の文書）
        must.append({"multi_match": {"query": device_id, "fields": ["tags.sysName", "tags.agent_host", "tags.source", "device_id"]}})
    body = json.dumps({"size": limit, "sort": [{"@timestamp": "desc"}], "query": {"bool": {"must": must}}}).encode()
    res = _request("POST", f"{OPENSEARCH_ENDPOINT}/{OPENSEARCH_INDEX}/_search", "aoss", body, {"Content-Type": "application/json"})
    if "error" in res:
        return {"error": res["error"], "hits": []}
    hits = [h.get("_source", {}) for h in (res.get("hits") or {}).get("hits", [])]
    return {"count": len(hits), "index": OPENSEARCH_INDEX, "minutes": minutes, "hits": hits}


def query_metrics(query: str, minutes: int = 15) -> dict:
    """PromQL の range query（step 60 秒）。例: snmp_interface_oper_up{sysName="dc1-a-leaf-01"}"""
    if not PROMETHEUS_QUERY_URL:
        return {"error": "メトリクスの検索はまだ配備されていない（IaC/terraform/aws-managed/pipeline/analytics を sinks に prometheus を入れて apply すると使える）", "series": []}
    if not query:
        return {"error": "query（PromQL）が空", "series": []}
    minutes = max(1, min(int(minutes), 24 * 60))
    end = int(time.time())
    params = urllib.parse.urlencode({"query": query, "start": end - minutes * 60, "end": end, "step": "60s"})
    url = PROMETHEUS_QUERY_URL.rstrip("/")
    url = url[: -len("/query")] + "/query_range" if url.endswith("/query") else url + "/query_range"
    res = _request("GET", f"{url}?{params}", "aps")
    if "error" in res:
        return {"error": res["error"], "series": []}
    if res.get("status") != "success":
        return {"error": f"Prometheus: {res.get('errorType', '')} {res.get('error', '')}".strip(), "series": []}
    series = []
    for r in (res.get("data") or {}).get("result", []):
        vals = r.get("values") or ([r["value"]] if "value" in r else [])
        series.append({"metric": r.get("metric", {}), "last": vals[-1][1] if vals else None, "points": len(vals),
                       "values": [[int(float(t)), v] for t, v in vals[-20:]]})
    return {"count": len(series), "minutes": minutes, "series": series}


# ---------------------------------------------------------------- アラートの通知の履歴（Athena → S3 Tables の alert_events。2026-10-04）
HISTORY_NOT_DEPLOYED = ("アラートの履歴（S3 Tables の alert_events を Athena で読む）はまだ配備していない（IaC/terraform/aws-managed/pipeline/analytics を apply して、"
                        "IaC/terraform/aws-managed/workflow を apply し直すと使える）。直近はメトリクスを query_metrics、ログを search_logs で見る")
# 列は app/temporal/rules.py の ALERT_EVENT_COLUMNS と同じ順（tests/test_agentcore.py が突き合わせる）
HISTORY_COLUMNS = ("event_id", "anomaly_id", "source", "status", "device_id", "kind", "target", "detail", "starts_at", "received_at")
HISTORY_LIMIT = 50


def history_sql(hours: int, by_device: bool) -> str:
    """alert_events を読む SQL。Lambda のやり直しで二重に入った行を event_id で落とし、新しい順に最大 50 件。
    hours は呼ぶ側で整数に丸めたもの。device_id は ? にして ExecutionParameters で渡す（SQL に埋め込まない）"""
    table = f'"{ATHENA_CATALOG}"."{HISTORY_NAMESPACE}"."{ALERT_EVENTS_TABLE}"'
    where = f"received_at > current_timestamp - interval '{int(hours)}' hour" + (" AND device_id = ?" if by_device else "")
    return (f"SELECT {', '.join(HISTORY_COLUMNS)} FROM (SELECT *, row_number() OVER (PARTITION BY event_id ORDER BY received_at) rn "
            f"FROM {table} WHERE {where}) WHERE rn = 1 ORDER BY received_at DESC LIMIT {HISTORY_LIMIT}")


def _jst_of(ts) -> str:
    """Athena の timestamptz（UTC）の文字列を日本時間の「2026-10-04 16:00:00」に。読めなければ空文字"""
    return toolkit.jst(toolkit.athena_epoch(ts))


def query_history(device_id: str = "", hours: int = 24) -> dict:
    """アラートの通知の履歴（Grafana / Splunk。発火と解消）。新しい順に最大 50 件で、同じ通知（event_id）の重複は落とす"""
    device_id = str(device_id or "").strip()
    hours = max(1, min(int(hours), 720))
    if not (ATHENA_WORKGROUP and ATHENA_CATALOG and HISTORY_NAMESPACE and ALERT_EVENTS_TABLE):
        return {"error": HISTORY_NOT_DEPLOYED, "device_id": device_id, "hours": hours, "rows": []}
    if device_id and not toolkit.ATHENA_PARAM_RE.match(device_id):
        return {"error": "device_id に使えない文字がある（英数字と . _ : / # ? - だけ）", "device_id": device_id, "hours": hours, "rows": []}
    cells_list, error = toolkit.athena_rows(history_sql(hours, bool(device_id)), ATHENA_WORKGROUP, [device_id] if device_id else (),
                                            max_rows=HISTORY_LIMIT, timeout=TIMEOUT, poll=POLL, timeout_hint="（hours を短くするか device_id で絞る）")
    if error:
        return {"error": error, "device_id": device_id, "hours": hours, "rows": []}
    rows = []
    for cells in cells_list:
        row = dict(zip(HISTORY_COLUMNS, cells))
        row["starts_at_jst"], row["received_at_jst"] = _jst_of(row.get("starts_at")), _jst_of(row.get("received_at"))
        rows.append(row)
    return {"device_id": device_id, "hours": hours, "count": len(rows), "rows": rows,
            "note": "1 行 = 1 つの通知（event_id = 異常・送り手・状態・starts_at が同じ送り直しは最初に届いた 1 行にまとめる。Grafana の 4 時間ごとの送り直しもまとまる）。starts_at は Grafana なら発火した時刻（resolved の行も同じ）、"
                    "Splunk ならその状態を最後に見た時刻（latest(_time)。resolved なら解消した時刻）。received_at は Lambda が受けた時刻。いま開いている異常は機器・回線の status で見る"}


TOOL_SPECS = [
    {"toolSpec": {
        "name": "search_logs",
        "description": "監視ログ（SNMP trap など）を機器名で検索する。異常の原因を調べるとき、その機器で直前に何が起きたかを見るのに使う。",
        "inputSchema": {"json": {"type": "object", "properties": {
            "device_id": {"type": "string", "description": "機器名（例 dc1-a-leaf-01）。空なら全機器"},
            "minutes": {"type": "integer", "description": "何分前まで見るか（既定 60、最大 1440）"},
            "limit": {"type": "integer", "description": "件数の上限（既定 20、最大 100）"},
        }}},
    }},
    {"toolSpec": {
        "name": "query_metrics",
        "description": "監視メトリクス（Prometheus）に PromQL を投げる。インタフェースの状態やトラフィックの推移を見るのに使う。例: snmp_interface_oper_up{sysName=\"dc1-a-leaf-01\"}",
        "inputSchema": {"json": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "PromQL（メトリクス名は <measurement>_<field>、ラベルは Telegraf のタグ）"},
            "minutes": {"type": "integer", "description": "何分前から見るか（既定 15、最大 1440）"},
        }}},
    }},
    {"toolSpec": {
        "name": "query_history",
        "description": "アラートの通知の履歴（Grafana / Splunk。発火と解消）を機器名で引く。新しい順に最大 50 件で、1 行が 1 つの通知（同じ通知の送り直しは最初に届いた 1 件にまとめる。status = firing / resolved、source = grafana / splunk、starts_at と received_at は UTC、_jst は日本時間）。starts_at は Grafana なら発火した時刻（resolved の行も同じ）、Splunk ならその状態を最後に見た時刻。いつ落ちていつ戻ったか、前にも同じアラートが出ていたかを見るのに使う。まだ配備されていないときは案内だけ返す。",
        "inputSchema": {"json": {"type": "object", "properties": {
            "device_id": {"type": "string", "description": "機器名（例 dc1-a-leaf-01）。空なら全機器"},
            "hours": {"type": "integer", "description": "何時間前まで見るか（既定 24、最大 720）"},
        }}},
    }},
]
TOOLS = {"search_logs": search_logs, "query_metrics": query_metrics, "query_history": query_history}
run_tool = toolkit.runner(TOOLS)
