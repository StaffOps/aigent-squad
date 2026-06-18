# syntax=docker/dockerfile:1

# ── Stage 1: builder ──────────────────────────────────────────────────────────
# git + openssh needed only to install the private otel-helper dep via git+ssh.
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    git openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p -m 0700 ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts

WORKDIR /app

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY requirements.txt .
RUN --mount=type=ssh pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade "wheel>=0.46.2" "setuptools>=79.0.1"

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
# No build tools. apt-get upgrade picks up any OS patches available in Debian 13.
# Note: ncurses/sqlite/perl CVEs are status=affected (no Debian fix yet) — they
# are retained because perl-base is required by dpkg and sqlite by Python stdlib.
FROM python:3.11-slim AS runtime

RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

RUN useradd -r -u 10001 appuser

WORKDIR /app
COPY src/ ./src/
COPY agents/ ./agents/

USER appuser

ENV AGENTS_DIR=/app/agents
EXPOSE 8000
CMD ["python", "-m", "src.supervisor.server"]
