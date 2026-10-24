"""A17 心跳与超时踢除的判定逻辑。

三块东西，只有中间这块值得单独测：

    客户端   每 15 s 发一个 PING                      → client/cli.py
    服务端   收到**任何**帧都刷新 ``last_active``；
             每 5 s 扫一次，45 s 没帧就判死             → server/server.py 的 _sweep_loop
    判定     ``expired(clients, now, timeout)``          → 本文件，纯函数，可注入假时钟

为什么判死时间是心跳的 3 倍：连续丢 3 个 PING 才算死，单次网络抖动不误杀。
这同时满足 D1 判据「空闲 1 分钟连接仍在」和 A17 判据「45—50 s 内其余端看到离线」。

为什么不用 TCP keepalive：Windows 默认 2 小时才开始探测，等于没有。
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import TYPE_CHECKING

from shared.config import IDLE_TIMEOUT

if TYPE_CHECKING:                      # 只为类型检查，避免与 connection 循环导入
    from server.connection import Client


def expired(
    clients: Iterable["Client"],
    *,
    now: float | None = None,
    timeout: float = IDLE_TIMEOUT,
) -> list["Client"]:
    """返回所有「已登录且超过 timeout 没收到任何帧」的连接。

    只考虑 ``authed`` 的连接：登录阶段有自己的 10 s 时限（协议第 1 章），
    不归心跳管，否则会把正在输口令／昵称的人误杀。
    """
    moment = time.monotonic() if now is None else now
    return [client for client in clients
            if client.authed and moment - client.last_active > timeout]


__all__ = ["expired"]
