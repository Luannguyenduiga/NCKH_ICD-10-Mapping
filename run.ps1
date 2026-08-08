Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "      Smart Medical Interoperability Gateway (SMIG)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path ".venv")) {
    Write-Host "[INFO] Chua co moi truong ao. Dang tao .venv..." -ForegroundColor Yellow
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Khong tao duoc moi truong ao. Kiem tra lai Python." -ForegroundColor Red
        Exit 1
    }
    Write-Host "[INFO] Dang cai dat thu vien..." -ForegroundColor Yellow
    & .venv\Scripts\python -m pip install --upgrade pip
    & .venv\Scripts\pip install -r requirements.txt
}

# EMR Cloud (HAPI FHIR) la thanh phan bat buoc cho chuc nang dong bo.
$fhirUp = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8090/fhir/metadata" -TimeoutSec 3 -UseBasicParsing
    $fhirUp = $r.StatusCode -eq 200
} catch { $fhirUp = $false }

if ($fhirUp) {
    Write-Host "[OK]   EMR Cloud (HAPI FHIR) dang chay tai http://127.0.0.1:8090" -ForegroundColor Green
} else {
    Write-Host "[WARN] Chua thay EMR Cloud tai http://127.0.0.1:8090" -ForegroundColor Yellow
    Write-Host "       Chuc nang 'Dong bo len EMR Cloud' se bao loi 503." -ForegroundColor Yellow
    Write-Host "       Mo mot cua so khac va chay: docker compose up -d" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[INFO] Khoi dong FastAPI Gateway va giao dien Dashboard..." -ForegroundColor Green
Write-Host "[INFO] Dia chi: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "[INFO] Lan chay dau tien can ~30 giay de nap mo hinh NLP." -ForegroundColor Green
Write-Host ""

& .venv\Scripts\python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
