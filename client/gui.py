"""tkinter 图形界面：登录窗 → 四区主窗 → 私聊窗 / 设置。

线程边界（A20 唯一必须 review 的一条）：

    NetClient 后台线程 ──on_event──► queue.Queue ──► root.after(50) ──► 主线程改控件

任何一行控件操作都不允许出现在网络线程里。tkinter 不是线程安全的，跨线程改控件
在 Windows 上是「随机崩」，而且崩的位置和真正的原因往往隔了很远。

断线之后按《结果示例》决定⑥处理：**不做静默重连**，横幅提示 + 「重新连接」按钮，
点了回到登录窗重新走一遍登录（v2 没有会话续期，也没有离线补发）。
"""

from __future__ import annotations

import ctypes
import queue
import re
import sys
import tkinter as tk
import traceback

from client import ui
from client.chatui import ChatFrame
from client.net import NetClient, reason_text
from shared.config import DEFAULT_MAX_FRAME, Config, build_parser
from shared.console import setup_console
from shared.protocol import COLORS, DEFAULT_COLOR

#: 界面轮询间隔（毫秒）。50 ms = 每秒 20 次，人手感觉不到延迟，又不至于空转。
POLL_MS = 50

#: 与 server.py 的 NICKNAME_RE 保持一致；在这里先拦一道，省一个来回
NICKNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{1,16}$")


def prepare_dpi(root: tk.Tk) -> float:
    """声明 DPI 感知，并按系统缩放把字体与像素常量放大。

    不声明会怎样：Windows 把整个窗口当位图拉伸，150% 屏上字是糊的，
    而且 Tk 的坐标是「逻辑像素」、截图是「物理像素」，两者差 1.5 倍——
    写自动化脚本截图时永远对不齐（这个坑真踩过）。
    """
    dpi = 96.0
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)      # 系统级 DPI 感知
            dpi = float(ctypes.windll.user32.GetDpiForSystem())
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
                dpi = float(ctypes.windll.user32.GetDpiForSystem())
            except (AttributeError, OSError):
                dpi = 96.0
    root.tk.call("tk", "scaling", dpi / 72.0)                   # 点字号按物理 DPI 换算
    return ui.init_scale(dpi)


class LoginFrame(tk.Frame):
    """登录窗：昵称 + 服务器 IP:端口 + 进入按钮 + 红字提示。"""

    def __init__(self, master: tk.Misc, app: "BluebirdApp") -> None:
        super().__init__(master, bg=ui.BG)
        self.app = app
        self.name_var = tk.StringVar(value=app.remember_name)
        self.host_var = tk.StringVar(value=app.host)
        self.port_var = tk.StringVar(value=str(app.port))
        self._build()

    def _build(self) -> None:
        wrap = tk.Frame(self, bg=ui.BG)
        wrap.pack(fill="both", expand=True, padx=ui.px(34), pady=ui.px(26))

        # ---- 品牌 ----
        brand = tk.Frame(wrap, bg=ui.BG)
        brand.pack(fill="x")
        image = ui.logo(self, 44)
        if image is not None:
            self._logo = image                      # 必须留引用，不然会被回收
            tk.Label(brand, image=image, bg=ui.BG).pack(side="left")
        titles = tk.Frame(brand, bg=ui.BG)
        titles.pack(side="left", padx=(ui.px(12), 0))
        tk.Label(titles, text="Bluebird", font=ui.font(ui.SIZE_TITLE, "bold"),
                 bg=ui.BG, fg=ui.INK, anchor="w").pack(anchor="w")
        tk.Label(titles, text="局域网聊天室", font=ui.font(ui.SIZE_SUBTLE),
                 bg=ui.BG, fg=ui.MUTED, anchor="w").pack(anchor="w")

        tk.Label(wrap, text="同一个局域网，同一个房间。", font=ui.font(ui.SIZE_SUBTLE),
                 bg=ui.BG, fg=ui.MUTED, anchor="w").pack(fill="x", pady=(ui.px(14), ui.px(16)))

        # ---- 表单卡片 ----
        card = tk.Frame(wrap, bg=ui.SURFACE, highlightthickness=1,
                        highlightbackground=ui.BORDER)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=ui.SURFACE)
        inner.pack(fill="x", padx=ui.px(20), pady=ui.px(18))

        tk.Label(inner, text="昵称", font=ui.font(ui.SIZE_SMALL), fg=ui.MUTED,
                 bg=ui.SURFACE, anchor="w").pack(fill="x")
        self._name_box = ui.field(inner, self.name_var)
        self._name_box.pack(fill="x", pady=(ui.px(6), ui.px(14)))
        self.name_entry = self._name_box.entry

        tk.Label(inner, text="服务器", font=ui.font(ui.SIZE_SMALL), fg=ui.MUTED,
                 bg=ui.SURFACE, anchor="w").pack(fill="x")
        server_row = tk.Frame(inner, bg=ui.SURFACE)
        server_row.pack(fill="x", pady=(ui.px(6), 0))
        server_row.grid_columnconfigure(0, weight=1)
        self._host_box = ui.field(server_row, self.host_var)
        self._host_box.grid(row=0, column=0, sticky="ew")
        tk.Label(server_row, text=":", bg=ui.SURFACE, fg=ui.SUBTLE,
                 font=ui.font()).grid(row=0, column=1, padx=ui.px(6))
        self._port_box = ui.field(server_row, self.port_var, width=6, justify="center")
        self._port_box.grid(row=0, column=2, sticky="e")

        # 错误区先占好位置，出现提示时按钮不会往下跳
        slot = tk.Frame(inner, bg=ui.SURFACE, height=ui.px(34))
        slot.pack(fill="x", pady=(ui.px(8), 0))
        slot.pack_propagate(False)
        self.error_label = tk.Label(slot, text="", fg=ui.DANGER, bg=ui.SURFACE,
                                    font=ui.font(ui.SIZE_SMALL), wraplength=ui.px(330),
                                    justify="left", anchor="w")
        self.error_label.pack(fill="both", expand=True)

        self.enter_button = ui.primary_button(inner, "进入聊天室", self.submit)
        self.enter_button.pack(fill="x")

        self.name_entry.focus_set()
        for widget in (self.name_entry, self._host_box.entry, self._port_box.entry):
            widget.bind("<Return>", lambda _e: self.submit())

    # ---------------------------------------------------------------- 提交

    def submit(self) -> None:
        name = self.name_var.get().strip()
        if not NICKNAME_RE.match(name):
            self.set_error("昵称 1—16 个字：中文、字母、数字或下划线")
            return
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            self.set_error("端口要填 1—65535 的数字")
            return
        if not 1 <= port <= 65535:
            self.set_error("端口要填 1—65535 的数字")
            return
        host = self.host_var.get().strip() or "127.0.0.1"
        self.set_error("")
        self.set_busy(True)
        self.app.begin_connect(name, host, port)

    def set_error(self, text: str) -> None:
        self.error_label.configure(text=text)

    def set_busy(self, busy: bool) -> None:
        self.enter_button.configure(state="disabled" if busy else "normal",
                                    text="正在进入…" if busy else "进入聊天室")
        for box in (self._name_box, self._host_box, self._port_box):
            box.entry.configure(state="disabled" if busy else "normal")
        if busy:
            self.error_label.configure(text="正在连接…", fg=ui.SUBTLE)
        else:
            self.error_label.configure(fg=ui.DANGER)


class BluebirdApp:
    """把窗口、网络层和事件泵粘在一起。"""

    def __init__(self, root: tk.Tk, *, host: str = "127.0.0.1", port: int = 8888,
                 name: str = "", color: str = DEFAULT_COLOR,
                 max_frame: int = DEFAULT_MAX_FRAME) -> None:
        self.root = root
        self.host = host
        self.port = port
        self.nickname = name
        self.color = color if color in COLORS else DEFAULT_COLOR
        self.remember_name = name
        self.max_frame = max_frame

        self.events: queue.Queue = queue.Queue()
        self.net: NetClient | None = None
        self.login: LoginFrame | None = None
        self.chat: ChatFrame | None = None
        self._generation = 0
        self._alive = True

        prepare_dpi(self.root)
        icon = ui.logo(self.root, 32)
        if icon is not None:
            self._icon = icon
            try:
                self.root.iconphoto(True, icon)
            except tk.TclError:
                pass
        self.root.title("Bluebird · 登录")
        self.root.configure(bg=ui.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._show_login()
        self.root.after(POLL_MS, self._poll)

    # ---------------------------------------------------------------- 窗口

    def _center(self, width: int, height: int) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _show_login(self) -> None:
        if self.chat is not None:
            self.chat.destroy()
            self.chat = None
        self.root.resizable(False, False)
        self._center(*ui.px_pair(ui.WINDOW_LOGIN))
        self.login = LoginFrame(self.root, self)
        self.login.pack(fill="both", expand=True)
        self.root.title("Bluebird · 登录")

    def _show_chat(self, server_text: str) -> None:
        if self.login is not None:
            self.login.destroy()
            self.login = None
        self.root.resizable(True, True)
        self.root.minsize(ui.px(760), ui.px(520))
        self._center(*ui.px_pair(ui.WINDOW_CHAT))
        self.chat = ChatFrame(self.root, self, server_text)
        self.chat.pack(fill="both", expand=True)
        self.chat.append_system(f"欢迎，{self.nickname}")
        self.root.title(f"Bluebird · {self.nickname}")

    def back_to_login(self) -> None:
        """断线后回登录窗：v2 不续会话，重新走一遍登录（决定⑥）。"""
        self._stop_net()
        self._show_login()
        if self.login is not None:
            self.login.set_error("刚才的连接断开了")

    def on_close(self) -> None:
        self._alive = False
        self._stop_net()
        self.root.destroy()

    # ---------------------------------------------------------------- 连接

    def begin_connect(self, name: str, host: str, port: int) -> None:
        self.nickname = name
        self.remember_name = name
        self.host = host
        self.port = port
        self._stop_net()
        self._generation += 1
        generation = self._generation
        self.net = NetClient(
            host, port, name, self.color,
            on_event=lambda event, g=generation: self.events.put((g, event[0], event[1])),
            max_frame=self.max_frame,
        )
        self.net.start()

    def _stop_net(self) -> None:
        if self.net is not None:
            self.net.stop()
            self.net = None

    # ---------------------------------------------------------------- 事件

    def _poll(self) -> None:
        if not self._alive:
            return
        while True:
            try:
                generation, kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation:
                continue                     # 上一轮连接的残留事件，丢掉
            try:
                self._handle(kind, payload)
            except Exception:                # 一个坏事件不能把事件泵弄死
                traceback.print_exc()
                if self.chat is not None:
                    self.chat.append_error("消息显示出了点问题，详见控制台")
        self.root.after(POLL_MS, self._poll)

    def _handle(self, kind: str, payload: dict) -> None:
        if kind == "login_ok":
            self.nickname = payload.get("nickname", self.nickname)
            self.color = payload.get("color", self.color)
            if self.chat is None:
                self._show_chat(f"{self.host}:{self.port}")
            return

        if kind == "login_fail":
            if self.login is not None:
                self.login.set_busy(False)
                self.login.set_error(payload.get("message", "登录失败"))
            self._stop_net()
            return

        if self.chat is None:
            return                            # 还没登录成功，界面操作先忽略

        if kind == "user_list":
            self.chat.set_users(payload.get("users", []))
        elif kind == "user_join":
            # 只更新名单；「某某进入聊天室」由服务端的 CHAT_SYSTEM 统一发，
            # 这里再拼一条就会每次上线都出现两条提示。
            self.chat.add_user(payload)
        elif kind == "user_leave":
            self.chat.remove_user(payload.get("nickname", ""))
        elif kind == "user_color":
            # 颜色只是每个人自己的显示偏好：静默更新名单，不在消息区刷提示
            self.chat.update_color(payload.get("nickname", ""), payload.get("color", "black"))
        elif kind == "system":
            self.chat.append_system(payload.get("text", ""))
        elif kind == "message":
            if payload.get("kind") == "private":
                self.chat.route_private(payload)
            else:
                self.chat.append_message(payload.get("from", "?"), payload.get("text", ""))
        elif kind == "error":
            self._handle_error(payload)
        elif kind == "disconnected":
            self.chat.on_disconnected(reason_text(payload.get("reason")))
            self._stop_net()

    def _handle_error(self, payload: dict) -> None:
        code = payload.get("code")
        text = payload.get("message") or "出了点问题"
        if code == 3001:
            # 私聊对象离线：提示落在私聊窗里更符合直觉
            for window in (self.chat.private_windows.values() if self.chat else []):
                if window.winfo_exists():
                    window.append_error(text)
                    return
        self.chat.append_error(text)

    # ---------------------------------------------------------------- 对外

    def send_group(self, text: str) -> None:
        if self.net is not None:
            self.net.send_group(text)

    def send_private(self, peer: str, text: str) -> None:
        if self.net is not None:
            self.net.send_private(peer, text)

    def set_color(self, color: str) -> None:
        self.color = color
        if self.net is not None:
            self.net.set_color(color)

    def is_online(self, nickname: str) -> bool:
        return self.chat is not None and self.chat.is_online(nickname)

    def close_private(self, peer: str) -> None:
        if self.chat is not None:
            self.chat.close_private(peer)


def main(argv: list[str] | None = None) -> int:
    setup_console()
    parser = build_parser("bluebird-gui", default_host="127.0.0.1")
    parser.add_argument("--name", default="", help="预填昵称（仍要在登录窗点进入）")
    parser.add_argument("--color", default=DEFAULT_COLOR, choices=COLORS)
    ns = parser.parse_args(argv)

    config = Config(host=ns.host, port=ns.port, max_frame=ns.max_frame, log_level=ns.log_level)
    root = tk.Tk()
    BluebirdApp(root, host=config.host, port=config.port, name=ns.name,
                color=ns.color, max_frame=config.max_frame)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
