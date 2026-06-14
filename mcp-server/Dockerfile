# syntax=docker/dockerfile:1
FROM python:3.12-alpine

RUN apk add --no-cache git openssh-client && mkdir -p -m 0700 ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=ssh pip install --no-cache-dir -r requirements.txt

COPY mcp-server/mcp-server.py .

CMD ["python", "mcp-server.py"]
