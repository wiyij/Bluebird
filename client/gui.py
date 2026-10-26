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
from client.session import save_last_host
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
        pad = {"padx": 28, "fill": "x"}

        tk.Label(self, text="Bluebird 局域网聊天室", font=ui.font(ui.SIZE_TITLE, "bold"),
                 bg=ui.BG, fg=ui.COLOR_HEX["black"], anchor="w").pack(pady=(24, 2), **pad)
        tk.Label(self, text="输入昵称进入聊天室；连别人的房间就把「服务器」改成对方 IP。",
                 font=ui.font(ui.SIZE_SMALL), fg=ui.MUTED, bg=ui.BG,
                 anchor="w").pack(**pad)

        form = tk.Frame(self, bg=ui.BG)
        form.pack(fill="x", padx=28, pady=(18, 0))
        form.grid_columnconfigure(1, weight=1)

        tk.Label(form, text="昵称", bg=ui.BG, font=ui.font()).grid(row=0, column=0, sticky="w")
        self.name_entry = tk.Entry(form, textvariable=self.name_var, font=ui.font(),
                                   relief="solid", borderwidth=1)
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=4)

        tk.Label(form, text="服务器", bg=ui.BG, font=ui.font()).grid(row=1, column=0, sticky="w")
        server_row = tk.Frame(form, bg=ui.BG)
        server_row.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=4)
        server_row.grid_columnconfigure(0, weight=1)
        tk.Entry(server_row, textvariable=self.host_var, font=ui.font(),
                 relief="solid", borderwidth=1).grid(row=0, column=0, sticky="ew")
        tk.Label(server_row, text=":", bg=ui.BG, font=ui.font()).grid(row=0, column=1, padx=4)
        tk.Entry(server_row, textvariable=self.port_var, font=ui.font(), width=6,
                 relief="solid", borderwidth=1).grid(row=0, column=2)

        self.error_label = tk.Label(self, text="", fg=ui.DANGER, bg=ui.BG,
                                    font=ui.font(ui.SIZE_SMALL), wraplength=360,
                                    justify="left", anchor="w")
        self.error_label.pack(fill="x", padx=28, pady=(12, 0))

        self.enter_button = tk.Button(self, text="进入聊天室", font=ui.font(ui.SIZE_BODY, "bold"),
                                      command=self.submit, height=1)
        self.enter_button.pack(fill="x", padx=28, pady=(6, 20))

        self.name_entry.focus_set()
        for widget in (self.name_entry,):
            widget.bind("<Return>", lambda _e: self.submit())

    # ---------------------------------------------------------------- 提交

    def submit(self) -> None:
        name = self.name_var.get().strip()
        if not NICKNAME_RE.match(name):
            self.set_error("昵称需 1—16 个字符，中英文、数字、下划线；不能有空格")
            return
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            self.set_error("端口必须是数字")
            return
        if not 1 <= port <= 65535:
            self.set_error("端口必须在 1—65535 之间")
            return
        host = self.host_var.get().strip() or "127.0.0.1"
        self.set_error("")
        self.set_busy(True)
        self.app.begin_connect(name, host, port)

    def set_error(self, text: str) -> None:
        self.error_label.configure(text=text)

    def set_busy(self, busy: bool) -> None:
        self.enter_button.configure(state="disabled" if busy else "normal",
                                    text="连接中…" if busy else "进入聊天室")
        if busy:
            self.error_label.configure(text="正在连接…", fg=ui.MUTED)
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

        self.root.title("Bluebird 局域网聊天室")
        self.root.configure(bg=ui.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        prepare_dpi(self.root)
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
        self.root.title("Bluebird 局域网聊天室 · 登录")

    def _show_chat(self, server_text: str) -> None:
        if self.login is not None:
            self.login.destroy()
            self.login = None
        self.root.resizable(True, True)
        self.root.minsize(ui.px(720), ui.px(480))
        self._center(*ui.px_pair(ui.WINDOW_CHAT))
        self.chat = ChatFrame(self.root, self, server_text)
        self.chat.pack(fill="both", expand=True)
        self.chat.append_system(f"你已以 {self.nickname} 的身份进入聊天室")
        self.root.title(f"Bluebird 局域网聊天室 · {self.nickname}")

    def back_to_login(self) -> None:
        """断线后回登录窗：v2 不续会话，重新走一遍登录（决定⑥）。"""
        self._stop_net()
        self._show_login()
        if self.login is not None:
            self.login.set_error("连接已断开，请重新登录")

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
                    self.chat.append_error("[界面异常] 处理服务端消息时出错，详见控制台")
        self.root.after(POLL_MS, self._poll)

    def _handle(self, kind: str, payload: dict) -> None:
        if kind == "login_ok":
            self.nickname = payload.get("nickname", self.nickname)
            self.color = payload.get("color", self.color)
            save_last_host(self.host, self.port)   # 下次双击默认还连这台
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
            self.chat.update_color(payload.get("nickname", ""), payload.get("color", "black"))
            # 自己改色时本地已经提示过一句（set_color），服务端这条广播只给别人看；
            # 都渲染的话界面上会出现「你把颜色改成了绿色」+「alice 的颜色改为 绿色」两条。
            if payload.get("nickname") != self.nickname:
                self.chat.append_system(
                    f"{payload.get('nickname')} 的颜色改为 "
                    f"{ui.COLOR_LABEL.get(payload.get('color'), payload.get('color'))}"
                )
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
        text = payload.get("message") or "操作失败"
        if code == 3001:
            # 私聊对象离线：提示落在私聊窗里更符合直觉
            for window in (self.chat.private_windows.values() if self.chat else []):
                if window.winfo_exists():
                    window.append_error(f"[错误 3001] {text}")
                    return
        self.chat.append_error(f"[错误 {code}] {text}")

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
        if self.chat is not None:
            self.chat.append_system(f"你把颜色改成了{ui.COLOR_LABEL.get(color, color)}")

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
