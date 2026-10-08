#!/usr/bin/env bash
# 手元で app/containerlab/lab.sh を動かす薄いラッパー。.env（無ければ .env.example）のイメージと TELEGRAF_LOCAL=1（trap を compose の Telegraf へ向ける）と
# LAB_CMD（案内の「戻すのは …」に出す打ち方。このラッパーのパス）を渡して sudo で呼ぶ
#   docker/compose/lab.sh up | down | check | fail-main | heal-main | trap-test | ...   （サブコマンドは app/containerlab/lab.sh の頭）
# 渡す環境はこの 5 つだけ（sudo -E にしない。シェルに REGISTRY や AWS_REGION があっても ECR や SSM へ行かない）
set -euo pipefail
cd "$(dirname "$0")"
ENVF=.env; [ -f "$ENVF" ] || ENVF=.env.example
# .env の値は docker compose 自身に読ませる。docker/compose/check.sh と同じ 2 行（読み方の説明はそちら。tests/test_local_compose.py が突き合わせる）。
# シェルに同じ名前の環境変数があればそちらが勝つ（compose と同じ）
ENV_ALL=$(docker compose --env-file "$ENVF" config --environment 2>/dev/null) || { echo "docker compose が $ENVF を読めない（書式の誤りか、config --environment の無い古い compose。理由は docker/compose で docker compose --env-file $ENVF config --environment >/dev/null を打って見る。値の一部が出ることがある）" >&2; exit 1; }
env_get() { printf '%s\n' "$ENV_ALL" | sed -n "s/^$1=//p" | tail -1; }
SRLINUX_IMAGE=$(env_get SRLINUX_IMAGE)
MULTITOOL_IMAGE=$(env_get MULTITOOL_IMAGE)
TREX_IMAGE=$(env_get TREX_IMAGE)
: "${SRLINUX_IMAGE:?$ENVF に SRLINUX_IMAGE が無い}" "${MULTITOOL_IMAGE:?$ENVF に MULTITOOL_IMAGE が無い}" "${TREX_IMAGE:?$ENVF に TREX_IMAGE が無い}"
lab() { sudo env SRLINUX_IMAGE="$SRLINUX_IMAGE" MULTITOOL_IMAGE="$MULTITOOL_IMAGE" TREX_IMAGE="$TREX_IMAGE" TELEGRAF_LOCAL=1 LAB_CMD="$0" "$PWD/../../app/containerlab/lab.sh" "$@"; }
# app/containerlab/lab.sh up は splab.clab.yml が既にあると作り直さない。gen_lab.py で台数を変えたあとや .env のイメージを変えたあとに
# 古い yml のまま deploy しないよう、up の前に毎回 render する
if [ "${1:-}" = up ]; then lab render; fi
lab "$@"
