#!/usr/bin/env bash
# 手元で app/containerlab/lab.sh を動かす薄いラッパー。.env（無ければ .env.example）のイメージと TELEGRAF_LOCAL=1（trap を compose の Telegraf へ向ける）を渡して sudo で呼ぶ
#   docker/compose/lab.sh up | down | check | fail-main | heal-main | trap-test | ...   （サブコマンドは app/containerlab/lab.sh の頭）
# 渡す環境はこの 3 つだけ（sudo -E にしない。シェルに REGISTRY や AWS_REGION があっても ECR や SSM へ行かない）
set -euo pipefail
cd "$(dirname "$0")"
ENVF=.env; [ -f "$ENVF" ] || ENVF=.env.example
# .env の値を読む（docker compose の読み方に合わせる）。docker/compose/check.sh と同じ関数（読み方の説明はそちら。tests/test_local_compose.py が同じ入力で突き合わせる）
env_get() { tr -d '\r' < "$ENVF" | sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$1=//p" | tail -1 | sed -e "s/^[[:space:]]*\"\([^\"]*\)\".*/\1/;t" -e "s/^[[:space:]]*'\([^']*\)'.*/\1/;t" -e 's/^[[:space:]]*//' -e 's/[[:space:]]\{1,\}#.*//' -e 's/[[:space:]]*$//'; }
SRLINUX_IMAGE=$(env_get SRLINUX_IMAGE)
MULTITOOL_IMAGE=$(env_get MULTITOOL_IMAGE)
: "${SRLINUX_IMAGE:?$ENVF に SRLINUX_IMAGE が無い}" "${MULTITOOL_IMAGE:?$ENVF に MULTITOOL_IMAGE が無い}"
lab() { sudo env SRLINUX_IMAGE="$SRLINUX_IMAGE" MULTITOOL_IMAGE="$MULTITOOL_IMAGE" TELEGRAF_LOCAL=1 "$PWD/../../app/containerlab/lab.sh" "$@"; }
# app/containerlab/lab.sh up は splab.clab.yml が既にあると作り直さない。gen_lab.py で台数を変えたあとや .env のイメージを変えたあとに
# 古い yml のまま deploy しないよう、up の前に毎回 render する
if [ "${1:-}" = up ]; then lab render; fi
lab "$@"
