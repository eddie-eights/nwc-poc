"""ops/up.sh と ops/sync-graph.sh が Web の EC2 の上で打つ。lab の定義から作ったトポロジ（app/containerlab/lab_topology.py の JSON）をグラフ DB に入れる
（マネージド版は Neptune、OSS 版（cycle 005）は Neo4j。どちらかは Web の環境変数 GRAPH_BACKEND で決まり、app/agentcore/graph.py が切り替える）。

Web と同じ環境変数（/etc/<prefix>-web.env）と依存（/opt/<prefix>-web/lib）で動かす。GUI の「静的データを投入」と同じ関数（graph.seed）を呼ぶ。
グラフ DB は出来た直後だとつながらないことがあるので、30 秒おきに 10 回まで試す。

環境変数:
  LAB_TOPOLOGY_B64  app/containerlab/lab_topology.py の出力（{"devices": [...], "links": [...], "layers": {...}}）を base64 にしたもの。無ければ app/agentcore/data の静的データ
  GRAPH_REPLACE     1 ならグラフ DB に入っていても入れ直す（lab を変えたあとの同期。動的な status は消えて全部 UP に戻る）。
                    既定は空で、グラフ DB が空のときだけ入れる（初期ロード）
  NAME_PREFIX       **必須。**リソース名の接頭辞（<owner>-nwc-poc か <owner>-nwc-oss。呼ぶ側の ops/up.sh / oss/ops/up.sh / ops/sync-graph.sh が渡す）。
                    Web の置き場と設定ファイルの名前に入る
"""
import base64
import json
import os
import sys
import time

PREFIX = os.environ.get("NAME_PREFIX") or sys.exit("NAME_PREFIX（リソース名の接頭辞 <owner>-nwc-poc）が要る")
APP = f"/opt/{PREFIX}-web"

# systemd の EnvironmentFile と同じく「名前=値」を 1 行ずつ読む（値に空白があるので source しない）
with open(f"/etc/{PREFIX}-web.env", encoding="utf-8") as f:
    for line in f:
        key, sep, value = line.rstrip("\n").partition("=")
        if sep and key and not key.startswith("#"):
            os.environ[key] = value
sys.path[:0] = [f"{APP}/src", f"{APP}/lib"]

import graph  # noqa: E402
import topology  # noqa: E402

# OSS 版（Web の環境変数に GRAPH_BACKEND=neo4j。cycle 005）は neo4j-uri を読み、入れ直す手順は ops/sync-graph.sh に --oss が付く
OSS = graph.BACKEND == "neo4j"
DB = "Neo4j" if OSS else "Neptune"
SYNC = "ops/sync-graph.sh --oss --replace" if OSS else "ops/sync-graph.sh --replace"
if not graph.configured():
    _param = "neo4j-uri" if OSS else "neptune-graph-id"
    _root = "IaC/terraform/oss/pipeline/graph" if OSS else "IaC/terraform/aws-managed/pipeline/graph"
    sys.exit(f"SSM の {os.environ.get('PARAM_PREFIX', '')}/{_param} が読めない（{_root} の apply が終わっているか）")

if os.environ.get("LAB_TOPOLOGY_B64"):
    lab = json.loads(base64.b64decode(os.environ["LAB_TOPOLOGY_B64"]))
    devices, links, layers, source = lab["devices"], lab["links"], lab.get("layers"), "lab の定義（app/containerlab/lab_topology.py）"
else:
    devices, links = topology.load_static()
    layers, source = topology.load_static_layers(), "静的データ（app/agentcore/data）"

last = None
for attempt in range(1, 11):
    try:
        counts = graph.count()
        break
    except Exception as e:  # 出来た直後の接続失敗や、IAM の反映待ち
        last = e
        print(f"{DB} にまだつながらない（{attempt}/10）: {e}", flush=True)
        time.sleep(30)
else:
    sys.exit(f"{DB} につながらない: {last}")

if counts["devices"] and os.environ.get("GRAPH_REPLACE") != "1":
    print(f"{DB} にはもう入っている。投入を飛ばす（入れ直すなら {SYNC}）: {counts}")
else:
    n_layers = len((layers or {}).get("vertices") or [])
    print(f"{DB} に {source} を入れた（{len(devices)} 台 / {len(links)} 本 / 上の層 {n_layers} 頂点）: {graph.seed(devices, links, layers)}")
