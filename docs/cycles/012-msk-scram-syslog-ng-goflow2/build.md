# Cycle 012 msk-scram-syslog-ng-goflow2 実装の記録

## Round 1

実装モデル: claude-opus-5-5 / effort: xhigh（このセッションの値。`set_session_effort` はこのセッションを指せず、high に下げられなかった）

エンジニア3（PM の指示）。ブランチ `feat/collectors-scram-syslog-goflow`（`docs/cycle-006-design` から）。AWS には何も立てていない・触っていない（検証 3 / 4 は PM がまとめて打つ）。Secrets Manager の値・`.env`・`deploy.env` は読んでいない。`docs/cycles/BACKLOG.md` は触っていない。

### commit

| commit | design の実装ステップ | 変えたファイル |
|---|---|---|
| ed8edf1 | 1: Spark の flows と Telegraf から syslog / MDT を外す | `app/spark/snmp_sinks.py`、`app/telegraf/{telegraf.conf.in,telegraf.sh}`、`IaC/terraform/aws-managed/pipeline/analytics/{locals,tables,variables}.tf`、`docker/compose/{compose.yaml,.env.example}`、`docker/images/telegraf/Dockerfile`、この design.md、`tests/{test_analytics,test_lab_debug,test_local_compose,test_oss,test_stream}.py`（15 files） |
| 8480a5c | 2: 手元の compose に syslog-ng と GoFlow2 | `app/syslog-ng/{syslog-ng.conf.in,syslog-ng.sh}`（新規）、`docker/images/syslog-ng/Dockerfile`（新規）、`docker/compose/{check.sh,compose.yaml,up.sh}`、`ops/up-common.sh`、`tests/test_collectors.py`（新規）、`tests/test_local_compose.py`、`tools/netflow_send.py`（新規）（10 files） |
| 5769fd6 | 3: Terraform | `IaC/terraform/aws-managed/base/core/{oss,outputs,security_groups,variables}.tf`、`base/ecr/{main,outputs}.tf`、`pipeline/stream/{collectors.tf（新規）,locals,msk,outputs,telegraf,variables}.tf`、`pipeline/stream/terraform.tfvars.example`、`IaC/terraform/oss/pipeline/stream/{collectors.tf（リンク）,kafka.tf}`、`deploy.env.example`、`ops/{deploy-env.sh,up.sh}`、`oss/ops/up.sh`、`tests/{test_analytics,test_oss,test_stream}.py`（22 files） |
| 594da74 | 4: ops と docs | `ops/{up-common.sh,up.sh,down.sh,down-common.sh,lab-common.sh}`、`oss/ops/up.sh`、`app/containerlab/{lab.sh,gen_lab.py}`、`deploy.env.example`、`CLAUDE.md`、`README.md`、`docker/compose/README.md`、`docs/{collection,data-stores,deploy,development,faq-fukuda-nwc-poc,pipeline,troubleshooting}.md`、`docs/architecture/` の 12 本、`tests/{test_analytics,test_lab_debug,test_oss_ops,test_stream}.py`（35 files） |
| （この commit） | この build.md、design.md の未確定事項 6・7、セルフレビューの直し（S1〜S3 のテスト、C5 の一時ファイル）、`docs/collection.md` の syslog の実物の 1 行、`docs/troubleshooting.md` の S4 | `docs/cycles/012-msk-scram-syslog-ng-goflow2/{build,design}.md`、`docs/{collection,development,troubleshooting}.md`、`ops/{up-common.sh,up.sh}`、`tests/{test_oss,test_oss_ops,test_stream}.py`（10 files） |

### 設計から逸脱した点

| # | 項目 | design | 実装 | 理由 |
|---|---|---|---|---|
| 1 | 4 | flows の格納先は未定 | logs と同じ（Splunk `sourcetype=netops:flows` / `source=telegraf:flow`、OpenSearch `<prefix>-snmp-logs-*`）。表は足さない | PM 了承（a）。design.md は ed8edf1 で直した |
| 2 | 2 | syslog-ng のホスト名のタグ | `sysName` | PM 了承（b）。Telegraf の `processors.rename` と同じ。design.md は ed8edf1 で直した |
| 3 | 5 | ― | デバッグ用の EC2 の Telegraf のログから `device_log` が消える。`lab.sh` の failover の「device_log（syslog）」の文言は据え置き | PM 了承（c）。文言は 013 で直す |
| 4 | 6 | `.env.example` に `SYSLOG_NG_IMAGE` / `GOFLOW2_IMAGE` | 足していない | 版は compose と `ops/up-common.sh` の定数で固定し、テストが照合する |
| 5 | 6 | goflow2 は `restart: on-failure:5` | `on-failure:10` | Kafka が無いと 1 秒で終わる。5 回では 9 秒、10 回で約 110 秒待てる（どちらも実測） |
| 6 | 2 | udp 5140 | udp と tcp の 5140 | NLB の UDP の target group のヘルスチェックは TCP。tcp で来た syslog も同じく `logs` へ。つないで切るだけでは syslog-ng の自身のログに何も出ない（下の検証の生ログ） |
| 7 | 2 | 公式の HEALTHCHECK | 制御ソケットを `/tmp` へ移し、HEALTHCHECK をそこへ向けた | nobody で動かすと既定の `/var/lib/syslog-ng/syslog-ng.ctl` が Permission denied |
| 8 | 1 | `kafka_bootstrap_brokers_scram` と `count` で切り替え | `kafka_collector_brokers` / `_auth` / `_secrets` / `_execution_statements` の 4 つの口（`msk.tf` と OSS 版の `kafka.tf` が持つ） | OSS 版は `msk.tf` を持たないので、data source は `msk.tf` に置けば `count` が要らない |
| 9 | 2・3 | 新しい ECS | Telegraf のクラスタに相乗り。ヘルスチェックはサービスごと（syslog-ng は TCP 5140、GoFlow2 は HTTP 8081 `/__health`。`/` は 404） | 1 つの表（`collector_listeners`）で target group を作るため |
| 10 | 1 | ― | 閉域のエンドポイントに `secretsmanager` を足し、stream の `ENDPOINTS` に入れた | ECS が secret を引くのに要る。design に書いていなかった |
| 11 | 5 | MDT の ops の行はステップ 4 | commit 3 に入れた | Terraform の変数を消すと `-var mdt_source_cidrs` が通らない。`MDT_SOURCE_CIDRS` は読むだけ読み、書いてあれば両方の `up.sh` が「使わない」と 1 行出す |
| 12 | 3 | ― | NLB と target group のリソース名は `telegraf_dialout` のまま | syslog の受け口を作り直さない |
| 13 | ― | `ensure_kms_key` | `ensure_msk_scram_key`。削除の予約中・無効の鍵は取り消して有効に戻す。secret が別の鍵で暗号化されていれば止める | 名前を用途に合わせた。down のあと 7 日以内に up したときに要る |
| 14 | docs | 一覧の 9 本 | `CLAUDE.md`、`README.md`、FAQ、`data-stores.md`、`troubleshooting.md`、`docker/compose/README.md`、`docs/architecture/` の 12 本まで広げた | MDT と Telegraf の syslog の記述が残っていた |
| 15 | 未確定事項 6 | KMS の鍵は「$1/月を日割り、約 ¥35」 | 削除の予約中の鍵は課金されない（取り消すと待った日数ぶん課金） | design から直した（design.md の表 L25 と未確定事項 6） |
| 16 | 未確定事項 7 | 5140 の付け替えで「apply 中に syslog が数十秒落ちる」 | 動いている stack に up.sh を打ち直すと数十分落ちる（core の SG が stream より先。初回は起きない） | design から直した（design.md の未確定事項 7。セルフレビューの C3） |

### 未確定事項

- 1（MSK に scram を in-place で足せるか）: 未確認。検証 3。
- 2（AxoSyslog のイメージの kafka）: 解消。`ghcr.io/axoflow/axosyslog:4.29.0` は arm64 があり、librdkafka が入っている。`kafka-c()` で手元の Kafka に書けた（検証 1）。
- 3（`kafka-c()` の `config()` のキー）: 手元では解消。librdkafka の名前（`security.protocol` / `sasl.mechanism` / `sasl.username` / `sasl.password`）と、syslog-ng のバッククォートの環境変数で、SCRAM-SHA-512 の Kafka（SASL_PLAINTEXT）に書けた。TLS（SASL_SSL）は MSK でしか打てないので **AWS で初めて確かめる**（検証 3）。
- 4（GoFlow2 の arm64）: 解消。`netsampler/goflow2:v2.2.7` に linux/arm64 がある。`X86_64` は使っていない。
- 6: 上の逸脱 15。
- 7: 上の逸脱 16。

```
$ bash v3/sr/images.sh（2026-10-09）
$ docker run --rm --platform linux/arm64 --entrypoint find ghcr.io/axoflow/axosyslog:4.29.0 / -xdev ( -name 'libkafka*' -o -name 'librdkafka*' )
/usr/lib/librdkafka.so.1
/usr/lib/librdkafka++.so.1
$ docker buildx imagetools inspect netsampler/goflow2:v2.2.7 | grep Platform
  Platform:    linux/amd64
  Platform:    unknown/unknown
  Platform:    linux/arm64
  Platform:    unknown/unknown
$ docker buildx imagetools inspect ghcr.io/axoflow/axosyslog:4.29.0 | grep Platform
  Platform:    linux/amd64
  Platform:    unknown/unknown
  Platform:    linux/arm/v7
  Platform:    unknown/unknown
  Platform:    linux/arm64
  Platform:    unknown/unknown
```

未確定事項 3（SCRAM を有効にした手元の Kafka。`kafka-storage format --add-scram` で資格情報を入れ、9097 で SASL_PLAINTEXT。資格情報はこの検査のための使い捨て）:

```
==== Kafka（SCRAM-SHA-512 の資格情報は format の --add-scram で入れる）
kafka ok (1)
SCRAM credential configs for user-principal 'ngtest' are SCRAM-SHA-512=iterations=4096
==== 設定に値が残っていない（/tmp/syslog-ng.conf の sasl の行）
43:      "security.protocol" => "SASL_PLAINTEXT",
44:      "sasl.mechanism" => "SCRAM-SHA-512",
45:      "sasl.username" => "`KAFKA_SASL_USER`",
46:      "sasl.password" => "`KAFKA_SASL_PASS`"
0
==== 送る
syslog を 2 つ送った
NetFlow v5 を 1 つ送った: nwc012-gf:2055/udp（10.0.0.1:12345 → 10.0.0.2:443 proto 6、10 パケット 8400 バイト）
==== logs（good だけが来るはず）
{"timestamp":1791466638,"tags":{"sysName":"leaf1","source":"172.20.0.6","severity":"notice","facility":"local7","appname":"scramtest"},"name":"device_log","fields":{"version":1,"timestamp":1791460800000000000,"severity_code":5,"message":"via SCRAM good","facility_code":23}}
org.apache.kafka.common.errors.TimeoutException
Processed a total of 1 messages
==== flows
{"type":"NETFLOW_V5","time_received_ns":1791466638544032418,"sequence_num":1,"sampling_rate":0,"sampler_address":"172.20.0.6","time_flow_start_ns":1791466637543
org.apache.kafka.common.errors.TimeoutException
Processed a total of 1 messages
==== syslog-ng（正しい）のログ
/tmp/syslog-ng.conf を作った（syslog: 0.0.0.0:5140/udp+tcp RFC5424 / brokers: nwc012-kscram:9097 / topic: logs / kafka auth: SASL/SCRAM-SHA-512）
syslog-ng: notice syslog-ng starting up; version='4.29.0'
==== syslog-ng（違う）のログ
/tmp/syslog-ng.conf を作った（syslog: 0.0.0.0:5140/udp+tcp RFC5424 / brokers: nwc012-kscram:9097 / topic: logs / kafka auth: SASL/SCRAM-SHA-512）
syslog-ng: err librdkafka: FAIL(3): [thrd:sasl_plaintext://nwc012-kscram:9097/bootstrap]: sasl_plaintext://nwc012-kscram:9097/bootstrap: SASL authentication error: Authentication failed during authentication due to invalid credentials with SASL mechanism SCRAM-SHA-512 (after 316ms in state AUTH_REQ);
syslog-ng: err librdkafka: FAIL(3): [thrd:sasl_plaintext://nwc012-kscram:9097/bootstrap]: sasl_plaintext://nwc012-kscram:9097/bootstrap: SASL authentication error: Authentication failed during authentication due to invalid credentials with SASL mechanism SCRAM-SHA-512 (after 316ms in state AUTH_REQ, 1 identical error(s) suppressed);
==== GoFlow2 のログ
time=2026-10-08T13:37:10.073Z level=INFO msg="starting GoFlow2"
time=2026-10-08T13:37:10.073Z level=INFO msg="starting collection" scheme=netflow hostname="" port=2055 count=1 workers=2 blocking=false queue_size=1000000
==== GoFlow2 のイメージの CA（-transport.kafka.tls で MSK の証明書を確かめる）
total 188
drwxr-xr-x    2 root     root          4096 Sep 17 17:32 .
drwxr-xr-x    4 root     root          4096 Sep 17 17:32 ..
-rw-r--r--    1 root     root        181724 Sep 17 13:13 ca-certificates.crt
==== Kafka の認証のログ
   3 	connection.failed.authentication.delay.ms = 100
   3 	principal.builder.class = class org.apache.kafka.common.security.authenticator.DefaultKafkaPrincipalBuilder
   1 INFO Successfully logged in. (org.apache.kafka.common.security.authenticator.AbstractLogin)
   1 INFO [SocketServer listenerType=BROKER, nodeId=1] Failed authentication with /172.20.0.4 (channelId=172.20.0.2:9097-172.20.0.4:33076-4-3) (Authentication failed during authentication due to invalid credentials with SASL 
   1 INFO [SocketServer listenerType=BROKER, nodeId=1] Failed authentication with /172.20.0.4 (channelId=172.20.0.2:9097-172.20.0.4:34954-5-2) (Authentication failed during authentication due to invalid credentials with SASL 
==== 片付け
==== 終わり
```

### テストの期待値を変えたもの

元の期待値は 012 より前の構成（MDT の受け口がある、syslog は Telegraf、コレクターは Telegraf だけ）を正しく縛っていた。design で構成を変えたので合わせた。

| テスト | 前 → 後 | 変えた理由 |
|---|---|---|
| `test_analytics` | 閉域のエンドポイント 16 → 17 本（sns 無しは 15 → 16、Splunk だけは 7 → 8） | stream が syslog-ng と GoFlow2 の資格情報を Secrets Manager から引く（逸脱 10） |
| `test_analytics` | 費用の Fargate のタスク 5 → 7 | syslog-ng と GoFlow2 |
| `test_analytics` | SG と通信の表から MDT（57000）を消し、`syslog_ng` / `goflow2` を足した（SG は 17 個）。`metric_topics` から `mdt`、`log_topics` に `flows` | design の 1・3・5 |
| `test_oss` | SG の通信の表 31 → 33 行 | `syslog_ng → kafka 9092`、`goflow2 → kafka 9092` |
| `test_stream` | `telegraf_ports` の表 → `collector_listeners` とヘルスチェックの表。mdt の行を消した | design の 3・5 |
| `test_lab_debug` | `dir_tag` の呼び元 8 → 10 | syslog-ng のイメージと GoFlow2 のミラー |
| `test_local_compose` | `MDT_PORT` を消し、syslog-ng / GoFlow2 と check.sh の 12 項目を足した | design の 5・6 |

本数: `test_analytics` 489 → 495、`test_stream` 75 → 82（セルフレビューの S3 で +1）、`test_oss` 167 → 168、`test_oss_ops` 148 → 173（C5 で +2）、`test_local_compose` 111 → 121、`test_collectors` 新規 78（`docs/development.md` も直した）。

### 検証

#### 1. 手元の compose（8480a5c の時点。マージのあとに取り直す → 下の「マージ後」）

`v3/verify1.sh`: 別のプロジェクト名（`nwc012v`）、空の env ファイルで kafka-1/2/3・syslog-ng・goflow2 だけを上げ、RFC5424 を 2 行・tcp の接続を 1 回・`tools/netflow_send.py 127.0.0.1 2055` を送り、`logs` と `flows` を読んで `down -v`。

```
==== 起こし直した回数（Kafka より先に起きた goflow2 / syslog-ng が終わって restart で戻ったか）
syslog-ng RestartCount=0
goflow2 RestartCount=4
==== GoFlow2 の /metrics
200
goflow2_flow_process_nf_total{router="127.0.0.1",version="5"} 1
goflow2_flow_traffic_packets_total{local_ip="::",local_port="2055",remote_ip="127.0.0.1",type="netflow"} 1
==== logs トピック
{"timestamp":1791467382,"tags":{"sysName":"leaf1","source":"127.0.0.1","severity":"notice","facility":"local7","appname":"sr_bgp_mgr"},"name":"device_log","fields":{"version":1,"timestamp":1791430496789012000,"severity_code":5,"procid":"1234","msgid":"BGP001","message":"BGP neighbor 10.0.0.2 state changed to IDLE","facility_code":23}}
{"timestamp":1791467382,"tags":{"sysName":"spine1","source":"127.0.0.1","severity":"err","facility":"local7","appname":"sr_linux"},"name":"device_log","fields":{"version":1,"timestamp":1791462897000001000,"severity_code":3,"message":"interface ethernet-1/1 down","facility_code":23}}
==== flows トピック
{"type":"NETFLOW_V5","time_received_ns":1791467382540526758,"sequence_num":1,"sampling_rate":0,"sampler_address":"127.0.0.1","time_flow_start_ns":1791467381540406942,"time_flow_end_ns":1791467382540406942,"bytes":8400,"packets":10,"src_addr":"10.0.0.1","dst_addr":"10.0.0.2","etype":"IPv4","proto":"TCP","src_port":12345,"dst_port":443,"in_if":1,"out_if":2,"src_mac":"00:00:00:00:00:00","dst_mac":"00
```

（この回の全文は scratchpad の `v3/verify1.out`。syslog-ng の起動直後の librdkafka の Connection refused 20 行は Kafka より先に起きたため。マージ後の回は全文を貼る）

Spark（`nwc-local-spark` の本物）で `read_rows()` が flows を読んだ行と、単体検査が使う `flow_message()` の比較:

```
{"ts": "2026-10-08 13:34:24", "topic": "flows", "measurement": "flow", "agent_host": null, "host": null, "tags_json": "{\"sampler\":\"127.0.0.1\",\"src\":\"10.0.0.1\",\"dst\":\"10.0.0.2\",\"proto\":\"TCP\",\"src_port\":\"12345\",\"dst_port\":\"443\",\"in_if\":\"1\",\"out_if\":\"2\",\"type\":\"NETFLOW_V5\"}", "fields_json": "{\"bytes\":\"8400\",\"packets\":\"10\"}", "ingested_at": "2026-10-08 13:35:38.109725", "kafka_topic": "flows", "kafka_partition": 0, "kafka_offset": 0}
{"ts": "2026-10-08 13:34:23", "topic": "logs", "measurement": "device_log", "agent_host": null, "host": null, "tags_json": "{\"sysName\":\"leaf1\",\"source\":\"127.0.0.1\",\"severity\":\"notice\",\"facility\":\"local7\",\"appname\":\"sr_bgp_mgr\"}", "fields_json": "{\"version\":\"1\",\"timestamp\":\"1791430496789012000\",\"severity_code\":\"5\",\"procid\":\"1234\",\"msgid\":\"BGP001\",\"message\":\"BGP neighbor 10.0.0.2 state changed to IDLE\",\"facility_code\":\"23\"}", "ingested_at": "2026-10-08 13:35:38.109725", "kafka_topic": "logs", "kafka_partition": 0, "kafka_offset": 0}
{"ts": "2026-10-08 13:34:23", "topic": "logs", "measurement": "device_log", "agent_host": null, "host": null, "tags_json": "{\"sysName\":\"spine1\",\"source\":\"127.0.0.1\",\"severity\":\"err\",\"facility\":\"local7\",\"appname\":\"sr_linux\"}", "fields_json": "{\"version\":\"1\",\"timestamp\":\"1791462897000001000\",\"severity_code\":\"3\",\"message\":\"interface ethernet-1/1 down\",\"facility_code\":\"23\"}", "ingested_at": "2026-10-08 13:35:38.109725", "kafka_topic": "logs", "kafka_partition": 0, "kafka_offset": 1}
flow_message: {"timestamp": 1791466464, "name": "flow", "tags": {"sampler": "127.0.0.1", "src": "10.0.0.1", "dst": "10.0.0.2", "proto": "TCP", "src_port": "12345", "dst_port": "443", "in_if": "1", "out_if": "2", "type": "NETFLOW_V5"}, "fields": {"bytes": "8400", "packets": "10"}}
tags 一致: True  fields 一致: True  ts 一致: True
```

syslog-ng の HEALTHCHECK と tcp の接続（逸脱 6・7）:

```
healthcheck={"Test":["CMD","/usr/sbin/syslog-ng-ctl","healthcheck","--timeout","5","--control","/tmp/syslog-ng.ctl"],"Interval":120000000000,"Timeout":5000000000,"StartPeriod":30000000000}
==== healthcheck（イメージの CMD をそのまま exec で打つ）
syslogng_io_worker_latency_seconds 0.00019083300000000001
syslogng_mainloop_io_worker_roundtrip_latency_seconds 0.00020599999999999999
syslogng_internal_events_queue_usage_ratio 0
exit=0
==== 既定の制御ソケット（直す前の公式の HEALTHCHECK と同じ）
Error connecting control socket, socket='/var/lib/syslog-ng/syslog-ng.ctl', error='Permission denied'
exit=1
==== tcp に 3 回つないで切る
ok
==== 自身のログ（Syslog connection の行が無いこと。数を数える）
0
```

#### 2. syslog の形の一致（Telegraf 1.40.1 と syslog-ng 4.29.0。各 34 行）

全 severity（facility local7）と全 facility（severity info）、頭の無い行、本文の前後に空白のある行を RFC5424 / RFC3164 で両方に送り、`(appname, message)` で突き合わせた（`v2/run_cmp.sh`）。Telegraf の実物の 1 行と syslog-ng の 1 行は `docs/collection.md` に貼った。キーの集合と型は `tests/test_collectors.py` が縛る（78 / 0）。

```
2026-10-08T13:23:56Z E! [inputs.syslog] Error in plugin: expecting a priority value within angle brackets [col 0]
2026-10-08T13:24:07Z E! [inputs.syslog] Error in plugin: expecting a priority value within angle brackets [col 0]
2026-10-08T13:24:07Z E! [inputs.syslog] Error in plugin: expecting a sequence number (from 1 to max 255 digits) [col 5]
==== RFC5424: telegraf 32 行 / syslog-ng 33 行
  差: app15_6|msg fac=15 sev=6
    telegraf : {"fields": {"facility_code": 15, "message": "msg fac=15 sev=6", "msgid": "ID15", "procid": "77", "severity_code": 6, "timestamp": 1791462896000001000, "version": 1}, "name": "device_log", "tags": {"appname": "app15_6", "facility": "cron2", "severity": "info", "sysName": "leaf1"}}
    syslog-ng: {"fields": {"facility_code": 15, "message": "msg fac=15 sev=6", "msgid": "ID15", "procid": "77", "severity_code": 6, "timestamp": 1791462896000001000, "version": 1}, "name": "device_log", "tags": {"appname": "app15_6", "facility": "solaris-cron", "severity": "info", "sysName": "leaf1"}}
  差: garbage|line without header
    telegraf : null
    syslog-ng: {"fields": {"facility_code": 1, "message": "line without header", "severity_code": 5, "timestamp": 1791465837072126000, "version": 1}, "name": "device_log", "tags": {"appname": "garbage", "facility": "user", "severity": "notice", "sysName": "172.17.0.1"}}
  差: |   spaced message
    telegraf : {"fields": {"facility_code": 23, "message": "   spaced message", "severity_code": 5, "timestamp": 1791462896000000000, "version": 1}, "name": "device_log", "tags": {"facility": "local7", "severity": "notice", "sysName": "leaf2"}}
    syslog-ng: null
  差: |spaced message
    telegraf : null
    syslog-ng: {"fields": {"facility_code": 23, "message": "spaced message", "severity_code": 5, "timestamp": 1791462896000000000, "version": 1}, "name": "device_log", "tags": {"facility": "local7", "severity": "notice", "sysName": "leaf2"}}
  一致 30
==== RFC3164: telegraf 32 行 / syslog-ng 33 行
  差: 1|2026-10-08T12:34:56Z leaf2 - - - -    sp
    telegraf : null
    syslog-ng: {"fields": {"facility_code": 23, "message": "2026-10-08T12:34:56Z leaf2 - - - -    spaced message", "severity_code": 5, "timestamp": 1791465847630286000}, "name": "device_log", "tags": {"appname": "1", "facility": "local7", "severity": "notice", "sysName": "172.17.0.1"}}
  差: app15_6|msg fac=15 sev=6
    telegraf : {"fields": {"facility_code": 15, "message": "msg fac=15 sev=6", "procid": "77", "severity_code": 6, "timestamp": 1791462896000000000}, "name": "device_log", "tags": {"appname": "app15_6", "facility": "cron2", "severity": "info", "sysName": "leaf1"}}
    syslog-ng: {"fields": {"facility_code": 15, "message": "msg fac=15 sev=6", "procid": "77", "severity_code": 6, "timestamp": 1791462896000000000}, "name": "device_log", "tags": {"appname": "app15_6", "facility": "solaris-cron", "severity": "info", "sysName": "leaf1"}}
  差: garbage|line without header
    telegraf : null
    syslog-ng: {"fields": {"facility_code": 1, "message": "line without header", "severity_code": 5, "timestamp": 1791465847630286000}, "name": "device_log", "tags": {"appname": "garbage", "facility": "user", "severity": "notice", "sysName": "172.17.0.1"}}
  差: |
    telegraf : {"fields": {"facility_code": 23, "severity_code": 5}, "name": "device_log", "tags": {"facility": "local7", "severity": "notice"}}
    syslog-ng: null
  一致 30
```

（比較の出力は `source` を除いている。`(appname, message)` が重なる行（`app23_6`）は 1 つに数えた。RFC3164 の `1|…` と `|` は同じ入力（RFC5424 の行）を、Telegraf と syslog-ng が別のキーに割ったもの）

#### 3. AWS（マネージド）

未実行（AWS で初めて確かめる。PM がまとめて打つ）。次は手元では確かめられなかった:

- 既存の MSK に scram を足す apply が in-place か（未確定事項 1）。
- 5140 の target group のヘルスチェックを HTTP → TCP に変えるのが in-place か（未確定事項 7 の落ちる時間は、何も立っていない初回の up.sh では起きないので測れない）。
- ECS が secret を引けるか: 鍵の既定のポリシーで実行ロールの `kms:Decrypt` が効くか（kms のエンドポイントは足していない。Secrets Manager が代わりに復号する前提）。
- SASL_SSL（TLS）で MSK に書けるか（未確定事項 3 の残り）。
- **SCRAM のユーザーが `logs` / `flows` に書けるか。** ACL を付けていないので、IAM と併用のクラスタでは拒否される見込みが高い（下のセルフレビューの C1。design 側の Must fix 候補で、PM の判断待ち）。
- `BatchAssociateScramSecret` に要る権限。
- IAM ロールの description の変更が in-place（`UpdateRoleDescription`）か。
- `--force-delete-without-recovery` の直後に同じ名前の secret を作れるか。鍵の削除の予約 → alias を外す順番。
- 012 より前に作った stream を 012 のコードで消せるか（`msk.tf` の data source が secret を引く）。
- NLB の UDP で送り元の IP が残るか（syslog-ng の `source` タグ）。

#### 4. AWS（OSS）

未実行（AWS で初めて確かめる）。Fargate の vCPU は quota 30 に対して ≈21.5 → ≈22.0。

#### 5. `bash ops/check.sh`（594da74、未コミットの変更が無い状態）

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（18 ルートとも OK）
== 3. スクリプトの構文
bash -n: 25 本
構文エラーなし
== 4. 模擬テスト
通過 137 / 失敗 0
通過 495 / 失敗 0
通過 158 / 失敗 0
通過 78 / 失敗 0
通過 3 / 失敗 0
通過 72 / 失敗 0
通過 7 / 失敗 0
通過 84 / 失敗 0
通過 121 / 失敗 0
68 項目すべて通過
通過 168 / 失敗 0
通過 171 / 失敗 0
通過 66 / 失敗 0
通過 81 / 失敗 0
通過 96 / 失敗 0
通過 325 / 失敗 0

すべて通過
```

マージのあとに取り直す（下の「マージ後」）。

### セルフレビュー

自分: claude-opus-5-5 / effort xhigh（`/robust` を design.md の実装ステップ 1〜6 と検証方法 1〜5 に照らして回した）。反対弁護人: Agent（general-purpose）/ model opus（claude-opus-5-5）。読み取り専用で、design.md・build.md・変更ファイルの一覧・方針・不安な箇所・ここまでの結論を渡した。

#### 指摘と片付け

| # | 分類 | [観点] | 場所 | 破綻シナリオ | 確かめたもの | 片付け |
|---|---|---|---|---|---|---|
| S1 | Should fix | [missing tests] | `IaC/terraform/aws-managed/pipeline/stream/msk.tf:47` | 実行ロールの `kms:Decrypt` を `kms:DescribeKey` に変えても全テストが通る。ECS が secret を復号できず syslog-ng / GoFlow2 のタスクが起きない | 退行の注入（下の表の M6） | 直した。`tests/test_oss.py:1030-1031` が `ScramSecret` / `ScramSecretKey` の Sid・Action・Resource を縛る |
| S2 | Should fix | [missing tests] | `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf:63` | GoFlow2 のヘルスチェックを `/` にしても通る。`/` は 404 なので target group が unhealthy のまま NetFlow / sFlow が落ちる | M9 | 直した。`tests/test_stream.py:129` がサービスごとの path（`/` / なし / `/__health`）を縛る |
| S3 | Should fix | [missing tests] | `IaC/terraform/aws-managed/pipeline/stream/collectors.tf:77,169` | syslog-ng / GoFlow2 のタスク定義から `secrets` を落としても、GoFlow2 の `-transport.kafka.sasl=scram-sha512` を落としても通る。MSK の 9096 で認証に落ちて書けない | M12・M13・M14 | 直した。`tests/test_stream.py:131-141` がタスク定義の実行ロール・`secrets`・syslog-ng の env・GoFlow2 の command を縛る |
| S4 | Should fix（design 側） | [runtime bugs] | `IaC/terraform/aws-managed/pipeline/stream/msk.tf:192-199` | secret `AmazonMSK_<prefix>-collectors` か alias `alias/<prefix>-msk-scram` が無い状態（012 より前のコードで作った stream、手で消した）で `ops/down.sh` を打つと、stream の destroy が data source の not found で止まり、stream が残る | 手元の Terraform 1.16 で同じ形（読めない data source に頼る resource）を作って destroy（下の生ログ）。`-refresh=false` なら消える | 実装で吸収していない（down.sh の destroy に `-refresh=false` を足すかは design の判断）。`docs/troubleshooting.md` に手順を足し、PM に報告する |
| C1 | Must fix 候補（design 側） | [correctness] | `IaC/terraform/aws-managed/pipeline/stream/msk.tf:122,161-162` | MSK は IAM と SCRAM を両方有効にしていて、Kafka の ACL を付ける処理がどこにも無い。IAM のアクセス制御を使うクラスタでは `allow.everyone.if.no.acl.found` が効かない（AWS の文書 iam-access-control.html）ので、SCRAM のユーザー `collectors` は `logs` / `flows` に書けない見込みが高い。syslog-ng は librdkafka の待ちが切れるたびに捨て、GoFlow2 は落ちては置き換えられる。ECS のヘルスチェックは Kafka を見ないので、どちらも healthy のまま何も届かない | `git grep -n -i -w -e acl -e acls -e allow.everyone.if.no.acl.found -- IaC ops oss app` が 0 行。手元では再現できない（手元の Kafka には IAM が無い）。根拠は AWS の文書だけ | 実装で吸収していない（ACL を誰がいつ付けるかは design の判断）。PM に差し戻しを推す。直し方の案: IAM で入れるクライアント（ワンショットのタスク等）から `User:collectors` に `logs` / `flows` の Write・Describe と、自動作成のための Create を付ける。検証 3 で最初に見るのは、syslog-ng / GoFlow2 のログの TOPIC_AUTHORIZATION_FAILED と、`logs` / `flows` に届くか |
| C2 | Nit | [runtime bugs] | `IaC/terraform/aws-managed/pipeline/stream/collectors.tf:135,226` | 2 つのサービスの `depends_on` に `aws_msk_scram_secret_association` が無い。初回の apply で関連付けより先にタスクが起きると認証に落ちる | 手元で同じ順番を作った（`v3/scram/late.sh`。SCRAM のユーザーが 0 の Kafka に syslog-ng と GoFlow2 を先に起こし、あとからユーザーを足す。生ログは下）。GoFlow2 は exit 1 で止まり、置き換え（`docker start` で真似た）のあと `flows` に書けた。syslog-ng は落ちずに再試行し、ユーザーが無い間に受けた `before-user` もあとで書いた。ECS は止まったタスクを置き換え続ける（AWS の文書 service-throttle-logic.html）。`ops/up.sh:975-978` の 7-2d は 10 分で安定しなくても警告を出して先へ進む（読んだだけ） | 直さない。`depends_on` を足しても関連付けが効くまでの最大 10 分（AWS の文書 msk-password-users.html）は残る。OSS 版と共有の `collectors.tf`（リンク）には MSK の resource を書けない |
| C3 | Should fix | [設計整合性] | `docs/cycles/012-msk-scram-syslog-ng-goflow2/design.md:123`（未確定事項 7） | 「apply 中に syslog が数十秒落ちる」と書いていたが、`3.` の core の apply が NLB → dialout の 5140 の SG のルールを先に外し、`7.` の stream の apply で syslog-ng が安定するまで落ちる。数十秒ではなく分〜数十分 | `git diff 9c40f87 -- IaC/terraform/aws-managed/base/core/security_groups.tf` で 5140 のルールが消えている。`ops/up.sh` のステップの順（3. L712 → 7. L904 → 7-2d. L973）を読んだ | design.md を直した（逸脱 16）。許容の判断は変えない（何も立っていないので初回の up.sh では起きない） |
| C4 | Nit | [保守性] | `app/syslog-ng/syslog-ng.sh:47` | コメントは「ops/up.sh が作る値は英数字だけ」だが、`ops/up-common.sh` は `token_urlsafe` なので `-` と `_` も混じる。L51 の検査は両方通すので実害は無い | `tests/test_collectors.py:133-134` が `Az09._~+/=@-` を通すことを縛る（78 / 0） | 直さない |
| C5 | Should fix | [security] | `ops/up-common.sh:330`（`ensure_msk_scram_secret`） | SCRAM のパスワードを一時ファイルに書いてから `create-secret` を呼ぶ。その最中に Ctrl+C / kill で止まると関数の後片付けまで行かず、0600 のファイルが `TMPDIR` に残る | 再現した（`v3/sr/c5.py`。偽物の aws が create-secret の最中に止まる。ファイルは stat だけ見て、中身は読んでいない。生ログは下）。SIGINT・SIGTERM のどちらも 417 バイトのファイルが 1 個残った | 直した。パスを大域の `MSK_SCRAM_INPUT` に持ち、`ops/up.sh:164` の `on_exit`（EXIT で呼ぶ）が消す。`tests/test_oss_ops.py` に SIGINT と SIGTERM の 2 項目（`ops/up.sh` の `on_exit` をそのまま使う）。退行を 2 つ入れて両方落ちる（下の生ログ）。`ensure_secret`（`:105`）と `ensure_fixed_secret`（`:142`）にも同じ穴があるが 012 の範囲外なので直していない（PM に BACKLOG の候補として渡す） |

#### 退行の注入（`v3/sr/mutate.py`。1 つずつ入れて、縛っているはずのテストを走らせ、毎回もとに戻す）

直す前:

```
== M1 up.sh: secret を鍵より先に作る: 落ちた（縛っている）
   test_stream: rc=1 最後の行: AssertionError: ops/up.sh は stream を作る回だけ、鍵 → secret → stream の apply の順に呼ぶ（msk.tf の data source が apply の時に引く）
== M2 down-common.sh: --force-delete-without-recovery を落とす: 落ちた（縛っている）
   test_oss_ops: rc=1 最後の行: AssertionError: マネージド版: 偽物の aws に知らないコマンドを打っていない
== M3 lab.sh: forward の DNAT から 2055 を落とす: 落ちた（縛っている）
   test_stream: rc=1 最後の行: AssertionError: lab.sh forward の NetFlow / sFlow の DNAT のポートは、NLB の受け口（collector_listeners）の netflow / sflow と同じ（cycle 012）
   test_lab_debug: rc=0 最後の行: 通過 84 / 失敗 0
   test_collectors: rc=0 最後の行: 通過 78 / 失敗 0
   test_oss_ops: rc=0 最後の行: 通過 171 / 失敗 0
== M4 up-common.sh: secret の鍵の照合をやめる: 落ちた（縛っている）
   test_oss_ops: rc=1 最後の行: AssertionError: ensure_msk_scram_secret: secret の暗号化の鍵が alias/x-nwc-poc-msk-scram の鍵と違えば、作り直さずに止め、消し方を出す
== M5 syslog-ng.sh: SASL の値の文字の検査をやめる: 落ちた（縛っている）
   test_collectors: rc=1 最後の行: AssertionError: scram: 英数字と ._~+/=@- は通る（sed の区切りの # を含まない）
== M6 msk.tf: kms:Decrypt を落とす: 通った（縛っていない）
   test_stream: rc=0 最後の行: 通過 81 / 失敗 0
== M7 snmp_sinks.py: flows の in_if を落とす: 落ちた（縛っている）
   test_analytics: rc=1 最後の行: AssertionError: flow_message: GoFlow2 の 1 行 → name flow、tags 9 つ（sampler / src / dst / proto / ポート 2 つ / IF 2 つ / type）、fields は bytes / packets、timestamp は time_received_ns を秒に（小数を切る）。値は文字列（Spark の from_json の StringType と同じ字面）。表に無いキー（etype / src_mac など）は持ってこない
== M8 down-common.sh: stream が消えなかったときも secret と鍵を消す: 落ちた（縛っている）
   test_oss_ops: rc=1 最後の行: AssertionError: ops/down.sh: pipeline/stream が消えなかったら SCRAM の secret と鍵は両方残す（msk.tf の data source が次の destroy でも引く）
== M9 telegraf.tf: GoFlow2 のヘルスチェックを / にする: 通った（縛っていない）
   test_stream: rc=0 最後の行: 通過 81 / 失敗 0
== M10 security_groups.tf: lab から NLB への 2055 を落とす: 落ちた（縛っている）
   test_stream: rc=1 最後の行: AssertionError: 土台の SG の通信の表に、受け口ごとの 3 本（管理ネットワーク → NLB、lab の EC2 → NLB、NLB → サービスの SG のタスクのポート）と、サービスごとのヘルスチェックの tcp がある
   test_oss: rc=0 最後の行: 通過 168 / 失敗 0
== M11 down-common.sh: 鍵の削除の予約と alias を外す順を逆にする: 落ちた（縛っている）
   test_oss_ops: rc=1 最後の行: AssertionError: マネージド版: KMS は alias/x-nwc-oss-nwc-poc-msk-scram の鍵だけ、secret を消したあとに 7 日の削除を予約し、それから alias を外す（x-nwc-poc の鍵と alias は残す）
== M12 collectors.tf: syslog-ng に secrets を渡さない: 通った（縛っていない）
   test_stream: rc=0 最後の行: 通過 81 / 失敗 0
   test_oss: rc=0 最後の行: 通過 168 / 失敗 0
```

S1〜S3 を直したあと（M6・M9・M12 と、同じ並びの M13・M14）:

```
== M6 msk.tf: kms:Decrypt を落とす: 落ちた（縛っている）
   test_stream: rc=0 最後の行: 通過 82 / 失敗 0
   test_oss: rc=1 最後の行: AssertionError: syslog-ng と GoFlow2 の口: マネージド版は MSK の SCRAM（9096 のブートストラップ、secret は AmazonMSK_<接頭辞>-collectors を data source で引いて ECS の secrets で ユーザー名とパスワードを入れ、実行ロールに secret と KMS の復号）。OSS 版は認証なしの 9092 で secret も権限も無い
== M9 telegraf.tf: GoFlow2 のヘルスチェックを / にする: 落ちた（縛っている）
   test_stream: rc=1 最後の行: AssertionError: 土台の SG の通信の表に、受け口ごとの 3 本（管理ネットワーク → NLB、lab の EC2 → NLB、NLB → サービスの SG のタスクのポート）と、サービスごとのヘルスチェックの tcp がある
== M12 collectors.tf: syslog-ng に secrets を渡さない: 落ちた（縛っている）
   test_stream: rc=1 最後の行: AssertionError: syslog-ng と GoFlow2 のタスク定義は、それぞれの実行ロールで local.kafka_collector_secrets（SCRAM のユーザー名とパスワード）を入れ、実行ロールのポリシーは local.kafka_collector_execution_statements。syslog-ng は KAFKA_BROKERS / KAFKA_AUTH、GoFlow2 は引数でブローカーと SCRAM を受ける
   test_oss: rc=0 最後の行: 通過 168 / 失敗 0
== M13 collectors.tf: GoFlow2 の SCRAM の引数を落とす: 落ちた（縛っている）
   test_stream: rc=1 最後の行: AssertionError: syslog-ng と GoFlow2 のタスク定義は、それぞれの実行ロールで local.kafka_collector_secrets（SCRAM のユーザー名とパスワード）を入れ、実行ロールのポリシーは local.kafka_collector_execution_statements。syslog-ng は KAFKA_BROKERS / KAFKA_AUTH、GoFlow2 は引数でブローカーと SCRAM を受ける
   test_oss: rc=0 最後の行: 通過 168 / 失敗 0
== M14 collectors.tf: GoFlow2 に secrets を渡さない: 落ちた（縛っている）
   test_stream: rc=1 最後の行: AssertionError: syslog-ng と GoFlow2 のタスク定義は、それぞれの実行ロールで local.kafka_collector_secrets（SCRAM のユーザー名とパスワード）を入れ、実行ロールのポリシーは local.kafka_collector_execution_statements。syslog-ng は KAFKA_BROKERS / KAFKA_AUTH、GoFlow2 は引数でブローカーと SCRAM を受ける
   test_oss: rc=0 最後の行: 通過 168 / 失敗 0
```

#### S4 の再現（`v3/sr/tfds/`。`data "external"` が flag ファイルの有無で成功・失敗する。msk.tf の data source の代わり）

```
$ terraform init
rerun this command to reinitialize your working directory. If you forget, other
commands will detect it and remind you to do so if necessary.
$ terraform apply（flag あり = secret がある）
Apply complete! Resources: 1 added, 0 changed, 0 destroyed.
$ terraform destroy（flag なし = secret が無い）

The data source received an unexpected error while attempting to execute the
program.

Program: /bin/sh
Error Message: secret not found

State: exit status 1
rc=1
$ terraform state list
data.external.secret
terraform_data.uses
$ terraform destroy -refresh=false
terraform_data.uses: Destroying... [id=e7f38d5a-479b-eb56-a7ce-d9d6cc127ba5]
terraform_data.uses: Destruction complete after 0s

Destroy complete! Resources: 1 destroyed.
rc=0
$ terraform state list
```

#### C2 の再現（`v3/scram/late.sh`。SCRAM の手元の Kafka。資格情報はこの検査のための使い捨て）

```
==== Kafka（SCRAM の受け口はあるが、ユーザーはまだ無い）
kafka ok (1)
SCRAM のユーザーの数: 0
==== syslog-ng と GoFlow2 を先に起こす（restart の決まりは付けない。ECS の置き換えは下で docker start で真似る）
GoFlow2: status=exited exit=1 restarts=0
==== GoFlow2 のログ（ユーザーが無いとき）
time=2026-10-08T16:39:02.602Z level=INFO msg="kafka: client has run out of available brokers to talk to: EOF for kafka transport"
==== syslog-ng のログ（ユーザーが無いとき）
/tmp/syslog-ng.conf を作った（syslog: 0.0.0.0:5140/udp+tcp RFC5424 / brokers: nwc012-lk:9097 / topic: logs / kafka auth: SASL/SCRAM-SHA-512）
syslog-ng: err librdkafka: FAIL(3): [thrd:sasl_plaintext://nwc012-lk:9097/bootstrap]: sasl_plaintext://nwc012-lk:9097/bootstrap: SASL authentication error: Authentication failed during authentication due to invalid crede
syslog-ng: err librdkafka: FAIL(3): [thrd:sasl_plaintext://nwc012-lk:9097/bootstrap]: sasl_plaintext://nwc012-lk:9097/bootstrap: SASL authentication error: Authentication failed during authentication due to invalid crede
syslog: before-user
==== ユーザーを足す（MSK の関連付けが遅れて効いた形）
Completed updating config for user ngtest.
GoFlow2: status=exited exit=1 restarts=0
syslog: after-user
==== logs（syslog-ng はそのまま。before-user が残るか、after-user が来るか）
latetest"}
"message":"before-user"
latetest"}
"message":"after-user"
Processed a total of 2 messages
==== syslog-ng のログ（ユーザーを足したあと）
syslog-ng: err librdkafka: FAIL(3): [thrd:sasl_plaintext://nwc012-lk:9097/bootstrap]: sasl_plaintext://nwc012-lk:9097/bootstrap: SASL authentication error: Authentication failed during authentication due to invalid crede
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: sasl_plaintext://nwc012-lk:9097/bootstrap: SASL authentication error: Authentication failed during authentication due to invalid credentials with SASL 
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: sasl_plaintext://nwc012-lk:9097/bootstrap: SASL authentication error: Authentication failed during authentication due to invalid credentials with SASL 
==== GoFlow2: 止まっていれば ECS の置き換えを docker start で真似る
GoFlow2: status=running exit=0 restarts=0
NetFlow v5 を 1 つ送った: nwc012-lgf:2055/udp（10.0.0.1:12345 → 10.0.0.2:443 proto 6、10 パケット 8400 バイト）
==== flows
{"type":"NETFLOW_V5","time_received_ns":1791477610291180169,"sequence_num":1,"sampling_rate":0,"sampler_address":"172.20
org.apache.kafka.common.errors.TimeoutException
Processed a total of 1 messages
==== 片付け
==== 終わり
```

#### C5 の再現（`v3/sr/c5.py`。直す前）と退行の注入（`v3/sr/mutate_c5.py`。直したあと）

```
SIGINT（Ctrl+C） の前（create-secret の最中）: TMPDIR のファイル 1 個
  nwc-secret.XXXXXX 権限 -rw------- 大きさ 417 バイト
  終了コード -2、出力: （なし）
SIGINT（Ctrl+C） のあと: TMPDIR のファイル 1 個
  nwc-secret.XXXXXX 権限 -rw------- 大きさ 417 バイト
  消した
SIGTERM（kill） の前（create-secret の最中）: TMPDIR のファイル 1 個
  nwc-secret.XXXXXX 権限 -rw------- 大きさ 417 バイト
  終了コード -15、出力: （なし）
SIGTERM（kill） のあと: TMPDIR のファイル 1 個
  nwc-secret.XXXXXX 権限 -rw------- 大きさ 417 バイト
  消した
割り込まないとき: 終了コード 0、出力: AmazonMSK_c5test-collectors を作った（値は出さない）
関数が戻った
割り込まないときのあと: TMPDIR のファイル 0 個
作業ディレクトリを消した: True
```

```
== C5-M1 ops/up.sh の on_exit から一時ファイルを消す行を外す
  AssertionError: ensure_msk_scram_secret: create-secret の最中に SIGINT で止まっても、値を書いた一時ファイルを残さない（ops/up.sh の on_exit が消す）
== C5-M2 ensure_msk_scram_secret を直す前（local の input）に戻す
  AssertionError: ensure_msk_scram_secret: create-secret の最中に SIGINT で止まっても、値を書いた一時ファイルを残さない（ops/up.sh の on_exit が消す）
== 戻したあとの diff --stat（直したぶんだけ）
 ops/up-common.sh | 11 ++++++-----
 ops/up.sh        |  1 +
 2 files changed, 7 insertions(+), 5 deletions(-)
```

C5 を直したあとの `bash ops/check.sh` で `test_stream` が落ちた（`AssertionError: ops/up.sh は stream を作る回だけ、鍵 → secret → stream の apply の順に呼ぶ…`）。原因は `on_exit` に足したコメントに関数名 `ensure_msk_scram_secret` を書き、テストの「1 回だけ呼ぶ」の数に入ったこと（`grep -n ensure_msk_scram_secret ops/up.sh` が 164 と 936 の 2 行）。コメントから関数名を外し、上の退行の注入を取り直した（出力は同じ）。

#### 反対弁護人に渡した疑問 (a)〜(h) の答え

反論が成立したものは無い。

| # | 疑問 | 答え | 部分的真実（残すもの） |
|---|---|---|---|
| (a) | S4（data source が引けないと destroy が止まる） | 起きるのは 012 より前に作った stream を 012 のコードで消すときだけ。troubleshooting の `-refresh=false` で足りる。down が途中で止まっても打ち直しで直る | 起きる範囲を「012 より前の stack」に絞って PM に渡す |
| (b) | 5140 の target group の変更が in-place か | matcher と timeout を書いていないので ModifyTargetGroup の in-place | HTTP → TCP の切り替えは未検証（検証 3） |
| (c) | 閉域で secret を引けるか | 引ける。perimeter の Deny に secretsmanager / kms が無く、復号は Secrets Manager が代わりに呼ぶ | ― |
| (d) | 鍵と secret の作る順・消す順 | 許容。同時に 2 つ打つと孤児の鍵が残りうるが、手で消す手順はある | 同時実行の孤児の鍵は残リスク |
| (e) | GoFlow2 の SCRAM の渡し方 | 正しい（環境変数と `-transport.kafka.tls` / `-transport.kafka.sasl=scram-sha512`） | ― |
| (f) | syslog-ng の値の扱い | 正しい（バッククォートの展開・文字の検査・値を出さない） | ― |
| (g) | Spark の flows の型 | どちらの分岐も MapType。double の割り算は下の Nit のとおり | ― |
| (h) | lab の forward の冪等 | 冪等（`unforward` を先に呼ぶ） | ― |

`auto.create.topics.enable` は効くが、SCRAM のユーザーがトピックを作れるかは C1 次第（手元の Kafka は ACL が無いので作れた）。

#### ジンテーゼ（アンチテーゼの前と後で変わったこと）

- ACL: 「検証 3 で確かめる未確定事項」の 1 行 → design 側の Must fix 候補（PM に差し戻しを推す）。
- 未確定事項 7: 数十秒 → 動いている stack に打ち直すと数十分（design を直した。許容は変えない）。C5 の一時ファイルの穴を直した。
- 手元で確かめられない残り（検証 3 の一覧）は変わらない。「SCRAM のユーザーが `logs` / `flows` を自動作成できる」は C1 次第に下げた。

#### 問題なしとした観点と根拠

| 観点 | 根拠（実行したもの / 読んだだけ） |
|---|---|
| [設計整合性] Telegraf と syslog-ng の device_log の形 | 実行: 検証 2（34 行ずつの突き合わせ）と `test_collectors`（78 / 0）。違いは 4 つで、どれも build.md と collection.md に書いた |
| [correctness] flows → Spark の共通の形 | 実行: 検証 1 の Spark の本物の `read_rows()` と `flow_message()` の比較（tags / fields / ts 一致）。退行 M7 を `test_analytics` が落とす |
| [correctness] `logs` / `flows` のトピックが無くても書ける | 読んだだけ: `msk.tf:123` の `auto.create.topics.enable=true`（OSS 版の Kafka も同じ）。手元の compose では作られた（検証 1） |
| [security] SASL の値の扱い | 実行: 退行 M5（値の文字の検査）を `test_collectors` が落とす。未確定事項 3 の生ログで、展開後の設定ファイルにバッククォートの環境変数名だけが残り値が無い |
| [security] 閉域の Deny が secret の取得を止めない | 読んだだけ: `perimeter.tf` の `perimeter_denied_actions` に `secretsmanager` / `kms` が無い。stream の `ENDPOINTS` に `secretsmanager`（`ops/up.sh:506`。`test_analytics` のエンドポイントの本数が縛る） |
| [runtime bugs] secret と鍵の作る順・消す順 | 実行: 退行 M1（鍵 → secret → apply の順）、M2（`--force-delete-without-recovery`）、M4（別の鍵の secret で止める）、M8（stream が残ったら両方残す）、M11（削除の予約 → alias を外す）をどれもテストが落とす |
| [runtime bugs] NLB の受け口 | 実行: 退行 M3（lab.sh の DNAT の 2055）、M10（lab → NLB の 2055）をテストが落とす。読んだだけ: `telegraf.tf:114` の `preserve_client_ip = true`（syslog-ng の `source` タグ。AWS で初めて確かめる） |
| [API compatibility] 消した MDT の残り | 実行: `git grep -n -I -e 57000 -e MDT -e mdt_ -- . ':!docs' ':!tests'` の 20 行はどれも「外した」の注記・戻し方、据え置きの description、`MDT_SOURCE_CIDRS` を読んで注意を出す行（`ops/deploy-env.sh:29`、両方の `up.sh`）。`telegraf_dialout` の SG と NLB の description は据え置き（変えると作り直し。`security_groups.tf:23-25`） |
| [type safety] `tools/netflow_send.py` の NetFlow v5 の形 | 実行: `struct.calcsize` がヘッダー 24 / レコード 48（RFC の NetFlow v5 と同じ）。GoFlow2 が読んだ（検証 1 の `goflow2_flow_process_nf_total ... 1`） |

#### Nit（直さない）

- `app/syslog-ng/syslog-ng.conf.in:33-34` のコメントが Telegraf との違いを 3 つしか挙げていない（RFC3164 の本文の無い行（Telegraf は facility と severity だけで書き、syslog-ng は捨てる）が抜けている。検証 2 の生ログには出ている）。
- `app/spark/snmp_sinks.py:259,301` の flows の時刻は double の割り算で秒にするので、`time_received_ns` が秒の境目の手前 128 ns 以内だと 1 秒切り上がり、Spark（long を double にしてから割る）と `flow_message`（int / int）で 1 秒ずれうる。乱数 20 万個ではずれ 0（`v3/sr/flowts.py`）:

```
$ python3 v3/sr/flowts.py
1791466638999999000: 正=1791466638 spark=1791466638 flow_message=1791466638
1791466638999999872: 正=1791466638 spark=1791466639 flow_message=1791466638
1791466638999999900: 正=1791466638 spark=1791466639 flow_message=1791466639
1791466638999999999: 正=1791466638 spark=1791466639 flow_message=1791466639
1791466638500000000: 正=1791466638 spark=1791466638 flow_message=1791466638
乱数 200000 個: spark と flow_message が違う 0、どちらかが正（切り捨て）と違う 0
```

- `docker/compose/check.sh:94` の `ss -Hlun` は Linux だけ（Mac では空になり、その項目が NG になる）。手元の compose は WSL2 が前提。
- `app/containerlab/lab.sh:240,246,268` のコメントが syslog の受け手を Telegraf と書いたまま（逸脱 3 の文言の据え置きと同じく 013 で直す）。
- `tools/netflow_send.py` に 65536 以上のポートを渡すと `OverflowError: sendto(): port must be 0-65535.` の traceback で止まる（数字でない値は「ポートは数字: abc」の 1 行で止まる。どちらも何も送らない）。

#### 直したあとの `bash ops/check.sh`（未コミットの直しを入れた状態。マージの前）

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（18 ルートとも OK）
== 3. スクリプトの構文
bash -n: 25 本
構文エラーなし
== 4. 模擬テスト
通過 137 / 失敗 0
通過 495 / 失敗 0
通過 158 / 失敗 0
通過 78 / 失敗 0
通過 3 / 失敗 0
通過 72 / 失敗 0
通過 7 / 失敗 0
通過 84 / 失敗 0
通過 121 / 失敗 0
68 項目すべて通過
通過 168 / 失敗 0
通過 173 / 失敗 0
通過 66 / 失敗 0
通過 82 / 失敗 0
通過 96 / 失敗 0
通過 325 / 失敗 0

すべて通過
```

### マージ後（`docs/cycle-006-design` の 68bb25c を取り込んだ）

PM の指示は bb24acf だったが、取り込む時点で `docs/cycle-006-design` は 68bb25c まで進んでいたので最新を取り込んだ（merge-base 9c40f87）。衝突は 30 ファイル。

#### 衝突の解き方

- 費用: theirs の値に、012 で増えた分（ours − base）を足した。lab は theirs（`m6i.xlarge`、25 セント）。stream は MSK 57 + Telegraf・syslog-ng・GoFlow2 と NLB の `(12*(3+TELEGRAF_AZ_NUM)+24+5)/10`。Kafbat UI の +2 は外した（010 で Web の EC2 に移った）。合計 約 $2.92/h、`STORES=s3` で 約 $1.99/h、SKIP_STREAM -$1.82/h、SKIP_ANALYTICS -$1.17/h、SKIP_LAB -$0.25/h。差分の足し算なので、四捨五入で ±$0.01 ずれることがある
- SG と通信の表: theirs の web → msk 9098（Kafbat UI）に、ours の syslog_ng / goflow2 → msk 9096 を足した。kafka_ui の SG は theirs どおり無い
- VPC エンドポイント: stream は ecr.api / ecr.dkr / logs / secretsmanager（theirs の Kafbat UI の分は外し、ours の secretsmanager は残した）
- lab: 機器名（dc1-a-leaf-0N / dc1-s-leaf-0N / dc1-trex-01）・TRex・x86 は theirs のまま。`app/containerlab/lab.sh` の forward は 011 の形で、2055 / 6343 の DNAT と DOCKER-USER の行が入っている
- compose: theirs の Spark の depends_on と healthcheck に、ours の syslog-ng / goflow2 を足した。`docker/compose/check.sh` は theirs の Spark の判定に ours の 3 項目を足して 14 項目
- tests: 両側の検査を両方残した（`test_analytics` の SG_KEYS は 16 個。kafka_ui を外し、syslog_ng と goflow2 を足した）
- docs: theirs の文に ours の syslog-ng・GoFlow2・SCRAM の語を足した。`docs/deploy.md` の手順の順は theirs（7-2 → 7 → 7-2b）
- 消えたはずの名前（`aws_security_group.kafka_ui`、`MDT_PORT`、`MDT_SOURCE_CIDRS`、`MDT`、旧い機器名 `dc1-leaf-0`、旧い費用 $2.81 / $2.88 / $1.96 / $1.78）が、ファイルごとに ours と theirs のどちらの数も超えていないことを `v3/merge/stale.py` で確かめた

#### 検証 1. 手元の compose（マージ後、全文）

`v3/verify1.sh` をそのまま回した。syslog-ng の librdkafka の Connection refused と goflow2 の `run out of available brokers` は Kafka より先に起きたため（goflow2 は restart で戻る）。Spark の読み替えと syslog-ng の HEALTHCHECK の個別の確認は取り直していない（theirs が変えたのは `app/spark/snmp_sinks.py` の docstring の機器名と、compose の Spark の depends_on / healthcheck だけで、読み替えの経路は変わらない。syslog-ng は下の ps で healthy）。

```
==== up
 Container nwc012v-goflow2-1 Created 
 Container nwc012v-syslog-ng-1 Created 
 Container nwc012v-kafka-1-1 Starting 
 Container nwc012v-kafka-3-1 Starting 
 Container nwc012v-kafka-2-1 Starting 
 Container nwc012v-kafka-3-1 Started 
 Container nwc012v-kafka-2-1 Started 
 Container nwc012v-kafka-1-1 Started 
 Container nwc012v-goflow2-1 Starting 
 Container nwc012v-syslog-ng-1 Starting 
 Container nwc012v-syslog-ng-1 Started 
 Container nwc012v-goflow2-1 Started 
==== kafka を待つ
kafka ok (1)
time="2026-10-09T02:24:37+09:00" level=warning msg="The \"SPLUNK_HEC_TOKEN\" variable is not set. Defaulting to a blank string."
time="2026-10-09T02:24:37+09:00" level=warning msg="The \"OPENSEARCH_PASSWORD\" variable is not set. Defaulting to a blank string."
time="2026-10-09T02:24:37+09:00" level=warning msg="The \"SPLUNK_PASSWORD\" variable is not set. Defaulting to a blank string."
time="2026-10-09T02:24:37+09:00" level=warning msg="The \"SPLUNK_HEC_TOKEN\" variable is not set. Defaulting to a blank string."
time="2026-10-09T02:24:37+09:00" level=warning msg="The \"AWS_REGION\" variable is not set. Defaulting to a blank string."
time="2026-10-09T02:24:37+09:00" level=warning msg="The \"AWS_REGION\" variable is not set. Defaulting to a blank string."
time="2026-10-09T02:24:37+09:00" level=warning msg="The \"OPENSEARCH_PASSWORD\" variable is not set. Defaulting to a blank string."
time="2026-10-09T02:24:37+09:00" level=warning msg="The \"GF_SECURITY_ADMIN_PASSWORD\" variable is not set. Defaulting to a blank string."
time="2026-10-09T02:24:37+09:00" level=warning msg="The \"OPENSEARCH_PASSWORD\" variable is not set. Defaulting to a blank string."
goflow2 running Up 6 seconds
kafka-1 running Up 14 seconds
kafka-2 running Up 14 seconds
kafka-3 running Up 14 seconds
syslog-ng running Up 14 seconds (healthy)
==== 起こし直した回数（Kafka より先に起きた goflow2 / syslog-ng が終わって restart で戻ったか）
syslog-ng RestartCount=0
goflow2 RestartCount=5
time=2026-10-08T17:24:24.637Z level=INFO msg="kafka: client has run out of available brokers to talk to: 3 errors occurred:\n\t* dial tcp [::1]:9096: connect: connection refused\n\t* dial tcp [::1]:9094: connect: connection refused\n\t* dial tcp [::1]:9095: connect: connection refused\n for kafka transport"
time=2026-10-08T17:24:25.689Z level=INFO msg="kafka: client has run out of available brokers to talk to: 3 errors occurred:\n\t* dial tcp [::1]:9095: connect: connection refused\n\t* dial tcp [::1]:9096: connect: connection refused\n\t* dial tcp [::1]:9094: connect: connection refused\n for kafka transport"
time=2026-10-08T17:24:26.819Z level=INFO msg="kafka: client has run out of available brokers to talk to: 3 errors occurred:\n\t* dial tcp [::1]:9094: connect: connection refused\n\t* dial tcp [::1]:9095: connect: connection refused\n\t* dial tcp [::1]:9096: connect: connection refused\n for kafka transport"
time=2026-10-08T17:24:28.081Z level=INFO msg="kafka: client has run out of available brokers to talk to: 3 errors occurred:\n\t* dial tcp [::1]:9096: connect: connection refused\n\t* dial tcp [::1]:9094: connect: connection refused\n\t* dial tcp [::1]:9095: connect: connection refused\n for kafka transport"
time=2026-10-08T17:24:29.790Z level=INFO msg="kafka: client has run out of available brokers to talk to: 3 errors occurred:\n\t* dial tcp [::1]:9096: connect: connection refused\n\t* dial tcp [::1]:9094: connect: connection refused\n\t* dial tcp [::1]:9095: connect: connection refused\n for kafka transport"
==== syslog-ng の起動ログ
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9095/bootstrap]: localhost:9095/bootstrap: Connect to ipv4#127.0.0.1:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9094/bootstrap]: localhost:9094/bootstrap: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9094/bootstrap]: localhost:9094/bootstrap: Connect to ipv4#127.0.0.1:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9094/bootstrap]: localhost:9094/bootstrap: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9094/bootstrap]: localhost:9094/bootstrap: Connect to ipv4#127.0.0.1:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9095/bootstrap]: localhost:9095/bootstrap: Connect to ipv6#[::1]:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9096/bootstrap]: localhost:9096/bootstrap: Connect to ipv4#127.0.0.1:9096 failed: Connection refused (after 0ms in state CONNECT, 1 identical error(s) suppressed);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9094/bootstrap]: localhost:9094/bootstrap: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9096/bootstrap]: localhost:9096/bootstrap: Connect to ipv6#[::1]:9096 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9094/bootstrap]: localhost:9094/bootstrap: Connect to ipv4#127.0.0.1:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9096/bootstrap]: localhost:9096/bootstrap: Connect to ipv4#127.0.0.1:9096 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9095/bootstrap]: localhost:9095/bootstrap: Connect to ipv4#127.0.0.1:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9096/bootstrap]: localhost:9096/bootstrap: Connect to ipv6#[::1]:9096 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9095/bootstrap]: localhost:9095/bootstrap: Connect to ipv6#[::1]:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9095/bootstrap]: localhost:9095/bootstrap: Connect to ipv4#127.0.0.1:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9094/bootstrap]: localhost:9094/bootstrap: Connect to ipv4#127.0.0.1:9094 failed: Connection refused (after 0ms in state CONNECT, 1 identical error(s) suppressed);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9095/bootstrap]: localhost:9095/bootstrap: Connect to ipv6#[::1]:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9094/bootstrap]: localhost:9094/bootstrap: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9094/1]: localhost:9094/1: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9095/2]: localhost:9095/2: Connect to ipv6#[::1]:9095 failed: Connection refused (after 0ms in state CONNECT);
==== goflow2 の起動ログ
time=2026-10-08T17:24:24.637Z level=INFO msg="kafka: client has run out of available brokers to talk to: 3 errors occurred:\n\t* dial tcp [::1]:9096: connect: connection refused\n\t* dial tcp [::1]:9094: connect: connection refused\n\t* dial tcp [::1]:9095: connect: connection refused\n for kafka transport"
time=2026-10-08T17:24:25.689Z level=INFO msg="kafka: client has run out of available brokers to talk to: 3 errors occurred:\n\t* dial tcp [::1]:9095: connect: connection refused\n\t* dial tcp [::1]:9096: connect: connection refused\n\t* dial tcp [::1]:9094: connect: connection refused\n for kafka transport"
time=2026-10-08T17:24:26.819Z level=INFO msg="kafka: client has run out of available brokers to talk to: 3 errors occurred:\n\t* dial tcp [::1]:9094: connect: connection refused\n\t* dial tcp [::1]:9095: connect: connection refused\n\t* dial tcp [::1]:9096: connect: connection refused\n for kafka transport"
time=2026-10-08T17:24:28.081Z level=INFO msg="kafka: client has run out of available brokers to talk to: 3 errors occurred:\n\t* dial tcp [::1]:9096: connect: connection refused\n\t* dial tcp [::1]:9094: connect: connection refused\n\t* dial tcp [::1]:9095: connect: connection refused\n for kafka transport"
time=2026-10-08T17:24:29.790Z level=INFO msg="kafka: client has run out of available brokers to talk to: 3 errors occurred:\n\t* dial tcp [::1]:9096: connect: connection refused\n\t* dial tcp [::1]:9094: connect: connection refused\n\t* dial tcp [::1]:9095: connect: connection refused\n for kafka transport"
time=2026-10-08T17:24:31.508Z level=INFO msg="starting GoFlow2"
time=2026-10-08T17:24:31.508Z level=INFO msg="starting collection" scheme=netflow hostname="" port=2055 count=1 workers=2 blocking=false queue_size=1000000
time=2026-10-08T17:24:31.513Z level=INFO msg="starting collection" scheme=sflow hostname="" port=6343 count=1 workers=2 blocking=false queue_size=1000000
==== 送る（host のネットワークのコンテナから）
sent: <189>1 2026-10-08T12:34:56.789012+09:00 leaf1 sr_bgp_mgr 1234 BGP001 - BGP neighbor 10.0.0.2 state changed to IDLE
sent: <187>1 2026-10-08T12:34:57.000001Z spine1 sr_linux - - - interface ethernet-1/1 down
tcp: connect+close
NetFlow v5 を 1 つ送った: 127.0.0.1:2055/udp（10.0.0.1:12345 → 10.0.0.2:443 proto 6、10 パケット 8400 バイト）
==== GoFlow2 の /metrics
200
goflow2_flow_process_nf_total{router="127.0.0.1",version="5"} 1
goflow2_flow_traffic_packets_total{local_ip="::",local_port="2055",remote_ip="127.0.0.1",type="netflow"} 1
==== logs トピック
The consumer rebalance protocol (KIP-848) is production-ready! Set group.protocol=consumer to try it out. See https://kafka.apache.org/documentation/#consumer_rebalance_protocol
{"timestamp":1791480278,"tags":{"sysName":"leaf1","source":"127.0.0.1","severity":"notice","facility":"local7","appname":"sr_bgp_mgr"},"name":"device_log","fields":{"version":1,"timestamp":1791430496789012000,"severity_code":5,"procid":"1234","msgid":"BGP001","message":"BGP neighbor 10.0.0.2 state changed to IDLE","facility_code":23}}
{"timestamp":1791480278,"tags":{"sysName":"spine1","source":"127.0.0.1","severity":"err","facility":"local7","appname":"sr_linux"},"name":"device_log","fields":{"version":1,"timestamp":1791462897000001000,"severity_code":3,"message":"interface ethernet-1/1 down","facility_code":23}}
==== flows トピック
The consumer rebalance protocol (KIP-848) is production-ready! Set group.protocol=consumer to try it out. See https://kafka.apache.org/documentation/#consumer_rebalance_protocol
{"type":"NETFLOW_V5","time_received_ns":1791480278443272180,"sequence_num":1,"sampling_rate":0,"sampler_address":"127.0.0.1","time_flow_start_ns":1791480277443079471,"time_flow_end_ns":1791480278443079471,"bytes":8400,"packets":10,"src_addr":"10.0.0.1","dst_addr":"10.0.0.2","etype":"IPv4","proto":"TCP","src_port":12345,"dst_port":443,"in_if":1,"out_if":2,"src_mac":"00:00:00:00:00:00","dst_mac":"00:00:00:00:00:00","src_vlan":0,"dst_vlan":0,"vlan_id":0,"ip_tos":0,"forwarding_status":0,"ip_ttl":0,"ip_flags":0,"tcp_flags":24,"icmp_type":0,"icmp_code":0,"ipv6_flow_label":0,"fragment_id":0,"fragment_offset":0,"src_as":0,"dst_as":0,"next_hop":"10.0.0.254","next_hop_as":0,"src_net":"10.0.0.0/24","dst_net":"10.0.0.0/24","bgp_next_hop":"","bgp_communities":[],"as_path":[],"mpls_ttl":[],"mpls_label":[],"mpls_ip":[],"observation_domain_id":0,"observation_point_id":0,"layer_stack":[],"layer_size":[],"ipv6_routing_header_addresses":[],"ipv6_routing_header_seg_left":0}
==== syslog-ng の自身のログ（tcp の accepted / closed が出ていないこと）
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9094/bootstrap: Connect to ipv4#127.0.0.1:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9094/bootstrap: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9094/bootstrap: Connect to ipv4#127.0.0.1:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9095/bootstrap: Connect to ipv6#[::1]:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9096/bootstrap: Connect to ipv4#127.0.0.1:9096 failed: Connection refused (after 0ms in state CONNECT, 1 identical error(s) suppressed);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9094/bootstrap: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9096/bootstrap: Connect to ipv6#[::1]:9096 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9094/bootstrap: Connect to ipv4#127.0.0.1:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9096/bootstrap: Connect to ipv4#127.0.0.1:9096 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9095/bootstrap: Connect to ipv4#127.0.0.1:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9096/bootstrap: Connect to ipv6#[::1]:9096 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9095/bootstrap: Connect to ipv6#[::1]:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9095/bootstrap: Connect to ipv4#127.0.0.1:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9094/bootstrap: Connect to ipv4#127.0.0.1:9094 failed: Connection refused (after 0ms in state CONNECT, 1 identical error(s) suppressed);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9095/bootstrap: Connect to ipv6#[::1]:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9094/bootstrap: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9094/1: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9095/2: Connect to ipv6#[::1]:9095 failed: Connection refused (after 0ms in state CONNECT);
syslog-ng: err librdkafka: FAIL(3): [thrd:localhost:9094/1]: localhost:9094/1: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT, 1 identical error(s) suppressed);
syslog-ng: err librdkafka: ERROR(3): [thrd:app]: rdkafka#producer-1: localhost:9094/1: Connect to ipv6#[::1]:9094 failed: Connection refused (after 0ms in state CONNECT, 1 identical error(s) suppressed);
==== down
 Container nwc012v-kafka-1-1 Removed 
 Container nwc012v-kafka-3-1 Stopped 
 Container nwc012v-kafka-3-1 Removing 
 Container nwc012v-kafka-3-1 Removed 
 Network nwc-local Removing 
 Volume nwc012v_kafka-3 Removing 
 Volume nwc012v_kafka-2 Removing 
 Volume nwc012v_kafka-1 Removing 
 Volume nwc012v_kafka-2 Removed 
 Volume nwc012v_kafka-1 Removed 
 Volume nwc012v_kafka-3 Removed 
 Network nwc-local Removed 
==== 終わり
```

#### 検証 5. `bash ops/check.sh`（マージ後。docs/development.md の本数と build.md を直したあとに取り直し、直す前の回と同じ出力）

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（18 ルートとも OK）
== 3. スクリプトの構文
bash -n: 27 本
構文エラーなし
== 4. 模擬テスト
通過 158 / 失敗 0
通過 495 / 失敗 0
通過 161 / 失敗 0
通過 78 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 104 / 失敗 0
通過 132 / 失敗 0
69 項目すべて通過
通過 172 / 失敗 0
通過 181 / 失敗 0
通過 66 / 失敗 0
通過 95 / 失敗 0
通過 103 / 失敗 0
通過 327 / 失敗 0
すべて通過
```

本数（`tests/test_*.py` の glob の順）: `test_alerts` 158、`test_analytics` 495、`test_app` 161、`test_collectors` 78、`test_dashboard_config` 3、`test_graph` 78、`test_kb_index` 7、`test_lab_debug` 104、`test_local_compose` 132、`test_nautobot` 69、`test_oss` 172、`test_oss_ops` 181、`test_oss_roll` 66、`test_stream` 95、`test_sync` 103、`test_workflow` 327（16 本。`docs/development.md` も合わせた）。全文は scratchpad の `v3/merge/check.out`（2827 行）。
