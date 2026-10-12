# 1 本の回線断で修復案を 1 件にまとめる（053）

設計: PM(opus-5.5) / effort: high。QUEUE 167。2026-10-12。

## 背景

- 2026-10-05 の AWS の動作確認で、1 本の回線断で修復案が 4 件できた。2 件の原因（Splunk の trap の検索がサブインターフェース `ethernet-1/1.0` を除いていない）は直した。残る 2 件の原因は、回線の leaf 側と spine 側が別の異常として数えられること（`docs/troubleshooting.md:505`、`docs/alert-comparison.md:326`）
- 2026-10-12 にユーザーがこの件を QUEUE に積んで進めることを了承した（「167 と Splunk のツール追加、両方 QUEUE に積んで進めて」）
- AWS は使わない。手元のテスト（`tests/test_workflow.py`）で確かめる

## 現物（PM が 2026-10-12 に読んで確かめたもの）

- 異常の id は `rules.anomaly_id`（`app/temporal/rules.py:218-220`）の `f"{device_id}#{kind}#{target}"`。送り手（Grafana / Splunk）が違っても、機器・種類・対象が同じなら同じ id になる。回線の両端は機器と IF が違うので、別の id になる
- `rules.alerts_from_message`（`rules.py:252-275`）が返す 1 件は `{anomaly_id, device_id, kind, target, detail[:1000], status, first_seen, source}`。`device_id` は `device_name` で小文字の短い名前に揃える
- starter の流れ（`app/temporal/worker.py`）
  - `handle_message`（`:381-395`）は `rules.START_KINDS`（`{"link_down"}`。`rules.py:208`）だけを扱う。`resolved` は `resolve_for`、それ以外は `start_for`
  - `start_for`（`:344-365`）は `should_start` → `awsio.latest_proposal(rules.proposal_id(aid, first_seen))` → `should_start` → `rules.maintenance_hold(alert, *awsio.read_topology())` → `client.start_workflow(..., id=rules.workflow_id(aid))`。`WorkflowAlreadyStartedError` は False
  - `resolve_for`（`:368-378`）は `investigate-<anomaly_id>` にシグナル `resolved` を送る。NOT_FOUND は False
  - `handle_decision`（`:398-` ）は `workflow_id(anomaly_of(proposal_id))` に決定を送る（proposal_id の頭の anomaly_id を使う）
- `rules.workflow_id`（`rules.py:296-299`）は `investigate-<anomaly_id>` で、発生の時刻を入れない（送り手ごとに starts_at が違うため）。`rules.proposal_id`（`:303-305`）は `<anomaly_id>#<first_seen>`
- ワークフロー（`worker.py:258-340`）が閉じるのは、解消の通知（シグナル `resolved`）が来たとき（verified / obsolete）か、rejected / expired / failed のあと解消を `HOLD_MINUTES`（既定 1440）分まで待ちきったとき。解消が来るまで id を握る
- `rules.maintenance_hold`（`rules.py:284-294`）は、`links` の `(a, a_if)` か `(b, b_if)` がアラートの `(device_id, target)` と一致する回線の相手を「関わる機器」に足す。回線の両端の照合はここにもう 1 つある
- `awsio.read_topology`（`app/temporal/awsio.py:104-112`）は機器 `{device_id, status, maintenance, role}` と回線 `{a, b, a_if, b_if, status}` を返す（Neptune の `MATCH (a)-[l:link]->(b)`）
- 回線の辺は `a < b`（機器名の順）で 1 本だけ置く（`app/agentcore/graph.py:16` と `_link_row`（`:366-370`））。lab の回線 `dc1-a-leaf-01 ethernet-1/1 ↔ dc1-spine-01 ethernet-1/3` は `a = dc1-a-leaf-01`
- `rules.build_prompt`（`rules.py:32-45`）の action の決め方は「Leaf dc1-a-leaf-01 の ethernet-1/1（dc1-spine-01 との fabric）が落ちている（link_down か、その上の isis_down）なら heal-main」。`a` の側にまとめると、この回線の異常はいつも `dc1-a-leaf-01#link_down#ethernet-1/1` になる
- 送り手の `target`: Grafana と Splunk の gNMI は IF 名（`ethernet-1/1`）。Splunk の trap は varbind の `ifName > ifDescr > ifIndex`（`app/splunk/nwc_alerts/default/savedsearches.conf:88` / `:120`）なので、ifName が無い機器では IF 名にならないことがある
- `rules.py:205` のコメント「同じ形を app/graph/status_handler.py も読む。形を変えるときは 4 か所を一緒に変える」は通知の本文（SNS のメッセージ）の形の話。このサイクルは本文の形を変えない（starter の中でだけ読み替える）
- テスト: `tests/test_workflow.py:195-202` は `awsio.read_topology` を差し替えて `worker.handle_message` を呼ぶ形がある。`:430` 付近で starter の関数の有無を見ている

## 設計方針

- **starter が回線の異常を、回線の `a` の端（`a`, `a_if`）にまとめる。** `link_down` のアラート（firing も resolved も）について、`(device_id, target)` が回線の `(a, a_if)` か `(b, b_if)` に一致したら、`device_id = a`、`target = a_if`、`anomaly_id` を作り直した写しで以後を処理する
  - 両端の firing は同じ `anomaly_id` → 同じ `workflow_id` になる。先に届いた方がワークフローを起こし、後の方は `WorkflowAlreadyStartedError` で起こさない（いまの Grafana と Splunk の重なりと同じ仕組み）
  - どちらの端の resolved も同じワークフローに届く
  - 修復案の id の形（`<anomaly_id>#<first_seen>`）と異常の id の形（`<device_id>#<kind>#<target>`）は変えない。テーブル・Web・エージェントのツールの読み方は変わらない
- **どちらの端から届いたかは detail に残す。** 写しの `detail` の頭に `回線 <a> <a_if> ↔ <b> <b_if>（<元の device_id> <元の target> 側の通知）。` を付け、全体を 1000 文字に切る（頭に付けるので切っても消えない）。`build_prompt` は detail をそのまま渡すので、エージェントは両端を知って調べられる
- **一致する回線が無ければそのまま。** `link_down` 以外、トポロジに無い IF（trap の ifIndex のまま等）、トポロジを読めないとき（Neptune に届かない）は元のアラートのまま扱う（いまの振る舞い）。読めないときにメッセージを配り直させるかは、いまの `start_for` の `read_topology` の失敗と同じ扱い（例外は呼び手へ投げ、SQS が配り直す）にする。resolved だけのメッセージでもトポロジを読む（読まないと、`b` の端の resolved が `a` の id のワークフローに届かない）
- **トポロジは 1 通のメッセージにつき 1 回だけ読む。** `handle_message` で、`START_KINDS` のアラートが 1 件でもあれば `read_topology` を 1 回読み、まとめ直しと `maintenance_hold` の両方に使う（`start_for` に devices / links を渡す。`start_for` 単体で呼ばれたとき用に、渡されなければいまどおり自分で読む）
- **純粋な関数は rules.py に置く。** `rules.link_anomaly(alert, links) -> dict`（新しい dict を返し、引数を変えない）。rules.py は boto3 / temporalio を import しない決まり（いまのまま）
- 却下した案（`design-log.md` には残さない。技術的に一意に決まるので PM が決めた）
  - 異常の id を回線の id（`<a>#<a_if>#<b>#<b_if>`）にする: id の形が変わり、Web・エージェント・docs の読み方が全部変わる。`build_prompt` の heal-main の条件も書き直しになる
  - 送り手（Grafana / Splunk）の側で片方の端だけを出す: 送り手はトポロジを知らない。2 つの送り手に同じ規則を 2 回書くことになる

## 変更対象ファイル

- `app/temporal/rules.py` — `link_anomaly(alert, links)` を足す。`anomaly_id` の docstring に「回線の両端は starter が `a` の端にまとめる」を 1 行足す
- `app/temporal/worker.py` — `handle_message` でトポロジを 1 回読み、`link_anomaly` で読み替えてから `resolve_for` / `start_for` へ。`start_for` は topology を受け取れるようにする。モジュールの docstring（`:9` 付近の starter の説明）に 1 行足す
- `tests/test_workflow.py` — 下の「検証方法」のテスト
- docs（いまの振る舞いの説明を直す。新しい文書は作らない）
  - `docs/troubleshooting.md:505` の行を消す（「直したら、その行を消す」の決まり。`nwc_trap` の段落は残す）
  - `docs/workflow.md:57` 付近（異常の id の説明）、`docs/pipeline.md:497`、`docs/architecture/resources/sns-sqs-lambda.md:34`、`docs/development.md:98` に「回線の両端の `link_down` は回線の `a` の端（機器名の順で前）の id にまとめる（053）」を足す
  - `docs/architecture/resources/temporal.md:125` の「1 本の回線断で修復案が 4 件できる」の行を、053 で 1 件にまとめた（手元のテストで確認、AWS では未確認）に直す
  - `docs/architecture/resources/splunk.md:166` の「回線の両端が別の異常になる件は …」を、053 でまとめたに直す
  - `docs/alert-comparison.md:326` は 2026-10-05 の記録なので本文は変えず、末尾に「両端は 053 でまとめた」を足す

## 再利用するもの

- `rules.anomaly_id`、`rules.workflow_id`、`rules.proposal_id`（形は変えない）
- `rules.maintenance_hold` の回線の照合の書き方（`(l.get("a"), l.get("a_if")) == (dev, target)`）
- `tests/test_workflow.py:195-202` の `awsio.read_topology` を差し替える形と、`worker.handle_message` を呼ぶ既存のテストの偽の Temporal client

## 実装ステップ

1. `rules.link_anomaly(alert, links)` を書く
   - `alert["kind"] != "link_down"` なら `alert` をそのまま返す
   - `links` を順に見て、`(a, a_if)` か `(b, b_if)` が `(alert["device_id"], alert["target"])` に一致する最初の回線を取る。無ければそのまま返す
   - 一致したのが `a` の端なら、id は同じのまま detail だけ付ける（`a` の端から届いた通知も、両端が分かるようにする）
   - 写しを返す: `device_id = a`、`target = a_if`、`anomaly_id = anomaly_id(a, kind, a_if)`、`detail` は上の形で頭に付けて `[:1000]`。ほかのキー（status / first_seen / source）はそのまま
   - 回線の値が空（`a` か `a_if` が空・None）の行は無視する
2. `worker.handle_message` を変える
   - `START_KINDS` のアラートが 1 件も無ければ、いまどおり何も読まずに終わる
   - あれば `devices, links = await asyncio.to_thread(awsio.read_topology)` を 1 回
   - 各アラートを `rules.link_anomaly(a, links)` で読み替え、resolved は `resolve_for`、firing は `start_for(client, a, (devices, links))`
3. `worker.start_for(client, alert, topology=None)` にする。`topology` が None なら、いまどおり自分で `read_topology` を読む
4. テストを足す（下の「検証方法」の T-1〜T-6）
5. docs を直す（「変更対象ファイル」の docs の行）
6. `bash ops/check.sh` を通す

## 検証方法

テストは `tests/test_workflow.py` に足す。`python3 tests/test_workflow.py` の最後の行が「失敗 0」、`bash ops/check.sh` の最後の行が「すべて通過」で終了コード 0 になること。

- T-1（純粋な関数。`b` の端）
  - 入力: `link_anomaly({"anomaly_id": "dc1-spine-01#link_down#ethernet-1/3", "device_id": "dc1-spine-01", "kind": "link_down", "target": "ethernet-1/3", "detail": "ethernet-1/3 is down", "status": "firing", "first_seen": 100, "source": "grafana"}, [{"a": "dc1-a-leaf-01", "a_if": "ethernet-1/1", "b": "dc1-spine-01", "b_if": "ethernet-1/3", "status": "DOWN"}])`
  - 期待: `anomaly_id == "dc1-a-leaf-01#link_down#ethernet-1/1"`、`device_id == "dc1-a-leaf-01"`、`target == "ethernet-1/1"`、`detail` が `回線 dc1-a-leaf-01 ethernet-1/1 ↔ dc1-spine-01 ethernet-1/3（dc1-spine-01 ethernet-1/3 側の通知）。ethernet-1/3 is down` で始まる、`status` / `first_seen` / `source` は元のまま、元の dict は変わっていない
- T-2（`a` の端）: 同じ回線で leaf 側のアラートを渡すと、`anomaly_id` は `dc1-a-leaf-01#link_down#ethernet-1/1` のまま、detail の頭に「（dc1-a-leaf-01 ethernet-1/1 側の通知）」が付く
- T-3（そのまま返すもの）: `kind == "bgp_down"`、トポロジに無い target（`"5"` のような ifIndex）、`links == []` の 3 通りで、戻りが元と同じ値（`==`）
- T-4（detail の長さ）: 元の detail が 1000 文字のとき、戻りの detail は 1000 文字で、頭の「回線 …」が残る
- T-5（starter。両端の firing で 1 本）: `awsio.read_topology` を上の 1 回線に差し替え、`awsio.latest_proposal` は None、偽の client は `start_workflow` の id を記録し、同じ id の 2 回目で `WorkflowAlreadyStartedError` を投げる。leaf 側と spine 側の firing を 1 通（alerts 2 件、starts_at は 100 と 160）で `handle_message` に渡す
  - 期待: `start_workflow` が呼ばれた id は `["investigate-dc1-a-leaf-01#link_down#ethernet-1/1"]` の 1 つだけ（2 回目は AlreadyStarted）。`read_topology` が呼ばれたのは 1 回
  - いまのコードでは id が 2 つ（`investigate-dc1-a-leaf-01#…` と `investigate-dc1-spine-01#link_down#ethernet-1/3`）になることを、実装の前に同じテストで確かめて build.md に書く（赤 → 緑）
- T-6（starter。どちらの端の resolved も同じワークフローへ）: spine 側の resolved だけの 1 通で `handle_message` を呼ぶと、偽の client の `get_workflow_handle` に渡る id が `investigate-dc1-a-leaf-01#link_down#ethernet-1/1`
- T-7（保守中の判定がいまどおり）: spine を `maintenance: True` にしたトポロジで spine 側の firing を渡すと、ワークフローを起こさない（読み替えたあとの `a` の端からも、相手の spine を関わる機器に数える）
- T-8（トポロジを読めない）: `read_topology` が例外を投げるとき、`handle_message` は例外を呼び手に投げる（メッセージを消さない。いまの `start_for` と同じ）。`START_KINDS` のアラートが無いメッセージ（`bgp_down` だけ）では `read_topology` を呼ばない
- 既存のテストがすべて通ること（`read_topology` を差し替えていない既存のテストで、`handle_message` が新しく `read_topology` を読んで落ちないか。落ちるなら既存のテストの差し替えを足す。テストの期待値は変えない）

## 未確定事項とリスク

- **閉じたあとに遅れて届いたもう片方の firing。** ワークフローが閉じる（解消の通知が来た）より後に、もう片方の端の firing が遅れて届くと、その端の `first_seen` の修復案は無いので新しいワークフローを起こす。いまの Grafana と Splunk の重なりにも同じ窓がある。物理の回線は両端が同時に戻り、解消の通知のあとに firing は来ないはずなので、このサイクルでは直さない（AWS で見えたら QUEUE に積む）
- **HOLD が明けたあとの、もう片方の端の繰り返しの通知。** 修復案を却下か expired にして `HOLD_MINUTES`（1440 分）まで待ちきり、ワークフローが閉じても回線が落ちたままなら、もう片方の端（starts_at が違う）の Grafana の繰り返しの通知は、まとめた id とその端の `first_seen` で修復案を引き、無いので 2 本目を起こす（1 回だけ。2 本目の修復案ができると以後は飛ばす）。HOLD 明けに次の通知で調べ直すのは `worker.py` の `run` のコメントどおりの振る舞いで、Splunk の通知（starts_at が検索ごとに変わる）は 053 の前から同じ端でも 2 本目を起こす（`review.md` の Round 1 に再現）。このサイクルでは直さない
- **Neptune を読めないとき、link_down の通知は firing も resolved も配り直しになる。** 053 の前は resolved は Neptune を読まなかった。b の端の resolved を a の端の id に読み替えるにはトポロジが要り、読まずに送ると b の端の解消が届かないまま消える。SQS の配り直し（5 回で DLQ）で Neptune の一時的な停止をしのぐ。DLQ に落ちると、ワークフローは解消を知らないまま、修復案の期限（expired）と `HOLD_MINUTES` まで残る
- **resolved は片方の端だけでも回線の解消として扱う。** 物理の回線は両端が同時に上がり下がりするので受け入れる。片方だけ admin down のような非対称の状態は、送り手が `admin_state` が disable の IF を数えない（Grafana と Splunk の gNMI。`savedsearches.conf:27`）ので、ここには来ない
- **Splunk の trap の target が IF 名でないとき（ifName が無い機器）はまとまらない。** lab の SR Linux は ifName を送る想定だが、AWS の trap の varbind は未確認。まとまらないときはいまの振る舞い（端ごと）に戻るだけで、壊れはしない
- **デプロイの入れ替えの間に走っている端ごとのワークフロー。** 古い id（spine 側）のワークフローに、新しい starter は resolved を送らない（`a` の id に送る）。PoC は毎回 `ops/down.sh` で消すので受け入れる。残っても `HOLD_MINUTES` で閉じる
- **修復案と Web の表示は `a` の端の機器名になる。** `list_proposals` を spine の機器名で絞ると出てこない。detail に両端を書くので、Web の詳細では分かる
- **アラートの履歴（S3 Tables の `alert_events`）は端ごとのまま。** status_handler（`app/graph/status_handler.py`）は starter を通らないので変わらない。`query_history` で spine を引けば spine 側の通知が出る
- **Neptune の辺の status は変えない。** 片端の resolved で辺を UP にする今の status_handler の振る舞いはこのサイクルの範囲外
- **AWS では確かめない。** 手元のテストだけ。AWS の次の動作確認で、1 本の回線断の修復案が 1 件になることを見る（そのときの検証記録に書く）
