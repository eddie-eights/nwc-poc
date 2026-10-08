"""Kafka（マネージド版は MSK の IAM 認証、OSS 版は PLAINTEXT。KAFKA_AUTH で切り替え）のトピックを読み、選んだ格納先に流し続ける Spark Structured Streaming のジョブ（Kafka の 4 分岐）。

EMR Serverless の上で動く（IaC/terraform/aws-managed/pipeline/analytics）。起動は ops/up.sh の a-3（start-job-run）で、引数は IaC/terraform/aws-managed/pipeline/analytics の
output job_driver_json_<iceberg|splunk|http> が組み立てる（--bootstrap / --checkpoint / --sinks と、格納先ごとの --iceberg-table などの値）。
Kafka と S3 Tables の jar、カタログの設定は spark-submit の --conf で渡す。

格納先は 4 つ（--sinks にカンマ区切り）:
  iceberg     全トピック → S3 Tables（Iceberg）のテーブルに append（履歴の正本）
  opensearch  ログのトピックだけ → OpenSearch Serverless（TIMESERIES 型のコレクション）の _bulk に SigV4 で POST
  prometheus  メトリクスのトピックだけ → Amazon Managed Service for Prometheus の remote write に SigV4 で POST（数値の field だけ）
  splunk      全トピック → Splunk の HTTP Event Collector（HEC）に 1 行 1 イベントで POST（Authorization: Splunk <token>。
              token は SSM の SecureString（--splunk-token-parameter）から起動時に読む。送り先は analytics の ECS の Splunk Enterprise
              （https://splunk.<prefix>.internal:8088、自己署名なので --splunk-skip-verify。VPC の中。2026-09-28 から AWS の外の Splunk へは送らない）。
              2026-09-26 まで MSK Connect の Splunk Connect for Kafka にする予定だったが、Spark から直接書くことにした）
どのトピックがメトリクスでどれがログかは --metric-topics / --log-topics（既定は Telegraf の metrics と traps,logs。
logs は機器の syslog。SR Linux が lab の EC2 へ送り、lab の EC2 が Telegraf（ECS）の内部 NLB へ DNAT する）。格納先ごとに別のストリーミングクエリ（別の Kafka の購読と checkpoint）に
する。1 つが止まったらジョブを 1 で終わらせ、EMR Serverless に起こし直させる（どのクエリも checkpoint の続きから読む）。

Telegraf の JSON 出力（outputs.kafka の data_format = "json"、json_timestamp_units = "1s"）は
  {"fields": {…}, "name": "<measurement>", "tags": {"agent_host": "…", "host": "…", …}, "timestamp": <秒>}
の形。列に分けるのは timestamp / name / agent_host / host だけで、tags と fields は JSON 文字列のまま入れる
（機器やメトリクスが増えてもテーブルの列を変えないため。IaC/terraform/aws-managed/pipeline/analytics/tables.tf の列と同じ）。

gnmic（cycle 013。機器の gNMI を購読する。app/gnmic）の event（format: event。split-events で 1 メッセージ 1 件）は
  {"name": "<subscription の名前>", "timestamp": <ナノ秒>, "tags": {"source": "<機器の IP>", "interface_name": "…", …}, "values": {"/interface/oper-state": "…"}}
の形（消えたときは values が無く deletes だけ）。read_rows が形で見分けて（fields が無く、values か deletes がある）Telegraf の形に読み替える
（トピックでは分けない。gnmi と metrics のどちらに来ても同じ）: timestamp は秒、name は GNMI_MEASUREMENTS で measurement に、tags のキーは接頭辞（…:）を落として
GNMI_TAGS で名前を替え、values のキーは最後の要素の接頭辞を落として - を _ に（/interface/oper-state → oper_state）、agent_host の列は tags.source。
deletes だけの event は捨てる。gnmic_message が同じ読み替えを Python で書いたもの（tests/test_stream.py が縛る）。読み替えた形がどの格納先にも入る（S3 Tables も）。

どの行にも一意の番号 event_id を付ける: Kafka のメッセージの value（from_json の前のバイト列そのまま）の SHA-256 の 16 進 64 文字（F.sha2）。
中身から作るので、Spark のやり直しで同じメッセージを送り直しても、Telegraf が同じメッセージを Kafka に 2 回入れても同じ値になり、読む側で重複を落とせる
（ここでは dropDuplicates しない）。Telegraf の timestamp は秒（json_timestamp_units = "1s"）なので、同じ秒に同じ中身の別の出来事も同じ event_id になる（受け入れる）。
元のメッセージを追うための Kafka の位置（kafka_topic / kafka_partition / kafka_offset）も別の項目で入れる（送り直しは別の offset になるので、重複の判定には使わない）。
入れるのは iceberg（列。tables.tf の列のあとに、ジョブが起動時に ALTER TABLE で足す。ICEBERG_ADDED_COLUMNS）、opensearch（ドキュメントの項目）、
splunk（event の項目）。prometheus には入れない（ラベルにすると 1 サンプルごとに別の系列になる）。

異常の検知はここではしない（2026-10-02 にやめた。detect のクエリと Neptune の anomaly 頂点、S3 Tables の anomaly_events、EventBridge への put_events を消した）。
検知と相関は格納先の側でする: Grafana のアラートルール（AMP の gNMI の IF / BGP / IS-IS、OpenSearch の trap。
app/grafana/provisioning/alerting）と Splunk の保存済みサーチ（gNMI の IF / BGP / IS-IS と trap。app/splunk/netops_alerts）が同じ 4 種類を
SNS のトピック <接頭辞>-alerts に出し、ワークフロー（SQS）とトポロジの status（graph の Lambda）がそれを受ける（cycle 002 で両方に揃えた。
IF の up / down は cycle 013 で SNMP のポーリングの ifOperStatus から gNMI の oper-state に替えた）。
そのために格納先に合わせた整形だけはここでする（Kafka の生データと S3 Tables へ書くものは、上の gnmic の読み替えのほかは変えない）:
  prometheus  文字列の状態を 1 / 0 の系列にする（STATE_FIELDS。interface の oper_state → oper_up と admin_state → admin_up、
              bgp_neighbor の session_state → session_up、isis_interface の oper_state → oper_up）
  prometheus / opensearch / splunk  sysName の無いレコード（gNMI と trap。source が機器の管理 IP）に、--device-map で引いた機器名を sysName として足す
                           （opensearch は表に無い機器でも source の IP をそのまま sysName にする。Grafana の trap のルールが tags.sysName で束ねるため。
                           splunk は cycle 013 から。Grafana と同じ機器名にして、アラートの anomaly_id（device#kind#target）を両方で揃える）

HTTP の送信は既定で driver でまとめて行う（マイクロバッチを collect する。PoC の量（機器数台、10 秒間隔）なら 1 分に数百行）。
量が増えたら --http-send executor で foreachPartition に切り替える（集めずに、パーティションごとに executor が送る）。remote write の protobuf と snappy は外部ライブラリ無しで組む
（EMR Serverless の Python に protobuf / python-snappy は無い。snappy は「全部リテラル」の圧縮で規格上正しい）。

Kafka は 1 回のトリガー（60 秒）に 1 つのクエリが 10000 件まで読む（--max-offsets-per-trigger。格納先ごとに --max-offsets-per-trigger-by-sink で変えられ、0 で上限なし）。
止めていたジョブを起こし直した直後や最初に earliest から読むときに、溜まった分を 1 回で読んで driver のメモリ（2g）に collect しないため。

OSS 版（cycle 005。IaC/terraform/oss）は同じジョブを ECS の Spark（local[*]）で動かし、接続の認証だけを環境変数で切り替える（無ければ上のマネージド版のまま）:
  KAFKA_AUTH=none           Kafka（KRaft の自前のクラスタ）に PLAINTEXT で繋ぐ（SG で絞る。既定 iam は MSK の IAM 認証）
  OPENSEARCH_AUTH=basic     OpenSearch の _bulk に Basic 認証で POST（OPENSEARCH_USER（既定 admin）/ OPENSEARCH_PASSWORD。既定 sigv4 は aoss の署名）
  PROMETHEUS_AUTH=none      remote write を署名せずに POST（VictoriaMetrics の vminsert。--prometheus-url は /insert/0/prometheus/api/v1/write。既定 sigv4 は aps の署名）
  SPLUNK_HEC_TOKEN          splunk の HEC の token。あれば SSM を読まずにこれを使い、--splunk-token-parameter は要らない
                            （Splunk のタスクが HEC の token を作るのと同じ SSM の SecureString。無ければマネージド版のまま SSM から読む）
パスワードと token は送るたびに環境変数から読む（executor へ運ぶ sender に値を持たせない。ECS のタスク定義の secrets で SSM の SecureString から渡す）。
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request

METRIC_TOPICS = "metrics,gnmi,mdt"   # metrics = Telegraf の inputs.snmp と lab の gNMI を変えた共通の形、gnmi = inputs.gnmi、mdt = inputs.cisco_telemetry_mdt（app/telegraf/telegraf.conf.in。Telegraf（ECS）で動く）
LOG_TOPICS = "traps,logs"   # traps = Telegraf の inputs.snmp_trap、logs = inputs.syslog（機器の syslog。measurement は device_log）
SINKS = ("iceberg", "opensearch", "prometheus", "splunk")
# 接続の認証（先頭が既定 = マネージド版。OSS 版は環境変数で後ろの方にする。モジュールの docstring）
KAFKA_AUTHS = ("iam", "none")
OPENSEARCH_AUTHS = ("sigv4", "basic")
PROMETHEUS_AUTHS = ("sigv4", "none")
HTTP_SEND = ("driver", "executor")   # HTTP の格納先（opensearch / prometheus / splunk）へ送る所。--http-send（既定 driver）
# Kafka の 1 回のトリガーに 1 つのクエリが読む件数の上限（全パーティションの合計。Spark の maxOffsetsPerTrigger）。0 なら付けない（上限なし）。
# ふだんの 1 回分（60 秒）より十分大きくして、いつもは何も抑えない。効くのは止めていたジョブを起こし直した直後と、最初に earliest から読むとき
# （HTTP の格納先は既定で 1 回分を driver（2g）に collect するので、その量を抑える）。--max-offsets-per-trigger と、格納先ごとの --max-offsets-per-trigger-by-sink
MAX_OFFSETS_PER_TRIGGER = 10000
TRIGGER = "60 seconds"
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3        # 5xx と接続エラーだけ打ち直す。4xx は捨ててログに出す（古すぎるサンプルなどは何度打っても通らない）
BULK_SIZE = 500         # 1 回の POST に載せる行数
OPENSEARCH_INDEX = "snmp-logs"
METRIC_PREFIX = "snmp"
SPLUNK_HEC_PATH = "/services/collector/event"   # HEC の JSON イベントの入口（--splunk-hec-url に無ければ足す）
SPLUNK_SOURCETYPE_PREFIX = "netops"             # sourcetype は netops:<トピック>（netops:metrics / netops:traps / netops:logs）
# 文字列の状態 → 1 / 0（Prometheus は数値しか持てない。Grafana の bgp_down / isis_down のルールが読む）。
# (measurement, field) → (系列の field 名, 1 になる値)。値は大文字小文字を見ない。表に無い文字列の field は今までどおり捨てる
STATE_FIELDS = {
    ("bgp_neighbor", "session_state"): ("session_up", "established"),
    ("isis_interface", "oper_state"): ("oper_up", "up"),
    # gnmic の interface_state（cycle 013。Grafana の link_down が snmp_interface_oper_up と snmp_interface_admin_up を読む）
    ("interface", "oper_state"): ("oper_up", "up"),
    ("interface", "admin_state"): ("admin_up", "enable"),
}
# gnmic の event を Telegraf の形に読み替える表（read_rows と gnmic_message が同じ表を使う。cycle 013。モジュールの docstring）。
# event の name（subscription の名前）→ measurement。表に無い名前（bgp_neighbor / isis_interface / system）はそのまま
GNMI_MEASUREMENTS = {"interface_state": "interface", "interface_stats": "interface"}
# tags のキー（接頭辞「…:」を落としたもの）→ Telegraf のころの名前（Grafana / Splunk のルールとダッシュボードが読む）。表に無いタグ（source、subscription-name …）はそのまま
GNMI_TAGS = {"interface_name": "ifName", "neighbor_peer-address": "peer_address", "interface_interface-name": "interface_name", "control_slot": "slot"}
GNMI_NS = 1000000000   # event の timestamp はナノ秒。割って小数を切り、秒（Telegraf の timestamp と同じ単位）にする（double の割り算。read_rows と gnmic_message で同じ）

# S3 Tables の表に、ジョブが起動時に足す列（tables.tf の列のあとに、この順。名前と Spark SQL の型）。tables.tf の schema には書かない:
# aws provider（6.64）の aws_s3tables_table は metadata の schema を変えると表を作り直す（RequiresReplace）ので、いまある行が消える。
# ALTER TABLE ADD COLUMNS は Iceberg のメタデータだけの変更で、いまある行はこの列が null のまま読める
ICEBERG_ADDED_COLUMNS = (("event_id", "string"), ("kafka_topic", "string"), ("kafka_partition", "int"), ("kafka_offset", "bigint"))


# ---------------------------------------------------------------- 引数
def parse_args(argv):
    p = argparse.ArgumentParser(prog="snmp_sinks.py", description=__doc__.split("\n")[0])
    p.add_argument("--bootstrap", required=True, help="Kafka の bootstrap servers（マネージド版は MSK の SASL/IAM の 9098、OSS 版（KAFKA_AUTH=none）は kafka-N の 9092）")
    p.add_argument("--checkpoint", required=True, help="checkpoint の親（s3://<バケット>/analytics/checkpoint/。格納先ごとに下にディレクトリを切る）")
    p.add_argument("--sinks", required=True, help="格納先（カンマ区切り。iceberg / opensearch / prometheus / splunk）")
    p.add_argument("--http-send", choices=HTTP_SEND, default="driver", help="HTTP の格納先へ送る所。driver = マイクロバッチを collect して driver が送る（既定）、"
                                                                         "executor = foreachPartition でパーティションごとに executor が送る")
    p.add_argument("--max-offsets-per-trigger", type=offsets_limit, default=MAX_OFFSETS_PER_TRIGGER,
                   help=f"Kafka の 1 回のトリガーに 1 つのクエリが読む件数の上限（全パーティションの合計。既定 {MAX_OFFSETS_PER_TRIGGER}。0 で上限なし）")
    p.add_argument("--max-offsets-per-trigger-by-sink", type=offsets_by_sink, default={},
                   help="格納先ごとの上限（<格納先>=<件数> のカンマ区切り。例 splunk=2000,prometheus=5000。書いた格納先だけ --max-offsets-per-trigger より優先し、0 はその格納先だけ上限なし）")
    p.add_argument("--region", default="ap-northeast-1", help="SigV4 のリージョン")
    p.add_argument("--metric-topics", default=METRIC_TOPICS, help="メトリクスのトピック（カンマ区切り。iceberg と prometheus が読む）")
    p.add_argument("--log-topics", default=LOG_TOPICS, help="ログのトピック（カンマ区切り。iceberg と opensearch が読む）")
    p.add_argument("--iceberg-table", default="", help="iceberg: catalog.namespace.table")
    p.add_argument("--opensearch-endpoint", default="", help="opensearch: コレクションのエンドポイント（https://…）")
    p.add_argument("--opensearch-index", default=OPENSEARCH_INDEX, help="opensearch: インデックス名")
    p.add_argument("--prometheus-url", default="", help="prometheus: remote write の URL（…/api/v1/remote_write）")
    p.add_argument("--splunk-hec-url", default="", help="splunk: HEC の URL（https://<host>:8088。/services/collector/event が無ければ足す）")
    p.add_argument("--splunk-token-parameter", default="", help="splunk: HEC の token を入れた SSM の SecureString の名前（/<接頭辞>/splunk/hec-token。値は起動時に読み、ログに出さない。"
                                                                 "環境変数 SPLUNK_HEC_TOKEN があれば要らない）")
    p.add_argument("--splunk-index", default="", help="splunk: イベントを入れる index（空なら token の既定の index）")
    p.add_argument("--splunk-skip-verify", action="store_true", help="splunk: HEC の TLS 証明書を検証しない（自己署名の Splunk Enterprise の検証用。既定は検証する）")
    p.add_argument("--device-map", default="", help="prometheus / opensearch / splunk: sysName の無いレコードの source（機器の管理 IP）を機器名に引く表"
                                                     "（別名=機器名,…。Splunk の DEVICE_MAP と同じ。app/containerlab/lab_topology.py --device-map。"
                                                     "表に無いとき prometheus と splunk は足さず、opensearch は source をそのまま sysName にする）")
    args = p.parse_args(argv)
    args.sinks = [s.strip() for s in args.sinks.split(",") if s.strip()]
    bad = [s for s in args.sinks if s not in SINKS]
    if bad or not args.sinks:
        p.error(f"--sinks は {', '.join(SINKS)} のどれか（カンマ区切り）: {args.sinks}")
    need = {"iceberg": ["iceberg_table"], "opensearch": ["opensearch_endpoint"], "prometheus": ["prometheus_url"],
            "splunk": ["splunk_hec_url", "splunk_token_parameter"]}
    for s in args.sinks:
        for k in need[s]:
            # OSS 版は token を環境変数 SPLUNK_HEC_TOKEN で受ける（splunk_token）ので、SSM のパラメータ名は要らない
            if k == "splunk_token_parameter" and os.environ.get("SPLUNK_HEC_TOKEN"):
                continue
            if not getattr(args, k):
                p.error(f"--sinks に {s} があるので --{k.replace('_', '-')} が要る")
    if not args.checkpoint.endswith("/"):
        args.checkpoint += "/"
    for k in ("metric_topics", "log_topics"):
        setattr(args, k, ",".join(t.strip() for t in getattr(args, k).split(",") if t.strip()))
        if not getattr(args, k):
            p.error(f"--{k.replace('_', '-')} が空")
    return args


def offsets_limit(text):
    """--max-offsets-per-trigger の値: 0 以上の整数（0 = 上限なし）"""
    if not re.fullmatch(r"\d+", text.strip()):
        raise argparse.ArgumentTypeError(f"0 以上の整数（0 で上限なし）: {text!r}")
    return int(text)


def offsets_by_sink(text):
    """--max-offsets-per-trigger-by-sink の値（splunk=2000,prometheus=5000）を {格納先: 件数} にする。空なら {}"""
    out = {}
    for item in (i.strip() for i in text.split(",")):
        if not item:
            continue
        sink, eq, value = item.partition("=")
        if not eq or sink.strip() not in SINKS:
            raise argparse.ArgumentTypeError(f"<格納先>=<件数> のカンマ区切り（格納先は {', '.join(SINKS)}）: {item!r}")
        out[sink.strip()] = offsets_limit(value)
    return out


def max_offsets(args, sink):
    """その格納先のクエリの maxOffsetsPerTrigger（格納先ごとの値があればそれ、無ければ共通の値。0 = 付けない）"""
    return args.max_offsets_per_trigger_by_sink.get(sink, args.max_offsets_per_trigger)


def sink_topics(sink, metric_topics, log_topics):
    """格納先が購読する Kafka のトピック（カンマ区切り）。iceberg / splunk は全部、prometheus はメトリクス、opensearch はログ"""
    if sink in ("iceberg", "splunk"):
        return ",".join(dict.fromkeys(metric_topics.split(",") + log_topics.split(",")))
    if sink == "prometheus":
        return metric_topics
    if sink == "opensearch":
        return log_topics
    raise ValueError(sink)


def env_choice(name, choices):
    """環境変数 name の値（小文字）。無いか空なら choices[0]（マネージド版）、choices に無い値は ValueError（綴り違いで黙ってマネージド版にしない）"""
    v = (os.environ.get(name) or choices[0]).strip().lower()
    if v not in choices:
        raise ValueError(f"{name} は {' / '.join(choices)} のどれか: {v!r}")
    return v


def basic_auth_header():
    """OPENSEARCH_AUTH=basic の Authorization。OPENSEARCH_PASSWORD が無ければ ValueError（認証なしで送って 401 を捨て続けない）"""
    password = os.environ.get("OPENSEARCH_PASSWORD") or ""
    if not password:
        raise ValueError("OPENSEARCH_AUTH=basic なのに OPENSEARCH_PASSWORD が無い")
    user = os.environ.get("OPENSEARCH_USER") or "admin"
    return "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------- 行の形（Kafka → 列）
def read_rows(spark, bootstrap, topics, max_offsets_per_trigger=0):
    """Kafka の topics（カンマ区切り）を読んで tables.tf の列にした DataFrame を返す（テストでは start せずに中身だけ見る）。
    max_offsets_per_trigger が 0 でなければ、1 回のトリガーに読む件数をそこで抑える（Kafka の maxOffsetsPerTrigger）"""
    from pyspark.sql import functions as F
    from pyspark.sql import types as T

    # Telegraf の JSON のうち、列に分ける部分だけ型を書く。tags / fields は文字列のまま。
    # values / deletes は gnmic の event（fields の代わりに values を持ち、消えたときは deletes だけ。cycle 013）を見分けて読み替えるため
    schema = T.StructType([
        T.StructField("timestamp", T.LongType()),
        T.StructField("name", T.StringType()),
        T.StructField("tags", T.MapType(T.StringType(), T.StringType())),
        T.StructField("fields", T.MapType(T.StringType(), T.StringType())),
        T.StructField("values", T.MapType(T.StringType(), T.StringType())),
        T.StructField("deletes", T.ArrayType(T.StringType())),
    ])
    reader = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap)
        .option("subscribe", topics)
        .option("startingOffsets", "earliest")
    )
    if env_choice("KAFKA_AUTH", KAFKA_AUTHS) == "none":
        # OSS 版の KRaft のクラスタ（IaC/terraform/oss/pipeline/stream）。暗号化も認証も無く、SG で Spark と Telegraf だけに絞る
        reader = reader.option("kafka.security.protocol", "PLAINTEXT")
    else:
        reader = (
            reader
            # MSK の IAM 認証（aws-msk-iam-auth の -all jar。AWS ドキュメント「Connect to MSK with IAM」の 4 項目）
            .option("kafka.security.protocol", "SASL_SSL")
            .option("kafka.sasl.mechanism", "AWS_MSK_IAM")
            .option("kafka.sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;")
            .option("kafka.sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler")
        )
    if max_offsets_per_trigger:
        reader = reader.option("maxOffsetsPerTrigger", str(max_offsets_per_trigger))
    raw = reader.load()
    value = F.col("value").cast("string")
    msg = F.from_json(value, schema)
    # gnmic の event は形で見分け、Telegraf の形（timestamp / name / tags / fields）の struct に読み替える（gnmic_struct。gnmic_message と同じ表）。
    # Telegraf の行も同じ 4 つの項目の struct にする（F.when の両側は同じ型でなければならない）
    gnmic = msg["fields"].isNull() & (msg["values"].isNotNull() | msg["deletes"].isNotNull())
    telegraf = F.struct(*(msg[k].alias(k) for k in ("timestamp", "name", "tags", "fields")))
    parsed = raw.select(
        F.col("topic"),
        F.when(gnmic, gnmic_struct(F, msg)).otherwise(telegraf).alias("m"),
        # agent_host の列: gnmic は機器の IP（tags.source = target の名前）、Telegraf は tags.agent_host（trap と syslog は無い）
        F.when(gnmic, msg["tags"]["source"]).otherwise(msg["tags"]["agent_host"]).alias("agent_host"),
        # 一意の番号は from_json の前の value（binary のまま）から作る。同じバイト列なら、いつ何度読んでも同じ値（モジュールの docstring）
        F.sha2(F.col("value"), 256).alias("event_id"),
        F.col("partition").alias("kafka_partition"),
        F.col("offset").alias("kafka_offset"),
    )
    rows = parsed.select(
        F.to_timestamp(F.from_unixtime(F.col("m.timestamp"))).alias("ts"),
        F.col("topic"),
        F.col("m.name").alias("measurement"),
        F.col("agent_host"),
        F.col("m.tags")["host"].alias("host"),
        F.to_json(F.col("m.tags")).alias("tags_json"),
        F.to_json(F.col("m.fields")).alias("fields_json"),
        F.current_timestamp().alias("ingested_at"),
        # ここから ICEBERG_ADDED_COLUMNS（同じ順）
        F.col("event_id"),
        F.col("topic").alias("kafka_topic"),
        F.col("kafka_partition"),
        F.col("kafka_offset"),
    ).where(F.col("ts").isNotNull())
    return rows


def gnmic_struct(F, msg):
    """read_rows の gnmic の分岐: from_json した event の列 msg を、Telegraf の形（timestamp 秒 / name / tags / fields）の struct にする
    （gnmic_message と同じ読み替え）。values の無い event（deletes だけ）は timestamp を null にして、read_rows の where で捨てる。
    読み替えたキーが重なったら後勝ち（build が spark.sql.mapKeyDedupPolicy=LAST_WIN にする。既定の EXCEPTION ではクエリが落ちる）"""
    def strip(c):   # 接頭辞（srl_nokia-…:）を落とす
        return F.regexp_replace(c, "^.*:", "")

    def lookup(table, c):   # 表にあれば替え、無ければそのまま（when を連ねる）
        out = None
        for k, v in table.items():
            out = F.when(c == k, v) if out is None else out.when(c == k, v)
        return out.otherwise(c)

    return F.struct(
        F.when(msg["values"].isNotNull(), (msg["timestamp"] / GNMI_NS).cast("long")).alias("timestamp"),
        lookup(GNMI_MEASUREMENTS, msg["name"]).alias("name"),
        F.transform_keys(msg["tags"], lambda k, v: lookup(GNMI_TAGS, strip(k))).alias("tags"),
        F.transform_keys(msg["values"], lambda k, v: F.translate(strip(F.element_at(F.split(k, "/"), -1)), "-", "_")).alias("fields"),
    )


def gnmic_message(m):
    """gnmic の event 1 件（dict）を Telegraf の形（timestamp / name / tags / fields）にする。read_rows が Spark の列で組むもの（gnmic_struct）と
    同じ読み替え（GNMI_MEASUREMENTS / GNMI_TAGS。tests/test_stream.py が event で確かめる）。値は文字列（Spark の from_json の StringType と同じく、
    文字列でない値は JSON の字面。null は null のまま）。読み替えたキーが重なったら後勝ち（build の mapKeyDedupPolicy=LAST_WIN と同じ）。
    values の無い event（消えたときの deletes だけ）と、timestamp が整数でないものは None（read_rows では ts が null で捨てる）。
    Telegraf の形（fields がある）は読み替えない（read_rows はそのまま通す）ので、ここには渡さない"""
    def text(v):
        return v if v is None or isinstance(v, str) else json.dumps(v, separators=(",", ":"), ensure_ascii=False)

    def strip(k):
        return re.sub(r"^.*:", "", k)

    ts, values = m.get("timestamp"), m.get("values")
    if not isinstance(values, dict) or not isinstance(ts, int) or isinstance(ts, bool):
        return None
    name = m.get("name")
    return {
        "timestamp": int(ts / GNMI_NS),
        "name": GNMI_MEASUREMENTS.get(name, name),
        "tags": {GNMI_TAGS.get(strip(k), strip(k)): text(v) for k, v in (m.get("tags") or {}).items()},
        "fields": {strip(k.split("/")[-1]).replace("-", "_"): text(v) for k, v in values.items()},
    }


def row_to_record(row):
    """Spark の Row（tables.tf の列）を、HTTP の格納先が使う辞書にする。ts は epoch 秒（float）"""
    d = row.asDict() if hasattr(row, "asDict") else dict(row)
    ts = d.get("ts")
    if isinstance(ts, dt.datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
        epoch = ts.timestamp()
    else:
        epoch = float(ts)
    return {
        "ts": epoch,
        "topic": d.get("topic"),
        "measurement": d.get("measurement"),
        "agent_host": d.get("agent_host"),
        "host": d.get("host"),
        "tags": _loads(d.get("tags_json")),
        "fields": _loads(d.get("fields_json")),
        "event_id": d.get("event_id"),
        "kafka_topic": d.get("kafka_topic"),
        "kafka_partition": d.get("kafka_partition"),
        "kafka_offset": d.get("kafka_offset"),
    }


def kafka_ids(r):
    """一意の番号と Kafka の位置（opensearch のドキュメントと splunk の event に入れる。prometheus には入れない）"""
    return {k: r.get(k) for k in ("event_id", "kafka_topic", "kafka_partition", "kafka_offset")}


def _loads(s):
    if not s:
        return {}
    try:
        v = json.loads(s)
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def parse_device_map(text):
    """"203.0.113.31=dc1-a-leaf-01,…" → {別名（小文字）: 機器名}。= の無い要素は捨てる（app/splunk/netops_alerts/bin/netops_sns.py の parse_device_map と同じ読み方）"""
    out = {}
    for p in (text or "").split(","):
        k, sep, v = p.partition("=")
        if sep and k.strip() and v.strip():
            out[k.strip().lower()] = v.strip()
    return out


def with_sysname(tags, devmap, fallback_source=False):
    """sysName の無い tags に、source を device map で引いた機器名を sysName として足した写しを返す。
    sysName があるとき（ポーリングと syslog）と、表に無いときはそのまま（同じ dict）。
    fallback_source なら、表に無い（表が空のときも）source はそのまま sysName にする（Splunk の coalesce('tags.sysName', …, 'tags.source') と同じ機器になる）"""
    if tags.get("sysName") not in (None, ""):
        return tags
    source = str(tags.get("source") or "").strip()
    name = (devmap or {}).get(source.lower()) or (source if fallback_source else "")
    return dict(tags, sysName=name) if name else tags


def _number(v):
    """field の値を数値にする。数値でなければ None（文字列の field は Prometheus に入れない）"""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- HTTP（共通）
def http_post(url, body, headers, context=None):
    """POST して (status, body) を返す。5xx と接続エラーは HTTP_RETRIES 回まで打ち直す。4xx はそのまま返す（呼ぶ側が捨てる）。
    context は TLS の設定（splunk の --splunk-skip-verify だけが渡す。無ければ既定の検証）"""
    last = None
    for attempt in range(1, HTTP_RETRIES + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=context) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            text = e.read()
            if e.code < 500:
                return e.code, text
            last = f"{e.code} {text[:200]!r}"
        except (urllib.error.URLError, OSError) as e:
            last = repr(e)
        time.sleep(2 * attempt)
    raise RuntimeError(f"POST {url} が {HTTP_RETRIES} 回とも失敗した: {last}")


def sigv4_headers(method, url, body, service, region, headers):
    """botocore で SigV4 の署名ヘッダーを足して返す（実行ロールの認証情報。AOSS は x-amz-content-sha256 が要る）"""
    import botocore.session
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    creds = botocore.session.get_session().get_credentials()
    if creds is None:
        raise RuntimeError("AWS の認証情報が無い（EMR Serverless の実行ロール）")
    h = dict(headers)
    h["x-amz-content-sha256"] = hashlib.sha256(body).hexdigest()
    req = AWSRequest(method=method, url=url, data=body, headers=h)
    SigV4Auth(creds.get_frozen_credentials(), service, region).add_auth(req)
    return dict(req.headers.items())


def log(msg):
    sys.stderr.write(f"[snmp_sinks] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- opensearch（_bulk）
def opensearch_docs(records, devmap=None):
    """_bulk の本文（action 行と document 行の対）。fields の値は数値なら数値にする。
    TIMESERIES 型はドキュメント ID を付けられないので、一意の番号 event_id は _id でなくドキュメントの項目にする（Kafka の位置も）。
    sysName の無いレコード（trap）は devmap で機器名を足し、表に無ければ source（送り元の IP）を sysName にする
    （Grafana の trap のルールが tags.sysName ごとに数えるので、無いと集計に出ない。2026-10-04 のレビュー）"""
    lines = []
    for r in records:
        doc = {
            "@timestamp": dt.datetime.fromtimestamp(r["ts"], dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "topic": r["topic"],
            "measurement": r["measurement"],
            "agent_host": r.get("agent_host"),
            "host": r.get("host"),
            "tags": with_sysname(r["tags"], devmap, fallback_source=True),
            "fields": {k: (_number(v) if _number(v) is not None else v) for k, v in r["fields"].items()},
            **kafka_ids(r),
        }
        lines.append('{"index":{}}')
        lines.append(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    return lines


def make_opensearch_sender(endpoint, index, region, devmap=None):
    url = endpoint.rstrip("/") + f"/{index}/_bulk"
    auth = env_choice("OPENSEARCH_AUTH", OPENSEARCH_AUTHS)
    if auth == "basic":
        basic_auth_header()   # パスワードが無ければ、送り始める前（ジョブの起動時）に止める

    def send(records):
        """送り、入らなかった（捨てた）ドキュメントの数を返す"""
        lines = opensearch_docs(records, devmap)
        dropped = 0
        for i in range(0, len(lines), BULK_SIZE * 2):
            body = ("\n".join(lines[i:i + BULK_SIZE * 2]) + "\n").encode("utf-8")
            if auth == "sigv4":
                headers = sigv4_headers("POST", url, body, "aoss", region, {"Content-Type": "application/x-ndjson"})
            else:
                # OSS 版の OpenSearch（Basic 認証）。値は送るたびに環境変数から読む（sender に持たせない）
                headers = {"Content-Type": "application/x-ndjson", "Authorization": basic_auth_header()}
            status, text = http_post(url, body, headers)
            if status >= 400:
                log(f"opensearch: _bulk が {status} を返した。{len(lines[i:i + BULK_SIZE * 2]) // 2} 件を捨てる: {text[:200]!r}")
                dropped += len(lines[i:i + BULK_SIZE * 2]) // 2
                continue
            try:
                res = json.loads(text)
            except ValueError:
                res = {}
            if res.get("errors"):
                failed = [it["index"] for it in res.get("items", []) if it.get("index", {}).get("error")]
                log(f"opensearch: {len(failed)} 件が入らなかった（最初の 1 件: {json.dumps(failed[0], ensure_ascii=False)[:200] if failed else ''}）")
                dropped += len(failed)
        return dropped
    return send


# ---------------------------------------------------------------- splunk（HTTP Event Collector）
def splunk_hec_url(url):
    """--splunk-hec-url を HEC のイベントの入口に揃える。https://host:8088 → …/services/collector/event、…/services/collector → …/event"""
    u = url.rstrip("/")
    if u.endswith(SPLUNK_HEC_PATH):
        return u
    if u.endswith("/services/collector"):
        return u + "/event"
    return u + SPLUNK_HEC_PATH


def _splunk_value(v):
    """field の値。数値の文字列（Telegraf は SNMP の Counter などを文字列で出す）は数値にし、bool / 数値 / それ以外の文字列はそのまま"""
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v
    return v


def splunk_events(records, index="", devmap=None):
    """HEC の JSON イベント（1 行 1 イベント。HEC は本文に並べた複数のイベントを 1 回で受ける）。
    time は epoch 秒、host は機器（無ければ Telegraf の agent_host）、sourcetype は netops:<トピック>、event に measurement / tags / fields と
    一意の番号 event_id、Kafka の位置（kafka_topic / kafka_partition / kafka_offset）。
    fields の数値の文字列は数値にする（Splunk が検索で数として扱えるように）。
    sysName の無い tags（gNMI と trap）は devmap で機器名を足す（表に無ければ足さない。cycle 013。保存済みサーチの device が Grafana と同じ機器名になる）"""
    lines = []
    for r in records:
        ev = {
            "time": r["ts"],
            "host": r.get("host") or r.get("agent_host") or "unknown",
            "source": f"telegraf:{r['measurement'] or 'unknown'}",
            "sourcetype": f"{SPLUNK_SOURCETYPE_PREFIX}:{r['topic']}",
            "event": {
                "topic": r["topic"],
                "measurement": r["measurement"],
                "agent_host": r.get("agent_host"),
                "tags": with_sysname(r["tags"], devmap),
                "fields": {k: _splunk_value(v) for k, v in r["fields"].items()},
                **kafka_ids(r),
            },
        }
        if index:
            ev["index"] = index
        lines.append(json.dumps(ev, separators=(",", ":"), ensure_ascii=False))
    return lines


def read_ssm_parameter(name, region):
    """SSM の SecureString を復号して読む（HEC の token。EMR Serverless の実行ロールに ssm:GetParameter。値はログに出さない）"""
    import boto3
    return boto3.client("ssm", region_name=region).get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def splunk_token(parameter, region):
    """HEC の token。OSS 版は ECS のタスク定義の secrets が同じ SSM の SecureString を環境変数 SPLUNK_HEC_TOKEN に入れる（あればそれ）。
    無ければマネージド版のまま SSM から読む。値はログに出さない"""
    return os.environ.get("SPLUNK_HEC_TOKEN") or read_ssm_parameter(parameter, region)


def make_splunk_sender(url, token, index="", skip_verify=False, devmap=None):
    url = splunk_hec_url(url)
    headers = {"Authorization": f"Splunk {token}", "Content-Type": "application/json"}
    context = None
    if skip_verify:
        import ssl
        context = ssl._create_unverified_context()  # noqa: S323 - 自己署名の Splunk Enterprise の検証用。既定は検証する

    def send(records):
        """送り、捨てたイベントの数を返す"""
        lines = splunk_events(records, index, devmap)
        dropped = 0
        for i in range(0, len(lines), BULK_SIZE):
            body = "\n".join(lines[i:i + BULK_SIZE]).encode("utf-8")
            status, text = http_post(url, body, headers, context)
            if status >= 400:
                # 400 は本文の形（time や event が無い）、401 / 403 は token（無効・無効化・index の許可が無い）。打ち直しても通らないので捨てる。
                # token の値は出さない（Splunk の応答にも入っていない）
                log(f"splunk: HEC が {status} を返した。{len(lines[i:i + BULK_SIZE])} 件を捨てる: {text[:200]!r}")
                dropped += len(lines[i:i + BULK_SIZE])
        return dropped
    return send


def make_splunk_sender_on_executor(url, token_parameter, region, index="", skip_verify=False, devmap=None):
    """--http-send executor の splunk の sender。token は driver から運ばず（Spark のタスクに載せない）、送るたびに executor が
    SSM（OSS 版は環境変数 SPLUNK_HEC_TOKEN）から読む。
    送り方（BULK_SIZE ごと、4xx は捨てる、TLS、devmap の sysName）は make_splunk_sender のまま。持つのは文字列と bool と device map の dict だけ（executor へ pickle で運ぶ）"""
    def send(records):
        return make_splunk_sender(url, splunk_token(token_parameter, region), index, skip_verify, devmap)(records)
    return send


# ---------------------------------------------------------------- prometheus（remote write）
_LABEL_BAD = re.compile(r"[^a-zA-Z0-9_]")
_METRIC_BAD = re.compile(r"[^a-zA-Z0-9_:]")


def metric_name(measurement, field):
    """snmp_<measurement>_<field> を Prometheus の名前の規則（[a-zA-Z_:][a-zA-Z0-9_:]*）に収める"""
    name = _METRIC_BAD.sub("_", f"{METRIC_PREFIX}_{measurement}_{field}")
    if name[0].isdigit():
        name = "_" + name
    return name


def label_name(tag):
    name = _LABEL_BAD.sub("_", tag)
    if not name or name[0].isdigit():
        name = "_" + name
    if name.startswith("__"):
        name = "_" + name.lstrip("_")  # __ で始まる名前は予約
    return name


def prometheus_series(records, devmap=None):
    """数値の field を 1 系列 1 サンプルにする。[(labels(sorted list of (name, value)), value, ms), …]
    文字列の状態は STATE_FIELDS の表で 1 / 0 の field に変える。sysName の無いレコード（gNMI）は devmap で機器名を足す。
    ラベルは tags と __name__ だけ（event_id と Kafka の位置は入れない。入れると 1 サンプルごとに別の系列になる）。
    トピックでは絞らない（prometheus のクエリは --metric-topics だけを購読している）。
    時刻の順に並べて返す（同じ時刻なら元の順）。1 バッチに同じ系列のサンプル（on_change の続けての変化）が逆順で入ると、AMP が out-of-order で拒むため"""
    out = []
    for r in records:
        base = {}
        for k, v in with_sysname(r["tags"], devmap).items():
            if v is None or v == "":
                continue
            base[label_name(k)] = str(v)
        ms = int(round(r["ts"] * 1000))
        for f, v in r["fields"].items():
            num = _number(v)
            if num is None:
                state = STATE_FIELDS.get((r["measurement"], f))
                if state is None or not isinstance(v, str):
                    continue
                f, num = state[0], 1.0 if v.strip().lower() == state[1] else 0.0
            labels = dict(base)
            labels["__name__"] = metric_name(r["measurement"] or "unknown", f)
            out.append((sorted(labels.items()), num, ms))
    return sorted(out, key=lambda s: s[2])


# protobuf の手組み（prometheus.WriteRequest。フィールド番号は prometheus/prompb/remote.proto と types.proto）
#   WriteRequest { repeated TimeSeries timeseries = 1; }
#   TimeSeries   { repeated Label labels = 1; repeated Sample samples = 2; }
#   Label        { string name = 1; string value = 2; }
#   Sample       { double value = 1; int64 timestamp = 2; }
def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field_bytes(num, payload):   # wire type 2（長さ付き）
    return _varint((num << 3) | 2) + _varint(len(payload)) + payload


def _field_varint(num, value):    # wire type 0
    if value < 0:
        value += 1 << 64
    return _varint(num << 3) + _varint(value)


def _field_double(num, value):    # wire type 1
    return _varint((num << 3) | 1) + struct.pack("<d", value)


def encode_write_request(series):
    """prometheus_series() の出力を WriteRequest の protobuf にする"""
    body = bytearray()
    for labels, value, ms in series:
        ts = bytearray()
        for name, val in labels:
            ts += _field_bytes(1, _field_bytes(1, name.encode("utf-8")) + _field_bytes(2, val.encode("utf-8")))
        ts += _field_bytes(2, _field_double(1, value) + _field_varint(2, ms))
        body += _field_bytes(1, bytes(ts))
    return bytes(body)


def snappy_compress(data, chunk=65536):
    """snappy の raw（block）形式。全部リテラルで出す（圧縮はしないが規格上正しい snappy。受け側は普通に伸長できる）。
    前置きは非圧縮長の varint、要素はタグ（下位 2 ビット 00 = リテラル。長さ - 1 が 60 以上なら 61 → 2 バイトの長さが続く）"""
    out = bytearray(_varint(len(data)))
    for i in range(0, len(data), chunk):
        part = data[i:i + chunk]
        n = len(part) - 1
        if n < 60:
            out.append(n << 2)
        else:
            out.append(61 << 2)
            out += struct.pack("<H", n)
        out += part
    return bytes(out)


def make_prometheus_sender(url, region, devmap=None):
    headers = {
        "Content-Type": "application/x-protobuf",
        "Content-Encoding": "snappy",
        "X-Prometheus-Remote-Write-Version": "0.1.0",
    }
    auth = env_choice("PROMETHEUS_AUTH", PROMETHEUS_AUTHS)   # none は OSS 版の vminsert（署名しない）

    def send(records):
        """送り、捨てたサンプルの数を返す"""
        series = prometheus_series(records, devmap)
        dropped = 0
        for i in range(0, len(series), BULK_SIZE):
            body = snappy_compress(encode_write_request(series[i:i + BULK_SIZE]))
            signed = sigv4_headers("POST", url, body, "aps", region, headers) if auth == "sigv4" else dict(headers)
            status, text = http_post(url, body, signed)
            if status >= 400:
                # 400 は out-of-order か古すぎるサンプル（startingOffsets=earliest で最初に流れる古い分など）。打ち直しても通らないので捨てる
                log(f"prometheus: remote write が {status} を返した。{len(series[i:i + BULK_SIZE])} サンプルを捨てる: {text[:200]!r}")
                dropped += len(series[i:i + BULK_SIZE])
        return dropped
    return send


# ---------------------------------------------------------------- クエリの組み立て
def partition_id():
    """executor のタスクのパーティション番号（pyspark が無いか、タスクの外なら -1）"""
    try:
        from pyspark import TaskContext
    except ImportError:
        return -1
    ctx = TaskContext.get()
    return ctx.partitionId() if ctx else -1


def sent_message(name, rows, dropped, where=""):
    """「N 行を送った」のログの文。捨てた数（4xx など。prometheus はサンプル、opensearch / splunk は件）があれば足す"""
    if not dropped:
        return f"{rows} 行を{where}送った"
    return f"{rows} 行を{where}送り、{dropped} {'サンプル' if name == 'prometheus' else '件'}を捨てた（4xx など）"


def send_partition(name, sender, batch_id, rows):
    """--http-send executor: 1 パーティションの行を executor で sender に渡し、(送った行数, 捨てた数) を返す（foreachPartition から呼ぶ。pyspark が無くても動く）。
    ts の順に並べてから送る（Prometheus は系列ごとに時刻が戻るサンプルを拒む。並べられるのはパーティションの中だけなので、
    prometheus は http_query が先に系列でパーティションを分け直す）"""
    records = sorted((row_to_record(r) for r in rows), key=lambda r: r["ts"])
    dropped = 0
    if records:
        dropped = sender(records) or 0
        log(f"{name}: batch {batch_id} partition {partition_id()} で {sent_message(name, len(records), dropped)}")
    return len(records), dropped


# Prometheus の系列は measurement（と field）と tags で決まる。Telegraf は Kafka のキーを付けないので、同じ系列の行が Kafka の
# どのパーティションにも入る。--http-send executor でそのまま foreachPartition にすると、同じ系列を 2 つのタスクが時刻の前後したまま
# 別々に送り、後から届いた古いサンプルを Prometheus が拒む。送る前にこの列でパーティションを分け直し、同じ系列を 1 つのタスクに集める
# （tags_json は Telegraf がキーを並べて出す JSON をそのまま to_json したもの）
SERIES_COLUMNS = ("measurement", "tags_json")


def http_query(rows, name, checkpoint, sender, http_send="driver"):
    """マイクロバッチごとに driver で collect して sender に渡す foreachBatch のクエリ。
    http_send が executor なら collect せず、foreachPartition でパーティションごとに executor が sender で送る（sender は pickle で executor へ運ぶ）"""
    def each_batch(batch_df, batch_id):
        # ts の順に並べてから送る（Kafka のパーティションをまたぐと時刻が前後する。Prometheus は系列ごとに時刻が戻るサンプルを拒む）
        records = sorted((row_to_record(r) for r in batch_df.collect()), key=lambda r: r["ts"])
        if records:
            dropped = sender(records) or 0
            log(f"{name}: batch {batch_id} で {sent_message(name, len(records), dropped)}")

    def each_batch_on_executors(batch_df, batch_id):
        # 行は driver に集めない。送った行数と捨てた数だけ accumulator で戻し、driver のログ（CloudWatch Logs）にも出す
        # （executor の stderr は S3 の logs にしか出ない。捨てた理由はそちら）
        if name == "prometheus":
            batch_df = batch_df.repartition(max(1, batch_df.rdd.getNumPartitions()), *SERIES_COLUMNS)
        sc = batch_df.sparkSession.sparkContext
        sent, dropped = sc.accumulator(0), sc.accumulator(0)

        def run(part):
            n, d = send_partition(name, sender, batch_id, part)
            sent.add(n)
            dropped.add(d)
        batch_df.foreachPartition(run)
        if sent.value:
            log(f"{name}: batch {batch_id} で {sent_message(name, sent.value, dropped.value, ' executor から')}"
                + ("。理由は executor の stderr（S3 の logs）" if dropped.value else ""))

    return (
        rows.writeStream.queryName(name)
        .foreachBatch({"driver": each_batch, "executor": each_batch_on_executors}[http_send])
        .option("checkpointLocation", checkpoint + name + "/")
        .trigger(processingTime=TRIGGER)
        .start()
    )


def ensure_iceberg_columns(spark, table):
    """表に無い ICEBERG_ADDED_COLUMNS を ALTER TABLE で後ろに足し、足した列の名前を返す（あれば何もしない）。
    tables.tf の表（2026-10-04 より前に作った表も、いま作る表も）はこの列を持たないので、iceberg のクエリを起こす前に毎回確かめる。
    いまある行の新しい列は null（元の value はもう無いので、あとから番号を付けられない）"""
    have = {f.name for f in spark.table(table).schema.fields}
    missing = [(n, t) for n, t in ICEBERG_ADDED_COLUMNS if n not in have]
    if missing:
        spark.sql(f"ALTER TABLE {table} ADD COLUMNS ({', '.join(f'{n} {t}' for n, t in missing)})")
    return [n for n, _ in missing]


def iceberg_query(rows, table, checkpoint):
    return (
        rows.writeStream.queryName("iceberg").format("iceberg")
        .outputMode("append")
        .option("checkpointLocation", checkpoint + "iceberg/")
        # テーブルの ts / topic は required だが、Spark の列は nullable のまま届く（to_timestamp と Kafka の topic）。
        # 検査を切らないと「ts should be required, but is optional」でクエリが止まる（2026-09-18 に実機で確認）。
        # ts が null の行は parse の where で落としてあり、Kafka の topic は null にならない
        .option("check-nullability", "false")
        .trigger(processingTime=TRIGGER)
        .toTable(table)
    )


KAFKA_IAM_PROPS = {"security.protocol": "SASL_SSL", "sasl.mechanism": "AWS_MSK_IAM",
                   "sasl.jaas.config": "software.amazon.msk.auth.iam.IAMLoginModule required;",
                   "sasl.client.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler"}


def kafka_admin_props():
    """AdminClient の認証の設定（read_rows の Kafka の認証と同じ切り替え。KAFKA_AUTH=none は OSS 版の PLAINTEXT）"""
    if env_choice("KAFKA_AUTH", KAFKA_AUTHS) == "none":
        return {"security.protocol": "PLAINTEXT"}
    return KAFKA_IAM_PROPS


def all_topics(args):
    """引数の格納先が読むトピックの和（重複なし、引数の順）"""
    seen = []
    for s in args.sinks:
        for t in sink_topics(s, args.metric_topics, args.log_topics).split(","):
            if t and t not in seen:
                seen.append(t)
    return seen


def ensure_topics(spark, bootstrap, topics):
    """無いトピックを作って、作った名前を返す（あるものは触らない）。
    MSK は auto.create.topics.enable=true だが、それは produce のとき。Telegraf が最初の trap / syslog を出すまで traps / logs は無く
    （SNMP のポーリングを止めている（Telegraf の SNMP_POLL=0）と metrics もずっと無い）、
    Spark の offset 読み（AdminClient）は無いトピックで UnknownTopicOrPartitionException で落ちて、起こし直しの上限（1 時間 5 回）を
    使い切っていた（2026-09-27 実測）。パーティション数と複製数はブローカーの既定（IaC/terraform/aws-managed/pipeline/stream の MSK configuration）。
    Telegraf と同時に作って TopicExistsException になっても、あるのだから先へ進む"""
    jvm = spark._jvm
    props = jvm.java.util.Properties()
    props.put("bootstrap.servers", bootstrap)
    for k, v in kafka_admin_props().items():
        props.put(k, v)
    admin = jvm.org.apache.kafka.clients.admin.AdminClient.create(props)
    try:
        have = set(admin.listTopics().names().get())
        missing = [t for t in topics if t not in have]
        if missing:
            none = jvm.java.util.Optional.empty()
            new = jvm.java.util.ArrayList()
            for t in missing:
                new.add(jvm.org.apache.kafka.clients.admin.NewTopic(t, none, none))
            try:
                admin.createTopics(new).all().get()
            except Exception as e:  # noqa: BLE001 - py4j の例外。TopicExists だけ許す
                if "TopicExistsException" not in str(e):
                    raise
        return missing
    finally:
        admin.close()


def build(spark, args):
    """引数の格納先ぶんのストリーミングクエリを起こして返す"""
    queries = []
    devmap = parse_device_map(args.device_map)
    # read_rows の gnmic の読み替えで map のキーが重なったら後勝ち（既定の EXCEPTION だとクエリが落ちる。gnmic_struct）。クエリを起こす前に決める
    spark.conf.set("spark.sql.mapKeyDedupPolicy", "LAST_WIN")
    for s in args.sinks:
        rows = read_rows(spark, args.bootstrap, sink_topics(s, args.metric_topics, args.log_topics), max_offsets(args, s))
        if s == "iceberg":
            added = ensure_iceberg_columns(spark, args.iceberg_table)
            if added:
                log(f"iceberg: {args.iceberg_table} に列 {', '.join(added)} を足した（いまある行は null）")
            queries.append(iceberg_query(rows, args.iceberg_table, args.checkpoint))
        elif s == "opensearch":
            queries.append(http_query(rows, s, args.checkpoint, make_opensearch_sender(args.opensearch_endpoint, args.opensearch_index, args.region, devmap), args.http_send))
        elif s == "prometheus":
            queries.append(http_query(rows, s, args.checkpoint, make_prometheus_sender(args.prometheus_url, args.region, devmap), args.http_send))
        elif s == "splunk" and args.http_send == "executor":
            # 起動時に読めるかだけ確かめる（読めなければ driver のときと同じく起動で落ちる）。値は捨て、executor が送るたびに読み直す
            splunk_token(args.splunk_token_parameter, args.region)
            queries.append(http_query(rows, s, args.checkpoint, make_splunk_sender_on_executor(
                args.splunk_hec_url, args.splunk_token_parameter, args.region, args.splunk_index, args.splunk_skip_verify, devmap), args.http_send))
        elif s == "splunk":
            # token は起動時に 1 回だけ読む（driver の中に置く。ログにも引数にも出ない）。読めなければジョブが起動で落ち、原因が stderr に出る
            token = splunk_token(args.splunk_token_parameter, args.region)
            queries.append(http_query(rows, s, args.checkpoint, make_splunk_sender(args.splunk_hec_url, token, args.splunk_index, args.splunk_skip_verify, devmap)))
    return queries


def main(argv):
    args = parse_args(argv[1:])
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName("snmp_sinks").getOrCreate()
    made = ensure_topics(spark, args.bootstrap, all_topics(args))
    log("トピック: " + ", ".join(all_topics(args)) + (f"（作った: {', '.join(made)}）" if made else "（全部あった）"))
    queries = build(spark, args)
    log("格納先: " + ", ".join(f"{s}({sink_topics(s, args.metric_topics, args.log_topics)}。1 回 {max_offsets(args, s) or '上限なし'} 件まで)" for s in args.sinks)
        + f"。HTTP の送信: {args.http_send}")
    # どれか 1 つでもクエリが止まったら、残りも止めて 1 で終わる。EMR Serverless の STREAMING モードがジョブごと起こし直し、
    # 止まったクエリも checkpoint の続きから読み直す（データは落ちない）。以前は他が動いているあいだ ERROR を出すだけでジョブが RUNNING のまま残り、
    # 一時的な失敗（HTTP の 5xx が HTTP_RETRIES 回続いた、S3 Tables の書き込みの失敗）で止まったクエリが二度と戻らなかった。
    # 起こし直しの回数はジョブの retry policy（STREAMING の既定は 1 時間に 5 回）まで。超えるとジョブが FAILED になる（docs/pipeline.md）
    dead = {}
    while not dead:
        try:
            spark.streams.awaitAnyTermination()
        except Exception:  # noqa: BLE001 - 落ちたクエリの例外。下で名前ごとに拾う
            pass
        spark.streams.resetTerminated()
        dead = {q.name: str(q.exception() or "例外なしで終了")[:500] for q in queries if not q.isActive}
    for name, why in dead.items():
        log(f"ERROR sink {name} が止まった（ジョブを 1 で終わらせて起こし直させる。checkpoint の続きから読む）: {why}")
    for q in queries:
        if q.isActive:
            try:
                q.stop()
            except Exception as e:  # noqa: BLE001 - 止めるときの例外は終わり方を変えない
                log(f"sink {q.name} を止めるときの例外: {str(e)[:200]}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
