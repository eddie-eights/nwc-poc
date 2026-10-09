# マネージド版の AWS 検証、analytics まで通して立てた回（2026-10-09）

AWS アカウント 493116771193、ap-northeast-1、`OWNER=efukuda`（接頭辞 `efukuda-nwc-poc`）。時刻は UTC（日本時間は 2026-10-09 の夕方）。打ったのは PM。

ここに書いた「通った」は、出力を見て確かめたものだけ。見ていないものは「未確認」、動かなかったものは「失敗」と書く。前の回（`20261009-aws-managed.md`）は stream までで、analytics を起こしていなかった。今回は `PIPELINE=1` で analytics と graph まで立て、障害注入から復旧まで通した。

## まとめ

- `ops/up.sh` は 3 本目で最後まで通った（rc=0）。1 本目は bash 3.2 の `unbound variable`（PR #25 で直した）、2 本目は Firehose の IAM の反映遅れ（BACKLOG 129）で止まった。
- 収集は全部通った。MSK に収集器 4 つ（gnmic / syslog-ng / GoFlow2 / Telegraf trap）が書き、`metrics` / `gnmi` / `traps` / `logs` にメッセージが入っている。lab の `fail-main` と `heal-main` も効いた（IS-IS の隣接 2 → 1 → 2）。
- **格納と可視化は失敗した。** EMR Serverless の Spark のジョブ 3 本のうち `sinks-splunk` と `sinks-grafana` が 5 回とも落ち、1 時間の再試行の上限で止まっていた。原因は突き止めた（下の「不具合」の 1）。`metrics` トピックに **values の無い gnmic の event**（`name` と ns の `timestamp` と `tags` だけ）が 400 件中 359 件入っていて、`app/spark/snmp_sinks.py` がそれを Telegraf の行（秒の `timestamp`）として読み、`from_unixtime` で年 173875 になって `collect()` の `ValueError` で死ぬ。
  - そのため AMP に `snmp_*` のメトリクスは無く、Grafana のルールは全部 `Normal (NoData)`、OpenSearch の `logs*` は 0 件、SNS の発行 0、graph-status の Lambda の起動 0、Neptune の link は全部 UP のままだった。**アラート → SNS → Lambda → トポロジの DOWN の経路は今回も未確認**（この不具合に塞がれた）。
- `flows` トピックは 0 件だった（GoFlow2 が 1 件も書いていない。lab から NetFlow を送っていないので、不具合かどうかは未確認）。
- 片付けは `ops/down.sh`（rc=0）。消えたかはサービスごとの API で確かめた（下の「片付け」）。

## 立てたもの（ops/up.sh）

環境変数は `OWNER=efukuda PIPELINE=1 AGENT=0 WORKFLOW=0 NO_DASHBOARD_PORTFORWARD=1`。ほかは deploy.env のまま（`KEEP_ECR=1`、`IMAGE_TAG=v1_manual`）。

| 本 | 結果 |
|---|---|
| 1 本目 | 7-4 で `SPLUNK_VERSION�: unbound variable` で止まった。Mac の bash 3.2 が `$VAR` の直後の全角文字を 1 バイト食う。`fix/up-sh-unbound-fullwidth`（PR #25）で 9 本 88 か所を `${VAR}` にした |
| 2 本目 | analytics の apply で `aws_kinesis_firehose_delivery_stream.alert_events` が `InvalidArgumentException: The security token included in the request is invalid. Ensure that the provided IAM role associated with firehose is not deleted.` で止まった。ロールを作った直後の IAM の反映遅れ。BACKLOG 129 に足した |
| 3 本目 | 同じコマンドの打ち直しで最後まで通った（rc=0）。stream / nautobot / graph は `0 added`、analytics だけ作り直し |

立ったもの: Web の EC2 `i-0606b17ccc569eccf`（t4g.medium）、lab の EC2 `i-079f16e1cfe989ec4`（m6i.xlarge）、MSK `efukuda-nwc-poc-stream`（ブローカー 2、SCRAM 9096）、ECS のクラスター 3 つ（telegraf: goflow2 / telegraf-dialout / syslog-ng / gnmic、analytics: splunk / grafana、nautobot）、NLB、Neptune Analytics、OpenSearch Serverless `logs`、EMR Serverless `00g9cvdetc4qe52l`、AMP、S3 Tables、Athena、Firehose、SNS、Lambda `graph-status`。待機だけで約 $2.92/h。

## 項目と結果

検査は SSM Run Command で Web の EC2 と lab の EC2 の中から打った。Grafana / Splunk / Kafbat UI のパスワードは SSM からシェルの変数に読み、出力に出していない。

### 収集（MSK まで）

| 見たもの | 結果 |
|---|---|
| ECS のサービス 7 つ | 全部 ACTIVE、desired 1 / running 1 |
| MSK の `MessagesInPerSec` | ブローカー 1 が約 4.0/s、ブローカー 2 が約 3.7/s で 08:31Z〜09:46Z ずっと一定。収集器が書き続けている |
| Kafbat UI | クラスター ONLINE、ブローカー 2、オンラインのパーティション 62、under-replicated 0 |
| `metrics`（EARLIEST から 400 件） | `interface_stats` の values あり 29、**values 無し 359**、`system` の values あり 12（下の不具合 1） |
| `gnmi`（EARLIEST から） | 8 件。`interface_state` と `isis_interface`、全部 values あり。**前の回は 0 件でトピック自体が無かった**（BACKLOG 118）。今回はできている。前の回との違いは `fail-main` を打ったことで、on-change の購読は値が変わったときだけ書いていると見える。購読の直後の初回値は今回も無いと見えるが、`fail-main` の前のメッセージ数を見ていないので未確認 |
| `traps`（EARLIEST から） | 8 件。`snmp_trap`、`timestamp` は秒 |
| `logs`（EARLIEST から） | 34 件。`device_log`、`timestamp` は秒 |
| `flows` | **0 件**。GoFlow2 が 1 件も書いていない。lab から NetFlow を送っていない（TRex も起こしていない）ので、不具合かは未確認 |
| lab の `status` / `forward-status` / `check` | 全部 OK（`fail-main` の前） |
| lab → NLB の syslog（UDP 5140） | `logger -d` の rc=0。**TCP 5140 は届かない**が、syslog-ng の受け口は UDP だけで NLB も UDP なので不具合ではない |

### 障害注入と復旧（lab）

| 時刻 | 操作 | 結果 |
|---|---|---|
| 09:27:00Z | `sudo lab fail-main` | `dc1-a-leaf-01` の `ethernet-1/1` を落とした。20 秒後の `show network-instance default protocols isis adjacency` で隣接が 2 → 1 |
| 09:50:32Z | `sudo lab heal-main` | 隣接が 1 → 2 に戻った |

`gnmi` の 8 件はこの間に入った（`interface_state` の oper-state と `isis_interface`）。

### 格納と可視化（Spark 以降）

| 見たもの | 結果 |
|---|---|
| EMR Serverless のジョブ | `sinks-s3iceberg` だけ RUNNING（attempt 1）。`sinks-splunk` は 09:26:04Z に attempt 5 で FAILED、`sinks-grafana` は 09:27:11Z に attempt 5 で FAILED。`retryPolicy.maxFailedAttemptsPerHour` が 5 なので、以後 1 時間は起きない |
| ジョブのログ（`/aws/emr-serverless/efukuda-nwc-poc` の driver の stderr） | `ValueError: year 173875 is out of range`。`snmp_sinks.py:833` の `batch_df.collect()` で Python の datetime に直すところ |
| Grafana | `/api/health` ok、13.2.3。データソース `aoss-logs`（grafana-opensearch-datasource）と `amp`（grafana-amazonprometheus-datasource）は provisioning 済み |
| AMP（Grafana のプロキシ経由で `count by (__name__) ({__name__=~"snmp_.+"})`） | 0 系列。メトリクスが 1 つも無い |
| Grafana のアラートルール | `link_down` / `bgp_down` / `isis_down` / trap の全部が `Normal (NoData)`。health は ok（ルールの式は壊れていない。データが無いだけ） |
| OpenSearch（`logs*` の直近 30 分） | 0 件 |
| Splunk | 8000 は 303 で届く。**8089（管理ポート）は Web の EC2 から接続がタイムアウト**（SG が開けていない。設計どおりで不具合ではない）。検索は 8000 の UI 経由になるので今回は見ていない。`/ecs/efukuda-nwc-poc-splunk` の直近 40 分のログは 0 行 |
| SNS `efukuda-nwc-poc-alerts` | `NumberOfMessagesPublished` 0 |
| Lambda `graph-status` | Invocations 0 |
| Neptune（Web の `graph.query`） | 機器 7 台、link は全部 UP。`n.id` が None で返ったのは検査の Cypher の属性名の違い（検査の側のミス） |
| Nautobot | `nautobot.efukuda-nwc-poc-nautobot.internal` が 10.0.0.203 に解ける。画面は見ていない |
| Web のユニット | active、`http://127.0.0.1:8080/` が 200 |

### 不具合ではないもの（記録）

- 収集器のログに SASL のエラーが残っているのは、up の 1〜2 本目のときのもの（stream ができる前）。3 本目のあとは出ていない。
- Grafana のログの `path=/api/datasources/uid//health` と `uid is empty` は、検査スクリプトがデータソースの `type` を `prometheus` で引いて空の uid を渡したため（実際の type は `grafana-amazonprometheus-datasource`）。Grafana の不具合ではない。

## 不具合

### 1. `sinks-splunk` と `sinks-grafana` が values の無い gnmic の event で落ちる（Must fix 相当）

`metrics` トピックに入っている gnmic の event の実物（Kafbat UI の `/api/clusters/<cl>/topics/metrics/messages/v2?mode=EARLIEST&limit=400`、09:53Z）:

```json
{"name":"interface_stats","timestamp":1791534762523125553,"tags":{"interface_name":"ethernet-1/7","source":"203.0.113.11","subscription-name":"interface_stats"}}
```

- `values` も `deletes` も無い。統計の無いインターフェース（ethernet-1/7、1/11、1/17 …）について gnmic がこの形で書く。400 件中 359 件がこれだった。
- `app/spark/snmp_sinks.py:282` の gnmic の判定は `fields.isNull() & (values.isNotNull() | deletes.isNotNull())` なので、この行は gnmic にならず `:300` の `otherwise(telegraf)` に落ちる。Telegraf の行は `timestamp` が秒なので、`:309` の `F.to_timestamp(F.from_unixtime(ts))` が ns の値を秒として読み、年 173875 になる。Spark の中ではそのまま通り、`:833` の `batch_df.collect()` で Python の `datetime` に直すときに `ValueError` で死ぬ。
- `sinks-s3iceberg` が RUNNING のままなのは、Python に `collect()` せず Iceberg に書くだけだから。この行の Iceberg のデータも壊れている可能性が高い（未確認）。
- 前の回の `20261009-aws-managed.md` の B は「`gnmic_message` が values の無い event を捨てる」と書いているが、捨てるのは `gnmic_struct` の中の話で、そこに来る前の `:282` の判定で Telegraf の側に落ちている。
- 直し方の案（新しいサイクルで）: 分岐を `fields.isNull()` だけで決め、values も deletes も無い行は捨てる。`ts` に上限（たとえば 1e11 秒）を付けて外れた行を捨てるか ns と見て直す。上の実物をそのままテストの入力にする。gnmic の側で空の event を出さない設定（`event-drop` などのプロセッサ）も検討する。

### 2. `flows` が 0 件（未確認）

GoFlow2 のサービスは running で、ログにエラーは無い。lab から NetFlow を送っていないので、送れば入るかは未確認。前の回は `ops/netflow_send.py` で送って入っていた。

## 片付け（ops/down.sh）

- 打ったのは `OWNER=efukuda ops/down.sh`（deploy.env の `KEEP_ECR=1`）。開始 09:57Z ごろ、終了 10:24Z ごろ（約 27 分）。rc=0。
- Terraform の destroy は 6 ルートとも `Destroy complete!`。analytics 47 / nautobot 16 / graph 11 / stream 63 / lab 7 / base/core 154 件（agent は state が無いので飛ばした）。いちばん長いのは MSK のクラスター（3m19s）と Lambda の SG（6m50s。Lambda の ENI が外れるのを待つ）。
- `ops/up.sh` が作った SSM のパラメータ 10 本、Secrets Manager の `AmazonMSK_efukuda-nwc-poc-collectors`、KMS の鍵 `alias/efukuda-nwc-poc-msk-scram` も down.sh が消した（鍵は 7 日後の削除を予約し alias を外した）。
- 消えたかはサービスごとの API で見た（下の表。10:27Z に `boto3` で一覧して prefix `efukuda-nwc-poc` を含むものを数えた）。down.sh の最後のタグ API の一覧は消えたものも返すので見ていない。

### 消えたことの確認

| サービス | 結果 |
|---|---|
| EC2（インスタンス / EIP / NAT / VPC エンドポイント） | 0 件 |
| NLB / Cloud Map の名前空間 / ECS のクラスター | 0 件 |
| MSK のクラスター | 0 件 |
| EMR Serverless のアプリ（TERMINATED 以外） | 0 件 |
| AMP のワークスペース（DELETED 以外） / OpenSearch Serverless のコレクション | 0 件 |
| Neptune Analytics のグラフ / Lambda / Firehose / SNS | 0 件 |
| S3 Tables のテーブルバケット / S3 のバケット / Athena のワークグループ | 0 件 |
| CloudWatch Logs のロググループ | 0 件 |
| Secrets Manager（削除予約を含む） / KMS の alias | 0 件 |
| KMS の鍵 | `PendingDeletion` が 2 本（この回と同日 1 回目の `msk-scram`。2026-10-16 に消える。待つあいだは課金なし） |
| IAM のロール | 0 件 |
| ECR のリポジトリ | 15 本残っている（`KEEP_ECR=1` のとおり。agent / gnmic / goflow2 / grafana / kafka-ui / lab-multitool / lab-srlinux / lab-trex / nautobot / redis / splunk / syslog-ng / telegraf / temporal / worker） |

## docs のずれ

- `20261009-aws-managed.md` の B の「`gnmic_message` は values の無い event を捨てる」は、上の不具合 1 のとおり実際には捨てられていない（分岐の前で Telegraf の側に落ちる）。記録なので書き換えず、ここで訂正する。
