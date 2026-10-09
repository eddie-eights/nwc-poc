# build: gnmic の初回値を落とさず、空の event を Kafka に書かない（030）

## Round 1

実装モデル: opus-5.5 / effort: high

### 変更

| ファイル | 何を |
|---|---|
| `app/gnmic/gnmic.yaml.in` | `processors.drop-empty`（`event-drop`、condition `.values == null or (.values \| length) == 0`）を `outputs:` の前に。`outputs.gnmi` / `outputs.metrics` の `split-events` の次（印の外）に `buffer-size: 10000` / `timeout: 60s` / `event-processors: [drop-empty]`。4 行目の「processor は使わない」を drop-empty の説明に書き換え、購読のコメントに初期同期の buffer を 1 行 |
| `tests/test_stream.py` | none 側の出力のキー集合に 3 つを足す。scram / none の両方で buffer-size・timeout・event-processors の値と processors の中身を見る check を 2 つ |
| `docs/pipeline.md` | 「gnmic の購読」に初期同期の buffer と空 event の drop、「gnmic の書き込みと ACL」に 030 で buffer を足したこと（AWS では未確認） |
| `docs/troubleshooting.md` | gnmi に初回値が無いときの行 |
| `docs/collection.md` | values の無い event を 030 から gnmic が捨てることを 1 行（セルフレビュー S2） |

commit: `gnmic の Kafka 出力に buffer と timeout を足し…`（ステップ 1）、`gnmic の buffer と drop-empty をテストで縛り…`（ステップ 2）、`セルフレビュー…`（ステップ 3）。

### 検証

#### 1. 描画（KAFKA_AUTH=none / scram）

```
…/scratchpad/030/none.yaml を作った（gnmi: 2 台 "127.0.0.1:57400", "127.0.0.2:57400" / brokers: 127.0.0.1:9092 / topics: gnmi, metrics / kafka auth: none）
…/scratchpad/030/scram.yaml を作った（gnmi: 2 台 "127.0.0.1:57400", "127.0.0.2:57400" / brokers: 127.0.0.1:9096 / topics: gnmi, metrics / kafka auth: SASL/SCRAM-SHA-512）
```

#### 2. gnmic 0.49.0 で設定を読む（dry-run の代わり）

gnmic 0.49.0 の `subscribe` に `--dry-run` は無い（`subscribe --help` に無い）。代わりに 2 つ打った。

(a) `gnmic processor`（event を入力ファイルから読んで processor を当てる）。入力は values あり 2 件・values のキー無し（2026-10-09 の実物の形）・`values: {}`・deletes だけ の 5 件。

```
$ docker run --rm -v …/030:/w:ro ghcr.io/openconfig/gnmic:0.49.0 --config /w/none.yaml processor --input /w/events.txt --name drop-empty
level=INFO msg="validating processor config" processor=drop-empty
level=INFO msg="added event processor to output" name=drop-empty type=event-drop
[ { "name": "interface_state", … "values": { "/srl_nokia-interfaces:interface/oper-state": "up" } },
  { "name": "system", … "values": { "/platform/control/memory/utilization": "0" } } ]
```

残ったのは values ありの 2 件だけ。壊れた jq（`… (.values | length) ==`）では弾かれる:

```
Error: failed initializing event processor 'drop-empty' of type='event-drop': unexpected EOF
```

(b) `gnmic --config none.yaml --debug subscribe` を機器も Kafka も無いまま起こしてログを見た（コンテナは見たあと `docker rm -f` 済み）。

```
level=INFO msg="validating processor config" processor=drop-empty
level=INFO msg="added event processor to output" output=kafka name=gnmi name=drop-empty type=event-drop
level=INFO msg="added event processor to output" output=kafka name=metrics name=drop-empty type=event-drop
level=INFO msg="initialized kafka producer" output=kafka name=gnmi worker=worker-0 config="{… \"BufferSize\":10000, … \"EventProcessors\":[\"drop-empty\"], …"
Timeout\":60000000000
```

processors の parse のエラーは無い。BufferSize 10000 / Timeout 60s（ns）として読まれた。

#### 3. テスト

赤（ステップ 1 のあと、テストを直す前）:

```
AssertionError: gnmic.sh render（KAFKA_AUTH=none。OSS 版と手元）: SCRAM の資格情報が無くても作れ、出力に sasl も tls も無い（ほかは scram と同じ）
```

緑（セルフレビューの直しのあと。最後の編集のあとに取り直した）:

```
$ uv run --frozen python3 tests/test_stream.py | tail -1
通過 110 / 失敗 0
```

全ファイル:

```
tests/test_agentcore.py: 通過 162 / 失敗 0
tests/test_alerts.py: 通過 168 / 失敗 0
tests/test_analytics.py: 通過 528 / 失敗 0
tests/test_collectors.py: 通過 79 / 失敗 0
tests/test_dashboard_config.py: 通過 3 / 失敗 0
tests/test_graph.py: 通過 78 / 失敗 0
tests/test_kb_index.py: 通過 7 / 失敗 0
tests/test_lab_debug.py: 通過 110 / 失敗 0
tests/test_local_compose.py: 通過 142 / 失敗 0
tests/test_nautobot.py: rc=1
  ModuleNotFoundError: No module named 'gradio'（app/dashboard/topology_view.py の import。手元の uv 環境に gradio が無い。030 の変更とは関係しない）
tests/test_oss.py: 通過 174 / 失敗 0
tests/test_oss_ops.py: 通過 203 / 失敗 0
tests/test_oss_roll.py: 通過 66 / 失敗 0
tests/test_stream.py: 通過 110 / 失敗 0
tests/test_sync.py: 通過 103 / 失敗 0
tests/test_workflow.py: 通過 333 / 失敗 0
```

#### 4. AWS（初回値が 4 台ぶん入る、metrics の空 event が 0、流量が減る）

未実行（PM が打つ。実装の範囲外）。

### 設計との差

- 設計の「`--dry-run` 相当」は、0.49.0 に `--dry-run` が無いので `gnmic processor` と、機器・Kafka 無しの `subscribe` の起動ログで代えた（上の 2）。
- `docs/collection.md` を 1 行直した（変更対象の表に無い。030 で「values の無い event がある」の記述が古くなるため。セルフレビュー S2）。
- buffer の説明を「producer が出来る前」から「送り手（sarama）が詰まっているあいだ」に直し、効くかは仮説（AWS で未確認）と書いた（セルフレビュー M1）。設定値は設計どおり。
- deletes だけの event（values が無い）も drop-empty で捨てる。設計の condition のとおりで、読み手（`gnmic_message` / `read_rows`）も捨てているので挙動は変わらない。yaml.in と docs にそう書いた。

### セルフレビュー

- 自分: opus-5.5 / effort high（xhigh に切り替えるツールが無いのでそのまま）
- 反対弁護人: opus（general-purpose、読み取り専用、文脈あり）。返ってきたあと作業ツリーの状態に増減なし

#### 反対弁護人の指摘と片付け

| # | 分類 | [観点] 場所 | 破綻シナリオ | 再現 / 確認 | 片付け |
|---|---|---|---|---|---|
| M1 | Must fix → 設計側 | [correctness] `app/gnmic/gnmic.yaml.in:25,82-84,102-104`、`docs/pipeline.md`、`docs/troubleshooting.md` | 「初期同期は producer が出来る前に来る」は誤り。gnmic 0.49.0 は `Metadata.Full=false` で producer は通信せずすぐ出来、購読は InitOutputs の後に始まる。1 回目の AWS で `gnmi` トピック自体が無かった（自動作成は有効）のは、event が出力に 1 件も届いていないことを示す。buffer では直らない見込みが高いのに、docs が仮説を事実として書き、運用者を誤誘導する | 上の 2(b) の自分のログで、機器も Kafka も無いまま `initialized kafka producer` が出ている（producer は接続を待たない）ことを再現。`docs/verification/20261009-aws-managed-2.md:40` で 2 回目も初回値が無く、変化だけ書かれたことを確認 | 文言は直した（buffer は「送り手が詰まっているあいだ」に効く、初回値に効くかは仮説で AWS 未確認、直らなければ上流を疑う）。**原因の見立ては設計側なので PM に報告**（設定値は設計どおり残す） |
| S1 | Should fix → 設計側 | [検証] design.md の検証方法の AWS の項 | Write の expire と sarama の送信失敗のログは output の `debug` のときだけ。次の AWS でも原因が見分けられない | 反対弁護人が gnmic v0.49.0 の kafka_output.go:561-563, 636-638 を引用。自分では読んでいない | PM に報告（検証の回に output の `debug: true` か `enable-metrics`、`--debug` の `gNMI Subscribe Response` を見る案） |
| S2 | Should fix | [docs] `docs/collection.md:227` | 030 から values の無い event は Kafka に出ないのに「ある」と書いたまま | 読んで確認 | 直した（1 行足す） |
| S3 | Should fix | [docs] `app/gnmic/gnmic.yaml.in:67-68` | 「read_rows には fields の無い行になるだけ」は 025 前の話、「read_rows の gnmic_message」も誤り（read_rows は gnmic_struct） | `app/spark/snmp_sinks.py:381-397` を読んで確認 | 直した |
| N1 | Nit | [副作用] `gnmic.yaml.in` の timeout | `timeout` は sarama の `Producer.Timeout` にも入る。required-acks が wait-for-local の間は使われない | 反対弁護人の引用のみ | コメントに 1 行足した |
| N2 | Nit | [runtime] timeout 60s | msgChan が満杯のとき、機器ごとの応答の処理が 1 件あたり最大 60s 止まる（5s の 12 倍）。goroutine は溜まらない | 反対弁護人の引用のみ（未再現） | 最終報告に回す |
| N3 | Nit | [見積もり] design.md の buffer のメモリ | 1 枠は split 前の SubscribeResponse 1 件。出力 2 つで最大 2 × 10000 | 反対弁護人の引用のみ | コメントに単位を書いた。設計の見積もりは PM に報告 |
| N4 | Nit | [docs] 「metrics の 9 割」 | 400 件中 359 件の時点の比率 | verification-2 の記述で確認 | 直した。deletes を捨てても読み手の挙動は変わらない（gnmic_struct / gnmic_message が捨てている）ことも確認済み |
| N5 | Nit | [テスト] `tests/test_stream.py` | jq の condition が実際に落とすかは自動テストに無い（docker が要る）。scram 側のキー集合は縛っていない（既存の穴） | — | 最終報告に回す |

#### 「問題なし」とした観点と根拠

- 設計整合性: 設計の A / B / C の各項目を design.md と diff で突き合わせた（読んだだけ）。
- テストが退行を縛るか: 退行 5 種を注入して全部落ちることを実行して確かめた（`scratchpad/030/mutate.py`。buffer-size 消し / timeout 5s / event-processors 消し / condition 変更 / buffer を scram 区間へ移す → 5 つとも rc=1）。注入のあと作業ツリーは元どおり。
- jq 式: 実物の形 4 種で `gnmic processor` を実行（上の 2(a)。直しのあとの scram の描画でも同じ 2 件が残る）。壊れた式が init で弾かれることも実行した。
- 資格情報: 足したのはコメントと設定値だけ。render の既存の check（値を書かない）は緑。
- test_nautobot.py の rc=1: `uv run --frozen python3 -c "import gradio"` が `ModuleNotFoundError`。030 の差分は `app/dashboard` と `tests/test_nautobot.py` に触れていない（origin/main との diff --stat が空）。

未解消の Must fix: 実装の範囲では 0（M1 の文言は直した。原因の見立ては設計側として PM に報告）。
