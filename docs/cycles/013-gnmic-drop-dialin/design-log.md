# 設計の経緯（013）

## Round 0

PM（SendMessage）との要件のやりとりの要約。PM の下書き（design.md、PM(fable-5.1) / effort: high、f766351 の時点）を受けて、エンジニア4 が gnmic v0.49.0 のソースとイメージ、リポジトリの参照箇所、012（feat/collectors-scram-syslog-goflow、594da74。未マージ）を調べ、判断の要る点を PM に返した。PM の決定は次の 6 つ（2026-10-09）。

### PM の決定

1. **機器名**: target の名前は IP、device map で `sysName` を引く。ユーザーの決定の「hostname」はタグの名前 `sysName` の意味で、機器の `host-name` を subscribe する意図ではない
   - 採らなかった案: `/system/name/host-name` を subscribe してタグにする。gnmic で別の subscription の値を他の event のタグにするには processor（`event-value-tag`）が要り、Spark でも別の event との突き合わせになる。device map の引き方（`with_sysname`）は Spark がすでに gNMI の行と trap で使っている
2. **系列名の接頭辞は `snmp_` のまま**
   - 採らなかった案: `gnmi_interface_*`（下書き）。`prometheus_series` は measurement に関わらず `snmp_<measurement>_<field>` を作るので、いまの gNMI の系列（bgp / isis）も `snmp_` で、名前を替えるとダッシュボード・ルール・evidence・tools.json の全部に及ぶ
3. **共通の形をやめる**（`lab_gnmi.star` / `lab_circuits.star`、`docs/collection.md` の節、`tests/test_stream.py` と `test_lab_debug.py` の検査を消す）
   - 採らなかった案: 共通の形（`device_cpu` / `if_stats` / `sessions` / `circuits` …）を残し、Starlark を Spark に移す。読むのは docs とテストだけ（下書きの背景のとおり）
4. **トピック: 状態は `gnmi`、カウンターは `metrics`**
   - 採らなかった案: 全部 `gnmi`。調べて分かったこと: Splunk と Iceberg は全トピックを読み、Prometheus は `--metric-topics`（`metrics,gnmi`）を読む（`app/spark/snmp_sinks.py` L176-183）ので、どちらにしても届く先は同じ。分け方は中身の意味（いまの `metrics` = 周期のカウンター）に合わせた
5. **subscribe は 5 つだけ**（`interface_state` / `interface_stats` / `bgp_neighbor` / `isis_interface` / `system`）
   - 採らなかった案: いまの dialin の subscription（`evpn_es` / `mac_table` / `lab_*`）を全部写す。読む側は共通の形だけで、決定 3 で無くなる
6. **event の形を整えるのは Spark**（Python の双子を `tests/test_stream.py` で縛る）
   - 採らなかった案: gnmic の processor（`event-strings` で名前を置き換え、`event-to-tag` など。下書き）。設定が gnmic の YAML と Go の正規表現に入り、Python のテストで縛れない
   - 採らなかった案: 配列で来る前提で Spark で `explode`（下書き）。Kafka の出力の `split-events: true` で 1 メッセージ 1 件のオブジェクトになる（下の事実）

### 調べて分かった事実

- gnmic の event（`pkg/formatters/event.go`、v0.49.0 の clone）
  - 形: L31-37 の `EventMsg`（`name` / `timestamp` / `tags` / `values` / `deletes`）。消えたときは `deleteToEvent`（L146-177）で `values` が無い
  - タグ: `tagsFromGNMIPath`（L182-221）がパスのキーを `<要素名の最後の ":" の後>_<キー名>` にする（`interface_name`、`neighbor_peer-address`、`interface_interface-name`、`control_slot`）。パスに origin があればパス名の先頭が `origin:` になる。`source` と `subscription-name` は `addMetaTags`（L455-466）が足す（同じ名前のタグがあれば `meta_` が付く）
  - values: `getValueFlat`（L253-）。json_ietf は JSON を平らにしてキーをパスにする（コンテナを subscribe すると葉ごとのキーになる）
- `split-events`: `pkg/outputs/kafka_output/kafka_output.go` L122、`pkg/outputs/output.go` L266-（`marshalSplit` が event ごとに `json.Marshal`）。SCRAM-SHA-512 は L823-826
- target（`pkg/config/targets.go`）: 名前と address はキー（L89-94）、port が無ければ全体の `port` を付ける（L130-153）。環境変数の展開は L268-309（`username` は常に、`password` は `$` で始まるときだけ）。outputs は `pkg/config/outputs.go` L62 の `expandMapEnv`（`msg-template` / `target-template` 以外を全部展開）
- イメージ `ghcr.io/openconfig/gnmic:0.49.0`: manifest は linux/amd64 と linux/arm64。alpine、`/bin/sh` と `/etc/ssl/certs/ca-certificates.crt` あり（bash は無い）、ENTRYPOINT `/app/gnmic`
- `GNMI_TARGETS` の形: SSM の値（`telegraf.tf` L143-160）も `lab_topology.py --gnmi-targets` も `"<IP>:57400", ...`。gnmic.sh はこれを読んで target のキーを IP にする（`tags.source` = IP。device map のキーと同じ）
- Splunk の機器名: `splunk_events`（`snmp_sinks.py` L463-485）は `sysName` を足さない。`netops_gnmi`（`savedsearches.conf` L69-114）は `coalesce('tags.sysName','tags.agent_host','tags.source')` なので gNMI の行は IP になる。temporal の `device_name`（`app/temporal/rules.py` L186-189）は IPv4 をそのまま使い、`anomaly_id` は `device#kind#target`（L192-194）なので、Grafana（`sysName`）と Splunk（IP）で別の anomaly になる。`netops_poll` が機器名を出せていたのは SNMP の Telegraf が `sysName` を付けていたから
- 012 との重なり: 012 も `snmp_sinks.py`（flows の分岐、`METRIC_TOPICS`）、`lab.sh`、`tests/test_analytics.py` / `test_stream.py` / `test_lab_debug.py` / `test_local_compose.py`、`docker/compose/check.sh` / `compose.yaml` を変える。Terraform は `collectors.tf`（OSS 版へは symlink）と `msk.tf` の `kafka_collector_*` の locals

### 手元で gnmic の event を取ろうとしたこと（取れなかった）

- SR Linux 26.7.2 を Docker Desktop（LinuxKit 6.12.76、arm64）で `docker run` と containerlab 0.79.0（`ghcr.io/srl-labs/clab` のコンテナ）の両方で立てたが、どちらも `net_inst_mgr` が `File exists (17) … creating veth "gway-2800"` で落ち、gNMI のサーバーが上がらなかった（containerlab の管理ネットのサブネットの重なりは直したうえで同じ）
- 片付け: lab のコンテナとネットワークは destroy 済み。`docker ps -a` に srl / clab / gnmic の名前のコンテナが無いことを確かめた（2026-10-09）
- そのため検証方法 2 の入力は、上のソースから組んだ形にする（design.md の未確定 1。PM が AWS で実物を確かめる）

### 設計で PM の下書きから変えたこと

- 決定 1〜6 による書き換え: 系列名（`gnmi_interface_*` → `snmp_interface_oper_up` / `admin_up`）とラベル（`hostname` → `sysName`）、processor をやめる、配列 + explode → `split-events`、`tests/test_gnmic.py` → `tests/test_stream.py`、subscription を 5 つに
- `link_down` の式: 下書きの `… == 0`（5 分の範囲）は on-change の値が来ない間に系列が消える。bgp_down / isis_down と同じ `last_over_time(…[24h])` と `lt 0.5` にした
- `tags.source` は host:port ではなく IP（target のキーに port を付けない）
- **Splunk にも `sysName` を足す（新しい判断。PM に確認する）**: `splunk_events` に device map を渡して `with_sysname` を通す。`link_down` が SNMP から gNMI に移ると Splunk の機器名が IP になり、Grafana と anomaly が分かれるため。副作用で bgp_down / isis_down の Splunk の機器名も IP から機器名に変わる（Grafana と揃う方向）
- on-change に `heartbeat-interval` を付けない（SR Linux が受けるか確かめていない。いまの bgp / isis と同じ扱い）
- 足したもの: IF の流量・エラーと CPU・メモリのパネル、`app/containerlab/trex/kafka_load.sh` / README、`app/agentcore/evidence.py` / `tools/tools.json`、`docker/compose/{check.sh,up.sh,compose.yaml}`、`tests/check_splunk_image.py`、`test_lab_debug` / `test_local_compose` / `test_nautobot` / `test_sync` / `test_oss` / `test_oss_ops` / `test_oss_roll`、Nautobot の IAM と locals（`pipeline/nautobot/`）、`docker/images/telegraf/Dockerfile`（star の COPY）、`.env.example` / `ops/deploy-env.sh` / `ops/down.sh` / `oss/ops/down.sh`
- 実装を 2 段に分けた（第 1 段 = 012 に依らないもの、第 2 段 = 012 のマージのあと Terraform / ops / docs）

### 新しい判断への PM の回答（c00e91b のあと）

- Splunk の `sysName`: **承認。** bgp / isis の Splunk の機器名が IP から機器名に変わる副作用は、Grafana と揃う方向なので受け入れる。設計本文に書き（design.md の設計方針 2）、`sysName` が付くことをテストで 1 件確かめる
- `heartbeat-interval` を付けない（Grafana は `last_over_time` 24h + `lt 0.5`、Splunk は 24 時間）: **承認**
- event の実物が取れない件: build.md に「ソースから組んだ形」と書き、design.md の未確定事項の筆頭に残す。AWS で実物を取って照合する項目を design.md の検証方法に足す（検証 7）
