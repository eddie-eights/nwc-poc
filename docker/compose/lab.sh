#!/usr/bin/env bash
# 手元で app/containerlab/lab.sh を動かす薄いラッパー。.env（無ければ .env.example）のイメージと TELEGRAF_LOCAL=1（trap を compose の Telegraf へ向ける）を渡して sudo で呼ぶ
#   docker/compose/lab.sh up | down | check | fail-main | heal-main | trap-test | ...   （サブコマンドは app/containerlab/lab.sh の頭）
# 渡す環境はこの 4 つだけ（sudo -E にしない。シェルに REGISTRY や AWS_REGION があっても ECR や SSM へ行かない）
set -euo pipefail
cd "$(dirname "$0")"
f=.env; [ -f "$f" ] || f=.env.example
env_get() { sed -n "s/^$1=//p" "$f" | tail -1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"; }
SRLINUX_IMAGE=$(env_get SRLINUX_IMAGE)
MULTITOOL_IMAGE=$(env_get MULTITOOL_IMAGE)
TREX_IMAGE=$(env_get TREX_IMAGE)
: "${SRLINUX_IMAGE:?$f に SRLINUX_IMAGE が無い}" "${MULTITOOL_IMAGE:?$f に MULTITOOL_IMAGE が無い}" "${TREX_IMAGE:?$f に TREX_IMAGE が無い}"
lab() { sudo env SRLINUX_IMAGE="$SRLINUX_IMAGE" MULTITOOL_IMAGE="$MULTITOOL_IMAGE" TREX_IMAGE="$TREX_IMAGE" TELEGRAF_LOCAL=1 "$PWD/../../app/containerlab/lab.sh" "$@"; }
# app/containerlab/lab.sh up は splab.clab.yml が既にあると作り直さない。gen_lab.py で台数を変えたあとや .env のイメージを変えたあとに
# 古い yml のまま deploy しないよう、up の前に毎回 render する
if [ "${1:-}" = up ]; then lab render; fi
lab "$@"
