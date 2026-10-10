# DevNet の CML サンドボックスで Cisco の収集の形を確かめる

← [README](../README.md)

Cisco DevNet Sandbox の CML（Cisco Modeling Labs）に、lab と同じ形のファブリックを NX-OS 9000v で組み、機器から出てくるデータ（MDT・gNMI・syslog・SNMP trap）を手元の compose で受け、SR Linux の lab と見比べるためのガイド。
受けたデータを AWS の閉域のパイプラインまで流す道も、1 案を手順の形で書く。

## この文書の範囲（docs だけ。組むのはこれから）

- 2026-10-10 のユーザーの判断で、このサイクルで作るのは文書だけ。サンドボックスの予約、VPN、CML の操作、NX-OS の設定、AWS への接続は、どれもまだ実施していない。
- 「未確認」と書いた値は、2026-10-10 に公式の文書で確かめようとして確かめられなかったもの。試すときに埋め、確かめた日を書き足す。
- 「AWS のパイプラインに流す」の節は IaC の変更案を含むが、コードは変えていない。変えるのは組むサイクルで、PM の判断を経てから。
- トポロジの YAML と NX-OS の設定ファイルも、この文書では作らない。下の設定の雛形を元に、組むサイクルで `app/containerlab/cml/` に入れる（「片付ける」）。

## 目的と範囲

- 確かめたいのは、Cisco の機器が出すデータの形。
  本番は MDT の dial-out の方針だが（[collection.md](collection.md)）、lab の SR Linux は MDT を話さない。
- 見るものは 4 つ。
  MDT の dial-out、gNMI の dial-in、syslog、SNMP trap。
- 受け口は手元（VPN をつないだ PC の `docker/compose/`）。
  手元だけで輪は閉じる（Spark → Splunk / OpenSearch / Grafana）。AWS に流すのは、本番と同じ道（MSK → Spark → S3 Tables）で見たいときだけ。
- AWS に流すときも、受け口は手元の compose のまま。
  compose の syslog-ng・gnmic の書き先を、トンネル越しの MSK に向ける（「AWS のパイプラインに流す」）。

## サンドボックスを予約する

公式のページ（下の「関連リンク」）で確かめたことと、確かめられなかったことを分けて書く。

| 項目 | 中身 | 出どころ |
|---|---|---|
| アカウント | Cisco のアカウント（DevNet）でログインする | Sandbox の Getting Started |
| 入口 | https://developer.cisco.com/sandbox の「Get started with Sandbox」からカタログを開く | Sandbox の Getting Started |
| サンドボックスの名前 | 予約型の「Cisco Modeling Labs」と「Cisco Modeling Labs Enterprise」の 2 つ | CML の Sandbox のページ |
| 中身 | どちらもサンプルのトポロジ（NX-OS・IOS-XE・IOS-XR・ASA・Linux）が入っている | CML の Sandbox のページ |
| Enterprise との違い | Enterprise は what-if の検証、API、サードパーティのイメージ向け | CML の Sandbox のページ |
| 予約の長さ（Sandbox 全体） | 最長 7 日（「Reservations up to 7 days」）。FAQ は「既定は 7 日」 | Sandbox の Getting Started、FAQ（2026-10-10） |
| 予約の長さ（別の記述） | First Reservation Guide は「既定は 2 時間」、CML の製品ページは「無料の 4 時間のセッション」 | First Reservation Guide、Modeling Labs の製品ページ |
| 同時の予約 | 1 人 1 つまで（「only one reservation at a time」）。長期は Sandbox Community Forum で相談 | FAQ |
| 立ち上がるまで | サンドボックスによって 10〜40 分 | Sandbox の Getting Started |
| VPN | 予約型は VPN が要る。Cisco Secure Client 5（旧 AnyConnect 4）か OpenConnect（「You can use openconnect instead of anyconnect」） | Sandbox の Getting Started |
| VPN の資格情報 | 「Your DevNet Sandbox Lab is Ready」のメール（devnetsandbox@cisco.com）と Output Window に出る | First Reservation Guide、FAQ |

- 予約の長さの記述は公式のページどうしで食い違う（7 日 / 2 時間 / 4 時間）。
  予約の画面で選べる長さが正で、CML のサンドボックスの最長は未確認（2026-10-10 に確認を試みた。予約のページが開けなかった）。
- 未確認（2026-10-10 に確認を試みた）: サンドボックスで置けるノード数の上限と RAM。
  公式に 20 ノードと書いてあるのは CML Personal のライセンスで、サンドボックスの上限ではない。
- 未確認（2026-10-10 に確認を試みた）: CML の UI の URL とログインの資格情報。
  予約のあとのメールか Output Window で確かめる。
- 未確認（2026-10-10 に確認を試みた）: いまのサンドボックスに NX-OS 9000v のイメージがあるか、版は何か。
  Cisco Community には、CML のサンドボックスから `nxosv9000` の定義が容量の都合で外された（「Node n0 Spine-1 definition nxosv9000 not found」）という投稿と、「Insufficient HW resources」の投稿がある（非公式。下の「関連リンク」）。
  無ければ Enterprise のほうか、ノードを減らす。

手順:

1. https://developer.cisco.com/sandbox を開き、Cisco のアカウントでログインする。
2. カタログで「Cisco Modeling Labs」を探し、予約する（開始は「now」。長さは画面で選べる最長にする。足りなければ延長できるかを画面で見る）。
3. 「Your DevNet Sandbox Lab is Ready」のメールを待つ（10〜40 分）。
4. メールの VPN のアドレスと資格情報で VPN をつなぐ。
   - Cisco Secure Client: https://software.cisco.com/download/home/283000185 から入れる（Cisco のアカウントが要る）。
   - OpenConnect（Mac は `brew install openconnect`、WSL2 の Ubuntu は `apt install openconnect`）: `sudo openconnect <メールの VPN のアドレス>`。ユーザー名とパスワードはメールのもの。
5. メールか Output Window に出る CML の URL をブラウザで開き、ログインする。
6. CML の Tools → Node and Image Definitions で `nxosv9000` があることと、イメージの版を確かめる（メニューの名前は 2.x の UI のもので、サンドボックスの版では未確認）。
   無ければここで止め、Enterprise のほうを検討する（イメージを持ち込める）。

VPN をつないだ PC の IP（VPN のインタフェースに振られたアドレス）を控える。機器から送るもの（syslog・trap・MDT）の宛先になる（「受け口までの道を決める」）。

## CML を操作する（Web UI・`cml` CLI・virl2-client）

トポロジは CML の lab の YAML（ノード・リンク・各ノードの起動時の設定）で持ち、3 つのどれかで入れる。
YAML の書式は、CML の Web UI で小さな lab を作って Download したものを元にする（公式の書式の文書は 2026-10-10 に開いていない）。

| 手段 | 向き | 入れ方 |
|---|---|---|
| Web UI | 最初の 1 回、画面で確かめながら | Dashboard の Import で YAML を選ぶ（ボタンの文言は未確認）。Lab を開いて Start |
| `cml` CLI（virlutils） | 手元から繰り返し上げ下げする | `pip install virlutils`（Python 3.10 以上）。接続先は環境変数 `VIRL_HOST` / `VIRL_USERNAME` / `VIRL_PASSWORD`（か `.virlrc`）。`cml ls`、`cml up`（カレントの `topology.yaml`。別名は `-f`。README の例は `.unl` だけで、CML の YAML を `-f` で渡せるかは未確認）、`cml use --lab-name <名前>`、`cml nodes`、`cml console <ノード>`、`cml down`、`cml rm` |
| virl2-client（Python） | スクリプトから | `pip3 install virl2_client`。**クライアントの版はコントローラーの版に合わせる**（README）。API の文書はコントローラーの Tools → Client Library にある。lab の読み込みと起動のメソッド名は未確認（コントローラーの文書で確かめる） |

- `VIRL_PASSWORD` のようにパスワードを環境変数に置くので、シェルの履歴に残さない（`read -s` で入れる）。リポジトリにも書かない。
- サンドボックスの CML の証明書は自己署名の見込み。`cml` が検証で止まるなら、virlutils の README の証明書の扱いを見る（未確認）。
- 自分で確かめるコマンド: `cml nodes` で 6 台が `BOOTED` になるまで待つ。NX-OS 9000v の起動には数分かかる。

## トポロジを決める

lab（`app/containerlab/splab.clab.yml.in`）と同じ 6 台（spine 2 + s-leaf 2 + a-leaf 2）を NX-OS 9000v で組む。TRex は組まない。

| 項目 | 中身 |
|---|---|
| ノードの定義 | `nxosv9000`（CML の「NX-OS 9000」。9000v / 9300v / 9500v を含む） |
| 1 台のメモリ | 8 GB・2 vCPU（CiscoDevNet の virlutils と cml-community の定義。製品の文書には既定値が無い） |
| 6 台のメモリ | 8 GB × 6 = 48 GB |
| 軽い定義 | コミュニティの `nxosv9000-lite`（1 vCPU・8 GB）。サンドボックスにあるかは未確認 |
| 転送の上限 | 約 2.3 Mb/s（CML の NX-OS 9000 のページ）。データの形を見るだけなので足りる |
| 管理 | `mgmt0`。DHCP は使えない（「Not supported on NX-OS 9000v」）ので固定で振る |
| インタフェース名 | `Ethernet1/N`（CML の文書では Eth1/x） |
| 外部コネクタ | 管理網を外部コネクタ（External Connector）につなぐ。モードは NAT（既定）か System Bridge（下の「受け口までの道を決める」） |

- 未確認（2026-10-10 に確認を試みた）: サンドボックスの RAM が 48 GB を載せられるか。
  足りなければ spine 1 + leaf 2 の 3 台（24 GB）に減らす（コミュニティの「Insufficient HW resources」の投稿もこの線）。
- 未確認（2026-10-10 に確認を試みた）: サンドボックスの管理網の形。
  2020 年の cml-community のトポロジは、外部コネクタ（bridge）で 10.10.20.0/24（GW 10.10.20.254）だった。
  NAT モードの既定は `virbr0` の 192.168.255.0/24（GW .1、DHCP は .2〜.254。NX-OS 9000v は DHCP が使えないので、この範囲の外か DHCP の範囲を避けて固定で振る）。
- 機器の名前は lab と同じにする（`dc1-spine-01` など）。
  sysName と syslog の hostname で機器を突き合わせる作りを、そのまま見比べられる。

SR Linux の lab との対応:

| lab（SR Linux） | CML（NX-OS 9000v） |
|---|---|
| `dc1-spine-01` / `-02`（管理 203.0.113.21 / .22） | 同じ名前。管理はサンドボックスの管理網で振る |
| `dc1-s-leaf-01` / `-02`、`dc1-a-leaf-01` / `-02` | 同じ名前 |
| spine の `ethernet-1/1`〜`1/4` → s-leaf-01、s-leaf-02、a-leaf-01、a-leaf-02 | spine の `Ethernet1/1`〜`1/4` を同じ順でつなぐ |
| leaf の `ethernet-1/1` → spine-01、`ethernet-1/2` → spine-02 | leaf の `Ethernet1/1` / `1/2` |
| リンクの /31（172.16.0.0〜172.16.0.15） | 同じアドレス（下の表） |
| `system0`（spine 10.255.0.1 / .2、s-leaf 10.255.1.1 / .2、a-leaf 10.255.2.1 / .2） | `loopback0` に同じアドレス |
| IS-IS `main`、L2、NET 49.0001.0000.0000.00XX.00、point-to-point、system0 は passive | IS-IS、同じ NET、同じ方針 |
| iBGP AS 65100、EVPN だけ、spine がルートリフレクタ、keepalive 10 / hold 30 | iBGP AS 65100、L2VPN EVPN、spine がルートリフレクタ |
| leaf の VXLAN VNI 100、mac-vrf `macvrf-100`、EVI 100、RT target:65100:100 | leaf の NVE、VNI 100 を VLAN 100 に割り当てる |
| leaf の `ethernet-1/3`（TRex） | 組まない（要るなら Linux のノードを足す） |

アドレス（`app/containerlab/gen_lab.py` の既定と同じ。spine 側が偶数、leaf 側が奇数）:

| spine | IF | spine 側 | 相手 | 相手の IF | 相手側 |
|---|---|---|---|---|---|
| dc1-spine-01 | Ethernet1/1 | 172.16.0.0/31 | dc1-s-leaf-01 | Ethernet1/1 | 172.16.0.1/31 |
| dc1-spine-01 | Ethernet1/2 | 172.16.0.2/31 | dc1-s-leaf-02 | Ethernet1/1 | 172.16.0.3/31 |
| dc1-spine-01 | Ethernet1/3 | 172.16.0.4/31 | dc1-a-leaf-01 | Ethernet1/1 | 172.16.0.5/31 |
| dc1-spine-01 | Ethernet1/4 | 172.16.0.6/31 | dc1-a-leaf-02 | Ethernet1/1 | 172.16.0.7/31 |
| dc1-spine-02 | Ethernet1/1 | 172.16.0.8/31 | dc1-s-leaf-01 | Ethernet1/2 | 172.16.0.9/31 |
| dc1-spine-02 | Ethernet1/2 | 172.16.0.10/31 | dc1-s-leaf-02 | Ethernet1/2 | 172.16.0.11/31 |
| dc1-spine-02 | Ethernet1/3 | 172.16.0.12/31 | dc1-a-leaf-01 | Ethernet1/2 | 172.16.0.13/31 |
| dc1-spine-02 | Ethernet1/4 | 172.16.0.14/31 | dc1-a-leaf-02 | Ethernet1/2 | 172.16.0.15/31 |

IS-IS の NET は lab と同じく管理 IP の最後のオクテットから作る（`49.0001.0000.0000.<4 桁>.00`。spine-01 は `.0021.`、s-leaf-01 は `.0011.`、a-leaf-01 は `.0031.`）。
管理 IP をサンドボックスの管理網で振り直しても、NET は lab の番号のままにする（SR Linux と並べて見るため）。

### ファブリックの設定の雛形（未確認）

NX-OS の VXLAN BGP EVPN の設定ガイド（10.4(x)）を 2026-10-10 に開けなかったので、下の雛形は NX-OS の一般的な書き方で、**コマンドの 1 行 1 行は公式で確かめていない**。
組むときに `Cisco Nexus 9000 Series NX-OS VXLAN Configuration Guide` の「Configuring VXLAN BGP EVPN」で確かめ、直した所をこの節に書き戻す。

spine（`dc1-spine-01` の例。`-02` は NET・loopback0・/31 を表のとおり替える）:

```text
hostname dc1-spine-01
feature isis
feature bgp
feature nv overlay
nv overlay evpn

router isis main
  net 49.0001.0000.0000.0021.00
  is-type level-2
  log-adjacency-changes

interface loopback0
  ip address 10.255.0.1/32
  ip router isis main
  isis passive-interface level-2

interface Ethernet1/1
  description fabric to dc1-s-leaf-01
  no switchport
  mtu 9216
  ip address 172.16.0.0/31
  ip router isis main
  isis network point-to-point
  no shutdown
! Ethernet1/2〜1/4 も同じ形で、表のアドレスと相手

router bgp 65100
  router-id 10.255.0.1
  timers bgp 10 30
  address-family l2vpn evpn
  template peer LEAF
    remote-as 65100
    update-source loopback0
    address-family l2vpn evpn
      send-community both
      route-reflector-client
  neighbor 10.255.1.1
    inherit peer LEAF
  neighbor 10.255.1.2
    inherit peer LEAF
  neighbor 10.255.2.1
    inherit peer LEAF
  neighbor 10.255.2.2
    inherit peer LEAF
```

leaf（`dc1-s-leaf-01` の例。`-02`・a-leaf は NET・loopback0・/31 を替える）:

```text
hostname dc1-s-leaf-01
feature isis
feature bgp
feature nv overlay
feature vn-segment-vlan-based
nv overlay evpn

vlan 100
  vn-segment 100

router isis main
  net 49.0001.0000.0000.0011.00
  is-type level-2
  log-adjacency-changes

interface loopback0
  ip address 10.255.1.1/32
  ip router isis main
  isis passive-interface level-2

interface Ethernet1/1
  description fabric to dc1-spine-01
  no switchport
  mtu 9216
  ip address 172.16.0.1/31
  ip router isis main
  isis network point-to-point
  no shutdown

interface Ethernet1/2
  description fabric to dc1-spine-02
  no switchport
  mtu 9216
  ip address 172.16.0.9/31
  ip router isis main
  isis network point-to-point
  no shutdown

interface nve1
  no shutdown
  host-reachability protocol bgp
  source-interface loopback0
  member vni 100
    ingress-replication protocol bgp

evpn
  vni 100 l2
    rd auto
    route-target import 65100:100
    route-target export 65100:100

router bgp 65100
  router-id 10.255.1.1
  timers bgp 10 30
  address-family l2vpn evpn
  template peer SPINE
    remote-as 65100
    update-source loopback0
    address-family l2vpn evpn
      send-community both
  neighbor 10.255.0.1
    inherit peer SPINE
  neighbor 10.255.0.2
    inherit peer SPINE
```

- `mtu 9216` は CML の NX-OS 9000 のページが「9.3(3) より前の版では MTU の設定を受けない」と書いているので、受けなければ消す。
- lab には TRex の口（`ethernet-1/3`、mac-vrf の bridged な口）があるが、CML では組まない。VLAN 100 に入れるアクセスポートは無くてよい（EVPN の経路が出るのは、ホストの MAC を学んだあと）。
- 確かめるコマンド: `show isis adjacency`、`show bgp l2vpn evpn summary`、`show nve peers`、`show nve vni`。

## 受け口までの道を決める

機器から送るもの（MDT dial-out・syslog・trap）は、機器から受け口へ届く道が要る。gNMI は受け口から機器へ取りにいく。
受け口は、VPN をつないだ PC の `docker/compose/`（「ローカルで Kafka・Spark・Splunk・OpenSearch・Grafana を立てる（006）」。[docker/compose/README.md](../docker/compose/README.md)）。

```
CML の NX-OS 9000v ─ 管理網 ─ 外部コネクタ ─ CML の VM ─ サンドボックスの VPN ─ PC（VPN の IP）
                                                                       │ network_mode: host
                              syslog 514/udp → 5140   ───────────────→ syslog-ng ─┐
                              trap   162/udp → 1162   ───────────────→ Telegraf  ─┤→ Kafka（localhost:9094〜9096）→ Spark → Splunk / OpenSearch / Grafana
                              MDT    57000/tcp        ───────────────→ Telegraf（MDT の入力は手で足す。ファイルに出す）
                              gNMI   50051/tcp        ←─────────────── gnmic   ─┘
```

| 案 | 受け口 | 機器から送るもの | gNMI |
|---|---|---|---|
| A. 手元（これを取る） | VPN をつないだ PC の compose | VPN の PC の IP へ送る。届くかは未確認 | PC から VPN 越しに取りにいく |
| B. CML の中 | CML に Linux（Ubuntu など）のノードを置き、Telegraf・syslog-ng・gnmic を動かす | 管理網の中で届く見込み | 同じノードから取りにいく |

- A を取る。B は AWS に流す道が無い（CML のノードから AWS の閉域へは届かない）ので、A で届かないときの切り分けにだけ使う。
- 外部コネクタのモード（CML の External Connectors の文書。「関連リンク」）:
  - NAT（既定）: `virbr0`、192.168.255.0/24。**lab のノードが始める通信**だけ外へ出る（「traffic initiated by lab nodes」。外から中への接続は書かれていない）。
    syslog・trap・MDT の dial-out は機器が始めるので NAT で通る見込み。gNMI の dial-in（PC → 機器）は通らない見込み。
  - System Bridge: `bridge0`。CML の VM の vNIC と同じ L2 に乗り、「外のネットワークから動いているトポロジへ直接つなげる」。gNMI の dial-in にはこちらが要る。
  - モードは、ノードを止めて wipe したときだけ変えられる（「Only unstarted and wiped nodes may change their selected configuration values」）。
- 未確認（2026-10-10 に確認を試みた）: VPN の内側で、CML の VM が VPN のクライアント（PC の IP）へ経路を持つか。
  NAT モードで機器から PC へ出た syslog が PC に届くかは、これで決まる。届かなければ B で切り分ける。
- 未確認（2026-10-10 に確認を試みた）: サンドボックスの CML で System Bridge モードが使えるか（CML の VM の NIC をサンドボックス側が管理している）。
  使えなければ gNMI は B（CML の中の Linux から gnmic）で取る。
- WSL2 の compose は、WSL の外から届くかが未確認（[docker/compose/README.md](../docker/compose/README.md)）。
  VPN を WSL の中（OpenConnect）でつなげば、VPN の IP も WSL の中にあるので、この問題を避けられる。Mac なら Docker Desktop の `network_mode: host` の扱いが別で、未確認。

受け口のポート:

| 種類 | 受け口（compose） | compose が待つポート | 機器側で変えられるか |
|---|---|---|---|
| MDT dial-out | Telegraf の `inputs.cisco_telemetry_mdt`（compose の Telegraf には無い。手で足す） | 57000/tcp | 変えられる（`destination-group` の `port`） |
| syslog | syslog-ng | 5140/udp（と tcp） | UDP の書式に宛先ポートが無い（下の注） |
| SNMP trap | Telegraf の `inputs.snmp_trap` | 1162/udp | 変えられる（`udp-port`） |
| NetFlow / sFlow | GoFlow2 | 2055/udp、6343/udp | NX-OS 9000v はこの文書では送らせない |
| gNMI | gnmic | 機器の 50051/tcp | 機器側は `grpc port` で変えられる |

- compose の受け口（Telegraf・syslog-ng・GoFlow2）は `network_mode: host` で、手元の lab の管理ネットの GW `203.0.113.1` が PC にあればそこだけで待つ（`docker/compose/up.sh` の `TELEGRAF_BIND`）。
  CML の機器は VPN の IP に送るので、**手元の lab は上げない**（上げてあるなら `docker/compose/lab.sh down` のあと `docker/compose/up.sh telegraf syslog-ng goflow2` で全部のインタフェースで待たせる。`up.sh` が WARNING を出すのは想定どおり）。
- NX-OS の `logging server` は、UDP の書式に `port` が無い（`port` は TLS の `secure` の書式だけ）。
  PC で 514/udp を 5140 に送り直す。trap も `udp-port 1162` を書けるが、既定の 162 で送って 1162 に送り直すほうが本番に近い。
  手元の lab の `docker/compose/lab.sh forward` の REDIRECT は送り元 `203.0.113.0/24` だけに効くので、CML の機器（NAT なら CML の VM の IP、Bridge なら機器の IP）向けの規則を手で足す（未確認。送り元の IP は届いた tcpdump で見る）:

  ```bash
  # WSL2（VPN のある側）。<CML の送り元> は tcpdump で見た送り元の CIDR
  sudo iptables -t nat -A PREROUTING -s <CML の送り元> -p udp --dport 514 -j REDIRECT --to-ports 5140
  sudo iptables -t nat -A PREROUTING -s <CML の送り元> -p udp --dport 162 -j REDIRECT --to-ports 1162
  sudo tcpdump -n -i any 'udp port 514 or udp port 5140 or udp port 162'
  ```

- compose の `SYSLOG_STANDARD` は `RFC5424`（lab の SR Linux 向け）。
  NX-OS の既定の syslog は RFC 3164 風（`<PRI>hostname: 2026 Oct 10 12:00:00 UTC: %FACILITY-SEV-MNEMONIC: 本文`）で、RFC5424 の受け口でも RFC3164 の受け口でも**捨てずに入る**が、`hostname:` が appname に、`sysName` には送り元の IP が入る（手元の syslog-ng 4.29.0 で確かめた。[troubleshooting.md](troubleshooting.md) の「syslog の試験行が logs に入らない」）。
  機器名で引けるようにするには、NX-OS 側で `logging rfc-strict 5424` にして受け口を `RFC5424` のままにするか、`DEVICE_MAP` に CML の管理 IP と名前を入れる。両方試して形を見比べる。
- gnmic の購読先 `GNMI_TARGETS` と Spark の `DEVICE_MAP` は、`docker/compose/up.sh` が手元の lab の定義（`app/containerlab/lab_topology.py`）から作る。
  CML の機器は定義に無いので、`up.sh` を使わず環境変数で直接渡して `docker compose up -d gnmic spark-…` する（渡し方の形は `lab_topology.py --gnmi-targets` / `--device-map` の出力と同じ。`"<管理 IP>:50051", …` と `<管理 IP>=<名前>,…`）。
  `GNMI_USERNAME` / `GNMI_PASSWORD` は NX-OS に作ったユーザーに替える。
- `app/gnmic/gnmic.yaml.in` の購読のパスは SR Linux のもの（`/interface[name=*]/oper-state` など）で、NX-OS では取れない見込み。
  compose の gnmic を向ける前に、gnmic の CLI で `capabilities` と `get` を当て、取れるパスを「SR Linux と見比べる」の表に埋める。
- MDT は compose の Telegraf に入力が無い（2026-10-08 に外した。[collection.md](collection.md)）。
  `git show ed8edf1^:app/telegraf/telegraf.conf.in` の `inputs.cisco_telemetry_mdt` の区間を元に、手元の Telegraf（compose の外で単体）で受けてファイルに出す。Kafka に書く形（measurement と field の名前）が決まってから compose に足す。

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

書式は Cisco の Programmability Guide と System Management Configuration Guide で確かめた（下の「関連リンク」。syslog の設定ガイドは 2026-10-10 に 404 で開けず、`logging server` の書式は 2026-10-10 より前に開いたときのまま）。
`<受け口の IP>` は VPN をつないだ PC の IP、VRF は管理の `management` とする。

### MDT の dial-out

```text
feature telemetry
telemetry
  destination-profile
    use-vrf management
    source-interface mgmt0
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
- TLS は、`destination-profile` に `certificate <パス> <ホスト名>` で証明書を入れたときだけ（10.4(x) の MDT のガイド）。入れなければ平文の gRPC。
  受け口の Telegraf も `tls_cert` 無しで待つ。
- `destination-profile` には `use-chunking`、`use-compression gzip`、`source-interface` も書ける。送り元の IP を管理 IP に固定したいので `source-interface mgmt0` を入れた。
- 受け口が GPB を読むには、CiscoDevNet の nx-telemetry-proto の .proto が要る。
  Telegraf 1.40.0 が NX-OS 10.6(4) から gRPC・GPB で受けた例が、Cisco の TechNote 226393 にある。
- 未確認（2026-10-10 に確認を試みた）: 手順の中の `feature nxapi` が DME に要るか。
- 確かめるコマンドは `show telemetry transport`、`show telemetry control database`、`show telemetry data collector brief`。

### gNMI の dial-in

```text
feature grpc
grpc port 50051
grpc use-vrf management
```

- 既定のポートは 50051 で、`grpc port` は書かなくてもよい。
  既定では管理の VRF が要求を受ける（`grpc use-vrf default` で default の VRF にも開ける）。
- 証明書は `grpc certificate <trustpoint>` で入れる。
  入れないときの既定の証明書は約 1 日で切れる（TechNote 220640）ので、試すときは `--skip-verify` で取る。compose の gnmic（`app/gnmic/gnmic.yaml.in`）は SR Linux の自己署名を `skip-verify` で通しているので、同じ扱いになる。
- エンコーディングは JSON と PROTO、origin は DME・DEVICE・OPENCONFIG。
- 未確認（2026-10-10 に確認を試みた）: OpenConfig のパスに `feature openconfig`（10.2(2) から）が要ること。
  TechNote には書いてあるが、設定ガイドでは確かめていない。
- 確かめるコマンドは `show grpc gnmi service statistics` と `show grpc gnmi transactions`。

```bash
gnmic -a <機器の管理 IP>:50051 -u <ユーザー> -p <パスワード> --skip-verify capabilities
gnmic -a <機器の管理 IP>:50051 -u <ユーザー> -p <パスワード> --skip-verify -e json_ietf get --path /interfaces/interface/state/oper-status
```

### syslog

```text
logging server <受け口の IP> 6 use-vrf management
logging source-interface mgmt0
logging origin-id hostname
! RFC 5424 に揃えるとき（2 回目）
logging rfc-strict 5424
```

- 書式は `logging server <host> [<severity> [use-vrf <vrf>]]`。
  6 は informational で、lab の SR Linux（`match-above informational`）に合わせた。
- 送る facility は既定で local7（lab の SR Linux も local7）。既定の VRF は management。
- RFC 5424 に揃えて送らせる `logging rfc-strict 5424` がある。
  既定の形と見比べるため、最初は入れずに受け、あとで入れて受け直す。受け口（compose の syslog-ng）は `SYSLOG_STANDARD=RFC5424` のままでよい（RFC 3164 の行も読める。[troubleshooting.md](troubleshooting.md)）。
- 未確認（2026-10-10 に確認を試みた）: NX-OS が既定で送る syslog の形。
  サードパーティの資料では `<PRI>hostname: 2026 Oct 10 12:00:00 UTC: %FACILITY-SEV-MNEMONIC: 本文` で、RFC 3164 の `Mmm dd hh:mm:ss` の時刻が無い。手元の syslog-ng ではこの形は `hostname` が appname になる（上の節）。
- 宛先ポートは 514/udp で変えられない。PC で 5140 へ送り直す（上の節）。

### SNMP trap

```text
snmp-server community public ro
snmp-server host <受け口の IP> traps version 2c public udp-port 162
snmp-server host <受け口の IP> use-vrf management udp-port 162
snmp-server source-interface traps mgmt0
snmp-server enable traps link linkDown linkUp cieLinkDown cieLinkUp
```

- `use-vrf` の行は、先に `traps` の行で宛先を作ってから入れる。
- community は lab と同じ `public`（v2c）。サンドボックスの中だけで使う。v3 なら `version 3 {auth|noauth|priv} <ユーザー>`（10.1(x) の SNMP のガイド）。
- `enable traps link` は種類を選べる（`linkDown` / `linkUp` が IETF の、`cieLinkDown` / `cieLinkUp` が Cisco 拡張の trap。設定ガイド）。
  lab の Splunk の保存済みサーチ `nwc_trap` は IETF の linkDown / linkUp（1.3.6.1.6.3.1.1.5.3 / .4）を見るので、両方出して形を比べる。
- `udp-port 162` は既定のままの明示（PC で 1162 へ送り直す）。PC の送り直しが要らないなら `udp-port 1162`。
- 未確認（2026-10-10 に確認を試みた）: `udp-port` を書かないときの既定のポート（設定ガイドのページに無い。162 の見込み）。

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
| syslog の形 | RFC 5424、local7 | 既定の形と `logging rfc-strict 5424` のときの形、hostname の位置、`sysName` に名前が入るか | 未確認 |
| trap の OID | linkDown / linkUp（1.3.6.1.6.3.1.1.5.3 / .4） | 同じ OID か、Cisco 拡張（cieLinkDown / cieLinkUp）か | 未確認 |
| trap の IF の名前 | ifName は `ethernet-1/N` | ifName が `Ethernet1/N` になるか（`lab_topology.py` の読み替えに要る） | 未確認 |

lab の gnmic の購読（`app/gnmic/gnmic.yaml.in`）と、NX-OS で当てる候補の対応:

| 購読の名前 | SR Linux のパス | 種類 | 書き先 | NX-OS の候補（未確認） |
|---|---|---|---|---|
| `interface_state` | `/interface[name=*]/oper-state`、`/interface[name=*]/admin-state` | on-change | `gnmi` | OpenConfig `/interfaces/interface[name=*]/state/oper-status`、`.../state/admin-status` |
| `interface_stats` | `/interface[name=*]/statistics` | sample 60 秒 | `metrics` | OpenConfig `/interfaces/interface[name=*]/state/counters` |
| `bgp_neighbor` | `/network-instance[name=default]/protocols/bgp/neighbor[peer-address=*]/session-state` | on-change | `gnmi` | OpenConfig `/network-instances/network-instance[name=default]/protocols/protocol[identifier=BGP][name=bgp]/bgp/neighbors/neighbor[neighbor-address=*]/state/session-state` |
| `isis_interface` | `/network-instance[name=default]/protocols/isis/instance[name=main]/interface[interface-name=*]/oper-state` | on-change | `gnmi` | OpenConfig の `protocol[identifier=ISIS][name=main]/isis/interfaces/interface[interface-id=*]/state`（NX-OS が IS-IS の OpenConfig を出すかは未確認。無ければ DME `sys/isis`） |
| `system` | `/platform/control[slot=*]/cpu[index=all]/total`、`/platform/control[slot=*]/memory` | sample 60 秒 | `metrics` | OpenConfig `/system/...` の CPU とメモリは NX-OS で未確認。DME `sys/procsys`（TechNote の MDT の例）が候補 |

- NX-OS の gNMI のパスは、まず `capabilities` で使えるモデルを見てから `get` で 1 つずつ試す。
  上の表の NX-OS 側のパスは候補で、どれも確かめていない。
- Spark（`app/spark/snmp_sinks.py`）は gnmic の event の `tags`（`interface_name`、`source`、`subscription-name`）と values の絶対パス（`/srl_nokia-interfaces:interface/statistics/in-octets` など）を読むので、NX-OS のパスで同じ値に落とすには読み替えが要る。CML ではまず形を取り、読み替えは組むサイクルで決める。
- trap は機器の IF を `shutdown` / `no shutdown` して出す。
  linkDown / linkUp のどの種類が出るかは、`snmp-server enable traps link` の種類の指定で変わる（設定ガイド）。

## AWS のパイプラインに流す

AWS のパイプラインは閉域（`IaC/terraform/aws-managed/base/core/perimeter.tf`。AWS の API は VPC エンドポイント、IAM は `aws:SourceVpc` の無い要求を Deny、MSK は VPC の中だけ）なので、PC から MSK に直接は書けない。
手元で輪を閉じるだけなら要らない。本番と同じ道（MSK → Spark → S3 Tables → Splunk / Grafana）で見たいときに、下の推奨の 1 案で流す。**どれも AWS では試していない（未確認）。**

### 推奨: lab の EC2 を踏み台にした SSM のトンネルで、compose の syslog-ng・gnmic を MSK の 9096 に向ける

IaC の変更が通信の表の 1 行で済み、新しい部品（VPN のエンドポイント・NAT・公開の口）を作らない。

```
PC の compose（syslog-ng / gnmic）─ 127.0.0.N:9096 ─ SSM のトンネル ─ lab の EC2 ─ MSK の b-N:9096（SASL_SSL / SCRAM）
```

手順:

1. IaC に 1 行足す（`IaC/terraform/aws-managed/base/core/security_groups.tf` の `sg_flows`。`only` を書かないので、lab の EC2 の SG に送信、MSK の SG に受信の規則ができる）:

   ```hcl
   { from = "lab", to = "msk", protocol = "tcp", port = 9096, why = "Kafka SCRAM - the PC's compose collectors through an SSM tunnel on the lab EC2 (docs/cml-sandbox.md)" },
   ```

   `PIPELINE=1 ops/up.sh`（か base の apply）で入れる。
2. SCRAM の bootstrap のアドレスを取る（デプロイする PC は Deny の対象外なので、PC から打てる）:

   ```bash
   aws kafka list-clusters-v2 --region ap-northeast-1 --query 'ClusterInfoList[].ClusterArn' --output text
   aws kafka get-bootstrap-brokers --region ap-northeast-1 --cluster-arn <ARN> --query BootstrapBrokerStringSaslScram --output text
   ```

   `b-1.<…>.kafka.ap-northeast-1.amazonaws.com:9096,b-2.<…>:9096`（ブローカーは `MSK_AZ_NUM` の数。既定 2）。
3. まず 1 本で疎通を見る。lab の EC2（`terraform output start_session_command` の instance id）で、SSM の `AWS-StartPortForwardingSessionToRemoteHost`（Session Manager の「Start a session」の文書。SSM Agent 3.1.1374.0 以上）:

   ```bash
   aws ssm start-session --region ap-northeast-1 --target <lab の instance id> \
     --document-name AWS-StartPortForwardingSessionToRemoteHost \
     --parameters '{"host":["b-1.<…>.kafka.ap-northeast-1.amazonaws.com"],"portNumber":["9096"],"localPortNumber":["9096"]}'
   ```

   手元の `localhost:9096` に TLS の握手が通れば（`openssl s_client -connect localhost:9096 -servername b-1.<…>`）、SG の行と経路は正しい。
4. ブローカー全部にトンネルを張る。Kafka のクライアントは bootstrap のあと各ブローカーの名乗るアドレス（`b-N…:9096`）へつなぎ直すので、1 本では足りない。
   SSM のポートフォワードは `localhost` にしか待てない（ローカルのアドレスを選べない）ので、ブローカーごとにループバックのアドレスを分けるには SSH over SSM（`AWS-StartSSHSession`。Session Manager の「Enable SSH connections」の文書）を使う:

   ```bash
   # lab の EC2 には鍵が無い（instance.tf に key_name が無い）ので、公開鍵を ec2-user の authorized_keys に手で入れる（SSM のシェルから）
   # ~/.ssh/config
   Host nwc-lab
     HostName <lab の instance id>
     User ec2-user
     ProxyCommand sh -c "aws ssm start-session --region ap-northeast-1 --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p'"
   ```

   ```bash
   ssh -N -L 127.0.0.2:9096:b-1.<…>:9096 -L 127.0.0.3:9096:b-2.<…>:9096 nwc-lab
   ```

   `/etc/hosts`（WSL2 のホスト側。compose は `network_mode: host`）に `127.0.0.2 b-1.<…>` と `127.0.0.3 b-2.<…>` を足す。名前を変えないので、MSK の証明書の検証がそのまま通る。
   `127.0.0.2` 以下のアドレスは Linux / WSL2 ではそのまま待てる。Mac は `sudo ifconfig lo0 alias 127.0.0.2` が要る。
5. compose の syslog-ng と gnmic の環境を替えて上げ直す（`docker/compose/compose.yaml` の値を上書きする。`compose.override.yaml` か環境変数。どちらにするかは組むときに決める）:

   | 変数 | 値 |
   |---|---|
   | `KAFKA_AUTH` | `scram`（`app/syslog-ng/syslog-ng.sh` と `app/gnmic/gnmic.sh` が対応。既定の `none` から替える） |
   | `KAFKA_BROKERS` | `b-1.<…>:9096,b-2.<…>:9096`（bootstrap の文字列そのまま） |
   | `KAFKA_SASL_USER` / `KAFKA_SASL_PASS` | Secrets Manager の `AmazonMSK_<prefix>-syslog-ng` / `-gnmic` の値。**人が Secrets Manager のコンソールで見て入れる。AI のセッションでは読まない・表示しない**（`CLAUDE.md`） |

   イメージは ECS と同じ（`docker/images/syslog-ng/Dockerfile`、gnmic）なので、TLS の CA はそのまま通る。
   MSK の ACL は Spark が起動時に SCRAM のユーザーごとに入れる（`app/spark/snmp_sinks.py` の `ensure_acls`）ので、AWS 側の Spark が動いていれば書ける。
6. AWS 側で見る: Kafbat UI の `logs` / `gnmi` / `metrics` に CML の機器の行が入り、Athena の `logs` にも落ちる。
   Spark の `DEVICE_MAP` に CML の管理 IP は無いので、`sysName` が空の行（NX-OS の既定の syslog）は機器名で引けない。`logging rfc-strict 5424` で名前を入れる（上の節）か、AWS 側の `DEVICE_MAP`（`ops/up.sh`）に足す。
7. 終わったら、トンネル（`ssh` / `aws ssm start-session`）を切り、`/etc/hosts` の行を消し、compose の環境を `none` / `localhost:9094,…` に戻し、IaC の 1 行を消して apply する（か `ops/down.sh`）。

- trap（Telegraf）と NetFlow / sFlow（GoFlow2）は MSK の IAM 認証（9098）で書く作り（`compose.yaml` の Telegraf の `KAFKA_AUTH` の既定は `iam`）。
  トンネル越しに IAM 認証で書けるかは未確認（PC の AWS の資格情報がコンテナに要る。デプロイする PC の資格情報は Deny の対象外）。まず syslog-ng と gnmic だけ流す。
- MDT は Kafka に書く形がまだ無い（受け口も無い）ので、流さない。
- SSM のポートフォワードは TCP だけの見込み（AWS の文書に UDP の記述が無い。未確認）。Kafka は TCP なので足りる。
- 費用の増分は無い（SSM の Session Manager は無料。lab の EC2 と MSK は立っている前提）。

### 見送った案

| 案 | 見送ったわけ |
|---|---|
| AWS Client VPN で PC を VPC に入れる | IaC が大きい（サーバー証明書と ACM、エンドポイント、サブネットの関連付け、認可の規則、SG の行、ルート）。エンドポイントの関連付けと接続の両方に時間課金（AWS の Client VPN の料金表）。PC で DevNet の VPN と AWS の VPN を同時に張ることになり、経路がぶつかりうる。閉域の Deny は IAM の話なので VPN では解けず、`aws:SourceVpc` が付く分だけ楽になるが、今回は AWS の API を PC から打たない |
| UDP の中継（PC → トンネル → lab の EC2 の socat → NLB の 5140 / 162） | SSM のトンネルは TCP だけの見込み（未確認）。EC2 で送り直すと送り元が EC2 の IP になり、trap の 162 は NLB の SG が `lab` の SG から受けない（syslog の 5140 は 037 から受ける）うえ、送り元の IP で機器を引けない（2026-10-09 の不具合 3 の所。[troubleshooting.md](troubleshooting.md)）。MDT の 57000 は NLB に無い |
| MSK の公開アクセス（public access）か、PC から届く口を開ける | 2026-09-28 の閉域の判断（VPC エンドポイント + Deny、NAT と外の Splunk を消した）に反する |
| 手元の Kafka から MSK へ MirrorMaker 2 / Kafka Connect で写す | 部品が 1 つ増え、MSK に届く問題は同じ（トンネルが要る） |
| B（CML の中の Linux）で受けて AWS へ送る | CML のノードから AWS の閉域へ届く道が無い |
| 旧 `terraform/` の NAT と外向きの経路に戻す | 2026-09-28 に消した。戻さない（プロジェクト別メモリ「PrivateLink の戻し点」） |

## 片付ける

1. 受けたデータ（`/tmp/cml-telegraf.out` など）のうち、表に書いたもの以外は消す。
2. AWS に流したなら、トンネルを切り、`/etc/hosts` と compose の環境を戻し、IaC の 1 行を消す（上の手順 7）。AWS の動作確認が終わったら `ops/down.sh`（消えたことを確かめて報告する）。
3. 手元の compose を止める（[docker/compose/README.md](../docker/compose/README.md) の消し方）。PC に足した iptables の REDIRECT の規則も消す。
4. VPN を切る。
5. Sandbox の予約を終える（予約の画面から終了する。ボタンの名前は未確認）。
   予約は 1 人 1 つなので、終えないと次が取れない。
6. シェルの履歴に `VIRL_PASSWORD` や SCRAM のパスワードが残っていないかを見る。

- 組むサイクルで、トポロジの YAML（CML の lab の書き出し）と NX-OS の設定を `app/containerlab/cml/` に入れる。パスワードと community は入れない（雛形は `<…>` のまま）。

## 関連リンク

どれも 2026-10-10 に開いた（開けなかったものはそう書く）。

| ページ | 確かめたこと |
|---|---|
| [CML の Sandbox](https://developer.cisco.com/docs/modeling-labs/sandbox/) | サンドボックスの 2 つの名前、サンプルのトポロジ |
| [Cisco Modeling Labs](https://developer.cisco.com/modeling-labs/) | 無料の 4 時間のセッション、CML Personal の 20 ノード |
| [Sandbox の Getting Started](https://developer.cisco.com/docs/sandbox/getting-started/) | 予約型と VPN、7 日まで、10〜40 分、Cisco Secure Client 5 と OpenConnect |
| [First Reservation Guide](https://developer.cisco.com/docs/sandbox/first-reservation-guide/) | 既定の 2 時間、VPN のメール |
| [Sandbox の FAQ](https://developer.cisco.com/docs/sandbox/faqs) | 既定は 7 日、同時の予約は 1 つ、長期は Community Forum、VPN の情報の出どころ |
| [CML の NX-OS 9000][cml-nxos] | 9000v / 9300v / 9500v、約 2.3 Mb/s、DHCP 不可、MTU は 9.3(3) から、mgmt0 と Eth1/x |
| [CML の External Connectors](https://developer.cisco.com/docs/modeling-labs/external-connectors/) | NAT と System Bridge の 2 つのモード、止めて wipe したノードだけ変えられる |
| [CML の NAT Mode (Default)](https://developer.cisco.com/docs/modeling-labs/nat-mode-default/) | `virbr0`、192.168.255.0/24、GW .1、DHCP .2〜.254、lab のノードが始める通信 |
| [virlutils](https://github.com/CiscoDevNet/virlutils) | `cml` CLI の入れ方と環境変数、`nxosv9000` の 8 GB・2 vCPU（テストのデータ） |
| [virl2-client](https://github.com/CiscoDevNet/virl2-client) | `pip3 install virl2_client`、版はコントローラーに合わせる、API の文書はコントローラーの Tools → Client Library |
| [cml-community](https://github.com/CiscoDevNet/cml-community) | `nxosv9000-lite`、サンドボックスの 2020 年のトポロジ |
| [Cisco Community: Nexus 9000 missing in CML sandbox](https://community.cisco.com/t5/devnet-sandbox/nexus-9000-missing-in-cml-sandbox/m-p/5239497)（非公式） | `nxosv9000` がサンドボックスから外されたという投稿（2026-10-10 は 403 で開けず、検索の抜き出しだけ） |
| [NX-OS 10.4(x) Model Driven Telemetry][nxos-mdt] | `telemetry` の書式、転送とエンコーディング、`sample-interval`、`certificate`、`use-chunking` / `use-compression` / `source-interface` |
| [TechNote 226393][tn-226393] | NX-OS 10.6(4) から Telegraf 1.40.0 へ gRPC・GPB、`show telemetry` |
| [NX-OS 10.6(x) gRPC Agent][nxos-grpc] | `feature grpc`、既定の 50051、管理の VRF |
| [NX-OS 10.4(x) gNMI][nxos-gnmi] | エンコーディングと origin、`show grpc gnmi` |
| [NX-OS 10.1(x) SNMP][nxos-snmp] | `snmp-server host` の書式（v2c / v3、`use-vrf`、`udp_port`）、`enable traps link` の種類 |
| [NX-OS 10.2(x) System Message Logging][nxos-syslog] | `logging server` の書式、local7、`rfc-strict 5424`、`origin-id`（**2026-10-10 は 404。10.4(x) / 10.1(x) の同じページも 404**。Cisco のガイドの一覧から辿り直す） |
| [nx-telemetry-proto](https://github.com/CiscoDevNet/nx-telemetry-proto) | MDT の GPB の .proto |
| [AxoSyslog: network()](https://www.axoflow.com/docs/axosyslog-core/chapter-sources/configuring-sources-network/) | `network()` は RFC 3164、RFC 5424 は `flags(syslog-protocol)` |
| [logger(1)](https://man7.org/linux/man-pages/man1/logger.1.html) | 2.26 から既定が RFC 5424、`--rfc3164` / `--rfc5424`、`-n` / `-P` / `-d` / `-T` |
| [Session Manager: Start a session](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html) | `AWS-StartPortForwardingSessionToRemoteHost`（`host` / `portNumber` / `localPortNumber`、SSM Agent 3.1.1374.0 以上） |
| [Session Manager: Enable SSH connections](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-getting-started-enable-ssh-connections.html) | `AWS-StartSSHSession` の `ProxyCommand` |
| [AWS Client VPN の料金](https://aws.amazon.com/vpn/pricing/) | エンドポイントの関連付けと接続の時間課金（見送った案） |

[cml-nxos]: https://developer.cisco.com/docs/modeling-labs/nx-os-9000
[nxos-mdt]: https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/104x/programmability/cisco-nexus-9000-series-nx-os-programmability-guide-104x/m-n9k-model-driven-telemetry-101x.html
[tn-226393]: https://www.cisco.com/c/en/us/support/docs/switches/nexus-9000-series-switches/226393-configure-and-verify-telemetry-on-nexus.html
[nxos-grpc]: https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/106x/programmability/cisco-nexus-9000-series-nx-os-programmability-guide-106x/m-grpc-agent.html
[nxos-gnmi]: https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/104x/programmability/cisco-nexus-9000-series-nx-os-programmability-guide-104x/m-gnmi.html
[nxos-snmp]: https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/101x/configuration/system-management/cisco-nexus-9000-series-nx-os-system-management-configuration-guide-101x/m-configuring-snmp.html
[nxos-syslog]: https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/102x/configuration/system-management/cisco-nexus-9000-series-nx-os-system-management-configuration-guide-102x/m-configuring-system-message-logging-10x.html

## 経緯

- 2026-10-10: 手順だけを書いた（`docs/cycles/QUEUE.md` の候補から）。サンドボックスの予約と NX-OS の設定は、まだ試していない。
- 2026-10-10: ガイドとして仕上げた（ユーザーの判断で docs だけ）。予約と VPN の公式の値、CML の操作（Web UI・`cml`・virl2-client）、ファブリックの設定の雛形（未確認）、compose で受ける道、AWS の閉域へ流す推奨案（SSM のトンネル）と見送った案を足した。組むのはこれから。
