FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY tests ./tests
COPY migrations ./migrations

RUN uv sync --frozen --no-cache

ENTRYPOINT ["uv", "run"]
CMD ["log_analysis", "--help"]
