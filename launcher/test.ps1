param(
    [switch]$Lifecycle,
    [switch]$SafeUpdate,
    [switch]$UpdateCheck
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
        if ($Lifecycle) { & $testExe --lifecycle }
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
