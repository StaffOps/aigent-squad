# syntax=docker/dockerfile:1
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl git openssh-client && rm -rf /var/lib/apt/lists/*
RUN mkdir -p -m 0700 ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=ssh pip install --no-cache-dir -r requirements.txt

RUN useradd -r -u 10001 appuser
USER appuser

COPY src/ ./src/
COPY agents/ ./agents/

ENV AGENTS_DIR=/app/agents

EXPOSE 8000
CMD ["python", "-m", "src.supervisor.server"]
