"""两个弹出窗：私聊窗（Toplevel）和设置面板。

私聊窗为什么必须是**独立窗口**而不是在主窗里切上下文：演示时一旦把主窗切走，
老师看到的就不是「一个聊天室 + 若干私聊」，而是「一个来回切的窗口」，
《结果示例》3.3 因此明确要求「主聊天室不出现任何私聊内容」。
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
        self.minsize(ui.px(320), ui.px(280))
        self.configure(bg=ui.BG)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        tk.Label(
            self, text=f"与 {peer} 私聊（只有你们两个人看得到）",
            font=ui.font(ui.SIZE_SMALL), fg=ui.MUTED, bg=ui.BG, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))

        wrap = tk.Frame(self, bg=ui.BG)
        wrap.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(0, weight=1)

        self.messages = ui.make_chat_text(wrap)
        self.messages.grid(row=0, column=0, sticky="nsew")
        scroll = tk.Scrollbar(wrap, command=self.messages.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.messages.configure(yscrollcommand=scroll.set)

        bottom = tk.Frame(self, bg=ui.BG)
        bottom.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 10))
        bottom.grid_columnconfigure(0, weight=1)

        self.entry = tk.Text(bottom, height=2, wrap="word", font=ui.font(),
                             relief="solid", borderwidth=1, highlightthickness=0)
        self.entry.grid(row=0, column=0, sticky="nsew")
        self.entry.bind("<Return>", self._on_return)

        self.send_button = tk.Button(bottom, text="发送", width=7, command=self.send)
        self.send_button.grid(row=0, column=1, sticky="ns", padx=(6, 0))

        self.counter = tk.Label(bottom, text=ui.counter_text(""), font=ui.font(ui.SIZE_SMALL),
                                fg=ui.MUTED, bg=ui.BG)
        self.counter.grid(row=1, column=0, sticky="e")
        self.entry.bind("<KeyRelease>", self._refresh_counter)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.entry.focus_set()

    # ---------------------------------------------------------------- 渲染

    def append_message(self, sender: str, text: str, color: str = "black") -> None:
        if color not in ui.COLOR_HEX:
            color = "black"
        ui.append_line(self.messages, f"[{sender}：{text}]\n", (f"c_{color}",))

    def append_system(self, text: str) -> None:
        ui.append_line(self.messages, f"[system：{text}]\n", ("system",))

    def append_error(self, text: str) -> None:
        ui.append_line(self.messages, f"{text}\n", ("error",))

    # ---------------------------------------------------------------- 交互

    def _on_return(self, event: tk.Event) -> str | None:
        if event.state & 0x0001:              # Shift+Enter = 换行
            return None
        self.send()
        return "break"

    def _refresh_counter(self, _event: tk.Event | None = None) -> None:
        used = ui.count_bytes(self.entry.get("1.0", "end-1c"))
        self.counter.configure(text=ui.counter_text(self.entry.get("1.0", "end-1c")),
                               fg=ui.DANGER if used > MAX_TEXT_BYTES else ui.MUTED)

    def send(self) -> None:
        text = self.entry.get("1.0", "end-1c").strip()
        if not text:
            return
        if ui.count_bytes(text) > MAX_TEXT_BYTES:
            self.append_error(f"消息超过 {MAX_TEXT_BYTES} 字节，请分段发送")
            return
        if not self.app.is_online(self.peer):
            # 协议里对方离线是服务端回 3001；这里先拦一道，省一个来回
            self.append_system(f"{self.peer} 不在线，消息没有发出去")
            return
        self.app.send_private(self.peer, text)
        self.entry.delete("1.0", "end")
        self._refresh_counter()
        self.entry.focus_set()

    def _on_close(self) -> None:
        self.app.close_private(self.peer)
        self.destroy()


class SettingsDialog(tk.Toplevel):
    """设置面板：只有颜色一项。没有改名——改名＝退出重登（《结果示例》3.4）。"""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master)
        self.app = app
        self.title("设置")
        self.geometry(f"{ui.px(320)}x{ui.px(230)}")
        self.resizable(False, False)
        self.configure(bg=ui.BG)
        self.transient(master)

        tk.Label(self, text="字体颜色", font=ui.font(ui.SIZE_BODY, "bold"),
                 bg=ui.BG, anchor="w").pack(fill="x", padx=16, pady=(16, 6))

        self.choice = tk.StringVar(value=app.color)
        row = tk.Frame(self, bg=ui.BG)
        row.pack(fill="x", padx=16)
        for color in COLORS:
            tk.Radiobutton(
                row, text=ui.COLOR_LABEL[color], value=color, variable=self.choice,
                command=self._on_change, font=ui.font(), bg=ui.BG,
                activebackground=ui.BG, fg=ui.COLOR_HEX[color],
                selectcolor=ui.BG, anchor="w", width=5,
            ).pack(side="left")

        tk.Label(self, text="改完立即生效，所有人都会看到你的新颜色。",
                 font=ui.font(ui.SIZE_SMALL), fg=ui.MUTED, bg=ui.BG, anchor="w",
                 wraplength=280, justify="left").pack(fill="x", padx=16, pady=(10, 0))

        tk.Frame(self, bg=ui.BORDER, height=1).pack(fill="x", padx=16, pady=12)

        tk.Label(self, text="昵称", font=ui.font(ui.SIZE_BODY, "bold"),
                 bg=ui.BG, anchor="w").pack(fill="x", padx=16)
        tk.Label(self, text=f"{app.nickname}（不可在这里修改；要改请退出重登）",
                 font=ui.font(ui.SIZE_SMALL), fg=ui.MUTED, bg=ui.BG, anchor="w",
                 wraplength=280, justify="left").pack(fill="x", padx=16, pady=(2, 0))

        tk.Button(self, text="完成", width=8, command=self.destroy).pack(pady=14)

    def _on_change(self) -> None:
        self.app.set_color(self.choice.get())


__all__ = ["PrivateWindow", "SettingsDialog"]
