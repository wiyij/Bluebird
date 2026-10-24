"""起停子进程的小工具（验收脚本共用）。

**为什么需要这个文件**：Windows 上 ``.venv\\Scripts\\python.exe`` 是个启动器，
它会把真正的解释器当子进程再起一个。只调 ``Popen.terminate()`` 杀掉的是启动器，
**真正的服务端进程会留下来继续占着端口**。下一个脚本于是连到了上一个剧本的服务端上，
现象非常迷惑：日志里一切正常，但「杀掉服务端」之后客户端完全没反应。

所以 Windows 统一用 ``taskkill /T`` 杀整棵进程树。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def python_exe() -> str:
    """优先用项目自带的虚拟环境解释器。"""
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def free_port() -> int:
    """向系统要一个空闲端口，用完立刻释放（不占着）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port: int, host: str = "127.0.0.1", timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def terminate_tree(proc: subprocess.Popen | None, timeout: float = 5.0) -> None:
    """连同子进程一起杀掉。重复调用安全。"""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


__all__ = ["ROOT", "python_exe", "free_port", "wait_port", "terminate_tree"]
