param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('git', 'portable')]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory = $true)]
    [int]$LauncherPid,

    [string]$ExpectedCommit = '',
    [string]$ExpectedReleaseId = '',
    [string]$PackagePath = '',
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
$logsDir = Join-Path $root 'runtime_logs'
$historyRoot = Join-Path $root 'Archives\launcher_updates'
$tempRoot = Join-Path $root 'runtime\temp\ocv-updates'
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$historyDir = Join-Path $historyRoot $timestamp
$backupDir = Join-Path $historyDir 'backup'
$createdListPath = Join-Path $historyDir 'created_files.txt'
$logPath = Join-Path $logsDir 'launcher_update.log'
$resultPath = Join-Path $logsDir 'launcher_update_result.txt'
$createdFiles = New-Object System.Collections.Generic.List[string]
$portableMutationStarted = $false

New-Item -ItemType Directory -Path $logsDir, $historyDir -Force | Out-Null

function Write-UpdateLog {
    param([string]$Message)
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Assert-UnderRoot {
    param([string]$Path, [string]$AllowedRoot)
    $full = [System.IO.Path]::GetFullPath($Path)
    $allowed = [System.IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\') + '\'
    if (-not $full.StartsWith($allowed, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe update path: $full"
    }
    return $full
}

function Wait-LauncherExit {
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while ([DateTime]::UtcNow -lt $deadline) {
        $process = Get-Process -Id $LauncherPid -ErrorAction SilentlyContinue
        if (-not $process) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "Launcher process $LauncherPid did not exit within 45 seconds."
}

function Assert-OcvServicesStopped {
    $netstat = & netstat.exe -ano -p tcp 2>$null
    foreach ($port in @(8010, 5173, 8030)) {
        if ($netstat | Select-String -Pattern (':{0}\s+.*LISTENING' -f $port)) {
            throw "OCV managed port $port is still listening; update cancelled."
        }
    }
}

function Invoke-Git {
    param([string[]]$Arguments)
    $output = & git.exe -C $root @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($output -join ' ')"
    }
    return ($output -join "`n").Trim()
}

function Test-ProtectedRelativePath {
    param([string]$RelativePath)
    $normalized = $RelativePath.Replace('/', '\').TrimStart('\')
    $first = ($normalized -split '\\', 2)[0]
    $protectedRoots = @(
        '.git', 'Archives', 'output', 'runtime', 'runtime_logs', 'Sound Material',
        'TTS_Output', 'tts_voices', 'Vocal', 'workspace', 'saved_parameters',
        'saved_agent_prompts', 'node_modules'
    )
    if ($protectedRoots -contains $first) { return $true }
    # Unknown root folders with non-ASCII names are treated as user data.
    if ($first -match '[^\x00-\x7F]') { return $true }
    if ($normalized -ieq '.env') { return $true }
    if ($normalized.StartsWith('frontend\node_modules\', [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $false
}

function Restore-PortableBackup {
    Write-UpdateLog 'Restoring the pre-update backup.'
    if (Test-Path -LiteralPath $backupDir) {
        Get-ChildItem -LiteralPath $backupDir -Recurse -File | ForEach-Object {
            $relative = $_.FullName.Substring($backupDir.Length).TrimStart('\')
            $destination = Assert-UnderRoot (Join-Path $root $relative) $root
            $parent = Split-Path -Parent $destination
            if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
    }
    foreach ($relative in $createdFiles) {
        $destination = Assert-UnderRoot (Join-Path $root $relative) $root
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            Remove-Item -LiteralPath $destination -Force
        }
    }
    Write-UpdateLog 'The pre-update backup has been restored.'
}

function Restart-Launcher {
    if ($NoRestart) { return }
    $launcher = Join-Path $root 'OCV_Launcher.exe'
    if (Test-Path -LiteralPath $launcher) {
        Start-Process -FilePath $launcher -WorkingDirectory $root
    }
}

try {
    Write-UpdateLog "Safe update helper started. mode=$Mode"
    Wait-LauncherExit
    Assert-OcvServicesStopped

    if ($Mode -eq 'git') {
        if ($ExpectedCommit -notmatch '^[0-9a-fA-F]{40}$') {
            throw 'Expected Git commit is invalid.'
        }
        $dirty = Invoke-Git -Arguments @('status', '--porcelain')
        if ($dirty) {
            throw 'The Git worktree is dirty; update cancelled.'
        }
        $oldCommit = Invoke-Git -Arguments @('rev-parse', 'HEAD')
        Invoke-Git -Arguments @('cat-file', '-e', ($ExpectedCommit + '^{commit}')) | Out-Null
        Invoke-Git -Arguments @('merge', '--ff-only', $ExpectedCommit) | Out-Null
        $newCommit = Invoke-Git -Arguments @('rev-parse', 'HEAD')
        @{
            mode = 'git'
            previous_commit = $oldCommit
            installed_commit = $newCommit
            completed_at = (Get-Date).ToString('o')
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $historyDir 'update.json') -Encoding UTF8
        Write-UpdateLog "Git fast-forward completed: $oldCommit -> $newCommit"
    }
    else {
        if (-not $PackagePath) { throw 'Portable update package path is empty.' }
        $package = Assert-UnderRoot $PackagePath $tempRoot
        if (-not (Test-Path -LiteralPath $package -PathType Leaf)) { throw "Update package not found: $package" }

        $stage = Assert-UnderRoot (Join-Path $tempRoot ("stage_" + $timestamp)) $tempRoot
        New-Item -ItemType Directory -Path $stage -Force | Out-Null
        Expand-Archive -LiteralPath $package -DestinationPath $stage -Force
        $source = Get-ChildItem -LiteralPath $stage -Directory | Where-Object {
            Test-Path -LiteralPath (Join-Path $_.FullName 'start_windows.bat')
        } | Select-Object -First 1
        if (-not $source) { throw 'Invalid update package: OCV project root was not found.' }

        $channelPath = Join-Path $source.FullName 'launcher\update-channel.json'
        if (-not (Test-Path -LiteralPath $channelPath)) { throw 'The update package is missing update-channel.json.' }
        $channel = Get-Content -LiteralPath $channelPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($ExpectedReleaseId -and [string]$channel.release_id -ne $ExpectedReleaseId) {
            throw "Update package release mismatch: expected $ExpectedReleaseId, got $($channel.release_id)"
        }
        $localChannelPath = Join-Path $root 'launcher\update-channel.json'
        $localOrder = 0
        if (Test-Path -LiteralPath $localChannelPath) {
            $localChannel = Get-Content -LiteralPath $localChannelPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $localOrder = [long]$localChannel.release_order
        }
        $minimumOrder = [long]$channel.portable_overlay_min_order
        $baselineCompatible = $minimumOrder -gt 0 -and $localOrder -ge $minimumOrder
        if ($channel.portable_overlay_safe -ne $true -and -not $baselineCompatible) {
            throw 'This release requires a complete portable package and cannot be overlaid safely.'
        }

        $files = Get-ChildItem -LiteralPath $source.FullName -Recurse -File
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($source.FullName.Length).TrimStart('\')
            if (Test-ProtectedRelativePath $relative) { continue }
            $destination = Assert-UnderRoot (Join-Path $root $relative) $root
            $backup = Join-Path $backupDir $relative
            if (Test-Path -LiteralPath $destination -PathType Leaf) {
                $backupParent = Split-Path -Parent $backup
                New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
                Copy-Item -LiteralPath $destination -Destination $backup -Force
            }
            else {
                $createdFiles.Add($relative)
            }
        }
        $createdFiles | Set-Content -LiteralPath $createdListPath -Encoding UTF8
        $portableMutationStarted = $true

        foreach ($file in $files) {
            $relative = $file.FullName.Substring($source.FullName.Length).TrimStart('\')
            if (Test-ProtectedRelativePath $relative) { continue }
            $destination = Assert-UnderRoot (Join-Path $root $relative) $root
            $parent = Split-Path -Parent $destination
            if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
            Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        }

        @{
            mode = 'portable'
            release_id = [string]$channel.release_id
            display_version = [string]$channel.display_version
            completed_at = (Get-Date).ToString('o')
            protected_data = @('.env', 'runtime', 'output', 'workspace', 'runtime_logs', 'user presets')
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $historyDir 'update.json') -Encoding UTF8
        Write-UpdateLog "Portable protected overlay completed: $($channel.release_id)"
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $package -Force -ErrorAction SilentlyContinue
    }

    "SUCCESS|$Mode|$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-UpdateLog 'Safe update completed.'
    Restart-Launcher
    exit 0
}
catch {
    Write-UpdateLog ("Safe update failed: " + $_.Exception.Message)
    if ($Mode -eq 'portable' -and $portableMutationStarted) {
        try { Restore-PortableBackup }
        catch { Write-UpdateLog ("Automatic rollback failed: " + $_.Exception.Message) }
    }
    ("FAILED|$Mode|" + $_.Exception.Message) | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Restart-Launcher
    exit 1
}
