#!/usr/bin/python3
"""search head のヘルスチェックに足す突き合わせ（Splunk をクラスターにする（004）の手当て A。docs/cycles/004-splunk-indexer-cluster/design.md）。

indexer のタスクが入れ替わって前と同じ IP をもらうと、search head は古い indexer の GUID のままその host:port を持ち続け、新しい indexer への
検索が 401 になる（検索は成功を返し、警告も出ない。その indexer に入ったイベントは見えず、アラートは黙って落ちる）。search head を起こし直すと直る。
そこで、cluster manager が Up と言っている peer（GUID）を、search head の distributed peers が同じ GUID の Up で持っているかを見て、
持っていなければ 1 で終わる。ECS の healthCheck（retries 10、間隔 30 秒）が続けて 10 回失敗すると、ECS が search head を入れ替える。

- indexer が本当に落ちているだけ（manager も Down と言う）なら、見ない。
- manager に聞けないときは 0（manager が落ちただけで search head まで入れ替えない）。
- indexer が普通に入ってきたときの食い違いは 1 秒ほどで消える（手元で確認）。

manager が Up と言う peer が、あるはずの台数（NWC_PEERS_EXPECTED。search head のタスク定義が indexer の数を入れる）より少ないときは、
食い違いが無くても ok でなく degraded と書く（reason=peers_up:<Up の数>/<あるはずの数>）。indexer が落ちているだけなので終了コードは 0
（search head を入れ替えても直らない）。NWC_PEERS_EXPECTED が無い・数でないときは台数を見ない（ok の reason=peers_up:<Up の数>）。

判定（ok / degraded / mismatch / skip / error）が前の回と変わったときだけ、決まった形の 1 行「nwc-peer-check state=<判定> reason=<理由>」を
PID 1（コンテナの入口）の stdout に書く（手当て B）。ECS では awslogs で CloudWatch Logs に出て、ops/up.sh が全タスク待ちのあとに
いまの search head のタスクの最新の行を読む（state=ok で indexer が全部 Up になるまで待ち、ならなければ止まる）。手元では docker logs に出る。
理由に入れるのは peer の名前と数と例外の型だけ（admin のパスワード・HEC の token・合言葉は入れない）。

イメージ（splunk/Dockerfile）に /sbin/nwc-peers-check.py として入れ、クラスターの search head のタスク定義（terraform/pipeline/analytics の
splunk.tf）が `/sbin/checkstate.sh && /sbin/nwc-peers-check.py` で呼ぶ。読む環境変数は上流の入口と同じ SPLUNK_CLUSTER_MASTER_URL
（manager の名前）と SPLUNK_PASSWORD（admin。どのタスクも同じ SSM の値）、それに NWC_PEERS_EXPECTED（indexer の数）。Splunk の Python でなく OS の /usr/bin/python3（標準ライブラリだけ）で動く
"""
import base64
import json
import os
import ssl
import sys
import urllib.request

TIMEOUT = 4          # 1 回の問い合わせ。ECS の healthCheck の timeout は 10 秒で、問い合わせは 2 回
LOCAL = "https://127.0.0.1:8089"
CM_PEERS = "/services/cluster/manager/peers"        # entry の name が GUID。content に label（名前）と status
SH_PEERS = "/services/search/distributed/peers"     # entry の name が host:port。content に guid・status・disabled
PREFIX = "nwc-peer-check"                # ops/up.sh が CloudWatch Logs でこの接頭辞の行を探す
OUT = "/proc/1/fd/1"                     # PID 1 の stdout（ヘルスチェックの出力は ECS ではどこにも残らない）
# 前に書いた行。同じ行を 30 秒ごとに積まない（タスクが入れ替われば消えて、新しいタスクは最初の判定を必ず書く）。
# ユーザーごとに分ける（ヘルスチェックと別のユーザーで手で動かしても、互いの前の行を書き換えられずに毎回書く、にならない）
STATE = f"/tmp/nwc-peer-check.{os.getuid()}.state"


def manager_base(url):
    """SPLUNK_CLUSTER_MASTER_URL（名前だけでも、https:// やポート付きでも）→ 管理 API の URL"""
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = "https://" + url
    if url.count(":") < 2:
        url += ":8089"
    return url


def get(base, path, password):
    req = urllib.request.Request(f"{base}{path}?output_mode=json&count=0",
                                 headers={"Authorization": "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE      # 管理 API は Splunk の自己署名の証明書
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))  # 宛先は自分と VPC の中。プロキシの環境変数を見ない
    with opener.open(req, timeout=TIMEOUT) as r:
        return json.load(r).get("entry", [])


def missing(cm_entries, sh_entries):
    """manager が Up と言う peer のうち、search head が同じ GUID の Up（無効にしていない）で持っていないものの名前"""
    seen = {str(e["content"].get("guid", "")).upper() for e in sh_entries
            if e["content"].get("status") == "Up" and str(e["content"].get("disabled", "")).lower() not in ("1", "true")}
    return sorted(str(e["content"].get("label") or e["name"]) for e in cm_entries
                  if e["content"].get("status") == "Up" and e["name"].upper() not in seen)


def record(state, reason, out=OUT, state_file=STATE):
    """判定の行を、前に書いた行と違うときだけ out に足す。書けなければヘルスチェックの出力に理由を出す（判定の終了コードは変えない）"""
    line = f"{PREFIX} state={state} reason={reason}"
    try:
        with open(state_file, encoding="utf-8") as f:
            if f.read() == line:
                return
    except OSError:
        pass
    try:
        with open(out, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        print(f"判定の行を {out} に書けない（{type(e).__name__}）")
        return
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass  # 次の回にもう一度書くだけ


def main(environ=os.environ, fetch=get, out=OUT, state_file=STATE):
    cm = environ.get("SPLUNK_CLUSTER_MASTER_URL", "")
    if not cm:
        return 0
    password = environ.get("SPLUNK_PASSWORD", "")
    try:
        cm_entries = fetch(manager_base(cm), CM_PEERS, password)
    except Exception as e:  # noqa: BLE001  manager が落ちている・起動中
        print(f"manager に聞けないので見ない（{type(e).__name__}）")
        record("skip", f"manager_unreachable:{type(e).__name__}", out, state_file)
        return 0
    try:
        sh_entries = fetch(LOCAL, SH_PEERS, password)
    except Exception as e:  # noqa: BLE001
        print(f"search head の distributed peers を読めない（{type(e).__name__}: {str(e)[:200]}）")
        record("error", f"sh_peers_unreadable:{type(e).__name__}", out, state_file)
        return 1
    lost = missing(cm_entries, sh_entries)
    if lost:
        print("manager は Up と言うが、search head が同じ GUID で Up と見ていない peer: " + ", ".join(lost))
        record("mismatch", "lost:" + ",".join("_".join(n.split()) for n in lost), out, state_file)
        return 1
    up = sum(1 for e in cm_entries if e["content"].get("status") == "Up")
    try:
        expected = int(environ.get("NWC_PEERS_EXPECTED", "") or 0)
    except ValueError:
        expected = 0
    if up < expected:
        print(f"manager が Up と言う peer が {up} 台で、{expected} 台に足りない（indexer が落ちている。search head は入れ替えない）")
        record("degraded", f"peers_up:{up}/{expected}", out, state_file)
        return 0
    record("ok", f"peers_up:{up}", out, state_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
