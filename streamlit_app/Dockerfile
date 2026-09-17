# Standalone image: this app only talks to the API over HTTP, so it needs
# none of the main project's dependencies (uv, ai/, researcher/).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

RUN useradd --create-home appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

USER appuser

EXPOSE 8501
ENTRYPOINT ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
