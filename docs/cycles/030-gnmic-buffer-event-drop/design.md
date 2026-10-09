# gnmic の初回値を落とさず、空の event を Kafka に書かない（030）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

| # | BACKLOG の行 | 出所 | 扱い |
|---|---|---|---|
| A | gnmic の on-change の購読が gnmi トピックに 1 件も書かない原因を調べて直す（購読直後の初回値が無い） | 2026-10-09 の AWS 検証 2 回目（`docs/verification/20261009-aws-managed-2.md`） | 直す |
| B | gnmic が values の無い event を Kafka に書かないようにする（metrics の 9 割が空の event） | 025 の「やらないこと」 | 直す |

A は「値が変われば書く」ところまで分かっていて、残るのは購読の直後の初回値（on-change の初期同期）が Kafka に無いこと。B は流量の問題で、同じ `outputs.kafka` の設定を触るので 1 サイクルにまとめる。

### 調査で分かった事実（2026-10-10、origin/main 8a0f40a）

- `app/gnmic/gnmic.yaml.in:66-` の `outputs.gnmi` / `outputs.metrics` は `type: kafka` / `address` / `topic` / `format: event` / `split-events: true` と、`# >>> kafka_auth scram` 〜 `# <<< kafka_auth scram` の印で囲んだ `sasl`（SCRAM-SHA-512）と `tls.ca-file`。`buffer-size` / `timeout` / `event-processors` は無い。
- gnmic 0.49.0（`docker/images/gnmic/Dockerfile` の `ARG GNMIC_VERSION=0.49.0`）の `outputs/kafka_output/kafka_output.go`（読んだ）:
  - `Write` は `select { case o.msgChan <- msg: case <-wctx.Done(): }` で、`wctx` は `timeout`（既定 `5s`）の context。`msgChan` の容量は `buffer-size`（既定 `0`）。受け手が居ないあいだに来た event はタイムアウトで**黙って捨てる**（ログは debug 相当）。
  - `Init` は producer（sarama）を goroutine で作り始めるだけで、出来るのを待たない。TLS + SCRAM の producer は数秒かかる。
  - つまり購読直後の初期同期の burst（on-change の全状態）が producer の完成前に `Write` に来ると、buffer 0 のまま 5 秒で捨てられる。**これが A の仮説**（AWS で確かめる。実装では設定だけ直す）。
- `event-drop` processor（`formatters/event_drop/`）は `condition`（jq 式）か `tags` / `values` の正規表現で event を捨てる。`outputs.<name>.event-processors: [<name>]` で output 側に付けられる（subscription 側にも付けられるが、今回は output 側）。`.values == null or (.values | length) == 0` の形の jq 式が通るかは**未検証**（実装で `gnmic` の `--dry-run` か手元の実機で確かめる。下の検証方法）。
- `app/gnmic/gnmic.yaml.in:20-21` のコメントが「on-change は購読の直後に今の状態を全部送り、その後は変わったときだけ送る」と書いている。同 4 行目「Telegraf の形（name / tags / fields、秒）への読み替えは app/spark/snmp_sinks.py の read_rows がする」。「processor は使わない」に当たる文言は grep に出なかった（実装で `grep -n processor app/gnmic/` を打って、あれば書き換える）。
- `app/gnmic/gnmic.sh` の `render` が `KAFKA_AUTH=scram`（既定）で印の行だけ消し、`none` で区間ごと消す。印の外に足す項目はどちらでも残る。
- `tests/test_stream.py:385-425` が描画後の yaml の `outputs.gnmi` / `outputs.metrics` を縛る: none 側は `set(o) == {"type", "address", "topic", "format", "split-events"}`、scram 側は `sasl` / `tls` の等式。**足す項目はこの集合に入れないと落ちる**。
- `app/spark/snmp_sinks.py` の `read_rows` は gnmi / metrics の event を `name / tags / values` で読む。values の無い event は fields が空の行になるだけで、落としても読み手は壊れない（`tests/test_analytics.py` の read_rows の check で空の values を期待するものが無いことを実装で確かめる）。

## 設計方針

**A. producer が出来るまで event を抱える（`buffer-size` と `timeout`）**

`outputs.gnmi` と `outputs.metrics` の両方（印の外、`split-events: true` の次の行）に足す。

```yaml
    split-events: true
    # 購読直後の初期同期の burst は producer（TLS + SCRAM）が出来る前に来る。既定（buffer-size 0 / timeout 5s）では
    # 受け手が居ないあいだの event を黙って捨てるので、抱えて待つ（2026-10-09 の AWS 検証で初回値が無かった件。030）
    buffer-size: 10000
    timeout: 60s
```

`buffer-size` は 4 台 × 5 購読の初期同期で数百〜数千 event（split-events 後）と見積もって 10000。`timeout` は producer の完成（数秒）より十分長く 60s。

**B. values の無い event を捨てる（`event-drop`）**

```yaml
processors:
  # values が空の event（metrics の 9 割）。Spark の read_rows には fields の無い行になるだけなので捨てる（030）
  drop-empty:
    event-drop:
      condition: '.values == null or (.values | length) == 0'
```

を `outputs:` の前に置き、`outputs.gnmi` と `outputs.metrics` に `event-processors: [drop-empty]` を足す。jq 式が通らなければ `values` の正規表現の形に変える（`gnmic` のドキュメント `docs/user_guide/event_processors/event_drop.md` を読む）。

**C. テストと docs**

1. `tests/test_stream.py:385-425` の none 側の集合に `buffer-size` / `timeout` / `event-processors` を足し、`o["buffer-size"] == 10000 and o["timeout"] == "60s" and o["event-processors"] == ["drop-empty"]`、`cfg["processors"]["drop-empty"]["event-drop"]["condition"]` の文字列、を gnmi / metrics の両方で見る。scram 側は既存の sasl / tls の等式のまま。
2. `docs/pipeline.md` の gnmic の節に「初期同期の buffer」「空 event の drop」を 1〜2 行ずつ。`docs/troubleshooting.md` に「gnmi に初回値が無い → buffer-size / timeout（030）」を 1 行。
3. `app/gnmic/gnmic.yaml.in:20-21` のコメントを A の内容で補う。「processor は使わない」の文言があれば消す。

**やらないこと。** subscription の `heartbeat-interval` は付けない（Grafana / Splunk は 24h を読む設計のまま）。sample モードへの変更はしない。Spark 側の `read_rows` は変えない。

## 変更対象ファイル

| ファイル | 件 | 何を |
|---|---|---|
| `app/gnmic/gnmic.yaml.in` | A, B | `buffer-size` / `timeout` / `processors` / `event-processors`、コメント |
| `tests/test_stream.py` | C | outputs の集合と値の check |
| `docs/pipeline.md` | C | gnmic の節 |
| `docs/troubleshooting.md` | C | 1 行 |

## 再利用するもの

- `app/gnmic/gnmic.sh render`（描画の仕組みはそのまま）。
- `tests/test_stream.py` の描画して yaml を読む部品（`_gnmic_yaml` 相当。実物の名前は test_stream.py:385 付近）。

## 実装ステップ

1. yaml.in に A と B を足し、手元で `app/gnmic/gnmic.sh render`（KAFKA_AUTH=none と scram の両方）が通り、`docker run --rm -v <描画後>:/gnmic.yaml ghcr.io/openconfig/gnmic:0.49.0 --config /gnmic.yaml subscribe --dry-run` 相当で設定の読み込みが通る（processor の jq 式がここで弾かれる）。通らなければ正規表現の形へ。1 commit。
2. test_stream の check を直し赤→緑。docs。1 commit。
3. `build.md`（頭は `実装モデル: opus-5.5 / effort: high`。dry-run の出力、テストの出力、セルフレビュー）。

## 検証方法（期待出力まで）

- `uv run --frozen python3 tests/test_stream.py` が `失敗 0`（件数は build の冒頭で実測して書く。check を足したぶん増える）。
- gnmic の設定の読み込み（`--dry-run` か `gnmic … subscribe` を機器無しで起動してログ）で `processors` の parse のエラーが出ない。
- AWS（PM が 1 回だけ打つ。実装の範囲外）: `ops/up.sh` の直後 5 分以内に gnmi トピックに `interface_state` / `isis_interface` の初回値が 4 台ぶん入る（Kafbat UI か `docs/verification` の手順）。metrics トピックの event のうち `values` が空のものが 0。流量が 2026-10-09 の記録より減る。

## 未確定事項とリスク

1. A の仮説（producer の完成前の burst を捨てている）は AWS でしか確かめられない。buffer を足しても初回値が無ければ、原因は機器側（SR Linux が on-change の初期同期を送らない）か購読の形で、次の候補は `updates-only: false` の明示と `sample` モードの併用。
2. jq 式の形は未検証。`event-drop` の `condition` が `.values` を見られるか（event の JSON は `name / timestamp / tags / values`）は dry-run で確かめる。
3. `buffer-size: 10000` のメモリは event 1 件 1 KB 以下として 10 MB 程度。Fargate の 0.5 GB で問題ない見積もり。
