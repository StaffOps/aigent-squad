"""Contract tests for the datasource cache layer (spec 30).

These tests target the *contract* of `DatasourceAdapter.collect()` as a
template method wrapping a deterministic, TTL-bounded, fail-open cache around
the abstract `_collect()`:

- disabled when cache_ttl <= 0 (no cache touch, no metrics)
- miss path: _collect called, cache.set called, cache_misses emitted
- hit path: _collect NOT called, cache_hits emitted, cached value returned
- deterministic sha256 key (same query -> same key; different query -> diff)
- fail-open: cache failures never break collect()
- create_adapters threads cache_ttl/cache_namespace onto every adapter
- _cache_id() overrides encode per-instance identity

Independent author (verification independence): implementation written by
another author; these assert behavior, not internals.
"""
import hashlib
from unittest.mock import patch

import pytest

from src.core.adapters import (
    AthenaAdapter,
    Boto3Adapter,
    DatasourceAdapter,
    HttpAdapter,
    McpAdapter,
    create_adapters,
)
from src.core.agent_config import DatasourceConfig


# --- A tiny concrete adapter we fully control -------------------------------


class _CountingAdapter(DatasourceAdapter):
    """Minimal concrete adapter: counts _collect calls, echoes the query."""

    def __init__(self):
        self.calls = 0
        self.last_query = None

    async def _collect(self, query: str) -> str:
        self.calls += 1
        self.last_query = query
        return f"fresh:{query}:{self.calls}"


def _expected_key(adapter: DatasourceAdapter, query: str) -> str:
    return hashlib.sha256(f"{adapter._cache_id()}|{query}".encode()).hexdigest()


# --- 1. Caching disabled (ttl <= 0) -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [0, -1, -300])
async def test_collect_disabled_calls_collect_every_time_no_cache_no_metrics(ttl):
    adapter = _CountingAdapter()
    adapter.cache_ttl = ttl

    with patch("src.core.adapters.cache") as mock_cache, \
         patch("src.core.adapters.cache_hits") as mock_hits, \
         patch("src.core.adapters.cache_misses") as mock_misses:
        r1 = await adapter.collect("q")
        r2 = await adapter.collect("q")

    # _collect runs on every call (no caching)
    assert adapter.calls == 2
    assert r1 == "fresh:q:1"
    assert r2 == "fresh:q:2"

    # cache is never touched, no metrics emitted
    mock_cache.get.assert_not_called()
    mock_cache.set.assert_not_called()
    mock_hits.add.assert_not_called()
    mock_misses.add.assert_not_called()


# --- 2. Cache miss ----------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_miss_runs_collect_sets_cache_and_emits_miss_metric():
    adapter = _CountingAdapter()
    adapter.cache_ttl = 300
    adapter.cache_namespace = "ns-test"

    with patch("src.core.adapters.cache") as mock_cache, \
         patch("src.core.adapters.cache_hits") as mock_hits, \
         patch("src.core.adapters.cache_misses") as mock_misses:
        mock_cache.get.return_value = None  # miss
        result = await adapter.collect("hello")

    assert result == "fresh:hello:1"
    assert adapter.calls == 1

    expected_key = _expected_key(adapter, "hello")
    mock_cache.get.assert_called_once_with(expected_key, namespace="ns-test")
    mock_cache.set.assert_called_once_with(
        expected_key, "fresh:hello:1", ttl=300, namespace="ns-test"
    )

    mock_misses.add.assert_called_once_with(1, {"namespace": "ns-test"})
    mock_hits.add.assert_not_called()


# --- 3. Cache hit -----------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_hit_skips_collect_emits_hit_metric_returns_cached():
    adapter = _CountingAdapter()
    adapter.cache_ttl = 300
    adapter.cache_namespace = "ns-hit"

    with patch("src.core.adapters.cache") as mock_cache, \
         patch("src.core.adapters.cache_hits") as mock_hits, \
         patch("src.core.adapters.cache_misses") as mock_misses:
        mock_cache.get.return_value = "cached-value"  # hit
        result = await adapter.collect("hello")

    assert result == "cached-value"
    assert adapter.calls == 0  # _collect NOT called on hit

    mock_cache.set.assert_not_called()  # do not re-write on hit
    mock_hits.add.assert_called_once_with(1, {"namespace": "ns-hit"})
    mock_misses.add.assert_not_called()


@pytest.mark.asyncio
async def test_collect_hit_on_empty_string_is_still_a_hit():
    """An empty-string cached value is not None -> must be treated as a hit
    (cache.get returns "" which is falsy but not None)."""
    adapter = _CountingAdapter()
    adapter.cache_ttl = 60

    with patch("src.core.adapters.cache") as mock_cache, \
         patch("src.core.adapters.cache_hits") as mock_hits, \
         patch("src.core.adapters.cache_misses") as mock_misses:
        mock_cache.get.return_value = ""  # cached empty string == a real hit
        result = await adapter.collect("q")

    assert result == ""
    assert adapter.calls == 0
    mock_hits.add.assert_called_once()
    mock_misses.add.assert_not_called()


# --- 4. Key determinism -----------------------------------------------------


@pytest.mark.asyncio
async def test_key_is_deterministic_same_query_same_key_across_calls():
    adapter = _CountingAdapter()
    adapter.cache_ttl = 300

    with patch("src.core.adapters.cache") as mock_cache, \
         patch("src.core.adapters.cache_hits"), \
         patch("src.core.adapters.cache_misses"):
        mock_cache.get.return_value = None
        await adapter.collect("same-query")
        await adapter.collect("same-query")

    keys = [c.args[0] for c in mock_cache.get.call_args_list]
    assert keys[0] == keys[1]
    assert keys[0] == _expected_key(adapter, "same-query")


@pytest.mark.asyncio
async def test_key_differs_for_different_queries():
    adapter = _CountingAdapter()
    adapter.cache_ttl = 300

    with patch("src.core.adapters.cache") as mock_cache, \
         patch("src.core.adapters.cache_hits"), \
         patch("src.core.adapters.cache_misses"):
        mock_cache.get.return_value = None
        await adapter.collect("query-A")
        await adapter.collect("query-B")

    keys = [c.args[0] for c in mock_cache.get.call_args_list]
    assert keys[0] != keys[1]
    assert keys[0] == _expected_key(adapter, "query-A")
    assert keys[1] == _expected_key(adapter, "query-B")


@pytest.mark.asyncio
async def test_key_differs_for_different_cache_id():
    """Two adapters with different identities must produce different keys for
    the same query (key includes _cache_id())."""
    a1 = Boto3Adapter(services=["ec2"])
    a2 = Boto3Adapter(services=["s3"])
    a1.cache_ttl = a2.cache_ttl = 300

    with patch("src.core.adapters.cache") as mock_cache, \
         patch("src.core.adapters.cache_hits"), \
         patch("src.core.adapters.cache_misses"), \
         patch.object(Boto3Adapter, "_collect", return_value="x"):
        mock_cache.get.return_value = None
        await a1.collect("q")
        await a2.collect("q")

    keys = [c.args[0] for c in mock_cache.get.call_args_list]
    assert keys[0] != keys[1]


# --- 5. Fail-open -----------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_open_relies_on_cachestore_swallowing_errors():
    """Fail-open at the CacheStore boundary: CacheStore.get/set swallow Redis
    errors and return None / no-op, so a store that fails open (returns None on
    get) lets collect() proceed as a clean miss and return fresh data.

    This complements the wrapper-level fail-open
    (test_fail_open_cache_get_raises_* / _set_raises_*), where collect() itself
    catches raw exceptions from the cache object — defense in depth across both
    layers.
    """
    adapter = _CountingAdapter()
    adapter.cache_ttl = 300

    with patch("src.core.adapters.cache") as mock_cache, \
         patch("src.core.adapters.cache_hits"), \
         patch("src.core.adapters.cache_misses"):
        # Store fails open: get returns None (miss), set is a silent no-op.
        mock_cache.get.return_value = None
        mock_cache.set.return_value = None
        result = await adapter.collect("q")

    assert result == "fresh:q:1"
    assert adapter.calls == 1
    mock_cache.set.assert_called_once()


@pytest.mark.asyncio
async def test_fail_open_real_cachestore_get_set_swallow_errors():
    """Integration-ish: with a real CacheStore whose redis is None (offline),
    get returns None and set is a no-op -> collect() behaves as a clean miss
    and returns the fresh value without raising."""
    from src.core.cache import CacheStore

    store = CacheStore()
    store.redis = None  # simulate Redis unavailable (fail-open posture)

    adapter = _CountingAdapter()
    adapter.cache_ttl = 300

    with patch("src.core.adapters.cache", store), \
         patch("src.core.adapters.cache_hits"), \
         patch("src.core.adapters.cache_misses"):
        result = await adapter.collect("q")

    assert result == "fresh:q:1"
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_fail_open_cache_get_raises_treated_as_miss():
    """Wrapper-level fail-open: if `cache.get` itself RAISES (not just returns
    None), collect() must swallow it, fall through to _collect() and return the
    fresh value as a clean miss. A miss metric is still emitted."""
    adapter = _CountingAdapter()
    adapter.cache_ttl = 300
    adapter.cache_namespace = "ns-getraise"

    with patch("src.core.adapters.cache") as mock_cache, \
         patch("src.core.adapters.cache_hits") as mock_hits, \
         patch("src.core.adapters.cache_misses") as mock_misses:
        mock_cache.get.side_effect = Exception("redis exploded on get")
        result = await adapter.collect("q")

    # fresh value returned, _collect ran exactly once (treated as a miss)
    assert result == "fresh:q:1"
    assert adapter.calls == 1

    # cache.get was attempted and raised; collect() still proceeded
    mock_cache.get.assert_called_once()
    # miss metric still emitted for the namespace
    mock_misses.add.assert_called_once_with(1, {"namespace": "ns-getraise"})
    mock_hits.add.assert_not_called()


@pytest.mark.asyncio
async def test_fail_open_cache_set_raises_no_exception_propagates():
    """Wrapper-level fail-open: a clean miss (get -> None) where `cache.set`
    RAISES must NOT propagate. collect() still returns the fresh _collect()
    value."""
    adapter = _CountingAdapter()
    adapter.cache_ttl = 300
    adapter.cache_namespace = "ns-setraise"

    with patch("src.core.adapters.cache") as mock_cache, \
         patch("src.core.adapters.cache_hits") as mock_hits, \
         patch("src.core.adapters.cache_misses") as mock_misses:
        mock_cache.get.return_value = None          # clean miss
        mock_cache.set.side_effect = Exception("redis exploded on set")
        result = await adapter.collect("q")          # must not raise

    # fresh value still returned despite the failing write-back
    assert result == "fresh:q:1"
    assert adapter.calls == 1

    mock_cache.set.assert_called_once()
    # it was a miss, so the miss metric is emitted, never a hit
    mock_misses.add.assert_called_once_with(1, {"namespace": "ns-setraise"})
    mock_hits.add.assert_not_called()


# --- 6. create_adapters threads cache settings ------------------------------


def test_create_adapters_threads_cache_ttl_and_namespace_all_types():
    configs = [
        DatasourceConfig(type="boto3", services=["ec2"]),
        DatasourceConfig(type="http", name="api", url="http://x.local", headers={}),
        DatasourceConfig(type="mcp", name="m", url="http://mcp:8080/sse", tools=["t"]),
    ]
    adapters = create_adapters(configs, cache_ttl=300, cache_namespace="aws")

    assert len(adapters) == 3
    types = {type(a) for a in adapters}
    assert types == {Boto3Adapter, HttpAdapter, McpAdapter}
    for a in adapters:
        assert a.cache_ttl == 300
        assert a.cache_namespace == "aws"


def test_create_adapters_defaults_keep_caching_disabled():
    """Default create_adapters() (no cache args) leaves caching disabled."""
    adapters = create_adapters([DatasourceConfig(type="boto3", services=["ec2"])])
    assert adapters[0].cache_ttl == 0
    assert adapters[0].cache_namespace == "default"


# --- 7. _cache_id() overrides -----------------------------------------------


def test_boto3_cache_id_is_order_independent():
    a = Boto3Adapter(services=["s3", "ec2", "rds"])
    b = Boto3Adapter(services=["rds", "ec2", "s3"])
    assert a._cache_id() == b._cache_id()
    assert a._cache_id() == "boto3:ec2,rds,s3"


def test_http_cache_id_includes_name_and_url():
    a = HttpAdapter(name="prom", url="http://prom.local/api", headers={})
    cid = a._cache_id()
    assert cid == "http:prom:http://prom.local/api"


def test_athena_cache_id_includes_database_and_table():
    a = AthenaAdapter(database="costs", table="line_items")
    assert a._cache_id() == "athena:costs.line_items"


def test_mcp_cache_id_includes_url_and_sorted_tools():
    a = McpAdapter(name="k", url="http://mcp:8080/sse", tools=["b", "a", "c"])
    assert a._cache_id() == "mcp:http://mcp:8080/sse:a,b,c"


def test_default_cache_id_is_class_name():
    adapter = _CountingAdapter()
    assert adapter._cache_id() == "_CountingAdapter"
