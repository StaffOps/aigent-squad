# Tasks: Per-Agent Capability Tiers

Legend: `[ ]` pending · `[~]` partial · `[x]` done. Each code task follows the pipeline
`dev` (implements) → `dev` (tests, independent) → `code-review` → coverage gate ≥90%.

> **Status 2026-08-11 (not-started):** spec authored. No code yet.

---

## Phase status table

| Phase | Scope | Status | Promotion trigger to next phase |
|-------|-------|--------|-------------------------------|
| **0** | Spec + round-table | `not-started` | Round-table (security + sre + dev) refutations incorporated; no unresolved blocking objection |
| **1** | Schema + capability gate (Tier 0 only) | `not-started` | All existing agents validated as Tier 0; CI gate passes; zero behavioral regression in eval suite |
| **2** | Per-agent ServiceAccount + IRSA | `not-started` | Tier 0 blanket Deny confirmed unchanged in prod; Tier 1/2 SA created + RBAC-audited |
| **3** | Tier 1 + 2 enforcement (gate + audit) | `not-started` | Capability gate blocks undeclared writes; audit events emit; silence TTL enforced; homologated with documentation-rag + incident-management (spec 44) |
| **4** | Tier 3 HITL gate | `not-started` | Slack approval loop functional; timeout = deny; E2E test with mock approval channel |
| **5** | Observability + docs | `not-started` | Metrics + VMAlert rules + dashboard live; `READ_ONLY_POLICY.md`, `SECURITY.md`, `MCP_INTEGRATION.md`, `README.md`, `AGENTS.md`, `docs/METRICS.md` updated |

---

## Phase 0 — Spec & validation (spec-first)

- [ ] T0.1: Write requirements/design/tasks (this spec) — DONE on merge of this dir.
- [x] T0.2: **Round-table** — EXECUTED 2026-08-12 with four independent reviewers
      (`code-review`, `security`, `sre`, `observability`), each instructed to refute rather than
      confirm. Verdicts: COMMIT AFTER FIXES / COMMIT WITH RECORDED BLOCKERS / DO NOT COMMIT AS-IS /
      DO NOT COMMIT AS-IS. Findings recorded in `requirements.md` → "Open issues — harness
      2026-08-12". Mechanical corrections already applied to this spec.
- [ ] T0.3: **BLOCKS ALL OF PHASE 1.** Resolve the 7 open design decisions H-1…H-7 in
      `requirements.md` and fold the resolutions into `design.md`. H-1 (how `agent_id` reaches the
      capability gate) and H-3 (transitive attenuation) are prerequisites for T1.4/T1.5 — the gate
      cannot be built without them.

---

## Phase 1 — Schema + capability gate (Tier 0 regression-proof)

Goal: introduce the tier model in code with Tier 0 as the ONLY active tier. All existing agents
gain `capability_tier: 0` (explicit or default). The capability gate exists but only enforces
"Tier 0 = deny all writes" — identical to today's behavior, just via a new code path.

- [ ] T1.1: `agent_config.py` — add `capability_tier: int = Field(default=0, ge=0, le=3)`,
      `write_scope: list[str] = []`, and `refuses: list[str] = []` (declarative, informational —
      actions the agent must refuse even when asked; owned by THIS spec, consumed by spec 44).
      Validation: tier 0 ↔ empty scope; tier 3 ↔ hitl present; **and `capability_tier == 0` ⟺
      `read_only is True`** (the field already exists on `AgentConfig` and defaults to `True` —
      it is not replaced, it is constrained, so the two can never disagree silently).
- [ ] T1.2: `agent_config.py` — add `hitl: Optional[HitlConfig] = None` model
      (`channel: str`, `timeout_seconds: int = 300`, `approvers: list[str]`).
- [ ] T1.3: Add `capability_tier: 0` explicitly to all 6 existing `agent.yaml` files
      (documentation, not behavior change — makes the invariant visible).
- [ ] T1.4: `capability_gate.py` (new) — `CapabilityGate.authorize(agent_id, tool_name, action_type)`
      → raises `CapabilityDeniedError` if action not in declared scope. For Tier 0, ALL write
      actions are denied (same as today but via gate).
- [ ] T1.5: Wire `CapabilityGate.authorize()` into `adapters.py` `call_tool()` — the gate runs
      BEFORE every tool execution, regardless of transport.
- [ ] T1.6: Tests (independent author, ≥90%):
  - Tier 0 agent attempting a write tool → `CapabilityDeniedError`.
  - Tier 0 agent calling a read tool → pass-through.
  - Unknown tier (missing config) → defaults to 0 (deny writes).
  - `write_scope` non-empty on Tier 0 → config validation error.
  - `hitl` absent on Tier 3 → config validation error.
  - `capability_tier: 0` + `read_only: false` → config validation error.
  - `capability_tier: 2` + `read_only: true` → config validation error.
  - All 6 existing `agent.yaml` files load with `capability_tier == 0` and `read_only is True`.
- [ ] T1.7: CI gate: `make capability-validate` checks all `agent.yaml` pass schema.
- [ ] T1.8: Eval regression: run existing eval suite → all 6 agents produce same results.

---

## Phase 2 — Per-agent ServiceAccount + IRSA

Goal: write-capable agents get their own K8s identity. Tier 0 agents keep the blanket Deny SA.

- [ ] T2.1: Terraform module — per-agent IRSA role factory. Input: agent name + allowed AWS actions +
      resource ARNs. Output: role ARN + trust policy scoped to the agent's SA.
- [ ] T2.2: Helm chart — parameterize `serviceAccountName` per agent deployment (today: shared SA).
      Tier 0 agents → existing SA with blanket Deny. Tier 1+ → dedicated SA annotated with the
      scoped IRSA role.
- [ ] T2.3: Kyverno policy: `require-readonly-sa` — validates that any pod using the Tier-0 SA
      has NO write verbs in its RBAC binding (prevents SA reuse by a future misconfigured pod).
- [ ] T2.4: RBAC audit (extend `mcp_rbac_audit.py`): accept an `--agent` flag and validate the
      agent's SA matches its declared `write_scope` (no extra permissions).
- [ ] T2.5: Tests: deploy to dev cluster; `kubectl auth can-i --list` for Tier 0 SA = 0 write;
      Tier 1 SA = only `gitlab:push`; Tier 2 SA = only incident.io + Grafana.
- [ ] T2.6: Document the SA topology in `docs/SECURITY.md`.

---

## Phase 3 — Tier 1 + 2 enforcement (gate + audit + silence TTL)

Goal: write-capable agents can actually perform their declared writes, but nothing beyond.

- [ ] T3.1: `capability_gate.py` — Tier 1 enforcement: validate tool call ∈ `write_scope`;
      log structured audit event; pass to adapter.
- [ ] T3.2: `capability_gate.py` — Tier 2 enforcement: same as Tier 1 + silence-specific logic:
  - Extract `duration` from Grafana silence tool args.
  - Enforce `duration <= MAX_SILENCE_TTL` (env-configurable, default 4h = 14400s).
  - Reject if duration is 0 or absent (never-permanent).
  - Emit `silence_created` audit event as a **structured log record** with
    `{agent, target_alertname, duration_seconds, incident_id, matcher, silence_id, trace_id}` —
    these are high-cardinality and belong on the log, not on the counter. The counter counterpart is
    `aigent_silence_created_total{agent, duration_bucket, outcome}` (T5.1).
- [ ] T3.3: `audit.py` (new or extend) — structured audit event emitter. Fields: `timestamp`,
      `agent_id`, `capability_tier`, `action`, `target`, `write_scope_entry`, `outcome`
      (allowed/denied/timeout), `correlation_id`, `user_id` (if HITL).
- [ ] T3.4: Audit events → structured log (OTel) + Redis stream (for spec 21 KB consumption).
- [ ] T3.5: Grafana allowlist update: add `create_silence` and `delete_silence` to a NEW
      `grafana-mcp` allowlist entry that is ONLY exposed to the `incident-management` agent's
      datasource — NOT to the `observability` agent (which keeps read-only).
- [ ] T3.6: Tests (independent author, ≥90%):
  - Tier 1 agent calls declared write scope tool → allowed + audit emitted.
  - Tier 1 agent calls undeclared tool → denied.
  - Tier 2 agent creates silence with duration ≤ TTL → allowed.
  - Tier 2 agent creates silence with duration > TTL → denied.
  - Tier 2 agent creates silence with duration = 0 (permanent) → denied.
  - Audit event structure validated (all required fields present).
  - `observability` agent (Tier 0) still cannot invoke `create_silence` even though
    it shares the `grafana-mcp` server.
- [ ] T3.7: Homologate with spec 44 agents (documentation-rag + incident-management) in dev.

---

## Phase 4 — Tier 3 HITL gate

Goal: infrastructure-mutating actions require Slack approval before execution.

- [ ] T4.1: `hitl_gate.py` (new) — `request_approval(action, target, blast_radius, rollback_cmd)`
      → posts to Slack channel; returns `Approved | Denied | Timeout`.
- [ ] T4.2: Slack message format:
  ```
  🔴 HITL Approval Required
  Agent: cluster-ops
  Action: kubernetes:restart-pod
  Target: pod/dpm-people-api-7f8b9c-xyz (ns: dpm, cluster: prd-nv)
  Blast radius: 1 pod, in-flight requests dropped
  Rollback: kubectl rollout undo deployment/dpm-people-api -n dpm
  Timeout: 5m (action NOT executed if unanswered)
  [✅ Approve] [❌ Deny]
  ```
- [ ] T4.3: Approval callback (Slack interactive message) → validate approver ∈ `hitl.approvers`;
      reject if approver is not in the group.
- [ ] T4.4: Timeout handling: after `hitl.timeout_seconds`, emit audit event with
      `outcome: timeout` and return `Denied` to the agent. The agent receives a message:
      "Action not approved within timeout. No changes made."
- [ ] T4.5: Wire HITL gate into `capability_gate.py`: Tier 3 actions call `hitl_gate` after
      scope validation but before tool execution.
- [ ] T4.6: Tests (independent author, ≥90%):
  - Mock Slack: approval within timeout → action executes.
  - Mock Slack: denial → action blocked + audit.
  - Mock Slack: timeout → action blocked + audit.
  - Approver not in `hitl.approvers` → approval rejected.
  - Tier 3 agent with no `hitl` config → config validation error (caught at load).
- [ ] T4.7: E2E in dev: real Slack channel, real approval flow, real pod restart (non-prod pod).

---

## Phase 5 — Observability + documentation

Goal: operational visibility into the capability tier system.

- [ ] T5.1: Metrics (every label set below is bounded — high-cardinality identifiers go on the
      audit log record, never on a metric label; see `observability-principles`):
  - `aigent_capability_gate_decisions_total{tier, agent, action, outcome}` (allowed/denied/timeout)
  - `aigent_write_actions_total{tier, agent, action}` (successful writes only)
  - `aigent_silence_created_total{agent, duration_bucket, outcome}` — `duration_bucket` ∈
    {`le_1h`, `le_2h`, `le_4h`}. **Not** `duration_seconds` (continuous, 1–14400) and **not**
    `target_alert` (unbounded alertname): both were in the first draft and are cardinality bombs.
  - `aigent_active_silences{agent}` (UpDownCounter — incremented on create, decremented on
    delete/expiry) so a dashboard can answer "how many alerts are silenced BY AN AGENT right now";
    Grafana's own `alertmanager_silences` cannot be split by creator.
  - `aigent_hitl_approval_latency_seconds{agent, outcome}` (histogram)
  - `aigent_hitl_late_approval_total{agent}` — approval callbacks arriving after the timeout
    (see H-5; if this is ever non-zero the state machine is leaking).
- [ ] T5.2: VMAlert rules:
  - `AigentUnauthorizedWriteAttempt` — Tier 0 agent attempted a write (should never happen post-deploy).
  - `AigentSilenceTTLViolation` — silence created with duration > cap (should never happen post-gate).
  - `AigentHITLTimeoutRate` — >50% of HITL requests timing out (approval fatigue signal).
- [ ] T5.3: Grafana dashboard panel: capability gate decisions (tier × agent × outcome).
- [ ] T5.4: Update `docs/READ_ONLY_POLICY.md` — the "Enforcement (4 layers)" section gains a
      "Per-agent capability tier (spec 43)" subsection. Tier 0 section states "unchanged."
- [ ] T5.5: Update `docs/SECURITY.md` — new §S6 "Capability Tiers" documenting the gate +
      per-agent SA + HITL.
- [ ] T5.6: Update `docs/MCP_INTEGRATION.md` — the per-MCP read-only table gains a column
      "Exposed to agents" showing which tier/agents can invoke write tools.
- [ ] T5.7: Update `README.md` — it currently asserts the read-only property globally. It becomes
      a **lie** the moment a Tier 1+ agent ships; restate as "read-only by default (Tier 0), with
      declared per-agent exceptions".
- [ ] T5.8: Update `AGENTS.md` — the agent roster gains a tier column; the read-only statement gets
      the same treatment as `README.md`.
- [ ] T5.9: Update `docs/METRICS.md` — add a row per new metric with its exact bounded label set,
      and extend the Labels table with the new `agent_id` values and the `tier`/`outcome`/
      `duration_bucket` enums.
- [ ] T5.10: `CHANGES.md` entry.

---

## Deferred (not in this spec's scope — tracked for completeness)

| Item | Trigger to activate |
|------|---------------------|
| Tier 3 operational agents (cluster-ops) | Spec 44 Phase 2+ defines specific agents; tier model from this spec is prerequisite |
| Grafana dedicated SA with silence-only permissions | Grafana version supports fine-grained RBAC for silences (v11.2+ provisioned) |
| Pre-approved runbook pattern (Tier 3 without per-action HITL) | Explicit user decision to allow autonomous remediation within a pre-approved playbook |
| A2A / distributed topology integration | Spec 42 activates; the capability gate already works cross-transport by design |
