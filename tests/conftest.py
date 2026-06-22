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
