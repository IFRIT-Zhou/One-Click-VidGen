param(
    [string]$ProjectRoot = "",
    [switch]$SkipModelDownload
)

$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$engineRoot = Join-Path $ProjectRoot "tools\IndexTTS25"
$python = Join-Path $ProjectRoot "runtime\python\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "OCV portable Python was not found: $python"
}

if (-not (Test-Path -LiteralPath (Join-Path $engineRoot ".git") -PathType Container)) {
    if (Test-Path -LiteralPath $engineRoot) {
        throw "IndexTTS25 exists but is not an official Git checkout: $engineRoot"
    }
    git clone --depth 1 https://github.com/index-tts/index-tts.git $engineRoot
    if ($LASTEXITCODE -ne 0) { throw "Official IndexTTS-2.5 source clone failed" }
} else {
    git -C $engineRoot pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "Official IndexTTS-2.5 source update failed" }
}

$packages = Join-Path $engineRoot "python_packages"
New-Item -ItemType Directory -Force $packages | Out-Null
$env:PIP_CACHE_DIR = Join-Path $ProjectRoot "runtime\cache\pip"
New-Item -ItemType Directory -Force $env:PIP_CACHE_DIR | Out-Null
& $python -m pip install --disable-pip-version-check --target $packages --upgrade --no-deps `
    fugashi unidic-lite openai-whisper tiktoken
if ($LASTEXITCODE -ne 0) { throw "IndexTTS-2.5 isolated dependency install failed" }

if (-not $SkipModelDownload) {
    $modelDir = Join-Path $engineRoot "checkpoints"
    New-Item -ItemType Directory -Force $modelDir | Out-Null
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
from indextts.utils.model_download import ensure_models_available
from huggingface_hub import hf_hub_download
ensure_models_available(r'$modelDir')
w2v_dir = r'$modelDir\hf_cache\w2v-bert-2.0'
for filename in ('model.safetensors', 'conformer_shaw.pt'):
    hf_hub_download('facebook/w2v-bert-2.0', filename=filename, local_dir=w2v_dir)
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
