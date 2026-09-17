param(
    [string]$ProjectRoot = "",
    [switch]$SkipModelDownload
)

# The official IndexTTS-2.5 source is pinned to a release tag instead of the moving
# default branch, so a deployment is reproducible and the source always matches the
# checkpoints it is used with.
$IndexTtsTag = "v2.5.0"

$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$engineRoot = Join-Path $ProjectRoot "tools\IndexTTS25"
$python = Join-Path $ProjectRoot "runtime\python\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "OCV portable Python was not found: $python"
}

if (Test-Path -LiteralPath (Join-Path $engineRoot ".git") -PathType Container) {
    git -C $engineRoot fetch --depth 1 origin "refs/tags/${IndexTtsTag}:refs/tags/${IndexTtsTag}"
    if ($LASTEXITCODE -ne 0) { throw "Official IndexTTS-2.5 source fetch failed" }
    git -C $engineRoot checkout --detach $IndexTtsTag
    if ($LASTEXITCODE -ne 0) { throw "Official IndexTTS-2.5 source checkout failed" }
} elseif (-not (Test-Path -LiteralPath (Join-Path $engineRoot "indextts\infer_v2_5.py") -PathType Leaf)) {
    if (Test-Path -LiteralPath $engineRoot) {
        throw "IndexTTS-2.5 source is incomplete: $engineRoot"
    }
    git clone --depth 1 --branch $IndexTtsTag https://github.com/index-tts/index-tts.git $engineRoot
    if ($LASTEXITCODE -ne 0) { throw "Official IndexTTS-2.5 source clone failed" }
} else {
    Write-Host "Using the IndexTTS-2.5 source bundled with OCV."
}

$packages = Join-Path $engineRoot "python_packages"
New-Item -ItemType Directory -Force $packages | Out-Null
$requiredPackages = @("fugashi", "unidic_lite", "whisper", "tiktoken")
$missingPackages = @($requiredPackages | Where-Object { -not (Test-Path -LiteralPath (Join-Path $packages $_)) })
if ($missingPackages.Count -gt 0) {
    Write-Host "Repairing IndexTTS-2.5 isolated dependencies: $($missingPackages -join ', ')"
    $env:PIP_CACHE_DIR = Join-Path $ProjectRoot "runtime\cache\pip"
    New-Item -ItemType Directory -Force $env:PIP_CACHE_DIR | Out-Null
    & $python -m pip install --disable-pip-version-check --target $packages --upgrade --no-deps `
        fugashi unidic-lite openai-whisper tiktoken
    if ($LASTEXITCODE -ne 0) { throw "IndexTTS-2.5 isolated dependency install failed" }
} else {
    Write-Host "IndexTTS-2.5 isolated dependencies are already available."
}

# Dependencies of the inference path that the isolated overlay above does not provide
# (it installs four packages with --no-deps). They live in their own manifest so that
# users who never run the local voice mode do not inherit them.
$inferenceManifest = Join-Path $ProjectRoot "requirements-indextts.txt"
if (Test-Path -LiteralPath $inferenceManifest -PathType Leaf) {
    Write-Host "Installing IndexTTS-2.5 inference dependencies from requirements-indextts.txt..."
    & $python -m pip install --disable-pip-version-check -r $inferenceManifest
    if ($LASTEXITCODE -ne 0) { throw "IndexTTS-2.5 inference dependency install failed" }
}

if (-not $SkipModelDownload) {
    $modelDir = Join-Path $engineRoot "checkpoints"
    New-Item -ItemType Directory -Force $modelDir | Out-Null
    Write-Host "Downloading the optional IndexTTS-2.5 model (resume is supported)..."
    $downloadCode = @"
from modelscope.hub.snapshot_download import snapshot_download
snapshot_download('IndexTeam/IndexTTS-2.5', local_dir=r'$modelDir')
"@
    & $python -c $downloadCode
    if ($LASTEXITCODE -ne 0) { throw "Official IndexTTS-2.5 model download failed" }

    $env:PYTHONPATH = "$packages;$engineRoot"
    $env:HF_HOME = Join-Path $modelDir "hf_cache"
    $env:HF_HUB_DOWNLOAD_TIMEOUT = "1800"
$auxCode = @"
import os
from indextts.utils.model_download import ensure_models_available
ensure_models_available(r'$modelDir')
w2v_dir = r'$modelDir\hf_cache\w2v-bert-2.0'
for filename in ('model.safetensors', 'conformer_shaw.pt'):
    path = os.path.join(w2v_dir, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
"@
    & $python -c $auxCode
    if ($LASTEXITCODE -ne 0) { throw "IndexTTS-2.5 auxiliary model download failed; rerun to resume" }
}

$examples = Join-Path $engineRoot "examples"
New-Item -ItemType Directory -Force $examples | Out-Null
$exampleCode = @"
from pathlib import Path
import requests
root = Path(r'$examples')
base = 'https://huggingface.co/spaces/IndexTeam/IndexTTS-2.5-Demo/resolve/main/examples'
for index in (*range(1, 10), 11, 12):
    target = root / f'voice_{index:02d}.wav'
    if target.is_file() and target.stat().st_size > 100:
        continue
    response = requests.get(f'{base}/{target.name}', timeout=180)
    response.raise_for_status()
    target.write_bytes(response.content)
"@
& $python -c $exampleCode
if ($LASTEXITCODE -ne 0) { throw "Official IndexTTS-2.5 example voice download failed" }

Write-Host "IndexTTS-2.5 deployment completed: $engineRoot"
