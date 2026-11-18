@echo off
chcp 65001 >nul
cd /d "%~dp0"
where uv >nul 2>nul
if %errorlevel%==0 (
    uv sync --extra desktop >nul
    uv run --extra desktop python desktop_app.py
) else (
    python desktop_app.py
)
pause
