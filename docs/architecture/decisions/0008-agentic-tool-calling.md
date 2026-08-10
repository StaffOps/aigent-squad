# ADR-0008: Agentic tool-calling (LLM-driven) — supersedes ADR-001 "Caminho A" (Path A, the non-agentic adapter approach)

| Field | Value |
|---|---|
| **Status** | accepted (implemented + homologated live 2026-07-20; count-framing + auto-route streaming deferred — see spec 37) |
| **Date** | 2026-07-19 |
| **Deciders** | Carlos Felipe Gomes |
| **Related to** | spec 37; supersedes the "Caminho A" (MCP-as-adapter, non-agentic) part of `specs/ADR-001-bedrock-direct-vs-strands.md`; preserves & extends ADR-0002 (config-driven agents) and ADR-0003 (read-only posture); ADR-0004 (fail-closed/fail-open) |

## Context

ADR-001 chose "Bedrock-direct, no framework" and, for tools, **Caminho A**: MCP/datasources are
*adapters* that the runtime pre-calls with fixed arguments, injecting the result into `<infra_data>`
for a single model call — the model never selects tools. Homologation (2026-07-19) exposed the
cost of this: "which pods in the monitoring ns?" answered "monitoring does not exist" while the namespace
is Active with 263 pods. The adapter could not target `pods_list_in_namespace(namespace="monitoring")`,
and the generic `namespaces_list` output was truncated (4000 chars) before the 57th of 72 namespaces.
The specialists are branded *subagents* yet cannot reason about which tool to call — a contradiction.

## Decision

Adopt **agentic tool-calling** for all agents: the LLM selects tools and arguments via the Bedrock
**Converse API** (`toolConfig`/`toolUse`) in a **bounded** loop. Tool schemas are built **generically**
from each datasource's declared **read-only allowlist** (MCP servers self-describe via `list_tools()`);
onboarding a new MCP/datasource is **config only, zero code** (extends ADR-0002).

This supersedes **only** the *non-agentic* "Caminho A" of ADR-001. It **keeps** Bedrock-direct /
no-framework (Converse is the provider-native loop). It **preserves** ADR-0003 read-only, whose
enforcement now rests on **defense-in-depth**: (1) a 100%-read-only app allowlist (the primary
boundary; mutating verbs rejected by validation), (2) IAM explicit-deny, (3) K8s RBAC get/list/watch,
(4) the Bedrock Guardrail on every turn, **including tool arguments (pre-exec) and results
(pre-context)**. Round-table (B1) correction: for **MCP** the backstop is the **MCP server's own
ServiceAccount RBAC** (get/list/watch), **audited per server at onboarding + CI** — the squad pod's
IRSA/IAM-deny does NOT extend to an MCP server. The mutating-verb blocklist is replaced by a
**positive, reviewed tool registry** (B2). A mis-classified tool call is caught by the SA-RBAC audit
+ registry, not assumed away.

## Alternatives considered

- **Keep Caminho A** — rejected: proven to produce false negatives, cannot target queries, and is
  not "agentic" despite the naming.
- **Bigger truncation cap only** — rejected: band-aid; bloats prompts/cost and still can't target.
- **Static per-query arg injection (`inject_query_as`)** — rejected as the primary design: brittle,
  only covers anticipated patterns; not general.
- **An agent framework (LangGraph/Strands/litellm)** — rejected: Converse gives native tool-use;
  ADR-001's no-framework stance stands.
- **Agentic with full tool catalogs (incl. mutating)** — rejected: violates read-only; mutating-tool
  servers (grafana-mcp/kubectl-mcp) stay out until their allowlists are curated 100% read-only.

## Consequences

**Positive**: accurate targeted answers; true subagents; generic config-driven onboarding of any
(incl. future) MCP; stronger, explicit read-only enforcement (4 layers).

**Negative / costs**: multiple Bedrock round-trips per query (bounded; budgeted + observed); allowlist
curation becomes security-critical (validated in CI); a new `converse()` path beside `invoke()`.

## When this decision would be wrong (reopen signals)

- Typical queries routinely exceed MAX_TOOL_STEPS → latency/cost unacceptable → revisit pre-seeding/hybrid.
- A tool advertised as read-only is found to mutate server-side → tighten server posture (prefer
  `--read-only`) / allowlist policy; IAM-deny + RBAC remain the backstop.
- A future decision to enable execution (ADR-0003 gate) → this ADR's read-only invariant is revisited
  with human-in-the-loop + a rewritten threat model.
