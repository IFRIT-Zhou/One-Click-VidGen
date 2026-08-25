param(
    [switch]$Lifecycle,
    [switch]$SafeUpdate,
    [switch]$UpdateCheck,
    [switch]$PortableUpdateCheck
)

$ErrorActionPreference = 'Stop'
$launcherDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $launcherDir
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$testExe = Join-Path $launcherDir 'bin\OCV_Launcher.RuntimeSmoke.exe'

New-Item -ItemType Directory -Path (Split-Path -Parent $testExe) -Force | Out-Null

$arguments = @(
    '/nologo',
    '/target:exe',
    '/optimize+',
    "/out:$testExe",
    "/reference:$(Join-Path $framework 'System.dll')",
    "/reference:$(Join-Path $framework 'System.Core.dll')",
    (Join-Path $launcherDir 'src\LauncherRuntime.cs'),
    (Join-Path $launcherDir 'tests\RuntimeSmoke.cs')
)

try {
    & $compiler $arguments
    if ($LASTEXITCODE -ne 0) { throw "Runtime smoke-test compilation failed: $LASTEXITCODE" }
    Push-Location $projectRoot
    try {
        if ($PortableUpdateCheck) {
            $portableFixture = Join-Path $projectRoot 'runtime\temp\launcher_portable_channel_smoke'
            if (Test-Path -LiteralPath $portableFixture) { Remove-Item -LiteralPath $portableFixture -Recurse -Force }
            New-Item -ItemType Directory -Path (Join-Path $portableFixture 'launcher') -Force | Out-Null
            Copy-Item -LiteralPath $testExe -Destination (Join-Path $portableFixture 'OCV_Launcher.RuntimeSmoke.exe') -Force
            Copy-Item -LiteralPath (Join-Path $launcherDir 'update-channel.json') -Destination (Join-Path $portableFixture 'launcher\update-channel.json') -Force
            Copy-Item -LiteralPath (Join-Path $launcherDir 'update-sources.json') -Destination (Join-Path $portableFixture 'launcher\update-sources.json') -Force
            Set-Content -LiteralPath (Join-Path $portableFixture 'start_windows.bat') -Value '@echo off' -Encoding ASCII
            try { & (Join-Path $portableFixture 'OCV_Launcher.RuntimeSmoke.exe') --portable-update-check }
            finally { Remove-Item -LiteralPath $portableFixture -Recurse -Force -ErrorAction SilentlyContinue }
        }
        elseif ($Lifecycle) { & $testExe --lifecycle }
        elseif ($UpdateCheck) { & $testExe --update-check }
        else { & $testExe }
    } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Runtime smoke test failed: $LASTEXITCODE" }
    if ($SafeUpdate) {
        & (Join-Path $launcherDir 'tests\SafeUpdateSmoke.ps1')
        if ($LASTEXITCODE -ne 0) { throw "Safe-update smoke test failed: $LASTEXITCODE" }
    }
}
finally {
    if (Test-Path -LiteralPath $testExe) { Remove-Item -LiteralPath $testExe -Force }
}
