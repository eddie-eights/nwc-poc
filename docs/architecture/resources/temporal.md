# Temporal（ECS）

← [リソースごとの知見](README.md)

## ひとことで

「調査 → 修復案 → 人の承認 → 適用 → 確認」を 1 本のワークフローとして進める実行基盤。
Temporal の開発用サーバー（`start-dev`）と Python の worker を、Fargate の 1 タスクに入れて動かしている。Temporal の履歴はタスクと一緒に消える。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| サービス | 1 タスクに temporal と worker の 2 コンテナ。Fargate ARM、1 vCPU / 2 GB。AZ を選ぶキーは無い（サブネット a に 1 つ） | `terraform/workflow/ecs.tf`、変数 `task_cpu`、`task_memory` |
| temporal のコンテナ | `temporalio/temporal` 1.9.1 を ECR の `<prefix>-temporal` に写したもの。`server start-dev`、SQLite は `/tmp/temporal.db` | `ecs.tf`、変数 `temporal_image_tag`、`ops/up.sh` の `TEMPORAL_TAG` |
| worker のコンテナ | `workflow/worker.py`。ECR の `<prefix>-worker` | `ecs.tf`、`workflow/` |
| ポート | gRPC 7233 はタスクの中の localhost だけ。UI 8233 だけ外に出す（認証は無い） | `ecs.tf` の `command` |
| 待ち時間 | 承認待ち 120 分（`APPROVAL_TIMEOUT_MINUTES`）、解消の確認 300 秒（`VERIFY_TIMEOUT`）、閉じずに待つ 1440 分（`HOLD_MINUTES`） | 変数 `approval_timeout_minutes`、`verify_timeout_seconds`、`hold_minutes` |
| ワークフローの id | `investigate-<anomaly_id>`（発生の時刻を入れない） | `workflow/worker.py`、`workflow/rules.py` |
| スイッチ | `WORKFLOW=1`。`AGENT=1` と `PIPELINE=1` が要り、`SKIP_LAB` / `SKIP_STREAM` / `SKIP_ANALYTICS` / `SKIP_GRAPH` は書けない | `ops/up.sh` |
| 費用 | 5 セント/時 | `ops/up.sh` の先頭のコメント |

worker が読み書きするもの:

| 置き場 | 中身 | 向き |
|---|---|---|
| SQS `<prefix>-anomalies` | アラート（`firing` / `resolved`） | 読む（long polling 20 秒） |
| AgentCore Runtime | 原因と修復案 | 聞く |
| Neptune Analytics の `proposal` | 修復案の「いま」（`pending` → `approved` / `rejected` → `applied` → `verified` / `failed`、`expired`、`obsolete`） | 読み書き。承認待ちは 30 秒おきに見る |
| S3 Tables の `proposal_events` | 作成・承認・却下・時間切れ・適用・確認を 1 行ずつ（`event_id` = `<proposal_id>#<event>`） | 書く（PyIceberg） |
| lab の EC2 | 修復のコマンド（`sudo lab heal-main`） | SSM の Run Command で打つ |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| 利用者の PC | PC → Web の EC2 → Temporal UI | 8233/tcp。SSM のポートフォワーディング（`ops/up.sh` の手順 8-5 がコマンドを出す） |
| SQS | worker → キュー | `sqs` のエンドポイント、タスクロール |
| AgentCore Runtime | worker → Runtime | `bedrock-agentcore` のエンドポイント、タスクロール |
| Neptune Analytics | worker → グラフ | `neptune-graph-data` のエンドポイント、SigV4 |
| S3 Tables | worker → `proposal_events` | `s3tables` のエンドポイント、Iceberg REST、SigV4 |
| lab の EC2 | worker → SSM → EC2 | `ssm` のエンドポイント、Run Command |

## 知見

- **タスクは 1 つだけ。2 つにすると別々の Temporal になる。**
  開発用サーバーがタスクの中にあるので、承認待ちのワークフローが片方にしか無くなる。コードから確かめた理由で、AWS では試していない（2026-10-04）。
  出典: `terraform/workflow/ecs.tf` のコメント、`ops/up.sh` の先頭のコメント。
- **タスクが入れ替わると、走っていたワークフローは消える。**
  SQLite がタスクの中にあるため。修復案の頂点は `pending` のまま残り、時間切れにもならない。Grafana の次の送り直しでは、同じ発生の修復案があるので起きない。直すなら「承認」タブで却下する。
  出典: [workflow.md](../../workflow.md) の「通知の重なりと取りこぼし」。
- **gRPC の 7233 はタスクの外に出さない。**
  `--ip 127.0.0.1` で待ち、UI だけを `--ui-ip 0.0.0.0` で出す。worker は同じタスクの `localhost:7233`。2026-09-29 までは 7233 も外に開いていた。
  出典: `ecs.tf` のコメント、[workflow.md](../../workflow.md) の「Temporal UI を開く」。
- **ワークフローは異常ごとに 1 つ、修復案は発生ごとに 1 つ。**
  ワークフローの id に発生の時刻を入れないので、Grafana と Splunk が同じ障害を知らせても Temporal が二重起動を弾く。修復案の id は `<anomaly_id>#<first_seen>` で、直ってからもう一度起きたら別の修復案になる。
  出典: [workflow.md](../../workflow.md) の「流れ」。
- **異常の「いま」を置く場所は持たない。**
  発生はワークフローそのもの、解消はシグナル `resolved` で持つ。
  出典: [workflow.md](../../workflow.md) の「流れ」。
- **確認（verify）は Neptune を見に行かず、解消の通知を待つ。**
  通知は「機器 → Telegraf → MSK → Spark → Prometheus / Splunk → ルールの評価 → SNS → SQS」を通るので、直ってから届くまで 1〜2 分かかる。300 秒待つ。
  出典: [workflow.md](../../workflow.md) の「修復案の状態」。
- **`rejected` / `expired` / `failed` で終わるときは、解消の通知が来るまでワークフローを閉じない。**
  閉じると、まだ直っていない同じ異常の次の通知がもう一度調査を起こすため。長くて 1440 分。
  出典: [workflow.md](../../workflow.md) の「修復案の状態」。
- **承認・却下は `pending` のときだけ書ける。**
  `WHERE n.status = 'pending'` と `SET` が 1 本の openCypher。Web と worker は修復案の頂点の `status` だけでやり取りする。
  出典: [workflow.md](../../workflow.md) の「流れ」。
- **`proposal_events` の承認・却下の行は、worker が頂点の変化を拾ったときに書く。**
  worker が止まっていると遅れて入る。再試行で二重に入ることがあるので、集計では `event_id` で落とす。
  出典: [workflow.md](../../workflow.md) の「流れ」。
- **保守中の機器の異常では起こさない。**
  アラートの機器か、落ちた回線の相手が Nautobot で `Maintenance` のとき。Neptune を読めないときは起こす。
  出典: [workflow.md](../../workflow.md) の「流れ」。
- **`boto3` / `pyiceberg` / `pyarrow` は、呼ばれたときに関数の中で読み込む。**
  Temporal のワークフローサンドボックスに合わせるため。
  出典: `workflow/awsio.py` の先頭のコメント。
- **apply の直後は worker が数回落ちる。**
  Temporal が上がるまで 1〜3 分かかる。
  出典: [workflow.md](../../workflow.md) の「うまくいかないとき」。
- **修復案は以前 DynamoDB のテーブルだった。**
  2026-09-24 に Neptune と S3 Tables に寄せた。
  出典: `terraform/workflow/proposals.tf` の先頭のコメント、[data-stores.md](../../data-stores.md) の「5. 経緯: DynamoDB をやめた（2026-09-24）」。
- **Temporal はいまは ECS。あとで EKS に移す（2026-09-17 のユーザー決定）。**
  出典: `terraform/workflow/locals.tf` の先頭のコメント。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| Temporal の履歴 | 残らない（SQLite がタスクの `/tmp`） |
| 2 タスク | 立てられない（上の知見。AWS では未確認） |
| フラップ | 走っているあいだに届いた落ち直しの `firing` は捨てる（[workflow.md](../../workflow.md) の「通知の重なりと取りこぼし」） |
| 処置の種類 | 回線を上げる `heal-main` と見るだけの `check` だけ。事前チェック（`precheck`）の警告は、落とす処置を足したときに効く |

このあと変わる予定: 修復案は S3 Tables の `proposal_events` だけに置き、Web の承認は SQS で worker に届ける。[修復案を S3 Tables にまとめる（003）の設計](../../cycles/003-proposals-in-s3tables/design.md)。

## 関連

- [sns-sqs-lambda.md](sns-sqs-lambda.md)、[agentcore-bedrock.md](agentcore-bedrock.md)、[neptune-analytics.md](neptune-analytics.md)、[s3-tables-athena.md](s3-tables-athena.md)、[lab-ec2.md](lab-ec2.md)
- [workflow.md](../../workflow.md): 「流れ」「修復案の状態」「通知の重なりと取りこぼし」「Temporal UI を開く」「うまくいかないとき」
- [workflow.md](../workflow.md): 構成（workflow）
