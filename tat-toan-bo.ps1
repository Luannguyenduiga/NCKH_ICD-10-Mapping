# Tat toan bo he thong SMIG demo. Giu nguyen du lieu trong cac volume Docker.

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$root = $PSScriptRoot

Write-Host "== Dung cac dich vu tren cac cong demo" -ForegroundColor Cyan
foreach ($port in @(8000, 8085, 8086, 8089, 3000)) {
    $tienTrinh = netstat -ano | Select-String ":$port\s" | Select-String "LISTENING" |
        ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
    foreach ($tt in $tienTrinh) {
        taskkill /PID $tt /F /T *> $null
        if ($LASTEXITCODE -eq 0) { Write-Host "[OK]   Da dung cong $port (PID $tt)" -ForegroundColor Green }
    }
}

$docker = $null
if (Get-Command docker -ErrorAction SilentlyContinue) { $docker = 'docker' }
elseif (Test-Path 'D:\docker\resources\bin\docker.exe') { $docker = 'D:\docker\resources\bin\docker.exe' }

if ($docker) {
    Write-Host "== Dung container (giu nguyen du lieu)" -ForegroundColor Cyan
    Push-Location $root
    & $docker compose stop | Out-Null
    Pop-Location
    & $docker stop vnpt-his-db *> $null
    Write-Host "[OK]   Da dung EMR Cloud va PostgreSQL VNPT" -ForegroundColor Green
}

Write-Host ""
Write-Host "Da tat. Docker Desktop van mo; thoat no tu khay he thong neu can." -ForegroundColor Green
