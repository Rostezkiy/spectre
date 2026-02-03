FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

WORKDIR /app

RUN apt-get update && apt-get install -y \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install --with-deps chromium

COPY . .

RUN mkdir -p /data

ENV SPECTRE_DB_PATH=/data/spectre.duckdb
ENV SPECTRE_CONFIG_PATH=/app/spectre.yaml
ENV SPECTRE_HOST=0.0.0.0
ENV SPECTRE_PORT=8000

EXPOSE 8000

CMD ["spectre", "serve", "--host", "0.0.0.0", "--port", "8000"]