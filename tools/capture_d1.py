"""D1 取证：一条命令跑完「服务端 + tap 代理 + 两个客户端」并留下字节日志。

对应 docs/test/D1.md 第 2 节的手工四终端步骤，把它们自动化，方便重复取证：

    py -3.13 tools/capture_d1.py                     # 8888 服务端、8889 tap
    py -3.13 tools/capture_d1.py --port 9000 --log docs/pcap/D1-tap-v2.log

产出：
    docs/pcap/D1-tap-v2.log   两个方向逐帧的十六进制 + JSON 明文
    docs/test/D1-capture.txt  本次抓包的帧清单摘要（默认会覆盖这份文件，换场景请用 --summary）

注意：D2 起服务端会多发 CHAT_SYSTEM（上下线提示），现在重抓会比 D1 时的 18 帧多。
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capture_d1", description="D1 抓包取证（tap 代理）")
    parser.add_argument("--port", type=int, default=8888, help="服务端端口")
    parser.add_argument("--tap-port", type=int, default=None, help="tap 监听端口，默认服务端端口 +1")
    parser.add_argument("--log", type=Path, default=ROOT / "docs/pcap/D1-tap-v2.log")
    parser.add_argument("--summary", type=Path, default=ROOT / "docs/test/D1-capture.txt",
                        help="帧清单摘要写到哪里（换场景记得改，别覆盖 D1 的记录）")
    parser.add_argument("--python", default=sys.executable, help="用哪个解释器起子进程")
    args = parser.parse_args(argv)

    tap_port = args.tap_port or args.port + 1
    log_path = args.log if args.log.is_absolute() else ROOT / args.log
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()

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

        tap = subprocess.Popen(
            [args.python, "-m", "tools.tap",
             "--listen-port", str(tap_port), "--target-port", str(args.port),
             "--log", str(log_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
        )
        procs.append(tap)
        if not wait_port(tap_port):
            print(f"tap 没起来：{tap_port}")
            return 2

        alice = start_client("alice", tap_port, args.python)
        bob = start_client("bob", tap_port, args.python)
        procs.extend([alice, bob])
        time.sleep(2.5)

        say(alice, "大家好，我是 alice")
        time.sleep(1.0)
        say(bob, "收到 😀")
        time.sleep(1.0)
        say(alice, "/who")
        time.sleep(0.5)
        say(alice, "/w bob 悄悄话只给你")
        time.sleep(0.8)

        for proc in (alice, bob):
            say(proc, "/quit")
        time.sleep(1.0)
        outputs = {}
        for name, proc in (("alice", alice), ("bob", bob)):
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

    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    frames = [line for line in text.splitlines() if line and not line.startswith("    ")]
    groups = [line for line in frames if "CHAT_GROUP" in line]

    summary = [
        f"服务端 127.0.0.1:{args.port}　tap 127.0.0.1:{tap_port}",
        f"日志 {log_path.relative_to(ROOT)}：{len(frames)} 帧（其中 CHAT_GROUP {len(groups)} 帧）",
        "",
        "===== alice 终端 =====",
        outputs.get("alice", "").rstrip(),
        "",
        "===== bob 终端 =====",
        outputs.get("bob", "").rstrip(),
        "",
        "===== 帧清单 =====",
        *frames,
    ]
    out_path = args.summary if args.summary.is_absolute() else ROOT / args.summary
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

    print(f"frames={len(frames)} chat_group={len(groups)} log={log_path.relative_to(ROOT)}")
    print(f"summary -> {out_path.relative_to(ROOT)}")
    return 0 if frames else 1


if __name__ == "__main__":
    raise SystemExit(main())
