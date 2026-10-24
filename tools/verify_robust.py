"""D2 复审修复的验收：心跳判死、慢客户端、坏帧容错、登录字段校验、客户端断线。

起因：2026-10-24 复审发现 5 处「会在 D3 引爆」的隐患（见 ``docs/test/D2-审查.md`` 第 6 节）。
这个脚本把它们从「读代码觉得对」变成「跑一遍能看见」：

    py -3.13 tools/verify_robust.py

结果写到 ``docs/test/D2-robust-evidence.txt``；退出码 0 表示全过。
脚本自己起服务端（0 端口），不占 8888。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.server import ChatServer                     # noqa: E402
from shared.config import Config, HEARTBEAT_INTERVAL      # noqa: E402
from shared.errors import ErrorCode                      # noqa: E402
from shared.protocol import (HEADER, MAGIC, VERSION, MsgType,  # noqa: E402
                             encode_frame, read_frame)

OUT: list[str] = []
OK: list[str] = []
BAD: list[str] = []


def log(line: str = "") -> None:
    OUT.append(line)


def check(tag: str, ok: bool, detail: str = "") -> None:
    (OK if ok else BAD).append(tag)
    log(f"[{'PASS' if ok else 'FAIL'}] {tag}  {detail}")


class Conn:
    """测试客户端：发帧、按类型等帧。"""

    def __init__(self, name: str, reader, writer) -> None:
        self.name = name
        self.reader = reader
        self.writer = writer
        self.skipped: list[int] = []

    async def send(self, msg_type: MsgType, body: dict | None = None) -> None:
        self.writer.write(encode_frame(msg_type, body or {}))
        await self.writer.drain()

    async def next(self, timeout: float = 2.0):
        return await asyncio.wait_for(read_frame(self.reader), timeout)

    async def until(self, wanted: MsgType, timeout: float = 2.0) -> dict:
        while True:
            msg_type, body = await self.next(timeout)
            if msg_type == wanted:
                return body
            self.skipped.append(msg_type)

    async def login(self) -> dict:
        await self.send(MsgType.LOGIN_REQ, {
            "username": self.name, "client_ver": "2.0", "color": "black",
        })
        resp = await self.until(MsgType.LOGIN_RESP)
        self.users = await self.until(MsgType.USER_LIST)
        return resp

    async def close(self) -> None:
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def open_conn(port: int, name: str) -> Conn:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    return Conn(name, reader, writer)


async def keep_alive(conn: Conn, interval: float = 0.1) -> asyncio.Task:
    """后台不停发 PING，把一条连接钉在「活着」的状态。"""
    async def loop() -> None:
        while True:
            await asyncio.sleep(interval)
            conn.writer.write(encode_frame(MsgType.PING))
            await conn.writer.drain()
    return asyncio.create_task(loop())


async def server_side() -> None:
    # 0.5 s 判死、0.1 s 扫一次：把 45 s 的等待压成半秒，判据完全一样
    srv = ChatServer(Config(host="127.0.0.1", port=0, log_level="CRITICAL"),
                     idle_timeout=0.5, sweep_interval=0.1)
    await srv.start()
    port = srv.port

    alice = await open_conn(port, "alice")
    await alice.login()
    bob = await open_conn(port, "bob")
    await bob.login()
    keep = await keep_alive(bob)                  # 整段测试里 bob 一直活着
    carol = dave = eve = dan = None
    try:
        # ---------- A1 心跳判死 ----------
        left = await bob.until(MsgType.USER_LEAVE, timeout=5)
        check("A17 心跳判死：长时间不发帧的连接被踢，广播 USER_LEAVE",
              left.get("nickname") == "alice" and left.get("reason") == "disconnect",
              f"{left}")
        system = await bob.until(MsgType.CHAT_SYSTEM, timeout=5)
        check("A17 系统提示写「超时下线」（reason 仍守协议，只有 logout/disconnect）",
              system.get("text") == "alice 超时下线", f"text={system.get('text')}")
        check("A17 被踢的连接从在线表里摘掉", srv.client_count == 1,
              f"在线 {srv.client_count} 人")

        # ---------- A2 昵称立刻可复用 ----------
        alice2 = await open_conn(port, "alice")
        resp = await alice2.login()
        check("A17 判死之后昵称立即可复用（不会有幽灵用户占着）",
              resp.get("code") == 0, f"code={resp.get('code')}")
        await alice2.close()

        # ---------- A3 PING 保活 ----------
        await asyncio.sleep(1.2)
        check("A17 持续 PING 的连接不会被误杀（任何帧都刷新 last_active）",
              srv.client_count == 1, f"在线 {srv.client_count} 人")

        # ---------- A4 写失败 → 断开，别人不受影响 ----------
        carol = await open_conn(port, "carol")
        await carol.login()
        dave = await open_conn(port, "dave")
        await dave.login()
        await carol.until(MsgType.CHAT_SYSTEM, timeout=3)      # dave 进入聊天室

        stuck = srv._clients["dave"]

        async def always_timeout(frame: bytes) -> None:
            raise TimeoutError("模拟写超时")

        stuck.send_frame = always_timeout          # type: ignore[method-assign]
        started = time.monotonic()
        await carol.send(MsgType.CHAT_GROUP, {"msg_id": "c-1", "text": "还活着吗"})
        got = await carol.until(MsgType.CHAT_GROUP, timeout=3)
        elapsed = time.monotonic() - started
        check("P0 写不进去的那条被断开，健康客户端照常收到消息",
              got.get("text") == "还活着吗", f"耗时 {elapsed:.2f} s")
        await asyncio.sleep(0.2)
        check("P0 被断开的连接从在线表里消失（不再有幽灵用户）",
              "dave" not in srv._clients, f"在线 {sorted(srv._clients)}")

        # ---------- A5 坏帧容错一次 ----------
        eve = await open_conn(port, "eve")
        await eve.login()
        garbage = HEADER.pack(MAGIC, VERSION, MsgType.CHAT_GROUP, 5) + b"{oops"
        eve.writer.write(garbage)
        await eve.writer.drain()
        err = await eve.until(MsgType.ERROR, timeout=3)
        check("P2 坏 JSON 回 2003", err.get("code") == int(ErrorCode.BAD_FRAME),
              f"code={err.get('code')}")
        await eve.send(MsgType.PING)
        pong = await eve.until(MsgType.PONG, timeout=3)
        check("P2 2003 之后连接保持（协议 §5「容错一次」）",
              pong.get("server_time") is not None, "PING→PONG 正常")
        eve.writer.write(garbage)
        await eve.writer.drain()
        await eve.until(MsgType.ERROR, timeout=3)
        closed = await asyncio.wait_for(eve.reader.read(16), timeout=3)
        check("P2 第二次坏帧才断开", closed == b"", f"读到 {len(closed)} 字节")

        # ---------- A6 登录必填字段 ----------
        dan = await open_conn(port, "dan")
        await dan.send(MsgType.LOGIN_REQ, {"username": "dan"})       # 缺 client_ver
        body = await dan.until(MsgType.LOGIN_RESP, timeout=3)
        closed = await asyncio.wait_for(dan.reader.read(16), timeout=3)
        check("P2 缺 client_ver 回 LOGIN_RESP{2003} 并断开（协议 §0 纪律 1）",
              body.get("code") == int(ErrorCode.BAD_FRAME) and closed == b"",
              f"code={body.get('code')}")
    finally:
        keep.cancel()
        await asyncio.gather(keep, return_exceptions=True)
        for conn in (alice, bob, carol, dave, eve, dan):
            if conn is not None:
                await conn.close()
        await asyncio.sleep(0.1)
        await srv.close()


def client_side() -> None:
    """真子进程：服务端被杀之后，CLI 必须自己退出来（旧实现会卡在 input() 上）。"""
    python = str(ROOT / ".venv" / "Scripts" / "python.exe")
    if not Path(python).exists():
        python = sys.executable
    port = "8921"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

    server = subprocess.Popen([python, "-m", "server.server", "--port", port],
                              cwd=ROOT, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=env)
    time.sleep(1.5)
    client = subprocess.Popen(
        [python, "-m", "client.cli", "--name", "alice", "--port", port],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        bufsize=1, env=env,
    )
    time.sleep(2.0)

    server.terminate()
    server.wait(timeout=5)
    started = time.monotonic()
    exited = False
    for _ in range(40):                      # 最多等 8 s
        time.sleep(0.2)
        if client.poll() is not None:
            exited = True
            break
    elapsed = time.monotonic() - started
    check("P0 服务端被杀后，CLI 自己退出（不需要按回车）",
          exited, f"{elapsed:.1f} s 后退出" if exited else "8 s 仍未退出")
    if not exited:
        client.kill()
    out = ""
    try:
        out = client.communicate(timeout=5)[0] or ""
    except subprocess.TimeoutExpired:
        client.kill()
        out = ""
    check("P0 断线提示是中文，不是 Python 异常 repr",
          "连接已断开" in out and "ConnectionResetError(" not in out,
          next((line for line in out.splitlines() if "连接" in line or "异常" in line), "（无输出）"))
    log()
    log("客户端最后几行输出：")
    for line in out.strip().splitlines()[-3:]:
        log(f"    {line}")


def main() -> int:
    log(f"心跳参数：{HEARTBEAT_INTERVAL:.0f} s 一次；本脚本用 0.5 s 判死 + 0.1 s 扫描做加速")
    log()
    log("===== A 服务端 =====")
    asyncio.run(server_side())
    log()
    log("===== B 客户端（真子进程）=====")
    client_side()
    log()
    log(f"合计 {len(OK) + len(BAD)} 项：PASS {len(OK)}，FAIL {len(BAD)}")
    if BAD:
        log("FAIL 项：" + "；".join(BAD))

    target = ROOT / "docs" / "test" / "D2-robust-evidence.txt"
    target.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"result: PASS {len(OK)} / FAIL {len(BAD)} -> {target.relative_to(ROOT)}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    raise SystemExit(main())
