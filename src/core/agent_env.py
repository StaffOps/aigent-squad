"""Environment profile for agent configs (spec 46 Phase 2).

Environments change WHERE things are and WHICH agents run — never WHAT an agent
may do. So, applied to the raw ``agent.yaml`` dict before schema validation:

- ``${VAR}`` is expanded only in datasource address fields (``url``, ``name``,
  ``headers`` values, ``tool_arguments`` values). A ``${`` anywhere else — e.g. in
  ``tools``, ``read_only``, ``capability_tier``, ``required_env`` — fails startup.
- A datasource whose variable is unset is dropped (the agent still loads) and
  reported as degraded; expanded values are never logged (headers carry secrets).
- ``AGENT_<NAME>_ENABLED`` can switch an agent off or back on, nothing else.
"""
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ADDRESS_FIELDS = ("url", "name")
_ADDRESS_MAPS = ("headers", "tool_arguments")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class AgentEnvError(RuntimeError):
    """A config tried to use the environment outside address fields."""


@dataclass(frozen=True)
class DegradedDatasource:
    agent: str
    datasource: str
    missing: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        # Variable NAMES only — never values.
        return {"agent": self.agent, "datasource": self.datasource, "missing_env": list(self.missing)}


def enabled_env_var(agent_name: str) -> str:
    return "AGENT_" + re.sub(r"[^A-Za-z0-9]", "_", agent_name).upper() + "_ENABLED"


def _expand(value: str, env: Mapping[str, str], missing: list[str], where: str) -> str:
    if "${" not in value:
        return value
    # Checked on the template, not the result: an env VALUE may legitimately contain "${".
    if "${" in _PLACEHOLDER.sub("", value):
        raise AgentEnvError(f"{where}: malformed placeholder (use ${{NAME}}, letters/digits/_ only)")

    def _sub(m: "re.Match[str]") -> str:
        var = m.group(1)
        if var in env and env[var] != "":
            return env[var]
        missing.append(var)  # unset or empty both mean "not configured here"
        return m.group(0)

    return _PLACEHOLDER.sub(_sub, value)


def _reject_placeholders(node: Any, where: str) -> None:
    if isinstance(node, str):
        if "${" in node:
            raise AgentEnvError(
                f"{where}: environment placeholders are only allowed in datasource "
                f"address fields (url, name, headers, tool_arguments) — spec 46"
            )
    elif isinstance(node, dict):
        for k, v in node.items():
            _reject_placeholders(k, where)
            _reject_placeholders(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _reject_placeholders(v, f"{where}[{i}]")


def apply_environment(
    raw: dict[str, Any], env: Optional[Mapping[str, str]] = None
) -> tuple[dict[str, Any], list[DegradedDatasource]]:
    """Return (resolved raw config, degraded datasources). Raises AgentEnvError."""
    env = os.environ if env is None else env
    name = str(raw.get("name", "?"))
    resolved = dict(raw)
    degraded: list[DegradedDatasource] = []

    override = env.get(enabled_env_var(name))
    if override is not None and override.strip() != "":
        flag = override.strip().lower()
        if flag in _TRUE:
            resolved["enabled"] = True
        elif flag in _FALSE:
            resolved["enabled"] = False
        else:
            raise AgentEnvError(f"{enabled_env_var(name)}={override!r}: expected true/false")

    # Everything except datasources must be placeholder-free.
    for key, value in raw.items():
        if key != "datasources":
            _reject_placeholders(value, f"agent '{name}'.{key}")

    kept: list[Any] = []
    for i, ds in enumerate(raw.get("datasources") or []):
        if not isinstance(ds, dict):
            kept.append(ds)  # schema validation reports it
            continue
        where = f"agent '{name}'.datasources[{i}]"
        missing: list[str] = []
        new_ds = dict(ds)
        for key, value in ds.items():
            if key in _ADDRESS_FIELDS and isinstance(value, str):
                new_ds[key] = _expand(value, env, missing, f"{where}.{key}")
            elif key in _ADDRESS_MAPS and isinstance(value, dict):
                new_ds[key] = {
                    k: (_expand(v, env, missing, f"{where}.{key}") if isinstance(v, str) else v)
                    for k, v in value.items()
                }
                for k in value:
                    _reject_placeholders(k, f"{where}.{key}")
            else:
                _reject_placeholders(value, f"{where}.{key}")
        if missing:
            label = str(ds.get("name") or ds.get("type") or i)
            degraded.append(DegradedDatasource(name, label, tuple(dict.fromkeys(missing))))
            continue
        kept.append(new_ds)
    if "datasources" in raw:
        resolved["datasources"] = kept
    return resolved, degraded
