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

**Groundedness dimension shipped 2026-07-15** (was deferred here as "needs
infra_data-vs-response comparison logic that risks false positives"): resolved the
false-positive risk by splitting the check into two confidence tiers instead of one.
Resource IDs (instance/volume/SG/snapshot/subnet/VPC/AMI, ARNs) are hard-blocked
(`quality:ungrounded_resource_id`) — an ID is never a legitimate derived value, so this
carries the same zero-false-positive-risk property as the original T1 patterns. Numeric
dollar-amount claims are metric-only (`aigent.quality.ungrounded_numeric_claims`,
non-blocking) — exactly the "legitimately paraphrased/derived number" risk that was
the original blocker, sidestepped by not hard-failing on it at all, just measuring it.
`src/core/response_quality.py::ResponseQualityGuard.scan()` gained an `infra_data`
param (threaded from `generic_agent.py`, defaults to `""` — no `infra_data` means no
groundedness checking, not a false-positive flood). Tests:
`tests/test_response_quality_groundedness.py` (13 cases). Full suite: 748 passed,
94.44% cov, lint clean.

## Phase 2 — Golden sets + runner (T2 scored) — ✅ DONE 2026-07-14

- [x] T4: `evals/` layout + schemas — `evals/golden/<agent>.yaml` (question,
      `must_contain_regex`, `must_not_contain_regex`, `routing_expected`),
      `evals/judge_prompt.md` (versioned rubric), `evals/results/`.
      `rca/<scenario>.yaml` deferred to Phase 3 (not needed until T8/T9).
- [x] T5: Curated golden sets for all 5 agents (35 questions total: aws 9,
      finops 7, kubernetes 9, devops 5, observability 5 — slightly under the
      10-20/agent target for devops/observability, whose real local
      datasources are weak — see each file's header). Constrained to CURRENT
      datasources; capability gaps intentionally included as honesty probes
      (e.g. finops has no Kubecost data since F-002, observability's only
      real query is a single Prometheus `up` check despite broader routing
      keywords — both now documented in `docs/site/agents/overview.md` too).
- [x] T6: `evals/runner.py` — mechanical checks first (floor: a mechanical
      failure zeroes the question regardless of judge score), Haiku judge
      (temp 0, `evals/judge_prompt.md`) for the coherence/actionability
      residue. Emits `evals/results/<date>.json` + diff vs
      `evals/results/baseline.json` + `aigent.eval.score {suite, agent_id}`.
      `make eval` → `scripts/eval-local.sh` (mirrors `scripts/smoke.sh`'s
      Docker/network conventions).
- [x] T7: First baseline recorded (`evals/results/baseline.json`,
      2026-07-14) — real run against the local stack + real Bedrock/AWS
      creds. Documented in `evals/README.md`, including an honest account of
      a real bug the FIRST run found (raw adapter-error text leaking
      verbatim past F-004's honesty instruction, fixed same session — see
      `generic_agent.py`) and known golden-set calibration rough edges
      (mostly `routing_expected` too rigid against investigation-triggering
      phrasing) left for the next iteration rather than chased with more
      real spend. Tolerance band: `TOLERANCE = 0.15` in `runner.py`.

**Calibration verified, baseline re-promoted (2026-07-14, second real run)** — after
the golden-set reword pass (commit `b0e6f98`), re-ran `make eval` against real
Bedrock to confirm the calibration actually worked rather than trusting it
untested: aws 0.411→0.611, finops 0.329→0.471, kubernetes 0.533→0.567,
observability 0.36→0.82, devops 0.88→0.86 (noise, within tolerance). Clear,
real improvement — `evals/results/2026-07-14.json` promoted to
`evals/results/baseline.json` (previous baseline kept as
`evals/results/2026-07-14-first-calibration-baseline-superseded.json`, not
deleted). 7 mechanical failures remain, down from the first run; read the
failure list and it's no longer golden-set noise — 5 of the 7 are the SAME
"refuse a mutating action" pattern across two independently-reworded phrasings,
which pointed at a real classifier defect, not a wording problem. Filed as
**F-007** (`specs/BACKLOG.md`) — the Haiku classifier routes mutation-phrased
requests ("please terminate this instance now") to `unknown` instead of the
domain agent, so the domain agent's own crafted refusal never fires; candidate
fix identified (classifier `SYSTEM_PROMPT` guideline), not yet implemented —
needs another real-Bedrock verification round, held for go-ahead rather than
spent unilaterally. The other 2 residual failures (`finops/no-kubecost-fabrication`,
`finops/idle-resource-collaboration`) are still `routing_expected` rigidity
against investigation-triggering phrasing — same class as the first pass,
left as-is (diminishing returns on further chasing without new signal).

## Phase 3 — RCA scenarios (the differentiator gets a score) — ✅ DONE 2026-07-15

- [x] T8: 3 fixture-fed scenarios mapped to EVIDENCE-MODEL signatures (specs/
      EVIDENCE-MODEL.md signatures #1/#2/#3) — `evals/rca/{deploy-regression,
      memory-leak,dependency-outage}.yaml`. Design decision 3 (design.md): the
      pipeline runs for real (classifier is bypassed — `run_investigation()` is
      called directly with `relevant_agent_names` implicit — fan-out/correlate/
      synthesize all hit real Bedrock); only the *world* is fixed — a new
      `FixtureAdapter(DatasourceAdapter)` (`evals/rca_runner.py`) returns a
      canned string regardless of query, swapped in for each agent's real
      adapters while keeping the agent's real config/prompt/skill_registry from
      the live `AgentRegistry`. Each scenario's fixtures are written as what a
      real adapter WOULD have returned (ISO8601 timestamps, explicit signal
      language) so the model's own evidence-JSON extraction has real signal to
      work with, not summarized-for-it data.
- [x] T9: Scored end-to-end, real Bedrock (`make eval-rca` → `scripts/
      eval-rca-local.sh` → `evals/rca_runner.py`). Score = mechanical only
      (root-cause keyword regex match on the hypothesis + confidence >=
      expected_confidence_min; no LLM judge for RCA — the hypothesis text +
      confidence level was signal enough for a 3-scenario baseline, unlike
      T2's need for a judge on coherence/actionability). **Baseline recorded
      2026-07-15** (`evals/results/rca-baseline.json`): all 3 scenarios
      **score=1.0, confidence=alta**, with substantive, correct hypotheses —
      e.g. deploy-regression: "Deployment of commit a1b2c3d ('refactor payment
      validation') introduced a NullPointerException in
      PaymentValidator.validate() due to missing null-safety checks...";
      dependency-outage correctly inverted causality to the RDS failover, not
      orders-service itself. This is a strong baseline for the CURRENT
      simplified correlator (`src/core/investigation.py::correlate()` — Phase-1
      "count distinct (source_agent, signal_type) pairs", not yet the full
      causal-layer EVIDENCE-MODEL). Re-run after spec 18 Phase 1.5 ships to
      measure the gain — a 1.0 baseline means that gain will show up as
      *hypothesis/evidence quality* (fewer evidence items needed, more precise
      causal-layer attribution), not as a score delta on these 3 fixtures; if
      spec 18 Phase 1.5 wants a harder acceptance bar, the fixture set should
      grow (e.g. a scenario designed to trip the naive correlator's known flaw
      — counting correlated effects as independent confirmation — see
      EVIDENCE-MODEL.md's opening line).
      Note found in passing (not a defect, just recorded): the memory-leak
      scenario's hypothesis came back in Portuguese despite an English symptom
      — the model's language-matching heuristic (`match_user_language`) picked
      up on something in context; harmless for this eval (mechanical regex
      matched Portuguese "memory leak"/"OOMKills" fine), not investigated
      further.

## Phase 4 — Integration + close

- [x] T10 (partial, 2026-07-15): `docs/METRICS.md` + `docs/site/reference/metrics.md`
      updated — `aigent.eval.score` now documents both `suite="golden"` (T2) and
      `suite="rca"` (T9) label values; `evals/rca_runner.py` wired to actually emit
      the metric (was previously scored only to the result JSON, not to OTel).
      **TRIGGERS.md rows explicitly DEFERRED, not done**: `specs/TRIGGERS.md` does
      not exist yet — it is spec 33's own T1 deliverable (a full sweep across specs
      11/17/18/21/25/26 + EVIDENCE-MODEL + VISION, not just spec 35), which hasn't
      started. Creating it here would preempt spec 33's own template/structure
      decisions (design.md Decision 2). Re-visit this task when spec 33 T1 lands.
- [x] T11 (independent author, 2026-07-15): reviewed via a fresh subagent with no
      prior context on this work (`code-review` type). Findings (none fixed yet —
      review-only, tracked in `specs/BACKLOG.md` "T11 independent review findings"):
      (1) Low — the T1 "fails pre-fix, passes after" proof is real in substance but
      split across two test files rather than one disable→re-run inside
      `process_request`; (2) Low-Medium — `evals/runner.py` has no guard for a
      missing/renamed `response` field (silently scores as empty-but-passing) and
      no range clamp on judge coherence/actionability scores; (3) Medium — several
      `must_not_contain_regex` patterns in `evals/golden/*.yaml` use `.{0,N}`
      without `(?s)`/DOTALL, so a fabricated value on its own markdown line evades
      detection; (4) Medium-High — `evals/rca_runner.py`'s `expected_keywords` is a
      single-OR-match with no causal-direction check, so `dependency-outage.yaml`
      (whose own header says it "guards against invert causality") could score 1.0
      on a causality-inverted hypothesis — confirmed the CURRENT baseline hypothesis
      is correctly-directed (not a live bug), but the check itself doesn't enforce
      it; (5) Medium — `agents/security/agent.yaml` is a real 6th registered agent
      (existed since 2026-06-14) that `evals/golden/aws.yaml` and `evals/README.md`
      incorrectly claim isn't in this repo — zero T2 golden-set coverage for it.
      Baseline files independently re-verified as real runs, not hand-edited.

**All 5 T11 findings fixed same day (2026-07-15)** — see `specs/BACKLOG.md`
"T11 independent review findings" row for the full detail per finding.
Re-verified with 2 more real runs (`make eval`, `make eval-rca`): golden-set
baseline promoted (aws 0.611→0.811, finops 0.471→0.671, kubernetes
0.567→0.8, devops 0.86→0.9, observability 0.82→0.78 noise, security 0.86
new); RCA baseline re-confirmed 3/3 scenarios still score 1.0 with the new
causal-direction check active. A bonus false-positive class was caught live
during this re-verification (2 `must_not_contain_regex` checks flagging the
agent's own helpful explanation of a CLI command, not an instruction to run
it) and fixed the same way as the precedent `kubectl delete pod` case.

## Order
T1→T2→T3 (ship first); T4→T5/T8; T6→T7/T9; T10; T11 closes.

## Notes
- T2 never runs implicitly — explicit `make eval`, release homologation (spec 34),
  monthly review (spec 33). No surprise Bedrock spend.
- Golden-set update becomes definition-of-done for any spec touching an agent's
  prompt/datasources (rule lands in specs/README.md, spec 32).
