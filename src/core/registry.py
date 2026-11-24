"""Agent registry — discovers and validates agents from AGENTS_DIR."""
import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import ValidationError

from src.core.agent_config import AgentConfig
from src.core.agent_env import DegradedDatasource, apply_environment, enabled_env_var
from src.core.logger import logger


class AgentRegistry:
    """Discovers agent configs from filesystem. Each subdir with agent.yaml = 1 agent."""

    def __init__(self, agents_dir: Optional[str] = None):
        self.agents_dir = Path(agents_dir or os.getenv("AGENTS_DIR") or "agents")
        self.agents: dict[str, AgentConfig] = {}
        self.prompts: dict[str, str] = {}
        # Spec 46: datasources dropped because their ${VAR} is unset in this environment.
        self.degraded_datasources: list[DegradedDatasource] = []

    def discover(self) -> None:
        """Scan AGENTS_DIR, parse and validate all agent configs."""
        if not self.agents_dir.exists():
            raise RuntimeError(f"AGENTS_DIR not found: {self.agents_dir}")

        self.degraded_datasources = []
        switches: dict[str, str] = {}  # AGENT_<NAME>_ENABLED -> agent name
        for agent_dir in sorted(self.agents_dir.iterdir()):
            config_path = agent_dir / "agent.yaml"
            if not agent_dir.is_dir() or not config_path.exists():
                continue

            raw = yaml.safe_load(config_path.read_text())
            # Spec 46: environment profile — addresses + on/off only, never capabilities.
            raw, degraded = apply_environment(raw)
            try:
                config = AgentConfig(**raw)
            except ValidationError as e:
                # Re-raise WITHOUT input values: after expansion they may hold
                # secrets (e.g. a header token) that must never reach the logs.
                errors = [
                    {"loc": ".".join(str(p) for p in err.get("loc", ())), "msg": err.get("msg", "")}
                    for err in e.errors(include_input=False, include_url=False)
                ]
                raise RuntimeError(f"Invalid agent config {config_path}: {errors}") from None

            # Two names that normalize to one AGENT_<NAME>_ENABLED (e.g. `a-b` and
            # `a_b`) would share a single on/off switch: refuse, whatever their state.
            switch = enabled_env_var(config.name)
            owner = switches.setdefault(switch, config.name)
            if owner != config.name:
                raise RuntimeError(
                    f"Agents '{owner}' and '{config.name}' share the switch {switch}; "
                    "rename one of them"
                )

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

            for d in degraded:
                logger.warning(
                    "datasource_unconfigured — dropped for this environment (agent still loads)",
                    extra=d.as_dict(),
                )
            self.degraded_datasources.extend(degraded)

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
