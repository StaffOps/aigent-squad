---
spec: 14-security-hardening
status: done
completed: 2026-07-03
superseded_by: null
depends_on: ["04-harden-security"]
deferred: []
---

# Feature: Security Hardening — Anti-Prompt-Injection Defense-in-Depth

**Spec**: `14-security-hardening`
**Severity**: 🔴 Critical (product positioning, not just a feature)
**Depends on**: `04-harden-security` (auth, non-root, basic delimitation — done)
**Related**: `ADR-001` (read-only), `docs/READ_ONLY_POLICY.md`, ROADMAP spec 14

---

## Thesis (why this is the product, not a detail)

AIgent-squad competes in the same category as Datadog Bits AI SRE, incident.io,
PagerDuty AI SRE, and Azure SRE Agent. **The deliberate difference (today)**:
those products **act** (rollback, scale, restart — with human-in-the-loop as a
safety crutch). AIgent-squad is **read-only in the current phase** — it doesn't
execute today.

> **Read-only is not permanent.** Executing actions is an open future possibility
> on the roadmap (not ruled out). But the security posture DEPENDS on which mode
> is active, and this spec is a **blocking prerequisite** to enable execution:
> - **While read-only** (today): the worst case of an injection is exfiltration/
>   manipulation/cost — never mutation. This spec hardens that surface.
> - **If/when executing** (future): STRIDE's "Elevation" becomes the dominant
>   threat again; an injection could trigger a destructive action. So execution
>   is conditional on (a) this spec implemented AND (b) **mandatory
>   human-in-the-loop** for any mutating action.

This inverts the relationship with prompt injection:
- In an agent that **acts**, a successful injection = production incident (e.g.
  "roll back the prod database" executed).
- In a **read-only** agent, the worst case of an injection = exfiltration of
  collected data, manipulation of the recommendation, or cost abuse. **Never
  mutation.**

So the current positioning is: **"the AI SRE you trust — read-only by default,
and when it acts, it will act with guardrails the others didn't have from the
start"**. Strong guardrails + multi-language anti-injection **are the
competitive differentiator** (and what makes future execution safe), not a
nice-to-have. This spec raises injection defense from 1 layer (textual
delimitation) to real defense-in-depth.

## Threat model (summary — full STRIDE in the design)

In the current read-only phase, the "mutation" class is eliminated, so the focus
is exfiltration/manipulation/cost. **When execution is enabled, this threat model
must be extended** (elevation/mutation return — see design.md):

| Threat | Vector | Impact |
|--------|--------|--------|
| **Exfiltration** | Injection makes the agent leak collected inventory/configs | Infra data leak |
| **Response manipulation** | Injection makes the agent recommend something malicious to the operator | Operator acts wrongly on poisoned advice |
| **Cost abuse** | Injection forces expensive invocations / loops | Token burn, cost |
| **Multi-language jailbreak** | Attack in PT/ES/zh/etc. or obfuscated (base64, leetspeak, unicode) | Bypasses textual delimitation |
| **Cross-tenant leak** | Data from one session/user leaks into another | Breaks isolation |

## User Stories

WHEN any untrusted input (user query, adapter/MCP output, history, skill) enters
the flow THEN the system SHALL evaluate it against a guardrail independent of the
agent's prompt, **in any language**.

WHEN the guardrail detects a prompt attack/jailbreak THEN the system SHALL refuse
the request (fail-closed) and log the event — not try to "clean" it and proceed.

WHEN the guardrail/security service is unavailable THEN the system SHALL
**refuse** (fail-closed), prioritizing security over availability.

WHEN the agent's response is generated THEN it SHALL pass through an output
filter (PII, secrets, canary tokens) BEFORE returning to the user.

WHEN infra data is injected into the context THEN it SHALL contain canary tokens
that, if they appear in the output, signal exfiltration.

WHEN the system processes requests THEN it SHALL enforce a rate limit + budget
cap per user/session to contain cost abuse via injection.

## Acceptance Criteria

- [ ] **Layer 1 — Bedrock Guardrails** active on every invoke (input + output),
      with prompt-attack detection, denied topics, PII, multi-language.
- [ ] **Layer 2 — Input pre-scan**: normalization (unicode/base64/homoglyph) +
      heuristics before the LLM; obvious detections blocked without invoke cost.
- [ ] **Layer 3 — Context isolation**: untrusted data in delimited blocks +
      reinforced system instruction (keeps what already exists).
- [ ] **Layer 4 — Output filter**: PII/secrets/canary scan on the response.
- [ ] **Layer 5 — Canary tokens**: injected into the infra context; leak →
      alert + block.
- [ ] **Layer 6 — Rate limit + budget cap** per user/session (anti-abuse).
- [ ] **Fail-closed**: guardrail unavailability → 403, not bypass.
- [ ] **Multi-language proven**: a test suite with attacks in ≥5 languages +
      obfuscations (base64, leetspeak, zero-width, unicode confusables).
- [ ] **Audit log** structured for every detection/refusal (without leaking the
      malicious payload in clear text in the logs).
- [ ] **Read-only kept in the current phase** (don't regress accidentally);
      enabling execution is an explicit decision outside this spec.
- [ ] ≥90% test coverage on new code.

## Out of scope

- Enabling the agent to act/execute — **open future work**, outside this spec.
  When addressed, it requires extending the threat model (elevation) +
  human-in-the-loop + this spec implemented as a prerequisite.
- Network WAF / DDoS (infra layer, not application — another spec).
- Training our own detection model (uses managed Bedrock Guardrails).

## Declared trade-offs (decided by the user)

- **Cost**: every invoke gains a Guardrail evaluation cost (input+output).
  Accepted — security is the product.
- **Latency**: +evaluation per request. Accepted.
- **Availability**: fail-closed reduces availability under guardrail failure.
  Accepted — security > uptime for this product (unlike competitors, who
  prioritize availability).
