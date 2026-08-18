# Handoff

> **Overwrite rule (spec 32, Decision 4)** — this file holds **only the current session
> + next steps**. It is *overwritten*, never appended. Previous content moves to
> `archive/handoffs/YYYY-MM-DD.md`. Permanent history lives in `CHANGES.md`; per-spec status
> in spec frontmatter + `specs/ROADMAP.md`.
>
> Prior sessions: `archive/handoffs/2026-07-16.md`, `archive/handoffs/2026-07-17.md`,
> `archive/handoffs/2026-07-24.md`, `archive/handoffs/2026-08-10.md`.

---

## Current state — 2026-08-17

**9 commits to `dev` in a single session.** All green: 1926 tests, 92.11% coverage, mypy strict.

| Commit | Delivery |
|--------|----------|
| `48de7a3` | Spec 45 Phase 1+2 (pin actions, scorecard, SECURITY.md, CODEOWNERS) |
| `0d20d25` | Registry migrated: Docker Hub → GHCR (`ghcr.io/staffops/aigent-squad`) |
| `519dc92` | Mypy strict enabled (81 errors → 0) |
| `ffb93ae` | All specs translated to English |
| `19bd125` | Spec 45 Phase 3 (cosign sign, attestations, verify job) |
| `e69984a` | Spec 45 Phase 4+5 (Renovate config, trivyignore.yaml, VERIFYING-RELEASES.md) — **spec 45 CLOSED** |
| `ce3e702` | Spec 43 Phase 0+1 (capability tier model + gate, Tier 0 only) |
| `a416443` | Branch protection on `main` + DOCKERHUB secrets deleted |
| `437ed7d` | Spec 25 Phases 1-3 (distributed circuit breaker, session lock, Bedrock semaphore) |

Also: helm-charts updated to GHCR (`2b681c8`), 5 stale branches deleted.

### Key changes from this session

- **GHCR** is the image registry now. `DOCKERHUB_*` secrets deleted. `DOCS_DEPLOY_TOKEN` remains.
- **Branch protection** on `main`: require PR (1 approval) + `test` status check, no force push.
- **Mypy strict** — 60 source files, zero errors.
- **Spec 45 (supply-chain)** — all 5 phases closed. Signed releases, attestations, Scorecard.
- **Spec 43 Phase 1** — capability gate in code, all agents Tier 0, design decisions H-1→H-7 resolved.
- **Spec 25 Phases 1-3** — distributed state (Redis circuit breaker, session lock, rate limiter, Bedrock semaphore).

---

## `dev` ahead of `main`: ~139 commits

Production still runs `0.4.0-homolog-agentic30` from Harbor (hand-built image). The new pipeline
publishes to GHCR when `dev` merges to `main`. No rush — user explicitly said "quero adicionar mais
coisas antes do próximo merge".

---

## TODOs — next session

### 🟠 P1 — Finish in-progress specs

1. **Spec 25 Phase 4-5** — k6 load test scenarios + docs (`MULTI-TENANCY.md`, `LOAD-TESTING.md`).
2. **Spec 18** — RCA investigation workflow (7 tasks: signal-coverage audit, EVIDENCE-MODEL correlator,
   LLM confidence ceiling, real-RCA existence proof).

### 🟡 P2 — Deferred small items

3. **T1.6** (spec 45) — Record baseline Scorecard score. Needs first run on `main`.
4. **T4.5** (spec 45) — Renovate proof-of-life. Install the App (https://github.com/apps/renovate)
   or add self-hosted workflow. Free for all repos.
5. **Spec 43 Phase 2-3** — per-agent ServiceAccount + IRSA + Tier 1/2 enforcement (infra work,
   blocked until write agents are actually needed).
6. **Spec 44** — documentation-rag + incident-management agents (depends on 43 Phase 3).

### ⚪ P3 — When ready

7. **F-013**: Merge `dev` → `main` + `helmfile apply`. Cut next version when ready.
8. **Spec 42** — Distributed topology / A2A (design done, implementation not started).
9. **Spec 33** — Operational review loop (not started).
10. **Spec 28** — LLM provider abstraction (design-only).
11. **B-03** — feedback → KbDelta.
