$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
docker compose -f docker-compose.local.yml up -d --wait --wait-timeout 180 postgres
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL could not start; run scripts/start-local.ps1 first if local secrets are missing.' }
docker build -f apps/api/Dockerfile --target test -t agentguard-product-test .
if ($LASTEXITCODE -ne 0) { throw 'Test image build failed.' }
docker run --rm --network agentguard-product_default -e AGENTGUARD_SECRET_FILE=/run/secrets/platform_config -e DEPLOYMENT_MODE=local -v ($projectRoot + ':/app:ro') -v ($projectRoot + '/.local/platform-config.json:/run/secrets/platform_config:ro') --entrypoint python agentguard-product-test -B scripts/check-security.py --all
if ($LASTEXITCODE -ne 0) { throw 'The test suite did not pass; inspect its summary.' }
