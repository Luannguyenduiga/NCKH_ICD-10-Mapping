# Unified Runner for VNPT HIS Clone

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   VNPT HIS CLONE RUNNER - STARTING UP   " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$CurrentDir = $PSScriptRoot
if (-not $CurrentDir) {
    $CurrentDir = Get-Location
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
