# SSM Parameter Store

← [リソースごとの知見](README.md)

## ひとことで

2 つの使い方をしている。terraform のルートをまたいで渡す値（String）の置き場と、シークレット（SecureString）の置き場。
シークレットは Terraform でなく `ops/up.sh` が作る。値を Terraform の state に載せないため。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| 名前 | どれも `/<prefix>/…` の下 | 各ルート、`ops/up.sh` |
| String | Terraform の各ルートが作る（下の表） | 各ルートの `aws_ssm_parameter` |
| SecureString | `ops/up.sh` の `ensure_secret`（乱数）と `ensure_fixed_secret`（決まった値）が作る。タグ `ManagedBy=ops/up.sh` | `ops/up.sh` |
| 消す | `ops/down.sh` の手順 5-2 が、タグ `ManagedBy=ops/up.sh` の付いたものだけ消す | `ops/down.sh` |
| エンドポイント | `ssm`（土台の分。`ssmmessages` と一緒にいつも作る） | `ops/up.sh` の手順 0 |
| 費用 | エンドポイントが 1 本 1.4 セント/時 × `ENDPOINTS_AZ_NUM` | `ops/up.sh` のコメント |

String（ルートをまたいで渡す値）:

| 名前 | 中身 | 書く側 | 読む側 |
|---|---|---|---|
| `/<prefix>/runtime-arn` | AgentCore Runtime の ARN | agent | Web の EC2（60 秒キャッシュ） |
| `/<prefix>/gateway-url` | AgentCore Gateway の URL | workflow | Runtime |
| `/<prefix>/msk-bootstrap` | MSK のブローカーの一覧 | stream | だれも読まない。手で確かめるときと手動構築のために残してある（Telegraf は環境変数、Spark はジョブの引数でもらう。[msk.md](msk.md)） |
| `/<prefix>/telegraf-address` | Telegraf の内部 NLB のアドレス | stream | lab の EC2（`lab forward`） |
| `/<prefix>/telegraf-source-cidr` | 取りにいく側のタスクのサブネットの CIDR | stream | lab の EC2（`lab forward`） |
| `/<prefix>/telegraf-dialin/<lab か nautobot>/gnmi-targets`、`snmp-agents` | Telegraf が取りにいく機器の一覧 | stream（最初の値）。`nautobot` のほうは Nautobot の Job が書き換える | Telegraf の取りにいく側（ECS の secrets） |
| `/<prefix>/neptune-graph-id` | Neptune Analytics のグラフの ID | graph | Runtime、Web、Lambda、worker |
| `/<prefix>/nautobot/url` | Nautobot の URL | nautobot | Web の EC2 |

SecureString（`ops/up.sh` が作る）:

| 名前 | 中身 | 作り方 | 受け取る側 |
|---|---|---|---|
| `/<prefix>/nautobot/secret-key`、`admin-password`、`db-password`、`api-token` | Django の SECRET_KEY、admin のパスワード、DB のパスワード、Web が使う API のトークン | 乱数 | Nautobot のタスク（ECS の secrets）、RDS（`db-password`） |
| `/<prefix>/grafana/admin-password` | Grafana の admin のパスワード | 乱数（`STORES` に `grafana` があるとき） | Grafana のタスク |
| `/<prefix>/splunk/admin-password`、`hec-token` | Splunk の admin のパスワード、HEC の token | 乱数、uuid（`STORES` に `splunk` があるとき） | Splunk のタスク |
| `/<prefix>/splunk/idxc-secret` | Splunk のクラスターの合言葉（cluster manager・indexer・search head が互いを確かめる） | 乱数（`SPLUNK_AZ_NUM` が 2 か 3 のとき） | Splunk のタスク（どの役割も同じ値） |
| `/<prefix>/telegraf-dialin/gnmi-username`、`gnmi-password`、`snmp-community` | 機器の gNMI と SNMP の認証情報 | 決まった値（containerlab の既定値を最初の値にする） | Telegraf の取りにいく側のタスク |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| ECS のタスク（Telegraf、Grafana、Splunk、Nautobot） | タスクの起動時に ECS が読む | タスク定義の `secrets`（`valueFrom` にパラメータの ARN） |
| Web の EC2、Runtime、Lambda、worker、lab の EC2 | 各自 → パラメータ | `ssm` のエンドポイント、それぞれのロール（`/<prefix>/*` か、名前を絞った許可） |
| RDS（Nautobot の DB） | Terraform → RDS | ephemeral で読み、write-only の引数（`password_wo`）に渡す |
| Nautobot の Job | Job → `telegraf-dialin/nautobot/*` | `ssm` のエンドポイント、タスクロール |
| `ops/up.sh`、`ops/down.sh` | PC → SSM | デプロイする人の認証。値は画面にもログにも出さない |

## 知見

- **シークレットを `ops/up.sh` が作るのは、Terraform の state に値を載せないため。**
  Terraform は名前（ARN）だけを扱い、タスクは ECS の secrets で受ける。
  出典: `ops/down.sh` の手順 5-2 のコメント、`ops/up.sh` のコメント。
- **もうあれば作り直さない。**
  `ops/up.sh` を打ち直してもパスワードは変わらない。機器の認証情報を手で書き換えた値も残る。型が SecureString でなければ止まる。
  出典: `ops/up.sh` の `ensure_secret`、`ensure_fixed_secret`。
- **タスクは起動のときに値を読む。SSM を手で変えたら、サービスを作り直す。**
  `aws ecs update-service --force-new-deployment`。
  出典: [troubleshooting.md](../../troubleshooting.md) の「パイプラインと WORKFLOW」。
- **ARN や ID を SSM で渡すと、あとから作ったルートを、先に作ったものを作り直さずにつなげる。**
  agent を後から作っても消しても Web の EC2 を作り直さない。stream を後から作っても lab の EC2 を作り直さない。
  出典: `terraform/agent/runtime.tf` と `terraform/pipeline/lab/telegraf.tf` のコメント。
- **Telegraf の機器の一覧は、Nautobot があるときは Terraform が値の変化を見ない。**
  最初の値は lab の定義から入れ、あとは Nautobot の Job が書き換えてサービスを作り直す（`ignore_changes`）。Nautobot が無い回は `…/lab/…` の名前で Terraform が値を持つ。
  出典: `terraform/pipeline/stream/telegraf.tf` のコメント。
- **DB のパスワードは plan にも state にも残らない。**
  Terraform は ephemeral で読み、`password_wo` に渡す。
  出典: `terraform/pipeline/nautobot/database.tf` のコメント。
- **`ops/down.sh` は、nautobot のルートが消えなかったときは `/<prefix>/nautobot/` の下を残す。**
  RDS のパスワードを Terraform が destroy でも読むので、消すと打ち直しても消せなくなる。次の `ops/down.sh` で消す。
  出典: `ops/down.sh` の手順 5-2 のコメント。
- **手で入れたパラメータは `ops/down.sh` で消えない。**
  消すのはタグ `ManagedBy=ops/up.sh` の付いたものだけ。String は各ルートの destroy で消える。
  出典: `ops/down.sh` の手順 5-2 のコメント。
- **パスワードの見方は、各ルートの出力のコマンド。**
  Grafana なら `grafana_password_command`。
  出典: [troubleshooting.md](../../troubleshooting.md) の「パイプラインと WORKFLOW」、[pipeline.md](../../pipeline.md) の「Grafana と Splunk を開く」。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| シークレットの入れ替え（ローテーション） | 仕組みは作っていない。手で変えたらサービスを作り直す |
| Secrets Manager | 使っていない（シークレットは SSM の SecureString に置く決まり） |
| 機器の認証情報 | lab では containerlab の既定値。本番の機器につなぐときは SSM の値を書き換える（`ops/up.sh` は、あれば触らない） |

## 関連

- [nautobot.md](nautobot.md)、[telegraf.md](telegraf.md)、[grafana.md](grafana.md)、[splunk.md](splunk.md)、[web-ec2.md](web-ec2.md)、[vpc-perimeter.md](vpc-perimeter.md)
- [deploy.md](../../deploy.md): 「`ops/up.sh` がすること」「`ops/down.sh` がすること」
- [data-stores.md](../../data-stores.md): 「15. ブローカーの渡し方と msk-bootstrap」
