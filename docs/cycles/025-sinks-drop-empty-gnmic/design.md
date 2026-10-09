# sinks を values の無い gnmic の event で落ちなくする（025）

設計: PM(fable-5.1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

2026-10-09 のマネージド版の AWS 検証 2 回目（`docs/verification/20261009-aws-managed-2.md` の不具合 1）で、Spark の sinks のジョブ `sinks-splunk` と `sinks-grafana` が 5 回とも落ち、AMP も OpenSearch も Splunk も空のままだった。原因は `metrics` トピックに入っている gnmic の event のうち、values も deletes も無いもの（400 件中 359 件）を `read_rows` が Telegraf の行として読んでしまい、ナノ秒の `timestamp` を秒として読んで年 173875 の `ts` を作り、`collect()` で Python の `datetime` に直すところで `ValueError` を出すこと。

Kafbat UI で取った実物（`/api/clusters/<cl>/topics/metrics/messages/v2?mode=EARLIEST&limit=400`、09:53Z）:

```json
{"name":"interface_stats","timestamp":1791534762523125553,"tags":{"interface_name":"ethernet-1/7","source":"203.0.113.11","subscription-name":"interface_stats"}}
```

このサイクルは、(1) `read_rows` がこの行を捨てること、(2) 1 行の壊れた timestamp でジョブが死なないこと、(3) 実物の event をテストの入力にすること、(4) 本物の Spark で同じ経路を 1 度通せる確認手段を持つこと、の 4 つをやる。収集の側（gnmic の設定）は触らない。

## 調査で分かった事実

### `read_rows` の経路（`app/spark/snmp_sinks.py`、main 5da99bd）

- `:282 gnmic = msg["fields"].isNull() & (msg["values"].isNotNull() | msg["deletes"].isNotNull())` — gnmic の判定に values か deletes を要求している。上の実物はどちらも無いので `gnmic` が偽になる
- `:300 F.when(F.col("topic") == FLOW_TOPIC, flow_struct).when(gnmic, gnmic_struct(F, msg)).otherwise(telegraf).alias("m")` — 偽なので `telegraf`（`:283`。`timestamp` をそのまま秒として使う struct）に落ちる
- `:302 F.when(gnmic, msg["tags"]["source"]).otherwise(msg["tags"]["agent_host"]).alias("agent_host")` — 同じ判定で agent_host も null になる
- `:309 F.to_timestamp(F.from_unixtime(F.col("m.timestamp"))).alias("ts")` と `:322 .where(F.col("ts").isNotNull())` — `m.timestamp` が 1791534762523125553（ナノ秒）のまま秒として変換される。null ではないので where を通る
- `:357`（`gnmic_struct`）`F.when(msg["values"].isNotNull(), (msg["timestamp"] / GNMI_NS).cast("long")).alias("timestamp")` — gnmic の側に来ていれば values が無い行は timestamp が null になり `:322` で捨てられる。**判定が直れば `gnmic_struct` は直さなくてよい**
- `:377`（`gnmic_message`、Python の双子）`if not isinstance(values, dict) or not isinstance(ts, int) or isinstance(ts, bool): return None` — 実物を渡せば None になる。双子は正しく、Spark の側だけがずれていた
- `:813`（`send_partition`）と `:833`（`http_query` の each_batch）の `batch_df.collect()` で Python の `datetime` に直す。年 173875 は Python の `datetime`（上限 9999 年）に入らず `ValueError`。`sinks-s3iceberg` は `collect()` せずに Iceberg に書くので RUNNING のままだった（壊れた ts の行が書かれた可能性が高い。環境は `ops/down.sh` で消したので、残っているデータは無い）
- Spark の中で年 173875 の値が `from_unixtime` → `to_timestamp` をどう通ったか（文字列の往復で桁あふれしなかったのか）は未確認。**守りは `ts` ではなく、変換前の `m.timestamp`（long）の範囲で入れる**（Spark の内部表現に依らない）

### テストの現状

- `tests/test_analytics.py` は `snmp_sinks.py` のソースの字面を見る。直す行の字面を縛っている check: `:471`（`.where(F.col("ts").isNotNull())`）、`:474-480`（`:300` の when の並び）、`:485-487`（`:282` の判定の字面そのまま）、`:488-490`（`:302` の agent_host）、`:491-496`（`gnmic_struct` の字面）。`_rr = src[src.index("def read_rows("):src.index("def row_to_record(")]`、`_parsed = src.split("parsed = raw.select(")[1].split("rows = parsed.select(")[0]`
- `:1072-1079` に `pyspark.sql.functions` / `types` の偽物 `_Any`（`__getattr__` / `__call__` / `__getitem__` / `__truediv__` / `__and__` / `__or__` が全部 self を返す）があり、`:1128` で `mod.read_rows(_sp1, "b:9098", "metrics")` を偽物で実行する（readStream の option を見るため）
- `tests/test_stream.py:287-351` が `gnmic_message` を本物の入力で動かす。`_ge` は gnmic のソースから組んだ `interface_state` の event で**実物ではない**（`:289` のコメント）。`:346-349` で deletes だけ・`values=None`・timestamp が文字列や小数のときに None を確かめているが、**キー `values` そのものが無い実物の形は入力にしていない**
- pyspark は手元に入っていない（`uv run --group dev --group web python -c 'import pyspark'` → `ModuleNotFoundError`）。この Mac（arm64）に Java も無い。本物の Spark は Docker のイメージ `nwc-local-spark`（`docker/images/spark/Dockerfile`。`apache/spark:3.5.9-java17-python3`。arm64 もある multi-arch で、この Mac では arm64 のイメージができる（build で確かめた）。`snmp_sinks.py` を `/opt/nwc/snmp_sinks.py` に COPY）か AWS にしか無い
- 件数の基準（main 5da99bd）: test_analytics 515、test_stream 106、`bash ops/check.sh` は 16 本すべて通過

### docs のずれ

- `app/spark/snmp_sinks.py:28-31`（モジュールの docstring）: 「read_rows が形で見分けて（fields が無く、values か deletes がある）」「deletes だけの event は捨てる」
- `docs/collection.md:227`: 「values の無い event（tags だけ。400 件のうち 358 件）もあり、`gnmic_message` はこれを捨てる。」 — 双子の `gnmic_message` は捨てるが、`read_rows` は捨てていなかった（上の `:282`）。同 `:232`: 「Spark が、形で見分けて（`fields` が無く `values` か `deletes` がある）」
- `docs/troubleshooting.md` の「パイプラインと WORKFLOW」の表（`:106-`）にこの症状の行が無い
- `app/gnmic/gnmic.yaml.in:4`: 「processor は使わない」。gnmic には空の event を落とす processor（`event-drop`）があるが、このサイクルでは使わない（下の「やらないこと」）

## 設計方針

### 1. gnmic の判定は「fields が無い」だけにする

`:282` を次にする。

```python
gnmic = msg["fields"].isNull()
```

Telegraf の JSON（trap / syslog-ng / `telegraf` の json serializer）は必ず `fields` を持つ。flows は `:300` の最初の when でトピックで先に分けている。したがって「fields が無い」行は gnmic の event か、どの形でもない壊れた JSON のどちらかで、どちらも `gnmic_struct` に送ってよい（values が無ければ timestamp が null になり `:322` で捨てる）。`:302` の agent_host も同じ `gnmic` 列を使うので、書き換えは `:282` の 1 行で両方が直る。

**行動の変化**: 今までは「fields も values も deletes も無いが timestamp のある JSON」が Telegraf の行として（fields が空のまま）通っていた。これからは捨てる。fields の無い行は格納先で使い道が無いので受け入れる。

### 2. values の無い gnmic の event は捨てる

`gnmic_struct` の `:357` はそのまま（values が無ければ timestamp が null）。deletes だけの event も、values も deletes も無い event も、同じ理由で `:322` が捨てる。`gnmic_message`（`:377`）もそのまま。

### 3. timestamp の範囲で守る

秒の timestamp が取りうる範囲の外の行は、`ts` に変換する前に捨てる。定数を 1 つ足し、`parsed` の最後に where を付ける。

```python
TIMESTAMP_MAX = 4102444800   # 2100-01-01T00:00:00Z の秒。これを超える timestamp（ナノ秒を秒と読んだ値など）の行は捨てる（cycle 025。1 行で collect() が死なないため）
```

```python
    parsed = raw.select(
        ...
    ).where(F.col("m.timestamp").between(0, TIMESTAMP_MAX))
```

- `m.timestamp` が null の行は `between` が null になるのでここで落ちる。`:322` の `.where(F.col("ts").isNotNull())` は残す（字面を縛るテスト `:471` があり、二重でも害が無い）
- 範囲は Telegraf の行にも flows の行にも掛かる（flows は `:293` でナノ秒を秒に直した後の値）。機器の時計が 2100 年を超えていたら捨てるが、そんな行は格納先でも使えない
- `gnmic_message` と `flow_message` には付けない。これらは「読み替え」の双子で、範囲の守りは `read_rows` だけの仕事（docstring にそう書く）

### 4. `parse_rows` を切り出して、本物の Spark でバッチの DataFrame を通せるようにする

`read_rows` の `:278`（`value = ...`）から `:323`（`return rows`）までを、モジュールの関数 `parse_rows(raw)` に切り出す。`read_rows` は reader を組んで `return parse_rows(reader.load())` にする。読み替えの中身は 1 文字も変えない（1〜3 の変更を除く）。

これで、Kafka を立てなくても `spark.createDataFrame(...)` で作った `value` / `topic` / `partition` / `offset` の DataFrame を同じ経路に通せる。`tests/spark_parse_check.py`（**`test_*.py` ではないので `ops/check.sh` の glob には入らない**。本物の Spark が要る）を足し、Docker のイメージの中で動かす。

```bash
docker build -f docker/images/spark/Dockerfile -t nwc-local-spark:parse-check app/spark
docker run --rm \
  -v "$PWD/app/spark/snmp_sinks.py:/opt/nwc/snmp_sinks.py:ro" \
  -v "$PWD/tests/spark_parse_check.py:/opt/nwc/spark_parse_check.py:ro" \
  nwc-local-spark:parse-check /opt/spark/bin/spark-submit --master 'local[1]' /opt/nwc/spark_parse_check.py
docker rmi nwc-local-spark:parse-check
```

タグは確認用の `nwc-local-spark:parse-check` にし、終わったら `docker rmi` で消す。手元の compose が使う `nwc-local-spark`（`:latest`）を、このブランチの `snmp_sinks.py` を焼いたイメージで上書きしないため。`--platform` は付けない（イメージが arm64 でできるので、arm64 の Mac でそのまま動く）。

`spark_parse_check.py` の中身:

- `sys.path` に `/opt/nwc` を足して `snmp_sinks` を import
- 入力は 6 件。`value` は JSON を UTF-8 の bytes にしたもの、`topic` / `partition` / `offset` はそれらしい値
  1. 上の実物の event（`metrics`。values も deletes も無い）
  2. 1 に `"values":{"/srl_nokia-interfaces:interface/statistics/in-octets":"12345"}` を足したもの（`metrics`）
  3. deletes だけの event `{"name":"interface_state","timestamp":1791534762523125553,"tags":{...},"deletes":["/srl_nokia-interfaces:interface/oper-state"]}`（`gnmi`）
  4. Telegraf の trap `{"fields":{"oid":"..."},"name":"snmp_trap","tags":{"agent_host":"203.0.113.11","host":"telegraf"},"timestamp":1791534762}`（`traps`）
  5. 4 の timestamp を 1791534762523125553 にしたもの（fields はある。範囲の守りだけが効く）
  6. fields も values も無く timestamp が秒の event `{"name":"interface_stats","timestamp":1791534762,"tags":{...}}`（`metrics`。範囲の内なので、判定だけが効く）
- `rows = snmp_sinks.parse_rows(raw)`、`got = rows.collect()`（ここで `ValueError` が出ないことが確認の中心）
- 期待は 2 行。`topic` / `measurement` / `agent_host` / `ts`（UTC の ISO）/ `fields_json` を print し、期待と違えば `NG` を print して exit 1、合えば `OK 2 rows` を print して exit 0
- 期待値: 2 → `metrics` / `interface` / `203.0.113.11` / `2026-10-09T08:32:42` / `{"in_octets":"12345"}`、4 → `traps` / `snmp_trap` / `203.0.113.11` / `2026-10-09T08:32:42` / `{"oid":"..."}`。`ts` は Spark の session timezone に依るので、`spark.conf.set("spark.sql.session.timeZone", "UTC")` を最初に打つ

**赤→緑の確認**: 古い版（main）には `parse_rows` が無いので、赤は直した版から作った一時ファイルで取る。2 つある。

- 判定だけ赤: 直した版の判定（1 の `gnmic = msg["fields"].isNull()`）だけを `:282` の古い字面（`msg["fields"].isNull() & (msg["values"].isNotNull() | msg["deletes"].isNotNull())`）に戻した一時ファイル。入力 6 が Telegraf の行として残り（入力 1 と 3 は範囲の守りが落とす）、`NG 3 rows` を print して exit 1
- 範囲も外した赤: 判定を古い字面に戻し、3 の where も外した一時ファイル（main と同じ読み替え）。`collect()` が `ValueError: year 173875 is out of range` で死ぬ

一時ファイルを `/opt/nwc/snmp_sinks.py` にマウントして同じコマンドを打ち、出力を `build.md` に貼る。そのあと直した版で `OK 2 rows` を貼る。

この Mac は arm64 で、`apache/spark:3.5.9-java17-python3` は arm64 もある multi-arch なので、イメージは arm64 でできて `--platform` 無しで動く（実装で確かめた。`build.md`）。

### 5. テスト

`tests/test_analytics.py`:

- `:485-487` の check を新しい字面に替える: `'gnmic = msg["fields"].isNull()' in _rr` かつ `'msg["values"].isNotNull() | msg["deletes"].isNotNull()' not in _rr`（説明文も「fields が無い行を gnmic の event とする。values も deletes も無い実物の event（2026-10-09 の AWS。400 件中 359 件）は gnmic_struct で timestamp が null になり捨てる」に替える）
- 足す check（4 件）:
  - `TIMESTAMP_MAX` は 4102444800（`re.search(r'^TIMESTAMP_MAX\s*=\s*4102444800\s', src, re.M)`）
  - `_parsed` に `.where(F.col("m.timestamp").between(0, TIMESTAMP_MAX))` がある（`_parsed` の取り方は変えない。where は `rows = parsed.select(` より前に来る）
  - `parse_rows(raw)` が関数としてあり（`"parse_rows" in funcs`）、`read_rows` が `return parse_rows(reader.load())` で終わる（`_rr` に字面がある）
  - `gnmic_message` と `flow_message` に `TIMESTAMP_MAX` が出ない（`"TIMESTAMP_MAX" not in src[src.index("def flow_message("):src.index("def gnmic_struct(")]` と `gnmic_message` の範囲も同様）
- `:1078` の `__and__` のコメント「gnmic の event の見分け」を「where の条件（ts.isNotNull() & …）など」に直す（字面だけ。偽物の動きは変えない）
- `:1128` の `mod.read_rows(_sp1, ...)` は偽物のまま通ること（`parse_rows` が偽物の F / T で動く）

`tests/test_stream.py`:

- 実物を定数にする（字面をそのまま。コメントに出所「2026-10-09 の AWS の `metrics`。`docs/verification/20261009-aws-managed-2.md` 不具合 1」）: `_ge_real = {"name": "interface_stats", "timestamp": 1791534762523125553, "tags": {"interface_name": "ethernet-1/7", "source": "203.0.113.11", "subscription-name": "interface_stats"}}`
- 足す check（2 件）:
  - `mod.gnmic_message(_ge_real) is None`（values のキーそのものが無い実物は捨てる）
  - `mod.gnmic_message(dict(_ge_real, values={"/srl_nokia-interfaces:interface/statistics/in-octets": "12345"})) == {"timestamp": 1791534762, "name": "interface", "tags": {"ifName": "ethernet-1/7", "source": "203.0.113.11", "subscription-name": "interface_stats"}, "fields": {"in_octets": "12345"}}`
- `:289` のコメント「実物ではない」の後に「実物は `_ge_real`（下）」と足す

### 6. docs

- `app/spark/snmp_sinks.py:28-31` の docstring: 「形で見分けて（fields が無い）」「values の無い event（deletes だけ、または values も deletes も無いもの。2026-10-09 の AWS では `metrics` の 400 件中 359 件）は捨てる」「timestamp が 0〜2100 年の範囲の外の行は捨てる（`TIMESTAMP_MAX`）」
- `docs/collection.md:227`: 「values の無い event（tags だけ。400 件のうち 359 件）もあり、`read_rows` も `gnmic_message` もこれを捨てる（025 で `read_rows` の判定を直した。それまでは Telegraf の行として通り、sinks が落ちていた）」。`:232` の「（`fields` が無く `values` か `deletes` がある）」→「（`fields` が無い）」
- `docs/troubleshooting.md` の「パイプラインと WORKFLOW」の表に 1 行: 症状「`sinks-splunk` / `sinks-grafana` のジョブが数分で落ちて立ち直りを繰り返し、ログに `ValueError: year 173875 is out of range`」、原因と直し方「gnmic の values の無い event を Telegraf の行として読んでいた（025 で直した）。直す前のイメージ（`snmp_sinks.py` のハッシュ）で立っていないかを見る」
- `tests/README.md` の表に `spark_parse_check` の行（`ops/check.sh` では回らない、Docker のイメージの中で本物の Spark で `parse_rows` を 6 件通す）
- `docs/verification/20261009-aws-managed-2.md` は記録なので触らない

## やらないこと

- **gnmic の側で空の event を出さない設定（`event-drop` processor）**: `gnmic.yaml.in` は「processor は使わない」方針で、変えれば AWS で収集を確かめ直す必要がある。Spark はどんなメッセージでも死なないことが先。BACKLOG に別の行で残す（Kafka の 9 割が空の event なので、流量を減らす価値はある）
- `gnmic_struct` の読み替えの表（`GNMI_MEASUREMENTS` / `GNMI_TAGS`）の変更
- `row_to_record` / `send_partition` の変更（`ValueError` は `collect()` の中、Python の関数に来る前に出る。Python の側では守れない）
- `gnmi` トピックに on-change の event が 1 件も来ない件（BACKLOG の別の行）
- Iceberg に書かれた壊れた行の掃除（`ops/down.sh` で S3 Tables ごと消えている）
- `docker/compose/` の変更（`spark-splunk` / `spark-http` は同じ `snmp_sinks.py` を焼いた同じイメージなので、直せば一緒に直る）

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `app/spark/snmp_sinks.py` | `:282` の判定、`TIMESTAMP_MAX` の定数（`GNMI_NS` の次）、`parsed` の where、`read_rows` → `parse_rows(raw)` の切り出し、docstring（`:28-31`、`read_rows` / `parse_rows` / `gnmic_message` / `flow_message`） |
| `tests/test_analytics.py` | `:485-487` の字面、check 4 件の追加、`:1078` のコメント |
| `tests/test_stream.py` | `_ge_real` と check 2 件、`:289` のコメント |
| `tests/spark_parse_check.py` | 新規。本物の Spark で `parse_rows` に 6 件通す |
| `tests/README.md` | `spark_parse_check` の行 |
| `docs/collection.md` | `:227`、`:232` |
| `docs/troubleshooting.md` | 「パイプラインと WORKFLOW」の表に 1 行 |

## 再利用するもの

- `gnmic_struct`（`:343-361`）: 変えない。values が無いとき timestamp を null にする既存の動きが、捨てる仕組みそのもの
- `gnmic_message`（`:364-385`）: 変えない。実物を渡しても None になる（テストで縛る）
- `tests/test_analytics.py` の偽物 `_Any` / `_Spark`（`:1072-1130`）: `parse_rows` もこの偽物で動く
- `docker/images/spark/Dockerfile` のイメージ `nwc-local-spark`: 本物の Spark の確認に使う。Dockerfile は変えない（`snmp_sinks.py` と check のスクリプトはマウントで差し込む）
- `docs/verification/20261009-aws-managed-2.md` の実物の event: テストの入力にそのまま使う

## 実装ステップ

commit は 3 つに分ける。

1. **`snmp_sinks.py` を直す**: `:282` の判定、`TIMESTAMP_MAX`、`parsed` の where、`parse_rows` の切り出し、docstring。`tests/test_analytics.py` の `:485-487` と `:1078` と追加の check、`tests/test_stream.py` の `_ge_real` と check を同じ commit に入れる（字面のテストが直す前の版で落ちるため）。`uv run --group dev --group web python tests/test_analytics.py` と `tests/test_stream.py` を回して件数を `build.md` に貼る
2. **`tests/spark_parse_check.py` を足す**: Docker で赤（判定だけ古い字面に戻した一時ファイルで `NG 3 rows`、範囲の where も外した一時ファイルで `ValueError`）→ 緑（直した版）を取り、出力を `build.md` に貼る。動かなければ、どこで止まったか（イメージの build / Spark の起動）と「未確認」を書く。`tests/README.md` の行も同じ commit
3. **docs**: `docs/collection.md`、`docs/troubleshooting.md`

セルフレビュー（`/robust`）は 1 と 2 の後にまとめて 1 回。cold review の Must fix は 0 にし、Should fix は直さず PM に報告する。

## 検証方法（期待出力まで）

1. `uv run --group dev --group web python tests/test_analytics.py` → 最後の行が `519 passed`（515 + 4。`:485` の check は置き換えなので件数に入らない）。失敗 0
2. `uv run --group dev --group web python tests/test_stream.py` → `108 passed`（106 + 2）
3. `bash ops/check.sh` → 16 本すべて通過（ほかの本の件数は main と同じ: test_agentcore 161、test_graph 78、test_sync 103、test_workflow 327、test_alerts 168、test_kb_index 7、test_lab_debug 97、test_nautobot 68、test_oss 174、test_oss_ops 200、test_oss_roll 66、test_local_compose 138、test_collectors 79、test_dashboard_config 3）
4. 赤の確認: (a) 判定だけ古い字面の一時ファイルをマウントすると、`docker run ... spark_parse_check.py` が 3 行（2、4 と、Telegraf の行として残った 6）を print し、`NG 3 rows` で exit 1。(b) 判定を古い字面に戻し範囲の where も外した一時ファイルでは、`ValueError: year 173875 is out of range` を含むトレースで exit 1。出力の該当行を `build.md` に貼る
5. 緑の確認（直した版）: 同じコマンドで最後の行が `OK 2 rows`、exit 0。2 行の `topic` / `measurement` / `agent_host` / `ts` / `fields_json` が「設計方針 4」の期待値と一致
6. `grep -n 'values か deletes' app/spark/snmp_sinks.py docs/collection.md` → 0 件（docs のずれが消えている）
7. `git diff --stat origin/main` の変更が「変更対象ファイル」の 7 本だけ

## 未確定事項とリスク

1. **イメージが arm64 の Mac の Docker 29 で動くか**（実装で確かめた。`apache/spark:3.5.9-java17-python3` は multi-arch で、arm64 のイメージが `--platform` 無しで動いた。`build.md`）。本物の Spark の確認はバッチの DataFrame だけなので、streaming と EMR Serverless は次の AWS の検証で `sinks-splunk` / `sinks-grafana` が RUNNING を保つこと（`aws emr-serverless list-job-runs` で `state=RUNNING` が 10 分以上続く）と、Splunk / AMP / OpenSearch に `interface` の行が入ることで確かめる
2. **Spark の中で年 173875 がどう表現されていたか**（未確認）。`from_unixtime` → `to_timestamp` の往復で null にも例外にもならず `collect()` まで届いた事実だけ分かっている。守りを `m.timestamp` の long の範囲で入れるのはこのため（`ts` の値に依らない）
3. **values の無い event が「統計の無いインターフェース」だけで出るのか**（未確認）。400 件中 359 件という比率は、SR Linux の 20 のインターフェースのうち値を返すのが数本しかないことを示すが、同じインターフェースで values のある event と無い event が交互に出るかは見ていない。どちらでも直し方は変わらない
4. **`TIMESTAMP_MAX` の 2100 年**: 機器の時計が狂って未来の値を出す行は黙って捨てる。捨てた件数はログに出さない（streaming の each_batch の外で数える手段を足すと範囲が広がる）。必要になったら別のサイクル
5. **「fields が無い JSON」の扱いの変化**（設計方針 1 の「行動の変化」）: Telegraf 以外の何かが `fields` 無しで `timestamp` のある JSON を書いていたら、今までは通り、これからは捨てる。いまの送り手（Telegraf、syslog-ng、GoFlow2、gnmic）にそれは無い
6. **Iceberg の `raw_telemetry` に壊れた行が書かれていた可能性**: 環境ごと消えているので確かめられない。次の AWS の検証で `SELECT max(ts) FROM raw_telemetry` が 2100 年より前であることを見る
