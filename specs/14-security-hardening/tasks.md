# Tasks: Security Hardening (anti-prompt-injection defense-in-depth)

Order by value/risk. Each layer is an independent deliverable — not big-bang.

## Phase 1 — Primary guardrail + fail-closed (biggest gain)
- [ ] Task 1: Terraform — provision a Bedrock Guardrail (prompt-attack, denied topics, PII, multi-language) + output the guardrail id/version
- [ ] Task 2: `GuardrailClient` — apply the guardrail in `bedrock.invoke` (input + output), via `guardrailIdentifier`/`guardrailVersion`
- [ ] Task 3: Fail-closed — guardrail blocks or is unavailable → 403 + audit log (NOT bypass)
- [ ] Task 4: Structured audit log (detection/refusal; no payload in clear text; with agent_id/user_id/session_id)
- [ ] Task 5: Tests ≥90% (block path, fail-closed path, allow path)

## Phase 2 — Exfiltration defense
- [ ] Task 6: `CanaryGuard` — inject canary tokens into `infra_data`; detect on output
- [ ] Task 7: `OutputFilter` — scan for PII/secrets/canary in the response before returning
- [ ] Task 8: Tests ≥90% (canary leak → block; PII redaction)

## Phase 3 — Cost/abuse + input optimization
- [x] Task 9: `RateLimiter` + `BudgetGuard` per user/session — **reused** from spec 31 L3 (`src/core/rate_limiter.py` `AdmissionGuard`: per-user sliding-window rate + global daily budget, atomic Lua check-and-reserve/T19d). Reconciled fail-open (availability for rate/budget) vs the guardrail's fail-closed (security) — round-table decision; not reimplemented.
- [x] Task 10: `InputScanner` (`src/core/input_scanner.py`, L2) — pre-LLM normalization (NFKC → zero-width strip → Cyrillic/Greek homoglyph fold) + cheap heuristics (oversized, control-char density, repeated-char abuse, base64-blob inspection with NFKC re-normalized + word-boundary marker match). Runs at the start of `generic_agent.process_request`, before adapters/context/guardrail. Fail-closed; audit digest-only; `GuardrailBlockedError` reuse. Flag `INPUT_SCANNER_ENABLED` (default ON).
- [x] Task 11: Tests — `tests/test_input_scanner.py` **88 tests, 100% coverage**. Independent test-author + code-review (APPROVE-WITH-NITS) + security review (APPROVE; HIGH-1 marker casing + MEDIUM-1 decoded NFKC remediated; word-boundary match added to avoid FP).

## Phase 4 — Multi-language regression gate
- [ ] Task 12: Attack suite in ≥5 languages (PT/EN/ES/zh/ar) + obfuscations (base64, leetspeak, zero-width, unicode confusables) — becomes a CI gate
- [ ] Task 13: Reinforce context isolation (L3) based on what the suite reveals

## Phase 5 — Docs
- [ ] Task 14: `docs/SECURITY.md` — threat model, layers, fail-closed, competitive posture; update `READ_ONLY_POLICY.md` cross-referencing this spec

## Phase 1 status table (update when implementing)
| Task | State | Note |
|------|-------|------|
| 1 — Terraform guardrail | ✅ done | `infra/terraform/guardrail/` — `aws_bedrock_guardrail` (PROMPT_ATTACK HIGH, content filters, PII BLOCK, optional denied topics) + published version; outputs `guardrail_id`/`guardrail_version` |
| 2 — GuardrailClient in invoke | ✅ done | `src/core/guardrail.py` applied in `bedrock._invoke_sync` INPUT (pre-invoke) + OUTPUT (post-invoke) via `apply_guardrail` (separate evals per Decision 1) |
| 3 — Fail-closed → 403 | ✅ done | block OR unavailable/misconfigured → `GuardrailBlockedError`; propagated through classifier/supervisor/investigation (fail-closed on ANY agent) → HTTP 403 at `/query` and `/v1/chat/completions`. Does NOT trip the Bedrock circuit breaker |
| 4 — Structured audit log | ✅ done | `GuardrailClient._audit` — `audit=True`, event, source, agent_id/user_id/session_id, sha256[:12] digest. Never logs the payload in clear text (`_extract_categories` returns only labels) |
| 5 — Tests ≥90% | ✅ done | `tests/test_guardrail.py` (25) + guardrail cases in `tests/test_bedrock.py`; **99% coverage** on `guardrail.py`. Independent test-author + code-review (APPROVE) per `verification-independence` |
| 6 — CanaryGuard | ✅ done | `src/core/canary.py`: per-request unique tokens (128-bit random hex, `CNRY-` prefix) injected into `infra_data` (head+tail); exact **and fuzzy** match (separator-obfuscation resistant — HIGH-1 fix) on model output → `GuardrailBlockedError` (exfiltration signal). Tokens never logged in clear text (sha256[:12] digest only). Gated by `CANARY_ENABLED` (default ON). Wired in `generic_agent.py`. |
| 7 — OutputFilter | ✅ done | `src/core/output_filter.py`: regex scan for AWS keys (AKIA/ASIA + secret-with-context), private keys (PEM), emails, CPF, credit cards (**Luhn-validated** to drop timestamp/ID false-positives), GitHub/GitLab tokens, generic API secrets. Detection → `GuardrailBlockedError` (fail-closed: block, not redact — Decision 2). Gated by `OUTPUT_FILTER_ENABLED` (default ON). Categories `leak:<pattern>`. |
| 8 — Tests ≥90% | ✅ done | `tests/test_canary.py` + `tests/test_output_filter.py` + `tests/test_canary_output_integration.py` — **canary 100%, output_filter 98%** (70 tests). Independent test-author + code-review (APPROVE-WITH-NITS) + security review (APPROVE, HIGH-1/MEDIUM-2/MEDIUM-3 remediated: fuzzy canary, AWS-secret proximity, Luhn) per `verification-independence`. |
| 9 — RateLimiter + BudgetGuard | ✅ reused | Delivered as `AdmissionGuard` in `src/core/rate_limiter.py` (spec 31 L3). Per-user sliding-window rate + global daily budget, Redis-coordinated, atomic Lua check-and-reserve. **Fail-open** (availability for rate/budget; fail-closed reserved for security guardrails — reconciled at spec-31 round-table). |
| 10 — InputScanner | ✅ done | `src/core/input_scanner.py`: normalize (NFKC + zero-width strip + Cyrillic/Greek→Latin homoglyph fold) + cheap heuristics (oversized, control chars, repeated chars, base64 blob injection markers). Fail-closed on scanner error. Audit with sha256[:12] digest only. Wired at start of `generic_agent.process_request` (before adapters/context/invoke). Gated by `INPUT_SCANNER_ENABLED` (default ON). |
| 11 — Tests ≥90% | ✅ done | `tests/test_input_scanner.py` — independent test-author per `verification-independence`. |
| 12–14 | ❌ not started | Phases 4–5 (multi-language attack suite, context reinforcement, docs) pending |

## Promotion triggers (when to reopen / harden)
- Guardrail false-positives block legitimate use → add our own detector as an L1 fallback.
- On-prem/multi-cloud requirement → swap Bedrock Guardrail for self-hosted Llama Guard.
- Read-only relaxed (the agent starts to act) → **rewrite the whole spec** (elevation returns to the threat model; human-in-the-loop mandatory).
