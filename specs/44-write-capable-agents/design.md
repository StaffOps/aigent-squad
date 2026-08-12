# Design: Write-Capable Agents (documentation-rag + incident-management)

## Architecture

Two new agents introduced on top of spec 43's tier model. The `documentation-rag` agent (Tier 1)
extends spec 21's distillation pipeline with a GitLab MR sink. The `incident-management` agent
(Tier 2) manages incident.io fields and Grafana silences bidirectionally. Both are subject to
the capability gate, per-agent ServiceAccount, and audit trail defined in spec 43.

```
                                spec 21 pipeline (unchanged)
                                ┌─────────────────────────────────────────────────────────┐
 RCA Investigation              │                                                         │
 (spec 18) ─────┐              │  Extractor → Enricher → Validator → KbStore (Postgres)  │
                │              │                              │                           │
                │              └──────────────────────────────┼───────────────────────────┘
                │                                             │
                │              ┌──────────────────────────────▼───────────────────────┐
                │              │       NEW: documentation-rag (Tier 1)                 │
                │              │                                                       │
                │              │  KbDelta (status=approved)                            │
                │              │       ↓                                               │
                │              │  Content-Safety Validator (anti-injection)             │
                │              │       ↓                                               │
                │              │  Provenance Enricher (investigation_id, hashes,       │
                │              │                       confidence, approver)            │
                │              │       ↓                                               │
                │              │  PII Redactor (spec 21, re-applied)                   │
                │              │       ↓                                               │
                │              │  GitLab MR Writer (branch: aigent/learning-*)         │
                │              │       ↓                                               │
                │              │  Audit emitter                                        │
                │              └───────────────────────────────────────────────────────┘
                │
                │              ┌───────────────────────────────────────────────────────┐
                │              │       incident-management (Tier 2)                     │
                │              │                                                       │
                └──────────────┤  On RCA complete (confidence ≥ high):                 │
                               │    → incident.io: update root_cause, severity, summary│
                               │                                                       │
                               │  On incident state change:                            │
                               │    → Grafana: create/delete silence (TTL ≤ 4h)        │
                               │    → Bidirectional sync (incident.io = SoT)           │
                               │                                                       │
                               │  Loop breaker: >5 silences / 10 min → HALT           │
                               │  Audit: every write action                            │
                               └───────────────────────────────────────────────────────┘
```

---

## Agent tier classification (per spec 43)

| Agent | Tier | Justification | Boundary between this and the next tier |
|-------|------|---------------|----------------------------------------|
| `documentation-rag` | **1** (Reviewable artifact) | Writes to GitLab as an MR — a human must merge before content reaches protected branches. No direct live-system effect. | If it could push to protected branches or write to a live store without review, it would be Tier 2. |
| `incident-management` | **2** (Narrow reversible write) | Writes to incident.io (field update) and Grafana (silence create/delete). Both are reversible (field can be re-set, silence can be deleted). Scoped to two specific APIs. | If it could restart pods, delete workloads, or make irreversible changes, it would be Tier 3. |

---

## Components

| Component | Location | Responsibility | New/Modified |
|-----------|----------|----------------|--------------|
| `documentation_rag_agent.py` | `src/agents/documentation_rag/` | Receives approved KbDeltas, validates content safety, writes GitLab MRs | **New** |
| `content_safety.py` | `src/core/kb/content_safety.py` | Scans candidate text for injection patterns, executable code, suspicious URLs | **New** |
| `gitlab_writer.py` | `src/agents/documentation_rag/gitlab_writer.py` | Creates branch, commits markdown, opens MR with provenance metadata | **New** |
| `revocation.py` | `src/core/kb/revocation.py` | Marks KB entry as revoked, closes/reverts GitLab MR | **New** |
| `incident_mgmt_agent.py` | `src/agents/incident_management/` | Fills incident.io fields, manages Grafana silences | **New** |
| `silence_manager.py` | `src/agents/incident_management/silence_manager.py` | TTL enforcement, loop breaker, bidirectional sync, audit | **New** |
| `incident_io_client.py` | `src/agents/incident_management/incident_io_client.py` | Scoped API client for incident.io field updates | **New** |
| `distillation.py` | `src/supervisor/distillation.py` | Gains a hook to invoke `documentation-rag` after approval | **Modified** |
| `capability_gate.py` | `src/core/capability_gate.py` | Validates writes per spec 43; already exists from spec 43 Phase 3 | **Unchanged** (consumed) |
| `agent.yaml` (2 new) | `agents/documentation-rag/`, `agents/incident-management/` | Declares tier, write_scope, datasources | **New** |

---

## `agent.yaml` additions (ADDITIVE ONLY)

```yaml
# agents/documentation-rag/agent.yaml
name: documentation-rag
description: "Persists validated platform learnings to GitLab as reviewable MRs."
domain: knowledge
capabilities: [kb_write, mr_creation]
routing_keywords: [knowledge, learning, kb, documentation, persist]
capability_tier: 1
write_scope:
  - "gitlab:push-branch"
  - "gitlab:create-mr"
datasources:
  - type: internal
    name: kb-pipeline
    source: spec-21-distillation
read_only: false
model:
  tier: standard
  temperature: 0.0
port: 8010
refuses:
  - "Write to any repository other than devops-platform-knowledge"
  - "Push to protected branches (main, development, production)"
  - "Write raw infrastructure data without validation"
  - "Persist content flagged by content-safety validator"
  - "Operate when budget guard is exhausted"

# agents/incident-management/agent.yaml
name: incident-management
description: "Manages incident lifecycle: fills incident.io fields, creates/deletes Grafana silences."
domain: incident
capabilities: [incident_field_update, silence_management]
routing_keywords: [incident, silence, mute, acknowledge, incident.io, grafana silence]
capability_tier: 2
write_scope:
  - "incident-io:update-field"
  - "incident-io:create-action"
  - "grafana:create-silence"
  - "grafana:delete-silence"
datasources:
  - type: mcp
    name: grafana-mcp
    url: "${GRAFANA_MCP_URL}"
    transport: streamable-http
    tools:
      - create_silence       # Tier 2 write — gated by capability gate + TTL enforcement
      - delete_silence       # Tier 2 write
      - get_silence          # read
      - list_silences        # read
  - type: http
    name: incident-io
    url: "${INCIDENT_IO_API_URL}"
read_only: false
model:
  tier: standard
  temperature: 0.0
port: 8011
refuses:
  - "Create permanent silences (duration 0 or absent)"
  - "Silence alerts not linked to an active incident"
  - "Modify Grafana dashboards, datasources, or alert rules"
  - "Create new incidents (only update existing)"
  - "Delete incidents"
  - "Exceed 5 silence operations in 10 minutes"
```

---

## Rationale (decisions)

### Decision 1: documentation-rag EXTENDS spec 21, does not build a parallel pipeline

**Choice**: the `documentation-rag` agent is a new **sink** at the end of spec 21's existing
`Extractor → Validator → KbStore` pipeline. It consumes only `KbItem`s that the pipeline already
persisted with `status == KbStatus.ACTIVE` — it does not
run its own extraction, its own validation, or its own confidence scoring.

**Justification (ordered by strength)**:
1. **Avoids silent divergence.** Two parallel pipelines (one to Postgres, one to GitLab) would
   inevitably drift in validation thresholds, redaction rules, and approval semantics. A single
   pipeline with two sinks guarantees consistency. B-19 (feedback → KbDelta routing) already
   assumes one pipeline — adding a parallel one would force B-19 to route to both, doubling
   integration points.
2. **Reuses the existing validator as the security boundary.** The persistent injection defense
   (this spec's central problem) needs a validation checkpoint. Spec 21's `Validator` already
   exists with confidence thresholds and `decision` type always-manual. Extending it with
   content-safety checks is cheaper and safer than building a new validator from scratch.
3. **Budget guard covers both sinks automatically.** Spec 21's `BudgetGuard` (monthly cap on
   distillation cost) applies once, before either sink writes — no coordination needed.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| GitLab writes are blocked if spec 21 pipeline is down | Acceptable — if the validator is down, nothing should be persisted (fail-closed) |
| Coupling: changes to spec 21's validator affect GitLab writes | Intentional — they SHOULD affect both sinks identically |
| Can't write to GitLab without a preceding investigation | By design — knowledge must be traceable to evidence |

**When this decision would be wrong (signals to reopen)**:
- If a use case emerges where knowledge originates OUTSIDE the investigation pipeline (e.g. operator manually submits a learning that wasn't triggered by an incident). Then a separate ingestion path with its own validator would be needed. But that path should still share the `ContentSafetyValidator` and `PIIRedactor` — only the source changes.

**Alternatives discarded**:
- *Parallel pipeline with own validator* — duplication, divergence risk, doubles integration surface for B-19.
- *Direct GitLab write from the supervisor without an agent* — loses the per-agent enforcement model from spec 43 (no SA isolation, no audit at the gate layer, no `write_scope` boundary).

---

### Decision 2: Persistent injection defense — layered content validation (not just regex)

**Choice**: three validation layers before content reaches GitLab, processed in order:

1. **Spec 21 Validator** (existing): confidence thresholds, type-based routing, `decision` type always manual.
2. **Content-Safety Validator** (new): pattern-based detection of instruction injection, executable code, and suspicious URLs.
3. **Provenance binding**: every KB entry is cryptographically tied to its source evidence (SHA-256 of the investigation evidence set). An entry without valid provenance is rejected.

A candidate learning is rejected if ANY layer fails. Rejection is an audit event.

**Justification (ordered by strength)**:
1. **The threat model is specific: a crafted log line → extracted as "knowledge" → persisted →
   later read by another agent as trusted context.** This is prompt injection that crosses the
   temporal boundary (momentary → permanent). The defense must operate at the content level
   (what does the text say?) AND at the provenance level (where did this come from?). Neither
   alone is sufficient: content-safety misses novel patterns; provenance alone does not prevent a
   legitimate-source-but-malicious-content entry.
2. **Layered defense means no single bypass compromises the system.** An attacker must:
   (a) get past the confidence threshold (the extracted fact must look plausible to the LLM),
   (b) evade the content-safety scanner (no instruction patterns, no code, no suspicious URLs),
   (c) forge provenance (bind to a real investigation). Each layer is independent.
3. **Post-hoc detection (revocation) is the final layer.** Defense-in-depth acknowledges that
   prevention is not 100%. The revocation path (mark as `revoked`, close MR, audit event) means
   that a poisoned entry discovered later can be removed with full traceability.

**Content-Safety Validator specifics**:

| Check | What it detects | Action on match |
|-------|----------------|-----------------|
| Instruction injection | `ignore previous`, `system:`, `<\|im_start\|>`, `[INST]`, `Human:`, `Assistant:` | REJECT + audit |
| Executable code | Shell commands (`rm -rf`, `curl \| bash`), SQL injection patterns, script tags | REJECT + audit |
| URL allowlist | Any URL not matching internal domains (`*.bigdatacorp.com.br`, `*.bdc.app.br`, GitLab internal) | REJECT + audit |
| Length anomaly | Content >5000 chars (learnings should be concise) | FLAG for manual review |
| Repetition anomaly | >3 KB entries from the same source investigation within 1 hour | FLAG for manual review |

**What NEVER becomes knowledge (deny-list)**:
- Raw secrets (even if `<redacted:type>` markers are expected — the content-safety layer rejects if redaction markers are absent for known secret patterns)
- Events from `kube-system` or `istio-system` namespaces without secondary evidence from another signal
- Content where confidence < 0.60 (below the lowest auto-approve threshold)
- Content explicitly marked as ephemeral by the investigation (debug notes, intermediate hypotheses)

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| False positives: legitimate learnings containing URLs or code snippets get rejected | Rejected items go to `pending_review` (not discarded) — human can approve |
| Regex-based injection detection is bypassable by sophisticated encoding | Defense-in-depth: provenance binding + post-hoc detection compensate; content-safety is one layer, not the only one |
| Provenance hashes are only as trustworthy as the evidence store | Evidence store is internal Postgres (spec 18), not attacker-controlled |

**When this decision would be wrong**:
- If the investigation evidence itself is stored in an attacker-writable location (S3 bucket with public write) — then provenance binding is meaningless. Currently, evidence lives in the internal Postgres + Redis scratchpad, both behind K8s network policy.
- If the content-safety patterns generate >30% false positive rate in production — then the check is too aggressive and should be relaxed (with human review compensating).

---

### Decision 3: incident.io is source of truth for silence intent; Grafana is enforcement mechanism

**Choice**: when bidirectional sync detects a conflict between incident.io's silence state and
Grafana's actual silences, incident.io wins. Grafana silences are treated as the *enforcement*
of incident.io's *intent*.

Resolution rules:
- **incident.io says "silenced" + Grafana has no silence** → CREATE silence in Grafana (enforcement drifted from intent).
- **Grafana has silence + incident.io says "active"** → DELETE silence in Grafana (intent changed, enforcement must follow).
- **Both agree** → no action.

**Justification (ordered by strength)**:
1. **Operator intent lives in incident.io.** When an operator acknowledges an incident and marks
   alerts as silenced, that decision lives in the incident record. Grafana silences are the
   mechanical enforcement of that decision. If a silence expires in Grafana but the incident is
   still in "mitigating" state with "alerts silenced" noted, the intent hasn't changed — the
   mechanism failed.
2. **Single source of truth prevents oscillation.** Without a defined winner, a conflict could
   trigger: Grafana silence expires → sync creates in incident.io → incident.io says "active" →
   sync deletes in Grafana → ... (loop). By declaring incident.io wins, the resolution is always
   unidirectional: read intent from incident.io → enforce in Grafana.
3. **Audit trail is in incident.io.** Incident records persist with full history; Grafana silences
   are ephemeral (expire and disappear). The durable record should be the authority.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| If incident.io is wrong (operator error), Grafana enforces the wrong state | Bounded by TTL (max 4h) — error self-heals on expiry. Manual Grafana override still possible and visible in audit. |
| Sync latency: Grafana could be out of sync for the polling interval | Polling interval ≤ 30s; bounded inconsistency window |
| Grafana manual overrides are overwritten by sync | By design — if you want to unsilence, update incident.io (the SoT) |

**When this decision would be wrong**:
- If incident.io's silence concept is removed or its API changes semantics — then the SoT designation must move.
- If operators need to manage silences ONLY via Grafana without touching incident.io — then incident.io cannot be SoT. But this contradicts the operational model where incidents are managed in incident.io.

---

### Decision 4: Runaway silencing loop breaker — hard cap with manual reset

**Choice**: the `incident-management` agent tracks a rolling window counter of silence operations
(create + delete). If >5 operations occur within 10 minutes, the agent enters a **halted state**:
no further silence operations are executed, a `runaway_silencing_loop` VMAlert fires, and a manual
reset (API call or config toggle) is required to resume.

**Justification (ordered by strength)**:
1. **Silencing is a write that REDUCES observability.** A bug that creates silences in a loop
   doesn't merely produce noise — it blinds the operator to real alerts. The failure mode is
   INVISIBLE (you don't notice what you're not alerted about). Therefore the safeguard must be
   aggressive and fail-closed (halt), not fail-open (warn and continue).
2. **Normal operation never hits this cap.** An incident has 1–3 related alerts silenced once.
   5 silence operations in 10 minutes means either a bug, a misconfigured sync, or an attack.
   The threshold is set at 2× the realistic maximum for a multi-alert incident.
3. **Manual reset forces human awareness.** Auto-recovery would defeat the purpose — the point is
   that a human sees the alert, investigates why 5+ silence operations happened, and consciously
   re-enables the agent after fixing the cause.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| A legitimate multi-incident burst could hit the cap | Extremely rare (5 simultaneous incidents each needing different silences within 10 min). If hit, human resets — acceptable latency for a safety mechanism. |
| Manual reset adds operational friction | By design — friction is the safety mechanism here |

**When this decision would be wrong**:
- If the system handles >50 incidents/day routinely, each with 3+ alerts — then the cap needs to scale with volume. But current usage (<10 incidents/week) makes 5/10min extremely conservative.

---

### Decision 5: Per-agent ServiceAccount scope — minimal blast radius per agent

**Choice**: each write agent gets its own ServiceAccount + IRSA role scoped to exactly its declared `write_scope`. Overlap between agents is zero.

| Agent | SA name | IRSA role scope | What it CAN do | What it CANNOT do |
|-------|---------|-----------------|----------------|-------------------|
| `documentation-rag` | `sa-documentation-rag` | GitLab API: push to `devops-platform-knowledge` repo, create MR. No other repos. | Push to `aigent/learning-*` branches, create MR | Push to protected branches, access other repos, any K8s write, any AWS write |
| `incident-management` | `sa-incident-management` | incident.io API: `PATCH /incidents/{id}`, `POST /actions`. Grafana API: `POST /api/alertmanager/grafana/api/v2/silences`, `DELETE /api/alertmanager/grafana/api/v2/silence/{id}` | Update incident fields, create/delete silences | Create/delete incidents, modify dashboards, modify alert rules, any K8s write, any AWS write |

**Justification**:
1. **Zero overlap means a compromised agent A cannot perform agent B's actions.** If `documentation-rag`'s SA token is leaked, the attacker can push to one repo — they cannot create silences or update incidents.
2. **Composes with distributed topology (spec 42).** When agents run in separate pods/clusters, each pod's projected SA token grants exactly its declared scope — network isolation + credential isolation.
3. **Audit attribution is unambiguous.** CloudTrail / K8s audit log + incident.io audit log show which SA performed which action.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| N ServiceAccounts to manage | N=2 (this spec); Terraform module from spec 43 Phase 2 makes this mechanical |
| Token rotation must happen per-agent | Standard IRSA token rotation (12h projected tokens) handles this |

---

## Invariants

- **documentation-rag ONLY writes to GitLab via MR** — never directly to a protected branch.
- **documentation-rag ONLY consumes `KbItem`s already persisted with `status == active`** — never raw investigation data, never a `pending_review` item, never a `KbDelta` (the delta has no status field; the status is decided by `decide_status()` and lives on the persisted item).
- **Spec 21's Validator is the gatekeeper for ALL KB writes** — GitLab sink does not bypass it.
- **incident-management NEVER creates permanent silences** — duration=0 or absent is rejected at the capability gate.
- **incident.io is the source of truth for silence intent** — Grafana is enforcement only.
- **Runaway loop breaker is fail-closed** — halts on threshold, requires manual reset.
- **A write action without an audit event is a bug** (inherited from spec 43).
- **Tier 0 agents are unaffected** — their enforcement posture does not change.

---

## Gap identified in spec 43

**Gap**: spec 43 does not define a `refuses` field in the `agent.yaml` schema. This spec introduces
`refuses: list[str]` as a declarative list of actions the agent MUST refuse even when asked. This is
**documentation-level** (not a new enforcement mechanism) — the actual enforcement is the capability
gate + write_scope (you can't do what's not in your scope). The `refuses` field makes the boundary
visible to humans and to the classifier (routing should not send silence requests to documentation-rag).

Recommendation: spec 43 should add `refuses: list[str] = []` (optional, informational) to the
agent.yaml schema in Phase 1. This is a non-breaking addition.

---

## Dependencies

| Service | Purpose | Agent |
|---------|---------|-------|
| GitLab API | Push branches, create MRs | documentation-rag |
| Spec 21 pipeline (KbStore, Validator, BudgetGuard) | Source of validated learnings | documentation-rag |
| incident.io API | Field updates, action creation | incident-management |
| Grafana API (existing MCP) | Silence create/delete | incident-management |
| Postgres + pgvector (spec 21) | KB storage, provenance records | documentation-rag |
| Redis | Loop breaker counter (rolling window) | incident-management |
| VictoriaMetrics / VMAlert | Metrics + alerting | both |
| Spec 43 capability gate | Enforcement of tier + write_scope | both |
| Spec 43 per-agent SA + IRSA | Infrastructure enforcement | both |
