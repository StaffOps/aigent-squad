# Tasks: User Feedback Loop (phase 1)

## Phase 1

- [x] T1 Spec: requirements, design (Decisions 1–5), tasks (2026-11-24)
- [x] T2 Settings `FEEDBACK_ENABLED`, `FEEDBACK_RESPONSE_TTL_SECONDS`, `FEEDBACK_RECORD_TTL_SECONDS`, `FEEDBACK_COMMENT_MAX_CHARS` (AC3, AC4) (2026-11-24)
- [x] T3 `src/gateway/feedback.py`: `FeedbackStore` (record + submit) and `POST /feedback` router (AC2–AC5) (2026-11-24)
- [x] T4 Response id plumbing in `src/gateway/main.py`: `/query` `response_id`, `/v1` `id`, `X-Aigent-Response-Id` header, fallback stream chunk ids (AC1) (2026-11-24)
- [x] T5 Metrics `aigent.feedback.score`, `aigent.feedback.errors` in `src/core/metrics.py` (AC6) (2026-11-24)
- [x] T6 Docs: `docs/METRICS.md`, `AGENTS.md`, `docs/site/architecture.md` gateway API, `docs/SECURITY.md` retention, `specs/TRIGGERS.md` row + review template line, `.env.example`, `CHANGELOG.md` (AC7) (2026-11-24)
- [x] T7 Implementer tests `tests/test_feedback.py` + shape regression updates; gates green (AC8) (2026-11-24)

## Phase 2 (not started)

- [ ] P2 Thread the response id into the supervisor's streaming chunks and report the real `agent_id` for proxied streams (design Decision 1 trade-offs)

## Verification independence

- [ ] T8 Independent test authorship — a different session extends/reviews the tests against this spec (AC9)
- [ ] T9 Independent code review — fresh pass against steering, AGENTS.md invariants and the ACs (AC9)
- [ ] T10 Security review — new input surface + auth scoping (`specs/README.md` mandatory rule) (AC9)

## Order

T2 → T3/T5 (parallel) → T4 → T6/T7. T8–T10 after T7, by other sessions.
