# syntax=docker/dockerfile:1

# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Build tools and SSH are builder-only — absent from the runtime image.
FROM python:3.11-alpine@sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4 AS builder

RUN apk add --no-cache \
    gcc musl-dev libffi-dev openssl-dev \
    git

WORKDIR /app

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# No credential mount needed: otel-helper (the one git+https dependency in
# requirements.txt) has lived in a public repo since 2026-07-14 (commit
# d8dc822) — a bare `pip install` resolves it with no auth. This used to
# require a `--secret id=github_token` build arg back when that dependency
# was private; removed 2026-07-15 (BACKLOG B-28) so a fresh clone/fork
# builds with a plain `docker build .`, no token needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade "wheel>=0.46.2" "setuptools>=79.0.1"

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
# Alpine: no perl, no ncurses, no apt — drastically smaller CVE surface.
FROM python:3.11-alpine@sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4 AS runtime

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

RUN adduser -D -u 10001 appuser

WORKDIR /app
COPY src/ ./src/
COPY agents/ ./agents/
COPY skills/ ./skills/

USER appuser

# One image, two tiers (spec 31). The runtime command is overridden per tier by
# the deployment (Helm `command:` / compose `command:`):
#   gateway    → python -m src.gateway.main      (port 8000, public front door)
#   supervisor → python -m src.supervisor.server (port 8001, backend, default below)
ENV AGENTS_DIR=/app/agents
EXPOSE 8000 8001
CMD ["python", "-m", "src.supervisor.server"]
