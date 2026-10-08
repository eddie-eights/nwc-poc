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
