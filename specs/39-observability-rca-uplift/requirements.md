---
spec: 39-observability-rca-uplift
status: done
completed: "2026-07-23"
superseded_by: null
depends_on: ["37-agentic-tool-calling"]
deferred: ["T3.4 deterministic investigation.py path (Phase 2 — prompt-RCA validated live, trigger not met)"]
---

# Feature: Observability & RCA uplift

Raise the squad's observability accuracy and add real cross-signal root-cause
capability, using **only read-only MCP servers already deployed** (grafana-mcp,
vm-mcp) plus curated metric-name knowledge. Zero new infra.

**Spec**: `39-observability-rca-uplift`

## Motivation

Post spec 37 the agents are agentic (LLM picks read-only tools), but:
- Observability only reaches VictoriaMetrics (vm-mcp). Logs (Loki), traces
  (Tempo), profiles (Pyroscope), alerts/incidents/OnCall/Sift are **not wired**,
  even though **grafana-mcp is already deployed** (SA = 23 read-only rules, PASS).
- Agents **hallucinate metric names** — there is no per-component metric catalog
  in their knowledge, so they guess metric names that don't exist in this env.
- There is no single agent that **correlates ≥3 independent signals** to assert a
  root cause (the investigation flow exists but isn't wired to multi-signal MCPs).

## User Stories

- WHEN an operator asks a logs/traces/profiling/alerts question THEN the squad
  SHALL query the right backend via a **read-only** grafana-mcp tool and answer
  from real data.
- WHEN an agent references a metric THEN it SHALL use a **canonical metric name**
  from the curated catalog (not a hallucinated name).
- WHEN an operator asks "why is X broken?" THEN a **troubleshoot/RCA agent** SHALL
  gather metrics + logs + traces + events (+ deploys) and assert a root cause only
  with **≥3 corroborating signals**, with calibrated confidence.

## Workstreams

- **WS1 — grafana-mcp wiring** (quick win, config-only): add a read-only
  grafana-mcp datasource to the observability agent (and the RCA agent) exposing
  Loki (logs), Tempo (traces), Pyroscope (profiles), Grafana alerts/annotations,
  Incidents/OnCall, and Sift investigations. Read-only allowlist + SA-RBAC gate.
- **WS2 — metric-catalog knowledge**: bring curated per-component metric-name
  catalogs into the squad's `skill_registry` (lazy-loaded `<skills>`), tagged by
  keyword, and list them on the observability/RCA agents so the model uses
  canonical names.
- **WS3 — troubleshoot/RCA agent**: wire grafana-mcp + skills into the existing
  `investigation.py` RCA path and add `rca` as a routable specialist. **NOT
  config-only** — `triage.py::should_investigate()` intercepts RCA queries before the
  classifier, so this needs code (see design Decision 4). Phase 1 = a grounded,
  time-anchored, clearly-labeled *hypothesis assistant*; Phase 2 (deterministic
  multi-query runner) is triggered by eval, not optional.

## Acceptance Criteria

- [ ] Observability agent answers a Loki logs query and a Tempo trace query from real data (live).
- [ ] A metric question yields a canonical metric name present in VictoriaMetrics (verified via labels/`__name__`).
- [ ] The RCA agent, on a seeded/real symptom, returns a root cause citing ≥3 independent signals + a confidence.
- [ ] Every new MCP datasource passes the MCP SA-RBAC audit gate; the Grafana SA token is Viewer/read-only.
- [ ] No mutating tools anywhere; read-only invariant intact (grafana-mcp mutating tools explicitly excluded from the allowlist).
- [ ] **M-1 (blocking)**: grafana-mcp's Grafana token is Viewer + a startup assertion refuses a non-Viewer token.
- [ ] **M-10 (blocking)**: an OUTPUT secret/PII filter (JWT/`Bearer`/`AKIA*`/connection-strings + email/IP/CPF) redacts before the answer reaches the user.
- [ ] Log/trace queries capped (`limit ≤ 50` + line truncation); ≤3 metric-catalog skills injected per turn.
- [ ] RCA is grounded (raw signals + causal mechanism + a disproved counter-hypothesis + falsifiability) and time-anchored; output labeled "AI-assisted hypothesis — verify before action" until the eval gate (≥80% correct, <5% harmful on golden replays) passes.

## Out of scope

- New infrastructure (all target MCPs are already deployed).
- Mutating/execution tools or HITL (separate roadmap).
- Model-tier escalation (spec 38 / B-30) — complementary, tracked separately.
