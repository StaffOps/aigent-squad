# Tasks: RCA Investigation Workflow

> Prerequisite: `17-multi-agent-collaboration` (fan-out), `19-config-driven-platform` (datasources/limits via config), `09-otel-instrumentation` (trace/timestamps).

## Phase 1 — Single-round RCA (MVP of the differentiator)

- [x] T1: Models `Evidence` and `RCAResult` in `src/core/investigation.py` — done 2026-06-14
- [x] T2: Timeline builder — orders `Evidence` temporally, marks cause candidates (deploy/config/restart) (depends on: T1) — done 2026-06-14
- [x] T3: Correlator — rule "≥3 independent signals → high; 2 → medium; 1 → low"; counter-evidence downgrades (depends on: T1) — done 2026-06-14
- [x] T4: Trivial-vs-investigate decisor (explicit threshold, no extra Bedrock call) — done 2026-06-14
- [x] T5: `investigation.py` in supervisor — orchestrates: time window + evidence fan-out (reuses spec 17) (depends on: T1, T4) — done 2026-06-14
- [x] T6: RCA synthesizer — 1 Bedrock call (Sonnet) merges evidence → `RCAResult` + prevention (depends on: T2, T3, T5) — done 2026-06-14
- [x] T7: Intent/flag `mode=investigate` in supervisor + output contract `RCAResult` (depends on: T5, T6) — done 2026-06-14
- [x] T8: Cost ceiling (number of agents, number of evidence queries) read from config (spec 19) and applied before calls (depends on: T5) — done 2026-06-14 (via env var cost cap)
- [x] T9 (test-author DIFFERENT from author): pytest ≥90% — parallel collection, timeline, correlation (3/2/1 signals), counter-evidence, trivial fast-path, cost ceiling (depends on: T7, T8) — done 2026-06-14
- [x] T10: Independent review (`code-review`): read-only preserved (zero remediation), anti-false-confidence, cost (depends on: T9) — done 2026-06-14
- [ ] T11: Smoke via Docker — 1 trivial symptom (fast-path) + 1 cross-domain (full investigation) (depends on: T10) — deferred (manual smoke only)

## Suggested order
T1→T2/T3; T4; T5→T6→T7; T8; T9→T10→T11.

## Phase 1.5 — Evidence-model correlator + real-RCA proof (added 2026-07-04)

> Gap found in the 2026-07-04 product review: `EVIDENCE-MODEL.md` (causal layers,
> independence test, 14 root-cause signatures — deliberated 2026-06-02 precisely to
> REPLACE the naive "≥3 signals" rule) was never implemented; T3 above shipped the
> naive correlator. The differentiator's best design is on paper only. Additionally,
> no real investigation has ever run end-to-end on a real incident.

- [ ] T12a: **Signal-coverage audit** (PR-02) — map all 33 EVIDENCE-MODEL signals
      (C1–C8, M1–M13, I1–I8, T1–T4, E1–E4) to what today's adapters can ACTUALLY
      collect. Known already: observability's only datasource is a static
      `query=up` (no PromQL-by-symptom, no Loki, no Tempo); devops has no
      deploy-history query (no ArgoCD). Output: coverage table → scopes the
      `37-evidence-adapters` candidate (BACKLOG B-01). Do BEFORE T12 — the
      correlator's value is bounded by collectable evidence
- [ ] T12: Implement the EVIDENCE-MODEL correlator in `src/core/investigation.py` —
      `Evidence` dataclass extended (causal_layer, fault_domain, timestamp_precision,
      derivation_source), `count_independent()` with DERIVATION_PAIRS,
      `validate_temporal_order()` with per-source tolerances, layer-based
      `score_confidence()` (Track A/B, contra-evidence blockers)
- [ ] T13: LLM confidence as ceiling + soft floor (synthesizer can lower with logged
      justification, never raise) — per EVIDENCE-MODEL §5/§8 (depends on: T12)
- [ ] T14: Measure the gain — re-run the spec-35 RCA scenario baseline (T9) after T12/T13;
      record before/after in `evals/results/` (depends on: T12, spec 35 T9)
- [ ] T15: **Real-RCA existence proof** — wire Alertmanager (or manually replay a real
      past incident's alert) in devops-core → `/alerts/incoming` → investigation →
      Slack post-back; write the result up as `docs/CASE-001.md` (symptom, evidence,
      RCA produced, human verdict on correctness). This is the product's first
      existence proof — worth more than any further platform work
- [ ] T16 (independent review): correlator vs EVIDENCE-MODEL spec — signatures, timing
      tolerances (CloudWatch 120s rule), independence semantics (depends on: T12–T14)

## Phase 2 — promotion triggers (partially implemented)

Open Phase 2 **only if** a trigger is observed in real usage:

| Trigger | Phase 2 Capability | Status |
|---------|-------------------|--------|
| Majority of RCAs need 2+ rounds to conclude | Iterative investigation loop (collection directed by 1st hypothesis) | NOT IMPLEMENTED |
| Single hypothesis is frequently wrong | Multi-hypothesis + fault-tree (rank competing hypotheses) | NOT IMPLEMENTED |
| Operators reopen the same incident | Integration with `21-incident-memory-learning` (retrieve similar cases) | ✅ done (spec 21) |
| Volume justifies proactive detection | Webhook that opens investigation from alert (no user) | ✅ done 2026-06-14 |

**Phase 2 capabilities IMPLEMENTED 2026-06-14:**
- ✅ Alert ingestion via `POST /alerts/incoming` (Alertmanager v2)
- ✅ Fingerprint dedup via Redis (configurable TTL `ALERT_DEDUP_TTL`)
- ✅ Slack post-back via `SLACK_WEBHOOK_URL` (opt-in)
- ✅ Metrics: `aigent.alerts.received`, `.deduplicated`, `.investigation_triggered`, `.postback`
- ✅ docs/ALERTING.md

**Phase 2 capabilities still NOT IMPLEMENTED:**
- Multi-round iterative investigation
- Multi-hypothesis fault-tree

## Notes
- Read-only is an invariant: the investigation **proposes** prevention, never executes.
- Do not reintroduce LangGraph (steering `project.md`) — orchestration is custom code.
- Cost is the biggest risk — ceiling via config is a hard gate, not a recommendation.
- Verification pipeline (`verification-independence.md`): T1–T8 author; T9 test-author in a different session; T10 code-review.

## Status (2026-06-14)

**Completed**: Phase 1 (T1–T10). Evidence/RCAResult models, timeline builder, correlator, triage decisor, investigation orchestrator, RCA synthesizer, `mode=investigate` flag, cost cap via env, tests, and code-review.

**NOT implemented (Phase 2)**:
- Multi-round iterative investigation (loop directed by 1st hypothesis)
- Alert ingestion endpoint (`/alerts/incoming`)
- Multi-hypothesis fault-tree (rank competing hypotheses)
- These remain as promotion triggers — implement only when real usage demonstrates Phase 1 is insufficient.

**Deferred**: T11 formal smoke test (manual smoke performed), Phase 2 entirely.
