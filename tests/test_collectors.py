"""機器の syslog と NetFlow を受けるコレクター（cycle 012。docs/cycles/012-msk-scram-syslog-ng-goflow2/design.md）が、それまでの Telegraf の形からずれていないかを見る。
- syslog-ng: app/syslog-ng/syslog-ng.conf.in の format-json のキーと型が、Telegraf 1.40.1 の inputs.syslog（+ processors.rename）が logs に書いていた device_log の実物の 1 行と同じ（検証 2）
- syslog-ng.sh render: 環境変数で設定を埋める（scram / none、RFC5424 / RFC3164、ポートとアドレス）。おかしな値は設定を書かずに止まり、SCRAM の値を設定にも出力にも出さない
- Dockerfile: syslog-ng.sh の置き場所・nobody・ヘルスチェックの制御ソケットが噛み合っている
- tools/netflow_send.py: NetFlow v5 のヘッダーとフロー 1 本（72 バイト）
実行は uv run python tests/test_collectors.py（sh と sed と grep を使う。AWS も docker も要らない）。"""
import importlib.util, json, os, re, secrets, shutil, socket, struct, subprocess, sys, tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SNG_SH = os.path.join(ROOT, "app", "syslog-ng", "syslog-ng.sh")
CONF_IN = os.path.join(ROOT, "app", "syslog-ng", "syslog-ng.conf.in")

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


TMP = tempfile.mkdtemp(prefix="nwc-collectors-")
BROKERS = "b-1.example:9096,b-2.example:9096,b-3.example:9096"

def render(env, conf=None):
    """sh syslog-ng.sh render を、渡した環境変数だけ（と PATH）で回す。(returncode, stdout+stderr, 書いた設定 or None) を返す"""
    conf = conf or os.path.join(tempfile.mkdtemp(dir=TMP), "syslog-ng.conf")
    e = {"PATH": os.environ["PATH"], "SYSLOG_NG_TEMPLATE": CONF_IN, "SYSLOG_NG_CONF": conf, **env}
    r = subprocess.run(["sh", SNG_SH, "render"], env=e, capture_output=True, text=True)
    body = open(conf, encoding="utf-8").read() if os.path.exists(conf) else None
    return r.returncode, r.stdout + r.stderr, body

def code(conf):  # 設定のうちコメントでない行（ヘッダーのコメントに SASL や __X__ の字が出てくるので外す）
    return "\n".join(l for l in conf.splitlines() if not l.lstrip().startswith("#"))

def json_pairs(conf):
    """描いた設定の $(format-json ...) の key=value を順に返す（オプションの --scope none と --omit-empty-values は除く）"""
    m = re.search(r"\$\(format-json\s+(.*?)\)\"\)", conf, re.S)
    assert m, "format-json が無い"
    toks, cur, depth = [], "", 0   # 空白で切る。ただし括弧の中（$(strip $MESSAGE)）の空白では切らない
    for ch in m.group(1):
        depth += (ch == "(") - (ch == ")")
        if ch.isspace() and depth == 0:
            toks, cur = toks + [cur] if cur else toks, ""
        else:
            cur += ch
    toks += [cur] if cur else []
    opts = {"--scope", "none", "--omit-empty-values"}
    return [tuple(t.split("=", 1)) for t in toks if t not in opts]

def shape(pairs):
    """key=value の並びを、Telegraf の行と同じ入れ子のキーと型（int64(...) は int、ほかは str）にする"""
    out = {}
    for k, v in pairs:
        t = int if v.startswith("int64(") else str
        *parents, leaf = k.split(".")
        d = out
        for p in parents:
            d = d.setdefault(p, {})
        d[leaf] = t
    return out

def tg_shape(line):  # Telegraf の実物の 1 行を、同じ入れ子のキーと型にする
    def f(v):
        return {k: f(x) for k, x in v.items()} if isinstance(v, dict) else type(v)
    return f(json.loads(line))


# ---- 1. Telegraf の device_log の実物（検証 2）。Telegraf 1.40.1 の inputs.syslog（+ processors.rename で hostname → sysName、json_timestamp_units = "1s"）に
#         同じ機器の行を RFC5424 と RFC3164 で 1 つずつ送り、outputs.file に出た行（2026-10-08、手元の docker。cycle 012 の build.md）
TG_5424 = ('{"fields":{"facility_code":23,"message":"msg fac=23 sev=0","msgid":"ID23","procid":"77","severity_code":0,"timestamp":1791462896000001000,"version":1},'
           '"name":"device_log","tags":{"appname":"app23_0","facility":"local7","severity":"emerg","source":"172.17.0.1","sysName":"leaf1"},"timestamp":1791465836}')
TG_3164 = ('{"fields":{"facility_code":23,"message":"msg fac=23 sev=0","procid":"77","severity_code":0,"timestamp":1791462896000000000},'
           '"name":"device_log","tags":{"appname":"app23_0","facility":"local7","severity":"emerg","source":"172.17.0.1","sysName":"leaf1"},"timestamp":1791465846}')
TAGS = {"sysName", "appname", "facility", "severity", "source"}
FIELDS = {"message", "severity_code", "facility_code", "procid", "msgid", "version", "timestamp"}
tg5, tg3 = tg_shape(TG_5424), tg_shape(TG_3164)
check("Telegraf の RFC5424 の行は tags が 5 つ・fields が 7 つ（design.md の検証 2 の表）", set(tg5["tags"]) == TAGS and set(tg5["fields"]) == FIELDS)
check("Telegraf の値の型: severity_code / facility_code / version / timestamp（fields と最上位）は数値、ほかは文字列",
      all(tg5["fields"][k] is int for k in ("severity_code", "facility_code", "version", "timestamp")) and tg5["timestamp"] is int
      and all(tg5["fields"][k] is str for k in ("message", "procid", "msgid")) and all(t is str for t in tg5["tags"].values()))

_rc5, _o5, conf5 = render({"KAFKA_AUTH": "none", "KAFKA_BROKERS": BROKERS, "SYSLOG_STANDARD": "RFC5424"})
_rc3, _o3, conf3 = render({"KAFKA_AUTH": "none", "KAFKA_BROKERS": BROKERS, "SYSLOG_STANDARD": "RFC3164"})
check("render が RFC5424 と RFC3164 の両方で通る", _rc5 == 0 and _rc3 == 0 and conf5 and conf3)
p5, p3 = json_pairs(conf5), json_pairs(conf3)
check("syslog-ng の RFC5424 の行は、Telegraf の RFC5424 の行とキーの集合も値の型も同じ（name / tags / fields / timestamp）", shape(p5) == tg5)
# RFC3164 には MSGID が無い。syslog-ng は fields.msgid=$MSGID を書くが、空なので --omit-empty-values で出ない（Telegraf も出さない）
check("syslog-ng の RFC3164 の行は、msgid（RFC3164 では空で出ない）を除いて Telegraf の RFC3164 の行と同じキーと型。version は描かない",
      shape([p for p in p3 if p[0] != "fields.msgid"]) == tg3 and ("fields.msgid", "$MSGID") in p3 and not any(k == "fields.version" for k, _ in p3))
check("値の無いキー（RFC3164 の msgid、PID の無い行の procid）は出さない: format-json に --omit-empty-values。--scope none で、書いたキーのほかを足さない",
      re.search(r"\$\(format-json --scope none --omit-empty-values\s", conf5) is not None)
# キーの名前だけでなく、どのマクロを入れるか（Grafana の logs のダッシュボードと Splunk の保存済みサーチが sysName / severity / message を見る）
check("format-json の中身: sysName は機器が名乗るホスト名、source は送り元の IP、severity / facility は名前、*_code は数値、timestamp は機器の時刻のナノ秒と受けた時刻の秒",
      dict(p5) == {"name": "device_log", "tags.sysName": "$HOST", "tags.appname": "$PROGRAM", "tags.facility": "$FACILITY", "tags.severity": "$LEVEL",
                   "tags.source": "$SOURCEIP", "fields.message": "$(strip $MESSAGE)", "fields.severity_code": "int64($LEVEL_NUM)",
                   "fields.facility_code": "int64($FACILITY_NUM)", "fields.procid": "$PID", "fields.msgid": "$MSGID", "fields.version": "int64(1)",
                   "fields.timestamp": "int64(${S_UNIXTIME}${S_USEC}000)", "timestamp": "int64($R_UNIXTIME)"})
check("HOST は機器が名乗る名前のまま（keep-hostname(yes)、DNS を引かない）", all(s in conf5 for s in ("keep-hostname(yes);", "use-dns(no);", "chain-hostnames(no);")))
check("書く先は Kafka の logs トピック（Spark の LOG_TOPICS と同じ名前）",
      'topic("logs")' in conf5 and re.search(r'^LOG_TOPICS = "[^"]*\blogs\b', read("app", "spark", "snmp_sinks.py"), re.M) is not None)


# ---- 2. render: 埋める値
check("KAFKA_AUTH=none: config() ごと消え、区間の印も SASL の字も残らない。bootstrap-servers は KAFKA_BROKERS",
      "config(" not in code(conf5) and "kafka_auth" not in code(conf5) and "sasl" not in code(conf5).lower() and f'bootstrap-servers("{BROKERS}")' in conf5)
check("RFC5424: udp と tcp の両方が flags(syslog-protocol) で 0.0.0.0:5140 を待つ（既定のアドレスとポート）",
      code(conf5).count('ip("0.0.0.0") port(5140) flags(syslog-protocol)') == 2 and 'transport("udp")' in conf5 and 'transport("tcp")' in conf5)
check("RFC3164: flags を付けない", "flags(" not in code(conf3) and code(conf3).count('ip("0.0.0.0") port(5140) );') == 2)
check("コメントでない行に __X__ が残らない（RFC5424 / RFC3164 とも）", "__" not in code(conf5) and "__" not in code(conf3))
_rc, _o, _c = render({"KAFKA_AUTH": "none", "KAFKA_BROKERS": BROKERS})
check("SYSLOG_STANDARD の既定は RFC3164（本番の Cisco IOS の BSD 形式）", _rc == 0 and _c == conf3 and "RFC3164" in _o)
_rc, _o, _c = render({"KAFKA_AUTH": "none", "KAFKA_BROKERS": BROKERS, "SYSLOG_STANDARD": "RFC5424", "SYSLOG_BIND": "203.0.113.1", "LOG_PORT": "6514"})
check("SYSLOG_BIND と LOG_PORT を渡せば、udp と tcp の両方がそこで待つ（手元の compose は lab の管理ネットの GW）",
      _rc == 0 and code(_c).count('ip("203.0.113.1") port(6514) flags(syslog-protocol)') == 2 and "0.0.0.0" not in code(_c) and "203.0.113.1:6514/udp+tcp" in _o)

# scram（既定）。値は英数字と ._~+/=@- だけ通る。毎回作る使い捨ての値で、設定にも出力にも出ないことを見る
_user, _pass = "collectors", "p" + secrets.token_urlsafe(24)
_rc, _o, _c = render({"KAFKA_BROKERS": BROKERS, "KAFKA_SASL_USER": _user, "KAFKA_SASL_PASS": _pass})
check("KAFKA_AUTH の既定は scram: config() に SASL_SSL / SCRAM-SHA-512 と、名前をバッククォートで囲んだ KAFKA_SASL_USER / KAFKA_SASL_PASS（syslog-ng が環境変数から入れる）",
      _rc == 0 and re.search(r'config\(\s*"security.protocol" => "SASL_SSL",\s*"sasl.mechanism" => "SCRAM-SHA-512",\s*'
                             r'"sasl.username" => "`KAFKA_SASL_USER`",\s*"sasl.password" => "`KAFKA_SASL_PASS`"\s*\)', _c) is not None
      and "kafka auth: SASL/SCRAM-SHA-512" in _o)
check("scram: 区間の印の行（# >>> / # <<<）だけ消える", not re.search(r"^# [<>]{3} kafka_auth", _c, re.M) and "config(" in code(_c))
check("scram: パスワードの値は設定にも render の出力にも出ない（/tmp の設定に残さない）", _pass not in _c and _pass not in _o)
_rc2, _, _c2 = render({"KAFKA_AUTH": "scram", "KAFKA_BROKERS": BROKERS, "KAFKA_SASL_USER": "u", "KAFKA_SASL_PASS": "Az09._~+/=@-"})
check("scram: 英数字と ._~+/=@- は通る（sed の区切りの # を含まない）", _rc2 == 0 and _c2 is not None)

# 止まる値。どれも設定を書かない（ECS では起動前に止まり、ログに理由が出る）
def refused(env, must):
    rc, out, conf = render(env)
    return rc != 0 and conf is None and must in out

NONE = {"KAFKA_AUTH": "none", "KAFKA_BROKERS": BROKERS}
for v in ("0", "065", "65536", "99999", "51a", "-1", "5140 "):
    check(f"LOG_PORT={v!r} は止まる（1〜65535 の数字だけ）", refused({**NONE, "LOG_PORT": v}, "LOG_PORT は 1〜65535 の数字"))
check("LOG_PORT=65535 は通る（上限）", render({**NONE, "LOG_PORT": "65535"})[0] == 0)
for v in ("0.0.0.0/0", "leaf1", "::1", "203.0.113.1;", "203.0.113", 'x") port(1'):
    check(f"SYSLOG_BIND={v!r} は止まる（空か IPv4 だけ）", refused({**NONE, "SYSLOG_BIND": v}, "SYSLOG_BIND は空"))
check("KAFKA_BROKERS が無ければ止まる", refused({"KAFKA_AUTH": "none"}, "KAFKA_BROKERS"))
for v in ("b-1:9096,", "b-1", "b#1:9096", "b-1:9096 b-2:9096", 'b-1:9096")'):
    check(f"KAFKA_BROKERS={v!r} は止まる（host:port をカンマで）", refused({**NONE, "KAFKA_BROKERS": v}, "KAFKA_BROKERS の形が違う"))
for v in ("iam", "SCRAM", ""):
    # 空は ${KAFKA_AUTH:-scram} で scram になる（資格情報が無いので止まる）
    check(f"KAFKA_AUTH={v!r} は止まる", refused({**NONE, "KAFKA_AUTH": v}, "KAFKA_SASL_USER が無い" if v == "" else "KAFKA_AUTH は scram か none"))
for v in ("rfc5424", "RFC 5424", "5424"):
    check(f"SYSLOG_STANDARD={v!r} は止まる", refused({**NONE, "SYSLOG_STANDARD": v}, "SYSLOG_STANDARD は RFC3164 か RFC5424"))
SCRAM = {"KAFKA_AUTH": "scram", "KAFKA_BROKERS": BROKERS}
check("scram で KAFKA_SASL_USER が無ければ止まる（空の名前で MSK に繋ぎに行かない）", refused({**SCRAM, "KAFKA_SASL_PASS": _pass}, "KAFKA_SASL_USER が無い"))
check("scram で KAFKA_SASL_PASS が無ければ止まる", refused({**SCRAM, "KAFKA_SASL_USER": _user}, "KAFKA_SASL_PASS が無い"))
check("scram で KAFKA_SASL_PASS が空なら止まる", refused({**SCRAM, "KAFKA_SASL_USER": _user, "KAFKA_SASL_PASS": ""}, "KAFKA_SASL_PASS が無い"))
for bad in ('"', "\\", "`", " ", "#", "$", "'", "\n"):
    _secret = "Zq" + secrets.token_hex(6)
    rc, out, conf = render({**SCRAM, "KAFKA_SASL_USER": _user, "KAFKA_SASL_PASS": _secret[:5] + bad + _secret[5:]})
    check(f"scram で KAFKA_SASL_PASS に {bad!r} があれば止まり、値を出さない（設定の文字列が壊れる）",
          rc != 0 and conf is None and "KAFKA_SASL_PASS に使えない文字" in out and _secret[:5] not in out and _secret[5:] not in out)
rc, out, conf = render({**SCRAM, "KAFKA_SASL_USER": "a b", "KAFKA_SASL_PASS": _pass})
check("scram で KAFKA_SASL_USER に空白があれば止まる", rc != 0 and conf is None and "KAFKA_SASL_USER に使えない文字" in out)
r = subprocess.run(["sh", SNG_SH, "bogus"], capture_output=True, text=True)
check("知らない引数は使い方を出して 1 で終わる", r.returncode == 1 and "sng render" in r.stdout)


# ---- 3. 設定のテンプレートとイメージ
conf_in = read("app", "syslog-ng", "syslog-ng.conf.in")
# syslog-ng はバッククォートをコメントの中でも環境変数として展開する（閉じていないと設定が読めない。4.29.0 で確かめた）
check("conf.in のバッククォートは KAFKA_SASL_USER と KAFKA_SASL_PASS を囲む 2 組だけ（コメントにも書かない）",
      conf_in.count("`") == 4 and re.findall(r"`([^`]*)`", conf_in) == ["KAFKA_SASL_USER", "KAFKA_SASL_PASS"])
check("conf.in の区間の印は 1 組（render の sed が見る形: 行頭の # >>> / # <<< kafka_auth scram）",
      len(re.findall(r"^# >>> kafka_auth scram$", conf_in, re.M)) == 1 and len(re.findall(r"^# <<< kafka_auth scram$", conf_in, re.M)) == 1)
check("conf.in の @version は 4.x（AxoSyslog 4 の書き方）", re.match(r"@version: 4\.\d+\n", conf_in) is not None)
check("syslog-ng 自身のログは notice 以上を標準出力へ（ECS のロググループ）",
      re.search(r"source \{ internal\(\); \};\s*filter \{ level\(notice\.\.emerg\); \};\s*destination\(d_self\);", conf_in) is not None
      and 'file("/dev/stdout"' in conf_in)
check("ポートの既定 5140 は lab の機器が送る先（app/containerlab/lab.sh の LOG_PORT）と同じ",
      sh_const(read("app", "syslog-ng", "syslog-ng.sh"), "LOG_PORT") == "${LOG_PORT:-5140}" and sh_const(read("app", "containerlab", "lab.sh"), "LOG_PORT") == "5140")

dockerfile = read("docker", "images", "syslog-ng", "Dockerfile")
sng = read("app", "syslog-ng", "syslog-ng.sh")
check("Dockerfile の版は ops/up-common.sh の SYSLOG_NG_VERSION と同じ（FROM は ghcr.io/axoflow/axosyslog）",
      re.search(r"^ARG SYSLOG_NG_VERSION=(\S+)$", dockerfile, re.M).group(1) == sh_const(read("ops", "up-common.sh"), "SYSLOG_NG_VERSION")
      and re.search(r"^FROM ghcr\.io/axoflow/axosyslog:\$\{SYSLOG_NG_VERSION\}$", dockerfile, re.M) is not None)
check("Dockerfile が置くテンプレートの場所 = syslog-ng.sh の SYSLOG_NG_TEMPLATE の既定、入口は /usr/local/bin/sng で run",
      "COPY syslog-ng.conf.in /etc/syslog-ng/" in dockerfile and "TEMPLATE=${SYSLOG_NG_TEMPLATE:-/etc/syslog-ng/syslog-ng.conf.in}" in sng
      and "COPY --chmod=0755 syslog-ng.sh /usr/local/bin/sng" in dockerfile
      and 'ENTRYPOINT ["/usr/local/bin/sng"]' in dockerfile and 'CMD ["run"]' in dockerfile)
check("nobody（65534）で動き、syslog-ng.sh は /tmp にだけ書く（設定・persist・pid・制御ソケット）",
      "USER 65534:65534" in dockerfile and "CONF=${SYSLOG_NG_CONF:-/tmp/syslog-ng.conf}" in sng and 'RUN_DIR=${CONF%/*}' in sng
      and '--persist-file "$RUN_DIR/syslog-ng.persist" --pidfile "$RUN_DIR/syslog-ng.pid" --control "$RUN_DIR/syslog-ng.ctl"' in sng)
check("HEALTHCHECK は syslog-ng.sh が置く /tmp の制御ソケットを見る（公式イメージの既定の場所は nobody では使えない）",
      re.search(r'^HEALTHCHECK .*CMD \["/usr/sbin/syslog-ng-ctl", "healthcheck", "--timeout", "5", "--control", "/tmp/syslog-ng.ctl"\]$', dockerfile, re.M) is not None)
check("syslog-ng.sh は POSIX sh（イメージは Alpine で bash が無い）", sng.startswith("#!/bin/sh\n"))
r = subprocess.run(["sh", "-n", SNG_SH], capture_output=True, text=True)
check("syslog-ng.sh は sh として読める", r.returncode == 0)


# ---- 4. tools/netflow_send.py（GoFlow2 を試す偽の NetFlow v5）
spec = importlib.util.spec_from_file_location("netflow_send", os.path.join(ROOT, "tools", "netflow_send.py"))
nf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nf)
pkt = nf.packet(1791462896.5, uptime_ms=60_000, seq=7)
check("NetFlow v5 は 72 バイト（ヘッダー 24 + フロー 1 本 48）", len(pkt) == 72)
ver, count, uptime, secs, nsecs, seq, _, _, _ = struct.unpack("!HHIIIIBBH", pkt[:24])
check("ヘッダー: version 5、count 1、sys_uptime と unix_secs / nsecs と flow_sequence", (ver, count, uptime, secs, nsecs, seq) == (5, 1, 60_000, 1791462896, 500_000_000, 7))
rec = struct.unpack("!4s4s4sHHIIIIHHBBBBHHBBH", pkt[24:])
check("フロー: 10.0.0.1:12345 → 10.0.0.2:443 の TCP（6）、10 パケット 8400 バイト、入力 if 1 → 出力 if 2（docstring の GoFlow2 の JSON と同じ値）",
      [socket.inet_ntoa(a) for a in rec[:3]] == ["10.0.0.1", "10.0.0.2", "10.0.0.254"]
      and rec[3:7] == (1, 2, 10, 8400) and rec[7:9] == (59_000, 60_000) and rec[9:11] == (12345, 443) and rec[13] == 6)
check("宛先: <host> [port] / <host>:<port>、既定のポートは 2055", nf.target(["127.0.0.1"]) == ("127.0.0.1", 2055)
      and nf.target(["127.0.0.1", "2056"]) == ("127.0.0.1", 2056) and nf.target(["nlb.example:2057"]) == ("nlb.example", 2057))
for bad in ([], ["a", "b", "c"], ["h", "x"], ["h:x"]):
    try:
        nf.target(bad)
        ok = False
    except SystemExit:
        ok = True
    check(f"宛先 {bad!r} は使い方かポートの誤りで止まる", ok)
check("netflow_send.py は tools Lambda の zip に入らない（gateway.tf の tools_files に無い）",
      "netflow_send" not in read("IaC", "terraform", "aws-managed", "workflow", "gateway.tf"))

shutil.rmtree(TMP)
print(f"通過 {passed} / 失敗 0")
