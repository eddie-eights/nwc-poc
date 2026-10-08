"""cycle 005（マネージドを OSS に置き換えた環境を作る）の oss/ops/（up.sh / down.sh / oss-images.sh）の模擬テスト。
aws / terraform / docker は偽物（下の FAKE_*）に差し替え、AWS には触れない。
  1. 接頭辞 <owner>-nwc-oss が、どの OWNER の組み合わせでもマネージド版の <owner>-nwc-poc と同じにならない（resolve_name_prefix を bash で呼ぶ）
  2. oss/ops/down.sh は IaC/terraform/oss/ の state と、名前・タグが <owner>-nwc-oss のもの（SSM のパラメータは ManagedBy=oss/ops/up.sh だけ）しか消さない。
     いちばん紛らわしい OWNER=x-nwc-oss のマネージド版（接頭辞 x-nwc-oss-nwc-poc。OSS 版の x-nwc-oss と頭が同じ）を同じアカウントに並べて確かめる。
     逆向き（ops/down.sh が OSS 版に触らない）も見る。消したあとに残っているもの（Project タグ）を数えて出す
  3. stream が消えなかったときは Kafka の CLUSTER_ID を残し、残りのルートは消しにいき、終了コード 1 で消えなかったルートを出す
  4. イメージの名前と版が oss/compose/（正）・docker/images/spark/ と docker/images/neo4j/ の Dockerfile・terraform の既定値・ECR のリポジトリに合い、
     mirror_oss_images が ECR に無いものだけを写す（spark / neo4j は app/spark/・app/neo4j/ を context に、docker/images/<名前>/Dockerfile でビルドする）
  5. ensure_secret の kafka-cluster-id（KRaft の CLUSTER_ID の形）と strong-password（OpenSearch の admin。値は画面に出さない）と、
     oss/ops/ が ops/ の関数を写さず読むこと、up.sh がマネージド版と同じ 9 つのルートを当て、Splunk のイメージと SSM をマネージド版と同じ関数で用意すること
  6. oss/ops/up.sh を偽物の道具で最後まで通す（3 回）。9 つのルートの apply の順番と渡す値、イメージと SSM のパラメータ、Web の部品、
     Neo4j が安定してからの同期、OpenSearch・VictoriaMetrics・Splunk が上がってからの Spark、ポートフォワードの案内と、
     打ち直し（イメージもパラメータも作り直さない）、サービスが安定しなかったとき（同期を飛ばし、Spark は起こし、警告を出す）。
     そのあと oss/ops/down.sh が、up.sh の作ったパラメータを全部消す
実行は python3 tests/test_oss_ops.py"""
import base64, hashlib, json, os, re, shutil, subprocess, tempfile, types

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

# ---- 偽物の aws。呼ばれた引数を FAKE_LOG に 1 行ずつ JSON で残し、FAKE_INV の在庫（JSON）を読み書きする。
# 名前の絞り込みは本物より緩い側（SSM の Path は文字列の前方一致）で真似る。知らないコマンドは 255 で落ち、ログに unknown を残す
FAKE_AWS = r'''#!/usr/bin/env python3
import fcntl, fnmatch, json, os, sys

args = sys.argv[1:]
inv_path = os.environ["FAKE_INV"]
lock = open(inv_path + ".lock", "a")
fcntl.flock(lock, fcntl.LOCK_EX)
with open(inv_path, encoding="utf-8") as f:
    inv = json.load(f)

def log(extra=None):
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"cmd": "aws", "args": args, **(extra or {})}) + "\n")

def opt(name):
    return args[args.index(name) + 1] if name in args else None

def multi(name):  # --filters a b c のように、次の --… までの値を全部
    if name not in args:
        return []
    out = []
    for a in args[args.index(name) + 1:]:
        if a.startswith("--"):
            break
        out.append(a)
    return out

def kv(item):  # "Key=Path,Option=Recursive,Values=/x/" → {"Key": "Path", ...}
    return dict(part.partition("=")[::2] for part in item.split(","))

def save():
    with open(inv_path, "w", encoding="utf-8") as f:
        json.dump(inv, f)

def fail(msg, rc=254):
    print(msg, file=sys.stderr)
    sys.exit(rc)

svc, op = (args + ["", ""])[:2]
query = opt("--query")
log()
if (svc, op) == ("sts", "get-caller-identity"):
    print("arn:aws:sts::123456789012:assumed-role/Admin/tester" if query == "Arn" else "123456789012")
# ---- ここから oss/ops/up.sh が打つもの（6.）。送ったコマンドは在庫の cmds に残し、結果を聞かれたら Success と答える
elif (svc, op) == ("ecr", "get-login-password"):
    print("fake-ecr-login")
elif svc == "s3" and op in ("cp", "sync"):
    pass
elif (svc, op) == ("ssm", "describe-instance-information"):
    print("Online")
elif (svc, op) == ("ssm", "send-command"):
    inv.setdefault("cmds", []).append(opt("--parameters"))
    save()
    print(f'cmd-{len(inv["cmds"])}')
elif (svc, op) == ("ssm", "get-command-invocation"):
    sent = inv["cmds"][int(opt("--command-id")[len("cmd-"):]) - 1]
    if query == "Status":
        print("Success")
    elif query == "StandardOutputContent":
        print(f'lab=active containers={os.environ.get("FAKE_LAB_NODES", "0")}' if "containers=" in sent else "")
    else:
        log({"unknown": "query " + str(query)}); fail("unknown query", 255)
elif (svc, op) == ("ssm", "start-session"):
    pass
elif (svc, op) == ("ec2", "reboot-instances"):
    pass
elif (svc, op) == ("ecs", "wait"):  # FAKE_ECS_UNSTABLE のサービスは安定しない（待ちが切れる）
    if set(multi("--services")) & set(os.environ.get("FAKE_ECS_UNSTABLE", "").split(",")):
        fail("Waiter ServicesStable failed: Max attempts exceeded", 255)
elif (svc, op) == ("ecs", "list-tasks"):
    print(f'arn:aws:ecs:ap-northeast-1:123456789012:task/{opt("--cluster")}/{opt("--service-name")}-t1')
elif (svc, op) == ("ecs", "describe-tasks"):
    if query == "tasks[].healthStatus":
        print("HEALTHY")
    elif query and query.startswith("tasks[0].attachments[0].details"):
        print("10.0.1.23")
    else:
        log({"unknown": "query " + str(query)}); fail("unknown query", 255)
elif (svc, op) == ("ecs", "update-service"):
    print(opt("--service"))
elif (svc, op) == ("glue", "get-catalog"):
    answers = {"Catalog.Name": "s3tablescatalog", "Catalog.FederatedCatalog.ConnectionName": "aws:s3tables",
               "Catalog.AllowFullTableExternalDataAccess": "True",
               "Catalog.CreateTableDefaultPermissions[].Principal.DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"}
    if query not in answers:
        log({"unknown": "query " + str(query)}); fail("unknown query", 255)
    print(answers[query])
elif svc == "logs" and op in ("put-retention-policy", "create-log-group", "tag-resource"):
    pass
elif (svc, op) == ("ssm", "describe-parameters"):
    names = []
    for name, p in sorted(inv["ssm"].items()):
        ok = True
        for f in map(kv, multi("--parameter-filters")):
            key, val = f["Key"], f["Values"]
            if key == "Path":
                ok = ok and f.get("Option") == "Recursive" and name.startswith(val)
            elif key == "Name":
                ok = ok and name == val
            elif key.startswith("tag:"):
                ok = ok and p["tags"].get(key[4:]) == val
            else:
                log({"unknown": "filter " + key}); fail("unknown filter", 255)
        if ok:
            names.append(name)
    if query == "Parameters[0].Type":
        print(inv["ssm"][names[0]]["type"] if names else "None")
    elif query == "Parameters[].Name":
        print("\t".join(names))
    else:
        log({"unknown": "query " + str(query)}); fail("unknown query", 255)
elif (svc, op) == ("ssm", "delete-parameter"):
    if opt("--name") not in inv["ssm"]:
        fail("An error occurred (ParameterNotFound)")
    del inv["ssm"][opt("--name")]
    save()
elif (svc, op) == ("ssm", "put-parameter"):
    src = opt("--cli-input-json")
    if not src or not src.startswith("file://"):
        fail("put-parameter には file:// で渡す", 255)
    with open(src[len("file://"):], encoding="utf-8") as f:
        d = json.load(f)
    inv["ssm"][d["Name"]] = {"type": d["Type"], "value": d["Value"], "tags": {t["Key"]: t["Value"] for t in d.get("Tags", [])}}
    save()
elif (svc, op) == ("ec2", "describe-vpcs"):
    f = [kv(x) for x in multi("--filters")]
    if [x["Name"] for x in f] != ["tag:Name"]:
        log({"unknown": "vpc filter"}); fail("unknown filter", 255)
    print(inv["vpcs"].get(f[0]["Values"], "None"))
elif (svc, op) == ("ec2", "describe-network-interfaces"):
    fs = {x["Name"]: x["Values"] for x in map(kv, multi("--filters"))}
    if "group-id" in fs:
        print("")
    elif set(fs) == {"vpc-id"} and query == "NetworkInterfaces[?InterfaceType=='agentic_ai'].NetworkInterfaceId":
        print("\t".join(e["id"] for e in inv["enis"] if e["vpc"] == fs["vpc-id"] and e["type"] == "agentic_ai"))
    elif set(fs) == {"description", "status"} and query == "NetworkInterfaces[].NetworkInterfaceId":
        print("\t".join(e["id"] for e in inv["enis"]
                        if fnmatch.fnmatchcase(e["desc"], fs["description"]) and e["status"] == fs["status"]))
    else:
        log({"unknown": "eni filter"}); fail("unknown filter", 255)
elif (svc, op) == ("ec2", "delete-network-interface"):
    ids = [e["id"] for e in inv["enis"]]
    if opt("--network-interface-id") not in ids:
        fail("An error occurred (InvalidNetworkInterfaceID.NotFound)")
    inv["enis"] = [e for e in inv["enis"] if e["id"] != opt("--network-interface-id")]
    save()
elif (svc, op) == ("ec2", "describe-security-groups"):
    print("")
elif (svc, op) == ("logs", "describe-log-groups"):
    print("\t".join(g for g in inv["log_groups"] if g.startswith(opt("--log-group-name-prefix"))))
elif (svc, op) == ("logs", "delete-log-group"):
    if opt("--log-group-name") not in inv["log_groups"]:
        fail("An error occurred (ResourceNotFoundException)")
    inv["log_groups"].remove(opt("--log-group-name"))
    save()
elif (svc, op) == ("resourcegroupstaggingapi", "get-resources"):
    if os.environ.get("FAKE_TAG_FAIL"):
        fail("An error occurred (ThrottlingException)")
    f = [kv(x) for x in multi("--tag-filters")]
    if [x["Key"] for x in f] != ["Project"]:
        log({"unknown": "tag filter"}); fail("unknown filter", 255)
    project = f[0]["Values"]
    arns = list(inv["tagged"].get(project, []))
    arns += ["arn:aws:ssm:ap-northeast-1:123456789012:parameter" + n for n, p in sorted(inv["ssm"].items())
             if p["tags"].get("Project") == project]
    print("\t".join(arns))
elif (svc, op) == ("ecr", "describe-images"):
    tag = (opt("--image-ids") or "").partition("=")[2]
    if not os.environ.get("FAKE_ECR_ALL") and f'{opt("--repository-name")}:{tag}' not in inv.get("ecr", []):
        fail("An error occurred (ImageNotFoundException)")
else:
    log({"unknown": f"{svc} {op}"})
    fail(f"fake aws: unknown {svc} {op}", 255)
'''

# ---- 偽物の terraform。state にはいつも 1 つ載っている（state list）。FAKE_TF_FAIL の -chdir の destroy だけ落ちる
# （DependencyViolation は出さないので打ち直さない）。VPC の中に Lambda がいるルートは 1 秒かけて、ENI を刈る裏の処理を回す
FAKE_TF = r'''#!/usr/bin/env python3
import json, os, sys, time

args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"cmd": "terraform", "args": args, "profile": os.environ.get("AWS_PROFILE")}) + "\n")
chdir = args[0][len("-chdir="):] if args and args[0].startswith("-chdir=") else ""
rest = args[1:] if chdir else args
verb = rest[0] if rest else ""
if verb == "init":
    if os.environ.get("FAKE_TF_INIT_FAIL") == chdir:  # lock にこの PC のハッシュが無いときの readonly の init
        print("Error: Provider dependency changes detected (lock file is read-only)", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
if rest[:2] == ["state", "list"]:
    print("aws_instance.this")
    sys.exit(0)
if verb == "apply":
    print("Apply complete! Resources: 1 added, 0 changed, 0 destroyed.")
    sys.exit(0)
if verb == "output" and os.environ.get("FAKE_TF_UP"):  # up.sh（6.）にだけ答える。値は「out-<ルートの末尾>-<output の名前>」
    name = rest[-1]
    if "-json" in rest:
        if name.endswith("_service_names"):
            print(json.dumps({k: f'x-nwc-oss-{name[:-len("_service_names")]}-{k}' for k in ("c", "a", "b")}))
        else:
            print("{}")
    elif name == "agent_repository_url":
        print("123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/x-nwc-oss-agent", end="")
    else:
        print(f'out-{chdir.rsplit("/", 1)[-1]}-{name}', end="")
    sys.exit(0)
if verb == "output":
    sys.exit(1)
if verb == "destroy":
    if chdir in os.environ.get("FAKE_TF_FAIL", "").split(","):
        print("Error: fake failure")
        sys.exit(1)
    if chdir.endswith(("/workflow", "/pipeline/graph")):
        time.sleep(1.0)
    print("Destroy complete! Resources: 1 destroyed.")
    sys.exit(0)
sys.exit("fake terraform: unknown " + " ".join(args))
'''

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"cmd": "docker", "args": sys.argv[1:]}) + "\n")
if sys.argv[1:3] == ["buildx", "ls"]:
    print("default *  docker\n  default  default  running  v0.20.0  linux/amd64, linux/arm64")
if sys.argv[1:2] == ["login"]:
    sys.stdin.read()
'''

# ---- 偽物の uv（up.sh が pip を打つのに使う。PyPI には行かない）。download は -d の先に空のホイールを、
# install は --target の先に neo4j/__init__.py（up.sh が「レイヤーはある」と見る目印）を置く
FAKE_UV = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"cmd": "uv", "args": args}) + "\n")
for flag, name in (("-d", "fake-1.0-py3-none-any.whl"), ("--target", "neo4j/__init__.py")):
    if flag in args:
        os.makedirs(os.path.dirname(os.path.join(args[args.index(flag) + 1], name)), exist_ok=True)
        open(os.path.join(args[args.index(flag) + 1], name), "w").close()
'''

# ---- 偽物の curl（containerlab の rpm）。-o の先に空でないファイルを置く
FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"cmd": "curl", "args": args}) + "\n")
with open(args[args.index("-o") + 1], "w") as f:
    f.write("fake")
'''

# oss/ops/up.sh が apply する順番
ROOTS = ["base/ecr", "base/core", "agent", "pipeline/lab", "pipeline/stream", "pipeline/graph",
         "pipeline/nautobot", "pipeline/analytics", "workflow"]

def lambda_eni(eni_id, fn, vpc):
    return {"id": eni_id, "desc": f"AWS Lambda VPC ENI-{fn}-0a1b2c", "status": "available", "type": "lambda", "vpc": vpc}

def ssm_param(managed_by, project):
    tags = {"Project": project}
    if managed_by:
        tags["ManagedBy"] = managed_by
    return {"type": "SecureString", "tags": tags}

# 同じアカウントに 3 つが並んでいる: OSS 版（OWNER=x → x-nwc-oss）、マネージド版（OWNER=x-nwc-oss → x-nwc-oss-nwc-poc）、
# マネージド版（OWNER=x → x-nwc-poc）
OSS_MANAGED_PARAMS = ["/x-nwc-oss/kafka/cluster-id", "/x-nwc-oss/kafka-ui/admin-password", "/x-nwc-oss/telegraf-dialin/gnmi-password",
                      "/x-nwc-oss/opensearch-password", "/x-nwc-oss/splunk/admin-password", "/x-nwc-oss/splunk/hec-token",
                      "/x-nwc-oss/neo4j-password", "/x-nwc-oss/nautobot/secret-key"]
def inventory():
    return {
        "ssm": {
            **{n: ssm_param("oss/ops/up.sh", "x-nwc-oss") for n in OSS_MANAGED_PARAMS},
            "/x-nwc-oss/manual/note": ssm_param(None, "x-nwc-oss"),  # 手で入れたもの（ManagedBy が無い）は残す
            # /x-nwc-oss/ の下でもマネージド版の ops/up.sh のタグのものは残す（Path だけで消さない）。Project は「残り」の数に入らない別の名前
            "/x-nwc-oss/shared/from-managed": ssm_param("ops/up.sh", "y-nwc-poc"),
            "/x-nwc-oss-nwc-poc/kafka-ui/admin-password": ssm_param("ops/up.sh", "x-nwc-oss-nwc-poc"),
            "/x-nwc-oss-nwc-poc/nautobot/secret-key": ssm_param("ops/up.sh", "x-nwc-oss-nwc-poc"),
            "/x-nwc-poc/kafka-ui/admin-password": ssm_param("ops/up.sh", "x-nwc-poc"),
        },
        "vpcs": {"x-nwc-oss-vpc": "vpc-0055", "x-nwc-oss-nwc-poc-vpc": "vpc-0aaa", "x-nwc-poc-vpc": "vpc-0bbb"},
        "enis": [
            lambda_eni("eni-oss-tools", "x-nwc-oss-tools", "vpc-0055"),
            lambda_eni("eni-mgd-tools", "x-nwc-oss-nwc-poc-tools", "vpc-0aaa"),
            lambda_eni("eni-mgd-graph", "x-nwc-oss-nwc-poc-graph-status", "vpc-0aaa"),
            {"id": "eni-mgd-runtime", "desc": "agentic", "status": "in-use", "type": "agentic_ai", "vpc": "vpc-0aaa"},
        ],
        "log_groups": ["/aws/bedrock-agentcore/runtimes/x_nwc_oss_agent-AAA-DEFAULT",
                       "/aws/bedrock-agentcore/runtimes/x_nwc_oss_nwc_poc_agent-BBB-DEFAULT",
                       "/aws/bedrock-agentcore/runtimes/x_nwc_poc_agent-CCC-DEFAULT"],
        "tagged": {"x-nwc-oss": ["arn:aws:ecs:ap-northeast-1:123456789012:cluster/x-nwc-oss-left"],
                   "x-nwc-oss-nwc-poc": ["arn:aws:ec2:ap-northeast-1:123456789012:vpc/vpc-0aaa",
                                         "arn:aws:ec2:ap-northeast-1:123456789012:subnet/subnet-0aaa"],
                   "x-nwc-poc": ["arn:aws:s3:::x-nwc-poc-left"]},
        "ecr": [],
    }

TMP = tempfile.mkdtemp(prefix="nwc-oss-ops-")
BIN = os.path.join(TMP, "bin")
os.makedirs(BIN)
for name, body in (("aws", FAKE_AWS), ("terraform", FAKE_TF), ("docker", FAKE_DOCKER), ("sleep", "#!/bin/sh\nexec /bin/sleep 0.1\n"),
                   ("uv", FAKE_UV), ("curl", FAKE_CURL), ("session-manager-plugin", "#!/bin/sh\nexit 0\n")):
    with open(os.path.join(BIN, name), "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(os.path.join(BIN, name), 0o755)
LOG, INV = os.path.join(TMP, "calls.jsonl"), os.path.join(TMP, "inv.json")

# down.sh と up.sh を打つ場所（リポジトリの写し）。ops/ と oss/ops/ のスクリプトと、9 つのルートの state と、
# up.sh が読む材料（イメージの元、Web とエージェントの部品、lab の定義）を置く。設定と秘密のファイルは写さない
REPO = os.path.join(TMP, "repo")
for d in ("ops", "oss/ops"):
    os.makedirs(os.path.join(REPO, d))
    for f in os.listdir(os.path.join(ROOT, d)):
        if f.endswith(".sh") or f in ("seed_graph.py", "roll_health.py"):
            shutil.copy(os.path.join(ROOT, d, f), os.path.join(REPO, d, f))
UP_DIRS = ("app/containerlab", "app/dashboard", "app/agentcore", "app/nautobot", "app/telegraf", "app/splunk", "app/spark", "app/neo4j", "app/graph",
           "app/temporal", "docker/images")
for d in UP_DIRS:
    shutil.copytree(os.path.join(ROOT, d), os.path.join(REPO, d), ignore=shutil.ignore_patterns(
        "__pycache__", ".env", ".env.*", "deploy.env", "*.tfvars", "*.rpm", "*.part", "node_modules", ".venv", "splab.clab.yml"))
for base in ("IaC/terraform/aws-managed", "IaC/terraform/oss"):
    for r in ROOTS:
        os.makedirs(os.path.join(REPO, base, r))
        with open(os.path.join(REPO, base, r, "terraform.tfstate"), "w") as f:
            f.write("{}")

def fake_env(extra=None):
    env = {"PATH": BIN + os.pathsep + os.environ["PATH"], "HOME": TMP, "TMPDIR": TMP, "FAKE_LOG": LOG, "FAKE_INV": INV}
    env.update(extra or {})
    return env

def reset(inv):
    with open(INV, "w", encoding="utf-8") as f:
        json.dump(inv, f)
    open(LOG, "w").close()

def calls():
    with open(LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

def run_down(script, owner, extra=None, inv=None):  # inv を渡すと、その在庫から始める（6. で up.sh の作ったものを消す）
    reset(inventory() if inv is None else inv)
    shutil.rmtree(os.path.join(REPO, "ops", "logs"), ignore_errors=True)
    envfile = os.path.join(TMP, f"owner-{owner}.env")
    with open(envfile, "w", encoding="utf-8") as f:
        f.write(f"OWNER={owner}\n")
    p = subprocess.run(["bash", script], cwd=REPO, env=fake_env({"DEPLOY_ENV_FILE": envfile, **(extra or {})}),
                       capture_output=True, text=True, timeout=300)
    with open(INV, encoding="utf-8") as f:
        inv = json.load(f)
    return p, calls(), inv

def tf_calls(cs):
    return [c for c in cs if c["cmd"] == "terraform"]

def chdir_of(c):
    return c["args"][0][len("-chdir="):]

def inits(cs):  # terraform init の引数（-chdir の後ろ）
    return [c["args"][1:] for c in tf_calls(cs) if c["args"][1] == "init"]

def destroyed(cs):  # destroy を打ったルート（-chdir の値）
    return {chdir_of(c) for c in tf_calls(cs) if c["args"][1] == "destroy"}

def aws_calls(cs, svc, op):
    return [c["args"] for c in cs if c["cmd"] == "aws" and c["args"][:2] == [svc, op]]

def arg_after(a, name):
    return a[a.index(name) + 1]

def multi_of(a, name):  # --services a b c のように、次の --… までの値を全部
    i = a.index(name) + 1
    j = next((k for k in range(i, len(a)) if a[k].startswith("--")), len(a))
    return a[i:j]

def logs_made():
    d = os.path.join(REPO, "ops", "logs")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []

def eni_ids(inv):
    return {e["id"] for e in inv["enis"]}

ALL_PARAMS = set(inventory()["ssm"])
ALL_ENIS = eni_ids(inventory())
ALL_LOG_GROUPS = set(inventory()["log_groups"])

# ================================================================ 1. 接頭辞
PREFIX_SH = r'''
. ops/common.sh; . ops/deploy-env.sh
for o in x a x-nwc-oss x-nwc-poc nwc-oss nwc-poc a-nwc-poc-nwc abcdefghijklmn a1-b2-c3; do
  OWNER=$o; resolve_name_prefix; m=$PREFIX; resolve_name_prefix nwc-oss; echo "$o $m $PREFIX"
done
'''
p = subprocess.run(["bash", "-c", PREFIX_SH], cwd=ROOT, capture_output=True, text=True, timeout=60)
rows = [line.split() for line in p.stdout.splitlines()]
check("resolve_name_prefix: OWNER を 9 通り読めた", p.returncode == 0 and len(rows) == 9 and all(len(r) == 3 for r in rows))
check("resolve_name_prefix nwc-oss の接頭辞は <owner>-nwc-oss、引数なしは今までどおり <owner>-nwc-poc",
      all(m == f"{o}-nwc-poc" and s == f"{o}-nwc-oss" for o, m, s in rows))
managed, oss = {m for _, m, _ in rows}, {s for _, _, s in rows}
check("どの OWNER の組み合わせでも、OSS 版の接頭辞はマネージド版の接頭辞と同じにならない（末尾が -nwc-oss と -nwc-poc で違う）",
      not managed & oss)
check("OWNER が上限の 14 文字なら、OSS 版の接頭辞は 22 文字（マネージド版と同じ長さ）",
      len(dict((o, s) for o, _, s in rows)["abcdefghijklmn"]) == 22)
p = subprocess.run(["bash", "-c", ". ops/common.sh; . ops/deploy-env.sh; OWNER=abcdefghijklmno; resolve_name_prefix nwc-oss; echo NOT-REACHED"],
                   cwd=ROOT, capture_output=True, text=True, timeout=60)
check("OWNER が 15 文字なら OSS 版でも止まる", p.returncode == 1 and "NOT-REACHED" not in p.stdout)

# ================================================================ 2. oss/ops/down.sh は OSS 版だけを消す
p, cs, inv = run_down("oss/ops/down.sh", "x")
out = p.stdout + p.stderr
check("oss/ops/down.sh（OWNER=x）: 終了コード 0", p.returncode == 0)
check("偽物の aws に知らないコマンドを打っていない", not [c for c in cs if c.get("unknown")])
check("terraform は IaC/terraform/oss/ の下だけを -chdir で触り、IaC/terraform/aws-managed/（マネージド版の state）には入らない",
      tf_calls(cs) and all(chdir_of(c).startswith("IaC/terraform/oss/") for c in tf_calls(cs)))
check("IaC/terraform/oss/ の 9 つのルートを全部 destroy した", destroyed(cs) == {f"IaC/terraform/oss/{r}" for r in ROOTS})
check("destroy には -var owner=x を渡す",
      all("owner=x" in c["args"] for c in tf_calls(cs) if c["args"][1] == "destroy"))
check("terraform に渡す認証のプロファイル名は接頭辞から作る（x-nwc-oss-terraform）",
      {c["profile"] for c in tf_calls(cs)} == {"x-nwc-oss-terraform"})
check("terraform のログは ops/logs/tf-oss-*（マネージド版の tf-* を上書きしない）",
      logs_made() and all(f.startswith("tf-oss-") for f in logs_made()) and "tf-oss-pipeline-stream-destroy.log" in logs_made())
check("SSM: oss/ops/up.sh が作った 8 つ（Kafka の CLUSTER_ID、OpenSearch・Splunk・Neo4j・Nautobot のものを含む）を消し、手で入れたものとマネージド版のものは残す",
      set(inv["ssm"]) == ALL_PARAMS - set(OSS_MANAGED_PARAMS))
check("SSM: delete-parameter は /x-nwc-oss/ の下にしか打っていない",
      all(arg_after(a, "--name").startswith("/x-nwc-oss/") for a in aws_calls(cs, "ssm", "delete-parameter")))
check("SSM の絞り込みは、describe-parameters のどの呼び出しも Path=/x-nwc-oss/（末尾の / まで）と ManagedBy=oss/ops/up.sh の両方を付ける",
      aws_calls(cs, "ssm", "describe-parameters")
      and all(a[a.index("--parameter-filters") + 1:a.index("--parameter-filters") + 3]
              == ["Key=Path,Option=Recursive,Values=/x-nwc-oss/", "Key=tag:ManagedBy,Values=oss/ops/up.sh"]
              for a in aws_calls(cs, "ssm", "describe-parameters")))
check("SSM: /x-nwc-oss/ の下にあってもタグ ManagedBy=ops/up.sh（マネージド版）のパラメータは消さない",
      "/x-nwc-oss/shared/from-managed" in inv["ssm"]
      and "/x-nwc-oss/shared/from-managed" not in {arg_after(a, "--name") for a in aws_calls(cs, "ssm", "delete-parameter")})
check("Lambda の ENI: OSS 版の tools のものだけ消し、マネージド版（x-nwc-oss-nwc-poc-tools / graph-status）と Runtime のものは残す",
      eni_ids(inv) == ALL_ENIS - {"eni-oss-tools"})
check("ENI の絞り込みは「AWS Lambda VPC ENI-<接頭辞>-<関数>-*」で、OSS 版の関数名だけ",
      {arg_after(a, "--filters") for a in aws_calls(cs, "ec2", "describe-network-interfaces") if "Name=status,Values=available" in a}
      <= {"Name=description,Values=AWS Lambda VPC ENI-x-nwc-oss-tools-*", "Name=description,Values=AWS Lambda VPC ENI-x-nwc-oss-graph-status-*",
          "Name=description,Values=AWS Lambda VPC ENI-x-nwc-oss-kb-index-*"})
check("VPC はタグ Name=x-nwc-oss-vpc（完全一致）で引き、Runtime の ENI は無いと出す",
      [arg_after(a, "--filters") for a in aws_calls(cs, "ec2", "describe-vpcs")] == ["Name=tag:Name,Values=x-nwc-oss-vpc"]
      and "Runtime の ENI の確認: VPC=vpc-0055 残り=なし" in out)
check("Runtime のロググループ: x_nwc_oss_agent- のものだけ消し、x_nwc_oss_nwc_poc_agent- と x_nwc_poc_agent- は残す",
      set(inv["log_groups"]) == ALL_LOG_GROUPS - {"/aws/bedrock-agentcore/runtimes/x_nwc_oss_agent-AAA-DEFAULT"})
check("残り: Project=x-nwc-oss（完全一致）だけを数え、ARN を並べて「残り: 2 件」と出す（マネージド版のものは出さない）",
      "残り: 2 件（Project=x-nwc-oss のタグ）" in out
      and "arn:aws:ecs:ap-northeast-1:123456789012:cluster/x-nwc-oss-left" in out
      and "parameter/x-nwc-oss/manual/note" in out and "x-nwc-oss-nwc-poc" not in out and "vpc/vpc-0aaa" not in out)
check("KEEP_ECR を書かなければ ECR も消す（IaC/terraform/oss/base/ecr を destroy した）", "IaC/terraform/oss/base/ecr" in destroyed(cs))

# ================================================================ 2'. 逆向き: マネージド版の ops/down.sh は OSS 版に触らない
p, cs, inv = run_down("ops/down.sh", "x-nwc-oss")
out = p.stdout + p.stderr
check("ops/down.sh（OWNER=x-nwc-oss → 接頭辞 x-nwc-oss-nwc-poc）: 終了コード 0", p.returncode == 0)
check("マネージド版: 偽物の aws に知らないコマンドを打っていない", not [c for c in cs if c.get("unknown")])
check("マネージド版: terraform は IaC/terraform/aws-managed/ の下だけ（IaC/terraform/oss/ の state には入らない）",
      tf_calls(cs) and all(chdir_of(c).startswith("IaC/terraform/aws-managed/") for c in tf_calls(cs)))
check("マネージド版: Runtime の ENI が残っているので base/core は -target で ENI に関わらないものだけ消す",
      "Runtime の ENI が残っている" in out
      and any(chdir_of(c) == "IaC/terraform/aws-managed/base/core" and "-target=aws_instance.this" in c["args"] for c in tf_calls(cs)))
check("マネージド版: SSM は /x-nwc-oss-nwc-poc/ の ManagedBy=ops/up.sh の 2 つだけ消し、OSS 版の 9 つは残す",
      set(inv["ssm"]) == ALL_PARAMS - {"/x-nwc-oss-nwc-poc/kafka-ui/admin-password", "/x-nwc-oss-nwc-poc/nautobot/secret-key"})
check("マネージド版: Lambda の ENI は x-nwc-oss-nwc-poc の 2 つだけ消し、OSS 版のものは残す",
      eni_ids(inv) == ALL_ENIS - {"eni-mgd-tools", "eni-mgd-graph"})
check("マネージド版: ロググループは x_nwc_oss_nwc_poc_agent- だけ消す",
      set(inv["log_groups"]) == ALL_LOG_GROUPS - {"/aws/bedrock-agentcore/runtimes/x_nwc_oss_nwc_poc_agent-BBB-DEFAULT"})
check("マネージド版: 残りも Project=x-nwc-oss-nwc-poc で数える（「残り: 2 件」）",
      "残り: 2 件（Project=x-nwc-oss-nwc-poc のタグ）" in out and "x-nwc-oss-left" not in out)
check("マネージド版: terraform init は「init -input=false」のまま（-lockfile を付けない。lock はマネージド版が書き足す）",
      inits(cs) and all(a == ["init", "-input=false"] for a in inits(cs)))
check("マネージド版: terraform のログは ops/logs/tf-*（tf-oss-* を作らない）",
      logs_made() and not [f for f in logs_made() if f.startswith("tf-oss-")])

p, cs, inv = run_down("ops/down.sh", "x")
out = p.stdout + p.stderr
check("ops/down.sh（OWNER=x → x-nwc-poc）: 終了コード 0 で、消したのは /x-nwc-poc/ のパラメータとロググループだけ",
      p.returncode == 0
      and set(inv["ssm"]) == ALL_PARAMS - {"/x-nwc-poc/kafka-ui/admin-password"}
      and eni_ids(inv) == ALL_ENIS
      and set(inv["log_groups"]) == ALL_LOG_GROUPS - {"/aws/bedrock-agentcore/runtimes/x_nwc_poc_agent-CCC-DEFAULT"}
      and "残り: 1 件（Project=x-nwc-poc のタグ）" in out)

# ================================================================ 3. stream が消えなかったとき
p, cs, inv = run_down("oss/ops/down.sh", "x", {"FAKE_TF_FAIL": "IaC/terraform/oss/pipeline/stream", "FAKE_TAG_FAIL": "1", "KEEP_ECR": "1"})
out = p.stdout + p.stderr
check("stream が消えなかった: 終了コード 1 で「NG: 消えなかったルート: pipeline/stream」と出す",
      p.returncode == 1 and "NG: 消えなかったルート: pipeline/stream（" in out)
check("stream が消えなかった: 後ろのルート（lab / agent / base/core）は消しにいく",
      {"IaC/terraform/oss/pipeline/lab", "IaC/terraform/oss/agent", "IaC/terraform/oss/base/core"} <= destroyed(cs))
check("stream が消えなかった: Kafka の CLUSTER_ID は残し（次の down.sh で消す）、ほかの oss/ops/up.sh のパラメータは消す",
      "/x-nwc-oss/kafka/cluster-id: 残す" in out
      and set(inv["ssm"]) == ALL_PARAMS - set(OSS_MANAGED_PARAMS) | {"/x-nwc-oss/kafka/cluster-id"})
check("KEEP_ECR=1 なら IaC/terraform/oss/base/ecr は destroy しない", "IaC/terraform/oss/base/ecr" not in destroyed(cs))
check("残りを数えられなかった（タグの API のエラー）ときは、0 件と言わずに「数えられなかった」と出す",
      "残り: 数えられなかった（上のエラー）" in out and "残り: 0 件" not in out)

p, cs, inv = run_down("oss/ops/down.sh", "x", {"FAKE_TF_FAIL": "IaC/terraform/oss/base/core"})
check("base/core（Kafka のデータの EFS）が消えなかったときも Kafka の CLUSTER_ID は残す",
      p.returncode == 1 and "/x-nwc-oss/kafka/cluster-id" in inv["ssm"] and "/x-nwc-oss/kafka-ui/admin-password" not in inv["ssm"])

p, cs, inv = run_down("oss/ops/down.sh", "x", {"FAKE_TF_FAIL": "IaC/terraform/oss/pipeline/nautobot"})
check("nautobot が消えなかった: 終了コード 1 で、/x-nwc-oss/nautobot/ の下（DB に入っている値と合わせるもの）だけ残し、ほかは Kafka の CLUSTER_ID も消す",
      p.returncode == 1 and "NG: 消えなかったルート: pipeline/nautobot（" in p.stdout + p.stderr
      and set(inv["ssm"]) == ALL_PARAMS - set(OSS_MANAGED_PARAMS) | {"/x-nwc-oss/nautobot/secret-key"})
check("nautobot が消えなかった: 前後のルート（analytics / graph / stream / base/core）は消しにいく",
      {f"IaC/terraform/oss/{r}" for r in ROOTS} == destroyed(cs))

# ================================================================ 4. イメージの名前と版
img_sh = read("oss/ops/oss-images.sh")
def tf_default_early(path, var):
    m = re.search(rf'variable\s+"{var}"\s*\{{[^}}]*?default\s*=\s*"([^"]+)"', read(path), re.S)
    return m and m.group(1)
V = {k: q or b for k, q, b in re.findall(r'^(OSS_[A-Z0-9_]+)=(?:"([^"]*)"|(\S*))', img_sh, re.M)}
compose_images = set(re.findall(r"^\s*image:\s*(\S+)", read("oss/compose/compose.yaml"), re.M))
check("oss-images.sh の公開イメージ（kafka / kafka-ui / opensearch / VictoriaMetrics の 3 つ）は oss/compose/compose.yaml の image: と同じ版",
      {f'{V["OSS_KAFKA_IMAGE"]}:{V["OSS_KAFKA_TAG"]}', f'{V["OSS_KAFKA_UI_IMAGE"]}:{V["OSS_KAFKA_UI_TAG"]}',
       f'{V["OSS_OPENSEARCH_IMAGE"]}:{V["OSS_OPENSEARCH_TAG"]}'}
      | {f'victoriametrics/{n}:{V["OSS_VM_TAG"]}' for n in ("vmstorage", "vminsert", "vmselect")} <= compose_images)
check("ビルドする spark / neo4j の版は docker/images/spark/・docker/images/neo4j/ の Dockerfile の ARG の既定値と同じで、oss/compose/<名前>/Dockerfile の FROM とも同じ",
      re.search(rf'^ARG SPARK_VERSION={re.escape(V["OSS_SPARK_VERSION"])}\s*$', read("docker/images/spark/Dockerfile"), re.M)
      and re.search(r'^FROM apache/spark:\$\{SPARK_VERSION\}-java17-python3\s*$', read("docker/images/spark/Dockerfile"), re.M)
      and re.search(rf'^ARG NEO4J_VERSION={re.escape(V["OSS_NEO4J_VERSION"])}\s*$', read("docker/images/neo4j/Dockerfile"), re.M)
      and re.search(r'^FROM neo4j:\$\{NEO4J_VERSION\}-community\s*$', read("docker/images/neo4j/Dockerfile"), re.M)
      and re.search(rf'^FROM apache/spark:{re.escape(V["OSS_SPARK_VERSION"])}-java17-python3\s*$', read("oss/compose/spark/Dockerfile"), re.M)
      and re.search(rf'^FROM neo4j:{re.escape(V["OSS_NEO4J_VERSION"])}-community\s*$', read("oss/compose/neo4j/Dockerfile"), re.M))
check("Neo4j の版は IaC/terraform/oss/pipeline/graph/neo4j.tf の neo4j_image_tag の既定値と同じ",
      tf_default_early("IaC/terraform/oss/pipeline/graph/neo4j.tf", "neo4j_image_tag") == V["OSS_NEO4J_VERSION"])
def tf_default(path, var):
    m = re.search(rf'variable\s+"{var}"\s*\{{[^}}]*?default\s*=\s*"([^"]+)"', read(path), re.S)
    return m and m.group(1)
check("Kafka の版は IaC/terraform/oss/pipeline/stream/kafka.tf の kafka_image_tag の既定値と同じ",
      tf_default("IaC/terraform/oss/pipeline/stream/kafka.tf", "kafka_image_tag") == V["OSS_KAFKA_TAG"])
check("Kafbat UI の版はマネージド版の ops/up.sh の KAFKA_UI_TAG と stream の kafka_ui_image_tag の既定値と同じ",
      re.search(r"^KAFKA_UI_TAG=(\S+)", read("ops/up.sh"), re.M).group(1) == V["OSS_KAFKA_UI_TAG"]
      == tf_default("IaC/terraform/aws-managed/pipeline/stream/variables.tf", "kafka_ui_image_tag"))
m = re.search(r'oss_repositories\s*=.*?toset\(\[([^\]]*)\]\)', read("IaC/terraform/aws-managed/base/ecr/main.tf"))
check("OSS_IMAGES（ECR に写すもの）は IaC/terraform/aws-managed/base/ecr の oss_repositories と同じ 7 つ",
      m and set(re.findall(r'"([^"]+)"', m.group(1))) == set(V["OSS_IMAGES"].split()) and len(V["OSS_IMAGES"].split()) == 7)

TAGS_SH = r'''
REGION=ap-northeast-1; PY=(python3)
. ops/lab-common.sh; . oss/ops/oss-images.sh
for n in $OSS_IMAGES; do t=$(oss_image_tag "$n") || exit 1; u=$(oss_image_upstream "$n") || exit 1; echo "$n $t ${u:--}"; done
oss_image_tag bogus && exit 1
oss_image_upstream bogus && exit 1
echo END
'''
p = subprocess.run(["bash", "-c", TAGS_SH], cwd=ROOT, capture_output=True, text=True, timeout=60)
T = {r[0]: (r[1], r[2]) for r in (line.split() for line in p.stdout.splitlines()) if len(r) == 3}
check("oss_image_tag / oss_image_upstream は OSS_IMAGES の 7 つ全部に答え、知らない名前は 1 を返す",
      p.returncode == 0 and p.stdout.rstrip().endswith("END") and set(T) == set(V["OSS_IMAGES"].split()))
check("spark / neo4j のタグは「<Dockerfile の ARG の版>-<app/spark/・app/neo4j/ の中身のハッシュ 12 桁>」で、写す元は無い（ビルドする）",
      re.fullmatch(rf'{re.escape(V["OSS_SPARK_VERSION"])}-[0-9a-f]{{12}}', T["spark"][0])
      and re.fullmatch(rf'{re.escape(V["OSS_NEO4J_VERSION"])}-[0-9a-f]{{12}}', T["neo4j"][0])
      and T["spark"][1] == T["neo4j"][1] == "-")

inv0 = inventory()
inv0["ecr"] = [f'x-nwc-oss-opensearch:{V["OSS_OPENSEARCH_TAG"]}']
reset(inv0)
REG = "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com"
MIRROR_SH = rf'''
REGION=ap-northeast-1; PY=(python3)
. ops/lab-common.sh; . oss/ops/oss-images.sh
mirror_oss_images {REG} x-nwc-oss $OSS_IMAGES || exit 1
echo END
'''
p = subprocess.run(["bash", "-c", MIRROR_SH], cwd=ROOT, env=fake_env(), capture_output=True, text=True, timeout=120)
docker = [c["args"] for c in calls() if c["cmd"] == "docker"]
pushed = {a[-1] for a in docker if a[0] == "push"} | {arg_after(a, "-t") for a in docker if a[:2] == ["buildx", "build"]}
check("mirror_oss_images: ECR にあるタグ（opensearch）は飛ばし、ほかの 6 つを <レジストリ>/<接頭辞>-<名前>:<タグ> に置く",
      p.returncode == 0 and "opensearch:3.9.0 はある" in p.stdout
      and pushed == {f"{REG}/x-nwc-oss-{n}:{T[n][0]}" for n in V["OSS_IMAGES"].split() if n != "opensearch"})
check("mirror_oss_images: 公開イメージは arm64 を引いて写す（apache/kafka と VictoriaMetrics の 3 つ）",
      {a[-1] for a in docker if a[0] == "pull" and a[1:3] == ["--platform", "linux/arm64"]}
      == {f'{V["OSS_KAFKA_IMAGE"]}:{V["OSS_KAFKA_TAG"]}'} | {f'victoriametrics/{n}:{V["OSS_VM_TAG"]}' for n in ("vmstorage", "vminsert", "vmselect")})
_builds = {a[-1]: a for a in docker if a[:2] == ["buildx", "build"]}
check("mirror_oss_images: spark / neo4j は app/spark/・app/neo4j/ を context に、docker/images/<名前>/Dockerfile で linux/arm64 でビルドして push し、版を --build-arg で渡す",
      sorted(_builds) == ["app/neo4j/", "app/spark/"]
      and all(a[2:4] == ["--platform", "linux/arm64"] and "--push" in a for a in _builds.values())
      and all(arg_after(_builds[f"app/{n}/"], "-f") == f"docker/images/{n}/Dockerfile" for n in ("spark", "neo4j"))
      and arg_after(_builds["app/spark/"], "--build-arg") == f'SPARK_VERSION={V["OSS_SPARK_VERSION"]}'
      and arg_after(_builds["app/neo4j/"], "--build-arg") == f'NEO4J_VERSION={V["OSS_NEO4J_VERSION"]}')
p = subprocess.run(["bash", "-c", rf'REGION=ap-northeast-1; PY=(python3); . ops/lab-common.sh; . oss/ops/oss-images.sh; mirror_oss_images {REG} x-nwc-oss bogus'],
                   cwd=ROOT, env=fake_env(), capture_output=True, text=True, timeout=60)
check("mirror_oss_images: 知らない名前は 1 で止まる", p.returncode == 1 and "知らないイメージ: bogus" in p.stderr)

# ================================================================ 5. CLUSTER_ID と、oss/ops/ の作り
reset(inventory())
SECRET_SH = r'''
REGION=ap-northeast-1; PY=(python3); PREFIX=x-nwc-oss; OWNER=x
. ops/common.sh; . ops/up-common.sh; OPS_DIR=oss/ops
ensure_secret /x-nwc-oss/kafka/new-id kafka-cluster-id "test" || exit 1
ensure_secret /x-nwc-oss/kafka/new-id kafka-cluster-id "test" || exit 1
'''
p = subprocess.run(["bash", "-c", SECRET_SH], cwd=ROOT, env=fake_env(), capture_output=True, text=True, timeout=60)
with open(INV, encoding="utf-8") as f:
    made = json.load(f)["ssm"].get("/x-nwc-oss/kafka/new-id", {})
value = made.get("value", "")
check("ensure_secret kafka-cluster-id: SecureString を 1 回だけ作り、2 回目は作り直さない",
      p.returncode == 0 and made.get("type") == "SecureString" and len(aws_calls(calls(), "ssm", "put-parameter")) == 1
      and "/x-nwc-oss/kafka/new-id はある（作り直さない）" in p.stdout)
check("ensure_secret kafka-cluster-id: 値は 16 バイトの base64url（22 文字、= なし、先頭は - でない）",
      re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{21}", value) is not None
      and len(base64.urlsafe_b64decode(value + "==")) == 16)
check("ensure_secret kafka-cluster-id: 値は画面にもコマンドラインにも出さない",
      value and value not in p.stdout + p.stderr and not any(value in " ".join(c["args"]) for c in calls()))
check("ensure_secret kafka-cluster-id: タグは ManagedBy=oss/ops/up.sh・Project=x-nwc-oss・owner=x（oss/ops/down.sh が消せる）",
      made.get("tags") == {"ManagedBy": "oss/ops/up.sh", "Project": "x-nwc-oss", "owner": "x"})
check("ensure_secret: 値を書いた一時ファイルを残さない", not [f for f in os.listdir(TMP) if f.startswith("nwc-secret.")])

# 先頭が「-」になる乱数を引いたら引き直す（Kafka の Uuid.randomUuid と同じ）。up-common.sh の Python をそのまま動かす
src = re.search(r"(def kafka_cluster_id\(\):\n(?:    .*\n)+)", read("ops/up-common.sh")).group(1)
draws = iter([bytes([0xF8]) + bytes(15), bytes([0x10]) + bytes(15)])  # 0xF8 の上位 6 ビットは 62 =「-」
ns = {"base64": base64, "uuid": types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(bytes=next(draws)))}
exec(src, ns)
v = ns["kafka_cluster_id"]()
check("kafka_cluster_id: 先頭が - になる値は捨てて引き直す", not v.startswith("-") and next(draws, None) is None)

reset(inventory())
STRONG_SH = r'''
REGION=ap-northeast-1; PY=(python3); PREFIX=x-nwc-oss; OWNER=x
. ops/common.sh; . ops/up-common.sh; OPS_DIR=oss/ops
for i in 1 2 3 4 5 6 7 8; do ensure_secret "/x-nwc-oss/os/p$i" strong-password "test" || exit 1; done
'''
p = subprocess.run(["bash", "-c", STRONG_SH], cwd=ROOT, env=fake_env(), capture_output=True, text=True, timeout=120)
with open(INV, encoding="utf-8") as f:
    made = {k: v for k, v in json.load(f)["ssm"].items() if k.startswith("/x-nwc-oss/os/")}
vals = [m.get("value", "") for m in made.values()]
check("ensure_secret strong-password: SecureString で、値は大文字・小文字・数字・記号（- か _）を全部含む 32 文字（OpenSearch 2.12 からの初期パスワードの条件）",
      p.returncode == 0 and len(vals) == 8 and all(m.get("type") == "SecureString" for m in made.values())
      and all(len(x) == 32 and re.search(r"[A-Z]", x) and re.search(r"[a-z]", x) and re.search(r"[0-9]", x) and re.search(r"[-_]", x) for x in vals)
      and len(set(vals)) == 8)
check("ensure_secret strong-password: 値は画面にもコマンドラインにも出さず、タグは ManagedBy=oss/ops/up.sh",
      all(x not in p.stdout + p.stderr and not any(x in " ".join(c["args"]) for c in calls()) for x in vals)
      and all(m.get("tags", {}).get("ManagedBy") == "oss/ops/up.sh" for m in made.values()))

up, down = read("oss/ops/up.sh"), read("oss/ops/down.sh")
def funcs(text):
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", text, re.M))
ops_funcs = set().union(*(funcs(read(f"ops/{f}")) for f in os.listdir(os.path.join(ROOT, "ops")) if f.endswith(".sh")))
check("oss/ops/up.sh と down.sh は関数を定義しない（ops/ の共通の関数を読む）", not funcs(up) and not funcs(down))
check("oss/ops/oss-images.sh は ops/ にある関数を書き直していない（写しを作らない）", not funcs(img_sh) & ops_funcs)
check("oss/ops/up.sh は ops/common.sh・ops/up-common.sh・ops/lab-common.sh・ops/deploy-env.sh を読む",
      all(s in up for s in (". ops/common.sh", ". ops/up-common.sh", '/../../ops/lab-common.sh"', '/../../ops/deploy-env.sh"')))
check("oss/ops/down.sh は ops/common.sh・ops/down-common.sh・ops/deploy-env.sh を読む",
      all(s in down for s in (". ops/common.sh", ". ops/down-common.sh", '/../../ops/deploy-env.sh"')))
check("マネージド版の ops/up.sh と ops/down.sh も同じ共通のファイルを読む（写しが 2 つにならない）",
      all(s in read("ops/up.sh") for s in (". ops/common.sh", ". ops/up-common.sh"))
      and all(s in read("ops/down.sh") for s in (". ops/common.sh", ". ops/down-common.sh")))
check("oss/ops/ の 2 つは resolve_name_prefix nwc-oss で接頭辞を作り、TF_DIR=IaC/terraform/oss・OPS_DIR=oss/ops・TF_LOG_NAME=tf-oss にする",
      all("resolve_name_prefix nwc-oss" in t and re.search(r"^TF_DIR=IaC/terraform/oss\b", t, re.M)
          and re.search(r"^OPS_DIR=oss/ops\b", t, re.M) and re.search(r"^TF_LOG_NAME=tf-oss\b", t, re.M) for t in (up, down)))
pos = lambda s: up.find(s)
_applies = [pos(f"tf_apply {r}") for r in ROOTS]
check("oss/ops/up.sh はルートを base/ecr → base/core → agent → pipeline/lab → pipeline/stream → pipeline/graph → pipeline/nautobot → pipeline/analytics → workflow の順に当て、ROOTS もその 9 つ（マネージド版と同じ範囲）",
      _applies[0] >= 0 and _applies == sorted(_applies) and len(set(_applies)) == 9
      and re.search(r'^ROOTS="' + " ".join(ROOTS) + r'"$', up, re.M))
check("oss/ops/down.sh は up.sh の 9 つのルートを全部消す（base/core は destroy_base_core、agent は destroy_agent）",
      all(re.search(rf"^\s*destroy_(lambda_)?root {re.escape(r)}\b", down, re.M) for r in ROOTS if r not in ("base/core", "agent"))
      and re.search(r"^destroy_agent$", down, re.M) and re.search(r"^destroy_base_core$", down, re.M))
check("oss/ops/up.sh は OSS_NOW を持たず、oss-images.sh の OSS_IMAGES（7 つ）をそのまま使って、ECR ができてから OSS のイメージを写すかビルドし、stream より先に済ませる",
      "OSS_NOW" not in up and len(V["OSS_IMAGES"].split()) == 7 and re.search(r"^for name in \$OSS_IMAGES; do$", up, re.M) is not None
      and 0 <= pos("tf_apply base/ecr") < pos('mirror_oss_images "$REG" "$PREFIX" $OSS_IMAGES') < pos("tf_apply pipeline/stream"))
check("oss/ops/up.sh の Splunk のイメージはマネージド版と同じ関数（ops/up-common.sh の splunk_image_check / build_splunk）で、docker login のあと、analytics より前",
      "splunk_image_check" in up and "$NEED_SPLUNK" in up
      and pos("aws ecr get-login-password") < pos("    build_splunk") < pos("tf_apply pipeline/analytics")
      and "splunk_image_check" in read("ops/up.sh") and "build_splunk" in read("ops/up.sh")
      and "docker buildx build" not in up)
check("oss/ops/up.sh は analytics の前に Splunk の SSM（ensure_splunk_secrets。マネージド版と同じ関数）と OpenSearch の admin（strong-password）と s3tablescatalog を用意する",
      0 <= pos('ensure_secret "/$PREFIX/opensearch-password" strong-password') < pos("tf_apply pipeline/analytics")
      and 0 <= pos('ensure_splunk_secrets "$SPLUNK_AZ_NUM"') < pos("tf_apply pipeline/analytics")
      and 0 <= pos("ensure_s3tables_catalog") < pos("tf_apply pipeline/analytics")
      and 'ensure_splunk_secrets "$SPLUNK_AZ_NUM"' in read("ops/up.sh"))
check("oss/ops/up.sh の Grafana のイメージはマネージド版と同じ関数（ops/up-common.sh の build_grafana。版は GRAFANA_VERSION、タグは dir_tag）で、docker login のあと、analytics より前",
      'GRAFANA_TAG=$(dir_tag "$GRAFANA_VERSION" app/grafana)' in up and 'ecr_has "$PREFIX-grafana" "$GRAFANA_TAG"' in up
      and pos("aws ecr get-login-password") < pos("    build_grafana") < pos("tf_apply pipeline/analytics")
      and "    build_grafana" in read("ops/up.sh") and "GRAFANA_VERSION=" not in read("ops/up.sh") and "GRAFANA_VERSION=" not in up
      and re.search(r"^GRAFANA_VERSION=(\S+)", read("ops/up-common.sh"), re.M).group(1)
      == re.search(r"^ARG GRAFANA_VERSION=(\S+)", read("docker/images/grafana/Dockerfile"), re.M).group(1)
      and 'docker buildx build --platform linux/arm64 --build-arg "GRAFANA_VERSION=$GRAFANA_VERSION" -t "$REG/$PREFIX-grafana:$GRAFANA_TAG" --push -f docker/images/grafana/Dockerfile app/grafana/'
      in read("ops/up-common.sh"))
# analytics と stream の -var は配列（ANALYTICS_VARS / STREAM_VARS）にまとめ、1 台ずつの入れ替え（roll_nodes）と apply に同じものを渡す
_an = up[pos("ANALYTICS_VARS=("):pos('tf_apply pipeline/analytics "${ANALYTICS_VARS[@]}"')]
_st = up[pos("STREAM_VARS=("):pos('tf_apply pipeline/stream "${STREAM_VARS[@]}"')]
check("oss/ops/up.sh は stream と analytics の apply の前に roll_nodes（oss/ops/roll-nodes.sh）を同じ -var の配列で打つ",
      ". oss/ops/roll-nodes.sh" in up and 0 <= pos(". ops/up-common.sh") < pos(". oss/ops/roll-nodes.sh")
      and 0 <= pos("STREAM_VARS=(") < pos('roll_nodes kafka pipeline/stream "${STREAM_VARS[@]}"\ntf_apply pipeline/stream "${STREAM_VARS[@]}"\n')
      and 0 <= pos("ANALYTICS_VARS=(") < pos('roll_nodes opensearch pipeline/analytics "${ANALYTICS_VARS[@]}"\ntf_apply pipeline/analytics "${ANALYTICS_VARS[@]}"\n')
      and _an.count("\n\n") == 0 and _st.count("\n\n") == 0 and up.count("roll_nodes ") == 2
      and re.search(r'^OSS_ROLL="\$\{OSS_ROLL:-1\}"; flag_value OSS_ROLL\b', up, re.M) is not None
      and 'if [ -n "$ROLL_PLAN" ]; then rm -f "$ROLL_PLAN"; fi' in up[pos("\ntrap '"):up.index("\n", pos("\ntrap '") + 1)])
check("oss/ops/up.sh は analytics の前に Grafana の admin のパスワードを SSM に作り、analytics に create_grafana=true と Grafana のタグを渡す（OSS 版はいつも Grafana を作る）",
      0 <= pos('ensure_secret "/$PREFIX/grafana/admin-password" password') < pos("tf_apply pipeline/analytics")
      and "-var create_grafana=true" in _an and '-var "grafana_image_tag=$GRAFANA_TAG"' in _an)
check("oss/ops/up.sh は analytics に 4 つの格納先と、Spark・OpenSearch・VictoriaMetrics・Splunk のタグ、Splunk の台数と index、Spark のサブネット、device map を渡す",
      all(v in _an for v in ("-var 'sinks=[" + '"iceberg","opensearch","prometheus","splunk"' + "]'", '-var "spark_image_tag=$SPARK_TAG"',
                              '-var "opensearch_image_tag=$OSS_OPENSEARCH_TAG"', '-var "victoriametrics_image_tag=$OSS_VM_TAG"',
                              '-var "splunk_image_tag=$SPLUNK_TAG"', '-var "splunk_index=$SPLUNK_INDEX"', '-var "splunk_az_num=$SPLUNK_AZ_NUM"',
                              '-var "emr_az_num=$EMR_AZ_NUM"', '-var "device_map=$DEVICE_MAP"')))
check("oss/ops/up.sh は graph の前に SSM の /<接頭辞>/neo4j-password を作り、status の Lambda のレイヤー（app/graph/requirements-oss.txt を arm64 向けに）を入れ、Neo4j のタグと alert_history=true を渡す",
      0 <= pos('ensure_secret "/$PREFIX/neo4j-password" password') < pos("tf_apply pipeline/graph")
      and 0 <= pos("--target IaC/terraform/oss/pipeline/graph/.build/neo4j-layer/python") < pos("tf_apply pipeline/graph")
      and '--platform "$LAYER_PLATFORM" --python-version "$LAYER_PYVER" -r app/graph/requirements-oss.txt' in up
      and re.search(r"^LAYER_PLATFORM=manylinux2014_aarch64; LAYER_PYVER=3\.13$", up, re.M)
      # pip に渡す値とハッシュに入れる値は同じ変数（レイヤーの節に platform の文字そのものは定義の 1 回だけ。4-1 の wheel の pip は別）
      and 'echo "$LAYER_PLATFORM $LAYER_PYVER"' in up and up[pos("LAYER_PLATFORM="):pos('"$LAYER_SHA" > IaC/terraform/oss')].count("manylinux") == 1
      and "shasum" not in up and 0 <= pos("""| "${PY[@]}" -c 'import hashlib, sys; print(hashlib.sha256(""") < pos('"$LAYER_SHA" > IaC/terraform/oss/pipeline/graph/.build/neo4j-layer.sha256')
      and 'tf_apply pipeline/graph -var "neo4j_image_tag=$NEO4J_TAG" -var alert_history=true' in up)
_sync_tf = read("IaC/terraform/oss/pipeline/graph/sync.tf")
_layer_pyver = re.search(r"^LAYER_PLATFORM=\S+; LAYER_PYVER=(\S+)$", up, re.M).group(1)
check("oss/ops/up.sh の LAYER_PYVER は sync.tf の Lambda の runtime とレイヤーの compatible_runtimes と同じ版（片方だけ変えると読めないレイヤーになる）",
      f'runtime          = "python{_layer_pyver}"' in _sync_tf and f'compatible_runtimes      = ["python{_layer_pyver}"]' in _sync_tf
      and _sync_tf.count("python3.") == 2)
UP_ENDPOINTS = {"ssm", "ssmmessages", "ecr.api", "ecr.dkr", "logs", "s3tables", "sns", "kinesis-firehose",
                "bedrock-runtime", "bedrock-agentcore", "ecs", "sqs", "bedrock-agentcore.gateway", "athena"}
check("oss/ops/up.sh のエンドポイントは 14 個: 土台の 5 つ、analytics と graph の s3tables・sns・kinesis-firehose、agent の bedrock-runtime・bedrock-agentcore、"
      "nautobot の ecs、workflow の sqs・bedrock-agentcore.gateway・athena",
      (m := re.search(r'^ENDPOINTS="([^"]*)"$', up, re.M)) and UP_ENDPOINTS == set(m.group(1).split()) and len(m.group(1).split()) == 14)
check("oss/ops/up.sh は agent・graph・workflow に lambda_az_num を、agent に runtime_az_num を、nautobot に nautobot_db_az_num を渡す（マネージド版が渡している値）",
      all(re.search(rf'^az_num {k} 1 1 ', up, re.M) for k in ("RUNTIME_AZ_NUM", "LAMBDA_AZ_NUM", "NAUTOBOT_DB_AZ_NUM"))
      and 'tf_apply agent -var "agent_image_tag=$IMAGE_TAG" -var "runtime_az_num=$RUNTIME_AZ_NUM" -var "lambda_az_num=$LAMBDA_AZ_NUM"' in up
      and 'tf_apply pipeline/graph -var "neo4j_image_tag=$NEO4J_TAG" -var alert_history=true -var "lambda_az_num=$LAMBDA_AZ_NUM"' in up
      and 'tf_apply workflow -var "worker_image_tag=$IMAGE_TAG" -var "lambda_az_num=$LAMBDA_AZ_NUM"' in up
      and 'tf_apply pipeline/nautobot -var "nautobot_image_tag=$NAUTOBOT_TAG" -var "redis_image_tag=$REDIS_TAG" -var "nautobot_db_az_num=$NAUTOBOT_DB_AZ_NUM"' in up)
check("oss/ops/up.sh は analytics に http_send と Spark の 1 回に読む件数（max_offsets_per_trigger とその格納先ごと）を、stream に telegraf_az_num と dialin_targets_from_nautobot=true を渡す",
      '-var "http_send=$HTTP_SEND"' in _an and '-var "max_offsets_per_trigger=' in _an and '-var "max_offsets_per_trigger_by_sink=' in _an
      and '-var "telegraf_az_num=$TELEGRAF_AZ_NUM"' in up and "-var dialin_targets_from_nautobot=true" in up)
_spark_tf = read("IaC/terraform/oss/pipeline/analytics/spark.tf")
check("Spark のサービスは Terraform では 0 台で作り（desired_count = 0、あとの変更は見ない）、up.sh が OpenSearch・VictoriaMetrics・Splunk を待ったあとで 1 台にする",
      re.search(r"^\s*desired_count\s*=\s*0$", _spark_tf, re.M) and "ignore_changes = [desired_count]" in _spark_tf
      and 0 <= pos("tf_apply pipeline/analytics") < pos("--services $OS_SERVICES") < pos("--services $VM_SERVICES")
      < pos("'tasks[].healthStatus'") < pos('echo "Splunk は起動した"') < pos("--desired-count 1")
      and up.count("--desired-count 1") == 1)
check("oss/ops/up.sh は Neo4j のサービスが安定してから、Web の EC2（部品を入れ直したあと）で ops/seed_graph.py を流す（マネージド版が Neptune に入れるのと同じスクリプト）",
      0 <= pos("aws ec2 reboot-instances") < pos("tf_apply pipeline/graph") < pos('--services "$NEO4J_SERVICE"') < pos("base64 < ops/seed_graph.py")
      < pos("tf_apply pipeline/nautobot")
      and "ops/seed_graph.py" in read("ops/up.sh") and "/usr/bin/python3.13 -" in up)
check("oss/ops/up.sh は Web に Neo4j のドライバーを入れる（app/dashboard/requirements-oss.txt のホイールを wheels-oss/ に取り、S3 に上げる）。wheels-oss/ は git に入れない",
      "fetch_wheels wheels-oss app/dashboard/requirements-oss.txt app/dashboard/requirements.txt " in up
      and 'aws s3 sync --only-show-errors --delete --exclude .requirements.sha256 wheels-oss/ "s3://$KB_BUCKET/web/wheels/"' in up
      and re.search(r"^neo4j==", read("app/dashboard/requirements-oss.txt"), re.M) and re.search(r"^-r requirements\.txt$", read("app/dashboard/requirements-oss.txt"), re.M)
      and re.search(r"^wheels-oss/$", read(".gitignore"), re.M) and re.search(r"^IaC/terraform/\*\*/\.build/$", read(".gitignore"), re.M) and all(subprocess.run(["git", "check-ignore", "-q", f"IaC/terraform/{r}/.build/x"], cwd=ROOT).returncode == 0 for r in ("oss/pipeline/graph", "aws-managed/workflow", "aws-managed/agent")))
_fw = re.search(r"^fetch_wheels\(\) \{.*?^\}$", read("ops/up-common.sh"), re.M | re.S)
_fw = _fw.group(0) if _fw else ""
check("Web の wheel はマネージド版と OSS 版が同じ関数（ops/up-common.sh の fetch_wheels）で取り、requirements と pip の引数のハッシュが置き場の .requirements.sha256 と"
      "違えば置き場を消して取り直す。S3 へは --delete で写し、.sha256 は上げない（005 のレビュー Nit 5）",
      all(w in _fw for w in ('"${WHEEL_ARGS[*]}"', '"${WHEEL_ARGS[@]}"', 'rm -rf "$dir"', '"$dir/.requirements.sha256"', 'cat "${@:2}"'))
      and _fw.index('rm -rf "$dir"') < _fw.index("download") < _fw.index('> "$dir/.requirements.sha256"')
      and "\nfetch_wheels wheels app/dashboard/requirements.txt\n" in read("ops/up.sh")
      and 'aws s3 sync --only-show-errors --delete --exclude .requirements.sha256 wheels/ "s3://$KB_BUCKET/web/wheels/"' in read("ops/up.sh")
      and "manylinux" not in up[pos('log "4-1.'):pos('log "4-2.')] and "*.whl" not in up and "*.whl" not in read("ops/up.sh"))
check("oss/ops/up.sh はワーカーのイメージを Neo4j のドライバー入り（app/temporal/requirements-oss.txt）でビルドする",
      'build_worker "$IMAGE_TAG" requirements-oss.txt' in up and re.search(r"^neo4j==", read("app/temporal/requirements-oss.txt"), re.M))
check("oss/ops/up.sh は agent（Runtime）のイメージも Neo4j のドライバー入り（app/agentcore/requirements-oss.txt）でビルドする。マネージド版は既定（requirements.txt）のまま",
      'build_agent "$REPO:$IMAGE_TAG" requirements-oss.txt' in up and re.search(r"^neo4j==", read("app/agentcore/requirements-oss.txt"), re.M)
      and re.search(r"^ARG REQUIREMENTS=requirements\.txt$", read("docker/images/agentcore/Dockerfile"), re.M)
      and '--build-arg "REQUIREMENTS=${2:-requirements.txt}"' in read("ops/up-common.sh")
      and re.search(r'^\s*build_agent "\$REPO:\$IMAGE_TAG"(\s+#.*)?$', read("ops/up.sh"), re.M))
check("oss/ops/up.sh は nautobot の前に ensure_nautobot_secrets（マネージド版と同じ関数）を呼ぶ",
      0 <= pos("\nensure_nautobot_secrets") < pos("tf_apply pipeline/nautobot") and "ensure_nautobot_secrets" in read("ops/up.sh"))
check("oss/ops/up.sh は最後に Web へのポートフォワーディングを開く（NO_DASHBOARD_PORTFORWARD=1 なら開かずに終わる）。exec の前に一時ファイルを片付ける",
      0 <= pos("tf_apply workflow") < pos('if [ -n "$NO_DASHBOARD_PORTFORWARD" ]; then') < pos("\ntrap - EXIT") < pos("\nexec aws ssm start-session")
      and "--document-name AWS-StartPortForwardingSession" in up[pos("\nexec aws ssm start-session"):]
      and up.rstrip().endswith('--parameters "{\\"portNumber\\":[\\"8080\\"],\\"localPortNumber\\":[\\"$LOCAL_PORT\\"]}"'))
check("oss/ops/up.sh はポートフォワードの案内（Web・lab・Kafbat UI・Nautobot・Splunk・Grafana・Neo4j のブラウザと Bolt）を、パスワードの値ではなく取り方で出す",
      all(f"output -raw {o}" in up for o in ("start_session_command", "kafka_ui_port_forward_command", "kafka_ui_password_command", "port_forward_command",
                                              "password_command", "splunk_port_forward_command", "splunk_password_command", "grafana_port_forward_command",
                                              "grafana_password_command", "opensearch_password_parameter",
                                              "neo4j_password_parameter", "neo4j_browser_port_forward_command", "neo4j_bolt_port_forward_command")))
check("oss/ops/up.sh は SPLUNK_AZ_NUM が 2 以上（クラスター）なら SPLUNK_INDEX を書けない（マネージド版と同じ）",
      re.search(r'^az_num SPLUNK_AZ_NUM 1 1 3 ', up, re.M) and re.search(r'^az_num EMR_AZ_NUM 1 1 3 ', up, re.M)
      and re.search(r'^SPLUNK_INDEX="\$\{SPLUNK_INDEX:-\}"$', up, re.M)
      and 'SPLUNK_INDEX を消すか、SPLUNK_AZ_NUM=1 にする' in up)
check("oss/ops/up.sh は stream の前に SSM の /<接頭辞>/kafka/cluster-id を kafka-cluster-id で作り、stream に Kafka の版を渡す",
      0 <= pos('ensure_secret "/$PREFIX/kafka/cluster-id" kafka-cluster-id') < pos("tf_apply pipeline/stream")
      and '-var "kafka_image_tag=$OSS_KAFKA_TAG"' in up)
check("oss/ops/ はシークレットの値を読まない（get-parameter / --with-decryption を打たない）",
      not re.search(r"get-parameter\b|--with-decryption", up + down + img_sh))
check("oss/ops/up.sh の lab の既定の認証情報は ensure_fixed_secret にだけ渡す（echo しない）",
      all("ensure_fixed_secret" in line for line in up.splitlines() if re.search(r"\$\{?LAB_[A-Z_]*(PASSWORD|COMMUNITY|USERNAME)", line)))

# ================================================================ 6. oss/ops/up.sh を偽物の道具で最後まで通す
LAB_NODES = len(re.findall(r"^ *kind: (?:nokia_srlinux|linux)$", read("app/containerlab/splab.clab.yml.in"), re.M))
def run_up(inv, extra=None):
    reset(inv)
    shutil.rmtree(os.path.join(REPO, "ops", "logs"), ignore_errors=True)
    envfile = os.path.join(TMP, "owner-x.env")
    with open(envfile, "w", encoding="utf-8") as f:
        f.write("OWNER=x\n")
    p = subprocess.run(["bash", "oss/ops/up.sh"], cwd=REPO, capture_output=True, text=True, timeout=600,
                       env=fake_env({"DEPLOY_ENV_FILE": envfile, "FAKE_TF_UP": "1", "FAKE_LAB_NODES": str(LAB_NODES), **(extra or {})}))
    with open(INV, encoding="utf-8") as f:
        inv = json.load(f)
    return p, calls(), inv

def first(cs, pred):  # 条件に合う最初の呼び出しの位置。無ければ -1
    return next((i for i, c in enumerate(cs) if pred(c)), -1)
def is_aws(c, svc, op, *words):
    return c["cmd"] == "aws" and c["args"][:2] == [svc, op] and all(w in " ".join(c["args"]) for w in words)
def applies(cs):  # apply を打った順の (ルート, 引数)
    return [(chdir_of(c), c["args"]) for c in tf_calls(cs) if c["args"][1] == "apply"]
def has_var(a, v):
    return any(a[i] == "-var" and a[i + 1] == v for i in range(len(a) - 1))
def apply_at(cs, root):
    return first(cs, lambda c: c["cmd"] == "terraform" and c["args"][:2] == [f"-chdir=IaC/terraform/oss/{root}", "apply"])
def is_seed(c):
    return is_aws(c, "ssm", "send-command", "base64 -d | NAME_PREFIX=x-nwc-oss LAB_TOPOLOGY_B64=", "/usr/bin/python3.13 -")
def spark_starts(cs):
    return [arg_after(c["args"], "--service") for c in cs if is_aws(c, "ecs", "update-service", "--desired-count 1")]

UP_PARAMS = {f"/x-nwc-oss/{n}" for n in (
    "telegraf-dialin/gnmi-username", "telegraf-dialin/gnmi-password", "telegraf-dialin/snmp-community", "kafka-ui/admin-password",
    "kafka/cluster-id", "neo4j-password", "nautobot/secret-key", "nautobot/admin-password", "nautobot/db-password", "nautobot/api-token",
    "opensearch-password", "splunk/admin-password", "splunk/hec-token", "grafana/admin-password")}
SPARK_SERVICES = [f"x-nwc-oss-spark-{k}" for k in "abc"]
empty = {"ssm": {}, "vpcs": {}, "enis": [], "log_groups": [], "tagged": {}, "ecr": []}

# ---- 1 回目: 何も無いところから（ポートフォワーディングは開かない）
p, cs, inv = run_up(dict(empty), {"NO_DASHBOARD_PORTFORWARD": "1"})
out = p.stdout + p.stderr
ap = applies(cs)
check("up.sh（通し）: 終了コード 0 で最後まで行き、偽物の知らないコマンドを打たず、未定義の変数も踏まない",
      p.returncode == 0 and not [c for c in cs if "unknown" in c] and "unbound variable" not in out and "command not found" not in out
      and "NO_DASHBOARD_PORTFORWARD=1: Web へのポートフォワーディングは開かない" in p.stdout)
check("up.sh（通し）: IaC/terraform/oss/ の 9 つのルートを決めた順に 1 回ずつ apply し、どれにも owner=x を渡す（IaC/terraform/aws-managed/ のルートには触らない）",
      [r for r, _ in ap] == [f"IaC/terraform/oss/{r}" for r in ROOTS] and all(has_var(a, "owner=x") for _, a in ap)
      and all(chdir_of(c).startswith("IaC/terraform/oss/") for c in tf_calls(cs)))
A = {r[len("IaC/terraform/oss/"):]: a for r, a in ap}
check("up.sh（通し）: base/core にエンドポイント 14 個と、閉域（network_perimeter=true）と endpoints_az_num=1 を渡す",
      "base/core" in A and any(a.startswith("interface_endpoints=") and set(json.loads(a.partition("=")[2])) == UP_ENDPOINTS for a in A["base/core"])
      and has_var(A["base/core"], "network_perimeter=true") and has_var(A["base/core"], "endpoints_az_num=1"))
check("up.sh（通し）: agent に agent_image_tag=v1・runtime_az_num=1・lambda_az_num=1、workflow に worker_image_tag=v1・lambda_az_num=1 を渡す",
      all(has_var(A.get("agent", []), v) for v in ("agent_image_tag=v1", "runtime_az_num=1", "lambda_az_num=1"))
      and all(has_var(A.get("workflow", []), v) for v in ("worker_image_tag=v1", "lambda_az_num=1")))
check("up.sh（通し）: graph に alert_history=true・lambda_az_num=1 と Neo4j のタグ、nautobot に nautobot_db_az_num=1、lab に forward_to_telegraf=true を渡す",
      all(has_var(A.get("pipeline/graph", []), v) for v in ("alert_history=true", "lambda_az_num=1", f'neo4j_image_tag={T["neo4j"][0]}'))
      and has_var(A.get("pipeline/nautobot", []), "nautobot_db_az_num=1") and has_var(A.get("pipeline/lab", []), "forward_to_telegraf=true"))
check("up.sh（通し）: stream に Kafka の版・telegraf_az_num=1・snmp_poll=true・syslog_standard=RFC3164・dialin_targets_from_nautobot=true と、lab の定義から作った SNMP と gNMI の宛先を渡す",
      all(has_var(A.get("pipeline/stream", []), v) for v in (f'kafka_image_tag={V["OSS_KAFKA_TAG"]}', "telegraf_az_num=1", "snmp_poll=true",
                                                              "syslog_standard=RFC3164", "dialin_targets_from_nautobot=true"))
      and any(re.match(r'snmp_agents="udp://[0-9.]+:161"', a) for a in A.get("pipeline/stream", []))
      and any(re.match(r'gnmi_targets="[0-9.]+:57400"', a) for a in A.get("pipeline/stream", [])))
check("up.sh（通し）: analytics に 4 つの格納先・http_send=driver・Spark と OpenSearch と VictoriaMetrics のタグ・splunk_az_num=1・emr_az_num=1 を渡す",
      all(has_var(A.get("pipeline/analytics", []), v) for v in ('sinks=["iceberg","opensearch","prometheus","splunk"]', "http_send=driver",
                                                                 f'spark_image_tag={T["spark"][0]}', f'opensearch_image_tag={V["OSS_OPENSEARCH_TAG"]}',
                                                                 f'victoriametrics_image_tag={V["OSS_VM_TAG"]}', "splunk_az_num=1", "emr_az_num=1")))
_gver = re.search(r"^GRAFANA_VERSION=(\S+)", read("ops/up-common.sh"), re.M).group(1)
_gb = [c["args"] for c in cs if c["cmd"] == "docker" and c["args"][:2] == ["buildx", "build"] and c["args"][-1] == "app/grafana/"]
_gtag = arg_after(_gb[0], "-t") if _gb else ""
check("up.sh（通し）: Grafana のイメージを arm64 でビルドし（版は --build-arg）、同じタグと create_grafana=true を analytics に渡す",
      len(_gb) == 1 and re.fullmatch(re.escape(REG) + r"/x-nwc-oss-grafana:" + re.escape(_gver) + r"-[0-9a-f]+", _gtag)
      and _gb[0][2:4] == ["--platform", "linux/arm64"] and "--push" in _gb[0] and arg_after(_gb[0], "--build-arg") == f"GRAFANA_VERSION={_gver}"
      and has_var(A.get("pipeline/analytics", []), "create_grafana=true")
      and has_var(A.get("pipeline/analytics", []), "grafana_image_tag=" + _gtag.rsplit(":", 1)[-1]))
check("up.sh（通し）: SSM のパラメータを 14 個、全部 SecureString で、ManagedBy=oss/ops/up.sh・Project=x-nwc-oss のタグを付けて作る（oss/ops/down.sh が消せる）",
      set(inv["ssm"]) == UP_PARAMS and all(m["type"] == "SecureString" and m["tags"].get("ManagedBy") == "oss/ops/up.sh"
                                           and m["tags"].get("Project") == "x-nwc-oss" for m in inv["ssm"].values()))
_secrets = [m.get("value", "") for n, m in inv["ssm"].items() if "/telegraf-dialin/" not in n]
check("up.sh（通し）: 乱数で作ったシークレット（11 個）の値を、画面にも、aws・terraform・docker の引数にも出さない",
      len(_secrets) == 11 and all(len(v) >= 16 for v in _secrets)
      and not any(v in out or any(v in " ".join(c["args"]) for c in cs) for v in _secrets))
docker = [c["args"] for c in cs if c["cmd"] == "docker"]
_tags = {arg_after(a, "-t") for a in docker if a[:2] == ["buildx", "build"]} | {a[-1] for a in docker if a[0] == "push"}
check("up.sh（通し）: イメージを ECR に置く（OSS の 7 つ、lab の 2 つ、Telegraf、Kafbat UI、Splunk、Grafana、agent、worker、Temporal、Nautobot、Redis）。docker login のあと、stream の apply より前",
      {t.split("/", 1)[1].split(":")[0] for t in _tags if t.startswith(REG + "/")}
      >= {f"x-nwc-oss-{n}" for n in V["OSS_IMAGES"].split()} | {f"x-nwc-oss-{n}" for n in ("telegraf", "splunk", "grafana", "agent", "worker", "nautobot")}
      and len(_tags) >= 17 and all(t.startswith(REG + "/x-nwc-oss-") for t in _tags)
      and 0 <= first(cs, lambda c: c["cmd"] == "docker" and c["args"][0] == "login")
      < first(cs, lambda c: c["cmd"] == "docker" and c["args"][0] in ("push", "buildx") and c["args"][:2] != ["buildx", "ls"] and c["args"][:2] != ["buildx", "version"])
      and max(i for i, c in enumerate(cs) if c["cmd"] == "docker") < apply_at(cs, "base/core"))
check("up.sh（通し）: ワーカーと agent のイメージは Neo4j のドライバー入り（REQUIREMENTS=requirements-oss.txt）で、タグは IMAGE_TAG（v1）",
      all(any(a[:2] == ["buildx", "build"] and arg_after(a, "-t") == f"{REG}/x-nwc-oss-{n}:v1" and "REQUIREMENTS=requirements-oss.txt" in a
              for a in docker) for n in ("worker", "agent")))
uv = [c["args"] for c in cs if c["cmd"] == "uv"]
check("up.sh（通し）: Web のホイール（app/dashboard/requirements-oss.txt）を wheels-oss/ に取り、Web とエージェントの部品と一緒に S3 に上げてから Web の EC2 を再起動する",
      any("download" in a and arg_after(a, "-d") == "wheels-oss" and arg_after(a, "-r") == "app/dashboard/requirements-oss.txt" for a in uv)
      and 0 <= first(cs, lambda c: is_aws(c, "s3", "cp", "app/dashboard/requirements-oss.txt"))
      < first(cs, lambda c: is_aws(c, "s3", "sync", "wheels-oss/", "/web/wheels/"))
      < first(cs, lambda c: is_aws(c, "ec2", "reboot-instances")) < apply_at(cs, "pipeline/lab"))
check("up.sh（通し）: status の Lambda のレイヤー（app/graph/requirements-oss.txt）を arm64 向けに入れてから graph を apply する",
      any("install" in a and arg_after(a, "--target") == "IaC/terraform/oss/pipeline/graph/.build/neo4j-layer/python"
          and arg_after(a, "--platform") == "manylinux2014_aarch64" and arg_after(a, "-r") == "app/graph/requirements-oss.txt" for a in uv)
      and 0 <= first(cs, lambda c: c["cmd"] == "uv" and "install" in c["args"]) < apply_at(cs, "pipeline/graph"))

def layer_sha():  # up.sh と同じ計算（app/graph/requirements-oss.txt + pip に渡す platform / python の版。up.sh の LAYER_PLATFORM / LAYER_PYVER から読む）
    m = re.search(r"^LAYER_PLATFORM=(\S+); LAYER_PYVER=(\S+)$", read("oss/ops/up.sh"), re.M)
    with open(os.path.join(REPO, "app", "graph", "requirements-oss.txt"), "rb") as f:
        return hashlib.sha256(f.read() + f"{m.group(1)} {m.group(2)}\n".encode()).hexdigest()

def wheel_sha():  # ops/up-common.sh の fetch_wheels と同じ計算（requirements を渡した順に + WHEEL_ARGS を空白でつないだ行）
    args = re.search(r"^WHEEL_ARGS=\((.*?)\)$", read("ops/up-common.sh"), re.M | re.S).group(1).split()
    body = b"".join(open(os.path.join(REPO, "app", "dashboard", n), "rb").read() for n in ("requirements-oss.txt", "requirements.txt"))
    return hashlib.sha256(body + (" ".join(args) + "\n").encode()).hexdigest()

def wheel_stamp():
    try:
        with open(os.path.join(REPO, "wheels-oss", ".requirements.sha256"), encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

def layer_stamp():
    try:
        with open(os.path.join(REPO, "IaC/terraform/oss/pipeline/graph/.build/neo4j-layer.sha256"), encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

_wsync = cs[first(cs, lambda c: is_aws(c, "s3", "sync", "wheels-oss/", "/web/wheels/"))]["args"]
check("up.sh（通し）: ホイールを取ったあと、app/dashboard/requirements-oss.txt と requirements.txt と pip の引数の SHA-256 を wheels-oss/.requirements.sha256 に残し、"
      "S3 へは --delete で写して .sha256 は除く",
      wheel_stamp() == wheel_sha() and "--delete" in _wsync and arg_after(_wsync, "--exclude") == ".requirements.sha256"
      and any("download" in a and all(w in a for w in ("--platform", "manylinux_2_28_aarch64", "--abi", "cp313")) for a in uv))
check("up.sh（通し）: レイヤーを入れたあと、app/graph/requirements-oss.txt と platform / python の版の SHA-256 を .build/neo4j-layer.sha256 に残す",
      layer_stamp() == layer_sha() and os.path.isfile(os.path.join(REPO, "IaC/terraform/oss/pipeline/graph/.build/neo4j-layer/python/neo4j/__init__.py")))
_neo_wait = first(cs, lambda c: is_aws(c, "ecs", "wait", "--services out-graph-neo4j_service_name"))
check("up.sh（通し）: Neo4j のサービスが安定してから、Web の EC2 に ops/seed_graph.py を 1 回送る（graph の apply のあと、nautobot の apply の前）",
      0 <= apply_at(cs, "pipeline/graph") < _neo_wait < first(cs, is_seed) < apply_at(cs, "pipeline/nautobot")
      and len([c for c in cs if is_seed(c)]) == 1
      and arg_after(cs[first(cs, is_seed)]["args"], "--instance-ids") == "out-core-web_instance_id")
_seed = json.loads(arg_after(cs[first(cs, is_seed)]["args"], "--parameters"))["commands"][-1] if first(cs, is_seed) >= 0 else ""
_sent = re.search(r"echo (\S+) \| base64 -d \| NAME_PREFIX=x-nwc-oss LAB_TOPOLOGY_B64=(\S+) ", _seed)
check("up.sh（通し）: 送るのはリポジトリの ops/seed_graph.py そのもので、トポロジは app/containerlab/lab_topology.py が lab の定義から作ったもの（機器と回線が入っている）",
      _sent and base64.b64decode(_sent.group(1)).decode() == read("ops/seed_graph.py")
      and (lambda t: isinstance(t, dict) and len(json.dumps(t)) > 200)(json.loads(base64.b64decode(_sent.group(2)))))
_os_wait = first(cs, lambda c: is_aws(c, "ecs", "wait", "x-nwc-oss-opensearch-a"))
_vm_wait = first(cs, lambda c: is_aws(c, "ecs", "wait", "x-nwc-oss-victoriametrics-a"))
_sp_health = first(cs, lambda c: is_aws(c, "ecs", "describe-tasks", "tasks[].healthStatus"))
_spark_at = first(cs, lambda c: is_aws(c, "ecs", "update-service"))
check("up.sh（通し）: Spark のサービス（3 つ）を 1 台にするのは、analytics の apply、OpenSearch と VictoriaMetrics の安定、Splunk の HEALTHY のあと",
      0 <= apply_at(cs, "pipeline/analytics") < _os_wait < _vm_wait < _sp_health < _spark_at < apply_at(cs, "workflow")
      and spark_starts(cs) == SPARK_SERVICES and "Splunk は起動した" in p.stdout
      and all(arg_after(c["args"], "--cluster") == "out-analytics-analytics_cluster_name" for c in cs if is_aws(c, "ecs", "update-service")))
check("up.sh（通し）: OpenSearch と VictoriaMetrics は output のサービス名を全部（名前の順で）待つ",
      multi_of(cs[_os_wait]["args"], "--services") == [f"x-nwc-oss-opensearch-{k}" for k in "abc"]
      and multi_of(cs[_vm_wait]["args"], "--services") == [f"x-nwc-oss-victoriametrics-{k}" for k in "abc"])
check("up.sh（通し）: lab の EC2 でトポロジが上がったのを確かめ（コンテナの数は lab の定義から）、Telegraf へ通す（lab forward）。警告は出ない",
      first(cs, lambda c: is_aws(c, "ssm", "send-command", "out-lab-lab_instance_id", "containers=")) >= 0
      and first(cs, lambda c: is_aws(c, "ssm", "send-command", "out-lab-lab_instance_id", "/usr/local/bin/lab forward")) >= 0
      and LAB_NODES > 0 and "トポロジが上がっていない" not in out and "安定しない" not in out and "上がりきらない" not in out)
check("up.sh（通し）: workflow のワーカーが安定してから Web を起こし直し、ポートフォワードの案内（Web・Kafbat UI・Nautobot・Splunk・Grafana・Neo4j）を出す",
      0 <= apply_at(cs, "workflow") < first(cs, lambda c: is_aws(c, "ecs", "wait", "out-workflow-service_name"))
      < max(i for i, c in enumerate(cs) if is_aws(c, "ssm", "send-command", "systemctl restart x-nwc-oss-web.service"))
      and all(s in p.stdout for s in ("out-core-start_session_command", "out-stream-kafka_ui_port_forward_command", "out-nautobot-port_forward_command",
                                      "out-analytics-splunk_port_forward_command", "out-analytics-grafana_port_forward_command",
                                      "out-analytics-grafana_password_command", "out-graph-neo4j_browser_port_forward_command",
                                      "out-graph-neo4j_bolt_port_forward_command", "out-graph-neo4j_password_parameter"))
      and not aws_calls(cs, "ssm", "start-session"))
check("up.sh（通し）: PC に残すのは wheels-oss/ とレイヤーの .build/ と rpm とログだけで、Nautobot のイメージの材料の一時フォルダは片付ける",
      os.path.isdir(os.path.join(REPO, "wheels-oss")) and not [f for f in os.listdir(TMP) if f.startswith("x-nwc-oss-nautobot.")]
      and any(f.startswith("tf-oss-") and f.endswith("-apply.log") for f in logs_made()))

# ---- 2 回目: 打ち直し（イメージも SSM のパラメータもある）。最後にポートフォワーディングを開く
made_values = {n: m.get("value") for n, m in inv["ssm"].items()}
p, cs2, inv2 = run_up(inv, {"FAKE_ECR_ALL": "1"})
docker2 = [c["args"] for c in cs2 if c["cmd"] == "docker"]
check("up.sh（打ち直し）: 終了コード 0。ECR にあるイメージは写さずビルドもせず、docker にも入らない",
      p.returncode == 0 and not [c for c in cs2 if "unknown" in c] and not [a for a in docker2 if a[0] in ("pull", "push", "login") or a[:2] == ["buildx", "build"]])
check("up.sh（打ち直し）: SSM のパラメータを作り直さない（値が変わると、動いている Kafka・Nautobot の DB・Neo4j と合わなくなる）",
      not aws_calls(cs2, "ssm", "put-parameter") and {n: m.get("value") for n, m in inv2["ssm"].items()} == made_values
      and all(f"{n} はある（作り直さない）" in p.stdout for n in UP_PARAMS))
check("up.sh（打ち直し）: status の Lambda のレイヤーは作り直さない（neo4j/ と .sha256 がそろっていれば pip を打たず、「はある」と言う）",
      not [c for c in cs2 if c["cmd"] == "uv" and "install" in c["args"]] and layer_stamp() == layer_sha()
      and "neo4j-layer）はある（app/graph/requirements-oss.txt は変わっていない）" in p.stdout)
check("up.sh（打ち直し）: ホイールは取り直さず（.sha256 が合っていれば「変わっていない」と言う）、9 つのルートは同じ順で apply し直し、同期と Spark の起動もやり直す",
      not [c for c in cs2 if c["cmd"] == "uv" and "download" in c["args"]] and wheel_stamp() == wheel_sha()
      and "wheels-oss/ に 1 個ある（app/dashboard/requirements-oss.txt app/dashboard/requirements.txt は変わっていない）" in p.stdout
      and [r for r, _ in applies(cs2)] == [f"IaC/terraform/oss/{r}" for r in ROOTS]
      and len([c for c in cs2 if is_seed(c)]) == 1 and spark_starts(cs2) == SPARK_SERVICES)
_last = cs2[-1]["args"] if cs2 else []
check("up.sh（打ち直し）: 最後に Web の EC2 へのポートフォワーディング（8080 → LOCAL_PORT の既定 8080）を開く",
      _last[:2] == ["ssm", "start-session"] and arg_after(_last, "--target") == "out-core-web_instance_id"
      and arg_after(_last, "--document-name") == "AWS-StartPortForwardingSession"
      and json.loads(arg_after(_last, "--parameters")) == {"portNumber": ["8080"], "localPortNumber": ["8080"]}
      and not [f for f in os.listdir(TMP) if f.startswith("x-nwc-oss-nautobot.")])

# ---- 3 回目: Neo4j と OpenSearch のサービスが安定しない（ついでに app/graph/requirements-oss.txt を変えて、レイヤーを作り直すことも見る）
_old_sha, _old_wheel_sha = layer_sha(), wheel_sha()
with open(os.path.join(REPO, "app", "graph", "requirements-oss.txt"), "a", encoding="utf-8") as f:
    f.write("# 版を変えたつもり\n")
with open(os.path.join(REPO, "app", "dashboard", "requirements.txt"), "a", encoding="utf-8") as f:   # -r で読まれる側だけを変える
    f.write("# 版を変えたつもり\n")
open(os.path.join(REPO, "wheels-oss", "old-0.9-py3-none-any.whl"), "w").close()   # 前の版の wheel
p, cs3, inv3 = run_up(inv2, {"FAKE_ECR_ALL": "1", "NO_DASHBOARD_PORTFORWARD": "1",
                             "FAKE_ECS_UNSTABLE": "out-graph-neo4j_service_name,x-nwc-oss-opensearch-b,out-workflow-service_name"})
out3 = p.stdout + p.stderr
check("up.sh（requirements-oss.txt を変えた）: レイヤーを消して pip を打ち直し、新しい SHA-256 を .sha256 に残す",
      [c for c in cs3 if c["cmd"] == "uv" and "install" in c["args"]] and layer_sha() != _old_sha and layer_stamp() == layer_sha()
      and "neo4j-layer）はある" not in out3)
check("up.sh（app/dashboard/requirements.txt を変えた）: wheels-oss/ を消して取り直し（前の版の wheel は残らない）、新しい SHA-256 を .requirements.sha256 に残す",
      [c for c in cs3 if c["cmd"] == "uv" and "download" in c["args"]] and wheel_sha() != _old_wheel_sha and wheel_stamp() == wheel_sha()
      and sorted(os.listdir(os.path.join(REPO, "wheels-oss"))) == [".requirements.sha256", "fake-1.0-py3-none-any.whl"]
      and "は変わっていない）" not in out3.split("4-1.", 1)[-1].split("4-2.", 1)[0])
check("up.sh（Neo4j が安定しない）: 同期を送らず、警告（トポロジは入れていない）を出して先へ進む（nautobot・analytics・workflow まで apply する）",
      p.returncode == 0 and not [c for c in cs3 if is_seed(c)] and out3.count("トポロジは入れていない") == 2
      and [r for r, _ in applies(cs3)] == [f"IaC/terraform/oss/{r}" for r in ROOTS])
check("up.sh（OpenSearch が安定しない）: 警告（どの格納先か）を出し、VictoriaMetrics と Splunk は待ち、Spark は起こす（書き先が上がれば ECS が起こし直す）",
      out3.count("格納先が 20 分たっても上がりきらない: OpenSearch（") == 2 and "VictoriaMetrics（" not in out3.split("上がりきらない:", 1)[-1].split("。Spark は", 1)[0]
      and first(cs3, lambda c: is_aws(c, "ecs", "wait", "x-nwc-oss-victoriametrics-a")) >= 0 and spark_starts(cs3) == SPARK_SERVICES)

_wf_waits = [i for i, c in enumerate(cs3) if is_aws(c, "ecs", "wait", "out-workflow-service_name")]
check("up.sh（workflow のワーカーが安定しない）: 2 回待ち（1 回 10 分）、警告を出して先へ進む（Web の起こし直しと最後の案内まで届き、警告は最後にもう一度出す。"
      "Temporal の UI の案内はタスクの IP が無いので出さない。005 のレビュー Nit 6）",
      p.returncode == 0 and len(_wf_waits) == 2 and out3.count("workflow のワーカーのサービス（out-workflow-service_name）が 20 分たっても安定しない") == 2
      and not [c for c in cs3 if is_aws(c, "ecs", "list-tasks", "out-workflow-service_name")]
      and max(i for i, c in enumerate(cs3) if is_aws(c, "ssm", "send-command", "systemctl restart x-nwc-oss-web.service")) > _wf_waits[-1]
      and "Temporal の UI" not in out3 and "Temporal の UI" in out)
_mup = read("ops/up.sh")
_m85 = _mup[_mup.index("# ---- 8-5. workflow"):_mup.index("# ---- 9. Runtime")]
check("ops/up.sh（マネージド版）の workflow の待ちも 2 回までで、安定しなければ警告（WF_WARN）を出して先へ進み、最後にもう一度出す",
      _m85.count('aws ecs wait services-stable --region "$REGION" --cluster "$WF_CLUSTER" --services "$WF_SERVICE"') == 2
      and 'WF_WARN="workflow のワーカーのサービス' in _m85 and re.search(r'^WF_WARN=""$', _mup, re.M)
      and """if [ -n "$WF_WARN" ]; then printf '\\033[1;33m%s\\033[0m\\n' "$WF_WARN"; fi""" in _mup)

# ---- up.sh の作ったものを oss/ops/down.sh が消す（同じ在庫から）
p, csd, invd = run_down("oss/ops/down.sh", "x", inv=inv3)
check("up.sh → down.sh: up.sh が作った SSM のパラメータ 14 個を全部消し、up.sh が apply した 9 つのルートを全部 destroy する",
      p.returncode == 0 and invd["ssm"] == {} and len(aws_calls(csd, "ssm", "delete-parameter")) == 14
      and destroyed(csd) == {f"IaC/terraform/oss/{r}" for r in ROOTS} and "残り: 0 件" in p.stdout)

check("oss/ops/up.sh と down.sh の terraform init は、どのルートも -lockfile=readonly（lock はマネージド版へのシンボリックリンクなので書き換えない）",
      len({tuple(c["args"][:1]) for c in tf_calls(cs) if c["args"][1] == "init"}) == 9
      and all(a == ["init", "-input=false", "-lockfile=readonly"] for a in inits(cs) + inits(csd)) and inits(csd))
check("IaC/terraform/oss/ の .terraform.lock.hcl は、どのルートもマネージド版の lock へのシンボリックリンク（実ファイルにしない）",
      all(os.path.islink(os.path.join(ROOT, "IaC/terraform/oss", r, ".terraform.lock.hcl")) for r in ROOTS))

# ---- up.sh / down.sh が -var で渡す名前は、どれもそのルートの variable "名" として宣言されている（シンボリックリンク先も読む）
def tf_variables(root):
    d = os.path.join(ROOT, "IaC/terraform/oss", root)
    names = set()
    for n in os.listdir(d):
        if n.endswith(".tf"):
            with open(os.path.join(d, n), encoding="utf-8") as f:   # open はシンボリックリンクの先を読む
                names |= set(re.findall(r'^variable\s+"(\w+)"', f.read(), re.M))
    return names
def var_names(args):
    return {args[i + 1].partition("=")[0] for i in range(len(args) - 1) if args[i] == "-var"}
_up_vars = {(r[len("IaC/terraform/oss/"):], v) for r, a in ap for v in var_names(a)}
_down_vars = {(chdir_of(c)[len("IaC/terraform/oss/"):], v) for c in tf_calls(csd) if c["args"][1] == "destroy" for v in var_names(c["args"])}
check("up.sh（通し）が 9 つのルートに渡した -var の名前は、どれも IaC/terraform/oss/<ルート>/*.tf の variable で宣言されている（owner を含めて全部）",
      _up_vars and {r for r, _ in _up_vars} == set(ROOTS) and not [(r, v) for r, v in _up_vars if v not in tf_variables(r)])
check("down.sh が destroy に渡した -var（workflow の worker_image_tag、stream の snmp_agents / gnmi_targets、owner）も、そのルートの variable で宣言されている",
      {("workflow", "worker_image_tag"), ("pipeline/stream", "snmp_agents"), ("pipeline/stream", "gnmi_targets")} <= _down_vars
      and not [(r, v) for r, v in _down_vars if v not in tf_variables(r)])

# ---- readonly の init が止まったとき（この PC の OS・CPU のハッシュが lock に無い）
p, cs, inv = run_up(dict(empty), {"NO_DASHBOARD_PORTFORWARD": "1", "FAKE_TF_INIT_FAIL": "IaC/terraform/oss/base/ecr"})
out = p.stdout + p.stderr
check("up.sh: readonly の init が止まったら apply せずに止まり、先にマネージド版のルートを init する案内（terraform -chdir=IaC/terraform/aws-managed/base/ecr init）を出す",
      p.returncode != 0 and not applies(cs) and "IaC/terraform/oss/base/ecr の init に失敗した" in out
      and "terraform -chdir=IaC/terraform/aws-managed/base/ecr init -input=false" in out and "-lockfile=readonly" in out)

# ================================================================ 7. ops/sync-graph.sh（--oss で OSS 版の state と接頭辞。up.sh の 7-3b と同じ処理を単独で）
def run_sync(*args):
    reset(inventory())
    envfile = os.path.join(TMP, "owner-x.env")
    with open(envfile, "w", encoding="utf-8") as f:
        f.write("OWNER=x\n")
    p = subprocess.run(["bash", "ops/sync-graph.sh", *args], cwd=REPO, capture_output=True, text=True, timeout=300,
                       env=fake_env({"DEPLOY_ENV_FILE": envfile, "FAKE_TF_UP": "1"}))
    with open(INV, encoding="utf-8") as f:
        inv = json.load(f)
    return p, calls(), inv

def sent_seed(inv):  # 送ったコマンドから (接頭辞, トポロジ JSON, GRAPH_REPLACE, スクリプトが ops/seed_graph.py そのものか)
    cmd = json.loads(inv["cmds"][-1])["commands"][-1]
    m = re.search(r"^echo (\S+) \| base64 -d \| NAME_PREFIX=(\S+) LAB_TOPOLOGY_B64=(\S+) GRAPH_REPLACE=(\d) /usr/bin/python3\.13 -$", cmd)
    return (m.group(2), json.loads(base64.b64decode(m.group(3))), m.group(4), base64.b64decode(m.group(1)).decode() == read("ops/seed_graph.py")) if m else None

p, cs, inv = run_sync("--oss")
_tf = [c["args"] for c in tf_calls(cs)]
_send = [c for c in cs if is_aws(c, "ssm", "send-command")]
check("sync-graph.sh --oss: Web の EC2 の id を IaC/terraform/oss/base/core の出力から取り、接頭辞 x-nwc-oss と lab のトポロジで ops/seed_graph.py を送る（GRAPH_REPLACE=0）",
      p.returncode == 0 and _tf == [["-chdir=IaC/terraform/oss/base/core", "output", "-raw", "web_instance_id"]]
      and len(_send) == 1 and arg_after(_send[0]["args"], "--instance-ids") == "out-core-web_instance_id"
      and (s := sent_seed(inv)) and s[0] == "x-nwc-oss" and s[2] == "0" and s[3] and len(s[1].get("nodes", s[1].get("devices", []))) == LAB_NODES
      and "再読み込み" in p.stdout)
p, cs, inv = run_sync("--oss", "--replace")
check("sync-graph.sh --oss --replace: 同じ送り先で GRAPH_REPLACE=1（入っていても lab の定義で入れ直す）",
      p.returncode == 0 and [c["args"][0] for c in tf_calls(cs)] == ["-chdir=IaC/terraform/oss/base/core"] and sent_seed(inv)[0] == "x-nwc-oss" and sent_seed(inv)[2] == "1")
p, cs, inv = run_sync()
check("sync-graph.sh（--oss 無し）: マネージド版の IaC/terraform/aws-managed/base/core と接頭辞 x-nwc-poc のまま（OSS 版の追加で変わらない）",
      p.returncode == 0 and [c["args"][0] for c in tf_calls(cs)] == ["-chdir=IaC/terraform/aws-managed/base/core"] and sent_seed(inv)[0] == "x-nwc-poc" and sent_seed(inv)[2] == "0")
p, cs, inv = run_sync("--oss", "--dry-run")
check("sync-graph.sh --oss --dry-run: terraform にも aws にも触らず、lab から作ったトポロジ JSON を出すだけ",
      p.returncode == 0 and not tf_calls(cs) and not [c for c in cs if c["cmd"] == "aws"] and not inv.get("cmds")
      and isinstance(json.loads(p.stdout), dict))
p, cs, inv = run_sync("--oss", "--yes")
check("sync-graph.sh: 知らない引数は使い方を出して 2 で止まる（terraform と aws には触らない）",
      p.returncode == 2 and "使い方" in p.stderr and not tf_calls(cs) and not inv.get("cmds"))

shutil.rmtree(TMP, ignore_errors=True)
print(f"通過 {passed} / 失敗 0")
