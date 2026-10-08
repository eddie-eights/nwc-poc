"""oss/ops/roll-nodes.sh が手元の PC で打つ。Kafka と OpenSearch の ECS のサービスを 1 台ずつ入れ替える手順（OSS 版。cycle 005 の
設計の未確定事項 2・4）のうち、出力を読むところだけをここに置く（シェルで読むと検査できないので）。

  roll_health.py plan <kafka|opensearch>              標準入力の terraform show -json <planfile> から、入れ替える台（aws_ecs_service.<種類>["台"]
                                                      のうち、変える（update）か作り直す（delete と create）もの）を空白区切りで出す。
                                                      作るだけ・消すだけの台は入れない（1 台ずつ入れ替える相手ではない）
  roll_health.py kafka <台…>                          標準入力の ECS Exec の出力（roll-nodes.sh の ROLL_PROBE_KAFKA）を読み、健全ならリーダー
  roll_health.py opensearch <台…>                     （Kafka は KRaft のアクティブな controller、OpenSearch は cluster manager）の台を出して 0 で終わる。
                                                      健全でなければ理由を 1 行出して 1 で終わる

ECS Exec はタスクの中のコマンドの終了コードを返さず、出力の前後に Session Manager の案内の行が付く（行末に \\r が付くこともある）。
そこでタスクの中で「==nwc-roll <節>」の印と「==nwc-rc <終了コード>」を出し、印の中だけを読む。
標準入力が端末でないとき roll-nodes.sh は ECS Exec を script で包み、macOS の script は行のどこかに EOF の写し（^D と後退 2 つ）を混ぜるので、
それは消してから読む。ECS Exec が「Cannot perform start session」で切れたときは、印の有無より先にその行を理由にする
（EOF なら、標準入力が端末でないのが原因）。
健全の条件:
  Kafka       どのコマンドも 0 で終わり、kafka-broker-api-versions.sh に全部の台が fenced でなく載り、
              kafka-topics.sh --under-replicated-partitions が 1 行も出さず、kafka-metadata-quorum.sh describe --replication に
              全部の台が Leader か Follower で載って遅れ（Lag）が ROLL_KAFKA_MAX_LAG 以下（リーダーはちょうど 1 台）
  OpenSearch  _cluster/health が green で number_of_nodes が台の数、_cat/cluster_manager が台のどれか（opensearch-<台>）
"""
import json
import re
import sys

# KRaft の metadata のログは何も無くても一定の間隔で進むので、0 ちょうどは求めない（入れ替えた台が追いついていない間は数百〜数千になる）
ROLL_KAFKA_MAX_LAG = 100
# macOS の script が、標準入力の EOF を疑似端末に渡したときに出る写し
SCRIPT_EOF_ECHO = "^D\x08\x08"


def sections(text):
    """印（==nwc-roll <節>）ごとの行と、節ごとの終了コード（==nwc-rc <数>）。印の外の行（Session Manager の案内）は捨てる"""
    secs, rcs, cur = {}, {}, None
    for raw in text.replace(SCRIPT_EOF_ECHO, "").splitlines():
        line = raw.rstrip("\r")
        m = re.match(r"^==nwc-roll (\S+)\s*$", line)
        if m:
            cur = m.group(1)
            secs.setdefault(cur, [])
            continue
        m = re.match(r"^==nwc-rc (\d+)\s*$", line)
        if m and cur is not None:
            rcs[cur] = int(m.group(1))
            continue
        if cur is not None:
            secs[cur].append(line)
    return secs, rcs


def head(text, n=3):
    """印が無いとき（ECS Exec がつながらない、など）に理由として見せる先頭の数行"""
    lines = [l.rstrip("\r") for l in text.replace(SCRIPT_EOF_ECHO, "").splitlines()
             if l.strip() and not l.startswith(("The Session Manager plugin", "Starting session", "Exiting session"))]
    return " / ".join(lines[:n]) or "（出力が空）"


def need(secs, rcs, text, names):
    """names の節が全部あり、終了コードが付いているものは 0。足りなければ理由"""
    broken = next((l.strip() for l in text.replace(SCRIPT_EOF_ECHO, "").splitlines() if "Cannot perform start session" in l), None)
    if broken:
        reason = f"ECS Exec のセッションを始められない（{broken[:200]}）"
        if "EOF" in broken:
            reason += "。標準入力が端末でないと EOF で切れる。端末から打つか、待たずに一度に入れ替えるなら OSS_ROLL=0"
        return reason
    if not secs:
        return f"ECS Exec の出力に印が無い（{head(text)}）"
    for name in names:
        if name not in secs:
            return f"ECS Exec の出力に {name} の節が無い（{head(text)}）"
        if rcs.get(name, 0) != 0:
            return f"{name} のコマンドが {rcs[name]} で終わった（{' / '.join(l for l in secs[name] if l.strip())[:300]}）"
    return None


def kafka(text, nodes):
    secs, rcs = sections(text)
    reason = need(secs, rcs, text, ("brokers", "urp", "quorum"))
    if reason:
        return None, reason
    for name in ("brokers", "urp", "quorum"):
        # TimeoutException のように語の後ろに付くので、頭の \b は付けない
        bad = [l for l in secs[name] if re.search(r"(Error|Exception)\b", l)]
        if bad:
            return None, f"{name} にエラーが出た（{bad[0].strip()[:200]}）"
    unfenced = set()
    for line in secs["brokers"]:
        m = re.search(r"\(id: (\d+) rack: .*? isFenced: (true|false)\)", line)
        if m and m.group(2) == "false":
            unfenced.add(m.group(1))
    if unfenced != set(nodes):
        return None, f"fenced でない broker が {sorted(unfenced) or 'ない'}（{sorted(nodes)} がそろうのを待つ）"
    urp = [l for l in secs["urp"] if "Partition:" in l]
    if urp:
        return None, f"複製が足りないパーティションが {len(urp)} 個（{urp[0].strip()[:120]} …）"
    rows = [l.split() for l in secs["quorum"] if l.strip()]
    if not rows or rows[0][:1] != ["NodeId"] or not {"Lag", "Status"} <= set(rows[0]):
        return None, f"kafka-metadata-quorum.sh の表の見出しが読めない（{' '.join(rows[0]) if rows else '空'}）"
    col = {c: i for i, c in enumerate(rows[0])}
    voters, leaders = {}, []
    for r in rows[1:]:
        if len(r) < len(col):
            continue
        node, status = r[col["NodeId"]], r[col["Status"]]
        if status in ("Leader", "Follower"):
            voters[node] = int(r[col["Lag"]]) if r[col["Lag"]].lstrip("-").isdigit() else -1
            if status == "Leader":
                leaders.append(node)
    if set(voters) != set(nodes):
        return None, f"controller の Leader / Follower が {sorted(voters) or 'ない'}（{sorted(nodes)} がそろうのを待つ）"
    behind = {n: lag for n, lag in voters.items() if not 0 <= lag <= ROLL_KAFKA_MAX_LAG}
    if behind:
        return None, f"controller の遅れ（Lag）が {ROLL_KAFKA_MAX_LAG} を超える台がある（{behind}）"
    if len(leaders) != 1:
        return None, f"controller の Leader が 1 台でない（{leaders}）"
    return leaders[0], None


def opensearch(text, nodes):
    secs, rcs = sections(text)
    if "nopass" in secs:
        return None, "ECS Exec のシェルに OPENSEARCH_INITIAL_ADMIN_PASSWORD が無い（タスクの secrets の環境変数が見えない）"
    reason = need(secs, rcs, text, ("health", "manager"))
    if reason:
        return None, reason
    body = "".join(secs["health"]).strip()
    try:
        health = json.loads(body)
    except ValueError:
        return None, f"_cluster/health を読めない（{body[:200] or '空'}）"
    if not isinstance(health, dict) or health.get("status") != "green" or health.get("number_of_nodes") != len(nodes):
        h = health if isinstance(health, dict) else {}
        return None, (f"status={h.get('status')} number_of_nodes={h.get('number_of_nodes')} "
                      f"unassigned_shards={h.get('unassigned_shards')} initializing_shards={h.get('initializing_shards')}"
                      f"（green と {len(nodes)} 台を待つ）")
    names = [l.strip() for l in secs["manager"] if l.strip()]
    leader = names[0][len("opensearch-"):] if names and names[0].startswith("opensearch-") else None
    if leader not in nodes:
        return None, f"cluster manager が台のどれでもない（{names[0] if names else '空'}）"
    return leader, None


def plan(text, kind):
    changes = json.loads(text).get("resource_changes") or []
    keys = []
    for rc in changes:
        if rc.get("mode") != "managed" or rc.get("type") != "aws_ecs_service" or rc.get("name") != kind:
            continue
        actions = set(rc.get("change", {}).get("actions") or [])
        if "update" in actions or {"delete", "create"} <= actions:
            keys.append(str(rc.get("index")))
    return sorted(keys)


def main(argv):
    if len(argv) >= 2 and argv[0] == "plan" and argv[1] in ("kafka", "opensearch"):
        print(" ".join(plan(sys.stdin.read(), argv[1])))
        return 0
    if len(argv) >= 2 and argv[0] in ("kafka", "opensearch"):
        leader, reason = (kafka if argv[0] == "kafka" else opensearch)(sys.stdin.read(), argv[1:])
        print(leader if reason is None else reason)
        return 0 if reason is None else 1
    print("使い方: roll_health.py plan <kafka|opensearch> / roll_health.py <kafka|opensearch> <台…>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
