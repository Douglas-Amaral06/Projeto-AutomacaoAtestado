$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $projectRoot ".venv\Scripts\uvicorn.exe"

if (-not (Test-Path -LiteralPath $server)) {
    throw "Dependencias nao encontradas. Instale primeiro conforme o README.md."
}

Set-Location -LiteralPath $projectRoot
& $server app.main:app --host 127.0.0.1 --port 8000 --reload
