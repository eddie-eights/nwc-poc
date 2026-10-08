# 手元の docker compose（WSL2）

WSL2 の中だけで、lab（containerlab の SR Linux）→ Telegraf → Kafka → Spark → OpenSearch / Prometheus / Splunk → Grafana まで一周させる構成。AWS は使わない。SR Linux に障害を入れると、Grafana のダッシュボードと Splunk の検索にそれが出る。

設計は [docs/cycles/006-local-compose/design.md](../../docs/cycles/006-local-compose/design.md)。AWS のマネージド版・OSS 版とは別物で、SNS・アラートの通知・Neptune・Nautobot・ワークフロー・エージェントは無い（Splunk の保存済みサーチは走るが、通知先が無い。Grafana は `ALERTS_TOPIC_ARN` が無いのでアラートルールを入れず、データソースとダッシュボードだけ）。

## 前提

WSL2 の Ubuntu に次を入れる。

| もの | 理由 |
|---|---|
| Docker Engine（docker-ce と docker-compose-plugin。Docker Desktop の WSL 統合は使わない） | Telegraf は `network_mode: host` で lab の管理ネット（203.0.113.0/24）に届き、`iptables` の REDIRECT で trap を受ける。Docker Desktop はエンジンが別の distro にいるので、host が Ubuntu のネットワークにならない |
| containerlab | lab（SR Linux 6 台 + VM 2 台）。`app/containerlab/lab.sh` が `sudo` で呼ぶ |
| `snmp`（snmpwalk / snmptrap）、`iptables`、`python3` | `lab check` / `trap-test`、trap の REDIRECT、`app/containerlab/lab_topology.py` |
| `.wslconfig` の `memory=20GB` 以上 | 見積もりは 16〜19 GB（SR Linux 6 台、Kafka 3 台、Splunk、OpenSearch、Spark 2 つ）。`check.sh` が 20 GB 未満なら注意を出す |

docker-ce は [docs/setup.md](../../docs/setup.md) の「WSL2（Ubuntu）」の手順で入れる（そこの `binfmt` の行は要らない）。残りは次で入れる。

```bash
sudo apt-get install -y docker-compose-plugin snmp iptables python3
```

```bash
bash -c "$(curl -sL https://get.containerlab.dev)"
```

QEMU（binfmt）は要らない。build するイメージ（Telegraf、Grafana、Spark、Splunk）は WSL の x86_64 のまま作る。SR Linux と multitool も ghcr.io の amd64 を取る。

足りないメモリは lab を減らして空ける。`python3 app/containerlab/gen_lab.py --leaves 2 --spines 1` で 5 台になる（`leaves` は 2 の倍数で 2 以上、`spines` は 1 以上）。戻すのは `--leaves 2 --spines 2`。これは git に入っている lab の定義（`app/containerlab/splab.clab.yml.in` と `app/containerlab/srlinux/*.cli`）を書き換える。lab が上がっているなら先に `docker/compose/lab.sh down` し、打ったあとで `up.sh`（Telegraf のポーリング先と Spark の device map が変わる）と `lab.sh up` をやり直す（`lab.sh up` は毎回 `app/containerlab/splab.clab.yml` を作り直してから deploy する）。spine が 1 台だと leaf の fabric は 1 本だけなので、`fail-main` は切り替わらずに断になる（`failover` の「切替 OK」と VM の疎通は出ない。linkDown の trap と Grafana の DOWN は 6 台のときと同じに出る）。

## 手順

リポジトリの直下で打つ。

```bash
cp docker/compose/.env.example docker/compose/.env
```

`.env` の値は手元だけの試し用（パスワード・HEC の token・lab のイメージ）。パスワードと token を変えるなら、最初の `up.sh` の前にここで変える。OpenSearch と Grafana は初回の起動で admin のパスワードを volume に書き込むので、あとから `.env` だけ変えても古い値のまま（新しい値で 401。Spark が OpenSearch へ送るログは 401 で捨てられ、`check.sh` も NG になる。Splunk のパスワードと HEC の token が同じかは未確認）。あとから変えるなら `docker/compose/down.sh -v` で volume ごと消してから上げ直す。`.env` は git に入らない。`check.sh` と `lab.sh` も `.env` を compose と同じように読む（行頭の `export `、CRLF、クォート無しの値の後ろの ` # メモ` は落とす）。

```bash
docker/compose/up.sh
```

`app/containerlab/lab_topology.py` から Telegraf のポーリング先・gNMI の購読先・Spark の device map を作り、`docker compose up -d --build` する。Splunk が `healthy` になるまで 2〜3 分。`docker compose -f docker/compose/compose.yaml ps` で 11 サービスが `running` になればよい。`docker compose` を直に打つと `SNMP_AGENTS` が空になり、Telegraf が起動の検査で止まるので、上げ直しも `up.sh` から（`docker/compose/up.sh telegraf` で Telegraf だけ）。

```bash
docker/compose/lab.sh up
```

lab を上げる（`sudo` のパスワードを聞かれる）。最後に `compose の Telegraf へ: trap 162/udp を 1162/udp へ向けた` が出ればよい。続けて `docker/compose/lab.sh check` で BGP・IS-IS・EVPN、VM の LAG と ping、SNMP の応答を見る（bond0 が無いと出たら WSL のカーネルに bonding が無い。`uname -r` と `zcat /proc/config.gz | grep BONDING` を控えておく）。サブコマンドは `app/containerlab/lab.sh` と同じ（`check` / `fail-main` / `heal-main` / `trap-test` / `down` など）。このラッパーは `.env` の `SRLINUX_IMAGE` / `MULTITOOL_IMAGE` と `TELEGRAF_LOCAL=1` の 3 つだけを渡すので、シェルに `REGISTRY` や `AWS_REGION` があっても ECR や SSM へは行かない。

```bash
docker/compose/check.sh
```

2〜3 分待ってから打つ。Kafka のトピックとメッセージ数、Prometheus の `snmp_interface_ifOperStatus`、OpenSearch の `snmp-logs`、Splunk の `sourcetype=netops:*`、Grafana のデータソース 2 つと Prometheus の health を見て、NG が無ければ `すべて ok`。Kafka のトピックは Spark が起動のときに作るので、Telegraf から届いているかはメッセージ数（Kafbat UI の `messagesCount`）で見る。`metrics` が 0 件なら NG。trap の `traps` は障害を入れるまで来ないので、0 件でも NG にせず `注意` を出す（下の `fail-main` か `trap-test` のあとに打ち直すと `ok` になる）。1 つでも NG なら非 0 で終わるので、`docker compose -f docker/compose/compose.yaml logs <サービス>` で見る。

障害を入れて見る:

```bash
docker/compose/lab.sh fail-main
```

数分で Grafana の `metrics` ダッシュボードの `dc1-leaf-01 ethernet-1/1` が DOWN、`logs` ダッシュボードと Splunk（`index=* source="telegraf:snmp_trap"`）に linkDown の trap と syslog が出る。戻すのは `docker/compose/lab.sh heal-main`。`docker/compose/lab.sh trap-test` は link 以外の trap を 1 通送る（Splunk の保存済みサーチ `netops_trap` が次の実行で 1 件）。

## 見る場所

下の画面のポートは全部 `127.0.0.1` に出す（WSL の外の LAN からは届かない）。host のネットワークにいる Telegraf の 4 つ（下の「ぶつかりやすいポート」）だけは host の全部のインターフェースで待つ。Windows のブラウザから同じ URL で開けるかは WSL の localhost 転送（`.wslconfig` の `localhostForwarding`、既定で有効）次第で、未確認。

| 画面 | URL | ログイン |
|---|---|---|
| Grafana | http://localhost:3000 | `admin` / `.env` の `GF_SECURITY_ADMIN_PASSWORD` |
| Splunk | http://localhost:8000 | `admin` / `.env` の `SPLUNK_PASSWORD` |
| Kafbat UI（Kafka） | http://localhost:18080 | なし |
| Prometheus | http://localhost:9090 | なし |
| OpenSearch（API） | http://localhost:9200 | `admin` / `.env` の `OPENSEARCH_PASSWORD` |

## ぶつかりやすいポート

Telegraf は host のネットワークにいるので、host の次のポートを開ける（`127.0.0.1` ではなく全部のインターフェース。WSL の外から届くかは WSL のネットワークのモード次第で、未確認）。ほかのプロセスが使っていると Telegraf が起動しない（`docker compose -f docker/compose/compose.yaml logs telegraf`）。telegraf と spark は `restart: on-failure:5` なので、5 回起こし直しても落ちるなら止まったままになる。`docker compose -f docker/compose/compose.yaml ps -a` で `Exited` なら `logs telegraf` で理由を見て、直してから `docker/compose/up.sh` で起こす。ポートは `app/telegraf/telegraf.sh` と `telegraf.conf.in` の固定値で、環境変数では変えられない。ぶつかったら相手のプロセスを止める。

| ポート | 用途 |
|---|---|
| 8080/tcp | Telegraf の health |
| 57000/tcp | Cisco の MDT（dial-out）の受け口。lab からは何も来ない |
| 1162/udp | SNMP trap（機器は 162 に送り、`lab.sh forward` が 1162 へ向ける） |
| 5140/udp | syslog |

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
