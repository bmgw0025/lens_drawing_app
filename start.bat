@echo off
cd /d "%~dp0"
venv\Scripts\python.exe webview_main.py
if errorlevel 1 (
    echo Exit code: %errorlevel%
    pause
)
