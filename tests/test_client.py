"""客户端网络层（client/net.py）的单元测试。

用假 writer 把「要联网才看得见」的三件事钉住：写帧、心跳、断线事件。
2026-10-24 复审时踩过的坑（``_send()`` 少传参数，空闲 15 s 就崩）就从这里防：

- ``_write(MsgType.PING)`` 不带正文也得能调通；
- 心跳循环必须过 15 s 那个点还活着；
- 判死之后必须发 ``disconnected(timeout)`` 且只发一次。
"""

from __future__ import annotations

import asyncio

import pytest

import client.net as netmod
from client.net import NetClient
from shared.protocol import MsgType, read_frame


class FakeWriter:
    """只实现 NetClient 用到的那点接口，把写下去的帧记下来。"""

    def __init__(self, *, hang: bool = False) -> None:
        self.frames: list[bytes] = []
        self.closing = False
        self._hang = hang

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        if self._hang:
            await asyncio.sleep(30)

    def close(self) -> None:
        self.closing = True

    async def wait_closed(self) -> None:
        return None


def make_net(**kwargs) -> tuple[NetClient, list[tuple[str, dict]]]:
    events: list[tuple[str, dict]] = []
    net = NetClient("127.0.0.1", 1, "alice", on_event=events.append, **kwargs)
    net.writer = FakeWriter()          # type: ignore[assignment]
    return net, events


async def decode(frame: bytes) -> tuple[int, dict]:
    reader = asyncio.StreamReader()
    reader.feed_data(frame)
    reader.feed_eof()
    return await read_frame(reader)


# ---------------------------------------------------------------- 写帧


async def test_write_without_a_body_is_allowed() -> None:
    """PING 没有正文：``_write(MsgType.PING)`` 必须能单参调用。"""
    net, _ = make_net()
    assert await net._write(MsgType.PING) is True

    msg_type, body = await decode(net.writer.frames[-1])
    assert msg_type == MsgType.PING
    assert body == {}


async def test_write_reports_send_failed_when_the_peer_stops_reading(monkeypatch) -> None:
    monkeypatch.setattr(netmod, "SEND_TIMEOUT", 0.05)
    net, events = make_net()
    net.writer = FakeWriter(hang=True)          # type: ignore[assignment]

    assert await net._write(MsgType.PING) is False
    assert events[-1][0] == "disconnected"
    assert events[-1][1]["reason"] == "send_failed"


async def test_msg_id_increments_per_client() -> None:
    net, _ = make_net()
    assert net._next_msg_id() == "alice-000001"
    assert net._next_msg_id() == "alice-000002"


# ---------------------------------------------------------------- 心跳


async def test_heartbeat_sends_ping_periodically() -> None:
    net, _ = make_net(heartbeat_interval=0.05, idle_timeout=10)
    net._last_rx = asyncio.get_running_loop().time()

    task = asyncio.create_task(net._heartbeat_loop())
    try:
        await asyncio.sleep(0.18)
        assert not task.done(), "心跳任务不该自己结束"
        pings = [f for f in net.writer.frames if f[3] == int(MsgType.PING)]
        assert len(pings) >= 2, f"5 个周期里只发了 {len(pings)} 个 PING"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_heartbeat_declares_death_after_idle_timeout() -> None:
    net, events = make_net(heartbeat_interval=0.02, idle_timeout=0.01)
    net._last_rx = 0.0

    await asyncio.wait_for(net._heartbeat_loop(), timeout=2)

    assert events[-1] == ("disconnected", {"reason": "timeout"})


async def test_disconnect_is_reported_only_once() -> None:
    net, events = make_net()
    net._emit("disconnected", {"reason": "network"})
    net._emit("disconnected", {"reason": "timeout"})

    assert events == [("disconnected", {"reason": "network"})]


# ---------------------------------------------------------------- 事件映射


@pytest.mark.parametrize(("msg_type", "body", "expected_kind"), [
    (MsgType.CHAT_GROUP, {"from": "bob", "text": "hi", "seq": 3}, "message"),
    (MsgType.CHAT_PRIVATE, {"from": "bob", "to": "alice", "text": "悄悄话"}, "message"),
    (MsgType.CHAT_SYSTEM, {"text": "bob 进入聊天室", "level": "info"}, "system"),
    (MsgType.USER_LIST, {"users": [{"nickname": "alice", "color": "black"}], "count": 1},
     "user_list"),
    (MsgType.USER_JOIN, {"nickname": "bob", "color": "green", "since": 1}, "user_join"),
    (MsgType.USER_LEAVE, {"nickname": "bob", "reason": "logout"}, "user_leave"),
    (MsgType.USER_COLOR, {"nickname": "bob", "color": "red"}, "user_color"),
    (MsgType.ERROR, {"code": 3001, "message": "对方不在线"}, "error"),
])
def test_dispatch_maps_frames_to_events(msg_type, body, expected_kind) -> None:
    net, events = make_net()
    net._dispatch(int(msg_type), body)

    assert events[-1][0] == expected_kind


def test_pong_is_silent() -> None:
    net, events = make_net()
    net._dispatch(int(MsgType.PONG), {"server_time": 1})
    assert events == []


def test_private_message_keeps_from_and_to() -> None:
    net, events = make_net()
    net._dispatch(int(MsgType.CHAT_PRIVATE),
                  {"from": "alice", "to": "bob", "text": "在吗", "seq": 7})

    kind, payload = events[-1]
    assert kind == "message"
    assert payload["kind"] == "private"
    assert (payload["from"], payload["to"], payload["text"]) == ("alice", "bob", "在吗")
    assert payload["seq"] == 7
