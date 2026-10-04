# Web の EC2

← [リソースごとの知見](README.md)

## ひとことで

運用者の画面（Gradio。チャット / トポロジ / 承認）を動かす EC2。
パブリック IP も受信ルールも無く、PC からは SSM のポートフォワーディングで開く。Grafana、Splunk、Temporal UI、Nautobot を開くときの踏み台も兼ねる。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| 台数と AZ | 1 台。サブネット a。AZ を選ぶキーは無い | `terraform/base/core/web.tf` の `aws_instance.web` |
| インスタンス | t4g.small、Amazon Linux 2023（arm64。AMI は SSM の公開パラメータから読む）、gp3 8 GB | `terraform/base/core/web.tf`、変数 `instance_type`、`ami_ssm_parameter` |
| メタデータ | IMDSv2 だけ、ホップ数 1 | `terraform/base/core/web.tf` |
| 画面 | Gradio が `127.0.0.1:8080` だけで待つ。systemd のユニット `<prefix>-web` | `web/app.py`、`terraform/base/core/templates/web_user_data.sh.tftpl` |
| 画面のコード | 共有バケット `<prefix>-kb-<アカウント>` の `web/` から起動時に取る | `ops/up.sh` の手順 4、`terraform/base/core/bucket.tf` |
| ロール | `<prefix>-web`。SSM の管理、`web/*` の読み取り、`/<prefix>/*` の `ssm:GetParameter`。Runtime と Gateway を呼ぶ許可は agent と workflow のルートが足す | `terraform/base/core/web.tf`、`terraform/agent/runtime.tf`、`terraform/workflow/iam.tf` |
| スイッチ | 無い（土台なので必ず作る）。PC 側のポートは `LOCAL_PORT`（既定 8080）、`NO_DASHBOARD_PORTFORWARD=1` で最後のポートフォワーディングを開かない | [deploy.md](../../deploy.md) の「`deploy.env` のキー」 |
| 費用 | 2.2 セント/時（t4g.small。土台は合わせて約 2 セント/時 + エンドポイント） | `ops/up.sh` の先頭のコメント |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| 利用者の PC | PC → Web | SSM のポートフォワーディング（SSM Agent が内側から ssmmessages へつなぐ）。`localhost:8080` |
| AgentCore Runtime | Web → Runtime | `InvokeAgentRuntime`（bedrock-agentcore のエンドポイント、IAM）。ARN は SSM `/<prefix>/runtime-arn` |
| AgentCore Gateway | Web → Gateway | `InvokeGateway`（承認タブとトポロジのツール）。URL は SSM `/<prefix>/gateway-url` |
| Neptune Analytics | Web → グラフ | neptune-graph-data のエンドポイント、SigV4（トポロジの表示と、最初の投入） |
| Nautobot | Web → Nautobot | 8080/tcp（トポロジの編集を REST API へ。SSM `/<prefix>/nautobot/url` があるあいだ） |
| Grafana / Splunk / Temporal UI / Nautobot | PC → Web → 各タスク | 3000 / 8000 / 8233 / 8080（Web を踏み台にしたポートフォワーディング） |
| S3 | Web → バケット | gateway 型エンドポイント（`web/` の取得と dnf） |

## 知見

- **Web は ECS にせず EC2 のままにしている。**
  踏み台を兼ねるため。ECS にするとタスクの IP が変わり、踏み台にしにくい。
  出典: [core.md](../core.md) の先頭。
- **EC2 に置くのは画面だけで、エージェント本体（`agent/app.py`）は置かない。**
  モデルを呼ぶのは Runtime。Web のログに `KeyError: 'MODEL_ID'` や Web のロールでの `bedrock:InvokeModel` の `AccessDenied` が出たら、置くファイルを間違えている。Web のロールに権限を足して直さない。
  出典: [troubleshooting.md](../../troubleshooting.md) の「チャットの答えがおかしい」、[README.md](../README.md) の「どのファイルがどこで動くか」。
- **Runtime の ARN は SSM から 60 秒ごとに読む。**
  agent をあとから足しても入れ替えても、Web の EC2 を作り直さずに済む。
  出典: `terraform/base/core/web.tf` の `web_assets` のコメント。
- **user_data を変えるとインスタンスは作り直しになる。**
  `user_data_replace_on_change`。画面のコードだけを変えたときは、`ops/up.sh` の手順 4 が S3 に置き直して EC2 を再起動する。
  出典: `terraform/base/core/web.tf`、`ops/up.sh` の手順 4-2 と 4-4。
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
| 認証 | 画面にログインは無い。入れる人は SSM の `start-session` を打てる IAM の利用者（[deploy.md](../../deploy.md) の「利用者に画面を渡す」） |

## 関連

- [core.md](../core.md)、[agent.md](../agent.md): 土台とチャットの経路
- [deploy.md](../../deploy.md): 利用者に画面を渡す
- [development.md](../../development.md): Web を手元で動かす
- [agentcore-bedrock.md](agentcore-bedrock.md)、[vpc-perimeter.md](vpc-perimeter.md)
