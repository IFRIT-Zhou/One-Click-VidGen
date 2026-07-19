@echo off
setlocal
cd /d "%~dp0"
call "%~dp0start_windows.bat"
exit /b %errorlevel%
