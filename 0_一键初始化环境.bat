@echo off
setlocal EnableDelayedExpansion
rem ============================================================================
rem 本文件必须保持 GBK(cp936) 编码 + chcp 936。
rem 中文批处理若存成 UTF-8 并配合 chcp 65001，cmd 解析时会截断中文行并当成命令
rem 执行（实测 21 行中文里 4 行报 "is not recognized"）。请勿改回 UTF-8。
rem ============================================================================
set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"
chcp 936 >nul
title 初始化便携视频产线环境

rem ==================== 可覆盖参数（设置同名环境变量即可，无需改文件） ====================
if not defined OCV_PYTHON_VERSION  set "OCV_PYTHON_VERSION=3.11.13"
if not defined OCV_PYTHON_BUILD    set "OCV_PYTHON_BUILD=20250818"
if not defined OCV_NODE_VERSION    set "OCV_NODE_VERSION=22.20.0"
if not defined OCV_CHROME_VERSION  set "OCV_CHROME_VERSION=131.0.6778.85"
if not defined OCV_HF_ENDPOINT     set "OCV_HF_ENDPOINT=https://hf-mirror.com"
if not defined OCV_PIP_INDEX       set "OCV_PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/"
if not defined OCV_TORCH_WHEELS    set "OCV_TORCH_WHEELS=https://mirrors.aliyun.com/pytorch-wheels/cu128/"
if not defined OCV_TORCH_INDEX     set "OCV_TORCH_INDEX=https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/"
rem   OCV_PYTHON                    指定已有解释器（必须 64 位、3.10~3.12），优先级最高
rem   OCV_PYTHON_MODE               portable=强制下载便携 Python；system=强制使用本机 Python
rem   OCV_PYTHON_URL                覆盖便携 Python 下载源前缀（目录形式，需以 / 结尾）
rem   OCV_INDEXTTS_URL              覆盖 IndexTTS-2.5 源码包下载地址
rem   OCV_FFMPEG_URL                覆盖 FFmpeg 压缩包下载地址
rem   OCV_INSTALL_INDEXTTS_MODEL    1=下载本地配音权重（约 10.2 GB，仅本机跑模型时需要，建议可用显存 8 GB 以上）；0=不下载且不询问
rem   OCV_FORCE_FFMPEG=1            系统已有 ffmpeg 时也下载便携版本
rem   OCV_SKIP_INDEX_PROBE=1       跳过 PyPI 镜像测速，直接用 OCV_PIP_INDEX
rem   OCV_TORCH_INDEX            torch 的 PEP 503 索引源（默认交大镜像，可换官方）
rem   OCV_TORCH_WHEELS           扁平 cu128 轮子目录（默认阿里云，作 --find-links 备份）
rem   OCV_SKIP_PIP / OCV_SKIP_NPM / OCV_SKIP_NODE / OCV_SKIP_FFMPEG / OCV_SKIP_CHROME
rem   OCV_SKIP_WHISPER / OCV_SKIP_INDEXTTS   设为 1 跳过对应步骤

set "PORTABLE_PY=%ROOT_DIR%runtime\python\python.exe"
set "VENV_DIR=%ROOT_DIR%runtime\python"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "NODE_DIR=%ROOT_DIR%runtime\node"
set "NPM=%NODE_DIR%\npm.cmd"
set "FFMPEG_DIR=%ROOT_DIR%tools\ffmpeg\bin"
set "BROWSER_DIR=%ROOT_DIR%runtime\hyperframes\.cache\hyperframes\chrome"
set "WHISPER_DIR=%ROOT_DIR%tools\whisper_models\faster-whisper-base"
set "INDEXTTS_DIR=%ROOT_DIR%tools\IndexTTS25"
set "BOOT_DIR=%ROOT_DIR%runtime\_bootstrap"
set "npm_config_cache=%ROOT_DIR%runtime\npm-cache"
set "PROBE_OUT=%TEMP%\ocv_python_probe.out"
set "PROBE_CODE=import struct,sys;print(sys.executable);print(sys.version_info[0]*100+sys.version_info[1]);print(struct.calcsize('P')*8);print(0 if sys.prefix==sys.base_prefix else 1)"
set "TORCH_LINKS=%OCV_TORCH_WHEELS%"
set "PYEXE="
set "PYMODE="
set "IS_PORTABLE="
set "CAND="
set "PROBED_EXE="
set "PROBED_VER="
set "PROBED_BITS="
set "PROBED_VENV="
set "ACCEPT="
set "PIP_STATUS=-"
set "NODE_STATUS=-"
set "FFMPEG_STATUS=-"
set "BROWSER_STATUS=-"
set "WHISPER_STATUS=-"
set "INDEXTTS_STATUS=-"
set "NPM_STATUS=-"

echo ============================================================
echo   一键成片 / One-Click VidGen —— 环境初始化
echo   项目目录：%ROOT_DIR%
echo ============================================================
echo.

if not exist ".env" if exist ".env.example" (
    copy /Y ".env.example" ".env" >nul
    echo [信息] 已从 .env.example 生成 .env，API Key 请在界面左侧面板填写。
    echo.
)

rem ==================== [1/8] Python 运行时 ====================
echo [1/8] 解析 Python 运行时...
if exist "%PORTABLE_PY%" (
    set "CAND="%PORTABLE_PY%""
    call :probe_current
    if defined ACCEPT (
        set "PYEXE=!PROBED_EXE!"
        set "PYMODE=项目内便携 Python"
        set "IS_PORTABLE=1"
    ) else (
        echo [警告] 项目内便携 Python 不可用（位数或版本不符），继续查找其它来源。
    )
)
if not defined PYEXE if /i not "%OCV_PYTHON_MODE%"=="portable" if defined OCV_PYTHON (
    if exist "!OCV_PYTHON!" (
        set "CAND="!OCV_PYTHON!""
        call :probe_current
        if defined ACCEPT (
            set "PYEXE=!PROBED_EXE!"
            set "PYMODE=环境变量 OCV_PYTHON"
        ) else (
            echo [警告] OCV_PYTHON 指定的解释器不合格（需要 64 位 Python 3.10~3.12）：!OCV_PYTHON!
        )
    ) else (
        echo [警告] OCV_PYTHON 指向的文件不存在：!OCV_PYTHON!
    )
)
if not defined PYEXE if /i not "%OCV_PYTHON_MODE%"=="portable" (
    where py >nul 2>nul
    if not errorlevel 1 (
        for %%V in (3.11 3.10 3.12) do (
            if not defined PYEXE (
                set "CAND=py -%%V"
                call :probe_current
                if defined ACCEPT (
                    set "PYEXE=!PROBED_EXE!"
                    set "PYMODE=系统 py -%%V"
                )
            )
        )
    )
)
if not defined PYEXE if /i not "%OCV_PYTHON_MODE%"=="portable" (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "CAND=python"
        call :probe_current
        if defined ACCEPT (
            set "PYEXE=!PROBED_EXE!"
            set "PYMODE=PATH 上的 python"
        )
    )
)

rem 自动探测到本机 Python 时提示：只有便携 Python 能启用本地配音与完整启动脚本
if not defined PYEXE goto :python_download
if "!IS_PORTABLE!"=="1" goto :python_ready
if /i "%OCV_PYTHON_MODE%"=="system" goto :python_ready
if defined OCV_PYTHON goto :python_ready
echo.
echo [提示] 本机 Python 模式下应用固定调用 runtime\python\python.exe，本地 IndexTTS-2.5
echo        配音不可用，start_windows.bat 也无法使用。
choice /c YN /n /t 20 /d Y /m "是否改用便携 Python（推荐，约 43 MB，功能完整）？[Y=便携 N=本机] " 2>nul
if errorlevel 2 (
    echo [信息] 继续使用本机 Python。
    goto :python_ready
)
echo [信息] 选择便携 Python。
set "PYEXE="
set "IS_PORTABLE="

:python_download
if not defined PYEXE (
    call :download_portable_python
    if errorlevel 1 (
        echo.
        echo [致命错误] 没有可用的 Python 运行时。
        echo             可手动下载便携 Python 并解压，使解释器位于：
        echo             %PORTABLE_PY%
        echo             或安装 64 位 Python 3.10~3.12 后设置 OCV_PYTHON 指向 python.exe。
        pause
        exit /b 1
    )
)

:python_ready
if not "!IS_PORTABLE!"=="1" (
    if "!PROBED_VENV!"=="1" (
        echo [信息] 使用已有的虚拟环境解释器：!PYEXE!
    ) else if exist "%VENV_PY%" (
        echo [信息] 复用项目内虚拟环境：%VENV_PY%
        set "PYEXE=%VENV_PY%"
    ) else (
        echo [信息] 正在创建项目内虚拟环境：%VENV_DIR%
        "!PROBED_EXE!" -m venv "%VENV_DIR%"
        if not exist "%VENV_PY%" (
            echo [致命错误] 虚拟环境创建失败，请检查磁盘空间与权限：%VENV_DIR%
            pause
            exit /b 1
        )
        set "PYEXE=%VENV_PY%"
    )
)
for %%D in ("!PYEXE!") do set "PYDIR=%%~dpD"
set "PATH=!PYDIR!;%ROOT_DIR%runtime\python;%NODE_DIR%;%FFMPEG_DIR%;%PATH%"
echo [信息] Python：!PYEXE!
echo [信息] 来源  ：!PYMODE!
echo.

rem ==================== [2/8] Python 依赖 ====================
echo [2/8] 检查 Python 核心依赖...
if defined OCV_SKIP_PIP (
    echo [信息] 已按 OCV_SKIP_PIP 跳过。
    set "PIP_STATUS=已跳过"
) else (
    "%PYEXE%" -I -c "import fastapi, uvicorn, torch, faster_whisper, PIL" >nul 2>nul
    if errorlevel 1 (
        echo [信息] 核心依赖不完整，开始安装（torch 轮子 3.46 GB，全部约 4-5 GB）...
        set "PIP_CACHE_DIR=%ROOT_DIR%runtime\cache\pip"
        if not exist "!PIP_CACHE_DIR!" mkdir "!PIP_CACHE_DIR!"
        call :pick_pypi_index
        echo [信息] PyPI 镜像：!PIP_INDEX!
        "%PYEXE%" -m pip install --upgrade pip -i "!PIP_INDEX!" --disable-pip-version-check
        call :install_torch
        echo [信息] 安装 requirements.txt 其余依赖...
        "%PYEXE%" -m pip install -r "%ROOT_DIR%requirements.txt" -i "!PIP_INDEX!" --find-links "!TORCH_LINKS!" --retries 5 --timeout 60
        if errorlevel 1 (
            echo [致命错误] Python 依赖安装失败。可手动重试：
            echo             "%PYEXE%" -m pip install -r "%ROOT_DIR%requirements.txt" -i !PIP_INDEX!
            pause
            exit /b 1
        )
    )
    "%PYEXE%" -I -c "import fastapi, uvicorn, torch, faster_whisper, PIL" >nul 2>nul
    if errorlevel 1 (
        echo [致命错误] 核心依赖仍不完整（fastapi / uvicorn / torch / faster_whisper / PIL）。
        pause
        exit /b 1
    )
    "%PYEXE%" -I -c "import indextts" >nul 2>nul
    if errorlevel 1 (
        echo [信息] 本地 IndexTTS-2.5 将按下方步骤单独装配（不由 pip 提供）。
    )
    set "PIP_STATUS=就绪"
    echo [信息] 核心依赖就绪。
)
echo.

rem ==================== [3/8] 便携 Node ====================
echo [3/8] 检查便携 Node 运行时...
if exist "%NPM%" (
    set "NODE_STATUS=项目内便携 Node"
    echo [信息] 已存在：%NPM%
) else if defined OCV_SKIP_NODE (
    set "NODE_STATUS=已跳过"
    echo [信息] 已按 OCV_SKIP_NODE 跳过。
) else (
    call :ensure_node
    if errorlevel 1 (
        set "NODE_STATUS=未安装"
        echo [警告] 便携 Node 未能安装，将尝试使用 PATH 上的系统 npm。
    ) else (
        set "NODE_STATUS=已下载便携 Node %OCV_NODE_VERSION%"
    )
)
echo.

rem ==================== [4/8] FFmpeg ====================
echo [4/8] 检查 FFmpeg...
if exist "%FFMPEG_DIR%\ffmpeg.exe" if exist "%FFMPEG_DIR%\ffprobe.exe" (
    set "FFMPEG_STATUS=项目内便携 FFmpeg"
    echo [信息] 已存在：%FFMPEG_DIR%
    goto :ffmpeg_done
)
if defined OCV_SKIP_FFMPEG (
    set "FFMPEG_STATUS=已跳过"
    echo [信息] 已按 OCV_SKIP_FFMPEG 跳过。
    goto :ffmpeg_done
)
set "SYS_FFMPEG_OK="
if not defined OCV_FORCE_FFMPEG (
    where ffmpeg >nul 2>nul
    if not errorlevel 1 (
        where ffprobe >nul 2>nul
        if not errorlevel 1 set "SYS_FFMPEG_OK=1"
    )
)
if defined SYS_FFMPEG_OK (
    set "FFMPEG_STATUS=系统 PATH 上的 FFmpeg"
    echo [信息] 系统 PATH 中已有 ffmpeg/ffprobe，跳过便携版下载。
    echo        如需项目自带（脱离系统 PATH），设置 OCV_FORCE_FFMPEG=1 重跑本脚本。
    goto :ffmpeg_done
)
call :ensure_ffmpeg
if errorlevel 1 (
    set "FFMPEG_STATUS=未安装"
    echo [警告] 便携 FFmpeg 未能安装。视频渲染需要 FFmpeg，请手动放入：
    echo        %FFMPEG_DIR%\ffmpeg.exe 与 ffprobe.exe
) else (
    set "FFMPEG_STATUS=已下载便携 FFmpeg"
)
:ffmpeg_done
echo.

rem ==================== [5/8] 无头浏览器 ====================
echo [5/8] 检查 Hyperframes 无头浏览器...
set "BROWSER_EXE="
set "FOUND_FILE="
if exist "%BROWSER_DIR%" call :find_in_tree "%BROWSER_DIR%" "chrome-headless-shell.exe"
if defined FOUND_FILE set "BROWSER_EXE=!FOUND_FILE!"
if defined BROWSER_EXE (
    set "BROWSER_STATUS=已就绪"
    echo [信息] 已存在：!BROWSER_EXE!
) else if defined OCV_SKIP_CHROME (
    set "BROWSER_STATUS=已跳过"
    echo [信息] 已按 OCV_SKIP_CHROME 跳过。
) else (
    call :ensure_browser
    if errorlevel 1 (
        set "BROWSER_STATUS=未安装"
        echo [警告] 无头浏览器未能安装，Hyperframes 渲染不可用（FFmpeg 直出仍可用）。
        echo        也可设置 HYPERFRAMES_BROWSER_PATH 指向本机 Chrome/Edge。
    ) else (
        set "BROWSER_STATUS=已下载 Chrome Headless Shell %OCV_CHROME_VERSION%"
    )
)
echo.

rem ==================== [6/8] Faster-Whisper 模型 ====================
echo [6/8] 检查 Faster-Whisper 字幕模型...
set "WHISPER_OK=1"
for %%F in (config.json model.bin tokenizer.json vocabulary.txt) do if not exist "%WHISPER_DIR%\%%F" set "WHISPER_OK="
if defined WHISPER_OK (
    set "WHISPER_STATUS=已就绪"
    echo [信息] 已存在：%WHISPER_DIR%
) else if defined OCV_SKIP_WHISPER (
    set "WHISPER_STATUS=已跳过"
    echo [信息] 已按 OCV_SKIP_WHISPER 跳过。
) else (
    call :ensure_whisper
    if errorlevel 1 (
        set "WHISPER_STATUS=未安装"
        echo [警告] Faster-Whisper Base 模型未能下载，字幕识别会退化为在线拉取模型。
    ) else (
        set "WHISPER_STATUS=已下载 faster-whisper-base"
    )
)
echo.

rem ==================== [7/8] IndexTTS-2.5 基础运行时 ====================
echo [7/8] 检查 IndexTTS-2.5 本地配音基础运行时...
if defined OCV_SKIP_INDEXTTS (
    set "INDEXTTS_STATUS=已跳过"
    echo [信息] 已按 OCV_SKIP_INDEXTTS 跳过。
    goto :indextts_done
)
if "!IS_PORTABLE!"=="1" (
    call :ensure_indextts
    if "!INDEXTTS_OK!"=="1" (
        if "!INDEXTTS_WEIGHTS!"=="1" (set "INDEXTTS_STATUS=本地配音可用") else (set "INDEXTTS_STATUS=仅基础就绪，本地配音未启用")
        echo [信息] IndexTTS-2.5 基础运行时就绪。
        rem 上游 IndexTTS-2.5 推理路径还需要 einops / munch / audiotools / wetext，
        rem 官方 deploy_indextts25.ps1 只在 python_packages 装了 4 个隔离包，这里补齐并修复旧环境
        "%PYEXE%" -I -c "import einops, munch, audiotools, wetext" >nul 2>nul
        if errorlevel 1 (
            echo [信息] IndexTTS-2.5 运行依赖不完整，正在补齐 requirements.txt ...
            if not defined PIP_INDEX set "PIP_INDEX=%OCV_PIP_INDEX%"
            "%PYEXE%" -m pip install -r "%ROOT_DIR%requirements.txt" -i "!PIP_INDEX!" --retries 5 --timeout 60
            if errorlevel 1 (
                echo [警告] IndexTTS-2.5 运行依赖补齐失败，本地配音可能不可用。
            ) else (
                echo [信息] IndexTTS-2.5 运行依赖已就绪。
            )
        )
        if not "!INDEXTTS_WEIGHTS!"=="1" (
            echo [信息] 未安装本地配音权重，本机跑 IndexTTS-2.5 模型不可用（约 10.2 GB）。
            echo        集群 GPU 配音、Qwen-TTS 云端配音、上传或已有配音、字幕识别与视频渲染
            echo        均不受影响。想启用本地配音：重跑本脚本、设 OCV_INSTALL_INDEXTTS_MODEL=1，
            echo        或在界面“声音设置”里按需安装。
        ) else (
            echo [信息] 本地配音权重已就绪。
        )
    ) else (
        set "INDEXTTS_STATUS=不完整"
        echo [警告] IndexTTS-2.5 基础运行时不完整，本地配音不可用（其它功能不受影响）。
    )
) else (
    set "INDEXTTS_STATUS=本机 Python 模式不适用"
    echo [信息] 本机 Python 模式跳过本地配音运行时：应用固定调用 runtime\python\python.exe。
    echo        需要本地 IndexTTS-2.5 配音时，删除 runtime\python 后设置 OCV_PYTHON_MODE=portable 重跑。
)
:indextts_done
echo.

rem ==================== [8/8] Node 依赖 ====================
if not exist "%NPM%" (
    echo [警告] 未找到便携 Node/npm：%NPM%
    echo [信息] 尝试使用 PATH 上的系统 npm...
    set "SYS_NPM="
    for /f "delims=" %%P in ('where npm.cmd 2^>nul') do if not defined SYS_NPM set "SYS_NPM=%%P"
    if not defined SYS_NPM (
        if defined OCV_SKIP_NPM (
            echo [警告] 未找到 npm，已按 OCV_SKIP_NPM 跳过。
        ) else (
            echo [致命错误] 未找到 npm。请安装 Node.js 22 及以上，或改用包含 runtime\node 的整合包。
            pause
            exit /b 1
        )
    ) else (
        set "NPM=!SYS_NPM!"
    )
)
echo [信息] npm   ：!NPM!

if defined OCV_SKIP_NPM (
    echo [8/8] 已按 OCV_SKIP_NPM 跳过 Node 依赖安装。
    set "NPM_STATUS=已跳过"
    goto :final
)

echo [8/8] 检查并补齐根目录 Node 依赖...
call "!NPM!" install --registry=https://registry.npmmirror.com
if errorlevel 1 (
    echo [致命错误] 根目录 Node 依赖安装失败，请检查网络。
    pause
    exit /b 1
)

echo.
echo [8/8] 检查并补齐前端 Node 依赖...
pushd "%ROOT_DIR%frontend"
call "!NPM!" install --registry=https://registry.npmmirror.com
set "INSTALL_RESULT=!ERRORLEVEL!"
popd
if not "!INSTALL_RESULT!"=="0" (
    echo [致命错误] 前端 Node 依赖安装失败，请检查网络。
    pause
    exit /b 1
)
set "NPM_STATUS=已安装"

:final
if exist "%BOOT_DIR%" rmdir /s /q "%BOOT_DIR%" >nul 2>nul
echo.
echo ============================================================
echo   环境检查完成
echo ============================================================
echo   Python 解释器：!PYEXE!
echo   解释器来源  ：!PYMODE!
echo   Python 依赖 ：!PIP_STATUS!
echo   便携 Node   ：!NODE_STATUS!
echo   FFmpeg      ：!FFMPEG_STATUS!
echo   无头浏览器  ：!BROWSER_STATUS!
echo   字幕模型    ：!WHISPER_STATUS!
echo   本地配音    ：!INDEXTTS_STATUS!
echo   Node 依赖   ：!NPM_STATUS!
echo.

if "!IS_PORTABLE!"=="1" (
    echo [校验] 运行便携环境自检 tools\portable_preflight.py ...
    "%PORTABLE_PY%" "%ROOT_DIR%tools\portable_preflight.py"
    if errorlevel 1 (
        echo.
        echo [警告] 便携环境自检未通过，start_windows.bat 会因此拒绝启动。
        echo        请按上面的提示补齐缺失项，或改用完整整合包。
        pause
        exit /b 1
    )
    echo.
    echo 下一步：双击 start_windows.bat 启动，浏览器打开 http://127.0.0.1:5173
) else (
    echo 下一步：本机 Python 模式，请依次执行：
    echo   "!PYEXE!" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8010
    echo   npm --prefix frontend run dev
    echo 注意：该模式不生成 runtime\node 与 runtime\hyperframes 等便携组件，
    echo       start_windows.bat 不适用，本地 IndexTTS-2.5 配音也不可用。
)
echo.
pause
exit /b 0

rem ==================== 子过程 ====================

rem 用 %CAND% 作为候选解释器进行探测；合格则设置 PROBED_* 与 ACCEPT
:probe_current
set "PROBED_EXE="
set "PROBED_VER="
set "PROBED_BITS="
set "PROBED_VENV="
set "ACCEPT="
if not defined CAND exit /b 0
del /q "%PROBE_OUT%" >nul 2>nul
%CAND% -E -s -c "%PROBE_CODE%" > "%PROBE_OUT%" 2>nul
if errorlevel 1 exit /b 0
if not exist "%PROBE_OUT%" exit /b 0
<"%PROBE_OUT%" (set /p PROBED_EXE=&set /p PROBED_VER=&set /p PROBED_BITS=&set /p PROBED_VENV=)
if not defined PROBED_EXE exit /b 0
if not defined PROBED_VER exit /b 0
if not "!PROBED_BITS!"=="64" exit /b 0
if !PROBED_VER! LSS 310 exit /b 0
if !PROBED_VER! GTR 312 exit /b 0
set "ACCEPT=1"
exit /b 0

rem 依次尝试多个下载源：%1=目标文件 %2=最小字节数 %3=URL 列表（空格分隔）
:fetch
set "FETCH_FILE=%~1"
set "FETCH_MIN=%~2"
set "FETCH_OK="
if not exist "%~dp1" mkdir "%~dp1"
if exist "%FETCH_FILE%" del /q "%FETCH_FILE%" >nul 2>nul
for %%U in (%~3) do (
    if not defined FETCH_OK (
        echo       [下载] %~nx1
        echo              源：%%U
        curl.exe -L -sS -# --fail --retry 2 --connect-timeout 15 --speed-limit 10240 --speed-time 30 -o "%FETCH_FILE%" "%%U"
        if exist "%FETCH_FILE%" for %%F in ("%FETCH_FILE%") do if %%~zF GEQ %FETCH_MIN% set "FETCH_OK=1"
        if defined FETCH_OK (
            for %%F in ("%FETCH_FILE%") do set /a "FETCH_MB=%%~zF/1048576"
            echo              [完成] 已下载 !FETCH_MB! MB
        ) else (
            echo              [失败] 该下载源不可用，换下一个源重试。
            if exist "%FETCH_FILE%" del /q "%FETCH_FILE%" >nul 2>nul
        )
    )
)
if defined FETCH_OK (exit /b 0) else (exit /b 1)

rem 实测单个 URL 的下载速度：%1=URL %2=探测字节数，结果写入 PROBE_SPEED（B/s）
:probe_speed
set "PROBE_SPEED=0"
set "PROBE_RAW="
for /f "delims=" %%S in ('curl.exe -sSL -m 15 -o NUL -r 0-%~2 -w "%%{speed_download}" "%~1" 2^>nul') do set "PROBE_RAW=%%S"
if not defined PROBE_RAW exit /b 1
for /f "tokens=1 delims=." %%A in ("!PROBE_RAW!") do set "PROBE_SPEED=%%A"
if not defined PROBE_SPEED set "PROBE_SPEED=0"
exit /b 0

rem 逐个实测国内 PyPI 镜像速度，选最快的写入 PIP_INDEX
:pick_pypi_index
set "PIP_INDEX=%OCV_PIP_INDEX%"
if defined OCV_SKIP_INDEX_PROBE exit /b 0
set "PIP_BEST=0"
set "PYPI_CANDIDATES=https://mirrors.aliyun.com/pypi/simple/ https://pypi.tuna.tsinghua.edu.cn/simple/ https://mirrors.cloud.tencent.com/pypi/simple/ https://mirrors.ustc.edu.cn/pypi/simple/ https://mirrors.huaweicloud.com/repository/pypi/simple/ https://mirror.nju.edu.cn/pypi/web/simple/"
echo [信息] 正在实测 PyPI 镜像速度（各约 3 MB 探测）...
for %%M in (%PYPI_CANDIDATES%) do (
    call :probe_speed "%%Mpip/" 3000000
    echo       %%M → !PROBE_SPEED! B/s
    if !PROBE_SPEED! GTR !PIP_BEST! (
        set "PIP_BEST=!PROBE_SPEED!"
        set "PIP_INDEX=%%M"
    )
)
if !PIP_BEST! LEQ 0 set "PIP_INDEX=%OCV_PIP_INDEX%"
exit /b 0

rem 实测 cu128 轮子源速度，选最快的写入 TORCH_LINKS
rem 取 torch 轮子源：PEP 503 索引源与扁平轮子目录（--find-links）互为备份
:pick_torch_sources
set "TORCH_INDEX="
set "TORCH_LINKS="
set "TORCH_ARGS="
echo [信息] 正在检查 torch 轮子源可用性...
"%PYEXE%" -m pip index versions torch --index-url %OCV_TORCH_INDEX% --timeout 10 --retries 0 --disable-pip-version-check >nul 2>nul
if not errorlevel 1 set "TORCH_INDEX=%OCV_TORCH_INDEX%"
if not defined TORCH_INDEX (
    "%PYEXE%" -m pip index versions torch --index-url https://download.pytorch.org/whl/cu128 --timeout 10 --retries 0 --disable-pip-version-check >nul 2>nul
    if not errorlevel 1 set "TORCH_INDEX=https://download.pytorch.org/whl/cu128"
)
curl.exe -sSL -m 15 -o NUL --fail "%OCV_TORCH_WHEELS%" >nul 2>nul
if not errorlevel 1 set "TORCH_LINKS=%OCV_TORCH_WHEELS%"
if defined TORCH_INDEX set "TORCH_ARGS=!TORCH_ARGS! --index-url !TORCH_INDEX!"
if defined TORCH_LINKS set "TORCH_ARGS=!TORCH_ARGS! --find-links !TORCH_LINKS!"
if not defined TORCH_ARGS echo [警告] 未能确认任何 torch 轮子源，将直接回退官方索引。
exit /b 0

rem 先单独装 torch/torchaudio：只走上面确认可用的国内源，避免 3.5 GB 被国际站拖慢
:install_torch
set "TORCH_PIN="
set "TORCHAUDIO_PIN="
for /f "tokens=1 delims=; " %%L in ('findstr /b /c:"torch==" "%ROOT_DIR%requirements.txt"') do set "TORCH_PIN=%%L"
for /f "tokens=1 delims=; " %%L in ('findstr /b /c:"torchaudio==" "%ROOT_DIR%requirements.txt"') do set "TORCHAUDIO_PIN=%%L"
if not defined TORCH_PIN exit /b 0
if not defined PIP_INDEX set "PIP_INDEX=%OCV_PIP_INDEX%"
"%PYEXE%" -I -c "import torch" >nul 2>nul
if not errorlevel 1 (
    echo [信息] torch 已安装，跳过。
    exit /b 0
)
call :pick_torch_sources
echo [信息] torch 索引源：!TORCH_INDEX!
echo [信息] torch 扁平源：!TORCH_LINKS!
if not defined TORCH_ARGS (
    echo [警告] 无可用国内源，直接使用官方索引（较慢）。
    "%PYEXE%" -m pip install --disable-pip-version-check --index-url https://download.pytorch.org/whl/cu128 --extra-index-url "!PIP_INDEX!" --retries 5 --timeout 120 "!TORCH_PIN!" "!TORCHAUDIO_PIN!"
    exit /b 0
)
"%PYEXE%" -m pip install --disable-pip-version-check !TORCH_ARGS! --extra-index-url "!PIP_INDEX!" --retries 5 --timeout 120 "!TORCH_PIN!" "!TORCHAUDIO_PIN!"
if errorlevel 1 (
    echo [警告] 国内源安装 torch 失败，回退官方索引重试...
    "%PYEXE%" -m pip install --disable-pip-version-check --index-url https://download.pytorch.org/whl/cu128 --extra-index-url "!PIP_INDEX!" --retries 5 --timeout 120 "!TORCH_PIN!" "!TORCHAUDIO_PIN!"
)
exit /b 0

rem 在目录树中查找真实存在的文件：%1=根目录 %2=文件名，结果写入 FOUND_FILE
:find_in_tree
set "FOUND_FILE="
if not exist "%~1" exit /b 1
for /f "delims=" %%F in ('dir /s /b "%~1\%~2" 2^>nul') do if not defined FOUND_FILE set "FOUND_FILE=%%F"
if defined FOUND_FILE (exit /b 0) else (exit /b 1)

rem 解压 tar.gz：%1=压缩包 %2=目标目录
:extract_targz
if exist "%~2" rmdir /s /q "%~2"
mkdir "%~2"
tar.exe -xzf "%~1" -C "%~2"
if errorlevel 1 exit /b 1
if not exist "%~2" exit /b 1
exit /b 0

rem 解压 zip：%1=压缩包 %2=目标目录
:extract_zip
if exist "%~2" rmdir /s /q "%~2"
mkdir "%~2"
tar.exe -xf "%~1" -C "%~2"
if errorlevel 1 exit /b 1
if not exist "%~2" exit /b 1
exit /b 0

rem 从国内镜像下载便携 Python 并安装到 runtime\python
:download_portable_python
echo [信息] 正在获取便携 Python %OCV_PYTHON_VERSION%（约 43 MB）...
if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    echo [致命错误] 自动下载仅支持 64 位 Windows，当前架构：%PROCESSOR_ARCHITECTURE%
    exit /b 1
)
where curl.exe >nul 2>nul
if errorlevel 1 (
    echo [致命错误] 缺少 Windows 自带的 curl.exe（Windows 10 1803 及以上提供）。
    exit /b 1
)
where tar.exe >nul 2>nul
if errorlevel 1 (
    echo [致命错误] 缺少 Windows 自带的 tar.exe（Windows 10 1803 及以上提供）。
    exit /b 1
)
set "ASSET=cpython-%OCV_PYTHON_VERSION%+%OCV_PYTHON_BUILD%-x86_64-pc-windows-msvc-install_only.tar.gz"
set "SOURCES=https://registry.npmmirror.com/-/binary/python-build-standalone/%OCV_PYTHON_BUILD%/%ASSET% https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download/%OCV_PYTHON_BUILD%/%ASSET% https://ghproxy.net/https://github.com/astral-sh/python-build-standalone/releases/download/%OCV_PYTHON_BUILD%/%ASSET% https://github.com/astral-sh/python-build-standalone/releases/download/%OCV_PYTHON_BUILD%/%ASSET%"
if defined OCV_PYTHON_URL set "SOURCES=%OCV_PYTHON_URL%%ASSET%"
call :fetch "%BOOT_DIR%\python.tar.gz" 20000000 "%SOURCES%"
if errorlevel 1 (
    echo [致命错误] 便携 Python 下载失败，所有镜像均不可用。
    exit /b 1
)
echo       正在解压到 runtime\python ...
call :extract_targz "%BOOT_DIR%\python.tar.gz" "%BOOT_DIR%\python_stage"
if errorlevel 1 (
    echo [致命错误] 便携 Python 解压失败。
    exit /b 1
)
if not exist "%BOOT_DIR%\python_stage\python\python.exe" (
    echo [致命错误] 便携 Python 压缩包结构异常，缺少 python\python.exe。
    exit /b 1
)
if exist "%ROOT_DIR%runtime\python" rmdir /s /q "%ROOT_DIR%runtime\python"
move "%BOOT_DIR%\python_stage\python" "%ROOT_DIR%runtime\python" >nul
if not exist "%PORTABLE_PY%" (
    echo [致命错误] 便携 Python 安装失败：%PORTABLE_PY%
    exit /b 1
)
"%PORTABLE_PY%" -m pip --version >nul 2>nul
if errorlevel 1 "%PORTABLE_PY%" -m ensurepip --upgrade >nul 2>nul
del /q "%BOOT_DIR%\python.tar.gz" >nul 2>nul
rmdir /s /q "%BOOT_DIR%\python_stage" >nul 2>nul
set "CAND="%PORTABLE_PY%""
call :probe_current
if not defined ACCEPT (
    echo [致命错误] 下载的便携 Python 自检失败。
    exit /b 1
)
set "PYEXE=!PROBED_EXE!"
set "PYMODE=自动下载便携 Python !OCV_PYTHON_VERSION!"
set "IS_PORTABLE=1"
echo [信息] 便携 Python 安装完成。
exit /b 0

rem 便携 Node.js 安装到 runtime\node
:ensure_node
echo [信息] 正在获取便携 Node.js %OCV_NODE_VERSION%（约 35 MB）...
set "ASSET=node-v%OCV_NODE_VERSION%-win-x64.zip"
set "SOURCES=https://registry.npmmirror.com/-/binary/node/v%OCV_NODE_VERSION%/%ASSET% https://mirror.nju.edu.cn/nodejs-release/v%OCV_NODE_VERSION%/%ASSET% https://nodejs.org/dist/v%OCV_NODE_VERSION%/%ASSET%"
call :fetch "%BOOT_DIR%\%ASSET%" 25000000 "%SOURCES%"
if errorlevel 1 (
    echo [警告] 便携 Node 下载失败，所有镜像均不可用。
    exit /b 1
)
call :extract_zip "%BOOT_DIR%\%ASSET%" "%BOOT_DIR%\node_stage"
if errorlevel 1 (
    echo [警告] 便携 Node 解压失败。
    exit /b 1
)
call :find_in_tree "%BOOT_DIR%\node_stage" "node.exe"
if not defined FOUND_FILE (
    echo [警告] 便携 Node 压缩包结构异常，未找到 node.exe。
    exit /b 1
)
set "NODE_SRC=!FOUND_FILE!"
set "NODE_TOP="
for /d %%D in ("%BOOT_DIR%\node_stage\*") do if not defined NODE_TOP set "NODE_TOP=%%~fD"
if exist "%NODE_DIR%" rmdir /s /q "%NODE_DIR%"
if defined NODE_TOP (
    move "!NODE_TOP!" "%NODE_DIR%" >nul
) else (
    for %%F in ("!NODE_SRC!") do set "NODE_SRC=%%~dpF"
    if not exist "%NODE_DIR%" mkdir "%NODE_DIR%"
    xcopy "!NODE_SRC!*" "%NODE_DIR%\" /E /I /Y /Q /H >nul
)
del /q "%BOOT_DIR%\%ASSET%" >nul 2>nul
rmdir /s /q "%BOOT_DIR%\node_stage" >nul 2>nul
if not exist "%NPM%" (
    echo [警告] 便携 Node 安装不完整，缺少 npm.cmd。
    exit /b 1
)
echo [信息] 便携 Node 安装完成：%NODE_DIR%
exit /b 0

rem 便携 FFmpeg 安装到 tools\ffmpeg\bin
:ensure_ffmpeg
echo [信息] 正在获取便携 FFmpeg（约 80-160 MB）...
set "SOURCES=https://ghfast.top/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip https://ghproxy.net/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
if defined OCV_FFMPEG_URL set "SOURCES=%OCV_FFMPEG_URL%"
call :fetch "%BOOT_DIR%\ffmpeg.zip" 30000000 "%SOURCES%"
if errorlevel 1 (
    echo [警告] 便携 FFmpeg 下载失败，所有镜像均不可用。
    exit /b 1
)
call :extract_zip "%BOOT_DIR%\ffmpeg.zip" "%BOOT_DIR%\ffmpeg_stage"
if errorlevel 1 (
    echo [警告] 便携 FFmpeg 解压失败。
    exit /b 1
)
call :find_in_tree "%BOOT_DIR%\ffmpeg_stage" "ffmpeg.exe"
if not defined FOUND_FILE (
    echo [警告] 便携 FFmpeg 压缩包结构异常，未找到 ffmpeg.exe。
    exit /b 1
)
if not exist "%FFMPEG_DIR%" mkdir "%FFMPEG_DIR%"
copy /Y "!FOUND_FILE!" "%FFMPEG_DIR%\ffmpeg.exe" >nul
call :find_in_tree "%BOOT_DIR%\ffmpeg_stage" "ffprobe.exe"
if defined FOUND_FILE copy /Y "!FOUND_FILE!" "%FFMPEG_DIR%\ffprobe.exe" >nul 2>nul
del /q "%BOOT_DIR%\ffmpeg.zip" >nul 2>nul
rmdir /s /q "%BOOT_DIR%\ffmpeg_stage" >nul 2>nul
if not exist "%FFMPEG_DIR%\ffmpeg.exe" (
    echo [警告] 便携 FFmpeg 安装失败。
    exit /b 1
)
echo [信息] 便携 FFmpeg 安装完成：%FFMPEG_DIR%
exit /b 0

rem Chrome Headless Shell 安装到 Hyperframes 缓存目录
:ensure_browser
echo [信息] 正在获取 Chrome Headless Shell %OCV_CHROME_VERSION%（约 106 MB）...
set "ASSET=chrome-headless-shell-win64.zip"
set "SOURCES=https://registry.npmmirror.com/-/binary/chrome-for-testing/%OCV_CHROME_VERSION%/win64/%ASSET% https://storage.googleapis.com/chrome-for-testing-public/%OCV_CHROME_VERSION%/win64/%ASSET%"
call :fetch "%BOOT_DIR%\%ASSET%" 50000000 "%SOURCES%"
if errorlevel 1 (
    echo [警告] 无头浏览器下载失败，所有镜像均不可用。
    exit /b 1
)
call :extract_zip "%BOOT_DIR%\%ASSET%" "%BROWSER_DIR%\%OCV_CHROME_VERSION%"
if errorlevel 1 (
    echo [警告] 无头浏览器解压失败。
    exit /b 1
)
del /q "%BOOT_DIR%\%ASSET%" >nul 2>nul
set "BROWSER_EXE="
call :find_in_tree "%BROWSER_DIR%" "chrome-headless-shell.exe"
if defined FOUND_FILE set "BROWSER_EXE=!FOUND_FILE!"
if not defined BROWSER_EXE (
    echo [警告] 无头浏览器安装不完整，未找到 chrome-headless-shell.exe。
    exit /b 1
)
echo [信息] 无头浏览器安装完成。
exit /b 0

rem Faster-Whisper Base 模型安装到 tools\whisper_models\faster-whisper-base
:ensure_whisper
echo [信息] 正在获取 Faster-Whisper Base 模型（约 145 MB）...
if not exist "%WHISPER_DIR%" mkdir "%WHISPER_DIR%"
for %%F in (config.json model.bin tokenizer.json vocabulary.txt) do (
    if not exist "%WHISPER_DIR%\%%F" (
        set "MIN=100000"
        if /i "%%F"=="config.json" set "MIN=500"
        set "SOURCES=%OCV_HF_ENDPOINT%/Systran/faster-whisper-base/resolve/main/%%F https://www.modelscope.cn/models/pengzhendong/faster-whisper-base/resolve/master/%%F https://huggingface.co/Systran/faster-whisper-base/resolve/main/%%F"
        call :fetch "%WHISPER_DIR%\%%F" !MIN! "!SOURCES!"
        if errorlevel 1 (
            echo [警告] %%F 下载失败。
            exit /b 1
        )
    )
)
for %%F in (config.json model.bin tokenizer.json vocabulary.txt) do if not exist "%WHISPER_DIR%\%%F" exit /b 1
echo [信息] 字幕模型安装完成：%WHISPER_DIR%
exit /b 0

rem IndexTTS-2.5 基础运行时：源码 + 示例音色 + 隔离依赖（+ 可选权重）
:ensure_indextts
set "INDEXTTS_OK="
set "INDEXTTS_WEIGHTS="
if not exist "%INDEXTTS_DIR%\indextts\infer_v2_5.py" (
    echo [信息] 正在获取 IndexTTS-2.5 源码（约 36 MB）...
    set "SOURCES=https://ghfast.top/https://github.com/index-tts/index-tts/archive/refs/heads/main.tar.gz https://ghproxy.net/https://github.com/index-tts/index-tts/archive/refs/heads/main.tar.gz https://codeload.github.com/index-tts/index-tts/tar.gz/refs/heads/main https://github.com/index-tts/index-tts/archive/refs/heads/main.tar.gz"
    if defined OCV_INDEXTTS_URL set "SOURCES=%OCV_INDEXTTS_URL%"
    call :fetch "%BOOT_DIR%\indextts.tar.gz" 10000000 "!SOURCES!"
    if errorlevel 1 (
        echo [警告] IndexTTS-2.5 源码下载失败。
        exit /b 0
    )
    call :extract_targz "%BOOT_DIR%\indextts.tar.gz" "%BOOT_DIR%\indextts_stage"
    if errorlevel 1 (
        echo [警告] IndexTTS-2.5 源码解压失败。
        exit /b 0
    )
    set "IT_SRC="
    for /d %%D in ("%BOOT_DIR%\indextts_stage\*") do if not defined IT_SRC set "IT_SRC=%%~fD"
    if not defined IT_SRC (
        echo [警告] IndexTTS-2.5 压缩包结构异常。
        exit /b 0
    )
    if not exist "%INDEXTTS_DIR%" mkdir "%INDEXTTS_DIR%"
    xcopy "!IT_SRC!\*" "%INDEXTTS_DIR%\" /E /I /Y /Q /H >nul
    del /q "%BOOT_DIR%\indextts.tar.gz" >nul 2>nul
    rmdir /s /q "%BOOT_DIR%\indextts_stage" >nul 2>nul
)
if not exist "%INDEXTTS_DIR%\indextts\infer_v2_5.py" (
    echo [警告] IndexTTS-2.5 源码不完整。
    exit /b 0
)

rem 示例音色：应用默认音色 voice_05.wav 等，取自官方 IndexTTS-2-Demo 空间
if not exist "%INDEXTTS_DIR%\examples" mkdir "%INDEXTTS_DIR%\examples"
set "VOICE_MISSING="
for %%V in (voice_01.wav voice_02.wav voice_03.wav voice_04.wav voice_05.wav voice_06.wav voice_07.wav voice_08.wav voice_09.wav voice_11.wav voice_12.wav emo_hate.wav emo_sad.wav) do if not exist "%INDEXTTS_DIR%\examples\%%V" set "VOICE_MISSING=1"
if defined VOICE_MISSING (
    echo [信息] 正在获取 IndexTTS-2.5 示例音色（13 个文件，约 4 MB）...
    for %%V in (voice_01.wav voice_02.wav voice_03.wav voice_04.wav voice_05.wav voice_06.wav voice_07.wav voice_08.wav voice_09.wav voice_11.wav voice_12.wav emo_hate.wav emo_sad.wav) do (
        if not exist "%INDEXTTS_DIR%\examples\%%V" (
            set "SOURCES=%OCV_HF_ENDPOINT%/spaces/IndexTeam/IndexTTS-2-Demo/resolve/main/examples/%%V https://huggingface.co/spaces/IndexTeam/IndexTTS-2-Demo/resolve/main/examples/%%V"
            call :fetch "%INDEXTTS_DIR%\examples\%%V" 10000 "!SOURCES!"
            if errorlevel 1 echo [警告] 示例音色 %%V 下载失败，可在界面改用自备参考音频。
        )
    )
)

rem 隔离依赖包：复用项目官方装配脚本
set "IT_PACKAGES_OK=1"
for %%P in (whisper tiktoken) do if not exist "%INDEXTTS_DIR%\python_packages\%%P" set "IT_PACKAGES_OK="
if not defined IT_PACKAGES_OK (
    echo [信息] 正在装配 IndexTTS-2.5 隔离依赖包（复用 tools\deploy_indextts25.ps1）...
    if not defined PIP_INDEX set "PIP_INDEX=%OCV_PIP_INDEX%"
    set "PIP_INDEX_URL=!PIP_INDEX!"
    powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT_DIR%tools\deploy_indextts25.ps1" -ProjectRoot "%ROOT_DIR:~0,-1%" -SkipModelDownload
    set "PS_RESULT=!ERRORLEVEL!"
    set "PIP_INDEX_URL="
    if not "!PS_RESULT!"=="0" (
        echo [警告] 装配脚本未完整结束（其收尾的示例音色/辅助模型步骤需要 requests
        echo        且直连 huggingface.co，国内网络常失败）。示例音色本脚本已从国内镜像
        echo        备齐，因此下面按必需文件是否齐全判定结果。也可手动重跑：
        echo        powershell -ExecutionPolicy Bypass -File tools\deploy_indextts25.ps1
    )
)

rem 可选：本地配音权重（约 10.2 GB）
if exist "%INDEXTTS_DIR%\checkpoints\gpt.pth" set "INDEXTTS_WEIGHTS=1"
if not defined INDEXTTS_WEIGHTS (
    set "DOWNLOAD_WEIGHTS="
    if /i "%OCV_INSTALL_INDEXTTS_MODEL%"=="1" set "DOWNLOAD_WEIGHTS=1"
    if not defined OCV_INSTALL_INDEXTTS_MODEL (
        where nvidia-smi >nul 2>nul
        if not errorlevel 1 (
            set "GPU_INFO="
            for /f "tokens=1,2 delims=," %%A in ('nvidia-smi --query-gpu^=name^,memory.total --format^=csv^,noheader^,nounits 2^>nul') do if not defined GPU_INFO (
                set "GPU_MEM=%%B"
                for /f "tokens=* delims= " %%M in ("!GPU_MEM!") do set "GPU_MEM=%%M"
                set "GPU_INFO=%%A，共 !GPU_MEM! MiB"
            )
            echo.
            echo [说明] “本地配音”指用本机显卡跑 IndexTTS-2.5 模型，需要额外下载约 10.2 GB 权重。
            echo        只有想在本机跑这个模型时才需要；集群 GPU 配音、Qwen-TTS 云端配音、
            echo        上传或已有配音、字幕识别、生图与视频渲染都不需要它。
            echo        建议显存：可用 8 GB 以上可单并发运行，12 GB 以上更从容
            echo        （项目自检在“并行数大于 1 且可用显存低于 9 GB”时会给出告警）。
            echo        以后想启用也可以：重跑本脚本、设 OCV_INSTALL_INDEXTTS_MODEL=1，
            echo        或在界面“声音设置”里按需安装。
            if defined GPU_INFO echo [信息] 检测到显卡：!GPU_INFO!
            choice /c YN /n /t 30 /d N /m "是否在本机运行 IndexTTS-2.5 本地配音（现在下载 10.2 GB 权重）？[Y/N] " 2>nul
            if not errorlevel 2 set "DOWNLOAD_WEIGHTS=1"
        ) else (
            echo [信息] 未检测到 NVIDIA 显卡，跳过本地配音权重：本地 IndexTTS-2.5 需要 NVIDIA GPU。
            echo        集群 GPU 配音、Qwen-TTS 云端配音、字幕识别与视频渲染都不受影响。
        )
    )
    if defined DOWNLOAD_WEIGHTS (
        echo [信息] 正在下载 IndexTTS-2.5 权重（约 10.2 GB，支持断点续传）...
        if not defined PIP_INDEX set "PIP_INDEX=%OCV_PIP_INDEX%"
        set "PIP_INDEX_URL=!PIP_INDEX!"
        powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT_DIR%tools\deploy_indextts25.ps1" -ProjectRoot "%ROOT_DIR:~0,-1%"
        set "PS_RESULT=!ERRORLEVEL!"
        set "PIP_INDEX_URL="
        if not "!PS_RESULT!"=="0" (
            echo [警告] 权重下载未完成，可在界面“声音设置”中继续安装。
        ) else (
            set "INDEXTTS_WEIGHTS=1"
        )
    )
)

set "IT_FINAL_OK=1"
for %%P in ("indextts\infer_v2_5.py" "python_packages\whisper" "python_packages\tiktoken" "examples\voice_05.wav") do if not exist "%INDEXTTS_DIR%\%%~P" set "IT_FINAL_OK="
if defined IT_FINAL_OK set "INDEXTTS_OK=1"
exit /b 0
