# Splunk と Grafana のアラートを比べる（Cycle 002 alert-parity-splunk-grafana）設計: Splunk と Grafana（OpenSearch + Prometheus）で同じアラートを出し、アラートの履歴で比べる

main(fable-5.1) / effort: high

## 背景

- Splunk と、Grafana（OpenSearch + Prometheus）のアラートのコリレーションを PoC で比べたい。その前に、両方で同じアラートを出す。揃えられないものは「できない」と理由を残す。
- いまは比べられない。両方にアラートのルールがある種類が 1 つも無い:

  | 種類 | 入力 | Grafana のルール | Splunk のサーチ |
  |---|---|---|---|
  | link_down | SNMP のポーリング（ifOperStatus） | ある | 無い |
  | link_down | trap（linkDown / linkUp） | 無い | ある |
  | bgp_down | gNMI の bgp_neighbor（on_change） | 無い | ある |
  | isis_down | gNMI の isis_interface（on_change） | 無い | ある |
  | trap | link 以外の trap | 無い | ある |

- データは同じ Kafka から全部の格納先に書いているが、格納先ごとに入れられる形が違う:

  | データ | Splunk | OpenSearch | Prometheus |
  |---|---|---|---|
  | trap / syslog | 入る | 入る | 入れない（ログ） |
  | SNMP のポーリング、gNMI の数値 | 入る | 入れない（メトリクス） | 入る |
  | gNMI の文字列の状態（BGP の `session_state`、IS-IS の `oper_state`） | 入る | 入れない | **入らない** |

  最後の行は、Prometheus が数値しか持てず、Spark が文字列の field を捨てているため。bgp_down と isis_down は、Grafana の側にデータが無い。
- 既定の設定（`SINK_SPLUNK=0` / `SNMP_POLL=0`）では、どちらも発火しない。
- 比べる道具は cycle 001 のアラートの履歴（`alert_events`。`source` が grafana / splunk）と Athena。**cycle 001 と rename-raw-telemetry が main に入ってから実装を始める。**

## 設計方針

### 合意した決定（経緯は design-log.md の Round 0）

1. 先に「同じ入力から同じアラート」を両方で出して比べる（速さ、取りこぼし、書きやすさ）。コリレーション（link → IS-IS → BGP を 1 つの障害にまとめる）は次のサイクル。
2. 無理に揃えない。自然に書けるものだけ作り、書けないものは理由と一緒に記録する。
3. 両方とも今までどおり SNS に出す。アラートの履歴を Athena で読んで比べる。
4. 実装はエンジニアセッションに頼む。
5. 完了の条件に、比較の結果の文書を含める。
6. 評価の間隔は両方 1 分。
7. 既定を `SINK_SPLUNK=1` / `SNMP_POLL=1` にして、何も書かなくても両方が同じ入力を見て発火するようにする。
8. 格納先に合わせた整形は Spark でやる。Telegraf と、Splunk に届くデータは変えない。

### 実物で確認した入力（推測ではない）

- Prometheus には数値の field しか入らない。spark/snmp_sinks.py の `prometheus_series`:
  ```python
  num = _number(v)
  if num is None:
      continue
  ```
  系列名は `snmp_<measurement>_<field>`（`metric_name`）、タグはラベルになる。
- gNMI と trap のイベントは機器名を持たない。`source` タグが機器の管理 IP（telegraf/telegraf.conf.in）。Splunk はアラートアクションが `DEVICE_MAP` で名前に直す（splunk/netops_alerts/bin/netops_sns.py の `parse_device_map`）。Grafana の通知テンプレートは `$a.Labels.sysName` を使うので、いまのままでは機器名が出ない。
- bgp_neighbor はタグ `peer_address`、field `session_state`。isis_interface はタグ `interface_name`（ethernet-1/1.0 など）、field `oper_state`（up / down）。どちらも on_change で、変わったときにしか値が来ない。
- Splunk のサーチ（splunk/netops_alerts/default/savedsearches.conf）:
  - 窓は `_index_earliest=-1m@m-10s _index_latest=@m-10s`、`fields _time _indextime _raw source` のあと `spath`。
  - 機器は `coalesce('tags.sysName', 'tags.agent_host', 'tags.source')`。
  - linkDown の IF 名は `foreach "fields.*"` で varbind の名前（数値 OID + ifIndex）から探す。field の名前が IF ごとに変わる。
  - trap の OID は `iso.…` と `.1.…` の 2 通りがあり、`replace` で揃えている。
- Grafana のルール（grafana/provisioning/alerting/netops.yaml）:
  - `snmp_interface_ifOperStatus{ifName!~"(lo|mgmt).*|.*[.].*"} unless on (sysName, ifName) (snmp_interface_ifAdminStatus == 2)`、しきい値は `within_range [1.5, 2.5]`、`interval: 30s`、`for: 0s`、`noDataState: KeepLast`。
  - テンプレートは target に `$a.Labels.ifName`、detail に `"%s is down (grafana)"` を固定で使う。`group_by: ['alertname', 'sysName', 'ifName']`。
  - `grafana/start.sh` は `PROMETHEUS_URL` と `ALERTS_TOPIC_ARN` の両方があるときだけアラートの設定を並べる。
- OpenSearch の文書（`opensearch_docs`）: `{"@timestamp","topic","measurement","agent_host","host","tags":{…},"fields":{…}}`。trap の OID は `tags.oid`。
- Spark のジョブの引数は `terraform/pipeline/analytics/outputs.tf` の `entryPointArguments`。analytics の変数 `device_map` はもうある（Splunk のタスク用。`ops/up.sh` が `SINK_SPLUNK` のときだけ `lab/lab_topology.py --device-map` で作る）。
- lab の障害の入れ方（lab/lab.sh）: `fail-main` / `heal-main` / `failover`（dc1-leaf-01 ethernet-1/1）だけ。

### 1. 格納先に合わせた整形を Spark でやる

Telegraf、Kafka、S3 Tables の生データ、Splunk に届くデータ（`splunk_events`）は変えない。

- **Prometheus: 文字列の状態を 1 / 0 にする**（`prometheus_series`）。
  - 対応表を 1 つ持つ。`bgp_neighbor.session_state` は established が 1、ほかは 0 で、field 名は `session_up`。`isis_interface.oper_state` は up が 1、ほかは 0 で、field 名は `oper_up`。大文字小文字は見ない。
  - 系列は `snmp_bgp_neighbor_session_up` と `snmp_isis_interface_oper_up`。
  - 表に無い文字列の field は今までどおり捨てる。
- **Prometheus と OpenSearch: 機器名を付ける**（`prometheus_series` と `opensearch_docs`）。
  - `sysName` が無いレコードは、`source` を対応表で引いて `sysName` を足す。表に無ければ足さない。
  - 対応表はジョブの引数 `--device-map`（書式は Splunk の `DEVICE_MAP` と同じ `別名=機器名,…`）。`ops/up.sh` は `SINK_SPLUNK` に関わらずいつも作って渡す。
- この整形の量（Prometheus の側で必要だったこと）は、比較の結果の文書に書く。

### 2. Grafana のルール（評価は 1 分）

| ルール | データソース | 式（考え方） |
|---|---|---|
| link_down（今ある） | Prometheus | 変えない。グループの `interval` を 1m にする |
| bgp_down | Prometheus | `last_over_time(snmp_bgp_neighbor_session_up[24h])` が 0 |
| isis_down | Prometheus | `last_over_time(snmp_isis_interface_oper_up[24h])` が 0 |
| trap | OpenSearch | 過去 10 分の `measurement:snmp_trap`（linkDown / linkUp と起動の OID を除く）を `tags.sysName` と `tags.oid` ごとに数え、1 以上 |

- `last_over_time` を使うのは、on_change の値が Prometheus では 5 分で途切れるため。そのままだと落ちているのに解消が出る。24 時間を超えて落ちたままだと解消が出てしまう。これは結果に「制約」として書く。
- trap は 10 分来なければ数が 0 になり、Grafana が解消を送る。Splunk の `netops_trap_clear` は機器ごと、Grafana は OID ごとに閉じる。違いは結果に書く。
- 通知テンプレートを種類によらない形にする。各ルールがラベル `target`（`{{ $labels.ifName }}` / `peer_address` / `interface_name` / OID）と注釈 `detail` を持ち、テンプレートは `$a.Labels.target` と `$a.Annotations.detail` を使う。`group_by` は `alertname, sysName, target`。
- trap の linkDown / linkUp は作らない。IF 名の入った field の名前が IF ごとに変わるので、OpenSearch の集計では取り出せない。「取り込みの段で正規化すれば書ける」と結果に記録する。
- `grafana/start.sh`: trap のルールは OpenSearch のデータソースがあるときだけ並べる。

### 3. Splunk のサーチ

- `netops_poll` を足す（`telegraf:interface` の `ifOperStatus` → link_down）。
  - ポーリングは 10 秒ごとに全 IF の値が来る。状態をそのまま出すと、毎分すべての IF の resolved を SNS に出してしまう。直前の 1 分と今の 1 分の最後の値を比べ、変わった IF だけを出す。初めて見る IF は down のときだけ出す。
  - 除くものは Grafana と同じ（lo / mgmt / サブインタフェース / ifAdminStatus が 2）。
- 既存の 3 本は検知の中身を変えない。

### 4. どのルールが出したかを detail に書く

`alert_events` の列は変えない。`detail` の末尾を揃える。

| 種類 | Grafana | Splunk |
|---|---|---|
| link_down（ポーリング） | `(grafana: poll)` | `(splunk: poll)` |
| link_down（trap） | 無い | `(splunk: linkDown trap)` / `(splunk: linkUp trap)`（今のまま） |
| bgp_down / isis_down | `(grafana: gnmi)` | `(splunk: gnmi)` |
| trap | `(grafana: trap)` | `(splunk: trap)` |

### 5. 既定の設定

- `ops/up.sh` の `SINK_SPLUNK` と `SNMP_POLL` の既定を 1 にする。stream の変数 `snmp_poll` と `telegraf.sh` の既定も合わせる。
- 送り手が無いときの `die` と注意の文言、頭のコメント、`deploy.env.example`、docs の「既定ではどちらも発火しない」を直す。
- 影響: Splunk の ECS が既定で立つ（+$0.12/h）。ポーリングが既定で動く（sns のエンドポイント +$0.014/h）。0 と書けば止められる。

### 6. 障害の入れ方（lab）

- `lab fail-bgp` / `lab heal-bgp`: dc1-leaf-01 の BGP の隣接 1 本を admin-state で止める / 戻す。
- `lab trap-test`: link 以外の trap を 1 通送る。SR Linux に出させる手段が無ければ net-snmp の `snmptrap` で送る。
- link_down と isis_down は今の `fail-main` / `heal-main`。

### 7. 比べ方と結果の文書（`docs/alert-comparison.md`）

1. できること・できないことの表。種類ごとに、Grafana と Splunk それぞれで「書けた / 書けない / 条件つき」と理由。取り込みの段で必要だった整形も書く。
2. 手順。障害を入れる → 2 分待つ → 戻す → 2 分待つ。種類ごとに 3 回。入れた時刻を控える。
3. Athena のクエリ。`alert_events` を `anomaly_id` と `status` でまとめ、`source` ごとの最初の `received_at` とその差（秒）を出す。片方にしか無い行は取りこぼしとして出す。ポーリングと trap は `detail` の末尾で分ける。
4. 結果。種類ごとの遅れ、取りこぼし、気づいた違い。

4 は AWS で障害を入れてから埋める（up.sh はユーザーが打つ）。**4 が埋まるまで、このサイクルは完了にしない。**

## 変更対象ファイル

- Spark: `spark/snmp_sinks.py`、`terraform/pipeline/analytics/outputs.tf`
- Grafana: `grafana/provisioning/alerting/netops.yaml`、`grafana/start.sh`
- Splunk: `splunk/netops_alerts/default/savedsearches.conf`
- 配備: `ops/up.sh`、`deploy.env.example`、`telegraf/telegraf.sh`、`terraform/pipeline/stream/variables.tf`
- lab: `lab/lab.sh`
- テスト: `tests/test_alerts.py`、`tests/test_analytics.py`、`tests/test_stream.py`、`tests/test_lab_debug.py`
- docs: `docs/alert-comparison.md`（新規）、`docs/pipeline.md`、`docs/deploy.md`

## 再利用するもの

- `lab/lab_topology.py --device-map` と、`ops/up.sh` がそれを analytics に渡している作り。
- `splunk/netops_alerts/bin/netops_sns.py` の `parse_device_map`（同じ書式を Spark でも読む）。
- `spark/snmp_sinks.py` の `_number` と、格納先ごとに関数が分かれている作り。
- Splunk のサーチの型（索引に入った時刻の窓、`fields _raw` → `spath`、`stats latest`）。
- `workflow/rules.py` の `alerts_from_message`。本文の形は変えないので、受け手は変えない。
- cycle 001 の `alert_events` と Athena のワークグループ。

## 実装ステップ

1. Spark: 状態の 1 / 0 と機器名。単体テスト。
2. Grafana: テンプレートを `target` / `detail` に変え、bgp_down / isis_down / trap のルールを足し、`interval` を 1m にする。手元のコンテナ（13.2.2）で起動を確かめる。
3. Splunk: `netops_poll` を足し、detail の末尾を揃える。手元のコンテナ（10.4.3）で確かめる。
4. 既定の設定とテスト。
5. lab.sh の `fail-bgp` / `heal-bgp` / `trap-test`。
6. docs（`alert-comparison.md` の 1〜3、pipeline.md、deploy.md）。
7. `ops/check.sh` を流す。

## 検証方法（期待出力つき）

- `python3 tests/test_analytics.py`:
  - `prometheus_series` に `bgp_neighbor`（`source` = 管理 IP、`session_state` = `established` / `active`）を入れると、`snmp_bgp_neighbor_session_up` が 1 / 0 で出て、ラベル `sysName` が機器名になる。
  - `isis_interface` の `oper_state` = `up` / `down` で `snmp_isis_interface_oper_up` が 1 / 0。
  - 表に無い文字列の field は系列にならない。
  - `opensearch_docs` に `sysName` の無い `snmp_trap` を入れると `tags.sysName` が機器名になる。対応表に無い IP では `sysName` を足さない。
  - `splunk_events` の出力は変更の前と同じ。
  - `SINK_SPLUNK` も `SNMP_POLL` も渡さないとき、送り手の判定が `OUT: 1`（今は `OUT: 0`）で、sinks に `splunk` が入る。
- `python3 tests/test_stream.py`: `SNMP_POLL` を渡さないとき、`tg render` に `inputs.snmp` の区間が残る（今は消える）。
- `python3 tests/test_alerts.py`:
  - netops.yaml のルールの title が `link_down` / `bgp_down` / `isis_down` / `trap` の 4 つで、グループの `interval` がどれも `1m`。
  - テンプレートが `Labels.target` と `Annotations.detail` を使い、`Labels.ifName` を使わない。
  - savedsearches.conf に `netops_poll` があり、`cron_schedule = * * * * *`。
- 手元のコンテナ:
  - Grafana が起動し、`/api/v1/provisioning/alert-rules` が 4 件返す。
  - Splunk に `ifOperStatus` が 1 → 2 → 2 → 1 のイベントを 1 分ずつ入れると、`netops_poll` が firing 1 行、0 行、resolved 1 行を出す。
- AWS（ユーザーが up.sh を打つ）:
  1. `sudo lab fail-main` から 3 分以内に、Athena で `dc1-leaf-01#link_down#ethernet-1/1` の firing が `source` = grafana と splunk の両方にある。Splunk は detail が `poll` と `linkDown trap` の 2 行。
  2. 同じ障害で `isis_down` の firing が両方にある。
  3. `sudo lab fail-bgp` で `bgp_down` の firing が両方にあり、`device_id` がどちらも機器名（IP ではない）。
  4. `sudo lab heal-main` / `heal-bgp` で resolved が両方にある。
  5. `sudo lab trap-test` で `trap` の firing が両方にあり、およそ 10 分後に resolved が両方にある。
  6. `docs/alert-comparison.md` の 4 が、種類ごとの遅れと取りこぼしで埋まっている。

## 未確定事項とリスク

1. **Grafana のアラートが OpenSearch Serverless（SigV4）を読めるかは試していない。** 読めなければ trap のルールは作らず、「できない」に理由を書く。ラベル名に `.` が入る（`tags.sysName`）ので、機器名を取り出す書き方も実物で確かめる。
2. **trap の OID の書き方が 2 通りある**（`iso.…` と `.1.…`）。OpenSearch のクエリを書く前に、実物の文書を 1 件読む。
3. **機器名の対応表をジョブの引数で渡す。** 機器を足したらジョブを作り直す必要がある。本番の台数では S3 のファイルか Nautobot から引く形に変える。
4. **`last_over_time` の 24 時間。** Telegraf がつなぎ直すと今の状態を全部送り直すので、普段は切れない。24 時間を超えて落ちたままの解消の誤りは残る。
5. **既定を変える影響。** deploy.env に何も書いていない環境は、次の up.sh で Splunk の ECS が立ち、ポーリングが始まる。
6. **Splunk の `netops_poll` は、Splunk が止まっていたあいだの変化を拾えない**（直前の 1 分としか比べない）。Grafana は状態を見るので拾える。違いとして結果に書く。
7. **SR Linux に link 以外の trap を出させる方法が分からない。** `snmptrap` で作ると送り元が lab の EC2 になり、機器名が引けないことがある。
8. **cycle 001 と rename-raw-telemetry のあとに作る。** `ops/up.sh`、`tests/test_analytics.py`、`docs/pipeline.md` が重なる。

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261004-cycle-002-alert-parity-splunk-grafana-design.html -->
