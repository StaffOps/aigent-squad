---
spec: 47-user-feedback
status: in-progress
completed: null
superseded_by: null
depends_on: ["31-edge-gateway-worker-pool", "33-operational-review-loop"]
deferred: []
---

# Feature: User Feedback Loop (phase 1)

**Spec**: `47-user-feedback`
**Origin**: BACKLOG B-03 (PRODUCT-REVIEW PR-04). B-19 (feedback → KbDelta through the
spec-21 pipeline) is the next step and is out of scope here; this spec must not block it.
**Touches**: a new public endpoint on the gateway (input surface + auth) → the mandatory
security review applies (`specs/README.md`).

## Thesis

The squad has no signal from the people who read its answers. The eval harness (spec 35)
measures golden questions; nothing measures real answers. A thumbs-up/down (or 1–5) per
answer, counted by agent, gives spec 33 reviews a satisfaction trend and gives B-19 the
records it needs (which answer, which session, what the user said was wrong).

## User stories

- WHEN a client receives an answer from `/query` or `/v1/chat/completions` THEN the
  response SHALL carry a response id the client can rate (`response_id` on `/query`, the
  OpenAI `id` on `/v1`, and an `X-Aigent-Response-Id` header on all three shapes).
- WHEN an authenticated client sends `POST /feedback` with that id and a score THEN the
  gateway SHALL store the rating and count it in `aigent.feedback.score{agent_id, score}`.
- WHEN a consumer rates a response produced for a DIFFERENT consumer (another API key)
  THEN the gateway SHALL answer 404 exactly as for an unknown id.
- WHEN the id is unknown or its rating window expired THEN the gateway SHALL answer 404.
- WHEN the response was already rated THEN the gateway SHALL answer 409.
- WHEN Redis is down THEN queries SHALL keep working (no response id record), and
  `POST /feedback` SHALL answer 503 (it cannot verify ownership, so it refuses).
- WHEN a spec 33 review runs THEN TRIGGERS.md SHALL have a row for the negative-feedback share.

## Acceptance criteria

- [ ] AC1 Response id on `/query` (`response_id` field), `/v1` non-streaming (`id`),
  `/v1` streaming (header; chunk ids match on the gateway fallback path). No existing field
  removed or renamed.
- [ ] AC2 `POST /feedback` edge-authenticated (same dependency as `/query`), scoped to the
  authenticated consumer (`consumer_id`, F-024).
- [ ] AC3 Validation: id format, score `up|down` or integer 1–5, comment ≤
  `FEEDBACK_COMMENT_MAX_CHARS`; unknown fields rejected.
- [ ] AC4 Storage in Redis with TTLs (`FEEDBACK_RESPONSE_TTL_SECONDS`,
  `FEEDBACK_RECORD_TTL_SECONDS`); no answer text, no user input stored.
- [ ] AC5 Fail-open for queries: a Redis error while recording an id never fails a query.
- [ ] AC6 Metrics `aigent.feedback.score{agent_id, score}` and
  `aigent.feedback.errors{reason}` with bounded labels only (never user/session/response id).
- [ ] AC7 Docs: METRICS.md, AGENTS.md env table, gateway API table, SECURITY.md retention,
  TRIGGERS.md row, CHANGELOG.
- [ ] AC8 Tests ≥90 % (gate), including auth/scoping, validation, TTL, label bounds,
  unknown/expired ids, `/query` + `/v1` shape regression.
- [ ] AC9 Independent test authorship, independent code review, security review.

## Out of scope

- LibreChat (or any UI) wiring — LibreChat's own thumbs do not call this API; a bridge is a
  later task (`docs/LIBRECHAT.md`).
- Routing feedback into the KB (B-19). Phase 1 keeps the records B-19 needs (scoped user,
  session, agent) but runs no LLM and no KB write.
- Changing a rating after it is submitted; listing or exporting feedback via the API.
- Response ids inside the supervisor's own streaming chunks (see design Decision 1).
