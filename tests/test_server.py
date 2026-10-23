"""服务端 v2 联调测试：真的起服务端，真的用 socket 登录、群聊、私聊、改色。

用 0 端口让系统分配空闲端口，测试之间不会抢 8888。
"""

from __future__ import annotations

import asyncio

import pytest

from server.server import ChatServer
from shared.config import Config
from shared.errors import ErrorCode
from shared.protocol import MsgType, encode_frame, read_frame


@pytest.fixture
async def server():
    instance = ChatServer(Config(host="127.0.0.1", port=0, log_level="WARNING"))
    await instance.start()
    yield instance
    await instance.close()


async def connect(server: ChatServer):
    return await asyncio.open_connection("127.0.0.1", server.port)


async def read_until(reader, wanted: int, timeout: float = 2.0) -> dict:
    """读到指定类型为止，跳过中间的其他帧。"""
    while True:
        msg_type, body = await asyncio.wait_for(read_frame(reader), timeout)
        if msg_type == wanted:
            return body


async def login(reader, writer, name: str, color: str = "black") -> dict:
    writer.write(encode_frame(MsgType.LOGIN_REQ, {
        "username": name, "client_ver": "2.0", "color": color,
    }))
    await writer.drain()
    return await read_until(reader, MsgType.LOGIN_RESP)


# ---------------------------------------------------------------- 登录


async def test_login_success_returns_profile_and_name_list(server: ChatServer) -> None:
    reader, writer = await connect(server)
    try:
        resp = await login(reader, writer, "alice", "green")
        assert resp["code"] == 0
        assert resp["nickname"] == "alice"
        assert resp["color"] == "green"

        user_list = await read_until(reader, MsgType.USER_LIST)
        assert user_list["count"] == 1
        assert user_list["users"][0]["nickname"] == "alice"
        assert user_list["users"][0]["color"] == "green"
    finally:
        writer.close()
        await writer.wait_closed()


async def test_duplicate_nickname_is_refused_and_connection_closed(server: ChatServer) -> None:
    reader_a, writer_a = await connect(server)
    await login(reader_a, writer_a, "alice")

    reader_b, writer_b = await connect(server)
    try:
        resp = await login(reader_b, writer_b, "alice")
        assert resp["code"] == int(ErrorCode.NICK_TAKEN)
        # 服务端回完就关连接
        assert await asyncio.wait_for(reader_b.read(16), timeout=2) == b""
    finally:
        for w in (writer_a, writer_b):
            w.close()
            await w.wait_closed()


async def test_bad_nickname_is_refused(server: ChatServer) -> None:
    reader, writer = await connect(server)
    try:
        resp = await login(reader, writer, "alice bob")   # 含空格
        assert resp["code"] == int(ErrorCode.BAD_NICKNAME)
    finally:
        writer.close()
        await writer.wait_closed()


async def test_business_frame_before_login_gets_1006(server: ChatServer) -> None:
    reader, writer = await connect(server)
    try:
        writer.write(encode_frame(MsgType.CHAT_GROUP, {"text": "hi"}))
        await writer.drain()

        body = await read_until(reader, MsgType.ERROR)
        assert body["code"] == int(ErrorCode.NOT_LOGGED_IN)
    finally:
        writer.close()
        await writer.wait_closed()


async def test_second_client_gets_user_join(server: ChatServer) -> None:
    reader_a, writer_a = await connect(server)
    await login(reader_a, writer_a, "alice")

    reader_b, writer_b = await connect(server)
    await login(reader_b, writer_b, "bob")

    joined = await read_until(reader_a, MsgType.USER_JOIN)
    assert joined["nickname"] == "bob"

    for w in (writer_a, writer_b):
        w.close()
        await w.wait_closed()


# ---------------------------------------------------------------- 群聊


async def test_group_message_reaches_everyone(server: ChatServer) -> None:
    reader_a, writer_a = await connect(server)
    reader_b, writer_b = await connect(server)
    await login(reader_a, writer_a, "alice")
    await login(reader_b, writer_b, "bob")
    try:
        writer_a.write(encode_frame(MsgType.CHAT_GROUP, {
            "msg_id": "alice-000001", "text": "大家好",
        }))
        await writer_a.drain()

        got_a = await read_until(reader_a, MsgType.CHAT_GROUP)
        got_b = await read_until(reader_b, MsgType.CHAT_GROUP)

        assert got_a == got_b
        assert got_b["from"] == "alice"
        assert got_b["text"] == "大家好"
        assert got_b["seq"] >= 1
    finally:
        for w in (writer_a, writer_b):
            w.close()
            await w.wait_closed()


# ---------------------------------------------------------------- 私聊


async def test_private_message_only_reaches_target_and_sender(server: ChatServer) -> None:
    reader_a, writer_a = await connect(server)
    reader_b, writer_b = await connect(server)
    reader_c, writer_c = await connect(server)
    await login(reader_a, writer_a, "alice")
    await login(reader_b, writer_b, "bob")
    await login(reader_c, writer_c, "carol")
    try:
        writer_a.write(encode_frame(MsgType.CHAT_PRIVATE, {
            "msg_id": "alice-000002", "to": "bob", "text": "悄悄话",
        }))
        await writer_a.drain()

        got_b = await read_until(reader_b, MsgType.CHAT_PRIVATE)
        got_a = await read_until(reader_a, MsgType.CHAT_PRIVATE)
        assert got_b["from"] == "alice" and got_b["to"] == "bob"
        assert got_a == got_b

        # carol 不该收到任何私聊帧：给她发个 PING，等到 PONG 为止（跳过名单等帧）
        writer_c.write(encode_frame(MsgType.PING))
        await writer_c.drain()
        await read_until(reader_c, MsgType.PONG)
    finally:
        for w in (writer_a, writer_b, writer_c):
            w.close()
            await w.wait_closed()


async def test_private_to_offline_user_returns_3001(server: ChatServer) -> None:
    reader, writer = await connect(server)
    await login(reader, writer, "alice")
    try:
        writer.write(encode_frame(MsgType.CHAT_PRIVATE, {
            "msg_id": "alice-000003", "to": "nobody", "text": "在吗",
        }))
        await writer.drain()

        body = await read_until(reader, MsgType.ERROR)
        assert body["code"] == int(ErrorCode.USER_OFFLINE)
    finally:
        writer.close()
        await writer.wait_closed()


# ---------------------------------------------------------------- 颜色


async def test_color_change_is_broadcast(server: ChatServer) -> None:
    reader_a, writer_a = await connect(server)
    reader_b, writer_b = await connect(server)
    await login(reader_a, writer_a, "alice")
    await login(reader_b, writer_b, "bob")
    try:
        writer_a.write(encode_frame(MsgType.SET_COLOR, {"color": "blue"}))
        await writer_a.drain()

        got_a = await read_until(reader_a, MsgType.USER_COLOR)
        got_b = await read_until(reader_b, MsgType.USER_COLOR)
        assert got_a == got_b == {"nickname": "alice", "color": "blue"}

        # 改色后再登录的新人，名单里应当是蓝色
        reader_c, writer_c = await connect(server)
        await login(reader_c, writer_c, "carol")
        users = {u["nickname"]: u["color"] for u in
                 (await read_until(reader_c, MsgType.USER_LIST))["users"]}
        assert users["alice"] == "blue"
        writer_c.close()
        await writer_c.wait_closed()
    finally:
        for w in (writer_a, writer_b):
            w.close()
            await w.wait_closed()


async def test_invalid_color_is_rejected(server: ChatServer) -> None:
    reader, writer = await connect(server)
    await login(reader, writer, "alice")
    try:
        writer.write(encode_frame(MsgType.SET_COLOR, {"color": "purple"}))
        await writer.drain()

        body = await read_until(reader, MsgType.ERROR)
        assert body["code"] == int(ErrorCode.BAD_FRAME)
    finally:
        writer.close()
        await writer.wait_closed()


# ---------------------------------------------------------------- 其他


async def test_unknown_type_keeps_connection_alive(server: ChatServer) -> None:
    reader, writer = await connect(server)
    await login(reader, writer, "alice")
    try:
        writer.write(encode_frame(0x7F, {"whatever": 1}))
        await writer.drain()

        body = await read_until(reader, MsgType.ERROR)
        assert body["code"] == int(ErrorCode.UNKNOWN_TYPE)

        writer.write(encode_frame(MsgType.PING))
        await writer.drain()
        assert (await asyncio.wait_for(read_frame(reader), timeout=2))[0] == MsgType.PONG
    finally:
        writer.close()
        await writer.wait_closed()


async def test_leave_is_broadcast_on_disconnect(server: ChatServer) -> None:
    reader_a, writer_a = await connect(server)
    reader_b, writer_b = await connect(server)
    await login(reader_a, writer_a, "alice")
    await login(reader_b, writer_b, "bob")
    try:
        writer_b.close()
        await writer_b.wait_closed()

        left = await read_until(reader_a, MsgType.USER_LEAVE)
        assert left["nickname"] == "bob"
    finally:
        writer_a.close()
        await writer_a.wait_closed()


async def test_server_survives_client_abort(server: ChatServer) -> None:
    _, victim = await connect(server)
    victim.transport.abort()               # 不发 FIN，直接 RST
    await asyncio.sleep(0.05)

    reader, writer = await connect(server)
    try:
        resp = await login(reader, writer, "alice")
        assert resp["code"] == 0
    finally:
        writer.close()
        await writer.wait_closed()


async def test_client_count_after_logout(server: ChatServer) -> None:
    reader, writer = await connect(server)
    await login(reader, writer, "alice")
    assert server.client_count == 1

    writer.write(encode_frame(MsgType.LOGOUT))
    await writer.drain()
    await asyncio.sleep(0.1)

    assert server.client_count == 0
    writer.close()
    await writer.wait_closed()
