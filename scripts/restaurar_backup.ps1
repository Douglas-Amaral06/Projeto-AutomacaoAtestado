param([Parameter(Mandatory=$true)][string]$Arquivo)
$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$projectRoot = Split-Path -Parent $PSScriptRoot
$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "Pare o servidor antes de restaurar o banco." }
$resolvedBackup = (Resolve-Path -LiteralPath $Arquivo).Path
$backupRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot "backups")).Path
if (-not $resolvedBackup.StartsWith($backupRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Por seguranca, selecione um backup dentro da pasta backups."
}
Set-Location -LiteralPath $projectRoot
$restoreTemp = Join-Path $projectRoot ".restore-temp"
if (Test-Path -LiteralPath $restoreTemp) { Remove-Item -LiteralPath $restoreTemp -Recurse -Force }
& ".\.venv\Scripts\python.exe" -c "import sys; from pathlib import Path; from app.maintenance import extract_verified_backup; extract_verified_backup(Path(sys.argv[1]), Path(sys.argv[2]))" $resolvedBackup $restoreTemp
if ($LASTEXITCODE -ne 0) {
    if (Test-Path -LiteralPath $restoreTemp) { Remove-Item -LiteralPath $restoreTemp -Recurse -Force }
    throw "Backup invalido ou inseguro. Nenhum dado atual foi substituido."
}
if (Test-Path -LiteralPath "data\atestados.db") { & (Join-Path $PSScriptRoot "backup.ps1") }
Copy-Item -LiteralPath (Join-Path $restoreTemp "data\atestados.db") -Destination (Join-Path $projectRoot "data\atestados.db") -Force
Get-ChildItem -LiteralPath (Join-Path $restoreTemp "data\uploads") -File -ErrorAction SilentlyContinue | Copy-Item -Destination (Join-Path $projectRoot "data\uploads") -Force
Remove-Item -LiteralPath $restoreTemp -Recurse -Force
Write-Host "Backup restaurado. Reinicie o servidor."
