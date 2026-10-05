"""cycle 005（マネージドを OSS に置き換えた環境を作る）の oss/ops/（up.sh / down.sh / oss-images.sh）の模擬テスト。
aws / terraform / docker は偽物（下の FAKE_*）に差し替え、AWS には触れない。
  1. 接頭辞 <owner>-nwc-oss が、どの OWNER の組み合わせでもマネージド版の <owner>-nwc-poc と同じにならない（resolve_name_prefix を bash で呼ぶ）
  2. oss/ops/down.sh は oss/terraform/ の state と、名前・タグが <owner>-nwc-oss のもの（SSM のパラメータは ManagedBy=oss/ops/up.sh だけ）しか消さない。
     いちばん紛らわしい OWNER=x-nwc-oss のマネージド版（接頭辞 x-nwc-oss-nwc-poc。OSS 版の x-nwc-oss と頭が同じ）を同じアカウントに並べて確かめる。
     逆向き（ops/down.sh が OSS 版に触らない）も見る。消したあとに残っているもの（Project タグ）を数えて出す
  3. stream が消えなかったときは Kafka の CLUSTER_ID を残し、残りのルートは消しにいき、終了コード 1 で消えなかったルートを出す
  4. イメージの名前と版が oss/compose/（正）・spark/ と neo4j/ の Dockerfile・terraform の既定値・ECR のリポジトリに合い、
     mirror_oss_images が ECR に無いものだけを写す（spark / neo4j はリポジトリの直下の spark/・neo4j/ をビルドする）
  5. ensure_secret の kafka-cluster-id（KRaft の CLUSTER_ID の形）と strong-password（OpenSearch の admin。値は画面に出さない）と、
     oss/ops/ が ops/ の関数を写さず読むこと、up.sh が analytics と graph まで当て、Splunk のイメージと SSM をマネージド版と同じ関数で用意すること
実行は python3 tests/test_oss_ops.py"""
import base64, json, os, re, shutil, subprocess, tempfile, types

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
    print("123456789012")
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
    if f'{opt("--repository-name")}:{tag}' not in inv.get("ecr", []):
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
    sys.exit(0)
if rest[:2] == ["state", "list"]:
    print("aws_instance.this")
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
'''

ROOTS = ["base/ecr", "base/core", "agent", "pipeline/lab", "pipeline/stream", "pipeline/analytics",
         "pipeline/graph", "pipeline/nautobot", "workflow"]

def lambda_eni(eni_id, fn, vpc):
    return {"id": eni_id, "desc": f"AWS Lambda VPC ENI-{fn}-0a1b2c", "status": "available", "type": "lambda", "vpc": vpc}

def ssm_param(managed_by, project):
    tags = {"Project": project}
    if managed_by:
        tags["ManagedBy"] = managed_by
    return {"type": "SecureString", "tags": tags}

# 同じアカウントに 3 つが並んでいる: OSS 版（OWNER=x → x-nwc-oss）、マネージド版（OWNER=x-nwc-oss → x-nwc-oss-nwc-poc）、
# マネージド版（OWNER=x → x-nwc-poc）
OSS_MANAGED_PARAMS = ["/x-nwc-oss/kafka/cluster-id", "/x-nwc-oss/kafka-ui/admin-password", "/x-nwc-oss/telegraf-dialin/gnmi-password"]
def inventory():
    return {
        "ssm": {
            **{n: ssm_param("oss/ops/up.sh", "x-nwc-oss") for n in OSS_MANAGED_PARAMS},
            "/x-nwc-oss/manual/note": ssm_param(None, "x-nwc-oss"),  # 手で入れたもの（ManagedBy が無い）は残す
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
for name, body in (("aws", FAKE_AWS), ("terraform", FAKE_TF), ("docker", FAKE_DOCKER), ("sleep", "#!/bin/sh\nexec /bin/sleep 0.1\n")):
    with open(os.path.join(BIN, name), "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(os.path.join(BIN, name), 0o755)
LOG, INV = os.path.join(TMP, "calls.jsonl"), os.path.join(TMP, "inv.json")

# down.sh を打つ場所（リポジトリの写し）。ops/ と oss/ops/ のスクリプトと、9 つのルートの state だけを置く
REPO = os.path.join(TMP, "repo")
for d in ("ops", "oss/ops"):
    os.makedirs(os.path.join(REPO, d))
    for f in os.listdir(os.path.join(ROOT, d)):
        if f.endswith(".sh"):
            shutil.copy(os.path.join(ROOT, d, f), os.path.join(REPO, d, f))
for base in ("terraform", "oss/terraform"):
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

def run_down(script, owner, extra=None):
    reset(inventory())
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

def destroyed(cs):  # destroy を打ったルート（-chdir の値）
    return {chdir_of(c) for c in tf_calls(cs) if c["args"][1] == "destroy"}

def aws_calls(cs, svc, op):
    return [c["args"] for c in cs if c["cmd"] == "aws" and c["args"][:2] == [svc, op]]

def arg_after(a, name):
    return a[a.index(name) + 1]

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
check("terraform は oss/terraform/ の下だけを -chdir で触り、terraform/（マネージド版の state）には入らない",
      tf_calls(cs) and all(chdir_of(c).startswith("oss/terraform/") for c in tf_calls(cs)))
check("oss/terraform/ の 9 つのルートを全部 destroy した", destroyed(cs) == {f"oss/terraform/{r}" for r in ROOTS})
check("destroy には -var owner=x を渡す",
      all("owner=x" in c["args"] for c in tf_calls(cs) if c["args"][1] == "destroy"))
check("terraform に渡す認証のプロファイル名は接頭辞から作る（x-nwc-oss-terraform）",
      {c["profile"] for c in tf_calls(cs)} == {"x-nwc-oss-terraform"})
check("terraform のログは ops/logs/tf-oss-*（マネージド版の tf-* を上書きしない）",
      logs_made() and all(f.startswith("tf-oss-") for f in logs_made()) and "tf-oss-pipeline-stream-destroy.log" in logs_made())
check("SSM: oss/ops/up.sh が作った 3 つ（Kafka の CLUSTER_ID を含む）を消し、手で入れたものとマネージド版のものは残す",
      set(inv["ssm"]) == ALL_PARAMS - set(OSS_MANAGED_PARAMS))
check("SSM: delete-parameter は /x-nwc-oss/ の下にしか打っていない",
      all(arg_after(a, "--name").startswith("/x-nwc-oss/") for a in aws_calls(cs, "ssm", "delete-parameter")))
check("SSM の絞り込みは Path=/x-nwc-oss/（末尾の / まで）と ManagedBy=oss/ops/up.sh",
      any(a[a.index("--parameter-filters") + 1:a.index("--parameter-filters") + 3]
          == ["Key=Path,Option=Recursive,Values=/x-nwc-oss/", "Key=tag:ManagedBy,Values=oss/ops/up.sh"]
          for a in aws_calls(cs, "ssm", "describe-parameters")))
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
check("KEEP_ECR を書かなければ ECR も消す（oss/terraform/base/ecr を destroy した）", "oss/terraform/base/ecr" in destroyed(cs))

# ================================================================ 2'. 逆向き: マネージド版の ops/down.sh は OSS 版に触らない
p, cs, inv = run_down("ops/down.sh", "x-nwc-oss")
out = p.stdout + p.stderr
check("ops/down.sh（OWNER=x-nwc-oss → 接頭辞 x-nwc-oss-nwc-poc）: 終了コード 0", p.returncode == 0)
check("マネージド版: 偽物の aws に知らないコマンドを打っていない", not [c for c in cs if c.get("unknown")])
check("マネージド版: terraform は terraform/ の下だけ（oss/terraform/ の state には入らない）",
      tf_calls(cs) and all(chdir_of(c).startswith("terraform/") for c in tf_calls(cs)))
check("マネージド版: Runtime の ENI が残っているので base/core は -target で ENI に関わらないものだけ消す",
      "Runtime の ENI が残っている" in out
      and any(chdir_of(c) == "terraform/base/core" and "-target=aws_instance.this" in c["args"] for c in tf_calls(cs)))
check("マネージド版: SSM は /x-nwc-oss-nwc-poc/ の ManagedBy=ops/up.sh の 2 つだけ消し、OSS 版の 4 つは残す",
      set(inv["ssm"]) == ALL_PARAMS - {"/x-nwc-oss-nwc-poc/kafka-ui/admin-password", "/x-nwc-oss-nwc-poc/nautobot/secret-key"})
check("マネージド版: Lambda の ENI は x-nwc-oss-nwc-poc の 2 つだけ消し、OSS 版のものは残す",
      eni_ids(inv) == ALL_ENIS - {"eni-mgd-tools", "eni-mgd-graph"})
check("マネージド版: ロググループは x_nwc_oss_nwc_poc_agent- だけ消す",
      set(inv["log_groups"]) == ALL_LOG_GROUPS - {"/aws/bedrock-agentcore/runtimes/x_nwc_oss_nwc_poc_agent-BBB-DEFAULT"})
check("マネージド版: 残りも Project=x-nwc-oss-nwc-poc で数える（「残り: 2 件」）",
      "残り: 2 件（Project=x-nwc-oss-nwc-poc のタグ）" in out and "x-nwc-oss-left" not in out)
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
p, cs, inv = run_down("oss/ops/down.sh", "x", {"FAKE_TF_FAIL": "oss/terraform/pipeline/stream", "FAKE_TAG_FAIL": "1", "KEEP_ECR": "1"})
out = p.stdout + p.stderr
check("stream が消えなかった: 終了コード 1 で「NG: 消えなかったルート: pipeline/stream」と出す",
      p.returncode == 1 and "NG: 消えなかったルート: pipeline/stream（" in out)
check("stream が消えなかった: 後ろのルート（lab / agent / base/core）は消しにいく",
      {"oss/terraform/pipeline/lab", "oss/terraform/agent", "oss/terraform/base/core"} <= destroyed(cs))
check("stream が消えなかった: Kafka の CLUSTER_ID は残し（次の down.sh で消す）、ほかの oss/ops/up.sh のパラメータは消す",
      "/x-nwc-oss/kafka/cluster-id: 残す" in out
      and set(inv["ssm"]) == ALL_PARAMS - set(OSS_MANAGED_PARAMS) | {"/x-nwc-oss/kafka/cluster-id"})
check("KEEP_ECR=1 なら oss/terraform/base/ecr は destroy しない", "oss/terraform/base/ecr" not in destroyed(cs))
check("残りを数えられなかった（タグの API のエラー）ときは、0 件と言わずに「数えられなかった」と出す",
      "残り: 数えられなかった（上のエラー）" in out and "残り: 0 件" not in out)

p, cs, inv = run_down("oss/ops/down.sh", "x", {"FAKE_TF_FAIL": "oss/terraform/base/core"})
check("base/core（Kafka のデータの EFS）が消えなかったときも Kafka の CLUSTER_ID は残す",
      p.returncode == 1 and "/x-nwc-oss/kafka/cluster-id" in inv["ssm"] and "/x-nwc-oss/kafka-ui/admin-password" not in inv["ssm"])

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
check("ビルドする spark / neo4j の版は spark/・neo4j/ の Dockerfile の ARG の既定値と同じで、oss/compose/<名前>/Dockerfile の FROM とも同じ",
      re.search(rf'^ARG SPARK_VERSION={re.escape(V["OSS_SPARK_VERSION"])}\s*$', read("spark/Dockerfile"), re.M)
      and re.search(r'^FROM apache/spark:\$\{SPARK_VERSION\}\s*$', read("spark/Dockerfile"), re.M)
      and re.search(rf'^ARG NEO4J_VERSION={re.escape(V["OSS_NEO4J_VERSION"])}\s*$', read("neo4j/Dockerfile"), re.M)
      and re.search(r'^FROM neo4j:\$\{NEO4J_VERSION\}-community\s*$', read("neo4j/Dockerfile"), re.M)
      and re.search(rf'^FROM apache/spark:{re.escape(V["OSS_SPARK_VERSION"])}\s*$', read("oss/compose/spark/Dockerfile"), re.M)
      and re.search(rf'^FROM neo4j:{re.escape(V["OSS_NEO4J_VERSION"])}-community\s*$', read("oss/compose/neo4j/Dockerfile"), re.M))
check("Neo4j の版は oss/terraform/pipeline/graph/neo4j.tf の neo4j_image_tag の既定値と同じ",
      tf_default_early("oss/terraform/pipeline/graph/neo4j.tf", "neo4j_image_tag") == V["OSS_NEO4J_VERSION"])
def tf_default(path, var):
    m = re.search(rf'variable\s+"{var}"\s*\{{[^}}]*?default\s*=\s*"([^"]+)"', read(path), re.S)
    return m and m.group(1)
check("Kafka の版は oss/terraform/pipeline/stream/kafka.tf の kafka_image_tag の既定値と同じ",
      tf_default("oss/terraform/pipeline/stream/kafka.tf", "kafka_image_tag") == V["OSS_KAFKA_TAG"])
check("Kafbat UI の版はマネージド版の ops/up.sh の KAFKA_UI_TAG と stream の kafka_ui_image_tag の既定値と同じ",
      re.search(r"^KAFKA_UI_TAG=(\S+)", read("ops/up.sh"), re.M).group(1) == V["OSS_KAFKA_UI_TAG"]
      == tf_default("terraform/pipeline/stream/variables.tf", "kafka_ui_image_tag"))
m = re.search(r'oss_repositories\s*=.*?toset\(\[([^\]]*)\]\)', read("terraform/base/ecr/main.tf"))
check("OSS_IMAGES（ECR に写すもの）は terraform/base/ecr の oss_repositories と同じ 7 つ",
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
check("spark / neo4j のタグは「<Dockerfile の ARG の版>-<spark/・neo4j/ の中身のハッシュ 12 桁>」で、写す元は無い（ビルドする）",
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
check("mirror_oss_images: spark / neo4j はリポジトリの直下の spark/・neo4j/ を linux/arm64 でビルドして push し、版を --build-arg で渡す",
      sorted(_builds) == ["neo4j", "spark"]
      and all(a[2:4] == ["--platform", "linux/arm64"] and "--push" in a for a in _builds.values())
      and arg_after(_builds["spark"], "--build-arg") == f'SPARK_VERSION={V["OSS_SPARK_VERSION"]}'
      and arg_after(_builds["neo4j"], "--build-arg") == f'NEO4J_VERSION={V["OSS_NEO4J_VERSION"]}')
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
check("oss/ops/ の 2 つは resolve_name_prefix nwc-oss で接頭辞を作り、TF_DIR=oss/terraform・OPS_DIR=oss/ops・TF_LOG_NAME=tf-oss にする",
      all("resolve_name_prefix nwc-oss" in t and re.search(r"^TF_DIR=oss/terraform\b", t, re.M)
          and re.search(r"^OPS_DIR=oss/ops\b", t, re.M) and re.search(r"^TF_LOG_NAME=tf-oss\b", t, re.M) for t in (up, down)))
pos = lambda s: up.find(s)
check("oss/ops/up.sh はルートを base/ecr → base/core → pipeline/lab → pipeline/stream → pipeline/analytics → pipeline/graph の順に当て、ROOTS もその 6 つ",
      0 <= pos("tf_apply base/ecr") < pos("tf_apply base/core") < pos("tf_apply pipeline/lab") < pos("tf_apply pipeline/stream")
      < pos("tf_apply pipeline/analytics") < pos("tf_apply pipeline/graph")
      and re.search(r'^ROOTS="base/ecr base/core pipeline/lab pipeline/stream pipeline/analytics pipeline/graph"$', up, re.M))
check("oss/ops/up.sh は ECR ができてから OSS のイメージを全部（OSS_IMAGES の 7 つ）写すかビルドし、stream より先に済ませる",
      (m := re.search(r'^OSS_NOW="([^"]*)"$', up, re.M)) and set(m.group(1).split()) == set(V["OSS_IMAGES"].split())
      and pos("tf_apply base/ecr") < pos('mirror_oss_images "$REG" "$PREFIX" $OSS_NOW') < pos("tf_apply pipeline/stream"))
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
_an = up[pos("tf_apply pipeline/analytics"):up.index("\n\n", pos("tf_apply pipeline/analytics"))]
check("oss/ops/up.sh は analytics に 4 つの格納先と、Spark・OpenSearch・VictoriaMetrics・Splunk のタグ、Splunk の台数と index、Spark のサブネット、device map を渡す",
      all(v in _an for v in ("-var 'sinks=[" + '"iceberg","opensearch","prometheus","splunk"' + "]'", '-var "spark_image_tag=$SPARK_TAG"',
                              '-var "opensearch_image_tag=$OSS_OPENSEARCH_TAG"', '-var "victoriametrics_image_tag=$OSS_VM_TAG"',
                              '-var "splunk_image_tag=$SPLUNK_TAG"', '-var "splunk_index=$SPLUNK_INDEX"', '-var "splunk_az_num=$SPLUNK_AZ_NUM"',
                              '-var "emr_az_num=$EMR_AZ_NUM"', '-var "device_map=$DEVICE_MAP"')))
check("oss/ops/up.sh は graph の前に SSM の /<接頭辞>/neo4j-password を作り、status の Lambda のレイヤー（graph/requirements-oss.txt を arm64 向けに）を入れ、Neo4j のタグと alert_history=true を渡す",
      0 <= pos('ensure_secret "/$PREFIX/neo4j-password" password') < pos("tf_apply pipeline/graph")
      and 0 <= pos("--target oss/terraform/pipeline/graph/.build/neo4j-layer/python") < pos("tf_apply pipeline/graph")
      and "--platform manylinux2014_aarch64" in up and "-r graph/requirements-oss.txt" in up
      and 'tf_apply pipeline/graph -var "neo4j_image_tag=$NEO4J_TAG" -var alert_history=true' in up)
check("oss/ops/up.sh のエンドポイントに、analytics と graph が使う s3tables（Spark の iceberg）・sns（Splunk のアラート）・kinesis-firehose（アラートの履歴）を入れる",
      (m := re.search(r'^ENDPOINTS="([^"]*)"$', up, re.M))
      and {"ssm", "ssmmessages", "ecr.api", "ecr.dkr", "logs", "s3tables", "sns", "kinesis-firehose"} == set(m.group(1).split()))
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

shutil.rmtree(TMP, ignore_errors=True)
print(f"通過 {passed} / 失敗 0")
