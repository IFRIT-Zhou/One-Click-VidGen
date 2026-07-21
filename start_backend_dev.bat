@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "BACKEND_PYTHON=%ROOT_DIR%runtime\python\python.exe"
set "ASR_PYTHON=%BACKEND_PYTHON%"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "APPDATA=%ROOT_DIR%runtime\data\appdata"
set "LOCALAPPDATA=%ROOT_DIR%runtime\data\localappdata"
set "TEMP=%ROOT_DIR%runtime\temp"
set "TMP=%TEMP%"
set "HF_HOME=%ROOT_DIR%tools\IndexTTS2\checkpoints\hf_cache"
set "HF_HUB_CACHE=%HF_HOME%"
set "TORCH_HOME=%HF_HOME%"
set "XDG_CACHE_HOME=%ROOT_DIR%runtime\cache"
set "NUMBA_CACHE_DIR=%ROOT_DIR%runtime\cache\numba"
set "MPLCONFIGDIR=%ROOT_DIR%runtime\cache\matplotlib"
set "CUDA_CACHE_PATH=%ROOT_DIR%runtime\cache\cuda"
set "PATH=%ROOT_DIR%runtime\python;%ROOT_DIR%runtime\python\Scripts;%ROOT_DIR%runtime\python\Lib\site-packages\torch\lib;%ROOT_DIR%tools\ffmpeg\bin;%ROOT_DIR%runtime\node;%PATH%"
set "HYPERFRAMES_BROWSER_PATH="
for /r "%ROOT_DIR%runtime\hyperframes\.cache\hyperframes\chrome" %%F in (chrome-headless-shell.exe) do if not defined HYPERFRAMES_BROWSER_PATH set "HYPERFRAMES_BROWSER_PATH=%%F"

if not exist "%BACKEND_PYTHON%" (
    echo [ERROR] Portable Python runtime was not found:
    echo %BACKEND_PYTHON%
    exit /b 1
)

if not defined HYPERFRAMES_BROWSER_PATH (
    echo [ERROR] Bundled Hyperframes Chrome Headless Shell was not found.
    exit /b 1
)

for %%D in ("%APPDATA%" "%LOCALAPPDATA%" "%TEMP%" "%XDG_CACHE_HOME%" "%NUMBA_CACHE_DIR%" "%MPLCONFIGDIR%" "%CUDA_CACHE_PATH%") do if not exist "%%~D" mkdir "%%~D"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri 'http://127.0.0.1:8010/api/session'; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 (
    echo Backend is already running on http://127.0.0.1:8010
    exit /b 0
)

cd /d "%ROOT_DIR%"
"%BACKEND_PYTHON%" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8010
