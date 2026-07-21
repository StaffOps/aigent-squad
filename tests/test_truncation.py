"""Tests for src/core/truncation.py — shared item-counting logic (spec 37).

Verifies that count_items correctly handles:
  - JSON arrays
  - JSON objects with well-known collection keys
  - Text tables with ALL-CAPS headers (kubectl output)
  - Multi-line text without header
  - Edge cases (empty, single-line, non-parseable)

Also verifies _truncate_with_marker uses count_items consistently.
"""
import json

import pytest

from src.core.truncation import count_items, _is_table_header


# --- count_items: JSON array ---

def test_count_items_json_array():
    data = json.dumps([{"name": f"pod-{i}"} for i in range(263)])
    assert count_items(data) == 263


def test_count_items_json_array_empty():
    assert count_items("[]") is None or count_items("[]") == 0


# --- count_items: JSON object with collection key ---

def test_count_items_json_object_items_key():
    data = json.dumps({"items": [{"name": f"pod-{i}"} for i in range(150)], "metadata": {}})
    assert count_items(data) == 150


def test_count_items_json_object_pods_key():
    data = json.dumps({"pods": [1, 2, 3], "total": 3})
    assert count_items(data) == 3


def test_count_items_json_object_fallback_largest_list():
    data = json.dumps({"x": [1, 2], "y": [1, 2, 3, 4, 5]})
    assert count_items(data) == 5


# --- count_items: text table (kubectl-style) ---

def test_count_items_text_table_with_header():
    """Simulates real kube-mcp pods_list_in_namespace output."""
    header = "NAMESPACE  APIVERSION  KIND  NAME  STATUS  AGE"
    rows = [f"monitoring  v1  Pod  pod-{i}  Running  1d" for i in range(263)]
    text = "\n".join([header] + rows)
    result = count_items(text)
    # Should count 263 data rows (header excluded)
    assert result == 263


def test_count_items_text_table_header_detection():
    """Verify header with typical kubectl column names is detected."""
    header = "NAME  READY  STATUS  RESTARTS  AGE"
    rows = ["nginx-abc  1/1  Running  0  5d", "redis-xyz  1/1  Running  2  3d"]
    text = "\n".join([header] + rows)
    assert count_items(text) == 2


def test_count_items_multiline_no_header():
    """Lines without a detectable header — all lines count."""
    text = "line one\nline two\nline three"
    assert count_items(text) == 3


def test_count_items_single_line():
    """Single line of text — not countable."""
    assert count_items("just a single result line") is None


def test_count_items_empty():
    assert count_items("") is None
    assert count_items("   ") is None


# --- _is_table_header ---

def test_is_table_header_kubectl_style():
    assert _is_table_header("NAMESPACE  APIVERSION  KIND  NAME") is True
    assert _is_table_header("NAME  READY  STATUS  RESTARTS  AGE") is True


def test_is_table_header_normal_text():
    assert _is_table_header("monitoring  v1  Pod  nginx-abc") is False
    assert _is_table_header("This is a normal sentence") is False


def test_is_table_header_mixed_case():
    """Mixed-case tokens should NOT be detected as header."""
    assert _is_table_header("Name  Ready  Status") is False


# --- _truncate_with_marker integration ---

def test_truncate_with_marker_text_table():
    """Integration: marker should report ~263 items for a kubectl text table."""
    from src.core.agentic_loop import _truncate_with_marker

    header = "NAMESPACE  APIVERSION  KIND  NAME  STATUS  AGE"
    rows = [f"monitoring  v1  Pod  pod-{i:03d}  Running  1d" for i in range(263)]
    full_text = "\n".join([header] + rows)

    # Truncate to 2000 chars (much smaller than full text)
    result = _truncate_with_marker(full_text, 2000)
    assert result.startswith("[truncated: showing first 2000 chars of ~263 items total]")


def test_truncate_with_marker_no_truncation_needed():
    """Short text should pass through unchanged."""
    from src.core.agentic_loop import _truncate_with_marker

    short = "NAMESPACE  NAME\nmonitoring  pod-1"
    result = _truncate_with_marker(short, 8000)
    assert result == short  # no marker prepended


def test_truncate_with_marker_json_array():
    """JSON array should report exact count."""
    from src.core.agentic_loop import _truncate_with_marker

    data = json.dumps([{"id": i} for i in range(100)])
    result = _truncate_with_marker(data, 200)
    assert "~100 items total" in result


# --- _summarize_tool_result uses same count ---

def test_summarize_uses_shared_count():
    """The streaming summary must use the same count_items logic."""
    from src.core.agentic_loop_streaming import _summarize_tool_result

    header = "NAMESPACE  APIVERSION  KIND  NAME  STATUS"
    rows = [f"monitoring  v1  Pod  pod-{i:03d}  Running" for i in range(263)]
    full_text = "\n".join([header] + rows)

    summary = _summarize_tool_result("pods_list_in_namespace", full_text)
    assert "263" in summary
