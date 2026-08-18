# Roadmap — AIgent-squad

**Work branch**: `dev`
**Base**: audit in `AUDIT.md` (2026-05-30)

This roadmap reflects the **real state** of the project, not the aspirational one
of the old README. The premise: the system **didn't build/run** at the time
(missing root Dockerfile, broken modules). Before any new feature, stabilize.

---

## Current state (honest)

Per-spec status lives in each spec's frontmatter; the **single validated
status table** is in the [Spec status (canonical)](#spec-status-canonical)
section at the bottom of this file (generated + CI-checked). Product/release
state and the current work order are below; live items are in [`BACKLOG.md`](BACKLOG.md).

**Suggested real version**: `0.4.0` cut 2026-07-15 (tag `v0.4.0`, GitHub Release
published, image `ghcr.io/staffops/aigent-squad:0.4.0` on GHCR — spec 34's
`RELEASE.md` executed for real, Phases 0-2: pre-flight, PR `dev→main` #19
merged, tag pushed, `release.yml` green, image content verified to contain
this milestone's code). Cluster (devops-core) still runs via the separate
Harbor path (`labs/aigent-squad:0.3.0-dev`, digest `e94a901`, redeployed +
homologated 2026-07-15, same code content as the 0.4.0 tag) — RELEASE.md
Phases 3-5 (chart bump/publish, overlay revert to the published chart, a
rollout actually sourced FROM the new Docker Hub tag) are explicitly
deferred, tracked as `specs/BACKLOG.md` B-25 (the Harbor-vs-Docker-Hub split
was never reconciled in any prior cycle either — not new to this one).
Post-0.4.0 on `dev`: spec 18 Phase 1.5 (EVIDENCE-MODEL correlator) is the
next real roadmap item.

> **Work order (product-first rebalance ~70% value / 30% platform, 2026-07-04)** —
> priority ordering only; per-spec status is in the canonical table below, not here:
> 1. **spec 36** (agent-native dev loop) — the multiplier; do before the rest
> 2. **quick quality fixes**: finops Athena datasource decision, F-001 aws
>    `<use_mcp_tool>` echo, spec-14 entry-point findings;
> 3. **spec 35** (structural quality gate, golden sets, RCA scenarios, groundedness) —
>    the functional twin of the attack suite;
> 4. **spec 18 Phase 1.5** — EVIDENCE-MODEL correlator + **CASE-001 real-RCA proof**;
>    not started — next real item on this list;
> 5. **process track in parallel**: specs 32 → 33 → 34 (34 executes on `0.4.0`);
> 6. **decision pending**: ADR-0007 (internal-first vs OSS) — prices the interface
>    choice (LibreChat live vs Slack v2) and the quickstart investment.
> 7. **direction (D-03, decided + refined 2026-07-04)**: the squad stays REACTIVE
>    (acts only when triggered; proactive watching = separate product). Core = spec
>    candidate `38-on-trigger-comprehension` — entity resolution + baseline verdicts
>    computed in code at trigger time (requirements seed: `docs/BEHAVIOR-BASELINES.md`;
>    entity #1 = the squad itself). Enters the queue after 35/18-Phase-1.5 (consumes
>    B-01 adapters + feedback loop); anomaly-detection integration stays OPTIONAL.
>
> Full critical-review reasoning: `specs/PRODUCT-REVIEW-2026-07.md` (PR-01..38, §§A–F).
> Live item tracking: `specs/BACKLOG.md` (findings F-*, product B-*, decisions D-*).

---

## Phases (execution order)

### Phase 0 — Stabilization (specs 01 → 02 → 03 → 04)
Prerequisite for everything. Order matters because of dependencies.

| # | Spec | Depends on | Measurable result |
|---|------|------------|--------------------|
| 1 | `01-fix-blockers` | — | `docker compose build && up` green |
| 2 | `02-unify-agent-architecture` | 01 | 5 agents on the same pattern, single contract, multi-turn |
| 3 | `03-fix-cache-observability` | 02 | deterministic cache, logs with context, OTLP |
| 4 | `04-harden-security` | 02 | authenticated endpoints, non-root containers |

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

| # | Spec | Depends on |
|---|------|------------|
| 06 | `resilience-patterns` (async-first, fail-open, circuit breaker, classifier fallback) | 02 |
| 07 | `readiness-probes` (`/healthz`+`/ready`+graceful shutdown) | 02 |
| 08 | `ci-cd-pipeline` (GitHub Actions, multi-arch, scan, coverage gate) | 01 |
| 09 | `otel-instrumentation` (7 services, propagation, Collector) | 02 |
| 10 | `metrics-and-cost-observability` (RED + tokens/cost $) | 09 |
| 11 | `bedrock-resilience-cost` (Haiku in the classifier, prompt caching, tiering) | 06 |
| 12 | `terraform-infra` (DynamoDB/ElastiCache/ECR/IRSA/Secrets) | — |
| 13 | `iam-least-privilege` (read-only per agent + explicit deny) | 12 |
| 14 | `security-hardening` (defense-in-depth anti-prompt-injection: multi-language Bedrock Guardrails, fail-closed, canary/output-filter, rate/budget; read-only as a security posture = competitive differentiator) | 04 |
| 15 | `sli-slo-framework` | 10 |
| 16 | `incident-runbooks` | 06, 07 |
| 17 | `multi-agent-collaboration` (cross-domain fan-out/fan-in + synthesis, agent-as-tools 1 hop) | 06, 09 |
| 18 | `rca-investigation-workflow` (**differentiator**: parallel evidence → timeline → correlation → RCA, read-only) | 17, 09, 19 |
| 19 | `config-driven-platform` (YAML + env override, secrets outside the YAML, agent registry) | — |
| ~~20~~ | ~~`grpc-inter-agent-mesh`~~ — **REMOVED** (2026-06-02): latency irrelevant vs model calls | — |
| 21 | `incident-memory-learning` (incident memory + similar-case retrieval; simple learning) | 18 |
| 22 | `agent-capability-manifest` (**open** roster via YAML + collaboration via `capabilities`/`evidence_types`/`delegates_to` metadata; **replaces 19**) | — |
| 23 | `test-harness-docker` (`Dockerfile.test` + `pytest --cov-fail-under=90` with mocks; same harness dev↔CI; consumed by 08) | — |
| 24 | `docs-portal-mkdocs` (MkDocs Material portal `src`→`public`; README becomes an index; ADRs; consolidates/deletes ghost docs) | — |
| 25 | `multi-tenant-concurrency` (distributed circuit breaker, session lock, rate limit/budget, Bedrock semaphore, k6 load test) | 06, 17 |
| 26 | `agent-skills` (lazy-loaded markdown knowledge, global, per-agent allowlist, keyword match) | 02 |
| 27 | `bedrock-cost-attribution` (AIP per model + FinOps tags; per-agent attribution via labeled token metric) | — |
| 28 | `llm-provider-abstraction` (multi-provider layer: `LLMProvider` Protocol + common `LLMService`; litellm candidate; preserves cost-attribution; clean-room) | reopens ADR-001 |
| 29 | `openai-compat-bridge` (OpenAI `/v1` API → LibreChat plugs in directly; auto + per-agent models; pseudo-streaming until spec 06) | enables LibreChat (Option A) |
| 30 | `datasource-cache-layer` (sha256 TTL cache in the adapter base; fail-open; emits `aigent.cache.hits/misses`) | — |
| 31 | `edge-gateway-worker-pool` (thin FastAPI gateway in front of the supervisor: admission control + WorkerPool backpressure + protocol isolation + global rate/budget; enables multi-replica supervisor; reuses `staffops-chaitops` `agent-api` patterns) | 25, 29 |
| 32 | `spec-lifecycle-ssot` (**process**: status frontmatter per spec + CI lint script = single source of truth; `specs/README.md` process doc; `specs/BACKLOG.md` for dormant/findings/deferred; HANDOFF overwrite rule; ROADMAP plan-only) | — |
| 33 | `operational-review-loop` (**process**: recurring review — measure ALL promotion triggers (`specs/TRIGGERS.md`), reconcile real Bedrock cost vs ANALYSIS estimates, triage BACKLOG; dated records in `specs/reviews/`; dormant phases promote ONLY with a review citation) | 32 |
| 34 | `release-runbook` (**process**: `RELEASE.md` sequencing dev→main→tag→image→chart→overlay→rollout→homologation→close across the 3 repos; tag/appVersion/overlay coherence rule; credential-hygiene close step) | 32, 33 |
| 35 | `quality-eval-harness` (**product**: T1 structural CI gate — scaffolding leaks/raw errors/ungrounded resource IDs, deterministic $0; T2 scored golden sets per agent + 3 fixture-fed RCA scenarios mapped to EVIDENCE-MODEL signatures; groundedness dimension; `aigent.eval.score`; the functional twin of the attack suite) | 36; feeds 33/34 |
| 36 | `agent-native-dev-loop` (**multiplier**: out-of-box local run, `Makefile` canonical verbs, auto-stub for the private dep, committed `.claude/` (settings + skills, AGENTS.md stays canonical), failure-mode playbook, CI runs the same make targets) | — |

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

### Backlog / dormant work

Moved to [`BACKLOG.md`](BACKLOG.md) (spec 32 T7/T8). Dormant items (finops↔Athena
re-enable, distributed-topology RemoteAgent) keep their blocker-triggers there;
product items are `B-*`, findings `F-*`, decisions `D-*`. Do not re-add a backlog
here — it belongs in one place.

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

## Long-term Vision

Moved to [`VISION.md`](VISION.md) — north-star / phased maturity levels (spec 32 T8).
ROADMAP is plan-only from here.

---

## Spec status (canonical)

> Single source of truth is each spec's `requirements.md` frontmatter. The table
> below is generated by `scripts/specs_status.py --table` and **validated in CI**
> (the `specs_status` gate). Do not hand-edit — re-run the script. Live items
> (findings `F-*`, product `B-*`, decisions `D-*`, deferred, dormant) live in
> [`BACKLOG.md`](BACKLOG.md), not here.

<!-- specs-status:start -->
| Spec | Status | Completed | Notes |
|------|--------|-----------|-------|
| 01-fix-blockers | bugfix | — | single-file bugfix.md |
| 02-unify-agent-architecture | done | — |  |
| 03-fix-cache-observability | bugfix | — | single-file bugfix.md |
| 04-harden-security | done | — |  |
| 05-helm-chart | superseded | — | → 22-agent-capability-manifest |
| 06-resilience-patterns | done-with-deferrals | — | deferred: T11 formal smoke |
| 07-readiness-probes | done | 2026-06-17 |  |
| 08-ci-cd-pipeline | done-with-deferrals | — | deferred: T7 demo stage |
| 10-metrics-and-cost-observability | done | 2026-06-18 |  |
| 11-bedrock-resilience-cost | done | 2026-07-02 |  |
| 14-security-hardening | done | 2026-07-03 | depends_on: 04-harden-security |
| 17-multi-agent-collaboration | done-with-deferrals | — | deferred: T11 formal smoke |
| 18-rca-investigation-workflow | in-progress | — |  |
| 19-config-driven-platform | superseded | — | → 22-agent-capability-manifest |
| 21-incident-memory-learning | done-with-deferrals | — | deferred: Opus enricher (Sonnet-only for now), Slack approval flow |
| 22-agent-capability-manifest | done | — |  |
| 23-test-harness-docker | done | 2026-06-18 |  |
| 24-docs-portal-mkdocs | done | — |  |
| 25-multi-tenant-concurrency | done | — |  |
| 26-agent-skills | done | — | depends_on: 02-unify-agent-architecture |
| 27-bedrock-cost-attribution | done | — |  |
| 28-llm-provider-abstraction | design-only | — |  |
| 29-openai-compat-bridge | done | 2026-07-01 |  |
| 30-datasource-cache-layer | done | 2026-06-21 |  |
| 31-edge-gateway-worker-pool | done-with-deferrals | 2026-07-01 | deferred: T21 k6 load test |
| 32-spec-lifecycle-ssot | done | 2026-07-17 |  |
| 33-operational-review-loop | not-started | — | depends_on: 32-spec-lifecycle-ssot |
| 34-release-runbook | done-with-deferrals | 2026-07-15 | deferred: RELEASE.md Phases 3-5 (chart bump / overlay / rollout) — gated on B-25 |
| 35-quality-eval-harness | done-with-deferrals | 2026-07-15 | deferred: TRIGGERS.md rows (deferred to spec 33 T1) |
| 36-agent-native-dev-loop | done-with-deferrals | 2026-07-04 | deferred: T11 independent review (fresh-clone dry run + .claude contract) |
| 37-agentic-tool-calling | done | 2026-07-20 |  |
| 38-model-tier-escalation | done | 2026-07-22 | depends_on: 37-agentic-tool-calling |
| 39-observability-rca-uplift | done-with-deferrals | 2026-07-23 | deferred: T3.4 deterministic investigation.py path (Phase 2 — prompt-RCA validated live, trigger not met) |
| 40-agentic-context-management | done | 2026-07-22 | depends_on: 37-agentic-tool-calling |
| 41-calibrated-honesty-structured | done-with-deferrals | 2026-08-08 | deferred: T3 histogram bucket boundaries (default SDK buckets — explicit boundaries need a View in the otel_helper MeterProvider; not settable via opentelemetry-api 1.29.0 create_histogram) |
| 42-distributed-topology-internal-a2a | not-started | — | depends_on: 37-agentic-tool-calling, 14-security-hardening |
| 43-agent-capability-tiers | in-progress (Phase 1 done) | — | depends_on: 37-agentic-tool-calling, 14-security-hardening |
| 44-write-capable-agents | not-started (blocked by 43 Phase 3) | — | depends_on: 43-agent-capability-tiers, 21-incident-memory-learning, 37-agentic-tool-calling |
| 45-supply-chain-hardening | done | 2026-08-17 | depends_on: 08-ci-cd-pipeline, 36-agent-native-dev-loop |
<!-- specs-status:end -->
