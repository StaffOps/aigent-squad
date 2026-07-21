# Design: Spec Lifecycle SSOT

## Architecture

```
specs/<NN-name>/requirements.md   ← YAML frontmatter = status SSOT
        │
        ├── scripts/specs_status.py ──┬── validate (CI gate, exit≠0 on drift)
        │                             └── --table → single ROADMAP status table
        │
specs/README.md      ← the process itself, written once
specs/BACKLOG.md     ← dormant + findings (F-NNN) + deferred register
specs/VISION.md      ← Levels 1–4 (moved out of ROADMAP)
specs/ROADMAP.md     ← plan only: phases, priority, deps + ONE validated table
HANDOFF.md           ← overwritten each session; history → archive/handoffs/
```

## Frontmatter schema (in `requirements.md` of each spec)

```yaml
---
spec: 32-spec-lifecycle-ssot
status: in-progress        # not-started | design-only | in-progress | done |
                           # done-with-deferrals | superseded | dormant | removed
completed: null            # ISO date when status becomes done*
superseded_by: null        # e.g. "22-agent-capability-manifest" (required if superseded)
depends_on: []             # spec ids
deferred: []               # ["T11 formal smoke", ...] (required if done-with-deferrals)
---
```

## Rationale (decisions)

### Decision 1: frontmatter in the spec dir, not a central STATUS.md

**Choice**: each spec owns its status; central views are derived/validated.

**Justification, in order of strength**:
1. **Locality kills drift at the root.** Every observed contradiction came from a
   *central* table lagging the spec's own record. A central file would be the same
   failure mode with one more file. The spec dir is where completion actually happens
   (tasks get checked there), so status must live where the change happens.
2. **Machine-checkable.** YAML frontmatter is trivially parseable — the CI gate becomes
   ~80 lines of Python, no dependencies beyond PyYAML (already in requirements).
3. **Same pattern the project already trusts**: `agent.yaml` + SKILL.md frontmatter —
   config-as-files with startup validation. Operators already think this way.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| 25 existing specs need backfill | One-time, ~1h; the script then protects it forever |
| Frontmatter in requirements.md vs a 4th file | Fewer files wins; requirements.md is the spec's front page |

**When this would be wrong**: if specs move to an external tracker (GitHub Issues/Linear)
— then frontmatter becomes a mirror and should be dropped, not synced.

### Decision 2: validate-by-default, generate-on-demand

**Choice**: the ROADMAP status table is maintained by hand but **validated** by the script
(CI fails on mismatch); `--table` can regenerate it. Not auto-committed generation.

**Justification**: auto-generated files in git create noisy diffs and merge friction for a
solo-dev direct-commit flow; a validator gives the same guarantee (no silent drift) with
zero pipeline machinery. Regeneration exists for convenience when the mismatch is large.

**Trade-off accepted**: a failing CI run instead of an auto-fix — deliberate, per the
existing "pre-push gate" culture (lint before push).

### Decision 3: BACKLOG.md is the ONLY home for dormant/findings/deferred

**Choice**: one file, three sections, IDs for findings (`F-NNN`).

**Justification**:
1. Today deferred work has *negative* discoverability — it exists only as prose inside
   closed specs (proven: nobody resurfaced spec 06/17/18's deferred smoke tests in 3
   weeks of milestones; the aws XML bug has no home at all).
2. One file = one place the spec-33 operational review sweeps. Two files (findings vs
   backlog) would recreate the fragmentation this spec exists to kill.
3. The dormant section preserves the ROADMAP's existing (good) blocker-trigger wording —
   moved, not rewritten, keeping the "do NOT resurface until a trigger fires" contract.

**Trade-off accepted**: BACKLOG.md grows — bounded by triage in the spec-33 review loop.

### Decision 4: HANDOFF overwritten, history archived

**Choice**: HANDOFF = last session + next steps; previous content moves to
`archive/handoffs/YYYY-MM-DD.md` at the start of each new session's handoff write.

**Justification**: a handoff's value is "what do I need to resume", which is inherently
*current*; stale sections actively mislead (proven: "dev and main in sync" was false at
read time). CHANGES.md already covers permanent history — appending to HANDOFF duplicates
it badly.

### Decision 5: the "done-with-open-tasks" check is frontmatter-internal, not a tasks.md checkbox count

**Choice**: the CI gate validates status consistency **within frontmatter** (a plain
`done` may carry no `deferred[]`; `done-with-deferrals` must carry a non-empty one). It
does **not** count `tasks.md` checkboxes to decide whether a `done` spec is "really" done.

**Justification, in order of strength**:
1. **The backfill (T2) proved checkbox state is not ground truth.** Measured 2026-07-16:
   spec 23 (`done`) had 10/10 boxes *unchecked*; spec 24 (`done`) 12/12 unchecked; spec 31
   (shipped, `done-with-deferrals`) 29/30 unchecked — all demonstrably shipped. Meanwhile
   spec 34 (`not-started`) had all 7 boxes *checked*. A "done ⇒ all boxes checked" gate
   would fail on genuinely-done specs and pass on unstarted ones — it would enforce the
   very drift this spec exists to kill.
2. **Consistency with Decision 1.** Frontmatter is the SSOT; deriving the gate from a
   *different* (unreliable) signal would reintroduce two-sources-of-truth by the back door.
3. **Honesty (steering `evidence-before-assertion`).** Blanket-ticking historical boxes to
   satisfy a mechanical gate would assert completion of individual sub-tasks — including
   spec 14's security tasks — that were never verified box-by-box. "Open work" belongs in
   `deferred[]` (a deliberate, authored statement), not inferred from a checkbox left stale.

**Trade-off accepted**:
| Cost | Reality |
|------|---------|
| The gate can't catch "marked done but a real task is silently unfinished" | That failure mode is caught by the independent-review task in each spec's `tasks.md`, not by counting boxes. The gate's job is *cross-view consistency*, not task auditing. |
| `tasks.md` checkbox state stays drifted for historical specs | Reconciling ~28 historical `tasks.md` is out of scope for spec 32 (and risky for the security spec). Going-forward specs keep boxes current; the operate/measure loop (spec 33) can reconcile opportunistically. |

**Supersedes** the Risks note's original assumption that backfill would "derive from
tasks.md checkboxes (mechanical)" — that assumption was tested against the real tree and
found false.

**When this would be wrong**: if a future discipline keeps `tasks.md` boxes reliably in
sync (e.g. spec 36's agent-native loop enforces it), a checkbox-vs-status cross-check could
be *added* as a second gate — but even then, additive, never replacing frontmatter as SSOT.

## Invariants

- One status vocabulary, defined once in specs/README.md — the script rejects others.
- `done` means zero open non-deferred tasks; anything else is `done-with-deferrals`.
- Every `deferred:` item appears in BACKLOG.md (script cross-checks).
- ROADMAP contains no status not derivable from frontmatter.
- BACKLOG dormant items keep explicit blocker-triggers (never bare "someday" rows).
- The process doc (specs/README.md) is the ONLY place the verification pipeline is
  spelled out; specs reference it.

## Verification

```bash
# the gate itself, locally (same as CI):
python3 scripts/specs_status.py            # exit 0 = consistent
python3 scripts/specs_status.py --table    # prints the canonical table
```
Negative tests (script exits ≠0): unknown status value; `superseded` without
`superseded_by`; plain `done` carrying a `deferred:` entry; `done-with-deferrals` with an
empty `deferred:`; a `deferred:` item absent from BACKLOG.md; the ROADMAP canonical table
(once its markers exist) out of sync with frontmatter.

## Risks

- Backfill mislabels a spec → mitigated by using the 2026-07-03 full-read ground truth
  (ROADMAP's Audit Summary + Remaining-specs tables) as the status source, NOT raw
  `tasks.md` checkbox counts (which were measured drifted at backfill time — see
  Decision 5). Statuses cross-checked against the ROADMAP tables during T2.
- The script becomes its own maintenance burden → kept dependency-free (PyYAML + stdlib),
  <150 LoC, no config.
