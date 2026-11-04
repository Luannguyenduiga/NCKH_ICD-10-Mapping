# Khoi dong toan bo he thong SMIG demo:
#   Docker Desktop -> EMR Cloud (HAPI FHIR + PostgreSQL) -> PostgreSQL cua VNPT
#   -> SMIG Gateway (8000) -> HIS A (8085) -> HIS B (8086) -> VNPT HIS (8089, 3000)
# Moi dich vu chay trong mot cua so PowerShell rieng de xem log. Chay lai an toan:
# dich vu nao da len roi thi bo qua.

param([switch]$KhongMoTrinhDuyet)

$ErrorActionPreference = 'Continue'
# Tat thanh tien trinh cua Invoke-WebRequest: bat no tren console PowerShell 5.1 lam
# viec cho dich vu len cham hon nhieu (CapabilityStatement cua HAPI nang ~650 KB).
$ProgressPreference = 'SilentlyContinue'
$root = $PSScriptRoot
Set-Location $root

function Buoc($msg)  { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Canh($msg)  { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Loi($msg)   { Write-Host "[LOI]  $msg" -ForegroundColor Red }

function Test-Url($url) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

# Gateway chi san sang khi mo hinh NLP da nap (co the tra HTTP 200 khi dang degraded).
function Test-Gateway {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 3
        return ($r.Content -match '"model_loaded":\s*true')
    } catch { return $false }
}

function Wait-Gateway($giay) {
    $het = (Get-Date).AddSeconds($giay)
    while ((Get-Date) -lt $het) {
        if (Test-Gateway) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Wait-Url($url, $giay) {
    $het = (Get-Date).AddSeconds($giay)
    while ((Get-Date) -lt $het) {
        if (Test-Url $url) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Mo-CuaSo($tieuDe, $lenh) {
    $script = "`$host.UI.RawUI.WindowTitle = '$tieuDe'; Set-Location '$root'; $lenh"
    Start-Process powershell -ArgumentList @('-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $script) | Out-Null
}

# ---------- 1. Docker Desktop ----------
Buoc "Docker Desktop"

$docker = $null
if (Get-Command docker -ErrorAction SilentlyContinue) { $docker = 'docker' }
elseif (Test-Path 'D:\docker\resources\bin\docker.exe') { $docker = 'D:\docker\resources\bin\docker.exe' }
elseif (Test-Path 'C:\Program Files\Docker\Docker\resources\bin\docker.exe') { $docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }

if (-not $docker) {
    Loi "Khong tim thay Docker. Cai Docker Desktop 4.49.0 truoc khi chay."
    exit 1
}

$dockerChay = $false
& $docker info *> $null
if ($LASTEXITCODE -eq 0) { $dockerChay = $true }

if (-not $dockerChay) {
    $exe = $null
    foreach ($p in @('D:\docker\Docker Desktop.exe', 'C:\Program Files\Docker\Docker\Docker Desktop.exe')) {
        if (Test-Path $p) { $exe = $p; break }
    }
    if ($exe) {
        Canh "Docker chua chay, dang mo Docker Desktop..."
        Start-Process $exe | Out-Null
        $het = (Get-Date).AddSeconds(240)
        while ((Get-Date) -lt $het) {
            & $docker info *> $null
            if ($LASTEXITCODE -eq 0) { $dockerChay = $true; break }
            Start-Sleep -Seconds 5
        }
    }
}

if (-not $dockerChay) {
    Loi "Docker chua san sang sau 4 phut. Mo Docker Desktop bang tay roi chay lai."
    exit 1
}
Ok "Docker engine dang chay"

# ---------- 2. EMR Cloud (HAPI FHIR + PostgreSQL) ----------
Buoc "EMR Cloud (HAPI FHIR)"
if (Test-Url 'http://127.0.0.1:8090/fhir/metadata') {
    Ok "EMR Cloud da chay, bo qua"
} else {
    & $docker compose up -d --pull missing | Out-Null
    if (Wait-Url 'http://127.0.0.1:8090/fhir/metadata' 240) { Ok "EMR Cloud san sang tai http://127.0.0.1:8090/fhir" }
    else { Loi "EMR Cloud chua len sau 4 phut. Xem: docker compose logs hapi-fhir" }
}

# ---------- 3. PostgreSQL cho VNPT ----------
Buoc "PostgreSQL cua VNPT HIS"
$tonTai = & $docker ps -a --filter "name=^/vnpt-his-db$" --format '{{.Names}}'
if ($tonTai -eq 'vnpt-his-db') {
    & $docker start vnpt-his-db | Out-Null
} else {
    $matKhau = 'postgres'
    $props = Join-Path $root 'VNPT_HIS\backend\src\main\resources\application.properties'
    if (Test-Path $props) {
        $dong = Get-Content $props | Where-Object { $_ -match '^spring\.datasource\.password=' }
        if ($dong) { $matKhau = ($dong -split '=', 2)[1].Trim() }
    }
    & $docker run -d --name vnpt-his-db --restart unless-stopped `
        -e "POSTGRES_PASSWORD=$matKhau" -e POSTGRES_DB=vnpt_his `
        -p 127.0.0.1:5432:5432 -v vnpt-his-pgdata:/var/lib/postgresql/data postgres:16-alpine | Out-Null
}
Start-Sleep -Seconds 5
if ((& $docker ps --filter "name=^/vnpt-his-db$" --format '{{.Status}}') -match 'Up') { Ok "PostgreSQL VNPT dang chay" }
else { Loi "PostgreSQL VNPT khong chay. Xem: docker logs vnpt-his-db" }

# ---------- 4. Moi truong Python cho Gateway va HIS ----------
Buoc "Moi truong Python (.venv)"
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Canh "Chua co .venv, dang tao va cai thu vien (lan dau mat vai phut)..."
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip -q
    & .\.venv\Scripts\pip.exe install -r requirements.txt -q
}
if (Test-Path '.venv\Scripts\python.exe') { Ok ".venv san sang" } else { Loi "Khong tao duoc .venv. Kiem tra Python 3.10/3.11."; exit 1 }

# ---------- 5. SMIG Gateway (8000) ----------
Buoc "SMIG Gateway (8000)"
# VRAM: chuong trinh khac (vi du job suy luan) co the chiem het GPU, khi do nap mo hinh
# NLP len GPU se loi 'CUDA out of memory'. Neu VRAM trong duoi 3 GB thi chay Gateway tren CPU.
$vramTrong = 0
try { $vramTrong = [int]((nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits) | Select-Object -First 1) } catch { $vramTrong = 0 }
if ($vramTrong -ge 3000) {
    $gpuLenh = ''
    Ok "GPU con trong $vramTrong MB, Gateway dung GPU"
} else {
    $gpuLenh = "`$env:CUDA_VISIBLE_DEVICES='-1'; "
    Canh "GPU chi con $vramTrong MB (co chuong trinh khac dang dung). Gateway chay tren CPU: cham hon, khoang 3 giay moi cau."
}
if (Test-Gateway) { Ok "Gateway da chay, bo qua" }
else {
    Mo-CuaSo 'SMIG Gateway 8000' ($gpuLenh + "& '.\run.ps1' -Port 8000 -AllowClientFacility")
    Ok "Da mo cua so Gateway (nap mo hinh mat 1-2 phut lan dau)"
}

# ---------- 6. HIS A va HIS B ----------
Buoc "HIS Python: bệnh viện A và B"
if (Test-Url 'http://127.0.0.1:8085/') { Ok "HIS A da chay, bo qua" }
else { Mo-CuaSo 'HIS A 8085' "& '.\hospital_his\run_his.ps1' -Port 8085 -FacilityCode 79001 -FacilityName 'Benh vien mo phong Viettel'"; Ok "Da mo cua so HIS A" }

if (Test-Url 'http://127.0.0.1:8086/') { Ok "HIS B da chay, bo qua" }
else { Mo-CuaSo 'HIS B 8086' "& '.\hospital_his\run_his.ps1' -Port 8086 -FacilityCode 79003 -FacilityName 'Benh vien Da khoa B'"; Ok "Da mo cua so HIS B" }

# ---------- 7. VNPT HIS: backend Java (8089) va frontend (3000) ----------
Buoc "VNPT HIS"
$jdk = $null
foreach ($h in @($env:JAVA_HOME, 'D:\tools\jdk17')) {
    if ($h -and (Test-Path (Join-Path $h 'bin\java.exe'))) { $jdk = $h; break }
}

if (Test-Url 'http://127.0.0.1:8089/api/patients') { Ok "VNPT backend da chay, bo qua" }
elseif (-not $jdk) { Canh "Chua tim thay JDK 17 (dat JAVA_HOME hoac cai vao D:\tools\jdk17). Bo qua VNPT backend." }
else {
    $lenh = "`$env:JAVA_HOME='$jdk'; `$env:PATH='$jdk\bin;' + `$env:PATH; Set-Location '$root\VNPT_HIS\backend'; .\mvnw.cmd spring-boot:run"
    Mo-CuaSo 'VNPT backend 8089' $lenh
    Ok "Da mo cua so VNPT backend (JDK: $jdk)"
}

if (Test-Url 'http://127.0.0.1:3000/') { Ok "VNPT frontend da chay, bo qua" }
elseif (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Canh "Chua co Node.js. Bo qua VNPT frontend." }
else {
    $fe = Join-Path $root 'VNPT_HIS\frontend'
    if (-not (Test-Path (Join-Path $fe 'node_modules'))) {
        Canh "Chua co node_modules, dang cai thu vien VNPT frontend..."
        Push-Location $fe; npm install --no-audit --no-fund | Out-Null; Pop-Location
    }
    Mo-CuaSo 'VNPT frontend 3000' "Set-Location '$fe'; npm run dev -- --host 127.0.0.1"
    Ok "Da mo cua so VNPT frontend"
}

# ---------- 8. Cho cac dich vu len ----------
Buoc "Kiem tra tung dich vu"
$kiemTra = @(
    @{ Ten = 'EMR Cloud (HAPI FHIR)';   Url = 'http://127.0.0.1:8090/fhir/metadata'; Giay = 240 },
    @{ Ten = 'SMIG Gateway';            Url = 'gateway';                             Giay = 300 },
    @{ Ten = 'HIS A (8085)';            Url = 'http://127.0.0.1:8085/';              Giay = 120 },
    @{ Ten = 'HIS B (8086)';            Url = 'http://127.0.0.1:8086/';              Giay = 120 },
    @{ Ten = 'VNPT backend (8089)';     Url = 'http://127.0.0.1:8089/api/patients';  Giay = 240 },
    @{ Ten = 'VNPT frontend (3000)';    Url = 'http://127.0.0.1:3000/';              Giay = 120 }
)
$ketQua = @()
foreach ($d in $kiemTra) {
    $len = $(if ($d.Url -eq 'gateway') { Wait-Gateway $d.Giay } else { Wait-Url $d.Url $d.Giay })
    $ketQua += [pscustomobject]@{ Dich_vu = $d.Ten; Trang_thai = $(if ($len) { 'SAN SANG' } else { 'CHUA LEN' }); Dia_chi = $(if ($d.Url -eq 'gateway') { 'http://127.0.0.1:8000/health' } else { $d.Url }) }
}
$ketQua | Format-Table -AutoSize | Out-String | Write-Host

# ---------- 9. Mo trinh duyet ----------
if (-not $KhongMoTrinhDuyet) {
    foreach ($u in @('http://127.0.0.1:8000', 'http://127.0.0.1:8085', 'http://127.0.0.1:8086', 'http://127.0.0.1:3000')) {
        Start-Process $u | Out-Null
    }
}

Write-Host ""
Write-Host "Xong. Dung cac cua so PowerShell se tat dich vu. De tat tat ca, chay tat-toan-bo.bat." -ForegroundColor Green
