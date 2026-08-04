@echo off
setlocal EnableDelayedExpansion

set "ROOT_DIR=%~dp0"
set "PYTHON=%ROOT_DIR%runtime\python\python.exe"
set "MOCK_URL=http://127.0.0.1:8030/api/v1"
cd /d "%ROOT_DIR%"

if not exist "%PYTHON%" (
    echo [ERROR] Portable Python was not found: %PYTHON%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "if ((Get-NetTCPConnection -LocalPort 8010,5173 -State Listen -ErrorAction SilentlyContinue).Count -gt 0) { exit 1 }"
if errorlevel 1 (
    echo [ERROR] One-Click VidGen is already running.
    echo         Close its launcher window first, then double-click this test launcher again.
    pause
    exit /b 1
)

if not exist "%ROOT_DIR%runtime_logs" mkdir "%ROOT_DIR%runtime_logs"
powershell -NoProfile -ExecutionPolicy Bypass -Command "if ((Get-NetTCPConnection -LocalPort 8030 -State Listen -ErrorAction SilentlyContinue).Count -gt 0) { exit 0 } else { exit 1 }"
if errorlevel 1 (
    echo [START] Starting local mock cloud-api...
    start "" /b "%PYTHON%" -m uvicorn dev.mock_cloud_api:app --host 127.0.0.1 --port 8030 1>>"%ROOT_DIR%runtime_logs\mock_cloud.stdout.log" 2>>"%ROOT_DIR%runtime_logs\mock_cloud.stderr.log"
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=[DateTime]::UtcNow.AddSeconds(20); while([DateTime]::UtcNow -lt $deadline){try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri 'http://127.0.0.1:8030/api/v1/health';if($r.StatusCode -eq 200){exit 0}}catch{};Start-Sleep -Milliseconds 300};exit 1"
if errorlevel 1 (
    echo [ERROR] Mock cloud-api failed to start. Check runtime_logs\mock_cloud.stderr.log
    pause
    exit /b 1
)

set "MOCK_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8030 .*LISTENING"') do if not defined MOCK_PID set "MOCK_PID=%%P"
if not defined MOCK_PID (
    echo [ERROR] Could not determine the mock cloud process ID.
    pause
    exit /b 1
)

set "CLOUD_API_BASE_URL=%MOCK_URL%"
set "CLOUD_API_RETRY_COUNT=0"
set "CLOUD_JOB_POLL_INTERVAL=0.5"
set "OCV_EXTRA_WATCHDOG_PID=!MOCK_PID!"

echo [OK] Mock cloud-api ready: http://127.0.0.1:8030
echo [INFO] Opening the mock dashboard and starting One-Click VidGen...
start "" "http://127.0.0.1:8030"
call "%ROOT_DIR%start_windows.bat"

taskkill /PID !MOCK_PID! /T /F >nul 2>nul
endlocal
