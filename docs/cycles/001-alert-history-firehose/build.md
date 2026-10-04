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
