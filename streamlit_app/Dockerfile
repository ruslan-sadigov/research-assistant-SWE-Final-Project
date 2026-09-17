# Standalone image: this app only talks to the API over HTTP, so it needs
# none of the main project's packages (ai/, researcher/, webapi/) - only its
# own isolated uv project (pyproject.toml + uv.lock in this directory).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN useradd --create-home appuser

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY . .

USER appuser

EXPOSE 8501
ENTRYPOINT ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
