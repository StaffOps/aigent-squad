"""Agent registry — discovers and validates agents from AGENTS_DIR."""
import os
from pathlib import Path
from typing import Optional

import yaml

from src.core.agent_config import AgentConfig
from src.core.logger import logger


class AgentRegistry:
    """Discovers agent configs from filesystem. Each subdir with agent.yaml = 1 agent."""

    def __init__(self, agents_dir: Optional[str] = None):
        self.agents_dir = Path(agents_dir or os.getenv("AGENTS_DIR", "agents"))
        self.agents: dict[str, AgentConfig] = {}
        self.prompts: dict[str, str] = {}

    def discover(self) -> None:
        """Scan AGENTS_DIR, parse and validate all agent configs."""
        if not self.agents_dir.exists():
            raise RuntimeError(f"AGENTS_DIR not found: {self.agents_dir}")

        for agent_dir in sorted(self.agents_dir.iterdir()):
            config_path = agent_dir / "agent.yaml"
            if not agent_dir.is_dir() or not config_path.exists():
                continue

            raw = yaml.safe_load(config_path.read_text())
            config = AgentConfig(**raw)

            if not config.enabled:
                logger.info(f"Agent '{config.name}' disabled, skipping")
                continue

            # Validate required env vars
            for env_var in config.required_env:
                if not os.getenv(env_var):
                    raise RuntimeError(
                        f"Agent '{config.name}' requires env var '{env_var}' but it is not set"
                    )

            # Load prompt
            prompt_path = agent_dir / "prompt.md"
            prompt = prompt_path.read_text() if prompt_path.exists() else f"You are {config.name}. {config.description}"

            # Set cache namespace default
            if not config.cache.namespace:
                config.cache.namespace = config.name

            self.agents[config.name] = config
            self.prompts[config.name] = prompt
            logger.info(f"Registered agent: {config.name} ({len(config.datasources)} datasources)")

    def get(self, name: str) -> Optional[AgentConfig]:
        return self.agents.get(name)

    def get_prompt(self, name: str) -> str:
        return self.prompts.get(name, "")

    def list_agents(self) -> list[AgentConfig]:
        return list(self.agents.values())

    def agent_names(self) -> list[str]:
        return list(self.agents.keys())
