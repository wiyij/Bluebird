"""GUI 的公共零件：配色、字体、输入框、按钮、只读消息框。

三个界面文件（gui / chatui / dialogs）共用这一份，避免「群聊窗和私聊窗的字号
不一样」这种低级的视觉不一致。

设计口径：
- 底色是一层很浅的冷灰（``BG``），内容放在白卡片（``SURFACE``）上，靠 1px 边框分层，
  不堆阴影——tkinter 画不出柔和的投影，硬画只会显脏。
- 强调色只有一种蓝（``ACCENT``），只给「主操作」用；其余按钮一律无底色。
- 协议里的 black / green / blue / red 是**语义色**，不是主题色；这里给的是投影到
  屏幕上的具体色值，四个都压得足够深，白底上都能看清。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

from shared.protocol import MAX_TEXT_BYTES

# ---- 字体 ----
#: tkinter 默认字体在中文 Windows 上会退化成宋体，部分机器直接显示方块。
#: 显式指定微软雅黑（《结果示例》6.2 ⑤）。
FONT_FAMILY = "微软雅黑"
SIZE_BODY = 11
SIZE_SMALL = 9
SIZE_SUBTLE = 10
SIZE_TITLE = 22
SIZE_BRAND = 12

# ---- 语义色（协议四色）----
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

# ---- 中性色 / 界面色 ----
BG = "#f6f7f9"            # 窗口底
SURFACE = "#ffffff"       # 卡片、消息区
PANEL_BG = "#f1f3f6"      # 顶栏、侧栏
BORDER = "#e3e6eb"        # 1px 分隔线
DIVIDER = "#edeff3"       # 更轻的分隔线
INK = "#202124"           # 正文
MUTED = "#5f6368"         # 次要文字
SUBTLE = "#6f7680"        # 提示、占位符（比 MUTED 浅，但仍要过白底对比度）
ACCENT = "#0b57d0"        # 主操作
ACCENT_DARK = "#0842a0"
ACCENT_SOFT = "#e8f0fe"
OK = "#188038"
DANGER = "#c5221f"
WARN_BG = "#fef7e0"
WARN_FG = "#8a5a00"
#: 旧名字，chatui 里断线横幅还在用
BANNER_BG = WARN_BG
BANNER_FG = WARN_FG

# ---- 尺寸 ----
WINDOW_LOGIN = (460, 444)
WINDOW_CHAT = (960, 680)
WINDOW_PRIVATE = (500, 480)
USERLIST_WIDTH = 168
TOPBAR_HEIGHT = 44

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


def logo(master: tk.Misc, size: int) -> tk.PhotoImage | None:
    """取应用图标，按「逻辑像素 × 屏幕缩放」缩到合适大小。

    每次新建一个 PhotoImage（数量是个位数），调用方把返回值挂在控件上即可——
    模块级缓存会在换 Tk 解释器（登录窗销毁后重建）时拿到失效的图片。
    """
    path = Path(__file__).resolve().parent.parent / "assets" / "bluebird.png"
    if not path.exists():
        return None
    image = tk.PhotoImage(master=master, file=str(path))
    factor = max(1, round(image.width() / max(1, size * SCALE)))
    if factor > 1:
        image = image.subsample(factor, factor)
    return image


# ---------------------------------------------------------------- 控件


def field(parent: tk.Misc, textvariable: tk.Variable, *,
          width: int | None = None, justify: str = "left") -> tk.Frame:
    """带焦点描边的单行输入框。返回外框，真正的 Entry 在 ``frame.entry``。"""
    box = tk.Frame(parent, bg=SURFACE, highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=ACCENT)
    entry = tk.Entry(
        box, textvariable=textvariable, width=width, justify=justify,
        font=font(), bd=0, relief="flat", bg=SURFACE, fg=INK,
        insertbackground=ACCENT, highlightthickness=0, disabledbackground=PANEL_BG,
    )
    entry.pack(fill="x", padx=px(10), pady=px(7))
    entry.bind("<FocusIn>", lambda _e: box.configure(highlightbackground=ACCENT))
    entry.bind("<FocusOut>", lambda _e: box.configure(highlightbackground=BORDER))
    box.entry = entry                       # type: ignore[attr-defined]
    return box


def primary_button(parent: tk.Misc, text: str, command) -> tk.Button:
    """主操作按钮：实心强调色，悬停加深。"""
    button = tk.Button(
        parent, text=text, command=command, font=font(SIZE_BODY, "bold"),
        bg=ACCENT, fg="#ffffff", activebackground=ACCENT_DARK,
        activeforeground="#ffffff", disabledforeground="#dbe3f2",
        relief="flat", bd=0, highlightthickness=0, cursor="hand2",
        padx=px(18), pady=px(8),
    )
    button.bind("<Enter>", lambda _e: _hover(button, ACCENT_DARK))
    button.bind("<Leave>", lambda _e: _hover(button, ACCENT))
    return button


def ghost_button(parent: tk.Misc, text: str, command, *,
                 bg: str = PANEL_BG, fg: str = MUTED) -> tk.Button:
    """无底色的次要按钮：悬停时浮一层浅蓝。"""
    button = tk.Button(
        parent, text=text, command=command, font=font(SIZE_SMALL),
        bg=bg, fg=fg, activebackground=ACCENT_SOFT, activeforeground=ACCENT,
        relief="flat", bd=0, highlightthickness=0, cursor="hand2",
        padx=px(10), pady=px(4),
    )
    button.bind("<Enter>", lambda _e: _tint(button, ACCENT_SOFT, ACCENT))
    button.bind("<Leave>", lambda _e: _tint(button, bg, fg))
    return button


def _hover(button: tk.Button, color: str) -> None:
    if str(button.cget("state")) == "normal":
        button.configure(bg=color)


def _tint(button: tk.Button, bg: str, fg: str) -> None:
    if str(button.cget("state")) == "normal":
        button.configure(bg=bg, fg=fg)


def scrollbar(parent: tk.Misc, command) -> tk.Scrollbar:
    """细滚动条：默认那根带 3D 边框的滚动条和整套配色不搭。"""
    return tk.Scrollbar(
        parent, command=command, orient="vertical", width=px(10),
        bg="#c9ced6", troughcolor=SURFACE, activebackground="#9aa3af",
        relief="flat", bd=0, highlightthickness=0, elementborderwidth=0,
    )


def hairline(parent: tk.Misc, *, bg: str = BORDER) -> tk.Frame:
    return tk.Frame(parent, bg=bg, height=1)


# ---------------------------------------------------------------- 消息区


def make_chat_text(parent: tk.Misc, **kwargs) -> tk.Text:
    """造一个「只读 + 自动换行 + 已配好颜色标签」的消息框。

    用 ``state="disabled"`` 是《结果示例》6.2 的隐含要求：消息区不许用户编辑，
    否则光标一进去就会和「贴底滚动」打架。
    """
    text = tk.Text(
        parent, wrap="word", state="disabled", font=font(),
        bg=SURFACE, fg=INK, relief="flat", highlightthickness=0,
        padx=px(20), pady=px(14), spacing1=px(3), spacing3=px(7),
        cursor="arrow", insertwidth=0, **kwargs,
    )
    for name, hex_value in COLOR_HEX.items():
        text.tag_configure(f"c_{name}", foreground=hex_value)
    #: 自己的消息靠右，和别人的消息在视觉上分栏（协议格式不变，只改对齐）
    text.tag_configure("mine", justify="right")
    #: 系统提示居中、浅灰、小一号，不再套 [system：…] 的方括号
    text.tag_configure("system", foreground=SUBTLE, justify="center",
                       font=font(SIZE_SMALL), spacing1=px(9), spacing3=px(3))
    text.tag_configure("error", foreground=DANGER, justify="center",
                       font=font(SIZE_SMALL))
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
