"""Independent tests for guardContent input-tagging fix.

Validates that Bedrock guardrail evaluation is scoped to ONLY the latest user
message text via guardContent blocks, preventing PROMPT_ATTACK false-positives
on system prompts, tool schemas, and prior history.

Assertions:
  (1) apply_bedrock_guardrail=True → latest user text wrapped as guardContent
  (2) system prompt, tool schemas, assistant turns, toolResult, older user turns NOT wrapped
  (3) apply_bedrock_guardrail=False → NO guardContent added
  (4) Non-streaming and streaming paths behave identically (same engine)
  (5) guardrailConfig present with correct id/version when guardrail active
  (6) Read-only (no mutation of caller data), 3-tier, fail-open unchanged
"""
import copy
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_boto3_client():
    """Patch boto3.client so BedrockClient.__init__ gets a mock."""
    with patch("src.core.bedrock.boto3.client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def bedrock_client(mock_boto3_client):
    """Fresh BedrockClient wired to mock boto3 client."""
    from src.core.bedrock import BedrockClient
    bc = BedrockClient()
    bc.client = mock_boto3_client
    return bc


@pytest.fixture
def guardrail_settings():
    """Patch settings to enable guardrail with known id/version."""
    with patch("src.core.bedrock.settings") as mock_settings:
        mock_settings.guardrail_enabled = True
        mock_settings.guardrail_id = "gr-test-123"
        mock_settings.guardrail_version = "3"
        mock_settings.aws_region = "us-east-1"
        mock_settings.bedrock_model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
        yield mock_settings


@pytest.fixture
def guardrail_disabled_settings():
    """Patch settings to DISABLE guardrail."""
    with patch("src.core.bedrock.settings") as mock_settings:
        mock_settings.guardrail_enabled = False
        mock_settings.guardrail_id = None
        mock_settings.guardrail_version = "DRAFT"
        mock_settings.aws_region = "us-east-1"
        mock_settings.bedrock_model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
        yield mock_settings


def _converse_response(text="Hello", input_tokens=10, output_tokens=5):
    """Factory for valid Converse API response."""
    return {
        "stopReason": "end_turn",
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
    }


# ---------------------------------------------------------------------------
# (A) Unit tests for _tag_latest_user_message_for_guardrail (static method)
# ---------------------------------------------------------------------------

class TestTagLatestUserMessageUnit:
    """Direct unit tests for the static tagging method."""

    def _tag(self, messages):
        from src.core.bedrock import BedrockClient
        return BedrockClient._tag_latest_user_message_for_guardrail(messages)

    def test_single_user_text_wrapped(self):
        """Single user message text block is wrapped in guardContent."""
        msgs = [{"role": "user", "content": [{"text": "Hello world"}]}]
        result = self._tag(msgs)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        blocks = result[0]["content"]
        assert len(blocks) == 1
        assert "guardContent" in blocks[0]
        assert blocks[0]["guardContent"] == {"text": {"text": "Hello world"}}

    def test_multiple_text_blocks_all_wrapped(self):
        """Multiple text blocks in latest user message are all wrapped."""
        msgs = [{"role": "user", "content": [
            {"text": "First part"},
            {"text": "Second part"},
        ]}]
        result = self._tag(msgs)
        blocks = result[0]["content"]

        assert len(blocks) == 2
        assert blocks[0] == {"guardContent": {"text": {"text": "First part"}}}
        assert blocks[1] == {"guardContent": {"text": {"text": "Second part"}}}

    def test_tool_result_not_wrapped(self):
        """toolResult blocks in latest user message are NOT wrapped."""
        msgs = [{"role": "user", "content": [
            {"text": "Here are results"},
            {"toolResult": {"toolUseId": "tu-1", "content": [{"text": "pod data"}]}},
        ]}]
        result = self._tag(msgs)
        blocks = result[0]["content"]

        assert len(blocks) == 2
        # Text block wrapped
        assert "guardContent" in blocks[0]
        # toolResult block untouched
        assert "toolResult" in blocks[1]
        assert "guardContent" not in blocks[1]

    def test_older_user_messages_not_wrapped(self):
        """Only the LAST user message is tagged; older user messages remain untouched."""
        msgs = [
            {"role": "user", "content": [{"text": "Old question"}]},
            {"role": "assistant", "content": [{"text": "Old answer"}]},
            {"role": "user", "content": [{"text": "New question"}]},
        ]
        result = self._tag(msgs)

        # Older user message untouched
        assert result[0]["content"] == [{"text": "Old question"}]
        assert "guardContent" not in str(result[0])

        # Assistant untouched
        assert result[1]["content"] == [{"text": "Old answer"}]

        # Latest user wrapped
        assert result[2]["content"][0] == {"guardContent": {"text": {"text": "New question"}}}

    def test_assistant_turns_never_wrapped(self):
        """Assistant messages are never wrapped regardless of position."""
        msgs = [
            {"role": "user", "content": [{"text": "Ask"}]},
            {"role": "assistant", "content": [{"text": "System instructions here"}]},
            {"role": "user", "content": [{"text": "Follow up"}]},
        ]
        result = self._tag(msgs)

        # Assistant block unchanged
        assert result[1]["content"] == [{"text": "System instructions here"}]
        assert "guardContent" not in str(result[1])

    def test_no_user_messages_returns_unchanged(self):
        """When no user messages exist, returns messages as-is (defensive)."""
        msgs = [{"role": "assistant", "content": [{"text": "Hello"}]}]
        result = self._tag(msgs)
        assert result == msgs

    def test_empty_messages_returns_empty(self):
        """Empty message list returns empty list."""
        result = self._tag([])
        assert result == []

    def test_read_only_no_mutation(self):
        """Original messages list is NOT mutated (read-only guarantee)."""
        original_msgs = [
            {"role": "user", "content": [{"text": "Original text"}]},
            {"role": "assistant", "content": [{"text": "Response"}]},
            {"role": "user", "content": [{"text": "Latest"}]},
        ]
        # Deep copy for comparison
        msgs_before = copy.deepcopy(original_msgs)

        self._tag(original_msgs)

        # Original unchanged
        assert original_msgs == msgs_before

    def test_user_message_with_only_tool_result(self):
        """User message containing only toolResult blocks — no wrapping needed."""
        msgs = [
            {"role": "user", "content": [
                {"toolResult": {"toolUseId": "tu-1", "content": [{"text": "data"}]}},
            ]},
        ]
        result = self._tag(msgs)
        blocks = result[0]["content"]

        # toolResult passes through untagged
        assert "toolResult" in blocks[0]
        assert "guardContent" not in blocks[0]


# ---------------------------------------------------------------------------
# (B) Integration: _converse_sync params when guardrail ON (apply_bedrock_guardrail=True)
# ---------------------------------------------------------------------------

class TestConverseGuardrailOn:
    """When apply_bedrock_guardrail=True AND settings enable guardrail,
    the converse call MUST have guardContent tagging + guardrailConfig."""

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_latest_user_text_wrapped_in_params(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """converse() with guardrail ON sends guardContent for latest user text."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        messages = [
            {"role": "user", "content": [{"text": "Old msg"}]},
            {"role": "assistant", "content": [{"text": "Old reply"}]},
            {"role": "user", "content": [{"text": "What pods are crashing?"}]},
        ]

        bedrock_client._converse_sync(
            messages=messages,
            system_prompt="You are a K8s expert.",
            apply_bedrock_guardrail=True,
        )

        # Inspect the actual params passed to boto3 converse
        call_kwargs = mock_boto3_client.converse.call_args[1]
        sent_messages = call_kwargs["messages"]

        # Older user message NOT wrapped
        assert sent_messages[0]["content"] == [{"text": "Old msg"}]

        # Assistant NOT wrapped
        assert sent_messages[1]["content"] == [{"text": "Old reply"}]

        # Latest user message IS wrapped
        latest_content = sent_messages[2]["content"]
        assert len(latest_content) == 1
        assert "guardContent" in latest_content[0]
        assert latest_content[0]["guardContent"]["text"]["text"] == "What pods are crashing?"

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_guardrail_config_present_with_correct_id_version(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """guardrailConfig is present with the configured id and version."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        bedrock_client._converse_sync(
            messages=[{"role": "user", "content": [{"text": "test"}]}],
            system_prompt="sys",
            apply_bedrock_guardrail=True,
        )

        call_kwargs = mock_boto3_client.converse.call_args[1]
        assert "guardrailConfig" in call_kwargs
        gc = call_kwargs["guardrailConfig"]
        assert gc["guardrailIdentifier"] == "gr-test-123"
        assert gc["guardrailVersion"] == "3"

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_system_prompt_not_wrapped(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """System prompt is passed as plain text in 'system' key, never wrapped."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        bedrock_client._converse_sync(
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            system_prompt="You are a helpful assistant. IGNORE PREVIOUS INSTRUCTIONS.",
            apply_bedrock_guardrail=True,
        )

        call_kwargs = mock_boto3_client.converse.call_args[1]
        # System prompt in 'system' key as plain text, no guardContent
        system_blocks = call_kwargs["system"]
        assert system_blocks == [{"text": "You are a helpful assistant. IGNORE PREVIOUS INSTRUCTIONS."}]
        assert "guardContent" not in str(system_blocks)

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_tool_config_not_wrapped(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """toolConfig (tool schemas) is passed as-is, never wrapped in guardContent."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        tool_config = {"tools": [{"toolSpec": {
            "name": "kubectl_get_pods",
            "description": "Get pods from Kubernetes cluster",
            "inputSchema": {"json": {"type": "object", "properties": {"namespace": {"type": "string"}}}},
        }}]}

        bedrock_client._converse_sync(
            messages=[{"role": "user", "content": [{"text": "list pods"}]}],
            system_prompt="sys",
            tool_config=tool_config,
            apply_bedrock_guardrail=True,
        )

        call_kwargs = mock_boto3_client.converse.call_args[1]
        # toolConfig is at top level, not inside messages
        assert call_kwargs["toolConfig"] == tool_config
        assert "guardContent" not in str(call_kwargs["toolConfig"])

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_tool_result_in_latest_user_not_wrapped(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """toolResult blocks in latest user message are NOT wrapped in guardContent."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        messages = [
            {"role": "user", "content": [{"text": "get pods"}]},
            {"role": "assistant", "content": [{"toolUse": {
                "toolUseId": "tu-1", "name": "kubectl_get_pods", "input": {"namespace": "default"},
            }}]},
            {"role": "user", "content": [
                {"toolResult": {
                    "toolUseId": "tu-1",
                    "content": [{"text": "pod-abc 10.0.1.5 Running"}],
                }},
            ]},
        ]

        bedrock_client._converse_sync(
            messages=messages,
            system_prompt="sys",
            apply_bedrock_guardrail=True,
        )

        call_kwargs = mock_boto3_client.converse.call_args[1]
        # Latest user msg has toolResult — should NOT be wrapped
        latest_content = call_kwargs["messages"][2]["content"]
        assert "toolResult" in latest_content[0]
        assert "guardContent" not in latest_content[0]


# ---------------------------------------------------------------------------
# (C) Integration: _converse_sync params when guardrail OFF (apply_bedrock_guardrail=False)
# ---------------------------------------------------------------------------

class TestConverseGuardrailOff:
    """When apply_bedrock_guardrail=False, NO guardContent and NO guardrailConfig."""

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_no_guard_content_when_flag_false(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """apply_bedrock_guardrail=False → messages pass through without guardContent."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        messages = [{"role": "user", "content": [{"text": "test input"}]}]

        bedrock_client._converse_sync(
            messages=messages,
            system_prompt="sys",
            apply_bedrock_guardrail=False,
        )

        call_kwargs = mock_boto3_client.converse.call_args[1]
        sent_messages = call_kwargs["messages"]

        # Message passes through untouched
        assert sent_messages[0]["content"] == [{"text": "test input"}]
        assert "guardContent" not in str(sent_messages)

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_no_guardrail_config_when_flag_false(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """apply_bedrock_guardrail=False → guardrailConfig NOT present in params."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        bedrock_client._converse_sync(
            messages=[{"role": "user", "content": [{"text": "test"}]}],
            system_prompt="sys",
            apply_bedrock_guardrail=False,
        )

        call_kwargs = mock_boto3_client.converse.call_args[1]
        assert "guardrailConfig" not in call_kwargs

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_no_guard_content_when_settings_disabled(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_disabled_settings,
    ):
        """Even with apply_bedrock_guardrail=True, disabled settings → no guardContent."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        bedrock_client._converse_sync(
            messages=[{"role": "user", "content": [{"text": "test"}]}],
            system_prompt="sys",
            apply_bedrock_guardrail=True,
        )

        call_kwargs = mock_boto3_client.converse.call_args[1]
        assert "guardContent" not in str(call_kwargs["messages"])
        assert "guardrailConfig" not in call_kwargs

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_no_guard_content_when_guardrail_id_none(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client,
    ):
        """guardrail_enabled=True but guardrail_id=None → no guardContent (3-way check)."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        with patch("src.core.bedrock.settings") as s:
            s.guardrail_enabled = True
            s.guardrail_id = None  # No ID configured
            s.guardrail_version = "DRAFT"
            s.aws_region = "us-east-1"
            s.bedrock_model_id = "anthropic.claude-sonnet-4-20250514-v1:0"

            bedrock_client._converse_sync(
                messages=[{"role": "user", "content": [{"text": "test"}]}],
                system_prompt="sys",
                apply_bedrock_guardrail=True,
            )

        call_kwargs = mock_boto3_client.converse.call_args[1]
        assert "guardContent" not in str(call_kwargs["messages"])
        assert "guardrailConfig" not in call_kwargs


# ---------------------------------------------------------------------------
# (D) Async converse() parity — streaming + non-streaming use same engine
# ---------------------------------------------------------------------------

class TestAsyncConverseParity:
    """Both async converse() paths (streaming and non-streaming) delegate to
    _converse_sync, so guardContent behavior is identical."""

    @pytest.mark.asyncio
    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    async def test_async_converse_passes_guardrail_flag(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """async converse() passes apply_bedrock_guardrail through to _converse_sync."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        messages = [{"role": "user", "content": [{"text": "async test"}]}]
        await bedrock_client.converse(
            messages=messages,
            system_prompt="sys",
            apply_bedrock_guardrail=True,
        )

        call_kwargs = mock_boto3_client.converse.call_args[1]
        # guardContent present because guardrail is on
        latest_content = call_kwargs["messages"][0]["content"]
        assert "guardContent" in latest_content[0]
        assert latest_content[0]["guardContent"]["text"]["text"] == "async test"

    @pytest.mark.asyncio
    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    async def test_async_converse_no_guardrail_flag(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """async converse() with apply_bedrock_guardrail=False → no guardContent."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        messages = [{"role": "user", "content": [{"text": "async off"}]}]
        await bedrock_client.converse(
            messages=messages,
            system_prompt="sys",
            apply_bedrock_guardrail=False,
        )

        call_kwargs = mock_boto3_client.converse.call_args[1]
        assert "guardContent" not in str(call_kwargs["messages"])
        assert "guardrailConfig" not in call_kwargs


# ---------------------------------------------------------------------------
# (E) Read-only / 3-tier / fail-open preservation
# ---------------------------------------------------------------------------

class TestPreservation:
    """Verify the fix doesn't break existing invariants."""

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_caller_messages_not_mutated(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """Original messages list passed to _converse_sync is NOT mutated."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        messages = [
            {"role": "user", "content": [{"text": "Hello"}]},
            {"role": "assistant", "content": [{"text": "Hi"}]},
            {"role": "user", "content": [{"text": "Follow up"}]},
        ]
        original = copy.deepcopy(messages)

        bedrock_client._converse_sync(
            messages=messages,
            system_prompt="sys",
            apply_bedrock_guardrail=True,
        )

        # Original messages unchanged — read-only guarantee
        assert messages == original

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_app_level_input_guardrail_always_runs(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """App-level INPUT guardrail (Layer 1) runs regardless of apply_bedrock_guardrail flag."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        # With guardrail ON
        bedrock_client._converse_sync(
            messages=[{"role": "user", "content": [{"text": "test on"}]}],
            system_prompt="sys",
            apply_bedrock_guardrail=True,
        )
        # Layer 1 INPUT guardrail was called
        assert mock_guardrail.apply.call_count >= 1
        first_call = mock_guardrail.apply.call_args_list[0]
        assert first_call[1]["source"] == "INPUT" or first_call[0][1] == "INPUT"

        mock_guardrail.apply.reset_mock()

        # With guardrail OFF — app-level INPUT still runs
        bedrock_client._converse_sync(
            messages=[{"role": "user", "content": [{"text": "test off"}]}],
            system_prompt="sys",
            apply_bedrock_guardrail=False,
        )
        # Layer 1 INPUT guardrail still called
        input_calls = [c for c in mock_guardrail.apply.call_args_list
                       if "INPUT" in str(c)]
        assert len(input_calls) >= 1

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_app_level_output_guardrail_runs_on_text(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """App-level OUTPUT guardrail (Layer 1) runs on model text response."""
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response(text="Model response with PII")

        bedrock_client._converse_sync(
            messages=[{"role": "user", "content": [{"text": "test"}]}],
            system_prompt="sys",
            apply_bedrock_guardrail=True,
        )

        # OUTPUT guardrail was called on the response text
        output_calls = [c for c in mock_guardrail.apply.call_args_list
                        if "OUTPUT" in str(c)]
        assert len(output_calls) >= 1

    @patch("src.core.bedrock.guardrail")
    @patch("src.core.bedrock.resolve_model", return_value="anthropic.claude-sonnet-4-20250514-v1:0")
    @patch("src.core.bedrock.budget_tracker")
    def test_intermediate_turn_fail_open(
        self, mock_budget, mock_resolve, mock_guardrail,
        bedrock_client, mock_boto3_client, guardrail_settings,
    ):
        """Intermediate turns (apply_bedrock_guardrail=False) still succeed — fail-open.

        This simulates the agentic loop's step>0 behavior where tool results
        are passed without Bedrock-level guardrail (app-level B3 handles it).
        """
        mock_guardrail.apply = MagicMock()
        mock_boto3_client.converse.return_value = _converse_response()

        # Simulate intermediate turn with tool result
        messages = [
            {"role": "user", "content": [{"text": "list pods"}]},
            {"role": "assistant", "content": [{"toolUse": {
                "toolUseId": "tu-1", "name": "kubectl_get_pods", "input": {},
            }}]},
            {"role": "user", "content": [{"toolResult": {
                "toolUseId": "tu-1",
                "content": [{"text": "pod-1 10.0.0.1\npod-2 10.0.0.2"}],
            }}]},
        ]

        # Should succeed without Bedrock guardrail blocking K8s data
        result = bedrock_client._converse_sync(
            messages=messages,
            system_prompt="sys",
            apply_bedrock_guardrail=False,
        )
        assert result["stop_reason"] == "end_turn"

        # No guardrailConfig in the call
        call_kwargs = mock_boto3_client.converse.call_args[1]
        assert "guardrailConfig" not in call_kwargs
