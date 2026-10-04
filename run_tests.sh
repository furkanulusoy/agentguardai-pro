#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo "Python 3 is required to generate isolated local test configuration." >&2
    exit 1
fi

"$PYTHON_BIN" scripts/prepare-local.py
docker compose up -d --wait postgres
docker build -f apps/api/Dockerfile --target test -t agentguard-product-test .
docker run --rm \
    --network agentguard-product_default \
    -e AGENTGUARD_SECRET_FILE=/run/secrets/platform_config \
    -e DEPLOYMENT_MODE=local \
    -v "$(pwd):/app:ro" \
    -v "$(pwd)/.local/platform-config.json:/run/secrets/platform_config:ro" \
    --entrypoint python \
    agentguard-product-test -B scripts/check-security.py --all
