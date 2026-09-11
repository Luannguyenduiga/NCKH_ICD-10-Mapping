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
REM run.bat khong nhan tham so nen phai dat ma co so o day, va phai dat TRUNG
REM voi mac dinh cua run.ps1: hai trinh khoi dong ma ra hai ma khac nhau thi
REM HIS goi Gateway se an 403 vi khai mot co so ma Gateway khong phuc vu.
REM Doi ma co so: dung run.ps1 -FacilityCode <ma CSKCB 5 chu so>.
set SMIG_FACILITY_CODE=79001
set SMIG_ALLOW_DEMO_FACILITY=0
echo [INFO] Khoi dong FastAPI Gateway va giao dien Dashboard...
echo [INFO] Dia chi: http://127.0.0.1:8000
echo [INFO] Lan chay dau tien can ~30 giay de nap mo hinh NLP.
echo.
.venv\Scripts\python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
pause
