"""D1 验收（协议层）：在真实 TCP 连接上逐条复跑 A03 / A05 / A08 的完成判据。

不是单元测试，而是「把服务端真起来、用字节说话」的验收脚本：

    py -3.13 tools/verify_d1.py

结果写到 docs/test/D1-live.txt（UTF-8）。退出码 0 表示 13 项全过。
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


async def read_one(reader, timeout=2.0):
    return await asyncio.wait_for(read_frame(reader), timeout)


async def until(reader, wanted, timeout=2.0):
    """读到指定类型为止，跳过中间的其他帧。

    D2 起服务端会多发 CHAT_SYSTEM（上下线提示），按位置读会读到它；
    验收关心的是「某类帧的内容」，所以跳过无关帧再断言。
    """
    while True:
        msg_type, body = await read_one(reader, timeout)
        if msg_type == wanted:
            return msg_type, body


async def drain_until_closed(reader, timeout=2.0) -> int:
    """读到 EOF 为止，返回剩余字节数；-1 表示没关。"""
    total = 0
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout)
            if not chunk:
                return total
            total += len(chunk)
    except asyncio.TimeoutError:
        return -1


async def main() -> int:
    srv = ChatServer(Config(host="127.0.0.1", port=0, log_level="DEBUG"))
    await srv.start()
    port = srv.port
    log(f"server listening on 127.0.0.1:{port}（0 端口由系统分配）\n")

    # ---------- A03：一帧拆成两次发，服务端仍能还原 ----------
    r1, w1 = await asyncio.open_connection("127.0.0.1", port)
    body = {"username": "alice", "client_ver": "2.0", "color": "black"}
    frame = encode_frame(MsgType.LOGIN_REQ, body)
    w1.write(frame[:4])
    await w1.drain()
    await asyncio.sleep(0.2)                      # 判据原文：先 4 字节，sleep 0.2 s
    w1.write(frame[4:])
    await w1.drain()
    try:
        t, resp = await read_one(r1)
        check("A03 半包：先 4 字节、0.2 s 后发余下，仍还原成一帧",
              t == MsgType.LOGIN_RESP and resp.get("code") == 0,
              f"收到 LOGIN_RESP code={resp.get('code')}")
    except Exception as exc:                       # noqa: BLE001
        check("A03 半包：先 4 字节、0.2 s 后发余下，仍还原成一帧", False, repr(exc))

    log(f"    首部实际字节：{frame[:8].hex(' ').upper()}   "
        f"magic=0x{int.from_bytes(frame[:2], 'big'):04X} ver={frame[2]}")
    check("A03 首两字节 43 48、整帧 = 8 字节头 + 正文",
          frame[:2] == b"\x43\x48" and int.from_bytes(frame[4:8], "big") == len(frame) - 8,
          f"整帧 {len(frame)} B = 8 + {len(frame) - 8}")

    ul = await until(r1, MsgType.USER_LIST)
    check("A11 USER_LIST 登录后立即下发",
          ul[0] == MsgType.USER_LIST and ul[1].get("count") == 1,
          f"count={ul[1].get('count')}")

    # ---------- A05 / A12：一帧进来，发送者与其他人各收一份 ----------
    r2, w2 = await asyncio.open_connection("127.0.0.1", port)
    w2.write(encode_frame(MsgType.LOGIN_REQ, {"username": "bob", "client_ver": "2.0"}))
    await w2.drain()
    await until(r2, MsgType.LOGIN_RESP)
    await until(r2, MsgType.USER_LIST)
    join = await until(r1, MsgType.USER_JOIN)     # alice 收到 USER_JOIN
    check("A11 上下线广播",
          join[0] == MsgType.USER_JOIN and join[1].get("nickname") == "bob",
          f"{join[1].get('nickname')} 进入")

    w1.write(encode_frame(MsgType.CHAT_GROUP,
                          {"msg_id": "alice-000001", "text": "大家好，我是 alice"}))
    await w1.drain()
    ta, ba = await until(r1, MsgType.CHAT_GROUP)
    tb, bb = await until(r2, MsgType.CHAT_GROUP)
    check("A05/A12 发送者收到自己的帧（回显），对方也收到同一帧",
          ta == tb == MsgType.CHAT_GROUP and ba == bb
          and ba["from"] == "alice" and ba["seq"] == bb["seq"],
          f"seq={ba.get('seq')}，两端正文一致={ba == bb}")

    raw = encode_frame(MsgType.CHAT_GROUP, {"text": "x"})
    log(f"    群聊帧首部：{raw[:8].hex(' ').upper()}（ver={raw[2]}）")

    # ---------- A08：未知 type → 2004，连接仍可用 ----------
    w1.write(encode_frame(0x7F, {"whatever": 1}))
    await w1.drain()
    t, err = await until(r1, MsgType.ERROR)
    check("A08 未知 type 回 ERROR(2004)",
          t == MsgType.ERROR and err.get("code") == int(ErrorCode.UNKNOWN_TYPE),
          f"code={err.get('code')}")

    w1.write(encode_frame(MsgType.PING))
    await w1.drain()
    t, pong = await until(r1, MsgType.PONG)
    check("A08 未知 type 之后连接保持（PING→PONG）", t == MsgType.PONG,
          f"PONG server_time={pong.get('server_time')}")

    # ---------- 坏 magic：不回包、直接断；服务端不崩 ----------
    r3, w3 = await asyncio.open_connection("127.0.0.1", port)
    w3.write(HEADER.pack(0x1234, VERSION, MsgType.PING, 2) + b"{}")
    await w3.drain()
    got = await drain_until_closed(r3)
    check("坏 magic 不回包、直接关闭", got == 0,
          f"关闭前收到 {got} 字节（-1 表示未关闭）")
    w3.close()

    # ---------- 版本 v1 连 v2 服务端：回 1003 后关闭 ----------
    r4, w4 = await asyncio.open_connection("127.0.0.1", port)
    w4.write(HEADER.pack(MAGIC, 1, MsgType.PING, 2) + b"{}")
    await w4.drain()
    t, err = await read_one(r4)
    check("版本不符回 ERROR(1003)",
          t == MsgType.ERROR and err.get("code") == int(ErrorCode.VERSION_MISMATCH),
          f"code={err.get('code')}")
    rest = await drain_until_closed(r4)
    check("版本不符后关闭连接", rest == 0, f"关闭前剩余 {rest} 字节")
    w4.close()

    # ---------- 谎报 length：不读体、直接断 ----------
    r5, w5 = await asyncio.open_connection("127.0.0.1", port)
    w5.write(HEADER.pack(MAGIC, VERSION, MsgType.CHAT_GROUP, 65535))
    await w5.drain()
    got = await drain_until_closed(r5)
    check("谎报 length=65535：先判长度再读体，直接断", got == 0,
          f"关闭前收到 {got} 字节")
    w5.close()

    # ---------- 客户端 RST 后服务端仍可服务 ----------
    r6, w6 = await asyncio.open_connection("127.0.0.1", port)
    w6.transport.abort()                           # 不发 FIN，直接 RST
    await asyncio.sleep(0.1)
    r7, w7 = await asyncio.open_connection("127.0.0.1", port)
    w7.write(encode_frame(MsgType.LOGIN_REQ, {"username": "carol", "client_ver": "2.0"}))
    await w7.drain()
    t, resp = await read_one(r7)
    check("杀客户端（RST）后服务端不崩、仍能登录",
          t == MsgType.LOGIN_RESP and resp.get("code") == 0,
          f"carol code={resp.get('code')}")
    check("RST 的未登录连接没被算进在线表", srv.client_count == 3,
          f"在线 {srv.client_count} 人（alice/bob/carol）")

    for w in (w1, w2, w7):
        w.close()
    await asyncio.sleep(0.1)
    await srv.close()

    log("")
    log(f"合计 {len(OK) + len(BAD)} 项：PASS {len(OK)}，FAIL {len(BAD)}")
    if BAD:
        log("FAIL 项：" + "；".join(BAD))
    return 0 if not BAD else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    target = ROOT / "docs" / "test" / "D1-live.txt"
    target.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"result: PASS {len(OK)} / FAIL {len(BAD)} -> {target.relative_to(ROOT)}")
    sys.exit(code)
