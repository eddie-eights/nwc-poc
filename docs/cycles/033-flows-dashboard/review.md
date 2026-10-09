# flows（GoFlow2）の Grafana のダッシュボードを作る（033）のレビュー

## Round 1

実行モデル: cold reviewer は opus（初回ビルド直後に依頼した。Must fix 0 なので完了判定のラウンドも兼ね、2 回目は呼ばない）。確認は PM（fable-5-1）。実装は opus（build.md）。

対象: `948d954..761780c`（origin/feat/033-flows-dashboard。032 の 948d954 の上）。

### cold reviewer の結果

## サマリ
「flows（GoFlow2）の Grafana のダッシュボードを作る（033）」の Round 1 をレビューした。対象は `git diff 948d954 HEAD`（HEAD = 761780c）。9 ファイル、+548 / -5。中身は新規の `flows.json`（362 行）、`start.sh` の cp 1 行、datasource のコメント 2 行、`tests/test_oss.py` の check 3 本（+45）、docs 3 本、`build.md`。

全体としては設計どおり。uid、title、tags、time、refresh、datasource、`topic:flows`、`timeField`、panel 6 つの型と集計が、どれも design.md の表と一致する。設計との差（panel 1 の title、上位 N を table にしたこと、check の置き場所、description を足したこと）は build.md の「設計との差」に全部書いてあり、どれも設計の意図の範囲に収まる。Must fix は無い。

### 見た観点 / 見ていない観点
見た観点

- [design.md との整合性] design.md の A / B / C を 1 つずつ、`flows.json`・`start.sh`・`test_oss.py`・docs と突き合わせた。「やらないこと」も確かめた: diff に alerting や Splunk の変更は無く、transform も無い。
- [correctness] 各 panel の query を、雛形の `logs.json` と比べた（datasource、`queryType`、`timeField`、terms と date_histogram の並べ方、`orderBy` が sum の id `"1"` を指していること）。field の名前は `app/spark/snmp_sinks.py` で確かめた。tags は `FLOW_TAGS` の名前で、値は文字列（`flow_map` が MapType(String,String) で作る）なので `.keyword` が付く。`fields` は `opensearch_docs` の `_number` で float になる（`snmp_sinks.py:467-475`、`:538`）。gridPos が重ならないことも見た（y = 0 / 7 / 16 / 24 で、2〜4 は x = 0 / 8 / 16、w = 8）。
- [runtime] `start.sh` は `bash -n app/grafana/start.sh` が無言で終わる（自分で実行）。cp の元になる `$SRC/dashboards/flows.json` は、Dockerfile の `COPY provisioning /etc/grafana/nwc` でイメージに入る。provider（`dashboards/nwc.yaml`）は `/tmp/grafana-dashboards` を読むので、ほかに変えるものは無い。
- [missing tests] テストを自分で実行した（HEAD 761780c）。
  - `uv run --frozen python3 tests/test_oss.py` → `通過 177 / 失敗 0`
  - `uv run --frozen python3 tests/test_analytics.py` → `通過 534 / 失敗 0`
  - `uv run --frozen python3 tests/test_local_compose.py` → `通過 143 / 失敗 0`
  - `uv run --frozen python3 tests/test_alerts.py` → `通過 168 / 失敗 0`
  - `uv run --frozen python3 tests/test_sync.py` → `通過 103 / 失敗 0`
  - `python3 -c 'import json; json.load(open(".../flows.json"))'` は通った。
  - check の中身も読んだ。uid と panel の数、datasource、query、timeField、集計 field と `FLOW_TAGS` / `FLOW_FIELDS` の照合、panel ごとの型・title・bucketAggs・metric・unit・terms の size / order / orderBy、start.sh の分岐（Prometheus だけ / OpenSearch だけ / 両方）を縛っている。
- [security / data loss] 変更は provisioning の JSON とコメント、`/tmp` への cp 1 行、テスト、docs だけ。機密情報や外部入力の扱いは増えていない。`rm -rf` の対象も変わっていない。
- [API compatibility] `_G_FILES` と「ダッシュボード 3 つ」の文言は、start.sh の出力と一致する（test_oss が通る）。`logs.json` と `metrics.json` は変わっていない。
- [docs] `docs/pipeline.md`・`docs/oss-variant.md`・FAQ の 1198 行・`datasources-oss/opensearch.yaml:3` の記述を、design.md の C-4 と事実（`docs/collection.md:25`、`ops/netflow_send.py` の docstring）に照らした。

見ていない観点

- Grafana と OpenSearch は立てていない。panel が実際に描けるか、query が 200 で返るかは、build.md に書かれた手元の compose の出力を読んだだけで、再現はしていない。
- AWS（OpenSearch Serverless の TIMESERIES コレクション）で `tags.*.keyword` と `fields.bytes`（float）の mapping がこのとおりになるかは見ていない。repo に index template は無い。design.md でも AWS の確認は実装の範囲外になっている。
- grafana-opensearch-datasource 2.34.4 が logs metric の `settings.limit` を効かせるかは、plugin のソースを見ていないので確かめていない（Nit 2）。
- type safety は対象外（JSON と shell と docs が中心）。

## Must fix
None

## Should fix
- [design.md との整合性] `docs/cycles/033-flows-dashboard/design.md` の「検証方法」の AWS の項（「lab の sFlow で panel 1〜6 に値が入る」）が事実と食い違っている。`docs/collection.md:25` には「受け口だけ（lab の機器からは来ない）」とあり、`ops/netflow_send.py` の docstring にも「lab の SR Linux は NetFlow を出さない」とある。実装側（panel の description と `docs/pipeline.md:425`）は「ふだんは空、`ops/netflow_send.py` で 1 本送ると出る」と正しく書いている。正本の design.md だけが古い前提のまま残っている。PM がこの手順どおり AWS で確かめると、全 panel が空のままになり、壊れていると読み違えるおそれがある。
  - 分類理由: コードは壊れず、困るのは AWS で確かめる PM の 1 回だけ。だから Should にした。build.md の「設計との差」で PM に回してあるが、design.md 側が直ったかどうかはこの diff では確認できない。確認すべきは、AWS で確かめる前に design.md の検証手順へ「`ops/netflow_send.py <NLB>:2055` で送ってから見る」が入っていること。

## Nit
- [missing tests] `tests/test_oss.py:1906` の `_fl_panel_ok` は `(t,) = p["targets"]` で 1 件を取り出している。target が 2 件以上になると、check の NG ではなく ValueError でスクリプトが止まる。テストが落ちること自体は変わらないが、どの check で落ちたかが出力に出ない。体裁の問題なので Nit にした。
- [correctness（未確認）] `flows.json` の panel 6 は title に「最新 100 件」とあり、`settings.limit: "100"` を渡している。build.md の手元の確認は 60 件で、100 件で打ち切られることまでは見ていない。plugin が `limit` を見ていなければ plugin の既定の件数が出て、title と合わない。描画は壊れないので Nit にした。
- [correctness（このサイクルの範囲外）] `fields.bytes` は `_number` が float にし、dynamic mapping でも `float` になる（build.md の手元の mapping）。OpenSearch の float は仮数が 24 bit なので、1 flow の bytes が 16,777,216 を超えると集計に使う値が丸まる。sum は double で足すが、元の値はもう丸まっている。原因は 012 の `opensearch_docs` で、この diff ではない。1 flow がその大きさになることは少ないので Nit にした。
- [docs] `docs/pipeline.md:420` に新しく足した「ダッシュボードは `app/grafana/provisioning/dashboards` の 3 つ。」の直後（`:426`）に、もとからある「ダッシュボードは `app/grafana/provisioning` だけで、…」が続き、同じ書き出しの箇条が 2 つ並ぶ。1 つの箇条にまとめると読みやすい。体裁だけなので Nit にした。
- [design.md との整合性] 同じ description の文（サンプリング率と、ふだんは空のこと）が panel 1〜5 の 5 か所に同じ字面で入っている。直すときに 1 か所だけ残すと食い違う。dashboard の JSON には共通化の仕組みが無いので、好みの範囲として Nit にした。

## 良かった点
- query の形（datasource の書き方、`queryType`、`timeField`、terms → date_histogram の順、`min_doc_count`）が `logs.json` とそろっていて、雛形をそのまま延ばしている。
- check が field の名前をハードコードせず、`snmp_sinks.py` の `FLOW_TAGS` / `FLOW_FIELDS` を import して照合している。Spark 側で名前が変わると赤になる。
- start.sh の分岐を 3 つの組（Prometheus だけ / OpenSearch だけ / 両方）で縛っていて、`flows.json` を置く分岐を間違えると落ちる。build.md の退行の注入 19 件が、それぞれどの check で落ちるかも書いてある。
- sampled な bytes を実流量と読み違えないよう、panel と docs の両方に「サンプリング率は掛けていない」と書いてある。「AWS では未確認」も明記していて、確かめた範囲を盛っていない。

## ユーザーへの質問
None


### PM の確認

**再現したもの**

- Should の前提: `docs/collection.md` の「受け口だけ（lab の機器からは来ない）」と `ops/netflow_send.py` の docstring を読んだ。design.md の検証方法の AWS の項だけが「lab の sFlow で値が入る」のまま残っていた（build.md の S1 が PM に回していた）。
- テスト（PM、docs を直したあと）: test_oss `通過 177 / 失敗 0`、test_analytics `通過 534 / 失敗 0`。test_local_compose 143 / test_alerts 168 / test_sync 103 は cold reviewer の実測（761780c）を引く。

**直したもの**

- Should（design.md の AWS の検証手順）: 「Web の EC2 から `ops/netflow_send.py <NLB>:2055` で 1 本送ってから panel 1〜6 を見る。送る前は全 panel が空で正常」に書き換えた。
- Nit 4（`docs/pipeline.md` の同じ書き出しの箇条 2 つ）: 「ダッシュボードは … 3 つ」の箇条の下に「正はこの provisioning だけ」を子の箇条として入れ、1 つにまとめた。

**直さなかったもの（最終報告へ）**

- Nit 1（`tests/test_oss.py` の `(t,) = p["targets"]` が target 2 件で ValueError）: 落ちることは変わらない。
- Nit 2（panel 6 の `settings.limit: "100"` を plugin が見るか）: AWS の確認で 100 件を超える flow は送らないので、このサイクルでは確かめない。
- Nit 3（`fields.bytes` が float で 2^24 を超える値が丸まる）: 012 由来。範囲外。
- Nit 5（panel 1〜5 の description が同じ文で 5 か所）: 体裁。

**未確認**

- Grafana と OpenSearch を手元で立てていない（build.md の手元の画面の出力を読んだだけ）。
- AWS（OpenSearch Serverless）で `tags.*.keyword` と `fields.bytes` の mapping が同じになるか。AWS の 1 回の検証で見る。
