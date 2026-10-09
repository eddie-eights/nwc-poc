# build — sinks を values の無い gnmic の event で落ちなくする（025）

実装モデル: opus-5.5 / effort: high

2026-10-09。基点は main の 65b97d3（PR #27 のマージ後）。ブランチは `fix/025-sinks-drop-empty-gnmic`。

## commit

| commit | 内容 |
| :--- | :--- |
| 6740a6f | ステップ 1: `snmp_sinks.py`（判定、`TIMESTAMP_MAX`、`parsed` の where、`parse_rows` の切り出し、docstring）と `tests/test_analytics.py` / `tests/test_stream.py` |
| 4ad0d14 | ステップ 2: `tests/spark_parse_check.py`（新規）と `tests/README.md` |
| （この commit） | ステップ 3: `docs/collection.md` / `docs/troubleshooting.md`、`snmp_sinks.py` のコメント 1 行（検証 6 の grep に掛かった字面）、この build.md |

## ステップ 1: `snmp_sinks.py` を直す

- `gnmic = msg["fields"].isNull()` にした（values / deletes を要求しない）。`agent_host` の列も同じ `gnmic` を使うので一緒に直る
- `TIMESTAMP_MAX = 4102444800` を `GNMI_NS` の次に足し、`parsed = raw.select(...)` の最後に `.where(F.col("m.timestamp").between(0, TIMESTAMP_MAX))` を付けた。`rows` の `.where(F.col("ts").isNotNull())` は残した
- `read_rows` は reader を組んで `return parse_rows(reader.load())` で終わる。読み替えは新しいモジュールの関数 `parse_rows(raw)`（`read_rows` の直後、`flow_message` の前）
- docstring: モジュール（gnmic の段落）、`read_rows`、`parse_rows`、`flow_message`、`gnmic_struct`、`gnmic_message`

テスト（`tests/test_analytics.py` の判定の字面を差し替え、check 4 件を足し、偽物 `_Any.__and__` のコメントを直した。`tests/test_stream.py` に `_ge_real` と check 2 件）:

```
$ uv run --group dev --group web python tests/test_analytics.py
通過 519 / 失敗 0
$ uv run --group dev --group web python tests/test_stream.py
通過 108 / 失敗 0
```

（main は 515 と 106。+4 と +2 で設計の期待どおり。`:1128` の `mod.read_rows(_sp1, ...)` は偽物の F / T のまま `parse_rows` まで通った）

## ステップ 2: `tests/spark_parse_check.py` と Docker の赤→緑

イメージ: `docker build -f docker/images/spark/Dockerfile -t nwc-local-spark:025-check app/spark`（10 分以内に終わった）。
**`apache/spark:3.5.9-java17-python3` は arm64 もある multi-arch で、この Mac では arm64 のイメージができた**（`docker image inspect … --format '{{.Architecture}}'` → `arm64`）。
`--platform linux/amd64` は付けずに `docker run --rm -v <snmp_sinks.py>:/opt/nwc/snmp_sinks.py:ro -v <spark_parse_check.py>:/opt/nwc/spark_parse_check.py:ro nwc-local-spark:025-check /opt/spark/bin/spark-submit --master 'local[1]' /opt/nwc/spark_parse_check.py` で回した。

赤の一時ファイルは直した版から作り、scratchpad に置いてマウントした（3 通り。終わったら消した。`nwc-local-spark:025-check` のタグも消した）。

| マウントした `snmp_sinks.py` | 結果 | exit |
| :--- | :--- | ---: |
| 判定を古い字面に戻し、範囲の where も外した（= main の読み替えと同じ） | `ValueError: year 173875 is out of range`（AWS と同じ） | 1 |
| 範囲の where だけ外した（判定は新しい） | `ValueError: year 173875 is out of range`（入力 5 の trap のナノ秒） | 1 |
| 判定だけ古い字面に戻した（範囲の where はある。設計の「赤」の作り方） | `OK 2 rows`（入力 1 が Telegraf の行になっても範囲の where で落ちる） | 0 |
| 直した版 | `OK 2 rows` | 0 |

赤（判定と範囲の両方を戻した版）の該当行:

```
Traceback (most recent call last):
  File "/opt/nwc/spark_parse_check.py", line 72, in <module>
    sys.exit(main())
  File "/opt/nwc/spark_parse_check.py", line 56, in main
    got = rows.collect()   # ここで ValueError（year … is out of range）が出ないことが確認の中心
  File "/opt/spark/python/lib/pyspark.zip/pyspark/sql/dataframe.py", line 1263, in collect
  ...
  File "/opt/spark/python/lib/pyspark.zip/pyspark/sql/types.py", line 282, in fromInternal
    def wrapped(*args, **kwargs):
ValueError: year 173875 is out of range
```

緑（直した版。docstring とコメントを直し終えた最後のファイルで取り直した。INFO の行を除く）:

```
row	metrics	interface	203.0.113.11	2026-10-09T08:32:42	{"in_octets":"12345"}
row	traps	snmp_trap	203.0.113.11	2026-10-09T08:32:42	{"oid":"1.3.6.1.6.3.1.1.5.3"}
OK 2 rows
```

exit 0。2 行の `topic` / `measurement` / `agent_host` / `ts` / `fields_json` は設計方針 4 の期待値と同じ（trap の oid は設計の `"..."` の代わりに `1.3.6.1.6.3.1.1.5.3`）。

分かったこと: 設計の未確定事項 2（年 173875 が Spark の中でどう通ったか）は、`from_unixtime` → `to_timestamp` が null にも例外にもならず、Python の `datetime` に直すところ（`pyspark/sql/types.py` の `TimestampType.fromInternal`）で初めて落ちる、と本物の Spark 3.5.9 で確かめた。

## ステップ 3: docs

- `docs/collection.md`: 「400 件のうち 359 件」と「`read_rows` も `gnmic_message` もこれを捨てる（025 で直した…）」、「形で見分けて（`fields` が無い）」
- `docs/troubleshooting.md`: 「パイプラインと WORKFLOW」の表の `metrics` の行の次に `ValueError: year 173875 is out of range` の行
- `tests/README.md` はステップ 2 の commit に入れた

## 検証（設計の「検証方法」）

1. `tests/test_analytics.py` → `通過 519 / 失敗 0`
2. `tests/test_stream.py` → `通過 108 / 失敗 0`
3. `bash ops/check.sh`（ステップ 3 の変更を入れた作業ツリーで。exit 0、最後の行 `すべて通過`）

   | 検査 | 件数 |
   | :--- | ---: |
   | test_agentcore | 161 |
   | test_alerts | 168 |
   | test_analytics | 519 |
   | test_collectors | 79 |
   | test_dashboard_config | 3 |
   | test_graph | 78 |
   | test_kb_index | 7 |
   | test_lab_debug | 97 |
   | test_local_compose | 138 |
   | test_nautobot | 68 項目 |
   | test_oss | 174 |
   | test_oss_ops | 200 |
   | test_oss_roll | 66 |
   | test_stream | 108 |
   | test_sync | 103 |
   | test_workflow | 327 |

   terraform fmt、9 つのルートの validate、スクリプトの構文、旧名 netops の検査も通過
4. 赤: 上の表（判定と範囲の両方を戻すと `ValueError`）
5. 緑: `OK 2 rows`、exit 0
6. `grep -n 'values か deletes' app/spark/snmp_sinks.py docs/collection.md` → 0 件（はじめは `parse_rows` の新しいコメントの「values か deletes を要求していたので」が 1 件掛かったので、「values と deletes のどちらかを」に直した）
7. `git diff --stat origin/main` は「変更対象ファイル」の 7 本とこの build.md

## 設計からずらした点

1. **`schema`（`T.StructType`）と `from pyspark.sql import …` も `parse_rows` に移した**。設計は `:278`〜`:323` の切り出しだが、`:279` の `F.from_json(value, schema)` が `read_rows` の前半で組む `schema` を使うため。`read_rows` には reader を組む部分だけが残る。字面を見る check（`_rr` は `read_rows` から `row_to_record` の前まで）は `parse_rows` を含むのでそのまま通る
2. **Docker は `--platform linux/amd64` を付けず arm64 で回した**。イメージが arm64 で build されたため（上）。`spark_parse_check.py` の docstring のコマンドも `--platform` を外した
3. **イメージのタグは `nwc-local-spark:025-check`**（設計は `nwc-local-spark`）。`nwc-local-spark:latest` は手元の compose（006）がほかの worktree から使うので、上書きしないため。終わったら消した
4. **赤の作り方を 3 通りにした**。設計どおり「判定だけ古い字面に戻した」版は、範囲の where が入力 1 も落とすので `OK 2 rows`（赤にならない）。設計の検証 4 の期待（4 行、1 と 3 と 5 の ts が年 173875）は範囲の where も無い状態のこと。そこで「両方戻す」（= main と同じ読み替え。赤）と「範囲だけ外す」（赤）を足した
5. **`spark_parse_check.py` で `spark.sql.mapKeyDedupPolicy=LAST_WIN` も設定した**（`build` と同じ。今の 5 件ではキーは重ならない）。`TZ=UTC` と `time.tzset()` も最初に打つ（`collect()` が返す naive な `datetime` は Python の側の地方時で作られるため）
6. **`tests/README.md` は表ではなく、表の下の「`test_*.py` でないもの」に書いた**（表は `ops/check.sh` が回す `test_*.py` の一覧。`check_splunk_image.py` と同じ扱い）
7. **`gnmic_struct` の docstring も直した**（「deletes だけ」→「deletes だけ、values も deletes も無い」、「read_rows の where」→「parse_rows の where」）。コードは変えていない
8. **ステップ 3 の commit に `snmp_sinks.py` のコメント 1 行が入る**（検証 6 の grep に掛かった字面を直したもの）
9. 赤の確認は、設計の書き方の「`git show origin/main:…` を scratchpad に出し」ではなく、直した版を写して書き換えた（設計の括弧の中の結論どおり。古い版には `parse_rows` が無い）

### セルフレビュー

観点ごとに自分の差分（`git diff origin/main`）を見直した。

- correctness:
  - `fields` が無い行は全部 `gnmic_struct` に行く。values が無ければ timestamp が null → 範囲の where（null の `between` は null）で落ちる。from_json が読めない JSON（null か、全項目 null の struct。どちらでも `fields` は null）も同じ道で落ちる（前も ts が null で落ちていた。行動は同じ）
  - 範囲を通った `m.timestamp` は 0〜4102444800 秒なので、`ts` は Python の `datetime` に必ず入る。collect() で ts が原因の `ValueError` はもう出ない（Docker の「範囲だけ外す」赤で、範囲の where が効いていることも確かめた）
  - `between(0, 4102444800)` の上限は int32 を超えるが、Spark の lit は long になり、`m.timestamp`（long）との比較は本物の Spark で通った
- security: 外から入る値の扱いは狭くなる方向だけ。シークレットや認証には触れていない
- runtime bugs: streaming の DataFrame に where を 1 つ足しただけで、状態を持つ演算は増えていない。`read_rows` の引数と戻り値は同じ（`build` は変えていない）
- data loss:
  - 2100 年より先と負の timestamp の行は黙って捨てる（設計の未確定 4。件数はログに出さない）
  - `fields` が null（`"fields": null`）の Telegraf の形の行は、前は fields が空のまま通り、これからは捨てる（設計方針 1 の「行動の変化」）
- API compatibility: `read_rows(spark, bootstrap, topics, max_offsets_per_trigger=0)` は同じ。`parse_rows` は足しただけ。test_oss / test_local_compose も含め `ops/check.sh` は全部通った
- type safety: 定数は int、where の比較は long 同士
- missing tests:
  - 判定の直し（`fields` だけ）は、Spark の上では範囲の where が入力 1 も落とすので、`spark_parse_check.py` の 5 件では単独では縛れていない（上の「判定だけ戻す」が `OK 2 rows`）。縛っているのは `test_analytics.py` の字面の check と、`test_stream.py` の双子の check だけ
  - `spark_parse_check.py` は `ops/check.sh` に入らない（設計どおり）

`/robust` 相当（1 回）:

- Pre-Mortem 1「直したのに、次の AWS 検証でも sinks が落ちる」: ts が原因の落ち方は範囲の where で塞いだ（範囲を通った値は必ず `datetime` に入る）。マネージド版は `ops/up.sh` が `snmp_sinks.py` を毎回 S3 に置き、OSS 版と手元の compose はイメージのタグが `app/spark/` のハッシュなので、古い版で立つのは古いイメージを指したままのときだけ（troubleshooting の行に書いた）。ほかの列（`ingested_at` は `current_timestamp`）に範囲外の値は入らない
- Pre-Mortem 2「sinks は落ちないが、Grafana / Splunk にデータが来ない」: values のある gnmic の event と Telegraf の行は、Spark の上で 2 行とも期待どおりの `measurement` / `agent_host` / `ts` / `fields_json` で出た。ただし Telegraf の `json_timestamp_units` を誤って `1ms` などにすると、全部の行が範囲の外として黙って捨てられ、ログにも出ない（前は落ちて気づけた）。直さずに Should fix として残す（下）
- Pre-Mortem 3「Spark 以外が `fields` 無しで timestamp のある JSON を書いていて、黙って消える」: いまの送り手（Telegraf / syslog-ng / GoFlow2 / gnmic）にそれは無い（設計の未確定 5）。syslog-ng は Telegraf の形（fields あり）で書き、flows はトピックで先に分かれる

直したもの:

- 検証 6 の grep に `parse_rows` の新しいコメントが掛かった → 字面を直した（ステップ 3 の commit）

直さなかったもの（Should fix として PM に渡す）:

1. 範囲の外で捨てた行の件数を出さない。Telegraf の timestamp の単位を間違えると全部が黙って消える（設計の未確定 4 のとおり、別のサイクル）
2. `spark_parse_check.py` に、判定の直しを単独で縛る入力が無い（例: `fields` も `values` も無く timestamp が秒の event を足せば、古い判定では Telegraf の行として 3 行目が出て NG になる）。設計の 5 件から外れるので足していない
3. `msg["deletes"]` は schema に残るが、どこでも読まなくなった（`test_analytics.py` の check が schema の字面を縛っているので残した）
