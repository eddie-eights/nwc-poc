"""手元の docker compose で動く構成（docker/compose。docs/cycles/006-local-compose/design.md）が、元にした定義からずれていないかを見る。
- 版: Kafka / Kafbat UI / OpenSearch は oss/ops/oss-images.sh、Telegraf と lab のイメージは ops/lab-common.sh、Grafana / Splunk は ops/up-common.sh と同値
- 契約: Telegraf は app/telegraf/telegraf.sh render が通る環境、Spark は app/spark/snmp_sinks.py の parse_args が通る引数、check.sh が見る名前は実物の定義にある
- app/containerlab/lab.sh: REGISTRY が無ければ ECR に触らずに pull、TELEGRAF_LOCAL=1 なら trap の REDIRECT だけ（デバッグ用の EC2 と同じ）。偽の docker / aws / iptables / sudo で動かす
- docker/compose/*.sh: up.sh が lab の値を環境で渡す、lab.sh が 3 つだけを sudo に渡す、check.sh が全部見てから終わりパスワードを引数に載せない
実行は uv run --group dev python tests/test_local_compose.py（pyyaml を使う。AWS も docker も要らない）。"""
import glob, importlib.util, io, json, os, re, shutil, subprocess, sys, tempfile

import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LC = os.path.join(ROOT, "docker", "compose")

passed = 0
def check(name, cond):
    global passed
    assert cond, name
    passed += 1
    print("ok", name)

def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()

def sh_const(src, name):  # シェルの NAME=値（行頭。引用符と行末のコメントは外す）
    m = re.search(rf"^{name}=('[^']*'|\"[^\"]*\"|\S*)", src, re.M)
    return m.group(1).strip("'\"") if m else None

def env_file(text):  # .env の KEY=値（コメントと空行は飛ばす）
    return dict(l.split("=", 1) for l in text.splitlines() if l and not l.startswith("#") and "=" in l)


compose = yaml.safe_load(read("docker", "compose", "compose.yaml"))
svc = compose["services"]
oss_images = read("oss", "ops", "oss-images.sh")
lab_common, up_common = read("ops", "lab-common.sh"), read("ops", "up-common.sh")
example = env_file(read("docker", "compose", ".env.example"))
lab_sh = read("app", "containerlab", "lab.sh")

# up.sh が lab の定義から作って環境で渡す 2 つ（AWS 版の ops/up.sh と同じ作り方。SNMP のポーリング先 SNMP_AGENTS は cycle 013 でやめた）
def topology(flag):
    return subprocess.run([sys.executable, os.path.join(ROOT, "app", "containerlab", "lab_topology.py"), os.path.join(ROOT, "app", "containerlab"), flag],
                          capture_output=True, text=True, check=True).stdout.strip()
UP_ENV = {"GNMI_TARGETS": topology("--gnmi-targets"), "DEVICE_MAP": topology("--device-map")}

def subst(s, env):  # compose の ${X} / ${X:-既定} を env で埋める
    return re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", lambda m: env.get(m.group(1)) or (m.group(2) or ""), s)


# ---- 1. 構成（design.md の「合意した決定」と「変更対象ファイル」）
check("services は 14 個（telegraf gnmic syslog-ng goflow2 kafka-1 kafka-2 kafka-3 kafka-ui spark-splunk spark-http opensearch prometheus splunk grafana。syslog-ng と goflow2 は cycle 012、gnmic は cycle 013）",
      list(svc) == "telegraf gnmic syslog-ng goflow2 kafka-1 kafka-2 kafka-3 kafka-ui spark-splunk spark-http opensearch prometheus splunk grafana".split())
check("named volume は design.md の 10 個（kafka-1/2/3、opensearch、prometheus、splunk-etc、splunk-var、grafana、spark-*-ckpt）",
      set(compose["volumes"]) == {"kafka-1", "kafka-2", "kafka-3", "opensearch", "prometheus", "splunk-etc", "splunk-var", "grafana",
                                  "spark-splunk-ckpt", "spark-http-ckpt"})
check("ネットワークは nwc-local", compose["networks"]["default"]["name"] == "nwc-local")
check("プロジェクト名は nwc-local（ディレクトリ名の compose にしない。volume が nwc-local_* になる）",
      compose["name"] == "nwc-local")
check("compose の ports は全部 127.0.0.1 に縛る（Kafka・Splunk・Grafana・OpenSearch を WSL の外へ出さない。host のネットワークにいる Telegraf・syslog-ng・GoFlow2 の受け口はこの外。gnmic は待ち受けない）",
      all(p.startswith("127.0.0.1:") for s in svc.values() for p in s.get("ports", [])))
def built(n, name):  # compose の build が app/<name> を context に、docker/images/<name>/Dockerfile を dockerfile にしていて、その Dockerfile が実在する
    b = svc[n]["build"]
    return (isinstance(b, dict) and b.get("context") == f"../../app/{name}" and b.get("dockerfile") == f"../../docker/images/{name}/Dockerfile"
            and os.path.isfile(os.path.join(LC, b["context"], b["dockerfile"])))
check("telegraf は network_mode: host で、build の context は ../../app/telegraf、dockerfile は ../../docker/images/telegraf/Dockerfile（context からの相対）",
      svc["telegraf"]["network_mode"] == "host" and built("telegraf", "telegraf") and "ports" not in svc["telegraf"])
check("gnmic は network_mode: host（lab の管理ネットの SR Linux の 57400/tcp へ繋ぐ）で、build の context は ../../app/gnmic、dockerfile は ../../docker/images/gnmic/Dockerfile、ports を書かない（cycle 013）",
      svc["gnmic"]["network_mode"] == "host" and built("gnmic", "gnmic") and svc["gnmic"]["image"] == "nwc-local-gnmic" and "ports" not in svc["gnmic"])
check("syslog-ng は network_mode: host で、build の context は ../../app/syslog-ng、dockerfile は ../../docker/images/syslog-ng/Dockerfile。goflow2 も host で、どちらも ports を書かない",
      svc["syslog-ng"]["network_mode"] == svc["goflow2"]["network_mode"] == "host" and built("syslog-ng", "syslog-ng")
      and svc["syslog-ng"]["image"] == "nwc-local-syslog-ng" and "ports" not in svc["syslog-ng"] and "ports" not in svc["goflow2"])

# ---- 2. 版の正
def oss_image(name):  # oss/ops/oss-images.sh の OSS_<name>_IMAGE:OSS_<name>_TAG
    return f"{sh_const(oss_images, f'OSS_{name}_IMAGE')}:{sh_const(oss_images, f'OSS_{name}_TAG')}"
for n in ("kafka-1", "kafka-2", "kafka-3"):
    check(f"{n} の image は oss/ops/oss-images.sh の Kafka と同じ（{oss_image('KAFKA')}）", svc[n]["image"] == oss_image("KAFKA"))
check("kafka-ui の image は oss/ops/oss-images.sh の Kafbat UI と同じで、127.0.0.1:18080 に出す",
      svc["kafka-ui"]["image"] == oss_image("KAFKA_UI") and svc["kafka-ui"]["ports"] == ["127.0.0.1:18080:8080"])
check("kafka-ui に渡すのはクラスターの名前と 3 台の PLAINTEXT の宛先だけ（AUTH_TYPE は書かない。宛先は各台の advertised の PLAINTEXT）",
      svc["kafka-ui"]["environment"] == {
          "KAFKA_CLUSTERS_0_NAME": "nwc",
          "KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS": ",".join(re.search(r"PLAINTEXT://([^,]+)", svc[n]["environment"]["KAFKA_ADVERTISED_LISTENERS"]).group(1)
                                                         for n in ("kafka-1", "kafka-2", "kafka-3"))}
      and svc["kafka-ui"]["environment"]["KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS"] == "kafka-1:9092,kafka-2:9092,kafka-3:9092")
check("opensearch の image は oss/ops/oss-images.sh の OpenSearch と同じ", svc["opensearch"]["image"] == oss_image("OPENSEARCH"))
check("grafana の GRAFANA_VERSION と splunk の SPLUNK_VERSION は ops/up-common.sh の値",
      svc["grafana"]["build"]["args"]["GRAFANA_VERSION"] == sh_const(up_common, "GRAFANA_VERSION")
      and svc["splunk"]["build"]["args"]["SPLUNK_VERSION"] == sh_const(up_common, "SPLUNK_VERSION")
      and built("grafana", "grafana") and built("splunk", "splunk"))
check("telegraf の TELEGRAF_VERSION は ops/lab-common.sh の値",
      svc["telegraf"]["build"]["args"]["TELEGRAF_VERSION"] == sh_const(lab_common, "TELEGRAF_VERSION"))
check("gnmic の GNMIC_VERSION は ops/up-common.sh の値で、docker/images/gnmic/Dockerfile の ARG の既定値も同じ（cycle 013）",
      svc["gnmic"]["build"]["args"]["GNMIC_VERSION"] == sh_const(up_common, "GNMIC_VERSION") is not None
      and re.search(r"^ARG GNMIC_VERSION=(\S+)$", read("docker", "images", "gnmic", "Dockerfile"), re.M).group(1) == sh_const(up_common, "GNMIC_VERSION"))
check(".env.example の SRLINUX_IMAGE / MULTITOOL_IMAGE / TREX_IMAGE は ops/lab-common.sh の upstream:tag",
      example["SRLINUX_IMAGE"] == f"{sh_const(lab_common, 'SRLINUX_UPSTREAM')}:{sh_const(lab_common, 'SRLINUX_TAG')}"
      and example["MULTITOOL_IMAGE"] == f"{sh_const(lab_common, 'MULTITOOL_UPSTREAM')}:{sh_const(lab_common, 'MULTITOOL_TAG')}"
      and example["TREX_IMAGE"] == f"{sh_const(lab_common, 'TREX_UPSTREAM')}:{sh_const(lab_common, 'TREX_TAG')}")
check("splunk は linux/amd64（上流が amd64 だけ）", svc["splunk"]["platform"] == "linux/amd64")
check("syslog-ng の SYSLOG_NG_VERSION と goflow2 のタグは ops/up-common.sh の SYSLOG_NG_VERSION / GOFLOW2_TAG で、Dockerfile の ARG の既定値も同じ（cycle 012）",
      svc["syslog-ng"]["build"]["args"]["SYSLOG_NG_VERSION"] == sh_const(up_common, "SYSLOG_NG_VERSION") is not None
      and svc["goflow2"]["image"] == f"netsampler/goflow2:{sh_const(up_common, 'GOFLOW2_TAG')}"
      and re.search(r"^ARG SYSLOG_NG_VERSION=(\S+)$", read("docker", "images", "syslog-ng", "Dockerfile"), re.M).group(1) == sh_const(up_common, "SYSLOG_NG_VERSION"))
check("telegraf・gnmic・syslog-ng と spark-splunk / spark-http は restart: on-failure:5（起こし直しは 5 回まで。swarm の deploy.restart_policy は使わない）",
      all(svc[n].get("restart") == "on-failure:5" and "deploy" not in svc[n] for n in ("telegraf", "gnmic", "syslog-ng", "spark-splunk", "spark-http"))
      and svc["gnmic"]["depends_on"] == ["kafka-1", "kafka-2", "kafka-3"])
check("goflow2 は restart: on-failure:10（Kafka が無いと 1 秒ほどで終わるので、5 回では 9 秒しか待てない。10 回で約 110 秒。cycle 012）",
      svc["goflow2"].get("restart") == "on-failure:10" and "deploy" not in svc["goflow2"])
# Spark は送り先が起きる前に始めると POST の再試行（app/spark/snmp_sinks.py の HTTP_RETRIES）のあとに落ちてジョブが終わるので、送り先が healthy になるまで起こさない。
# マージキー（<<: *spark）は depends_on を混ぜないので、x-spark には書かず各 service に Kafka ごと書く
_kafka = {f"kafka-{i}": {"condition": "service_started"} for i in (1, 2, 3)}
check("spark-splunk は Kafka の 3 つが起き、Splunk が healthy になってから起こす（x-spark に depends_on は無い）",
      svc["spark-splunk"]["depends_on"] == {**_kafka, "splunk": {"condition": "service_healthy"}} and "depends_on" not in compose["x-spark"])
check("spark-http は Kafka の 3 つが起き、OpenSearch と Prometheus が healthy になってから起こす",
      svc["spark-http"]["depends_on"] == {**_kafka, "opensearch": {"condition": "service_healthy"}, "prometheus": {"condition": "service_healthy"}})
_hc = {n: svc[n].get("healthcheck", {}) for n in ("splunk", "opensearch", "prometheus")}
check("splunk / opensearch / prometheus の healthcheck: splunk はイメージの /sbin/checkstate.sh、opensearch は / が 200 か 401（security が認証を求める）、prometheus は /-/ready",
      _hc["splunk"].get("test") == ["CMD-SHELL", "/sbin/checkstate.sh"]
      and _hc["opensearch"].get("test") == ["CMD-SHELL", "curl -s -o /dev/null -w '%{http_code}' http://localhost:9200/ | grep -Eqx '200|401'"]
      and _hc["prometheus"].get("test") == ["CMD", "wget", "-q", "-O", "/dev/null", "http://localhost:9090/-/ready"]
      and all(h.get("start_period") and h.get("retries") for h in _hc.values()))

# ---- 3. Kafka: OSS 版の ECS（IaC/terraform/oss/pipeline/stream/kafka.tf の kafka_environment）と同じ値に、EXTERNAL リスナーを足しただけ
# 比べるのは kafka.tf で文字どおりの値のもの。違うと決めてあるのはホスト名（voter と advertised）・ヒープ・保持期間（手元は既定の 168 時間）
_tf = read("IaC", "terraform", "oss", "pipeline", "stream", "kafka.tf")
_tf_block = _tf[_tf.index("kafka_environment = ["):]
_tf_block = _tf_block[:_tf_block.index("\n  ]")]
_tf_env = {k: v for k, v in re.findall(r'\{\s*name\s*=\s*"(\w+)",\s*value\s*=\s*"([^"]*)"\s*\}', _tf_block) if "${" not in v}
_ext = {"KAFKA_LISTENERS", "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"}
_same = set(_tf_env) - _ext - {"KAFKA_LOG_RETENTION_HOURS"}
check("kafka.tf の kafka_environment から比べる値を 13 個読めた（読めずに素通りしない）",
      len(_same) == 13 and _ext <= set(_tf_env) and "KAFKA_HEAP_OPTS" not in _tf_env)
for i, n in enumerate(("kafka-1", "kafka-2", "kafka-3")):
    e, port = svc[n]["environment"], 9094 + i
    check(f"{n}: EXTERNAL 以外の KAFKA_*（ホスト名・ヒープ・保持期間を除く）は kafka.tf と同じ値",
          all(str(e.get(k)).lower() == v.lower() for k, v in _tf_env.items() if k in _same))
    check(f"{n}: リスナーは kafka.tf のものに EXTERNAL://:{port}（advertised は {n}:9092 と localhost:{port}）を足し、127.0.0.1:{port} に出す",
          e["KAFKA_LISTENERS"] == _tf_env["KAFKA_LISTENERS"] + f",EXTERNAL://:{port}"
          and e["KAFKA_ADVERTISED_LISTENERS"] == f"PLAINTEXT://{n}:9092,EXTERNAL://localhost:{port}"
          and e["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"] == _tf_env["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"] + ",EXTERNAL:PLAINTEXT"
          and svc[n]["ports"] == [f"127.0.0.1:{port}:{port}"])
_k = [svc[n]["environment"] for n in ("kafka-1", "kafka-2", "kafka-3")]
check("3 台の KAFKA_NODE_ID は 1〜3、voter はその番号と service 名の 9093（固定の voter）、CLUSTER_ID は 3 台で同じ KRaft の形（22 字）",
      [x["KAFKA_NODE_ID"] for x in _k] == [1, 2, 3]
      and all(x["KAFKA_CONTROLLER_QUORUM_VOTERS"] == ",".join(f"{y['KAFKA_NODE_ID']}@kafka-{y['KAFKA_NODE_ID']}:9093" for y in _k) for x in _k)
      and len({x["CLUSTER_ID"] for x in _k}) == 1 and re.fullmatch(r"[A-Za-z0-9_-]{22}", _k[0]["CLUSTER_ID"]))
check("ヒープは初期と上限が同じ（kafka.tf と同じ形。値は手元向けに小さい）",
      all(re.fullmatch(r"-Xms(\d+)m -Xmx\1m", x["KAFKA_HEAP_OPTS"]) for x in _k))
check("トピックは自動で作る（Telegraf・gnmic が最初に書く）",
      all(svc[n]["environment"]["KAFKA_AUTO_CREATE_TOPICS_ENABLE"] == "true" for n in ("kafka-1", "kafka-2", "kafka-3")))

# ---- 4. Telegraf: 環境は app/telegraf/telegraf.sh の契約どおり。up.sh の値を入れると render が通り、出力（traps）は host の EXTERNAL の 3 台へ
_tg = svc["telegraf"]["environment"]
check("telegraf の KAFKA_BROKERS は 3 台の EXTERNAL（localhost:9094-9096）、KAFKA_AUTH=none。SYSLOG_STANDARD / MDT_PORT は渡さない（syslog は syslog-ng、MDT は外した。cycle 012）",
      _tg["KAFKA_BROKERS"] == "localhost:9094,localhost:9095,localhost:9096" and _tg["KAFKA_AUTH"] == "none" and _tg["SINK"] == "kafka"
      and "SYSLOG_STANDARD" not in _tg and "MDT_PORT" not in _tg)
check("telegraf は trap だけ受ける（cycle 013）: 購読先・ポーリング先・機器の認証情報・TELEGRAF_ROLE / SNMP_POLL を渡さない",
      not any(k in _tg for k in ("GNMI_TARGETS", "SNMP_AGENTS", "GNMI_USERNAME", "GNMI_PASSWORD", "SNMP_COMMUNITY", "TELEGRAF_ROLE", "SNMP_POLL")))
_gn = svc["gnmic"]["environment"]
check("gnmic の環境は KAFKA_AUTH=none、KAFKA_BROKERS は telegraf と同じ、GNMI_TARGETS は up.sh の値、GNMI_USERNAME / GNMI_PASSWORD は ops/lab-common.sh の lab の公開既定値（cycle 013）",
      _gn == {"KAFKA_AUTH": "none", "KAFKA_BROKERS": _tg["KAFKA_BROKERS"], "GNMI_TARGETS": "${GNMI_TARGETS:-}",
              "GNMI_USERNAME": sh_const(lab_common, "LAB_GNMI_USERNAME"), "GNMI_PASSWORD": sh_const(lab_common, "LAB_GNMI_PASSWORD")}
      and _gn["GNMI_USERNAME"] is not None and _gn["GNMI_PASSWORD"] is not None)

def tg_render(env, extra=None):  # env は compose の ${X} を埋める値、extra は compose が渡さない環境（LOG_PORT など）
    with tempfile.TemporaryDirectory() as d:
        e = {"PATH": os.environ["PATH"], "TELEGRAF_TEMPLATE": os.path.join(ROOT, "app", "telegraf", "telegraf.conf.in"),
             "TELEGRAF_CONF": os.path.join(d, "telegraf.conf")}
        e.update({k: subst(str(v), env) for k, v in _tg.items()})
        e.update(extra or {})
        r = subprocess.run(["bash", os.path.join(ROOT, "app", "telegraf", "telegraf.sh"), "render"], capture_output=True, text=True, env=e)
        conf = open(e["TELEGRAF_CONF"], encoding="utf-8").read() if r.returncode == 0 else ""
        return r, conf

_r, _conf = tg_render(UP_ENV)
check("up.sh の値を入れた telegraf の環境で telegraf.sh render が通り、出力 1 つ（traps）が localhost:9094-9096 へ書き、IAM の設定が無い",
      _r.returncode == 0 and _conf.count('brokers = ["localhost:9094","localhost:9095","localhost:9096"]') == 1
      and "sasl_mechanism" not in _conf and not any("203.0.113." in l for l in _conf.splitlines() if not l.lstrip().startswith("#")))
check("telegraf.sh は up.sh の値が無くても（docker compose を直に打っても）render が通る（受けるのは trap だけで、lab の値を使わない）",
      tg_render({})[0].returncode == 0)

def gn_render(env):  # compose の ${X} を env で埋めた gnmic の環境で app/gnmic/gnmic.sh render を打ち、(結果, 設定) を返す
    with tempfile.TemporaryDirectory() as d:
        e = {"PATH": os.environ["PATH"], "GNMIC_TEMPLATE": os.path.join(ROOT, "app", "gnmic", "gnmic.yaml.in"), "GNMIC_CONF": os.path.join(d, "gnmic.yaml")}
        e.update({k: subst(str(v), env) for k, v in _gn.items()})
        r = subprocess.run(["sh", os.path.join(ROOT, "app", "gnmic", "gnmic.sh"), "render"], capture_output=True, text=True, env=e)
        conf = open(e["GNMIC_CONF"], encoding="utf-8").read() if r.returncode == 0 else ""
        return r, conf
_gr, _gconf = gn_render(UP_ENV)
_gy = yaml.safe_load(_gconf) if _gconf else {}
_ips = re.findall(r'"([0-9.]+):57400"', UP_ENV["GNMI_TARGETS"])
check("up.sh の値を入れた gnmic の環境で gnmic.sh render が通り、lab の機器の全部（名前は IP）を購読し、gnmi / metrics の 2 つの出力が localhost:9094-9096 へ認証なし（sasl と tls が無い）で書く",
      _gr.returncode == 0 and len(_ips) >= 2 and sorted(_gy["targets"]) == sorted(_ips)
      and all(_gy["targets"][ip]["address"] == f"{ip}:57400" for ip in _ips)
      and {n: (o["topic"], o["address"]) for n, o in _gy["outputs"].items()}
      == {"gnmi": ("gnmi", _tg["KAFKA_BROKERS"]), "metrics": ("metrics", _tg["KAFKA_BROKERS"])}
      and not any("sasl" in o or "tls" in o for o in _gy["outputs"].values()) and "NokiaSrl1" not in _gconf
      and not any("__" in l for l in _gconf.splitlines() if not l.lstrip().startswith("#")))
_gr = gn_render({})[0]
check("docker compose を直に打つ（GNMI_TARGETS が空）と gnmic.sh は形の検査で止まる（compose.yaml の頭の注意のとおり。up.sh から上げる）",
      _gr.returncode != 0 and "GNMI_TARGETS" in _gr.stderr)
# 受け口の 2 つ（trap・health。syslog と MDT は cycle 012 で外した）。TELEGRAF_BIND が空なら今までどおり全部のインターフェース（ECS は何も渡さない）
_listen = lambda b, hp="8080": [f'service_address = "udp://{b}:1162"', f'service_address = "http://{b}:{hp}"']
check("TELEGRAF_BIND が空なら受け口の 2 つは今までどおり全部のインターフェース（udp://:1162 / http://:8080）で、__ が残らず、syslog と MDT の受け口は無い",
      all(x in _conf for x in _listen("")) and "__" not in _conf and "/ bind:" not in _r.stdout
      and "5140" not in _conf and "57000" not in _conf)
_r, _conf = tg_render({**UP_ENV, "TELEGRAF_BIND": "203.0.113.1"})
check("TELEGRAF_BIND=203.0.113.1（docker/compose/up.sh が lab の管理ネットの GW を渡す）なら受け口の 2 つともそのアドレスだけで待ち、起動の 1 行に bind が出る",
      _r.returncode == 0 and all(x in _conf for x in _listen("203.0.113.1")) and "__" not in _conf
      and "/ health: 8080/tcp / bind: 203.0.113.1" in _r.stdout)
_r, _conf = tg_render({**UP_ENV, "TELEGRAF_BIND": "127.0.0.1", "HEALTH_PORT": "18081"})
check("HEALTH_PORT（.env）を変えると health の受け口がそのポートになる（009 の design.md の検証方法 1・2。MDT_PORT は cycle 012 で外した）",
      _r.returncode == 0 and all(x in _conf for x in _listen("127.0.0.1", "18081")) and "__" not in _conf
      and "trap: 1162/udp / health: 18081/tcp" in _r.stdout)
_bad = [{"HEALTH_PORT": v} for v in ("0", "65536", "08080", "80a", "-1", "99999")] + [{"TRAP_PORT": "0"}] \
    + [{"TELEGRAF_BIND": v} for v in ("::1", "localhost", "203.0.113.1 ", "1.2.3", "1.2.3.4#x")]
_badr = [tg_render(UP_ENV, b)[0] for b in _bad]
check("telegraf.sh render: ポートが 1〜65535 の数字でない（0 / 65536 / 先頭の 0 / 数字以外）か、TELEGRAF_BIND が空でも IPv4 でもなければ、名前を出して止まる",
      all(r.returncode != 0 and next(iter(b)) in r.stderr for r, b in zip(_badr, _bad)))
_tgsh = read("app", "telegraf", "telegraf.sh")
check("HEALTH_PORT の既定は compose・.env.example・telegraf.sh（ECS）で同じ（8080）。TELEGRAF_BIND の既定は空。MDT_PORT はどこにも無い（cycle 012）",
      (_tg["HEALTH_PORT"], _tg["TELEGRAF_BIND"]) == ("${HEALTH_PORT:-8080}", "${TELEGRAF_BIND:-}") and example["HEALTH_PORT"] == "8080"
      and (sh_const(_tgsh, "HEALTH_PORT"), sh_const(_tgsh, "TELEGRAF_BIND")) == ("${HEALTH_PORT:-8080}", "${TELEGRAF_BIND:-}")
      and "MDT_PORT" not in example and sh_const(_tgsh, "MDT_PORT") is None)
# ---- 4b. syslog-ng と GoFlow2（cycle 012）: Telegraf と同じ Kafka（EXTERNAL の 3 つ、認証なし）に、Telegraf と同じアドレス（TELEGRAF_BIND）で待つ
_sg = svc["syslog-ng"]["environment"]
check("syslog-ng の環境は KAFKA_AUTH=none、KAFKA_BROKERS は telegraf と同じ、SYSLOG_STANDARD=RFC5424（lab の SR Linux の形式）、SYSLOG_BIND は up.sh の TELEGRAF_BIND",
      _sg == {"KAFKA_AUTH": "none", "KAFKA_BROKERS": _tg["KAFKA_BROKERS"], "SYSLOG_STANDARD": "RFC5424", "SYSLOG_BIND": "${TELEGRAF_BIND:-}"})
def sng_render(env):  # compose の ${X} を env で埋めた syslog-ng の環境で app/syslog-ng/syslog-ng.sh render を打ち、(結果, コメントの行を除いた設定) を返す
    with tempfile.TemporaryDirectory() as d:
        e = {"PATH": os.environ["PATH"], "SYSLOG_NG_TEMPLATE": os.path.join(ROOT, "app", "syslog-ng", "syslog-ng.conf.in"),
             "SYSLOG_NG_CONF": os.path.join(d, "syslog-ng.conf")}
        e.update({k: subst(str(v), env) for k, v in _sg.items()})
        r = subprocess.run(["sh", os.path.join(ROOT, "app", "syslog-ng", "syslog-ng.sh"), "render"], capture_output=True, text=True, env=e)
        conf = open(e["SYSLOG_NG_CONF"], encoding="utf-8").read() if r.returncode == 0 else ""
        return r, "\n".join(l for l in conf.splitlines() if not l.lstrip().startswith("#"))
_r, _conf = sng_render({})
_r2, _conf2 = sng_render({"TELEGRAF_BIND": "203.0.113.1"})
check("compose の syslog-ng の環境で syslog-ng.sh render が通り、Kafka は localhost:9094-9096 に認証なし（SASL の行が無い）、RFC5424 で読み、"
      "TELEGRAF_BIND が空なら 0.0.0.0、203.0.113.1 ならそこの 5140 で udp と tcp を待つ",
      _r.returncode == 0 and 'bootstrap-servers("localhost:9094,localhost:9095,localhost:9096")' in _conf and "sasl" not in _conf
      and _conf.count('ip("0.0.0.0") port(5140) flags(syslog-protocol)') == 2 and "__" not in _conf
      and _r2.returncode == 0 and _conf2.count('ip("203.0.113.1") port(5140) flags(syslog-protocol)') == 2)
_gc = svc["goflow2"]["command"]
_ga = dict(a[1:].split("=", 1) for a in _gc)
check("goflow2 の引数: NetFlow 2055/udp と sFlow 6343/udp を TELEGRAF_BIND で待ち、telegraf と同じ Kafka の flows へ JSON で書く。/metrics は 8081（8080 は Telegraf の health）。認証と TLS の引数は無い",
      len(_ga) == len(_gc) == 6 and _ga["listen"] == "netflow://${TELEGRAF_BIND:-}:2055,sflow://${TELEGRAF_BIND:-}:6343"
      and _ga["transport"] == "kafka" and _ga["transport.kafka.brokers"] == _tg["KAFKA_BROKERS"] and _ga["transport.kafka.topic"] == "flows"
      and _ga["format"] == "json" and _ga["addr"] == "${TELEGRAF_BIND:-}:8081" and example["HEALTH_PORT"] != "8081")
check("syslog-ng の 5140 は app/containerlab/lab.sh の LOG_PORT（SR Linux の syslog の送り先）と同じ",
      sh_const(lab_sh, "LOG_PORT") == "5140")
check("lab の管理ネットの GW は docker/compose/up.sh・check.sh と app/containerlab/lab.sh で同じ（203.0.113.1）",
      sh_const(read("docker", "compose", "up.sh"), "MGMT_GW") == sh_const(read("docker", "compose", "check.sh"), "MGMT_GW") == sh_const(lab_sh, "MGMT_GW") == "203.0.113.1")
_tg_topics = set(re.findall(r'^\s*topic = "(\w+)"', read("app", "telegraf", "telegraf.conf.in"), re.M))
_gn_topics = set(re.findall(r'^\s*topic: (\w+)$', read("app", "gnmic", "gnmic.yaml.in"), re.M))
_topics = _tg_topics | _gn_topics

# ---- 5. Spark: 1 イメージから 2 サービス。引数は snmp_sinks.py の parse_args が受ける
spec = importlib.util.spec_from_file_location("snmp_sinks", os.path.join(ROOT, "app", "spark", "snmp_sinks.py"))
sinks = importlib.util.module_from_spec(spec); sys.modules["snmp_sinks"] = sinks; spec.loader.exec_module(sinks)

def with_env(env, f):
    saved = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        return f()
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

def spark_args(name, drop_env=()):
    s = svc[name]
    vals = {**example, **UP_ENV}
    argv = [subst(a, vals) for a in s["command"][s["command"].index("/opt/nwc/snmp_sinks.py") + 1:]]
    env = {k: subst(str(v), vals) for k, v in s["environment"].items()}
    env.update({k: None for k in drop_env})
    saved, sys.stderr = sys.stderr, io.StringIO()
    try:
        return with_env(env, lambda: sinks.parse_args(argv))
    except SystemExit:
        return None
    finally:
        sys.stderr = saved

for n in ("spark-splunk", "spark-http"):
    c = svc[n]["command"]
    check(f"{n}: docker/images/spark/Dockerfile の同じイメージ、KAFKA_AUTH=none（PLAINTEXT）、--checkpoint は volume の下、iceberg が無い",
          built(n, "spark") and svc[n]["image"] == "nwc-local-spark" and svc[n]["environment"]["KAFKA_AUTH"] == "none"
          and c[c.index("--checkpoint") + 1] == "file:///opt/spark/work-dir/checkpoint" and "--sinks" in c
          and svc[n]["volumes"] == [f"{n}-ckpt:/opt/spark/work-dir"] and not any("iceberg" in str(a) for a in c))
_a = spark_args("spark-splunk")
check("spark-splunk の引数と環境で parse_args が通る（sinks は splunk、HEC は https://splunk:8088 を検証なしで、token は SPLUNK_HEC_TOKEN）",
      _a is not None and _a.sinks == ["splunk"] and _a.splunk_skip_verify and _a.splunk_hec_url == "https://splunk:8088"
      and _a.bootstrap == "kafka-1:9092,kafka-2:9092,kafka-3:9092")
check("spark-splunk は SPLUNK_HEC_TOKEN が無いと parse_args で止まる（SSM の名前を渡していないので、環境の token が要る）",
      spark_args("spark-splunk", drop_env=("SPLUNK_HEC_TOKEN",)) is None)
_a = spark_args("spark-http")
check("spark-http の引数と環境で parse_args が通る（opensearch,prometheus。remote write は http://prometheus:9090/api/v1/write）",
      _a is not None and _a.sinks == ["opensearch", "prometheus"] and _a.prometheus_url == "http://prometheus:9090/api/v1/write"
      and _a.opensearch_endpoint == "http://opensearch:9200" and svc["spark-http"]["environment"]["OPENSEARCH_AUTH"] == "basic"
      and svc["spark-http"]["environment"]["PROMETHEUS_AUTH"] == "none")
check("up.sh の DEVICE_MAP を Spark の --device-map に入れると、lab の機器の管理 IP が機器名に引ける",
      "dc1-a-leaf-01" in sinks.parse_device_map(_a.device_map).values())
check("Spark の --metric-topics と --log-topics は gnmic が書くトピック（metrics / gnmi）と Telegraf の traps、syslog-ng の logs・GoFlow2 の flows だけ（cycle 012・013）",
      set(_a.metric_topics.split(",")) | set(_a.log_topics.split(",")) == _topics | {"logs", "flows"}
      and _gn_topics == {"metrics", "gnmi"} and _tg_topics == {"traps"})

# ---- 6. OpenSearch / Prometheus / Grafana / Splunk
_os = svc["opensearch"]["environment"]
_os_tf = dict(re.findall(r'\{\s*name\s*=\s*"([\w.]+)",\s*value\s*=\s*"([^"]*)"\s*\}', read("IaC", "terraform", "oss", "pipeline", "analytics", "opensearch.tf")))
check("opensearch は single-node、HTTP の TLS を切る（Spark と Grafana は http:// に Basic 認証）。mmap・TLS・Performance Analyzer は opensearch.tf と同じで、heap は初期と上限が同じ",
      _os["discovery.type"] == "single-node" and _os["plugins.security.ssl.http.enabled"] == "false"
      and all(_os[k] == _os_tf[k] for k in ("node.store.allow_mmap", "plugins.security.ssl.http.enabled", "DISABLE_PERFORMANCE_ANALYZER_AGENT_CLI"))
      and re.fullmatch(r"-Xms(\d+)m -Xmx\1m", _os["OPENSEARCH_JAVA_OPTS"]))
check("prometheus は remote write を受け、設定は prometheus.yml（scrape なし）",
      "--web.enable-remote-write-receiver" in svc["prometheus"]["command"]
      and "./prometheus.yml:/etc/prometheus/prometheus.yml:ro" in svc["prometheus"]["volumes"]
      and "scrape_configs" not in read("docker", "compose", "prometheus.yml"))
_gf = svc["grafana"]["environment"]
check("grafana は PROMETHEUS_AUTH=none / OPENSEARCH_AUTH=basic で、URL は compose の中の prometheus と opensearch、ALERTS_TOPIC_ARN は渡さない",
      _gf["PROMETHEUS_AUTH"] == "none" and _gf["OPENSEARCH_AUTH"] == "basic" and _gf["PROMETHEUS_URL"] == "http://prometheus:9090"
      and _gf["OPENSEARCH_URL"] == "http://opensearch:9200" and "ALERTS_TOPIC_ARN" not in _gf and "ALERTS_TOPIC_ARN" not in svc["splunk"]["environment"])
check("Spark が書く OpenSearch のインデックスと Grafana が読むインデックスが同じ（snmp-logs）",
      _gf["OPENSEARCH_INDEX"] == spark_args("spark-http").opensearch_index == "snmp-logs")
check("splunk の HEC の token は spark-splunk と同じ .env の値", svc["splunk"]["environment"]["SPLUNK_HEC_TOKEN"] == svc["spark-splunk"]["environment"]["SPLUNK_HEC_TOKEN"])

# ---- 7. 設定（.env.example）と compose の ${VAR}
_vars = set(re.findall(r"\$\{(\w+)", read("docker", "compose", "compose.yaml")))
check(".env.example のキーは design.md の 7 つと Telegraf の HEALTH_PORT（009。trap と syslog は lab と揃えるので出さない。MDT_PORT は cycle 012 で外した）と TRex のイメージ（011）",
      set(example) == {"SPLUNK_PASSWORD", "SPLUNK_HEC_TOKEN", "OPENSEARCH_PASSWORD", "GF_SECURITY_ADMIN_PASSWORD", "SRLINUX_IMAGE", "MULTITOOL_IMAGE", "TREX_IMAGE", "AWS_REGION",
                       "HEALTH_PORT"})
check("compose.yaml の ${VAR} は全部 .env.example のキーか up.sh が渡す 3 つ（GNMI_TARGETS / DEVICE_MAP / TELEGRAF_BIND）。SNMP_AGENTS は無い（cycle 013）",
      _vars and _vars <= set(example) | set(UP_ENV) | {"TELEGRAF_BIND"} and "TELEGRAF_BIND" in _vars and "SNMP_AGENTS" not in _vars)
check("SPLUNK_HEC_TOKEN は uuid の形（Splunk のイメージが作る HEC の token。ops/up.sh と同じ形）",
      re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", example["SPLUNK_HEC_TOKEN"]) is not None)
check("docker/compose/.env は git に入らず、.env.example は入る",
      subprocess.run(["git", "check-ignore", "-q", "docker/compose/.env"], cwd=ROOT).returncode == 0
      and subprocess.run(["git", "check-ignore", "-q", "docker/compose/.env.example"], cwd=ROOT).returncode == 1)
check("containerlab が作る app/containerlab/clab-splab/ は git に入らない（.gitignore の app/containerlab/clab-*/）",
      subprocess.run(["git", "check-ignore", "-q", "app/containerlab/clab-splab/topology-data.json"], cwd=ROOT).returncode == 0
      and subprocess.run(["git", "check-ignore", "-q", "app/containerlab/lab.sh"], cwd=ROOT).returncode == 1)
check("docker/ の下に .py が無い（ops/check.sh の ast.parse の対象だが、置かない）",
      not [f for _, _, fs in os.walk(os.path.join(ROOT, "docker")) for f in fs if f.endswith(".py")])
SCRIPTS = ["up.sh", "down.sh", "check.sh", "lab.sh"]
check("docker/compose の 4 つと app/containerlab/lab.sh は実行できる（docker/compose/lab.sh は ../../app/containerlab/lab.sh を直に呼び、lab.sh も自分を \"$SELF\" で呼ぶ）",
      all(os.access(os.path.join(LC, s), os.X_OK) for s in SCRIPTS) and os.access(os.path.join(ROOT, "app", "containerlab", "lab.sh"), os.X_OK))
check("docker/compose の 4 つと app/containerlab/lab.sh は 1 つずつ bash -n が通る（bash -n a b は a しか見ない）",
      all(subprocess.run(["bash", "-n", p]).returncode == 0 for p in [os.path.join(LC, s) for s in SCRIPTS] + [os.path.join(ROOT, "app", "containerlab", "lab.sh")]))
check("ops/check.sh の bash -n は git ls-files '*.sh' の全部（ファイルを並べない）、.py の find に docker がある",
      re.search(r"^SH=\$\(git ls-files '\*\.sh'\)$", read("ops", "check.sh"), re.M) is not None
      and "docker/compose/*.sh" not in read("ops", "check.sh")
      and re.search(r"^find .*\bdocker\b.* -name '\*\.py'", read("ops", "check.sh"), re.M) is not None)
# ops/check.sh の 3 の .sh の部分（log "3. の次の行から .py の前まで）を、git の木で打つ。追跡している .sh（中身は true）を 1 つずつ構文エラー（if だけ）に替えて、
# どの位置でも落ちることを見る。追跡していない .sh（構文エラーのまま）は見ない
_s3 = re.search(r'^log "3\. .*\n((?:.*\n)*?)if command -v python3', read("ops", "check.sh"), re.M).group(1)
def s3_run(cwd):
    return subprocess.run(["bash", "-c", 'set -euo pipefail\ndie() { echo "$*" >&2; exit 1; }\n' + _s3], cwd=cwd, capture_output=True, text=True)
def s3_write(d, f, body):
    os.makedirs(os.path.join(d, os.path.dirname(f)), exist_ok=True)
    with open(os.path.join(d, f), "w") as fh:
        fh.write(body)
_s3_tmp = tempfile.mkdtemp()
_s3_files = ["a.sh", "b/c.sh", "b/d e.sh"]
subprocess.run(["git", "init", "-q", _s3_tmp], check=True)
for _f in _s3_files:
    s3_write(_s3_tmp, _f, "true\n")
s3_write(_s3_tmp, "untracked.sh", "if\n")
subprocess.run(["git", "add", "--", *_s3_files], cwd=_s3_tmp, check=True)
def s3_bad(bad):  # bad のファイルだけ構文エラーにして打ち、終了コードを返す
    s3_write(_s3_tmp, bad, "if\n")
    r = s3_run(_s3_tmp).returncode
    s3_write(_s3_tmp, bad, "true\n")
    return r
_r = s3_run(_s3_tmp)
check("ops/check.sh の 3: git が追跡している .sh のどれか 1 つ（2 番目以降と、名前に空白のあるものを含む）が構文エラーなら落ち、全部通れば 0 で本数を出す（bash -n a b c は a しか見ない。追跡していない .sh は見ない）",
      _r.returncode == 0 and "bash -n: 3 本" in _r.stdout and [f for f in _s3_files if s3_bad(f) == 0] == [])
shutil.rmtree(_s3_tmp)
_s3_tmp = tempfile.mkdtemp()
_r = s3_run(_s3_tmp)
subprocess.run(["git", "init", "-q", _s3_tmp], check=True)
_r2 = s3_run(_s3_tmp)
check("ops/check.sh の 3: git の外か、追跡している .sh が 1 つも無ければ、何も見ずに「通過」にせず止まる",
      _r.returncode != 0 and _r2.returncode != 0 and "git ls-files で .sh が取れない" in _r2.stderr)
shutil.rmtree(_s3_tmp)
_tracked_sh = subprocess.run(["git", "ls-files", "*.sh"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
_r = s3_run(ROOT)
check(f"ops/check.sh の 3: このリポジトリで通り、本数は git ls-files '*.sh' と同じ {len(_tracked_sh)} 本（docker/compose の 4 つと app/containerlab/lab.sh を含む）",
      _r.returncode == 0 and f"bash -n: {len(_tracked_sh)} 本" in _r.stdout
      and {f"docker/compose/{s}" for s in SCRIPTS} | {"app/containerlab/lab.sh"} <= set(_tracked_sh))

# ---- 8. 偽のコマンドで動かす（呼ばれたコマンドを FAKE_LOG に 1 行ずつ書く）
TMP = tempfile.mkdtemp()
BIN = os.path.join(TMP, "bin")
os.mkdir(BIN)
def fake(name, body=""):
    p = os.path.join(BIN, name)
    with open(p, "w") as f:
        f.write('#!/usr/bin/env bash\n{ printf %s "$(basename "$0")"; printf " %s" "$@"; echo; } >> "$FAKE_LOG"\n' + body)
    os.chmod(p, 0o755)
fake("aws", "echo pw\n")
# docker login は --password-stdin を読む（読まないと aws の側が SIGPIPE で落ち、pipefail で lab.sh が止まる）
# SR Linux の CLI（docker exec -i … sr_cli -d）は標準入力を読む。lab.sh の bgp_admin が state を読み直す行には admin-state FAKE_BGP_ADMIN を返し、
# failover の route が たどる dc1-a-leaf-01 → 10.255.1.1/32 の経路は dc1-spine-02（172.16.0.12）だけ（切替のあと）を返す
fake("docker", '[ "${1:-}" = login ] && cat >/dev/null\n'
     'if [ "${1:-}" = exec ] && [ "${2:-}" = -i ]; then case "$(cat)" in\n'
     '  *"info from state / network-instance default protocols bgp neighbor"*) echo "admin-state ${FAKE_BGP_ADMIN:-}" ;;\n'
     '  *"route-table ipv4-unicast route 10.255.1.1/32"*) echo "next-hop-group 7" ;;\n'
     '  *"next-hop-group 7 next-hop * next-hop"*) echo "    next-hop 9" ;;\n'
     '  *"route-table next-hop 9"*) echo "ip-address 172.16.0.12 subinterface ethernet-1/2.0" ;;\n'
     'esac; fi\n'
     # docker compose: config --environment は --env-file の KEY=値 の行をそのまま（クォートも # メモも外さない。本物の読み方は下の本物の compose のテスト）、
     # 続けて自分の環境を出す（あとの方が勝つので、本物と同じくシェルが勝つ）。FAKE_CONFIG_FAIL=1 なら値のかけらを標準エラーに出して 15 で落ちる。
     # ps は FAKE_PS（無ければ spark-splunk と spark-http が running の 2 行）。ほか（up / down）は渡された環境を書く
     'if [ "${1:-}" = compose ]; then case " $* " in\n'
     '  *" config --environment "*) f=; prev=; for a in "$@"; do [ "$prev" = --env-file ] && f=$a; prev=$a; done\n'
     '    [ "${FAKE_CONFIG_FAIL:-0}" = 1 ] && { echo "failed to read $f: line 3: unterminated quoted value \\"SECRETFRAG" >&2; exit 15; }\n'
     '    grep -E "^[A-Za-z_][A-Za-z0-9_]*=" "$f"; env ;;\n'
     '  *" ps "*) if [ -n "${FAKE_PS+x}" ]; then printf "%s\\n" "$FAKE_PS"; else cat <<\'J\'\n'
     '{"Service":"spark-http","State":"running","Status":"Up 5 minutes","ExitCode":0,"Health":""}\n'
     '{"Service":"spark-splunk","State":"running","Status":"Up 5 minutes","ExitCode":0,"Health":""}\n'
     'J\n'
     '    fi ;;\n'
     '  *) echo "ENV SNMP_AGENTS=${SNMP_AGENTS-unset} GNMI_TARGETS=${GNMI_TARGETS-unset} DEVICE_MAP=${DEVICE_MAP-unset} TELEGRAF_BIND=${TELEGRAF_BIND-unset}" >> "$FAKE_LOG" ;;\n'
     'esac; fi\n')
# -S（規則の一覧）には FAKE_IPT_RULES を返す
fake("iptables", 'case " $* " in *" -S"*) printf "%s" "${FAKE_IPT_RULES:-}" ;; esac\n')
# 本物の sudo は環境を消す（env_reset）。PATH と FAKE_LOG（と偽の docker が返す FAKE_BGP_ADMIN）だけ残して、渡された引数を打つ
fake("sudo", 'exec env -i PATH="$PATH" FAKE_LOG="$FAKE_LOG" FAKE_BGP_ADMIN="${FAKE_BGP_ADMIN:-}" "$@"\n')
# lab.sh up が打つ（containerlab は deploy を書くだけ、modprobe は何もしない）
fake("containerlab")
fake("modprobe")
# up.sh と check.sh が lab の管理ネットの GW を探す ip -o -4 addr show。FAKE_GW=1 なら containerlab の bridge に 203.0.113.1 がある（203.0.113.10 は似た別のアドレス）
fake("ip", r'''echo "1: lo    inet 127.0.0.1/8 scope host lo\       valid_lft forever preferred_lft forever"
echo "5: eth0    inet 203.0.113.10/24 brd 203.0.113.255 scope global eth0\       valid_lft forever preferred_lft forever"
[ "${FAKE_GW:-0}" = 1 ] && echo "7: br-1a2b3c4d5e6f    inet 203.0.113.1/24 brd 203.0.113.255 scope global br-1a2b3c4d5e6f\       valid_lft forever preferred_lft forever"
exit 0
''')
# lab.sh failover が待つ sleep と、断を見る snmpwalk（何も返さないので、down が見えるまで 10 回 sleep 1 する）
fake("sleep")
fake("snmpwalk")
# check.sh が打つ ss -Hlun（待っている UDP）。FAKE_SNG=0 か FAKE_DOWN=1 なら syslog-ng の 5140 が無い（51400 と相手側の :5140 は似た別のもの）
fake("ss", r'''echo "UNCONN 0      0       127.0.0.53%lo:53         0.0.0.0:*"
echo "UNCONN 0      0       0.0.0.0:51400            0.0.0.0:*"
echo "UNCONN 0      0       0.0.0.0:41234            203.0.113.11:5140"
[ "${FAKE_SNG:-1}" = 1 ] && [ "${FAKE_DOWN:-0}" != 1 ] && echo "UNCONN 0      0       ${FAKE_SNG_ADDR:-0.0.0.0}:5140      0.0.0.0:*"
exit 0
''')
fake("free", 'printf "               total        used        free\\nMem:  %s  1000  1000\\nSwap:  0  0  0\\n" "${FAKE_MEM:-32000}"\n')
# check.sh が打つ curl。引数と、-K - で渡された標準入力を書き、URL ごとに決めた応答を返す。FAKE_DOWN=1 なら繋がらない（出力なしで 7）。
# Splunk の応答は FAKE_SPLUNK があればそれ（認証の失敗は 401 でも curl -sS は本文を出して 0 で終わる）。Kafka の metrics / gnmi / traps / logs のメッセージ数は FAKE_METRICS / FAKE_GNMI / FAKE_TRAPS / FAKE_LOGS、
# Telegraf の health と GoFlow2 の /metrics の HTTP の番号は FAKE_TG_HEALTH / FAKE_GF_METRICS
fake("curl", r'''prev=; url=
for a in "$@"; do
  case "$a" in http*) url=$a ;; esac
  if [ "$prev" = -K ] && [ "$a" = - ]; then printf 'STDIN %s\n' "$(cat)" >> "$FAKE_LOG"; fi
  prev=$a
done
[ "${FAKE_DOWN:-0}" = 1 ] && exit 7
case "$url" in
  *18080/api/clusters/nwc/topics*) printf '{"topics":[{"name":"metrics","messagesCount":%s},{"name":"gnmi","messagesCount":%s},{"name":"traps","messagesCount":%s},{"name":"logs","messagesCount":%s},{"name":"flows","messagesCount":0}]}\n' "${FAKE_METRICS:-120}" "${FAKE_GNMI:-40}" "${FAKE_TRAPS:-3}" "${FAKE_LOGS:-9}" ;;
  *9090/api/v1/query*) echo '{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1760000000,"12"]}]}}' ;;
  *9200/snmp-logs/_count*) echo "{\"count\":${FAKE_OS_COUNT:-5}}" ;;
  *8089/services/search/jobs/export*)
    if [ -n "${FAKE_SPLUNK:-}" ]; then printf '%s\n' "$FAKE_SPLUNK"
    else printf '%s\n' '{"preview":true,"result":{"count":"0"}}' '{"preview":false,"result":{"count":"7"}}'; fi ;;
  *3000/api/datasources/uid/amp/health*) echo '{"status":"OK","message":"Successfully queried the Prometheus API."}' ;;
  *3000/api/datasources*) echo '[{"uid":"amp","type":"prometheus"},{"uid":"aoss-logs","type":"grafana-opensearch-datasource"}]' ;;
  http://*:8081/metrics) printf '%s' "${FAKE_GF_METRICS:-200}" ;;
  http://*:*/) printf '%s' "${FAKE_TG_HEALTH:-200}" ;;
esac
''')
LOG = os.path.join(TMP, "calls.log")
CLEAN = ("REGISTRY", "AWS_REGION", "PARAM_PREFIX", "TELEGRAF_IMAGE", "TELEGRAF_LOCAL", "SRLINUX_IMAGE", "MULTITOOL_IMAGE", "TREX_IMAGE",
         "SNMP_AGENTS", "GNMI_TARGETS", "DEVICE_MAP", "FAKE_IPT_RULES", "FAKE_MEM", "FAKE_DOWN", "FAKE_OS_COUNT", "FAKE_SPLUNK",
         "FAKE_METRICS", "FAKE_TRAPS", "FAKE_LOGS", "FAKE_GW", "FAKE_TG_HEALTH", "FAKE_GF_METRICS", "FAKE_SNG", "FAKE_SNG_ADDR", "TELEGRAF_BIND", "HEALTH_PORT", "LOG_PORT", "TRAP_PORT",
         "LAB_CMD", "FAKE_BGP_ADMIN", "FAKE_PS", "FAKE_CONFIG_FAIL") + tuple(example)   # .env.example の名前もシェルにあれば .env より勝つので消す

def run(cmd, cwd=None, **env):
    """偽のコマンドを先に置いた PATH で cmd を打ち、(結果, 呼ばれたコマンドの行) を返す。lab に効く環境変数は消してから env を足す"""
    e = {k: v for k, v in os.environ.items() if k not in CLEAN}
    e.update(PATH=BIN + os.pathsep + os.environ["PATH"], FAKE_LOG=LOG, **env)
    open(LOG, "w").close()
    r = subprocess.run(cmd, env=e, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r, open(LOG, encoding="utf-8").read().splitlines()

LAB = os.path.join(ROOT, "app", "containerlab", "lab.sh")
SRL, MT, TREX = example["SRLINUX_IMAGE"], example["MULTITOOL_IMAGE"], example["TREX_IMAGE"]
PULLS = [f"docker pull -q {SRL}", f"docker pull -q {MT}", f"docker pull -q {TREX}"]
REDIRECT = ("iptables -t nat -I PREROUTING 1 -s 203.0.113.0/24 -d 203.0.113.1 -p udp --dport 162 "
            "-m comment --comment nwc-lab-telegraf -j REDIRECT --to-ports 1162")

# app/containerlab/lab.sh pull（design.md の lab の切り替え (1)）
_r, _c = run([LAB, "pull"], SRLINUX_IMAGE=SRL, MULTITOOL_IMAGE=MT, TREX_IMAGE=TREX)
check("app/containerlab/lab.sh pull: REGISTRY が無ければ aws も docker login も打たず、3 つのイメージを docker pull するだけ", _r.returncode == 0 and _c == PULLS)
_r, _c = run([LAB, "pull"], SRLINUX_IMAGE=SRL, MULTITOOL_IMAGE=MT, TREX_IMAGE=TREX, REGISTRY="111122223333.dkr.ecr.ap-northeast-1.amazonaws.com", AWS_REGION="ap-northeast-1")
check("app/containerlab/lab.sh pull: REGISTRY があれば（lab の EC2）今までどおり ECR に login してから pull する",
      _r.returncode == 0 and sorted(_c[:2]) + _c[2:] == ["aws ecr get-login-password --region ap-northeast-1",
                                    "docker login --username AWS --password-stdin 111122223333.dkr.ecr.ap-northeast-1.amazonaws.com"] + PULLS)
_r, _c = run([LAB, "pull"], SRLINUX_IMAGE=SRL, MULTITOOL_IMAGE=MT, TREX_IMAGE=TREX, REGISTRY="111122223333.dkr.ecr.ap-northeast-1.amazonaws.com")
check("app/containerlab/lab.sh pull: REGISTRY があって AWS_REGION が無ければ、今までどおり何も取らずに止まる", _r.returncode != 0 and _c == [])

# app/containerlab/lab.sh forward（(2)。手元とデバッグ用の EC2 は REDIRECT だけ、それ以外は stream の分岐で SSM を読む）
_r, _c = run([LAB, "forward"], TELEGRAF_LOCAL="1")
check("app/containerlab/lab.sh forward: TELEGRAF_LOCAL=1 なら aws を打たず、trap の 162 → 1162 の REDIRECT を 1 本だけ入れ、案内は「compose の Telegraf へ」",
      _r.returncode == 0 and [c for c in _c if " -I " in c] == [REDIRECT] and not [c for c in _c if c.startswith("aws")]
      and "compose の Telegraf へ: trap 162/udp を 1162/udp へ向けた" in _r.stdout)
_r, _c = run([LAB, "forward"], TELEGRAF_IMAGE="111122223333.dkr.ecr.ap-northeast-1.amazonaws.com/nwc-telegraf:1")
check("app/containerlab/lab.sh forward: TELEGRAF_IMAGE（デバッグ用の EC2）は今までどおり同じ REDIRECT で、案内は「この EC2 の Telegraf へ」",
      _r.returncode == 0 and [c for c in _c if " -I " in c] == [REDIRECT] and "この EC2 の Telegraf へ" in _r.stdout)
_r, _c = run([LAB, "forward"], TELEGRAF_LOCAL="0")
check("app/containerlab/lab.sh forward: TELEGRAF_LOCAL が 1 でなければ stream の分岐（AWS_REGION が要る）へ行き、REDIRECT を入れない",
      _r.returncode != 0 and not [c for c in _c if " -I " in c])

# hint（(2)。障害を入れたあとにどこを見るか）は関数を切り出して呼ぶ
_fn = re.search(r"^local_telegraf\(\).*?^}\n", lab_sh, re.S | re.M).group(0)
def hint(**env):
    return run(["bash", "-c", f"FW_TAG=nwc-lab-telegraf\n{_fn}hint 'EC2 の文' 'stream の文'"], **env)[0].stdout
check("app/containerlab/lab.sh hint: TELEGRAF_LOCAL=1 なら compose の Grafana と Splunk を案内し、TELEGRAF_IMAGE と stream と転送なしの案内は変わらない",
      "compose の Telegraf: 数分で Grafana（:3000）と Splunk（:8000）" in hint(TELEGRAF_LOCAL="1")
      and hint(TELEGRAF_IMAGE="x") == "  この EC2 の Telegraf: EC2 の文\n"
      and hint(FAKE_IPT_RULES="-A PREROUTING -m comment --comment nwc-lab-telegraf -j DNAT\n") == "  stream: stream の文\n"
      and "転送が張られていない" in hint())
_fail = re.search(r"\n  failover\)(.*?)\n    ;;", lab_sh, re.S).group(1)
check("app/containerlab/lab.sh failover（fail-main のあとの案内。TELEGRAF_IMAGE の分岐があるのはここ）: TELEGRAF_IMAGE の次に local_telegraf の分岐があり、compose の Grafana と Splunk を案内する",
      re.search(r'if \[ -n "\$\{TELEGRAF_IMAGE:-\}" \]; then.*?elif local_telegraf; then\s+echo "== Telegraf（compose の Telegraf）=="', _fail, re.S) is not None)
check("app/containerlab/lab.sh: /etc/*-lab.env が無くても止まらない（手元。ls が失敗しても || true）",
      'ENV_FILE=$(ls /etc/*-lab.env 2>/dev/null | head -1 || true)' in lab_sh and '[ -n "$ENV_FILE" ] && set -a' in lab_sh)

# 置き場所を写した木（tmp/docker/compose と、tmp/app/containerlab → リポジトリの app/containerlab）で docker/compose の 4 つを動かす
def tree(env=None, files=SCRIPTS, lab_copy=False):
    t = tempfile.mkdtemp(dir=TMP)
    lc = os.path.join(t, "docker", "compose")
    os.makedirs(lc)
    os.makedirs(os.path.join(t, "app"))
    for s in files + [".env.example", "compose.yaml"]:
        shutil.copy2(os.path.join(LC, s), lc)
    if lab_copy:  # lab.sh render が splab.clab.yml を書くので、リポジトリの app/containerlab ではなく写しに書かせる
        shutil.copytree(os.path.join(ROOT, "app", "containerlab"), os.path.join(t, "app", "containerlab"), ignore=shutil.ignore_patterns("clab-*", "splab.clab.yml", "__pycache__"))
    else:
        os.symlink(os.path.join(ROOT, "app", "containerlab"), os.path.join(t, "app", "containerlab"))
    if env is not None:  # 試し用の .env（中身はこのテストが書く）
        with open(os.path.join(lc, ".env"), "w") as f:
            f.write(env)
    return lc

# docker/compose/lab.sh
_lc = tree()
_r, _c = run([os.path.join(_lc, "lab.sh"), "pull"], REGISTRY="leak.example.com", AWS_REGION="ap-northeast-1", PARAM_PREFIX="/nwc")
check("docker/compose/lab.sh: .env が無ければ .env.example を docker compose config --environment で読み、そのイメージ 3 つと TELEGRAF_LOCAL=1、案内に出す自分のパス LAB_CMD の 5 つだけを sudo env で app/containerlab/lab.sh に渡す（sudo -E にしない）",
      _r.returncode == 0 and _c[0] == "docker compose --env-file .env.example config --environment"
      and _c[1].split()[:7] == ["sudo", "env", f"SRLINUX_IMAGE={SRL}", f"MULTITOOL_IMAGE={MT}", f"TREX_IMAGE={TREX}", "TELEGRAF_LOCAL=1", f"LAB_CMD={_lc}/lab.sh"]
      and os.path.realpath(_c[1].split()[7]) == os.path.realpath(LAB) and _c[1].split()[8:] == ["pull"])
check("docker/compose/lab.sh pull: シェルに REGISTRY や AWS_REGION があっても ECR に行かず、上流（ghcr.io と Docker Hub）の 3 つを取る", _c[2:] == PULLS)
_r, _c = run([os.path.join(_lc, "lab.sh"), "forward"])
check("docker/compose/lab.sh forward: REDIRECT を 1 本入れ、案内は「compose の Telegraf へ」（aws は打たない）",
      _r.returncode == 0 and [c for c in _c if " -I " in c] == [REDIRECT] and "compose の Telegraf へ" in _r.stdout
      and not [c for c in _c if c.startswith("aws")])
_lc = tree("SRLINUX_IMAGE=example.com/srl:1\nMULTITOOL_IMAGE=example.com/mt:2\nTREX_IMAGE=example.com/trex:3\n")
_r, _c = run([os.path.join(_lc, "lab.sh"), "status"])
_r2, _c2 = run([os.path.join(_lc, "lab.sh"), "status"], SRLINUX_IMAGE="example.com/shell:9")
check("docker/compose/lab.sh: .env があればそれを読んでそのイメージを使い、シェルに同じ名前があればそちらが勝つ（compose と同じ。クォートや # メモの読み方は下の本物の compose のテスト）",
      _c[0] == "docker compose --env-file .env config --environment"
      and _c[1].split()[2:5] == ["SRLINUX_IMAGE=example.com/srl:1", "MULTITOOL_IMAGE=example.com/mt:2", "TREX_IMAGE=example.com/trex:3"]
      and _c2[1].split()[2:5] == ["SRLINUX_IMAGE=example.com/shell:9", "MULTITOOL_IMAGE=example.com/mt:2", "TREX_IMAGE=example.com/trex:3"])
_lc = tree("SRLINUX_IMAGE=example.com/srl:1\n")
_r, _c = run([os.path.join(_lc, "lab.sh"), "up"])
check("docker/compose/lab.sh: .env に MULTITOOL_IMAGE が無ければ sudo を打たずに止まる",
      _r.returncode != 0 and _c == ["docker compose --env-file .env config --environment"] and "MULTITOOL_IMAGE が無い" in _r.stderr)
_lc = tree("SRLINUX_IMAGE=example.com/srl:1\nMULTITOOL_IMAGE=example.com/mt:2\n")
_r, _c = run([os.path.join(_lc, "lab.sh"), "up"])
check("docker/compose/lab.sh: .env に TREX_IMAGE が無ければ（011 より前の .env）sudo を打たずに止まる",
      _r.returncode != 0 and _c == ["docker compose --env-file .env config --environment"] and "TREX_IMAGE が無い" in _r.stderr)
_r, _c = run([os.path.join(tree(), "lab.sh"), "up"], FAKE_CONFIG_FAIL="1")
check("docker/compose/lab.sh: compose が .env を読めなければ sudo を打たずに止まり、compose のエラー（値のかけらを含むことがある）は出さない",
      _r.returncode == 1 and _c == ["docker compose --env-file .env.example config --environment"]
      and "docker compose が .env.example を読めない" in _r.stderr and "SECRETFRAG" not in _r.stdout + _r.stderr)
# app/containerlab/lab.sh up は splab.clab.yml があると render しないので、gen_lab.py で台数を変えたあとも古い yml で deploy する。ラッパーが毎回 render する
_lc = tree(lab_copy=True)
_yml = os.path.join(os.path.dirname(os.path.dirname(_lc)), "app", "containerlab", "splab.clab.yml")
with open(_yml, "w") as f:
    f.write("古い yml（gen_lab.py の前）\n")
_r, _c = run([os.path.join(_lc, "lab.sh"), "up"])
check("docker/compose/lab.sh up: app/containerlab/lab.sh render を打ってから up し、splab.clab.yml を今の .in と .env のイメージで作り直してから deploy する",
      _r.returncode == 0
      and [c.split()[8] if c.startswith("sudo ") else c for c in _c if c.startswith(("sudo ", "containerlab "))]
      == ["render", "up", "containerlab deploy -t splab.clab.yml --reconfigure"]
      and open(_yml, encoding="utf-8").read() == read("app", "containerlab", "splab.clab.yml.in").replace("__SRLINUX_IMAGE__", SRL).replace("__MULTITOOL_IMAGE__", MT).replace("__TREX_IMAGE__", TREX))
check("app/containerlab/lab.sh render: 作った splab.clab.yml のイメージ名（SRLINUX_IMAGE と MULTITOOL_IMAGE と TREX_IMAGE）を出す（手元は REGISTRY が無い）",
      f"splab.clab.yml を作った（イメージは {SRL} と {MT} と {TREX}）" in _r.stdout)
_r, _c = run([os.path.join(_lc, "lab.sh"), "down"])
check("docker/compose/lab.sh: up 以外（down など）は render しない", [c.split()[8] for c in _c if c.startswith("sudo ")] == ["down"])

# app/containerlab/lab.sh の障害と戻しの案内（(9)・(11)）。偽の docker / iptables / sleep / snmpwalk で fail-main / fail-bgp / heal-bgp / failover を打つ。
# 「戻すのは …」などの打ち方は呼ばれ方で決まる: 手元はラッパーが渡す LAB_CMD、EC2 は PATH の lab（sudo lab）、それ以外は呼ばれたパス（sudo <パス>）
_lc = tree()
_W = os.path.join(_lc, "lab.sh")
os.symlink(LAB, os.path.join(BIN, "lab"))
_r, _c = run([_W, "fail-bgp"], FAKE_BGP_ADMIN="disable")
check("docker/compose/lab.sh fail-bgp: 隣接を止め、compose の Grafana と Splunk を案内し、戻すのは 'docker/compose/lab.sh heal-bgp'（ラッパーの打ち方。EC2 の lab ではない）",
      _r.returncode == 0 and f"compose の Telegraf: 数分で Grafana（:3000）と Splunk（:8000）に出る（SNS のトピックは無い）。戻すのは '{_W} heal-bgp'\n" in _r.stdout
      and "dc1-spine-01 = 10.255.0.1）を止める" in _r.stdout and "'lab " not in _r.stdout and "sudo" not in _r.stdout)
_r, _c = run([_W, "heal-bgp"], FAKE_BGP_ADMIN="enable")
check("docker/compose/lab.sh heal-bgp: 隣接を戻し、確かめるのは 'docker/compose/lab.sh check'",
      _r.returncode == 0 and f"（10.255.0.1）を戻す。established に戻るまで数十秒（'{_W} check' で見る）" in _r.stdout)
_r, _c = run([_W, "fail-bgp"], FAKE_BGP_ADMIN="enable")
check("lab.sh fail-bgp: 読み直した admin-state が disable でなければ案内を出さずに 1 で止まる", _r.returncode == 1 and "戻すのは" not in _r.stdout)
_r, _c = run([_W, "fail-main"])
check("docker/compose/lab.sh fail-main: 回線を落とし、切替の確認は 'docker/compose/lab.sh failover'、戻すのは 'docker/compose/lab.sh heal-main'",
      _r.returncode == 0 and "docker exec clab-splab-dc1-a-leaf-01 ip link set e1-1 down" in _c
      and f"切替の確認は '{_W} failover' が待ってくれる。戻すのは '{_W} heal-main'" in _r.stdout)
_r, _c = run([_W, "failover"])
check("docker/compose/lab.sh failover: fail-main を打って切替を見て、SNMP の断を待ち（sleep）、最後に compose の Grafana と Splunk を案内し、戻すのは 'docker/compose/lab.sh heal-main'",
      _r.returncode == 0 and "docker exec clab-splab-dc1-a-leaf-01 ip link set e1-1 down" in _c and "  切替 OK（5 秒以内）" in _r.stdout and _c.count("sleep 1") == 10
      and "== Telegraf（compose の Telegraf）==" in _r.stdout
      and _r.stdout.rstrip("\n").endswith(f"Splunk（:8000）に linkDown の trap と syslog が出る。戻すのは '{_W} heal-main'"))
_r, _c = run(["bash", "app/containerlab/lab.sh", "fail-bgp"], cwd=ROOT, FAKE_BGP_ADMIN="disable", TELEGRAF_LOCAL="1")
_r2, _c2 = run(["bash", "app/containerlab/lab.sh", "failover"], cwd=ROOT, TELEGRAF_LOCAL="1")
_r3, _c3 = run(["bash", "app/containerlab/lab.sh", "fail-bgp"], cwd=ROOT, FAKE_BGP_ADMIN="disable")
_r4, _c4 = run(["bash", "app/containerlab/lab.sh", "fail-bgp"], cwd=ROOT, FAKE_BGP_ADMIN="disable", TELEGRAF_IMAGE="x")
_r5, _c5 = run(["bash", "app/containerlab/lab.sh", "failover"], cwd=ROOT, TELEGRAF_IMAGE="x")
check("app/containerlab/lab.sh を直に打つ（LAB_CMD なし、PATH に無い）と、案内は呼ばれたパスに sudo を付けた 'sudo app/containerlab/lab.sh …'（design.md の検証方法 8・9・11）",
      _r.returncode == 0 and "。戻すのは 'sudo app/containerlab/lab.sh heal-bgp'\n" in _r.stdout
      and _r2.returncode == 0 and "切替の確認は 'sudo app/containerlab/lab.sh failover' が待ってくれる。戻すのは 'sudo app/containerlab/lab.sh heal-main'" in _r2.stdout
      and _r2.stdout.rstrip("\n").endswith("戻すのは 'sudo app/containerlab/lab.sh heal-main'")
      and _r3.returncode == 0 and _r3.stdout.endswith("  Telegraf への転送が張られていない（sudo app/containerlab/lab.sh forward-status）\n")
      and _r4.returncode == 0 and _r4.stdout.endswith("  この EC2 の Telegraf: BGP の状態は gnmic が取るので、ここには出ない（'sudo app/containerlab/lab.sh check' で established 以外になったのを見る）。戻すのは 'sudo app/containerlab/lab.sh heal-bgp'\n")
      and _r5.returncode == 0 and _r5.stdout.rstrip("\n").endswith("'sudo app/containerlab/lab.sh telegraf logs' に snmp_trap の linkDown が出る（IF / IS-IS の状態は gnmic が取るので、この EC2 では出ない）。戻すのは 'sudo app/containerlab/lab.sh heal-main'"))
_r, _c = run(["lab", "fail-bgp"], FAKE_BGP_ADMIN="disable", TELEGRAF_IMAGE="x")
_r2, _c2 = run(["lab", "failover"], TELEGRAF_IMAGE="x")
_r3, _c3 = run(["lab", "fail-bgp"], FAKE_BGP_ADMIN="disable", FAKE_IPT_RULES="-A PREROUTING -m comment --comment nwc-lab-telegraf -j DNAT\n")
_r4, _c4 = run(["lab", "fail-bgp"], FAKE_BGP_ADMIN="disable")
check("EC2（PATH の lab で打つ）は今までどおり 'sudo lab …': デバッグ用の EC2 は check / Telegraf のログと heal-bgp / heal-main（trap だけなので BGP と IF の状態は出ないと言う。cycle 013）、stream は SNS と heal-bgp、転送なしは forward-status",
      _r.stdout.endswith("  この EC2 の Telegraf: BGP の状態は gnmic が取るので、ここには出ない（'sudo lab check' で established 以外になったのを見る）。戻すのは 'sudo lab heal-bgp'\n")
      and _r2.stdout.rstrip("\n").endswith("'sudo lab telegraf logs' に snmp_trap の linkDown が出る（IF / IS-IS の状態は gnmic が取るので、この EC2 では出ない）。戻すのは 'sudo lab heal-main'")
      and "切替の確認は 'sudo lab failover' が待ってくれる。戻すのは 'sudo lab heal-main'" in _r2.stdout
      and _r3.stdout.endswith("を SNS のトピックに出す。戻すのは 'sudo lab heal-bgp'\n") and "  stream: 数分で Grafana と Splunk の両方が bgp_down（dc1-a-leaf-01 の 10.255.0.1 と" in _r3.stdout
      and _r4.stdout.endswith("  Telegraf への転送が張られていない（sudo lab forward-status）\n")
      and all(r.returncode == 0 for r in (_r, _r2, _r3, _r4)))
os.remove(os.path.join(BIN, "lab"))

# docker/compose/up.sh / down.sh
_lc = tree()
_r, _c = run([os.path.join(_lc, "up.sh")])
check("up.sh: .env が無ければ docker を打たずに止まり、cp .env.example .env と、パスワードを変えるなら最初の up.sh の前（あとからなら down.sh -v）を案内する",
      _r.returncode == 1 and _c == [] and "cp .env.example .env" in _r.stderr and "最初の up.sh の前" in _r.stderr and "down.sh -v" in _r.stderr)
_lc = tree(read("docker", "compose", ".env.example"))
_r, _c = run([os.path.join(_lc, "up.sh")])
_upenv = f"ENV SNMP_AGENTS=unset GNMI_TARGETS={UP_ENV['GNMI_TARGETS']} DEVICE_MAP={UP_ENV['DEVICE_MAP']}"
check("up.sh: app/containerlab/lab_topology.py の 2 つ（GNMI_TARGETS / DEVICE_MAP）を環境で渡して docker compose up -d --build を打つ（SNMP_AGENTS は cycle 013 で渡さない）。"
      "lab の管理ネットの GW（203.0.113.1）が無ければ TELEGRAF_BIND を空にして（全部のインターフェース）WARNING で lab.sh up のあとの up.sh telegraf syslog-ng goflow2 を案内する",
      _r.returncode == 0 and _c == ["ip -o -4 addr show", "docker compose up -d --build", f"{_upenv} TELEGRAF_BIND="]
      and all(UP_ENV.values()) and "WARNING: lab の管理ネット（203.0.113.1）がまだ無いので、Telegraf・syslog-ng・GoFlow2 は" in _r.stderr
      and "docker/compose/up.sh telegraf syslog-ng goflow2 で 203.0.113.1 だけに直す" in _r.stderr)
_r, _c = run([os.path.join(_lc, "up.sh")], FAKE_GW="1", TELEGRAF_BIND="10.9.9.9")
check("up.sh: host に 203.0.113.1（lab.sh up が作る bridge）があれば TELEGRAF_BIND=203.0.113.1 で渡し、WARNING を出さない（シェルの TELEGRAF_BIND は使わない。203.0.113.10 と取り違えない）",
      _r.returncode == 0 and _c == ["ip -o -4 addr show", "docker compose up -d --build", f"{_upenv} TELEGRAF_BIND=203.0.113.1"] and "WARNING" not in _r.stderr)
_r, _c = run([os.path.join(_lc, "up.sh"), "telegraf"])
check("up.sh: 引数は docker compose up に渡す（up.sh telegraf で Telegraf だけ作り直す）", _r.returncode == 0 and _c[1] == "docker compose up -d --build telegraf")
_r, _c = run([os.path.join(_lc, "down.sh"), "-v"])
check("down.sh -v: docker compose down -v", _r.returncode == 0 and _c[0] == "docker compose down -v")

# docker/compose/check.sh（design.md の検証方法「WSL」の 4）
PW = example["OPENSEARCH_PASSWORD"]
_lc = tree(read("docker", "compose", ".env.example"))
_r, _c = run([os.path.join(_lc, "check.sh")])
_ok = [l for l in _r.stdout.splitlines() if l.startswith("ok  ")]
check("check.sh: 応答が全部そろえば 15 項目とも ok で「すべて ok」、終了コード 0（メモリが 20 GB 以上なら注意を出さない）",
      _r.returncode == 0 and len(_ok) == 15 and _r.stdout.splitlines()[-1] == "すべて ok" and "注意" not in _r.stdout)
check("check.sh: .env は docker compose --env-file .env config --environment で読み、Spark の 2 つは docker compose ps -a --format json で 1 回だけ見る",
      [c for c in _c if c.startswith("docker ")] == ["docker compose --env-file .env config --environment", "docker compose ps -a --format json spark-splunk spark-http"])
_argv = [c for c in _c if c.startswith("curl ")]
_stdin = [c for c in _c if c.startswith("STDIN ")]
check("check.sh: パスワードは curl の引数に載せず（ps に出る）、-K - の標準入力で user = \"admin:…\" として渡す（OpenSearch・Splunk・Grafana 2 つの 4 回）。Kafka のトピックの一覧は 1 回だけ取る",
      len(_argv) == 8 and len([c for c in _argv if "18080/api/clusters/nwc/topics" in c]) == 1 and not [c for c in _argv if PW in c] and _stdin == [f'STDIN user = "admin:{PW}"'] * 4)
check("check.sh: Splunk の検索は sourcetype=netops:*（Spark の SPLUNK_SOURCETYPE_PREFIX）、Prometheus は Grafana のダッシュボードとアラートが使う snmp_interface_oper_up（gnmic の interface_state を Spark が読み替えた系列）",
      sinks.SPLUNK_SOURCETYPE_PREFIX == "netops" and any("sourcetype=netops:*" in c for c in _argv)
      and "snmp_interface_oper_up" in read("app", "grafana", "provisioning", "dashboards", "metrics.json")
      and "snmp_interface_oper_up" in read("app", "grafana", "provisioning", "alerting", "netops-prometheus.yaml")
      and any("query=count(snmp_interface_oper_up)" in c for c in _argv))
check("check.sh: Grafana で見る uid（amp / aoss-logs）は app/grafana/provisioning/datasources-oss の定義にある",
      {m for f in ("prometheus.yaml", "opensearch.yaml")
       for m in re.findall(r"uid: (\S+)", read("app", "grafana", "provisioning", "datasources-oss", f))} == {"amp", "aoss-logs"})
check("check.sh: Kafka で見るトピック（metrics / gnmi / traps / logs / flows。メッセージ数は metrics と gnmi と traps と logs）は gnmic（metrics / gnmi）か Telegraf（traps）か syslog-ng（logs）か GoFlow2（flows）が書くトピック（cycle 012・013）",
      "{'metrics', 'gnmi', 'traps', 'logs', 'flows'}" in read("docker", "compose", "check.sh")
      and {"metrics", "gnmi", "traps", "logs", "flows"} == _topics | {"logs", _ga["transport.kafka.topic"]})
TH, GF, SG = "Telegraf: health が 200", "GoFlow2: /metrics が 200", "syslog-ng: udp 5140 を待っている"
check("check.sh: Telegraf の health は 203.0.113.1 が無ければ 127.0.0.1 の .env の HEALTH_PORT（8080）に、GoFlow2 の /metrics は 127.0.0.1:8081 に、認証なしで打つ",
      [c for c in _argv if c.endswith(":8080/")] == ["curl -sS --max-time 60 -o /dev/null -w %{http_code} http://127.0.0.1:8080/"] and f"ok  {TH}" in _ok
      and [c for c in _argv if "8081" in c] == ["curl -sS --max-time 60 -o /dev/null -w %{http_code} http://127.0.0.1:8081/metrics"] and f"ok  {GF}" in _ok
      and f"ok  {SG}" in _ok)
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_GW="1")
check("check.sh: 203.0.113.1 があれば（up.sh が TELEGRAF_BIND にしたアドレス）そこの Telegraf の health と GoFlow2 の /metrics に打つ",
      _r.returncode == 0 and [c.split()[-1] for c in _c if c.startswith("curl ") and "-w" in c] == ["http://203.0.113.1:8080/", "http://203.0.113.1:8081/metrics"])
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_TG_HEALTH="000")
check("check.sh: Telegraf の health に繋がらない（restart の上限で止まった、bind に失敗した）なら NG で、ps -a と logs telegraf と up.sh telegraf を案内する",
      _r.returncode == 1 and [l for l in _r.stdout.splitlines() if TH in l]
      == [f"NG  {TH}: 繋がらない（docker compose ps -a telegraf が Exited なら logs telegraf で理由を見て up.sh telegraf）"])
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_TG_HEALTH="503")
check("check.sh: Telegraf の health が 200 以外なら HTTP の番号を出して NG", _r.returncode == 1 and any(l.startswith(f"NG  {TH}: HTTP 503（") for l in _r.stdout.splitlines()))
_r, _c = run([os.path.join(tree(read("docker", "compose", ".env.example").replace("HEALTH_PORT=8080", "HEALTH_PORT=18081")), "check.sh")])
_r2, _c2 = run([os.path.join(tree(read("docker", "compose", ".env.example")), "check.sh")], HEALTH_PORT="18082")
check("check.sh: health のポートは compose と同じくシェルの HEALTH_PORT、.env の HEALTH_PORT の順",
      [c.split()[-1] for c in _c if c.startswith("curl ") and "-w" in c] == ["http://127.0.0.1:18081/", "http://127.0.0.1:8081/metrics"]
      and [c.split()[-1] for c in _c2 if c.startswith("curl ") and "-w" in c] == ["http://127.0.0.1:18082/", "http://127.0.0.1:8081/metrics"])
KM, KT, KG = "Kafka: metrics のメッセージ数 > 0", "Kafka: traps のメッセージ数 > 0", "Kafka: gnmi のメッセージ数 > 0"
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_METRICS="0")
check("check.sh: Kafka の metrics のメッセージ数が 0 なら NG（gnmic の sample から届いていない。トピックは Spark が作るのであっても証拠にならない）",
      _r.returncode == 1 and f"NG  {KM}: 0 件" in _r.stdout.splitlines() and f"ok  {KT}" in _r.stdout.splitlines() and f"ok  {KG}" in _r.stdout.splitlines())
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_GNMI="0")
check("check.sh: Kafka の gnmi のメッセージ数が 0 なら、metrics が届いていても NG で logs gnmic を案内する（on-change の購読だけが断られた。cycle 013 のセルフレビュー F5）",
      _r.returncode == 1 and f"ok  {KM}" in _r.stdout.splitlines()
      and [l for l in _r.stdout.splitlines() if KG in l] == [f"NG  {KG}: 0 件（gnmic の on-change（IF・BGP・IS-IS の状態）が届いていない。購読した直後に今の値を 1 回送るので、gnmic が繋がっていれば 0 にならない。docker compose logs gnmic）"])
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_TRAPS="0")
check("check.sh: Kafka の traps が 0 件なら NG にせず「注意」で fail-main か trap-test を案内し、ほかが ok なら「すべて ok」で 0",
      _r.returncode == 0 and f"ok  {KM}" in _r.stdout.splitlines() and _r.stdout.splitlines()[-1] == "すべて ok"
      and [l for l in _r.stdout.splitlines() if KT in l]
      == [f"注意 {KT}: 0 件（trap は障害を入れるまで来ない。docker/compose/lab.sh fail-main か trap-test のあとに打ち直す）"])
KL = "Kafka: logs のメッセージ数 > 0"
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_LOGS="0")
check("check.sh: Kafka の logs が 0 件なら NG にせず「注意」で fail-main と logs syslog-ng を案内し、ほかが ok なら「すべて ok」で 0（cycle 012）",
      _r.returncode == 0 and _r.stdout.splitlines()[-1] == "すべて ok" and f"ok  {KL}" not in _r.stdout.splitlines()
      and [l for l in _r.stdout.splitlines() if KL in l]
      == [f"注意 {KL}: 0 件（syslog は機器が出すまで来ない。docker/compose/lab.sh fail-main のあとに打ち直す。来ないままなら docker compose logs syslog-ng）"])
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_SNG="0")
_r2, _c2 = run([os.path.join(_lc, "check.sh")], FAKE_SNG_ADDR="203.0.113.1")
check("check.sh: syslog-ng は ss -Hlun の待っているアドレスが :5140 で終わる行があれば ok（0.0.0.0 でも 203.0.113.1 でも）。"
      "無ければ（51400 と相手側の :5140 は数えない）NG で ps -a と logs syslog-ng と up.sh syslog-ng を案内する",
      _r.returncode == 1 and [l for l in _r.stdout.splitlines() if SG in l]
      == [f"NG  {SG}: 待っていない（docker compose ps -a syslog-ng が Exited なら logs syslog-ng で理由を見て up.sh syslog-ng）"]
      and "ss -Hlun" in _c and _r2.returncode == 0 and f"ok  {SG}" in _r2.stdout.splitlines())
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_GF_METRICS="000")
_r2, _c2 = run([os.path.join(_lc, "check.sh")], FAKE_GF_METRICS="404")
check("check.sh: GoFlow2 の /metrics に繋がらなければ NG で ps -a と logs goflow2 と up.sh goflow2 を案内し、200 以外なら HTTP の番号を出して NG",
      _r.returncode == 1 and [l for l in _r.stdout.splitlines() if GF in l]
      == [f"NG  {GF}: 繋がらない（docker compose ps -a goflow2 が Exited なら logs goflow2 で理由を見て up.sh goflow2）"]
      and _r2.returncode == 1 and any(l.startswith(f"NG  {GF}: HTTP 404（") for l in _r2.stdout.splitlines()))
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_OS_COUNT="0", FAKE_MEM="16000")
check("check.sh: 1 つが 0 件なら、そこだけ NG にして残りも見てから終了コード 1。メモリが 20 GB 未満なら注意を出す",
      _r.returncode == 1 and "NG  OpenSearch: snmp-logs の件数 > 0: 0 件" in _r.stdout and len([l for l in _r.stdout.splitlines() if l.startswith("ok  ")]) == 14
      and "注意: メモリが 16000 MiB" in _r.stdout and _r.stdout.splitlines()[-1].startswith("NG がある"))
SPL = "Splunk: sourcetype=netops:* の直近 10 分の件数 > 0"
def splunk_line(body):  # Splunk の応答を body にして check.sh を打ち、Splunk の行を返す
    _r, _c = run([os.path.join(_lc, "check.sh")], FAKE_SPLUNK=body)
    return [l for l in _r.stdout.splitlines() if SPL in l]
check("check.sh: Splunk の認証の失敗（messages の FATAL / ERROR）は「0 件」と分けてその理由を（result の行があっても）、result が 1 行も無ければ「result が無い」と messages を出す。count が 0 なら「0 件」、3 なら ok",
      splunk_line('{"messages":[{"type":"FATAL","text":"Unauthorized"}]}') == [f"NG  {SPL}: FATAL Unauthorized"]
      and splunk_line('{"messages":[{"type":"ERROR","text":"Unauthorized"}]}\n{"result":{"count":"5"}}') == [f"NG  {SPL}: ERROR Unauthorized"]
      and splunk_line('{"messages":[{"type":"WARN","text":"call not properly authenticated"}]}') == [f"NG  {SPL}: result が無い: WARN call not properly authenticated"]
      and splunk_line('{"result":{"count":0}}') == [f"NG  {SPL}: 0 件"]
      and splunk_line('{"result":{"count":3}}') == [f"ok  {SPL}"])
_dup = '{"messages":[' + ",".join(['{"type":"ERROR","text":"Unauthorized"}'] * 30) + ']}'
_long = '{"messages":[{"type":"FATAL","text":"' + "x" * 300 + '\\n  y"},{"type":"ERROR","text":"b\\nc"}]}'
_ll = splunk_line(_long)
check("check.sh: Splunk の理由は同じものを 1 つにし（ERROR Unauthorized × 30 → 1 つ）、改行は空白にして 1 行、理由は 200 字まで",
      splunk_line(_dup) == [f"NG  {SPL}: ERROR Unauthorized"]
      and splunk_line('{"messages":[{"type":"WARN","text":"a"},{"type":"WARN","text":"a"}]}') == [f"NG  {SPL}: result が無い: WARN a"]
      and len(_ll) == 1 and _ll[0].startswith(f"NG  {SPL}: ERROR b c; FATAL xxx") and len(_ll[0]) - len(f"NG  {SPL}: ") == 200)
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_DOWN="1")
check("check.sh: どこにも繋がらなくても set -e で途中で落ちず、13 項目とも NG（JSON の 10 は「読めない応答: 空」、Telegraf と GoFlow2 は「繋がらない」、syslog-ng は「待っていない」）で終了コード 1",
      _r.returncode == 1 and _r.stdout.count("読めない応答: 空") == 10 and _r.stdout.count("NG  ") == 13 and f"NG  {TH}: 繋がらない（" in _r.stdout
      and f"NG  {GF}: 繋がらない（" in _r.stdout and f"NG  {SG}: 待っていない（" in _r.stdout)
_lc = tree('OPENSEARCH_PASSWORD=a"b\\c\nSPLUNK_PASSWORD=x\nGF_SECURITY_ADMIN_PASSWORD=y\n')
_r, _c = run([os.path.join(_lc, "check.sh")])
check("check.sh: パスワードの \" と \\ は curl の設定の書き方で逃がす", 'STDIN user = "admin:a\\"b\\\\c"' in _c)
# Spark の 2 つ（restart: on-failure:5 で止まった、依存が healthy にならず Created のまま、コンテナが無い）
SPK = [f"Spark: {n} が動いている" for n in ("spark-splunk", "spark-http")]
def ps_json(*rows):  # docker compose ps --format json の 1 行 1 コンテナ
    return "\n".join(json.dumps({"Service": n, "State": st, "Status": stt, "ExitCode": ec, "Health": ""}) for n, st, stt, ec in rows)
def spark_lines(**env):
    _r, _c = run([os.path.join(_lc, "check.sh")], **env)
    return _r.returncode, [l for l in _r.stdout.splitlines() if l.startswith(("ok  Spark:", "NG  Spark:"))]
_lc = tree(read("docker", "compose", ".env.example"))
check("check.sh: Spark の 2 つが running なら ok（1 行 1 コンテナでも、古い compose の配列 1 行でも）",
      spark_lines() == (0, [f"ok  {SPK[0]}", f"ok  {SPK[1]}"])
      and spark_lines(FAKE_PS=json.dumps([{"Service": n, "State": "running", "Status": "Up 1 minute", "ExitCode": 0, "Health": ""} for n in ("spark-splunk", "spark-http")]))
      == (0, [f"ok  {SPK[0]}", f"ok  {SPK[1]}"]))
check("check.sh: Spark が exited（on-failure:5 を使い切った）や restarting なら、状態と Status を出して logs と up.sh <service> を案内して NG、終了コード 1",
      spark_lines(FAKE_PS=ps_json(("spark-splunk", "exited", "Exited (1) 3 minutes ago", 1), ("spark-http", "restarting", "Restarting (1) 10 seconds ago", 1)))
      == (1, [f"NG  {SPK[0]}: exited（Exited (1) 3 minutes ago）。docker compose -f docker/compose/compose.yaml logs spark-splunk で理由を見て、直してから docker/compose/up.sh spark-splunk",
              f"NG  {SPK[1]}: restarting（Restarting (1) 10 seconds ago）。docker compose -f docker/compose/compose.yaml logs spark-http で理由を見て、直してから docker/compose/up.sh spark-http"]))
check("check.sh: Spark が created（依存が healthy にならず up が止まった）なら、依存を見るよう案内して NG。片方だけ止まっていればそちらだけ NG",
      spark_lines(FAKE_PS=ps_json(("spark-splunk", "created", "Created", 0), ("spark-http", "running", "Up 5 minutes", 0)))
      == (1, [f"NG  {SPK[0]}: created（Created）。依存の splunk / opensearch / prometheus が healthy でない（docker compose -f docker/compose/compose.yaml ps -a で見る）", f"ok  {SPK[1]}"]))
check("check.sh: Spark のコンテナが無い（ps が何も返さない。上がっていないか docker に繋がらない）なら NG で up.sh を案内する",
      spark_lines(FAKE_PS="") == (1, [f"NG  {s}: コンテナが無い（上がっていないか docker に繋がらない。docker/compose/up.sh で上げる）" for s in SPK]))
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_CONFIG_FAIL="1")
check("check.sh: compose が .env を読めなければ curl を打たずに止まり、compose のエラー（値のかけらを含むことがある）は出さない",
      _r.returncode == 1 and _c == ["docker compose --env-file .env config --environment"]
      and "docker compose が .env を読めない" in _r.stderr and "SECRETFRAG" not in _r.stdout + _r.stderr)
# check.sh と lab.sh の .env の読み方（ENV_ALL と env_get の 2 行）を、本物の docker compose で試し用の .env に打つ（config --environment は daemon が無くても動く。
# 2026-10-08 に v5.1.3 で確かめた）。export / CRLF / クォート / \" / # メモ / $$ / $VAR / ${VAR} / = のまわりの空白 / 2 回目の定義、シェルが勝つこと、読めない書式で値を出さないこと
_two = [re.search(r"^ENV_ALL=.*\n^env_get\(\) \{.*\}$", read("docker", "compose", s), re.M).group(0) for s in ("check.sh", "lab.sh")]
check("docker/compose/check.sh と lab.sh の .env の読み方（ENV_ALL と env_get の 2 行）は同じ", _two[0] == _two[1])
ENV_IN = ('export A="x"\nB=y # memo\nC=z\r\nD="p # q" # memo\nE=x#y\nF=\'s # t\' # m\n  export G=g  \nH=a"b\\c\nI=\nJ="j"\nK=1\nK=2\n'
          'P1=pa$$word\nP2=ab$HOME\nP3="a\\"b"\nP4 = spaced\nP5=val\t# memo\nP7=a\nP9=${P7}z\nP11="q$$r"\nP12=\'s$$t\'\nOVR=fromfile\n')
ENV_WANT = {"A": "x", "B": "y", "C": "z", "D": "p # q", "E": "x#y", "F": "s # t", "G": "g", "H": 'a"b\\c', "I": "", "J": "j", "K": "2",
            "P1": "pa$word", "P2": "ab" + os.environ.get("HOME", ""), "P3": 'a"b', "P4": "spaced", "P5": "val\t# memo", "P7": "a", "P9": "az",
            "P11": "q$r", "P12": "s$$t", "OVR": "fromshell"}
def env_read(text, **shell):  # 試し用の .env を置いた木で 2 行を打ち、(結果, {名前: 値}) を返す。名前はシェルから消し、shell だけ足す
    lc = tree(text)
    e = {k: v for k, v in os.environ.items() if k not in ENV_WANT and k not in CLEAN}
    e.update(shell)
    r = subprocess.run(["bash", "-c", f'set -euo pipefail\ncd "$1"\nENVF=.env\n{_two[0]}\nfor k in {" ".join(ENV_WANT)}; do printf "%s=[%s]\\n" "$k" "$(env_get "$k")"; done', "_", lc],
                       env=e, capture_output=True, text=True)
    return r, dict(re.fullmatch(r"(\w+)=\[(.*)\]", l, re.S).groups() for l in r.stdout.splitlines() if re.fullmatch(r"(\w+)=\[(.*)\]", l, re.S))
if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode if shutil.which("docker") else 1:
    print("飛ばした: docker compose が無いので、.env の読み方を本物の compose で試すテスト（2 つ）を打っていない")
else:
    _r, _got = env_read(ENV_IN, OVR="fromshell")
    check("check.sh と lab.sh は .env を docker compose と同じに読む（export / CRLF / クォート / \\\" / # メモ / $$ と $VAR と ${VAR} の展開 / ' ' の中は展開しない / = のまわりの空白 / 2 回目の定義）。シェルに同じ名前があればそちらが勝つ",
          _r.returncode == 0 and _got == ENV_WANT)
    _r, _got = env_read('A=1\nSECRETVAL="never closed MARKER123\nB=2\n')
    check("check.sh と lab.sh: compose が読めない .env（閉じないクォート）なら値を出さずに止まり、compose のエラー（値のかけらを含む）は出さない",
          _r.returncode == 1 and "docker compose が .env を読めない" in _r.stderr and "MARKER123" not in _r.stdout + _r.stderr and _got == {})
_lc = tree()
_r, _c = run([os.path.join(_lc, "check.sh")])
check("check.sh: .env が無ければ curl を打たずに止まる", _r.returncode == 1 and _c == [])

shutil.rmtree(TMP)
print(f"通過 {passed} / 失敗 0")
