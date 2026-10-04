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
| `agent/toolkit.py` | `athena_rows`（開始 → 待つ → 止める → 結果）、`athena_epoch`、`ATHENA_PARAM_RE` を evidence.py から移して共通化。セルフレビューで `ATHENA_TEXT_RE`（proposal_id 用）、`brief` / `brief_error`（画面に出す例外を短く、ARN とアカウント ID を伏せる）、クライアントを作るのを try の中へ |
| `agent/evidence.py` | `query_history` が `toolkit.athena_rows` を使う |
| `agent/proposals.py` | Athena で読む（row_number で proposal_id ごとに最新。値は ExecutionParameters）。`decide` は pending を確かめて決定のキューに送る（`status: sent`）。設定は `toolkit.Param`（環境変数 → SSM）。セルフレビューで proposal_id の検査を `ATHENA_TEXT_RE` に、SQS の例外を `brief_error` に |
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
| `tests/test_workflow.py`、`test_app.py`、`test_graph.py`、`test_sync.py` | 上の検査（Neptune の proposal の検査は削除して置き換え）。セルフレビューで test_app に 8 項目、test_workflow に 1 項目（下の「セルフレビュー」） |

`ops/up.sh`、`tests/test_analytics.py`、`web/config.py` は変えていない（下の逸脱を参照）。

### 設計からの逸脱と残るリスク

- Athena の共通の関数は `agent/evidence.py` ではなく `agent/toolkit.py` に置いた。Web の EC2 に上がる agent のモジュールは toolkit / topology / graph / proposals だけで、proposals.py が evidence.py を import すると Web で落ちるため（勉強用に報告済み）。
- Web の設定は `web/config.py` を変えずに `toolkit.Param`（環境変数が先、無ければ SSM の `<prefix>/<名前>`）で渡す。SSM のパラメータは terraform/workflow の proposals.tf が作る。そのため `ops/up.sh` に足すものは無い。
- Runtime のコンテナの中での代替実行（`list_proposals`）は、設計の「まだ配備されていない」ではなく「修復案を読めない: Athena を呼べない: AccessDeniedException: User: *** is not authorized …」（例外の名前と 160 字までの理由。ARN とアカウント ID は `***`）になる見込み（勉強用が受け入れ済み）。Runtime には `<prefix>/*` の `ssm:GetParameter`（proposals.tf の `reader_access`）があるので設定は読めるが、Athena の権限が無いため。AWS では未確認。
- worker のタスクロールの Neptune の Write / Delete は残した（使わなくなったが、外すことは設計に無い。iam.tf のコメントに書いた）。
- device_id と proposal_id の絞り込みはサブクエリの内側、status は外側（設計の SQL と同じ結果。最新の行の status で絞るため外側に置く必要がある）。SELECT は `*` ではなく 28 列を並べる。
- proposal_id の検査は `ATHENA_TEXT_RE`（`'` と制御文字を除く 1000 字まで）。target は自由文になりうる（Splunk は ifDescr に落ちる）ので、device_id・status 用の `ATHENA_PARAM_RE` より広くした（セルフレビューの E1）。`'` を含む target の修復案は今も詳細を引けず、承認も送れない。
- `run()` が `_proposal_id` を決める前に届いた decide のシグナルは無視される（proposal_id が空と一致しない）。決定はワークフローが `created` を書いたあとにしか画面に出ないので、実際には起きない。
- test_sync の Neptune の権限の検査を「Runtime は読むだけ」に書き換えた（設計の変更対象のテストに無いが、ステップ 7 の権限の変更を縛る検査が他に無いため）。
- web/app.py の 30 秒ごとの描き直しが Athena を 1 本打つ。開いているブラウザ 1 つあたり 1 日 2880 本で、スキャンが小さいうちは 1 本 10 MB の最低課金で約 $0.14/日（勉強用に報告済み）。
- Athena の `?` がサブクエリの中で通るかは AWS で未確認（cycle 001 と同じ）。

### 検証（最後の編集のあとに取り直した出力）

セルフレビューで直したあとに取り直した出力は、下の「セルフレビュー」の末尾にある。この節は直す前のもの。

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

### セルフレビュー

- 自分: opus-5.5 / effort xhigh。
- 反対弁護人: opus（general-purpose。文脈を渡して 1 回、読み取り専用）。作ったのは scratchpad の probe1.py / probe2.py だけ。いまの `git status --porcelain -uall` は自分の変更 4 ファイルとこのラウンドのログだけ。
- 反対弁護人の結論: Must は無し。Should は E1・R1・R2・R3、残りは Nit。

#### 指摘と片付け

| ID | 分類 | [観点] | 場所 | 破綻シナリオ | 確かめたこと | 片付け |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| S1 | Should | [security / 情報漏洩] | `agent/toolkit.py` の `athena_rows` の except と FAILED の理由、`agent/proposals.py` の SQS の except | AWS の例外文をそのまま画面とチャットに返す。AccessDenied の本文には assumed-role の ARN とアカウント ID が入る | 注入 g / h / i（生の例外に戻す）で test_app が落ちる | 直した。`brief_error`: 例外の名前と 160 字までの理由。ARN・12 桁の数字・AKIA/ASIA のキー ID は `***`（勉強用の依頼 1） |
| S2 | Should | [runtime] | `agent/toolkit.py` の `athena_rows` | Athena のクライアントを try の外で作っていた。リージョンが無いと BotoCoreError が上がり、Web のハンドラが落ちる | 注入 p（try の外へ戻す）で `BotoCoreError: You must specify a region.` が上がって test_app が落ちる | 直した。try の中で作り「Athena を呼べない」にする |
| S3 | Should | [missing tests] | `tests/test_app.py` | Athena が断ったときに「読めない」が返り、例外で落ちないことを縛るテストが無かった | — | 足した（ログ 220〜223。勉強用の依頼 2） |
| S4 | Should | [missing tests / runtime] | `tests/test_workflow.py` | Web の EC2 に配る 4 モジュール（graph / proposals / toolkit / topology）だけで proposals を import できるか、import で boto3 のクライアントを作らないかを縛るテストが無かった | 注入 j（proposals が evidence を import）と k（toolkit が import で client を作る）で落ちる | 足した（ログ 1127。子プロセスに 4 モジュールと agent/data だけを写し、boto3 の client / Session は呼ぶと例外にする） |
| S5 | Should | [missing tests] | `tests/test_app.py` | `athena_rows` の既定の狭い検査を縛るテストが無かった。query_history は呼ぶ前に自分で device_id を検査するため | 注入 n（既定を ATHENA_TEXT_RE に広げる）が **rc=0 で通った** | 足した（ログ 186）。足したあと注入 n で落ちることを確かめた |
| E1 | Should | [入力検証 / 回帰] | `agent/proposals.py` の `_query` / `get_proposal` / `decide`（旧 `ATHENA_PARAM_RE`） | target が自由文だと、修復案はできても詳細を引けず、承認も送れず、expired まで残る。自由文になるのは、Splunk の savedsearches.conf L132 で ifDescr に落ちたとき（空白・`[]`・日本語・128 字超え）。基点の Neptune では決められたので回帰 | 反対弁護人の probe1: `Ethernet Interface 1` / `eth[1]` / 120 字の target は受け付けられ、`ATHENA_PARAM_RE` は False | 直した。proposal_id は `ATHENA_TEXT_RE`（`'` と制御文字を除く 1000 字まで。ExecutionParameters は 1 つ 1024 字まで）。device_id と status は狭いまま。ログ 207 / 208、注入 m / o で落ちる。`'` を含む target は残る |
| R1 | Should | [HITL / 順序 / 監査] | `workflow/worker.py` の decide（先着 1 回）、`terraform/workflow/events.tf` の標準キュー | 2 人が数秒差で却下と承認を押すと、ワーカーに先に届いたほうが勝つ。押した順ではない。負けた決定は proposal_events にもログにも残らない（Temporal の履歴にはシグナルとして残る） | 読んだだけ。反対弁護人の probe2 は temporalio が無く動かず、こちらも再現していない | 差し戻し（勉強用）。design.md L72 / L164 の「標準キュー・先着 1 回」どおりの動きで、実装では吸収しない。選択肢は FIFO、`ignored` の行、却下を優先、の 3 つ |
| R2 | Should | [data / 履歴の消失] | `workflow/worker.py` の NOT_FOUND の救済 | Temporal の履歴が消えたとき（/tmp の dev server）に救えるのは pending だけ。approved / applied で止まった修復案は閉じない。基点も同じ | 読んだだけ | 差し戻し（勉強用）。design.md のリスク 5 の範囲 |
| R3 | Should | [デプロイ順 / TF] | design.md のリスク 9、`workflow/awsio.py` の `from_pylist` | 古いワーカーが 28 列のテーブルに書いても失敗しない。seq / first_seen が null の行が入る。テーブルを作り直すと ARN が変わり、workflow を apply し直すまで Web とツールの HistoryTable は古い ARN を指す | 再現した（下の r3_old_writer.py）。null の seq は、`rules._seq` が 0 とし、Athena の DESC も NULLS LAST なので、どちらも最新に選ばない。ARN の件は読んだだけ | 差し戻し（勉強用）。リスク 9 の文言と「analytics を作り直したら workflow も apply」 |
| R4 | Nit | [UX] | `workflow/worker.py` の decide | expired の直前に押した決定は黙って捨てられ、画面は sent のまま。DLQ にアラームが無い | 読んだだけ | 最終報告へ |
| R5 | Nit | [決定の経路] | `terraform/workflow/ecs.tf`（UI 8233、ECS Exec） | Temporal の UI と ECS Exec から、pending の確認なしでシグナルや Reset を送れる。届く人はリスク 6 と同じ管理者 | 読んだだけ。UI の書き込み操作が既定で有効かは未確認 | 最終報告へ |
| R6 | Nit | [最小権限] | `terraform/workflow/iam.tf` ほか | ワーカーの Neptune の Write / Delete、AuditTable の `table/*`、RunCommandResult の `*` が残っている | 読んだだけ | 最終報告へ（外すことは設計に無い） |
| R7 | Nit | [コスト] | `web/app.py` の 30 秒の Timer、`web/incident_view.py` | Athena を 30 秒ごとに 1 本、承認 1 回で 2 本打つ | 読んだだけ | 逸脱の欄に記録済み |
| R8 | Nit | [Temporal] | `workflow/worker.py` の `_proposal_id` | init で決めていないので、run の前のシグナルは捨てられる | 読んだだけ | 逸脱の欄に記録済み（行が書かれるまで pid は画面に出ない） |
| R9 | Nit | [設計とのずれ] | — | 記録済みの逸脱（Runtime の文言、toolkit に置いたこと） | — | 記録済み |

#### 退行を入れて、テストが落ちるかを確かめた

ファイルを書き換えてテストを走らせ、必ず元に戻した（scratchpad の inject_r1sr.py ほか）。全部、落ちた。

| 注入 | 入れた退行 | 落ちたテスト |
| :--- | :--- | :--- |
| a | graph/access.tf で Runtime にも Write / Delete | test_sync「Runtime と Web のロールにも neptune-graph …Runtime は読むだけ」 |
| b | decide から `self._decision or` を外す（後勝ち） | test_workflow「合うシグナルが 2 回…効くのは 1 回目」 |
| b2 | pid の突き合わせを外す | test_workflow「proposal_id の違うシグナル…は無視して待ち続け」 |
| c | アラートのキューで決定を処理する | test_workflow「アラートのキューに来た決定…は…消す」 |
| d | web_access を reader のロールにも付ける | test_workflow「決定のキューに送れるのは Web の EC2 のロールだけ」 |
| e | status を SQL の内側で絞る | test_app「list_proposals の SQL は…status は外側」 |
| f | put_proposal で古い pending を expired にしない | test_workflow「同じ異常で proposal_id の違う pending があれば、expired の行を…」 |

g〜p の出力（inject_r1sr.py）:

```
== g: brief_error を外す（生の例外を 300 字）: rc=1
    AssertionError: Athena が AccessDenied で断っても落ちずに「修復案を読めない: Athena を呼べない: AccessDeniedException: …」。ARN とアカウント ID は出さず短い
== h: FAILED の理由を伏せない: rc=1
    AssertionError: Athena が FAILED の理由に ARN があっても伏せて短く返す（落ちない）
== i: SQS の例外を生で返す: rc=1
    AssertionError: SQS が AccessDenied でも「送れない: AccessDenied: …」だけ（ARN とアカウント ID は出さない）
== j: proposals が evidence を import する: rc=1
    AssertionError: Web に上げる agent のモジュール（graph proposals toolkit topology）だけで proposals を import でき、import で boto3 のクライアントを作らず、設定が無ければ NOT_DEPLOYED を返す（evidence などは読まない）:  call last):
== k: toolkit が import のときに Athena のクライアントを作る: rc=1
    AssertionError: Web に上げる agent のモジュール（graph proposals toolkit topology）だけで proposals を import でき、import で boto3 のクライアントを作らず、設定が無ければ NOT_DEPLOYED を返す（evidence などは読まない）: oolkit
== l: status を内側で絞る（SQL の意味）: rc=1
    AssertionError: list_proposals の SQL は proposal_id ごとに最新の行（seq、同じなら event_time が遅いほう）を選び、status は外側、device_id は内側で絞る
== m: proposal_id を狭い検査に戻す: rc=1
    AssertionError: 空白・[]・日本語の入った proposal_id も詳細を引けて、承認を送れる（値は ExecutionParameters で渡る）
== n: athena_rows の既定の検査を広げる（query_history も ' 以外を通す）: rc=1
    AssertionError: toolkit.athena_rows も既定は狭い検査（空白も通さない）。広い検査（proposal_id 用）は param_re で渡したときだけで、それでも ' は通さない
== o: ATHENA_TEXT_RE が改行を通す（\Z を $ に）: rc=1
    KeyError: 'error'
== p: クライアントを try の外で作る: rc=1
    BotoCoreError: You must specify a region.
```

（n は S5 のテストを足す前は rc=0 だった。o と p は AssertionError ではなく例外で落ちる。）

#### 木の外で確かめたもの

- PyIceberg 0.10.0（SqlCatalog）で、28 列の書き込みと読み直し、型、同じ異常の古い pending を残すこと（scratchpad の ice_roundtrip.py）
  ```
  pids ['dc1-leaf-01#link_down#ethernet-1/1#1699999900', 'dc1-leaf-01#link_down#ethernet-1/1#1700000000']
  new approved 2 山田 (web) 1700000119 1700000000 1700000060 1700000120
  old pending 1
  types {'seq': 'int', 'first_seen': 'int', 'precheck': 'str', 'decided_at': 'int', 'apply_output': 'str'}
  json True none {} other {}
  OK
  ```
- R3: 基点の awsio（12 列）で、28 列のテーブルに書く（scratchpad の r3_old_writer.py）
  ```
  old cols 12 new cols 28
  APPEND OK
  rows 1
  {'proposal_id': 'a#1', 'seq': None, 'event': 'approved', 'status': 'approved', 'device_id': 'd1', 'first_seen': None, 'decided_by': '山田', 'event_time': datetime.datetime(2023, 11, 14, 22, 13, 20, tzinfo=zoneinfo.ZoneInfo(key='UTC'))}
  ```
- SQL の意味: sqlite で確かめ、test_app に取り込んだ（ログ 198）

#### 問題なしとした観点と根拠

| 観点 | 根拠 |
| :--- | :--- |
| SQL インジェクション | 値は ExecutionParameters で渡り、`'v'` の中で意味を持つ `'` と制御文字は弾く（ログ 186 / 202 / 208、注入 m / o）。カタログとテーブルの名前は SSM から取る（読んだだけ） |
| HITL | SendMessage は Web のロールだけ（注入 d）。decide はチャットのツールに無い（test_app 217）。アラートのキューに来た決定は捨てる（注入 c） |
| 先着 1 回と pid の突き合わせ | 注入 b / b2 |
| 最新の行の選び方 | sqlite の意味テスト（ログ 198）、注入 e / l。同じ seq の行が 2 つあっても 1 つに決まる（反対弁護人の probe1: `dup rows seq 2 2 latest status approved`） |
| 画面に出す例外 | 注入 g / h / i / p |
| Web の EC2 での import | 注入 j / k |
| Terraform | fmt と 9 つのルートの validate（check.sh の 1・2）。plan は未実行 |

#### 未確認（AWS を触らないため）

1. Athena の ExecutionParameters が、サブクエリの中の `?` に効くか
2. proposal_events の変更が、plan で replace になるか
3. Runtime での list_proposals の実際の文言
4. Temporal の dev server の UI で、書き込み操作が既定で有効か（R5）
5. SQS の標準キューで、数秒差の 2 通が逆順に届く頻度（R1）
6. design.md の AWS 1〜6

#### ジンテーゼ（反対弁護人のあとで変えた結論）

- **proposal_id の検査**
  - 前: proposal_id は `ATHENA_PARAM_RE` に収まる。
  - 後: 収まるのはいまの lab の送り元だけで、入口では保証されていない。そこで出口の検査を、`'` と制御文字だけを弾く形に広げた。`'` を含む target の修復案は、今も決められない。
- **二重の決定**
  - 前: 先着 1 回なので、二重の決定は安全。
  - 後: 安全なのはデータの面だけ。先着は押した順ではなく、ワーカーに届いた順で決まる。負けた決定は Temporal の履歴にしか残らない（設計へ差し戻し）。
- **古いワーカー（design のリスク 9）**
  - 前: 古いワーカーは書けずに、それで気づける。
  - 後: 書けてしまう。古いワーカーが作った修復案は、空欄の多い行として一覧に出る。それでも最新には選ばれないので、新しいワーカーが作った修復案の状態は壊れない。

#### セルフレビューで直したあとの検証

`bash ops/check.sh` の全文は [build-r1-selfreview-check.log](build-r1-selfreview-check.log)（1535 行、2026-10-05 01:37、exit 0）。

```
== 1. terraform fmt -check -recursive terraform
差分なし
== 2. 9 つのルートの validate      （9 つとも OK）
== 3. ops スクリプトの構文
構文エラーなし
== 4. 模擬テスト
通過 142 / 失敗 0     (test_app)
通過 72 / 失敗 0      (test_graph)
通過 60 / 失敗 0      (test_stream)
通過 95 / 失敗 0      (test_sync)
通過 439 / 失敗 0     (test_analytics)
通過 295 / 失敗 0     (test_workflow)
通過 113 / 失敗 0     (test_alerts)
通過 7 / 失敗 0       (test_kb_index)
通過 75 / 失敗 0      (test_lab_debug)
58 項目すべて通過     (test_nautobot)
すべて通過
```

（括弧のファイル名は貼るときに足した。== 2 は 9 行を 1 行にまとめた。）セルフレビューで足した項目（ログの行番号）:

```
186: ok toolkit.athena_rows も既定は狭い検査（空白も通さない）。広い検査（proposal_id 用）は param_re で渡したときだけで、それでも ' は通さない
198: ok SQL の意味（sqlite）: 最新は seq が最大で同じなら event_time が遅い行。status は最新の行で絞り、機器と id は全行で絞る。新しい順
207: ok 空白・[]・日本語の入った proposal_id も詳細を引けて、承認を送れる（値は ExecutionParameters で渡る）
208: ok 128 文字を超える proposal_id も Athena に渡す。' ・改行・1000 文字超えは渡さない
220: ok Athena が AccessDenied で断っても落ちずに「修復案を読めない: Athena を呼べない: AccessDeniedException: …」。ARN とアカウント ID は出さず短い
221: ok Athena が FAILED の理由に ARN があっても伏せて短く返す（落ちない）
222: ok SQS が AccessDenied でも「送れない: AccessDenied: …」だけ（ARN とアカウント ID は出さない）
223: ok Athena のクライアントを作れない（リージョンが無いなど）ときも落ちずに「修復案を読めない」
1127: ok Web に上げる agent のモジュール（graph proposals toolkit topology）だけで proposals を import でき、import で boto3 のクライアントを作らず、設定が無ければ NOT_DEPLOYED を返す（evidence などは読まない）: RESULT [] [] True
```

ログ 1473 行の `neptune read failed, using static data: Missing Dependency …` は、直す前のログ（build-r1-check.log）にも同じ行がある。test_nautobot の冒頭で `agent/topology.py` が Neptune を読めずに静的データへ切り替えたときの警告で、今回の変更とは関係ない。
