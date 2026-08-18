# Handoff

> **Overwrite rule (spec 32, Decision 4)** — this file holds **only the current session
> + next steps**. It is *overwritten*, never appended. Previous content moves to
> `archive/handoffs/YYYY-MM-DD.md`. Permanent history lives in `CHANGES.md`; per-spec status
> in spec frontmatter + `specs/ROADMAP.md`.
>
> Prior sessions: `archive/handoffs/2026-07-16.md`, `archive/handoffs/2026-07-17.md`,
> `archive/handoffs/2026-07-24.md`, `archive/handoffs/2026-08-10.md`.

---

## Current state — 2026-08-18

**Two-day session. 20+ commits to `dev`. All specs in-progress closed or advanced.**

### Cluster
- Image: `harbor.bigdatacorp.com.br/labs/aigent-squad:0.5.0-dev-205fb21`
- 4 pods healthy (2 gateway + 2 supervisor), smoke test passed
- Grafana dashboard: `DevOps-Testing/StaffOps` — AIgent Squad Concurrency (uid: `a4j5w8`)

### Specs closed this session
| Spec | Completed |
|------|-----------|
| 45 (supply-chain hardening) | 2026-08-17 — all 5 phases |
| 25 (multi-tenant concurrency) | 2026-08-18 — all tasks |
| 18 (RCA investigation) | 2026-08-18 — done-with-deferrals (T11 Docker smoke, T14 eval deferred) |

### Key deliverables
- **GHCR** migration (Docker Hub → `ghcr.io/staffops/aigent-squad`)
- **Mypy strict** (0 errors, 60 source files)
- **Capability gate** (spec 43 Phase 0+1 — code done, Tier 0 only)
- **EVIDENCE-MODEL correlator** (spec 18 T12+T13 — real incident validated)
- **Supply-chain** (cosign signing, attestations, Renovate, Scorecard, pin-check)
- **Distributed concurrency** (Redis circuit breaker, session lock, Bedrock semaphore)
- **k6 load test** validated live (0% errors, 3 VUs)
- **Harness findings** fixed (contextvar leak, Redis TLS, queue metric drift)
- **56 new dedicated tests** (B2), total 1982 passing, 92.96% coverage
- **Branch protection** on `main` (require PR + approval + test check)
- **DOCKERHUB secrets deleted**, **F-008 fixed**, **F-014/F-015 closed (accepted)**

---

## TODOs — next session

### 🔴 P0 — Cut the release

1. **Merge `dev` → `main`** — ~150 commits. Creates the first pipeline-built GHCR image.
   Scorecard runs. Renovate activates. This is the last blocker for everything "official".

### 🟠 P1 — Post-merge

2. **T1.6** (spec 45) — Record baseline Scorecard score after first run.
3. **T4.5** (spec 45) — Trigger Renovate manually, confirm it opens a PR.
4. **Version decision** — tag `v0.5.0`? Per `version-management`, bump only with measurable result.
   Evidence: 4 specs closed, cluster-validated, mypy strict, supply-chain signed. Strong case.

### 🟡 P2 — Next features (user decides priority)

5. **Spec 43 Phase 2-3** — per-agent ServiceAccount + IRSA + Tier 1/2 enforcement.
   Enables write agents. Infra work (Terraform, Helm, Kyverno).
6. **Spec 44** — `documentation-rag` + `incident-management` agents (depends on 43 Phase 3).
7. **Spec 33** — Operational review loop (not started, no dependencies).
8. **T15 real RCA** — complete with org data in `evals/results/rca-private/` (gitignorado).

### ⚪ P3 — Future

9. **Spec 42** — Distributed topology / A2A (design done, implementation large).
10. **Spec 28** — LLM provider abstraction (design-only).
11. **B-03** — feedback → KbDelta.
