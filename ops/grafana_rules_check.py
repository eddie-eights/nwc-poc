"""ops/up.sh・oss/ops/up.sh の最後と ops/check-grafana.sh が Web の EC2 の上で打つ。Grafana のアラートルールが評価でエラーになっていないかを確かめる。

Grafana のルールの API（/api/prometheus/grafana/api/v1/rules。スケジューラーの評価の結果）を読む。ルールは execErrState: KeepLast なので、
評価がエラーでもルールの health は ok、state は inactive のままで、エラーは alerts[].state の「Normal (Error, KeepLast)」にだけ出る
（AWS 検証で見つけた不具合 3 件を直す（008）で実測。lastError も空）。それで alerts[].state に「(Error」があるか「Error」で始まるもの、
health が error、lastError が空でないものをエラーとする。KeepLast はエラーの理由を API に残さないので、理由は Grafana のログ
（ロググループ /ecs/<prefix>-grafana の「Failed to evaluate rule」）を見る。

Web と同じ環境変数（/etc/<prefix>-web.env）と依存（/opt/<prefix>-web/lib の boto3）で動かす。admin のパスワードは SSM の
/<prefix>/grafana/admin-password から読む（表示しない）。Grafana は Cloud Map の grafana.<prefix>.internal:3000（SG で Web から届く）。

確かめ始めてから全部のルールがもう 1 回評価されるまで 10 秒おきに読み直す（立てた直後は Grafana が起動中か、まだ評価していない。
最初に読めたときの評価は、データソースを直す前のものかもしれないので判定に使わない。ルールの間隔は 1 分なので最大 1 分ほど延びる）。
エラーのあったルールは、もう 1 回評価されてもエラーなら NG にする。エラーの評価が作った状態は、直したあとの成功した評価に 1 回分残り、
その次の評価で消える（Grafana の stale の扱い。13.2.2 で実測）ので、NG は 1 分ほど遅れて出る。
最後の行は「判定: OK / NG / 未確認 …」。終了コードは 0（エラーのルールが無い）/ 1（エラーのルールがある）/ 2（確かめられなかった）。

環境変数:
  NAME_PREFIX   **必須。**リソース名の接頭辞（<owner>-nwc-poc か <owner>-nwc-oss。呼ぶ側が渡す）。Web の置き場、Grafana の名前、SSM の名前に入る
  GRAFANA_WAIT  全部のルールが評価されるのを待つ秒数（既定 300）
"""
import base64
import collections
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request

INTERVAL = 10


def load_web_env(prefix):
    # systemd の EnvironmentFile と同じく「名前=値」を 1 行ずつ読む（ops/seed_graph.py と同じ）
    with open(f"/etc/{prefix}-web.env", encoding="utf-8") as f:
        for line in f:
            key, sep, value = line.rstrip("\n").partition("=")
            if sep and key and not key.startswith("#"):
                os.environ[key] = value
    sys.path[:0] = [f"/opt/{prefix}-web/src", f"/opt/{prefix}-web/lib"]


def admin_password(prefix):
    import boto3

    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
    return ssm.get_parameter(Name=f"/{prefix}/grafana/admin-password", WithDecryption=True)["Parameter"]["Value"]


class Unauthorized(Exception):
    pass


def make_fetch(base_url, password):
    """ルールの API を 1 回読む関数を返す。パスワードは Authorization ヘッダーにだけ入れ、例外の文にも出さない"""
    auth = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Grafana は VPC の中。プロキシを通さない

    def fetch():
        req = urllib.request.Request(f"{base_url}/api/prometheus/grafana/api/v1/rules")
        req.add_unredirected_header("Authorization", auth)  # リダイレクト先には送らない
        try:
            with opener.open(req, timeout=10) as r:
                body = json.load(r)
            if not isinstance(body, dict):
                raise ValueError(f"JSON のオブジェクトでない（{type(body).__name__}）")
            return body
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise Unauthorized(f"HTTP {e.code}（admin のパスワードが Grafana と SSM で違う）") from None
            raise

    return fetch


def rules_of(body):
    return [(g.get("name") or "", r) for g in (body.get("data") or {}).get("groups") or [] for r in g.get("rules") or []]


def evaluated(rule):
    ev = rule.get("lastEvaluation") or ""
    return bool(ev) and not ev.startswith("0001-")


def problems(rule):
    out = []
    if rule.get("health") == "error":
        out.append("health=error")
    if rule.get("lastError"):
        out.append(f"lastError={rule['lastError'][:300]}")
    bad = collections.Counter(st for st in ((a.get("state") or "") for a in rule.get("alerts") or [])
                              if "(Error" in st or st.startswith("Error"))
    out += [f"{st} ×{n}" for st, n in sorted(bad.items())]
    return out


def line(group, rule):
    states = collections.Counter((a.get("state") or "?") for a in rule.get("alerts") or [])
    shown = "、".join(f"{st} ×{n}" for st, n in sorted(states.items())) or "なし"
    return (f"{group}/{rule.get('name')}: health={rule.get('health')} state={rule.get('state')} "
            f"評価={rule.get('lastEvaluation') or 'まだ'} alerts={shown}")


def check(fetch, wait, clock=time.monotonic, sleep=time.sleep, out=print):
    """ルールの API を読み、確かめ始めてから全部のルールが評価されたら判定する。0 / 1 / 2 を返す"""
    deadline = clock() + wait
    first = None  # 最初に読めたときの各ルールの lastEvaluation。これと違う評価（新しい評価）だけで判定する
    erred = {}  # 新しい評価でエラーがあったルールの、その評価の lastEvaluation。これと違う評価でもエラーなら NG

    def fresh(group, rule):
        return evaluated(rule) and rule.get("lastEvaluation") != first.get((group, rule.get("name")))

    while True:
        why = ""
        try:
            rules = rules_of(fetch())
        except Unauthorized as e:
            out(f"判定: 未確認（Grafana のルールの API が {e}）")
            return 2
        except (OSError, ValueError, http.client.HTTPException) as e:  # 起動中（つながらない・502・途中で切れる）と、JSON でない応答。HTTPError も URLError も OSError
            rules, why = None, f"Grafana のルールの API が読めない（{type(e).__name__}: {e}）"
        if rules is not None:
            if first is None:
                first = {(g, r.get("name")): r.get("lastEvaluation") or "" for g, r in rules}
            if not rules:
                why = "ルールが 0 本（app/grafana/start.sh が ALERTS_TOPIC_ARN とデータソースの URL のあるときだけ並べる）"
            elif all(fresh(g, r) for g, r in rules):
                bad = [(f"{g}/{r.get('name')}", p) for g, r in rules if (p := problems(r))]
                # エラーを初めて見た評価なら覚えて待つ（setdefault）。覚えた評価と違う評価でもエラーなら確か
                once = [f"{g}/{r.get('name')}" for g, r in rules if problems(r)
                        and erred.setdefault((g, r.get("name")), r.get("lastEvaluation")) == r.get("lastEvaluation")]
                if not once:
                    for g, r in rules:
                        out(line(g, r))
                    if not bad:
                        out(f"判定: OK（{len(rules)} 本とも評価のエラーなし）")
                        return 0
                    for name, p in bad:
                        out(f"エラー: {name}（{'、'.join(p)}）")
                    names = "、".join(name for name, _ in bad)
                    out(f"判定: NG（{len(rules)} 本のうち {len(bad)} 本の評価がエラー: {names}）")
                    return 1
                why = f"エラーのあったルールのもう 1 回の評価を待っている（直した直後は前の評価のエラーが 1 回分残る）: {'、'.join(once)}"
            else:
                waiting = [f"{g}/{r.get('name')}" for g, r in rules if not fresh(g, r)]
                why = f"確かめ始めてから評価されていないルール: {'、'.join(waiting)}"
        if clock() >= deadline:
            out(f"判定: 未確認（{wait} 秒待った。{why}）")
            return 2
        sleep(INTERVAL)


def main():
    prefix = os.environ.get("NAME_PREFIX") or sys.exit("NAME_PREFIX（リソース名の接頭辞 <owner>-nwc-poc）が要る")
    wait = int(os.environ.get("GRAFANA_WAIT") or 300)
    load_web_env(prefix)
    try:
        password = admin_password(prefix)
    except Exception as e:  # noqa: BLE001  boto3 の例外の型は lib を読んでからでないと分からない
        print(f"判定: 未確認（SSM の /{prefix}/grafana/admin-password が読めない: {type(e).__name__}）")
        return 2
    return check(make_fetch(f"http://grafana.{prefix}.internal:3000", password), wait)


if __name__ == "__main__":
    sys.exit(main())
