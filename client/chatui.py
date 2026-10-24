"""主窗口：顶栏 / 消息区 / 在线列表 / 输入区（《结果示例》3.1 的四区布局）。

四条硬规矩，写在这里免得以后被「顺手优化」掉：

1. **贴底滚动**：新消息到达时只在「本来就在底部」的情况下才 ``see("end")``，
   用户往上翻历史时绝不抢滚动条（T06、T07）。
2. **颜色按发送者取**：消息帧里不带颜色，客户端查 ``self.users[nickname]["color"]``。
   改色只影响之后的新消息，已渲染的历史不受影响——因为插入时就用了固定 tag。
3. **私聊不进主窗**：``route_private`` 只往 Toplevel 里塞（T11）。
4. **线程边界**：这里所有方法都必须在主线程调；网络事件由 ``BluebirdApp``
   从 ``queue.Queue`` 取出来再进来，没有任何一行控件操作发生在网络线程。
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
        self.grid_rowconfigure(2, weight=1)
        self._build_topbar()
        self._build_banner()
        self._build_messages()
        self._build_users()
        self._build_input()

    def _build_topbar(self) -> None:
        bar = tk.Frame(self, bg=ui.PANEL_BG, height=ui.px(ui.TOPBAR_HEIGHT))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.pack_propagate(False)

        tk.Label(bar, text="Bluebird 聊天室", font=ui.font(ui.SIZE_BRAND, "bold"),
                 bg=ui.PANEL_BG, fg=ui.COLOR_HEX["black"]).pack(side="left", padx=(12, 14))
        self.status_dot = tk.Label(bar, text="●", font=ui.font(13), fg=ui.OK, bg=ui.PANEL_BG)
        self.status_dot.pack(side="left")
        self.status_text = tk.Label(bar, text="已连接", font=ui.font(ui.SIZE_SMALL),
                                    fg=ui.MUTED, bg=ui.PANEL_BG)
        self.status_text.pack(side="left", padx=(3, 14))
        tk.Label(bar, text=f"服务器 {self.server_text}", font=ui.font(ui.SIZE_SMALL),
                 fg=ui.MUTED, bg=ui.PANEL_BG).pack(side="left")

        self.settings_button = tk.Button(bar, text="⚙ 设置", font=ui.font(ui.SIZE_SMALL),
                                         command=self.open_settings, relief="flat",
                                         bg=ui.PANEL_BG, activebackground=ui.BORDER)
        self.settings_button.pack(side="right", padx=(4, 10))
        self.count_label = tk.Label(bar, text="在线 0 人", font=ui.font(ui.SIZE_SMALL),
                                    fg=ui.MUTED, bg=ui.PANEL_BG)
        self.count_label.pack(side="right", padx=6)

    def _build_banner(self) -> None:
        """断线横幅：默认不显示，断线时才 grid 出来（A24）。"""
        self.banner = tk.Frame(self, bg=ui.BANNER_BG)
        self.banner_label = tk.Label(self.banner, text="连接已断开", bg=ui.BANNER_BG,
                                     fg=ui.BANNER_FG, font=ui.font(ui.SIZE_SMALL), anchor="w")
        self.banner_label.pack(side="left", padx=12, pady=6)
        self.reconnect_button = tk.Button(self.banner, text="重新连接",
                                          font=ui.font(ui.SIZE_SMALL),
                                          command=self.app.back_to_login,
                                          bg=ui.BANNER_BG, relief="solid", borderwidth=1)
        self.reconnect_button.pack(side="right", padx=10, pady=4)

    def _build_messages(self) -> None:
        wrap = tk.Frame(self, bg=ui.BG)
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.messages = ui.make_chat_text(wrap)
        self.messages.grid(row=0, column=0, sticky="nsew")
        scroll = tk.Scrollbar(wrap, command=self.messages.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.messages.configure(yscrollcommand=scroll.set)

    def _build_users(self) -> None:
        wrap = tk.Frame(self, bg=ui.PANEL_BG)
        wrap.grid(row=2, column=1, rowspan=2, sticky="nsew")
        tk.Label(wrap, text="在线用户", font=ui.font(ui.SIZE_SMALL, "bold"),
                 bg=ui.PANEL_BG, fg=ui.MUTED, anchor="w").pack(fill="x", padx=10, pady=(8, 2))

        self.user_list = tk.Listbox(
            wrap, font=ui.font(), activestyle="none", bg=ui.PANEL_BG,
            relief="flat", highlightthickness=0, selectbackground="#dbe7ff",
            selectforeground=ui.COLOR_HEX["black"],
        )
        self.user_list.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        self.user_list.bind("<Double-Button-1>", self._on_user_double_click)

        tk.Label(wrap, text="双击昵称开私聊", font=ui.font(ui.SIZE_SMALL),
                 bg=ui.PANEL_BG, fg=ui.MUTED).pack(fill="x", pady=(0, 6))

    def _build_input(self) -> None:
        wrap = tk.Frame(self, bg=ui.BG)
        wrap.grid(row=3, column=0, sticky="ew", padx=10, pady=(4, 8))
        wrap.grid_columnconfigure(0, weight=1)

        self.input = tk.Text(wrap, height=2, wrap="word", font=ui.font(),
                             relief="solid", borderwidth=1, highlightthickness=0)
        self.input.grid(row=0, column=0, sticky="ew")
        self.input.bind("<Return>", self._on_return)
        self.input.bind("<KeyRelease>", self._refresh_counter)

        self.send_button = tk.Button(wrap, text="发送", width=8, command=self.send)
        self.send_button.grid(row=0, column=1, sticky="ns", padx=(8, 0))

        self.counter = tk.Label(wrap, text=ui.counter_text(""), font=ui.font(ui.SIZE_SMALL),
                                fg=ui.MUTED, bg=ui.BG)
        self.counter.grid(row=1, column=0, sticky="e", pady=(2, 0))

        self.input.focus_set()

    # ---------------------------------------------------------------- 消息

    def append_message(self, sender: str, text: str) -> None:
        """群聊消息：[昵称：内容]，按发送者的颜色着色。"""
        ui.append_line(self.messages, f"[{sender}：{text}]\n", (f"c_{self.color_of(sender)}",))

    def append_system(self, text: str) -> None:
        ui.append_line(self.messages, f"[system：{text}]\n", ("system",))

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
                "end", foreground=ui.COLOR_HEX.get(self.color_of(nickname), ui.COLOR_HEX["black"])
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
        window.append_system(f"与 {peer} 的私聊已打开")
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
        raw = self.input.get("1.0", "end-1c")
        self.counter.configure(text=ui.counter_text(raw),
                               fg=ui.DANGER if ui.count_bytes(raw) > MAX_TEXT_BYTES else ui.MUTED)

    def send(self) -> None:
        if not self.connected:
            return
        text = self.input.get("1.0", "end-1c").strip()
        if not text:
            return
        if ui.count_bytes(text) > MAX_TEXT_BYTES:
            self.append_error(f"消息超过 {MAX_TEXT_BYTES} 字节，请分段发送（协议第 8 章）")
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
        self.status_text.configure(text="已断线", fg=ui.DANGER)
        self.banner_label.configure(text=f"连接已断开：{reason_text}")
        self.banner.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.input.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.append_error(f"[连接已断开] {reason_text}")


__all__ = ["ChatFrame"]
