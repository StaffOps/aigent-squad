# Handoff

> **Overwrite rule (spec 32, Decision 4)** — this file holds **only the current session
> + next steps**. It is *overwritten*, never appended. Previous content moves to
> `archive/handoffs/YYYY-MM-DD.md`. Permanent history lives in `CHANGELOG.md`; per-spec status
> in spec frontmatter + `specs/ROADMAP.md`.
>
> Prior sessions: `archive/handoffs/2026-07-16.md`, `archive/handoffs/2026-07-17.md`,
> `archive/handoffs/2026-07-24.md`, `archive/handoffs/2026-08-10.md`,
> `archive/handoffs/2026-08-19.md`.

---

## Current state — 2026-11-24

- **Latest release:** `v0.5.0` (2026-08-18). Everything below is on `dev` only, listed under
  `CHANGELOG.md` → `[Unreleased]`.
- **`dev` CI:** green on the last merge (#70) — Test, Stack, SAST, CodeQL.
- **`main` image publish:** failing since 2026-11-18 at the Trivy gate (F-030, see Pending).

### Shipped to `dev` on 2026-11-24 (PRs #45–#70)

| PRs | What |
|-----|------|
| #45, #47, #54, #55 | **Spec 46 portable deployment profiles — `done`.** Pluggable conversation history (`HISTORY_BACKEND`: `redis` default, `dynamodb`, `postgres`); `${VAR}` expanded only in datasource address fields; `AGENT_<NAME>_ENABLED`; datasource URL checks; independent review findings fixed |
| #59, #63, #65 | Spec 46 follow-ups (F-025): Postgres expiry purge + `POSTGRES_SSLMODE`/`POSTGRES_SSLROOTCERT`, `HISTORY_MAX_MESSAGE_CHARS`, URL/credential scrubbing in errors and logs, `/ready` details without raw text; review of #62/#63 |
| #56 | **F-024:** history, rate limits and sessions scoped to the API-key consumer (`<consumer>:<user_id>`) |
| #60, #61 | Streamed answers saved to history (F-026); **L4 output filter + L5 canary on streamed answers** (F-031, `src/core/stream_output_guard.py`) |
| #62 | DynamoDB history reads honour `ttl` (F-027); gateway job records expire, Redis key names hash ids (F-028) |
| #66 | KB retention, `KB_ENABLED`, provenance (F-029) |
| #67 | Gateway input-size caps (413) and enforced job timeout (504) (F-009 S4 + C1) |
| #68 | **Spec 47 Phase 1:** `POST /feedback`, response ids on every answer, `aigent.feedback.*` metrics |
| #50, #52, #53 | Skills: 10 generic triage examples, punctuated keywords match, the repo ships `example-skills/` only (real catalogs via `SKILLS_DIR`) |
| #64 | OpenSSF Scorecard quick wins (B-33): least-privilege workflow tokens, injection fixes, CodeQL, signed release assets, SECURITY.md reporting links |
| #46, #48, #49, #51, #57, #58, #69, #70 | Docs: README refresh, environment-specific references removed, data classification and retention, BACKLOG hygiene + postponements, spec 48 written |

---

## Pending — and why

1. **The next release waits for the next `otel-helper` release.** The dependency bump that
   clears the Python CVEs ships with it. The release flow is `RELEASE.md`; its Phases 3-5
   (chart bump / overlay / rollout) still have to be exercised on a real cut.
2. **Image publish is blocked (F-030).** `build.yml` on `main` fails the Trivy gate (HIGH
   findings in the Alpine OS layer plus Python dependencies). It closes with two pieces that
   are not on `dev` yet:
   - the move to golden distroless base images — prepared on a **local, unpushed branch**;
   - the dependency bump that comes with the next `otel-helper` release.
3. **Chart changes are local** in the sibling `helm-charts` repo (not pushed); they go out
   with the release.
4. **BACKLOG reconciliation:** the F-029 and F-009 rows in `specs/BACKLOG.md` still read
   OPEN / PARTIAL although #66 and #67 shipped the fixes — close them with the PR references.

## Validation rule (in force)

- **Code changes** are validated in a test cluster **before** they are pushed.
- **CI-only changes** (workflows, lint/scan config) are validated by CI.
- Gates on every change: `make lint`, `make typecheck`, `make test` (≥90%), `make specs-status`,
  `make docs`, `make harness-score` (floor `MIN_LEVEL`). "Passes locally" is not "passes CI":
  confirm with `gh run list`.

## Open backlog highlights

- **B-33 — raise the OpenSSF Scorecard score** (owner: high priority). Quick wins shipped in
  #64; next: hash-pinning the remaining dependencies, the best-practices badge self-assessment,
  a code-review policy, fuzzing. Vulnerabilities close with F-030. Re-scan after each step.
- **Spec 48 — on-trigger comprehension** (`not-started`): the next feature spec (entity registry
  + baseline verdicts).
- **Spec 47** (`in-progress`): Phase 2 (response id in streamed chunks, real `agent_id` for
  proxied streams) and the independent test, code and security reviews (T8–T10).
- **Deferreds scheduled now** (`specs/BACKLOG.md` → Deferred register): spec 18 T14 correlator
  gain measurement, the spec 35 quality-regression row in `specs/TRIGGERS.md`, spec 39 T3.4.
- **Postponed (owner decision 2026-11-24)** — cannot be tested in the current environment:
  specs 42 (distributed topology), 43 (capability tiers, Phases 2+), 44 (write-capable agents);
  spec 08 T7 demo stage; spec 21 Opus enricher + Slack approval; spec 31 T21 k6 load run.

## How to resume

```bash
git fetch origin && git switch dev && git pull --ff-only
make up && make smoke        # local stack (history on Redis by default)
make test                    # full suite + 90% gate
```

Then: read `specs/ROADMAP.md` (canonical status table) and `specs/BACKLOG.md`; pick up B-33
or spec 48; work on a branch off `dev`, open a PR to `dev`, never push to `main`.
