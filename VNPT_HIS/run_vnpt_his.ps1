# Unified Runner for VNPT HIS Clone

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   VNPT HIS CLONE RUNNER - STARTING UP   " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$CurrentDir = $PSScriptRoot
if (-not $CurrentDir) {
    $CurrentDir = Get-Location
}

# 0. Nap VNPT_HIS\.env neu co (KEY=VALUE, mot dong mot bien). Dung de giu
#    SMIG_GATEWAY_API_KEY - khoa API do Gateway cap cho co so 79002 - ma khong
#    ghi vao application.properties. Bien dat o day duoc ke thua sang cua so
#    Spring Boot mo ben duoi, va application.properties doc no qua
#    ${SMIG_GATEWAY_API_KEY:}. Tep nay nam trong .gitignore (*.env).
$envFile = Join-Path $CurrentDir ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $k, $v = $line.Split("=", 2)
            [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim().Trim('"'), "Process")
        }
    }
    Write-Host "[INFO] Da nap cau hinh tu $envFile" -ForegroundColor Green
}
if ($env:SMIG_GATEWAY_API_KEY) {
    Write-Host "[INFO] Khoa API Gateway: da dat (khong hien)" -ForegroundColor Green
} else {
    Write-Host "[WARN] Chua dat SMIG_GATEWAY_API_KEY. Neu Gateway da cap khoa, dong bo len truc se bi 401." -ForegroundColor Yellow
}

# 1. Start Backend in a new window
Write-Host "[1/2] Starting Spring Boot Java Backend..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$CurrentDir\backend'; Write-Host 'Starting Spring Boot backend (PostgreSQL)...' -ForegroundColor Green; ./mvnw.cmd spring-boot:run" -WindowStyle Normal

# 2. Start Frontend in a new window
Write-Host "[2/2] Starting React Vite Frontend..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$CurrentDir\frontend'; Write-Host 'Starting Vite development server...' -ForegroundColor Cyan; npm run dev" -WindowStyle Normal

Write-Host ""
Write-Host "Both Frontend and Backend are launching!" -ForegroundColor Cyan
Write-Host "- Frontend URL: http://localhost:3000" -ForegroundColor Yellow
Write-Host "- Backend URL:  http://localhost:8089" -ForegroundColor Yellow
Write-Host "- Make sure your local PostgreSQL is running and database 'vnpt_his' exists." -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Cyan
