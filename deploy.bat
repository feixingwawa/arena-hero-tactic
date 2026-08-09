@echo off
setlocal
cd /d "%~dp0"

REM One-click deploy for Windows (double-click or: deploy.bat)
where python >nul 2>nul
if errorlevel 1 (
  echo [deploy] ERROR: python not found in PATH. Install Python 3.11+ first.
  pause
  exit /b 1
)

python scripts\deploy.py %*
set ERR=%ERRORLEVEL%
echo.
if %ERR% neq 0 (
  echo [deploy] failed with exit code %ERR%
  pause
  exit /b %ERR%
)
echo [deploy] done. Open http://127.0.0.1:8765 if Dashboard was started.
pause
endlocal
