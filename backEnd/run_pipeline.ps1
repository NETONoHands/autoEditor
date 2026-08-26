$ErrorActionPreference = "Stop"

$backendRoot = $PSScriptRoot
$workspaceRoot = Split-Path -Parent $backendRoot
$pythonExe = Join-Path $backendRoot ".venv/Scripts/python.exe"
$projectRoot = $backendRoot

# Preencha estes caminhos antes de executar.
$videoPath = Join-Path $backendRoot "cortes/corte_1_como_funciona_o_quadstick_para_pcd.mp4"
$srtPath = Join-Path $backendRoot "cortes/corte_1_como_funciona_o_quadstick_para_pcd.srt"
$editName = "como funciona o quadstick para pcd"
$lutPath = Join-Path $backendRoot "assets/color/Assets/Vivid LUTs 3.cube"

if (-not (Test-Path $pythonExe)) {
    throw "Python do venv nao encontrado em: $pythonExe"
}

if (-not (Test-Path $videoPath)) {
    throw "Video nao encontrado em: $videoPath"
}

if (-not (Test-Path $srtPath)) {
    throw "SRT nao encontrado em: $srtPath"
}

Set-Location $workspaceRoot
& $pythonExe -m backEnd.main `
    "$videoPath" `
    "$srtPath" `
    "$editName" `
    --project-root "$projectRoot" `
    --lut-path "$lutPath"

Write-Host "Pipeline finalizado. Confira a pasta Output." -ForegroundColor Green