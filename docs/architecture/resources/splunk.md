# Splunk（ECS）

← [リソースごとの知見](README.md)

## ひとことで

全部のトピックを入れて検索する画面で、異常を見つけて SNS に知らせる送り手の 1 つ。Grafana と比べるために置いてある。
Splunk Enterprise の公式イメージにアラートの app を足したものを、Fargate で動かしている。既定はタスク 1 つ。`SPLUNK_AZ_NUM` を 2 か 3 にすると indexer のクラスターになる（cluster manager 1、indexer は AZ ごとに 1、search head 1）。
index はタスクの中にあり、タスクと一緒に消える。クラスターは indexer どうしで複製するので、indexer が 1 台残っていれば検索できる。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| サービス | `<prefix>-splunk`（1 台のときは全部の役目、クラスターのときは search head）。ECS のクラスター `<prefix>-analytics`。1 タスク、サブネット a | `terraform/pipeline/analytics/splunk.tf` |
| クラスターのサービス | `SPLUNK_AZ_NUM` が 2 か 3 のときだけ。`<prefix>-splunk-cm`（cluster manager、1 タスク、サブネット a）と `<prefix>-splunk-idx`（indexer、`SPLUNK_AZ_NUM` タスク、サブネット a から `SPLUNK_AZ_NUM` 個） | `splunk.tf`、`deploy.env.example` の `SPLUNK_AZ_NUM`（既定 1、1〜3） |
| タスクの大きさ | Fargate x86、2 vCPU / 4 GB、エフェメラルストレージ 40 GiB | 変数 `splunk_task_cpu`、`splunk_task_memory`、`splunk_ephemeral_storage_gib` |
| イメージ | Splunk Enterprise 10.4.3 の公式イメージ + app `netops_alerts`。ECR の `<prefix>-splunk` | `splunk/Dockerfile`、変数 `splunk_image_tag` |
| 名前とポート | Cloud Map `splunk.<prefix>.internal`。Web 8000（http）、HEC 8088（https、自己署名）。クラスターのときは `splunk-cm.<prefix>.internal`（manager）と `splunk-idx.<prefix>.internal`（indexer。A レコードが indexer の数だけ）が増え、HEC の宛先は `splunk-idx` になる | `splunk.tf`、`locals.tf` の `splunk_hec_url` |
| Splunk どうしのポート | 管理と検索 8089、複製 9887、転送の受け口 9997。SG `splunk` から `splunk` へだけ開けてある（1 台のときも規則はある。相手がいないだけ）。ほかの SG からは 8089 に届かない | `terraform/base/core/security_groups.tf` |
| 入るもの | 5 つのトピックの全部。sourcetype は `netops:<トピック>`。index は `main`（HEC の token の既定） | `spark/snmp_sinks.py`、変数 `SPLUNK_INDEX`（既定は空） |
| ライセンス | 試用（60 日、1 日 500 MB まで）。起動時に環境変数で同意する | `splunk.tf` の `SPLUNK_START_ARGS`、`SPLUNK_GENERAL_TERMS` |
| シークレット | SSM の SecureString `/<prefix>/splunk/admin-password`、`/<prefix>/splunk/hec-token`。クラスターのときは `/<prefix>/splunk/idxc-secret`（manager・indexer・search head が互いを確かめる合言葉）も。どれも `ops/up.sh` が作る。値は Terraform も state も持たない | `ops/up-common.sh` の `ensure_splunk_secrets`（`ops/up.sh` の手順 7-4 が呼ぶ）、`splunk.tf` の `secrets` |
| スイッチ | `STORES` の `splunk`。クラスターにするかは `SPLUNK_AZ_NUM` | `deploy.env.example` |
| 費用 | 1 タスク 12 セント/時（`STORES` の `splunk` 全体では Spark のジョブと合わせて約 +$0.34/h）。`SPLUNK_AZ_NUM=2` は 4 タスクで 49 セント/時（+$0.37/h）、`3` は 5 タスクで 61 セント/時（+$0.49/h）。AZ をまたぐ複製の通信料は入っていない | `ops/up.sh` の費用の目安（526〜583 行）、`deploy.env.example` |

`SPLUNK_AZ_NUM` と構成:

| `SPLUNK_AZ_NUM` | タスク | 複製の数 | 落ちても検索できる範囲 |
|---|---|---|---|
| 1（既定） | 1（全部の役目） | なし | なし（タスクが落ちるとデータは消える） |
| 2 | cluster manager 1 + indexer 2 + search head 1 = 4 | 2 | indexer 1 台 |
| 3 | cluster manager 1 + indexer 3 + search head 1 = 5 | 3 | indexer 2 台 |

クラスターの役割（イメージは同じ。環境変数 `SPLUNK_ROLE` で分ける）:

| タスク | すること | 持つもの |
|---|---|---|
| cluster manager（`splunk-cm`） | indexer の登録と、複製の指図 | 複製の設定（複製の数と、検索できる複製の数。どちらも indexer の数） |
| indexer（`splunk-idx`） | HEC で受けて index `main` に入れる。相手に複製を送る | index `main`（自分の分と、相手の分の複製） |
| search head（`splunk`） | 検索、UI、保存済みサーチ、アラートアクション | app `netops_alerts`、`DEVICE_MAP`、SNS へ publish するタスクロール |

保存済みサーチ（app `netops_alerts`。4 本とも毎分）:

| 保存済みサーチ | 見るもの | 出すもの |
|---|---|---|
| `netops_poll` | SNMP のポーリングの `ifOperStatus` | down かどうかが変わった IF だけ。`link_down` の `firing` / `resolved` |
| `netops_gnmi` | gNMI の BGP の `session_state`、IS-IS の `oper_state` | 前の値と比べて変わったときだけ。`bgp_down` / `isis_down` の `firing` / `resolved` |
| `netops_trap` | trap | linkDown は `link_down` の `firing`、linkUp は `resolved`。ほかの trap は `trap` の `firing` |
| `netops_trap_clear` | trap（過去 70 分） | その機器から link 以外の trap が 10 分来なければ `trap` を `resolved` |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| Spark（ジョブ `sinks-splunk`） | Spark → HEC | 8088/tcp、`/services/collector/event`、HEC の token。宛先は 1 台のとき `splunk`、クラスターのとき `splunk-idx`（どの indexer も同じ token で受ける） |
| 利用者の PC | PC → Web の EC2 → Splunk | 8000/tcp。SSM のポートフォワーディング（output `splunk_port_forward_command`）。ユーザー admin。クラスターのときの入口は search head。manager の画面（クラスターの状態）は output `splunk_cm_port_forward_command`（`http://localhost:8001`） |
| Splunk どうし（クラスターのときだけ） | search head → indexer（検索）、manager ↔ indexer・search head（管理）、indexer ↔ indexer（複製）、search head・manager → indexer（自分の `_internal` と `_audit` の転送） | 8089 / 9887 / 9997。合言葉は `/<prefix>/splunk/idxc-secret` |
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
  先に出すと、HEC への POST が再試行のあとに落ちてジョブが止まる。手順 7-4b が最長 20 分待つ（最初の起動は 5〜10 分）。クラスターのときは 3 つのサービスの全部のタスク（`SPLUNK_AZ_NUM` + 2 個）を待つ。
  出典: `splunk.tf` のコメント、`ops/up.sh` の手順 7-4b。
- **クラスターの保存済みサーチは search head だけで動く。**
  manager と indexer でも動くと、同じアラートが台の数だけ SNS に出る。`splunk/entrypoint.sh` が、`SPLUNK_ROLE` が search head と 1 台用（`splunk_standalone`）のとき以外は、起動の前に app `netops_alerts` を消す。SNS へ publish するタスクロールと `DEVICE_MAP` も search head のタスク定義にだけ付く。
  出典: `splunk/entrypoint.sh`、`splunk.tf`。
- **indexer は、止められる（SIGTERM）と先に `splunk offline` を打つ。**
  いきなり止まると、manager が次の世代を約 5 分確定できず、search head の検索が黙って 0 件になる（データは消えない）。`splunk offline` で manager が primary を残りの indexer へ付け替えてから止まる。60 秒で終わらなければ打ち切って、いつもどおり止める。タスク定義の `stopTimeout` は 120 秒。ログには `nwc-offline: start` と `nwc-offline: rc=<終了コード> <秒>s` が出る。
  2026-10-05 に AWS で確かめた（`SPLUNK_AZ_NUM=2`。indexer のタスクを 1 つ止めると `nwc-offline: start` → `nwc-offline: rc=0 43s` が出て、ECS が代わりを起動した）。
  効くのは SIGTERM で止まるときだけ。落ちたときや SIGKILL のときは走らない。
  出典: `splunk/entrypoint.sh`、004 の設計の「未確定事項とリスク」の 11。
- **search head のヘルスチェックは、indexer の GUID の突き合わせも見る（`nwc-peer-check`）。**
  indexer が入れ替わって前と同じ IP をもらうと、search head は古い GUID のままその indexer を持ち続け、検索が黙って欠ける（手元の Docker で再現。Fargate で同じ IP がまた割り当てられるかは未確認）。`splunk/peers_check.py`（イメージの `/sbin/nwc-peers-check.py`）が、manager が Up と言う indexer を search head が同じ GUID の Up で持っているかを見る。食い違いが 10 回（約 5 分）続くと、ECS が search head を入れ替える。
  判定が変わったときだけ、ログに `nwc-peer-check state=<ok / degraded / mismatch / skip / error> reason=<理由>` を 1 行書く。`degraded` は manager が Up と言う indexer がタスク定義の数（`NWC_PEERS_EXPECTED`）より少ない（`reason=peers_up:<Up の数>/<あるはずの数>`。終了コードは 0 で、search head は入れ替えない）、`skip` は manager に聞けない、`error` は search head の peers を読めない。
  `ops/up.sh` の手順 7-4b は、全タスクが HEALTHY になったあとにこの行を読み、`state=ok reason=peers_up:<indexer の数>` になるまで最大 6 分待つ。ならなければ止まる。
  2026-10-05 に AWS で確かめた（`nwc-peer-check state=ok reason=peers_up:2`）。`mismatch` で search head が入れ替わるところは AWS では未確認。
  出典: `splunk/peers_check.py`、`ops/up-common.sh` の `splunk_cluster_check`。
- **indexer が AZ に 1 台ずつになるかは、Fargate の振り分けに任せている（保証ではない）。**
  同じ AZ に 2 台いたら、`ops/up.sh` が注意を出して進む。2026-10-05 の AWS（`SPLUNK_AZ_NUM=2`）では ap-northeast-1a と ap-northeast-1c に分かれた。
  出典: `splunk.tf` の `aws_ecs_service.splunk_idx`、`ops/up-common.sh` の `splunk_cluster_check`。
- **新しい indexer は、ECS のヘルスチェックが通るまで HEC の宛先に載らない。**
  `splunk-idx` の Cloud Map にだけ `health_check_custom_config` を付けてある。クラスターに入る前の indexer に Spark が送らないようにするため。
  出典: `splunk.tf` の `aws_service_discovery_service.splunk_idx`。
- **indexer の入れ替えは 1 台ずつ。ただし ECS は複製が終わったかを見ない。**
  `deployment_minimum_healthy_percent = 50`、`deployment_maximum_percent = 100`。ECS が見るのはコンテナの HEALTHY だけなので、1 台目の複製が終わる前に 2 台目が入れ替わると、その分は消える（PoC では受け入れている）。
  出典: `splunk.tf`、004 の設計の「未確定事項とリスク」の 4。
- **クラスターの index は `main` だけ。**
  `SPLUNK_AZ_NUM` が 2 か 3 のとき `SPLUNK_INDEX` を書くと、`ops/up.sh` が何も作る前に止まる。`STORES` に `splunk` が無いときも止まる。
  出典: `ops/up.sh`、`deploy.env.example`。
- **1 台とクラスターを切り替えると、空から始まる。**
  index はタスクの中にあり、引き継がない。
  出典: `deploy.env.example`、004 の設計。
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
  [troubleshooting.md](../../troubleshooting.md) の「パイプラインと WORKFLOW」の「Splunk のアラートが出ない」「手順 7-4b で…HEALTHY にならない」「手順 7-4b で…突き合わせ…」の行。ログは `/ecs/<prefix>-splunk`（ストリームの接頭辞は search head と 1 台用が `splunk`、manager が `splunk-cm`、indexer が `splunk-idx`）。
  出典: 同じファイル。
- **2026-09-28 までは、AWS の外の Splunk へ NAT で出していた。**
  いまは VPC の中だけ。
  出典: `splunk.tf` の先頭のコメント。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| index の保存 | タスクのエフェメラルストレージ。タスクと一緒に消える（残すなら EFS が要る）。クラスターでも、indexer を全部同時に落とす（`ops/down.sh`、analytics の作り直し）と全部消える |
| cluster manager と search head | 1 つずつで、サブネット a にしかいない。この AZ が落ちている間は検索もアラートも止まる（データは残りの indexer にある） |
| search head が入れ替わっている間 | Splunk のアラートは出ない。`nwc-peer-check` が効くまでの約 5 分も、古い GUID の indexer に入ったイベントのアラートは落ちる。あとから出し直す仕組みは無い |
| AWS の上での通し | 2026-10-05 に AWS で確かめた: `sudo lab fail-main` で Splunk が `link_down` と `isis_down` を出し、`sns` のエンドポイント越しに SNS へ届いた（`SPLUNK_AZ_NUM=2` の構成）。未確認: `bgp_down` と `trap` の firing、trap の送り元の IP が `DEVICE_MAP` に当たるか、SR Linux の linkDown の trap に IF 名が載るか、Splunk の画面 |
| `SPLUNK_AZ_NUM` | 2026-10-05 に AWS で確かめたのは `2`（4 タスク、indexer は 2 つの AZ）。`3` は未確認。`1` のままの 1 台の構成も、004 を入れたあとの AWS では未確認 |
| 試用ライセンス | タスクごとに別々の試用ライセンスを持つ。2026-10-05 の `SPLUNK_AZ_NUM=2` ではクラスターとアラートが動いた。日数がたったあとの挙動は未確認 |
| 既知の不具合（2026-10-05） | trap のサブインターフェース（`ethernet-1/1.0`）の `link_down` と、起動の直後の `resolved` のまとめ送りは直した（手元で確認、AWS では未確認）。回線の両端が別の異常になる件は [troubleshooting.md](../../troubleshooting.md) の「既知の不具合」 |
| Telegraf か Splunk が 10 分を超えて止まったとき | `netops_poll` の「前の値」が無くなり、戻ったときに新しい `starts_at` で `firing` をもう 1 回出す |
| Splunk が止まっているあいだの変化 | 次に状態が変わるまで出ない |

クラスターの設計と、手元の Docker で確かめたことは [Splunk をクラスターにする（004）の設計](../../cycles/004-splunk-indexer-cluster/design.md)。

## 関連

- [emr-serverless.md](emr-serverless.md)、[grafana.md](grafana.md)、[sns-sqs-lambda.md](sns-sqs-lambda.md)、[ssm-parameter-store.md](ssm-parameter-store.md)
- [pipeline.md](../../pipeline.md): 「Grafana と Splunk を開く」「アラート」「Splunk のアラート」
- [alert-comparison.md](../../alert-comparison.md): Splunk と Grafana のアラートを比べる（002）の結果、「Splunk の `netops_poll`」
- FAQ の 10 章「Splunk」と 9 章「格納先とテーブル、重複」: [faq-fukuda-nwc-poc.md](../../faq-fukuda-nwc-poc.md)
