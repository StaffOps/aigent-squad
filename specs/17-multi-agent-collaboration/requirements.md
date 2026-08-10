---
spec: 17-multi-agent-collaboration
status: done-with-deferrals
completed: null
superseded_by: null
depends_on: []
deferred: ["T11 formal smoke"]
---

# Feature: Multi-Agent Collaboration

**Spec**: `17-multi-agent-collaboration`
**Severity**: 🟢 Feature (new capability, not a fix)
**Origin**: `../ANALYSIS.md` → "New behaviors" (agent-as-tools + parallel execution)
**Depends on**: `06-resilience-patterns` (async-first — without it the fan-out serializes), `09-otel-instrumentation` (trace to visualize the call graph)

Today the system is **hub-and-spoke**: the classifier picks **exactly 1** agent per query and cross-domain queries fall into `"unknown"`. This spec adds collaboration: the supervisor can invoke **N agents in parallel** when the query touches multiple domains and **synthesize** a single response; and an agent can request data from another (**agent-as-tools**) when it needs context outside its domain.

## User Stories

WHEN a user query touches multiple domains (e.g., "why did my AWS cost go up after the last k8s deploy?") THEN the classifier SHALL return an ordered list of relevant agents (1..N), not a single one.

WHEN the classifier selects N≥2 agents THEN the supervisor SHALL call them **in parallel** (not sequentially) and synthesize the responses into a single coherent answer.

WHEN N=1 THEN behavior SHALL be identical to the current flow (no synthesis overhead).

WHEN an agent needs data from another domain to answer THEN it SHALL be able to request that data from another agent via a controlled tool call (agent-as-tools), with a maximum depth of 1 hop.

WHEN synthesis is performed THEN the supervisor SHALL preserve attribution (which agent said what) and the read-only policy.

WHEN any agent in the fan-out fails or times out THEN the supervisor SHALL synthesize with the available responses and signal the degradation (not fail the entire query).

## Acceptance Criteria

- [ ] Classifier returns `agents: [{agent, confidence, reasoning}]` ordered by relevance (new contract, backward-compatible with `selected_agent`).
- [ ] Supervisor executes **concurrent** fan-out (`asyncio.gather`) for N≥2 agents; total time ≈ max(agent latencies), not the sum.
- [ ] Synthesis step: 1 Bedrock call that receives the N responses + the query and produces the final response with attribution.
- [ ] N=1 does not trigger synthesis (unchanged fast-path).
- [ ] Agent-as-tools: an agent can call **≤1** other agent; recursion/cycles blocked by `hop` header (max depth = 1).
- [ ] Fan-out tolerates partial failure: 1 agent down → response goes out with the remaining ones + degradation note.
- [ ] Hard limit: maximum parallel agents per query configurable (default 3) — cost protection.
- [ ] Single trace spans supervisor → N agents → synthesis (context propagation — depends on spec 09).
- [ ] Tests (author ≠ test-author, ≥90%): multi-agent classifier, parallel fan-out, synthesis, partial failure, cycle blocking, fast-path N=1.

## Out of scope

- Streaming of the synthesized response (future; `agent_base` already has `AsyncIterable`).
- Debate/negotiation between agents (multiple round-trips) — this spec does **1 round** of fan-out + synthesis.
- Agent-as-tools depth > 1 hop (explicitly forbidden due to cost/latency/cycles).
- Change in per-agent history isolation (spec 02 maintains it).
