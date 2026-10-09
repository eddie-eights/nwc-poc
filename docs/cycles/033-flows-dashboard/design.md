# flows（GoFlow2）の Grafana のダッシュボードを作る（033）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

| # | BACKLOG の行 | 出所 | 扱い |
|---|---|---|---|
| A | flows（GoFlow2）の Grafana のダッシュボードを作る（012 は Kafka と格納先まで。画面は無い） | 012 の範囲外 | 作る |

flows は GoFlow2 が sFlow / IPFIX を受けて Kafka の `flows` トピックに書き、Spark が OpenSearch（`logs` と同じ index、`topic: flows`）に格納するところまで 012 で出来ている。Grafana には logs のダッシュボード（`logs.json`）しか無く、flows を見る画面が無い。

### 調査で分かった事実（2026-10-10、origin/main 8a0f40a）

- `app/spark/snmp_sinks.py:86-89`: flows の 1 行は `FLOW_NAME = "flow"`、tags は `sampler / src / dst / proto / src_port / dst_port / in_if / out_if / type`、fields は `bytes / packets`。`:524` の `opensearch_docs` が `@timestamp / topic / tags / fields` の文書にする（OpenSearch では `tags.src` は keyword を持つ text、`fields.bytes` は数値。実物の mapping は `app/spark/` の index template か `tests/test_analytics.py` で実装が確かめる）。
- `app/grafana/provisioning/dashboards/logs.json`: uid `nwc-logs`、datasource `{type: grafana-opensearch-datasource, uid: aoss-logs}`、query は lucene、`timeField: "@timestamp"`、`topic.keyword` の terms と logs panel。これが flows の雛形。
- `app/grafana/start.sh`: `OPENSEARCH_URL` が有るとき（マネージド版）だけ `cp "$SRC/dashboards/logs.json" /tmp/grafana-dashboards/`。OSS 版（Grafana on ECS）も同じ start.sh。
- `app/grafana/provisioning/datasources-oss/opensearch.yaml:3` のコメント「uid は…aoss-logs にして、ダッシュボード（logs.json）と…」。
- `tests/test_oss.py:1857` の `_G_FILES` に `dashboards/logs.json`、`:1860` の文言「マネージド版と同じダッシュボード 2 つ・アラートの定義 3 つ」。
- docs: `docs/faq-fukuda-nwc-poc.md:1198` の表「| OpenSearch (logs) | … | ダッシュボード `logs.json` と、アラートルール `trap` |」。`docs/pipeline.md` / `docs/oss-variant.md` の Grafana の節。
- `tests/test_sync.py` / `test_analytics.py` に `logs.json` の panel を縛る check があるか（datasource の uid、`timeField`）は実装で grep して、同じ形の check を flows に足す。

## 設計方針

**A. `flows.json`（logs.json と同じ datasource、同じ index）**

`app/grafana/provisioning/dashboards/flows.json`。uid `nwc-flows`、title `nwc / flows`、tags `["nwc"]`、`time: now-6h`、refresh `1m`。全 panel の datasource は `{"type": "grafana-opensearch-datasource", "uid": "aoss-logs"}`、query の `timeField` は `@timestamp`、lucene の query は `topic:flows`。panel は 6 つ。

| # | panel | 型 | query |
|---|---|---|---|
| 1 | bytes/s（全体） | timeseries | metrics `[{"type": "sum", "field": "fields.bytes", "id": "1"}]`、bucketAggs `date_histogram`（`@timestamp`、interval auto）。1 秒あたりに直すのは Grafana の transform ではなく「bytes の合計」のまま、title に「合計 / 区間」と書く |
| 2 | 上位の送信元 | bar gauge か table | terms `tags.src.keyword`（size 10、order by sum of `fields.bytes`） |
| 3 | 上位の宛先 | 同上 | terms `tags.dst.keyword` |
| 4 | プロトコル別 | piechart | terms `tags.proto.keyword` |
| 5 | sampler 別 bytes | timeseries | terms `tags.sampler.keyword` × date_histogram、metric sum `fields.bytes` |
| 6 | 生の flow | logs | query `topic:flows`、最新 100 件 |

フィールドの名前（`tags.src.keyword` か `tags.src`）は OpenSearch の実際の mapping に合わせる（`logs.json` が `topic.keyword` を使っているので dynamic mapping で `.keyword` が付く見込み。実装で index template を確かめる）。

**B. 配る**

`app/grafana/start.sh` の `OPENSEARCH_URL` の分岐に `cp "$SRC/dashboards/flows.json" /tmp/grafana-dashboards/` を足す（logs.json の行の隣）。provisioning の `dashboards.yaml` が `/tmp/grafana-dashboards/` を読んでいるなら他は変えない。

**C. テストと docs**

1. `tests/test_oss.py`: `_G_FILES` に `dashboards/flows.json`、文言を「ダッシュボード 3 つ」に。
2. `flows.json` の check（logs.json の check があるファイルに並べる。無ければ `tests/test_sync.py` に）: JSON として読める、uid `nwc-flows`、全 panel の datasource uid が `aoss-logs`、全 query に `topic:flows`、`timeField` が `@timestamp`、panel が 6 つ、`fields.bytes` と `tags.src.keyword` / `tags.dst.keyword` / `tags.proto.keyword` / `tags.sampler.keyword` が出てくる。
3. `app/grafana/start.sh` の check に `flows.json` の cp を足す。
4. docs: `docs/pipeline.md` の Grafana の節と `docs/oss-variant.md`、FAQ 1198 行の表を「ダッシュボード `logs.json` / `flows.json`」に。`app/grafana/provisioning/datasources-oss/opensearch.yaml:3` のコメントに `flows.json` を足す。

**やらないこと。** flows のアラートルールは作らない。Splunk 側の flows の画面は作らない。bytes/s への換算（transform）はしない。

## 変更対象ファイル

| ファイル | 件 | 何を |
|---|---|---|
| `app/grafana/provisioning/dashboards/flows.json` | A | 新規 |
| `app/grafana/start.sh` | B | cp 1 行 |
| `app/grafana/provisioning/datasources-oss/opensearch.yaml` | C | コメント |
| `tests/test_oss.py` / `tests/test_sync.py`（logs.json の check がある側） | C | check |
| `docs/pipeline.md` / `docs/oss-variant.md` / `docs/faq-fukuda-nwc-poc.md` | C | 文言 |

## 再利用するもの

- `logs.json` の datasource / query / timeField の形。
- `snmp_sinks.py` の `FLOW_TAGS` / `FLOW_FIELDS` の名前。

## 実装ステップ

1. `flows.json` を書く。`python3 -c "import json; json.load(open('app/grafana/provisioning/dashboards/flows.json'))"` が通る。start.sh の cp。1 commit。
2. check と docs。1 commit。
3. `build.md`（頭は `実装モデル: opus-5.5 / effort: high`。テストの出力、セルフレビュー）。

## 検証方法（期待出力まで）

- `uv run --frozen python3 tests/test_oss.py` と check を足した側のテストが `失敗 0`（件数は実測）。
- `bash -n app/grafana/start.sh` が無言。
- 手元の compose（`docker/compose/`）に Grafana と OpenSearch があれば `docker compose up` して `http://localhost:3000/d/nwc-flows` が 6 panel で開く（flows のデータは lab が無いと空。panel のエラーが無いことだけ見る）。無ければ AWS で。
- AWS（PM が 1 回だけ打つ。実装の範囲外）: Grafana の `nwc / flows` が開き、lab の sFlow で panel 1〜6 に値が入る。panel のエラー（`field not found` など）が無い。

## 未確定事項とリスク

1. `tags.src.keyword` の名前は dynamic mapping 前提。index template で `tags.*` を keyword 型に固定しているなら `.keyword` 無しが正しい。実装で確かめて check もそれに合わせる。
2. 上位 N の panel を bar gauge にするか table にするかは実装の判断（logs.json にある型を優先）。
3. 手元で OpenSearch + Grafana が立たないなら、画面の確認は AWS の 1 回だけになる。panel のエラーが出たら PM がその場で直す。
