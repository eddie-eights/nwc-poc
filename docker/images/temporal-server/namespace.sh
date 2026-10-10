#!/bin/sh
# namespace を無いときだけ作る（cycle 039。docker/images/temporal-server/Dockerfile が /etc/temporal/namespace-rds.sh に置く）。
# entrypoint-rds.sh が master のパスワードを env から外したあとに背景で起こす。公式 create-namespace.sh と同じ流れで、サーバーが上がるのを待ってから作る。
# 使い方: namespace-rds.sh <frontend のアドレス> <namespace> <retention>
# 失敗しても exit 0（本体の temporal-server は落とさない。healthCheck が namespace を見るので ECS 側で UNHEALTHY になる）。
# exec tini の後は tini の子になり、抜けたら tini が回収する（ゾンビを残さない）。
set -u

address=$1
namespace=$2
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
