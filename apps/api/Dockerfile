FROM python:3.12-slim

# Pin uv version for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

WORKDIR /app

# Install deps before copying source — maximises layer cache hits on redeploy
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and complete the sync (installs the project itself)
COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Railway injects $PORT; fall back to 8000 for local docker runs
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
