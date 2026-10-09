"""本物の Spark で snmp_sinks.parse_rows に Kafka の行の形のバッチの DataFrame を 6 件通し、collect() まで死なずに 2 行になることを確かめる（cycle 025）。

test_*.py ではないので ops/check.sh では回らない（pyspark と Java が要る。手元の Mac には無い）。Spark のイメージ（docker/images/spark/Dockerfile）を
確認用の別のタグで build して、その中で回す。手元の compose が使う nwc-local-spark（:latest）を、このブランチの snmp_sinks.py を焼いたイメージで上書きしないため:

  docker build -f docker/images/spark/Dockerfile -t nwc-local-spark:parse-check app/spark
  docker run --rm \\
    -v "$PWD/app/spark/snmp_sinks.py:/opt/nwc/snmp_sinks.py:ro" \\
    -v "$PWD/tests/spark_parse_check.py:/opt/nwc/spark_parse_check.py:ro" \\
    nwc-local-spark:parse-check /opt/spark/bin/spark-submit --master 'local[1]' /opt/nwc/spark_parse_check.py
  docker rmi nwc-local-spark:parse-check

apache/spark のイメージは arm64 もあるので、arm64 の Mac でもそのまま build と run ができる（--platform は付けない。2026-10-09 に確かめた。cycle 025 の build.md）。
合えば最後の行が OK 2 rows で exit 0、違えば NG を print して exit 1。collect() で ValueError が出たらトレースで exit 1。
入力の 1 は 2026-10-09 の AWS の metrics の実物（docs/verification/20261009-aws-managed-2.md 不具合 1）。values も deletes も無い gnmic の event で、
025 の前の read_rows はこれを Telegraf の行として読み、ナノ秒の timestamp を秒と読んで年 173875 の ts を作り、collect() が ValueError で落ちていた。
入力の 6 は gnmic の判定（fields が無いこと）だけを縛る。timestamp が秒なので範囲の守りは効かず、判定を古い字面（values か deletes を見る形）に戻すと
Telegraf の行として残って 3 行になり NG になる。
"""
import json
import os
import sys
import time

# collect() が返す ts（naive な datetime）は Python の側の地方時で作られるので、Spark の session timezone と一緒に UTC に揃える
os.environ["TZ"] = "UTC"
time.tzset()
sys.path.insert(0, "/opt/nwc")

import snmp_sinks  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402

NS = 1791534762523125553   # 実物の event の timestamp（ナノ秒）。秒は 1791534762 = 2026-10-09T08:32:42Z
TAGS = {"interface_name": "ethernet-1/7", "source": "203.0.113.11", "subscription-name": "interface_stats"}
REAL = {"name": "interface_stats", "timestamp": NS, "tags": TAGS}   # 1. 実物（字面のまま。values も deletes も無い）
TRAP = {"fields": {"oid": "1.3.6.1.6.3.1.1.5.3"}, "name": "snmp_trap", "tags": {"agent_host": "203.0.113.11", "host": "telegraf"}, "timestamp": 1791534762}
INPUTS = [
    ("metrics", REAL),
    ("metrics", dict(REAL, values={"/srl_nokia-interfaces:interface/statistics/in-octets": "12345"})),   # 2. 実物に values を足したもの
    ("gnmi", {"name": "interface_state", "timestamp": NS, "tags": TAGS, "deletes": ["/srl_nokia-interfaces:interface/oper-state"]}),   # 3. deletes だけ
    ("traps", TRAP),   # 4. Telegraf の trap
    ("traps", dict(TRAP, timestamp=NS)),   # 5. 4 の timestamp をナノ秒にしたもの（fields はある。範囲の守りだけが効く）
    ("metrics", {"name": "interface_stats", "timestamp": 1791534762, "tags": TAGS}),   # 6. fields も values も無く timestamp が秒（範囲の内。判定だけが効く）
]
# (topic, measurement, agent_host, ts の UTC の ISO, fields_json)。2 と 4 だけが残る
EXPECTED = [
    ("metrics", "interface", "203.0.113.11", "2026-10-09T08:32:42", '{"in_octets":"12345"}'),
    ("traps", "snmp_trap", "203.0.113.11", "2026-10-09T08:32:42", '{"oid":"1.3.6.1.6.3.1.1.5.3"}'),
]


def main():
    spark = SparkSession.builder.appName("spark_parse_check").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.mapKeyDedupPolicy", "LAST_WIN")   # build と同じ（snmp_sinks.build）
    raw = spark.createDataFrame(
        [(bytearray(json.dumps(m, separators=(",", ":")).encode("utf-8")), topic, 0, offset) for offset, (topic, m) in enumerate(INPUTS)],
        "value binary, topic string, partition int, offset long",
    )
    rows = snmp_sinks.parse_rows(raw)
    got = rows.collect()   # ここで ValueError（year … is out of range）が出ないことが確認の中心
    out = []
    for r in got:
        ts = r["ts"].isoformat() if r["ts"] is not None else None
        line = (r["topic"], r["measurement"], r["agent_host"], ts, r["fields_json"])
        print("row", *line, sep="\t")
        out.append(line)
    spark.stop()
    if sorted(out) != sorted(EXPECTED):
        print(f"NG {len(out)} rows（期待は 2 行: {EXPECTED}）")
        return 1
    print(f"OK {len(out)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
