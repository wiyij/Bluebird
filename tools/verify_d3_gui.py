"""D3 GUI 自动化走查：不用鼠标，直接把真实窗口跑起来逐条验。

判据来自《结果示例》3.1—3.4 与第 5 章的 T06、T07、T09、T11、T12，外加 A24 的断线横幅。

    py -3.13 tools/verify_d3_gui.py            # 只跑判据
    py -3.13 tools/verify_d3_gui.py --shots    # 顺便把截图存到 docs/gui/

它开的是**真的 tkinter 窗口**（不是 mock），对面用真的 NetClient 当另一个用户，
所以「贴底滚动」「颜色 tag」「私聊不走主窗」这些都是在真控件上验的。

结果写 docs/test/D3-gui-evidence.txt，退出码 0 表示全过。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import queue
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tkinter as tk                                    # noqa: E402

from client import ui                                    # noqa: E402
from client.gui import BluebirdApp                       # noqa: E402
from client.net import NetClient                         # noqa: E402
from tools.procutil import (free_port, python_exe, terminate_tree,  # noqa: E402
                            wait_port)

OUT: list[str] = []
OK: list[str] = []
BAD: list[str] = []
SHOTS = False
SHOT_DIR = ROOT / "docs" / "gui"


def log(line: str = "") -> None:
    OUT.append(line)


def check(tag: str, ok: bool, detail: str = "") -> None:
    (OK if ok else BAD).append(tag)
    log(f"[{'PASS' if ok else 'FAIL'}] {tag}  {detail}")


# ---------------------------------------------------------------- 截图


def capture(widget: tk.Misc, name: str) -> None:
    """截这个窗口（含标题栏），用 System.Drawing + Win32 的窗口矩形，不引第三方库。

    用 ``GetWindowRect`` 而不是 Tk 的 ``winfo_rootx``：声明 DPI 感知之前，Tk 给的是
    逻辑像素、``CopyFromScreen`` 收的是物理像素，两者差 1.5 倍，截出来永远是歪的。
    """
    if not SHOTS:
        return
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    widget.update_idletasks()
    hwnd = widget.winfo_id()
    frame = ctypes.windll.user32.GetParent(hwnd) or hwnd
    rect = wintypes.RECT()
    # 优先用 DWM 的「可见边框」：GetWindowRect 把 Win10 的透明边距和阴影也算进来，
    # 截图边上就会带一圈桌面。
    try:
        got = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            frame, 9, ctypes.byref(rect), ctypes.sizeof(rect)   # DWMWA_EXTENDED_FRAME_BOUNDS
        )
    except (AttributeError, OSError):
        got = -1
    if got != 0:
        ctypes.windll.user32.GetWindowRect(frame, ctypes.byref(rect))
    x, y = rect.left, rect.top
    w, h = rect.right - rect.left, rect.bottom - rect.top
    target = (SHOT_DIR / name).as_posix()
    script = (
        "Add-Type -AssemblyName System.Drawing;"
        f"$b=New-Object System.Drawing.Bitmap {w},{h};"
        f"$g=[System.Drawing.Graphics]::FromImage($b);"
        f"$g.CopyFromScreen({x},{y},0,0,$b.Size);"
        f"$b.Save('{target}');"
        "$g.Dispose();$b.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script],
                   capture_output=True, check=False)
    log(f"   截图 → {Path(target).relative_to(ROOT)}")


# ---------------------------------------------------------------- 驱动


def pump(root: tk.Tk, seconds: float = 2.0, until=None) -> bool:
    """跑 tkinter 的事件循环 N 秒（不用 mainloop，这样脚本还能往下走）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else True


class Peer:
    """另一个「用户」：无界面，只有 NetClient，用来当对手戏。"""

    def __init__(self, port: int, name: str) -> None:
        self.name = name
        self.events: queue.Queue = queue.Queue()
        self.seen: list[tuple[str, dict]] = []
        self.net = NetClient("127.0.0.1", port, name, on_event=self.events.put)

    def start(self) -> None:
        self.net.start()

    def drain(self, seconds: float = 1.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                item = self.events.get(timeout=0.05)
            except queue.Empty:
                continue
            self.seen.append(item)

    def wait(self, kind: str, seconds: float = 3.0) -> dict | None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                item = self.events.get(timeout=0.05)
            except queue.Empty:
                continue
            self.seen.append(item)
            if item[0] == kind:
                return item[1]
        return None

    def has(self, kind: str) -> bool:
        return any(k == kind for k, _ in self.seen)

    def stop(self) -> None:
        self.net.stop()


# ---------------------------------------------------------------- 主流程


def main() -> int:
    global SHOTS
    parser = argparse.ArgumentParser(prog="verify_d3_gui")
    parser.add_argument("--port", type=int, default=0, help="0 = 向系统要一个空闲端口")
    parser.add_argument("--shots", action="store_true", help="顺便把截图存到 docs/gui/")
    args = parser.parse_args()
    SHOTS = args.shots
    if not args.port:
        args.port = free_port()

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    server = subprocess.Popen([python_exe(), "-m", "server.server", "--port", str(args.port)],
                              cwd=ROOT, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=env)
    if not wait_port(args.port):
        print("服务端没起来")
        terminate_tree(server)
        return 2

    root = tk.Tk()
    app = BluebirdApp(root, host="127.0.0.1", port=args.port)
    seen: list[tuple[str, float, str]] = []
    _original_handle = app._handle

    def _spy(kind: str, payload: dict) -> None:
        seen.append((kind, time.monotonic(),
                     str(payload.get("text") or payload.get("nickname") or "")))
        _original_handle(kind, payload)

    app._handle = _spy                      # 记录事件流，失败时好定位
    pump(root, 0.3)
    log("===== 登录窗 =====")
    capture(root, "D3-login.png")
    check("启动即出现登录窗（不是先连再问）", app.login is not None and app.chat is None)

    app.login.name_var.set("alice")
    app.login.submit()
    ok = pump(root, 6, until=lambda: app.chat is not None)
    check("输入昵称后进入主窗", ok and app.chat is not None)
    if app.chat is None:
        log("登录没成功，后面跳过")
        return finish(root, app, server)
    check("顶栏显示服务器地址", app.chat.server_text == f"127.0.0.1:{args.port}",
          app.chat.server_text)
    check("在线人数从 0 变 1", app.chat.count_label.cget("text") == "在线 1 人",
          app.chat.count_label.cget("text"))
    app.chat.input.insert("1.0", "大家好，我是 alice")
    app.chat._refresh_counter()
    check("字数计数器按 UTF-8 字节算",
          app.chat.counter.cget("text") == ui.counter_text("大家好，我是 alice"),
          app.chat.counter.cget("text"))

    # ---------------- 第二个用户上线 ----------------
    bob = Peer(args.port, "bob")
    bob.start()
    login = bob.wait("login_ok", seconds=5)
    check("第二个用户自己登录成功（后面几条的前置）", login is not None, str(login))
    listed = pump(root, 5, until=lambda: app.chat.is_online("bob"))
    rendered_at = time.monotonic()
    join_at = next((ts for kind, ts, _ in seen if kind == "user_join"), None)
    ui_delay = rendered_at - join_at if join_at else 99.0
    # 量的是「事件到手 → 列表已更新」这一段（GUI 的职责）；
    # 服务端到客户端的那一跳由 tools/verify_d3.py 单独压，混在一起会互相甩锅。
    check("有人上线，在线列表 1 s 内更新（T06 的前置）", listed and ui_delay <= 1.0,
          f"事件→渲染 {ui_delay:.2f} s，列表={list(app.chat.users)}")
    # 这条查的是「不重复渲染」，不是延迟：窗口放宽，免得被偶发的网络抖动带偏
    pump(root, 8, until=lambda: "bob 进入聊天室" in app.chat.message_text())
    again = app.chat.message_text().count("bob 进入聊天室")
    check("上线只出现一条系统提示（USER_JOIN 不重复渲染）", again == 1, f"出现 {again} 次")

    # ---------------- 群聊 ----------------
    app.chat.input.delete("1.0", "end")
    app.chat.input.insert("1.0", "你好 😀 世界")
    sent_at = time.monotonic()
    app.chat.send()
    got = bob.wait("message", seconds=5)
    delay = time.monotonic() - sent_at
    check("GUI 发出的消息到得了对方（含 emoji）",
          got is not None and got.get("text") == "你好 😀 世界",
          f"{(got or {}).get('text', '没收到')}（{delay:.2f} s）")
    check("发出后输入框清空", app.chat.current_text() == "", repr(app.chat.current_text()))
    pump(root, 5, until=lambda: "[alice：你好 😀 世界]" in app.chat.message_text())
    body = app.chat.message_text()
    check("自己的消息也回显，格式为 [昵称：内容]（全角冒号）",
          "[alice：你好 😀 世界]" in body,
          "消息区尾部：" + repr(body[-120:]) + " 事件流：" +
          repr([(k, n) for k, _, n in seen[-8:]]))

    bob.net.send_group("收到，这是 bob 的回复")
    pump(root, 2, until=lambda: "bob：收到，这是 bob 的回复" in app.chat.message_text())
    check("对方的消息出现在消息区", "bob：收到，这是 bob 的回复" in app.chat.message_text())
    check("消息按发送者着色（bob 用黑色 tag）",
          "c_black" in app.chat.messages.tag_names()
          and app.chat.messages.tag_cget("c_black", "foreground") == ui.COLOR_HEX["black"])

    # ---------------- 贴底滚动 ----------------
    app.chat.messages.see("end")
    root.update()
    for index in range(30):
        bob.net.send_group(f"第 {index:02d} 条")
        pump(root, 0.12)
    pump(root, 2, until=lambda: "第 29 条" in app.chat.message_text())
    at_bottom = app.chat.messages.yview()[1] >= 0.999
    check("T06 连发 30 条：最新一条贴在底部", at_bottom,
          f"yview={tuple(round(v, 3) for v in app.chat.messages.yview())}")

    app.chat.messages.yview_moveto(0.0)
    root.update()
    before = app.chat.messages.yview()[0]
    bob.net.send_group("这条到了但我正在看历史")
    pump(root, 3, until=lambda: "这条到了但我正在看历史" in app.chat.message_text())
    after = app.chat.messages.yview()[0]
    check("T07 上翻时不强制拉回底部", abs(after - before) < 0.05,
          f"{before:.3f} → {after:.3f}")
    check("T07 新消息照样收进来（只是不动视图）",
          "这条到了但我正在看历史" in app.chat.message_text())
    app.chat.messages.see("end")

    # ---------------- 颜色 ----------------
    app.chat.open_settings()
    pump(root, 0.4)
    capture(app.chat.settings, "D3-settings.png")
    app.chat.settings.choice.set("green")
    app.chat.settings._on_change()
    color_event = bob.wait("user_color")
    check("T09 改色广播到所有人", color_event == {"nickname": "alice", "color": "green"},
          str(color_event))
    pump(root, 1, until=lambda: app.chat.color_of("alice") == "green")
    check("T09 本地列表跟着变色", app.chat.color_of("alice") == "green")
    app.chat.settings.destroy()

    bob.net.set_color("blue")
    pump(root, 1.5, until=lambda: app.chat.color_of("bob") == "blue")
    check("T09 别人改色我这边的列表也变蓝", app.chat.color_of("bob") == "blue")

    capture(root, "D3-main.png")

    # ---------------- 私聊 ----------------
    app.chat.user_list.selection_clear(0, "end")
    for index in range(app.chat.user_list.size()):
        if app.chat.user_list.get(index) == "bob":
            app.chat.user_list.selection_set(index)
    app.chat._on_user_double_click(None)
    pump(root, 0.3)
    window = app.chat.private_windows.get("bob")
    check("T11 双击昵称开出独立私聊窗", window is not None and window.winfo_exists())
    check("T11 私聊窗标题明确", window is not None and window.title() == "与 bob 私聊",
          window.title() if window else "")

    window.entry.insert("1.0", "这句只有你能看到")
    window.send()
    private = bob.wait("message")
    check("T11 私聊投递到对方", private is not None and private.get("kind") == "private"
          and private.get("text") == "这句只有你能看到", str(private and private.get("text")))
    pump(root, 1)
    check("T11 主聊天室不出现私聊内容", "只有你能看到" not in app.chat.message_text())

    bob.net.send_private("alice", "收到，这条也只给你")
    pump(root, 2, until=lambda: "收到，这条也只给你" in window.messages.get("1.0", "end-1c"))
    check("T11 对方的私聊进小窗",
          "收到，这条也只给你" in window.messages.get("1.0", "end-1c"))
    capture(window, "D3-private.png")

    # ---------------- 离线私聊 ----------------
    bob.stop()
    pump(root, 3, until=lambda: not app.chat.is_online("bob"))
    check("T12 对方离线后列表里消失", not app.chat.is_online("bob"))
    check("T12 私聊窗提示已离线",
          "bob 已离线" in window.messages.get("1.0", "end-1c"))
    window.entry.insert("1.0", "你还在吗")
    window.send()
    pump(root, 0.5)
    check("T12 给离线的人发私聊：就地提示，不发出去",
          "不在线" in window.messages.get("1.0", "end-1c")
          and app.chat.current_text() == "")
    window.destroy()                     # 截图前关掉，免得挡住主窗
    pump(root, 0.3)

    # ---------------- 断线 ----------------
    terminate_tree(server)               # 整棵树杀，否则启动器下面的真服务端还在
    ok = pump(root, 8, until=lambda: not app.chat.connected)
    check("A24 服务端被杀后主窗进入断线状态", ok and not app.chat.connected)
    check("A24 横幅出现", app.chat.banner.winfo_ismapped())
    check("A24 状态圆点变红",
          app.chat.status_dot.cget("fg") == ui.DANGER, app.chat.status_dot.cget("fg"))
    check("A24 断线后输入框禁用", str(app.chat.input.cget("state")) == "disabled",
          str(app.chat.input.cget("state")))
    capture(root, "D3-disconnected.png")
    app.chat.reconnect_button.invoke()
    pump(root, 0.5)
    check("点「重新连接」回到登录窗（v2 不续会话）",
          app.chat is None and app.login is not None)
    check("回登录窗时昵称还预填着", app.login.name_var.get() == "alice",
          app.login.name_var.get())

    return finish(root, app, server)


def finish(root: tk.Tk, app: BluebirdApp, server: subprocess.Popen) -> int:
    log()
    log(f"合计 {len(OK) + len(BAD)} 项：PASS {len(OK)}，FAIL {len(BAD)}")
    if BAD:
        log("FAIL 项：" + "；".join(BAD))

    target = ROOT / "docs" / "test" / "D3-gui-evidence.txt"
    target.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"result: PASS {len(OK)} / FAIL {len(BAD)} -> {target.relative_to(ROOT)}")

    app._alive = False
    try:
        root.destroy()
    except tk.TclError:
        pass
    terminate_tree(server)
    return 0 if not BAD else 1


if __name__ == "__main__":
    raise SystemExit(main())
