# Tasks: Quality Eval Harness

> After spec 36 (make targets exist). T1 lands first — it gates the two known defect
> classes before their fixes even ship. Verification pipeline per `specs/README.md`.

## Phase 1 — T1 structural gate (deterministic, $0)

- [ ] T1: Defect-class fixture corpus — reproduce the two shipped classes as fixtures:
      tool-scaffolding leak (`<use_mcp_tool>` debris) and raw adapter error surfaced
      (`AccessDenied`, `Traceback`, `botocore.exceptions`) — suite must FAIL on pre-fix
      behavior (regression proof, attack-suite method)
- [ ] T2: `tests/test_response_quality.py` — mocked-Bedrock pipeline per agent asserting:
      no scaffolding tokens, no raw error strings in user-facing content, response
      contract fields present, no empty/duplicated context blocks (depends on: T1)
- [ ] T3: Wire into the existing CI 90% gate (offline, deterministic — spec 23 rules)
      (depends on: T2)

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
