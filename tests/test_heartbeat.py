"""A17：心跳判死的纯函数测试。

这里只测「谁该被踢」这一个判断，用假时钟，不 sleep、不起服务端；
真正的扫描循环在 ``tests/test_server.py::test_idle_client_is_kicked_...`` 里端到端验。
"""

from __future__ import annotations

from shared.config import IDLE_TIMEOUT
from server.heartbeat import expired


class FakeClient:
    """只实现 ``expired()`` 用到的两个属性。"""

    def __init__(self, last_active: float, *, state: str = "authed", nickname: str = "x") -> None:
        self.last_active = last_active
        self.state = state
        self.nickname = nickname

    @property
    def authed(self) -> bool:
        return self.state == "authed"


def test_fresh_client_is_kept() -> None:
    assert expired([FakeClient(last_active=100.0)], now=100.0, timeout=45) == []


def test_client_past_timeout_is_expired() -> None:
    stale = FakeClient(last_active=100.0, nickname="alice")
    assert expired([stale], now=100.0 + 45.1, timeout=45) == [stale]


def test_exactly_at_the_boundary_is_still_alive() -> None:
    # 判据是「> timeout」而不是「>= timeout」：正好 45 s 这一次不杀，留给下一次扫描
    assert expired([FakeClient(last_active=100.0)], now=145.0, timeout=45) == []


def test_pre_login_connection_is_not_swept() -> None:
    # 登录阶段有 10 s 时限（协议第 1 章），不归心跳管
    pending = FakeClient(last_active=0.0, state="connected")
    assert expired([pending], now=1000.0, timeout=45) == []


def test_only_the_stale_ones_are_returned() -> None:
    alive = FakeClient(last_active=100.0, nickname="alive")
    stale_a = FakeClient(last_active=50.0, nickname="a")
    stale_b = FakeClient(last_active=10.0, nickname="b")
    result = expired([alive, stale_a, stale_b], now=100.0, timeout=45)
    assert result == [stale_a, stale_b]


def test_three_heartbeats_worth_of_silence_is_the_default() -> None:
    # 45 s = 3 × 15 s，这个倍数关系写在协议第 8 章，改一个就要改另一个
    assert IDLE_TIMEOUT == 45.0
