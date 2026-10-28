"""主窗口：顶栏 / 消息区 / 在线列表 / 输入区（《结果示例》3.1 的四区布局）。

四条硬规矩，写在这里免得以后被「顺手优化」掉：

1. **贴底滚动**：新消息到达时只在「本来就在底部」的情况下才 ``see("end")``，
   用户往上翻历史时绝不抢滚动条（T06、T07）。
2. **颜色按发送者取**：消息帧里不带颜色，客户端查 ``self.users[nickname]["color"]``。
   改色只影响之后的新消息，已渲染的历史不受影响——因为插入时就用了固定 tag。
3. **私聊不进主窗**：``route_private`` 只往 Toplevel 里塞（T11）。
4. **线程边界**：这里所有方法都必须在主线程调；网络事件由 ``BluebirdApp``
   从 ``queue.Queue`` 取出来再进来，没有任何一行控件操作发生在网络线程。

视觉上只做三件事：一条顶栏、一块白消息区、一条浅灰侧栏；自己的消息靠右，
系统提示居中，其余留白。消息正文仍按 ``[昵称]：内容`` 渲染，不改协议字段。
"""

from __future__ import annotations

import tkinter as tk

from client import ui
from client.dialogs import PrivateWindow, SettingsDialog
from shared.protocol import MAX_TEXT_BYTES


class ChatFrame(tk.Frame):
    def __init__(self, master: tk.Misc, app, server_text: str) -> None:
        super().__init__(master, bg=ui.BG)
        self.app = app
        self.server_text = server_text
        self.users: dict[str, dict] = {}
        self.private_windows: dict[str, PrivateWindow] = {}
        self.settings: SettingsDialog | None = None
        self.connected = True
        self._build()

    # ---------------------------------------------------------------- 布局

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, minsize=ui.px(ui.USERLIST_WIDTH))
        self.grid_rowconfigure(3, weight=1)
        self._build_topbar()
        ui.hairline(self).grid(row=1, column=0, columnspan=2, sticky="ew")
        self._build_banner()
        self._build_messages()
        self._build_users()
        self._build_input()

    def _build_topbar(self) -> None:
        bar = tk.Frame(self, bg=ui.PANEL_BG, height=ui.px(ui.TOPBAR_HEIGHT))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=ui.PANEL_BG)
        left.pack(side="left", padx=(ui.px(16), 0))
        image = ui.logo(self, 18)
        if image is not None:
            self._logo = image
            tk.Label(left, image=image, bg=ui.PANEL_BG).pack(side="left", padx=(0, ui.px(8)))
        tk.Label(left, text="Bluebird", font=ui.font(ui.SIZE_BRAND, "bold"),
                 bg=ui.PANEL_BG, fg=ui.INK).pack(side="left")

        tk.Frame(left, bg=ui.BORDER, width=1, height=ui.px(16)).pack(
            side="left", padx=ui.px(12))

        self.status_dot = tk.Label(left, text="●", font=ui.font(9),
                                   fg=ui.OK, bg=ui.PANEL_BG)
        self.status_dot.pack(side="left")
        self.status_text = tk.Label(left, text="已连接", font=ui.font(ui.SIZE_SMALL),
                                    fg=ui.MUTED, bg=ui.PANEL_BG)
        self.status_text.pack(side="left", padx=(ui.px(4), ui.px(10)))
        tk.Label(left, text=self.server_text, font=ui.font(ui.SIZE_SMALL),
                 fg=ui.SUBTLE, bg=ui.PANEL_BG).pack(side="left")

        right = tk.Frame(bar, bg=ui.PANEL_BG)
        right.pack(side="right", padx=(0, ui.px(10)))
        self.settings_button = ui.ghost_button(right, "设置", self.open_settings,
                                               bg=ui.PANEL_BG)
        self.settings_button.pack(side="right", padx=(ui.px(8), 0))
        self.count_label = tk.Label(
            right, text="在线 0 人", font=ui.font(ui.SIZE_SMALL),
            fg=ui.ACCENT, bg=ui.ACCENT_SOFT, padx=ui.px(8), pady=ui.px(2),
        )
        self.count_label.pack(side="right")

    def _build_banner(self) -> None:
        """断线横幅：默认不显示，断线时才 grid 出来（A24）。"""
        self.banner = tk.Frame(self, bg=ui.WARN_BG)
        self.banner_label = tk.Label(self.banner, text="", bg=ui.WARN_BG,
                                     fg=ui.WARN_FG, font=ui.font(ui.SIZE_SMALL),
                                     anchor="w")
        self.banner_label.pack(side="left", padx=(ui.px(18), ui.px(8)), pady=ui.px(8))
        self.reconnect_button = tk.Button(
            self.banner, text="重新连接", font=ui.font(ui.SIZE_SMALL, "bold"),
            command=self.app.back_to_login, bg="#fffaf0", fg=ui.WARN_FG,
            activebackground="#fbeec9", activeforeground=ui.WARN_FG,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground="#e6d29a", cursor="hand2",
            padx=ui.px(12), pady=ui.px(4),
        )
        self.reconnect_button.pack(side="right", padx=(0, ui.px(14)))

    def _build_messages(self) -> None:
        wrap = tk.Frame(self, bg=ui.SURFACE)
        wrap.grid(row=3, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.messages = ui.make_chat_text(wrap)
        self.messages.grid(row=0, column=0, sticky="nsew")
        scroll = ui.scrollbar(wrap, self.messages.yview)
        scroll.grid(row=0, column=1, sticky="ns", pady=ui.px(6))
        self.messages.configure(yscrollcommand=scroll.set)

    def _build_users(self) -> None:
        wrap = tk.Frame(self, bg=ui.PANEL_BG)
        wrap.grid(row=3, column=1, rowspan=2, sticky="nsew")
        tk.Frame(wrap, bg=ui.BORDER, width=1).place(x=0, y=0, relheight=1.0)

        header = tk.Frame(wrap, bg=ui.PANEL_BG)
        header.pack(fill="x", padx=(ui.px(14), ui.px(10)), pady=(ui.px(12), ui.px(6)))
        tk.Label(header, text="成员", font=ui.font(ui.SIZE_SMALL, "bold"),
                 fg=ui.MUTED, bg=ui.PANEL_BG, anchor="w").pack(side="left")

        ui.hairline(wrap, bg=ui.DIVIDER).pack(fill="x", padx=ui.px(12))

        self.user_list = tk.Listbox(
            wrap, font=ui.font(), activestyle="none", bg=ui.PANEL_BG, fg=ui.INK,
            relief="flat", bd=0, highlightthickness=0, width=1,
            selectbackground=ui.ACCENT_SOFT, selectforeground=ui.INK,
            selectborderwidth=0, exportselection=False, cursor="hand2",
        )
        self.user_list.pack(fill="both", expand=True,
                            padx=(ui.px(8), ui.px(4)), pady=(ui.px(8), ui.px(10)))
        self.user_list.bind("<Double-Button-1>", self._on_user_double_click)

    def _build_input(self) -> None:
        outer = tk.Frame(self, bg=ui.BG)
        outer.grid(row=4, column=0, sticky="ew", padx=ui.px(16), pady=(ui.px(10), ui.px(8)))

        card = tk.Frame(outer, bg=ui.SURFACE, highlightthickness=1,
                        highlightbackground=ui.BORDER, highlightcolor=ui.BORDER)
        card.pack(fill="x")
        card.grid_columnconfigure(0, weight=1)

        self.input = tk.Text(
            card, height=2, wrap="word", font=ui.font(), bd=0, relief="flat",
            bg=ui.SURFACE, fg=ui.INK, insertbackground=ui.ACCENT,
            highlightthickness=0, padx=ui.px(10), pady=ui.px(9),
        )
        self.input.grid(row=0, column=0, sticky="ew", padx=(ui.px(4), 0), pady=ui.px(4))
        self.input.bind("<Return>", self._on_return)
        self.input.bind("<KeyRelease>", self._refresh_counter)

        self.send_button = ui.primary_button(card, "发送", self.send)
        self.send_button.grid(row=0, column=1, sticky="ns",
                              padx=(ui.px(6), ui.px(8)), pady=ui.px(8))

        self.counter = tk.Label(outer, text=ui.counter_text(""), font=ui.font(ui.SIZE_SMALL),
                                fg=ui.SUBTLE, bg=ui.BG, anchor="e")
        self.counter.pack(fill="x", pady=(ui.px(4), 0))

        # 占位提示：一个盖在输入框上的小标签，不碰 Text 的内容（否则长度计数会把它算进去）
        self._placeholder = tk.Label(self.input, text="说点什么…", font=ui.font(),
                                     fg=ui.SUBTLE, bg=ui.SURFACE, cursor="xterm")
        self._placeholder.bind("<Button-1>", self._focus_input)
        self.input.bind("<Button-1>", self._focus_input, add="+")
        for sequence in ("<<Paste>>", "<<Cut>>", "<<Clear>>"):
            self.input.bind(sequence, self._sync_soon, add="+")
        self._sync_placeholder()

        self.input.focus_set()

    # ---------------------------------------------------------------- 占位提示

    def _sync_placeholder(self) -> None:
        """空且在线时才显示提示；它只是一个浮在上面的标签，不参与文本内容。"""
        if self.connected and not self.current_text():
            self._placeholder.place(x=0, y=0)
        else:
            self._placeholder.place_forget()

    def _focus_input(self, _event: tk.Event | None = None) -> None:
        self.input.focus_set()

    def _sync_soon(self, _event: tk.Event | None = None) -> None:
        """粘贴/剪切之后再同步，别在事件处理中途把占位符盖上去。"""
        self.after_idle(self._sync_placeholder)

    # ---------------------------------------------------------------- 消息

    def append_message(self, sender: str, text: str) -> None:
        """群聊消息：[昵称]：内容，按发送者着色；自己的靠右。"""
        tags = [f"c_{self.color_of(sender)}"]
        if sender == getattr(self.app, "nickname", None):
            tags.append("mine")
        ui.append_line(self.messages, f"[{sender}]：{text}\n", tuple(tags))

    def append_system(self, text: str) -> None:
        ui.append_line(self.messages, f"{text}\n", ("system",))

    def append_error(self, text: str) -> None:
        ui.append_line(self.messages, f"{text}\n", ("error",))

    def color_of(self, nickname: str) -> str:
        color = self.users.get(nickname, {}).get("color", "black")
        return color if color in ui.COLOR_HEX else "black"

    # ---------------------------------------------------------------- 名单

    def set_users(self, users: list[dict]) -> None:
        self.users = {user["nickname"]: dict(user) for user in users if user.get("nickname")}
        self._redraw_users()

    def add_user(self, payload: dict) -> None:
        nickname = payload.get("nickname")
        if nickname:
            self.users[nickname] = dict(payload)
            self._redraw_users()

    def remove_user(self, nickname: str) -> None:
        if nickname in self.users:
            del self.users[nickname]
            self._redraw_users()
        self.notify_offline(nickname)

    def update_color(self, nickname: str, color: str) -> None:
        if nickname in self.users and color in ui.COLOR_HEX:
            self.users[nickname]["color"] = color
            self._redraw_users()

    def _redraw_users(self) -> None:
        self.user_list.delete(0, "end")
        for nickname in self.users:
            self.user_list.insert("end", nickname)
            self.user_list.itemconfig(
                "end", foreground=ui.COLOR_HEX.get(self.color_of(nickname), ui.INK)
            )
        self.count_label.configure(text=f"在线 {len(self.users)} 人")

    def is_online(self, nickname: str) -> bool:
        return nickname in self.users

    # ---------------------------------------------------------------- 私聊

    def _on_user_double_click(self, _event: tk.Event) -> None:
        selection = self.user_list.curselection()
        if not selection:
            return
        peer = self.user_list.get(selection[0])
        if peer != self.app.nickname:
            self.open_private(peer)

    def open_private(self, peer: str) -> PrivateWindow:
        window = self.private_windows.get(peer)
        if window is not None and window.winfo_exists():
            window.lift()
            window.focus_set()
            return window
        window = PrivateWindow(self, self.app, peer)
        window.append_system(f"开始和 {peer} 私聊")
        self.private_windows[peer] = window
        return window

    def close_private(self, peer: str) -> None:
        self.private_windows.pop(peer, None)

    def route_private(self, payload: dict) -> None:
        """私聊只进 Toplevel，绝不进主消息区（T11）。"""
        me = self.app.nickname
        peer = payload.get("to") if payload.get("from") == me else payload.get("from")
        if not peer:
            return
        window = self.private_windows.get(peer)
        if window is None or not window.winfo_exists():
            window = self.open_private(peer)
        window.append_message(payload["from"], payload["text"], self.color_of(payload["from"]))

    def notify_offline(self, nickname: str) -> None:
        window = self.private_windows.get(nickname)
        if window is not None and window.winfo_exists():
            window.append_system(f"{nickname} 已离线")

    def open_settings(self) -> None:
        if self.settings is not None and self.settings.winfo_exists():
            self.settings.lift()
            return
        self.settings = SettingsDialog(self, self.app)

    # ---------------------------------------------------------------- 输入

    def _on_return(self, event: tk.Event) -> str | None:
        if event.state & 0x0001:              # Shift+Enter 换行
            return None
        self.send()
        return "break"

    def _refresh_counter(self, _event: tk.Event | None = None) -> None:
        raw = self.current_text()
        self.counter.configure(
            text=ui.counter_text(raw),
            fg=ui.DANGER if ui.count_bytes(raw) > MAX_TEXT_BYTES else ui.SUBTLE,
        )
        self._sync_placeholder()

    def send(self) -> None:
        if not self.connected:
            return
        text = self.current_text().strip()
        if not text:
            return
        if ui.count_bytes(text) > MAX_TEXT_BYTES:
            self.append_error(
                f"这条有 {ui.count_bytes(text)} 字节，超过 {MAX_TEXT_BYTES} 字节上限，分段发吧"
            )
            return
        self.app.send_group(text)
        self.input.delete("1.0", "end")
        self._refresh_counter()
        self.input.focus_set()

    def current_text(self) -> str:
        return self.input.get("1.0", "end-1c")

    def message_text(self) -> str:
        """给自动化测试用：读出消息区全文。"""
        return self.messages.get("1.0", "end-1c")

    # ---------------------------------------------------------------- 断线

    def on_disconnected(self, reason_text: str) -> None:
        self.connected = False
        self.status_dot.configure(fg=ui.DANGER)
        self.status_text.configure(text="已断开", fg=ui.DANGER)
        self.banner_label.configure(text=f"连接断开了 · {reason_text}")
        self.banner.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.input.configure(state="disabled", bg=ui.PANEL_BG)
        self.send_button.configure(state="disabled", bg="#b9c7e2",
                                   disabledforeground="#f4f6fa")
        # 名单和人数是断线前的快照，压成灰色，别让它看起来还「在线」
        self.count_label.configure(fg=ui.SUBTLE, bg=ui.PANEL_BG)
        self.user_list.configure(state="disabled", disabledforeground=ui.SUBTLE)
        self.settings_button.configure(state="disabled")
        for window in self.private_windows.values():
            if window.winfo_exists():
                window.on_disconnected()
        self._sync_placeholder()
        self.append_error(f"连接断开了 · {reason_text}")


__all__ = ["ChatFrame"]
