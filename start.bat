@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.12 or later is required. Install from python.org.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  python -m venv .venv
  if errorlevel 1 goto failed
)
if not exist .venv\ready.txt (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto failed
  echo ready>.venv\ready.txt
)
.venv\Scripts\python.exe app.py
pause
exit /b
:failed
echo Setup failed. Check the error above and your internet connection.
pause
exit /b 1
