"""Tests for src.supervisor.distillation — pipeline orchestration."""
import pytest
from unittest.mock import patch, AsyncMock

from src.core.investigation import RCAResult
from src.core.kb.models import KbDelta
from src.supervisor.distillation import distill_rca


@pytest.mark.asyncio
@patch("src.supervisor.distillation.extract_deltas", new_callable=AsyncMock)
async def test_distill_skips_low_confidence_rca(mock_extract):
    rca = RCAResult(hypothesis="something", confidence="baixa")
    await distill_rca(rca)
    mock_extract.assert_not_called()


@pytest.mark.asyncio
@patch("src.supervisor.distillation.check_budget", return_value=False)
@patch("src.supervisor.distillation.extract_deltas", new_callable=AsyncMock)
async def test_distill_skips_when_budget_exhausted(mock_extract, mock_budget):
    rca = RCAResult(hypothesis="something", confidence="alta")
    await distill_rca(rca)
    mock_extract.assert_not_called()


@pytest.mark.asyncio
@patch("src.supervisor.distillation.record_cost")
@patch("src.supervisor.distillation.kb_store")
@patch("src.supervisor.distillation.embed", new_callable=AsyncMock)
@patch("src.supervisor.distillation.enrich_deltas", new_callable=AsyncMock)
@patch("src.supervisor.distillation.extract_deltas", new_callable=AsyncMock)
@patch("src.supervisor.distillation.check_budget", return_value=True)
async def test_distill_full_pipeline_inserts_kb_item(
    mock_budget, mock_extract, mock_enrich, mock_embed, mock_store, mock_record
):
    delta = KbDelta(
        action="create", type="troubleshooting",
        title="Fix X", content="Do Y", confidence=0.9,
    )
    mock_extract.return_value = [delta]
    mock_enrich.return_value = [delta]
    mock_embed.return_value = [0.1] * 1024
    mock_store.insert = AsyncMock()

    rca = RCAResult(hypothesis="root cause found", confidence="alta")
    await distill_rca(rca, investigation_id="inv-123")

    mock_store.insert.assert_called_once()
    inserted = mock_store.insert.call_args[0][0]
    assert inserted.title == "Fix X"
    assert inserted.status == "active"
