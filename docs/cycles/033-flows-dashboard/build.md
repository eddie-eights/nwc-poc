# flows（GoFlow2）の Grafana のダッシュボードを作る（033）— build

## Round 1

実装モデル: opus-5.5 / effort: high

### 変更ファイル

| ファイル | 件 | commit |
|---|---|---|
| `app/grafana/provisioning/dashboards/flows.json`（新規）/ `app/grafana/start.sh` | A・B | f626e27、eea32db（panel の description） |
| `tests/test_oss.py` | C-1〜3 | 9f16374、eea32db（panel ごとに縛る・snmp_sinks を import・OpenSearch だけの組） |
| `app/grafana/provisioning/datasources-oss/opensearch.yaml` / `docs/pipeline.md` / `docs/oss-variant.md` / `docs/faq-fukuda-nwc-poc.md` | C-4 | 9f16374、eea32db |
| `app/grafana/provisioning/datasources/opensearch.yaml`（マネージド版のコメント） | C-4 | eea32db |

### 検証（最後の編集のあとに取り直した出力）

```
$ uv run --frozen python3 tests/test_oss.py   (exit 0)
通過 177 / 失敗 0

$ uv run --frozen python3 tests/test_alerts.py   (exit 0)
通過 168 / 失敗 0

$ uv run --frozen python3 tests/test_analytics.py   (exit 0)
通過 534 / 失敗 0

$ uv run --frozen python3 tests/test_local_compose.py   (exit 0)
通過 143 / 失敗 0

$ uv run --frozen python3 tests/test_sync.py
通過 103 / 失敗 0

$ uv run --frozen python3 tests/test_dashboard_config.py
通過 3 / 失敗 0

$ bash -n app/grafana/start.sh   (exit 0)
(無言)

$ python3 -c "import json; json.load(open('app/grafana/provisioning/dashboards/flows.json'))"   (exit 0)
(無言)
```

### 退行の注入（どれも test_oss が落ちる。注入のあとは元に戻した）

```
[start.sh の flows.json の cp を外す] exit 1: AssertionError: OSS 版の環境変数（…）… ダッシュボード 3 つ …
[flows.json の cp を PROMETHEUS_URL の分岐へ移す] exit 1: AssertionError: flows.json は start.sh が OPENSEARCH_URL のあるときだけ並べる…
[OPENSEARCH_URL の分岐でも metrics.json を並べる] exit 1: AssertionError: flows.json は start.sh が OPENSEARCH_URL のあるときだけ並べる…
[tags.src.keyword を tags.src にする] exit 1: AssertionError: flows.json: uid nwc-flows、panel 6 つ、…
[1 つの query を topic:logs にする] exit 1: AssertionError: flows.json: uid nwc-flows、panel 6 つ、…
[1 つの datasource uid を amp にする] exit 1: AssertionError: flows.json: uid nwc-flows、panel 6 つ、…
[fields.bytes を fields.byte にする] exit 1: AssertionError: flows.json: uid nwc-flows、panel 6 つ、…
[panel 5 を tags.type.keyword にする] exit 1: AssertionError: flows.json: uid nwc-flows、panel 6 つ、…
[sum を avg にする] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
[orderBy を無い metric id 9 にする] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
[panel 2 と 3 の field を入れ替える] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
[panel 1 の date_histogram を消す] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
[piechart を table にする] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
[bytes を packets にする] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
[unit を bps にする] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
[terms の size を 500 にする] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
[panel 1 に logs の metric を足す] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
[panel 5 の terms と date_histogram の順を逆にする] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
[logs の limit を 500 にする] exit 1: AssertionError: flows.json の panel ごとの中身は設計の表どおり…
```

### 手元の画面（docker compose。project 名 nwc033、イメージ名とネットワーク名も override で分けた）

OpenSearch 3.9.0 + Grafana 13.2.3（`docker/images/grafana` のビルド。OpenSearch のプラグインは AWS と同じ版）。Grafana の `/api/ds/query` に各 panel の targets を投げた。最後の編集のあとに 3 つの状態で取り直した。

```
# snmp-logs の index が無い（GET /snmp-logs は 404）
  panel 1〜5: status 200 error None frames 0
  panel 6 logs: status 200 error None frames 1 rows [0]
GET /d/nwc-flows: 200

# trap が 1 件だけ（flows の mapping が無い）
mapping tags: ['oid', 'source', 'sysName'] fields: {"ifIndex": {"type": "float"}}
  panel 1 timeseries: status 200 error None frames 1 rows [361]
  panel 2〜5: status 200 error None frames 0
  panel 6 logs: status 200 error None frames 1 rows [0]

# Spark の flow_message → opensearch_docs で作った flow を 60 件
doc の例: {"@timestamp":"…","topic":"flows","measurement":"flow",…,"tags":{"sampler":"172.20.20.2","src":"10.0.1.1","dst":"10.0.9.2","proto":"UDP",…,"type":"NETFLOW_V5"},"fields":{"bytes":1490.0,"packets":6.0},…}
bulk errors: False items: 60
mapping tags.src: {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}}  fields.bytes: float
  panel 1 timeseries: status 200 error None frames 1 rows [361]
  panel 2 table: status 200 error None frames 1 rows [3] 先頭 ['10.0.1.2', 115409]
  panel 3 table: status 200 error None frames 1 rows [2] 先頭 ['10.0.9.1', 153178]
  panel 4 piechart: status 200 error None frames 1 rows [2]
  panel 5 timeseries: status 200 error None frames 2 rows [361, 361]
  panel 6 logs: status 200 error None frames 1 rows [60]
GET /d/nwc-flows: 200
```

- ブラウザで `http://localhost:3000/d/nwc-flows` を開いたのは 1 回目（description を足す前。そのときの文書は fields.bytes が long の手書き）。6 panel が描け、エラーの表示は無かった。
- 後片付け: `down -v`、`docker/compose/.env` を消し、`docker rmi nwc033-grafana`。nwc033 のコンテナ・ボリューム・ネットワーク・イメージが無いことを確かめた。
- AWS: 未確認（設計どおり PM が打つ）。

### 設計との差

- panel 1 の title は「bytes/s（全体）」ではなく「bytes（合計 / 区間）」。設計が /s への換算をせず「合計 / 区間」と書くとしているので、/s を名乗らない。
- 上位 N（panel 2・3）は table（logs.json に bar gauge が無い。設計の未確定事項 2 の判断）。
- check は test_sync.py ではなく test_oss.py に置いた（logs.json の check と `_G_FILES` がそこにある）。設計の C-3「start.sh の check に cp を足す」は `_G_FILES` と「OPENSEARCH_URL のときだけ並べる」check で満たした。
- 設計の C-2 より広く縛った（panel ごとの型・集計・単位・orderBy、OpenSearch だけの組）。
- panel 1〜5 に description（サンプリング率を掛けていない、ふだんは空）を足した。
- 設計の AWS の検証は「lab の sFlow で panel 1〜6 に値が入る」だが、lab の機器は flow を出さない（`docs/collection.md` の表、`ops/netflow_send.py` の docstring）。AWS では `ops/netflow_send.py <NLB>:2055` で送ってから見る必要がある。設計は変えていない（PM の判断）。
- 手元の画面は設計の「空でエラーが無いことだけ」より広く、データを入れた状態も見た。

### セルフレビュー

- 自分: opus-5.5 / high。反対弁護人: opus（general-purpose、読み取り専用、文脈を渡した）。終わったあと `git status --porcelain -uall` は変化なし。
- Must fix: 0。

| # | 分類 | [観点] | 場所 | 破綻シナリオ | 再現 | 片付け |
|---|---|---|---|---|---|---|
| S1 | Should | [設計整合性] | design.md の検証方法、`docs/pipeline.md` | lab から flow は来ないので AWS ではふだん全 panel が空で、壊れたと読み違える | `docs/collection.md:25`「受け口だけ（lab の機器からは来ない）」。手元で index 無し・flows 0 件のときも panel はエラーにならないことを上の「手元の画面」で確かめた | 直した: panel の description と pipeline.md に「ふだんは空、netflow_send で出る」。設計の AWS 手順は「設計との差」に書いて PM に回す |
| S2 | Should | [検証の前提] | `app/spark/snmp_sinks.py:467`（`_number` は float） | 1 回目の手元の文書は fields.bytes を int で書き mapping が long。Spark の実物は float | `opensearch_docs` の出力は `"bytes":8400.0`。float の mapping で取り直し、6 panel とも 200・error なし | 直した: Spark の関数で作った文書で取り直した（上の出力） |
| S3 | Should | [missing tests] | `tests/test_oss.py`（flows.json の check） | sum→avg、orderBy の参照先、送信元と宛先の入れ替えなど 11 の改変が通る | 反対弁護人の mut.py の出力（どれも PASS） | 直した: panel ごとの check。上の注入 12 件で落ちる |
| S4 | Should | [意味の誤解] | `flows.json`、`docs/pipeline.md` | sFlow の bytes はサンプル分だけ（GoFlow2 も Spark も sampling_rate を掛けず、FLOW_FIELDS に率が無い）。decbytes を実流量と読む | `snmp_sinks.py` の FLOW_FIELDS は bytes / packets だけ（test_analytics の「GoFlow2 のキーは 11 個」） | 直した: description と pipeline.md に書いた |
| S5 | Should | [docs] | `docs/pipeline.md` | flows.json の行に「AWS では未確認」が無い | 読んだ | 直した |
| N1 | Nit | [check の壊れやすさ] | `tests/test_oss.py` | FLOW_TAGS の書式を変えると赤 | 反対弁護人の mut.py | 直した: snmp_sinks を import |
| N2 | Nit | [docs] | `docs/pipeline.md`、FAQ | logs.json を「traps / logs」と書いたが、panel 1 は全トピック（flows も）の件数 | logs.json の panel 1 は query `*` × `topic.keyword` | 直した |
| N3 | Nit | [コメント] | `app/grafana/provisioning/datasources/opensearch.yaml:1` | マネージド版のコメントに flows が無い | 読んだ | 直した |
| N4 | Nit | [記録] | build.md | title と check の置き場の差 | — | 直した: 「設計との差」に書いた |
| N5 | Nit | [check の穴] | `tests/test_oss.py` | OpenSearch だけの組を縛っていない | — | 直した: 注入「OPENSEARCH_URL の分岐でも metrics.json」で落ちる |
| N6 | Nit | [空のとき] | flows.json | index が無いと全 panel が index_not_found と見込んだ | 手元で index 無し（404）でも 6 panel とも status 200・error なし | 当たらない（再現した出力は上） |

- 残した Nit: logs panel（panel 6）は flow の文書に message の項目が無いので、本文が JSON 全体になる（logs.json の trap と同じ）。gridPos の重なりは check で縛っていない。
- 「問題なし」とした観点: マネージド版でも `.keyword` になるか（index template は repo に無く、logs.json の `topic.keyword` は 2026-09-28 に AWS で読めている。読んだだけ）。piechart と orderBy "1" は AWS と同じ Dockerfile で焼いたプラグインで、手元の query が通った（実行した。版の番号は見ていない）。
