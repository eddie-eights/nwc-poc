# Cycle 008 aws-verification-bugs 設計: AWS 検証で見つけた不具合 3 件を直す

PM(fable-5.1) / effort: high

この文書は現行の設計だけを書く。質問は無かった（ユーザーが判断を PM に委ねた 2026-10-08）ので design-log.md は作らない。

## 背景

2026-10-08 のマネージド版の AWS 検証（`docs/verification/20261008-managed-aws.md`）と、同日の `fix/neo4j-id-labels` で、動作に効く不具合が 3 件残った。どれも互いに独立だが、全部「グラフと通知の経路」なので 1 サイクルに束ねる（依存の無いものを同じエンジニアに渡すのは、ファイルが `app/graph/` と `app/agentcore/graph.py` と `app/grafana/` で重ならず、まとめて 1 本の branch で済むため）。

| # | BACKLOG | 症状 | 影響 |
|---|---|---|---|
| A | 34 | Grafana の trap ルール `nwc-trap` が Grafana 13.2.2 で `bucket budget out of bounds: terms aggregations would produce up to 13600 buckets per time bucket, leaving fewer than 20 time buckets within the 65535 bucket limit` になり、起動からずっと `Normal (Error, KeepLast)` | Grafana 側の trap のアラートが一度も出ない。Splunk 側の trap は通る |
| B | 35 | graph-status の Lambda が、同じ秒に resolved が 2 件届くと Neptune Analytics の `ExecuteQuery` が `ConflictException`（concurrent operations）で落ちる | Lambda の非同期の再試行で 54 秒後に反映された。データは失われないが status が 1 分近く遅れる |
| C | 7 | Neo4j（OSS 版）で `MATCH (n) WHERE n.registered = false`（count と seed）と `MATCH (n:interface) WHERE n.device_id = $id`（remove_device）が全走査 | 頂点が増えると seed と count が遅くなる。Neptune には関係ない |

## 設計方針

### A. trap ルールの terms を小さくし、時間の区切りを固定する

- 原因は、`grafana-opensearch-datasource` が評価の前に「terms の size の積 × 時間の区切り」を 65535 と比べる検査（bucket budget）で、いまの `sysName` 50 × `oid` 20 と `interval: auto` がそれを超えること。**13600 の計算式は推測しない。** 実装の最初にプラグインのソース（GitHub `grafana/opensearch-datasource` の `bucket budget` を含む Go か TypeScript のファイル）を読み、式を `build.md` に引用する。`tests/test_alerts.py:893` のいまの式 `n[0] * n[1] * 20 <= 65535`（50 × 20 × 20 = 20000 で通ってしまう）はプラグインの式と合っていないので、**テストの式をプラグインの式に合わせる**
- 直し方は 2 つを同時にやる。(1) `date_histogram` の `interval` を `auto` から固定（10 分の範囲で 20 区切りになる `30s`）にして、時間の区切りの数を決める。(2) terms の size を、プラグインの式で 65535 に収まる値に下げる（候補は sysName 20 × oid 10。lab は機器 6 台、除外後の trap の OID の種類は数個なので十分）。式を読んだ結果 (1) だけで収まるなら (2) は不要だが、その場合も size は lab の規模に合わせて下げてよい
- `field` の並び `tags.sysName.keyword` → `tags.oid.keyword` → `@timestamp` と、`noDataState: OK` / `execErrState: KeepLast`、SNS の contact point は変えない

### B. ConflictException を関数の中でやり直す

- `app/graph/status_handler.py` の `handler()` の `r = apply(a)` を、`ClientError` の `Error.Code == "ConflictException"` のときだけ短い待ちで打ち直す。待ちは新しい定数 `CONFLICT_WAITS = (0.5, 1.0, 2.0)`（1 回目と合わせて 4 回、追加の待ちは最大 3.5 秒 / 件）。それ以外の例外はいままでどおり 1 件を errors に積んで次へ進み、最後に `RuntimeError`
- `apply()` は `graph.set_status` / `set_layer_status` を呼ぶだけで、同じ通知を 2 回当てても同じ結果になる（status を置くだけ）ので打ち直してよい
- 時間の予算: docstring（`status_handler.py:18-30`）の Firehose の最長 27.6 秒 + Neptune の待ちに、1 件あたり最大 3.5 秒が足される。timeout 60 秒と `NEPTUNE_CONFIG` / `RETRY_WAITS` は変えない（2026-10-05 のユーザー決定）。docstring の「Neptune の途中で timeout したときも…」の段落に、ConflictException は関数の中で最大 3.5 秒 × 件数だけ待ってから諦めることを 1〜2 文で足す
- `ConflictException` が `CONFLICT_WAITS` を使い切っても続くときは、いままでどおり errors に積んで最後に落とす（Lambda の非同期の再試行に任せる）
- Neo4j（OSS 版）のドライバは `max_transaction_retry_time` で自分で打ち直すので、ここでは `ClientError` だけを見る

### C. Neo4j に索引を張り、ラベル無しの走査をラベル付きに分ける

- `app/agentcore/graph.py` の `_neo4j_schema()`（171 行目）で、一意制約と一緒に次を張る（`IF NOT EXISTS`。`SCHEMA_TTL` の仕組みはそのまま）
  - `CREATE INDEX nwc_{label}_registered IF NOT EXISTS FOR (n:{label}) ON (n.registered)` を `("device", "interface", "change") + LAYER_LABELS` の全ラベル
  - `CREATE INDEX nwc_interface_device_id IF NOT EXISTS FOR (n:interface) ON (n.device_id)`
- Neo4j はラベルの無い property の索引を持てないので、ラベル無しの 2 つ（`count()` の 316 行目 `MATCH (n) WHERE n.registered = false RETURN count(n)` と 373 行目 `MATCH (n) WHERE n.registered = false RETURN n`）は **Neo4j のときだけ**ラベルごとの問い合わせに分ける（`_lbl` と同じく `BACKEND == "neo4j"` で分岐）。合計と連結は Python 側で足す。Neptune に送る文字列は 1 文字も変えない（`tests/test_graph.py:194,207` が文字列で照合している）
- `remove_device` の 589 行目はラベル付き（`n:interface`）なので文字列は変えず、索引だけで速くなる
- `ops/seed_graph.py` の seed のあと、Neo4j のときだけ `CALL db.prepareForReplanning()` を 1 回呼ぶ（大量投入の直後は統計が古く、索引があっても使わないことがある）。失敗しても seed は失敗にしない（WARNING で出す）

## 実物で確認した契約

- Grafana のエラー文（AWS で 2026-10-08 に観測。`docs/verification/20261008-managed-aws.md:235-243`）: `bucket budget out of bounds: terms aggregations would produce up to 13600 buckets per time bucket, leaving fewer than 20 time buckets within the 65535 bucket limit`
- いまのルール `app/grafana/provisioning/alerting/netops-opensearch.yaml`（`nwc-trap`）: `relativeTimeRange from: 600 / to: 0`、terms `tags.sysName.keyword` `size: '50'`、terms `tags.oid.keyword` `size: '20'`、date_histogram `'@timestamp'` `interval: auto` `min_doc_count: '0'`
- ConflictException の観測: `docs/verification/20261008-managed-aws.md:19,148,241`（「同じ秒に resolved が 2 件届いた」「54 秒後に反映」）
- `handler()` のループ（`app/graph/status_handler.py:183-191`）: `_neptune()` → `apply(a)` を `except Exception` で包み、`errors.append(...)` して `continue`。最後に `raise RuntimeError`
- `app/agentcore/graph.py:184`: `CREATE CONSTRAINT nwc_{label}_id IF NOT EXISTS FOR (n:{_ident(label)}) REQUIRE n.id IS UNIQUE`
- **推測のまま残っているもの**: プラグインの bucket budget の式（上の A で実装の最初に読む）

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `app/grafana/provisioning/alerting/netops-opensearch.yaml` | `nwc-trap` の terms の `size` と date_histogram の `interval`（A） |
| `tests/test_alerts.py:893` | bucket budget の式をプラグインの式に合わせる。`interval` の固定値も見る（A） |
| `app/graph/status_handler.py` | `CONFLICT_WAITS`、`handler()` の打ち直し、docstring の予算（B） |
| `tests/test_sync.py` | ConflictException が 1 回で成功 / 使い切って失敗 / ほかの ClientError は打ち直さない、の 3 件と、予算の式に `CONFLICT_WAITS` を足す（B。`test_sync.py:487` の式を見る） |
| `app/agentcore/graph.py` | `_neo4j_schema()` の索引、`count()` と 373 行目のラベル分け（C） |
| `tests/test_graph.py` | Neo4j のときのラベル分けと索引の DDL。Neptune の文字列は不変のまま通ること（C） |
| `ops/seed_graph.py` | `db.prepareForReplanning()`（C） |
| `docs/cycles/BACKLOG.md` | PM が直す。エンジニアは触らない |

## 再利用するもの

- `_lbl()`（`app/agentcore/graph.py:215`）の「Neo4j のときだけラベルを付ける」分岐
- `_neo4j_schema()` の `SCHEMA_TTL` と `IF NOT EXISTS` の形
- `RETRY_WAITS` の「待ちの tuple を回す」形（`status_handler.py:145`）
- `tests/test_sync.py` の偽の boto クライアントと `test_graph.py` の `reset()` / `calls()`

## 実装ステップ

1. A: プラグインのソースで bucket budget の式を読み、`build.md` に引用。yaml と `test_alerts.py` を直す（commit 1 本）
2. B: `status_handler.py` と `test_sync.py`（commit 1 本）
3. C: `graph.py` と `test_graph.py` と `seed_graph.py`（commit 1 本）
4. `uv sync --group dev --group web` → `bash ops/check.sh` が `すべて通過` / exit 0
5. 手元の確認（下）。できなかったものは `build.md` に「未確認」と書く

## 検証方法

- `bash ops/check.sh` の最後が `すべて通過`、`echo $?` が `0`
- A: `uv run pytest tests/test_alerts.py -q` が通り、新しい式で `size` を元の `50` / `20` に戻すと落ちる（red-green）。手元で `docker compose -f docker/compose/compose.yaml up -d opensearch grafana` を上げ（Mac でも Grafana と OpenSearch は arm64 で動く。Splunk は要らない）、`curl -u admin:<.env の値> http://127.0.0.1:3000/api/prometheus/grafana/api/v1/rules` の `nwc-trap` の `health` が `error` でないこと（`ok` か `nodata`）。上がらなければ未確認と書く
- B: `uv run pytest tests/test_sync.py -q`。偽の Neptune が `ConflictException` を 1 回だけ返すケースで `handler()` が例外にならず結果を返す。4 回返すケースで `RuntimeError` の文に `ConflictException` が入る。`AccessDeniedException` は 1 回で errors に積まれ打ち直さない（`time.sleep` を偽物にして待ちの合計が 3.5 秒であることも見る）
- C: `docker run --rm -d --name nwc-neo4j -p 7687:7687 -e NEO4J_AUTH=none neo4j:2026.09.0-community` を上げ、`GRAPH_BACKEND=neo4j`（変数名は `app/agentcore/graph.py` の `BACKEND` の読み方に合わせる）で `ops/seed_graph.py` を lab の定義で流したあと、`cypher-shell` か Python ドライバで `SHOW INDEXES` に `nwc_interface_device_id` と `nwc_device_registered` が出ること、`PROFILE MATCH (n:interface) WHERE n.device_id = 'a-ce-01' RETURN n` の計画に `NodeIndexSeek` があり `NodeByLabelScan` が無いこと。count の分けた問い合わせも同じ
- AWS では確認しない（このサイクルは手元とテストまで。AWS の確認は次に up.sh を打つときにまとめる。`build.md` に「AWS では未確認」と書く）

## 未確定事項とリスク

1. bucket budget の式がソースから読み取れない、または Grafana 13.2.3 と 13.2.2 で式が違う → 式が分からなければ、`interval` 固定 + size 20 × 10 で手元の Grafana が `error` を出さないことを根拠にし、テストは「size の積 × 20 ≤ 65535 の 1/4」のような保守的な式にして `build.md` に理由を書く
2. `prepareForReplanning()` が Community 版で使えない可能性 → 失敗しても seed を止めない設計なので、使えなければ WARNING のまま。`build.md` に出力を貼る
3. Neo4j の索引を張るのは `_neo4j_schema()` で、タスクが入れ替わると消えてまた張られる（一意制約と同じ）。張るのに失敗しても `_neo4j_schema()` は WARNING で続ける既存の形に合わせる
4. ConflictException の打ち直しで Lambda の 60 秒を超える可能性（通知が 10 件以上まとめて来て全部が衝突するとき）→ 今回は件数の上限を付けない。超えれば Lambda の再試行で今までどおり直る
