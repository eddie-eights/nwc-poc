"""TRex の stateless（STL）プロファイル: SNMPv2c の linkDown trap を UDP で撃ち続ける（後段の負荷試験用。中身は ../README.md）。

TRex のコンソールから:
    start -f /opt/nwc-trex/stl/udp_trap.py --port 0 -t dst=203.0.113.1,pps=1000

-t で渡せるもの（全部省略可。値は文字列で届くので中で変換する。知らないキーは無視する）:
    dst        宛先 IP。既定は lab の EC2 の管理ネットワーク側（203.0.113.1）。機器の trap と同じ宛先で、lab forward の DNAT が Telegraf の NLB へ向ける
    dport      宛先ポート。既定 162（NLB も 162 で受けてタスクの 1162 へ渡す。タスクへ直に撃つなら 1162）
    src        送り元 IP。既定は dc1-trex-01 の管理 IP（203.0.113.101。NLB の SG が通す管理ネットワークの中で、device_map にも載っている）
    sport      送り元ポート。既定 161
    pps        1 ポートあたりの送信レート（packets/s）。既定 100
    community  SNMPv2c の community。既定 public（containerlab が全ノードに入れる値）
    ifindex    varbind の ifIndex。既定 1
    ifname     varbind の ifName。既定 ethernet-1/1（Splunk の netops_trap が target に使う）

中身は IF-MIB の linkDown（snmpTrapOID .1.3.6.1.6.3.1.1.5.3）。varbind は sysUpTime.0 / snmpTrapOID.0 のあとに
linkDown の OBJECTS（ifIndex / ifAdminStatus = up / ifOperStatus = down）と ifName。BER はこのファイルで組む（TRex の scapy の版に依らない）。
TRex 2.41 のコンソールは CentOS 7 の Python 2.7 で動くことがあるので、Python 2 / 3 のどちらでも読める書き方にする（f-string と int.to_bytes を使わない）。
TRex の API はこの手元に無いので、import は get_streams の中で遅延する。scapy の層も trex_stl_lib.api から取る（TRex が同梱の scapy を sys.path に足すのはそこ）。
"""

LINK_DOWN = "1.3.6.1.6.3.1.1.5.3"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SNMP_TRAP_OID = "1.3.6.1.6.3.1.1.4.1.0"
IF_INDEX = "1.3.6.1.2.1.2.2.1.1"
IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"

DEFAULTS = {
    "dst": "203.0.113.1",
    "dport": "162",
    "src": "203.0.113.101",
    "sport": "161",
    "pps": "100",
    "community": "public",
    "ifindex": "1",
    "ifname": "ethernet-1/1",
}


def _bytes(xs):
    return bytes(bytearray(xs))


def _tlv(tag, body):
    n = len(body)
    if n < 0x80:
        head = [n]
    else:
        head = []
        while n:
            head.insert(0, n & 0xFF)
            n >>= 8
        head = [0x80 | len(head)] + head
    return _bytes([tag] + head) + body


def _int(v, tag=0x02):
    # INTEGER（0x02）と TimeTicks（0x43）。最短の 2 の補数（正の数で先頭ビットが立つときは 0x00 を足す）
    v = int(v)
    out = []
    while True:
        out.insert(0, v & 0xFF)
        v >>= 8
        if (v == 0 and not out[0] & 0x80) or (v == -1 and out[0] & 0x80):
            break
    return _tlv(tag, _bytes(out))


def _oid(s):
    a = [int(x) for x in s.split(".")]
    body = [40 * a[0] + a[1]]
    for x in a[2:]:
        chunk = [x & 0x7F]
        x >>= 7
        while x:
            chunk.insert(0, 0x80 | (x & 0x7F))
            x >>= 7
        body += chunk
    return _tlv(0x06, _bytes(body))


def _str(s):
    return _tlv(0x04, s if isinstance(s, bytes) else s.encode("utf-8"))


def _varbind(oid, value):
    return _tlv(0x30, _oid(oid) + value)


def trap_payload(community="public", ifindex=1, ifname="ethernet-1/1", uptime=100, request_id=1):
    """SNMPv2c の linkDown trap の UDP ペイロード（BER）。"""
    binds = b"".join([
        _varbind(SYS_UPTIME, _int(uptime, 0x43)),
        _varbind(SNMP_TRAP_OID, _oid(LINK_DOWN)),
        _varbind("%s.%d" % (IF_INDEX, ifindex), _int(ifindex)),
        _varbind("%s.%d" % (IF_ADMIN_STATUS, ifindex), _int(1)),
        _varbind("%s.%d" % (IF_OPER_STATUS, ifindex), _int(2)),
        _varbind("%s.%d" % (IF_NAME, ifindex), _str(ifname)),
    ])
    pdu = _tlv(0xA7, _int(request_id) + _int(0) + _int(0) + _tlv(0x30, binds))   # SNMPv2-Trap-PDU
    return _tlv(0x30, _int(1) + _str(community) + pdu)                            # version 1 = v2c


class UdpTrap(object):
    def get_streams(self, direction=0, **kwargs):
        from trex_stl_lib.api import IP, UDP, Ether, Raw, STLPktBuilder, STLStream, STLTXCont

        t = dict((k, str(kwargs.get(k, v))) for k, v in DEFAULTS.items())
        payload = trap_payload(t["community"], int(t["ifindex"]), t["ifname"])
        # Ether の MAC は TRex がポートの設定（default_gw の ARP）で埋める
        pkt = Ether() / IP(src=t["src"], dst=t["dst"]) / UDP(sport=int(t["sport"]), dport=int(t["dport"])) / Raw(payload)
        return [STLStream(packet=STLPktBuilder(pkt=pkt), mode=STLTXCont(pps=float(t["pps"])))]


def register():
    return UdpTrap()
