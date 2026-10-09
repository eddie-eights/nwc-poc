# syslog-ng（AxoSyslog）のイメージ（cycle 012）。機器の syslog を受けて Kafka の logs トピックへ書く（app/syslog-ng/syslog-ng.conf.in）。
# 公式イメージに設定のテンプレートと入口（app/syslog-ng/syslog-ng.sh = /usr/local/bin/sng）を足すだけ。閉域の ECS は外から設定を取れないので焼き込む。
# 版の正は ops/up-common.sh の SYSLOG_NG_VERSION（この ARG の既定値も同じ値にしてある。変えるときは両方を変える）。kafka-c() のモジュールと arm64 は 4.29.0 で確かめた
ARG SYSLOG_NG_VERSION=4.29.0
FROM ghcr.io/axoflow/axosyslog:${SYSLOG_NG_VERSION}
COPY syslog-ng.conf.in /etc/syslog-ng/
COPY --chmod=0755 syslog-ng.sh /usr/local/bin/sng
# 公式イメージは root で動く。待つのは 1024 以上のポートだけなので nobody で動かす（書くのは /tmp だけ）
USER 65534:65534
# 公式イメージの HEALTHCHECK は既定の制御ソケットを見るので、sng run が置く /tmp の制御ソケットへ向け直す（間隔は公式のまま）
HEALTHCHECK --interval=120s --timeout=5s --start-period=30s CMD ["/usr/sbin/syslog-ng-ctl", "healthcheck", "--timeout", "5", "--control", "/tmp/syslog-ng.ctl"]
ENTRYPOINT ["/usr/local/bin/sng"]
CMD ["run"]
