$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& ".\.venv\Scripts\python.exe" -c "from app.maintenance import create_backup; print(create_backup())"
