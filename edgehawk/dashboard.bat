@echo off
REM Launch the standalone dashboard as a native desktop window (always-on-top).
REM Run start-scanner.bat in a separate window for live data to flow in.

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
title EdgeHawk Dashboard
python -m src.cli dashboard-app --always-on-top
