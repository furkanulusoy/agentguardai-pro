# Legacy Flask reference image. The supported product stack is defined by the
# repository root docker-compose.yml and apps/api/Dockerfile.
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml LICENSE README.md ./
COPY agentguard ./agentguard
COPY mock_services ./mock_services
COPY server ./server
COPY examples ./examples

RUN pip install --no-cache-dir ".[server]"

EXPOSE 8000

CMD ["python3", "-m", "server.app"]
