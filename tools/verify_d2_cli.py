"""D2 三端联调（真实 CLI）：登录 → 群聊 → 私聊 → 退出，逐条核对现象。

前置：服务端已在 127.0.0.1:8888（或传参指定）监听。

    py -3.13 -m server.server
    py -3.13 tools/verify_d2_cli.py 8888

输出写到 docs/test/D2-cli.txt；退出码 0 表示全部通过。
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
time.sleep(1.6)
bob = start("bob")
time.sleep(1.6)
carol = start("carol")
time.sleep(2.0)

say(alice, "三端都在吗")
time.sleep(1.2)
say(bob, "在")
time.sleep(1.0)
say(alice, "/w bob 这条只给 bob")
time.sleep(1.0)
say(carol, "/quit")
time.sleep(1.2)
say(alice, "/quit")
time.sleep(0.6)
say(bob, "/quit")
time.sleep(1.0)

outputs: dict[str, str] = {}
for name, proc in (("alice", alice), ("bob", bob), ("carol", carol)):
    try:
        out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    outputs[name] = out or ""

checks = [
    ("三端都登录成功",
     all("已进入聊天室：" + name in outputs[name] for name in ("alice", "bob", "carol"))),
    ("carol 的名单里有 3 人", "在线 3 人" in outputs["carol"]),
    ("alice 的群聊三端都收到",
     all("[alice：三端都在吗]" in outputs[n] for n in ("alice", "bob", "carol"))),
    ("顺序一致：bob 那条在 alice 那条之后",
     outputs["bob"].find("[bob：在]") > outputs["bob"].find("[alice：三端都在吗]")),
    ("私聊只到 bob（alice 回显 + bob 收到）",
     "[私聊 alice→bob：这条只给 bob]" in outputs["bob"]
     and "[私聊 alice→bob：这条只给 bob]" in outputs["alice"]),
    ("carol 完全看不到私聊", "这条只给 bob" not in outputs["carol"]),
    ("alice 收到 carol 退出的系统提示", "carol 退出了" in outputs["alice"]),
    ("bob 收到 carol 退出的系统提示", "carol 退出了" in outputs["bob"]),
    ("退出无异常栈", all("Traceback" not in out for out in outputs.values())),
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

target = ROOT / "docs" / "test" / "D2-cli.txt"
target.write_text("\n".join(lines) + "\n", encoding="utf-8")

for index, (label, ok) in enumerate(checks, 1):
    print(f"check {index}: {'PASS' if ok else 'FAIL'}")
print(f"result: PASS {sum(ok for _, ok in checks)} / FAIL "
      f"{sum(not ok for _, ok in checks)} -> {target.relative_to(ROOT)}")
sys.exit(0 if all(ok for _, ok in checks) else 1)
