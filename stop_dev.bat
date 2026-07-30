@echo off
setlocal EnableDelayedExpansion

set "STOP_FAILED=0"
for %%R in (8010 5173) do (
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%%R .*LISTENING"') do (
        taskkill /PID %%P /T /F >nul 2>nul
        if errorlevel 1 set "STOP_FAILED=1"
    )
)

if "!STOP_FAILED!"=="1" (
    echo [ERROR] One or more services could not be stopped. Please run this file as administrator once.
) else (
    echo Local services stopped.
)
pause
