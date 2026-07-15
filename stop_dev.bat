@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=[IO.Path]::GetFullPath('%~dp0'); Get-CimInstance Win32_Process | Where-Object { ($_.Name -in @('python.exe','node.exe')) -and $_.CommandLine -like ('*' + $root + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo Local services stopped.
pause
