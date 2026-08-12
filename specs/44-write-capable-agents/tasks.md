# Tasks: Write-Capable Agents (documentation-rag + incident-management)

Legend: `[ ]` pending · `[~]` partial · `[x]` done. Each code task follows the pipeline
`dev` (implements) → `dev` (tests, independent) → `code-review` → coverage gate ≥90%.

> **Status 2026-08-12 (not-started):** spec authored. No code yet.
> **Prerequisite**: spec 43 Phases 1–3 must be complete (capability gate + per-agent SA + Tier 1/2 enforcement).

---

## Phase status table

| Phase | Scope | Status | Promotion trigger to next phase |
|-------|-------|--------|-------------------------------|
| **0** | Spec + round-table | `not-started` | Round-table (security + sre + dev) refutations incorporated; no unresolved blocking objection; spec 43 Phase 3 confirmed complete |
| **1** | Content-safety validator + documentation-rag agent | `not-started` | Content-safety rejects known injection patterns (eval suite); documentation-rag creates MR in dev GitLab; provenance metadata present in MR; PII redaction confirmed |
| **2** | incident-management agent + silence manager | `not-started` | incident.io field updates work; Grafana silences created with TTL enforced; loop breaker halts at threshold; bidirectional sync resolves conflicts per design |
| **3** | Bidirectional sync + conflict resolution | `not-started` | incident.io ↔ Grafana sync operates for 48h in dev without manual intervention; conflict cases tested E2E; audit trail complete |
| **4** | Observability + docs + revocation | `not-started` | All metrics emitting; VMAlert rules firing on test violations; revocation path tested E2E; docs updated |

---

## Phase 0 — Spec & validation

- [ ] T0.1: Write requirements/design/tasks (this spec) — DONE on merge of this dir.
- [x] T0.2: **Round-table** — EXECUTED 2026-08-12 with four independent reviewers
      (`code-review`, `security`, `sre`, `observability`), each instructed to refute rather than
      confirm, and to verify every claim about existing behaviour against `src/`. Verdicts:
      COMMIT AFTER FIXES / COMMIT WITH RECORDED BLOCKERS / DO NOT COMMIT AS-IS / DO NOT COMMIT
      AS-IS. Answers to the original focus areas:
    - Content-safety validator is **NOT** sufficient — it is a denylist, bypassable by construction
      (see H-2).
    - incident.io-wins is under-specified: the outage case and the resolve case are both missing
      (see H-5).
    - Loop-breaker threshold was not the binding problem; the missing delete-on-resolve was.
    - `refuses` → **spec 43 owns it**, now registered in spec 43 T1.1.
      Full findings in `requirements.md` → "Open issues — harness 2026-08-12". Mechanical
      corrections (phantom `KbDelta.status`, unfalsifiable SHALL, `trace_id` propagation, doc-sync
      tasks) already applied.
- [ ] T0.3: **BLOCKS ALL OF PHASE 1.** Resolve the 9 open design decisions H-1…H-9 in
      `requirements.md` and fold the resolutions into `design.md`. H-1 (trigger mechanism) blocks
      T1.8; H-2 (anti-poisoning strategy) blocks T1.1–T1.6, since it may change what the validator
      is for.
- [ ] T0.4: Confirm spec 43 Phase 3 is complete (capability gate + Tier 1/2 enforcement live) AND
      that spec 43's own T0.3 resolved H-1/H-3 there — a Tier 1 agent built on an unresolved
      capability gate inherits the hole.

---

## Phase 1 — Content-safety validator + documentation-rag agent

Goal: the documentation-rag agent can receive approved KbDeltas from spec 21's pipeline and write
them to GitLab as MRs, with full anti-injection defense and provenance tracking.

### Content-safety validator

- [ ] T1.1: `src/core/kb/content_safety.py` — `ContentSafetyValidator.validate(text: str) → ValidationResult`.
  - Checks: instruction injection patterns, executable code, URL allowlist, length anomaly, repetition anomaly.
  - Returns: `passed | rejected(reason) | flagged(reason)`.
  - Rejection list: see design.md Decision 2 table.
- [ ] T1.2: `content_safety.py` — deny-list categories that NEVER become knowledge:
  - Content with raw secret patterns (not just redaction markers).
  - Events exclusively from `kube-system`/`istio-system` without secondary evidence.
  - Content with confidence < 0.60.
  - Content marked as ephemeral by the investigation.
- [ ] T1.3: Wire `ContentSafetyValidator` into spec 21's `distillation.py` — after `Validator` approves,
      before passing to any sink. Rejection → `status: rejected_content_safety`, audit event emitted.

### Provenance binding

- [ ] T1.4: `src/core/kb/provenance.py` — `ProvenanceRecord` model:
  - `investigation_id: str`
  - `evidence_hashes: list[str]` (SHA-256 of each evidence artifact used)
  - `confidence_score: float`
  - `approver: str` (auto or human identity)
  - `extraction_timestamp: datetime`
  - `pipeline_version: str`
- [ ] T1.5: Extend `kb_provenance` table (spec 21's schema) with `evidence_hashes jsonb` column.
- [ ] T1.6: `distillation.py` — after approval, compute evidence hashes and attach `ProvenanceRecord`.

### documentation-rag agent

- [ ] T1.7: `agents/documentation-rag/agent.yaml` — as specified in design.md.
- [ ] T1.8: `src/agents/documentation_rag/agent.py` — agent entrypoint:
  - **Trigger: BLOCKED on H-1 (see requirements.md).** There is no pub/sub in this repo and
    `distill_rca()` is fire-and-forget: it calls `decide_status(delta)`, builds a `KbItem`, awaits
    `kb_store.insert(item)` and returns. Nothing is emitted. The two candidates are (a) emit an
    event at the end of `distill_rca` after a successful insert, or (b) poll `KbStore` for
    `status='active'` with a published watermark — which requires a schema change (`published_at` /
    `mr_url` column) to avoid re-publishing the same item forever. **Do not start this task before
    T0.3 picks one**; the earlier "(internal pub/sub or polling)" phrasing hid an unmade decision.
  - Passes each through `ContentSafetyValidator` (defense-in-depth: even though distillation.py already called it, the agent re-validates — trust no upstream in distributed topology).
  - Calls `GitLabWriter`.
  - Emits audit event (counter + structured log — see T4.4).
- [ ] T1.9: `src/agents/documentation_rag/gitlab_writer.py`:
  - Creates branch `aigent/learning-{investigation_id}-{timestamp}`.
  - Commits markdown file with content + frontmatter (provenance metadata).
  - Creates MR targeting the repo's default branch.
  - MR description includes: provenance, link to investigation, confidence, approver, and the
    originating OTel `trace_id` rendered as a Grafana deeplink — so an auditor can pivot from "this
    MR exists" back to the investigation and its evidence queries. Free-text prose alone is not
    navigable (see H-9).
  - Uses GitLab API v4 (personal access token or project access token scoped to the single repo).
- [ ] T1.10: ServiceAccount + IRSA for `documentation-rag`:
  - Terraform: IRSA role with `sts:AssumeRoleWithWebIdentity` scoped to the agent's SA.
  - The role has NO AWS permissions (GitLab is not AWS-native); the SA is used for the capability
    gate's identity validation and Kyverno policy.
  - GitLab token stored as K8s Secret (ExternalSecret from AWS Secrets Manager), mounted to the
    agent's pod only.

### Tests (Phase 1)

- [ ] T1.11: Tests (independent author, ≥90%):
  - Content-safety: known injection patterns → rejected.
  - Content-safety: legitimate learning text → passed.
  - Content-safety: borderline cases (code snippet in a legitimate runbook) → flagged for review.
  - Content-safety: deny-list categories → rejected.
  - Provenance: evidence hashes computed correctly from investigation evidence set.
  - Provenance: entry without valid provenance → rejected.
  - GitLab writer: mock API — branch created, file committed, MR opened with correct metadata.
  - GitLab writer: attempt to push to protected branch → error (should never happen due to branch naming, but assert).
  - Budget guard: exhausted → no MR created, graceful skip.
  - E2E: investigation → extractor → validator → content-safety → documentation-rag → MR exists.

---

## Phase 2 — incident-management agent + silence manager

Goal: the incident-management agent can update incident.io fields and create/delete Grafana
silences with TTL enforcement, audit, and loop breaker.

### incident-management agent

- [ ] T2.1: `agents/incident-management/agent.yaml` — as specified in design.md.
- [ ] T2.2: `src/agents/incident_management/agent.py` — agent entrypoint:
  - Triggered by: RCA completion event (confidence ≥ high) with an associated incident.io incident.
  - Triggered by: incident.io state change webhook (for silence lifecycle).
  - Routes to appropriate handler (field update or silence management).
- [ ] T2.3: `src/agents/incident_management/incident_io_client.py`:
  - `update_incident(id, fields: dict)` — PATCH fields (root_cause, severity, summary).
  - `create_action(id, action_text)` — POST action item.
  - Scoped token (env var `INCIDENT_IO_API_TOKEN`) with minimum required permissions.
  - Retries with exponential backoff; structured error logging.

### Silence manager

- [ ] T2.4: `src/agents/incident_management/silence_manager.py`:
  - `create_silence(alert_name, duration_seconds, incident_id, comment)`:
    - Validates `duration_seconds > 0` AND `<= MAX_SILENCE_TTL` (env: default 14400s = 4h).
    - Validates `incident_id` references an active incident.
    - Checks loop breaker (Redis ZRANGEBYSCORE on rolling window).
    - Calls Grafana MCP `create_silence` tool (via capability gate).
    - Records silence in incident.io.
    - Emits audit event.
  - `delete_silence(silence_id, incident_id)`:
    - Calls Grafana MCP `delete_silence`.
    - Updates incident.io.
    - Emits audit event.
- [ ] T2.5: Loop breaker (Redis sorted set):
  - Key: `incident-mgmt:silence-ops` (sorted set, score = timestamp).
  - On each operation: ZADD with current timestamp, ZREMRANGEBYSCORE to prune entries older than 10min, ZCARD to check count.
  - If count > 5 → set `incident-mgmt:halted` flag, emit `runaway_silencing_loop` audit event + metric.
  - All subsequent silence operations return error until manual reset.
  - Manual reset: `DELETE incident-mgmt:halted` key (via admin API endpoint or kubectl).
- [ ] T2.6: ServiceAccount + IRSA for `incident-management`:
  - Terraform: IRSA role (identity only, no AWS permissions — incident.io/Grafana are not AWS-native).
  - incident.io token: K8s Secret (ExternalSecret), mounted to agent's pod only.
  - Grafana token: reuses existing grafana-mcp token (per spec 43 Decision 3 — acknowledged single-layer). The capability gate is the enforcement boundary.

### Tests (Phase 2)

- [ ] T2.7: Tests (independent author, ≥90%):
  - incident.io client: field update with mock API → success + fields set.
  - incident.io client: update non-existent incident → error handled gracefully.
  - Silence manager: duration within TTL → silence created.
  - Silence manager: duration > TTL → rejected at gate.
  - Silence manager: duration = 0 → rejected.
  - Silence manager: no active incident for the alert → refused.
  - Loop breaker: 5 operations → ok; 6th → halted.
  - Loop breaker: after 10min window passes → counter resets, operations resume.
  - Loop breaker: halted state → all ops rejected until manual reset.
  - Audit events: every create/delete emits correctly structured event.
  - Capability gate integration: `observability` agent (Tier 0) cannot invoke `create_silence` even though grafana-mcp is shared.

---

## Phase 3 — Bidirectional sync + conflict resolution

Goal: incident.io ↔ Grafana silence state is synchronized, with incident.io as SoT and
conflicts resolved automatically per design.md Decision 3.

- [ ] T3.1: `src/agents/incident_management/sync.py` — `SilenceSync` component:
  - Polls incident.io for silence state of active incidents (interval: 30s, configurable).
  - Polls Grafana for active silences created by this agent (filter by `createdBy` or comment tag).
  - Compares states, applies resolution rules:
    - incident.io=silenced, Grafana=no silence → create silence.
    - Grafana=silence, incident.io=active → delete silence.
    - Both agree → no-op.
  - Each resolution action goes through the capability gate + audit.
- [ ] T3.2: Conflict resolution audit trail:
  - Every resolution emits: `{type: "conflict_resolution", direction, incident_id, alert_name, action_taken, reason}`.
- [ ] T3.3: Sync health metric:
  - `aigent_incident_mgmt_sync_conflicts_total{resolution_type}` — counts conflict occurrences.
  - `aigent_incident_mgmt_sync_latency_seconds` — polling cycle duration.
- [ ] T3.4: Grafana silence tagging:
  - Silences created by this agent include a comment: `[aigent:incident-management] incident_id={id} expires={ttl}`.
  - Sync only manages silences with this tag — never touches manually-created silences.
- [ ] T3.5: Tests (independent author, ≥90%):
  - Conflict: incident.io=silenced, Grafana=none → silence created.
  - Conflict: Grafana=silenced, incident.io=active → silence deleted.
  - No conflict: both agree → no action.
  - Manual silence (no agent tag) → ignored by sync.
  - Sync resolution goes through capability gate → audit emitted.
  - Sync resolution respects loop breaker → halts if threshold hit.
  - E2E (48h soak): deploy to dev, create test incident + silences, verify steady-state.

---

## Phase 4 — Observability + docs + revocation

Goal: full operational visibility, documentation, and the after-the-fact poisoning defense.

### Revocation path

- [ ] T4.1: `src/core/kb/revocation.py`:
  - `revoke_kb_entry(kb_id, reason, revoker)`:
    - Sets `status: revoked` in Postgres.
    - If GitLab MR exists → close MR (or revert if merged, via revert MR).
    - Emits `kb_entry_revoked` audit event.
- [ ] T4.2: API endpoint: `POST /kb/{id}/revoke` (extends spec 21's approval endpoints).
- [ ] T4.3: Post-hoc anomaly detection on KB entries:
  - MR creation rate > 3× baseline → alert.
  - MR content length > 2× p95 of existing entries → flag for review.
  - >2 revocations from the same source investigation → block that investigation from future distillation.

### Metrics

- [ ] T4.4: Metrics (extend `src/core/metrics.py` — the existing OTel meter + Prometheus exporter
      path; do NOT invent a second telemetry path). Every label below is a **fixed enum**; no
      `incident_id`, `investigation_id`, `mr_url`, `silence_id`, `kb_entry_id` or `alertname` ever
      appears as a metric label — those go on the structured log record:
  - `aigent_documentation_rag_mrs_total{outcome=created|rejected|revoked}`
  - `aigent_documentation_rag_content_safety_total{result=passed|rejected|flagged, check_type}` —
    `check_type` is a closed enum: `instruction_injection|executable_code|url_violation|
    length_anomaly|repetition|denylist`. The first draft used a free-text `reason`, which is
    unbounded.
  - `aigent_incident_mgmt_silences_total{action=create|delete, outcome=success|rejected|halted}`
  - `aigent_incident_mgmt_field_updates_total{outcome=success|error|superseded}` — `superseded`
    counts RCA corrections (H-3).
  - `aigent_incident_mgmt_sync_conflicts_total{resolution_type}`
  - `aigent_incident_mgmt_loop_breaker_triggered_total`
  - `aigent_incident_mgmt_write_dropped_total{target=incident_io|grafana, reason=timeout|rate_limited|retry_exhausted}` — degraded write, so a dropped write is never silent (H-4).
  - `aigent_incident_mgmt_sync_failure_total{target}` — source-of-truth unreachable (H-5).
  - `aigent_kb_retrieval_confidence` (histogram, label `type` only) — detects downward drift in the
    confidence distribution, one of the few signals that can surface systematic poisoning (H-8).
  - Every audit event named in requirements (`poisoning_attempt_detected`, `kb_entry_revoked`,
    `runaway_silencing_loop`) MUST have a counter here **and** a log record. Today's draft alerts on
    `content_safety_total{result="rejected"}`, which is a broader signal than the named event — they
    are not the same thing (H-7).

### VMAlert rules

- [ ] T4.5: VMAlert rules (add to existing rules file):
  - `DocumentationRagPoisoningAttempt`: fires on the **named** event counter, not on the broader
    content-safety rejection counter (H-7).
  - `DocumentationRagHighRejectionRate`: `rate(aigent_documentation_rag_content_safety_total{result="rejected"}[1h]) / rate(aigent_documentation_rag_content_safety_total[1h]) > 0.5`
  - `IncidentMgmtRunawayLoop`: `aigent_incident_mgmt_loop_breaker_triggered_total > 0` (instant, any trigger)
  - `IncidentMgmtSilenceConflictRate`: `rate(aigent_incident_mgmt_sync_conflicts_total[1h]) > 10`
  - `AigentOrphanSilence`: agent-created silences exist while no active incident is being updated —
    catches the "incident resolved, silence still muting" case (H-5).
  - `AigentSilenceAboutToExpire`: TTL remaining < 15 min while the incident is still active, so the
    re-create/stop decision reaches a human instead of happening silently.
  - `IncidentMgmtWriteDropped`: `rate(aigent_incident_mgmt_write_dropped_total[15m]) > 0` — the
    operator must learn that the system of record was NOT populated (H-4).

- [ ] T4.5b: Per-entry retrieval attribution (H-8): emit a structured log on every RAG retrieval
      carrying `kb_entry_id`, `confidence`, and `trace_id`. Without this, a poisoned entry being
      consumed repeatedly is invisible — today `kb_rag_hits` is a bare total with no attribution, so
      the revocation path this spec depends on has nothing to trigger it.

### Documentation

- [ ] T4.6: `docs/WRITE-AGENTS.md`:
  - Architecture (pipeline diagram), tier assignments, security model.
  - Content-safety validator: what it checks, how to extend patterns.
  - Revocation workflow: how to revoke a poisoned entry.
  - Loop breaker: how to manually reset.
  - Bidirectional sync: resolution rules, how to override.
- [ ] T4.7: Update `docs/SECURITY.md` §S6 (from spec 43) — add documentation-rag and incident-management agent details.
- [ ] T4.8: Update `docs/KNOWLEDGE-BASE.md` (from spec 21) — add GitLab sink, provenance, revocation.
- [ ] T4.8b: Update `README.md` and `AGENTS.md` — the agent roster gains two agents that are NOT
      read-only. Both files currently assert the read-only property; leaving them unchanged ships a
      documented lie (spec 43 T5.7/T5.8 does the global restatement, this task adds the two agents).
- [ ] T4.8c: Update `docs/METRICS.md` — one row per metric added in T4.4 with its exact bounded
      label set, plus the two new `agent_id` values in the Labels table.
- [ ] T4.9: Update `docs/MCP_INTEGRATION.md` — add `grafana-mcp` write tools exposed only to `incident-management`.
- [ ] T4.10: `CHANGES.md` entry.

---

## Order dependencies (cross-phase)

```
spec 43 Phase 3 complete ──────────────────────────────────────────┐
                                                                    │
Phase 0 (this spec: round-table) ──────────────────────────────────┤
    ↓                                                               │
Phase 1 (content-safety + documentation-rag)                       │
    ↓                                                               │
Phase 2 (incident-management + silence manager) ←──────────────────┘
    ↓                                                       (uses capability gate from spec 43)
Phase 3 (bidirectional sync)
    ↓
Phase 4 (observability + docs + revocation)
```

Phase 1 and Phase 2 are **independent** of each other — they can be developed in parallel.
Phase 3 depends on Phase 2 (sync requires the silence manager to exist).
Phase 4 depends on Phase 1 + Phase 2 + Phase 3 (observability covers both agents + sync).

---

## Deferred (not in this spec's scope — tracked for completeness)

| Item | Trigger to activate |
|------|---------------------|
| Tier 3 operational agents (cluster-ops: pod restart, rollout force, Helm delete, PVC grow) | Separate spec; requires spec 43 Phase 4 (HITL gate) complete |
| Advanced NER-based content-safety (beyond regex) | If false positive rate on content-safety validator > 30% in production |
| Opus enricher activation for KB distillation | When `BEDROCK_OPUS_MODEL_ID` is configured (spec 21 deferred item) |
| Grafana dedicated SA with silence-only permissions | When Grafana v11.2+ RBAC is deployed (spec 43 deferred item) |
| Slack-based approval for documentation-rag MRs (replacing GitLab MR review) | If GitLab MR review latency > 48h consistently |
| incident.io webhook (push-based sync, replacing polling) | When incident.io webhook integration is provisioned |
| Cross-investigation knowledge graph (linking related learnings) | When KB has > 200 entries and pattern detection demands it |
