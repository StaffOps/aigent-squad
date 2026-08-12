---
spec: 43-agent-capability-tiers
status: not-started
completed: null
superseded_by: null
depends_on: ["37-agentic-tool-calling", "14-security-hardening"]
deferred: []
---

# Feature: Per-Agent Capability Tiers

**Spec**: `43-agent-capability-tiers`
**Severity**: 🔴 Architectural (transforms a global invariant into a per-agent, transport-agnostic enforcement model)
**Origin**: product need — write-capable agents (documentation-rag, incident-management, future operational agents) require mutating access while the existing 6 agents MUST remain read-only with zero regression.
**Depends on**: `37-agentic-tool-calling` (agentic loop + MCP allowlist), `14-security-hardening` (guardrail layers)
**Composes with**: `42-distributed-topology-internal-a2a` (dormant — the tier model MUST hold in-process AND over network hops)

---

## Problem

Today **read-only is a GLOBAL property** — enforced by 4 layers (prompts, adapter code with no write path, IRSA blanket Deny, K8s RBAC + Kyverno) plus 3 more for MCP (fail-closed tool allowlist, MCP ServiceAccount RBAC, guardrail on args/results). There is no mechanism to grant a specific agent write access without weakening the invariant for all agents.

The user wants:
1. **Default agents stay read-only** (aws, devops, finops, kubernetes, observability, security) — unchanged.
2. **New write-capable agents** that produce artifacts (GitLab MRs), write to systems of record (incident.io, Grafana silences), or eventually mutate infrastructure (pod restart, PVC resize) with human-in-the-loop.

The invariant must become **per-agent and declared**, without weakening it for agents that keep read-only.

---

## User Stories

WHEN a new agent is onboarded with `capability_tier: 0` (or no tier declared) THEN it SHALL be subject to the SAME 4+3 read-only enforcement layers that exist today — no regression.

WHEN a Tier 1 agent produces a write artifact THEN it SHALL manifest as a GitLab MR/commit that requires human merge — no direct effect on any live system.

WHEN a Tier 2 agent performs a narrow reversible write (e.g. incident.io field update, Grafana silence) THEN it SHALL be limited to the exact write surface declared in its `write_scope`, enforced at the ServiceAccount/IRSA layer AND the tool allowlist — not broader.

WHEN a Tier 2 agent creates a Grafana silence THEN it SHALL enforce a mandatory TTL (max 4h, never permanent) and produce an audit event — because silences reduce observability and blinding the operator is a first-class risk.

WHEN a Tier 3 agent attempts an infrastructure mutation (restart pod, force rollout, delete Helm release, grow PVC) THEN it SHALL require **human-in-the-loop approval** before execution — no autonomous mutation of live infrastructure.

WHEN HITL approval is requested THEN the approval request SHALL be delivered to a named Slack channel, include the exact action + blast radius + rollback path, and time out after a configurable window (default 5 minutes) with the action NOT executed on timeout.

WHEN the capability tier enforcement is evaluated THEN it SHALL be **transport-agnostic** — the same enforcement layer works whether the caller is an in-process function call or a network hop (composability with spec 42).

WHEN an agent's `agent.yaml` declares a `capability_tier` THEN the schema change SHALL be **additive only** — `capabilities` remains `list[str]` and `routing_keywords` remains a flat `list[str]` attribute on `AgentConfig`, with no rename or nesting (the keyword scan in `src/core/classifier.py` iterates `config.routing_keywords` directly; any nesting breaks routing silently).

WHEN both `read_only` and `capability_tier` are present on an `AgentConfig` THEN they SHALL be consistent by validation, not by convention — `capability_tier == 0` requires `read_only is True`, and `capability_tier > 0` requires `read_only is False`. `read_only` remains the human-readable assertion; the tier is the enforced value. An inconsistent pair is a config error, never a silent precedence rule.

WHEN a write-capable agent runs in production THEN it SHALL have its own Kubernetes ServiceAccount + IRSA role — NOT share the blanket-Deny identity used by Tier 0 agents.

WHEN the `grafana-mcp` write surface is exposed (Tier 2, silence creation) THEN the design SHALL acknowledge the verified fact that this path is single-layer (allowlist only — the Grafana SA token is already write-capable, Viewer token declined 2026-07-23) and either add a second enforcement layer or explicitly accept the residual risk with documented compensating controls.

---

## Acceptance Criteria

### Tier model
- [ ] Four tiers defined (0/1/2/3), each with distinct enforcement depth and blast radius.
- [ ] Tier 0 agents experience zero change in their enforcement posture.
- [ ] `agent.yaml` schema gains `capability_tier: int` (default 0, additive, backward-compatible).
- [ ] `agent.yaml` schema gains `write_scope: list[str]` (empty for Tier 0, enumerated for higher tiers).

### Enforcement (transport-agnostic)
- [ ] Enforcement layer identified that does NOT depend on in-process trust (works for local function calls AND network hops).
- [ ] Per-agent ServiceAccount + IRSA: Tier 0 agents keep the blanket Deny; Tier 1+ get scoped roles.
- [ ] Tool allowlist at the MCP/adapter layer reflects the declared tier (fail-closed — a Tier 0 agent cannot invoke a write tool even if the MCP server exposes one).

### Tier-specific controls
- [ ] Tier 1: write operations produce only reviewable artifacts (MR/commit), never direct system mutation.
- [ ] Tier 2: writes bounded by `write_scope`; Grafana silences enforce max TTL + audit trail.
- [ ] Tier 3: HITL gate with Slack approval, configurable timeout, fail-closed on timeout.

### Observability & audit
- [ ] Every write action (any tier > 0) emits a structured audit event (who, what, when, tier, approval_status).
- [ ] Metric: `aigent_write_actions_total{tier, agent, action, outcome}` — `aigent_` prefix, matching every existing metric in `src/core/metrics.py`.
- [ ] Silences emit `aigent_silence_created_total{agent, duration_bucket, outcome}`. Label set is **bounded by construction**: `duration_bucket` ∈ {`le_1h`, `le_2h`, `le_4h`}, never the raw `duration_seconds`. Unbounded identifiers (`incident_id`, `alertname`, `silence_id`, `matcher`) go on the audit **log record**, never on a metric label — 2000-series SDK limit, per `observability-principles`.
- [ ] Every audit event named in this spec maps to BOTH a bounded-label counter (alertable in MetricsQL) AND a structured log record carrying the high-cardinality context. An event that exists only as a log line cannot be alerted on and does not satisfy this criterion.

### Schema consistency
- [ ] `capability_tier == 0` ⟺ `read_only is True`; `capability_tier > 0` ⟺ `read_only is False`, enforced by a validator, with a test proving an inconsistent pair fails config load.

### Grafana single-layer risk
- [ ] The design explicitly addresses the grafana-mcp single-layer enforcement (allowlist-only, write-capable token).
- [ ] Either a second enforcement layer is added OR the residual risk is documented with compensating controls.

## Out of scope
- Specific agent implementations (documentation-rag, incident-management) — those are spec 44.
- A2A/distributed topology implementation — this spec only requires the tier model to be transport-agnostic so it COMPOSES with spec 42 when it activates.
- Tier 3 operational agents (pod restart, etc.) — future; this spec defines the model, spec 44+ implements specific agents.

---

## Open issues — harness 2026-08-12 (BLOCK Phase 1)

Four independent reviewers (`code-review`, `security`, `sre`, `observability`) were run against
this spec before it was committed, with instructions to refute rather than confirm. Verdicts:
COMMIT AFTER FIXES / COMMIT WITH RECORDED BLOCKERS / DO NOT COMMIT AS-IS / DO NOT COMMIT AS-IS.

The **mechanical** corrections they found are already applied above (metric prefix, bounded label
sets, `read_only` ⟺ tier validation, schema invariant stated instead of pinned to a line number,
`refuses` registered in Phase 1, doc-sync tasks). The items below are **design decisions that
remain open**. Each MUST be resolved in T0.3 before any Phase 1 code is written — this document is
committed with the holes visible, not with them papered over.

| # | Open issue | Why it blocks | Decision needed |
|---|-----------|---------------|-----------------|
| **H-1** | `agent_id` never reaches `call_tool()`. `call_tool()` is a method on `McpDatasourceAdapter`, which is bound to a **datasource**, not to an agent. T1.5 as written wires the gate into a function that has no caller identity. | The gate cannot authorize `(agent_id, tool, action)` without `agent_id`. This is the load-bearing mechanism of the whole spec. | Explicit parameter threaded from the agentic loop, a `contextvars` carrier, or per-agent adapter instances. Pick one and specify it in design.md. |
| **H-2** | For non-K8s/non-AWS write targets (incident.io, GitLab, Grafana) the gate is a **single** layer. Those are plain HTTPS calls with a bearer token — there is no IRSA/RBAC to catch a gate bypass. The check lives in the same process that executes the tool, so a routing bug or a `write_scope` typo (`grafana:*` instead of `grafana:create-silence`) bypasses it. | Today read-only holds because there is **no write code path at all**. Replacing "no code path" with "one in-process check" is a strictly weaker guarantee, and the spec presents it as defense-in-depth. | Either add an independent egress enforcement point (per-target proxy/sidecar or NetworkPolicy + allowed method/path), or accept and document the residual risk explicitly — the same treatment already given to the Grafana token. |
| **H-3** | No transitive capability attenuation. If a Tier 0 agent can delegate to a Tier 2 agent (supervisor fan-out today; A2A once spec 42 activates), the effective privilege is the **union of the reachable graph** — the caller's tier is never checked. | "Tier 0 agents are unchanged" becomes false the moment delegation exists: a Tier 0 agent can ask a Tier 2 agent to silence an alert. | Adopt `effective_tier = min(caller_tier, callee_tier)` as a spec invariant, or state explicitly that delegation to a higher tier is forbidden and enforce it. |
| **H-4** | Tier 1+ isolation is an assumption, not a requirement. Agents run **in-process** in one supervisor today (`src/supervisor/agent.py` hands `config.datasources` to `GenericAgent`), so every mounted secret is reachable by the same process. T2.2 says write agents are "likely separate deployments" — "likely" is not enforcement. | A per-agent ServiceAccount is cosmetic while the agents are co-located: a Tier 1 agent can read the Tier 2 credential. | Make a dedicated pod per Tier 1+ agent a hard requirement of this spec, or state that the SA split provides no isolation until that happens. |
| **H-5** | HITL approval is not an authentication boundary. Slack identifies the approver by user ID; any channel member can approve; the approval is not bound to the action (no nonce → replay is possible); and no state machine rejects a callback that lands after the timeout. T4.4 describes the **requesting** side only. | An approval gate that can be replayed, or honoured at T+5:01, is not a gate. This is the only control standing between the agent and irreversible infra mutation. | Nonce embedded in the Slack payload + a persisted `PENDING → APPROVED \| DENIED \| TIMEOUT` state in Redis checked atomically by the callback. Decide separately whether channel membership is accepted as the authorization boundary (acceptable if deliberate and documented). |
| **H-6** | The audit trail is writable by the actor and emitted after the fact. Events go to a structured log + Redis stream, both inside the agent's own write scope; a crash between execution and emit leaves a write with no record. | An audit trail the actor can suppress or lose is not evidence. | Emit intent **before** execution and outcome after (a missing outcome is itself a signal), and place the sink outside the agent's SA/IRSA scope. |
| **H-7** | Tier 3 mixes reversible and irreversible actions. PVC grow cannot be shrunk on the EBS CSI driver; `helm uninstall` with `reclaimPolicy: Delete` destroys volumes. The HITL template carries a `Rollback:` field that has no valid value for either. | Presenting a rollback field for an action with no rollback teaches the approver that HITL made it safe. | Split Tier 3 into 3a (reversible: pod restart, rollout restart) and 3b (irreversible: PVC grow, Helm delete) with an explicit "⚠️ NO ROLLBACK EXISTS" acknowledgement in the 3b approval, or exclude irreversible actions from this spec's scope. |

**Cross-spec note.** The reviewer that raised H-3 reported "spec 42 does not exist on disk". That
was an artefact of the branch it read (`fix/f019-shared-token-budget`, which never contained
`specs/42/`); spec 42 landed in `dev` via PR #22 on 2026-08-12. The substance of H-3 is
independent of that and stands.
