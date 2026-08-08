$ErrorActionPreference = 'Stop'
$launcherDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $launcherDir
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$snapshotExe = Join-Path $launcherDir 'bin\OCV_Launcher.UiSnapshot.exe'
$snapshotPng = Join-Path $launcherDir 'ui-preview.png'
$logo = Join-Path $projectRoot 'frontend\public\one-click-vidgen-logo.png'

$arguments = @(
    '/nologo',
    '/target:exe',
    "/out:$snapshotExe",
    "/resource:$logo,OcvLauncher.Logo.png",
    "/reference:$(Join-Path $framework 'System.dll')",
    "/reference:$(Join-Path $framework 'System.Core.dll')",
    "/reference:$(Join-Path $framework 'System.Drawing.dll')",
    "/reference:$(Join-Path $framework 'System.Windows.Forms.dll')",
    (Join-Path $launcherDir 'src\LauncherRuntime.cs'),
    (Join-Path $launcherDir 'src\MainForm.cs'),
    (Join-Path $launcherDir 'tests\UiSnapshot.cs')
)

try {
    & $compiler $arguments
    if ($LASTEXITCODE -ne 0) { throw "UI snapshot compilation failed: $LASTEXITCODE" }
    Push-Location $projectRoot
    try { & $snapshotExe $snapshotPng } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "UI snapshot failed: $LASTEXITCODE" }
    Write-Host "Snapshot: $snapshotPng"
}
finally {
    if (Test-Path -LiteralPath $snapshotExe) { Remove-Item -LiteralPath $snapshotExe -Force }
}
