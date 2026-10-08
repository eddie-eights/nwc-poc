# ECR

← [リソースごとの知見](README.md)

## ひとことで

コンテナイメージの置き場。自分でビルドしたものも、公開のイメージの写し（ミラー）も、全部ここに置く。
VPC から外へ出る経路が無いので、Fargate も EC2 も Runtime も、イメージは ECR からしか引けない。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| リポジトリ | 11 個（下の表）。時間課金が無いので、スイッチに関わらずいつも作る。OSS 版（`IaC/terraform/oss/`、`project = nwc-oss`）はこれに 7 個を足す（下の「OSS 版だけのリポジトリ」） | `IaC/terraform/aws-managed/base/ecr/main.tf` |
| タグ | `IMMUTABLE`（同じタグに上書きできない） | `main.tf` |
| 消し方 | `force_delete = true`。destroy でイメージごと消える | `main.tf` |
| スキャン | `scan_on_push = true` | `main.tf` |
| 作る順 | `ops/up.sh` の手順 1 でリポジトリ、手順 2 でイメージ（ECR に無いタグだけ） | `ops/up.sh` |
| 残す | `KEEP_ECR=1 ops/down.sh` で ECR だけ残す（既定は 0 で消す） | `ops/down.sh`、`deploy.env.example` |
| エンドポイント | `ecr.api`、`ecr.dkr`（レイヤーは S3 の gateway エンドポイントから取る） | `ops/up.sh` の手順 0 |
| 費用 | リポジトリに時間課金は無い。エンドポイントが 1 本 1.4 セント/時 × `ENDPOINTS_AZ_NUM` | `main.tf` と `ops/up.sh` のコメント |

リポジトリと中身:

| リポジトリ（`<prefix>-…`） | 元 | 動く場所 | タグ |
|---|---|---|---|
| `agent` | `app/agentcore/`（自前ビルド） | AgentCore Runtime | `IMAGE_TAG`（既定 `v1`） |
| `worker` | `app/temporal/`（自前ビルド） | ECS Fargate（workflow） | `IMAGE_TAG` |
| `temporal` | `temporalio/temporal`（写し） | ECS Fargate（workflow） | 上流の版（`ops/up-common.sh` の `TEMPORAL_TAG`） |
| `lab-srlinux` | `ghcr.io/nokia/srlinux`（写し。約 1 GB） | lab の EC2 | 上流の版（`ops/lab-common.sh` の `SRLINUX_TAG`） |
| `lab-multitool` | `ghcr.io/srl-labs/network-multitool`（写し） | lab の EC2 | 上流の版（`MULTITOOL_TAG`） |
| `telegraf` | 公式の `telegraf` に設定のテンプレートと `tg` を足す | ECS Fargate（stream） | `<版>-<ディレクトリの中身のハッシュ 12 桁>` |
| `kafka-ui` | `ghcr.io/kafbat/kafka-ui`（写し） | ECS Fargate（stream） | 上流の版（`ops/up.sh` の `KAFKA_UI_TAG`） |
| `grafana` | 公式の Grafana OSS に plugin と provisioning を焼き込む | ECS Fargate（analytics） | 同上 |
| `splunk` | 公式の `splunk/splunk` に検知のアプリと入口のスクリプトを足す（amd64 だけ、約 2〜3 GB） | ECS Fargate x86（analytics） | 同上 |
| `nautobot` | 公式の Nautobot に Job などを足す | ECS Fargate（nautobot） | 同上（ハッシュは `app/nautobot/` に `app/agentcore/graph.py`・`app/agentcore/toolkit.py` と lab の定義の seed を足したビルドの材料から作る） |
| `redis` | 公式の redis（写し） | Nautobot のタスクの中 | 上流の版（`ops/up-common.sh` の `REDIS_TAG`） |

OSS 版だけのリポジトリ（`oss_repositories`。マネージド版では作らない）: `kafka` / `opensearch` / `vminsert` / `vmselect` / `vmstorage`（公開イメージの写し）と `spark` / `neo4j`（ops がビルドする）。

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| デプロイする人の PC | PC → ECR | `docker buildx build --push` と写しの push。PC の認証 |
| ECS のタスク | Fargate → ECR | `ecr.api`、`ecr.dkr` のエンドポイント、タスク実行ロール |
| lab の EC2 | EC2 → ECR | 同じエンドポイント、インスタンスロール |
| AgentCore Runtime | Runtime → ECR | 同じエンドポイント、実行ロール |

## 知見

- **公開のイメージも ECR に写す。**
  Fargate はプライベートのネットワークから Docker Hub を引けないため。
  出典: [data-stores.md](../../data-stores.md) の「7. イメージと動く場所」。
- **コードを変えたら、タグを進める。**
  リポジトリが `IMMUTABLE` なので、同じタグには push できない。自前ビルドの `agent` と `worker` は `IMAGE_TAG` を変える。
  出典: [data-stores.md](../../data-stores.md) の「9. タグ」、`IaC/terraform/aws-managed/base/ecr/main.tf` のコメント。
- **`telegraf` / `grafana` / `splunk` / `nautobot` は、中身を変えれば自動でタグが変わる。**
  タグにディレクトリの中身のハッシュが入る（`ops/lab-common.sh` の `dir_tag`）。`IMAGE_TAG` を上げなくてよい。
  出典: [data-stores.md](../../data-stores.md) の「9. タグ」、`ops/up.sh` のコメント。
- **`ops/up.sh` は、ECR にそのタグが無いときだけビルドして push する。**
  `ecr_has` で見る。
  出典: [data-stores.md](../../data-stores.md) の「9. タグ」、`ops/lab-common.sh`。
- **AgentCore Runtime は linux/arm64 のイメージしか動かせない。**
  x86_64 でビルドしたイメージは起動しない。ほかも arm64 に揃えてある。
  出典: [data-stores.md](../../data-stores.md) の「8. arm64 に揃える（Splunk だけ x86）」。
- **例外は `splunk`。公式イメージが amd64 しか無い。**
  そのタスクだけ `X86_64` にし、`--platform linux/amd64` で作る。公式イメージに COPY するだけなので、arm64 の PC でもエミュレーション無しで作れる。
  出典: 同上。
- **写しの push で「only the available single-platform image was pushed」と出ても問題ない。**
  arm64 だけ push したという意味。
  出典: 同上。
- **`KEEP_ECR=1` で残すと、翌日の `ops/up.sh` でビルドを飛ばせる。**
  保管料は 7.39 GB（11 リポジトリ）で月 約 110 円（2026-10-08 の実測）。
  出典: [deploy.md](../../deploy.md) の「消したあとに残るもの」、`ops/down.sh` の先頭のコメント。
- **`docker pull` がタイムアウトするときは、エンドポイントを見る。**
  `ecr.api` / `ecr.dkr` が `ops/up.sh` の手順 0 の一覧にあるか、S3 の gateway エンドポイントがプライベートのルートテーブルに載っているか。`explicit deny` なら VPC のエンドポイントを通っていない。
  出典: [pipeline.md](../../pipeline.md) の「動かないとき」。
- **デバッグ用の EC2 のイメージは、別のリポジトリに置く。**
  `<prefix>-debug-lab-srlinux` / `-debug-lab-multitool` / `-debug-telegraf`（CloudFormation のスタックが作る）。SR Linux は `ops/up.sh` で置いてあっても、もう一度 push する。
  出典: [pipeline.md](../../pipeline.md) の「デバッグ用の EC2（lab + Telegraf を 1 台）」。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| ライフサイクルポリシー（古いイメージを消す） | `agent` だけにある（新しい 5 個を残す）。ほかの 10 個（OSS 版はさらに 7 個）には無い（`IaC/terraform/aws-managed/base/ecr/main.tf`） |
| スキャンの結果の扱い | リポジトリに記述が無い（push のときにスキャンが走る設定だけ） |

## 関連

- [lab-ec2.md](lab-ec2.md)、[telegraf.md](telegraf.md)、[grafana.md](grafana.md)、[splunk.md](splunk.md)、[nautobot.md](nautobot.md)、[temporal.md](temporal.md)、[agentcore-bedrock.md](agentcore-bedrock.md)
- [data-stores.md](../../data-stores.md): 「7. イメージと動く場所」〜「10. コードの入口」
- [core.md](../core.md): 土台
