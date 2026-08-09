# Handoff

> **Overwrite rule (spec 32, Decision 4)** — this file holds **only the current session
> + next steps**. It is *overwritten*, never appended. Previous content moves to
> `archive/handoffs/YYYY-MM-DD.md`. Permanent history lives in `CHANGES.md`; per-spec status
> in spec frontmatter + `specs/ROADMAP.md`.
>
> Prior sessions: `archive/handoffs/2026-07-16.md`, `archive/handoffs/2026-07-17.md`,
> `archive/handoffs/2026-07-24.md`.

---

## Current session — 2026-08-08/09

Branch `fix/openai-compat-drop-system-messages`, **10 commits, none pushed**. Working tree
clean. All four gates green: **suite 1857 passed / 0 failed**, coverage 93.17%,
`make lint` PASS, `make specs-status` PASS, `make harness-score` PASS (L1 floor).

Sibling repo `k8s-setup` has **1 commit, not pushed** (`1fcb41d`, agentic30 values).
`helm-charts` is clean and in sync (verified with an explicit fetch) — chart 0.9.5 /
appVersion 0.4.0 is already published on `gh-pages`; nothing pending there.

### Shipped

- **Test gate went from red to green.** 13 failures on committed HEAD, all stale tests, four
  independent causes (`a434884`). See F-010.
- **spec 41 (B-16 Phase-2) closed** as `done-with-deferrals` and **homologated live**
  (`2ca3a22`). Homologation found a production bug 56 tests had missed: `x_aigent` never
  reached a single client because `build_completion` read the assessment by attribute while
  the gateway receives it as a dict over `resp.json()`, and a bare `except: pass` hid it
  (`ff6ad19`).
- **Coverage blind spot closed** (`3dee3cd`): `src/supervisor/server.py` was omitted from
  coverage as a "thin wrapper" while carrying the alertmanager webhook's tier-resolution
  closure — 62%, closure entirely uncovered. Now measured (70%) and tested.
- **F-012 closed at the cause** — the `reload`+`monkeypatch` module-state leak.
- **harness-score CI gate** (`e308f60`), **ruff green** (`fdb2003`, `8c05602`),
  `pyproject.toml` baseline (`33e89be`).
- **Independent review run retroactively** (`7570541`) — it caught a real must-fix: a test
  that could silently assert nothing. See the process debt below.
- **A2A protocol evaluated** → no spec; recorded under the existing dormant backlog entry
  (`47f6657`), with the reopen trigger and an ~11-step path.
- **agentic30 built + deployed** to `devops-core/staffops` (helm rev 66) and the
  declared/live drift closed in `k8s-setup` (`1fcb41d`). **But see F-013.**

---

## TODOs — next session

### 🔴 P0 — Regularise how production got its artifact (F-013)

1. **Production is running a manually-built image.** `0.4.0-homolog-agentic30` was built and
   pushed to Harbor **by hand** from commit `47f6657`, which lives on a feature branch — it
   did not pass CI, was not produced by a pipeline, and its code is in neither `main` nor
   `dev`. `ci-cd-conventions` names this exactly: *"Manual `docker push` to registry (must go
   through pipeline)"*. Legitimate as homologation (it found the `x_aigent` bug), unacceptable
   as a resting state. Fix = items 2 and 3 below; the pipeline then produces the official
   image. Tracked as **F-013**.

### 🟠 P1 — Push and reconcile (this is what closes P0)

2. **Push both repos.** `aigent-squad` is `[ahead 34]`; `k8s-setup` has 1 commit. Everything
   verified today ran against the `otel_helper` **stub**, so CI has never validated any of
   these 10 commits. A disk failure loses the day. (The deployed image *was* built with real
   deps, so runtime wiring is exercised in-cluster — but that is not the same as CI.)
3. **Slice the branch.** ~40 commits under a name describing one openai-compat fix, spanning
   four unrelated subjects (that fix, harness/lint, spec 41, test cleanup). Merge to `dev` is
   a **pure fast-forward** (verified: `dev` has not moved, zero divergence), so slicing is
   cheap now and gets costlier per commit. Note `build.yml` only fires on `main` and
   `helm-charts/release.yaml` only on `main` + `charts/**`, so nothing publishes from `dev`
   or a feature branch.

### 🟡 P2 — Operational hygiene

4. **Swap the helmfile's local chart path for the published chart** (F-014). It still points
   at `../../../helm-charts/charts/aigent-squad` with a comment saying the chart is *"NOT yet
   published … revert once published"* — that condition was met (0.9.5 is on `gh-pages`) and
   the comment is stale. While it stays relative, deploys only work on a machine with that
   repo cloned at an exact path — zero reproducibility for CI or another person. Validate the
   swap with `helmfile diff` first.
5. **LibreChat chart secret rotation** (F-015) — `JWT_SECRET`, `JWT_REFRESH_SECRET`,
   `CREDS_KEY`, `CREDS_IV` use `randAlphaNum` with no `lookup`, so **every** `helmfile apply`
   rotates them: everyone logged out, and previously-encrypted user credentials become
   undecryptable. Rotated three times during this session's applies (accepted at the time —
   single user). Fix in the chart: `lookup` to preserve, or point the values at an
   ExternalSecret.
6. **Decide on the `release.yml` tag path.** It fires on `push` of tag `v*` (and
   `workflow_dispatch`), so a release can be cut from any tagged commit without passing
   through `main`. If the intent is "only `main` releases", this is a gap in the process.

### 🔵 P3 — Test-quality debt created this session

7. **Verification-independence was violated** on `ff6ad19` and `3dee3cd` — the same author
   wrote the production fix and its tests. Disclosed in the commit messages and reviewed
   *retroactively* (which worked only because nothing was pushed). Independent test review
   should still happen.
8. **Suite order-independence is unproven.** `pytest-randomly` is not installed, so
   "order-independent" is an assertion, not a measurement. F-012 showed this bug class costs
   a full isolated-worktree investigation to diagnose.
9. **F-011 still open** — the MCP adapter fails open correctly but surfaces `unhandled errors
   in a TaskGroup` instead of the real cause (anyio wraps it). Behaviour fine, observability
   not. The brittle substring assert was deliberately NOT re-added; fix the unwrapping in
   `adapters.py`.

### ⚪ P4 — Carried over, untouched

10. **Decisions pending (owner: user)** — delete/merge candidates: specs 05/19 (superseded),
    `skills/oomkill-investigation` → merge into `root-cause-analysis`.
11. **Functional Portuguese — keep or strip?** `config.py` bilingual + triage keywords + PT
    eval/attack fixtures (removing degrades bilingual UX and weakens PT-attack tests).
12. **Cut `0.5.0`?** Per `version-management`, only bump with a measurable result in prod. The
    spec-41 homologation is arguably that evidence — worth a deliberate decision now.
13. **B-03** (feedback → KbDelta) and **spec 28** (provider abstraction) — roadmap P3, not
    started.
14. **A2A** — dormant, do not reopen until the 3-part trigger in `specs/BACKLOG.md` fires.
