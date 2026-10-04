# build — 003-proposals-in-s3tables

## Round 1

実装モデル: opus-5.5 / effort: xhigh（high に下げる手段がこのセッションに無く、xhigh のまま実装した）

worktree `.claude/worktrees/feat-proposals-s3tables`（ブランチ `feat/proposals-s3tables`、main `cfd58af` から）。

### 実装した内容

design.md の実装ステップ 1〜8 を実施。ただしステップ 4 の `terraform plan`（作り直しになるか）は AWS を触らないので未実行。

### 変更ファイル一覧

| ファイル | 変更 |
| :--- | :--- |
| `workflow/rules.py` | `PROPOSAL_EVENT_COLUMNS` を 28 列に。`proposal_event` を全項目の行に（seq、4000 字で切る）。`decision_from_message`、`anomaly_of`（proposal_id → ワークフロー ID）、`latest_proposals` |
| `workflow/awsio.py` | `audit_rows` に int。`latest_proposal` / `anomaly_proposals`（PyIceberg で読み、seq 最大の行）。`read_proposal` / `write_proposal` / `update_proposal` を削除。`receive_messages` / `delete_message` にキューの URL を渡す。`DECISION_QUEUE_URL` |
| `workflow/worker.py` | `put_proposal` が辞書を返す（同じ異常の古い pending に `expired` を足してから `created`。別の実行の行があれば再試行なしのエラー）。`record_event` に一本化（`get_decision` / `record_decision` / `set_status` を削除）。シグナル `decide` を辞書に（proposal_id が違えば無視、先着 1 回）。決定のキューを待つループ（`handle_decision`、NOT_FOUND で pending なら `expired`）。アラートのキューの `type: decision` は捨てる。30 秒ごとの頂点の読み取りを削除 |
| `agent/toolkit.py` | `athena_rows`（開始 → 待つ → 止める → 結果）、`athena_epoch`、`ATHENA_PARAM_RE` を evidence.py から移して共通化 |
| `agent/evidence.py` | `query_history` が `toolkit.athena_rows` を使う |
| `agent/proposals.py` | Athena で読む（row_number で proposal_id ごとに最新。値は ExecutionParameters）。`decide` は pending を確かめて決定のキューに送る（`status: sent`）。設定は `toolkit.Param`（環境変数 → SSM） |
| `agent/graph.py` | `get_record` / `update_record` を削除（`list_records` は残す） |
| `agent/app.py`、`tools/handler.py` | docstring の言い換え |
| `web/incident_view.py`、`web/app.py` | 「承認を送った。反映まで少し待つ（数秒〜20 秒。更新を押す）」の文言。docstring とコメント |
| `.env.example` | 承認タブの 5 つの設定（SSM 経由が既定）、ワーカーの `DECISION_QUEUE_URL`。`DECISION_POLL` を削除 |
| `terraform/pipeline/analytics/tables.tf` | `proposal_events` を 28 列に（`seq` は int、時刻 4 列は timestamptz） |
| `terraform/pipeline/analytics/history.tf`、`outputs.tf` | ワークグループと出力の説明 |
| `terraform/pipeline/graph/access.tf`、`locals.tf` | Neptune の Write / Delete は Web のロール（`graph_writer_role`）だけ。Runtime は読むだけ |
| `terraform/workflow/events.tf` | `<prefix>-decisions` と DLQ（5 回）。SNS の購読なし。ポリシーは VPC の外を拒むだけ |
| `terraform/workflow/iam.tf` | worker のタスクロールに decisions の受信・削除 |
| `terraform/workflow/proposals.tf` | Web のロールにだけ `sqs:SendMessage` と Athena の 4 文（`web_access`）。SSM に Web の設定 5 つ。コメントを書き直し |
| `terraform/workflow/locals.tf`、`gateway.tf` | Athena の 4 文を `history_read_statements` にまとめ、gateway と web_access の両方で使う。`HistoryTable` に proposal_events の ARN。tools Lambda に `PROPOSAL_EVENTS_TABLE` |
| `terraform/workflow/ecs.tf`、`outputs.tf` | worker に `DECISION_QUEUE_URL`。出力 `decision_queue_url` / `decision_dlq_url`。precondition の文言 |
| `tests/test_workflow.py`、`test_app.py`、`test_graph.py`、`test_sync.py` | 上の検査（Neptune の proposal の検査は削除して置き換え） |

`ops/up.sh`、`tests/test_analytics.py`、`web/config.py` は変えていない（下の逸脱を参照）。

### 設計からの逸脱と残るリスク

- Athena の共通の関数は `agent/evidence.py` ではなく `agent/toolkit.py` に置いた。Web の EC2 に上がる agent のモジュールは toolkit / topology / graph / proposals だけで、proposals.py が evidence.py を import すると Web で落ちるため（勉強用に報告済み）。
- Web の設定は `web/config.py` を変えずに `toolkit.Param`（環境変数が先、無ければ SSM の `<prefix>/<名前>`）で渡す。SSM のパラメータは terraform/workflow の proposals.tf が作る。そのため `ops/up.sh` に足すものは無い。
- Runtime のコンテナの中での代替実行（`list_proposals`）は、設計の「まだ配備されていない」ではなく「修復案を読めない: Athena を呼べない: …AccessDenied…」になる見込み。Runtime には `<prefix>/*` の `ssm:GetParameter`（proposals.tf の `reader_access`）があるので設定は読めるが、Athena の権限が無いため。AWS では未確認。
- worker のタスクロールの Neptune の Write / Delete は残した（使わなくなったが、外すことは設計に無い。iam.tf のコメントに書いた）。
- device_id と proposal_id の絞り込みはサブクエリの内側、status は外側（設計の SQL と同じ結果。最新の行の status で絞るため外側に置く必要がある）。SELECT は `*` ではなく 28 列を並べる。
- proposal_id の検査は `ATHENA_PARAM_RE`（`^[A-Za-z0-9._:/#?-]{1,128}$`）をそのまま使う。target は ifName / OID / IP で、この文字の範囲に収まる。
- `run()` が `_proposal_id` を決める前に届いた decide のシグナルは無視される（proposal_id が空と一致しない）。決定はワークフローが `created` を書いたあとにしか画面に出ないので、実際には起きない。
- test_sync の Neptune の権限の検査を「Runtime は読むだけ」に書き換えた（設計の変更対象のテストに無いが、ステップ 7 の権限の変更を縛る検査が他に無いため）。
- web/app.py の 30 秒ごとの描き直しが Athena を 1 本打つ。開いているブラウザ 1 つあたり 1 日 2880 本で、スキャンが小さいうちは 1 本 10 MB の最低課金で約 $0.14/日（勉強用に報告済み）。
- Athena の `?` がサブクエリの中で通るかは AWS で未確認（cycle 001 と同じ）。

### 検証（最後の編集のあとに取り直した出力）

`bash ops/check.sh` の全文は [build-r1-check.log](build-r1-check.log)（1526 行、2026-10-05 01:01、exit 0）。以下はその抜粋で、行は書き換えていない。

```
== 1. terraform fmt -check -recursive terraform
差分なし

== 2. 9 つのルートの validate
terraform/base/ecr  OK
terraform/base/core  OK
terraform/agent  OK
terraform/pipeline/lab  OK
terraform/pipeline/stream  OK
terraform/pipeline/analytics  OK
terraform/pipeline/graph  OK
terraform/pipeline/nautobot  OK
terraform/workflow  OK

== 3. ops スクリプトの構文
構文エラーなし

== 4. 模擬テスト
通過 134 / 失敗 0     (test_app)
通過 72 / 失敗 0      (test_graph)
通過 60 / 失敗 0      (test_stream)
通過 95 / 失敗 0      (test_sync)
通過 439 / 失敗 0     (test_analytics)
通過 294 / 失敗 0     (test_workflow)
通過 113 / 失敗 0     (test_alerts)
通過 7 / 失敗 0       (test_kb_index)
通過 75 / 失敗 0      (test_lab_debug)
58 項目すべて通過     (test_nautobot)
すべて通過
```

（括弧のファイル名は貼るときに足した。ログ中の Traceback は、例外の経路を確かめるテストが意図して出しているもの。）

変更前（main `cfd58af` を git archive で取り出して同じ venv で実行）の項目数: app 104、graph 74、sync 95、workflow 278、analytics 439。ほかは変えていない。graph は proposal の頂点の検査を消したので 2 減った。

#### design.md の検証方法と出力

1. `python3 tests/test_workflow.py`（ログの行番号）
   ```
   956: ok created の行は 28 列を全部（PROPOSAL_EVENT_COLUMNS の順）持ち、seq が 1、status が pending、decided_at が None
   957: ok 前の行から approved の行を作ると seq が 2 で、修復案の項目（kind / target / reason / precheck / first_seen / created_at）は同じ
   962: ok decision_from_message は決定の本文をシグナルの辞書にする（decided_at は sent_at）
   964: ok decision が approved / rejected でない、proposal_id が空や # の無い形なら None
   965: ok type が decision でない・アラートの JSON・読めない本文は None
   1240: ok アラートのキューに来た決定（{"type":"decision",…}）は、シグナルを送らず起こさずに消す（決定は決定のキューからだけ受ける）
   1229: ok 同じ異常で proposal_id の違う pending があれば、expired の行（verify_note は新しい修復案ができた）を 1 つ足してから created を足す（同じ append）
   1082: ok 決定のキュー（decisions）と DLQ（5 回）があり、SNS は購読しない（Web が直接送る）
   1083: ok 決定のキューに送れる（sqs:SendMessage）のは Web の EC2 のロールだけ（web_access は web_role_name に付ける。Runtime と tools Lambda には付けない = チャットから承認できない）
   1221: ok proposal_id の違うシグナル（同じ異常の前の発生・別の異常への決定）は無視して待ち続け、時間切れで expired
   1220: ok 合うシグナルが 2 回（approved、rejected の順）届いたら、効くのは 1 回目（approved、decided_by も 1 回目の名前）
   958: ok audit_rows は timestamptz を UTC の datetime に、seq を int に、None は None のまま、ほかは文字列にする
   1248: ok ワークフローが無い（NOT_FOUND）のに修復案が pending なら、expired の行（verify_note はワークフローがもう無い）を 1 つ足して消す
   ```
2. `python3 tests/test_app.py`
   ```
   190: ok proposals.COLUMNS は workflow/rules.py の PROPOSAL_EVENT_COLUMNS と同じ名前・同じ順（列を足したら両方）
   197: ok list_proposals は今と同じキー（画面とツールが読む 14 個）を返す
   198: ok 時刻は epoch 秒と JST（updated_at はその行の event_time）、seq は int、NULL の文字列は空文字、detail はアラートの detail
   191: ok list_proposals は設定が無ければ {"error": NOT_DEPLOYED, "proposals": []} で、Athena を呼ばない
   195: ok 値は ExecutionParameters で device_id → status の順に渡り、SQL の文字列には現れない。ワークグループ指定で打つ
   205: ok decide は pending を確かめてから、決定のキューに type decision の本文を 1 回送る（行は書かない）
   206: ok decide の返り値は status が sent（まだ approved ではない。行はワーカーがシグナルを受けて足す）
   207: ok 送った本文は workflow/rules.py の decision_from_message が読める（同じ形）
   208: ok pending でない修復案（approved）には送らずにエラー
   209: ok pending でない修復案（verified）には送らずにエラー
   210: ok pending でない修復案（expired）には送らずにエラー
   211: ok 修復案が無ければ送らずにエラー
   217: ok チャットのツール（TOOL_SPECS）に decide は無く、list_proposals だけ（承認は画面で人が決める）
   ```
3. 列の突き合わせ（test_analytics）と Runtime の Neptune の権限（test_sync）
   ```
   602: ok proposal_events の列と順は workflow/rules.py の PROPOSAL_EVENT_COLUMNS と同じ
   456: ok Runtime と Web のロールにも neptune-graph をこのグラフにだけ付ける。書き込み（Write / Delete）は Web だけで、Runtime は読むだけ（2026-10-05）
   ```
4. `grep -rn "n:proposal\|update_record" workflow agent web`
   ```
   rc=1
   ```
   （0 件。出力なしで grep の終了コードが 1）
5. ステップ 4 の `terraform plan`（proposal_events が作り直しになるか）— **未実行**（AWS を触らない）。
6. AWS 1〜6（承認待ちの 1 件、送ったの文言と 20 秒以内の反映、seq 1〜4 の並び、二重の決定で行が増えない、Neptune の proposal が 0、エージェントの list_proposals）— **未実行**（ユーザーが `ops/up.sh` を打つ）。
