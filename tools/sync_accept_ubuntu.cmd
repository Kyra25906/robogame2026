@echo off
setlocal
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_accept_ubuntu.ps1" %*
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" (
  echo Sync or acceptance failed. Read the first ERROR above.
) else (
  echo Sync and acceptance passed.
)
pause
exit /b %EXIT_CODE%
