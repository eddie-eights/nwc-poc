"""005 ステップ 1 の 3: VictoriaMetrics のクラスター（vminsert 1、vmselect 1、vmstorage 3、複製数 2）。

Spark と同じ形（spark/snmp_sinks.py の encode_write_request と snappy_compress。remote write）で 100 系列 × 10 サンプルを入れ、
vmstorage を 1 台ずつ止めても、全部の系列とサンプルが読め、結果に "isPartial":false が返るかを見る。
時刻が前後したサンプル（Spark は 1 バッチの中を ts で並べ替えるが、バッチをまたぐと古い時刻が後から届く）を受けるかも見る。

使い方: python3 oss/compose/check_vm.py（終わってもコンテナは残す。片付けは docker compose --profile '*' down -v）
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "spark"))
import snmp_sinks  # noqa: E402

INSERT = "http://127.0.0.1:18480/insert/0/prometheus/api/v1/write"
SELECT = "http://127.0.0.1:18481/select/0/prometheus/api/v1/"
HEADERS = {"Content-Type": "application/x-protobuf", "Content-Encoding": "snappy", "X-Prometheus-Remote-Write-Version": "0.1.0"}
STORAGES = ("vmstorage-1", "vmstorage-2", "vmstorage-3")
NOW_MS = int(time.time()) // 60 * 60 * 1000


def dc(*args):
    subprocess.run(["docker", "compose", "--profile", "vm", *args], cwd=HERE, check=True, capture_output=True)


def write(series):
    """[(labels, value, ms)] を 1 回の remote write で送り、HTTP の状態を返す"""
    status, text = snmp_sinks.http_post(INSERT, snmp_sinks.snappy_compress(snmp_sinks.encode_write_request(series)), HEADERS)
    if status >= 300:
        print(f"  remote write が {status}: {text[:200]!r}")
    return status


def get(path, **params):
    params["nocache"] = "1"   # vmselect の結果のキャッシュで、止める前の答えが返らないように
    with urllib.request.urlopen(SELECT + path + "?" + urllib.parse.urlencode(params, doseq=True), timeout=30) as r:
        return json.load(r)


def scalar(expr):
    d = get("query", query=expr)
    vals = [float(x["value"][1]) for x in d["data"]["result"]]
    return (vals[0] if vals else None), d.get("isPartial")


def vminsert_metric(prefix):
    with urllib.request.urlopen("http://127.0.0.1:18480/metrics", timeout=5) as r:
        return [line for line in r.read().decode().splitlines() if line.startswith(prefix)]


def reachable():
    """vminsert から見えている vmstorage の台数（vm_rpc_vmstorage_is_reachable）。
    これは「壊れた印が付いていない」の意味で、起動直後はまだつないでいなくても 1 になる。つながったかは connected() で見る"""
    return sum(1 for line in vminsert_metric("vm_rpc_vmstorage_is_reachable") if line.rstrip().endswith(" 1"))


def connected():
    """vminsert が書き込み用の TCP をつないでいる vmstorage の台数（vm_tcpdialer_conns{name="vminsert_metric_rows"}）"""
    return sum(1 for line in vminsert_metric('vm_tcpdialer_conns{name="vminsert_metric_rows"') if float(line.split()[-1]) >= 1)


def incompletely_replicated():
    """2 つ目の複製を作れなかった行の数。vminsert は 204 を返し、vmselect も isPartial を立てないので、ここでしか分からない"""
    return int(float(vminsert_metric("vm_rpc_rows_incompletely_replicated_total")[0].split()[-1]))


def labels(**kv):
    return sorted(kv.items())


def wait(what, fn, limit=60, step=1.0):
    end = time.time() + limit
    while time.time() < end:
        try:
            if fn():
                print(f"  ok: {what}")
                return True
        except OSError:
            pass
        time.sleep(step)
    print(f"  タイムアウト: {what}")
    return False


def report(tag, name="oss_check_value"):
    n_series, p1 = scalar(f'count(last_over_time({name}[3h]))')
    n_samples, p2 = scalar(f'sum(count_over_time({name}[3h]))')
    total, p3 = scalar(f'sum(sum_over_time({name}[3h]))')
    fmt = lambda v: "無し" if v is None else f"{v:.0f}"   # noqa: E731
    print(f"  {tag}（{name}）: 系列 {fmt(n_series)}、サンプル {fmt(n_samples)}、値の合計 {fmt(total)}、isPartial {p1}/{p2}/{p3}")
    return n_series, n_samples


def main():
    print("== 5 台を起こす")
    dc("up", "-d", "vmstorage-1", "vmstorage-2", "vmstorage-3", "vminsert", "vmselect")
    wait("vminsert が答える", lambda: urllib.request.urlopen("http://127.0.0.1:18480/health", timeout=3).status == 200, step=0.02)
    print(f"  この時点の reachable {reachable()} 台、つないでいる台 {connected()} 台")

    # vminsert は vmstorage ごとの送り役が最初の 200ms の刻みで初めてつなぐ（netstorage.go の run → checkHealth → dial）。
    # それより前に受けた行は、送り役が先につないだ自分の台にだけ書き、まだつないでいない次の台への 2 つ目は諦める
    # （sendBufToReplicasNonblocking。ログに "cannot make a copy #2"）。/health も書き込みも 204 で、reachable も起動時から 1 なので外からは分からない
    print("== 起動直後（/health が 200 を返した直後）に 100 系列 × 1 サンプル")
    startup = [(labels(__name__="oss_check_startup", series=str(i)), 1.0, NOW_MS - 60 * 60 * 1000) for i in range(100)]
    print(f"  remote write: {write(startup)}")
    wait("vminsert が 3 台ともつないだ（vm_tcpdialer_conns）", lambda: connected() == 3)
    wait("vmselect が答える", lambda: urllib.request.urlopen("http://127.0.0.1:18481/health", timeout=3).status == 200)
    time.sleep(1)
    startup_incomplete = incompletely_replicated()
    print(f"  2 つ目の複製を作れなかった行（vm_rpc_rows_incompletely_replicated_total）: {startup_incomplete}")

    print("== つないだあとに 100 系列 × 10 サンプル（1 時間前から 1 分おき。値は 1 なので合計はサンプル数と同じ）")
    batch = [(labels(__name__="oss_check_value", series=str(i)), 1.0, NOW_MS - 60 * 60 * 1000 + k * 60 * 1000)
             for k in range(10) for i in range(100)]
    print(f"  remote write: {write(batch)}")
    wait("1000 サンプルが読める", lambda: scalar('sum(count_over_time(oss_check_value[3h]))')[0] == 1000)
    report("3 台")
    report("3 台", "oss_check_startup")
    print(f"  2 つ目の複製を作れなかった行（起動直後の分から増えていなければ、この 1000 行は 2 つずつある）: {incompletely_replicated()}")

    for stop in STORAGES:
        print(f"== {stop} を止める")
        dc("stop", stop)
        time.sleep(3)
        report(f"{stop} を止めたまま")
        report(f"{stop} を止めたまま", "oss_check_startup")
        extra = [(labels(__name__="oss_check_during_stop", stopped=stop, series=str(i)), 1.0, NOW_MS - 30 * 60 * 1000) for i in range(10)]
        print(f"  止めたまま 10 系列を書く: {write(extra)}")
        dc("start", stop)
        wait(f"{stop} が戻る（vminsert が 3 台ともつなぎ直した）", lambda: reachable() == 3 and connected() == 3, limit=60)
    wait("止めているあいだに書いた 30 系列が読める", lambda: scalar('count(last_over_time(oss_check_during_stop[3h]))')[0] == 30)
    print(f"  止めているあいだに書いた系列: {scalar('count(last_over_time(oss_check_during_stop[3h]))')}")
    print(f"  2 つ目の複製を作れなかった行（1 台止めても残り 2 台に 2 つ作れるので、起動直後の {startup_incomplete} のまま）: {incompletely_replicated()}")

    print("== 時刻が前後したサンプル")
    t0 = NOW_MS - 90 * 60 * 1000
    rev = [(labels(__name__="oss_check_ooo", case="reverse_in_one_request"), float(k), t0 + k * 60 * 1000) for k in range(9, -1, -1)]
    print(f"  1 回の送信の中で新しい順（10 サンプル）: {write(rev)}")
    for k, label in ((5, "最初に 5 分前"), (50, "次に 50 分前（古い）"), (20, "最後に 20 分前（間）")):
        st = write([(labels(__name__="oss_check_ooo", case="older_in_later_request"), float(k), NOW_MS - k * 60 * 1000)])
        print(f"  別々の送信で {label}: {st}")
    old = NOW_MS - 40 * 24 * 3600 * 1000
    print(f"  保持期間（-retentionPeriod=30d）より古い 40 日前: {write([(labels(__name__='oss_check_ooo', case='older_than_retention'), 1.0, old)])}")
    want = {"reverse_in_one_request": 10, "older_in_later_request": 3}
    got = {}

    def export():
        got.clear()
        with urllib.request.urlopen(SELECT.replace("/api/v1/", "/api/v1/export") + "?" + urllib.parse.urlencode(
                {"match[]": "oss_check_ooo", "start": str((old - 3600 * 1000) // 1000), "nocache": "1"}), timeout=30) as r:
            for line in r.read().decode().splitlines():
                d = json.loads(line)
                got[d["metric"]["case"]] = d["timestamps"]
        return all(len(got.get(k, [])) == n for k, n in want.items())
    wait("前後した 2 つの場合が全部読める", export, limit=30)
    for case in ("reverse_in_one_request", "older_in_later_request", "older_than_retention"):
        ts = got.get(case)
        print(f"  {case}: " + ("無い" if ts is None else f"{len(ts)} サンプル、時刻は昇順に並んで返る: {ts == sorted(ts)}"))
    print("== 終わり")


if __name__ == "__main__":
    main()
