$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
# Generates new local infrastructure keys; never reads or modifies .env.
if (-not (Test-Path -LiteralPath '.local')) { New-Item -ItemType Directory -Path '.local' | Out-Null }
function New-Key([int]$Bytes) {
    $buffer = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    return [Convert]::ToBase64String($buffer).Replace('+','-').Replace('/','_')
}
if ((Test-Path -LiteralPath '.local/platform-config.json') -ne (Test-Path -LiteralPath '.local/postgres-password')) {
    throw 'Incomplete local configuration; restore both files. Keys were not regenerated.'
}
if (-not (Test-Path -LiteralPath '.local/platform-config.json')) {
    $dbPassword = (New-Key 32).TrimEnd('=')
    $config = @{
        database_url = "postgresql://agentguard:$dbPassword@postgres:5432/agentguard"
        jwt_secret_key = New-Key 48
        secret_encryption_key = New-Key 32
    }
    [IO.File]::WriteAllText((Join-Path $PWD '.local/postgres-password'), $dbPassword)
    [IO.File]::WriteAllText((Join-Path $PWD '.local/platform-config.json'), ($config | ConvertTo-Json))
}
docker compose -f docker-compose.local.yml up -d --build --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) { throw 'Local stack could not start.' }
Write-Host 'AgentGuard: http://localhost:5000'
