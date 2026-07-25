FROM python:3.11-slim

WORKDIR /app

# System deps for building any wheels that need compilation (e.g. some
# transitive crypto deps of Telethon) and CA certs for outbound HTTPS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Data volume: SQLite DB, logs, exports, Telethon session file.
RUN mkdir -p data/logs data/exports data/summaries

EXPOSE 8000

# The first run requires an interactive login code (and 2FA password, if
# set) unless a .session file is already present in ./data - see README.
CMD ["python", "-m", "app.main"]
