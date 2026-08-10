"""Shared fixtures for AIgent-squad tests."""
import pytest


MINIMAL_AGENT_YAML = """\
name: {name}
description: Test agent for {name}
domain: testing
capabilities:
  - test-capability
datasources: []
"""


@pytest.fixture
def make_agent_dir(tmp_path):
    """Factory fixture to create an agent directory with agent.yaml + prompt.md."""

    def _make(name: str, yaml_override: str | None = None, prompt: str = "You are a test agent."):
        agent_dir = tmp_path / name
        agent_dir.mkdir()
        yaml_content = yaml_override or MINIMAL_AGENT_YAML.format(name=name)
        (agent_dir / "agent.yaml").write_text(yaml_content)
        (agent_dir / "prompt.md").write_text(prompt)
        return agent_dir

    return _make


@pytest.fixture(autouse=True)
def _disable_guardrail_by_default(monkeypatch):
    """Disable the module-level guardrail singleton so tests that predate spec 14
    don't trip the fail-closed path. Tests that exercise the guardrail explicitly
    patch it themselves.
    """
    try:
        from src.core.guardrail import guardrail
        monkeypatch.setattr(guardrail, "enabled", False)
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _reset_module_level_state():
    """Reset every module-level mutable container in ``src/`` before each test.

    These globals are correct in production — a circuit breaker that forgets its
    failures cannot trip, and the agent-name cache exists to avoid re-reading the
    registry per request. They are only a hazard under test, where one test's
    accumulated state silently becomes the next test's starting condition. With a
    fixed collection order that stays benign by luck; the suite runs under
    ``pytest-randomly`` with an unpinned seed, so luck is not available.

    This is deliberately a single repo-wide fixture rather than a per-file reset.
    F-016 was closed claiming a whole bug class was removed when in fact only one
    global (``_KEY_AGENT_MAP``) had been dealt with, and CI then failed on
    ``_server_breakers`` — which one test class already reset in its own
    ``setup_method``, proving the hazard was known but handled locally. Anything
    added to the list below is covered everywhere at once.

    Keep this in sync when a new module-level container appears in ``src/``. Find
    them with::

        grep -rnE '^_[a-zA-Z_]+: *(dict|list|set)\\[.*\\] *= *(\\{\\}|\\[\\]|set\\(\\))' src/
    """
    targets = (
        ("src.core.agentic_loop", "_server_breakers"),  # per-MCP-server circuit breakers
        ("src.core.canary", "_detection_counts"),       # canary detection tallies
        ("src.gateway.main", "_agent_names"),           # cached agent-name list
    )
    for module_path, attr in targets:
        try:
            module = __import__(module_path, fromlist=[attr])
            getattr(module, attr).clear()
        except (ImportError, AttributeError):
            # A module that isn't importable in this context cannot leak state.
            pass
