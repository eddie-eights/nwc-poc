# gnmic の初回値を落とさず、空の event を Kafka に書かない（030）のレビュー

## Round 1

実行モデル: cold reviewer は opus（初回ビルド直後に依頼した。Must fix 0 なので完了判定のラウンドも兼ね、2 回目は呼ばない）。確認は PM（fable-5-1）。実装は opus（build.md）。

対象: `4d3d071..475840b`（origin/feat/030-gnmic-buffer-event-drop）。

### cold reviewer の結果

## サマリ

対象は `4d3d071..475840b`（origin/feat/030-gnmic-buffer-event-drop）。6 ファイル、+176 / -3。そのうち build.md が 135 行で、コードと設定の差分は `app/gnmic/gnmic.yaml.in`（+25）と `tests/test_stream.py`（+11）、docs が 3 ファイルで +8。

全体の評価: 設定の値（`buffer-size: 10000` / `timeout: 60s` / `event-processors: [drop-empty]` / `processors.drop-empty.event-drop.condition`）は design.md の A / B / C と一致しています。印（`# >>> kafka_auth scram`）の外に置かれていて、scram と none のどちらの描画でも残ります。gnmic 0.49.0 の実物で processor を当てると、values のある event だけが残ることを再現しました。Must fix はありません。

ただし A（初回値の欠落）の直し方は、この差分の docs 自身が「効かない見込みが高い」と書いています。この件は設計側の論点として下の Should fix と「ユーザーへの質問」に回しました。

### 見た観点 / 見ていない観点

- **design.md との整合性（見た）。**
  A / B / C の各項目を diff と突き合わせました。設計に無いものは 2 つです。
  - `docs/collection.md` の 1 行（build.md の「設計との差」に記録あり）。
  - yaml.in のコメントの文言が設計の案と違うこと（「producer が出来る前」を「送り手が詰まっているあいだ」に変えた。build.md に記録あり）。
  どちらも設計の意図からは外れていません。
- **correctness（見た）。**
  gnmic v0.49.0 のソースを読みました（手元のクローンの `ce0d417` = tag v0.49.0）。
  - `pkg/outputs/kafka_output/kafka_output.go:544-563`: `Write` が `timeout` 付きの select で、`msgChan` の容量は `buffer-size`。
  - `:849`: `Producer.Timeout = c.Timeout`。
  - `:512`: RequiredAcks の既定は wait-for-local。
  - `pkg/outputs/output.go:266-`: split-events のとき `ResponseToEventMsgs(..., evps...)` が event-processors を split の前に当てる。
  - `pkg/formatters/event.go:324-`: `ToMap` は values が空なら `values` キーを出さない。
  - `pkg/formatters/event_drop/event_drop.go:117-146`: condition が真なら捨てる。評価がエラーでも捨てる。
  - `pkg/app/subscribe.go:361-398`: `export` は output ごとに goroutine を立てて `wg.Wait()` する。

  以上から、yaml.in のコメントのうち次の 2 点は事実と合っています。
  - 「1 枠は split 前の SubscribeResponse 1 件」
  - 「timeout は Producer.Timeout にも入る（wait-for-local なら使われない）」
- **correctness（動的に見た）。**
  `app/gnmic/gnmic.sh render` を KAFKA_AUTH=none と scram の両方で回しました（偽の値。scratchpad に書き、見たあと消しました）。そのうえで `docker run ghcr.io/openconfig/gnmic:0.49.0 --config <scram の描画> processor --input <実装者の events.txt> --name drop-empty` を打ちました。
  入力は次の 5 件です。
  - values あり 2 件
  - values のキーが無いもの
  - `values: {}`
  - deletes だけのもの

  出力は values ありの 2 件（`interface_state` と `system`）だけでした。build.md の 2(a) と同じ結果です。
- **テスト（見た）。**
  `uv run --frozen python3 tests/test_stream.py` を実行し、最後の行は `通過 110 / 失敗 0` でした。
  新しい check の 2 本は、scram と none の両方で値と processors の中身を等式で見ています。
- **security（見た）。**
  足されたのは設定値とコメントだけです。資格情報の `${...}` の扱いと render の検査は変わっていません。
- **runtime bugs（見た、静的のみ）。**
  `export` が `wg.Wait()` で同期するので、buffer が満杯のときは受信のループが 1 応答あたり最大 60s 止まります。満杯になるのは Kafka が長く止まったときだけなので、Nit に入れました。
- **data loss（見た）。**
  deletes だけの event も捨てるようになります。消費者は Spark（`app/spark/snmp_sinks.py` の `METRIC_TOPICS = "metrics,gnmi"`）だけで、`gnmic_struct` / `gnmic_message` も捨てていたので、下流の振る舞いは変わりません。
- **API compatibility（見た）。**
  `gn get`（`gnmic get --format event`）は出力の event-processors を通らないので、従来どおり空の event も出ます。挙動は変わりません。
- **見ていない観点。**
  - AWS での効果（初回値が入るか、metrics の空 event が 0 になるか）。実行環境が無く、design.md でも PM の範囲です。
  - Kafka や機器を繋いだ起動。
  - `tests/test_analytics.py` の再実行（今回の差分は Spark に触れていません）。
  - type safety は該当がありません（YAML とテストの変更だけ）。

## Must fix

None

## Should fix

- **[design.md との整合性 + correctness] A の直し方の根拠が、この差分の docs の中で食い違っている。**
  - 場所: `docs/pipeline.md:71-72` と `docs/pipeline.md:121-122`、`app/gnmic/gnmic.yaml.in:25`。
  - 72 行目は、1 回目の AWS で gnmi トピック自体が無かったことから「event が gnmic の出力に 1 件も届いていなかった見込みが高い」と書いています。
  - 一方、122 行目は「初期同期の burst がこれ（送り手の詰まり）に当たる見込み」、yaml.in:25 は buffer を「手当て」と書いています。2 つの「見込み」が逆を向いています。
  - ソースから見ると、72 行目のほうが筋が通ります。`Metadata.Full=false`（kafka_output.go:859）なので、producer は接続を待たずに出来ます（build.md 2(b) のログで再現済み）。
  - 送り手が詰まって 5s で捨てるのは、sarama の内部のバッファ（既定の ChannelBufferSize 256 の段）を越えたぶんだけです。burst の先頭は sarama に渡って書かれるので、トピックは出来るはずです。
  - 2 回目の AWS（`docs/verification/20261009-aws-managed-2.md:40`）でも、gnmi の 8 件は全部 `fail-main` の後の変化でした。初回値が 1 件も無いことは、buffer の仮説では説明できません。
  - 困る人と場面: 次の AWS で初回値が無かったとき、読む人は pipeline.md の同じ節で逆の 2 つの見立てを読みます。troubleshooting.md:125 も「まず buffer-size / timeout があるか」を先に見させます。
  - 壊れるものは無い（設定は無害で、設計どおり）ので Must ではありません。運用の判断を誤らせる記述なので Should です。
  - 直す方向: 122 行目と yaml.in:25 を「buffer は送り手が詰まったときの取りこぼしの手当てで、初回値が丸ごと無い件には効かない見込み」に揃えます。原因の見立ては design.md の「未確定事項とリスク 1」と合わせて PM が決めます。
- **[missing tests] scram 側の出力のキー集合が縛られていない。**
  - 場所: `tests/test_stream.py:397-404`（既存）と、新しい check `:418-421`。
  - none 側は `set(o) == {...8 個}` で余計なキーを弾きます。scram 側は sasl / tls の等式と、新しい 3 つの値しか見ていません。
  - そのため、scram の区間（`# >>> kafka_auth scram` の中）にだけ別の項目（例: 誤って `required-acks` や `debug: true`）を足しても緑のままです。
  - 既存の穴で、今回の差分が広げたものではありません。ただ、030 で出力の項目が 3 つ増え、出力の形を縛る意味が増したので Should にしました（build.md の N5 も同じ指摘）。

## Nit

- **[runtime bugs] `timeout: 60s` で受信のループの止まりが長くなる。**
  - 場所: `app/gnmic/gnmic.yaml.in:86,107`。
  - `pkg/app/subscribe.go:361-398` の `export` は `wg.Wait()` で Write の完了を待ちます。そのため buffer（10000 応答）が満杯のときは、機器ごとの受信が 1 応答あたり最大 60s 止まります（既定は 5s）。
  - 満杯になるのは MSK が長く止まったときだけで、そのとき捨てるか待つかの差にすぎません。好みの範囲なので Nit です。
- **[correctness] design.md のメモリの見積もりの単位がずれている。**
  - design.md の「未確定事項とリスク 3」は「event 1 件 1 KB × 10000 = 10 MB」と見積もっています。実際の 1 枠は split 前の SubscribeResponse（`kafka_output.go:261` の `chan *outputs.ProtoMsg`）で、出力が 2 つあるので最大 20000 応答です。
  - gnmic のタスクは `memory = 512`（`IaC/terraform/aws-managed/pipeline/stream/gnmic.tf:70`）です。1 応答が数 KB でも 100 MB 程度なので、壊れはしません。yaml.in のコメントは正しい単位で書かれています。
- **[correctness] condition の評価がエラーのとき、gnmic は event を捨てる。**
  - 場所: `event_drop.go:141-145`。processor の `debug` が無いので、ログにも出ません。
  - 今の jq 式は object に対する `length` だけで、エラーになる入力は思い当たりません。将来 condition を書き換えるときの罠として、コメントに 1 行あってもよい程度です。

## 良かった点

- gnmic 0.49.0 に `--dry-run` が無いことを確かめたうえで、`gnmic processor` を実物の event の 4 つの形に当てて確かめていました。壊れた jq が init で弾かれることも確かめていて、design.md の「未確定事項 2」をきちんと潰しています。私も同じ入力と scram 側の描画で同じ結果を再現しました。
- 値は design.md どおりに保ちつつ、コメントと docs で「AWS で未確認の仮説」であることを明記していました。事実と仮説を分けて書いています（ただし上の Should のとおり、2 か所で見立てが食い違っています）。
- `docs/collection.md` の古くなる記述を拾い、build.md の「設計との差」に記録していました。

## ユーザーへの質問

- A（初回値が無い件）について、030 の buffer は「burst の後ろが捨てられる」場合にしか効かず、「1 件も無い」という観測とは合いません（上の Should の 1 つ目）。次の AWS 検証で初回値を見るとき、原因を見分けられるようにする手当てを、030 のうちに足すか決めてほしいです。
  - 案は次の 2 つです（build.md の S1 と同じ）。
    - 出力に `debug: true` か `enable-metrics` を付ける
    - gnmic を `--debug` で起こし、`gNMI Subscribe Response` が来ているかを見る
  - 足さないと、次の回も「buffer を足しても無い」としか分からず、design.md のリスク 1 にある次の候補（`updates-only: false` の明示、sample の併用）へ進む判断の材料が残りません。


### PM の確認

cold reviewer が実行した `test_stream`（110 / 0）、`gnmic processor` の再現、gnmic 0.49.0 のソースの読みは、PM は読んだだけ（ソースは読んでいない）。

**再現したもの**

- Should 2（scram のキー集合）: 直したあとの check が穴を塞ぐことを赤→緑で確かめた。scram の区間に `debug: true` を 2 か所足して `uv run --frozen python3 tests/test_stream.py` を打つと `AssertionError: gnmic.yaml: 出力は gnmi / metrics の 2 つ（…）。scram では SASL/SCRAM-SHA-512 と TLS（…）` で落ちる。行を消すと `通過 110 / 失敗 0`。

**直したもの**

- Should 1（見立ての食い違い）: `docs/pipeline.md:121-123`、`docs/troubleshooting.md:125`、`app/gnmic/gnmic.yaml.in` のコメント 3 か所、`docs/cycles/030-gnmic-buffer-event-drop/design.md`（調査で分かった事実、設計方針 A、未確定事項 1）を「buffer は送り手が詰まったときの取りこぼしの手当てで、初回値が丸ごと無い件には効かない見込み。原因は機器の初期同期か gnmic の受信の側」に揃えた。`docs/pipeline.md:72-73` の見立てはそのまま。
- Should 2（scram のキー集合）: `tests/test_stream.py` の scram 側の check に `set(o) == {…10 個}` を足した。
- Nit 2（メモリの見積もり）: design.md の未確定事項 3 を「1 枠は split 前の SubscribeResponse、出力 2 つで最大 20000、100 MB 程度で memory 512 に収まる」に直した。
- ユーザーへの質問（原因を見分ける手当て）: 設定には足さない（`debug: true` / `enable-metrics` は比較にならなくなる）。代わりに design.md の検証方法の AWS の項に「lab の EC2 から gnmic の CLI で同じ購読を機器に直接当てて初期同期を見る → 来なければ機器側（`updates-only: false` / sample）、来るなら gnmic の受信側（タスクを `--debug` で起こす）」の手順を書いた。AWS は 029〜033 をまとめて 1 回で打つ。

**直さなかったもの（最終報告へ）**

- Nit 1（`timeout: 60s` で受信のループが最大 60s 止まる）: 満杯は MSK が長く止まったときだけで、捨てるか待つかの差。設計どおり 60s のまま。
- Nit 3（condition のエラー時は捨てる）: 今の jq 式でエラーになる入力は無い。コメントは足さない。
- build.md の N2（timeout 60s、未再現）: 同上。

**未確認**

- AWS での効果（gnmi の初回値、metrics の空 event 0、流量）。029〜033 をまとめて 1 回で打つ。

テスト（PM、直したあと）: `uv run --frozen python3 tests/test_stream.py` → `通過 110 / 失敗 0`。
