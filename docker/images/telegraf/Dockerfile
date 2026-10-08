# Telegraf のコンテナ（IaC/terraform/aws-managed/pipeline/stream の ECS。Fargate の arm64）。ops/up.sh の手順 2 がビルドして ECR の <接頭辞>-telegraf に置く。
# デバッグ用の EC2（ops/lab-debug.sh）は同じ Dockerfile を linux/amd64 でビルドする（x86_64 の lab と同じホストに載るため。リポジトリは <接頭辞>-debug-telegraf）。
# 上流の公式イメージに、設定のテンプレート（telegraf.conf.in）、lab の gNMI を共通の形に変える Starlark（lab_*.star）と
# 入口（telegraf.sh → /usr/local/bin/tg）を足すだけ。
# 上流の /entrypoint.sh は root で起きて setpriv で telegraf に落とすが、ここでは初めから telegraf（非 root）で起こす。
# 非 root は 1024 未満のポートで待てないので、trap は 1162 で受ける（NLB が 162 を 1162 に向ける。IaC/terraform/aws-managed/pipeline/stream の telegraf.tf）。
# 受け口は trap 1162/udp と outputs.health 8080/tcp（syslog は syslog-ng、MDT は外した。cycle 012）
ARG TELEGRAF_VERSION=1.40.1
FROM telegraf:${TELEGRAF_VERSION}

COPY telegraf.conf.in lab_gnmi.star lab_circuits.star /etc/telegraf/
COPY --chmod=0755 telegraf.sh /usr/local/bin/tg

USER telegraf
ENTRYPOINT ["/usr/local/bin/tg"]
CMD ["run"]
