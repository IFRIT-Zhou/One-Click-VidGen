@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "NPM=%ROOT_DIR%runtime\node\npm.cmd"
set "APPDATA=%ROOT_DIR%runtime\data\appdata"
set "LOCALAPPDATA=%ROOT_DIR%runtime\data\localappdata"
set "TEMP=%ROOT_DIR%runtime\temp"
set "TMP=%TEMP%"
set "XDG_CACHE_HOME=%ROOT_DIR%runtime\cache"
set "npm_config_cache=%ROOT_DIR%runtime\npm-cache"
set "PATH=%ROOT_DIR%runtime\node;%ROOT_DIR%tools\ffmpeg\bin;%PATH%"

if not exist "%NPM%" (
    echo [ERROR] Portable Node/npm runtime was not found:
    echo %NPM%
    exit /b 1
)

for %%D in ("%APPDATA%" "%LOCALAPPDATA%" "%TEMP%" "%XDG_CACHE_HOME%" "%npm_config_cache%") do if not exist "%%~D" mkdir "%%~D"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri 'http://127.0.0.1:5173'; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 (
    echo Frontend is already running on http://127.0.0.1:5173
    exit /b 0
)

cd /d "%ROOT_DIR%frontend"
call "%NPM%" run dev -- --host 127.0.0.1 --port 5173 --strictPort
