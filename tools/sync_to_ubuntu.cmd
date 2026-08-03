@echo off
setlocal
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_to_ubuntu.ps1" %*
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" (
  echo Sync failed. Read the error above before closing this window.
) else (
  echo Sync succeeded.
)
pause
exit /b %EXIT_CODE%
