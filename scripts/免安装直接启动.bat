@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0win_app.ps1"
if %errorlevel% neq 0 (
    echo.
    echo 运行出错，请查看上方提示信息。
    pause
)
