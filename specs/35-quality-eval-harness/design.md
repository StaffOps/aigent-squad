# Design: Quality Eval Harness

## Architecture (two tiers, same economics as the attack suite)

```
push ──▶ T1 structural (tests/test_response_quality.py)
         mocked Bedrock · deterministic · $0 · joins the 90% CI gate
         catches: scaffolding leaks, raw error strings, contract violations

make eval ──▶ T2 scored (evals/runner)
              golden/<agent>.yaml ─ mechanical checks (must-contain / must-not / routing)
              rca/<scenario>.yaml ─ fixture evidence → investigation → expected root cause
              open-ended rubric ─ LLM judge (Haiku, temp 0, versioned prompt)
              └─▶ evals/results/<date>.json + score diff vs baseline
                        └─▶ spec 33 review trends · spec 34 homologation gate
                        └─▶ aigent.eval.score {suite, agent_id}
```

## Rationale (decisions)

### Decision 1: two tiers — structural gate always, scored eval on demand

**Choice**: deterministic zero-cost checks on every push; model-in-the-loop scoring only
at explicit moments (release, monthly review, `make eval`).

**Justification, in order of strength**:
1. **The two shipped defects were structural, not semantic.** `<use_mcp_tool>` debris and
   `AccessDenied` strings need no LLM to detect — a regex over the final response in a
   mocked pipeline catches both, free, on every push. Gate the cheap class immediately.
2. **Proven in-house economics**: the attack suite made the same split (134 deterministic
   cases as gate; live cluster probes at homologation) and it worked — findings A–D came
   from the live tier, regressions are held by the deterministic tier.
3. Real-Bedrock evals on every push would cost money, add latency and flake (throttling)
   — exactly what spec 23 banned from the unit gate.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| T1 can't judge answer *quality* | Not its job — T2 does, at the moments quality decisions are made |
| T2 scores can drift between runs (model nondeterminism) | temp 0 + mechanical checks first + judge only for the rubric residue; tolerance band on the baseline |

**When this would be wrong**: if T2 stays unrun for 2+ cycles (same failure mode as
spec 33 reviews) → wire it as a scheduled job then, not before.

### Decision 2: mechanical checks first, LLM judge last

**Choice**: per golden question, must-contain / must-not-contain / routing expectation
are string/structure checks; the judge (Haiku, temperature 0, versioned rubric) scores
only what strings can't (coherence, actionability).

**Justification**:
1. Mechanical checks are free, stable and debuggable — a failed `must-contain: "640"`
   points at the exact regression; a judge score of 6/10 points at nothing.
2. Judge-only evals are circular (an LLM grading an LLM with no anchor); anchoring on
   curated expected-content keeps the judge to the residue where human curation is too
   expensive.
3. Haiku at temp 0 keeps a full T2 run in the ~$1–3 range (same tiering logic as
   spec 11: cheap model for constrained tasks).

### Decision 3: RCA scenarios are fixture-fed, mapped to EVIDENCE-MODEL signatures

**Choice**: synthetic incidents inject evidence via adapter fixtures (mocked datasource
outputs), not by breaking real infrastructure; each scenario is a known root-cause
signature (#1 deploy regression, #2 memory leak, #3 dependency outage).

**Justification**:
1. Deterministic and cheap: the investigation pipeline (classifier → fan-out →
   correlate → synthesize) runs for real; only the *world* is fixtures — so the score
   measures OUR correlation/synthesis, not the chaos of a lab cluster.
2. EVIDENCE-MODEL's signatures are precisely "expected causal patterns" — they are the
   answer key the correlator should reproduce. This also makes the eval the *acceptance
   test* for implementing EVIDENCE-MODEL in the correlator (spec 18 Phase 1.5): score
   before vs after is the ROI measurement.
3. Chaos-engineering-style live scenarios are a different (expensive) tool — deferred
   until fixture evals stop finding problems.

### Decision 4: the golden sets double as the honest capability matrix

**Choice**: each agent's golden set may only contain questions its CURRENT datasources
can answer (finops = Cost Explorer only, devops = what HttpAdapter actually reaches).

**Justification**: writing the sets forces the per-agent capability audit the product
analysis called for — gaps become visible as "can't write 10 answerable questions for
this agent", which is a product finding (BACKLOG), not an eval failure. The eval then
protects the floor while the roadmap raises the ceiling.

## Invariants

- T1 is deterministic and offline — no network, no cost, no flake (spec 23 rules).
- T2 never runs implicitly (explicit `make eval` / release / review) — no surprise spend.
- Judge prompts + rubric are versioned files; a judge change invalidates the baseline
  (recorded in the result header).
- Eval fixtures never contain real customer/infra data (same hygiene as spec 21 PII rules).
- A baseline regression beyond tolerance is a release BLOCKER (spec 34), not a warning.

## Verification

- Seed T1 with the two known defect classes reproduced as fixtures — suite must fail on
  pre-fix code and pass after (regression proof, same method as the attack suite).
- T2 dry-run on the aws agent golden set against the local stack; score reproducible
  within tolerance across 2 consecutive runs.
- RCA scenario #1 (deploy regression) scored end-to-end; result file lands in
  `evals/results/` with baseline header.

## Risks

- Golden sets rot as prompts/datasources evolve → each `agent.yaml`-touching spec must
  update the agent's golden set (definition-of-done addition via specs/README.md).
- Judge leniency drift → temp 0 + versioned rubric + mechanical anchors bound it; the
  baseline diff makes drift visible as a score jump without a code change.
- Overfitting prompts to the golden set → sets are curated, private-ish and rotated at
  reviews (spec 33 owns rotation cadence).
