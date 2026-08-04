$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& (Join-Path $PSScriptRoot "backup.ps1")
& uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
& (Join-Path $PSScriptRoot "executar_testes.ps1")
Write-Host "Atualizacao validada. Recarregue a extensao no navegador e reinicie o servidor."
