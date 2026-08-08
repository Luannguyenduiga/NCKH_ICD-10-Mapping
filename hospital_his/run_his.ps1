Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "          Simulated Hospital Information System (HIS)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

# Move to the root directory where the virtual environment .venv exists
Set-Location "$PSScriptRoot\.."

if (-not (Test-Path ".venv")) {
    Write-Host "[ERROR] Virtual environment not found. Please run the main gateway first (run.ps1) to initialize it." -ForegroundColor Red
    Exit 1
}

Write-Host "[INFO] Starting Mock HIS server..." -ForegroundColor Green
Write-Host "[INFO] HIS will be available at http://127.0.0.1:8085" -ForegroundColor Green
Write-Host ""

& .venv\Scripts\python -m uvicorn hospital_his.server:app --reload --host 127.0.0.1 --port 8085
