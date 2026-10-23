$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root 'runtime\temp\launcher_repair_20261022'
$kit = Join-Path $out 'OCV_Launcher_Repair'
New-Item -ItemType Directory -Path $kit -Force | Out-Null
$files = @('start_windows.bat', 'tools/launcher_service_probe.py', 'launcher/safe_update_helper.ps1')
$manifest = @{}
foreach ($name in $files) {
    $dest = Join-Path $kit ('payload/' + $name)
    New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $root $name) -Destination $dest -Force
    $manifest[$name] = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash.ToLowerInvariant()
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $kit 'manifest.json'), ($manifest | ConvertTo-Json), $utf8)
Copy-Item -LiteralPath (Join-Path $root 'tools/launcher_repair_kit.py') -Destination (Join-Path $kit 'repair.py') -Force
Copy-Item -LiteralPath (Join-Path $root 'docs/launcher_repair_20261022.txt') -Destination (Join-Path $kit '使用说明.txt') -Force
foreach ($mode in @('install', 'diagnose', 'restore')) {
    $content = "@echo off`r`nchcp 65001 >nul`r`nset PYTHONUTF8=1`r`ncd /d `"%~dp0`"`r`nif not exist `"..\runtime\python\python.exe`" (`r`n echo Please place this folder inside the OCV directory.`r`n pause`r`n exit /b 1`r`n)`r`n`"..\runtime\python\python.exe`" -I repair.py $mode`r`npause`r`n"
    [IO.File]::WriteAllText((Join-Path $kit ($mode + '.bat')), $content, [Text.Encoding]::ASCII)
}
$zip = Join-Path $out 'OCV_Launcher_Repair_20261022.zip'
Compress-Archive -LiteralPath $kit -DestinationPath $zip -Force
Get-FileHash -LiteralPath $zip -Algorithm SHA256

