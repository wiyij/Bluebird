"""一键启动：双击（或运行本文件）就打开聊天室。

做的事只有三件：

1. 8888 上没有服务端 → 在后台起一个（日志写 logs/server.log，带 --idle-exit）；
2. 等服务端能连上；
3. 打开登录窗。

关于「关掉窗口以后」：本文件**不杀服务端**，而是让服务端自己收工——
`--idle-exit 120` 表示最后一个客户端走了再等 2 分钟就退出。这样
「先开三个客户端窗口」和「关掉任意一个」都不会误伤别人，也不会在你
明天改完代码后留下一个跑着旧代码的进程占着 8888。

    py start_chat.py                      # 本机起服务端 + 打开登录窗
    py start_chat.py --no-server          # 只开客户端（连别人机器上的服务端）
    py start_chat.py --host 192.168.1.10  # 预填服务器地址

桌面上那个快捷方式指向的就是本文件（解释器用 pythonw.exe，所以不会闪黑框）。
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LAUNCH_LOG = LOG_DIR / "launcher.log"
SERVER_LOG = LOG_DIR / "server.log"

sys.path.insert(0, str(ROOT))
from client.session import is_local_host, load_last_host  # noqa: E402

#: 最后一个客户端离开后，服务端再等多久自退（秒）
IDLE_EXIT = 120.0


# ---------------------------------------------------------------- 日志 / 报错


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H:%M:%S")
    with LAUNCH_LOG.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"{stamp}  {message}\n")


def redirect_streams() -> None:
    """pythonw 下 sys.stdout / sys.stderr 是 None，任何 print 都得有地方去。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stream = LAUNCH_LOG.open("a", encoding="utf-8", errors="replace")
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def show_error(title: str, detail: str) -> None:
    """没有控制台时，错误只能靠弹窗告诉用户。"""
    log(f"ERROR {title}: {detail}")
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, detail)
        root.destroy()
    except Exception:                              # noqa: BLE001 - 弹窗失败也不能再炸
        pass


# ---------------------------------------------------------------- 服务端


def python_exe() -> str:
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    with socket.socket() as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def wait_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(port):
            return True
        time.sleep(0.2)
    return False


def ensure_server(port: int) -> bool:
    """有服务端就复用，没有就起一个。返回 False 表示起不来。"""
    if port_open(port):
        log(f"{port} 上已有服务端，直接复用")
        return True

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log(f"启动服务端：{port}")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    with SERVER_LOG.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 =====\n")
        subprocess.Popen(
            [python_exe(), "-m", "server.server",
             "--port", str(port), "--idle-exit", str(IDLE_EXIT)],
            cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
    if not wait_port(port):
        return False
    log("服务端已就绪")
    return True


# ---------------------------------------------------------------- 界面


def run_gui(host: str, port: int, name: str) -> int:
    sys.path.insert(0, str(ROOT))
    import tkinter as tk

    from client.gui import BluebirdApp

    root = tk.Tk()
    BluebirdApp(root, host=host, port=port, name=name)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    redirect_streams()
    parser = argparse.ArgumentParser(prog="start_chat", description="Bluebird 聊天室一键启动")
    parser.add_argument("--host", default=None,
                        help="登录窗里预填的服务器地址；不给就用上次连过的，再没有就 127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="默认 8888（或用上次的端口）")
    parser.add_argument("--name", default="", help="预填昵称")
    parser.add_argument("--no-server", action="store_true",
                        help="只开客户端；连别人机器上的服务端时用")
    args = parser.parse_args(argv)

    host, port, no_server = decide_target(args.host, args.port, args.no_server)
    log(f"===== 启动 host={host} port={port} no_server={no_server}")

    if not no_server and not ensure_server(port):
        show_error("Bluebird 聊天室",
                   f"服务端没能在 {port} 端口启动。\n\n"
                   f"可能是端口被别的程序占用，详见：\n{SERVER_LOG}")
        return 2

    try:
        return run_gui(host, port, args.name)
    except Exception:                              # noqa: BLE001
        detail = traceback.format_exc()
        show_error("Bluebird 聊天室", f"界面启动失败：\n{detail}")
        return 1


def decide_target(explicit_host: str | None, explicit_port: int | None,
                  no_server: bool) -> tuple[str, int, bool]:
    """决定这次连哪里、要不要在本机起服务端。

    规则就三条，按优先级：
    1. 命令行给了 --host → 听命令行的；
    2. 没给 → 看上次成功登录过的地址：是本机就照旧起服务端，是别人的 IP 就只当客户端
       （**这一步是「同一局域网进不了同一个房间」的根因**：以前每台机器都默认起一个
       本地服务端并连 127.0.0.1，于是两台机器各在各的空房间里）；
    3. 都没有 → 127.0.0.1 + 起服务端（单机演示）。
    """
    if explicit_host:
        return explicit_host, explicit_port or 8888, no_server

    last = load_last_host()
    if last is not None and not is_local_host(last[0]):
        log(f"上次连的是 {last[0]}:{last[1]}（不是本机），这次只开客户端")
        return last[0], explicit_port or last[1], True

    if last is not None:
        return "127.0.0.1", explicit_port or last[1], no_server
    return "127.0.0.1", explicit_port or 8888, no_server


if __name__ == "__main__":
    raise SystemExit(main())
