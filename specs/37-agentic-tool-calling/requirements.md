---
spec: 37-agentic-tool-calling
status: done
completed: 2026-07-20
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Agentic tool-calling (LLM-driven tools across all agents)

**Spec**: `37-agentic-tool-calling`
**Severity**: 🔴 Architectural (redesigns the agent execution core; supersedes ADR-001 "Caminho A")
**Origin**: homologation 2026-07-19. Query "quais pods no ns monitoring?" returned "monitoring
não existe" while the namespace is Active with 263 pods. Root cause: the non-agentic
"Caminho A" adapter pre-calls a fixed tool set with no query-specific args and injects the
(4000-char-truncated) result — it cannot target `pods_list_in_namespace(namespace="monitoring")`,
and the namespaces list was truncated before the 57th entry. The specialists are called
*subagents* but do not reason about which tool to call — they are template injectors.

## Problem

The `GenericAgent` (Caminho A of ADR-001) executes every request as: pre-collect all
datasource adapters with static/empty arguments → inject results into `<infra_data>` →
single Bedrock `invoke_model` call. Consequences:

- **No targeting**: the model cannot ask "pods of namespace X" — adapters run with fixed args.
- **Truncation blindness**: adapter output is capped at 4000 chars; large lists (72 ns, 263 pods)
  are silently cut, so the model reasons over a partial view and asserts false negatives.
- **Not really agents**: a "subagent" that cannot choose a tool + arguments is not agentic.

## User Stories

WHEN a user asks a specialist a question that maps to a specific tool call (e.g. "pods in
namespace monitoring") THEN the agent SHALL let the LLM select the tool and its arguments
(e.g. `pods_list_in_namespace(namespace="monitoring")`) and answer from the real result.

WHEN the LLM needs more than one call to answer (e.g. list namespaces, then list pods in one)
THEN the agent SHALL run a bounded multi-step tool-use loop until the model produces a final
answer or a step/latency budget is exhausted.

WHEN an operator onboards a NEW MCP server (or any datasource) THEN it SHALL require **config
only** (URL + read-only tool allowlist) with **zero new code** — the agentic loop discovers the
server's tools dynamically and exposes only the allowlisted, read-only ones to the LLM.

WHEN the LLM attempts to call a tool that is NOT in the datasource's allowlist THEN the system
SHALL refuse the call (fail-closed) and never execute it.

WHEN the LLM attempts any mutating action THEN it SHALL be impossible: the allowlist exposes
only read-only tools AND IAM explicit-deny + K8s RBAC (get/list/watch) reject mutation at the
infrastructure layer even if a tool were mis-classified (defense-in-depth, ADR-0003).

WHEN a tool call fails, times out, or the server is unreachable THEN the loop SHALL degrade
gracefully (surface the error to the model, continue or finalize) and never crash the request
(fail-open availability, ADR-0004).

WHEN the input or output contains disallowed content THEN the Bedrock Guardrail (spec 14) SHALL
still evaluate every model turn (unchanged safety net).

## Acceptance Criteria

- [ ] The LLM drives tool selection + arguments (Bedrock Converse API `toolConfig`) for every agent.
- [ ] Tool schemas are built **generically** from each datasource's declared read-only surface
      (MCP: `list_tools()` ∩ allowlist; boto3/http: configured read-only ops) — no per-server code.
- [ ] Adding a new MCP datasource (URL + allowlist) exposes its allowlisted tools with **no code change / no rebuild**.
- [ ] **Invariant: 100% read-only.** Only allowlisted read-only tools are ever callable; a
      mutating tool name in an allowlist is a config error caught by validation, not at runtime.
- [ ] Bounded loop: hard caps on tool-call steps per request and total wall-clock; exceeding
      the cap finalizes with what's known (no infinite loops, no runaway Bedrock cost).
- [ ] Fail-open: unreachable/erroring tool → inline error to the model, request still answers.
- [ ] Guardrail evaluated on every model turn (input + output), unchanged.
- [ ] The `monitoring` regression is fixed: "pods in ns monitoring" returns the real count.
- [ ] Cost/latency budget documented + observable (tokens, tool-call count, loop duration per request).
- [ ] `make specs-status` green; ADR-001 "Caminho A" marked superseded by the new ADR.

- [ ] **Streaming/transparency**: the agent streams its steps live (reasoning + each tool call/args
      + a guardrail-scanned result summary) over the OpenAI-compat SSE, so LibreChat shows the
      subagent thinking + acting on the cluster in real time (replaces pseudo-streaming).
- [ ] **Round-table (2026-07-19) blocking fixes** reflected: MCP ServiceAccount-RBAC audit gate (B1);
      positive tool registry + CODEOWNERS (B2); guardrail on tool args + results (B3); hard budgets
      MAX_LOOP_DURATION_MS/TOKENS/RESULT_CHARS (B4); MCP session pooling + circuit breaker + 5s timeout
      (B5); `converse()` as a full engine (B6); MCP→Converse schema normalizer (B7); sequential
      multi-toolUse + partial-failure assembly (B8).

### Streaming & transparency (user story)

WHEN a user asks a question via LibreChat THEN the agent SHALL stream its steps live — model
reasoning (optionally Claude extended-thinking), each tool call with arguments, and a
guardrail-scanned summary of each result — so the user watches the subagent think and act on the
cluster in real time, instead of a single final block. Streamed results SHALL pass the same
redaction/guardrail as B3 (never stream raw tool output).

## Fora de escopo

- **Enabling execution / mutating tools** — stays read-only (ADR-0003). This spec makes the
  agent *choose read-only tools*, not *act*.
- **Wiring grafana-mcp / kubectl-mcp** — deferred until their allowlists are curated to 100%
  read-only (they expose mutating/destructive tools). Agentic + full catalog = out of scope here.
- **LLM-provider abstraction** (spec 28) — stays Bedrock-direct (Converse API, no framework).
- **Removing the SSE transport** (spec 37 assumes streamable-http default from the prior change).
