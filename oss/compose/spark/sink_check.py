"""005 ステップ 1 の 5: EMR を使わない Spark 3.5（spark-submit --master local[2]）で、spark/snmp_sinks.py の読み方と送り方のまま
compose の Kafka から読み、OpenSearch と vminsert へ書けるか。

snmp_sinks.py は MSK の IAM 認証と SigV4（AMP / OpenSearch Serverless）に決め打ちなので、ここだけ差し替える:
  - read_rows: readStream の kafka.security.* / kafka.sasl.* の 4 つを捨てる（compose の Kafka は PLAINTEXT）
  - ensure_topics: KAFKA_IAM_PROPS を空にする
  - 送り役: SigV4 の代わりに、OpenSearch は basic 認証と自己署名の TLS、vminsert は認証なし。中身（opensearch_docs、
    prometheus_series、encode_write_request、snappy_compress、http_post）は snmp_sinks.py のものをそのまま使う
Iceberg は手元の Hadoop カタログ（file://）に iceberg_query のまま書く。S3 Tables は AWS に届かないネットワークの中なので、
jar のクラスが読めるかと、カタログを開いたときにどこで止まるかだけを見る。

使い方: oss/compose/check-spark.sh から spark-submit で起こす（compose の spark コンテナの中。spark/ は /opt/check、ここは /opt/oss）。
コンテナの Python は 3.10（apache/spark:3.5.9-java17-python3）なので、f 文字列の {} の中にバックスラッシュを書かない
"""
import base64
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/opt/check")
import snmp_sinks as S  # noqa: E402

BOOTSTRAP = "kafka-1:9092,kafka-2:9092,kafka-3:9092"
OPENSEARCH = "https://opensearch-1:9200"
OS_AUTH = "Basic " + base64.b64encode(b"admin:NwcOss-Trial-2026!").decode()   # compose.yaml の試し用の値
VMINSERT = "http://vminsert:8480/insert/0/prometheus/api/v1/write"
VMSELECT = "http://vmselect:8481/select/0/prometheus/api/v1/query"
WORK = "/tmp/oss-check/"
LOCAL_TABLE = "local.nwc.raw_telemetry"
DUMMY_TABLE_BUCKET = "arn:aws:s3tables:ap-northeast-1:000000000000:bucket/nwc-oss-dummy"   # 無いバケット。AWS には届かない
TLS = ssl._create_unverified_context()   # compose の OpenSearch は自己署名の証明書（試しだけ。ECS では検証する）

DEVICES = ("dc1-leaf-01", "dc1-leaf-02", "dc1-spine-01", "dc1-spine-02")
IFS = ("ethernet-1/1", "ethernet-1/2", "ethernet-1/3", "ethernet-1/4", "ethernet-1/49")
MINUTES = 5
N_TRAPS, N_LOGS = 50, 20


def log(msg):
    print(f"[sink_check] {msg}", flush=True)


# ---------------------------------------------------------------- snmp_sinks.py の差し替え
class _Reader:
    """DataStreamReader の写し。MSK の IAM 認証の option だけ捨てる"""
    dropped = []

    def __init__(self, r):
        self.r = r

    def format(self, f):
        return _Reader(self.r.format(f))

    def option(self, k, v):
        if k.startswith(("kafka.security.", "kafka.sasl.")):
            _Reader.dropped.append(k)
            return self
        return _Reader(self.r.option(k, v))

    def load(self):
        return self.r.load()


class _Spark:
    def __init__(self, spark):
        self.spark = spark

    @property
    def readStream(self):  # noqa: N802 - SparkSession と同じ名前
        return _Reader(self.spark.readStream)


def opensearch_sender(records):
    url = f"{OPENSEARCH}/{S.OPENSEARCH_INDEX}/_bulk"
    lines = S.opensearch_docs(records)
    dropped = 0
    for i in range(0, len(lines), S.BULK_SIZE * 2):
        body = ("\n".join(lines[i:i + S.BULK_SIZE * 2]) + "\n").encode("utf-8")
        status, text = S.http_post(url, body, {"Content-Type": "application/x-ndjson", "Authorization": OS_AUTH}, TLS)
        res = json.loads(text) if status < 400 else {}
        if status >= 400 or res.get("errors"):
            failed = [it["index"] for it in res.get("items", []) if it.get("index", {}).get("error")] if status < 400 else lines[i::2]
            log(f"opensearch: {status} で {len(failed)} 件が入らなかった: {str(failed[0])[:300] if failed else text[:300]!r}")
            dropped += len(failed)
    return dropped


def vminsert_sender(records):
    headers = {"Content-Type": "application/x-protobuf", "Content-Encoding": "snappy", "X-Prometheus-Remote-Write-Version": "0.1.0"}
    series = S.prometheus_series(records)
    dropped = 0
    for i in range(0, len(series), S.BULK_SIZE):
        status, text = S.http_post(VMINSERT, S.snappy_compress(S.encode_write_request(series[i:i + S.BULK_SIZE])), headers)
        if status >= 400:
            log(f"vminsert: {status} で {len(series[i:i + S.BULK_SIZE])} サンプルを捨てた: {text[:200]!r}")
            dropped += len(series[i:i + S.BULK_SIZE])
    return dropped


# ---------------------------------------------------------------- 入れるデータ（Telegraf の JSON の形）
def messages(now):
    """[(topic, value)]。metrics は 4 台 × 5 IF × 5 分（ifHCInOctets と ifOperStatus の 2 field）と bgp_neighbor の状態 4 件、
    traps は snmp_trap 50 件、logs は device_log 20 件"""
    out = []
    t0 = now - 10 * 60
    for d_i, dev in enumerate(DEVICES):
        for f_i, ifn in enumerate(IFS):
            for m in range(MINUTES):
                out.append(("metrics", {"name": "interface", "timestamp": t0 + m * 60,
                                        "tags": {"agent_host": f"172.20.20.{11 + d_i}", "host": "telegraf", "sysName": dev, "ifName": ifn},
                                        "fields": {"ifHCInOctets": 1000 * (m + 1) + f_i, "ifOperStatus": 1}}))
        out.append(("metrics", {"name": "bgp_neighbor", "timestamp": t0, "tags": {"agent_host": f"172.20.20.{11 + d_i}", "host": "telegraf",
                                                                                  "sysName": dev, "neighbor": "10.0.0.1"},
                                "fields": {"session_state": "established"}}))
    for k in range(N_TRAPS):
        out.append(("traps", {"name": "snmp_trap", "timestamp": t0 + k, "tags": {"source": f"172.20.20.{11 + k % 4}", "name": "linkDown",
                                                                                 "mib": "IF-MIB", "oid": ".1.3.6.1.6.3.1.1.5.3", "host": "telegraf"},
                              "fields": {"ifIndex": k % 5 + 1, "sysUpTimeInstance": 1000 + k}}))
    for k in range(N_LOGS):
        out.append(("logs", {"name": "device_log", "timestamp": t0 + k, "tags": {"sysName": DEVICES[k % 4], "severity": "warning",
                                                                                 "appname": "sr_linux", "host": "telegraf"},
                             "fields": {"message": f"test message {k}", "severity_code": 4}}))
    return [(t, json.dumps(v, separators=(",", ":"))) for t, v in out]


# ---------------------------------------------------------------- 確かめるための読み出し
def os_count():
    req = urllib.request.Request(f"{OPENSEARCH}/{S.OPENSEARCH_INDEX}/_count", headers={"Authorization": OS_AUTH})
    try:
        with urllib.request.urlopen(req, timeout=10, context=TLS) as r:
            return json.load(r)["count"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 0
        raise


def vm_scalar(expr):
    with urllib.request.urlopen(VMSELECT + "?" + urllib.parse.urlencode({"query": expr, "nocache": "1"}), timeout=10) as r:
        res = json.load(r)["data"]["result"]
    return float(res[0]["value"][1]) if res else 0.0


def wait(what, fn, limit=180):
    end = time.time() + limit
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last is True:
                log(f"ok: {what}")
                return True
        except Exception as e:  # noqa: BLE001 - 待つあいだの一時的な失敗
            last = repr(e)[:200]
        time.sleep(2)
    log(f"タイムアウト: {what}（最後: {last}）")
    return False


def check_classes(spark):
    loader = spark._jvm.java.lang.Thread.currentThread().getContextClassLoader()
    for name in ("org.apache.spark.sql.kafka010.KafkaSourceProvider",
                 "org.apache.iceberg.spark.SparkCatalog",
                 "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
                 "org.apache.iceberg.rest.RESTCatalog",
                 "org.apache.iceberg.aws.s3.S3FileIO",
                 "org.apache.iceberg.aws.RESTSigV4AuthManager",
                 "software.amazon.s3tables.iceberg.S3TablesCatalog",
                 # S3 Tables のカタログの jar は AWS SDK を自分の名前の下に入れて（shade して）いる。素の software.amazon.awssdk.services.s3tables は無い
                 "software.amazon.s3tables.shaded.awssdk.services.s3tables.S3TablesClient"):
        try:
            loader.loadClass(name)
            log(f"  クラス {name}: 読めた")
        except Exception as e:  # noqa: BLE001 - py4j の例外
            log(f"  クラス {name}: 読めない（{str(e).splitlines()[0][:200]}）")


def consumer_groups(spark):
    """Kafka のコンシューマーグループと、そこに commit された offset（Kafbat UI が lag に使うもの）"""
    jvm = spark._jvm
    props = jvm.java.util.Properties()
    props.put("bootstrap.servers", BOOTSTRAP)
    admin = jvm.org.apache.kafka.clients.admin.AdminClient.create(props)
    try:
        groups = [g.groupId() for g in admin.listConsumerGroups().all().get().toArray()]
        log(f"  コンシューマーグループ: {len(groups)} 個")
        for g in groups:
            offsets = admin.listConsumerGroupOffsets(g).partitionsToOffsetAndMetadata().get()
            log(f"    {g}: commit された offset {offsets.size()} 件")
    finally:
        admin.close()


def first_line(e):
    text = str(e)
    for line in text.splitlines():
        if "Exception" in line or "Error" in line:
            return line.strip()[:400]
    return text.strip().splitlines()[0][:400] if text.strip() else repr(e)


def main():
    from pyspark.sql import SparkSession

    spark = (SparkSession.builder.appName("oss_sink_check")
             # EMR Serverless の job の設定（terraform/pipeline/analytics/outputs.tf）と同じ Iceberg の拡張
             .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
             # 手元の Iceberg（Hadoop カタログ。ファイルはコンテナの /tmp）
             .config("spark.sql.catalog.local", "org.apache.iceberg.spark.SparkCatalog")
             .config("spark.sql.catalog.local.type", "hadoop")
             .config("spark.sql.catalog.local.warehouse", "file://" + WORK + "warehouse")
             # S3 Tables（いまの EMR と同じ。S3TablesCatalog）
             .config("spark.sql.catalog.s3tables", "org.apache.iceberg.spark.SparkCatalog")
             .config("spark.sql.catalog.s3tables.catalog-impl", "software.amazon.s3tables.iceberg.S3TablesCatalog")
             .config("spark.sql.catalog.s3tables.warehouse", DUMMY_TABLE_BUCKET)
             # S3 Tables の Iceberg REST の入口（AWS の文書の「Accessing tables using the Amazon S3 Tables Iceberg REST endpoint」の形）
             .config("spark.sql.catalog.s3rest", "org.apache.iceberg.spark.SparkCatalog")
             .config("spark.sql.catalog.s3rest.type", "rest")
             .config("spark.sql.catalog.s3rest.uri", "https://s3tables.ap-northeast-1.amazonaws.com/iceberg")
             .config("spark.sql.catalog.s3rest.warehouse", DUMMY_TABLE_BUCKET)
             .config("spark.sql.catalog.s3rest.rest.sigv4-enabled", "true")
             .config("spark.sql.catalog.s3rest.rest.signing-name", "s3tables")
             .config("spark.sql.catalog.s3rest.rest.signing-region", "ap-northeast-1")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    log(f"Spark {spark.version}、Java {spark._jvm.java.lang.System.getProperty('java.version')}、Python {sys.version.split()[0]}")

    log("== jar のクラス")
    check_classes(spark)

    log("== トピック（ensure_topics。KAFKA_IAM_PROPS を空にして PLAINTEXT）")
    S.KAFKA_IAM_PROPS.clear()
    topics = S.METRIC_TOPICS.split(",") + S.LOG_TOPICS.split(",")
    log(f"  作った: {S.ensure_topics(spark, BOOTSTRAP, topics)}")

    log("== Kafka に入れる（Spark のバッチの Kafka 書き込み）")
    msgs = messages(int(time.time()) // 60 * 60)
    (spark.createDataFrame(msgs, "topic string, value string")
     .write.format("kafka").option("kafka.bootstrap.servers", BOOTSTRAP).save())
    n_metrics = sum(1 for t, _ in msgs if t == "metrics")
    log(f"  metrics {n_metrics} 件、traps {N_TRAPS} 件、logs {N_LOGS} 件")
    want_series = len(DEVICES) * len(IFS) * 2 + len(DEVICES)
    want_samples = len(DEVICES) * len(IFS) * MINUTES * 2 + len(DEVICES)

    log("== Iceberg（手元の Hadoop カタログ。tables.tf と同じ列で作り、ensure_iceberg_columns で 4 列を足す）")
    spark.sql("CREATE NAMESPACE IF NOT EXISTS local.nwc")
    spark.sql(f"CREATE TABLE IF NOT EXISTS {LOCAL_TABLE} (ts timestamp NOT NULL, topic string NOT NULL, measurement string, agent_host string, "
              "host string, tags_json string, fields_json string, ingested_at timestamp NOT NULL) USING iceberg")
    log(f"  足した列: {S.ensure_iceberg_columns(spark, LOCAL_TABLE)}")

    log("== ストリーミングのクエリを起こす（snmp_sinks の http_query と iceberg_query。トリガーは 5 秒に縮める）")
    S.TRIGGER = "5 seconds"
    wrapped = _Spark(spark)
    ckpt = WORK + "checkpoint/"
    queries = [
        S.http_query(S.read_rows(wrapped, BOOTSTRAP, S.sink_topics("opensearch", S.METRIC_TOPICS, S.LOG_TOPICS)), "opensearch", ckpt, opensearch_sender),
        S.http_query(S.read_rows(wrapped, BOOTSTRAP, S.sink_topics("prometheus", S.METRIC_TOPICS, S.LOG_TOPICS)), "prometheus", ckpt, vminsert_sender),
        S.iceberg_query(S.read_rows(wrapped, BOOTSTRAP, S.sink_topics("iceberg", S.METRIC_TOPICS, S.LOG_TOPICS)), LOCAL_TABLE, ckpt),
    ]
    log(f"  read_rows から捨てた option: {sorted(set(_Reader.dropped))}")

    want_docs = N_TRAPS + N_LOGS
    wait(f"OpenSearch の {S.OPENSEARCH_INDEX} に {want_docs} 件", lambda: os_count() == want_docs)
    log(f"  OpenSearch の件数: {os_count()}")
    series_q = 'count(last_over_time({__name__=~"snmp_.+"}[1h]))'
    samples_q = 'sum(count_over_time({__name__=~"snmp_.+"}[1h]))'
    bgp_q = 'sum(last_over_time(snmp_bgp_neighbor_session_up[1h]))'
    wait(f"vmselect で {want_series} 系列、{want_samples} サンプル",
         lambda: vm_scalar(series_q) == want_series and vm_scalar(samples_q) == want_samples)
    log(f"  vmselect: 系列 {vm_scalar(series_q):.0f}、サンプル {vm_scalar(samples_q):.0f}、BGP の session_up {vm_scalar(bgp_q):.0f}")
    want_rows = len(msgs)
    wait(f"Iceberg の表に {want_rows} 行", lambda: spark.table(LOCAL_TABLE).count() == want_rows)
    log(f"  Iceberg: {spark.table(LOCAL_TABLE).count()} 行、topic ごと {sorted((r[0], r[1]) for r in spark.table(LOCAL_TABLE).groupBy('topic').count().collect())}、"
        f"event_id の null {spark.table(LOCAL_TABLE).where('event_id IS NULL').count()} 行")

    log("== Spark の読んだ位置はどこにあるか（Kafbat UI の lag に出るか）")
    consumer_groups(spark)
    offsets_dir = ckpt + "opensearch/offsets/"
    last = sorted((f for f in os.listdir(offsets_dir) if f.isdigit()), key=int)[-1]
    with open(offsets_dir + last, encoding="utf-8") as f:
        log(f"  checkpoint の offsets/{last} の最後の行: {f.read().splitlines()[-1]}")

    for q in queries:
        q.stop()

    log("== S3 Tables のカタログを開く（AWS に届かないネットワーク。資格情報も渡していない）")
    for cat in ("s3tables", "s3rest"):
        started = time.time()
        try:
            spark.sql(f"SHOW NAMESPACES IN {cat}").collect()
            log(f"  {cat}: 開けた（想定外）")
        except Exception as e:  # noqa: BLE001 - どこで止まるかを見る
            log(f"  {cat}: {time.time() - started:.0f} 秒で止まった: {first_line(e)}")
    log("== 終わり")
    spark.stop()


if __name__ == "__main__":
    main()
