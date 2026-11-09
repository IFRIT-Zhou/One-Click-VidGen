# Optional offline OCR for Windows OCV. Input is a bounded JPEG on stdin as ASCII
# base64, not a file path, URL, command or script. All processing stays in memory.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Resolve-Async($op, $type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.GetAwaiter().GetResult()
}
$inputText = [Console]::In.ReadToEnd()
if ($inputText.Length -gt 4000000) { throw 'Image too large' }
$ms = [IO.MemoryStream]::new([Convert]::FromBase64String($inputText))
$rs = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($ms)
try {
    $decoder = Resolve-Async ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($rs)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Resolve-Async ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('zh-Hans'))
        if (!$engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
        if (!$engine) { throw 'No installed OCR language' }
        $result = Resolve-Async ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $words = @(foreach ($line in $result.Lines) { foreach ($word in $line.Words) {
            $r = $word.BoundingRect
            @{text=$word.Text; box=@($r.X,$r.Y,($r.X+$r.Width),($r.Y+$r.Height))}
        }})
        ConvertTo-Json -InputObject $words -Depth 4 -Compress
    } finally { $bitmap.Dispose() }
} finally { $rs.Dispose(); $ms.Dispose() }
