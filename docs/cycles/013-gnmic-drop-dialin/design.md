# gNMI を gnmic に移し、SNMP のポーリングと telegraf-dialin を外す（013）

設計: PM(fable-5.1) / effort: high

BACKLOG 28「コレクターを gNMI / SNMP trap / syslog-ng / GoFlow2 の 4 種にする」の後半。**012（MSK の SCRAM。gnmic の Kafka の出力は SASL/SCRAM で書く）と 011（lab の機器名 `dc1-a-leaf-01` 等。gnmic の対象の一覧が変わる）のあと**に始める。

## 背景

- ユーザーの決定（2026-10-08）: gNMI は Telemetry 専用のコレクター（gnmic を推奨。未確認）で受け、telegraf-dialin（gNMI の subscribe + SNMP のポーリング）は要らなくなる。SNMP のポーリング（`ifTable` / `sysName`）が消えるので、**Grafana の `link_down` と機器の突き合わせを gNMI の `/interface/oper-state` と hostname に乗せ替える**
- gnmic（`gnmic.openconfig.net`、2026-10-08 確認）: Kafka の出力は `format: event`（既定）で、1 メッセージが `[{"name":"<subscription>","timestamp":<ns>,"tags":{"source":"<host:port>","subscription-name":…},"values":{"/path":value}}]` の**配列**。`sasl: {user, password, mechanism}` の mechanism は PLAIN / SCRAM-SHA-256 / SCRAM-SHA-512 / OAUTHBEARER（MSK の IAM は無い → 012 の SCRAM を使う）。`tls: {skip-verify}` あり。`event-*` の processor（`event-strings` で名前の置換、`event-to-tag` など）
- いまの gNMI（`telegraf.conf.in` の dialin、`app/telegraf/lab_gnmi.star` / `lab_circuits.star`）: Telegraf の `inputs.gnmi` が SR Linux 6 台を subscribe し、Starlark で共通の形（`device_cpu` / `device_memory` / `if_stats` / `sessions` / `circuits`）に変える。この共通の形を読むのはダッシュボードでもエージェントでもなく、**docs とテストだけ**（`docs/collection.md` L141-152、`tests/test_stream.py` L156 / L179-186 / L283-363、`test_lab_debug.py` L299）。そのため Starlark の移植はしない（共通の形は gnmic の event をそのまま Spark で読む形に置き換える）
- Grafana の `link_down`（`app/grafana/provisioning/alerting/netops-prometheus.yaml`、uid `nwc-link-down`）は `snmp_interface_ifOperStatus … unless on (sysName, ifName) (snmp_interface_ifAdminStatus == 2)` で、SNMP のポーリングの measurement に乗っている。`bgp_down` / `isis_down` は gNMI の `last_over_time(…[24h])`。Splunk の保存済みサーチも同じ measurement を見る（実装時に `app/splunk/` を grep して列挙する）
- 対象の一覧: `ops/up.sh` の `GNMI_TARGETS` / `SNMP_AGENTS`（SSM の `/<prefix>/telegraf-dialin/*`）と、Nautobot から流す `dialin_targets_from_nautobot`（`aws_ssm_parameter.dialin_targets_lab/nautobot`、`nb_sync` の `TARGET_KEYS`）。gnmic は設定ファイルの `targets:` で持つので、**SSM の一覧 → gnmic の設定** の変換が要る

## 設計方針

### 1. gnmic を ECS のサービスにする

- イメージ: `ghcr.io/openconfig/gnmic:<tag>`（arm64 あり。未確認なら実装の最初に `docker manifest inspect`）。ECR `<prefix>-gnmic`。設定は `app/gnmic/gnmic.yaml.in` + `app/gnmic/gnmic.sh`（環境変数と SSM の一覧から `targets:` を書いて `gnmic subscribe` を起動。`docker/images/gnmic/Dockerfile` で COPY。`dir_tag`）
- 資格情報: `username` / `password` は SSM の `gnmi-username` / `gnmi-password`（`ensure_fixed_secret`。いまと同じ）を ECS の `secrets` で受ける。Kafka は 012 の SCRAM の secret（`valueFrom` の JSON キー）。`KAFKA_AUTH=none`（OSS・手元）では `sasl:` を書かない
- subscribe（SR Linux のパス。いまの `inputs.gnmi` の subscription をそのまま写し、`/interface[name=*]/oper-state` と `/interface[name=*]/admin-state` を足す）。`sample-interval` はいまの値を引き継ぐ。`encoding: json_ietf`
- Kafka の出力: `outputs.kafka: {address: <KAFKA_BROKERS>, topic: gnmi, format: event, sasl: {user, password, mechanism: SCRAM-SHA-512}, tls: {}}`。**processor `event-strings`** でパスの `/srl_nokia-…:` の接頭辞を落とし、`event-to-tag` で `name` を subscription の名前に揃える
- SG `gnmic`（新規。`telegraf_dialin` と同じ: 何も受けない、lab の管理ネットの tcp 57400 と msk 9096 へ）。`telegraf_dialin` の SG と `aws_api_clients` の項目を消す（SG の作り直しは土台の down の回で）
- `nb_sync`（Nautobot の Job「Telegraf とグラフ DB に同期」）: `TARGET_KEYS` を `("gnmi-targets",)` にし、書いたあとに **gnmic のサービスを `force-new-deployment`**（いまの dialin と同じ手筋）

### 2. Spark: gnmic の event を読む

- `app/spark/snmp_sinks.py` `read_rows()`: `gnmi` トピックは **配列**で届くので `from_json(ArrayType(...))` + `explode`。`values` → `fields`（`coalesce(fields, values)`）、`timestamp` は ns なので `/ 1e9`。`tags.source`（`host:port`）から `host` を取り、`tags.hostname` が無ければ `source` の host 部分を `agent_host` に。**`name` は subscription の名前**（`interface` / `bgp` / `isis` / `system`）
- `STATE_FIELDS`（L90-93）に `("interface","oper_state"): ("oper_up", "up")`、`("interface","admin_state"): ("admin_up", "enable")` を足す（文字列 → 0/1 の列。Prometheus の remote write に出る名前は `snmp_interface_oper_up` 相当 → 名前は `gnmi_interface_oper_up` にし、Grafana のルールをそれに合わせる）。既存の `bgp` / `isis` の STATE_FIELDS はパスの接頭辞が変わるだけで同じ
- `METRIC_TOPICS = "metrics,gnmi"`（`mdt` は 012 で消えている）

### 3. Grafana / Splunk のルールを gNMI に乗せ替える

- `link_down`: `gnmi_interface_oper_up{ifName!~"(lo|mgmt).*|.*[.].*"} == 0 unless on (hostname, ifName) (gnmi_interface_admin_up == 0)`。detail の `(grafana: poll)` を `(grafana: gnmi)` に。`sysName` のラベルは `hostname`（gnmic の `source` から Spark が付ける）。**ラベル名は `bgp_down` / `isis_down` と同じにする**（いまの gNMI の行が使っているもの）
- Splunk の `link_down` の保存済みサーチも同じ条件に。`app/grafana/provisioning/dashboards/` の metrics ダッシュボードの `snmp_interface_*` のパネルを `gnmi_interface_*` に
- `app/agentcore/` のトポロジの status（`ifOperStatus` を見ている箇所があれば）と `app/graph/`（status の Lambda）はアラートの本文から読むので、**アラートの `labels` のキーが変わらなければ触らない**（実装時に grep で確かめる）

### 4. 消すもの

- `telegraf.tf` の dialin のタスク定義・サービス・`dialin_credentials` の `SNMP_COMMUNITY`・`aws_ssm_parameter.dialin_targets_*`・実行ロールの `/telegraf-dialin/*`（gnmic 用に `/gnmic/*` へ改名）
- `telegraf.conf.in` の `# >>> role dialin`（L45-236）、`lab_gnmi.star` / `lab_circuits.star`、`telegraf.sh` の `TELEGRAF_ROLE=dialin` の分岐と `SNMP_POLL`
- `ops/up.sh` の `SNMP_POLL`（L277 / L895-903）、`snmp-community` の secret、`-var snmp_agents` / `snmp_poll`、`link_down` の sender の分岐（L331-342。gnmi に固定）。`deploy.env.example` の `SNMP_POLL` / `SNMP_AGENTS`
- `app/containerlab/lab.sh forward` の udp 161 の ACCEPT（tcp 57400 は gnmic の SG から）。SR Linux の `snmp-server` の設定は trap のために残す
- `docker/compose/`: `telegraf` の dialin（あれば）を `gnmic` のサービスに
- `docs/collection.md` の共通の形の表（L141-152）は「gnmic の event の形」に書き換え。`docs/pipeline.md` L34、`docs/architecture/README.md` L41、`docs/architecture/pipeline.md` L27

## 変更対象ファイル

- 新規: `app/gnmic/{gnmic.yaml.in,gnmic.sh}`、`docker/images/gnmic/Dockerfile`、`tests/test_gnmic.py`
- Terraform: `pipeline/stream/{collectors.tf（gnmic を足す）,telegraf.tf（dialin を消す）,variables.tf,outputs.tf}`、`base/core/{security_groups.tf,oss.tf}`、`base/ecr/main.tf`
- ops: `ops/up.sh`、`ops/up-common.sh`、`deploy.env.example`、`app/containerlab/lab.sh`
- アプリ: `app/telegraf/{telegraf.conf.in,telegraf.sh}`（`lab_gnmi.star` / `lab_circuits.star` は削除）、`app/spark/snmp_sinks.py`、`app/grafana/provisioning/{alerting,dashboards}/`、`app/splunk/`、`app/nautobot/netops/nb_sync.py`
- テスト: `tests/test_stream.py`（Starlark を実行している検査を gnmic の event の検査に置き換え）、`test_lab_debug.py`、`test_analytics.py`、`test_alerts.py`（link_down の式）、`test_nautobot.py`（TARGET_KEYS）
- docs: `docs/collection.md`、`docs/pipeline.md`、`docs/architecture/`、`docs/troubleshooting.md`、`README.md`

## 再利用するもの

- 012 の `collectors.tf` の形（タスク定義 / サービス / 実行ロールの secret）と `KAFKA_AUTH` の分岐
- `telegraf.tf` の dialin の `secrets` と `depends_on = [aws_ssm_parameter.dialin_targets_*]`、`nb_sync` の `force-new-deployment`
- `snmp_sinks.py` の `STATE_FIELDS` の仕組み（文字列の状態を 0/1 の列にする）
- `tests/test_alerts.py` の Grafana のルールの式を読む検査

## 実装ステップ

1. gnmic の設定と Spark の読み替え（手元の compose で lab → gnmic → Kafka → Spark → Prometheus の `gnmi_interface_oper_up` が出るまで）
2. Grafana / Splunk のルールとダッシュボードの乗せ替え、`test_alerts`
3. Terraform と ops から dialin / SNMP のポーリングを消す、`nb_sync`
4. docs。AWS の検証（PM）

## 検証方法

1. **手元の compose**: `lab.sh up` → `up.sh` → `docker compose logs gnmic` に 6 台の `target … subscription started`。Kafbat UI の `gnmi` に `[{"name":"interface","timestamp":…,"tags":{"source":"203.0.113.31:57400",…},"values":{"/interface[name=ethernet-1/1]/oper-state":"up",…}}]`。Prometheus で `gnmi_interface_oper_up{hostname="dc1-a-leaf-01",ifName="ethernet-1/1"}` が 1。`sudo lab fail-main` のあと 0 になり、Grafana の `link_down` が firing（期待出力を `build.md` に貼る）。`heal-main` で resolved
2. **`tests/test_gnmic.py`**: gnmic の event（上の実物の 1 行を固定の入力に）→ Spark の読み替え → `fields.oper_up == 1`、`timestamp` が秒。`gnmic.yaml.in` の subscription のパスに `oper-state` / `admin-state` がある。`link_down` の式が `gnmi_interface_oper_up` を見て `snmp_interface_` が残っていない（`app/grafana` / `app/splunk` を grep して 0 件）
3. **AWS（マネージド）**: `ops/up.sh` → `ecs describe-services` の `gnmic` が `runningCount 1`、`telegraf-dialin` が無い、SSM に `/<prefix>/telegraf-dialin/` が無い。`sudo lab fail-main` → Grafana / Splunk の `link_down` と `isis_down` が SNS に出て、Web のトポロジの `dc1-a-leaf-01 ethernet-1/1` が DOWN、`heal-main` で戻る。Nautobot の Job で `gnmi-targets` を書き直すと gnmic が入れ替わる。**終わったら `ops/down.sh`**
4. `bash ops/check.sh` が `すべて通過`

## 未確定事項とリスク

1. **gnmic の event の JSON の実物**（配列・`values` のキーがフルパス）は docs の例しか見ていない。実装の最初に手元の compose で 1 行取って、Spark の読み替えとテストの固定の入力にする（「現物を読まずに書式を推測しない」）
2. **SR Linux の `/interface/oper-state` の値**は `up` / `down` だが、`admin-state` は `enable` / `disable`（YANG の `srl_nokia-interfaces`）。`STATE_FIELDS` の真の値は実物で確かめる
3. **`ifName` / `hostname` のラベル名**: いまの Grafana のルールは `sysName` / `ifName`（SNMP）と、gNMI の行は別の名前を使っているかもしれない。実装時に `netops-prometheus.yaml` の 3 つのルールの `on (...)` を揃える。ラベル名が変わるとアラートの `labels` が変わり、`app/temporal` の `link_down` の読み取り（`test_workflow`）に響く → 変えない方に揃える
4. **SNMP のポーリングを消すと `sysName` が無くなる**ので、機器名は gnmic の `source`（IP:port）から `device_map`（`app/agentcore/data/devices.yaml`）で引く。gnmic の `targets:` の名前を機器名にすれば `tags.source` にその名前が入る（gnmic は target の名前を source にする仕様。未確認 → 1 で確かめる）
5. 対象の一覧を Nautobot から流す経路（`dialin_targets_from_nautobot`）は gnmic でも同じ SSM の鍵で動くが、**gnmic は設定ファイルを読み直さない**ので入れ替え（`force-new-deployment`）が要る。いまの dialin と同じなので手間は同じ
