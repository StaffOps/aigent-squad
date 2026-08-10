"""Shared truncation helpers for tool results (spec 37 + spec 40).

Provides a single, consistent item-counting function used by both
_truncate_with_marker (model-facing marker) and _summarize_tool_result
(streaming UX summary) so reported counts never disagree.

Also provides context-trimming for the agentic loop (spec 40, DC1-DC5):
trim_message_history() replaces older toolResult content with enriched
deterministic summaries, keeping tool_use↔toolResult pairing intact.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.core.metrics import context_trimmed_messages

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Context-trimming (spec 40, DC1-DC5)
# ---------------------------------------------------------------------------


def _summarize_for_context(tool_name: str, tool_args: dict, result_text: str) -> str:
    """Build a deterministic enriched summary for a trimmed toolResult (DC1).

    Format: [context-trimmed] <tool> | args:<json ≤200> | shape:<n items/chars>
            | sample:<first 3 lines/values> | keys:<top-5>

    This is NOT an LLM call — purely deterministic, cheap, predictable.
    The model can still cite specific sample values from trimmed steps.
    """
    parts: list[str] = [f"[context-trimmed] {tool_name}"]

    # args: compact JSON ≤200 chars
    try:
        args_json = json.dumps(tool_args, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        args_json = str(tool_args)
    if len(args_json) > 200:
        args_json = args_json[:197] + "..."
    parts.append(f"args:{args_json}")

    # shape: item count or char count
    stripped = result_text.strip()
    item_count = count_items(result_text)
    if item_count is not None:
        parts.append(f"shape:{item_count} items")
    else:
        parts.append(f"shape:{len(stripped)} chars")

    # sample: first 3 lines or JSON values
    sample = _extract_sample(stripped)
    if sample:
        parts.append(f"sample:{sample}")

    # keys: top-5 JSON keys (if JSON object)
    keys = _extract_top_keys(stripped)
    if keys:
        parts.append(f"keys:{keys}")

    return " | ".join(parts)


def _extract_sample(text: str) -> str:
    """Extract first 3 lines/values as sample for context summary."""
    if not text:
        return ""

    # Try JSON array — first 3 values
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list) and parsed:
                samples = []
                for item in parsed[:3]:
                    s = json.dumps(item, default=str, ensure_ascii=False)
                    if len(s) > 80:
                        s = s[:77] + "..."
                    samples.append(s)
                return "; ".join(samples)
        except (json.JSONDecodeError, ValueError):
            pass

    # Try JSON object with a collection value — first 3 items of that collection
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                for val in parsed.values():
                    if isinstance(val, list) and val:
                        samples = []
                        for item in val[:3]:
                            s = json.dumps(item, default=str, ensure_ascii=False)
                            if len(s) > 80:
                                s = s[:77] + "..."
                            samples.append(s)
                        return "; ".join(samples)
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: first 3 non-empty lines
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    sample_lines = lines[:3]
    result = "; ".join(sample_lines)
    if len(result) > 240:
        result = result[:237] + "..."
    return result


def _extract_top_keys(text: str) -> str:
    """Extract top-5 JSON keys if result is a JSON object."""
    if not text.startswith("{"):
        return ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            keys = list(parsed.keys())[:5]
            return ",".join(keys)
    except (json.JSONDecodeError, ValueError):
        pass
    return ""


def trim_message_history(messages: list[dict[str, Any]], keep_last_n: int, agent_id: str = "unknown") -> list[dict[str, Any]]:
    """Trim older toolResult turns in-place, keeping last N verbatim (DC2-DC5).

    Rules:
    - NEVER trim the first message (original user question — has text blocks).
    - NEVER trim the last 2 messages (current assistant tool_use + user toolResult).
    - A user message is a "toolResult turn" iff any block has a 'toolResult' key (DC3).
    - Counting unit = toolResult-bearing user messages.
    - Within a fan-out turn, each toolResult block is trimmed independently (DC3).
    - Only the 'text' field inside toolResult.content[i] is replaced (DC2).
    - toolUseId is NEVER touched (Converse pairing contract).

    Args:
        messages: The mutable message list from the agentic loop.
        keep_last_n: Number of recent toolResult turns to keep verbatim.
        agent_id: Agent identifier for the context_trimmed_messages metric.

    Returns:
        The same list (mutated in place) for convenience.
    """
    from src.core.agent_config import CONTEXT_TRIM_ENABLED, MAX_LOOP_TOKENS

    if not CONTEXT_TRIM_ENABLED:
        return messages

    effective_n = max(keep_last_n, 1)

    # Identify indices of toolResult-bearing user messages.
    # Exclude the first message (original user question) and last 2 messages.
    safe_end = max(0, len(messages) - 2)
    tool_result_indices: list[int] = []

    for idx in range(1, safe_end):
        msg = messages[idx]
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if any(isinstance(b, dict) and b.get("toolResult") for b in content):
            tool_result_indices.append(idx)

    # Keep last N toolResult turns verbatim; trim older ones
    if len(tool_result_indices) <= effective_n:
        return messages

    indices_to_trim = tool_result_indices[:-effective_n]

    # ADD (d): emit metric counting trimmed turns (one increment per trim event)
    context_trimmed_messages.add(len(indices_to_trim), {"agent_id": agent_id})

    for idx in indices_to_trim:
        msg = messages[idx]
        content = msg.get("content", [])
        for block in content:
            if not isinstance(block, dict):
                continue
            tool_result = block.get("toolResult")
            if tool_result is None:
                continue
            # Extract tool_name and args from the paired assistant tool_use
            tool_name, tool_args = _find_paired_tool_use(messages, idx, tool_result.get("toolUseId", ""))
            # Replace each text content block with enriched summary
            tr_content = tool_result.get("content")
            if not isinstance(tr_content, list):
                continue
            for content_item in tr_content:
                if not isinstance(content_item, dict):
                    continue
                if "text" not in content_item:
                    continue
                original_text = content_item["text"]
                content_item["text"] = _summarize_for_context(
                    tool_name, tool_args, original_text
                )

    # R4 residual risk: warn if estimated trimmed history > 80% of MAX_LOOP_TOKENS
    _warn_if_context_large(messages, MAX_LOOP_TOKENS)

    return messages


def _find_paired_tool_use(
    messages: list[dict[str, Any]], tool_result_idx: int, tool_use_id: str
) -> tuple[str, dict]:
    """Find the tool_use block that matches a toolUseId (for summary context).

    Searches the preceding assistant message for the matching toolUse block.
    Returns (tool_name, tool_args) or ("unknown", {}) if not found.
    """
    if not tool_use_id:
        return "unknown", {}

    # The preceding message should be an assistant message with toolUse blocks
    for search_idx in range(tool_result_idx - 1, -1, -1):
        msg = messages[search_idx]
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        for block in content:
            if not isinstance(block, dict):
                continue
            tool_use = block.get("toolUse")
            if tool_use and tool_use.get("toolUseId") == tool_use_id:
                return tool_use.get("name", "unknown"), tool_use.get("input", {})
        break  # Only check the immediately preceding assistant message

    return "unknown", {}


def _warn_if_context_large(messages: list[dict[str, Any]], max_loop_tokens: int) -> None:
    """R4: log a warning when estimated context exceeds 80% of MAX_LOOP_TOKENS."""
    # Rough estimate: chars / 4 ≈ tokens
    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if "text" in block:
                        total_chars += len(block["text"])
                    tr = block.get("toolResult")
                    if isinstance(tr, dict):
                        for c in tr.get("content", []):
                            if isinstance(c, dict) and "text" in c:
                                total_chars += len(c["text"])

    approx_tokens = total_chars // 4
    threshold = int(max_loop_tokens * 0.8)
    if approx_tokens > threshold:
        logger.warning(
            "Context history exceeds 80% of MAX_LOOP_TOKENS after trimming",
            extra={
                "approx_tokens": approx_tokens,
                "max_loop_tokens": max_loop_tokens,
                "threshold_pct": 80,
            },
        )
