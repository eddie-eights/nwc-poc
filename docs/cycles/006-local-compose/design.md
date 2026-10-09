# Cycle 006 local-compose 設計: 手元の docker compose で動く構成を作る

PM(fable-5.1) / effort: high

この文書は現行の設計だけを書く。変えた経緯は [design-log.md](design-log.md)。

## 背景

いまの構成はマネージド版（`terraform/`）と OSS 版（`oss/terraform/`）の 2 つで、どちらも AWS に立てる。パイプライン（lab → Telegraf → Kafka → Spark → 格納先 → Grafana / Splunk）を手元で動かす手段は無く、`oss/compose/` は OSS 版の部品（Kafka、OpenSearch、VictoriaMetrics、Neo4j、Spark の jar）を 1 つずつ確かめる使い捨てで、lab から Grafana まではつながらない。

このサイクルで作るのは、**WSL2 の中だけでパイプラインが一周する構成**。containerlab の SR Linux に障害を入れると、Grafana のダッシュボードと Splunk の検索にそれが出る、まで。AWS は使わない（ユーザー決定 2026-10-08: compose はローカルの WSL で動かす用。OSS 版は AWS で比べる用）。

### 合意した決定（2026-10-08。質問と却下した案は design-log.md）

| 項目 | 決定 |
|---|---|
| 置き場所 | `local/compose/`（compose.yaml、`.env.example`、`up.sh` / `down.sh` / `check.sh` / `lab.sh`） |
| 動かす先 | WSL2（Ubuntu）の中の Docker Engine（`docs/setup.md` の手順で入れた docker-ce）。Mac と Docker Desktop は未確認 |
| containerlab | compose の外。WSL で `lab/lab.sh` をそのまま使う（`sudo`）。SR Linux 6 台 + VM 2 台の既定トポロジ |
| Telegraf | compose の中、`network_mode: host`。イメージは `telegraf/Dockerfile` を build。1 台 |
| Kafka | KRaft 3 台（`apache/kafka:4.3.1`。`oss/compose` と同じ設定）+ Kafbat UI 1 台（`ghcr.io/kafbat/kafka-ui:v1.5.0`） |
| Spark | 1 イメージ（`spark/Dockerfile`）から 2 サービス。`spark-splunk`（`--sinks splunk`）と `spark-http`（`--sinks opensearch,prometheus`）。Iceberg は入れない |
| OpenSearch | 1 台（`opensearchproject/opensearch:3.9.0`、single-node、security は Basic 認証で TLS なし） |
| Prometheus | 1 台（`prom/prometheus:v3.15.0`、remote write の受け口を開く） |
| Splunk | 1 台（`splunk/Dockerfile`。standalone、HEC） |
| Grafana | 1 台（`grafana/Dockerfile`。`PROMETHEUS_AUTH=none` / `OPENSEARCH_AUTH=basic`） |
| 入れない | Nautobot、Neo4j、Temporal、Web（Gradio）、エージェント、VictoriaMetrics、VictoriaLogs、SNS |
| データ | named volume。`down.sh -v` で消す |
| 設定 | `.env`（`.env.example` から写す。試し用の値が入っている）。lab の値（SNMP_AGENTS など）は `up.sh` が `lab/lab_topology.py` から作って渡す |

## 設計方針

### 構成

```
WSL2（Ubuntu）
├── containerlab（sudo lab/lab.sh up。管理ネット 203.0.113.0/24、ホストは 203.0.113.1）
│   ├── SR Linux ×6（trap → 203.0.113.1:162、syslog → 203.0.113.1:5140、SNMP 161 / gNMI 57400 を受ける）
│   └── VM ×2（multitool。bond0）
└── docker compose（local/compose。ネットワーク nwc-local）
    ├── telegraf（network_mode: host。5140/udp、1162/udp、57000/tcp、8080/tcp を host で待つ）
    │     → Kafka へ localhost:9094,9095,9096（EXTERNAL リスナー）
    ├── kafka-1/2/3（内部 kafka-N:9092、host へ 127.0.0.1:9094/9095/9096）、kafka-ui（127.0.0.1:18080）
    ├── spark-splunk（Kafka → Splunk HEC https://splunk:8088）
    ├── spark-http（Kafka → OpenSearch http://opensearch:9200、Prometheus http://prometheus:9090/api/v1/write）
    ├── opensearch（9200）、prometheus（9090）、splunk（127.0.0.1:8000）
    └── grafana（127.0.0.1:3000。Prometheus と OpenSearch をデータソースに）
```

- **Telegraf を host ネットワークに置く理由。** SR Linux は trap と syslog を管理ネットの GW（203.0.113.1 = WSL のホスト）に送る。デバッグ用の EC2（`cloudformation/lab-debug.yaml`）と同じ形にすれば、`lab/lab.sh forward` の `TELEGRAF_IMAGE` 分岐（trap の 162/udp を 1162/udp へ `REDIRECT` するだけ）がそのまま使える。ポーリング（SNMP 161 / gNMI 57400）は host から管理ネットへ直接届く。compose のネットワークに置いて DNAT する案は、stream（ECS の NLB）向けの規則を手元用に書き直すことになるので採らない。
- **Kafka の EXTERNAL リスナー。** host ネットワークの Telegraf は `kafka-N:9092` を引けないので、各ブローカーに `EXTERNAL://:909N`（N = 4, 5, 6）を足し、host へ同じ番号で publish し、`KAFKA_ADVERTISED_LISTENERS` に `EXTERNAL://localhost:909N` を足す。内部（Spark、Kafbat UI）は `kafka-N:9092` のまま。`KAFKA_LISTENER_SECURITY_PROTOCOL_MAP` に `EXTERNAL:PLAINTEXT` を足す。トピックは `KAFKA_AUTO_CREATE_TOPICS_ENABLE=true`（Telegraf が最初に書く。Spark の `ensure_topics` もある）。
- **Telegraf の環境変数**（`telegraf/telegraf.sh` の契約。`KAFKA_BROKERS` は `^[A-Za-z0-9.-]+:[0-9]+(,…)*$` に合う）: `SINK=kafka`、`KAFKA_AUTH=none`、`KAFKA_BROKERS=localhost:9094,localhost:9095,localhost:9096`、`TELEGRAF_ROLE=all`、`SYSLOG_STANDARD=RFC5424`（`ops/lab-common.sh` の `LAB_SYSLOG_STANDARD`）、`SNMP_POLL=1`、`AWS_REGION=ap-northeast-1`（`: "${AWS_REGION:?}"` があるので要るが使わない）、`SNMP_AGENTS` / `GNMI_TARGETS`（`python3 lab/lab_topology.py lab --snmp-agents` / `--gnmi-targets` の出力。`up.sh` が作る）、`GNMI_USERNAME=admin`、`GNMI_PASSWORD=NokiaSrl1!`、`SNMP_COMMUNITY=public`（`ops/lab-common.sh` の lab だけの公開既定値）。
- **Spark の 2 サービス。** どちらも `spark/Dockerfile` のイメージ（jar は焼いてあるが Iceberg のカタログ設定は渡さない）。`command` は `oss/terraform/pipeline/analytics/spark.tf` の並びと同じ: `/opt/spark/bin/spark-submit --master local[*] --driver-memory 1024m /opt/nwc/snmp_sinks.py --bootstrap kafka-1:9092,kafka-2:9092,kafka-3:9092 --checkpoint file:///opt/spark/work-dir/checkpoint --sinks <sinks> --region ap-northeast-1 --metric-topics metrics,gnmi,mdt --log-topics traps,logs --device-map "${DEVICE_MAP}"` に、splunk なら `--splunk-hec-url https://splunk:8088 --splunk-skip-verify`、http なら `--opensearch-endpoint http://opensearch:9200 --opensearch-index snmp-logs --prometheus-url http://prometheus:9090/api/v1/write`。環境変数は `SPLUNK_HEC_TOKEN`（あれば SSM を読まない。`snmp_sinks.py:497-499`）、`OPENSEARCH_AUTH=basic` + `OPENSEARCH_PASSWORD`、`PROMETHEUS_AUTH=none`、`AWS_EC2_METADATA_DISABLED=true`。checkpoint は named volume を `/opt/spark/work-dir`（イメージに在り、`spark` ユーザーが書ける）に付ける（無いパスに付けると root 所有になって書けない）。
- **OpenSearch 1 台。** `discovery.type=single-node`、`OPENSEARCH_INITIAL_ADMIN_PASSWORD`（`.env` の試し用の値）、`plugins.security.ssl.http.enabled=false`（Spark と Grafana は `http://opensearch:9200` に Basic 認証。`snmp_sinks.py` に自己署名を飛ばす設定が無い。OSS 版の `spark.tf:62` と同じ理由）、`node.store.allow_mmap=false`、`DISABLE_PERFORMANCE_ANALYZER_AGENT_CLI=true`、`OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m`（`oss/compose` と同じ）。インデックス `snmp-logs` は Spark の `_bulk` が作る。
- **Prometheus。** `prom/prometheus:v3.15.0`（Docker Hub 2026-09-25、amd64 / arm64 あり）。`command: --config.file=/etc/prometheus/prometheus.yml --web.enable-remote-write-receiver --storage.tsdb.retention.time=3d`。scrape は無し（空の `prometheus.yml`）。Grafana のデータソースは `datasources-oss/prometheus.yaml`（uid `amp`）をそのまま使うので、`PROMETHEUS_URL=http://prometheus:9090`。
- **Splunk。** `splunk/Dockerfile`（`SPLUNK_VERSION=10.4.3`、`ops/up-common.sh` と同値、linux/amd64 だけ）。`SPLUNK_START_ARGS=--accept-license`、`SPLUNK_GENERAL_TERMS=--accept-sgt-current-at-splunk-com`、`SPLUNK_PASSWORD`、`SPLUNK_HEC_TOKEN`（公式イメージが HEC を作る。TLS は既定の自己署名なので Spark は `--splunk-skip-verify`）、`SPLUNK_ROLE` は既定（standalone。app `netops_alerts` が残る）、`AWS_REGION` と `DEVICE_MAP`。`ALERTS_TOPIC_ARN` は渡さない（保存済みサーチは走るが、アラートアクションの SNS への publish は失敗してログに出るだけ。手元では見ない）。`SPLUNK_INDEX` は空（`ops/up.sh:240` と同じ。保存済みサーチは `index=*`）。
- **Grafana。** `grafana/Dockerfile`（`GRAFANA_VERSION=13.2.2`）。`grafana/start.sh` の契約: `PROMETHEUS_AUTH=none`、`OPENSEARCH_AUTH=basic`、`OPENSEARCH_URL=http://opensearch:9200`、`OPENSEARCH_INDEX=snmp-logs`、`OPENSEARCH_PASSWORD`、`PROMETHEUS_URL=http://prometheus:9090`、`AWS_REGION`、`GF_SECURITY_ADMIN_PASSWORD`。`ALERTS_TOPIC_ARN` を渡さないので、**アラートルールも送り先も入らない**（`start.sh:36-49` は `ALERTS_TOPIC_ARN` があるときだけ `provisioning/alerting` を並べる）。手元で見るのはダッシュボードだけ。
- **Spark の Kafka の認証。** 2 サービスとも `KAFKA_AUTH=none`（`snmp_sinks.py` の既定は `iam`。無いと PLAINTEXT の Kafka に IAM で繋ぎに行く）。
- **再起動。** `telegraf` / `spark-splunk` / `spark-http` は `restart: on-failure`（Splunk が起きる前に HEC への POST が落ちてジョブが終わるので、ECS のサービスの代わり）。
- **host へ出すポート。** compose の `ports` は全部 `127.0.0.1:` に縛る（Kafka の 9094〜9096、Kafbat UI 18080、Grafana 3000、Splunk 8000 / 8089、OpenSearch 9200、Prometheus 9090。管理ポートは `check.sh` が叩く）。host ネットワークの Telegraf の 4 つ（8080 / 57000 / 1162 / 5140）は `ports` ではないので全部のインターフェースで待つ（塞ぐのは BACKLOG）。
- **プロジェクト名。** compose.yaml に `name: nwc-local`（`oss/compose` の `nwc-oss` と同じ形）。無いとディレクトリ名の `compose` になり、ほかの `compose` ディレクトリの構成と volume がぶつかって `down.sh -v` が相手の volume を消す。volume は `nwc-local_<名前>`。
- **lab の手元用の切り替え（`lab/lab.sh`）。** 変更は 3 か所に絞る。(1) `pull`: `REGISTRY` が無ければ ECR の login を飛ばし、`docker pull` だけする（手元は `ghcr.io/nokia/srlinux:26.7.2` と `ghcr.io/srl-labs/network-multitool:v0.10.0`。`ops/lab-common.sh` の `SRLINUX_UPSTREAM:SRLINUX_TAG` / `MULTITOOL_UPSTREAM:MULTITOOL_TAG` と同値）。(2) `forward` / `hint` / `failover` の案内（障害のあとの案内の分岐は `fail-main)` ではなく `failover)` にある）: `TELEGRAF_IMAGE` の分岐を関数 `local_telegraf()`（`TELEGRAF_IMAGE` **または** `TELEGRAF_LOCAL=1`）にする（handling は同じ: trap の REDIRECT だけ。案内の文は「compose の Telegraf」）。(3) `/etc/*-lab.env` が無ければそのまま進む（いまも `|| true` で進む。確かめるだけ）。`telegraf` サブコマンド（EC2 の docker run）は手元では使わない（compose が持つ）。ファイルのモードは 100755 にする（`local/compose/lab.sh` と `lab.sh` 自身の `"$SELF" render` が直に呼ぶ。EC2 は `lab/setup.sh` が chmod する）。
- **`local/compose/lab.sh`（薄いラッパー）。** `.env`（無ければ `.env.example`）から `SRLINUX_IMAGE` / `MULTITOOL_IMAGE` の 2 キーだけ読み、`sudo env SRLINUX_IMAGE=… MULTITOOL_IMAGE=… TELEGRAF_LOCAL=1 ../../lab/lab.sh "$@"` を呼ぶ。`sudo -E` で全部渡さないのは、シェルに `REGISTRY` や `AWS_REGION` があっても ECR や SSM へ行かないため。`up` の前に毎回 `lab/lab.sh render` を打つ（`lab/lab.sh up` は `splab.clab.yml` があると render しないので、`gen_lab.py` で台数を変えたあとや `.env` のイメージを変えたあとに古い yml のまま deploy する）。`lab/lab.sh` は `cd "$(dirname "$SELF")"` するので、cwd はどこでもよい。
- **`local/compose/up.sh`。** `lab/lab_topology.py lab` の `--snmp-agents` / `--gnmi-targets` / `--device-map` を環境変数にして `docker compose up -d --build "$@"` を呼ぶ（compose.yaml は `${SNMP_AGENTS:-}` 等で受ける。値に `"` と `,` があるので `env_file` には書かず、呼び出し側の環境変数で渡す。空なら `docker compose config` は通るが Telegraf は `telegraf.sh` の形の検査で止まる）。`down.sh` は `docker compose down`（`-v` でデータも消す）。`check.sh` は検証方法の「合格の確認」を打つ。パスワードは curl の引数に載せず（`ps` に出る）`-K -` で標準入力から渡す。
- **`.env.example`。** キーは `SPLUNK_PASSWORD`、`SPLUNK_HEC_TOKEN`、`OPENSEARCH_PASSWORD`、`GF_SECURITY_ADMIN_PASSWORD`、`SRLINUX_IMAGE`、`MULTITOOL_IMAGE`、`AWS_REGION`。値は試し用（`oss/compose` の `NwcOss-Trial-2026!` と同じ流儀の、強度の条件を満たす固定値）。本物のシークレットは入れない（手元だけで使い、AWS につながない）。
- **版の正。** Kafka / Kafbat UI / OpenSearch は `oss/compose/compose.yaml`、Telegraf は `ops/lab-common.sh` の `TELEGRAF_VERSION`、Grafana / Splunk は `ops/up-common.sh`、SR Linux / multitool は `ops/lab-common.sh`。compose の build args と image タグはこれらと同値にし、`tests/test_local_compose.py` が照合する。Prometheus だけはこの構成にしか無いので compose が正。

### 資源の見積もり（未実測）

| 何 | RAM |
|---|---|
| Kafka ×3（heap 384 MB ずつ） | 約 2 GB |
| OpenSearch（heap 512 MB） | 約 1 GB |
| Spark ×2（driver 1 GB ずつ） | 約 3 GB |
| Splunk | 約 2 GB |
| Grafana / Prometheus / Telegraf / Kafbat UI | 約 1.5 GB |
| SR Linux ×6（ixr-d2l） | 約 6〜9 GB |

合計 16〜19 GB。`.wslconfig` の `memory` を 20 GB 以上にする（`check.sh` が `free -m` の total を見て 19456 MiB 未満なら警告。`memory=20GB` は `free -g` だと 19 になるので GiB で切り捨てない）。足りなければ `lab/gen_lab.py --leaves 2 --spines 1`（5 台。`leaves` は 2 の倍数で 2 以上、`spines` は 1 以上）で減らす。

### 実物で確認した契約

- `telegraf/telegraf.sh:31,42,59`: `KAFKA_AUTH=${KAFKA_AUTH:-iam}`、`none` は IAM の行を消し `aws_config` も書かない。
- `telegraf/telegraf.conf.in:285-347`: `outputs.kafka` ×5（metrics / gnmi / traps / logs / mdt）、`brokers = [__KAFKA_BROKERS__]`。
- `spark/snmp_sinks.py:49-51,74,119,137-138,196-200,497-499`: `OPENSEARCH_AUTH=basic` は `OPENSEARCH_PASSWORD` 必須、`PROMETHEUS_AUTH=none` で署名しない、`SPLUNK_HEC_TOKEN` があれば `--splunk-token-parameter` 不要。
- `grafana/provisioning/datasources-oss/{prometheus,opensearch}.yaml`: uid `amp` / `aoss-logs`、`${PROMETHEUS_URL}`、`${OPENSEARCH_URL}` + Basic。
- `lab/lab.sh:221-229`: `TELEGRAF_IMAGE` の `forward` は `iptables -t nat -I PREROUTING 1 -s 203.0.113.0/24 -d 203.0.113.1 -p udp --dport 162 -m comment --comment nwc-lab-telegraf -j REDIRECT --to-ports 1162`。
- `lab/lab.sh:110-114`: `pull` は `: "${REGISTRY:?}" "${AWS_REGION:?}"` と ECR login の後に `docker pull`。
- `splunk/netops_alerts/default/savedsearches.conf:53,97,137`: `index=* source="telegraf:interface"` など。Spark は sourcetype `netops:<topic>`、source `telegraf:<measurement>` で送る。
- `oss/compose/compose.yaml` の `x-kafka-env`: KRaft の固定 voter、`CLUSTER_ID` 固定、複製 3 / ISR 2 / パーティション 2。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `local/compose/compose.yaml` | 新規。上の構成。`name: nwc-local`。named volume: `kafka-1/2/3`、`opensearch`、`prometheus`、`splunk-etc`、`splunk-var`、`grafana`、`spark-splunk-ckpt`、`spark-http-ckpt` |
| `local/compose/prometheus.yml` | 新規。`global:` だけ（scrape 無し） |
| `local/compose/.env.example` | 新規。7 キー |
| `local/compose/up.sh` / `down.sh` / `check.sh` / `lab.sh` | 新規。`set -euo pipefail`、`cd "$(dirname "$0")"` |
| `local/compose/README.md` | 新規。前提（WSL2、docker-ce + `docker-compose-plugin`、containerlab、`snmp`、`.wslconfig`、binfmt は要らない）、手順、見る場所、消し方 |
| `lab/lab.sh` | `pull` の login を `REGISTRY` があるときだけに。`forward` / `hint` / `failover` の分岐を `local_telegraf()` に。モード 100755 |
| `ops/check.sh` | 手順 3 の `bash -n` に `local/compose/*.sh` を足す。`find` の対象に `local` を足す（`.py` は置かない） |
| `tests/test_local_compose.py` | 新規。下の検証方法 |
| `tests/test_lab_debug.py` | `forward` の分岐の正規表現を `if local_telegraf; then` に |
| `.gitignore` | 変えない。`.gitignore:4` の `.env` が `local/compose/.env` も無視する（`git check-ignore -v` で確かめる） |
| `README.md` / `docs/setup.md` / `docs/architecture/README.md` | 手元の構成の入口（`local/compose/README.md` へのリンク）と、ディレクトリ表に `local/` を足す |
| `docs/cycles/BACKLOG.md` | この行は着手済みのまま。完了は `/cycle-review` で |

## 再利用するもの

- `telegraf/`、`spark/`、`grafana/`、`splunk/` の Dockerfile と設定をそのまま build する（手元用の写しは作らない）。
- `oss/compose/compose.yaml` の `x-kafka-env` と `kafka-ui`（文字どおり写す。EXTERNAL リスナーだけ足す）。`oss/compose` 自体は変えない（OSS 版の確認用のまま。007 で扱いを決める）。
- `lab/lab.sh`、`lab/lab_topology.py`、`lab/gen_lab.py`、`lab/splab.clab.yml.in`、`lab/srlinux/*.cli`（trap / syslog の宛先 203.0.113.1 は手元でも同じ）。
- `grafana/provisioning/datasources-oss/`（OSS 版と同じ分岐）、ダッシュボード `metrics.json` / `logs.json`（アラートルールは `ALERTS_TOPIC_ARN` が無いので入らない）。
- `tests/test_lab_debug.py` の `check` / `read` の流儀。

## 実装ステップ

1. `local/compose/compose.yaml` と `prometheus.yml`、`.env.example` を書く。Kafka の 3 台と `kafka-ui` は `oss/compose` から写し、EXTERNAL リスナーを足す。
2. `up.sh` / `down.sh` / `lab.sh` / `check.sh` を書く。`check.sh` は検証方法の「合格の確認」のコマンドを順に打ち、どれかが落ちたら非 0。
3. `lab/lab.sh` の 3 か所を直す。`TELEGRAF_IMAGE` の分岐を関数 `local_telegraf()`（`[ -n "${TELEGRAF_IMAGE:-}" ] || [ "${TELEGRAF_LOCAL:-0}" = 1 ]`）にまとめる。
4. `tests/test_local_compose.py` を書き、`ops/check.sh` に `local/compose/*.sh` を足す。`bash ops/check.sh` を通す。
5. Mac の手元で `docker compose config` と `docker compose build telegraf grafana spark-http`（amd64 の Splunk は Mac では build しない）が通ることを確かめる（実装の確認。WSL の確認はユーザー）。
6. `local/compose/README.md` と docs を書く。
7. WSL で通しの検証（下）。結果は `build.md` に貼る。WSL が無い実装環境では「WSL 未実施」と書く。

## 検証方法

### 手元（実装環境。AWS も WSL も要らない）

- `bash ops/check.sh` の最後の行が `すべて通過`。
- `uv run --group dev python tests/test_local_compose.py` が `ok …` を並べて終わる。確かめる内容:
  - `local/compose/compose.yaml` が yaml として読め、services が `telegraf kafka-1 kafka-2 kafka-3 kafka-ui spark-splunk spark-http opensearch prometheus splunk grafana` の 11 個
  - `telegraf` が `network_mode: host`、build の context が `../../telegraf`
  - `kafka-N` の image が `oss/compose/compose.yaml` の `kafka-1` と同じ、`kafka-ui` の image も同じ
  - `opensearch` の image が `oss/compose` の `opensearch-1` と同じ、`discovery.type=single-node` と `plugins.security.ssl.http.enabled=false` がある
  - `grafana` / `splunk` の build args の `GRAFANA_VERSION` / `SPLUNK_VERSION` が `ops/up-common.sh` の値、`telegraf` の `TELEGRAF_VERSION` が `ops/lab-common.sh` の値
  - `prometheus` の command に `--web.enable-remote-write-receiver`、`spark-http` の command に `--prometheus-url http://prometheus:9090/api/v1/write`、`spark-splunk` に `--splunk-skip-verify`、どちらにも `--sinks` と `--checkpoint file:///opt/spark/work-dir/checkpoint`、`iceberg` が無い
  - compose.yaml の `${VAR}` が全部 `.env.example` のキーか `up.sh` が export する 3 つ（`SNMP_AGENTS` / `GNMI_TARGETS` / `DEVICE_MAP`）に含まれる
  - `.env.example` の `SRLINUX_IMAGE` / `MULTITOOL_IMAGE` が `ops/lab-common.sh` の upstream:tag と同値
  - `lab/lab.sh` に `local_telegraf()`（`TELEGRAF_IMAGE` か `TELEGRAF_LOCAL=1`）があり、`forward` と `hint` がそれを呼ぶ、`pull` に `REGISTRY` の有無の分岐がある
  - `local/` の下に `.py` が無い
- `docker compose -f local/compose/compose.yaml --env-file local/compose/.env.example config` が exit 0（`SNMP_AGENTS` 等は空のまま）。

### WSL（ユーザーが打つ。期待出力まで）

1. `cp local/compose/.env.example local/compose/.env`、`local/compose/up.sh` → `docker compose ps` で 11 サービスが `running`（Splunk は `healthy` まで 2〜3 分）。
2. `local/compose/lab.sh up` → 最後に `この EC2 の Telegraf へ: trap 162/udp を 1162/udp へ向けた` に相当する手元用の文。`sudo iptables -t nat -S PREROUTING | grep nwc-lab-telegraf` が 1 行。
3. `local/compose/lab.sh check` が全台 `ok`。
4. 2〜3 分待って `local/compose/check.sh` が全部 `ok`:
   - Kafka: `curl -s 127.0.0.1:18080/api/clusters/nwc/topics` に `metrics` `gnmi` `traps` `logs` の名前がある（トピックは Spark の `ensure_topics` が作るので、Telegraf から届いた証拠ではない。届いたかは次の Prometheus 以降で見る）
   - Prometheus: `curl -s 'http://127.0.0.1:9090/api/v1/query?query=count(snmp_interface_ifOperStatus)'` の `result[0].value[1]` が `0` より大きい
   - OpenSearch: `curl -s -u admin:… http://127.0.0.1:9200/snmp-logs/_count` の `count` が 0 より大きい
   - Splunk: `curl -sk -u admin:… https://127.0.0.1:8089/services/search/jobs/export -d search='search index=* sourcetype=netops:* earliest=-10m | stats count' -d output_mode=json` の `count` が 0 より大きい
   - Grafana: `curl -s -u admin:… http://127.0.0.1:3000/api/datasources` に uid `amp` と `aoss-logs`、`/api/datasources/uid/amp/health` が `OK`
5. `local/compose/lab.sh fail-main` → Grafana の `metrics` ダッシュボードで `dc1-leaf-01 ethernet-1/1` が DOWN、`logs` ダッシュボードに linkDown の trap と syslog、Splunk で `index=* source="telegraf:snmp_trap"` に linkDown。`heal-main` で戻る。
6. `local/compose/lab.sh trap-test` → Splunk の保存済みサーチ `netops_trap` が次の実行で 1 件（`index=_internal source=*scheduler.log savedsearch_name=netops_trap` で `result_count=1`）。
7. `local/compose/down.sh -v` と `local/compose/lab.sh down` のあと `docker ps -a` に nwc のコンテナが無く、`docker volume ls` に `nwc-local_` の volume が無い。

合格は 4〜6 が通ること。

## 未確定事項とリスク

1. **WSL2 のカーネルで SR Linux と `modprobe bonding` が動くか（未確認）。** containerlab は WSL2 を公式に支援しているが、このユーザーの PC では未実施。bonding が無ければ VM 2 台の bond0 が上がらない。`lab/lab.sh:126` の `modprobe bonding || echo …` は失敗しても続けるので `lab up` は止まらず、`lab check` の `bond0 が無い` で分かる（EC2 の挙動は変えない）。失敗したら `uname -r` と `zcat /proc/config.gz | grep BONDING` を報告してもらい、次のサイクルで扱う。
2. **Docker Engine の場所。** `network_mode: host` と `iptables` の REDIRECT が効くのは、docker-ce が WSL の Ubuntu の中にあるとき。Docker Desktop の WSL 統合はエンジンが別の distro にいるので、host ネットワークが Ubuntu のネットワーク名前空間にならず、この設計は動かない（未確認。README に「docker-ce を WSL に入れる」と書く）。
3. **iptables の実装。** Ubuntu の `iptables` が nft 版でも `REDIRECT` は使える。Docker が自分の規則を入れた後に `-I PREROUTING 1` で先頭に入れるので順序は EC2 と同じ。WSL で `DOCKER-USER` チェインが無い構成は無い想定（Docker が作る）。
4. **Splunk のイメージは amd64 だけ。** WSL（x86_64）は問題ない。Mac（Apple Silicon）は `platform: linux/amd64` のエミュレーションになり、起動に数分かかるか落ちる。Mac は未確認のまま残す。
5. **Spark の checkpoint の書き込み権限。** `/opt/spark/work-dir` に named volume を付ければ `spark` ユーザー（uid 185）で書ける想定。build で `docker compose logs spark-http` に `Permission denied` が無いことを見る。
6. **Telegraf の 8080/tcp（health）と 57000/tcp（MDT）が host の他のプロセスとぶつかる。** README に書く。`telegraf.conf.in` で固定値（実装で確かめた）なので、環境変数で変えられるようにするのは BACKLOG。
7. **Kafka の advertised listener が `localhost`。** compose の外の Telegraf（host）からだけ使う前提。WSL の外（Windows 側）からは使わない。
8. **メモリ。** 見積もりは未実測。SR Linux の実測は EC2（arm64）の値しか無い。WSL で `free -m` を見て、足りなければ 5 台構成に落とす。
9. **OpenSearch 3.9 の security plugin を TLS なしで動かす設定名。** `plugins.security.ssl.http.enabled: false` は `oss/terraform` の OSS 版で AWS で動いた設定（2026-10-07）と同じ。single-node との組み合わせは手元で未確認。

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261008-cycle-006-local-compose-design.html -->
