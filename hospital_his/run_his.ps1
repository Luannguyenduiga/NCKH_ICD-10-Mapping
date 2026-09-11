# Khoi dong HIS mo phong (Python).
#
# -FacilityCode la ma co so kham chua benh cua ban HIS nay. Hai cach dung:
#
#   1. Trien khai that: moi benh vien mot ban Gateway rieng. -FacilityCode PHAI
#      trung voi -FacilityCode cua ban Gateway ma HIS nay goi, neu khong Gateway
#      tra 403.
#   2. Thu nghiem cuc bo: MOT ban Gateway phuc vu nhieu benh vien (chay run.ps1
#      voi -AllowClientFacility). Luc do cac HIS deu tro vao cung mot -GatewayUrl
#      nhung -FacilityCode khac nhau, va chinh ma nay giu cho ho so khong lan.
param(
    [int]$Port = 8085,
    [string]$GatewayUrl = "http://127.0.0.1:8000",
    [string]$FacilityCode = "79001",
    [string]$FacilityName = "Benh vien mo phong Viettel",
    # Benh an cuc bo cua RIENG benh vien nay. Bo trong thi suy ra tu -FacilityCode,
    # nen chay hai ban HIS tren cung mot may la moi ban tu co tep rieng - khong
    # con canh hai "benh vien" nhin thay y nguyen danh sach benh nhan cua nhau.
    [string]$DbPath = ""
)

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

$env:SMIG_GATEWAY_URL = $GatewayUrl
$env:SMIG_FACILITY_CODE = $FacilityCode
$env:SMIG_FACILITY_NAME = $FacilityName
$env:SMIG_HIS_ALLOWED_ORIGINS = "http://127.0.0.1:$Port,http://localhost:$Port"

if ($DbPath) {
    $env:SMIG_HIS_DB = $DbPath
} elseif ($FacilityCode -ne "79001") {
    # Giu nguyen ten tep cu cho cau hinh mac dinh, de ban demo dang dung khong
    # bong dung mat het benh an sau khi cap nhat.
    $safe = ($FacilityCode -replace '[^A-Za-z0-9\-_]', '_')
    $env:SMIG_HIS_DB = Join-Path $PSScriptRoot "his_db_$safe.sqlite"
}

Write-Host "[INFO] Starting Mock HIS server..." -ForegroundColor Green
Write-Host "[INFO] HIS will be available at http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "[INFO] Co so kham chua benh: $FacilityCode" -ForegroundColor Green
Write-Host "[INFO] Gateway: $GatewayUrl" -ForegroundColor Green
if ($env:SMIG_HIS_DB) {
    Write-Host "[INFO] Benh an cuc bo: $($env:SMIG_HIS_DB)" -ForegroundColor Green
}
Write-Host ""

& .venv\Scripts\python -m uvicorn hospital_his.server:app --reload --host 127.0.0.1 --port $Port
