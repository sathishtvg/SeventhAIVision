# Shared by api, ingestion, and scheduler (plan §12) — they differ only by
# the command docker-compose.yml gives each container, not by image content.
#
# Dependency-first layer ordering: copy pyproject.toml files + minimal stubs
# before copying all source so the `pip install` layer is cached even when
# only .py files change. Only pyproject.toml changes trigger a slow reinstall.
FROM --platform=linux/amd64 python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        ffmpeg \
        curl \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── 1. Install deps first (cache-stable as long as pyproject.toml is unchanged) ──
COPY shared/pyproject.toml /app/shared/pyproject.toml
COPY backend/pyproject.toml /app/backend/pyproject.toml

# Stubs match actual package dirs: shared → inner "shared/", backend → "app/"
# (pyproject.toml packages.find: shared has include=["shared*"], backend has include=["app*"])
RUN mkdir -p /app/shared/shared \
    && touch /app/shared/shared/__init__.py \
    && mkdir -p /app/backend/app \
    && touch /app/backend/app/__init__.py

# This install pulls several hundred MB (torch, insightface, onnxruntime), which
# makes it the one layer most likely to die on a slow/flaky link — it has failed
# here with a PyPI ReadTimeoutError. Two mitigations:
#   * --retries/--timeout so a single slow response doesn't abort the build;
#   * a BuildKit cache mount instead of --no-cache-dir, so a retry reuses
#     already-downloaded wheels rather than starting the download over. The
#     cache lives outside the image, so image size is unchanged.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 10 --timeout 120 -e /app/shared && \
    pip install --retries 10 --timeout 120 -e "/app/backend[dev]"

# ── 2. Overlay full source (invalidates only the COPY layer, not pip install) ──
COPY shared/ /app/shared/
COPY backend/ /app/backend/

WORKDIR /app/backend

EXPOSE 8000 8001

# Non-root user — CIS Docker Benchmark 4.1 (Gap 30)
RUN useradd -m -u 1001 -s /bin/bash appuser
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
