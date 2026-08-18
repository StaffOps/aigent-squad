# Tasks: Harden Security

- [x] T1: Create `src/core/auth.py` with `require_token` dependency (X-Internal-Token) (S1) — done 2026-06-14
- [x] T2: Apply `Depends(require_token)` to `/process` (5 agents) and `/query` (supervisor); `/health` stays free (S1) — done 2026-06-14
- [x] T3: Supervisor injects `X-Internal-Token` when calling agents; MCP injects when calling supervisor (S1) — done 2026-06-14
- [x] T4: Add non-root `USER` to all 6 Dockerfiles + root Dockerfile (S2) — done 2026-06-14
- [x] T5: Add `securityContext` to compose/manifests where supported (S2) — done 2026-06-14
- [x] T6: Redis with `--requirepass` in compose; propagate `REDIS_PASSWORD`/`REDIS_SSL` to agents (S3) — done 2026-06-14
- [x] T7: Delimit untrusted data (`<user_query>`/`<infra_data>`) in context for all 5 agents + reinforcement in prompt.md (S4) — done 2026-06-14
- [x] T8: Create `docs/SECURITY.md` (dev vs prod model, IRSA, External Secrets, NetworkPolicy, mTLS) (S5) — done 2026-06-14
- [x] T9: Update `.env.example` with `INTERNAL_API_TOKEN` and `REDIS_PASSWORD` (S1,S3) — done 2026-06-14
- [x] T10: Tests: 401 without token / 200 with token; `id` non-root in the image (depends on: T1,T2,T4) — done 2026-06-14

## Suggested order
T1 → T2 → T3; T4/T5/T6 in parallel; T7; T8/T9; T10 closes.

## Notes
- Depends on spec 02 (unified servers) to apply auth consistently.
- Flag to the user: creating network services without authz is a risk; this spec is what closes it.

## Status (2026-06-14)

**Completed**: All tasks (T1–T10). Auth middleware, non-root containers, Redis password, prompt injection delimiters, security documentation, .env.example updated.

**Production-only items documented as future work** (in `docs/SECURITY.md`):
- Istio mTLS (requires mesh deployment)
- NetworkPolicy (requires K8s deployment)
- External Secrets Operator (requires AWS infra)
- IRSA (requires EKS)

**Deferred**: Nothing code-side — spec fully complete for dev/local scope. Production hardening is documented for future deploy specs.
