@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_schedule.ps1"
if errorlevel 1 (
    echo.
    echo Schedule setup failed. Review the error above.
    pause
    exit /b 1
)

echo.
echo Schedule setup complete.
pause
exit /b 0
