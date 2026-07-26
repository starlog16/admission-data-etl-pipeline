@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PROJECT_ROOT=%~dp0.."
"%PROJECT_ROOT%\.venv\Scripts\python.exe" "%PROJECT_ROOT%\scripts\run_pipeline.py" %*
