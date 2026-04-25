@echo off
REM Show per-setup expectancy stats. Double-click to run.

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m src.cli stats
echo.
echo Recent trades:
python -m src.cli trades --limit 20
pause
