$ErrorActionPreference = "Stop"

$pythonExe = "e:/Projetos/autoEditor/.venv/Scripts/python.exe"
$projectRoot = "E:/Projetos/autoEditor"

# Preencha estes caminhos antes de executar.
$videoPath = "E:/Projetos/autoEditor/cortes/corte_1_como_funciona_o_quadstick_para_pcd.mp4"
$srtPath = "E:/Projetos/autoEditor/cortes/corte_1_como_funciona_o_quadstick_para_pcd.srt"
$lutPath = "E:/Projetos/autoEditor/assets/color/Assets/Vivid LUTs 3.cube"

if (-not (Test-Path $pythonExe)) {
    throw "Python do venv nao encontrado em: $pythonExe"
}

if (-not (Test-Path $videoPath)) {
    throw "Video nao encontrado em: $videoPath"
}

if (-not (Test-Path $srtPath)) {
    throw "SRT nao encontrado em: $srtPath"
}

if (-not (Test-Path $lutPath)) {
    throw "LUT nao encontrado em: $lutPath"
}

& $pythonExe "$projectRoot/main.py" `
    "$videoPath" `
    "$srtPath" `
    --project-root "$projectRoot" `
    --lut-path "$lutPath"

Write-Host "Pipeline finalizado. Confira a pasta Output." -ForegroundColor Green