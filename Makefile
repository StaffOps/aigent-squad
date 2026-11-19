# Canonical command surface (spec 36). Every golden path is a target here;
# AGENTS.md/QUICKSTART reference these instead of raw commands. CI calls the
# same targets (same-harness principle — spec 23 extended to the entrypoint).
.PHONY: up down smoke test test-one test-ci lint typecheck eval specs-status pin-check mcp-rbac-audit harness-score install-hooks verify-release scan capability-validate load-test help

# AI-agent harness maturity floor (harness-score L0-L4). Raise this ONLY after
# the score genuinely clears the next level — never to make a red CI go green.
MIN_LEVEL ?= 1
# Pinned for determinism: an unpinned scanner can change what the repo scores
# between runs, which defeats the point of gating on it.
HARNESS_SCORE_VERSION ?= 1.5.2

load-test: ## Run k6 basic load test against local stack
	k6 run tests/load/scenario_basic.js

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
	pytest tests/ --cov --cov-fail-under=90 --tb=short -q $(PYTEST_FLAGS)

lint: ## Ruff, CI-verbatim scope (run via Docker if no local ruff)
	@command -v ruff >/dev/null 2>&1 && ruff check src/ tests/ || \
	docker run --rm -v "$$(pwd):/app" -w /app python:3.11-slim \
	  sh -c "pip install -q ruff && ruff check src/ tests/"

typecheck: ## Mypy strict-ish gate (run via Docker if no local mypy)
	@command -v mypy >/dev/null 2>&1 && mypy src/ || \
	docker run --rm -v "$$(pwd):/app" -w /app python:3.11-slim \
	  sh -c "pip install -q mypy && mypy src/"

eval: ## Quality eval T2 (spec 35) — golden sets + LLM judge, real Bedrock cost (~$1-3)
	./scripts/eval-local.sh

eval-rca: ## RCA scenario eval T8/T9 (spec 35 Phase 3) — fixture-fed, real Bedrock cost (~$0.50-1.50)
	./scripts/eval-rca-local.sh

specs-status: ## Spec status SSOT lint (spec 32) — frontmatter consistency + ROADMAP table check
	@python3 -c "import yaml" >/dev/null 2>&1 && python3 scripts/specs_status.py || \
	docker run --rm -v "$$(pwd):/app" -w /app python:3.11-slim \
	  sh -c "pip install -q pyyaml==6.0.2 && python3 scripts/specs_status.py"

pin-check: ## Fail if any GitHub Action is not pinned to a 40-hex commit SHA (spec 45 T2.2)
	@# INVERSE match on purpose: fail on anything after `@` that is not 40 hex chars. A denylist
	@# of tag shapes misses semver like @v4.1.0. Local (./…) and docker:// refs have no @ref.
	@if grep -rPn 'uses:\s+[^#\s]+@(?![0-9a-f]{40}\b)' .github/workflows/; then \
	  echo ""; \
	  echo "ERROR: unpinned action ref(s) above. Pin to a full 40-char commit SHA:"; \
	  echo "  uses: owner/repo@<40-hex-sha> # vX.Y.Z"; \
	  echo "Resolve with: curl -s https://api.github.com/repos/<owner>/<repo>/git/ref/tags/<tag>"; \
	  echo "(if .object.type is 'tag', deref via /git/tags/<sha> to get the commit)"; \
	  exit 1; \
	else \
	  echo "pin-check OK — every action ref is a 40-hex commit SHA"; \
	fi

mcp-rbac-audit: ## Prove MCP ServiceAccount is read-only (spec 37 gate). SA=<name> NS=<ns> [CTX=<ctx>]
	@test -n "$(SA)" || { echo "ERROR: SA env var required (ServiceAccount name)"; exit 1; }
	@test -n "$(NS)" || { echo "ERROR: NS env var required (namespace)"; exit 1; }
	@python3 scripts/mcp_rbac_audit.py --serviceaccount "$(SA)" --namespace "$(NS)" \
	  $(if $(CTX),--context "$(CTX)",)

harness-score: ## AI-agent harness maturity gate (L0-L4); floor set by MIN_LEVEL, report-only with MIN_LEVEL=0
	@command -v npx >/dev/null 2>&1 && npx --yes harness-score@$(HARNESS_SCORE_VERSION) --min-level $(MIN_LEVEL) || \
	docker run --rm -v "$$(pwd):/app" -w /app node:20-slim \
	  npx --yes harness-score@$(HARNESS_SCORE_VERSION) --min-level $(MIN_LEVEL)

install-hooks: ## One-time opt-in: enforce "docs ship with code" via a pre-commit hook (.githooks/)
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit
	@echo "✅ pre-commit hook installed (core.hooksPath=.githooks). Bypass per-commit: git commit --no-verify"


capability-validate: ## Validate all agent configs for capability tier consistency (spec 43)
	@python3 -c "import yaml" >/dev/null 2>&1 && python3 scripts/capability_validate.py || \
	docker run --rm -v "$$(pwd):/app" -w /app python:3.11-slim \
	  sh -c "pip install -q pyyaml==6.0.2 pydantic==2.11.7 && python3 scripts/capability_validate.py"

scan: ## Run Trivy vulnerability scan locally (mirrors CI dep_scan + build gate)
	@docker build -t aigent-squad:scan-local . 2>/dev/null
	@docker run --rm -v "$(pwd)/.trivyignore.yaml:/root/.trivyignore.yaml" \
	  -v /var/run/docker.sock:/var/run/docker.sock \
	  aquasec/trivy:latest image \
	  --severity CRITICAL,HIGH --exit-code 1 \
	  --ignorefile /root/.trivyignore.yaml \
	  aigent-squad:scan-local

verify-release: ## Verify a released image signature + attestation: make verify-release DIGEST=sha256:abc...
	@if [ -z "$(DIGEST)" ]; then echo "Usage: make verify-release DIGEST=sha256:..."; exit 1; fi
	@echo "Verifying ghcr.io/staffops/aigent-squad@$(DIGEST)..."
	cosign verify \
	  --certificate-identity-regexp "^https://github.com/StaffOps/aigent-squad/" \
	  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
	  "ghcr.io/staffops/aigent-squad@$(DIGEST)"
	gh attestation verify \
	  "oci://ghcr.io/staffops/aigent-squad@$(DIGEST)" \
	  --owner StaffOps
	@echo "✅ Signature + provenance verified"
