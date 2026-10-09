# gnmic のイメージ（cycle 013）。機器の gNMI を購読して Kafka の gnmi / metrics トピックへ書く（app/gnmic/gnmic.yaml.in）。
# 公式イメージに設定のテンプレートと入口（app/gnmic/gnmic.sh = /usr/local/bin/gn）を足すだけ。閉域の ECS は外から設定を取れないので焼き込む。context は app/gnmic/。
# 公式イメージは alpine（/bin/sh と /etc/ssl/certs/ca-certificates.crt あり、bash なし）、入口は /app/gnmic、linux/amd64 と linux/arm64（0.49.0 で確かめた）
ARG GNMIC_VERSION=0.49.0
FROM ghcr.io/openconfig/gnmic:${GNMIC_VERSION}
COPY gnmic.yaml.in /etc/gnmic/
COPY --chmod=0755 gnmic.sh /usr/local/bin/gn
# 待ち受けるポートは無い（取りにいくだけ）。nobody で動かす（書くのは /tmp だけ）
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/gn"]
CMD ["run"]
