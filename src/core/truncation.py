"""Shared truncation helpers for tool results (spec 37).

Provides a single, consistent item-counting function used by both
_truncate_with_marker (model-facing marker) and _summarize_tool_result
(streaming UX summary) so reported counts never disagree.
"""
from __future__ import annotations

import json
import re


def count_items(text: str) -> int | None:
    """Estimate the number of logical items in a tool result text.

    Strategies (tried in order):
      1. JSON array → len(array)
      2. JSON object with a collection key (items, pods, results, ...) → len(list)
      3. Text table / multi-line text → non-empty lines minus header row

    For Strategy 3 (text tables like kubectl output), the header row is
    excluded when the first non-empty line looks like a header:
      - All-uppercase tokens (e.g. "NAMESPACE  APIVERSION  KIND  NAME ...")
      - OR starts with common kubectl headers like "NAME " or "NAMESPACE "

    Returns None when count cannot be determined (single line of text, etc.).
    """
    stripped = text.strip()
    if not stripped:
        return None

    # Strategy 1: JSON array — count elements
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return len(parsed)
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 2: JSON object — count items in well-known collection key
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                _COLLECTION_KEYS = ("items", "pods", "results", "data", "nodes", "records")
                best_count: int | None = None
                for key in _COLLECTION_KEYS:
                    val = parsed.get(key)
                    if isinstance(val, list):
                        best_count = len(val)
                        break
                # Fallback: largest list value among all top-level keys
                if best_count is None:
                    for val in parsed.values():
                        if isinstance(val, list):
                            candidate = len(val)
                            if best_count is None or candidate > best_count:
                                best_count = candidate
                if best_count is not None and best_count > 0:
                    return best_count
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Strategy 3: Text table / multi-line — non-empty lines minus header
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 1:
        return None

    # Detect header row: first non-empty line with all-uppercase tokens
    # (kubectl output: "NAMESPACE  APIVERSION  KIND  NAME  STATUS ...")
    first_line = lines[0].strip()
    has_header = _is_table_header(first_line)

    data_rows = len(lines) - (1 if has_header else 0)
    return data_rows if data_rows > 0 else None


def _is_table_header(line: str) -> bool:
    """Heuristic: detect if a line looks like a text-table header.

    Returns True when the line consists of all-uppercase tokens separated
    by whitespace (typical kubectl output), or matches common header prefixes.
    """
    tokens = line.split()
    if not tokens:
        return False

    # All tokens uppercase and at least 2 columns → very likely header
    if len(tokens) >= 2 and all(re.match(r'^[A-Z][A-Z0-9_/-]*$', t) for t in tokens):
        return True

    # Common kubectl single-word headers
    _HEADER_PREFIXES = ("NAME", "NAMESPACE", "STATUS", "READY", "AGE", "NODE")
    if tokens[0] in _HEADER_PREFIXES and len(tokens) >= 2:
        return True

    return False
