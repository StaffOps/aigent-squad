# Competitive Analysis — AI SRE / Incident-RCA Agents

**Last updated**: 2026-06-16
**Method**: reading READMEs, configs and structure of 6 open-source projects in
`example-sres/` + commercial products (Datadog Bits, incident.io, PagerDuty,
Azure SRE Agent) via public material. For most, based on READMEs + structure;
**HolmesGPT had a code deep dive** (reference peer). Aurora/OpenSRE: deep code
read still pending.

> Scope: positioning and product direction. For architecture decisions see
> `specs/ADR-001` and `specs/14-security-hardening/`.

---

## TL;DR

- The "AI SRE that investigates and does RCA" space is **crowded and maturing
  fast**. The closest/most mature to ours: **Aurora** (Arvo) and **OpenSRE** (Tracer).
- The axis where we are nearly alone: **read-only by default + defense-in-depth
  security from the start**. Most race toward autonomy (acting); we start with
  security discipline and enable execution later.
- **Read-only is the current posture, not permanent** (see `READ_ONLY_POLICY.md`).
  The advantage: we build the guardrail BEFORE acting; competitors that already
  act had to add a guardrail afterward (Aurora added NeMo + Sigma).

---

## Comparison matrix

| Project | Category | Stack | Autonomy (acts?) | Multi-agent | Guardrails / security | Maturity |
|---------|----------|-------|------------------|-------------|-----------------------|----------|
| **AIgent-squad** (us) | Consultative AI SRE / RCA | Python, **Bedrock-direct** | **Read-only today** (execution = future with HITL) | supervisor + classifier + fan-out + synthesizer | spec 14 (Bedrock Guardrails, fail-closed, multi-language) — **written, not impl** | pre-1.0, not deployed |
| **Aurora** (Arvo-AI) | Incident investigation/RCA | Python, Flask, Celery, **LangGraph**, Next.js | **Acts**: runs CLI in sandboxed pods, suggests PRs | LangGraph multi-agent, 30+ tools | **NeMo input rail (anti-injection) + 37 SigmaHQ rules + per-org allow/denylist** | Apache-2, active (Discord, demos) |
| **HolmesGPT** (Robusta/MS, **CNCF**) | AI SRE / RCA + 24/7 operator | Python, FastAPI, Pydantic, **litellm** (multi-LLM incl. Bedrock) | **Read-only by design, respects RBAC, "safe in prod"** (but has `ApprovalRequirement` for HITL and the operator can open PRs via GitHub) | single agentic loop + `max_steps`; YAML toolsets | param sanitization (`shlex.quote`), **anti-loop safeguards**, RBAC; no dedicated anti-injection guardrail | **CNCF sandbox**, very active, ~46 builtin toolsets |
| **OpenDerisk** (derisk-ai) | DeepResearch RCA | Python (derived from DB-GPT), `uv` monorepo | Investigates; **Code-Agent generates code** dynamically | **5 agents**: SRE, Code, Report, Vis, Data | not evident in the README | MIT, V0.2, OpenRCA dataset (microsoft) |
| **OpenSRE** (Tracer-Cloud) | Framework + **RL env / benchmark** for AI SRE | Python, `uv`, ruff/mypy | **Suggests and, optionally, executes remediation** | framework for you to assemble, 60+ tools | has SECURITY + trust center | Apache-2, **pre-alpha**, trending |
| **SmythOS / sre** | **Agent runtime/SDK** (not SRE-specific) | **TypeScript**, pnpm monorepo | generic agent platform | generic orchestration | "security built-in", resource abstractions | MIT, mature, SDK+CLI |
| **sre-agent** | AI SRE diagnostics | Python 3.13, **Anthropic-direct** | **Read-only** (diagnoses, suggests fix, posts to Slack) | single-agent + **MCP** | bandit in CI | pip-installable, simple/focused |
| **versus-incident** | Incident routing + AI detect | **Go**, Helm | Detects log anomaly; **routes** (doesn't remediate) | rules engine + AI agent | — | MIT, AI agent beta, on-call integrations |

---

## Where we are AHEAD

1. **Read-only by design + a pre-built safe execution path.** When we act, the
   defense (spec 14) already exists. Aurora/OpenSRE act and had to chase the
   guardrail afterward. Order matters: guardrail-before-acting > after.
2. **Multi-language anti-prompt-injection defense planned as its own layer**
   (Bedrock Guardrails, model-independent). Few have this for real (Aurora is
   the exception). Azure SRE Agent **only supports English** — a gap for us to
   exploit.
3. **Bedrock-direct, no framework** (ADR-001) — less lock-in and surface than
   Aurora (LangGraph) or those depending on their own runtime.
4. **Per-agent cost attribution** (spec 27, AIP + metric) — FinOps maturity that
   none of the examples highlights.

## Where we are BEHIND (honest gaps)

1. **Security is still spec, not code.** Aurora **already runs** NeMo + Sigma in
   production. Our security differentiator is only real once spec 14 is
   implemented. → **priority**.
2. **No benchmark/measurement of RCA quality.** OpenSRE has a *scored* synthetic
   suite (root-cause accuracy, required evidence, adversarial red herrings). We
   can't measure whether our RCA is good. → **biggest product gap**.
3. **Maturity/deploy.** Everyone has releases/stars/community; we haven't
   deployed yet.
4. **Evidence-chain visualization.** OpenDerisk renders the evidence chain (Vis
   protocol). We have the synthesizer, but lack the visualization.
5. **Tool/integration catalog.** Aurora 30+, OpenSRE 60+. We have boto3/k8s/
   http/athena/mcp — a good start, but a smaller catalog.

---

## Ideas worth "stealing" (prioritized)

| Source | Idea | Why for us | Fits in |
|--------|------|------------|---------|
| **HolmesGPT** | **Context-window management**: server-side filtering + spill large result to disk + `llm_summarize` transformer for tool output | Directly solves the pain we saw (MCP brought 165KB of events → input tokens). Cuts cost and avoids OOM | new spec / adapters + spec 27 |
| **HolmesGPT** | **`ApprovalRequirement`** per-tool (`needs_approval` + `reason` + `prefixes_to_save`) | Ready human-in-the-loop model for WHEN we execute — granular per tool/command | future execution + spec 14 |
| **HolmesGPT** | **`safeguards.prevent_overly_repeated_tool_call`** (cheap anti-loop) | Contains cost/loops without ML; complements our max_rounds | spec 17/18 + spec 14 (abuse) |
| **HolmesGPT** | **YAML toolsets** with `prerequisites`, `transformers`, `expose_remotely` (cross-cluster via MCP) | Richer than our adapters; `prerequisites` (checks `kubectl version` first) and paginated jq-query avoid overflow | adapters / agent.yaml |
| **HolmesGPT** | **litellm** as a multi-provider layer | We'd trade boto3-bedrock lock-in for a multi-LLM abstraction (OpenAI/Anthropic/Bedrock/Gemini) without rewriting | reevaluate vs ADR-001 |
| **OpenSRE** | **Scored** synthetic RCA suite (accuracy, evidence, red herrings) | Solves "how do I know the RCA is good?"; fits our ≥90% test gate | new spec / 18-rca |
| **Aurora** | **NeMo Guardrails** input rail + **SigmaHQ** rules for commands | Complements/alternative to Bedrock Guardrails; Sigma is gold for when we execute | spec 14 |
| **versus-incident** | **training / shadow / detect** modes | Introduce detection/action with NO risk (shadow = "would have alerted") | proactive agents roadmap / future execution |
| **OpenDerisk** | **Evidence-chain visualization** + per-role multi-agent (Report/Vis/Data) | Makes the RCA auditable/explainable to the operator | spec 18 / UI |
| **Aurora** | **Per-org command allow/denylist** | Governance prerequisite WHEN we execute | future execution + spec 14 |
| **sre-agent** | CLI setup wizard + onboarding simplicity | Reduces adoption friction | DX / docs |
| **OpenSRE / Aurora** | Dependency graph traversal in investigation | Richer correlation than blind fan-out | spec 17/18 |

---

## Positioning (how we sell ourselves)

> **"The read-only-by-default AI SRE — that investigates rigorously and, when it
> acts, will act with guardrails the others only added after they were already
> acting."**

- **We don't compete (today) on autonomy** — we compete on **verifiable trust**
  and **RCA rigor**.
- The market splits in two: "automate remediation" (Datadog/incident.io/
  PagerDuty/Azure/Aurora) and "framework/benchmark" (OpenSRE/SmythOS). We are
  **consultative-with-security-rigor**, with a door open to controlled execution.
- **When execution arrives** (roadmap, open): we inherit the pitch of those that
  act, BUT with the defense already mature and human-in-the-loop by design — not
  as a patch.

## Signals to revisit this document

- Implement spec 14 → update "gap #1" (security becomes a real strength).
- Adopt an RCA benchmark → update "gap #2".
- Decide to enable execution → revise the whole positioning (no longer "doesn't
  act") and extend the threat model (see spec 14 / READ_ONLY_POLICY).
- Re-analyze competitors roughly every quarter (the space evolves fast).

## Analysis backlog

- ✅ **HolmesGPT** (Robusta/MS, CNCF sandbox) — analyzed (code deep dive,
  2026-06-16). It's the most mature and most aligned peer (read-only by design,
  respects RBAC, multi-LLM via litellm incl. Bedrock). Key findings already
  incorporated into the matrix and the ideas table above: context-window
  management (spill-to-disk + llm_summarize), `ApprovalRequirement` (per-tool
  HITL), anti-loop `safeguards`, YAML toolsets with prerequisites/transformers.
  **Notable**: not even HolmesGPT has a dedicated anti-injection guardrail (only
  param sanitization) — reinforces that our spec 14 would be a real differentiator.
- **Deep code read** (not just README) of Aurora and OpenSRE — the two closest
  remaining — to extract concrete implementation patterns.

## HolmesGPT — deep dive (the reference peer)

Being CNCF, maintained by Robusta + Microsoft, and the most aligned (read-only),
it's worth highlighting what we learned reading the code (`holmes/core/`):

- **Stack**: FastAPI + Pydantic + **litellm** (multi-LLM abstraction — OpenAI/
  Anthropic/Azure/**Bedrock**/Gemini). `ToolCallingLLM` with `max_steps` is the
  agentic loop.
- **Read-only by design + RBAC** — same thesis as ours, but with a **ready HITL
  model** (`ApprovalRequirement`) for when they need action (e.g. operator mode
  opening PRs via GitHub). It's the path we described for our future execution —
  they already have the structure.
- **Context management is their differentiator** (and our current pain):
  `tool_context_window_limiter` spills large results to disk, the `llm_summarize`
  transformer summarizes tool output with a fast model, paginated jq-query
  (batches of 500) avoids overflow. Directly applicable to our MCP/adapters.
- **Security**: param sanitization (`shlex.quote`), anti-loop `safeguards`, RBAC
  — but **no** dedicated prompt-injection detector. The leader's gap = our
  opportunity (spec 14).
- **Operator mode**: runs 24/7, detects proactively, notifies Slack — it's the
  "proactive agents" of our roadmap, already mature. UX reference.

---

## Deep-dive: engineering patterns to adopt (2026-06-16)

Findings from **code reading** (HolmesGPT, Aurora, OpenSRE) that are concrete
improvements for us. Ordered by value.

### 🔴 High value — attack our biggest gaps

1. **Scored RCA benchmark — OpenSRE `tests/benchmarks/_framework/`** (gap #2).
   Not "a test" — it's a scientific RCA-quality evaluation framework:
   - **CloudOpsBench**: a corpus of **452 scenarios** (HF dataset), not committed
     to the repo — pulled at runtime; revision-pinned S3 mirror for Fargate runs.
   - `cost.py`: **per-model input/output cost accounting + hard-cap budget**
     (`CostBudgetExceeded` halts the run and publishes a partial report — no
     silent overrun). Matches our efficiency steering 100%.
   - `overfit.py`: **anti-overfitting guards** (per-system/category uniformity,
     held-out 80/20, A/A consistency of 2 seeds) — ensures a "win" isn't just
     luck concentrated in a cluster of cases.
   - `provenance.py` + `integrity.py`: each run records code SHA, config, env,
     model versions → reproducible and auditable.
   - **Why steal it**: today we don't measure whether the RCA is good. This is
     the method. Becomes a candidate for its **own spec** (e.g. `28-rca-benchmark`).

2. **Real layered anti-injection defense — Aurora `server/guardrails/`** (gap #1, feeds spec 14).
   - `input_rail.py`: **NeMo Guardrails as a pre-flight** BEFORE the agent plans —
     "compromised inputs never reach tool selection". Uses the structured
     `triggered_input_rail` variable (not refusal string-match — immune to
     model-specific wording). **Fail-closed**: any error blocks. It's exactly our
     spec 14 design, already implemented — a direct reference.
   - `sigma_loader.py`: **transpiles SigmaHQ rules → regex** to detect malicious
     commands (clear syslog, crypto mining, curl|wget exec /tmp, etc.). Curated
     subset (linux/process_creation, high/critical). **Our spec 14's L2**, ready
     for when we execute commands.
   - `test_sigma_canary.py`: a **canary** test ensuring the defense didn't regress.
   - **Why steal it**: validates spec 14 and gives a reference implementation.
     NeMo is an alternative/complement to Bedrock Guardrails (multi-provider via
     a LangChain factory).

### 🟠 Medium value — efficiency and robustness

3. **Context-window management — HolmesGPT `core/tools_utils/`** (feeds the efficiency steering).
   - `tool_context_window_limiter.py`: **spill large results to disk**; only a
     summary enters the context. `get_pct_token_count` sizes by % of the model's window.
   - `llm_summarize` transformer: summarizes tool output with a **fast model**
     before injecting (tiering applied to output, not just routing).
   - **paginated** jq-query (batches of 500) in the k8s toolset — avoids overflow at the source.
   - **Why steal it**: it's the direct solution to the pain we measured (MCP
     brought 165KB). Attacks cost at the root.

4. **Per-tool `ApprovalRequirement` — HolmesGPT `core/tools.py`** (future execution).
   `needs_approval` + `reason` + `prefixes_to_save` model (approval of a reusable
   bash command prefix). It's the granular human-in-the-loop we'll need.

5. **`safeguards.prevent_overly_repeated_tool_call` — HolmesGPT** (efficiency + anti-abuse).
   Blocks identical repeated tool calls — contains loops/cost without ML. Trivial to port.

### 🟢 Very interesting (relevant, not urgent)

6. **Declarative YAML toolsets with `prerequisites` — HolmesGPT**. A toolset
   declares what it needs (`kubectl version --client`) and only activates if the
   prerequisite passes. More robust than our adapters assuming the dependency
   exists. `expose_remotely: true` allows calling a cluster-local toolset via
   cross-cluster MCP.

7. **`litellm` as a multi-LLM layer — HolmesGPT**. One wrapper, N providers
   (OpenAI/Anthropic/Azure/Bedrock/Gemini) + prompt caching + content_filter
   finish_reason already handled. Reevaluate vs. our direct boto3-bedrock
   (ADR-001) — trade-off: less lock-in vs. one more dependency.

8. **24/7 operator mode — HolmesGPT**. Runs in the background, detects
   proactively, notifies Slack, opens PRs (GitHub integration). It's the
   "proactive agents" of our roadmap, mature — UX/architecture reference.

9. **training/shadow/detect modes — versus-incident**. Introduce
   detection/action without risk: `shadow` logs "would have alerted" without
   alerting. A safe rollout pattern for when we act.

### Candidate specs (derived from these findings)
- `28-rca-benchmark` — scored RCA-quality suite (from OpenSRE; high value).
- Reinforce `14-security-hardening` with NeMo input rail + Sigma rules (from Aurora).
- A **context budgeting** spec/feature across the adapters (from HolmesGPT;
  feeds the efficiency steering).
