# MSK に SASL/SCRAM を足し、syslog-ng と GoFlow2 を立てる（MDT を外す）（012）

設計: エンジニア3(opus-5.5) / effort: xhigh（Round 2。Round 1 は PM(fable-5.1) / effort: high。経緯は design-log.md）

BACKLOG 28「コレクターを gNMI / SNMP trap / syslog-ng / GoFlow2 の 4 種にする」の前半。後半（gNMI を gnmic に移し、SNMP のポーリングと telegraf-dialin を外す）は 013 で、このサイクルと 011 のあとにやる。**このサイクルは 011（lab の組み直し）と独立**で、lab の機器名や TRex に触らない。

## 背景

- **Round 2 の差し戻し（Must fix C1、PM が 2026-10-09 に確定）**: 「MSK は IAM と SCRAM を両方有効にしていて、Kafka の ACL を付ける処理がどこにも無い。IAM のアクセス制御を使うクラスタでは `allow.everyone.if.no.acl.found` が効かない（AWS の文書 iam-access-control.html）ので、SCRAM のユーザー `collectors` は `logs` / `flows` に書けない」
  - 根本原因: Round 1 の設計が「SCRAM のユーザーは secret を関連付ければ書ける」と置いていた。ACL を誰がいつ付けるかが設計に無かった（実装もそのとおり）。同じ前提で「トピックは MSK が自動で作る」とも置いていたが、ACL の無い主体は自動作成も起こせない（手元で実測）
  - 対応は方針 7（Spark が IAM で ACL を入れる）。破綻のしかた（syslog-ng は持ちこたえ、GoFlow2 はその間のフローを捨てる）は手元で測った（design-log.md の Round 1）
- ユーザーの決定（2026-10-08、確認済み）: BACKLOG 28 の括弧の中がコレクター。gNMI → Telemetry 専用のコレクター、SNMP trap → Telegraf、syslog → syslog-ng、NetFlow → GoFlow2。telegraf-dialin は要らなくなる
- 2026-10-08 の調査（実装の制約。確認日 2026-10-08）
  - **GoFlow2 と syslog-ng（librdkafka）は MSK の IAM 認証（`AWS_MSK_IAM`）を喋れない。** GoFlow2 の Kafka の transport は `-transport.kafka.sasl` に `none / plain / scram-sha256 / scram-sha512` だけ（ユーザー名とパスワードは環境変数 `KAFKA_SASL_USER` / `KAFKA_SASL_PASS`）。librdkafka も OAUTHBEARER の自前の実装が要る。**MSK に SASL/SCRAM を IAM と併用で有効にし、9096 で受ける**
  - MSK の SCRAM（AWS の `msk-password-tutorial`）: 資格情報は **Secrets Manager** の secret（名前は `AmazonMSK_` で始まる、値は `{"username":"…","password":"…"}`）で、**顧客管理の KMS キーが必須**。`BatchAssociateScramSecret`（Terraform は `aws_msk_scram_secret_association`）で付ける。SCRAM を有効にするとクライアント通信は TLS
  - MSK の ACL（AWS の文書、2026-10-09 確認）: `iam-access-control.html` は「IAM のアクセス制御のクラスターでは `allow.everyone.if.no.acl.found` が効かない」「Kafka の ACL は IAM の主体には効かない」。`msk-acls.html` は ACL の追加を Kafka の AdminClient で行い、「MSK は `allow.everyone.if.no.acl.found` を既定で true にする（ACL の無い資源には誰でも触れる）」とする。IAM と SCRAM の併用でどちらが SCRAM の主体に効くかはどちらの文書にも無い（未確定事項 8）。ACL の作成（`CreateAcls`）に要る IAM の権限は `kafka-cluster:AlterCluster`（`Connect` と `DescribeCluster` も。`kafka-actions.html` は Kafka の ALTER CLUSTER と同じとする）
  - **SR Linux は NetFlow / IPFIX を出せない**（原理的）。フローの送り元は lab の Linux 側になる。**このサイクルでは GoFlow2 の受け口と Kafka までの経路を作り、lab からのフローの送り元は 013 以降に回す**（検証は手元の compose と `tools/netflow_send.py`）
- このリポジトリの決まり「シークレットは `ops/up.sh` が SSM Parameter Store の SecureString として作る」は、MSK の SCRAM が Secrets Manager + 顧客管理 KMS を強制するので、**この 1 件だけ Secrets Manager にする例外**を `CLAUDE.md` と `docs/` に書く。値は読まない・表示しない
- MDT（`inputs.cisco_telemetry_mdt`、`mdt` トピック、`mdt_source_cidrs`）は送り手が無いので外す。戻し方は `docs/collection.md` に 1 行

## 設計方針

### 1. MSK: SASL/SCRAM を IAM と併用

| 何 | どこ | 内容 |
|---|---|---|
| クラスタ | `IaC/terraform/aws-managed/pipeline/stream/msk.tf` | `client_authentication { sasl { iam = true  scram = true } }`。既存のクラスタに scram を足すのは in-place の更新の見込み（未確定事項 1）。`client_broker = "TLS"` はそのまま |
| secret の関連付け | 同上 | `aws_msk_scram_secret_association.collectors`。secret は data source で ARN だけ引く（値は state に入れない） |
| secret と鍵 | `ops/up-common.sh` の `ensure_msk_scram_key` / `ensure_msk_scram_secret`、`ops/up.sh` | 鍵: `alias/<prefix>-msk-scram`（無ければ作る。削除の予約中・無効なら取り消して有効に戻す）。secret: `AmazonMSK_<prefix>-collectors`（あれば飛ばす・値を出さない・umask 077 の一時ファイル・`--cli-input-json`。一時ファイルは `ops/up.sh` の `on_exit` が消す）。ユーザー名 `collectors`。secret が別の鍵で暗号化されていれば止める。`ops/down.sh` は secret を `--force-delete-without-recovery`、鍵を `schedule-key-deletion --pending-window-in-days 7` |
| 収集器の口 | `msk.tf` の locals（OSS 版は `kafka.tf`） | `kafka_collector_brokers`（9096）/ `kafka_collector_auth`（`scram`。OSS 版は `none`）/ `kafka_collector_secrets` / `kafka_collector_execution_statements` の 4 つ。`collectors.tf` はこれだけを見る |
| SG | `base/core/security_groups.tf`、`oss.tf` | `syslog_ng → msk tcp 9096`、`goflow2 → msk tcp 9096`（OSS 版は `kafka 9092`）。既存の SG の description は変えない |
| エンドポイント | 閉域のエンドポイント | `secretsmanager` を足し、stream の `ENDPOINTS` に入れる（ECS が secret を引く） |
| OSS 版 | `IaC/terraform/oss/pipeline/stream/kafka.tf` | ECS の Kafka は認証なし（PLAINTEXT 9092）のまま。secret・KMS・association・ACL は無い |

資格情報の渡し方: ECS のタスク定義の `secrets` で `"<secret ARN>:username::"` と `":password::"`。実行ロールに `secretsmanager:GetSecretValue`（その secret）と `kms:Decrypt`（その鍵）。**Terraform も実装者もレビュアーも値に触らない**（`get-secret-value` を打たない）。

### 2. syslog-ng（AxoSyslog）を ECS のサービスにする

- イメージ: `ghcr.io/axoflow/axosyslog:4.29.0`（arm64 あり、librdkafka 入り。実測）。`docker/images/syslog-ng/Dockerfile` で `app/syslog-ng/{syslog-ng.conf.in,syslog-ng.sh}` を COPY し、ECR `<prefix>-syslog-ng` に置く（タグは `dir_tag`）。nobody で動かすので制御ソケットは `/tmp`、HEALTHCHECK もそこへ向ける
- 受け口: udp と tcp の 5140（tcp は NLB の UDP の target group のヘルスチェックが開く口。tcp で来た syslog も同じく書く）。`SYSLOG_STANDARD` が RFC5424 なら `flags(syslog-protocol)`
- 書き先: `logs`。メッセージは Telegraf の `device_log` の JSON の形（`name` / `tags.{sysName,appname,facility,severity,source}` / `fields.{message,severity_code,facility_code,procid,msgid,version,timestamp}` / `timestamp` は受けた時刻の秒）。`$(format-json --scope none --omit-empty-values …)` で作る。Telegraf 1.40.1 の実物の 1 行を `docs/collection.md` に貼り、`tests/test_collectors.py` がキーと型を突き合わせる
- Kafka: `kafka-c(bootstrap-servers(…) topic("logs") config(…))`。`config()` は librdkafka の名前で `security.protocol` = `SASL_SSL`、`sasl.mechanism` = `SCRAM-SHA-512`、`sasl.username` / `sasl.password` は環境変数 `KAFKA_SASL_USER` / `KAFKA_SASL_PASS` をバッククォートで囲んで書く（syslog-ng が設定を読むときに入れ、`/tmp` の設定に値が残らない）。`KAFKA_AUTH=none` では `syslog-ng.sh render` が `config()` の区間を消す
- **キューは既定のまま**（`log-fifo-size` / `retries` / `message.timeout.ms` / disk-buffer を書かない）。書けない間の振る舞い（方針 7）はこの既定で測った
- ECS: `IaC/terraform/aws-managed/pipeline/stream/collectors.tf`（新規。OSS 版はリンク）。Telegraf のクラスタに相乗り（ARM64、0.25 vCPU / 0.5 GB、`desired_count = 1`）。ヘルスチェックは TCP 5140。ログは `/ecs/<prefix>-syslog-ng`

### 3. GoFlow2 を ECS のサービスにする

- イメージ: `netsampler/goflow2:v2.2.7`（arm64 あり。実測）。ECR `<prefix>-goflow2` に写す。設定は全部フラグ
- 受け口: udp 2055（NetFlow v5/v9/IPFIX）と udp 6343（sFlow）。NLB は `collector_listeners = { trap → telegraf-dialout, syslog → syslog-ng, netflow → goflow2, sflow → goflow2 }` の表でサービスごとに target group を持つ（NLB と target group のリソース名は `telegraf_dialout` のまま）。ヘルスチェックは HTTP 8081 `/__health`
- 書き先: `flows`。コマンドは `-listen 'netflow://:2055,sflow://:6343' -transport=kafka -transport.kafka.brokers=<口> -transport.kafka.topic=flows -format=json -addr=:8081`、SCRAM なら `-transport.kafka.tls -transport.kafka.sasl=scram-sha512`（`kafka_collector_auth` で locals が組む）
- 手元の compose では `restart: on-failure:10`（Kafka が無いと 1 秒で終わる。10 回で約 110 秒待てる。実測）

### 4. Spark: `flows` を読む

- `app/spark/snmp_sinks.py`: `LOG_TOPICS = "traps,logs,flows"`。`flows` だけ GoFlow2 の JSON を共通の形に読み替える（`name = "flow"`、`tags = {sampler, src, dst, proto, src_port, dst_port, in_if, out_if, type}`、`fields = {bytes, packets}`、`timestamp = time_received_ns / 1e9`）
- 格納先は `logs` と同じ経路（Iceberg の `raw_telemetry`、OpenSearch の `<prefix>-snmp-logs-*`、Splunk の `sourcetype=netops:flows` / `source=telegraf:flow`）
- `pipeline/analytics/variables.tf` の `log_topics` の既定に `flows`、`metric_topics` から `mdt` を外す。手元の compose の spark も `--log-topics` に `flows`。flows の画面は作らない

### 5. Telegraf: trap だけにし、MDT を外す

- `app/telegraf/telegraf.conf.in` / `telegraf.sh`: `inputs.syslog` と `inputs.cisco_telemetry_mdt`、`outputs.kafka` の `logs` / `mdt`、`MDT_PORT` / `LOG_PORT` を外す。gNMI と SNMP のポーリング（dialin）は触らない（013）
- `telegraf.tf`: `telegraf_ports` から `syslog` と `mdt` を外し、`collector_listeners` に統合
- `mdt_source_cidrs`（variables / SG / `deploy.env.example` / `ops/up.sh`）を外す。`MDT_SOURCE_CIDRS` が書いてあれば両方の `up.sh` が「使わない」と 1 行出す
- `app/containerlab/lab.sh forward`: NLB への DNAT に 2055 / 6343 を足す（宛先の NLB は同じ）
- デバッグ用の EC2 の Telegraf のログから `device_log` が消える（`lab.sh` の failover の文言は 013 で直す）

### 6. 手元の compose と OSS 版

- `docker/compose/compose.yaml`: `syslog-ng`（udp/tcp 5140、`KAFKA_AUTH=none`）と `goflow2`（udp 2055 / 6343、`-addr :8081`）。版は compose と `ops/up-common.sh` の定数で固定し、テストが照合する
- `docker/compose/check.sh`: syslog-ng が 5140 を待つ、GoFlow2 の `/__health` が 200、`logs` が 0 件なら注意
- OSS 版: `collectors.tf` はリンクで共有し、`kafka.tf` の 4 つの口（`none`、9092）を使う

### 7. SCRAM のユーザーに Kafka の ACL を付ける（Round 2）

**何を**: `User:collectors` に、トピック `logs` と `flows`（`LITERAL`）の `WRITE` と `DESCRIBE` を `ALLOW`。4 つの ACL（host は `*`）。

- 入れないもの: `CREATE`（トピックは Spark が作る。収集器にトピック名の綴り違いでトピックを作らせない）、CLUSTER の ACL（`IDEMPOTENT_WRITE` 等。どちらの収集器も冪等の producer を使わない。re:Post に SCRAM のクラスターで CLUSTER の ACL を入れるとブローカー間の複製が止まる例がある）、`PREFIXED` / `*`、ブローカーの Read の ACL（`msk-acls.html` は「ブローカーは super user」とする。未確定事項 9）

**どこで**: `app/spark/snmp_sinks.py` の `ensure_acls(spark, bootstrap)` を足し、`main` が `ensure_topics` の直後に呼ぶ（3 本のジョブ `sinks-s3iceberg` / `sinks-splunk` / `sinks-grafana` のどれもが起動のたびに入れる）。

- `ensure_topics` と同じ IAM の AdminClient（`kafka_admin_props()`、EMR の実行ロール）で `createAcls` を 1 回（4 つの `AclBinding`）。`createAcls` は同じものを何度入れても同じ（冪等）。ACL はトピックより先にあってもよい
- `KAFKA_AUTH=none`（OSS 版の ECS の Kafka、手元の compose。authorizer が無い）では何もせず `[]` を返し、AdminClient も作らない
- 失敗は上げる（ジョブが起動で落ち、原因が stderr に出る。`ensure_topics` の TopicExists 以外と同じ扱い）
- 定数: `SCRAM_USER = "collectors"`（`ops/up-common.sh` の `ensure_msk_scram_secret` の username）、`SCRAM_TOPICS = ("logs", "flows")`（`syslog-ng.conf.in` の `topic("logs")`、`collectors.tf` の `-transport.kafka.topic=flows`）。テストが 3 か所と突き合わせる
- ログ: iam のときだけ 1 行「ACL: User:collectors に WRITE logs, DESCRIBE logs, WRITE flows, DESCRIBE flows」
- `ensure_topics` の docstring と `syslog-ng.conf.in` のコメントの「MSK が自動で作る」を直す（AWS の文書どおりなら SCRAM の収集器は自動作成できないので（MSK では未確認。未確定事項 8）、`logs` / `flows` は Spark が作る）

**IAM**: `pipeline/analytics/access.tf` の `KafkaCluster`（クラスタの ARN）に `kafka-cluster:AlterCluster` を足す。`AlterCluster` は Kafka の ALTER CLUSTER と同じ幅（どの主体・資源への `CreateAcls` / `DeleteAcls`、パーティションの再配置、リーダー選出、SCRAM の資格情報の変更 等。MSK でどれが効くかは未確認）を許すので、コメントに「ensure_acls の createAcls のため」とその幅を書く。実行ロールの description は変えない。

**採らなかった場所**: Terraform の kafka provider（閉域の MSK に手元から届かない）、stream の一回きりの ECS タスク（AdminClient と IAM の jar を持つイメージが別に要る。トピックはどのみち Spark が作るので待ちは縮まない）、Lambda、手作業。理由の表は design-log.md。

**ACL が入るまでの間**（`ops/up.sh` では stream の apply から analytics の 7-5 のジョブ起動まで数十分。手元の ACL つき Kafka で、待ちが librdkafka の `message.timeout.ms` 既定 300 秒を超える約 340 秒で測った）:

| | 振る舞い | ACL が入ったあと |
|---|---|---|
| syslog-ng | 落ちない（再起動 0）。`err kafka: failed to publish message; topic='logs', error='Broker: Topic authorization failed'` を約 3 回/秒。**メッセージは捨てずに syslog-ng のキューで持つ**（`rd_kafka_produce` がその場で失敗し、AxoSyslog は `LTR_RETRY` → `time_reopen` 待ちで巻き戻す。librdkafka のキューに入らないので 300 秒の時計も回らない） | 再起動なしで、待っていた分も書く（3 行 + 1 行 → written 4 / dropped 0） |
| GoFlow2 | 落ちない（再起動 0）。`level=ERROR msg="transport error" … not authorized to access this topic`（`-err.cnt 10` / `-err.int 10s` で黙る）。**そのフローは捨てる**（sarama の非同期の producer は Errors に返したものを再送しない） | 新しいフローから書く |
| ECS / NLB | どちらのヘルスチェックも Kafka を見ないので healthy のまま | ― |

- syslog-ng のキューの上限はメモリの `log-fifo-size`（既定 10000）。超えた分と、syslog-ng が再起動したときのキューは消える（未確定事項 12）
- `SKIP_ANALYTICS=1` の回は Spark が動かないので ACL が入らず、syslog-ng / GoFlow2 は `logs` / `flows` に書けない（トピックも無い）。`docs/deploy.md` の `SKIP_ANALYTICS` の行に書く。`SKIP_STREAM=1` は `SKIP_ANALYTICS=1` を伴う（収集器も無い）
- `ensure_topics` は `log_topics` にあるトピックしか作らない。`log_topics` から `logs` / `flows` を外すとそのトピックは作られず、ACL だけが入る（未確定事項 11）

## 変更対象ファイル

Round 1（ed8edf1..c699806）で入れたもの:

- 新規: `app/syslog-ng/{syslog-ng.conf.in,syslog-ng.sh}`、`docker/images/syslog-ng/Dockerfile`、`IaC/terraform/aws-managed/pipeline/stream/collectors.tf`（OSS 版はリンク）、`tools/netflow_send.py`、`tests/test_collectors.py`
- Terraform: `pipeline/stream/{msk,telegraf,locals,variables,outputs}.tf`、`terraform.tfvars.example`、OSS 版の `pipeline/stream/kafka.tf`、`base/core/{security_groups,oss,variables,outputs}.tf`、`base/ecr/{main,outputs}.tf`、`pipeline/analytics/{variables,locals,tables}.tf`
- ops: `ops/{up.sh,up-common.sh,down.sh,down-common.sh,deploy-env.sh,lab-common.sh}`、`oss/ops/up.sh`、`deploy.env.example`
- アプリ: `app/telegraf/{telegraf.conf.in,telegraf.sh}`、`app/spark/snmp_sinks.py`、`app/containerlab/{lab.sh,gen_lab.py}`、`docker/images/telegraf/Dockerfile`
- 手元: `docker/compose/{compose.yaml,.env.example,check.sh,up.sh,README.md}`
- docs: `CLAUDE.md`、`README.md`、`docs/{collection,data-stores,deploy,development,faq-fukuda-nwc-poc,pipeline,troubleshooting}.md`、`docs/architecture/` の 12 本
- テスト: `tests/{test_analytics,test_lab_debug,test_local_compose,test_oss,test_oss_ops,test_stream}.py`

Round 2（方針 7）で変えるもの:

- `app/spark/snmp_sinks.py`（`ensure_acls`、`main`、`ensure_topics` の docstring）
- `IaC/terraform/aws-managed/pipeline/analytics/access.tf`（`AlterCluster`、`KafkaTopics` のコメント）
- `app/syslog-ng/syslog-ng.conf.in`（「MSK / Kafka が自動で作る」のコメントだけ）
- `tests/test_analytics.py`（`ensure_acls` の偽の JVM での検査、定数の突き合わせ、`AlterCluster`、`main` の順）、`tests/test_collectors.py`（キューの既定を変えていないことの検査）、`tests/test_oss.py`（`KAFKA_AUTH=none` で `ensure_acls` が何もしない。検証 4 の「ACL の行が出ない」を手元で縛る）
- `docs/deploy.md`（`SKIP_ANALYTICS`）、`docs/pipeline.md`（`ensure_topics` の段と ACL）、`docs/architecture/resources/msk.md`（知見）、`docs/troubleshooting.md`（`Topic authorization failed` の行）、`docs/development.md`（本数）

## 再利用するもの

- `ensure_topics` の AdminClient の作り（`kafka_admin_props()`、py4j の `jvm.java.util.Properties`、`try … finally admin.close()`）と、`tests/test_analytics.py` の偽の JVM（`_JVM` / `_Admin` / `_Fut`）
- `ops/up-common.sh` の `ensure_fixed_secret` の作り（Secrets Manager 向けに写した）
- `telegraf.tf` のタスク定義・サービス・NLB の dynamic `load_balancer`・実行ロールの形
- `tests/test_stream.py` の「conf.in のトピックの表と Terraform のポートの表を突き合わせる」検査

## 実装ステップ

Round 1 のステップ 1〜4 は済み（ed8edf1 / 8480a5c / 5769fd6 / 594da74 / c699806）。Round 2:

1. **ACL**: `snmp_sinks.py` に `ensure_acls` と `main` の呼び出し、`access.tf` に `AlterCluster`、`syslog-ng.conf.in` のコメント。`tests/test_analytics.py`・`tests/test_collectors.py`・`tests/test_oss.py` に検査を足し、退行を入れて落ちることを確かめる
2. **docs**: `deploy.md` / `pipeline.md` / `msk.md` / `troubleshooting.md` / `development.md`
3. **手元の実コードの検証**（検証 6）
4. **AWS で確かめる**（PM が条件を満たして実行。OWNER=efukuda、終わったら `ops/down.sh`）: 検証 3 / 4

commit は Round 2 で 1 つ以上。各 commit で `bash ops/check.sh` が `すべて通過`。

## 検証方法

1. **手元の compose（Mac）**: kafka-1/2/3・syslog-ng・goflow2 を上げ、RFC5424 の 1 行を udp 5140 へ、`tools/netflow_send.py 127.0.0.1 2055` → `logs` に `{"fields":{"message":"hello",…},"name":"device_log",…}` が 1 件、`flows` に `{"type":"NETFLOW_V5",…,"src_addr":"10.0.0.1",…}` が 1 件（Round 1 で取得済み）
2. **syslog の形の一致**: Telegraf 1.40.1 の `device_log` と syslog-ng の 1 行のキーの集合と型が同じ。`tests/test_collectors.py` が縛る（Round 1 で取得済み）
3. **AWS（マネージド）**（PM が実行）:
   - `describe-cluster` の `ClientAuthentication.Sasl` が Scram / Iam とも Enabled、`list-scram-secrets` に `AmazonMSK_<prefix>-collectors` が 1 つ、`syslog-ng` と `goflow2` が `runningCount 1`、`bootstrap_brokers_sasl_scram` が `:9096` でブローカーの台数ぶん（既定は 2 台なので 2 つ。設計時は 3 つと書いていたが、2026-10-09 の AWS 検証で 2 つと確かめた）。既存のクラスタに scram を足す apply が in-place（`-/+` なら止めて報告）
   - **ACL（Round 2）**: Spark のジョブの driver の stderr に「ACL: User:collectors に WRITE logs, DESCRIBE logs, WRITE flows, DESCRIBE flows」が 3 本とも出る。ジョブが RUNNING になったあと、`/ecs/<prefix>-syslog-ng` の `Topic authorization failed` と `/ecs/<prefix>-goflow2` の `not authorized to access this topic` が**止まる**（それまでは出ていてよい）。Kafbat UI の ACL の画面（または IAM の AdminClient の `describeAcls`）で `User:collectors` の 4 つが見える
   - **ACL の前の認可の失敗（未確定事項 8）**: stream ができてから analytics のジョブが起きる前に、lab の EC2 のホストから NLB の 5140 へ RFC5424 の 1 行（`logger -n <NLB> -P 5140 -d --rfc5424 acl-probe` 等）と `tools/netflow_send.py <NLB>:2055` を 1 回ずつ送る。期待: `/ecs/<prefix>-syslog-ng` に `Topic authorization failed`、`/ecs/<prefix>-goflow2` に `not authorized to access this topic` が出る（AWS の文書どおり）。**出ずに `logs` / `flows` へ書けていたら、`allow.everyone.if.no.acl.found` が SCRAM の主体に効いている**ので、そこで止めて報告する（未確定事項 8 の次の手）。送れずにジョブが起きたら「判定できず」と報告する
   - lab の EC2 から `sudo lab fail-main` → Splunk / Grafana の logs に `device_log` が出る。lab の EC2 のホストで `tools/netflow_send.py <NLB>:2055` を 1 回打って Splunk に `sourcetype=netops:flows` が 1 件
   - **複製（未確定事項 9）**: MSK の CloudWatch の `UnderReplicatedPartitions` が ACL のあとも 0
   - 終わったら `ops/down.sh`。secret が消え、KMS の鍵が `PendingDeletion` になったことを確かめる
4. **AWS（OSS）**: 他のサイクルの検証と一緒に 1 回。`syslog-ng` / `goflow2` が ECS の Kafka（9092）に書く。Spark の stderr に ACL の行が出ない（`KAFKA_AUTH=none`）
5. `bash ops/check.sh` の最後の行が `すべて通過`。`docs/development.md` の本数が実際と同じ
6. **手元の実コードの ACL（Round 2）**: ACL を効かせた使い捨ての Kafka（apache/kafka、StandardAuthorizer、`allow.everyone.if.no.acl.found=false`、SASL/SCRAM-SHA-512 の listener、使い捨ての資格情報）に、Round 1 のイメージの syslog-ng と GoFlow2 を SCRAM で繋ぐ。ACL の無いうちに syslog を送ったあと、`nwc-local-spark` のイメージでこのブランチの `snmp_sinks.py` の **実物の `ensure_topics` と `ensure_acls`** を呼ぶ（`kafka_admin_props` だけ PLAINTEXT に差し替え、`KAFKA_AUTH` は iam のまま。`ensure_acls` は 2 回呼んで冪等を見る）。期待: `ensure_acls` が 4 つを返し 2 回目も例外なし、`kafka-acls --list` に `User:collectors` の `logs` / `flows` の WRITE と DESCRIBE、syslog-ng の stats が dropped 0 で ACL の前の分も written、`logs` に ACL の前と後の行、`flows` に ACL のあとの NetFlow が 1 件、どちらの再起動も 0。終わったらコンテナとネットワークが 0

## 未確定事項とリスク

1. **既存の MSK に SCRAM を in-place で足せるか**。検証 3 で plan を見る。いまは何も立っていないので、初回は up.sh から作る
2. （解消）AxoSyslog 4.29.0 に librdkafka が入っている
3. `kafka-c()` の `config()` のキーは手元の SASL_PLAINTEXT で通った。**TLS（SASL_SSL）は MSK でしか打てない**ので検証 3 で初めて確かめる。GoFlow2 の `-transport.kafka.tls` も同じ
4. （解消）GoFlow2 v2.2.7 に arm64 がある
5. **SR Linux の sFlow**（コンテナ版で出るか未確認）。013 で
6. KMS の鍵は `down.sh` のあと 7 日 `PendingDeletion` で残る。削除の予約中は課金されない（取り消すと待った日数ぶん課金）。報告に書く
7. 動いている stack に up.sh を打ち直すと、syslog が数十分落ちる（core の SG の apply が stream の付け替えより先）。初回は起きない。lab の検証でしか使っていないので許容
8. **MSK で C1 が本当に起きるか**: AWS の文書は「IAM のクラスターでは `allow.everyone.if.no.acl.found` が効かない」とする一方、「MSK はこれを既定で true にする」ともし、AWS のブログの SCRAM と IAM の併用の例には ACL の段が無い。文書に従って ACL を入れる（入れて害は無い）。docs とコードのコメントは「文書から読んだ想定。MSK では未確認」と書く。検証 3 の「ACL の前の認可の失敗」で確かめる。**失敗が出なかった場合（既定の true が SCRAM の主体に効いている）**: 収集器は ACL が無くても書け（`logs` / `flows` も自分で作れる）、収集器の SCRAM の資格情報（syslog-ng / GoFlow2 のタスクに入る）で ACL の無いトピック（`metrics` / `gnmi` / `traps` 等）とクラスターに何でもできる（読む・消す・作る・設定を変える）。`ensure_acls` のあとは `logs` / `flows` だけ他の SCRAM の主体から閉じる。次の手は MSK の `server_properties` に `allow.everyone.if.no.acl.found=false` を足すこと（文書どおりなら何も変わらない）だが、設計方針に関わるので PM が決める（このサイクルの範囲には入れていない）
9. **ブローカー間の複製**: `msk-acls.html` は「ブローカーは super user」としつつ、手順にブローカーの Read の ACL を足す段がある。トピックの ACL だけを入れ、CLUSTER の ACL は入れない。検証 3 で `UnderReplicatedPartitions` が 0 のままかを見る。崩れたら ACL を消して（`deleteAcls`）報告
10. **`AlterCluster` の幅**: EMR の実行ロールが Kafka の ALTER CLUSTER と同じこと（どの主体・資源への ACL の作成と削除、パーティションの再配置、リーダー選出、SCRAM の資格情報の変更 等。MSK でどれが効くかは未確認）もできるようになる。ACL を入れるのに要る最小の権限がこれしかない。実行ロールは Spark のジョブだけが使う
11. **ACL の失敗でジョブが落ちる**: `createAcls` が決まって失敗する（権限が無い等）と、3 本のジョブが起動で落ち、起こし直しの上限（1 時間 5 回）を使い切る。黙って通すと収集器が書けないまま気付けないので、落とす方を採る。原因は stderr と `docs/troubleshooting.md`。`log_topics` から `logs` / `flows` を外すと、そのトピックは作られず ACL だけが入る（収集器は書けない。既定では両方ある）
12. **ACL が入るまでの間**（数十分）: GoFlow2 はその間のフローを捨てる（流量の統計なので許容）。syslog-ng は持つが、上限はメモリのキュー（既定 10000）で、syslog-ng が再起動するとキューは消える。librdkafka が受け取ったあとに配送が失敗した分（起動の直後の数秒にだけ当たりうる）も捨てる。lab の syslog の量なら 10000 には届かない見込み（未測定）
13. 手元の検証の Kafka（apache/kafka 4.3.1、SASL_PLAINTEXT）は MSK（4.1.x、SASL_SSL、IAM と併用）と同じではない。手元で確かめたのは ACL の付け方と収集器の振る舞いまで

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261009-cycle-012-msk-scram-syslog-ng-goflow2-design.html -->
