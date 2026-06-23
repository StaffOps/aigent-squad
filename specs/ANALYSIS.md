# Cross-Domain Analysis — AIgent-squad

**Date**: 2026-06-02
**Branch**: `dev`
**Method**: 8 specialists (dev, security, observability, aws, finops, gitops, sre,
documentation) read the project in parallel, each through their own lens.
**Scope**: findings **beyond** `AUDIT.md` (B1–B4, A1–A3, S1–S5, C1–C2, O1–O4,
D1–D5, H1–H5). Nothing here repeats the AUDIT — it only deepens or discovers what
it didn't see.

> Findings marked ✅ were verified directly in the code. The others come from the
> specialists with `file:line` cited and should be confirmed during implementation.

---

## TL;DR — What the AUDIT didn't see

The AUDIT focused on *"doesn't build / doesn't run"*. This analysis shows that
**even after building, the system has serious structural problems** across 3 axes:

1. **No concurrency** — all I/O (boto3, requests, httpx.Client) is synchronous
   inside `async` handlers. One request blocks all others. Athena even blocks the
   event loop for 30s.
2. **Fails closed** — Redis and DynamoDB without error handling. Any backing-
   service outage = total outage. The correct way is to fail **open** (cache
   miss / empty history, but the system responds).
3. **Invisible and indefensible** — broken telemetry (6/7 services without
   instrumentation, broken trace propagation, zero metrics), and read-only is
   prompt-only — there's no real IAM deny nor RBAC behind it.

These three points are in no current spec. They are a prerequisite for "real
deploy" just as much as the AUDIT blockers.

---

## 🔴 Convergent findings (≥2 specialists, high severity)

### CONV-1 — Synchronous I/O inside async handlers → zero concurrency ✅
**Flagged by**: dev (F1–F12), sre (R4), aws (F7), observability (F9)
**Verified evidence**:
- `src/core/bedrock.py:50` — `self.client.invoke_model()` synchronous; `:78` —
  `time.sleep(delay)` in retry (blocks the event loop for up to 1+2+4=7s).
- `src/core/cache.py` — synchronous `redis.Redis`.
- `src/core/gitlab_client.py` — `requests` library (synchronous) throughout.
- `src/core/docs_portal.py:12` — `httpx.Client` (synchronous), never closed.
- `src/agents/observability/agent.py:109` + `server.py:47` — synchronous `httpx.get()`.
- `src/agents/finops/agent.py` — Athena polling with `time.sleep(1)` × up to 30
  iterations = **30s of frozen event loop**.
- `src/core/state_store.py` — synchronous `put_item`/`query` declared as
  `async def` (masking the blocking).

**Impact**: with uvicorn single-worker, the system **serializes all requests**.
It's the project's biggest performance bug — worse than the broken cache.
**Fix**: `aioboto3` for Bedrock/DynamoDB, `httpx.AsyncClient` in gitlab/docs,
`asyncio.to_thread()` for the kubernetes-client, `asyncio.sleep()` instead of
`time.sleep()`.

### CONV-2 — Dependencies fail closed (Redis + DynamoDB) ✅
**Flagged by**: sre (R2, R3), dev (F6)
**Verified evidence**: `src/core/cache.py` and `src/core/state_store.py` **have no
try/except**. `CacheStore.get/set/exists` and `ChatStorage.save/fetch_*`
propagate `ConnectionError`/`ClientError` straight to the handler.
**Impact**: Redis down → every agent request fails (cache is a **non-critical**
dependency). DynamoDB down → the supervisor (`agent.py:50` does
`fetch_all_chats`) fails 100% of queries.
**Fix**: fail-open. Redis down = cache miss + log. DynamoDB down = empty history
(classifier and agents keep working, just without context).

### CONV-3 — Classifier is a SPOF + expensive (wrong model) ✅
**Flagged by**: sre (R1), finops (F1), aws (F11)
**Verified evidence**: `src/core/classifier.py` calls `bedrock.invoke()` with the
**same** `settings.bedrock_model_id` (Sonnet 4.5) used for responses. Every query
makes **2 Bedrock invocations**.
**Double impact**:
- **Reliability**: if Bedrock is throttled, 100% of queries fail before reaching
  any agent. No fallback (keyword rule, "last agent of the session", or asking
  the user).
- **Cost**: Sonnet 4.5 for routing is ~10–13× more expensive than Haiku. ~$24/mo
  wasted in the classifier alone @ 200 queries/day.
**Fix**: separate `classifier_model_id` (Haiku 3.5) + rule-based fallback when
Bedrock fails.

### CONV-4 — Health endpoints lie ✅
**Flagged by**: sre (R6), dev (F4)
**Verified evidence**: all `server.py` have `/health` returning
`{"status": "healthy"}` unconditionally — never checks Redis, DynamoDB or Bedrock.
**Impact**: K8s probes never detect a broken pod. A pod with dead Redis stays in
the Service endpoints and fails every request. The `05-helm-chart` spec already
assumes `/healthz` + `/ready` that **don't exist**.
**Fix**: `/healthz` (liveness, always 200 if the process responds) + `/ready`
(readiness, checks deps with a 2s timeout + 5s cache).

### CONV-5 — Bedrock blocking + retry without jitter
**Flagged by**: aws (F2), dev (F1), sre (R4)
**Evidence**: `bedrock.py` — hand-rolled retry without jitter (thundering herd)
and stacked **on top** of botocore's default retry (up to 9 real calls per logical
invocation). `botocore` adaptive retry is not configured.
**Fix**: `Config(retries={"mode": "adaptive", "max_attempts": 3})` + jitter in the
app fallback + distinguish transient from permanent errors.

---

## By domain — NEW findings (not in the AUDIT)

### DEV
- **F14 — broken `_load_prompt()`** ✅: `agent_base.py:18` resolves
  `Path(__file__).parent / "prompt.md"` = `src/core/prompt.md` (doesn't exist).
  Every agent using the **base class** method gets the **fallback** text, not the
  real prompt. Only those that reimplement a local `_load_prompt()` work.
- **F5 — fragile classifier parsing**: doesn't handle JSON in markdown
  (` ```json `), JSON truncated by `max_tokens`, `confidence` as a string, or
  `selected_agent` outside the known set (→ `KeyError` in `AGENT_URLS[...]`).
- **F13 — no `__init__.py`** in `src/` and subpackages (depends on `PYTHONPATH=.`
  in the Dockerfiles).
- **F15 — bare `except:`** in `kubernetes/agent.py:25` (swallows
  SystemExit/KeyboardInterrupt).
- **F4 — deprecated `@app.on_event`** (FastAPI 0.109+) → use `lifespan`.

### SECURITY
- **SEC-D4 — read-only is PROMPT-ONLY** (HIGH): `docs/READ_ONLY_POLICY.md`
  promises 4 layers, but only layer 1 (prompt) exists. The `~/.aws` mount gives
  the agent the dev's **full** permissions. `finops/agent.py` has
  `boto3.client('athena')` that can DROP a database. There's no real IAM deny nor
  K8s RBAC.
- **SEC-D1/D2 — SSRF / exfiltration** (HIGH): `devops/agent.py` passes the user
  query straight to `gitlab_client.search_in_company()` over the **whole org**
  ("Company"); results (internal code) enter the Bedrock prompt.
  `docs_portal`/`PROMETHEUS_URL` configurable without an allowlist (risk of
  pointing at `169.254.169.254`).
- **SEC-D3 — prompt injection deeper than S4**: the **inventory itself is
  attacker-controllable** (EC2 instance name = payload). S4's XML tags aren't
  enough — needs encoding of untrusted data + output filtering + using the
  `messages` API correctly (data as a document, not an instruction).
- **SEC-D12 — self-declared `user_id`** (MEDIUM): any client claims any
  `user_id`/`session_id` → accesses other users' history in DynamoDB. Without
  authn there's no way to validate.
- **SEC-D8 — open MCP server gateway**: port 8006 without auth, `user_id`
  hardcoded `"kiro-user"`, all MCP queries share one DynamoDB session.
- **SEC-D5/D6/D7** — secrets as inline env var (violates 12-factor), unpinned
  `python:3.12-alpine` (should be `3.11-slim`), deps without hashes.

### OBSERVABILITY
- **F1 — 6/7 services without instrumentation**: only `aws/server.py` calls
  `FastAPIInstrumentor`/`HTTPXClientInstrumentor`. The supervisor and the other 4
  agents don't.
- **F2 — broken trace propagation**: `supervisor/agent.py` creates an
  `httpx.AsyncClient` but never instruments it → doesn't inject `traceparent`.
  Each agent starts a **new, disconnected** trace. The fan-out is invisible in Tempo.
- **F4 — zero application metrics**: no `MeterProvider`. `prometheus-client` in
  requirements is never imported. No RED, no tokens/cost, no cache hit ratio, no
  classifier confidence.
- **F5 — cardinality violation**: `user_id`/`session_id` as span attributes
  (prohibited by `observability-principles`).
- **F6 — `JSONFormatter` loses the `extra`** (confirms AUDIT's O2 with the exact
  cause: `hasattr(record, 'extra')` is always False in standard logging).
- **F10 — no OTel Collector in the stack** (App → Collector → Backend is the only
  allowed flow).

### AWS
- **F3 — DynamoDB race**: `put_item` without a `ConditionExpression`; SK =
  `agent#timestamp` → 2 messages in the same microsecond overwrite each other.
- **F5 — query without pagination**: `fetch_chat`/`fetch_all_chats` read 1 page; a
  large LLM response can exceed 1MB and silently lose messages.
- **F6 — `ec2.describe_instances()` without pagination** + blocks the event loop.
- **F4 — hardcoded 24h TTL** (too short, not configurable).
- **F1 — no shared `boto3.Session`**: 8 independent clients, redundant IRSA token
  refresh.
- **F9/F12 — no boot validation**: neither of Bedrock model access nor of the
  DynamoDB table's existence → fails on the first request, not at boot.
- **F10 — dead `ce` client** in the aws-agent (widens IAM scope needlessly).

### FINOPS
- **F2 — prompt caching off** ✅: `bedrock.py:28-29` commented out. The system
  prompt (~6400 tokens) re-sent on every call. **~$103/mo wasted** @ 200
  queries/day (caching gives a 90% discount on cached input).
- **Cost per query**: ~$0.026–0.052 (Sonnet). Optimized cost model: **$207→$99/mo**
  (Haiku in the classifier + caching + scale-to-zero).
- **F7 — RAG has negative ROI at the current volume**: README estimates
  +$1327–5595/mo. At $0.22/query of infra overhead. Defer until >2000 queries/day;
  use static context injection (~$0.005/query) before that.
- **F5 — 13 always-on pods** for a low-traffic ChatOps → KEDA scale-to-zero saves
  ~$28/mo.

### GITOPS
- **F8 — ZERO CI/CD** (HIGH): docs reference GitLab CI, but the repo is **GitHub**
  (`github.com:karlipegomes/AIgent-squad`). There's no `.gitlab-ci.yml` nor
  `.github/workflows/`. **Nothing builds the images the `05-helm-chart` spec
  assumes exist.** It's the missing link between code and deploy.
- **F4 — Dockerfiles**: single-stage, `python:3.12-alpine` (should be
  `3.11-slim`), no multi-arch (BDC runs Graviton/arm64), no `USER`.
- **F3 — each image contains the whole `src/`** (all 5 agents) — surface + size.
- **F5 — `.gitignore` ignores `.dockerignore`** (inverted logic) → the build
  context ships the whole repo.
- **F9 — monolithic `requirements.txt`** → each image ~400MB+ (k8s+slack+mcp in
  all), slow cold-start for KEDA.
- **F2 — `dynamodb-local` without a healthcheck** → the supervisor comes up before
  DynamoDB is ready.

### SRE
- **R5 — no graceful shutdown in the agents**: only the supervisor has a hook.
  SIGTERM on the 5 specialists aborts Bedrock mid-flight, leaks connections →
  connection reset on every EKS rollout.
- **R7 — no circuit breaker**: a dead agent → every query classified to it waits
  25s for a timeout. With a 100-connection pool, 100 concurrent queries exhaust
  the pool and stall even the healthy calls.
- **R8 — the supervisor doesn't verify agent availability at boot** (hardcoded
  URLs, no probe).
- **R9 — no correlation ID** propagated supervisor→agent.

### DOCUMENTATION
- **D6/D10 — ghost docs** ✅: `CHANGES.md` and `docs/MIGRATION.md` tell you to run
  `server_new.py` (5 files) and `cd terraform/` — **none of which exist** (the
  `IMPLEMENTATION_HISTORY.md` even says it removed the `server_new.py`).
- **D7 — "v2.0" in 10 files** vs the README's "0.x pre-release" (23 matches of
  `v2.0`). Dissonance: the project is simultaneously pre-release and "Production
  Ready" depending on the file.
- **D8 — corrupted encoding in 7 files** (AUDIT D4 only saw 2): "docker-compoif",
  "Responif", "sefordo" — broken find/replace (`se`→`if`, `re`→`this`).
- **D9 — port collision**: `docs/LOCAL_DEVELOPMENT.md` lists DynamoDB Local on
  8001 (should be 8100).
- **D11 — no `CHANGELOG.md`** (Keep a Changelog) despite the ROADMAP requiring it.
- **D13 — `docs/PREREQUISITES.md` describes the wrong DynamoDB schema**
  (`session_id` instead of `pk`+`sk`).

---

## Proposed new specs

Ordered by dependency. The 4 AUDIT specs (01–04) remain the prerequisite; these
**extend** the roadmap.

| # | Proposed spec | Origin | Severity | Depends on |
|---|---------------|--------|----------|------------|
| **06** | `resilience-patterns` — timeouts, retries w/ jitter, circuit breaker, fail-open Redis/DynamoDB, **async-first refactor** (`aioboto3`/`httpx.AsyncClient`/`asyncio.to_thread`), classifier fallback | CONV-1, CONV-2, CONV-3, CONV-5, sre R1–R9, dev F1–F12 | 🔴 | 02 |
| **07** | `readiness-probes` — `/healthz` (liveness) + `/ready` (checks deps) + graceful shutdown (`lifespan`, flush OTel, drain) | CONV-4, sre R5/R6, dev F4 | 🔴 | 02 (prereq of 05) |
| **08** | `ci-cd-pipeline` — GitHub Actions (test→build-dev→demo→release), multi-arch amd64+arm64, Trivy, cosign, SBOM, 90% coverage gate, push ECR/Harbor | gitops F8, AUDIT "no tests" | 🔴 | 01 |
| **09** | `otel-instrumentation` — `setup_telemetry(service_name)`, instrument 7 services, trace propagation, OTel Collector in the stack, explicit sampler | obs F1–F12 | 🟠 | 02 |
| **10** | `metrics-and-cost-observability` — RED + LLM catalog (tokens, **cost $**, classifier confidence, cache hit), emitted via OTel; Grafana dashboards | obs (metrics-catalog), finops F-cost-obs | 🟠 | 09 |
| **11** | `bedrock-resilience-cost` — Haiku in the classifier, **prompt caching** (re-enable), model tiering, cross-region failover, per-session token budget | CONV-3, finops F1/F2, aws F2/F11 Prop3 | 🟠 | 06 |
| **12** | `terraform-infra` — DynamoDB (PITR, TTL enabled, GSIs), ElastiCache, 7 ECR repos, IRSA, Secrets Manager, S3 Athena | aws Prop1/Prop4, ROADMAP Phase 2 | 🟠 | — |
| **13** | `iam-least-privilege` — per-agent read-only policy + **explicit deny** (terminate/delete/iam:*), only 3/7 services need AWS beyond Bedrock | SEC-D4, aws Prop2 | 🟠 | 12 |
| **14** | `security-hardening` — threat model (STRIDE), authn/authz + audit log, NetworkPolicy + Istio mTLS, **prompt-injection guardrails** (input scan + context isolation + output filter + canary) | SEC-D1/D2/D3/D8/D12, sec Prop1–4 | 🟠 | 04 |
| **15** | `sli-slo-framework` — SLIs (availability, p95 latency, classifier/agent success rate), Tier-3 SLOs, error budget, burn-rate alerts | sre Prop2, obs Prop3 | 🟡 | 10 |
| **16** | `incident-runbooks` — playbooks (agent down, Bedrock throttled, Redis/DynamoDB down, high latency, total outage) | sre Prop4 | 🟡 | 06, 07 |
| **17** | `multi-agent-collaboration` — multi-agent classifier, **parallel fan-out/fan-in** for cross-domain queries + synthesis, **agent-as-tools** (1 hop) | ANALYSIS "new behaviors" | 🟢 | 06, 09 |
| **18** | `rca-investigation-workflow` — **the differentiator**: symptom → evidence fan-out → timeline → cross-signal correlation → RCA (confidence + evidence + prevention). Read-only | expected gain (RCA), `investigation-protocol` | 🟢 | 17, 09, 19 |
| **19** | `config-driven-platform` — config file (YAML) + env override + secrets outside the YAML; registry of agents/datasources/limits/models per role. **Product prereq** | user requirement, hardcodes | 🟠 | — |
| ~~**20**~~ | ~~`grpc-inter-agent-mesh`~~ — **REMOVED** (decision 2026-06-02): irrelevant latency gain (~3ms over ~5s model calls). gRPC is not justified by RCA speed | — | ❌ | — |
| **21** | `incident-memory-learning` — persists investigations `{signature, evidence, root cause, fix}` + retrieves similar incidents. **Simple** learning loop (no expensive Knowledge Base) | expected gain (learning) | 🟢 | 18 |
| **22** | `agent-capability-manifest` — **open** roster of specialists via YAML manifest; metadata-driven collaboration (`capabilities`, `evidence_types`, `delegates_to`). **Replaces 19** | user requirement (open roster + metadata collaboration) | 🟠 | — |
| **23** | `test-harness-docker` — `Dockerfile.test` + single command: `pytest --cov-fail-under=90` with mocks (fakeredis/moto/respx), no real services; same harness dev↔CI | user requirement (tests via Dockerfile), `dev-environment` | 🔴 | — |
| **24** | `docs-portal-mkdocs` — consolidate scattered docs into an MkDocs Material portal (`src`→`public`, DevOps-portal style); README becomes an index; ADRs; kills ghost/encoding docs | user requirement (organize open-source docs), D6–D15 | 🟠 | — |

> **See [`ECOSYSTEM.md`](ECOSYSTEM.md)**: decision made (2026-06-02) = **keep
> AIgent-squad SEPARATE** (no merge, not becoming a chaitops collector). Specs
> like 14/17/19/21 exist more mature in `staffops-chaitops` and should be
> **reused by copy** (AgentRegistry, BudgetGuard, KbDelta, the anomaly-detection
> payload contract), not by dependency. gRPC (20) removed. Docs in English.

### Tests & verification (cross-cutting — affects every spec with code)

Current state ✅ verified: **zero** tests, no `pytest`/`tox`, no CI
(`.github`/`.gitlab-ci.yml`), no test deps in `requirements.txt`. This is the
global steering's verification prerequisite — it blocks declaring anything
"done". The **dockerized harness** that operationalizes all this is spec
**`23-test-harness-docker`**.

Rules to apply across **all** specs 06–22 that touch code:
- **Coverage ≥90%** measured via Docker (`pytest --cov=<pkg> --cov-fail-under=90`,
  `python:3.11-slim`) — a build gate, not an aspirational goal (steering
  `dev-environment.md`).
- **Code author ≠ test author** — implementation and tests in different
  sessions/agents; tests written against the contract/spec, not the
  implementation (global steering `verification-independence.md`).
- **Mandatory independent review** via the `code-review` subagent — "the test
  passed" is not enough.
- Spec **08-ci-cd-pipeline** materializes the gate (coverage gate in GitHub
  Actions); the 3-stage pipeline (`implement` → `write-tests` → `review`) is the
  day-to-day operational form.

Highest-value minimal suite (priority by branch/error-path): classifier parsing
(JSON in markdown, truncated, unknown agent), cache key determinism (sha256),
response contract (`{role,content,timestamp,agent_id}`), supervisor routing
(timeout/500/fallback), Bedrock retry, Redis/DynamoDB fail-open.

### New behaviors (non-spec, embed in the specs above)
- **Async-first** is the most impactful behavior change — belongs to 06, but
  cuts across all the code.
- **Fail-open** on all non-critical dependencies (Redis, history) — an invariant
  to add to `steering/project.md`.
- **Model tiering** (Haiku routes, Sonnet responds) — changes the
  `bedrock.invoke()` contract.
- **Response streaming** (there's already `AsyncIterable` in `agent_base`, never
  implemented) — UX and latency perception.
- **Agent-as-tools + cross-domain collaboration** — now specified in
  **`17-multi-agent-collaboration`** (parallel fan-out/fan-in + synthesis;
  agent-as-tools with 1 hop). Depends on 06 (async) and 09 (trace).

### Documentation (cross-cutting)
- **Restructure docs** (Diátaxis): 11 overlapping docs → ~8 focused. Delete
  `CHANGES.md`, `VERSIONS.md`, `GENERIC_VERSION.md`, `MIGRATION.md` (ghost).
- **ADR log**: 001-langgraph-removal, 002-bedrock-direct, 003-read-only-design,
  004-classifier-vs-tool-use, 005-HTTP-per-agent-vs-in-process.
- **`CHANGELOG.md`** Keep a Changelog + an honest reset to `0.1.0`.
- **Rationale audit (Level 3)** on specs 01–04 before implementing.

---

## Prioritization recommendation

1. **AUDIT 01** (blockers) — without it nothing builds. *Immutable.*
2. **Spec 06 (resilience/async)** + **02 (unify)** together — the async-first
   refactor is cheaper to do during the agent unification than afterward.
3. **Spec 07 (probes)** — a code prerequisite that `05-helm-chart` already assumes.
4. **Spec 08 (CI/CD)** — in parallel; it's the missing link between code and
   deploy, and brings the coverage gate that covers the "zero tests" gap.
5. **09/11** (telemetry + Bedrock cost) — before the real deploy, otherwise it
   goes up blind and expensive.
6. **12/13/14** (infra + IAM + security) — Phase 2, together with 05-helm-chart.
7. **15/16** (SLO + runbooks) — when there's real traffic to measure.

> The AUDIT answered *"why it doesn't run"*. This analysis answers *"why, even
> running, it isn't production-ready"*: no concurrency, fails closed, blind and
> indefensible. Specs 06–14 close that distance.

---

## Cost Estimates — RCA Investigation (reference 2026-06)

Bedrock prices (Claude): Haiku $0.25/$1.25 | Sonnet $3/$15 | Opus $15/$75
(input/output per 1M tokens).

### Per call (estimated average)

| Role | Model | Tokens (in/out) | Cost |
|------|-------|-----------------|------|
| Classifier (routing) | Haiku | ~1K / ~0.2K | ~$0.001 |
| Collector agent (evidence) | Sonnet | ~2K / ~1K | ~$0.02 |
| Synthesizer (correlation) | Sonnet (Level 1–2) | ~8K / ~2K | ~$0.05 |
| Synthesizer (correlation) | Opus (Level 3–4) | ~8K / ~2K | ~$0.50 |
| Distillation extractor (draft KB) | Sonnet | ~4K / ~1K | ~$0.03 |
| Distillation enricher (refine KB) | Opus | ~6K / ~2K | ~$0.24 |

### Per RCA investigation (5 agents, connectivity problem)

| Level | Max rounds | Bedrock calls | Tokens (in/out) | Cost/RCA | Estimated time |
|-------|------------|---------------|-----------------|----------|----------------|
| **1** Hub-and-spoke | 1 | ~7 | ~22K / ~7K | **~$0.17** | ~6s |
| **2** Iterative | 5 | ~31 | ~90K / ~35K | **~$0.80** | ~25s |
| **3** Shared context | 10 | ~62 | ~200K / ~70K | **~$1.65** | ~50s |
| **4** Autonomous | 25 | ~150+ | ~500K / ~180K | **~$4.20** | ~2min |

+ Distillation (1× per investigation): ~$0.27 (Sonnet draft + Opus enricher)

### Cost/value comparison

| Approach | Cost | Time |
|----------|------|------|
| Senior engineer investigating manually | ~$50–100 | 30–60min |
| AIgent-squad Level 1 | $0.17 | 6s |
| AIgent-squad Level 4 (max) | $4.20 + $0.27 distillation | ~2min |

**Conclusion**: even in the most expensive scenario (Level 4), the cost is ~10–25×
lower than human investigation.

### Monthly projection (by incident volume)

| Incidents/mo | Level 1 | Level 2 | Level 4 |
|--------------|---------|---------|---------|
| 10 | $1.70 | $8.00 | $42.00 |
| 50 | $8.50 | $40.00 | $210.00 |
| 200 | $34.00 | $160.00 | $840.00 |

Note: in practice, not every investigation needs all the rounds. max_rounds is a
ceiling, not the average use.
