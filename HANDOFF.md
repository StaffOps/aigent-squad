# Handoff

> **Overwrite rule (spec 32, Decision 4)** — this file holds **only the current session
> + next steps**. It is *overwritten*, never appended. Previous content moves to
> `archive/handoffs/YYYY-MM-DD.md`. Permanent history lives in `CHANGES.md`; per-spec status
> in spec frontmatter + `specs/ROADMAP.md`.
>
> Prior sessions: `archive/handoffs/2026-07-16.md`, `archive/handoffs/2026-07-17.md`,
> `archive/handoffs/2026-07-24.md`, `archive/handoffs/2026-08-10.md`.

---

## Current state — 2026-08-19

### v0.5.0 released 🎉

Tag `v0.5.0` pushed 2026-08-18. All CI workflows green:
- **GHCR image**: `ghcr.io/staffops/aigent-squad:0.5.0` (signed + attested)
- **GitHub Release**: with SBOM attached
- **Scorecard**: running on `main`
- **Test**: 1982 passed, 92.96% coverage, mypy strict, lint, specs-status

### Cluster
- Image: `harbor.bigdatacorp.com.br/labs/aigent-squad:0.5.0-dev-205fb21`
- 4 pods healthy (2 gateway + 2 supervisor)
- Grafana dashboard: `DevOps-Testing/StaffOps` (uid: `a4j5w8`)

### Specs closed (3-day session, 2026-08-17 to 2026-08-19)
| Spec | Status |
|------|--------|
| 45 (supply-chain) | done 2026-08-17 |
| 25 (multi-tenant concurrency) | done 2026-08-18 |
| 18 (RCA investigation) | done-with-deferrals 2026-08-18 |
| 33 (operational review loop) | done-with-deferrals 2026-08-19 |
| 43 Phase 0+1 (capability gate) | done (Phases 2+ pending) |

### Other deliverables
- GHCR migration (Docker Hub → `ghcr.io/staffops/aigent-squad`)
- Mypy strict (0 errors)
- Branch protection on `main`
- Renovate workflow (activates after merge)
- 56 dedicated tests (B2), harness findings fixed
- k6 validated live, first operational review executed
- `TRIGGERS.md` catalog (12 triggers, 11 gaps documented)

---

## TODOs — next session

### 🟡 P2 — Quick wins (metric gaps from review)

1. Emit `aigent_investigation_rounds` counter in `run_investigation()` (~1h)
2. Emit `aigent_skill_miss` counter in `skills.py` (~1h)
3. Emit `aigent_rca_confidence` gauge in `_synthesize_rca()` (~1h)

### 🟡 P2 — Features

4. **Spec 43 Phase 2-3** — per-agent ServiceAccount + IRSA + Tier 1/2 enforcement
5. **Spec 44** — documentation-rag + incident-management agents (depends on 43 Phase 3)

### ⚪ P3 — Future

6. **Spec 42** — Distributed topology / A2A
7. **Spec 28** — LLM provider abstraction
8. **B-03** — feedback → KbDelta
9. **Docs workflow** — `DOCS_DEPLOY_TOKEN` needs a valid PAT with push to `StaffOps/staffops.github.io` (Fine-grained, resource owner = StaffOps org, Contents: Read+Write)
10. **F-013 final closure** — switch helmfile to GHCR image (when ready)
