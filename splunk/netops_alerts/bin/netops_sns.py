"""Splunk のアラートアクション netops_sns。保存済みサーチ（default/savedsearches.conf）の結果を SNS のトピックへ publish する。

Splunk は `python netops_sns.py --execute` で起こし、標準入力に JSON（results_file = 結果の CSV（gzip）の場所 など）を渡す。
結果の 1 行 = アラート 1 件で、列は device / kind / target / status（firing | resolved）/ detail / starts_at（epoch 秒）。
publish する JSON は Grafana（grafana/provisioning/alerting）と同じ形で、workflow/rules.py の alerts_from_message と graph/status_handler.py が読む:
  {"source": "splunk", "alerts": [{"status", "device_id", "kind", "target", "detail", "starts_at"}, …]}

- 機器名: gNMI と trap のイベントは機器名でなく管理 IP（tags.source）を持つ。DEVICE_MAP（別名=機器名,…）で名前に直す
- 認証: ECS のタスクロール（AWS_CONTAINER_CREDENTIALS_RELATIVE_URI から一時的な認証情報を取る）。アクセスキーは置かない
- 設定: コンテナの環境変数を splunk/entrypoint.sh がファイルに写したもの（splunkd の子プロセスはコンテナの環境変数を引き継がない）。
  boto3 には環境変数でなく引数で渡す（認証情報の口・リージョン・AWS_ENDPOINT_URL_SNS）
- ライブラリ: boto3 を app の lib/ に同梱する（splunk/Dockerfile がイメージのビルドのときに入れる。Splunk の Python に pip で入れない。
  VPC から PyPI へは出られない）。boto3 は publish のときに読むので、boto3 の無い PC でもテストできる

失敗は stderr に "ERROR …" で書いて 0 以外で終わる（Splunk が splunkd.log の sendmodalert に残す）。Splunk は打ち直さないので、ここで 3 回まで試す
"""
import csv
import gzip
import json
import os
import re
import sys
import time

ENV_FILE = "/opt/container_artifact/nwc-alerts.env"   # splunk/entrypoint.sh が書く
ENV_KEYS = ("AWS_REGION", "ALERTS_TOPIC_ARN", "DEVICE_MAP", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_ENDPOINT_URL_SNS")
APP_LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")   # boto3 など（splunk/Dockerfile が入れる）
STATUSES = ("firing", "resolved")
MAX_ALERTS = 50      # 1 通に入れる件数（SNS の本文は 256 KB まで。1 件は数百バイト）
ATTEMPTS = 3
TIMEOUT = 10
SUBJECT = "netops alert"
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def log(level, text):
    sys.stderr.write(f"{level} {text}\n")


def load_env(path=ENV_FILE, environ=None):
    """設定を読む。ファイル（KEY=VALUE の行。値が空の行は無いのと同じ）が土台で、プロセスの環境変数に同じ名前があればそちら"""
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.rstrip("\n").partition("=")
                if sep and k in ENV_KEYS and v:
                    env[k] = v
    except OSError:
        pass
    for k in ENV_KEYS:
        v = (os.environ if environ is None else environ).get(k)
        if v:
            env[k] = v
    return env


def parse_device_map(text):
    """"203.0.113.31=dc1-leaf-01,dc1-leaf-01.example.net=dc1-leaf-01" → {別名（小文字）: 機器名}。= の無い要素は捨てる"""
    out = {}
    for p in (text or "").split(","):
        k, sep, v = p.partition("=")
        if sep and k.strip() and v.strip():
            out[k.strip().lower()] = v.strip()
    return out


def device_name(name, devmap):
    """機器名をトポロジの device_id に揃える。小文字にして device map を引き、無ければドメインを落とす（IPv4 はそのまま）"""
    name = str(name or "").strip().lower()
    if not name:
        return ""
    short = name if IPV4_RE.match(name) else name.split(".", 1)[0]
    return devmap.get(name) or devmap.get(short) or short


def _epoch(v):
    try:
        return max(int(float(v or 0)), 0)
    except (TypeError, ValueError):
        return 0


def alerts_from_rows(rows, devmap):
    """サーチの結果の行 → アラートの list。機器か種類が無い行、status が firing / resolved でない行は捨てる"""
    out = []
    for r in rows:
        dev, kind = device_name(r.get("device"), devmap), str(r.get("kind") or "").strip()
        status = str(r.get("status") or "").strip().lower()
        if not dev or not kind or status not in STATUSES:
            continue
        out.append({"status": status, "device_id": dev, "kind": kind, "target": str(r.get("target") or "").strip(),
                    "detail": str(r.get("detail") or "")[:1000], "starts_at": _epoch(r.get("starts_at"))})
    return out


def messages(alerts, size=MAX_ALERTS):
    """publish する本文の list（MAX_ALERTS 件ごとに 1 通）"""
    return [json.dumps({"source": "splunk", "alerts": alerts[i:i + size]}, ensure_ascii=False, separators=(",", ":"))
            for i in range(0, len(alerts), size)]


def read_rows(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def topic_region(topic_arn, default=""):
    """arn:aws:sns:<region>:<account>:<name> の region"""
    parts = (topic_arn or "").split(":")
    return parts[3] if len(parts) >= 6 and parts[3] else default


def sns_client(env):
    """SNS のクライアント（boto3）。認証情報は ECS のタスクロールの口（env の AWS_CONTAINER_CREDENTIALS_RELATIVE_URI。手元で偽の口に向けるときは
    AWS_CONTAINER_CREDENTIALS_FULL_URI）からだけ取り、アクセスキーの環境変数・~/.aws・IMDS へは逃げない。値はログに出さない。
    リージョンはトピックの ARN から（分からないときだけ AWS_REGION）、宛先は AWS_ENDPOINT_URL_SNS があればそこ。
    プロキシの環境変数と、AWS_ENDPOINT_URL などの宛先の設定は見ない（認証情報の口は 169.254.170.2、SNS は VPC のエンドポイント）。
    試し直しは send がする（boto3 の中では打ち直さない）"""
    if APP_LIB not in sys.path:
        sys.path.insert(0, APP_LIB)
    import boto3
    from botocore.config import Config
    from botocore.credentials import ContainerProvider
    creds = ContainerProvider(environ=env).load()
    if creds is None:
        raise RuntimeError("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI が無い（ECS のタスクロールが付いていない）")
    c = creds.get_frozen_credentials()
    session = boto3.session.Session(aws_access_key_id=c.access_key, aws_secret_access_key=c.secret_key, aws_session_token=c.token or None,
                                    region_name=topic_region(env["ALERTS_TOPIC_ARN"], env.get("AWS_REGION", "")) or None)
    config = Config(connect_timeout=TIMEOUT, read_timeout=TIMEOUT, retries={"total_max_attempts": 1}, proxies={},
                    ignore_configured_endpoint_urls=True)
    return session.client("sns", endpoint_url=env.get("AWS_ENDPOINT_URL_SNS") or None, config=config)


def describe(e):
    """失敗を 1 行に。SNS が断った（botocore の ClientError）なら HTTP の状態とエラーコード。認証情報の値は入らない"""
    r = getattr(e, "response", None)
    if isinstance(r, dict) and isinstance(r.get("Error"), dict):
        status = (r.get("ResponseMetadata") or {}).get("HTTPStatusCode", "?")
        return f"HTTP {status} {r['Error'].get('Code', '')}: {str(r['Error'].get('Message', ''))[:300]}"
    return f"{type(e).__name__}: {str(e)[:300]}"


def send(env, texts, sleep=time.sleep, connect=None):
    """本文を順に publish する。失敗したらクライアントを作り直して（認証情報を取り直して）ATTEMPTS 回まで試す。送れた通数を返す"""
    connect = connect or sns_client
    client, sent = None, 0
    for text in texts:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                client = client or connect(env)
                client.publish(TopicArn=env["ALERTS_TOPIC_ARN"], Subject=SUBJECT, Message=text)
                sent += 1
                break
            except ImportError:
                raise   # app の lib/ に boto3 が無い（イメージの作り方の誤り）。試し直しても直らない
            except Exception as e:   # boto3 の失敗はどれも試し直す（ClientError = SNS が断った、BotoCoreError = 通信・認証情報の口）
                client, err = None, describe(e)
            log("ERROR", f"publish に失敗した（{attempt}/{ATTEMPTS}）: {err}")
            if attempt < ATTEMPTS:
                sleep(attempt)
    return sent


def main(argv, stdin):
    if len(argv) < 2 or argv[1] != "--execute":
        log("FATAL", "Unsupported execution mode (expected --execute flag)")
        return 1
    try:
        payload = json.loads(stdin.read())
        env = load_env()
        if not env.get("ALERTS_TOPIC_ARN"):
            log("ERROR", f"ALERTS_TOPIC_ARN が無い（{ENV_FILE} とコンテナの環境変数）")
            return 2
        rows = read_rows(payload["results_file"])
        alerts = alerts_from_rows(rows, parse_device_map(env.get("DEVICE_MAP", "")))
        texts = messages(alerts)
        sent = send(env, texts)
        log("INFO", f"search={payload.get('search_name')} rows={len(rows)} alerts={len(alerts)} published={sent}/{len(texts)}")
        return 0 if sent == len(texts) else 2
    except Exception as e:   # Splunk に traceback を流さない（1 行で残す）
        log("ERROR", f"Unexpected error: {type(e).__name__}: {e}")
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin))
