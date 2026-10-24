"""D2 取证：TC-05「私聊隔离」的字节级证据。

判据原文（四天清单 D2 门禁、验收用例 TC-05）：**A 私聊 B，C 的报文计数不变**，
而且要求「抓包验证」。这个脚本给的就是抓包：

    服务端 8888 ← tap-alice 8889 ← alice
                ← tap-bob   8890 ← bob       三份日志，一人一份，互不串
                ← tap-carol 8891 ← carol

每个客户端连自己的 tap，所以每份日志就是**那一条连接上的全部字节**。
只要 carol 的日志里 CHAT_PRIVATE（0x11）一条都没有，同时她照常收到群聊，
「私聊没有发给她」就不再是靠程序里的计数器推断，而是看字节说话。

    py -3.13 tools/capture_d2.py
    py -3.13 tools/capture_d2.py --port 9000 --out-dir docs/pcap

产出：
    docs/pcap/D2-tap-alice.log / -bob.log / -carol.log   三条连接各自的帧日志
    docs/test/D2-capture.txt                             帧计数与判据
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ("alice", "bob", "carol")


def wait_port(port: int, host: str = "127.0.0.1", timeout: float = 10.0) -> bool:
    """等到端口能连上（或超时）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def start_client(name: str, port: int, python: str) -> subprocess.Popen:
    return subprocess.Popen(
        [python, "-m", "client.cli", "--host", "127.0.0.1", "--port", str(port),
         "--name", name],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        cwd=ROOT,
    )


def say(proc: subprocess.Popen, line: str) -> None:
    assert proc.stdin is not None
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def count(lines: list[str], needle: str) -> int:
    return sum(1 for line in lines if needle in line and not line.startswith("    "))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capture_d2", description="D2 私聊隔离抓包取证")
    parser.add_argument("--port", type=int, default=8888, help="服务端端口")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "docs/pcap")
    parser.add_argument("--summary", type=Path, default=ROOT / "docs/test/D2-capture.txt")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)

    out_dir = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    logs = {name: out_dir / f"D2-tap-{name}.log" for name in CLIENTS}
    for path in logs.values():
        if path.exists():
            path.unlink()

    procs: list[subprocess.Popen] = []
    try:
        server = subprocess.Popen(
            [args.python, "-m", "server.server", "--port", str(args.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
        )
        procs.append(server)
        if not wait_port(args.port):
            print(f"服务端没起来：{args.port}")
            return 2

        tap_ports: dict[str, int] = {}
        for index, name in enumerate(CLIENTS):
            tap_port = args.port + 1 + index
            tap_ports[name] = tap_port
            procs.append(subprocess.Popen(
                [args.python, "-m", "tools.tap",
                 "--listen-port", str(tap_port), "--target-port", str(args.port),
                 "--log", str(logs[name])],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
            ))
            if not wait_port(tap_port):
                print(f"tap 没起来：{tap_port}")
                return 2

        clients = {name: start_client(name, tap_ports[name], args.python) for name in CLIENTS}
        procs.extend(clients.values())
        time.sleep(2.5)

        say(clients["alice"], "三端都在吗")                 # 群聊
        time.sleep(0.8)
        say(clients["bob"], "在")                           # 群聊
        time.sleep(0.8)
        say(clients["alice"], "/w bob 这条只给 bob 看")      # 私聊
        time.sleep(0.8)
        say(clients["carol"], "我也在，我什么都没收到")       # 证明 carol 的字节确实在抓
        time.sleep(0.8)

        outputs: dict[str, str] = {}
        for name, proc in clients.items():
            say(proc, "/quit")
        time.sleep(1.0)
        for name, proc in clients.items():
            try:
                out, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
            outputs[name] = out or ""
    finally:
        for proc in reversed(procs):
            if proc.poll() is None:
                proc.terminate()
        time.sleep(0.3)
        for proc in reversed(procs):
            if proc.poll() is None:
                proc.kill()

    text = {name: (path.read_text(encoding="utf-8", errors="replace")
                   if path.exists() else "") for name, path in logs.items()}
    lines = {name: body.splitlines() for name, body in text.items()}
    private = {name: count(ls, "CHAT_PRIVATE") for name, ls in lines.items()}
    group = {name: count(ls, "CHAT_GROUP") for name, ls in lines.items()}
    total = {name: len([ln for ln in ls if not ln.startswith("    ") and ln.strip()])
             for name, ls in lines.items()}

    checks = [
        ("三条连接都抓到了帧（抓包链路有效）", all(total[n] > 0 for n in CLIENTS),
         ", ".join(f"{n}={total[n]}" for n in CLIENTS)),
        ("alice 的私聊出得去：C→S 1 条 + S→C 1 条回显", private["alice"] == 2,
         f"CHAT_PRIVATE={private['alice']}"),
        ("bob 收到 1 条私聊", private["bob"] == 1, f"CHAT_PRIVATE={private['bob']}"),
        ("carol 的字节流里 0 条私聊（隔离成立）", private["carol"] == 0,
         f"CHAT_PRIVATE={private['carol']}"),
        ("carol 同时在正常收发群聊（不是没连上）", group["carol"] >= 4,
         f"CHAT_GROUP={group['carol']}（alice 发言 + bob 发言 + carol 自己的回显）"),
        ("carol 的日志里搜不到私聊正文", "只给 bob" not in text["carol"], "0 处命中"),
        ("三端都登录成功", all("已进入聊天室：" + n in outputs[n] for n in CLIENTS), ""),
    ]

    summary = [
        f"服务端 127.0.0.1:{args.port}；三个 tap 分别为 "
        + "、".join(f"{n}:{tap_ports[n]}" for n in CLIENTS),
        "",
        "===== 帧计数 =====",
        *[f"{n:6s} 总帧 {total[n]:3d}  CHAT_GROUP {group[n]:2d}  CHAT_PRIVATE {private[n]:2d}"
          for n in CLIENTS],
        "",
        "===== 判据 =====",
        *[f"[{'PASS' if ok else 'FAIL'}] {label}  {detail}" for label, ok, detail in checks],
        "",
        "===== carol 的字节流（全量，证明里面没有 0x11）=====",
        *[ln for ln in lines["carol"] if not ln.startswith("    ")],
    ]
    summary_path = args.summary if args.summary.is_absolute() else ROOT / args.summary
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

    failed = [label for label, ok, _ in checks if not ok]
    for label, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}  {detail}")
    print(f"result: {len(checks) - len(failed)}/{len(checks)} -> {summary_path.relative_to(ROOT)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
