# Dependency installation and package building.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./

# Cache dependency installation separately from application changes.
RUN uv sync --locked --no-dev --no-install-project

COPY ai/ ./ai/
COPY src/ ./src/

# Install the application normally, without an editable source link.
RUN uv sync --locked --no-dev --no-editable


# Development tools and tests.
FROM builder AS test

COPY . .
RUN uv sync --locked --no-editable

CMD ["uv", "run", "--locked", "--no-sync", "python", "-m", "pytest", "-v"]


# Application runtime without development tools or uv.
FROM python:3.12-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    HOME="/home/appuser" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home appuser \
    && install -d -o appuser -g appuser /home/appuser/.cache/research-assistant

COPY --from=builder /app/.venv /app/.venv

USER appuser

ENTRYPOINT ["python", "-m", "researcher"]
CMD ["--help"]


# HTTP API runtime, sharing the same installed package as the CLI.
FROM python:3.12-slim AS api

ENV PATH="/app/.venv/bin:$PATH" \
    HOME="/home/appuser" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home appuser \
    && install -d -o appuser -g appuser /home/appuser/.cache/research-assistant

COPY --from=builder /app/.venv /app/.venv

USER appuser

EXPOSE 8000
ENTRYPOINT ["uvicorn", "webapi.api:app", "--host", "0.0.0.0", "--port", "8000"]
