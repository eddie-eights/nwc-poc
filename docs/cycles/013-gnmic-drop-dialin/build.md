# build: gNMI を gnmic に移し、SNMP のポーリングと telegraf-dialin を外す（013）

## Round 1

実装モデル: opus-5.5 / effort: xhigh（get_session の値。自分のセッションの effort は set_session_effort で変えられないので、既定の high に下げていない）

### 第 1 段（44c7ccd・bd10786 と、この commit）

- 実装: gnmic の設定・入口・イメージ（`app/gnmic/`、`docker/images/gnmic/Dockerfile`）、Spark の gnmic の読み替え（`gnmic_message` と read_rows の分岐、`GNMI_MEASUREMENTS` / `GNMI_TAGS`、`STATE_FIELDS` の `oper_state` / `admin_state`、splunk の devmap）、Grafana の `link_down`（`snmp_interface_oper_up` の 24h `last_over_time` と admin disable の除外、`lt 0.5`）とダッシュボード、Splunk の `netops_poll` を消して `netops_gnmi` に link_down、evidence / tools.json の例、kafka_load と TRex の README、テスト
- event の形は**ソースから組んだもの（実物ではない）**。gnmic v0.49.0 のソース（`formatters/event.go`・`outputs/kafka_output`）から組んだ。手元の SR Linux は動かないので、実物の照合は AWS の検証 7（PM）。design.md の未確定 1
- 012 のマージ（bd10786）: 衝突は `snmp_sinks.py` の read_rows だけで、`F.when(flows).when(gnmic).otherwise(telegraf)` に解いた
- 設計から足したもの（design.md に先に書き足し、design-log に 1 行）: splunk のジョブにも `--device-map`（Terraform 3 本）、ダッシュボードの `last_over_time` / `[5m]`、`docker/compose/{check.sh,README.md}` の系列名を第 1 段へ前倒し、`test_oss.py` の偽の pyspark に `|` / `&`

#### 検証（第 1 段の終わり。最後の編集のあとに取り直した出力）

1. `bash ops/check.sh`

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（aws-managed 9・oss 9 の全ルートが OK）
== 3. スクリプトの構文
bash -n: 28 本
構文エラーなし
== 4. 模擬テスト
通過 157 / 失敗 0      (test_alerts)
通過 504 / 失敗 0      (test_analytics)
通過 161 / 失敗 0
通過 78 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 104 / 失敗 0
通過 132 / 失敗 0      (test_local_compose)
69 項目すべて通過
通過 172 / 失敗 0      (test_oss)
通過 181 / 失敗 0
通過 66 / 失敗 0
通過 115 / 失敗 0      (test_stream)
通過 103 / 失敗 0
通過 327 / 失敗 0

すべて通過
exit=0
```

1 回目は `tests/test_oss.py` が落ちた（原因: read_rows の gnmic の条件 `msg["fields"].isNull() & (msg["values"].isNotNull() | msg["deletes"].isNotNull())` を、test_oss の偽の列 `AnyObj` が `|` を持たずに受けられない。`AnyObj` に `__or__` / `__and__` を足した）。

```
  File ".../app/spark/snmp_sinks.py", line 275, in read_rows
    gnmic = msg["fields"].isNull() & (msg["values"].isNotNull() | msg["deletes"].isNotNull())
TypeError: unsupported operand type(s) for |: 'AnyObj' and 'AnyObj'
!! tests/test_oss.py が失敗した
```

2. `tests/test_stream.py`: `通過 115 / 失敗 0`
3. `tests/test_analytics.py`: `通過 504 / 失敗 0`
4. `tests/test_alerts.py`: `通過 157 / 失敗 0`。`grep -rn "ifOperStatus\|netops_poll" app/grafana app/splunk` → 出力なし、`exit=1`（0 件）
5. gnmic のイメージ: `docker build -q -t nwc-gnmic-check:013 -f docker/images/gnmic/Dockerfile app/gnmic` → `sha256:cad1fb0d013a…`。`docker run`（`KAFKA_AUTH=none`、`KAFKA_BROKERS=127.0.0.1:9092`、target 2 台 `192.0.2.11` / `192.0.2.12`、偽の資格情報）で 20 秒:

```
/tmp/gnmic.yaml を作った（gnmi: 2 台 "192.0.2.11:57400", "192.0.2.12:57400" / brokers: 127.0.0.1:9092 / topics: gnmi, metrics / kafka auth: none）
level=INFO msg="gnmic version" version=0.49.0 commit=ce0d4173
level=INFO msg="using config file" path=/tmp/gnmic.yaml
level=INFO msg="starting output" type=kafka   (2 回)
level=INFO msg="queuing target" target=192.0.2.11 / 192.0.2.12
level=INFO msg="initialized kafka producer" output=kafka name=metrics … "Format":"event" … "SplitEvents":true … "Topic":"metrics"
level=INFO msg="initialized kafka producer" output=kafka name=gnmi … "Format":"event" … "SplitEvents":true … "Topic":"gnmi"
level=INFO msg="sending gNMI SubscribeRequest" target=192.0.2.11 … interface/statistics mode:SAMPLE sample_interval:60000000000
level=INFO msg="sending gNMI SubscribeRequest" target=192.0.2.11 … bgp/neighbor[peer-address=*]/session-state mode:ON_CHANGE
level=INFO msg="sending gNMI SubscribeRequest" target=192.0.2.11 … platform/control[slot=*]/cpu[index=all]/total, …/memory mode:SAMPLE
level=INFO msg="sending gNMI SubscribeRequest" target=192.0.2.11 … interface[name=*]/oper-state, …/admin-state mode:ON_CHANGE
level=INFO msg="sending gNMI SubscribeRequest" target=192.0.2.11 … isis/instance[name=main]/interface[interface-name=*]/oper-state mode:ON_CHANGE
（192.0.2.12 も同じ 5 つ）
level=ERROR msg="subscription receive error" target=192.0.2.11 subscription=bgp_neighbor err="failed to create a subscribe client, target='192.0.2.11', retry in 10s. err=rpc error: code = Unavailable …
（ERROR は全部この形で、target に届かないもの。設定の誤りのエラーは無い）
running exit=0
```

6. （第 2 段）未実行
7. / 8. AWS（PM）: 未実行
- `tests/check_splunk_image.py`（ops/check.sh に入らない実機の検査。HEC の event を gnmic の形に、サーチを `netops_gnmi` に替えた）: 未実行。セルフレビューで走らせる

### 第 2 段（この commit。ae03dcb で docs/cycle-006-design を取り込んだあと）

- 実装: telegraf-dialin（タスク定義・サービス・SSM 3 つ・SG `telegraf_dialin`・`telegraf_dialin_*` の output）、SNMP のポーリング（`inputs.snmp`・`SNMP_POLL`・`snmp_poll` / `snmp_agents`・`lab_topology.py --snmp-agents`・`lab.sh forward` の udp 161）、`TELEGRAF_ROLE` と `# >>> role` の区間、`lab_gnmi.star` / `lab_circuits.star`（Dockerfile の COPY も）、Telegraf の `outputs.kafka` の `metrics` / `gnmi` を外した。Telegraf は trap（`traps`）だけ
- gnmic を足した: `IaC/terraform/aws-managed/pipeline/stream/gnmic.tf`（012 の `collectors.tf` の形。arm64・1 タスク・SCRAM 9096・`<prefix>-gnmic-exec` / `-task`・SSM `/<prefix>/gnmic/<lab|nautobot>/gnmi-targets` と `gnmi-username` / `gnmi-password`・output `gnmic_exec_command` / `gnmic_list_tasks_command`）、`IaC/terraform/oss/pipeline/stream/gnmic.tf`（認証なし 9092）、SG `gnmic`（何も受けない）、ECR `<prefix>-gnmic`、`app/gnmic/gnmic.sh get`（ECS Exec で 1 回取る）、`build_gnmic`（`ops/up-common.sh` の `GNMIC_VERSION`）
- Nautobot: `nb_map.TARGET_KEYS = ("gnmi-targets",)`、`nb_sync` の作り直し先を gnmic に、`pipeline/nautobot` の IAM / locals、Job の名前「gnmic とグラフ DB に同期」
- ops: `up.sh` / `oss/ops/up.sh` は `SNMP_POLL` が残っていれば注意だけ、base/core に `telegraf_dialin` の SG が残り stream が使っていれば何も作る前に止まる、`link_down` の Grafana の送り手は `GRAFANA` と `SINK_PROMETHEUS` だけで決まる。`down.sh` は `/<prefix>/telegraf-dialin/` の前の回の分も ManagedBy で消す
- lab / compose: `tg test` / `tg gnmi`・`lab.sh telegraf test|gnmi` は案内して exit 1、`LAB_SNMP_COMMUNITY` を消した、compose に `gnmic` を 14 番目のサービスとして足した（第 1 段の時点で compose に `telegraf-dialin` は無く、`telegraf` 1 つが `TELEGRAF_ROLE=all` だった）
- docs: design.md 4. の docs の全部（collection / pipeline / architecture / data-stores / deploy / troubleshooting / nautobot / workflow / FAQ / README / alert-comparison / hearing / docker/compose/README.md）。`troubleshooting.md` の `-var 'snmp_agents=…'` は変数が無くなって打つと落ちるので消した
- 設計から足したこと（design.md に先に書き、design-log に記録）: 設計方針 1・4 の事実の直し（1 回取るのは `lab.sh gnmic` ではなく `gnmic_exec_command` の `gn get`）と 4. の「第 2 段の実装で決めた細目」
- **012 Round 2 の ACL が入るまで、マネージドの gnmic は Kafka に書けない見込み**（SCRAM のユーザーに `gnmi` / `metrics` の Write・Describe が無い。design.md の未確定 7。AWS 未確認。OSS は認証なしなので影響しない）
- 変更ファイル: 85（`git diff --stat`: 1078 insertions / 1523 deletions）＋新規 2（2 つの `gnmic.tf`）。削除 2（`app/telegraf/lab_gnmi.star`・`lab_circuits.star`）

#### テストの期待値を変えたもの（元の期待値が誤りになった理由）

- `tests/test_stream.py`（115 → 104）: dialin の役割・`SNMP_POLL`・`inputs.snmp` / `inputs.gnmi`・Starlark（`lab_cpu` / `lab_memory` / `lab_if_counters` / MAC / circuits / `_num`）・`tg test` の検査は、検査の対象（設定の区間・.star）を消したので外した。「trap だけ」「古い環境変数を読まない」「`tg test` / `gnmi` は案内して 1」を足した
- `tests/test_lab_debug.py`（104 → 97）: ポーリング先・inputs.gnmi の 3 つ・.star・udp 161 の検査を、「ポーリング先を作らない」「gnmic の購読先は SSM から」「161/udp は通さない」「`gn get` で 1 回取る」に替えた。Telegraf の出力は 3 → 1、SG の入れ替えの守りに `telegraf_dialin` を足した、`dir_tag` の呼び元は 10 → 12（gnmic の `ops/up.sh` と `oss/ops/up.sh`）
- `tests/test_nautobot.py`（69 → 68）: `snmp-agents` のキーを消した（`nb_map.TARGET_KEYS` が 1 つ）
- `tests/test_local_compose.py`（132 → 137）: compose の gnmic のサービス（host ネットワーク・`GNMIC_VERSION`・render）を足し、Telegraf の出力は 1 つ・`SNMP_AGENTS` を渡さないに替えた
- `tests/test_oss_ops.py`（181 → 182）: gnmic のイメージのビルドを足し、OSS の SecureString は 14 → 13（`/telegraf-dialin/snmp-community` が無くなり、gnmic の 2 つは dialin の 2 つの置き換え）
- `tests/test_sync.py`・`test_oss.py`・`test_analytics.py`: 名前（dialin → gnmic）、`--snmp-agents` が無いこと、`SNMP_POLL` で送り手が変わらないこと

#### 検証（第 2 段の終わり。最後の編集のあとに取り直した出力）

1. `bash ops/check.sh`（検証方法 1 と 6。`terraform fmt` / `validate` は 1・2）

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（aws-managed 9・oss 9 の全ルートが OK）
== 3. スクリプトの構文
bash -n: 28 本
構文エラーなし
== 4. 模擬テスト
通過 157 / 失敗 0      (test_alerts)
通過 504 / 失敗 0      (test_analytics)
通過 161 / 失敗 0      (test_app)
通過 78 / 失敗 0       (test_collectors)
通過 3 / 失敗 0        (test_dashboard_config)
通過 78 / 失敗 0       (test_graph)
通過 7 / 失敗 0        (test_kb_index)
通過 97 / 失敗 0       (test_lab_debug)
通過 137 / 失敗 0      (test_local_compose)
68 項目すべて通過       (test_nautobot)
通過 172 / 失敗 0      (test_oss)
通過 182 / 失敗 0      (test_oss_ops)
通過 66 / 失敗 0       (test_oss_roll)
通過 104 / 失敗 0      (test_stream)
通過 103 / 失敗 0      (test_sync)
通過 327 / 失敗 0      (test_workflow)

すべて通過
```

テストを 1 本ずつ（`uv run --group dev --group web python tests/<t>.py`）:

```
---- test_stream rc=0
---- test_lab_debug rc=0
---- test_local_compose rc=0
---- test_sync rc=0
---- test_oss_ops rc=0
---- test_oss rc=0
---- test_oss_roll rc=0
---- test_analytics rc=0
---- test_nautobot rc=0
---- test_alerts rc=0
---- test_app rc=0
```

2. / 3. / 4. `test_stream` / `test_analytics` / `test_alerts`: 上の 1 に入っている（`通過 104` / `504` / `157`）
5. gnmic のイメージ（第 2 段の `gnmic.sh get` を足したあとに作り直した）: `docker build -q -t nwc-gnmic-check:013 -f docker/images/gnmic/Dockerfile app/gnmic` → `sha256:76d51024fc4e…`、`build rc=0`

```
---- render (scram)
rc=0
/tmp/gnmic.yaml を作った（gnmi: 1 台 "192.0.2.10:57400" / brokers: 127.0.0.1:9096 / topics: gnmi, metrics / kafka auth: SASL/SCRAM-SHA-512）
---- get (target 203.0.113.1 に届かない)
rc=1
level=INFO msg="sending gNMI GetRequest" target=203.0.113.1 … interface[name=*]/oper-state … admin-state … network-…
"203.0.113.1:57400" GetRequest failed: rpc error: code = DeadlineExceeded desc = context deadline exceeded while waiting for connections to become ready
Error: one or more requests failed
---- run 35s (target 203.0.113.1 に届かない)
running restarts=0 exit=0
15        (subscription receive error の行数。5 つの購読 × 10 秒ごと)
level=ERROR msg="subscription receive error" target=203.0.113.1 subscription=bgp_neighbor err="failed to create a subscribe client, target='203.0.113.1', retry in 10s. err=rpc error: code = Unavailable …
level=INFO msg="initialized kafka producer" output=kafka name=gnmi …
level=INFO msg="initialized kafka producer" output=kafka name=metrics …
```

`SKIP_LAB=1` の案内（届かない target に 10 秒ごとに繋ぎ直し、プロセスは落ちない）は、step 3 のときに同じ形で 80 秒見た（`running restarts=0 exit=0`）。ECS では未確認

6. `terraform fmt` / `validate`: 上の 1（18 ルートが OK）
7. / 8. AWS（PM）: 未実行
- `tests/check_splunk_image.py`: 未実行。セルフレビューで走らせる
