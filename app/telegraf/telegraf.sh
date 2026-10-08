#!/usr/bin/env bash
# Telegraf のコンテナ（IaC/terraform/aws-managed/pipeline/stream の ECS。docker/images/telegraf/Dockerfile）の入口。イメージの /usr/local/bin/tg。
#   tg run    （既定。ECS が起こす）設定を作って Telegraf を起こす
#   tg render （設定を作るだけ。中身を見る。tests/test_lab_debug.py も手元でこれを回す）
# 受けるのは機器から送ってくる SNMP trap だけ（stream の ECS の telegraf-dialout。NLB の後ろ）。デバッグ用の EC2（IaC/cloudformation/lab-debug.yaml）は
# SINK=stdout で同じことをする（app/containerlab/lab.sh telegraf）。
# gNMI の購読と SNMP のポーリング（取りにいく側の telegraf-dialin。TELEGRAF_ROLE=dialin と tg test / gnmi）は cycle 013 でやめた。gNMI は gnmic が取り、
# 1 回だけ取って見るのは gnmic のタスクの gn get（app/gnmic/gnmic.sh。stream の output の gnmic_exec_command）。TELEGRAF_ROLE と SNMP_POLL はもう読まない。
# 機器（lab の EC2 の中の containerlab）からの経路は lab の EC2 側で `sudo lab forward-status` を見る。
set -euo pipefail
TEMPLATE=${TELEGRAF_TEMPLATE:-/etc/telegraf/telegraf.conf.in}
# コンテナの / は書けるが、書くのは /tmp だけにする（作り直せば消える）
CONF=${TELEGRAF_CONF:-/tmp/telegraf.conf}
export AWS_CONFIG_FILE="${CONF%/*}/aws_config"
# 出力先。kafka = Kafka（stream の ECS。既定。マネージド版は MSK、OSS 版は ECS の Kafka）/ stdout = 標準出力（デバッグ用の EC2。MSK が無い）。telegraf.conf.in の「>>> sink <名前>」の区間
SINK=${SINK:-kafka}
SINKS="kafka stdout"
# Kafka の認証。iam = MSK の IAM 認証（既定）/ none = 認証なしの PLAINTEXT（OSS 版の ECS の Kafka。cycle 005。app/spark/snmp_sinks.py と同じ名前と値）。
# telegraf.conf.in の「>>> kafka_auth iam」の区間。SINK=kafka のときだけ効く
KAFKA_AUTH=${KAFKA_AUTH:-iam}
# 受け口のポート。既定は ECS（IaC/terraform/aws-managed/pipeline/stream の telegraf.tf。何も渡さない）と同じで、環境変数があればそれ。telegraf.conf.in の __X_PORT__ を埋める
# trap を受ける UDP のポート。機器は 162 に送り、NLB が 1162 に向ける（非 root は 1024 未満で待てない。app/containerlab/lab.sh の TRAP_PORT と同じ）
TRAP_PORT=${TRAP_PORT:-1162}
# 生きているかの口（outputs.health）の TCP のポート（IaC/terraform/aws-managed/pipeline/stream の telegraf.tf の NLB のヘルスチェック）
HEALTH_PORT=${HEALTH_PORT:-8080}
# 受け口の 2 つ（trap・health）を待つ IPv4 アドレス。空（既定）は全部のインターフェース（ECS とデバッグ用の EC2）。
# 手元の compose は docker/compose/up.sh が lab の管理ネットの GW（203.0.113.1）を渡し、WSL のほかのインターフェースでは待たない
TELEGRAF_BIND=${TELEGRAF_BIND:-}

render() {
  # ECS のタスク定義の環境変数（IaC/terraform/aws-managed/pipeline/stream の telegraf.tf）を埋めて $CONF を作る:
  #   KAFKA_BROKERS  Kafka のブローカー（host:port をカンマで。MSK は IAM 認証の口の 9098、OSS 版は kafka-N.<名前空間>:9092）。SINK=stdout では要らない
  #   KAFKA_AUTH     Kafka の認証（iam / none、既定 iam）。none は outputs.kafka の IAM の行を消し、aws_config も書かない
  #   TRAP_PORT / HEALTH_PORT / TELEGRAF_BIND  受け口のポートとアドレス（上の既定。ECS は渡さない。手元の compose は docker/compose/up.sh）
  #   AWS_REGION
  : "${AWS_REGION:?}"
  local q="" s drop=() ins="" auth="" p
  # sed で埋めるので、決まった形だけ通す（ポートは 1〜65535 の数字、アドレスは空か IPv4）
  for p in TRAP_PORT HEALTH_PORT; do
    case "${!p}" in ''|0*|*[!0-9]*) echo "$p は 1〜65535 の数字: ${!p}" >&2; exit 1 ;; esac
    [ "${!p}" -le 65535 ] || { echo "$p は 1〜65535 の数字: ${!p}" >&2; exit 1; }
  done
  if [ -n "$TELEGRAF_BIND" ] && ! printf '%s' "$TELEGRAF_BIND" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
    echo "TELEGRAF_BIND は空（全部のインターフェース）か IPv4 のアドレス: $TELEGRAF_BIND" >&2; exit 1
  fi
  case " $SINKS " in *" $SINK "*) ;; *) echo "SINK は $SINKS のどれか: $SINK" >&2; exit 1 ;; esac
  case "$KAFKA_AUTH" in iam|none) ;; *) echo "KAFKA_AUTH は iam か none: $KAFKA_AUTH" >&2; exit 1 ;; esac
  ins=" / trap: ${TRAP_PORT}/udp / health: ${HEALTH_PORT}/tcp${TELEGRAF_BIND:+ / bind: $TELEGRAF_BIND}"
  if [ "$SINK" = kafka ]; then
    : "${KAFKA_BROKERS:?}"
    if ! printf '%s' "$KAFKA_BROKERS" | grep -Eq '^[A-Za-z0-9.-]+:[0-9]+(,[A-Za-z0-9.-]+:[0-9]+)*$'; then
      echo "KAFKA_BROKERS の形が違う: $KAFKA_BROKERS" >&2; exit 1
    fi
    q=$(printf '"%s"' "${KAFKA_BROKERS//,/\",\"}")
    if [ "$KAFKA_AUTH" = iam ]; then
      # Telegraf の MSK IAM 認証は profile の指定が要る（telegraf.conf.in の注記）。鍵を書かない [default] なので、
      # SDK はタスクロール（ECS が入れる AWS_CONTAINER_CREDENTIALS_RELATIVE_URI）を使う
      printf '[default]\nregion = %s\n' "$AWS_REGION" > "$AWS_CONFIG_FILE"
    else
      # 認証なしの PLAINTEXT。outputs.kafka から TLS と SASL の行を消す
      drop+=(-e "/^# >>> kafka_auth iam/,/^# <<< kafka_auth iam/d")
      auth=" / kafka auth: none"
    fi
  fi
  # 選ばなかった出力の区間を消す
  for s in $SINKS; do [ "$s" = "$SINK" ] || drop+=(-e "/^# >>> sink $s/,/^# <<< sink $s/d"); done
  sed "${drop[@]}" -e "s#__KAFKA_BROKERS__#$q#" -e "s#__AWS_REGION__#$AWS_REGION#" \
    -e "s#__BIND__#$TELEGRAF_BIND#" -e "s#__TRAP_PORT__#$TRAP_PORT#" -e "s#__HEALTH_PORT__#$HEALTH_PORT#" "$TEMPLATE" > "$CONF"
  echo "$CONF を作った（sink: ${SINK}${q:+ / brokers: $KAFKA_BROKERS}${auth}${ins}）"
}

case "${1:-run}" in
  run)
    render
    exec telegraf --config "$CONF"
    ;;
  render) render ;;
  test | gnmi)
    echo "tg $1 は cycle 013 でやめた（取りにいく側の telegraf-dialin ごと）。gNMI を 1 回取って見るのは gnmic のタスクの gn get（stream の output の gnmic_exec_command）" >&2
    exit 1
    ;;
  *) sed -n '2,4p' "$0"; exit 1 ;;
esac
