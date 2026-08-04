$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "Instale o uv: https://docs.astral.sh/uv/" }
if (-not (Test-Path -LiteralPath ".venv")) { & uv venv --python 3.12 }
& uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
if (-not (Test-Path -LiteralPath ".env")) { Copy-Item -LiteralPath ".env.example" -Destination ".env" }
Write-Host "Dependencias instaladas. Agora execute .\scripts\configurar_seguranca.ps1 e configure GEMINI_API_KEY no .env."
