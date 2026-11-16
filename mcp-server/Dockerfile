# syntax=docker/dockerfile:1
FROM python:3.12-alpine

# git is needed for the public git+https otel-helper dependency (no SSH/credentials).
RUN apk add --no-cache git

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN adduser -D -u 10001 appuser
USER appuser

COPY mcp-server/mcp-server.py .

CMD ["python", "mcp-server.py"]
