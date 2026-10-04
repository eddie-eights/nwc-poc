"""Kafka（MSK、IAM 認証）のトピックを読み、選んだ格納先に流し続ける Spark Structured Streaming のジョブ（Kafka の 4 分岐）。

EMR Serverless の上で動く（terraform/pipeline/analytics）。起動は ops/up.sh の a-3（start-job-run）で、引数は terraform/pipeline/analytics の
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
（機器やメトリクスが増えてもテーブルの列を変えないため。terraform/pipeline/analytics/tables.tf の列と同じ）。

どの行にも一意の番号 event_id を付ける: Kafka のメッセージの value（from_json の前のバイト列そのまま）の SHA-256 の 16 進 64 文字（F.sha2）。
中身から作るので、Spark のやり直しで同じメッセージを送り直しても、Telegraf が同じメッセージを Kafka に 2 回入れても同じ値になり、読む側で重複を落とせる
（ここでは dropDuplicates しない）。Telegraf の timestamp は秒（json_timestamp_units = "1s"）なので、同じ秒に同じ中身の別の出来事も同じ event_id になる（受け入れる）。
元のメッセージを追うための Kafka の位置（kafka_topic / kafka_partition / kafka_offset）も別の項目で入れる（送り直しは別の offset になるので、重複の判定には使わない）。
入れるのは iceberg（列。tables.tf の列のあとに、ジョブが起動時に ALTER TABLE で足す。ICEBERG_ADDED_COLUMNS）、opensearch（ドキュメントの項目）、
splunk（event の項目）。prometheus には入れない（ラベルにすると 1 サンプルごとに別の系列になる）。

異常の検知はここではしない（2026-10-02 にやめた。detect のクエリと Neptune の anomaly 頂点、S3 Tables の anomaly_events、EventBridge への put_events を消した）。
検知と相関は格納先の側でする: Grafana のアラートルール（AMP の ifOperStatus。grafana/provisioning/alerting）と Splunk の保存済みサーチ
（trap・gNMI の BGP / IS-IS。splunk/netops_alerts）が SNS のトピック <接頭辞>-alerts に出し、ワークフロー（SQS）と
トポロジの status（graph の Lambda）がそれを受ける。

HTTP の送信は既定で driver でまとめて行う（マイクロバッチを collect する。PoC の量（機器数台、10 秒間隔）なら 1 分に数百行）。
量が増えたら --http-send executor で foreachPartition に切り替える（集めずに、パーティションごとに executor が送る）。remote write の protobuf と snappy は外部ライブラリ無しで組む
（EMR Serverless の Python に protobuf / python-snappy は無い。snappy は「全部リテラル」の圧縮で規格上正しい）。

Kafka は 1 回のトリガー（60 秒）に 1 つのクエリが 10000 件まで読む（--max-offsets-per-trigger。格納先ごとに --max-offsets-per-trigger-by-sink で変えられ、0 で上限なし）。
止めていたジョブを起こし直した直後や最初に earliest から読むときに、溜まった分を 1 回で読んで driver のメモリ（2g）に collect しないため。
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import struct
import sys
import time
import urllib.error
import urllib.request

METRIC_TOPICS = "metrics,gnmi,mdt"   # metrics = Telegraf の inputs.snmp と lab の gNMI を変えた共通の形、gnmi = inputs.gnmi、mdt = inputs.cisco_telemetry_mdt（telegraf/telegraf.conf.in。Telegraf（ECS）で動く）
LOG_TOPICS = "traps,logs"   # traps = Telegraf の inputs.snmp_trap、logs = inputs.syslog（機器の syslog。measurement は device_log）
SINKS = ("iceberg", "opensearch", "prometheus", "splunk")
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
# S3 Tables の表に、ジョブが起動時に足す列（tables.tf の列のあとに、この順。名前と Spark SQL の型）。tables.tf の schema には書かない:
# aws provider（6.64）の aws_s3tables_table は metadata の schema を変えると表を作り直す（RequiresReplace）ので、いまある行が消える。
# ALTER TABLE ADD COLUMNS は Iceberg のメタデータだけの変更で、いまある行はこの列が null のまま読める
ICEBERG_ADDED_COLUMNS = (("event_id", "string"), ("kafka_topic", "string"), ("kafka_partition", "int"), ("kafka_offset", "bigint"))


# ---------------------------------------------------------------- 引数
def parse_args(argv):
    p = argparse.ArgumentParser(prog="snmp_sinks.py", description=__doc__.split("\n")[0])
    p.add_argument("--bootstrap", required=True, help="MSK の bootstrap servers（SASL/IAM、9098）")
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
    p.add_argument("--splunk-token-parameter", default="", help="splunk: HEC の token を入れた SSM の SecureString の名前（/<接頭辞>/splunk/hec-token。値は起動時に読み、ログに出さない）")
    p.add_argument("--splunk-index", default="", help="splunk: イベントを入れる index（空なら token の既定の index）")
    p.add_argument("--splunk-skip-verify", action="store_true", help="splunk: HEC の TLS 証明書を検証しない（自己署名の Splunk Enterprise の検証用。既定は検証する）")
    args = p.parse_args(argv)
    args.sinks = [s.strip() for s in args.sinks.split(",") if s.strip()]
    bad = [s for s in args.sinks if s not in SINKS]
    if bad or not args.sinks:
        p.error(f"--sinks は {', '.join(SINKS)} のどれか（カンマ区切り）: {args.sinks}")
    need = {"iceberg": ["iceberg_table"], "opensearch": ["opensearch_endpoint"], "prometheus": ["prometheus_url"],
            "splunk": ["splunk_hec_url", "splunk_token_parameter"]}
    for s in args.sinks:
        for k in need[s]:
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


# ---------------------------------------------------------------- 行の形（Kafka → 列）
def read_rows(spark, bootstrap, topics, max_offsets_per_trigger=0):
    """Kafka の topics（カンマ区切り）を読んで tables.tf の列にした DataFrame を返す（テストでは start せずに中身だけ見る）。
    max_offsets_per_trigger が 0 でなければ、1 回のトリガーに読む件数をそこで抑える（Kafka の maxOffsetsPerTrigger）"""
    from pyspark.sql import functions as F
    from pyspark.sql import types as T

    # Telegraf の JSON のうち、列に分ける部分だけ型を書く。tags / fields は文字列のまま
    schema = T.StructType([
        T.StructField("timestamp", T.LongType()),
        T.StructField("name", T.StringType()),
        T.StructField("tags", T.MapType(T.StringType(), T.StringType())),
        T.StructField("fields", T.MapType(T.StringType(), T.StringType())),
    ])
    reader = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap)
        .option("subscribe", topics)
        .option("startingOffsets", "earliest")
        # MSK の IAM 認証（aws-msk-iam-auth の -all jar。AWS ドキュメント「Connect to MSK with IAM」の 4 項目）
        .option("kafka.security.protocol", "SASL_SSL")
        .option("kafka.sasl.mechanism", "AWS_MSK_IAM")
        .option("kafka.sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;")
        .option("kafka.sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler")
    )
    if max_offsets_per_trigger:
        reader = reader.option("maxOffsetsPerTrigger", str(max_offsets_per_trigger))
    raw = reader.load()
    parsed = raw.select(
        F.col("topic"),
        F.from_json(F.col("value").cast("string"), schema).alias("m"),
        # 一意の番号は from_json の前の value（binary のまま）から作る。同じバイト列なら、いつ何度読んでも同じ値（モジュールの docstring）
        F.sha2(F.col("value"), 256).alias("event_id"),
        F.col("partition").alias("kafka_partition"),
        F.col("offset").alias("kafka_offset"),
    )
    rows = parsed.select(
        F.to_timestamp(F.from_unixtime(F.col("m.timestamp"))).alias("ts"),
        F.col("topic"),
        F.col("m.name").alias("measurement"),
        F.col("m.tags")["agent_host"].alias("agent_host"),
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
def opensearch_docs(records):
    """_bulk の本文（action 行と document 行の対）。fields の値は数値なら数値にする。
    TIMESERIES 型はドキュメント ID を付けられないので、一意の番号 event_id は _id でなくドキュメントの項目にする（Kafka の位置も）"""
    lines = []
    for r in records:
        doc = {
            "@timestamp": dt.datetime.fromtimestamp(r["ts"], dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "topic": r["topic"],
            "measurement": r["measurement"],
            "agent_host": r.get("agent_host"),
            "host": r.get("host"),
            "tags": r["tags"],
            "fields": {k: (_number(v) if _number(v) is not None else v) for k, v in r["fields"].items()},
            **kafka_ids(r),
        }
        lines.append('{"index":{}}')
        lines.append(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    return lines


def make_opensearch_sender(endpoint, index, region):
    url = endpoint.rstrip("/") + f"/{index}/_bulk"

    def send(records):
        """送り、入らなかった（捨てた）ドキュメントの数を返す"""
        lines = opensearch_docs(records)
        dropped = 0
        for i in range(0, len(lines), BULK_SIZE * 2):
            body = ("\n".join(lines[i:i + BULK_SIZE * 2]) + "\n").encode("utf-8")
            headers = sigv4_headers("POST", url, body, "aoss", region, {"Content-Type": "application/x-ndjson"})
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


def splunk_events(records, index=""):
    """HEC の JSON イベント（1 行 1 イベント。HEC は本文に並べた複数のイベントを 1 回で受ける）。
    time は epoch 秒、host は機器（無ければ Telegraf の agent_host）、sourcetype は netops:<トピック>、event に measurement / tags / fields と
    一意の番号 event_id、Kafka の位置（kafka_topic / kafka_partition / kafka_offset）。
    fields の数値の文字列は数値にする（Splunk が検索で数として扱えるように）"""
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
                "tags": r["tags"],
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


def make_splunk_sender(url, token, index="", skip_verify=False):
    url = splunk_hec_url(url)
    headers = {"Authorization": f"Splunk {token}", "Content-Type": "application/json"}
    context = None
    if skip_verify:
        import ssl
        context = ssl._create_unverified_context()  # noqa: S323 - 自己署名の Splunk Enterprise の検証用。既定は検証する

    def send(records):
        """送り、捨てたイベントの数を返す"""
        lines = splunk_events(records, index)
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


def make_splunk_sender_on_executor(url, token_parameter, region, index="", skip_verify=False):
    """--http-send executor の splunk の sender。token は driver から運ばず（Spark のタスクに載せない）、送るたびに executor が SSM から読む。
    送り方（BULK_SIZE ごと、4xx は捨てる、TLS）は make_splunk_sender のまま。持つのは文字列と bool だけ（executor へ pickle で運ぶ）"""
    def send(records):
        return make_splunk_sender(url, read_ssm_parameter(token_parameter, region), index, skip_verify)(records)
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


def prometheus_series(records):
    """数値の field を 1 系列 1 サンプルにする。[(labels(sorted list of (name, value)), value, ms), …]
    ラベルは tags と __name__ だけ（event_id と Kafka の位置は入れない。入れると 1 サンプルごとに別の系列になる）。
    トピックでは絞らない（prometheus のクエリは --metric-topics だけを購読している）"""
    out = []
    for r in records:
        base = {}
        for k, v in r["tags"].items():
            if v is None or v == "":
                continue
            base[label_name(k)] = str(v)
        ms = int(round(r["ts"] * 1000))
        for f, v in r["fields"].items():
            num = _number(v)
            if num is None:
                continue
            labels = dict(base)
            labels["__name__"] = metric_name(r["measurement"] or "unknown", f)
            out.append((sorted(labels.items()), num, ms))
    return out


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


def make_prometheus_sender(url, region):
    headers = {
        "Content-Type": "application/x-protobuf",
        "Content-Encoding": "snappy",
        "X-Prometheus-Remote-Write-Version": "0.1.0",
    }

    def send(records):
        """送り、捨てたサンプルの数を返す"""
        series = prometheus_series(records)
        dropped = 0
        for i in range(0, len(series), BULK_SIZE):
            body = snappy_compress(encode_write_request(series[i:i + BULK_SIZE]))
            signed = sigv4_headers("POST", url, body, "aps", region, headers)
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
    （SNMP のポーリングを止めている（Telegraf の SNMP_POLL=0。既定）と metrics もずっと無い）、
    Spark の offset 読み（AdminClient）は無いトピックで UnknownTopicOrPartitionException で落ちて、起こし直しの上限（1 時間 5 回）を
    使い切っていた（2026-09-27 実測）。パーティション数と複製数はブローカーの既定（terraform/pipeline/stream の MSK configuration）。
    Telegraf と同時に作って TopicExistsException になっても、あるのだから先へ進む"""
    jvm = spark._jvm
    props = jvm.java.util.Properties()
    props.put("bootstrap.servers", bootstrap)
    for k, v in KAFKA_IAM_PROPS.items():
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
    for s in args.sinks:
        rows = read_rows(spark, args.bootstrap, sink_topics(s, args.metric_topics, args.log_topics), max_offsets(args, s))
        if s == "iceberg":
            added = ensure_iceberg_columns(spark, args.iceberg_table)
            if added:
                log(f"iceberg: {args.iceberg_table} に列 {', '.join(added)} を足した（いまある行は null）")
            queries.append(iceberg_query(rows, args.iceberg_table, args.checkpoint))
        elif s == "opensearch":
            queries.append(http_query(rows, s, args.checkpoint, make_opensearch_sender(args.opensearch_endpoint, args.opensearch_index, args.region), args.http_send))
        elif s == "prometheus":
            queries.append(http_query(rows, s, args.checkpoint, make_prometheus_sender(args.prometheus_url, args.region), args.http_send))
        elif s == "splunk" and args.http_send == "executor":
            # 起動時に読めるかだけ確かめる（読めなければ driver のときと同じく起動で落ちる）。値は捨て、executor が送るたびに SSM から読み直す
            read_ssm_parameter(args.splunk_token_parameter, args.region)
            queries.append(http_query(rows, s, args.checkpoint, make_splunk_sender_on_executor(
                args.splunk_hec_url, args.splunk_token_parameter, args.region, args.splunk_index, args.splunk_skip_verify), args.http_send))
        elif s == "splunk":
            # token は起動時に 1 回だけ読む（driver の中に置く。ログにも引数にも出ない）。読めなければジョブが起動で落ち、原因が stderr に出る
            token = read_ssm_parameter(args.splunk_token_parameter, args.region)
            queries.append(http_query(rows, s, args.checkpoint, make_splunk_sender(args.splunk_hec_url, token, args.splunk_index, args.splunk_skip_verify)))
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
