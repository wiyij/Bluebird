"""D2 验收：三端在线、群聊扇出、私聊隔离、系统消息、限流、登录硬化。

用原始 TCP 连接（不经过 CLI），逐条复跑 A09—A16 的判据，结果写到
docs/test/D2-evidence.txt。

    py -3.13 tools/verify_d2.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.server import ChatServer                      # noqa: E402
from shared.config import Config                          # noqa: E402
from shared.errors import ErrorCode                       # noqa: E402
from shared.protocol import MsgType, encode_frame, read_frame  # noqa: E402

OUT: list[str] = []
OK: list[str] = []
BAD: list[str] = []


def log(line: str = "") -> None:
    OUT.append(line)


def check(tag: str, ok: bool, detail: str = "") -> None:
    (OK if ok else BAD).append(tag)
    log(f"[{'PASS' if ok else 'FAIL'}] {tag}  {detail}")


class Conn:
    """一个测试客户端：发帧、读帧、按类型等帧。"""

    def __init__(self, name: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.name = name
        self.reader = reader
        self.writer = writer
        self.skipped: list[int] = []

    async def send(self, msg_type: MsgType, body: dict | None = None) -> None:
        self.writer.write(encode_frame(msg_type, body or {}))
        await self.writer.drain()

    async def next(self, timeout: float = 2.0) -> tuple[int, dict]:
        return await asyncio.wait_for(read_frame(self.reader), timeout)

    async def until(self, wanted: MsgType, timeout: float = 2.0) -> dict:
        while True:
            msg_type, body = await self.next(timeout)
            if msg_type == wanted:
                return body
            self.skipped.append(msg_type)

    async def login(self, color: str = "black") -> dict:
        await self.send(MsgType.LOGIN_REQ, {
            "username": self.name, "client_ver": "2.0", "color": color,
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


async def main() -> int:
    srv = ChatServer(Config(host="127.0.0.1", port=0, log_level="WARNING"))
    await srv.start()
    port = srv.port

    alice = await open_conn(port, "alice")
    bob = await open_conn(port, "bob")
    carol = await open_conn(port, "carol")

    # ---------- A10 登录 + A11 名单 / 上下线 ----------
    resp = await alice.login()
    check("A10 登录成功（code=0，昵称回填）",
          resp.get("code") == 0 and resp.get("nickname") == "alice", f"code={resp.get('code')}")

    await bob.login()
    join_bob = await alice.until(MsgType.USER_JOIN)
    sys_bob = await alice.until(MsgType.CHAT_SYSTEM)
    check("A11 有人上线：除自己外各收 1 条 USER_JOIN", join_bob.get("nickname") == "bob",
          f"USER_JOIN={join_bob.get('nickname')}")
    check("A14 上线同时发 1 条 CHAT_SYSTEM", sys_bob.get("text") == "bob 进入聊天室",
          f"text={sys_bob.get('text')} level={sys_bob.get('level')}")

    await carol.login()
    users = carol.users
    check("A11 第 3 人登录立刻看到前 2 人", users.get("count") == 3,
          f"count={users.get('count')} 名单={[u['nickname'] for u in users.get('users', [])]}")

    join_carol_a = await alice.until(MsgType.USER_JOIN)
    sys_carol_a = await alice.until(MsgType.CHAT_SYSTEM)
    join_carol_b = await bob.until(MsgType.USER_JOIN)
    sys_carol_b = await bob.until(MsgType.CHAT_SYSTEM)
    check("A11 第三个人上线，alice 与 bob 各收 1 条 USER_JOIN",
          join_carol_a.get("nickname") == "carol" and join_carol_b.get("nickname") == "carol",
          "alice/bob 各 1 条")
    check("A14 系统消息每端只来 1 条（不重复）",
          sys_carol_a.get("text") == sys_carol_b.get("text") == "carol 进入聊天室",
          sys_carol_a.get("text", ""))

    # ---------- A12 群聊：seq 与扇出 ----------
    await alice.send(MsgType.CHAT_GROUP, {"msg_id": "alice-000001", "text": "三端群聊测试"})
    got_a, got_b, got_c = (await alice.until(MsgType.CHAT_GROUP),
                           await bob.until(MsgType.CHAT_GROUP),
                           await carol.until(MsgType.CHAT_GROUP))
    check("A12 三人同时在线：一方发言，三端各收 1 条且 seq 相同",
          got_a == got_b == got_c and got_a["seq"] == got_b["seq"] == got_c["seq"],
          f"seq={got_a.get('seq')}，from={got_a.get('from')}")
    check("A12 发送者那份是回显（含自己）", got_a.get("from") == "alice" and got_a.get("text") == "三端群聊测试")

    # ---------- A13 私聊隔离 ----------
    await alice.send(MsgType.CHAT_PRIVATE, {
        "msg_id": "alice-000002", "to": "bob", "text": "只给 bob 的悄悄话",
    })
    priv_b = await bob.until(MsgType.CHAT_PRIVATE)
    priv_a = await alice.until(MsgType.CHAT_PRIVATE)
    check("A13 私聊投递给对方，发送者收到回显",
          priv_b == priv_a and priv_b.get("to") == "bob" and priv_b.get("from") == "alice",
          f"seq={priv_b.get('seq')}")

    carol.skipped.clear()
    await carol.send(MsgType.PING)
    await carol.until(MsgType.PONG)
    check("A13 私聊隔离：carol 全程没收到这条私聊",
          MsgType.CHAT_PRIVATE not in carol.skipped,
          f"carol 只收到 {[MsgType(t).name for t in carol.skipped]}")

    await alice.send(MsgType.CHAT_PRIVATE, {
        "msg_id": "alice-000003", "to": "dave", "text": "在吗",
    })
    err = await alice.until(MsgType.ERROR)
    check("A13 私聊对象离线回 3001，不暂存", err.get("code") == int(ErrorCode.USER_OFFLINE),
          f"code={err.get('code')} message={err.get('message')}")

    # ---------- A16 限流 ----------
    bob.skipped.clear()
    alice.skipped.clear()
    carol.skipped.clear()
    for index in range(12):
        await bob.send(MsgType.CHAT_GROUP, {"msg_id": f"bob-{index:06d}", "text": f"刷屏 {index}"})

    bob_frames = [await bob.next(3) for _ in range(12)]
    bob_chats = [body for msg_type, body in bob_frames if msg_type == MsgType.CHAT_GROUP]
    bob_errors = [body for msg_type, body in bob_frames if msg_type == MsgType.ERROR]
    check("A16 1 秒内 12 条：只转发前 10 条", len(bob_chats) == 10, f"转发 {len(bob_chats)} 条")
    check("A16 其余回 2002", len(bob_errors) == 2
          and all(e["code"] == int(ErrorCode.RATE_LIMITED) for e in bob_errors),
          f"错误 {len(bob_errors)} 个")

    alice_chats = [await alice.until(MsgType.CHAT_GROUP, 3) for _ in range(10)]
    carol_chats = [await carol.until(MsgType.CHAT_GROUP, 3) for _ in range(10)]
    check("A16 超限的消息不扇出：其他两端各只收 10 条",
          len(alice_chats) == 10 and len(carol_chats) == 10,
          f"alice={len(alice_chats)} carol={len(carol_chats)}")
    seqs = [c["seq"] for c in alice_chats]
    check("A16 被丢弃的消息没有占 seq（转发的 10 条 seq 严格 +1）",
          all(b - a == 1 for a, b in zip(seqs, seqs[1:])),
          f"seq={seqs}")

    # ---------- A11 / A14 下线 ----------
    await carol.send(MsgType.LOGOUT)
    left = await alice.until(MsgType.USER_LEAVE)
    left_sys = await alice.until(MsgType.CHAT_SYSTEM)
    check("A11 退出：其他人收 1 条 USER_LEAVE（reason=logout）",
          left.get("nickname") == "carol" and left.get("reason") == "logout",
          f"reason={left.get('reason')}")
    check("A14 退出同时发 1 条 CHAT_SYSTEM", left_sys.get("text") == "carol 退出了",
          f"text={left_sys.get('text')}")

    for conn in (alice, bob, carol):
        await conn.close()
    await asyncio.sleep(0.1)
    await srv.close()

    # ---------- A10 登录硬化（单独起一个 0.3 s 超时的服务端）----------
    fast = ChatServer(Config(host="127.0.0.1", port=0, log_level="WARNING"), login_timeout=0.3)
    await fast.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", fast.port)
    try:
        closed = await asyncio.wait_for(reader.read(16), timeout=2)
        check("A10 登录时限：0.3 s 不发 LOGIN_REQ，服务端直接关闭（不分帧回包）",
              closed == b"", f"读到 {len(closed)} 字节")
    finally:
        writer.close()

    reader, writer = await asyncio.open_connection("127.0.0.1", fast.port)
    try:
        writer.write(encode_frame(MsgType.PING))
        await writer.drain()
        msg_type, body = await asyncio.wait_for(read_frame(reader), timeout=2)
        check("A10 未登录就发 PING → 1006 并断开",
              msg_type == MsgType.ERROR and body.get("code") == int(ErrorCode.NOT_LOGGED_IN),
              f"code={body.get('code')}")
        closed = await asyncio.wait_for(reader.read(16), timeout=2)
        check("A10 1006 之后连接被关闭", closed == b"", f"读到 {len(closed)} 字节")
    finally:
        writer.close()
    await fast.close()

    log("")
    log(f"合计 {len(OK) + len(BAD)} 项：PASS {len(OK)}，FAIL {len(BAD)}")
    if BAD:
        log("FAIL 项：" + "；".join(BAD))
    return 0 if not BAD else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    target = ROOT / "docs" / "test" / "D2-evidence.txt"
    target.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"result: PASS {len(OK)} / FAIL {len(BAD)} -> {target.relative_to(ROOT)}")
    sys.exit(code)
