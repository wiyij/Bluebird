"""统一错误码与中文文案（前后端共用一份）。

与 docs/protocol.md 第 5 章逐行对应；v2 只保留 10 个码——没有口令、没有会话
令牌、没有文件传输、没有管理员踢人，对应的码一并删掉。

用法：
    from shared.errors import ErrorCode, message_for
    ErrorCode.NICK_TAKEN               # 1001
    message_for(ErrorCode.NICK_TAKEN)  # '该昵称已有人使用，请换一个'
"""

from __future__ import annotations

from enum import IntEnum


class ErrorCode(IntEnum):
    """协议 v2 的全部返回码。0 表示成功，不产生 ERROR 帧。"""

    OK = 0

    # ---- 1xxx 会话：一律断开连接 ----
    NICK_TAKEN = 1001          # 昵称已被占用（在线唯一）
    VERSION_MISMATCH = 1003    # 协议版本不符
    BAD_NICKNAME = 1004        # 昵称格式非法
    NOT_LOGGED_IN = 1006       # 未登录就发了业务帧

    # ---- 2xxx 报文：连接保持 ----
    MESSAGE_TOO_LONG = 2001    # 消息过长（> 2000 字节）
    RATE_LIMITED = 2002        # 发送过于频繁
    BAD_FRAME = 2003           # 报文格式错误
    UNKNOWN_TYPE = 2004        # 未知消息类型

    # ---- 3xxx 业务：连接保持 ----
    USER_OFFLINE = 3001        # 私聊对象不在线（不暂存）

    # ---- 5xxx ----
    INTERNAL_ERROR = 5001      # 服务端内部错误


#: 客户端直接展示的中文文案（与协议文档第 5 章一致）
MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.NICK_TAKEN: "该昵称已有人使用，请换一个",
    ErrorCode.VERSION_MISMATCH: "客户端版本过旧，请更新后再连接",
    ErrorCode.BAD_NICKNAME: "昵称需 1—16 个字符，仅支持中英文、数字与下划线",
    ErrorCode.NOT_LOGGED_IN: "请先登录",
    ErrorCode.MESSAGE_TOO_LONG: "消息超过 2000 字节，请分段发送",
    ErrorCode.RATE_LIMITED: "发送太快了，请稍后再试",
    ErrorCode.BAD_FRAME: "客户端数据异常，请重启客户端",
    ErrorCode.UNKNOWN_TYPE: "客户端与服务端版本不匹配",
    ErrorCode.USER_OFFLINE: "对方不在线",
    ErrorCode.INTERNAL_ERROR: "服务器开小差了，请稍后重连",
}

#: 收到后必须断开连接的码（协议文档第 5 章「连接」列）
FATAL_CODES: frozenset[ErrorCode] = frozenset({
    ErrorCode.NICK_TAKEN,
    ErrorCode.VERSION_MISMATCH,
    ErrorCode.BAD_NICKNAME,
    ErrorCode.NOT_LOGGED_IN,
})


def message_for(code: int | ErrorCode) -> str:
    """把错误码翻译成中文；未知码返回兜底文案，绝不抛异常。"""
    try:
        return MESSAGES[ErrorCode(code)]
    except (ValueError, KeyError):
        return "未知错误"


__all__ = ["ErrorCode", "MESSAGES", "FATAL_CODES", "message_for"]
