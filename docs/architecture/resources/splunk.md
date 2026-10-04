# Splunk（ECS）

← [リソースごとの知見](README.md)

## ひとことで

全部のトピックを入れて検索する画面で、異常を見つけて SNS に知らせる送り手の 1 つ。Grafana と比べるために置いてある。
Splunk Enterprise の公式イメージにアラートの app を足したものを、Fargate のタスク 1 つで動かしている。index はタスクと一緒に消える。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| サービス | `<prefix>-splunk`。クラスター `<prefix>-analytics`。1 タスク。AZ を選ぶキーは無い | `terraform/pipeline/analytics/splunk.tf` |
| タスクの大きさ | Fargate x86、2 vCPU / 4 GB、エフェメラルストレージ 40 GiB | 変数 `splunk_task_cpu`、`splunk_task_memory`、`splunk_ephemeral_storage_gib` |
| イメージ | Splunk Enterprise 10.4.3 の公式イメージ + app `netops_alerts`。ECR の `<prefix>-splunk` | `splunk/Dockerfile`、変数 `splunk_image_tag` |
| 名前とポート | Cloud Map `splunk.<prefix>.internal`。Web 8000（http）、HEC 8088（https、自己署名）、管理 API 8089（SG では開けていない） | `splunk.tf`、`terraform/base/core/security_groups.tf` |
| 入るもの | 5 つのトピックの全部。sourcetype は `netops:<トピック>`。index は `main`（HEC の token の既定） | `spark/snmp_sinks.py`、変数 `SPLUNK_INDEX`（既定は空） |
| ライセンス | 試用（60 日、1 日 500 MB まで）。起動時に環境変数で同意する | `splunk.tf` の `SPLUNK_START_ARGS`、`SPLUNK_GENERAL_TERMS` |
| シークレット | SSM の SecureString `/<prefix>/splunk/admin-password`、`/<prefix>/splunk/hec-token`（`ops/up.sh` が作る。値は Terraform も state も持たない） | `ops/up.sh`、`splunk.tf` の `secrets` |
| スイッチ | `STORES` の `splunk` | `deploy.env.example` |
| 費用 | 12 セント/時（`STORES` の `splunk` 全体では Spark のジョブと合わせて約 +$0.34/h） | `ops/up.sh` の先頭のコメント、`deploy.env.example` |

保存済みサーチ（app `netops_alerts`。4 本とも毎分）:

| 保存済みサーチ | 見るもの | 出すもの |
|---|---|---|
| `netops_poll` | SNMP のポーリングの `ifOperStatus` | down かどうかが変わった IF だけ。`link_down` の `firing` / `resolved` |
| `netops_gnmi` | gNMI の BGP の `session_state`、IS-IS の `oper_state` | `bgp_down` / `isis_down` の `firing` / `resolved` |
| `netops_trap` | trap | linkDown は `link_down` の `firing`、linkUp は `resolved`。ほかの trap は `trap` の `firing` |
| `netops_trap_clear` | trap（過去 70 分） | その機器から link 以外の trap が 10 分来なければ `trap` を `resolved` |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| Spark（ジョブ `sinks-splunk`） | Spark → HEC | 8088/tcp、`/services/collector/event`、HEC の token |
| 利用者の PC | PC → Web の EC2 → Splunk | 8000/tcp。SSM のポートフォワーディング（output `splunk_port_forward_command`）。ユーザー admin |
| SNS | Splunk → `<prefix>-alerts` | `sns` のエンドポイント。アラートアクション `netops_sns` がタスクロール（`sns:Publish` だけ）で publish |
| SSM | タスクの起動時に読む | 実行ロール |

## 知見

- **イメージは amd64 しか無い。**
  ほかのコンテナは arm64 にそろえてあるが、Splunk だけ x86 のタスクにしている。
  出典: `terraform/pipeline/analytics/splunk.tf` の先頭のコメント、[data-stores.md](../../data-stores.md) の「8. arm64 に揃える（Splunk だけ x86）」。
- **ライセンスと Splunk General Terms には、デプロイする人が同意したことになる。**
  イメージは `SPLUNK_START_ARGS=--accept-license` と `SPLUNK_GENERAL_TERMS` が無いと起きない。Splunk のサイトへの登録は要らない。
  出典: `splunk.tf` の先頭のコメント、FAQ「Splunk のライセンスは、Splunk のサイトでメールアドレスを登録しないと使えない？」。
- **試用の 60 日は、そのタスクが最初に起きた時から数える。**
  タスクが入れ替わると新しい試用が始まる。切れると無料のライセンスに落ち、アラートが使えなくなる。1 日 500 MB を超える日が続くと検索が止められる。
  出典: 同じ FAQ、FAQ「Splunk はデータ量で課金されると聞いた。Splunk Cloud の話？」（料金の形は記憶から書いた、と FAQ にある）。
- **起動に数分かかるので、`ops/up.sh` は HEALTHY になってから Spark のジョブを出す。**
  先に出すと、HEC への POST が再試行のあとに落ちてジョブが止まる。手順 7-4b が最長 20 分待つ（最初の起動は 5〜10 分）。
  出典: `splunk.tf` のコメント、`ops/up.sh` の手順 7-4b。
- **アラートは、Splunk の Python が持っている boto3 で SNS に publish する。**
  10.4.3 は python3.13 に boto3 1.37.14。app には同梱しない。Splunk の版を変えたら `tests/check_splunk_image.py` で確かめる。1 通に 50 件まで、失敗は 3 回まで試す。
  出典: [pipeline.md](../../pipeline.md) の「Splunk のアラート」。
- **splunkd は、コンテナの環境変数を子プロセスに引き継がない。**
  `splunk/entrypoint.sh` が、要る値（リージョン、トピックの ARN、`DEVICE_MAP`、認証情報の取り出し口の URI）を `/opt/container_artifact/nwc-alerts.env` に写す。鍵そのものは書かない。
  出典: 同上。
- **保存済みサーチは「索引に入った時刻」で直前の 1 分を読む。**
  イベントの時刻で切ると、Spark のマイクロバッチで遅れて届いた分を取りこぼす。スケジューラが遅れても飛ばさない（`realtime_schedule = 0`）。
  出典: 同上。
- **`props.conf` の `KV_MODE = json` と `spath` を重ねると、1 行も出なくなる。**
  全部の項目が同じ値 2 つの多値になる（10.4.3 で実測）。項目は `fields` で `_raw` だけにしてから `spath` で取る。
  出典: 同上。
- **gNMI と trap のイベントは、機器名でなく IP を持つ。**
  アラートアクションが環境変数 `DEVICE_MAP` で機器名に直す。直せなかった IP はそのまま `device_id` になり、Neptune では「未登録」の頂点になる。
  出典: 同上。
- **Splunkbase の Splunk Add-on for AWS は使っていない。**
  配布物を公開リポジトリに置けず、VPC から Splunkbase へも出られない。
  出典: 同上。
- **HEC は、来たものを全部入れる。重複は防げない。**
  Spark が送り直すと 2 回入る。アラートは「最後の状態」で判定するので影響しない。影響するのは件数や合計の検索と、取り込み量（ライセンス）。
  出典: FAQ「Splunk に同じデータが二重に入るのは、防げる？」。
- **無い index を指定すると、HEC は 200 を返すのにイベントは捨てられる。**
  Splunk は index を自動では作らない。既定（`SPLUNK_INDEX` が空）では `main` に入るので起きない。2026-10-04 に手元のコンテナで見つけた。
  出典: FAQ「HEC で index を指定しないと、自動で index の名前が付く？」（Splunk の文書 https://help.splunk.com/en/splunk-enterprise/get-started/get-data-in/10.0/get-data-with-http-event-collector/format-events-for-http-event-collector 、2026-10-05 に確認）。
- **確かめるときの検索。**
  サーチが動いたかは `index=_internal sourcetype=scheduler savedsearch_name=netops_*`。publish の結果は `index=_internal sourcetype=splunkd sendmodalert netops_sns`。データは `index=main`。
  出典: [pipeline.md](../../pipeline.md) の「Splunk のアラート」「Grafana と Splunk を開く」。
- **アラートが出ないときの見方。**
  [troubleshooting.md](../../troubleshooting.md) の「パイプラインと WORKFLOW」の「Splunk のアラートが出ない」「手順 7-4b で…HEALTHY にならない」の行。ログは `/ecs/<prefix>-splunk`。
  出典: 同じファイル。
- **2026-09-28 までは、AWS の外の Splunk へ NAT で出していた。**
  いまは VPC の中だけ。
  出典: `splunk.tf` の先頭のコメント。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| index の保存 | タスクのエフェメラルストレージ。タスクと一緒に消える（残すなら EFS が要る） |
| 2 タスク | 立てられない（index がタスクの中にしか無い） |
| AWS の上での通し | 未確認（`sns` のエンドポイント越しの publish、trap の送り元の IP が `DEVICE_MAP` に当たるか、SR Linux の linkDown の trap に IF 名が載るか、`netops_poll` を足したあとの形） |
| Telegraf か Splunk が 10 分を超えて止まったとき | `netops_poll` の「前の値」が無くなり、戻ったときに新しい `starts_at` で `firing` をもう 1 回出す |
| Splunk が止まっているあいだの変化 | 次に状態が変わるまで出ない |

このあと変わる予定: indexer をクラスターにして、index を残せるようにする。[Splunk をクラスターにする（004）の設計](../../cycles/004-splunk-indexer-cluster/design.md)。

## 関連

- [emr-serverless.md](emr-serverless.md)、[grafana.md](grafana.md)、[sns-sqs-lambda.md](sns-sqs-lambda.md)、[ssm-parameter-store.md](ssm-parameter-store.md)
- [pipeline.md](../../pipeline.md): 「Grafana と Splunk を開く」「アラート」「Splunk のアラート」
- [alert-comparison.md](../../alert-comparison.md): Splunk と Grafana のアラートを比べる（002）の結果、「Splunk の `netops_poll`」
- FAQ の 10 章: [faq-fukuda-nwc-poc.md](../../faq-fukuda-nwc-poc.md)
