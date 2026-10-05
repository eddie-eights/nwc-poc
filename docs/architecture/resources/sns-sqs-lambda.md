# SNS・SQS・Lambda

← [リソースごとの知見](README.md)

## ひとことで

アラートを配る経路。Grafana と Splunk が同じ形の JSON を SNS のトピックに publish し、トピックが Lambda（Neptune の `status` とアラートの履歴）と SQS（ワークフローを起こす worker）へ配る。
SQS はもう 1 本あり、Web の承認・却下を worker に届ける（決定のキュー。SNS は通らない）。
配達は「少なくとも 1 回」で、順序も 1 回だけの配達も約束しない。受け手は同じ知らせを何度受けてもよい作りにしてある。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| トピック | `<prefix>-alerts`。暗号は AWS 管理の鍵（`alias/aws/sns`）。送り手も受け手も無いときも作る | `terraform/base/core/alerts.tf` |
| Lambda | `<prefix>-graph-status`。python3.13、arm64、128 MB、timeout 60 秒、VPC の中（`LAMBDA_AZ_NUM`、既定 1） | `terraform/pipeline/graph/sync.tf`、`graph/status_handler.py` |
| Lambda の zip の中身 | `graph/status_handler.py`（`index.py` として）、`agent/graph.py`、`agent/toolkit.py`、`workflow/rules.py` | `sync.tf` |
| Lambda のログ | `/aws/lambda/<prefix>-graph-status` | `sync.tf` |
| キュー | `<prefix>-anomalies`。可視性タイムアウト 120 秒、保持 1 日、long polling 20 秒、5 回受け取ったら DLQ へ | `terraform/workflow/events.tf` |
| DLQ | `<prefix>-anomalies-dlq`。保持 14 日 | `events.tf` |
| 決定のキュー | `<prefix>-decisions`。Web の承認・却下が 1 件 1 通で入る。設定はアラートのキューと同じ（可視性タイムアウト 120 秒、保持 1 日、long polling 20 秒、5 回で DLQ）。SNS の購読は無い。URL は SSM の `/<prefix>/decision-queue-url` | `events.tf`、`terraform/workflow/proposals.tf` |
| 決定の DLQ | `<prefix>-decisions-dlq`。保持 14 日 | `events.tf` |
| 購読 | Lambda（graph のルート）と SQS（workflow のルート。`raw_message_delivery = true`）。どちらも自分のルートが作る | `sync.tf`、`events.tf` |
| スイッチ | トピックは常に作る。Lambda は `PIPELINE=1`（`SKIP_GRAPH=1` で外す）、SQS は `WORKFLOW=1` | `ops/up.sh` |
| 費用 | 時間課金は無い（publish は 100 万件/月まで、SQS は 100 万リクエスト/月まで無料）。エンドポイント `sns`、`sqs` が 1.4 セント/時 × `ENDPOINTS_AZ_NUM` | `alerts.tf` と `events.tf` のコメント、`ops/up.sh` の先頭のコメント |

メッセージの中身:

| 項目 | 中身 |
|---|---|
| 本文 | `{"source": "grafana" \| "splunk", "alerts": [{"status", "device_id", "kind", "target", "detail", "starts_at"}]}` |
| `status` | `firing`（発火）か `resolved`（解消） |
| `kind` | `link_down`、`bgp_down`、`isis_down`、`trap` |
| 異常の id | `<device_id>#<kind>#<target>`。送り手が違っても同じ機器・種類・対象なら同じ |
| 履歴の行の `event_id` | `<anomaly_id>#<source>#<status>#<starts_at の epoch 秒>`（`starts_at` が無いと末尾は `#0`） |
| 決定のキューの本文 | `{"type": "decision", "proposal_id", "decision": "approved" \| "rejected", "decided_by", "sent_at"}`（読むのは `workflow/rules.py` の `decision_from_message`） |

このファイルの Lambda は `<prefix>-graph-status` だけ。Gateway のツールの Lambda `<prefix>-tools` と KB の index を作る `<prefix>-kb-index` は [agentcore-bedrock.md](agentcore-bedrock.md)。

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| Grafana、Splunk | 送り手 → トピック | `sns` のエンドポイント、タスクロールの `sns:Publish` だけ。アクセスキーは置かない |
| Lambda `<prefix>-graph-status` | トピック → Lambda | SNS が非同期で呼ぶ |
| Firehose `<prefix>-alert-events` | Lambda → Firehose | `kinesis-firehose` のエンドポイント、`firehose:PutRecordBatch`（analytics がある回だけ） |
| Neptune Analytics | Lambda → グラフ | `neptune-graph-data` のエンドポイント、SigV4 |
| SQS `<prefix>-anomalies` | トピック → キュー | SNS のサービスが配る（キューのポリシーで許す） |
| worker（Temporal のタスク） | worker → キュー（アラートと決定の 2 本） | `sqs` のエンドポイント、long polling 20 秒、タスクロール（受け取りと削除） |
| Web の EC2（承認タブ） | Web → 決定のキュー | `sqs` のエンドポイント、インスタンスロールの `sqs:SendMessage`（ポリシー `<prefix>-workflow-web`。このロールだけ） |

## 知見

- **トピックを土台に置くのは、送り手と受け手のどれが先に作られても参照できるようにするため。**
  送り手は analytics、受け手は graph と workflow で、ルートが別。
  出典: `terraform/base/core/alerts.tf` の先頭のコメント。
- **トピックもキューも、VPC の外からの呼び出しを拒む。**
  偽のアラートでワークフローを起こしたり `status` を書き換えたりさせないため。トピックで拒むのは Publish だけ（ポリシーの読み書きは、デプロイする人が変わっても戻せるように残す）。SNS からキューへの配信は `aws:PrincipalIsAWSService` で外れる。
  出典: `alerts.tf` と `terraform/workflow/events.tf` のコメント。
- **SQS を挟むのは、ECS のタスクを SNS から直接叩けないから。**
  HTTP の口も Lambda も要らない一番安い経路。
  出典: `events.tf` の先頭のコメント。
- **2026-10-02 までは Spark が EventBridge に出していた。**
  Spark の detect が `put_events` し、ルールが同じ 2 つの受け手へ流していた。いまは検知を Grafana と Splunk に寄せている。
  出典: `alerts.tf`、`events.tf`、`sync.tf` の先頭のコメント。
- **メッセージの形を変えるときは 3 か所を一緒に変える。**
  Grafana のテンプレート、Splunk のアラートアクション、`workflow/rules.py` の `alerts_from_message`（worker と Lambda が同じものを使う）。
  出典: [pipeline.md](../../pipeline.md) の「アラート」。
- **Lambda の zip に `agent/toolkit.py` が無いと、実行時に `ModuleNotFoundError` で落ちる。**
  `agent/graph.py` が読み込むため。apply は通るので気づきにくい。
  出典: `terraform/pipeline/graph/sync.tf` のコメント。
- **Lambda は Neptune より先に Firehose へ送る。**
  Neptune が遅くても応答しなくても履歴は残る。Firehose に長くて 15.6 秒（エンドポイントが 2 AZ なら 21.6 秒）、Neptune の 1 回の問い合わせに長くて 27 秒（2 AZ なら 33 秒）。エンドポイントを 3 AZ にすると上限が 66.6 秒で timeout の 60 秒を超える（`ops/up.sh` は注意だけ出して進む。2026-10-05 のユーザー決定）。
  出典: [pipeline.md](../../pipeline.md) の「アラートの履歴」、`ops/up.sh` のコメント。
- **Lambda が例外を投げるのは、Neptune への書き込みが失敗したときだけ。**
  Firehose の失敗では落とさない（`status` の正しさを履歴より優先する）。落ちると Lambda の非同期のやり直しが 2 回まで走る。
  出典: [pipeline.md](../../pipeline.md) の「アラートの履歴」。
- **やり直しは、書けていた通知も流し直す。**
  同じ通知が履歴に二重に入る（読むときに `event_id` で落とす）。そのあいだに届いた通知の `status` を古い値に戻すこともある。
  出典: 同上、アラートの履歴を残す（001）の設計のリスクの 10。
- **ログの目印は 3 つ。**
  `ALERT_EVENT_LOST`（ERROR。Firehose に 3 回送っても届かなかった行。JSON がそのまま出る）、`ALERT_DROPPED`（WARNING。形の合わない通知や行を組めない通知）、`UNREGISTERED`（WARNING。トポロジに無い機器や IF）。
  出典: [pipeline.md](../../pipeline.md) の「アラートの履歴」「Neptune のトポロジ」。
- **ワークフローを起こすのは `firing` の `link_down` だけ。**
  trap と BGP / IS-IS は Neptune の `status` を変えるだけ。`resolved` は、走っているワークフローにシグナルで伝える（走っていなければ何もしない）。
  出典: [workflow.md](../../workflow.md) の「流れ」。
- **Grafana と Splunk が同じ障害を知らせても、ワークフローは 1 つ。**
  ワークフローの id が `investigate-<anomaly_id>` で、Temporal が二重起動を弾く。`status` は上書きなので、同じ値を 2 回書いても変わらない。
  出典: [workflow.md](../../workflow.md) の「通知の重なりと取りこぼし」、[data-stores.md](../../data-stores.md) の「アラートの経路（格納先 → 修復）」。
- **worker はメッセージを「処理できた」ときだけ消す。**
  起こした・起こす理由が無い・保守中・既に走っている・解消を伝える相手がいない、のどれかなら消す。Temporal や Neptune に届かないときは残して配り直させ、5 回で DLQ へ。処理が 120 秒を超えると、もう一度受け取る。
  出典: [workflow.md](../../workflow.md) の「流れ」、[data-stores.md](../../data-stores.md) の「アラートの経路（格納先 → 修復）」。
- **決定のキューに送れるのは Web の EC2 のロールだけ。**
  「チャットからは承認できない」を IAM で守る。Runtime と tools の Lambda のロールには `sqs:SendMessage` を付けない。キューのポリシーは VPC の外からの呼び出しの Deny だけ（閉域があるとき）。
  出典: `terraform/workflow/proposals.tf` の先頭のコメント、`events.tf` のコメント。
- **決定をアラートのキューに入れても効かない。**
  アラートのキューには Grafana と Splunk のタスクロールも SNS 越しに届くので、worker はそこに来た決定を読めないメッセージとして消す。
  出典: `workflow/worker.py` の `handle_message` のコメント。
- **決定のメッセージは、シグナルを送れたか、ワークフローが無いと分かったら消す。**
  ワークフローが無く修復案が `pending` なら、worker が `expired` の行を足してから消す。Temporal や S3 Tables に届かないときは残し、5 回で DLQ へ。worker が 1 日を超えて止まると、保持の切れた決定は消える（修復案は `pending` のまま）。
  出典: `workflow/worker.py` の `handle_decision`、`events.tf` のコメント。
- **`starts_at` の意味は送り手で違う。**
  Grafana は発火した時刻で、`resolved` でも発火の時刻のまま。Splunk はその状態を最後に見た時刻（`resolved` なら戻った時刻）。
  出典: [pipeline.md](../../pipeline.md) の「アラートの履歴」。
- **`starts_at` の無い通知は、別の回の発生でも履歴で 1 行にまとまる。**
  `event_id` の末尾が `#0` になるため。いまの送り手は `starts_at` を付けるので起きない見込み。
  出典: [data-stores.md](../../data-stores.md) の「アラートの経路（格納先 → 修復）」。
- **`status` が変わらないときの見方。**
  [troubleshooting.md](../../troubleshooting.md) の「パイプラインと WORKFLOW」の「Neptune の `status` が変わらず…」「`query_history` に出ない通知がある」の行。
  出典: 同じファイル。

区間ごとの保証（[data-stores.md](../../data-stores.md) の「アラートの経路（格納先 → 修復）」から）:

| 区間 | 失う場面 | 重複する場面 |
|---|---|---|
| Splunk → SNS | publish を 3 回試して失敗した分 | 同じイベントが 2 回 index に入ったとき |
| Grafana → SNS | 通知の失敗は Grafana が送り直す | 発火中は 4 時間ごとに送り直す |
| SNS → Lambda | やり直し（2 回）を使い切った分 | Lambda が失敗してやり直した分 |
| SNS → SQS → worker → Temporal | 5 回受け取っても処理できなかった分は DLQ へ | 処理が 120 秒を超えたとき |
| Web → 決定のキュー → worker → Temporal | 5 回受け取っても処理できなかった分は DLQ へ。worker が 1 日を超えて止まると保持が切れる | 処理が 120 秒を超えたとき（同じ内容の決定はワークフローが捨てる） |

## 制約と未確認

| 項目 | 状態 |
|---|---|
| エンドポイントが 3 AZ のとき | Lambda の待ちの上限（66.6 秒）が timeout（60 秒）を超える。注意だけ出す |
| フラップ（承認待ちのあいだに直って、また落ちた） | 落ち直しの `firing` がワークフローの走っているあいだに届くと捨てる。次に調査が起きるのは Grafana の送り直し（4 時間後）か Splunk の次の変化 |
| DLQ に入ったメッセージ | 戻す仕組みは作っていない（保持 14 日） |
| AWS の上での通し（Grafana と Splunk の publish、Firehose への書き込み） | 2026-10-05 に AWS で確かめた。`sudo lab fail-main` で Grafana と Splunk の両方が `link_down` と `isis_down` を出し、SNS → Lambda（`status` の書き換えと Firehose）と SNS → SQS → worker の配信が通った。`bgp_down` と `trap` の firing は未確認 |
| 決定のキュー（Web → SQS → worker） | 2026-10-05 に AWS で承認の 1 通を確かめた。却下、重複、DLQ に落ちる経路は未確認 |

2026-10-05 に、Web の承認も SQS で worker に届ける形に変えた。[修復案を S3 Tables にまとめる（003）の設計](../../cycles/003-proposals-in-s3tables/design.md)。

## 関連

- [grafana.md](grafana.md)、[splunk.md](splunk.md)、[firehose.md](firehose.md)、[neptune-analytics.md](neptune-analytics.md)、[temporal.md](temporal.md)
- [pipeline.md](../../pipeline.md): 「アラート」「アラートの履歴」
- [workflow.md](../../workflow.md): 「流れ」「通知の重なりと取りこぼし」
- [data-stores.md](../../data-stores.md): 「届け方の保証（どの区間で、失うか、重複するか）」
