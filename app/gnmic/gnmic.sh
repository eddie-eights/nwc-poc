#!/bin/sh
# gnmic のコンテナ（IaC/terraform/aws-managed/pipeline/stream の ECS。docker/images/gnmic/Dockerfile）の入口。イメージの /usr/local/bin/gn。
#   gn run     （既定。ECS が起こす）設定を作って gnmic の subscribe を起こす
#   gn render  設定を作るだけ（中身を見る。tests/test_stream.py も手元でこれを回す）
#   gn get [パス ...]  （ECS Exec でタスクに入って打つ。コマンドは stream の output gnmic_exec_command）状態を get で 1 回取り、Kafka と同じ event の形で標準出力に出す
# 機器の gNMI を購読して Kafka の gnmi / metrics トピックへ書く（cycle 013。それまでは Telegraf の inputs.gnmi = telegraf-dialin）。イメージ（alpine）に bash が無いので POSIX sh で書く
set -eu
TEMPLATE=${GNMIC_TEMPLATE:-/etc/gnmic/gnmic.yaml.in}
# 書くのは /tmp だけ（ユーザーは nobody）
CONF=${GNMIC_CONF:-/tmp/gnmic.yaml}
# Kafka の認証。scram = MSK の SASL/SCRAM-SHA-512（既定。9096、TLS。KAFKA_SASL_USER / KAFKA_SASL_PASS が要る）/ none = 認証なしの PLAINTEXT（OSS 版の ECS の Kafka と手元の compose）。
# gnmic.yaml.in の「>>> kafka_auth scram」の区間
KAFKA_AUTH=${KAFKA_AUTH:-scram}

die() { echo "$*" >&2; exit 1; }

render() {
  # ECS のタスク定義の環境変数を埋めて $CONF を作る:
  #   KAFKA_BROKERS  Kafka のブローカー（host:port をカンマで。MSK は SASL/SCRAM の口の 9096、OSS 版は kafka-N.<名前空間>:9092）
  #   GNMI_TARGETS   購読先（"<IP>:57400", ...。cycle 013 より前の telegraf-dialin と同じ形）。ops/up.sh が lab の定義から作る（app/containerlab/lab_topology.py --gnmi-targets）。
  #                  target の名前は IP（event の tags.source。Spark が device map で機器名 sysName を引く）
  #   KAFKA_AUTH     上の既定
  #   GNMI_USERNAME / GNMI_PASSWORD  機器の認証情報。KAFKA_SASL_USER / KAFKA_SASL_PASS  MSK の SCRAM の資格情報（KAFKA_AUTH=scram のとき）。
  #                  ここでは埋めない（値を /tmp の設定に書かない）。gnmic.yaml.in が ${...} で持ち、gnmic が設定を読むときに環境変数から入れる。
  #                  ここでは有るかだけ見る（値は出さない）
  : "${KAFKA_BROKERS:?}"
  gnmi=${GNMI_TARGETS:-}
  # 形が崩れていると gnmic が起きないか、違う相手に繋ぎにいくので、決まった形だけ通す
  printf '%s' "$gnmi" | grep -Eq '^"[0-9.]+:[0-9]+"(, *"[0-9.]+:[0-9]+")*$' \
    || die "GNMI_TARGETS が無いか形が違う（ops/up.sh が lab の定義から作って stream の gnmi_targets に渡す）: ${gnmi}"
  printf '%s' "$KAFKA_BROKERS" | grep -Eq '^[A-Za-z0-9.-]+:[0-9]+(,[A-Za-z0-9.-]+:[0-9]+)*$' || die "KAFKA_BROKERS の形が違う: ${KAFKA_BROKERS}"
  # 無いと gnmic は空の名前で機器や MSK に繋ぎにいって認証で落ち続けるので、起こす前に止める
  [ -n "${GNMI_USERNAME:-}" ] && [ -n "${GNMI_PASSWORD:-}" ] || die "GNMI_USERNAME / GNMI_PASSWORD が無い（stream の ECS は SSM の SecureString を secrets で受ける。ops/up.sh が作る）"
  case "$KAFKA_AUTH" in
    scram)
      [ -n "${KAFKA_SASL_USER:-}" ] && [ -n "${KAFKA_SASL_PASS:-}" ] \
        || die "KAFKA_SASL_USER / KAFKA_SASL_PASS が無い（stream の ECS は Secrets Manager の AmazonMSK_<接頭辞>-gnmic を secrets で受ける。ops/up.sh が作る）"
      # 区間の印の行だけ消す
      drop='/^# [<>]\{3\} kafka_auth scram/d' auth='SASL/SCRAM-SHA-512'
      ;;
    none)
      # 認証なしの PLAINTEXT。sasl と tls ごと消す
      drop='/^# >>> kafka_auth scram/,/^# <<< kafka_auth scram/d' auth='none'
      ;;
    *) die "KAFKA_AUTH は scram か none: ${KAFKA_AUTH}" ;;
  esac
  # target の塊を別のファイルに書き、ひな形の __TARGETS__ の行と差し替える。名前は IP、address は GNMI_TARGETS の host:port のまま。
  # username / password は target ごとに ${...} で書く（gnmic.yaml.in の注記）。同じ IP が 2 回あると YAML のキーが重なって gnmic が起きないので止める
  tfile="$CONF.targets"
  : > "$tfile"
  n=0 seen=' '
  for t in $(printf '%s' "$gnmi" | tr -d '" ' | tr ',' ' '); do
    ip=${t%:*}
    case "$seen" in *" $ip "*) rm -f "$tfile"; die "GNMI_TARGETS に同じ IP が 2 回ある: ${ip}" ;; esac
    seen="$seen$ip "
    n=$((n + 1))
    printf '  "%s":\n    address: "%s"\n    username: "${GNMI_USERNAME}"\n    password: "${GNMI_PASSWORD}"\n' "$ip" "$t" >> "$tfile"
  done
  sed -e "$drop" -e "s#__KAFKA_BROKERS__#${KAFKA_BROKERS}#" -e "/^__TARGETS__\$/r $tfile" -e '/^__TARGETS__$/d' "$TEMPLATE" > "$CONF"
  rm -f "$tfile"
  echo "$CONF を作った（gnmi: ${n} 台 ${gnmi} / brokers: ${KAFKA_BROKERS} / topics: gnmi, metrics / kafka auth: ${auth}）"
}

case "${1:-run}" in
  run)
    render
    exec /app/gnmic --config "$CONF" subscribe
    ;;
  render) render ;;
  get)
    # subscribe は設定の outputs（Kafka）に書くので、見るだけのときは get にする（outputs を使わない）。形は Kafka と同じ event（format: event）。
    # パスを渡さなければ状態の 4 つ（interface_state / bgp_neighbor / isis_interface の購読と同じパス）。機器に届かないときは lab の EC2 の forward-status を見る
    shift
    render >&2
    [ $# -gt 0 ] || set -- '/interface[name=*]/oper-state' '/interface[name=*]/admin-state' \
      '/network-instance[name=default]/protocols/bgp/neighbor[peer-address=*]/session-state' \
      '/network-instance[name=default]/protocols/isis/instance[name=main]/interface[interface-name=*]/oper-state'
    # パスの [ ] を glob にしないよう、引数の並びのまま --path を挟む
    n=$#
    for p in "$@"; do set -- "$@" --path "$p"; done
    shift "$n"
    exec /app/gnmic --config "$CONF" get --type STATE --format event "$@"
    ;;
  *) sed -n '2,5p' "$0"; exit 1 ;;
esac
