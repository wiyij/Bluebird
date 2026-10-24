"""生成「Bluebird 聊天室」快捷方式（.lnk），顺手放一份到桌面。

    py tools/make_shortcut.py                # 生成到仓库根 + 桌面
    py tools/make_shortcut.py --no-desktop   # 只在仓库根生成

为什么用 pythonw.exe：双击时不会先闪一个黑框；启动器的错误会弹消息框，
日志写到 logs/launcher.log。

.lnk 里存的是绝对路径，所以换目录后要重新生成一次；
`.lnk` 已加入 .gitignore——快捷方式是本机产物，不该进版本库。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Bluebird 聊天室"


def pythonw() -> Path:
    candidate = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.exists():
        return candidate
    return Path(sys.executable).with_name("pythonw.exe")


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def make_shortcut(target_lnk: Path) -> None:
    script = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut({_ps_quote(str(target_lnk))})
$s.TargetPath = {_ps_quote(str(pythonw()))}
$s.Arguments = '"' + {_ps_quote(str(ROOT / 'start_chat.py'))} + '"'
$s.WorkingDirectory = {_ps_quote(str(ROOT))}
$s.IconLocation = {_ps_quote(str(ROOT / 'assets' / 'bluebird.ico'))}
$s.Description = 'Bluebird 局域网聊天室'
$s.WindowStyle = 1
$s.Save()
"""
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "PowerShell 创建快捷方式失败")


def verify(lnk: Path) -> dict[str, str]:
    script = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut({_ps_quote(str(lnk))})
"TargetPath=$($s.TargetPath)"
"Arguments=$($s.Arguments)"
"WorkingDirectory=$($s.WorkingDirectory)"
"IconLocation=$($s.IconLocation)"
"""
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True)
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def main() -> int:
    parser = argparse.ArgumentParser(prog="make_shortcut")
    parser.add_argument("--no-desktop", action="store_true")
    args = parser.parse_args()

    targets = [ROOT / f"{NAME}.lnk"]
    desktop = Path.home() / "Desktop"
    if not args.no_desktop and desktop.exists():
        targets.append(desktop / f"{NAME}.lnk")

    for lnk in targets:
        lnk.parent.mkdir(parents=True, exist_ok=True)
        make_shortcut(lnk)
        info = verify(lnk)
        print(f"[OK] {lnk}")
        for key in ("TargetPath", "Arguments", "WorkingDirectory", "IconLocation"):
            print(f"     {key:16s} {info.get(key, '（空）')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
