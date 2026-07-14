# Tasks: Quality Eval Harness

> After spec 36 (make targets exist). T1 lands first — it gates the two known defect
> classes before their fixes even ship. Verification pipeline per `specs/README.md`.

## Phase 1 — T1 structural gate (deterministic, $0) — ✅ DONE 2026-07-14

- [x] T1: Defect-class fixture corpus — `tests/test_response_quality_regression.py`
      reproduces the exact live-observed text for the three shipped classes: F-001
      (aws `<use_mcp_tool>` XML), F-002 (finops raw boto3 `AccessDeniedException`
      string), F-003 (kubernetes raw MCP `TaskGroup` exception). Regression proof is
      structural (new patterns exist and fire on the reproduced shapes), not a literal
      git-bisect replay — the pre-fix commits are already merged from earlier the same
      session.
- [x] T2 — implemented as **`src/core/response_quality.py`
      (`ResponseQualityGuard`) + `tests/test_response_quality.py`**: a new production
      guard (not just a test file — see design note below) scans every agent response
      for tool-scaffolding tags (`<use_mcp_tool>`, `<tool_call>`, `<function_calls>`,
      `<invoke>`) and raw adapter/infra error signatures (our own `[svc] error:` /
      `[mcp:...] error:` prefix, `Traceback (most recent call last):`,
      `botocore.exceptions.*`, boto3's `An error occurred (...Exception) when calling`,
      anyio's `unhandled errors in a TaskGroup`). Fail-closed (block, reuses
      `GuardrailBlockedError`) — unlike `CanaryGuard` (L5, redact-and-continue since
      F-005), neither defect class is ever legitimate content, so there's no
      benign-false-positive case to preserve availability for. Wired into
      `GenericAgent.process_request` in the same slot as `OutputFilter` (L4).
      `tests/test_response_quality_regression.py` proves it end-to-end via
      `GenericAgent.process_request` with a mocked Bedrock response, for all 5 agents'
      config shape. Design decision: `design.md`'s own justification ("a regex over the
      final response... catches both, free, on every push") implies the check must run
      in production, not just CI — a pure test-only implementation would not have
      protected the real cluster the next time a prompt regresses.
- [x] T3: Automatic — `pytest tests/` (both `scripts/test-local.sh` and CI's
      `test.yml`) already discovers every file under `tests/`, so the two new files
      join the 90% gate with no separate wiring. Full suite: 727 passed, 94.35% cov
      (response_quality.py itself: 100% cov), lint clean.

**Metric**: `aigent.quality.violations` (labels: `agent_id`, `category`) — documented in
`docs/METRICS.md` §"Quality — Structural Gate (spec 35 T1)".

**Deferred to Phase 1 follow-up / Phase 2**: the groundedness dimension (numeric
claims/resource IDs must appear in `infra_data`) listed in `design.md`'s acceptance
criteria — needs infra_data-vs-response comparison logic that risks false positives on
legitimately paraphrased numbers; not attempted this pass, tracked as open, not silently
dropped.

## Phase 2 — Golden sets + runner (T2 scored)

- [ ] T4: `evals/` layout + schemas — `golden/<agent>.yaml` (question, must-contain,
      must-not-contain, routing), `rca/<scenario>.yaml`, `results/`
- [ ] T5: Curate golden sets for the 5 agents (10–20 Qs each), constrained to CURRENT
      datasources — capability gaps found while writing go to BACKLOG as product
      findings (this task IS the capability matrix audit) (depends on: T4)
- [ ] T6: Eval runner — mechanical checks first; LLM judge (Haiku, temp 0, versioned
      rubric) for the open-ended residue; emits `evals/results/<date>.json` + diff vs
      baseline + `aigent.eval.score {suite, agent_id}`; exposed as `make eval`
      (depends on: T4, spec 36 T4)
- [ ] T7: Record the first baseline + tolerance band; document in `evals/README.md`
      (depends on: T5, T6)

## Phase 3 — RCA scenarios (the differentiator gets a score)

- [ ] T8: 3 fixture-fed scenarios mapped to EVIDENCE-MODEL signatures — #1 deploy
      regression, #2 memory leak (Track B), #3 dependency outage — expected root cause
      + confidence per the model's rules (depends on: T4)
- [ ] T9: Score the RCA flow end-to-end on the 3 scenarios; record baseline. This
      baseline is the BEFORE for spec 18 Phase 1.5 (EVIDENCE-MODEL correlator) —
      re-run after to measure the gain (depends on: T6, T8)

## Phase 4 — Integration + close

- [ ] T10: `docs/METRICS.md` (+`aigent.eval.score`) and TRIGGERS.md rows (quality
      regression thresholds) — feeds specs 33/34 (depends on: T7, T9)
- [ ] T11 (independent author): review/extend suites against the contract — especially
      that T1 fails on the reproduced defect classes and T2 scores are reproducible
      within tolerance (depends on: T3, T9)

## Order
T1→T2→T3 (ship first); T4→T5/T8; T6→T7/T9; T10; T11 closes.

## Notes
- T2 never runs implicitly — explicit `make eval`, release homologation (spec 34),
  monthly review (spec 33). No surprise Bedrock spend.
- Golden-set update becomes definition-of-done for any spec touching an agent's
  prompt/datasources (rule lands in specs/README.md, spec 32).
