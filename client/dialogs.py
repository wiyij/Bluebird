"""两个弹出窗：私聊窗（Toplevel）和设置面板。

私聊窗为什么必须是**独立窗口**而不是在主窗里切上下文：演示时一旦把主窗切走，
老师看到的就不是「一个聊天室 + 若干私聊」，而是「一个来回切的窗口」，
《结果示例》3.3 因此明确要求「主聊天室不出现任何私聊内容」。

两个窗口都不写「请在这里…」「改完立即生效…」这类说明书式的话：控件自己会说话，
标题栏和按钮已经把该说的说完了。
"""

from __future__ import annotations

import tkinter as tk

from client import ui
from shared.protocol import COLORS, MAX_TEXT_BYTES


class PrivateWindow(tk.Toplevel):
    """与某个人的一对一窗口。标题「与 xxx 私聊」是它和群聊的唯一区分。"""

    def __init__(self, master: tk.Misc, app, peer: str) -> None:
        super().__init__(master)
        self.app = app
        self.peer = peer
        self.title(f"与 {peer} 私聊")
        self.geometry("{}x{}".format(*ui.px_pair(ui.WINDOW_PRIVATE)))
        self.minsize(ui.px(380), ui.px(360))
        self.configure(bg=ui.BG)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_header()
        self._build_messages()
        self._build_input()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.entry.focus_set()

    # ---------------------------------------------------------------- 布局

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=ui.SURFACE)
        header.grid(row=0, column=0, sticky="ew")

        avatar = tk.Canvas(header, width=ui.px(36), height=ui.px(36),
                           bg=ui.SURFACE, highlightthickness=0)
        avatar.pack(side="left", padx=(ui.px(18), ui.px(10)), pady=ui.px(10))
        avatar.create_oval(0, 0, ui.px(36), ui.px(36), fill=ui.ACCENT, outline="")
        avatar.create_text(ui.px(18), ui.px(18), text=(self.peer[:1] or "?").upper(),
                           fill="#ffffff", font=ui.font(ui.SIZE_BRAND, "bold"))

        names = tk.Frame(header, bg=ui.SURFACE)
        names.pack(side="left")
        tk.Label(names, text=self.peer, font=ui.font(ui.SIZE_BRAND, "bold"),
                 fg=ui.INK, bg=ui.SURFACE, anchor="w").pack(anchor="w")
        tk.Label(names, text="私聊", font=ui.font(ui.SIZE_SMALL),
                 fg=ui.SUBTLE, bg=ui.SURFACE, anchor="w").pack(anchor="w")

        ui.hairline(self).grid(row=1, column=0, sticky="ew")

    def _build_messages(self) -> None:
        wrap = tk.Frame(self, bg=ui.SURFACE)
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.messages = ui.make_chat_text(wrap)
        self.messages.grid(row=0, column=0, sticky="nsew")
        scroll = ui.scrollbar(wrap, self.messages.yview)
        scroll.grid(row=0, column=1, sticky="ns", pady=ui.px(6))
        self.messages.configure(yscrollcommand=scroll.set)

    def _build_input(self) -> None:
        outer = tk.Frame(self, bg=ui.BG)
        outer.grid(row=3, column=0, sticky="ew", padx=ui.px(14), pady=(ui.px(10), ui.px(8)))

        card = tk.Frame(outer, bg=ui.SURFACE, highlightthickness=1,
                        highlightbackground=ui.BORDER, highlightcolor=ui.BORDER)
        card.pack(fill="x")
        card.grid_columnconfigure(0, weight=1)

        self.entry = tk.Text(
            card, height=2, wrap="word", font=ui.font(), bd=0, relief="flat",
            bg=ui.SURFACE, fg=ui.INK, insertbackground=ui.ACCENT,
            highlightthickness=0, padx=ui.px(10), pady=ui.px(9),
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(ui.px(4), 0), pady=ui.px(4))
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<KeyRelease>", self._refresh_counter)

        self.send_button = ui.primary_button(card, "发送", self.send)
        self.send_button.grid(row=0, column=1, sticky="ns",
                              padx=(ui.px(6), ui.px(8)), pady=ui.px(8))

        self.counter = tk.Label(outer, text=ui.counter_text(""), font=ui.font(ui.SIZE_SMALL),
                                fg=ui.SUBTLE, bg=ui.BG, anchor="e")
        self.counter.pack(fill="x", pady=(ui.px(4), 0))

        self._placeholder = tk.Label(self.entry, text=f"发给 {self.peer}…", font=ui.font(),
                                     fg=ui.SUBTLE, bg=ui.SURFACE, cursor="xterm")
        self._placeholder.bind("<Button-1>", self._focus_entry)
        self.entry.bind("<Button-1>", self._focus_entry, add="+")
        for sequence in ("<<Paste>>", "<<Cut>>", "<<Clear>>"):
            self.entry.bind(sequence, self._sync_soon, add="+")
        self._sync_placeholder()

    # ---------------------------------------------------------------- 占位提示

    def _sync_placeholder(self) -> None:
        if not self._text():
            self._placeholder.place(x=0, y=0)
        else:
            self._placeholder.place_forget()

    def _focus_entry(self, _event: tk.Event | None = None) -> None:
        self.entry.focus_set()

    def _sync_soon(self, _event: tk.Event | None = None) -> None:
        self.after_idle(self._sync_placeholder)

    # ---------------------------------------------------------------- 渲染

    def append_message(self, sender: str, text: str, color: str = "black") -> None:
        if color not in ui.COLOR_HEX:
            color = "black"
        tags = [f"c_{color}"]
        if sender == getattr(self.app, "nickname", None):
            tags.append("mine")
        ui.append_line(self.messages, f"[{sender}]：{text}\n", tuple(tags))

    def append_system(self, text: str) -> None:
        ui.append_line(self.messages, f"{text}\n", ("system",))

    def append_error(self, text: str) -> None:
        ui.append_line(self.messages, f"{text}\n", ("error",))

    # ---------------------------------------------------------------- 交互

    def _on_return(self, event: tk.Event) -> str | None:
        if event.state & 0x0001:              # Shift+Enter = 换行
            return None
        self.send()
        return "break"

    def _text(self) -> str:
        return self.entry.get("1.0", "end-1c")

    def _refresh_counter(self, _event: tk.Event | None = None) -> None:
        raw = self._text()
        self.counter.configure(
            text=ui.counter_text(raw),
            fg=ui.DANGER if ui.count_bytes(raw) > MAX_TEXT_BYTES else ui.SUBTLE,
        )
        self._sync_placeholder()

    def send(self) -> None:
        text = self._text().strip()
        if not text:
            return
        if ui.count_bytes(text) > MAX_TEXT_BYTES:
            self.append_error(
                f"这条有 {ui.count_bytes(text)} 字节，超过 {MAX_TEXT_BYTES} 字节上限，分段发吧"
            )
            return
        if not self.app.is_online(self.peer):
            # 协议里对方离线是服务端回 3001；这里先拦一道，省一个来回
            self.append_system(f"{self.peer} 不在线，这条没发出去")
            return
        self.app.send_private(self.peer, text)
        self.entry.delete("1.0", "end")
        self._refresh_counter()
        self.entry.focus_set()

    def _on_close(self) -> None:
        self.app.close_private(self.peer)
        self.destroy()

    def on_disconnected(self) -> None:
        """主连接断了：私聊窗也别再装作能发消息。"""
        self.entry.configure(state="disabled", bg=ui.PANEL_BG)
        self.send_button.configure(state="disabled", bg="#b9c7e2",
                                   disabledforeground="#f4f6fa")
        self._placeholder.place_forget()
        self.append_error("连接断开了")


class SettingsDialog(tk.Toplevel):
    """设置面板：只有颜色一项。没有改名——改名＝退出重登（《结果示例》3.4）。"""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master)
        self.app = app
        self.title("设置")
        self.geometry(f"{ui.px(360)}x{ui.px(270)}")
        self.resizable(False, False)
        self.configure(bg=ui.SURFACE)
        self.transient(master)

        tk.Label(self, text="颜色", font=ui.font(ui.SIZE_BODY, "bold"),
                 bg=ui.SURFACE, fg=ui.INK, anchor="w").pack(
            fill="x", padx=ui.px(22), pady=(ui.px(20), ui.px(10)))

        self.choice = tk.StringVar(value=app.color)
        row = tk.Frame(self, bg=ui.SURFACE)
        row.pack(fill="x", padx=ui.px(22))
        for color in COLORS:
            tk.Radiobutton(
                row, text=ui.COLOR_LABEL[color], value=color, variable=self.choice,
                command=self._on_change, indicatoron=False,
                font=ui.font(ui.SIZE_SMALL, "bold"), fg=ui.COLOR_HEX[color],
                bg=ui.SURFACE, activebackground=ui.ACCENT_SOFT,
                selectcolor=ui.ACCENT_SOFT, relief="flat", bd=0,
                highlightthickness=0, cursor="hand2",
                padx=ui.px(10), pady=ui.px(6),
            ).pack(side="left", padx=(0, ui.px(6)))

        tk.Frame(self, bg=ui.DIVIDER, height=1).pack(
            fill="x", padx=ui.px(22), pady=ui.px(18))

        tk.Label(self, text="昵称", font=ui.font(ui.SIZE_SMALL),
                 bg=ui.SURFACE, fg=ui.MUTED, anchor="w").pack(fill="x", padx=ui.px(22))
        tk.Label(self, text=app.nickname, font=ui.font(ui.SIZE_BODY),
                 bg=ui.SURFACE, fg=ui.INK, anchor="w").pack(
            fill="x", padx=ui.px(22), pady=(ui.px(2), 0))

        self._done = ui.primary_button(self, "完成", self.destroy)
        self._done.pack(fill="x", padx=ui.px(22), pady=(ui.px(18), ui.px(18)))

    def _on_change(self) -> None:
        self.app.set_color(self.choice.get())


__all__ = ["PrivateWindow", "SettingsDialog"]
