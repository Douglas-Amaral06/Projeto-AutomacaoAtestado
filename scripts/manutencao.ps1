$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& ".\.venv\Scripts\python.exe" -c "from app.maintenance import create_backup,apply_retention,prune_backups; print('Backup:',create_backup()); print('Retencao:',apply_retention()); print('Backups antigos removidos:',prune_backups())"
