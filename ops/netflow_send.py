"""NetFlow v5 のパケットを 1 つ（フローは 1 本）UDP で送る。GoFlow2 の受け口（cycle 012。2055/udp）を試す。

lab の SR Linux は NetFlow を出さない（sFlow だけ。コンテナ版で出るかは未確認）ので、GoFlow2 → Kafka の flows → Spark の経路はこれで確かめる。
標準ライブラリだけで動く（lab の EC2 のホストの python3 でも打てる）。この tools/ は tools Lambda の場所でもあるが、Lambda の zip に入るのは
IaC/terraform/aws-managed/workflow/gateway.tf の tools_files に書いたものだけなので、これは入らない。

    uv run python tools/netflow_send.py 127.0.0.1 2055     # 手元の compose の GoFlow2
    python3 tools/netflow_send.py <NLB の DNS 名>:2055       # AWS（lab の EC2 のホストから）

送るフロー: 10.0.0.1:12345 → 10.0.0.2:443 の TCP、10 パケット 8400 バイト、入力 if 1 → 出力 if 2。GoFlow2 の JSON では
type NETFLOW_V5 / src_addr 10.0.0.1 / dst_addr 10.0.0.2 / proto TCP（数でなく名前） / src_port 12345 / dst_port 443 / packets 10 / bytes 8400 / in_if 1 / out_if 2 になる。
"""

import socket
import struct
import sys
import time

DEFAULT_PORT = 2055
FLOW = {
    "src": "10.0.0.1",
    "dst": "10.0.0.2",
    "nexthop": "10.0.0.254",
    "in_if": 1,
    "out_if": 2,
    "packets": 10,
    "bytes": 8400,
    "src_port": 12345,
    "dst_port": 443,
    "tcp_flags": 0x18,  # PSH + ACK
    "proto": 6,  # TCP
}


def packet(now: float, uptime_ms: int = 60_000, seq: int = 1) -> bytes:
    """NetFlow v5 のヘッダー（24 バイト）とフロー 1 本（48 バイト）。数値はネットワークバイトオーダー。"""
    secs = int(now)
    header = struct.pack("!HHIIIIBBH", 5, 1, uptime_ms, secs, int((now - secs) * 1e9), seq, 0, 0, 0)
    f = FLOW
    record = struct.pack(
        "!4s4s4sHHIIIIHHBBBBHHBBH",
        socket.inet_aton(f["src"]), socket.inet_aton(f["dst"]), socket.inet_aton(f["nexthop"]),
        f["in_if"], f["out_if"], f["packets"], f["bytes"],
        uptime_ms - 1_000, uptime_ms,  # First / Last（sys_uptime からのミリ秒）
        f["src_port"], f["dst_port"], 0, f["tcp_flags"], f["proto"], 0,
        0, 0, 24, 24, 0,  # src_as / dst_as / src_mask / dst_mask / pad
    )
    return header + record


def target(argv: list[str]) -> tuple[str, int]:
    """<host> [port] か <host>:<port>。"""
    if not argv or len(argv) > 2:
        sys.exit("使い方: netflow_send.py <host> [port] または <host>:<port>（既定のポートは 2055）")
    host, port = argv[0], DEFAULT_PORT
    if len(argv) == 2:
        port = argv[1]
    elif host.count(":") == 1:
        host, port = host.split(":")
    try:
        port = int(port)
    except ValueError:
        sys.exit(f"ポートは数字: {port}")
    return host, port


def main() -> None:
    host, port = target(sys.argv[1:])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(packet(time.time()), (host, port))
    f = FLOW
    print(f"NetFlow v5 を 1 つ送った: {host}:{port}/udp（{f['src']}:{f['src_port']} → {f['dst']}:{f['dst_port']} proto {f['proto']}、"
          f"{f['packets']} パケット {f['bytes']} バイト）")


if __name__ == "__main__":
    main()
