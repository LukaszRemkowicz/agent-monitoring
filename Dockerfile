FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY migrations ./migrations

FROM base AS development

RUN uv sync --frozen --no-cache

ENTRYPOINT ["uv", "run"]
CMD ["log_analysis", "--help"]

FROM base AS production

ENV UV_NO_DEV=1
ENV UV_FROZEN=1
ENV UV_NO_SYNC=1

RUN uv sync --frozen --no-dev --no-cache

ENTRYPOINT ["uv", "run"]
CMD ["log_analysis", "--help"]
