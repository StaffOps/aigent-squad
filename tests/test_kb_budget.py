"""Tests for src.core.kb.budget — monthly budget cap logic."""
from unittest.mock import patch, MagicMock

from src.core.kb.budget import check_budget, record_cost


@patch("src.core.kb.budget.cache")
@patch("src.core.kb.budget.MONTHLY_BUDGET_USD", 50.0)
def test_budget_allows_when_under_cap(mock_cache):
    mock_cache.get.return_value = 10.0
    assert check_budget(5.0) is True


@patch("src.core.kb.budget.cache")
@patch("src.core.kb.budget.MONTHLY_BUDGET_USD", 50.0)
def test_budget_blocks_when_over_cap(mock_cache):
    mock_cache.get.return_value = 48.0
    assert check_budget(5.0) is False


@patch("src.core.kb.budget.cache")
def test_record_cost_updates_redis(mock_cache):
    mock_cache.get.return_value = 10.0
    record_cost(2.5)
    mock_cache.set.assert_called_once()
    args = mock_cache.set.call_args
    assert args[0][1] == 12.5
