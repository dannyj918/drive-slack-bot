FROM python:3.11-slim

WORKDIR /app

# Chroma and pypdf have no extra system deps on slim, but keep curl for health checks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent volume mount point for RAG index + changes token
ENV CHROMA_DB_PATH=/data/chroma_db
ENV CHANGES_TOKEN_FILE=/data/changes_token.txt

CMD ["python", "bot.py"]
