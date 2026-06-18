# syntax=docker/dockerfile:1

# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Build tools and SSH are builder-only — absent from the runtime image.
FROM python:3.11-alpine AS builder

RUN apk add --no-cache \
    gcc musl-dev libffi-dev openssl-dev \
    git openssh-client

RUN mkdir -p -m 0700 ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts

WORKDIR /app

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY requirements.txt .
RUN --mount=type=ssh pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade "wheel>=0.46.2" "setuptools>=79.0.1"

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
# Alpine: no perl, no ncurses, no apt — drastically smaller CVE surface.
FROM python:3.11-alpine AS runtime

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

RUN adduser -D -u 10001 appuser

WORKDIR /app
COPY src/ ./src/
COPY agents/ ./agents/

USER appuser

ENV AGENTS_DIR=/app/agents
EXPOSE 8000
CMD ["python", "-m", "src.supervisor.server"]
