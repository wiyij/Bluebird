"""GUI 的公共零件：颜色、字体、只读消息框。

三个界面文件（gui / chatui / dialogs）共用这一份，避免「群聊窗和私聊窗的字号
不一样」这种低级的视觉不一致。

关于配色：协议里的 ``black/green/blue/red`` 是**语义色**，不是十六进制。
这里给的是投影到屏幕上的具体色值——四色都选得足够深，白底上都能看清。
"""

from __future__ import annotations

import tkinter as tk

from shared.protocol import MAX_TEXT_BYTES

# ---- 字体 ----
#: tkinter 默认字体在中文 Windows 上会退化成宋体，部分机器直接显示方块。
#: 显式指定微软雅黑（《结果示例》6.2 ⑤）。
FONT_FAMILY = "微软雅黑"
SIZE_BODY = 11
SIZE_SMALL = 9
SIZE_TITLE = 15
SIZE_BRAND = 12

# ---- 颜色 ----
#: 协议四色 → 屏幕色值。和 docs/protocol.md 第 4.3 章的取值一一对应。
COLOR_HEX: dict[str, str] = {
    "black": "#1f1f1f",
    "green": "#137333",
    "blue": "#0b57d0",
    "red": "#c5221f",
}
#: 设置面板里给人看的名字
COLOR_LABEL: dict[str, str] = {
    "black": "黑色",
    "green": "绿色",
    "blue": "蓝色",
    "red": "红色",
}

BG = "#ffffff"
PANEL_BG = "#f5f6f8"
BORDER = "#d8dbe0"
MUTED = "#6b7280"
DANGER = "#c5221f"
OK = "#1e8e3e"
BANNER_BG = "#fff4ce"
BANNER_FG = "#7a4b00"

# ---- 尺寸 ----
WINDOW_LOGIN = (430, 292)
WINDOW_CHAT = (900, 640)
WINDOW_PRIVATE = (460, 420)
USERLIST_WIDTH = 140
TOPBAR_HEIGHT = 40

#: 屏幕缩放系数（150% 缩放时 = 1.5）。像素常量都要过 ``px()`` 才不会被压小。
#: 为什么需要它：进程声明 DPI 感知之后，Tk 的坐标变成物理像素，窗口不会
#: 再被 Windows 位图拉伸（字也清楚了），但写死的 900×640 在 150% 屏上就显得偏小。
SCALE = 1.0


def init_scale(dpi: float) -> float:
    """按系统 DPI 定缩放系数；100% 屏上是 1.0，什么都不变。"""
    global SCALE
    SCALE = max(1.0, dpi / 96.0)
    return SCALE


def px(value: float) -> int:
    """逻辑像素 → 当前缩放下的实际像素。"""
    return int(round(value * SCALE))


def px_pair(size: tuple[int, int]) -> tuple[int, int]:
    return px(size[0]), px(size[1])


def font(size: int = SIZE_BODY, weight: str = "normal") -> tuple[str, int, str]:
    return (FONT_FAMILY, size, weight)


def make_chat_text(parent: tk.Misc, **kwargs) -> tk.Text:
    """造一个「只读 + 自动换行 + 已配好颜色标签」的消息框。

    用 ``state="disabled"`` 是《结果示例》6.2 的隐含要求：消息区不许用户编辑，
    否则光标一进去就会和「贴底滚动」打架。
    """
    text = tk.Text(
        parent, wrap="word", state="disabled", font=font(),
        bg=BG, fg=COLOR_HEX["black"], relief="flat", highlightthickness=0,
        padx=12, pady=8, spacing1=2, spacing3=3, cursor="arrow", **kwargs,
    )
    for name, hex_value in COLOR_HEX.items():
        text.tag_configure(f"c_{name}", foreground=hex_value)
    text.tag_configure("system", foreground=MUTED, justify="center")
    text.tag_configure("error", foreground=DANGER)
    return text


def append_line(text: tk.Text, line: str, tags: tuple[str, ...] = ()) -> None:
    """往只读消息框里追加一行，并处理「贴底」规则。

    三条硬性规则（《结果示例》3.1）：
    ① 新消息永远追加在末尾；② 默认滚到底；③ 用户正在往上翻时**不**强制拉回底部。
    实现就是先看 ``yview()[1]`` 是不是已经贴底，再决定要不要 ``see("end")``。
    """
    at_bottom = is_at_bottom(text)
    text.configure(state="normal")
    text.insert("end", line, tags)
    text.configure(state="disabled")
    if at_bottom:
        text.see("end")


def is_at_bottom(text: tk.Text) -> bool:
    """滚动条是否停在最底部（0.999 是留一点浮点误差的余量）。"""
    try:
        return text.yview()[1] >= 0.999
    except tk.TclError:              # 控件还没映射
        return True


def count_bytes(text: str) -> int:
    """协议按 UTF-8 **字节**限长，不是按字符（一个汉字 3 字节、emoji 4 字节）。"""
    return len(text.encode("utf-8"))


def counter_text(text: str) -> str:
    used = count_bytes(text)
    return f"{used}/{MAX_TEXT_BYTES} 字节"
