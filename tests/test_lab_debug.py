"""デバッグ用の EC2（IaC/cloudformation/lab-debug.yaml。lab + Telegraf を 1 台）が IaC/terraform/aws-managed/pipeline/lab と stream の Telegraf からずれていないかを見る。
- 独立: スタックが VPC・エンドポイント・バケット・ECR を持ち、ops/up.sh / ops/down.sh は触らない（2026-10-04 ユーザー決定。ops/lab-debug.sh だけで扱う）
- 版: CloudFormation のパラメータの既定値 = IaC/terraform/aws-managed/pipeline/lab の変数の既定値 = ops/lab-common.sh（ops/up.sh と ops/lab-debug.sh が source する）
- EC2 の中: どちらの user_data も env を書いて S3 の lab/ を置き直し、app/containerlab/setup.sh を exec するだけ（TELEGRAF_IMAGE の値だけが違う）
- ロール: IaC/terraform/aws-managed/pipeline/lab/iam.tf と同じ Sid と Action（ECR は Telegraf のリポジトリも読む）
- Telegraf: 同じ telegraf.conf.in を SINK=stdout で描く。入力は MSK 向けと同じで、出力だけが標準出力になる（app/telegraf/telegraf.sh render を手元で回す）
実行は uv run python tests/test_lab_debug.py（pyyaml を使う。AWS も docker も要らない）。"""
import json, os, re, shlex, subprocess, sys, tempfile, tomllib

import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

passed = 0
def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)

def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()

def read_ops(name):  # ops/up.sh / down.sh は、読んでいる共通の関数（ops/common.sh と ops/<name>-common.sh。OSS 版の ops/oss/ と共通）とつないで見る
    return read("ops", "common.sh") + read("ops", f"{name}-common.sh") + read("ops", f"{name}.sh")

# CloudFormation の短縮形（!Sub / !Ref / !If ...）を {"Fn::Sub": ...} の形で読む
class CfnLoader(yaml.SafeLoader):
    pass
def _cfn(loader, suffix, node):
    name = "Ref" if suffix == "Ref" else ("Fn::GetAtt" if suffix == "GetAtt" else "Fn::" + suffix)
    if isinstance(node, yaml.ScalarNode):
        v = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        v = loader.construct_sequence(node, deep=True)
    else:
        v = loader.construct_mapping(node, deep=True)
    return {name: v}
CfnLoader.add_multi_constructor("!", _cfn)

cfn = yaml.load(read("IaC", "cloudformation", "lab-debug.yaml"), Loader=CfnLoader)
params = cfn["Parameters"]
res = cfn["Resources"]
common = read("ops", "lab-common.sh")
lab_vars = read("IaC", "terraform", "aws-managed", "pipeline", "lab", "variables.tf")
iam_tf = read("IaC", "terraform", "aws-managed", "pipeline", "lab", "iam.tf")
tftpl = read("IaC", "terraform", "aws-managed", "pipeline", "lab", "templates", "lab_user_data.sh.tftpl")
setup = read("app", "containerlab", "setup.sh")
lab_sh = read("app", "containerlab", "lab.sh")
tg_sh = read("app", "telegraf", "telegraf.sh")
up = read_ops("up")
down = read_ops("down")
dbg = read("ops", "lab-debug.sh")

def tf_default(name):
    m = re.search(r'variable "%s" \{[\s\S]*?default\s*=\s*("?)([^"\n]*)\1\n' % name, lab_vars)
    return m.group(2) if m else None
def sh_const(src, name):
    m = re.search(r"^%s=(\S+)" % name, src, re.M)
    return m.group(1).strip('"') if m else None

# ---- 版と既定値
check("ContainerlabVersion の既定値 = IaC/terraform/aws-managed/pipeline/lab の containerlab_version = ops/lab-common.sh の CONTAINERLAB_VERSION",
      str(params["ContainerlabVersion"]["Default"]) == tf_default("containerlab_version") == sh_const(common, "CONTAINERLAB_VERSION") is not None)
# lab のイメージの ECR のタグは上流の版 + -amd64（2026-10-08 より前の arm64 の写しが KEEP_ECR=1 で同じ名前のまま残っていても、写しを飛ばさない）
LAB_IMAGES = [("SRLINUX", "srlinux", "SrlinuxImageTag", "srlinux_image_tag"), ("MULTITOOL", "multitool", "MultitoolImageTag", "multitool_image_tag"),
              ("TREX", "trex", "TrexImageTag", "trex_image_tag")]
check("ops/lab-common.sh: lab の EC2 のアーキ LAB_ARCH は amd64 で、ECR のタグ *_ECR_TAG は上流の版 *_TAG の後ろに -$LAB_ARCH",
      sh_const(common, "LAB_ARCH") == "amd64"
      and all(sh_const(common, f"{n}_ECR_TAG") == f"${n}_TAG-$LAB_ARCH" for n, *_ in LAB_IMAGES))
for n, repo, cfn_name, tf_name in LAB_IMAGES:
    check(f"{cfn_name} の既定値 = IaC/terraform/aws-managed/pipeline/lab の {tf_name} = ops/lab-common.sh の {n}_TAG + -amd64（上流の版そのままでない）",
          str(params[cfn_name]["Default"]) == tf_default(tf_name) == f"{sh_const(common, n + '_TAG')}-amd64"
          and sh_const(common, n + "_TAG") not in (None, tf_default(tf_name)))
_oss_up = read("ops", "oss", "up.sh")
_ecr_has_lab = [(f, m) for f, s in (("ops/lab-common.sh", common), ("ops/up.sh", up), ("ops/oss/up.sh", _oss_up), ("ops/lab-debug.sh", dbg))
                for m in re.findall(r'ecr_has "\$\w+-lab-(\w+)" "\$(\w+)"', s)]
check("ECR に lab のイメージがあるかは *_ECR_TAG で見る（lab-common.sh の mirror_lab_images、ops/up.sh、ops/oss/up.sh、ops/lab-debug.sh で 3 つずつ）",
      sorted(_ecr_has_lab) == sorted((f, (repo, f"{n}_ECR_TAG")) for f in ("ops/lab-common.sh", "ops/up.sh", "ops/oss/up.sh", "ops/lab-debug.sh")
                                     for n, repo, *_ in LAB_IMAGES))
check("mirror_lab_images は上流の <upstream>:<版> を linux/$LAB_ARCH で引き、ECR の <接頭辞>-lab-<名前>:<*_ECR_TAG> に置く",
      all(f'mirror_image "${n}_UPSTREAM:${n}_TAG" "$1/$2-lab-{repo}:${n}_ECR_TAG" "linux/$LAB_ARCH"' in common for n, repo, *_ in LAB_IMAGES))
check("lab-debug.sh が CloudFormation に渡す lab のイメージのタグは *_ECR_TAG",
      all(f'"{cfn_name}=${n}_ECR_TAG"' in dbg for n, _, cfn_name, _ in LAB_IMAGES))
check("base/ecr の lab のリポジトリ（lab_repositories）は mirror_lab_images が置く先と同じ（ECR に無いリポジトリへは push できない）",
      set(re.findall(r'"(\w+)"', re.search(r"lab_repositories\s*=\s*var\.create_lab_repositories \? toset\(\[([^\]]*)\]\)",
                                           read("IaC", "terraform", "aws-managed", "base", "ecr", "main.tf")).group(1)))
      == set(re.findall(r'"\$1/\$2-lab-([a-z]+):', common)) == {repo for _, repo, *_ in LAB_IMAGES})
check("InstanceType / VolumeSize / ImageId / AutoStartLab の既定値は IaC/terraform/aws-managed/pipeline/lab と同じ",
      params["InstanceType"]["Default"] == tf_default("instance_type")
      and str(params["VolumeSize"]["Default"]) == tf_default("volume_size")
      and params["ImageId"]["Default"] == tf_default("ami_ssm_parameter")
      and params["AutoStartLab"]["Default"] == tf_default("auto_start_lab"))
check("InstanceType の選べる値は terraform の検査と同じ（x86_64 だけ。TRex のイメージが amd64 だけのため）",
      params["InstanceType"]["AllowedValues"] == re.findall(r'"([a-z0-9]+\.\w+)"', re.search(r'contains\(\[([^\]]*)\], var\.instance_type\)', lab_vars).group(1)))
check("ops/lab-common.sh の TELEGRAF_VERSION = docker/images/telegraf/Dockerfile の ARG の既定値",
      sh_const(common, "TELEGRAF_VERSION") == re.search(r"^ARG TELEGRAF_VERSION=(\S+)", read("docker", "images", "telegraf", "Dockerfile"), re.M).group(1))
check("ops/up.sh と ops/lab-debug.sh は ops/lab-common.sh を source し、lab の版を自分では持たない",
      '. "$(dirname "$0")/lab-common.sh"' in up and '. "$(dirname "$0")/lab-common.sh"' in dbg
      and not any(re.search(r"^%s=" % k, s, re.M) for k in ("SRLINUX_TAG", "MULTITOOL_TAG", "TREX_TAG", "LAB_ARCH", "SRLINUX_ECR_TAG", "MULTITOOL_ECR_TAG", "TREX_ECR_TAG",
                                                         "CONTAINERLAB_VERSION", "TELEGRAF_VERSION", "CONTAINERLAB_RPM") for s in (up, dbg))
      and not any(re.search(r"^(ecr_has|fetch|dir_tag)\(\)", s, re.M) for s in (up, dbg)))
check("イメージの作り方（ミラー・Telegraf のビルド・app/containerlab/ の置き方）は lab-common.sh の関数を両方が呼ぶ。Telegraf のタグはデバッグ用は -$LAB_ARCH 付き（lab の 3 つとそろえる。stream の arm64 は別の repo <prefix>-telegraf で -arm64 を付けない）",
      all(f in up for f in ("mirror_lab_images ", "build_telegraf ", "upload_lab ", "TELEGRAF_TAG=$(telegraf_tag)"))
      and all(f in dbg for f in ("mirror_lab_images ", "build_telegraf ", "upload_lab ", 'TELEGRAF_TAG="$(telegraf_tag)-$LAB_ARCH"'))
      and "docker pull" not in up.split("# ---- 2. イメージ")[1].split("# ---- 3.")[0].split("NEED_TEMPORAL")[0]
      and "ghcr.io/nokia/srlinux" not in up and "ghcr.io/nokia/srlinux" not in dbg)
_pass = re.findall(r'"(\w+)=\$', dbg.split("--parameter-overrides")[1].split("--tags")[0])
check("lab-debug.sh は既定値の無いパラメータと版を全部渡す（版は lab-common.sh から）",
      {k for k, v in params.items() if "Default" not in v} <= set(_pass)
      and {"ContainerlabVersion", "SrlinuxImageTag", "MultitoolImageTag", "TrexImageTag", "TelegrafImageTag", "CreateInstance", "NetworkPerimeter"} <= set(_pass)
      and set(_pass) <= set(params))

# ---- ロール（iam.tf と同じ Sid と Action）
role = res["Role"]["Properties"]
stmts = {s["Sid"]: s for s in role["Policies"][0]["PolicyDocument"]["Statement"]}
tf_sids = re.findall(r'Sid\s*=\s*"(\w+)"', iam_tf)
def tf_actions(sid):
    block = iam_tf.split(f'Sid      = "{sid}"' if f'Sid      = "{sid}"' in iam_tf else f'Sid    = "{sid}"')[1].split("},")[0]
    return sorted(re.findall(r'"((?:ecr|s3|ssm):\w+)"', re.search(r"Action\s*=\s*(.*)", block).group(1)))
def as_list(v):
    return sorted(v if isinstance(v, list) else [v])
check("ロールの Sid は iam.tf と同じ（TelegrafAddress は stream の NLB を読む forward 用なので、デバッグ用の EC2 には要らない）",
      set(stmts) == set(tf_sids) - {"TelegrafAddress"} and "ssm:GetParameter" not in str(stmts))
check("Sid ごとの Action は iam.tf と同じ", all(as_list(stmts[s]["Action"]) == tf_actions(s) for s in stmts))
repos = {k: v["Properties"] for k, v in res.items() if v["Type"] == "AWS::ECR::Repository"}
check("ECR はこのスタックのリポジトリ 4 つ（lab の 3 つと Telegraf）だけを読む",
      stmts["EcrPull"]["Resource"] == [{"Fn::GetAtt": f"{k}.Arn"} for k in ("SrlinuxRepository", "MultitoolRepository", "TrexRepository", "TelegrafRepository")])
check("S3 は このスタックのバケットの lab/ だけ（GetObject と prefix 付きの ListBucket）",
      stmts["S3Read"]["Resource"] == {"Fn::Sub": "${Bucket.Arn}/lab/*"} and stmts["S3List"]["Resource"] == {"Fn::GetAtt": "Bucket.Arn"}
      and stmts["S3List"]["Condition"] == {"StringLike": {"s3:prefix": "lab/*"}})
_perim = role["Policies"][1]["Fn::If"]
_deny = _perim[1]["PolicyDocument"]["Statement"][0]
_tf_perim = read("IaC", "terraform", "aws-managed", "base", "core", "perimeter.tf")
check("SSM のマネージドポリシーと、NetworkPerimeter のときだけ境界の Deny（perimeter.tf と同じ Action と条件。SourceVpc はこのスタックの VPC）",
      role["ManagedPolicyArns"] == [{"Fn::Sub": "arn:${AWS::Partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"}]
      and _perim[0] == "HasPerimeter" and _perim[2] == {"Ref": "AWS::NoValue"}
      and _deny["Effect"] == "Deny" and _deny["Resource"] == "*"
      and _deny["Action"] == re.findall(r'"([\w-]+:[\w*]+)"', _tf_perim.split("perimeter_denied_actions = [")[1].split("]")[0])
      and _deny["Condition"] == {"StringNotEqualsIfExists": {"aws:SourceVpc": {"Ref": "Vpc"}, "aws:CalledViaLast": "s3tables.amazonaws.com"},
                                 "BoolIfExists": {"aws:ViaAWSService": "false"}}
      and '"aws:SourceVpc" = aws_vpc.this.id, "aws:CalledViaLast" = "s3tables.amazonaws.com"' in _tf_perim
      and 'BoolIfExists            = { "aws:ViaAWSService" = "false" }' in _tf_perim
      and "AmazonSSMManagedInstanceCore" in iam_tf)

# ---- このスタックだけで閉じる（土台 IaC/terraform/aws-managed/base/core を使わない）
check("パラメータに土台から受け取るもの（サブネット・SG・バケット・境界ポリシー）が無い",
      not {"SubnetId", "SecurityGroupId", "BucketName", "PerimeterPolicyArn"} & set(params)
      and not re.search(r"ImportValue|base/core の output", read("IaC", "cloudformation", "lab-debug.yaml")))
check("VPC はインターネットへの経路を持たない（IGW / NAT / 0.0.0.0/0 の経路が無く、サブネットはパブリック IP を付けない）",
      not any(v["Type"] in ("AWS::EC2::InternetGateway", "AWS::EC2::NatGateway", "AWS::EC2::Route", "AWS::EC2::EgressOnlyInternetGateway") for v in res.values())
      and res["Subnet"]["Properties"]["MapPublicIpOnLaunch"] is False
      and res["Vpc"]["Properties"]["EnableDnsSupport"] is True and res["Vpc"]["Properties"]["EnableDnsHostnames"] is True)
_eps = {v["Properties"]["ServiceName"]["Fn::Sub"].rsplit(".", 1)[-1] if not v["Properties"]["ServiceName"]["Fn::Sub"].endswith(("ecr.api", "ecr.dkr"))
        else v["Properties"]["ServiceName"]["Fn::Sub"].split("${AWS::Region}.")[1]: v["Properties"]
        for v in res.values() if v["Type"] == "AWS::EC2::VPCEndpoint"}
check("エンドポイントは S3（gateway）と SSM で入る 2 本と ECR の 2 本だけ",
      set(_eps) == {"s3", "ssm", "ssmmessages", "ecr.api", "ecr.dkr"} and _eps["s3"]["VpcEndpointType"] == "Gateway"
      and "PolicyDocument" not in _eps["s3"])
check("インターフェース型はどれも private DNS を有効にし、ポリシーは endpoints.tf と同じ「このアカウントのプリンシパルだけ」",
      all(_eps[k]["VpcEndpointType"] == "Interface" and _eps[k]["PrivateDnsEnabled"] is True
          and _eps[k]["SecurityGroupIds"] == [{"Ref": "EndpointSecurityGroup"}]
          and _eps[k]["PolicyDocument"]["Statement"] == [{"Sid": "OwnAccountOnly", "Effect": "Allow", "Principal": "*", "Action": "*", "Resource": "*",
                                                          "Condition": {"StringEquals": {"aws:PrincipalAccount": {"Ref": "AWS::AccountId"}}}}]
          for k in ("ssm", "ssmmessages", "ecr.api", "ecr.dkr")))
_isg, _esg = res["InstanceSecurityGroup"]["Properties"], res["EndpointSecurityGroup"]["Properties"]
check("EC2 の SG は受信無し・送信 443 だけ。エンドポイントの SG は EC2 の SG からの 443 だけ受ける",
      "SecurityGroupIngress" not in _isg and [(r["IpProtocol"], r["FromPort"], r["ToPort"]) for r in _isg["SecurityGroupEgress"]] == [("tcp", 443, 443)]
      and [(r["FromPort"], r["SourceSecurityGroupId"]) for r in _esg["SecurityGroupIngress"]] == [(443, {"Ref": "InstanceSecurityGroup"})])
check("ECR のリポジトリはタグを上書きできず、スタックを消すとイメージごと消える（base/ecr の <接頭辞>-lab-* と別の名前）",
      {k: r["RepositoryName"]["Fn::Sub"] for k, r in repos.items()}
      == {"SrlinuxRepository": "${NamePrefix}-debug-lab-srlinux", "MultitoolRepository": "${NamePrefix}-debug-lab-multitool",
          "TrexRepository": "${NamePrefix}-debug-lab-trex", "TelegrafRepository": "${NamePrefix}-debug-telegraf"}
      and all(r["ImageTagMutability"] == "IMMUTABLE" and r["EmptyOnDelete"] is True and r["ImageScanningConfiguration"] == {"ScanOnPush": True}
              for r in repos.values()))
_bkt = res["Bucket"]["Properties"]
check("バケットは <接頭辞>-lab-debug-<アカウント>で、公開を全部止めて暗号化し、暗号化されていない経路を拒む",
      _bkt["BucketName"] == {"Fn::Sub": "${NamePrefix}-lab-debug-${AWS::AccountId}"}
      and all(_bkt["PublicAccessBlockConfiguration"].values()) and len(_bkt["PublicAccessBlockConfiguration"]) == 4
      and res["BucketPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]["Sid"] == "DenyInsecureTransport")
check("lab-debug.sh のバケットとリポジトリの名前はテンプレートと同じ作り方（down は Outputs を読まずに空にできる）",
      'BUCKET="$PREFIX-lab-debug-$ACCOUNT_ID"' in dbg and 'REPO_PREFIX="$PREFIX-debug"' in dbg
      and cfn["Outputs"]["RepositoryPrefix"]["Value"]["Fn::Sub"].endswith("/${NamePrefix}-debug")
      and 'mirror_lab_images "$REG" "$REPO_PREFIX"' in dbg and 'build_telegraf "$REG/$REPO_PREFIX-telegraf:$TELEGRAF_TAG" linux/amd64' in dbg)
check("EC2 は CreateInstance=true のときだけ作り、そのときは TelegrafImageTag が要る（Rules）。エンドポイントができてから起こす",
      res["Instance"]["Condition"] == "HasInstance" and cfn["Conditions"]["HasInstance"] == {"Fn::Equals": [{"Ref": "CreateInstance"}, "true"]}
      and cfn["Rules"]["TelegrafTagWithInstance"]["Assertions"][0]["Assert"] == {"Fn::Not": [{"Fn::Equals": [{"Ref": "TelegrafImageTag"}, ""]}]}
      and {"SsmEndpoint", "SsmMessagesEndpoint", "EcrApiEndpoint", "EcrDkrEndpoint", "S3Endpoint", "SubnetRouteTable"} <= set(res["Instance"]["DependsOn"])
      and all(cfn["Outputs"][k]["Condition"] == "HasInstance" for k in ("InstanceId", "StartSessionCommand")))
_up_case = dbg.split("\n  up)\n")[1]
check("lab-debug.sh up は初回に EC2 の無い器を作り、イメージと app/containerlab/ を置いてから EC2 を作る",
      _up_case.index("deploy false") < _up_case.index("mirror_lab_images") < _up_case.index('upload_lab "$BUCKET"') < _up_case.index('deploy true "$TELEGRAF_TAG"'))
_down_case = dbg.split("\n  down)\n")[1].split(";;")[0]
check("lab-debug.sh down はバケットを空にしてからスタックを消し、消えるのを待つ",
      _down_case.index('aws s3 rm --only-show-errors --recursive "s3://$BUCKET"') < _down_case.index("delete-stack")
      < _down_case.index("wait stack-delete-complete"))

# ---- EC2（instance.tf と同じ守り）
inst = res["Instance"]["Properties"]
check("IMDSv2 必須・hop limit 1（コンテナから IMDS に届かせない）。instance.tf と同じ",
      inst["MetadataOptions"] == {"HttpEndpoint": "enabled", "HttpTokens": "required", "HttpPutResponseHopLimit": 1}
      and 'http_tokens   = "required"' in read("IaC", "terraform", "aws-managed", "pipeline", "lab", "instance.tf")
      and "http_put_response_hop_limit = 1" in read("IaC", "terraform", "aws-managed", "pipeline", "lab", "instance.tf"))
ebs = inst["BlockDeviceMappings"][0]["Ebs"]
check("ルートは gp3・暗号化・EC2 と一緒に消える。パブリック IP は付けない",
      ebs["VolumeType"] == "gp3" and ebs["Encrypted"] is True and ebs["DeleteOnTermination"] is True
      and "NetworkInterfaces" not in inst and "AssociatePublicIpAddress" not in str(inst))
check("Project と owner のタグ（Terraform の default_tags と同じ）を EC2 とロールに付ける",
      all({t["Key"] for t in res[r]["Properties"]["Tags"]} >= {"Name", "Project", "owner"} for r in ("Instance", "Role")))

# ---- UserData（tftpl と同じ形）
ud = inst["UserData"]["Fn::Base64"]["Fn::Sub"]
TF2CFN = {"name_prefix": "NamePrefix", "region": "AWS::Region", "account_id": "AWS::AccountId", "bucket": "Bucket",
          "containerlab_version": "ContainerlabVersion", "srlinux_image_tag": "SrlinuxImageTag",
          "multitool_image_tag": "MultitoolImageTag", "trex_image_tag": "TrexImageTag", "auto_start_lab": "AutoStartLab"}
def body(s):  # コメントと、2 つで違ってよい行（TELEGRAF_IMAGE と、案内のコマンド名）を外し、リポジトリの名前（-debug- が付く）をそろえる
    s = re.sub(r"\$\{(\w+)\}", lambda m: "${" + TF2CFN.get(m.group(1), m.group(1)) + "}", s).replace("${NamePrefix}-debug-lab-", "${NamePrefix}-lab-")
    return [l for l in s.strip().splitlines()
            if not (l.startswith("# ") or l.startswith("TELEGRAF_IMAGE=") or "is not in s3://" in l)]
check("UserData は terraform の user_data（tftpl）と、変数の置き換えとリポジトリの名前と TELEGRAF_IMAGE の行のほかは同じ",
      body(tftpl) == body(ud) and "/${NamePrefix}-debug-lab-srlinux:${SrlinuxImageTag}\n" in ud and "/${NamePrefix}-debug-lab-multitool:${MultitoolImageTag}\n" in ud
      and "/${NamePrefix}-debug-lab-trex:${TrexImageTag}\n" in ud)
check("UserData の ${…} はパラメータか疑似パラメータかこのスタックのバケットだけ（シェルの変数は $LAB と書く）",
      all(v in params or v.startswith("AWS::") or v == "Bucket" for v in re.findall(r"\$\{([\w:]+)\}", ud)))
check("TELEGRAF_IMAGE は lab の EC2 では空、デバッグ用の EC2 ではこのスタックのリポジトリ（<接頭辞>-debug-telegraf。stream と同じ作り方のイメージ）",
      "\nTELEGRAF_IMAGE=\n" in tftpl and "\nTELEGRAF_IMAGE=${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/${NamePrefix}-debug-telegraf:${TelegrafImageTag}\n" in ud)
env_keys = re.findall(r"^(\w+)=", tftpl.split("<<'__ENV__'")[1].split("__ENV__")[0], re.M)
check("env のキーは 2 つの user_data と setup.sh の頭の一覧で同じ",
      env_keys == re.findall(r"^(\w+)=", ud.split("<<'__ENV__'")[1].split("__ENV__")[0], re.M)
      and env_keys == [k.strip() for k in re.search(r"^# env のキー: (.*)$", setup, re.M).group(1).split("/")])
check("どちらも起動のたびに流し（cloud-config の always）、app/containerlab/ を置き直して setup.sh を exec する",
      all("- [scripts-user, always]" in s and "exec bash $LAB/src/setup.sh" in s and "aws s3 sync --delete" in s for s in (tftpl, ud)))
check("lab/ の置き場は upload_lab の宛先と同じ（s3://<バケット>/lab/）",
      'aws s3 sync --only-show-errors --delete app/containerlab/ "s3://$1/lab/"' in common and "s3://${bucket}/lab/ $LAB/src/" in tftpl and "s3://${Bucket}/lab/ $LAB/src/" in ud)
# upload_lab の sync は --delete（手元で消した・改名した srlinux/*.cli を S3 に残さない。EC2 も --delete で読むので、残ると古い機器名が戻って lab logs が止まる）。
# --exclude に当たるものは S3 側でも消さない（aws s3 sync の --delete の説明）。別の cp で置く rpm を除かないと、毎回 sync が消して cp が置き直す
_ul = re.search(r"^upload_lab\(\) \{.*?^\}", common, re.M | re.S).group(0)
_ul_sync = [l for l in _ul.splitlines() if l.strip().startswith("aws s3 sync ")]
_ul_ex = lambda l: set(re.findall(r'--exclude "([^"]+)"', l))
check("upload_lab の sync は --delete 付きで、除くのは描いた splab.clab.yml・__pycache__・.DS_Store と、下の cp で置く rpm（$CONTAINERLAB_RPM）",
      len(_ul_sync) == 1 and " --delete " in _ul_sync[0]
      and _ul_ex(_ul_sync[0]) == {"splab.clab.yml", "__pycache__/*", "*.DS_Store", "$CONTAINERLAB_RPM"}
      and 'aws s3 cp --only-show-errors "$CONTAINERLAB_RPM" "s3://$1/lab/"' in _ul)
_ulc = re.search(r'output "upload_lab_command" \{.*?value\s*=\s*"(.*)"\n', read("IaC", "terraform", "aws-managed", "pipeline", "lab", "outputs.tf"), re.S).group(1).replace('\\"', '"')
check("lab の output upload_lab_command も同じ --delete と同じ除外（rpm は var.containerlab_version の名前）",
      _ulc.startswith("aws s3 sync --delete app/containerlab/ ")
      and _ul_ex(_ulc.split(" && ")[0]) == {"splab.clab.yml", "__pycache__/*", "*.DS_Store", "containerlab_${var.containerlab_version}_linux_amd64.rpm"}
      and sh_const(common, "CONTAINERLAB_RPM") == "containerlab_${CONTAINERLAB_VERSION}_linux_amd64.rpm")
check("setup.sh は TELEGRAF_IMAGE があるときだけ Telegraf のユニットを作り、無ければ消す（lab の EC2 には残さない）",
      re.search(r'if \[ -n "\$\{TELEGRAF_IMAGE:-\}" \]; then\n\s*cat > "\$TG_UNIT"[\s\S]*?ExecStart=\$SRC/lab\.sh telegraf run[\s\S]*?else\n\s*systemctl disable --now[\s\S]*?rm -f "\$TG_UNIT"', setup) is not None)
check("setup.sh は lab のユニットを tftpl の前の版と同じ中身で作る（lab.sh up / down、20 分待つ）",
      "ExecStart=$SRC/lab.sh up" in setup and "ExecStop=$SRC/lab.sh down" in setup and "TimeoutStartSec=1200" in setup)

# ---- Telegraf: 同じ telegraf.conf.in を SINK で描き分ける
def render(sink, **extra):
    with tempfile.TemporaryDirectory() as d:
        env = {"PATH": os.environ["PATH"], "TELEGRAF_TEMPLATE": os.path.join(ROOT, "app", "telegraf", "telegraf.conf.in"),
               "TELEGRAF_CONF": os.path.join(d, "telegraf.conf"), "AWS_REGION": "ap-northeast-1", **({"SINK": sink} if sink else {}), **extra}
        r = subprocess.run(["bash", os.path.join(ROOT, "app", "telegraf", "telegraf.sh"), "render"], capture_output=True, text=True, env=env)
        if r.returncode != 0:
            return None, r.stderr, os.listdir(d)
        with open(env["TELEGRAF_CONF"], "rb") as f:
            return tomllib.load(f), r.stdout, os.listdir(d)
kafka, _, kafka_files = render(None, KAFKA_BROKERS="b-1.example:9098,b-2.example:9098")
stdout_conf, out, stdout_files = render("stdout")
check("SINK の既定は kafka（MSK）。outputs.kafka が 1 つ（traps。logs と mdt は cycle 012、metrics と gnmi は cycle 013 で外した）で outputs.file は無い", kafka is not None
      and len(kafka["outputs"]["kafka"]) == 1 and "file" not in kafka["outputs"] and "aws_config" in kafka_files)
check("SINK=stdout は KAFKA_BROKERS が無くても描け、outputs.kafka が無く outputs.file（stdout / json）だけ。aws_config も書かない",
      stdout_conf is not None and "kafka" not in stdout_conf["outputs"]
      and stdout_conf["outputs"]["file"] == [{"files": ["stdout"], "data_format": "json", "json_timestamp_units": "1s"}]
      and "aws_config" not in stdout_files and "sink: stdout" in out)
check("入力（trap）と agent と health は kafka と stdout で同じ",
      stdout_conf["inputs"] == kafka["inputs"] and stdout_conf["agent"] == kafka["agent"]
      and stdout_conf["outputs"]["health"] == kafka["outputs"]["health"])
check("stdout の json は MSK に載るもの（outputs.kafka）と同じ形（秒の timestamp）",
      all(o.get("data_format") == "json" and o.get("json_timestamp_units") == "1s" for o in kafka["outputs"]["kafka"]))
check("知らない SINK と、kafka で KAFKA_BROKERS が無いのは描かずに止まる",
      render("s3")[0] is None and render("kafka")[0] is None)
check("Telegraf は syslog も MDT も受けない（syslog は syslog-ng。cycle 012）: inputs.syslog / inputs.cisco_telemetry_mdt が無く、SYSLOG_STANDARD / LOG_PORT / MDT_PORT は見ない",
      all("syslog" not in c["inputs"] and "cisco_telemetry_mdt" not in c["inputs"] for c in (kafka, stdout_conf))
      and all(render("stdout", **{k: v})[0] == stdout_conf for k, v in (("SYSLOG_STANDARD", "rfc3164"), ("LOG_PORT", "x"), ("MDT_PORT", "x"))))
tpl = read("app", "telegraf", "telegraf.conf.in")
check("telegraf.conf.in の出力の区間は telegraf.sh の SINKS と同じ名前で、開きと閉じが対になる",
      sorted(re.findall(r"^# >>> sink (\w+)", tpl, re.M)) == sorted(re.findall(r"^# <<< sink (\w+)", tpl, re.M))
      == sorted(re.search(r'^SINKS="([^"]*)"', tg_sh, re.M).group(1).split()))
# Kafka の認証（cycle 005）: 既定 iam は MSK の IAM（今まで通り）。none は OSS 版の Kafka（PLAINTEXT）向けに outputs.kafka の IAM の行を消す
_IAM_KEYS = ("enable_tls", "sasl_mechanism", "sasl_aws_msk_iam_region", "sasl_aws_msk_iam_profile")
_auth_blks = re.findall(r"^# >>> kafka_auth iam\n(.*?)^# <<< kafka_auth iam\n", tpl, re.M | re.S)
check("telegraf.conf.in の「>>> kafka_auth iam」の区間は outputs.kafka ごとに 1 つ（traps の 1 つ）で、IAM の 4 行だけを囲む",
      len(_auth_blks) == 1 == tpl.count("# >>> kafka_auth iam") == tpl.count("# <<< kafka_auth iam") == tpl.count("[[outputs.kafka]]")
      and all([l.split("=", 1)[0].strip() for l in b.splitlines()] == list(_IAM_KEYS) for b in _auth_blks)
      and not re.search(r"^\s*(sasl_|enable_tls)", re.sub(r"^# >>> kafka_auth iam\n.*?^# <<< kafka_auth iam\n", "", tpl, flags=re.M | re.S), re.M))
_iam, out_iam, iam_files = render(None, KAFKA_BROKERS="b-1.example:9098", KAFKA_AUTH="iam")
_none, out_none, none_files = render(None, KAFKA_BROKERS="kafka-1.example:9092", KAFKA_AUTH="none")
check("KAFKA_AUTH の既定は iam: outputs.kafka に TLS と MSK の IAM の 4 行があり、aws_config を書く。iam を明示しても同じ。ログに kafka auth は出ない",
      sh_const(tg_sh, "KAFKA_AUTH") == "${KAFKA_AUTH:-iam}"
      and all(o["enable_tls"] is True and o["sasl_mechanism"] == "AWS-MSK-IAM" and o["sasl_aws_msk_iam_region"] == "ap-northeast-1"
              and o["sasl_aws_msk_iam_profile"] == "default" for o in kafka["outputs"]["kafka"])
      and _iam is not None and _iam["outputs"]["kafka"] == [dict(o, brokers=["b-1.example:9098"]) for o in kafka["outputs"]["kafka"]]
      and "aws_config" in iam_files and "kafka auth" not in out_iam)
check("KAFKA_AUTH=none は outputs.kafka から TLS と SASL の行だけを消し（ほかのキーは iam と同じ）、aws_config を書かない。ログに kafka auth: none",
      _none is not None and len(_none["outputs"]["kafka"]) == 1
      and all(set(_IAM_KEYS).isdisjoint(o) for o in _none["outputs"]["kafka"])
      and [dict(o, brokers=None) for o in _none["outputs"]["kafka"]]
      == [dict({k: v for k, v in o.items() if k not in _IAM_KEYS}, brokers=None) for o in _iam["outputs"]["kafka"]]
      and all(o["brokers"] == ["kafka-1.example:9092"] for o in _none["outputs"]["kafka"])
      and _none["inputs"] == _iam["inputs"] and _none["agent"] == _iam["agent"]
      and "aws_config" not in none_files and "kafka auth: none" in out_none)
check("知らない KAFKA_AUTH（plaintext / 大文字の IAM）は描かずに止まる。SINK=stdout では KAFKA_AUTH を見ない",
      render(None, KAFKA_BROKERS="b:9092", KAFKA_AUTH="plaintext")[0] is None
      and render(None, KAFKA_BROKERS="b:9092", KAFKA_AUTH="IAM")[0] is None
      and render("stdout", KAFKA_AUTH="none")[0] == stdout_conf)
# 受けるのは trap だけ（cycle 013。gNMI の購読と SNMP のポーリング、入力を分けていた TELEGRAF_ROLE と Starlark はやめ、gNMI は gnmic が取る）
check("入力は trap だけ（inputs.snmp_trap 1 つ）で、processors / aggregators（Starlark）は無い。kafka の出力は traps トピック 1 つで namepass は snmp_trap",
      all(sorted(c["inputs"]) == ["snmp_trap"] and len(c["inputs"]["snmp_trap"]) == 1
          and "processors" not in c and "aggregators" not in c for c in (kafka, stdout_conf))
      and [o["topic"] for o in kafka["outputs"]["kafka"]] == ["traps"]
      and [o["namepass"] for o in kafka["outputs"]["kafka"]] == [["snmp_trap"]]
      and "trap: 1162/udp" in out and not any(w in out for w in ("gnmi:", "snmp poll", "role:", "mdt:")))
check("telegraf.conf.in に取りにいく入力（inputs.snmp / inputs.gnmi）・Starlark・役割と snmp_poll の区間・__SNMP_AGENTS__ / __GNMI_TARGETS__ が無い",
      not any(w in tpl for w in ("[[inputs.snmp]]", "[[inputs.gnmi]]", "starlark", "# >>> role", "# <<< role", "# >>> snmp_poll", "# <<< snmp_poll",
                                 "__SNMP_AGENTS__", "__GNMI_TARGETS__")))
_tg_code = "\n".join(l for l in tg_sh.splitlines() if not l.lstrip().startswith("#"))
_old_env = {"SNMP_POLL": "1", "SNMP_AGENTS": '"udp://203.0.113.11:161"', "GNMI_TARGETS": '"203.0.113.11:57400"', "TELEGRAF_ROLE": "dialin",
            "GNMI_USERNAME": "u", "GNMI_PASSWORD": "p", "SNMP_COMMUNITY": "c"}
check("cycle 013 より前の環境変数（SNMP_POLL / SNMP_AGENTS / GNMI_TARGETS / TELEGRAF_ROLE / 機器の認証情報）は見ない: 残っていても描く設定は変わらず、空や知らない値でも止まらない",
      render("stdout", **_old_env)[0] == stdout_conf
      and render("stdout", SNMP_POLL="yes", TELEGRAF_ROLE="dial-in", SNMP_AGENTS="bad", GNMI_TARGETS="")[0] == stdout_conf
      and render(None, KAFKA_BROKERS="b-1.example:9098,b-2.example:9098", **_old_env)[0] == kafka
      and not any(re.search(r"\b%s\b" % k, _tg_code) for k in ("SNMP_POLL", "SNMP_AGENTS", "GNMI_TARGETS", "TELEGRAF_ROLE", "ROLES",
                                                                 "GNMI_USERNAME", "GNMI_PASSWORD", "SNMP_COMMUNITY")))
def _tg(cmd):  # telegraf を呼ぶ前に止まるので、手元に telegraf は無くてよい
    with tempfile.TemporaryDirectory() as d:
        env = {"PATH": os.environ["PATH"], "TELEGRAF_TEMPLATE": os.path.join(ROOT, "app", "telegraf", "telegraf.conf.in"),
               "TELEGRAF_CONF": os.path.join(d, "telegraf.conf"), "AWS_REGION": "ap-northeast-1", "SINK": "stdout"}
        return subprocess.run(["bash", os.path.join(ROOT, "app", "telegraf", "telegraf.sh"), cmd], capture_output=True, text=True, env=env), os.listdir(d)
_tg_out = [_tg(c) for c in ("test", "gnmi")]
check("tg test / tg gnmi は cycle 013 でやめた: 設定を描かず telegraf も呼ばずに、gnmic のタスクの gn get（stream の output の gnmic_exec_command）を案内して 1 で止まる",
      all(r.returncode == 1 and "cycle 013 でやめた" in r.stderr and "gnmic_exec_command" in r.stderr and "gn get" in r.stderr
          and "telegraf: command not found" not in r.stderr and r.stdout == "" and files == [] for r, files in _tg_out))

# 機器の認証情報: Telegraf は持たない（gNMI の認証は gnmic が SSM の SecureString / compose の環境から受ける。trap の community は見ない）
check("telegraf.conf.in は機器の認証情報を持たない: ${GNMI_USERNAME} / ${GNMI_PASSWORD} / ${SNMP_COMMUNITY} も値（NokiaSrl1 / public）も community の行も無い",
      not any(w in tpl for w in ("NokiaSrl1", "public", "${GNMI_USERNAME}", "${GNMI_PASSWORD}", "${SNMP_COMMUNITY}", "community")))
check("lab.sh は gNMI の認証情報を持たず、SNMP_COMMUNITY=public は trap-test の snmptrap のためだけに残す。lab-common.sh は LAB_GNMI_* だけ持ち、LAB_SNMP_COMMUNITY は無い",
      sh_const(lab_sh, "GNMI_USERNAME") is None and sh_const(lab_sh, "GNMI_PASSWORD") is None
      and sh_const(lab_sh, "SNMP_COMMUNITY") == "public" and lab_sh.count("$SNMP_COMMUNITY") == 1 and 'snmptrap -v2c -c "$SNMP_COMMUNITY"' in lab_sh
      and sh_const(common, "LAB_GNMI_USERNAME") == "admin" and sh_const(common, "LAB_GNMI_PASSWORD") == "'NokiaSrl1!'"
      and sh_const(common, "LAB_SNMP_COMMUNITY") is None)

# ---- lab.sh: この EC2 の Telegraf
check("trap のポートは lab.sh と telegraf.sh で同じ（機器は 162 に送り、デバッグ用の EC2 は REDIRECT で Telegraf の待つポートへ）",
      "${TRAP_PORT:-" + sh_const(lab_sh, "TRAP_PORT") + "}" == sh_const(tg_sh, "TRAP_PORT") == "${TRAP_PORT:-1162}"
      and re.search(r'iptables -t nat -I PREROUTING 1 -s "\$MGMT" -d "\$MGMT_GW" -p udp --dport 162 "\$\{c\[@\]\}" -j REDIRECT --to-ports "\$TRAP_PORT"', lab_sh) is not None)
check("syslog は Telegraf が受けない（telegraf.sh に LOG_PORT / SYSLOG_STANDARD が無い。syslog-ng が受ける。cycle 012）。lab.sh の LOG_PORT は SR Linux の送り先のポートとして 5140 のまま",
      sh_const(lab_sh, "LOG_PORT") == "5140" and sh_const(tg_sh, "LOG_PORT") is None and sh_const(tg_sh, "SYSLOG_STANDARD") is None)
check("lab の SR Linux の syslog の形式は lab.sh の LOG_STANDARD = lab-common.sh の LAB_SYSLOG_STANDARD = RFC5424",
      sh_const(lab_sh, "LOG_STANDARD") == sh_const(common, "LAB_SYSLOG_STANDARD") == "RFC5424")
check("lab.sh telegraf run は同じイメージを host ネットワークで SINK=stdout で起こす（trap だけなので購読先・ポーリング先・機器の認証情報は渡さない。cycle 013）",
      re.search(r"docker run -d --name \"\$TG\" --restart unless-stopped --network host [^\n]*\\\n\s*-e SINK=stdout -e SYSLOG_STANDARD=\"\$LOG_STANDARD\" -e AWS_REGION \"\$TELEGRAF_IMAGE\" run >/dev/null\n", lab_sh) is not None
      and "--snmp-agents" not in lab_sh and "--gnmi-targets" not in lab_sh and "--snmp-agents" not in up
      and "app/containerlab/lab_topology.py app/containerlab --gnmi-targets" in up)
check("lab.sh telegraf test / gnmi は cycle 013 でやめた: Telegraf を触らず、PC から gnmic_exec_command（gn get）を打つよう言って 1 で止まる",
      re.search(r'test \| gnmi\)\n\s*echo "lab telegraf \$2 は cycle 013 でやめた[^"]*gnmic_exec_command[^"]*" >&2\n\s*exit 1\n', lab_sh) is not None)
check("lab.sh の forward は TELEGRAF_IMAGE があれば SSM の NLB を見ずに抜ける（デバッグ用の EC2 は stream を使わない。手元の compose の TELEGRAF_LOCAL=1 も同じ分岐。tests/test_local_compose.py が動かして見る）",
      re.search(r'forward\)\n\s*if local_telegraf; then[\s\S]*?exit 0\n\s*fi', lab_sh) is not None
      and 'local_telegraf() { [ -n "${TELEGRAF_IMAGE:-}" ] || ' in lab_sh)
check("lab.sh pull は lab の 3 つ（SR Linux / multitool / TRex）を引き、TELEGRAF_IMAGE があるときだけ Telegraf も引く",
      'for i in "$SRLINUX_IMAGE" "$MULTITOOL_IMAGE" "$TREX_IMAGE" ${TELEGRAF_IMAGE:+"$TELEGRAF_IMAGE"}; do docker pull -q "$i"; done' in lab_sh)

# ---- ops: up.sh / down.sh とは別（2026-10-04 ユーザー決定）
def _code(src):  # コメント行と echo の案内を外した、実際に動く行
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#") and "echo " not in l)
check("ops/up.sh / ops/down.sh はデバッグ用の EC2 を作らない・消さない・見ない",
      not any(w in _code(src) for src in (up, down) for w in ("lab-debug.sh", "lab-debug\"", "cfn_stack_status", "cloudformation", "LAB_DEBUG_STACK", "flag_value LAB_DEBUG")))
check("LAB_DEBUG は使わない。deploy.env に残っていれば up.sh が注意を出すだけ（deploy.env.example には無い）",
      "LAB_DEBUG" not in read("deploy.env.example")
      and re.search(r'case "\$\{LAB_DEBUG:-\}" in \'\'\|0\|false\|no\) ;; \*\) echo "注意: LAB_DEBUG は使わない。[^"]*ops/lab-debug\.sh up / down', up) is not None)
_epblk = up[up.index('ENDPOINTS=""'):up.index('echo "インターフェース型エンドポイント')]
def _endpoints(roots, **env):
    r = subprocess.run(["bash", "-c", f'ROOTS="{roots}"\n' + _epblk + 'echo "OUT: $ENDPOINTS"'],
                       capture_output=True, text=True, env={"PATH": os.environ["PATH"], **env})
    return r.stdout.strip().splitlines()[-1]
check("土台だけなら up.sh は ECR のエンドポイントを作らない（LAB_DEBUG=1 が残っていても。デバッグ用の EC2 は自分の VPC のを使う）",
      _endpoints("base/ecr base/core", LAB_DEBUG="1") == "OUT: ssm ssmmessages" == _endpoints("base/ecr base/core"))
def _stack_status(aws_out, aws_rc):
    """cfn_stack_status を、決まった出力と終了コードを返す偽の aws で打つ。(stdout, stderr, 終了コード)"""
    with tempfile.TemporaryDirectory() as d:
        fake = os.path.join(d, "aws")
        with open(fake, "w") as f:
            f.write(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(aws_out)} >&2\nexit {aws_rc}\n" if aws_rc else f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(aws_out)}\n")
        os.chmod(fake, 0o755)
        r = subprocess.run(["bash", "-c", 'REGION=ap-northeast-1; . ops/lab-common.sh; cfn_stack_status x-lab-debug'],
                           capture_output=True, text=True, cwd=ROOT, env={"PATH": d + os.pathsep + os.environ["PATH"]})
    return r.stdout.strip(), r.stderr.strip(), r.returncode
_ok, _gone, _err = (_stack_status("CREATE_COMPLETE", 0),
                    _stack_status("An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id x-lab-debug does not exist", 254),
                    _stack_status("An error occurred (ExpiredToken) when calling the DescribeStacks operation: token expired", 254))
check("cfn_stack_status は「無い」（空・0）と「読めない」（認証切れなど。1）を分ける（読めないのを無いと扱うと、lab-debug.sh down が消さずに終わり、up が器を作り直そうとする）",
      _ok == ("CREATE_COMPLETE", "", 0) and _gone == ("", "", 0) and _err[0] == "" and _err[2] == 1 and "ExpiredToken" in _err[1]
      and "s=$(stack_status)" in dbg and '"$(stack_status)"' not in dbg)
check("スタック名は <接頭辞>-lab-debug で、ロールとインスタンスプロファイルは lab の EC2（<接頭辞>-lab）と別の名前",
      'STACK="$PREFIX-lab-debug"' in dbg and role["RoleName"] == {"Fn::Sub": "${NamePrefix}-lab-debug"}
      and res["InstanceProfile"]["Properties"]["InstanceProfileName"] == {"Fn::Sub": "${NamePrefix}-lab-debug"})
# ---- dir_tag（Dockerfile は docker/images/<名前>/ にあり context の外なので、3 つ目の引数で渡してハッシュに入れる）
def _dir_tag(cwd, *args):
    r = subprocess.run(["bash", "-c", f'PY=(python3); . {shlex.quote(os.path.join(ROOT, "ops", "lab-common.sh"))}; dir_tag "$@"', "_", *args],
                       capture_output=True, text=True, cwd=cwd)
    return r.stdout.strip(), r.returncode
with tempfile.TemporaryDirectory() as d:
    os.makedirs(os.path.join(d, "app", "x")); os.makedirs(os.path.join(d, "docker", "images", "x"))
    with open(os.path.join(d, "app", "x", "a.txt"), "w") as f:
        f.write("a\n")
    df_path = os.path.join("docker", "images", "x", "Dockerfile")
    with open(os.path.join(d, df_path), "w") as f:
        f.write("FROM scratch\n")
    _t0, _t1 = _dir_tag(d, "1", "app/x"), _dir_tag(d, "1", "app/x", df_path)
    _t1b = _dir_tag(d, "1", "app/x", df_path)
    with open(os.path.join(d, df_path), "a") as f:
        f.write("# x\n")
    _t2 = _dir_tag(d, "1", "app/x", df_path)
    _missing = _dir_tag(d, "1", "app/none", df_path)
check("dir_tag は 3 つ目からのファイルもハッシュに入れる（Dockerfile だけ変えてもタグが変わり、同じ中身なら同じタグ。ディレクトリが無ければ失敗）",
      all(rc == 0 and re.fullmatch(r"1-[0-9a-f]{12}", t) for t, rc in (_t0, _t1, _t2))
      and _t1 == _t1b and _t0[0] != _t1[0] and _t1[0] != _t2[0] and _missing[1] != 0)
_tag_calls = [m for n in ("lab-common.sh", "up-common.sh", "up.sh") for m in re.findall(r'^[^#\n]*?\bdir_tag "\$\w+" ([^)\n;]*)', read("ops", n), re.M)] \
    + [m for n in ("up.sh", "oss-images.sh") for m in re.findall(r'^[^#\n]*?\bdir_tag "\$\w+" ([^)\n;]*)', read("ops", "oss", n), re.M)]
_tag_df = [re.fullmatch(r'(?:app/([\w-]+)|"\$NAUTOBOT_CTX") docker/images/([\w-]+)/Dockerfile\s*', c) for c in _tag_calls]
_builds = "".join(read(*p) for p in (("ops", "lab-common.sh"), ("ops", "up-common.sh"), ("ops", "oss", "oss-images.sh")))
check("dir_tag の呼び元 12 か所は、どれも docker build の -f と同じ docker/images/<名前>/Dockerfile を渡す（context が app/<名前>/ ならその名前と同じ。"
      "syslog-ng は cycle 012、gnmic は cycle 013 で ops/up.sh と ops/oss/up.sh に足した）",
      len(_tag_calls) == 12 and all(_tag_df)
      and all(m.group(1) in (None, m.group(2)) for m in _tag_df)
      and {m.group(2) for m in _tag_df} == {"telegraf", "splunk", "grafana", "nautobot", "spark", "neo4j", "syslog-ng", "gnmic"}
      and all(f"-f docker/images/{m.group(2)}/Dockerfile " in _builds for m in _tag_df))

# ---- lab.sh graph / graph-stop（cycle 010。containerlab graph を 127.0.0.1:50080 で裏に起こし、手元のポートフォワードで開く）
# lab.sh を一時ディレクトリに写し（src/ の代わり。splab.clab.yml があるので render しない）、偽の systemd-run / systemctl / containerlab / curl を PATH の先に置いて打つ
def _lab_graph(*args, active=False, **env):
    with tempfile.TemporaryDirectory() as d:
        b = os.path.join(d, "bin"); os.mkdir(b)
        log = os.path.join(d, "calls.log"); open(log, "w").close()
        bodies = {"systemd-run": "", "containerlab": "",
                  "systemctl": f'[ "${{1:-}}" = is-active ] && exit {0 if active else 3}\n',
                  "curl": 'case " $* " in *" PUT "*) echo tok ;; *) echo i-0123456789abcdef0 ;; esac\n'}
        for n, body in bodies.items():
            with open(os.path.join(b, n), "w") as f:
                f.write('#!/usr/bin/env bash\n{ printf %s "$(basename "$0")"; printf " %s" "$@"; echo; } >> "$FAKE_LOG"\n' + body)
            os.chmod(os.path.join(b, n), 0o755)
        with open(os.path.join(d, "lab.sh"), "w") as f:
            f.write(lab_sh)
        os.chmod(os.path.join(d, "lab.sh"), 0o755)
        open(os.path.join(d, "splab.clab.yml"), "w").close()
        e = {k: v for k, v in os.environ.items() if k not in ("NAME_PREFIX", "AWS_REGION", "REGISTRY", "TELEGRAF_IMAGE")}
        e.update(PATH=b + os.pathsep + os.environ["PATH"], FAKE_LOG=log, **env)
        r = subprocess.run([os.path.join(d, "lab.sh"), *args], env=e, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        return r, open(log, encoding="utf-8").read().splitlines(), os.path.realpath(d)
_ge = {"NAME_PREFIX": "x-nwc-poc", "AWS_REGION": "ap-northeast-1"}
_fwd = ("aws ssm start-session --region ap-northeast-1 --target i-0123456789abcdef0 --document-name AWS-StartPortForwardingSession "
        "--parameters portNumber=50080,localPortNumber=50080")
_r, _c, _d = _lab_graph("graph", **_ge)
_run = [c for c in _c if c.startswith("systemd-run ")]
check("lab.sh graph: systemd-run の一時ユニット（<接頭辞>-lab-graph）で containerlab graph を 127.0.0.1:50080 で起こし、手元で打つポートフォワードのコマンドを出す",
      _r.returncode == 0 and _run == [f"systemd-run --unit=x-nwc-poc-lab-graph --collect --property=WorkingDirectory={_d} --setenv=CLAB_VERSION_CHECK=disable "
                                      "containerlab graph -t splab.clab.yml --srv 127.0.0.1:50080"]
      and "systemctl is-active --quiet x-nwc-poc-lab-graph" in _c and _fwd in _r.stdout and "http://localhost:50080/" in _r.stdout
      # 開けないときの案内は journalctl -u（--collect の一時ユニットはすぐ落ちると消え、systemctl status は could not be found になる。024 B）
      and "sudo journalctl -u x-nwc-poc-lab-graph" in _r.stdout and "systemctl status" not in _r.stdout)
_r, _c, _ = _lab_graph("graph", active=True, **_ge)
check("lab.sh graph: もう動いていれば systemd-run を打たず（同じ名前のユニットは作れない）、案内とコマンドだけ出す",
      _r.returncode == 0 and not any(c.startswith("systemd-run ") for c in _c) and "もう動いている" in _r.stdout and _fwd in _r.stdout)
_r, _c, _ = _lab_graph("graph", AWS_REGION="ap-northeast-1")
check("lab.sh graph: NAME_PREFIX が無い（/etc/*-lab.env の無い手元）なら何も起こさずに止まる",
      _r.returncode != 0 and _c == [] and "lab の EC2 だけ" in _r.stderr)
_r, _c, _ = _lab_graph("graph-stop", **_ge)
check("lab.sh graph-stop: systemctl stop <接頭辞>-lab-graph を打つ（動いていなくても失敗にしない）",
      _r.returncode == 0 and _c == ["systemctl stop x-nwc-poc-lab-graph"])
_r, _c, _ = _lab_graph("down", **_ge)
check("lab.sh down: graph-stop で図を止めてから containerlab destroy する",
      _r.returncode == 0 and _c == ["systemctl stop x-nwc-poc-lab-graph", "containerlab destroy -t splab.clab.yml --cleanup"])
_r, _c, _ = _lab_graph("down")
check("lab.sh down: NAME_PREFIX が無ければ（手元の compose）systemctl を打たずに destroy だけ",
      _r.returncode == 0 and _c == ["containerlab destroy -t splab.clab.yml --cleanup"])
# ---- lab.sh logs / trex stop / trex status（011 Round 2）。lab.sh・テンプレート・srlinux/*.cli を一時ディレクトリに写し、偽の docker を PATH の先に置く。
# srlinux/ には S3 に残った古い .cli（dc1-leaf-01。011 で dc1-a-leaf-01 に改名した名前）を 1 本混ぜる
def _lab_docker(*args, running=False):
    with tempfile.TemporaryDirectory() as d:
        b = os.path.join(d, "bin"); os.mkdir(b)
        log = os.path.join(d, "calls.log"); open(log, "w").close()
        with open(os.path.join(b, "docker"), "w") as f:
            f.write('#!/usr/bin/env bash\n{ printf docker; printf " %s" "$@"; echo; } >> "$FAKE_LOG"\n'
                    'case " $* " in *" pgrep "*|*" pkill "*) [ -n "${FAKE_RUNNING:-}" ] || exit 1; echo "42 /bin/bash ./t-rex-64 -i" ;; esac\n')
        os.chmod(os.path.join(b, "docker"), 0o755)
        with open(os.path.join(d, "lab.sh"), "w") as f:
            f.write(lab_sh)
        os.chmod(os.path.join(d, "lab.sh"), 0o755)
        with open(os.path.join(d, "splab.clab.yml.in"), "w") as f:
            f.write(read("app", "containerlab", "splab.clab.yml.in"))
        open(os.path.join(d, "splab.clab.yml"), "w").close()
        os.mkdir(os.path.join(d, "srlinux"))
        for n in [*sorted(os.listdir(os.path.join(ROOT, "app", "containerlab", "srlinux"))), "dc1-leaf-01.cli"]:
            open(os.path.join(d, "srlinux", n), "w").close()
        e = {k: v for k, v in os.environ.items() if k not in ("NAME_PREFIX", "AWS_REGION", "LINES")}
        e.update(PATH=b + os.pathsep + os.environ["PATH"], FAKE_LOG=log, **({"FAKE_RUNNING": "1"} if running else {}))
        r = subprocess.run([os.path.join(d, "lab.sh"), *args], env=e, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        return r, open(log, encoding="utf-8").read().splitlines()
_srl_nodes = [n for n, v in yaml.safe_load(read("app", "containerlab", "splab.clab.yml.in"))["topology"]["nodes"].items() if v.get("kind") == "nokia_srlinux"]
_routers = re.search(r"^routers\(\) \{.*?^\}", lab_sh, re.M | re.S)
check("lab.sh の routers() は srlinux/*.cli を数えず（glob を使わない）、トポロジのテンプレート（$TOPO.in）を読む",
      _routers is not None and "*.cli" not in _routers.group(0) and '"$TOPO.in"' in _routers.group(0))
_r, _c = _lab_docker("logs")
check("lab.sh logs: 打つ機器はテンプレートの nodes の kind: nokia_srlinux（6 台、この順）だけで、srlinux/ に残った古い .cli の機器（dc1-leaf-01）は打たない",
      _r.returncode == 0 and len(_srl_nodes) == 6
      and _c == [f"docker exec clab-splab-{n} tail -n 20 /var/log/srlinux/file/messages" for n in _srl_nodes])
_trex_case = re.search(r"^  trex\)\n.*?^    ;;$", lab_sh, re.M | re.S).group(0)
check("lab.sh trex: start / stop / status は同じ式（TREX_PROC=t-rex-64。ラッパーと子の _t-rex-64 の両方に当たる）で pgrep / pkill する",
      sh_const(lab_sh, "TREX_PROC") == "t-rex-64"
      and re.findall(r"\b(pgrep|pkill) (-[a-z]+) (\S+)", _trex_case) == [("pgrep", "-f", '"$TREX_PROC"'), ("pkill", "-f", '"$TREX_PROC"'), ("pgrep", "-af", '"$TREX_PROC"')])
_r, _c = _lab_docker("trex", "stop", running=True)
_r2, _c2 = _lab_docker("trex", "stop")
check("lab.sh trex stop: dc1-trex-01 の中で pkill -f t-rex-64。動いていなければ（pkill が 1）止まらずに案内を出す",
      _r.returncode == 0 and _c == ["docker exec clab-splab-dc1-trex-01 pkill -f t-rex-64"]
      and _r2.returncode == 0 and _c2 == _c and "TRex は動いていない" in _r2.stdout)
_r, _c = _lab_docker("trex", "status", running=True)
check("lab.sh trex status: pgrep -af t-rex-64 のあとに出力の末尾（/var/log/trex.log）を出す",
      _r.returncode == 0 and _c == ["docker exec clab-splab-dc1-trex-01 pgrep -af t-rex-64", "docker exec clab-splab-dc1-trex-01 tail -n 20 /var/log/trex.log"]
      and "./t-rex-64 -i" in _r.stdout)

# ---- lab.sh failover の route()（024 A）。lab.sh の set -euo pipefail のもとで、route() の本体だけを偽の srl() と打つ
_route_fn = re.search(r"^    route\(\) \{.*?^    \}", lab_sh, re.M | re.S).group(0)
def _route(srl_body):
    return subprocess.run(["bash", "-c", "set -euo pipefail\nsrl() { " + srl_body + " }\n" + _route_fn + "\nroute"],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
_r = _route(":;")
check("lab.sh failover の route(): IS-IS の経路が無い（srl が何も返さない）とき、set -e と pipefail で止まらず「(IS-IS の経路が無い)」を出して 0 で返る",
      _r.returncode == 0 and _r.stdout == "  (IS-IS の経路が無い)\n")
_r = _route('case "$2" in *route-table\\ ipv4-unicast*) echo "next-hop-group 3" ;; *next-hop-group*) echo "    next-hop 1" ;; '
            '*) printf "ip-address 172.16.0.12\\nsubinterface ethernet-1/50.0\\n" ;; esac;')
check("lab.sh failover の route(): 経路があるときは next-hop-group → next-hop → ip-address / subinterface をたどって 1 行に出す（形は変わらない）",
      _r.returncode == 0 and "ip-address 172.16.0.12 subinterface ethernet-1/50.0" in _r.stdout and "IS-IS の経路が無い" not in _r.stdout)

# ---- trex/kafka_load.sh の nodes() / peer() / payload()（024 F）。関数だけを抜き出し、cwd を app/containerlab/trex にして打つ（docker も MSK も要らない）
_kl = read("app", "containerlab", "trex", "kafka_load.sh")
_kl_fns = "\n".join(re.search(p, _kl, re.M | re.S).group(0) for p in (r"^nodes\(\) \{.*?\}$", r"^peer\(\) \{.*?\}$", r"^payload\(\) \{.*?^\}"))
def _kl_run(script):
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + _kl_fns + "\n" + script], capture_output=True, text=True,
                          cwd=os.path.join(ROOT, "app", "containerlab", "trex"), stdin=subprocess.DEVNULL, timeout=30)
_kl_nodes = [(n, v["mgmt-ipv4"]) for n, v in yaml.safe_load(read("app", "containerlab", "splab.clab.yml.in"))["topology"]["nodes"].items()
             if v.get("kind") == "nokia_srlinux"]
_r = _kl_run("nodes")
check("kafka_load.sh の nodes(): splab.clab.yml.in の SR Linux 6 台の名前と mgmt-ipv4 を出す（.in の現物と同じ。trex は kind linux なので入らない）",
      _r.returncode == 0 and len(_kl_nodes) == 6 and ("dc1-a-leaf-01", "203.0.113.31") in _kl_nodes and "dc1-trex-01" not in _r.stdout
      and [tuple(l.split()) for l in _r.stdout.splitlines()] == _kl_nodes)
_r = _kl_run('for n in $(nodes | cut -d" " -f1); do echo "$n $(peer "$n")"; done')
_kl_peers = dict(l.split(" ", 1) for l in _r.stdout.splitlines())
check("kafka_load.sh の peer(): 各機器の srlinux/<機器>.cli の最初の overlay の neighbor（dc1-a-leaf-01 は 10.255.0.1、dc1-spine-01 は 10.255.1.1。空の機器が無い）",
      _r.returncode == 0 and len(_kl_peers) == 6 and _kl_peers.get("dc1-a-leaf-01") == "10.255.0.1" and _kl_peers.get("dc1-spine-01") == "10.255.1.1"
      and all(re.fullmatch(r"10\.255\.\d+\.\d+", p) for p in _kl_peers.values()))
_r = _kl_run("TOPIC=gnmi; payload")
_kl_recs = [json.loads(l) for l in _r.stdout.splitlines()]
check("kafka_load.sh の gnmi のレコード: neighbor_peer-address は peer() の値（10.255.0.1 の固定でない）で、source はその機器の管理 IP",
      '"neighbor_peer-address":"10.255.0.1"' not in _kl and '"neighbor_peer-address":"%s"' in _kl
      and _r.returncode == 0 and [(r["tags"]["source"], r["tags"]["neighbor_peer-address"]) for r in _kl_recs]
      == [(ip, _kl_peers[n]) for n, ip in _kl_nodes])

# ---- lab.sh の trex_cfg() / edge_ports() と trex/stl の 2 つ（024 J）。関数を抜き出し、lab.sh の定数の定義行を前に置いて cwd を app/containerlab で打つ
_trex_line = re.search(r"^TREX_NET=\S+; TREX_IP_BASE=\d+$", lab_sh, re.M).group(0)
_trex_net, _trex_base = re.fullmatch(r"TREX_NET=(\S+); TREX_IP_BASE=(\d+)", _trex_line).groups()
_trex_pre = "\n".join(("TREX=" + sh_const(lab_sh, "TREX"), _trex_line, "TOPO=" + sh_const(lab_sh, "TOPO")))
def _trex_fn(name):
    return re.search(r"^%s\(\) \{.*?^\}" % name, lab_sh, re.M | re.S).group(0)
def _trex_run(script):
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + _trex_pre + "\n" + script], capture_output=True, text=True,
                          cwd=os.path.join(ROOT, "app", "containerlab"), stdin=subprocess.DEVNULL, timeout=30)
_r = _trex_run(_trex_fn("trex_cfg") + "\ntrex_cfg eth1 eth2 eth3 eth4")
_tc = yaml.safe_load(_r.stdout) if _r.returncode == 0 else None
_ips = ["%s.%d" % (_trex_net, int(_trex_base) + i) for i in range(4)]
check("lab.sh の trex_cfg eth1〜eth4: port_limit 4、interfaces はその順、port_info の ip は $TREX_NET.(TREX_IP_BASE+i)、default_gw は組の相手（0↔1、2↔3）",
      _tc is not None and _tc[0]["port_limit"] == 4 and _tc[0]["interfaces"] == ["eth1", "eth2", "eth3", "eth4"]
      and _ips == ["10.100.0.11", "10.100.0.12", "10.100.0.13", "10.100.0.14"]
      and [(p["ip"], p["default_gw"]) for p in _tc[0]["port_info"]] == [(_ips[0], _ips[1]), (_ips[1], _ips[0]), (_ips[2], _ips[3]), (_ips[3], _ips[2])])
_r = _trex_run("srl() { echo \"oper-state up\"; }\nx() { :; }\ntrex_ports() { :; }\n" + _trex_fn("edge_ports") + "\nedge_ports")
check("lab.sh の edge_ports: splab.clab.yml.in の TRex のリンク 4 本（eth1〜4 ↔ s-leaf-01/02・a-leaf-01/02 の ethernet-1/3）を読み、leaf ごとに srl の oper-state を出す",
      _r.returncode == 0 and [l.split() for l in _r.stdout.splitlines()]
      == [[n, "ethernet-1/3", "oper-state", "up"] for n in ("dc1-s-leaf-01", "dc1-s-leaf-02", "dc1-a-leaf-01", "dc1-a-leaf-02")])
sys.path.insert(0, os.path.join(ROOT, "app", "containerlab", "trex", "stl"))
sys.dont_write_bytecode = True  # app/containerlab/ は upload_lab が S3 へ丸ごと送るので、__pycache__ を作らない
import udp_syslog, udp_trap  # TRex（trex_stl_lib）無しで import できること自体も見ている
check("trex/stl/udp_syslog.py の syslog_payload: RFC 5424 の形で PRI は local7（23*8+severity）",
      udp_syslog.syslog_payload("dc1-trex-01", "sr_bgp_mgr", 5, "msg") == b"<189>1 - dc1-trex-01 sr_bgp_mgr - - - msg")
try:
    udp_syslog.syslog_payload("dc1-trex-01", "sr_bgp_mgr", 8, "msg")
    _bad = False
except ValueError:
    _bad = True
check("trex/stl/udp_syslog.py の syslog_payload: severity が 0〜7 の外（8）なら ValueError", _bad)
_tp = udp_trap.trap_payload()
check("trex/stl/udp_trap.py の trap_payload: bytes で、先頭は BER の SEQUENCE（0x30）",
      isinstance(_tp, bytes) and len(_tp) > 2 and _tp[0] == 0x30)
check("trex/stl の既定の宛先: syslog は lab.sh の MGMT_GW:LOG_PORT（5140）、trap は MGMT_GW:162（lab.sh forward が NLB へ DNAT する口）",
      udp_syslog.DEFAULTS["dst"] == udp_trap.DEFAULTS["dst"] == sh_const(lab_sh, "MGMT_GW")
      and udp_syslog.DEFAULTS["dport"] == sh_const(lab_sh, "LOG_PORT") == "5140" and udp_trap.DEFAULTS["dport"] == "162")

_lab_out =read("IaC", "terraform", "aws-managed", "pipeline", "lab", "outputs.tf")
check("lab の output graph_port_forward_command は lab.sh graph が出すコマンドと同じ（宛先は aws_instance.lab.id、ポートは lab.sh の GRAPH_PORT）",
      sh_const(lab_sh, "GRAPH_PORT") == "50080"
      and 'value       = "' + _fwd.replace("ap-northeast-1", "${var.region}").replace("i-0123456789abcdef0", "${aws_instance.lab.id}") + '"' in _lab_out)

for f in (("ops", "lab-common.sh"), ("ops", "lab-debug.sh"), ("ops", "up.sh"), ("ops", "down.sh"),
          ("ops", "common.sh"), ("ops", "up-common.sh"), ("ops", "down-common.sh"), ("app", "containerlab", "setup.sh"), ("app", "containerlab", "lab.sh"), ("app", "telegraf", "telegraf.sh")):
    r = subprocess.run(["bash", "-n", os.path.join(ROOT, *f)], capture_output=True, text=True)
    check(f"{'/'.join(f)} は bash として読める", r.returncode == 0)

print(f"通過 {passed} / 失敗 0")
