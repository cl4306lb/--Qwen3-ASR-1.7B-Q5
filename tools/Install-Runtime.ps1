param([string]$Proxy = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$cache = Join-Path $projectRoot '.setup-cache'
$vendor = Join-Path $projectRoot 'CapsWriter-Offline'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$assets = @(
    @{ Name='CapsWriter-Offline-20260914.zip'; Url='https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/v2.6/CapsWriter-Offline-20260914.zip'; SHA='40a46100e2b221ed487d85aa923a38f8f722090bf1fa86975b701baceafbb6f4'; Destination=$projectRoot; Marker='CapsWriter-Offline\start_server.exe' },
    @{ Name='Qwen3-ASR-1.7B-q5_k.zip'; Url='https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/Qwen3-ASR-1.7B-q5_k.zip'; SHA='f40040fe62a5ef0c09f8699fdbcb30f18bb8ae2bcd515ed4954e1f62b8b0e88f'; Destination=(Join-Path $vendor 'models\Qwen3-ASR'); Marker='CapsWriter-Offline\models\Qwen3-ASR\Qwen3-ASR-1.7B\qwen3_asr_llm.gguf' },
    @{ Name='Qwen3-ForcedAligner-0.6B.zip'; Url='https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/Qwen3-ForcedAligner-0.6B.zip'; SHA='8fa035db150e1621a01cbb313bf152b593818b0f15dd94180452f22fc77fbf43'; Destination=(Join-Path $vendor 'models\Qwen3-ForcedAligner'); Marker='CapsWriter-Offline\models\Qwen3-ForcedAligner\Qwen3-ForcedAligner-0.6B\qwen3_aligner_llm.q5_k.gguf' },
    @{ Name='ffmpeg-release-essentials.zip'; Url='https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'; SHA='60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'; Destination=(Join-Path $cache 'ffmpeg'); Marker='CapsWriter-Offline\ffmpeg.exe' }
)
$needsConfig = !(Test-Path -LiteralPath (Join-Path $vendor 'config_server.py'))
foreach ($asset in $assets) {
    if (Test-Path -LiteralPath (Join-Path $projectRoot $asset.Marker)) {
        Write-Host "Already installed: $($asset.Name)"
        continue
    }
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $archive = Join-Path $cache $asset.Name
    if (!(Test-Path -LiteralPath $archive)) {
        Write-Host "Downloading: $($asset.Name)"
        $request = @{Uri=$asset.Url; OutFile=$archive; UseBasicParsing=$true; TimeoutSec=3600}
        if ($Proxy) { $request.Proxy = $Proxy }
        Invoke-WebRequest @request
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $asset.SHA) {
        throw "SHA256 mismatch: $archive. Official assets may have changed; do not use this download."
    }
    New-Item -ItemType Directory -Force -Path $asset.Destination | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $asset.Destination -Force
    if ($asset.Name -eq 'ffmpeg-release-essentials.zip') {
        foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
            $binary = Get-ChildItem -LiteralPath $asset.Destination -Recurse -File -Filter $name | Select-Object -First 1
            if (!$binary) { throw "Missing FFmpeg binary: $name" }
            Copy-Item -LiteralPath $binary.FullName -Destination (Join-Path $vendor $name)
        }
    }
    if (!(Test-Path -LiteralPath (Join-Path $projectRoot $asset.Marker))) {
        throw "Archive layout is unexpected: $($asset.Name)"
    }
}
if ($needsConfig) {
    Copy-Item -LiteralPath (Join-Path $projectRoot 'configs\config_server.py') -Destination (Join-Path $vendor 'config_server.py') -Force
}
foreach ($relative in @(
    'models\Qwen3-ASR\Qwen3-ASR-1.7B\qwen3_asr_encoder_frontend.onnx',
    'models\Qwen3-ASR\Qwen3-ASR-1.7B\qwen3_asr_encoder_backend.onnx',
    'models\Qwen3-ForcedAligner\Qwen3-ForcedAligner-0.6B\qwen3_aligner_encoder_frontend.int4.onnx',
    'models\Qwen3-ForcedAligner\Qwen3-ForcedAligner-0.6B\qwen3_aligner_encoder_backend.int4.onnx',
    'ffprobe.exe'
)) {
    if (!(Test-Path -LiteralPath (Join-Path $vendor $relative))) { throw "Missing runtime file: $relative" }
}
Write-Host 'Runtime ready. Build the desktop app with desktop/build.ps1.'
