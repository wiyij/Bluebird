"""记住「上次连的是哪台服务器」。

为什么需要：一台机器上双击快捷方式时，启动器会**本机起一个服务端**，登录窗预填
`127.0.0.1`。两台机器各自双击，就各自进了自己那个空房间——看起来就是「同一个局域网
却进不了同一个聊天室」。

所以这里记一笔上次成功登录的地址：
- 上次连的是本机 → 照旧起服务端；
- 上次连的是别人的 IP → 说明这台机器是客户端，这次**不再起本机服务端**，直接把地址预填好。

状态文件放在 `%LOCALAPPDATA%\\Bluebird\\last_host.txt`（不放进仓库，也不依赖当前目录）。
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


def state_dir() -> Path:
    """每用户的状态目录；取不到 LOCALAPPDATA 时退回用户主目录。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".bluebird"
    return root / "Bluebird"


STATE_PATH = state_dir() / "last_host.txt"


def load_last_host() -> tuple[str, int] | None:
    """读上次的地址；文件不存在或格式不对都返回 None（不抛异常）。

    用 ``utf-8-sig`` 读：别的工具（比如 PowerShell 的 ``Set-Content -Encoding utf8``）
    写出来的文件带 BOM，按 utf-8 读会得到一个 ``\\ufeff`` 开头的地址，连过去必然失败。
    """
    try:
        text = STATE_PATH.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    if not text:
        return None
    host, _, port_text = text.partition(":")
    host = host.strip()
    if not host or not port_text.strip().isdigit():
        return None
    port = int(port_text.strip())
    return (host, port) if 1 <= port <= 65535 else None


def save_last_host(host: str, port: int) -> None:
    """记下这次成功登录的地址；写不了就算了，不能因为记不住地址让程序挂掉。"""
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(f"{host}:{port}", encoding="utf-8")
    except OSError as exc:                      # pragma: no cover - 只在只读盘上发生
        print(f"[提示] 记不住上次的地址：{exc}", file=sys.stderr)


def local_addresses() -> set[str]:
    """本机的几个「自己」：回环 + 网卡地址。"""
    addresses = {"127.0.0.1", "localhost", "::1"}
    try:
        addresses.add(socket.gethostbyname(socket.gethostname()))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("223.5.5.5", 53))     # 不会真的发包，只为让内核选源地址
            addresses.add(sock.getsockname()[0])
    except OSError:
        pass
    return addresses


def is_local_host(host: str) -> bool:
    return host in local_addresses()


__all__ = ["STATE_PATH", "load_last_host", "save_last_host", "local_addresses", "is_local_host"]
