"""应用层协议 v2 的帧编解码。

与 docs/protocol.md 一一对应：

    [ magic 2B ][ ver 1B ][ type 1B ][ length 4B 大端 ][ body: UTF-8 JSON ]
      0x4348      0x02                                               ≤ 65528

v2 相对 v1 的变化（详见协议文档第 4 章）：
- 删除文件传输 4 种（0x40—0x43）；
- 新增颜色 2 种（0x04 SET_COLOR、0x05 USER_COLOR）；
- 登录不再有口令、令牌与断点续传，只认昵称；
- 单条正文上限从 8192 降到 2000 字节。

三条实现纪律不变：
1. 解析一律用 `readexactly`；
2. 先验长度再读体；
3. 报文体必须是 JSON 对象。

异常约定：
- 协议违规 → ``ProtocolError``（带 code，调用方据此决定回什么帧、要不要断）；
- 对端正常关闭 → ``asyncio.IncompleteReadError`` 原样抛出，调用方安静收尾。
"""

from __future__ import annotations

import asyncio
import json
import struct
from enum import IntEnum
from typing import Any

from shared.errors import ErrorCode

# ---- 帧常量 ----
MAGIC = 0x4348                    # ASCII "CH"
VERSION = 2                       # v2：无口令、无文件、有颜色
HEADER = struct.Struct(">HBBI")   # magic, ver, type, length（大端）
HEADER_SIZE = HEADER.size         # 8
MAX_FRAME = 65536                 # 整帧上限（含首部）
MAX_BODY = MAX_FRAME - HEADER_SIZE  # 65528，length 字段的上限
MAX_TEXT_BYTES = 2000             # 单条聊天正文上限（UTF-8 字节）

# ---- 颜色 ----
DEFAULT_COLOR = "black"
COLORS: tuple[str, ...] = ("black", "green", "blue", "red")


class MsgType(IntEnum):
    """协议文档第 3 章的 15 种消息类型。"""

    LOGIN_REQ = 0x01
    LOGIN_RESP = 0x02
    LOGOUT = 0x03
    SET_COLOR = 0x04
    USER_COLOR = 0x05

    CHAT_GROUP = 0x10
    CHAT_PRIVATE = 0x11
    CHAT_SYSTEM = 0x12

    USER_LIST = 0x20
    USER_JOIN = 0x21
    USER_LEAVE = 0x22

    PING = 0x30
    PONG = 0x31

    ERROR = 0x50
    ACK = 0x60


class ProtocolError(Exception):
    """帧头非法、版本不符、超长、JSON 解析失败。

    :param reason: 机器可读的原因，写日志用
    :param detail: 人类可读的补充信息
    :param code: 要回给对端的错误码；``None`` 表示不回包、直接断开
    """

    def __init__(self, reason: str, detail: str = "", code: int | None = None) -> None:
        self.reason = reason
        self.detail = detail
        self.code = code
        super().__init__(f"{reason}: {detail}" if detail else reason)


def encode_frame(mtype: int | MsgType, body: dict[str, Any] | None = None) -> bytes:
    """把一条消息编码成完整的一帧。

    :raises ValueError: 参数不合法（非 dict 的 body、未知类型码、超出长度上限）。
        这是**本机编程错误**，不是网络错误，所以不抛 ProtocolError。
    """
    try:
        type_code = int(mtype)
    except (TypeError, ValueError):
        raise ValueError(f"消息类型必须是整数：{mtype!r}") from None
    if not 0 <= type_code <= 0xFF:
        raise ValueError(f"消息类型必须是 0—255：{type_code}")

    payload: dict[str, Any] = {} if body is None else body
    if not isinstance(payload, dict):
        raise ValueError(f"报文体必须是 dict，收到 {type(payload).__name__}")

    try:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"报文体无法序列化为 JSON：{exc}") from exc

    if len(raw) > MAX_BODY:
        raise ValueError(f"报文体 {len(raw)} 字节超过上限 {MAX_BODY}（整帧 {MAX_FRAME}）")

    return HEADER.pack(MAGIC, VERSION, type_code, len(raw)) + raw


async def read_frame(
    reader: asyncio.StreamReader,
    max_body: int = MAX_BODY,
) -> tuple[int, dict[str, Any]]:
    """从流里读一整帧，返回 ``(type, body)``。

    ``type`` 原样返回 int（不转 MsgType），未知类型由分发层回 2004。

    :raises asyncio.IncompleteReadError: 对端关闭或数据不足（正常收尾路径）
    :raises ProtocolError: 帧头非法 / 版本不符 / 超长 / JSON 解析失败
    """
    header = await reader.readexactly(HEADER_SIZE)
    magic, version, msg_type, length = HEADER.unpack(header)

    if magic != MAGIC:
        # 回包也没有意义：对端说的根本不是我们的协议，直接断
        raise ProtocolError("bad_magic", f"0x{magic:04X}，期望 0x{MAGIC:04X}")

    if version != VERSION:
        raise ProtocolError(
            "bad_version", f"ver={version}，期望 {VERSION}",
            code=ErrorCode.VERSION_MISMATCH,
        )

    if length > max_body:
        # length 可能是攻击者构造的，先判再读，绝不分配
        raise ProtocolError("frame_too_large", f"length={length}，上限 {max_body}")

    payload = await reader.readexactly(length) if length else b""

    try:
        body = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ProtocolError("bad_json", f"不是合法 UTF-8：{exc}", code=ErrorCode.BAD_FRAME) from exc
    except json.JSONDecodeError as exc:
        raise ProtocolError("bad_json", f"JSON 解析失败：{exc}", code=ErrorCode.BAD_FRAME) from exc

    if not isinstance(body, dict):
        raise ProtocolError(
            "bad_body", f"报文体必须是对象，收到 {type(body).__name__}",
            code=ErrorCode.BAD_FRAME,
        )

    return int(msg_type), body


def is_known_type(msg_type: int) -> bool:
    """类型码是否在协议 v2 的 15 种之内（分发层用它决定要不要回 2004）。"""
    try:
        MsgType(msg_type)
    except ValueError:
        return False
    return True


def is_valid_color(color: object) -> bool:
    return isinstance(color, str) and color in COLORS


def text_too_long(text: str) -> bool:
    """按 UTF-8 **字节数**判断正文是否超限（不是字符数：一个汉字 3 字节、emoji 4 字节）。"""
    return len(text.encode("utf-8")) > MAX_TEXT_BYTES


__all__ = [
    "MAGIC", "VERSION", "HEADER", "HEADER_SIZE", "MAX_FRAME", "MAX_BODY", "MAX_TEXT_BYTES",
    "DEFAULT_COLOR", "COLORS", "MsgType", "ProtocolError", "encode_frame", "read_frame",
    "is_known_type", "is_valid_color", "text_too_long",
]
