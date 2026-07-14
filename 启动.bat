@echo off
REM 无黑色终端窗口启动（使用 pythonw）
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m grok_account_manager
) else (
  start "" pythonw -m grok_account_manager
)
