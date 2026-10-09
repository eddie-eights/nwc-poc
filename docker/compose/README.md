# 手元の docker compose（WSL2）

WSL2 の中だけで、lab（containerlab の SR Linux）→ gnmic（gNMI）・Telegraf（trap）→ Kafka → Spark → OpenSearch / Prometheus / Splunk → Grafana まで一周させる構成。
AWS は使わない。SR Linux に障害を入れると、Grafana のダッシュボードと Splunk の検索にそれが出る。

設計は [docs/cycles/006-local-compose/design.md](../../docs/cycles/006-local-compose/design.md)。
AWS のマネージド版・OSS 版とは別物で、SNS・アラートの通知・Neptune・Nautobot・ワークフロー・エージェントは無い。

- Splunk の保存済みサーチは走るが、通知先が無い。
- Grafana は `ALERTS_TOPIC_ARN` が無いのでアラートルールを入れず、データソースとダッシュボードだけ。

## 前提

WSL2 の Ubuntu に次を入れる。

| もの | 理由 |
|---|---|
| Docker Engine（docker-ce と docker-compose-plugin。Docker Desktop の WSL 統合は使わない） | gnmic・Telegraf（と syslog-ng・GoFlow2）は `network_mode: host` で lab の管理ネット（203.0.113.0/24）に届く（表の下）。Docker Desktop はエンジンが別の distro にいるので、host が Ubuntu のネットワークにならない |
| containerlab | lab（SR Linux 6 台 + TRex 1 台）。`app/containerlab/lab.sh` が `sudo` で呼ぶ |
| `snmp`（snmpwalk / snmptrap）、`iptables`、`python3` | `lab check` / `trap-test`、trap の REDIRECT、`app/containerlab/lab_topology.py` |
| `.wslconfig` の `memory=20GB` 以上 | 見積もりは 16〜19 GB（SR Linux 6 台、Kafka 3 台、Splunk、OpenSearch、Spark 2 つ）。`check.sh` が 20 GB 未満なら注意を出す |

- Docker Engine: host のネットワークで、gnmic は SR Linux の gNMI を購読し、Telegraf は `iptables` の REDIRECT で trap を受ける。

docker-ce は [docs/setup.md](../../docs/setup.md) の「WSL2（Ubuntu）」の手順で入れる（そこの `binfmt` の行は要らない）。残りは次で入れる。

```bash
sudo apt-get install -y docker-compose-plugin snmp iptables python3
```

```bash
bash -c "$(curl -sL https://get.containerlab.dev)"
```

QEMU（binfmt）は要らない。build するイメージ（Telegraf、gnmic、Grafana、Spark、Splunk）は WSL の x86_64 のまま作る。
SR Linux も ghcr.io の amd64 を取る。TRex（Docker Hub の `trexcisco/trex`）は amd64 しか無いが、WSL の x86_64 ならそのまま動く。

足りないメモリは lab を減らして空ける。

- `python3 app/containerlab/gen_lab.py --leaves 2 --spines 1` で SR Linux が 5 台になる（`leaves` は 2 の倍数で 2 以上、`spines` は 1 以上）。戻すのは `--leaves 2 --spines 2`。
- これは git に入っている lab の定義（`app/containerlab/splab.clab.yml.in` と `app/containerlab/srlinux/*.cli`）を書き換える。
- lab が上がっているなら先に `docker/compose/lab.sh down` する。
  打ったあとで `lab.sh up` と `up.sh`（gnmic の購読先と Spark の device map が変わる）をこの順でやり直す。
  - `lab.sh up` は毎回 `app/containerlab/splab.clab.yml` を作り直してから deploy する。
- spine が 1 台だと leaf の fabric は 1 本だけなので、`fail-main` は切り替わらずに断になる。
  - `failover` の「切替 OK」は出ない。linkDown の trap と Grafana の DOWN は 6 台のときと同じに出る。

## 手順

リポジトリの直下で打つ。

```bash
cp docker/compose/.env.example docker/compose/.env
```

`.env` の値は手元だけの試し用（パスワード・HEC の token・lab のイメージ）。`.env` は git に入らない。

- パスワードと token を変えるなら、最初の `up.sh` の前にここで変える。
- OpenSearch と Grafana は初回の起動で admin のパスワードを volume に書き込むので、あとから `.env` だけ変えても古い値のまま。
  - 新しい値で 401。Spark が OpenSearch へ送るログは 401 で捨てられ、`check.sh` も NG になる。
  - Splunk のパスワードと HEC の token が同じかは未確認。
  - あとから変えるなら `docker/compose/down.sh -v` で volume ごと消してから上げ直す。
- `check.sh` と `lab.sh` は `.env` の値を `docker compose --env-file .env config --environment` で compose 自身に読ませる。
  - クォート、`$` の展開、` # メモ` の扱いは compose と同じになる（値に `$` をそのまま入れるなら `$$` と書くか `'…'` で囲む）。
  - シェルに同じ名前の環境変数があればそちらが勝つのも compose と同じ。値は 1 行に限る。
  - 書式の誤りで compose が読めないと、2 つとも値を出さずに `docker compose が .env を読めない` で止まる。
    理由は `docker/compose` で `docker compose --env-file .env config --environment >/dev/null` を打つと出る（値の一部が出ることがある）。
  - `config --environment` が無い古い compose でも同じく止まる（どの版から有るかは未確認。v5.1.3 には有る）。

```bash
docker/compose/lab.sh up
```

lab を上げる（`sudo` のパスワードを聞かれる）。

- compose より先に上げる。
  Telegraf は lab の管理ネットの GW `203.0.113.1` で待つので、containerlab がそのアドレスを付ける bridge が先に要る（下の `up.sh`）。
- 最後に `compose の Telegraf へ: trap 162/udp を 1162/udp へ向けた` が出ればよい（Telegraf がまだ無くても iptables の規則は入る）。
- サブコマンドは `app/containerlab/lab.sh` と同じ（`check` / `fail-main` / `heal-main` / `trap-test` / `down` など）。
  障害のあとの案内（「戻すのは …」）もこのラッパーの打ち方で出る。
- このラッパーが渡すのは次の 4 つだけ。
  `.env` の `SRLINUX_IMAGE` / `TREX_IMAGE` と `TELEGRAF_LOCAL=1`、案内に出す自分のパス `LAB_CMD`。
  - そのため、シェルに `REGISTRY` や `AWS_REGION` があっても ECR や SSM へは行かない。
  - `.env` にイメージの 2 つが無いと `up`（と `render`）は止まる。`down` / `check` などはイメージを見ないので打てる。
    011 より前の `.env` には `TREX_IMAGE` が無いので、`.env.example` から写す。

```bash
docker/compose/up.sh
```

`app/containerlab/lab_topology.py` から gnmic の gNMI の購読先と Spark の device map を作り、`docker compose up -d --build` する。

- Spark の 2 つは送り先（`spark-splunk` は Splunk、`spark-http` は OpenSearch と Prometheus）が `healthy` になるまで起こさない。
  - 先に起きると送り先への POST が落ちてジョブが終わる。
  - そのため `up.sh` は Splunk が `healthy` になるまでの 2〜3 分戻らない。
- `docker compose -f docker/compose/compose.yaml ps` で 14 サービスが `running` になればよい。
- 送り先が `healthy` にならなければ、`up.sh` は次のように止まり、Spark は `Created` のまま残る。
  `dependency failed to start: container nwc-local-splunk-1 is unhealthy`
  - `logs splunk`（か `opensearch` / `prometheus`）で理由を見て、直してから `up.sh` を打ち直す。
- `docker compose` を直に打つと `GNMI_TARGETS` が空になり、gnmic が起動の検査で止まる。
  上げ直しも `up.sh` から（`docker/compose/up.sh telegraf` で Telegraf だけ、`docker/compose/up.sh gnmic` で gnmic だけ）。
- Telegraf・syslog-ng・GoFlow2 の受け口（下の「ぶつかりやすいポート」）は、host に `203.0.113.1` があればそこだけで待つ（`TELEGRAF_BIND`。`ip -o -4 addr show` で見る）。
  - lab より先に打つと `WARNING: lab の管理ネット（203.0.113.1）がまだ無いので…` が出て、WSL の全部のインターフェースで待つ。
  - そのときは `lab.sh up` のあとに `docker/compose/up.sh telegraf syslog-ng goflow2` で `203.0.113.1` だけに直す。

続けて `docker/compose/lab.sh check` で BGP・IS-IS、TRex の回線（各 leaf の `ethernet-1/3` と TRex の `eth1`〜`eth4`）、SNMP の応答を見る。

```bash
docker/compose/check.sh
```

2〜3 分待ってから打つ。次を見て、NG が無ければ `すべて ok`。

- Spark の 2 つ（`spark-splunk` / `spark-http`）が `running` か
- Kafka のトピックとメッセージ数
- Prometheus の `snmp_interface_oper_up`、OpenSearch の `snmp-logs`、Splunk の `sourcetype=nwc:*`
- Grafana のデータソース 2 つと Prometheus の health
- Telegraf の health と GoFlow2 の `/metrics`（8081）
  - 宛先は動いているコンテナの設定（`docker inspect`）から読む。Telegraf は環境の `TELEGRAF_BIND` と `HEALTH_PORT`（無ければ 8080）、GoFlow2 は引数の `-addr`。
  - bind が空なら `127.0.0.1` に打つ。`203.0.113.1` なのに host に無ければ（`up.sh` のあとに `lab.sh down` した）、打たずに NG で `lab.sh up` か `up.sh telegraf syslog-ng goflow2` を案内する。
  - コンテナが無ければ NG で `up.sh telegraf`（`up.sh goflow2`）を案内する。
- syslog-ng が 5140/udp で待っているか

Kafka のトピックは Spark が起動のときに作るので、gnmic と Telegraf から届いているかはメッセージ数（Kafbat UI の `messagesCount`）で見る。

- `metrics`（gnmic の IF のカウンター）と `gnmi`（gnmic の IF・BGP・IS-IS の状態。購読した直後に今の値を 1 回送る）が 0 件なら NG。
  見るのは `docker compose -f docker/compose/compose.yaml logs gnmic`。
- trap の `traps` は障害を入れるまで来ないので、0 件でも NG にせず `注意` を出す。
  下の `fail-main` か `trap-test` のあとに打ち直すと `ok` になる。

1 つでも NG なら非 0 で終わるので、`docker compose -f docker/compose/compose.yaml logs <サービス>` で見る。

- Spark が `exited` なら `restart: on-failure:5` を使い切って止まっている。
  `logs spark-splunk` などで理由を見て、直してから `docker/compose/up.sh spark-splunk`。
- `created` なら送り先が `healthy` になっていない。

障害を入れて見る:

```bash
docker/compose/lab.sh fail-main
```

数分で Grafana の `metrics` ダッシュボードの `dc1-a-leaf-01 ethernet-1/1` が DOWN になる。
`logs` ダッシュボードと Splunk（`index=* source="telegraf:snmp_trap"`）に linkDown の trap と syslog が出る。

- 戻すのは `docker/compose/lab.sh heal-main`。
- `docker/compose/lab.sh trap-test` は link 以外の trap を 1 通送る（Splunk の保存済みサーチ `nwc_trap` が次の実行で 1 件）。

## 見る場所

- 下の画面のポートは全部 `127.0.0.1` に出す（WSL の外の LAN からは届かない）。
- host のネットワークにいる Telegraf・syslog-ng・GoFlow2 の受け口（下の「ぶつかりやすいポート」）は lab の管理ネットの GW `203.0.113.1` だけで待つ。
  lab より先に `up.sh` を打ったときだけ全部のインターフェースで、`WARNING` が出る。
- Windows のブラウザから同じ URL で開けるかは WSL の localhost 転送（`.wslconfig` の `localhostForwarding`、既定で有効）次第で、未確認。

| 画面 | URL | ログイン |
|---|---|---|
| Grafana | http://localhost:3000 | `admin` / `.env` の `GF_SECURITY_ADMIN_PASSWORD` |
| Splunk | http://localhost:8000 | `admin` / `.env` の `SPLUNK_PASSWORD` |
| Kafbat UI（Kafka） | http://localhost:18080 | なし |
| Prometheus | http://localhost:9090 | なし |
| OpenSearch（API） | http://localhost:9200 | `admin` / `.env` の `OPENSEARCH_PASSWORD` |

**Kafka の内部トピック（`__consumer_offsets` 等）は 1 パーティション**

- `compose.yaml` の `x-kafka-env` で、既定の 50 から 1 に絞った（OSS 版の `kafka.tf` と同じ）。
- 数はトピックが最初に作られたとき（consumer group を初めて使ったとき）に決まる。Spark は consumer group を使わないので、前からある volume でもふつうはまだ作られておらず、上げ直せば 1 で作られる。
- 前の volume に 50 で出来ているかは、次の出力の PartitionCount で分かる。

  ```bash
  docker compose -f docker/compose/compose.yaml exec kafka-1 /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka-1:9092 --describe --topic __consumer_offsets
  ```

- 50 で出来ていて 1 にしたいときだけ、`docker/compose/down.sh -v` で消して上げ直す。Kafka だけでなく Splunk・OpenSearch・Grafana・Prometheus・Spark の checkpoint も全部消える。

## ぶつかりやすいポート

Telegraf・syslog-ng・GoFlow2 は host のネットワークにいるので、host の次のポートを開ける。

- 待つのは lab の管理ネットの GW `203.0.113.1` だけ。
  `127.0.0.1` では待たない（lab の外から偽の trap や syslog を入れられないように）。
- lab が無いときに `up.sh` を打つと全部のインターフェースで待つ。`WARNING` が出る。
  - WSL の外から届くかは WSL のネットワークのモード次第で、未確認。
- ほかのプロセスが使っていると Telegraf が起動しない（`docker compose -f docker/compose/compose.yaml logs telegraf`）。
- `203.0.113.1` が無いとき（lab を `down` したまま）に Telegraf が起こし直されても `bind: cannot assign requested address` で落ちる。
- telegraf・syslog-ng と spark は `restart: on-failure:5`（goflow2 は Kafka が上がるまで落ちるので `on-failure:10`）。
  5 回起こし直しても落ちるなら止まったままになる。
  - Docker は回数を戻さない。
  - spark は `check.sh` の「Spark: … が動いている」が NG になる。
- `docker compose -f docker/compose/compose.yaml ps -a` で `Exited` なら `logs telegraf` で理由を見る。
  直してから `docker/compose/up.sh telegraf` で起こす（`check.sh` の「Telegraf: health が 200」も NG になる）。
- lab を `down` / `up` で作り直したあとは、Telegraf が動いていても次で待ち直させる（作り直した bridge で前の待ち受けが受け続けるかは未確認）。
  `docker compose -f docker/compose/compose.yaml restart telegraf syslog-ng goflow2`

health のポートは `.env` の `HEALTH_PORT` で変えられる（変えたら `docker/compose/up.sh telegraf`。`check.sh` は `.env` でなく動いている Telegraf のコンテナの `HEALTH_PORT` に打つ）。
trap と syslog は lab の `app/containerlab/lab.sh`（`TRAP_PORT` / `LOG_PORT`）と SR Linux の syslog の送り先に揃えてあるので変えられない。
ぶつかったら相手のプロセスを止める。

| ポート | 用途 |
|---|---|
| 8080/tcp | Telegraf の health（`.env` の `HEALTH_PORT`） |
| 1162/udp | SNMP trap（機器は 162 に送り、`lab.sh forward` が 1162 へ向ける） |
| 5140/udp・5140/tcp | syslog（syslog-ng。tcp は ECS の NLB のヘルスチェックと同じ口） |
| 2055/udp | NetFlow（GoFlow2。lab からは来ない。`uv run python ops/netflow_send.py 127.0.0.1 2055` で 1 つ送る） |
| 6343/udp | sFlow（GoFlow2） |
| 8081/tcp | GoFlow2 の `/metrics`（8080 は Telegraf の health） |

compose の `ports` で host に出すのは 3000、8000、8089、9090、9094〜9096、9200、18080（どれも 127.0.0.1）。

## 消す

```bash
docker/compose/lab.sh down
```

```bash
docker/compose/down.sh -v
```

`-v` を付けると volume（Kafka・OpenSearch・Prometheus・Splunk・Grafana・Spark の checkpoint）も消す。付けなければデータを残して止めるだけ。

「名前を nwc に揃える（019）」より前に上げた volume には、前の名前のものが残る（下の `## 経緯`）。

- Splunk の volume には前の名前のアプリが残る（同じ版のまま上げると新しいアプリが入らない）。
- Grafana の volume にも前の名前の空のフォルダが残る。
- 019 より前に上げていたら、`docker/compose/down.sh -v` で volume ごと消してから上げ直す。

WSL を落とすと（`wsl --shutdown` など）trap の REDIRECT が消える。lab が上がったままなら `docker/compose/lab.sh forward` で張り直す（`lab.sh up` も最後に張り直す）。

lab の `down` は trap の REDIRECT（目印 `nwc-lab-telegraf`）を残す。次の `lab.sh up` が消してから張り直すので残っていても害は無いが、消すなら次を打つ。

```bash
sudo iptables -t nat -D PREROUTING -s 203.0.113.0/24 -d 203.0.113.1 -p udp --dport 162 -m comment --comment nwc-lab-telegraf -j REDIRECT --to-ports 1162
```

containerlab が `app/containerlab/clab-splab/`（root の持ち物）を作る。git の無視の対象（`.gitignore` の `app/containerlab/clab-*/`）なので `git status` には出ない。消すなら `sudo rm -rf app/containerlab/clab-splab`。

## 確かめたこと

「手元の compose の未確認を確かめる（034）」で、Mac（Apple Silicon、Docker Desktop）の docker で確かめた結果。

**Prometheus は Kafka の追い付きで時刻が戻るサンプルを 1 時間まで受ける**

- 確かめた日と版: 2026-10-10、`prom/prometheus:v3.15.0`（`compose.yaml` と同じ版）を単体で立てた。
- 同じ系列に `t=now`、`t=now-60s` の順で remote write すると、既定（`out_of_order_time_window` が 0）では 2 回目が `400` `out of order sample` で捨てられた。
  - Spark（`spark-http`）は 4xx を打ち直さずに捨てるので、lag が溜まったあとの追い付きで同じ系列の古いサンプルが後から届くと欠ける。
- `prometheus.yml` に `storage.tsdb.out_of_order_time_window: 1h` を足すと、同じ送り方で 2 回とも `204` になり、2 つとも入った。
  - 3.15.0 にはこれを変える起動の引数が無いので、`compose.yaml` でなく `prometheus.yml` に書いた。
  - Prometheus が持っている最新の時刻より 1 時間以上古いサンプルは、これまでどおり `400` で捨てる（追い付きの最大を 1 時間と見積もった）。
- 前から上げている Prometheus は設定ファイルを読み直さないので、`docker compose -f docker/compose/compose.yaml restart prometheus` で起こし直す。
  - volume は消さなくてよい（前の設定で書いた volume のまま新しい設定で起こし直し、時刻が戻るサンプルが `204` で入るのを確かめた）。
  - 確かめたのは単体の Prometheus のコンテナを同じ volume で作り直した形で、`restart` そのものは打っていない。

**Splunk のアプリを作り直しても、volume `splunk-etc` には写らない**

- 確かめた日と版: 2026-10-10、Splunk 10.4.4（`compose.yaml` の splunk を、ほかの構成とぶつからない別のプロジェクト名で 1 つだけ上げた）。
  - Mac のエミュレーション（`platform: linux/amd64`）でも立ち、`healthy` まで 2〜3 分（起動から 120〜165 秒）だった。
- `app/splunk/nwc_alerts/default/savedsearches.conf` に 1 行足してイメージを build し直し、`up -d` でコンテナを作り直した。
  - イメージの `/opt/splunk-etc/apps/nwc_alerts` には写ったが、volume の `/opt/splunk/etc/apps/nwc_alerts` は前のままだった。
  - 上流のイメージの `/sbin/updateetc.sh` は、イメージと volume の `splunk.version` が違うときだけ `/opt/splunk-etc` を volume へ写す。同じ版のまま作り直しても写らない。
- アプリ（`app/splunk/`）を変えたら、Splunk の `etc` の volume を消してから上げる。
  - `docker/compose/down.sh -v` で全部消す（Kafka・OpenSearch・Prometheus・Grafana・Spark の checkpoint も消える）。
  - Splunk だけなら次の 2 つのあとに `docker/compose/up.sh`。
    - `splunk-etc` だけを消して上げ直すと変更が写り、admin のパスワードも `.env` の値で入れ直されるのを確かめた（`splunk-var` の検索データは残る）。確かめたのは別のプロジェクト名の `docker compose up` で、`up.sh` そのものと、`spark-splunk` が動いたままの `rm` は打っていない。
    - Web で作ったサーチやダッシュボード、`local/` の設定も `splunk-etc` と一緒に消える。
    - Splunk が `healthy` に戻るまで（2〜3 分）`spark-splunk` は HEC に書けない。止まっていたら `up.sh`（引数なし）でまとめて上げ直す。

    ```bash
    docker compose -f docker/compose/compose.yaml rm -s -f splunk
    ```

    ```bash
    docker volume rm nwc-local_splunk-etc
    ```

**`check.sh` の Splunk の判定は本物の応答と合う**

上と同じ Splunk 10.4.4 に、`check.sh` と同じ `curl`（`/services/search/jobs/export`、`output_mode=json`）を打った本文に、`check.sh` の判定の式を当てた。`check.sh` は変えていない。

| 打ち方 | HTTP | 本文 | 判定 |
|---|---|---|---|
| パスワード違い | 401 | `{"messages":[{"type":"ERROR","text":"Unauthorized"}]}` | `NG … ERROR Unauthorized` |
| `check.sh` の検索（まだ何も入っていない） | 200 | `{"preview":false,"offset":0,"lastrow":true,"result":{"count":"0"}}` | `NG … 0 件` |
| `index=_internal … \| head 1 \| stats count` | 200 | 上と同じ形で `"count":"1"` | `ok` |
| 知らないコマンド（`\| nosuchcmd`） | 400 | `{"messages":[{"type":"FATAL","text":"Unknown search command 'nosuchcmd'."}]}` | `NG … FATAL Unknown search command 'nosuchcmd'.` |
| `eval` の引数の誤りや、無い lookup | 200 | 空 | `NG … 読めない応答: 空` |

- `result` と `messages` の `ERROR` が 1 つの応答に混ざる形は、試した打ち方（失敗する subsearch、無い index への `collect` など）では出なかった。混ざったときの判定（`ERROR` を理由に NG）はテストの偽の応答でだけ確かめている。

## 経緯

- 2026-10-09（019）: Splunk のアプリの名前を `nwc_alerts` に揃えた（「名前を nwc に揃える（019）」）。
