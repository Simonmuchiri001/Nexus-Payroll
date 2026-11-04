# syntax=docker/dockerfile:1.7
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=180

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY desktop/pyproject.toml desktop/pyproject.toml
RUN uv sync --frozen --no-dev --package nexus-api --no-install-project --no-editable

COPY backend backend
RUN uv sync --frozen --no-dev --package nexus-api --no-editable

FROM python:3.13-slim AS runtime

RUN groupadd --system nexus \
    && useradd --system --gid nexus --home-dir /app --shell /usr/sbin/nologin nexus

WORKDIR /app
COPY --from=builder --chown=nexus:nexus /app/.venv /app/.venv
COPY --from=builder --chown=nexus:nexus /app/backend /app/backend

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER nexus
WORKDIR /app/backend
EXPOSE 8000

CMD ["uvicorn", "nexus_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
