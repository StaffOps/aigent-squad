"""Validate all agent configs for capability tier consistency (spec 43).

Exit 0 if all pass; exit 1 with details on first failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.agent_config import AgentConfig  # noqa: E402


def main() -> int:
    agents_dir = Path(__file__).resolve().parent.parent / "agents"
    errors: list[str] = []
    validated = 0

    for agent_dir in sorted(agents_dir.iterdir()):
        yaml_path = agent_dir / "agent.yaml"
        if not yaml_path.is_file():
            continue

        with open(yaml_path) as f:
            data = yaml.safe_load(f)

        try:
            AgentConfig(**data)
            validated += 1
        except Exception as e:
            errors.append(f"  {yaml_path.relative_to(agents_dir.parent)}: {e}")

    if errors:
        print(f"❌ capability-validate FAILED ({len(errors)} error(s)):")
        for err in errors:
            print(err)
        return 1

    print(f"✅ capability-validate OK — {validated} agent configs valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
