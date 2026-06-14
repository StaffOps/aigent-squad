"""Tests for src/core/classifier.py"""
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from src.core.agent_config import AgentConfig
from src.core.classifier import Classifier, ClassifierResult, AgentMatch


def _make_registry():
    """Create a mock registry with 2 agents."""
    aws_config = AgentConfig(
        name="aws", description="AWS specialist", domain="cloud", capabilities=["ec2"]
    )
    k8s_config = AgentConfig(
        name="kubernetes", description="K8s specialist", domain="infra", capabilities=["pods"]
    )
    registry = MagicMock()
    registry.agent_names.return_value = ["aws", "kubernetes"]
    registry.list_agents.return_value = [aws_config, k8s_config]
    return registry


@pytest.mark.asyncio
async def test_classify_returns_agent_name():
    registry = _make_registry()
    classifier = Classifier(registry)

    bedrock_response = json.dumps({
        "agents": [{"agent": "aws", "confidence": 0.95}],
        "reasoning": "User asks about EC2"
    })

    with patch("src.core.classifier.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=bedrock_response)
        result = await classifier.classify("list my EC2 instances", [])

    assert isinstance(result, ClassifierResult)
    assert result.selected_agent == "aws"
    assert result.agents[0].agent == "aws"
    assert result.agents[0].confidence == 0.95
    assert result.reasoning == "User asks about EC2"


@pytest.mark.asyncio
async def test_classify_fallback_on_invalid_json():
    registry = _make_registry()
    classifier = Classifier(registry)

    bedrock_response = "I think this should go to the kubernetes agent for pods."

    with patch("src.core.classifier.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=bedrock_response)
        result = await classifier.classify("list pods", [])

    assert result.selected_agent == "kubernetes"
    assert result.agents[0].agent == "kubernetes"
    assert result.agents[0].confidence == 0.5
    assert result.reasoning == "Fallback parsing"


@pytest.mark.asyncio
async def test_classify_unknown_on_garbage():
    registry = _make_registry()
    classifier = Classifier(registry)

    bedrock_response = "~~~random garbage that matches nothing~~~"

    with patch("src.core.classifier.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=bedrock_response)
        result = await classifier.classify("asdfghjkl", [])

    assert result.selected_agent == "unknown"
    assert result.agents == []
    assert result.confidence == 0.0
