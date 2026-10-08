#!/usr/bin/env bash
# 手元の compose を止める。-v を付けると volume（Kafka・OpenSearch・Prometheus・Splunk・Grafana・Spark の checkpoint）も消す
#   docker/compose/down.sh [-v]
set -euo pipefail
cd "$(dirname "$0")"
docker compose down "$@"
