# Handoff

> **Overwrite rule (spec 32, Decision 4)** — this file holds **only the current session
> + next steps**. It is *overwritten*, never appended. Previous content moves to
> `archive/handoffs/YYYY-MM-DD.md`. Permanent history lives in `CHANGES.md`; per-spec status
> in spec frontmatter + `specs/ROADMAP.md`.
>
> Prior sessions: `archive/handoffs/2026-07-16.md`, `archive/handoffs/2026-07-17.md`,
> `archive/handoffs/2026-07-24.md`.

---

## Current state — 2026-08-10, post-merge

**PR #20 is merged.** 51 commits went from `fix/openai-compat-drop-system-messages` into `dev`
as merge commit `4a40959`. `dev` had been **red since 2026-07-23** and is green again.

**CI validated this code with real dependencies for the first time.** Every local run before
today used the `otel_helper` stub, so the whole branch had never been through CI.

| Gate | On `dev` (CI, real deps) |
|---|---|
| `test` | **1895 passed / 0 failed**, seed `183224343` |
| coverage | **94.15%** |
| `lint` · `typecheck` · `specs_status` · `harness_score` · `dep_scan` · SAST | all pass |

Three distinct collection orders have now passed: `1581913328` (PR), `183224343` (dev), plus
the local unpinned runs.

### What the merge delivered

- **Suite recovered from red** — 13 stale failures, four independent causes, zero production
  defects.
- **spec 41 (calibrated honesty)** closed and homologated live; homologation caught a
  production bug 56 passing tests had missed (`x_aigent` reached no client).
- **Two new gates**: `make typecheck` (mypy, proven able to fail by injecting a type error)
  and `pytest-randomly` with an unpinned seed.
- **F-010, F-011, F-012, F-016, F-017, F-018 closed.** F-014/F-015 accepted as-is.
- **Coverage 93.18% → 94.15%** on the branches that carried real risk — `server.py` 62% → 89%
  (the alertmanager path, the only RCA flow with no human in the loop),
  `supervisor_client.py` 76% → 98%.

### The lesson from this session, recorded because it repeated

CI failed on its very first run, on `_server_breakers` — a module-level global that **16 local
configurations never exposed**. That failure also proved an earlier claim of mine wrong: F-016
was committed saying it "removes the whole bug class", when it had removed exactly one global
and no sweep had been done. The claim is marked as wrong in `specs/BACKLOG.md` rather than
edited away, and F-018 carries the real class-level fix.

Twice more in the same session, a stated fact turned out to be an inference: I concluded CI
was not randomising because the seed line was absent (`-q` suppresses it), and I told the user
`GATEWAY_KEY_AGENT_MAP` had become hot-reloadable (it had not — env is fixed at container
start). Both were caught only by going and checking. **Check before asserting; the doc is not
done when it sounds right.**

---

## TODOs — next session

### 🔴 P0 — Get production onto a pipeline-built image (F-013)

1. Production runs `0.4.0-homolog-agentic30`, **built and pushed to Harbor by hand** from a
   feature-branch commit. `ci-cd-conventions` names it: *"Manual `docker push` to registry
   (must go through pipeline)"*. The code is now in `dev`, which is step one.
2. **`build.yml` fires only on `main`**, so `dev` publishes nothing. The official image
   requires a `dev` → `main` merge. `main` is production — get the `dev..main` diff reviewed
   before proposing it, and note `helm-charts/release.yaml` also only fires on `main` +
   `charts/**`.
3. After the official image exists, point `k8s-setup/staffops/aigent-squad/values.yaml.gotmpl`
   at it and `helmfile apply` (expect the F-015 secret rotation on that apply).

### 🟠 P1 — Small, cheap, recorded

4. `release.yml` fires on any `v*` tag, so a release can be cut from a tagged commit that
   never passed through `main`. Decide whether that is intended.
5. Two non-blocking review leftovers: `test_gateway_main_paths.py:55` comment (verify it is
   still stale), and `test_high_risk_coverage.py:183` asserting the agentic path is reached
   but not the SSE body content.
6. The merged branch `fix/openai-compat-drop-system-messages` still exists on the remote. Fully
   merged, so deleting is safe and recoverable from `4a40959`.

### 🟡 P2 — Accepted as-is (user decision, 2026-08-09)

7. **F-014** (helmfile points at a relative chart path with a stale "revert once published"
   comment) and **F-015** (LibreChat chart rotates `JWT_SECRET`, `JWT_REFRESH_SECRET`,
   `CREDS_KEY`, `CREDS_IV` on every apply). Both in other repos. Accepted because this is a
   single-user internal product where dev/test/homolog/prd are one environment — the blast
   radius is the operator. **Revisit before a second user or any environment split**: F-015
   then orphans encrypted user credentials, which is not recoverable.

### ⚪ P3 — Carried over

8. **Cut `0.5.0`?** Per `version-management`, bump only with a measurable result in production.
   The spec-41 homologation plus two new gates and +1pp coverage on the riskiest paths is
   arguably that evidence. Owner: user.
9. Decisions pending (owner: user): specs 05/19 (superseded), `skills/oomkill-investigation` →
   merge into `root-cause-analysis`, and whether to keep functional Portuguese.
10. **Toward strict mypy** — ~184 missing-annotation errors remain outside the enabled checks.
    Cheap to tighten incrementally now that the gate exists and can fail.
11. Untested and registered: `adapters.py`'s 65 lines (judged not worth pinning, which is not
    judged correct), `kb/store.py` fail-open guards, `check_http`'s status threshold,
    `AGENTS_DIR=""` empty-string edge.
12. **B-03** (feedback → KbDelta) and **spec 28** (provider abstraction) — roadmap P3, not
    started. First real feature work once P0 is done.
13. **A2A** — dormant; do not reopen until the 3-part trigger in `specs/BACKLOG.md` fires.
