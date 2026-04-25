@echo off
REM One-time Windows setup. Double-click to run.
REM Creates the venv, installs deps, and copies .env.example to .env.

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python is not on PATH.
    echo Install from https://www.python.org/downloads/ and tick "Add Python to PATH".
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create venv.
        pause
        exit /b 1
    )
)

echo Installing dependencies (this can take 1-2 minutes)...
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency install failed.
    pause
    exit /b 1
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo Copied .env.example to .env
    echo Opening .env in Notepad - fill in FINNHUB_API_KEY and DISCORD_WEBHOOK_URL.
    notepad ".env"
)

echo.
echo Setup complete. You can now double-click start-scanner.bat each morning.
pause
