# build — 001-alert-history-firehose

## Round 1

実装モデル: opus-5.5 / effort: high

worktree `.claude/worktrees/cycle-001-alert-history-firehose`（ブランチ `cycle-001-alert-history-firehose`、main `76dbcb6` から）。

### 実装した内容

design.md の実装ステップ 1〜7 をすべて実施。

### 変更ファイル一覧

| ファイル | 変更 |
| :--- | :--- |
| `workflow/rules.py` | `ALERT_EVENT_COLUMNS`、`iso_utc`、`alert_event()`、プロンプトの言い換え |
| `graph/status_handler.py` | Neptune と Firehose を両方試し最後に RuntimeError。`send_history`（500 件ずつ） |
| `terraform/pipeline/graph/variables.tf`、`sync.tf` | `alert_history`、`ALERT_STREAM`、`firehose:PutRecordBatch`（dynamic） |
| `terraform/pipeline/analytics/tables.tf` | `aws_s3tables_table.alert_events` |
| `terraform/pipeline/analytics/history.tf`（新規） | Firehose `<prefix>-alert-events`、ロール `<prefix>-alert-firehose`、ロググループ、Athena ワークグループ `<prefix>-history` |
| `terraform/pipeline/analytics/outputs.tf` | `alert_events_stream_name`、`alert_events_table_name`、`alert_events_table_arn`（セルフレビュー #1）、`athena_workgroup`、`athena_catalog` |
| `terraform/base/core/perimeter.tf`、`variables.tf` | `-alert-firehose` を例外に、validation に `kinesis-firehose` / `athena`。Deny の Action に `athena:*` / `firehose:*`（セルフレビュー #1 #2） |
| `cloudformation/lab-debug.yaml` | Deny の Action を perimeter.tf とそろえる（`athena:*` / `firehose:*`） |
| `terraform/pipeline/graph/locals.tf`、`sync.tf` | graph-status のロールに閉域の Deny（`status_perimeter`。セルフレビュー #2） |
| `docs/architecture/core.md` | エンドポイント（kinesis-firehose / athena）と IAM の Deny の行 |
| `ops/up.sh` | `ensure_s3tables_catalog`、`ANALYTICS_LEFT` / `analytics_on`、`endpoints_for`、`GRAPH_VARS` |
| `agent/evidence.py`、`tools/tools.json` | `query_history` を Athena で（ExecutionParameters、row_number で重複除去、LIMIT 50） |
| `agent/app.py` | プロンプトの言い換え |
| `terraform/workflow/locals.tf`、`gateway.tf` | analytics の出力を try で読み、tools Lambda に環境変数 4 つと IAM 4 文（dynamic。テーブルの読み取りは alert_events の ARN だけ） |
| `tests/test_app.py`、`test_sync.py`、`test_analytics.py`、`test_workflow.py` | 上の検査 |
| `docs/data-stores.md`、`pipeline.md`、`development.md`、`deploy.md`、`architecture/pipeline.md`、`architecture/agent.md` | alert_events / Firehose / Athena の記述 |

### 設計からの逸脱と残るリスク

- `agent/app.py` の言い換えは :83（設計は場所を書いていない）。
- starts_at の無い通知は starts_at を null、event_id の末尾を `0` にする（設計に無いケース）。
- `put_record_batch` は 500 件ずつに分けて呼ぶ（設計は「1 回」。Splunk は 1 通 50 件だが Records が複数あり得るため）。
- test_sync の期待値を OSError → RuntimeError に変えた。元の期待値は「Neptune の例外をそのまま上げる」で、設計の決定 6（両方試して最後に RuntimeError）と両立しないため。
- up.sh: state に analytics が残っている回も `kinesis-firehose` / `athena` を数える（`ANALYTICS_LEFT`）。残ったルートのループに pipeline/graph を analytics の後に足した。
- `ensure_s3tables_catalog` は 7-4（analytics の apply）の冒頭で呼ぶ。get-catalog が EntityNotFound 以外で失敗したら die。既存の catalog の設定が違えば警告。
- `GRAPH_VARS` は bash 3.2 で空配列を展開できる書き方。
- Firehose の depends_on はロールのポリシーだけ。Athena のスキャン上限は 1 GiB。
- device_id は `^[A-Za-z0-9._:/#?-]{1,128}$` で絞る（設計は「ExecutionParameters で渡す」だけ。Athena は文字列パラメータを `'…'` の式として読むため、引用符を通さない）。
- tools Lambda の `HISTORY_NAMESPACE` はワークグループが空なら空。IAM の HistoryCatalog は `database/*` と `table/*/*`（読むだけ）。
- `?` はサブクエリの WHERE に置いた。Athena の文書（querying-with-prepared-statements）は、パラメータを DML のどこにでも置けると書いている。サブクエリの中で通るかは AWS で試していない。
- JST は Python 側で足す。MaxResults=51（ヘッダ行 + 50）。`ATHENA_*` はモジュールの定数として import 時に読む（OPENSEARCH / PROMETHEUS と同じ）。
- test_workflow に「HISTORY_COLUMNS と ALERT_EVENT_COLUMNS の一致」と gateway の検査を足した（設計の検証方法に無い追加）。
- tools Lambda のロールの description を変えた。
- docs/development.md のテスト項目数は今回触った 4 本だけ直した。test_stream（44 → 実 60）と test_lab_debug（66 → 実 75）はこのサイクルの前から古い。
- docs/deploy.md の SKIP_ANALYTICS の金額（-$0.57/h）は、増えたエンドポイントを含めた見積もり。
- テストの実行には boto3（`uv sync --group web`）が要る。サイクル前から同じ（toolkit.py が boto3 を先頭で import）。

### 検証（最後の編集のあとに取り直した出力）

`bash ops/check.sh` の全文は [build-r1-check.log](build-r1-check.log)（1250 行、2026-10-04 17:42、exit 0）。以下はその抜粋で、行は書き換えていない。

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
通過 100 / 失敗 0     (test_app)
通過 59 / 失敗 0      (test_graph)
通過 60 / 失敗 0      (test_stream)
通過 72 / 失敗 0      (test_sync)
通過 264 / 失敗 0     (test_analytics)
通過 270 / 失敗 0     (test_workflow)
通過 89 / 失敗 0      (test_alerts)
通過 7 / 失敗 0       (test_kb_index)
通過 75 / 失敗 0      (test_lab_debug)
58 項目すべて通過     (test_nautobot)
すべて通過
```

（括弧のファイル名は貼るときに足した。ログ中の Traceback は、例外の経路を確かめるテストが意図して出しているもの。）

変更前（main `76dbcb6`）の項目数: app 79、graph 59、stream 60、sync 59、analytics 250、workflow 262、alerts 89、kb_index 7、lab_debug 75、nautobot 58。

#### design.md の検証方法と出力

1. `python3 tests/test_analytics.py` — テーブルの集合、alert_events の列、エンドポイント 13 → 15 本
   ```
   ok 異常の履歴のテーブル anomaly_events は無い（S3 Tables のテーブルは snmp_metrics と proposal_events と alert_events だけ）
   ok alert_events をいつも作り（count 無し）、列はどれも required = false
   ok alert_events の列と順は workflow/rules.py の ALERT_EVENT_COLUMNS と同じ
   ok 全部なら 15 本で重複しない（ecr / logs / s3tables / bedrock-agentcore は 1 本ずつ）、ENDPOINTS_MULTI_AZ=1 で 2 AZ。events は無く、アラートの送り手がいれば sns
   ok sns のエンドポイントは Grafana のアラートか Splunk があるときだけ（どちらも無ければ 14 本。Splunk だけでも足す）
   ok kinesis-firehose（graph の履歴）と athena（workflow の query_history）は analytics を作る回か、analytics が state に残っているときだけ
   通過 264 / 失敗 0
   ```
2. `python3 tests/test_sync.py` — alert_history の false / true
   ```
   ok variables.tf の alert_history は bool で既定 false（analytics を作らない回は送らない）
   ok alert_history が false なら ALERT_STREAM は空、true なら <接頭辞>-alert-events（analytics の Firehose と同じ名前）
   ok firehose の権限は alert_history が true のときだけで、PutRecordBatch を <接頭辞>-alert-events の ARN だけに
   ok up.sh は analytics を作る回（SKIP_ANALYTICS が空）だけ graph に -var alert_history=true を渡す
   通過 72 / 失敗 0
   ```
   設計は「false のとき ALERT_STREAM が環境変数に無い」。実装は `ALERT_STREAM = ""`（空なら送らない）で、テストもその形を見ている。
3. status_handler（test_sync の中）— 2 行・event_id・同じ通知の再送・機器の無い通知・Neptune の失敗
   ```
   ok ALERT_STREAM が空なら Firehose に送らない（alert_history=false の配備）
   ok Grafana の firing と resolved（starts_at は同じ）は 1 回の put_record_batch に 2 行、event_id は status で分かれる
   ok 行の列は rules.ALERT_EVENT_COLUMNS と同じ、時刻は ISO 8601 の UTC（starts_at は通知のまま、received_at は受けた時刻）
   ok 同じ通知をもう一度受けても Lambda では落とさず、同じ event_id の行をまた送る（重複は読む側が event_id で落とす）
   ok Neptune で無視した通知（機器の無いもの）も履歴には 1 行送る
   ok Neptune に書けなくても Firehose には送り、最後に RuntimeError（片方が落ちても両方を 1 回ずつ試す）
   ok FailedPutCount が 0 でなければ RuntimeError（Neptune は書いたうえで）
   ok Firehose の呼び出しが例外でも Neptune は書き、最後に RuntimeError
   ok 1 回の呼び出しで 500 件を超えたら put_record_batch を 500 件ずつに分ける（API の上限）
   ```
   event_id の値（`dc1-leaf-01#link_down#ethernet-1/1#grafana#firing#1790000000` / `…#resolved#1790000000`）はテストの中で文字列一致で見ている。
4. `python3 tests/test_app.py` — 未配備、SQL、ExecutionParameters
   ```
   ok query_history は環境変数が無ければ rows == [] で「未配備」を返し、Athena を呼ばない
   ok query_history の SQL は event_id で重複を落とし（row_number() OVER (PARTITION BY event_id）、新しい順に LIMIT 50
   ok query_history は "<catalog>"."<namespace>"."<table>" を読み、10 列を ALERT_EVENT_COLUMNS の順で選ぶ
   ok device_id は ExecutionParameters（'…' で囲んだ文字列の式）で渡り、SQL の文字列には現れない
   ok 引用符の入った device_id は Athena に投げずにエラー
   ok 時間内に終わらなければ stop_query_execution で止めてエラー
   通過 100 / 失敗 0
   ```
5. AWS 1〜5（catalog、Firehose → Athena の件数、時刻の読み、firehose-errors、エージェントの回答）— **未実行**（ユーザーが `ops/up.sh` を打つ）。

### セルフレビュー

- 自分: opus-5.5 / effort high（スキルの既定は xhigh。セッションの effort を変えられなかった）
- 反対弁護人: Agent（general-purpose、model opus）1 回。design.md と build.md のパス・変更ファイル・方針・不安な箇所・それまでの結論を渡し、読み取り専用で頼んだ。いまの `git status --porcelain -uall` は自分の変更（M 27 本と ?? 4 本）だけ。
- 入力は design.md と worktree のコード。差分は `git diff 76dbcb6`（main は 76dbcb6 → 6655fe2 に進んでいる。下の「残り」）。

#### 指摘と片付け

| # | 分類 | 観点 | 場所 | 破綻シナリオ | 確かめたもの | 片付け |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| 1 | Should | security | `terraform/workflow/gateway.tf:116-161`、`terraform/base/core/perimeter.tf:32-48` | tools Lambda の認証情報が VPC の外に漏れると、Athena 経由（Athena が代わりに出す呼び出しは aws:ViaAWSService=true で s3tables:* の Deny に当たらない）でテーブルバケットの全テーブル（proposal_events / snmp_metrics も）を読める | SAR（list_s3tables.html）で GetTable / GetTableData / GetTableMetadataLocation の資源が Table* と確認。IAM の条件キーの文書で aws:SourceVpc はエンドポイント経由のときだけ入ると確認 | 直した: Deny に `athena:*`、テーブルの読み取りを `alert_events` の ARN だけに（analytics の出力 `alert_events_table_arn`）。注入 M17 M19 M22 M23 M24 M25 M26 でテストが落ちる（下） |
| 2 | Should | security | `terraform/pipeline/graph/sync.tf:58`（ロール）、`:105`（直した attachment） | graph-status のロールは `firehose:PutRecordBatch` を持つのに閉域の Deny が無く、漏れた認証情報で VPC の外から履歴の行を偽造できる | sync.tf の attachment が status ロールに無いことを読んだ | 直した: Deny に `firehose:*`、status ロールに `status_perimeter`。注入 M18 M20 M21 で落ちる |
| 3 | Should | correctness | `graph/status_handler.py:106-112`（決定 6） | Firehose だけが落ちても例外 → 非同期のやり直しで Neptune の apply がもう一度走る。やり直しの前に resolved が届いていると、解消済みの回線が DOWN に戻る（docstring :19-20 の「繰り返して害が無く」は誤り） | `da_probe.py`: 「firing（Firehose 失敗）→ resolved → firing のやり直し」で Neptune = DOWN | **差し戻し**（設計の決定 6。合意済みの決定を変えるのでユーザーの判断待ち） |
| 4 | Should | correctness | `agent/evidence.py:181`、`TOOL_SPECS`、`tools/tools.json:174` | 説明文は「送り直しも 1 行ずつ」だったが、SQL は event_id で最初の 1 行にまとめる。エージェントが件数を誤読する | SQL（history_sql）と説明文を突き合わせた | 直した: 説明文を「同じ通知の送り直しは最初に届いた 1 件にまとめる」に |
| 5 | Should | runtime | `agent/evidence.py` の run_tool（既存） | Gateway の失敗が「未配備」の文言に化ける | 読んだだけ。サイクル前からの経路 | 最終報告（範囲外） |
| 6 | Should | runtime | `ops/up.sh:680`（graph）と `:937`（analytics） | 既に analytics（Grafana / Splunk）が動いている環境の更新で、graph が先に `ALERT_STREAM` を持つ。ストリームができるまでの数分、全通知が ResourceNotFound → 例外 → #3 のやり直し | up.sh の apply の順を grep（graph :680 が analytics :937 より前）。初回配備は送り手が analytics にいるので窓が無い | **差し戻し**（設計の「graph は analytics より前に apply してよい」）。#3 と一緒に決める |
| 7 | Should | 運用 | `terraform/pipeline/analytics/history.tf` | `firehose-errors/` に落ちても誰も気づかない（アラームが無い） | 読んだだけ | 最終報告（設計に無い） |
| 8 | Nit | correctness | `workflow/rules.py:305-308` | starts_at の無い通知は event_id の末尾が `0` で、時刻の違う別の通知が query_history で 1 行にまとまる | `da_probe.py`: 1 時間あけた 2 通が `d#trap#x#splunk#firing#0` で同じ | 最終報告。Grafana と Splunk のテンプレートはどちらも starts_at を必ず入れる（design.md:40-45） |
| 9 | Should | 設計整合性 | `workflow/rules.py` の alerts_from_message（既存）、決定 2 | device_id か kind が空の通知は共有の読み手が落とすので、決定 2「Neptune で無視した通知（device_id が無いなど）も記録する」は `?` の分しか満たさない | `da_probe.py`: device_id 無し → 0 件、kind 無し → 0 件、device_id `?` → 1 件 | **差し戻し**（決定 2 と「読み手は 1 つのまま」がぶつかる） |
| 10 | Nit | 設計整合性 | `ops/up.sh` の GRAPH_VARS | SKIP_ANALYTICS=1 で analytics が state に残っている回は alert_history=false になり、履歴が止まる（エンドポイントも足さないので食い違いは無い） | `ep_probe.py`: 「graph を作る / analytics は残っている」で履歴のエンドポイント なし / GRAPH_VARS 空 | 最終報告（設計どおり「analytics を作る回だけ」） |
| 11 | Nit | security | `history.tf` の trust | Firehose の trust に aws:SourceArn が無い（aws:SourceAccount だけ） | 読んだだけ | 最終報告 |
| 12 | Nit | 記録 | build.md の逸脱の欄 | 「Athena のパラメータは WHERE 句だけ」は文書と違う | Athena の文書（querying-with-prepared-statements） | 直した |
| 13 | Should | missing tests | `tests/test_app.py` | 引用符 1 文字だけの device_id を縛るテストが無かった | 注入 M12 | 直した（下で M12 が落ちる） |
| 14 | Should | missing tests | `tests/test_analytics.py`（ensure_s3tables_catalog） | catalog を作る分岐にテストが無かった | 注入 M13〜M16 | 直した（5 項目） |
| 15 | Should | runtime | `agent/evidence.py:150` | `hours=inf` で OverflowError（例外で落ち、エラーの dict にならない） | `inf_probe.py`: query_history / search_logs / query_metrics の 3 つとも OverflowError、`'abc'` と `None` はエラーの dict | 最終報告（原因は既存の run_tool が OverflowError を拾わないこと。3 つのツールに共通で範囲外） |
| 16 | Nit | 保守性 | `ops/up.sh` の残ったルートのループ | graph も analytics も残っている回は、graph を作らなくても kinesis-firehose を足す | `ep_probe.py`: 「graph も analytics も残っている」で kinesis-firehose | 据え置き（残った graph が alert_history=true なら要る） |
| 17 | Nit | 保守性 | `history.tf:119`、`tests/test_sync.py:335` の項目名、`ops/up.sh:434` のコメント、get-catalog の呼び出し 3 回 | 読み物の重複・古い言い回し | 読んだだけ | 直さない |

注入（反対弁護人 #1 #2 の直しに付けた検査。`mutate2.py`。毎回元に戻した）:
```
M17 perimeter から athena:* を外す: test_analytics exit 1 / AssertionError: perimeter.tf: athena:* と firehose:* も IAM 側で拒む（Athena が代わりに読む S3 Tables は s3tables:* の Deny で止まらない。履歴の行を VPC の外から書かせない）
M18 perimeter から firehose:* を外す: test_analytics exit 1 / AssertionError: perimeter.tf: athena:* と firehose:* も IAM 側で拒む（Athena が代わりに読む S3 Tables は s3tables:* の Deny で止まらない。履歴の行を VPC の外から書かせない）
M19 CFn から athena:* を外す: test_lab_debug exit 1 / AssertionError: SSM のマネージドポリシーと、NetworkPerimeter のときだけ境界の Deny（perimeter.tf と同じ Action と条件。SourceVpc はこのスタックの VPC）
M20 graph-status に perimeter を付けない: test_sync exit 1 / AssertionError: graph-status のロールにも閉域の Deny を付ける（firehose を持つので、VPC の外から履歴の行を書かせない）。NETWORK_PERIMETER=0 か古い土台なら付けない
M21 graph の perimeter の local を消す: test_sync exit 1 / AssertionError: graph-status のロールにも閉域の Deny を付ける（firehose を持つので、VPC の外から履歴の行を書かせない）。NETWORK_PERIMETER=0 か古い土台なら付けない
M22 HistoryTable を /table/* に戻す: test_workflow exit 1 / AssertionError: query_history の IAM は analytics があるときだけ（dynamic）で、athena はワークグループ、s3tables のテーブルの読み取りは alert_events だけ（読むだけ）
M23 HistoryBucket にテーブルの読み取りを足す: test_workflow exit 1 / AssertionError: query_history の IAM は analytics があるときだけ（dynamic）で、athena はワークグループ、s3tables のテーブルの読み取りは alert_events だけ（読むだけ）
M24 analytics の alert_events_table_arn を消す: test_analytics exit 1 / AssertionError: output alert_events_table_arn がある
M25 workflow の locals で arn を読まない: test_workflow exit 1 / AssertionError: tools Lambda に ATHENA_WORKGROUP / ATHENA_CATALOG / HISTORY_NAMESPACE / ALERT_EVENTS_TABLE を渡す（値は analytics の出力）
M26 HistoryTable を athena_workgroup で絞る: test_workflow exit 1 / AssertionError: query_history の IAM は analytics があるときだけ（dynamic）で、athena はワークグループ、s3tables のテーブルの読み取りは alert_events だけ（読むだけ）
git status 行数（注入前と同じはず）: 31
```

注入（実装時の検査。`mutate.py`。上の直しのあとに取り直した）:
```
M1 Neptune が落ちたら Firehose を飛ばす: test_sync exit 1 / AssertionError: Neptune に書けなくても Firehose には送り、最後に RuntimeError（片方が落ちても両方を 1 回ずつ試す）
M2 event_id から status を外す: test_sync exit 1 / AssertionError: Grafana の firing と resolved（starts_at は同じ）は 1 回の put_record_batch に 2 行、event_id は status で分かれる
M3 device_id を SQL に埋め込む: test_app exit 1 / AssertionError: device_id は ExecutionParameters（'…' で囲んだ文字列の式）で渡り、SQL の文字列には現れない
M4 SKIP_ANALYTICS でも alert_history=true: test_sync exit 1 / AssertionError: up.sh は analytics を作る回（SKIP_ANALYTICS が空）だけ graph に -var alert_history=true を渡す
M5 FailedPutCount を見ない: test_sync exit 1 / AssertionError: FailedPutCount が 0 でなければ RuntimeError（Neptune は書いたうえで）
M6 BATCH を 1000 に: test_sync exit 1 / AssertionError: 1 回の呼び出しで 500 件を超えたら put_record_batch を 500 件ずつに分ける（API の上限）
M7 時刻から Z を外す: test_sync exit 1 / AssertionError: 行の列は rules.ALERT_EVENT_COLUMNS と同じ、時刻は ISO 8601 の UTC（starts_at は通知のまま、received_at は受けた時刻）
M8 rules の received_at を timestamp に: test_analytics exit 1 / AssertionError: alert_events の列と順は workflow/rules.py の ALERT_EVENT_COLUMNS と同じ
M9 perimeter の -alert-firehose の例外を外す: test_analytics exit 1 / AssertionError: perimeter.tf: Firehose のロール <接頭辞>-alert-firehose を資源側の Deny の例外に入れる（-kb と同じ。サービスが VPC の外から書く）
M10 重複除去の row_number を外す: test_app exit 1 / AssertionError: query_history の SQL は event_id で重複を落とし（row_number() OVER (PARTITION BY event_id）、新しい順に LIMIT 50
M11 時間切れで stop しない: test_app exit 1 / AssertionError: 時間内に終わらなければ stop_query_execution で止めてエラー
M12 引用符の device_id を通す: test_app exit 1 / AssertionError: 引用符の入った device_id は Athena に投げずにエラー（引用符 1 文字だけでも）
M13 get-catalog のどんな失敗でも作る: test_analytics exit 1 / AssertionError: up.sh: get-catalog がそれ以外のエラー（権限など）なら作らずに止まる
M14 アカウントを差し込まない: test_analytics exit 1 / AssertionError: up.sh: s3tablescatalog が無ければ（EntityNotFoundException）create-catalog を 1 回打ち、FederatedCatalog はこのリージョンとアカウントの bucket/*、既定の権限は IAM_ALLOWED_PRINCIPALS
M15 違う設定の catalog で警告しない: test_analytics exit 1 / AssertionError: up.sh: 設定の違う s3tablescatalog があれば作り直さず、警告だけ出して先へ進む
M16 catalog を apply の後に呼ぶ: test_analytics exit 1 / ValueError: substring not found
git status 行数（注入前と同じはず）: 31
```
（31 は build-r1-selfreview-check.log を足したあとの数で、注入の前も 31。M16 は検査の前の切り出しで落ちる。）

`da_probe.py`（#3 #8 #9）:
```
#3 Firehose だけ落ちる → Lambda の非同期のやり直し
  1 回目 firing: RuntimeError firehose: RuntimeError: ServiceUnavailableException / Neptune = DOWN
  その後に resolved が届く: Neptune = UP
  firing のやり直し（Firehose は直った）: Neptune = DOWN ← 解消済みなのに DOWN
#8 starts_at の無い通知
  1 時間あけた 2 通の event_id: d#trap#x#splunk#firing#0 / d#trap#x#splunk#firing#0 同じ = True
#9 device_id / kind の無い通知
  {"status": "firing", "kind": "link_down", "target": "e1"} → alerts_from_message 0 件
  {"status": "firing", "device_id": "d", "target": "e1"} → alerts_from_message 0 件
  {"status": "firing", "device_id": "?", "kind": "link_down", "target": "e1"} → alerts_from_message 1 件
```

#### 問題なしとした観点

- ACCOUNT_ID の順: `ensure_s3tables_catalog`（up.sh:920）より前の :401 で入る（grep）。
- 送り直しで event_id が変わらない: test_sync の「同じ通知をもう一度受けても…同じ event_id」と注入 M2。
- 閉域の Deny の Action を増やして止まる呼び手が無い: `git grep -n -i -e 'client("athena")' -e 'client("firehose")' -e 'athena:' -e 'firehose:' -- ':!docs' ':!tests'` で、呼ぶのは `agent/evidence.py:155`（tools Lambda）と `graph/status_handler.py:68`（graph-status）だけ。どちらもエンドポイント（athena / kinesis-firehose）を up.sh が足す。Firehose 自身のロール（history.tf:23）には IAM 側の Deny を付けていない（history.tf:8-9 の grep）。
- 初回の配備: アラートの送り手（Grafana / Splunk）は analytics にいるので、analytics の apply より前に通知は来ない（#6 は更新のときだけ）。
- workflow がエンドポイントを数える: `ep_probe.py` の「workflow だけ残っている」で athena が入る。

#### 修正後の検証

`bash ops/check.sh` の全文は [build-r1-selfreview-check.log](build-r1-selfreview-check.log)（1259 行、exit 0）。抜粋（行は書き換えていない）:
```
== 1. terraform fmt -check -recursive terraform
差分なし
== 2. 9 つのルートの validate
terraform/base/core  OK
terraform/pipeline/analytics  OK
terraform/pipeline/graph  OK
terraform/workflow  OK
== 3. ops スクリプトの構文
構文エラーなし
通過 100 / 失敗 0     (test_app)
通過 73 / 失敗 0      (test_sync)
通過 271 / 失敗 0     (test_analytics)
通過 270 / 失敗 0     (test_workflow)
通過 75 / 失敗 0      (test_lab_debug)
すべて通過
exit 0
```
（括弧のファイル名は貼るときに足した。省いた行は全文のログにある。）

#### ジンテーゼ（反対弁護人のあと）

- 結論の変わったところ: 「閉域の Deny は s3tables:* で足りる」→「Athena と Firehose を経由する読み書きは別に止める」。「Neptune の書き込みは繰り返して害が無い」→「やり直しの合間に後の通知が来ると古い値に戻る」。
- 部分的な真実: #1 #2 の直しは、Athena の呼び出しが athena のエンドポイントを通り aws:SourceVpc が付くこと、Athena が代わりに出す呼び出しに aws:ViaAWSService=true が付くことを前提にしている。どちらも AWS では未確認。外れると query_history は「Athena を呼べない」で止まる（読めない側に倒れる）。
- 残り: #3 #6 #9 は設計の決定 2 と 6 に原因があるので差し戻す。決定 6 はユーザーと合意したもので、変えるかどうかはユーザーが決める。main は 6655fe2 に進み、status_handler.py / up.sh / perimeter.tf / gateway.tf / tests など 20 本以上が重なる。cycle 003（修復案を S3 Tables と Athena で読む）とも Athena の作りが重なる。

## Round 2

実装モデル: opus-5.5 / effort: high

worktree とブランチは Round 1 と同じ。main `9823ea6`（design.md の決定 2 と 6 の直し）を先に merge し、そのうえで直した。依頼は「直してから merge」だったが、main の status_handler.py と test_sync.py が大きく変わっていたので順を入れ替えた。merge のあとの中身は同じになる。merge と Round 2 は 1 つの commit にした。

### 実装した内容

design.md（`9823ea6`）の決定 2 と 6、「書く側」の :56-62、実装ステップ 2、検証方法の status_handler の 7 項目。

### 変更ファイル一覧

| ファイル | 変更 |
| :--- | :--- |
| `graph/status_handler.py` | `send_history` は 500 件ずつ送り、届かなかった行だけを合わせて 3 回まで送り直す（0.2 秒と 0.4 秒待つ）。残った行は 1 行ずつ `ALERT_EVENT_LOST <行の JSON>` を ERROR で書き、例外にしない。`_put` は FailedPutCount なら ErrorCode の付いた行を返し、RequestResponses の数が合わないときと例外のときは全部を返す。`FIREHOSE_CONFIG`（botocore の再試行なし、接続 2 秒、読み 3 秒）のクライアントは専用の `_cache` に置く。`handler` は `ALERT_DROPPED` の WARNING に件数を出し、Neptune の失敗のときだけ RuntimeError |
| `workflow/rules.py` | `_payload`（alerts_from_message の封筒の開け方を切り出した）、`alert_count`（alerts の要素の数） |
| `tests/test_sync.py` | 検証方法の 7 項目に加え、送り直しの境界（RequestResponses、500 件、3 回目まで、ERROR の書式）、形の合わない要素、読めないメッセージ、FIREHOSE_CONFIG を検査。73 → 83 項目 |
| `tests/test_workflow.py` | alert_count |
| `docs/pipeline.md`、`data-stores.md`、`troubleshooting.md`、`development.md` | 決定 6 の動き、`ALERT_EVENT_LOST` と `ALERT_DROPPED` の探し方、Neptune が応答しないときは何も残らないこと、テストの項目数 |
| merge で両側が変えた 22 本 | `agent/app.py`、`docs/architecture/{agent,core,pipeline}.md`、`docs/{data-stores,deploy,development,pipeline}.md`、`graph/status_handler.py`、`ops/up.sh`、`terraform/base/core/{perimeter,variables}.tf`、`terraform/pipeline/graph/{locals,sync,variables}.tf`、`terraform/workflow/{gateway,locals}.tf`、`tests/test_{analytics,app,sync,workflow}.py`、`tools/tools.json` |

### 設計からの逸脱と残るリスク

- 検証方法の「fake の neptunedata」は `graph.set_status` を例外にする偽物で代えた。main は Neptune Analytics（neptune-graph）に移っていて、neptunedata は呼ばない。
- 検証方法の「Neptune の結果は ignored」は、main では `unregistered` が返る（未登録の頂点を作る）。テストは「未登録の機器の通知も履歴には 1 行送る」で見ている（設計の文言の直しは設計側の仕事）。
- `FIREHOSE_CONFIG` と専用の `_cache` は設計に無い。botocore の既定（再試行あり、接続の待ち 60 秒）だと、1 回目の呼び出しだけで Lambda の 30 秒を使い切り、ERROR を書く前に落ちる（下の retry_probe）。
- 送り直しは 500 件の塊ごとに 3 回まで（設計の「1 回送る（500 件以下）」は Round 1 から 500 件ずつに分けている）。
- 全部捨てた本文（`alerts` の要素はあるが 1 件も読めない）は `ALERT_DROPPED` だけを出し、「読めないメッセージ」は出さない（WARNING は 1 回）。
- `{"alerts": []}` は「読めないメッセージ」になる（捨てた通知は 0 件）。
- 決定 6 の「Firehose に送れなくてもログから戻せる」は、Neptune がエラーを返すときだけ成り立つ。Neptune が応答しないと、Lambda の timeout が先に来て行もログも残らない（セルフレビュー #1。差し戻し）。

### 検証（最後の編集のあとに取り直した出力）

`bash ops/check.sh` の全文は [build-r2-check.log](build-r2-check.log)（1259 行、exit 0）。以下はその抜粋で、行は書き換えていない。

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
通過 104 / 失敗 0     (test_app)
通過 73 / 失敗 0      (test_graph)
通過 60 / 失敗 0      (test_stream)
通過 83 / 失敗 0      (test_sync)
通過 271 / 失敗 0     (test_analytics)
通過 272 / 失敗 0     (test_workflow)
通過 89 / 失敗 0      (test_alerts)
通過 7 / 失敗 0       (test_kb_index)
通過 75 / 失敗 0      (test_lab_debug)
58 項目すべて通過     (test_nautobot)
すべて通過
```

（括弧のファイル名は貼るときに足した。ログ中の Traceback は、例外の経路を確かめるテストが意図して出しているもの。）

merge した main（`9823ea6`）の項目数: app 83、graph 73、stream 60、sync 61、analytics 250、workflow 263、alerts 89、kb_index 7、lab_debug 75、nautobot 58（docs/development.md の main の版）。

#### design.md の検証方法と出力

1. `python3 tests/test_analytics.py`: テーブルの集合、alert_events の列、エンドポイントの本数
   ```
   ok 異常の履歴のテーブル anomaly_events は無い（S3 Tables のテーブルは snmp_metrics と proposal_events と alert_events だけ）
   ok alert_events をいつも作り（count 無し）、列はどれも required = false
   ok alert_events の列と順は workflow/rules.py の ALERT_EVENT_COLUMNS と同じ
   ok 全部なら 16 本で重複しない（ecr / logs / s3tables / bedrock-agentcore は 1 本ずつ。graph は Neptune Analytics の neptune-graph-data）、ENDPOINTS_MULTI_AZ=1 で 2 AZ。events は無く、アラートの送り手がいれば sns
   ok sns のエンドポイントは Grafana のアラートか Splunk があるときだけ（どちらも無ければ 15 本。Splunk だけでも足す）
   ok kinesis-firehose（graph の履歴）と athena（workflow の query_history）は analytics を作る回か、analytics が state に残っているときだけ
   通過 271 / 失敗 0
   ```
   設計の「13 → 15 本」は main `76dbcb6` の数え方。main `9823ea6` は全部で 14 本（どちらも無ければ 13 本）なので、2 本足して 16 本（15 本）。
2. `python3 tests/test_sync.py`: alert_history の false と true
   ```
   ok variables.tf の alert_history は bool で既定 false（analytics を作らない回は送らない）
   ok alert_history が false なら ALERT_STREAM は空、true なら <接頭辞>-alert-events（analytics の Firehose と同じ名前）
   ok firehose の権限は alert_history が true のときだけで、PutRecordBatch を <接頭辞>-alert-events の ARN だけに
   ok up.sh は analytics を作る回（SKIP_ANALYTICS が空）だけ graph に -var alert_history=true を渡す
   ok graph-status のロールにも閉域の Deny を付ける（firehose を持つので、VPC の外から履歴の行を書かせない）。NETWORK_PERIMETER=0 か古い土台なら付けない
   通過 83 / 失敗 0
   ```
3. status_handler（test_sync の中）: 設計の 7 項目は 387〜399 行、ほかは足した境界の検査
   ```
   ok Neptune に書けなければ最後に RuntimeError で落とす（Lambda の非同期の再試行に任せる。書き込みは繰り返して害が無い）
   ok ALERT_STREAM が空なら Firehose に送らない（alert_history=false の配備）
   ok Grafana の firing と resolved（starts_at は同じ）は 1 回の put_record_batch に 2 行、event_id は status で分かれる
   ok 行の列は rules.ALERT_EVENT_COLUMNS と同じ、時刻は ISO 8601 の UTC（starts_at は通知のまま、received_at は受けた時刻）
   ok 同じ通知をもう一度受けても Lambda では落とさず、同じ event_id の行をまた送る（重複は読む側が event_id で落とす）
   ok Neptune で無視した通知（機器の無いもの）も履歴には 1 行送る
   ok 送り切れたら待たず、ERROR も出さない
   ok Neptune に書けなくても Firehose には送り（put_record_batch は呼ばれる）、Neptune の失敗だけで最後に RuntimeError
   ok 1 回目に 2 行中 1 行（2 行目）が届かなければ、2 回目はその 1 行だけを送る。0.2 秒待ち、例外にも ERROR にもしない
   ok Firehose が毎回例外なら put_record_batch は 3 回、待ちは 0.2 秒と 0.4 秒、行ごとに ERROR を 1 つ出して例外にしない（Neptune は書く）
   ok ERROR は ALERT_EVENT_LOST のあとに行の JSON そのまま（Logs Insights で拾って戻せる）
   ok 3 回目まで届かなかった行だけを ERROR にする（届いた行は出さない）
   ok RequestResponses の数が合わなければどの行が落ちたか分からないので、全部を送り直す
   ok 1 回の呼び出しで 500 件を超えたら put_record_batch を 500 件ずつに分ける（API の上限）
   ok device_id の無いアラートは put_record_batch に送らず、WARNING を 1 回（ALERT_DROPPED と件数）
   ok 形の合わない要素（kind が無い・status が pending・dict でない）は数えて WARNING 1 回に、正しい 1 件だけ行にする
   ok 未登録の機器・IF の異常は WARNING で UNREGISTERED をログに出す
   ok 未登録の機器の通知も履歴には 1 行送る
   ok 登録済みなら INFO（どの送り手のどのアラートかをログに残す）
   ok 読めないメッセージは WARNING でログに出す（ALERT_DROPPED ではない）
   ok Firehose へは FIREHOSE_CONFIG で 1 つだけ作ったクライアントで送り、botocore の再試行を切る（1 回）。3 回の接続と読みの待ち + 送り直しの待ちが Lambda の timeout（30 秒）に収まる
   ```
   alert_count（test_workflow の中）:
   ```
   ok alert_count は alerts の要素を形にかかわらず数える（alerts_from_message との差が捨てた件数。封筒も開け、読めない本文は 0）
   通過 272 / 失敗 0
   ```
4. `python3 tests/test_app.py`: 未配備、SQL、ExecutionParameters
   ```
   ok query_history は環境変数が無ければ rows == [] で「未配備」を返し、Athena を呼ばない
   ok query_history の SQL は event_id で重複を落とし（row_number() OVER (PARTITION BY event_id）、新しい順に LIMIT 50
   ok query_history は "<catalog>"."<namespace>"."<table>" を読み、10 列を ALERT_EVENT_COLUMNS の順で選ぶ
   ok device_id は ExecutionParameters（'…' で囲んだ文字列の式）で渡り、SQL の文字列には現れない
   通過 104 / 失敗 0
   ```
5. AWS 1〜5（catalog、Firehose → Athena の件数、時刻の読み、firehose-errors、エージェントの回答）: **未実行**（ユーザーが `ops/up.sh` を打つ）。

### セルフレビュー

- 自分: opus-5.5 / effort high（スキルの既定は xhigh。セッションの effort を変えられなかった）
- 反対弁護人: Agent（general-purpose、model opus）1 回。design.md と build.md のパス、変更ファイル、方針、不安な箇所（時間の予算、テストが送る経路を縛っているか、merge の解き方）、それまでの結論を渡し、読み取り専用で頼んだ。返ってきたあとの `git status --porcelain -uall` は自分の変更だけで、増えたファイルは無かった。
- 入力は design.md（`9823ea6`）と worktree のコード。

#### 指摘と片付け

| # | 分類 | 観点 | 場所 | 破綻シナリオ | 確かめたもの | 片付け |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| 1 | Must | data loss / 設計整合性 | `graph/status_handler.py:132-150`、`agent/graph.py:58-66`、`terraform/pipeline/graph/sync.tf:125` | Neptune が応答しない（閉域のエンドポイントや SG の誤り、Neptune の停止）と、graph.py のクライアント（接続 10 秒、legacy の max_attempts=2 で 3 回）が 1 件目で 30 秒を超える。Neptune を先に書くので send_history に着く前に Lambda が timeout し、行も `ALERT_EVENT_LOST` も残らない。非同期のやり直し 2 回も同じ。決定 6 の「欠けた分はログから戻せる」が成り立たない | `hang_probe.py`（下）: Neptune への送信 3 回、handler 全体 31.5 秒、put_record_batch に着いたのは 31.5 秒後 | **差し戻し**。原因は「Neptune を先に書く」順と、Neptune のクライアントの待ち時間が設計の時間の予算に入っていないこと。docs（pipeline.md、data-stores.md、troubleshooting.md、docstring）は「Neptune がエラーを返すときは両方を試す。応答しないと何も残らない（`Task timed out` だけ）」に直した |
| 2 | Should | data loss | 同上 | Neptune が遅い（1 件 数秒）× 1 通 50 件（Splunk）なら、Neptune が落ちていなくても 30 秒を超え、Firehose が止まっていればさらに 15 秒 | 計算だけ（実測していない） | **差し戻し**（#1 と同じ時間の予算の問題） |
| 3 | Should | missing tests | `tests/test_sync.py`（FIREHOSE_CONFIG の検査）、`graph/status_handler.py:76-80` | 送る経路を `toolkit.client("firehose")`（既定の設定）に替えても、偽物を置き場に入れるテストは通った。置き場が toolkit と共有で、先に toolkit が既定の設定で作ると FIREHOSE_CONFIG が効かない | 反対弁護人が注入して 83/0 を確認 | 直した: 専用の `_cache`、検査は boto3.client を差し替えて呼ばれた引数を見る。注入 R18 R19 で落ちる（下） |
| 4 | Nit | docs | `docs/troubleshooting.md:79` | `ALERT_EVENT_LOST` が 0 件なら全部入ったと読める（#1 の timeout は何も出さない） | 読んだだけ | 直した: `Task timed out` も見るように書いた |
| 5 | Nit | ログ | `graph/status_handler.py:128-131` | `ALERT_DROPPED` の文言が dict でない要素に触れない。`{"alerts": []}` は「読めない」と出る | 読んだだけ | 最終報告 |
| 6 | Nit | 設計整合性 | design.md:181 | 「Neptune の結果は ignored」は main では `unregistered` | test_sync の「未登録の機器の通知も履歴には 1 行送る」 | 最終報告（設計の文言） |
| 7 | Nit | correctness | `workflow/rules.py` の alert_event | starts_at の無い通知は event_id の末尾が `#0`（Round 1 #8 と同じ） | Round 1 の da_probe | 最終報告 |
| 8 | Nit | 保守性 | `agent/graph.py:60` のコメント、`tests/test_sync.py` の項目名 | graph.py の「再試行 1 回」は実際は 3 回試す（neptune_probe）。test_sync の「書き込みは繰り返して害が無い」は Round 1 #3 で崩れている。どちらも main から来たもの | `neptune_probe.py`（下）、`git grep` で main にあることを確認 | 最終報告（範囲外） |
| 9 | Should | runtime | `graph/status_handler.py:47-49` | 自分で見つけた。boto3 の既定（legacy、5 回、接続 60 秒）だと、Firehose に届かないときに 1 回目の呼び出しで 30 秒を使い切り、ERROR を書く前に timeout する | `retry_probe.py`（下）: 既定は 5 回 10.5 秒（接続 1 秒に縮めて） | 直した: FIREHOSE_CONFIG。`lost_probe.py` で 3 回、6.6 秒、ERROR 3 件 |
| 10 | Should | missing tests | `tests/test_sync.py`（_ShortAnswer） | RequestResponses が Records より短いときに、zip で拾うと落ちた行を取りこぼす。そこを縛る検査が弱かった | 注入 R15 | 直した: 偽物の返す RequestResponses を変えて検査を強めた（下で R15 が落ちる） |

注入（Round 2 の検査。`mutate3.py`。毎回元に戻した）:
```
R1 送り直しをしない（1 回で終わる）: test_sync exit 1 / AssertionError: 1 回目に 2 行中 1 行（2 行目）が届かなければ、2 回目はその 1 行だけを送る。0.2 秒待ち、例外にも ERROR にもしない
R2 送り直しを 1 回だけにする（合わせて 2 回）: test_sync exit 1 / AssertionError: Firehose が毎回例外なら put_record_batch は 3 回、待ちは 0.2 秒と 0.4 秒、行ごとに ERROR を 1 つ出して例外にしない（Neptune は書く）
R3 一部失敗でも全部を送り直す: test_sync exit 1 / AssertionError: 1 回目に 2 行中 1 行（2 行目）が届かなければ、2 回目はその 1 行だけを送る。0.2 秒待ち、例外にも ERROR にもしない
R4 Firehose の失敗で例外にする: test_sync exit 1 / RuntimeError: firehose
R5 残った行を ERROR に書かない: test_sync exit 1 / AssertionError: Firehose が毎回例外なら put_record_batch は 3 回、待ちは 0.2 秒と 0.4 秒、行ごとに ERROR を 1 つ出して例外にしない（Neptune は書く）
R6 残った行をまとめて ERROR 1 つにする: test_sync exit 1 / AssertionError: Firehose が毎回例外なら put_record_batch は 3 回、待ちは 0.2 秒と 0.4 秒、行ごとに ERROR を 1 つ出して例外にしない（Neptune は書く）
R7 捨てた件数の WARNING を出さない: test_sync exit 1 / AssertionError: device_id の無いアラートは put_record_batch に送らず、WARNING を 1 回（ALERT_DROPPED と件数）
R8 捨てた件数を数えず、全部捨てたときだけ読めないと出す: test_sync exit 1 / AssertionError: device_id の無いアラートは put_record_batch に送らず、WARNING を 1 回（ALERT_DROPPED と件数）
R9 捨てた通知も WARNING と読めないの 2 回出す: test_sync exit 1 / AssertionError: device_id の無いアラートは put_record_batch に送らず、WARNING を 1 回（ALERT_DROPPED と件数）
R10 Neptune の失敗で Firehose を飛ばす: test_sync exit 1 / AssertionError: Neptune に書けなくても Firehose には送り（put_record_batch は呼ばれる）、Neptune の失敗だけで最後に RuntimeError
R11 Neptune の失敗でも例外にしない: test_sync exit 1 / AssertionError: Neptune に書けなければ最後に RuntimeError で落とす（Lambda の非同期の再試行に任せる。書き込みは繰り返して害が無い）
R12 botocore の再試行を残す: test_sync exit 1 / AssertionError: Firehose へは FIREHOSE_CONFIG で 1 つだけ作ったクライアントで送り、botocore の再試行を切る（1 回）。3 回の接続と読みの待ち + 送り直しの待ちが Lambda の timeout（30 秒）に収まる
R13 接続の待ちを既定の 60 秒にする: test_sync exit 1 / AssertionError: Firehose へは FIREHOSE_CONFIG で 1 つだけ作ったクライアントで送り、botocore の再試行を切る（1 回）。3 回の接続と読みの待ち + 送り直しの待ちが Lambda の timeout（30 秒）に収まる
R14 クライアントに設定を渡さない: test_sync exit 1 / AssertionError: Firehose へは FIREHOSE_CONFIG で 1 つだけ作ったクライアントで送り、botocore の再試行を切る（1 回）。3 回の接続と読みの待ち + 送り直しの待ちが Lambda の timeout（30 秒）に収まる
R15 RequestResponses の数が合わなくても zip で拾う: test_sync exit 1 / AssertionError: RequestResponses の数が合わなければどの行が落ちたか分からないので、全部を送り直す
R16 alert_count が dict の要素だけ数える: test_workflow exit 1 / AssertionError: alert_count は alerts の要素を形にかかわらず数える（alerts_from_message との差が捨てた件数。封筒も開け、読めない本文は 0）
R17 alert_count が封筒を開けない: test_workflow exit 1 / AssertionError: alert_count は alerts の要素を形にかかわらず数える（alerts_from_message との差が捨てた件数。封筒も開け、読めない本文は 0）
R18 送る経路が toolkit.client（既定の設定）を使う: test_sync exit 1 / AssertionError: Grafana の firing と resolved（starts_at は同じ）は 1 回の put_record_batch に 2 行、event_id は status で分かれる
R19 クライアントの置き場を toolkit._clients と共有する: test_sync exit 1 / AssertionError: Firehose へは FIREHOSE_CONFIG で 1 つだけ作ったクライアントで送り、botocore の再試行を切る（1 回）。3 回の接続と読みの待ち + 送り直しの待ちが Lambda の timeout（30 秒）に収まる
git status 行数（注入前と同じはず）: 69
```
（69 は merge 中の M / A / D と build-r2-check.log の数で、注入の前も 69。）

本物の botocore を、つながらないアドレス（10.255.255.1 と 192.0.2.1）に向けた実測。AWS には触れていない:
```
# retry_probe.py（#9。接続の待ちは 1 秒に縮めた）
botocore 1.43.94
default: attempts=5 elapsed=10.5s err=ConnectTimeoutError
total_max_attempts=1: attempts=1 elapsed=1.0s err=ConnectTimeoutError

# lost_probe.py（#9 の直しのあと。FIREHOSE_CONFIG のクライアントで send_history に 3 行）
HTTP の送信 3 回 / 所要 6.6 秒 / 戻り値 3 / ERROR 3 件
   ALERT_EVENT_LOST {"event_id": "e0", "device_id": "dc1-leaf-01"}
   ALERT_EVENT_LOST {"event_id": "e1", "device_id": "dc1-leaf-01"}
   ALERT_EVENT_LOST {"event_id": "e2", "device_id": "dc1-leaf-01"}

# neptune_probe.py（#8。反対弁護人が書いたもの。接続の待ちは 1 秒に縮めた）
graph.py と同じ max_attempts=2 (legacy) attempts 3 elapsed 1.8 EndpointConnectionError
status_handler の FIREHOSE_CONFIG と同じ total_max_attempts=1 attempts 1 elapsed 0.0 EndpointConnectionError

# hang_probe.py（#1。handler に graph.py と同じ Config の neptune-graph のクライアントを入れ、Firehose は偽物。Traceback は省いた）
handler の例外: RuntimeError neptune {"source": "", "status": "firing", "device_id": "dc1-leaf-01", "kind": "link_down", "target": "ethernet-1/1"}: C
Neptune への HTTP 送信 3 回 / handler 全体 31.5 秒 / put_record_batch に着いた時刻 [31.535162925720215]
Lambda の timeout 30 秒を 超える
```

#### 問題なしとした観点

- WARNING のあと ERROR の順（決定 2 と 6 のログの出し方）: design.md:58-61 と handler を突き合わせた（読んだだけ）。
- `_put` の `failed or rows`: ErrorCode の付いた行が拾えないときは全部を送り直すので、起きうるのは重複だけで、欠けは出ない。重複は読む側が event_id で落とす（test_app の row_number の検査と Round 1 の注入 M10）。
- alert_count が数えるもの: design.md:61「`alerts` の要素の数と、返ってきた数の差」と同じ。注入 R16 R17。
- ログの大きさ: 行の detail は 1000 文字以下、SNS の 1 通は 256 KB 以下なので、1 行ずつの ERROR は CloudWatch Logs のイベントの上限（256 KB）に収まる（読んだだけ）。
- merge の解き方: 両側が変えた 22 本は check.sh で fmt、validate、10 本のテストが通る（上の全文）。up.sh の重なった分岐は 1 つにした（Round 2 の作業中に見つけて直した）。

#### ジンテーゼ（反対弁護人のあと）

- 結論の変わったところ: 「FIREHOSE_CONFIG で、Firehose が止まっても ERROR は timeout の前に書ける」→「Neptune がすぐ返すときだけ書ける」。docs の「Neptune が落ちても Firehose には送る」→「Neptune がエラーを返すときだけ。応答しないと何も残らない」。
- 部分的な真実: #2 は計算だけで実測していない。Neptune Analytics が実際にどのくらいで返すかは AWS で測るまで分からない。
- 残り: #1 #2 は設計の順（Neptune を先に書く）と時間の予算に原因があるので差し戻す。直し方の候補は 3 つ。(a) graph-status の Lambda で使う Neptune のクライアントの待ちを短くし、`context.get_remaining_time_in_millis()` を見て残りが少なければ Neptune を打ち切って Firehose に回す。(b) Firehose を先に送る。(c) Lambda の timeout を延ばす。cold review には出さない。
