@echo off
setlocal
set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"
chcp 65001 >nul
title 初始化便携视频产线环境
set "PYTHON=%ROOT_DIR%runtime\python\python.exe"
set "NPM=%ROOT_DIR%runtime\node\npm.cmd"
set "npm_config_cache=%ROOT_DIR%runtime\npm-cache"
set "PATH=%ROOT_DIR%runtime\python;%ROOT_DIR%runtime\node;%ROOT_DIR%tools\ffmpeg\bin;%PATH%"

if not exist "%PYTHON%" (
    echo [致命错误] 缺少项目内便携 Python：%PYTHON%
    pause
    exit /b 1
)

if not exist "%NPM%" (
    echo [致命错误] 缺少项目内便携 Node/npm：%NPM%
    pause
    exit /b 1
)

echo [1/3] 检查便携 Python 核心依赖...
"%PYTHON%" -I -c "import fastapi, faster_whisper, indextts, torch, uvicorn"
if errorlevel 1 (
    echo [致命错误] 便携 Python 依赖不完整，请重新获取完整整合包。
    pause
    exit /b 1
)

echo [2/3] 检查并补齐根目录 Node 依赖...
call "%NPM%" install --registry=https://registry.npmmirror.com

if errorlevel 1 (
    echo [致命错误] 根目录 Node 依赖安装失败，请检查网络。
    pause
    exit /b 1
)

echo [3/3] 检查并补齐前端 Node 依赖...
pushd "%ROOT_DIR%frontend"
call "%NPM%" install --registry=https://registry.npmmirror.com
set "INSTALL_RESULT=%ERRORLEVEL%"
popd
if not "%INSTALL_RESULT%"=="0" (
    echo [致命错误] 前端 Node 依赖安装失败，请检查网络。
    pause
    exit /b 1
)

echo.
echo 环境检查完成。所有运行时和缓存均位于当前项目目录。
pause
