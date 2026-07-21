# Tasks: Agentic tool-calling

Legenda: `[ ]` pendente · `[~]` parcial · `[x]` done. Cada task de código segue o pipeline
`dev` (implementa) → `dev` (testa, independente) → `code-review` → gate cobertura ≥90%.

> **Status 2026-07-20 (done-with-deferrals):** Phases 1–4 implemented, each via an
> independent `dev`→`dev`(tests)→`code-review` pipeline, + **homologated LIVE** on
> devops-core (agentic tool-calling + streaming steps + read-only + guardrail 3-tier).
> Round-table B1–B8 fixes landed. Deferred (BACKLOG Deferred register + Grafana section):
> count-framing (headline count), auto-route streaming, MCP SA-RBAC CI gate, G-1..G-5, R1–R7.

## Phase 0 — Spec & validation (spec-first)
- [ ] T1: Write requirements/design/tasks (this spec) — DONE on merge of this dir.
- [ ] T2: Write ADR-0008 superseding ADR-001 "Caminho A"; mark ADR-001 superseded_by 0008.
- [ ] T3: **Round-table** (security / sre / dev) to refute the spec + ADR before code.
- [ ] T4: Incorporate round-table refutations.

## Phase 1 — Bedrock Converse tool-use core
- [ ] T5: `bedrock.py` — add `converse(system, messages, tool_config, ...)` (Converse API,
      `toolConfig`, returns stopReason + toolUse blocks). Keep `invoke()` for classifier/synthesis.
- [ ] T6: Tests for `converse()`: tool_use stopReason parsing, toolResult round-trip, no-tool path,
      error/timeout mapping. (independent author)
- [ ] T7: Guardrail integration on every converse turn (input + output) — reuse spec 14 path.

## Phase 2 — Generic tool exposure (config-driven)
- [ ] T8: tool-spec builder — map a datasource's read-only allowlist → Converse `toolSpec[]`.
      MCP: `list_tools()` ∩ allowlist (forward name + inputSchema); boto3/http: declared read-only ops.
- [ ] T9: `adapters.py` — `list_tool_specs()` + `call_tool(name, args)` per adapter (MCP first);
      enforce allowlist in `call_tool` (fail-closed). Cache tool specs per `cache_ttl`.
- [ ] T10: `agent_config.py` — validation rejecting known-mutating verbs in any allowlist
      (create/update/delete/patch/apply/scale/restart/exec/run/cordon/drain/install/uninstall/...);
      CI test proving a mutating allowlist fails validation.

## Phase 3 — Agentic loop in GenericAgent
- [ ] T11: `generic_agent.py` — replace pre-collect+inject+single-call with the bounded loop
      (MAX_TOOL_STEPS + wall-clock budget; fail-open on tool error; finalize on exhaustion).
- [ ] T12: Preserve conversation history, skills block, and the prompt-injection framing for
      tool results (treat tool output as DATA).
- [ ] T13: Observability — metrics `tool_calls_per_request`, `loop_duration_ms`, `tokens_in/out`,
      `steps_exhausted_total`; spans per tool call.
- [ ] T14: Tests: multi-step loop, allowlist refusal, fail-open on tool error, step-cap finalize,
      guardrail-block turn. (independent author, ≥90%)

## Phase 4 — Rollout (kube-mcp / vm-mcp first — already 100% read-only)
- [ ] T15: `code-review` of the full change.
- [ ] T16: Build + push multi-arch image; deploy to devops-core homolog.
- [ ] T17: **Homologate the regression**: "pods in ns monitoring" returns the real count (263);
      cross-agent smoke (aws/observability read-only queries) via the loop.
- [ ] T18: Docs: update MCP_INTEGRATION.md (agentic, not Caminho A), read-only-policy, CHANGES.

## Phase 2.5 — Round-table blocking fixes (2026-07-19; land with the phases above)
- [ ] B1: MCP onboarding checklist + CI gate auditing each MCP server's ServiceAccount RBAC is
      read-only (get/list/watch). Document: the squad pod's IRSA/IAM-deny does NOT cover MCP servers.
- [ ] B2: Positive tool registry (URL + SA proof + audited tool list + last-review date) + CODEOWNERS
      gate on agent config. Not a mutating-verb blocklist.
- [ ] B3: Guardrail on tool ARGUMENTS (pre-exec: SSRF/injection/exfil) + tool RESULTS (pre-context:
      secret/PII redaction, injection-canary scan).
- [ ] B4: Hard budgets as config (MAX_TOOL_STEPS, MAX_LOOP_DURATION_MS=15000, MAX_LOOP_TOKENS=50000,
      MAX_TOOL_RESULT_CHARS=4000), checked before each converse(); document 1.5–3× cost multiplier.
- [ ] B5: MCP session pooling (1 connect+init per request/server, reuse N calls) + per-server circuit
      breaker (open after 3 failures) + 5s per-call timeout.
- [ ] B6: converse() as a full engine (own retry loop, response parser, per-iteration token accounting
      + budget check, guardrail hook).
- [ ] B7: schema normalizer (inline $ref/$defs, simplify unions, cap depth, hyphen↔underscore,
      fallback for missing inputSchema).
- [ ] B8: multi-toolUse sequential (v1) + partial-failure assembly (error per tool, toolUseId
      correlation); ConverseMockBuilder fixture; loop state-transition diagram before code.

## Phase 3.5 — Streaming & transparency
- [ ] S1: Real streaming — replace pseudo-streaming with incremental SSE deltas over the OpenAI-compat bridge.
- [ ] S2: Loop emits steps as deltas: reasoning, each tool call (name+args), guardrail-scanned result summary.
- [ ] S3: (optional/flag) enable Claude extended-thinking; stream reasoning.
- [ ] S4: Result streaming gated by B3 redaction (never stream raw tool output).
- [ ] S5: Verify in LibreChat: the subagent's think+act steps render live.

## Recommended (round-table, non-blocking — target phase noted)
- [ ] R1 (P4): per-agent feature flag for canary + instant rollback; keep old _collect behind a flag for 1 release.
- [ ] R2 (P4): staggered MCP enablement (kube-mcp first, 1-week soak, then vm-mcp).
- [ ] R3 (P4): rollback criteria (p95 >2× OR steps-exhausted >10% OR error >5% → revert) + VMRules alerts.
- [ ] R4 (P2): pre-validate LLM args against inputSchema before call_tool; response-size cap per call.
- [ ] R5 (P1): scope K8s RBAC to specific namespaces (not cluster-wide get *.*) where feasible.
- [ ] R6 (P3): step-limit → clearly degraded response (partial: true + unfulfilled tool list).
- [ ] R7: correctness SLI <5% requests hitting step limit; per-step spans + finalize_reason + cost-per-request.

## Deferred (explicit, not in this spec)
- D1: Wire grafana-mcp / kubectl-mcp — requires curating their allowlists to 100% read-only first
      (they expose mutating/destructive tools). Blocked on that curation.
- D2: Enabling execution / mutating tools (ADR-0003 gate — human-in-the-loop, threat-model rewrite).

## Promotion triggers (reopen for a Phase-5)
- Routine queries need > MAX_TOOL_STEPS round-trips → revisit pre-seeding/hybrid.
- A read-only-looking tool found to mutate server-side → tighten server posture / allowlist policy.

## Post-homolog follow-ups (2026-07-20)

- [x] G-1 — gateway auto-routes unknown/`base`/`large` models (no 400). Live-validated.
- [x] G-2 — gateway accepts `Authorization: Bearer` (+ timing-safe X-Internal-Token compare). Live-validated.
- [x] guardContent input-tagging (Tier-2 converse) — defense-in-depth.
- [x] **G-6** — guardrail PROMPT_ATTACK false-positive on the squad's OWN framing. FIXED (2026-07-21):
      (1) skip per-stage app-level INPUT scan on assembled framing + single ingress guard on the
      genuine user question; (2) disable the redundant server-side converse guardrail (input at
      ingress; app-level OUTPUT + B3 cover the rest). Live: benign → 200 "74 namespaces"; injection → 403.
- [x] G-4 — auto-route streaming (classify → stream the agentic agent's steps). Live-validated.
- [x] G-5 / G-3 key — per-consumer scope via `GATEWAY_KEY_AGENT_MAP`. Live-validated (key → observability).
- [x] G-3 — stable prod endpoint confirmed (HTTPRoute + in-cluster DNS); per-consumer key mechanism ready.
- [x] MCP SA-RBAC audit gate — `scripts/mcp_rbac_audit.py` + Makefile + REQUIRED doc. Live: mcp-kube = 26 read-only rules, PASS.
