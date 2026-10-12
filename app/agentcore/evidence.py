"""調査の証拠を取るツール（WORKFLOW。エージェントが原因分析のために OpenSearch / Prometheus / S3 Tables を見に行く）。

Grafana / Splunk のアラート（2026-10-02 までは Spark が検知していた）を受けて、エージェントが Neptune / S3 Tables / OpenSearch / Prometheus を見に行って原因を分析する。
Neptune は graph.py / topology.py（neighbors / blast_radius）、ここは残りの 3 つ:
  search_logs    OpenSearch Serverless の logs コレクション（IaC/terraform/aws-managed/pipeline/analytics の sinks=opensearch。Spark が traps と logs（機器の syslog）を書く）を機器名で検索
  query_metrics  Amazon Managed Service for Prometheus（sinks=prometheus。Spark が metrics を remote write）に PromQL を投げる
  query_history  S3 Tables（Iceberg）の alert_events（Grafana / Splunk のアラートの通知。graph の status Lambda → Firehose が追記）を Athena で読む（2026-10-04）
  search_splunk  Splunk（sinks=splunk。Spark が全トピックを HEC へ書く）を決めた SPL で検索する（cycle 054）。STORES に grafana が無く
                 search_logs / query_metrics が「配備されていない」構成でも、ログ・trap・メトリクスを引ける。エージェントは SPL を書けない

エンドポイントは環境変数 OPENSEARCH_ENDPOINT（https://...aoss.amazonaws.com）/ OPENSEARCH_INDEX（既定 snmp-logs）/
PROMETHEUS_QUERY_URL（https://aps-workspaces.<region>.amazonaws.com/workspaces/<id>/api/v1/query）/
ATHENA_WORKGROUP・ATHENA_CATALOG・HISTORY_NAMESPACE・ALERT_EVENTS_TABLE（IaC/terraform/aws-managed/pipeline/analytics の history.tf の出力）。
SPLUNK_URL（https://splunk.<prefix>.internal:8089。analytics の splunk_search_url。1 台ならその 1 台、クラスターなら search head）/ SPLUNK_USER（既定 admin）。
Splunk のパスワードは環境変数 SPLUNK_PASSWORD か SSM の <PARAM_PREFIX>/splunk/admin-password（SecureString。ops/up.sh が作る）。
Splunk の 8089 は自己署名の証明書なので検証しない（app/splunk/peers_check.py と同じ。宛先は VPC の中で、SG は lambda からだけ開ける）。
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
import http.client
import json
import os
import re
import ssl
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
SPLUNK_URL = os.environ.get("SPLUNK_URL", "").rstrip("/")
SPLUNK_USER = os.environ.get("SPLUNK_USER") or "admin"
SPLUNK_PASSWORD = toolkit.Param("SPLUNK_PASSWORD", "splunk/admin-password", decrypt=True)
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


# ---------------------------------------------------------------- Splunk の検索（cycle 054）
SPLUNK_NOT_DEPLOYED = "Splunk の検索はまだ配備されていない（IaC/terraform/aws-managed/pipeline/analytics を STORES に splunk を入れて apply すると使える）"
SPLUNK_TOPICS = ("metrics", "gnmi", "traps", "logs", "flows")  # sourcetype は nwc:<topic>（Spark の splunk_events）
SPLUNK_DEVICE_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")
SPLUNK_KEYWORD_MAX = 100
# SPL の引用を抜け出す・サブサーチ・マクロ・パイプを作れる文字。エスケープせずに拒む（SPL の規則の読み違いに頼らない）。制御文字は isprintable で拒む
SPLUNK_KEYWORD_BAD = frozenset('"\\|[]`')
SPLUNK_RAW_MAX = 1000
# search_splunk の全体（パスワードの取得・接続・読み出し）の締め切り（秒）。Runtime → Gateway の mcp_client.GATEWAY_TIMEOUT 30 秒より短く。
# jobs/export は検索が終わるまで流し続けるので、過ぎたら読むのをやめて、読めた行（改行で終わった行）だけを truncated 付きで返す
SPLUNK_DEADLINE = 25
SPLUNK_CHUNK = 64 * 1024
# Runtime のコンテナには SPLUNK_URL が無く、8089 も lambda → splunk しか開けない。app.run_tool は Gateway のエラーを
# コンテナの「配備されていない」で上書きしない（cycle 054 のセルフレビュー）
GATEWAY_ONLY = frozenset({"search_splunk"})


def splunk_args(device_id: str = "", topic: str = "", keyword: str = "", minutes: int = 60, limit: int = 20) -> tuple[str, str]:
    """引数を検証して、決めた形の SPL を組み立てる。(spl, "") か、通らなければ ("", エラーの文)。
    minutes / limit は search_logs と同じく int() に直して丸める（整数にできなければ ValueError。run_tool が error にする）"""
    device_id, topic, keyword = str(device_id or "").strip(), str(topic or "").strip(), str(keyword or "").strip()
    minutes = max(1, min(int(minutes), 24 * 60))
    limit = max(1, min(int(limit), 100))
    if device_id and not SPLUNK_DEVICE_RE.fullmatch(device_id):
        return "", "device_id に使えない文字がある（英数字と . _ : - だけ、64 文字まで）"
    if topic and topic not in SPLUNK_TOPICS:
        return "", f"topic は {' / '.join(SPLUNK_TOPICS)} のどれか（空なら全部）: {topic!r}"
    if len(keyword) > SPLUNK_KEYWORD_MAX:
        return "", f"keyword は {SPLUNK_KEYWORD_MAX} 文字まで"
    if any(c in SPLUNK_KEYWORD_BAD or not c.isprintable() for c in keyword):
        return "", "keyword に使えない文字がある（\" \\ | [ ] ` と制御文字は使えない）"
    spl = f'search index=* sourcetype="nwc:{topic or "*"}" earliest=-{minutes}m'
    if device_id:
        # 本文の語で先に絞る（索引で引ける。earliest の範囲を全部読んでから eval で絞らない）。機器名は Spark の splunk_events の
        # 本文の tags に出る（HEC の host も tags.host か agent_host）ので、落ちるのは host が unknown のイベントだけ
        spl += f' "{device_id}"'
    if keyword:
        spl += f' "{keyword}"'
    # 機器名は保存済みサーチと同じ coalesce（app/splunk/nwc_alerts/default/savedsearches.conf）
    spl += " | eval device=coalesce('tags.sysName', 'tags.agent_host', 'tags.source', host)"
    if device_id:
        # 機器名の完全一致と FQDN の sysName（<id>. で始まる）に、機器の IP を持つ欄の完全一致を足す。前方一致にしない
        # （10.0.0.1 が 10.0.0.10 に、spine-1 が spine-10 に当たる）。device map で sysName が付いた機器（trap / gNMI）は device が
        # sysName になるので IP は tags.source / tags.agent_host で、flows は機器名を持たないので送り元の tags.sampler で当てる（cycle 054 Round 3）
        spl += (f' | search device="{device_id}" OR device="{device_id}.*" OR tags.agent_host="{device_id}"'
                f' OR tags.source="{device_id}" OR tags.sampler="{device_id}" OR host="{device_id}"')
    return spl + f" | head {limit} | fields _time device sourcetype source _raw", ""


def _splunk_truncated() -> str:
    return f"{SPLUNK_DEADLINE} 秒で打ち切った（topic / device_id で絞るか minutes を短く）"


def _set_read_timeout(res, seconds: float) -> None:
    """読み出しのソケットの timeout を締め切りの残りに合わせる（open の timeout は接続のときの値のまま残るため）。
    http.client.HTTPResponse の fp.raw._sock（socket.SocketIO の私有の属性）。見つからなければ何もしない（open のときの値のまま）"""
    sock = getattr(getattr(getattr(res, "fp", None), "raw", None), "_sock", None)
    if sock is not None:
        sock.settimeout(seconds)


def _splunk_post(url: str, body: bytes, password: str, deadline: float) -> tuple[list[str], str, str]:
    """Splunk の管理 API に POST して、(改行で終わった行, 打ち切ったときの文 か "", "") か ([], "", エラーの文)。
    エラーの文は _send と同じ形。締め切り deadline（time.monotonic）を過ぎたら読むのをやめ、改行で終わっていない行は捨てる。
    Basic 認証・自己署名の証明書を検証しない・プロキシの環境変数を見ない、は app/splunk/peers_check.py と同じ"""
    headers = {"Authorization": "Basic " + base64.b64encode(f"{SPLUNK_USER}:{password}".encode("utf-8")).decode("ascii"),
               "Content-Type": "application/x-www-form-urlencoded"}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # 8089 は Splunk の自己署名の証明書
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))  # 宛先は VPC の中
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return [], _splunk_truncated(), ""
    try:
        res = opener.open(urllib.request.Request(url, data=body, headers=headers, method="POST"), timeout=min(TIMEOUT, remaining))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except (OSError, ValueError, http.client.HTTPException):  # 本文を読む途中で切れても、状態コードは返す
            detail = ""
        finally:
            e.close()
        return [], "", f"HTTP {e.code}: {detail}"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, http.client.HTTPException) as e:  # HTTPException は応答の頭が壊れている（BadStatusLine ほか）
        if time.monotonic() >= deadline:
            return [], _splunk_truncated(), ""
        return [], "", f"届かない: {e}"
    lines, buf, cut = [], b"", ""
    try:
        with res:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cut = _splunk_truncated()
                    break
                wait = min(TIMEOUT, remaining)
                _set_read_timeout(res, wait)
                try:
                    chunk = res.read1(SPLUNK_CHUNK)  # 届いた分だけ返す（read(n) は n バイト揃うまで待つ）
                except TimeoutError:
                    cut = _splunk_truncated() if wait == remaining else f"{TIMEOUT} 秒応答が途切れたので打ち切った（topic / device_id で絞るか minutes を短く）"
                    break
                if not chunk:
                    break
                *done, buf = (buf + chunk).split(b"\n")
                lines.extend(x.decode("utf-8", "replace") for x in done)
    except (OSError, ValueError, http.client.HTTPException) as e:  # 読んでいる途中で切れた（IncompleteRead ほか）
        if not lines:
            return [], "", f"届かない: {e!r}"
        return lines, f"応答が途中で切れた（{e!r}）。読めた分だけ返す", ""  # 読めた行は捨てない（改行で終わっていない行は捨てる）
    if not cut and buf:
        lines.append(buf.decode("utf-8", "replace"))  # 最後まで読めたときは、改行の無い最後の行も 1 行
    return lines, cut, ""


def search_splunk(device_id: str = "", topic: str = "", keyword: str = "", minutes: int = 60, limit: int = 20) -> dict:
    """Splunk の全トピックを決めた SPL で検索する（最大 limit 件）。エージェントは SPL を書けず、引数は検証してから埋める"""
    deadline = time.monotonic() + SPLUNK_DEADLINE  # 全体の締め切り。パスワードの取得（SSM）に使った時間も数える（取得そのものは打ち切れない）
    if not SPLUNK_URL:
        return {"error": SPLUNK_NOT_DEPLOYED, "events": []}
    spl, error = splunk_args(device_id, topic, keyword, minutes, limit)
    if error:
        return {"error": error, "events": []}
    password = SPLUNK_PASSWORD.value()
    if not password:
        return {"error": "Splunk のパスワードが無い（環境変数 SPLUNK_PASSWORD か SSM の splunk/admin-password）", "events": []}
    body = urllib.parse.urlencode({"search": spl, "output_mode": "json"}).encode("utf-8")
    rows, cut, error = _splunk_post(f"{SPLUNK_URL}/services/search/jobs/export", body, password, deadline)
    if error:
        return {"error": error, "events": []}
    # 応答は 1 行 1 件の JSON（{"preview": false, "offset": N, "result": {...}}）。エラーは {"messages": [{"type": "ERROR", "text": ...}]}
    events, problems, lines, skipped = [], [], 0, 0
    for line in rows:
        if not line.strip():
            continue
        lines += 1
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if not isinstance(row, dict):
            skipped += 1
            continue
        for m in row.get("messages") or []:
            if isinstance(m, dict) and str(m.get("type", "")).upper() in ("ERROR", "FATAL"):
                problems.append(f"{str(m.get('type')).upper()} {m.get('text', '')}".strip())
        result = row.get("result")
        if isinstance(result, dict):
            events.append({"time": result.get("_time", ""), "device": result.get("device", ""), "sourcetype": result.get("sourcetype", ""),
                           "source": result.get("source", ""), "raw": str(result.get("_raw", ""))[:SPLUNK_RAW_MAX]})
    if problems:
        return {"error": "Splunk: " + "; ".join(problems), "events": []}
    if cut and not events:
        return {"error": cut, "events": []}
    if lines and skipped == lines:
        return {"error": "Splunk の応答が読めない", "events": []}
    out = {"count": len(events), "minutes": max(1, min(int(minutes), 24 * 60)), "topic": str(topic or "").strip() or "*", "events": events}
    if skipped:
        out["skipped"] = skipped  # JSON として読めなかった行の数
    if cut:
        out["truncated"] = cut  # 締め切りで読むのをやめた（events は途中まで）
    return out


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
    {"toolSpec": {
        "name": "search_splunk",
        "description": "Splunk に入っている全トピック（metrics / gnmi / traps / logs / flows）を機器名・トピック・語で引く。search_logs / query_metrics が配備されていないときに使う。"
                       "まず topic と device_id で絞る（minutes が長く topic が空だと遅い。25 秒で打ち切り、途中までを truncated 付きで返す）。SPL は書けない（決めた形の検索だけ）。STORES に splunk が無い構成では案内だけ返す。",
        "inputSchema": {"json": {"type": "object", "properties": {
            "device_id": {"type": "string", "description": "機器名（例 dc1-a-leaf-01）か機器の IP。完全一致（sysName が FQDN の機器は <id>. で始まるものも当たる）。flows は機器名を持たないので、送り元の機器の IP（sampler）で引く。空なら全機器"},
            "topic": {"type": "string", "description": "metrics / gnmi / traps / logs / flows のどれか。空なら全部"},
            "keyword": {"type": "string", "description": "本文に含む語（例 linkDown）。100 文字まで、\" \\ | [ ] ` は使えない。空なら絞らない"},
            "minutes": {"type": "integer", "description": "何分前まで見るか（既定 60、最大 1440）"},
            "limit": {"type": "integer", "description": "件数の上限（既定 20、最大 100）"},
        }}},
    }},
]
TOOLS = {"search_logs": search_logs, "query_metrics": query_metrics, "query_history": query_history, "search_splunk": search_splunk}
run_tool = toolkit.runner(TOOLS)
