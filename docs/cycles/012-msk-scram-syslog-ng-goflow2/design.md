# MSK に SASL/SCRAM を足し、syslog-ng と GoFlow2 を立てる（MDT を外す）（012）

設計: PM(fable-5.1) / effort: high

BACKLOG 28「コレクターを gNMI / SNMP trap / syslog-ng / GoFlow2 の 4 種にする」の前半。後半（gNMI を gnmic に移し、SNMP のポーリングと telegraf-dialin を外す）は 013 で、このサイクルと 011 のあとにやる。**このサイクルは 011（lab の組み直し）と独立**で、lab の機器名や TRex に触らない。

## 背景

- ユーザーの決定（2026-10-08、確認済み）: BACKLOG 28 の括弧の中がコレクター。gNMI → Telemetry 専用のコレクター、SNMP trap → Telegraf、syslog → syslog-ng、NetFlow → GoFlow2。telegraf-dialin は要らなくなる
- 2026-10-08 の調査（実装の制約。確認日 2026-10-08）
  - **GoFlow2 と syslog-ng（librdkafka）は MSK の IAM 認証（`AWS_MSK_IAM`）を喋れない。** GoFlow2 の Kafka の transport は `-transport.kafka.sasl` に `none / plain / scram-sha256 / scram-sha512` だけ（`transport/kafka/kafka.go`。ユーザー名とパスワードは環境変数 `KAFKA_SASL_USER` / `KAFKA_SASL_PASS`）。librdkafka も OAUTHBEARER の自前の実装が要る。**MSK に SASL/SCRAM を IAM と併用で有効にし、9096 で受けるのが現実的**
  - MSK の SCRAM（AWS の `msk-password-tutorial`、2026-10-08 確認）: 資格情報は **Secrets Manager** の secret（名前は `AmazonMSK_` で始まる、種別は Other、値は `{"username":"…","password":"…"}`）で、**顧客管理の KMS キーが必須**（既定の `aws/secretsmanager` は使えない。`kms-key-id` はエイリアスでなくキー ID か ARN）。`BatchAssociateScramSecret` で付ける（Terraform は `aws_msk_scram_secret_association`）。MSK が secret にリソースポリシーを付けるので手で書き換えない。SCRAM を有効にするとクライアント通信は TLS になる
  - **SR Linux は NetFlow / IPFIX を出せない**（7730 SXR 限定。原理的）。sFlow は設定項目はあるがコンテナ版で動くか未確認（26.7.2）。フローの送り元は lab の Linux 側（011 のあとは TRex のコンテナ、またはデバッグ用の EC2 のホスト）の softflowd / hsflowd になる。**このサイクルでは GoFlow2 の受け口と Kafka までの経路を作り、lab からのフローの送り元は 013 以降に回す**（検証は手元の compose と `tools/` の偽の NetFlow 送信で行う）
- このリポジトリの決まり「シークレットは `ops/up.sh` が SSM Parameter Store の SecureString として作る」は、MSK の SCRAM が Secrets Manager + 顧客管理 KMS を強制するので、**この 1 件だけ Secrets Manager にする例外**を `CLAUDE.md` と `docs/` に書く。値は読まない・表示しないのは同じ
- MDT（`inputs.cisco_telemetry_mdt`、`mdt` トピック、`mdt_source_cidrs`）は 2026-10-08 時点で送り手が無い（lab は SR Linux、本番の Cisco は無い）。コレクターを 4 種に揃えるときに外す。戻すときは git から取る（この design.md に戻し方の目印を書く）

## 設計方針

### 1. MSK: SASL/SCRAM を IAM と併用

| 何 | どこ | 内容 |
|---|---|---|
| クラスタ | `IaC/terraform/aws-managed/pipeline/stream/msk.tf` | `client_authentication { sasl { iam = true  scram = true } }`。**既存のクラスタに scram を足すのは in-place の更新**（AWS の UpdateSecurity。未確認 → 検証 3）。`encryption_in_transit.client_broker = "TLS"` はそのまま |
| secret の関連付け | 同上 | `resource "aws_msk_scram_secret_association" "collectors" { cluster_arn = aws_msk_cluster.stream.arn  secret_arn_list = [data.aws_secretsmanager_secret.msk_scram.arn] }`。secret は **data source で ARN だけ引く**（値は state に入れない） |
| secret の作成 | `ops/up.sh`（`ops/up-common.sh` に `ensure_msk_scram_secret` を足す） | `ensure_fixed_secret` と同じ作り（存在すれば飛ばす・値を出さない・umask 077 の一時ファイル・`--cli-input-json`）。名前は `AmazonMSK_<prefix>-collectors`。ユーザー名は `collectors`、パスワードは `ensure_secret` と同じ乱数生成。KMS キーは `ops/up.sh` が `aws kms create-key`（説明 `MSK SCRAM secret key of <prefix>`、タグ ManagedBy/Project/owner）+ エイリアス `alias/<prefix>-msk-scram` で 1 回だけ作り、エイリアスがあれば使い回す。`ops/down.sh` は secret を `--force-delete-without-recovery` で消し、キーは `schedule-key-deletion --pending-window-in-days 7` にする（KMS は即時に消せない。残るのは 7 日で、課金は $1/月を日割り → 報告に書く） |
| ブローカーの口 | `msk.tf` の locals | `kafka_bootstrap_brokers_scram = aws_msk_cluster.stream.bootstrap_brokers_sasl_scram`（9096）を足す。`kafka_bootstrap_by_protocol` に `SASL_SCRAM` を足す必要は無い（Kafbat UI は IAM のまま） |
| SG | `IaC/terraform/aws-managed/base/core/security_groups.tf` | `sg_flows` に `syslog_ng → msk tcp 9096`、`goflow2 → msk tcp 9096` を足す（鍵は `"${from}-${to}-${protocol}-${port}"`）。msk ↔ msk は 9092-9098 で既に含む。**SG の description は変えない**（変えると作り直し） |
| OSS 版 | `IaC/terraform/oss/pipeline/stream/kafka.tf` | ECS の Kafka は認証なし（PLAINTEXT 9092）のまま。`kafka_client_environment = [{ KAFKA_AUTH = none }]` を 2 つの新サービスにも渡す。SCRAM の secret・KMS・association は OSS 版には無い（`oss/ops/up.sh` は作らない）。土台の `oss.tf` の通信の表に `syslog_ng → kafka 9092`、`goflow2 → kafka 9092` を足す |

資格情報の渡し方（2 つの新サービス共通）: ECS のタスク定義の `secrets` で `valueFrom = "<secret ARN>:username::"` と `":password::"`（JSON のキー選択）。実行ロールに `secretsmanager:GetSecretValue`（その secret の ARN）と `kms:Decrypt`（そのキーの ARN）。**Terraform は値に触らない。** 実装者もレビュアーも値を読まない（`aws secretsmanager get-secret-value` を打たない）。

### 2. syslog-ng（AxoSyslog）を ECS のサービスにする

- イメージ: `ghcr.io/axoflow/axosyslog:<tag>`（AxoSyslog。`kafka()` 宛先 = `kafka-c()` が librdkafka で、Debian のパッケージでは `axosyslog-mod-rdkafka` が別だが、コンテナのイメージに kafka のモジュールが入っているかは**未確認**（未確定事項 2。実装の最初に `docker run --rm <image> syslog-ng --module-registry | grep -i kafka` で確かめ、無ければ `balabit/syslog-ng` を見る）。タグは実装時の最新の安定版を `ops/up.sh` の `SYSLOG_NG_TAG` に固定し、`mirror_image`（arm64）で ECR `<prefix>-syslog-ng` に写す（`IaC/terraform/aws-managed/base/ecr/main.tf` の `pipeline_repositories` に `syslog-ng` と `goflow2` を足す）
- 受け口: udp 5140（いまの Telegraf の `inputs.syslog` と同じ。NLB の listener 5140 の target group を telegraf-dialout から syslog-ng のサービスに付け替える）。RFC 5424（`syslog(transport(udp) port(5140) flags(syslog-protocol))`。`deploy.env` の `SYSLOG_STANDARD` が RFC3164 なら `flags()` 無し）
- 書き先: Kafka の `logs` トピック。**メッセージは Telegraf の JSON の形**（Spark の `read_rows()` がそのまま読める）:

  ```
  {"fields":{"message":"<本文>","severity_code":<0-7>,"facility_code":<0-23>,"procid":"<PROGRAM の pid>","msgid":"<MSGID>","version":1,"timestamp":<送信元の時刻 ns>},
   "name":"device_log",
   "tags":{"hostname":"<HOST>","appname":"<PROGRAM>","facility":"<FACILITY>","severity":"<LEVEL>","source":"<SOURCEIP>"},
   "timestamp":<受信時刻 秒>}
  ```

  `$(format-json --scope rfc5424 …)` で作る（キーの名前は Telegraf の `inputs.syslog` が出すものと同じにして、Grafana の logs のダッシュボードと Splunk の保存済みサーチ（`device_log` の `hostname` / `severity` / `message`）を変えない。**Telegraf が出す実物の 1 行を手元の compose で取って `docs/collection.md` に貼り、syslog-ng の出力と見比べる**（検証 2）。`timestamp` は Telegraf の `json_timestamp_units` の既定と同じ秒）
- Kafka の設定（`kafka-c()`。**キーの名前は未確認**（未確定事項 3）。一般の librdkafka の名前で書く）: `bootstrap-servers("<KAFKA_BROKERS>")`、`topic("logs")`、`config("security.protocol" => "SASL_SSL", "sasl.mechanism" => "SCRAM-SHA-512", "sasl.username" => "<env>", "sasl.password" => "<env>")`。`KAFKA_AUTH=none`（OSS 版・手元）では `config()` を書かない。設定は `app/syslog-ng/syslog-ng.conf.in` + `app/syslog-ng/syslog-ng.sh`（`telegraf.sh` と同じく環境変数を sed で埋めて起動。`KAFKA_AUTH` は `scram` / `none`）。Dockerfile は要らない（公式イメージに conf をマウント … ではなく、閉域で ECS に渡す都合上、`docker/images/syslog-ng/Dockerfile` で conf.in と sh を COPY したものを ECR に置く。タグは `dir_tag` で決める = `telegraf` と同じ手筋）
- ECS: `IaC/terraform/aws-managed/pipeline/stream/collectors.tf`（新規。telegraf.tf と同じ構造: タスク定義（ARM64、0.25 vCPU / 0.5 GB）、サービス（`desired_count = 1`、NLB の target group）、実行ロール（secret と KMS）、タスクロール（EcsExec と perimeter）、ログは `/ecs/<prefix>-syslog-ng`）。SG は `syslog_ng`（新規。`telegraf_dialout` と同じ: NLB から udp 5140 を受け、msk 9096 とエンドポイントへ）

### 3. GoFlow2 を ECS のサービスにする

- イメージ: `netsampler/goflow2:<tag>`（Docker Hub。2026-10-08 の README で `-transport=kafka`、`-transport.kafka.brokers`、`-transport.kafka.topic`、`-transport.kafka.sasl`、`-transport.kafka.tls`、`-listen 'netflow://:2055,sflow://:6343'`、`-format json`、環境変数 `KAFKA_SASL_USER` / `KAFKA_SASL_PASS` を確認）。arm64 のイメージがあるかは**未確認**（未確定事項 4。無ければこのタスクだけ X86_64）。`ops/up.sh` の `GOFLOW2_TAG` で固定し、ECR `<prefix>-goflow2` に写す。Dockerfile は要らない（設定は全部フラグ）
- 受け口: udp 2055（NetFlow v5/v9/IPFIX）と udp 6343（sFlow）。NLB の listener 2055 / 6343 を足す（`telegraf_ports` の表を `collector_listeners = { trap → telegraf-dialout, syslog → syslog-ng, netflow → goflow2, sflow → goflow2 }` に広げ、サービスごとに target group を持つ）。SG `goflow2`（新規）: NLB から udp 2055 / 6343 を受け、msk 9096 へ
- 書き先: 新しいトピック `flows`。GoFlow2 の JSON（snake_case。`type, time_received_ns, sampler_address, src_addr, dst_addr, bytes, packets, proto, src_port, dst_port, in_if, out_if, etype`。`time_received_ns` はナノ秒）は Telegraf の形ではないので、**Spark 側で読み替える**（次節）。コマンド: `goflow2 -listen 'netflow://:2055,sflow://:6343' -transport=kafka -transport.kafka.brokers=<KAFKA_BROKERS> -transport.kafka.topic=flows -format=json` + SCRAM なら `-transport.kafka.tls -transport.kafka.sasl=scram-sha512`（`KAFKA_AUTH=none` では付けない。切り替えは ECS の `command` を Terraform の locals で組む）

### 4. Spark: `flows` を読む

- `app/spark/snmp_sinks.py`: `LOG_TOPICS = "traps,logs,flows"`。`read_rows()` の JSON スキーマは Telegraf の形（`name` / `tags` / `fields` / `timestamp`）なので、`flows` のときだけ**読み替えの列**を足す: `name = "flow"`、`tags = {sampler: sampler_address, src: src_addr, dst: dst_addr, proto, src_port, dst_port, in_if, out_if, type}`、`fields = {bytes, packets}`、`timestamp = time_received_ns / 1e9`。実装は `from_json` のスキーマをトピックで分ける（`kafka_topic == "flows"` で `when`）。`ts isNotNull` の filter はそのまま
- 格納先: `logs` と同じ経路（Iceberg の `raw_telemetry`、OpenSearch の `<prefix>-flows-*`、Splunk の sourcetype `nwc:flow`）。`INDEX_PREFIX` / `SOURCETYPE` の表（L84-87）に `flows` を足す
- `IaC/terraform/aws-managed/pipeline/analytics/variables.tf` の `log_topics` の既定に `flows` を足し、`metric_topics` の既定から `mdt` を外す（`locals.tf` L2 / `tables.tf` L58 のコメントも）
- 手元の compose（`docker/compose/compose.yaml`）: spark の `--log-topics` に `flows`。Grafana / Splunk のダッシュボードは触らない（flows の画面は BACKLOG に積む）

### 5. Telegraf: trap だけにし、MDT を外す

- `app/telegraf/telegraf.conf.in`: dialout の `inputs.syslog`（L264-）と `inputs.cisco_telemetry_mdt`（L243-）を消し、`outputs.kafka` の `logs` / `mdt` の分（L317 / L347 付近）と namepass を消す。冒頭のコメント（L7 / L17 / L22 / L24 / L30）も直す。**gNMI と SNMP のポーリング（dialin）はこのサイクルでは触らない**（013）
- `app/telegraf/telegraf.sh`: `MDT_PORT` と `LOG_PORT` の render と起動の 1 行（L37 / L84 / L99）から消す。`SYSLOG_STANDARD` は syslog-ng の方へ移る（`ops/up.sh` の `-var syslog_standard` は collectors.tf の syslog-ng のタスクに渡す）
- `telegraf.tf`: `telegraf_ports` から `syslog` と `mdt` を外す（上の `collector_listeners` に統合）。dialout のポート 5140 / 57000 と env `SYSLOG_STANDARD` を消す
- `base/core/variables.tf` L146-158 の `mdt_source_cidrs`、`security_groups.tf` のその行、`deploy.env.example` L128-130、`ops/up.sh` L702（`MAIN_VARS+=(-var mdt_source_cidrs…)`）と L296-299 の案内を消す。`kafka_descriptions.telegraf_task` の文言から MDT を外す（IAM ロールの description なので作り直しになる → 検証 3 で確かめる）
- `docker/compose/`: `MDT_PORT` の env と compose の値（009 で足したもの）を消す。`check.sh` の「Telegraf: health が 200」はそのまま
- `app/containerlab/lab.sh forward`: ECS 向けの DNAT を `162 → trap`、`5140 → syslog-ng`、`2055 / 6343 → goflow2` にする。いまは 1 つの `telegraf-address`（NLB）に DNAT しているので、NLB が 1 つのままなら **宛先は変わらず、ポートが 2 つ増えるだけ**。`local_telegraf` の REDIRECT は 162 → `TRAP_PORT` のまま。手元の compose は syslog-ng が 5140 を直接待つので REDIRECT は要らない
- 戻し方の目印: MDT を戻すときは `git show <このサイクルの最初の commit>^:app/telegraf/telegraf.conf.in` の `inputs.cisco_telemetry_mdt` と `base/core/variables.tf` の `mdt_source_cidrs` を取る、と `docs/collection.md` に 1 行書く

### 6. 手元の compose と OSS 版

- `docker/compose/compose.yaml`: `syslog-ng`（`ghcr.io/axoflow/axosyslog`、host ネットワーク、udp 5140、`KAFKA_AUTH=none`、`KAFKA_BROKERS=localhost:9094,…`）と `goflow2`（udp 2055 / 6343、`-transport.kafka.brokers=localhost:9094,…`）を足す。`restart: on-failure:5`。`.env.example` に `SYSLOG_NG_IMAGE` / `GOFLOW2_IMAGE`
- `docker/compose/check.sh`: 「syslog-ng: udp 5140 を待っている（`ss -lun`）」「GoFlow2: `/metrics`（8080/tcp。Telegraf の health とぶつかるので GoFlow2 は `-addr :8081`）が 200」「Kafka の `logs` が 0 件なら注意」を足す
- `docker/compose/lab.sh`: SR Linux の `remote-server` は 203.0.113.1:5140 のまま（送り先は変わらない）
- OSS 版（`IaC/terraform/oss/pipeline/stream`）: `collectors.tf` はリンクで共有し、`kafka.tf` の `kafka_client_environment`（`KAFKA_AUTH=none`）と `kafka_bootstrap_brokers`（9092）を使う。SCRAM の secret の `data` は OSS 版で評価されない形にする（`count = var.kafka_auth == "scram" ? 1 : 0` のように、`msk.tf` / `kafka.tf` の locals で `kafka_collector_auth = "scram"` / `"none"` を持たせる）

## 変更対象ファイル

- 新規: `docs/cycles/012-msk-scram-syslog-ng-goflow2/`、`app/syslog-ng/{syslog-ng.conf.in,syslog-ng.sh}`、`docker/images/syslog-ng/Dockerfile`、`IaC/terraform/aws-managed/pipeline/stream/collectors.tf`（OSS 版はリンク）、`tools/netflow_send.py`（NetFlow v5 の偽のパケットを 1 つ送る。検証用）、`tests/test_collectors.py`
- Terraform: `pipeline/stream/{msk.tf,telegraf.tf,variables.tf,outputs.tf,terraform.tfvars.example}`、`IaC/terraform/oss/pipeline/stream/{kafka.tf,oss.auto.tfvars}`、`base/core/{security_groups.tf,oss.tf,variables.tf,outputs.tf}`、`base/ecr/main.tf`、`pipeline/analytics/{variables.tf,locals.tf,tables.tf}`
- ops: `ops/up.sh`（タグ 2 つ、ECR に写す、secret と KMS、`-var` の増減、費用の定数に Fargate 2 タスク分）、`ops/up-common.sh`（`ensure_msk_scram_secret`、`ensure_kms_key`）、`ops/down.sh` / `ops/down-common.sh`（secret と KMS の削除）、`oss/ops/up.sh`（ECR に写す）、`ops/check.sh`（`bash -n` は `git ls-files` で自動）、`deploy.env.example`
- アプリ: `app/telegraf/{telegraf.conf.in,telegraf.sh}`、`app/spark/snmp_sinks.py`、`app/containerlab/lab.sh`
- 手元: `docker/compose/{compose.yaml,.env.example,check.sh,README.md}`
- docs: `CLAUDE.md`（Secrets Manager の例外 1 件）、`docs/collection.md`（4 種の表、syslog の実物の 1 行、MDT の戻し方）、`docs/pipeline.md`、`docs/architecture/README.md`、`docs/architecture/pipeline.md`、`docs/data-stores.md`、`docs/deploy.md`（KMS の 7 日）、`docs/troubleshooting.md`、`README.md`
- テスト: `tests/test_stream.py`（mdt の行 L113-117 / L187-190 / L225 / L229-230 を消し、collectors の表を見る検査を足す）、`tests/test_lab_debug.py`（L232 / L295-303 / L331-343 の mdt）、`tests/test_local_compose.py`（L291 と新サービス）、`tests/test_analytics.py`（L122-128 / L199 / L413 / L471 / L566 / L1723-1726 の mdt と flows）

## 再利用するもの

- `ops/up-common.sh` `ensure_fixed_secret`（L129-152）の作り（存在確認 → umask 077 の一時ファイル → `--cli-input-json`）を Secrets Manager 向けに写す
- `telegraf.tf` のタスク定義・サービス・NLB の dynamic `load_balancer`・実行ロールの形
- `app/telegraf/telegraf.sh` の render（sed で `__X__` を埋める、値の検査、`KAFKA_AUTH` の分岐）
- `docker/compose/check.sh` の `env_get` と判定の書き方（009）
- `tests/test_stream.py` の「conf.in のトピックの表と Terraform のポートの表を突き合わせる」検査

## 実装ステップ

1. **Spark と Telegraf**（AWS に触らない）: `snmp_sinks.py` の `flows` の読み替えと `LOG_TOPICS`、`telegraf.conf.in` / `telegraf.sh` から syslog と MDT を外す、analytics の変数、compose の `MDT_PORT` を消す。テストの mdt の行を直し、`flows` の読み替えの単体検査（GoFlow2 の JSON 1 行 → 共通の形）を足す
2. **syslog-ng と GoFlow2 を手元の compose で立てる**: イメージの確認（未確定事項 2 / 4）、`app/syslog-ng/`、`docker/images/syslog-ng/Dockerfile`、compose、`check.sh`、`tools/netflow_send.py`。検証 1 / 2 を通す
3. **Terraform**: `msk.tf`（scram + association + data source）、`collectors.tf`、SG の表（マネージドと OSS）、ECR、analytics、`telegraf.tf` の縮小。`terraform validate` と test_stream / test_analytics / test_oss
4. **ops**: `up.sh` / `up-common.sh` / `down.sh` の secret と KMS、タグ、ECR へ写す、`-var`、費用の定数、`lab.sh forward` のポート。docs
5. **AWS で確かめる**（PM が条件を満たして実行。OWNER=efukuda、終わったら `ops/down.sh`）: 検証 3 / 4

commit は 4 つ（ステップごと）。各 commit で `bash ops/check.sh` が `すべて通過`。

## 検証方法

1. **手元の compose（Mac で可）**: `docker compose up -d kafka-1 kafka-2 kafka-3 syslog-ng goflow2` → `logger -n 127.0.0.1 -P 5140 --rfc5424 -t test "hello"`（または `python3 -c` で RFC 5424 の 1 行を udp 5140 へ）と `uv run python tools/netflow_send.py 127.0.0.1 2055` → Kafbat UI（または `kafka-console-consumer`）で `logs` に `{"fields":{"message":"hello",…},"name":"device_log",…}` が 1 件、`flows` に `{"type":"NETFLOW_V5",…,"src_addr":"10.0.0.1",…}` が 1 件。期待出力を `build.md` に貼る
2. **syslog の形の一致**: Telegraf 1.40.1（009 の手順で `--test`）が出す `device_log` の 1 行（`tags` のキー 5 つ、`fields` のキー 7 つ）と、syslog-ng の出力の 1 行を並べて、**キーの集合が同じ**であること（値の型: `severity_code` / `facility_code` / `version` は数値、`timestamp` は数値）。`tests/test_collectors.py` が conf.in の `format-json` のキーと Telegraf のキーの表を突き合わせる
3. **AWS（マネージド）**: `ops/up.sh` のあと、`aws kafka describe-cluster` の `ClientAuthentication.Sasl` が `{"Scram":{"Enabled":true},"Iam":{"Enabled":true}}`、`list-scram-secrets` に `AmazonMSK_<prefix>-collectors` が 1 つ、`ecs describe-services` で `syslog-ng` と `goflow2` が `runningCount 1`、`bootstrap_brokers_sasl_scram` の output が `:9096` で 3 つ。既存のクラスタに scram を足したときの apply が **in-place**（plan に `~ update in-place` で `aws_msk_cluster.stream`。`-/+` が出たら止めて報告）。lab の EC2 から `sudo lab fail-main` → Splunk / Grafana の logs に `device_log`（syslog-ng 経由）が出る。NetFlow は lab から送れないので、lab の EC2 のホストで `tools/netflow_send.py <NLB>:2055` を 1 回打って Splunk に `nwc:flow` が 1 件。**終わったら `ops/down.sh`。** secret が消え、KMS キーが `PendingDeletion` になったことを確かめる（`describe-key` の `KeyState`）
4. **AWS（OSS）**: 011 や他のサイクルの検証と一緒に 1 回。`syslog-ng` / `goflow2` が ECS の Kafka（9092）に書く。別立てで時間を取らない
5. `bash ops/check.sh` が `すべて通過`。`docs/development.md` の本数を直す

## 未確定事項とリスク

1. **既存の MSK に SCRAM を in-place で足せるか**（AWS の `UpdateSecurity` は可能とされるが、Terraform の provider が置き換えにしないか）。検証 3 で plan を見る。置き換えになるなら、このサイクルの AWS 検証は up.sh から作り直す回で行う（いまは何も立っていない）
2. **AxoSyslog のコンテナのイメージに kafka のモジュールが入っているか**（未確認）。実装のステップ 2 の最初に確かめる。無ければ `balabit/syslog-ng`（公式）か、`ghcr.io/axoflow/axosyslog` の別のタグ
3. **`kafka-c()` の `config()` のキーの名前**（AxoSyslog のリファレンスの URL が 404 で、確認できていない）。librdkafka の名前（`security.protocol` / `sasl.mechanism` / `sasl.username` / `sasl.password`）で書き、手元で MSK の代わりに **SCRAM を有効にした compose の Kafka**（`KAFKA_SASL_ENABLED_MECHANISMS=SCRAM-SHA-512` + `kafka-configs --alter --add-config 'SCRAM-SHA-512=[password=…]'`）で 1 回通す（検証 1 の追加。やれなければ検証 3 で初めて確かめる、と `build.md` に書く）
4. **GoFlow2 の arm64 のイメージ**（未確認）。無ければ `collectors.tf` の goflow2 だけ `cpuArchitecture = "X86_64"`
5. **SR Linux の sFlow**（コンテナ版で出るか未確認）。出るなら 013 で `sflow` の設定を lab に足し、GoFlow2 の 6343 で受ける。このサイクルでは触らない
6. KMS キーは `down.sh` のあと 7 日残る（$1/月の日割り、約 ¥35）。報告に書く。free の消し残り（VPC / SG / SSM）は報告に載せない（既定どおり）
7. `syslog` の 5140 を NLB の別の target group へ付け替えるので、**apply 中に syslog が数十秒落ちる**（dialout のタスクは残るが受け口が無い）。lab の検証でしか使っていないので許容
