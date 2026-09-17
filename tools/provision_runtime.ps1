<#
.SYNOPSIS
    Provision the runtime assets a source (Git) deployment of One-Click-VidGen needs.

.DESCRIPTION
    Assets are described in tools/portable_assets.json. Each entry is pinned to an
    immutable version and carries the SHA-256 (or, for non-LFS vendor files, the git
    blob SHA-1) published by that vendor, so any mirror can be used safely: a source
    that serves anything else is rejected and the next source is tried.

    Per asset the flow is strictly:

        download -> verify -> extract into staging -> self-check
                 -> back up the previous installation -> swap -> final self-check
                 -> (on any failure) roll back to the previous installation

    Nothing overwrites an existing installation until the new copy has been fully
    downloaded, verified, extracted and self-checked, and the previous copy is kept
    until the new one passes its check at the final location. Assets whose vendor
    publishes no checksum are intentionally absent from the manifest.

    Verified downloads are cached under runtime\_bootstrap\downloads so that a repair
    run does not download hundreds of megabytes again; deleting that folder is safe
    and only costs a re-download.

.PARAMETER Component
    python | node | ffmpeg | whisper | all (default: all)

.PARAMETER Force
    Reinstall even when the component is already present.

.PARAMETER List
    Print the manifest and exit without downloading anything.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\provision_runtime.ps1 -Component python
#>
param(
    [string]$Component = "all",
    [string]$ProjectRoot = "",
    [switch]$Force,
    [switch]$List
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path

$manifestPath = Join-Path $PSScriptRoot "portable_assets.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Asset manifest not found: $manifestPath"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

$cacheDir = Join-Path $ProjectRoot "runtime\_bootstrap\downloads"
$stageRoot = Join-Path $ProjectRoot "runtime\_bootstrap\stage"

function Get-ProjectPath([string]$relative) {
    return (Join-Path $ProjectRoot $relative)
}

function Test-Installed($asset) {
    $target = Get-ProjectPath $asset.install_to
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { return $false }
    foreach ($entry in $asset.required_entries) {
        if (-not (Test-Path -LiteralPath (Join-Path $target $entry))) { return $false }
    }
    return $true
}

function Get-FileSha256Hex([string]$path) {
    # .NET instead of Get-FileHash: that cmdlet is absent on some Windows images
    # (observed on a stock Windows 11 install), and this streams the file so it also
    # copes with the multi-hundred-megabyte archives.
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try {
        return ([BitConverter]::ToString($algorithm.ComputeHash($stream)) -replace '-', '').ToLower()
    } finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

function Test-Fingerprint([string]$path, $spec) {
    # Accepts either the vendor SHA-256 or the git blob SHA-1 recorded in the manifest.
    if ($spec.sha256) {
        return ((Get-FileSha256Hex $path) -eq $spec.sha256.ToLower())
    }
    if ($spec.git_blob_sha1) {
        # Only used for small vendor files that have no published SHA-256.
        $bytes = [System.IO.File]::ReadAllBytes($path)
        $header = [System.Text.Encoding]::ASCII.GetBytes("blob $($bytes.Length)" + [char]0)
        $payload = New-Object byte[] ($header.Length + $bytes.Length)
        [Array]::Copy($header, 0, $payload, 0, $header.Length)
        [Array]::Copy($bytes, 0, $payload, $header.Length, $bytes.Length)
        $sha1 = [System.Security.Cryptography.SHA1]::Create()
        try {
            $actual = ([BitConverter]::ToString($sha1.ComputeHash($payload)) -replace '-', '').ToLower()
        } finally {
            $sha1.Dispose()
        }
        return ($actual -eq $spec.git_blob_sha1.ToLower())
    }
    throw "No fingerprint is recorded for '$($spec.name)' - refusing to install unverified content."
}

function Get-RemoteFile([string]$url, [string]$destination) {
    $curl = Join-Path $env:SystemRoot "System32\curl.exe"
    if (-not (Test-Path -LiteralPath $curl -PathType Leaf)) { $curl = "curl.exe" }
    & $curl -L --fail --retry 2 --connect-timeout 15 --speed-limit 10240 --speed-time 30 -sS -o $destination $url
    return ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $destination -PathType Leaf))
}

function Receive-VerifiedFile($spec, [string]$destination) {
    foreach ($url in $spec.urls) {
        if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Force }
        Write-Host "   source: $url"
        if (-not (Get-RemoteFile $url $destination)) {
            Write-Host "   unavailable, trying the next source"
            continue
        }
        if (Test-Fingerprint $destination $spec) {
            Write-Host "   verified against the vendor fingerprint"
            return
        }
        Write-Host "   REJECTED: checksum does not match the vendor value"
        Remove-Item -LiteralPath $destination -Force
    }
    throw "Every source failed or failed verification. Nothing was installed."
}

function Get-CachedVerifiedFile($spec, [string]$cachePath) {
    if (Test-Path -LiteralPath $cachePath -PathType Leaf) {
        if (Test-Fingerprint $cachePath $spec) {
            Write-Host "   reusing verified download: $cachePath"
            return
        }
        Remove-Item -LiteralPath $cachePath -Force
    }
    Receive-VerifiedFile $spec $cachePath
}

function Expand-Staged([string]$archive, [string]$destination) {
    if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
    New-Item -ItemType Directory -Force $destination | Out-Null
    & tar.exe -xf $archive -C $destination
    if ($LASTEXITCODE -ne 0) { throw "Extraction failed: $archive" }
}

function Invoke-SelfCheck($asset, [string]$baseDir) {
    if (-not $asset.self_check) { return }
    $executable = Join-Path $baseDir $asset.self_check.path
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Self-check failed for '$($asset.id)': $executable is missing."
    }
    $arguments = @()
    if ($asset.self_check.args) { $arguments = @($asset.self_check.args) }
    $output = (& $executable $arguments 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Self-check failed for '$($asset.id)': exit code $LASTEXITCODE`n$output"
    }
    if ($asset.self_check.expect_stdout -and ($output -notmatch [regex]::Escape($asset.self_check.expect_stdout))) {
        throw "Self-check failed for '$($asset.id)': output does not contain '$($asset.self_check.expect_stdout)'`n$output"
    }
}

function Install-FromStaging($asset, [string]$payloadDir) {
    $target = Get-ProjectPath $asset.install_to
    $parent = Split-Path -Parent $target
    New-Item -ItemType Directory -Force $parent | Out-Null

    $backup = $null
    if (Test-Path -LiteralPath $target) {
        $backup = "$target.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        Write-Host "   backing up the existing installation to $(Split-Path -Leaf $backup)"
        Move-Item -LiteralPath $target -Destination $backup
    }

    try {
        Move-Item -LiteralPath $payloadDir -Destination $target
        Invoke-SelfCheck $asset $target
    } catch {
        Write-Host "   FAILED after the swap, rolling back"
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        if ($backup -and (Test-Path -LiteralPath $backup)) {
            Move-Item -LiteralPath $backup -Destination $target
            Write-Host "   the previous installation was restored"
        }
        throw
    }

    if ($backup -and (Test-Path -LiteralPath $backup)) {
        Remove-Item -LiteralPath $backup -Recurse -Force
    }
    Write-Host "   installed: $target"
}

function Install-ArchiveAsset($asset) {
    Write-Host "== $($asset.title) $($asset.version)"
    if ((Test-Installed $asset) -and -not $Force) {
        Write-Host "   already present, skipping (use -Force to replace)"
        return
    }

    $extension = if ($asset.archive -eq "tar.gz") { ".tar.gz" } else { ".zip" }
    $cachePath = Join-Path $cacheDir ("{0}-{1}{2}" -f $asset.id, $asset.sha256.Substring(0, 12), $extension)
    Get-CachedVerifiedFile $asset $cachePath

    $stageDir = Join-Path $stageRoot $asset.id
    Write-Host "   extracting into staging"
    Expand-Staged $cachePath $stageDir

    $payloadDir = Join-Path $stageDir $asset.payload
    if (-not (Test-Path -LiteralPath $payloadDir -PathType Container)) {
        throw "Unexpected archive layout for '$($asset.id)': '$($asset.payload)' is missing."
    }

    Write-Host "   self-check before touching the installed copy"
    Invoke-SelfCheck $asset $payloadDir

    Install-FromStaging $asset $payloadDir
    Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
}

function Install-FileSetAsset($asset) {
    Write-Host "== $($asset.title) $($asset.version)"
    if ((Test-Installed $asset) -and -not $Force) {
        Write-Host "   already present, skipping (use -Force to replace)"
        return
    }

    $stageDir = Join-Path $stageRoot $asset.id
    if (Test-Path -LiteralPath $stageDir) { Remove-Item -LiteralPath $stageDir -Recurse -Force }
    New-Item -ItemType Directory -Force $stageDir | Out-Null

    foreach ($file in $asset.files) {
        Write-Host "   $($file.name)"
        $cachePath = Join-Path $cacheDir ("{0}-{1}" -f $asset.id, $file.name)
        Get-CachedVerifiedFile $file $cachePath
        Copy-Item -LiteralPath $cachePath -Destination (Join-Path $stageDir $file.name) -Force
    }

    Install-FromStaging $asset $stageDir
}

if ($List) {
    foreach ($asset in $manifest.assets) {
        Write-Host ("{0,-9} {1,-22} {2}" -f $asset.id, $asset.version, $asset.install_to)
    }
    exit 0
}

New-Item -ItemType Directory -Force $cacheDir, $stageRoot | Out-Null

$selected = @()
if ($Component -eq "all") {
    $selected = @($manifest.assets)
} else {
    $selected = @($manifest.assets | Where-Object { $_.id -eq $Component })
    if ($selected.Count -eq 0) {
        $known = ($manifest.assets | ForEach-Object { $_.id }) -join ", "
        throw "Unknown component '$Component'. Known components: $known, all"
    }
}

foreach ($asset in $selected) {
    if ($asset.files) {
        Install-FileSetAsset $asset
    } else {
        Install-ArchiveAsset $asset
    }
}

Write-Host "Provisioning finished."
