@echo off
setlocal EnableExtensions
pushd "%~dp0"

echo Setting up tw_stock on Windows...
echo.

where.exe winget >nul 2>nul
if errorlevel 1 (
    echo ERROR: winget was not found. Install or update App Installer from Microsoft Store, then try again.
    goto :failed
)

where.exe git >nul 2>nul
if errorlevel 1 (
    echo Installing Git...
    winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto :failed
)

call :find_python
if not defined PYTHON (
    echo Installing Python 3.12...
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto :failed
    call :find_python
)

if not defined PYTHON (
    echo ERROR: Python 3.12 was installed but could not be located.
    echo Restart Windows, then run this file again. If the problem continues, install Python 3.12 from python.org.
    goto :failed
)

echo Using Python: "%PYTHON%"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" --version 2>nul | findstr /b /c:"Python 3.12" >nul
    if errorlevel 1 (
        echo ERROR: The existing .venv does not use Python 3.12.
        echo Rename or remove .venv manually, then run this file again.
        goto :failed
    )
) else (
    "%PYTHON%" -m venv .venv
    if errorlevel 1 goto :failed
)

echo Installing Python packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

if not exist ".env" if exist ".env.example" (
    copy ".env.example" ".env" >nul
    if errorlevel 1 goto :failed
)

echo.
echo Setup complete.
echo Start the desktop app with:
echo   .venv\Scripts\python.exe desktop\main.py
echo Configure .env before using features that require API credentials.
popd
pause
exit /b 0

:find_python
set "PYTHON="
where.exe py >nul 2>nul
if not errorlevel 1 (
    for /f "usebackq delims=" %%I in (`py -3.12 -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON=%%I"
)

if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYTHON if exist "%ProgramFiles%\Python312\python.exe" set "PYTHON=%ProgramFiles%\Python312\python.exe"
if not defined PYTHON if exist "%ProgramFiles%\Python 3.12\python.exe" set "PYTHON=%ProgramFiles%\Python 3.12\python.exe"

if defined PYTHON (
    "%PYTHON%" --version 2>nul | findstr /b /c:"Python 3.12" >nul
    if errorlevel 1 set "PYTHON="
)
exit /b 0

:failed
echo.
echo Setup did not complete. Review the error above and try again.
popd
pause
exit /b 1
