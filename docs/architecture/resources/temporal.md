# Temporal（ECS）

← [リソースごとの知見](README.md)

## ひとことで

「調査 → 修復案 → 人の承認 → 適用 → 確認」を 1 本のワークフローとして進める実行基盤。
Temporal のサーバー（`temporal-server`、本番モード）と Temporal UI と Python の worker を、Fargate の 1 タスクに入れて動かしている。
Temporal の履歴は Nautobot の RDS for PostgreSQL（DB `temporal` / `temporal_visibility`）に残るので、タスクが入れ替わっても走っていたワークフローは続く（cycle 036）。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| サービス | 1 タスクに temporal・ui・worker の 3 コンテナ。Fargate ARM、1 vCPU / 2 GB。AZ を選ぶキーは無い（サブネット a に 1 つ）。デプロイは `deployment_minimum_healthy_percent = 100` / `deployment_maximum_percent = 200`（新しいタスクが上がってから古いのを止める） | `IaC/terraform/aws-managed/workflow/ecs.tf`、変数 `task_cpu`、`task_memory` |
| temporal のコンテナ | `temporalio/server` 1.32.1 に `temporal-sql-tool`・`temporal`（CLI）・psql 18 を足して自前でビルドしたもの（ECR の `<prefix>-temporal`。タグは中身のハッシュ入り）。entrypoint が起動のたびに、Nautobot の master でロール `temporal` と DB を作り（あれば何もしない）、スキーマを最新まで上げ、namespace `default`（保持 72 時間）を作る。healthCheck は namespace `default` の describe | `docker/images/temporal-server/`、`ecs.tf`、`ops/up-common.sh` の `TEMPORAL_SERVER_VERSION` / `POSTGRES_MAJOR` / `build_temporal_server` |
| DB | Nautobot の RDS for PostgreSQL 18 に相乗り。ロール `temporal` が DB `temporal` と `temporal_visibility` の持ち主。TLS（`SQL_TLS_ENABLED=true`）、接続は 4 + 2（visibility）まで、history の shard は 4（あとから変えられない） | `ecs.tf` の環境変数、変数 `temporal_sql_max_conns` ほか、`IaC/terraform/aws-managed/pipeline/nautobot/` |
| シークレット | ロール `temporal` のパスワードは SSM の SecureString `/<prefix>/temporal/db-password`（`ops/up.sh` が workflow の apply の前に乱数で作る）。master のパスワードは Nautobot の `/<prefix>/nautobot/db-password` を読む。どちらも ECS の `secrets` で渡し、state にもタスク定義にも値を書かない | `ops/up-common.sh` の `ensure_temporal_secrets`、`iam.tf` の `execution_db_passwords` |
| ui のコンテナ | `temporalio/ui` 2.55.0 を ECR の `<prefix>-temporal-ui` に写したもの。temporal が HEALTHY になってから起きる。`essential = false`（落ちてもワークフローは止めない） | `ecs.tf`、変数 `temporal_ui_image_tag`、`ops/up-common.sh` の `TEMPORAL_UI_TAG` |
| worker のコンテナ | `app/temporal/worker.py`。ECR の `<prefix>-worker`。temporal が HEALTHY になってから起きる | `ecs.tf`、`app/temporal/` |
| ポート | UI の 8233 だけを外に出す（認証は無い）。gRPC 7233〜7239 と membership 6933〜6939 は portMappings に無く、SG は workflow → workflow の自分宛てだけ | `ecs.tf`、`IaC/terraform/aws-managed/base/core/security_groups.tf` |
| 待ち時間 | 承認待ち 120 分（`APPROVAL_TIMEOUT_MINUTES`）、解消の確認 300 秒（`VERIFY_TIMEOUT`）、閉じずに待つ 1440 分（`HOLD_MINUTES`） | 変数 `approval_timeout_minutes`、`verify_timeout_seconds`、`hold_minutes` |
| ワークフローの id | `investigate-<anomaly_id>`（発生の時刻を入れない） | `app/temporal/worker.py`、`app/temporal/rules.py` |
| スイッチ | `WORKFLOW=1`。`AGENT=1` と `PIPELINE=1` が要り、`SKIP_LAB` / `SKIP_STREAM` / `SKIP_ANALYTICS` / `SKIP_GRAPH` は書けない | `ops/up.sh` |
| 費用 | 5 セント/時 | `ops/up.sh` の費用の目安（524〜584 行） |

worker が読み書きするもの:

| 置き場 | 中身 | 向き |
|---|---|---|
| SQS `<prefix>-anomalies` | アラート（`firing` / `resolved`） | 読む（long polling 20 秒） |
| SQS `<prefix>-decisions` | Web の承認・却下（決定 1 件が 1 通） | 読む（long polling 20 秒）。ワークフローにシグナル `decide` で渡す |
| AgentCore Runtime | 原因と修復案 | 聞く |
| Neptune Analytics | トポロジ（事前チェックと、保守中の機器の判定に使う）。修復案は置かない | 読むだけ |
| S3 Tables の `proposal_events` | 修復案。作成・承認・却下・時間切れ・適用・確認のたびに 1 行（28 列。どの行にも全項目。`event_id` = `<proposal_id>#<event>`、「いま」は `seq` が最大の行） | 読み書き（PyIceberg）。書くのは worker だけ |
| lab の EC2 | 修復のコマンド（`sudo lab heal-main`） | SSM の Run Command で打つ |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| 利用者の PC | PC → Web の EC2 → Temporal UI | 8233/tcp。SSM のポートフォワーディング（`ops/up.sh` の手順 8-5 がコマンドを出す） |
| RDS for PostgreSQL（Nautobot） | temporal → DB `temporal` / `temporal_visibility` | 5432/tcp、TLS。ロール `temporal` のパスワード（作るときだけ master） |
| SQS | worker → キュー（アラートと決定の 2 本） | `sqs` のエンドポイント、タスクロール（受け取りと削除だけ） |
| AgentCore Runtime | worker → Runtime | `bedrock-agentcore` のエンドポイント、タスクロール |
| Neptune Analytics | worker → グラフ（読むだけ） | `neptune-graph-data` のエンドポイント、SigV4 |
| S3 Tables | worker → `proposal_events` | `s3tables` のエンドポイント、Iceberg REST、SigV4 |
| lab の EC2 | worker → SSM → EC2 | `ssm` のエンドポイント、Run Command |

## 知見

- **タスクは 1 つ（`desired_count` は 0 か 1）。**
  履歴は RDS にあるので 2 つでも壊れないが、history の shard が 4 では分ける意味が薄い。デプロイの入れ替わりの間だけ、2 つのタスクが同じ DB の Temporal として並ぶ。
  出典: `IaC/terraform/aws-managed/workflow/ecs.tf` のコメント。
- **タスクが入れ替わっても、走っていたワークフローは続く（cycle 036）。**
  - 履歴は RDS の `temporal` にあり、新しいタスクの temporal が続きから動かす。手元の docker で、ワークフローを起こしてからコンテナを再起動しても `Running` のまま残ることを確かめた（2026-10-10。[cycle 036 の build.md](../../cycles/036-temporal-rds/build.md)）。AWS の RDS では未確認。
  - RDS ごと消す `ops/down.sh` のあとは残らない（Nautobot の RDS は down.sh で消える）。
- **初期化は temporal のコンテナの entrypoint がやる。**
  - ロールと DB は Nautobot の master で「無いときだけ」作る。スキーマは `schema_version` が無い初回だけ `setup-schema`、毎回 `update-schema`（べき等。消すコマンドは打たない）。
  - namespace `default` は temporal-server を起こしたあとに背景で作る。healthCheck がその describe を見るので、worker と ui は namespace ができてから起きる。
  - ロールと DB を作ったあと、master のパスワード（`NAUTOBOT_DB_PASSWORD`）は env から外してから temporal-server を起こす。
  - namespace を作る背景のプロセスは、抜けたあと ECS の init（`initProcessEnabled`）が回収する。
  - 出典: `docker/images/temporal-server/entrypoint.sh`。
- **gRPC の 7233〜7239 と membership の 6933〜6939 はタスクの外に出さない。**
  temporal は `BIND_ON_IP=0.0.0.0` で待つが portMappings に出さず、SG は workflow → workflow の自分宛てだけ（デプロイの入れ替わりで 2 つのタスクが並ぶ間に使う）。worker は同じタスクの `localhost:7233`、ui は `127.0.0.1:7233`。
  出典: `ecs.tf` のコメント、[workflow.md](../../workflow.md) の「Temporal UI を開く」。
- **ワークフローは異常ごとに 1 つ、修復案は発生ごとに 1 つ。**
  ワークフローの id に発生の時刻を入れないので、Grafana と Splunk が同じ障害を知らせても Temporal が二重起動を弾く。修復案の id は `<anomaly_id>#<first_seen>` で、直ってからもう一度起きたら別の修復案になる。
  出典: [workflow.md](../../workflow.md) の「流れ」。
- **異常の「いま」を置く場所は持たない。**
  発生はワークフローそのもの、解消はシグナル `resolved` で持つ。
  出典: [workflow.md](../../workflow.md) の「流れ」。
- **確認（verify）は Neptune を見に行かず、解消の通知を待つ。**
  通知は「機器 → gnmic → MSK → Spark → Prometheus / Splunk → ルールの評価 → SNS → SQS」を通るので、直ってから届くまで 1〜2 分かかる。300 秒待つ。
  出典: [workflow.md](../../workflow.md) の「修復案の状態」。
- **`rejected` / `expired` / `failed` で終わるときは、解消の通知が来るまでワークフローを閉じない。**
  閉じると、まだ直っていない同じ異常の次の通知がもう一度調査を起こすため。長くて 1440 分。
  出典: [workflow.md](../../workflow.md) の「修復案の状態」。
- **承認・却下は、Web が SQS に送り、worker がシグナル `decide` でワークフローに渡す。**
  - 1 つの worker のプロセスが 3 つを動かす: アラートのキューを読むループ、決定のキューを読むループ、Temporal の worker。
  - 承認待ちは頂点を見に行かず、シグナルを待つ。押してから反映まで数秒〜20 秒。
  - 出典: `app/temporal/worker.py` の先頭のコメント、[workflow.md](../../workflow.md) の「流れ」。
- **決定は最初の 1 通だけが効く。**
  - 内容の違う後の決定は `ignored` の行で残す（`status` は変えない）。同じ内容の重複（SQS の配り直し）は捨てる。
  - 承認待ちが時間切れや解消で終わったあとの決定は、ログだけ。
  - 出典: `app/temporal/worker.py` の `InvestigateAnomaly.decide`、[workflow.md](../../workflow.md) の「流れ」。
- **`proposal_events` に書くのは worker だけ。**
  - Web はテーブルに書かない。worker が止まっていると、承認・却下の行は遅れて入る（決定は SQS で 1 日まで待つ）。
  - 再試行で二重に入ることがあるので、集計では `event_id` で落とす。コミットがぶつかったら 5 回までやり直す。
  - 出典: `app/temporal/awsio.py` の `append_proposal_events`、`IaC/terraform/aws-managed/workflow/events.tf` のコメント。
- **保守中の機器の異常では起こさない。**
  アラートの機器か、落ちた回線の相手が Nautobot で `Maintenance` のとき。Neptune を読めないときは起こす。
  出典: [workflow.md](../../workflow.md) の「流れ」。
- **`boto3` / `pyiceberg` / `pyarrow` は、呼ばれたときに関数の中で読み込む。**
  Temporal のワークフローサンドボックスに合わせるため。
  出典: `app/temporal/awsio.py` の先頭のコメント。
- **apply の直後は worker が数回落ちる。**
  2026-10-10 までの話。cycle 036 から worker は temporal の HEALTHY（namespace `default` ができた）を待って起きるので、落ちない見込み（AWS では未確認）。
  出典: [workflow.md](../../workflow.md) の「うまくいかないとき」。
- **2026-10-05 に AWS で通した。**
  - `sudo lab fail-main` のあと、`proposal_events` に created → approved → applied → verified の行が入った。
  - Web の「承認」タブで承認すると `sudo lab heal-main` が Success になり、処置から 103 秒で verified になった。
  - 決めた人の名前に `'` を入れても通った。却下・時間切れ・failed の経路は未確認。
  - 出典: 2026-10-05 の動作確認（`AGENT=1 PIPELINE=1 WORKFLOW=1 ENDPOINTS_AZ_NUM=2 SPLUNK_AZ_NUM=2`）。
- **Temporal はいまは ECS。あとで EKS に移す（2026-09-17 のユーザー決定）。**
  出典: `IaC/terraform/aws-managed/workflow/locals.tf` の先頭のコメント。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| Temporal の履歴 | Nautobot の RDS に残る。`ops/down.sh` で RDS ごと消える |
| RDS の PostgreSQL 18 | Temporal の動作確認済みの一覧は 12〜16。手元の `postgres:18` ではスキーマが入り動いた（2026-10-10）。RDS の 18、TLS（`SQL_HOST_VERIFICATION=false`）、master での `CREATE EXTENSION btree_gin` は AWS では未確認。ホスト名検証を有効にするには、RDS の CA をイメージに入れ（いまの Dockerfile には無い）、ecs.tf に `SQL_HOST_VERIFICATION=true` と `SQL_CA` を足し、`tests/test_workflow.py` の期待を変える。entrypoint の psql / temporal-sql-tool は同じ env を読むので変えなくてよい |
| 2 タスク | デプロイの入れ替わりの間だけ並ぶ。AWS では未確認 |
| フラップ | 走っているあいだに届いた落ち直しの `firing` は捨てる（[workflow.md](../../workflow.md) の「通知の重なりと取りこぼし」） |
| 処置の種類 | `heal-main`（`dc1-a-leaf-01` の `ethernet-1/1` を上げる）と見るだけの `check` だけ。事前チェック（`precheck`）の警告は落とす処置を足したときに効く |
| worker が 1 日を超えて止まる | そのあいだに Web から送った決定は SQS の保持（1 日）で消える。修復案は `pending` のまま残る |
| 却下・時間切れ（`expired`）・`failed`・`ignored` の経路 | AWS では未確認（模擬テストだけ） |
| 1 本の回線断で修復案が 4 件できる | 既知（2026-10-05。[troubleshooting.md](../../troubleshooting.md) の「既知の不具合」）。承認した 1 件が verified、残りの 3 件は obsolete になった |
| Temporal UI の画面 | 開いて確かめていない（2026-10-05 の動作確認では見ていない） |

## 関連

- [sns-sqs-lambda.md](sns-sqs-lambda.md)、[agentcore-bedrock.md](agentcore-bedrock.md)、[neptune-analytics.md](neptune-analytics.md)、[s3-tables-athena.md](s3-tables-athena.md)、[lab-ec2.md](lab-ec2.md)
- [workflow.md](../../workflow.md): 「流れ」「修復案の状態」「通知の重なりと取りこぼし」「Temporal UI を開く」「うまくいかないとき」
- [workflow.md](../workflow.md): 構成（workflow）

## 経緯

- 2026-09-24: 修復案は DynamoDB のテーブルだったのを、Neptune と S3 Tables に寄せた（[data-stores.md](../../data-stores.md) の「5. 経緯: DynamoDB をやめた（2026-09-24）」）。
- 2026-09-29 まで: gRPC の 7233 も外に開いていた（出典: `ecs.tf` のコメント）。
- 2026-10-05（003）: 修復案を S3 Tables の `proposal_events` だけに置き、Web の承認を SQS で worker に届ける形に変えた。
  - [修復案を S3 Tables にまとめる（003）の設計](../../cycles/003-proposals-in-s3tables/design.md)
  - 出典: `IaC/terraform/aws-managed/workflow/proposals.tf` の先頭のコメント。
- 2026-10-10（036）: CLI の開発用サーバー（`temporalio/temporal` 1.9.1、履歴はコンテナの中のファイル）をやめ、`temporalio/server` 1.32.1 の本番モードで Nautobot の RDS に履歴を書く形に変えた。UI は別コンテナ。
  - [Temporal の履歴を RDS に残す（036）の設計](../../cycles/036-temporal-rds/design.md)
- 2026-10-10（039）: entrypoint で master のパスワードを exec の前に env から外し、psql / temporal-sql-tool の TLS をサーバー本体と同じ env から導き、temporal のコンテナに `initProcessEnabled` を足した。[temporal-server の entrypoint の守りを締める（039）の設計](../../cycles/039-temporal-entrypoint-hardening/design.md)
