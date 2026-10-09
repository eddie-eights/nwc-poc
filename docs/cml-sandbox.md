# DevNet の CML サンドボックスで Cisco の収集の形を確かめる

← [README](../README.md)

Cisco DevNet Sandbox の CML（Cisco Modeling Labs）に、lab と同じ形のファブリックを NX-OS 9000v で組む手順。
機器から出てくるデータの形（MDT・gNMI・syslog・SNMP trap）を、SR Linux の lab と見比べるのが目的。2026-10-10 時点の手順だけで、まだ試していない。

## 目的と範囲

- 確かめたいのは、Cisco の機器が出すデータの形。
  本番は MDT の dial-out の方針だが（[collection.md](collection.md)）、lab の SR Linux は MDT を話さない。
- 見るものは 4 つ。
  MDT の dial-out、gNMI の dial-in、syslog、SNMP trap。
- データは AWS に送らない。
  受け口は手元（Mac か WSL2）か CML の中に置き、ファイルか画面で中身を見る。
- トポロジの YAML と機器の設定ファイルは、この文書では作らない。
  リポジトリに入れるのは次のサイクル（下の「片付ける」）。

## サンドボックスを予約する

公式のページ（下の「関連リンク」）で確かめたことと、確かめられなかったことを分けて書く。

| 項目 | 中身 | 出どころ |
|---|---|---|
| アカウント | Cisco のアカウント（DevNet）でログインする | Sandbox の Getting Started |
| 入口 | https://developer.cisco.com/sandbox の「Get started with Sandbox」からカタログを開く | Sandbox の Getting Started |
| サンドボックスの名前 | 予約型の「Cisco Modeling Labs」と「Cisco Modeling Labs Enterprise」の 2 つ | CML の Sandbox のページ |
| 中身 | どちらもサンプルのトポロジ（NX-OS・IOS-XE・IOS-XR・ASA・Linux）が入っている | CML の Sandbox のページ |
| Enterprise との違い | Enterprise は what-if の検証、API、サードパーティのイメージ向け | CML の Sandbox のページ |
| 予約の長さ | 既定は 2 時間で、合計 7 日まで延ばせる（Sandbox 全体の説明） | First Reservation Guide、FAQ |
| 予約の長さ（CML） | CML の製品ページは「無料の 4 時間のセッション」と書いている | Modeling Labs の製品ページ |
| 同時の予約 | 1 人 1 つまで | FAQ |
| 立ち上がるまで | サンドボックスによって 10〜40 分 | Sandbox の Getting Started |
| VPN | 予約型は VPN が要る。Cisco Secure Client 5（旧 AnyConnect）か OpenConnect | Sandbox の Getting Started |
| VPN の資格情報 | 「Your DevNet Sandbox Lab is Ready」のメール（devnetsandbox@cisco.com）と Output Window に出る | First Reservation Guide、FAQ |

- 未確認（2026-10-10 に確認を試みた）: CML のサンドボックスで予約できる最長（4 時間か 7 日か）。
  サンドボックスの予約のページが開けなかった。
- 未確認（2026-10-10 に確認を試みた）: サンドボックスで置けるノード数の上限と RAM。
  公式に 20 ノードと書いてあるのは CML Personal のライセンスで、サンドボックスの上限ではない。
- 未確認（2026-10-10 に確認を試みた）: CML の UI の URL とログインの資格情報。
  予約のあとのメールか Output Window で確かめる。
- 未確認（2026-10-10 に確認を試みた）: いまのサンドボックスに NX-OS 9000v のイメージがあるか、版は何か。

手順:

1. https://developer.cisco.com/sandbox を開き、Cisco のアカウントでログインする。
2. カタログで「Cisco Modeling Labs」を探し、予約する（開始は既定の「now」、長さは 2 時間から）。
3. 「Your DevNet Sandbox Lab is Ready」のメールを待つ。
4. メールの VPN のアドレスと資格情報で Cisco Secure Client（か OpenConnect）をつなぐ。
5. メールか Output Window に出る CML の URL をブラウザで開き、ログインする。
6. CML の Node definitions で `nxosv9000` があることと、その版を確かめる。
   無ければここで止め、Enterprise のほうを検討する（イメージを持ち込める）。

Cisco Secure Client は https://software.cisco.com/download/home/283000185 から入れる（Cisco のアカウントが要る）。

## トポロジを決める

lab（`app/containerlab/splab.clab.yml.in`）と同じ 6 台（spine 2 + s-leaf 2 + a-leaf 2）を NX-OS 9000v で組む。TRex は組まない。

| 項目 | 中身 |
|---|---|
| ノードの定義 | `nxosv9000`（CML の「NX-OS 9000」。9000v / 9300v / 9500v を含む） |
| 1 台のメモリ | 8 GB・2 vCPU（CiscoDevNet の virlutils と cml-community の定義。製品の文書には既定値が無い） |
| 6 台のメモリ | 8 GB × 6 = 48 GB |
| 軽い定義 | コミュニティの `nxosv9000-lite`（1 vCPU・8 GB）。サンドボックスにあるかは未確認 |
| 転送の上限 | 約 2.3 Mb/s（CML の NX-OS 9000 のページ）。データの形を見るだけなので足りる |
| 管理 | `mgmt0`。DHCP は使えないので固定で振る |
| インタフェース名 | `Ethernet1/N`（CML の文書では Eth1/x） |

- 未確認（2026-10-10 に確認を試みた）: サンドボックスの RAM が 48 GB を載せられるか。
  足りなければ spine 1 + leaf 2 の 3 台（24 GB）に減らす。
- 未確認（2026-10-10 に確認を試みた）: サンドボックスの管理網の形。
  2020 年の cml-community のトポロジは、外部コネクタ（bridge）で 10.10.20.0/24（GW 10.10.20.254）だった。
- 機器の名前は lab と同じにする（`dc1-spine-01` など）。
  sysName と syslog の hostname で機器を突き合わせる作りを、そのまま見比べられる。

SR Linux の lab との対応:

| lab（SR Linux） | CML（NX-OS 9000v） |
|---|---|
| `dc1-spine-01` / `-02`（管理 203.0.113.21 / .22） | 同じ名前。管理はサンドボックスの管理網で振る |
| `dc1-s-leaf-01` / `-02`、`dc1-a-leaf-01` / `-02` | 同じ名前 |
| spine の `ethernet-1/1`〜`1/4` → s-leaf-01、s-leaf-02、a-leaf-01、a-leaf-02 | spine の `Ethernet1/1`〜`1/4` を同じ順でつなぐ |
| leaf の `ethernet-1/1` → spine-01、`ethernet-1/2` → spine-02 | leaf の `Ethernet1/1` / `1/2` |
| リンクの /31（172.16.0.0〜172.16.0.15） | 同じアドレス |
| `system0`（spine 10.255.0.1 / .2、s-leaf 10.255.1.1 / .2、a-leaf 10.255.2.1 / .2） | `loopback0` に同じアドレス |
| IS-IS `main`、L2、NET 49.0001.0000.0000.00XX.00、point-to-point、system0 は passive | IS-IS、同じ NET、同じ方針 |
| iBGP AS 65100、EVPN だけ、spine がルートリフレクタ、keepalive 10 / hold 30 | iBGP AS 65100、L2VPN EVPN、spine がルートリフレクタ |
| leaf の VXLAN VNI 100、mac-vrf `macvrf-100`、EVI 100、RT target:65100:100 | leaf の NVE、VNI 100 を VLAN に割り当てる |
| leaf の `ethernet-1/3`（TRex） | 組まない（要るなら Linux のノードを足す） |

- ファブリック（IS-IS・BGP EVPN・VXLAN）の NX-OS の設定は、この文書では書かない。
  コマンドを公式で確かめていないので、トポロジの YAML と一緒に次のサイクルで書く。

## 受け口までの道を決める

機器から送るもの（MDT dial-out・syslog・trap）は、機器から受け口へ届く道が要る。gNMI は受け口から機器へ取りにいく。

| 案 | 受け口 | 機器から送るもの | gNMI |
|---|---|---|---|
| A. 手元 | Mac の Telegraf か WSL2 の compose | VPN の手元の IP へ送る。届くかは未確認 | 手元から VPN 越しに取りにいく |
| B. CML の中 | CML に Linux（Ubuntu など）のノードを置き、Telegraf・syslog-ng・gnmic を動かす | 管理網の中で届く見込み | 同じノードから取りにいく |

- 未確認（2026-10-10 に確認を試みた）: VPN の内側で、機器から手元の PC（VPN で振られた IP）へ接続を張れるか。
- 未確認（2026-10-10 に確認を試みた）: CML のノードがインターネットに出られるか（外部コネクタの NAT / bridge）。
  B でパッケージを入れるのに要る。
- まず A を試し、届かなければ B にする。
  A で gNMI だけ取れて dial-out が来なければ、VPN が手元向きの接続を通していないと見る。
- WSL2 の compose は、WSL の外から届くかが未確認（[docker/compose/README.md](../docker/compose/README.md)）。
  A は Mac の Telegraf を先に試す。

受け口のポート:

| 種類 | 受け口 | ポート | 機器側で変えられるか |
|---|---|---|---|
| MDT dial-out | Telegraf の `inputs.cisco_telemetry_mdt` | 57000/tcp | 変えられる（`destination-group` の `port`） |
| syslog | syslog-ng（compose）か Telegraf の `inputs.syslog` | compose は 5140/udp | UDP の書式に宛先ポートが無い（下の注） |
| SNMP trap | Telegraf の `inputs.snmp_trap` | compose は 1162/udp | 変えられる（`udp-port`） |
| gNMI | gnmic か Telegraf の `inputs.gnmi` | 機器の 50051/tcp | 機器側は `grpc port` で変えられる |

- NX-OS の `logging server` は、UDP の書式に `port` が無い（`port` は TLS の `secure` の書式だけ）。
  受け口を 514/udp で待たせるか、受け口の前で 514 を 5140 に転送する。
- MDT の受け口は 2026-10-08 に外してある（[collection.md](collection.md)）。
  手元では `git show ed8edf1^:app/telegraf/telegraf.conf.in` の `inputs.cisco_telemetry_mdt` の区間を元に、出力をファイルにする。

手元の Telegraf の例（Telegraf 単体で動かし、Kafka には出さない）:

```toml
[[inputs.cisco_telemetry_mdt]]
  transport = "grpc"
  service_address = ":57000"

[[inputs.snmp_trap]]
  service_address = "udp://:1162"

[[outputs.file]]
  files = ["stdout", "/tmp/cml-telegraf.out"]
  data_format = "json"
```

## NX-OS の設定を入れる

書式は Cisco の Programmability Guide と System Management Configuration Guide で確かめた（下の「関連リンク」）。
`<受け口の IP>` は前の節で決めた受け口、VRF は管理の `management` とする。

### MDT の dial-out

```text
feature telemetry
telemetry
  destination-profile
    use-vrf management
  destination-group 1
    ip address <受け口の IP> port 57000 protocol gRPC encoding GPB
  sensor-group 1
    data-source DME
    path sys/intf depth unbounded
  sensor-group 2
    data-source DME
    path sys/bgp depth unbounded
  subscription 1
    dst-grp 1
    snsr-grp 1 sample-interval 60000
    snsr-grp 2 sample-interval 0
```

- `sample-interval` はミリ秒で、`0` は変化したときだけ（event）送る。
  lab の gnmic の `interface_stats`（60 秒）と `bgp_neighbor`（on-change）に合わせた。
- 転送は gRPC（既定）・HTTP・UDP、エンコーディングは GPB（既定）・JSON。
  JSON も見るときは、別の `destination-group` を `encoding JSON` で足す。
- 受け口が GPB を読むには、CiscoDevNet の nx-telemetry-proto の .proto が要る。
  Telegraf 1.40.0 が NX-OS 10.6(4) から gRPC・GPB で受けた例が、Cisco の TechNote 226393 にある。
- 未確認（2026-10-10 に確認を試みた）: dial-out が既定で TLS か。手順の中の `feature nxapi` が DME に要るか。
- 確かめるコマンドは `show telemetry transport`、`show telemetry control database`、`show telemetry data collector brief`。

### gNMI の dial-in

```text
feature grpc
grpc port 50051
```

- 既定のポートは 50051 で、`grpc port` は書かなくてもよい。
  既定では管理の VRF が要求を受ける（`grpc use-vrf default` で default の VRF にも開ける）。
- 証明書は `grpc certificate <trustpoint>` で入れる。
  入れないときの既定の証明書は約 1 日で切れる（TechNote 220640）ので、試すときは `--skip-verify` で取る。
- エンコーディングは JSON と PROTO、origin は DME・DEVICE・OPENCONFIG。
- 未確認（2026-10-10 に確認を試みた）: OpenConfig のパスに `feature openconfig`（10.2(2) から）が要ること。
  TechNote には書いてあるが、設定ガイドでは確かめていない。
- 確かめるコマンドは `show grpc gnmi service statistics` と `show grpc gnmi transactions`。

```bash
gnmic -a <機器の管理 IP>:50051 -u <ユーザー> -p <パスワード> --skip-verify capabilities
```

### syslog

```text
logging server <受け口の IP> 6 use-vrf management
logging origin-id hostname
```

- 書式は `logging server <host> [<severity> [use-vrf <vrf>]]`。
  6 は informational で、lab の SR Linux（`match-above informational`）に合わせた。
- 送る facility は既定で local7（lab の SR Linux も local7）。既定の VRF は management。
- RFC 5424 に揃えて送らせる `logging rfc-strict 5424` がある。
  既定の形と見比べるため、最初は入れずに受け、あとで入れて受け直す。
- 未確認（2026-10-10 に確認を試みた）: NX-OS が既定で送る syslog の形（RFC 3164 か 5424 か）。

### SNMP trap

```text
snmp-server community public ro
snmp-server host <受け口の IP> traps version 2c public udp-port 1162
snmp-server host <受け口の IP> use-vrf management udp-port 1162
snmp-server enable traps link
```

- `use-vrf` の行は、先に `traps` の行で宛先を作ってから入れる。
- community は lab と同じ `public`（v2c）。サンドボックスの中だけで使う。
- 未確認（2026-10-10 に確認を試みた）: `udp-port` を書かないときの既定のポート（設定ガイドのページに無い）。

## SR Linux と見比べる

受けたデータを、lab の SR Linux のものと次の観点で並べる。結果はこの表の右の列に書き足す。

| 観点 | SR Linux（lab） | NX-OS 9000v で見るもの | 結果 |
|---|---|---|---|
| gNMI のポートと TLS | 57400、TLS（自己署名） | 50051、既定の証明書 | 未確認 |
| gNMI の IF の状態 | `/interface[name=*]/oper-state`（on-change） | OpenConfig `/interfaces/interface/state/oper-status` | 未確認 |
| gNMI の IF のカウンタ | `/interface[name=*]/statistics`（60 秒） | OpenConfig `.../state/counters` か DME `sys/intf` | 未確認 |
| gNMI の BGP | `.../bgp/neighbor[peer-address=*]/session-state` | OpenConfig の BGP の `neighbors` か DME `sys/bgp` | 未確認 |
| gNMI の IS-IS | `.../isis/instance[name=main]/interface[...]/oper-state` | OpenConfig の IS-IS のパスがあるか | 未確認 |
| gNMI の CPU・メモリ | `/platform/control[slot=*]/cpu[index=all]/total`、`/memory` | OpenConfig の `/system` か DME | 未確認 |
| MDT のエンコーディング | 送れない | GPB と JSON で、Telegraf の measurement（sensor path）と field の名前 | 未確認 |
| MDT の機器の名前 | なし | Telegraf の `source` タグ（機器が名乗る node_id）が hostname になるか | 未確認 |
| syslog の形 | RFC 5424、local7 | 既定の形と `logging rfc-strict 5424` のときの形、hostname の位置 | 未確認 |
| trap の OID | linkDown / linkUp（1.3.6.1.6.3.1.1.5.3 / .4） | 同じ OID か、Cisco 拡張（cieLinkDown / cieLinkUp）か | 未確認 |
| trap の IF の名前 | ifName は `ethernet-1/N` | ifName が `Ethernet1/N` になるか（`lab_topology.py` の読み替えに要る） | 未確認 |

- NX-OS の gNMI のパスは、まず `capabilities` で使えるモデルを見てから `get` で 1 つずつ試す。
  上の表の NX-OS 側のパスは候補で、どれも確かめていない。
- trap は機器の IF を `shutdown` / `no shutdown` して出す。
  linkDown / linkUp のどの種類が出るかは、`snmp-server enable traps link` の種類の指定で変わる（設定ガイド）。

## 片付ける

1. 受けたデータ（`/tmp/cml-telegraf.out` など）のうち、表に書いたもの以外は消す。
2. 手元の Telegraf か compose を止める（compose は [docker/compose/README.md](../docker/compose/README.md) の消し方）。
3. VPN を切る。
4. Sandbox の予約を終える（予約の画面から終了する。ボタンの名前は未確認）。
   予約は 1 人 1 つなので、終えないと次が取れない。

- AWS には何も作らないので、`ops/down.sh` は要らない。
- 次のサイクルで、トポロジの YAML（CML の lab の書き出し）と NX-OS の設定を `app/containerlab/cml/` に入れる。

## 関連リンク

どれも 2026-10-10 に開いた。

| ページ | 確かめたこと |
|---|---|
| [CML の Sandbox](https://developer.cisco.com/docs/modeling-labs/sandbox/) | サンドボックスの 2 つの名前、サンプルのトポロジ |
| [Cisco Modeling Labs](https://developer.cisco.com/modeling-labs/) | 無料の 4 時間のセッション、CML Personal の 20 ノード |
| [Sandbox の Getting Started](https://developer.cisco.com/docs/sandbox/getting-started/) | 予約型と VPN、10〜40 分、Cisco Secure Client |
| [First Reservation Guide](https://developer.cisco.com/docs/sandbox/first-reservation-guide/) | 既定の 2 時間、7 日まで、VPN のメール |
| [Sandbox の FAQ](https://developer.cisco.com/docs/sandbox/faqs) | 同時の予約は 1 つ、VPN の情報の出どころ |
| [CML の NX-OS 9000][cml-nxos] | 9000v / 9300v / 9500v、約 2.3 Mb/s、DHCP 不可、mgmt0 と Eth1/x |
| [virlutils](https://github.com/CiscoDevNet/virlutils) | `nxosv9000` の 8 GB・2 vCPU（テストのデータ） |
| [cml-community](https://github.com/CiscoDevNet/cml-community) | `nxosv9000-lite`、サンドボックスの 2020 年のトポロジ |
| [NX-OS 10.4(x) Model Driven Telemetry][nxos-mdt] | `telemetry` の書式、転送とエンコーディング、`sample-interval` |
| [TechNote 226393][tn-226393] | NX-OS 10.6(4) から Telegraf 1.40.0 へ gRPC・GPB、`show telemetry` |
| [NX-OS 10.6(x) gRPC Agent][nxos-grpc] | `feature grpc`、既定の 50051、管理の VRF |
| [NX-OS 10.4(x) gNMI][nxos-gnmi] | エンコーディングと origin、`show grpc gnmi` |
| [NX-OS 10.1(x) SNMP][nxos-snmp] | `snmp-server host` の書式、`enable traps link` |
| [NX-OS 10.2(x) System Message Logging][nxos-syslog] | `logging server` の書式、local7、`rfc-strict 5424`、`origin-id` |
| [nx-telemetry-proto](https://github.com/CiscoDevNet/nx-telemetry-proto) | MDT の GPB の .proto |

[cml-nxos]: https://developer.cisco.com/docs/modeling-labs/nx-os-9000
[nxos-mdt]: https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/104x/programmability/cisco-nexus-9000-series-nx-os-programmability-guide-104x/m-n9k-model-driven-telemetry-101x.html
[tn-226393]: https://www.cisco.com/c/en/us/support/docs/switches/nexus-9000-series-switches/226393-configure-and-verify-telemetry-on-nexus.html
[nxos-grpc]: https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/106x/programmability/cisco-nexus-9000-series-nx-os-programmability-guide-106x/m-grpc-agent.html
[nxos-gnmi]: https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/104x/programmability/cisco-nexus-9000-series-nx-os-programmability-guide-104x/m-gnmi.html
[nxos-snmp]: https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/101x/configuration/system-management/cisco-nexus-9000-series-nx-os-system-management-configuration-guide-101x/m-configuring-snmp.html
[nxos-syslog]: https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/102x/configuration/system-management/cisco-nexus-9000-series-nx-os-system-management-configuration-guide-102x/m-configuring-system-message-logging-10x.html

## 経緯

- 2026-10-10: 手順だけを書いた（`docs/cycles/QUEUE.md` の候補から）。サンドボックスの予約と NX-OS の設定は、まだ試していない。
