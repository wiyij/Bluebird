"""A08：路由分发的单元测试 —— 不碰网络，只验「查表 / 兜底」这两件事。"""

from __future__ import annotations

import pytest

from server.router import Router
from shared.errors import ErrorCode
from shared.protocol import MsgType


class FakeClient:
    """只实现 dispatch 用得到的那点接口。"""

    def __init__(self) -> None:
        self.errors: list[tuple[int, str]] = []
        self.handled: list[int] = []

    async def send_error(self, code, message=None, ref_msg_id=None) -> None:
        self.errors.append((int(code), message or ""))

    async def send(self, msg_type, body=None) -> None:   # 处理函数里可能用到
        self.handled.append(int(msg_type))

    label = "fake"


async def test_dispatch_calls_the_registered_handler() -> None:
    router = Router()
    seen: list[dict] = []

    @router.handler(MsgType.CHAT_GROUP)
    async def on_group(client, body):
        seen.append(body)

    ok = await router.dispatch(FakeClient(), int(MsgType.CHAT_GROUP), {"text": "hi"})

    assert ok is True
    assert seen == [{"text": "hi"}]


async def test_unknown_type_gets_2004_and_is_reported() -> None:
    router = Router()
    client = FakeClient()

    ok = await router.dispatch(client, 0x7F, {})

    assert ok is False
    assert client.errors == [(int(ErrorCode.UNKNOWN_TYPE), "未知消息类型 0x7F")]


async def test_handler_crash_becomes_5001_not_a_dead_connection() -> None:
    router = Router()

    @router.handler(MsgType.CHAT_PRIVATE)
    async def boom(client, body):
        raise RuntimeError("故意炸一下")

    client = FakeClient()
    ok = await router.dispatch(client, int(MsgType.CHAT_PRIVATE), {})

    assert ok is True                      # 类型是认识的，只是处理失败
    assert client.errors[0][0] == int(ErrorCode.INTERNAL_ERROR)


def test_registering_the_same_type_twice_is_refused() -> None:
    router = Router()

    @router.handler(MsgType.PING)
    async def first(client, body):
        pass

    with pytest.raises(ValueError, match="已经注册过"):

        @router.handler(MsgType.PING)
        async def second(client, body):
            pass


async def test_protocol_error_in_handler_maps_to_its_code() -> None:
    from shared.protocol import ProtocolError

    router = Router()

    @router.handler(MsgType.CHAT_GROUP)
    async def too_long(client, body):
        raise ProtocolError("too_long", "超过 2000 字节", code=ErrorCode.MESSAGE_TOO_LONG)

    client = FakeClient()
    await router.dispatch(client, int(MsgType.CHAT_GROUP), {})

    assert client.errors == [(int(ErrorCode.MESSAGE_TOO_LONG), "超过 2000 字节")]


def test_registered_types_are_sorted_and_unique() -> None:
    router = Router()

    @router.handler(MsgType.PONG)
    async def pong(client, body):
        pass

    @router.handler(MsgType.PING)
    async def ping(client, body):
        pass

    assert router.registered == (int(MsgType.PING), int(MsgType.PONG))
