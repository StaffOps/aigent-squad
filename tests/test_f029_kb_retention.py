"""F-029: incident-memory KB switch, retention sweep, provenance, endpoint gating."""
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.core.config import Settings, settings
from src.core.investigation import RCAResult
from src.core.kb import store as store_mod
from src.core.kb.models import KbDelta, KbItem, KbProvenance
from src.core.kb.store import KbStore


class _ACM:
    """Async context manager returning a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return None


def _conn(fetch_batches=None):
    """A mock connection whose fetch() returns successive id batches."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_ACM(None))
    if fetch_batches is not None:
        conn.fetch = AsyncMock(side_effect=[[{"id": i} for i in b] for b in fetch_batches])
    return conn


def _store(conn):
    st = KbStore()
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_ACM(conn))
    st._pool = pool
    return st


@pytest.fixture
def kb_on(monkeypatch):
    monkeypatch.setattr(settings, "kb_enabled", True)
    monkeypatch.setattr(settings, "kb_rejected_retention_days", 30)
    monkeypatch.setattr(settings, "kb_item_max_age_days", None)
    monkeypatch.setattr(settings, "kb_retention_batch_size", 2)
    monkeypatch.setattr(settings, "kb_retention_max_batches", 3)
    monkeypatch.setattr(settings, "kb_retention_interval_seconds", 3600)


# ── Settings ────────────────────────────────────────────────────────────


def test_settings_defaults():
    s = Settings()
    assert s.kb_enabled is True
    assert s.kb_rejected_retention_days == 30
    assert s.kb_item_max_age_days is None
    assert s.kb_retention_interval_seconds > 0


def test_settings_max_age_blank_means_unset(monkeypatch):
    monkeypatch.setenv("KB_ITEM_MAX_AGE_DAYS", "")
    assert Settings().kb_item_max_age_days is None
    monkeypatch.setenv("KB_ITEM_MAX_AGE_DAYS", "90")
    assert Settings().kb_item_max_age_days == 90


@pytest.mark.parametrize("env,value", [
    ("KB_ITEM_MAX_AGE_DAYS", "0"),
    ("KB_REJECTED_RETENTION_DAYS", "0"),
    ("KB_RETENTION_BATCH_SIZE", "0"),
])
def test_settings_reject_non_positive(monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_kb_enabled_false_from_env(monkeypatch):
    monkeypatch.setenv("KB_ENABLED", "false")
    assert Settings().kb_enabled is False


# ── KB_ENABLED=false in the store ───────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_store_never_connects(monkeypatch):
    monkeypatch.setattr(settings, "kb_enabled", False)
    create = AsyncMock()
    monkeypatch.setattr(store_mod.asyncpg, "create_pool", create)
    st = KbStore()
    await st.connect()
    create.assert_not_called()
    assert st._pool is None


@pytest.mark.asyncio
async def test_disabled_store_ignores_existing_pool(monkeypatch):
    """Even with a pool (e.g. flag flipped at runtime) every method is a no-op."""
    monkeypatch.setattr(settings, "kb_enabled", False)
    conn = _conn()
    st = _store(conn)
    assert await st.insert(KbItem(title="t", content="c")) is None
    assert await st.search_similar([0.1]) == []
    assert await st.get("x") is None
    assert await st.update_status("x", "active") is False
    assert await st.list_pending_review() == []
    assert await st.enforce_retention() == 0
    st._pool.acquire.assert_not_called()


@pytest.mark.asyncio
async def test_connect_runs_retention_sweep(kb_on, monkeypatch):
    pool = MagicMock()
    monkeypatch.setattr(store_mod.asyncpg, "create_pool", AsyncMock(return_value=pool))
    st = KbStore()
    with patch.object(KbStore, "enforce_retention", new=AsyncMock(return_value=0)) as sweep:
        await st.connect()
    sweep.assert_awaited_once()
    assert st._pool is pool


@pytest.mark.asyncio
async def test_connect_failure_skips_sweep(kb_on, monkeypatch):
    monkeypatch.setattr(store_mod.asyncpg, "create_pool", AsyncMock(side_effect=OSError("refused")))
    st = KbStore()
    with patch.object(KbStore, "enforce_retention", new=AsyncMock()) as sweep:
        await st.connect()
    sweep.assert_not_called()
    assert st._pool is None


# ── Retention sweep ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_deletes_rejected_in_batches_until_short_batch(kb_on):
    conn = _conn(fetch_batches=[["a", "b"], ["c"]])
    st = _store(conn)
    with patch.object(store_mod, "kb_retention_deleted") as deleted_metric:
        assert await st.enforce_retention() == 3

    # Two batches: the second was short, so the loop stopped without a 3rd fetch.
    assert conn.fetch.await_count == 2
    first_sql, days, limit = conn.fetch.await_args_list[0].args
    assert "status IN ('rejected', 'superseded')" in first_sql
    assert "updated_at" in first_sql and "FOR UPDATE SKIP LOCKED" in first_sql
    assert (days, limit) == (30, 2)
    executed = [c.args for c in conn.execute.await_args_list]
    assert executed[0][0] == store_mod._UNLINK_SUPERSEDED_BY and executed[0][1] == ["a", "b"]
    assert executed[1][0] == store_mod._DELETE_BY_IDS and executed[1][1] == ["a", "b"]
    assert executed[3][1] == ["c"]
    assert [c.args for c in deleted_metric.add.call_args_list] == [
        (2, {"rule": "rejected_superseded"}), (1, {"rule": "rejected_superseded"}),
    ]


@pytest.mark.asyncio
async def test_sweep_stops_when_nothing_expired(kb_on):
    conn = _conn(fetch_batches=[[]])
    st = _store(conn)
    assert await st.enforce_retention() == 0
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_is_bounded_by_max_batches(kb_on):
    conn = _conn(fetch_batches=[["a", "b"], ["c", "d"], ["e", "f"], ["never"]])
    st = _store(conn)
    assert await st.enforce_retention() == 6
    assert conn.fetch.await_count == 3  # kb_retention_max_batches


@pytest.mark.asyncio
async def test_max_age_rule_runs_only_when_configured(kb_on, monkeypatch):
    conn = _conn(fetch_batches=[[], ["old"]])
    monkeypatch.setattr(settings, "kb_item_max_age_days", 90)
    st = _store(conn)
    with patch.object(store_mod, "kb_retention_deleted") as deleted_metric:
        assert await st.enforce_retention() == 1
    max_age_sql, days, _ = conn.fetch.await_args_list[1].args
    assert "status IN ('active', 'pending_review')" in max_age_sql
    assert "created_at" in max_age_sql
    assert days == 90
    deleted_metric.add.assert_called_once_with(1, {"rule": "max_age"})


@pytest.mark.asyncio
async def test_max_age_unset_keeps_active_items(kb_on):
    conn = _conn(fetch_batches=[[]])
    st = _store(conn)
    await st.enforce_retention()
    assert conn.fetch.await_count == 1  # only the rejected/superseded rule


@pytest.mark.asyncio
async def test_sweep_error_is_fail_open_and_counted(kb_on):
    conn = _conn()
    conn.fetch = AsyncMock(side_effect=RuntimeError("deadlock detected"))
    st = _store(conn)
    with patch.object(store_mod, "kb_retention_errors") as err_metric:
        assert await st.enforce_retention() == 0
    err_metric.add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_sweep_error_keeps_count_of_rows_already_deleted(kb_on, monkeypatch):
    monkeypatch.setattr(settings, "kb_item_max_age_days", 7)
    conn = _conn()
    conn.fetch = AsyncMock(side_effect=[[{"id": "a"}], RuntimeError("boom")])
    st = _store(conn)
    with patch.object(store_mod, "kb_retention_errors") as err_metric:
        assert await st.enforce_retention() == 1
    err_metric.add.assert_called_once()


# ── Opportunistic scheduling ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_schedule_not_due_does_nothing(kb_on):
    st = _store(_conn())
    st._schedule_retention()  # fresh store: last run = now
    assert st._retention_task is None


@pytest.mark.asyncio
async def test_schedule_due_starts_one_background_sweep(kb_on):
    st = _store(_conn())
    st._last_retention_run -= 7200
    gate = asyncio.Event()

    async def _slow_sweep():
        await gate.wait()
        return 0

    with patch.object(st, "enforce_retention", side_effect=_slow_sweep) as sweep:
        st._schedule_retention()
        task = st._retention_task
        assert task is not None
        st._last_retention_run -= 7200
        st._schedule_retention()  # one is still running → no second task
        assert st._retention_task is task
        gate.set()
        await task
    assert sweep.call_count == 1


@pytest.mark.asyncio
async def test_reads_and_writes_trigger_schedule(kb_on):
    conn = _conn()
    conn.fetchrow = AsyncMock(return_value={"id": "new-id"})
    conn.fetch = AsyncMock(return_value=[])
    st = _store(conn)
    with patch.object(st, "_schedule_retention") as sched:
        await st.insert(KbItem(title="t", content="c"))
        await st.search_similar([0.1])
        await st.list_pending_review()
    assert sched.call_count == 3


def test_schedule_without_running_loop_is_safe(kb_on):
    st = _store(_conn())
    st._last_retention_run -= 7200
    st._schedule_retention()
    assert st._retention_task is None


@pytest.mark.asyncio
async def test_close_cancels_running_sweep(kb_on):
    st = KbStore()
    pool = AsyncMock()
    st._pool = pool

    async def _forever():
        await asyncio.sleep(3600)

    task = asyncio.get_running_loop().create_task(_forever())
    st._retention_task = task
    await st.close()
    assert task.cancelled()
    assert st._retention_task is None
    pool.close.assert_awaited_once()


# ── Provenance ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_writes_provenance_row_in_same_transaction(kb_on):
    conn = _conn()
    conn.fetchrow = AsyncMock(return_value={"id": "item-1"})
    st = _store(conn)
    prov = KbProvenance(
        source_investigation_id="7c9e6679-7425-40de-944b-e07fc1f90ae7",
        confidence=0.8, extractor_model="m-agent", enricher_model="m-synth",
    )
    with patch.object(store_mod, "kb_provenance_written") as prov_metric:
        assert await st.insert(KbItem(title="t", content="c"), provenance=prov) == "item-1"
    conn.transaction.assert_called_once()
    sql, *args = conn.execute.await_args.args
    assert "INSERT INTO kb_provenance" in sql
    assert args == ["item-1", "7c9e6679-7425-40de-944b-e07fc1f90ae7", 0.8, "m-agent", "m-synth"]
    prov_metric.add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_insert_without_provenance_writes_no_provenance_row(kb_on):
    conn = _conn()
    conn.fetchrow = AsyncMock(return_value={"id": "item-1"})
    st = _store(conn)
    with patch.object(store_mod, "kb_provenance_written") as prov_metric:
        assert await st.insert(KbItem(title="t", content="c")) == "item-1"
    conn.execute.assert_not_called()
    prov_metric.add.assert_not_called()


@pytest.mark.asyncio
async def test_insert_provenance_failure_is_fail_open(kb_on):
    conn = _conn()
    conn.fetchrow = AsyncMock(return_value={"id": "item-1"})
    conn.execute = AsyncMock(side_effect=RuntimeError("fk violation"))
    st = _store(conn)
    prov = KbProvenance(source_investigation_id=str(uuid.uuid4()), confidence=0.5)
    assert await st.insert(KbItem(title="t", content="c"), provenance=prov) is None


def _pipeline_patches(delta):
    return (
        patch("src.supervisor.distillation.check_budget", return_value=True),
        patch("src.supervisor.distillation.extract_deltas", new=AsyncMock(return_value=[delta])),
        patch("src.supervisor.distillation.enrich_deltas", new=AsyncMock(return_value=[delta])),
        patch("src.supervisor.distillation.embed", new=AsyncMock(return_value=[0.1] * 4)),
        patch("src.supervisor.distillation.record_cost"),
        patch("src.supervisor.distillation.resolve_model", side_effect=lambda role: f"model-{role}"),
    )


@pytest.mark.asyncio
async def test_distill_passes_provenance_for_uuid_investigation_id(kb_on):
    from src.supervisor.distillation import distill_rca

    delta = KbDelta(action="create", type="pattern", title="T", content="C", confidence=0.9)
    inv_id = str(uuid.uuid4())
    store = MagicMock()
    store.insert = AsyncMock(return_value="id")
    p = _pipeline_patches(delta)
    with p[0], p[1], p[2], p[3], p[4], p[5], patch("src.supervisor.distillation.kb_store", store):
        await distill_rca(RCAResult(hypothesis="h", confidence="alta"), investigation_id=inv_id)

    item = store.insert.await_args.args[0]
    prov = store.insert.await_args.kwargs["provenance"]
    assert item.metadata["investigation_id"] == inv_id
    assert prov == KbProvenance(
        source_investigation_id=inv_id, confidence=0.9,
        extractor_model="model-agent", enricher_model="model-synthesis",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("inv_id", [None, "", "not-a-uuid"])
async def test_distill_without_valid_id_writes_no_provenance(kb_on, inv_id):
    from src.supervisor.distillation import distill_rca

    delta = KbDelta(action="create", type="pattern", title="T", content="C", confidence=0.9)
    store = MagicMock()
    store.insert = AsyncMock(return_value="id")
    p = _pipeline_patches(delta)
    with p[0], p[1], p[2], p[3], p[4], p[5], patch("src.supervisor.distillation.kb_store", store):
        await distill_rca(RCAResult(hypothesis="h", confidence="alta"), investigation_id=inv_id)
    store.insert.assert_awaited_once()
    assert store.insert.await_args.kwargs["provenance"] is None


def test_role_model_is_best_effort():
    from src.supervisor import distillation

    with patch.object(distillation, "resolve_model", side_effect=RuntimeError("misconfigured")):
        assert distillation._role_model("agent") is None


@pytest.mark.asyncio
async def test_distill_disabled_does_nothing(monkeypatch):
    from src.supervisor.distillation import distill_rca

    monkeypatch.setattr(settings, "kb_enabled", False)
    with patch("src.supervisor.distillation.check_budget") as budget, \
         patch("src.supervisor.distillation.extract_deltas", new=AsyncMock()) as extract:
        await distill_rca(RCAResult(hypothesis="h", confidence="alta"), investigation_id=str(uuid.uuid4()))
    budget.assert_not_called()
    extract.assert_not_called()


@pytest.mark.asyncio
async def test_run_investigation_sets_investigation_id():
    from src.core.state_store import ConversationMessage
    from src.supervisor.investigation import run_investigation

    agent = MagicMock()
    agent.process_request = AsyncMock(return_value=ConversationMessage(
        role="assistant", content="[]", timestamp="2026-11-24T00:00:00Z",
    ))
    with patch("src.supervisor.investigation.llm") as llm, \
         patch("src.supervisor.investigation.inject_similar_cases", new=AsyncMock(return_value="")):
        llm.invoke = AsyncMock(return_value='{"hypothesis": "h", "contradicting_evidence_indices": [], "prevention": []}')
        rca = await run_investigation(symptom="pods crashlooping", agents={"obs": agent}, session_id="s1")
    assert uuid.UUID(rca.investigation_id)  # a real InvestigationState id
    assert "investigation_id" not in rca.to_dict()  # API/LLM shape unchanged


@pytest.mark.asyncio
async def test_supervisor_passes_investigation_id_to_distillation():
    from src.supervisor.agent import SupervisorAgent

    registry = MagicMock()
    registry.list_agents.return_value = []
    registry.agent_names.return_value = []
    registry.agents = {}
    with patch("src.supervisor.agent.create_adapters", return_value=[]):
        sup = SupervisorAgent(registry)

    rca = RCAResult(hypothesis="h", confidence="alta", investigation_id=str(uuid.uuid4()))
    with patch("src.supervisor.agent.should_investigate", return_value=True), \
         patch("src.supervisor.agent.run_investigation", new=AsyncMock(return_value=rca)), \
         patch("src.supervisor.agent.distill_rca", new=AsyncMock()) as distill:
        await sup.process_request("why is X failing after deploy", "u1", "s1")
        await asyncio.sleep(0)  # let the fire-and-forget task run
    distill.assert_awaited_once_with(rca, investigation_id=rca.investigation_id)


# ── RAG + endpoints ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rag_disabled_skips_embedding_and_lookup(monkeypatch):
    from src.core.kb import rag

    monkeypatch.setattr(settings, "kb_enabled", False)
    with patch.object(rag, "embed", new=AsyncMock()) as emb, patch.object(rag, "kb_store") as st:
        assert await rag.inject_similar_cases("symptom") == ""
    emb.assert_not_called()
    st.search_similar.assert_not_called()


@pytest.fixture
def srv_client():
    from fastapi.testclient import TestClient

    import src.supervisor.server as srv

    srv.app.dependency_overrides[srv.require_token] = lambda: None
    try:
        yield srv, TestClient(srv.app, raise_server_exceptions=False)
    finally:
        srv.app.dependency_overrides.pop(srv.require_token, None)


@pytest.mark.parametrize("method,path", [
    ("get", "/kb/pending"),
    ("post", "/kb/abc/approve"),
    ("post", "/kb/abc/reject"),
])
def test_kb_endpoints_disabled_return_503(srv_client, monkeypatch, method, path):
    srv, client = srv_client
    monkeypatch.setattr(settings, "kb_enabled", False)
    with patch.object(srv, "kb_store") as st:
        resp = getattr(client, method)(path)
    assert resp.status_code == 503
    assert "KB_ENABLED=false" in resp.json()["detail"]
    st.list_pending_review.assert_not_called()
    st.update_status.assert_not_called()


def test_kb_endpoints_enabled_still_work(srv_client, monkeypatch):
    srv, client = srv_client
    monkeypatch.setattr(settings, "kb_enabled", True)
    with patch.object(srv, "kb_store") as st:
        st.list_pending_review = AsyncMock(return_value=[])
        resp = client.get("/kb/pending")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_kb_endpoint_auth_runs_before_disabled_check(monkeypatch):
    from fastapi.testclient import TestClient

    import src.supervisor.server as srv

    monkeypatch.setattr(settings, "kb_enabled", False)
    resp = TestClient(srv.app, raise_server_exceptions=False).get("/kb/pending")
    assert resp.status_code == 401
