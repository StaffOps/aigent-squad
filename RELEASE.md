# Release Runbook

> Spec `34-release-runbook`. Sequences EXISTING CI (`release.yml`, `build.yml`,
> chart-releaser) across 3 repos — it does not add new automation. Every step
> is a single command or a single verifiable check. If a step doesn't fit that
> shape, that's a smell to fix in this file, not a reason to skip it.
>
> **Repos involved** (labeled per step below):
> - `[app]` — this repo (`staffops-aigent-squad`), branch `dev` → `main`
> - `[helm-charts]` — `github.com:StaffOps/helm-charts`, chart `charts/aigent-squad`
> - `[k8s-setup]` — `gitlab.com:.../k8s-setup`, `staffops/` (helmfile + values)
>
> **Tooling note (updated 2026-07-17)**: spec 32 shipped, so Phase 0's status
> check is now automated — `make specs-status` (`scripts/specs_status.py`)
> validates the `specs/ROADMAP.md` canonical table against every spec's
> frontmatter, and the verification pipeline lives in `specs/README.md`. Spec 33
> (operational review) is still unstarted, so Phase 6's review step stays
> not-yet-active. `steering/version-management.md` (a "global steering" file
> referenced by `steering/project.md`) isn't present in this repo — the operative
> local rule is `steering/milestone-criteria.md` + the bump-gate tracked in
> `specs/ROADMAP.md`'s "Suggested real version" line. That remaining gap is a
> BACKLOG item, not a blocker for this runbook (design.md: "any CI gap found in
> T4 is a BACKLOG item, not scope").

---

## Phase 0 — Pre-flight `[app]`

1. **CI green on `dev`**: `gh run list --branch dev -L 5` — all `completed success`.
2. **Spec status gate**: `make specs-status` — `scripts/specs_status.py`
   validates the `specs/ROADMAP.md` canonical table against every spec's
   frontmatter and fails on any drift (unknown status, a `done` carrying
   deferrals, a `deferred:` item missing from `specs/BACKLOG.md`). Confirm
   `HANDOFF.md`'s most recent entries match `git log`.
3. **`CHANGES.md` `[Unreleased]` accurate**: read the section top-to-bottom;
   every entry must correspond to something actually shipped and reachable
   from `dev`. Stale/superseded entries get folded or removed before the cut,
   not silently carried forward.
4. **Bump justified**: MINOR (`0.X.0`) only on a *validated milestone* — real
   deploy + real tests in the target environment, not just "features are
   done" (`steering/milestone-criteria.md`; historical precedent: 0.3.0 was
   gated on "gateway cluster-validated", not on a feature list). Read
   `specs/ROADMAP.md`'s current "Suggested real version" / gate note for
   what's actually being validated this cycle. If the gate note is stale
   (this note itself was stale on 2026-07-15 — a single correction pass fixed
   it across 3 spots in ROADMAP.md + 4 in AGENTS.md), fix it here before proceeding, not
   after.

## Phase 1 — Merge `[app]`

1. Open PR `dev` → `main`: `gh pr create --base main --head dev --title
   "release: <version>" --body <summary>`.
2. **Guard job enforces source branch** (`.github/workflows/test.yml`
   `guard:` job, `if: github.base_ref == 'main'`) — fails LOUDLY (exits 1) if
   the PR's head isn't literally `dev`. Deliberately not calling this
   "fail-closed" — that term means something stricter elsewhere in this repo
   (Guardrail/InputScanner/OutputFilter: unavailable-or-blocked → hard 403,
   no bypass possible, AGENTS.md invariant #6). This guard has no branch
   protection behind it (see Invariants) — a red check does NOT block the
   merge by itself, so a human still has to actually watch it fail and stop.
3. CI green on the PR (`Test`, `SAST`, `Docs build_check` — same jobs as any
   `dev` push, plus the guard job).
4. Merge (squash or merge commit — match repo convention, currently merge
   commit per `git log --oneline origin/main` history).
5. **Docs site auto-publishes from `main`** (`docs.yml`, `on: push: branches:
   [main] paths: [docs/site/**, mkdocs.yml]`) — verify the live site updated
   if EITHER `docs/site/` or `mkdocs.yml` changed this cycle (`gh run list
   --branch main -L 3`); a nav/theme/plugin-only `mkdocs.yml` change still
   triggers a deploy even with zero content changes.

## Phase 2 — Tag + image `[app]`

1. `git checkout main && git pull`
2. `git tag vX.Y.Z && git push origin vX.Y.Z`
3. `release.yml` fires: single-arch build → **Trivy gate** (CRITICAL/HIGH,
   `.trivyignore` exceptions) → multi-arch (`linux/amd64,linux/arm64`) build +
   push to Docker Hub (`karlipegomes/aigent-squad:X.Y.Z` + `:latest`) → SBOM →
   GitHub Release (auto-generated notes).
4. **Verify the published image actually contains this milestone's code** —
   this is the 0.2.0 lesson (the `0.2.0` tag was published WITHOUT the
   gateway, only `latest` had it, because the tag was cut before the gateway
   commit landed). Pull the tagged image and grep for a marker unique to this
   cycle's work, e.g.:
   ```
   docker pull karlipegomes/aigent-squad:X.Y.Z
   docker run --rm karlipegomes/aigent-squad:X.Y.Z python3 -c \
     "import src.core.response_quality; print('groundedness OK' if hasattr(src.core.response_quality, 'ungrounded_numeric_claims') else 'MISSING')"
   ```
   (swap the marker per release — pick something from this cycle's actual diff.)

## Phase 3 — Chart `[helm-charts]`

> Only needed if the app's public API/config surface changed enough to
> warrant a chart bump — check `Chart.yaml`'s current `version`/`appVersion`
> against what's already published (`helm search repo staffops/aigent-squad
> --versions`) before assuming a bump is required.
>
> **Standing gap, not yet fixed (see BACKLOG)**: `charts/aigent-squad/
> values.yaml`'s `image.repository` still points at a personal Docker Hub
> account (`karlipegomes/aigent-squad`), from the pre-org-migration era —
> the "neutralize this" TODO from the 0.3.0 cycle was never actually done.
> Not blocking a release (the value still works), but check whether THIS
> cycle is the one to finally fix it before assuming it's someone else's problem.

1. Bump `charts/aigent-squad/Chart.yaml`: `version` (chart SemVer, bump per
   the size of the chart-level change) and `appVersion: "X.Y.Z"` (must equal
   the app tag from Phase 2 — this is the coherence rule, see Invariants).
2. Update `CHANGELOG.md` in the chart dir.
3. PR → `main` (or push if the repo's convention allows direct main pushes —
   confirm `git log` there before assuming `[app]`'s no-direct-push rule
   applies; treat it the same way unless told otherwise).
4. `release.yaml` (chart-releaser) fires automatically on `main` when
   `charts/**` changes — publishes to the `gh-pages` index, no manual step.
5. Verify: `helm repo update staffops && helm search repo
   staffops/aigent-squad --versions | head -3` shows the new version.
6. **Revert any local-path chart override** — if `[k8s-setup]`'s
   `helmfile.yaml.gotmpl` still points `chart:` at a local filesystem path
   (as of 2026-07-15 it does — "TEMPORARY" comment, chart fixes not yet
   published), this is the step that makes reverting to `chart:
   staffops/aigent-squad` possible. Skip only if the override is still
   needed (unpublished chart-only fixes) — record the exception + a revert
   trigger condition inline in the helmfile comment, don't just leave it
   silently stale (this is exactly how the path bug found 2026-07-15 sat
   unnoticed for a while).

## Phase 4 — Overlay `[k8s-setup]`

1. Point `staffops/aigent-squad/values.yaml.gotmpl` (or the helmfile
   `releases:` entry) at the published chart version from Phase 3, if the
   local-path override was reverted.
2. **Remove temporary tag pins** — if `services.{gateway,supervisor}.image.tag`
   is pinned to a `-dev` tag (it is, as of 2026-07-15: `0.3.0-dev`), decide:
   revert to inheriting `Chart.appVersion` (now `X.Y.Z`, no `-dev` suffix), OR
   explicitly record why the pin stays (e.g. still iterating between chart
   releases) with a condition for when to revert.
3. `pullPolicy` back to `IfNotPresent` once the tag is an immutable release
   tag (not a `-dev` tag some other process keeps overwriting) — `Always` is
   correct ONLY while multiple pushes reuse one mutable tag.
4. Commit + push (`main`, this repo's convention already treats `main` as
   the working branch — confirmed via `git log`, unlike `[app]`).

## Phase 5 — Rollout `[k8s-setup]`

1. `cd staffops && helmfile -e default -l name=aigent-squad diff` — **if this
   errors** with a `--validate`/`--dry-run` flag-group conflict, your local
   `helm-diff` plugin predates Helm v4 support: `helm plugin update diff`
   (fixed locally 2026-07-15 this way, upstream added v4 support in
   `helm-diff` 3.15.10 — see `specs/BACKLOG.md` B-26). If updating the
   plugin isn't possible right now, fall back to `helmfile build`. Be
   precise about what that fallback actually proves: `helmfile
   build` only confirms the CURRENTLY COMMITTED values render without a
   syntax error — it does NOT by itself prove there's no delta from what's
   ACTUALLY DEPLOYED. To get that second guarantee, diff the rendered output
   by hand against `helm get values aigent-squad -n staffops`. Only skip
   straight to `kubectl rollout restart` (instead of `helmfile apply`) once
   BOTH checks agree there's no values delta — image-only changes qualify,
   nothing else (lower blast radius, no chart/values machinery involved;
   this is what 2026-07-15's cycle actually did, blocked from a blind
   `apply` by the auto-mode classifier for lacking a working diff preview).
2. If `helmfile apply` IS used (values actually changed): `helmfile -e
   default -l name=aigent-squad apply`.
3. Watch rollout: `kubectl rollout status deployment/aigent-squad-gateway
   deployment/aigent-squad-supervisor -n staffops --timeout=180s` — both
   `successfully rolled out`.
4. **Pods run the EXPECTED digest** (not just "Running"):
   ```
   kubectl get pods -n staffops -l app.kubernetes.io/instance=aigent-squad \
     -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].imageID}{"\n"}{end}'
   ```
   Every pod's digest must match the digest pushed in Phase 2 (or Phase 2's
   Harbor-mirror equivalent, if `[k8s-setup]` still points at Harbor rather
   than the now-public Docker Hub tag — reconcile this discrepancy the first
   time Phase 3/4 actually get executed for real; as of 2026-07-15 the
   cluster still pulls from Harbor `labs/aigent-squad`, a separate,
   private, manually-pushed image, NOT the Docker Hub tag Phase 2 publishes.
   Until Phase 3/4 are executed for real, Phase 5 in practice means: rebuild
   + push the SAME image content to Harbor's `0.3.0-dev` tag, then
   `kubectl rollout restart` to force a re-pull — no helmfile/chart
   involvement, exactly what shipped this cycle).

## Phase 6 — Homologation `[app]` (against the public gateway)

Reused from the actual 2026-07-01/03/15 homologation sessions. Fetch the
real `INTERNAL_API_TOKEN` read-only from Secrets Manager for each of these —
never print it, only use it inline in a `curl` header:
```
IAT=$(aws secretsmanager get-secret-value --secret-id STAFFOPS_AIGENT_SQUAD \
  --region us-east-1 --query SecretString --output text | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['internal-api-token'])")
```

1. **Health**: `curl -s https://aigent-squad.<org>.app.br/ready` → `{"status":"ready",...}`.
2. **Real query per critical agent** — at minimum `aws` (e.g. "How many EC2
   instances are running?") and one other agent touched by this cycle's
   changes; confirm `agent` field in the response matches expectation and
   the answer is substantive (not an error/empty string).
3. **One injection/security probe expecting 403** — e.g. a known attack-suite
   payload (`tests/test_attack_suite.py` has 62 parametrized cases; pick one)
   sent via `curl` to `/query` — must come back `403 guardrail_blocked` or
   `input_scanner_block`, never 200.
4. **Admission headers** — send enough requests to approach the rate/budget
   limit and confirm `429`/`503` responses carry the expected
   `Retry-After`/budget headers (or, cheaper: read `AdmissionGuard`'s current
   config and confirm the headers are present on ANY response, even a 200).
5. **MCP round-trip** (if this cycle touched an MCP-backed agent, e.g.
   `kubernetes`): a real query that exercises the MCP datasource, confirm no
   `raw_taskgroup_exception`/connection-error text leaks (would mean
   `ResponseQualityGuard` regressed).
6. **`/v1/models`** lists every agent in `agents/` (catches an agent silently
   failing to register, e.g. a `required_env` var missing at startup).

## Phase 7 — Close `[app]`

1. **Credential hygiene FIRST, not last** — moved to position 1 deliberately
   (2026-07-15 review finding: burying this between two not-yet-active steps
   risked a skim-skip, exactly the "PAT TODO sat for days" failure mode this
   step exists to prevent). Revoke any temporary tokens/PATs used DURING this
   cycle (the 2026-07-01 lesson: "revoke the two PATs pasted in chat" sat as
   a HANDOFF TODO for days). Check: any `gh auth token` / manually-created PAT
   used for this release specifically, not a long-lived credential someone
   else depends on. **Do this before anything else in Phase 7, not after.**
2. **`CHANGES.md` cut**: rename `[Unreleased]` → `[X.Y.Z] - <date>`, start a
   fresh empty `[Unreleased]` above it. Consolidate/dedupe entries that
   accumulated since the last real cut (check: is everything currently under
   `[Unreleased]` actually still accurate, or does some of it describe a
   defect that was later fixed differently? fold, don't just concatenate).
3. **Update `specs/ROADMAP.md`** "Suggested real version" line to the new
   version + the honest next bump gate.
4. **`specs/<NN>/tasks.md`** for every spec that shipped this cycle: confirm
   `[x]` + completion dates are accurate (not just "some things checked").
5. **`HANDOFF.md` overwrite** (spec 32 rule, now active): overwrite `HANDOFF.md`
   with the current session + next steps only, moving the prior content to
   `archive/handoffs/YYYY-MM-DD.md`.
6. **Run the operational review** (spec 33, once it ships — same
   not-yet-active note as above).

---

## Invariants

- **Tag/appVersion/overlay coherence**: chart `appVersion` MUST equal the
  released app tag; an overlay image-tag pin is a TEMPORARY EXCEPTION with a
  written revert condition, never a silent permanent fork. Worked example —
  the actual 2026-07-03 incident: chart `appVersion` was bumped to the
  stable `0.3.0`, but the real running image was still tagged `0.3.0-dev` in
  Harbor (a separate rebuild-under-mutable-tag flow) — fixed by explicitly
  pinning `services.*.image.tag: 0.3.0-dev` in the overlay as a **recorded**
  exception, not letting the chart's inherited `appVersion` silently lie
  about what's actually deployed.
  **On divergence, which artifact is authoritative (2026-07-15 review
  finding F5, made explicit because the two readings genuinely differ)**:
  the DEPLOYED IMAGE is ground truth, the chart's `appVersion` is the thing
  that must be brought into agreement with it — never the other way around
  (don't "fix" a real cluster to match a chart file). Pin the overlay +
  record the exception (like the 0.3.0-dev case), don't silently edit
  `appVersion` to match and call it resolved.
  **This "exception" has in practice been the ONLY mode ever used** — every
  release cycle to date, including 2026-07-15's, deployed via the Harbor/
  overlay-pin path and never actually exercised the Docker-Hub/chart-bump
  path (Phase 2/3) for real (see the dry-run section's gap #1 below). Treat
  the rule above as describing the REAL steady state, not a rare fallback.
- **`main` never receives direct pushes in `[app]`** — PR from `dev` only,
  enforced by the guard job; this is manual discipline (no branch protection
  yet — HANDOFF has an open item for the GitHub plan needed). `[helm-charts]`
  and `[k8s-setup]` may have different real conventions — verify via `git
  log` before assuming `[app]`'s rule transfers; don't invent a stricter
  rule than what's actually enforced.
- **A published tag is immutable** — a bad release gets `X.Y.Z+1`, never a
  re-tag or force-push over an existing tag.
- **Homologation always includes ≥1 NEGATIVE probe** (Phase 6.3) — a release
  that only tests happy paths never validates the security posture.

## Version-bump decision rule

MINOR (`0.X.0`): a validated milestone — real deploy + real tests in the
target environment (not merely "the features are code-complete"). Read the
CURRENT gate from `specs/ROADMAP.md`'s "Suggested real version" section
before assuming what's being validated — that line has gone stale before
(2026-07-15: it named spec-14 findings A/B/D as an open gate 4 days after
they'd actually closed) and this runbook's own Phase 0 step 4 exists
specifically to catch that class of staleness before it propagates into a
release decision.

PATCH (`0.X.Y+1`): a bad release's successor, or a fix with no new
validated capability.

MAJOR: not applicable pre-1.0 — this project hasn't defined what MAJOR means
yet; cross that bridge when it's real.

## Dry-run validation (2026-07-15, against the 0.3.0 cycle)

Every ad-hoc action actually taken during the 2026-07-01 → 2026-07-03 cycle
(source: `HANDOFF.md`, sessions "2026-07-01" and "2026-07-02 → 2026-07-03"),
mapped onto a phase above — zero orphans found:

| 0.3.0-cycle action (from HANDOFF) | Runbook phase |
|---|---|
| Helmfile install under k8s-setup, two-doc structure | Phase 4/5 (first-time setup, same repo/phase) |
| Multi-arch image build, push to Harbor `0.3.0-dev` | Phase 2 (Harbor-vs-Docker-Hub gap noted inline in Phase 5) |
| Terraform apply (IRSA, DynamoDB, Guardrail, VPC endpoints) | Out of scope (infra provisioning, not a release step — one-time/rare, not part of the repeatable per-release path) |
| Secrets via ExternalSecret → Secrets Manager | Out of scope (same — infra setup, not per-release) |
| Chart fixes (ExternalSecret v1, Redis persistence, git-sync path, agents[] schema) bumped to 0.9.0 | Phase 3 |
| `Boto3Adapter` region_name fix, `Classifier._extract_json` fence-strip fix | Phase 0 (pre-flight should have caught these via tests — BACKLOG: neither had a regression test until later; not this runbook's gap to fix, noted for spec 33) |
| Real homologation queries (EC2/S3/IAM counts, cost trend) | Phase 6.2 |
| Gateway timeout bump (15→30s) found live | Phase 6 finding → Phase 0 pre-flight next cycle (config gaps found during homologation feed back into the NEXT release's pre-flight, not silently forgotten) |
| "Commit-time TODO" list: publish chart, revert local-path override, revert pullPolicy, **revoke 2 PATs** | Phase 3 (publish+revert), Phase 4 (pullPolicy), Phase 7.1 (PAT revocation) |
| "neutralize `gateway.image.repository`" (same TODO list) | **Corrected 2026-07-15 (T6 review finding F1)**: this is `[helm-charts]`'s `charts/aigent-squad/values.yaml` `image.repository: karlipegomes/aigent-squad` (Phase 3, NOT Phase 4/`[k8s-setup]` — the overlay has its own separate `repository`/`pullPolicy` fields, a different concern). Still unedited as of 2026-07-15 (confirmed live on `helm-charts` `main`) — **never actually done**, and wasn't tracked anywhere until this dry-run surfaced it; added to `specs/BACKLOG.md` same day rather than left as a RELEASE.md-only mention |
| Tag mismatch (chart appVersion=0.3.0 vs real image 0.3.0-dev) found + fixed via overlay pin | Invariants (coherence rule) + Phase 4.2 |
| 4 security findings (A/B/C/D) found during homologation, deferred | Phase 6.3 (negative-probe homologation is exactly what surfaces this class) + tracked in the spec, not lost |
| Two local commits "NOT pushed yet, awaiting approval" | Phase 0/1 (pre-flight literally starts with "is `dev` even pushed" — this runbook assumes yes; if not, that's a step before Phase 0, added implicitly by "CI green on dev" requiring a push to have happened) |

No orphan actions found — every real 0.3.0-cycle step maps to a phase above.
Three real gaps surfaced by the mapping (not phase-shape problems, tooling
and process gaps) — filed in `specs/BACKLOG.md` the same day (2026-07-15
T6 review finding F2: a gap only mentioned in this file's prose has no
anchor if this file is later trimmed):
1. **`specs/BACKLOG.md` B-25** — Harbor (private, manual push) vs Docker Hub
   (public, `release.yml`-driven) are two DIFFERENT image publishing paths
   that have never been reconciled — every cycle so far, including
   2026-07-15's, has released to Harbor only, never actually exercised
   Phase 2/3's Docker-Hub-and-chart path for real. `[app]`'s own
   `steering/project.md`/`AGENTS.md` describe the Docker-Hub path as
   canonical but it's never been the ACTUAL deploy path used.
2. **`specs/BACKLOG.md` B-26** — `helmfile diff`/`apply` is currently broken
   (helm-diff/Helm-v4 incompatibility, found 2026-07-15) — Phase 5 documents
   the fallback but the underlying tooling issue itself is unfixed.
3. **`specs/BACKLOG.md` B-27** — `charts/aigent-squad/values.yaml`'s
   `image.repository` still a personal Docker Hub account (T6 review finding
   F1 — this was misattributed to the wrong repo/phase in an earlier draft of
   this table; corrected).
