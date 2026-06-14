"""Tests for AgentRegistry public contract.

Tests what the registry SHOULD do per spec, not how it does it internally.
"""
import pytest
from pydantic import ValidationError

from src.core.registry import AgentRegistry


class TestDiscoverFindsAgents:
    def test_discover_finds_agents(self, tmp_path, make_agent_dir):
        """Given a dir with 2 valid agent.yaml + prompt.md, discover() registers both."""
        make_agent_dir("alpha")
        make_agent_dir("beta")

        registry = AgentRegistry(agents_dir=str(tmp_path))
        registry.discover()

        assert len(registry.agent_names()) == 2
        assert "alpha" in registry.agent_names()
        assert "beta" in registry.agent_names()


class TestDiscoverSkipsDisabled:
    def test_discover_skips_disabled(self, tmp_path, make_agent_dir):
        """Agent with enabled: false is not registered."""
        make_agent_dir("active")
        make_agent_dir(
            "inactive",
            yaml_override=(
                "name: inactive\n"
                "description: Disabled agent\n"
                "domain: testing\n"
                "capabilities: [x]\n"
                "datasources: []\n"
                "enabled: false\n"
            ),
        )

        registry = AgentRegistry(agents_dir=str(tmp_path))
        registry.discover()

        assert "active" in registry.agent_names()
        assert "inactive" not in registry.agent_names()


class TestDiscoverFailsOnMissingRequiredEnv:
    def test_discover_fails_on_missing_required_env(self, tmp_path, make_agent_dir, monkeypatch):
        """Agent requiring env var that's not set → RuntimeError on discover."""
        monkeypatch.delenv("SOME_SECRET_KEY", raising=False)
        make_agent_dir(
            "needs-env",
            yaml_override=(
                "name: needs-env\n"
                "description: Agent needing env\n"
                "domain: testing\n"
                "capabilities: [x]\n"
                "datasources: []\n"
                "required_env:\n"
                "  - SOME_SECRET_KEY\n"
            ),
        )

        registry = AgentRegistry(agents_dir=str(tmp_path))

        with pytest.raises(RuntimeError, match="SOME_SECRET_KEY"):
            registry.discover()


class TestDiscoverIgnoresDirWithoutYaml:
    def test_discover_ignores_dir_without_yaml(self, tmp_path, make_agent_dir):
        """Directory without agent.yaml is silently skipped."""
        make_agent_dir("valid-agent")
        # Create a dir with no agent.yaml
        no_yaml_dir = tmp_path / "random-dir"
        no_yaml_dir.mkdir()
        (no_yaml_dir / "README.md").write_text("not an agent")

        registry = AgentRegistry(agents_dir=str(tmp_path))
        registry.discover()

        assert registry.agent_names() == ["valid-agent"]


class TestGetReturnsConfig:
    def test_get_returns_config(self, tmp_path, make_agent_dir):
        """After discover, get(name) returns the correct AgentConfig."""
        make_agent_dir("my-agent")

        registry = AgentRegistry(agents_dir=str(tmp_path))
        registry.discover()

        config = registry.get("my-agent")
        assert config is not None
        assert config.name == "my-agent"
        assert config.domain == "testing"
        assert "test-capability" in config.capabilities


class TestGetPromptReturnsContent:
    def test_get_prompt_returns_content(self, tmp_path, make_agent_dir):
        """Prompt loaded from prompt.md is retrievable."""
        make_agent_dir("prompt-agent", prompt="You are a specialized prompt agent.")

        registry = AgentRegistry(agents_dir=str(tmp_path))
        registry.discover()

        prompt = registry.get_prompt("prompt-agent")
        assert prompt == "You are a specialized prompt agent."


class TestDiscoverFailsOnInvalidSchema:
    def test_discover_fails_on_invalid_schema(self, tmp_path):
        """YAML with missing required field → ValidationError."""
        agent_dir = tmp_path / "bad-agent"
        agent_dir.mkdir()
        # Missing required fields: name, description, domain, capabilities
        (agent_dir / "agent.yaml").write_text("description: incomplete\n")
        (agent_dir / "prompt.md").write_text("prompt")

        registry = AgentRegistry(agents_dir=str(tmp_path))

        with pytest.raises(ValidationError):
            registry.discover()
