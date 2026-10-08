# Grafana（ECS）

← [リソースごとの知見](README.md)

## ひとことで

Prometheus のメトリクスと OpenSearch のログを見る画面で、異常を見つけて SNS に知らせる送り手の 1 つ。
Grafana OSS を Fargate のタスク 1 つで動かしている。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| サービス | `<prefix>-grafana`。クラスター `<prefix>-analytics`。1 タスク、サブネット a。AZ を選ぶキーは無い | `IaC/terraform/aws-managed/pipeline/analytics/grafana.tf` |
| タスクの大きさ | Fargate ARM、0.5 vCPU / 1 GB | 変数 `grafana_task_cpu`、`grafana_task_memory` |
| イメージ | Grafana OSS 13.2.3 にデータソースの plugin（amazonprometheus 3.2.0、opensearch 2.34.4。版は固定）と provisioning を焼き込んだもの。ECR の `<prefix>-grafana` | `docker/images/grafana/Dockerfile`、変数 `grafana_image_tag` |
| 名前 | Cloud Map `grafana.<prefix>.internal:3000` | `grafana.tf` |
| データソース | Prometheus（Amazon Managed Prometheus）と OpenSearch Serverless（`snmp-logs`）。どちらも SigV4（タスクロール） | `app/grafana/provisioning`、`app/grafana/start.sh` |
| アラートルール | 4 本（フォルダ `nwc-alerts`）。1 分ごとに評価、`for: 0s` | `app/grafana/provisioning/alerting/netops-prometheus.yaml`、`netops-opensearch.yaml` |
| 送り先 | SNS `<prefix>-alerts`。解消は 30 秒以内（`group_interval`）、直らないあいだは 4 時間ごとに送り直す（`repeat_interval`） | `app/grafana/provisioning/alerting/netops.yaml` |
| admin のパスワード | SSM の SecureString `/<prefix>/grafana/admin-password`（`ops/up.sh` が乱数で作る） | `ops/up.sh`、`grafana.tf` の `secrets` |
| ログ | `/ecs/<prefix>-grafana` | `grafana.tf` |
| スイッチ | `STORES` の `grafana`（Grafana だけを切り替えるキーは無い） | `deploy.env.example` |
| 費用 | 2 セント/時（`STORES` の `grafana` 全体では約 +$0.60/h） | `ops/up.sh` の費用の目安（手順 0 の終わりのコメントと `COST_CENTS`）、`deploy.env.example` の `STORES` |

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
  出典: `IaC/terraform/aws-managed/pipeline/analytics/grafana.tf` の先頭のコメント。
- **タスクは 1 つだけにしている。増やすと通知が二重になる。**
  アラートルールの評価もタスクの中で動く。HA を組まずに複数台にすると、全部の台が全ルールを評価する。設定もタスクの中の SQLite でタスクごとに別々。2 つにして AWS で試してはいない。
  出典: `grafana.tf` のコメント（Grafana の文書「Configure high availability」、https://grafana.com/docs/grafana/latest/alerting/set-up/configure-high-availability/ 、2026-10-04 確認）。
- **plugin はイメージに焼き込む。**
  AWS の外へ出る経路が無いので、起動時に落とせない。
  出典: [pipeline.md](../../pipeline.md) の「Grafana と Splunk を開く」。
- **UI で変えたものは、タスクと一緒に消える。**
  ダッシュボードもルールも provisioning だけ。provisioning したルール・連絡先・ポリシーは画面から変えられない。残すなら `app/grafana/provisioning` に書いて `ops/up.sh`（イメージから作り直す）。
  出典: [pipeline.md](../../pipeline.md) の「Grafana と Splunk を開く」「Grafana のアラート」。
- **`netops.yaml` のテンプレートの `$` はそのまま書く。**
  `$$` とエスケープすると Grafana が起動しない（`Invalid format of the submitted template`。13.2.2 で実測）。
  出典: [pipeline.md](../../pipeline.md) の「Grafana のアラート」。
- **データが無いときの扱いは、ルールで分けてある。**
  Prometheus の 3 本は直前の状態のまま（`KeepLast`。分からないときに発火も解消もしない）。`trap` は NoData を OK にする（`KeepLast` だと発火したまま解消しない）。
  出典: [pipeline.md](../../pipeline.md) の「Grafana のアラート」。
- **評価がエラーでも、画面のルールは Normal に見える。ルールの API で確かめる。**
  4 本とも `execErrState: KeepLast` なので、クエリが失敗してもアラートは出ない。エラーはルールの API（`/api/prometheus/grafana/api/v1/rules`）の `alerts[].state` に `Normal (Error, KeepLast)`（発火中の系列なら `Alerting (Error, KeepLast)`）と出るだけで、`health` は `ok`、`lastError` は空のまま（13.2.2 で実測）。インデックスが無いだけなら NoData で、エラーにはならない。
  それで `ops/up.sh`（OSS 版は `oss/ops/up.sh`）の手順 9-2 と `ops/check-grafana.sh [--oss]` が、Web の EC2 から SSM Run Command でこの API を読み、`(Error` を含むか `Error` で始まる状態、`health=error`、空でない `lastError` のどれかがあるルールを NG にする（`ops/grafana_rules_check.py`）。API がルールのグループをページに分けて返したとき（`data.groupNextToken`）は、`group_next_token` で最後のページまで読む。13.2.3 の既定は `group_limit=-1`（全部を 1 ページで返す）なので、今はトークンは来ない。同じトークンが繰り返すか 100 ページを超えたら読めなかったとして扱う（待ち切れたら未確認）。判定に使うのは、打ってから全部のルールがもう 1 回評価された結果（`lastEvaluation` が変わったもの）。打ったときに見える評価は、データソースを直す前・壊れる前のものかもしれない。そのうえで、エラーのあったルールはもう 1 回評価されるのを待ち、そこでもエラーなら NG にする。エラーの評価が作った状態（ラベルがルールのラベルだけのもの）は、直したあとの成功した評価に 1 回分残り、その次の評価で消える（Grafana の stale の扱い。13.2.2 で実測: 14:12:11 に直し、14:13:00 の評価は `Alerting` と `Normal (Error, KeepLast)` が並び、14:14:00 の評価で `Alerting` だけになった）。それで NG は OK より 1 分ほど遅く出る。結果は 0 OK / 1 NG / 2 未確認 / 3 確かめる前に止まった（`ops/check-grafana.sh` の終了コード。[troubleshooting.md](../../troubleshooting.md) の「`ops/check-grafana.sh` の終了コード」）。up.sh は NG でも未確認でも止めず、黄色の警告を最後にもう一度出す。ログの案内は NG のときだけ（未確認は評価のエラーとは限らない）。
  ルールのクエリを `/api/ds/query` に投げて HTTP 200 を見る方法は取らなかった。スケジューラーの実際の評価ではなくクエリ A だけを別の時間範囲で投げ直すことになり、ルールを足すたびに確かめる側も足す必要がある。API の GET 1 回なら、今あるルールも後で足すルールも同じに見られる。
  理由の文は API に残らないので、ログ（`/ecs/<prefix>-grafana` の `Failed to evaluate rule`）で見る。この行は Grafana がやり直すエラーの 1・2 回目（3 回まで）に出る（13.2.2 で実測）。
  出典: `ops/grafana_rules_check.py` の先頭のコメント、`ops/up-common.sh` の `grafana_rules_step`。
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
  Grafana は `/var/lib/grafana` に SQLite を、`app/grafana/start.sh` は `/tmp` に provisioning を書く。
  出典: `grafana.tf` のコメント。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| 4 本になったあとの形（Splunk と Grafana のアラートを比べる（002）） | 2026-10-05 に AWS で `link_down` と `isis_down` の発火を確かめた（`sudo lab fail-main`）。`bgp_down` と `trap` の発火、Grafana の画面は未確認 |
| OpenSearch Serverless を SigV4 でルールの評価に使えるか | 未確認（ダッシュボードで読めることは 2026-09-28 に確認済み） |
| 機器ごと止まったとき | 検知しない（系列が途切れると解消を送る） |
| `SNMP_POLL=0` | `link_down` は発火も解消もしない |
| 1 タスク・1 AZ | 止まっているあいだはルールが評価されない |
| ルールの評価のエラーの確かめ（手順 9-2、`ops/check-grafana.sh`） | 打ったあとの評価だけを見る（あとで壊れたら打ち直す）。打ってから全部のルールが評価されるまで最大 1 分、エラーがあればもう 1 回の評価まで 1 分ほど延びる。打ったあとの評価で 1 回でもエラーになったルールは、すぐ直っても NG になる（次の評価にエラーが残るため）。NoData はエラーではないので OK になる。待つのは最大 5 分で、評価されないルール（止めたルールなど）があれば「未確認」。SSM Run Command の結果を待つのは `SSM_RUN_WAIT` 秒（既定 1800）までで、過ぎたら未確認。マネージド版は `STORES` に `grafana` があるときと、今回は analytics を作らない回（`PIPELINE=0`・`SKIP_ANALYTICS=1`）でも前の回の Grafana の ECS サービスが state に残っているときに打つ。analytics の state の一覧か、Grafana のクラスターとサービスの名前（`tf output`）が読めないか空なら、確かめず（`aws ecs wait` も打たず）に黄色の警告を出して先へ進む（最後の案内まで届かせる）。AWS では未実行（API の形とログは手元の 13.2.2 で確かめた） |
| Grafana がやり直さないエラーでも `Failed to evaluate rule` がログに出るか | 未確認。出なければ `--filter-pattern '"level=error"'` で探す |

OSS 版（`IaC/terraform/oss/pipeline/analytics/grafana.tf`）も同じイメージとルールで 1 タスク立てる。データソースだけが VictoriaMetrics の vmselect（署名なし）と ECS の OpenSearch（Basic 認証）に変わる（`app/grafana/provisioning/datasources-oss`。uid が同じなので、ダッシュボードとアラートルールはそのまま使う）。[oss-variant.md](../../oss-variant.md)。

## 関連

- [prometheus.md](prometheus.md)、[opensearch-serverless.md](opensearch-serverless.md)、[sns-sqs-lambda.md](sns-sqs-lambda.md)、[splunk.md](splunk.md)
- [pipeline.md](../../pipeline.md): 「Grafana と Splunk を開く」「アラート」「Grafana のアラート」
- [alert-comparison.md](../../alert-comparison.md): Splunk と Grafana のアラートを比べる（002）の結果
