# Cycle 008 aws-verification-bugs 実装の記録

## Round 1

実装モデル: claude-opus-5-5 / effort: 既定のまま（PM の依頼で動くエンジニアのセッションで、自分の effort は切り替えていない）

エンジニア1（PM の指示）。ブランチ `fix/aws-verification-bugs`（`docs/cycle-006-design` を merge した 3c99552 から。design.md は a773fc4）。AWS には何も立てていない（PM の指示で AWS の操作はしない）。BACKLOG.md は触っていない。

### commit

| commit | 内容 | 規模 |
|---|---|---|
| d7c1fbf | A: `nwc-trap` の terms の size を 50 / 20 → 10 / 10、date_histogram の interval を auto → 30s。`tests/test_alerts.py` の bucket budget の式をプラグインの式の写し（`terms_buckets` / `bucket_budget_ok`）に替えた | 2 files, +28 −7 |
| a9dcec1 | B: `CONFLICT_WAITS = (0.5, 1.0, 2.0)` と `_apply_retrying`（ConflictException だけ打ち直す）、docstring に 2 行。`tests/test_sync.py` に 3 件と予算の式 | 2 files, +64 −3 |
| 4c35742 | C: `_neo4j_schema` が制約のあとで `_INDEXES`（9 個）を張る、`_unregistered`（Neo4j のときだけラベルごと）を count と seed に使う、`ops/seed_graph.py` の `db.prepareForReplanning()`。`tests/test_graph.py` と `tests/test_oss.py` | 4 files, +163 −16 |
| e517ae5 | セルフレビューの直し（C）: `tests/test_graph.py` の seed_graph.py の失敗のケースを、botocore の ClientError ではなく偽の Neo4jError で投げる | 1 file, +17 −8 |

### A のプラグインの式

grafana-opensearch-datasource v2.34.4（手元の Grafana 13.2.2 に入っている版。`gh api repos/grafana/opensearch-datasource/contents/<path>?ref=v2.34.4` で取った）。

- `pkg/opensearch/lucene_handler.go:69` 検査に入るのは `hasAutoDateHistogram(q.BucketAggs) && hasTermsAgg(q.BucketAggs)` のときだけ。`hasAutoDateHistogram`（:189-191）は date_histogram の interval が `"auto"`（未設定も auto）のとき真
- `lucene_handler.go:136-142` `termsBucketProduct` は terms ごとの見積もりを掛け合わせる（65535 で頭打ち）
- `lucene_handler.go:160-165` `termsBucketEstimate`: `if shards <= 1` なら `size`、それ以外は `shardSize := int64(float64(size)*1.5) + 10` で `return shards * shardSize`
- `pkg/tsdb/interval.go:22,26,30` `defaultMaxBuckets = 65535`、`bucketHeadroomPercent = 90`、`minTimeBuckets = 20`
- `interval.go:118-120` `budget := maxBuckets * bucketHeadroomPercent / 100`、`timeTarget := budget / termsProduct`、`if timeTarget < minTimeBuckets` で `bucket budget out of bounds`
- `pkg/opensearch/shard_count.go:12` shard 数は 5 分キャッシュ、:40 と :57 で index が無い・取れないときは 1 として扱う

当てはめ: 前の 50 × 20 は shard 2 で (2 × (75 + 10)) × (2 × (30 + 10)) = 170 × 80 = 13600、58981 / 13600 = 4 < 20 で AWS のエラー文の 13600 と合う（shard 1 なら 1000 で通るので、AWS の index は 2 shard と逆算。AWS の shard 数そのものは見ていない）。新しい 10 × 10 は shard 2 で 50 × 50 = 2500、58981 / 2500 = 23 ≥ 20（interval が auto に戻っても通る）。interval を 30s に固定したので検査そのものに入らず、10 分の区切りは端を含めて 21 個、2500 × 21 = 52500 ≤ 65535。

### 設計から逸脱した点

| # | design | 実装 | 理由 |
|---|---|---|---|
| 1 | A の size の候補は sysName 20 × oid 10 | 10 × 10 | 20 × 10 は shard 2 で (2 × 40) × (2 × 25) = 4000、58981 / 4000 = 14 < 20 で、interval が auto に戻ると落ちる。trap を送るのは SR Linux の 6 台（`app/containerlab/gen_lab.py:20`）、グラフの機器は 8 台（下の count() の devices）で、どちらも 10 に収まる |
| 2 | 検証は `uv run pytest tests/X.py -q` | `uv run --group dev python tests/X.py` | このリポジトリのテストは pytest を使わない素のスクリプトで、pytest も入っていない（`ops/check.sh` も同じ形で流す） |
| 3 | A の手元の確認は rules API の `health` が `error` でないこと | `/api/ds/query` にルールの model を投げてプラグインの応答を見た | `execErrState: KeepLast` のため、前のルールも `health=ok`（`Normal (Error, KeepLast)`）になりエラーを隠す。rules API だけでは前後の違いが出ない |
| 4 | 変更対象に `tests/test_oss.py` が無い | 変えた | OSS 版の golden の照合が count / seed の Neo4j の 1 文を前提にしていたので、ラベルごとの何文か（`_SPLIT4`）に対応させた。golden のファイルは変えていない。索引の順番・Neo4jError・DriverError の検査を足した |
| 5 | count と seed の未登録の読みを Neo4j のときラベルごとに | count は全 8 ラベル、seed は device と interface の 2 ラベルだけ | seed() が置き換えるのはこの 2 つで、上の層の未登録は seed_layers が別に読む。Neptune に送る文字列はどちらも変えていない |
| 6 | C の確認の機器 `a-ce-01` | `dc1-leaf-01` | `a-ce-01` は lab の定義に無い |
| 7 | seed_graph.py のテストの置き場は書いていない | `tests/test_graph.py` が `runpy` で seed_graph.py を流す | `/etc/<prefix>-web.env` の open と graph.query を偽物にして、呼ぶ・失敗で止まらない・Neptune では呼ばない、の 3 件 |

### 検証（2〜5 と 7 は 4c35742 のあと、未コミットの変更が無い状態で取った。セルフレビューの直し e517ae5 は `tests/test_graph.py` だけなので、1 と 6 をそのあとで取り直した）

#### 1. `uv sync --group dev --group web` → `bash ops/check.sh`

```
HEAD e517ae5 / 未コミット: [?? docs/cycles/008-aws-verification-bugs/build.md]
Resolved 86 packages in 3ms
Audited 82 packages in 39ms
--- ops/check.sh の最後 2 行

すべて通過
exit=0
```

check.sh の全 2579 行のうち、段の見出しと各テストの結果行（行番号: 中身）:

```
2: == 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
5: == 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
25: == 3. ops スクリプトの構文
28: == 4. 模擬テスト
167: 通過 138 / 失敗 0
658: 通過 489 / 失敗 0
1230: 通過 158 / 失敗 0
1234: 通過 3 / 失敗 0
1314: 通過 78 / 失敗 0
1324: 通過 7 / 失敗 0
1409: 通過 84 / 失敗 0
1487: 通過 77 / 失敗 0
1730: 通過 171 / 失敗 0
1875: 通過 144 / 失敗 0
1942: 通過 66 / 失敗 0
2018: 通過 75 / 失敗 0
2134: 通過 100 / 失敗 0
2577: 通過 325 / 失敗 0
2579: すべて通過
```

#### 2. 変更したテスト（`uv run --group dev python tests/<名前>.py`）

```
HEAD 4c35742
tests/test_alerts.py rc=0 通過 138 / 失敗 0
tests/test_sync.py rc=0 通過 100 / 失敗 0
tests/test_graph.py rc=0 通過 78 / 失敗 0
tests/test_oss.py rc=0 通過 171 / 失敗 0
tests/test_oss_ops.py rc=0 通過 144 / 失敗 0
```

#### 3. A: 退行を入れて test_alerts.py が落ちること（scratchpad の `a_red.sh`。yaml を書き換えて流し、元に戻す）

```
 size: '50'  size: '20'  interval: 30s 
== size を 50 / 20 に戻す（interval は 30s のまま）
AssertionError: trap の時間の区切りは固定の 30s（10 分を 20 個。auto だと terms の見積もりが shard の数で変わり、プラグインが評価をエラーにしうる）。terms は auto だったとしても shard 2 で通る大きさで、固定の区切り 21 個（端を含む）を掛けても 65535 に収まる
 size: '10'  size: '10'  interval: auto 
== interval を auto に戻す（size は 10 / 10 のまま）
AssertionError: trap の時間の区切りは固定の 30s（10 分を 20 個。auto だと terms の見積もりが shard の数で変わり、プラグインが評価をエラーにしうる）。terms は auto だったとしても shard 2 で通る大きさで、固定の区切り 21 個（端を含む）を掛けても 65535 に収まる
 size: '20'  size: '10'  interval: 30s 
== design の候補 20 / 10（interval は 30s）
AssertionError: trap の時間の区切りは固定の 30s（10 分を 20 個。auto だと terms の見積もりが shard の数で変わり、プラグインが評価をエラーにしうる）。terms は auto だったとしても shard 2 で通る大きさで、固定の区切り 21 個（端を含む）を掛けても 65535 に収まる
```

#### 4. A: 手元の Grafana（Grafana 13.2.2、プラグイン 2.34.4。OpenSearch の `snmp-logs` を shard 2 で作り trap を 1 件入れた。パスワードはその場で作った試し用の値で `.env` は読んでいない。終わったら `down -v`）

前のルール（50 × 20 / auto）と新しいルール（10 × 10 / 30s）の model を `/api/ds/query` に直接投げた（`a_grafana2.sh`）:

```
 Container nwc-avb-opensearch-1 Started 
{"acknowledged":true,"shards_acknowledged":true,"index":"snmp-logs"}
{"snmp-logs":{"settings":{"index":{"number_of_shards":"2"}}}}
doc created
 Container nwc-avb-grafana-1 Started 
前のルール（50 × 20 / auto）: HTTP 400 status=500 error='bucket budget out of bounds: terms aggregations would produce up to 13600 buckets per time bucket, leaving fewer than 20 time buckets within the 65535 bucket limit; reduce the terms size or narrow the time range' frames=0 count合計=0
新しいルール（10 × 10 / 30s）: HTTP 200 status=200 error=None frames=1 count合計=1
trap のルール: ok inactive None [('Normal (Error, KeepLast)', None)]
grafana-1  | logger=ngalert.scheduler rule_uid=nwc-trap org_id=1 version=1 fingerprint=684b1b4cc4308b64 now=2026-10-08T11:50:30Z rule_uid=nwc-trap org_id=1 t=2026-10-08T11:50:31.076047215Z level=error msg="Failed to evaluate rule" attempt=2 max_attempts=3 next_attempt_in=1.361992725s error="the result-set has errors that can be retried: [sse.dataQueryError] failed to execute query [A]: bucket budg
grafana-1  | logger=plugin.grafana-opensearch-datasource t=2026-10-08T11:50:31.51156034Z level=error msg="Partial data response error" dsName="OpenSearch (logs)" dsUid=aoss-logs error="bucket budget out of bounds: terms aggregations would produce up to 13600 buckets per time bucket, leaving fewer than 20 time buckets within the 65535 bucket limit; reduce the terms size or narrow the time range" pl
== 片付け
 Network nwc-local Removing 
 Volume nwc-avb_opensearch Removed 
 Volume nwc-avb_grafana Removed 
 Network nwc-local Removed 
残ったコンテナ 0 / volume 0
```

rules API（ルールの yaml を差し替えて 2 回上げた。`a_grafana.sh`）。前のルールも `ok` で、上の 3 のとおり KeepLast がエラーを隠す:

```
== OpenSearch
 Container nwc-avb-opensearch-1 Starting 
 Container nwc-avb-opensearch-1 Started 
{"acknowledged":true,"shards_acknowledged":true,"index":"snmp-logs"}
{"snmp-logs":{"settings":{"index":{"number_of_shards":"2"}}}}
== 前のルール（50 × 20 / interval auto）
 Container nwc-avb-grafana-1 Starting 
 Container nwc-avb-grafana-1 Started 
grafana 13.2.2
opensearch plugin 2.34.4
doc created
ok inactive 2026-10-08T11:47:00Z 
== 新しいルール（10 × 10 / interval 30s）
 Container nwc-avb-grafana-1 Starting 
 Container nwc-avb-grafana-1 Started 
grafana 13.2.2
opensearch plugin 2.34.4
doc created
ok firing 2026-10-08T11:48:00Z 
== 片付け
 Network nwc-local Removing 
 Volume nwc-avb_grafana Removed 
 Volume nwc-avb_opensearch Removed 
 Network nwc-local Removed 
0
0
```

#### 5. B: 退行を入れて test_sync.py が落ちること（`b_red.py`。status_handler.py を書き換えて流し、元に戻す）

```
== 打ち直さない（apply を直に呼ぶ）: rc=1
   ok Neptune に書けなくても Firehose には送り（put_record_batch は呼ばれる）、Neptune の失敗だけで最後に RuntimeError
   ok Neptune が 1 件目から落ちても、行は全部その前に送ってあり、残りの通知も書いてから最後に RuntimeError
       raise OSError("neptune unreachable")
   OSError: neptune unreachable
       raise RuntimeError("; ".join(errors)[:2000])
   RuntimeError: neptune {"source": "grafana", "status": "resolved", "device_id": "dc1-leaf-01", "kind": "link_down", "target": "eth1"}: ClientError: An error occurred (ConflictException) when calling th
== どの ClientError も打ち直す: rc=1
   ok Neptune が 1 件目から落ちても、行は全部その前に送ってあり、残りの通知も書いてから最後に RuntimeError
   ok ConflictException が続けば CONFLICT_WAITS（0.5・1・2 秒。合わせて 3.5 秒）を待って 4 回で諦め、残りの通知も書いてから RuntimeError（文に ConflictException）
       raise OSError("neptune unreachable")
   OSError: neptune unreachable
       check("ほかの ClientError（AccessDeniedException）は打ち直さず、1 回で errors に積んで最後に RuntimeError",
   AssertionError: ほかの ClientError（AccessDeniedException）は打ち直さず、1 回で errors に積んで最後に RuntimeError
== 待ちが 1 回少ない（0.5, 1.0）: rc=1
   ok Neptune に書けなくても Firehose には送り（put_record_batch は呼ばれる）、Neptune の失敗だけで最後に RuntimeError
   ok Neptune が 1 件目から落ちても、行は全部その前に送ってあり、残りの通知も書いてから最後に RuntimeError
       raise OSError("neptune unreachable")
   OSError: neptune unreachable
       check("ConflictException が続けば CONFLICT_WAITS（0.5・1・2 秒。合わせて 3.5 秒）を待って 4 回で諦め、残りの通知も書いてから RuntimeError（文に ConflictException）",
   AssertionError: ConflictException が続けば CONFLICT_WAITS（0.5・1・2 秒。合わせて 3.5 秒）を待って 4 回で諦め、残りの通知も書いてから RuntimeError（文に ConflictException）
== 使い切ったあと投げない（None を返す）: rc=1
   ok Neptune に書けなければ最後に RuntimeError で落とす（Lambda の非同期の再試行に任せる。やり直しの合間に後の通知が来ると古い値に戻る）
   ok Neptune に書けなくても Firehose には送り（put_record_batch は呼ばれる）、Neptune の失敗だけで最後に RuntimeError
   ok Neptune が 1 件目から落ちても、行は全部その前に送ってあり、残りの通知も書いてから最後に RuntimeError
       raise OSError("neptune unreachable")
   OSError: neptune unreachable
   AttributeError: 'NoneType' object has no attribute 'get'
元に戻した: True
```

#### 6. C: 退行を入れて test_graph.py / test_oss.py が落ちること（`c_red.py`。graph.py / seed_graph.py を書き換えて流し、元に戻す。e517ae5 の commit の直前に、commit したのと同じ中身の tests/test_graph.py で取った。12 通り目の「botocore の ClientError だけ捕まえる」はセルフレビューで足した）

```
== 索引を張らない
   tests/test_graph.py: rc=1 AssertionError: neo4j: スキーマは制約のあとに全ラベルの registered と interface の device_id の索引を張り（IF NOT EXISTS）、SCHEMA_TTL の内は張り直さない
   tests/test_oss.py: rc=1 AssertionError: neo4j: 制約のすぐあとに、全ラベルの registered と interface の device_id の索引を 1 度だけ張る（IF NOT EXISTS）
== device_id の索引が無い
   tests/test_graph.py: rc=1 AssertionError: neo4j: スキーマは制約のあとに全ラベルの registered と interface の device_id の索引を張り（IF NOT EXISTS）、SCHEMA_TTL の内は張り直さない
   tests/test_oss.py: rc=1 AssertionError: neo4j: 制約のすぐあとに、全ラベルの registered と interface の device_id の索引を 1 度だけ張る（IF NOT EXISTS）
== 索引の Neo4jError を上げる
   tests/test_graph.py: rc=0 通過 78 / 失敗 0
   tests/test_oss.py: rc=1 Neo4jError: Index already exists with different name
== 索引の前に張ったことにする
   tests/test_graph.py: rc=0 通過 78 / 失敗 0
   tests/test_oss.py: rc=1 AssertionError: neo4j: 索引を張る途中のドライバの失敗（DriverError）もそのまま上げてクエリを打たず、張ったことにしない（次のクエリでまた張る）
== count の未登録をラベル無しのまま
   tests/test_graph.py: rc=1 AssertionError: neo4j: count は未登録の頂点をラベルごとに数えて足す（ラベル無しの MATCH (n) は registered の索引を使えず全部の頂点を読む）
   tests/test_oss.py: rc=1 AssertionError: neo4j: 送るのは golden（Neptune の openCypher）を _dialect で直し、id で引く頂点にラベル（_lbl）を足したものだけで、パラメータも順番も同じ（centrality より前の全関数。_REWRITE4 の 1 文だけは決めた書き方に替え、_SPLIT4 の 2 文はラベルごとに分け
== seed の読みをラベル無しのまま
   tests/test_graph.py: rc=1 AttributeError: 'int' object has no attribute 'get'
   tests/test_oss.py: rc=1 AssertionError: neo4j: 送るのは golden（Neptune の openCypher）を _dialect で直し、id で引く頂点にラベル（_lbl）を足したものだけで、パラメータも順番も同じ（centrality より前の全関数。_REWRITE4 の 1 文だけは決めた書き方に替え、_SPLIT4 の 2 文はラベルごとに分け
== seed が全ラベルを読む
   tests/test_graph.py: rc=1 AttributeError: 'int' object has no attribute 'get'
   tests/test_oss.py: rc=1 AssertionError: neo4j: 送るのは golden（Neptune の openCypher）を _dialect で直し、id で引く頂点にラベル（_lbl）を足したものだけで、パラメータも順番も同じ（centrality より前の全関数。_REWRITE4 の 1 文だけは決めた書き方に替え、_SPLIT4 の 2 文はラベルごとに分け
== Neptune でもラベルごとに分ける
   tests/test_graph.py: rc=1 AssertionError: count は登録済みの機器・IF・回線、上の層の頂点と辺、未登録の頂点を数える
   tests/test_oss.py: rc=1 AssertionError: 環境変数が無いとき、graph.py が出す openCypher とパラメータは切り替えを入れる前と 1 文字も違わない（golden と同じ）
== seed_graph が prepareForReplanning を呼ばない
   tests/test_graph.py: rc=1 AssertionError: neo4j: ops/seed_graph.py は投入（seed）を全部送ったあとで 1 度だけ db.prepareForReplanning を呼ぶ（統計を取り直して索引を使う計画にする）
   tests/test_oss.py: rc=0 通過 171 / 失敗 0
== seed_graph が失敗を捕まえない
   tests/test_graph.py: rc=1 AssertionError: neo4j: db.prepareForReplanning が無い・失敗しても（ドライバの Neo4jError）、seed_graph.py は WARNING を出すだけで止まらない（投入は済んでいる）
   tests/test_oss.py: rc=0 通過 171 / 失敗 0
== seed_graph が botocore の ClientError だけ捕まえる
   tests/test_graph.py: rc=1 AssertionError: neo4j: db.prepareForReplanning が無い・失敗しても（ドライバの Neo4jError）、seed_graph.py は WARNING を出すだけで止まらない（投入は済んでいる）
   tests/test_oss.py: rc=0 通過 171 / 失敗 0
== seed_graph が Neptune でも呼ぶ
   tests/test_graph.py: rc=1 AssertionError: Neptune では seed_graph.py は db.prepareForReplanning を呼ばない
   tests/test_oss.py: rc=0 通過 171 / 失敗 0
元に戻した: True
git status --porcelain -uall: [ M tests/test_graph.py
?? docs/cycles/008-aws-verification-bugs/build.md]
```

「索引の Neo4jError を上げる」と「索引の前に張ったことにする」は test_graph.py では落ちず、test_oss.py が縛る。seed_graph.py の 4 つは test_graph.py だけが縛る。

#### 7. C: 手元の Neo4j（`docker run --rm -d --name nwc-neo4j -p 7687:7687 -e NEO4J_AUTH=none neo4j:2026.09.0-community`、ドライバ 6.3.1。`ops/seed_graph.py` を lab の定義で `GRAPH_REPLACE=1`。ドライバの「relationship type does not exist」の通知は空の DB への問い合わせで出るものなので除いた。終わったら `docker rm -f -v`）

```
2026-10-08 12:14:39.629+0000 INFO  Started.
== ops/seed_graph.py（GRAPH_REPLACE=1、PARAM_PREFIX は空なので SSM は引かない）
Neo4j に lab の定義（app/containerlab/lab_topology.py） を入れた（8 台 / 12 本 / 上の層 62 頂点）: {'devices': 8, 'interfaces': 38, 'links': 12, 'layers': 62, 'layer_edges': 84, 'unregistered': 0}
== 索引と計画
SHOW INDEXES（nwc_ で始まるもの 17 個）
   nwc_bgp_session_id RANGE ['bgp_session'] ['id'] ONLINE
   nwc_bgp_session_registered RANGE ['bgp_session'] ['registered'] ONLINE
   nwc_change_id RANGE ['change'] ['id'] ONLINE
   nwc_change_registered RANGE ['change'] ['registered'] ONLINE
   nwc_device_id RANGE ['device'] ['id'] ONLINE
   nwc_device_registered RANGE ['device'] ['registered'] ONLINE
   nwc_ethernet_segment_id RANGE ['ethernet_segment'] ['id'] ONLINE
   nwc_ethernet_segment_registered RANGE ['ethernet_segment'] ['registered'] ONLINE
   nwc_evpn_instance_id RANGE ['evpn_instance'] ['id'] ONLINE
   nwc_evpn_instance_registered RANGE ['evpn_instance'] ['registered'] ONLINE
   nwc_interface_device_id RANGE ['interface'] ['device_id'] ONLINE
   nwc_interface_id RANGE ['interface'] ['id'] ONLINE
   nwc_interface_registered RANGE ['interface'] ['registered'] ONLINE
   nwc_ip_interface_id RANGE ['ip_interface'] ['id'] ONLINE
   nwc_ip_interface_registered RANGE ['ip_interface'] ['registered'] ONLINE
   nwc_isis_adjacency_id RANGE ['isis_adjacency'] ['id'] ONLINE
   nwc_isis_adjacency_registered RANGE ['isis_adjacency'] ['registered'] ONLINE
count(): {'devices': 8, 'interfaces': 38, 'links': 12, 'layers': 62, 'layer_edges': 84, 'unregistered': 0}
CALL db.prepareForReplanning(): [] r
PROFILE MATCH (n:interface) WHERE n.device_id = 'dc1-leaf-01' RETURN n
  rows=5 [{'n': <Node element_id='4:30b5ba86-db37-4369-a76a-9bc8e63c34b1:12' labels=frozenset({'interface'}) properties={'address': '203.0.113.31', 'device_id': 'dc1-leaf-01', 'name': 'mgmt0', 'id': 'dc1-leaf-01#mgmt0'}>}] ops=['ProduceResults', 'NodeIndexSeek']
PROFILE MATCH (n:`device`) WHERE n.registered = false RETURN count(n) AS n
  rows=1 [{'n': 0}] ops=['ProduceResults', 'EagerAggregation', 'NodeIndexSeek']
PROFILE MATCH (n:`interface`) WHERE n.registered = false RETURN n
  rows=0 [] ops=['ProduceResults', 'NodeIndexSeek']
PROFILE MATCH (n) WHERE n.registered = false RETURN count(n) AS n
  rows=1 [{'n': 0}] ops=['ProduceResults', 'EagerAggregation', 'Filter', 'AllNodesScan']
== 未登録の機器を 1 つ作る（set_status が知らない機器の頂点を registered = false で作る）
set_status: {'device_id': 'zz-ce-09', 'status': 'ALARM', 'updated': 0, 'unregistered': True}
count(): {'devices': 8, 'interfaces': 38, 'links': 12, 'layers': 62, 'layer_edges': 84, 'unregistered': 1}
CALL db.prepareForReplanning(): [] r
PROFILE MATCH (n:`device`) WHERE n.registered = false RETURN count(n) AS n
  rows=1 [{'n': 1}] ops=['ProduceResults', 'EagerAggregation', 'NodeIndexSeek']
== remove_device（interface を device_id で消す）
remove_device: {'removed': 'dc1-leaf-01'} count(): {'devices': 7, 'interfaces': 33, 'links': 9, 'layers': 62, 'layer_edges': 80, 'unregistered': 1}
set_status: {'device_id': 'dc1-leaf-01', 'if_name': 'ethernet-1/1', 'status': 'DOWN', 'updated': 0, 'unregistered': True}
count(): {'devices': 7, 'interfaces': 33, 'links': 9, 'layers': 62, 'layer_edges': 80, 'unregistered': 3}
未登録: [('dc1-leaf-01', 'device', None), ('dc1-leaf-01#ethernet-1/1', 'interface', 'DOWN'), ('zz-ce-09', 'device', 'ALARM')]
== ops/seed_graph.py（GRAPH_REPLACE=1）
Neo4j に lab の定義（app/containerlab/lab_topology.py） を入れた（8 台 / 12 本 / 上の層 62 頂点）: {'devices': 8, 'interfaces': 38, 'links': 12, 'layers': 62, 'layer_edges': 84, 'unregistered': 1}
未登録: [('zz-ce-09', 'device')]
dc1-leaf-01: [(None, None)] ethernet-1/1: [('DOWN', None)]
残った nwc-neo4j: 0
```

#### 8. AWS

AWS では未確認（design の検証方法どおり。次に up.sh を打つときにまとめる）。

### 未確認の項目

- AWS の全部: A の AOSS の index の shard 数と Grafana のルールの状態、B の実際の ConflictException の打ち直し、C の ECS 上の Neo4j
- A を AWS で確かめるときの判定: ルールは `execErrState: KeepLast` なので、評価エラーでも rules API の `health` は `ok` のまま（検証 4 の前のルール）。design の「`health` が `error` でないこと」では直っていなくても合格に見える。rules API の `alerts[].state` に `(Error` が無いこと、または `/api/ds/query` にルールの model を投げて HTTP 200 を見る
- 認証ありの Neo4j（AWS の OSS 版）で `db.prepareForReplanning()` が通るか。通らなくても seed は止めず WARNING だけ（テストは偽の Neo4jError で確かめた。本物のドライバの型では未確認）
- プラグインが 2.34.4 より新しい版になったときの式。`docker/images/grafana/Dockerfile:12` はプラグインの版を固定せず、イメージのタグ（`ops/up.sh:637` の dir_tag）にも版が入らないので、AWS ではビルドした日の最新で動く。2026-10-08 の最新は v2.34.4（`gh api repos/grafana/opensearch-datasource/releases/latest` → `v2.34.4 2026-09-15T18:36:47Z`）。固定は範囲外（PM に報告する）

### セルフレビュー

- 自分: claude-opus-5-5 / effort 既定のまま（切り替えていない）。`/robust` を design.md の実装ステップと検証方法に照らして回した
- 反対弁護人: Agent（general-purpose、model opus = claude-opus-5-5、effort 既定）。文脈（方針・迷った点・結論・取り消した判断）を渡し、読み取り専用で頼んだ。返ってきたあとの `git status --porcelain -uall` は `?? docs/cycles/008-aws-verification-bugs/build.md` だけ（反対弁護人の写しは scratchpad の `da/`）

#### 指摘と片付け

| # | 分類 | 観点 | 場所 | 破綻シナリオ | 再現 | 片付け |
|---|---|---|---|---|---|---|
| 1 | Should fix（反対弁護人。自分は範囲外の Nit としていたのを上げた） | [再発・再現性] | `docker/images/grafana/Dockerfile:12`、`ops/up.sh:637-638` | プラグインの版を指定せず、イメージのタグ（dir_tag）にも版が入らない。AWS のビルドはその日の最新を入れ、budget の式が変わると nwc-trap が評価エラーに戻る。test_alerts の式は 2.34.4 の写しなので落ちず、KeepLast（#2）で表にも出ない | Dockerfile:12 に版が無いこと、up.sh:637 の dir_tag の引数にプラグインが無いことを読んだ。`gh api repos/grafana/opensearch-datasource/releases/latest --jq '.tag_name + " " + .published_at'` → `v2.34.4 2026-09-15T18:36:47Z`（今日ビルドすれば手元と同じ版） | 直していない。design の変更対象に Dockerfile が無い（範囲外）ので PM に BACKLOG の候補として報告。未確認の項目に書いた |
| 2 | Should fix（反対弁護人） | [検知・検証方法] | `app/grafana/provisioning/alerting/netops-opensearch.yaml` の `execErrState: KeepLast`、design.md:82 | design の AWS の確認（rules API の `health` が `error` でないこと）では、直っていなくても `ok` に見える。プラグインの更新で再発しても誰も気付かない | 検証 4 の前のルールが `ok inactive None [('Normal (Error, KeepLast)', None)]`。design.md:82 を読んだ | 未確認の項目に AWS での判定（`alerts[].state` に `(Error` が無い、または `/api/ds/query` が 200）を書いた。ops でルールの Error 状態を検知する仕組みは範囲外なので PM に BACKLOG の候補として報告 |
| 3 | Should fix（反対弁護人は Nit。missing tests なので上げた） | [テストが縛っていない退行] | `tests/test_graph.py` の `_seed_graph`（`ops/seed_graph.py:72`） | 失敗のケースが botocore の ClientError を投げていたので、seed_graph.py の `except graph.errors()` を `except graph.ClientError` に狭めても落ちない。本番の Neo4j の失敗は Neo4jError なので、狭めると投入のあとでトレースバックで落ち、up.sh が失敗に見える | 反対弁護人の写し（`da/ops/seed_graph.py` の 72 行目だけ `except graph.ClientError as e:`）を `.venv/bin/python da/tests/test_graph.py` → `通過 78 / 失敗 0`。`uv run --group dev python -c "import neo4j"` → `ModuleNotFoundError: No module named 'neo4j'` | 直した（e517ae5）。偽の `_Neo4jError4` を投げ、`graph._server_errors` を Neo4j のときだけそれを返す偽物にする。runpy の中の例外は出力に書いて 3 件の check で落とす。検証 6 に「botocore の ClientError だけ捕まえる」を足し、12 通りが全部落ちる |
| 4 | Nit（反対弁護人） | [境界値] | `netops-opensearch.yaml:22, 57, 65` | terms は件数の多い順に上位 10 件なので、送り元（device map に無ければ IP）が 10 を超えると件数の少ない機器の trap が結果から落ちる。系列が消えると解消の通知が出るかもしれない（Grafana のこの挙動は実測していない） | コメントの「lab は機器 6 台」は trap を送る SR Linux の数で正しかった（`app/containerlab/gen_lab.py:20` の「6 台とも SNMP の trap」、`splab.clab.yml.in` は nokia_srlinux 6 + linux 2）。余裕は 4 | 直していない（最終報告に回す）。逸脱 1 の「8 台」の書き方だけ直した |
| 5 | Nit（反対弁護人と自分） | [予算の式] | `tests/test_sync.py:536-540`、`app/graph/status_handler.py:32-33` の docstring | 予算の式も docstring の「通知 1 件あたり長くて 3.5 秒」も待ちの和だけで、打ち直す apply() の時間を数えていない。1 回目が読みの timeout で長引いてから 409 になると 60 秒を超えうる（Lambda の非同期の再試行で戻る。design のリスク 4） | 式と docstring を読んだだけ | 直していない（最終報告に回す） |
| 6 | Nit（反対弁護人） | [同時実行] | `app/graph/status_handler.py:57, 106` | 待ちが固定で揺らぎが無く、3 つ以上の呼び出しが同じ頂点を同時に書くと、負けた側同士が同じ時刻に打ち直してまたぶつかる | 読んだだけ | 直していない（最終報告に回す） |
| 7 | Nit（自分） | [テストの後始末] | `tests/test_graph.py` の `_seed_graph` | BACKEND を元の値でなく `"neptune"` に戻し、NAME_PREFIX / GRAPH_REPLACE を pop して元の値に戻さない | 反対弁護人: このファイルでそれらを読むのは seed_graph.py だけで、graph.py は import のときに読むので実害は見つからなかった（読んだだけ） | 直していない（最終報告に回す） |

#### 退行の注入

検証 3（A 3 通り）、5（B 4 通り）、6（C 12 通り）。全部どれかのテストが落ちる。C の「索引の Neo4jError を上げる」「索引の前に張ったことにする」は test_oss.py だけ、seed_graph.py の 4 つは test_graph.py だけが縛る。

#### 計測: 17 文のスキーマの張り直しと、2 つのプロセスが同時に空の DB へ張ったとき

`docker run --rm -d --name nwc-neo4j -p 7687:7687 -e NEO4J_AUTH=none neo4j:2026.09.0-community` に scratchpad の `sr_schema.sh`（`sr_schema.py`。`graph._neo4j_schema` をそのまま呼ぶ。logging は WARNING 以上を出す。「張れたもの」は nwc_ で始まり ONLINE の索引の数）。終わったら `docker rm -f -v`。

```
2026-10-08 12:10:08.057+0000 INFO  Started.
nwc_ の制約と索引: 0
(1) 空の DB で初めて張る: 0.321 秒
(2) 全部あるときの張り直し 1 回目: 0.093 秒
(2) 全部あるときの張り直し 2 回目: 0.039 秒
(2) 全部あるときの張り直し 3 回目: 0.035 秒
nwc_ の制約と索引: 0
(3) 同時 1 回目 A: 0.816 秒
(3) 同時 1 回目 B: 1.615 秒
  張れたもの: 17
nwc_ の制約と索引: 0
(3) 同時 2 回目 A: 0.807 秒
(3) 同時 2 回目 B: 1.656 秒
  張れたもの: 17
nwc_ の制約と索引: 0
(3) 同時 3 回目 B: 0.860 秒
(3) 同時 3 回目 A: 1.857 秒
  張れたもの: 17
```

WARNING も例外も出なかった。認証ありの Neo4j と、長い書き込みと並んだときのロック待ちは確かめていない。

#### 問題なしとした観点と根拠

- seed() が device / interface の未登録だけを読むこと: 検証 6 の「seed の読みをラベル無しのまま」「seed が全ラベルを読む」が両方のテストで落ちる。検証 7 の 2 回目の投入で、未登録の dc1-leaf-01 とその IF は置き換わり（IF の DOWN は残る）、zz-ce-09 は未登録のまま残る。反対弁護人も graph.py:393-401 を読んで不成立とした
- count() が Neo4j で 8 本になること: 呼び出し元は seed・seed_layers・sync_physical・seed_graph.py だけで、ポーリングの経路に無い（反対弁護人の grep。読んだだけ）
- スキーマの 17 文: 上の計測。全部あるときの張り直しは 0.1 秒未満、同時に張っても 3 回とも 17 個が ONLINE
- test_oss.py の `_SPLIT4`: 照合は完全一致のまま。検証 6 で count / seed の退行が test_oss.py でも落ちる。Neptune の文字列は「Neptune でもラベルごとに分ける」が golden の check で落ちる
- B の ConflictException の取りこぼしと、誤った打ち直し: `graph.query()` は `_client().execute_query` を直接呼び、例外を包み直さない（graph.py:128-134。読んだだけ）。status_handler は Neptune 専用。botocore の neptune-graph の ConflictException には retryable の印が無い（反対弁護人が service-2.json で確認）。予算の式は 2 AZ で 58.1 秒 < 60（test_sync。上限ではない点は #5）
- A の 30s 固定: `hasAutoDateHistogram` が偽になり検査に入らない（lucene_handler.go:69, 189-191）。手元の Grafana で新しいルールが HTTP 200（検証 4）。reducer は sum で区切りに依らず、区切りは 21 個 × 2500 = 52500 ≤ 65535

#### ジンテーゼ

- 前: Must fix 無し。プラグインの版は範囲外の Nit。seed_graph.py の失敗はテストの 3 件目で確かめたとしていた
- 後: Must fix 無しは変わらない。A の「直った」は「プラグイン 2.34.4 と interval の固定のもとで」の条件付きにした。2026-10-08 の最新は 2.34.4 なので今日ビルドすれば同じだが、版を固定しない限り保証は無い（#1。Should fix に上げ、PM に BACKLOG の候補として出す）。KeepLast のため、AWS の確認は rules API の `health` ではなく `state` か `/api/ds/query` で見る（#2）。seed_graph.py の失敗の型をテストが縛っていなかったのは直した（#3、e517ae5）
- 残るリスク: AWS の全部、認証ありの Neo4j、size 10 の余裕（#4）、打ち直しの時間と揺らぎ（#5、#6）

## Round 2

実装モデル: claude-opus-5-5 / effort: 既定のまま（切り替えていない）

エンジニア1（PM の指示）。review.md の Round 1 の Should fix 2 件と Nit の「shard 2 は推測」を、ブランチ `fix/grafana-plugin-pin-error-state`（cde390f から）で直した。
同じブランチの次の commit（ルールの Error 状態の検出）とは別 commit。AWS には触っていない。BACKLOG.md は触っていない。

### 直したこと

| 指摘 | 直したこと |
|---|---|
| Should fix 1（trap の送り元が size を超える） | `netops-opensearch.yaml` のコメントを「lab は trap の送り元 7 = SR Linux 6 台 + lab.sh の trap-test の dc1-host-01」にし、上位 size 件なので超えると落ちることを書いた。`tests/test_alerts.py` に check を 1 つ: `srlinux/*.cli` のうち trap の宛先が gen_lab の `MGMT_GW` のもの（全台で、`splab.clab.yml.in` の nokia_srlinux の数と同じ）と `ACC_VM` を足した数が sysName の size 以下。コメントの数・台数・機器名もそれと同じ |
| Should fix 2（プラグインの版） | `docker/images/grafana/Dockerfile` で amazonprometheus 3.2.0、opensearch 2.34.4 に固定（ARG にしない。`--build-arg` で替えてもタグ（dir_tag）が変わらないので）。`tests/test_alerts.py` に `OPENSEARCH_PLUGIN_COPIED = "2.34.4"`（式を写した版）を置き、Dockerfile の版と突き合わせる check を 1 つ |
| Nit（yaml 23 行目の shard 2） | 「shard 2 はエラー文の 13600 から逆算した値で、AOSS の shard 数そのものは見ていない」を 1 行足した |
| （ついで） | `docs/architecture/resources/grafana.md` のイメージの行に 2 つの版を書いた |

amazonprometheus の版は手元のイメージで実測した（AWS には当たっていない。ECR の名前のタグは手元に残っていたもの）。GitHub の最新は v3.3.0（2026-10-06）だが、10-07 のビルドにも入っていないので、AWS で動かした 3.2.0 にした。

```
$ gh api repos/grafana/grafana-amazonprometheus-datasource/releases/latest --jq '.tag_name + " " + .published_at'
v3.3.0 2026-10-06T18:02:22Z
$ bash scratchpad/plugin_versions.sh   # 手元のイメージの /opt/grafana-plugins/*/plugin.json の version
== nwc-local-grafana:latest (created 2026-10-07T03:12:35.846726087Z)
/opt/grafana-plugins/grafana-amazonprometheus-datasource/plugin.json "version": "3.2.0"
/opt/grafana-plugins/grafana-opensearch-datasource/plugin.json "version": "2.34.4"
== 493116771193.dkr.ecr.ap-northeast-1.amazonaws.com/efukuda-nwc-poc-grafana:13.2.2-8bc87b12c3d6 (created 2026-10-07T03:12:35.846726087Z)
/opt/grafana-plugins/grafana-amazonprometheus-datasource/plugin.json "version": "3.2.0"
/opt/grafana-plugins/grafana-opensearch-datasource/plugin.json "version": "2.34.4"
== 493116771193.dkr.ecr.ap-northeast-1.amazonaws.com/efukuda-nwc-oss-grafana:13.2.2-8bc87b12c3d6 (created 2026-10-07T03:12:35.846726087Z)
/opt/grafana-plugins/grafana-amazonprometheus-datasource/plugin.json "version": "3.2.0"
/opt/grafana-plugins/grafana-opensearch-datasource/plugin.json "version": "2.34.4"
== 493116771193.dkr.ecr.ap-northeast-1.amazonaws.com/efukuda-nwc-poc-grafana:13.2.2-b00e47ea43ca (created 2026-10-05T01:30:29.411140294Z)
/opt/grafana-plugins/grafana-amazonprometheus-datasource/plugin.json "version": "3.2.0"
/opt/grafana-plugins/grafana-opensearch-datasource/plugin.json "version": "2.34.4"
== 493116771193.dkr.ecr.ap-northeast-1.amazonaws.com/efukuda-nwc-poc-grafana:13.2.2-c1806714ce6a (created 2026-09-28T07:42:02.834936801Z)
/opt/grafana-plugins/grafana-amazonprometheus-datasource/plugin.json "version": "3.2.0"
/opt/grafana-plugins/grafana-opensearch-datasource/plugin.json "version": "2.34.4"
```

### 検証

#### 1. `uv sync --group dev --group web` → `bash ops/check.sh`

```
HEAD cde390f / 未コミット: [ M app/grafana/provisioning/alerting/netops-opensearch.yaml
 M docker/images/grafana/Dockerfile
 M docs/architecture/resources/grafana.md
 M docs/cycles/008-aws-verification-bugs/build.md
 M tests/test_alerts.py]
Resolved 86 packages in 2ms
Audited 82 packages in 1ms
--- ops/check.sh の最後 2 行

すべて通過
exit=0
```

check.sh の全 2620 行のうち、段の見出しと各テストの結果行（行番号: 中身）:

```
2: == 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
5: == 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
25: == 3. スクリプトの構文
29: == 4. 模擬テスト
170: 通過 140 / 失敗 0
661: 通過 489 / 失敗 0
1233: 通過 158 / 失敗 0
1237: 通過 3 / 失敗 0
1317: 通過 78 / 失敗 0
1327: 通過 7 / 失敗 0
1412: 通過 84 / 失敗 0
1524: 通過 111 / 失敗 0
1767: 通過 171 / 失敗 0
1916: 通過 148 / 失敗 0
1983: 通過 66 / 失敗 0
2059: 通過 75 / 失敗 0
2175: 通過 100 / 失敗 0
2618: 通過 325 / 失敗 0
2620: すべて通過
```

#### 2. 足した check（`uv run --group dev python tests/test_alerts.py`。138 → 140）

```
ok Grafana のプラグインは版を固定して入れ、opensearch は bucket budget の式を写した版（2.34.4）と同じ。amazonprometheus は AWS で動かしたイメージの版。版の無い install も ARG で替えられる版も無い
ok trap のルールの機器の terms（上位 size 件）は lab の trap の送り元（trap を lab の EC2 へ送る SR Linux（全台）+ trap-test の ACC_VM）を全部返せる。yaml のコメントの送り元の数も同じ
通過 140 / 失敗 0
```

#### 3. 退行を入れて test_alerts.py が落ちること（scratchpad の `inject_a.py`。ファイルを書き換えて流し、元に戻す）

```
落ちた rc=1 opensearch の版を外す: AssertionError: Grafana のプラグインは版を固定して入れ、opensearch は bucket budget の式を写した版（2.34.4）と同じ。amazonprometheus は AWS で動かしたイメージの版。版の無い install も ARG で替えられる版も無い
落ちた rc=1 amazonprometheus の版を外す: AssertionError: Grafana のプラグインは版を固定して入れ、opensearch は bucket budget の式を写した版（2.34.4）と同じ。amazonprometheus は AWS で動かしたイメージの版。版の無い install も ARG で替えられる版も無い
落ちた rc=1 opensearch を 2.35.0 にする: AssertionError: Grafana のプラグインは版を固定して入れ、opensearch は bucket budget の式を写した版（2.34.4）と同じ。amazonprometheus は AWS で動かしたイメージの版。版の無い install も ARG で替えられる版も無い
落ちた rc=1 版を ARG にする: AssertionError: Grafana のプラグインは版を固定して入れ、opensearch は bucket budget の式を写した版（2.34.4）と同じ。amazonprometheus は AWS で動かしたイメージの版。版の無い install も ARG で替えられる版も無い
落ちた rc=1 ARG を足す: AssertionError: Grafana のプラグインは版を固定して入れ、opensearch は bucket budget の式を写した版（2.34.4）と同じ。amazonprometheus は AWS で動かしたイメージの版。版の無い install も ARG で替えられる版も無い
落ちた rc=1 写した版の定数だけ上げる: AssertionError: Grafana のプラグインは版を固定して入れ、opensearch は bucket budget の式を写した版（2.35.0）と同じ。amazonprometheus は AWS で動かしたイメージの版。版の無い install も ARG で替えられる版も無い
落ちた rc=1 sysName の size を 6 にする（送り元 7 > 6）: AssertionError: trap のルールの機器の terms（上位 size 件）は lab の trap の送り元（trap を lab の EC2 へ送る SR Linux（全台）+ trap-test の ACC_VM）を全部返せる。yaml のコメントの送り元の数も同じ
落ちた rc=1 コメントの送り元を 6 のままにする: AssertionError: trap のルールの機器の terms（上位 size 件）は lab の trap の送り元（trap を lab の EC2 へ送る SR Linux（全台）+ trap-test の ACC_VM）を全部返せる。yaml のコメントの送り元の数も同じ
落ちた rc=1 コメントの機器名を変える: AssertionError: trap のルールの機器の terms（上位 size 件）は lab の trap の送り元（trap を lab の EC2 へ送る SR Linux（全台）+ trap-test の ACC_VM）を全部返せる。yaml のコメントの送り元の数も同じ
落ちた rc=1 1 台の trap の宛先を消す: AssertionError: trap のルールの機器の terms（上位 size 件）は lab の trap の送り元（trap を lab の EC2 へ送る SR Linux（全台）+ trap-test の ACC_VM）を全部返せる。yaml のコメントの送り元の数も同じ
戻したあと: M app/grafana/provisioning/alerting/netops-opensearch.yaml |  M docker/images/grafana/Dockerfile |  M docs/architecture/resources/grafana.md |  M tests/test_alerts.py
元のまま: 通過 140 / 失敗 0 rc 0
```

#### 4. 版を固定した Dockerfile を手元でビルドする（`docker build -f docker/images/grafana/Dockerfile app/grafana/`、arm64。入った版を読んでからイメージを消す）

```
#1 DONE 0.0s
#2 DONE 1.3s
#3 DONE 0.0s
#4 DONE 0.0s
#6 [2/4] RUN mkdir -p /opt/grafana-plugins  && grafana cli --pluginsDir /opt/grafana-plugins plugins install grafana-amazonprometheus-datasource 3.2.0  && grafana cli --pluginsDir /opt/grafana-plugins plugins install grafana-opensearch-datasource 2.34.4  && chown -R 472:0 /opt/grafana-plugins
#6 0.396 logger=settings t=2026-10-08T12:51:26.319088841Z level=info msg="Starting Grafana" version=13.2.3 commit=90ffed056f0884267356c12a0eeb72a022af53f1 branch=release-13.2.3#patched compiled=2026-09-28T20:55:24Z
#6 3.726 logger=settings t=2026-10-08T12:51:29.649190051Z level=info msg="Starting Grafana" version=13.2.3 commit=90ffed056f0884267356c12a0eeb72a022af53f1 branch=release-13.2.3#patched compiled=2026-09-28T20:55:24Z
#6 DONE 6.3s
#7 DONE 0.0s
#8 DONE 0.0s
#9 DONE 1.7s
build exit=0
grafana version 13.2.3
/opt/grafana-plugins/grafana-amazonprometheus-datasource/plugin.json "version": "3.2.0"
/opt/grafana-plugins/grafana-opensearch-datasource/plugin.json "version": "2.34.4"
image removed
gone: nwc-pin-test-grafana:local
```

### 未確認の項目

- AWS のビルド（`build_grafana` の buildx で arm64 を ECR に push）と、ECS の上で 3.2.0 / 2.34.4 が動くこと。Dockerfile が変わったのでタグが変わり、次の `ops/up.sh` / `oss/ops/up.sh` で作り直しになる
- `docs/development.md` のテストの本数（test_alerts 138）は 140 になった。PM のブランチ（afecd19）が同じ行を直しているので、ここでは直していない
