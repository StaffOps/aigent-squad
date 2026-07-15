#!/usr/bin/env bash
# Local test harness (spec 36 T2) — runs the SAME pytest gate as CI, via Docker
# (python:3.11-slim, per steering), handling the private `otel-helper` dep
# automatically: if it can't be installed (no SSH/deploy key), a no-op stub is
# generated and the run proceeds — LOUDLY flagged at the end.
#
# Usage:
#   scripts/test-local.sh                 # full suite + coverage gate (>=90%)
#   scripts/test-local.sh tests/test_x.py # single file/pattern (no coverage gate)
set -euo pipefail
cd "$(dirname "$0")/.."

IMG="python:3.11-slim"
STUB_DIR=".local-stubs"
PYTEST_TARGET="${1:-tests/}"
if [ "$#" -ge 1 ]; then
  PYTEST_CMD="pytest ${PYTEST_TARGET} --tb=short -q"
else
  # CI-identical gate (see .github/workflows/test.yml)
  PYTEST_CMD="pytest tests/ --cov --cov-fail-under=90 --tb=short -q"
fi

# The local harness ALWAYS stubs the private otel-helper dep: the test
# container (python:3.11-slim) ships without git and without credentials, so a
# git+https private requirement can never install there. CI is the only place
# the real dependency is validated (deploy key on the runner) — hence the loud
# warning at the end. This is deliberate, not a fallback (spec 36 Decision 4).
STUBBED=1
./scripts/stub-otel.sh "${STUB_DIR}"
grep -v "^otel-helper @" requirements.txt > "${STUB_DIR}/requirements.no-otel.txt"
REQ="${STUB_DIR}/requirements.no-otel.txt"
EXTRA_PYPATH="/app/${STUB_DIR}"

RC=0
docker run --rm -v "$(pwd):/app" -w /app \
  -e PYTHONPATH="/app${EXTRA_PYPATH:+:${EXTRA_PYPATH}}" \
  -e REDIS_HOST=localhost -e SERVICE_NAME=test \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 -e AWS_REGION=us-east-1 \
  "${IMG}" sh -c "
    pip install --quiet -r ${REQ} && \
    pip install --quiet pytest pytest-asyncio pytest-cov 'fakeredis[lua]>=2.36,<3' 'respx>=0.22,<0.23' opentelemetry-api && \
    ${PYTEST_CMD}
  " || RC=$?

if [ "${STUBBED}" -eq 1 ]; then
  echo ""
  echo "⚠️  This run used the otel_helper STUB (private dep unreachable)."
  echo "   Telemetry wiring and the real dependency set were NOT validated."
  echo "   After pushing, confirm CI: gh run list --branch \$(git branch --show-current) -L 3"
fi
exit ${RC}
