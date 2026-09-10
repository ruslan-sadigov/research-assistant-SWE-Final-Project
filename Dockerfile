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
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home appuser

COPY --from=builder /app/.venv /app/.venv

# Temporary entry point until the real research CLI is integrated.
COPY demo_ai.py ./
COPY data/ ./data/

USER appuser

CMD ["python", "demo_ai.py", "--offline", "--limit", "5"]
