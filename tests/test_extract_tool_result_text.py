"""Tests for _extract_tool_result_text (spec 41 M1).

Written against the CONTRACT (docstring + real agentic_loop.py message shape),
NOT by mirroring the implementation. The function extracts toolResult text from
the messages array that run_agentic_loop() builds — Converse protocol shape:

    {"role": "user", "content": [{"toolResult": {"toolUseId": ..., "content": [{"text": ...}]}}]}

Error results add "status": "error" but the text extraction should still work.
"""


from src.core.generic_agent import _extract_tool_result_text


class TestHappyPathMultiBlock:
    """Multiple tool results across multiple user messages."""

    def test_single_tool_result_single_text(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "tool_1",
                            "content": [{"text": "pod Running"}],
                        }
                    }
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == "pod Running"

    def test_multiple_text_blocks_in_single_tool_result(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "tool_1",
                            "content": [
                                {"text": "line 1"},
                                {"text": "line 2"},
                            ],
                        }
                    }
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == "line 1\nline 2"

    def test_multiple_tool_results_in_single_message(self):
        """Converse protocol: multiple tool results go in one user message."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "tool_1",
                            "content": [{"text": "result A"}],
                        }
                    },
                    {
                        "toolResult": {
                            "toolUseId": "tool_2",
                            "content": [{"text": "result B"}],
                        }
                    },
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == "result A\nresult B"

    def test_multiple_user_messages_across_conversation(self):
        """Across a multi-step loop, multiple user messages accumulate."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [{"text": "first step"}],
                        }
                    }
                ],
            },
            {"role": "assistant", "content": [{"text": "thinking..."}]},
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t2",
                            "content": [{"text": "second step"}],
                        }
                    }
                ],
            },
        ]
        result = _extract_tool_result_text(messages)
        assert result == "first step\nsecond step"


class TestAssistantRoleIgnored:
    """Assistant messages (model responses) must be skipped entirely."""

    def test_assistant_with_toolresult_key_is_ignored(self):
        """Even if an assistant message somehow has a toolResult key, skip it."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "should_not_appear",
                            "content": [{"text": "ghost data"}],
                        }
                    }
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == ""

    def test_mixed_roles_only_user_extracted(self):
        messages = [
            {"role": "assistant", "content": [{"text": "hello"}]},
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [{"text": "real data"}],
                        }
                    }
                ],
            },
            {"role": "assistant", "content": [{"text": "reply"}]},
        ]
        result = _extract_tool_result_text(messages)
        assert result == "real data"


class TestNonListContent:
    """User messages with string content (plain text, not tool results)."""

    def test_user_message_with_string_content(self):
        """The initial user prompt is a plain string, not a list."""
        messages = [
            {"role": "user", "content": "What pods are crashing?"},
        ]
        result = _extract_tool_result_text(messages)
        assert result == ""

    def test_user_message_with_integer_content(self):
        """Edge case: content is not a list (malformed)."""
        messages = [{"role": "user", "content": 42}]
        result = _extract_tool_result_text(messages)
        assert result == ""

    def test_user_message_with_none_content(self):
        messages = [{"role": "user", "content": None}]
        result = _extract_tool_result_text(messages)
        assert result == ""


class TestMissingToolResultKey:
    """Blocks within user content list that lack the toolResult key."""

    def test_block_with_only_text_key(self):
        """Some user messages have text blocks (e.g. system prompt assembly)."""
        messages = [
            {
                "role": "user",
                "content": [{"text": "some context"}],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == ""

    def test_block_with_tooluse_not_toolresult(self):
        """toolUse blocks (assistant requests) in user content are ignored."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"toolUse": {"toolUseId": "t1", "name": "get_pods", "input": {}}},
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == ""

    def test_empty_dict_block(self):
        messages = [{"role": "user", "content": [{}]}]
        result = _extract_tool_result_text(messages)
        assert result == ""

    def test_non_dict_block_in_list(self):
        """A list item that isn't a dict at all."""
        messages = [{"role": "user", "content": ["just a string", 123]}]
        result = _extract_tool_result_text(messages)
        assert result == ""


class TestEmptyContentList:
    """User message with an empty content list."""

    def test_empty_list(self):
        messages = [{"role": "user", "content": []}]
        result = _extract_tool_result_text(messages)
        assert result == ""


class TestItemWithoutText:
    """toolResult content items that have no 'text' key or empty text."""

    def test_content_item_with_image_instead_of_text(self):
        """Some tool results might have non-text content (image blocks)."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [{"image": {"format": "png", "source": "..."}}],
                        }
                    }
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == ""

    def test_content_item_with_empty_text(self):
        """Empty string text should not be included (falsy)."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [{"text": ""}],
                        }
                    }
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == ""

    def test_mixed_text_and_non_text_items(self):
        """Only text items should be extracted; others skipped."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [
                                {"text": "valid"},
                                {"image": {"format": "png", "source": "..."}},
                                {"text": "also valid"},
                            ],
                        }
                    }
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == "valid\nalso valid"

    def test_non_dict_items_in_result_content(self):
        """If content list has non-dict items, skip them gracefully."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": ["not a dict", 42, {"text": "ok"}],
                        }
                    }
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == "ok"


class TestErrorToolResultShape:
    """Error toolResult (fail-open pattern from agentic_loop._error_tool_result).

    Shape: {"toolResult": {"toolUseId": ..., "content": [{"text": msg}], "status": "error"}}
    The function should still extract the text — error results are evidence too.
    """

    def test_error_tool_result_text_extracted(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t_err",
                            "content": [{"text": "[Tool error] connection timeout"}],
                            "status": "error",
                        }
                    }
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == "[Tool error] connection timeout"

    def test_mixed_success_and_error_results(self):
        """Both success and error results contribute to effective_infra_data."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [{"text": "3 pods running"}],
                        }
                    },
                    {
                        "toolResult": {
                            "toolUseId": "t2",
                            "content": [{"text": "timeout querying metrics"}],
                            "status": "error",
                        }
                    },
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == "3 pods running\ntimeout querying metrics"


class TestToolResultContentNotList:
    """Edge case: toolResult.content is not a list."""

    def test_content_is_string(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": "raw string instead of list",
                        }
                    }
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == ""

    def test_content_is_none(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": None,
                        }
                    }
                ],
            }
        ]
        result = _extract_tool_result_text(messages)
        assert result == ""


class TestEmptyInput:
    """Empty or trivial message lists."""

    def test_empty_messages_list(self):
        assert _extract_tool_result_text([]) == ""

    def test_messages_with_no_user_role(self):
        messages = [
            {"role": "assistant", "content": [{"text": "hi"}]},
            {"role": "system", "content": "prompt"},
        ]
        assert _extract_tool_result_text(messages) == ""
