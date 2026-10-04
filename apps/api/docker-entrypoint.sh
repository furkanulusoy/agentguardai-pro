#!/bin/sh
set -e
alembic upgrade head
exec gunicorn apps.api.main:app --worker-class uvicorn.workers.UvicornWorker --workers 1 --bind 0.0.0.0:8000 --timeout 120 --error-logfile -
