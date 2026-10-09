#!/bin/sh
# Grafana のコンテナ（docker/images/grafana/Dockerfile）の入口。選んだ格納先のデータソースとダッシュボード、アラートの定義だけを /tmp に並べてから、上流の /run.sh を起こす。
# ECS のタスク定義の環境変数（IaC/terraform/aws-managed/pipeline/analytics の grafana.tf）:
#   PROMETHEUS_URL    AMP のワークスペース（https://aps-workspaces.<region>.amazonaws.com/workspaces/<id>）。空なら Prometheus を出さない（STORES に grafana が無い）
#   OPENSEARCH_URL    logs コレクションのエンドポイント。空なら OpenSearch を出さない（STORES に grafana が無い）
#   OPENSEARCH_INDEX  ログの index（app/spark/snmp_sinks.py の OPENSEARCH_INDEX）
#   ALERTS_TOPIC_ARN  アラートを publish する SNS のトピック（IaC/terraform/aws-managed/base/core の alerts.tf）。これがあるときだけアラートの定義（provisioning/alerting）を
#                     並べる: 送り先（nwc.yaml）と、データソースがあるほうのルール（nwc-prometheus.yaml / nwc-opensearch.yaml）
#   AWS_REGION
#   PROMETHEUS_AUTH / OPENSEARCH_AUTH  OSS 版（cycle 005。IaC/terraform/oss）だけ none / basic。データソースを datasources-oss の定義にする
#                     （VictoriaMetrics の vmselect に署名なし / 自前の OpenSearch に Basic 認証。uid は同じ amp / aoss-logs なので、
#                     ダッシュボードとアラートのルールはそのまま）。無ければ sigv4（マネージド版）
#   OPENSEARCH_USER   OPENSEARCH_AUTH=basic のときのユーザー（既定 admin）。パスワード OPENSEARCH_PASSWORD は ECS が SSM の SecureString から入れる
# 管理者のパスワード GF_SECURITY_ADMIN_PASSWORD は ECS が SSM の SecureString から入れる（値はここでもログでも出さない）
set -eu
SRC=/etc/grafana/nwc
DST=/tmp/grafana-provisioning
PROM_DS="$SRC/datasources"
OS_DS="$SRC/datasources"
case "${PROMETHEUS_AUTH:-sigv4}" in
  sigv4) ;;
  none) PROM_DS="$SRC/datasources-oss" ;;
  *) echo "PROMETHEUS_AUTH は sigv4 / none のどれか: ${PROMETHEUS_AUTH}" >&2; exit 1 ;;
esac
case "${OPENSEARCH_AUTH:-sigv4}" in
  sigv4) ;;
  basic) OS_DS="$SRC/datasources-oss"; export OPENSEARCH_USER="${OPENSEARCH_USER:-admin}" ;;
  *) echo "OPENSEARCH_AUTH は sigv4 / basic のどれか: ${OPENSEARCH_AUTH}" >&2; exit 1 ;;
esac
rm -rf "$DST" /tmp/grafana-dashboards
mkdir -p "$DST/datasources" "$DST/dashboards" "$DST/plugins" "$DST/alerting" /tmp/grafana-dashboards
cp "$SRC/dashboards/nwc.yaml" "$DST/dashboards/"
if [ -n "${PROMETHEUS_URL:-}" ]; then
  cp "$PROM_DS/prometheus.yaml" "$DST/datasources/"
  cp "$SRC/dashboards/metrics.json" /tmp/grafana-dashboards/
  if [ -n "${ALERTS_TOPIC_ARN:-}" ]; then
    cp "$SRC/alerting/nwc-prometheus.yaml" "$DST/alerting/"
  fi
fi
if [ -n "${OPENSEARCH_URL:-}" ]; then
  cp "$OS_DS/opensearch.yaml" "$DST/datasources/"
  cp "$SRC/dashboards/logs.json" /tmp/grafana-dashboards/
  cp "$SRC/dashboards/flows.json" /tmp/grafana-dashboards/
  if [ -n "${ALERTS_TOPIC_ARN:-}" ]; then
    cp "$SRC/alerting/nwc-opensearch.yaml" "$DST/alerting/"
  fi
fi
if [ -n "${ALERTS_TOPIC_ARN:-}" ] && [ -n "${PROMETHEUS_URL:-}${OPENSEARCH_URL:-}" ]; then
  cp "$SRC/alerting/nwc.yaml" "$DST/alerting/"
fi
export GF_PATHS_PROVISIONING="$DST"
echo "provisioning: $(ls "$DST/datasources" /tmp/grafana-dashboards "$DST/alerting" | tr '\n' ' ')"
exec /run.sh "$@"
