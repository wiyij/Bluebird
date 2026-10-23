"""A05 的自动化验收：echo 服务端真的能收帧、回帧、并在坏帧时按协议收场。

手工验收是「开两个终端互发」，这里是它的可重复版本：
用 0 端口起服务端，客户端连上去发一帧，断言拿回来的字节与发出去的一致。
"""

from __future__ import annotations

import asyncio

import pytest

from server.server import EchoServer
from shared.config import Config
from shared.protocol import MAGIC, VERSION, MsgType, encode_frame, read_frame


@pytest.fixture
async def echo_server():
    # port=0 → 由系统分配空闲端口，测试之间不会抢 8888
    server = EchoServer(Config(host="127.0.0.1", port=0, log_level="WARNING"))
    await server.start()
    yield server
    await server.close()


async def test_echo_roundtrip(echo_server: EchoServer) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", echo_server.port)
    body = {"msg_id": "alice-000001", "text": "你好，世界 😀"}
    try:
        writer.write(encode_frame(MsgType.CHAT_GROUP, body))
        await writer.drain()

        msg_type, echoed = await asyncio.wait_for(read_frame(reader), timeout=2)

        assert msg_type == MsgType.CHAT_GROUP
        assert echoed == body
    finally:
        writer.close()
        await writer.wait_closed()


async def test_multiple_frames_are_echoed_in_order(echo_server: EchoServer) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", echo_server.port)
    try:
        # 一次塞三条，服务端必须按顺序拆、按顺序回
        payloads = [{"msg_id": f"bob-{i:06d}", "text": f"第 {i} 条"} for i in (1, 2, 3)]
        writer.write(b"".join(encode_frame(MsgType.CHAT_GROUP, p) for p in payloads))
        await writer.drain()

        for expected in payloads:
            _, got = await asyncio.wait_for(read_frame(reader), timeout=2)
            assert got == expected
    finally:
        writer.close()
        await writer.wait_closed()


async def test_bad_magic_closes_without_reply(echo_server: EchoServer) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", echo_server.port)
    try:
        writer.write(b"\x12\x34" + bytes([VERSION]) + b"\x30\x00\x00\x00\x02{}")
        await writer.drain()

        assert await asyncio.wait_for(reader.read(16), timeout=2) == b""
    finally:
        writer.close()
        await writer.wait_closed()


async def test_version_mismatch_gets_error_frame(echo_server: EchoServer) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", echo_server.port)
    try:
        writer.write(bytes([MAGIC >> 8, MAGIC & 0xFF, 2, MsgType.PING, 0, 0, 0, 2]) + b"{}")
        await writer.drain()

        msg_type, body = await asyncio.wait_for(read_frame(reader), timeout=2)
        assert msg_type == MsgType.ERROR
        assert body["code"] == 1003
    finally:
        writer.close()
        await writer.wait_closed()


async def test_two_clients_see_each_other(echo_server: EchoServer) -> None:
    """A06 的判据：两个客户端在线，A 发的文本要出现在 B 的终端。"""
    reader_a, writer_a = await asyncio.open_connection("127.0.0.1", echo_server.port)
    reader_b, writer_b = await asyncio.open_connection("127.0.0.1", echo_server.port)
    try:
        await asyncio.sleep(0.02)          # 让两边的 handle 协程都登记完成
        assert echo_server.client_count == 2

        body = {"msg_id": "alice-000001", "text": "有人吗？"}
        writer_a.write(encode_frame(MsgType.CHAT_GROUP, body))
        await writer_a.drain()

        # A 收到回显，B 收到同一条
        _, got_by_a = await asyncio.wait_for(read_frame(reader_a), timeout=2)
        _, got_by_b = await asyncio.wait_for(read_frame(reader_b), timeout=2)
        assert got_by_a == body
        assert got_by_b == body
    finally:
        for writer in (writer_a, writer_b):
            writer.close()
            await writer.wait_closed()


async def test_server_survives_client_abort(echo_server: EchoServer) -> None:
    """客户端硬断（RST）之后服务端必须还能接新连接。"""
    victim = await asyncio.open_connection("127.0.0.1", echo_server.port)
    victim[1].transport.abort()            # 不发 FIN，直接 RST
    await asyncio.sleep(0.05)

    reader, writer = await asyncio.open_connection("127.0.0.1", echo_server.port)
    try:
        writer.write(encode_frame(MsgType.PING))
        await writer.drain()
        msg_type, body = await asyncio.wait_for(read_frame(reader), timeout=2)
        assert (msg_type, body) == (MsgType.PING, {})
    finally:
        writer.close()
        await writer.wait_closed()
