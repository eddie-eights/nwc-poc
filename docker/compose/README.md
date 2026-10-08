# 手元の docker compose（WSL2）

WSL2 の中だけで、lab（containerlab の SR Linux）→ gnmic（gNMI）・Telegraf（trap）→ Kafka → Spark → OpenSearch / Prometheus / Splunk → Grafana まで一周させる構成。AWS は使わない。SR Linux に障害を入れると、Grafana のダッシュボードと Splunk の検索にそれが出る。

設計は [docs/cycles/006-local-compose/design.md](../../docs/cycles/006-local-compose/design.md)。AWS のマネージド版・OSS 版とは別物で、SNS・アラートの通知・Neptune・Nautobot・ワークフロー・エージェントは無い（Splunk の保存済みサーチは走るが、通知先が無い。Grafana は `ALERTS_TOPIC_ARN` が無いのでアラートルールを入れず、データソースとダッシュボードだけ）。

## 前提

WSL2 の Ubuntu に次を入れる。

| もの | 理由 |
|---|---|
| Docker Engine（docker-ce と docker-compose-plugin。Docker Desktop の WSL 統合は使わない） | gnmic・Telegraf（と syslog-ng・GoFlow2）は `network_mode: host` で lab の管理ネット（203.0.113.0/24）に届き、gnmic は SR Linux の gNMI を購読し、Telegraf は `iptables` の REDIRECT で trap を受ける。Docker Desktop はエンジンが別の distro にいるので、host が Ubuntu のネットワークにならない |
| containerlab | lab（SR Linux 6 台 + TRex 1 台）。`app/containerlab/lab.sh` が `sudo` で呼ぶ |
| `snmp`（snmpwalk / snmptrap）、`iptables`、`python3` | `lab check` / `trap-test`、trap の REDIRECT、`app/containerlab/lab_topology.py` |
| `.wslconfig` の `memory=20GB` 以上 | 見積もりは 16〜19 GB（SR Linux 6 台、Kafka 3 台、Splunk、OpenSearch、Spark 2 つ）。`check.sh` が 20 GB 未満なら注意を出す |

docker-ce は [docs/setup.md](../../docs/setup.md) の「WSL2（Ubuntu）」の手順で入れる（そこの `binfmt` の行は要らない）。残りは次で入れる。

```bash
sudo apt-get install -y docker-compose-plugin snmp iptables python3
```

```bash
bash -c "$(curl -sL https://get.containerlab.dev)"
```

QEMU（binfmt）は要らない。build するイメージ（Telegraf、gnmic、Grafana、Spark、Splunk）は WSL の x86_64 のまま作る。SR Linux と multitool も ghcr.io の amd64 を取る。TRex（Docker Hub の `trexcisco/trex`）は amd64 しか無いが、WSL の x86_64 ならそのまま動く。

足りないメモリは lab を減らして空ける。`python3 app/containerlab/gen_lab.py --leaves 2 --spines 1` で SR Linux が 5 台になる（`leaves` は 2 の倍数で 2 以上、`spines` は 1 以上）。戻すのは `--leaves 2 --spines 2`。これは git に入っている lab の定義（`app/containerlab/splab.clab.yml.in` と `app/containerlab/srlinux/*.cli`）を書き換える。lab が上がっているなら先に `docker/compose/lab.sh down` し、打ったあとで `lab.sh up` と `up.sh`（gnmic の購読先と Spark の device map が変わる）をこの順でやり直す（`lab.sh up` は毎回 `app/containerlab/splab.clab.yml` を作り直してから deploy する）。spine が 1 台だと leaf の fabric は 1 本だけなので、`fail-main` は切り替わらずに断になる（`failover` の「切替 OK」は出ない。linkDown の trap と Grafana の DOWN は 6 台のときと同じに出る）。

## 手順

リポジトリの直下で打つ。

```bash
cp docker/compose/.env.example docker/compose/.env
```

`.env` の値は手元だけの試し用（パスワード・HEC の token・lab のイメージ）。パスワードと token を変えるなら、最初の `up.sh` の前にここで変える。OpenSearch と Grafana は初回の起動で admin のパスワードを volume に書き込むので、あとから `.env` だけ変えても古い値のまま（新しい値で 401。Spark が OpenSearch へ送るログは 401 で捨てられ、`check.sh` も NG になる。Splunk のパスワードと HEC の token が同じかは未確認）。あとから変えるなら `docker/compose/down.sh -v` で volume ごと消してから上げ直す。`.env` は git に入らない。`check.sh` と `lab.sh` は `.env` の値を `docker compose --env-file .env config --environment` で compose 自身に読ませるので、クォート、`$` の展開、` # メモ` の扱いは compose と同じになる（値に `$` をそのまま入れるなら `$$` と書くか `'…'` で囲む）。シェルに同じ名前の環境変数があればそちらが勝つのも compose と同じ。値は 1 行に限る。書式の誤りで compose が読めないと、2 つとも値を出さずに `docker compose が .env を読めない` で止まる（理由は `docker/compose` で `docker compose --env-file .env config --environment >/dev/null` を打つと出る。値の一部が出ることがある）。`config --environment` が無い古い compose でも同じく止まる（どの版から有るかは未確認。v5.1.3 には有る）。

```bash
docker/compose/lab.sh up
```

lab を上げる（`sudo` のパスワードを聞かれる）。compose より先に上げる（Telegraf は lab の管理ネットの GW `203.0.113.1` で待つので、containerlab がそのアドレスを付ける bridge が先に要る。下の `up.sh`）。最後に `compose の Telegraf へ: trap 162/udp を 1162/udp へ向けた` が出ればよい（Telegraf がまだ無くても iptables の規則は入る）。サブコマンドは `app/containerlab/lab.sh` と同じ（`check` / `fail-main` / `heal-main` / `trap-test` / `down` など）。障害のあとの案内（「戻すのは …」）もこのラッパーの打ち方で出る。このラッパーは `.env` の `SRLINUX_IMAGE` / `MULTITOOL_IMAGE` / `TREX_IMAGE` と `TELEGRAF_LOCAL=1`、案内に出す自分のパス `LAB_CMD` の 5 つだけを渡すので、シェルに `REGISTRY` や `AWS_REGION` があっても ECR や SSM へは行かない。

```bash
docker/compose/up.sh
```

`app/containerlab/lab_topology.py` から gnmic の gNMI の購読先と Spark の device map を作り、`docker compose up -d --build` する。Spark の 2 つは送り先（`spark-splunk` は Splunk、`spark-http` は OpenSearch と Prometheus）が `healthy` になるまで起こさない（先に起きると送り先への POST が落ちてジョブが終わる）ので、`up.sh` は Splunk が `healthy` になるまでの 2〜3 分戻らない。`docker compose -f docker/compose/compose.yaml ps` で 14 サービスが `running` になればよい。送り先が `healthy` にならなければ `up.sh` は `dependency failed to start: container nwc-local-splunk-1 is unhealthy` のように止まり、Spark は `Created` のまま残る。`logs splunk`（か `opensearch` / `prometheus`）で理由を見て、直してから `up.sh` を打ち直す。`docker compose` を直に打つと `GNMI_TARGETS` が空になり、gnmic が起動の検査で止まるので、上げ直しも `up.sh` から（`docker/compose/up.sh telegraf` で Telegraf だけ、`docker/compose/up.sh gnmic` で gnmic だけ）。Telegraf・syslog-ng・GoFlow2 の受け口（下の「ぶつかりやすいポート」）は、host に `203.0.113.1` があればそこだけで待つ（`TELEGRAF_BIND`。`ip -o -4 addr show` で見る）。lab より先に打つと `WARNING: lab の管理ネット（203.0.113.1）がまだ無いので…` が出て、WSL の全部のインターフェースで待つ。そのときは `lab.sh up` のあとに `docker/compose/up.sh telegraf syslog-ng goflow2` で `203.0.113.1` だけに直す。

続けて `docker/compose/lab.sh check` で BGP・IS-IS、TRex の回線（各 leaf の `ethernet-1/3` と TRex の `eth1`〜`eth4`）、SNMP の応答を見る。

```bash
docker/compose/check.sh
```

2〜3 分待ってから打つ。Spark の 2 つ（`spark-splunk` / `spark-http`）が `running` か、Kafka のトピックとメッセージ数、Prometheus の `snmp_interface_oper_up`、OpenSearch の `snmp-logs`、Splunk の `sourcetype=netops:*`、Grafana のデータソース 2 つと Prometheus の health、Telegraf の health（`up.sh` と同じく `203.0.113.1` があればそこ、無ければ `127.0.0.1` の `HEALTH_PORT`）、syslog-ng が 5140/udp で待っているか、GoFlow2 の `/metrics`（8081）を見て、NG が無ければ `すべて ok`。Kafka のトピックは Spark が起動のときに作るので、gnmic と Telegraf から届いているかはメッセージ数（Kafbat UI の `messagesCount`）で見る。`metrics`（gnmic の IF のカウンター）と `gnmi`（gnmic の IF・BGP・IS-IS の状態。購読した直後に今の値を 1 回送る）が 0 件なら NG（`docker compose -f docker/compose/compose.yaml logs gnmic`）。trap の `traps` は障害を入れるまで来ないので、0 件でも NG にせず `注意` を出す（下の `fail-main` か `trap-test` のあとに打ち直すと `ok` になる）。1 つでも NG なら非 0 で終わるので、`docker compose -f docker/compose/compose.yaml logs <サービス>` で見る。Spark が `exited` なら `restart: on-failure:5` を使い切って止まっている（`logs spark-splunk` などで理由を見て、直してから `docker/compose/up.sh spark-splunk`）。`created` なら送り先が `healthy` になっていない。

障害を入れて見る:

```bash
docker/compose/lab.sh fail-main
```

数分で Grafana の `metrics` ダッシュボードの `dc1-a-leaf-01 ethernet-1/1` が DOWN、`logs` ダッシュボードと Splunk（`index=* source="telegraf:snmp_trap"`）に linkDown の trap と syslog が出る。戻すのは `docker/compose/lab.sh heal-main`。`docker/compose/lab.sh trap-test` は link 以外の trap を 1 通送る（Splunk の保存済みサーチ `netops_trap` が次の実行で 1 件）。

## 見る場所

下の画面のポートは全部 `127.0.0.1` に出す（WSL の外の LAN からは届かない）。host のネットワークにいる Telegraf・syslog-ng・GoFlow2 の受け口（下の「ぶつかりやすいポート」）は lab の管理ネットの GW `203.0.113.1` だけで待つ（lab より先に `up.sh` を打ったときだけ全部のインターフェースで、`WARNING` が出る）。Windows のブラウザから同じ URL で開けるかは WSL の localhost 転送（`.wslconfig` の `localhostForwarding`、既定で有効）次第で、未確認。

| 画面 | URL | ログイン |
|---|---|---|
| Grafana | http://localhost:3000 | `admin` / `.env` の `GF_SECURITY_ADMIN_PASSWORD` |
| Splunk | http://localhost:8000 | `admin` / `.env` の `SPLUNK_PASSWORD` |
| Kafbat UI（Kafka） | http://localhost:18080 | なし |
| Prometheus | http://localhost:9090 | なし |
| OpenSearch（API） | http://localhost:9200 | `admin` / `.env` の `OPENSEARCH_PASSWORD` |

## ぶつかりやすいポート

Telegraf・syslog-ng・GoFlow2 は host のネットワークにいるので、host の次のポートを開ける。待つのは lab の管理ネットの GW `203.0.113.1` だけ（`127.0.0.1` では待たない。lab の外から偽の trap や syslog を入れられないように）。lab が無いときに `up.sh` を打つと全部のインターフェースで待つ（`WARNING` が出る。WSL の外から届くかは WSL のネットワークのモード次第で、未確認）。ほかのプロセスが使っていると Telegraf が起動しない（`docker compose -f docker/compose/compose.yaml logs telegraf`）。`203.0.113.1` が無いとき（lab を `down` したまま）に Telegraf が起こし直されても `bind: cannot assign requested address` で落ちる。telegraf・syslog-ng と spark は `restart: on-failure:5`（goflow2 は Kafka が上がるまで落ちるので `on-failure:10`）なので、5 回起こし直しても落ちるなら止まったままになる（Docker は回数を戻さない。spark は `check.sh` の「Spark: … が動いている」が NG になる）。`docker compose -f docker/compose/compose.yaml ps -a` で `Exited` なら `logs telegraf` で理由を見て、直してから `docker/compose/up.sh telegraf` で起こす（`check.sh` の「Telegraf: health が 200」も NG になる）。lab を `down` / `up` で作り直したあとは、Telegraf が動いていても `docker compose -f docker/compose/compose.yaml restart telegraf syslog-ng goflow2` で待ち直させる（作り直した bridge で前の待ち受けが受け続けるかは未確認）。

health のポートは `.env` の `HEALTH_PORT` で変えられる（変えたら `docker/compose/up.sh telegraf`。`check.sh` も `HEALTH_PORT` に打つ）。trap と syslog は lab の `app/containerlab/lab.sh`（`TRAP_PORT` / `LOG_PORT`）と SR Linux の syslog の送り先に揃えてあるので変えられない。ぶつかったら相手のプロセスを止める。

| ポート | 用途 |
|---|---|
| 8080/tcp | Telegraf の health（`.env` の `HEALTH_PORT`） |
| 1162/udp | SNMP trap（機器は 162 に送り、`lab.sh forward` が 1162 へ向ける） |
| 5140/udp・5140/tcp | syslog（syslog-ng。tcp は ECS の NLB のヘルスチェックと同じ口） |
| 2055/udp | NetFlow（GoFlow2。lab からは来ない。`uv run python tools/netflow_send.py 127.0.0.1 2055` で 1 つ送る） |
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

WSL を落とすと（`wsl --shutdown` など）trap の REDIRECT が消える。lab が上がったままなら `docker/compose/lab.sh forward` で張り直す（`lab.sh up` も最後に張り直す）。

lab の `down` は trap の REDIRECT（目印 `nwc-lab-telegraf`）を残す。次の `lab.sh up` が消してから張り直すので残っていても害は無いが、消すなら次を打つ。

```bash
sudo iptables -t nat -D PREROUTING -s 203.0.113.0/24 -d 203.0.113.1 -p udp --dport 162 -m comment --comment nwc-lab-telegraf -j REDIRECT --to-ports 1162
```

containerlab が `app/containerlab/clab-splab/`（root の持ち物）を作る。git の無視の対象（`.gitignore` の `app/containerlab/clab-*/`）なので `git status` には出ない。消すなら `sudo rm -rf app/containerlab/clab-splab`。
