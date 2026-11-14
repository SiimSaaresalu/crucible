# syntax=docker/dockerfile:1
# uv 0.12.18 has no python3.14 variant tag, so copy the pinned uv binary onto the Python image.
FROM python:3.14-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=python3.14
WORKDIR /app

# Dependencies first, so this layer caches until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-bookworm AS runtime
ARG NIGHTSHIFT_GIT_COMMIT=unknown
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin nightshift
COPY --from=builder /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PORT=8000 \
    RUNS_DIR=/tmp/runs \
    PYTHONUNBUFFERED=1 \
    NIGHTSHIFT_GIT_COMMIT=${NIGHTSHIFT_GIT_COMMIT}
USER nightshift
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/healthz' % os.environ['PORT'], timeout=2)"]
CMD ["sh", "-c", "exec uvicorn --factory nightshift.server.app:create_app --host 0.0.0.0 --port ${PORT}"]
