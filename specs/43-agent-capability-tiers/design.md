# Design: Per-Agent Capability Tiers

## Architecture

Transform the global read-only invariant into a **per-agent, declarative capability tier** enforced
at a layer that is transport-agnostic (works in-process today and across network hops when spec 42
activates). The enforcement point is the **capability gate** — a middleware/interceptor that sits
between the caller (supervisor, A2A client, or any dispatch mechanism) and the tool execution layer.

```
                    ┌────────────────────────────────────────────────┐
                    │         Capability Gate (enforcement point)     │
                    │  ┌──────────────────────────────────────────┐  │
 dispatch ──────────┤  │ 1. Read agent.yaml → capability_tier     │  │
 (in-process call   │  │ 2. Read agent.yaml → write_scope         │  │
  OR network hop)   │  │ 3. Validate: requested action ∈ scope?   │  │──▶ tool execution
                    │  │ 4. Tier 3? → HITL gate (block until OK)  │  │    (adapter/MCP)
                    │  │ 5. Emit audit event                       │  │
                    │  └──────────────────────────────────────────┘  │
                    └────────────────────────────────────────────────┘
                                        │
                    ┌───────────────────────────────────────────┐
                    │  Infrastructure enforcement (defense-in-depth)  │
                    │  • Per-agent ServiceAccount + IRSA               │
                    │  • K8s RBAC scoped to declared write_scope       │
                    │  • Kyverno: reject mutations from Tier 0 SA     │
                    └───────────────────────────────────────────┘
```

## Tier definitions

| Tier | Name | Blast radius | Enforcement depth | Examples |
|------|------|-------------|-------------------|----------|
| **0** | Read-only | None (observation only) | 4+3 layers (unchanged from today) | aws, devops, finops, kubernetes, observability, security |
| **1** | Reviewable artifact | Indirect — human reviews before effect | Capability gate + GitLab MR (no live effect) + scoped IRSA (git push to branch only) | documentation-rag (writes to GitLab as MR) |
| **2** | Narrow reversible write | Bounded — scoped to a specific system of record, reversible | Capability gate + scoped IRSA + scoped MCP allowlist + TTL/audit for silences | incident-management (incident.io fields, Grafana silences) |
| **3** | Infrastructure mutation | High — affects live workloads | All of Tier 2 + mandatory HITL approval gate + Slack channel + timeout | future: pod restart, rollout force, Helm release delete, PVC grow |

### Why these boundaries (not arbitrary)

The tier boundaries track a concrete escalation in **irreversibility × autonomy**:

- **0→1**: the agent produces output, but a human stands between the output and any system state change. Zero autonomous effect.
- **1→2**: the agent changes state in a system of record, but the change is (a) narrow (scoped), (b) reversible (silence can be deleted, incident.io field can be re-set), and (c) does not affect infrastructure availability.
- **2→3**: the agent changes live infrastructure — a pod restart drops in-flight requests, a Helm delete removes a workload, a PVC grow is irreversible. Autonomous execution here is unacceptable per ADR-001.

## Components

| Component | Responsibility | Change |
|-----------|----------------|--------|
| `agent_config.py` | Schema | Add `capability_tier: int = 0` + `write_scope: list[str] = []` + validation |
| `capability_gate.py` (new) | Enforcement | Intercepts tool calls; validates against tier + scope; emits audit; gates HITL |
| `adapters.py` | Tool execution | `call_tool()` passes through the capability gate before execution |
| `hitl_gate.py` (new) | HITL approval | Slack webhook + poll/callback; timeout → deny |
| `audit.py` (new or extend) | Audit trail | Structured event per write action |
| Helm chart / Terraform | Infra | Per-agent ServiceAccount + IRSA role; Kyverno policies |

## `agent.yaml` schema additions (additive only)

```yaml
# Tier 0 (default — backward-compatible, no new fields required)
name: observability
capability_tier: 0          # optional, defaults to 0
# write_scope absent or empty → read-only invariant holds

# Tier 1
name: documentation-rag
capability_tier: 1
write_scope:
  - "gitlab:push-branch"
  - "gitlab:create-mr"

# Tier 2
name: incident-management
capability_tier: 2
write_scope:
  - "incident-io:update-field"
  - "incident-io:create-action"
  - "grafana:create-silence"
  - "grafana:delete-silence"

# Tier 3 (future)
name: cluster-ops
capability_tier: 3
write_scope:
  - "kubernetes:restart-pod"
  - "kubernetes:rollout-restart"
  - "kubernetes:delete-helm-release"
  - "kubernetes:expand-pvc"
hitl:
  channel: "#ops-approvals"
  timeout_seconds: 300
  approvers: ["oncall-sre"]
```

**Validation rules** (enforced at config load + CI):
- `capability_tier: 0` → `write_scope` MUST be empty (or absent).
- `capability_tier: 3` → `hitl` block MUST be present.
- `write_scope` entries are namespaced strings (`system:action`); unknown systems fail validation.
- `capabilities` (list[str]) and `routing_keywords` (flat) are UNTOUCHED — additive only.

---

## Rationale (decisions)

### Decision 1: Enforcement at the capability gate layer, NOT at the transport layer

**Choice**: a `CapabilityGate` that intercepts every tool invocation and validates
`(agent_id, tool_name, action) ∈ declared_scope` — regardless of how the invocation arrived
(in-process `call_tool()`, HTTP from a remote supervisor, A2A Task).

**Justification (ordered by strength)**:
1. **Transport-agnostic by construction.** The user's explicit hard constraint: the model must hold
   both in today's single process AND distributed. An enforcement point inside the tool execution
   layer (below transport) satisfies this by definition — it sees every call regardless of origin.
2. **Defense-in-depth with existing layers.** The gate does not REPLACE the 4+3 layers — it ADDS
   a per-agent discriminator on top. Tier 0 agents still have IRSA blanket Deny + adapter code with
   no write path + MCP read-only allowlist. The gate is the FIRST check; infra enforcement is the LAST.
3. **Single enforcement point to audit.** Every write action passes through one code path that emits
   the audit event — simpler to prove correct than distributed enforcement across N adapters.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Single point of failure for write authorization | Compensated by infra-layer defense-in-depth (IRSA/RBAC still block even if gate bypassed) |
| Gate must be imported by every execution path (in-process, future remote) | Architecturally simple — one `call_tool()` wrapper; remote paths proxy through it |

**When this decision would be wrong (signals to reopen)**:
- If the number of execution paths proliferates (>3) and maintaining the gate import becomes fragile — then enforcement should move into the ServiceAccount/IRSA layer exclusively (but that layer is not transport-agnostic for non-K8s targets like incident.io).
- If a compliance requirement mandates that ONLY the infra layer (not app code) is the enforcement point — then the gate becomes advisory-only and all enforcement moves to IAM/RBAC.

**Alternatives discarded**:
- *Enforce only at IRSA/RBAC* — insufficient because non-K8s targets (incident.io, GitLab API) are not covered by K8s RBAC; would require a separate mechanism per external system.
- *Enforce at the transport layer (NetworkPolicy)* — does not work in-process; also cannot distinguish "read tool call from Tier 0 agent" from "write tool call from Tier 2 agent" on the same network path.

---

### Decision 2: Per-agent ServiceAccount + IRSA (blanket Deny stays for Tier 0)

**Choice**: each agent with `capability_tier > 0` gets its own Kubernetes ServiceAccount annotated
with a scoped IRSA role. Tier 0 agents KEEP the shared SA with the blanket write Deny.

**Justification**:
1. **Zero regression for Tier 0.** The blanket Deny is unchanged — Tier 0 agents cannot write even
   if the app-layer gate has a bug. This is the strongest guarantee: IAM says no.
2. **Least privilege for write agents.** A Tier 1 agent (documentation-rag) gets an IRSA role that
   can `git push` to specific repos and `create MR` — nothing else. A Tier 2 agent gets API tokens
   scoped to incident.io + Grafana silence endpoint — nothing else.
3. **Audit trail at the cloud layer.** CloudTrail / K8s audit log shows which SA performed which
   action, attributable to a specific agent.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| N ServiceAccounts + N IRSA roles to manage | N is small (2–4 write agents in the near term); Terraform modules make this mechanical |
| Agents must run as separate pods (or use projected SA tokens) for per-agent SA | Acceptable — write agents are likely separate deployments anyway for blast-radius isolation |

**When this decision would be wrong**:
- If write agents proliferate (>10) and SA management becomes a burden — then a credential-broker pattern (one SA, dynamic token scoping) might be needed.

---

### Decision 3: Grafana silence path — accepted single-layer (allowlist) with compensating controls

**Choice**: acknowledge that the grafana-mcp write surface (Grafana silences) is behind a SINGLE
enforcement layer (the tool allowlist) because the Grafana SA token is already write-capable and a
Viewer token was explicitly declined (2026-07-23). Accept this residual risk with mandatory
compensating controls rather than adding a second layer.

**Justification (ordered by strength)**:
1. **Operational decision already made.** The Viewer token was declined because Grafana's
   permission model does not allow Viewer + silence-create (silences require Editor); a separate
   token would need a dedicated Grafana service account with exactly `alerting:silence:create` and
   nothing else — Grafana OSS does not support this granularity until v11.2+ RBAC (and the deployed
   version may not have it).
2. **Compensating controls bound the blast radius.** (a) The capability gate enforces `write_scope`
   = only `grafana:create-silence` and `grafana:delete-silence` — no other Grafana write tool is
   exposed. (b) Mandatory TTL (max 4h, configurable, never permanent) is enforced in the gate before
   the tool call reaches the MCP. (c) Every silence emits an audit event + metric. (d) A VMAlert
   rule fires if any silence duration exceeds the TTL cap or if silence count exceeds a threshold.
3. **Adding a second layer (dedicated Grafana SA) is deferred, not refused.** When Grafana RBAC
   supports fine-grained silence-only permissions, or when the deployed version is upgraded, a
   second layer SHOULD be added. This is tracked as a follow-up.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| A bug in the allowlist code could expose additional Grafana write tools | Existing fail-closed allowlist logic (spec 37) + CI test asserting the exact tool list mitigates |
| Single-layer means a single bypass = write access | Blast radius bounded by TTL cap + audit + alerting; worst case = 4h silence, not data loss |

**When this decision would be wrong**:
- If the Grafana token grants destructive capabilities beyond silences (dashboard delete, datasource modify) AND the allowlist is bypassed — then the blast radius is larger than acknowledged. Mitigated by spec 37's allowlist CI test and guardrail on tool args.
- If compliance requires dual-layer enforcement for ALL write paths without exception.

---

### Decision 4: HITL for Tier 3 — Slack approval with fail-closed timeout

**Choice**: Tier 3 actions require explicit approval in a named Slack channel. The request includes
the exact action, target, blast radius, and rollback command. On timeout (default 5 min), the action
is NOT executed (fail-closed). Only members of a declared approvers group can approve.

**Justification**:
1. **ADR-001 mandate.** "Execution may not ship without human-in-the-loop approval for any
   mutating action." Tier 3 = mutating action on live infrastructure.
2. **Fail-closed on timeout is non-negotiable.** An unanswered approval request means nobody
   validated the action. Executing anyway defeats the purpose of HITL entirely. The agent's worst
   case is "I tried but timed out" — not "I acted without approval."
3. **Slack is where on-call lives.** The approval channel is the same one where incident
   coordination happens — the approver has context. Alternative channels (email, PagerDuty) add
   latency without adding safety.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Latency: 0–300s wait for approval | Tier 3 actions are inherently non-urgent enough to wait 5 min (if truly urgent, a human is already acting manually) |
| Slack availability dependency | If Slack is down, Tier 3 actions cannot proceed — acceptable (fail-closed) |
| Approval fatigue if too many requests | Mitigated by Tier 3 being rare (only infrastructure mutations) |

**When this decision would be wrong**:
- If Tier 3 actions need sub-second response (e.g. auto-remediation during incident) — then HITL
  as designed is too slow and a pre-approved runbook pattern (approve the playbook, not each
  invocation) would be needed. This is explicitly out of scope for the initial model.

---

### Decision 5: Silences reduce observability — mandatory TTL + audit as first-class risk control

**Choice**: Grafana silences created by any agent are capped at a maximum TTL (default 4h,
configurable, never permanent). Every silence creation emits a structured audit event and
increments a metric. A VMAlert rule fires on threshold violation.

**Justification**:
1. **Blinding the operator is a denial-of-observability attack vector.** A permanent silence on
   a critical alert means that when the real failure happens, nobody is notified. This is not
   theoretical — it is the exact mechanism by which past real incidents went undetected for hours.
2. **Reversibility requires bounded duration.** A 4h silence self-heals (expires); a permanent
   silence requires someone to remember it exists and delete it manually. The former is reversible
   by construction; the latter accumulates as tech debt.
3. **Audit + alerting closes the feedback loop.** The SRE team sees "agent X silenced alert Y
   for 2h because of incident Z" in the audit trail, and the metric ensures dashboard visibility.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| 4h max may be too short for long-running maintenance | The agent can re-create the silence (with a new audit event) — intentional friction, not a bug |
| Audit event volume | Write agents are rare; silence creation is rarer still — volume is negligible |

**When this decision would be wrong**:
- If a legitimate use case requires silences >4h autonomously (e.g. a multi-day migration) — then
  HITL approval for extended silences (not the default path) would be needed.

---

## Invariants

- **Tier 0 agents MUST NOT gain write capability through any code change to this spec.** The
  blanket Deny, adapter code without write paths, and read-only allowlists remain untouched.
- **A write action without an audit event is a bug.** Every Tier 1/2/3 action MUST emit.
- **Tier 3 action without HITL approval is a security incident.** The gate MUST block.
- **Silences are never permanent.** The gate MUST enforce `duration <= max_silence_ttl`.
- **`capabilities` and `routing_keywords` in `agent.yaml` are never renamed or nested.**

## Dependencies

| Service | Purpose |
|---------|---------|
| Slack API | HITL approval channel (Tier 3) |
| GitLab API | MR creation (Tier 1) |
| incident.io API | Field updates (Tier 2) |
| Grafana API | Silence create/delete (Tier 2) |
| VictoriaMetrics / VMAlert | Audit metrics + alerting on TTL violations |
| Kubernetes | Per-agent ServiceAccount + RBAC |
| AWS IAM (IRSA) | Per-agent scoped roles |
