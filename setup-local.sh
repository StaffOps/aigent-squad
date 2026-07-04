#!/usr/bin/env bash
# Thin wrapper kept for backwards compatibility — the canonical entrypoint is
# the Makefile (spec 36): make up && make smoke
set -euo pipefail
cd "$(dirname "$0")"

echo "🚀 AIgent-squad — local setup"

command -v docker >/dev/null 2>&1 || { echo "❌ Docker not found."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "❌ Docker Compose v2 not found (docker compose)."; exit 1; }
command -v make >/dev/null 2>&1 || { echo "❌ make not found."; exit 1; }
[ -d "$HOME/.aws" ] || echo "⚠️  ~/.aws not found — Bedrock/AWS queries will fail (health/tests still work)."

make up
make smoke || { echo "⚠️  smoke failed — see messages above (AGENTS.md → Playbook)."; exit 1; }

echo ""
echo "Next steps:  make help   |   make test   |   make lint"
