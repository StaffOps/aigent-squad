"""Tests for scripts/specs_status.py — spec-status SSOT validator.

Written against the DOCUMENTED CONTRACT (spec 32 T6), NOT by mirroring
implementation details.  Uses tmp_path fixture trees so no real specs/ is touched.
"""
from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Import the module under test without modifying it or adding __init__.py
# ---------------------------------------------------------------------------
_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "specs_status.py"

_spec = importlib.util.spec_from_file_location("specs_status", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

# Public API
run = _mod.run
collect = _mod.collect
validate = _mod.validate
canonical_table = _mod.canonical_table
roadmap_mismatch = _mod.roadmap_mismatch
load_frontmatter = _mod.load_frontmatter

# Constants
ALLOWED = _mod.ALLOWED
TABLE_START = _mod.TABLE_START
TABLE_END = _mod.TABLE_END


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_spec(
    specs_dir: pathlib.Path,
    name: str,
    frontmatter: dict[str, Any] | None = None,
    *,
    bugfix_only: bool = False,
    no_frontmatter: bool = False,
) -> None:
    """Create a minimal spec directory under specs_dir.

    If frontmatter is provided, serialise it as YAML frontmatter in
    requirements.md.  If bugfix_only, write only bugfix.md (no requirements.md).
    If no_frontmatter, write requirements.md with plain content (no ---).
    """
    d = specs_dir / name
    d.mkdir(parents=True, exist_ok=True)

    if bugfix_only:
        (d / "bugfix.md").write_text("# Bugfix\nSome fix.\n", encoding="utf-8")
        return

    req = d / "requirements.md"
    if no_frontmatter:
        req.write_text("# Some spec\nNo frontmatter here.\n", encoding="utf-8")
        return

    if frontmatter is not None:
        fm_yaml = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
        req.write_text(f"---\n{fm_yaml}---\n\n# Spec\n", encoding="utf-8")


def _valid_fm(name: str, status: str = "in-progress", **extra: Any) -> dict[str, Any]:
    """Return a minimal valid frontmatter dict for the given spec dir name."""
    d: dict[str, Any] = {"spec": name, "status": status}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# 1. Consistent tree — run() exit 0, empty errors
# ---------------------------------------------------------------------------


class TestConsistentTree:
    def test_valid_mixed_statuses(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-alpha", _valid_fm("01-alpha", "not-started"))
        _write_spec(tmp_path, "02-beta", _valid_fm("02-beta", "in-progress"))
        _write_spec(tmp_path, "03-gamma", _valid_fm("03-gamma", "done"))

        code, output = run(tmp_path)
        assert code == 0
        assert "OK" in output

        errors = validate(collect(tmp_path), tmp_path)
        assert errors == []


# ---------------------------------------------------------------------------
# 2. Status not in ALLOWED -> error
# ---------------------------------------------------------------------------


class TestInvalidStatus:
    def test_unknown_status(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-bad", _valid_fm("01-bad", "invented-status"))
        errors = validate(collect(tmp_path), tmp_path)
        assert len(errors) == 1
        assert "unknown status" in errors[0]

    def test_run_exits_1(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-bad", _valid_fm("01-bad", "banana"))
        code, _ = run(tmp_path)
        assert code == 1


# ---------------------------------------------------------------------------
# 3. Superseded requires superseded_by
# ---------------------------------------------------------------------------


class TestSuperseded:
    def test_missing_superseded_by(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-old", _valid_fm("01-old", "superseded"))
        errors = validate(collect(tmp_path), tmp_path)
        assert any("superseded_by" in e for e in errors)

    def test_superseded_with_superseded_by(self, tmp_path: pathlib.Path) -> None:
        _write_spec(
            tmp_path,
            "01-old",
            _valid_fm("01-old", "superseded", superseded_by="02-new"),
        )
        errors = validate(collect(tmp_path), tmp_path)
        assert errors == []


# ---------------------------------------------------------------------------
# 4. Plain 'done' must not carry deferrals
# ---------------------------------------------------------------------------


class TestDoneDeferrals:
    def test_done_with_deferred_list_is_error(self, tmp_path: pathlib.Path) -> None:
        _write_spec(
            tmp_path,
            "01-x",
            _valid_fm("01-x", "done", deferred=["some task"]),
        )
        errors = validate(collect(tmp_path), tmp_path)
        assert any("done" in e and "deferred" in e for e in errors)


# ---------------------------------------------------------------------------
# 5. done-with-deferrals requires non-empty deferred[]
# ---------------------------------------------------------------------------


class TestDoneWithDeferrals:
    def test_empty_deferred_is_error(self, tmp_path: pathlib.Path) -> None:
        _write_spec(
            tmp_path,
            "01-x",
            _valid_fm("01-x", "done-with-deferrals", deferred=[]),
        )
        errors = validate(collect(tmp_path), tmp_path)
        assert any("done-with-deferrals" in e for e in errors)

    def test_non_empty_deferred_with_backlog(self, tmp_path: pathlib.Path) -> None:
        item = "Implement caching layer"
        _write_spec(
            tmp_path,
            "01-x",
            _valid_fm("01-x", "done-with-deferrals", deferred=[item]),
        )
        # Write a BACKLOG.md containing the item
        (tmp_path / "BACKLOG.md").write_text(
            f"# Backlog\n\n- {item}\n", encoding="utf-8"
        )
        errors = validate(collect(tmp_path), tmp_path)
        assert errors == []


# ---------------------------------------------------------------------------
# 6. deferred[] item not in BACKLOG.md -> error; BACKLOG absent -> skip check
# ---------------------------------------------------------------------------


class TestDeferredBacklogCrosscheck:
    def test_item_not_in_backlog_is_error(self, tmp_path: pathlib.Path) -> None:
        _write_spec(
            tmp_path,
            "01-x",
            _valid_fm("01-x", "done-with-deferrals", deferred=["Missing thing"]),
        )
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n- Unrelated stuff\n", encoding="utf-8"
        )
        errors = validate(collect(tmp_path), tmp_path)
        assert any("BACKLOG" in e for e in errors)

    def test_backlog_absent_skips_check(self, tmp_path: pathlib.Path) -> None:
        _write_spec(
            tmp_path,
            "01-x",
            _valid_fm("01-x", "done-with-deferrals", deferred=["Some deferred task"]),
        )
        # No BACKLOG.md at all
        errors = validate(collect(tmp_path), tmp_path)
        assert not any("BACKLOG" in e for e in errors)

    def test_strip_parenthetical_before_matching(self, tmp_path: pathlib.Path) -> None:
        """The cross-check strips from '(' onward before substring matching."""
        item_with_paren = "Implement caching (Phase 2)"
        _write_spec(
            tmp_path,
            "01-x",
            _valid_fm("01-x", "done-with-deferrals", deferred=[item_with_paren]),
        )
        # BACKLOG contains just "Implement caching" (no parenthetical)
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n- Implement caching\n", encoding="utf-8"
        )
        errors = validate(collect(tmp_path), tmp_path)
        assert not any("BACKLOG" in e for e in errors)


# ---------------------------------------------------------------------------
# 7. frontmatter spec: value != directory name -> error
# ---------------------------------------------------------------------------


class TestSpecNameMismatch:
    def test_mismatch_is_error(self, tmp_path: pathlib.Path) -> None:
        _write_spec(
            tmp_path,
            "01-actual-dir",
            {"spec": "01-wrong-name", "status": "in-progress"},
        )
        errors = validate(collect(tmp_path), tmp_path)
        assert any("spec:" in e and "dir name" in e for e in errors)

    def test_match_is_ok(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-correct", _valid_fm("01-correct"))
        errors = validate(collect(tmp_path), tmp_path)
        assert errors == []


# ---------------------------------------------------------------------------
# 8. Bugfix-tier (bugfix.md only, no requirements.md) -> no error, skipped
# ---------------------------------------------------------------------------


class TestBugfixTier:
    def test_bugfix_only_no_error(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-fix", bugfix_only=True)
        errors = validate(collect(tmp_path), tmp_path)
        assert errors == []

    def test_bugfix_collected_as_none(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-fix", bugfix_only=True)
        specs = collect(tmp_path)
        assert specs["01-fix"] is None


# ---------------------------------------------------------------------------
# 9. requirements.md present but NO frontmatter -> error
# ---------------------------------------------------------------------------


class TestMissingFrontmatter:
    def test_no_frontmatter_is_error(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-missing", no_frontmatter=True)
        errors = validate(collect(tmp_path), tmp_path)
        assert any("frontmatter" in e for e in errors)


# ---------------------------------------------------------------------------
# 10. roadmap_mismatch
# ---------------------------------------------------------------------------


class TestRoadmapMismatch:
    def test_roadmap_absent_returns_none(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-a", _valid_fm("01-a"))
        specs = collect(tmp_path)
        assert roadmap_mismatch(specs, tmp_path) is None

    def test_no_markers_returns_none(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-a", _valid_fm("01-a"))
        (tmp_path / "ROADMAP.md").write_text("# Roadmap\nStuff.\n", encoding="utf-8")
        specs = collect(tmp_path)
        assert roadmap_mismatch(specs, tmp_path) is None

    def test_mismatched_table_returns_error(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-a", _valid_fm("01-a"))
        specs = collect(tmp_path)
        (tmp_path / "ROADMAP.md").write_text(
            f"# Roadmap\n{TABLE_START}\nwrong table\n{TABLE_END}\n", encoding="utf-8"
        )
        result = roadmap_mismatch(specs, tmp_path)
        assert result is not None
        assert "out of sync" in result

    def test_mismatched_table_run_exits_1(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-a", _valid_fm("01-a"))
        (tmp_path / "ROADMAP.md").write_text(
            f"# Roadmap\n{TABLE_START}\nwrong\n{TABLE_END}\n", encoding="utf-8"
        )
        code, _ = run(tmp_path)
        assert code == 1

    def test_matching_table_returns_none(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-a", _valid_fm("01-a"))
        specs = collect(tmp_path)
        table = canonical_table(specs)
        (tmp_path / "ROADMAP.md").write_text(
            f"# Roadmap\n{TABLE_START}\n{table}\n{TABLE_END}\n", encoding="utf-8"
        )
        assert roadmap_mismatch(specs, tmp_path) is None


# ---------------------------------------------------------------------------
# 11. canonical_table rendering
# ---------------------------------------------------------------------------


class TestCanonicalTable:
    def test_header_and_rows(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-alpha", _valid_fm("01-alpha", "in-progress"))
        _write_spec(tmp_path, "02-beta", _valid_fm("02-beta", "done"))
        specs = collect(tmp_path)
        table = canonical_table(specs)
        lines = table.split("\n")
        # Header + separator + 2 data rows
        assert len(lines) == 4
        assert "Spec" in lines[0] and "Status" in lines[0]
        assert "01-alpha" in lines[2]
        assert "02-beta" in lines[3]

    def test_superseded_shows_arrow(self, tmp_path: pathlib.Path) -> None:
        _write_spec(
            tmp_path,
            "01-old",
            _valid_fm("01-old", "superseded", superseded_by="02-new"),
        )
        specs = collect(tmp_path)
        table = canonical_table(specs)
        assert "→ 02-new" in table

    def test_done_with_deferrals_lists_items(self, tmp_path: pathlib.Path) -> None:
        _write_spec(
            tmp_path,
            "01-x",
            _valid_fm("01-x", "done-with-deferrals", deferred=["task A", "task B"]),
        )
        specs = collect(tmp_path)
        table = canonical_table(specs)
        assert "task A" in table
        assert "task B" in table
        assert "deferred:" in table

    def test_bugfix_tier_shows_bugfix(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-fix", bugfix_only=True)
        specs = collect(tmp_path)
        table = canonical_table(specs)
        assert "bugfix" in table


# ---------------------------------------------------------------------------
# 12. emit_table via run()
# ---------------------------------------------------------------------------


class TestEmitTable:
    def test_emit_table_returns_0_and_canonical(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-a", _valid_fm("01-a", "not-started"))
        _write_spec(tmp_path, "02-b", _valid_fm("02-b", "done"))

        code, output = run(tmp_path, emit_table=True)
        assert code == 0

        specs = collect(tmp_path)
        expected = canonical_table(specs)
        assert output == expected


# ---------------------------------------------------------------------------
# Module constants sanity
# ---------------------------------------------------------------------------


class TestConstants:
    def test_allowed_has_8_statuses(self) -> None:
        assert len(ALLOWED) == 8
        assert "not-started" in ALLOWED
        assert "design-only" in ALLOWED
        assert "in-progress" in ALLOWED
        assert "done" in ALLOWED
        assert "done-with-deferrals" in ALLOWED
        assert "superseded" in ALLOWED
        assert "dormant" in ALLOWED
        assert "removed" in ALLOWED

    def test_table_markers(self) -> None:
        assert TABLE_START == "<!-- specs-status:start -->"
        assert TABLE_END == "<!-- specs-status:end -->"


# ---------------------------------------------------------------------------
# load_frontmatter unit tests
# ---------------------------------------------------------------------------


class TestLoadFrontmatter:
    def test_valid_frontmatter(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("---\nstatus: done\n---\n\n# Content\n", encoding="utf-8")
        result = load_frontmatter(p)
        assert result == {"status": "done"}

    def test_no_frontmatter(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("# Just a heading\nContent.\n", encoding="utf-8")
        result = load_frontmatter(p)
        assert result is None


# ---------------------------------------------------------------------------
# Reviewer follow-ups (S1/S2/S3): auxiliary rendering + dir-filter branches
# ---------------------------------------------------------------------------


class TestCanonicalTableAuxBranches:
    def test_depends_on_shown_in_notes(self, tmp_path: pathlib.Path) -> None:
        """S1: a non-superseded, non-deferral spec with depends_on renders it."""
        _write_spec(
            tmp_path,
            "02-child",
            _valid_fm("02-child", "not-started", depends_on=["01-parent"]),
        )
        table = canonical_table(collect(tmp_path))
        assert "depends_on: 01-parent" in table

    def test_completed_date_shown(self, tmp_path: pathlib.Path) -> None:
        """S2: a done spec with a completed date shows it (not the — placeholder)."""
        _write_spec(
            tmp_path,
            "01-shipped",
            _valid_fm("01-shipped", "done", completed="2026-07-10"),
        )
        table = canonical_table(collect(tmp_path))
        assert "2026-07-10" in table

    def test_missing_completed_shows_dash(self, tmp_path: pathlib.Path) -> None:
        _write_spec(tmp_path, "01-x", _valid_fm("01-x", "done"))
        table = canonical_table(collect(tmp_path))
        # the done row falls back to the em-dash placeholder
        assert "| 01-x | done | — |" in table


class TestSpecDirFilter:
    def test_non_spec_dirs_ignored(self, tmp_path: pathlib.Path) -> None:
        """S3: only NN-prefixed dirs are treated as specs."""
        _write_spec(tmp_path, "01-real", _valid_fm("01-real"))
        # decoys that must NOT be collected as specs
        (tmp_path / "archive").mkdir()
        (tmp_path / "archive" / "requirements.md").write_text(
            "---\nspec: nope\nstatus: banana\n---\n", encoding="utf-8"
        )
        (tmp_path / "utils").mkdir()
        (tmp_path / "BACKLOG.md").write_text("# Backlog\n", encoding="utf-8")

        specs = collect(tmp_path)
        assert set(specs) == {"01-real"}
        # the banana status in archive/ must not leak into validation
        assert validate(specs, tmp_path) == []
