FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: libssl for psycopg/cryptography at runtime is included in
# python:3.12-slim; install OpenSSL explicitly to silence Prisma-style
# warnings from binary wheels that probe for it.
RUN apt-get update && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY app/ ./app/

# Bundled privacy policy (app/privacy.py serves it at /privacy).
COPY PRIVACY_POLICY.md .

# Tables are created at startup via lifespan (see app/main.py). For
# production, set SKIP_AUTO_CREATE_TABLES=1 and run migrations instead.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
