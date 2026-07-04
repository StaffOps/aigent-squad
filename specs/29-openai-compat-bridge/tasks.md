# Tasks: OpenAI-Compatible Bridge

- [x] Task 1: `src/supervisor/openai_compat.py` — Pydantic models (request,
  message, response, chunk, model list).
- [x] Task 2: `messages_to_user_input()` translator (last user turn + system
  context) and `resolve_target(model, registry)` (auto vs per-agent vs 404).
- [x] Task 3: `build_completion()` (non-stream) and `sse_stream()` (stream)
  encoders from the supervisor result dict.
- [x] Task 4: Wire `/v1/models` and `/v1/chat/completions` into
  `src/supervisor/server.py` with `require_token`; identity mapping. (depends on 1-3)
- [x] Task 5: Tests `tests/test_openai_compat.py` ≥90% — model list, translator,
  non-stream completion, SSE stream, per-agent routing, unknown model 404, auth.
  (depends on 1-4)
- [x] Task 6: `infra/librechat/librechat.yaml` example + docs
  (`docs/LIBRECHAT.md`); update README + ROADMAP. (depends on 4)
- [x] Task 7: Lint + tests via Docker; commit + push to `dev`. (depends on 5-6)

## Phase 1 status

| Task | State | Note |
|------|-------|------|
| 1-5 | this change | bridge + tests |
| Real token streaming | deferred | spec 06 (`AsyncIterable`); bridge is pseudo-stream until then |
| `usage` token counts | deferred | zeros for now; spec 10/27 |
