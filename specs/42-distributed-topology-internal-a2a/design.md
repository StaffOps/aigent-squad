# Design: Distributed topology — internal A2A

## Architecture

Transition from:
```
Gateway :8000 → Supervisor :8001 → [in-process GenericAgent × 6]
                                    (function calls, same PID)
```

To:
```
Gateway :8000 → Supervisor :8001 → A2A Tasks → Specialist :800N (×6)
                                    (HTTP/JSON-RPC, separate PIDs)
                                    mTLS + SUPERVISOR_AGENT_TOKEN
```

The gateway is untouched. The supervisor remains the single orchestration point
— it gains an A2A client that replaces the current direct `self.agents[name].process_request()`
call. Each specialist becomes an A2A-capable HTTP server exposing its Agent Card
at the well-known path and accepting Tasks from the supervisor.

```
┌─ Supervisor (A2A client) ─────────────────────────────────────────────┐
│                                                                       │
│  classify(input) → AgentMatch[]                                       │
│  for each agent in matches:                                           │
│     task = a2a_client.send_task(card.url, {message: input, ...})      │
│     wait task.status == COMPLETED (SSE) or TIMEOUT                    │
│  synthesize(results, failed)                                          │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
          │ A2A Task/SSE (mTLS)         │ A2A Task/SSE (mTLS)
          ▼                             ▼
   ┌─ aws-agent :8010 ─┐       ┌─ observability-agent :8011 ─┐
   │  A2A server        │       │  A2A server                  │
   │  GenericAgent      │       │  GenericAgent                │
   │  agentic_loop      │       │  agentic_loop                │
   │  MCP adapters      │       │  MCP adapters                │
   │  IRSA (own role)   │       │  IRSA (own role)             │
   └────────────────────┘       └──────────────────────────────┘
```

### Key design constraints

1. **Agent Card = additive fields on `agent.yaml`**. The existing `capabilities: list[str]`
   and `routing_keywords: list[str]` are untouched in name and shape.
   A new top-level key `a2a` (optional, absent = in-process mode) carries the
   card metadata. `src/core/classifier.py:222` still reads `config.routing_keywords`
   directly — no indirection, no rename.

2. **Hybrid mode**: the supervisor decides at startup whether to dispatch
   in-process or via A2A, per agent, based on the presence of `a2a.url` in the
   agent's config. This makes the transition incremental — one specialist at a
   time.

3. **Security layers carry over identically**: each specialist process runs the
   full `InputScanner` → Guardrail → agentic_loop → `OutputFilter` pipeline.
   The supervisor additionally guards the *inbound* A2A response (Artifact)
   through the existing output-filter path before synthesis.

## Components

| Component | Change |
|-----------|--------|
| `agent.yaml` (schema) | **Additive** `a2a:` block (url, auth, health). Existing fields frozen. |
| `src/core/a2a_client.py` (new) | Sends A2A Tasks, receives Artifacts via SSE. Circuit breaker per agent. |
| `src/supervisor/agent.py` | `_fan_out` gains a branch: if agent has `a2a.url`, dispatch via `a2a_client` instead of `self.agents[name].process_request()`. |
| `src/specialist/a2a_server.py` (new) | Thin HTTP layer: Agent Card at well-known path, accepts Tasks, delegates to `GenericAgent.process_request()`. |
| `src/core/token_budget.py` | Extended to accept reported tokens from remote responses and add to session bucket. |
| `src/core/registry.py` | Skip instantiating in-process `GenericAgent` for agents with `a2a.url` — they run elsewhere. |
| `docker-compose.yaml` | Optional profile (`--profile distributed`) spinning up specialist containers alongside the supervisor. |

## Rationale

### Decision 1: A2A protocol vs plain gRPC/REST between our own services

**Choice**: adopt A2A (Agent-to-Agent protocol, Linux Foundation, contributed by
Google) as the inter-service wire format, rather than bespoke gRPC or REST.

**Justification, in order of strength**:

1. **Future-proofing at near-zero marginal cost.** A2A's value proposition is
   cross-framework interop — which an all-ours topology does not *need* today.
   However, if a third party ever appears (condition 1 of the dormant trigger),
   we already speak the protocol. The alternative — distribute first on plain
   REST, then retrofit A2A — is a second migration for something we can build
   natively once.
2. **Structured Task lifecycle.** A2A defines Task states
   (submitted/working/completed/failed/canceled), SSE streaming, and artifact
   typing. Building this from scratch on REST would re-derive it anyway.
3. **Agent Card as a discovery contract.** The card format (capabilities, skills,
   auth mechanisms) maps naturally to our `agent.yaml` — the additive enrichment
   is ~10 lines per agent.
4. **Community ecosystem.** 6 official SDKs, growing adoption in the LLM-ops
   space. If we hire or partner, A2A literacy transfers.

**Accepted trade-offs**:

| Cost | Reality |
|------|---------|
| Protocol overhead for purely internal traffic (JSON-RPC envelope, Task state machine) | Negligible vs the LLM invocation latency (~3-30s per agent). Wire overhead is <1ms on localhost. |
| Additional dependency (A2A SDK or hand-rolled client) | Client is ~200 LoC (JSON-RPC POST + SSE read). We hand-roll to avoid framework lock-in (consistent with ADR-001 posture). |
| Learning curve for contributors | A2A's surface for *internal* use is small: send Task, receive Artifact. We do not use push notifications, multi-party negotiation, or capability-based discovery across networks. |

**When this decision would be wrong (reopen signals)**:

- If A2A development stalls (no spec release in 12 months, community
  abandonment) — then it's protocol overhead without ecosystem payoff; switch to
  raw HTTP + internal contract.
- If internal latency requirements tighten below 10ms RTT per specialist call
  (rules out HTTP) — then gRPC with streaming would be necessary.
- If the third-party trigger *never* fires after 12 months in production —
  reassess whether the A2A envelope adds value, or simplify to raw REST.

**Alternatives discarded**:

- **Plain gRPC (custom .proto)**: lower latency, but requires maintaining our own
  IDL, offers nothing reusable if a third party appears, and duplicates the Task
  lifecycle model we'd build anyway. Discarded because the complexity delta vs
  A2A-over-HTTP is tiny for our latency envelope (3-30s LLM calls).
- **Plain REST (OpenAPI)**: maximum simplicity, but we'd reinvent Task state,
  streaming, and card-based discovery. Acceptable as a fallback if A2A proves
  unmaintained (see reopen signal above).
- **MCP for agent-to-agent**: MCP is agent↔tool (capability invocation), not
  agent↔agent (task delegation with artifacts). Using it here would abuse the
  abstraction.

**The honest answer**: the protocol's internal value is modest. The real argument
is "distribute first, and A2A costs almost nothing extra vs raw REST, while
positioning for the hypothetical third party." If that third party never
materializes, we carry a thin JSON-RPC envelope as dead weight — not expensive,
but not free either.

---

### Decision 2: Process-per-agent vs current in-process fan-out

**Choice**: each specialist becomes an independently deployable process. The
supervisor remains a single process (it has no LLM invocation of its own beyond
classification and synthesis).

**Justification, in order of strength**:

1. **Fault isolation is the primary value.** An unrecoverable error (OOM, segfault
   in a native extension, infinite loop in a tool adapter) in one specialist
   currently kills all six. Process isolation limits blast radius to the affected
   agent.
2. **Independent scaling.** If 70% of traffic goes to `aws` + `kubernetes`, those
   specialists can scale independently without carrying the idle memory of
   `finops` and `security`.
3. **Independent release cadence.** A `prompt.md` change for one agent should not
   require re-deploying five others. Today it does (single image).
4. **Technology heterogeneity (future).** A Rust-based adapter or a Go-based MCP
   server can be a specialist without Python FFI.

**Acknowledged**: ADR-001 and ADR-0002 explicitly favor in-process. Their
rationale was *simplicity at current scale*. This decision does NOT claim they
were wrong — it claims the PRECONDITIONS they assumed (small scale, one team,
rapid iteration) may not hold indefinitely, and documents the migration path.

**Accepted trade-offs**:

| Cost | Reality |
|------|---------|
| Operational complexity: N deployments, N health checks, N rollback plans | This is the dormant entry's "objection 2" — the dominant cost. Addressed in Phase A of the task plan: earn operational maturity first. |
| Network latency added to fan-out | LLM call is 3-30s; network hop adds <10ms on same-cluster. Not meaningful. |
| Per-agent containers = more memory (each loads Python + deps) | ~150MB base per specialist × 6 = ~900MB added. Acceptable on a cluster that runs VictoriaMetrics at 32GB. |
| Debugging harder (distributed traces instead of stack traces) | Phase A step 2 addresses this: cross-agent tracing must work before distribution is allowed. |

**When this decision would be wrong (reopen signals)**:

- If the platform stays at 1-3 QPS with no specialist-specific scaling need for
  12+ months in production — process-per-agent is overhead without payoff; revert
  to in-process.
- If the team shrinks to 1 person maintaining all agents — the operational
  surface of N deployments is unjustifiable for a solo maintainer.
- If Python's shared-nothing multiprocessing (no GIL sharing) means we get
  fault-isolation via `multiprocessing` without network hops — worth
  re-evaluating.

**Alternatives discarded**:

- **Sidecar per agent (same pod, separate containers)**: lighter than separate
  pods but loses independent scaling. Discarded because it solves only fault
  isolation, not the scaling or release-cadence goals.
- **In-process with supervisor-restart-on-panic**: the current model with a
  process-level watchdog. Discarded because it restarts ALL agents, not just the
  failed one, and doesn't address scaling.
- **Thread-per-agent with fault domains**: Python's GIL and lack of true thread
  isolation make this non-viable for crash isolation.

---

### Decision 3: Per-session cost guardrail across network hops

> **⚠️ Corrected 2026-08-11 after adversarial review.** The first draft of this
> decision asserted that `budget_tracker` in `token_budget.py` "already uses Redis".
> **That is false.** `src/core/token_budget.py:25` holds `self._usage: dict[str, int]`
> guarded by a `threading.Lock`, and its own docstring says it is "deliberately simple
> (in-memory dict)" and that a multi-replica deployment "would need DynamoDB or Redis".
> The error mattered: it turned a **prerequisite** into an assumed given, and made this
> decision look cheaper than it is. The corrected version below treats moving the budget
> to a shared store as work that must land *before* distribution, not alongside it.

**Choice**: the token budget becomes a **centralized** per-session counter in a shared
store (Redis), and that migration is a **hard prerequisite of Phase A** — not part of the
A2A work. Each specialist reports its consumption back to the supervisor in the A2A
Artifact metadata; the supervisor applies the delta to the session bucket before
dispatching the next specialist.

**Why this is a prerequisite rather than a detail**: today the budget is per-process, and
today there is exactly one process, so per-process happens to equal per-session. The
moment specialists become independent deployables, an in-memory counter means **each
replica enforces its own private budget** — N replicas silently permit up to N× the
intended spend, with no error and no signal. That is a cost-control regression that
would ship looking like success. Note this is already latent today for the **gateway**,
which runs multiple replicas; distribution widens it rather than creating it.

**Justification, in order of strength**:

1. **Single source of truth.** A distributed budget (per-agent local counters reconciled
   later) risks overspend between reconciliation windows. Redis atomic `INCRBY` is
   already proven in this codebase — the **rate limiter** uses Redis, which is where the
   original draft's confusion came from.
2. **No specialist can run away.** The supervisor checks the budget BEFORE dispatching
   and AFTER receiving, bracketing the specialist's spend. If the artifact reports tokens
   greater than the remaining budget, the supervisor hard-caps synthesis.
3. **API shape survives.** The existing `budget_tracker.check_budget(session_id)` /
   `record_usage(session_id, tokens)` surface can be kept while the backing store changes
   underneath, so call sites do not churn. The *implementation* is replaced, not the
   contract.

**Accepted trade-offs**:

| Cost | Reality |
|------|---------|
| Migrating the budget store is real work, and it lands before any A2A benefit | Unavoidable, and worth doing on its own merits — the multi-replica gateway already has this hole. Sequencing it first means distribution cannot silently multiply spend. |
| Specialist self-reporting tokens (could lie or omit) | Both endpoints are ours (internal A2A). A malicious specialist means a compromised deploy, which is a larger problem than token leakage. Defence: the supervisor also tracks wall-clock as a secondary cap. |
| Redis round-trip per dispatch | The rate-limiter path already pays a same-cluster Redis round-trip, so the *pattern* and its latency are known — but this is a **new** call on the budget path, not a free ride on an existing one. Do not repeat the original draft's claim that it costs nothing. |
| Redis becomes a hard dependency of cost control | It already is for rate limiting. Decide the failure mode explicitly: rate limiting today fails **open**; a budget that fails open under Redis loss is an unbounded-spend path. Recommend fail-closed for budget, which is a deliberate divergence and must be stated in the implementing spec. |

**When this decision would be wrong**: if we move to third-party federation (untrusted
specialists), self-reported tokens cannot be trusted — that would need proxy-metered
counting of artifact content on the supervisor side. For internal-only, self-report is
sufficient. Also wrong if session fan-out ever becomes large enough that a single Redis
key is contended; at that point shard by session prefix.

---

### Decision 4: Partial-fan-out failure semantics

**Choice**: extend the existing `_fan_out` contract — `asyncio.gather(*tasks, return_exceptions=True)` with `ok` / `failed` lists — to the network case, adding:
- **Per-agent timeout** (configurable, default: loop budget + 10s margin).
- **Circuit breaker** per specialist (existing `CircuitBreaker` pattern, reused).
- **Degraded synthesis**: the synthesizer already receives `(agent_id, content)[]` + `failed: list[str]` and produces a partial answer citing which agents were unavailable.

No new semantics invented — the current behavior merely gains a network timeout
dimension.

**Justification**: the synthesizer is already built to handle partial results.
Introducing retries or quorum-based routing adds complexity without user value
at this scale (≤3 agents per fan-out, most queries route to 1).

**Accepted trade-offs**:

| Cost | Reality |
|------|---------|
| A specialist timeout = user waits the full timeout before getting a partial answer | Timeout is tight (loop budget + 10s). The alternative — canceling early — loses work in progress. For costly LLM calls, waiting is preferable to wasting tokens. |
| No retry (fail-fast) | An LLM call that timed out after 30s is unlikely to succeed immediately on retry. Better to degrade than double-spend. |

**When this decision would be wrong**: if specialists become stateful (holding
partial results that can be resumed) — then retry-with-checkpoint makes sense.
Currently all agents are stateless per-request.

---

### Decision 5: Read-only invariant in a distributed topology

**Choice**: the read-only invariant is enforced **at the specialist** — each
specialist's own `InputScanner` + Guardrail + adapter allowlist + IRSA deny is
the enforcement boundary. The supervisor NEVER asks a specialist to perform a
write; the A2A Task schema has no "write" capability negotiation.

**Justification, in order of strength**:

1. **Defense-in-depth unchanged.** Today, read-only is enforced at 4 layers per
   agent. Distributing changes nothing — each agent process still has those 4
   layers. The network hop doesn't add a write path.
2. **No delegation confusion.** The supervisor sends a *query* (user's natural
   language question) as the Task message. The specialist decides which tools to
   call, subject to its own read-only allowlist. The supervisor never constructs
   a tool call — it delegates the *question*, not an *action*.
3. **Outbound response filtering.** The specialist applies `OutputFilter` on its
   own response. The supervisor additionally passes the received Artifact through
   `output_filter` before synthesis — double-filtered, same as today's per-agent
   + synthesis path.

**Accepted trade-offs**:

| Cost | Reality |
|------|---------|
| Redundant filtering (specialist + supervisor both filter output) | Costs <5ms; defense-in-depth demands it. A bug in one layer doesn't bypass the other. |
| A compromised specialist could claim read-only but execute writes | Same risk as today (a compromised agent in-process). Mitigation: IRSA explicit-deny, Kubernetes RBAC, MCP SA audit — all independent of the application layer. |

**When this decision would be wrong**: if we ever need a specialist to perform a
write (remediation agent) — the invariant changes. But that change is gated by
`docs/READ_ONLY_POLICY.md` preconditions (spec 14 shipped, HITL approval) and is
orthogonal to distribution.

---

## Agent Card schema (additive to `agent.yaml`)

```yaml
# Existing fields (FROZEN — never rename or restructure):
name: aws
description: "..."
capabilities: [ec2_inventory, security_group_audit, ...]   # list[str], unchanged
routing_keywords: [ec2, instance, s3, ...]                  # flat list, read at classifier.py:222
# ... (datasources, cache, model, read_only, etc.)

# NEW — additive A2A block (optional; absence = in-process mode):
a2a:
  url: "http://aws-agent:8010"        # base URL of the specialist A2A server
  auth: "bearer"                       # auth mechanism (initially: bearer token)
  health: "/health"                    # health check path
  timeout_ms: 130000                   # per-task timeout (loop budget + margin)
```

The `a2a` key is optional. When absent, the supervisor dispatches in-process (the
current behavior). When present, the supervisor routes via the A2A client. This
enables incremental migration, one agent at a time.

**UNVERIFIED** (do not assert as fact): the current A2A spec version number and
the exact well-known discovery path. Check the primary source
(`github.com/a2aproject/A2A`) before implementation.

## Invariants

1. `agent.yaml` additive-only — no rename, no nesting of existing fields.
2. Classifier routing (`config.routing_keywords`) unchanged.
3. Security pipeline (InputScanner, Guardrail, OutputFilter, IRSA deny, RBAC)
   enforced per-agent, whether in-process or remote.
4. Per-session token budget atomic in a SHARED store, honoured regardless of
   distribution. **Not true today** — it is an in-memory dict per process
   (`token_budget.py:25`), so this invariant must be established by T3a before
   distribution, not assumed. See the correction note on Decision 3.
5. Fan-out partial failure → degraded synthesis (not crash).
6. Read-only invariant unchanged (specialist refuses writes locally; supervisor
   never asks for writes).
7. Spec 22 (`agent-capability-manifest`) is `status: done`, is a local routing
   hint — not an Agent Card. Untouched by this spec.

## Verification

- **Unit**: A2A client/server round-trip with mocked GenericAgent (in-process
  parity: same input → same output).
- **Integration**: `docker compose --profile distributed` with one specialist
  extracted; smoke test query returns equivalent answer.
- **Regression**: the full `make test` suite passes unchanged (keyword routing,
  fan-out synthesis, guardrail, budget).
- **Fault injection**: kill one specialist mid-request; verify degraded synthesis
  cites the failure.
- **Tracing**: a single trace spans gateway → supervisor → specialist → Bedrock,
  visible in OTel collector.
- **Security**: specialist rejects Tasks without valid auth token. Specialist
  refuses write-implying tool calls (existing allowlist test suite).

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Operational overhead unjustified at current scale | High (1-3 QPS) | Gated by dormant trigger; spec stays `draft` until triggered |
| A2A spec instability (breaking changes) | Medium | Our internal surface is minimal (Task + Artifact); pin SDK version, hand-rolled client decouples from upstream churn |
| Token budget race condition (concurrent fan-out both spend before checking) | Low | Redis atomic INCRBY; check-before-dispatch bracket |
| Distributed debugging harder than in-process | Medium | Phase A step 2 mandates working cross-agent tracing BEFORE extracting first specialist |
| Container memory increase (~900MB total for 6 agents) | Low | Acceptable; monitored via ScaleOps |
