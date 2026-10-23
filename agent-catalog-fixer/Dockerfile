FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY app/ ./app/

# Bundled privacy policy (app/privacy.py serves it at /privacy).
COPY PRIVACY_POLICY.md .

# SQLite dev.db is created at startup; production should set DATABASE_URL to
# Postgres and SKIP_AUTO_CREATE_TABLES=1. PORT is provided by the host.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
