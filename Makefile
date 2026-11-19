# Canonical command surface (spec 36). Every golden path is a target here;
# AGENTS.md/QUICKSTART reference these instead of raw commands. CI calls the
# same targets (same-harness principle — spec 23 extended to the entrypoint).
.PHONY: up down smoke test test-one test-ci lint typecheck bandit docs eval specs-status pin-check mcp-rbac-audit harness-score install-hooks verify-release scan scan-fs capability-validate load-test openapi help

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
	docker compose up -d --build   # rebuild from the working tree (cached) — never run a stale image
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

# Tool versions pinned in requirements-lint.txt (same file CI installs from).
RUFF_VERSION := $(shell sed -n 's/^ruff==//p' requirements-lint.txt)
MYPY_VERSION := $(shell sed -n 's/^mypy==//p' requirements-lint.txt)
BANDIT_VERSION := $(shell sed -n 's/^bandit==//p' requirements-lint.txt)
# Docs builder pinned in requirements-docs.txt (same file docs.yml installs from).
MKDOCS_MATERIAL_VERSION := $(shell sed -n 's/^mkdocs-material==//p' requirements-docs.txt)
# Trivy CLI = the version trivy-action v0.36.0 (SHA-pinned in test/build/release.yml)
# installs by default (its `version` input). Bump together with that action pin.
TRIVY_VERSION ?= 0.70.0
# Named volume keeps the ~130 MiB vuln DB between runs (CI downloads it fresh each job;
# the DB content, not the cache, decides findings).
TRIVY_CACHE := -v aigent-trivy-cache:/root/.cache/trivy

# A local binary is used ONLY when its version equals the pin; otherwise Docker with
# the pinned version. No `local || docker` fallback: a failing local run must fail,
# not be silently re-run under a different version.
lint: ## Ruff, CI-verbatim scope (pinned version; local binary only if it matches the pin)
	@if command -v ruff >/dev/null 2>&1 && [ "$$(ruff --version | cut -d' ' -f2)" = "$(RUFF_VERSION)" ]; then \
	  ruff check src/ tests/; \
	else \
	  docker run --rm -v "$$(pwd):/app" -w /app python:3.11-slim \
	    sh -c "pip install -q -r requirements-lint.txt && ruff check src/ tests/"; \
	fi

typecheck: ## Mypy gate (pinned version; local binary only if it matches the pin)
	@if command -v mypy >/dev/null 2>&1 && [ "$$(mypy --version | cut -d' ' -f2)" = "$(MYPY_VERSION)" ]; then \
	  mypy src/; \
	else \
	  docker run --rm -v "$$(pwd):/app" -w /app python:3.11-slim \
	    sh -c "pip install -q -r requirements-lint.txt && mypy src/"; \
	fi

bandit: ## Bandit SAST, CI-verbatim (sast.yml); pinned version, local binary only if it matches the pin
	@if command -v bandit >/dev/null 2>&1 && [ "$$(bandit --version | head -1 | cut -d' ' -f2)" = "$(BANDIT_VERSION)" ]; then \
	  bandit -r src/ mcp-server/ --severity-level medium --confidence-level medium; \
	else \
	  docker run --rm -v "$$(pwd):/app" -w /app python:3.11-slim \
	    sh -c "pip install -q -r requirements-lint.txt && bandit -r src/ mcp-server/ --severity-level medium --confidence-level medium"; \
	fi

# Python 3.12 on purpose: docs.yml builds the site on 3.12 (docs-only, not the app runtime).
docs: ## Strict MkDocs build, CI-verbatim (docs.yml build_check); pinned mkdocs-material, output in site/
	@if command -v mkdocs >/dev/null 2>&1 && pip show mkdocs-material 2>/dev/null | grep -qx "Version: $(MKDOCS_MATERIAL_VERSION)"; then \
	  mkdocs build --strict --site-dir site; \
	else \
	  docker run --rm -v "$$(pwd):/app" -w /app python:3.12-slim \
	    sh -c "pip install -q -r requirements-docs.txt && mkdocs build --strict --site-dir site"; \
	fi

eval: ## Quality eval T2 (spec 35) — golden sets + LLM judge, real Bedrock cost (~$1-3)
	./scripts/eval-local.sh

eval-rca: ## RCA scenario eval T8/T9 (spec 35 Phase 3) — fixture-fed, real Bedrock cost (~$0.50-1.50)
	./scripts/eval-rca-local.sh

openapi: ## Regenerate openapi/{gateway,supervisor}.json (Backstage API entities; CI fails on drift)
	@# Docker, same interpreter as the CI test job; imports log to stdout, so specs go to files.
	docker run --rm -v "$$(pwd):/app" -w /app -e REDIS_HOST=localhost -e AWS_REGION=us-east-1 python:3.11-slim \
	  sh -c "apt-get -qq update >/dev/null && apt-get -qq install -y git >/dev/null 2>&1 && \
	         pip install -q -r requirements.txt 2>/dev/null && \
	         python scripts/export_openapi.py src.gateway.main:app openapi/gateway.json >/dev/null && \
	         python scripts/export_openapi.py src.supervisor.server:app openapi/supervisor.json >/dev/null"
	@echo "openapi/ regenerated"

specs-status: ## Spec status SSOT lint (spec 32) — frontmatter consistency + ROADMAP table check
	@python3 -c "import yaml" >/dev/null 2>&1 && python3 scripts/specs_status.py || \
	docker run --rm -v "$$(pwd):/app" -w /app python:3.11-slim \
	  sh -c "pip install -q pyyaml==6.0.2 && python3 scripts/specs_status.py"

pin-check: ## Fail if any GitHub Action is not pinned to a 40-hex commit SHA (spec 45 T2.2)
	@# INVERSE match on purpose: fail on anything after `@` that is not 40 hex chars. A denylist
	@# of tag shapes misses semver like @v4.1.0. Local (./…) and docker:// refs have no @ref.
	@# perl, not `grep -P`: macOS grep has no -P, exits 2, and the old `if` read that as
	@# "no match" -> a false OK. Any scanner error now FAILS the gate (rc 2).
	@files=$$(find .github/workflows -type f \( -name '*.yml' -o -name '*.yaml' \)); \
	[ -n "$$files" ] || { echo "pin-check: no workflow files found"; exit 2; }; \
	perl -ne 'if (/uses:\s+[^#\s]+@(?![0-9a-f]{40}\b)/) { print "$$ARGV:$$.: $$_"; $$bad = 1 } close ARGV if eof; END { exit($$bad ? 3 : 0) }' $$files; rc=$$?; \
	if [ $$rc -eq 3 ]; then \
	  echo ""; \
	  echo "ERROR: unpinned action ref(s) above. Pin to a full 40-char commit SHA:"; \
	  echo "  uses: owner/repo@<40-hex-sha> # vX.Y.Z"; \
	  echo "Resolve with: curl -s https://api.github.com/repos/<owner>/<repo>/git/ref/tags/<tag>"; \
	  echo "(if .object.type is 'tag', deref via /git/tags/<sha> to get the commit)"; \
	  exit 1; \
	elif [ $$rc -ne 0 ]; then \
	  echo "pin-check: scanner failed (rc=$$rc) - gate NOT passed"; exit 2; \
	else \
	  echo "pin-check OK - every action ref is a 40-hex commit SHA"; \
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

scan: ## Trivy image scan (mirrors the build.yml/release.yml gate); build errors are shown and fail
	docker build -t aigent-squad:scan-local .
	docker run --rm $(TRIVY_CACHE) -v "$$(pwd)/.trivyignore.yaml:/root/.trivyignore.yaml:ro" \
	  -v /var/run/docker.sock:/var/run/docker.sock \
	  aquasec/trivy:$(TRIVY_VERSION) image \
	  --severity CRITICAL,HIGH --exit-code 1 \
	  --ignorefile /root/.trivyignore.yaml \
	  aigent-squad:scan-local

scan-fs: ## Trivy filesystem/dependency scan (mirrors test.yml dep_scan); no image build
	docker run --rm $(TRIVY_CACHE) -v "$$(pwd):/src:ro" -w /src \
	  aquasec/trivy:$(TRIVY_VERSION) fs \
	  --severity CRITICAL,HIGH --exit-code 1 \
	  --ignorefile .trivyignore.yaml \
	  .

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
