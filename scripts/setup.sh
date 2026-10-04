#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "Python 3 is required to generate local infrastructure secrets." >&2
    exit 1
fi

"$PYTHON_BIN" scripts/prepare-local.py
docker compose up -d --build
echo "AgentGuard AI Pro: http://localhost:5000"
