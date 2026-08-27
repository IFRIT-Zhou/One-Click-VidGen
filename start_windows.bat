@echo off
setlocal EnableDelayedExpansion

set "ROOT_DIR=%~dp0"
set "ROOT_PATH=%ROOT_DIR:~0,-1%"
cd /d "%ROOT_DIR%"
set "BACKEND_PYTHON=%ROOT_DIR%runtime\python\python.exe"
set "WATCHDOG_PYTHON=%ROOT_DIR%runtime\python\pythonw.exe"
set "ASR_PYTHON=%BACKEND_PYTHON%"
set "NPM=%ROOT_DIR%runtime\node\npm.cmd"
set "NODE=%ROOT_DIR%runtime\node\node.exe"
set "VITE=%ROOT_DIR%frontend\node_modules\vite\bin\vite.js"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "APPDATA=%ROOT_DIR%runtime\data\appdata"
set "LOCALAPPDATA=%ROOT_DIR%runtime\data\localappdata"
set "TEMP=%ROOT_DIR%runtime\temp"
set "TMP=%TEMP%"
set "HF_HOME=%ROOT_DIR%tools\IndexTTS25\checkpoints\hf_cache"
set "HF_HUB_CACHE=%HF_HOME%"
set "TORCH_HOME=%HF_HOME%"
set "XDG_CACHE_HOME=%ROOT_DIR%runtime\cache"
set "NUMBA_CACHE_DIR=%ROOT_DIR%runtime\cache\numba"
set "MPLCONFIGDIR=%ROOT_DIR%runtime\cache\matplotlib"
set "CUDA_CACHE_PATH=%ROOT_DIR%runtime\cache\cuda"
set "npm_config_cache=%ROOT_DIR%runtime\npm-cache"
set "PATH=%ROOT_DIR%runtime\python;%ROOT_DIR%runtime\python\Scripts;%ROOT_DIR%runtime\python\Lib\site-packages\torch\lib;%ROOT_DIR%tools\ffmpeg\bin;%ROOT_DIR%runtime\node;%PATH%"
set "HYPERFRAMES_BROWSER_PATH="
for /r "%ROOT_DIR%runtime\hyperframes\.cache\hyperframes\chrome" %%F in (chrome-headless-shell.exe) do if not defined HYPERFRAMES_BROWSER_PATH set "HYPERFRAMES_BROWSER_PATH=%%F"

for %%D in ("%APPDATA%" "%LOCALAPPDATA%" "%TEMP%" "%XDG_CACHE_HOME%" "%NUMBA_CACHE_DIR%" "%MPLCONFIGDIR%" "%CUDA_CACHE_PATH%" "%npm_config_cache%") do if not exist "%%~D" mkdir "%%~D"

echo Checking runtime commands...
if not exist "!BACKEND_PYTHON!" (
    echo [ERROR] Backend Python venv was not found:
    echo !BACKEND_PYTHON!
    pause
    exit /b 1
)
if not exist "!WATCHDOG_PYTHON!" (
    echo [ERROR] Portable Python windowless runtime was not found:
    echo !WATCHDOG_PYTHON!
    pause
    exit /b 1
)

echo Checking portable runtime paths...
"%BACKEND_PYTHON%" "%ROOT_DIR%tools\portable_preflight.py"
if errorlevel 1 (
    echo [ERROR] Portable runtime path check failed. Please correct the reported configuration.
    pause
    exit /b 1
)

if not exist "!NPM!" (
    echo [ERROR] Portable Node/npm runtime was not found:
    echo !NPM!
    pause
    exit /b 1
)

if not defined HYPERFRAMES_BROWSER_PATH (
    echo [ERROR] Bundled Hyperframes Chrome Headless Shell was not found.
    echo         Please re-download the complete portable package.
    pause
    exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [ERROR] FFmpeg was not found.
    pause
    exit /b 1
)

if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo [INFO] Created .env from .env.example. Configure API Keys in the left-side UI panel.
    ) else (
        echo [WARN] .env and .env.example were not found.
    )
    echo.
)

if not exist "node_modules\hyperframes" (
    echo [ERROR] Root dependencies are missing.
    echo        Run: npm install
    pause
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo [ERROR] Frontend dependencies are missing.
    echo        Run: cd frontend
    echo             npm install
    pause
    exit /b 1
)

if not exist "%ROOT_DIR%runtime_logs" mkdir "%ROOT_DIR%runtime_logs"

call :check_backend_state
set "BACKEND_STATE=%errorlevel%"
if "%BACKEND_STATE%"=="2" (
    echo [RESTART] Backend source files changed; stopping stale service on port 8010...
    call :stop_port 8010
    if errorlevel 1 goto :startup_failed
    call :wait_port_closed 8010
    if errorlevel 1 goto :startup_failed
    set "BACKEND_STATE=1"
)
if "%BACKEND_STATE%"=="1" (
    echo [START] Backend in unified console...
    start "" /b "!BACKEND_PYTHON!" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8010 1>>"!ROOT_DIR!runtime_logs\backend.stdout.log" 2>>"!ROOT_DIR!runtime_logs\backend.stderr.log"
) else (
    echo [OK] Backend is already running and source is current.
)

call :check_url "http://127.0.0.1:5173"
if errorlevel 1 (
    echo [START] Frontend in unified console...
    start "" /b "!NODE!" "!VITE!" "!ROOT_DIR!frontend" --host 127.0.0.1 --port 5173 --strictPort 1>>"!ROOT_DIR!runtime_logs\frontend.stdout.log" 2>>"!ROOT_DIR!runtime_logs\frontend.stderr.log"
) else (
    echo [OK] Frontend is already running.
)

call :wait_url "http://127.0.0.1:8010/api/session" "Backend"
if errorlevel 1 goto :startup_failed
call :wait_url "http://127.0.0.1:5173" "Frontend"
if errorlevel 1 goto :startup_failed

echo.
echo Open this URL:
echo   http://127.0.0.1:5173
echo.
echo Backend and frontend are running without extra windows.
echo Keep this window open while using the app.
echo Closing this window will automatically stop both local services.
echo You can also run stop_dev.bat for manual cleanup.
echo.

set "BACKEND_PID="
set "FRONTEND_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8010 .*LISTENING"') do if not defined BACKEND_PID set "BACKEND_PID=%%P"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":5173 .*LISTENING"') do if not defined FRONTEND_PID set "FRONTEND_PID=%%P"
if not defined BACKEND_PID goto :startup_failed
if not defined FRONTEND_PID goto :startup_failed

set "WATCHDOG_EXTRA_ARGS="
if defined OCV_EXTRA_WATCHDOG_PID set "WATCHDOG_EXTRA_ARGS=--pid !OCV_EXTRA_WATCHDOG_PID!"

set "LIFETIME_HEARTBEAT=%ROOT_DIR%runtime_logs\launcher_%RANDOM%_%RANDOM%.heartbeat"
>"!LIFETIME_HEARTBEAT!" echo alive
start "" /b "!WATCHDOG_PYTHON!" "!ROOT_DIR!tools\local_service_watchdog.py" --heartbeat "!LIFETIME_HEARTBEAT!" --pid "!BACKEND_PID!" --pid "!FRONTEND_PID!" !WATCHDOG_EXTRA_ARGS! --log "!ROOT_DIR!runtime_logs\service_watchdog.log"
if errorlevel 1 (
    echo [ERROR] Could not start the service lifetime watchdog.
    goto :startup_failed
)

:keep_services_alive
>"!LIFETIME_HEARTBEAT!" echo alive
ping 127.0.0.1 -n 3 >nul
goto :keep_services_alive

:startup_failed
echo.
echo [ERROR] One or more services failed to start.
echo         Check logs under: !ROOT_DIR!runtime_logs
pause
exit /b 1

:check_url
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri '%~1'; if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { exit 0 } } catch {}; exit 1" >nul 2>nul
exit /b %errorlevel%

:check_backend_state
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri 'http://127.0.0.1:8010/api/health'; $data = $response.Content | ConvertFrom-Json; if (-not $data.server_started_at) { exit 2 }; $started = [DateTimeOffset]::FromUnixTimeMilliseconds([int64]([double]$data.server_started_at * 1000)).UtcDateTime; $files = @(); $files += Get-ChildItem -LiteralPath '%ROOT_DIR%backend\app' -Recurse -File -Filter '*.py'; $files += Get-ChildItem -LiteralPath '%ROOT_DIR%' -File -Filter '*.py'; $latest = $files | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1; if ($latest -and $latest.LastWriteTimeUtc -gt $started.AddSeconds(2)) { exit 2 }; exit 0 } catch { exit 1 }" >nul 2>nul
exit /b %errorlevel%

:stop_port
set "STOP_FAILED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%~1 .*LISTENING"') do (
    taskkill /PID %%P /T /F >nul 2>nul
    if errorlevel 1 set "STOP_FAILED=1"
)
if "!STOP_FAILED!"=="1" (
    echo [ERROR] Could not stop the stale service on port %~1. Please run stop_dev.bat as administrator once.
    exit /b 1
)
exit /b 0

:wait_port_closed
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$deadline = [DateTime]::UtcNow.AddSeconds(10); while ([DateTime]::UtcNow -lt $deadline) { $listening = netstat -ano | Select-String -Pattern ('^\s*TCP\s+\S+:' + %~1 + '\s+\S+\s+LISTENING\s+\d+\s*$'); if (-not $listening) { exit 0 }; Start-Sleep -Milliseconds 250 }; exit 1" >nul 2>nul
exit /b %errorlevel%

:wait_url
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$deadline = [DateTime]::UtcNow.AddSeconds(45); while ([DateTime]::UtcNow -lt $deadline) { try { $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri '%~1'; if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { Write-Host '[OK] %~2 ready: %~1'; exit 0 } } catch {}; Start-Sleep -Milliseconds 500 }; Write-Host '[ERROR] %~2 did not become ready: %~1'; exit 1"
exit /b %errorlevel%
