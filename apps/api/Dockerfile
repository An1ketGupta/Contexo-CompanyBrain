FROM python:3.12-slim

# Pin uv version for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

# Minimal OS hygiene + curl for HEALTHCHECK. python:3.12-slim already has
# libc/ssl needed by httpx; keep the layer thin.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps before copying source — maximises layer cache hits on redeploy.
# Pin the exact lockfile (no `uv.lock*` glob) so a missing lockfile fails the
# build instead of silently resolving fresh versions.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and complete the sync (installs the project itself)
COPY . .
RUN uv sync --frozen --no-dev

# Non-root runtime user. Create after install so the uv cache (in /root) is
# discarded and the runtime image never carries credentials with root.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/tmp \
    && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

EXPOSE 8000

# Container-level health probe. Railway has its own probe configured against
# /health, but a local `docker run` benefits from the same signal.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent --show-error "http://127.0.0.1:${PORT:-8000}/health" || exit 1

# Railway injects $PORT; fall back to 8000 for local docker runs
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
