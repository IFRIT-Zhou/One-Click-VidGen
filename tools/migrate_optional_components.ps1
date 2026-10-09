param([string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\')
if (!(Test-Path -LiteralPath (Join-Path $root 'OCV_Launcher.exe'))) { throw 'Not an OCV root' }
function Inside([string]$relative) {
    $path=[IO.Path]::GetFullPath((Join-Path $root $relative))
    if (!$path.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Path outside OCV' }
    return $path
}
function MoveComponent([string]$from,[string]$to) {
    $source=Inside $from; $target=Inside $to
    if (!(Test-Path -LiteralPath $source)) { return }
    if (Test-Path -LiteralPath $target) { throw "Target already exists: $to" }
    if ((Get-Item -LiteralPath $source).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked source refused' }
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    Move-Item -LiteralPath $source -Destination $target
    Write-Output "Moved $from -> $to"
}
$running=@(Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -and ($_.ExecutablePath.StartsWith((Inside 'runtime/comfyui')+'\',[StringComparison]::OrdinalIgnoreCase) -or $_.ExecutablePath.StartsWith((Inside 'tts')+'\',[StringComparison]::OrdinalIgnoreCase))})
if($running.Count){throw 'Stop the optional ComfyUI/TTS engine before migration.'}
MoveComponent 'runtime/comfyui' 'comfyui/engine'
$activePath=Inside 'comfyui/engine/active.json'
if(Test-Path -LiteralPath $activePath){
    $version=(Get-Content -LiteralPath $activePath -Raw | ConvertFrom-Json).version
    if($version -notmatch '^[a-zA-Z0-9._-]+$' -or $version -in @('.','..')){throw 'Invalid version'}
    $nodes=Inside "comfyui/engine/releases/$version/ComfyUI/custom_nodes"
    foreach($node in Get-ChildItem -LiteralPath $nodes -Directory | Where-Object {$_.Name -ne '__pycache__'}) {
        MoveComponent "comfyui/engine/releases/$version/ComfyUI/custom_nodes/$($node.Name)" "comfyui/custom_nodes/$($node.Name)"
    }
}
$oldNodes=Inside 'comfyui_plugins'
if(Test-Path -LiteralPath $oldNodes){
    foreach($node in Get-ChildItem -LiteralPath $oldNodes -Directory | Where-Object {$_.Name -ne '__pycache__'}){
        MoveComponent "comfyui_plugins/$($node.Name)" "comfyui/custom_nodes/$($node.Name)"
    }
}
MoveComponent 'tools/IndexTTS25' 'tts/IndexTTS25'
$oldModels=Inside 'tts/IndexTTS25/checkpoints'
$newModels=Inside 'models/tts/indextts25'
if(Test-Path -LiteralPath $oldModels){
    $files=@(Get-ChildItem -LiteralPath $oldModels -Recurse -File)
    if(@(Get-ChildItem -LiteralPath $oldModels -Recurse -Attributes ReparsePoint).Count){throw 'Model links require manual migration'}
    # Check every collision before removing any identical duplicate.
    foreach($file in $files){
        $target=Join-Path $newModels $file.FullName.Substring($oldModels.Length+1)
        if(Test-Path -LiteralPath $target){
            if($file.Length -ne (Get-Item -LiteralPath $target).Length -or (Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $target).Hash){
                throw "Different model files at target: $target"
            }
        }
    }
    foreach($file in $files){
        $target=Join-Path $newModels $file.FullName.Substring($oldModels.Length+1)
        if(Test-Path -LiteralPath $target){Remove-Item -LiteralPath $file.FullName -Force}
        else{New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null; Move-Item -LiteralPath $file.FullName -Destination $target}
    }
    Remove-Item -LiteralPath $oldModels -Recurse -Force
    Write-Output 'TTS models consolidated after SHA-256 verification.'
}
$python=Inside 'tts/python'
if(!(Test-Path -LiteralPath (Join-Path $python '.ocv-copy-complete'))){
    & robocopy (Inside 'runtime/python') $python /E /XJ /R:1 /W:1 /MT:8 /NFL /NDL /NP /NJH /NJS /XD __pycache__ /XF *.pyc *.log
    if($LASTEXITCODE -ge 8){throw 'TTS Python copy failed; rerun to resume'}
    Set-Content -LiteralPath (Join-Path $python '.ocv-copy-complete') -Value 'Migrated from OCV portable Python' -Encoding utf8
}
Write-Output 'Component migration complete; models remain under models/.'
