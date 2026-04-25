@echo off
REM Daily premarket scanner launcher. Double-click to start.
REM Pulls latest code, activates venv, runs scan loop with Discord alerts.

cd /d "%~dp0\.."

echo Pulling latest changes...
git pull --ff-only

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

title Premarket Scanner - Ctrl+C to stop
echo.
echo Starting premarket scanner. Alerts will be sent to your Discord.
echo Close this window or press Ctrl+C to stop.
echo.

python -m src.cli scan --loop 60

pause
