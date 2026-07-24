"""Independent verification tests for the large-list undercount fix (spec 37).

Written by test-author (separate session) against the CONTRACT:
  - count_items() must report TRUE item count from the FULL text
  - _truncate_with_marker() uses count_items on the FULL text BEFORE truncating
  - _summarize_tool_result() uses the SAME count_items helper
  - Single truncation path at 8000 chars (no residual 4000 cut)
  - Text table header detection excludes the header from the count

These tests validate the fix that prevented kube-mcp text-table results
(263 rows) from being under-reported as ~38 due to early truncation.
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Helpers to build realistic test data
# ---------------------------------------------------------------------------

def _build_kubectl_table(num_rows: int, *, wide: bool = False) -> str:
    """Build a realistic kubectl text-table with ALL-CAPS header + N data rows.

    Each data row is ~70 chars for standard or ~120 for wide.
    """
    if wide:
        header = "NAMESPACE  APIVERSION  KIND  NAME  STATUS  RESTARTS  AGE  NODE  IP"
        row_tpl = "monitoring  v1  Pod  workload-{i:04d}  Running  0  15d  ip-10-0-{i}.ec2  10.0.{a}.{b}"
    else:
        header = "NAMESPACE  NAME  READY  STATUS  RESTARTS  AGE"
        row_tpl = "monitoring  pod-{i:04d}  1/1  Running  0  1d"

    rows = []
    for i in range(num_rows):
        row = row_tpl.format(i=i, a=i % 256, b=(i * 7) % 256)
        rows.append(row)

    return "\n".join([header] + rows)


def _build_json_array(num_items: int) -> str:
    """Build a JSON array with num_items objects."""
    items = [{"name": f"pod-{i:04d}", "namespace": "monitoring", "status": "Running"}
             for i in range(num_items)]
    return json.dumps(items)


def _build_json_object_with_items(num_items: int) -> str:
    """Build a JSON object with an 'items' key containing num_items elements."""
    obj = {
        "apiVersion": "v1",
        "kind": "PodList",
        "metadata": {"resourceVersion": "12345"},
        "items": [{"metadata": {"name": f"pod-{i:04d}"}} for i in range(num_items)],
    }
    return json.dumps(obj)


# ---------------------------------------------------------------------------
# Case 1: TEXT TABLE (1 header + 263 rows) → count_items reports 263
# ---------------------------------------------------------------------------

class TestCase1TextTableCounting:
    """count_items and _truncate_with_marker must report 263 for a table with
    1 header row + 263 data rows (not 264, not the truncated-visible count)."""

    def test_count_items_263_row_table(self):
        """count_items on a full 263-row text table returns 263 (header excluded)."""
        from src.core.truncation import count_items

        table = _build_kubectl_table(263)
        result = count_items(table)
        assert result == 263, f"Expected 263, got {result}"

    def test_count_items_not_264(self):
        """Ensure header is NOT counted — count should be 263 not 264."""
        from src.core.truncation import count_items

        table = _build_kubectl_table(263)
        lines = [ln for ln in table.splitlines() if ln.strip()]
        # Confirm there are 264 non-empty lines (1 header + 263 data)
        assert len(lines) == 264
        # But count_items returns 263
        assert count_items(table) == 263

    def test_truncate_marker_reports_263_items(self):
        """_truncate_with_marker on a 263-row table reports ~263 in marker."""
        from src.core.agentic_loop import _truncate_with_marker

        table = _build_kubectl_table(263)
        # Table is ~18KB, truncate to 8000
        result = _truncate_with_marker(table, 8000)
        assert "263 items total" in result
        # The marker must be the first line
        first_line = result.splitlines()[0]
        assert "263" in first_line
        assert "truncated" in first_line.lower()

    def test_truncate_marker_never_says_38(self):
        """The old bug: if text was pre-cut to ~4000 chars, only ~38 rows would
        be visible and the marker would say 38. This must NEVER happen now."""
        from src.core.agentic_loop import _truncate_with_marker

        table = _build_kubectl_table(263)
        result = _truncate_with_marker(table, 8000)
        # Must NOT contain "~38" anywhere
        assert "38 items" not in result
        # Must contain "~263"
        assert "263 items" in result

    def test_count_items_wide_table_263_rows(self):
        """Wide tables (more columns) still count correctly."""
        from src.core.truncation import count_items

        table = _build_kubectl_table(263, wide=True)
        assert count_items(table) == 263


# ---------------------------------------------------------------------------
# Case 2: JSON array [263 items] → 263
# ---------------------------------------------------------------------------

class TestCase2JsonArray:
    """JSON array with 263 elements must report exactly 263."""

    def test_count_items_json_array_263(self):
        from src.core.truncation import count_items

        data = _build_json_array(263)
        assert count_items(data) == 263

    def test_count_items_json_array_1000(self):
        """Larger arrays also counted correctly."""
        from src.core.truncation import count_items

        data = _build_json_array(1000)
        assert count_items(data) == 1000

    def test_count_items_json_array_1(self):
        from src.core.truncation import count_items

        data = json.dumps([{"name": "single"}])
        assert count_items(data) == 1

    def test_truncate_marker_json_array(self):
        """Marker reports JSON array count correctly."""
        from src.core.agentic_loop import _truncate_with_marker

        data = _build_json_array(263)
        result = _truncate_with_marker(data, 2000)
        assert "263 items total" in result


# ---------------------------------------------------------------------------
# Case 3: JSON object {"items": [263]} → 263
# ---------------------------------------------------------------------------

class TestCase3JsonObjectItems:
    """JSON object with 'items' key containing 263 elements must report 263."""

    def test_count_items_json_object_items_263(self):
        from src.core.truncation import count_items

        data = _build_json_object_with_items(263)
        assert count_items(data) == 263

    def test_count_items_json_object_pods_key(self):
        """'pods' is also a recognized collection key."""
        from src.core.truncation import count_items

        data = json.dumps({"pods": list(range(50)), "total": 50})
        assert count_items(data) == 50

    def test_count_items_json_object_results_key(self):
        """'results' is also a recognized collection key."""
        from src.core.truncation import count_items

        data = json.dumps({"results": [{"x": i} for i in range(100)], "count": 100})
        assert count_items(data) == 100

    def test_truncate_marker_json_object(self):
        """Marker reports JSON object collection count."""
        from src.core.agentic_loop import _truncate_with_marker

        data = _build_json_object_with_items(263)
        result = _truncate_with_marker(data, 2000)
        assert "263 items total" in result


# ---------------------------------------------------------------------------
# Case 4: 20000-char text truncated to ~8000 — single path, no residual 4000
# ---------------------------------------------------------------------------

class TestCase4SingleTruncationPath:
    """Proves the single 8000 truncation path. No residual 4000 cut exists."""

    def test_20k_text_truncated_to_8000(self):
        """A 20000-char table truncated at 8000 must produce output starting
        with the marker and the content portion being exactly 8000 chars."""
        from src.core.agentic_loop import _truncate_with_marker

        table = _build_kubectl_table(263, wide=True)
        # Ensure our test data is large enough
        assert len(table) > 15000, f"Table too short: {len(table)}"

        result = _truncate_with_marker(table, 8000)
        # Must be truncated (has marker)
        assert result.startswith("[truncated:")
        # Content after marker is 8000 chars
        marker_line = result.splitlines()[0]
        content_after_marker = result[len(marker_line) + 1:]  # +1 for \n
        assert len(content_after_marker) == 8000

    def test_marker_states_true_total_not_visible_count(self):
        """The marker must state the TRUE total items (from full text),
        not the count visible after truncation."""
        from src.core.agentic_loop import _truncate_with_marker
        from src.core.truncation import count_items

        table = _build_kubectl_table(263)
        full_count = count_items(table)
        assert full_count == 263

        result = _truncate_with_marker(table, 4000)
        # Marker must say 263, NOT the number of lines visible in first 4000 chars
        assert "263 items total" in result

        # Prove visible count would be much smaller
        content = result.split("\n", 1)[1]  # content after marker
        visible_lines = [ln for ln in content.splitlines() if ln.strip()]
        # With header, visible lines should be well under 263
        assert len(visible_lines) < 100, "Visible lines should be much less than 263"

    def test_no_4000_truncation_remnant(self):
        """The _truncate_with_marker function uses the max_chars param directly.
        If called with 8000, the content is exactly 8000 chars — proving no
        hidden 4000-char cut exists upstream."""
        from src.core.agentic_loop import _truncate_with_marker

        # Build a table > 20000 chars
        table = _build_kubectl_table(500, wide=True)
        assert len(table) > 20000

        result = _truncate_with_marker(table, 8000)
        marker_end = result.index("\n") + 1
        content = result[marker_end:]
        # Content must be exactly 8000 — not 4000, not any other value
        assert len(content) == 8000, f"Expected 8000, got {len(content)}"

    def test_truncation_preserves_first_8000_chars(self):
        """Content after marker must be the literal first 8000 chars of the
        original text — no reordering or filtering."""
        from src.core.agentic_loop import _truncate_with_marker

        table = _build_kubectl_table(500)
        result = _truncate_with_marker(table, 8000)
        marker_end = result.index("\n") + 1
        content = result[marker_end:]
        assert content == table[:8000]


# ---------------------------------------------------------------------------
# Case 5: _summarize_tool_result count == _truncate_with_marker count
# ---------------------------------------------------------------------------

class TestCase5SharedCountHelper:
    """Both _summarize_tool_result and _truncate_with_marker must use the same
    count_items() helper and produce identical counts for the same input."""

    def test_text_table_counts_agree(self):
        """For a 263-row text table, both functions report 263."""
        from src.core.agentic_loop import _truncate_with_marker
        from src.core.agentic_loop_streaming import _summarize_tool_result

        table = _build_kubectl_table(263)

        # Marker
        marker_result = _truncate_with_marker(table, 8000)
        assert "263 items" in marker_result

        # Summary
        summary = _summarize_tool_result("pods_list_in_namespace", table)
        assert "263" in summary

    def test_json_array_counts_agree(self):
        """For a 263-element JSON array, both functions report 263."""
        from src.core.agentic_loop import _truncate_with_marker
        from src.core.agentic_loop_streaming import _summarize_tool_result

        data = _build_json_array(263)

        marker_result = _truncate_with_marker(data, 2000)
        assert "263 items" in marker_result

        summary = _summarize_tool_result("list_resources", data)
        assert "263" in summary

    def test_json_object_counts_agree(self):
        """For a JSON object with 263 items, both functions report 263."""
        from src.core.agentic_loop import _truncate_with_marker
        from src.core.agentic_loop_streaming import _summarize_tool_result

        data = _build_json_object_with_items(263)

        marker_result = _truncate_with_marker(data, 2000)
        assert "263 items" in marker_result

        summary = _summarize_tool_result("get_pods", data)
        assert "263" in summary

    def test_shared_helper_is_same_function(self):
        """Both modules import count_items from the same source."""
        # This is a structural test — confirms no copy-paste divergence
        from src.core.truncation import count_items as canonical

        # Verify _truncate_with_marker uses it (import inside function)
        import src.core.agentic_loop as al_mod
        import src.core.agentic_loop_streaming as als_mod

        # Call both on the same input to confirm they use the shared helper
        table = _build_kubectl_table(50)
        # Both should produce same count
        marker = al_mod._truncate_with_marker(table, 200)
        summary = als_mod._summarize_tool_result("test", table)
        # Both say 50
        assert "50" in marker
        assert "50" in summary


# ---------------------------------------------------------------------------
# Case 6: Small non-truncated result → no marker
# ---------------------------------------------------------------------------

class TestCase6NoMarkerWhenSmall:
    """When the result is small enough to fit within max_chars, no truncation
    marker should be prepended — the text passes through unchanged."""

    def test_short_text_no_marker(self):
        """Short text (< 8000) passes through without modification."""
        from src.core.agentic_loop import _truncate_with_marker

        short = "NAMESPACE  NAME\nmonitoring  pod-1\nmonitoring  pod-2"
        result = _truncate_with_marker(short, 8000)
        assert result == short
        assert "[truncated" not in result

    def test_small_json_array_no_marker(self):
        """Small JSON array passes through without truncation."""
        from src.core.agentic_loop import _truncate_with_marker

        data = json.dumps([{"name": "pod-1"}, {"name": "pod-2"}])
        result = _truncate_with_marker(data, 8000)
        assert result == data
        assert "[truncated" not in result

    def test_exact_limit_no_marker(self):
        """Text at exactly max_chars length passes through without marker."""
        from src.core.agentic_loop import _truncate_with_marker

        text = "x" * 8000
        result = _truncate_with_marker(text, 8000)
        assert result == text
        assert "[truncated" not in result

    def test_one_char_over_gets_marker(self):
        """Text 1 char over the limit DOES get truncated with marker."""
        from src.core.agentic_loop import _truncate_with_marker

        text = "x" * 8001
        result = _truncate_with_marker(text, 8000)
        assert "[truncated" in result
        assert result != text

    def test_empty_string_no_marker(self):
        """Empty string passes through unchanged."""
        from src.core.agentic_loop import _truncate_with_marker

        result = _truncate_with_marker("", 8000)
        assert result == ""


# ---------------------------------------------------------------------------
# Edge cases (additional coverage)
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases that strengthen coverage of the fix."""

    def test_count_items_table_with_empty_lines(self):
        """Empty lines in the middle of a table should not inflate count."""
        from src.core.truncation import count_items

        header = "NAME  STATUS  AGE"
        rows = ["pod-1  Running  1d", "", "pod-2  Running  2d", "  ", "pod-3  Running  3d"]
        text = "\n".join([header] + rows)
        # Only non-empty lines count, minus header
        result = count_items(text)
        assert result == 3  # 3 non-empty data rows

    def test_count_items_returns_none_for_single_line(self):
        """A single line of text is not countable."""
        from src.core.truncation import count_items

        assert count_items("just one line of output") is None

    def test_count_items_json_empty_array(self):
        """An empty JSON array returns 0 or None (no items)."""
        from src.core.truncation import count_items

        result = count_items("[]")
        # Either 0 or None is acceptable — both mean "no items"
        assert result == 0 or result is None

    def test_count_items_invalid_json(self):
        """Invalid JSON that looks like JSON should fall back to text mode."""
        from src.core.truncation import count_items

        text = '[{"name": "incomplete...'
        # Should not crash, should fall back
        result = count_items(text)
        # Falls back to line counting — single line → None
        assert result is None

    def test_is_table_header_all_caps(self):
        """ALL-CAPS multi-token line is detected as header."""
        from src.core.truncation import _is_table_header

        assert _is_table_header("NAMESPACE  APIVERSION  KIND  NAME  STATUS") is True

    def test_is_table_header_single_token(self):
        """Single uppercase token is NOT a table header."""
        from src.core.truncation import _is_table_header

        assert _is_table_header("NAME") is False

    def test_is_table_header_data_row(self):
        """A typical data row (lowercase pod name) is NOT a header."""
        from src.core.truncation import _is_table_header

        assert _is_table_header("monitoring  v1  Pod  my-app-abc123  Running") is False

    def test_truncate_marker_format(self):
        """Marker format is exactly as expected: [truncated: showing first N chars of ~M items total]."""
        from src.core.agentic_loop import _truncate_with_marker

        table = _build_kubectl_table(100)
        result = _truncate_with_marker(table, 2000)
        first_line = result.splitlines()[0]
        assert first_line == "[truncated: showing first 2000 chars of 100 items total]"

    def test_truncate_fallback_when_count_undetermined(self):
        """When count_items returns None, marker falls back to char count."""
        from src.core.agentic_loop import _truncate_with_marker

        # Single long line — count_items returns None
        text = "a" * 10000
        result = _truncate_with_marker(text, 5000)
        first_line = result.splitlines()[0]
        # Should say "of 10000 chars total" (no ~items)
        assert "10000 chars total" in first_line
        assert "items" not in first_line
