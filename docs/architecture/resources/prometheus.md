# Amazon Managed Prometheus

← [リソースごとの知見](README.md)

## ひとことで

機器のメトリクスの時系列を置くワークスペース（Amazon Managed Service for Prometheus）。
書くのは Spark、読むのは Grafana（グラフとアラートルール）とエージェントの `query_metrics`。正本ではなく、見るための写し（正本は S3 Tables の `raw_telemetry`）。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| ワークスペース | alias `<prefix>-metrics`。1 つ | `terraform/pipeline/analytics/sinks.tf` の `aws_prometheus_workspace.metrics` |
| 入るもの | MSK の `metrics` / `gnmi` / `mdt` のトピックの数値。メトリクス名の頭に `snmp_` が付く（例: `snmp_interface_ifOperStatus`、`snmp_bgp_neighbor_session_up`、`snmp_isis_interface_oper_up`） | `spark/snmp_sinks.py` |
| ラベル | Telegraf の tags と `__name__` だけ。`event_id` と Kafka の位置は入れない | `spark/snmp_sinks.py` |
| 書き方 | Spark の remote write（protobuf + snappy を自前で組む）、SigV4 | `spark/snmp_sinks.py` |
| スイッチ | `STORES` の `grafana`（OpenSearch のログ用コレクションと Grafana と一緒に作る） | `deploy.env.example` |
| AZ | 選ぶものが無い（サービス側で動く）。入口は `aps-workspaces` のエンドポイント（`ENDPOINTS_AZ_NUM`） | `ops/up.sh` の `endpoints_for` |
| 費用 | ワークスペースは 0。取り込んだサンプル数と保存量の課金が別にある | `ops/up.sh` の先頭のコメント、`sinks.tf` のコメント |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| Spark（EMR Serverless） | Spark → ワークスペース | `aps-workspaces` のエンドポイント、remote write、SigV4（実行ロール） |
| Grafana | Grafana → ワークスペース | 同じエンドポイント、SigV4（タスクロール）。データソースとルール `link_down` / `bgp_down` / `isis_down` |
| tools の Lambda（`query_metrics`） | Lambda → ワークスペース | 同じエンドポイント、SigV4、PromQL |

## 知見

- **重複は入らない。何もしなくてよい。**
  系列（名前とラベル）と時刻が同じサンプルは 1 つしか持てない。Spark が同じバッチを送り直しても増えない。ただしこの動きは記憶から書いたもので、AWS では確かめていない（FAQ にそう書いてある）。
  出典: FAQ「OpenSearch と Prometheus でも、重複を防げる？ Grafana の側で落とすべき？」。
- **時刻が戻ったサンプルと古すぎるサンプルは 4xx で断られ、Spark は捨てる。**
  捨てた数は driver のログに出る。
  出典: [data-stores.md](../../data-stores.md) の「届け方の保証」。
- **`event_id` をラベルに入れない。**
  入れると 1 サンプルごとに別の系列になる。
  出典: `spark/snmp_sinks.py` のコメント。
- **BGP と IS-IS の状態は、Spark が 1 / 0 に直して入れる。**
  gNMI の `session_state` と `oper_state` は文字列。`idle` や `active` の区別は Prometheus には残らない（Splunk の `detail` で見る）。
  出典: [pipeline.md](../../pipeline.md) の「Grafana のアラート」、[alert-comparison.md](../../alert-comparison.md) の「Grafana の側」。
- **gNMI の系列には、Spark が device map で `sysName` のラベルを足す。**
  対象は `sysName` を持たないレコード全部。ラベルが増えると別の系列になるので、切り替えの前後をまたぐ期間は同じものが 2 本に見える。
  出典: [alert-comparison.md](../../alert-comparison.md) の「Spark の整形の副作用」。
- **gNMI の on_change は、変わったときにしか値が来ない。**
  ルールは `last_over_time(...[24h])` で最後の値を見る。落ちたまま 24 時間たつと系列が消えて解消が出る。
  出典: [alert-comparison.md](../../alert-comparison.md) の「Grafana の側」。
- **ワークスペースのリソースポリシーでは、VPC の外を拒んでいない。**
  Deny と `aws:SourceVpc` が効くか確かめられないため。IAM 側の Deny（`<prefix>-network-perimeter`）だけで止めている。
  出典: `terraform/pipeline/analytics/sinks.tf` のコメント。
- **`SNMP_POLL=0` では `snmp_interface_ifOperStatus` が無い。**
  ダッシュボード「netops / SNMP metrics」、`query_metrics`、ルール `link_down` が動かない。
  出典: FAQ「snmp ポーリングはデフォルトでは無効にして…」。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| サンプルあたりの単価 | リポジトリに数字が無い（`sinks.tf` に「確認は取れていない」とある） |
| リソースポリシーの Deny | 付けていない（上の知見） |
| 同じサンプルを 2 回受けたときの動き | AWS では確かめていない |

このあと変わる予定: OSS 版では Prometheus も ECS に立てる。[マネージドを OSS に置き換えた環境を作る（005）の設計](../../cycles/005-oss-on-ecs/design.md)。

## 関連

- [emr-serverless.md](emr-serverless.md)、[grafana.md](grafana.md)
- [data-stores.md](../../data-stores.md): 「届け方の保証」
- [pipeline.md](../../pipeline.md): 「Grafana のアラート」
