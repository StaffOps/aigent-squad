# Design: Agentic tool-calling

## Architecture

Replace the Caminho-A single-shot flow with a **bounded agentic loop** using the Bedrock
**Converse API** (`toolConfig` / native tool-use). Same Bedrock-direct posture (no framework).

```
process_request(query):
  tools = build_tool_specs(datasources)     # generic: read-only allowlist -> Converse toolSpec[]
  messages = [ user(query) ]
  loop (bounded by MAX_STEPS + wall-clock budget):
      guardrail(INPUT, last_user/tool_content)          # spec 14, every turn
      resp = bedrock.converse(system, messages, toolConfig=tools)
      if resp.stopReason == "tool_use":
          for call in resp.toolUses:
              enforce_allowlist(call.name)               # fail-closed; only read-only tools
              guardrail(TOOL_INPUT, call.input)          # B3: block SSRF/injection/exfil in ARGS (pre-exec)
              result = execute_tool(call.name, call.input)   # via the datasource adapter (5s timeout, circuit breaker)
              result = guardrail(TOOL_OUTPUT, result)    # B3: redact secrets/PII in RESULT *before* context/stream
              messages += toolResult(call.id, result[:MAX_TOOL_RESULT_CHARS])  # redact happens BEFORE truncation
          continue
      else:                                              # final answer
          guardrail(OUTPUT, resp.text); return resp.text
  # budget exhausted -> finalize with a "partial answer / hit step limit" note
```

## Components

| Component | Responsibility | Change |
|-----------|----------------|--------|
| `bedrock.py` | LLM invocation | **Add** a `converse()` method (Converse API, `toolConfig`, `toolUse`/`toolResult`) alongside the existing `invoke()`. `invoke()` stays for non-tool paths (classifier, synthesis). |
| `adapters.py` | Datasource access | Each adapter gains `list_tool_specs()` (declare its read-only tools as Converse `toolSpec`) + `call_tool(name, args)` (execute one allowlisted tool). MCP adapter derives specs from `session.list_tools()` ∩ allowlist; boto3/http declare their configured read-only ops. Existing `_collect` retired for agentic agents. |
| `generic_agent.py` | Agent execution | Replace pre-collect+inject+single-call with the bounded loop above. |
| `agent_config.py` | Config | `datasources[].tools` = **read-only allowlist** (already exists). Add validation: reject known-mutating verbs. Optional per-datasource `max_tools_exposed`. |
| tool-spec builder | Generic mapping | One code path turns any datasource's allowlist into Converse `toolSpec[]`. New MCP = config only. |

## Rationale (decisões)

### Decisão 1: Bedrock Converse API native tool-use (not a framework, not invoke_model hand-rolling)

**Escolha**: use the Bedrock **Converse API** `toolConfig`/`toolUse` loop for agentic execution.

**Justificativa (ordem de força)**:
1. It is the **native, provider-supported** tool-use loop — no LangGraph/Strands (honors ADR-001
   "Bedrock-direct, no framework"; we supersede only the *non-agentic* part, not the framework stance).
2. `invoke_model` (current) has no tool-use; hand-rolling Anthropic tool-use JSON over `invoke_model`
   would reimplement what Converse gives natively + drift from the API.
3. Converse is model-agnostic within Bedrock → eases a future spec-28 provider move.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Multiple Bedrock round-trips per query | Bounded by MAX_STEPS; most queries = 1–2 tool calls. Cost/latency budgeted + observed. |
| New `converse()` path beside `invoke()` | Classifier/synthesis stay on `invoke()`; only agent execution uses `converse()`. |

**Quando estaria errado**: if a query needs >N tool round-trips routinely (latency unacceptable) —
then pre-fetch hints or a hybrid (seed the first tool result) would be reconsidered.

### Decisão 2: Generic, config-driven tool exposure (zero-code onboarding)

**Escolha**: one builder maps any datasource's read-only allowlist → Converse `toolSpec[]`;
MCP tool schemas come from the server's own `list_tools()` filtered by the allowlist.

**Justificativa**:
1. Hard user requirement: **adding a new MCP = config only, no new code** (extends ADR-0002).
2. MCP servers self-describe their tools (name + inputSchema) → we forward the allowlisted subset.
3. Keeps "an agent is configuration, not code" true for the *tool* surface, not just the datasource.

**Trade-offs**: the LLM sees only what the allowlist permits; a too-narrow allowlist limits the
agent (config problem, not code). Tool schemas fetched at connect (cached per `cache_ttl`).

### Decisão 3: Read-only stays the LAW — enforced by defense-in-depth (corrected by round-table)

**Escolha**: the 100% read-only invariant is enforced by independent layers — **but the layers
differ per datasource type** (round-table B1 correction: the pod's IRSA/IAM-deny does NOT extend
to an MCP server, which runs under its OWN ServiceAccount):

| Datasource | Read-only enforcement |
|-----------|------------------------|
| **boto3** (aws/finops/security) | app allowlist **+ pod IRSA IAM explicit-deny** on mutating actions |
| **http** (observability/devops) | app allowlist + read-only endpoints only |
| **MCP** (kube-mcp, vm-mcp, future) | app allowlist **+ the MCP server's OWN ServiceAccount RBAC** (must be `get/list/watch`-only) — this is **audited per server at onboarding and enforced in CI**, NOT assumed. IAM/RBAC of the squad pod are irrelevant to what the MCP server can do. |
| **all** | **positive tool registry** (B2): allowed tools are an explicit, reviewed allowlist (not a mutating-verb blocklist) + **Bedrock Guardrail** on every turn, **including tool arguments (pre-exec) and tool results (pre-context)** (B3). |

**Justificativa**:
1. Agentic makes the allowlist the *primary* boundary; for MCP the real backstop is the **server's
   SA RBAC**, so onboarding MUST prove the server is read-only (SA audit) — a blocklist of verbs is
   insufficient (a server could name a mutating tool anything).
2. Prompt-injection now steers tool **choice + args** → the guardrail must also see **arguments**
   (SSRF/exfil via a URL/path/selector arg) and **results** (secret redaction, injection canary).

**Trade-offs**:
| Custo | Realidade |
|-------|-----------|
| MCP onboarding requires an SA RBAC audit + CI gate | It is the actual security boundary for MCP; cheap vs. the risk. |
| Guardrail on args+results adds latency/cost per turn | Bounded; args are small; results capped at MAX_TOOL_RESULT_CHARS. |

**Quando estaria errado**: a server advertising a read-looking tool that mutates via a backend the
SA audit missed. Mitigation: positive registry + prefer `--read-only` servers + periodic re-audit.

### Decisão 5: MCP session pooling, circuit breaker, hard budgets (round-table B4/B5)

**Escolha**: per request, **one MCP connect+initialize per server, reused for N `call_tool`**
(pooling); per-server **circuit breaker** (open after 3 consecutive failures); **5s timeout per
`call_tool`**. Hard budgets as first-class config, checked **before each `converse()`**:
`MAX_TOOL_STEPS=5`, `MAX_LOOP_DURATION_MS=15000`, `MAX_LOOP_TOKENS=50000`, `MAX_TOOL_RESULT_CHARS=4000`.

**Justificativa**: MCP is now a hard dependency in the hot path; connect-per-call would multiply
latency and failure surface. Budgets cap the documented **1.5–3× cost/latency multiplier** of the loop.

### Decisão 6: `converse()` is a NEW Bedrock engine; schema normalizer; sequential multi-toolUse (B6/B7/B8)

- **B6**: `converse()` is not a thin wrapper — it needs its own retry loop, response parser,
  per-iteration token accounting (checked against budget), and guardrail hook. `invoke()` stays for
  classifier/synthesis.
- **B7**: a **schema normalizer** turns MCP `inputSchema` → Converse `toolSpec` (inline `$ref`/`$defs`,
  simplify unions, cap depth, map hyphens↔underscores bidirectionally, fallback when `inputSchema`
  missing). ~30–50% of raw MCP schemas need this — without it the system is non-functional.
- **B8**: multiple `toolUse` blocks per turn execute **sequentially (v1)** with **partial-failure
  assembly** (one error per failed tool, correct `toolUseId` correlation).

### Decisão 4: Bounded loop (steps + wall-clock) — fail-open on exhaustion

**Escolha**: hard cap `MAX_TOOL_STEPS` (e.g. 5) + total wall-clock budget; on exhaustion, finalize
with what's gathered + a visible "hit step limit" note.

**Justificativa**: prevents infinite tool loops and runaway Bedrock cost; aligns with ADR-0004
(fail-open availability).

### Decisão 7: Stream the loop's steps (transparency) — real streaming, not pseudo

**Escolha**: emit incremental SSE deltas over the OpenAI-compat bridge as the loop runs — model
reasoning (optionally Claude extended-thinking), each tool call (name + args), and a
**guardrail-scanned** summary of each tool result — so LibreChat renders the subagent thinking and
acting on the cluster **live**. Replaces the current pseudo-streaming (single final delta).

**Justificativa**: the value of "subagents" is visible reasoning + actions; the loop already has
the steps, so streaming them is publishing events, not new logic.

**Trade-offs**: tool results streamed to the user MUST pass the guardrail (B3 — redact secrets/PII);
extended-thinking costs extra tokens (flag; budgeted); LibreChat's collapsible-thinking rendering
depends on format (step text always shows).

**Quando estaria errado**: if step streaming leaks sensitive data despite the guardrail → gate
result-streaming behind B3 redaction, never stream raw tool output.

## Invariantes

- **100% read-only**: no mutating tool is ever exposed or callable. Enforced by a **positive tool
  registry** (explicit, reviewed allowlist per datasource — NOT a mutating-verb blocklist),
  CODEOWNERS-gated; per-datasource backstop (boto3 = pod IRSA IAM-deny; MCP = the server's own
  ServiceAccount RBAC, audited at onboarding + CI); guardrail on turns, **arguments, and results**.
- **Transparency**: the loop streams its steps (reasoning + tool call/args + guardrail-scanned
  result summary) so the user sees the subagent think + act live.
- **Zero-code onboarding**: a new MCP/datasource is config only.
- **Guardrail on every turn** (input + output).
- **Bounded**: every request terminates within MAX_TOOL_STEPS and the wall-clock budget.
- **Fail-closed** on allowlist; **fail-open** on tool/transport errors.

## Cost & latency

- Each tool round-trip = 1 Converse call. Budget: MAX_TOOL_STEPS caps calls; expected 1–2 for
  typical queries. Emit metrics: `tool_calls_per_request`, `loop_duration_ms`, `tokens_in/out`,
  `steps_exhausted_total`. Alert if p95 steps or cost regresses.

## Scale strategy (large clusters — thousands of pods/logs/traces)

Dumping full results into context does NOT scale (MBs won't fit ~200K tokens, and the loop
re-sends the growing history each turn = cost + latency). Three levers, in order of leverage:

1. **Filtered/aggregated tool calls (the agentic lever)** — the LLM narrows: filter by
   namespace/status/label, use count/aggregate tools, LogQL/TraceQL with `limit` + time-range.
   It fetches the small + relevant, never "list all 10,000 pods".
2. **Count-marker** — for "how many" / pattern questions, `_truncate_with_marker` reports the
   TRUE total ("N items total"); `COUNT_FRAMING_INSTRUCTION` tells the model to report N and
   treat the shown rows as a sample. Answers counts at ANY scale without ingesting every row.
3. **Sample cap** — `MAX_TOOL_RESULT_CHARS` (40K ≈ 10K tokens) bounds the SAMPLE the model
   reads; it is deliberately NOT the primary scaling lever. The count comes from the marker,
   specifics from filtered queries. `MAX_LOOP_TOKENS`/`MAX_LOOP_DURATION_MS` keep cost bounded.

Raising the cap further trades cost/latency for marginal sample size — prefer levers 1+2.

## Failure modes

| Mode | Handling |
|------|----------|
| Tool/server unreachable | inline `[mcp:x] error` to model; loop continues/finalizes (fail-open) |
| LLM asks for non-allowlisted tool | refuse (fail-closed), return an error toolResult to the model |
| Loop won't converge | MAX_STEPS cap → finalize with partial + note |
| Guardrail blocks a turn | return the guardrail refusal (unchanged) |
| MCP `list_tools` fails at connect | datasource contributes no tools (degraded, logged), other datasources still work |

## Dependências externas

| Serviço | Propósito |
|---------|-----------|
| AWS Bedrock Converse API | native tool-use loop |
| MCP servers (streamable-http) | tool catalogs via `list_tools()`; execution via `call_tool()` |
| Bedrock Guardrail `w11piaof9jp1` | per-turn safety (spec 14) |

## Supersede

Supersedes the **Caminho A** (MCP-as-adapter, non-agentic) part of **ADR-001**. Keeps
Bedrock-direct/no-framework. See ADR-0008. ADR-0002 (config-driven) and ADR-0003 (read-only)
are **preserved and extended**, not reversed.

## Post-homologation findings (2026-07-20)

Landed after the live homolog on devops-core:

- **G-1 (unknown model → auto-route):** the gateway `resolve_target` maps an unknown/`base`/`large`
  model id to auto-route (None) instead of HTTP 400; known `aigent-squad-<agent>` still forces. Lets
  OpenAI-style consumers (Grafana LLM app) that can't set an arbitrary model reach the squad.
- **G-2 (`Authorization: Bearer`):** gateway edge auth accepts a Bearer token (matches
  `INTERNAL_API_TOKEN` via `hmac.compare_digest`, or `GATEWAY_API_KEYS`) alongside the existing
  headers; the X-Internal-Token compare is now timing-safe (closed F-009/S1).
- **guardContent input-tagging (Tier-2, defense-in-depth):** `converse()` wraps only the latest user
  message in a Bedrock `guardContent` block so the server-side guardrail evaluates the genuine user
  turn, not the system prompt / tool schemas / history.

### RESOLVED 2026-07-21 — guardrail PROMPT_ATTACK false-positive on our own framing (G-6)

**Resolution:** fixed in two layers — (1) skip the per-stage app-level INPUT scan on assembled
framing + a single ingress guard (`agent_id=ingress`) on the genuine user question; (2) disable the
redundant Bedrock server-side converse guardrail (input is guarded at ingress; the app-level OUTPUT
guardrail + B3 tool-args/result cover the rest). Live: "quais namespaces existem no cluster?" → 200
"74 namespaces"; a blatant injection → 403. PROMPT_ATTACK was NOT disabled. The original analysis is
retained below for context.

The **app-level Tier-1 input guardrail** (`guardrail.apply(source="INPUT")`, fed by `_user_text` /
`_extract_converse_user_text` in `bedrock.py`) evaluates the **assembled per-stage prompt** — the
classifier's agent-catalog and the agent's instructions live inside the *user-role* messages — so the
squad's OWN framing (verbs like manage/delete/execute) trips PROMPT_ATTACK (content filter, MEDIUM).
Base/auto-route cluster queries 403 today.

**Evidence:** `apply-guardrail` on the bare user phrase `"quais namespaces existem no cluster?"` →
action **NONE** (passes); on a blatant injection → **BLOCKED** (the filter is correct on real input).
So only our framing false-positives. The guardContent (Tier-2) change does **not** fix this because the
FP lives in the Tier-1 app-level `apply_guardrail` on assembled text, not in the converse server-side call.

**Fix direction (security-approved; aligns with AWS "tag only user-provided content"):** guard the
**genuine end-user question once, at ingress**, and stop running PROMPT_ATTACK on our own assembled
framing. Do **NOT** disable PROMPT_ATTACK — the security review refuted that: read-only reads can still
exfiltrate tokens/PII via logs, and the output PII filter has gaps (bearer tokens, connection strings).
Tracked as G-6 in the BACKLOG.
