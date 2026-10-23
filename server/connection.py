"""单个客户端的连接封装：发帧、回错误、关闭。

这一层只管「怎么把一帧写到对端」，不关心业务。身份字段在登录成功后被填上：
``nickname``（在线唯一）与 ``color``（black / green / blue / red）。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from shared.errors import ErrorCode, message_for
from shared.protocol import DEFAULT_COLOR, MsgType, encode_frame


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
    leave_reason: str = "disconnect"  # logout / kicked / disconnect

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
        """直接写一整帧（广播时复用同一份编码结果，不重复序列化）。"""
        self.writer.write(frame)
        await self.writer.drain()      # 背压：缓冲满时在这里等

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

    async def close(self) -> None:
        self.state = "closed"
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass


__all__ = ["Client"]
