# Production container for the real platform (apps/api) -- distinct
# from the repo-root Dockerfile, which packages the OLDER Flask
# reference deployment (server/app.py). Built from the repo root as
# context (docker-compose.yml does this) because apps/api imports
# infrastructure/, connectors/, and agentguard/ as siblings, not
# vendored copies.
FROM python:3.11-slim AS runtime

WORKDIR /app

# System deps for psycopg[binary]'s build step and asyncpg's C
# extensions -- removed after pip install so the final image doesn't
# carry a full build toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml LICENSE README.md ./
COPY agentguard ./agentguard
COPY apps ./apps
COPY infrastructure ./infrastructure
COPY connectors ./connectors
COPY alembic ./alembic
COPY alembic.ini ./

# gunicorn is not a project dependency (sdk/python and the reference
# Flask deployment don't need it) -- installed here only, scoped to
# this image, the same way sdk/typescript's tsx is a devDependency
# scoped to its own package.
RUN pip install --no-cache-dir ".[api]" "gunicorn>=22.0"

# Patch inherited build tooling and remove compiler packages from the runtime.
RUN pip install --no-cache-dir --upgrade "pip>=26.2" "setuptools>=83.0" \
    && apt-get purge -y --auto-remove build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

RUN useradd --create-home --uid 10001 agentguard
USER agentguard
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]


FROM runtime AS test
USER root
RUN pip install --no-cache-dir flask mcp
USER agentguard
