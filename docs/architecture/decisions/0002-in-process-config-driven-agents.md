# ADR-0002: Specialists are in-process, config-driven agents — not per-agent services

| Field | Value |
|---|---|
| **Status** | accepted |
| **Date** | 2026-06-14 (backfilled 2026-07-03) |
| **Deciders** | Carlos Felipe Gomes |
| **Related to** | specs 02 (unify architecture), 22 (capability manifest, supersedes 19); ADR-0001; `specs/ROADMAP.md` backlog "Distributed topology (code)" |

## Context

The original codebase ran 7 HTTP services (supervisor + 5 specialist servers +
MCP), with two divergent agent patterns coexisting (AUDIT A1–A3): four of five
`server.py`s reimplemented their own agent class, ignored `chat_history`, and
returned a different response contract. Each agent meant one more image,
pipeline, deployment and drift surface. Inter-agent latency analysis (spec 06
design) showed pod topology is irrelevant to speed: a Bedrock call costs
~2–8 s, HTTP between pods ~1–3 ms — parallelism of model calls is the only
real speed lever.

## Decision

An agent is **configuration, not code**: a directory (`agents/<name>/agent.yaml`
+ `prompt.md`) auto-discovered at startup by `AgentRegistry` and executed by a
single `GenericAgent` implementation with declarative datasource adapters
(boto3, kubernetes, http, athena, mcp). All specialists run **in-process inside
the supervisor** — one Docker image for the whole system, no per-agent ports or
services. Adding an agent requires zero code and zero rebuild.

## Alternatives considered

- **Per-agent HTTP services (status quo)** — independent scaling per agent;
  rejected: 13 always-on pods for a low-traffic ChatOps, N pipelines/images,
  proven contract drift, and no latency benefit (ms vs the model's seconds).
- **Distributed topology behind the same config** — the Helm chart still
  renders it, but the supervisor deliberately does not implement a
  `RemoteAgent` client; deferred with an explicit reopen trigger (real need
  for independent per-agent scaling/isolation).
- **One image per agent with shared base** — halves the drift but keeps the
  build/deploy multiplication; rejected as the worst of both.

## Consequences

- **Positive:** zero-code extensibility (6th agent proven via config only);
  one image to patch/scan; fan-out is an `asyncio.gather` over in-process
  objects; single response contract enforced by construction.
- **Negative / trade-offs:** no independent per-agent scaling or fault
  isolation (accepted — reopen trigger documented); the image carries all
  adapters (~irrelevant, they are light Python libs; only declared ones are
  instantiated).
- **To watch:** if one agent's datasource load starts starving the supervisor
  event loop, that is the reopen signal for the distributed topology.
