#!/usr/bin/env bash
# MSK のトピック metrics / gnmi へ kafka-producer-perf-test でレコードを流す（後段の負荷試験用。中身は trex/README.md の「Kafka へ流す」）。
#   kafka_load.sh metrics|gnmi [件数] [件/秒]   （lab の EC2 で root。既定は 100000 件を 1000 件/秒。件/秒に -1 で上限なし）
# metrics と gnmi は gnmic（app/gnmic）が機器の gNMI を購読して書くもので UDP ではないので、TRex では作れない。gnmic が MSK に書く形
# （format: event、split-events で 1 メッセージ 1 件、ナノ秒の timestamp。Spark の read_rows が Telegraf の形に読み替える）のレコードをここで作り、
# apache/kafka のイメージの kafka-producer-perf-test で撃つ。event の形は gnmic のソースから組んだもの（実物ではない。cycle 013 の design.md の未確定 1）。
# MSK は IAM 認証（SASL_SSL、9098）なので aws-msk-iam-auth の jar をイメージの libs に差し込み、認証はこの EC2 のインスタンスプロファイルで通す。
# 環境（/etc/*-lab.env か呼び出し側）:
#   AWS_REGION     必須
#   BOOTSTRAP      MSK の bootstrap（SASL/IAM の口）。無ければ SSM の $PARAM_PREFIX/msk-bootstrap を読む
#   KAFKA_IMAGE    既定 apache/kafka:4.3.1（oss/ops/oss-images.sh の OSS_KAFKA_TAG と同じ版）
#   IAM_JAR        aws-msk-iam-auth の all の jar のこのホストでの置き場。既定 /opt/nwc-trex/aws-msk-iam-auth-2.3.9-all.jar
# 動かして確かめていない（このサイクルでは書くだけ。負荷試験のときに、README の「前提」の 3 つを揃えてから回す）。
set -euo pipefail
SELF=$(readlink -f "$0")
cd "$(dirname "$SELF")"
# user_data が書く env（キーは ../setup.sh の頭）。1 つ目だけ読む
for ENV_FILE in /etc/*-lab.env; do
  # shellcheck source=/dev/null
  [ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }
  break
done

TOPIC=${1:-}
case "$TOPIC" in metrics | gnmi) ;; *) sed -n '2,3p' "$SELF"; exit 1 ;; esac
RECORDS=${2:-100000}
THROUGHPUT=${3:-1000}
KAFKA_IMAGE=${KAFKA_IMAGE:-apache/kafka:4.3.1}
IAM_JAR=${IAM_JAR:-/opt/nwc-trex/aws-msk-iam-auth-2.3.9-all.jar}
: "${AWS_REGION:?}"
if [ -z "${BOOTSTRAP:-}" ]; then
  : "${PARAM_PREFIX:?BOOTSTRAP か PARAM_PREFIX が要る}"
  BOOTSTRAP=$(aws ssm get-parameter --region "$AWS_REGION" --name "$PARAM_PREFIX/msk-bootstrap" --query Parameter.Value --output text)
fi
[ -f "$IAM_JAR" ] || { echo "$IAM_JAR が無い（aws-msk-iam-auth の all の jar。README の「前提」）" >&2; exit 1; }

# lab の SR Linux（splab.clab.yml.in の kind nokia_srlinux）の名前と管理 IP。レコードの tags.source に IP を使う（機器名は Spark が device map で足す）
nodes() { awk '/^    [a-z0-9-]+:$/ { n = $1; sub(":", "", n) } /kind: nokia_srlinux/ { k = n } /mgmt-ipv4:/ && n == k { print n, $2 }' ../splab.clab.yml.in; }

# 1 行 1 レコード（kafka-producer-perf-test の --payload-file は改行で区切り、毎回どれか 1 行を選ぶ）。timestamp は作った時刻で固定
payload() {
  local now ip i
  now=$(date +%s)000000000
  while read -r _ ip; do
    if [ "$TOPIC" = metrics ]; then
      # gnmic の interface_stats（app/gnmic/gnmic.yaml.in。sample 60 秒。機器ごとに ethernet-1/1〜1/3）
      for i in 1 2 3; do
        printf '{"name":"interface_stats","timestamp":%s,"tags":{"interface_name":"ethernet-1/%d","source":"%s","subscription-name":"interface_stats"},"values":{"/interface/statistics/in-octets":"%d","/interface/statistics/out-octets":"%d","/interface/statistics/in-error-packets":"0","/interface/statistics/out-error-packets":"0"}}\n' \
          "$now" "$i" "$ip" $((RANDOM * 1000)) $((RANDOM * 1000))
      done
    else
      # gnmic の bgp_neighbor（on-change。established なので Splunk の netops_gnmi と Grafana の bgp_down は発火しない）
      printf '{"name":"bgp_neighbor","timestamp":%s,"tags":{"network-instance_name":"default","neighbor_peer-address":"10.255.0.1","source":"%s","subscription-name":"bgp_neighbor"},"values":{"/network-instance/protocols/bgp/neighbor/session-state":"established"}}\n' \
        "$now" "$ip"
    fi
  done < <(nodes)
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cat > "$work/client.properties" <<'EOF'
security.protocol=SASL_SSL
sasl.mechanism=AWS_MSK_IAM
sasl.jaas.config=software.amazon.msk.auth.iam.IAMLoginModule required;
sasl.client.callback.handler.class=software.amazon.msk.auth.iam.IAMClientCallbackHandler
EOF
payload > "$work/payload.txt"
[ -s "$work/payload.txt" ] || { echo "レコードを作れなかった（../splab.clab.yml.in に SR Linux が無い）" >&2; exit 1; }
echo "$TOPIC へ $RECORDS 件（${THROUGHPUT} 件/秒。レコード $(wc -l < "$work/payload.txt") 種）を $BOOTSTRAP に流す"

# --network host: インスタンスプロファイルの認証情報（IMDS）と MSK へ、この EC2 のまま出る
docker run --rm --network host -e AWS_REGION \
  -v "$work:/work:ro" -v "$IAM_JAR:/opt/kafka/libs/aws-msk-iam-auth-all.jar:ro" \
  "$KAFKA_IMAGE" /opt/kafka/bin/kafka-producer-perf-test.sh \
  --topic "$TOPIC" --num-records "$RECORDS" --throughput "$THROUGHPUT" --payload-file /work/payload.txt \
  --producer-props bootstrap.servers="$BOOTSTRAP" acks=all linger.ms=10 --producer.config /work/client.properties
