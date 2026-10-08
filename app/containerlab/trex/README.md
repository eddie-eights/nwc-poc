# TRex（後段の負荷試験の道具）

lab の `dc1-trex-01`（containerlab の `kind: linux`、イメージは `trexcisco/trex:2.41`）で、Telegraf から後ろ（MSK → Spark / Splunk → Grafana / Splunk のアラート → SNS → Lambda）に負荷をかけるための材料。**このサイクル（011）では置くだけで、撃っていない。** TRex がこの lab で起動するかも確かめていない（下の「確かめていないこと」）。

| ファイル | 中身 |
|---|---|
| `trex_cfg.yaml.in` | TRex の設定の型。`lab trex start` がポート（`eth1`〜`eth4`）とアドレスを埋めて、コンテナの `/etc/trex_cfg.yaml` に書く |
| `stl/udp_trap.py` | SR Linux の linkDown と同じ形の SNMPv2c trap（sysUpTime、snmpTrapOID = linkDown、ifIndex、ifAdminStatus = up、ifOperStatus = down、ifName）を UDP で撃ち続ける STL プロファイル |
| `stl/udp_syslog.py` | SR Linux と同じ RFC 5424 / `local7` の syslog 1 行（既定は BGP の隣接が落ちた体の文）を UDP で撃ち続ける STL プロファイル |
| `kafka_load.sh` | MSK の `metrics` / `gnmi` に、Telegraf が書くのと同じ形の JSON を `kafka-producer-perf-test` で流す（ポーリングと gNMI は UDP ではないので TRex では作れない） |

## lab の中の配線

- `dc1-trex-01` のポートは 4 本: `eth1` → `dc1-s-leaf-01 ethernet-1/3`、`eth2` → `dc1-s-leaf-02 ethernet-1/3`、`eth3` → `dc1-a-leaf-01 ethernet-1/3`、`eth4` → `dc1-a-leaf-02 ethernet-1/3`（`../gen_lab.py` が作る）
- leaf の `ethernet-1/3.0` は mac-vrf `macvrf-100`（EVPN-VXLAN、VNI 100）の素の subinterface。LAG も Ethernet Segment も無い
- TRex のポートのアドレスは `10.100.0.11`〜`10.100.0.14`。`default_gw` は組の相手（ポート 0 ↔ 1、2 ↔ 3。TRex はポートを 2 本ずつ組にする）。4 本とも同じ mac-vrf にいるので、EVPN が通っていれば ARP が解ける
- `eth0` は containerlab の管理ネットワーク（`203.0.113.101`）。`lab trap-test` の送り元はこの IP（Spark の `--device-map` と Splunk の `DEVICE_MAP` で `dc1-trex-01` に引ける）

## 起こす・止める

lab の EC2 に SSM で入り、root で:

```bash
sudo lab trex start     # /etc/trex_cfg.yaml を書き、stl/ を /opt/nwc-trex/stl に写し、t-rex-64 -i（stateless のサーバ）を裏で起こす
sudo lab trex status    # プロセスと /var/log/trex.log の末尾（LINES=50 で行数を変える）
sudo lab trex stop
```

トポロジを上げても TRex は起動しない（負荷試験をやると決めるまで CPU を取らせない）。撃つのはコンテナの中の `trex-console` から:

```bash
sudo docker exec -it clab-splab-dc1-trex-01 sh -c 'cd "$(dirname "$(find / -xdev -maxdepth 5 -name t-rex-64 -type f | head -1)")" && ./trex-console'
```

```text
trex> start -f /opt/nwc-trex/stl/udp_trap.py --port 0 -t dst=203.0.113.1,dport=162,pps=1000
trex> stats
trex> stop -a
```

`-t` で渡せる値と既定は各プロファイルの先頭の docstring。値は `,` と `=` で区切るので、syslog の本文（`msg`）にこの 2 つは入れられない。

## L2 が leaf のあいだを通るかを見る

`lab check` は「各 leaf の `ethernet-1/3` と `dc1-trex-01` の `eth1`〜`eth4` が両端とも up」までしか見ない（TRex のポート同士は同じコンテナにあるので、ping では Linux の中で折り返してしまい L2 を通らない）。EVPN で MAC が回っているかは、`lab trex start` のあと:

1. trex-console で `arp`（各ポートが `default_gw` の ARP を出す。2.41 で先に `service` モードに入る要があるかは未確認。入ったら `service --off` で戻す）
2. どれか 1 台の leaf で bridge-table を見る: `sudo lab cli dc1-a-leaf-01 'show network-instance macvrf-100 bridge-table mac-table all'`
3. TRex の 4 ポートの MAC が 4 つ載っていれば通っている（自分の `ethernet-1/3.0` に 1 つ、`vxlan1.100` の先に 3 つ）

自動の `lab check` には入れていない（TRex を起こさないと MAC が出ないため）。

## どこへ撃つか

宛先は機器と同じにする。SR Linux は trap を `203.0.113.1:162`、syslog を `203.0.113.1:5140` に送り、lab の EC2 の `lab forward`（iptables の DNAT）が Telegraf の NLB へ向ける。NLB の listener は trap が **162/udp**（タスクの 1162 へ）、syslog が **5140/udp**（タスクも 5140）なので、プロファイルの既定は `dst=203.0.113.1`、trap は `dport=162`、syslog は `dport=5140`。送り元は `dc1-trex-01` の管理 IP（`203.0.113.101`）にしてある。NLB のセキュリティグループは管理ネットワーク（`203.0.113.0/24`）からを通し、NLB は送り元の IP を残すので、Splunk と Spark は `dc1-trex-01` の trap として読む。

デバッグ用の EC2（`IaC/cloudformation/lab-debug.yaml`）では Telegraf が同じホストで動き、`lab forward` は 162 を 1162 へ向けるだけ。宛先は同じでよい。

### 負荷の経路は決まっていない

**いまの配線のままでは、上のパケットは NLB に届かない。** TRex のデータのポート（`eth1`〜`eth4`）は leaf の mac-vrf に入るが、mac-vrf は L2 だけ（IRB も default への経路も無い）なので、`203.0.113.1` へは出られない。候補は 2 つで、どちらも実機で確かめていない（design.md の未確定事項 3。負荷試験をやると決めたサイクルで決める）:

- **(a) `eth0`（管理ネットワーク）を TRex のポートにする**
  - `/etc/trex_cfg.yaml` の `interfaces` を `['eth0', 'eth1']` の組に書き換えて起動し直す（TRex はポートを偶数本で組む。5 本目として足すのではなく組を作り直す）。ポート 0（`eth0`）は `ip: 203.0.113.101`、`default_gw: 203.0.113.1`
  - 出たパケットは lab の EC2 の docker のブリッジに着き、`lab forward` の DNAT でそのまま NLB へ行く。プロファイルの既定値がそのまま使える
  - 気になる点: af_packet がコンテナの管理の IF を握るので、TRex を起こしているあいだ `lab trap-test` や `docker exec` 越しの操作に影響するかもしれない。TRex が `203.0.113.101` の ARP に応える形になる
- **(b) containerlab の `host` endpoint で 5 本目・6 本目の veth を EC2 の netns に出す**
  - `../splab.clab.yml.in` の links に `["dc1-trex-01:eth5", "host:trex-load0"]`（と組にする `eth6`）を足し、EC2 側の `trex-load0` にアドレスを振って、iptables で NLB へ DNAT する（`lab forward` と同じ形の規則を足す）
  - 管理ネットワークに触らないが、`gen_lab.py` と `lab forward` の両方を変える

### 届いたことの見方

撃つ前に `sudo lab forward-status` で DNAT の規則と NLB の宛先を確かめる。Telegraf に着いたかは MSK の `traps` / `logs` のトピックを Kafbat UI（`docs/pipeline.md`「Kafka の画面（Kafbat UI）を開く」）か、下の `BytesInPerSec` で見る。

## どう測るか

| 見るところ | 何で見るか | 飽和の合図 |
|---|---|---|
| Telegraf → MSK | CloudWatch `AWS/Kafka` の `BytesInPerSec`（トピック別。`traps` / `logs` / `metrics` / `gnmi`） | 撃つ pps を上げても増えない |
| MSK → Spark | Spark のログの `StreamingQueryProgress` の `batchDuration` と `inputRowsPerSecond` / `processedRowsPerSecond` | トリガー（60 秒）に `batchDuration` が近づく、processed が input を下回り続ける |
| MSK のコンシューマラグ | CloudWatch `AWS/Kafka` の `MaxOffsetLag` / `SumOffsetLag`（コンシューマグループ別） | 増え続ける。Spark の構造化ストリーミングは offset を Kafka に commit しない（checkpoint に持つ）ので、Spark の分はここに出ないことがある。そのときは上の Spark のログで見る |
| SNS → Lambda | CloudWatch `AWS/Lambda` の `<prefix>-graph-status` の `Throttles` / `ConcurrentExecutions` / `Duration` | `Throttles` が 0 でなくなる |
| アラートの遅れ | アラートの履歴（analytics がある回だけ。Iceberg、`docs/pipeline.md`「アラートの履歴」）の `received_at` と、撃ち始めた時刻の差。Grafana と Splunk を `source` で分けて比べる | 平常時（`lab trap-test` 1 通）より遅れる |

trap は送り手（Grafana / Splunk）が同じ機器・種類・対象を 1 つの異常にまとめるので、同じパケットを撃ち続けてもアラートは 1 件しか増えない。後段の件数を増やしたいときは `ifindex` / `ifname` を変えたプロファイルを別ポートで並べる。

## lab の EC2 を大きくする

SR Linux 6 台と TRex 1 台で 11〜13 GB（推定）、`m6i.xlarge` は 16 GB。TRex が起きない・SR Linux が落ちるときは `m6i.2xlarge`（32 GB、東京で 0.496 $/h）に上げる:

1. `IaC/terraform/aws-managed/pipeline/lab/terraform.tfvars`（無ければ `terraform.tfvars.example` から作る）に `instance_type = "m6i.2xlarge"`
2. `bash ops/up.sh` をいつもどおり回す（lab の apply でインスタンスの種類が変わる。止めて変えるので lab は起動し直しになる）

選べるのは `variables.tf` の validation の 5 つ（`m6i.xlarge` / `m6i.2xlarge` / `c6i.2xlarge` / `t3.xlarge` / `t3.2xlarge`）。どれも x86_64（TRex のイメージが amd64 だけなので arm64 には戻せない）。デバッグ用の EC2 は CFn のパラメータ `InstanceType`（同じ 5 つ）。

## Kafka へ流す（`kafka_load.sh`）

```bash
sudo bash /opt/<prefix>-lab/src/trex/kafka_load.sh metrics 100000 1000   # metrics に 10 万件を 1000 件/秒
sudo bash /opt/<prefix>-lab/src/trex/kafka_load.sh gnmi 50000 -1         # gnmi に 5 万件を上限なしで
```

レコードは lab の SR Linux 6 台（`../splab.clab.yml.in` から読む）の名前と管理 IP を使う。`metrics` は機器ごとに `ethernet-1/1`〜`1/3` の `interface`（カウンタは乱数）、`gnmi` は `bgp_neighbor` の `session_state = established`（`bgp_down` は発火しない）。timestamp は作った時刻で固定。

### 前提（このサイクルでは揃えていない）

1. **lab の EC2 の IAM ロール**（`IaC/terraform/aws-managed/pipeline/lab/iam.tf`）に、MSK の `kafka-cluster:Connect` / `DescribeTopic` / `WriteData`（クラスターと `metrics` / `gnmi` のトピック）と、SSM の `<PARAM_PREFIX>/msk-bootstrap` の読み取りが無い。足すか、`BOOTSTRAP` を手で渡す
2. **lab の EC2 から MSK の 9098（SASL/IAM）へ届くか**確かめていない（MSK のセキュリティグループが lab のサブネットを通すか）
3. **イメージと jar を外から取れない**（VPC に NAT も IGW も無い）。`apache/kafka:4.3.1` と `aws-msk-iam-auth-2.3.9-all.jar` を手元で取り、イメージは ECR の `<prefix>-lab-*` のリポジトリ（lab の IAM が pull できるのはこの名前だけ）へ、jar は lab のバケットへ置いてから、`KAFKA_IMAGE` と `IAM_JAR` で渡す

## 確かめていないこと

- TRex 2.41（2018 年、CentOS 7）が SR Linux 26.7.2 と同じホストで、`privileged: true` + af_packet、hugepages 無しで起きるか（design.md の未確定事項 2）
- TRex のイメージに `pgrep` / `pkill` / `find` があるか（`lab trex start|stop|status` が使う）。無ければ `docker exec` で `ps` を見て止める
- `trex-console` が Python 2.7 で動くか 3 で動くか。プロファイルはどちらでも読める書き方にしてある（手元では Python 3.14 と scapy の SNMP の層でパケットを読み戻して確かめた）
- 負荷の経路（上の (a) / (b)）と、メモリ（上の「大きくする」）
