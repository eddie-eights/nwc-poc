# 調査と修復（WORKFLOW）

← [README](../README.md)

`<prefix>` は `deploy.env` の `OWNER` から作る接頭辞 `<owner>-nwc-poc`。`deploy.env` に `AGENT=1`、`PIPELINE=1`、`WORKFLOW=1` を書いて `ops/up.sh` を打つと、下の全部ができる。アラート（ワークフローを起こす `link_down`）の送り手が要る: Splunk（`STORES` の `splunk`。linkDown の trap と SNMP のポーリングから検知する）か、Grafana のアラート（`STORES` の `grafana` に `SNMP_POLL=1`。SNMP のポーリングから検知する）。既定（`STORES=s3,grafana,splunk` / `SNMP_POLL=1`）では両方ある。`STORES` から `splunk` を外して `SNMP_POLL=0` にすると送り手が無く、`ops/up.sh` が止まる。

## 流れ

```mermaid
flowchart LR
  GRAF["Grafana<br/>アラートルール"] -->|"firing / resolved"| SNS["SNS<br/>prefix-alerts"]
  SPL["Splunk<br/>保存済みサーチ"] -->|"firing / resolved"| SNS
  SNS --> SQS["SQS<br/>prefix-anomalies"]
  SQS -->|"long polling 20 秒"| WK["worker<br/>link_down の異常ごとにワークフロー"]
  WK -->|"原因と修復案を聞く"| RT["AgentCore Runtime"]
  WK -->|"1 段ごとに 1 行（created で pending）"| PEV["修復案<br/>S3 Tables proposal_events"]
  PEV -->|"Athena で「いま」を読む"| WEB["Web の「承認」タブ"]
  WEB -->|"承認 / 却下"| DQ["SQS<br/>prefix-decisions"]
  DQ -->|"long polling 20 秒"| WK
  WK -->|"SSM Run Command<br/>sudo lab heal-main"| LAB["lab の EC2"]
```

- temporal（`start-dev`、データは SQLite）と worker は、ECS Fargate の 1 タスクに入っている。Temporal の履歴はタスクと一緒に消える。
- 修復案の置き場は S3 Tables の `proposal_events` だけ（2026-10-05 から。Neptune に修復案の頂点は無い）。作成・承認・却下・時間切れ・適用・確認のたびに 1 行足し、上書きしない。どの行にも全項目（28 列。原因、コマンド、理由、事前チェック、決めた人、処置の結果など）が入る。列の正は `app/temporal/rules.py` の `PROPOSAL_EVENT_COLUMNS`。
- 書くのは worker だけ。`event_id` は `<proposal_id>#<event>`、`seq` は `created` が 1 で、あとは前の行 + 1。再試行で同じ行が二重に入ることがあるので、集計では `event_id` で落とす。
- 修復案の「いま」は、`proposal_id` ごとに `seq` が最大の行の `status`。読むのは Athena（ワークグループ `<prefix>-history`）で、Web の「承認」タブとエージェントの `list_proposals` が同じ SQL を使う（`app/agentcore/proposals.py`）。
- Web の承認・却下は、決定専用の SQS `<prefix>-decisions` に 1 通送るだけ（`{"type":"decision","proposal_id","decision","decided_by","sent_at"}`）。worker の 2 つ目のループがこのキューを読み、ワークフローにシグナル `decide` を送る。ワークフローは待ち時間なしで続きに進む。押してから「承認」タブに出るまで数秒〜20 秒。
- 決定は最初の 1 通だけが効く。同じ修復案に内容の違う決定が後から届くと、`ignored` の行を足す（`status` は変わらない）。同じ内容の重複（SQS の配り直し）は捨てる。
- ワークフローがもう無いのに `pending` の修復案へ決定が届くと、worker が `expired` の行を足して閉じる。同じ異常の新しい修復案を作るときも、古い `pending` を `expired` で閉じてから `created` を足す（1 回のコミット）。
- 修復案には事前チェックが付く（`precheck`。「承認」タブの詳細に出る）。worker がその処置をいまの Neptune のトポロジに重ね、孤立する機器と冗長が切れる機器（残りの回線が 1 本）を出す。処置がグラフをどう変えるかは `app/temporal/rules.py` の `ACTION_CHANGES`。いまの処置は回線を上げる `heal-main` と見るだけの `check` なので、警告（注意・危険）は出ない。落とす処置を足したときに効く。同じ計算をチャットから `what_if` で引ける（チャットの側も worker の側も機器の役割を読み、TRex を Leaf どうしの通り道に数えない。worker は `awsio.read_topology` で役割を読む）。
- 承認・却下はチャットのツールに出していない。決定のキューに送れるのは Web の EC2 のロールだけで（`IaC/terraform/aws-managed/workflow/proposals.tf` の `<prefix>-workflow-web`）、Runtime と tools の Lambda のロールには `sqs:SendMessage` が無い。コード（ツールに出さない）と IAM の両方で線を引いている。
- アラートは Grafana と Splunk が同じ形の JSON で SNS のトピック `<prefix>-alerts` に出す（`{"source", "alerts": [{"status", "device_id", "kind", "target", "detail", "starts_at"}]}`。読むのは `app/temporal/rules.py` の `alerts_from_message`）。異常の id は `<device_id>#<kind>#<target>`。
- ワークフローは異常ごとに 1 つ（id は `investigate-<anomaly_id>`。発生の時刻を入れない）。Grafana と Splunk が同じ障害を知らせても、同じ id なので Temporal が二重起動を弾く。修復案は発生ごと（`<anomaly_id>#<first_seen>`。`first_seen` はアラートの `starts_at`）で、直ってからもう一度起きた次の発生は、前の修復案とは別の `proposal_id` になる。
- 起こすのは `firing` の `link_down` だけ（`app/temporal/rules.py` の `START_KINDS`。trap と BGP / IS-IS は Neptune の `status` を変えるだけ）。`resolved` は、走っているワークフローにシグナル `resolved` で伝える（走っていなければ何もしない）。異常の「いま」を置く場所は持たず、発生はワークフローそのもの、解消はシグナルで持つ。
- アラートの機器か、落ちた回線の相手の機器が Nautobot で保守中（Status が `Maintenance`。Neptune の `device` の `maintenance`）なら起こさない（`app/temporal/rules.py` の `maintenance_hold`。starter のログに `skip … 保守中の機器`）。Neptune を読めないときは起こす。
- アラートのキューのメッセージは、起こした・起こす理由が無い・保守中・同じ発生の修復案がもうある・同じ異常のワークフローが既に走っている・解消を伝える相手がいない、のどれかなら消す。Temporal、S3 Tables、Neptune に届かないときは消さずに残し、配り直させる（5 回で DLQ `<prefix>-anomalies-dlq`）。
- 決定のキューのメッセージは、シグナルを送れたか、ワークフローが無いと分かったら消す。Temporal や S3 Tables に届かないときは残す（5 回で DLQ `<prefix>-decisions-dlq`）。どちらのキューも保持は 1 日なので、worker が 1 日を超えて止まると、そのあいだに送った決定は消える（修復案は `pending` のまま残り、時間切れを待つ）。
- 同じルートで AgentCore Gateway（MCP）と tools Lambda も作る。Runtime はツールを Gateway 経由で呼ぶ（届かなければコンテナの中のツールで答える）。

## 修復案の状態

```mermaid
stateDiagram-v2
  [*] --> pending: 調査が終わる
  pending --> approved: 承認
  pending --> rejected: 却下（何もしない）
  pending --> expired: 120 分待っても決まらない
  pending --> obsolete: 待つあいだに解消の通知が届いた
  approved --> obsolete: 打つ直前に見たら、もう解消していた
  approved --> applied: lab でコマンドを打つ
  approved --> failed: コマンドが打てなかった
  applied --> verified: 300 秒のうちに解消の通知が届いた
  applied --> failed: 届かなかった
  rejected --> [*]
  expired --> [*]
  obsolete --> [*]
  verified --> [*]
  failed --> [*]
```

- 承認待ちは、シグナル `decide` か `resolved` が届くまで待つ（見に行かない）。120 分（`APPROVAL_TIMEOUT_MINUTES`）で `expired`。
- `expired` になる道は 3 つ: 120 分の時間切れ、同じ異常の新しい修復案ができた、決定が届いたがワークフローがもう無い。理由は行の `verify_note` に入る。
- 図の状態のほかに、行の `event` には `ignored`（効かなかった決定）がある。`status` を変えないので図には無い。
- 確認（verify）は Neptune を見に行かず、解消の通知（`resolved` のアラート）を 300 秒（`VERIFY_TIMEOUT`）まで待つ。通知は「機器 → Telegraf → MSK → Spark → Prometheus / Splunk → ルールの評価 → SNS → SQS」を通るので、直ってから届くまで 1〜2 分かかる。
- `rejected` / `expired` / `failed` で終わるときは、解消の通知が来るまで（長くて 1440 分。`HOLD_MINUTES`）ワークフローを閉じない。閉じると、まだ直っていない同じ異常の次の通知がもう一度調査を起こすため。
- 環境変数の既定は `app/temporal/worker.py`、タスクに渡す値は `IaC/terraform/aws-managed/workflow/ecs.tf`。

## 通知の重なりと取りこぼし

送り手は「いまの状態」を出すだけで、順序も 1 回だけの配達も約束しない。受け手は同じ知らせを何度受けてもよい作りにしてある。

| 場面 | どうなる |
|---|---|
| Grafana と Splunk が同じ `link_down` を知らせる | 異常の id が同じなので、ワークフローは 1 つ。後から来たほうは捨てる |
| 直らないまま時間がたつ | Grafana は 4 時間ごと（`repeat_interval`）に同じ `starts_at` で送り直す。ワークフローが走っていれば捨て、閉じていても同じ発生の修復案があれば起こさない。Splunk は状態が変わったときだけ出し、送り直さない |
| 調査や承認待ちのあいだに直って、また落ちた（フラップ） | `resolved` はその run 全体に 1 つのフラグなので、直った時点で `obsolete` になる。落ち直しの `firing` は、ワークフローが閉じた後に届けば新しい発生として調べ、走っているあいだ（調査の途中など）に届けば捨てる。捨てたときは、Grafana の次の送り直し（4 時間後）か Splunk の次の変化まで調査は起きない |
| 解消の通知が、ワークフローの始まる前や閉じた後に届く | 伝える相手がいないので捨てる（Neptune の `status` は Lambda が別に `UP` へ戻す） |
| Temporal のタスクが入れ替わる | 走っていたワークフローは消える（SQLite がタスクの中）。修復案は `pending` のまま残り、ワークフローの時間切れは働かない。Grafana の次の送り直しでは、同じ発生の修復案があるので起きない。「承認」タブで承認か却下を押すと、worker が `expired` の行を足して閉じる（処置は打たない）。同じ異常がもう一度起きて新しい修復案ができたときも、古い `pending` は `expired` で閉じる |
| 同じ修復案に承認と却下が続けて届く（2 人が押した、SQS が配り直した） | 最初の 1 通だけが効く。内容の違う後の決定は `ignored` の行で残り、同じ内容の重複は捨てる |

## 試す

1. lab に入り（[pipeline.md](pipeline.md) の「lab に入る」）、`sudo lab fail-main` で DC 側 Leaf の fabric（`dc1-a-leaf-01 ethernet-1/1`）を落とす。
2. 1〜2 分で `link_down` のアラートが出て（既定では Grafana と Splunk の両方から。Grafana は Alerting → Alert rules で Firing、Splunk は linkDown の trap とポーリングから。`SNMP_POLL=0` では Splunk の trap からだけ。Web の「トポロジ」タブではその回線が `DOWN` になる）、数十秒で「承認」タブに修復案（原因・打つコマンド・理由）が `pending` で並ぶ。
3. 下の詳細（原因・コマンド・事前チェック・理由）を読み、名前を入れて「詳細を読んだ」にチェックを入れてから「承認して直す」を押すと `approved` → `applied` → `verified` / `failed` と進む（`verified` は、直ったあとの解消の通知が届いてから。1〜2 分）。名前は「決めた人」の列に `<名前> (web)` で残る（Web には認証が無いので、名乗ってもらう）。

## Temporal UI を開く

UI（8233）はプライベートサブネットのタスクにあるので、Web の EC2 を踏み台にしてポートフォワーディングする。`ops/up.sh` の手順 8-5 が同じコマンドを表示する。
8233 は土台の通信の表の `web` → `workflow` の 1 行で開いている（[architecture/core.md](architecture/core.md) の「SG」）。gRPC の 7233 はタスクの外に出さない（Temporal は `--ip 127.0.0.1` で待ち、UI だけを `--ui-ip 0.0.0.0` で出す。ワーカーは同じタスクの `localhost:7233`）。

```bash
INSTANCE_ID=$(terraform -chdir=IaC/terraform/aws-managed/base/core output -raw web_instance_id); echo "$INSTANCE_ID"
WF_CLUSTER=$(terraform -chdir=IaC/terraform/aws-managed/workflow output -raw cluster_name); echo "$WF_CLUSTER"
WF_SERVICE=$(terraform -chdir=IaC/terraform/aws-managed/workflow output -raw service_name); echo "$WF_SERVICE"
WF_TASK=$(aws ecs list-tasks --region ap-northeast-1 --cluster "$WF_CLUSTER" --service-name "$WF_SERVICE" --query 'taskArns[0]' --output text); echo "$WF_TASK"
WF_TASK_IP=$(aws ecs describe-tasks --region ap-northeast-1 --cluster "$WF_CLUSTER" --tasks "$WF_TASK" --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value | [0]' --output text); echo "$WF_TASK_IP"
aws ssm start-session --region ap-northeast-1 --target "$INSTANCE_ID" --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters "{\"host\":[\"$WF_TASK_IP\"],\"portNumber\":[\"8233\"],\"localPortNumber\":[\"8233\"]}"
```

このあと http://localhost:8233/ を開く。UI に認証は無い（VPC の外からは届かない）。

## うまくいかないとき

ワーカーのログ:

```bash
terraform -chdir=IaC/terraform/aws-managed/workflow output -raw worker_logs_command; echo
```

| 症状 | 原因と直し方 |
|---|---|
| apply の直後に worker が落ちる | Temporal が上がるまで 1〜3 分かかる。数回落ちてから上がる |
| 承認を押しても `pending` のまま | 反映まで数秒〜20 秒かかる。「更新」を押す。1 分たっても変わらなければ、worker のログに `decide <proposal_id>` が出ているか、DLQ `<prefix>-decisions-dlq` に溜まっていないかを見る |
| 承認を押したら `expired` になった | ワークフローがもう無かった（worker のタスクが入れ替わった）。処置は打たれない。まだ落ちていれば、次の通知（Grafana の送り直しは 4 時間ごと）で別の修復案が出る |
| 承認しても approved のまま進まない（UI で `TimeoutError`） | ワーカーのイメージが古い。`deploy.env` の `IMAGE_TAG` を上げて `ops/up.sh` |
| Runtime のログに `gateway tools/list failed, using local tools` | Gateway に届いていない。答えはコンテナの中のツールで返る |
| 修復案が出ない | アラートが SQS まで届いていない。送り手 → SNS → SQS の順に見る（[troubleshooting.md](troubleshooting.md) の「パイプラインと WORKFLOW」） |
| `link_down` なのに修復案が出ない（starter のログに `保守中の機器`） | 仕様。その機器か回線の相手が Nautobot で `Maintenance`。Status を `Active` に戻すと、次のアラートから起きる |
| trap や BGP / IS-IS の異常に修復案が出ない | 仕様。ワークフローを起こすのは `link_down` だけ（`app/temporal/rules.py` の `START_KINDS`） |
| 承認したのに `obsolete` になった | 承認までに解消の通知が届いた。古い処置は打たない。もう一度落ちれば、次の通知で別の修復案が出る |
| 直したのに `failed`（`300 秒待っても解消の通知が届かない`） | 解消の通知が遅れたか届いていない。Grafana のルールが Normal に戻ったか、SQS の DLQ に溜まっていないかを見る。回線が実際に上がっていれば、もう打たなくてよい |
| 却下したあと、同じ異常の修復案が二度と出ない | 仕様。直るまで（長くて 1440 分）同じ異常の調査を起こさない。直ってからもう一度落ちれば出る |
| ポートフォワーディングはつながるが Temporal UI が開かない | タスクが RUNNING か（`aws ecs describe-services`）と、ポートフォワーディングの先の IP がそのタスクか。タスクの ENI に `<prefix>-workflow`、Web の EC2 に `<prefix>-web` の SG が付いているか（8233 は土台の通信の表の `web` → `workflow`） |
