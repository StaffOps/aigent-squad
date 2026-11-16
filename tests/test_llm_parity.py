"""Spec 28 Task 9 — provider parity: one contract, both providers.

Each test runs against BedrockClient (boto3 mocked) and OpenAICompatClient
(httpx mocked with respx) and asserts the SAME caller-visible behavior.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from botocore.exceptions import ClientError

from src.core.config import settings
from src.core.guardrail import GuardrailBlockedError
from src.core.llm.types import LLMProvider

URL = "https://llm.test/v1/chat/completions"


def _bedrock_invoke_resp(text):
    body = json.dumps({"content": [{"text": text}], "usage": {"input_tokens": 3, "output_tokens": 2}}).encode()
    return {"body": MagicMock(read=MagicMock(return_value=body))}


def _bedrock_converse_resp(blocks, stop):
    return {"output": {"message": {"content": blocks}}, "stopReason": stop,
            "usage": {"inputTokens": 3, "outputTokens": 2}}


def _oa(text=None, tool_calls=None, finish="stop"):
    msg = {"content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2}}


class _Harness:
    """Builds a client of either provider and scripts its next responses."""

    def __init__(self, kind, monkeypatch, router):
        self.kind = kind
        self.router = router
        if kind == "bedrock":
            from src.core.bedrock import BedrockClient
            with patch("src.core.bedrock.boto3.client"):
                self.client = BedrockClient()
            self.client.client = MagicMock()
        else:
            monkeypatch.setattr(settings, "llm_provider", "openai")
            monkeypatch.setattr(settings, "llm_model_id", "gpt-test")
            monkeypatch.setattr(settings, "openai_api_key", "k")
            from src.core.llm.openai_provider import OpenAICompatClient
            self.client = OpenAICompatClient(base_url="https://llm.test/v1", http_client=httpx.Client())

    def text(self, *texts):
        if self.kind == "bedrock":
            self.client.client.invoke_model.side_effect = [_bedrock_invoke_resp(t) for t in texts]
        else:
            self.router.post(URL).mock(side_effect=[httpx.Response(200, json=_oa(t)) for t in texts])

    def throttle_then_text(self, text):
        if self.kind == "bedrock":
            err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "InvokeModel")
            self.client.client.invoke_model.side_effect = [err, _bedrock_invoke_resp(text)]
        else:
            self.router.post(URL).mock(side_effect=[httpx.Response(429), httpx.Response(200, json=_oa(text))])

    def always_throttle(self):
        if self.kind == "bedrock":
            err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "InvokeModel")
            self.client.client.invoke_model.side_effect = err
        else:
            self.router.post(URL).mock(return_value=httpx.Response(429))

    def converse_tool_use(self):
        if self.kind == "bedrock":
            self.client.client.converse.return_value = _bedrock_converse_resp(
                [{"toolUse": {"toolUseId": "id1", "name": "t", "input": {"a": 1}}}], "tool_use")
        else:
            self.router.post(URL).mock(return_value=httpx.Response(200, json=_oa(
                tool_calls=[{"id": "id1", "type": "function", "function": {"name": "t", "arguments": '{"a": 1}'}}],
                finish="tool_calls")))

    def converse_text(self, text):
        if self.kind == "bedrock":
            self.client.client.converse.return_value = _bedrock_converse_resp([{"text": text}], "end_turn")
        else:
            self.router.post(URL).mock(return_value=httpx.Response(200, json=_oa(text)))


@pytest.fixture(params=["bedrock", "openai"])
def h(request, monkeypatch):
    monkeypatch.setattr("src.core.llm.service.time.sleep", lambda _s: None)
    with respx.mock(assert_all_called=False) as router:
        yield _Harness(request.param, monkeypatch, router)


MSG = [{"role": "user", "content": "hi"}]
CMSG = [{"role": "user", "content": [{"text": "hi"}]}]


def test_both_satisfy_protocol(h):
    assert isinstance(h.client, LLMProvider)


@pytest.mark.asyncio
async def test_invoke_returns_text(h):
    h.text("answer")
    assert await h.client.invoke(MSG, "sys") == "answer"


@pytest.mark.asyncio
async def test_invoke_metrics_contract(h):
    h.text("answer")
    with patch("src.core.llm.service.token_counter") as tc, \
         patch("src.core.llm.service.budget_tracker") as bt:
        await h.client.invoke(MSG, "sys", agent_id="aws", session_id="s")
    dirs = sorted(c.args[1]["direction"] for c in tc.add.call_args_list)
    assert dirs == ["input", "output"]
    assert all(c.args[1]["agent_id"] == "aws" and "model" in c.args[1] for c in tc.add.call_args_list)
    bt.record_usage.assert_called_once_with("s", 3, 2)


@pytest.mark.asyncio
async def test_throttle_is_retried(h):
    h.throttle_then_text("after-retry")
    assert await h.client.invoke(MSG, "sys") == "after-retry"


@pytest.mark.asyncio
async def test_exhaustion_raises_and_counts_breaker_failure(h):
    h.always_throttle()
    with pytest.raises(Exception):
        await h.client.invoke(MSG, "sys")
    assert h.client.circuit_breaker._mem_failure_count == 1


@pytest.mark.asyncio
async def test_open_breaker_rejects(h):
    h.client.circuit_breaker.can_execute = MagicMock(return_value=False)
    with pytest.raises(Exception, match="circuit breaker is OPEN"):
        await h.client.invoke(MSG, "sys")


@pytest.mark.asyncio
async def test_output_guardrail_block_does_not_trip_breaker(h):
    h.text("leak")
    def apply(text, source="INPUT", **_kw):
        if source == "OUTPUT":
            raise GuardrailBlockedError("blocked", "OUTPUT")
    with patch("src.core.llm.service.guardrail") as g:
        g.apply.side_effect = apply
        with pytest.raises(GuardrailBlockedError):
            await h.client.invoke(MSG, "sys")
    assert h.client.circuit_breaker._mem_failure_count == 0


@pytest.mark.asyncio
async def test_converse_tool_use_contract(h):
    h.converse_tool_use()
    res = await h.client.converse(CMSG, "sys", tool_config={"tools": [{"toolSpec": {"name": "t"}}]})
    assert res["stop_reason"] == "tool_use"
    assert res["content"] == [{"type": "tool_use", "toolUseId": "id1", "name": "t", "input": {"a": 1}}]
    assert res["usage"] == {"input_tokens": 3, "output_tokens": 2}


@pytest.mark.asyncio
async def test_converse_text_contract(h):
    h.converse_text("final")
    res = await h.client.converse(CMSG, "sys", model_id_override="override-model")
    assert res["stop_reason"] == "end_turn"
    assert res["content"] == [{"type": "text", "text": "final"}]


@pytest.mark.asyncio
async def test_bedrock_retry_logs_carry_error_code(monkeypatch):
    """Reviewer fix 5: the ClientError code is back in the retry/failure log extra."""
    from src.core.bedrock import BedrockClient
    monkeypatch.setattr("src.core.llm.service.time.sleep", lambda _s: None)
    with patch("src.core.bedrock.boto3.client"):
        client = BedrockClient()
    client.client = MagicMock()
    err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "InvokeModel")
    client.client.invoke_model.side_effect = err
    with patch("src.core.llm.service.logger") as log, pytest.raises(ClientError):
        await client.invoke(MSG, "sys")
    warn_codes = {c.kwargs["extra"]["error_code"] for c in log.warning.call_args_list}
    err_codes = {c.kwargs["extra"].get("error_code") for c in log.error.call_args_list}
    assert warn_codes == {"ThrottlingException"} and "ThrottlingException" in err_codes
    assert client._error_code(ValueError("x")) is None


@pytest.mark.asyncio
async def test_base_service_close_is_noop():
    from src.core.bedrock import BedrockClient
    with patch("src.core.bedrock.boto3.client"):
        assert BedrockClient().close() is None
