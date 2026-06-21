# Design: Datasource Cache Layer

## Pattern: template method on the base adapter

`DatasourceAdapter.collect()` becomes a concrete method that wraps caching around
an abstract `_collect()` that each subclass implements (the old `collect` bodies
move to `_collect`). This adds caching to all five adapters in one place.

```python
class DatasourceAdapter(ABC):
    cache_ttl: int = 0            # 0/negative → caching disabled
    cache_namespace: str = "default"

    @abstractmethod
    async def _collect(self, query: str) -> str: ...

    def _cache_id(self) -> str:
        """Stable identity for the key. Override when output depends on config."""
        return self.__class__.__name__

    async def collect(self, query: str) -> str:
        if self.cache_ttl <= 0:
            return await self._collect(query)
        key = hashlib.sha256(f"{self._cache_id()}|{query}".encode()).hexdigest()
        ns = self.cache_namespace
        hit = cache.get(key, namespace=ns)
        if hit is not None:
            cache_hits.add(1, {"namespace": ns})
            return hit
        cache_misses.add(1, {"namespace": ns})
        result = await self._collect(query)
        cache.set(key, result, ttl=self.cache_ttl, namespace=ns)
        return result
```

### Per-adapter `_cache_id()`
The key must distinguish adapters whose output depends on instance config:

| Adapter | `_cache_id()` |
|---------|---------------|
| Boto3 | `boto3:` + sorted services |
| Kubernetes | class name (no instance config) |
| Http | `http:` + name + url |
| Athena | `athena:` + database.table |
| Mcp | `mcp:` + url + sorted tools |

## Threading TTL/namespace
`create_adapters(datasource_configs, cache_ttl=0, cache_namespace="default")`
sets `cache_ttl` / `cache_namespace` on each constructed adapter. The supervisor
passes the agent's config:

```python
adapters = create_adapters(
    config.datasources,
    cache_ttl=config.cache.ttl,
    cache_namespace=config.cache.namespace,
)
```
`registry` already defaults `cache.namespace` to the agent name.

## Metrics
- `aigent.cache.hits` / `aigent.cache.misses` (existing counters) emitted with a
  single bounded label `namespace` (one per agent/config, ~6). No `agent_id`
  duplicate — namespace already identifies the agent's cache.

> `aigent.cache.tokens_saved` is **not** added here. A datasource hit avoids an
> API call, not LLM tokens — the infra data still enters the prompt. That metric
> belongs to Bedrock prompt caching (spec 11). `docs/METRICS.md` is updated to
> reflect this.

## Fail-open
`CacheStore` already swallows Redis errors (returns `None` on get, no-ops on set).
So a Redis outage degrades to "always miss" — `_collect()` still runs. No new
error handling needed; a test verifies it.

## Trade-offs
- **Errors are cached too.** Adapters return error strings (they don't raise), so
  a transient `[ec2] error: ...` is cached for the TTL. Accepted: TTLs are short
  (1–60 min) and the system is fail-open by design. Error-aware caching can be a
  later refinement.
- **Sync cache calls inside async `collect()`** match the existing pattern (the
  concrete `_collect()` bodies for boto3/k8s/athena are already blocking). Not
  changing the concurrency model here.

## Testing strategy
- key determinism: same adapter+query → identical sha256 key.
- miss → `_collect` called once, value stored, `cache_misses` emitted.
- hit → `_collect` NOT called, cached value returned, `cache_hits` emitted.
- `ttl <= 0` → cache bypassed, `_collect` always called, no metrics.
- fail-open → cache.get/set raising → `collect()` still returns `_collect` output.
- `create_adapters` threads ttl/namespace onto every adapter type.
- Verification independence: tests authored by a separate agent.
