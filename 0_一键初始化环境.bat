@echo off
cd /d %~dp0
chcp 65001 >nul
title 初始化视频产线环境

echo [系统接管] 正在强制配置 npm 国内淘宝镜像源...
call npm config set registry https://registry.npmmirror.com

echo [系统接管] 正在将 hyperframes 渲染引擎固化至本地项目目录...
echo [提示] 首次安装需要拉取依赖，请耐心等待几分钟，切勿关闭窗口。
call npm install hyperframes

if errorlevel 1 (
    echo.
    echo [致命错误] 安装失败，请检查网络连接或 Node.js 环境。
    pause
    exit /b
)

echo.
echo ✅ 核心渲染引擎安装完毕！环境已全部就绪。
pause