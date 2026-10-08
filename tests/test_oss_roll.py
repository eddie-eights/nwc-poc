"""cycle 005（マネージドを OSS に置き換えた環境を作る）の、Kafka と OpenSearch の台を 1 台ずつ入れ替える手順（oss/ops/roll-nodes.sh と
oss/ops/roll_health.py。設計の未確定事項 2・4）の模擬テスト。terraform / aws / sleep / session-manager-plugin は偽物に差し替え、AWS には触れない。
  1. roll_health.py が ECS Exec の出力（Session Manager の案内と \\r が混ざる）から、Kafka（fenced でない broker・複製の足りないパーティション・
     KRaft の controller の Leader と遅れ）と OpenSearch（green・台の数・cluster manager）の健全さを判定し、plan の JSON から入れ替える台を選ぶ
  2. タスクの中で打つコマンド（ROLL_PROBE_KAFKA / ROLL_PROBE_OPENSEARCH）を手元の bash で、偽物の kafka-*.sh / curl / timeout に向けて打ち、
     印と終了コードが roll_health.py の読める形で出ること（--command の「/bin/bash -c '<コマンド>'」が 3 つの引数に割れることも）
  3. roll_nodes を偽物の道具で通す: リーダーでない台から 1 台ずつ -target で apply し、リーダーは最後。入れ替えた台の中からは見ない。
     変わる台が無い・初めて作る・OSS_ROLL=0 のときは何も入れ替えない。入れ替える前から健全でない・入れ替えたあと戻らない・サービスが安定しない・
     plan が失敗する・Session Manager plugin が無い、のどれでも止まり、残りの台を案内する（plan のファイルは残さない）。
     標準入力が端末でなければ、ECS Exec を script（util-linux の -q -c の形と BSD の形を出力の印で見分ける）で包み、script が無いか
     どちらの形でも打てなければ、何も入れ替えずに「端末から打つか OSS_ROLL=0」と案内して止まる。ECS Exec が
     「Cannot perform start session: EOF」で切れたら、roll_health.py はその行と同じ案内を理由にする
  4. roll-nodes.sh が読む terraform の output とサービス・コンテナの名前が、IaC/terraform/oss/ に実在する（ECS Exec が有効なことも）
実際の ECS Exec（Session Manager の非対話の動き、--command の割り方、入れ替え中の Lag の値）は AWS で確かめていない
（2026-10-08 に macOS から up.sh ごと script -q /dev/null で包んで一度通した。util-linux の script で包む形は AWS で確かめていない）。
実行は python3 tests/test_oss_roll.py"""
import importlib.util, json, os, pty, re, shlex, shutil, subprocess, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

passed = 0
def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)

def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()

spec = importlib.util.spec_from_file_location("roll_health", os.path.join(ROOT, "oss/ops/roll_health.py"))
rh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rh)

KAFKA_NODES = ["1", "2", "3"]
OS_NODES = ["1", "2", "cm"]

BANNER_HEAD = ("\r\nThe Session Manager plugin was installed successfully. Use the AWS CLI to start a session.\r\n\r\n\r\n"
               "Starting session with SessionId: ecs-execute-command-0123456789abcdef0\r\n")
BANNER_TAIL = "\r\n\r\nExiting session with sessionId: ecs-execute-command-0123456789abcdef0.\r\n\r\n"

def wrap(body):
    """ECS Exec の出力の形（前後に Session Manager の案内、行末は \\r\\n）"""
    return BANNER_HEAD + body.replace("\n", "\r\n") + BANNER_TAIL

def kafka_out(nodes=KAFKA_NODES, leader="2", fenced=(), urp=0, lag=None, rc=None, observers=(), leaders=None):
    rc = rc or {}
    lines = ["==nwc-roll brokers"]
    for n in nodes:
        lines.append(f"kafka-{n}.x-nwc-oss.internal:9092 (id: {n} rack: null isFenced: {'true' if n in fenced else 'false'}) -> (")
    lines += [f"==nwc-rc {rc.get('brokers', 0)}", "==nwc-roll urp"]
    lines += [f"\tTopic: nwc.telemetry\tTopicId: AAAAAAAAAAAAAAAAAAAAAA\tPartition: {i}\tLeader: 1\tReplicas: 1,2,3\tIsr: 1,2" for i in range(urp)]
    lines += [f"==nwc-rc {rc.get('urp', 0)}", "==nwc-roll quorum",
              "NodeId\tDirectoryId           \tLogEndOffset\tLag\tLastFetchTimestamp\tLastCaughtUpTimestamp\tStatus  "]
    leaders = leaders if leaders is not None else [leader]
    for n in nodes:
        lines.append(f"{n}\tAAAAAAAAAAAAAAAAAAAAAA\t1200\t{(lag or {}).get(n, 0)}\t1759900000000\t1759900000000\t"
                     f"{'Leader' if n in leaders else 'Follower'}")
    for n in observers:
        lines.append(f"{n}\tBBBBBBBBBBBBBBBBBBBBBB\t900\t300\t1759900000000\t1759900000000\tObserver")
    lines += [f"==nwc-rc {rc.get('quorum', 0)}", "==nwc-roll end"]
    return "\n".join(lines) + "\n"

def os_out(manager="cm", status="green", count=3, health=None, rc=None):
    rc = rc or {}
    body = health if health is not None else json.dumps({"cluster_name": "x-nwc-oss", "status": status, "number_of_nodes": count,
                                                          "number_of_data_nodes": 2, "active_shards": 12, "unassigned_shards": 0,
                                                          "initializing_shards": 0})
    return ("==nwc-roll health\n" + body + "\n\n" + f"==nwc-rc {rc.get('health', 0)}\n" + "==nwc-roll manager\n"
            + (f"opensearch-{manager}\n" if manager else "") + "\n" + f"==nwc-rc {rc.get('manager', 0)}\n" + "==nwc-roll end\n")

# ---------------------------------------------------------------- 1. roll_health.py
check("roll_health kafka: Session Manager の案内と \\r が混ざっても、3 台がそろい遅れが無ければ Leader の台を返す",
      rh.kafka(wrap(kafka_out(leader="2")), KAFKA_NODES) == ("2", None))
check("roll_health kafka: Observer の行（controller でない台）は数えない",
      rh.kafka(wrap(kafka_out(leader="3", observers=("4",))), KAFKA_NODES) == ("3", None))
check("roll_health kafka: 遅れが ROLL_KAFKA_MAX_LAG ちょうどは健全、1 つでも超えたら待つ",
      rh.kafka(kafka_out(lag={"3": rh.ROLL_KAFKA_MAX_LAG}), KAFKA_NODES)[0] == "2"
      and "遅れ" in (rh.kafka(kafka_out(lag={"3": rh.ROLL_KAFKA_MAX_LAG + 1}), KAFKA_NODES)[1] or ""))
for name, out, word in [
    ("fenced の broker", kafka_out(fenced=("3",)), "fenced でない broker"),
    ("broker が 2 台しか載らない", kafka_out(nodes=["1", "2"]), "fenced でない broker"),
    ("複製の足りないパーティション", kafka_out(urp=2), "複製が足りないパーティションが 2 個"),
    ("Leader が 2 台", kafka_out(leaders=["1", "2"]), "Leader が 1 台でない"),
    ("Leader が無い", kafka_out(leaders=[]), "Leader が 1 台でない"),
    ("kafka-broker-api-versions.sh が 124（timeout）で終わった", kafka_out(rc={"brokers": 124}), "brokers のコマンドが 124 で終わった"),
    ("kafka-metadata-quorum.sh が 1 で終わった", kafka_out(rc={"quorum": 1}), "quorum のコマンドが 1 で終わった"),
    ("urp の節に Exception", kafka_out().replace("==nwc-roll urp\n", "==nwc-roll urp\norg.apache.kafka.common.errors.TimeoutException: Timed out\n"), "urp にエラーが出た"),
    ("quorum の表の見出しが無い", kafka_out().replace("NodeId\t", "Node\t"), "見出しが読めない"),
    ("途中で切れた（urp から後が無い）", kafka_out().split("==nwc-roll urp")[0], "urp の節が無い"),
    ("ECS Exec がつながらない（印が無い）", "An error occurred (TargetNotConnectedException) when calling the ExecuteCommand operation: The execute command failed\n", "印が無い"),
    ("動いているタスクが無い（roll_exec の行だけ）", "x-nwc-oss-kafka-3 に動いているタスクが無い（None）\n", "動いているタスクが無い"),
]:
    leader, reason = rh.kafka(wrap(out), KAFKA_NODES)
    check(f"roll_health kafka: {name} → 健全でない（{word}）", leader is None and word in (reason or ""))
check("roll_health kafka: 印が無いときの理由に ECS Exec の出力の先頭を入れ、Session Manager の案内は入れない",
      "TargetNotConnectedException" in rh.kafka(wrap("An error occurred (TargetNotConnectedException)\n"), KAFKA_NODES)[1]
      and "Session Manager" not in rh.kafka(wrap("An error occurred (TargetNotConnectedException)\n"), KAFKA_NODES)[1]
      and "（出力が空）" in rh.kafka(wrap(""), KAFKA_NODES)[1])

check("roll_health opensearch: green で 3 台、cluster manager が opensearch-cm なら cm を返す（案内と \\r が混ざっても）",
      rh.opensearch(wrap(os_out()), OS_NODES) == ("cm", None)
      and rh.opensearch(wrap(os_out(manager="1")), OS_NODES) == ("1", None))
for name, out, word in [
    ("yellow（入れ替えた台のシャードを複製し直している）", os_out(status="yellow"), "status=yellow"),
    ("台が 2 台", os_out(count=2), "number_of_nodes=2"),
    ("cluster manager がほかの名前", os_out(manager="9"), "cluster manager が台のどれでもない（opensearch-9）"),
    ("cluster manager が空", os_out(manager=""), "cluster manager が台のどれでもない（空）"),
    ("_cluster/health が JSON でない", os_out(health="Unauthorized"), "_cluster/health を読めない（Unauthorized）"),
    ("_cluster/health が JSON の配列", os_out(health="[]"), "status=None"),
    ("curl が 7（つながらない）で終わった", os_out(health="", rc={"health": 7}), "health のコマンドが 7 で終わった"),
    ("パスワードの環境変数が見えない", "==nwc-roll nopass\n", "OPENSEARCH_INITIAL_ADMIN_PASSWORD が無い"),
    ("ECS Exec がつながらない", "An error occurred (InvalidParameterException)\n", "印が無い"),
]:
    leader, reason = rh.opensearch(wrap(out), OS_NODES)
    check(f"roll_health opensearch: {name} → 健全でない（{word}）", leader is None and word in (reason or ""))

def plan_json(changes):
    rcs = []
    for addr, actions in changes:
        m = re.match(r'^(data\.)?(\w+)\.(\w+)(?:\["([^"]+)"\])?$', addr)
        rcs.append({"address": addr, "mode": "data" if m.group(1) else "managed", "type": m.group(2), "name": m.group(3),
                    "index": m.group(4), "change": {"actions": actions}})
    return json.dumps({"format_version": "1.2", "resource_changes": rcs})

PLAN_MIXED = plan_json([
    ('aws_ecs_service.kafka["1"]', ["update"]),
    ('aws_ecs_service.kafka["2"]', ["no-op"]),
    ('aws_ecs_service.kafka["3"]', ["delete", "create"]),
    ('aws_ecs_service.kafka["4"]', ["create"]),
    ('aws_ecs_service.kafka["5"]', ["delete"]),
    ('aws_ecs_service.kafka["6"]', ["create", "delete"]),
    ('aws_ecs_task_definition.kafka["1"]', ["delete", "create"]),
    ('aws_ecs_service.kafka_ui', ["update"]),
    ('aws_ecs_service.opensearch["cm"]', ["update"]),
    ('data.aws_ecs_service.kafka["9"]', ["read"]),
])
check("roll_health plan: aws_ecs_service.<種類> の update と作り直し（delete・create の順はどちらでも）だけを、台の順に並べて出す",
      rh.plan(PLAN_MIXED, "kafka") == ["1", "3", "6"] and rh.plan(PLAN_MIXED, "opensearch") == ["cm"])
check("roll_health plan: 作るだけ・消すだけ・no-op・ほかのリソースは入れない。resource_changes が無ければ空",
      rh.plan(plan_json([('aws_ecs_service.kafka["4"]', ["create"]), ('aws_ecs_service.kafka["2"]', ["no-op"])]), "kafka") == []
      and rh.plan(json.dumps({"format_version": "1.2"}), "kafka") == [])

def run_health(args, stdin):
    return subprocess.run([sys.executable, os.path.join(ROOT, "oss/ops/roll_health.py")] + args, input=stdin,
                          capture_output=True, text=True, timeout=30)
r_ok, r_ng, r_use = run_health(["kafka"] + KAFKA_NODES, wrap(kafka_out(leader="1"))), run_health(["opensearch"] + OS_NODES, os_out(status="red")), run_health(["kafka"], "")
r_plan = run_health(["plan", "kafka"], PLAN_MIXED)
EOF_OUT = "==nwc-roll brokers\nCannot perform start session: EOF\n"
eof_reason, other_reason = rh.kafka(wrap(EOF_OUT), KAFKA_NODES)[1] or "", rh.opensearch(wrap("Cannot perform start session: unexpected status 403\n"), OS_NODES)[1] or ""
check("roll_health.py: ECS Exec が「Cannot perform start session」で切れたら節の有無より先にその行を理由にし、EOF なら「端末から打つか OSS_ROLL=0」を足す",
      "ECS Exec のセッションを始められない（Cannot perform start session: EOF）" in eof_reason and "端末から打つか" in eof_reason
      and "OSS_ROLL=0" in eof_reason and "urp の節が無い" not in eof_reason
      and "Cannot perform start session: unexpected status 403" in other_reason and "端末から打つか" not in other_reason)
EOT = "^D\x08\x08"   # macOS の script が標準入力の EOF を疑似端末に渡したときの写し
check("roll_health.py: script の EOF の写し（^D と後退 2 つ）が出力の頭や行の頭に混ざっても読める。理由の先頭の数行にも出さない",
      rh.kafka(EOT + wrap(kafka_out()), KAFKA_NODES) == ("2", None)
      and rh.kafka(wrap(kafka_out()).replace("==nwc-roll urp", EOT + "==nwc-roll urp"), KAFKA_NODES) == ("2", None)
      and rh.opensearch(EOT + wrap(os_out()), OS_NODES) == ("cm", None)
      and rh.head(EOT + "\r\nAn error occurred (AccessDeniedException)\r\n") == "An error occurred (AccessDeniedException)")
check("roll_health.py（コマンド）: 健全ならリーダーを出して 0、健全でなければ理由を出して 1、使い方の誤りは 2、plan は空白区切り",
      (r_ok.returncode, r_ok.stdout) == (0, "1\n") and r_ng.returncode == 1 and "status=red" in r_ng.stdout
      and r_use.returncode == 2 and "使い方" in r_use.stderr and (r_plan.returncode, r_plan.stdout) == (0, "1 3 6\n"))

# ---------------------------------------------------------------- 2. タスクの中で打つコマンド
roll_sh = read("oss/ops/roll-nodes.sh")
def probe(name):
    m = re.search(rf"^{name}='([^']*)'$", roll_sh, re.M)
    assert m, name
    return m.group(1)
PROBE_KAFKA, PROBE_OS = probe("ROLL_PROBE_KAFKA"), probe("ROLL_PROBE_OPENSEARCH")
check("ROLL_PROBE_*: シングルクォートとバックスラッシュを使わず、--command の「/bin/bash -c '<コマンド>'」が /bin/bash・-c・コマンドの 3 つに割れる",
      all("'" not in p and "\\" not in p and shlex.split(f"/bin/bash -c '{p}'") == ["/bin/bash", "-c", p] for p in (PROBE_KAFKA, PROBE_OS))
      and 'aws ecs execute-command' in roll_sh and '--command "/bin/bash -c \'$ROLL_PROBE\'"' in roll_sh
      and PROBE_KAFKA.startswith("B=/opt/kafka/bin; ") and "localhost:9092" in PROBE_KAFKA and "http://localhost:9200/" in PROBE_OS)

tmp = tempfile.mkdtemp(prefix="nwc-roll-test-")

def write_exe(path, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(path, 0o755)

# 偽物の Kafka の道具（引数を見て、正常な台の出力を出す）と timeout（macOS には無い。そのまま打つ）と curl
probe_bin = os.path.join(tmp, "probe-bin")
kafka_bin = os.path.join(tmp, "kafka-bin")
os.makedirs(probe_bin); os.makedirs(kafka_bin)
write_exe(os.path.join(probe_bin, "timeout"), '#!/bin/bash\nshift\nexec "$@"\n')
KAFKA_TOOLS = {
    "kafka-broker-api-versions.sh": "".join(f"kafka-{n}.x-nwc-oss.internal:9092 (id: {n} rack: null isFenced: false) -> (\n"
                                            "\tProduce(0): 0 to 11 [usable: 11],\n)\n" for n in KAFKA_NODES),
    "kafka-topics.sh": "",
    "kafka-metadata-quorum.sh": "NodeId\tDirectoryId\tLogEndOffset\tLag\tLastFetchTimestamp\tLastCaughtUpTimestamp\tStatus\n"
                                + "".join(f"{n}\tAAAA\t500\t0\t1759900000000\t1759900000000\t{'Leader' if n == '3' else 'Follower'}\n" for n in KAFKA_NODES),
}
for tool, out in KAFKA_TOOLS.items():
    rc = "${FAKE_KAFKA_RC:-0}" if tool == "kafka-broker-api-versions.sh" else "0"
    write_exe(os.path.join(kafka_bin, tool), f"#!/bin/bash\necho \"$*\" >>{tmp}/kafka-args\ncat <<'EOF'\n{out}EOF\nexit {rc}\n")
write_exe(os.path.join(probe_bin, "curl"), "#!/bin/bash\n"
          'echo "$*" >>' + tmp + '/curl-args\n'
          'case "$*" in *_cluster/health*) printf \'{"status":"green","number_of_nodes":3}\' ;; *cluster_manager*) printf "opensearch-2\\n" ;; esac\n')

def run_probe(p, env_extra=None):
    env = {"PATH": probe_bin + ":/usr/bin:/bin", **(env_extra or {})}
    return subprocess.run(["/bin/bash", "-c", p.replace("B=/opt/kafka/bin", "B=" + kafka_bin)], env=env,
                          capture_output=True, text=True, timeout=30)
pk = run_probe(PROBE_KAFKA)
check("ROLL_PROBE_KAFKA を bash で打つと、3 つの節と終了コードが出て roll_health.py が健全（Leader は 3）と読む。bootstrap は localhost:9092",
      pk.returncode == 0 and rh.kafka(wrap(pk.stdout), KAFKA_NODES) == ("3", None)
      and "Produce(0)" not in pk.stdout and "--bootstrap-server localhost:9092" in open(f"{tmp}/kafka-args").read()
      and "--under-replicated-partitions" in open(f"{tmp}/kafka-args").read())
pk_ng = run_probe(PROBE_KAFKA, {"FAKE_KAFKA_RC": "1"})
check("ROLL_PROBE_KAFKA: kafka-broker-api-versions.sh が 1 で終わると（grep を挟んでも）その終了コードが brokers の節に出る",
      "==nwc-rc 1" in pk_ng.stdout and "brokers のコマンドが 1 で終わった" in (rh.kafka(pk_ng.stdout, KAFKA_NODES)[1] or ""))
po = run_probe(PROBE_OS, {"OPENSEARCH_INITIAL_ADMIN_PASSWORD": "pw-not-shown"})
po_none = run_probe(PROBE_OS)
check("ROLL_PROBE_OPENSEARCH を bash で打つと、タスクの環境変数のパスワードで _cluster/health と _cat/cluster_manager を読み、roll_health.py が健全と読む。"
      "パスワードは出力に出さず、環境変数が無ければ nopass だけ出す",
      po.returncode == 0 and rh.opensearch(po.stdout, OS_NODES) == ("2", None) and "pw-not-shown" not in po.stdout
      and "-u admin:pw-not-shown" in open(f"{tmp}/curl-args").read()
      and po_none.stdout == "==nwc-roll nopass\n" and "OPENSEARCH_INITIAL_ADMIN_PASSWORD" in rh.opensearch(po_none.stdout, OS_NODES)[1])

# ---------------------------------------------------------------- 3. roll_nodes を偽物の道具で通す
# 偽物の terraform。呼ばれた引数を FAKE_LOG に JSON で残す。state list は FAKE_STATE、show -json は FAKE_PLAN_JSON のファイル、
# output は FAKE_NODES（台 → サービス名）から作る。plan は -out のファイルを作り、FAKE_PLAN_RC で終わる
FAKE_TF = f'''#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"cmd": "terraform", "args": args}}) + "\\n")
verb = args[1]
if verb == "init":
    sys.exit(0)
if verb == "state" and args[2] == "list":
    print(os.environ.get("FAKE_STATE", ""))
    sys.exit(0)
if verb == "plan":
    out = [a for a in args if a.startswith("-out=")][0][len("-out="):]
    open(out, "w").write("fake plan")
    print("Plan: 0 to add, 3 to change, 0 to destroy.")
    sys.exit(int(os.environ.get("FAKE_PLAN_RC", "0")))
if verb == "show":
    assert args[2] == "-json" and os.path.exists(args[3]), args
    print(open(os.environ["FAKE_PLAN_JSON"]).read())
    sys.exit(0)
if verb == "output":
    nodes = json.loads(os.environ["FAKE_NODES"])
    name = args[-1]
    if name.endswith("_service_names"):
        print(json.dumps(nodes))
    elif name in ("kafka_ecs_cluster_name", "analytics_cluster_name"):
        print("x-nwc-oss-" + name.split("_")[0], end="")
    elif name.endswith("_log_group_name"):
        print("/x-nwc-oss/" + name.split("_")[0], end="")
    else:
        sys.exit(1)
    sys.exit(0)
if verb == "apply":
    print("Apply complete! Resources: 0 added, 1 changed, 0 destroyed.")
    sys.exit(0)
sys.exit(1)
'''
# 偽物の aws。execute-command は FAKE_EXEC_SEQ（JSON の配列）の出力を 1 回に 1 つずつ出す（尽きたら最後のものを出し続ける）
FAKE_AWS = f'''#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"cmd": "aws", "args": args}}) + "\\n")
def opt(name):
    return args[args.index(name) + 1]
if args[:2] == ["ecs", "list-tasks"]:
    if os.environ.get("FAKE_NO_TASK"):
        print("None")
    else:
        print("arn:aws:ecs:ap-northeast-1:111122223333:task/" + opt("--cluster") + "/" + opt("--service-name"))
    sys.exit(0)
if args[:2] == ["ecs", "execute-command"]:
    seq = json.load(open(os.environ["FAKE_EXEC_SEQ"]))
    cnt = os.environ["FAKE_EXEC_SEQ"] + ".n"
    n = int(open(cnt).read()) if os.path.exists(cnt) else 0
    open(cnt, "w").write(str(n + 1))
    sys.stdout.write(seq[min(n, len(seq) - 1)])
    sys.exit(0)
if args[:2] == ["ecs", "wait"]:
    sys.exit(int(os.environ.get("FAKE_WAIT_RC", "0")))
sys.exit(255)
'''
fake_bin = os.path.join(tmp, "fake-bin")
os.makedirs(fake_bin)
write_exe(os.path.join(fake_bin, "terraform"), FAKE_TF)
write_exe(os.path.join(fake_bin, "aws"), FAKE_AWS)
write_exe(os.path.join(fake_bin, "sleep"), "#!/bin/sh\nexit 0\n")
write_exe(os.path.join(fake_bin, "session-manager-plugin"), "#!/bin/sh\nexit 0\n")
# Session Manager plugin が無い PATH（この PC に本物が入っていても見えないよう、使う道具だけをリンクで並べる）
bare_bin = os.path.join(tmp, "bare-bin")
os.makedirs(bare_bin)
for f in ("terraform", "aws", "sleep"):
    os.symlink(os.path.join(fake_bin, f), os.path.join(bare_bin, f))
for tool in ("awk", "cat", "env", "grep", "mkdir", "mktemp", "rm", "tee", "dirname", "sed", "tr"):
    os.symlink(shutil.which(tool), os.path.join(bare_bin, tool))

# 偽物の script。呼ばれた引数と SHELL・AWS_PAGER を FAKE_LOG に残す。FAKE_SCRIPT_FORM=linux は util-linux（-q -c "<文字列>" /dev/null を
# $SHELL -c で打つ。BSD の形は 0 で終わってコマンドを打たない）、bsd は macOS（-q /dev/null <コマンド…> を打ち、頭に ^D の写しを出す。
# -c は知らない）、それ以外はどちらの形も打てない
FAKE_SCRIPT = f'''#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"cmd": "script", "args": args, "shell": os.environ.get("SHELL"), "pager": os.environ.get("AWS_PAGER")}}) + "\\n")
form = os.environ.get("FAKE_SCRIPT_FORM", "")
if form == "linux" and len(args) == 4 and args[:2] == ["-q", "-c"] and args[3] == "/dev/null":
    os.execv(os.environ["SHELL"], [os.environ["SHELL"], "-c", args[2]])
if form == "linux" and args[:2] == ["-q", "/dev/null"]:
    sys.exit(0)
if form == "bsd" and len(args) >= 3 and args[:2] == ["-q", "/dev/null"]:
    sys.stdout.write("^D\\x08\\x08")
    sys.stdout.flush()
    os.execvp(args[2], args[2:])
sys.stderr.write("script: illegal option\\n")
sys.exit(1)
'''
script_bin = os.path.join(tmp, "script-bin")
os.makedirs(script_bin)
write_exe(os.path.join(script_bin, "script"), FAKE_SCRIPT)
# script の無い PATH（Session Manager plugin はある）
noscript_bin = os.path.join(tmp, "noscript-bin")
os.makedirs(noscript_bin)
for f in os.listdir(bare_bin):
    os.symlink(os.path.realpath(os.path.join(bare_bin, f)), os.path.join(noscript_bin, f))
os.symlink(os.path.join(fake_bin, "session-manager-plugin"), os.path.join(noscript_bin, "session-manager-plugin"))

HARNESS = r'''set -euo pipefail
REGION=ap-northeast-1
. "$NWC_ROOT/ops/deploy-env.sh"
. "$NWC_ROOT/ops/common.sh"
. "$NWC_ROOT/ops/up-common.sh"
. "$NWC_ROOT/oss/ops/roll-nodes.sh"
TF_DIR=IaC/terraform/oss; OPS_DIR="$NWC_ROOT/oss/ops"; TF_LOG_NAME=tf-oss; TF_INIT_LOCKFILE=readonly
PY=("$NWC_PY"); PREFIX=x-nwc-oss; OWNER=x
OSS_ROLL="${OSS_ROLL-1}"; flag_value OSS_ROLL
ROLL_MINUTES_PRE="${T_MIN_PRE:-$ROLL_MINUTES_PRE}"; ROLL_MINUTES_KAFKA="${T_MIN:-$ROLL_MINUTES_KAFKA}"; ROLL_MINUTES_OPENSEARCH="${T_MIN:-$ROLL_MINUTES_OPENSEARCH}"
roll_nodes "$T_KIND" "$T_ROOT" -var "a=b" -var 'sinks=["x"]'
echo "==roll done plan=[$ROLL_PLAN]"
'''

case_no = 0
def roll(kind="kafka", plan=None, seq=None, state=None, path_bin=fake_bin, tty=True, pre_path=None, **env_extra):
    """tty=True なら標準入力を疑似端末にする（端末から打ったとき）。False なら /dev/null（CI やエージェントのシェル）。pre_path は PATH の頭に足す"""
    global case_no
    case_no += 1
    d = os.path.join(tmp, f"case{case_no}")
    os.makedirs(d)
    nodes = KAFKA_NODES if kind == "kafka" else OS_NODES
    with open(os.path.join(d, "plan.json"), "w") as f:
        f.write(plan if plan is not None else plan_json([(f'aws_ecs_service.{kind}["{n}"]', ["update"]) for n in nodes]))
    with open(os.path.join(d, "seq.json"), "w") as f:
        json.dump([wrap(s) for s in (seq or [kafka_out() if kind == "kafka" else os_out()])], f)
    if state is None:
        state = "\n".join([f'aws_ecs_service.{kind}["{n}"]' for n in nodes] + ["aws_ecs_cluster.kafka"])
    env = {"PATH": path_bin + ":" + os.environ["PATH"] if path_bin == fake_bin else path_bin, "HOME": tmp, "TMPDIR": d,
           "NWC_ROOT": ROOT, "NWC_PY": sys.executable, "FAKE_LOG": os.path.join(d, "log"), "FAKE_STATE": state,
           "FAKE_PLAN_JSON": os.path.join(d, "plan.json"), "FAKE_EXEC_SEQ": os.path.join(d, "seq.json"),
           "FAKE_NODES": json.dumps({n: f"x-nwc-oss-{kind}-{n}" for n in nodes}), "T_KIND": kind,
           "T_ROOT": "pipeline/stream" if kind == "kafka" else "pipeline/analytics", **env_extra}
    if pre_path:
        env["PATH"] = pre_path + ":" + env["PATH"]
    master, slave = pty.openpty() if tty else (None, None)
    try:
        r = subprocess.run([shutil.which("bash"), "-c", HARNESS], cwd=d, env=env, capture_output=True, text=True, timeout=60,
                           stdin=slave if tty else subprocess.DEVNULL)
    finally:
        if tty:
            os.close(master); os.close(slave)
    calls = []
    if os.path.exists(env["FAKE_LOG"]):
        with open(env["FAKE_LOG"], encoding="utf-8") as f:
            calls = [json.loads(l) for l in f]
    r.calls = calls
    r.dir = d
    r.leftover = [f for f in os.listdir(d) if "roll-plan" in f]
    return r

def tf_verbs(r):
    return [c["args"][1] for c in r.calls if c["cmd"] == "terraform"]
def is_apply(c):
    return c["cmd"] == "terraform" and c["args"][1] == "apply"
def target(c):
    """apply の -target=aws_ecs_service.<種類>["台"] の台"""
    return re.search(r'\["([^"]+)"\]', [a for a in c["args"] if a.startswith("-target=")][0]).group(1)
def applied(r):
    return [target(c) for c in r.calls if is_apply(c)]
def execs(r):
    """execute-command と apply を起きた順に: ("exec", 台) / ("apply", 台)"""
    out = []
    for c in r.calls:
        if c["cmd"] == "aws" and c["args"][:2] == ["ecs", "execute-command"]:
            out.append(("exec", c["args"][c["args"].index("--task") + 1].rsplit("-", 1)[1]))
        elif is_apply(c):
            out.append(("apply", target(c)))
    return out

r = roll()
first_apply = [c for c in r.calls if c["cmd"] == "terraform" and c["args"][1] == "apply"][0]["args"]
plan_call = [c for c in r.calls if c["cmd"] == "terraform" and c["args"][1] == "plan"][0]["args"]
check("roll_nodes kafka（3 台とも変わる。リーダーは 2）: 入れ替える前に健全さを見てから、1 → 3 → 2（リーダーは最後）の順に 1 台ずつ apply する",
      r.returncode == 0 and applied(r) == ["1", "3", "2"] and execs(r)[0][0] == "exec"
      and "Kafka の台を 1 台ずつ入れ替えた（入れ替えた順: 1 3 2）" in r.stdout)
check("roll_nodes: plan と apply に呼ぶ側の -var（と owner）を渡し、apply は -target=aws_ecs_service.kafka[\"台\"] の 1 つだけ。init は readonly",
      plan_call[0] == "-chdir=IaC/terraform/oss/pipeline/stream" and "-var" in plan_call and "owner=x" in plan_call and "a=b" in plan_call
      and 'sinks=["x"]' in plan_call and any(a.startswith("-out=") for a in plan_call)
      and "owner=x" in first_apply and "a=b" in first_apply and 'sinks=["x"]' in first_apply
      and [a for a in first_apply if a.startswith("-target=")] == ['-target=aws_ecs_service.kafka["1"]']
      and "-auto-approve" in first_apply
      and all(c["args"][2:] == ["-input=false", "-lockfile=readonly"] for c in r.calls if c["cmd"] == "terraform" and c["args"][1] == "init"))
ev = execs(r)
after = [ev[i + 1] for i, e in enumerate(ev) if e[0] == "apply" and i + 1 < len(ev)]
check("roll_nodes: 入れ替えた台のすぐあとの健全さは、入れ替えた台でない台の中から見る（ECS Exec のコンテナは kafka、クラスターは output の名前）",
      len(after) == 3 and all(a[0] == "exec" for a in after)
      and all(after[i][1] != applied(r)[i] for i in range(3))
      and all(c["args"][c["args"].index("--container") + 1] == "kafka" and c["args"][c["args"].index("--cluster") + 1] == "x-nwc-oss-kafka"
              and "--interactive" in c["args"] and c["args"][c["args"].index("--command") + 1] == f"/bin/bash -c '{PROBE_KAFKA}'"
              for c in r.calls if c["args"][:2] == ["ecs", "execute-command"]))
waits = [c["args"] for c in r.calls if c["args"][:2] == ["ecs", "wait"]]
check("roll_nodes: apply のたびにそのサービスの services-stable を待ち、plan のファイルは消す（-out のファイルが残らない）",
      [w[w.index("--services") + 1] for w in waits] == ["x-nwc-oss-kafka-1", "x-nwc-oss-kafka-3", "x-nwc-oss-kafka-2"]
      and all(w[w.index("--cluster") + 1] == "x-nwc-oss-kafka" for w in waits)
      and r.leftover == [] and "==roll done plan=[]" in r.stdout)

r = roll(seq=[kafka_out(leader="1"), kafka_out(leader="3")])
check("roll_nodes kafka: リーダーが入れ替えの途中で変わったら、その時のリーダーを避ける（1 がリーダー → 2、3 がリーダーになったら 1、最後に 3）",
      r.returncode == 0 and applied(r) == ["2", "1", "3"])

r = roll(seq=[kafka_out(), kafka_out(urp=3), kafka_out(fenced=("1",)), kafka_out(lag={"1": 4000}), kafka_out()])
check("roll_nodes kafka: 入れ替えたあと健全に戻るまで待ち（複製の不足・fenced・遅れ）、理由が変わるたびに 1 行出す",
      r.returncode == 0 and applied(r) == ["1", "3", "2"]
      and "複製が足りないパーティションが 3 個" in r.stdout and "fenced でない broker" in r.stdout and "遅れ" in r.stdout)

r = roll(plan=plan_json([('aws_ecs_service.kafka["3"]', ["update"]), ('aws_ecs_service.kafka["1"]', ["no-op"]),
                         ('aws_ecs_task_definition.kafka["3"]', ["delete", "create"])]))
check("roll_nodes kafka: plan で変わる台（3）だけを入れ替える（変わらない台は apply しない）",
      r.returncode == 0 and applied(r) == ["3"] and "Kafka の変わる台: 3（全部の台: 1 2 3）" in r.stdout)

r = roll(plan=plan_json([('aws_ecs_service.kafka["1"]', ["no-op"]), ('aws_ecs_service.kafka_ui', ["update"])]))
check("roll_nodes kafka: 変わる台が無ければ、ECS Exec も apply もせずに戻る（このあとの tf_apply に任せる）",
      r.returncode == 0 and applied(r) == [] and not any(c["cmd"] == "aws" for c in r.calls) and "Kafka の台は変わらない" in r.stdout
      and r.leftover == [])

r = roll(state="aws_ecs_cluster.kafka\naws_iam_role.kafka_task")
check("roll_nodes kafka: state に Kafka のサービスが無い（初めて作る）ときは plan も打たない",
      r.returncode == 0 and tf_verbs(r) == ["init", "state"] and "==roll done" in r.stdout)

r = roll(OSS_ROLL="0")
check("roll_nodes: OSS_ROLL=0 なら terraform も aws も打たずに戻り、一度に入れ替わることを出す",
      r.returncode == 0 and r.calls == [] and "OSS_ROLL=0" in r.stdout and "同時に入れ替わる" in r.stdout)

r = roll(seq=[kafka_out(fenced=("2",))], T_MIN_PRE="0")
check("roll_nodes kafka: 入れ替える前から健全でなければ（時間切れ）、何も apply せずに止まり、理由と OSS_ROLL=0 の逃げ道を出す",
      r.returncode == 1 and applied(r) == [] and "入れ替える前から健全でない" in r.stderr and "fenced でない broker" in r.stderr
      and "まだ何も入れ替えていない" in r.stderr and "OSS_ROLL=0" in r.stderr and "/x-nwc-oss/kafka" in r.stderr)

r = roll(FAKE_NO_TASK="1", T_MIN_PRE="0")
check("roll_nodes kafka: 動いているタスクが無ければ ECS Exec を打たず、健全でないと見て止まる",
      r.returncode == 1 and applied(r) == [] and not any(c["args"][:2] == ["ecs", "execute-command"] for c in r.calls)
      and "動いているタスクが無い" in r.stderr)

r = roll(seq=[kafka_out(), kafka_out(fenced=("1",))], T_MIN="0")
check("roll_nodes kafka: 入れ替えたあと時間内に健全に戻らなければ止まり、入れ替えた台と残りの台（1 → 残り 2 3）を出す",
      r.returncode == 1 and applied(r) == ["1"] and "健全に戻らない" in r.stderr and "入れ替えた台: 1。残り: 2 3" in r.stderr
      and "もう一度" in r.stderr)

r = roll(FAKE_WAIT_RC="255")
check("roll_nodes kafka: サービスが 2 回待っても安定しなければ止まり、残りの台を出す（次の台は apply しない）",
      r.returncode == 1 and applied(r) == ["1"] and len([c for c in r.calls if c["args"][:2] == ["ecs", "wait"]]) == 2
      and "20 分たっても安定しない" in r.stderr and "残り: 2 3" in r.stderr)

r = roll(FAKE_PLAN_RC="1")
check("roll_nodes: plan に失敗したら何も入れ替えずに止まり、plan のファイルを残さない",
      r.returncode == 1 and applied(r) == [] and "plan に失敗した" in r.stderr and "まだ何も入れ替えていない" in r.stderr
      and r.leftover == [] and not any(c["cmd"] == "aws" for c in r.calls))

r = roll(plan="not json")
check("roll_nodes: plan の JSON を読めなければ何も入れ替えずに止まる",
      r.returncode == 1 and applied(r) == [] and "変わる台を読めなかった" in r.stderr and r.leftover == [])

r = roll(path_bin=bare_bin)
check("roll_nodes: 変わる台があるのに Session Manager plugin が無ければ、ECS Exec も apply もせずに止まり、OSS_ROLL=0 の逃げ道を出す",
      r.returncode == 1 and applied(r) == [] and not any(c["cmd"] == "aws" for c in r.calls)
      and "Session Manager plugin が無い" in r.stderr and "OSS_ROLL=0" in r.stderr)

def scripts(r):
    """偽物の script の呼び出しのうち、ECS Exec を包んだもの（印で形を見分ける呼び出しは除く）"""
    return [c for c in r.calls if c["cmd"] == "script" and "nwc-roll-tty" not in " ".join(c["args"])]
def exec_args_ok(r, probe_cmd=None):
    """偽物の aws に届いた execute-command の引数が、包まずに打ったときと同じ（--command が 1 つの引数のまま）"""
    ex = [c["args"] for c in r.calls if c["args"][:2] == ["ecs", "execute-command"]]
    return len(ex) > 0 and all(a[a.index("--command") + 1] == f"/bin/bash -c '{probe_cmd or PROBE_KAFKA}'" and "--interactive" in a
                               and a[a.index("--task") + 1].startswith("arn:aws:ecs:") for a in ex)

r = roll(tty=True, pre_path=script_bin, FAKE_SCRIPT_FORM="linux")
check("roll_nodes: 標準入力が端末なら script を打たない（ECS Exec をそのまま打つ）",
      r.returncode == 0 and applied(r) == ["1", "3", "2"] and not any(c["cmd"] == "script" for c in r.calls) and "疑似端末" not in r.stdout)

r = roll(tty=False, pre_path=script_bin, FAKE_SCRIPT_FORM="linux")
sc = scripts(r)
check("roll_nodes: 標準入力が端末でなく script が util-linux の形なら、ECS Exec を script -q -c \"<文字列>\" /dev/null で包み"
      "（文字列はいまの bash が割る。AWS_PAGER は空）、--command の引数は崩れずに届いて、端末から打ったときと同じ順に入れ替える",
      r.returncode == 0 and applied(r) == ["1", "3", "2"] and "script で疑似端末を付けて打つ（linux の形）" in r.stdout
      and len(sc) == len([c for c in r.calls if c["args"][:2] == ["ecs", "execute-command"]]) > 0
      and all(c["args"][:2] == ["-q", "-c"] and c["args"][3] == "/dev/null" and os.path.basename(c["shell"] or "") == "bash"
              and c["pager"] == "" for c in sc)
      and exec_args_ok(r))

r = roll(tty=False, pre_path=script_bin, FAKE_SCRIPT_FORM="bsd")
sc = scripts(r)
check("roll_nodes: 標準入力が端末でなく script が BSD（macOS）の形なら、script -q /dev/null <コマンド…> で包み（AWS_PAGER は空）、"
      "出力の頭の ^D の写しがあっても健全と読んで入れ替える",
      r.returncode == 0 and applied(r) == ["1", "3", "2"] and "script で疑似端末を付けて打つ（bsd の形）" in r.stdout
      and len(sc) > 0 and all(c["args"][:4] == ["-q", "/dev/null", "aws", "ecs"] and c["pager"] == "" for c in sc)
      and exec_args_ok(r))

r = roll(kind="opensearch", tty=False, pre_path=script_bin, FAKE_SCRIPT_FORM="linux", seq=[os_out(manager="cm")])
check("roll_nodes opensearch: 端末が無く util-linux の形でも、--command（パスワードの環境変数を読むコマンド）は崩れずに届く",
      r.returncode == 0 and applied(r) == ["1", "2", "cm"] and exec_args_ok(r, PROBE_OS))

if shutil.which("script"):
    r = roll(tty=False)
    check(f"roll_nodes: 端末が無いとき、この PC の本物の script（{shutil.which('script')}）で形を見分けて包み、入れ替える",
          r.returncode == 0 and applied(r) == ["1", "3", "2"] and "script で疑似端末を付けて打つ" in r.stdout and exec_args_ok(r))
else:
    print("skip: この PC に script が無いので、本物の script で包むケースは見ない")

r = roll(tty=False, path_bin=noscript_bin)
check("roll_nodes: 端末が無く script も無ければ、ECS Exec も apply もせずに止まり、「端末から打つか OSS_ROLL=0」を出す",
      r.returncode == 1 and applied(r) == [] and not any(c["args"][:2] == ["ecs", "execute-command"] for c in r.calls)
      and "標準入力が端末でなく" in r.stderr and "端末から打つか" in r.stderr and "OSS_ROLL=0" in r.stderr
      and "まだ何も入れ替えていない" in r.stderr and r.leftover == [])

r = roll(tty=False, pre_path=script_bin, FAKE_SCRIPT_FORM="none")
check("roll_nodes: 端末が無く script がどちらの形でも打てなければ（印が出ない）、同じく何も入れ替えずに止まる",
      r.returncode == 1 and applied(r) == [] and not any(c["args"][:2] == ["ecs", "execute-command"] for c in r.calls)
      and len([c for c in r.calls if c["cmd"] == "script"]) == 2 and "端末から打つか" in r.stderr)

r = roll(seq=[EOF_OUT], T_MIN_PRE="0")
check("roll_nodes: ECS Exec が「Cannot perform start session: EOF」で切れたら、入れ替える前に止まり、理由に原因と「端末から打つか」を出す",
      r.returncode == 1 and applied(r) == [] and "入れ替える前から健全でない" in r.stderr
      and "ECS Exec のセッションを始められない（Cannot perform start session: EOF）" in r.stderr and "端末から打つか" in r.stderr)

r = roll(kind="opensearch", seq=[os_out(manager="cm"), os_out(status="yellow"), os_out(manager="cm")])
ex = [c["args"] for c in r.calls if c["args"][:2] == ["ecs", "execute-command"]]
check("roll_nodes opensearch（1・2・cm が変わる。cluster manager は cm）: 1 → 2 → cm の順に入れ替え、yellow の間は待つ。"
      "ECS Exec のコンテナは opensearch、クラスターは analytics_cluster_name",
      r.returncode == 0 and applied(r) == ["1", "2", "cm"] and "status=yellow" in r.stdout
      and all(a[a.index("--container") + 1] == "opensearch" and a[a.index("--cluster") + 1] == "x-nwc-oss-analytics"
              and a[a.index("--command") + 1] == f"/bin/bash -c '{PROBE_OS}'" for a in ex)
      and "OpenSearch の台を 1 台ずつ入れ替えた（入れ替えた順: 1 2 cm）" in r.stdout)

r = roll(kind="opensearch", seq=["==nwc-roll nopass\n"], T_MIN_PRE="0")
check("roll_nodes opensearch: タスクの中にパスワードの環境変数が見えなければ、入れ替える前に止まる",
      r.returncode == 1 and applied(r) == [] and "OPENSEARCH_INITIAL_ADMIN_PASSWORD が無い" in r.stderr)

r = subprocess.run([shutil.which("bash"), "-c", HARNESS], cwd=tmp, capture_output=True, text=True, timeout=30,
                   env={"PATH": fake_bin + ":" + os.environ["PATH"], "NWC_ROOT": ROOT, "NWC_PY": sys.executable,
                        "T_KIND": "kafka_ui", "T_ROOT": "pipeline/stream", "FAKE_LOG": os.path.join(tmp, "log-bad")})
check("roll_nodes: 種類が kafka / opensearch でなければ止まる（terraform は打たない）",
      r.returncode == 1 and "roll_nodes の種類は kafka か opensearch" in r.stderr and not os.path.exists(os.path.join(tmp, "log-bad")))

# ---------------------------------------------------------------- 4. terraform の名前と合っている
stream_tf = read("IaC/terraform/oss/pipeline/stream/kafka.tf")
os_tf = read("IaC/terraform/oss/pipeline/analytics/opensearch.tf")
an_out = read("IaC/terraform/oss/pipeline/analytics/outputs.tf")
def block(text, head):
    i = text.index(head)
    return text[i:text.index("\n}\n", i)]
check("roll-nodes.sh が読む output（クラスター・サービス名・ロググループ）が IaC/terraform/oss/ にあり、サービス名は台をキーにした map",
      all(f'output "{o}"' in stream_tf for o in ("kafka_ecs_cluster_name", "kafka_service_names", "kafka_log_group_name"))
      and all(f'output "{o}"' in os_tf for o in ("opensearch_service_names", "opensearch_log_group_name"))
      and 'output "analytics_cluster_name"' in an_out
      and "for n, s in aws_ecs_service.kafka : n => s.name" in stream_tf and "for n, s in aws_ecs_service.opensearch : n => s.name" in os_tf
      and all(o in roll_sh for o in ("kafka_ecs_cluster_name", "kafka_service_names", "kafka_log_group_name", "analytics_cluster_name",
                                     "opensearch_service_names", "opensearch_log_group_name")))
ks, oss_ = block(stream_tf, 'resource "aws_ecs_service" "kafka"'), block(os_tf, 'resource "aws_ecs_service" "opensearch"')
check("Kafka と OpenSearch のサービスは台ごと（for_each）で ECS Exec が有効、コンテナの名前は kafka / opensearch（roll_exec の --container）、"
      "OpenSearch のパスワードはタスクの secrets の OPENSEARCH_INITIAL_ADMIN_PASSWORD",
      "for_each" in ks and "enable_execute_command = true" in ks and "for_each" in oss_ and "enable_execute_command = true" in oss_
      and 'name         = "kafka"' in stream_tf and 'name         = "opensearch"' in os_tf
      and re.search(r'name\s*=\s*"OPENSEARCH_INITIAL_ADMIN_PASSWORD"', os_tf) is not None
      and all(a in stream_tf for a in ("ssmmessages:CreateControlChannel", "ssmmessages:OpenDataChannel"))
      and all(a in os_tf for a in ("ssmmessages:CreateControlChannel", "ssmmessages:OpenDataChannel")))
check("Kafka と OpenSearch の台の名前が roll_health.py の前提（Kafka は node.id が台、OpenSearch は opensearch-<台>）",
      "tostring(i + 1)" in stream_tf and '{ name = "KAFKA_NODE_ID", value = each.key }' in stream_tf
      and '{ name = "node.name", value = "opensearch-${each.key}" }' in os_tf)

shutil.rmtree(tmp)
print(f"通過 {passed} / 失敗 0")
