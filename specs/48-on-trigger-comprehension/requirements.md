---
spec: 48-on-trigger-comprehension
status: not-started
completed: null
superseded_by: null
depends_on: ["18-rca-investigation-workflow", "30-datasource-cache-layer", "35-quality-eval-harness", "37-agentic-tool-calling", "41-calibrated-honesty-structured", "46-portable-deployment-profiles"]
deferred: []
---

# Feature: On-trigger comprehension (entity registry + baseline verdicts)

**Spec**: `48-on-trigger-comprehension`
**Origin**: BACKLOG strategic line B-17, B-21, B-22, B-23, B-24 (referred to there as
"spec 38-on-trigger-comprehension"; 38 is taken by model-tier escalation, so this is 48).
Product decision D-03 (2026-07-04): the squad is **reactive by design**. Requirements seed:
`docs/BEHAVIOR-BASELINES.md`.
**Depends on**: 18 (Evidence model + correlator), 30 (Redis datasource cache), 35 (eval
harness), 37 (agentic loop, where context is consumed), 41 (calibrated honesty, groundedness
scan), 46 (per-environment config: addresses live in the deployment, never in this repo).

---

## Problem

Asked "is service X slow?", an agent fetches p99 = 840 ms and the LLM guesses whether that is
bad. It does not know what 840 ms means for that service at that hour. Alerts arriving at
`/alerts/incoming` become a free-text symptom; nobody checks whether the alerted signal is
still abnormal before an investigation spends tokens on it. And when no history exists, the
answer has no standard way to say "I could not judge whether this is normal".

## Thesis

**Judgments are code; the LLM interprets judgments.** When triggered (a user question or an
alert, never a schedule), the squad resolves which entity is meant from a per-environment
**entity registry**, computes in code whether each of the entity's declared signals is
`normal`, `borderline`, `abnormal` or `no-baseline` against a robust baseline, and hands the
model those **verdicts** (plus a truncated raw sample) instead of raw numbers alone. Verdicts
cost zero LLM tokens to produce. Nothing polls, watches or pages.

---

## User Stories

WHEN a deployment provides an entity registry file THEN the squad SHALL load and validate it at
startup.

WHEN the registry file is invalid or unreadable THEN comprehension SHALL be disabled, a startup
WARNING SHALL name the problem (entry and field, never values), `/ready` SHALL report it as an
informational detail (never 503), a metric SHALL record it, and the rest of the squad SHALL run
normally (owner decision 2026-11-24).

WHEN no registry is configured THEN comprehension SHALL be off, the squad SHALL behave exactly as
today, and startup SHALL log `comprehension_disabled` once.

WHEN a user question mentions a registry alias THEN the supervisor SHALL resolve it to that entity
deterministically (no LLM call) and compute verdicts for the entity's signals before the agents
answer.

WHEN an alias matches more than one entity THEN no verdict SHALL be computed for it and the
context SHALL list the candidates, so the answer can ask which one was meant.

WHEN a verdict is computed THEN it SHALL carry: entity, signal, current value, baseline method,
expected value and spread, verdict, deviation, `since` (when the excursion began, if any),
baseline age, and the EVIDENCE-MODEL id + layer of the signal.

WHEN a signal has no usable baseline (no data, too little history, source down, budget
exhausted, degenerate series) THEN its verdict SHALL be `no-baseline` with a reason, and the
answer SHALL say it could not assess the normality of that signal. An empty query result SHALL
never become `normal`.

WHEN the agent answers with verdicts in context THEN it SHALL cite the verdict rather than
re-judging normality itself, and the numbers in the verdict block SHALL count as grounded for the
spec 41 groundedness scan.

WHEN an Alertmanager alert matches a registry entity THEN `/alerts/incoming` SHALL attach the
entity, the alerted signal and its current verdict BEFORE the investigation starts. A `normal`
current verdict SHALL be reported ("alert firing, signal currently within baseline") and SHALL NOT
suppress the investigation.

WHEN an investigation runs THEN computed verdicts SHALL enter `Evidence[]` with `signal_id`,
`causal_layer` and `strength` pre-filled (abnormal and borderline as their catalog layer, normal as
elimination, no-baseline never as evidence).

WHEN alert labels or user text contain query syntax THEN nothing from them SHALL reach a metrics
query; queries are built only from registry-defined values.

WHEN the metrics source, Redis or the registry signal is unavailable THEN the request SHALL still
be answered, with `no-baseline` verdicts for the affected signals (fail-open for availability).

WHEN an investigation or alert flow starts (Phase 2) THEN a compact environment snapshot (registry
inventory + last cached verdicts) SHALL be available as context, computed lazily on that trigger and
cached with a TTL — never refreshed by a background timer. The snapshot SHALL NOT be injected into
plain chat queries (owner decision 2026-11-24).

WHEN an operator reviews comprehension quality THEN metrics SHALL expose verdict mix, `no-baseline`
reasons, coverage, baseline freshness and verdict latency with bounded labels only, and TRIGGERS.md
SHALL carry the review rows.

---

## Acceptance Criteria

1. Registry schema (Pydantic, versioned `version: 1`) covering entity id, aliases, kind, tier,
   owner, k8s identity, metric identity labels, cost tag, alert match, signals (id, EVIDENCE-MODEL
   id, `why`, query template, unit, method, direction, thresholds) and declared blind spots;
   loaded from `ENTITY_REGISTRY_FILE`; unset → feature off; invalid/unreadable → feature off +
   startup WARNING (problem named, no values) + `/ready` informational detail (200) +
   `aigent.comprehension.registry_load{result}`; the squad otherwise starts normally.
2. The repo ships ONLY a generic example registry with placeholders (entry #1 = the squad itself,
   using the product's own `aigent_*` metrics); no environment-specific names. The chart mounts a
   registry supplied by the deploying team's own config repo.
3. Deterministic entity resolution (alias, case-insensitive, word-boundary) for user text and exact
   label match for alerts; ambiguous → candidates listed, no verdict; caps on entities and signals
   per request.
4. Verdict engine in pure code with four methods — `static`, `robust-z` (median/MAD), `seasonal`
   (same hour-of-week, K weeks), `derivative` — plus minimum-history and sustain (M of N) rules,
   with defaults (borderline z = 2, abnormal z = 3, sustain 3 of 5) overridable globally in the
   registry `defaults` block and per signal; unit-tested against synthetic series with known verdicts, including every `no-baseline` reason.
5. Query construction substitutes only registry values validated against a safe character set; a
   test proves alert labels and user text cannot reach a query string.
6. Metrics source = Prometheus-compatible HTTP query API (read-only GET), address and optional
   token from env (spec 46 style); per-query and per-request timeouts; failures → `no-baseline`.
7. Computed baselines cached in Redis (sha256 keys, method-specific TTL); Redis down → recompute
   (fail-open); cache effectiveness visible through the existing `aigent.cache.*` metrics.
8. Verdicts injected into agent context as a compact, data-tagged `<baseline_verdicts>` block
   (bounded token size) with an env-overridable instruction; the block is part of
   `effective_infra_data` for the groundedness scan.
9. Alert flow: entity + signal + current verdict attached before investigating; never suppresses.
10. Investigation flow: verdicts mapped to `Evidence` (layer/strength rules above).
11. EVIDENCE-MODEL catalog extension (B-24): ids for disk usage, quota headroom and cost anomaly,
    documented in this spec (the frozen `EVIDENCE-MODEL.md` is not edited) and in the code catalog.
12. Metrics (B-23): ≥1 new `aigent.comprehension.*` metric per measured concern, bounded labels
    (no entity, user, query or raw error labels), in `metrics.py` + `docs/METRICS.md`; TRIGGERS.md
    rows for coverage, honesty, freshness, and precision (precision marked GAP until a feedback
    signal exists, B-03).
13. Eval (spec 35): golden questions with known verdicts against a fixture Prometheus-API stub
    (abnormal, normal, no-baseline cases); the no-baseline honesty case passes 100%.
14. Environment snapshot (B-17): lazy, TTL-cached, size-capped, injected into investigation and alert
    flows only — never into plain chat queries.
15. Diff primitive (B-18): `collect_diff(signal, window_a, window_b)` computed in code over the same
    metrics source, returned as a compact delta (Phase 3).
16. Docs: `docs/BEHAVIOR-BASELINES.md` (status → implemented parts), `docs/ARCHITECTURE.md`,
    `docs/METRICS.md`, AGENTS.md env table, chart README, a how-to for writing a registry.
17. Live verification: entity #1 (the squad itself) answers "is the gateway healthy?" with a
    computed verdict on a real cluster; one alert replay shows the verdict before investigation.

## Out of scope

- Any proactive behavior: polling, scheduled baseline refresh, watching, paging, alert
  suppression or alert generation (D-03; external anomaly-detection systems own that).
- ML baselines (Prophet, isolation forest, etc.). The four methods are the default; a signal that
  defeats all four is the trigger to revisit, recorded via spec 33 review.
- Using an external anomaly-detection system as a baseline source (B-05/D-03: optional; its alerts
  already arrive at `/alerts/incoming`). The metrics-source interface leaves room for it.
- Gap-directed round 2 (B-13): it needs the verdicts this spec produces, but it changes the
  investigation loop and belongs in a spec extending 18.
- Incident exclusion from baseline windows via KB lookup (BEHAVIOR-BASELINES §4) — deferred to after
  Phase 1 (owner decision 2026-11-24); listed as future work in `tasks.md`.
- A runtime check that the answer states each `no-baseline` — honesty is eval-gated only (owner
  decision 2026-11-24).
- Log/trace-based verdicts (Loki/Tempo). Phase 1–3 cover metric signals only.
- Verdict precision measurement until a user-feedback signal exists (B-03).
- Editing `specs/EVIDENCE-MODEL.md` (frozen record).
