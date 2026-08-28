# Dockerfile — TikDown-rs (§11)
# Multi-stage: builder (python:3.13-slim + uv) → runtime (python:3.13-slim + ffmpeg)
# Patrón oficial de uv (L-K1: NO usar builder distroless uv:latest — venv colgante).

# ============ STAGE 1: builder ============
FROM python:3.13-slim AS builder

# uv desde la imagen oficial (solo binarios)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# L-K3: ENV sin comentarios inline — los comentarios van en líneas propias
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0
# UV_PYTHON_DOWNLOADS=0: usa el CPython de la imagen base (sin descarga de intérprete)

WORKDIR /app

# 1) Lockfile + pyproject primero (caché de capas por lockfile)
COPY pyproject.toml uv.lock README.md ./

# 2) Dependencias sin el proyecto (aprovecha caché; README exigido por hatchling, F-04)
RUN uv sync --frozen --no-editable --no-install-project

# 3) Código del proyecto después
COPY src/ ./src/
RUN uv sync --frozen --no-editable

# ============ STAGE 2: runtime ============
FROM python:3.13-slim AS runtime

# ffmpeg/ffprobe: dependencia dura (T46)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Solo el venv resuelto (sin uv ni caché de build)
COPY --from=builder /app/.venv /app/.venv

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
ENV DATA_DIR=/app/data

# Directorio de datos persistente (DB, fernet.key, archive, vídeos — T8)
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 0

# Entrypoint del daemon; HEALTHCHECK con --start-period >= selfcheck de arranque (T50)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["tikdown-rs", "daemon", "healthcheck"]

CMD ["tikdown-rs", "daemon", "run"]
