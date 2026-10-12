# 1 本の回線断で修復案を 1 件にまとめる（053）の実装

## Round 1

実装モデル: opus-5.5 / effort: high

### 実装

- `rules.link_anomaly(alert, links)` を足した。link_down の (device_id, target) が回線の (a, a_if) か (b, b_if) に一致すれば、a の端の id・機器・IF に読み替え、detail の頭に `回線 <a> <a_if> ↔ <b> <b_if>（<元の機器> <元の IF> 側の通知）。` を付けて 1000 文字に切った写しを返す
- `worker.handle_message`: START_KINDS のアラートがあればトポロジを 1 回読み、各アラートを `link_anomaly` で読み替えてから `resolve_for` / `start_for` へ。読めなければ例外を投げる（メッセージは消えずに配り直し）
- `worker.start_for(client, alert, topology=None)`: 渡されなければいまどおり自分で読む
- テスト T-1〜T-8 と start_for の 2 件、端ごとに別の 1 通で順に届く T-5 を `tests/test_workflow.py` に足した
- docs: 設計の「変更対象ファイル」の行と、セルフレビューで見つけた Neptune が止まったときの説明（下の S-2）

変更ファイル:

- `app/temporal/rules.py`
- `app/temporal/worker.py`
- `app/temporal/awsio.py`（docstring だけ。S-2）
- `tests/test_workflow.py`
- `docs/troubleshooting.md`
- `docs/workflow.md`
- `docs/pipeline.md`
- `docs/development.md`
- `docs/alert-comparison.md`
- `docs/data-stores.md`（S-2）
- `docs/architecture/resources/sns-sqs-lambda.md`
- `docs/architecture/resources/temporal.md`
- `docs/architecture/resources/splunk.md`
- `docs/architecture/resources/neptune-analytics.md`（S-2）

設計からの逸脱:

- `link_anomaly` は b の端を照合するとき、b と b_if が空でないことも見る（target が空のアラートが b_if の空の行に当たらないように）。T-3 の 2 件目で縛った
- `docs/troubleshooting.md` の「既知の不具合」の表はヘッダーだけになった。消した行は表の下の箇条書きに「053 でまとめた（手元のテストで確認、AWS では未確認）」として残した（`nwc_trap` の「4 件のうち 2 件」の前提が消えないように）
- 設計の `app/graph/graph.py`（design.md:24）は実在しない。辺を a < b に揃えるのは `app/agentcore/graph.py` の `_link_row`（:366）。docstring は実在のパスで書いた。design.md は直していない
- 設計の範囲の外の docs を 4 か所直した（`awsio.py` の docstring 2 か所、`neptune-analytics.md` の 2 か所、`data-stores.md` の 1 か所）。053 で starter が link_down の resolved でも Neptune を読むようになったのに、Neptune を読むのは「事前チェックと保守中の判定」だけ、と書いてあったため

### 赤 → 緑（T-5）

worker を変える前（rules.py と tests だけ変えた状態）の `uv run --group dev --group web python tests/test_workflow.py`:

```
ok 053 T-1 link_anomaly: b の端（spine）の link_down は回線の a の端の id・機器・IF に読み替え、…
ok 053 T-2 …
ok 053 T-3 …
ok 053 T-3 …
ok 053 T-4 …
AssertionError: 053 T-5 handle_message: 回線の leaf 側と spine 側の firing（1 通に 2 件）は同じ id のワークフロー 1 本だけ（2 回目は AlreadyStarted）。トポロジを読むのは 1 通につき 1 回（いま: 起こした id ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1', 'investigate-dc1-spine-01#link_down#ethernet-1/3']、read_topology 2 回）
```

worker を変えたあと: rc=0、`通過 471 / 失敗 0`（セルフレビューでテストを 1 件足したあとの最終は下）。

### 検証方法の各項目（最後の編集のあとに取り直したもの）

`uv run --group dev --group web python tests/test_workflow.py`（`python3` 単体では yaml が無く import で落ちるので uv で走らせた）: rc=0

```
ok 053 T-1 link_anomaly: b の端（spine）の link_down は回線の a の端の id・機器・IF に読み替え、detail の頭に両端とどちらの端の通知かを付ける。status / first_seen / source は元のまま、元の dict は変えない（いま: {'anomaly_id': 'dc1-a-leaf-01#link_down#ethernet-1/1', 'device_id': 'dc1-a-leaf-01', 'kind': 'link_down', 'target': 'ethernet-1/1', 'detail': '回線 dc1-a-leaf-01 ethernet-1/1 ↔ dc1-spine-01 ethernet-1/3（dc1-spine-01 ethernet-1/3 側の通知）。ethernet-1/3 is down', 'status': 'firing', 'first_seen': 100, 'source': 'grafana'}）
ok 053 T-2 link_anomaly: a の端（leaf）の link_down は id・機器・IF はそのまま、detail の頭にだけ両端と（leaf 側の通知）を付ける（いま: {'anomaly_id': 'dc1-a-leaf-01#link_down#ethernet-1/1', 'device_id': 'dc1-a-leaf-01', 'kind': 'link_down', 'target': 'ethernet-1/1', 'detail': '回線 dc1-a-leaf-01 ethernet-1/1 ↔ dc1-spine-01 ethernet-1/3（dc1-a-leaf-01 ethernet-1/1 側の通知）。ethernet-1/1 is down', 'status': 'firing', 'first_seen': 100, 'source': 'grafana'}）
ok 053 T-3 link_anomaly: link_down 以外・トポロジに無い target（trap の ifIndex のまま）・回線が無いときは元と同じ値を返す
ok 053 T-3 link_anomaly: a か a_if が空・None の回線の行は無視し、b か b_if が空の行では b の端を照合しない（target が空のアラートを空の b_if に合わせない）
ok 053 T-4 link_anomaly: 元の detail が 1000 文字でも戻りは 1000 文字で、頭の「回線 …」が残る（いま: 1000 文字）
ok 053 T-5 handle_message: 回線の leaf 側と spine 側の firing（1 通に 2 件）は同じ id のワークフロー 1 本だけ（2 回目は AlreadyStarted）。トポロジを読むのは 1 通につき 1 回（いま: 起こした id ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1']、read_topology 1 回）
ok 053 T-5 handle_message: ワークフローに渡すアラートは a の端に読み替えたもの（detail の頭に両端とどちらの端の通知か）
ok 053 T-5 handle_message: spine 側の firing だけでも a の端の id で起こす（いま: ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1']）
ok 053 T-5 handle_message: Grafana の group_by どおり端ごとに別の 1 通で順に届いても、ワークフローは a の端の id の 1 本だけ（いま: ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1']）
ok 053 T-6 handle_message: spine 側の resolved だけの 1 通でも、a の端の id のワークフローへシグナル resolved を送る（いま: [('investigate-dc1-a-leaf-01#link_down#ethernet-1/1', 'resolved', 'splunk')]）
ok 053 T-7 handle_message: spine が保守中なら、spine 側・leaf 側どちらの firing でもワークフローを起こさない（読み替えたあとの a の端からも相手の spine を数える）（いま: [] / []）
ok 053 T-8 handle_message: トポロジを読めなければ firing でも resolved でも例外を呼び手に投げ、起こさずシグナルも送らない。starter_queue はメッセージを消さない（いま: [('Neptune に届かない', [], []), ('Neptune に届かない', [], [])] / []）
ok 053 T-8 handle_message: link_down の無いメッセージ（bgp_down と trap だけ）ではトポロジを読まない（いま: read_topology 0 回）
ok 053 start_for: topology を渡さずに単体で呼べば、いまどおり自分でトポロジを読んで起こす
ok 053 start_for: topology を渡せばトポロジを読まない
通過 472 / 失敗 0
```

- 既存のテスト: 上の `通過 472 / 失敗 0` に含まれる。既存のテストの期待値は変えていない（`read_topology` を差し替えていない既存の handle_message のテストも、そのまま通った）
- `bash ops/check.sh`: 下の「ops/check.sh」

### セルフレビュー

- 自分: opus-5.5 / effort high（サブエージェントの中では effort を切り替えられない）
- 反対弁護人: opus / effort xhigh（文脈を渡し、読み取り専用。返ってきたあとの `git status --porcelain -uall` は変更 11 ファイルのまま、増えたファイル無し）
- 反対弁護人の結論: Must fix 0。Should fix 2（S-1 は自分の指摘と同じ）、Nit 7

| # | 分類 | 観点 | 場所 | 破綻シナリオ | 確かめたこと | 片付け |
|---|---|---|---|---|---|---|
| S-1 | Should fix | correctness（設計側） | `app/temporal/rules.py:277-293`、`app/temporal/worker.py:353` | 両端の starts_at がずれた（leaf 100、spine 160）回線断で、修復案 `leaf#100` を却下か expired にし、HOLD_MINUTES（1440 分）が明けても回線が落ちたまま。spine 側の繰り返しの通知（starts_at 160）は `leaf#160` を引き、無いので 2 本目のワークフローと修復案を起こす。053 の前は `spine#160` があって飛ばされていた | `repro/hold.py`（テストの 053 の節を読み込み、`latest_proposal` が `leaf#100` だけ rejected を返す状態で両端の繰り返しを渡す）の出力: `leaf repeat 100 -> []` / `spine repeat 160 -> ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1']` | 原因は設計（修復案の id が通知した端の first_seen のまま）。設計の「未確定事項とリスク」の 1 つ目は「解消の通知のあと」の窓で、HOLD 明けの窓は書いていない。実装で吸収せず PM に報告 |
| S-2 | Should fix | runtime / 運用（docs） | `app/temporal/worker.py:393-398` | Neptune に届かないあいだ、link_down の resolved と、修復案がある繰り返しの通知も配り直しになり、5 回で DLQ に行く。053 の前は resolved は Neptune を読まず、繰り返しの通知は修復案の確認で先に抜けていた（`git show 4f29031:app/temporal/worker.py` の start_for と handle_message で確認）。resolved が DLQ に落ちると、ワークフローは expired と HOLD まで残る | 振る舞いは設計どおり（設計方針「resolved だけのメッセージでもトポロジを読む」、T-8）。T-8 の 1 件目が resolved でも投げることを縛っている | コードは設計どおりなので変えない。古くなった説明 5 か所（`awsio.py:6` と `:105` の docstring、`neptune-analytics.md:42` と `:79-80`、`data-stores.md:235`）を直した。読めないときに resolved だけ読み替えずに送るかは設計の判断として PM に報告 |
| N-1 | Nit | correctness | `app/temporal/rules.py:285-292` | 同じ（機器, IF）を含む回線が 2 本あると、最初に当たった行で読み替える。`read_topology` の MATCH に ORDER BY が無いので、firing と resolved が別の id になりうる | 読んだだけ。`sync_physical` は古い辺を消すので、普段のデータでは 2 本にならない（反対弁護人の根拠: `app/agentcore/graph.py:544-548`） | 直さない |
| N-2 | Nit | docs | `docs/troubleshooting.md:499-506` | 表から行を消すと、`nwc_trap` の「4 件のうち 2 件」が何の 4 件か分からない | 読んで確認 | 直した（表の下に 053 でまとめた箇条書きを足した） |
| N-3 | Nit | docs | `app/temporal/rules.py:281`、`design.md:24` | 存在しない `app/graph/graph.py` を指す | `ls app/graph/graph.py` → No such file。`app/agentcore/graph.py:366` に `_link_row` | rules.py は直した。design.md は PM に報告 |
| N-4 | Nit | 保守性 | `app/temporal/worker.py:345-348` | `start_for` を単独で呼ぶと `link_anomaly` を通らない。いまは handle_message 以外の呼び手が無い | `grep -n "start_for(" app/` で呼び手は handle_message だけ | 直さない |
| N-5 | Nit / missing tests | テスト | `tests/test_workflow.py` | T-5 が両端を 1 通に入れている（Grafana は group_by で端ごとに別の通知）。`_la_spine_alert` の中身が leaf | 読んで確認 | 端ごとに別の 1 通で順に届く T-5 を足した。変数名を `_la_leaf_alert` に直した。S-1 / N-1 を固定するテストは足していない |
| N-6 | Nit | 記録 | `docs/cycles/053-link-anomaly-merge/` | build.md が無い | 反対弁護人の時点では書く前だった | このファイル |
| N-7 | Nit | correctness（設計で受け入れた） | `app/temporal/worker.py:399-404` | 片方の端の resolved だけでまとめたワークフローが閉じる（Splunk の片端の linkUp、Grafana の系列が消える） | 設計の「未確定事項とリスク」の 2 つ目で受け入れている | 直さない |

退行を 1 つずつ注入して `tests/test_workflow.py` が落ちることを確かめた（`repro/mutate.py`。各注入のあと元に戻し、`git diff --stat` が注入の前と同じことを確かめた）:

```
M1 start_for に topology を渡さない: rc=1 AssertionError: 053 T-5 handle_message: 回線の leaf 側と spine 側の firing（1 通に 2 件）は同じ id のワークフロー 1 本だけ（…
M2 resolved を読み替えない: rc=1 AssertionError: 053 T-6 handle_message: spine 側の resolved だけの 1 通でも、a の端の id のワークフローへシグナル resolved を送る（いま: [('investigate-dc1-spine-01#link_down#ethernet-1/3', …
M3 START_KINDS が無くてもトポロジを読む: rc=1 ?
M4 a の端では detail を付けない: rc=1 AssertionError: 053 T-2 link_anomaly: a の端（leaf）の link_down は id・機器・IF はそのまま、detail の頭にだけ両端と（leaf 側の通知）を付ける（…
M5 detail を切らない: rc=1 AssertionError: 053 T-4 link_anomaly: 元の detail が 1000 文字でも戻りは 1000 文字で、頭の「回線 …」が残る（いま: 1090 文字）
M6 b/b_if の空を見ない: rc=1 AssertionError: 053 T-3 link_anomaly: a か a_if が空・None の回線の行は無視し、b か b_if が空の行では b の端を照合しない（…
M7 頭を後ろに付ける: rc=1 AssertionError: 053 T-1 link_anomaly: b の端（spine）の link_down は回線の a の端の id・機器・IF に読み替え、…
```

M3 の `?` は AssertionError ではなく例外で落ちたため。M3 だけ注入し直した（`repro/m3.py`）出力の末尾:

```
rc 1
  File "…/tests/test_workflow.py", line 2156, in _topo_boom
    raise RuntimeError("Neptune に届かない")
RuntimeError: Neptune に届かない
```

link_down の無いメッセージでトポロジを読み、差し替えてある失敗する `read_topology` に当たって落ちた（T-8 の 2 件目の呼び出し）。

問題なしとした観点と根拠:

- 設計との整合: 実装ステップ 1〜6 と検証方法 T-1〜T-8 を design.md と突き合わせた。各項目は上の出力で ok
- 引数を変えない: T-1 が元の dict と比べて縛る（M7 などの注入で T-1 が落ちることも確認）
- 保守中の判定: 読み替えたあとの a の端からも相手を数える（T-7、両方向）
- Temporal の二重起動の弾き方: 偽の client が同じ id の 2 回目で WorkflowAlreadyStartedError を投げ、start_for が False を返して handle_message が投げないこと（T-5 の 3 件）
- security: rules.py は boto3 / temporalio を import しないまま（`grep -n "^import\|^from" app/temporal/rules.py` で増えていない）。外に送る値は増えていない（detail の頭は機器名と IF 名だけ）

### ops/check.sh

最後の編集（セルフレビューの直し）のあとに `bash ops/check.sh` を走らせた。終了コード 0。出力から段の見出しと各テストの最後の行を抜いた:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
== 3. スクリプトの構文
== 4. 模擬テスト
通過 168 / 失敗 0
通過 168 / 失敗 0
通過 549 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 121 / 失敗 0
通過 145 / 失敗 0
68 項目すべて通過
通過 177 / 失敗 0
通過 210 / 失敗 0
通過 66 / 失敗 0
通過 119 / 失敗 0
通過 104 / 失敗 0
通過 472 / 失敗 0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
すべて通過
```

### PM に回すもの

- S-1（設計側の Should fix）: HOLD が明けたあと、もう一方の端の繰り返しの通知で 2 本目が起きる。直し方の案は、link_down に限り既にある修復案を first_seen を除いた anomaly_id で引くこと
- S-2: Neptune を読めないとき、resolved だけ読み替えずに送るか（いまは設計どおり投げて配り直し、5 回で DLQ）
- 設計の範囲外で古いもの（053 の前から）: `docs/workflow.md:65`、`docs/architecture/resources/temporal.md:96`、`docs/nautobot.md:230` は「Neptune を読めないときは起こす」と書いているが、コードは 053 の前も後も例外を投げて配り直させる。ほかの docs（`web-ec2.md:92`、`agentcore-bedrock.md:123`、`firehose.md:76`、`s3-tables-athena.md:94`）が「既知の不具合」を指しているのに、その件は表に無い
- design.md:24 の `app/graph/graph.py` は `app/agentcore/graph.py`

