"""单个客户端的连接封装：发帧、回错误、关闭。

这一层只管「怎么把一帧写到对端」，不关心业务。身份字段在登录成功后被填上：
``nickname``（在线唯一）与 ``color``（black / green / blue / red）。

两条防「一个客户端拖死整个房间」的纪律：

1. 每次写都带 ``SEND_TIMEOUT``：对端不读数据时 ``drain()`` 会一直挂着，
   而广播是串行的，挂一个就等于挂所有人。超时即 ``abort()``。
2. ``abort()`` 是立即断（不 flush）。写失败／心跳超时都走它，让本连接的读循环
   立刻醒过来、走 ``finally`` 把昵称从在线表里摘掉——否则就是「幽灵用户」。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from shared.config import SEND_TIMEOUT
from shared.errors import ErrorCode, message_for
from shared.protocol import DEFAULT_COLOR, MsgType, encode_frame

from server.ratelimit import SlidingWindow


@dataclass(eq=False)          # eq=False → 保留对象身份哈希，才能放进 set 当在线表
class Client:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    peer: tuple[str, int] | None = None
    connected_at: float = field(default_factory=time.time)

    # ---- 以下字段登录成功后才有值 ----
    nickname: str | None = None
    color: str = DEFAULT_COLOR
    since: int = field(default_factory=lambda: int(time.time()))
    state: str = "connected"          # connected / authed / closing / closed
    leave_reason: str = "disconnect"  # 只允许协议第 4.1 章的 logout / disconnect
    leave_note: str | None = None     # CHAT_SYSTEM 里的说法，如「超时下线」
    last_active: float = field(default_factory=time.monotonic)   # A17：最近收帧时刻
    limiter: SlidingWindow = field(default_factory=SlidingWindow)  # A16：10 条/秒

    @property
    def authed(self) -> bool:
        return self.state == "authed"

    @property
    def alive(self) -> bool:
        return not self.writer.is_closing()

    @property
    def label(self) -> str:
        """日志里怎么称呼这个连接。"""
        who = self.nickname or "未登录"
        if self.peer:
            return f"{who}@{self.peer[0]}:{self.peer[1]}"
        return who

    def touch(self) -> None:
        """收到**任何**一帧都调它——心跳判活的唯一依据。

        注意不能只在 PING 里调：一个正在刷屏的用户如果被判死，就是明显的误杀。
        """
        self.last_active = time.monotonic()

    def profile(self) -> dict:
        """广播给别人的公开信息：不带 peer、不带 token。"""
        return {
            "nickname": self.nickname,
            "color": self.color,
            "since": self.since,
        }

    # ---------------------------------------------------------------- 发送

    async def send(self, msg_type: int | MsgType, body: dict | None = None) -> None:
        await self.send_frame(encode_frame(msg_type, body if body is not None else {}))

    async def send_frame(self, frame: bytes) -> None:
        """写一整帧（广播时复用同一份编码结果，不重复序列化）。

        ``drain()`` 带超时：对端不读数据时它可能永远不返回，而调用方
        （``ChatServer._broadcast``）是串行的，一个人卡住等于全员卡住。
        超时抛 ``TimeoutError``，由调用方 ``abort()`` 掉这条连接。
        """
        self.writer.write(frame)
        await asyncio.wait_for(self.writer.drain(), SEND_TIMEOUT)

    async def send_error(
        self,
        code: int | ErrorCode,
        message: str | None = None,
        ref_msg_id: str | None = None,
    ) -> None:
        body: dict = {"code": int(code), "message": message or message_for(code)}
        if ref_msg_id:
            body["ref_msg_id"] = ref_msg_id
        await self.send(MsgType.ERROR, body)

    # ---------------------------------------------------------------- 关闭

    def abort(self) -> None:
        """立即断开，不 flush。

        用在「这条连接已经不可信」的三个地方：写超时、写失败、心跳判死。
        关键是让本连接的读循环立刻收到异常，从而走进 ``_forget`` 把昵称摘掉。
        """
        self.state = "closing"
        transport = getattr(self.writer, "transport", None)
        if transport is not None:
            transport.abort()
        else:                                  # 兜底：上面的属性取不到就直接 close
            self.writer.close()

    async def close(self) -> None:
        self.state = "closed"
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass


__all__ = ["Client"]
