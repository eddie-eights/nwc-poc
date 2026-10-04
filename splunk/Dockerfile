# Splunk Enterprise のコンテナ（terraform/pipeline/analytics の ECS。Fargate の x86_64）。ops/up.sh の手順 2 が SINK_SPLUNK=1 のときビルドして
# ECR の <接頭辞>-splunk に置く。上流の公式イメージ（splunk/splunk。amd64 だけ）に、アラートの app（netops_alerts）と入口（entrypoint.sh）と、
# app が SNS へ publish するのに使う boto3 を足す。
# 上流は etc を /opt/splunk-etc に持ち、起動のとき /opt/splunk/etc（VOLUME）へ写すので、app は /opt/splunk-etc/apps に置く
# （/opt/splunk/etc に COPY しても VOLUME に隠れる）。
# boto3 は app の lib/ に入れる（Splunk の Python に pip で入れない。app を消せば一緒に消える）。この公開リポジトリには置かず、1 段目で PyPI から取る。
# 1 段目はビルドする PC の CPU の python イメージで動かし（$BUILDPLATFORM）、boto3 と依存は純粋な Python（*-none-any の wheel）なので CPU を問わない。
# 2 段目（amd64）は COPY だけ = arm64 の PC でも QEMU 無しでビルドできる（--platform linux/amd64）。
# PYTHON_VERSION は Splunk の Python（10.4.3 は 3.13.11）と、netops_alerts/default/alert_actions.conf の python.required と揃える。
# botocore のデータ（全サービスの API 定義。約 27 MB）は SNS の分と、どのサービスでも読む直下のファイル（endpoints.json・partitions.json など）だけ残す。
# Splunkbase の add-on（Splunk Add-on for AWS など）は入れていない（配布物をこの公開リポジトリに置けず、VPC から Splunkbase へも出られない）
ARG SPLUNK_VERSION=10.4.3
ARG PYTHON_VERSION=3.13

FROM --platform=$BUILDPLATFORM python:${PYTHON_VERSION}-slim AS lib
# boto3 と botocore は同じ版で出る。依存も版を固定する（boto3 を上げるときは一緒に見直す）
ARG BOTO3_VERSION=1.43.108
# 置き場は /out（/lib は Debian ではシステムのライブラリ = /usr/lib へのリンク。そこへ入れると OS のライブラリごと COPY してしまう）
RUN pip install --no-cache-dir --no-compile --disable-pip-version-check --root-user-action=ignore --only-binary=:all: --target /out \
      "boto3==${BOTO3_VERSION}" "botocore==${BOTO3_VERSION}" jmespath==1.1.0 python-dateutil==2.9.0.post0 s3transfer==0.19.2 six==1.17.0 urllib3==2.8.0 \
 && rm -rf /out/bin \
 && find /out/botocore/data -mindepth 1 -maxdepth 1 -type d ! -name sns -exec rm -rf {} + \
 && ! grep -h '^Tag:' /out/*.dist-info/WHEEL | grep -v -- '-none-any$' \
 && ! find /out -name '*.so' | grep .

FROM splunk/splunk:${SPLUNK_VERSION}

COPY --chown=splunk:splunk netops_alerts /opt/splunk-etc/apps/netops_alerts
COPY --from=lib --chown=splunk:splunk /out /opt/splunk-etc/apps/netops_alerts/lib
COPY --chmod=0755 entrypoint.sh /sbin/nwc-entrypoint.sh

# ENTRYPOINT を替えると上流の CMD は消えるので書き直す
ENTRYPOINT ["/sbin/nwc-entrypoint.sh"]
CMD ["start-service"]
