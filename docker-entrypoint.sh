#!/bin/sh
set -e
# Apply schema migrations, then serve. Managed hosts inject $PORT.
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-1}" --proxy-headers --forwarded-allow-ips="*"
