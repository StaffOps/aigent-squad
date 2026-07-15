# Canonical command surface (spec 36). Every golden path is a target here;
# AGENTS.md/QUICKSTART reference these instead of raw commands. CI calls the
# same targets (same-harness principle — spec 23 extended to the entrypoint).
.PHONY: up down smoke test test-one test-ci lint eval specs-status install-hooks help

help: ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-14s %s\n", $$1, $$2}'

up: ## Start the local two-tier stack (gateway :8000 → supervisor :8001)
	docker compose up -d
	@echo "waiting for gateway /ready…"
	@for i in $$(seq 1 30); do \
	  curl -sf http://localhost:8000/ready >/dev/null 2>&1 && { echo "✅ up — try: make smoke"; exit 0; }; \
	  sleep 2; \
	done; echo "❌ gateway not ready after 60s → docker compose logs gateway supervisor"; exit 1

down: ## Stop the stack (add V=1 to drop volumes)
	docker compose down $(if $(V),-v,)

smoke: ## Health + 1 real query + /v1/models against the local stack
	./scripts/smoke.sh

test: ## Full suite + coverage gate via Docker; auto-stubs the private otel dep
	./scripts/test-local.sh

test-one: ## Single file/pattern: make test-one FILE=tests/test_gateway_main.py
	./scripts/test-local.sh $(FILE)

test-ci: ## CI-identical pytest gate (assumes deps installed — used by CI)
	pytest tests/ --cov --cov-fail-under=90 --tb=short -q

lint: ## Ruff, CI-verbatim scope (run via Docker if no local ruff)
	@command -v ruff >/dev/null 2>&1 && ruff check src/ tests/ || \
	docker run --rm -v "$$(pwd):/app" -w /app python:3.11-slim \
	  sh -c "pip install -q ruff && ruff check src/ tests/"

eval: ## Quality eval T2 (spec 35) — golden sets + LLM judge, real Bedrock cost (~$1-3)
	./scripts/eval-local.sh

eval-rca: ## RCA scenario eval T8/T9 (spec 35 Phase 3) — fixture-fed, real Bedrock cost (~$0.50-1.50)
	./scripts/eval-rca-local.sh

specs-status: ## Spec status lint (spec 32) — not implemented yet
	@echo "spec 32 pending — see specs/32-spec-lifecycle-ssot/"; exit 1

install-hooks: ## One-time opt-in: enforce "docs ship with code" via a pre-commit hook (.githooks/)
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit
	@echo "✅ pre-commit hook installed (core.hooksPath=.githooks). Bypass per-commit: git commit --no-verify"
