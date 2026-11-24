"""Spec 14 L4/L5 on the STREAMING agentic path — independent contract tests.

Written by a test author independent of the implementation
(verification-independence). Tests target the contract of
``src.core.stream_output_guard.guard_agentic_stream`` and its wiring through
``GenericAgent.process_request_streaming`` -> supervisor
``process_request_streaming`` -> ``openai_compat.sse_stream_agentic``:

- A secret in the final answer never reaches the SSE bytes or history.
- A blocked answer ends with the refusal chunk + ``content_filter`` + [DONE].
- Canary leaks (also across a chunk boundary) are redacted + audited; the
  stream continues. Canary escalation blocks.
- Trace lines (thinking / tool-result summary) with secrets are hidden; the
  stream continues.
- Clean answers pass byte-identical, re-sliced into <=200-char chunks.
- Settings toggles are honoured.

The real agentic loop is replaced by a fake async generator; everything
downstream of it (guard, supervisor wrapper, SSE encoder) is real.
"""
from __future__ import annotations

import json
import re
from typing import Any, AsyncGenerator, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.adapters import DatasourceAdapter
from src.core.agent_config import AgentConfig
from src.core.agentic_loop_streaming import (
    StepDone,
    StepFinalChunk,
    StepRouting,
    StepThinking,
    StepToolCall,
    StepToolResult,
)
from src.core.canary import CanaryGuard
from src.core.config import settings
from src.core.generic_agent import GenericAgent
from src.core.guardrail import GuardrailBlockedError
from src.core.stream_output_guard import (
    BLOCKED_FINISH_REASON,
    FINAL_CHUNK_SIZE,
    REFUSAL_TEXT,
    guard_agentic_stream,
)
from src.supervisor.openai_compat import sse_stream_agentic

AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"  # 16 uppercase chars after the prefix
PEM_HEADER = "-----BEGIN RSA PRIVATE KEY-----"
CANARY_RE = re.compile(r"CNRY-[0-9a-f]{32}")


# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _collect(gen: AsyncGenerator[Any, None]) -> List[Any]:
    return [e async for e in gen]


async def _events(*items: Any) -> AsyncGenerator[Any, None]:
    for item in items:
        yield item


def _chunks(text: str, size: int = 50) -> List[StepFinalChunk]:
    return [StepFinalChunk(text=text[i:i + size]) for i in range(0, len(text), size)]


def _final_text(events: List[Any]) -> str:
    return "".join(e.text for e in events if isinstance(e, StepFinalChunk))


def _guard(events: AsyncGenerator[Any, None], tokens: List[str] | None = None,
           session_id: str = "sess-guard") -> AsyncGenerator[Any, None]:
    return guard_agentic_stream(
        events,
        agent_id="aws",
        user_id="u1",
        session_id=session_id,
        canary_guard=CanaryGuard(),
        canary_tokens=tokens or [],
    )


def _sse_content(frames: List[str]) -> str:
    """Concatenate every content delta from SSE frames (ignores [DONE])."""
    out = []
    for frame in frames:
        payload = frame.removeprefix("data: ").strip()
        if payload == "[DONE]":
            continue
        for choice in json.loads(payload)["choices"]:
            out.append(choice["delta"].get("content") or "")
    return "".join(out)


def _sse_finish_reasons(frames: List[str]) -> List[str]:
    reasons = []
    for frame in frames:
        payload = frame.removeprefix("data: ").strip()
        if payload == "[DONE]":
            continue
        for choice in json.loads(payload)["choices"]:
            if choice.get("finish_reason"):
                reasons.append(choice["finish_reason"])
    return reasons


class _FakeAdapter(DatasourceAdapter):
    """Non-MCP adapter: its data is what the canary gets injected into."""

    def __init__(self, response: str = "node status: healthy") -> None:
        self._response = response

    async def _collect(self, query: str) -> str:
        return self._response


def _agent(name: str = "aws", with_infra: bool = True) -> GenericAgent:
    config = AgentConfig(
        name=name, description="t", domain="testing", capabilities=["t"], datasources=[],
    )
    adapters: List[DatasourceAdapter] = [_FakeAdapter()] if with_infra else []
    return GenericAgent(config=config, prompt="You are a test agent.", adapters=adapters)


def _patch_loop(monkeypatch, make_events) -> dict:
    """Replace run_agentic_loop_streaming. ``make_events(system_prompt)`` returns
    the list of step events; the system prompt carries the injected canaries."""
    seen: dict = {}

    def fake_loop(**kwargs: Any) -> AsyncGenerator[Any, None]:
        seen.update(kwargs)
        return _events(*make_events(kwargs["system_prompt"]))

    monkeypatch.setattr(
        "src.core.agentic_loop_streaming.run_agentic_loop_streaming", fake_loop,
    )
    return seen


@pytest.fixture
def storage_spy(monkeypatch):
    spy = MagicMock()
    spy.save_chat_message = AsyncMock()
    spy.fetch_all_chats = AsyncMock(return_value=[])
    spy.fetch_chat = AsyncMock(return_value=[])
    monkeypatch.setattr("src.core.state_store.storage", spy)
    monkeypatch.setattr("src.supervisor.agent.storage", spy)

    budget = MagicMock()
    monkeypatch.setattr("src.supervisor.agent.budget_tracker", budget)
    scanner = MagicMock()
    scanner.scan = MagicMock(side_effect=lambda text, **kw: text)
    monkeypatch.setattr("src.supervisor.agent.InputScanner", MagicMock(return_value=scanner))
    monkeypatch.setattr("src.core.guardrail.guardrail", MagicMock())
    monkeypatch.setattr("src.supervisor.agent.should_investigate", lambda *a, **kw: False)
    span = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=False)
    tracer = MagicMock()
    tracer.start_as_current_span = MagicMock(return_value=span)
    monkeypatch.setattr("src.supervisor.agent.tracer", tracer)
    return spy


def _supervisor(agents: dict, classification: Any = None):
    from src.supervisor.agent import SupervisorAgent

    sup = object.__new__(SupervisorAgent)
    sup.agents = agents
    sup.max_agents = 3
    sup.skill_registry = MagicMock()
    classifier = AsyncMock()
    if classification is not None:
        classifier.classify.return_value = classification
    sup.classifier = classifier
    return sup


def _saved_assistant(spy) -> List[str]:
    return [
        c.args[3].content for c in spy.save_chat_message.await_args_list
        if c.args[3].role == "assistant"
    ]


async def _run_e2e(monkeypatch, storage_spy, make_events, *, session_id: str,
                   autoroute: bool = False) -> tuple[List[str], List[str]]:
    """Drive real GenericAgent -> real supervisor -> real SSE encoder.

    Returns (sse_frames, saved_assistant_contents)."""
    from src.core.classifier import AgentMatch, ClassifierResult

    _patch_loop(monkeypatch, make_events)
    agent = _agent("aws")
    monkeypatch.setattr(agent, "has_agentic_tools", lambda: True)
    if autoroute:
        sup = _supervisor(
            {"aws": agent},
            ClassifierResult(agents=[AgentMatch(agent="aws", confidence=0.9)], reasoning="r"),
        )
        gen = await sup.process_request_streaming(
            user_input="list instances", user_id="u1", session_id=session_id,
        )
    else:
        sup = _supervisor({"aws": agent})
        gen = await sup.process_request_streaming(
            user_input="list instances", user_id="u1", session_id=session_id,
            force_agent="aws",
        )
    assert gen is not None
    frames = await _collect(sse_stream_agentic(gen, "aigent-squad-aws"))
    return frames, _saved_assistant(storage_spy)


# ═══════════════════════════════════════════════════════════════════════════════
# End to end: GenericAgent -> supervisor -> SSE + history
# ═══════════════════════════════════════════════════════════════════════════════


class TestEndToEndSecretNeverLeaves:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("autoroute", [False, True], ids=["forced", "autoroute"])
    @pytest.mark.parametrize("secret", [AWS_KEY, PEM_HEADER], ids=["aws_key", "pem"])
    async def test_secret_in_final_answer_not_in_sse_nor_history(
        self, monkeypatch, storage_spy, secret, autoroute,
    ):
        answer = f"Here you go. The credential is {secret} — keep it safe. " * 3

        frames, saved = await _run_e2e(
            monkeypatch, storage_spy,
            lambda _sp: [StepToolCall(tool_name="t", args_display=""), *_chunks(answer, 37),
                         StepDone(finish_reason="stop")],
            session_id=f"e2e-secret-{autoroute}-{len(secret)}",
        )

        raw = "".join(frames)
        assert secret not in raw
        assert "Here you go" not in raw  # none of the original answer is emitted
        assert REFUSAL_TEXT in _sse_content(frames)
        assert _sse_finish_reasons(frames) == [BLOCKED_FINISH_REASON]
        assert frames[-1] == "data: [DONE]\n\n"
        assert saved == []  # blocked answer never saved to history
        for call in storage_spy.save_chat_message.await_args_list:
            assert secret not in call.args[3].content

    @pytest.mark.asyncio
    async def test_canary_leak_redacted_in_sse_and_history_stream_continues(
        self, monkeypatch, storage_spy,
    ):
        captured: dict = {}

        def make(system_prompt: str) -> List[Any]:
            tokens = CANARY_RE.findall(system_prompt)
            assert tokens, "canary must be injected into non-MCP infra_data"
            captured["token"] = tokens[0]
            answer = f"All 3 instances healthy. Session: {tokens[0]} end of report."
            # Split the token across a chunk boundary on purpose.
            cut = answer.index(tokens[0]) + 10
            return [StepFinalChunk(text=answer[:cut]), StepFinalChunk(text=answer[cut:]),
                    StepDone(finish_reason="stop")]

        frames, saved = await _run_e2e(monkeypatch, storage_spy, make, session_id="e2e-canary")

        token = captured["token"]
        hex_part = token.removeprefix("CNRY-")
        raw = "".join(frames)
        assert hex_part not in raw
        content = _sse_content(frames)
        assert "All 3 instances healthy." in content
        assert "[redacted]" in content
        assert _sse_finish_reasons(frames) == ["stop"]
        assert len(saved) == 1
        assert hex_part not in saved[0]
        assert "[redacted]" in saved[0]

    @pytest.mark.asyncio
    async def test_clean_answer_saved_verbatim(self, monkeypatch, storage_spy):
        answer = "There are 3 running EC2 instances in us-east-1, all healthy."
        frames, saved = await _run_e2e(
            monkeypatch, storage_spy,
            lambda _sp: [*_chunks(answer, 7), StepDone(finish_reason="stop")],
            session_id="e2e-clean", autoroute=True,
        )
        assert answer in _sse_content(frames)
        assert saved == [answer]
        assert _sse_finish_reasons(frames) == ["stop"]


class TestGenericAgentWiring:
    @pytest.mark.asyncio
    async def test_canary_injected_into_infra_data_and_detected(self, monkeypatch):
        captured: dict = {}

        def make(system_prompt: str) -> List[Any]:
            captured["tokens"] = CANARY_RE.findall(system_prompt)
            return [StepFinalChunk(text=f"ref {captured['tokens'][-1]} done here."),
                    StepDone()]

        _patch_loop(monkeypatch, make)
        gen = await _agent(with_infra=True).process_request_streaming(
            input_text="q", user_id="u1", session_id="wire-1", chat_history=[],
        )
        events = await _collect(gen)
        assert len(captured["tokens"]) == 2
        text = _final_text(events)
        assert captured["tokens"][-1] not in text
        assert "[redacted]" in text
        assert isinstance(events[-1], StepDone) and events[-1].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_no_infra_data_means_no_canary_but_filter_still_runs(self, monkeypatch):
        seen = _patch_loop(
            monkeypatch,
            lambda _sp: [StepFinalChunk(text=f"key {AWS_KEY} here"), StepDone()],
        )
        gen = await _agent(with_infra=False).process_request_streaming(
            input_text="q", user_id="u1", session_id="wire-2", chat_history=[],
        )
        events = await _collect(gen)
        assert not CANARY_RE.search(seen["system_prompt"])
        assert _final_text(events) == REFUSAL_TEXT
        assert events[-1] == StepDone(finish_reason=BLOCKED_FINISH_REASON)

    @pytest.mark.asyncio
    async def test_non_streaming_process_request_still_blocks_secret(self, monkeypatch):
        """Contract 7: the non-streaming path is unchanged (still raises 403-mapped error)."""
        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=f"Your key is {AWS_KEY} ok.")
        monkeypatch.setattr("src.core.generic_agent.llm", llm)
        with pytest.raises(GuardrailBlockedError) as exc:
            await _agent(with_infra=False).process_request(
                input_text="q", user_id="u1", session_id="wire-3", chat_history=[],
            )
        assert "leak:aws_access_key" in exc.value.categories


# ═══════════════════════════════════════════════════════════════════════════════
# Unit: guard_agentic_stream
# ═══════════════════════════════════════════════════════════════════════════════


class TestFinalAnswer:
    @pytest.mark.asyncio
    async def test_clean_answer_identical_and_resliced(self):
        answer = "".join(f"line {i}: all good. " for i in range(60))  # > 1000 chars
        assert len(answer) > 3 * FINAL_CHUNK_SIZE
        events = await _collect(_guard(_events(*_chunks(answer, 13), StepDone())))

        finals = [e for e in events if isinstance(e, StepFinalChunk)]
        assert _final_text(events) == answer
        assert all(len(e.text) <= FINAL_CHUNK_SIZE for e in finals)
        assert len(finals) == -(-len(answer) // FINAL_CHUNK_SIZE)
        assert events[-1] == StepDone(finish_reason="stop")

    @pytest.mark.asyncio
    async def test_final_chunks_held_until_done(self):
        """Nothing of the answer is yielded before StepDone arrives."""
        released: List[str] = []

        async def inner():
            yield StepFinalChunk(text="first part ")
            released.append("after-first")
            yield StepFinalChunk(text="second part")
            released.append("after-second")
            yield StepDone()

        gen = _guard(inner())
        first = await gen.__anext__()
        assert isinstance(first, StepFinalChunk)
        assert released == ["after-first", "after-second"]
        await gen.aclose()

    @pytest.mark.asyncio
    async def test_finish_reason_length_preserved(self):
        events = await _collect(_guard(_events(StepFinalChunk(text="partial answer ok"),
                                               StepDone(finish_reason="length"))))
        assert events[-1] == StepDone(finish_reason="length")

    @pytest.mark.asyncio
    async def test_secret_split_across_chunks_blocked(self):
        events = await _collect(_guard(_events(
            StepFinalChunk(text="Your key: AKIAABCD"),
            StepFinalChunk(text="EFGHIJKLMNOP is active."),
            StepDone(),
        )))
        assert events == [StepFinalChunk(text=REFUSAL_TEXT),
                          StepDone(finish_reason=BLOCKED_FINISH_REASON)]

    @pytest.mark.asyncio
    async def test_quality_guard_structural_defect_blocks(self):
        events = await _collect(_guard(_events(
            StepFinalChunk(text="<use_mcp_tool>describe_instances</use_mcp_tool> answer"),
            StepDone(),
        )))
        assert events[-2:] == [StepFinalChunk(text=REFUSAL_TEXT),
                               StepDone(finish_reason=BLOCKED_FINISH_REASON)]

    @pytest.mark.asyncio
    async def test_inner_generator_raising_block_yields_refusal(self):
        async def inner():
            yield StepToolCall(tool_name="t", args_display="")
            yield StepFinalChunk(text="original answer text that must vanish")
            raise GuardrailBlockedError(reason="blocked", source="OUTPUT", categories=["l1"])

        events = await _collect(_guard(inner()))
        assert isinstance(events[0], StepToolCall)
        assert "original answer" not in _final_text(events)
        assert events[-2:] == [StepFinalChunk(text=REFUSAL_TEXT),
                               StepDone(finish_reason=BLOCKED_FINISH_REASON)]

    @pytest.mark.asyncio
    async def test_no_step_done_clean_text_still_emitted(self):
        events = await _collect(_guard(_events(StepFinalChunk(text="answer without done marker"))))
        assert _final_text(events) == "answer without done marker"

    @pytest.mark.asyncio
    async def test_no_step_done_secret_still_filtered(self):
        events = await _collect(_guard(_events(StepFinalChunk(text=f"leak {AWS_KEY} here"))))
        assert AWS_KEY not in _final_text(events)
        assert _final_text(events) == REFUSAL_TEXT
        assert events[-1] == StepDone(finish_reason=BLOCKED_FINISH_REASON)

    @pytest.mark.asyncio
    async def test_blocked_stream_through_sse_ends_with_content_filter_and_done(self):
        frames = await _collect(sse_stream_agentic(
            _guard(_events(StepFinalChunk(text=f"{PEM_HEADER}\nMIIE..."), StepDone())),
            "m",
        ))
        assert PEM_HEADER not in "".join(frames)
        assert _sse_content(frames).endswith(REFUSAL_TEXT)
        assert _sse_finish_reasons(frames) == [BLOCKED_FINISH_REASON]
        assert frames[-1] == "data: [DONE]\n\n"


class TestQualityGuardErrors:
    @pytest.mark.asyncio
    async def test_quality_guard_internal_error_does_not_drop_answer(self, monkeypatch):
        """Review P3: same as process_request — a non-block quality-guard error is swallowed."""
        def _boom(self, *a, **kw):
            raise RuntimeError("detector bug")
        monkeypatch.setattr("src.core.response_quality.ResponseQualityGuard.scan", _boom)
        events = await _collect(_guard(_events(
            StepFinalChunk(text="All 3 pods are running fine."), StepDone(),
        )))
        assert _final_text(events) == "All 3 pods are running fine."
        assert events[-1] == StepDone(finish_reason="stop")


class TestCanary:
    @pytest.mark.asyncio
    async def test_canary_split_across_chunk_boundary_redacted_and_audited(self, monkeypatch):
        guard = CanaryGuard()
        _, tokens = guard.inject("data")
        audit = MagicMock()
        monkeypatch.setattr(CanaryGuard, "_audit", staticmethod(audit))
        token = tokens[1]
        answer = f"Report ok. Trace: {token}. Bye now."
        cut = answer.index(token) + 17

        events = await _collect(guard_agentic_stream(
            _events(StepFinalChunk(text=answer[:cut]), StepFinalChunk(text=answer[cut:]), StepDone()),
            agent_id="aws", user_id="u1", session_id="canary-split",
            canary_guard=guard, canary_tokens=tokens,
        ))
        text = _final_text(events)
        assert token.removeprefix("CNRY-") not in text
        assert text.startswith("Report ok. Trace: ") and "[redacted]" in text
        assert events[-1] == StepDone(finish_reason="stop")
        audit.assert_called_once()
        assert token not in str(audit.call_args)  # audit never carries the token

    @pytest.mark.asyncio
    async def test_canary_escalation_blocks(self, monkeypatch):
        monkeypatch.setattr(settings, "canary_escalation_threshold", 1)
        guard = CanaryGuard()
        _, tokens = guard.inject("data")
        session = "canary-escalate"

        def stream():
            return guard_agentic_stream(
                _events(StepFinalChunk(text=f"see {tokens[0]} please"), StepDone()),
                agent_id="aws", user_id="u1", session_id=session,
                canary_guard=guard, canary_tokens=tokens,
            )

        first = await _collect(stream())
        assert first[-1] == StepDone(finish_reason="stop")
        second = await _collect(stream())
        assert second == [StepFinalChunk(text=REFUSAL_TEXT),
                          StepDone(finish_reason=BLOCKED_FINISH_REASON)]

    @pytest.mark.asyncio
    async def test_canary_in_thinking_redacted(self):
        guard = CanaryGuard()
        _, tokens = guard.inject("data")
        events = await _collect(guard_agentic_stream(
            _events(StepThinking(text=f"I see marker {tokens[0]} in data"),
                    StepFinalChunk(text="final answer"), StepDone()),
            agent_id="aws", user_id="u1", session_id="canary-think",
            canary_guard=guard, canary_tokens=tokens,
        ))
        thinking = events[0]
        assert isinstance(thinking, StepThinking)
        assert tokens[0].removeprefix("CNRY-") not in thinking.text
        assert "[redacted]" in thinking.text
        assert _final_text(events) == "final answer"


class TestTraceLines:
    @pytest.mark.asyncio
    async def test_thinking_with_secret_replaced_stream_continues(self, monkeypatch):
        metric = MagicMock()
        monkeypatch.setattr("src.core.stream_output_guard.stream_output_interventions", metric)
        events = await _collect(_guard(_events(
            StepThinking(text=f"the key {AWS_KEY} appears in config"),
            StepFinalChunk(text="Final answer without secrets."),
            StepDone(),
        )))
        assert AWS_KEY not in events[0].text
        assert isinstance(events[0], StepThinking) and events[0].text
        assert _final_text(events) == "Final answer without secrets."
        assert events[-1] == StepDone(finish_reason="stop")
        metric.add.assert_called_once_with(1, {"agent_id": "aws", "action": "trace_redacted"})

    @pytest.mark.asyncio
    async def test_tool_result_with_secret_replaced_keeps_tool_name(self):
        events = await _collect(_guard(_events(
            StepToolResult(tool_name="get_secret", summary=f"📦 {PEM_HEADER} MIIE"),
            StepFinalChunk(text="Done reviewing."),
            StepDone(),
        )))
        result = events[0]
        assert isinstance(result, StepToolResult)
        assert result.tool_name == "get_secret"
        assert PEM_HEADER not in result.summary and result.summary
        assert events[-1] == StepDone(finish_reason="stop")

    @pytest.mark.asyncio
    async def test_secret_trace_through_sse_hidden(self):
        frames = await _collect(sse_stream_agentic(
            _guard(_events(StepToolResult(tool_name="t", summary=f"found {AWS_KEY} in env"),
                           StepFinalChunk(text="answer"), StepDone())),
            "m",
        ))
        assert AWS_KEY not in "".join(frames)
        assert _sse_finish_reasons(frames) == ["stop"]

    @pytest.mark.asyncio
    async def test_tool_call_args_with_secret_hidden(self):
        """Review P2: a secret passed as an arg VALUE (not a secret-like key) is hidden."""
        events = await _collect(_guard(_events(
            StepToolCall(tool_name="describe", args_display=f'filter="{AWS_KEY}"'),
            StepFinalChunk(text="Nothing found for that filter."),
            StepDone(),
        )))
        call = events[0]
        assert isinstance(call, StepToolCall)
        assert call.tool_name == "describe"
        assert AWS_KEY not in call.args_display and call.args_display
        assert _final_text(events) == "Nothing found for that filter."
        assert events[-1] == StepDone(finish_reason="stop")

    @pytest.mark.asyncio
    async def test_tool_call_args_canary_redacted(self):
        token = "CNRY-" + "0123456789abcdef" * 2
        events = await _collect(_guard(_events(
            StepToolCall(tool_name="search", args_display=f'q="{token}"'),
            StepDone(),
        ), tokens=[token], session_id="sess-args-canary"))
        assert token not in events[0].args_display
        assert "[redacted]" in events[0].args_display

    @pytest.mark.asyncio
    async def test_clean_trace_unchanged(self):
        thinking = StepThinking(text="checking the pods now")
        result = StepToolResult(tool_name="t", summary="📦 3 items (120 chars)")
        events = await _collect(_guard(_events(thinking, result, StepDone())))
        assert events == [thinking, result, StepDone()]


class TestPassthroughAndMetrics:
    @pytest.mark.asyncio
    async def test_other_events_pass_through_unchanged(self):
        routing = StepRouting(agent="aws", confidence=0.9, reasoning="r", sub_query="")
        call = StepToolCall(tool_name="describe", args_display='region="us-east-1"')
        unknown = object()
        events = await _collect(_guard(_events(routing, call, unknown, StepDone())))
        assert events[0] is routing
        assert events[1] == call  # clean args: content unchanged (rebuilt after the scan)
        assert events[2] is unknown
        assert events[-1] == StepDone()

    @pytest.mark.asyncio
    async def test_blocked_metric_emitted(self, monkeypatch):
        metric = MagicMock()
        monkeypatch.setattr("src.core.stream_output_guard.stream_output_interventions", metric)
        await _collect(_guard(_events(StepFinalChunk(text=f"k={AWS_KEY} xx"), StepDone())))
        metric.add.assert_called_once_with(1, {"agent_id": "aws", "action": "blocked"})

    @pytest.mark.asyncio
    async def test_inner_generator_closed_on_early_consumer_close(self):
        closed: List[bool] = []

        async def inner():
            try:
                yield StepToolCall(tool_name="t", args_display="")
                yield StepFinalChunk(text="x" * 50)
                yield StepDone()
            finally:
                closed.append(True)

        gen = _guard(inner())
        await gen.__anext__()
        await gen.aclose()
        assert closed == [True]


class TestToggles:
    @pytest.mark.asyncio
    async def test_output_filter_disabled_passes_secret_through(self, monkeypatch):
        monkeypatch.setattr(settings, "output_filter_enabled", False)
        answer = f"Key is {AWS_KEY}, rotate it."
        events = await _collect(_guard(_events(StepFinalChunk(text=answer), StepDone())))
        assert _final_text(events) == answer
        assert events[-1] == StepDone(finish_reason="stop")

    @pytest.mark.asyncio
    async def test_canary_disabled_leaves_token(self, monkeypatch):
        monkeypatch.setattr(settings, "canary_enabled", False)
        token = "CNRY-" + "ab" * 16
        events = await _collect(_guard(
            _events(StepFinalChunk(text=f"ref {token} ok"), StepDone()), tokens=[token],
        ))
        assert token in _final_text(events)

    @pytest.mark.asyncio
    async def test_quality_guard_disabled_passes_scaffolding(self, monkeypatch):
        monkeypatch.setattr(settings, "response_quality_enabled", False)
        answer = "<tool_call>x</tool_call> visible"
        events = await _collect(_guard(_events(StepFinalChunk(text=answer), StepDone())))
        assert _final_text(events) == answer
