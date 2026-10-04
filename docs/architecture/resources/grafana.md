# Grafana（ECS）

← [リソースごとの知見](README.md)

## ひとことで

Prometheus のメトリクスと OpenSearch のログを見る画面で、異常を見つけて SNS に知らせる送り手の 1 つ。
Grafana OSS を Fargate のタスク 1 つで動かしている。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| サービス | `<prefix>-grafana`。クラスター `<prefix>-analytics`。1 タスク、サブネット a。AZ を選ぶキーは無い | `terraform/pipeline/analytics/grafana.tf` |
| タスクの大きさ | Fargate ARM、0.5 vCPU / 1 GB | 変数 `grafana_task_cpu`、`grafana_task_memory` |
| イメージ | Grafana OSS 13.2.2 にデータソースの plugin と provisioning を焼き込んだもの。ECR の `<prefix>-grafana` | `grafana/Dockerfile`、変数 `grafana_image_tag` |
| 名前 | Cloud Map `grafana.<prefix>.internal:3000` | `grafana.tf` |
| データソース | Prometheus（Amazon Managed Prometheus）と OpenSearch Serverless（`snmp-logs`）。どちらも SigV4（タスクロール） | `grafana/provisioning`、`grafana/start.sh` |
| アラートルール | 4 本（フォルダ `nwc-alerts`）。1 分ごとに評価、`for: 0s` | `grafana/provisioning/alerting/netops-prometheus.yaml`、`netops-opensearch.yaml` |
| 送り先 | SNS `<prefix>-alerts`。解消は 30 秒以内（`group_interval`）、直らないあいだは 4 時間ごとに送り直す（`repeat_interval`） | `grafana/provisioning/alerting/netops.yaml` |
| admin のパスワード | SSM の SecureString `/<prefix>/grafana/admin-password`（`ops/up.sh` が乱数で作る） | `ops/up.sh`、`grafana.tf` の `secrets` |
| ログ | `/ecs/<prefix>-grafana` | `grafana.tf` |
| スイッチ | `STORES` の `grafana`（Grafana だけを切り替えるキーは無い） | `deploy.env.example` |
| 費用 | 2 セント/時（`STORES` の `grafana` 全体では約 +$0.60/h） | `ops/up.sh` の先頭のコメント、`deploy.env.example` |

ルールと中身:

| ルール | 見るもの | 出すもの |
|---|---|---|
| `link_down` | Prometheus の `snmp_interface_ifOperStatus`（SNMP のポーリング） | down の IF で `firing`。ループバック、管理ポート、サブインタフェース、admin-state が disable のポートは外す |
| `bgp_down` | Prometheus の `snmp_bgp_neighbor_session_up` | 0 で `firing`。対象は相手の IP |
| `isis_down` | Prometheus の `snmp_isis_interface_oper_up` | 0 で `firing`。対象はサブインタフェース |
| `trap` | OpenSearch の `snmp_trap`（過去 10 分を機器と OID ごとに数える） | 1 通以上で `firing`、10 分来なければ `resolved`。linkDown / linkUp などは数えない |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| 利用者の PC | PC → Web の EC2 → Grafana | 3000/tcp。SSM のポートフォワーディング（output `grafana_port_forward_command`）。ユーザー admin |
| Amazon Managed Prometheus | Grafana → ワークスペース | `aps-workspaces` のエンドポイント、SigV4 |
| OpenSearch Serverless | Grafana → `snmp-logs` | OpenSearch Serverless の VPC エンドポイント、SigV4。読める範囲はデータアクセスポリシーで絞る |
| SNS | Grafana → `<prefix>-alerts` | `sns` のエンドポイント、タスクロールの `sns:Publish` だけ |
| SSM | タスクの起動時に読む | 実行ロール `<prefix>-grafana-exec` |

## 知見

- **Amazon Managed Grafana は使えない。**
  サインインに IAM Identity Center か SAML の IdP が要り、このアカウントには Organizations も Identity Center も無い。
  出典: [pipeline.md](../../pipeline.md) の「Grafana と Splunk を開く」。
- **OpenSearch Serverless の Dashboards も使えない。**
  VPC エンドポイントだけのコレクションには届かない。それで Grafana を立てている。
  出典: `terraform/pipeline/analytics/grafana.tf` の先頭のコメント。
- **タスクは 1 つだけにしている。増やすと通知が二重になる。**
  アラートルールの評価もタスクの中で動く。HA を組まずに複数台にすると、全部の台が全ルールを評価する。設定もタスクの中の SQLite でタスクごとに別々。2 つにして AWS で試してはいない。
  出典: `grafana.tf` のコメント（Grafana の文書「Configure high availability」、https://grafana.com/docs/grafana/latest/alerting/set-up/configure-high-availability/ 、2026-10-04 確認）。
- **plugin はイメージに焼き込む。**
  AWS の外へ出る経路が無いので、起動時に落とせない。
  出典: [pipeline.md](../../pipeline.md) の「Grafana と Splunk を開く」。
- **UI で変えたものは、タスクと一緒に消える。**
  ダッシュボードもルールも provisioning だけ。provisioning したルール・連絡先・ポリシーは画面から変えられない。残すなら `grafana/provisioning` に書いて `ops/up.sh`（イメージから作り直す）。
  出典: [pipeline.md](../../pipeline.md) の「Grafana と Splunk を開く」「Grafana のアラート」。
- **`netops.yaml` のテンプレートの `$` はそのまま書く。**
  `$$` とエスケープすると Grafana が起動しない（`Invalid format of the submitted template`。13.2.2 で実測）。
  出典: [pipeline.md](../../pipeline.md) の「Grafana のアラート」。
- **データが無いときの扱いは、ルールで分けてある。**
  Prometheus の 3 本は直前の状態のまま（`KeepLast`。分からないときに発火も解消もしない）。`trap` は NoData を OK にする（`KeepLast` だと発火したまま解消しない）。
  出典: [pipeline.md](../../pipeline.md) の「Grafana のアラート」。
- **linkDown / linkUp の trap から `link_down` を出すのは Splunk だけ。**
  IF 名の入った varbind の名前が IF ごとに変わり、OpenSearch の集計では取り出せない。
  出典: [pipeline.md](../../pipeline.md) の「アラート」。
- **`resolved` の通知も、発火したときの `starts_at` と `detail` のまま来る。**
  解消かどうかは `status` で見る。
  出典: [alert-comparison.md](../../alert-comparison.md) の「Grafana の側」。
- **落ちてから通知までは、Spark のマイクロバッチ 60 秒 + ルールの評価（1 分ごと）のぶん遅れる。**
  出典: [pipeline.md](../../pipeline.md) の「アラート」の表。
- **パスワードはタスクの起動時に読む。**
  SSM の値を変えたら、サービスを作り直さないと効かない。
  出典: `grafana.tf` の `secrets`。
- **`readonlyRootFilesystem` は付けていない。**
  Grafana は `/var/lib/grafana` に SQLite を、`grafana/start.sh` は `/tmp` に provisioning を書く。
  出典: `grafana.tf` のコメント。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| 4 本になったあとの形（Splunk と Grafana のアラートを比べる（002）） | AWS の上では未確認 |
| OpenSearch Serverless を SigV4 でルールの評価に使えるか | 未確認（ダッシュボードで読めることは 2026-09-28 に確認済み） |
| 機器ごと止まったとき | 検知しない（系列が途切れると解消を送る） |
| `SNMP_POLL=0` | `link_down` は発火も解消もしない |
| 1 タスク・1 AZ | 止まっているあいだはルールが評価されない |

## 関連

- [prometheus.md](prometheus.md)、[opensearch-serverless.md](opensearch-serverless.md)、[sns-sqs-lambda.md](sns-sqs-lambda.md)、[splunk.md](splunk.md)
- [pipeline.md](../../pipeline.md): 「Grafana と Splunk を開く」「アラート」「Grafana のアラート」
- [alert-comparison.md](../../alert-comparison.md): Splunk と Grafana のアラートを比べる（002）の結果
