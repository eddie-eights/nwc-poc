"""ワーカーの「外に触る」部分。環境変数の読み出しと AWS 呼び出しをここにまとめる。

worker.py のアクティビティは全部このファイルの関数を asyncio.to_thread で呼ぶ。
Temporal のワークフロー（決定的でないといけない）から直接呼ぶものは 1 つも無い。

  Neptune    Neptune Analytics。トポロジと status を openCypher で読むだけ（事前チェックと保守中の判定）。書かない
  S3 Tables  修復案（proposal_events）に追記し、読む（PyIceberg。作成・承認・却下・適用・確認を 1 行ずつ。どの行も全項目）
  AgentCore  Runtime を invoke して原因分析を答えさせる
  SSM        lab EC2 に Run Command で 1 行打つ
  SQS        Grafana / Splunk のアラート（SNS → SQS）と、Web の承認・却下（決定のキュー）を long polling で受け取る

以前は異常も修復案も DynamoDB だった（2026-09-24 に Neptune と S3 Tables に寄せた。2026-10-04 に Neptune を Neptune Analytics に替えた。
2026-10-05 に修復案の頂点 proposal をやめ、修復案は S3 Tables だけに置く）。

boto3 / pyiceberg / pyarrow は import せず、呼ばれたときに関数の中で読む。Temporal のワークフローサンドボックスが
このモジュールを再 import するときに重い依存を引きずらないようにするため。

OSS 版（cycle 005）は GRAPH_BACKEND=neo4j で、トポロジを Neptune Analytics の代わりに Neo4j（NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD /
NEO4J_DATABASE）から読む。クエリは同じ文字列のまま、送る直前に _dialect で直す（app/agentcore/graph.py の _dialect の写し）。
"""

import datetime as dt
import json
import os
import re
import time
import uuid

import rules  # 判断だけの純粋関数（同じディレクトリ。重い依存は無い）

ANOMALY_QUEUE_URL = os.environ.get("ANOMALY_QUEUE_URL", "")  # IaC/terraform/aws-managed/workflow の events.tf（SNS のトピックを購読するキュー）
DECISION_QUEUE_URL = os.environ.get("DECISION_QUEUE_URL", "")  # IaC/terraform/aws-managed/workflow の events.tf（Web の承認・却下が届くキュー。SNS は購読しない）
NEPTUNE_GRAPH_ID = os.environ.get("NEPTUNE_GRAPH_ID", "")    # Neptune Analytics のグラフの ID（g-xxxxxxxxxx。IaC/terraform/aws-managed/pipeline/graph）
GRAPH_BACKEND = (os.environ.get("GRAPH_BACKEND") or "neptune").strip().lower()   # OSS 版だけ neo4j（app/agentcore/graph.py と同じ）
if GRAPH_BACKEND not in ("neptune", "neo4j"):
    raise ValueError(f"GRAPH_BACKEND は neptune / neo4j のどれか: {GRAPH_BACKEND!r}")
NEO4J_URI = os.environ.get("NEO4J_URI", "")                  # OSS 版の Neo4j（bolt://…。IaC/terraform/oss/pipeline/graph）
NEO4J_USER = os.environ.get("NEO4J_USER") or "neo4j"
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")        # ECS の secrets で SSM の SecureString から渡す
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE") or "neo4j"
GRAPH_ENV = "NEO4J_URI" if GRAPH_BACKEND == "neo4j" else "NEPTUNE_GRAPH_ID"   # グラフの接続先の変数の名前（worker.py が起動時に有無を見る）
AUDIT_TABLE_BUCKET_ARN = os.environ.get("AUDIT_TABLE_BUCKET_ARN", "")  # IaC/terraform/aws-managed/pipeline/analytics の S3 Tables のバケット
AUDIT_NAMESPACE = os.environ.get("AUDIT_NAMESPACE", "")
PROPOSAL_EVENTS_TABLE = os.environ.get("PROPOSAL_EVENTS_TABLE", "proposal_events")
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
LAB_INSTANCE_ID = os.environ.get("LAB_INSTANCE_ID", "")
REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
COMMIT_RETRIES = 5  # 証跡の append が他のアクティビティの append とぶつかった（CommitFailedException）ときに読み直して打ち直す回数

_cache = {}


def _boto(name, config=None, **kw):
    import boto3

    return boto3.client(name, region_name=REGION, config=config, **kw)


def _agent_config():
    """AgentCore の invoke は、エージェントがツールを何本も回すと 1 分を超える。botocore の既定（読み取り 60 秒・3 回まで再送）だと
    途中で切って同じ質問をもう一度投げ、アクティビティの start_to_close（worker.py）に届く前に 3 倍の時間と費用を使う。
    読み取りを 150 秒まで待ち、再送はしない（やり直しは Temporal の RetryPolicy に任せる）"""
    from botocore.config import Config

    return Config(read_timeout=150, connect_timeout=10, retries={"max_attempts": 1})


# ---------------------------------------------------------------- Neptune Analytics（トポロジと status。読むだけ）
def cypher(q: str, **params) -> list:
    """neptune-graph で openCypher を 1 本打ち、結果の行（dict）の list を返す（IAM 認証の署名は boto3 が付ける）。クライアントは使い回す。
    値は全部パラメータで渡す（エージェントの答えの本文に何が入ってもクエリは壊れない）。app/agentcore/graph.py の query と同じ"""
    if GRAPH_BACKEND == "neo4j":
        return _neo4j_cypher(q, params)
    if "neptune" not in _cache:
        from botocore.config import Config

        _cache["neptune"] = _boto("neptune-graph", Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 2}))
    kw = {"parameters": params} if params else {}
    res = _cache["neptune"].execute_query(graphIdentifier=NEPTUNE_GRAPH_ID, queryString=q, language="OPEN_CYPHER", **kw)
    return json.loads(res["payload"].read()).get("results", [])


def _dialect(q: str) -> str:
    """Neptune の openCypher を Neo4j の Cypher に直す。app/agentcore/graph.py の _dialect の写し（ワーカーの image は app/temporal/ だけで、
    app/agentcore/ を持たない）。いまのクエリは id(x) しか使わないが、`~id` や AS from / to を足しても壊れないよう 3 つとも写す。
    2 つが同じ答えを返すことは tests/test_oss.py が見る"""
    q = re.sub(r"\bid\((\w+)\)", r"\1.id", q)
    q = q.replace("`~id`", "id")
    return re.sub(r"\bAS (from|to)\b", r"AS `\1`", q)


def _neo4j_cypher(q: str, params: dict) -> list:
    """OSS 版。頂点の id は property id（_dialect で直す）。ここで読むのは値だけ（頂点や辺そのものは返さない）。
    neo4j は OSS 版の image にしか入れないので、ここで import する"""
    if "neo4j" not in _cache:
        from neo4j import GraphDatabase

        auth = (NEO4J_USER, NEO4J_PASSWORD) if NEO4J_PASSWORD else None
        _cache["neo4j"] = GraphDatabase.driver(NEO4J_URI, auth=auth, connection_timeout=10, max_transaction_retry_time=15)
    res = _cache["neo4j"].execute_query(_dialect(q), parameters_=params, database_=NEO4J_DATABASE)
    return [dict(r.items()) for r in res.records]


def read_topology() -> tuple[list, list]:
    """(devices, links)。事前チェック（rules.precheck）と保守中の判定（rules.maintenance_hold）に渡す形だけ読む:
    機器は id・status・maintenance、回線は両端の機器・IF と status"""
    devices = [{"device_id": r.get("id"), "status": r.get("status"), "maintenance": bool(r.get("maintenance"))}
               for r in cypher("MATCH (n:device) RETURN id(n) AS id, n.status AS status, n.maintenance AS maintenance")]
    links = [{k: r.get(k) for k in ("a", "b", "a_if", "b_if", "status")}
             for r in cypher("MATCH (a)-[l:link]->(b) RETURN id(a) AS a, id(b) AS b, l.a_if AS a_if, l.b_if AS b_if, l.status AS status")]
    return devices, links


# ---------------------------------------------------------------- S3 Tables（修復案。proposal_events）
def catalog_properties() -> dict:
    """S3 Tables の Iceberg REST エンドポイントにつなぐ PyIceberg の設定（SigV4 の署名名は s3tables）"""
    return {
        "type": "rest", "warehouse": AUDIT_TABLE_BUCKET_ARN, "uri": f"https://s3tables.{REGION}.amazonaws.com/iceberg",
        "rest.sigv4-enabled": "true", "rest.signing-name": "s3tables", "rest.signing-region": REGION, "s3.region": REGION,
    }


def _table():
    """proposal_events を開く（カタログは使い回す。テーブルは呼ぶたびに読み直して、いちばん新しいスナップショットを見る）"""
    if "catalog" not in _cache:
        from pyiceberg.catalog import load_catalog

        _cache["catalog"] = load_catalog("s3tables", **catalog_properties())
    return _cache["catalog"].load_table(f"{AUDIT_NAMESPACE}.{PROPOSAL_EVENTS_TABLE}")


def audit_rows(rows: list, columns) -> list:
    """rules.proposal_event の行を、列の型（timestamptz は epoch 秒 → UTC の datetime、int は int）に合わせた dict にする"""
    def conv(v, t):
        if v is None:
            return None
        if t == "timestamptz":
            return dt.datetime.fromtimestamp(int(v), dt.timezone.utc)
        return int(v) if t == "int" else str(v)
    return [{n: conv(r.get(n), t) for n, t in columns} for r in rows]


def from_table_rows(rows: list) -> list:
    """PyIceberg で読んだ行を rules.proposal_event の形に戻す（timestamptz の datetime → epoch 秒）"""
    return [{k: int(v.timestamp()) if isinstance(v, dt.datetime) else v for k, v in r.items()} for r in rows]


def _scan(column: str, value: str) -> list:
    """proposal_events の column = value の行を全部読む（値は式で渡す。文字列に埋めない）"""
    from pyiceberg.expressions import EqualTo

    return from_table_rows(_table().scan(row_filter=EqualTo(column, value)).to_arrow().to_pylist())


def latest_proposal(proposal_id: str) -> dict:
    """修復案の「いま」（proposal_id の行のうち seq が最大の行）。無ければ空の辞書"""
    return rules.latest_proposals(_scan("proposal_id", proposal_id)).get(proposal_id, {})


def anomaly_proposals(anomaly_id: str) -> dict:
    """同じ異常（anomaly_id）の修復案ごとの「いま」。{proposal_id: 最新の行}"""
    return rules.latest_proposals(_scan("anomaly_id", anomaly_id))


def append_proposal_events(rows: list, columns) -> None:
    """proposal_events に rows を append する（1 回のコミット。rows の順に入る）。
    同じテーブルへの append がぶつかったら（CommitFailedException）読み直して打ち直す"""
    if not rows:
        return
    import pyarrow as pa
    from pyiceberg.exceptions import CommitFailedException

    data = audit_rows(rows, columns)
    for attempt in range(COMMIT_RETRIES):
        table = _table()
        try:
            table.append(pa.Table.from_pylist(data, schema=table.schema().as_arrow()))
            return
        except CommitFailedException:
            if attempt == COMMIT_RETRIES - 1:
                raise
            time.sleep(1 + attempt)


# ---------------------------------------------------------------- AgentCore Runtime
def ask_agent(prompt: str) -> str:
    session_id = f"workflow-{uuid.uuid4()}"  # runtimeSessionId は 33 文字以上
    res = _boto("bedrock-agentcore", _agent_config()).invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN, runtimeSessionId=session_id, qualifier="DEFAULT",
        contentType="application/json", accept="application/json",
        payload=json.dumps({"prompt": prompt}, ensure_ascii=False).encode("utf-8"))
    data = json.loads(res["response"].read())
    if not isinstance(data, dict) or data.get("status") != "success":
        raise RuntimeError(f"agent error: {str(data)[:300]}")
    return data.get("response", "")


# ---------------------------------------------------------------- SSM（lab EC2）
def run_on_lab(command: str, timeout: int = 120) -> tuple[str, str]:
    """SSM Run Command で lab EC2 に 1 行打ち、(status, output) を返す"""
    ssm = _boto("ssm")
    cmd = ssm.send_command(
        InstanceIds=[LAB_INSTANCE_ID], DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command], "executionTimeout": [str(timeout)]}, TimeoutSeconds=60)
    cid = cmd["Command"]["CommandId"]
    deadline = time.time() + timeout + 30
    while time.time() < deadline:
        time.sleep(3)
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=LAB_INSTANCE_ID)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if inv["Status"] in ("Pending", "InProgress", "Delayed"):
            continue
        out = (inv.get("StandardOutputContent", "") + inv.get("StandardErrorContent", ""))[:4000]
        return inv["Status"], out
    return "TimedOut", ""


# ---------------------------------------------------------------- SQS（Grafana / Splunk のアラートと、Web の承認・却下）
def receive_messages(queue_url: str) -> list:
    """queue_url は ANOMALY_QUEUE_URL（アラート）か DECISION_QUEUE_URL（決定）"""
    return _boto("sqs").receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=20).get("Messages", [])


def delete_message(queue_url: str, receipt: str) -> None:
    _boto("sqs").delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
