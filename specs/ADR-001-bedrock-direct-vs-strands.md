# ADR-001 — Bedrock-direct vs. an agent framework (Strands)

**Status**: Accepted — the Bedrock-direct/no-framework decision stands; the **"Caminho A" (MCP-as-adapter, non-agentic) part is superseded by ADR-0008** (spec 37, agentic tool-calling via the Bedrock Converse API — still no framework).
**Date**: 2026-06-16
**Decision context**: AIgent-squad multi-agent orchestration
**Related**: `02-unify-agent-architecture`, `17-multi-agent-collaboration`, `18-rca-investigation-workflow`, steering `project.md` ("do not reintroduce LangGraph")

---

## Decision

**Keep orchestration on Bedrock-direct (classifier + GenericAgent +
investigation/synthesizer), without adopting the AWS Strands Agents SDK at this
time.**

---

## Context

Strands Agents (AWS open-source SDK, GA 1.0 in Jul/2025) was evaluated as an
alternative to the current orchestration. Strands is **model-driven**: the LLM
autonomously decides, in a loop, which tools to call — instead of the workflow
being hand-coded. 1.0 offers 4 multi-agent patterns (agents-as-tools, swarm,
graph, workflow) and integrates with Bedrock AgentCore (managed
runtime/memory/observability).

The project **has already migrated away from a framework before** (LangGraph
removed in favor of Bedrock-direct — recorded as a prohibition in steering). The
question is whether Strands justifies reversing that direction.

### Current state (what exists and is test-validated)

| Capability | Current implementation |
|-----------|------------------------|
| Routing | `classifier` (Bedrock call) → specialist |
| Specialists | `GenericAgent` + `AGENTS_DIR` (config-driven, `agent.yaml`) |
| Fan-out + synthesis | `investigation.py` + `synthesizer.py` |
| Tool-calling | Hand-written **read-only** adapters (`adapters.py`) |
| Resilience | `circuit_breaker.py`, deterministic sha256 cache, fail-open |
| Observability | OTel instrumented |
| Read-only is law | 4 layers: prompt, IAM deny, RBAC, refusal templates |

---

## Rationale (in order of strength)

1. **"Read-only is law" conflicts with the model-driven paradigm.** The core of
   Strands is the LLM autonomously deciding which tools to invoke in a loop. The
   project deliberately built the opposite: classifier-based routing, read-only
   adapters, 4 layers of write refusal. Adopting the autonomous tool-loop would
   force rebuilding those barriers *on top of* a framework whose purpose is to
   remove exactly that control.

2. **The "hard part" is already done and tested.** Circuit breaker, deterministic
   cache, fail-open DynamoDB, fan-out, synthesizer and OTel already exist with
   ~85% coverage. Strands' immediate gain (orchestration + tool loop) is exactly
   what is already coded and validated.

3. **The cost of leaving a framework was already paid once (LangGraph).**
   Re-coupling to a framework now risks repeating the same migration cycle. The
   direction recorded in steering is direct control over Bedrock.

---

## Accepted trade-offs

| Cost | Reality |
|------|---------|
| We maintain by hand orchestration Strands would give for free (classifier, fan-out, synthesizer) | Already written, tested and stable — marginal maintenance cost is low |
| We don't use ready-made patterns (swarm/graph) nor AgentCore (managed runtime/memory) | Not needed while agents are consultative read-only single/few-rounds |
| We stay "off" AWS's recommended path for agents | Zero lock-in and full alignment with the read-only posture compensate |

---

## When this decision would be wrong (signals to reopen)

- **Agents stop being consultative read-only** and start executing autonomous
  multi-step actions (chaining tools dynamically, multi-step planning). Then the
  hand-written classifier + investigation + synthesizer become dead weight, and
  Strands' tool-loop + swarm/graph start to **enable** what we don't have yet,
  instead of competing with what we already have.
- **Need for managed runtime/memory** (not wanting to operate it on EKS) →
  Bedrock AgentCore starts to make sense.
- **Hand-written orchestration grows to the point where maintenance exceeds** the
  cost of adopting a framework (e.g. many new coordination patterns per quarter).

---

## Alternatives considered and discarded

- **Strands Agents SDK (self-hosted on EKS)** — discarded now: the model-driven
  paradigm conflicts with read-only; it would rebuild write barriers on top of
  the framework.
- **Strands + Bedrock AgentCore (managed)** — discarded now: it solves
  runtime/memory we already cover (EKS + DynamoDB + pgvector); introduces
  coupling without proportional gain in the current consultative case.
- **Reintroduce LangGraph** — prohibited by steering (`project.md`).
