@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 (
  echo Could not open the ConnectionPilot program folder.
  pause
  exit /b 1
)
where py >nul 2>nul
if not errorlevel 1 goto run_py
where python >nul 2>nul
if not errorlevel 1 goto run_python
echo Python 3.11 or newer is required.
echo Install it from https://www.python.org/downloads/windows/
echo During installation, select Add Python to PATH.
pause
exit /b 1
:run_py
py -3 bootstrap.py
if errorlevel 1 pause
exit /b
:run_python
python bootstrap.py
if errorlevel 1 pause
exit /b
