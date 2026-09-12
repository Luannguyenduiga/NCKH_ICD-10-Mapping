# Khoi dong mot ban SMIG Gateway.
#
# Moi benh vien chay MOT ban rieng voi -FacilityCode khac nhau. Ma co so di vao
# `identifier.system` cua ma benh an, nen hai ban trung ma se tron ho so cua hai
# benh vien vao nhau (xem README muc 6.6).
param(
    [int]$Port = 8000,
    [string]$FacilityCode = "79001",
    [string]$FacilityName = "Benh vien mo phong Viettel",
    # Cho phep MOT ban Gateway phuc vu NHIEU benh vien: moi HIS tu khai ma co so
    # trong tung yeu cau. Chi dung khi thu nghiem cuc bo - mo hinh NLP chiem vai
    # GB RAM nen chay hai ban Gateway tren mot may la qua nang.
    #
    # KHONG bat khi trien khai that: ma co so la danh tinh cua ben ghi ho so, de
    # ben goi tu khai thi benh vien B khai minh la benh vien A duoc ngay.
    [switch]$AllowClientFacility
)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "      Smart Medical Interoperability Gateway (SMIG)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $PSScriptRoot

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
    $ProgressPreference = 'SilentlyContinue' #Close progress bar
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

$env:SMIG_FACILITY_CODE = $FacilityCode
$env:SMIG_FACILITY_NAME = $FacilityName
if ($AllowClientFacility) { $env:SMIG_ALLOW_CLIENT_FACILITY = "1" }
else { $env:SMIG_ALLOW_CLIENT_FACILITY = "0" }

# Ma CSKCB that do co quan BHXH cap, gom 5 chu so (vd 01001), va Gateway kiem dang
# no ngay luc khoi dong. Cac ma demo trong README deu bat dau bang "BV-" nen nhan
# ra duoc: gap ma demo thi noi long de con chay trinh dien; gap BAT KY ma nao
# khac thi giu che do chat, nen go nham mot ky tu cua ma that se lam Gateway dung
# han kem thong bao ro, thay vi chay tiep va ghi ra mot dong ban ghi khoa hong.
if ($FacilityCode -like "BV-*") { $env:SMIG_ALLOW_DEMO_FACILITY = "1" }
else { $env:SMIG_ALLOW_DEMO_FACILITY = "0" }
if ($env:SMIG_ALLOW_DEMO_FACILITY -eq "1") {
    Write-Host "[WARN] Ma co so '$FacilityCode' la ma tu dat, khong phai ma CSKCB do BHXH cap." -ForegroundColor Yellow
    Write-Host "       Du lieu ghi ra KHONG doi chieu duoc voi cong giam dinh BHYT." -ForegroundColor Yellow
}
# HIS goi Gateway tu cong khac nen phai nam trong danh sach CORS.
$env:SMIG_ALLOWED_ORIGINS = "http://127.0.0.1:$Port,http://localhost:$Port," +
    "http://127.0.0.1:8085,http://localhost:8085," +
    "http://127.0.0.1:8086,http://localhost:8086," +
    "http://127.0.0.1:3000,http://localhost:3000"

Write-Host ""
Write-Host "[INFO] Co so kham chua benh: $FacilityCode - $FacilityName" -ForegroundColor Green
if ($AllowClientFacility) {
    Write-Host "[WARN] Che do NHIEU CO SO: HIS tu khai ma co so trong tung yeu cau." -ForegroundColor Yellow
    Write-Host "       Chi dung de thu nghiem cuc bo, KHONG dung khi trien khai that." -ForegroundColor Yellow
}
Write-Host "[INFO] Dia chi: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "[INFO] Lan chay dau tien can ~30 giay de nap mo hinh NLP." -ForegroundColor Green
# Khoa API cho duong lien thong /api/fhir/*: Gateway tu bat khi kho khoa co it
# nhat mot khoa (backend/data/api_keys.json). Cap khoa cho tung HIS bang:
#   .venv\Scripts\python -m backend.auth issue --facility <ma CSKCB> --name "<ten>"
# Dong [AUTH] luc khoi dong cho biet dang o che do nao.
Write-Host ""

& .venv\Scripts\python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port $Port
