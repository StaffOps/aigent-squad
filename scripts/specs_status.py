#!/usr/bin/env python3
"""Validate the spec-status single source of truth and emit the ROADMAP table.

SSOT = YAML frontmatter in each ``specs/<NN-name>/requirements.md`` (spec 32,
``32-spec-lifecycle-ssot``). Status is authored **only** there; every other
view (ROADMAP's table, HANDOFF, prose) derives from or is validated against it.

Deliberately does NOT trust ``tasks.md`` checkbox counts as a status signal:
this repo's checkbox state has drifted far from reality (shipped specs with
every box unchecked — the very drift this spec exists to kill), so "open
tasks" is expressed in frontmatter via ``deferred[]``, not by counting boxes.
See ``specs/32-spec-lifecycle-ssot/design.md`` Decision 5.

Usage::

    python3 scripts/specs_status.py            # validate; exit 0 = consistent
    python3 scripts/specs_status.py --table     # print the canonical status table

Dependency-free beyond PyYAML (already pinned in requirements.txt) + stdlib.
Path arguments are parameterized so the validator can be unit-tested against
fixture trees (see tests/test_specs_status.py).
"""
from __future__ import annotations

import pathlib
import re
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
SPECS = REPO / "specs"

ALLOWED = {
    "not-started", "design-only", "in-progress", "done",
    "done-with-deferrals", "superseded", "dormant", "removed",
}

# Canonical table is fenced by these markers in ROADMAP.md. Absent until the
# Phase 2 slim-down (spec 32 T8) lands them — the sync check stays inert until
# then so the Phase 1 gate is self-consistent.
TABLE_START = "<!-- specs-status:start -->"
TABLE_END = "<!-- specs-status:end -->"

SPEC_DIR_RE = re.compile(r"^\d\d-")


def load_frontmatter(path: pathlib.Path) -> dict | None:
    """Return parsed YAML frontmatter, or None if the file has none."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    return yaml.safe_load(parts[1]) or {}


def collect(specs_dir: pathlib.Path = SPECS) -> dict[str, dict | None]:
    """Map spec dir name -> frontmatter dict (None = no frontmatter present)."""
    specs: dict[str, dict | None] = {}
    for d in sorted(specs_dir.iterdir()):
        if not d.is_dir() or not SPEC_DIR_RE.match(d.name):
            continue
        req = d / "requirements.md"
        specs[d.name] = load_frontmatter(req) if req.exists() else None
    return specs


def validate(
    specs: dict[str, dict | None],
    specs_dir: pathlib.Path = SPECS,
) -> list[str]:
    errors: list[str] = []
    backlog_path = specs_dir / "BACKLOG.md"
    backlog = backlog_path.read_text(encoding="utf-8") if backlog_path.exists() else None

    for name, fm in specs.items():
        d = specs_dir / name
        if fm is None:
            # Bugfix-tier (single bugfix.md, no requirements.md) needs no frontmatter.
            if not (d / "requirements.md").exists() and (d / "bugfix.md").exists():
                continue
            errors.append(f"{name}: requirements.md is missing YAML frontmatter")
            continue

        status = fm.get("status")
        if status not in ALLOWED:
            errors.append(
                f"{name}: unknown status {status!r} (allowed: {sorted(ALLOWED)})"
            )
            continue

        if fm.get("spec") != name:
            errors.append(f"{name}: frontmatter spec:{fm.get('spec')!r} != dir name")

        deferred = fm.get("deferred") or []

        if status == "superseded" and not fm.get("superseded_by"):
            errors.append(f"{name}: status 'superseded' requires superseded_by")

        # "done-with-open-tasks" is a frontmatter invariant, not a checkbox count:
        if status == "done-with-deferrals" and not deferred:
            errors.append(
                f"{name}: 'done-with-deferrals' requires a non-empty deferred[]"
            )
        if status == "done" and deferred:
            errors.append(
                f"{name}: plain 'done' must not carry deferred[]; "
                "use 'done-with-deferrals'"
            )

        # deferred <-> BACKLOG cross-check (only once BACKLOG.md exists — T7/Phase 2).
        if deferred and backlog is not None:
            for item in deferred:
                key = str(item).split("(")[0].strip()
                if key and key not in backlog:
                    errors.append(
                        f"{name}: deferred item {item!r} not referenced in specs/BACKLOG.md"
                    )

    return errors


def canonical_table(specs: dict[str, dict | None]) -> str:
    rows = [
        "| Spec | Status | Completed | Notes |",
        "|------|--------|-----------|-------|",
    ]
    for name in sorted(specs):
        fm = specs[name]
        if fm is None:
            rows.append(f"| {name} | bugfix | — | single-file bugfix.md |")
            continue
        status = fm.get("status", "?")
        completed = fm.get("completed") or "—"
        notes = ""
        if status == "superseded":
            notes = f"→ {fm.get('superseded_by')}"
        elif status == "done-with-deferrals":
            notes = "deferred: " + ", ".join(str(x) for x in (fm.get("deferred") or []))
        elif fm.get("depends_on"):
            notes = "depends_on: " + ", ".join(fm["depends_on"])
        rows.append(f"| {name} | {status} | {completed} | {notes} |")
    return "\n".join(rows)


def roadmap_mismatch(
    specs: dict[str, dict | None],
    specs_dir: pathlib.Path = SPECS,
) -> str | None:
    roadmap = specs_dir / "ROADMAP.md"
    if not roadmap.exists():
        return None
    text = roadmap.read_text(encoding="utf-8")
    if TABLE_START not in text or TABLE_END not in text:
        return None  # canonical block not present yet (Phase 2 / T8)
    current = text.split(TABLE_START, 1)[1].split(TABLE_END, 1)[0].strip()
    if current != canonical_table(specs).strip():
        return "ROADMAP canonical status table is out of sync with frontmatter (run --table)"
    return None


def run(specs_dir: pathlib.Path = SPECS, emit_table: bool = False) -> tuple[int, str]:
    """Core entry point. Returns (exit_code, output_text)."""
    specs = collect(specs_dir)

    if emit_table:
        return 0, canonical_table(specs)

    errors = validate(specs, specs_dir)
    mism = roadmap_mismatch(specs, specs_dir)
    if mism:
        errors.append(mism)

    if errors:
        lines = ["spec status validation FAILED:"] + [f"  - {e}" for e in errors]
        return 1, "\n".join(lines)

    with_fm = sum(1 for v in specs.values() if v is not None)
    bugfix = sum(1 for v in specs.values() if v is None)
    return 0, f"spec status OK — {with_fm} specs with frontmatter, {bugfix} bugfix-tier"


def main(argv: list[str]) -> int:
    code, out = run(emit_table="--table" in argv)
    print(out, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
