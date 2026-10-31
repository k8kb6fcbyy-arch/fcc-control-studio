@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo [1/3] Checking Python and creating isolated environment...
python --version
if errorlevel 1 (
  echo Python 3.11+ was not found. Please install Python and add it to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create Python virtual environment.
    pause
    exit /b 1
  )
)
echo [2/3] Installing FCC dependencies (network needed on first run)...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
  echo Installation failed. Please check network and dependencies.
  pause
  exit /b 1
)
echo [3/3] FCC local service starting...
echo AI Bootstrap: http://127.0.0.1:8099/start
echo First-time setup: http://127.0.0.1:8099/setup
echo Easy I/O: http://127.0.0.1:8099/easy
echo Advanced mode: http://127.0.0.1:8099/expert
echo Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m fcc.server --host 127.0.0.1 --port 8099 --db fcc.db
pause
