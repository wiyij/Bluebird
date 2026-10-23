"""消息分发：type → 处理函数。

读循环只负责「读一帧」，分发给谁由这里决定。这样新增一种消息类型，
只需要写一个函数加一行装饰器，不用动读循环——A08 的完成判据就是这一条。

    router = Router()

    @router.handler(MsgType.PING)
    async def on_ping(client: Client, body: dict) -> None:
        await client.send(MsgType.PONG, {"server_time": int(time.time())})

    await router.dispatch(client, msg_type, body)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from shared.errors import ErrorCode
from shared.protocol import MsgType, ProtocolError

if TYPE_CHECKING:                      # 只为类型检查，运行时不导入，避免循环依赖
    from server.connection import Client

log = logging.getLogger("router")

Handler = Callable[["Client", dict], Awaitable[None]]


class Router:
    """类型码到处理函数的映射表，外加一条统一的出错兜底。"""

    def __init__(self) -> None:
        self._handlers: dict[int, Handler] = {}

    # ---------------------------------------------------------------- 注册

    def register(self, msg_type: int | MsgType, handler: Handler) -> None:
        code = int(msg_type)
        if code in self._handlers:
            raise ValueError(f"消息类型 0x{code:02X} 已经注册过，不允许覆盖")
        self._handlers[code] = handler

    def handler(self, msg_type: int | MsgType):
        """装饰器写法：``@router.handler(MsgType.PING)``。"""
        def decorator(fn: Handler) -> Handler:
            self.register(msg_type, fn)
            return fn
        return decorator

    # ---------------------------------------------------------------- 分发

    @property
    def registered(self) -> tuple[int, ...]:
        return tuple(sorted(self._handlers))

    def get(self, msg_type: int) -> Handler | None:
        return self._handlers.get(msg_type)

    async def dispatch(self, client: "Client", msg_type: int, body: dict) -> bool:
        """把一帧交给对应处理函数。

        返回 ``True`` 表示已处理；``False`` 表示类型未知，已回 2004。
        处理函数内部抛异常时回 5001，但**不断开连接**——一条坏消息不该
        把整个会话带走。
        """
        handler = self._handlers.get(msg_type)
        if handler is None:
            log.warning("unknown type 0x%02X from %s", msg_type, client.label)
            await client.send_error(
                ErrorCode.UNKNOWN_TYPE,
                f"未知消息类型 0x{msg_type:02X}",
            )
            return False

        try:
            await handler(client, body)
        except ProtocolError as exc:
            log.warning("protocol error in handler 0x%02X: %s", msg_type, exc)
            if exc.code is not None:
                await client.send_error(exc.code, exc.detail or exc.reason)
        except Exception:                       # noqa: BLE001 - 兜底，防止单条消息带崩进程
            log.exception("handler for 0x%02X crashed", msg_type)
            await client.send_error(ErrorCode.INTERNAL_ERROR)
        return True


__all__ = ["Router", "Handler"]
