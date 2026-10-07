"""手元の docker compose で動く構成（local/compose。docs/cycles/006-local-compose/design.md）が、元にした定義からずれていないかを見る。
- 版: Kafka / Kafbat UI / OpenSearch は oss/compose/compose.yaml、Telegraf と lab のイメージは ops/lab-common.sh、Grafana / Splunk は ops/up-common.sh と同値
- 契約: Telegraf は telegraf/telegraf.sh render が通る環境、Spark は spark/snmp_sinks.py の parse_args が通る引数、check.sh が見る名前は実物の定義にある
- lab/lab.sh: REGISTRY が無ければ ECR に触らずに pull、TELEGRAF_LOCAL=1 なら trap の REDIRECT だけ（デバッグ用の EC2 と同じ）。偽の docker / aws / iptables / sudo で動かす
- local/compose/*.sh: up.sh が lab の値を環境で渡す、lab.sh が 3 つだけを sudo に渡す、check.sh が全部見てから終わりパスワードを引数に載せない
実行は uv run --group dev python tests/test_local_compose.py（pyyaml を使う。AWS も docker も要らない）。"""
import glob, importlib.util, io, json, os, re, shutil, subprocess, sys, tempfile

import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LC = os.path.join(ROOT, "local", "compose")

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


compose = yaml.safe_load(read("local", "compose", "compose.yaml"))
svc = compose["services"]
oss = yaml.safe_load(read("oss", "compose", "compose.yaml"))["services"]
lab_common, up_common = read("ops", "lab-common.sh"), read("ops", "up-common.sh")
example = env_file(read("local", "compose", ".env.example"))
lab_sh = read("lab", "lab.sh")

# up.sh が lab の定義から作って環境で渡す 3 つ（AWS 版の ops/up.sh と同じ作り方）
def topology(flag):
    return subprocess.run([sys.executable, os.path.join(ROOT, "lab", "lab_topology.py"), os.path.join(ROOT, "lab"), flag],
                          capture_output=True, text=True, check=True).stdout.strip()
UP_ENV = {"SNMP_AGENTS": topology("--snmp-agents"), "GNMI_TARGETS": topology("--gnmi-targets"), "DEVICE_MAP": topology("--device-map")}

def subst(s, env):  # compose の ${X} / ${X:-既定} を env で埋める
    return re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", lambda m: env.get(m.group(1)) or (m.group(2) or ""), s)


# ---- 1. 構成（design.md の「合意した決定」と「変更対象ファイル」）
check("services は 11 個（telegraf kafka-1 kafka-2 kafka-3 kafka-ui spark-splunk spark-http opensearch prometheus splunk grafana）",
      list(svc) == "telegraf kafka-1 kafka-2 kafka-3 kafka-ui spark-splunk spark-http opensearch prometheus splunk grafana".split())
check("named volume は design.md の 10 個（kafka-1/2/3、opensearch、prometheus、splunk-etc、splunk-var、grafana、spark-*-ckpt）",
      set(compose["volumes"]) == {"kafka-1", "kafka-2", "kafka-3", "opensearch", "prometheus", "splunk-etc", "splunk-var", "grafana",
                                  "spark-splunk-ckpt", "spark-http-ckpt"})
check("ネットワークは nwc-local", compose["networks"]["default"]["name"] == "nwc-local")
check("プロジェクト名は nwc-local（ディレクトリ名の compose にしない。volume が nwc-local_* になり、oss/compose の nwc-oss とも別）",
      compose["name"] == "nwc-local" and yaml.safe_load(read("oss", "compose", "compose.yaml"))["name"] != "nwc-local")
check("compose の ports は全部 127.0.0.1 に縛る（Kafka・Splunk・Grafana・OpenSearch を WSL の外へ出さない。host のネットワークにいる Telegraf の 4 つはこの外）",
      all(p.startswith("127.0.0.1:") for s in svc.values() for p in s.get("ports", [])))
check("telegraf は network_mode: host で、build の context は ../../telegraf",
      svc["telegraf"]["network_mode"] == "host" and svc["telegraf"]["build"]["context"] == "../../telegraf" and "ports" not in svc["telegraf"])

# ---- 2. 版の正
for n in ("kafka-1", "kafka-2", "kafka-3"):
    check(f"{n} の image は oss/compose の kafka-1 と同じ（{oss['kafka-1']['image']}）", svc[n]["image"] == oss["kafka-1"]["image"])
check("kafka-ui は oss/compose の kafka-ui と image・環境・ポートが同じ",
      all(svc["kafka-ui"][k] == oss["kafka-ui"][k] for k in ("image", "environment", "ports")))
check("opensearch の image は oss/compose の opensearch-1 と同じ", svc["opensearch"]["image"] == oss["opensearch-1"]["image"])
check("grafana の GRAFANA_VERSION と splunk の SPLUNK_VERSION は ops/up-common.sh の値",
      svc["grafana"]["build"]["args"]["GRAFANA_VERSION"] == sh_const(up_common, "GRAFANA_VERSION")
      and svc["splunk"]["build"]["args"]["SPLUNK_VERSION"] == sh_const(up_common, "SPLUNK_VERSION")
      and svc["grafana"]["build"]["context"] == "../../grafana" and svc["splunk"]["build"]["context"] == "../../splunk")
check("telegraf の TELEGRAF_VERSION は ops/lab-common.sh の値",
      svc["telegraf"]["build"]["args"]["TELEGRAF_VERSION"] == sh_const(lab_common, "TELEGRAF_VERSION"))
check(".env.example の SRLINUX_IMAGE / MULTITOOL_IMAGE は ops/lab-common.sh の upstream:tag",
      example["SRLINUX_IMAGE"] == f"{sh_const(lab_common, 'SRLINUX_UPSTREAM')}:{sh_const(lab_common, 'SRLINUX_TAG')}"
      and example["MULTITOOL_IMAGE"] == f"{sh_const(lab_common, 'MULTITOOL_UPSTREAM')}:{sh_const(lab_common, 'MULTITOOL_TAG')}")
check("splunk は linux/amd64（上流が amd64 だけ）", svc["splunk"]["platform"] == "linux/amd64")

# ---- 3. Kafka: oss/compose の x-kafka-env の写しに EXTERNAL リスナーを足しただけ
_oss_env = oss["kafka-1"]["environment"]
_ext = {"KAFKA_LISTENERS", "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP", "KAFKA_ADVERTISED_LISTENERS"}
for i, n in enumerate(("kafka-1", "kafka-2", "kafka-3")):
    e, o, port = svc[n]["environment"], oss[n]["environment"], 9094 + i
    check(f"{n}: EXTERNAL 以外の KAFKA_* と CLUSTER_ID は oss/compose と同じ値",
          all(e.get(k) == v for k, v in o.items() if (k.startswith("KAFKA_") or k == "CLUSTER_ID") and k not in _ext))
    check(f"{n}: リスナーは oss/compose のものに EXTERNAL://:{port}（advertised は localhost:{port}）を足し、127.0.0.1:{port} に出す",
          e["KAFKA_LISTENERS"] == o["KAFKA_LISTENERS"] + f",EXTERNAL://:{port}"
          and e["KAFKA_ADVERTISED_LISTENERS"] == o["KAFKA_ADVERTISED_LISTENERS"] + f",EXTERNAL://localhost:{port}"
          and e["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"] == o["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"] + ",EXTERNAL:PLAINTEXT"
          and svc[n]["ports"] == [f"127.0.0.1:{port}:{port}"])
check("トピックは自動で作る（Telegraf が最初に書く）",
      all(svc[n]["environment"]["KAFKA_AUTO_CREATE_TOPICS_ENABLE"] == "true" for n in ("kafka-1", "kafka-2", "kafka-3")))

# ---- 4. Telegraf: 環境は telegraf/telegraf.sh の契約どおり。up.sh の値を入れると render が通り、出力は host の EXTERNAL の 3 つ
_tg = svc["telegraf"]["environment"]
check("telegraf の KAFKA_BROKERS は 3 台の EXTERNAL（localhost:9094-9096）、KAFKA_AUTH=none、SYSLOG_STANDARD は ops/lab-common.sh の LAB_SYSLOG_STANDARD",
      _tg["KAFKA_BROKERS"] == "localhost:9094,localhost:9095,localhost:9096" and _tg["KAFKA_AUTH"] == "none" and _tg["SINK"] == "kafka"
      and _tg["SYSLOG_STANDARD"] == sh_const(lab_common, "LAB_SYSLOG_STANDARD"))
check("telegraf の GNMI_USERNAME / GNMI_PASSWORD / SNMP_COMMUNITY は ops/lab-common.sh の lab の公開既定値",
      (_tg["GNMI_USERNAME"], _tg["GNMI_PASSWORD"], _tg["SNMP_COMMUNITY"])
      == (sh_const(lab_common, "LAB_GNMI_USERNAME"), sh_const(lab_common, "LAB_GNMI_PASSWORD"), sh_const(lab_common, "LAB_SNMP_COMMUNITY")))

def tg_render(env):
    with tempfile.TemporaryDirectory() as d:
        e = {"PATH": os.environ["PATH"], "TELEGRAF_TEMPLATE": os.path.join(ROOT, "telegraf", "telegraf.conf.in"),
             "TELEGRAF_CONF": os.path.join(d, "telegraf.conf")}
        e.update({k: subst(str(v), env) for k, v in _tg.items()})
        r = subprocess.run(["bash", os.path.join(ROOT, "telegraf", "telegraf.sh"), "render"], capture_output=True, text=True, env=e)
        conf = open(e["TELEGRAF_CONF"], encoding="utf-8").read() if r.returncode == 0 else ""
        return r, conf

_r, _conf = tg_render(UP_ENV)
check("up.sh の値を入れた telegraf の環境で telegraf.sh render が通り、出力 5 つが localhost:9094-9096 へ書き、IAM の設定が無い",
      _r.returncode == 0 and _conf.count('brokers = ["localhost:9094","localhost:9095","localhost:9096"]') == 5
      and "sasl_mechanism" not in _conf and "203.0.113." in _conf)
check("docker compose を直に打つ（SNMP_AGENTS が空）と telegraf.sh は形の検査で止まる（compose.yaml の頭の注意のとおり。up.sh から上げる）",
      tg_render({})[0].returncode != 0)
_topics = set(re.findall(r'^\s*topic = "(\w+)"', read("telegraf", "telegraf.conf.in"), re.M))

# ---- 5. Spark: 1 イメージから 2 サービス。引数は snmp_sinks.py の parse_args が受ける
spec = importlib.util.spec_from_file_location("snmp_sinks", os.path.join(ROOT, "spark", "snmp_sinks.py"))
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
    check(f"{n}: spark/Dockerfile の同じイメージ、KAFKA_AUTH=none（PLAINTEXT）、--checkpoint は volume の下、iceberg が無い",
          svc[n]["build"] == "../../spark" and svc[n]["image"] == "nwc-local-spark" and svc[n]["environment"]["KAFKA_AUTH"] == "none"
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
      "dc1-leaf-01" in sinks.parse_device_map(_a.device_map).values())
check("Spark の --metric-topics と --log-topics は Telegraf が書くトピックだけ",
      set(_a.metric_topics.split(",")) | set(_a.log_topics.split(",")) == _topics)

# ---- 6. OpenSearch / Prometheus / Grafana / Splunk
_os = svc["opensearch"]["environment"]
check("opensearch は single-node、HTTP の TLS を切る（Spark と Grafana は http:// に Basic 認証）。mmap と heap は oss/compose と同じ",
      _os["discovery.type"] == "single-node" and _os["plugins.security.ssl.http.enabled"] == "false"
      and all(_os[k] == oss["opensearch-1"]["environment"][k] for k in ("node.store.allow_mmap", "DISABLE_PERFORMANCE_ANALYZER_AGENT_CLI", "OPENSEARCH_JAVA_OPTS")))
check("prometheus は remote write を受け、設定は prometheus.yml（scrape なし）",
      "--web.enable-remote-write-receiver" in svc["prometheus"]["command"]
      and "./prometheus.yml:/etc/prometheus/prometheus.yml:ro" in svc["prometheus"]["volumes"]
      and "scrape_configs" not in read("local", "compose", "prometheus.yml"))
_gf = svc["grafana"]["environment"]
check("grafana は PROMETHEUS_AUTH=none / OPENSEARCH_AUTH=basic で、URL は compose の中の prometheus と opensearch、ALERTS_TOPIC_ARN は渡さない",
      _gf["PROMETHEUS_AUTH"] == "none" and _gf["OPENSEARCH_AUTH"] == "basic" and _gf["PROMETHEUS_URL"] == "http://prometheus:9090"
      and _gf["OPENSEARCH_URL"] == "http://opensearch:9200" and "ALERTS_TOPIC_ARN" not in _gf and "ALERTS_TOPIC_ARN" not in svc["splunk"]["environment"])
check("Spark が書く OpenSearch のインデックスと Grafana が読むインデックスが同じ（snmp-logs）",
      _gf["OPENSEARCH_INDEX"] == spark_args("spark-http").opensearch_index == "snmp-logs")
check("splunk の HEC の token は spark-splunk と同じ .env の値", svc["splunk"]["environment"]["SPLUNK_HEC_TOKEN"] == svc["spark-splunk"]["environment"]["SPLUNK_HEC_TOKEN"])

# ---- 7. 設定（.env.example）と compose の ${VAR}
_vars = set(re.findall(r"\$\{(\w+)", read("local", "compose", "compose.yaml")))
check(".env.example のキーは design.md の 7 つ",
      set(example) == {"SPLUNK_PASSWORD", "SPLUNK_HEC_TOKEN", "OPENSEARCH_PASSWORD", "GF_SECURITY_ADMIN_PASSWORD", "SRLINUX_IMAGE", "MULTITOOL_IMAGE", "AWS_REGION"})
check("compose.yaml の ${VAR} は全部 .env.example のキーか up.sh が渡す 3 つ（SNMP_AGENTS / GNMI_TARGETS / DEVICE_MAP）",
      _vars and _vars <= set(example) | set(UP_ENV))
check("SPLUNK_HEC_TOKEN は uuid の形（Splunk のイメージが作る HEC の token。ops/up.sh と同じ形）",
      re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", example["SPLUNK_HEC_TOKEN"]) is not None)
check("local/compose/.env は git に入らず、.env.example は入る",
      subprocess.run(["git", "check-ignore", "-q", "local/compose/.env"], cwd=ROOT).returncode == 0
      and subprocess.run(["git", "check-ignore", "-q", "local/compose/.env.example"], cwd=ROOT).returncode == 1)
check("local/ の下に .py が無い（ops/check.sh の ast.parse の対象だが、置かない）",
      not [f for _, _, fs in os.walk(os.path.join(ROOT, "local")) for f in fs if f.endswith(".py")])
SCRIPTS = ["up.sh", "down.sh", "check.sh", "lab.sh"]
check("local/compose の 4 つと lab/lab.sh は実行できる（local/compose/lab.sh は ../../lab/lab.sh を直に呼び、lab.sh も自分を \"$SELF\" で呼ぶ）",
      all(os.access(os.path.join(LC, s), os.X_OK) for s in SCRIPTS) and os.access(os.path.join(ROOT, "lab", "lab.sh"), os.X_OK))
check("local/compose の 4 つと lab/lab.sh は 1 つずつ bash -n が通る（bash -n a b は a しか見ない）",
      all(subprocess.run(["bash", "-n", p]).returncode == 0 for p in [os.path.join(LC, s) for s in SCRIPTS] + [os.path.join(ROOT, "lab", "lab.sh")]))
check("ops/check.sh の bash -n（1 つずつ打つ for 文）に local/compose/*.sh、.py の find に local がある",
      re.search(r'^for f in .* local/compose/\*\.sh; do bash -n "\$f"; done$', read("ops", "check.sh"), re.M) is not None
      and re.search(r"^find .*\blocal\b.* -name '\*\.py'", read("ops", "check.sh"), re.M) is not None)
# ops/check.sh の 3 の 1 行を、並んだファイルを写した木（中身は true）で打つ。1 つずつ構文エラー（if だけ）に替えて、どの位置でも落ちることを見る
_s3 = re.search(r'^log "3\. .*\n(.*)\n', read("ops", "check.sh"), re.M).group(1)
_s3_tmp = tempfile.mkdtemp()
_s3_files = sorted({os.path.relpath(f, ROOT) for p in _s3.replace(";", " ").split() if p.endswith(".sh")
                    for f in glob.glob(os.path.join(ROOT, p))})
for _f in _s3_files:
    os.makedirs(os.path.join(_s3_tmp, os.path.dirname(_f)), exist_ok=True)
    with open(os.path.join(_s3_tmp, _f), "w") as f:
        f.write("true\n")
def s3_run(bad=None):  # bad のファイルだけ構文エラーにして 3 の 1 行を打ち、終了コードを返す
    if bad:
        with open(os.path.join(_s3_tmp, bad), "w") as f:
            f.write("if\n")
    r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + _s3], cwd=_s3_tmp, capture_output=True, text=True)
    if bad:
        with open(os.path.join(_s3_tmp, bad), "w") as f:
            f.write("true\n")
    return r.returncode
check(f"ops/check.sh の 3: 並んだ {len(_s3_files)} ファイルのどれか 1 つ（2 番目以降を含む）が構文エラーなら落ち、全部通れば 0（bash -n a b c は a しか見ず、b と c は位置引数になる）",
      len(_s3_files) >= 19 and "local/compose/check.sh" in _s3_files and s3_run() == 0
      and [f for f in _s3_files if s3_run(f) == 0] == [])
shutil.rmtree(_s3_tmp)

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
fake("docker", '[ "${1:-}" = login ] && cat >/dev/null\nif [ "${1:-}" = compose ]; then echo "ENV SNMP_AGENTS=${SNMP_AGENTS-unset} GNMI_TARGETS=${GNMI_TARGETS-unset} DEVICE_MAP=${DEVICE_MAP-unset}" >> "$FAKE_LOG"; fi\n')
# -S（規則の一覧）には FAKE_IPT_RULES を返す
fake("iptables", 'case " $* " in *" -S"*) printf "%s" "${FAKE_IPT_RULES:-}" ;; esac\n')
# 本物の sudo は環境を消す（env_reset）。PATH と FAKE_LOG だけ残して、渡された引数を打つ
fake("sudo", 'exec env -i PATH="$PATH" FAKE_LOG="$FAKE_LOG" "$@"\n')
# lab.sh up が打つ（containerlab は deploy を書くだけ、modprobe は何もしない）
fake("containerlab")
fake("modprobe")
fake("free", 'printf "               total        used        free\\nMem:  %s  1000  1000\\nSwap:  0  0  0\\n" "${FAKE_MEM:-32000}"\n')
# check.sh が打つ curl。引数と、-K - で渡された標準入力を書き、URL ごとに決めた応答を返す。FAKE_DOWN=1 なら繋がらない（出力なしで 7）。
# Splunk の応答は FAKE_SPLUNK があればそれ（認証の失敗は 401 でも curl -sS は本文を出して 0 で終わる）
fake("curl", r'''prev=; url=
for a in "$@"; do
  case "$a" in http*) url=$a ;; esac
  if [ "$prev" = -K ] && [ "$a" = - ]; then printf 'STDIN %s\n' "$(cat)" >> "$FAKE_LOG"; fi
  prev=$a
done
[ "${FAKE_DOWN:-0}" = 1 ] && exit 7
case "$url" in
  *18080/api/clusters/nwc/topics*) echo '{"topics":[{"name":"metrics"},{"name":"gnmi"},{"name":"traps"},{"name":"logs"},{"name":"mdt"}]}' ;;
  *9090/api/v1/query*) echo '{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1760000000,"12"]}]}}' ;;
  *9200/snmp-logs/_count*) echo "{\"count\":${FAKE_OS_COUNT:-5}}" ;;
  *8089/services/search/jobs/export*)
    if [ -n "${FAKE_SPLUNK:-}" ]; then printf '%s\n' "$FAKE_SPLUNK"
    else printf '%s\n' '{"preview":true,"result":{"count":"0"}}' '{"preview":false,"result":{"count":"7"}}'; fi ;;
  *3000/api/datasources/uid/amp/health*) echo '{"status":"OK","message":"Successfully queried the Prometheus API."}' ;;
  *3000/api/datasources*) echo '[{"uid":"amp","type":"prometheus"},{"uid":"aoss-logs","type":"grafana-opensearch-datasource"}]' ;;
esac
''')
LOG = os.path.join(TMP, "calls.log")
CLEAN = ("REGISTRY", "AWS_REGION", "PARAM_PREFIX", "TELEGRAF_IMAGE", "TELEGRAF_LOCAL", "SRLINUX_IMAGE", "MULTITOOL_IMAGE",
         "SNMP_AGENTS", "GNMI_TARGETS", "DEVICE_MAP", "FAKE_IPT_RULES", "FAKE_MEM", "FAKE_DOWN", "FAKE_OS_COUNT", "FAKE_SPLUNK")

def run(cmd, **env):
    """偽のコマンドを先に置いた PATH で cmd を打ち、(結果, 呼ばれたコマンドの行) を返す。lab に効く環境変数は消してから env を足す"""
    e = {k: v for k, v in os.environ.items() if k not in CLEAN}
    e.update(PATH=BIN + os.pathsep + os.environ["PATH"], FAKE_LOG=LOG, **env)
    open(LOG, "w").close()
    r = subprocess.run(cmd, env=e, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r, open(LOG, encoding="utf-8").read().splitlines()

LAB = os.path.join(ROOT, "lab", "lab.sh")
SRL, MT = example["SRLINUX_IMAGE"], example["MULTITOOL_IMAGE"]
PULLS = [f"docker pull -q {SRL}", f"docker pull -q {MT}"]
REDIRECT = ("iptables -t nat -I PREROUTING 1 -s 203.0.113.0/24 -d 203.0.113.1 -p udp --dport 162 "
            "-m comment --comment nwc-lab-telegraf -j REDIRECT --to-ports 1162")

# lab/lab.sh pull（design.md の lab の切り替え (1)）
_r, _c = run([LAB, "pull"], SRLINUX_IMAGE=SRL, MULTITOOL_IMAGE=MT)
check("lab/lab.sh pull: REGISTRY が無ければ aws も docker login も打たず、2 つのイメージを docker pull するだけ", _r.returncode == 0 and _c == PULLS)
_r, _c = run([LAB, "pull"], SRLINUX_IMAGE=SRL, MULTITOOL_IMAGE=MT, REGISTRY="111122223333.dkr.ecr.ap-northeast-1.amazonaws.com", AWS_REGION="ap-northeast-1")
check("lab/lab.sh pull: REGISTRY があれば（lab の EC2）今までどおり ECR に login してから pull する",
      _r.returncode == 0 and sorted(_c[:2]) + _c[2:] == ["aws ecr get-login-password --region ap-northeast-1",
                                    "docker login --username AWS --password-stdin 111122223333.dkr.ecr.ap-northeast-1.amazonaws.com"] + PULLS)
_r, _c = run([LAB, "pull"], SRLINUX_IMAGE=SRL, MULTITOOL_IMAGE=MT, REGISTRY="111122223333.dkr.ecr.ap-northeast-1.amazonaws.com")
check("lab/lab.sh pull: REGISTRY があって AWS_REGION が無ければ、今までどおり何も取らずに止まる", _r.returncode != 0 and _c == [])

# lab/lab.sh forward（(2)。手元とデバッグ用の EC2 は REDIRECT だけ、それ以外は stream の分岐で SSM を読む）
_r, _c = run([LAB, "forward"], TELEGRAF_LOCAL="1")
check("lab/lab.sh forward: TELEGRAF_LOCAL=1 なら aws を打たず、trap の 162 → 1162 の REDIRECT を 1 本だけ入れ、案内は「compose の Telegraf へ」",
      _r.returncode == 0 and [c for c in _c if " -I " in c] == [REDIRECT] and not [c for c in _c if c.startswith("aws")]
      and "compose の Telegraf へ: trap 162/udp を 1162/udp へ向けた" in _r.stdout)
_r, _c = run([LAB, "forward"], TELEGRAF_IMAGE="111122223333.dkr.ecr.ap-northeast-1.amazonaws.com/nwc-telegraf:1")
check("lab/lab.sh forward: TELEGRAF_IMAGE（デバッグ用の EC2）は今までどおり同じ REDIRECT で、案内は「この EC2 の Telegraf へ」",
      _r.returncode == 0 and [c for c in _c if " -I " in c] == [REDIRECT] and "この EC2 の Telegraf へ" in _r.stdout)
_r, _c = run([LAB, "forward"], TELEGRAF_LOCAL="0")
check("lab/lab.sh forward: TELEGRAF_LOCAL が 1 でなければ stream の分岐（AWS_REGION が要る）へ行き、REDIRECT を入れない",
      _r.returncode != 0 and not [c for c in _c if " -I " in c])

# hint（(2)。障害を入れたあとにどこを見るか）は関数を切り出して呼ぶ
_fn = re.search(r"^local_telegraf\(\).*?^}\n", lab_sh, re.S | re.M).group(0)
def hint(**env):
    return run(["bash", "-c", f"FW_TAG=nwc-lab-telegraf\n{_fn}hint 'EC2 の文' 'stream の文'"], **env)[0].stdout
check("lab/lab.sh hint: TELEGRAF_LOCAL=1 なら compose の Grafana と Splunk を案内し、TELEGRAF_IMAGE と stream と転送なしの案内は変わらない",
      "compose の Telegraf: 数分で Grafana（:3000）と Splunk（:8000）" in hint(TELEGRAF_LOCAL="1")
      and hint(TELEGRAF_IMAGE="x") == "  この EC2 の Telegraf: EC2 の文\n"
      and hint(FAKE_IPT_RULES="-A PREROUTING -m comment --comment nwc-lab-telegraf -j DNAT\n") == "  stream: stream の文\n"
      and "転送が張られていない" in hint())
_fail = re.search(r"\n  failover\)(.*?)\n    ;;", lab_sh, re.S).group(1)
check("lab/lab.sh failover（fail-main のあとの案内。TELEGRAF_IMAGE の分岐があるのはここ）: TELEGRAF_IMAGE の次に local_telegraf の分岐があり、compose の Grafana と Splunk を案内する",
      re.search(r'if \[ -n "\$\{TELEGRAF_IMAGE:-\}" \]; then.*?elif local_telegraf; then\s+echo "== Telegraf（compose の Telegraf）=="', _fail, re.S) is not None)
check("lab/lab.sh: /etc/*-lab.env が無くても止まらない（手元。ls が失敗しても || true）",
      'ENV_FILE=$(ls /etc/*-lab.env 2>/dev/null | head -1 || true)' in lab_sh and '[ -n "$ENV_FILE" ] && set -a' in lab_sh)

# 置き場所を写した木（tmp/local/compose と、tmp/lab → リポジトリの lab）で local/compose の 4 つを動かす
def tree(env=None, files=SCRIPTS, lab_copy=False):
    t = tempfile.mkdtemp(dir=TMP)
    lc = os.path.join(t, "local", "compose")
    os.makedirs(lc)
    for s in files + [".env.example", "compose.yaml"]:
        shutil.copy2(os.path.join(LC, s), lc)
    if lab_copy:  # lab.sh render が splab.clab.yml を書くので、リポジトリの lab ではなく写しに書かせる
        shutil.copytree(os.path.join(ROOT, "lab"), os.path.join(t, "lab"), ignore=shutil.ignore_patterns("clab-*", "splab.clab.yml", "__pycache__"))
    else:
        os.symlink(os.path.join(ROOT, "lab"), os.path.join(t, "lab"))
    if env is not None:  # 試し用の .env（中身はこのテストが書く）
        with open(os.path.join(lc, ".env"), "w") as f:
            f.write(env)
    return lc

# local/compose/lab.sh
_lc = tree()
_r, _c = run([os.path.join(_lc, "lab.sh"), "pull"], REGISTRY="leak.example.com", AWS_REGION="ap-northeast-1", PARAM_PREFIX="/nwc")
check("local/compose/lab.sh: .env が無ければ .env.example のイメージと TELEGRAF_LOCAL=1 の 3 つだけを sudo env で lab/lab.sh に渡す（sudo -E にしない）",
      _r.returncode == 0 and _c[0].split()[:5] == ["sudo", "env", f"SRLINUX_IMAGE={SRL}", f"MULTITOOL_IMAGE={MT}", "TELEGRAF_LOCAL=1"]
      and os.path.realpath(_c[0].split()[5]) == os.path.realpath(LAB) and _c[0].split()[6:] == ["pull"])
check("local/compose/lab.sh pull: シェルに REGISTRY や AWS_REGION があっても ECR に行かず、ghcr.io の 2 つを取る", _c[1:] == PULLS)
_r, _c = run([os.path.join(_lc, "lab.sh"), "forward"])
check("local/compose/lab.sh forward: REDIRECT を 1 本入れ、案内は「compose の Telegraf へ」（aws は打たない）",
      _r.returncode == 0 and [c for c in _c if " -I " in c] == [REDIRECT] and "compose の Telegraf へ" in _r.stdout
      and not [c for c in _c if c.startswith("aws")])
_lc = tree('SRLINUX_IMAGE="example.com/srl:1"\nMULTITOOL_IMAGE=\'example.com/mt:2\'\n')
_r, _c = run([os.path.join(_lc, "lab.sh"), "status"])
check("local/compose/lab.sh: .env があればそちらのイメージを使う（値の \" と ' は外す）",
      _c and _c[0].split()[2:4] == ["SRLINUX_IMAGE=example.com/srl:1", "MULTITOOL_IMAGE=example.com/mt:2"])
_lc = tree("SRLINUX_IMAGE=example.com/srl:1\n")
_r, _c = run([os.path.join(_lc, "lab.sh"), "up"])
check("local/compose/lab.sh: .env に MULTITOOL_IMAGE が無ければ sudo を打たずに止まる", _r.returncode != 0 and _c == [] and "MULTITOOL_IMAGE が無い" in _r.stderr)
# lab/lab.sh up は splab.clab.yml があると render しないので、gen_lab.py で台数を変えたあとも古い yml で deploy する。ラッパーが毎回 render する
_lc = tree(lab_copy=True)
_yml = os.path.join(os.path.dirname(os.path.dirname(_lc)), "lab", "splab.clab.yml")
with open(_yml, "w") as f:
    f.write("古い yml（gen_lab.py の前）\n")
_r, _c = run([os.path.join(_lc, "lab.sh"), "up"])
check("local/compose/lab.sh up: lab/lab.sh render を打ってから up し、splab.clab.yml を今の .in と .env のイメージで作り直してから deploy する",
      _r.returncode == 0
      and [c.split()[6] if c.startswith("sudo ") else c for c in _c if c.startswith(("sudo ", "containerlab "))]
      == ["render", "up", "containerlab deploy -t splab.clab.yml --reconfigure"]
      and open(_yml, encoding="utf-8").read() == read("lab", "splab.clab.yml.in").replace("__SRLINUX_IMAGE__", SRL).replace("__MULTITOOL_IMAGE__", MT))
_r, _c = run([os.path.join(_lc, "lab.sh"), "down"])
check("local/compose/lab.sh: up 以外（down など）は render しない", [c.split()[6] for c in _c if c.startswith("sudo ")] == ["down"])

# local/compose/up.sh / down.sh
_lc = tree()
_r, _c = run([os.path.join(_lc, "up.sh")])
check("up.sh: .env が無ければ docker を打たずに止まり、cp .env.example .env と、パスワードを変えるなら最初の up.sh の前（あとからなら down.sh -v）を案内する",
      _r.returncode == 1 and _c == [] and "cp .env.example .env" in _r.stderr and "最初の up.sh の前" in _r.stderr and "down.sh -v" in _r.stderr)
_lc = tree(read("local", "compose", ".env.example"))
_r, _c = run([os.path.join(_lc, "up.sh")])
check("up.sh: lab/lab_topology.py の 3 つ（SNMP_AGENTS / GNMI_TARGETS / DEVICE_MAP）を環境で渡して docker compose up -d --build を打つ",
      _r.returncode == 0 and _c == ["docker compose up -d --build",
                                    f"ENV SNMP_AGENTS={UP_ENV['SNMP_AGENTS']} GNMI_TARGETS={UP_ENV['GNMI_TARGETS']} DEVICE_MAP={UP_ENV['DEVICE_MAP']}"]
      and all(UP_ENV.values()))
_r, _c = run([os.path.join(_lc, "up.sh"), "telegraf"])
check("up.sh: 引数は docker compose up に渡す（up.sh telegraf で Telegraf だけ作り直す）", _r.returncode == 0 and _c[0] == "docker compose up -d --build telegraf")
_r, _c = run([os.path.join(_lc, "down.sh"), "-v"])
check("down.sh -v: docker compose down -v", _r.returncode == 0 and _c[0] == "docker compose down -v")

# local/compose/check.sh（design.md の検証方法「WSL」の 4）
PW = example["OPENSEARCH_PASSWORD"]
_lc = tree(read("local", "compose", ".env.example"))
_r, _c = run([os.path.join(_lc, "check.sh")])
_ok = [l for l in _r.stdout.splitlines() if l.startswith("ok  ")]
check("check.sh: 応答が全部そろえば 6 項目とも ok で「すべて ok」、終了コード 0（メモリが 20 GB 以上なら注意を出さない）",
      _r.returncode == 0 and len(_ok) == 6 and _r.stdout.splitlines()[-1] == "すべて ok" and "注意" not in _r.stdout)
_argv = [c for c in _c if c.startswith("curl ")]
_stdin = [c for c in _c if c.startswith("STDIN ")]
check("check.sh: パスワードは curl の引数に載せず（ps に出る）、-K - の標準入力で user = \"admin:…\" として渡す（OpenSearch・Splunk・Grafana 2 つの 4 回）",
      len(_argv) == 6 and not [c for c in _argv if PW in c] and _stdin == [f'STDIN user = "admin:{PW}"'] * 4)
check("check.sh: Splunk の検索は sourcetype=netops:*（Spark の SPLUNK_SOURCETYPE_PREFIX）、Prometheus は Grafana のダッシュボードとアラートが使う snmp_interface_ifOperStatus",
      sinks.SPLUNK_SOURCETYPE_PREFIX == "netops" and any("sourcetype=netops:*" in c for c in _argv)
      and "snmp_interface_ifOperStatus" in read("grafana", "provisioning", "dashboards", "metrics.json")
      and any("query=count(snmp_interface_ifOperStatus)" in c for c in _argv))
check("check.sh: Grafana で見る uid（amp / aoss-logs）は grafana/provisioning/datasources-oss の定義にある",
      {m for f in ("prometheus.yaml", "opensearch.yaml")
       for m in re.findall(r"uid: (\S+)", read("grafana", "provisioning", "datasources-oss", f))} == {"amp", "aoss-logs"})
check("check.sh: Kafka で見るトピック（metrics / gnmi / traps / logs）は Telegraf が書くトピック",
      {"metrics", "gnmi", "traps", "logs"} <= _topics)
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_OS_COUNT="0", FAKE_MEM="16000")
check("check.sh: 1 つが 0 件なら、そこだけ NG にして残りも見てから終了コード 1。メモリが 20 GB 未満なら注意を出す",
      _r.returncode == 1 and "NG  OpenSearch: snmp-logs の件数 > 0: 0 件" in _r.stdout and len([l for l in _r.stdout.splitlines() if l.startswith("ok  ")]) == 5
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
_r, _c = run([os.path.join(_lc, "check.sh")], FAKE_DOWN="1")
check("check.sh: どこにも繋がらなくても set -e で途中で落ちず、6 項目とも「読めない応答: 空」の NG で終了コード 1",
      _r.returncode == 1 and _r.stdout.count("読めない応答: 空") == 6)
_lc = tree('OPENSEARCH_PASSWORD=a"b\\c\nSPLUNK_PASSWORD=x\nGF_SECURITY_ADMIN_PASSWORD=y\n')
_r, _c = run([os.path.join(_lc, "check.sh")])
check("check.sh: パスワードの \" と \\ は curl の設定の書き方で逃がす", 'STDIN user = "admin:a\\"b\\\\c"' in _c)
_lc = tree()
_r, _c = run([os.path.join(_lc, "check.sh")])
check("check.sh: .env が無ければ curl を打たずに止まる", _r.returncode == 1 and _c == [])

shutil.rmtree(TMP)
print(f"通過 {passed} / 失敗 0")
