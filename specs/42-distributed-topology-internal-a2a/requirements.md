---
spec: 42-distributed-topology-internal-a2a
status: not-started
completed: null
superseded_by: null
depends_on: ["37-agentic-tool-calling", "14-security-hardening"]
deferred: []
---

# Feature: Distributed topology — internal A2A between our own services

**Spec**: `42-distributed-topology-internal-a2a`
**Severity**: 🔴 Architectural (redefines the deployment boundary; affects every
invariant that currently relies on in-process co-location)
**Origin**: dormant entry in `specs/BACKLOG.md:152+` (evaluated 2026-08-09, kept
dormant). This spec *documents the design* at `status: not-started` — it is
**explicitly NOT a decision to build**.

## Scope — INTERNAL A2A only

Both endpoints belong to us. The supervisor and its six specialists — today a
single process (`SupervisorAgent` + `GenericAgent` instances sharing a Python
runtime) — would become independently deployable services speaking the A2A
protocol to each other, within our own network perimeter.

This is **not** third-party federation. We are not accepting Tasks from agents
we do not control, nor trusting Artifacts from unknown origins.

### What this scoping dissolves from the dormant entry's objections

**Objection 1 (security)**: the dormant entry raises *untrusted-artifact* and
*confused-deputy* concerns (a remote agent borrowing our IRSA/cluster read
access). Internal A2A **materially weakens** both:

- *Untrusted artifacts*: all specialist processes are built from our codebase,
  deployed by our GitOps, signing keys we hold. The trust decision is "do I
  trust my own build?" not "do I trust an opaque third-party response."
- *Confused deputy*: each specialist keeps its own IRSA role (already scoped
  per-agent in `agent.yaml → required_env`); the supervisor never proxies its
  own credentials — the hop is an authenticated RPC between our services, not
  a delegation of identity.

**What it does NOT dissolve** — and what becomes the **dominant cost**:

**Objection 2 (operational readiness)**: fan-out today is a function call. One
failure domain, local latency, one token-budget bucket. Distributing it means:
N services × N SLOs × N deploys × partial-fan-out failure handling × cross-agent
tracing × network-hop latency. None of this is security-gated; it is an ops
maturity gate. This becomes the primary obstacle, NOT the protocol itself.

### Dormant trigger status (2026-08-11)

The dormant entry defines three conditions, **all** of which must hold
simultaneously before the work is approved:

| # | Condition | Status today |
|---|-----------|--------------|
| 1 | A real user asks for cross-org or cross-framework agent collaboration, naming the external system | **Not met** — no request exists |
| 2 | MCP-as-tool proven insufficient with specific technical reason | **Not met** — MCP-as-tool is working |
| 3 | CI green 30 consecutive days | **~1 day old** (CI stabilized ~2026-08-10) |

Writing this spec is preparedness, not commitment. The trigger gates
implementation.

## Problem

The single-process architecture creates coupling that constrains future
evolution:

- **Scaling**: all six specialists scale together, whether the load is AWS-heavy
  or observability-heavy. A single noisy specialist's memory/CPU usage affects
  all others.
- **Fault isolation**: an unrecoverable panic in one specialist takes down all
  six. The circuit-breaker in `agentic_loop.py` handles transient tool failures,
  not interpreter-level crashes.
- **Independent release**: a prompt.md change for the finops agent requires
  re-deploying the supervisor + all 5 other agents.
- **Technology heterogeneity** (future): a Python-only process cannot host a Go
  or Rust specialist without FFI gymnastics.

These are **not urgent today** — the current architecture is simple, cheap, and
correct for our scale. This spec documents the path so that when (if) the
trigger fires, the design is not improvised under pressure.

## User Stories

WHEN the trigger (all 3 conditions in `specs/BACKLOG.md`) fires AND the team
decides to proceed THEN the supervisor SHALL be able to route a request to a
specialist running as an independent service via A2A, without modifying the
public-facing gateway contract.

WHEN a specialist process is unreachable or times out THEN the supervisor SHALL
degrade gracefully: synthesize from available responses and report which agents
failed (extending the current `agents_failed` behavior in `_fan_out`).

WHEN the supervisor dispatches to N specialists over the network THEN the
per-session token budget (`budget_tracker`) SHALL account for tokens consumed by
each remote specialist, so that the cumulative cap holds as if they ran
in-process.

WHEN a specialist receives a Task from the supervisor THEN it SHALL validate the
supervisor's identity (mutual auth) and apply the same `InputScanner` +
Guardrail + `OutputFilter` pipeline it runs today, losing zero security layers.

WHEN agent.yaml is enriched with A2A Agent Card fields THEN those fields SHALL
be **additive only** — `capabilities` (list[str]) and `routing_keywords` (flat
list) MUST NOT be renamed, nested, or restructured; `src/core/classifier.py:222`
reads `config.routing_keywords` directly.

## Acceptance Criteria

- [ ] AC-1: Supervisor can dispatch a request to a specialist running in a
  separate process and receive a structured response equivalent to the current
  in-process `AgentResponse`.
- [ ] AC-2: A specialist crash does not take down the supervisor or any other
  specialist.
- [ ] AC-3: Token budget accounting works across the network hop — cumulative
  `session_token_budget` honoured.
- [ ] AC-4: Distributed tracing propagates the same `trace_id` from gateway →
  supervisor → specialist, visible in the OTel collector.
- [ ] AC-5: Agent Card generation from `agent.yaml` is purely additive — existing
  fields unchanged, keyword routing unbroken (verified by
  `test_classifier.py` suite passing unmodified).
- [ ] AC-6: No specialist accepts a Task without authenticating the caller.
- [ ] AC-7: Read-only invariant enforced identically in distributed mode — a
  specialist never proxies a write on behalf of the supervisor.
- [ ] AC-8: Synthesis degrades gracefully when 1 of N specialists is unreachable
  (same contract as current `_fan_out` → `synthesizer.synthesize` with
  `failed` list).

## Out of scope

- Third-party / cross-org federation (accepting Tasks from agents we do not
  control). This spec is strictly internal-internal.
- Changes to the public gateway API contract (POST /query, /v1/*).
- Multi-cluster deployment — all services assumed same cluster/VPC initially.
- Async push notifications (A2A feature) — all internal calls are
  request/response.
- Implementation or building — this spec is `status: not-started`, a design document.
