# Spec process — lifecycle, template, verification

The process itself, written once. Individual specs reference this file
instead of restating any of it. Written by spec 32 (`32-spec-lifecycle-ssot`)
in response to a recurring failure mode: status living in ≥4 places
(each spec's `tasks.md`, two `ROADMAP.md` tables, `HANDOFF.md`, ad-hoc notes)
with no mechanical check, drifting apart on every milestone.

## Single source of truth: frontmatter

Every spec's `requirements.md` starts with YAML frontmatter — that is the
**only** place status is authored. Everywhere else (`ROADMAP.md`'s status
table, this repo's own claims) either derives from it or is validated
against it by `scripts/specs_status.py` (CI-gated).

```yaml
---
spec: NN-name
status: in-progress        # not-started | design-only | in-progress | done |
                           # done-with-deferrals | superseded | dormant | removed
completed: null            # ISO date (YYYY-MM-DD), set when status becomes done*
superseded_by: null        # e.g. "22-agent-capability-manifest" — REQUIRED if status: superseded
depends_on: []             # other spec ids, e.g. ["02-unify-agent-architecture"]
deferred: []               # e.g. ["T11 formal smoke test"] — REQUIRED if status: done-with-deferrals;
                           # every entry here must also appear in specs/BACKLOG.md's Deferred register
---
```

**Status vocabulary** (exactly these eight values — the script rejects
anything else):

| Status | Meaning |
|--------|---------|
| `not-started` | requirements/design may exist; no `tasks.md` work begun |
| `design-only` | `design.md` exists, deliberately not implemented (e.g. spec 28) |
| `in-progress` | some `tasks.md` checkboxes done, spec actively being worked |
| `done` | every non-deferred `tasks.md` checkbox is checked, zero open tails |
| `done-with-deferrals` | shipped, but named tails remain (`deferred:` populated, cross-checked against BACKLOG.md) |
| `superseded` | replaced by another spec entirely (`superseded_by:` required) |
| `dormant` | intentionally not pursued until a named trigger fires (trigger lives in BACKLOG.md's Dormant section, not in frontmatter) |
| `removed` | spec directory kept for history but the feature was reverted/deleted |

**Rule**: `done` means zero open non-deferred tasks. If ANY task is
consciously left open (not abandoned, just deferred), the status is
`done-with-deferrals`, never a plain `done` with an asterisk in prose.

## Lifecycle

```
not-started ──▶ design-only ──▶ in-progress ──▶ done
                                      │              │
                                      │              ├──▶ done-with-deferrals
                                      ▼              │
                                  dormant             ▼
                                                  (operate/measure — spec 33)
                                                       │
                                                       ▼
                                                  superseded / removed
                                                  (when a later spec replaces it)
```

A spec doesn't end at `done`. Shipped specs enter an **operate/measure**
stage (spec 33, `33-operational-review-loop`): recurring review sweeps
BACKLOG.md's Deferred register and Dormant section, checks whether a
dormant trigger fired, and reconciles real operational data (cost, error
rates) against what the spec assumed at design time. A spec is never
"done and forgotten" — it's "done, and now measured."

## Two tiers: full spec vs `bugfix.md`

Not every change needs three files. Use judgment:

- **Full spec** (`requirements.md` + `design.md` + `tasks.md`): new
  capability, architecture change, anything touching an input surface, auth,
  or data egress (see the mandatory security-review rule below), or work
  spanning >1 session.
- **`bugfix.md`** (single file, in the spec dir it fixes, or a fresh
  `specs/NN-short-name/bugfix.md` if the fix doesn't belong to an existing
  spec): a defect fix, small enough to describe in one file — what broke,
  root cause, the fix, how it was verified. No frontmatter required (it
  inherits the parent spec's status, or gets logged as an `F-NNN` finding in
  `specs/BACKLOG.md` if it doesn't belong to any spec).

When unsure, default to the full spec — the cost of the extra file is small
compared to the cost of an undocumented architecture decision.

## Template (full spec)

```
specs/NN-short-name/
  requirements.md   # frontmatter (above) + Thesis + User Stories (WHEN/THEN) + Acceptance Criteria + Out of scope
  design.md         # Architecture + Rationale (numbered decisions, each with justification + trade-offs) + Invariants + Verification + Risks
  tasks.md          # Checkbox list, phased, "Order" section noting parallelism, independent-review/test-author tasks called out explicitly
```

`requirements.md`'s Acceptance Criteria and `tasks.md`'s checkboxes should be
in near 1:1 correspondence — the frontmatter's `status` is judged against
`tasks.md`, not against prose elsewhere.

## Verification-independence pipeline (written once)

Every full spec's `tasks.md` ends with two non-author-overlapping gates,
called out explicitly as separate tasks:

1. **Independent test authorship**: a different session/agent than the one
   that wrote the implementation writes the tests (or reviews/extends tests
   the implementer drafted) — catches tests that only encode the
   implementer's own blind spots.
2. **Independent code review**: a fresh review pass (no implementation
   context carried over) against steering docs, idioms, security, and the
   spec's own Acceptance Criteria.

Both are checkboxed tasks in `tasks.md`, not implicit. A spec is not `done`
until both have run, even if all functional tasks are checked.

## Mandatory security review

Any spec that touches **an input surface, authentication, or data egress**
requires an explicit security-focused review task in `tasks.md` (separate
from the general independent code review) before it can reach `done`.
Examples that trigger this: new API endpoints, new datasource types, new
places raw model output reaches a user, anything in the trust boundary
described in `AGENTS.md` invariant #10, any change to `src/core/guardrail.py`
/ `input_scanner.py` / `output_filter.py` / `canary.py`.

## Numbering and language

- Specs are numbered sequentially, never reused, even if a spec is later
  superseded or removed (the number stays a historical pointer).
- **Language convention**: specs 01–19ish written in Portuguese are FROZEN
  historical record — not retranslated (explicitly out of scope, keeps this
  spec's own footprint small). All specs from `32-spec-lifecycle-ssot`
  onward, and any spec touched significantly after 2026-07-03, are English.
  Mixed-language specs (partially retrofitted) are not a bug — don't
  "fix" the language of an untouched historical file as a side effect of an
  unrelated change.

## Document homes

Adopted from `template-project` conventions, backfilled 2026-07-03:

| Document type | Home |
|----------------|------|
| Product requirements (why, personas, success metrics) | `docs/prd/` (currently `docs/prd/aigent-squad.md`) |
| Architecture Decision Records (a decision that outlives one spec, or is referenced by multiple) | `docs/architecture/decisions/000N-slug.md` — see `docs/architecture/decisions/README.md` for the index |
| A decision scoped to exactly one spec | `design.md`'s Rationale section (numbered decisions) — no separate ADR needed |
| Findings, product backlog items, pending decisions, dormant work, deferred tails | `specs/BACKLOG.md` (the ONLY home — see Decision 3 in spec 32's `design.md`) |
| Long-term vision / phased maturity levels | `specs/VISION.md` |
| Session continuity | `HANDOFF.md` — current session + next steps only; prior sessions archived to `archive/handoffs/YYYY-MM-DD.md` (overwritten, not appended — see spec 32 `design.md` Decision 4) |
| Frozen historical deliberation (superseded analysis, not living docs) | `Status: frozen (date)` banner at the top, left in place under `specs/` |

When a new architectural decision is made that would matter to someone
reading the codebase cold (not just this one spec's contributors), write an
ADR. When it's a local implementation choice, a design.md Rationale entry is
enough — don't create an ADR for every decision.

## Validating locally

```bash
python3 scripts/specs_status.py            # exit 0 = consistent, prints nothing
python3 scripts/specs_status.py --table    # prints the canonical ROADMAP status table
```

CI runs the same script as a gate (see `.github/workflows/test.yml`) — a
spec dir with inconsistent frontmatter (unknown status value, `done` with
open non-deferred tasks, `superseded` without `superseded_by`, a `deferred:`
entry missing from `specs/BACKLOG.md`'s Deferred register) fails the build.
