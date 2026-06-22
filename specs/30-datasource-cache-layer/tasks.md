# Tasks: Datasource Cache Layer

- [x] T1: Base `DatasourceAdapter`: concrete `collect()` (cache wrap) + abstract `_collect()` + `_cache_id()` + `cache_ttl`/`cache_namespace` (C1,C2,C4,C6) — done 2026-06-21
- [x] T2: Move each subclass body `collect()` → `_collect()`; add `_cache_id()` (Boto3, Http, Athena, Mcp) (C1) — done 2026-06-21
- [x] T3: `create_adapters(..., cache_ttl, cache_namespace)` sets cache config on every adapter (C3) — done 2026-06-21
- [x] T4: `supervisor/agent.py` passes `config.cache.ttl` + `config.cache.namespace` to `create_adapters` (C3) — done 2026-06-21
- [x] T5: Emit `aigent.cache.hits` / `aigent.cache.misses` with `namespace` label (C5) — done 2026-06-21
- [x] T6: `docs/METRICS.md` — cache hits/misses out of "Known gaps"; `tokens_saved` reassigned to spec 11 (C5) — done 2026-06-21
- [x] T7: Tests — hit/miss/disabled/fail-open(wrapper+store)/key-determinism/threading (independent author, tests/test_cache_layer.py, 20 tests) — done 2026-06-21
- [x] T8: Build + tests via Docker, coverage ≥90% (266 passed, 92.62%) — done 2026-06-21

## Note
- Fail-open hardened beyond the design: `collect()` wraps `cache.get`/`cache.set`
  in try/except so the invariant holds even if a non-`CacheStore` backend raises
  (raised by the independent test author; addressed + covered).

## Order
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8
