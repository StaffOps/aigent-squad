# Design: Security Hardening — Anti-Prompt-Injection Defense-in-Depth

## Architecture (defense-in-depth)

Each request crosses independent layers. A compromised layer does not compromise
the others (principle: no layer trusts the previous one).

```
                 untrusted input (query / adapter / mcp / history / skill)
                              │
                 ┌────────────▼─────────────┐
   Layer 2       │ Input pre-scan            │  normalize (unicode/base64/
   (pre-LLM)     │ + heuristics              │  homoglyph) → cheap reject
                 └────────────┬─────────────┘
                 ┌────────────▼─────────────┐
   Layer 3       │ Context isolation         │  <untrusted> blocks +
                 │ (prompt construction)     │  system reinforcement
                 └────────────┬─────────────┘
                 ┌────────────▼─────────────┐
   Layer 1       │ Bedrock Guardrail (INPUT) │  prompt-attack / denied topics /
   (primary)     │                           │  PII — model-based, multi-language
                 └────────────┬─────────────┘
                 ┌────────────▼─────────────┐
   Layer 5       │ Canary injection          │  tokens embedded in infra_data
                 └────────────┬─────────────┘
                       Bedrock InvokeModel
                 ┌────────────▼─────────────┐
   Layer 1       │ Bedrock Guardrail (OUTPUT)│  grounding / PII / denied content
                 └────────────┬─────────────┘
                 ┌────────────▼─────────────┐
   Layer 4       │ Output filter             │  PII/secret/canary leak scan
                 └────────────┬─────────────┘
                              ▼ response (or 403 fail-closed)

   Layer 6 (cross-cutting): rate limit + budget cap per user/session
   Audit log (cross-cutting): every detection/refusal
```

## STRIDE threat model

| STRIDE | Threat | Mitigation in this spec |
|--------|--------|-------------------------|
| **S**poofing | Request impersonates a legitimate user/service | Auth `X-Internal-Token` (spec 04) + per-identity rate limit (L6) |
| **T**ampering | Injection alters the agent's behavior | Guardrail (L1) + pre-scan (L2) + context isolation (L3) |
| **R**epudiation | Attack without a trace | Structured audit log of every detection/refusal |
| **I**nfo disclosure | Exfiltration of infra/PII via the response | Output filter (L4) + canary (L5) + Guardrail output (L1) |
| **D**oS / abuse | Token burn, expensive loops | Rate limit + budget cap (L6) + max rounds (existing) |
| **E**levation | Agent is led to "act" | **Read-only (current phase)** (4 layers) — blocks at the root TODAY; if execution is enabled, it becomes the dominant threat and requires human-in-the-loop |

> "E" (elevation) — the most severe threat in competitors that act — is
> neutralized by the read-only architecture, not by this spec. This spec focuses
> on T/I/D, which is where read-only does NOT help.

## Components (layers)

| # | Component | Where | Responsibility |
|---|-----------|-------|----------------|
| L1 | `GuardrailClient` (Bedrock) | wrapper in `bedrock.invoke` | Model-independent input+output evaluation |
| L2 | `InputScanner` | before building context | Normalization + cheap heuristics |
| L3 | Context isolation | `generic_agent` (exists, reinforce) | `<untrusted>` delimitation + system reinforcement |
| L4 | `OutputFilter` | after invoke | PII/secret/canary in the response |
| L5 | `CanaryGuard` | inject into `infra_data` + check the output | Exfiltration detection |
| L6 | `RateLimiter`/`BudgetGuard` | supervisor entrypoint | Anti-abuse (Redis) |

## Rationale (decisions)

### Decision 1: Bedrock Guardrails as the primary layer (not our own regex)

**Choice**: use AWS Bedrock Guardrails as the primary prompt-attack detector,
instead of building our own detection.

**Justification, in order of strength**:
1. **Model independence**: the Guardrail evaluates separately from the agent's
   invoke. An injection that fools the LLM does **not** fool the Guardrail
   (distinct evaluations). That is real defense-in-depth, not the same layer twice.
2. **Native multi-language**: Bedrock's prompt-attack detection covers multiple
   languages — meets the "any language" requirement without us training anything.
   Direct differentiator: Azure SRE Agent **only supports English**.
3. **Managed + evolving**: AWS updates the detectors; we don't become owners of a
   jailbreak classifier (which ages fast).
4. We are already on Bedrock (same IAM/network plane) — low integration cost.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Cost per evaluation (input+output) per request | Security is the product — the user accepted |
| Extra latency per request | Accepted; mitigable with pre-scan blocking junk first (L2) |
| Dependency on an AWS service | Aligned with the stack; fail-closed covers unavailability |

**When it would be wrong**: if the Guardrail has a false-positive rate high
enough to block legitimate use, or if an on-prem/multi-cloud requirement
prohibits an AWS dependency. Then: our own detector or OSS (e.g. Llama Guard) as L1.

**Discarded alternatives**:
- Regex/heuristics as the primary layer — fragile, bypassable, not multi-language
  (demoted to L2, cheap, complementary).
- Self-hosted Llama Guard — more operations, no clear gain vs. managed now.

### Decision 2: Fail-closed (security > availability)

**Choice**: if the Guardrail/security service does not respond, **refuse** the
query (403), don't bypass.

**Justification**:
1. The whole product sells itself as "secure and trustworthy". A bypass under
   failure destroys the guarantee — worse than being unavailable.
2. Read-only already limits the damage of a bypass, but exfiltration/manipulation
   would still occur. Not worth the risk.
3. **Competitive contrast**: competitors prioritize availability (an SRE tool
   that goes down during an incident is useless). We accept the tension and
   choose security — because we are NOT the critical remediation path (we are
   consultative); if we go down, the operator still has their tools.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Unavailability under guardrail failure | Accepted; we are consultative, not the executor |
| Tension with the requested "resilience" | Resolved in favor of security AT THIS POINT; resilience (circuit breaker, fail-open) remains for NON-security-critical dependencies (DynamoDB, cache) |

**When it would be wrong**: if the product evolves into a critical operations
path (unlikely given read-only).

### Decision 3: read-only is NOT this spec — it's a current-phase prerequisite

**Choice**: this spec does **not** add read-only enforcement; it depends on the
existing read-only (4 layers) as a given.

**Justification**: read-only is what makes the threat model tractable (eliminates
elevation/mutation). Mixing the two would dilute both. This spec assumes
read-only and attacks what's left (exfiltration/manipulation/cost).

**Reopen signal**: if read-only is ever relaxed (the agent starts to act), this
spec needs to be **rewritten** — the threat model changes completely (elevation
becomes the dominant threat again, human-in-the-loop becomes mandatory).

## Competitive positioning (justifies the design)

| Dimension | Datadog Bits / incident.io / PagerDuty / Azure SRE | AIgent-squad |
|-----------|---------------------------------------------------|--------------|
| Autonomy | Act (rollback/scale/restart) | **Read-only today** (execution is open future, with guardrails) |
| Mitigates injection→mutation via | Human-in-the-loop (crutch) | **Architecture** today (no mutation code path); HITL mandatory if/when executing |
| Injection blast radius | High (can execute) | **Low today** (read only) |
| Multi-language anti-injection | Limited (Azure: English only) | **Yes** (Bedrock Guardrails) |
| Posture under security failure | Availability-first | **Fail-closed** (security-first) |
| Pitch | "automates remediation" | **"read-only by default; when it acts, with guardrails the others didn't have from the start"** |

> Today we don't compete on autonomy — we compete on **verifiable trust**. When
> execution arrives, this spec is what lets us act *with* the guarantee the
> competitors only added later. It's the technical materialization of the pitch.

## Invariants

- No layer trusts the previous one (real defense-in-depth).
- Fail-closed on any security-component failure (L1, L2, L4).
- The audit log never records the malicious payload in clear text (avoids log
  injection / re-exposure).
- `agent_id`/`user_id`/`session_id` in every audit event (traceability).
- Read-only is the current-phase posture (this spec doesn't touch it, nor make it eternal).

## External dependencies

| Service | Purpose |
|---------|---------|
| AWS Bedrock Guardrails | Primary input/output detection (L1) |
| Redis | Rate limit + budget counters (L6) |
| OTel/audit sink | Structured detection log |

## Phases (not big-bang)

Implementation is incremental (see tasks.md). Order by value/risk:
L1 (Guardrail) + fail-closed first (biggest gain), then L4/L5 (exfil), then L2
(cost optimization), L6 (abuse), and finally the multi-language suite as a
regression gate.
