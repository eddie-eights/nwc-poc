# Web の EC2

← [リソースごとの知見](README.md)

## ひとことで

運用者の画面（Gradio。チャット / トポロジ / 承認）を動かす EC2。
パブリック IP も受信ルールも無く、PC からは SSM のポートフォワーディングで開く。Grafana、Splunk、Temporal UI、Nautobot を開くときの踏み台も兼ねる。
stream を作る回は Kafbat UI も Docker で同居する（Kafbat UI を Web の EC2 に同居させる（010））。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| 台数と AZ | 1 台。サブネット a。AZ を選ぶキーは無い | `IaC/terraform/aws-managed/base/core/web.tf` の `aws_instance.web` |
| インスタンス | t4g.medium（Kafbat UI の JVM の分。t4g.small の 2 GB では足りない）、Amazon Linux 2023（arm64。AMI は SSM の公開パラメータから読む）、gp3 16 GB（Docker とイメージの分） | `IaC/terraform/aws-managed/base/core/web.tf`、変数 `instance_type`、`ami_ssm_parameter` |
| メタデータ | IMDSv2 だけ、ホップ数 2（Kafbat UI のコンテナが Docker の bridge 越しにインスタンスロールを取る） | `IaC/terraform/aws-managed/base/core/web.tf` |
| 画面 | Gradio が `127.0.0.1:8080` だけで待つ。systemd のユニット `<prefix>-web` | `app/dashboard/app.py`、`IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl` |
| Kafbat UI | Docker のコンテナが `127.0.0.1:8082` だけで待つ。systemd のユニット `<prefix>-kafka-ui`（動きは表の下） | `IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl`、`IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf` |
| 画面のコード | 共有バケット `<prefix>-kb-<アカウント>` の `web/` から起動時に取る | `ops/up.sh` の手順 4、`IaC/terraform/aws-managed/base/core/bucket.tf` |
| ロール | `<prefix>-web`。SSM の管理、`web/*` の読み取り、`/<prefix>/*` の `ssm:GetParameter`。ECR の `<prefix>-kafka-ui` からのイメージの取得。ほかのルートが足す許可は表の下 | `IaC/terraform/aws-managed/base/core/web.tf`、`IaC/terraform/aws-managed/agent/runtime.tf`、`IaC/terraform/aws-managed/workflow/proposals.tf` |
| スイッチ | 無い（土台なので必ず作る）。PC 側のポートは `LOCAL_PORT`（既定 8080）、`NO_DASHBOARD_PORTFORWARD=1` で最後のポートフォワーディングを開かない | [deploy.md](../../deploy.md) の「`deploy.env` のキー」 |
| 費用 | 4.3 セント/時（t4g.medium。土台は合わせて約 4 セント/時 + エンドポイント） | `ops/up.sh` の費用の目安（524〜584 行） |

- Kafbat UI のユニット `<prefix>-kafka-ui` の動き:
  - Restart=always、30 秒ごと。終了コード 75 は成功の扱いにして起こし直さない。
  - イメージ・接続先・パスワードは SSM の `/<prefix>/kafka-ui/` から起動のたびに読む。
  - パラメータが無ければ（stream が無い回）1 回で止まり（75。コンテナは無い）、Web のユニットの `Wants=` で Web の再起動が起こす。
    手で止めても Web の start / restart のたびに起き直す。止めたままにするなら `systemctl mask --runtime <prefix>-kafka-ui`。
  - それ以外で読めなければ 69 で終わって 30 秒ごとに起こし直す。
  - Docker の節が落ちても user_data は止まらない（Gradio が先）。ログは `journalctl -u <prefix>-kafka-ui`。
- ロールにほかのルートが足す許可:
  - Runtime と Gateway を呼ぶ許可は agent と workflow のルートが足す。
  - MSK の権限（Kafbat UI）は stream のルートがポリシー `<prefix>-kafka-ui` で足す。
  - 承認タブの許可（決定のキューへの `sqs:SendMessage` と、Athena での `proposal_events` の読み取り）は workflow のルートがポリシー `<prefix>-workflow-web` で足す。

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| 利用者の PC | PC → Web | SSM のポートフォワーディング（SSM Agent が内側から ssmmessages へつなぐ）。`localhost:8080` |
| AgentCore Runtime | Web → Runtime | `InvokeAgentRuntime`（bedrock-agentcore のエンドポイント、IAM）。ARN は SSM `/<prefix>/runtime-arn` |
| Athena → S3 Tables の `proposal_events` | Web → Athena（承認タブの一覧と詳細） | `athena` のエンドポイント、インスタンスロール。ワークグループ `<prefix>-history`。開いているあいだ 30 秒ごとに 1 本 |
| SQS `<prefix>-decisions` | Web → キュー（承認・却下） | `sqs` のエンドポイント、`sqs:SendMessage`。URL は SSM `/<prefix>/decision-queue-url` |
| AgentCore Gateway | 呼んでいない | Web に置くモジュール（`toolkit` / `topology` / `graph` / `proposals`）に Gateway のクライアント（`app/agentcore/mcp_client.py`）は入っていない。ロールにも `InvokeGateway` は付けない（付ける先は表の下） |
| Neptune Analytics | Web → グラフ | neptune-graph-data のエンドポイント、SigV4（トポロジの表示と、最初の投入）。修復案は読まない |
| Nautobot | Web → Nautobot | 8080/tcp（トポロジの編集を REST API へ。SSM `/<prefix>/nautobot/url` があるあいだ） |
| Grafana / Splunk / Temporal UI / Nautobot | PC → Web → 各タスク | 3000 / 8000 / 8233 / 8080（Web を踏み台にしたポートフォワーディング） |
| Kafbat UI | PC → Web の `127.0.0.1:8082` | SSM のポートフォワーディング（`AWS-StartPortForwardingSession`。PC 側も 8082） |
| MSK | Web → MSK（Kafbat UI） | 9098/tcp、IAM 認証（インスタンスロール） |
| S3 | Web → バケット | gateway 型エンドポイント（`web/` の取得と dnf） |

- AgentCore Gateway の `InvokeGateway` を付けるのは Runtime だけ（`IaC/terraform/aws-managed/workflow/proposals.tf` の `reader_access`）。

## 知見

- **Web は ECS にせず EC2 のままにしている。**
  踏み台を兼ねるため。ECS にするとタスクの IP が変わり、踏み台にしにくい。
  出典: [core.md](../core.md) の先頭。
- **EC2 に置くのは画面だけで、エージェント本体（`app/agentcore/app.py`）は置かない。**
  - モデルを呼ぶのは Runtime。
  - Web のログに `KeyError: 'MODEL_ID'` や Web のロールでの `bedrock:InvokeModel` の `AccessDenied` が出たら、置くファイルを間違えている。Web のロールに権限を足して直さない。
  - 出典: [troubleshooting.md](../../troubleshooting.md) の「チャットの答えがおかしい」、[README.md](../README.md) の「どのファイルがどこで動くか」。
- **Runtime の ARN は SSM から 60 秒ごとに読む。**
  agent をあとから足しても入れ替えても、Web の EC2 を作り直さずに済む。
  出典: `IaC/terraform/aws-managed/base/core/web.tf` の `web_assets` のコメント。
- **user_data を変えるとインスタンスは作り直しになる。**
  `user_data_replace_on_change`。画面のコードだけを変えたときは、`ops/up.sh` の手順 4 が S3 に置き直して EC2 を再起動する。
  出典: `IaC/terraform/aws-managed/base/core/web.tf`、`ops/up.sh` の手順 4-2 と 4-4。
- **承認タブは Neptune を使わない。**
  - 一覧と詳細は Athena で `proposal_events` を読み、承認・却下は決定のキューに 1 通送る。押したあと反映まで数秒〜20 秒かかる（画面にもそう出る）。
  - 名前は 40 文字までで、`<名前> (web)` として「決めた人」に残る。承認には名前と「詳細を読んだ」のチェックが要る。
  - 出典: `app/dashboard/incident_view.py`、`app/agentcore/proposals.py` の `decide`、[workflow.md](../../workflow.md) の「流れ」。
- **ポートフォワーディングはアイドル 20 分で切れる。**
  `start-session` をやり直して再読み込みする。
  出典: [troubleshooting.md](../../troubleshooting.md) の「画面に入れない」。
- **ログは journald と cloud-init。**
  `sudo journalctl -u <prefix>-web -n 100`、起動時の失敗は `/var/log/cloud-init-output.log`。
  出典: [troubleshooting.md](../../troubleshooting.md) の「チャットの答えがおかしい」。
- **Runtime が 150 秒返らないと失敗にする。**
  初回は Runtime の起動が遅いので再送する。
  出典: 同上。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| 1 台・1 AZ | 冗長にしていない。AZ が止まると画面も踏み台も使えない（コードから読める範囲。AWS で確かめた記録は無い） |
| 承認タブ | 2026-10-05 に AWS で確かめた（承認 → `heal-main` が Success → verified。名前に `'` を入れても通った）。却下は未確認 |
| ページのタイトル | 文字化けする。既知（2026-10-05。[troubleshooting.md](../../troubleshooting.md) の「既知の不具合」） |
| 認証 | 画面にログインは無い。入れる人は SSM の `start-session` を打てる IAM の利用者（[deploy.md](../../deploy.md) の「利用者に画面を渡す」） |

## 関連

- [core.md](../core.md)、[agent.md](../agent.md): 土台とチャットの経路
- [deploy.md](../../deploy.md): 利用者に画面を渡す
- [development.md](../../development.md): Web を手元で動かす
- [agentcore-bedrock.md](agentcore-bedrock.md)、[vpc-perimeter.md](vpc-perimeter.md)

## 経緯

- 2026-10-05: 承認タブが Neptune を使わなくなった（一覧と詳細は Athena、承認・却下は決定のキュー）。
