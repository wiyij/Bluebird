"""客户端（client/cli.py）的单元测试：心跳、发送、断线判死。

存在的原因：2026-10-24 复审时 ``_heartbeat_loop`` 调 ``_send(MsgType.PING)``
少传了一个参数，而当时所有用例都在 15 s 之前就结束了，**没有一条会触发心跳**——
所以集成测试全绿，真机上空闲 15 s 客户端就自己崩了。这里用假 writer + 改小的
间隔把「15 s 那一下」提前到毫秒级，专门盯这条路径。
"""

from __future__ import annotations

import asyncio

import pytest

import client.cli as cli
from client.cli import ChatClient
from shared.config import Config
from shared.protocol import MsgType, read_frame


class FakeWriter:
    """只实现 ChatClient 用到的那点接口，把写下去的帧记下来。"""

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closing = False

        class _Transport:
            def abort(inner) -> None:            # noqa: N805 - 只为接口形状
                self.closing = True

        self.transport = _Transport()

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.closing

    def close(self) -> None:
        self.closing = True

    async def wait_closed(self) -> None:
        return None


async def _decode(frame: bytes):
    reader = asyncio.StreamReader()
    reader.feed_data(frame)
    reader.feed_eof()
    return await read_frame(reader)


def make_client(*, name: str = "alice") -> tuple[ChatClient, FakeWriter]:
    client = ChatClient(Config(host="127.0.0.1", port=1), name)
    writer = FakeWriter()
    client.writer = writer            # type: ignore[assignment]
    return client, writer


async def test_send_without_a_body_is_allowed() -> None:
    """PING 没有正文：``_send(MsgType.PING)`` 必须能单参调用。

    这条是上面那个「空闲 15 s 就崩」的 bug 的回归用例。
    """
    client, writer = make_client()
    await client._send(MsgType.PING)

    msg_type, body = await _decode(writer.frames[-1])
    assert msg_type == MsgType.PING
    assert body == {}


async def test_heartbeat_sends_ping_periodically(monkeypatch) -> None:
    monkeypatch.setattr(cli, "HEARTBEAT_INTERVAL", 0.05)
    client, writer = make_client()
    client._last_rx = asyncio.get_running_loop().time()      # 刚刚收过帧，不该判死

    task = asyncio.create_task(client._heartbeat_loop())
    try:
        await asyncio.sleep(0.18)
        assert not task.done(), "心跳任务不该自己结束"
        pings = [f for f in writer.frames if f[3] == int(MsgType.PING)]
        assert len(pings) >= 2, f"5 个周期里只发了 {len(pings)} 个 PING"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_heartbeat_declares_death_after_idle_timeout(monkeypatch) -> None:
    monkeypatch.setattr(cli, "HEARTBEAT_INTERVAL", 0.02)
    monkeypatch.setattr(cli, "IDLE_TIMEOUT", 0.01)
    client, writer = make_client()
    client._last_rx = 0.0                                    # 很久以前

    await asyncio.wait_for(client._heartbeat_loop(), timeout=2)

    assert client._closed is True
    assert writer.closing is True, "判死之后要真的把连接断掉"


async def test_closed_flag_stops_the_heartbeat() -> None:
    client, writer = make_client()
    client._closed = True

    await asyncio.wait_for(client._heartbeat_loop(), timeout=1)

    assert writer.frames == []


@pytest.mark.parametrize("text", ["你好", "hello", "😀 emoji"])
async def test_send_chat_roundtrips_the_text(text: str) -> None:
    client, writer = make_client()
    await client._send(MsgType.CHAT_GROUP, {"msg_id": "alice-000001", "text": text})

    msg_type, body = await _decode(writer.frames[-1])
    assert msg_type == MsgType.CHAT_GROUP
    assert body["text"] == text
