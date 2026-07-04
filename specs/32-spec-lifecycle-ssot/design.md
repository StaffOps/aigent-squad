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
Negative tests: flip a frontmatter to `done` with an open task → script exits ≠0;
add a `deferred:` item absent from BACKLOG.md → exits ≠0.

## Risks

- Backfill mislabels a spec → mitigated by deriving from tasks.md checkboxes (mechanical),
  and the reconciliation pass done in this spec's T2 was already ground-truthed on
  2026-07-03 (full read).
- The script becomes its own maintenance burden → kept dependency-free (PyYAML + stdlib),
  <150 LoC, no config.
