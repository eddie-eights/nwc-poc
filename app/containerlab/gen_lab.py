"""Spine-Leaf の lab（containerlab のトポロジ + Nokia SR Linux の設定）を作る。splab.clab.yml.in と srlinux/<機器>.cli の唯一の元。

構成（既定。すべて架空のアドレス）:
    dc1-s-leaf-01  dc1-s-leaf-02      ← s-leaf（WAN 側）
       │  ╲    ╱  │
    dc1-spine-01  dc1-spine-02        ← Spine（BGP EVPN のルートリフレクタ）
       │  ╱    ╲  │
    dc1-a-leaf-01  dc1-a-leaf-02      ← a-leaf（DC 側）
    dc1-trex-01（TRex。eth1〜eth4 を各 leaf の ethernet-1/<spine の数 + 1> へ 1 本ずつ）

  - 物理層: spine と a-leaf / s-leaf は full mesh（spine 側 ethernet-1/<leaf の番号>、leaf 側 ethernet-1/<spine の番号>）。
    TRex のポートは s-leaf、a-leaf の順に eth1 から。leaf 側はその口を mac-vrf の素の subinterface（bridged）にする（LAG も ES も無い）
  - IP 層: fabric は /31（172.16.x.y）、ループバック system0 は 10.255.<段>.<番号>/32。IS-IS（instance main、L2、point-to-point）で配る
  - EVPN/BGP 層: iBGP AS 65100、EVPN の AFI だけ。spine がルートリフレクタで a-leaf / s-leaf がクライアント（ループバック同士で張る）。
    mac-vrf macvrf-100（EVI 100、VNI 100、RT target:65100:100）に TRex の口を入れ、VXLAN（vxlan1.100）で leaf のあいだを通す。
    TRex のポートは全部同じ L2（10.100.0.0/24）に見える
  - 監視: SR Linux 6 台とも SNMP の trap（linkDown / linkUp）と syslog を 203.0.113.1（lab の EC2）へ。gNMI（57400）は containerlab が有効にする
  - TRex（trexcisco/trex。amd64 だけ）はトポロジを上げても起動しない。lab.sh trex start が /etc/trex_cfg.yaml を書いて t-rex-64 -i を起こす
    （app/containerlab/trex/README.md）

  SR-MPLS は SR Linux のコンテナでは ixr6e / ixr10e（ライセンスが要る）だけなので、いまは VXLAN。ライセンスが来たら `type:` を ixr6e にして
  トンネルを SR-MPLS に替える（docs/pipeline.md「lab」）。

台数を増やす（大規模化の練習）: python3 app/containerlab/gen_lab.py --leaves 4 --spines 3
  a-leaf は偶数台（TRex のポートは 2 本 1 組なので、s-leaf 2 台と合わせて偶数にする）。s-leaf はいつも 2 台、TRex はいつも 1 台で、
  leaf が増えるとポートが増える。書き出した結果はそのまま
  lab_topology.py が読んで Neptune / Telegraf / Spark に配る（機器の一覧は lab の定義 1 か所）。既定の出力は git に入れてあり、
  tests/test_sync.py が「既定で作り直しても同じ」ことを確かめる（手で直すとテストが落ちる。直すならここを直して作り直す）。

使い方: python3 app/containerlab/gen_lab.py [--leaves N] [--spines N] [--out <lab のディレクトリ>]
"""

import argparse
import os

MGMT_SUBNET = "203.0.113.0/24"
MGMT_GW = "203.0.113.1"        # lab の EC2 側（trap / syslog の宛先）。lab.sh の MGMT_GW と同じ
LOG_PORT = 5140                # 機器の syslog の宛先ポート。lab.sh の LOG_PORT と telegraf.conf.in の inputs.syslog と同じ
LOG_FACILITY = "local7"        # 機器の syslog のファシリティ。本番の Cisco（IOS の既定）に合わせる
AS = 65100
EVI = 100
VNI = 100
L2_SUBNET = "10.100.0"         # TRex のポートが乗る L2（macvrf-100）。アドレスは trex/trex_cfg.yaml.in（lab.sh trex start が書く）
PORT_SPEED = "25G"             # ixr-d2l の ethernet-1/1〜48 は 25G
BANDWIDTH = 25000
TREX = "dc1-trex-01"


def mgmt_ip(n: int) -> str:
    return f"203.0.113.{n}"


class Switch:
    def __init__(self, name, tier, index, mgmt, loopback):
        self.name, self.tier, self.index, self.mgmt, self.loopback = name, tier, index, mgmt, loopback
        self.ifaces = []      # (ifname, description, address/len, peer)  fabric の /31
        self.edge = None      # (ifname, TRex のポート)  mac-vrf に入れる下向きの口

    @property
    def net(self) -> str:
        return f"49.0001.0000.0000.{int(self.mgmt.rsplit('.', 1)[1]):04d}.00"


def plan(leaves: int, spines: int):
    """機器と回線を決める。戻り値は (switches, trex, links)。trex は (name, mgmt, [TRex のポート])、
    links は [(a, a_if, b, b_if, comment)]（containerlab の endpoints）"""
    if leaves < 2 or leaves % 2 or spines < 1:
        raise SystemExit("--leaves は 2 以上の偶数（TRex のポートは 2 本 1 組）、--spines は 1 以上")
    s_leaf = [Switch(f"dc1-s-leaf-{i + 1:02d}", "s-leaf", i, mgmt_ip(11 + i), f"10.255.1.{i + 1}") for i in range(2)]
    spine = [Switch(f"dc1-spine-{i + 1:02d}", "spine", i, mgmt_ip(21 + i), f"10.255.0.{i + 1}") for i in range(spines)]
    a_leaf = [Switch(f"dc1-a-leaf-{i + 1:02d}", "a-leaf", i, mgmt_ip(31 + i), f"10.255.2.{i + 1}") for i in range(leaves)]
    if spines > 8 or leaves > 60:
        raise SystemExit("spine は 8 台まで、leaf は 60 台まで（管理アドレスとポートの割り当ての都合）")
    downs = s_leaf + a_leaf   # spine から見た下側（ポートの順）
    links = []
    for s in spine:
        for j, d in enumerate(downs):
            i = s.index * len(downs) + j
            s_addr, d_addr = f"172.16.{i // 128}.{(2 * i) % 256}", f"172.16.{i // 128}.{(2 * i + 1) % 256}"
            s.ifaces.append((f"ethernet-1/{j + 1}", f"fabric to {d.name} {PORT_SPEED}", f"{s_addr}/31", d.name))
            d.ifaces.append((f"ethernet-1/{s.index + 1}", f"fabric to {s.name} {PORT_SPEED}", f"{d_addr}/31", s.name))
            links.append((s.name, f"e1-{j + 1}", d.name, f"e1-{s.index + 1}", f"{s_addr}/31 - {d_addr}/31"))
    host_port = spines + 1
    ports = []
    for n, sw in enumerate(downs):   # TRex 1 台のポートを s-leaf、a-leaf の順に 1 本ずつ
        port = f"eth{n + 1}"
        sw.edge = (f"ethernet-1/{host_port}", port)
        ports.append(port)
        links.append((TREX, port, sw.name, f"e1-{host_port}", f"TRex port {n} ({port}) - {sw.name} mac-vrf"))
    return s_leaf + spine + a_leaf, (TREX, mgmt_ip(101), ports), links


# ---------------------------------------------------------------- SR Linux の設定（`set /` の行だけ）
def srl_config(sw: Switch, switches: list) -> str:
    lines = [f"set / system name host-name {sw.name}"]
    for ifn, desc, addr, _ in sw.ifaces:
        lines += [
            f'set / interface {ifn} description "{desc}"',
            f"set / interface {ifn} admin-state enable",
            f"set / interface {ifn} subinterface 0 admin-state enable",
            f"set / interface {ifn} subinterface 0 ipv4 admin-state enable",
            f"set / interface {ifn} subinterface 0 ipv4 address {addr}",
        ]
    if sw.edge:
        ifn, port = sw.edge
        lines += [
            f'set / interface {ifn} description "to {TREX} {port} {PORT_SPEED}"',
            f"set / interface {ifn} admin-state enable",
            f"set / interface {ifn} subinterface 0 type bridged",
            f"set / interface {ifn} subinterface 0 admin-state enable",
        ]
    lines += [
        "set / interface system0 admin-state enable",
        "set / interface system0 subinterface 0 admin-state enable",
        "set / interface system0 subinterface 0 ipv4 admin-state enable",
        f"set / interface system0 subinterface 0 ipv4 address {sw.loopback}/32",
    ]
    if sw.edge:
        lines += [
            f"set / tunnel-interface vxlan1 vxlan-interface {VNI} type bridged",
            f"set / tunnel-interface vxlan1 vxlan-interface {VNI} ingress vni {VNI}",
        ]
    # underlay（default）: IS-IS と、EVPN の iBGP
    lines += ["set / network-instance default type default"]
    lines += [f"set / network-instance default interface {ifn}.0" for ifn, *_ in sw.ifaces]
    lines += [
        "set / network-instance default interface system0.0",
        "set / network-instance default protocols isis instance main admin-state enable",
        "set / network-instance default protocols isis instance main level-capability L2",
        f"set / network-instance default protocols isis instance main net [ {sw.net} ]",
        "set / network-instance default protocols isis instance main ipv4-unicast admin-state enable",
    ]
    for ifn, *_ in sw.ifaces:
        lines += [
            f"set / network-instance default protocols isis instance main interface {ifn}.0 circuit-type point-to-point",
            f"set / network-instance default protocols isis instance main interface {ifn}.0 ipv4-unicast admin-state enable",
        ]
    lines += [
        "set / network-instance default protocols isis instance main interface system0.0 passive true",
        "set / network-instance default protocols isis instance main interface system0.0 ipv4-unicast admin-state enable",
        f"set / network-instance default protocols bgp autonomous-system {AS}",
        f"set / network-instance default protocols bgp router-id {sw.loopback}",
        "set / network-instance default protocols bgp afi-safi evpn admin-state enable",
        "set / network-instance default protocols bgp afi-safi ipv4-unicast admin-state disable",
        f"set / network-instance default protocols bgp group overlay peer-as {AS}",
        "set / network-instance default protocols bgp group overlay afi-safi evpn admin-state enable",
        "set / network-instance default protocols bgp group overlay afi-safi ipv4-unicast admin-state disable",
        f"set / network-instance default protocols bgp group overlay transport local-address {sw.loopback}",
        "set / network-instance default protocols bgp group overlay timers keepalive-interval 10",
        "set / network-instance default protocols bgp group overlay timers hold-time 30",
    ]
    if sw.tier == "spine":
        lines += [
            "set / network-instance default protocols bgp group overlay route-reflector client true",
            f"set / network-instance default protocols bgp group overlay route-reflector cluster-id {sw.loopback}",
        ]
        peers = [s for s in switches if s.tier != "spine"]
    else:
        peers = [s for s in switches if s.tier == "spine"]
    for p in peers:
        lines += [
            f"set / network-instance default protocols bgp neighbor {p.loopback} peer-group overlay",
            f'set / network-instance default protocols bgp neighbor {p.loopback} description "{p.name}"',
        ]
    # overlay（mac-vrf）: TRex の口を EVI 100 に入れ、VXLAN で他の leaf と結ぶ
    if sw.edge:
        ni = f"macvrf-{EVI}"
        lines += [
            f"set / network-instance {ni} type mac-vrf",
            f"set / network-instance {ni} admin-state enable",
            f'set / network-instance {ni} description "EVI {EVI} (VNI {VNI}) TRex ports L2 {L2_SUBNET}.0/24"',
            f"set / network-instance {ni} interface {sw.edge[0]}.0",
            f"set / network-instance {ni} vxlan-interface vxlan1.{VNI}",
            f"set / network-instance {ni} protocols bgp-evpn bgp-instance 1 admin-state enable",
            f"set / network-instance {ni} protocols bgp-evpn bgp-instance 1 vxlan-interface vxlan1.{VNI}",
            f"set / network-instance {ni} protocols bgp-evpn bgp-instance 1 evi {EVI}",
            f"set / network-instance {ni} protocols bgp-evpn bgp-instance 1 ecmp 2",
            f"set / network-instance {ni} protocols bgp-vpn bgp-instance 1 route-target export-rt target:{AS}:{EVI}",
            f"set / network-instance {ni} protocols bgp-vpn bgp-instance 1 route-target import-rt target:{AS}:{EVI}",
        ]
    # 監視: trap と syslog を lab の EC2（lab.sh forward が Telegraf へ DNAT）へ
    lines += [
        "set / system snmp network-instance mgmt admin-state enable",
        "set / system snmp trap-group telegraf admin-state enable",
        "set / system snmp trap-group telegraf network-instance mgmt",
        "set / system snmp trap-group telegraf destination telegraf admin-state enable",
        f"set / system snmp trap-group telegraf destination telegraf address {MGMT_GW}",
        "set / system snmp trap-group telegraf destination telegraf security-level no-auth-no-priv",
        "set / system snmp trap-group telegraf destination telegraf community-entry telegraf community public",  # 名前を community と同じにすると SR Linux が commit を拒む（2026-09-26 実測）
        "set / system logging network-instance mgmt",
        # 本番は Cisco（IOS の既定は local7）を想定するので、SR Linux の subsystem のログも local7 で出す（SR Linux の既定は local6）
        f"set / system logging subsystem-facility {LOG_FACILITY}",
        f"set / system logging remote-server {MGMT_GW} transport udp",
        f"set / system logging remote-server {MGMT_GW} remote-port {LOG_PORT}",
    ]
    for sub in ("bgp", "chassis", "evpn", "isis", "linux", "netinst", "xdp"):
        lines.append(f"set / system logging remote-server {MGMT_GW} subsystem {sub} priority match-above informational")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- containerlab のトポロジ（テンプレート）
def clab_template(switches: list, trex: tuple, links: list, leaves: int, spines: int) -> str:
    tier_ja = {"s-leaf": "s-leaf（WAN 側）", "spine": "Spine（EVPN のルートリフレクタ）", "a-leaf": "a-leaf（DC 側）"}
    tname, tmgmt, tports = trex
    host_port = spines + 1
    out = [
        "# Spine-Leaf の DC ファブリック（IS-IS underlay + iBGP EVPN + VXLAN。すべて架空のアドレス）。app/containerlab/gen_lab.py が作る。**手で直さない**",
        f"# （python3 app/containerlab/gen_lab.py --leaves {leaves} --spines {spines} で作り直す。既定の出力は tests/test_sync.py が確かめる）。",
        "#",
        "# これはテンプレート。__SRLINUX_IMAGE__ などは lab.sh render が ECR の URI に置き換えて splab.clab.yml を作る。",
        "# スイッチは Nokia SR Linux（kind nokia_srlinux、type ixr-d2l = ライセンス不要。SR-MPLS にするときは ixr6e + ライセンス）。",
        "# 設定は srlinux/<機器名>.cli（`set /` の行だけ。containerlab が candidate に流して commit save する）。",
        "# SNMP（v2c、community public）と gNMI（57400、TLS）は containerlab が SR Linux で有効にする。SR Linux 6 台とも trap（linkDown / linkUp）と",
        f"# syslog の宛先を {MGMT_GW}（この管理網の EC2 側）に向け、lab.sh forward がそれを Telegraf（ECS）の NLB へ DNAT する。",
        "#",
        "#               dc1-s-leaf-01 / dc1-s-leaf-02        （s-leaf。WAN 側）",
        "#                          ╲  ╳  ╱",
        "#                   dc1-spine-01 ... dc1-spine-0N        （full mesh。/31 + IS-IS）",
        "#                          ╱  ╳  ╲",
        "#               dc1-a-leaf-01 / dc1-a-leaf-02        （a-leaf。DC 側）",
        f"#   {tname}: eth1〜eth{len(tports)} を各 leaf の e1-{host_port} へ 1 本ずつ（leaf 側は mac-vrf の素の subinterface。LAG / ES は無い）",
        "#",
        "# 機器名は <site>-<role>-<連番2桁>（role = s-leaf / spine / a-leaf / trex）。SR Linux の host-name（= SNMP の sysName、syslog の",
        "# hostname）も同じ値なので、collector は SNMP 側の sysName で機器を突き合わせられる。",
        "# インタフェースは containerlab の endpoints では e1-N、SR Linux の設定と ifTable（ifName）では ethernet-1/N。lab_topology.py が読み替える。",
        "name: splab",
        "",
        "mgmt:",
        "  # 名前と subnet を固定する（EC2 の中だけの docker network）。VPC からは Telegraf のタスクだけが、VPC のルートで lab の EC2 を経由して届く",
        "  # （IaC/terraform/aws-managed/pipeline/lab の locals.tf の mgmt_cidr と lab.sh の MGMT が同じ subnet）",
        "  network: nwc-lab",
        f"  ipv4-subnet: {MGMT_SUBNET}",
        "",
        "topology:",
        "  defaults:",
        "    env:",
        "      TZ: Asia/Tokyo",
        "",
        "  kinds:",
        "    nokia_srlinux:",
        "      image: __SRLINUX_IMAGE__",
        "      type: ixr-d2l",
        "    linux:",
        "      image: __MULTITOOL_IMAGE__",
        "",
        "  nodes:",
    ]
    for tier in ("s-leaf", "spine", "a-leaf"):
        out.append(f"    # ---------- {tier_ja[tier]} ----------")
        for sw in [s for s in switches if s.tier == tier]:
            out += [
                f"    {sw.name}:",
                "      kind: nokia_srlinux",
                f"      group: {tier}",
                f"      mgmt-ipv4: {sw.mgmt}",
                f"      startup-config: srlinux/{sw.name}.cli",
                "",
            ]
    out += [
        "    # ---------- TRex（linux。af_packet でポートを直接使うので privileged。本体は lab.sh trex start で起こす）----------",
        f"    {tname}:",
        "      kind: linux",
        "      image: __TREX_IMAGE__",
        "      group: trex",
        f"      mgmt-ipv4: {tmgmt}",
        "      privileged: true",
        "      exec:",
    ]
    out += [f"        - ip link set {p} up" for p in tports]
    out += ["", "  links:", "    # fabric（spine - a-leaf / s-leaf の /31。SR Linux 側の e1-N は設定の ethernet-1/N）"]
    for a, a_if, b, b_if, comment in links:
        if a.startswith("dc1-spine-"):
            out.append(f'    - endpoints: ["{a}:{a_if}", "{b}:{b_if}"]   # {comment}')
    out.append("    # TRex のポート（各 leaf へ 1 本）")
    for a, a_if, b, b_if, comment in links:
        if not a.startswith("dc1-spine-"):
            out.append(f'    - endpoints: ["{a}:{a_if}", "{b}:{b_if}"]   # {comment}')
    return "\n".join(out) + "\n"


def generate(leaves: int, spines: int) -> dict:
    """{相対パス: 中身}。splab.clab.yml.in と srlinux/<機器>.cli"""
    switches, trex, links = plan(leaves, spines)
    files = {"splab.clab.yml.in": clab_template(switches, trex, links, leaves, spines)}
    for sw in switches:
        files[f"srlinux/{sw.name}.cli"] = srl_config(sw, switches)
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--leaves", type=int, default=2, help="a-leaf（DC 側）の数（偶数。既定 2）")
    ap.add_argument("--spines", type=int, default=2, help="spine の数（既定 2）")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)), help="書き出す lab のディレクトリ（既定はこのファイルの場所）")
    a = ap.parse_args()
    files = generate(a.leaves, a.spines)
    srl_dir = os.path.join(a.out, "srlinux")
    os.makedirs(srl_dir, exist_ok=True)
    for fn in os.listdir(srl_dir):   # 前の台数の設定を残さない
        if fn.endswith(".cli") and f"srlinux/{fn}" not in files:
            os.remove(os.path.join(srl_dir, fn))
    for rel, text in files.items():
        with open(os.path.join(a.out, rel), "w", encoding="utf-8") as f:
            f.write(text)
    print(f"{a.out}: {len(files)} ファイル（SR Linux {len(files) - 1} 台）")


if __name__ == "__main__":
    main()
