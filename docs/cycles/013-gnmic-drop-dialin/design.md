# gNMI を gnmic に移し、SNMP のポーリングと telegraf-dialin を外す（013）

設計: エンジニア4(opus-5.5) / effort: xhigh（PM(fable-5.1) の下書きを、PM の 6 つの決定と手元の調べで書き直した。経緯は design-log.md の Round 0）

BACKLOG 28「コレクターを gNMI / SNMP trap / syslog-ng / GoFlow2 の 4 種にする」の後半。011（lab の機器名 `dc1-a-leaf-01` 等）のあと。**012（MSK の SCRAM）に依るのは Terraform と ops だけ**なので、実装を 2 つに分ける（第 1 段は 012 を待たない、第 2 段は 012 をマージしてから）。

## 背景

- ユーザーの決定（2026-10-08）: gNMI は Telemetry 専用のコレクター（gnmic）で受け、telegraf-dialin（gNMI の subscribe + SNMP のポーリング）を外す。SNMP のポーリング（`ifTable` / `sysName`）が消えるので、Grafana / Splunk の `link_down` を gNMI の `/interface/oper-state` に乗せ替える
- PM の決定（2026-10-09。design-log.md の Round 0）
  1. 機器名: gnmic の target の名前は IP。機器名（ラベル / タグ `sysName`）は Spark が device map で引く（いまの gNMI の行と同じ。機器の `host-name` は subscribe しない）
  2. 系列名の接頭辞は `snmp_` のまま（`snmp_interface_oper_up` など）
  3. 共通の形（`lab_gnmi.star` / `lab_circuits.star` の `device_cpu` / `if_stats` / `sessions` / `circuits` …）はやめる。star・docs の節・試験を消す
  4. トピック: 状態（on-change）は `gnmi`、カウンター（sample）は `metrics`
  5. subscribe は 5 つだけ（`evpn_es` / `mac_table` / `lab_*` は外す）
  6. event の形を整えるのは Spark（gnmic の processor は使わない）。同じ読み替えの Python の双子を置き、`tests/test_stream.py` で縛る
- gnmic v0.49.0 の事実（ソースとイメージで確かめた。design-log.md）
  - `format: event` の 1 件は `{"name":<subscription>,"timestamp":<ns>,"tags":{...},"values":{...}}`（消えたときは `deletes` だけで `values` が無い）。Kafka の出力の `split-events: true` で 1 メッセージ 1 件のオブジェクトになる（配列にならない）
  - tags: パスのキーは `<要素名>_<キー名>`（`interface_name`、`neighbor_peer-address`、`interface_interface-name`、`control_slot`、`cpu_index`、`network-instance_name`、`instance_name`）と、`source`（= target の名前）、`subscription-name`
  - values: キーはキーを除いたパス（`/interface/oper-state` など。モジュールの接頭辞 `srl_nokia-…:` が付くかは SR Linux の返し方による）。json_ietf の 64 bit の整数は文字列で来る（RFC 7951）
  - target の名前に port が無ければ、名前はそのまま・アドレスは `<名前>:<port>`（全体の `port`）。**`tags.source` は IP だけになる**
  - subscription ごとに別の SubscribeRequest（1 つが SR Linux に断られても、ほかは止まらない）。subscription ごとに `outputs` を選べる
  - 環境変数: `outputs` の値は全部展開する。target の `username` は常に、`password` は `$` で始まるときだけ展開する（設定ファイルに資格情報を書かずに済む）
  - イメージ `ghcr.io/openconfig/gnmic:0.49.0` は amd64 / arm64、alpine（`/bin/sh` と CA の束あり）、入口は `/app/gnmic`
- いまの Splunk の gNMI の検索（`netops_gnmi`）は機器を `coalesce('tags.sysName', 'tags.agent_host', 'tags.source')` で決めるが、Spark は Splunk へ送るとき `sysName` を足していない（gNMI の行は IP になる）。`netops_poll` が `link_down` で機器名を出せていたのは SNMP の Telegraf が `sysName` を付けていたから

## 設計方針

### 1. gnmic（`app/gnmic/`、`docker/images/gnmic/Dockerfile`）

- `gnmic.yaml.in`（設定のひな形）と `gnmic.sh`（入口。alpine なので POSIX sh）。`gnmic.sh` は環境変数からひな形を埋めて `/tmp/gnmic.yaml` に書き、`exec /app/gnmic --config /tmp/gnmic.yaml subscribe`
  - 見るだけの手当ては `gn get [パス ...]`（`telegraf-dialin` の `tg gnmi` の代わり）: 同じ設定で `gnmic get --type STATE --format event` を 1 回打ち、標準出力に出す。パスを渡さなければ状態の 4 つ（`interface_state` / `bgp_neighbor` / `isis_interface` のパス）。subscribe は設定の `outputs`（Kafka）に書くので使わない（`get` は `outputs` を使わない。v0.49.0 のイメージで確かめた）
  - `GNMI_TARGETS`（いまと同じ形 `"<IP>:57400", ...`。SSM の値も `lab_topology.py --gnmi-targets` もそのまま）→ `targets:` に `<IP>: {address: <IP>:57400}`（名前は IP、PM の決定 1）。形が違えば止まる
  - 資格情報は値を書かない: target は `username: ${GNMI_USERNAME}` / `password: ${GNMI_PASSWORD}`、Kafka は `sasl: {user: ${KAFKA_SASL_USER}, password: ${KAFKA_SASL_PASS}, mechanism: SCRAM-SHA-512}`（gnmic が読むときに展開する）
  - `KAFKA_AUTH=scram` なら `sasl:` と `tls: {}`（CA はイメージの束）、`none`（OSS・手元）なら書かない。`KAFKA_BROKERS` はカンマ区切りのまま
  - 全体: `encoding: json_ietf`、`skip-verify: true`（lab の自己署名）、`port: 57400`
- subscribe（5 つ。パスは SR Linux 26.7 の YANG）

  | 名前 | パス | モード | 出力（トピック） |
  |---|---|---|---|
  | `interface_state` | `/interface[name=*]/oper-state`、`/interface[name=*]/admin-state` | on-change | `gnmi` |
  | `interface_stats` | `/interface[name=*]/statistics` | sample 60s | `metrics` |
  | `bgp_neighbor` | `/network-instance[name=default]/protocols/bgp/neighbor[peer-address=*]/session-state` | on-change | `gnmi` |
  | `isis_interface` | `/network-instance[name=default]/protocols/isis/instance[name=main]/interface[interface-name=*]/oper-state` | on-change | `gnmi` |
  | `system` | `/platform/control[slot=*]/cpu[index=all]/total`、`/platform/control[slot=*]/memory` | sample 60s | `metrics` |

  - on-change に `heartbeat-interval` は付けない（いまの bgp / isis と同じ。SR Linux が受けるか確かめていない）。値はまばらなので、Grafana は `last_over_time(…[24h])`、Splunk は 24 時間を読む（いまの bgp_down / isis_down と同じ）
  - 出力は 2 つ（`gnmi` / `metrics`。同じブローカー、`format: event`、`split-events: true`）。subscription の `outputs:` で振り分ける
- Dockerfile: `FROM ghcr.io/openconfig/gnmic:<版>` に `gnmic.yaml.in` と `gnmic.sh` を COPY、`USER 65534:65534`（書くのは `/tmp` だけ）、`ENTRYPOINT`。版の正は第 2 段で `ops/up-common.sh`（012 の `SYSLOG_NG_VERSION` と同じ形。ARG の既定値も同値）。context は `app/gnmic/`

### 2. Spark（`app/spark/snmp_sinks.py`）

- `read_rows` の schema に `values`（Map<String,String>）を足し、**`values` があってもとの `fields` が無い行を gnmic の event として読み替える**（形で分ける。トピックでは分けない = `gnmi` / `metrics` のどちらに来ても同じ）。012 の `flows` の分岐には触らない
  - `timestamp`: ns → 秒（`(ns / 1e9).cast(long)`。012 の flows と同じ）
  - `name` → measurement: `GNMI_MEASUREMENTS = {"interface_state": "interface", "interface_stats": "interface"}`、ほかはそのまま（`bgp_neighbor` / `isis_interface` / `system`）
  - `values` → `fields`: キーは最後の要素、`接頭辞:` を落とし `-` を `_` に（`/interface/oper-state` → `oper_state`、`.../statistics/in-octets` → `in_octets`、`.../memory/utilization` → `utilization`）。値は文字列のまま（数の文字列は `prometheus_series` の `_number` が数にする）
  - `tags`: キーの `接頭辞:` を落とし、`GNMI_TAGS` で名前を替える（`interface_name`→`ifName`、`neighbor_peer-address`→`peer_address`、`interface_interface-name`→`interface_name`、`control_slot`→`slot`）。表に無いタグ（`source`、`subscription-name`、`network-instance_name` …）は残す
  - キーが重なったら後勝ち（`build` で `spark.sql.mapKeyDedupPolicy=LAST_WIN`。既定の EXCEPTION だとジョブが落ちる）
  - `agent_host` の列は `tags.source`（IP）。`host` は無い
  - `values` の無い event（`deletes` だけ）は捨てる（いまの ts が null の行と同じ扱い）
- Python の双子 `gnmic_message(m)`: 1 件の dict を Telegraf の形（`timestamp` 秒 / `name` / `tags` / `fields`）にする。同じ表（`GNMI_MEASUREMENTS` / `GNMI_TAGS`）を使う。消えた event と timestamp が整数でないものは None
- `STATE_FIELDS` に `("interface","oper_state"): ("oper_up","up")` と `("interface","admin_state"): ("admin_up","enable")` を足す（`snmp_interface_oper_up` / `snmp_interface_admin_up` が 1 / 0）
- **Splunk にも `sysName` を足す**: `splunk_events(records, index, devmap)` が `with_sysname(tags, devmap)` を通す（`make_splunk_sender` / `make_splunk_sender_on_executor` に devmap を渡す）。Grafana と同じ機器名になり、temporal の `anomaly_id`（`device#kind#target`）が Grafana と Splunk で揃う（bgp_down / isis_down も IP から機器名に変わる）
  - Spark のジョブへ `--device-map` を渡す Terraform の条件（`job_driver`。マネージドは `pipeline/analytics/outputs.tf`、OSS は `oss/pipeline/analytics/spark.tf`）に `splunk` を足す。いまは prometheus / opensearch のジョブにしか渡さないので、splunk のジョブ（sinks を分けたとき）は devmap が空のまま IP になる
- docstring の SNMP のポーリングの説明を gnmic に替える。`METRIC_TOPICS` は 012 の `"metrics,gnmi"` のまま

### 3. Grafana / Splunk

- Grafana `link_down`（uid `nwc-link-down`）: `last_over_time(snmp_interface_oper_up{ifName!~"(lo|mgmt).*|.*[.].*"}[24h]) unless on (sysName, ifName) (last_over_time(snmp_interface_admin_up[24h]) == 0)`、しきい値は `lt 0.5`（bgp_down / isis_down と同じ）。`target` は `{{ .Labels.ifName }}` のまま、detail は `… is down (grafana: gnmi)`。冒頭のコメントを直す
- ダッシュボード `metrics.json`: 変数を `label_values(snmp_interface_oper_up, sysName)`、IF の状態のパネルを `snmp_interface_oper_up`（値の対応 1 = up / 0 = down。on-change で 5 分を超えると線が切れるので `last_over_time(…[24h])` で包む。`link_down` と同じ理由）、流量を `rate(snmp_interface_in_octets / out_octets[5m]) * 8`、エラーを `rate(snmp_interface_in_error_packets / out_error_packets[5m])`（sample が 60 秒になったので、10 秒ごとのポーリングのときの `[2m]` では点が 2 つしか入らない）、`sysUpTime` のパネルを CPU（`snmp_system_instant`）とメモリ（`snmp_system_utilization`）に替える。題名「netops / SNMP metrics」と uid はそのまま（系列の接頭辞が `snmp_` のままなのと同じ）
- Splunk: `netops_poll` の stanza とコメントを消し、`netops_gnmi` に `link_down` を足す
  - 対象: `source="telegraf:interface" (oper_state OR admin_state)`（語で先に絞る。60 秒ごとのカウンターを読まない）
  - target は `tags.ifName`。ループバック・管理ポート・サブインタフェースは見ない（いまの `netops_poll` / trap と同じ）
  - oper と admin は別の event で来ることがあるので、`sort 0 _time | streamstats last(state) as cur_state last(admin) as cur_admin by device kind target` で前の値を持ち越す。`link_down` の down は `cur_state!="up" AND coalesce(cur_admin,"")!="disable"`
  - detail は `<ifName> is down|up (splunk: gnmi)`。以降の前 / 今の比べ方は今の `netops_gnmi` のまま
  - `props.conf` のコメント（`fields.ifOperStatus` → `fields.oper_state`）
- `app/agentcore/evidence.py` と `tools/tools.json` の PromQL の例を `snmp_interface_oper_up{sysName="dc1-a-leaf-01"}` に

### 4. 消すもの（第 2 段）

- Telegraf: `telegraf.conf.in` の `# >>> role dialin` の区間、`telegraf.sh` の `TELEGRAF_ROLE=dialin` と `SNMP_POLL`、`lab_gnmi.star` / `lab_circuits.star`（Dockerfile の COPY も）。dialout（trap など）は残す
- Terraform: `telegraf.tf` の dialin（タスク定義・サービス・`aws_ssm_parameter.dialin_targets_*`・`SNMP_COMMUNITY`・実行ロールの `/telegraf-dialin/*`）、`variables.tf` の `snmp_agents` / `snmp_poll`、`outputs.tf` の `telegraf_dialin_*`。gnmic は 012 の `collectors.tf` の形（`kafka_collector_*` の locals、arm64、1 タスク）で足し、SSM のパスは `/<prefix>/gnmic/<出どころ>/gnmi-targets` と `/<prefix>/gnmic/gnmi-username・gnmi-password`。SG `telegraf_dialin` → `gnmic`（何も受けない。管理ネットの tcp 57400、MSK 9096 / OSS は Kafka 9092、エンドポイントへ）。ECR `<prefix>-gnmic`
- Nautobot: `nb_map.TARGET_KEYS = ("gnmi-targets",)`、`nb_sync` の入れ替え先を gnmic のサービスに、`pipeline/nautobot` の IAM と locals（SSM のパスとサービス名）
- ops: `up.sh` の `SNMP_POLL` / `snmp-community` / `-var snmp_agents` / `snmp_poll` / `link_down` の sender の分岐（gnmi に固定）と gnmic のイメージ、`deploy.env.example` / `.env.example` / `deploy-env.sh`、`down.sh`、`oss/ops/up.sh` / `down.sh`
- lab: `lab.sh forward` の udp 161、`lab.sh telegraf` は trap だけに（`telegraf test` / `gnmi` を消す）。gnmic のタスクに入って 1 回取る手当ては `lab.sh` ではなく stream の output `gnmic_exec_command`（PC から ECS Exec で `gn get`。設計方針 1）。lab の EC2 のロールは ECS Exec も gnmic の ECR も持たないため。`lab_topology.py --snmp-agents`。SR Linux の `snmp-server` は trap のために残す。`failover` / `check` の手元の snmpwalk は残す
- 手元の compose: `telegraf-dialin` → `gnmic` のサービス、`up.sh` の `SNMP_AGENTS`（`check.sh` の `count(snmp_interface_ifOperStatus)` → `count(snmp_interface_oper_up)` は系列名の変更と一緒に第 1 段で済ませる）
- 第 2 段の実装で決めた細目（方針・範囲は上のまま）
  - Telegraf は受ける側（trap）だけになるので、`TELEGRAF_ROLE` ごと外す（`# >>> role` の区間も、`telegraf.sh` の役割の分岐も無くす）。`outputs.kafka` の `metrics` / `gnmi` も外す（書くのは trap の `traps` だけ）。`telegraf.sh render` は up.sh の値を受けない
  - `tg test` / `tg gnmi` と `lab.sh telegraf test|gnmi` は消さずに「cycle 013 でやめた。gNMI を 1 回取るのは `gn get`（`gnmic_exec_command`）」と出して exit 1（古い手順を打った人を迷わせない）
  - `lab.sh` は gNMI の資格情報を持たない（gnmic が SSM から受ける）。`SNMP_COMMUNITY` は `trap-test` のために残す。`ops/lab-common.sh` の `LAB_SNMP_COMMUNITY` は使う所が無くなるので消す
  - 手元の compose: `gnmic` のサービス（`KAFKA_AUTH=none`。`GNMI_TARGETS` は `docker/compose/up.sh` が lab の定義から作る）
  - `SNMP_POLL`: 前の `deploy.env` で止まらないよう `deploy-env.sh` は読み、`ops/up.sh` / `oss/ops/up.sh` は書いてあれば注意を出すだけ（止めない）。`link_down` の Grafana の sender は `GRAFANA` と `SINK_PROMETHEUS` だけで決まり、`SNMP_POLL` に依らない
  - SG の入れ替えの守り: base/core の state に `telegraf_dialin` の SG が残り、stream がそれを使っていれば、`up.sh` / `oss/ops/up.sh` は何も作る前に止まり `down.sh` を案内する（2026-10-04 の dialout / dialin の改名と同じ形）
  - SSM: `/<prefix>/telegraf-dialin/` の 3 つ（`snmp-community` も）は作らない。前の回の分は `down.sh` が ManagedBy のタグで消す。OSS の `oss/ops/up.sh` は `/<prefix>/gnmic/gnmi-username`・`gnmi-password` を作り、gnmic のイメージを `build_gnmic`（`ops/up-common.sh` の `GNMIC_VERSION`）で作る。OSS の SecureString は 13 個
  - Nautobot の seed は機器の SNMP のサービス（udp 161）を残す（機器が SNMP を喋る印で `enabled` を決める。取りにはいかない）。`nb_sync` が書き替えるのは gnmic の `gnmi-targets` だけ
  - `SKIP_LAB=1` の案内: gnmic は届かない target に 10 秒ごとに繋ぎ直し、プロセスは落ちない（2026-10-09 に手元の docker で 80 秒見た。ECS では未確認）。`up.sh` の文言と `deploy.env.example` はこの事実で書く
  - `gnmic.tf` のサービスは 1 タスク（gnmic のクラスタリング / locker は使わない。2 つ動くと同じ機器を 2 回購読して Kafka に重複が出る）
- docs: `collection.md`（共通の形の節を消し gnmic の event と読み替えの表に）、`pipeline.md`、`architecture/`（README / core / pipeline / resources の telegraf・grafana・splunk・prometheus・msk・nautobot・ssm-parameter-store・lab-ec2・vpc-perimeter）、`alert-comparison.md`、`data-stores.md`、`deploy.md`、`troubleshooting.md`、`nautobot.md`、`workflow.md`、FAQ、README、`docker/compose/README.md`

## 変更対象ファイル

- 第 1 段（012 を待たない）
  - 新規: `app/gnmic/gnmic.yaml.in`、`app/gnmic/gnmic.sh`、`docker/images/gnmic/Dockerfile`
  - `app/spark/snmp_sinks.py`、`IaC/terraform/aws-managed/pipeline/analytics/{outputs.tf,variables.tf}` と `IaC/terraform/oss/pipeline/analytics/spark.tf`（`--device-map` を splunk のジョブにも。設計方針 2）、`app/grafana/provisioning/alerting/netops-prometheus.yaml`、`app/grafana/provisioning/dashboards/metrics.json`、`app/splunk/netops_alerts/default/{savedsearches.conf,props.conf}`、`app/agentcore/evidence.py`、`tools/tools.json`、`app/containerlab/trex/{kafka_load.sh,README.md}`（`metrics` に流す見本を gnmic の event に）
  - テスト: `tests/test_stream.py`（`gnmic_message` と `gnmic.yaml.in` / `gnmic.sh`）、`test_analytics.py`（read_rows の偽の pyspark、splunk の sysName）、`test_alerts.py`（`link_down` の式、保存済みサーチ 3 + trap_clear、`netops_gnmi` の参照実装に link_down）、`test_local_compose.py`（`metrics.json` の参照と、`docker/compose/{check.sh,README.md}` の `count(snmp_interface_oper_up)`）、`tests/check_splunk_image.py`（HEC に入れる event と `netops_gnmi`）、`test_oss.py`（read_rows を回す偽の pyspark の列に `|` / `&` を足す）
- 第 2 段（012 のマージのあと）: 4. の全部。テストは `test_stream`（dialin と star の検査を消す）、`test_lab_debug`、`test_local_compose`、`test_nautobot`、`test_sync`、`test_oss`、`test_oss_ops`、`test_oss_roll`

## 再利用するもの

- 012 の `collectors.tf`（タスク定義・サービス・`kafka_collector_secrets` / `kafka_collector_execution_statements`）と `build_syslog_ng` / `dir_tag` の形、`flow_message` と read_rows の分岐の書き方（表を双子と共有する）
- `with_sysname` / `parse_device_map`、`STATE_FIELDS`、`_number`
- `netops_gnmi` の前 / 今の比べ方と、`test_alerts.py` の参照実装（`netops_poll` のもの）を `link_down` に流用
- `telegraf.tf` の dialin の `secrets` と `nb_sync` の `forceNewDeployment`

## 実装ステップ

1. （第 1 段）gnmic の設定と入口と Dockerfile、Spark の読み替えと双子と `STATE_FIELDS` と Splunk の sysName、テスト（`test_stream` / `test_analytics`）
2. （第 1 段）Grafana / Splunk のルール、ダッシュボード、evidence / tools.json、kafka_load、テスト（`test_alerts` / `test_local_compose` / `check_splunk_image`）。ここで `bash ops/check.sh`
3. （第 2 段。012 のマージのあと docs/cycle-006-design を取り込む）Terraform と ops と Nautobot と lab と compose から dialin と SNMP のポーリングを消し、gnmic を足す
4. （第 2 段）docs。AWS の検証は PM

## 検証方法

1. `bash ops/check.sh` → `すべて通過`（第 1 段と第 2 段の終わりにそれぞれ）
2. `python3 tests/test_stream.py`: `gnmic_message` に下の event を入れると、次になる（他に: 消えた event・timestamp が文字列・接頭辞付きのキー・表に無いタグ・重なるキーの後勝ち）
   - 入力（ソースから組んだ形。**実物ではない**。未確定 1）: `{"name":"interface_state","timestamp":1700000000123456789,"tags":{"interface_name":"ethernet-1/1","source":"203.0.113.31","subscription-name":"interface_state"},"values":{"/srl_nokia-interfaces:interface/oper-state":"down"}}`
   - 出力: `{"timestamp":1700000000,"name":"interface","tags":{"ifName":"ethernet-1/1","source":"203.0.113.31","subscription-name":"interface_state"},"fields":{"oper_state":"down"}}`
   - それを `prometheus_series`（device map `203.0.113.31 → dc1-a-leaf-01`）に通すと `snmp_interface_oper_up{ifName="ethernet-1/1",source="203.0.113.31",subscription_name="interface_state",sysName="dc1-a-leaf-01"} 0`
   - `gnmic.sh` を偽の環境変数で動かした `/tmp/gnmic.yaml` に、5 つの subscription・2 つの出力・target の名前が IP・資格情報の値が無いこと（`${…}` のまま）。`KAFKA_AUTH=none` で `sasl` が無い
3. `python3 tests/test_analytics.py`: 偽の pyspark で read_rows を動かし、schema に `values`、形での分岐、`mapKeyDedupPolicy`。`splunk_events` に devmap を渡すと `tags.sysName` が入る
4. `python3 tests/test_alerts.py`: `link_down` の式としきい値、保存済みサーチが `netops_gnmi` / `netops_trap` / `netops_trap_clear`、`netops_gnmi` の link_down が参照実装と同じ答え（oper と admin が別の event、admin disable、ループバック除外）。`grep -rn "ifOperStatus\|netops_poll" app/grafana app/splunk` が 0 件
5. gnmic のイメージ: `docker build` が通り、`docker run` で設定の読み込みまで進む（target に届かない・Kafka が無い前提で、設定の誤りのエラーが出ないこと）。手元の SR Linux は動かない（未確定 1）ので、subscribe の実物はここでは取らない
6. （第 2 段）`terraform fmt` / `validate`（`ops/check.sh` に入っている）
7. AWS（マネージド、PM）: gnmic の event の実物を Kafbat UI の `gnmi` と `metrics` から 1 行ずつ取り、検証 2 の入力（ソースから組んだ形）と照合する（values のキーの接頭辞、カウンターが文字列か数か、`oper-state` / `admin-state` の値の綴り、tags のキー）。違えば `tests/test_stream.py` の入力を実物に替える（未確定 1）
8. AWS（マネージド、PM）: `ops/up.sh` → `gnmic` が `runningCount 1`、`telegraf-dialin` が無い。Kafbat UI の `gnmi` / `metrics` に gnmic の event。AMP で `snmp_interface_oper_up{sysName="dc1-a-leaf-01",ifName="ethernet-1/1"}` が 1。`sudo lab fail-main` で 0 になり、Grafana と Splunk の `link_down` と `isis_down` が SNS に出て、Web のトポロジの `dc1-a-leaf-01 ethernet-1/1` が DOWN。`heal-main` で resolved。終わったら `ops/down.sh`

## 未確定事項とリスク

1. **gnmic の event の実物を取れていない。** 手元の Docker Desktop（LinuxKit 6.12.76）では SR Linux 26.7.2 の `net_inst_mgr` が `File exists (17) … creating veth "gway-2800"` で落ちる（docker run でも containerlab 0.79.0 でも同じ。design-log.md）。values のキーの接頭辞、カウンターが文字列か数か、`oper-state` / `admin-state` の値の綴りは PM の AWS の検証で確かめる。Spark は接頭辞を落とし、文字列と数の両方を受け、`lower()` で比べるので、どちらでも動くように書く
2. **on-change の `heartbeat-interval` を付けない**ため、24 時間変わらない IF は系列が古くなる（Grafana は KeepLast、Splunk は前が無い扱い）。いまの bgp / isis と同じ制約。付けるなら SR Linux が受けるかを AWS で確かめてから
3. **SNMP と gNMI で IF の名前が同じか**: SNMP の `ifName` は `ethernet-1/1`、gNMI の `interface[name]` も `ethernet-1/1`（SR Linux の表記）。トポロジ（`app/graph`）の辺の IF 名と合うかは AWS で見る
4. **012 との衝突**: 012 は `snmp_sinks.py`・`lab.sh`・`test_analytics` / `test_stream` / `test_lab_debug` / `test_local_compose`・`docker/compose/{check.sh,compose.yaml}` も変える。第 1 段の変更は別の関数と小さい塊に留め、マージで解く
5. Splunk の `link_down` の機器名は Spark が付ける `sysName` に依る。device map に無い機器は `tags.source`（IP）になる（いまの trap と同じ）
6. `system` の CPU は `cpu[index=all]` を指す（SR Linux は `all` を集計の行として持つ。いまの lab_* は `*`）。無ければ `*` に替える（AWS で確かめる）
7. **gnmic の Kafka の ACL**: マネージドは IAM と SCRAM の併用なので、SCRAM のユーザーには Kafka の ACL が要る（012 の Must fix。012 Round 2 で Spark の `ensure_topics` に ACL を足す方向）。ACL は 012 Round 2 の仕組みに乗せる（`gnmi` と `metrics` のトピックの Write・Describe を `User:collectors` か別のユーザーに）。012 Round 2 がマージされたら揃える。それまでのマネージドの gnmic は Kafka に書けない見込み（OSS は認証なしなので影響しない）
