@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 双击本文件 = 在当前机器上生成「Bluebird 聊天室」快捷方式（仓库根 + 桌面）。
rem 快捷方式里存的是绝对路径，所以每台机器、每次挪动目录都要重新生成一次。

echo 正在生成快捷方式……
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "tools\make_shortcut.py"
    goto :done
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "tools\make_shortcut.py"
    goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "tools\make_shortcut.py"
    goto :done
)

echo [Bluebird] 没找到 Python，无法生成快捷方式。
echo 请先安装 Python 3.10 或更高版本。

:done
echo.
pause
