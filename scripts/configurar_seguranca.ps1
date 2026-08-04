param(
    [string]$Usuario,
    [string]$Nome,
    [switch]$GerarSenha
)
$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) { Copy-Item (Join-Path $projectRoot ".env.example") $envFile }
$lines = Get-Content -LiteralPath $envFile
$currentSecret = $lines | Where-Object { $_ -match '^APP_SECRET_KEY=' } | Select-Object -First 1
if (-not $currentSecret -or $currentSecret -eq 'APP_SECRET_KEY=gere_com_configurar_seguranca.ps1') {
    $bytes = New-Object byte[] 48
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    $generator.GetBytes($bytes)
    $generator.Dispose()
    $secret = [Convert]::ToBase64String($bytes)
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match '^APP_SECRET_KEY=') { "APP_SECRET_KEY=$secret"; $found = $true } else { $line }
    }
    if (-not $found) { $updated += "APP_SECRET_KEY=$secret" }
    Set-Content -LiteralPath $envFile -Value $updated -Encoding UTF8
} else {
    Write-Host "APP_SECRET_KEY existente preservada."
}
Set-Location -LiteralPath $projectRoot
if ($Usuario -and $Nome -and $GerarSenha) {
    & ".\.venv\Scripts\python.exe" (Join-Path $PSScriptRoot "criar_admin.py") --usuario $Usuario --nome $Nome --gerar-senha
} else {
    & ".\.venv\Scripts\python.exe" (Join-Path $PSScriptRoot "criar_admin.py")
}
