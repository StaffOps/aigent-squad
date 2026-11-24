"""Independent verification of spec 46 environment profiles for agents (task T16).

Oracle: spec 46 AC 6-8 and design Decisions 6-8/10 — environments change WHERE
(addresses) and WHICH agents run, never WHAT an agent may do.
"""
import copy
import json
import logging
import re
from pathlib import Path

import pytest
import yaml

from src.core.agent_config import AgentConfig
from src.core.agent_env import AgentEnvError, apply_environment, enabled_env_var
from src.core.logger import JSONFormatter
from src.core.registry import AgentRegistry

REPO_AGENTS = Path(__file__).resolve().parent.parent / "agents"
SECRET = "glpat-SUPER-SECRET-VALUE-123"


def _agent(**overrides):
    raw = {
        "name": "probe",
        "description": "probe agent",
        "domain": "testing",
        "capabilities": ["x"],
        "datasources": [],
    }
    raw.update(overrides)
    return raw


def _mcp(**overrides):
    ds = {"type": "mcp", "name": "probe-mcp", "url": "${PROBE_URL}", "tools": ["list_things"]}
    ds.update(overrides)
    return ds


# ---------------------------------------------------------------------------
# Expansion in address fields (AC 6)
# ---------------------------------------------------------------------------

class TestExpansion:
    def test_all_address_fields_expand(self):
        raw = _agent(datasources=[_mcp(
            name="${DS_NAME}",
            url="https://${HOST}:${PORT}/mcp",
            headers={"Authorization": "Bearer ${TOKEN}", "X-Static": "plain"},
            tool_arguments={"namespace": "${NS}", "fixed": "v"},
        )])
        env = {"DS_NAME": "grafana", "HOST": "mcp.svc", "PORT": "8080", "TOKEN": SECRET, "NS": "monitoring"}
        resolved, degraded = apply_environment(raw, env)
        [ds] = resolved["datasources"]
        assert degraded == []
        assert ds["name"] == "grafana"
        assert ds["url"] == "https://mcp.svc:8080/mcp"
        assert ds["headers"] == {"Authorization": f"Bearer {SECRET}", "X-Static": "plain"}
        assert ds["tool_arguments"] == {"namespace": "monitoring", "fixed": "v"}
        assert ds["tools"] == ["list_things"]
        AgentConfig(**resolved)  # the result still validates

    @pytest.mark.parametrize("literal", [
        "http://host/$path", "cost $5", "$$", "$HOME", "{not}", "$ {X}", "a$b{c}",
    ])
    def test_literal_dollar_is_untouched(self, literal):
        # URLs must be http(s) (spec 46 review fix), so embed non-URL literals in a path
        url = literal if literal.startswith("http") else f"https://host/{literal}"
        resolved, degraded = apply_environment(_agent(datasources=[_mcp(url=url)]), {})
        assert resolved["datasources"][0]["url"] == url
        assert degraded == []

    def test_env_value_containing_placeholder_is_not_re_expanded(self):
        env = {"PROBE_URL": "http://x/${OTHER}", "OTHER": "nope"}
        resolved, _ = apply_environment(_agent(datasources=[_mcp()]), env)
        assert resolved["datasources"][0]["url"] == "http://x/${OTHER}"

    @pytest.mark.parametrize("bad", [
        "${PROBE_URL", "${}", "${1ABC}", "${A-B}", "${A B}", "${A:-default}", "${${A}}", "x${", "${A}${",
    ])
    def test_malformed_placeholder_fails_startup(self, bad):
        with pytest.raises(AgentEnvError):
            apply_environment(_agent(datasources=[_mcp(url=bad)]), {"PROBE_URL": "x", "A": "y"})

    def test_malformed_placeholder_in_header_value_fails(self):
        raw = _agent(datasources=[_mcp(url="http://x", headers={"Authorization": "Bearer ${TOKEN"})])
        with pytest.raises(AgentEnvError):
            apply_environment(raw, {"TOKEN": SECRET})

    def test_placeholder_in_header_or_argument_key_is_rejected(self):
        for field in ("headers", "tool_arguments"):
            raw = _agent(datasources=[_mcp(url="http://x", **{field: {"${KEY}": "v"}})])
            with pytest.raises(AgentEnvError):
                apply_environment(raw, {"KEY": "k"})

    def test_lowercase_and_underscore_names_are_valid(self):
        env = {"_x": "https://a", "mcp_url_2": "b"}
        resolved, degraded = apply_environment(_agent(datasources=[_mcp(url="${_x}/${mcp_url_2}")]), env)
        assert resolved["datasources"][0]["url"] == "https://a/b"
        assert degraded == []

    def test_defaults_to_process_environment(self, monkeypatch):
        monkeypatch.setenv("PROBE_URL", "http://from-os-environ")
        resolved, _ = apply_environment(_agent(datasources=[_mcp()]))
        assert resolved["datasources"][0]["url"] == "http://from-os-environ"


# ---------------------------------------------------------------------------
# Unset variables (AC 6, Decision 7)
# ---------------------------------------------------------------------------

class TestUnsetVariables:
    @pytest.mark.parametrize("env", [{}, {"PROBE_URL": ""}], ids=["unset", "empty"])
    def test_missing_var_drops_only_that_datasource(self, env):
        raw = _agent(datasources=[
            {"type": "kubernetes", "resources": ["pods"]},
            _mcp(),
            {"type": "http", "name": "static", "url": "http://fixed"},
        ])
        resolved, degraded = apply_environment(raw, env)
        assert [d.get("name", d["type"]) for d in resolved["datasources"]] == ["kubernetes", "static"]
        assert len(degraded) == 1
        assert degraded[0].as_dict() == {
            "agent": "probe", "datasource": "probe-mcp", "missing_env": ["PROBE_URL"]}

    def test_one_missing_var_drops_the_whole_datasource_and_names_each_var_once(self):
        ds = _mcp(
            url="${HOST}/${PATH_A}/${HOST}",
            headers={"Authorization": "Bearer ${TOKEN}"},
            tool_arguments={"ns": "${NS}"},
        )
        resolved, degraded = apply_environment(_agent(datasources=[ds]), {"HOST": "h", "TOKEN": SECRET})
        assert resolved["datasources"] == []
        assert degraded[0].missing == ("PATH_A", "NS")
        assert SECRET not in json.dumps(degraded[0].as_dict())

    def test_degraded_label_falls_back_to_type(self):
        ds = {"type": "http", "url": "${PROM}"}
        _, degraded = apply_environment(_agent(datasources=[ds]), {})
        assert degraded[0].datasource == "http"

    def test_agent_still_validates_after_drop(self):
        resolved, _ = apply_environment(_agent(datasources=[_mcp()]), {})
        assert AgentConfig(**resolved).datasources == []

    def test_degraded_record_is_immutable(self):
        _, [d] = apply_environment(_agent(datasources=[_mcp()]), {})
        with pytest.raises(Exception):
            d.agent = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Capabilities cannot be widened (AC 7)
# ---------------------------------------------------------------------------

class TestForbiddenFields:
    @pytest.mark.parametrize("field,value", [
        ("tools", ["${EXTRA_TOOL}"]),
        ("tools", ["list_things", "${EXTRA_TOOL}"]),
        ("transport", "${TRANSPORT}"),
        ("inject_query_as", "${ARG}"),
        ("services", ["${SVC}"]),
        ("resources", ["${RES}"]),
        ("type", "${TYPE}"),
        ("database", "${DB}"),
        ("headers", "${ALL_HEADERS}"),
        ("tool_arguments", "${ALL_ARGS}"),
        ("url", ["${URL}"]),
    ])
    def test_datasource_non_address_fields(self, field, value):
        env = {k: "x" for k in ("EXTRA_TOOL", "TRANSPORT", "ARG", "SVC", "RES", "TYPE", "DB",
                                "ALL_HEADERS", "ALL_ARGS", "URL")}
        ds = _mcp(url="http://x")
        ds[field] = value
        with pytest.raises(AgentEnvError):
            apply_environment(_agent(datasources=[ds]), env)

    @pytest.mark.parametrize("field,value", [
        ("read_only", "${READ_ONLY}"),
        ("capability_tier", "${TIER}"),
        ("required_env", ["${REQ}"]),
        ("skills", ["${SKILL}"]),
        ("model", {"tier": "${MODEL_TIER}"}),
        ("write_scope", ["${SCOPE}"]),
        ("hitl", {"approvers": ["${WHO}"]}),
        ("name", "${NAME}"),
        ("description", "uses ${X}"),
        ("routing_keywords", ["${KW}"]),
        ("cache", {"namespace": "${NS}"}),
        ("enabled", "${ON}"),
        ("delegates_to", [{"agent": "${A}", "when": "always"}]),
    ])
    def test_agent_level_fields(self, field, value):
        # Rejected whether or not the variable is set — profiles cannot touch these at all.
        for env in ({}, {"READ_ONLY": "false", "TIER": "3", "X": "y"}):
            with pytest.raises(AgentEnvError):
                apply_environment(_agent(**{field: value}), env)

    def test_rejected_even_when_agent_is_disabled_by_override(self):
        raw = _agent(datasources=[_mcp(url="http://x", tools=["${T}"])])
        with pytest.raises(AgentEnvError):
            apply_environment(raw, {"AGENT_PROBE_ENABLED": "false"})

    def test_error_message_never_contains_env_values(self):
        raw = _agent(datasources=[_mcp(url="http://x", headers={"A": f"{SECRET}"}, tools=["${T}"])])
        with pytest.raises(AgentEnvError) as exc:
            apply_environment(raw, {"T": SECRET})
        assert SECRET not in str(exc.value)

    def test_allowlist_and_read_only_pass_through_unchanged(self):
        raw = _agent(read_only=True, capability_tier=0, datasources=[_mcp(tools=["a", "b"])])
        resolved, _ = apply_environment(raw, {"PROBE_URL": "http://x", "AGENT_PROBE_ENABLED": "true"})
        assert resolved["read_only"] is True
        assert resolved["capability_tier"] == 0
        assert resolved["datasources"][0]["tools"] == ["a", "b"]


# ---------------------------------------------------------------------------
# AGENT_<NAME>_ENABLED (Decision 8)
# ---------------------------------------------------------------------------

class TestEnabledOverride:
    @pytest.mark.parametrize("name,var", [
        ("aws", "AGENT_AWS_ENABLED"),
        ("my-agent", "AGENT_MY_AGENT_ENABLED"),
        ("Mixed.Case-2", "AGENT_MIXED_CASE_2_ENABLED"),
        ("finops", "AGENT_FINOPS_ENABLED"),
    ])
    def test_var_name(self, name, var):
        assert enabled_env_var(name) == var

    @pytest.mark.parametrize("value,expected", [
        ("false", False), ("FALSE", False), (" 0 ", False), ("no", False), ("off", False),
        ("true", True), ("True", True), ("1", True), ("yes", True), ("ON", True),
    ])
    def test_parsing(self, value, expected):
        start = not expected
        resolved, _ = apply_environment(_agent(name="my-agent", enabled=start),
                                        {"AGENT_MY_AGENT_ENABLED": value})
        assert resolved["enabled"] is expected

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_override_keeps_yaml(self, value):
        resolved, _ = apply_environment(_agent(enabled=False), {"AGENT_PROBE_ENABLED": value})
        assert resolved["enabled"] is False
        resolved, _ = apply_environment(_agent(), {"AGENT_PROBE_ENABLED": value})
        assert resolved.get("enabled", True) is True

    @pytest.mark.parametrize("value", ["maybe", "2", "disabled", "truee"])
    def test_garbage_override_fails_loudly(self, value):
        with pytest.raises(AgentEnvError, match="AGENT_PROBE_ENABLED"):
            apply_environment(_agent(), {"AGENT_PROBE_ENABLED": value})

    def test_override_of_other_agent_is_ignored(self):
        resolved, _ = apply_environment(_agent(), {"AGENT_OTHER_ENABLED": "false"})
        assert resolved.get("enabled", True) is True

    def test_override_changes_nothing_else(self):
        raw = _agent(datasources=[_mcp(url="http://fixed")], skills=["s"], read_only=True)
        resolved, _ = apply_environment(raw, {"AGENT_PROBE_ENABLED": "false"})
        assert {k: v for k, v in resolved.items() if k != "enabled"} == raw


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------

class TestPurity:
    def test_input_is_not_mutated(self):
        raw = _agent(enabled=True, datasources=[
            _mcp(headers={"Authorization": "Bearer ${TOKEN}"}, tool_arguments={"ns": "${NS}"}),
            _mcp(name="dropped", url="${MISSING}"),
        ])
        before = copy.deepcopy(raw)
        apply_environment(raw, {"PROBE_URL": "http://x", "TOKEN": SECRET, "NS": "n",
                                "AGENT_PROBE_ENABLED": "false"})
        assert raw == before

    def test_no_datasources_key_stays_absent(self):
        raw = _agent()
        del raw["datasources"]
        resolved, degraded = apply_environment(raw, {})
        assert "datasources" not in resolved and degraded == []

    def test_non_dict_datasource_is_left_for_schema_validation(self):
        resolved, _ = apply_environment(_agent(datasources=["garbage"]), {})
        assert resolved["datasources"] == ["garbage"]

    def test_idempotent(self):
        env = {"PROBE_URL": "http://x"}
        once, _ = apply_environment(_agent(datasources=[_mcp()]), env)
        twice, _ = apply_environment(once, env)
        assert once == twice


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------

class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.records: list[logging.LogRecord] = []
        self.setFormatter(JSONFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        self.lines.append(self.format(record))


@pytest.fixture
def captured_logs():
    handler = _Capture()
    log = logging.getLogger("agent-squad")
    log.addHandler(handler)
    yield handler
    log.removeHandler(handler)


def _write(tmp_path: Path, raw: dict) -> None:
    d = tmp_path / raw["name"]
    d.mkdir()
    (d / "agent.yaml").write_text(yaml.safe_dump(raw))
    (d / "prompt.md").write_text(f"prompt for {raw['name']}")


class TestRegistryIntegration:
    def test_expanded_and_degraded_agents_both_load(self, tmp_path, monkeypatch, captured_logs):
        monkeypatch.setenv("GOOD_URL", "http://good:8080/mcp")
        monkeypatch.setenv("GOOD_TOKEN", SECRET)
        monkeypatch.delenv("BAD_URL", raising=False)
        _write(tmp_path, _agent(name="good", datasources=[
            _mcp(url="${GOOD_URL}", headers={"Authorization": "Bearer ${GOOD_TOKEN}"})]))
        _write(tmp_path, _agent(name="bad", datasources=[_mcp(name="bad-mcp", url="${BAD_URL}")]))

        reg = AgentRegistry(agents_dir=str(tmp_path))
        reg.discover()

        assert sorted(reg.agent_names()) == ["bad", "good"]
        assert reg.get("good").datasources[0].url == "http://good:8080/mcp"
        assert reg.get("good").datasources[0].headers["Authorization"] == f"Bearer {SECRET}"
        assert reg.get("bad").datasources == []
        assert [d.as_dict() for d in reg.degraded_datasources] == [
            {"agent": "bad", "datasource": "bad-mcp", "missing_env": ["BAD_URL"]}]

        warnings = [line for line, r in zip(captured_logs.lines, captured_logs.records)
                    if r.levelno == logging.WARNING]
        assert any("datasource_unconfigured" in w and "BAD_URL" in w and '"bad"' in w for w in warnings)
        # Expanded secrets never reach any log line (AC 6, Decision 10).
        assert all(SECRET not in line for line in captured_logs.lines)

    def test_rediscover_does_not_duplicate_degraded(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BAD_URL", raising=False)
        _write(tmp_path, _agent(name="bad", datasources=[_mcp(url="${BAD_URL}")]))
        reg = AgentRegistry(agents_dir=str(tmp_path))
        reg.discover()
        reg.discover()
        assert len(reg.degraded_datasources) == 1

    def test_disabled_agent_is_skipped_and_not_reported_degraded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_OFF_AGENT_ENABLED", "false")
        monkeypatch.delenv("NEEDED_ENV_XYZ", raising=False)
        _write(tmp_path, _agent(name="off-agent", required_env=["NEEDED_ENV_XYZ"],
                                datasources=[_mcp(url="${UNSET_FOR_OFF}")]))
        _write(tmp_path, _agent(name="on"))
        reg = AgentRegistry(agents_dir=str(tmp_path))
        reg.discover()  # required_env of a disabled agent must not fail startup
        assert reg.agent_names() == ["on"]
        assert reg.degraded_datasources == []

    def test_override_can_turn_a_yaml_disabled_agent_on(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_SLEEPER_ENABLED", "true")
        _write(tmp_path, _agent(name="sleeper", enabled=False))
        reg = AgentRegistry(agents_dir=str(tmp_path))
        reg.discover()
        assert reg.agent_names() == ["sleeper"]

    def test_forbidden_placeholder_fails_discovery(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SNEAKY_TOOL", "delete_everything")
        _write(tmp_path, _agent(name="evil", datasources=[_mcp(url="http://x", tools=["${SNEAKY_TOOL}"])]))
        with pytest.raises(AgentEnvError):
            AgentRegistry(agents_dir=str(tmp_path)).discover()

    def test_invalid_override_fails_discovery(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_PROBE_ENABLED", "perhaps")
        _write(tmp_path, _agent())
        with pytest.raises(AgentEnvError):
            AgentRegistry(agents_dir=str(tmp_path)).discover()


# ---------------------------------------------------------------------------
# The shipped agents/ directory (AC 8)
# ---------------------------------------------------------------------------

def _placeholders_in_repo_agents() -> set[str]:
    found: set[str] = set()
    for path in REPO_AGENTS.glob("*/agent.yaml"):
        found |= set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", path.read_text()))
    return found


class TestShippedAgents:
    def test_off_aws_profile_without_addresses_still_loads(self, monkeypatch):
        for var in _placeholders_in_repo_agents() | {"AWS_REGION"}:
            monkeypatch.delenv(var, raising=False)
        for name in ("aws", "finops", "security"):  # AWS-only agents, off in the off-AWS profile
            monkeypatch.setenv(enabled_env_var(name), "false")
        reg = AgentRegistry(agents_dir=str(REPO_AGENTS))
        reg.discover()
        names = set(reg.agent_names())
        assert not names & {"aws", "finops", "security"}
        assert {"kubernetes", "observability"} <= names
        degraded_agents = {d.agent for d in reg.degraded_datasources}
        assert {"kubernetes", "observability"} <= degraded_agents
        for d in reg.degraded_datasources:
            assert d.missing, "a degraded entry always names the missing variable"

    def test_fully_addressed_profile_has_no_degraded(self, monkeypatch):
        for var in _placeholders_in_repo_agents():
            monkeypatch.setenv(var, f"http://{var.lower()}.example:8080")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        reg = AgentRegistry(agents_dir=str(REPO_AGENTS))
        reg.discover()
        assert reg.degraded_datasources == []
        for cfg in reg.list_agents():
            for ds in cfg.datasources:
                for value in (ds.url, ds.name, *ds.headers.values(), *ds.tool_arguments.values()):
                    assert "${" not in value
