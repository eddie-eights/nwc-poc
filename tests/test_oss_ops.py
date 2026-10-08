"""cycle 005（マネージドを OSS に置き換えた環境を作る）の ops/oss/（up.sh / down.sh / oss-images.sh）の模擬テスト。
aws / terraform / docker は偽物（下の FAKE_*）に差し替え、AWS には触れない。
  1. 接頭辞 <owner>-nwc-oss が、どの OWNER の組み合わせでもマネージド版の <owner>-nwc-poc と同じにならない（resolve_name_prefix を bash で呼ぶ）
  2. ops/oss/down.sh は IaC/terraform/oss/ の state と、名前・タグが <owner>-nwc-oss のもの（SSM のパラメータは ManagedBy=ops/oss/up.sh だけ）しか消さない。
     いちばん紛らわしい OWNER=x-nwc-oss のマネージド版（接頭辞 x-nwc-oss-nwc-poc。OSS 版の x-nwc-oss と頭が同じ）を同じアカウントに並べて確かめる。
     逆向き（ops/down.sh が OSS 版に触らない）も見る。消したあとに残っているもの（Project タグ）を数えて出す
  3. stream が消えなかったときは Kafka の CLUSTER_ID を残し、残りのルートは消しにいき、終了コード 1 で消えなかったルートを出す
  4. イメージの名前と版が ops/oss/oss-images.sh（正）・docker/images/spark/ と docker/images/neo4j/ の Dockerfile・terraform の既定値・
     手元の docker/compose/compose.yaml・ECR のリポジトリに合い、
     mirror_oss_images が ECR に無いものだけを写す（spark / neo4j は app/spark/・app/neo4j/ を context に、docker/images/<名前>/Dockerfile でビルドする）
  5. ensure_secret の kafka-cluster-id（KRaft の CLUSTER_ID の形）と strong-password（OpenSearch の admin。値は画面に出さない）と、
     ops/oss/ が ops/ の関数を写さず読むこと、up.sh がマネージド版と同じ 9 つのルートを当て、Splunk のイメージと SSM をマネージド版と同じ関数で用意すること
  6. ops/oss/up.sh を偽物の道具で最後まで通す（3 回）。9 つのルートの apply の順番と渡す値、イメージと SSM のパラメータ、Web の部品、
     Neo4j が安定してからの同期、OpenSearch・VictoriaMetrics・Splunk が上がってからの Spark、ポートフォワードの案内と、
     打ち直し（イメージもパラメータも作り直さない）、サービスが安定しなかったとき（同期を飛ばし、Spark は起こし、警告を出す）。
     そのあと ops/oss/down.sh が、up.sh の作ったパラメータを全部消す
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
import base64, fcntl, fnmatch, json, os, re, signal, sys

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

def kv(item):  # "Key=Path,Option=Recursive,Values=/x/" → {"Key": "Path", ...}。「Values=a,b」の b（= が無い）は前の値に足す
    d, last = {}, None
    for part in item.split(","):
        k, eq, v = part.partition("=")
        if eq:
            d[k], last = v, k
        else:
            d[last] += "," + part
    return d

def save():
    with open(inv_path, "w", encoding="utf-8") as f:
        json.dump(inv, f)

def fail(msg, rc=254):
    print(msg, file=sys.stderr)
    sys.exit(rc)

def grafana_check(sent):  # 送ったのが ops/grafana_rules_check.py（9-2・ops/check-grafana.sh）か
    m = re.fullmatch(r"echo (\S+) \| base64 -d \| NAME_PREFIX=\S+ /usr/bin/python3\.13 -", json.loads(sent)["commands"][-1])
    return bool(m) and b"api/v1/rules" in base64.b64decode(m.group(1))

svc, op = (args + ["", ""])[:2]
query = opt("--query")
log()
if (svc, op) == ("sts", "get-caller-identity"):
    print("arn:aws:sts::123456789012:assumed-role/Admin/tester" if query == "Arn" else "123456789012")
# ---- ここから ops/oss/up.sh が打つもの（6.）。送ったコマンドは在庫の cmds に残し、結果を聞かれたら Success と答える
# （Grafana のルールの確かめは FAKE_GRAFANA=NG / UNKNOWN のとき Failed と答え、[標準出力, 標準エラー] を本物の --output text と同じくタブでつないで返す）
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
        print("Failed" if grafana_check(sent) and os.environ.get("FAKE_GRAFANA") in ("NG", "UNKNOWN") else "Success")
    elif query == "StandardOutputContent":
        print(f'lab=active containers={os.environ.get("FAKE_LAB_NODES", "0")}' if "containers=" in sent
              else "判定: OK（4 本とも評価のエラーなし）" if grafana_check(sent) else "")
    elif query == "[StandardOutputContent,StandardErrorContent]" and grafana_check(sent):
        print("判定: 未確認（300 秒待った。Grafana に届かない（URLError: timed out））\t" if os.environ.get("FAKE_GRAFANA") == "UNKNOWN"
              else "nwc-opensearch/trap: health=ok\n判定: NG（4 本のうち 1 本の評価がエラー: nwc-opensearch/trap）\t")
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
elif (svc, op) == ("ec2", "describe-vpcs"):  # 在庫の値は ID 1 つか、同じ名前の VPC が並ぶときは古い順の list
    f = [kv(x) for x in multi("--filters")]
    if [x["Name"] for x in f] != ["tag:Name"]:
        log({"unknown": "vpc filter"}); fail("unknown filter", 255)
    ids = inv["vpcs"].get(f[0]["Values"], [])
    ids = [ids] if isinstance(ids, str) else ids
    if query != "Vpcs[].VpcId":
        log({"unknown": "query " + str(query)}); fail("unknown query", 255)
    print("\t".join(ids))
elif (svc, op) == ("ec2", "describe-network-interfaces"):
    fs = {x["Name"]: x["Values"] for x in map(kv, multi("--filters"))}
    if "group-id" in fs:
        print("")
    elif set(fs) == {"vpc-id"} and query == "NetworkInterfaces[?InterfaceType=='agentic_ai'].NetworkInterfaceId":
        print("\t".join(e["id"] for e in inv["enis"] if e["vpc"] in fs["vpc-id"].split(",") and e["type"] == "agentic_ai"))
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
# ---- MSK の SCRAM の secret と KMS の鍵（cycle 012）。在庫の secrets は {名前: {"kms": 鍵の ARN}}（中身は持たない）、
# kms は {alias: 鍵の ARN}、kms_keys は {鍵の ARN: 状態}。FAKE_SM_FAIL なら delete-secret が落ちる。FAKE_KMS_DENY なら describe-key が権限で落ちる。
# 削除を予約された secret は "deleted" を持つ。create-secret / create-key が作ったものは中身とタグも持つ（値が外に出ていないかを見るため）
elif (svc, op) == ("secretsmanager", "create-secret"):
    src = opt("--cli-input-json")
    if not src or not src.startswith("file://"):
        log({"unknown": "create-secret without file://"}); fail("create-secret には file:// で渡す", 255)
    if os.environ.get("FAKE_SM_CREATE_SIGNAL"):  # 作っている最中に止められた。端末の Ctrl+C（kill -<sig> -<プロセスグループ>）と同じく、呼んだ shell と自分の両方に届く
        sig = getattr(signal, "SIG" + os.environ["FAKE_SM_CREATE_SIGNAL"])
        os.kill(os.getppid(), sig)
        signal.signal(sig, signal.SIG_DFL); os.kill(os.getpid(), sig)
    with open(src[len("file://"):], encoding="utf-8") as f:
        d = json.load(f)
    if d["Name"] in inv.setdefault("secrets", {}):
        fail("An error occurred (ResourceExistsException)")
    inv["secrets"][d["Name"]] = {"kms": d.get("KmsKeyId", "None"), "value": d["SecretString"], "desc": d.get("Description", ""),
                                 "tags": {t["Key"]: t["Value"] for t in d.get("Tags", [])}}
    save()
    print(json.dumps({"ARN": "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:" + d["Name"] + "-AbCdEf", "Name": d["Name"]}))
elif (svc, op) == ("secretsmanager", "restore-secret"):
    s = inv.get("secrets", {}).get(opt("--secret-id"))
    if s is None or "deleted" not in s:
        log({"unknown": "restore-secret " + str(opt("--secret-id"))}); fail("unknown restore-secret", 255)
    del s["deleted"]
    save()
elif (svc, op) == ("kms", "create-key"):
    arn = f'arn:aws:kms:ap-northeast-1:123456789012:key/k-new{len(inv.setdefault("kms_keys", {}))}'
    if query != "KeyMetadata.Arn":
        log({"unknown": "query " + str(query)}); fail("unknown query", 255)
    inv["kms_keys"][arn] = "Enabled"
    inv.setdefault("kms_made", {})[arn] = {"desc": opt("--description"), "tags": dict(t.replace("TagKey=", "").split(",TagValue=", 1) for t in multi("--tags"))}
    save()
    print(arn)
elif (svc, op) == ("kms", "create-alias"):
    if opt("--alias-name") in inv.setdefault("kms", {}):
        fail("An error occurred (AlreadyExistsException)")
    if opt("--target-key-id") not in inv.get("kms_keys", {}):
        log({"unknown": "create-alias " + str(opt("--target-key-id"))}); fail("unknown key", 255)
    inv["kms"][opt("--alias-name")] = opt("--target-key-id")
    save()
elif (svc, op) == ("kms", "cancel-key-deletion"):  # 取り消した鍵は Disabled になる（使うには enable-key が要る）
    if inv.get("kms_keys", {}).get(opt("--key-id")) != "PendingDeletion":
        fail("An error occurred (KMSInvalidStateException)")
    inv["kms_keys"][opt("--key-id")] = "Disabled"
    save()
elif (svc, op) == ("kms", "enable-key"):
    if inv.get("kms_keys", {}).get(opt("--key-id")) not in ("Enabled", "Disabled"):
        fail("An error occurred (KMSInvalidStateException)")
    inv["kms_keys"][opt("--key-id")] = "Enabled"
    save()
elif (svc, op) == ("secretsmanager", "describe-secret"):
    s = inv.get("secrets", {}).get(opt("--secret-id"))
    if s is None:
        fail("An error occurred (ResourceNotFoundException) when calling the DescribeSecret operation: Secrets Manager can't find the specified secret.")
    if query == "Name":
        print(opt("--secret-id"))
    elif query == "[KmsKeyId,DeletedDate]":
        print(f'{s["kms"]}\t{s.get("deleted", "None")}')
    else:
        log({"unknown": "query " + str(query)}); fail("unknown query", 255)
elif (svc, op) == ("secretsmanager", "delete-secret"):
    if "--force-delete-without-recovery" not in args:
        log({"unknown": "delete-secret without --force-delete-without-recovery"}); fail("unknown delete-secret", 255)
    if os.environ.get("FAKE_SM_FAIL"):
        fail("An error occurred (AccessDeniedException) when calling the DeleteSecret operation")
    if opt("--secret-id") not in inv.get("secrets", {}):
        fail("An error occurred (ResourceNotFoundException)")
    del inv["secrets"][opt("--secret-id")]
    save()
elif (svc, op) == ("kms", "describe-key"):
    if os.environ.get("FAKE_KMS_DENY"):
        fail("An error occurred (AccessDeniedException) when calling the DescribeKey operation")
    arn = inv.get("kms", {}).get(opt("--key-id"))
    if arn is None:
        fail(f'An error occurred (NotFoundException) when calling the DescribeKey operation: Alias {opt("--key-id")} is not found.')
    if query != "KeyMetadata.[Arn,KeyState]":
        log({"unknown": "query " + str(query)}); fail("unknown query", 255)
    print(f'{arn}\t{inv["kms_keys"][arn]}')
elif (svc, op) == ("kms", "schedule-key-deletion"):
    arn = opt("--key-id")
    if arn not in inv.get("kms_keys", {}) or opt("--pending-window-in-days") != "7":
        log({"unknown": "schedule-key-deletion " + str(arn)}); fail("unknown key", 255)
    if inv["kms_keys"][arn] == "PendingDeletion":
        fail("An error occurred (KMSInvalidStateException)")
    inv["kms_keys"][arn] = "PendingDeletion"
    save()
elif (svc, op) == ("kms", "delete-alias"):
    if opt("--alias-name") not in inv.get("kms", {}):
        fail("An error occurred (NotFoundException)")
    del inv["kms"][opt("--alias-name")]
    save()
else:
    log({"unknown": f"{svc} {op}"})
    fail(f"fake aws: unknown {svc} {op}", 255)
'''

# ---- 偽物の terraform。state にはいつも 1 つ載っている（state list）。state show aws_vpc.this は FAKE_TF_VPC（{-chdir の値: VPC の ID}）に
# 載っているルートだけ本物と同じ形で答え、ほかは本物と同じく rc=1。FAKE_TF_FAIL の -chdir の destroy だけ落ちる
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
if rest[:2] == ["state", "show"]:
    vpc = json.loads(os.environ.get("FAKE_TF_VPC", "{}")).get(chdir)
    if rest[2:] != ["aws_vpc.this"] or not vpc:
        print("Error: No instance found for the given address!", file=sys.stderr)
        sys.exit(1)
    # 本物（hashicorp/aws 6.x）の形。id の前に *_id の行が並ぶ（行頭の空白のあとが id のものだけ拾えているか）
    print("\n".join([
        "# aws_vpc.this:",
        'resource "aws_vpc" "this" {',
        f'    arn                                  = "arn:aws:ec2:ap-northeast-1:123456789012:vpc/{vpc}"',
        '    cidr_block                           = "10.0.0.0/16"',
        '    default_security_group_id            = "sg-0default"',
        '    dhcp_options_id                      = "dopt-0aaa"',
        "    enable_dns_hostnames                 = true",
        f'    id                                   = "{vpc}"',
        "    ipv6_association_id                  = null",
        '    owner_id                             = "123456789012"',
        "    tags                                 = {",
        '        "Name" = "x-vpc"',
        "    }",
        "}"]))
    sys.exit(0)
if verb == "apply":
    print("Apply complete! Resources: 1 added, 0 changed, 0 destroyed.")
    sys.exit(0)
if verb == "output" and os.environ.get("FAKE_TF_UP"):  # up.sh（6.）にだけ答える。値は「out-<ルートの末尾>-<output の名前>」
    name = rest[-1]
    if name in os.environ.get("FAKE_TF_EMPTY", "").split(","):  # 空の出力（Grafana を作っていない analytics の grafana_service_name）
        sys.exit(0)
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

# ops/oss/up.sh が apply する順番
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
KEY_MGD = "arn:aws:kms:ap-northeast-1:123456789012:key/k-mgd"  # alias/x-nwc-oss-nwc-poc-msk-scram の鍵
KEY_POC = "arn:aws:kms:ap-northeast-1:123456789012:key/k-poc"  # alias/x-nwc-poc-msk-scram の鍵
OSS_MANAGED_PARAMS = ["/x-nwc-oss/kafka/cluster-id", "/x-nwc-oss/kafka-ui/admin-password", "/x-nwc-oss/telegraf-dialin/gnmi-password",
                      "/x-nwc-oss/opensearch-password", "/x-nwc-oss/splunk/admin-password", "/x-nwc-oss/splunk/hec-token",
                      "/x-nwc-oss/neo4j-password", "/x-nwc-oss/nautobot/secret-key"]
def inventory():
    return {
        "ssm": {
            **{n: ssm_param("ops/oss/up.sh", "x-nwc-oss") for n in OSS_MANAGED_PARAMS},
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
        "secrets": {"AmazonMSK_x-nwc-oss-nwc-poc-collectors": {"kms": KEY_MGD}, "AmazonMSK_x-nwc-poc-collectors": {"kms": KEY_POC}},
        "kms": {"alias/x-nwc-oss-nwc-poc-msk-scram": KEY_MGD, "alias/x-nwc-poc-msk-scram": KEY_POC},
        "kms_keys": {KEY_MGD: "Enabled", KEY_POC: "Enabled"},
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

# down.sh と up.sh を打つ場所（リポジトリの写し）。ops/ と ops/oss/ のスクリプトと、9 つのルートの state と、
# up.sh が読む材料（イメージの元、Web とエージェントの部品、lab の定義）を置く。設定と秘密のファイルは写さない
REPO = os.path.join(TMP, "repo")
for d in ("ops", "ops/oss"):
    os.makedirs(os.path.join(REPO, d))
    for f in os.listdir(os.path.join(ROOT, d)):
        if f.endswith(".sh") or f in ("seed_graph.py", "roll_health.py", "grafana_rules_check.py"):
            shutil.copy(os.path.join(ROOT, d, f), os.path.join(REPO, d, f))
UP_DIRS = ("app/containerlab", "app/dashboard", "app/agentcore", "app/nautobot", "app/telegraf", "app/syslog-ng", "app/splunk", "app/grafana", "app/spark", "app/neo4j", "app/graph",
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

# ================================================================ 2. ops/oss/down.sh は OSS 版だけを消す
p, cs, inv = run_down("ops/oss/down.sh", "x")
out = p.stdout + p.stderr
check("ops/oss/down.sh（OWNER=x）: 終了コード 0", p.returncode == 0)
check("偽物の aws に知らないコマンドを打っていない", not [c for c in cs if c.get("unknown")])
check("OSS 版は MSK を持たないので、Secrets Manager にも KMS にも触らない（マネージド版の SCRAM の secret と鍵はそのまま。cycle 012）",
      not [c for c in cs if c["cmd"] == "aws" and c["args"][0] in ("secretsmanager", "kms")]
      and (inv["secrets"], inv["kms"], inv["kms_keys"]) == (inventory()["secrets"], inventory()["kms"], inventory()["kms_keys"]))
check("terraform は IaC/terraform/oss/ の下だけを -chdir で触り、IaC/terraform/aws-managed/（マネージド版の state）には入らない",
      tf_calls(cs) and all(chdir_of(c).startswith("IaC/terraform/oss/") for c in tf_calls(cs)))
check("IaC/terraform/oss/ の 9 つのルートを全部 destroy した", destroyed(cs) == {f"IaC/terraform/oss/{r}" for r in ROOTS})
check("destroy には -var owner=x を渡す",
      all("owner=x" in c["args"] for c in tf_calls(cs) if c["args"][1] == "destroy"))
check("terraform に渡す認証のプロファイル名は接頭辞から作る（x-nwc-oss-terraform）",
      {c["profile"] for c in tf_calls(cs)} == {"x-nwc-oss-terraform"})
check("terraform のログは ops/logs/tf-oss-*（マネージド版の tf-* を上書きしない）",
      logs_made() and all(f.startswith("tf-oss-") for f in logs_made()) and "tf-oss-pipeline-stream-destroy.log" in logs_made())
check("SSM: ops/oss/up.sh が作った 8 つ（Kafka の CLUSTER_ID、OpenSearch・Splunk・Neo4j・Nautobot のものを含む）を消し、手で入れたものとマネージド版のものは残す",
      set(inv["ssm"]) == ALL_PARAMS - set(OSS_MANAGED_PARAMS))
check("SSM: delete-parameter は /x-nwc-oss/ の下にしか打っていない",
      all(arg_after(a, "--name").startswith("/x-nwc-oss/") for a in aws_calls(cs, "ssm", "delete-parameter")))
check("SSM の絞り込みは、describe-parameters のどの呼び出しも Path=/x-nwc-oss/（末尾の / まで）と ManagedBy=ops/oss/up.sh の両方を付ける",
      aws_calls(cs, "ssm", "describe-parameters")
      and all(a[a.index("--parameter-filters") + 1:a.index("--parameter-filters") + 3]
              == ["Key=Path,Option=Recursive,Values=/x-nwc-oss/", "Key=tag:ManagedBy,Values=ops/oss/up.sh"]
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
check("VPC は state の aws_vpc.this から読めないときタグ Name=x-nwc-oss-vpc（完全一致）で全部（Vpcs[].VpcId）引き、Runtime の ENI は無いと出す",
      [(arg_after(a, "--filters"), arg_after(a, "--query")) for a in aws_calls(cs, "ec2", "describe-vpcs")]
      == [("Name=tag:Name,Values=x-nwc-oss-vpc", "Vpcs[].VpcId")]
      and "Runtime の ENI を探す VPC: vpc-0055（タグ Name=x-nwc-oss-vpc）" in out
      and "Runtime の ENI の確認: VPC=vpc-0055 残り=なし" in out)
check("Runtime のロググループ: x_nwc_oss_agent- のものだけ消し、x_nwc_oss_nwc_poc_agent- と x_nwc_poc_agent- は残す",
      set(inv["log_groups"]) == ALL_LOG_GROUPS - {"/aws/bedrock-agentcore/runtimes/x_nwc_oss_agent-AAA-DEFAULT"})
check("残り: Project=x-nwc-oss（完全一致）だけを数え、ARN を並べて「残り: 2 件」と出す（マネージド版のものは出さない）",
      "残り: 2 件（Project=x-nwc-oss のタグ）" in out
      and "arn:aws:ecs:ap-northeast-1:123456789012:cluster/x-nwc-oss-left" in out
      and "parameter/x-nwc-oss/manual/note" in out and "x-nwc-oss-nwc-poc" not in out and "vpc/vpc-0aaa" not in out)
check("残りの一覧のあとは「この一覧では消えたかを決めない」と出し、「全部消えている」とは言わない。base/core を全部消したので VPC の案内は出さない",
      "この一覧では消えたかを決めない" in out and "全部消えている" not in out and "使い回す" not in out)
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
def aws_ops(cs, svc):  # svc に打った操作を順に
    return [c["args"][1] for c in cs if c["cmd"] == "aws" and c["args"][0] == svc]

def aws_pos(cs, svc, op):
    return next(i for i, c in enumerate(cs) if c["cmd"] == "aws" and c["args"][:2] == [svc, op])

check("マネージド版: MSK の SCRAM の secret は AmazonMSK_x-nwc-oss-nwc-poc-collectors だけを --force-delete-without-recovery で消し、"
      "x-nwc-poc のものは残す（cycle 012）",
      set(inv["secrets"]) == {"AmazonMSK_x-nwc-poc-collectors"}
      and [arg_after(a, "--secret-id") for a in aws_calls(cs, "secretsmanager", "delete-secret")] == ["AmazonMSK_x-nwc-oss-nwc-poc-collectors"]
      and "AmazonMSK_x-nwc-oss-nwc-poc-collectors: 消した" in out)
check("マネージド版: KMS は alias/x-nwc-oss-nwc-poc-msk-scram の鍵だけ、secret を消したあとに 7 日の削除を予約し、それから alias を外す"
      "（x-nwc-poc の鍵と alias は残す）",
      inv["kms"] == {"alias/x-nwc-poc-msk-scram": KEY_POC} and inv["kms_keys"] == {KEY_MGD: "PendingDeletion", KEY_POC: "Enabled"}
      and aws_ops(cs, "kms") == ["describe-key", "schedule-key-deletion", "delete-alias"]
      and aws_pos(cs, "secretsmanager", "delete-secret") < aws_pos(cs, "kms", "schedule-key-deletion") < aws_pos(cs, "kms", "delete-alias")
      and "alias/x-nwc-oss-nwc-poc-msk-scram: 鍵の削除を予約し（7 日後に消える。待つあいだは課金されない）、alias を外した" in out)
check("マネージド版: secret の中身を読むコマンド（get-secret-value）は打たない", not aws_calls(cs, "secretsmanager", "get-secret-value"))
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
check("ops/down.sh（OWNER=x）: SCRAM の secret と鍵も x-nwc-poc のものだけ消す（x-nwc-oss-nwc-poc のものは残す）",
      set(inv["secrets"]) == {"AmazonMSK_x-nwc-oss-nwc-poc-collectors"}
      and inv["kms"] == {"alias/x-nwc-oss-nwc-poc-msk-scram": KEY_MGD} and inv["kms_keys"] == {KEY_MGD: "Enabled", KEY_POC: "PendingDeletion"})

# ---- 5-3. の分かれ道（cycle 012）
p, cs, inv = run_down("ops/down.sh", "x-nwc-oss", {"FAKE_TF_FAIL": "IaC/terraform/aws-managed/pipeline/stream"})
out = p.stdout + p.stderr
check("ops/down.sh: pipeline/stream が消えなかったら SCRAM の secret と鍵は両方残す（msk.tf の data source が次の destroy でも引く）",
      p.returncode != 0 and not [c for c in cs if c.get("unknown")]
      and not [c for c in cs if c["cmd"] == "aws" and c["args"][0] in ("secretsmanager", "kms")]
      and (inv["secrets"], inv["kms"], inv["kms_keys"]) == (inventory()["secrets"], inventory()["kms"], inventory()["kms_keys"])
      and "AmazonMSK_x-nwc-oss-nwc-poc-collectors と alias/x-nwc-oss-nwc-poc-msk-scram: 残す（IaC/terraform/aws-managed/pipeline/stream が消えなかったので" in out)

p, cs, inv = run_down("ops/down.sh", "x-nwc-oss", {"FAKE_SM_FAIL": "1"})
out = p.stdout + p.stderr
check("ops/down.sh: secret を消せなかったら鍵も残す（消すと secret を復号できなくなる）",
      not [c for c in cs if c.get("unknown")] and not aws_calls(cs, "kms", "schedule-key-deletion") and not aws_calls(cs, "kms", "delete-alias")
      and inv["kms_keys"][KEY_MGD] == "Enabled" and "AmazonMSK_x-nwc-oss-nwc-poc-collectors: 消せなかった（上のエラー）。鍵も残す" in out)

pending = inventory()
del pending["secrets"]["AmazonMSK_x-nwc-oss-nwc-poc-collectors"]
pending["kms_keys"][KEY_MGD] = "PendingDeletion"
p, cs, inv = run_down("ops/down.sh", "x-nwc-oss", inv=pending)
out = p.stdout + p.stderr
check("ops/down.sh: secret が無く、鍵が削除の予約中（前の down.sh が alias を外せなかった）なら、予約し直さずに alias だけ外す",
      p.returncode == 0 and not [c for c in cs if c.get("unknown")]
      and "AmazonMSK_x-nwc-oss-nwc-poc-collectors: 無い" in out
      and aws_ops(cs, "kms") == ["describe-key", "delete-alias"] and inv["kms"] == {"alias/x-nwc-poc-msk-scram": KEY_POC})

# ================================================================ 2''. 同じ名前の VPC が 2 つあるとき（2026-10-08 の OSS 版の検証の不具合 1）
# 古い方（前の打ち直しの残り。ENI は無い）が先に返り、今回の VPC に Runtime の ENI が残っている。1 つ目だけ見ると全部消しにいき、
# SG の削除待ちを繰り返して落ちる。どちらの down.sh も destroy_base_core（ops/down-common.sh）を通る
def runtime_eni(vpc):
    return {"id": f"eni-runtime-{vpc}", "desc": "agentic", "status": "in-use", "type": "agentic_ai", "vpc": vpc}

def twin_vpcs(name, new, eni_in):  # name の VPC を [古い, 新しい] の 2 つにし、Runtime の ENI を eni_in の VPC の 1 つだけにする
    inv = inventory()
    inv["vpcs"][name] = ["vpc-0old", new]
    inv["enis"] = [e for e in inv["enis"] if e["type"] != "agentic_ai"] + [runtime_eni(eni_in)]
    return inv

def base_core_destroys(cs, tf_dir):
    return [c["args"] for c in tf_calls(cs) if chdir_of(c) == f"{tf_dir}/base/core" and c["args"][1] == "destroy"]

def kept_base_core(cs, tf_dir):  # base/core は -target で ENI に関わらないものだけ消し、全部は消しにいかない
    ds = base_core_destroys(cs, tf_dir)
    return bool(ds) and all(any(x.startswith("-target=") for x in a) for a in ds)

for script, owner, prefix, tf_dir, new in (("ops/oss/down.sh", "x", "x-nwc-oss", "IaC/terraform/oss", "vpc-0055"),
                                           ("ops/down.sh", "x-nwc-oss", "x-nwc-oss-nwc-poc", "IaC/terraform/aws-managed", "vpc-0aaa")):
    p, cs, inv = run_down(script, owner, inv=twin_vpcs(f"{prefix}-vpc", new, new))
    out = p.stdout + p.stderr
    check(f"{script}（VPC が 2 つ、state から読めない）: タグで当たった 2 つを両方見て、新しい方の Runtime の ENI で base/core を残す",
          p.returncode == 0 and not [c for c in cs if c.get("unknown")]
          and f"Runtime の ENI を探す VPC: vpc-0old,{new}（タグ Name={prefix}-vpc）" in out
          and [arg_after(a, "--filters") for a in aws_calls(cs, "ec2", "describe-network-interfaces") if "Name=vpc-id" in arg_after(a, "--filters")]
          == [f"Name=vpc-id,Values=vpc-0old,{new}"]
          and f"Runtime の ENI が残っている: eni-runtime-{new}" in out and kept_base_core(cs, tf_dir))
    up = script.replace("down.sh", "up.sh")
    check(f"{script}（Runtime の ENI で base/core を残した）: 最後の案内は「そのままでよい。次の {up} が使い回す」と、"
          "タグの一覧では消えたかを決めないこと（「数時間おいて打ち直す」「全部消えている」は出さない。2026-10-08 の AWS 検証）",
          f"そのままでよい。次の {up} が使い回す" in out and "この一覧では消えたかを決めない" in out
          and "数時間おいて" not in out and "全部消えている" not in out)

    p, cs, inv = run_down(script, owner, {"FAKE_TF_VPC": json.dumps({f"{tf_dir}/base/core": new})},
                          inv=twin_vpcs(f"{prefix}-vpc", new, new))
    out = p.stdout + p.stderr
    check(f"{script}（VPC が 2 つ、state に aws_vpc.this がある）: state の ID だけを見て（タグでは引かない）base/core を残す",
          p.returncode == 0 and not aws_calls(cs, "ec2", "describe-vpcs")
          and f"Runtime の ENI を探す VPC: {new}（state の aws_vpc.this）" in out
          and f"Runtime の ENI が残っている: eni-runtime-{new}" in out and kept_base_core(cs, tf_dir))

# state の VPC に ENI が無ければ、同じ名前の別の VPC（前の打ち直しの残り）に ENI があっても全部消す（state が優先）
p, cs, inv = run_down("ops/oss/down.sh", "x", {"FAKE_TF_VPC": json.dumps({"IaC/terraform/oss/base/core": "vpc-0055"})},
                      inv=twin_vpcs("x-nwc-oss-vpc", "vpc-0055", "vpc-0old"))
out = p.stdout + p.stderr
check("ops/oss/down.sh（state の VPC に ENI が無く、同じ名前の古い VPC にだけある）: state を信じて base/core を全部消す",
      p.returncode == 0 and not aws_calls(cs, "ec2", "describe-vpcs")
      and "Runtime の ENI の確認: VPC=vpc-0055 残り=なし" in out
      and base_core_destroys(cs, "IaC/terraform/oss") and not kept_base_core(cs, "IaC/terraform/oss"))

# ================================================================ 3. stream が消えなかったとき
p, cs, inv = run_down("ops/oss/down.sh", "x", {"FAKE_TF_FAIL": "IaC/terraform/oss/pipeline/stream", "FAKE_TAG_FAIL": "1", "KEEP_ECR": "1"})
out = p.stdout + p.stderr
check("stream が消えなかった: 終了コード 1 で「NG: 消えなかったルート: pipeline/stream」と出す",
      p.returncode == 1 and "NG: 消えなかったルート: pipeline/stream（" in out)
check("stream が消えなかった: 後ろのルート（lab / agent / base/core）は消しにいく",
      {"IaC/terraform/oss/pipeline/lab", "IaC/terraform/oss/agent", "IaC/terraform/oss/base/core"} <= destroyed(cs))
check("stream が消えなかった: Kafka の CLUSTER_ID は残し（次の down.sh で消す）、ほかの ops/oss/up.sh のパラメータは消す",
      "/x-nwc-oss/kafka/cluster-id: 残す" in out
      and set(inv["ssm"]) == ALL_PARAMS - set(OSS_MANAGED_PARAMS) | {"/x-nwc-oss/kafka/cluster-id"})
check("KEEP_ECR=1 なら IaC/terraform/oss/base/ecr は destroy しない", "IaC/terraform/oss/base/ecr" not in destroyed(cs))
check("残りを数えられなかった（タグの API のエラー）ときは、0 件と言わずに「数えられなかった」と出す",
      "残り: 数えられなかった（上のエラー）" in out and "残り: 0 件" not in out)

p, cs, inv = run_down("ops/oss/down.sh", "x", {"FAKE_TF_FAIL": "IaC/terraform/oss/base/core"})
check("base/core（Kafka のデータの EFS）が消えなかったときも Kafka の CLUSTER_ID は残す",
      p.returncode == 1 and "/x-nwc-oss/kafka/cluster-id" in inv["ssm"] and "/x-nwc-oss/kafka-ui/admin-password" not in inv["ssm"])

p, cs, inv = run_down("ops/oss/down.sh", "x", {"FAKE_TF_FAIL": "IaC/terraform/oss/pipeline/nautobot"})
check("nautobot が消えなかった: 終了コード 1 で、/x-nwc-oss/nautobot/ の下（DB に入っている値と合わせるもの）だけ残し、ほかは Kafka の CLUSTER_ID も消す",
      p.returncode == 1 and "NG: 消えなかったルート: pipeline/nautobot（" in p.stdout + p.stderr
      and set(inv["ssm"]) == ALL_PARAMS - set(OSS_MANAGED_PARAMS) | {"/x-nwc-oss/nautobot/secret-key"})
check("nautobot が消えなかった: 前後のルート（analytics / graph / stream / base/core）は消しにいく",
      {f"IaC/terraform/oss/{r}" for r in ROOTS} == destroyed(cs))

# ================================================================ 4. イメージの名前と版
img_sh = read("ops/oss/oss-images.sh")
def tf_default_early(path, var):
    m = re.search(rf'variable\s+"{var}"\s*\{{[^}}]*?default\s*=\s*"([^"]+)"', read(path), re.S)
    return m and m.group(1)
V = {k: q or b for k, q, b in re.findall(r'^(OSS_[A-Z0-9_]+)=(?:"([^"]*)"|(\S*))', img_sh, re.M)}
compose_images = set(re.findall(r"^\s*image:\s*(\S+)", read("docker/compose/compose.yaml"), re.M))
check("oss-images.sh の公開イメージのうち手元の compose にもあるもの（kafka / kafka-ui / opensearch）は docker/compose/compose.yaml の image: と同じ版",
      {f'{V["OSS_KAFKA_IMAGE"]}:{V["OSS_KAFKA_TAG"]}', f'{V["OSS_KAFKA_UI_IMAGE"]}:{V["OSS_KAFKA_UI_TAG"]}',
       f'{V["OSS_OPENSEARCH_IMAGE"]}:{V["OSS_OPENSEARCH_TAG"]}'} <= compose_images)
check("OpenSearch と VictoriaMetrics の版は IaC/terraform/oss/pipeline/analytics/ の opensearch_image_tag・victoriametrics_image_tag の既定値と同じ",
      tf_default_early("IaC/terraform/oss/pipeline/analytics/opensearch.tf", "opensearch_image_tag") == V["OSS_OPENSEARCH_TAG"]
      and tf_default_early("IaC/terraform/oss/pipeline/analytics/victoriametrics.tf", "victoriametrics_image_tag") == V["OSS_VM_TAG"])
check("ビルドする spark / neo4j の版は docker/images/spark/・docker/images/neo4j/ の Dockerfile の ARG の既定値と同じで、FROM はその ARG を使う",
      re.search(rf'^ARG SPARK_VERSION={re.escape(V["OSS_SPARK_VERSION"])}\s*$', read("docker/images/spark/Dockerfile"), re.M)
      and re.search(r'^FROM apache/spark:\$\{SPARK_VERSION\}-java17-python3\s*$', read("docker/images/spark/Dockerfile"), re.M)
      and re.search(rf'^ARG NEO4J_VERSION={re.escape(V["OSS_NEO4J_VERSION"])}\s*$', read("docker/images/neo4j/Dockerfile"), re.M)
      and re.search(r'^FROM neo4j:\$\{NEO4J_VERSION\}-community\s*$', read("docker/images/neo4j/Dockerfile"), re.M))
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
. ops/lab-common.sh; . ops/oss/oss-images.sh
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
. ops/lab-common.sh; . ops/oss/oss-images.sh
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
p = subprocess.run(["bash", "-c", rf'REGION=ap-northeast-1; PY=(python3); . ops/lab-common.sh; . ops/oss/oss-images.sh; mirror_oss_images {REG} x-nwc-oss bogus'],
                   cwd=ROOT, env=fake_env(), capture_output=True, text=True, timeout=60)
check("mirror_oss_images: 知らない名前は 1 で止まる", p.returncode == 1 and "知らないイメージ: bogus" in p.stderr)

# ================================================================ 5. CLUSTER_ID と、ops/oss/ の作り
reset(inventory())
SECRET_SH = r'''
REGION=ap-northeast-1; PY=(python3); PREFIX=x-nwc-oss; OWNER=x
. ops/common.sh; . ops/up-common.sh; OPS_DIR=ops/oss
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
check("ensure_secret kafka-cluster-id: タグは ManagedBy=ops/oss/up.sh・Project=x-nwc-oss・owner=x（ops/oss/down.sh が消せる）",
      made.get("tags") == {"ManagedBy": "ops/oss/up.sh", "Project": "x-nwc-oss", "owner": "x"})
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
. ops/common.sh; . ops/up-common.sh; OPS_DIR=ops/oss
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
check("ensure_secret strong-password: 値は画面にもコマンドラインにも出さず、タグは ManagedBy=ops/oss/up.sh",
      all(x not in p.stdout + p.stderr and not any(x in " ".join(c["args"]) for c in calls()) for x in vals)
      and all(m.get("tags", {}).get("ManagedBy") == "ops/oss/up.sh" for m in made.values()))

# ---- MSK の SCRAM の鍵と secret（cycle 012。マネージド版の ops/up.sh が stream の apply の前に呼ぶ ops/up-common.sh の関数）
SCRAM_SH = r'''
REGION=ap-northeast-1; PY=(python3); PREFIX=x-nwc-poc; OWNER=x
. ops/common.sh; . ops/up-common.sh; OPS_DIR=ops
for i in 1 2; do ensure_msk_scram_key || exit 1; ensure_msk_scram_secret || exit 1; echo "KEY=$MSK_SCRAM_KEY_ARN"; done
'''
def run_scram(inv, extra=None, sh=SCRAM_SH):
    reset(inv)
    p = subprocess.run(["bash", "-c", sh], cwd=ROOT, env=fake_env(extra), capture_output=True, text=True, timeout=60)
    with open(INV, encoding="utf-8") as f:
        return p, calls(), json.load(f)

def no_scram():  # 在庫から x-nwc-poc の secret と鍵を除く（何も無いところから作る）
    i = inventory()
    del i["secrets"]["AmazonMSK_x-nwc-poc-collectors"]; del i["kms"]["alias/x-nwc-poc-msk-scram"]; del i["kms_keys"][KEY_POC]
    return i

p, cs, inv = run_scram(no_scram())
out = p.stdout + p.stderr
_new = [a for a in inv["kms_keys"] if "/k-new" in a] or ["（作られていない）"]
_made = inv["secrets"].get("AmazonMSK_x-nwc-poc-collectors", {})
_val = json.loads(_made.get("value", "{}"))
check("ensure_msk_scram_key: alias/x-nwc-poc-msk-scram が無ければ鍵を 1 回だけ作って alias を付け、2 回目は作り直さない（MSK_SCRAM_KEY_ARN はその鍵）",
      p.returncode == 0 and not [c for c in cs if c.get("unknown")] and len(_new) == 1
      and inv["kms"]["alias/x-nwc-poc-msk-scram"] == _new[0] and len(aws_calls(cs, "kms", "create-key")) == 1 and len(aws_calls(cs, "kms", "create-alias")) == 1
      and p.stdout.count(f"KEY={_new[0]}\n") == 2 and "alias/x-nwc-poc-msk-scram はある（作り直さない）" in p.stdout
      and inv.get("kms_made", {}).get(_new[0], {}).get("tags") == {"ManagedBy": "ops/up.sh", "Project": "x-nwc-poc", "owner": "x"})
check("ensure_msk_scram_secret: AmazonMSK_x-nwc-poc-collectors を作った鍵（MSK_SCRAM_KEY_ARN）で暗号化して 1 回だけ作り、2 回目は作り直さない",
      _made.get("kms") == _new[0] and len(aws_calls(cs, "secretsmanager", "create-secret")) == 1
      and "AmazonMSK_x-nwc-poc-collectors はある（作り直さない）" in p.stdout
      and _made.get("tags") == {"ManagedBy": "ops/up.sh", "Project": "x-nwc-poc", "owner": "x"})
check("ensure_msk_scram_secret: 中身は JSON の username / password（MSK の SCRAM の形）で、パスワードは 32 文字の乱数",
      set(_val) == {"username", "password"} and _val["username"] == "collectors" and re.fullmatch(r"[A-Za-z0-9_-]{32}", _val["password"]) is not None)
check("ensure_msk_scram_secret: パスワードは画面にもコマンドラインにも出さず、値を書いた一時ファイルを残さず、中身を読むコマンド（get-secret-value）は打たない",
      _val.get("password") and _val["password"] not in out and not any(_val["password"] in " ".join(c["args"]) for c in cs)
      and not [f for f in os.listdir(TMP) if f.startswith("nwc-secret.")] and not aws_calls(cs, "secretsmanager", "get-secret-value"))
# create-secret の最中に止められても、値を書いた一時ファイルは ops/up.sh の EXIT の trap（on_exit）が消す。on_exit は ops/up.sh のものをそのまま使う
_on_exit = re.search(r"^on_exit\(\) \{.*?^\}\ntrap on_exit EXIT\n", read("ops/up.sh"), re.S | re.M).group(0)
for _sig in ("INT", "TERM"):
    for _f in [f for f in os.listdir(TMP) if f.startswith("nwc-secret.")]:
        os.remove(os.path.join(TMP, _f))
    _p, _cs, _inv = run_scram(no_scram(), {"FAKE_SM_CREATE_SIGNAL": _sig},
                              SCRAM_SH.replace("OPS_DIR=ops\n", "OPS_DIR=ops\nGRAPH_PID=\"\"; NAUTOBOT_CTX=\"\"\n" + _on_exit, 1))
    check(f"ensure_msk_scram_secret: create-secret の最中に SIG{_sig} で止まっても、値を書いた一時ファイルを残さない（ops/up.sh の on_exit が消す）",
          _p.returncode < 0 and len(aws_calls(_cs, "secretsmanager", "create-secret")) == 1 and "AmazonMSK_x-nwc-poc-collectors" not in _inv["secrets"]
          and not [f for f in os.listdir(TMP) if f.startswith("nwc-secret.")])
check("ensure_msk_scram_key / secret: x-nwc-oss-nwc-poc の secret と鍵には触らない",
      inv["secrets"]["AmazonMSK_x-nwc-oss-nwc-poc-collectors"] == inventory()["secrets"]["AmazonMSK_x-nwc-oss-nwc-poc-collectors"]
      and inv["kms"]["alias/x-nwc-oss-nwc-poc-msk-scram"] == KEY_MGD and inv["kms_keys"][KEY_MGD] == "Enabled")

# 直前の down.sh が鍵の削除を予約し、secret の削除も予約されている（手で消したとき）: 予約を取り消して有効に戻し、secret を戻す
_pend = inventory()
_pend["kms_keys"][KEY_POC] = "PendingDeletion"
_pend["secrets"]["AmazonMSK_x-nwc-poc-collectors"]["deleted"] = "2026-10-08T00:00:00+09:00"
p, cs, inv = run_scram(_pend)
check("ensure_msk_scram_key: 鍵が削除の予約中なら取り消して有効に戻す（cancel-key-deletion のあと enable-key）。secret の削除の予約も戻し、どちらも作り直さない",
      p.returncode == 0 and aws_ops(cs, "kms") == ["describe-key", "cancel-key-deletion", "enable-key", "describe-key"]
      and inv["kms_keys"][KEY_POC] == "Enabled" and "deleted" not in inv["secrets"]["AmazonMSK_x-nwc-poc-collectors"]
      and len(aws_calls(cs, "secretsmanager", "restore-secret")) == 1 and not aws_calls(cs, "secretsmanager", "create-secret")
      and "alias/x-nwc-poc-msk-scram は PendingDeletion だったので有効に戻した" in p.stdout and f"KEY={KEY_POC}" in p.stdout)

# secret が別の鍵（作り直す前の鍵や aws/secretsmanager）で暗号化されている: MSK が受けないので止める
_other = inventory()
_other["secrets"]["AmazonMSK_x-nwc-poc-collectors"]["kms"] = KEY_MGD
p, cs, inv = run_scram(_other)
check("ensure_msk_scram_secret: secret の暗号化の鍵が alias/x-nwc-poc-msk-scram の鍵と違えば、作り直さずに止め、消し方を出す",
      p.returncode != 0 and not aws_calls(cs, "secretsmanager", "create-secret") and "KEY=" not in p.stdout
      and "AmazonMSK_x-nwc-poc-collectors の暗号化の鍵（" + KEY_MGD + "）が alias/x-nwc-poc-msk-scram の鍵と違う" in p.stderr
      and "--force-delete-without-recovery" in p.stderr)
_other["secrets"]["AmazonMSK_x-nwc-poc-collectors"]["kms"] = "None"   # aws/secretsmanager で暗号化した secret は KmsKeyId が無い
p, cs, inv = run_scram(_other)
check("ensure_msk_scram_secret: aws/secretsmanager の鍵（KmsKeyId が無い）で暗号化した secret でも止める", p.returncode != 0 and "の暗号化の鍵（None）が" in p.stderr)
for _form in (KEY_POC.rsplit("/", 1)[1], "alias/x-nwc-poc-msk-scram", "arn:aws:kms:ap-northeast-1:123456789012:alias/x-nwc-poc-msk-scram"):
    _other["secrets"]["AmazonMSK_x-nwc-poc-collectors"]["kms"] = _form
    p, cs, inv = run_scram(_other)
    check(f"ensure_msk_scram_secret: KmsKeyId が同じ鍵の別の書き方（{_form}）なら通す", p.returncode == 0 and not aws_calls(cs, "secretsmanager", "create-secret"))

# 鍵を確かめられない（権限が無いなど）: 新しい鍵を作らずに止める（作ると alias が 2 つ目の鍵を指せず、次の apply が別の鍵を引く）
p, cs, inv = run_scram(no_scram(), {"FAKE_KMS_DENY": "1"})
check("ensure_msk_scram_key: describe-key が NotFound 以外で落ちたら、鍵も secret も作らずに止める",
      p.returncode != 0 and not aws_calls(cs, "kms", "create-key") and not aws_calls(cs, "secretsmanager", "create-secret")
      and "alias/x-nwc-poc-msk-scram を確かめられない: An error occurred (AccessDeniedException)" in p.stderr)

up, down = read("ops/oss/up.sh"), read("ops/oss/down.sh")
def funcs(text):
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", text, re.M))
ops_funcs = set().union(*(funcs(read(f"ops/{f}")) for f in os.listdir(os.path.join(ROOT, "ops")) if f.endswith(".sh")))
check("ops/oss/up.sh と down.sh は関数を定義しない（ops/ の共通の関数を読む）", not funcs(up) and not funcs(down))
check("ops/oss/oss-images.sh は ops/ にある関数を書き直していない（写しを作らない）", not funcs(img_sh) & ops_funcs)
check("ops/oss/up.sh は ops/common.sh・ops/up-common.sh・ops/lab-common.sh・ops/deploy-env.sh を読む",
      all(s in up for s in (". ops/common.sh", ". ops/up-common.sh", '/../lab-common.sh"', '/../deploy-env.sh"')))
check("ops/oss/down.sh は ops/common.sh・ops/down-common.sh・ops/deploy-env.sh を読む",
      all(s in down for s in (". ops/common.sh", ". ops/down-common.sh", '/../deploy-env.sh"')))
check("マネージド版の ops/up.sh と ops/down.sh も同じ共通のファイルを読む（写しが 2 つにならない）",
      all(s in read("ops/up.sh") for s in (". ops/common.sh", ". ops/up-common.sh"))
      and all(s in read("ops/down.sh") for s in (". ops/common.sh", ". ops/down-common.sh")))
check("ops/oss/ の 2 つは resolve_name_prefix nwc-oss で接頭辞を作り、TF_DIR=IaC/terraform/oss・OPS_DIR=ops/oss・TF_LOG_NAME=tf-oss にする",
      all("resolve_name_prefix nwc-oss" in t and re.search(r"^TF_DIR=IaC/terraform/oss\b", t, re.M)
          and re.search(r"^OPS_DIR=ops/oss\b", t, re.M) and re.search(r"^TF_LOG_NAME=tf-oss\b", t, re.M) for t in (up, down)))
pos = lambda s: up.find(s)
_applies = [pos(f"tf_apply {r}") for r in ROOTS]
check("ops/oss/up.sh はルートを base/ecr → base/core → agent → pipeline/lab → pipeline/stream → pipeline/graph → pipeline/nautobot → pipeline/analytics → workflow の順に当て、ROOTS もその 9 つ（マネージド版と同じ範囲）",
      _applies[0] >= 0 and _applies == sorted(_applies) and len(set(_applies)) == 9
      and re.search(r'^ROOTS="' + " ".join(ROOTS) + r'"$', up, re.M))
check("ops/oss/down.sh は up.sh の 9 つのルートを全部消す（base/core は destroy_base_core、agent は destroy_agent）",
      all(re.search(rf"^\s*destroy_(lambda_)?root {re.escape(r)}\b", down, re.M) for r in ROOTS if r not in ("base/core", "agent"))
      and re.search(r"^destroy_agent$", down, re.M) and re.search(r"^destroy_base_core$", down, re.M))
check("ops/oss/up.sh は OSS_NOW を持たず、oss-images.sh の OSS_IMAGES（7 つ）をそのまま使って、ECR ができてから OSS のイメージを写すかビルドし、stream より先に済ませる",
      "OSS_NOW" not in up and len(V["OSS_IMAGES"].split()) == 7 and re.search(r"^for name in \$OSS_IMAGES; do$", up, re.M) is not None
      and 0 <= pos("tf_apply base/ecr") < pos('mirror_oss_images "$REG" "$PREFIX" $OSS_IMAGES') < pos("tf_apply pipeline/stream"))
check("ops/oss/up.sh の Splunk のイメージはマネージド版と同じ関数（ops/up-common.sh の splunk_image_check / build_splunk）で、docker login のあと、analytics より前",
      "splunk_image_check" in up and "$NEED_SPLUNK" in up
      and pos("aws ecr get-login-password") < pos("    build_splunk") < pos("tf_apply pipeline/analytics")
      and "splunk_image_check" in read("ops/up.sh") and "build_splunk" in read("ops/up.sh")
      and "docker buildx build" not in up)
check("ops/oss/up.sh は analytics の前に Splunk の SSM（ensure_splunk_secrets。マネージド版と同じ関数）と OpenSearch の admin（strong-password）と s3tablescatalog を用意する",
      0 <= pos('ensure_secret "/$PREFIX/opensearch-password" strong-password') < pos("tf_apply pipeline/analytics")
      and 0 <= pos('ensure_splunk_secrets "$SPLUNK_AZ_NUM"') < pos("tf_apply pipeline/analytics")
      and 0 <= pos("ensure_s3tables_catalog") < pos("tf_apply pipeline/analytics")
      and 'ensure_splunk_secrets "$SPLUNK_AZ_NUM"' in read("ops/up.sh"))
check("ops/oss/up.sh の Grafana のイメージはマネージド版と同じ関数（ops/up-common.sh の build_grafana。版は GRAFANA_VERSION、タグは dir_tag）で、docker login のあと、analytics より前",
      'GRAFANA_TAG=$(dir_tag "$GRAFANA_VERSION" app/grafana docker/images/grafana/Dockerfile)' in up and 'ecr_has "$PREFIX-grafana" "$GRAFANA_TAG"' in up
      and pos("aws ecr get-login-password") < pos("    build_grafana") < pos("tf_apply pipeline/analytics")
      and "    build_grafana" in read("ops/up.sh") and "GRAFANA_VERSION=" not in read("ops/up.sh") and "GRAFANA_VERSION=" not in up
      and re.search(r"^GRAFANA_VERSION=(\S+)", read("ops/up-common.sh"), re.M).group(1)
      == re.search(r"^ARG GRAFANA_VERSION=(\S+)", read("docker/images/grafana/Dockerfile"), re.M).group(1)
      and 'docker buildx build --platform linux/arm64 --build-arg "GRAFANA_VERSION=$GRAFANA_VERSION" -t "$REG/$PREFIX-grafana:$GRAFANA_TAG" --push -f docker/images/grafana/Dockerfile app/grafana/'
      in read("ops/up-common.sh"))
# analytics と stream の -var は配列（ANALYTICS_VARS / STREAM_VARS）にまとめ、1 台ずつの入れ替え（roll_nodes）と apply に同じものを渡す
_an = up[pos("ANALYTICS_VARS=("):pos('tf_apply pipeline/analytics "${ANALYTICS_VARS[@]}"')]
_st = up[pos("STREAM_VARS=("):pos('tf_apply pipeline/stream "${STREAM_VARS[@]}"')]
check("ops/oss/up.sh は stream と analytics の apply の前に roll_nodes（ops/oss/roll-nodes.sh）を同じ -var の配列で打つ",
      ". ops/oss/roll-nodes.sh" in up and 0 <= pos(". ops/up-common.sh") < pos(". ops/oss/roll-nodes.sh")
      and 0 <= pos("STREAM_VARS=(") < pos('roll_nodes kafka pipeline/stream "${STREAM_VARS[@]}"\ntf_apply pipeline/stream "${STREAM_VARS[@]}"\n')
      and 0 <= pos("ANALYTICS_VARS=(") < pos('roll_nodes opensearch pipeline/analytics "${ANALYTICS_VARS[@]}"\ntf_apply pipeline/analytics "${ANALYTICS_VARS[@]}"\n')
      and _an.count("\n\n") == 0 and _st.count("\n\n") == 0 and up.count("roll_nodes ") == 2
      and re.search(r'^OSS_ROLL="\$\{OSS_ROLL:-1\}"; flag_value OSS_ROLL\b', up, re.M) is not None
      and 'if [ -n "$ROLL_PLAN" ]; then rm -f "$ROLL_PLAN"; fi' in up[pos("\ntrap '"):up.index("\n", pos("\ntrap '") + 1)])
check("ops/oss/up.sh は analytics の前に Grafana の admin のパスワードを SSM に作り、analytics に create_grafana=true と Grafana のタグを渡す（OSS 版はいつも Grafana を作る）",
      0 <= pos('ensure_secret "/$PREFIX/grafana/admin-password" password') < pos("tf_apply pipeline/analytics")
      and "-var create_grafana=true" in _an and '-var "grafana_image_tag=$GRAFANA_TAG"' in _an)
check("ops/oss/up.sh は analytics に 4 つの格納先と、Spark・OpenSearch・VictoriaMetrics・Splunk のタグ、Splunk の台数と index、Spark のサブネット、device map を渡す",
      all(v in _an for v in ("-var 'sinks=[" + '"iceberg","opensearch","prometheus","splunk"' + "]'", '-var "spark_image_tag=$SPARK_TAG"',
                              '-var "opensearch_image_tag=$OSS_OPENSEARCH_TAG"', '-var "victoriametrics_image_tag=$OSS_VM_TAG"',
                              '-var "splunk_image_tag=$SPLUNK_TAG"', '-var "splunk_index=$SPLUNK_INDEX"', '-var "splunk_az_num=$SPLUNK_AZ_NUM"',
                              '-var "emr_az_num=$EMR_AZ_NUM"', '-var "device_map=$DEVICE_MAP"')))
check("ops/oss/up.sh は graph の前に SSM の /<接頭辞>/neo4j-password を作り、status の Lambda のレイヤー（app/graph/requirements-oss.txt を arm64 向けに）を入れ、Neo4j のタグと alert_history=true を渡す",
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
check("ops/oss/up.sh の LAYER_PYVER は sync.tf の Lambda の runtime とレイヤーの compatible_runtimes と同じ版（片方だけ変えると読めないレイヤーになる）",
      f'runtime          = "python{_layer_pyver}"' in _sync_tf and f'compatible_runtimes      = ["python{_layer_pyver}"]' in _sync_tf
      and _sync_tf.count("python3.") == 2)
UP_ENDPOINTS = {"ssm", "ssmmessages", "ecr.api", "ecr.dkr", "logs", "s3tables", "sns", "kinesis-firehose",
                "bedrock-runtime", "bedrock-agentcore", "ecs", "sqs", "bedrock-agentcore.gateway", "athena"}
check("ops/oss/up.sh のエンドポイントは 14 個: 土台の 5 つ、analytics と graph の s3tables・sns・kinesis-firehose、agent の bedrock-runtime・bedrock-agentcore、"
      "nautobot の ecs、workflow の sqs・bedrock-agentcore.gateway・athena",
      (m := re.search(r'^ENDPOINTS="([^"]*)"$', up, re.M)) and UP_ENDPOINTS == set(m.group(1).split()) and len(m.group(1).split()) == 14)
check("ops/oss/up.sh は agent・graph・workflow に lambda_az_num を、agent に runtime_az_num を、nautobot に nautobot_db_az_num を渡す（マネージド版が渡している値）",
      all(re.search(rf'^az_num {k} 1 1 ', up, re.M) for k in ("RUNTIME_AZ_NUM", "LAMBDA_AZ_NUM", "NAUTOBOT_DB_AZ_NUM"))
      and 'tf_apply agent -var "agent_image_tag=$IMAGE_TAG" -var "runtime_az_num=$RUNTIME_AZ_NUM" -var "lambda_az_num=$LAMBDA_AZ_NUM"' in up
      and 'tf_apply pipeline/graph -var "neo4j_image_tag=$NEO4J_TAG" -var alert_history=true -var "lambda_az_num=$LAMBDA_AZ_NUM"' in up
      and 'tf_apply workflow -var "worker_image_tag=$IMAGE_TAG" -var "lambda_az_num=$LAMBDA_AZ_NUM"' in up
      and 'tf_apply pipeline/nautobot -var "nautobot_image_tag=$NAUTOBOT_TAG" -var "redis_image_tag=$REDIS_TAG" -var "nautobot_db_az_num=$NAUTOBOT_DB_AZ_NUM"' in up)
check("ops/oss/up.sh は analytics に http_send と Spark の 1 回に読む件数（max_offsets_per_trigger とその格納先ごと）を、stream に telegraf_az_num と dialin_targets_from_nautobot=true を渡す",
      '-var "http_send=$HTTP_SEND"' in _an and '-var "max_offsets_per_trigger=' in _an and '-var "max_offsets_per_trigger_by_sink=' in _an
      and '-var "telegraf_az_num=$TELEGRAF_AZ_NUM"' in up and "-var dialin_targets_from_nautobot=true" in up)
_spark_tf = read("IaC/terraform/oss/pipeline/analytics/spark.tf")
check("Spark のサービスは Terraform では 0 台で作り（desired_count = 0、あとの変更は見ない）、up.sh が OpenSearch・VictoriaMetrics・Splunk を待ったあとで 1 台にする",
      re.search(r"^\s*desired_count\s*=\s*0$", _spark_tf, re.M) and "ignore_changes = [desired_count]" in _spark_tf
      and 0 <= pos("tf_apply pipeline/analytics") < pos("--services $OS_SERVICES") < pos("--services $VM_SERVICES")
      < pos("'tasks[].healthStatus'") < pos('echo "Splunk は起動した"') < pos("--desired-count 1")
      and up.count("--desired-count 1") == 1)
check("ops/oss/up.sh は Neo4j のサービスが安定してから、Web の EC2（部品を入れ直したあと）で ops/seed_graph.py を流す（マネージド版が Neptune に入れるのと同じスクリプト）",
      0 <= pos("aws ec2 reboot-instances") < pos("tf_apply pipeline/graph") < pos('--services "$NEO4J_SERVICE"') < pos("base64 < ops/seed_graph.py")
      < pos("tf_apply pipeline/nautobot")
      and "ops/seed_graph.py" in read("ops/up.sh") and "/usr/bin/python3.13 -" in up)
check("ops/oss/up.sh は Web に Neo4j のドライバーを入れる（app/dashboard/requirements-oss.txt のホイールを wheels-oss/ に取り、S3 に上げる）。wheels-oss/ は git に入れない",
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
check("ops/oss/up.sh はワーカーのイメージを Neo4j のドライバー入り（app/temporal/requirements-oss.txt）でビルドする",
      'build_worker "$IMAGE_TAG" requirements-oss.txt' in up and re.search(r"^neo4j==", read("app/temporal/requirements-oss.txt"), re.M))
check("ops/oss/up.sh は agent（Runtime）のイメージも Neo4j のドライバー入り（app/agentcore/requirements-oss.txt）でビルドする。マネージド版は既定（requirements.txt）のまま",
      'build_agent "$REPO:$IMAGE_TAG" requirements-oss.txt' in up and re.search(r"^neo4j==", read("app/agentcore/requirements-oss.txt"), re.M)
      and re.search(r"^ARG REQUIREMENTS=requirements\.txt$", read("docker/images/agentcore/Dockerfile"), re.M)
      and '--build-arg "REQUIREMENTS=${2:-requirements.txt}"' in read("ops/up-common.sh")
      and re.search(r'^\s*build_agent "\$REPO:\$IMAGE_TAG"(\s+#.*)?$', read("ops/up.sh"), re.M))
check("ops/oss/up.sh は nautobot の前に ensure_nautobot_secrets（マネージド版と同じ関数）を呼ぶ",
      0 <= pos("\nensure_nautobot_secrets") < pos("tf_apply pipeline/nautobot") and "ensure_nautobot_secrets" in read("ops/up.sh"))
check("ops/oss/up.sh は最後に Web へのポートフォワーディングを開く（NO_DASHBOARD_PORTFORWARD=1 なら開かずに終わる）。exec の前に一時ファイルを片付ける",
      0 <= pos("tf_apply workflow") < pos('if [ -n "$NO_DASHBOARD_PORTFORWARD" ]; then') < pos("\ntrap - EXIT") < pos("\nexec aws ssm start-session")
      and "--document-name AWS-StartPortForwardingSession" in up[pos("\nexec aws ssm start-session"):]
      and up.rstrip().endswith('--parameters "{\\"portNumber\\":[\\"8080\\"],\\"localPortNumber\\":[\\"$LOCAL_PORT\\"]}"'))
check("ops/oss/up.sh はポートフォワードの案内（Web・lab・Kafbat UI・Nautobot・Splunk・Grafana・Neo4j のブラウザと Bolt）を、パスワードの値ではなく取り方で出す",
      all(f"output -raw {o}" in up for o in ("start_session_command", "kafka_ui_port_forward_command", "kafka_ui_password_command", "port_forward_command",
                                              "password_command", "splunk_port_forward_command", "splunk_password_command", "grafana_port_forward_command",
                                              "grafana_password_command", "opensearch_password_parameter",
                                              "neo4j_password_parameter", "neo4j_browser_port_forward_command", "neo4j_bolt_port_forward_command")))
check("ops/oss/up.sh は SPLUNK_AZ_NUM が 2 以上（クラスター）なら SPLUNK_INDEX を書けない（マネージド版と同じ）",
      re.search(r'^az_num SPLUNK_AZ_NUM 1 1 3 ', up, re.M) and re.search(r'^az_num EMR_AZ_NUM 1 1 3 ', up, re.M)
      and re.search(r'^SPLUNK_INDEX="\$\{SPLUNK_INDEX:-\}"$', up, re.M)
      and 'SPLUNK_INDEX を消すか、SPLUNK_AZ_NUM=1 にする' in up)
check("ops/oss/up.sh は stream の前に SSM の /<接頭辞>/kafka/cluster-id を kafka-cluster-id で作り、stream に Kafka の版を渡す",
      0 <= pos('ensure_secret "/$PREFIX/kafka/cluster-id" kafka-cluster-id') < pos("tf_apply pipeline/stream")
      and '-var "kafka_image_tag=$OSS_KAFKA_TAG"' in up)
check("ops/oss/ はシークレットの値を読まない（get-parameter / --with-decryption を打たない）",
      not re.search(r"get-parameter\b|--with-decryption", up + down + img_sh))
check("ops/oss/up.sh の lab の既定の認証情報は ensure_fixed_secret にだけ渡す（echo しない）",
      all("ensure_fixed_secret" in line for line in up.splitlines() if re.search(r"\$\{?LAB_[A-Z_]*(PASSWORD|COMMUNITY|USERNAME)", line)))

# ================================================================ 6. ops/oss/up.sh を偽物の道具で最後まで通す
LAB_NODES = len(re.findall(r"^ *kind: (?:nokia_srlinux|linux)$", read("app/containerlab/splab.clab.yml.in"), re.M))
def run_up(inv, extra=None):
    reset(inv)
    shutil.rmtree(os.path.join(REPO, "ops", "logs"), ignore_errors=True)
    envfile = os.path.join(TMP, "owner-x.env")
    with open(envfile, "w", encoding="utf-8") as f:
        f.write("OWNER=x\n")
    p = subprocess.run(["bash", "ops/oss/up.sh"], cwd=REPO, capture_output=True, text=True, timeout=600,
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
check("up.sh（通し）: SSM のパラメータを 14 個、全部 SecureString で、ManagedBy=ops/oss/up.sh・Project=x-nwc-oss のタグを付けて作る（ops/oss/down.sh が消せる）",
      set(inv["ssm"]) == UP_PARAMS and all(m["type"] == "SecureString" and m["tags"].get("ManagedBy") == "ops/oss/up.sh"
                                           and m["tags"].get("Project") == "x-nwc-oss" for m in inv["ssm"].values()))
_secrets = [m.get("value", "") for n, m in inv["ssm"].items() if "/telegraf-dialin/" not in n]
check("up.sh（通し）: 乱数で作ったシークレット（11 個）の値を、画面にも、aws・terraform・docker の引数にも出さない",
      len(_secrets) == 11 and all(len(v) >= 16 for v in _secrets)
      and not any(v in out or any(v in " ".join(c["args"]) for c in cs) for v in _secrets))
_slver = re.search(r"^SYSLOG_NG_VERSION=(\S+)$", read("ops/up-common.sh"), re.M).group(1)
_gfver = re.search(r"^GOFLOW2_TAG=(\S+)$", read("ops/up-common.sh"), re.M).group(1)
_sb = [c["args"] for c in cs if c["cmd"] == "docker" and c["args"][:2] == ["buildx", "build"] and c["args"][-1] == "app/syslog-ng/"]
_stag = arg_after(_sb[0], "-t") if _sb else ""
check("up.sh（通し）: syslog-ng のイメージを arm64 でビルドし（版は ops/up-common.sh の SYSLOG_NG_VERSION を --build-arg、Dockerfile は docker/images/syslog-ng/）、"
      "同じタグを stream に渡す（cycle 012）",
      len(_sb) == 1 and re.fullmatch(re.escape(REG) + r"/x-nwc-oss-syslog-ng:" + re.escape(_slver) + r"-[0-9a-f]+", _stag)
      and _sb[0][2:4] == ["--platform", "linux/arm64"] and "--push" in _sb[0] and arg_after(_sb[0], "--build-arg") == f"SYSLOG_NG_VERSION={_slver}"
      and arg_after(_sb[0], "-f") == "docker/images/syslog-ng/Dockerfile"
      and has_var(A.get("pipeline/stream", []), "syslog_ng_image_tag=" + _stag.rsplit(":", 1)[-1]))
check("up.sh（通し）: GoFlow2 は上流の netsampler/goflow2 の arm64 を ops/up-common.sh の GOFLOW2_TAG のまま ECR に置き直し、同じタグを stream に渡す（cycle 012）",
      [c["args"] for c in cs if c["cmd"] == "docker" and "goflow2" in " ".join(c["args"])]
      == [["pull", "--platform", "linux/arm64", f"netsampler/goflow2:{_gfver}"], ["tag", f"netsampler/goflow2:{_gfver}", f"{REG}/x-nwc-oss-goflow2:{_gfver}"],
          ["push", f"{REG}/x-nwc-oss-goflow2:{_gfver}"]]
      and has_var(A.get("pipeline/stream", []), f"goflow2_image_tag={_gfver}"))
_slw = first(cs, lambda c: is_aws(c, "ecs", "wait", "--services out-stream-syslog_ng_service_name out-stream-goflow2_service_name"))
check("up.sh（通し）: stream の apply のあと、syslog-ng と GoFlow2 のサービス（output の名前）が Telegraf と同じクラスターで安定するのを待つ（cycle 012）",
      0 <= apply_at(cs, "pipeline/stream") < _slw
      and arg_after(cs[_slw]["args"], "--cluster") == "out-stream-telegraf_cluster_name" and "syslog-ng と GoFlow2 は動いている" in out)
docker = [c["args"] for c in cs if c["cmd"] == "docker"]
_tags = {arg_after(a, "-t") for a in docker if a[:2] == ["buildx", "build"]} | {a[-1] for a in docker if a[0] == "push"}
check("up.sh（通し）: イメージを ECR に置く（OSS の 7 つ、lab の 2 つ、Telegraf、Kafbat UI、Splunk、Grafana、agent、worker、Temporal、Nautobot、Redis、"
      "syslog-ng、GoFlow2）。docker login のあと、stream の apply より前",
      {t.split("/", 1)[1].split(":")[0] for t in _tags if t.startswith(REG + "/")}
      >= {f"x-nwc-oss-{n}" for n in V["OSS_IMAGES"].split()}
      | {f"x-nwc-oss-{n}" for n in ("telegraf", "splunk", "grafana", "agent", "worker", "nautobot", "syslog-ng", "goflow2")}
      and len(_tags) >= 19 and all(t.startswith(REG + "/x-nwc-oss-") for t in _tags)
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
    m = re.search(r"^LAYER_PLATFORM=(\S+); LAYER_PYVER=(\S+)$", read("ops/oss/up.sh"), re.M)
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

def grafana_sent(c):  # ops/grafana_rules_check.py を送った send-command なら (接頭辞, リポジトリのものそのものか)。違えば None
    if not is_aws(c, "ssm", "send-command"):
        return None
    m = re.fullmatch(r"echo (\S+) \| base64 -d \| NAME_PREFIX=(\S+) /usr/bin/python3\.13 -", json.loads(arg_after(c["args"], "--parameters"))["commands"][-1])
    body = base64.b64decode(m.group(1)) if m else b""
    return (m.group(2), body.decode() == read("ops/grafana_rules_check.py")) if b"api/v1/rules" in body else None

_g = [i for i, c in enumerate(cs) if grafana_sent(c)]
_gwait = first(cs, lambda c: is_aws(c, "ecs", "wait", "--services out-analytics-grafana_service_name"))
check("up.sh（通し）: 最後（9-2）に Grafana のサービスが安定してから、Web の EC2 に ops/grafana_rules_check.py そのものを接頭辞 x-nwc-oss で 1 回送る。OK なら警告は出ない",
      len(_g) == 1 and grafana_sent(cs[_g[0]]) == ("x-nwc-oss", True) and arg_after(cs[_g[0]]["args"], "--instance-ids") == "out-core-web_instance_id"
      and 0 <= apply_at(cs, "workflow") < _gwait < _g[0]
      and arg_after(cs[_gwait]["args"], "--cluster") == "out-analytics-analytics_cluster_name"
      and multi_of(cs[_gwait]["args"], "--services") == ["out-analytics-grafana_service_name"]
      and "判定: OK（4 本とも評価のエラーなし）" in p.stdout and "アラートルールの評価を確かめた結果が OK ではない" not in out)

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
p, cs3, inv3 = run_up(inv2, {"FAKE_ECR_ALL": "1", "NO_DASHBOARD_PORTFORWARD": "1", "FAKE_GRAFANA": "NG",
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
_gw = "Grafana のアラートルールの評価を確かめた結果が OK ではない（判定: NG（4 本のうち 1 本の評価がエラー: nwc-opensearch/trap））。"
check("up.sh（Grafana のルールの評価がエラー）: 止めずに（0）警告を出し、最後にもう一度出す。警告には判定の行（タブの手前まで）とログの見方（/ecs/x-nwc-oss-grafana）と"
      "確かめ直すコマンド（ops/check-grafana.sh --oss）",
      p.returncode == 0 and out3.count(_gw) == 2 and out3.count("aws logs tail /ecs/x-nwc-oss-grafana --region ap-northeast-1") == 2
      and out3.count("直したら ops/check-grafana.sh --oss\033[0m") == 2 and len([c for c in cs3 if grafana_sent(c)]) == 1)
_mup = read("ops/up.sh")
_m85 = _mup[_mup.index("# ---- 8-5. workflow"):_mup.index("# ---- 9. Runtime")]
check("ops/up.sh（マネージド版）の workflow の待ちも 2 回までで、安定しなければ警告（WF_WARN）を出して先へ進み、最後にもう一度出す",
      _m85.count('aws ecs wait services-stable --region "$REGION" --cluster "$WF_CLUSTER" --services "$WF_SERVICE"') == 2
      and 'WF_WARN="workflow のワーカーのサービス' in _m85 and re.search(r'^WF_WARN=""$', _mup, re.M)
      and """if [ -n "$WF_WARN" ]; then printf '\\033[1;33m%s\\033[0m\\n' "$WF_WARN"; fi""" in _mup)
_m92 = _mup[_mup.index("# ---- 9-2. Grafana"):_mup.index("# ---- 10. ポートフォワーディング")]
check("ops/up.sh（マネージド版）も Runtime のロググループのあと（9-2）に同じ確かめを打ち、OK でなければ警告（GRAFANA_WARN）を最後にもう一度出す。"
      "state の一覧は grep -q で見ない（先に抜けると terraform が SIGPIPE になり、pipefail で偽になる）",
      _mup.index("# ---- 9. Runtime") < _mup.index("# ---- 9-2. Grafana") and re.search(r'^GRAFANA_WARN=""$', _mup, re.M)
      and not [l for l in _m92.splitlines() if "grep -q" in l and not l.lstrip().startswith("#")]
      and """if [ -n "$GRAFANA_WARN" ]; then printf '\\033[1;33m%s\\033[0m\\n' "$GRAFANA_WARN"; fi""" in _mup)

# ---- ops/up.sh の 9-2 だけを bash で打つ（015 の 81・83）。terraform は偽物（state list は M92_DIR/state の中身、output は out-<名前>。
# M92_EMPTY の出力は空、M92_FAIL の出力は rc=1、M92_STATE_FAIL=1 なら state list が rc=1）。grafana_rules_step は引数を出すだけ。
# 最後に GRAFANA_WARN の値を出す（最後の再掲が出すのはこの値。黄色の 1 回目の表示だけでは入れたかどうか分からない）
M92_DIR = os.path.join(TMP, "m92")
os.makedirs(os.path.join(M92_DIR, "bin"))
with open(os.path.join(M92_DIR, "bin", "terraform"), "w", encoding="utf-8") as f:
    f.write("""#!/bin/sh
echo "$*" >> "$M92_DIR/calls"
case "$2 $3" in
  "state list") [ -z "$M92_STATE_FAIL" ] || { echo "Error: Error acquiring the state lock" >&2; exit 1; }; cat "$M92_DIR/state"; exit $? ;;
  "output -raw")
    case ",$M92_FAIL," in *",$4,"*) echo "Error: Output \\"$4\\" not found" >&2; exit 1 ;; esac
    case ",$M92_EMPTY," in *",$4,"*) exit 0 ;; esac
    printf 'out-%s' "$4"; exit 0 ;;
esac
echo "fake terraform: unknown $*" >&2; exit 9
""")
os.chmod(os.path.join(M92_DIR, "bin", "terraform"), 0o755)
def m92(state="", grafana="", left="", **env):
    with open(os.path.join(M92_DIR, "state"), "w", encoding="utf-8") as f:
        f.write(state)
    open(os.path.join(M92_DIR, "calls"), "w").close()
    script = ("set -euo pipefail\nREGION=ap-northeast-1; PREFIX=x-nwc-poc\n. ops/common.sh\n. ops/up-common.sh\n"
              'grafana_rules_step() { echo "STEP $*"; }\n'
              f'INSTANCE_ID=i-web; GRAFANA="{grafana}"; ANALYTICS_LEFT="{left}"\n' + _m92 + "printf 'WARN=[%s]\\nDONE\\n' \"$GRAFANA_WARN\"\n")
    p = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env={"PATH": os.path.join(M92_DIR, "bin") + os.pathsep + os.environ["PATH"], "HOME": TMP, "M92_DIR": M92_DIR,
                            "M92_EMPTY": "", "M92_FAIL": "", "M92_STATE_FAIL": "", **env})
    with open(os.path.join(M92_DIR, "calls"), encoding="utf-8") as f:
        return p, f.read().splitlines()
_step = "STEP i-web out-analytics_cluster_name out-grafana_service_name ops/check-grafana.sh"
_left_msg = "。今回は analytics を作らないが、前の回の Grafana が残っている"
_st_gf = "aws_ecs_cluster.analytics\naws_ecs_service.grafana[0]\naws_ecs_service.opensearch[0]\n"
_r = {k: m92(**a) for k, a in {
    "made": dict(grafana="1", left="1", state=_st_gf),
    "left": dict(left="1", state=_st_gf),
    "left_nogf": dict(left="1", state="aws_ecs_cluster.analytics\naws_ecs_service.grafana_x\naws_ecs_service.opensearch[0]\n"),
    "none": dict(state=_st_gf),
    "left_big": dict(left="1", state="aws_ecs_service.grafana[0]\n" + "".join(f"aws_cloudwatch_log_group.x[{i}]\n" for i in range(200000))),
    "empty": dict(grafana="1", M92_EMPTY="grafana_service_name"),
    "left_empty": dict(left="1", state=_st_gf, M92_EMPTY="analytics_cluster_name"),
    "fail": dict(grafana="1", M92_FAIL="analytics_cluster_name"),
    "left_unread": dict(left="1", state=_st_gf, M92_STATE_FAIL="1"),
}.items()}
_an = "-chdir=IaC/terraform/aws-managed/pipeline/analytics"
check("ops/up.sh の 9-2: Grafana を今回作る（GRAFANA）なら、state を見ずに analytics の出力のクラスターとサービスで grafana_rules_step を 1 回打つ",
      _r["made"][0].returncode == 0 and _r["made"][0].stdout.count("STEP ") == 1 and _step in _r["made"][0].stdout and _left_msg not in _r["made"][0].stdout
      and "WARN=[]\n" in _r["made"][0].stdout
      and _r["made"][1] == [f"{_an} output -raw analytics_cluster_name", f"{_an} output -raw grafana_service_name"])
check("ops/up.sh の 9-2（83）: 今回は analytics を作らない回（PIPELINE=0 など）でも、残った analytics（ANALYTICS_LEFT）の state に Grafana の ECS サービスがあれば、"
      "見出しにそう書いて同じく打つ",
      _r["left"][0].returncode == 0 and _step in _r["left"][0].stdout and _left_msg in _r["left"][0].stdout and "WARN=[]\n" in _r["left"][0].stdout
      and _r["left"][1][0] == f"{_an} state list")
check("ops/up.sh の 9-2（83）: 残った analytics に Grafana の ECS サービスが無ければ（aws_ecs_service.grafana[…] だけを見る）打たない。"
      "analytics が残っていなければ state も見ない",
      all(_r[k][0].returncode == 0 and "STEP" not in _r[k][0].stdout and "9-2." not in _r[k][0].stdout and _r[k][0].stdout.endswith("WARN=[]\nDONE\n")
          for k in ("left_nogf", "none"))
      and _r["left_nogf"][1] == [f"{_an} state list"] and _r["none"][1] == [])
check("ops/up.sh の 9-2（83）: state の一覧が長くても（パイプの 64 KB を超えても）Grafana を見つける（grep -q だと terraform が SIGPIPE で落ち、pipefail で見落とす）",
      _r["left_big"][0].returncode == 0 and _step in _r["left_big"][0].stdout and "WARN=[]\n" in _r["left_big"][0].stdout)
_skip = ("IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない"
         "（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh")
check("ops/up.sh の 9-2（81）: analytics の出力（クラスターかサービスの名前）が空なら、どれのことかを言い（NG: の行）、止めずに（0）確かめていないと警告して先へ進む。"
      "grafana_rules_step（aws ecs wait と確かめ）は打たない",
      all(_r[k][0].returncode == 0 and f"NG: IaC/terraform/aws-managed/pipeline/analytics の出力 {n} が空" in _r[k][0].stderr
          and "STEP" not in _r[k][0].stdout and _r[k][0].stdout.endswith("DONE\n") and f"WARN=[{_skip}]" in _r[k][0].stdout
          for k, n in (("empty", "grafana_service_name"), ("left_empty", "analytics_cluster_name"))))
check("ops/up.sh の 9-2（81）: analytics の出力が読めない（terraform output が rc≠0）なら、terraform のエラーを残し、止めずに（0）確かめていないと警告して先へ進む",
      _r["fail"][0].returncode == 0 and 'Error: Output "analytics_cluster_name" not found' in _r["fail"][0].stderr
      and "NG: IaC/terraform/aws-managed/pipeline/analytics の出力 analytics_cluster_name が読めない（上のエラー）" in _r["fail"][0].stderr
      and "STEP" not in _r["fail"][0].stdout and _r["fail"][0].stdout.endswith("DONE\n") and f"WARN=[{_skip}]" in _r["fail"][0].stdout
      and _r["fail"][1] == [f"{_an} output -raw analytics_cluster_name"])
check("ops/up.sh の 9-2（83・D8）: 今回は analytics を作らない回で、残った analytics の state の一覧が読めない（state list が rc≠0）なら、黙って飛ばさず、"
      "見出しにそう書いて、止めずに（0）確かめていないと警告する。出力は読まず、grafana_rules_step も打たない",
      _r["left_unread"][0].returncode == 0 and "Error: Error acquiring the state lock" in _r["left_unread"][0].stderr
      and "。今回は analytics を作らないが、前の回の analytics の state が読めない" in _r["left_unread"][0].stdout
      and "STEP" not in _r["left_unread"][0].stdout and _r["left_unread"][0].stdout.endswith("DONE\n") and f"WARN=[{_skip}]" in _r["left_unread"][0].stdout
      and _r["left_unread"][1] == [f"{_an} state list"])
check("ops/up.sh の 9-2（81）: terraform が読めなくても up.sh を止めない（|| exit 1 を書かない。止めると配るコマンドと警告の再掲まで届かない。8-5 と同じ）",
      "|| exit 1" not in _m92 and "grafana_skip_warn ops/check-grafana.sh" in _m92)

# ---- 4 回目（81）: Grafana のサービスの名前の出力が空なら、9-2 は確かめずに警告して先へ進む（空の --services で ecs wait を打ったり、確かめを送ったりしない）
p4, cs4, _ = run_up(inv3, {"FAKE_ECR_ALL": "1", "NO_DASHBOARD_PORTFORWARD": "1", "FAKE_TF_EMPTY": "grafana_service_name"})
_skip_oss = ("IaC/terraform/oss/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない"
             "（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh --oss")
check("ops/oss/up.sh（81）: 9-2 で analytics の出力 grafana_service_name が空なら、どれのことかを言い（NG: の行）、止めずに（0）確かめていないと警告して"
      "配るコマンドまで進み、警告は最後にもう一度出す。ecs wait も確かめの送信も打たない",
      p4.returncode == 0 and "9-2. Grafana のアラートルール" in p4.stdout and "利用者に配るコマンド" in p4.stdout
      and "NG: IaC/terraform/oss/pipeline/analytics の出力 grafana_service_name が空" in p4.stderr
      and (p4.stdout + p4.stderr).count(_skip_oss) == 2
      and not [c for c in cs4 if grafana_sent(c)]
      and not [c for c in cs4 if is_aws(c, "ecs", "wait") and ("" in multi_of(c["args"], "--services") or not multi_of(c["args"], "--services"))]
      and not [c for c in cs4 if is_aws(c, "ecs", "wait", "grafana")])
check("ops/oss/up.sh（81）: 7-4b の analytics のクラスターの名前も tf_output で読み、読めない・空なら 7-4b で止まる（9-2 もこのクラスター）。"
      "9-2 のサービスの名前は読めなくても止めず、grafana_skip_warn で警告する",
      "AN_CLUSTER=$(tf_output pipeline/analytics analytics_cluster_name) || exit 1" in up
      and "if GF_SERVICE=$(tf_output pipeline/analytics grafana_service_name); then" in up
      and 'grafana_skip_warn "ops/check-grafana.sh --oss"' in up
      and "GF_SERVICE=$(tf_output pipeline/analytics grafana_service_name) || exit 1" not in up
      and "tf pipeline/analytics output -raw analytics_cluster_name" not in up and "tf pipeline/analytics output -raw grafana_service_name" not in up)

# ---- up.sh の作ったものを ops/oss/down.sh が消す（同じ在庫から）
p, csd, invd = run_down("ops/oss/down.sh", "x", inv=inv3)
check("up.sh → down.sh: up.sh が作った SSM のパラメータ 14 個を全部消し、up.sh が apply した 9 つのルートを全部 destroy する",
      p.returncode == 0 and invd["ssm"] == {} and len(aws_calls(csd, "ssm", "delete-parameter")) == 14
      and destroyed(csd) == {f"IaC/terraform/oss/{r}" for r in ROOTS} and "残り: 0 件" in p.stdout)

check("ops/oss/up.sh と down.sh の terraform init は、どのルートも -lockfile=readonly（lock はマネージド版へのシンボリックリンクなので書き換えない）",
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

# ================================================================ 8. ops/check-grafana.sh（--oss で OSS 版の state と接頭辞。up.sh の 9-2 と同じ確かめを単独で）
def run_check(*args, extra=None):
    reset(inventory())
    envfile = os.path.join(TMP, "owner-x.env")
    with open(envfile, "w", encoding="utf-8") as f:
        f.write("OWNER=x\n")
    p = subprocess.run(["bash", "ops/check-grafana.sh", *args], cwd=REPO, capture_output=True, text=True, timeout=300,
                       env=fake_env({"DEPLOY_ENV_FILE": envfile, "FAKE_TF_UP": "1", **(extra or {})}))
    return p, calls()

p, cs = run_check("--oss")
_send = [c for c in cs if is_aws(c, "ssm", "send-command")]
check("check-grafana.sh --oss: Web の EC2 と Grafana のサービスを IaC/terraform/oss の出力から取り、接頭辞 x-nwc-oss で ops/grafana_rules_check.py を 1 回送る。OK なら 0",
      p.returncode == 0 and [c["args"] for c in tf_calls(cs)] == [["-chdir=IaC/terraform/oss/base/core", "output", "-raw", "web_instance_id"],
                                                                   ["-chdir=IaC/terraform/oss/pipeline/analytics", "output", "-raw", "grafana_service_name"]]
      and len(_send) == 1 and grafana_sent(_send[0]) == ("x-nwc-oss", True) and arg_after(_send[0]["args"], "--instance-ids") == "out-core-web_instance_id"
      and p.stdout.rstrip().endswith("判定: OK（4 本とも評価のエラーなし）") and not aws_calls(cs, "ecs", "wait"))
p, cs = run_check()
check("check-grafana.sh（--oss 無し）: マネージド版の IaC/terraform/aws-managed と接頭辞 x-nwc-poc",
      p.returncode == 0 and {chdir_of(c) for c in tf_calls(cs)} == {"IaC/terraform/aws-managed/base/core", "IaC/terraform/aws-managed/pipeline/analytics"}
      and [grafana_sent(c) for c in cs if is_aws(c, "ssm", "send-command")] == [("x-nwc-poc", True)])
p, cs = run_check("--oss", extra={"FAKE_GRAFANA": "NG"})
check("check-grafana.sh: NG なら 1 で終わり、判定の行と、理由を見る Grafana のログのコマンド（/ecs/x-nwc-oss-grafana の Failed to evaluate rule）を出す",
      p.returncode == 1 and "判定: NG（4 本のうち 1 本の評価がエラー: nwc-opensearch/trap）" in p.stdout + p.stderr
      and "aws logs tail /ecs/x-nwc-oss-grafana --region ap-northeast-1 --since 1h --filter-pattern '\"Failed to evaluate rule\"'" in p.stderr)
p, cs = run_check("--oss", extra={"FAKE_GRAFANA": "UNKNOWN"})
check("check-grafana.sh: 未確認（判定: 未確認。届かない・401・待ち切れ）なら 2 で終わり、判定の行を出す。評価のエラーとは限らないので Grafana のログは案内しない",
      p.returncode == 2 and "判定: 未確認（300 秒待った。Grafana に届かない（URLError: timed out））" in p.stdout + p.stderr
      and "Failed to evaluate rule" not in p.stdout + p.stderr)
p, cs = run_check("--oss", extra={"FAKE_TF_EMPTY": "grafana_service_name"})
check("check-grafana.sh: Grafana が無い（grafana_service_name が空）なら理由を言って 3 で止まり、確かめを送らない",
      p.returncode == 3 and "Grafana が無い" in p.stderr and not aws_calls(cs, "ssm", "send-command"))
p, cs = run_check("--oss", extra={"DEPLOY_ENV_FILE": os.path.join(TMP, "no-such.env")})
check("check-grafana.sh: deploy.env の誤り（load_deploy_env の die）も 3 で止まる（ops/common.sh の die の 1 は NG と紛れる）",
      p.returncode == 3 and "DEPLOY_ENV_FILE のファイルが無い" in p.stderr and not tf_calls(cs) and not [c for c in cs if c["cmd"] == "aws"])
p, cs = run_check("--oss", extra={"SSM_RUN_WAIT": "0"})
check("check-grafana.sh: SSM_RUN_WAIT の値の誤りも 3 で止まる（確かめを送らない）",
      p.returncode == 3 and "SSM_RUN_WAIT は SSM Run Command の結果を待つ秒数" in p.stderr and not [c for c in cs if c["cmd"] == "aws"])
p, cs = run_check("--yes")
check("check-grafana.sh: 知らない引数は使い方を出して 3 で止まる（terraform と aws には触らない）",
      p.returncode == 3 and "使い方" in p.stderr and not tf_calls(cs) and not [c for c in cs if c["cmd"] == "aws"])

shutil.rmtree(TMP, ignore_errors=True)
print(f"通過 {passed} / 失敗 0")
