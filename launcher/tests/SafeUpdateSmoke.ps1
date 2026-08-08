$ErrorActionPreference = 'Stop'
$launcherDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$helper = Join-Path $launcherDir 'safe_update_helper.ps1'
$projectRoot = Split-Path -Parent $launcherDir
$fixture = Join-Path $projectRoot 'runtime\temp\launcher_safe_update_smoke'
$fakeRoot = Join-Path $fixture 'project'
$sourceParent = Join-Path $fixture 'source'
$sourceRoot = Join-Path $sourceParent 'One-Click-VidGen-main'
$packageDir = Join-Path $fakeRoot 'runtime\temp\ocv-updates'
$package = Join-Path $packageDir 'update.zip'

try {
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
    New-Item -ItemType Directory -Path $fakeRoot, $sourceRoot, $packageDir -Force | Out-Null

    New-Item -ItemType Directory -Path (Join-Path $fakeRoot 'runtime'), (Join-Path $fakeRoot 'output'), (Join-Path $fakeRoot 'workspace') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $fakeRoot 'app.txt') -Value 'old-source' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $fakeRoot '.env') -Value 'SECRET=keep-me' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $fakeRoot 'runtime\model.bin') -Value 'model-keep' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $fakeRoot 'output\video.mp4') -Value 'output-keep' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $fakeRoot 'workspace\job.json') -Value 'workspace-keep' -Encoding UTF8

    New-Item -ItemType Directory -Path (Join-Path $sourceRoot 'launcher'), (Join-Path $sourceRoot 'runtime'), (Join-Path $sourceRoot 'output') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $sourceRoot 'start_windows.bat') -Value '@echo off' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $sourceRoot 'app.txt') -Value 'new-source' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $sourceRoot '.env') -Value 'SECRET=overwrite-attempt' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $sourceRoot 'runtime\model.bin') -Value 'model-overwrite-attempt' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $sourceRoot 'output\video.mp4') -Value 'output-overwrite-attempt' -Encoding UTF8
    @{
        release_id = 'smoke-release-1'
        release_order = 1
        display_version = 'Smoke Test'
        portable_overlay_safe = $true
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $sourceRoot 'launcher\update-channel.json') -Encoding UTF8

    Compress-Archive -LiteralPath $sourceRoot -DestinationPath $package -CompressionLevel Fastest
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper `
        -Mode portable `
        -ProjectRoot $fakeRoot `
        -LauncherPid 999999 `
        -ExpectedReleaseId 'smoke-release-1' `
        -PackagePath $package `
        -NoRestart
    if ($LASTEXITCODE -ne 0) { throw "Update helper exited with code $LASTEXITCODE" }

    $assertions = @(
        @{ Name = 'source updated'; Passed = ((Get-Content -LiteralPath (Join-Path $fakeRoot 'app.txt') -Raw).Trim() -eq 'new-source') },
        @{ Name = '.env protected'; Passed = ((Get-Content -LiteralPath (Join-Path $fakeRoot '.env') -Raw).Trim() -eq 'SECRET=keep-me') },
        @{ Name = 'runtime protected'; Passed = ((Get-Content -LiteralPath (Join-Path $fakeRoot 'runtime\model.bin') -Raw).Trim() -eq 'model-keep') },
        @{ Name = 'output protected'; Passed = ((Get-Content -LiteralPath (Join-Path $fakeRoot 'output\video.mp4') -Raw).Trim() -eq 'output-keep') },
        @{ Name = 'workspace protected'; Passed = ((Get-Content -LiteralPath (Join-Path $fakeRoot 'workspace\job.json') -Raw).Trim() -eq 'workspace-keep') },
        @{ Name = 'backup created'; Passed = [bool](Get-ChildItem -LiteralPath (Join-Path $fakeRoot 'Archives\launcher_updates') -Recurse -File -Filter 'app.txt' -ErrorAction SilentlyContinue) }
    )
    foreach ($assertion in $assertions) {
        if (-not $assertion.Passed) { throw "Assertion failed: $($assertion.Name)" }
        Write-Host "PASS $($assertion.Name)"
    }

    $failureRoot = Join-Path $fixture 'failure-project'
    $failureSourceParent = Join-Path $fixture 'failure-source'
    $failureSource = Join-Path $failureSourceParent 'One-Click-VidGen-main'
    $failurePackageDir = Join-Path $failureRoot 'runtime\temp\ocv-updates'
    $failurePackage = Join-Path $failurePackageDir 'update.zip'
    New-Item -ItemType Directory -Path $failureRoot, $failureSource, $failurePackageDir -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $failureRoot 'app.txt') -Value 'rollback-old-source' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $failureRoot 'blocked') -Value 'parent-is-a-file' -Encoding UTF8
    New-Item -ItemType Directory -Path (Join-Path $failureSource 'launcher'), (Join-Path $failureSource 'blocked') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $failureSource 'start_windows.bat') -Value '@echo off' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $failureSource 'app.txt') -Value 'rollback-new-source' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $failureSource 'blocked\child.txt') -Value 'force-copy-failure' -Encoding UTF8
    @{
        release_id = 'smoke-rollback-1'
        release_order = 2
        display_version = 'Smoke Rollback Test'
        portable_overlay_safe = $true
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $failureSource 'launcher\update-channel.json') -Encoding UTF8
    Compress-Archive -LiteralPath $failureSource -DestinationPath $failurePackage -CompressionLevel Fastest
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper `
        -Mode portable `
        -ProjectRoot $failureRoot `
        -LauncherPid 999999 `
        -ExpectedReleaseId 'smoke-rollback-1' `
        -PackagePath $failurePackage `
        -NoRestart
    if ($LASTEXITCODE -eq 0) { throw 'Rollback smoke test unexpectedly succeeded.' }
    if ((Get-Content -LiteralPath (Join-Path $failureRoot 'app.txt') -Raw).Trim() -ne 'rollback-old-source') {
        throw 'Assertion failed: failed update did not restore overwritten source.'
    }
    if ((Get-Content -LiteralPath (Join-Path $failureRoot 'blocked') -Raw).Trim() -ne 'parent-is-a-file') {
        throw 'Assertion failed: failed update changed the collision fixture.'
    }
    Write-Host 'PASS failed update rolled back overwritten source'

    $gitRoot = Join-Path $fixture 'git-project'
    New-Item -ItemType Directory -Path $gitRoot -Force | Out-Null
    & git.exe -C $gitRoot init --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize Git update fixture.' }
    & git.exe -C $gitRoot config user.email 'launcher-smoke@example.invalid'
    & git.exe -C $gitRoot config user.name 'OCV Launcher Smoke'
    @('runtime_logs/', 'Archives/', 'runtime/') | Set-Content -LiteralPath (Join-Path $gitRoot '.gitignore') -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $gitRoot 'app.txt') -Value 'git-old-source' -Encoding UTF8
    & git.exe -C $gitRoot add .gitignore app.txt
    & git.exe -C $gitRoot commit --quiet -m 'old'
    $baseBranch = (& git.exe -C $gitRoot branch --show-current).Trim()
    & git.exe -C $gitRoot checkout --quiet -b update-source
    Set-Content -LiteralPath (Join-Path $gitRoot 'app.txt') -Value 'git-new-source' -Encoding UTF8
    & git.exe -C $gitRoot add app.txt
    & git.exe -C $gitRoot commit --quiet -m 'new'
    $expectedCommit = (& git.exe -C $gitRoot rev-parse HEAD).Trim()
    & git.exe -C $gitRoot checkout --quiet $baseBranch
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper `
        -Mode git `
        -ProjectRoot $gitRoot `
        -LauncherPid 999999 `
        -ExpectedCommit $expectedCommit `
        -NoRestart
    if ($LASTEXITCODE -ne 0) { throw "Git safe-update helper exited with code $LASTEXITCODE" }
    if ((& git.exe -C $gitRoot rev-parse HEAD).Trim() -ne $expectedCommit) {
        throw 'Assertion failed: Git update did not fast-forward to the expected commit.'
    }
    if ((Get-Content -LiteralPath (Join-Path $gitRoot 'app.txt') -Raw).Trim() -ne 'git-new-source') {
        throw 'Assertion failed: Git update did not install the new source.'
    }
    Write-Host 'PASS Git update used the exact fast-forward commit'

    Set-Content -LiteralPath (Join-Path $gitRoot 'app.txt') -Value 'dirty-user-change' -Encoding UTF8
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper `
        -Mode git `
        -ProjectRoot $gitRoot `
        -LauncherPid 999999 `
        -ExpectedCommit $expectedCommit `
        -NoRestart
    if ($LASTEXITCODE -eq 0) { throw 'Dirty Git worktree update unexpectedly succeeded.' }
    if ((Get-Content -LiteralPath (Join-Path $gitRoot 'app.txt') -Raw).Trim() -ne 'dirty-user-change') {
        throw 'Assertion failed: dirty Git worktree was changed.'
    }
    Write-Host 'PASS dirty Git worktree was rejected without modification'
    Write-Host 'SAFE_UPDATE_SMOKE=PASS'
    exit 0
}
finally {
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
