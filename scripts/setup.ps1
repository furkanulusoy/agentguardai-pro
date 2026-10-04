$ErrorActionPreference = 'Stop'

# Backward-compatible entry point. The canonical Windows startup script
# generates .local secrets without reading or modifying dotenv files.
& (Join-Path $PSScriptRoot 'start-local.ps1')
if ($LASTEXITCODE -ne 0) { throw 'AgentGuard local startup failed.' }
