#!/usr/bin/env bash
# 手元の compose を通しで確かめる（docs/cycles/006-local-compose/design.md の検証方法「WSL」の 4）。全部見てから、1 つでも NG なら非 0 で終わる
#   docker/compose/check.sh    （up.sh と lab.sh up のあと 2〜3 分待ってから）
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo ".env が無い（up.sh の前に cp .env.example .env）" >&2; exit 1; }
ENVF=.env
# .env の値を読む（docker compose の読み方に合わせる）。行頭の export と CRLF の \r を落とし、"…" と '…' は中身だけ（閉じたあとの # メモは捨てる）、
# クォート無しは空白のあとの # から後ろと前後の空白を落とす（x#y の # は残す）。同じ名前が 2 つあれば後のもの。"…" の中の \ の逃がしと $VAR の展開はしない。
# docker/compose/lab.sh と同じ関数（tests/test_local_compose.py が同じ入力で突き合わせる）
env_get() { tr -d '\r' < "$ENVF" | sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$1=//p" | tail -1 | sed -e "s/^[[:space:]]*\"\([^\"]*\)\".*/\1/;t" -e "s/^[[:space:]]*'\([^']*\)'.*/\1/;t" -e 's/^[[:space:]]*//' -e 's/[[:space:]]\{1,\}#.*//' -e 's/[[:space:]]*$//'; }
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

# Kafka は Kafbat UI のトピックの一覧を 1 回取って 3 つ見る。messagesCount はトピックの全パーティションのメッセージ数の和
# （2026-10-08 に手元の compose で kafka-get-offsets.sh の最新オフセットの和と同じ値を確認。docker exec しなくて済む）
kafka=$(get - 'http://127.0.0.1:18080/api/clusters/nwc/topics?perPage=100' || true)
judge "Kafka: トピック metrics / gnmi / traps / logs がある" \
  "(lambda n: 'ok' if not n else '無い: ' + ', '.join(n))(sorted({'metrics', 'gnmi', 'traps', 'logs'} - {t['name'] for t in json.loads(s)['topics']}))" \
  <<<"$kafka"
# トピックは Spark が起動のときに作るので、Telegraf から届いているかはメッセージ数で見る。trap は障害を入れるまで来ないので、traps の 0 件は NG にしない
cnt="{t['name']: t['messagesCount'] for t in json.loads(s)['topics']}"
judge "Kafka: metrics のメッセージ数 > 0" "'ok' if $cnt.get('metrics', 0) > 0 else '0 件'" <<<"$kafka"
judge "Kafka: traps のメッセージ数 > 0" \
  "'ok' if $cnt.get('traps', 0) > 0 else '注意: 0 件（trap は障害を入れるまで来ない。docker/compose/lab.sh fail-main か trap-test のあとに打ち直す）'" \
  <<<"$kafka"
judge "Prometheus: count(snmp_interface_ifOperStatus) > 0" \
  "(lambda r: 'ok' if r and float(r[0]['value'][1]) > 0 else '0 件')(json.loads(s)['data']['result'])" \
  <<<"$(get - 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=count(snmp_interface_ifOperStatus)' -G || true)"
judge "OpenSearch: snmp-logs の件数 > 0" \
  "'ok' if json.loads(s)['count'] > 0 else '0 件'" \
  <<<"$(get OPENSEARCH_PASSWORD 'http://127.0.0.1:9200/snmp-logs/_count' || true)"
# 認証の失敗などで result が無い応答を 0 件と分ける（本物の 401 の本文は未確認）。messages の FATAL / ERROR はその理由（同じものは 1 つに）、
# ほかは「result が無い」、JSON でなければ「読めない応答」
judge "Splunk: sourcetype=netops:* の直近 10 分の件数 > 0" \
  "(lambda o: (lambda m, c: '; '.join(sorted({e for t, e in m if t in ('FATAL', 'ERROR')}))
     or (('ok' if max(c) > 0 else '0 件') if c else 'result が無い' + (': ' + '; '.join(sorted({e for _, e in m})) if m else '')))(
     [(x.get('type'), str(x.get('type')) + ' ' + str(x.get('text'))) for d in o for x in d.get('messages', [])],
     [int(d['result'].get('count', 0)) for d in o if 'result' in d]))([json.loads(l) for l in s.splitlines() if l.strip()] or json.loads(s))" \
  <<<"$(get SPLUNK_PASSWORD -k 'https://127.0.0.1:8089/services/search/jobs/export' \
        --data-urlencode 'search=search index=* sourcetype=netops:* earliest=-10m | stats count' -d output_mode=json || true)"
judge "Grafana: データソース uid amp / aoss-logs がある" \
  "(lambda n: 'ok' if not n else '無い: ' + ', '.join(n))(sorted({'amp', 'aoss-logs'} - {d['uid'] for d in json.loads(s)}))" \
  <<<"$(get GF_SECURITY_ADMIN_PASSWORD 'http://127.0.0.1:3000/api/datasources' || true)"
judge "Grafana: amp（Prometheus）の health が OK" \
  "'ok' if json.loads(s).get('status') == 'OK' else json.loads(s).get('message', s[:200])" \
  <<<"$(get GF_SECURITY_ADMIN_PASSWORD 'http://127.0.0.1:3000/api/datasources/uid/amp/health' || true)"
# Telegraf の health（outputs.health）。up.sh と同じく lab の管理ネットの GW（app/containerlab/lab.sh の MGMT_GW）が host にあればそこ、無ければ 127.0.0.1 に打つ
# （GW が無いときの Telegraf は全部のインターフェースで待つ）。ポートは compose と同じくシェルの HEALTH_PORT、.env の HEALTH_PORT、8080 の順。
# restart: on-failure:5 で止まったままのときもここで分かる
MGMT_GW=203.0.113.1
tb=127.0.0.1; ip -o -4 addr show 2>/dev/null | grep -q " $MGMT_GW/" && tb=$MGMT_GW
hp=${HEALTH_PORT:-$(env_get HEALTH_PORT)}
judge "Telegraf: health が 200" \
  "'ok' if s.strip() == '200' else ('繋がらない' if s.strip() in ('', '000') else 'HTTP ' + s.strip()) + '（docker compose ps -a telegraf が Exited なら logs telegraf で理由を見て up.sh telegraf）'" \
  <<<"$(get - -o /dev/null -w '%{http_code}' "http://$tb:${hp:-8080}/" || true)"

if [ "$ng" = 0 ]; then echo "すべて ok"; else echo "NG がある（docker compose logs <サービス> で見る）"; fi
exit "$ng"
