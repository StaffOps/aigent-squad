# AIgent-Squad — Claude Code Guide

Multi-agent AI system for AWS/Kubernetes operations: **1 supervisor + 5 specialist
agents** (aws, kubernetes, finops, devops, observability) + an MCP server.
Config-driven, Bedrock-direct, **read-only by default**.

> **Status**: Phase 0 (stabilization), pre-release `0.x`. Not production. Known
> blockers are tracked in [`.kiro/specs/AUDIT.md`](.kiro/specs/AUDIT.md); the plan
> is in [`.kiro/specs/ROADMAP.md`](.kiro/specs/ROADMAP.md). Work on branch `dev`.

This repo is **spec-driven**: `.kiro/` is the single source of truth. This
`CLAUDE.md` is the Claude Code entrypoint and imports the same steering rules Kiro
uses, so both tools share one definition (no drift).

---

## Build, test, lint — ALL via Docker

The local machine has **no Python SDK**. Every build/test/lint runs in a container
(`python:3.11-slim` for tests — not 3.12, due to pkg_resources/OTel issues).

```bash
# Tests + coverage gate (must stay ≥90% — see .coveragerc)
docker run --rm -v "$(pwd):/app" -w /app python:3.11-slim sh -c \
  "pip install -e '.[dev]' -q || pip install -r requirements.txt -q; pytest --cov=src --cov-fail-under=90"

# Lint (mirrors CI: ruff.toml — rules F, E7, E9)
docker run --rm -v "$(pwd):/app" -w /app python:3.11-slim sh -c \
  "pip install ruff -q && ruff check src/ tests/"

# Local stack (supervisor + agents + redis + dynamodb-local)
./setup-local.sh        # then: curl http://localhost:8000/health
docker compose up        # alternative

# Docker image build
docker build -t aigent-squad .
```

> Tests require an SSH key for the private `staffops-otel-libs` dependency
> (`requirements.txt` pulls `otel-helper` via `git+ssh`). CI uses a read-only
> deploy key; locally, an agent forwarding `~/.ssh` is needed.

---

## Architecture (invariants — do not violate)

- **1 supervisor (8000) + 5 specialists (8001–8005) + MCP server (8006).**
- Routing is done by the **classifier** (a Bedrock call), never manually.
- Every agent inherits from `src/core/agent_base.py::Agent` and implements
  `async process_request(...)`. **Reference pattern: `src/agents/aws/`.**
- `server.py` is HTTP transport + instrumentation **only** — zero business logic.
- Single response contract: `{role, content, timestamp, agent_id}`.
- Per-agent isolated history in DynamoDB: `pk = user#session`, `sk = agent#timestamp`.
- The Bedrock model id has a **single source**: `src/core/config.py` → env
  `BEDROCK_MODEL_ID` (currently the `us.` inference profile for Claude Sonnet 4.5).

### Layout

```
src/
  core/        # agent_base, bedrock, classifier, cache, config, adapters, generic_agent
  agents/      # aws, kubernetes, finops, devops, observability (agent.py + server.py)
  supervisor/  # orchestrator: classifier → route → synthesize
  api/         # Slack entrypoint
agents/        # config-driven agent definitions (agent.yaml + prompt.md)
skills/        # lazy-loaded markdown knowledge (spec 26)
.kiro/specs/   # requirements/design/tasks per feature (source of truth)
.kiro/steering/# always-on project rules (imported below)
terraform/     # IAM/IRSA, DynamoDB, Bedrock endpoints, cost AIP
```

---

## Read-only posture (current, not permanent)

All agents are **consultative today** — they never run `create/update/delete/
terminate`. On a change request: refuse and point to automation (Terraform/
ArgoCD/GitOps). Execution is an open roadmap item gated by spec 14 + human-in-the-
loop. See [`docs/READ_ONLY_POLICY.md`](docs/READ_ONLY_POLICY.md),
[`.kiro/specs/14-security-hardening/`](.kiro/specs/14-security-hardening/) and
[`.kiro/specs/ADR-001-bedrock-direct-vs-strands.md`](.kiro/specs/ADR-001-bedrock-direct-vs-strands.md).

---

## Project rules (imported from `.kiro/steering/` — single source of truth)

@.kiro/steering/project.md
@.kiro/steering/efficiency-cost.md
@.kiro/steering/licensing-clean-room.md
@.kiro/steering/milestone-criteria.md

---

## Where to look

| I need to… | Go to |
|------------|-------|
| Understand the real state / blockers | [`.kiro/specs/AUDIT.md`](.kiro/specs/AUDIT.md), [`.kiro/specs/ANALYSIS.md`](.kiro/specs/ANALYSIS.md) |
| See the plan / phase order | [`.kiro/specs/ROADMAP.md`](.kiro/specs/ROADMAP.md) |
| Work a feature | `.kiro/specs/<NN-feature>/{requirements,design,tasks}.md` |
| Understand a design decision | [`.kiro/specs/ADR-001-bedrock-direct-vs-strands.md`](.kiro/specs/ADR-001-bedrock-direct-vs-strands.md) |
| Add a new agent | [`docs/HOW-TO-NEW-AGENT.md`](docs/HOW-TO-NEW-AGENT.md) |
| Architecture / security / observability | [`docs/`](docs/) |
| Deploy to Kubernetes | `helm-charts/charts/aigent-squad` (sibling repo) |

---

## Conventions Claude Code must follow

- **Spec-driven**: an architectural change updates the spec's `design.md` **before**
  implementing. Do not implement a feature without its spec.
- **Tests ship with code** (≥90% coverage, Docker-measured) — code without tests is
  not "done".
- **In-code documentation is always English** (comments, docstrings, identifiers,
  commit messages); conversation language may differ.
- **Commits**: Conventional Commits. Never commit/push without explicit approval;
  never push to `main`. Work on `dev`. Stage files explicitly (no `git add .`).
- **Licensing**: never copy third-party code — learn patterns, implement from
  scratch; dependencies only via the declared package manager with license verified
  (see imported `licensing-clean-room.md`).
- Respect the prohibitions in the imported `project.md` (no LangGraph, no root
  container, no unauthenticated endpoints, deterministic cache keys, etc.).
