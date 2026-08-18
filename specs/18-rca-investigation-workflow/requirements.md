---
spec: 18-rca-investigation-workflow
status: done-with-deferrals
completed: 2026-08-18
superseded_by: null
depends_on: []
deferred: ["T11 formal smoke"]
---

# Feature: RCA Investigation Workflow

**Spec**: `18-rca-investigation-workflow`
**Severity**: 🟢 Feature (the product differentiator)
**Origin**: expected user value (troubleshooting + RCA); steering `investigation-protocol.md`, skill `root-cause-analysis`
**Depends on**: `17-multi-agent-collaboration` (evidence fan-out), `09-otel-instrumentation` (trace), `19-config-driven-platform` (datasources via config)

Transforms the system from "answers 1 question" to "**investigates a problem**". Given a symptom (alert or report), the supervisor coordinates the agents in parallel to collect factual evidence, builds a **timeline**, **correlates signals**, and produces an **RCA with confidence level, evidence, and prevention** — instead of a single-agent response.

## User Stories

WHEN the user describes a symptom ("latency went up at 14h on service X") THEN the system SHALL initiate an investigation, not just route to 1 agent.

WHEN an investigation starts THEN the supervisor SHALL perform **parallel** evidence collection fan-out: observability (metrics/logs/traces), kubernetes (events/restarts/OOM), devops (recent deploys/MRs in the time window), aws (infrastructure health).

WHEN evidence is collected THEN the system SHALL build a **timeline** with timestamps (cause precedes effect) and correlate the signals.

WHEN ≥3 independent signals point to the same cause THEN the system SHALL declare an RCA with "high" confidence; with fewer, "medium/low" + what is missing to confirm.

WHEN an RCA is declared THEN it SHALL include: hypothesis, evidence (with strong/medium/weak hierarchy), timeline, and **prevention proposal** (alert/test/guardrail/runbook).

WHEN a signal **contradicts** the hypothesis THEN the system SHALL refine or discard the hypothesis (not ignore the counter-evidence).

WHEN the cause is obvious (fix < 30s, single domain) THEN the system SHALL answer directly, **without** opening an investigation (avoid overhead).

## Acceptance Criteria

- [ ] Distinct investigation endpoint/intent from the simple query flow (or flag `mode=investigate`).
- [ ] Evidence collection in **parallel** (reuses fan-out from spec 17), with a time window derived from the symptom.
- [ ] Structured `Evidence`: `{source_agent, signal_type, timestamp, strength (strong/medium/weak), summary}`.
- [ ] Timeline ordered by timestamp, with marking of events that are cause candidates (deploy, config change, restart).
- [ ] Correlation: rule "≥3 independent signals → high confidence" implemented and testable.
- [ ] RCA output: `{hypothesis, confidence, evidence[], timeline[], contradicting[], prevention[]}`.
- [ ] Counter-evidence is not silently discarded (`contradicting` field + effect on confidence).
- [ ] Fast-path: trivial symptom does not trigger fan-out (explicit decision threshold).
- [ ] Cost limit: number of agents and number of evidence queries per investigation configurable (via spec 19).
- [ ] Read-only preserved: investigation only **reads** (no remediation action executed — only proposed).
- [ ] Tests (test-author ≠ author, ≥90%): parallel collection, timeline construction, correlation rule (3 signals), counter-evidence handling, trivial fast-path.

## Out of scope

- **Automatic remediation** (executing the fix) — violates read-only; only proposes.
- Proactive anomaly detection (CronJob that opens an investigation on its own) — future.
- Incident memory / learning between investigations → spec `21-incident-memory-learning`.
- Phase 2 (advanced multi-hypothesis correlation, fault-tree) — see promotion triggers in `tasks.md`.
