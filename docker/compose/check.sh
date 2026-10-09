#!/usr/bin/env bash
# 手元の compose を通しで確かめる（docs/cycles/006-local-compose/design.md の検証方法「WSL」の 4）。全部見てから、1 つでも NG なら非 0 で終わる
#   docker/compose/check.sh    （up.sh と lab.sh up のあと 2〜3 分待ってから）
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo ".env が無い（up.sh の前に cp .env.example .env）" >&2; exit 1; }
ENVF=.env
# .env の値は docker compose 自身に読ませる（config --environment は compose が compose.yaml の展開に使う値を KEY=値 で 1 行ずつ出す）。クォート、\ の逃がし、
# $X と $$ の展開、# メモの扱いが compose と同じになり、シェルに同じ名前の環境変数があればそちらが勝つのも compose と同じ。値は 1 行に限る（改行を含む値は読み違える）。
# compose が読めないときのエラーは値の一部を含むことがあるので出さない。docker/compose/lab.sh と同じ 2 行（tests/test_local_compose.py が突き合わせる）
ENV_ALL=$(docker compose --env-file "$ENVF" config --environment 2>/dev/null) || { echo "docker compose が $ENVF を読めない（書式の誤りか、config --environment の無い古い compose。理由は docker/compose で docker compose --env-file $ENVF config --environment >/dev/null を打って見る。値の一部が出ることがある）" >&2; exit 1; }
env_get() { printf '%s\n' "$ENV_ALL" | sed -n "s/^$1=//p" | tail -1; }
if command -v python3 >/dev/null; then PY=(python3)
elif command -v uv >/dev/null; then PY=(uv run --python 3.13 python)
else echo "python3 か uv が要る（応答の JSON を読む）" >&2; exit 1; fi
ng=0
# GET / POST する。パスワードは引数に載せず（ps に出る）、curl の設定として標準入力で渡す。キーが - なら認証なし
get() {  # get <パスワードの .env のキー|-> <curl の引数...>
  local k=$1; shift
  if [ "$k" = - ]; then curl -sS --max-time 60 "$@"; return; fi
  printf 'user = "admin:%s"\n' "$(env_get "$k" | sed 's/[\\"]/\\&/g')" | curl -sS --max-time 60 -K - "$@"
}
# 応答（標準入力）を Python の式で判定する。式は本文 s から、通れば 'ok'、落ちれば理由、NG にしない注意なら '注意: 理由' を返す。
# 理由は 1 行（改行と続く空白は空白 1 つ）で 200 字まで
judge() {  # judge <名前> <式>
  local r
  r=$("${PY[@]}" -c "import json, sys
s = sys.stdin.read()
try: r = ($2)
except Exception: r = '読めない応答: ' + (s.strip()[:200] or '空')
print(' '.join(str(r).split())[:200])") || r="判定に失敗した"
  case "$r" in
    ok) echo "ok  $1" ;;
    注意:*) echo "注意 $1: ${r#注意: }" ;;
    *) echo "NG  $1: $r"; ng=1 ;;
  esac
}

# メモリ（design.md の見積もりは 16〜19 GB）。.wslconfig の 20GB は MemTotal では 19.x GiB に見えるので 19 GiB で切る
if command -v free >/dev/null; then
  m=$(free -m | awk '/^Mem:/{print $2}')
  [ "$m" -ge 19456 ] || echo "注意: メモリが ${m} MiB（20 GB 未満）。.wslconfig の memory を 20GB 以上にするか、lab を減らす（docker/compose/README.md）"
fi

# Spark の 2 つが動いているか。restart: on-failure:5 で止まったままのときと、依存（splunk / opensearch / prometheus）が healthy にならず Created のままのときを、
# 下の件数の NG より先に分ける。--format json は 1 行 1 コンテナ（古い compose は配列を 1 行）で、コンテナが無ければ何も出ない。
# compose の警告（up.sh が渡す GNMI_TARGETS などが無い）は出さない
ps=$(docker compose ps -a --format json spark-splunk spark-http 2>/dev/null || true)
for svc in spark-splunk spark-http; do
  judge "Spark: $svc が動いている" \
    "(lambda c: 'コンテナが無い（上がっていないか docker に繋がらない。docker/compose/up.sh で上げる）' if not c else 'ok' if all(x['State'] == 'running' for x in c)
     else '; '.join(sorted({x['State'] + '（' + x['Status'] + '）' for x in c if x['State'] != 'running'})) + (
       '。依存の splunk / opensearch / prometheus が healthy でない（docker compose -f docker/compose/compose.yaml ps -a で見る）' if all(x['State'] == 'created' for x in c)
       else '。docker compose -f docker/compose/compose.yaml logs $svc で理由を見て、直してから docker/compose/up.sh $svc'))(
     [x for v in [json.loads(l) for l in s.splitlines() if l.strip()] for x in (v if isinstance(v, list) else [v]) if x['Service'] == '$svc'])" \
    <<<"$ps"
done

# Kafka は Kafbat UI のトピックの一覧を 1 回取って 5 つ見る。messagesCount はトピックの全パーティションのメッセージ数の和
# （2026-10-08 に手元の compose で kafka-get-offsets.sh の最新オフセットの和と同じ値を確認。docker exec しなくて済む）
kafka=$(get - 'http://127.0.0.1:18080/api/clusters/nwc/topics?perPage=100' || true)
judge "Kafka: トピック metrics / gnmi / traps / logs / flows がある" \
  "(lambda n: 'ok' if not n else '無い: ' + ', '.join(n))(sorted({'metrics', 'gnmi', 'traps', 'logs', 'flows'} - {t['name'] for t in json.loads(s)['topics']}))" \
  <<<"$kafka"
# トピックは Spark が起動のときに作るので、gnmic・Telegraf・syslog-ng から届いているかはメッセージ数で見る（metrics は gnmic の IF のカウンター、
# gnmi は gnmic の IF・BGP・IS-IS の状態。on-change は購読した直後に今の値を 1 回送るので、gnmic が繋がっていれば 0 にならない）。trap と syslog は障害を入れるまで来ないこともあるので、
# traps と logs の 0 件は NG にしない。flows は lab の SR Linux が NetFlow を出さないので数を見ない（ops/netflow_send.py で送ったときだけ増える）
cnt="{t['name']: t.get('messagesCount') for t in json.loads(s)['topics']}"
# num('<トピック>') は件数（int）か、件数が読めない（messagesCount が無い・null・数でない）ときの NG の理由（str）。トピックが無ければ 0 件。
# 件数が読めないのは 0 件とは別の問題なので、traps と logs もそのときは注意でなく NG
num="(lambda c: lambda n: (lambda v: v if type(v) is int else \"messagesCount が無い（Kafbat UI の応答の形が違う。curl -s 'http://127.0.0.1:18080/api/clusters/nwc/topics?perPage=100' で中身を見る）\")(c.get(n, 0)))($cnt)"
judge "Kafka: metrics のメッセージ数 > 0" "(lambda v: v if type(v) is str else 'ok' if v > 0 else '0 件')($num('metrics'))" <<<"$kafka"
judge "Kafka: gnmi のメッセージ数 > 0" \
  "(lambda v: v if type(v) is str else 'ok' if v > 0 else '0 件（gnmic の on-change（IF・BGP・IS-IS の状態）が届いていない。購読した直後に今の値を 1 回送るので、gnmic が繋がっていれば 0 にならない。docker compose logs gnmic）')($num('gnmi'))" \
  <<<"$kafka"
judge "Kafka: traps のメッセージ数 > 0" \
  "(lambda v: v if type(v) is str else 'ok' if v > 0 else '注意: 0 件（trap は障害を入れるまで来ない。docker/compose/lab.sh fail-main か trap-test のあとに打ち直す）')($num('traps'))" \
  <<<"$kafka"
judge "Kafka: logs のメッセージ数 > 0" \
  "(lambda v: v if type(v) is str else 'ok' if v > 0 else '注意: 0 件（syslog は機器が出すまで来ない。docker/compose/lab.sh fail-main のあとに打ち直す。来ないままなら docker compose logs syslog-ng）')($num('logs'))" \
  <<<"$kafka"
judge "Prometheus: count(snmp_interface_oper_up) > 0" \
  "(lambda r: 'ok' if r and float(r[0]['value'][1]) > 0 else '0 件')(json.loads(s)['data']['result'])" \
  <<<"$(get - 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=count(snmp_interface_oper_up)' -G || true)"
judge "OpenSearch: snmp-logs の件数 > 0" \
  "'ok' if json.loads(s)['count'] > 0 else '0 件'" \
  <<<"$(get OPENSEARCH_PASSWORD 'http://127.0.0.1:9200/snmp-logs/_count' || true)"
# 認証の失敗などで result が無い応答を 0 件と分ける。messages の FATAL / ERROR はその理由（同じものは 1 つに）、
# ほかは「result が無い」、JSON でなければ「読めない応答」。本物の応答（034 で 10.4.4 に打った）: パスワード違いは 401 で {"messages":[{"type":"ERROR","text":"Unauthorized"}]}、
# 知らないコマンドなどは 400 で messages の FATAL、eval の引数の誤りや無い lookup は 200 で空の本文、通れば 200 で {"preview":false,"offset":0,"lastrow":true,"result":{"count":"N"}}（count は文字列）
judge "Splunk: sourcetype=nwc:* の直近 10 分の件数 > 0" \
  "(lambda o: (lambda m, c: '; '.join(sorted({e for t, e in m if t in ('FATAL', 'ERROR')}))
     or (('ok' if max(c) > 0 else '0 件') if c else 'result が無い' + (': ' + '; '.join(sorted({e for _, e in m})) if m else '')))(
     [(x.get('type'), str(x.get('type')) + ' ' + str(x.get('text'))) for d in o for x in d.get('messages', [])],
     [int(d['result'].get('count', 0)) for d in o if 'result' in d]))([json.loads(l) for l in s.splitlines() if l.strip()] or json.loads(s))" \
  <<<"$(get SPLUNK_PASSWORD -k 'https://127.0.0.1:8089/services/search/jobs/export' \
        --data-urlencode 'search=search index=* sourcetype=nwc:* earliest=-10m | stats count' -d output_mode=json || true)"
judge "Grafana: データソース uid amp / aoss-logs がある" \
  "(lambda n: 'ok' if not n else '無い: ' + ', '.join(n))(sorted({'amp', 'aoss-logs'} - {d['uid'] for d in json.loads(s)}))" \
  <<<"$(get GF_SECURITY_ADMIN_PASSWORD 'http://127.0.0.1:3000/api/datasources' || true)"
judge "Grafana: amp（Prometheus）の health が OK" \
  "'ok' if json.loads(s).get('status') == 'OK' else json.loads(s).get('message', s[:200])" \
  <<<"$(get GF_SECURITY_ADMIN_PASSWORD 'http://127.0.0.1:3000/api/datasources/uid/amp/health' || true)"
# Telegraf・syslog-ng・GoFlow2 は host のネットワークで、up.sh がコンテナを作ったときの TELEGRAF_BIND（lab の管理ネットの GW 203.0.113.1 か、空なら全部のインターフェース）で待つ。
# Telegraf の health と GoFlow2 の /metrics の宛先は、いま host にあるアドレスでなく動いているコンテナの設定から読む（up.sh のあとに lab.sh down / up しても、
# コンテナは作ったときのアドレスで待ったまま）。compose の呼び方は上の Spark の ps と同じ。ps -a は止まったコンテナも返す（Exited なら curl が「繋がらない」になる）
cid() { docker compose ps -a -q "$1" 2>/dev/null | head -n 1 || true; }  # cid <サービス> → コンテナ ID（無ければ空）
# dest <サービス> <コンテナ ID> <bind> → 打つ先のアドレスか、curl を打たずに NG にする理由（頭に「NG 」）。bind が空なら全部のインターフェースで待つので 127.0.0.1。
# grep は -q にしない（見つけた所で読むのをやめると ip が SIGPIPE で落ち、pipefail で外れたことになる）
dest() {
  if [ -z "$2" ]; then echo "NG コンテナが無い（docker/compose/up.sh $1 で上げる）"
  elif [ -z "$3" ]; then echo 127.0.0.1
  elif ip -o -4 addr show 2>/dev/null | grep -F -- " $3/" >/dev/null; then echo "$3"
  else echo "NG $3 が host に無い（lab.sh down のあとなら docker/compose/lab.sh up で戻すか、docker/compose/up.sh telegraf syslog-ng goflow2 で上げ直す）"; fi
}
probe() {  # probe <名前> <サービス> <dest の出力> <:ポート/パス>。HTTP の番号が 200 なら ok
  case "$3" in
    NG\ *) echo "NG  $1: ${3#NG }"; ng=1 ;;
    *) judge "$1" \
         "'ok' if s.strip() == '200' else ('繋がらない' if s.strip() in ('', '000') else 'HTTP ' + s.strip()) + '（docker compose ps -a $2 が Exited なら logs $2 で理由を見て up.sh $2）'" \
         <<<"$(get - -o /dev/null -w '%{http_code}' "http://$3$4" || true)" ;;
  esac
}
# Telegraf の health（outputs.health）。bind とポートはコンテナの環境の TELEGRAF_BIND と HEALTH_PORT（無ければ 8080）。.env の HEALTH_PORT は読まない
# （コンテナが上がったあとに .env だけ変えると、待っていないポートに打つ）。restart: on-failure:5 で止まったままのときもここで分かる
tg=$(cid telegraf); tg_env=
[ -z "$tg" ] || tg_env=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$tg" 2>/dev/null || true)
hp=$(printf '%s\n' "$tg_env" | sed -n 's/^HEALTH_PORT=//p' | tail -n 1)
probe "Telegraf: health が 200" telegraf "$(dest telegraf "$tg" "$(printf '%s\n' "$tg_env" | sed -n 's/^TELEGRAF_BIND=//p' | tail -n 1)")" ":${hp:-8080}/"
# syslog-ng は 5140/udp を待っているか（ss の 4 列目が待っているアドレス:ポート。bind に依らない）
judge "syslog-ng: udp 5140 を待っている" \
  "'ok' if any(l.split()[3].endswith(':5140') for l in s.splitlines() if len(l.split()) > 3) else '待っていない（docker compose ps -a syslog-ng が Exited なら logs syslog-ng で理由を見て up.sh syslog-ng）'" \
  <<<"$(ss -Hlun 2>/dev/null || true)"
# GoFlow2 は /metrics（8081。8080 は Telegraf の health）。bind はコンテナの引数の -addr=<bind>:8081
gf=$(cid goflow2); gf_cmd=
[ -z "$gf" ] || gf_cmd=$(docker inspect --format '{{json .Config.Cmd}}' "$gf" 2>/dev/null || true)
probe "GoFlow2: /metrics が 200" goflow2 "$(dest goflow2 "$gf" "$(printf '%s\n' "$gf_cmd" | sed -n 's/.*"-addr=\([^"]*\):[0-9]*".*/\1/p' | tail -n 1)")" ":8081/metrics"

if [ "$ng" = 0 ]; then echo "すべて ok"; else echo "NG がある（docker compose logs <サービス> で見る）"; fi
exit "$ng"
