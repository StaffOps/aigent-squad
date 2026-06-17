"""Tests for the OpenAI-compatible bridge (spec 29).

Targets src/supervisor/openai_compat.py directly (server.py is in .coveragerc
omit). Validates the OpenAI contract against the spec, not the implementation.
"""
import json

import pytest

from src.supervisor.openai_compat import (
    ChatCompletionRequest,
    ChatMessage,
    UnknownModelError,
    build_completion,
    list_models,
    messages_to_user_input,
    resolve_target,
    sse_stream,
)

AGENTS = ["aws", "kubernetes", "finops"]


# ─── list_models ────────────────────────────────────────────────────

def test_list_models_includes_auto_and_per_agent():
    models = list_models(AGENTS)
    ids = [m.id for m in models.data]
    assert "aigent-squad" in ids
    assert "aigent-squad-aws" in ids
    assert "aigent-squad-kubernetes" in ids
    assert "aigent-squad-finops" in ids
    # auto + one per agent
    assert len(ids) == 1 + len(AGENTS)
    assert models.object == "list"
    assert all(m.object == "model" and m.owned_by == "aigent-squad" for m in models.data)


def test_list_models_empty_registry_still_has_auto():
    models = list_models([])
    assert [m.id for m in models.data] == ["aigent-squad"]


# ─── resolve_target ─────────────────────────────────────────────────

def test_resolve_target_auto_returns_none():
    assert resolve_target("aigent-squad", AGENTS) is None


def test_resolve_target_per_agent_returns_agent():
    assert resolve_target("aigent-squad-aws", AGENTS) == "aws"
    assert resolve_target("aigent-squad-finops", AGENTS) == "finops"


def test_resolve_target_unknown_agent_raises():
    with pytest.raises(UnknownModelError):
        resolve_target("aigent-squad-nosuch", AGENTS)


def test_resolve_target_unrelated_model_raises():
    with pytest.raises(UnknownModelError):
        resolve_target("gpt-4o", AGENTS)


# ─── messages_to_user_input ─────────────────────────────────────────

def test_messages_translator_takes_last_user_turn():
    msgs = [
        ChatMessage(role="user", content="first"),
        ChatMessage(role="assistant", content="reply"),
        ChatMessage(role="user", content="second"),
    ]
    assert messages_to_user_input(msgs) == "second"


def test_messages_translator_prepends_system_context():
    msgs = [
        ChatMessage(role="system", content="be terse"),
        ChatMessage(role="user", content="list ec2"),
    ]
    out = messages_to_user_input(msgs)
    assert "be terse" in out and "list ec2" in out


def test_messages_translator_no_user_falls_back_to_last():
    msgs = [ChatMessage(role="assistant", content="orphan")]
    assert messages_to_user_input(msgs) == "orphan"


def test_messages_translator_empty():
    assert messages_to_user_input([]) == ""


# ─── build_completion (non-stream) ──────────────────────────────────

def test_build_completion_shapes_openai_response():
    result = {"agent": "aws", "response": "Found 3 instances", "confidence": 0.9}
    resp = build_completion(result, "aigent-squad")
    assert resp.object == "chat.completion"
    assert resp.model == "aigent-squad"
    assert resp.choices[0].message.role == "assistant"
    assert resp.choices[0].message.content == "Found 3 instances"
    assert resp.choices[0].finish_reason == "stop"
    assert resp.id.startswith("chatcmpl-")
    assert resp.usage.total_tokens == 0


def test_build_completion_missing_response_is_empty_string():
    resp = build_completion({"agent": "aws"}, "aigent-squad")
    assert resp.choices[0].message.content == ""


# ─── sse_stream (stream) ────────────────────────────────────────────

def _parse_sse(frames: list[str]) -> list:
    """Extract JSON payloads from SSE frames, ignoring the [DONE] sentinel."""
    out = []
    for f in frames:
        assert f.startswith("data: ") and f.endswith("\n\n")
        payload = f[len("data: "):].strip()
        if payload == "[DONE]":
            out.append("[DONE]")
        else:
            out.append(json.loads(payload))
    return out


@pytest.mark.asyncio
async def test_sse_stream_emits_prelude_content_finish_done():
    result = {"agent": "aws", "response": "hello world"}
    frames = [f async for f in sse_stream(result, "aigent-squad")]
    parsed = _parse_sse(frames)

    # Last frame is the DONE sentinel.
    assert parsed[-1] == "[DONE]"
    # Prelude carries the assistant role.
    assert parsed[0]["choices"][0]["delta"]["role"] == "assistant"
    # Some frame carries the full content.
    contents = [
        p["choices"][0]["delta"].get("content")
        for p in parsed[:-1]
        if isinstance(p, dict)
    ]
    assert "hello world" in contents
    # A finish frame exists.
    assert any(
        isinstance(p, dict) and p["choices"][0]["finish_reason"] == "stop"
        for p in parsed[:-1]
    )
    # All chunks share one id and the chunk object type.
    ids = {p["id"] for p in parsed[:-1] if isinstance(p, dict)}
    assert len(ids) == 1
    assert all(p["object"] == "chat.completion.chunk" for p in parsed[:-1] if isinstance(p, dict))


@pytest.mark.asyncio
async def test_sse_stream_empty_response_skips_content_frame():
    result = {"agent": "aws", "response": ""}
    frames = [f async for f in sse_stream(result, "aigent-squad")]
    parsed = _parse_sse(frames)
    assert parsed[-1] == "[DONE]"
    # No content delta when the answer is empty (prelude + finish + done only).
    content_frames = [
        p for p in parsed[:-1]
        if isinstance(p, dict) and p["choices"][0]["delta"].get("content")
    ]
    assert content_frames == []


# ─── request model parsing ──────────────────────────────────────────

def test_request_defaults_stream_false():
    req = ChatCompletionRequest(model="aigent-squad", messages=[ChatMessage(role="user", content="hi")])
    assert req.stream is False
    assert req.user is None
