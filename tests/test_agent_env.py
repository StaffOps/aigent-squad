"""Spec 46 Phase 2 — environment-addressed agents/MCPs (addresses + on/off, never capabilities)."""
import pytest

from src.core.agent_env import AgentEnvError, apply_environment, enabled_env_var
from src.core.registry import AgentRegistry


def _cfg(**over):
    base = {
        "name": "kubernetes",
        "description": "d",
        "domain": "k8s",
        "capabilities": ["pods"],
        "datasources": [
            {"type": "mcp", "name": "k8s-mcp", "url": "${K8S_MCP_URL}", "tools": ["pods_list"]},
            {"type": "kubernetes", "resources": ["pods"]},
        ],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Expansion of address fields
# ---------------------------------------------------------------------------

def test_url_expanded_from_env():
    raw, degraded = apply_environment(_cfg(), {"K8S_MCP_URL": "http://mcp.example.internal:8080/mcp"})
    assert raw["datasources"][0]["url"] == "http://mcp.example.internal:8080/mcp"
    assert degraded == []


def test_partial_and_multiple_placeholders():
    cfg = _cfg(datasources=[{"type": "http", "name": "gl", "url": "${BASE}/api/${VER}"}])
    raw, _ = apply_environment(cfg, {"BASE": "https://gl", "VER": "v4"})
    assert raw["datasources"][0]["url"] == "https://gl/api/v4"


def test_headers_and_tool_arguments_values_expanded():
    cfg = _cfg(datasources=[{
        "type": "http", "name": "gitlab", "url": "https://gl",
        "headers": {"PRIVATE-TOKEN": "${GITLAB_TOKEN}"},
        "tool_arguments": {"namespace": "${NS}"},
    }])
    raw, _ = apply_environment(cfg, {"GITLAB_TOKEN": "s3cr3t", "NS": "argo"})
    ds = raw["datasources"][0]
    assert ds["headers"] == {"PRIVATE-TOKEN": "s3cr3t"}
    assert ds["tool_arguments"] == {"namespace": "argo"}


def test_env_value_containing_placeholder_syntax_is_kept_verbatim():
    raw, _ = apply_environment(_cfg(), {"K8S_MCP_URL": "http://x/${not-a-var}"})
    assert raw["datasources"][0]["url"] == "http://x/${not-a-var}"


def test_literal_dollar_without_brace_untouched():
    cfg = _cfg(datasources=[{"type": "http", "name": "p", "url": "http://p/q?x=$1"}])
    raw, _ = apply_environment(cfg, {})
    assert raw["datasources"][0]["url"] == "http://p/q?x=$1"


def test_input_not_mutated():
    cfg = _cfg()
    apply_environment(cfg, {"K8S_MCP_URL": "http://a"})
    assert cfg["datasources"][0]["url"] == "${K8S_MCP_URL}"


# ---------------------------------------------------------------------------
# Unset variable → datasource dropped, agent kept, no secret values reported
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env", [{}, {"K8S_MCP_URL": ""}])
def test_unset_or_empty_var_drops_only_that_datasource(env):
    raw, degraded = apply_environment(_cfg(), env)
    assert [d["type"] for d in raw["datasources"]] == ["kubernetes"]
    assert len(degraded) == 1
    assert degraded[0].as_dict() == {"agent": "kubernetes", "datasource": "k8s-mcp", "missing_env": ["K8S_MCP_URL"]}


def test_degraded_report_never_contains_values():
    cfg = _cfg(datasources=[{
        "type": "http", "name": "gitlab", "url": "${GITLAB_URL}",
        "headers": {"PRIVATE-TOKEN": "${GITLAB_TOKEN}"},
    }])
    _, degraded = apply_environment(cfg, {"GITLAB_TOKEN": "super-secret-value"})
    assert "super-secret-value" not in repr(degraded[0].as_dict())
    assert degraded[0].missing == ("GITLAB_URL",)


def test_malformed_placeholder_fails_startup():
    cfg = _cfg(datasources=[{"type": "mcp", "name": "m", "url": "${bad-name}"}])
    with pytest.raises(AgentEnvError, match="malformed"):
        apply_environment(cfg, {})


# ---------------------------------------------------------------------------
# Capabilities can never come from the environment (AC 7)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("read_only", "${RO}"),
    ("capability_tier", "${TIER}"),
    ("required_env", ["${X}"]),
    ("skills", ["${S}"]),
    ("description", "hello ${USER}"),
    ("model", {"temperature": "${T}"}),
])
def test_placeholder_outside_datasources_rejected(field, value):
    with pytest.raises(AgentEnvError, match="only allowed in datasource address fields"):
        apply_environment(_cfg(**{field: value}), {"RO": "false", "TIER": "3"})


@pytest.mark.parametrize("ds_field,value", [
    ("tools", ["${EXTRA_TOOL}"]),
    ("type", "${TYPE}"),
    ("transport", "${T}"),
    ("inject_query_as", "${Q}"),
])
def test_placeholder_in_non_address_datasource_field_rejected(ds_field, value):
    ds = {"type": "mcp", "name": "m", "url": "http://m", "tools": ["a"], ds_field: value}
    with pytest.raises(AgentEnvError):
        apply_environment(_cfg(datasources=[ds]), {"EXTRA_TOOL": "delete_pod"})


def test_placeholder_in_header_key_rejected():
    ds = {"type": "http", "name": "h", "url": "http://h", "headers": {"${H}": "v"}}
    with pytest.raises(AgentEnvError):
        apply_environment(_cfg(datasources=[ds]), {"H": "X-Evil"})


# ---------------------------------------------------------------------------
# AGENT_<NAME>_ENABLED override
# ---------------------------------------------------------------------------

def test_enabled_env_var_name():
    assert enabled_env_var("kubernetes") == "AGENT_KUBERNETES_ENABLED"
    assert enabled_env_var("doc-rag") == "AGENT_DOC_RAG_ENABLED"


@pytest.mark.parametrize("val,expected", [("false", False), ("0", False), ("OFF", False), ("true", True), ("1", True)])
def test_enabled_override(val, expected):
    raw, _ = apply_environment(_cfg(enabled=not expected), {"AGENT_KUBERNETES_ENABLED": val, "K8S_MCP_URL": "x"})
    assert raw["enabled"] is expected


def test_enabled_override_blank_is_ignored():
    raw, _ = apply_environment(_cfg(enabled=False), {"AGENT_KUBERNETES_ENABLED": " ", "K8S_MCP_URL": "x"})
    assert raw["enabled"] is False


def test_enabled_override_invalid_fails():
    with pytest.raises(AgentEnvError, match="expected true/false"):
        apply_environment(_cfg(), {"AGENT_KUBERNETES_ENABLED": "maybe"})


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------

AGENT_YAML = """\
name: {name}
description: test
domain: testing
capabilities: [x]
datasources:
  - type: mcp
    name: k8s-mcp
    url: "${{K8S_MCP_URL}}"
    tools: [pods_list]
"""


def test_registry_drops_unconfigured_datasource_and_reports(tmp_path, make_agent_dir, monkeypatch):
    from unittest.mock import patch
    monkeypatch.delenv("K8S_MCP_URL", raising=False)
    make_agent_dir("kube", yaml_override=AGENT_YAML.format(name="kube"))
    reg = AgentRegistry(agents_dir=str(tmp_path))
    with patch("src.core.registry.logger") as log:
        reg.discover()
    assert reg.get("kube").datasources == []
    assert [d.as_dict() for d in reg.degraded_datasources] == [
        {"agent": "kube", "datasource": "k8s-mcp", "missing_env": ["K8S_MCP_URL"]}
    ]
    msg, = [c for c in log.warning.call_args_list if "datasource_unconfigured" in c.args[0]]
    assert msg.kwargs["extra"]["missing_env"] == ["K8S_MCP_URL"]


def test_registry_expands_and_keeps_allowlist(tmp_path, make_agent_dir, monkeypatch):
    monkeypatch.setenv("K8S_MCP_URL", "http://mcp.example.internal:8080/mcp")
    make_agent_dir("kube", yaml_override=AGENT_YAML.format(name="kube"))
    reg = AgentRegistry(agents_dir=str(tmp_path))
    reg.discover()
    ds = reg.get("kube").datasources[0]
    assert ds.url == "http://mcp.example.internal:8080/mcp"
    assert ds.tools == ["pods_list"]
    assert reg.degraded_datasources == []


def test_registry_env_disables_agent(tmp_path, make_agent_dir, monkeypatch):
    monkeypatch.setenv("AGENT_KUBE_ENABLED", "false")
    make_agent_dir("kube", yaml_override=AGENT_YAML.format(name="kube"))
    make_agent_dir("other")
    reg = AgentRegistry(agents_dir=str(tmp_path))
    reg.discover()
    assert reg.agent_names() == ["other"]


def test_registry_rejects_capability_from_env(tmp_path, make_agent_dir):
    make_agent_dir("evil", yaml_override=AGENT_YAML.format(name="evil").replace("[pods_list]", '["${TOOL}"]'))
    reg = AgentRegistry(agents_dir=str(tmp_path))
    with pytest.raises(AgentEnvError):
        reg.discover()


def test_repo_agents_have_no_hardcoded_mcp_urls():
    """AC 8: default MCP addresses come from the environment, not the image."""
    import pathlib
    import yaml
    for path in pathlib.Path("agents").glob("*/agent.yaml"):
        cfg = yaml.safe_load(path.read_text())
        for ds in cfg.get("datasources") or []:
            if ds.get("type") == "mcp":
                assert ds["url"].startswith("${"), f"{path}: MCP url must be ${{VAR}} (spec 46)"


# ---------------------------------------------------------------------------
# Spec 46 review fixes: expanded URL validation (L2) + sanitized schema errors (L1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,ok", [
    ("http://mcp.example.internal:8080/mcp", True),
    ("https://api.example.com/v1", True),
    ("http://127.0.0.1:9000/mcp", True),              # sidecar on loopback is legitimate
    ("file:///etc/passwd", False),
    ("ftp://h/x", False),
    ("http://user:pass@h/x", False),                   # credentials belong in headers
    ("http://169.254.169.254/latest/meta-data/", False),
    ("http://[fd00:ec2::254]/latest/", False),
    ("http://metadata.google.internal/computeMetadata/v1/", False),
    ("http://h/x\r\nX-Evil: 1", False),
    ("mcp.example:8080", False),                       # no scheme
])
def test_expanded_url_validation(url, ok):
    raw, degraded = apply_environment(_cfg(), {"K8S_MCP_URL": url})
    urls = [d.get("url") for d in raw["datasources"]]
    if ok:
        assert url in urls and degraded == []
    else:
        assert url not in urls
        assert [d.as_dict() for d in degraded] == [
            {"agent": "kubernetes", "datasource": "k8s-mcp", "missing_env": [], "reason": "invalid_url"}
        ]
        assert url not in repr(degraded[0].as_dict())  # the value never reaches /ready


def test_link_local_allowed_only_with_explicit_opt_in():
    env = {"K8S_MCP_URL": "http://169.254.170.2/v2/metadata", "AIGENT_ALLOW_LINK_LOCAL_DATASOURCES": "true"}
    raw, degraded = apply_environment(_cfg(), env)
    assert degraded == [] and raw["datasources"][0]["url"].startswith("http://169.254.")


def test_registry_schema_error_never_prints_expanded_secret(tmp_path, make_agent_dir, monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-SUPER-SECRET")
    make_agent_dir("bad", yaml_override=(
        "name: bad\ndescription: d\ndomain: x\ncapabilities: [x]\n"
        "datasources:\n  - typ: http\n    name: gl\n    url: https://gl.example\n"
        "    headers:\n      PRIVATE-TOKEN: \"${GITLAB_TOKEN}\"\n"
    ))
    reg = AgentRegistry(agents_dir=str(tmp_path))
    with pytest.raises(RuntimeError) as exc:
        reg.discover()
    assert "Invalid agent config" in str(exc.value)
    assert "glpat-SUPER-SECRET" not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
