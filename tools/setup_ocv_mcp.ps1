param(
    [switch]$AllowGeneration,
    [switch]$AllowDelete,
    [switch]$ReadOnly,
    [string[]]$UploadRoots = @(),
    [string[]]$UploadFiles = @(),
    [string]$CookieFile = '',
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$bootstrap = Join-Path $root 'runtime\python\python.exe'
$environment = Join-Path $root 'runtime\ocv_mcp'
$python = Join-Path $environment 'Scripts\python.exe'
$server = Join-Path $root 'plugins\codex_bridge\mcp_server.py'
if (-not (Test-Path -LiteralPath $bootstrap)) { throw '找不到 OCV 便携 Python。' }
if (-not (Test-Path -LiteralPath $python)) {
    & $bootstrap -m venv $environment
    if ($LASTEXITCODE -ne 0) { throw 'MCP 独立环境创建失败。' }
}
if (-not $SkipInstall) {
    & $python -m pip install -r (Join-Path $root 'plugins\codex_bridge\mcp-requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'MCP 依赖安装失败；OCV 原环境未修改。' }
}
$serverArgs = @($server)
if ($AllowGeneration) { $serverArgs += '--allow-generation' }
if ($AllowDelete) { $serverArgs += '--allow-delete' }
if ($ReadOnly) { $serverArgs += '--read-only' }
foreach ($uploadRoot in $UploadRoots) {
    $resolved = (Resolve-Path -LiteralPath $uploadRoot).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) { throw '上传授权根必须是目录。' }
    $serverArgs += @('--allow-upload-root', $resolved)
}
if ($CookieFile) {
    $serverArgs += @('--cookie-file', (Resolve-Path -LiteralPath $CookieFile).Path)
}
foreach ($uploadFile in $UploadFiles) {
    $resolved = (Resolve-Path -LiteralPath $uploadFile).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { throw '上传文件授权必须指向文件。' }
    $serverArgs += @('--allow-upload-file', $resolved)
}
$config = @{ mcpServers = @{ ocv = @{ command = $python; args = $serverArgs } } }
$destination = Join-Path $environment 'client-config.json'
# Generated client configuration only, never changes another app's settings.
$config | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $destination -Encoding UTF8
Write-Output "MCP 独立环境就绪。客户端配置：$destination"
Write-Output '没有启动 OCV、没有修改任何制作项目，也没有提交模型任务。'
Write-Output '只读检查：使用上述 Python 运行 mcp_server.py --check（先启动 OCV）。'
