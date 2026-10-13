@echo off
setlocal
cd /d "%~dp0"
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
  python server.py --open
  goto finished
)
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer was not found. Install Python and add it to PATH.
  pause
  exit /b 1
)
py -3 server.py --open
:finished
if errorlevel 1 (
  echo The editor could not start. Try a different port: python server.py --port 8767 --open
  pause
  exit /b 1
)
exit /b 0
