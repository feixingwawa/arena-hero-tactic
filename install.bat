@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM =============================================================================
REM Arena Hero Tactic — Windows 一键部署（双击或命令行）
REM
REM   install.bat
REM   install.bat --api-key YOUR_KEY
REM   install.bat --no-start
REM
REM 远程首次安装（CMD）：
REM   curl -fsSL -o install.py https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py
REM   py install.py
REM =============================================================================

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 install.py %*
  set ERR=%ERRORLEVEL%
  goto :after
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python install.py %*
  set ERR=%ERRORLEVEL%
  goto :after
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 install.py %*
  set ERR=%ERRORLEVEL%
  goto :after
)

echo [install] ERROR: 未找到 Python。请安装 Python 3.11+ 并勾选 "Add to PATH"。
echo           https://www.python.org/downloads/
pause
exit /b 1

:after
echo.
if not "%ERR%"=="0" (
  echo [install] failed with exit code %ERR%
  pause
  exit /b %ERR%
)
echo [install] done. Dashboard: http://127.0.0.1:8765/
pause
endlocal
