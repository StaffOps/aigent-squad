"""Spec 28 — independent contract tests (verification-independence, Task 10).

Derived from ``specs/28-llm-provider-abstraction`` requirements + design
Decisions 6, 8, 9, 10, 11, 12 — NOT from the implementation. Each test states the
clause it checks. httpx is mocked with respx; nothing touches the network.

Areas: secrets (Decision 11), startup validation (Decision 8), wire translation
(Decision 6), resilience + accounting (Decisions 3/9/11), agentic loop end-to-end
with the openai provider (spec 37 allowlist + budgets).
"""
from __future__ import annotations

import io
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from src.core.config import settings
from src.core.llm.types import ErrorClass, LLMProviderError

BASE = "https://llm.independent.test/v1"
URL = f"{BASE}/chat/completions"
SECRET = "sk-indep-SECRET-9f8e7d6c5b4a"
FILE_SECRET = "sk-from-FILE-1a2b3c4d5e6f"


# ── Fixtures / helpers ───────────────────────────────────────────────────────


@pytest.fixture
def openai_env(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_model_id", "agent-m")
    monkeypatch.setattr(settings, "llm_classifier_model_id", None)
    monkeypatch.setattr(settings, "llm_synthesis_model_id", None)
    monkeypatch.setattr(settings, "openai_base_url", BASE)
    monkeypatch.setattr(settings, "openai_api_key", SECRET)
    monkeypatch.setattr(settings, "openai_api_key_file", None)
    monkeypatch.setattr(settings, "openai_api_key_header", "Authorization")
    monkeypatch.setattr(settings, "openai_max_tokens_field", "max_tokens")
    monkeypatch.setattr(settings, "llm_price_input_per_1m", None)
    monkeypatch.setattr(settings, "llm_price_output_per_1m", None)
    monkeypatch.setattr(settings, "guardrail_enabled", True)
    monkeypatch.setattr(settings, "guardrail_id", None)
    sleeps: list[float] = []
    monkeypatch.setattr("src.core.llm.service.time.sleep", sleeps.append)
    return sleeps


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def app_logs():
    """Capture the app logger (JSON-formatted, incl. extras + tracebacks)."""
    from src.core.logger import JSONFormatter, logger
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    old = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield stream
    logger.removeHandler(handler)
    logger.setLevel(old)


def _client():
    from src.core.llm.openai_provider import OpenAICompatClient
    return OpenAICompatClient(http_client=httpx.Client())


def _completion(text: Any = "ok", tool_calls=None, finish="stop", usage=None) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": usage if usage is not None else {"prompt_tokens": 10, "completion_tokens": 4}}


def _call(cid: str, name: str, args: Any) -> dict[str, Any]:
    raw = args if isinstance(args, str) else json.dumps(args)
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": raw}}


def _body(route, i: int = -1) -> dict[str, Any]:
    return json.loads(route.calls[i].request.content)


USER = [{"role": "user", "content": "hello"}]
CUSER = [{"role": "user", "content": [{"text": "hello"}]}]


# ═════════════════════════════════════════════════════════════════════════════
# 1. Secrets (Decision 11)
# ═════════════════════════════════════════════════════════════════════════════


class TestSecrets:
    def test_key_file_wins_over_env_key(self, openai_env, monkeypatch, router, tmp_path):
        kf = tmp_path / "key"
        kf.write_text(FILE_SECRET + "\n")
        monkeypatch.setattr(settings, "openai_api_key_file", str(kf))
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))

        import asyncio
        asyncio.run(_client().invoke(USER, "sys"))

        auth = route.calls[0].request.headers["authorization"]
        assert auth == f"Bearer {FILE_SECRET}", "file-mounted key must win, trimmed"
        assert SECRET not in auth

    def test_key_file_alone_satisfies_startup(self, openai_env, monkeypatch, tmp_path):
        from src.core.model_tier import validate_tier_models_at_startup
        kf = tmp_path / "key"
        kf.write_text(FILE_SECRET)
        monkeypatch.setattr(settings, "openai_api_key", None)
        monkeypatch.setattr(settings, "openai_api_key_file", str(kf))
        validate_tier_models_at_startup()  # no raise

    def test_empty_key_file_fails_startup(self, openai_env, monkeypatch, tmp_path):
        from src.core.model_tier import validate_tier_models_at_startup
        kf = tmp_path / "key"
        kf.write_text("   \n")
        monkeypatch.setattr(settings, "openai_api_key", None)
        monkeypatch.setattr(settings, "openai_api_key_file", str(kf))
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            validate_tier_models_at_startup()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500, 503])
    async def test_error_paths_never_leak_key(self, openai_env, router, app_logs, status):
        """The server echoes the key (as OpenAI does for 401); nothing of ours may repeat it."""
        router.post(URL).mock(return_value=httpx.Response(status, json={"error": {
            "message": f"Incorrect API key provided: {SECRET}. body was {{'messages': ...}}",
            "type": "invalid_request_error", "code": "invalid_api_key"}}))
        with pytest.raises(Exception) as ei:
            await _client().invoke(USER, "secret-system-prompt", agent_id="aws")

        exc = ei.value
        for text in (str(exc), repr(exc), app_logs.getvalue()):
            assert SECRET not in text
        assert "secret-system-prompt" not in str(exc), "never the request body"
        assert isinstance(exc, LLMProviderError)
        assert exc.status_code == status

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", [
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.ConnectError("connection refused"),
    ])
    async def test_timeout_paths_never_leak_key(self, openai_env, router, app_logs, exc):
        route = router.post(URL).mock(side_effect=exc)
        with pytest.raises(type(exc)) as ei:
            await _client().invoke(USER, "sys")
        assert route.call_count == 3
        assert SECRET not in str(ei.value) and SECRET not in repr(ei.value)
        assert SECRET not in app_logs.getvalue()

    @pytest.mark.asyncio
    async def test_success_path_logs_and_metrics_never_carry_key(self, openai_env, router, app_logs, monkeypatch):
        import src.core.llm.service as svc
        recorders = {n: MagicMock() for n in ("token_counter", "estimated_cost", "llm_duration",
                                              "prompt_size_tokens", "llm_provider_errors")}
        for n, m in recorders.items():
            monkeypatch.setattr(svc, n, m)
        router.post(URL).mock(side_effect=[httpx.Response(429), httpx.Response(200, json=_completion())])

        await _client().invoke(USER, "sys", agent_id="aws")

        blob = app_logs.getvalue() + "".join(repr(m.mock_calls) for m in recorders.values())
        assert SECRET not in blob

    def test_client_repr_and_attributes_do_not_hold_key(self, openai_env):
        c = _client()
        assert SECRET not in repr(c)
        assert SECRET not in repr(vars(c))

    def test_provider_error_carries_only_bounded_facts(self):
        e = LLMProviderError("openai", ErrorClass.CLIENT_ERROR, status_code=401, code="invalid_api_key")
        assert "401" in str(e) and "invalid_api_key" in str(e) and "openai" in str(e)

    @pytest.mark.asyncio
    async def test_key_echoed_in_error_code_is_not_repeated(self, openai_env, router, app_logs):
        router.post(URL).mock(return_value=httpx.Response(401, json={"error": {"code": SECRET}}))
        with pytest.raises(LLMProviderError) as ei:
            await _client().invoke(USER, "sys")
        assert SECRET not in str(ei.value)
        assert SECRET not in app_logs.getvalue()

    @pytest.mark.asyncio
    async def test_unreadable_key_file_error_names_path_not_content(self, openai_env, monkeypatch, tmp_path):
        from src.core.llm.openai_provider import load_api_key
        missing = tmp_path / "nope"
        monkeypatch.setattr(settings, "openai_api_key_file", str(missing))
        with pytest.raises(RuntimeError) as ei:
            load_api_key()
        assert str(missing) in str(ei.value)
        assert SECRET not in str(ei.value)


# ═════════════════════════════════════════════════════════════════════════════
# 2. Startup validation (Decision 8) — loud for openai, unchanged for bedrock
# ═════════════════════════════════════════════════════════════════════════════


class TestStartupValidation:
    @pytest.mark.parametrize("field,value", [
        ("openai_base_url", None),
        ("openai_base_url", ""),
        ("openai_base_url", "   "),
        ("openai_base_url", "ftp://llm.test/v1"),
        ("openai_base_url", "llm.test/v1"),
        ("openai_base_url", "file:///etc/passwd"),
    ])
    def test_bad_base_url_refuses_boot(self, openai_env, monkeypatch, field, value):
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, field, value)
        with pytest.raises(RuntimeError, match="OPENAI_BASE_URL"):
            validate_tier_models_at_startup()

    @pytest.mark.parametrize("key", [None, "", "  "])
    def test_no_key_refuses_boot(self, openai_env, monkeypatch, key):
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, "openai_api_key", key)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            validate_tier_models_at_startup()

    @pytest.mark.parametrize("model", [None, "", "   "])
    def test_no_model_refuses_boot(self, openai_env, monkeypatch, model):
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, "llm_model_id", model)
        # Role-specific ids do NOT substitute for the required LLM_MODEL_ID.
        monkeypatch.setattr(settings, "llm_classifier_model_id", "small")
        with pytest.raises(RuntimeError, match="LLM_MODEL_ID"):
            validate_tier_models_at_startup()

    def test_all_errors_reported_and_key_never_in_message(self, openai_env, monkeypatch):
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, "openai_base_url", None)
        monkeypatch.setattr(settings, "llm_model_id", None)
        with pytest.raises(RuntimeError) as ei:
            validate_tier_models_at_startup()
        msg = str(ei.value)
        assert "OPENAI_BASE_URL" in msg and "LLM_MODEL_ID" in msg
        assert SECRET not in msg

    @pytest.mark.parametrize("url", ["http://localhost:11434/v1", "https://api.example.com/v1"])
    def test_http_and_https_accepted_and_us_prefix_not_required(self, openai_env, monkeypatch, url):
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, "openai_base_url", url)
        monkeypatch.setattr(settings, "llm_model_id", "llama3.1:8b")  # no `us.` prefix
        validate_tier_models_at_startup()

    def test_bedrock_defaults_need_no_new_env(self, monkeypatch):
        """Bedrock default boots with every new LLM_*/OPENAI_* var unset."""
        from src.core.model_tier import resolve_model, validate_tier_models_at_startup
        for name in ("llm_model_id", "llm_classifier_model_id", "llm_synthesis_model_id",
                     "openai_base_url", "openai_api_key", "openai_api_key_file",
                     "llm_price_input_per_1m", "llm_price_output_per_1m"):
            monkeypatch.setattr(settings, name, None)
        monkeypatch.setattr(settings, "llm_provider", "bedrock")
        validate_tier_models_at_startup()
        assert resolve_model("agent") == settings.bedrock_agent_model_id
        assert resolve_model("classifier") == settings.bedrock_classifier_model_id
        assert resolve_model("synthesis") == settings.bedrock_synthesis_model_id

    def test_default_settings_select_bedrock(self, monkeypatch):
        from src.core.config import Settings
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        assert Settings(redis_host="x").llm_provider == "bedrock"

    @pytest.mark.parametrize("bad", ["", "us.meta.llama3-70b"])
    def test_bedrock_invalid_tier_model_still_fails(self, monkeypatch, bad):
        """The bedrock tier check (empty / unknown family) is untouched by spec 28."""
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, "llm_provider", "bedrock")
        monkeypatch.setattr(settings, "bedrock_tier_fast_model_id", bad)
        with pytest.raises(RuntimeError, match="BEDROCK_TIER_FAST_MODEL_ID"):
            validate_tier_models_at_startup()

    def test_openai_skips_bedrock_tier_family_check(self, openai_env, monkeypatch):
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, "bedrock_tier_fast_model_id", "us.meta.llama3-70b")
        validate_tier_models_at_startup()

    def test_openai_tiers_collapse_to_role_models(self, openai_env, monkeypatch):
        from src.core.model_tier import resolve_model_for_tier
        monkeypatch.setattr(settings, "llm_classifier_model_id", "small-m")
        assert resolve_model_for_tier("fast") == "small-m"
        assert resolve_model_for_tier("standard") == "agent-m"
        assert resolve_model_for_tier("deep") == "agent-m"


# ═════════════════════════════════════════════════════════════════════════════
# 3. Wire translation (Decision 6)
# ═════════════════════════════════════════════════════════════════════════════


class TestInvokeTranslation:
    @pytest.mark.asyncio
    async def test_system_first_and_anthropic_blocks_joined(self, openai_env, router):
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion("hi")))
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "part-A"}, {"type": "text", "text": "part-B"}]},
            {"role": "assistant", "content": "prev-answer"},
            {"role": "user", "content": "follow-up"},
        ]
        out = await _client().invoke(msgs, "THE-SYSTEM", temperature=0.2, max_tokens=321,
                                     match_user_language=False)

        body = _body(route)
        assert out == "hi"
        assert body["messages"][0] == {"role": "system", "content": "THE-SYSTEM"}
        roles = [m["role"] for m in body["messages"]]
        assert roles == ["system", "user", "assistant", "user"]
        first_user = body["messages"][1]["content"]
        assert "part-A" in first_user and "part-B" in first_user
        assert first_user.index("part-A") < first_user.index("part-B")
        assert body["messages"][3]["content"] == "follow-up"
        assert body["model"] == "agent-m" and body["max_tokens"] == 321 and body["temperature"] == 0.2
        assert "tools" not in body

    @pytest.mark.asyncio
    async def test_language_directive_reaches_system_message(self, openai_env, router):
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))
        await _client().invoke(USER, "BASE-SYS")
        system = _body(route)["messages"][0]["content"]
        assert system.startswith("BASE-SYS") and "SAME language" in system

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role,expected", [("classifier", "cls-m"), ("synthesis", "syn-m"), ("agent", "agent-m")])
    async def test_role_selects_model(self, openai_env, monkeypatch, router, role, expected):
        monkeypatch.setattr(settings, "llm_classifier_model_id", "cls-m")
        monkeypatch.setattr(settings, "llm_synthesis_model_id", "syn-m")
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))
        await _client().invoke(USER, "s", role=role)
        assert _body(route)["model"] == expected

    @pytest.mark.asyncio
    async def test_max_completion_tokens_field(self, openai_env, monkeypatch, router):
        monkeypatch.setattr(settings, "openai_max_tokens_field", "max_completion_tokens")
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))
        await _client().converse(CUSER, "s", max_tokens=777)
        body = _body(route)
        assert body["max_completion_tokens"] == 777
        assert "max_tokens" not in body

    @pytest.mark.asyncio
    @pytest.mark.parametrize("header", ["api-key", "x-api-key"])
    async def test_azure_style_header_sends_raw_key_and_no_bearer(self, openai_env, monkeypatch, router, header):
        monkeypatch.setattr(settings, "openai_api_key_header", header)
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))
        await _client().invoke(USER, "s")
        headers = route.calls[0].request.headers
        assert headers[header] == SECRET
        assert "authorization" not in headers

    @pytest.mark.asyncio
    async def test_use_cache_is_a_noop_no_cache_control_on_wire(self, openai_env, router):
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))
        await _client().invoke(USER, "s", use_cache=True)
        assert "cache_control" not in route.calls[0].request.content.decode()

    @pytest.mark.asyncio
    async def test_url_built_from_base_with_trailing_slash(self, openai_env, monkeypatch, router):
        monkeypatch.setattr(settings, "openai_base_url", BASE + "/")
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))
        await _client().invoke(USER, "s")
        assert route.call_count == 1


TOOL_CONFIG = {"tools": [
    {"toolSpec": {"name": "list_pods", "description": "List pods",
                  "inputSchema": {"json": {"type": "object", "properties": {"ns": {"type": "string"}}}}}},
    {"toolSpec": {"name": "get_pod", "description": "Get one pod",
                  "inputSchema": {"json": {"type": "object", "properties": {"name": {"type": "string"}}}}}},
]}


class TestConverseTranslation:
    @pytest.mark.asyncio
    async def test_tool_specs_become_functions(self, openai_env, router):
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))
        await _client().converse(CUSER, "sys", tool_config=TOOL_CONFIG)
        tools = _body(route)["tools"]
        assert [t["type"] for t in tools] == ["function", "function"]
        assert tools[0]["function"]["name"] == "list_pods"
        assert tools[0]["function"]["description"] == "List pods"
        assert tools[0]["function"]["parameters"] == TOOL_CONFIG["tools"][0]["toolSpec"]["inputSchema"]["json"]
        assert tools[1]["function"]["name"] == "get_pod"

    @pytest.mark.asyncio
    async def test_no_bedrock_guardrail_config_on_the_wire(self, openai_env, router):
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))
        await _client().converse(CUSER, "sys", tool_config=TOOL_CONFIG, apply_bedrock_guardrail=True)
        raw = route.calls[0].request.content.decode()
        assert "guardrail" not in raw.lower()
        assert "guardContent" not in raw

    @pytest.mark.asyncio
    async def test_multi_tool_history_ordering(self, openai_env, router):
        """assistant[text + 2 toolUse] → assistant.tool_calls (2, same order);
        user[2 toolResult + text] → 2 role=tool msgs (same order) BEFORE the user text."""
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion("final")))
        history = [
            {"role": "user", "content": [{"text": "q1"}]},
            {"role": "assistant", "content": [
                {"text": "let me look"},
                {"toolUse": {"toolUseId": "tu-1", "name": "list_pods", "input": {"ns": "a"}}},
                {"toolUse": {"toolUseId": "tu-2", "name": "get_pod", "input": {"name": "p"}}},
            ]},
            {"role": "user", "content": [
                {"text": "also consider this"},
                {"toolResult": {"toolUseId": "tu-1", "content": [{"text": "pod-a Running"}]}},
                {"toolResult": {"toolUseId": "tu-2", "status": "error", "content": [{"text": "boom"}]}},
            ]},
        ]
        out = await _client().converse(history, "sys", tool_config=TOOL_CONFIG)

        msgs = _body(route)["messages"]
        assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "tool", "user"]
        asst = msgs[2]
        assert "let me look" in (asst.get("content") or "")
        calls = asst["tool_calls"]
        assert [c["id"] for c in calls] == ["tu-1", "tu-2"]
        assert all(c["type"] == "function" for c in calls)
        assert [c["function"]["name"] for c in calls] == ["list_pods", "get_pod"]
        assert [json.loads(c["function"]["arguments"]) for c in calls] == [{"ns": "a"}, {"name": "p"}]
        assert msgs[3]["tool_call_id"] == "tu-1" and "pod-a Running" in msgs[3]["content"]
        assert msgs[4]["tool_call_id"] == "tu-2" and msgs[4]["content"].startswith("[tool error]")
        assert "boom" in msgs[4]["content"]
        assert msgs[5]["content"] == "also consider this"
        assert out["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_guard_content_block_text_is_forwarded(self, openai_env, router):
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))
        msgs = [{"role": "user", "content": [{"guardContent": {"text": {"text": "tagged-question"}}}]}]
        await _client().converse(msgs, "sys")
        user = _body(route)["messages"][1]
        assert user["role"] == "user" and "tagged-question" in user["content"]

    @pytest.mark.asyncio
    async def test_response_with_two_tool_calls(self, openai_env, router):
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion(
            text="calling", finish="tool_calls",
            tool_calls=[_call("c1", "list_pods", {"ns": "x"}), _call("c2", "get_pod", {"name": "y"})])))
        out = await _client().converse(CUSER, "sys", tool_config=TOOL_CONFIG)

        assert out["stop_reason"] == "tool_use"
        uses = [b for b in out["content"] if b["type"] == "tool_use"]
        assert [(u["toolUseId"], u["name"], u["input"]) for u in uses] == [
            ("c1", "list_pods", {"ns": "x"}), ("c2", "get_pod", {"name": "y"})]
        texts = [b["text"] for b in out["content"] if b["type"] == "text"]
        assert texts == ["calling"]

    @pytest.mark.asyncio
    async def test_null_content_with_tool_calls(self, openai_env, router):
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion(
            text=None, finish="tool_calls", tool_calls=[_call("c1", "list_pods", {})])))
        out = await _client().converse(CUSER, "sys", tool_config=TOOL_CONFIG)
        assert out["stop_reason"] == "tool_use"
        assert [b["type"] for b in out["content"]] == ["tool_use"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw", ['{"ns": ', "not json", "", '"just a string"'])
    async def test_malformed_args_become_empty_input_with_warning(self, openai_env, router, app_logs, raw):
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion(
            text=None, finish="tool_calls", tool_calls=[_call("c1", "list_pods", raw)])))
        out = await _client().converse(CUSER, "sys", tool_config=TOOL_CONFIG)
        use = next(b for b in out["content"] if b["type"] == "tool_use")
        assert use["input"] == {}
        assert out["stop_reason"] == "tool_use"
        if raw in ('{"ns": ', "not json"):  # invalid JSON → WARNING (Decision 6)
            assert '"level": "WARNING"' in app_logs.getvalue()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("finish,expected", [
        ("stop", "end_turn"), ("length", "max_tokens"),
        ("content_filter", "content_filtered"), ("tool_calls", "tool_use"),
    ])
    async def test_finish_reason_mapping(self, openai_env, router, finish, expected):
        tool_calls = [_call("c", "list_pods", {})] if finish == "tool_calls" else None
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion(
            text="x", finish=finish, tool_calls=tool_calls)))
        out = await _client().converse(CUSER, "sys", tool_config=TOOL_CONFIG)
        assert out["stop_reason"] == expected

    @pytest.mark.asyncio
    async def test_tool_calls_reported_with_stop_are_tool_use(self, openai_env, router):
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion(
            text=None, finish="stop", tool_calls=[_call("c", "list_pods", {})])))
        out = await _client().converse(CUSER, "sys", tool_config=TOOL_CONFIG)
        assert out["stop_reason"] == "tool_use"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [
        {"choices": []}, {"choices": None}, {}, {"error": "x"},
    ])
    async def test_empty_choices_is_malformed_and_not_retried(self, openai_env, router, payload):
        route = router.post(URL).mock(return_value=httpx.Response(200, json=payload))
        with pytest.raises(LLMProviderError) as ei:
            await _client().converse(CUSER, "sys")
        assert ei.value.error_class is ErrorClass.MALFORMED
        assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_non_json_200_is_malformed(self, openai_env, router):
        route = router.post(URL).mock(return_value=httpx.Response(200, text="<html>gateway</html>"))
        with pytest.raises(LLMProviderError) as ei:
            await _client().invoke(USER, "sys")
        assert ei.value.error_class is ErrorClass.MALFORMED
        assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_usage_mapping_in_converse_result(self, openai_env, router):
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion(usage={
            "prompt_tokens": 120, "completion_tokens": 30, "prompt_tokens_details": {"cached_tokens": 100}})))
        out = await _client().converse(CUSER, "sys")
        assert out["usage"] == {"input_tokens": 120, "output_tokens": 30}


# ═════════════════════════════════════════════════════════════════════════════
# 4. Resilience + accounting (Decisions 3, 9, 11)
# ═════════════════════════════════════════════════════════════════════════════


class TestRetry:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
    async def test_retryable_status_then_success(self, openai_env, router, status):
        route = router.post(URL).mock(side_effect=[
            httpx.Response(status), httpx.Response(200, json=_completion("recovered"))])
        assert await _client().invoke(USER, "s") == "recovered"
        assert route.call_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", [httpx.ReadTimeout("t"), httpx.ConnectError("c"), httpx.RemoteProtocolError("r")])
    async def test_transport_errors_retried(self, openai_env, router, exc):
        route = router.post(URL).mock(side_effect=[exc, httpx.Response(200, json=_completion("ok"))])
        assert await _client().invoke(USER, "s") == "ok"
        assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_bounded_attempts_reraise_original_with_jittered_backoff(self, openai_env, router):
        sleeps = openai_env
        route = router.post(URL).mock(return_value=httpx.Response(503))
        with pytest.raises(LLMProviderError) as ei:
            await _client().invoke(USER, "s")
        assert route.call_count == 3
        assert ei.value.status_code == 503
        assert ei.value.error_class is ErrorClass.SERVER_ERROR
        assert len(sleeps) == 2
        assert 1.0 <= sleeps[0] < 2.0 and 2.0 <= sleeps[1] < 3.0  # 1s·2^n + U(0,1)

    @pytest.mark.asyncio
    async def test_converse_has_same_retry_budget(self, openai_env, router):
        route = router.post(URL).mock(return_value=httpx.Response(429))
        with pytest.raises(LLMProviderError):
            await _client().converse(CUSER, "s", tool_config=TOOL_CONFIG)
        assert route.call_count == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422])
    async def test_other_4xx_not_retried(self, openai_env, router, status):
        sleeps = openai_env
        route = router.post(URL).mock(return_value=httpx.Response(status, json={"error": {"type": "bad"}}))
        with pytest.raises(LLMProviderError) as ei:
            await _client().invoke(USER, "s")
        assert route.call_count == 1
        assert sleeps == []
        assert ei.value.error_class is ErrorClass.CLIENT_ERROR
        assert ei.value.code == "bad"

    @pytest.mark.asyncio
    async def test_provider_error_metric_labels(self, openai_env, router, monkeypatch):
        import src.core.llm.service as svc
        counter = MagicMock()
        monkeypatch.setattr(svc, "llm_provider_errors", counter)
        router.post(URL).mock(side_effect=[httpx.Response(429), httpx.ReadTimeout("t"),
                                           httpx.Response(200, json=_completion())])
        await _client().invoke(USER, "s")
        labels = [c.args[1] for c in counter.add.call_args_list]
        assert labels == [{"provider": "openai", "error_class": "throttled"},
                          {"provider": "openai", "error_class": "timeout"}]


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_breaker_opens_and_stops_hitting_the_endpoint(self, openai_env, router):
        route = router.post(URL).mock(return_value=httpx.Response(500))
        c = _client()
        failures = 0
        for _ in range(10):
            try:
                await c.invoke(USER, "s")
            except LLMProviderError:
                failures += 1
            except Exception as e:
                assert "circuit breaker" in str(e).lower()
                break
        else:
            pytest.fail("circuit breaker never opened for the openai provider")
        assert 1 <= failures <= 5
        calls_before = route.call_count
        with pytest.raises(Exception, match="(?i)circuit breaker"):
            await c.converse(CUSER, "s")
        assert route.call_count == calls_before, "open breaker must short-circuit converse too"

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self, openai_env, router):
        route = router.post(URL).mock(side_effect=(
            [httpx.Response(400)] * 4 + [httpx.Response(200, json=_completion())] + [httpx.Response(400)] * 4
            + [httpx.Response(200, json=_completion("still-closed"))]))
        c = _client()
        results = []
        for _ in range(10):
            try:
                results.append(await c.invoke(USER, "s"))
            except LLMProviderError:
                results.append("err")
        assert results[-1] == "still-closed"
        assert route.call_count == 10


class TestAccounting:
    @pytest.mark.asyncio
    async def test_metric_labels_and_budget(self, openai_env, router, monkeypatch):
        import src.core.llm.service as svc
        tokens, cost, budget = MagicMock(), MagicMock(), MagicMock()
        monkeypatch.setattr(svc, "token_counter", tokens)
        monkeypatch.setattr(svc, "estimated_cost", cost)
        monkeypatch.setattr(svc, "budget_tracker", budget)
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion(
            usage={"prompt_tokens": 50, "completion_tokens": 9})))

        await _client().invoke(USER, "s", agent_id="finops", session_id="child", budget_session_id="parent")

        token_calls = {(c.args[0], tuple(sorted(c.args[1].items()))) for c in tokens.add.call_args_list}
        assert token_calls == {
            (50, (("agent_id", "finops"), ("direction", "input"), ("model", "agent-m"))),
            (9, (("agent_id", "finops"), ("direction", "output"), ("model", "agent-m"))),
        }
        assert cost.add.call_args.args[1] == {"model": "agent-m", "agent_id": "finops"}
        budget.record_usage.assert_called_once_with("parent", 50, 9)

    @pytest.mark.asyncio
    async def test_converse_budget_defaults_to_session(self, openai_env, router, monkeypatch):
        import src.core.llm.service as svc
        budget = MagicMock()
        monkeypatch.setattr(svc, "budget_tracker", budget)
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion(
            usage={"prompt_tokens": 7, "completion_tokens": 3})))
        await _client().converse(CUSER, "s", agent_id="aws", session_id="sess")
        budget.record_usage.assert_called_once_with("sess", 7, 3)

    @pytest.mark.asyncio
    async def test_model_override_is_the_cost_label(self, openai_env, router, monkeypatch):
        import src.core.llm.service as svc
        cost = MagicMock()
        monkeypatch.setattr(svc, "estimated_cost", cost)
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion()))
        await _client().converse(CUSER, "s", agent_id="aws", model_id_override="tier-x")
        assert _body(route)["model"] == "tier-x"
        assert cost.add.call_args.args[1] == {"model": "tier-x", "agent_id": "aws"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["invoke", "converse"])
    async def test_configured_pricing_cached_billed_at_input_rate(self, openai_env, router, monkeypatch, method):
        """1000 prompt (400 cached, included) × $2/1M + 100 completion × $10/1M = $0.003."""
        import src.core.llm.service as svc
        cost = MagicMock()
        monkeypatch.setattr(svc, "estimated_cost", cost)
        monkeypatch.setattr(settings, "llm_price_input_per_1m", 2.0)
        monkeypatch.setattr(settings, "llm_price_output_per_1m", 10.0)
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion(usage={
            "prompt_tokens": 1000, "completion_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 400}})))
        c = _client()
        if method == "invoke":
            await c.invoke(USER, "s", agent_id="aws")
        else:
            await c.converse(CUSER, "s", agent_id="aws")
        assert cost.add.call_args.args[0] == pytest.approx(0.003)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("price_in,price_out", [(None, None), (2.0, None), (None, 10.0)])
    async def test_absent_pricing_is_zero_cost(self, openai_env, router, monkeypatch, price_in, price_out):
        import src.core.llm.service as svc
        cost = MagicMock()
        monkeypatch.setattr(svc, "estimated_cost", cost)
        monkeypatch.setattr(settings, "llm_price_input_per_1m", price_in)
        monkeypatch.setattr(settings, "llm_price_output_per_1m", price_out)
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion(
            usage={"prompt_tokens": 10_000, "completion_tokens": 10_000})))
        await _client().invoke(USER, "s", agent_id="aws")
        assert cost.add.call_args.args[0] == 0.0

    @pytest.mark.parametrize("price_in,price_out,warns", [
        (None, None, True), (2.0, None, True), (None, 10.0, True), (2.0, 10.0, False)])
    def test_pricing_warning_at_startup(self, openai_env, monkeypatch, price_in, price_out, warns):
        import src.core.llm.factory as factory
        from src.core.model_tier import validate_tier_models_at_startup
        log = MagicMock()
        monkeypatch.setattr(factory, "logger", log)
        monkeypatch.setattr(settings, "llm_price_input_per_1m", price_in)
        monkeypatch.setattr(settings, "llm_price_output_per_1m", price_out)
        validate_tier_models_at_startup()
        text = " ".join(str(c.args[0]) for c in log.warning.call_args_list)
        assert ("llm_pricing_unset" in text) is warns

    def test_bedrock_pricing_ignores_llm_price_vars(self, monkeypatch):
        from src.core.model_tier import compute_cost
        monkeypatch.setattr(settings, "llm_provider", "bedrock")
        baseline = compute_cost(settings.bedrock_agent_model_id, 1000, 1000)
        monkeypatch.setattr(settings, "llm_price_input_per_1m", 999.0)
        monkeypatch.setattr(settings, "llm_price_output_per_1m", 999.0)
        assert compute_cost(settings.bedrock_agent_model_id, 1000, 1000) == baseline
        assert baseline > 0


# ═════════════════════════════════════════════════════════════════════════════
# 5. Guardrail L1 inside the service under openai (Decision 10)
# ═════════════════════════════════════════════════════════════════════════════


class TestServiceGuardrailByProvider:
    @pytest.mark.asyncio
    async def test_enabled_unprovisioned_guardrail_is_noop_under_openai(self, openai_env, router, monkeypatch):
        import src.core.guardrail as gmod
        import src.core.llm.service as svc
        boto = MagicMock()
        guard = gmod.GuardrailClient(client=boto)
        monkeypatch.setattr(svc, "guardrail", guard)
        router.post(URL).mock(return_value=httpx.Response(200, json=_completion("answer")))

        assert await _client().invoke(USER, "s") == "answer"
        out = await _client().converse(CUSER, "s")
        assert out["content"][0]["text"] == "answer"
        assert guard.enabled is False
        boto.apply_guardrail.assert_not_called()

    def test_same_settings_under_bedrock_are_fail_closed(self, monkeypatch):
        import src.core.guardrail as gmod
        monkeypatch.setattr(settings, "llm_provider", "bedrock")
        monkeypatch.setattr(settings, "guardrail_enabled", True)
        monkeypatch.setattr(settings, "guardrail_id", None)
        guard = gmod.GuardrailClient(client=MagicMock())
        assert guard.enabled is True
        with pytest.raises(gmod.GuardrailBlockedError):
            guard.apply("anything", source="INPUT")


# ═════════════════════════════════════════════════════════════════════════════
# 6. Agentic loop (spec 37) end-to-end with the openai provider
# ═════════════════════════════════════════════════════════════════════════════


def _mcp_adapter(tools=("list_pods",)):
    from src.core.adapters import McpAdapter
    adapter = McpAdapter(name="k8s-mcp", url="http://k8s-mcp:8080", tools=list(tools))
    adapter.list_tool_specs = AsyncMock(return_value=[
        {"toolSpec": {"name": t, "description": f"{t} tool",
                      "inputSchema": {"json": {"type": "object", "properties": {}}}}} for t in tools])
    adapter.call_tool = AsyncMock(return_value="pod-a Running\npod-b CrashLoopBackOff")
    return adapter


def _loop_kwargs(adapters, **kw):
    base = dict(query="which pods are failing?", system_prompt="You are k8s.",
                history_text="No previous conversation", mcp_adapters=adapters,
                agent_id="kubernetes", user_id="u", session_id="s")
    base.update(kw)
    return base


@pytest.fixture
def loop_llm(openai_env, monkeypatch):
    import src.core.agentic_loop as loop_mod
    c = _client()
    monkeypatch.setattr(loop_mod, "bedrock", c)
    return c


class TestAgenticLoopOpenAI:
    @pytest.mark.asyncio
    async def test_allowlist_refuses_non_allowlisted_tool_call(self, loop_llm, router):
        from src.core.agentic_loop import run_agentic_loop
        adapter = _mcp_adapter(("list_pods",))
        route = router.post(URL).mock(side_effect=[
            httpx.Response(200, json=_completion(text=None, finish="tool_calls", tool_calls=[
                _call("c-ok", "list_pods", {}), _call("c-bad", "delete_pod", {"name": "pod-b"})])),
            httpx.Response(200, json=_completion("pod-b is in CrashLoopBackOff.")),
        ])

        text, _ = await run_agentic_loop(**_loop_kwargs([adapter]))

        assert "CrashLoopBackOff" in text
        adapter.call_tool.assert_awaited_once()
        assert adapter.call_tool.await_args.args[0] == "list_pods"
        # Only the allowlisted tool was advertised to the model.
        assert [t["function"]["name"] for t in _body(route, 0)["tools"]] == ["list_pods"]
        # Round-trip: the second request carries both results, correlated by id, in order.
        msgs = _body(route, 1)["messages"]
        asst = next(m for m in msgs if m["role"] == "assistant")
        assert [c["id"] for c in asst["tool_calls"]] == ["c-ok", "c-bad"]
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["c-ok", "c-bad"]
        assert "pod-a Running" in tool_msgs[0]["content"]
        assert "pod-a" not in tool_msgs[1]["content"]
        assert "not found" in tool_msgs[1]["content"] or "error" in tool_msgs[1]["content"].lower()
        assert msgs.index(asst) < msgs.index(tool_msgs[0])

    @pytest.mark.asyncio
    async def test_max_tool_steps_bounds_http_calls(self, loop_llm, router, monkeypatch):
        import src.core.agentic_loop as loop_mod
        monkeypatch.setattr(loop_mod, "MAX_TOOL_STEPS", 3)
        adapter = _mcp_adapter()
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion(
            text=None, finish="tool_calls", tool_calls=[_call("c", "list_pods", {})])))

        text, _ = await loop_mod.run_agentic_loop(**_loop_kwargs([adapter]))

        assert route.call_count == 3
        assert adapter.call_tool.await_count == 3
        assert "budget" in text.lower() or "incomplete" in text.lower()

    @pytest.mark.asyncio
    async def test_token_budget_uses_openai_usage(self, loop_llm, router, monkeypatch):
        import src.core.agentic_loop as loop_mod
        monkeypatch.setattr(loop_mod, "MAX_LOOP_TOKENS", 150)
        adapter = _mcp_adapter()
        route = router.post(URL).mock(return_value=httpx.Response(200, json=_completion(
            text=None, finish="tool_calls", tool_calls=[_call("c", "list_pods", {})],
            usage={"prompt_tokens": 70, "completion_tokens": 10})))

        text, _ = await loop_mod.run_agentic_loop(**_loop_kwargs([adapter]))

        assert route.call_count == 2  # 80 + 80 = 160 ≥ 150 → stop before the 3rd
        assert "incomplete" in text.lower() or "budget" in text.lower()

    @pytest.mark.asyncio
    async def test_tool_args_guardrail_inactive_under_openai(self, loop_llm, router, monkeypatch):
        """With L1 off, B3 tool-arg checks are no-ops (they'd fail-closed under bedrock)."""
        import src.core.agentic_loop as loop_mod
        import src.core.guardrail as gmod
        guard = gmod.GuardrailClient(client=MagicMock())
        monkeypatch.setattr(loop_mod, "guardrail", guard)
        adapter = _mcp_adapter()
        router.post(URL).mock(side_effect=[
            httpx.Response(200, json=_completion(text=None, finish="tool_calls",
                                                 tool_calls=[_call("c", "list_pods", {})])),
            httpx.Response(200, json=_completion("done")),
        ])
        text, _ = await loop_mod.run_agentic_loop(**_loop_kwargs([adapter]))
        assert text == "done"
        adapter.call_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provider_error_mid_loop_propagates(self, loop_llm, router):
        """A non-retryable provider failure is not swallowed by the loop."""
        from src.core.agentic_loop import run_agentic_loop
        router.post(URL).mock(return_value=httpx.Response(400, json={"error": {"code": "context_length_exceeded"}}))
        with pytest.raises(LLMProviderError) as ei:
            await run_agentic_loop(**_loop_kwargs([_mcp_adapter()]))
        assert ei.value.code == "context_length_exceeded"


class TestStreamingAgenticLoopOpenAI:
    @pytest.mark.asyncio
    async def test_streaming_loop_allowlist_and_final_text(self, openai_env, router, monkeypatch):
        import src.core.agentic_loop_streaming as stream_mod
        monkeypatch.setattr(stream_mod, "bedrock", _client())
        adapter = _mcp_adapter(("list_pods",))
        route = router.post(URL).mock(side_effect=[
            httpx.Response(200, json=_completion(text=None, finish="tool_calls", tool_calls=[
                _call("c-ok", "list_pods", {}), _call("c-bad", "exec_in_pod", {"cmd": "rm -rf /"})])),
            httpx.Response(200, json=_completion("pod-b is crashing.")),
        ])

        events = [e async for e in stream_mod.run_agentic_loop_streaming(**_loop_kwargs([adapter]))]

        adapter.call_tool.assert_awaited_once()
        assert adapter.call_tool.await_args.args[0] == "list_pods"
        final = "".join(getattr(e, "text", "") for e in events if type(e).__name__ == "StepFinalChunk")
        assert "pod-b is crashing." in final
        assert type(events[-1]).__name__ == "StepDone"
        tool_msgs = [m for m in _body(route, 1)["messages"] if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["c-ok", "c-bad"]
        assert "not found" in tool_msgs[1]["content"]
