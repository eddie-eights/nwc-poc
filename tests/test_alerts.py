"""アラートの送り手の模擬テスト（AWS にも Splunk にも Grafana にも触れない）。
Splunk のアラートアクション（app/splunk/netops_alerts/bin/netops_sns.py）が保存済みサーチの結果を SNS の本文にして publish すること（boto3）、
保存済みサーチ（default/savedsearches.conf）と Grafana のアラート（app/grafana/provisioning/alerting/netops*.yaml）が同じ形の本文を出し、
受け手（app/temporal/rules.py の alerts_from_message）がそのまま読めること、SNS のトピック（IaC/terraform/aws-managed/base/core の alerts.tf）と
イメージ（docker/images/splunk/Dockerfile・entrypoint.sh、app/grafana/start.sh）と ops/up.sh・ops/check.sh がその配線を持つこと。
受け手の側は tests/test_workflow.py（SQS → ワークフロー）と tests/test_sync.py（Lambda → Neptune の status）。
実行は uv run --group dev python tests/test_alerts.py（boto3 が無くても通る。あれば手元の偽の SNS へ本物の boto3 で publish して確かめる）"""
import ast, contextlib, csv, glob, gzip, http.server, importlib.util, io, json, math, os, re, signal, subprocess, sys, tempfile, threading, time, urllib.parse

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app", "temporal"))
passed = 0


def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()

def read_ops(name):  # ops/up.sh / down.sh は、読んでいる共通の関数（ops/common.sh と ops/<name>-common.sh。OSS 版の oss/ops/ と共通）とつないで見る
    return read("ops", "common.sh") + read("ops", f"{name}-common.sh") + read("ops", f"{name}.sh")


ACTION = "app/splunk/netops_alerts/bin/netops_sns.py"
sns = load(ACTION, "netops_sns")
import rules   # noqa: E402  受け手（app/temporal/rules.py）
src = read(ACTION)
KEYS = ["status", "device_id", "kind", "target", "detail", "starts_at"]
TOPIC = "arn:aws:sns:ap-northeast-1:111122223333:netops-alerts"
CREDS = ("AKIDTEST", "test-secret", "test-token")   # 偽の値（手元の偽の認証情報の口が返す）

# ---- アラートアクション: 行 → アラート
def imported(nodes):
    return {n.split(".")[0] for node in nodes if isinstance(node, (ast.Import, ast.ImportFrom))
            for n in ([a.name for a in node.names] if isinstance(node, ast.Import) else [node.module])}


top = imported(ast.parse(src).body)
check("アラートアクションはモジュールの頭では標準ライブラリだけを読み、boto3 / botocore は publish のときに読む（Splunk の Python が持っているもの。"
      "app に同梱せず、sys.path も足さない。boto3 の無い PC でもテストできる）",
      top <= set(sys.stdlib_module_names) and imported(ast.walk(ast.parse(src))) - top == {"boto3", "botocore"}
      and "sys.path" not in src and not hasattr(sns, "APP_LIB"))
devmap = sns.parse_device_map(" 203.0.113.31=dc1-leaf-01 ,DC1-Leaf-02.Example.Net=dc1-leaf-02,壊れた要素,=x,y=,203.0.113.101=")
check("device map は「別名=機器名」をカンマで並べたもの。別名は小文字にし、= の無い要素と片方が空の要素は捨てる",
      devmap == {"203.0.113.31": "dc1-leaf-01", "dc1-leaf-02.example.net": "dc1-leaf-02"} and sns.parse_device_map("") == {} and sns.parse_device_map(None) == {})
check("機器名は device map を引き、無ければ小文字にしてドメインを落とす（IPv4 は落とさない）",
      [sns.device_name(n, devmap) for n in ("203.0.113.31", "DC1-LEAF-02.example.net", "Dc1-Spine-01.lab.local", "198.51.100.7", " dc1-leaf-01 ", "", None)]
      == ["dc1-leaf-01", "dc1-leaf-02", "dc1-spine-01", "198.51.100.7", "dc1-leaf-01", "", ""])
check("受け手（rules.device_name）も同じ規則で揃える（device map を通ったあとの名前はそのまま通る）",
      all(rules.device_name(n) == sns.device_name(n, {}) for n in ("DC1-LEAF-02.example.net", "198.51.100.7", "dc1-leaf-01", "")))
check("starts_at は epoch 秒の整数にする（小数は切り捨て、読めない値と負の値は 0）",
      [sns._epoch(v) for v in ("1790000000.9", 1790000000, "", None, "x", "-5")] == [1790000000, 1790000000, 0, 0, 0, 0])
ROWS = [
    {"device": "203.0.113.31", "kind": "bgp_down", "target": "10.255.0.1", "status": "firing", "detail": "bgp session to 10.255.0.1 is active (splunk: gnmi)", "starts_at": "1790000000.5"},
    {"device": "DC1-SPINE-01.lab", "kind": "link_down", "target": " ethernet-1/1 ", "status": "RESOLVED", "detail": "", "starts_at": ""},
    {"device": "", "kind": "trap", "target": ".1.3", "status": "firing"},
    {"device": "dc1-leaf-01", "kind": "", "target": "x", "status": "firing"},
    {"device": "dc1-leaf-01", "kind": "trap", "target": ".1.3", "status": "pending"},
    {"device": "dc1-leaf-01", "kind": "trap", "status": "firing", "detail": "x" * 3000},
]
alerts = sns.alerts_from_rows(ROWS, devmap)
check("結果の 1 行 = アラート 1 件。機器か種類が無い行と、status が firing / resolved でない行は捨てる",
      [(a["device_id"], a["kind"], a["target"], a["status"], a["starts_at"]) for a in alerts]
      == [("dc1-leaf-01", "bgp_down", "10.255.0.1", "firing", 1790000000), ("dc1-spine-01", "link_down", "ethernet-1/1", "resolved", 0), ("dc1-leaf-01", "trap", "", "firing", 0)])
check("アラートの項目は 6 つで、detail は 1000 字で切る", all(list(a) == KEYS for a in alerts) and len(alerts[2]["detail"]) == 1000)
texts = sns.messages(alerts)
check("本文は {\"source\": \"splunk\", \"alerts\": […]} の JSON 1 通（空白を入れない）",
      len(texts) == 1 and json.loads(texts[0]) == {"source": "splunk", "alerts": alerts} and ", " not in texts[0].replace(alerts[0]["detail"], ""))
many = sns.messages([dict(alerts[0], target=f"10.0.0.{i}") for i in range(120)])
check("50 件ごとに 1 通に分ける（SNS の本文は 256 KB まで）。0 件なら何も送らない",
      [len(json.loads(t)["alerts"]) for t in many] == [50, 50, 20] and sns.messages([]) == [] and sns.MAX_ALERTS == 50
      and max(len(t.encode()) for t in sns.messages([dict(alerts[2], target=f"t{i}") for i in range(50)])) < 256 * 1024)
got = rules.alerts_from_message(texts[0], now=1790000123)
check("受け手（rules.alerts_from_message）がそのまま読める: anomaly_id は <機器>#<種類>#<対象>、starts_at が無ければ受けた時刻",
      [(a["anomaly_id"], a["status"], a["first_seen"], a["source"]) for a in got]
      == [("dc1-leaf-01#bgp_down#10.255.0.1", "firing", 1790000000, "splunk"), ("dc1-spine-01#link_down#ethernet-1/1", "resolved", 1790000123, "splunk"),
          ("dc1-leaf-01#trap#", "firing", 1790000123, "splunk")])
with tempfile.TemporaryDirectory() as tmp:
    RESULTS = os.path.join(tmp, "results.csv.gz")
    with gzip.open(RESULTS, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["device", "kind", "target", "status", "detail", "starts_at", "__mv_device"])
        w.writeheader()
        w.writerow(dict(ROWS[0], __mv_device=""))
        w.writerow({"device": "dc1-leaf-02", "kind": "trap", "target": ".1.3.6.1.4.1.9.9.41.2.0.1", "status": "resolved", "detail": "改行\nとカンマ, を含む", "starts_at": "1790000060"})
    rows = sns.read_rows(RESULTS)
    check("結果のファイル（gzip の CSV。Splunk が results_file で渡す）を行の dict にする（余分な列は無視、改行とカンマ入りの値も読む）",
          len(rows) == 2 and rows[0]["device"] == "203.0.113.31" and rows[1]["detail"] == "改行\nとカンマ, を含む"
          and [a["device_id"] for a in sns.alerts_from_rows(rows, devmap)] == ["dc1-leaf-01", "dc1-leaf-02"])

    # ---- 設定（entrypoint.sh が書くファイル）
    ep = read("app", "splunk", "entrypoint.sh")
    check("entrypoint.sh がファイルに写す環境変数は、アラートアクションが読むもの（ENV_KEYS）と同じ並び",
          tuple(re.search(r"\nfor k in ([A-Z_ ]+); do\n", ep).group(1).split()) == sns.ENV_KEYS
          and re.search(r'ENV_FILE="\$\{NETOPS_ALERTS_ENV:-([^}]+)\}"', ep).group(1) == sns.ENV_FILE)
    check("entrypoint.sh は写したあと上流の入口へ exec する（引数はそのまま）、ファイルは splunk ユーザーが読める（umask 022）",
          ep.rstrip().endswith('exec /sbin/entrypoint.sh "$@"') and "umask 022" in ep and "set -eu" in ep)
    check("認証情報そのもの（AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN）はファイルに写さない",
          not any(k in ep or k in sns.ENV_KEYS for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")))
    ENVF = os.path.join(tmp, "nwc-alerts.env")
    DM = "203.0.113.31=dc1-leaf-01,10.255.2.2=dc1-leaf-02"
    stub = os.path.join(tmp, "entrypoint.sh")
    with open(stub, "w", encoding="utf-8") as f:
        f.write(ep.replace('exec /sbin/entrypoint.sh "$@"', 'echo "upstream $*"'))
    r = subprocess.run(["bash", stub, "start-service"], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "NETOPS_ALERTS_ENV": ENVF, "AWS_REGION": "ap-northeast-1", "ALERTS_TOPIC_ARN": TOPIC, "DEVICE_MAP": DM,
                            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/abc", "SPLUNK_PASSWORD": "must-not-be-copied"})
    written = read(ENVF) if os.path.exists(ENVF) else ""
    check("entrypoint.sh を実際に走らせる: 6 行の KEY=VALUE を書き、他の環境変数（SPLUNK_PASSWORD など）は写さない",
          r.returncode == 0 and r.stdout.strip() == "upstream start-service" and [l.split("=", 1)[0] for l in written.splitlines()] == list(sns.ENV_KEYS)
          and "must-not-be-copied" not in written)
    env = sns.load_env(ENVF, environ={})
    check("load_env はそのファイルを読む（値の中の = とカンマはそのまま、空の値は入れない）",
          env == {"AWS_REGION": "ap-northeast-1", "ALERTS_TOPIC_ARN": TOPIC, "DEVICE_MAP": DM, "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/abc"})
    check("プロセスの環境変数に同じ名前があればそちら（知らない名前は読まない）。ファイルが無くても落ちない",
          sns.load_env(ENVF, environ={"AWS_REGION": "us-east-1", "OTHER": "x"})["AWS_REGION"] == "us-east-1"
          and "OTHER" not in sns.load_env(ENVF, environ={"OTHER": "x"})
          and sns.load_env(os.path.join(tmp, "none"), environ={"ALERTS_TOPIC_ARN": TOPIC}) == {"ALERTS_TOPIC_ARN": TOPIC})
    up_stub = os.path.join(tmp, "upstream.sh")   # 上流の入口（/sbin/entrypoint.sh）の代わり
    with open(up_stub, "w", encoding="utf-8") as f:
        f.write('echo "upstream $*"\n')
    stub_role = os.path.join(tmp, "entrypoint-role.sh")
    with open(stub_role, "w", encoding="utf-8") as f:
        f.write(ep.replace("sudo -n -u splunk rm -rf ", "echo removed ").replace("/sbin/entrypoint.sh", f"bash {up_stub}"))
    roles = {}
    for role in (None, "splunk_standalone", "splunk_search_head", "splunk_cluster_master", "splunk_indexer"):
        r = subprocess.run(["bash", stub_role, "start-service"], capture_output=True, text=True,
                           env={"PATH": os.environ["PATH"], "NETOPS_ALERTS_ENV": os.path.join(tmp, "role.env"), **({"SPLUNK_ROLE": role} if role else {})})
        roles[role] = (r.returncode, r.stdout.splitlines())
    check("クラスター（「Splunk をクラスターにする（004）」）では保存済みサーチ（app netops_alerts）を search head だけに残す: SPLUNK_ROLE が無い・standalone・"
          "search_head は消さず、manager と indexer は上流の入口の前に /opt/splunk-etc/apps/netops_alerts を splunk ユーザーで消す（同じアラートが台の数だけ出ない）",
          all(roles[k] == (0, ["upstream start-service"]) for k in (None, "splunk_standalone", "splunk_search_head"))
          and all(roles[k] == (0, ["removed /opt/splunk-etc/apps/netops_alerts", "upstream start-service"]) for k in ("splunk_cluster_master", "splunk_indexer"))
          and ep.count("sudo -n -u splunk rm -rf /opt/splunk-etc/apps/netops_alerts") == 1)

    # indexer は止められると splunk offline を打ってから、上流の入口へ SIGTERM を回す（「Splunk をクラスターにする（004）」のリスク 11）
    up_term = os.path.join(tmp, "upstream-term.sh")   # 上流の入口と同じく、SIGTERM で teardown（splunk stop）して終わる
    with open(up_term, "w", encoding="utf-8") as f:
        f.write('trap \'echo "upstream teardown"; kill $s; exit 0\' TERM\necho "upstream $*"\nsleep 30 &\ns=$!\nwait $s\n')
    fake_args = os.path.join(tmp, "offline.args")
    fake = os.path.join(tmp, "fake-splunk")   # sudo -n -u splunk /opt/splunk/bin/splunk の代わり。引数をファイルに書き、FAKE_SLEEP 秒かけて FAKE_RC で終わる
    with open(fake, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/bash\nprintf "%s\\n" "$@" > {fake_args}\necho "fake offline"\nsleep "${{FAKE_SLEEP:-0}}"\nexit "${{FAKE_RC:-0}}"\n')
    bindir = os.path.join(tmp, "bin")   # GNU の timeout（コンテナにはある。macOS には無い）と同じ約束: 打ち切ったら 124
    os.makedirs(bindir, exist_ok=True)
    with open(os.path.join(bindir, "timeout"), "w", encoding="utf-8") as f:
        f.write(f"#!{sys.executable}\nimport subprocess, sys\ntry:\n    sys.exit(subprocess.run(sys.argv[2:], timeout=float(sys.argv[1])).returncode)\n"
                "except subprocess.TimeoutExpired:\n    sys.exit(124)\n")
    for x in (fake, os.path.join(bindir, "timeout")):
        os.chmod(x, 0o755)

    def term_run(role, offline_timeout=None, **env):
        """入口を走らせ、上流の入口が起きたら SIGTERM を送る。(終了コード, 出力の行, 秒)"""
        script, out = os.path.join(tmp, "entrypoint-term.sh"), os.path.join(tmp, "term.out")
        s = ep.replace("sudo -n -u splunk rm -rf ", "echo removed ").replace("sudo -n -u splunk /opt/splunk/bin/splunk", fake).replace("/sbin/entrypoint.sh", f"bash {up_term}")
        if offline_timeout:
            s = re.sub(r"\nOFFLINE_TIMEOUT=\d+\n", f"\nOFFLINE_TIMEOUT={offline_timeout}\n", s)
        with open(script, "w", encoding="utf-8") as f:
            f.write(s)
        if os.path.exists(fake_args):
            os.remove(fake_args)
        t0 = time.time()
        with open(out, "w", encoding="utf-8") as fo:
            p = subprocess.Popen(["bash", script, "start-service"], stdout=fo, stderr=subprocess.STDOUT,
                                 env={"PATH": bindir + os.pathsep + os.environ["PATH"], "NETOPS_ALERTS_ENV": os.path.join(tmp, "term.env"),
                                      "SPLUNK_ROLE": role, "SPLUNK_PASSWORD": "pw-not-printed", **env})
        while "upstream start-service" not in read(out) and time.time() - t0 < 10:
            time.sleep(0.05)
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=20)
        return rc, read(out).splitlines(), time.time() - t0

    rc, lines, _ = term_run("splunk_indexer")
    args = read(fake_args).splitlines() if os.path.exists(fake_args) else []
    check("indexer: SIGTERM で splunk offline を splunk ユーザーで打ち（admin で認証）、終わったら上流の入口へ SIGTERM を回して teardown させる。"
          "かかった秒数を nwc-offline の行に出し、パスワードは出さない",
          rc == 0 and lines[:4] == ["removed /opt/splunk-etc/apps/netops_alerts", "upstream start-service", "nwc-offline: start", "fake offline"]
          and re.fullmatch(r"nwc-offline: rc=0 [01]s", lines[4]) is not None and lines[5:] == ["upstream teardown"]
          and args == ["offline", "-auth", "admin:pw-not-printed"] and not any("pw-not-printed" in l for l in lines)
          and 'timeout "$OFFLINE_TIMEOUT" sudo -n -u splunk /opt/splunk/bin/splunk offline -auth "admin:${SPLUNK_PASSWORD:-}" < /dev/null' in ep)
    rc, lines, sec = term_run("splunk_indexer", offline_timeout=1, FAKE_SLEEP="30")
    check("indexer: offline が OFFLINE_TIMEOUT 秒で終わらなければ打ち切り（124）、それでも上流の入口へ SIGTERM を回す",
          rc == 0 and lines[2:4] == ["nwc-offline: start", "fake offline"] and re.fullmatch(r"nwc-offline: rc=124 [123]s", lines[4]) is not None
          and lines[5:] == ["upstream teardown"] and sec < 10)
    rc, lines, _ = term_run("splunk_indexer", FAKE_RC="22")
    check("indexer: offline が失敗しても（splunkd がまだ起きていないなど）上流の入口へ SIGTERM を回して止まる",
          rc == 0 and lines[2:4] == ["nwc-offline: start", "fake offline"] and re.fullmatch(r"nwc-offline: rc=22 [01]s", lines[4]) is not None
          and lines[5:] == ["upstream teardown"])
    roles = {r: term_run(r) for r in ("splunk_search_head", "splunk_cluster_master", "splunk_standalone")}
    check("indexer 以外（search head・manager・standalone）は今までどおり上流の入口へ exec し、SIGTERM は上流がそのまま受ける（offline は打たない）",
          all(rc == 0 and lines[-2:] == ["upstream start-service", "upstream teardown"] and not any("nwc-offline" in l for l in lines)
              for rc, lines, _ in roles.values()) and not os.path.exists(fake_args))
    stf_idx = read("IaC", "terraform", "aws-managed", "pipeline", "analytics", "splunk.tf").split('resource "aws_ecs_task_definition" "splunk_idx"', 1)[1].split("\nresource ", 1)[0]
    stop_timeout = int(re.search(r"\n    stopTimeout = (\d+)\n", stf_idx).group(1))
    offline_timeout = int(re.search(r"\nOFFLINE_TIMEOUT=(\d+)\n", ep).group(1))
    check("offline を打ち切る秒数と、そのあとの splunk stop（手元で 47 秒）が、indexer のタスクの stopTimeout（splunk.tf。Fargate の上限 120 秒）に収まる",
          stop_timeout == 120 and offline_timeout + 47 <= stop_timeout)

    # ---- SNS へ publish（boto3）
    check("リージョンはトピックの ARN から取る（ARN で分からないときだけ AWS_REGION）",
          sns.topic_region(TOPIC) == "ap-northeast-1" and sns.topic_region("", "us-west-2") == "us-west-2" and sns.topic_region("arn:aws:sns", "x") == "x")
    check("boto3 には環境変数でなく引数で渡す: 認証情報は ECS のタスクロールの口だけ（アクセスキーの環境変数・~/.aws へは逃げない）、"
          "プロキシの環境変数と AWS_ENDPOINT_URL などの宛先の設定は見ない、boto3 の中では打ち直さない（送り直しは send）",
          "ContainerProvider(environ=env)" in src and "proxies={}" in src and "ignore_configured_endpoint_urls=True" in src and '"total_max_attempts": 1' in src
          and "AWS_ACCESS_KEY_ID" not in src and "AWS_SECRET_ACCESS_KEY" not in src)

    class Denied(Exception):
        """botocore の ClientError と同じく response を持つ"""
        def __init__(self):
            super().__init__("An error occurred (AuthorizationError) when calling the Publish operation")
            self.response = {"Error": {"Code": "AuthorizationError", "Message": "not authorized " + "x" * 400}, "ResponseMetadata": {"HTTPStatusCode": 403}}

    check("失敗は 1 行にする: SNS が断ったら HTTP の状態・エラーコード・理由（300 字まで）、ほかは例外の名前と中身",
          sns.describe(Denied()) == "HTTP 403 AuthorizationError: " + ("not authorized " + "x" * 400)[:300] and sns.describe(TimeoutError("timed out")) == "TimeoutError: timed out")

    # ---- 送り直し（client は差し替える）
    def run_send(outcomes, connect_errors=()):
        """client.publish が outcomes の順に成功（None）/ 失敗（例外）し、client を作る（認証情報を取る）のが connect_errors の順に失敗する。
        (送れた通数, publish の回数, client を作った回数, 待った秒, publish の引数, stderr)"""
        log = {"publish": [], "connect": 0, "slept": []}
        outcomes, connect_errors = list(outcomes), list(connect_errors)

        class Client:
            def publish(self, **kw):
                log["publish"].append(kw)
                o = outcomes.pop(0) if outcomes else None
                if o is not None:
                    raise o

        def connect(e):
            log["connect"] += 1
            if connect_errors:
                raise connect_errors.pop(0)
            return Client()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            sent = sns.send(env, ["a", "b"], sleep=log["slept"].append, connect=connect)
        return sent, len(log["publish"]), log["connect"], log["slept"], log["publish"], err.getvalue()

    sent, n, nconn, slept, calls, err = run_send([])
    check("2 通とも 1 回で送れれば待たない。client（認証情報）は 1 回だけ作って使い回す。publish するのはトピック・件名・本文",
          (sent, n, nconn, slept, err) == (2, 2, 1, [], "") and calls == [{"TopicArn": TOPIC, "Subject": "netops alert", "Message": m} for m in ("a", "b")])
    sent, n, nconn, slept, calls, err = run_send([Denied(), TimeoutError("timed out")])
    check("失敗したら client を作り直して（認証情報を取り直して）送り直す（1 通につき 3 回まで。待ちは 1 秒、2 秒）",
          (sent, n, nconn, slept) == (2, 4, 3, [1, 2]) and err.count("ERROR publish に失敗した") == 2 and "HTTP 403 AuthorizationError" in err and "TimeoutError: timed out" in err)
    sent, n, nconn, slept, calls, err = run_send([OSError("down")] * 3)
    check("3 回とも失敗した通は諦めて次の通へ進む（送れた通数を返す）", (sent, n, slept) == (1, 4, [1, 2]) and "（3/3）" in err and calls[-1]["Message"] == "b")
    sent, n, nconn, slept, calls, err = run_send([], connect_errors=[RuntimeError("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI が無い")] * 3)
    check("認証情報を取れないのも失敗として数えて送り直す", (sent, n, nconn, slept) == (1, 1, 4, [1, 2]) and err.count("RuntimeError: AWS_CONTAINER_CREDENTIALS_RELATIVE_URI が無い") == 3)
    try:
        run_send([], connect_errors=[ModuleNotFoundError("No module named 'boto3'")]); raised = None
    except ImportError as e:
        raised = e
    check("boto3 が読めない（Splunk の Python に無い）ときは送り直さずに止まる（main が理由を ERROR で残して 3 にする）", isinstance(raised, ModuleNotFoundError))
    check("送り直しの回数と待ちの定数", (sns.ATTEMPTS, sns.TIMEOUT, sns.SUBJECT, sns.STATUSES) == (3, 10, "netops alert", ("firing", "resolved")))

    # ---- 本物の boto3 で、手元の偽の認証情報の口と偽の SNS へ publish する（boto3 が無ければ飛ばす。ops/check.sh は dev のグループで入れる）
    try:
        import boto3  # noqa: F401
    except ImportError:
        print("-- boto3 が無いので、本物の boto3 での publish は確かめない")
    else:
        from botocore.utils import ContainerMetadataFetcher
        seen = {"creds": 0, "posts": [], "fail": []}
        CRED_JSON = json.dumps({"AccessKeyId": CREDS[0], "SecretAccessKey": CREDS[1], "Token": CREDS[2], "Expiration": "2099-01-01T00:00:00Z"}).encode()

        class Fake(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def reply(self, code, body, ctype):
                self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body)

            def do_GET(self):   # 認証情報の口（ECS の 169.254.170.2 の代わり）
                seen["creds"] += 1
                self.reply(200 if self.path == "/creds" else 404, CRED_JSON, "application/json")

            def do_POST(self):   # SNS の Query API
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                seen["posts"].append({"path": self.path, "auth": self.headers.get("Authorization", ""), "token": self.headers.get("X-Amz-Security-Token"),
                                      "form": dict(urllib.parse.parse_qsl(body))})
                if seen["fail"]:
                    status, code = seen["fail"].pop(0)
                    return self.reply(status, f'<ErrorResponse xmlns="http://sns.amazonaws.com/doc/2010-03-31/"><Error><Type>Sender</Type><Code>{code}</Code>'
                                              f'<Message>not authorized</Message></Error><RequestId>r</RequestId></ErrorResponse>'.encode(), "text/xml")
                self.reply(200, b'<PublishResponse xmlns="http://sns.amazonaws.com/doc/2010-03-31/"><PublishResult><MessageId>m-1</MessageId></PublishResult>'
                                b'<ResponseMetadata><RequestId>r</RequestId></ResponseMetadata></PublishResponse>', "text/xml")

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        local = {"ALERTS_TOPIC_ARN": TOPIC, "AWS_REGION": "us-west-2", "AWS_CONTAINER_CREDENTIALS_FULL_URI": base + "/creds", "AWS_ENDPOINT_URL_SNS": base + "/"}
        # プロセスの環境変数に紛らわしい値を置く（boto3 がこれらを見たら、届かない・別の鍵で署名する・別のリージョンへ行く・打ち直す）
        dead = "http://127.0.0.1:9"
        noise = {"HTTP_PROXY": dead, "HTTPS_PROXY": dead, "http_proxy": dead, "https_proxy": dead, "ALL_PROXY": dead, "AWS_ENDPOINT_URL": dead,
                 "AWS_ENDPOINT_URL_SNS": dead, "AWS_ACCESS_KEY_ID": "AKIDENV", "AWS_SECRET_ACCESS_KEY": "env-secret", "AWS_REGION": "eu-west-1",
                 "AWS_DEFAULT_REGION": "eu-west-1", "AWS_MAX_ATTEMPTS": "9", "AWS_RETRY_MODE": "standard",
                 "AWS_CONFIG_FILE": os.path.join(tmp, "none"), "AWS_SHARED_CREDENTIALS_FILE": os.path.join(tmp, "none")}
        drop = ("NO_PROXY", "no_proxy", "AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI")
        keep = {k: os.environ.get(k) for k in (*noise, *drop)}
        os.environ.update(noise)
        for k in drop:
            os.environ.pop(k, None)
        try:
            c = sns.sns_client(local)
            check("本物の boto3: リージョンはトピックの ARN（AWS_REGION の環境変数ではない）、宛先は AWS_ENDPOINT_URL_SNS（env ファイルの値。プロセスの環境変数ではない）",
                  c.meta.region_name == "ap-northeast-1" and c.meta.endpoint_url.rstrip("/") == base and seen["creds"] == 1)
            c = sns.sns_client({"ALERTS_TOPIC_ARN": TOPIC, "AWS_CONTAINER_CREDENTIALS_FULL_URI": base + "/creds"})
            check("本物の boto3: AWS_ENDPOINT_URL_SNS が無ければトピックのリージョンの SNS（VPC のインターフェース型エンドポイントがこの名前を引き受ける）",
                  c.meta.endpoint_url == "https://sns.ap-northeast-1.amazonaws.com")
            check("本物の boto3: RELATIVE_URI は ECS の口（http://169.254.170.2）に足して引く",
                  ContainerMetadataFetcher().full_url("/v2/credentials/abc") == "http://169.254.170.2/v2/credentials/abc")
            try:
                sns.sns_client({"ALERTS_TOPIC_ARN": TOPIC}); raised = ""
            except RuntimeError as e:
                raised = str(e)
            check("本物の boto3: 口が無い（タスクロールが付いていない）ときは理由を言って止まる。アクセスキーの環境変数へは逃げない",
                  "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI が無い" in raised)
            seen.update(creds=0, posts=[], fail=[(500, "InternalError")])
            slept, err = [], io.StringIO()
            with contextlib.redirect_stderr(err):
                sent = sns.send(local, [texts[0], "{}"], sleep=slept.append)
            ok = [p for p in seen["posts"]]
            check("本物の boto3: 偽の SNS へ 2 通届く。本文は Query API の Publish（トピック・件名・アラートの JSON）で、"
                  "一時的な認証情報（token 付き）でトピックのリージョンの sns として SigV4 で署名してある。プロキシの環境変数は通らない",
                  sent == 2 and len(ok) == 3 and ok[1]["path"] == "/"
                  and ok[1]["form"] == {"Action": "Publish", "Version": "2010-03-31", "TopicArn": TOPIC, "Subject": "netops alert", "Message": texts[0]}
                  and ok[2]["form"]["Message"] == "{}"
                  and all(p["auth"].startswith("AWS4-HMAC-SHA256 Credential=AKIDTEST/") and "/ap-northeast-1/sns/aws4_request" in p["auth"] and p["token"] == "test-token"
                          for p in ok))
            check("本物の boto3: SNS の 500 は boto3 の中で打ち直さず（AWS_MAX_ATTEMPTS も見ない）、send が client を作り直して（認証情報を取り直して）送り直す",
                  slept == [1] and seen["creds"] == 2 and "ERROR publish に失敗した（1/3）: HTTP 500 InternalError: not authorized" in err.getvalue())
            seen.update(creds=0, posts=[], fail=[(403, "AuthorizationError")] * 3)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                sent = sns.send(local, ["{}"], sleep=lambda s: None)
            check("本物の boto3: 3 回とも断られたら 0 通。ログは HTTP の状態とエラーコードだけで、認証情報は出ない",
                  sent == 0 and len(seen["posts"]) == 3 and err.getvalue().count("HTTP 403 AuthorizationError: not authorized") == 3
                  and not any(v in err.getvalue() for v in (*CREDS, "AKIDENV", "env-secret")))
        finally:
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            srv.shutdown()

    # ---- main（Splunk が --execute で起こし、標準入力に JSON を渡す）
    def run_main(argv, stdin, envd=None, sent=None, fail=None):
        published = []
        def send(e, texts, sleep=None):
            if fail:
                raise fail
            published.extend(texts)
            return len(texts) if sent is None else sent
        keep = sns.load_env, sns.send
        sns.load_env, sns.send = (lambda: dict(env if envd is None else envd)), send
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = sns.main(argv, io.StringIO(stdin))
        finally:
            sns.load_env, sns.send = keep
        return rc, published, err.getvalue()

    PAYLOAD = json.dumps({"search_name": "netops_gnmi", "results_file": RESULTS, "session_key": "must-not-be-logged"})
    rc, published, err = run_main(["netops_sns.py", "--execute"], PAYLOAD)
    check("main: 結果のファイルを読んで publish し、0 で終わる（件数を 1 行で残す）",
          rc == 0 and len(published) == 1 and [a["device_id"] for a in json.loads(published[0])["alerts"]] == ["dc1-leaf-01", "dc1-leaf-02"]
          and "INFO search=netops_gnmi rows=2 alerts=2 published=1/1" in err)
    check("main: Splunk のセッションキーはログに出さない", "must-not-be-logged" not in err)
    check("main: --execute 以外では何もしない（1）", run_main(["netops_sns.py"], PAYLOAD)[0] == 1 and run_main(["netops_sns.py", "--other"], PAYLOAD)[:2] == (1, []))
    rc, published, err = run_main(["netops_sns.py", "--execute"], PAYLOAD, envd={"AWS_REGION": "ap-northeast-1"})
    check("main: トピックの ARN が無ければ送らずに 2（理由を ERROR で残す）", rc == 2 and published == [] and "ERROR ALERTS_TOPIC_ARN が無い" in err)
    check("main: 送れなかった通があれば 2（Splunk の sendmodalert に失敗として残る）", run_main(["netops_sns.py", "--execute"], PAYLOAD, sent=0)[0] == 2)
    rc, published, err = run_main(["netops_sns.py", "--execute"], json.dumps({"results_file": os.path.join(tmp, "none.csv.gz")}))
    check("main: 結果のファイルが無い・JSON が壊れているなどは traceback を出さずに 3", rc == 3 and "ERROR Unexpected error: FileNotFoundError" in err and "Traceback" not in err
          and run_main(["netops_sns.py", "--execute"], "{壊れた")[0] == 3 and run_main(["netops_sns.py", "--execute"], PAYLOAD, fail=ValueError("x"))[0] == 3)
    rc, published, err = run_main(["netops_sns.py", "--execute"], PAYLOAD, fail=ModuleNotFoundError("No module named 'boto3'"))
    check("main: boto3 が読めない（Splunk の版を変えて Splunk の Python から無くなった）ときは、理由と確かめ方を 1 行で残して 3（traceback は出さない）",
          rc == 3 and published == [] and len(err.splitlines()) == 1 and "Traceback" not in err
          and f"ERROR boto3 を読めない（ModuleNotFoundError: No module named 'boto3'）。Splunk の Python {sys.version.split()[0]}（" in err
          and "tests/check_splunk_image.py" in err and "ffba169" in err)
    with gzip.open(RESULTS, "wt", encoding="utf-8", newline="") as f:
        f.write("device,kind,target,status,detail,starts_at\n")
    rc, published, err = run_main(["netops_sns.py", "--execute"], PAYLOAD)
    check("main: 行が 0 件なら何も送らずに 0", rc == 0 and published == [] and "published=0/0" in err)


# ---- Splunk の app（conf）
def parse_conf(text):
    """Splunk の conf → {stanza: {key: value}}。行末の \\ は次の行へ続く（続きの行はコメントにならない）"""
    out, stanza, key = {}, None, None
    for line in text.splitlines():
        if key is not None:
            out[stanza][key] += "\n" + line.rstrip("\\").rstrip() if line.endswith("\\") else "\n" + line
            if not line.endswith("\\"):
                key = None
            continue
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            stanza = s[1:-1]; out[stanza] = {}
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        out[stanza][k] = v.rstrip("\\").rstrip() if v.endswith("\\") else v
        if v.endswith("\\"):
            key = k
    return out


APP = ("app", "splunk", "netops_alerts")
saved = parse_conf(read(*APP, "default", "savedsearches.conf"))
actions = parse_conf(read(*APP, "default", "alert_actions.conf"))
check("保存済みサーチは 4 つ（SNMP のポーリングの IF、gNMI の BGP / IS-IS、trap、trap を時間で閉じる）", list(saved) == ["netops_poll", "netops_gnmi", "netops_trap", "netops_trap_clear"])
COMMON = {"enableSched": "1", "cron_schedule": "* * * * *", "realtime_schedule": "0", "dispatch.latest_time": "+5m", "alert.track": "0", "alert.digest_mode": "1",
          "alert.suppress": "0", "counttype": "number of events", "relation": "greater than", "quantity": "0", "action.netops_sns": "1"}
check("どのサーチも毎分走り、遅れても飛ばさず（realtime_schedule = 0）、結果が 1 行でもあれば全部の行をまとめて 1 回アクションへ渡す（digest）。抑制はしない",
      all({k: s.get(k) for k in COMMON} == COMMON for s in saved.values()))
check("アクションは netops_sns だけ（メールや webhook は付けない）", all([k for k in s if k.startswith("action.")] == ["action.netops_sns"] for s in saved.values()))
pipes = {n: [p.strip() for p in s["search"].split("\n| ")] for n, s in saved.items()}
check("どのサーチも _raw だけにしてから spath で項目を取る（KV_MODE = json の項目に spath を重ねると多値になって 1 行も出ない）",
      all(p[1] == "fields _time _indextime _raw source" and p[2] == "spath" and s["search"].count("spath") == 1 for (n, p), s in zip(pipes.items(), saved.values())))
check("どのサーチも最後は table device kind target status detail starts_at（アラートアクションが読む列）",
      all(p[-1] == "table device kind target status detail starts_at" for p in pipes.values())
      and all(re.search(rf"r\.get\(\"{c}\"\)", src) for c in ("device", "kind", "target", "status", "detail", "starts_at")))
check("窓は索引に入った時刻で切る（直前の 1 分を 10 秒手前にずらして 1 回だけ読む。前の値としてポーリングはその前の 10 分、gNMI は 24 時間も読む。"
      "イベントの時刻の窓 dispatch.earliest_time は広く取る）",
      pipes["netops_trap"][0].endswith("_index_earliest=-1m@m-10s _index_latest=@m-10s") and saved["netops_trap"]["dispatch.earliest_time"] == "-1h"
      and pipes["netops_poll"][0].endswith("_index_earliest=-11m@m-10s _index_latest=@m-10s") and saved["netops_poll"]["dispatch.earliest_time"] == "-1h"
      and pipes["netops_gnmi"][0].endswith("_index_earliest=-1441m@m-10s _index_latest=@m-10s") and saved["netops_gnmi"]["dispatch.earliest_time"] == "-25h"
      and pipes["netops_trap_clear"][0].endswith("_index_earliest=-70m@m-10s _index_latest=@m-10s") and saved["netops_trap_clear"]["dispatch.earliest_time"] == "-3h")
check("イベントは source で選ぶ（Spark の splunk_events が telegraf:<measurement> を付ける）。index は決め打ちしない（SPLUNK_INDEX で変わる）",
      pipes["netops_poll"][0].startswith('index=* source="telegraf:interface" ')
      and pipes["netops_gnmi"][0].startswith('index=* (source="telegraf:bgp_neighbor" OR source="telegraf:isis_interface") ')
      and all(pipes[n][0].startswith('index=* source="telegraf:snmp_trap" ') for n in ("netops_trap", "netops_trap_clear"))
      and 'f"telegraf:{r[\'measurement\'] or \'unknown\'}"' in read("app", "spark", "snmp_sinks.py"))
DEVICE = "eval device=coalesce('tags.sysName', 'tags.agent_host', 'tags.source')"
check("機器は tags.sysName > tags.agent_host > tags.source（gNMI と trap は IP。アラートアクションが DEVICE_MAP で名前に直す）",
      all(DEVICE in p for p in pipes.values()))
p = saved["netops_poll"]["search"]
check("ポーリング: ifOperStatus が 2 なら down（admin-state が disable の IF は down と数えない）。target は ifName（linkDown trap と同じ anomaly_id）",
      "eval target='tags.ifName', oper=tonumber('fields.ifOperStatus'), admin=coalesce(tonumber('fields.ifAdminStatus'), 1)" in p
      and 'eval down=if(oper==2 AND admin!=2, 1, 0), in_now=if(_indextime >= relative_time(now(), "-1m@m-10s"), 1, 0)' in p)
check("ポーリング: 前 = 今の 1 分より前に入った最後の値、今 = 読んだ 11 分ぶんの最後の値（イベントの時刻で）、starts_at = 今の状態になった時刻",
      'eval prev_down=if(in_now==0, down, null()), down_time=if(down==1, _time, null()), up_time=if(down==0, _time, null())' in p
      and "eventstats max(down_time) as last_down max(up_time) as last_up by device target" in p
      and "eval now_down=if(coalesce(last_down, 0) > coalesce(last_up, 0), 1, 0)" in p
      and "eval run_time=if(_time > coalesce(if(now_down==1, last_up, last_down), 0), _time, null())" in p
      and "stats latest(prev_down) as prev_down max(now_down) as now_down max(in_now) as arrived latest(admin) as now_admin min(run_time) as starts_at by device target" in p
      and 'eval kind="link_down", status=if(now_down==1, "firing", "resolved")' in p)
check("ポーリング: down のまま admin-state を disable にして閉じたときは、detail を is up ではなく is admin down にする",
      'eval detail=target." is ".if(status=="firing", "down", if(now_admin==2, "admin down", "up"))." (splunk: poll)"' in p)

# ---- ポーリングの遷移（SPL は動かせないので、表と参照実装と where の条件を突き合わせる）
# 前（null = 今の 1 分より前の 10 分に値が無い / 0 / 1）× 今（0 / 1 / null = 今の 1 分に値が入っていない）→ firing / なし（None）/ resolved
POLL_TABLE = {
    (None, 0): None, (None, 1): "firing", (None, None): None,
    (0, 0): None, (0, 1): "firing", (0, None): None,
    (1, 0): "resolved", (1, 1): None, (1, None): None,
}
POLL_BACK = (int(re.search(r"_index_earliest=-(\d+)m@m-10s", pipes["netops_poll"][0]).group(1)) - 1) * 60   # 前として読む秒数（今の 1 分を除く）


def poll_ref(events, T, back=POLL_BACK):
    """netops_poll の参照実装（IF 1 つぶん）。events = [(索引の時刻, イベントの時刻, down 0/1)]、T = その回の窓の終わり（@m-10s）。
    今の 1 分 = 索引の時刻が [T-60, T)、前 = [T-60-back, T-60)。返すのは (status, starts_at)。出さないときは (None, None)"""
    seen = [e for e in events if T - 60 - back <= e[0] < T]
    if not any(e[0] >= T - 60 for e in seen):
        return None, None
    prev = [e for e in seen if e[0] < T - 60]
    prev_down = max(prev, key=lambda e: e[1])[2] if prev else None
    now_down = max(seen, key=lambda e: e[1])[2]
    status = "firing" if now_down == 1 and prev_down != 1 else "resolved" if prev_down == 1 and now_down == 0 else None
    if not status:
        return None, None
    changed = max((e[1] for e in seen if e[2] != now_down), default=float("-inf"))
    return status, min(e[1] for e in seen if e[1] > changed)


def poll_runs(events, n, back=POLL_BACK):
    return [poll_ref(events, 60 * k, back) for k in range(1, n + 1)]


def ev(t, down, delay=2):   # Telegraf の時刻 t のポーリング 1 回（delay 秒後に索引に入る）
    return (t + delay, t, down)


check("ポーリングの遷移: 参照実装が表のとおり（前の値は今の 1 分より前に入った最後のもの、今の 1 分に値が無ければ出さない）",
      all(poll_ref(([(10, 10, pd)] if pd is not None else []) + ([(70, 70, nd)] if nd is not None else []), 120)[0] == want
          for (pd, nd), want in POLL_TABLE.items()))
_pw = next(x for x in pipes["netops_poll"] if x.startswith("where arrived"))[len("where "):]
_py = re.sub(r"isnull\((\w+)\)", r"(\1 is None)", re.sub(r"isnotnull\((\w+)\)", r"(\1 is not None)", _pw)).replace(" AND ", " and ").replace(" OR ", " or ")


def poll_spl(prev_down, now):
    """SPL の where と status を同じ値で評価する。今が null（今の 1 分に値が無い）なら arrived = 0 で、now_down は前の値（前も無ければ 0 と 1 の両方）"""
    outs = set()
    for now_down in ([now] if now is not None else [prev_down] if prev_down is not None else [0, 1]):
        ok = eval(_py, {}, {"arrived": int(now is not None), "prev_down": prev_down, "now_down": now_down})
        outs.add(("firing" if now_down == 1 else "resolved") if ok else None)
    return outs


check("ポーリングの遷移: SPL の where（と status=if(now_down==1, …)）が表と同じ組み合わせで出す",
      _pw == "arrived==1 AND ((isnull(prev_down) AND now_down==1) OR (isnotnull(prev_down) AND prev_down!=now_down))"
      and all(poll_spl(pd, nd) == {want} for (pd, nd), want in POLL_TABLE.items()))
check("ポーリングの遷移: 参照実装の前の長さは SPL の窓（-11m@m-10s から今の 1 分を除いた 10 分）", POLL_BACK == 600)
check("ポーリング: 前の 1 分が空（Spark のバッチが境界の前後に揺れた）でも、down が続いている IF の firing を出し直さない",
      poll_runs([ev(10, 0), ev(70, 1), ev(80, 1), ev(190, 1), ev(250, 1)], 5) == [(None, None), ("firing", 70), (None, None), (None, None), (None, None)])
check("ポーリング: 前の 1 分が空のあいだに up に戻った IF も resolved を出す（starts_at は up に戻った時刻）",
      poll_runs([ev(10, 0), ev(70, 1), ev(190, 0), ev(250, 0)], 5) == [(None, None), ("firing", 70), (None, None), ("resolved", 190), (None, None)])
check("ポーリング: 直前の 1 分だけと比べる形（前の長さ 60 秒）だと、上の 2 つで firing の出し直しと resolved の取りこぼしが起きる（この検査で見分けられる）",
      poll_runs([ev(10, 0), ev(70, 1), ev(190, 1)], 4, back=60)[3] == ("firing", 190)
      and poll_runs([ev(10, 0), ev(70, 1), ev(190, 0)], 4, back=60)[3] == (None, None))
check("ポーリング: starts_at は down になった時刻（1 分に何回 down を読んでも、最初の down。直前に up があればそのあと）",
      poll_runs([ev(10, 0), ev(65, 0), ev(75, 1), ev(85, 1), ev(95, 1)], 2)[1] == ("firing", 75)
      and poll_runs([ev(5, 1), ev(15, 1)], 1)[0] == ("firing", 5))
check("ポーリング: 送り直しの重複（前に入った古い down がもう一度入る）で、up に戻った IF を down にしない",
      poll_runs([ev(10, 1), ev(70, 0), (130, 10, 1)], 3) == [("firing", 10), ("resolved", 70), (None, None)])
check("ポーリング: 10 分を超えて値が途切れたあとも down が続いていれば、新しい発生として出し直す（限界。starts_at は途切れたあとの最初の値）",
      poll_runs([ev(10, 1), ev(70, 1), ev(790, 1)], 14)[13] == ("firing", 790)
      and poll_runs([ev(10, 1), ev(70, 1), ev(670, 1)], 12)[11] == (None, None))
SKIP_IF = re.search(r'NOT match\(target, "([^"]+)"\)', p).group(1)
check("ポーリング: 見ない IF は Grafana の link_down と同じ（ループバック・管理ポート・サブインタフェース）",
      [n for n in ("lo0", "mgmt0", "ethernet-1/1.0", "ethernet-1/1", "ethernet-1/49", "irb0") if not re.search(SKIP_IF, n)] == ["ethernet-1/1", "ethernet-1/49", "irb0"]
      and [n for n in ("lo0", "mgmt0", "ethernet-1/1.0", "ethernet-1/1", "ethernet-1/49", "irb0") if not re.fullmatch("(lo|mgmt).*|.*[.].*", n)] == ["ethernet-1/1", "ethernet-1/49", "irb0"]
      and 'ifName!~"(lo|mgmt).*|.*[.].*"' in read("app", "grafana", "provisioning", "alerting", "netops-prometheus.yaml"))
g = saved["netops_gnmi"]["search"]
check("gNMI: BGP は session_state が established 以外で bgp_down、IS-IS は oper_state が up 以外で isis_down",
      'eval kind=if(source=="telegraf:bgp_neighbor", "bgp_down", "isis_down")' in g and "'fields.session_state'" in g and "'fields.oper_state'" in g
      and 'eval down=if((kind=="bgp_down" AND state=="established") OR (kind=="isis_down" AND state=="up"), 0, 1), '
          'in_now=if(_indextime >= relative_time(now(), "-1m@m-10s"), 1, 0)' in g)
check("gNMI: target は BGP がピアのアドレス、IS-IS が IF 名（status Lambda の set_layer_status が引く名前）",
      "coalesce('tags.peer_address', 'tags.neighbor_peer_address'), 'tags.interface_name'" in g)
_gp = pipes["netops_gnmi"]
_pp = [x.replace("by device target", "by device kind target") for x in pipes["netops_poll"]]
check("gNMI: 前の値と比べて down かどうかが変わったときだけ出す。比べ方（前・今・出す条件・starts_at）はポーリングと同じ行（対象は device kind target）。"
      "detail の state は最後の値",
      all(x in _gp for x in _pp if x.startswith(("eval prev_down=", "eventstats ", "eval now_down=", "eval run_time=", "where arrived")))
      and "stats latest(prev_down) as prev_down max(now_down) as now_down max(in_now) as arrived latest(state) as state min(run_time) as starts_at by device kind target" in _gp
      and 'eval status=if(now_down==1, "firing", "resolved")' in _gp
      and [x.split(" ")[0] for x in _gp] == ["index=*", "fields", "spath", "eval", "eval", "eval", "eval", "where", "eval", "eval", "eventstats", "eval", "eval",
                                              "stats", "where", "eval", "eval", "table"])
GNMI_BACK = (int(re.search(r"_index_earliest=-(\d+)m@m-10s", _gp[0]).group(1)) - 1) * 60
check("gNMI の遷移: 参照実装の前の長さは SPL の窓（-1441m@m-10s から今の 1 分を除いた 24 時間。Grafana の last_over_time(...[24h]) と同じ）",
      GNMI_BACK == 86400 and "[24h]" in read("app", "grafana", "provisioning", "alerting", "netops-prometheus.yaml"))
_H = 3600
check("gNMI: 起動の直後（購読の最初の送信や、Spark が Kafka を頭から読み直した分が今の 1 分にまとめて入る）は、up / established の resolved を出さない。down は firing",
      poll_ref([(65, 10, 0), (66, 20, 0), (67, 30, 1)], 120, GNMI_BACK) == ("firing", 30)
      and poll_ref([(65, 10, 1), (66, 20, 0)], 120, GNMI_BACK) == (None, None)
      and poll_ref([(65, 10, 0)], 120, GNMI_BACK) == (None, None))
check("gNMI: Telegraf がつなぎ直して今の状態を送り直しても、前と同じなら出さない（24 時間以内）。down のまま送り直しても firing を出し直さない",
      poll_runs([ev(10, 0), ev(6 * _H + 10, 0)], 6 * 60 + 1, GNMI_BACK)[-1] == (None, None)
      and poll_runs([ev(10, 1), ev(6 * _H + 10, 1)], 6 * 60 + 1, GNMI_BACK)[-1] == (None, None))
check("gNMI: 変わったときは前の値が何時間前でも出す（down から 2 時間後に established → resolved。ポーリングの 10 分なら前が無くて出せない）",
      poll_runs([ev(10, 1), ev(2 * _H + 10, 0)], 2 * 60 + 1, GNMI_BACK)[-1] == ("resolved", 2 * _H + 10)
      and poll_runs([ev(10, 1), ev(2 * _H + 10, 0)], 2 * 60 + 1)[-1] == (None, None))
check("gNMI: 24 時間を超えて down が続いたあとの resolved は出さない。down を送り直すと firing を出し直す（限界。Grafana が閉じる）",
      poll_runs([ev(10, 1), ev(25 * _H + 10, 0)], 25 * 60 + 1, GNMI_BACK)[-1] == (None, None)
      and poll_runs([ev(10, 1), ev(25 * _H + 10, 1)], 25 * 60 + 1, GNMI_BACK)[-1] == ("firing", 25 * _H + 10))


def norm(oid, search):
    """サーチの中の OID の正規化（replace(replace(x, "^iso\\.", ".1."), "^1\\.", ".1.")）を同じ正規表現で写す"""
    m = re.search(r"""replace\(replace\('tags\.oid', "([^"]+)", "([^"]+)"\), "([^"]+)", "([^"]+)"\)""", search)
    return re.sub(m.group(3), m.group(4), re.sub(m.group(1), m.group(2), oid))


t, c = saved["netops_trap"]["search"], saved["netops_trap_clear"]["search"]
check("trap: OID は先頭を .1. に揃える（MIB が無いと Telegraf は iso.3.6… と書く）",
      [norm(o, t) for o in ("iso.3.6.1.6.3.1.1.5.3", "1.3.6.1.6.3.1.1.5.3", ".1.3.6.1.6.3.1.1.5.3", "iso.3.6.1.4.1.9.1.0.1")]
      == [".1.3.6.1.6.3.1.1.5.3"] * 3 + [".1.3.6.1.4.1.9.1.0.1"] and norm("iso.3.6.1.6.3.1.1.5.3", c) == ".1.3.6.1.6.3.1.1.5.3")
IGNORED = [".1.3.6.1.6.3.1.1.5.1", ".1.3.6.1.6.3.1.1.5.2", ".1.3.6.1.4.1.8072.4.0.2", ".1.3.6.1.4.1.8072.4.0.3"]
LINK = [".1.3.6.1.6.3.1.1.5.3", ".1.3.6.1.6.3.1.1.5.4"]
excluded = lambda s: re.findall(r'oid!="(\.[0-9.]+)"', s)   # noqa: E731
check("trap: 機器が起きた知らせ（coldStart / warmStart / nsNotifyShutdown / nsNotifyRestart）は異常にしない", excluded(t) == IGNORED)
_tskip = re.search(r'where kind!="link_down" OR NOT match\(target, "([^"]+)"\)', t)
check("trap: linkDown / linkUp でも、ポーリングと同じ IF（ループバック・管理ポート・サブインタフェース）は見ない。link 以外の trap の target（OID）は落とさない",
      _tskip is not None and _tskip.group(1) == SKIP_IF
      and pipes["netops_trap"].index(_tskip.group(0)) == pipes["netops_trap"].index('eval target=if(kind=="link_down", coalesce(if_name, if_descr, if_index, "?"), oid)') + 1
      and [n for n in ("ethernet-1/1.0", "lo0", "mgmt0", "ethernet-1/1", "5", "?") if not re.search(_tskip.group(1), n)] == ["ethernet-1/1", "5", "?"])
check("trap: linkDown は link_down の firing、linkUp は resolved。それ以外は kind = trap（target は trap の OID）の firing",
      'eval kind=if(oid==".1.3.6.1.6.3.1.1.5.3" OR oid==".1.3.6.1.6.3.1.1.5.4", "link_down", "trap")' in t
      and 'eval status=if(oid==".1.3.6.1.6.3.1.1.5.4", "resolved", "firing")' in t
      and 'eval target=if(kind=="link_down", coalesce(if_name, if_descr, if_index, "?"), oid)' in t)
vb = dict(re.findall(r'(if_\w+)=if\(isnull\(if_\w+\) AND match\(varbind, "([^"]+)"\)', t))
check("trap: IF は varbind の ifName > ifDescr > ifIndex（名前でも数値 OID でも、末尾の ifIndex 付きでも拾う。似た OID は拾わない）",
      list(vb) == ["if_name", "if_descr", "if_index"]
      and all(re.search(vb["if_name"], v) for v in ("ifName", "ifName.5", ".1.3.6.1.2.1.31.1.1.1.1.5"))
      and all(re.search(vb["if_descr"], v) for v in ("ifDescr.5", ".1.3.6.1.2.1.2.2.1.2.5"))
      and all(re.search(vb["if_index"], v) for v in ("ifIndex.5", ".1.3.6.1.2.1.2.2.1.1.5"))
      and not any(re.search(vb[k], v) for k, v in (("if_name", "ifNameX"), ("if_name", ".1.3.6.1.2.1.31.1.1.1.18.5"), ("if_index", ".1.3.6.1.2.1.2.2.1.10.5"), ("if_descr", "ifAdminStatus.5")))
      and 'foreach "fields.*" [ eval varbind=replace(replace("<<MATCHSTR>>", "^iso\\.", ".1."), "^1\\.", ".1."),' in t)
check("trap の解消: link の trap は対象にしない（linkUp が閉じる）。その機器から link 以外の trap が 10 分来なければ、機器ごとにまとめて resolved",
      excluded(c) == IGNORED + LINK and "stats max(_indextime) as last latest(_time) as starts_at by device oid" in c
      and "eventstats max(last) as device_last by device" in c
      and 'where device_last >= relative_time(now(), "-11m@m-10s") AND device_last < relative_time(now(), "-10m@m-10s")' in c
      and 'eval kind="trap", target=oid, status="resolved"' in c)
check("trap の解消の target は trap の firing と同じ（OID）なので、同じ anomaly_id を閉じる",
      "target=oid" in c and 'coalesce(if_name, if_descr, if_index, "?"), oid)' in t)
check("サーチが出す kind は受け手が知っているものだけ（link_down はワークフローを起こし、bgp_down / isis_down / trap は status だけ）",
      set(re.findall(r'"(link_down|bgp_down|isis_down|trap)"', p + g + t + c)) == {"link_down", "bgp_down", "isis_down", "trap"} and rules.START_KINDS == {"link_down"})
check("detail の末尾でどの入力から出したかが分かる（Grafana は (grafana: …)）: ポーリングは (splunk: poll)、gNMI は (splunk: gnmi)、"
      "link の trap は (splunk: linkDown trap) / (splunk: linkUp trap)、ほかの trap は (splunk: trap)",
      re.findall(r"\((splunk[^)]*)\)", p + g + t + c) == ["splunk: poll", "splunk: gnmi", "splunk: linkUp trap", "splunk: linkDown trap", "splunk: trap", "splunk: trap"])
check("アラートアクションの定義: カスタム、標準入力は JSON、Python は 3.13 と書く（boto3 は Splunk の Python のもの。latest だと Splunk を上げたとき黙って替わる）",
      actions == {"netops_sns": dict(actions["netops_sns"], **{"is_custom": "1", "payload_format": "json", "python.required": "3.13"})}
      and os.path.exists(os.path.join(ROOT, *APP, "bin", "netops_sns.py")) and os.path.exists(os.path.join(ROOT, *APP, "default", "data", "ui", "alerts", "netops_sns.html")))
check("spec（README/*.conf.spec）がある（無いと btool check が知らない設定として警告する）",
      "[netops_sns]" in read(*APP, "README", "alert_actions.conf.spec") and "action.netops_sns = " in read(*APP, "README", "savedsearches.conf.spec"))
appc, meta = parse_conf(read(*APP, "default", "app.conf")), parse_conf(read(*APP, "metadata", "default.meta"))
check("app は有効で、画面には出さない。サーチ・アクション・props は全体へ export する（HEC のイベントは他の app の文脈でも読める）",
      appc["install"]["state"] == "enabled" and appc["ui"]["is_visible"] == "0" and appc["package"]["id"] == "netops_alerts"
      and all(meta[k] == {"export": "system"} for k in ("alert_actions", "savedsearches", "props")))
check("props: telegraf:* の source は JSON として読む（画面で tags.* / fields.* を使えるように）", parse_conf(read(*APP, "default", "props.conf")) == {"source::telegraf:*": {"KV_MODE": "json"}})
check("app の中に認証情報や local/ は無い（公開リポジトリ）",
      not os.path.exists(os.path.join(ROOT, *APP, "local")) and not any(re.search(r"(?i)(password|token|secret)\s*=", read(*APP, "default", f))
                                                                         for f in ("savedsearches.conf", "alert_actions.conf", "app.conf", "props.conf")))

# ---- Splunk のイメージ
df = read("docker", "images", "splunk", "Dockerfile")
dcode = [l for l in df.splitlines() if l.strip() and not l.startswith("#")]
check("Splunk のイメージは上流の公式イメージに app と入口とヘルスチェックの突き合わせ（peers_check.py）を足すだけ（RUN は無い = arm64 の PC でも QEMU 無しでビルドできる。boto3 は同梱しない）",
      dcode == ["ARG SPLUNK_VERSION=10.4.4", "FROM splunk/splunk:${SPLUNK_VERSION}", "COPY --chown=splunk:splunk netops_alerts /opt/splunk-etc/apps/netops_alerts",
                "COPY --chmod=0755 entrypoint.sh /sbin/nwc-entrypoint.sh", "COPY --chmod=0755 peers_check.py /sbin/nwc-peers-check.py",
                'ENTRYPOINT ["/sbin/nwc-entrypoint.sh"]', 'CMD ["start-service"]'])

# ---- search head のヘルスチェックに足す突き合わせ（app/splunk/peers_check.py。「Splunk をクラスターにする（004）」の手当て A）
pc = load("app/splunk/peers_check.py", "peers_check")
pcsrc = read("app", "splunk", "peers_check.py")
check("peers_check.py は OS の /usr/bin/python3 で標準ライブラリだけを読む（Splunk の Python に頼らない）。イメージの /sbin/nwc-peers-check.py を、"
      "クラスターの search head のヘルスチェック（splunk.tf）が checkstate.sh のあとに呼ぶ",
      pcsrc.startswith("#!/usr/bin/python3\n") and imported(ast.walk(ast.parse(pcsrc))) <= set(sys.stdlib_module_names)
      and '"/sbin/checkstate.sh && /sbin/nwc-peers-check.py"' in read("IaC", "terraform", "aws-managed", "pipeline", "analytics", "splunk.tf"))
check("peers_check.py: SPLUNK_CLUSTER_MASTER_URL（上流の入口と同じく名前だけ）を管理 API の URL にする（https:// とポート 8089 が無ければ足す）",
      pc.manager_base("splunk-cm.t-nwc-poc.internal") == "https://splunk-cm.t-nwc-poc.internal:8089"
      and pc.manager_base(" https://x:8089/ ") == "https://x:8089" and pc.manager_base("https://x") == "https://x:8089"
      and pc.manager_base("x:18089") == "https://x:18089")


def _cm(guid, status="Up", label=None):  # cluster/manager/peers の entry（name が GUID）
    return {"name": guid, "content": {"status": status, **({"label": label} if label else {})}}


def _sh(guid, status="Up", disabled=False, host="10.0.1.10:8089"):  # search/distributed/peers の entry（name が host:port）
    return {"name": host, "content": {"guid": guid, "status": status, "disabled": disabled}}


check("peers_check.py の missing: manager が Up と言う peer のうち、search head が同じ GUID の Up で持っていないもの（GUID の大小は見ない。manager も Down なら見ない。"
      "search head で無効・Down は持っていない扱い。名前は label、無ければ GUID）",
      pc.missing([_cm("AAA-1", label="idx-a"), _cm("BBB-2", label="idx-b"), _cm("CCC-3", "Down", "idx-c")], [_sh("aaa-1"), _sh("BBB-2", disabled=True)]) == ["idx-b"]
      and pc.missing([_cm("AAA-1", label="idx-a")], [_sh("AAA-1", "Down")]) == ["idx-a"]
      and pc.missing([_cm("AAA-1", label="idx-a")], [_sh("AAA-1", disabled="1")]) == ["idx-a"]
      and pc.missing([_cm("NEW-9")], [_sh("OLD-1")]) == ["NEW-9"]
      and pc.missing([_cm("AAA-1"), _cm("BBB-2")], [_sh("AAA-1"), _sh("BBB-2", host="10.0.2.10:8089")]) == []
      and pc.missing([], [_sh("AAA-1")]) == [])
_PW = "pw-must-not-be-printed"
_PCENV = {"SPLUNK_CLUSTER_MASTER_URL": "splunk-cm.t-nwc-poc.internal", "SPLUNK_PASSWORD": _PW}


def _pc_main(environ, resp, d=None, out_file=None):  # resp: パス → entry の並び（例外なら投げる）。d: 判定の行と前の行を置く場所（同じ d なら続きの回）
    calls, out = [], io.StringIO()   # 戻り値は (終了コード, 出力, 問い合わせ, PID 1 の stdout の代わりのファイルの行)
    d = d or tempfile.mkdtemp()
    out_file = out_file or os.path.join(d, "pid1-stdout")

    def fetch(base, path, password):
        calls.append((base, path, password))
        if isinstance(resp[path], Exception):
            raise resp[path]
        return resp[path]
    with contextlib.redirect_stdout(out):
        rc = pc.main(environ=environ, fetch=fetch, out=out_file, state_file=os.path.join(d, "state"))
    lines = open(out_file, encoding="utf-8").read().splitlines() if os.path.exists(out_file) else []
    return rc, out.getvalue(), calls, lines


_ok = {pc.CM_PEERS: [_cm("AAA-1", label="idx-a")], pc.SH_PEERS: [_sh("AAA-1")]}
_runs = {"none": _pc_main({"SPLUNK_PASSWORD": _PW}, _ok), "ok": _pc_main(_PCENV, _ok),
         "lost": _pc_main(_PCENV, {pc.CM_PEERS: [_cm("AAA-1", label="idx-a"), _cm("BBB-2", label="idx-b")], pc.SH_PEERS: [_sh("AAA-1"), _sh("OLD-1")]}),
         "cm_down": _pc_main(_PCENV, {pc.CM_PEERS: OSError("refused"), pc.SH_PEERS: [_sh("AAA-1")]}),
         "sh_down": _pc_main(_PCENV, {pc.CM_PEERS: [_cm("AAA-1")], pc.SH_PEERS: TimeoutError("timed out")})}
check("peers_check.py の main: SPLUNK_CLUSTER_MASTER_URL が無い（standalone）なら聞かずに 0。manager に聞けなければ 0（manager が落ちただけで search head を入れ替えない）、"
      "search head の peers を読めない・食い違えば 1（label を出す）。問い合わせは manager と自分の管理 API へ admin のパスワードで、パスワードは出力しない",
      _runs["none"][:3] == (0, "", [])
      and _runs["ok"][:3] == (0, "", [("https://splunk-cm.t-nwc-poc.internal:8089", pc.CM_PEERS, _PW), ("https://127.0.0.1:8089", pc.SH_PEERS, _PW)])
      and _runs["lost"][0] == 1 and _runs["lost"][1].rstrip().endswith(": idx-b")
      and _runs["cm_down"][0] == 0 and "OSError" in _runs["cm_down"][1] and len(_runs["cm_down"][2]) == 1
      and _runs["sh_down"][0] == 1 and "TimeoutError" in _runs["sh_down"][1]
      and not any(_PW in r[1] for r in _runs.values()))
check("peers_check.py の判定の行（手当て B）: ok / mismatch / skip / error を「nwc-peer-check state=<判定> reason=<理由>」の 1 行で PID 1 の stdout（/proc/1/fd/1）に書く。"
      "理由は Up の peer の数・消えた peer の名前・例外の型だけで、例外の文やパスワードは入れない。standalone は書かない",
      pc.OUT == "/proc/1/fd/1" and pc.PREFIX == "nwc-peer-check"
      and _runs["none"][3] == [] and _runs["ok"][3] == ["nwc-peer-check state=ok reason=peers_up:1"]
      and _runs["lost"][3] == ["nwc-peer-check state=mismatch reason=lost:idx-b"]
      and _runs["cm_down"][3] == ["nwc-peer-check state=skip reason=manager_unreachable:OSError"]
      and _runs["sh_down"][3] == ["nwc-peer-check state=error reason=sh_peers_unreadable:TimeoutError"]
      and _pc_main(_PCENV, {pc.CM_PEERS: [_cm("AAA-1")], pc.SH_PEERS: RuntimeError(f"admin:{_PW}")})[3] == ["nwc-peer-check state=error reason=sh_peers_unreadable:RuntimeError"])
_pcd = tempfile.mkdtemp()
_two = {pc.CM_PEERS: [_cm("AAA-1", label="idx-a"), _cm("BBB-2", label="idx-b")], pc.SH_PEERS: [_sh("AAA-1"), _sh("BBB-2")]}
_gone = {pc.CM_PEERS: [_cm("AAA-1", label="idx a"), _cm("BBB-2", label="idx-b")], pc.SH_PEERS: [_sh("OLD-1"), _sh("BBB-2")]}
_seq = [_pc_main(_PCENV, r, _pcd)[3] for r in (_ok, _ok, _two, _gone, _gone, _two, _two)]
check("peers_check.py の判定の行: 前の回と同じ判定なら書かない（30 秒ごとに積まない）。Up の数が変われば書く。peer の名前の空白は _ にする",
      _seq[-1] == ["nwc-peer-check state=ok reason=peers_up:1", "nwc-peer-check state=ok reason=peers_up:2",
                   "nwc-peer-check state=mismatch reason=lost:idx_a", "nwc-peer-check state=ok reason=peers_up:2"]
      and [len(x) for x in _seq] == [1, 1, 2, 3, 3, 4, 4])
_pcd = tempfile.mkdtemp()
_nowrite = _pc_main(_PCENV, _ok, _pcd, out_file=os.path.join(_pcd, "no-such-dir", "fd1"))
_after = _pc_main(_PCENV, _ok, _pcd)
check("peers_check.py の判定の行: 書けないときはヘルスチェックの出力に型を出し、終了コードは判定のまま。前の行として覚えないので、次の回に書ける",
      _nowrite[0] == 0 and _nowrite[1] == f"判定の行を {os.path.join(_pcd, 'no-such-dir', 'fd1')} に書けない（FileNotFoundError）\n" and _nowrite[3] == []
      and _after[:2] == (0, "") and _after[3] == ["nwc-peer-check state=ok reason=peers_up:1"])
_PCENV2 = dict(_PCENV, NWC_PEERS_EXPECTED="2")
_one_down = {pc.CM_PEERS: [_cm("AAA-1", label="idx-a"), _cm("BBB-2", "Down", "idx-b")], pc.SH_PEERS: [_sh("AAA-1")]}
_deg = _pc_main(_PCENV2, _one_down)
_pcd = tempfile.mkdtemp()
_dseq = [_pc_main(_PCENV2, r, _pcd) for r in (_one_down, _one_down, _two, _one_down)]
_three = {pc.CM_PEERS: [_cm("AAA-1"), _cm("BBB-2"), _cm("CCC-3")], pc.SH_PEERS: [_sh("AAA-1"), _sh("BBB-2"), _sh("CCC-3")]}
check("peers_check.py: manager が Up と言う peer が NWC_PEERS_EXPECTED（indexer の数）より少なければ、食い違いが無くても ok でなく "
      "「state=degraded reason=peers_up:<Up の数>/<あるはずの数>」と書き、終了コードは 0（indexer が落ちているだけで、search head は入れ替えない）。"
      "そろえば ok に戻る。多いのは ok。食い違いは degraded より先（1）。NWC_PEERS_EXPECTED が無い・数でない・0 なら台数を見ない",
      _deg[0] == 0 and _deg[3] == ["nwc-peer-check state=degraded reason=peers_up:1/2"] and "1 台で、2 台に足りない" in _deg[1] and _PW not in _deg[1]
      and [r[0] for r in _dseq] == [0, 0, 0, 0]
      and _dseq[-1][3] == ["nwc-peer-check state=degraded reason=peers_up:1/2", "nwc-peer-check state=ok reason=peers_up:2",
                           "nwc-peer-check state=degraded reason=peers_up:1/2"]
      and _pc_main(_PCENV2, _three)[:4:3] == (0, ["nwc-peer-check state=ok reason=peers_up:3"])
      and _pc_main(dict(_PCENV, NWC_PEERS_EXPECTED="3"), {pc.CM_PEERS: [_cm("AAA-1", label="idx-a"), _cm("BBB-2", label="idx-b")],
                                                          pc.SH_PEERS: [_sh("AAA-1"), _sh("OLD-1")]})[:4:3] == (1, ["nwc-peer-check state=mismatch reason=lost:idx-b"])
      and all(_pc_main(dict(_PCENV, NWC_PEERS_EXPECTED=v), _one_down)[:4:3] == (0, ["nwc-peer-check state=ok reason=peers_up:1"]) for v in ("", "x", "0", "1"))
      and _pc_main(_PCENV, _one_down)[3] == ["nwc-peer-check state=ok reason=peers_up:1"])
_sh_env = read("IaC", "terraform", "aws-managed", "pipeline", "analytics", "splunk.tf")
check("splunk.tf: クラスターの search head のタスクだけに NWC_PEERS_EXPECTED（indexer の数 = splunk_az_num）を渡す（manager と indexer の環境変数には入れない）",
      re.search(r'\{ name = "SPLUNK_ROLE", value = "splunk_search_head" \},\n(?:\s*#[^\n]*\n)*\s*\{ name = "NWC_PEERS_EXPECTED", value = tostring\(var\.splunk_az_num\) \},\n'
                r'\s*\], local\.splunk_cluster_environment\) : \[\]\)', _sh_env) is not None
      and _sh_env.count("NWC_PEERS_EXPECTED") == 1)
_mgmt = []


class _Mgmt(http.server.BaseHTTPRequestHandler):  # Splunk の管理 API の代わり（平文の http）
    def log_message(self, *a): pass

    def do_GET(self):
        _mgmt.append((self.path, self.headers.get("Authorization", "")))
        body = json.dumps({"entry": [{"name": "AAA-1", "content": {"status": "Up"}}], "paging": {}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)


_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Mgmt)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
_pkeys = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")
_pkeep = {k: os.environ.get(k) for k in _pkeys}
os.environ.update({k: "http://127.0.0.1:9" for k in _pkeys if "no_" not in k.lower()})
os.environ.pop("NO_PROXY", None); os.environ.pop("no_proxy", None)
try:
    _got = pc.get(f"http://127.0.0.1:{_srv.server_port}", pc.CM_PEERS, "pw")
finally:
    for k, v in _pkeep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _srv.shutdown()
check("peers_check.py の get: admin の Basic 認証で ?output_mode=json&count=0 を GET し、entry の並びを返す（プロキシの環境変数は見ない）",
      _got == [{"name": "AAA-1", "content": {"status": "Up"}}]
      and _mgmt == [(pc.CM_PEERS + "?output_mode=json&count=0", "Basic YWRtaW46cHc=")])
img = load("tests/check_splunk_image.py", "check_splunk_image")
check("Splunk の版を変えたら、コンテナの検査（tests/check_splunk_image.py。その版の Python の boto3 で publish できるか）を走らせて CHECKED を書き換える: "
      "CHECKED の Splunk = Dockerfile の SPLUNK_VERSION、CHECKED の Python = python.required の版",
      img.CHECKED["splunk"] == re.search(r"^ARG SPLUNK_VERSION=(\S+)$", df, re.M).group(1)
      and img.CHECKED["python"].startswith(actions["netops_sns"]["python.required"] + ".") and set(img.CHECKED) == {"splunk", "python", "boto3"})
check("tests/check_splunk_image.py はコンテナを消すとき、イメージの VOLUME（/opt/splunk/etc・var）の匿名ボリュームも消す"
      "（docker rm -f -v。-v が無いと 1 回走らせるごとに約 1.3 GB 残る）",
      re.findall(r'run\("docker", "rm",[^)]*\)', read("tests", "check_splunk_image.py")) == ['run("docker", "rm", "-f", "-v", NAME, check_rc=False)'])
up = read_ops("up")
check("up.sh の SPLUNK_VERSION / GRAFANA_VERSION は Dockerfile の ARG の既定値と同じ",
      re.search(r"^SPLUNK_VERSION=([\d.]+)", up, re.M).group(1) == re.search(r"ARG SPLUNK_VERSION=([\d.]+)", df).group(1)
      and re.search(r"^GRAFANA_VERSION=([\d.]+)", up, re.M).group(1) == re.search(r"ARG GRAFANA_VERSION=([\d.]+)", read("docker", "images", "grafana", "Dockerfile")).group(1))
check("up.sh は Splunk と Grafana のイメージを <版>-<ディレクトリのハッシュ> のタグでビルドする（中身を変えたら別のタグ。Splunk は amd64、Grafana は arm64）",
      'SPLUNK_TAG=$(dir_tag "$SPLUNK_VERSION" app/splunk docker/images/splunk/Dockerfile)' in up and 'GRAFANA_TAG=$(dir_tag "$GRAFANA_VERSION" app/grafana docker/images/grafana/Dockerfile)' in up
      and 'docker buildx build --platform linux/amd64 --build-arg "SPLUNK_VERSION=$SPLUNK_VERSION" -t "$REG/$PREFIX-splunk:$SPLUNK_TAG" --push -f docker/images/splunk/Dockerfile app/splunk/' in up
      and 'docker buildx build --platform linux/arm64 --build-arg "GRAFANA_VERSION=$GRAFANA_VERSION" -t "$REG/$PREFIX-grafana:$GRAFANA_TAG" --push -f docker/images/grafana/Dockerfile app/grafana/' in up)
stf = read("IaC", "terraform", "aws-managed", "pipeline", "analytics", "splunk.tf")
check("Splunk のタスクはトピックの ARN と device map を環境変数で受け、タスクロールは土台のトピックへの sns:Publish だけを足す",
      '{ name = "ALERTS_TOPIC_ARN", value = local.alerts_topic_arn }' in stf and re.search(r'\{ name = "DEVICE_MAP", value = var\.device_map \}', stf) is not None
      and re.search(r'Action\s*=\s*\["sns:Publish"\]\s*\n\s*Resource\s*=\s*local\.alerts_topic_arn', stf) is not None and '"sns:*"' not in stf)
lt = load("app/containerlab/lab_topology.py", "lab_topology")
dm = subprocess.run([sys.executable, os.path.join(ROOT, "app", "containerlab", "lab_topology.py"), "app/containerlab", "--device-map"], capture_output=True, text=True, cwd=ROOT)
dmap = sns.parse_device_map(dm.stdout.strip())
check("device map（app/containerlab/lab_topology.py --device-map）は管理 IP と回線の IP を機器名に引ける形で、アラートアクションがそのまま読む",
      dm.returncode == 0 and len(dmap) >= 8 and all(sns.IPV4_RE.match(k) and re.fullmatch(r"[a-z0-9-]+", v) for k, v in dmap.items())
      and sns.device_name("10.255.2.1", dmap) == "dc1-leaf-01" and len(dm.stdout.strip()) < 4000)

# ---- Grafana のアラート（送り先と本文は netops.yaml、ルールは格納先ごとに netops-prometheus.yaml / netops-opensearch.yaml）
def nocomment(text):
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


gcode = nocomment(read("app", "grafana", "provisioning", "alerting", "netops.yaml"))
gpcode = nocomment(read("app", "grafana", "provisioning", "alerting", "netops-prometheus.yaml"))
gocode = nocomment(read("app", "grafana", "provisioning", "alerting", "netops-opensearch.yaml"))
rcode = gpcode + "\n" + gocode
tmpl = re.search(r'\{\{ define "netops\.sns" \}\}(.*)\{\{ end \}\}\s*$', gcode, re.S).group(1)
check("Grafana の本文の項目は Splunk のアラートアクションと同じ 6 つ（同じ順）、source は grafana",
      re.findall(r'"(\w+)":', tmpl) == ["source", "alerts"] + KEYS and tmpl.startswith('{"source":"grafana","alerts":['))


def render(alerts):
    """netops.sns のテンプレートを Go のテンプレートの代わりに写す（使っている構文だけ）。%q は JSON の文字列と同じ引用"""
    m = re.fullmatch(r"(.*)\{\{ range \$i, \$a := \.Alerts \}\}(.*)\{\{ end \}\}(.*)", tmpl, re.S)
    out = []
    for i, a in enumerate(alerts):
        s = m.group(2).replace("{{ if $i }},{{ end }}", "," if i else "")
        s = s.replace('{{ printf "%q" (or $a.Labels.sysName $a.Labels.source) }}', json.dumps(a["labels"].get("sysName") or a["labels"].get("source", "")))
        s = re.sub(r'\{\{ printf "%q" \$a\.Labels\.(\w+) \}\}', lambda x: json.dumps(a["labels"].get(x.group(1), "")), s)
        s = re.sub(r'\{\{ printf "%q" \$a\.Annotations\.(\w+) \}\}', lambda x: json.dumps(a["annotations"].get(x.group(1), "")), s)
        s = s.replace('{{ printf "%q" $a.Status }}', json.dumps(a["status"])).replace("{{ $a.StartsAt.Unix }}", str(a["starts_at"]))
        out.append(s)
    return m.group(1) + "".join(out) + m.group(3)


body = render([{"status": "firing", "labels": {"sysName": "dc1-leaf-01", "ifName": "ethernet-1/49", "kind": "link_down", "target": "ethernet-1/49"},
                "annotations": {"detail": "ethernet-1/49 is down (grafana: poll)"}, "starts_at": 1790000000},
               {"status": "resolved", "labels": {"sysName": "DC1-Spine-01", "kind": "link_down", "target": 'eth"x'},
                "annotations": {"detail": 'eth"x is down (grafana: poll)'}, "starts_at": 1790000030},
               {"status": "firing", "labels": {"source": "203.0.113.99", "peer_address": "10.255.0.9", "kind": "bgp_down", "target": "10.255.0.9"},
                "annotations": {"detail": "bgp session to 10.255.0.9 is not established (grafana: gnmi)"}, "starts_at": 1790000060}])
check("Grafana の本文は埋めたあと JSON になる（テンプレートの構文は全部埋まる。値の中の \" も壊さない。機器名が無ければ送り元の IP）",
      "{{" not in body and [(a["device_id"], a["target"], a["detail"]) for a in json.loads(body)["alerts"]]
      == [("dc1-leaf-01", "ethernet-1/49", "ethernet-1/49 is down (grafana: poll)"), ("DC1-Spine-01", 'eth"x', 'eth"x is down (grafana: poll)'),
          ("203.0.113.99", "10.255.0.9", "bgp session to 10.255.0.9 is not established (grafana: gnmi)")]
      and all(list(a) == KEYS for a in json.loads(body)["alerts"]))
check("受け手がそのまま読める: Grafana の link_down は Splunk の linkDown trap と同じ anomaly_id（同じ障害は 1 つのワークフロー）",
      [(a["anomaly_id"], a["status"], a["first_seen"], a["source"]) for a in rules.alerts_from_message(body)][:2]
      == [("dc1-leaf-01#link_down#ethernet-1/49", "firing", 1790000000, "grafana"), ("dc1-spine-01#link_down#eth\"x", "resolved", 1790000030, "grafana")]
      and rules.alerts_from_message(sns.messages(sns.alerts_from_rows([{"device": "10.255.2.1", "kind": "link_down", "target": "ethernet-1/49", "status": "firing"}], dmap))[0], now=1)[0]["anomaly_id"]
      == "dc1-leaf-01#link_down#ethernet-1/49")
check("$ は二重にしない（$$ と書くと Grafana 13.2.2 が起動しない）。${…} は連絡先の環境変数 2 つだけ。ルールのファイルは $ を持たない"
      "（provisioning がラベルの $labels を環境変数として消すので .Labels で書く）",
      "$$" not in gcode and set(re.findall(r"\$\{(\w+)\}", gcode)) == {"ALERTS_TOPIC_ARN", "AWS_REGION"} and "$" not in rcode)


def grafana_rules(code):
    """ルールのファイルを rule ごとに切り分け、title → その rule の本文"""
    return {re.search(r"^\s*title: (\S+)$", r, re.M).group(1): r for r in re.split(r"\n(?=[ \t]*- uid: )", code)[1:]}


grules = dict(grafana_rules(gpcode), **grafana_rules(gocode))
check("ルールは 4 本（link_down / bgp_down / isis_down は Prometheus、trap は OpenSearch）。評価は 1 分ごと（Splunk の保存済みサーチと同じ）",
      sorted(grules) == ["bgp_down", "isis_down", "link_down", "trap"] and set(grafana_rules(gocode)) == {"trap"}
      and re.findall(r"^    interval: (\S+)$", rcode, re.M) == ["1m", "1m"] and "folder: nwc-alerts" in gpcode and "folder: nwc-alerts" in gocode)
check("どのルールもラベル kind（= title）/ target と注釈 detail（末尾が「(grafana: <入力>)」）を持ち、すぐ発火する",
      all(re.search(rf"labels:\s*\n\s*kind: {t}\n\s*(sysName: .*\n\s*)?target: '\{{\{{ .+ \}}\}}'\n", r) and "condition: C" in r and "for: 0s" in r
          for t, r in grules.items())
      and {t: re.search(r"detail: '.*\((grafana: \w+)\)'", r).group(1) for t, r in grules.items()}
      == {"link_down": "grafana: poll", "bgp_down": "grafana: gnmi", "isis_down": "grafana: gnmi", "trap": "grafana: trap"})
check("link_down: ifOperStatus が 2 の IF（ループバック・管理ポート・サブ IF・admin down は見ない。値で判定するので直れば次の評価で解消）。target は ifName",
      "expr: 'snmp_interface_ifOperStatus{ifName!~\"(lo|mgmt).*|.*[.].*\"} unless on (sysName, ifName) (snmp_interface_ifAdminStatus == 2)'" in grules["link_down"]
      and re.search(r"type: within_range\s*\n\s*params: \[1\.5, 2\.5\]", grules["link_down"]) is not None
      and "target: '{{ .Labels.ifName }}'" in grules["link_down"])
ss = read("app", "spark", "snmp_sinks.py")
check("bgp_down / isis_down: Spark が書く 1 / 0 の系列の直近 24 時間の最後の値が 0.5 未満なら発火。target は peer_address / interface_name（gNMI の tag）",
      "expr: 'last_over_time(snmp_bgp_neighbor_session_up[24h])'" in grules["bgp_down"] and "target: '{{ .Labels.peer_address }}'" in grules["bgp_down"]
      and "expr: 'last_over_time(snmp_isis_interface_oper_up[24h])'" in grules["isis_down"] and "target: '{{ .Labels.interface_name }}'" in grules["isis_down"]
      and all(re.search(r"type: lt\s*\n\s*params: \[0\.5\]", grules[t]) for t in ("bgp_down", "isis_down"))
      and '("bgp_neighbor", "session_state"): ("session_up", "established")' in ss and '("isis_interface", "oper_state"): ("oper_up", "up")' in ss)
check("ルールのメトリクス名とラベルは Spark が AMP に書く名前（snmp_<measurement>_<field>、tag はそのままラベル）で、データソースは uid: amp",
      'METRIC_PREFIX = "snmp"' in ss and gpcode.count("datasourceUid: amp") == 3
      and re.search(r"^\s*uid: amp$", read("app", "grafana", "provisioning", "datasources", "prometheus.yaml"), re.M) is not None
      and all(f in read("app", "telegraf", "telegraf.conf.in") for f in ("ifOperStatus", "ifAdminStatus", "ifName", "sysName")))
SPLUNK_TRAP_SKIP = re.findall(r'oid!="([.0-9]+)"', re.search(r"^\[netops_trap\]$(.*?)^\[", read("app", "splunk", "netops_alerts", "default", "savedsearches.conf"), re.S | re.M).group(1))
check("trap: 過去 10 分の snmp_trap を機器と OID ごとに数える（OpenSearch のデータソース uid: aoss-logs、文字列は .keyword）。除く OID は Splunk の netops_trap と"
      "同じに linkDown / linkUp を足したもの。target は OID",
      "datasourceUid: aoss-logs" in grules["trap"] and "from: 600" in grules["trap"] and "measurement.keyword:snmp_trap AND NOT tags.oid.keyword:(" in grules["trap"]
      and set(re.findall(r'"([.0-9]+)"', re.search(r"tags\.oid\.keyword:\((.*?)\)", grules["trap"]).group(1)))
      == set(SPLUNK_TRAP_SKIP) | {".1.3.6.1.6.3.1.1.5.3", ".1.3.6.1.6.3.1.1.5.4"} and len(SPLUNK_TRAP_SKIP) == 4
      and re.findall(r"field: (\S+)", grules["trap"]) == ["tags.sysName.keyword", "tags.oid.keyword", "'@timestamp'"]
      and """sysName: '{{ index .Labels "tags.sysName.keyword" }}'""" in grules["trap"] and """target: '{{ index .Labels "tags.oid.keyword" }}'""" in grules["trap"]
      and re.search(r"type: gt\s*\n\s*params: \[0\]", grules["trap"]) is not None
      and re.search(r"^\s*uid: aoss-logs$", read("app", "grafana", "provisioning", "datasources", "opensearch.yaml"), re.M) is not None)
def terms_buckets(sizes, shards):
    """grafana-opensearch-datasource 2.34.4 の termsBucketProduct / termsBucketEstimate（pkg/opensearch/lucene_handler.go）を写す。
    terms 1 つを shard 1 なら size、2 以上なら shards * (int(size * 1.5) + 10) と見積もって掛ける"""
    return math.prod(n if shards <= 1 else shards * (int(n * 1.5) + 10) for n in sizes)


def bucket_budget_ok(sizes, interval, shards, max_buckets=65535):
    """同じく bucketFloorInterval（pkg/tsdb/interval.go）。検査は date_histogram の interval が auto のときだけで、
    max_buckets * 90 / 100 を terms の見積もりで割って 20 未満なら「bucket budget out of bounds」"""
    return interval != "auto" or max_buckets * 90 // 100 // terms_buckets(sizes, shards) >= 20


tsizes = [int(x) for x in re.findall(r"size: '(\d+)'", grules["trap"])]
tinterval = re.search(r"^\s*interval: (\S+)$", grules["trap"], re.M).group(1)
check("trap の bucket budget の写しは AWS で見たエラーを再現する（AOSS の index は shard 2 で、機器 50 × OID 20 が 13600 になり auto で落ちる。shard 1 の OSS では通る）",
      terms_buckets([50, 20], 2) == 13600 and not bucket_budget_ok([50, 20], "auto", 2) and bucket_budget_ok([50, 20], "auto", 1))
check("trap の時間の区切りは固定の 30s（10 分を 20 個。auto だと terms の見積もりが shard の数で変わり、プラグインが評価をエラーにしうる）。"
      "terms は auto だったとしても shard 2 で通る大きさで、固定の区切り 21 個（端を含む）を掛けても 65535 に収まる",
      tinterval == "30s" and len(tsizes) == 2 and all(bucket_budget_ok(tsizes, i, s) for i in (tinterval, "auto") for s in (1, 2))
      and terms_buckets(tsizes, 2) * (600 // 30 + 1) <= 65535)
check("データが無い・クエリが失敗したときは直前の状態のまま（分からないときに発火も解消もしない）。trap だけは数えるものが無ければ解消（10 分来なければ閉じる）",
      all("noDataState: KeepLast" in grules[t] for t in ("link_down", "bgp_down", "isis_down")) and "noDataState: OK" in grules["trap"]
      and all("execErrState: KeepLast" in r for r in grules.values()))
check("ルールの kind のうちワークフローを起こすのは link_down だけ（bgp_down / isis_down / trap は記録だけ）", rules.START_KINDS == {"link_down"})
check("連絡先は SNS（鍵は書かない = タスクロールで SigV4）。解消も送る",
      "type: sns" in gcode and "topic_arn: ${ALERTS_TOPIC_ARN}" in gcode and re.search(r"sigv4:\s*\n\s*region: \$\{AWS_REGION\}", gcode) is not None
      and "disableResolveMessage: false" in gcode and "message: '{{ template \"netops.sns\" . }}'" in gcode
      and not re.search(r"(?i)access_key|secret_key|assume_role|profile", gcode + rcode) and "groups:" not in gcode)
check("通知ポリシー: 対象（機器 + target）ごとに 1 通、発火はすぐ、解消は 30 秒以内、直らないあいだは 4 時間ごとに送り直す",
      "receiver: nwc-sns" in gcode and "group_by: ['alertname', 'sysName', 'target']" in gcode and "group_wait: 0s" in gcode and "group_interval: 30s" in gcode and "repeat_interval: 4h" in gcode)
gdf = read("docker", "images", "grafana", "Dockerfile")
check("Grafana のイメージは provisioning を持ち、SigV4 を既定の認証情報（タスクロール）で使う",
      "COPY provisioning /etc/grafana/netops" in gdf and "GF_AUTH_SIGV4_AUTH_ENABLED=true" in gdf and "GF_AWS_ALLOWED_AUTH_PROVIDERS=default" in gdf)
gtf = read("IaC", "terraform", "aws-managed", "pipeline", "analytics", "grafana.tf")
check("Grafana のタスクはトピックの ARN を環境変数で受け、タスクロールは土台のトピックへの sns:Publish だけを足す",
      '{ name = "ALERTS_TOPIC_ARN", value = local.alerts_topic_arn }' in gtf and re.search(r'Action\s*=\s*\["sns:Publish"\]\s*\n\s*Resource\s*=\s*local\.alerts_topic_arn', gtf) is not None and '"sns:*"' not in gtf)


def start_sh(**env):
    """app/grafana/start.sh を一時ディレクトリに向けて走らせ、並んだアラートの定義とデータソースを返す"""
    with tempfile.TemporaryDirectory() as tmp:
        sh = read("app", "grafana", "start.sh").replace("SRC=/etc/grafana/netops", f"SRC={os.path.join(ROOT, 'app', 'grafana', 'provisioning')}")
        sh = sh.replace("/tmp/grafana-", f"{tmp}/grafana-").replace('exec /run.sh "$@"', 'echo "run $GF_PATHS_PROVISIONING"')
        assert "/etc/grafana" not in sh and "/tmp/grafana" not in sh.replace(tmp, "") and "exec " not in sh
        r = subprocess.run(["sh", "-c", sh], capture_output=True, text=True, env=dict({"PATH": os.environ["PATH"]}, **env))
        assert r.returncode == 0 and r.stdout.strip().endswith(f"run {tmp}/grafana-provisioning"), r.stderr
        return sorted(os.listdir(f"{tmp}/grafana-provisioning/alerting")), sorted(os.listdir(f"{tmp}/grafana-provisioning/datasources"))


AMP = "https://aps-workspaces.ap-northeast-1.amazonaws.com/workspaces/ws-x"
check("start.sh: アラートの定義は ALERTS_TOPIC_ARN があるときだけ並べ、ルールはデータソースがあるほうだけ（無いデータソースを読むルールは並べない）",
      start_sh(PROMETHEUS_URL=AMP, ALERTS_TOPIC_ARN=TOPIC) == (["netops-prometheus.yaml", "netops.yaml"], ["prometheus.yaml"])
      and start_sh(ALERTS_TOPIC_ARN=TOPIC, OPENSEARCH_URL="https://x") == (["netops-opensearch.yaml", "netops.yaml"], ["opensearch.yaml"])
      and start_sh(PROMETHEUS_URL=AMP, OPENSEARCH_URL="https://x", ALERTS_TOPIC_ARN=TOPIC)
      == (["netops-opensearch.yaml", "netops-prometheus.yaml", "netops.yaml"], ["opensearch.yaml", "prometheus.yaml"])
      and start_sh(PROMETHEUS_URL=AMP, OPENSEARCH_URL="https://x") == ([], ["opensearch.yaml", "prometheus.yaml"])
      and start_sh(ALERTS_TOPIC_ARN=TOPIC) == ([], []) and start_sh() == ([], []))

# ---- SNS のトピック（土台）と受け手の配線
atf = read("IaC", "terraform", "aws-managed", "base", "core", "alerts.tf")
acode = "\n".join(l for l in atf.splitlines() if not l.lstrip().startswith("#"))
check("トピックは土台（base/core）に 1 つ（<接頭辞>-alerts）、保存時の暗号化は AWS 管理の鍵",
      acode.count('resource "aws_sns_topic" ') == 1 and 'name = "${local.name_prefix}-alerts"' in acode and 'kms_master_key_id = "alias/aws/sns"' in acode)
check("トピックのポリシー: 許すのはこのアカウントだけ（Principal に * の Allow は無い）",
      re.search(r'sid\s*=\s*"OwnAccount".*?identifiers = \["arn:\$\{local\.partition\}:iam::\$\{local\.account_id\}:root"\]', acode, re.S) is not None
      and len(re.findall(r'identifiers = \["\*"\]', acode)) == 1 and acode.index('identifiers = ["*"]') > acode.index('effect    = "Deny"'))
deny = acode[acode.index('dynamic "statement"'):]
check("閉域（network_perimeter）のとき、VPC の外からの sns:Publish を拒む（拒むのは Publish だけ）",
      "for_each = var.network_perimeter ? [1] : []" in deny and 'sid       = "DenyOutsideVpc"' in deny and 'actions   = ["sns:Publish"]' in deny
      and re.findall(r'test\s*=\s*"(\w+)"\s*\n\s*variable\s*=\s*"([\w:]+)"', deny)
      == [("StringNotEqualsIfExists", "aws:SourceVpc"), ("BoolIfExists", "aws:ViaAWSService"), ("Bool", "aws:PrincipalIsAWSService"), ("ArnNotLike", "aws:PrincipalArn")]
      and "values   = [aws_vpc.this.id]" in deny and "values   = local.perimeter_exempt_principals" in deny)
check("土台は alerts_topic_arn を出し、閉域の IAM 側の Deny に sns:* がある（events:* はもう無い）",
      re.search(r'output "alerts_topic_arn" \{[^}]*value\s*=\s*aws_sns_topic\.alerts\.arn', read("IaC", "terraform", "aws-managed", "base", "core", "outputs.tf")) is not None
      and '"sns:*"' in read("IaC", "terraform", "aws-managed", "base", "core", "perimeter.tf") and '"events:*"' not in read("IaC", "terraform", "aws-managed", "base", "core", "perimeter.tf"))
TRY = 'alerts_topic_arn = try(data.terraform_remote_state.main.outputs.alerts_topic_arn, "")'
check("送り手（analytics）と受け手（workflow / graph）は土台の state からトピックを読む（古い土台なら apply の前に理由を言って止まる）",
      all(TRY in read("IaC", "terraform", "aws-managed", *r, "locals.tf") for r in (("pipeline", "analytics"), ("pipeline", "graph"), ("workflow",)))
      and all('condition     = local.alerts_topic_arn != ""' in read("IaC", "terraform", "aws-managed", *f) for f in
              (("pipeline", "analytics", "grafana.tf"), ("pipeline", "analytics", "splunk.tf"), ("pipeline", "graph", "sync.tf"), ("workflow", "events.tf"))))
wtf = read("IaC", "terraform", "aws-managed", "workflow", "events.tf")
check("受け手は 2 つ: SQS（raw message delivery。ワークフロー）と Lambda（status）",
      re.search(r'resource "aws_sns_topic_subscription" "anomalies" \{[^}]*protocol\s*=\s*"sqs"[^}]*raw_message_delivery\s*=\s*true', wtf, re.S) is not None
      and re.search(r'resource "aws_sns_topic_subscription" "status" \{[^}]*protocol\s*=\s*"lambda"', read("IaC", "terraform", "aws-managed", "pipeline", "graph", "sync.tf"), re.S) is not None)
check("EventBridge のルールと Spark からの put_events はどこにも無い",
      not any("aws_cloudwatch_event_" in read(f) or "events:PutEvents" in read(f)
              for f in glob.glob(os.path.join(ROOT, "IaC", "terraform", "aws-managed", "**", "*.tf"), recursive=True) if ".terraform" not in f)
      and "put_events" not in "\n".join(l for l in ss.splitlines() if not l.lstrip().startswith("#")).split('"""', 2)[2])

# ---- ops/check.sh
chk = read("ops", "check.sh")
# for の語を bash と同じように glob 展開する（check.sh は tests/test_*.py のグロブで回す。名指しなら名指しのまま）
listed = [os.path.relpath(p, ROOT) for w in re.search(r"\n  for t in ([^;]+); do\n", chk).group(1).split()
          for p in (sorted(glob.glob(os.path.join(ROOT, w))) if any(c in w for c in "*?[") else [os.path.join(ROOT, w)])]
check("check.sh は tests/ の test_*.py を全部走らせる（このテストも）",
      sorted(listed) == sorted("tests/" + os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "tests", "test_*.py"))) and "tests/test_alerts.py" in listed)
dirs = re.search(r"\nfind ([a-z ]+) -name '\*\.py'", chk).group(1).split()
tracked = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"], capture_output=True, text=True, cwd=ROOT).stdout.split()
check("check.sh の構文検査は .py のあるディレクトリを全部見る（app/splunk/ のアラートアクションと app/graph/ の Lambda も。どちらも app/ の下）",
      {p.split("/")[0] for p in tracked} <= set(dirs) and "app" in dirs
      and os.path.isdir(os.path.join(ROOT, "app", "splunk")) and os.path.isdir(os.path.join(ROOT, "app", "graph")))

# ---- lab.sh: 比べるための障害（fail-bgp / heal-bgp / trap-test）
lab = read("app", "containerlab", "lab.sh")
labc = {k: re.search(rf"(?:^|; ){k}=([^;\s]+)", lab, re.M).group(1) for k in ("BGP_NODE", "BGP_PEER", "ACC_VM", "TEST_TRAP_OID")}
check("lab.sh の fail-bgp / heal-bgp は BGP_NODE の設定にある iBGP の neighbor（BGP_PEER）の admin-state を disable / enable にする。使い方の表示に 3 つが載る",
      f"set / network-instance default protocols bgp neighbor {labc['BGP_PEER']} peer-group overlay" in read("app", "containerlab", "srlinux", labc["BGP_NODE"] + ".cli")
      and '"set / network-instance default protocols bgp neighbor $BGP_PEER admin-state $1" "commit now"' in lab
      and all(f"\n    bgp_admin {s}\n" in lab for s in ("disable", "enable"))
      and all(f"\n  {c})\n" in lab for c in ("fail-bgp", "heal-bgp", "trap-test"))
      and [i for i, l in enumerate(lab.splitlines(), 1) if l.startswith("#   lab.sh ")] == [3, 4, 5, 6] and "fail-bgp | heal-bgp | trap-test" in lab.splitlines()[3]
      and "  *) sed -n '2,6p' \"$SELF\"; exit 1 ;;" in lab and "      *) sed -n '6p' \"$SELF\"; exit 1 ;;" in lab and "telegraf run" in lab.splitlines()[5])
_ba = lab[lab.index("\nbgp_admin() {"):lab.index("\n}\n", lab.index("\nbgp_admin() {"))]
check("lab.sh の fail-bgp / heal-bgp は commit のあと state の admin-state を読み直し、変わっていなければ 1 で止まる（sr_cli の終了コードに頼らない。grep -q は pipe に繋がない）",
      '"info from state / network-instance default protocols bgp neighbor $BGP_PEER admin-state")' in _ba
      and 'grep -qw "admin-state $1" <<<"$st" || {' in _ba and _ba.rstrip().endswith("exit 1; }") and "| grep" not in _ba)
_dm = dict(kv.split("=") for kv in subprocess.run([sys.executable, os.path.join(ROOT, "app", "containerlab", "lab_topology.py"), os.path.join(ROOT, "app", "containerlab"), "--device-map"],
                                                   capture_output=True, text=True, check=True).stdout.strip().split(","))
check("lab.sh の trap-test の OID は Splunk の netops_trap も Grafana の trap ルールも除かない（どちらも kind = trap）。管理ネットワークの中（ACC_VM の netns）から"
      "機器の trap と同じ $MGMT_GW:162 へ送り、送り元の管理 IP は device map で ACC_VM になる",
      labc["TEST_TRAP_OID"] not in set(SPLUNK_TRAP_SKIP) | {".1.3.6.1.6.3.1.1.5.3", ".1.3.6.1.6.3.1.1.5.4"}
      and labc["TEST_TRAP_OID"] not in re.search(r"tags\.oid\.keyword:\((.*?)\)", grules["trap"]).group(1)
      and """nsenter -t "$pid" -n snmptrap -v2c -c "$SNMP_COMMUNITY" "$MGMT_GW:162" '' "$TEST_TRAP_OID" """ in lab
      and """pid=$(docker inspect -f '{{.State.Pid}}' "clab-$LAB-$ACC_VM")""" in lab
      and [ip for ip, n in _dm.items() if n == labc["ACC_VM"] and ip.startswith("203.0.113.")] == ["203.0.113.102"])
print(f"通過 {passed} / 失敗 0")
