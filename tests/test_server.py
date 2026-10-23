"""A05/A08 的服务端联调测试：真的起一个服务端，真的用 socket 说话。

用 0 端口让系统分配空闲端口，测试之间不会抢 8888。
"""

from __future__ import annotations

import asyncio

import pytest

from server.server import ChatServer
from shared.config import Config
from shared.protocol import MAGIC, VERSION, MsgType, encode_frame, read_frame


@pytest.fixture
async def server():
    instance = ChatServer(Config(host="127.0.0.1", port=0, log_level="WARNING"))
    await instance.start()
    yield instance
    await instance.close()


async def connect(server: ChatServer):
    return await asyncio.open_connection("127.0.0.1", server.port)


async def test_group_message_is_broadcast_to_everyone(server: ChatServer) -> None:
    reader_a, writer_a = await connect(server)
    reader_b, writer_b = await connect(server)
    try:
        await asyncio.sleep(0.02)          # 等两个 handle 协程登记完成
        assert server.client_count == 2

        body = {"msg_id": "alice-000001", "text": "有人吗？"}
        writer_a.write(encode_frame(MsgType.CHAT_GROUP, body))
        await writer_a.drain()

        _, got_by_a = await asyncio.wait_for(read_frame(reader_a), timeout=2)
        _, got_by_b = await asyncio.wait_for(read_frame(reader_b), timeout=2)
        assert got_by_a == body
        assert got_by_b == body
    finally:
        for writer in (writer_a, writer_b):
            writer.close()
            await writer.wait_closed()


async def test_ping_gets_pong_with_server_time(server: ChatServer) -> None:
    reader, writer = await connect(server)
    try:
        writer.write(encode_frame(MsgType.PING))
        await writer.drain()

        msg_type, body = await asyncio.wait_for(read_frame(reader), timeout=2)
        assert msg_type == MsgType.PONG
        assert isinstance(body["server_time"], int)
        assert body["server_time"] > 1_600_000_000
    finally:
        writer.close()
        await writer.wait_closed()


async def test_unknown_type_keeps_the_connection_alive(server: ChatServer) -> None:
    """A08 的判据：未知类型回 2004，但连接不能断，后续消息照样处理。"""
    reader, writer = await connect(server)
    try:
        writer.write(encode_frame(0x7F, {"whatever": 1}))
        await writer.drain()

        msg_type, body = await asyncio.wait_for(read_frame(reader), timeout=2)
        assert msg_type == MsgType.ERROR
        assert body["code"] == 2004

        # 连接仍然可用
        writer.write(encode_frame(MsgType.PING))
        await writer.drain()
        msg_type, _ = await asyncio.wait_for(read_frame(reader), timeout=2)
        assert msg_type == MsgType.PONG
    finally:
        writer.close()
        await writer.wait_closed()


async def test_bad_magic_closes_without_reply(server: ChatServer) -> None:
    reader, writer = await connect(server)
    try:
        writer.write(b"\x12\x34" + bytes([VERSION, MsgType.PING, 0, 0, 0, 2]) + b"{}")
        await writer.drain()

        assert await asyncio.wait_for(reader.read(16), timeout=2) == b""
    finally:
        writer.close()
        await writer.wait_closed()


async def test_version_mismatch_gets_1003(server: ChatServer) -> None:
    reader, writer = await connect(server)
    try:
        writer.write(bytes([MAGIC >> 8, MAGIC & 0xFF, 2, MsgType.PING, 0, 0, 0, 2]) + b"{}")
        await writer.drain()

        msg_type, body = await asyncio.wait_for(read_frame(reader), timeout=2)
        assert msg_type == MsgType.ERROR
        assert body["code"] == 1003
    finally:
        writer.close()
        await writer.wait_closed()


async def test_server_survives_client_abort(server: ChatServer) -> None:
    """客户端硬断（RST）之后服务端必须还能接新连接。"""
    _, victim = await connect(server)
    victim.transport.abort()               # 不发 FIN，直接 RST
    await asyncio.sleep(0.05)

    reader, writer = await connect(server)
    try:
        writer.write(encode_frame(MsgType.PING))
        await writer.drain()
        msg_type, _ = await asyncio.wait_for(read_frame(reader), timeout=2)
        assert msg_type == MsgType.PONG
    finally:
        writer.close()
        await writer.wait_closed()


async def test_client_count_drops_after_disconnect(server: ChatServer) -> None:
    reader, writer = await connect(server)
    await asyncio.sleep(0.02)
    assert server.client_count == 1

    writer.close()
    await writer.wait_closed()
    await asyncio.sleep(0.05)

    assert server.client_count == 0
