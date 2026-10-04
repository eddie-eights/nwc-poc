# アラートの履歴を残す（Cycle 001 alert-history-firehose）設計: アラートの通知の履歴を Lambda graph-status → Firehose → S3 Tables（alert_events）に追記し、Athena で読む

main(opus-5.5) / effort: high（cycle-design の既定は xhigh だが、セッションの effort を自分で変えられないため high で設計した）

## 背景

- 障害の履歴を置く場所は、2026-10-02 に `anomaly_events` を消してから決まっていない（docs/data-stores.md:17 に「未定」とある）。いまの異常は Neptune の `status` で、アラートは Grafana と Splunk の画面で見ている。どちらの画面もタスクと一緒に消える。
- これまでの会話で次を決めた:
  - 開いた・閉じたの記録は、すべてのアラートを SNS で受ける Lambda `<prefix>-graph-status` が書く。Temporal には書かせない。
  - S3 Tables は履歴の置き場としてだけ使う。「いま開いている障害」は Neptune を見る。
  - MSK のトピックは足さない。
  - Temporal による証拠集め（collect_evidence）は別のサイクルでやる。
- エージェントのツール `query_history`（agent/evidence.py:97）は、いまは「Athena 未配備」と返すだけ。これを本物にする。

## 設計方針

### 合意した決定（経緯は design-log.md の Round 0）

1. 1 行は、届いたアラートの通知 1 件。Lambda の側では重複を落とさない。Grafana の 4 時間ごとの送り直しも、Grafana と Splunk の両方から来た分も、そのまま行にする。開いた・閉じたの組み合わせは読む側で作る。
2. 対象はすべての kind（link_down / bgp_down / isis_down / trap）。Neptune に該当する頂点が無くて無視した通知も記録する。ただし `device_id` か `kind` が無い通知と、status が firing / resolved でない通知は記録しない（`alerts_from_message` が落とす。anomaly_id を作れないため）。落としたときはログに WARNING を出す。Grafana と Splunk のルールはどちらも必ず付けるので、普段は起きない。
3. `ops/down.sh` で履歴が消えてよい。テーブルは proposal_events と同じテーブルバケットに置く。
4. このサイクルで Athena まで作り、エージェントの `query_history` を alert_events につなぐ。
5. テーブル名は `alert_events`。
6. status の正しさを履歴の完全さより優先する。Neptune への書き込みが失敗したら最後に例外を投げ、Lambda の非同期のやり直し（2 回）に任せる。Firehose への送信の失敗では例外を投げない。Lambda の中で 3 回まで送り直し、それでも残った行はログに ERROR として行の中身ごと書いて終わる（欠けた分はログから戻せる）。やり直しで履歴が二重に入った分は、読むときに event_id で落とす。
7. `s3tablescatalog`（Glue の S3 Tables 連携。アカウントとリージョンに 1 つ）は、無ければ `ops/up.sh` が作る。down.sh では消さない（ほかの OWNER の環境と共有しているため）。
8. `query_history` は通知の行をそのまま返す。event_id で重複を落とし、新しい順に最大 50 件。
9. 技術的な前提として次をユーザーと確認済み:
   - Firehose のストリーム、そのロール、Athena のワークグループは analytics に置く。名前は固定。
   - VPC エンドポイントに kinesis-firehose と athena の 2 本を足す。
   - Firehose のロールを閉域の Deny の例外に入れる。
   - 書けなかった行は `firehose-errors/` に落とす。
   - Athena のクエリ結果は Athena の管理ストレージに置く。
   - Firehose のバッファは 60 秒。
   - event_id の作り方は下のとおり。

### 実物で確認した入力の書式（推測ではない）

Grafana の通知テンプレート。grafana/provisioning/alerting/netops.yaml:82 から引用:
```
{"source":"grafana","alerts":[{"status":%q,"device_id":sysName,"kind":kind,"target":ifName,"detail":"<if> is down (grafana)","starts_at":StartsAt.Unix}]}
```

Splunk の通知。splunk/netops_alerts/bin/netops_sns.py:6 と :106 から引用。キーは Grafana と同じで、1 通に最大 50 件入る:
```
{"source": "splunk", "alerts": [{"status", "device_id", "kind", "target", "detail", "starts_at"}, …]}
```

- 読むのは `workflow/rules.py` の `alerts_from_message` で、返り値は `{anomaly_id, device_id, kind, target, detail, status, first_seen, source}`。anomaly_id は `<device_id>#<kind>#<target>`。
- starts_at の意味は送り手によって違う:
  - Grafana の resolved は、発火したときの StartsAt を持ったまま来る。firing は同じ starts_at で 4 時間ごとに送り直される。
  - Splunk の starts_at は、その状態の latest(_time)。resolved では解消した時刻になる。
- この違いは列を変えずにそのまま持ち、docs に書く。

### 書く側（pipeline/graph）

- `graph/status_handler.py` の `handler` は、呼び出しの全部の通知の行を組み、環境変数 `ALERT_STREAM` が空でなければ先に `firehose.put_record_batch` で送る（500 件ずつ。SNS の 1 通は多くて 50 件なので、ふつうは 1 回）。そのあと alert ごとに `apply` で Neptune に書く（Round 3）。
  - 行は通知だけから組み（`rules.alert_event`）、Neptune の結果を入れないので、Neptune より先に送れる。Neptune が遅くても応答しなくても、Lambda の timeout の前に履歴は残る。
  - `FailedPutCount > 0` のときは失敗した行だけを、例外のときは全部の行を送り直す。試すのは合わせて 3 回まで（あいだは 0.2 秒、0.4 秒）。
  - 3 回目のあとも残った行は、1 行ずつ JSON のままログに ERROR で書く。例外は投げず、Neptune に進む。
  - Firehose に使う時間に上限を付ける（Round 2）。Firehose のクライアントは botocore の再試行を切り、接続 2 秒・読み 3 秒にする。3 回 ×（接続 2 秒 + 読み 3 秒）+ 待ち 0.6 秒 = 15.6 秒で、timeout 60 秒のうち 44 秒は Neptune に残る。接続の待ちはエンドポイントの IP ごとにかかるので、エンドポイントが 2 つの AZ にあるとき（`endpoints_multi_az = true`）は 3 ×（2 × 2 + 3）+ 0.6 = 21.6 秒で、残りは 38 秒（Round 4 の実装で実測して足した。Neptune 1 回の 33 秒と足しても 60 秒に収まる）。
  - Lambda の timeout は 60 秒（Round 2 で 30 秒から延ばした）。
  - この Lambda が使う Neptune のクライアントは、待ちを短くする（接続 3 秒、読み 10 秒、試すのは 2 回まで。2 回目は、使い回した接続が向こうで切れていた（keep-alive の切れ）ときを救う。1 回の呼び出しは長くて 2 ×（3 + 10）秒 + 再試行の前の待ち 1 秒 = 27 秒、エンドポイントが 2 つの AZ にあれば 2 ×（2 × 3 + 10）+ 1 = 33 秒）。エージェントや up.sh が使う `agent/graph.py` の既定（接続 10 秒、読み 60 秒、3 回まで）は変えない（Round 3）。
  - Neptune への書き込みの例外は捕まえて覚えておき、ほかの alert の処理は続ける。最後に、1 件でも失敗していれば RuntimeError を投げ、Lambda の非同期のやり直し（2 回）に任せる。Neptune の途中で timeout したときも、同じくやり直しになる（残り時間を見て打ち切ることはしない）。どちらもやり直しで同じ行がもう一度送られ、読む側が event_id で落とす。
  - Firehose の失敗だけでは例外を投げない（投げると、Firehose が止まっているあいだ、通知のたびに Neptune への書き込みまでやり直しになる）。
  - `alerts_from_message` が落とした通知（`device_id` か `kind` が無い、status が firing / resolved でない）は、落とした件数をログに WARNING で書く（`alerts` の要素の数と、返ってきた数の差）。行にはしない。
  - 行を組めない通知（starts_at が epoch ミリ秒で 9999 年を超えるなど、`rules.alert_event` が例外になるもの）は、その 1 件だけを行にせず、ALERT_DROPPED の WARNING に 1 件ずつ書いて、Neptune には書く。その 1 件のせいで、ほかの通知の行と Neptune の書き込みを落とさない（Round 4 の実装のセルフレビューで足した）。
  - 行の JSON に UTF-8 にできない文字（孤立したサロゲート）があれば、その行だけ `\u` でエスケープする。1 行のせいで 500 件のバッチと ERROR の書き出しを落とさない（同じく Round 4 で足した）。
  - `ALERT_STREAM` が空なら行を組まず、Firehose には何も送らない。いまの動きと同じ。
- 行の形（列名は小文字。Iceberg V2 で、Firehose が Parquet にする）。列の定義は `workflow/rules.py` に `ALERT_EVENT_COLUMNS` として置く。PROPOSAL_EVENT_COLUMNS と同じ作りで、tables.tf のテストが突き合わせる:
  - `event_id` string: `<anomaly_id>#<source>#<status>#<starts_at>`（starts_at は epoch 秒の整数）
  - `anomaly_id`, `source`, `status`, `device_id`, `kind`, `target`, `detail`: string（alerts_from_message の値をそのまま）
  - `starts_at` timestamptz: 通知の starts_at
  - `received_at` timestamptz: Lambda が受けた時刻
  - 時刻は ISO 8601 の UTC（`2026-10-04T07:00:00.000000Z`）で送る。Firehose が受け付ける書式は文書で確かめきれていないので、リスクの欄に書き、検証方法でも確かめる。
  - 行を組み立てる関数 `rules.alert_event(alert, received_at)` は純粋関数にして、テストする。
- `terraform/pipeline/graph/sync.tf`:
  - 変数 `alert_history`（bool。既定 false）を足す。true のときは Lambda の環境変数に `ALERT_STREAM = "${prefix}-alert-events"` を入れ、IAM に `firehose:PutRecordBatch` を足す（そのストリームの ARN だけ）。
  - `ops/up.sh` は、analytics がある回（今回作るか、state に残っている。up.sh の `analytics_on`）にだけ `-var alert_history=true` を渡す。graph は analytics より前に apply するが、名前は固定なので analytics の output を待たなくてよい（Round 5 で「analytics を作る回（SKIP_ANALYTICS が空）」から改めた。`SKIP_ANALYTICS=1` で analytics を残したまま graph だけ作り直すと、ストリームがあるのに履歴が止まっていた）。
- `ops/up.sh` の `endpoints_for`:
  - `pipeline/graph` で、analytics がある回は `kinesis-firehose` を足す。
  - `workflow` で、analytics がある回は `athena` を足す。
  - analytics が state に残っていると分かるのは手順 3 の残ったルートのループのあとなので、そのとき今回作るルートの分をもう一度足す（Round 5）。
  - `terraform/base/core/variables.tf` の validation に `kinesis-firehose` と `athena` を足す。

### Firehose → S3 Tables（pipeline/analytics と base/core）

- `terraform/pipeline/analytics/tables.tf` に `aws_s3tables_table.alert_events` を足す。いつも作る（count は付けない）。列は上の ALERT_EVENT_COLUMNS。
- `terraform/pipeline/analytics/` に新しく `history.tf` を作る:
  - `aws_iam_role.alert_firehose`:
    - 名前は `${prefix}-alert-firehose`。trust は firehose.amazonaws.com で、aws:SourceAccount で絞る。
    - 許可は s3tables（GetTableBucket / GetNamespace / GetTable / GetTableData / GetTableMetadataLocation / PutTableData / UpdateTableMetadataLocation）。対象はテーブルバケットとその `/table/*`。
    - glue（GetCatalog / GetDatabase(s) / GetTable(s) / UpdateTable）。対象は `catalog`、`catalog/s3tablescatalog`、`catalog/s3tablescatalog/*`、`database/*`、`table/*/*`。
    - 土台のバケットの `firehose-errors/*` への s3:PutObject と、関連する s3 の操作。
    - logs:PutLogEvents。
    - 閉域の IAM 側の Deny は付けない（Firehose は VPC の外から呼ぶため）。
  - `aws_kinesis_firehose_delivery_stream.alert_events`:
    - 名前は `${prefix}-alert-events`、destination は `iceberg`。
    - `catalog_arn = arn:aws:glue:<region>:<acct>:catalog/s3tablescatalog/<table bucket 名>`。
    - `destination_table_configuration` は database = namespace、table = alert_events。
    - `buffering_interval = 60`、`buffering_size = 1`（MiB）。
    - `s3_configuration` は土台のバケットで、`error_output_prefix = firehose-errors/alert_events/`。
    - `s3_backup_mode = FailedDataOnly`。CloudWatch のログを付ける。
  - `aws_athena_workgroup.history`:
    - 名前は `${prefix}-history`。`managed_query_results_configuration { enabled = true }` で、クエリ結果用のバケットは作らない。
    - `enforce_workgroup_configuration = true`、`bytes_scanned_cutoff_per_query` を付けて走りすぎを止める。
    - force_destroy にする。
  - outputs: `alert_events_stream_name`、`alert_events_table_name`、`athena_workgroup`、`athena_catalog`（`s3tablescatalog/<table bucket 名>`）。
- `terraform/base/core/perimeter.tf`:
  - `perimeter_exempt_principals` に `arn:...:role/${prefix}-alert-firehose` を足す。`-kb` と同じ理由（サービスがロールを引き受け、自分の側から来る）で、コメントにもそう書く。
  - これでテーブルバケットのポリシー（tables.tf）と土台のバケットのポリシー（bucket.tf）の両方の Deny から外れる。
- `ops/up.sh`:
  - analytics を apply する前に、`aws glue get-catalog --catalog-id s3tablescatalog` が失敗したときだけ `aws glue create-catalog` を打つ。
    - FederatedCatalog は Identifier `arn:aws:s3tables:<region>:<acct>:bucket/*`、ConnectionName `aws:s3tables`。
    - CreateDatabaseDefaultPermissions と CreateTableDefaultPermissions は IAM_ALLOWED_PRINCIPALS / ALL。
    - `AllowFullTableExternalDataAccess=True`。
  - 冪等にする。`ops/down.sh` では消さない。消し方は docs に書く。

### 読む側（Athena → query_history。workflow）

- `agent/evidence.py` の `query_history(device_id, hours)` を作り直す。環境変数は `ATHENA_WORKGROUP`、`ATHENA_CATALOG`、`HISTORY_NAMESPACE`、`ALERT_EVENTS_TABLE`。
  - どれかが空なら、いまと同じ「未配備」を返す。
  - 次の SQL を `start_query_execution` に投げ、`get_query_execution` を最大およそ 20 秒ポーリングし、`get_query_results` で読む:
    ```sql
    SELECT event_id, anomaly_id, source, status, device_id, kind, target, detail, starts_at, received_at
    FROM (SELECT *, row_number() OVER (PARTITION BY event_id ORDER BY received_at) rn FROM "<catalog>"."<ns>"."alert_events"
          WHERE received_at > current_timestamp - interval '<h>' hour [AND device_id = ?])
    WHERE rn = 1 ORDER BY received_at DESC LIMIT 50
    ```
  - device_id は Athena の `ExecutionParameters` で渡し、SQL に埋め込まない。hours は 1〜720 の整数に丸める。
  - 時刻には JST を足して返す（recent_changes と同じ見せ方）。
  - boto3 の athena クライアントを使う。tools Lambda の python3.13 には boto3 が入っているので、依存は増えない。
  - 説明文を「アラートの通知の履歴（Grafana / Splunk。発火と解消）」に変える。`tools/tools.json` と `TOOL_SPECS` の両方を直す。
- `terraform/workflow/gateway.tf`:
  - tools Lambda に上の環境変数 4 つを入れる。値は analytics の remote state から読み、無ければ空。
  - IAM を足す:
    - athena の StartQueryExecution / GetQueryExecution / GetQueryResults / StopQueryExecution。対象はそのワークグループ。
    - glue の GetCatalog / GetDatabase / GetTable。対象は s3tablescatalog の配下。
    - s3tables の GetTableBucket / GetNamespace / GetTable / GetTableData / GetTableMetadataLocation。対象はテーブルバケット。
  - tools Lambda に付いている閉域の Deny（s3tables:*）は、Athena が呼び手に代わって出す呼び出し（aws:ViaAWSService=true）には効かない、という前提を置く。この前提はリスクの欄に書く。
- `agent/app.py` と `workflow/rules.py` のプロンプトの「長期の履歴は query_history（S3）」は、「アラートの履歴は query_history」に言い換える。

## 変更対象ファイル

- 書く側: `graph/status_handler.py`、`workflow/rules.py`（ALERT_EVENT_COLUMNS と alert_event）、`terraform/pipeline/graph/sync.tf`（と variables.tf）
- S3 Tables と Firehose: `terraform/pipeline/analytics/tables.tf`、`terraform/pipeline/analytics/history.tf`（新規）、`terraform/pipeline/analytics/outputs.tf`
- 土台: `terraform/base/core/perimeter.tf`、`terraform/base/core/variables.tf`
- 読む側: `agent/evidence.py`、`tools/tools.json`、`agent/app.py`、`terraform/workflow/gateway.tf`、`terraform/workflow/locals.tf`
- 配備: `ops/up.sh`（catalog の作成、alert_history、エンドポイント）
- テスト: `tests/test_analytics.py`、`tests/test_sync.py`、`tests/test_app.py`、`tests/test_workflow.py`、`tests/test_alerts.py`（必要なら）
- docs: `docs/data-stores.md`（「未定」を alert_events にする）、`docs/architecture/pipeline.md`、`docs/pipeline.md`、`docs/development.md`、`docs/architecture/agent.md`、`docs/deploy.md`（エンドポイント 2 本と Firehose の費用）

## 再利用するもの

- `workflow/rules.py` の `alerts_from_message` と `anomaly_id`。読み手は 1 つのままにする。
- PROPOSAL_EVENT_COLUMNS と tables.tf を突き合わせるテストの作り方（tests/test_analytics.py:307-311）。これを alert_events にも使う。
- 閉域の例外の作り方: `perimeter_exempt_principals` と `${prefix}-kb` の前例。
- エンドポイントの足し方: `ops/up.sh` の `endpoints_for` と `add_endpoints`。
- 環境変数が空なら「未配備」を返す作り: `agent/evidence.py` の OPENSEARCH と PROMETHEUS の分。
- remote state から読むやり方: workflow の locals.tf:119-122。

## 実装ステップ（エンジニアセッションに頼む）

1. rules.py に `ALERT_EVENT_COLUMNS` と `alert_event()` を足し、単体テストを書く。
2. status_handler.py: 全部の通知の行を組んで先に Firehose に送り、そのあと Neptune に書く。Neptune が失敗したときだけ最後に例外を投げる。Firehose は fake boto3 でテストする:
   - 送る件数
   - FailedPutCount が出たら失敗した行だけを送り直し、3 回で止めてログに書く（例外は投げない）
   - device_id か kind が無い通知は WARNING を出し、行にしない
   - ALERT_STREAM が空なら送らない
   - Neptune が失敗しても Firehose には送る
   - 送る順番（Firehose が Neptune より先。Firehose が止まっていても、3 回の送信とログの ERROR を済ませてから Neptune に進む）
3. analytics の tables.tf と history.tf と outputs。base/core の perimeter と validation。
4. graph の sync.tf（alert_history の変数、環境変数、IAM）。up.sh（alert_history、catalog、エンドポイント）。
5. evidence.query_history、tools.json、プロンプト、gateway.tf と locals.tf。
6. docs。
7. `python3 tests/test_*.py` をすべて流す。

## 検証方法（期待出力つき）

- `python3 tests/test_analytics.py`:
  - S3 Tables のテーブルの集合が `["snmp_metrics", "proposal_events", "alert_events"]` になる。
  - alert_events の `(name, type)` の並びが ALERT_EVENT_COLUMNS と一致する。
  - エンドポイントの検査の期待値に、kinesis-firehose と athena が足される（13 本 → 15 本）。
- `python3 tests/test_sync.py`:
  - alert_history が false のとき、ALERT_STREAM が環境変数に無く、firehose の IAM も付かない。
  - true のときは `firehose:PutRecordBatch` が `${prefix}-alert-events` の ARN だけに付く。
- status_handler のテストで次を確かめる:
  - Grafana の firing と resolved を 1 通ずつ（starts_at が同じ）入れると、Firehose に 2 行が届く。event_id は `dc1-leaf-01#link_down#ethernet-1/1#grafana#firing#1790000000` と `…#resolved#1790000000`。
  - 同じ通知をもう 1 回入れると、同じ event_id で届く。
  - Neptune に頂点が無い機器の通知も 1 行届く。Neptune の結果は unregistered（main で Neptune Analytics に移ったあとの値）。
  - device_id が無い通知は、put_record_batch に渡らず、ログに WARNING が 1 行出る。
  - fake の neptunedata が例外を投げても put_record_batch は（その前に）呼ばれ、handler は RuntimeError を投げる。
  - fake の firehose が毎回例外を投げると、put_record_batch は 3 回呼ばれ、ログに ERROR が行の数だけ出て、handler は例外を投げずに返る。
  - fake の firehose が 1 回目に 2 行のうち 1 行を失敗で返すと、2 回目は失敗した 1 行だけを送る。
  - Records が 2 通（通知 5 件）なら、put_record_batch 1 回（5 行）が Neptune への最初の書き込みより前に呼ばれる。fake の firehose が毎回例外なら、3 回の送信と行ごとの ERROR がすべて Neptune への最初の書き込みより前に起きる。Neptune を先に書くように戻すと、このテストが落ちる（注入して確かめる）。
  - starts_at が epoch ミリ秒の通知が 3 件中 1 件あると、その 1 件は行にならず ALERT_DROPPED の WARNING が 1 回出て、ほかの 2 行は届き、3 件とも Neptune に書かれる。ALERT_STREAM が空なら WARNING も出ない。detail に孤立したサロゲートがある行も、ほかの行と同じ 1 回の put_record_batch で届き、Firehose が止まっていれば ERROR に UTF-8 で書ける（Round 4 で足した）。
- Neptune が遅い・応答しない・Firehose が止まっているときの時間（Round 3。偽物で測り、build.md に書く）:
  - Neptune が 1 回 9.9 秒で答える偽物で、行が Firehose に着くのは Neptune の最初の問い合わせより前で、受けてから 5 秒以内。
  - Neptune が応答しない（接続できない、接続して黙る）ときも、行は Neptune を呼ぶ前に着く。
  - Firehose が止まっているとき、3 回送り直して行の中身を ERROR に書き、そのあと Neptune に進む。Firehose に使った時間の上限を測り、60 秒のうち何秒が Neptune に残るかを書く。
- `python3 tests/test_app.py`:
  - query_history は環境変数が無ければ `rows == []` で、「未配備」の文言を返す。
  - fake の athena が渡す SQL に `row_number() OVER (PARTITION BY event_id` と `LIMIT 50` が入る。device_id は ExecutionParameters で渡り、SQL の文字列には現れない。
- AWS（PIPELINE=1 で analytics がある構成。ユーザーが up.sh を打つ）:
  1. `aws glue get-catalog --catalog-id s3tablescatalog` が成功する。
  2. lab で IF を 1 本落とす（docs の手順）。2 分以内に `aws firehose describe-delivery-stream` の `DeliveryStreamStatus=ACTIVE` を確かめ、Athena で `SELECT count(*) FROM "s3tablescatalog/<bucket>"."netops"."alert_events"` が 1 以上になる。
  3. starts_at と received_at が 1970 年ではなく、いまの時刻（UTC）として読める。
  4. `firehose-errors/alert_events/` にオブジェクトが無い。
  5. エージェントに「dc1-leaf-01 のアラートの履歴」と聞くと、query_history が firing の行を返す。

## 未確定事項とリスク

- 行は Neptune より先に送るので、Neptune の途中で Lambda が時間切れになっても履歴は残る（Firehose が止まっていたなら、行の中身の ERROR もその前に出ている）。残らないのは、行を送り終える前に落ちた場合だけ: メモリ不足や起動の失敗、または Firehose の応答が細切れに返り続けて読みの待ち（3 秒。1 回の読みごとの上限で、応答全体の上限ではない）が効かない場合（AWS のエンドポイントでは起きない見込みで、確かめていない）。「欠けた分はログから戻せる」はこの範囲。
- Neptune が遅いと、1 回の呼び出しの alert をすべて書く前に timeout することがある。1 件の alert が Neptune に問い合わせるのは 1〜5 回（登録済みの link は 2 回、未登録の IF は 5 回まで）なので、1 回の問い合わせが 10 秒近くかかると数件で 60 秒を超える（Round 3 の実測は design-log.md の Round 3）。履歴の行は届くが、status は Lambda の非同期のやり直し（2 回）まで遅れ、やり直しでも同じ所で切れると、後ろの alert の status は更新されない。どこまで書いたかは、ログの alert ごとの INFO で追う（timeout した呼び出しには `Task timed out` が出る）。

1. **Firehose が受け付ける timestamptz の書式を、文書で確かめきれていない。** ISO 8601 の UTC で送る。検証 3 で 1970 年や NULL になったら epoch ミリ秒に切り替える。
2. **Firehose → S3 Tables と Athena → S3 Tables が IAM だけで通るかは、AWS で試していない。** Lake Formation が IAM 任せのモードで、AllowFullTableExternalDataAccess を付けている前提。通らなければ `lakeformation:GetDataAccess` を足すか、Lake Formation で許可を出す。
3. **閉域の Deny が効くかもまだ確かめていない。** Firehose のロールを例外にしたのでテーブルバケットのポリシーは通るはず。tools Lambda の IAM 側の Deny（s3tables:*）は、Athena が呼び手に代わって出す呼び出し（aws:ViaAWSService）には効かない前提。外れたら Athena の分を例外にするか、tools Lambda の IAM 側で調整する。
4. **`s3tablescatalog` はアカウントで共有し、消さない。** ほかの OWNER がすでに別の設定（Lake Formation の管理など）で作っていたら、そのまま使うことになり、権限の前提が崩れる。up.sh は既存の catalog の設定を表示して、違えば警告する。
5. **down.sh の順番。** analytics（ストリームとテーブル）を消してから graph を消すまでのあいだ、Lambda の Firehose への送信は失敗し、行はログに残る。up.sh で graph が analytics より先にできるあいだも同じ。どちらも status の更新は止まらない。ログに ERROR が出る。
6. **starts_at の意味が Grafana と Splunk で違う。** 列はそのまま持つ。読む人とエージェントのために docs とツールの説明に書く。
7. **料金。** VPC エンドポイントが 2 本増える（各 1.4 セント/時 × AZ）。Firehose は取り込んだ GB あたりの課金で、PoC の量なら小さい。Athena はスキャン量の課金で、上限を付ける。
8. **Terraform の provider 6.64.0 での書き方。** `iceberg_configuration` の `catalog_arn` と、Athena の `managed_query_results_configuration` は、バイナリに文字列があることまでは確かめた。引数の形は実装時に `terraform validate` で確かめる。
9. **Neptune の問い合わせに、サーバ側の時間の上限（`queryTimeoutMilliseconds`）を付けていない。** クライアントが読みの 10 秒であきらめても、Neptune の側ではその問い合わせが走り続け、2 回目の試行や Lambda のやり直しの問い合わせと重なることがある。同じ通知の書き込みどうしなら値は同じだが、走り続けた問い合わせが、あとから届いた別の通知（同じ要素の resolved など）の書き込みより後に効くと、新しい status を古い値に戻す（Round 4 のセルフレビューで足した）。直すなら `agent/graph.py` の execute_query に渡す。このサイクルでは直さない（Round 3）。
10. **やり直しで、古い通知が新しい通知のあとに書かれることがある。** Neptune への書き込みが失敗した（または途中で timeout した）呼び出しは、Lambda の非同期のやり直しで数分後にもう一度走る。そのあいだに同じ要素の resolved が届いて UP にしていると、やり直した firing が DOWN に戻す。やり直しは呼び出しの通知を全部流し直す（書けていた通知も）ので、逆向き（やり直した古い resolved が、そのあいだに届いた firing の DOWN を UP に戻す）も起きる（Round 4 のセルフレビューで足した）。このサイクルで入った危険ではなく、前からある（このサイクルの前から、Neptune の失敗は例外のまま Lambda のやり直しに任せていた）。直すなら別のサイクル（通知の starts_at を見て、古い通知で新しい status を上書きしない、など）。
11. **Neptune への書き込みは、Firehose に送り終えるまで待つ（Round 4）。** ふつうは put_record_batch 1 回のぶん（AWS では測っていない）だが、Firehose が止まっているときや送り直しになったときは、1 回の呼び出しごとに長くて 22 秒遅れる。呼び出しごとに Firehose にかかった時間が違う（片方だけ送り直しになった、など）と、同じ要素の 2 つの通知を Neptune に書く順番が、届いた順番と入れ替わることがあり、古い値が残る。リスク 10 と同じ種類の危険で、入れ替わる窓が最大 22 秒広がる。直し方もリスク 10 と同じ（Round 4 のセルフレビューで足した）。
12. **starts_at の無い通知は、別の回の発生でも 1 行にまとまる。** starts_at が無いか読めない（無限大を含む）と、event_id の末尾が `#0`、列の starts_at が空になる。同じ異常・送り手・状態の別の発生も event_id が同じになり、読むとき（`row_number() OVER (PARTITION BY event_id`）に 1 行になる。いまの送り手（Grafana と Splunk）は starts_at を付けるので起きない見込み。直すなら別のサイクル（starts_at が無いときは received_at を使う、など。ただしやり直しの重複が 1 行にまとまらなくなる）（cold review の S-4 で足した）。
13. **IAM の権限が広い。まず通すことを優先した。** Firehose のロールの S3 Tables（`history.tf` の `${table_bucket_arn}/table/*`）と Glue（同じファイルの `database/*` と `table/*/*`）、workflow の tools Lambda の `HistoryCatalog`（`terraform/workflow/gateway.tf` の glue の `database/*` と `table/*/*`）は、alert_events とその名前空間だけに絞れる。最初の up.sh で通ったのを確かめてから、実際に呼ばれた ARN（CloudTrail）に合わせて絞る。あわせて、Firehose のロールの trust に `aws:SourceArn`（ストリームの ARN）を足す。リスク 2 の `lakeformation:GetDataAccess` も、要ると分かったときだけ足す（cold review の S-3 と Nit で足した）。

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261004-cycle-001-alert-history-firehose-design.html -->
