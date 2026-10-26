@echo off
"%SystemRoot%\System32\chcp.com" 65001 >nul 2>nul
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
rem Repair only this process environment, never the user's global PATH.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\WindowsPowerShell\v1.0;%ROOT_DIR%runtime\python;%ROOT_DIR%runtime\python\Scripts;%ROOT_DIR%runtime\python\Lib\site-packages\torch\lib;%ROOT_DIR%tools\ffmpeg\bin;%ROOT_DIR%runtime\node;%PATH%"
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

rem Prefer the bundled executable; do not confuse a missing WHERE with missing FFmpeg.
set "FFMPEG_EXE=%ROOT_DIR%tools\ffmpeg\bin\ffmpeg.exe"
if not exist "!FFMPEG_EXE!" (
    set "FFMPEG_EXE="
    if not exist "%SystemRoot%\System32\where.exe" (
        echo [ERROR] Bundled FFmpeg is missing and Windows where.exe is unavailable.
        echo Expected: %ROOT_DIR%tools\ffmpeg\bin\ffmpeg.exe
        pause
        exit /b 1
    )
    for /f "delims=" %%F in ('%SystemRoot%\System32\where.exe ffmpeg.exe 2^>nul') do if not defined FFMPEG_EXE set "FFMPEG_EXE=%%F"
)
if not defined FFMPEG_EXE (
    echo [ERROR] FFmpeg was not found in the portable package or PATH.
    echo Expected: %ROOT_DIR%tools\ffmpeg\bin\ffmpeg.exe
    pause
    exit /b 1
)
"!FFMPEG_EXE!" -version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] FFmpeg exists but could not run: !FFMPEG_EXE!
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

if not exist "%ROOT_DIR%tools\launcher_service_probe.py" (
    echo [ERROR] Launcher probe is missing. Reinstall the complete launcher patch.
    goto :startup_failed
)
call :check_backend_state
set "BACKEND_STATE=%errorlevel%"
if not "%BACKEND_STATE%"=="0" if not "%BACKEND_STATE%"=="1" if not "%BACKEND_STATE%"=="2" if not "%BACKEND_STATE%"=="3" (
    echo [ERROR] Backend probe failed with code %BACKEND_STATE%. Backend state is unknown; startup cancelled.
    goto :startup_failed
)
if "%BACKEND_STATE%"=="2" (
    echo [RESTART] Backend source files changed; stopping stale service on port 8010...
    call :stop_port 8010
    if errorlevel 1 goto :startup_failed
    call :wait_port_closed 8010
    if errorlevel 1 goto :startup_failed
    set "BACKEND_STATE=1"
)
if "%BACKEND_STATE%"=="3" (
    echo [SAFE] Backend source changed, but an OCV task is still active.
    echo        Keeping the existing backend until the task completes.
    set "BACKEND_STATE=0"
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
"%BACKEND_PYTHON%" "%ROOT_DIR%tools\launcher_service_probe.py" url "%~1"
exit /b %errorlevel%

:check_backend_state
"%BACKEND_PYTHON%" "%ROOT_DIR%tools\launcher_service_probe.py" backend "%ROOT_PATH%"
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
"%BACKEND_PYTHON%" "%ROOT_DIR%tools\launcher_service_probe.py" closed "%~1"
exit /b %errorlevel%

:wait_url
"%BACKEND_PYTHON%" "%ROOT_DIR%tools\launcher_service_probe.py" wait "%~1"
exit /b %errorlevel%

