@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "APP_DIR=%~dp0tools\IndexTTS2"
set "PYTHON=%ROOT_DIR%runtime\python\python.exe"
set "APPDATA=%ROOT_DIR%runtime\data\indextts2\appdata"
set "LOCALAPPDATA=%ROOT_DIR%runtime\data\indextts2\localappdata"
set "TEMP=%ROOT_DIR%runtime\temp"
set "TMP=%TEMP%"
set "HF_HOME=%APP_DIR%\checkpoints\hf_cache"
set "HF_HUB_CACHE=%APP_DIR%\checkpoints\hf_cache"
set "TORCH_HOME=%HF_HOME%"
set "XDG_CACHE_HOME=%ROOT_DIR%runtime\cache"
set "NUMBA_CACHE_DIR=%ROOT_DIR%runtime\cache\numba"
set "MPLCONFIGDIR=%ROOT_DIR%runtime\cache\matplotlib"
set "CUDA_CACHE_PATH=%ROOT_DIR%runtime\cache\cuda"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PATH=%ROOT_DIR%runtime\python;%ROOT_DIR%runtime\python\Scripts;%ROOT_DIR%runtime\python\Lib\site-packages\torch\lib;%ROOT_DIR%tools\ffmpeg\bin;%PATH%"

for %%D in ("%APPDATA%" "%LOCALAPPDATA%" "%TEMP%" "%XDG_CACHE_HOME%" "%NUMBA_CACHE_DIR%" "%MPLCONFIGDIR%" "%CUDA_CACHE_PATH%") do if not exist "%%~D" mkdir "%%~D"

if not exist "%PYTHON%" (
    echo [ERROR] Official IndexTTS2 Python runtime not found:
    echo %PYTHON%
    pause
    exit /b 1
)

if not exist "%APP_DIR%\checkpoints\config.yaml" (
    echo [ERROR] Official IndexTTS2 checkpoints not found:
    echo %APP_DIR%\checkpoints
    pause
    exit /b 1
)

echo Starting official IndexTTS2 WebUI on http://127.0.0.1:7860 ...
start "Official IndexTTS2 WebUI" /D "%APP_DIR%" "%PYTHON%" -I "%APP_DIR%\webui.py" --host 127.0.0.1 --port 7860 --fp16

echo.
echo If the browser does not open automatically, visit:
echo   http://127.0.0.1:7860
echo.
echo Keep the opened window running while using the UI.
pause
