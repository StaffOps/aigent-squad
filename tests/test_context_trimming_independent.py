"""Independent tests for spec 40 — Agentic Context Management (DC1–DC5).

Tests are written AGAINST THE CONTRACT in specs/40-agentic-context-management/design.md,
NOT by mirroring implementation details. They validate the 9 cases specified in T4:
  1. N=0 → floor to 1 (effective_n)
  2. N=1 — only the most recent toolResult turn kept verbatim
  3. Fan-out (3 toolResults in one user msg) all summarized when older than N
  4. Single-step loop — no-op (nothing to trim)
  5. 8-step loop → steps 1-3 trimmed, 4-8 verbatim at N=5
  6. Original user question never touched
  7. toolUseId pairing invariant after trim
  8. Trim disabled = no-op
  9. Mocked Bedrock Converse accepts the trimmed message structure (no ValidationException)
"""
from __future__ import annotations

import copy
import json
import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to build Bedrock Converse message structures
# ---------------------------------------------------------------------------

def _user_question(text: str) -> dict:
    """Build the initial user question message (role=user, text blocks)."""
    return {"role": "user", "content": [{"text": text}]}


def _assistant_tool_use(tool_use_id: str, tool_name: str, args: dict) -> dict:
    """Build an assistant message with a single toolUse block."""
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": tool_use_id,
                    "name": tool_name,
                    "input": args,
                }
            }
        ],
    }


def _user_tool_result(tool_use_id: str, result_text: str) -> dict:
    """Build a user message with a single toolResult block."""
    return {
        "role": "user",
        "content": [
            {
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"text": result_text}],
                    "status": "success",
                }
            }
        ],
    }


def _user_fanout_tool_results(results: list[tuple[str, str]]) -> dict:
    """Build a user message with multiple toolResult blocks (fan-out turn).

    Each tuple is (toolUseId, result_text).
    """
    return {
        "role": "user",
        "content": [
            {
                "toolResult": {
                    "toolUseId": uid,
                    "content": [{"text": text}],
                    "status": "success",
                }
            }
            for uid, text in results
        ],
    }


def _assistant_fanout_tool_uses(uses: list[tuple[str, str, dict]]) -> dict:
    """Build an assistant message with multiple tool_use blocks (fan-out).

    Each tuple is (toolUseId, tool_name, args).
    """
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": uid,
                    "name": name,
                    "input": args,
                }
            }
            for uid, name, args in uses
        ],
    }


def _build_n_step_loop(n: int, question: str = "What is the cluster status?") -> list[dict]:
    """Build a complete n-step agentic loop conversation.

    Structure: [user_question, (assistant_tool_use, user_tool_result) × n]
    """
    messages = [_user_question(question)]
    for i in range(1, n + 1):
        tool_use_id = f"tu-step-{i}"
        tool_name = f"tool_{i}"
        args = {"query": f"step {i} query", "index": i}
        result_text = json.dumps({
            "items": [f"item_{i}_{j}" for j in range(10)],
            "count": 10,
            "metadata": {"step": i, "cluster": "prd"},
        })
        messages.append(_assistant_tool_use(tool_use_id, tool_name, args))
        messages.append(_user_tool_result(tool_use_id, result_text))
    return messages


def _is_trimmed(text: str) -> bool:
    """Check if text has been replaced with a context-trimmed summary (DC1)."""
    return text.startswith("[context-trimmed]")


def _get_tool_result_text(msg: dict, block_idx: int = 0) -> str:
    """Extract the text from a toolResult content block."""
    return msg["content"][block_idx]["toolResult"]["content"][0]["text"]


def _get_tool_use_id(msg: dict, block_idx: int = 0) -> str:
    """Extract toolUseId from a toolResult block."""
    return msg["content"][block_idx]["toolResult"]["toolUseId"]


# ---------------------------------------------------------------------------
# The function under test — import with config mocking
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_trim(monkeypatch):
    """Ensure trimming is enabled with N=5 as default for tests."""
    monkeypatch.setenv("AIGENT_CONTEXT_TRIM_ENABLED", "true")
    monkeypatch.setenv("AIGENT_CONTEXT_KEEP_LAST_N", "5")


def _call_trim(messages: list[dict], keep_last_n: int = 5, enabled: bool = True):
    """Call trim_message_history with the given N and enabled flag, using env vars."""
    env = {
        "AIGENT_CONTEXT_TRIM_ENABLED": "true" if enabled else "false",
        "AIGENT_CONTEXT_KEEP_LAST_N": str(keep_last_n),
        "AIGENT_MAX_LOOP_TOKENS": "300000",
    }
    with patch.dict(os.environ, env):
        # Reimport to pick up env vars
        import importlib
        import src.core.agent_config as ac
        importlib.reload(ac)
        from src.core.truncation import trim_message_history
        return trim_message_history(messages, ac.CONTEXT_KEEP_LAST_N)


# ===========================================================================
# Test Cases
# ===========================================================================


class TestCase1_NZeroFloorsToOne:
    """DC4: N=0 → effective_n = max(0, 1) = 1. Floor guarantees ≥1 kept.

    Key insight: the 'last 2 messages' safety rule (DC4) protects the final
    assistant+user pair from even being considered. So in a 4-step loop (9 msgs),
    safe_end=7, eligible toolResult turns are at indices 2, 4, 6 (steps 1-3).
    With effective_n=1, keep last 1 eligible → step 3 (idx 6) kept, steps 1-2 trimmed.
    Step 4 (idx 8) is always verbatim by the last-2-messages rule.
    """

    def test_n_zero_keeps_at_least_one_verbatim(self):
        """When N=0, the floor ensures at least 1 eligible toolResult turn is kept."""
        # Use 4 steps: steps 1-2 trimmed, step 3 is the 'kept' one (floor=1), step 4 protected.
        messages = _build_n_step_loop(4, "explain pods")
        original_step3 = _get_tool_result_text(messages[6])  # step 3 result at idx 6
        original_step4 = _get_tool_result_text(messages[8])  # step 4 result at idx 8

        result = _call_trim(messages, keep_last_n=0)

        # Step 4 (last pair) always verbatim (last-2-messages rule)
        assert _get_tool_result_text(result[8]) == original_step4

        # Step 3 kept verbatim (floor N=1 means keep last 1 eligible)
        assert _get_tool_result_text(result[6]) == original_step3

        # Steps 1, 2 trimmed (indices 2, 4)
        assert _is_trimmed(_get_tool_result_text(result[2]))
        assert _is_trimmed(_get_tool_result_text(result[4]))


class TestCase2_NOne:
    """DC4: N=1 — only 1 eligible toolResult turn is kept verbatim (beyond the
    last-2-messages protected pair).

    In a 5-step loop (11 msgs): safe_end=9, eligible = indices 2,4,6,8 (steps 1-4).
    Step 5 at idx 10 protected by last-2-messages. With N=1, keep last eligible
    (step 4, idx 8), trim steps 1-3.
    """

    def test_n_one_trims_all_but_last_eligible(self):
        """With N=1, only the last eligible toolResult turn is untouched."""
        messages = _build_n_step_loop(5)
        original_step4 = _get_tool_result_text(messages[8])  # step 4 at idx 8
        original_step5 = _get_tool_result_text(messages[10])  # step 5 at idx 10

        result = _call_trim(messages, keep_last_n=1)

        # Step 5 verbatim (last-2-messages protection)
        assert _get_tool_result_text(result[10]) == original_step5
        # Step 4 verbatim (last eligible, kept by N=1)
        assert _get_tool_result_text(result[8]) == original_step4

        # Steps 1, 2, 3 trimmed (indices 2, 4, 6)
        for step_idx in [2, 4, 6]:
            assert _is_trimmed(_get_tool_result_text(result[step_idx])), \
                f"Step at msg index {step_idx} should be trimmed"


class TestCase3_FanOutTrimmed:
    """DC3: Fan-out turn (3 toolResults in one user msg) — all blocks trimmed when older than N.

    The fan-out turn is ONE user message with 3 toolResult blocks. With enough
    subsequent steps to push it past N, all 3 blocks get trimmed independently.
    """

    def test_fanout_all_blocks_trimmed_when_older(self):
        """A fan-out user message with 3 toolResult blocks gets ALL blocks trimmed
        when the entire turn is older than N."""
        # Build: question → assistant fan-out (3 tools) → user fan-out (3 results)
        #       → 3 more single steps to push the fan-out past N=1
        messages = [
            _user_question("Compare metrics across 3 clusters"),
            _assistant_fanout_tool_uses([
                ("tu-fa-1", "query_metrics", {"cluster": "prd"}),
                ("tu-fa-2", "query_metrics", {"cluster": "hml"}),
                ("tu-fa-3", "query_metrics", {"cluster": "dev"}),
            ]),
            _user_fanout_tool_results([
                ("tu-fa-1", '{"cpu": 80, "mem": 60}'),
                ("tu-fa-2", '{"cpu": 50, "mem": 40}'),
                ("tu-fa-3", '{"cpu": 20, "mem": 30}'),
            ]),
            # Step 2
            _assistant_tool_use("tu-s2", "analyze", {"input": "compare"}),
            _user_tool_result("tu-s2", "Analysis: prd highest CPU"),
            # Step 3 (protected by last-2-messages since it's the final pair)
            _assistant_tool_use("tu-s3", "summarize", {"input": "final"}),
            _user_tool_result("tu-s3", "Summary: prd needs attention"),
        ]

        # With N=1: eligible toolResult turns at indices 2, 4 (safe_end=5, idx 6 protected).
        # Keep last 1 eligible → idx 4 kept; idx 2 (fan-out) trimmed.
        result = _call_trim(messages, keep_last_n=1)

        # Fan-out turn (index 2) — all 3 blocks should be trimmed
        fanout_msg = result[2]
        for i in range(3):
            text = fanout_msg["content"][i]["toolResult"]["content"][0]["text"]
            assert _is_trimmed(text), f"Fan-out block {i} should be trimmed"

        # Step 2 (index 4) kept verbatim (last eligible)
        assert _get_tool_result_text(result[4]) == "Analysis: prd highest CPU"

        # Step 3 verbatim (last-2 protection)
        assert _get_tool_result_text(result[6]) == "Summary: prd needs attention"


class TestCase4_SingleStepNoOp:
    """Only 1 step means nothing to trim — the function is a no-op."""

    def test_single_step_loop_untouched(self):
        """With only 1 toolResult turn, nothing gets trimmed regardless of N."""
        messages = _build_n_step_loop(1, "Quick check")
        original = copy.deepcopy(messages)

        result = _call_trim(messages, keep_last_n=5)

        # Nothing changed
        assert result == original

    def test_single_step_with_n_one(self):
        """Even N=1 keeps the single step verbatim (it's the last one)."""
        messages = _build_n_step_loop(1)
        original_text = _get_tool_result_text(messages[-1])

        result = _call_trim(messages, keep_last_n=1)

        assert _get_tool_result_text(result[-1]) == original_text


class TestCase5_EightStepWithN5:
    """DC4: 8-step loop → trim older eligible steps, keep last N=5 eligible verbatim.

    8-step loop = 17 messages. safe_end = 15. Step 8 result at idx 16 → protected.
    Eligible toolResult turns: indices 2,4,6,8,10,12,14 (steps 1-7).
    With N=5: keep last 5 eligible (steps 3-7 at indices 6,8,10,12,14).
    Trim: steps 1-2 (indices 2, 4).
    Step 8 always verbatim (last-2-messages rule).
    """

    def test_eight_steps_first_two_trimmed(self):
        """In an 8-step loop with N=5, steps 1-2 are trimmed and 3-8 kept."""
        messages = _build_n_step_loop(8)
        # Capture originals for steps 3-8
        originals = {}
        for step in range(3, 9):
            idx = step * 2
            originals[step] = _get_tool_result_text(messages[idx])

        result = _call_trim(messages, keep_last_n=5)

        # Steps 1-2 trimmed (result indices: 2, 4)
        for step in range(1, 3):
            idx = step * 2
            assert _is_trimmed(_get_tool_result_text(result[idx])), \
                f"Step {step} (msg idx {idx}) should be trimmed"

        # Steps 3-8 verbatim (result indices: 6, 8, 10, 12, 14, 16)
        for step in range(3, 9):
            idx = step * 2
            assert _get_tool_result_text(result[idx]) == originals[step], \
                f"Step {step} (msg idx {idx}) should be verbatim"

    def test_trimmed_summary_has_enriched_format(self):
        """DC1: Trimmed content uses the enriched [context-trimmed] format with
        args, shape, sample, and keys."""
        messages = _build_n_step_loop(8)
        result = _call_trim(messages, keep_last_n=5)

        # Check a trimmed step's summary format (step 1 at index 2)
        summary = _get_tool_result_text(result[2])
        assert summary.startswith("[context-trimmed]")
        assert "args:" in summary
        assert "shape:" in summary
        assert "sample:" in summary


class TestCase6_OriginalQuestionNeverTouched:
    """DC3: The user's original question (index 0, has text blocks) is NEVER trimmed."""

    def test_original_question_preserved(self):
        """The first message (user question with text) is never modified."""
        question_text = "What is the health status of the production cluster?"
        messages = _build_n_step_loop(8, question_text)

        result = _call_trim(messages, keep_last_n=1)

        # First message must be the original user question, untouched
        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["text"] == question_text

    def test_question_is_not_identified_as_tool_result_turn(self):
        """A user message with only text blocks is not a toolResult turn."""
        messages = _build_n_step_loop(4)
        # Verify: message 0 has text, not toolResult
        first_msg = messages[0]
        assert any("text" in b for b in first_msg["content"])
        assert not any(b.get("toolResult") for b in first_msg["content"])

        result = _call_trim(messages, keep_last_n=1)
        assert result[0]["content"][0]["text"] == "What is the cluster status?"


class TestCase7_ToolUseIdPairingInvariant:
    """DC2: toolUseId is NEVER touched — pairing invariant preserved after trim."""

    def test_tool_use_id_unchanged_after_trim(self):
        """All toolUseIds in toolResult blocks are identical before and after trim."""
        messages = _build_n_step_loop(6)

        # Collect all toolUseIds before trim
        ids_before = []
        for msg in messages:
            if msg.get("role") == "user":
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("toolResult"):
                        ids_before.append(block["toolResult"]["toolUseId"])

        result = _call_trim(messages, keep_last_n=2)

        # Collect all toolUseIds after trim
        ids_after = []
        for msg in result:
            if msg.get("role") == "user":
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("toolResult"):
                        ids_after.append(block["toolResult"]["toolUseId"])

        # MUST be identical — pairing invariant
        assert ids_before == ids_after

    def test_tool_result_dict_structure_preserved(self):
        """DC2: The toolResult dict and its top-level keys are never altered."""
        messages = _build_n_step_loop(4)

        # Record original structure
        original_keys_per_block = []
        for msg in messages:
            if msg.get("role") == "user":
                for block in msg.get("content", []):
                    tr = block.get("toolResult")
                    if tr:
                        original_keys_per_block.append(set(tr.keys()))

        result = _call_trim(messages, keep_last_n=1)

        # After trim, same structure
        trimmed_keys_per_block = []
        for msg in result:
            if msg.get("role") == "user":
                for block in msg.get("content", []):
                    tr = block.get("toolResult")
                    if tr:
                        trimmed_keys_per_block.append(set(tr.keys()))

        assert original_keys_per_block == trimmed_keys_per_block


class TestCase8_TrimDisabledNoOp:
    """Config switch: AIGENT_CONTEXT_TRIM_ENABLED=false → no-op."""

    def test_disabled_returns_messages_unchanged(self):
        """When trim is disabled, messages are returned without any modification."""
        messages = _build_n_step_loop(8)
        original = copy.deepcopy(messages)

        result = _call_trim(messages, keep_last_n=5, enabled=False)

        assert result == original


class TestCase9_BedrockConverseAcceptsTrimmed:
    """Validate that the trimmed message structure is valid for Bedrock Converse API.

    Converse contract requirements:
    - messages alternate user/assistant roles (can have consecutive if multi-block)
    - every toolUse has a matching toolResult with same toolUseId
    - toolResult.content[i] must have 'text' key (string)
    """

    def test_trimmed_messages_valid_converse_structure(self):
        """Trimmed messages maintain valid Converse message structure."""
        messages = _build_n_step_loop(8)
        result = _call_trim(messages, keep_last_n=5)

        # Validate basic structure
        assert len(result) > 0
        assert result[0]["role"] == "user"  # starts with user

        # Collect all toolUseIds from assistant messages
        tool_use_ids = set()
        for msg in result:
            if msg["role"] == "assistant":
                for block in msg.get("content", []):
                    tu = block.get("toolUse")
                    if tu:
                        tool_use_ids.add(tu["toolUseId"])

        # Collect all toolUseIds from user toolResult messages
        tool_result_ids = set()
        for msg in result:
            if msg["role"] == "user":
                for block in msg.get("content", []):
                    tr = block.get("toolResult")
                    if tr:
                        tool_result_ids.add(tr["toolUseId"])

        # Every toolUse must have a matching toolResult and vice versa
        assert tool_use_ids == tool_result_ids, \
            f"Pairing mismatch: uses={tool_use_ids - tool_result_ids}, results={tool_result_ids - tool_use_ids}"

    def test_trimmed_tool_result_content_has_text(self):
        """Every trimmed toolResult.content[] block still has a 'text' key with a string."""
        messages = _build_n_step_loop(8)
        result = _call_trim(messages, keep_last_n=3)

        for msg in result:
            if msg["role"] == "user":
                for block in msg.get("content", []):
                    tr = block.get("toolResult")
                    if tr:
                        for content_item in tr.get("content", []):
                            assert "text" in content_item, "Missing 'text' in trimmed toolResult content"
                            assert isinstance(content_item["text"], str), "text must be str"
                            assert len(content_item["text"]) > 0, "text must be non-empty"

    def test_mock_bedrock_converse_accepts_trimmed(self):
        """Simulate Bedrock Converse validation — no ValidationException on trimmed structure."""
        messages = _build_n_step_loop(8)
        result = _call_trim(messages, keep_last_n=5)

        # Simulate the Converse API validation:
        # 1. Messages must have 'role' and 'content'
        # 2. Content must be list of blocks
        # 3. Each block is one of: text, toolUse, toolResult
        # 4. toolResult.toolUseId must match a preceding toolUse.toolUseId
        errors = _validate_converse_messages(result)
        assert not errors, f"Converse validation errors: {errors}"


def _validate_converse_messages(messages: list[dict]) -> list[str]:
    """Simulate Bedrock Converse message structure validation.

    Returns list of validation error strings (empty = valid).
    """
    errors = []
    seen_tool_use_ids = set()
    seen_tool_result_ids = set()

    for i, msg in enumerate(messages):
        if "role" not in msg:
            errors.append(f"msg[{i}]: missing 'role'")
            continue
        if "content" not in msg:
            errors.append(f"msg[{i}]: missing 'content'")
            continue
        if not isinstance(msg["content"], list):
            errors.append(f"msg[{i}]: 'content' must be list")
            continue

        for j, block in enumerate(msg["content"]):
            if not isinstance(block, dict):
                errors.append(f"msg[{i}].content[{j}]: block must be dict")
                continue

            if "text" in block:
                if not isinstance(block["text"], str):
                    errors.append(f"msg[{i}].content[{j}]: 'text' must be string")
            elif "toolUse" in block:
                tu = block["toolUse"]
                if "toolUseId" not in tu:
                    errors.append(f"msg[{i}].content[{j}]: toolUse missing toolUseId")
                else:
                    seen_tool_use_ids.add(tu["toolUseId"])
                if "name" not in tu:
                    errors.append(f"msg[{i}].content[{j}]: toolUse missing name")
            elif "toolResult" in block:
                tr = block["toolResult"]
                if "toolUseId" not in tr:
                    errors.append(f"msg[{i}].content[{j}]: toolResult missing toolUseId")
                else:
                    seen_tool_result_ids.add(tr["toolUseId"])
                if "content" not in tr or not isinstance(tr["content"], list):
                    errors.append(f"msg[{i}].content[{j}]: toolResult.content must be list")
                else:
                    for k, ci in enumerate(tr["content"]):
                        if "text" not in ci:
                            errors.append(f"msg[{i}].content[{j}].content[{k}]: missing 'text'")
                        elif not isinstance(ci["text"], str):
                            errors.append(f"msg[{i}].content[{j}].content[{k}]: 'text' not str")
            else:
                errors.append(f"msg[{i}].content[{j}]: unknown block type: {list(block.keys())}")

    # Pairing check
    unmatched_uses = seen_tool_use_ids - seen_tool_result_ids
    unmatched_results = seen_tool_result_ids - seen_tool_use_ids
    if unmatched_uses:
        errors.append(f"toolUse without matching toolResult: {unmatched_uses}")
    if unmatched_results:
        errors.append(f"toolResult without matching toolUse: {unmatched_results}")

    return errors


# ===========================================================================
# Additional contract coverage
# ===========================================================================


class TestSummaryEnrichment:
    """DC1: The summary must be enriched (not just shape/emoji)."""

    def test_summary_contains_tool_name(self):
        """Summary includes the tool name for context."""
        messages = _build_n_step_loop(3)
        result = _call_trim(messages, keep_last_n=1)
        summary = _get_tool_result_text(result[2])  # step 1 result
        # Should contain the tool name
        assert "tool_1" in summary

    def test_summary_contains_args_info(self):
        """Summary includes args for reproducibility."""
        messages = _build_n_step_loop(3)
        result = _call_trim(messages, keep_last_n=1)
        summary = _get_tool_result_text(result[2])
        assert "args:" in summary

    def test_summary_contains_shape(self):
        """Summary includes shape (item count or char count)."""
        messages = _build_n_step_loop(3)
        result = _call_trim(messages, keep_last_n=1)
        summary = _get_tool_result_text(result[2])
        assert "shape:" in summary


class TestLastTwoMessagesNeverTrimmed:
    """DC4 safety: the last 2 messages (current assistant tool_use + user toolResult)
    are never trimmed, even if N=1 and they're the only ones."""

    def test_last_two_always_verbatim(self):
        """The final assistant + user pair is always kept verbatim."""
        messages = _build_n_step_loop(6)
        original_last_result = _get_tool_result_text(messages[-1])
        original_last_assistant = messages[-2]["content"][0]["toolUse"]["name"]

        result = _call_trim(messages, keep_last_n=1)

        # Last 2 messages unchanged
        assert _get_tool_result_text(result[-1]) == original_last_result
        assert result[-2]["content"][0]["toolUse"]["name"] == original_last_assistant


class TestInPlaceMutation:
    """The function mutates and returns the same list object."""

    def test_returns_same_list_reference(self):
        """trim_message_history returns the same list (mutated in place)."""
        messages = _build_n_step_loop(4)
        result = _call_trim(messages, keep_last_n=1)
        assert result is messages


# ===========================================================================
# Coverage boost — helper functions exercised via the trim contract
# ===========================================================================


class TestCountItems:
    """Exercise count_items (shape detection) for different result formats."""

    def test_json_array_shape(self):
        """Shape detection for JSON array → item count."""
        from src.core.truncation import count_items
        assert count_items('[1, 2, 3]') == 3
        assert count_items('[]') == 0

    def test_json_object_with_collection_key(self):
        """Shape detection for JSON object with well-known collection key."""
        from src.core.truncation import count_items
        assert count_items('{"items": [1, 2, 3]}') == 3
        assert count_items('{"pods": ["a", "b"]}') == 2
        assert count_items('{"results": []}') is None  # empty list, returns None (0 not > 0)
        assert count_items('{"data": [1]}') == 1

    def test_json_object_fallback_largest_list(self):
        """When no well-known key, use largest list value."""
        from src.core.truncation import count_items
        assert count_items('{"foo": [1, 2], "bar": [1, 2, 3, 4]}') == 4

    def test_text_table_with_header(self):
        """Text table with uppercase header row → lines minus header."""
        from src.core.truncation import count_items
        table = "NAME  NAMESPACE  STATUS\npod-1  default  Running\npod-2  kube-system  Running"
        assert count_items(table) == 2  # 3 lines - 1 header = 2

    def test_text_table_no_header(self):
        """Multi-line text without header → all lines counted."""
        from src.core.truncation import count_items
        text = "line one\nline two\nline three"
        assert count_items(text) == 3

    def test_single_line_returns_none(self):
        """Single line of text → cannot determine count."""
        from src.core.truncation import count_items
        assert count_items("just a single line") is None

    def test_empty_returns_none(self):
        """Empty string → None."""
        from src.core.truncation import count_items
        assert count_items("") is None
        assert count_items("   ") is None

    def test_invalid_json_array(self):
        """Broken JSON starting with [ falls through to text strategy."""
        from src.core.truncation import count_items
        result = count_items("[invalid json\nsecond line")
        assert result == 2  # multi-line text

    def test_invalid_json_object(self):
        """Broken JSON starting with { falls through to text strategy."""
        from src.core.truncation import count_items
        result = count_items("{invalid\nline2\nline3")
        assert result == 3


class TestIsTableHeader:
    """Exercise _is_table_header heuristic."""

    def test_kubectl_style_header(self):
        """All-uppercase tokens → True."""
        from src.core.truncation import _is_table_header
        assert _is_table_header("NAME  NAMESPACE  STATUS  AGE") is True

    def test_common_prefix_header(self):
        """Starts with common prefix like NAME + multiple tokens → True."""
        from src.core.truncation import _is_table_header
        assert _is_table_header("NAME ready status") is True

    def test_not_header(self):
        """Normal text → False."""
        from src.core.truncation import _is_table_header
        assert _is_table_header("pod-1  default  Running") is False

    def test_empty_line(self):
        """Empty → False."""
        from src.core.truncation import _is_table_header
        assert _is_table_header("") is False


class TestExtractSampleAndKeys:
    """Exercise _extract_sample and _extract_top_keys for context summaries."""

    def test_extract_sample_json_array(self):
        """JSON array → first 3 values."""
        from src.core.truncation import _extract_sample
        result = _extract_sample('[1, 2, 3, 4, 5]')
        assert "1" in result
        assert "2" in result
        assert "3" in result

    def test_extract_sample_json_object_with_list(self):
        """JSON object with a list value → first 3 items of that list."""
        from src.core.truncation import _extract_sample
        result = _extract_sample('{"results": ["a", "b", "c", "d"]}')
        assert "a" in result

    def test_extract_sample_multiline_text(self):
        """Multi-line text → first 3 lines."""
        from src.core.truncation import _extract_sample
        result = _extract_sample("line1\nline2\nline3\nline4")
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    def test_extract_sample_empty(self):
        """Empty → empty string."""
        from src.core.truncation import _extract_sample
        assert _extract_sample("") == ""

    def test_extract_sample_long_values_truncated(self):
        """Long values in JSON array get truncated to 80 chars."""
        from src.core.truncation import _extract_sample
        long_str = "x" * 100
        result = _extract_sample(json.dumps([long_str]))
        assert "..." in result

    def test_extract_top_keys_json_object(self):
        """JSON object → top 5 keys."""
        from src.core.truncation import _extract_top_keys
        obj = json.dumps({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6})
        result = _extract_top_keys(obj)
        assert "a" in result
        assert "e" in result

    def test_extract_top_keys_not_json(self):
        """Non-JSON → empty string."""
        from src.core.truncation import _extract_top_keys
        assert _extract_top_keys("not json") == ""

    def test_extract_top_keys_json_array(self):
        """JSON array → empty (not an object)."""
        from src.core.truncation import _extract_top_keys
        assert _extract_top_keys("[1, 2, 3]") == ""


class TestWarnIfContextLarge:
    """Exercise _warn_if_context_large (R4 residual risk)."""

    def test_warning_emitted_when_large(self, caplog):
        """When estimated tokens > 80% of MAX_LOOP_TOKENS, a warning is logged."""
        from src.core.truncation import _warn_if_context_large
        import logging

        # Create messages with enough text to exceed 80% of a small max_loop_tokens
        # 80% of 100 = 80 tokens → 320 chars needed
        messages = [{"role": "user", "content": [{"text": "x" * 500}]}]

        with caplog.at_level(logging.WARNING):
            _warn_if_context_large(messages, max_loop_tokens=100)

        assert "80%" in caplog.text or "MAX_LOOP_TOKENS" in caplog.text

    def test_no_warning_when_small(self, caplog):
        """No warning when context is well within budget."""
        from src.core.truncation import _warn_if_context_large
        import logging

        messages = [{"role": "user", "content": [{"text": "hello"}]}]

        with caplog.at_level(logging.WARNING):
            _warn_if_context_large(messages, max_loop_tokens=300000)

        assert "MAX_LOOP_TOKENS" not in caplog.text


class TestFindPairedToolUseEdgeCases:
    """Cover edge cases in _find_paired_tool_use."""

    def test_empty_tool_use_id(self):
        """Empty toolUseId → returns ('unknown', {})."""
        from src.core.truncation import _find_paired_tool_use
        messages = [_user_question("hi")]
        assert _find_paired_tool_use(messages, 0, "") == ("unknown", {})

    def test_no_matching_assistant(self):
        """No preceding assistant message → returns ('unknown', {})."""
        from src.core.truncation import _find_paired_tool_use
        messages = [
            _user_question("hi"),
            _user_tool_result("tu-1", "some result"),
        ]
        assert _find_paired_tool_use(messages, 1, "tu-1") == ("unknown", {})


class TestSummaryWithMissingToolUseId:
    """When a toolResult has no matching tool_use, summary uses 'unknown'."""

    def test_trimmed_summary_with_unknown_tool(self):
        """If paired tool_use can't be found, summary says 'unknown'."""
        # Build a scenario where the assistant msg has a DIFFERENT toolUseId
        # than the toolResult. Need enough steps so the orphan is trimmed.
        messages = [
            _user_question("test"),
            # Step 1: assistant has tu-different, but toolResult uses tu-orphan (mismatched)
            {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "tu-different", "name": "other_tool", "input": {}}}
            ]},
            _user_tool_result("tu-orphan", "some big result text here " * 10),
            # Step 2
            _assistant_tool_use("tu-2", "tool_b", {"x": 1}),
            _user_tool_result("tu-2", "result 2"),
            # Step 3
            _assistant_tool_use("tu-3", "tool_c", {"x": 2}),
            _user_tool_result("tu-3", "result 3"),
            # Step 4 (last-2-messages protected)
            _assistant_tool_use("tu-last", "final_tool", {}),
            _user_tool_result("tu-last", "final result"),
        ]

        # N=2: eligible turns at indices 2, 4, 6 (safe_end=7). Keep last 2 → trim idx 2.
        result = _call_trim(messages, keep_last_n=2)
        summary = _get_tool_result_text(result[2])
        assert "[context-trimmed]" in summary
        assert "unknown" in summary


class TestCountItemsNoCollectionKey:
    """Cover the JSON object branch where no well-known key exists and no lists."""

    def test_json_object_no_lists(self):
        """JSON object with only scalar values → None."""
        from src.core.truncation import count_items
        assert count_items('{"a": 1, "b": "hello"}') is None

    def test_json_object_empty_collection(self):
        """JSON object where all known keys have empty list → None."""
        from src.core.truncation import count_items
        # Largest list is empty — returns None because 0 not > 0
        assert count_items('{"other_key": []}') is None
