# Roadmap — AIgent-squad

**Work branch**: `dev`
**Base**: audit in `AUDIT.md` (2026-05-30)

This roadmap reflects the **real state** of the project, not the aspirational one
of the old README. The premise: the system **didn't build/run** at the time
(missing root Dockerfile, broken modules). Before any new feature, stabilize.

---

## Current state (honest)

| Dimension | Status |
|-----------|--------|
| Build (`docker compose build`) | ✅ passes (spec 01 completed 2026-06-14) |
| Unified agents | ✅ config-driven GenericAgent (spec 02 + 22) |
| Multi-turn (history) | ✅ unified via agent_base |
| Cache | ✅ deterministic sha256 key (spec 03) |
| Observability | ✅ OTel wired, JSONFormatter, PROMETHEUS_URL, custom metrics (spec 03) |
| Security (baseline) | ✅ auth, non-root, redis password, prompt delimiters (spec 04) |
| Security (anti-injection) | ⚠️ spec 14 Phase 1 done (Bedrock Guardrail L1 + fail-closed + audit); Phases 2–5 pending |
| Edge gateway | ✅ spec 31 cluster-validated (2026-07-01, devops-core): gateway + supervisor in-process, Istio HTTPRoute, IRSA→Bedrock, guardrail, agentsSource git+configmap. Image `0.3.0-dev` on Harbor labs; chart 0.9.0 |
| Tests | ✅ ~93% global coverage, CI gate 90% |
| Docs | ✅ MkDocs site (architecture/metrics two-tier), METRICS.md, SECURITY.md, KNOWLEDGE-BASE.md, HOW-TO |
| Async/Resilience | ✅ full async, circuit breaker, fail-open (spec 06) |
| Multi-agent | ✅ fan-out, synthesizer, agent-as-tools (spec 17) |
| RCA | ⚠️ Phase 1 done (single-round); Phase 2 pending (spec 18) |
| Incident memory | ✅ pgvector KB, extraction, RAG injection (spec 21) |
| Platform | ✅ config-driven, Helm chart, zero-code agent add (spec 22) |
| CI/CD | ✅ GitHub Actions, multi-arch, Trivy scan-before-push, Bandit SAST (spec 08) |

**Suggested real version**: `0.2.0` released; gateway (spec 31) validated in
devops-core and running as `0.3.0-dev` (image on Harbor labs, chart 0.9.0).
Cut a stable `0.3.0` (drop `-dev`) once the milestone is committed and the image
is rebuilt with the app fixes under a stable tag. The README's
"v2.0 / Production Ready" is inflated (see `version-management.md`).

---

## Phases (execution order)

### Phase 0 — Stabilization (specs 01 → 02 → 03 → 04)
Prerequisite for everything. Order matters because of dependencies.

| # | Spec | Severity | Depends on | Measurable result |
|---|------|----------|------------|--------------------|
| 1 | `01-fix-blockers` | ✅ done | — | `docker compose build && up` green |
| 2 | `02-unify-agent-architecture` | ✅ done | 01 | 5 agents on the same pattern, single contract, multi-turn |
| 3 | `03-fix-cache-observability` | ✅ done | 02 | deterministic cache, logs with context, OTLP |
| 4 | `04-harden-security` | ✅ done | 02 | authenticated endpoints, non-root containers |

**Phase 0 exit criterion**: `docker compose up` brings everything up healthy, 1
query per agent responds with the correct contract, tests pass, endpoints require
a token. Only then does it make sense to talk about "deploy".

### Phase 1 — Quality & Docs (parallelizable after Phase 0)
- Align the Bedrock model (single source) — D1.
- Fix doc encoding and `ARCHITECTURE.md` (remove LangGraph) — D3, D4.
- Update the README with the real status — D2.
- `.dockerignore`, split `requirements.txt` per agent, move `gitlab_client`
  hardcodes to config — H1–H5.
- Minimal test suite with CI (GitLab CI already has an initial doc).

### Phase 2 — Real deploy (only after Phase 0+1 validated)
- Infra Terraform (DynamoDB, ElastiCache, IAM/IRSA, ECR).
- **Application Helm chart** — spec [`05-helm-chart`](05-helm-chart/): 7
  parameterized services, IRSA, External Secrets, KEDA, Argo Rollouts,
  NetworkPolicy, securityContext, mandatory labels.
- Per-environment manifests/values (DEV/HML/PRD/BTC) with probes,
  `resources.requests`, mandatory labels (`k8s-best-practices`).
- CI/CD pipeline (multi-arch build, scan, push Harbor/ECR).
- Validate read-only via real IAM deny + RBAC.

> Code prerequisite for spec 05: add `/healthz` and `/ready` endpoints (today
> there's only `/health`).

### Phase 3 — Features (original roadmap, revalidated)
Only after the system really runs. Reuses the README roadmap, but without
inflating the version before real usage:
- RAG/Knowledge Bases (high cost — evaluate ROI; see estimates in the README).
- Slack integration (depends on `api/server.py` rewritten in spec 01).
- Proactive agents (CronJobs).
- Remaining phases 6–13 of `archive/IMPLEMENTATION_HISTORY.md` as needed.

---

## Cross-domain analysis (2026-06-02) — proposed new specs

The analysis in [`ANALYSIS.md`](ANALYSIS.md) (8 specialists in parallel) found
structural problems **beyond** the AUDIT. Summary: even after building, the
system **has no concurrency** (synchronous I/O in async handlers), **fails
closed** (Redis/DynamoDB with no error handling), and is **blind/indefensible**
(broken telemetry, read-only only in the prompt). Proposed new specs:

| # | Spec | Severity | Depends on |
|---|------|----------|------------|
| 06 | `resilience-patterns` (async-first, fail-open, circuit breaker, classifier fallback) | ✅ done | 02 |
| 07 | `readiness-probes` (`/healthz`+`/ready`+graceful shutdown) | 🔴 | 02 |
| 08 | `ci-cd-pipeline` (GitHub Actions, multi-arch, scan, coverage gate) | ✅ done | 01 |
| 09 | `otel-instrumentation` (7 services, propagation, Collector) | 🟠 | 02 |
| 10 | `metrics-and-cost-observability` (RED + tokens/cost $) | 🟠 | 09 |
| 11 | `bedrock-resilience-cost` (Haiku in the classifier, prompt caching, tiering) | 🟠 | 06 |
| 12 | `terraform-infra` (DynamoDB/ElastiCache/ECR/IRSA/Secrets) | 🟠 | — |
| 13 | `iam-least-privilege` (read-only per agent + explicit deny) | 🟠 | 12 |
| 14 | `security-hardening` (defense-in-depth anti-prompt-injection: multi-language Bedrock Guardrails, fail-closed, canary/output-filter, rate/budget; read-only as a security posture = competitive differentiator) | ⚠️ Phase 1 done (Guardrail L1 + fail-closed + audit); Phases 2-5 pending | 04 |
| 15 | `sli-slo-framework` | 🟡 | 10 |
| 16 | `incident-runbooks` | 🟡 | 06, 07 |
| 17 | `multi-agent-collaboration` (cross-domain fan-out/fan-in + synthesis, agent-as-tools 1 hop) | ✅ done | 06, 09 |
| 18 | `rca-investigation-workflow` (**differentiator**: parallel evidence → timeline → correlation → RCA, read-only) | ⚠️ Phase 1 done; Phase 2 partial (alert ingestion done; multi-round/fault-tree NOT done) | 17, 09, 19 |
| 19 | `config-driven-platform` (YAML + env override, secrets outside the YAML, agent registry) | ❌ substituted by spec 22 | — |
| ~~20~~ | ~~`grpc-inter-agent-mesh`~~ — **REMOVED** (2026-06-02): latency irrelevant vs model calls | ❌ | — |
| 21 | `incident-memory-learning` (incident memory + similar-case retrieval; simple learning) | ✅ done | 18 |
| 22 | `agent-capability-manifest` (**open** roster via YAML + collaboration via `capabilities`/`evidence_types`/`delegates_to` metadata; **replaces 19**) | ✅ done (Phase A+B) | — |
| 23 | `test-harness-docker` (`Dockerfile.test` + `pytest --cov-fail-under=90` with mocks; same harness dev↔CI; consumed by 08) | 🔴 | — |
| 24 | `docs-portal-mkdocs` (MkDocs Material portal `src`→`public`; README becomes an index; ADRs; consolidates/deletes ghost docs) | 🟠 | — |
| 25 | `multi-tenant-concurrency` (distributed circuit breaker, session lock, rate limit/budget, Bedrock semaphore, k6 load test) | 🟠 | 06, 17 |
| 26 | `agent-skills` (lazy-loaded markdown knowledge, global, per-agent allowlist, keyword match) | ✅ done | 02 |
| 27 | `bedrock-cost-attribution` (AIP per model + FinOps tags; per-agent attribution via labeled token metric) | ✅ done (infra+app; deploy pending) | — |
| 28 | `llm-provider-abstraction` (multi-provider layer: `LLMProvider` Protocol + common `LLMService`; litellm candidate; preserves cost-attribution; clean-room) | 📝 design only | reopens ADR-001 |
| 29 | `openai-compat-bridge` (OpenAI `/v1` API on the supervisor → LibreChat plugs in directly; auto + per-agent models; pseudo-streaming until spec 06) | ✅ implemented | enables LibreChat (Option A) |
| 31 | `edge-gateway-worker-pool` (thin FastAPI gateway in front of the supervisor: admission control + WorkerPool backpressure + protocol isolation + global rate/budget; enables multi-replica supervisor; reuses `staffops-chaitops` `agent-api` patterns) | 📝 spec written, impl pending | 25, 29 |

> **ADR-001** ([`ADR-001-bedrock-direct-vs-strands.md`](ADR-001-bedrock-direct-vs-strands.md)): decision to keep Bedrock-direct (not adopt Strands). Reopen signal: agents stop being consultative read-only.

> **Infra (Terraform)** — was not a numbered spec; delivered in `infra/terraform/`
> (covers spec 12 `terraform-infra` + part of 13 `iam-least-privilege`): modules
> `iam/` (IRSA + read-only policies), `dynamodb/` (sessions), `bedrock/` (VPC
> endpoints), `bedrock-aip/` (cost attribution). Tags centralized in
> `provider.default_tags`.

> **Relevant fix (2026-06-16)**: `BEDROCK_MODEL_ID` corrected to use an inference
> profile (`us.anthropic.claude-sonnet-4-5-...`). The raw model id fails with
> `on-demand throughput isn't supported` — Sonnet 4.5 requires an inference
> profile. Affected config.py, .env.example, compose, and the Terraform
> `allowed_model_arns`.

> See [`ECOSYSTEM.md`](ECOSYSTEM.md): **decision (2026-06-02) = keep SEPARATE**.
> Specs 14/17/19/21 exist more mature in `staffops-chaitops` → reuse **by copy**
> (not dependency). gRPC (20) removed. Docs in English. Real differentiator =
> lightweight specialists with direct data access (not communication).

> See [`EVIDENCE-MODEL.md`](EVIDENCE-MODEL.md): catalog of cross-domain signals +
> correlation rule by causal layers + independence test (observability+sre+
> troubleshoot deliberation, 2026-06-02). Replaces the naive "≥3 signals". Feeds
> spec 18 (evidence model) and specs 09/10 (metrics catalog).

> Spec 08 (CI/CD) supersedes the "GitLab CI" reference — the repo is on
> **GitHub**, no pipeline configured. Spec 07 covers the `/healthz`+`/ready`
> prerequisite that 05-helm-chart assumes.

### Product direction (RCA-first)

The expected gain is **troubleshooting/RCA**. Critical path of the differentiator:

```
06 (async) → 17 (fan-out) → 18 (RCA)      ← product core
19 (config) early, in parallel             ← unblocks productization (config file + env)
21 (learning) after 18                     ← learning (Sonnet extractor → Opus enricher → KB)
```

### Backlog (DORMANT — do not schedule until a blocker forces it)

> These items are **explicitly deferred to a future phase**. Do NOT surface them
> as "next steps" or recurring suggestions. Pull an item ONLY when it becomes a
> hard blocker for other work — otherwise leave it here untouched.
>
> - **finops ↔ Athena**: the finops agent's `athena` datasource is denied by
>   IRSA (`enable_athena_finops=false`). Blocker trigger: someone actually needs
>   Kubecost/CUR analysis through the agent. Fix then: enable Athena in IRSA (+
>   CUR target) or drop the datasource.
> - **Distributed topology (code)**: chart renders it but the supervisor only
>   routes in-process (needs a RemoteAgent HTTP client). Blocker trigger: a real
>   need for independent per-agent scaling/isolation. ADR-001 favors in-process.

| Item | Description |
|------|-------------|
| Specialized adapters | Create `GitLabAdapter` (`type: gitlab`) and `RAGAdapter` (`type: rag`) — the generic HttpAdapter doesn't replicate the old gitlab_client's query intelligence (search_code, search_docs, list_projects). Same for RAG (Bedrock Knowledge Bases). |
| **Distributed topology (code)** | The Helm chart renders a `distributed` topology (supervisor + N specialist Deployments + mcp-server), but the supervisor **only routes in-process** (`SupervisorAgent` instantiates `GenericAgent` in memory; no HTTP call to remote specialists — `close()` is a no-op "agents are in-process"). To make `topology: distributed` functional, implement a `RemoteAgent`/HTTP supervisor client that, when configured, delegates to `http://<release>-<agent>:8001/process` instead of the in-process instance. Until then, `distributed` is infra-scaffold only. ADR-001 favors in-process (inter-agent latency irrelevant vs model cost), so this is deliberately deferred — reopen only if a real need for independent per-agent scaling/isolation emerges. Discovered 2026-07-01 while validating spec 31 in-cluster. **LOW priority / deferred (2026-07-02): a lot must land before this — 0.3.0 release, spec 14 Phase 2 (security), spec 11 (cost), budget TOCTOU. Do NOT pick up until those ship.** |
| **finops ↔ Athena mismatch** | The `finops` agent declares two datasources — `boto3 ce` (Cost Explorer, works) and `athena` (Kubecost DB). But the IRSA role is provisioned with `enable_athena_finops = false`, so `athena:StartQueryExecution` is denied → the AthenaAdapter fails on every finops query (`AccessDeniedException`), adding latency and a visible error in the response. Two options: (a) enable Athena in the IRSA (`enable_athena_finops = true` + CUR/Kubecost bucket/workgroup/db vars) once a real Athena target exists, or (b) drop the `athena` datasource from the finops agent config until then. Cost Explorer alone already returns real spend (~$191k/30d confirmed 2026-07-01). Discovered 2026-07-01 during fix homologation. Related: the slow path (Athena timeout + Bedrock ≈ 17s) also forced bumping `GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS` 15→30 in the overlay. |
| Spec 07 | Readiness probes `/healthz` + `/ready` + graceful shutdown (partially done in 06) |
| Spec 11 | Bedrock model tiering (Haiku in the classifier, Sonnet in the agents) |
| Spec 22 Phase B | Helm chart (done) — refine with ExternalSecret, NetworkPolicy |
```

Principles: efficient communication (async + fan-out), quality learning (Opus
enriches before persisting), config via file+env, **not overly complex** (round
limits as a hard stop).

### Round limits per level

| Level | max_rounds | Synthesizer model | Cost/RCA (5 agents) |
|-------|------------|-------------------|----------------------|
| 1 (MVP) | 1 | Sonnet | ~$0.17 |
| 2 (iterative) | 5 | Sonnet | ~$0.80 |
| 3 (shared context) | 10 | Opus | ~$1.65 |
| 4 (autonomous) | 25 | Opus | ~$4.20 |

### Milestone checklist (mandatory at every delivery)

Per `documentation-sync.md` — when closing any phase/milestone:

- [ ] Touched specs reflect what was **implemented** (not what was planned — fix divergences)
- [ ] ROADMAP updated (status, dates, completed items)
- [ ] README updated if anything user-visible changed
- [ ] CHANGELOG entry (if version bump)
- [ ] Tests passing (≥90% coverage)
- [ ] Real costs measured vs estimates (adjust the table in ANALYSIS.md if diverging >30%)

---

## Versioning principles (from this point on)

Per `version-management.md`:
- Bump only with a **measurable result** in the target environment, not per
  implemented feature.
- Don't go back to "Production Ready" before a stable deploy + tests + operator
  validation.
- Consolidated CHANGELOG per milestone, not per commit.

---

## Specs ↔ findings map

| Spec | Findings (AUDIT.md) |
|------|---------------------|
| 01-fix-blockers | B1, B2, B3, B4 |
| 02-unify-agent-architecture | A1, A2, A3, H1 |
| 03-fix-cache-observability | C1, C2, O1, O2, O3, O4, D5 |
| 04-harden-security | S1, S2, S3, S4, S5 |
| Phase 1 (docs/hygiene) | D1, D2, D3, D4, H2, H3, H4, H5, tests |

---

## Long-term Vision: Autonomous Multi-Agent System

**North star**: an autonomous agent system with shared memory and multi-step reasoning.
**Approach**: incremental evolution — each level is only justified when the
previous one proves a measurable limitation.

### Level 1 — Hub-and-spoke with fan-out (current specs)

The supervisor orchestrates; agents collect evidence independently; the
synthesizer correlates.

- **Delivers**: RCA in ~5s with cross-evidence from N agents in parallel.
- **Expected limitation**: blind collection — each agent doesn't know what the
  others found.
- **Promotion trigger to Level 2**: 1-round RCA is insufficient in >30% of cases
  (incomplete evidence, uncovered gaps).

### Level 2 — Iterative investigation

The synthesizer detects evidence gaps → triggers a targeted 2nd round (specific
agents, refined questions).

- **Delivers**: adaptive investigation that digs deeper where the 1st round was weak.
- **Expected limitation**: agents still operate in isolation — they refine
  without knowing what others found.
- **Promotion trigger to Level 3**: context from other agents would improve
  collection in >20% of cases (e.g. observability knowing devops found a recent
  deploy would change the metrics query).

### Level 3 — Shared memory + cross context

Agents receive a summary of what others collected (shared blackboard/scratchpad).
Each agent can refine its collection based on others' findings. It's not P2P
chat — it's 1 context broadcast → informed collection.

- **Delivers**: self-reinforcing evidence (agent A finds a deploy → agent B
  focuses on post-deploy metrics → more precise correlation).
- **Expected limitation**: the flow is still orchestrated by the supervisor;
  agents don't decide "I need to investigate X that no one asked for".
- **Promotion trigger to Level 4**: the system needs real autonomy — decisions
  without a human in the loop, auto-trigger, emergent hypotheses no individual
  agent would propose.
- **Cost**: each round with context = more tokens (N summaries × M agents).
  Validate ROI before advancing.

### Level 4 — Autonomous agents with multi-step reasoning

Agents propose hypotheses, delegate to each other, iterate until converging on an
RCA. Shared long-term memory. Auto-trigger (detects symptom → investigates
without waiting for a human). Convergence by voting/confidence, not a fixed round.

- **Delivers**: a system that solves emergent problems no previous level would.
- **Risks**: explosive token cost, infinite loops, incorrect decisions without supervision.
- **Mandatory guardrails**: per-investigation budget cap, max iterations,
  human-in-the-loop for actions (read-only for collection), kill switch.
- **Prerequisites**: Levels 1–3 validated + RCA quality metrics + controlled cost.

### Evolution principles

- **Each level proves value before advancing** — don't build Level 3 without
  evidence that Level 2 is insufficient.
- **Promotion triggers are measurable** — not "seems like we need it", but "in
  X% of cases, Y failed due to Z".
- **Cost is a real constraint** — each level multiplies tokens. Measure
  $/investigation at each level.
- **Read-only is the posture** — at all levels, agents collect and analyze. They
  never execute a fix automatically (without a human approving). (Note: read-only
  is the current posture, not eternal — see `docs/READ_ONLY_POLICY.md`.)

---

## Audit Summary (2026-06-14)

### Specs completed (tasks.md updated)

| Spec | Status | Notes |
|------|--------|-------|
| 01-fix-blockers | ✅ done (previously marked) | — |
| 02-unify-agent-architecture | ✅ done (previously marked) | — |
| 03-fix-cache-observability | ✅ done | T1–T10 all complete |
| 04-harden-security | ✅ done | T1–T10 all complete; prod items (mTLS, NetworkPolicy) documented as future |
| 06-resilience-patterns | ✅ done | T1–T10 done; T4 N/A (files deleted in spec 22); T11 formal smoke deferred |
| 08-ci-cd-pipeline | ✅ done | T1,T3–T6,T8,T9 done; T2 (mkdocs) blocked by spec 24; T7 (demo) deferred |
| 17-multi-agent-collaboration | ✅ done | T1–T10 done; T11 formal smoke deferred; coverage ~85% (gate=80%) |
| 18-rca-investigation-workflow | ⚠️ Phase 1 done | T1–T10 done; T11 deferred; **Phase 2 NOT implemented** |
| 21-incident-memory-learning | ✅ done | All phases (1–4) + docs/tests; Opus enricher deferred (Sonnet-only) |
| 22-agent-capability-manifest | ✅ done | Phase A + Phase B both complete |

### Specs NOT done

| Spec | Status |
|------|--------|
| 05-helm-chart | Not started (original; superseded partially by spec 22 Phase B) |
| 07-readiness-probes | ✅ Complete (2026-06-17) — /healthz, /ready, /health alias |
| 09-otel-instrumentation | Not started (partial coverage via otel-helper) |
| 10-metrics-and-cost-observability | ✅ Phase 1 (2026-06-18) — efficiency (collect/llm duration, prompt size) + quality (rounds) |
| 30-datasource-cache-layer | ✅ (2026-06-21) — sha256 TTL cache wired into adapters, fail-open; `aigent.cache.hits/misses` now emitted |
| 11-bedrock-resilience-cost | Not started |
| 12-terraform-infra | Delivered outside the numbered spec (see `infra/terraform/`) |
| 13-iam-least-privilege | Partially delivered in `infra/terraform/iam/` |
| 14-security-hardening | Spec written; not implemented |
| 15-sli-slo-framework | Not started |
| 16-incident-runbooks | Not started |
| 19-config-driven-platform | ❌ Substituted by spec 22 |
| 23-test-harness-docker | Not started |
| 24-docs-portal-mkdocs | Not started |
| 25-multi-tenant-concurrency | Not started |
| 28-llm-provider-abstraction | Design only |
| 29-openai-compat-bridge | Implemented |
