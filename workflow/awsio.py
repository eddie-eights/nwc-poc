"""ワーカーの「外に触る」部分。環境変数の読み出しと AWS 呼び出しをここにまとめる。

worker.py のアクティビティは全部このファイルの関数を asyncio.to_thread で呼ぶ。
Temporal のワークフロー（決定的でないといけない）から直接呼ぶものは 1 つも無い。

  Neptune    Neptune Analytics。修復案の「いま」（label proposal）を openCypher で読み書きする（異常の頂点は 2026-10-02 にやめた。発生と解消は Temporal が持つ）
  S3 Tables  修復案の証跡（proposal_events）に追記する（PyIceberg。作成・承認・却下・適用・確認を 1 行ずつ）
  AgentCore  Runtime を invoke して原因分析を答えさせる
  SSM        lab EC2 に Run Command で 1 行打つ
  SQS        Grafana / Splunk のアラート（SNS → SQS）を long polling で受け取る

以前は異常も修復案も DynamoDB だった（2026-09-24 に Neptune と S3 Tables に寄せた。2026-10-04 に Neptune を Neptune Analytics に替えた）。

boto3 / pyiceberg / pyarrow は import せず、呼ばれたときに関数の中で読む。Temporal のワークフローサンドボックスが
このモジュールを再 import するときに重い依存を引きずらないようにするため。
"""

import datetime as dt
import json
import os
import time
import uuid

ANOMALY_QUEUE_URL = os.environ.get("ANOMALY_QUEUE_URL", "")  # terraform/workflow の events.tf（SNS のトピックを購読するキュー）
NEPTUNE_GRAPH_ID = os.environ.get("NEPTUNE_GRAPH_ID", "")    # Neptune Analytics のグラフの ID（g-xxxxxxxxxx。terraform/pipeline/graph）
AUDIT_TABLE_BUCKET_ARN = os.environ.get("AUDIT_TABLE_BUCKET_ARN", "")  # terraform/pipeline/analytics の S3 Tables のバケット
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


# ---------------------------------------------------------------- Neptune Analytics（修復案の「いま」）
def cypher(q: str, **params) -> list:
    """neptune-graph で openCypher を 1 本打ち、結果の行（dict）の list を返す（IAM 認証の署名は boto3 が付ける）。クライアントは使い回す。
    値は全部パラメータで渡す（エージェントの答えの本文に何が入ってもクエリは壊れない）。agent/graph.py の query と同じ"""
    if "neptune" not in _cache:
        from botocore.config import Config

        _cache["neptune"] = _boto("neptune-graph", Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 2}))
    kw = {"parameters": params} if params else {}
    res = _cache["neptune"].execute_query(graphIdentifier=NEPTUNE_GRAPH_ID, queryString=q, language="OPEN_CYPHER", **kw)
    return json.loads(res["payload"].read()).get("results", [])


def _item(n: dict, key: str) -> dict:
    """頂点 1 件（{~id, ~labels, ~properties}）を、id を key（proposal_id）に置き換えた dict にする。_writer は write_proposal の目印なので出さない"""
    d = {k: v for k, v in (n.get("~properties") or {}).items() if k != "_writer"}
    d[key] = n.get("~id")
    return d


def _props(fields: dict) -> dict:
    """頂点に書く property の map。None と空文字は書かない。property の値はスカラーだけなので、それ以外は文字列にする"""
    return {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in fields.items() if v is not None and v != ""}


def read_proposal(proposal_id: str) -> dict:
    rows = cypher("MATCH (n:proposal) WHERE id(n) = $id RETURN n", id=proposal_id)
    return _item(rows[0]["n"], "proposal_id") if rows else {}


def write_proposal(item: dict, only_new: bool = False) -> bool:
    """修復案の頂点を書く。only_new なら同じ proposal_id が無いときだけ作り、あれば書かずに False（人が決めた status を pending に戻さない）。
    「自分が作ったか」は、作るときだけ書く目印（_writer）が自分のものかで見分ける（MERGE 1 本なので、同時に 2 つ来ても作るのは片方）"""
    props = _props({k: v for k, v in item.items() if k != "proposal_id"})
    if only_new:
        token = uuid.uuid4().hex
        rows = cypher("MERGE (n:proposal {`~id`: $id}) ON CREATE SET n += $props, n._writer = $token RETURN n._writer AS w",
                      id=item["proposal_id"], props=props, token=token)
        return bool(rows and rows[0].get("w") == token)
    cypher("MERGE (n:proposal {`~id`: $id}) SET n += $props", id=item["proposal_id"], props=props)
    return True


def update_proposal(proposal_id: str, fields: dict, only_status: str | None = None) -> bool:
    """修復案の頂点の fields を書き換える（updated_at は今）。only_status なら status がその値のときだけ書く
    （条件と書き込みが 1 本のクエリなので、読んでから書くあいだに割り込まれない）。書けたら True"""
    cond, params = ("", {})
    if only_status:
        cond, params = " AND n.status = $only", {"only": only_status}
    rows = cypher(f"MATCH (n:proposal) WHERE id(n) = $id{cond} SET n += $fields RETURN id(n) AS id",
                  id=proposal_id, fields=_props({**fields, "updated_at": int(time.time())}), **params)
    return bool(rows)


def read_topology() -> tuple[list, list]:
    """(devices, links)。事前チェック（rules.precheck）と保守中の判定（rules.maintenance_hold）に渡す形だけ読む:
    機器は id・status・maintenance、回線は両端の機器・IF と status"""
    devices = [{"device_id": r.get("id"), "status": r.get("status"), "maintenance": bool(r.get("maintenance"))}
               for r in cypher("MATCH (n:device) RETURN id(n) AS id, n.status AS status, n.maintenance AS maintenance")]
    links = [{k: r.get(k) for k in ("a", "b", "a_if", "b_if", "status")}
             for r in cypher("MATCH (a)-[l:link]->(b) RETURN id(a) AS a, id(b) AS b, l.a_if AS a_if, l.b_if AS b_if, l.status AS status")]
    return devices, links


# ---------------------------------------------------------------- S3 Tables（修復案の証跡）
def catalog_properties() -> dict:
    """S3 Tables の Iceberg REST エンドポイントにつなぐ PyIceberg の設定（SigV4 の署名名は s3tables）"""
    return {
        "type": "rest", "warehouse": AUDIT_TABLE_BUCKET_ARN, "uri": f"https://s3tables.{REGION}.amazonaws.com/iceberg",
        "rest.sigv4-enabled": "true", "rest.signing-name": "s3tables", "rest.signing-region": REGION, "s3.region": REGION,
    }


def audit_rows(rows: list, columns) -> list:
    """rules.proposal_event の行を、列の型（timestamptz は epoch 秒 → UTC の datetime）に合わせた dict にする"""
    def conv(v, t):
        if v is None:
            return None
        return dt.datetime.fromtimestamp(int(v), dt.timezone.utc) if t == "timestamptz" else str(v)
    return [{n: conv(r.get(n), t) for n, t in columns} for r in rows]


def append_proposal_events(rows: list, columns) -> None:
    """proposal_events に rows を append する。同じテーブルへの append がぶつかったら（CommitFailedException）読み直して打ち直す"""
    if not rows:
        return
    import pyarrow as pa
    from pyiceberg.exceptions import CommitFailedException

    if "catalog" not in _cache:
        from pyiceberg.catalog import load_catalog

        _cache["catalog"] = load_catalog("s3tables", **catalog_properties())
    data = audit_rows(rows, columns)
    for attempt in range(COMMIT_RETRIES):
        table = _cache["catalog"].load_table(f"{AUDIT_NAMESPACE}.{PROPOSAL_EVENTS_TABLE}")
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


# ---------------------------------------------------------------- SQS（Grafana / Splunk のアラート）
def receive_messages() -> list:
    return _boto("sqs").receive_message(QueueUrl=ANOMALY_QUEUE_URL, MaxNumberOfMessages=10, WaitTimeSeconds=20).get("Messages", [])


def delete_message(receipt: str) -> None:
    _boto("sqs").delete_message(QueueUrl=ANOMALY_QUEUE_URL, ReceiptHandle=receipt)
