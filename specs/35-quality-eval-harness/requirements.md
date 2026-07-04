# Feature: Quality Eval Harness (golden sets per agent + RCA scenarios, CI-gated)

**Spec**: `35-quality-eval-harness`
**Severity**: 🔴 High (the product's VALUE has no gate — security does, quality doesn't)
**Origin**: critical product analysis 2026-07-04. Evidence: two response-quality defects
shipped to the real cluster and were caught only by ad-hoc human reading (aws agent
echoing raw `<use_mcp_tool>` XML; finops surfacing an AccessDenied error in every
answer). Meanwhile security has a 134-case deterministic CI gate (`test_attack_suite.py`)
— the exact pattern quality lacks. The RCA differentiator (spec 18) has never been
scored against a known-answer scenario.
**Depends on**: `36-agent-native-dev-loop` (make targets, local loop). Related: spec 33
(eval scores feed the operational review), spec 18 (RCA scenarios).

---

## Thesis

"Our RCA is good" is currently an untested claim, and "the agents answer well" is
validated by whoever happens to read a response. The repo already proved the right
pattern with the attack suite: **deterministic, no-cost, parametrized cases as a CI
gate**, plus a smaller live tier for what genuinely needs a model. This spec applies
that same two-tier pattern to functional quality: cheap structural checks on every push,
and a scored golden-set/LLM-judge tier run on demand and per release — producing a
number the operational review (spec 33) and release homologation (spec 34) can cite.

## Two tiers (mirroring the security suite's economics)

| Tier | Runs | Cost | What it catches |
|------|------|------|-----------------|
| **T1 structural** (deterministic, mocked Bedrock) | every push (CI gate) | $0 | tool-scaffolding leaks (`<use_mcp_tool>`, raw XML/JSON debris), error strings surfaced to users (`AccessDenied`, tracebacks), empty/duplicated context blocks, contract violations, adapter-failure text reaching the answer verbatim |
| **T2 scored** (real Bedrock, golden sets + judge) | `make eval` on demand + release homologation (spec 34) + monthly review (spec 33) | ~$1–3/run | wrong/low-quality answers, routing misses, RCA correctness vs known-answer scenarios |

## User Stories

WHEN any code/prompt/config change is pushed THEN T1 SHALL fail CI if an agent response
(mocked pipeline) contains tool-scaffolding artifacts, raw adapter error strings, or
violates the response contract — the aws-XML and finops-AccessDenied classes become
regressions, not discoveries.

WHEN `make eval` runs THEN each enabled agent SHALL be scored against its golden set
(10–20 curated questions with expected-content criteria) and the run SHALL emit a
per-agent score plus a diff vs the last recorded run.

WHEN the RCA flow is evaluated THEN ≥3 synthetic incident scenarios with a known root
cause (fixture-fed evidence, e.g. deploy-regression, OOM/memory-leak, dependency-outage
— straight from EVIDENCE-MODEL signatures) SHALL be scored on: correct hypothesis,
evidence cited, confidence calibrated to the rules, prevention proposed.

WHEN a golden-set score drops below its recorded baseline by more than the tolerance
THEN the release homologation (spec 34) SHALL treat it as a blocker finding.

WHEN eval results are produced THEN they SHALL be persisted (versioned JSON/markdown
under `evals/results/`) so spec 33 reviews can trend them.

## Acceptance Criteria

- [ ] `evals/` layout: `golden/<agent>.yaml` (question, must-contain / must-not-contain,
      routing expectation), `rca/<scenario>.yaml` (symptom, fixture evidence, expected
      root cause + confidence), `results/` (dated, versioned).
- [ ] **T1 structural suite** (`tests/test_response_quality.py`): mocked-Bedrock
      pipeline runs per agent; asserts NO tool-scaffolding tokens, NO raw adapter error
      strings (`AccessDenied`, `Traceback`, `botocore.exceptions…`) in user-facing
      content, contract fields present. Deterministic, offline, joins the 90% CI gate.
- [ ] **T2 runner** (`make eval`): executes golden sets against the real local stack
      (or configured endpoint), scores must-contain/must-not/routing mechanically, and
      uses an LLM judge (Haiku, temperature 0) ONLY for the open-ended quality rubric —
      judge prompts + rubric versioned in `evals/`.
- [ ] ≥3 RCA scenarios implemented with fixture evidence mapped to EVIDENCE-MODEL
      signatures (#1 deploy regression, #2 memory leak, #3 dependency outage).
- [ ] Baseline + tolerance recorded after the first full run; `make eval` prints the
      diff vs baseline; spec 34 homologation references it.
- [ ] Per-agent golden sets curated for the 5 agents (10–20 Qs each); questions must be
      answerable by the datasources the agent ACTUALLY has (finops = CE-only until the
      Athena decision) — the eval doubles as the honest per-agent capability matrix.
- [ ] **Groundedness dimension** (PR-05): numeric claims and resource IDs in an answer
      must appear in the collected `infra_data` — T1 checks it mechanically on mocked
      pipelines (fixture numbers vs response numbers); T2 scores it on live runs. The
      quality twin of the canary check: canary catches data that SHOULDN'T leave,
      groundedness catches claims that never came IN.
- [ ] Metric: `aigent.eval.score` (labels: `suite`, `agent_id`) emitted by the T2
      runner, documented in `docs/METRICS.md`.
- [ ] Results feed spec 33: TRIGGERS.md gains rows for quality regression thresholds.

## Out of scope

- Fixing the defects the first runs will find (aws XML echo, finops error surfacing) —
  those are their own fixes (F-001 + finops datasource decision); the eval EXISTS to
  catch their recurrence.
- Load/latency testing (spec 31 T21 k6 owns that).
- Continuous scheduled eval runs (on-demand + release + monthly first; automate only if
  skipped in practice — same philosophy as specs 33/34).
- Public benchmark participation (OpenRCA/CloudOpsBench) — future spec once internal
  scoring is stable.
