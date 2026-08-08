@echo off
title Simulated Hospital Information System (HIS)
echo ==========================================================
echo           Simulated Hospital Information System (HIS)
echo ==========================================================
echo.

cd %~dp0..
if not exist .venv (
    echo [ERROR] Virtual environment not found. Please run the main gateway first ^(run.bat^) to initialize it.
    pause
    exit /b 1
)

echo [INFO] Starting Mock HIS server...
echo [INFO] HIS will be available at http://127.0.0.1:8085
echo.
.venv\Scripts\python -m uvicorn hospital_his.server:app --reload --host 127.0.0.1 --port 8085
pause
