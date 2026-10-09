"""TRex の stateless（STL）プロファイル: 機器の syslog（RFC 5424、local7）を UDP で撃ち続ける（後段の負荷試験用。中身は ../README.md）。

TRex のコンソールから:
    start -f /opt/nwc-trex/stl/udp_syslog.py --port 0 -t dst=203.0.113.1,pps=1000

-t で渡せるもの（全部省略可。値は文字列で届くので中で変換する。知らないキーは無視する）:
    dst       宛先 IP。既定は lab の EC2 の管理ネットワーク側（203.0.113.1）。SR Linux の remote-server と同じ宛先で、lab forward の DNAT が Telegraf の NLB へ向ける
    dport     宛先ポート。既定 5140（NLB もタスクも 5140）
    src       送り元 IP。既定は dc1-trex-01 の管理 IP（203.0.113.101）
    sport     送り元ポート。既定 514
    pps       1 ポートあたりの送信レート（packets/s）。既定 100
    host      RFC 5424 の HOSTNAME（Telegraf が sysName に付け替える）。既定 dc1-trex-01
    app       APP-NAME。既定 sr_bgp_mgr
    severity  0〜7。既定 5（notice。PRI は local7 = 23 と合わせて <189>）
    msg       本文。既定は BGP の隣接が落ちた体の 1 行（-t は「,」と「=」で区切るので、本文にその 2 つは入れられない。空白がコンソールで通るかは未確認）

TIMESTAMP は NILVALUE（"-"）にする。TRex は同じバイト列を撃ち続けるので、時刻を入れても試験の開始時刻のまま古くなるだけ
（Telegraf 1.40 の inputs.syslog は metric の時刻を受けた時刻にし、メッセージの時刻は field の timestamp に入れるだけ。syslog.go を 2026-10-08 に確認）。
TRex 2.41 のコンソールは CentOS 7 の Python 2.7 で動くことがあるので、Python 2 / 3 のどちらでも読める書き方にする。
TRex の API はこの手元に無いので、import は get_streams の中で遅延する。
"""

LOCAL7 = 23

DEFAULTS = {
    "dst": "203.0.113.1",
    "dport": "5140",
    "src": "203.0.113.101",
    "sport": "514",
    "pps": "100",
    "host": "dc1-trex-01",
    "app": "sr_bgp_mgr",
    "severity": "5",
    "msg": "BGP peer 10.255.0.1 (network-instance default) moved from established to idle (TRex load test)",
}


def syslog_payload(host="dc1-trex-01", app="sr_bgp_mgr", severity=5, msg=""):
    """RFC 5424 の 1 行（UDP の 1 データグラム = 1 メッセージ。末尾の改行は付けない）。"""
    severity = int(severity)
    if not 0 <= severity <= 7:
        raise ValueError("severity は 0〜7: %r" % severity)
    # <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID STRUCTURED-DATA [SP MSG]
    line = "<%d>1 - %s %s - - -" % (LOCAL7 * 8 + severity, host, app)
    return (line + " " + msg if msg else line).encode("utf-8")


class UdpSyslog(object):
    def get_streams(self, direction=0, **kwargs):
        from trex_stl_lib.api import IP, UDP, Ether, Raw, STLPktBuilder, STLStream, STLTXCont

        t = dict((k, str(kwargs.get(k, v))) for k, v in DEFAULTS.items())
        payload = syslog_payload(t["host"], t["app"], t["severity"], t["msg"])
        # Ether の MAC は TRex がポートの設定（default_gw の ARP）で埋める
        pkt = Ether() / IP(src=t["src"], dst=t["dst"]) / UDP(sport=int(t["sport"]), dport=int(t["dport"])) / Raw(payload)
        return [STLStream(packet=STLPktBuilder(pkt=pkt), mode=STLTXCont(pps=float(t["pps"])))]


def register():
    return UdpSyslog()
