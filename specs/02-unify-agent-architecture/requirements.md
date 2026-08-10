---
spec: 02-unify-agent-architecture
status: done
completed: null
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Unify Agent Architecture

**Spec**: `02-unify-agent-architecture`
**Severity**: 🟠 High
**Findings**: A1, A2, A3, H1 (see `../AUDIT.md`)

Converge all agents to a single pattern: the `Agent` base class (`src/core/agent_base.py`), implemented async, with tracing, validation, and actual use of `chat_history`. Today only `aws` follows this pattern; k8s/finops/devops/observability reimplement their own classes in the `server.py` files and ignore the corresponding `agent.py`.

## User Stories

WHEN the supervisor routes a query to any agent THEN the agent SHALL process via `process_request(input_text, user_id, session_id, chat_history, additional_params)` inherited from `Agent`.

WHEN any agent responds THEN the HTTP payload SHALL have the same contract `{role, content, timestamp, agent_id}`.

WHEN the supervisor sends `chat_history` to an agent THEN the agent SHALL incorporate that history into the prompt context (functional multi-turn).

WHEN a `server.py` instantiates the agent THEN it SHALL import the class from `agent.py` (not redefine logic inline).

## Acceptance Criteria

- [ ] All 5 `server.py` files import and use the class from `agent.py` (zero agent classes redefined in server).
- [ ] All 5 agents inherit from `Agent` and implement async `process_request`.
- [ ] Identical response contract across all 5 `/process` endpoints (`role, content, timestamp, agent_id`).
- [ ] `chat_history` is formatted and injected into the prompt in all 5 agents.
- [ ] Supervisor reads only `content` (removes the `get("content", get("response"))` fallback).
- [ ] `observability/agent.py` now exists with the `Agent` pattern (today only server exists).
- [ ] FinOps `agent.py` (Athena+Kubecost+RAG) is the version that runs; the server inline version is discarded.
- [ ] Tests: response contract + history usage in at least 1 agent.

## Out of scope

- Cache key and observability → spec 03.
- Authentication and non-root → spec 04.
- Separating `requirements.txt` per agent (H1) is desirable but optional here; if done, it must not break build.
