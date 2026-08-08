param(
    [switch]$SkipRootCopy
)

$ErrorActionPreference = 'Stop'
$launcherDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $launcherDir
$sourceDir = Join-Path $launcherDir 'src'
$outputDir = Join-Path $launcherDir 'bin'
$outputExe = Join-Path $outputDir 'OCV_Launcher.exe'
$rootExe = Join-Path $projectRoot 'OCV_Launcher.exe'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$logo = Join-Path $projectRoot 'frontend\public\one-click-vidgen-logo.png'
$manifest = Join-Path $launcherDir 'OcvLauncher.manifest'

if (-not (Test-Path -LiteralPath $compiler)) {
    throw "Windows .NET Framework C# compiler was not found: $compiler"
}
if (-not (Test-Path -LiteralPath $logo)) {
    throw "OCV logo was not found: $logo"
}

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

$sources = Get-ChildItem -LiteralPath $sourceDir -Filter '*.cs' -File | ForEach-Object { $_.FullName }
$arguments = @(
    '/nologo',
    '/target:winexe',
    '/optimize+',
    '/platform:anycpu',
    "/out:$outputExe",
    "/win32manifest:$manifest",
    "/resource:$logo,OcvLauncher.Logo.png",
    "/reference:$(Join-Path $framework 'System.dll')",
    "/reference:$(Join-Path $framework 'System.Core.dll')",
    "/reference:$(Join-Path $framework 'System.Drawing.dll')",
    "/reference:$(Join-Path $framework 'System.Windows.Forms.dll')"
) + $sources

& $compiler $arguments
if ($LASTEXITCODE -ne 0) {
    throw "OCV Launcher compilation failed with exit code $LASTEXITCODE"
}

if (-not $SkipRootCopy) {
    Copy-Item -LiteralPath $outputExe -Destination $rootExe -Force
}

$built = Get-Item -LiteralPath $outputExe
Write-Host "Built: $($built.FullName) ($([Math]::Round($built.Length / 1KB, 1)) KiB)"
if (-not $SkipRootCopy) { Write-Host "Copied: $rootExe" }
