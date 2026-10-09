#!/usr/bin/env bash
# 手元（WSL2 の docker-ce）で compose を上げる。gNMI の購読先・device map は AWS 版（ops/up.sh）と同じく lab の定義から作って環境で渡す
#   docker/compose/up.sh [docker compose up の引数...]   （例: up.sh telegraf で Telegraf だけ作り直す）
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo ".env が無い。先に: cp .env.example .env（パスワードと HEC トークンを変えるなら最初の up.sh の前に。あとで変えるなら down.sh -v で volume ごと作り直す）" >&2; exit 1; }
if command -v python3 >/dev/null; then PY=(python3)
elif command -v uv >/dev/null; then PY=(uv run --python 3.13 python)
else echo "python3 か uv が要る（app/containerlab/lab_topology.py を動かす）" >&2; exit 1; fi
GNMI_TARGETS=$("${PY[@]}" ../../app/containerlab/lab_topology.py ../../app/containerlab --gnmi-targets)
DEVICE_MAP=$("${PY[@]}" ../../app/containerlab/lab_topology.py ../../app/containerlab --device-map)
# Telegraf・syslog-ng・GoFlow2（host のネットワーク）が待つアドレス。lab の管理ネットの GW（app/containerlab/lab.sh の MGMT_GW。lab.sh up で containerlab の bridge に付く）が
# host にあればそこだけで待つ。無ければ bind に失敗するので空（全部のインターフェース）にする。docker/compose/check.sh も同じ見方で health と /metrics に打つ
MGMT_GW=203.0.113.1
if ip -o -4 addr show 2>/dev/null | grep -q " $MGMT_GW/"; then TELEGRAF_BIND=$MGMT_GW
else
  TELEGRAF_BIND=
  echo "WARNING: lab の管理ネット（${MGMT_GW}）がまだ無いので、Telegraf・syslog-ng・GoFlow2 は WSL の全部のインターフェースで待つ。docker/compose/lab.sh up のあとに docker/compose/up.sh telegraf syslog-ng goflow2 で ${MGMT_GW} だけに直す" >&2
fi
export GNMI_TARGETS DEVICE_MAP TELEGRAF_BIND
# Spark の 2 つは送り先（Splunk / OpenSearch / Prometheus）が healthy になるまで起こさない（compose.yaml の depends_on）ので、初回はここで 2〜3 分待つ。
# healthy にならなければ dependency failed to start で止まる（docker/compose/README.md）
docker compose up -d --build "$@"
echo "上げた。通しの確認は docker/compose/check.sh"
