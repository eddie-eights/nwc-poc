#!/bin/sh
# syslog-ng（AxoSyslog）のコンテナ（IaC/terraform/aws-managed/pipeline/stream の ECS。docker/images/syslog-ng/Dockerfile）の入口。イメージの /usr/local/bin/sng。
#   sng run     （既定。ECS が起こす）設定を作って syslog-ng を起こす
#   sng render  設定を作るだけ（中身を見る。tests/test_collectors.py も手元でこれを回す）
# 機器の syslog を受けて Kafka の logs トピックへ書く（cycle 012。それまでは Telegraf の inputs.syslog）。イメージ（Alpine）に bash が無いので POSIX sh で書く
set -eu
TEMPLATE=${SYSLOG_NG_TEMPLATE:-/etc/syslog-ng/syslog-ng.conf.in}
# 書くのは /tmp だけ（ユーザーは nobody。イメージの /var/lib/syslog-ng は root しか書けない）。persist / pid / control も同じ場所に置く
CONF=${SYSLOG_NG_CONF:-/tmp/syslog-ng.conf}
RUN_DIR=${CONF%/*}
# Kafka の認証。scram = MSK の SASL/SCRAM-SHA-512（既定。9096、TLS。KAFKA_SASL_USER / KAFKA_SASL_PASS が要る）/ none = 認証なしの PLAINTEXT（OSS 版の ECS の Kafka と手元の compose）。
# syslog-ng.conf.in の「>>> kafka_auth scram」の区間
KAFKA_AUTH=${KAFKA_AUTH:-scram}
# 機器の syslog の形式。RFC3164 = BSD 形式（本番の Cisco IOS の既定。既定）/ RFC5424 = 新しい形式（lab の SR Linux。ops/up.sh と docker/compose/compose.yaml が渡す）。
# 形式が違うと項目がきれいに取れない（RFC5424 を RFC3164 で読むと、版の 1 と時刻が本文に入る）
SYSLOG_STANDARD=${SYSLOG_STANDARD:-RFC3164}
# 受け口のポート（udp と、NLB のヘルスチェックが開く tcp。app/containerlab/lab.sh の LOG_PORT と同じ）
LOG_PORT=${LOG_PORT:-5140}
# 受け口の IPv4 アドレス。空（既定）は全部のインターフェース（ECS）。手元の compose は docker/compose/up.sh が lab の管理ネットの GW（203.0.113.1）を渡す
SYSLOG_BIND=${SYSLOG_BIND:-}

die() { echo "$*" >&2; exit 1; }

render() {
  # ECS のタスク定義の環境変数（IaC/terraform/aws-managed/pipeline/stream の collectors.tf）を埋めて $CONF を作る:
  #   KAFKA_BROKERS    Kafka のブローカー（host:port をカンマで。MSK は SASL/SCRAM の口の 9096、OSS 版は kafka-N.<名前空間>:9092）
  #   KAFKA_AUTH / SYSLOG_STANDARD / LOG_PORT / SYSLOG_BIND  上の既定
  #   KAFKA_SASL_USER / KAFKA_SASL_PASS  MSK の SCRAM の資格情報（KAFKA_AUTH=scram のとき）。ここでは埋めない（値を /tmp の設定に書かない）。
  #                    syslog-ng.conf.in が `名前` で持ち、syslog-ng が設定を読むときに環境変数から入れる。ここでは有るかと文字の種類だけ見る（値は出さない）
  : "${KAFKA_BROKERS:?}"
  # sed で埋めるので、決まった形だけ通す（ポートは 1〜65535 の数字、アドレスは空か IPv4）
  case "$LOG_PORT" in ''|0*|*[!0-9]*) die "LOG_PORT は 1〜65535 の数字: ${LOG_PORT}" ;; esac
  [ "$LOG_PORT" -le 65535 ] || die "LOG_PORT は 1〜65535 の数字: ${LOG_PORT}"
  if [ -n "$SYSLOG_BIND" ] && ! printf '%s' "$SYSLOG_BIND" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
    die "SYSLOG_BIND は空（全部のインターフェース）か IPv4 のアドレス: ${SYSLOG_BIND}"
  fi
  printf '%s' "$KAFKA_BROKERS" | grep -Eq '^[A-Za-z0-9.-]+:[0-9]+(,[A-Za-z0-9.-]+:[0-9]+)*$' || die "KAFKA_BROKERS の形が違う: ${KAFKA_BROKERS}"
  # RFC5424 は flags(syslog-protocol) で読み、Telegraf と同じく fields.version（= 1）を出す。RFC3164 は flags 無しで、version の行を消す
  case "$SYSLOG_STANDARD" in
    RFC5424) flags='flags(syslog-protocol)' version='s#__VERSION_PAIR__#fields.version=int64(1)#' ;;
    RFC3164) flags='' version='/__VERSION_PAIR__/d' ;;
    *) die "SYSLOG_STANDARD は RFC3164 か RFC5424: ${SYSLOG_STANDARD}" ;;
  esac
  case "$KAFKA_AUTH" in
    scram)
      # 無いと syslog-ng は空の名前で MSK に繋ぎに行って認証で落ち続けるので、起こす前に止める。
      # 値は設定の "..." の中にそのまま入るので、" \ ` や空白で文字列が壊れないよう、英数字と ._~+/=@- だけ通す（ops/up.sh が作る値は英数字だけ）
      for v in KAFKA_SASL_USER KAFKA_SASL_PASS; do
        eval "val=\${$v:-}"
        [ -n "$val" ] || die "$v が無い（stream の ECS は Secrets Manager の AmazonMSK_<接頭辞>-collectors を secrets で受ける。ops/up.sh が作る）"
        case "$val" in *[!A-Za-z0-9._~+/=@-]*) die "$v に使えない文字がある（英数字と ._~+/=@- だけ。値は出さない）" ;; esac
      done
      # 区間の印の行だけ消す
      drop='/^# [<>]\{3\} kafka_auth scram/d' auth='SASL/SCRAM-SHA-512'
      ;;
    none)
      # 認証なしの PLAINTEXT。config() ごと消す
      drop='/^# >>> kafka_auth scram/,/^# <<< kafka_auth scram/d' auth='none'
      ;;
    *) die "KAFKA_AUTH は scram か none: ${KAFKA_AUTH}" ;;
  esac
  sed -e "$drop" -e "$version" -e "s#__KAFKA_BROKERS__#${KAFKA_BROKERS}#" -e "s#__BIND__#${SYSLOG_BIND:-0.0.0.0}#" -e "s#__LOG_PORT__#${LOG_PORT}#" \
    -e "s#__SYSLOG_FLAGS__#${flags}#" "$TEMPLATE" > "$CONF"
  echo "$CONF を作った（syslog: ${SYSLOG_BIND:-0.0.0.0}:${LOG_PORT}/udp+tcp ${SYSLOG_STANDARD} / brokers: ${KAFKA_BROKERS} / topic: logs / kafka auth: ${auth}）"
}

case "${1:-run}" in
  run)
    render
    # -F = 前で動く（ECS はこのプロセスを見る）。--no-caps = nobody なので capability を触らない（1024 以上のポートしか待たない）
    exec /usr/sbin/syslog-ng -F --no-caps -f "$CONF" --persist-file "$RUN_DIR/syslog-ng.persist" --pidfile "$RUN_DIR/syslog-ng.pid" --control "$RUN_DIR/syslog-ng.ctl"
    ;;
  render) render ;;
  *) sed -n '2,4p' "$0"; exit 1 ;;
esac
