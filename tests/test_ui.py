"""界面公共零件（client/ui.py）的纯函数测试：不起窗口，只验算法与常量。

真正需要窗口的那部分（贴底滚动、颜色 tag）在 ``tools/verify_d3_gui.py`` 里
用真控件验，这里只钉住两件最容易写错的事：
**长度按字节算**、**四色与协议一一对应**。
"""

from __future__ import annotations

import pytest

from client import ui
from shared.protocol import COLORS, MAX_TEXT_BYTES


@pytest.fixture(autouse=True)
def _restore_scale():
    """init_scale 改的是模块级全局，用例之间要还原。"""
    before = ui.SCALE
    yield
    ui.SCALE = before


def test_count_bytes_is_utf8_not_characters() -> None:
    # 一个汉字 3 字节、emoji 4 字节：这正是 700 个汉字会被 2001 拒掉的原因
    assert ui.count_bytes("abc") == 3
    assert ui.count_bytes("你好") == 6
    assert ui.count_bytes("😀") == 4
    assert ui.count_bytes("你好 😀") == 11


def test_counter_shows_bytes_and_limit() -> None:
    assert ui.counter_text("") == f"0/{MAX_TEXT_BYTES} 字节"
    assert ui.counter_text("你好") == f"6/{MAX_TEXT_BYTES} 字节"


def test_px_follows_dpi_scale() -> None:
    ui.init_scale(96)
    assert ui.px(100) == 100
    ui.init_scale(144)                     # 150% 缩放
    assert ui.px(100) == 150
    assert ui.px_pair((900, 640)) == (1350, 960)
    ui.init_scale(192)                     # 200%
    assert ui.px(100) == 200


def test_scale_never_goes_below_one() -> None:
    # 小于 100% 的缩放在 Windows 上很少见；真遇到也不该把窗口缩得看不清
    assert ui.init_scale(72) == 1.0


def test_color_tables_cover_exactly_the_protocol_colors() -> None:
    assert set(ui.COLOR_HEX) == set(COLORS)
    assert set(ui.COLOR_LABEL) == set(COLORS)
    # 四色都得足够深，白底上要看得清（亮度越低越好）
    for value in ui.COLOR_HEX.values():
        red, green, blue = (int(value[i:i + 2], 16) for i in (1, 3, 5))
        assert 0.299 * red + 0.587 * green + 0.114 * blue < 128


def test_color_names_are_chinese() -> None:
    assert ui.COLOR_LABEL["black"] == "黑色"
    assert ui.COLOR_LABEL["red"] == "红色"
