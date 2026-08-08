@echo off
title Smart Medical Interoperability Gateway
echo ==========================================================
echo       Smart Medical Interoperability Gateway (SMIG)
echo ==========================================================
echo.

if not exist .venv (
    echo [INFO] Chua co moi truong ao. Dang tao .venv...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Khong tao duoc moi truong ao. Kiem tra lai Python.
        pause
        exit /b %errorlevel%
    )
    echo [INFO] Dang cai dat thu vien...
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\pip install -r requirements.txt
)

echo.
echo [LUU Y] Chuc nang "Dong bo len EMR Cloud" can HAPI FHIR chay o cong 8090.
echo         Neu chua chay, mo mot cua so khac va go: docker compose up -d
echo.
echo [INFO] Khoi dong FastAPI Gateway va giao dien Dashboard...
echo [INFO] Dia chi: http://127.0.0.1:8000
echo [INFO] Lan chay dau tien can ~30 giay de nap mo hinh NLP.
echo.
.venv\Scripts\python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
pause
