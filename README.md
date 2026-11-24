# AIgent Squad — multi-agent AWS/Kubernetes operations

[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/StaffOps/aigent-squad/badge)](https://scorecard.dev/viewer/?uri=github.com/StaffOps/aigent-squad)

An edge gateway, a supervisor with six in-process specialist agents, and an MCP
server. Read-only by default. Config-driven (no agent framework). The LLM is AWS
Bedrock by default, or any OpenAI-compatible API.

| | |
|---|---|
| **Latest release** | `0.5.0` — tag [`v0.5.0`](https://github.com/StaffOps/aigent-squad/releases/tag/v0.5.0) (2026-08-18), image `ghcr.io/staffops/aigent-squad:0.5.0` (cosign-signed, SBOM attested) |
| **On `dev` since then** | Pluggable LLM provider: Bedrock or any OpenAI-compatible API (spec 28, done) · secret settings as `SecretStr` · a fresh clone reaches a healthy stack with `make up` (spec 36 T11) · CI brings the compose stack up on every PR · ruff/mypy pinned in one file for `make` and CI · Backstage TechDocs + catalog · specs 06, 17 and 33 closed. Full list: [`CHANGELOG.md`](CHANGELOG.md) `[Unreleased]` |
| **Status per spec** | [`specs/ROADMAP.md`](specs/ROADMAP.md) (canonical, CI-validated table) |
| **Session state** | [`docs/handoff.md`](docs/handoff.md) |

Work happens on `dev`; releases go through a PR `dev → main` plus a tag ([`RELEASE.md`](RELEASE.md)).

---

## 🎯 Overview

**Routing and agents**
- An LLM classifier (Haiku by default) routes each query to 1–3 specialists; cross-domain queries fan out in parallel and a synthesizer merges the answers.
- Six specialists, all the same `GenericAgent` driven by `agents/<name>/agent.yaml` + `prompt.md`: **aws**, **kubernetes**, **finops**, **devops**, **observability**, **security**. A new agent is a new directory, with no code change ([`docs/HOW-TO-NEW-AGENT.md`](docs/HOW-TO-NEW-AGENT.md)).
- Agentic tool-calling (spec 37): the LLM picks read-only MCP tools and arguments in a bounded loop (defaults: 8 steps, 120 s, 300K tokens; set by `AIGENT_MAX_*` env vars). A new MCP server is a URL plus an allowlist.
- Model tiering per role and per query complexity (specs 11, 38), context trimming for long tool loops (spec 40), lazy-loaded markdown skills (spec 26; generic examples in `example-skills/`, your own catalog via `SKILLS_DIR`).
- RCA investigations, from a query or an Alertmanager alert (spec 18), plus an optional incident-memory knowledge base on PostgreSQL + pgvector (spec 21).
- Per-agent isolated conversation history in DynamoDB (24 h TTL).

**Security** (details: [`docs/SECURITY.md`](docs/SECURITY.md))
- Read-only today, enforced by the system prompt, IAM deny, K8s RBAC and response templates, plus a fail-closed tool allowlist for agentic calls ([`docs/READ_ONLY_POLICY.md`](docs/READ_ONLY_POLICY.md)).
- Anti-prompt-injection in depth, layers L1–L6 (spec 14): Bedrock Guardrail, InputScanner, context isolation, output filter, canary tokens. The security layers fail closed (403); availability dependencies (Redis, DynamoDB) fail open.
- Two-tier trust boundary: only the gateway can call the supervisor's `/internal/*` routes (separate `SUPERVISOR_INTERNAL_TOKEN`).
- Supply chain (spec 45): GitHub Actions pinned to commit SHAs, release images signed with cosign and attested ([`docs/VERIFYING-RELEASES.md`](docs/VERIFYING-RELEASES.md)).

**LLM** (spec 28, [ADR-0009](docs/architecture/decisions/0009-llm-provider-abstraction.md))
- Every call goes through one `LLMService` layer (circuit breaker, retry, guardrail, token and cost metrics, per-session token budget).
- `LLM_PROVIDER=bedrock` (default; prompt caching, per-agent cost attribution through Application Inference Profiles) or `LLM_PROVIDER=openai` for any OpenAI-compatible `/chat/completions` API.
- With a provider other than Bedrock, the Bedrock Guardrail (L1) is **off**. The service logs a startup warning, sets `aigent.llm.guardrail_l1_active=0` and reports it in `/ready`. L2–L6 still run.

**Integrations**
- OpenAI-compatible `/v1` API on the gateway, used by LibreChat or any OpenAI client ([`docs/LIBRECHAT.md`](docs/LIBRECHAT.md)). Each agent is also exposed as a model (`aigent-squad-<agent>`).
- MCP: the squad as an MCP server (`mcp-server/`, port 8006) and agents as MCP clients ([`docs/MCP_INTEGRATION.md`](docs/MCP_INTEGRATION.md)).
- Alertmanager webhook (`POST /alerts/incoming`) that starts an RCA and can post the result back to Slack ([`docs/ALERTING.md`](docs/ALERTING.md)).

**Observability**
- OpenTelemetry traces and metrics through the Collector, JSON logs, custom `aigent.*` metrics ([`docs/METRICS.md`](docs/METRICS.md), [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md)).
- A quality eval harness with golden queries and RCA scenarios (`evals/`, spec 35; `make eval`, `make eval-rca`).

---

## 🏗️ Architecture

```
  LibreChat / OpenAI clients (/v1) · curl (/query) · MCP server (:8006)
                                   │
                                   ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │ GATEWAY :8000 (public, spec 31)                                   │
  │ edge auth (fail-closed) · per-user rate + daily budget (Redis)    │
  │ WorkerPool backpressure (503 + Retry-After) · /v1 shaping · cancel│
  └─────────────────────────────────┬─────────────────────────────────┘
                                    │ POST /internal/process
                                    │ (SUPERVISOR_INTERNAL_TOKEN)
                                    │          Alertmanager webhook
                                    │          (POST /alerts/incoming)
                                    │                 │
  ┌─────────────────────────────────▼─────────────────▼───────────────┐
  │ SUPERVISOR :8001 (backend-only)                                   │
  │ InputScanner (L2) → classifier → 1–3 agents → synthesizer         │
  │ RCA investigation · guardrail on input, tool args/results, output │
  │ canary + output filter · per-agent history                        │
  │                                                                   │
  │ In-process specialists (GenericAgent, no ports):                  │
  │   aws · kubernetes · finops · devops · observability · security   │
  │ datasource adapters: boto3 · kubernetes · http · athena · mcp     │
  └─────────────────────────────────┬─────────────────────────────────┘
                                    │
  ┌─────────────────────────────────┴─────────────────────────────────┐
  │ LLMService (src/core/llm/) → Bedrock (default) | OpenAI-compatible│
  │ DynamoDB (history, 24 h TTL) · Redis (datasource cache, rate,     │
  │ budget, jobs) · PostgreSQL + pgvector (incident memory, optional) │
  └───────────────────────────────────────────────────────────────────┘
```

**Request path**: the gateway authenticates and admits the request, then forwards it
to the supervisor. The supervisor scans the input, classifies it, loads the history
for each selected agent and runs the agents (in parallel when there are several).
Each agent fetches data through its adapters (cached in Redis, 1–60 min TTL) and
calls the LLM. The supervisor filters the output, saves the history and returns
`{role, content, timestamp, agent_id}`.

**Deployment**: the validated Kubernetes topology is the in-process one, with only
`gateway` and `supervisor` Deployments. The Helm chart lives in the sibling
`helm-charts` repo (`charts/aigent-squad`). Architecture decisions:
[`docs/architecture/decisions/`](docs/architecture/decisions/README.md); more detail:
[`docs/site/architecture.md`](docs/site/architecture.md).

---

## 🚀 Quick start

**Prerequisites**: Docker with Compose, `make`, and AWS credentials in `~/.aws` (mounted
read-only, used for Bedrock). kubectl is optional (only for the kubernetes agent). No
local Python is needed: run, test and lint all go through Docker.

```bash
git clone git@github.com:StaffOps/aigent-squad.git
cd aigent-squad

make up          # build + start the two-tier stack, wait for gateway /ready
make smoke       # health + 1 real query + /v1/models
make test        # full suite + 90% coverage gate (Docker)
make lint        # ruff, same scope as CI
make typecheck   # mypy gate (CI blocks tests on it)
make down        # stop (V=1 also drops volumes)
```

`docker-compose.yaml` ships dev-safe defaults, so no `.env` is needed. Copy
[`.env.example`](.env.example) only to override settings. `make help` lists every
target. Full walkthrough: [`QUICKSTART.md`](QUICKSTART.md). Common failures and
fixes: the playbook in [`AGENTS.md`](AGENTS.md#playbook--common-failure-modes-spec-36).

**Local services**

| Service | Address |
|---------|---------|
| Gateway (public: `/query`, `/v1/*`, `/jobs/{id}/cancel`) | http://localhost:8000 |
| Supervisor (`/internal/*`, gateway-only) | port 8001, internal |
| MCP server | http://localhost:8006 |
| Redis / PostgreSQL / DynamoDB Local | localhost:6379 / :5432 / :8100 |
| Prometheus / Grafana | http://localhost:9099 / http://localhost:3001 |
| LibreChat | http://localhost:3080 ([`docs/LIBRECHAT.md`](docs/LIBRECHAT.md)) |

---

## 🧪 Testing

```bash
make test                                  # full suite, ≥90% coverage gate
make test-one FILE=tests/test_gateway_main.py
```

The suite runs in random order (`pytest-randomly`, seed not pinned). Locally,
`make test` replaces the `otel-helper` dependency with a stub and prints a warning, so
CI (`gh run list`) has the final verdict.

Query the running stack:

```bash
# Auto-routed query
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Token: dev-secret-token' \
  -d '{"user_input": "How many EC2 instances are running?", "user_id": "u1", "session_id": "s1"}'

# List models (aigent-squad + aigent-squad-<agent>), then force one specialist
curl -H 'X-Internal-Token: dev-secret-token' http://localhost:8000/v1/models
curl -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Token: dev-secret-token' \
  -d '{"model": "aigent-squad-aws", "messages": [{"role": "user", "content": "List EC2 instances"}]}'
```

---

## 🔒 Security

The squad is read-only today. Executing actions is an open roadmap item (specs 43–44),
and it depends on capability tiers and human-in-the-loop. The read-only posture has
four layers: system prompts, IAM explicit deny on write actions, K8s RBAC limited to
`get`/`list`/`watch`, and response templates that suggest automation instead of manual
changes. For agentic tool calls, a fail-closed tool allowlist and the MCP server's own
ServiceAccount RBAC hold it (checked by `make mcp-rbac-audit`).

See [`docs/SECURITY.md`](docs/SECURITY.md), [`docs/READ_ONLY_POLICY.md`](docs/READ_ONLY_POLICY.md)
and [`specs/14-security-hardening/`](specs/14-security-hardening/).

---

## 🛠️ Technology stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.11 (`python:3.11-alpine` runtime image, non-root; `python:3.11-slim` for tests) |
| API | FastAPI + uvicorn (gateway and supervisor), httpx |
| LLM | AWS Bedrock (Claude, via boto3) or any OpenAI-compatible API, behind `LLMService` |
| State and cache | DynamoDB (history), Redis (cache, rate/budget, jobs), PostgreSQL + pgvector (optional KB) |
| Integrations | MCP (`mcp` SDK), Kubernetes client, Slack SDK |
| Config | pydantic-settings (env vars are the single source) |
| Observability | OpenTelemetry (`otel-helper`), Prometheus client, JSON logs |
| Quality gates | pytest (≥90% coverage), ruff, mypy, bandit, harness score |
| CI/CD | GitHub Actions; multi-arch images (amd64/arm64) on GHCR, signed with cosign |
| Deploy | Docker Compose (local), Kubernetes/EKS via Helm, Terraform for AWS ([`infra/terraform/`](infra/terraform/README.md)) |

---

## 📊 Roadmap

The roadmap and the status of every spec are in [`specs/ROADMAP.md`](specs/ROADMAP.md).
Its canonical table is generated from each spec's frontmatter and validated in CI
(`make specs-status`). Open findings, backlog items and deferred work are in
[`specs/BACKLOG.md`](specs/BACKLOG.md); the long-term direction is in
[`specs/VISION.md`](specs/VISION.md).

---

## 📖 Documentation

### Product and decisions
- [`docs/prd/aigent-squad.md`](docs/prd/aigent-squad.md) — PRD: problem, personas, success metrics, scope
- [`docs/architecture/decisions/`](docs/architecture/decisions/README.md) — ADRs 0001–0009 (Bedrock-direct, in-process agents, read-only posture, fail-closed vs fail-open, two-tier gateway, standalone product, internal-first vs OSS, agentic tool-calling, LLM provider abstraction)

### Specs and planning (`specs/`)
- [`specs/README.md`](specs/README.md) — how the spec process works (lifecycle, status frontmatter, tiers, review)
- [`specs/ROADMAP.md`](specs/ROADMAP.md) — phased plan + canonical status table
- [`specs/BACKLOG.md`](specs/BACKLOG.md) — findings (`F-*`), product backlog (`B-*`), decisions (`D-*`), deferred work
- [`specs/VISION.md`](specs/VISION.md) — long-term maturity levels
- [`specs/AUDIT.md`](specs/AUDIT.md) — historical audit (2026-05-30), kept as a record
- [`docs/handoff.md`](docs/handoff.md) — current session and next steps (prior sessions in `archive/handoffs/`)
- [`steering/project.md`](steering/project.md) — project rules and invariants

### Getting started and releases
- [`QUICKSTART.md`](QUICKSTART.md) — setup in 3 steps
- [`CHANGELOG.md`](CHANGELOG.md) — changelog
- [`RELEASE.md`](RELEASE.md) — release runbook (spec 34)
- [`docs/VERIFYING-RELEASES.md`](docs/VERIFYING-RELEASES.md) — verify image signatures and attestations, and the signed release assets
- [`docs/OPENSSF-BEST-PRACTICES.md`](docs/OPENSSF-BEST-PRACTICES.md) — OpenSSF Best Practices (passing) criteria mapped to evidence, with open gaps
- [`docs/site/CI-CD.md`](docs/site/CI-CD.md) — CI/CD pipeline and versioning

### Technical docs
- [`docs/site/architecture.md`](docs/site/architecture.md) — architecture
- [`docs/HOW-TO-NEW-AGENT.md`](docs/HOW-TO-NEW-AGENT.md) — create an agent (datasources, skills, MCP)
- [`docs/MCP_INTEGRATION.md`](docs/MCP_INTEGRATION.md) — MCP server and clients
- [`docs/LIBRECHAT.md`](docs/LIBRECHAT.md) — LibreChat through the `/v1` bridge
- [`docs/ALERTING.md`](docs/ALERTING.md) — Alertmanager ingestion + Slack post-back
- [`docs/KNOWLEDGE-BASE.md`](docs/KNOWLEDGE-BASE.md) — incident-memory KB (pgvector)
- [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) — logging and tracing
- [`docs/METRICS.md`](docs/METRICS.md) — custom metrics catalog
- [`docs/SECURITY.md`](docs/SECURITY.md) · [`docs/READ_ONLY_POLICY.md`](docs/READ_ONLY_POLICY.md) — security model and read-only policy
- [`docs/SETUP.md`](docs/SETUP.md) · [`docs/PREREQUISITES.md`](docs/PREREQUISITES.md) — local setup and infrastructure requirements
- [`docs/LOAD-TESTING.md`](docs/LOAD-TESTING.md) — k6 load tests
- [`docs/COMPETITIVE-ANALYSIS.md`](docs/COMPETITIVE-ANALYSIS.md) — comparison with other AI SRE agents
- [`src/supervisor/README.md`](src/supervisor/README.md) — supervisor internals

### Infrastructure (Terraform)
- [`infra/terraform/README.md`](infra/terraform/README.md) — AWS modules (IAM/IRSA, DynamoDB, Bedrock endpoints, cost AIP)
- [`infra/terraform/bedrock-aip/README.md`](infra/terraform/bedrock-aip/README.md) — Application Inference Profiles and cost attribution

### AI tooling (tool-agnostic)
[`AGENTS.md`](AGENTS.md) is the canonical guide for any AI coding assistant, with
detailed rules in [`steering/`](steering/) and plans in [`specs/`](specs/).
[`CLAUDE.md`](CLAUDE.md) only points to it. Per-tool local directories (`.claude/`,
`.cursor/`, …) are git-ignored.

---

## 🤝 Contributing

Branch from `dev` and open a PR back to `dev`. Every PR runs lint, typecheck, tests
with the ≥90% coverage gate, dependency and SAST scans, and a compose stack-up check.
Releases are a PR `dev → main` plus a `vX.Y.Z` tag. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) and [`docs/site/CI-CD.md`](docs/site/CI-CD.md).

Conventions: spec-driven (update the spec's `design.md` before implementing), tests,
metrics and docs ship with the code, Conventional Commits. The details are in
[`AGENTS.md`](AGENTS.md#workflow-rules).

---

## 📚 References

- AWS Labs Agent Squad (original inspiration): https://github.com/awslabs/agent-squad
- AWS Bedrock: https://aws.amazon.com/bedrock/
- Model Context Protocol: https://modelcontextprotocol.io/

## 📝 License

Apache 2.0. See [LICENSE](LICENSE).
