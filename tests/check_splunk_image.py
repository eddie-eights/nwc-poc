"""Splunk のイメージ（docker/images/splunk/Dockerfile）の中で、Splunk の Python が持っている boto3 でアラートを SNS へ送れることを確かめる。
アラートアクション（app/splunk/netops_alerts/bin/netops_sns.py）は boto3 を同梱せず、Splunk の Python のものを使う。Splunk の版
（docker/images/splunk/Dockerfile の SPLUNK_VERSION）を上げると boto3 が無くなる・変わることがあるので、版を変えたらこれを走らせ、通ったら CHECKED を書き換える
（tests/test_alerts.py が CHECKED と Dockerfile の版・python.required を突き合わせるので、書き換えないと ops/check.sh が落ちる）。
boto3 が無くなっていたら、boto3 を app の lib/ に同梱する形（git の ffba169）に戻す。

確かめること（AWS へは出ない。偽の認証情報の口と偽の SNS を、コンテナの中で Splunk の Python で動かす）
  1. 直に: Splunk の Python（alert_actions.conf の python.required の版）で boto3 / botocore を読み、netops_sns の sns_client で
     SNS のクライアントを作り、send で偽の SNS へ 1 通 publish する
  2. 本物の流れで: 入口（app/splunk/entrypoint.sh）で起こした Splunk に HEC で link down のイベントを 1 件入れ、保存済みサーチ（netops_poll）→
     アラートアクション（splunkd が python.required の Python で起こす）→ 偽の SNS に 1 通だけ届く。本文・件名・送った Python と boto3 の版
     （User-Agent）と、splunkd.log の published=1/1 / exit code=0 を見る
  3. boto3 が読めないとき: app の bin/ に読むと失敗する boto3.py を置いて（2 と同じ流れ）、splunkd.log に理由の分かる ERROR が出て、送らないこと
  4. 見えた版（Splunk・Python・boto3）が CHECKED と同じ

ops/check.sh には入れない（1.8 GB のイメージを取ってきて Splunk を起こし、毎分のサーチを 3 回待つので数分かかる。splunk/splunk は amd64 だけなので、
arm64 の PC ではエミュレーションで動かす。2026-10-04 に arm64 の Mac で、healthy まで 80 秒）。
名前が test_*.py でないのはそのため（ops/check.sh は tests/test_*.py を全部走らせる）。
使い方: python3 tests/check_splunk_image.py [イメージ]（省略すると app/splunk/ を linux/amd64 でビルドして nwc-splunk-check:local にする。
イメージは消さない）。docker が要る。手元の Python は標準ライブラリだけ。Splunk の管理者のパスワードと HEC のトークンはその場で作る乱数で、表示しない
"""
import json
import os
import re
import secrets
import subprocess
import sys
import time
import uuid

CHECKED = {"splunk": "10.4.4", "python": "3.13.11", "boto3": "1.37.14"}   # この検査が通った組み合わせ（2026-10-08）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = "nwc-splunk-check:local"
NAME = f"nwc-splunk-check-{os.getpid()}"
APP = "/opt/splunk/etc/apps/netops_alerts"
TOPIC = "arn:aws:sns:ap-northeast-1:111122223333:netops-alerts"
PORT = 18080
POSTS = "/tmp/nwc_posts.jsonl"
SHADOW = f"{APP}/bin/boto3.py"

# 偽の認証情報の口（GET /creds）と偽の SNS（POST /）。コンテナの中で Splunk の Python（標準ライブラリだけ）で動かし、受けたものを 1 行 1 JSON で書く
FAKE = r'''
import json, sys, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PORT, OUT = int(sys.argv[1]), sys.argv[2]
CREDS = json.dumps({"AccessKeyId": "AKIDLOCALTEST", "SecretAccessKey": "local-test-secret", "Token": "local-test-token",
                    "Expiration": "2099-01-01T00:00:00Z"}).encode()
OK = (b'<PublishResponse xmlns="http://sns.amazonaws.com/doc/2010-03-31/"><PublishResult><MessageId>m-1</MessageId></PublishResult>'
      b'<ResponseMetadata><RequestId>r</RequestId></ResponseMetadata></PublishResponse>')
def write(rec):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def reply(self, code, body, ctype):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        write({"kind": "creds", "path": self.path})
        self.reply(200 if self.path == "/creds" else 404, CREDS, "application/json")
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        write({"kind": "publish", "path": self.path, "content_type": self.headers.get("Content-Type"), "user_agent": self.headers.get("User-Agent") or "",
               "token": self.headers.get("X-Amz-Security-Token"), "form": dict(urllib.parse.parse_qsl(body))})
        self.reply(200, OK, "text/xml")
srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
write({"kind": "ready"})
srv.serve_forever()
'''

# 1. 直に: Splunk の Python で boto3 を読み、アラートアクションの sns_client / send で 1 通送る
DIRECT = r'''
import json, sys
sys.path.insert(0, "%s/bin")
import boto3, botocore
import netops_sns as sns
env = sns.load_env()
client = sns.sns_client(env)
alert = {"status": "firing", "device_id": "dc1-a-leaf-01", "kind": "link_down", "target": "ethernet-1/9", "detail": "check_splunk_image (direct)", "starts_at": 1}
sent = sns.send(env, sns.messages([alert]))
print(json.dumps({"python": sys.version.split()[0], "executable": sys.executable, "boto3": boto3.__version__, "botocore": botocore.__version__,
                  "boto3_file": boto3.__file__, "service": client.meta.service_model.service_name, "endpoint": client.meta.endpoint_url, "sent": sent}))
''' % APP

passed = 0


def check(name, cond, detail=""):
    global passed
    if not cond:
        print("NG", name)
        if detail:
            print("   ", detail)
        raise SystemExit(1)
    passed += 1
    print("ok", name)


def run(*args, input=None, env=None, check_rc=True):
    p = subprocess.run(args, input=input, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check_rc and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])} … が {p.returncode} で終わった: {(p.stderr or p.stdout)[-800:]}")
    return p


def dexec(*args, user="splunk", input=None, check_rc=True):
    return run("docker", "exec", *(["-i"] if input is not None else []), "-u", user, NAME, *args, input=input, check_rc=check_rc)


def posts():
    return [json.loads(l) for l in dexec("cat", POSTS, check_rc=False).stdout.splitlines() if l.strip()]


def publishes():
    return [p for p in posts() if p["kind"] == "publish"]


def splunkd_log():
    return dexec("cat", "/opt/splunk/var/log/splunk/splunkd.log", user="root").stdout


def action_lines():
    return [l for l in splunkd_log().splitlines() if "action=netops_sns" in l]


def wait(what, cond, timeout, step=5):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return cond()


def hec_link_down(token, if_name):
    """HEC に link down（ifOperStatus=2）のイベントを 1 件入れる（Telegraf の SNMP の interface と同じ形）"""
    ev = {"time": int(time.time()), "host": "203.0.113.11", "source": "telegraf:interface", "sourcetype": "netops:metrics",
          "event": {"topic": "metrics", "measurement": "interface", "agent_host": "203.0.113.11",
                    "tags": {"agent_host": "203.0.113.11", "ifName": if_name, "sysName": "dc1-a-leaf-01"},
                    "fields": {"ifOperStatus": 2, "ifAdminStatus": 1, "ifDescr": if_name}}}
    out = dexec("curl", "-sk", "https://127.0.0.1:8088/services/collector/event", "-H", f"Authorization: Splunk {token}", "-d", json.dumps(ev)).stdout
    if '"code":0' not in out.replace(" ", ""):
        raise RuntimeError(f"HEC が受けなかった: {out[:300]}")


def ua_versions(ua):
    py, b3 = re.search(r"lang/python#([\d.]+)", ua), re.search(r"Boto3/([\d.]+)", ua)
    return (py.group(1) if py else None), (b3.group(1) if b3 else None)


def main(argv):
    if run("docker", "info", check_rc=False).returncode != 0:
        print("docker が動いていない"); return 2
    conf = open(os.path.join(ROOT, "app", "splunk", "netops_alerts", "default", "alert_actions.conf"), encoding="utf-8").read()
    required = re.search(r"^python\.required\s*=\s*(\S+)", conf, re.M).group(1)
    image = argv[1] if len(argv) > 1 else IMAGE
    if len(argv) <= 1:
        print(f"-- docker/images/splunk/Dockerfile と app/splunk/ を linux/amd64 でビルドして {IMAGE} にする")
        run("docker", "buildx", "build", "--platform", "linux/amd64", "--load", "-t", IMAGE,
            "-f", os.path.join(ROOT, "docker", "images", "splunk", "Dockerfile"), os.path.join(ROOT, "app", "splunk"))
    token = str(uuid.uuid4())
    env = dict(os.environ, SPLUNK_PASSWORD=secrets.token_urlsafe(18), SPLUNK_HEC_TOKEN=token)   # docker run の引数に値を書かない（ps に出さない）
    try:
        run("docker", "run", "-d", "--name", NAME, "--platform", "linux/amd64",
            "-e", "SPLUNK_START_ARGS=--accept-license", "-e", "SPLUNK_GENERAL_TERMS=--accept-sgt-current-at-splunk-com",
            "-e", "SPLUNK_PASSWORD", "-e", "SPLUNK_HEC_TOKEN",
            "-e", f"ALERTS_TOPIC_ARN={TOPIC}", "-e", "AWS_REGION=ap-northeast-1", "-e", "DEVICE_MAP=203.0.113.11=dc1-a-leaf-01",
            "-e", f"AWS_CONTAINER_CREDENTIALS_FULL_URI=http://127.0.0.1:{PORT}/creds", "-e", f"AWS_ENDPOINT_URL_SNS=http://127.0.0.1:{PORT}/",
            image, env=env)
        print(f"-- {image} を {NAME} で起こした。healthy になるのを待つ（数分）")
        t0 = time.time()
        health = wait("healthy", lambda: (s := run("docker", "inspect", "-f", "{{.State.Status}} {{.State.Health.Status}}", NAME).stdout.split())
                      and (s[0] != "running" or s[1] in ("healthy", "unhealthy")) and s, 1500, 10)
        check(f"Splunk が入口（app/splunk/entrypoint.sh）から起きて healthy になる（{int(time.time() - t0)} 秒）", health and health[-1] == "healthy",
              run("docker", "logs", "--tail", "30", NAME, check_rc=False).stdout)
        version = re.search(r"^VERSION=(\S+)", dexec("cat", "/opt/splunk/etc/splunk.version").stdout, re.M).group(1)
        btool = dexec("/opt/splunk/bin/splunk", "btool", "alert_actions", "list", "netops_sns").stdout
        check(f"splunkd が読む設定でも python.required = {required}（btool）", re.search(rf"^python\.required\s*=\s*{re.escape(required)}$", btool, re.M) is not None, btool)

        dexec("sh", "-c", "cat > /tmp/nwc_fake_sns.py", input=FAKE)
        run("docker", "exec", "-d", "-u", "splunk", NAME, "/opt/splunk/bin/splunk", "cmd", "python3", "/tmp/nwc_fake_sns.py", str(PORT), POSTS)
        check("偽の認証情報の口と偽の SNS がコンテナの中で動く", wait("fake", lambda: any(p["kind"] == "ready" for p in posts()), 60, 2))

        # ---- 1. 直に
        dexec("sh", "-c", "cat > /tmp/nwc_direct.py", input=DIRECT)
        p = dexec("/opt/splunk/bin/splunk", "cmd", f"python{required}", "/tmp/nwc_direct.py", check_rc=False)
        direct = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 and p.stdout.strip() else None
        check(f"直に: Splunk の Python {required} で boto3 / botocore が読め、SNS のクライアントを作れる（app に同梱していない = Splunk の site-packages のもの）",
              direct is not None and direct["python"].startswith(required + ".") and direct["service"] == "sns"
              and direct["endpoint"] == f"http://127.0.0.1:{PORT}/" and "/opt/splunk/lib/" in direct["boto3_file"] and APP not in direct["boto3_file"],
              (p.stderr or p.stdout)[-1500:])
        print(f"   python {direct['python']}（{direct['executable']}）boto3 {direct['boto3']} botocore {direct['botocore']} {direct['boto3_file']}")
        got = publishes()
        check("直に: netops_sns.send で偽の SNS へ 1 通届く（認証情報は偽の口から、署名つき、Query API の Publish）",
              direct["sent"] == 1 and len(got) == 1 and got[0]["form"].get("Action") == "Publish" and got[0]["form"].get("TopicArn") == TOPIC
              and got[0]["token"] == "local-test-token" and any(x["kind"] == "creds" and x["path"] == "/creds" for x in posts()), json.dumps(got, ensure_ascii=False))

        # ---- 2. 本物の流れ（HEC → netops_poll → アラートアクション → 偽の SNS）
        before = len(publishes())
        hec_link_down(token, "ethernet-1/1")
        print("-- HEC に link down（dc1-a-leaf-01 ethernet-1/1）を入れた。netops_poll（毎分）が送るのを待つ")
        wait("publish", lambda: len(publishes()) > before, 240)
        time.sleep(75)   # 次の回で重ねて送らないこと（サーチは毎分）
        got = publishes()[before:]
        msg = json.loads(got[0]["form"].get("Message", "{}")) if got else {}
        alerts = msg.get("alerts") or [{}]
        check("本物の流れ: アラートアクションが偽の SNS へ 1 通だけ送る（次の回で重ねて送らない）", len(got) == 1, json.dumps(got, ensure_ascii=False))
        check("本物の流れ: 本文は Grafana と同じ形の JSON（source=splunk、link_down の firing 1 件）、件名は netops alert、form は Publish",
              got[0]["form"].get("Action") == "Publish" and got[0]["form"].get("Subject") == "netops alert" and got[0]["form"].get("TopicArn") == TOPIC
              and got[0]["content_type"].startswith("application/x-www-form-urlencoded") and msg.get("source") == "splunk" and len(msg.get("alerts", [])) == 1
              and {k: alerts[0].get(k) for k in ("status", "device_id", "kind", "target", "detail")}
              == {"status": "firing", "device_id": "dc1-a-leaf-01", "kind": "link_down", "target": "ethernet-1/1", "detail": "ethernet-1/1 is down (splunk: poll)"}
              and isinstance(alerts[0].get("starts_at"), int), json.dumps(got, ensure_ascii=False))
        ua_py, ua_boto3 = ua_versions(got[0]["user_agent"])
        check(f"本物の流れ: splunkd は Python {required} で起こし、Splunk の boto3 で送っている（User-Agent: Python {ua_py}、boto3 {ua_boto3}）",
              ua_py == direct["python"] and ua_boto3 == direct["boto3"], got[0]["user_agent"])
        lines = action_lines()
        check("本物の流れ: splunkd.log に件数（published=1/1）と exit code=0 が残る",
              any("STDERR" in l and "search=netops_poll rows=1 alerts=1 published=1/1" in l for l in lines) and any("exit code=0" in l for l in lines),
              "\n".join(lines[-10:]))

        # ---- 3. boto3 が読めないとき（app の bin/ に読むと失敗する boto3.py を置く。sys.path の先頭は bin/ なので site-packages より先に読まれる）
        dexec("sh", "-c", f"""printf '%s\\n' 'raise ModuleNotFoundError("No module named boto3 (check_splunk_image が置いた偽物)", name="boto3")' > {SHADOW}""")
        before, nlines = len(publishes()), len(action_lines())
        hec_link_down(token, "ethernet-1/2")
        print("-- bin/boto3.py を置いて、HEC に link down（ethernet-1/2）を入れた。splunkd.log に理由が出るのを待つ")
        wait("error", lambda: any("exit code=" in l for l in action_lines()[nlines:]), 240)
        new = action_lines()[nlines:]
        check("boto3 が読めないとき: splunkd.log に理由（boto3 を読めない・Splunk の Python の版・確かめ方）が 1 行で出て、exit code=3。送らない",
              any("STDERR" in l and "boto3 を読めない" in l and f"Splunk の Python {required}." in l and "tests/check_splunk_image.py" in l for l in new)
              and any("exit code=3" in l for l in new) and not any("Traceback" in l for l in new) and len(publishes()) == before, "\n".join(new[-10:]))
        print("   " + next(l for l in new if "boto3 を読めない" in l)[-260:])
        dexec("rm", "-f", SHADOW)

        # ---- 4. 版
        seen = {"splunk": version, "python": direct["python"], "boto3": direct["boto3"]}
        check(f"見えた版が CHECKED と同じ（{seen}）。違うなら、上が通っているので CHECKED を書き換える（Splunk の版を変えたら Dockerfile・ops/up.sh も）",
              seen == CHECKED, f"CHECKED={CHECKED}")
    finally:
        run("docker", "rm", "-f", "-v", NAME, check_rc=False)   # -v: イメージの VOLUME（/opt/splunk/etc・var）の匿名ボリュームも消す
    print(f"すべて通過（{passed} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
