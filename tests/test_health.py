"""Tests for spec 07 — /healthz, /ready, and DependencyChecker cache."""
import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# DependencyChecker unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_redis_ok():
    """check_redis returns ok=True when Redis.ping() returns True."""
    from src.core.health import DependencyChecker

    checker = DependencyChecker(cache_ttl=5.0, timeout=2.0)
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    result = await checker.check_redis(mock_redis)

    assert result.ok is True
    assert "pong" in result.detail


@pytest.mark.asyncio
async def test_check_redis_down():
    """check_redis returns ok=False when Redis.ping() raises."""
    from src.core.health import DependencyChecker

    checker = DependencyChecker(cache_ttl=5.0, timeout=2.0)
    mock_redis = MagicMock()
    mock_redis.ping.side_effect = ConnectionError("refused")

    result = await checker.check_redis(mock_redis)

    assert result.ok is False
    assert "redis" in result.detail


@pytest.mark.asyncio
async def test_check_redis_timeout():
    """check_redis returns ok=False when ping takes longer than timeout."""
    from src.core.health import DependencyChecker

    checker = DependencyChecker(cache_ttl=5.0, timeout=0.05)
    mock_redis = MagicMock()

    def _slow_ping():
        time.sleep(0.5)
        return True

    mock_redis.ping.side_effect = _slow_ping

    result = await checker.check_redis(mock_redis)

    assert result.ok is False
    assert "timeout" in result.detail


@pytest.mark.asyncio
async def test_check_dynamodb_ok():
    """check_dynamodb returns ok=True when table.load() succeeds."""
    from src.core.health import DependencyChecker

    checker = DependencyChecker()
    mock_table = MagicMock()
    mock_table.load.return_value = None

    result = await checker.check_dynamodb(mock_table)

    assert result.ok is True


@pytest.mark.asyncio
async def test_check_dynamodb_down():
    """check_dynamodb returns ok=False when table.load() raises."""
    from src.core.health import DependencyChecker

    checker = DependencyChecker()
    mock_table = MagicMock()
    mock_table.load.side_effect = Exception("no endpoint")

    result = await checker.check_dynamodb(mock_table)

    assert result.ok is False


@pytest.mark.asyncio
async def test_cache_prevents_repinging():
    """Second call within TTL uses cached result — no second ping."""
    from src.core.health import DependencyChecker

    checker = DependencyChecker(cache_ttl=60.0, timeout=2.0)
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    await checker.check_redis(mock_redis)
    await checker.check_redis(mock_redis)

    # ping must have been called exactly once despite two check calls
    mock_redis.ping.assert_called_once()


@pytest.mark.asyncio
async def test_cache_expires():
    """After TTL expires, a fresh ping is issued."""
    from src.core.health import DependencyChecker

    checker = DependencyChecker(cache_ttl=0.05, timeout=2.0)
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    await checker.check_redis(mock_redis)
    await asyncio.sleep(0.1)
    await checker.check_redis(mock_redis)

    assert mock_redis.ping.call_count == 2


@pytest.mark.asyncio
async def test_check_bedrock_creds_ok():
    """check_bedrock_creds returns ok=True when STS.get_caller_identity() succeeds."""
    from src.core.health import DependencyChecker

    checker = DependencyChecker()
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"UserId": "test"}

    result = await checker.check_bedrock_creds(mock_sts)

    assert result.ok is True


@pytest.mark.asyncio
async def test_check_bedrock_creds_down():
    """check_bedrock_creds returns ok=False when STS raises."""
    from src.core.health import DependencyChecker

    checker = DependencyChecker()
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.side_effect = Exception("no credentials")

    result = await checker.check_bedrock_creds(mock_sts)

    assert result.ok is False


# ---------------------------------------------------------------------------
# Supervisor /healthz and /ready endpoint tests
# ---------------------------------------------------------------------------


def _make_supervisor_app():
    """Return a TestClient for the supervisor FastAPI app with mocked deps."""
    import redis as redis_lib
    import boto3

    with (
        patch("src.supervisor.agent.AgentRegistry") as mock_reg_cls,
        patch("src.supervisor.agent.SkillRegistry"),
        patch("src.supervisor.agent.GenericAgent"),
        patch("src.supervisor.agent.create_adapters", return_value=[]),
        patch("src.core.bedrock.boto3"),
        patch("src.core.state_store.boto3"),
        patch("src.core.cache.redis.Redis"),
        patch("src.core.classifier.bedrock"),
        patch("otel_helper.setup_telemetry"),
        patch("otel_helper.get_tracer", return_value=MagicMock()),
        patch("src.supervisor.server.kb_store") as mock_kb,
        patch("src.supervisor.server._health_redis") as mock_hredis,
        patch("src.supervisor.server._health_dynamodb_table") as mock_htable,
        patch("src.supervisor.server._checker") as mock_checker,
        patch("src.supervisor.server.supervisor") as mock_sup,
    ):
        mock_kb.connect = AsyncMock()
        mock_kb.close = AsyncMock()

        mock_sup.agents = {"aws": MagicMock(), "k8s": MagicMock()}
        mock_sup.close = AsyncMock()
        mock_sup.registry = MagicMock()

        from src.core.health import DepResult

        mock_checker.check_redis = AsyncMock(
            return_value=DepResult(ok=True, detail="redis: pong")
        )
        mock_checker.check_dynamodb = AsyncMock(
            return_value=DepResult(ok=True, detail="dynamodb: table reachable")
        )

        # Import after patches are applied
        import importlib
        import src.supervisor.server as srv_mod
        importlib.reload(srv_mod)

        from fastapi.testclient import TestClient
        client = TestClient(srv_mod.app, raise_server_exceptions=False)
        return client, mock_checker, mock_sup


def test_supervisor_healthz_always_200():
    """/healthz returns 200 regardless of dependency state."""
    with (
        patch("otel_helper.setup_telemetry"),
        patch("otel_helper.get_tracer", return_value=MagicMock()),
        patch("src.supervisor.server.kb_store") as mock_kb,
        patch("src.supervisor.server.supervisor") as mock_sup,
        patch("src.supervisor.server._checker"),
        patch("src.supervisor.server._health_redis"),
        patch("src.supervisor.server._health_dynamodb_table"),
    ):
        mock_kb.connect = AsyncMock()
        mock_kb.close = AsyncMock()
        mock_sup.close = AsyncMock()
        mock_sup.agents = {}
        mock_sup.registry = MagicMock()

        import importlib
        import src.supervisor.server as srv_mod
        importlib.reload(srv_mod)

        from fastapi.testclient import TestClient
        client = TestClient(srv_mod.app, raise_server_exceptions=False)

        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_supervisor_health_legacy_alias():
    """/health (legacy) returns 200."""
    with (
        patch("otel_helper.setup_telemetry"),
        patch("otel_helper.get_tracer", return_value=MagicMock()),
        patch("src.supervisor.server.kb_store") as mock_kb,
        patch("src.supervisor.server.supervisor") as mock_sup,
        patch("src.supervisor.server._checker"),
        patch("src.supervisor.server._health_redis"),
        patch("src.supervisor.server._health_dynamodb_table"),
    ):
        mock_kb.connect = AsyncMock()
        mock_kb.close = AsyncMock()
        mock_sup.close = AsyncMock()
        mock_sup.agents = {}
        mock_sup.registry = MagicMock()

        import importlib
        import src.supervisor.server as srv_mod
        importlib.reload(srv_mod)

        from fastapi.testclient import TestClient
        client = TestClient(srv_mod.app, raise_server_exceptions=False)

        response = client.get("/health")
        assert response.status_code == 200


def test_supervisor_ready_503_when_redis_down():
    """/ready returns 503 when Redis check fails."""
    from src.core.health import DepResult

    with (
        patch("otel_helper.setup_telemetry"),
        patch("otel_helper.get_tracer", return_value=MagicMock()),
        patch("src.supervisor.server.kb_store") as mock_kb,
        patch("src.supervisor.server.supervisor") as mock_sup,
        patch("src.supervisor.server._checker") as mock_checker,
        patch("src.supervisor.server._health_redis"),
        patch("src.supervisor.server._health_dynamodb_table"),
    ):
        mock_kb.connect = AsyncMock()
        mock_kb.close = AsyncMock()
        mock_sup.close = AsyncMock()
        mock_sup.agents = {"aws": MagicMock()}
        mock_sup.registry = MagicMock()

        mock_checker.check_redis = AsyncMock(
            return_value=DepResult(ok=False, detail="redis: connection refused")
        )
        mock_checker.check_dynamodb = AsyncMock(
            return_value=DepResult(ok=True, detail="dynamodb: table reachable")
        )

        import importlib
        import src.supervisor.server as srv_mod
        importlib.reload(srv_mod)

        from fastapi.testclient import TestClient
        client = TestClient(srv_mod.app, raise_server_exceptions=False)

        response = client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["redis"]["ok"] is False


def test_supervisor_ready_503_when_dynamodb_down():
    """/ready returns 503 when DynamoDB check fails."""
    from src.core.health import DepResult

    with (
        patch("otel_helper.setup_telemetry"),
        patch("otel_helper.get_tracer", return_value=MagicMock()),
        patch("src.supervisor.server.kb_store") as mock_kb,
        patch("src.supervisor.server.supervisor") as mock_sup,
        patch("src.supervisor.server._checker") as mock_checker,
        patch("src.supervisor.server._health_redis"),
        patch("src.supervisor.server._health_dynamodb_table"),
    ):
        mock_kb.connect = AsyncMock()
        mock_kb.close = AsyncMock()
        mock_sup.close = AsyncMock()
        mock_sup.agents = {"aws": MagicMock()}
        mock_sup.registry = MagicMock()

        mock_checker.check_redis = AsyncMock(
            return_value=DepResult(ok=True, detail="redis: pong")
        )
        mock_checker.check_dynamodb = AsyncMock(
            return_value=DepResult(ok=False, detail="dynamodb: no endpoint")
        )

        import importlib
        import src.supervisor.server as srv_mod
        importlib.reload(srv_mod)

        from fastapi.testclient import TestClient
        client = TestClient(srv_mod.app, raise_server_exceptions=False)

        response = client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["dynamodb"]["ok"] is False


def test_supervisor_ready_503_when_no_agents():
    """/ready returns 503 when no agents are loaded."""
    from src.core.health import DepResult

    with (
        patch("otel_helper.setup_telemetry"),
        patch("otel_helper.get_tracer", return_value=MagicMock()),
        patch("src.supervisor.server.kb_store") as mock_kb,
        patch("src.supervisor.server._health_redis"),
        patch("src.supervisor.server._health_dynamodb_table"),
    ):
        mock_kb.connect = AsyncMock()
        mock_kb.close = AsyncMock()

        import importlib
        import src.supervisor.server as srv_mod
        importlib.reload(srv_mod)

        # Override module-level globals after reload (reload undoes patch())
        mock_sup = MagicMock()
        mock_sup.close = AsyncMock()
        mock_sup.agents = {}  # no agents
        mock_sup.registry = MagicMock()
        srv_mod.supervisor = mock_sup

        mock_checker = MagicMock()
        mock_checker.check_redis = AsyncMock(
            return_value=DepResult(ok=True, detail="redis: pong")
        )
        mock_checker.check_dynamodb = AsyncMock(
            return_value=DepResult(ok=True, detail="dynamodb: table reachable")
        )
        srv_mod._checker = mock_checker

        from fastapi.testclient import TestClient
        client = TestClient(srv_mod.app, raise_server_exceptions=False)

        response = client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["agents"]["ok"] is False


def test_supervisor_ready_200_all_ok():
    """/ready returns 200 when all dependencies are healthy."""
    from src.core.health import DepResult

    with (
        patch("otel_helper.setup_telemetry"),
        patch("otel_helper.get_tracer", return_value=MagicMock()),
        patch("src.supervisor.server.kb_store") as mock_kb,
        patch("src.supervisor.server._health_redis"),
        patch("src.supervisor.server._health_dynamodb_table"),
    ):
        mock_kb.connect = AsyncMock()
        mock_kb.close = AsyncMock()

        import importlib
        import src.supervisor.server as srv_mod
        importlib.reload(srv_mod)

        # Override module-level globals after reload (reload undoes patch())
        mock_sup = MagicMock()
        mock_sup.close = AsyncMock()
        mock_sup.agents = {"aws": MagicMock(), "k8s": MagicMock()}
        mock_sup.registry = MagicMock()
        srv_mod.supervisor = mock_sup

        mock_checker = MagicMock()
        mock_checker.check_redis = AsyncMock(
            return_value=DepResult(ok=True, detail="redis: pong")
        )
        mock_checker.check_dynamodb = AsyncMock(
            return_value=DepResult(ok=True, detail="dynamodb: table reachable")
        )
        srv_mod._checker = mock_checker

        from fastapi.testclient import TestClient
        client = TestClient(srv_mod.app, raise_server_exceptions=False)

        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"


# ---------------------------------------------------------------------------
# MCP server /healthz and /ready endpoint tests
# ---------------------------------------------------------------------------


def test_mcp_healthz_always_200():
    """/healthz returns 200 — never depends on supervisor."""
    import sys
    # Ensure clean import of mcp module
    for key in list(sys.modules.keys()):
        if "mcp_server" in key or "mcp-server" in key:
            del sys.modules[key]

    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        "mcp_server",
        os.path.join(os.path.dirname(__file__), "..", "mcp-server", "mcp-server.py"),
    )
    mcp_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp_mod)

    from fastapi.testclient import TestClient
    client = TestClient(mcp_mod.app)

    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_mcp_health_legacy_alias():
    """/health (legacy) returns 200."""
    import sys
    for key in list(sys.modules.keys()):
        if "mcp_server" in key or "mcp-server" in key:
            del sys.modules[key]

    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        "mcp_server",
        os.path.join(os.path.dirname(__file__), "..", "mcp-server", "mcp-server.py"),
    )
    mcp_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp_mod)

    from fastapi.testclient import TestClient
    client = TestClient(mcp_mod.app)

    response = client.get("/health")
    assert response.status_code == 200


def test_mcp_ready_503_when_supervisor_down():
    """/ready returns 503 when supervisor is unreachable."""
    import sys
    for key in list(sys.modules.keys()):
        if "mcp_server" in key or "mcp-server" in key:
            del sys.modules[key]

    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        "mcp_server",
        os.path.join(os.path.dirname(__file__), "..", "mcp-server", "mcp-server.py"),
    )
    mcp_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp_mod)

    from fastapi.testclient import TestClient
    import httpx

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client_cls.return_value = mock_client

        client = TestClient(mcp_mod.app, raise_server_exceptions=False)
        response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["supervisor"]["ok"] is False


def test_mcp_ready_200_when_supervisor_ok():
    """/ready returns 200 when supervisor /healthz responds."""
    import sys
    for key in list(sys.modules.keys()):
        if "mcp_server" in key or "mcp-server" in key:
            del sys.modules[key]

    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        "mcp_server",
        os.path.join(os.path.dirname(__file__), "..", "mcp-server", "mcp-server.py"),
    )
    mcp_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp_mod)

    from fastapi.testclient import TestClient

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        client = TestClient(mcp_mod.app, raise_server_exceptions=False)
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
