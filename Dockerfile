# Multi-stage build following the official uv pattern (specs §14.1).
# T-DEPLOY-14: builder MUST be python:3.13-slim, never the distroless uv image
# (its copied .venv references a missing interpreter).
FROM python:3.13-slim AS builder

# Official recommended way to get uv into the build.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, so this layer is cached when only the source changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-editable --no-install-project

COPY src ./src
RUN uv sync --frozen --no-editable

# T-DEPLOY-14: slim runtime with a real interpreter for the copied venv.
FROM python:3.13-slim AS runtime

# T-DEPLOY-10: yt-dlp post-processing needs ffmpeg on the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system tikdown \
    && useradd --system --gid tikdown --create-home tikdown

COPY --from=builder --chown=tikdown:tikdown /app/.venv /app/.venv

# WORKDIR must precede the relative COPYs below, or './' lands in '/' instead
# of '/app' (symptom: 'alembic.ini not found' at first boot, T-DEPLOY-5).
WORKDIR /app

# T-DEPLOY-5: non-Python resources copied explicitly; without them the first
# boot dies with FileNotFoundError on alembic.ini.
COPY --chown=tikdown:tikdown alembic.ini ./
COPY --chown=tikdown:tikdown alembic/ ./alembic/

ENV DATA_DIR=/app/data
ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /app/data && chown tikdown:tikdown /app/data
USER tikdown

# T-CLI-5: verified against the real typer command tree (tests/test_docker_config.py).
# start-period kept conservative (60s): migrations run on first boot.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD ["tikdown-rs", "daemon", "healthcheck"]

CMD ["tikdown-rs", "daemon", "run"]
