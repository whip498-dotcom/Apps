@echo off
REM Convenience launcher: opens scanner AND dashboard in separate windows.
REM Each runs independently — close either to stop just that one.

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

start "" "%~dp0start-scanner.bat"
timeout /t 4 /nobreak >nul
start "" "%~dp0dashboard.bat"

echo Launched scanner and dashboard in separate windows.
echo Close this prompt — the other two will keep running.
pause
