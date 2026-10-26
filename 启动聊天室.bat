@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 双击本文件即可启动：有 .venv 就用它，没有就用系统的 Python。
rem 不需要先装依赖——Bluebird 只用标准库。

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "%~dp0start_chat.py"
    exit /b 0
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0start_chat.py"
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0start_chat.py"
    exit /b 0
)

echo [Bluebird] 没找到 Python。
echo 请先安装 Python 3.10 或更高版本，安装时勾选 "Add python.exe to PATH"。
echo 装好后再双击本文件即可。
echo.
pause
exit /b 1
