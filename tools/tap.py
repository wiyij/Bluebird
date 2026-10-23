"""A07 取证工具：应用层抓包代理（tap）。

客户端不直连服务端，而是连 tap；tap 把字节原样转发给服务端，同时**按帧**
记录两个方向的数据。它本身也验证了一件事：我们的拆帧逻辑能吃住真实流量。

    py -3.13 -m tools.tap                       # 监听 8889，转发到 127.0.0.1:8888
    py -3.13 -m tools.tap --listen-port 9001 --target-port 9000

    # 客户端改连 tap 的端口
    py -3.13 -m client.cli --port 8889 --name alice

输出（同时写日志文件）：

    20:31:07.412  C→S   52 B  type=0x10 CHAT_GROUP   len=44
      43 48 01 10 00 00 00 2C  |  {"msg_id":"alice-000001","text":"大家好"}

为什么不用 Wireshark：本机没装（Npcap 在，但 tshark/Wireshark 都不在），
自建 tap 能拿到**同一批字节**，且不依赖管理员权限与网卡驱动。
装好 Wireshark 之后按 docs/test/D1.md 的三步可补一张 GUI 截图。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from shared.console import setup_console
from shared.protocol import HEADER, HEADER_SIZE, MAX_BODY, MsgType


def type_name(msg_type: int) -> str:
    try:
        return MsgType(msg_type).name
    except ValueError:
        return "UNKNOWN"


def split_frames(buf: bytearray):
    """从缓冲区里切出所有完整帧，剩下的留到下一次。"""
    while len(buf) >= HEADER_SIZE:
        _magic, _ver, _type, length = HEADER.unpack_from(buf)
        if length > MAX_BODY:
            raise ValueError(f"length={length} 超过上限，流已不可信")
        if len(buf) < HEADER_SIZE + length:
            return                      # 半包，等下一次读
        frame = bytes(buf[: HEADER_SIZE + length])
        del buf[: HEADER_SIZE + length]
        yield frame


class Recorder:
    def __init__(self, log_path: Path | None) -> None:
        self._file = log_path.open("w", encoding="utf-8") if log_path else None
        self.frames: list[tuple[str, bytes]] = []

    def record(self, direction: str, frame: bytes) -> None:
        _magic, _ver, msg_type, length = HEADER.unpack_from(frame)
        header = frame[:HEADER_SIZE].hex(" ").upper()
        body = frame[HEADER_SIZE:].decode("utf-8", "replace")
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = (
            f"{stamp}  {direction}  {len(frame):3d} B  "
            f"type=0x{msg_type:02X} {type_name(msg_type):<12} len={length}\n"
            f"    {header}  |  {body}"
        )
        self.frames.append((direction, frame))
        print(line, flush=True)
        if self._file:
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()


async def pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
               direction: str, recorder: Recorder) -> None:
    buf = bytearray()
    try:
        while data := await reader.read(65536):
            buf.extend(data)
            for frame in split_frames(buf):
                recorder.record(direction, frame)
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError, ValueError) as exc:
        print(f"[tap] {direction} 结束：{exc}", file=sys.stderr)
    finally:
        try:
            writer.close()
        except (ConnectionError, OSError):
            pass


async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter,
                 target: tuple[str, int], recorder: Recorder) -> None:
    peer = client_writer.get_extra_info("peername")
    print(f"[tap] 新连接 {peer}", file=sys.stderr)
    try:
        server_reader, server_writer = await asyncio.open_connection(*target)
    except OSError as exc:
        print(f"[tap] 连不上服务端 {target}：{exc}", file=sys.stderr)
        client_writer.close()
        return

    await asyncio.gather(
        pump(client_reader, server_writer, "C→S", recorder),
        pump(server_reader, client_writer, "S→C", recorder),
    )


async def serve(args: argparse.Namespace, recorder: Recorder) -> None:
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, (args.target_host, args.target_port), recorder),
        args.listen_host, args.listen_port,
    )
    print(f"[tap] 监听 {args.listen_host}:{args.listen_port} → "
          f"{args.target_host}:{args.target_port}", file=sys.stderr)
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(prog="tap", description="应用层抓包代理")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8889)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, default=8888)
    parser.add_argument("--log", type=Path, default=None, help="帧日志写到哪个文件")
    args = parser.parse_args(argv)

    recorder = Recorder(args.log)
    try:
        asyncio.run(serve(args, recorder))
    except KeyboardInterrupt:
        pass
    finally:
        recorder.close()
        print(f"[tap] 共记录 {len(recorder.frames)} 帧", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
