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
ALERT_DROPPED としてログに出す。
status の正しさを履歴の完全さより優先する（design.md の決定 6）。Neptune と Firehose は片方がエラーを返しても両方を試し、
Neptune への書き込みが 1 件でも失敗したときだけ最後に例外で落とす（Lambda の非同期の再試行に任せる。履歴に二重に入った行は読む側が
event_id で落とす）。Firehose は Lambda の中で合わせて 3 回まで送り直し、それでも届かなかった行は 1 行ずつ JSON のまま ERROR で
ALERT_EVENT_LOST としてログに書いて終わる（例外にすると、Firehose が止まっているあいだ通知のたびに Neptune の書き込みまでやり直しになる。
欠けた行は CloudWatch Logs Insights で `filter @message like /ALERT_EVENT_LOST/` と探して戻せる）。
Neptune を先に書くので、Neptune が応答しないまま Lambda の timeout（60 秒）を使い切ると行もログも残らない。それを防ぐため、Neptune の
クライアントは待ちを短くし（NEPTUNE_CONFIG）、通知ごとに書く前に Lambda の残り時間を見る。Firehose の取り分と Neptune 1 回の最大に
足りなければ、その通知から先は Neptune に書かずに書けなかったものとして扱い（行は送り、最後に例外でやり直す）、WARNING で
NEPTUNE_SKIPPED を 1 回ログに出す。
これで防げるのは Neptune が応答しない（1 回目の問い合わせで落ちる）場合。残り時間は通知ごとに 1 回しか見ないので、遅いが答える Neptune に
1 件の通知が何回も問い合わせる（未登録の IF なら 5 回）と、確認を通ったあとで timeout しうる。
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
# 届かないとき 1 回目の呼び出しだけで Lambda の timeout を使い切り、残った行を ERROR に書く前にタイムアウトする。3 回でも 15.6 秒に収まる
FIREHOSE_CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1, "mode": "standard"})
# Neptune のクライアントもこの Lambda では待ちを短くし、再試行しない（1 回の呼び出しは長くて 13 秒）。agent/graph.py の既定
# （接続 10 秒・読み 60 秒・3 回まで）はエージェントと up.sh が使うので変えず、graph._cache に入れて差し替える
NEPTUNE_CONFIG = Config(connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 1, "mode": "standard"})
FIREHOSE_SHARE = 20   # Firehose に残す秒数（3 回 ×（接続 2 秒 + 読み 3 秒）+ 待ち 0.6 秒 = 15.6 秒に余裕を足したもの）
# Lambda の残りがこれより少なければ、その通知から先は Neptune に書かない（Firehose の取り分 + Neptune 1 回の最大 = 33 秒）
NEPTUNE_BUDGET_MS = (FIREHOSE_SHARE + NEPTUNE_CONFIG.connect_timeout + NEPTUNE_CONFIG.read_timeout) * 1000
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


def _put(stream: str, rows: list) -> list:
    """put_record_batch を 1 回。戻り値は届かなかった行（FailedPutCount が 0 なら空、例外なら全部）"""
    try:
        r = _firehose().put_record_batch(
            DeliveryStreamName=stream, Records=[{"Data": json.dumps(row, ensure_ascii=False).encode()} for row in rows])
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
            log.error("ALERT_EVENT_LOST %s", json.dumps(row, ensure_ascii=False))
        lost += len(left)
    return lost


def handler(event, context=None):
    """SNS からの呼び出し（Records[].Sns.Message）。Neptune への書き込みと Firehose への送信を両方試し、
    Neptune への書き込みが失敗していれば最後に RuntimeError で落として Lambda の非同期の再試行に任せる（Firehose の失敗では落とさない）。
    context（Lambda が渡す）の残り時間が NEPTUNE_BUDGET_MS より少なくなったら、その通知から先は Neptune に書かず、書けなかったものとして数える"""
    results, rows, errors = [], [], []
    received_at = time.time()
    remaining = getattr(context, "get_remaining_time_in_millis", None)   # context が無い（手で呼んだ）ときは見ない
    skipped = 0
    for rec in event.get("Records") or []:
        message = (rec.get("Sns") or {}).get("Message", "")
        alerts = rules.alerts_from_message(message)
        dropped = rules.alert_count(message) - len(alerts)
        if dropped:
            log.warning("ALERT_DROPPED device_id か kind が無い、または status が firing / resolved でない通知を %d 件捨てた（履歴にも残らない）: %s",
                        dropped, str(message)[:300])
        elif not alerts:
            log.warning("読めないメッセージ（捨てる）: %s", str(message)[:300])
        if not alerts:
            continue
        for a in alerts:
            rows.append(rules.alert_event(a, received_at))
            line = json.dumps({k: a.get(k) for k in ("source", "status", "device_id", "kind", "target")}, ensure_ascii=False)
            left = remaining() if remaining and not skipped else None
            if skipped or (left is not None and left < NEPTUNE_BUDGET_MS):
                if not skipped:
                    log.warning("NEPTUNE_SKIPPED Lambda の残りが %d ミリ秒で %d ミリ秒（Firehose の取り分 + Neptune 1 回の最大）に足りないので、"
                                "この通知から先は Neptune に書かない（履歴の行は送り、最後に例外でやり直す）: %s", left, NEPTUNE_BUDGET_MS, line)
                skipped += 1
                continue
            try:
                _neptune()
                r = apply(a)
            except Exception as e:  # noqa: BLE001 - 1 件が落ちても残りの通知と Firehose は試す
                log.exception("Neptune に書けなかった: %s", line)
                errors.append(f"neptune {line}: {type(e).__name__}: {e}")
                continue
            if r.get("unregistered"):
                log.warning("UNREGISTERED 未登録の機器・インタフェースの異常（トポロジに登録する）: %s -> %s", line, json.dumps(r, ensure_ascii=False))
            else:
                log.info("%s -> %s", line, json.dumps(r, ensure_ascii=False))
            results.append(r)
    if skipped:   # 先頭に置く（後ろの 2000 文字の切り詰めで消えないように）
        errors.insert(0, f"neptune: Lambda の残り時間が足りず {skipped} 件を書かなかった")
    stream = os.environ.get("ALERT_STREAM", "")
    if rows and stream:
        send_history(stream, rows)
    if errors:
        raise RuntimeError("; ".join(errors)[:2000])
    return results
