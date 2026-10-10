#!/bin/sh
# namespace を無いときだけ作る（cycle 039。docker/images/temporal-server/Dockerfile が /etc/temporal/namespace-rds.sh に置く）。
# entrypoint-rds.sh が master のパスワードを env から外したあとに背景で起こす。公式 create-namespace.sh と同じ流れで、サーバーが上がるのを待ってから作る。
# 使い方: namespace-rds.sh <frontend のアドレス> <namespace> <retention>
# 引数が足りなければ非 0 で抜ける（起こし方の誤り）。動作中の失敗（frontend が開かない、cluster health が通らない、作れない）は log を出して exit 0 で抜けるだけ。本体の temporal-server は別プロセスで、この終了コードを誰も待たない。健全性は ECS の healthCheck の describe が見る。
# exec tini の後は tini の子になり、抜けたら tini が回収する（ゾンビを残さない）。
set -u

: "${1:?namespace-rds.sh <frontend のアドレス> <namespace> <retention> の 1 つ目が無い}"
address=$1
: "${2:?namespace-rds.sh <frontend のアドレス> <namespace> <retention> の 2 つ目が無い}"
namespace=$2
: "${3:?namespace-rds.sh <frontend のアドレス> <namespace> <retention> の 3 つ目が無い}"
retention=$3

log() { echo "entrypoint-rds: $*" >&2; }

n=0
until nc -z -w 10 "${address%:*}" "${address##*:}"; do
  n=$((n + 1)); if [ "$n" -ge 30 ]; then log "namespace: frontend の ${address##*:} が開かない（30 回）。作らずに抜ける"; exit 0; fi
  sleep 5
done
n=0
until temporal operator cluster health --address "$address" >/dev/null 2>&1; do
  n=$((n + 1)); if [ "$n" -ge 30 ]; then log "namespace: cluster health が通らない（30 回）。作らずに抜ける"; exit 0; fi
  sleep 5
done
n=0
while :; do
  if temporal operator namespace describe -n "$namespace" --address "$address" >/dev/null 2>&1; then
    log "namespace: $namespace がある"; exit 0
  fi
  if temporal operator namespace create -n "$namespace" --retention "$retention" --address "$address" >/dev/null 2>&1; then
    log "namespace: $namespace を作った（retention ${retention}）"; exit 0
  fi
  n=$((n + 1)); if [ "$n" -ge 30 ]; then log "namespace: $namespace を作れない（30 回）"; exit 0; fi
  sleep 5
done
