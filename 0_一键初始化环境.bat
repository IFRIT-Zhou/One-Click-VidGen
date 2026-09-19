@echo off
setlocal
set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"
chcp 65001 >nul
title 初始化便携视频产线环境

rem Keep this file UTF-8 with chcp 65001, keep the comments ASCII and keep every
rem Chinese string short: cmd.exe mangles long multi-byte lines in batch files
rem (observed as "is not recognized" errors on the comment lines themselves).
rem
rem Download, checksum, extract, self-check, backup, atomic replace and rollback
rem all live in tools\provision_runtime.ps1, driven by tools\portable_assets.json.
rem This script only orchestrates and contains no download logic of its own.
rem
rem The browser is not downloaded: OCV uses the system Chrome/Edge (see
rem find_portable_hyperframes_browser in module5_video_render.py).

set "ROOT_ARG=%ROOT_DIR:~0,-1%"
set "PYTHON=%ROOT_DIR%runtime\python\python.exe"
set "NPM=%ROOT_DIR%runtime\node\npm.cmd"
set "FFMPEG=%ROOT_DIR%tools\ffmpeg\bin\ffmpeg.exe"
set "WHISPER=%ROOT_DIR%tools\whisper_models\faster-whisper-base\model.bin"
set "npm_config_cache=%ROOT_DIR%runtime\npm-cache"
if not defined OCV_PIP_INDEX set "OCV_PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/"
if not defined OCV_TORCH_WHEELS set "OCV_TORCH_WHEELS=https://mirrors.aliyun.com/pytorch-wheels/cu128/"
set "PATH=%ROOT_DIR%runtime\python;%ROOT_DIR%runtime\node;%ROOT_DIR%tools\ffmpeg\bin;%PATH%"

echo.
echo [1/6] 便携 Python
if not exist "%PYTHON%" goto :install_python
echo       已存在，跳过。
goto :python_ready
:install_python
call :provision python
if errorlevel 1 goto :fatal
if not exist "%PYTHON%" goto :fatal
:python_ready

echo.
echo [2/6] 便携 Node.js
if not exist "%NPM%" goto :install_node
echo       已存在，跳过。
goto :node_ready
:install_node
call :provision node
if errorlevel 1 goto :fatal
if not exist "%NPM%" goto :fatal
:node_ready

echo.
echo [3/6] 便携 FFmpeg
if not exist "%FFMPEG%" goto :install_ffmpeg
if defined OCV_FORCE_FFMPEG goto :install_ffmpeg
echo       已存在，跳过。
goto :ffmpeg_ready
:install_ffmpeg
call :provision ffmpeg
if errorlevel 1 goto :fatal
:ffmpeg_ready

echo.
echo [4/6] 字幕模型
if not exist "%WHISPER%" goto :install_whisper
echo       已存在，跳过。
goto :whisper_ready
:install_whisper
call :provision whisper
if errorlevel 1 goto :fatal
:whisper_ready

echo.
echo [5/6] Python 依赖
"%PYTHON%" -I -c "import fastapi, faster_whisper, torch, uvicorn, PIL" >nul 2>nul
if not errorlevel 1 goto :python_deps_ready
echo       安装 torch / torchaudio
"%PYTHON%" -m pip install --disable-pip-version-check --find-links "%OCV_TORCH_WHEELS%" --extra-index-url %OCV_PIP_INDEX% "torch==2.8.0+cu128" "torchaudio==2.8.0+cu128"
if not errorlevel 1 goto :python_rest
echo       回退官方 cu128 索引
"%PYTHON%" -m pip install --disable-pip-version-check --index-url https://download.pytorch.org/whl/cu128 "torch==2.8.0+cu128" "torchaudio==2.8.0+cu128"
if errorlevel 1 goto :fatal
:python_rest
echo       安装其余依赖
"%PYTHON%" -m pip install --disable-pip-version-check -i %OCV_PIP_INDEX% -r "%ROOT_DIR%requirements.txt"
if errorlevel 1 goto :fatal
"%PYTHON%" -I -c "import fastapi, faster_whisper, torch, uvicorn, PIL" >nul 2>nul
if errorlevel 1 goto :fatal
:python_deps_ready

echo.
echo [6/6] Node 依赖
call "%NPM%" install --registry=https://registry.npmmirror.com
if errorlevel 1 goto :npm_failed
pushd "%ROOT_DIR%frontend"
call "%NPM%" install --registry=https://registry.npmmirror.com
set "INSTALL_RESULT=%ERRORLEVEL%"
popd
if not "%INSTALL_RESULT%"=="0" goto :npm_failed

echo.
echo 环境检查完成。运行时与缓存均在项目目录内。
pause
exit /b 0

:npm_failed
echo [致命错误] Node 依赖安装失败，请检查网络。
goto :fatal

:provision
echo       按清单获取 %1（下载后校验厂商 SHA-256）
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT_DIR%tools\provision_runtime.ps1" -Component %1 -ProjectRoot "%ROOT_ARG%"
exit /b %errorlevel%

:fatal
echo.
echo [致命错误] 初始化未完成，请检查上方输出。
echo             清单与校验值：tools\portable_assets.json
pause
exit /b 1
