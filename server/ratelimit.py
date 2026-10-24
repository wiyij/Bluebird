"""单用户发送限流（A16）。

协议第 8 章：单用户滑动窗口 **10 条/秒**，超限回 `2002` 并丢弃，不扇出。

为什么用滑动窗口而不是固定窗口：固定窗口在整秒边界会放过 2 倍流量
（0.99 s 发 10 条、1.01 s 再发 10 条，两个窗口各 10 条，实际 1 秒内 20 条）。
滑动窗口记录每条消息的时间戳，窗口一直往前滑，边界问题自然消失。

为什么用 `time.monotonic()` 而不是 `time.time()`：墙上时钟会被 NTP 校正、
用户改时间，甚至倒退；单调时钟只会往前走，适合算间隔。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

RATE_LIMIT = 10        # 窗口内最多多少条
RATE_WINDOW = 1.0      # 窗口长度（秒）


class SlidingWindow:
    """一个用户一个实例；`allow()` 返回 False 表示这一条要丢掉。"""

    def __init__(
        self,
        limit: int = RATE_LIMIT,
        window: float = RATE_WINDOW,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit 至少为 1")
        if window <= 0:
            raise ValueError("window 必须为正数")
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: deque[float] = deque()

    def allow(self) -> bool:
        """记一次发送；窗口内已满 limit 条时返回 False，且不记录本次。"""
        now = self._clock()
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True

    def __len__(self) -> int:
        return len(self._hits)


__all__ = ["RATE_LIMIT", "RATE_WINDOW", "SlidingWindow"]
