"""「上次连的是哪台服务器」与启动器目标选择的单元测试。

2026-10-26 的现场问题：两台机器各自双击快捷方式，启动器各自起了一个本地服务端、
登录窗又都预填 127.0.0.1，于是两台机器各在各的空房间里——看起来就是「同一个局域网
进不了同一个聊天室」。这一组用例把修法钉住：**记住上次连的地址，连过别人的机器就
不再在本机起房间**。
"""

from __future__ import annotations

import pytest

import client.session as session
import start_chat


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "STATE_PATH", tmp_path / "last_host.txt")


def test_roundtrip() -> None:
    assert session.load_last_host() is None
    session.save_last_host("172.20.10.5", 8888)
    assert session.load_last_host() == ("172.20.10.5", 8888)


@pytest.mark.parametrize("content", ["", "abc", "1.2.3.4:", "1.2.3.4:abc", "1.2.3.4:0", "1.2.3.4:70000"])
def test_garbage_state_is_ignored(content: str) -> None:
    session.STATE_PATH.write_text(content, encoding="utf-8")
    assert session.load_last_host() is None


def test_save_overwrites() -> None:
    session.save_last_host("10.0.0.1", 8888)
    session.save_last_host("10.0.0.2", 9000)
    assert session.load_last_host() == ("10.0.0.2", 9000)


def test_loopback_is_local() -> None:
    assert session.is_local_host("127.0.0.1")
    assert session.is_local_host("localhost")


def test_explicit_host_wins() -> None:
    assert start_chat.decide_target("10.0.0.9", 9999, False) == ("10.0.0.9", 9999, False)


def test_remembered_remote_host_means_client_only(monkeypatch) -> None:
    """上次连的是别人 → 这次只开客户端，不再在本机起一个空房间。"""
    monkeypatch.setattr(start_chat, "load_last_host", lambda: ("10.0.0.9", 8888))
    monkeypatch.setattr(start_chat, "is_local_host", lambda host: False)
    assert start_chat.decide_target(None, None, False) == ("10.0.0.9", 8888, True)


def test_remembered_local_host_still_starts_server(monkeypatch) -> None:
    monkeypatch.setattr(start_chat, "load_last_host", lambda: ("127.0.0.1", 8888))
    monkeypatch.setattr(start_chat, "is_local_host", lambda host: True)
    assert start_chat.decide_target(None, None, False) == ("127.0.0.1", 8888, False)


def test_no_history_means_single_machine_demo(monkeypatch) -> None:
    monkeypatch.setattr(start_chat, "load_last_host", lambda: None)
    assert start_chat.decide_target(None, None, False) == ("127.0.0.1", 8888, False)


def test_no_server_flag_is_respected(monkeypatch) -> None:
    monkeypatch.setattr(start_chat, "load_last_host", lambda: None)
    assert start_chat.decide_target(None, None, True) == ("127.0.0.1", 8888, True)
