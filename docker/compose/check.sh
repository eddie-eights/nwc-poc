#!/usr/bin/env bash
# 手元の compose を通しで確かめる（docs/cycles/006-local-compose/design.md の検証方法「WSL」の 4）。全部見てから、1 つでも NG なら非 0 で終わる
#   docker/compose/check.sh    （up.sh と lab.sh up のあと 2〜3 分待ってから）
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo ".env が無い（up.sh の前に cp .env.example .env）" >&2; exit 1; }
env_get() { sed -n "s/^$1=//p" .env | tail -1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"; }
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
# 応答（標準入力）を Python の式で判定する。式は本文 s から、通れば 'ok'、落ちれば理由を返す
judge() {  # judge <名前> <式>
  local r
  r=$("${PY[@]}" -c "import json, sys
s = sys.stdin.read()
try: print($2)
except Exception: print('読めない応答: ' + (s.strip()[:200].replace(chr(10), ' ') or '空'))") || r="判定に失敗した"
  if [ "$r" = ok ]; then echo "ok  $1"; else echo "NG  $1: $r"; ng=1; fi
}

# メモリ（design.md の見積もりは 16〜19 GB）。.wslconfig の 20GB は MemTotal では 19.x GiB に見えるので 19 GiB で切る
if command -v free >/dev/null; then
  m=$(free -m | awk '/^Mem:/{print $2}')
  [ "$m" -ge 19456 ] || echo "注意: メモリが ${m} MiB（20 GB 未満）。.wslconfig の memory を 20GB 以上にするか、lab を減らす（docker/compose/README.md）"
fi

judge "Kafka: トピック metrics / gnmi / traps / logs がある" \
  "(lambda n: 'ok' if not n else '無い: ' + ', '.join(n))(sorted({'metrics', 'gnmi', 'traps', 'logs'} - {t['name'] for t in json.loads(s)['topics']}))" \
  <<<"$(get - 'http://127.0.0.1:18080/api/clusters/nwc/topics?perPage=100' || true)"
judge "Prometheus: count(snmp_interface_ifOperStatus) > 0" \
  "(lambda r: 'ok' if r and float(r[0]['value'][1]) > 0 else '0 件')(json.loads(s)['data']['result'])" \
  <<<"$(get - 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=count(snmp_interface_ifOperStatus)' -G || true)"
judge "OpenSearch: snmp-logs の件数 > 0" \
  "'ok' if json.loads(s)['count'] > 0 else '0 件'" \
  <<<"$(get OPENSEARCH_PASSWORD 'http://127.0.0.1:9200/snmp-logs/_count' || true)"
# 認証の失敗などで result が無い応答を 0 件と分ける（本物の 401 の本文は未確認）。messages の FATAL / ERROR はその理由、ほかは「result が無い」、JSON でなければ「読めない応答」
judge "Splunk: sourcetype=netops:* の直近 10 分の件数 > 0" \
  "(lambda o: (lambda m, c: '; '.join(e for t, e in m if t in ('FATAL', 'ERROR'))
     or (('ok' if max(c) > 0 else '0 件') if c else 'result が無い' + (': ' + '; '.join(e for _, e in m) if m else '')))(
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

if [ "$ng" = 0 ]; then echo "すべて ok"; else echo "NG がある（docker compose logs <サービス> で見る）"; fi
exit "$ng"
