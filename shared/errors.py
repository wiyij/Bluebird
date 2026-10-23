"""统一错误码与客户端文案（前后端共用一份）。

与 docs/protocol.md 第 5 章逐行对应。改这里必须同步文档，反之亦然；
两边不一致时以协议文档为准。

用法：
    from shared.errors import ErrorCode, message_for
    ErrorCode.NICK_TAKEN            # 1001
    message_for(ErrorCode.NICK_TAKEN)  # '该昵称已有人使用，请换一个'
"""

from __future__ import annotations

from enum import IntEnum


class ErrorCode(IntEnum):
    """协议 v1 的全部返回码。0 表示成功，不产生 ERROR 帧。"""

    OK = 0

    # ---- 1xxx 会话：一律断开连接 ----
    NICK_TAKEN = 1001          # 昵称已被占用
    BAD_PASSWORD = 1002        # 口令错误 / 失败次数过多
    VERSION_MISMATCH = 1003    # 协议版本不符
    BAD_NICKNAME = 1004        # 昵称格式非法
    SESSION_EXPIRED = 1005     # 会话已失效（token 无效）
    NOT_LOGGED_IN = 1006       # 未登录就发了业务帧

    # ---- 2xxx 报文：连接保持 ----
    MESSAGE_TOO_LONG = 2001    # 消息过长
    RATE_LIMITED = 2002        # 发送过于频繁
    BAD_FRAME = 2003           # 报文格式错误
    UNKNOWN_TYPE = 2004        # 未知消息类型

    # ---- 3xxx 业务：连接保持 ----
    USER_OFFLINE = 3001        # 目标用户不在线
    FILE_REJECTED = 3002       # 文件过大 / 并发超限
    TRANSFER_ABORTED = 3003    # 传输超时或已取消

    # ---- 4xxx / 5xxx ----
    KICKED = 4001              # 被管理员移除
    INTERNAL_ERROR = 5001      # 服务端内部错误


#: 客户端直接展示的中文文案（与协议文档第 5 章一致）
MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.NICK_TAKEN: "该昵称已有人使用，请换一个",
    ErrorCode.BAD_PASSWORD: "口令不正确，请重试",
    ErrorCode.VERSION_MISMATCH: "客户端版本过旧，请更新后再连接",
    ErrorCode.BAD_NICKNAME: "昵称需 1—16 个字符，仅支持中英文、数字与下划线",
    ErrorCode.SESSION_EXPIRED: "登录已过期，请重新登录",
    ErrorCode.NOT_LOGGED_IN: "请先登录",
    ErrorCode.MESSAGE_TOO_LONG: "消息超过 8192 字节，请分段发送",
    ErrorCode.RATE_LIMITED: "发送太快了，请稍后再试",
    ErrorCode.BAD_FRAME: "客户端数据异常，请重启客户端",
    ErrorCode.UNKNOWN_TYPE: "客户端与服务端版本不匹配",
    ErrorCode.USER_OFFLINE: "对方不在线，消息已暂存",
    ErrorCode.FILE_REJECTED: "文件超过 50 MB 或同时传输过多，请稍后再试",
    ErrorCode.TRANSFER_ABORTED: "传输中断，可重新发送",
    ErrorCode.KICKED: "你已被管理员移出聊天室",
    ErrorCode.INTERNAL_ERROR: "服务器开小差了，请稍后重连",
}

#: 收到后必须断开连接的码（协议文档第 5 章「连接」列）
FATAL_CODES: frozenset[ErrorCode] = frozenset({
    ErrorCode.NICK_TAKEN,
    ErrorCode.BAD_PASSWORD,
    ErrorCode.VERSION_MISMATCH,
    ErrorCode.BAD_NICKNAME,
    ErrorCode.SESSION_EXPIRED,
    ErrorCode.NOT_LOGGED_IN,
    ErrorCode.KICKED,
})


def message_for(code: int | ErrorCode) -> str:
    """把错误码翻译成中文；未知码返回兜底文案，绝不抛异常。"""
    try:
        return MESSAGES[ErrorCode(code)]
    except (ValueError, KeyError):
        return "未知错误"


__all__ = ["ErrorCode", "MESSAGES", "FATAL_CODES", "message_for"]
