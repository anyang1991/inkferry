@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
where py.exe >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
  if not errorlevel 1 (
    py -3 server.py %*
    goto finish
  )
)
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
python server.py %*
:finish
set "PAPERFERRY_EXIT=%errorlevel%"
if not "%PAPERFERRY_EXIT%"=="0" pause
exit /b %PAPERFERRY_EXIT%
