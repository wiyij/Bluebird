"""把 tap 日志里的真实载荷装进重建的 TCP 段，生成可在 Wireshark 里打开的 pcap。

**这不是实时抓包。** TCP/IP 头（端口、序号、校验和）是按五元组重建的，只有
payload 与真实链路逐字节相同——本机没有管理员权限，装不了 Npcap 抓包驱动，
所以用这种方式把已经抓到手的字节放进 Wireshark 里看。报告里引用时必须注明

    「载荷来自 tools/tap.py 的真实抓包，链路层／网络层头部为按五元组重建」

用法：

    py -3.13 tools/tap_to_pcap.py docs/pcap/D1-tap-v2.log docs/pcap/D1-replay.pcap
    py -3.13 tools/tap_to_pcap.py --date 2026-10-24 --server-port 8888 ...

Wireshark 里显示过滤器用 `tcp.port == 8888`；右键 Follow → TCP Stream 能还原成
两条完整会话。
"""

from __future__ import annotations

import argparse
import re
import struct
from datetime import datetime
from pathlib import Path

FRAME_RE = re.compile(
    r"^(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{3})\s+"
    r"(?P<dir>C→S|S→C)\s+(?P<size>\d+) B\s+type=(?P<type>0x[0-9A-Fa-f]{2})\s+\S*\s+len=(?P<len>\d+)"
)
#: tap 的日志行形如 ``    43 48 02 10 ...  |  {"msg_id":...}``：
#: 左边是 8 字节首部的十六进制，右边是正文的 UTF-8 文本。
HEX_RE = re.compile(r"^\s+((?:[0-9A-Fa-f]{2} )+)\s+\|\s{0,2}(?P<body>.*)$")

CLIENT_MAC = bytes.fromhex("020000000002")
SERVER_MAC = bytes.fromhex("020000000001")


def checksum(data: bytes) -> int:
    """RFC 1071 的 16 位反码和。"""
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def ip4(addr: str) -> bytes:
    return bytes(int(part) for part in addr.split("."))


def build_packet(payload: bytes, tcp: tuple[int, int, int, int, int, int],
                 src_ip: str, dst_ip: str) -> bytes:
    """拼一个以太网 + IPv4 + TCP 的帧。

    tcp = (src_port, dst_port, seq, ack, flags, window)
    """
    src_port, dst_port, seq, ack, flags, window = tcp
    src_mac, dst_mac = (CLIENT_MAC, SERVER_MAC) if src_port > dst_port else (SERVER_MAC, CLIENT_MAC)

    tcp_header = struct.pack(
        ">HHIIBBHHH", src_port, dst_port, seq, ack, 5 << 4, flags, window, 0, 0
    )
    pseudo = ip4(src_ip) + ip4(dst_ip) + struct.pack(">BBH", 0, 6, len(tcp_header) + len(payload))
    tcp_sum = checksum(pseudo + tcp_header + payload)
    tcp_header = tcp_header[:16] + struct.pack(">H", tcp_sum) + tcp_header[18:]

    total_len = 20 + len(tcp_header) + len(payload)
    ip_header = struct.pack(
        ">BBHHHBBH4s4s",
        0x45, 0, total_len, 0, 0x4000, 64, 6, 0, ip4(src_ip), ip4(dst_ip),
    )
    ip_sum = checksum(ip_header)
    ip_header = ip_header[:10] + struct.pack(">H", ip_sum) + ip_header[12:]

    ether = dst_mac + src_mac + struct.pack(">H", 0x0800)
    return ether + ip_header + tcp_header + payload


def parse_log(text: str, date: str) -> list[dict]:
    packets = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = FRAME_RE.match(line)
        if not match:
            continue
        if index + 1 >= len(lines):
            break
        hex_match = HEX_RE.match(lines[index + 1])
        if not hex_match:
            continue
        # 首部 8 字节原样取十六进制；正文取日志里的 UTF-8 文本再按原编码写回。
        # 两者拼起来就是 tap 当时转发的完整一帧（只有对端发来的非法 UTF-8 会有损）。
        header = bytes.fromhex(hex_match.group(1).replace(" ", ""))
        body = hex_match.group("body").encode("utf-8")
        payload = header + body
        declared = int(match["len"])
        if len(body) != declared:
            raise ValueError(
                f"第 {index + 1} 行：正文声明 {declared} 字节，日志文本重编码后 {len(body)} 字节，"
                "多半是含非法 UTF-8，无法无损还原"
            )
        stamp = datetime.strptime(
            f"{date} {match['h']}:{match['m']}:{match['s']}.{match['ms']}",
            "%Y-%m-%d %H:%M:%S.%f",
        )
        packets.append({
            "time": stamp,
            "direction": match["dir"],
            "declared": int(match["size"]),
            "payload": payload,
        })
    return packets


def write_pcap(packets: list[dict], out: Path, client_ip: str, server_ip: str,
               client_port: int, server_port: int) -> int:
    seq = {"C→S": 1000, "S→C": 9000}
    ack = {"C→S": 9000, "S→C": 1000}
    frames = 0
    with out.open("wb") as fh:
        fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for packet in packets:
            outbound = packet["direction"] == "C→S"
            src_ip, dst_ip = (client_ip, server_ip) if outbound else (server_ip, client_ip)
            sport, dport = (client_port, server_port) if outbound else (server_port, client_port)
            frame = build_packet(
                packet["payload"],
                (sport, dport, seq[packet["direction"]], ack[packet["direction"]], 0x18, 65535),
                src_ip, dst_ip,
            )
            seq[packet["direction"]] += len(packet["payload"])
            ack[packet["direction"]] = seq["S→C" if outbound else "C→S"]
            sec = int(packet["time"].timestamp())
            usec = packet["time"].microsecond
            fh.write(struct.pack("<IIII", sec, usec, len(frame), len(frame)))
            fh.write(frame)
            frames += 1
    return frames


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tap_to_pcap", description="tap 日志 → 可读 pcap（载荷原样，头部重建）")
    parser.add_argument("log", type=Path, help="tap 日志")
    parser.add_argument("out", type=Path, help="输出的 .pcap")
    parser.add_argument("--date", default="2026-10-24", help="日志日期（日志里只有时分秒）")
    parser.add_argument("--client-ip", default="127.0.0.1")
    parser.add_argument("--server-ip", default="127.0.0.1")
    parser.add_argument("--client-port", type=int, default=54321)
    parser.add_argument("--server-port", type=int, default=8888)
    args = parser.parse_args(argv)

    packets = parse_log(args.log.read_text(encoding="utf-8", errors="replace"), args.date)
    if not packets:
        print("日志里没解析出帧：检查是不是 tap 的输出格式")
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    frames = write_pcap(packets, args.out, args.client_ip, args.server_ip,
                        args.client_port, args.server_port)
    total = sum(len(p["payload"]) for p in packets)
    print(f"{frames} 帧，载荷合计 {total} 字节 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
