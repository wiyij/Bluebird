"""D1 验收（客户端层）：起两个真实 CLI 客户端，走「两个终端互发文本」这条判据。

前置：服务端已经在监听（默认 127.0.0.1:8888）。

    py -3.13 -m server.server                  # 终端 1
    py -3.13 tools/verify_d1_cli.py            # 终端 2，可带端口参数

输出写到 docs/test/D1-cli.txt；退出码 0 表示 7 项全过。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = sys.argv[1] if len(sys.argv) > 1 else "8888"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def start(name: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "client.cli", "--host", "127.0.0.1", "--port", PORT,
         "--name", name],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        env=ENV, cwd=ROOT,
    )


def say(proc: subprocess.Popen, line: str) -> None:
    assert proc.stdin is not None
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


alice = start("alice")
bob = start("bob")
time.sleep(2.5)                          # 等两边登录完成

say(alice, "大家好，我是 alice")
time.sleep(1.2)
say(bob, "收到 😀")
time.sleep(1.2)
say(alice, "/who")
time.sleep(0.8)
say(alice, "/w bob 悄悄话只给你")
time.sleep(1.0)

for proc in (alice, bob):
    say(proc, "/quit")
time.sleep(1.0)

outputs: dict[str, str] = {}
for name, proc in (("alice", alice), ("bob", bob)):
    try:
        out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    outputs[name] = out or ""

checks = [
    ("alice 登录成功", "已进入聊天室：alice" in outputs["alice"]),
    ("bob 登录成功", "已进入聊天室：bob" in outputs["bob"]),
    ("bob 终端打印了 alice 的文字", "[alice：大家好，我是 alice]" in outputs["bob"]),
    ("alice 终端打印了 bob 的文字", "[bob：收到 😀]" in outputs["alice"]),
    ("alice 看到在线 2 人", "在线 2 人" in outputs["alice"]),
    ("bob 收到私聊", "[私聊 alice→bob：悄悄话只给你]" in outputs["bob"]),
    ("quit 退出无异常栈", "Traceback" not in outputs["alice"] + outputs["bob"]),
]

lines = []
for name, out in outputs.items():
    lines.append(f"===== {name} 的终端输出 =====")
    lines.append(out.rstrip())
    lines.append("")
lines.append("===== 判据 =====")
for label, ok in checks:
    lines.append(f"[{'PASS' if ok else 'FAIL'}] {label}")
lines.append(f"合计 {len(checks)} 项：PASS {sum(ok for _, ok in checks)}，"
             f"FAIL {sum(not ok for _, ok in checks)}")

target = ROOT / "docs" / "test" / "D1-cli.txt"
target.write_text("\n".join(lines) + "\n", encoding="utf-8")

for index, (label, ok) in enumerate(checks, 1):
    print(f"check {index}: {'PASS' if ok else 'FAIL'}")
print(f"result: PASS {sum(ok for _, ok in checks)} / FAIL "
      f"{sum(not ok for _, ok in checks)} -> {target.relative_to(ROOT)}")
sys.exit(0 if all(ok for _, ok in checks) else 1)
