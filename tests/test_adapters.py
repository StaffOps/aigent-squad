"""Tests for src/core/adapters.py"""
import os
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from src.core.adapters import (
    Boto3Adapter,
    HttpAdapter,
    KubernetesAdapter,
    create_adapters,
)
from src.core.agent_config import DatasourceConfig


# --- create_adapters tests ---


def test_create_adapters_boto3():
    cfg = DatasourceConfig(type="boto3", services=["ec2", "s3"])
    adapters = create_adapters([cfg])
    assert len(adapters) == 1
    assert isinstance(adapters[0], Boto3Adapter)
    assert adapters[0].services == ["ec2", "s3"]


def test_create_adapters_http():
    cfg = DatasourceConfig(type="http", name="test-api", url="http://example.com", headers={"X-Key": "val"})
    adapters = create_adapters([cfg])
    assert len(adapters) == 1
    assert isinstance(adapters[0], HttpAdapter)
    assert adapters[0].name == "test-api"


def test_create_adapters_kubernetes():
    cfg = DatasourceConfig(type="kubernetes")
    adapters = create_adapters([cfg])
    assert len(adapters) == 1
    assert isinstance(adapters[0], KubernetesAdapter)


def test_create_adapters_unknown_type_skipped():
    cfg = DatasourceConfig(type="unknown_xyz")
    adapters = create_adapters([cfg])
    assert adapters == []


# --- HttpAdapter tests ---


@pytest.mark.asyncio
async def test_http_adapter_collect():
    adapter = HttpAdapter(name="myapi", url="http://test.local/data", headers={})

    mock_response = MagicMock()
    mock_response.text = "response body content"
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.core.adapters.httpx.AsyncClient", return_value=mock_client):
        result = await adapter.collect("test query")

    assert "[http:myapi]" in result
    assert "response body content" in result


@pytest.mark.asyncio
async def test_http_adapter_env_interpolation():
    with patch.dict(os.environ, {"MY_HOST": "resolved.host"}):
        adapter = HttpAdapter(name="env-test", url="http://${MY_HOST}/api", headers={})
    assert adapter.url == "http://resolved.host/api"


# --- Boto3Adapter tests ---


@pytest.mark.asyncio
async def test_boto3_adapter_collect_ec2():
    adapter = Boto3Adapter(services=["ec2"])

    mock_client = MagicMock()
    mock_client.describe_instances.return_value = {
        "Reservations": [
            {"Instances": [{"State": {"Name": "running"}}, {"State": {"Name": "stopped"}}]}
        ]
    }

    with patch("src.core.adapters.boto3.client", return_value=mock_client):
        result = await adapter.collect("list instances")

    assert "[ec2]" in result
    assert "2 instances" in result
    assert "1 running" in result
    mock_client.describe_instances.assert_called_once()


# --- KubernetesAdapter tests ---


@pytest.mark.asyncio
async def test_kubernetes_adapter_graceful_failure():
    with patch("src.core.adapters.k8s_config.load_incluster_config", side_effect=Exception("no cluster")):
        with patch("src.core.adapters.k8s_config.load_kube_config", side_effect=Exception("no kubeconfig")):
            adapter = KubernetesAdapter()
            result = await adapter.collect("list pods")

    assert "[k8s] error:" in result
