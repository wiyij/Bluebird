"""A16 限流窗口的单元测试：用假时钟，不 sleep。"""

from __future__ import annotations

import pytest

from server.ratelimit import SlidingWindow


def test_allows_exactly_limit_times() -> None:
    now = [0.0]
    window = SlidingWindow(limit=3, window=1.0, clock=lambda: now[0])

    assert [window.allow() for _ in range(3)] == [True, True, True]
    assert window.allow() is False


def test_window_slides_so_the_11th_second_is_fine() -> None:
    now = [0.0]
    window = SlidingWindow(limit=2, window=1.0, clock=lambda: now[0])

    assert window.allow() is True
    assert window.allow() is True
    assert window.allow() is False

    now[0] = 1.0                       # 0 时刻那两条正好过期
    assert window.allow() is True
    assert window.allow() is True      # 窗口又空了两格
    assert window.allow() is False     # 满 2 条

    now[0] = 2.5                       # 1.0 时刻那两条也过期了
    assert window.allow() is True


def test_denied_calls_do_not_consume_quota() -> None:
    now = [0.0]
    window = SlidingWindow(limit=1, window=1.0, clock=lambda: now[0])

    assert window.allow() is True
    for _ in range(5):
        assert window.allow() is False
    assert len(window) == 1

    now[0] = 1.0
    assert window.allow() is True


def test_rejects_nonsense_arguments() -> None:
    with pytest.raises(ValueError):
        SlidingWindow(limit=0)
    with pytest.raises(ValueError):
        SlidingWindow(window=0)
