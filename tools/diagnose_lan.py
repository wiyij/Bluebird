"""同一局域网却进不了同一个聊天室？在两个机器上各跑一遍这个脚本。

    py tools\\diagnose_lan.py                     # 只查本机能不能当服务端
    py tools\\diagnose_lan.py --peer 172.20.10.5  # 顺便测「能不能连到对方」

它会按「本机地址 → 端口监听 → 防火墙 → 本机自连 → 连对方」的顺序逐条判定，
每条都直接告诉你过没过，最后给一句话结论。判据不靠猜，全是实测。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.protocol import MsgType, encode_frame, read_frame      # noqa: E402

PORT_DEFAULT = 8888


# ---------------------------------------------------------------- 输出


def head(text: str) -> None:
    print(f"\n=== {text} ===")


def ok(text: str, detail: str = "") -> None:
    print(f"[PASS] {text}  {detail}".rstrip())


def bad(text: str, detail: str = "") -> None:
    print(f"[FAIL] {text}  {detail}".rstrip())


def info(text: str) -> None:
    print(f"       {text}")


# ---------------------------------------------------------------- 查询


def ps(command: str, timeout: float = 25.0) -> str:
    """跑一段 PowerShell 取文本；取不到就返回空串，不让诊断脚本自己崩。

    两个坑：① 中文网卡名在 GBK 控制台下会让 ``text=True`` 解码失败，
    所以统一拿 bytes 再按 UTF-8 解，并让 PowerShell 用 UTF-8 输出；
    ② 命令本身出错时 stdout 可能是 None。
    """
    wrapped = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + command
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", wrapped],
                             capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    if not out.stdout:
        return ""
    return out.stdout.decode("utf-8", "replace").strip()


def local_ipv4() -> list[tuple[str, str, str]]:
    """[(接口名, 地址, 网关)]，只列 Up 的网卡。"""
    raw = ps(
        "Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' -and $_.IPv4Address } | "
        "ForEach-Object { \"$($_.InterfaceAlias)|$($_.IPv4Address.IPAddress)|$($_.IPv4DefaultGateway.NextHop)\" }"
    )
    rows = []
    for line in raw.splitlines():
        parts = line.strip().split("|")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def egress_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("223.5.5.5", 53))
            return sock.getsockname()[0]
    except OSError:
        return ""


def listening_pid(port: int) -> int | None:
    raw = ps(f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
             f"Select-Object -First 1).OwningProcess")
    return int(raw) if raw.isdigit() else None


def process_path(pid: int) -> str:
    return ps(f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ExecutablePath")


def firewall_allows(path: str) -> tuple[bool, str]:
    """有没有一条「入站放行」的规则覆盖这个可执行文件。

    这条查询要逐条规则取 ApplicationFilter，本机 374 条规则跑了 40 多秒，
    所以超时给到 120 s——之前按 25 s 截断，会把「其实放行了」误报成「被拦截」。
    """
    raw = ps(
        "Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow | ForEach-Object { "
        "$r = $_; $app = ($r | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue).Program; "
        "if ($app) { \"$app|$($r.Profile)\" } }",
        timeout=120,
    )
    target = path.lower().replace("/", "\\")
    for line in raw.splitlines():
        app, _, profile = line.strip().partition("|")
        if app.lower().replace("/", "\\") == target:
            return True, profile
    return False, ""


def tcp_state(host: str, port: int, timeout: float = 3.0) -> tuple[str, float]:
    """返回 (结果, 耗时)。结果是 ok / refused / timeout / error。"""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "ok", time.monotonic() - start
    except ConnectionRefusedError:
        return "refused", time.monotonic() - start
    except (socket.timeout, TimeoutError):
        return "timeout", time.monotonic() - start
    except OSError:
        return "error", time.monotonic() - start


def full_login(host: str, port: int) -> tuple[bool, str]:
    """完整的「连上 → 发 LOGIN_REQ → 收 LOGIN_RESP」，比 ping 靠谱得多。"""

    async def run() -> tuple[bool, str]:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
        except (OSError, asyncio.TimeoutError) as exc:
            return False, f"连不上：{exc.__class__.__name__}"
        try:
            writer.write(encode_frame(MsgType.LOGIN_REQ, {
                "username": f"diag{int(time.time()) % 10000}", "client_ver": "diag",
            }))
            await writer.drain()
            msg_type, body = await asyncio.wait_for(read_frame(reader), 5)
            if msg_type == MsgType.LOGIN_RESP and body.get("code") == 0:
                return True, "登录成功"
            return False, f"回了 0x{msg_type:02X}：{body}"
        except (OSError, asyncio.TimeoutError) as exc:
            return False, f"握手失败：{exc.__class__.__name__}"
        finally:
            writer.close()

    return asyncio.run(run())


# ---------------------------------------------------------------- 主流程


def main() -> int:
    parser = argparse.ArgumentParser(prog="diagnose_lan")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT)
    parser.add_argument("--peer", default=None,
                        help="对方机器的 IP（可选）；填了会实测能不能连上它")
    args = parser.parse_args()

    failures: list[str] = []

    head("1　本机地址（对方要连的就是这里）")
    rows = local_ipv4()
    egress = egress_ip()
    if not rows:
        bad("取不到任何 IPv4 地址")
        failures.append("没有可用地址")
    for alias, addr, gateway in rows:
        mark = ""
        if addr == egress:
            mark = "  ← 用这个（有默认路由）"
        elif gateway:
            mark = "  ← 也能用（有网关）"
        elif addr.startswith(("192.168.", "10.")):
            mark = "  ← 虚拟网卡（VMware/VirtualBox），**别用**"
        info(f"{alias:10s} {addr:16s} 网关={gateway or '无'}{mark}")
    if egress:
        ok("本机对外的局域网地址", egress)

    head("2　服务端有没有在监听")
    pid = listening_pid(args.port)
    if pid:
        ok(f"{args.port} 端口在监听", f"pid={pid}")
        bind = ps(f"(Get-NetTCPConnection -LocalPort {args.port} -State Listen | "
                  f"Select-Object -First 1).LocalAddress")
        if bind == "0.0.0.0":
            ok("监听在 0.0.0.0（所有网卡都收）")
        elif bind:
            bad("只监听了某一个地址，别人连不进来", bind)
            info("用 `python -m server.server`（默认 0.0.0.0）重起一次")
            failures.append("监听地址不对")
    else:
        bad(f"{args.port} 端口没有程序在监听", "对方连过来会被拒绝")
        info("先双击「启动聊天室.bat」，或运行 py -3 -m server.server")
        failures.append("服务端没起")

    head("3　防火墙给不给这个进程放行")
    if pid:
        path = process_path(pid)
        info("正在查防火墙规则（这台机器要 40 秒左右）…")
        allowed, profile = firewall_allows(path)
        if allowed:
            ok("有入站放行规则", f"{path}（{profile}）")
        else:
            bad("没有覆盖这个程序的入站放行规则", path or "（取不到路径）")
            info("以管理员身份运行一次：")
            info(f'netsh advfirewall firewall add rule name="Bluebird" dir=in action=allow '
                 f'program="{path}" enable=yes profile=any')
            failures.append("防火墙可能拦截")
    else:
        info("（没有监听进程，跳过）")

    head(f"4　本机自连：127.0.0.1:{args.port} 与 {egress or '局域网地址'}")
    success, detail = full_login("127.0.0.1", args.port)
    (ok if success else bad)("回环地址能完整登录一次", detail)
    if egress:
        success, detail = full_login(egress, args.port)
        (ok if success else bad)(f"经局域网地址 {egress} 能完整登录一次", detail)
        if not success:
            failures.append("本机经局域网地址都连不上")

    if args.peer:
        head(f"5　连对方：{args.peer}:{args.port}")
        result, cost = tcp_state(args.peer, args.port)
        if result == "ok":
            ok("TCP 能连上", f"{cost * 1000:.0f} ms")
            success, detail = full_login(args.peer, args.port)
            (ok if success else bad)("完整登录一次", detail)
        elif result == "refused":
            bad("连接被拒绝（对方机器上没有服务端在监听）", f"{cost * 1000:.0f} ms")
            info("在对方机器上双击「启动聊天室.bat」，或先跑本脚本确认第 2 项")
            failures.append("对方没起服务端")
        elif result == "timeout":
            bad("连接超时（包到不了对方）", f">{cost:.1f} s")
            info("三种可能：① IP 填错了（对方有 3 个虚拟网卡地址，别拿 192.168.x 的）")
            info("　　　　　　② 路由器/热点开了「客户端隔离」（AP isolation）")
            info("　　　　　　③ 对方防火墙把入站丢了（对方跑本脚本看第 3 项）")
            failures.append("网络不通")
        else:
            bad("连接出错", f"耗时 {cost:.1f}s")

    head("结论")
    if failures:
        print("  × 有 " + str(len(failures)) + " 处问题：" + "、".join(failures))
        print("  → 按上面的 [FAIL] 逐条处理；两边都跑一遍本脚本，谁失败就是谁的问题。")
        return 1
    print("  √ 本机可以作为服务端，也可以作为客户端。")
    if not args.peer:
        print("  → 在**另一台机器**上运行：")
        print(f"     py -3 tools\\diagnose_lan.py --peer {egress or '<本机IP>'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
