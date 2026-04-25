@echo off
REM Show past backtest results from data_cache/backtest_history.jsonl

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m src.cli backtest-results --history --trades
pause
