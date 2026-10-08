#!/usr/bin/env bash
# 手元（WSL2 の docker-ce）で compose を上げる。ポーリング先・gNMI の購読先・device map は AWS 版（ops/up.sh）と同じく lab の定義から作って環境で渡す
#   local/compose/up.sh [docker compose up の引数...]   （例: up.sh telegraf で Telegraf だけ作り直す）
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo ".env が無い。先に: cp .env.example .env（パスワードと HEC トークンを変えるなら最初の up.sh の前に。あとで変えるなら down.sh -v で volume ごと作り直す）" >&2; exit 1; }
if command -v python3 >/dev/null; then PY=(python3)
elif command -v uv >/dev/null; then PY=(uv run --python 3.13 python)
else echo "python3 か uv が要る（lab/lab_topology.py を動かす）" >&2; exit 1; fi
SNMP_AGENTS=$("${PY[@]}" ../../lab/lab_topology.py ../../lab --snmp-agents)
GNMI_TARGETS=$("${PY[@]}" ../../lab/lab_topology.py ../../lab --gnmi-targets)
DEVICE_MAP=$("${PY[@]}" ../../lab/lab_topology.py ../../lab --device-map)
export SNMP_AGENTS GNMI_TARGETS DEVICE_MAP
docker compose up -d --build "$@"
echo "上げた。lab は local/compose/lab.sh up、通しの確認は local/compose/check.sh（Splunk は healthy まで 2〜3 分）"
