# review — sinks を values の無い gnmic の event で落ちなくする（025）

## Round 1

cold reviewer（opus、general-purpose）に依頼した。レビューのモデル: opus 5.5。

## サマリ

対象は `origin/main`...`origin/fix/025-sinks-drop-empty-gnmic`（2cf5094）の 8 ファイル、+302 / -31。中身は `app/spark/snmp_sinks.py`、`tests/test_analytics.py`、`tests/test_stream.py`、`tests/spark_parse_check.py`（新規）、`tests/README.md`、`docs/collection.md`、`docs/troubleshooting.md`、`docs/cycles/025-.../build.md`。

全体の評価: 設計方針 1〜6 はすべて実装に入っている。判定 `gnmic = msg["fields"].isNull()`、`TIMESTAMP_MAX = 4102444800`、`parsed` の最後の `.where(F.col("m.timestamp").between(0, TIMESTAMP_MAX))`、`parse_rows(raw)` の切り出しの 4 つ。設計からずらした点は build.md の「設計からずらした点」に理由つきで書いてあり、どれも設計の意図に沿っている。本物の Spark 3.5.9 で `parse_rows` を自分で動かし、設計の 5 件と、追加で試した境界の 13 件のどちらでも `collect()` は死ななかった。結果も設計の意図どおり。Must fix は無い。

### 見た観点 / 見ていない観点

見た観点

- **design.md との整合性**: 設計の「変更対象ファイル」7 本と build.md だけが変わっていることを確かめた（`git diff origin/main --stat`）。設計方針 1〜6 の字面は、ソースと突き合わせて確かめた。検証 6 の `grep -n 'values か deletes' app/spark/snmp_sinks.py docs/collection.md` は 0 件だった。
- **correctness**: 本物の Spark で `parse_rows` を動かした。
  - イメージ: `docker build -f docker/images/spark/Dockerfile -t nwc-local-spark:review app/spark`。arm64 の Mac でそのまま build できた。
  - 設計の 5 件: `docker run --rm -v …/snmp_sinks.py:/opt/nwc/snmp_sinks.py:ro -v …/spark_parse_check.py:/opt/nwc/spark_parse_check.py:ro nwc-local-spark:review /opt/spark/bin/spark-submit --master 'local[1]' /opt/nwc/spark_parse_check.py` を打った。出力は `row metrics interface 203.0.113.11 2026-10-09T08:32:42 {"in_octets":"12345"}`、`row traps snmp_trap 203.0.113.11 2026-10-09T08:32:42 {"oid":"1.3.6.1.6.3.1.1.5.3"}`、`OK 2 rows`。
  - 境界の 13 件は、同じイメージで `python3 -c` から直に `snmp_sinks.parse_rows` に通した。リポジトリにはファイルを足していない。結果:

    | 入力 | 結果 |
    |---|---|
    | JSON でない value | 捨てる |
    | value が null（tombstone） | 捨てる |
    | 負の timestamp | 捨てる |
    | fields も values も無く、秒の timestamp を持つ JSON | 捨てる。設計方針 1 の「行動の変化」のとおり。古い判定なら Telegraf の行として残る |
    | `"fields": null` | 捨てる |
    | timestamp = 4102444801 | 捨てる |
    | long を超える timestamp | 捨てる |
    | gnmic の timestamp が小数 | 捨てる |
    | flows（ナノ秒） | 残る。`ts` は 2026-10-09 08:32:42、agent_host は None で前と同じ |
    | `"fields": {}` | 残る |
    | timestamp = 4102444800 | 残る。`ts` は 2100-01-01 00:00:00 で、collect() も通る |
    | `fields` が文字列の gnmic の event | 残る。from_json が fields を null にして gnmic の側に行く |
    | `tags` が文字列の trap | 残る。agent_host は None |

- **runtime bugs**:
  - `read_rows` の引数と戻り値は変わっていない。呼び出し元は `build` の `:1007` だけ。
  - `tests/test_analytics.py` の偽物の F / T で動く `read_rows` は `parse_rows` まで通る（テストで確かめた）。
- **API compatibility**: 呼び出し元を全部見た。`snmp_sinks.py` の中にしか呼び出しが無いことを確かめた。
- **missing tests**: 足した check の中身を読み、何を縛るかを確かめた。テストを実行した結果:
  - `uv run --group dev --group web python tests/test_analytics.py` → `通過 519 / 失敗 0`
  - `uv run --group dev --group web python tests/test_stream.py` → `通過 108 / 失敗 0`
  - `bash ops/check.sh` → exit 0、最後の行は `すべて通過`。出力を grep で絞ったので、本ごとの件数は test_workflow の 327 しか手元に残っていない。
- **security / data loss**: 外から入る値の扱いは、捨てる方向に狭まるだけ。シークレットや認証には触れていない。黙って捨てる行が増える件（2100 年より後、負の値、fields の無い JSON）は、設計の未確定 4 と 5 が受け入れ済みなので、指摘にしない。
- **type safety**: `between(0, 4102444800)` は long 同士の比較で、本物の Spark でも通った。

見ていない観点

- **赤の確認を自分では取っていない**: 判定と範囲の両方を戻した版で `ValueError` が出ることは build.md の記録だけで、自分では回していない。そのかわり、範囲の守りは境界の入力（4102444801、負の値）で、判定の直しは「fields も values も無く秒の timestamp を持つ JSON」で、それぞれ直した版で効いていることを確かめた。
- **streaming（readStream）での実行**: Kafka を立てていない。`parse_rows` はバッチの DataFrame でしか動かしていない。
- **AWS の EMR Serverless での実行**: `sinks-splunk` / `sinks-grafana` が RUNNING を保つかは、設計どおり次の AWS 検証の仕事。
- **後片付け**: 確認に使った `nwc-local-spark:review` のタグは消し、消えたことを確かめた（`docker image ls` で 0 件）。

## Must fix

None

## Should fix

- **[missing tests] `tests/spark_parse_check.py` の 5 件では、判定の直し（`:296` の `gnmic = msg["fields"].isNull()`）を本物の Spark で単独に縛れていない**
  - 場所: `tests/spark_parse_check.py:215-221`（INPUTS）。
  - 破綻シナリオ: 判定を古い字面に戻しても、範囲の where が入力 1 を落とすので `OK 2 rows` になる。build.md の表の 3 行目のとおりで、これは設計の検証 4 の作り方そのものの穴。判定を縛っているのは、`test_analytics.py` の字面の check と、Python の双子 `gnmic_message` の check だけになる。
  - 直す案: 「fields も values も無く、timestamp が秒」の 1 件を足す。たとえば `("metrics", {"name": "interface_stats", "timestamp": 1791534762, "tags": TAGS})`。直した版では捨てられることを、本物の Spark で確かめた（上の境界の入力）。古い判定なら Telegraf の行として残って 3 行になり、NG になる。
  - 分類の理由: 今の挙動は正しく、壊れてもいない。ただ、将来この判定を誰かが字面を変えて戻すと、本物の Spark の確認では気づけない。今すぐではないので Should。

- **[design.md との整合性（軽微）] 設計の検証 4（赤の作り方）は、設計の手順どおりでは赤にならない**
  - 場所: design.md の「検証方法」の 4、「設計方針 4」の赤→緑。実装の build.md「ずらした点 4」で、両方を戻す版と範囲だけ外す版を足して補っている。
  - 何が困るか: design.md はそのままなので、あとで design.md だけを読んで赤を取り直す人は、`OK 2 rows` を見て混乱する。
  - 分類の理由: 実装の誤りではなく設計の記述の誤り。コードは壊れていないので Should。design.md を直すか、上の 1 件を足して「判定だけ戻すと 3 行で NG」になるようにすれば、設計の手順が成り立つ。

## Nit

- **[docs] `docs/collection.md:227` の「400 件のうち 359 件」は、同じ節が出所として挙げる 1 回目の検証と数が合わない**
  - 同じ節の `:218` と `:228` は `docs/verification/20261009-aws-managed.md`（1 回目）を出所にしている。1 回目の `:196` は 358 件で、359 件は 2 回目（`20261009-aws-managed-2.md:39`）の数。
  - 設計の指示どおりの数なので Nit。「（2 回目の検証では 359 件）」のように出所を添えると曖昧さが消える。
- **[docs] `docs/troubleshooting.md` に足した行の「直す前のイメージ（`snmp_sinks.py` のハッシュ）」は、OSS 版と手元の compose にしか当たらない**
  - 不具合が出たのはマネージド版の EMR Serverless で、ここは `ops/up.sh` が S3 に置いた `snmp_sinks.py` を使う。マネージド版向けに「S3 に置かれた `snmp_sinks.py` が直した版か」も書くと、読む人が迷わない。
- **[docs] `tests/spark_parse_check.py` の docstring のコマンド `-t nwc-local-spark` は、手元の compose の image 名（`docker/compose/compose.yaml:53`）と同じ**
  - 回すと compose の `:latest` を、このブランチの `snmp_sinks.py` を焼いたイメージで上書きする。build.md の「ずらした点 3」では、これを避けて `:025-check` を使っている。
  - docstring も別のタグ（例: `nwc-local-spark:parse-check`）にして、最後に `docker rmi` を書く方が安全。設計どおりの字面なので Nit。
- **[保守性] `parse_rows` の schema の `deletes` は、もうどこからも読まれない**
  - 場所: `app/spark/snmp_sinks.py:287`。
  - コメント（`:280`）で理由は書いてあり、test_analytics の check が字面を縛っているので残した、という判断も build.md にある。次に schema を触るときに外す候補。
- **[可読性] flows の行の `agent_host` は、判定が変わって `F.when(gnmic, …)` の gnmic の側（`tags.source`）を通るようになった**
  - GoFlow2 の JSON には `fields` が無いため。`tags` も無いので値は前と同じ null（本物の Spark で確かめた）。
  - `:315` のコメント「gnmic は機器の IP…」からは、flows がこの側を通ることが読み取れない。flows も通る旨を一言添えると、後で `gnmic` 列を別の用途に使う人が迷わない。

## 良かった点

- 守りを `ts` ではなく変換前の long（`m.timestamp`）に入れたので、Spark の timestamp の内部表現に依らない。境界値 4102444800 と 4102444801 で、本物の Spark でも意図どおり分かれた。
- `parse_rows` の切り出しで、Kafka 無しで本物の Spark に同じ経路を通せるようになった。`spark_parse_check.py` は TZ と session timezone の両方を UTC に揃えているので、出力が環境に依らない。
- `read_rows` の公開の形（引数・戻り値）を変えていないので、`build` と既存のテスト（偽物の F / T を含む）が無変更で通る。
- build.md が、設計の赤の作り方が成り立たないことを自分で見つけて、3 通りの赤で補っている。「判定だけ戻すと赤にならない」ことも隠さずに書いている。

## ユーザーへの質問

None

### PM の判定

- Must fix 0
- Should fix 2 → 両方直した（この review.md と同じ commit。直した点は build.md の「Round 1: cold review 後の直し」）
  - Should fix 1: `tests/spark_parse_check.py` に 6 件目（fields も values も無く、timestamp が秒）を足した
  - Should fix 2: design.md の設計方針 4 と検証方法 4 の赤の作り方を、6 件目を前提にした手順（判定だけ戻すと `NG 3 rows`）に直し、`--platform linux/amd64` の記述を外した
- Nit 5 → 直さない。ただし Nit 3（Docker のタグ）は安全側なので一緒に直した（`nwc-local-spark:parse-check` と `docker rmi`。design.md と docstring の両方）。Nit 1・2・4・5 は直さない

根拠: reviewer が本物の Spark で 5 件と境界の 13 件を回し、意図どおりだった（上の表）。PM は diff を読んで、設計方針 1〜6 の字面を照合した（読んだだけ）。

### 直したあとのテスト結果

```
$ uv run --group dev --group web python tests/test_analytics.py
通過 519 / 失敗 0
$ uv run --group dev --group web python tests/test_stream.py
通過 108 / 失敗 0
$ bash ops/check.sh
（exit=0。最後の行）すべて通過
```

Docker の緑（直した版。`nwc-local-spark:parse-check`、arm64）:

```
row	metrics	interface	203.0.113.11	2026-10-09T08:32:42	{"in_octets":"12345"}
row	traps	snmp_trap	203.0.113.11	2026-10-09T08:32:42	{"oid":"1.3.6.1.6.3.1.1.5.3"}
OK 2 rows
exit=0
```

Docker の赤（判定だけ古い字面に戻した一時ファイル）:

```
row	metrics	interface	203.0.113.11	2026-10-09T08:32:42	{"in_octets":"12345"}
row	traps	snmp_trap	203.0.113.11	2026-10-09T08:32:42	{"oid":"1.3.6.1.6.3.1.1.5.3"}
row	metrics	interface_stats	None	2026-10-09T08:32:42	None
NG 3 rows（期待は 2 行: [('metrics', 'interface', '203.0.113.11', '2026-10-09T08:32:42', '{"in_octets":"12345"}'), ('traps', 'snmp_trap', '203.0.113.11', '2026-10-09T08:32:42', '{"oid":"1.3.6.1.6.3.1.1.5.3"}')]）
exit=1
```

使った `nwc-local-spark:parse-check` は `docker rmi` で消し、`docker image ls nwc-local-spark` に残っていないことを確かめた。
