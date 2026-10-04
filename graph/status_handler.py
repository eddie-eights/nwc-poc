"""terraform/pipeline/graph の Lambda（<prefix>-graph-status）。Grafana と Splunk のアラート（SNS のトピック <接頭辞>-alerts）を受けて、
Neptune の機器と回線の動的な状態（property status）を書く。設計の「動的なステータス反映（トラップ / ログ → Lambda → Neptune の属性を UP → DOWN）」。

  firing    kind=link_down → 機器 device_id のインタフェース target が付く回線（辺）を DOWN
            kind=bgp_down  → 機器 device_id の BGP のセッション（頂点 bgp_session。target = 相手の IP）を DOWN（gNMI の session-state）
            kind=isis_down → 機器 device_id の IS-IS の隣接（頂点 isis_adjacency。target = サブインタフェース）を DOWN（gNMI の adjacency）
            それ以外（trap）  → 機器（頂点）を ALARM（トラップは機器が落ちた印ではないので DOWN にしない）
  resolved  同じ要素を UP に戻す（trap の解消は、機器が ALARM のときだけ UP。IF の分からない linkDown の DOWN は上書きしない）

メッセージの形は workflow/rules.py の alerts_from_message が読む（ワーカーの starter と同じ読み手。zip に rules.py として同梱する）。
1 通に何件か入っていることがある（Grafana はグループごとに 1 通）。同じ障害を Grafana と Splunk の両方が知らせても、書くのは同じ値なので害は無い。
zip には agent/graph.py も同梱する（openCypher の組み立てと boto3 の neptune-graph はそちら）。グラフの ID は環境変数 NEPTUNE_GRAPH_ID。
トポロジに無い機器やインタフェースは捨てずに「未登録」の頂点として Neptune に残し（graph.set_status）、WARNING で UNREGISTERED を
ログに出す（登録漏れの印。CloudWatch Logs Insights で `filter @message like /UNREGISTERED/` と探す。lab に足した機器は
ops/sync-graph.sh --replace で登録すると、未登録の頂点は置き換わる）。

届いた通知は 1 件 1 行で、アラートの履歴（S3 Tables の alert_events）にも Firehose で送る（環境変数 ALERT_STREAM。空なら送らない。
terraform/pipeline/graph の alert_history）。行は rules.alert_event が組み、Neptune で無視した通知（機器の無いものなど）も送る。
alerts_from_message が捨てた通知（device_id か kind が無い・status が firing / resolved でない）は行にせず、件数を WARNING で
ALERT_DROPPED としてログに出す。行を組めない通知（starts_at が 9999 年を超えるなど、rules.alert_event が例外になるもの）も行にせず、
1 件ずつ ALERT_DROPPED の WARNING に出して Neptune には書く（その 1 件のせいでほかの通知の行と Neptune を落とさない）。ALERT_STREAM が空なら行を組まない。
行は Neptune より先に送る。呼び出しの全部の通知の行を組んで Firehose に送り、そのあと通知ごとに Neptune に書く（行は通知だけから組み、
Neptune の結果を入れないので、先に送れる）。Firehose に使うのは長くても 22 秒ほど（FIREHOSE_CONFIG と RETRY_WAITS。エンドポイントが 3 つの AZ にあれば
28 秒ほど）なので、Neptune が遅くても応答しなくても、Lambda の timeout（60 秒）の前に履歴は残る。
status の正しさを履歴の完全さより優先する（design.md の決定 6）。Firehose は Lambda の中で合わせて 3 回まで送り直し、それでも届かなかった
行は 1 行ずつ JSON のまま ERROR で ALERT_EVENT_LOST としてログに書いて、例外にせず Neptune に進む（例外にすると、Firehose が止まっている
あいだ通知のたびに Neptune の書き込みまでやり直しになる。欠けた行は CloudWatch Logs Insights で `filter @message like /ALERT_EVENT_LOST/`
と探して戻せる）。Neptune への書き込みが 1 件でも失敗したら、残りの通知も書いてから最後に例外で落とす。Neptune の途中で timeout したときも
同じく、Lambda の非同期のやり直しに任せる（status は遅れる。やり直しは書けた通知も流し直すので、そのあいだに届いた通知の値を古い値に
戻すこともある（design.md のリスク 10）。やり直しで履歴に二重に入った行は、読む側が event_id で落とす）。
"""
import json
import logging
import os
import time

import boto3
from botocore.config import Config

import graph
import rules
import toolkit

log = logging.getLogger()
log.setLevel(logging.INFO)

STATUS_OF = {"firing": "DOWN", "resolved": "UP"}
LAYER_KIND = {"bgp_down": "bgp", "isis_down": "isis"}   # アラートの kind → graph.set_layer_status の kind（頂点の id の真ん中）
BATCH = 500   # put_record_batch の 1 回の上限（件数）。SNS の 1 通は多くて 50 件（Splunk）なので、ふつうは 1 回で済む
RETRY_WAITS = (0.2, 0.4)   # Firehose の送り直しの前に待つ秒数。1 回目と合わせて 3 回まで試す
# Firehose のクライアントは botocore の再試行を切り、早めにあきらめる。既定（5 回まで・接続の待ちが 60 秒）のままだと、エンドポイントに
# 届かないとき 1 回目の呼び出しだけで Lambda の timeout を使い切り、残った行を ERROR に書く前に（Neptune にも書かずに）タイムアウトする。
# 3 回でも 3 ×（接続 2 秒 + 読み 3 秒）+ 待ち 0.6 秒 = 15.6 秒に収まり、60 秒のうち 44 秒は Neptune に残る。接続の待ちはエンドポイントの
# IP ごとにかかるので、エンドポイントが 2 つの AZ にあるとき（endpoints_az_num = 2）は 3 ×（2 × 2 + 3）+ 0.6 = 21.6 秒、残りは 38 秒。
# 3 つの AZ なら 3 ×（3 × 2 + 3）+ 0.6 = 27.6 秒
FIREHOSE_CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1, "mode": "standard"})
# Neptune のクライアントもこの Lambda では待ちを短くし、試すのは 2 回まで（使い回した接続が向こうで切れていた（keep-alive の切れ）
# ときを 1 回は救う。1 回の呼び出しは長くて 2 ×（接続 3 秒 + 読み 10 秒）+ 再試行の前の待ち 1 秒 = 27 秒、エンドポイントが 2 つの AZ に
# あれば 2 ×（2 × 3 + 10）+ 1 = 33 秒。Firehose の 21.6 秒と足しても 60 秒に収まるのは問い合わせ 1 回まで。通知 1 件は 1〜5 回問い合わせる。
# 3 つの AZ なら 2 ×（3 × 3 + 10）+ 1 = 39 秒で、Firehose の 27.6 秒と足すと 66.6 秒になり、問い合わせ 1 回でも 60 秒を超える。
# そのときは ops/up.sh が注意を出す（2026-10-05 のユーザー決定。timeout とここの待ちは変えない））。agent/graph.py の既定
# （接続 10 秒・読み 60 秒・3 回まで）はエージェントと up.sh が使うので変えず、graph._cache に入れて差し替える
NEPTUNE_CONFIG = Config(connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 2, "mode": "standard"})
_cache = {"firehose": None, "neptune": None}   # toolkit._clients とは分ける（toolkit.client("firehose") が先に既定の設定で作ったものを拾わない）


def apply(alert: dict) -> dict:
    """アラート 1 件を Neptune に反映する。戻り値は graph.set_status の結果（ignored のときは理由）"""
    status = STATUS_OF.get(alert.get("status"))
    if status is None:
        return {"ignored": f"status {alert.get('status')}"}
    device_id = str(alert.get("device_id") or "")
    if not device_id or device_id == "?":
        return {"ignored": "device_id が無い"}
    kind, target = alert.get("kind"), str(alert.get("target") or "")
    if kind in LAYER_KIND:
        if not target or target == "?":
            return {"ignored": f"{kind} の target が無い"}
        return graph.set_layer_status(device_id, LAYER_KIND[kind], target, status)
    if kind == "link_down" and target and target != "?":
        return graph.set_status(device_id, target, status)
    if kind == "link_down":
        return graph.set_status(device_id, "", status)   # どのインタフェースか分からない linkDown は機器に付ける
    if status == "DOWN":
        return graph.set_status(device_id, "", "ALARM")
    # trap は TTL で閉じる（splunk/ の保存済みサーチ）。そのあいだに機器が DOWN になっていたら、それは linkDown の印なので残す
    return graph.set_status(device_id, "", "UP", only_if="ALARM")


def _firehose():
    """Firehose のクライアント（FIREHOSE_CONFIG で 1 つだけ作る）"""
    if _cache["firehose"] is None:
        _cache["firehose"] = boto3.client("firehose", region_name=toolkit.REGION, config=FIREHOSE_CONFIG)
    return _cache["firehose"]


def _neptune() -> None:
    """graph.py が使う neptune-graph のクライアントを、NEPTUNE_CONFIG で 1 つだけ作ったものに差し替える"""
    if _cache["neptune"] is None:
        _cache["neptune"] = boto3.client("neptune-graph", region_name=toolkit.REGION, config=NEPTUNE_CONFIG)
    graph._cache["client"] = _cache["neptune"]


def _dumps(row: dict) -> str:
    """行の JSON。UTF-8 にできない文字（孤立したサロゲート）を含む行だけ \\u でエスケープする（その 1 行のせいで、バッチ全体の送信と ERROR の書き出しを落とさない）"""
    s = json.dumps(row, ensure_ascii=False)
    try:
        s.encode()
    except UnicodeEncodeError:
        return json.dumps(row)
    return s


def _put(stream: str, rows: list) -> list:
    """put_record_batch を 1 回。戻り値は届かなかった行（FailedPutCount が 0 なら空、例外なら全部）"""
    try:
        r = _firehose().put_record_batch(
            DeliveryStreamName=stream, Records=[{"Data": _dumps(row).encode()} for row in rows])
    except Exception as e:  # noqa: BLE001 - 何で落ちても全部を送り直す
        log.warning("Firehose %s に送れなかった（%d 件）: %s: %s", stream, len(rows), type(e).__name__, e)
        return rows
    if not r.get("FailedPutCount"):
        return []
    answers = r.get("RequestResponses") or []
    # RequestResponses は Records と同じ順で 1 件ずつ返り、失敗した分にだけ ErrorCode が付く。数が合わなければ全部を送り直す
    failed = [row for row, x in zip(rows, answers) if x.get("ErrorCode")] if len(answers) == len(rows) else []
    codes = sorted({x.get("ErrorCode") for x in answers if x.get("ErrorCode")})
    log.warning("Firehose %s に %d / %d 件が届かなかった: %s", stream, r["FailedPutCount"], len(rows), codes)
    return failed or rows


def send_history(stream: str, rows: list) -> int:
    """alert_events の行を Firehose に送る（500 件ずつ）。届かなかった行だけを、1 回目と合わせて 3 回まで送り直す。
    それでも残った行は 1 行ずつ JSON のまま ERROR でログに書き、例外は投げない。戻り値は届かなかった行の数"""
    lost = 0
    for i in range(0, len(rows), BATCH):
        left = _put(stream, rows[i:i + BATCH])
        for wait in RETRY_WAITS:
            if not left:
                break
            time.sleep(wait)
            left = _put(stream, left)
        for row in left:
            log.error("ALERT_EVENT_LOST %s", _dumps(row))
        lost += len(left)
    return lost


def handler(event, context=None):
    """SNS からの呼び出し（Records[].Sns.Message）。全部の通知の行を組んで先に Firehose へ送り、そのあと通知ごとに Neptune に書く。
    Neptune への書き込みが失敗していれば最後に RuntimeError で落として Lambda の非同期の再試行に任せる（Firehose の失敗では落とさない）"""
    alerts, rows = [], []
    received_at = time.time()
    stream = os.environ.get("ALERT_STREAM", "")
    for rec in event.get("Records") or []:
        message = (rec.get("Sns") or {}).get("Message", "")
        got = rules.alerts_from_message(message)
        dropped = rules.alert_count(message) - len(got)
        if dropped:
            log.warning("ALERT_DROPPED device_id か kind が無い、または status が firing / resolved でない通知を %d 件捨てた（履歴にも残らない）: %s",
                        dropped, str(message)[:300])
        elif not got:
            log.warning("読めないメッセージ（捨てる）: %s", str(message)[:300])
        alerts.extend(got)
        if not stream:   # ALERT_STREAM が空なら行を組まない（履歴の無い配備では、このサイクルの前と同じ動き）
            continue
        for a in got:
            try:
                rows.append(rules.alert_event(a, received_at))
            except Exception as e:  # noqa: BLE001 - 行を組めない 1 件のせいで、ほかの通知の行と Neptune を落とさない
                log.warning("ALERT_DROPPED 行を組めない通知を履歴に残さない（Neptune には書く）: %s: %s: %s", type(e).__name__, e,
                            json.dumps({k: a.get(k) for k in ("source", "status", "device_id", "kind", "target", "starts_at")}))
    if rows:   # Neptune より先に送る（Neptune が遅くても応答しなくても、timeout の前に履歴は残る）
        send_history(stream, rows)
    results, errors = [], []
    for a in alerts:
        line = json.dumps({k: a.get(k) for k in ("source", "status", "device_id", "kind", "target")}, ensure_ascii=False)
        try:
            _neptune()
            r = apply(a)
        except Exception as e:  # noqa: BLE001 - 1 件が落ちても残りの通知は書く
            log.exception("Neptune に書けなかった: %s", line)
            errors.append(f"neptune {line}: {type(e).__name__}: {e}")
            continue
        if r.get("unregistered"):
            log.warning("UNREGISTERED 未登録の機器・インタフェースの異常（トポロジに登録する）: %s -> %s", line, json.dumps(r, ensure_ascii=False))
        else:
            log.info("%s -> %s", line, json.dumps(r, ensure_ascii=False))
        results.append(r)
    if errors:
        raise RuntimeError("; ".join(errors)[:2000])
    return results
