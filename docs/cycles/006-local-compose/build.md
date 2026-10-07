# Cycle 006 local-compose 実装: 手元の docker compose で動く構成を作る

## Round 1

実装モデル: claude-opus-5-5 / effort: xhigh

設計モデル（PM: fable-5.1）は実装モデルより上位なので、design.md はそのまま実装した。範囲は実装ステップ 1〜6 と検証方法の「手元」。ステップ 7（WSL の通し）は **WSL 未実施**（ユーザーが打つ）。AWS には触っていない。

### 変更ファイル

| ファイル | 変更 |
|---|---|
| `local/compose/compose.yaml` | 新規。プロジェクト名 `nwc-local`、11 サービス、named volume 10 個、ネットワーク `nwc-local` |
| `local/compose/prometheus.yml` | 新規。`global:` だけ |
| `local/compose/.env.example` | 新規。7 キー（試し用の固定値） |
| `local/compose/up.sh` / `down.sh` / `check.sh` / `lab.sh` | 新規。`lab.sh` は `up` の前に毎回 `render` する（逸脱 13） |
| `local/compose/README.md` | 新規。前提、手順、見る場所、ぶつかりやすいポート、消し方 |
| `lab/lab.sh` | `local_telegraf()` を足し、`hint` / `failover` の案内 / `forward` の分岐に使う。`pull` の ECR login を `REGISTRY` があるときだけに。ファイルのモードを 100755 に |
| `ops/check.sh` | `bash -n` に `local/compose/*.sh`、`.py` の `find` に `local` |
| `tests/test_local_compose.py` | 新規。75 項目 |
| `tests/test_lab_debug.py` | `forward` の分岐の期待値を `local_telegraf` に合わせる（下の「期待値を変えたテスト」） |
| `README.md` / `docs/setup.md` / `docs/architecture/README.md` | 入口のリンク、ドキュメントの表とディレクトリの表に `local/` |
| `docs/cycles/BACKLOG.md` | 範囲外で見つけた 4 件と、セルフレビューで直さなかった 8 件を `- [ ]` で足す |

### 設計から逸脱した点

1. **`.gitignore` は変えていない。** `local/compose/.env` は既存の `.gitignore:4 .env` が既に無視している（`git check-ignore -v local/compose/.env` → `.gitignore:4:.env	local/compose/.env`）。テストが「.env は git に入らず .env.example は入る」を確かめる。
2. **Spark の 2 サービスに `KAFKA_AUTH=none` を渡す。** design.md の Spark の環境変数に無いが、`snmp_sinks.py` の既定は `iam`（MSK）なので、無いと PLAINTEXT の Kafka に IAM で繋ぎに行く。
3. **`failover` に案内の分岐を足した。** design.md は「`forward` / `hint` / `fail-main` の案内」と書くが、`fail-main)`（lab.sh:154-160）に `TELEGRAF_IMAGE` の分岐は無く、障害のあとの案内の分岐は `failover)` にある。そこに `elif local_telegraf` を足した（3 か所の範囲の中）。
4. **`lab/lab.sh` のモードを 100644 → 100755。** `local/compose/lab.sh` は `lab/lab.sh` を直に呼び、`lab.sh` 自身も `"$SELF" render` / `pull` / `forward` で自分を呼ぶ。EC2 は `lab/setup.sh:28` が `chmod 0755` するが、git から取り出した手元のファイルは実行できない。
5. **`local/compose/lab.sh` は `sudo -E` ではなく `sudo env` で 3 つだけ渡す。** `.env` は全部 source せず、`SRLINUX_IMAGE` / `MULTITOOL_IMAGE` の 2 キーだけ sed で読む（無ければ `.env.example`）。シェルに `REGISTRY` や `AWS_REGION` があっても ECR や SSM へ行かないようにするため。
6. **`check.sh` のメモリの注意は `free -m` の total が 19456 MiB 未満。** design.md は `free -g` だが、`.wslconfig` の `memory=20GB` は `free -g` で 19 になるので、GiB の切り捨てで誤って注意が出る。
7. **`check.sh` はパスワードを curl の引数に載せない**（`ps` に出る）。`-K -` で `user = "admin:…"` を標準入力から渡す（`"` と `\` は逃がす）。
8. **compose の `ports` は全部 `127.0.0.1`。** design.md の構成図で 9200 / 9090 / 8089 は書いていないが、`check.sh` が OpenSearch・Prometheus・Splunk の管理ポートを叩くので出した。host のネットワークにいる Telegraf の 4 つ（8080 / 57000 / 1162 / 5140）は `ports` ではないので、host の全部のインターフェースで待つ（下の「セルフレビュー」の S3）。
9. **`telegraf` / `spark-splunk` / `spark-http` に `restart: on-failure`。** Splunk が起きる前に Spark の HEC への POST が落ちてジョブが終わるため（ECS のサービスの代わり）。
10. **compose.yaml の `SNMP_AGENTS` / `GNMI_TARGETS` / `DEVICE_MAP` は `${VAR:-}`。** `docker compose config` を `.env.example` だけで通すため（空なら Telegraf は `telegraf.sh` の形の検査で止まる。テストが確かめる）。
11. **`local/compose/README.md` の前提に `docker-compose-plugin` と containerlab・`snmp` の入れ方を書いた。** `docs/setup.md` の docker-ce の手順は `docker-compose-plugin` を入れない。
12. **compose.yaml に `name: nwc-local` を足した**（`oss/compose` の `name: nwc-oss` と同じ形）。無いとプロジェクト名がディレクトリ名の `compose` になり、ほかの `compose` という名前のディレクトリの構成と volume（`compose_grafana` など）がぶつかって、`down.sh -v` で相手の volume も消える（下の「セルフレビュー」の S-b）。そのため design.md の検証方法 7 の「`docker volume ls` に `compose_` の volume が無い」は `nwc-local_` で見る。
13. **`local/compose/lab.sh` は `up` の前に毎回 `lab/lab.sh render` を打つ**（`lab/lab.sh` は変えていない）。`lab/lab.sh up` は `splab.clab.yml` があると render しないので、`gen_lab.py` で台数を変えたあとや `.env` のイメージを変えたあとに古い yml のまま deploy する（下の「セルフレビュー」の S1）。

### 期待値を変えたテスト

`tests/test_lab_debug.py:374` の「`forward` は `TELEGRAF_IMAGE` があれば SSM の NLB を見ずに抜ける」は、`forward)` の直後が `if [ -n "${TELEGRAF_IMAGE:-}" ]; then` であることを正規表現で見ていた。design.md の実装ステップ 3 がこの分岐を `local_telegraf()` にまとめるので、正規表現を `if local_telegraf; then` に替え、`local_telegraf()` の中に `[ -n "${TELEGRAF_IMAGE:-}" ] || ` があることを足した（元の期待値は「分岐の書き方」を縛っていて、分岐の意味は変わっていない。動かして見るのは `tests/test_local_compose.py` の forward の 3 項目）。

### 不具合（テストで見つけて直した）

- `check.sh` がどこにも繋がらないとき「読めない応答:  」（空白だけ）になった。原因はヒアストリング `<<<"$(...)"` が末尾に改行を足すこと。`s.strip()` を見て空なら「空」と出す。
- `check.sh` の Splunk が空の応答で「0 件」になった（`max([...] or [0])`）。`or [0]` を外し、空なら「読めない応答」で NG にする。

### 検証（手元）

#### `bash ops/check.sh`（最後の編集のあと）

```

== 1. terraform fmt -check -recursive terraform oss/terraform
差分なし

== 2. 9 つのルートの validate（terraform/ と oss/terraform/）
terraform/base/ecr  OK
terraform/base/core  OK
terraform/agent  OK
terraform/pipeline/lab  OK
terraform/pipeline/stream  OK
terraform/pipeline/analytics  OK
terraform/pipeline/graph  OK
terraform/pipeline/nautobot  OK
terraform/workflow  OK
oss/terraform/base/ecr  OK
oss/terraform/base/core  OK
oss/terraform/agent  OK
oss/terraform/pipeline/lab  OK
oss/terraform/pipeline/stream  OK
oss/terraform/pipeline/analytics  OK
oss/terraform/pipeline/graph  OK
oss/terraform/pipeline/nautobot  OK
oss/terraform/workflow  OK

== 3. ops スクリプトの構文
構文エラーなし

== 4. 模擬テスト
（各テストファイルの最後の行）
通過 137 / 失敗 0
通過 485 / 失敗 0
通過 158 / 失敗 0
通過 72 / 失敗 0
通過 7 / 失敗 0
通過 82 / 失敗 0
通過 75 / 失敗 0
62 項目すべて通過
通過 158 / 失敗 0
通過 139 / 失敗 0
通過 56 / 失敗 0
通過 75 / 失敗 0
通過 95 / 失敗 0
通過 324 / 失敗 0

すべて通過
```

終了コード 0。

#### `uv run --group dev python tests/test_local_compose.py`（`ops/check.sh` の中の出力）

```
ok services は 11 個（telegraf kafka-1 kafka-2 kafka-3 kafka-ui spark-splunk spark-http opensearch prometheus splunk grafana）
ok named volume は design.md の 10 個（kafka-1/2/3、opensearch、prometheus、splunk-etc、splunk-var、grafana、spark-*-ckpt）
ok ネットワークは nwc-local
ok プロジェクト名は nwc-local（ディレクトリ名の compose にしない。volume が nwc-local_* になり、oss/compose の nwc-oss とも別）
ok compose の ports は全部 127.0.0.1 に縛る（Kafka・Splunk・Grafana・OpenSearch を WSL の外へ出さない。host のネットワークにいる Telegraf の 4 つはこの外）
ok telegraf は network_mode: host で、build の context は ../../telegraf
ok kafka-1 の image は oss/compose の kafka-1 と同じ（apache/kafka:4.3.1）
ok kafka-2 の image は oss/compose の kafka-1 と同じ（apache/kafka:4.3.1）
ok kafka-3 の image は oss/compose の kafka-1 と同じ（apache/kafka:4.3.1）
ok kafka-ui は oss/compose の kafka-ui と image・環境・ポートが同じ
ok opensearch の image は oss/compose の opensearch-1 と同じ
ok grafana の GRAFANA_VERSION と splunk の SPLUNK_VERSION は ops/up-common.sh の値
ok telegraf の TELEGRAF_VERSION は ops/lab-common.sh の値
ok .env.example の SRLINUX_IMAGE / MULTITOOL_IMAGE は ops/lab-common.sh の upstream:tag
ok splunk は linux/amd64（上流が amd64 だけ）
ok kafka-1: EXTERNAL 以外の KAFKA_* と CLUSTER_ID は oss/compose と同じ値
ok kafka-1: リスナーは oss/compose のものに EXTERNAL://:9094（advertised は localhost:9094）を足し、127.0.0.1:9094 に出す
ok kafka-2: EXTERNAL 以外の KAFKA_* と CLUSTER_ID は oss/compose と同じ値
ok kafka-2: リスナーは oss/compose のものに EXTERNAL://:9095（advertised は localhost:9095）を足し、127.0.0.1:9095 に出す
ok kafka-3: EXTERNAL 以外の KAFKA_* と CLUSTER_ID は oss/compose と同じ値
ok kafka-3: リスナーは oss/compose のものに EXTERNAL://:9096（advertised は localhost:9096）を足し、127.0.0.1:9096 に出す
ok トピックは自動で作る（Telegraf が最初に書く）
ok telegraf の KAFKA_BROKERS は 3 台の EXTERNAL（localhost:9094-9096）、KAFKA_AUTH=none、SYSLOG_STANDARD は ops/lab-common.sh の LAB_SYSLOG_STANDARD
ok telegraf の GNMI_USERNAME / GNMI_PASSWORD / SNMP_COMMUNITY は ops/lab-common.sh の lab の公開既定値
ok up.sh の値を入れた telegraf の環境で telegraf.sh render が通り、出力 5 つが localhost:9094-9096 へ書き、IAM の設定が無い
ok docker compose を直に打つ（SNMP_AGENTS が空）と telegraf.sh は形の検査で止まる（compose.yaml の頭の注意のとおり。up.sh から上げる）
ok spark-splunk: spark/Dockerfile の同じイメージ、KAFKA_AUTH=none（PLAINTEXT）、--checkpoint は volume の下、iceberg が無い
ok spark-http: spark/Dockerfile の同じイメージ、KAFKA_AUTH=none（PLAINTEXT）、--checkpoint は volume の下、iceberg が無い
ok spark-splunk の引数と環境で parse_args が通る（sinks は splunk、HEC は https://splunk:8088 を検証なしで、token は SPLUNK_HEC_TOKEN）
ok spark-splunk は SPLUNK_HEC_TOKEN が無いと parse_args で止まる（SSM の名前を渡していないので、環境の token が要る）
ok spark-http の引数と環境で parse_args が通る（opensearch,prometheus。remote write は http://prometheus:9090/api/v1/write）
ok up.sh の DEVICE_MAP を Spark の --device-map に入れると、lab の機器の管理 IP が機器名に引ける
ok Spark の --metric-topics と --log-topics は Telegraf が書くトピックだけ
ok opensearch は single-node、HTTP の TLS を切る（Spark と Grafana は http:// に Basic 認証）。mmap と heap は oss/compose と同じ
ok prometheus は remote write を受け、設定は prometheus.yml（scrape なし）
ok grafana は PROMETHEUS_AUTH=none / OPENSEARCH_AUTH=basic で、URL は compose の中の prometheus と opensearch、ALERTS_TOPIC_ARN は渡さない
ok Spark が書く OpenSearch のインデックスと Grafana が読むインデックスが同じ（snmp-logs）
ok splunk の HEC の token は spark-splunk と同じ .env の値
ok .env.example のキーは design.md の 7 つ
ok compose.yaml の ${VAR} は全部 .env.example のキーか up.sh が渡す 3 つ（SNMP_AGENTS / GNMI_TARGETS / DEVICE_MAP）
ok SPLUNK_HEC_TOKEN は uuid の形（Splunk のイメージが作る HEC の token。ops/up.sh と同じ形）
ok local/compose/.env は git に入らず、.env.example は入る
ok local/ の下に .py が無い（ops/check.sh の ast.parse の対象だが、置かない）
ok local/compose の 4 つと lab/lab.sh は実行できる（local/compose/lab.sh は ../../lab/lab.sh を直に呼び、lab.sh も自分を "$SELF" で呼ぶ）
ok local/compose の 4 つと lab/lab.sh は 1 つずつ bash -n が通る（bash -n a b は a しか見ない）
ok ops/check.sh の bash -n に local/compose/*.sh、.py の find に local がある
ok lab/lab.sh pull: REGISTRY が無ければ aws も docker login も打たず、2 つのイメージを docker pull するだけ
ok lab/lab.sh pull: REGISTRY があれば（lab の EC2）今までどおり ECR に login してから pull する
ok lab/lab.sh pull: REGISTRY があって AWS_REGION が無ければ、今までどおり何も取らずに止まる
ok lab/lab.sh forward: TELEGRAF_LOCAL=1 なら aws を打たず、trap の 162 → 1162 の REDIRECT を 1 本だけ入れ、案内は「compose の Telegraf へ」
ok lab/lab.sh forward: TELEGRAF_IMAGE（デバッグ用の EC2）は今までどおり同じ REDIRECT で、案内は「この EC2 の Telegraf へ」
ok lab/lab.sh forward: TELEGRAF_LOCAL が 1 でなければ stream の分岐（AWS_REGION が要る）へ行き、REDIRECT を入れない
ok lab/lab.sh hint: TELEGRAF_LOCAL=1 なら compose の Grafana と Splunk を案内し、TELEGRAF_IMAGE と stream と転送なしの案内は変わらない
ok lab/lab.sh failover（fail-main のあとの案内。TELEGRAF_IMAGE の分岐があるのはここ）: TELEGRAF_IMAGE の次に local_telegraf の分岐があり、compose の Grafana と Splunk を案内する
ok lab/lab.sh: /etc/*-lab.env が無くても止まらない（手元。ls が失敗しても || true）
ok local/compose/lab.sh: .env が無ければ .env.example のイメージと TELEGRAF_LOCAL=1 の 3 つだけを sudo env で lab/lab.sh に渡す（sudo -E にしない）
ok local/compose/lab.sh pull: シェルに REGISTRY や AWS_REGION があっても ECR に行かず、ghcr.io の 2 つを取る
ok local/compose/lab.sh forward: REDIRECT を 1 本入れ、案内は「compose の Telegraf へ」（aws は打たない）
ok local/compose/lab.sh: .env があればそちらのイメージを使う（値の " と ' は外す）
ok local/compose/lab.sh: .env に MULTITOOL_IMAGE が無ければ sudo を打たずに止まる
ok local/compose/lab.sh up: lab/lab.sh render を打ってから up し、splab.clab.yml を今の .in と .env のイメージで作り直してから deploy する
ok local/compose/lab.sh: up 以外（down など）は render しない
ok up.sh: .env が無ければ docker を打たずに止まり、cp .env.example .env と、パスワードを変えるなら最初の up.sh の前（あとからなら down.sh -v）を案内する
ok up.sh: lab/lab_topology.py の 3 つ（SNMP_AGENTS / GNMI_TARGETS / DEVICE_MAP）を環境で渡して docker compose up -d --build を打つ
ok up.sh: 引数は docker compose up に渡す（up.sh telegraf で Telegraf だけ作り直す）
ok down.sh -v: docker compose down -v
ok check.sh: 応答が全部そろえば 6 項目とも ok で「すべて ok」、終了コード 0（メモリが 20 GB 以上なら注意を出さない）
ok check.sh: パスワードは curl の引数に載せず（ps に出る）、-K - の標準入力で user = "admin:…" として渡す（OpenSearch・Splunk・Grafana 2 つの 4 回）
ok check.sh: Splunk の検索は sourcetype=netops:*（Spark の SPLUNK_SOURCETYPE_PREFIX）、Prometheus は Grafana のダッシュボードとアラートが使う snmp_interface_ifOperStatus
ok check.sh: Grafana で見る uid（amp / aoss-logs）は grafana/provisioning/datasources-oss の定義にある
ok check.sh: Kafka で見るトピック（metrics / gnmi / traps / logs）は Telegraf が書くトピック
ok check.sh: 1 つが 0 件なら、そこだけ NG にして残りも見てから終了コード 1。メモリが 20 GB 未満なら注意を出す
ok check.sh: どこにも繋がらなくても set -e で途中で落ちず、6 項目とも「読めない応答: 空」の NG で終了コード 1
ok check.sh: パスワードの " と \ は curl の設定の書き方で逃がす
ok check.sh: .env が無ければ curl を打たずに止まる
通過 75 / 失敗 0
```

#### `docker compose -f local/compose/compose.yaml --env-file local/compose/.env.example config`

終了コード 0、434 行、標準エラー 0 行（セルフレビューの直しのあとに取り直した）。1 行目は `name: nwc-local`、volume は `nwc-local_grafana` などの形。

#### `docker compose -f local/compose/compose.yaml --env-file local/compose/.env.example build telegraf grafana spark-http`（Mac、arm64）

1 回目は Docker Hub への接続で Grafana だけ落ちた（`failed to do request: Head "https://registry-1.docker.io/v2/grafana/grafana/manifests/13.2.2": EOF`。直後の `docker manifest inspect grafana/grafana:13.2.2` は `net/http: TLS handshake timeout`、`13.2.1` は取れた）。`docker pull grafana/grafana:13.2.2` が通ったあと、3 つまとめて打ち直した:

```
 Image nwc-local-telegraf Building 
 Image nwc-local-grafana Building 
 Image nwc-local-spark Building 
...
#24 naming to docker.io/library/nwc-local-telegraf:latest done
#25 naming to docker.io/library/nwc-local-grafana:latest done
#26 naming to docker.io/library/nwc-local-spark:latest done
 Image nwc-local-telegraf Built 
 Image nwc-local-grafana Built 
 Image nwc-local-spark Built 
```

終了コード 0。Splunk は amd64 だけなので build していない。

`name: nwc-local` を足したあとに打ち直した（キャッシュが効いて `#17`〜`#23` は `CACHED`）:

```
 Image nwc-local-telegraf Built 
 Image nwc-local-grafana Built 
 Image nwc-local-spark Built 
```

終了コード 0。

セルフレビューの直しのあと（compose.yaml と 3 つの build の context は変えていない）にもう一度打った:

```
 Image nwc-local-grafana Built 
 Image nwc-local-spark Built 
 Image nwc-local-telegraf Built 
```

終了コード 0。

#### Spark の checkpoint の書き込み（design.md の未確定事項 5）

```
$ docker run --rm -v nwc-local-ckpt-probe:/opt/spark/work-dir --entrypoint /bin/bash nwc-local-spark -c 'id; ls -ld /opt/spark/work-dir; mkdir -p /opt/spark/work-dir/checkpoint/x && echo writable'
uid=185(spark) gid=185(spark) groups=185(spark)
drwxrwxr-x 2 spark spark 4096 Jul 24 22:08 /opt/spark/work-dir
writable
```

確かめたあと `docker volume rm nwc-local-ckpt-probe`（`docker volume ls -q | grep -c nwc-local-ckpt-probe` → `0`）。

#### WSL（ステップ 7）

WSL 未実施（ユーザーが打つ）。

### セルフレビュー

- 自分: claude-opus-5-5 / effort: xhigh
- 反対弁護人: Agent（general-purpose、model: opus）。effort は Agent ツールで指定できないので未指定。読み取り専用で頼み、打ったのは `tests/test_local_compose.py` と `docker compose config` の 2 つだけ（本人の報告）。いまの `git status --porcelain -uall` は自分の変更だけ。
- 入力は design.md と実装したファイル。会話の記憶は根拠にしていない。

#### 自分で見た分（反対弁護人の前）

| # | 分類 | 観点と場所 | 破綻シナリオ | 確かめたもの | 片付け |
|---|---|---|---|---|---|
| S-a | Should fix | [ドキュメントの正確性] `local/compose/README.md:5` | 「Grafana のアラートルールと Splunk の保存済みサーチは入るが、通知先が無い」と書いていた。実際は Grafana にアラートルールが入らない | `grafana/start.sh:36-49` は `ALERTS_TOPIC_ARN` があるときだけ `alerting/` を写す（読んだだけ）。compose は `ALERTS_TOPIC_ARN` を渡さない（テストの「grafana は … ALERTS_TOPIC_ARN は渡さない」） | 直した: 「Grafana は `ALERTS_TOPIC_ARN` が無いのでアラートルールを入れず、データソースとダッシュボードだけ」 |
| S-b | Must fix | [data loss] `local/compose/compose.yaml:14` | `name:` が無いとプロジェクト名がディレクトリ名の `compose` になる。ほかの `compose` という名前のディレクトリの構成と volume がぶつかり、こちらの `down.sh -v` で相手の volume が消える | `projname.py`（一時ディレクトリに a/compose と b/compose を作る）: `a create: 0 volumes: ['compose_grafana']` → `after b down -v, volumes: []` | 直した: `name: nwc-local`（逸脱 12）。テスト「プロジェクト名は nwc-local」 |
| S-c | Should fix | [UX] `local/compose/up.sh:15` | up.sh は Splunk の healthy まで「3〜5 分」、README は「2〜3 分」で食い違う | 読んだだけ | 直した: 2〜3 分にそろえた |
| Nit | Nit | [UX] `lab/lab.sh:111` | `render` が「イメージは ?」と出す（`${REGISTRY:-?}`。手元には `REGISTRY` が無い） | `s1probe.py` の出力 `render（6 台のとき）: 0 splab.clab.yml を作った（イメージは ?）` | 直さない（表示だけ。design.md の 3 か所の外）。BACKLOG に足した |
| Nit | Nit | [設計整合性] design.md:159（リスク 1） | 「modprobe で止まる」と書くが、`lab/lab.sh:126` は `\|\| echo` で先へ進む | 読んだだけ | design.md の直しとして PM に報告（下の N5） |
| Nit | Nit | [保守性] `local/compose/check.sh:36-52` | `<<<"$(get … \|\| true)"` の `\|\| true` は効いていない（外しても挙動が変わらない） | `ortrue.sh`（`set -euo pipefail` で、関数のヒアストリングのコマンド置換を失敗させる）: `/bin/bash（GNU bash, version 3.2.57）: 終了コード 0 / 関数は走った / 次の行に来た` 、`nwc-local-spark の /bin/bash（GNU bash, version 5.1.16）: 終了コード 0` も同じ。`inject2.py`（6 つの `\|\| true` を全部外す）: `終了コード 0 / 通過 75 / 失敗 0` | 直さない（害は無い） |
| Nit | Nit | [runtime] `local/compose/compose.yaml:212` | `networks.default.name: nwc-local` は `-p` でプロジェクト名を変えても同じ名前になる（S2 の試しで、別プロジェクトの `down -v` が `nwc-local` のネットワークを消した。nwc-local は上がっていなかった） | up.sh / down.sh は `-p` を渡さず、`up` / `down` の後ろには置けない: `docker compose … up -p other --dry-run` → `unknown shorthand flag: 'p' in -p`（down も同じ） | 直さない（`docker compose -p` を直に打ったときだけ） |

退行の注入（`inject.py`。1 つずつ入れて `tests/test_local_compose.py` を走らせ、終わったらバイト単位で戻す）: 10 件中 9 件で落ちた。落ちなかったのは「check.sh: Kafka の get の `|| true` を外す」（`終了コード 0`）で、上の Nit のとおり効いていない行なので捕まえるものが無い。

| 注入 | 結果 |
|---|---|
| lab.sh pull: REGISTRY の分岐を外す（いつも ECR に login） | 終了コード 1 |
| lab.sh local_telegraf: TELEGRAF_LOCAL を見ない | 終了コード 1 |
| lab.sh failover: local_telegraf の案内の分岐を消す | 終了コード 1 |
| check.sh: パスワードを -u で引数に載せる | 終了コード 1 |
| check.sh: Kafka の get の \|\| true を外す | 終了コード 0 |
| check.sh: 1 つ NG でもすぐ exit 0 で抜ける | 終了コード 1 |
| local lab.sh: sudo -E にする | 終了コード 1 |
| compose: grafana のポートを 0.0.0.0 に出す | 終了コード 1 |
| compose: telegraf の KAFKA_AUTH を iam にする | 終了コード 1 |
| up.sh: DEVICE_MAP を渡さない | 終了コード 1 |

#### 反対弁護人の指摘と再現

Must fix 0、Should fix 4、Nit 5、未確認の懸念 4。

| # | 分類 | 観点と場所 | 破綻シナリオ | 再現（コマンドと出力） | 片付け |
|---|---|---|---|---|---|
| S1 | Should fix | [runtime bugs] `lab/lab.sh:123`、`local/compose/README.md:30` | README のとおり `gen_lab.py --leaves 2 --spines 1` を打って `lab.sh up` をやり直しても、`up` は `splab.clab.yml` があると render しないので 6 台の古い yml で deploy する。古い yml は gen_lab.py が消した `.cli` を指し、Telegraf・Spark（.in から 5 台）と lab の台数が食い違う。`.env` のイメージを変えたときも古いイメージで deploy する | `s1probe.py`（lab/ の写しで render → gen_lab）: `.in のノード: [… 'dc1-spine-01']` / `yml のノード（up はこれで deploy する）: [… 'dc1-spine-01', 'dc1-spine-02']` / `yml が指す startup-config で無いもの: ['srlinux/dc1-spine-02.cli']` / `lab.sh up の render の条件: ['[ -f "$TOPO" ] \|\| "$SELF" render']`。containerlab の止まり方は実測していない | 直した: `local/compose/lab.sh:15` が `up` の前に毎回 `render` する（逸脱 13。`lab/lab.sh` は変えない）。README:30 を「lab が上がっているなら先に down、打ったあとで up.sh と lab.sh up をやり直す」に。テスト 2 項目（`render` → `up` → `containerlab deploy` の順で yml が今の .in と .env のイメージになる、`down` では render しない）。注入（`inject_s1.py`）: `render の行を消す: 終了コード 1` / `up 以外でも render する: 終了コード 1` / `元に戻した: True` |
| S2 | Should fix | [data loss] `local/compose/README.md:40`、`local/compose/up.sh:6`、`spark/snmp_sinks.py:423-426` | 一度上げたあとで `.env` の `OPENSEARCH_PASSWORD` / `GF_SECURITY_ADMIN_PASSWORD` を変えて up.sh し直すと、volume の admin は古い値のまま。Spark の `_bulk` は 401 で捨てられ（`status >= 400` で `continue`）、そのあいだのログは OpenSearch に入らない | `s2probe2.py`（別のプロジェクト名で 1 回目を .env.example の値、2 回目を 2 つのパスワードを変えた .env で up）: `OpenSearch に新しい値: 401  初回の値: 200` / `Grafana に新しい値: 401  初回の値: 200` / `コンテナの環境は新しい値か: OpenSearch True  Grafana True` / 後片付け `残った volume: []  残ったコンテナ: []`。Splunk のパスワードと HEC の token は確かめていない | 直した（文書）: README:40 と up.sh:6 を「変えるなら最初の up.sh の前。あとからなら `down.sh -v` で volume ごと作り直す」に。テスト「up.sh: .env が無ければ … 最初の up.sh の前（あとからなら down.sh -v）を案内する」 |
| S3 | Should fix | [security] `local/compose/README.md:70`、`telegraf/telegraf.conf.in:245,252,266,375` | README とテスト名と逸脱 8 は「host へ出すポートは全部 127.0.0.1」と言うが、host のネットワークにいる Telegraf は 57000 / 1162 / 5140 / 8080 を全部のインターフェースで待つ。WSL の外から偽の trap・syslog・MDT を入れられる | `grep -n` の出力: `245:  service_address = ":57000"` / `252:  service_address = "udp://:1162"` / `266:  server = "udp://:5140"` / `375:  service_address = "http://:8080"`。WSL の外から届くかは未確認（U4） | 直した（文書とテスト名）: README:70・82・91、テスト名「compose の ports は全部 127.0.0.1 に縛る（… Telegraf の 4 つはこの外）」、逸脱 8。塞ぐのは design.md の 3 か所の外なので BACKLOG に足した |
| S4 | Should fix | [missing tests] `local/compose/check.sh:34-36`、`spark/snmp_sinks.py:850`、design.md:146 | Kafka の項目は Telegraf が止まっていても ok（トピックは Spark が `ensure_topics` で作る）。trap の REDIRECT が効いていなくても「すべて ok」になる（OpenSearch と Splunk は syslog とポーリングだけで 0 件を超える） | `grep -n`: `850:    made = ensure_topics(spark, args.bootstrap, all_topics(args))`（読んで確かめた。動かしてはいない） | 直した（文書）: README:58 に「trap は見ない。トピックがあっても Telegraf から届いている証拠にはならない」。項目を足すのは design.md の検証方法を変えるので BACKLOG に足し、PM に報告 |
| N1 | Nit | [correctness] `local/compose/check.sh:7`、`local/compose/lab.sh:8` | `.env` の行末の `# メモ`・CRLF・`export` を compose と違う読み方をする | `n2probe.py`: `N1 行末のメモ: [ a b c # m e m o ]` / `N1 CRLF: [ a b c \r ]` / `N1 export: [ ]` | 直さない（`.env.example` の形では起きない）。BACKLOG に足した |
| N2 | Nit | [診断] `local/compose/check.sh:43-46` | Splunk の認証の失敗が「0 件」と出て、データが来ていないのと区別できない | `n2probe.py`: `N2 401 の本文: 0 件` / `N2 成功・件数あり: ok` | 直さない（NG にはなる）。BACKLOG に足した |
| N3 | Nit | [UX] `lab/lab.sh:159,165` | `fail-main` と `heal-bgp` は手元に無い `lab failover` / `lab check` を案内する | 読んだだけ | 直さない（design.md の 3 か所の外）。BACKLOG に足した |
| N4 | Nit | [ドキュメント] `local/compose/README.md:30` | `--spines 1` だと leaf の fabric が 1 本なので、`fail-main` は切り替わらず断になる | `s1probe.py`: `N4: leaf-01 の fabric の相手: ['dc1-spine-01']` | 直した: README:30 に見え方の違いを書いた |
| N5 | Nit | [設計整合性] design.md の :57 / :58 / :59 / :75 / :97 / :100 / :109 / :146 / :153 / :159（リスク 1） / :166 | design.md が逸脱 1・3・5・6・12 と S4 に追いついていない。特に :153 は `compose_` を見るので、volume が残っていても「消えた」と判断する | 読んだだけ（逸脱の各項目に根拠） | design.md は PM の正本なので直さず、PM に報告 |
| U1 | 未確認 | [runtime] `local/compose/prometheus.yml` | `out_of_order_time_window` が無いので、spark-http の追い付きで逆順に届いたサンプルが 400 で捨てられるかもしれない | 未再現（Spark を落として上げ直す通しが要る） | BACKLOG に足した |
| U2 | 未確認 | [runtime] `local/compose/compose.yaml` の `splunk-etc` | volume があると、作り直したイメージのアプリが `/opt/splunk/etc` に写らないかもしれない | 未再現（Splunk のイメージは amd64 だけで、Mac では動かしていない） | BACKLOG に足した |
| U3 | 未確認 | [運用] `local/compose/README.md:105` | `wsl --shutdown` のあと trap の REDIRECT が消える | 読んだだけ（iptables の規則は再起動で消える） | 直した: README:105 に「`local/compose/lab.sh forward` で張り直す」 |
| U4 | 未確認 | [security] S3 と同じ | WSL の mirrored モードで LAN から Telegraf の 4 つに届くか | 未再現（WSL 未実施） | S3 の BACKLOG に含めた |

反対弁護人が「成立しない」とした 12 件（sudo の secure_path、`TELEGRAF_LOCAL` の子への引き継ぎ、Kafka の `localhost` の ::1、`.env` が git に入る、など）は読んだだけで、再現していない。`.env` が git に入らないこと、`TELEGRAF_LOCAL` を渡した `forward` が REDIRECT を入れることはテストで見ている。

#### 「問題なし」とした観点

| 観点 | 根拠（実行したもの） |
|---|---|
| 設計整合性: `lab/lab.sh` の変更が design.md の 3 か所（`local_telegraf` と `hint` / `failover` / `forward` の分岐、`pull` の `REGISTRY`）とモードだけ | `git diff --stat`: `lab/lab.sh \| 24 ++++++++++++++++++-------`。中身は読んだだけ |
| `oss/compose` を変えていない | `git status --porcelain -uall` に `oss/` が無い |
| API compatibility: lab の EC2 の挙動（`REGISTRY` ありの `pull`、`TELEGRAF_IMAGE` の `forward`、stream の分岐） | テスト 3 項目と注入（REGISTRY の分岐を外す: 終了コード 1） |
| security: パスワードを `ps` に出さない、シェルの `REGISTRY` / `AWS_REGION` を lab へ渡さない、compose の `ports` は 127.0.0.1 | テストと注入（-u: 終了コード 1、sudo -E: 終了コード 1、0.0.0.0: 終了コード 1） |
| runtime: check.sh がどこにも繋がらなくても途中で落ちず 6 項目とも NG を出す | テスト（「どこにも繋がらなくても set -e で途中で落ちず …」）と `ortrue.sh`（bash 3.2 と 5.1） |
| Spark の checkpoint の volume に spark ユーザーが書ける | 上の「Spark の checkpoint の書き込み」 |

#### ジンテーゼ

- 部分的な真実
  - S3 / U4: 「127.0.0.1 に縛る」と言えるのは compose の `ports` だけ。
  - S4: `check.sh` の「すべて ok」は、ポーリングと syslog の経路が通った証拠。trap の経路と Telegraf が動いている証拠ではない。
  - S2: `.env` のパスワードは初回の前にしか変えられない。
  - 注入の 9/10: テストが縛っているのは注入した 12 件（10 件と S1 の 2 件）の範囲だけ。S2・S3・N1・N2 の挙動はテストで縛っていない（S2 と S3 は文書の直し）。
- 結論の言い直し
  - Mac で確かめたのは次の 4 つまで。
    - `docker compose config`
    - 3 つの build
    - 偽のコマンドを使った 75 項目
    - `ops/check.sh`
  - 「手元で通しが動く」は、WSL の通し（ステップ 7）が済むまで条件付き。
  - 台数とイメージの変更には、`lab.sh up` が毎回 render して追従する。
  - パスワードの変更は、最初の `up.sh` の前か `down.sh -v` のあとに限る。
- 残リスク
  - U1、U2。
  - Splunk のパスワードと HEC の token をあとから変えたときの挙動（未確認）。
  - S3 の届く範囲（U4）。
  - WSL 未実施。
  - N1〜N3。
- アンチテーゼ前との差分
  - 前の結論は「compose の構成は design.md どおりで、ポートは全部 127.0.0.1、`check.sh` が通れば通しが動いている」だった。
  - 今の結論は 3 点を限定した。
    - 127.0.0.1 なのは compose の `ports` だけ（Telegraf の 4 つは別）。
    - `check.sh` の ok は trap の経路を含まない。
    - lab の台数とイメージを変えたら `lab.sh up` が追従し、パスワードは初回の前だけ変えられる。
