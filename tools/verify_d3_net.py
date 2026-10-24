"""D3 收尾 · T13 换网段复验：不经过 127.0.0.1，走真实网卡。

T13 原文：「换一台机器（不同网段）启动服务端，客户端改 IP → 能连上并互发，无需改代码」。

一台机器能验的部分（本脚本）：把服务端绑在 0.0.0.0，客户端连**本机的局域网 IP**，
看它到底有没有经过真实网卡。能验三条：
  ① 代码里没有写死 127.0.0.1；② 服务端确实监听在 0.0.0.0；③ 对端地址是局域网 IP 而不是回环。

一台机器验不了的部分：真正的「另一台机器」。那一步只能现场用第二台设备做。

    py -3.13 tools/verify_d3_net.py                 # 自动取本机局域网 IP
    py -3.13 tools/verify_d3_net.py --host 10.0.0.5 # 指定地址

结果写 docs/test/D3-net-evidence.txt，退出码 0 表示全过。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.server import ChatServer                     # noqa: E402
from shared.config import Config                         # noqa: E402
from shared.protocol import MsgType, encode_frame, read_frame  # noqa: E402
from tools.procutil import free_port, python_exe, terminate_tree, wait_port  # noqa: E402

OUT: list[str] = []
OK: list[str] = []
BAD: list[str] = []


def log(line: str = "") -> None:
    OUT.append(line)


def check(tag: str, ok: bool, detail: str = "") -> None:
    (OK if ok else BAD).append(tag)
    log(f"[{'PASS' if ok else 'FAIL'}] {tag}  {detail}")


def lan_ip() -> str:
    """取本机「会被用来上网」的那个地址（UDP connect 不会真的发包）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        pass
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        addr = info[4][0]
        if not addr.startswith(("127.", "169.254.")):
            return addr
    return "127.0.0.1"


class Conn:
    def __init__(self, name: str, reader, writer, host: str) -> None:
        self.name = name
        self.host = host
        self.reader = reader
        self.writer = writer
        self.skipped: list[int] = []

    async def send(self, msg_type: MsgType, body: dict | None = None) -> None:
        self.writer.write(encode_frame(msg_type, body or {}))
        await self.writer.drain()

    async def until(self, wanted: MsgType, timeout: float = 3.0) -> dict:
        while True:
            msg_type, body = await asyncio.wait_for(read_frame(self.reader), timeout)
            if msg_type == wanted:
                return body
            self.skipped.append(msg_type)

    async def login(self) -> dict:
        await self.send(MsgType.LOGIN_REQ, {
            "username": self.name, "client_ver": "2.0", "color": "black",
        })
        resp = await self.until(MsgType.LOGIN_RESP)
        await self.until(MsgType.USER_LIST)
        return resp

    async def close(self) -> None:
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def open_conn(host: str, port: int, name: str) -> Conn:
    reader, writer = await asyncio.open_connection(host, port)
    return Conn(name, reader, writer, host)


async def part_a(host: str) -> None:
    """进程内服务端：能顺便看到服务端眼里的对端地址。"""
    port = free_port()
    srv = ChatServer(Config(host="0.0.0.0", port=port, log_level="CRITICAL"))
    await srv.start()
    alice = bob = carol = None
    try:
        alice = await open_conn(host, port, "alice")
        resp = await alice.login()
        check(f"经 {host} 登录成功（不是 127.0.0.1）", resp.get("code") == 0,
              f"code={resp.get('code')}")

        peer = srv._clients["alice"].peer
        check("服务端看到的对端地址是同网段的局域网 IP",
              bool(peer) and peer[0] == host, f"peer={peer}")
        check("服务端确实监听在 0.0.0.0（不是只绑回环）",
              any(sock.getsockname()[0] == "0.0.0.0" for sock in srv._server.sockets),
              str([sock.getsockname() for sock in srv._server.sockets]))

        bob = await open_conn(host, port, "bob")
        await bob.login()
        carol = await open_conn(host, port, "carol")
        await carol.login()
        check("三端同时在线（走局域网 IP）", srv.client_count == 3,
              f"在线 {srv.client_count} 人")

        await alice.send(MsgType.CHAT_GROUP, {"msg_id": "a-1", "text": "换网后还能聊吗"})
        got = await carol.until(MsgType.CHAT_GROUP)
        check("群聊经真实网卡送达", got.get("text") == "换网后还能聊吗",
              f"seq={got.get('seq')}")

        await alice.send(MsgType.CHAT_PRIVATE, {
            "msg_id": "a-2", "to": "bob", "text": "悄悄话",
        })
        priv = await bob.until(MsgType.CHAT_PRIVATE)
        await carol.send(MsgType.PING)
        await carol.until(MsgType.PONG)
        check("私聊仍然隔离", priv.get("from") == "alice"
              and MsgType.CHAT_PRIVATE not in carol.skipped,
              f"carol 收到 {[MsgType(t).name for t in carol.skipped]}")

        await alice.send(MsgType.SET_COLOR, {"color": "green"})
        color = await carol.until(MsgType.USER_COLOR)
        check("改色广播经真实网卡送达", color == {"nickname": "alice", "color": "green"},
              str(color))

        loopback = await open_conn("127.0.0.1", port, "dave")
        ok = (await loopback.login()).get("code") == 0
        await loopback.send(MsgType.LOGOUT)
        await asyncio.sleep(0.1)
        await loopback.close()
        check("同一个服务端，127.0.0.1 也照样能连（两种地址都行）", ok, "code=0")
    finally:
        for conn in (alice, bob, carol):
            if conn is not None:
                await conn.close()
        await asyncio.sleep(0.1)
        await srv.close()


def part_b(host: str) -> None:
    """真实入口 `python -m server.server`：证明发布形态也是绑 0.0.0.0。"""
    port = free_port()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    server = subprocess.Popen([python_exe(), "-m", "server.server", "--port", str(port)],
                              cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              env=env)
    try:
        if not wait_port(port):
            check("python -m server.server 起得来（回环探测）", False, "端口没开")
            return
        check("python -m server.server 起得来（回环探测）", True, f"port={port}")

        async def probe() -> bool:
            try:
                reader, writer = await asyncio.open_connection(host, port)
            except OSError as exc:
                log(f"    连 {host}:{port} 失败：{exc}")
                return False
            writer.write(encode_frame(MsgType.LOGIN_REQ, {
                "username": "probe", "client_ver": "2.0",
            }))
            await writer.drain()
            try:
                msg_type, body = await asyncio.wait_for(read_frame(reader), 5)
            except (asyncio.TimeoutError, OSError):
                writer.close()
                return False
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            return msg_type == MsgType.LOGIN_RESP and body.get("code") == 0

        ok = asyncio.run(probe())
        check(f"真实服务端经 {host} 可以登录", ok, "LOGIN_RESP code=0" if ok else "连不上")
    finally:
        terminate_tree(server)


def main() -> int:
    parser = argparse.ArgumentParser(prog="verify_d3_net")
    parser.add_argument("--host", default=None, help="默认自动取本机局域网 IP")
    args = parser.parse_args()
    host = args.host or lan_ip()

    log("===== D3 收尾 · T13 换网段复验 =====")
    log(f"本机局域网 IP：{host}")
    log(f"回环地址：127.0.0.1　（两边连的是同一个服务端 0.0.0.0）")
    log()
    check("拿到的是局域网地址而不是回环/自动私有地址",
          not host.startswith(("127.", "169.254.")), host)
    log()
    log("----- A 进程内服务端（能看到对端地址）-----")
    try:
        asyncio.run(part_a(host))
    except Exception as exc:                      # noqa: BLE001 - 工具要跑完，别半路崩
        check("A 段跑完", False, f"{type(exc).__name__}: {exc}")
    log()
    log("----- B 真实入口 python -m server.server -----")
    part_b(host)
    log()
    log(f"合计 {len(OK) + len(BAD)} 项：PASS {len(OK)}，FAIL {len(BAD)}")
    log()
    log("说明：以上证明「换网段只要改 IP，代码不用动」。")
    log("      「另一台机器」那一半仍需现场用第二台设备做（手机热点下让同学连进来即可）。")
    if BAD:
        log("FAIL 项：" + "；".join(BAD))

    target = ROOT / "docs" / "test" / "D3-net-evidence.txt"
    target.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"result: PASS {len(OK)} / FAIL {len(BAD)} -> {target.relative_to(ROOT)}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    raise SystemExit(main())
