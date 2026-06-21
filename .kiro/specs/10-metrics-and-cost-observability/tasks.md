# Tasks: Metrics & Cost Observability (efficiency + quality)

- [x] T1: Define 4 metrics in `src/core/metrics.py` (collect_duration, llm_duration, prompt_size_tokens, investigation_rounds) (M1,M2,M3) — done 2026-06-18
- [x] T2: Emit `aigent.collect.duration` in `src/core/generic_agent.py` (wrap adapter fan-out) (M1) — done 2026-06-18
- [x] T3: Emit `aigent.llm.duration` + `aigent.prompt.size_tokens` in `src/core/bedrock.py` (M1,M2) — done 2026-06-18
- [x] T4: Emit `aigent.investigation.rounds` in `src/supervisor/investigation.py` (M3) — done 2026-06-18
- [x] T5: Reorganize `docs/METRICS.md` by purpose (RED/Efficiency/Quality/Domain) + add 4 metrics + "Known gaps" (cache) section (M4) — done 2026-06-18
- [x] T6: Tests for all 4 metric emissions (depends on: T1–T4) — done 2026-06-18 (test_bedrock, test_generic_agent, test_run_investigation; reviewed + strengthened by an independent agent for verification independence)
- [x] T7: Build + tests via Docker, coverage ≥90% (depends on: T6) — done 2026-06-18 (246 passed, 92.46%)

## Order
T1 → (T2, T3, T4 in parallel) → T5 → T6 → T7.

## Deferred (out of scope — see design.md "Deferred")
- `aigent.cache.hits/misses` emission — datasource cache not wired into adapters
- `aigent.cache.tokens_saved` — same; requires adapter cache layer (separate spec)
