# Design: User Feedback Loop (phase 1)

## Architecture

```
client ──/query or /v1──▶ gateway ──/internal/process──▶ supervisor
            │   (answer + response id)
            │   record_response(id → consumer, user, session, agent)   Redis, TTL 24 h, fail-open
            ▼
client ──POST /feedback {response_id, score, comment?}──▶ gateway (edge auth)
            │   GET  feedback:resp:<id>      missing / other consumer → 404
            │   SET  feedback:score:<id> NX  exists → 409          Redis, TTL 7 d
            │   aigent.feedback.score{agent_id, score}++
            ▼
         201 {response_id, status: "recorded"}
```

All code lives in the gateway (`src/gateway/feedback.py`): an APIRouter plus a small
`FeedbackStore`. The supervisor is unchanged. The gateway already owns edge auth and the
consumer identity (F-024), which is what scoping needs, and invariant 8 holds: storing a
rating is transport-level bookkeeping, not orchestration.

## Rationale

### Decision 1: the gateway mints the response id

**Choice**: the gateway generates one id per answer — `resp-<32 hex>` on `/query` (body
field `response_id`), `chatcmpl-<32 hex>` on `/v1` (the OpenAI `id`) — and returns it in an
`X-Aigent-Response-Id` header on every answer shape.

**Justification**:
1. The gateway is where the authenticated consumer is known; minting the id there lets the
   ownership record be written in the same place, with no new field on `/internal/process`.
2. On `/v1` the OpenAI `id` is what clients already keep per message, so a client needs no
   new field to rate an answer.
3. Additive only: `/query` gains one field; `/v1` keeps its `chatcmpl-` id format.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| Proxied `/v1` streams carry the supervisor's chunk ids, not the gateway id | The header carries the rateable id; the gateway's own fallback stream uses the same id in its chunks. Threading the id into the supervisor stream is tasks.md P2 |
| Proxied streams are labelled `agent_id="auto"` (or the forced agent) | The gateway does not parse the SSE body; the label stays bounded. P2 with the id threading |

**When this is wrong**: if a UI (LibreChat bridge) can only rate by chunk id — then do P2.

**Alternatives**: supervisor-minted id (needs the id back through the gateway before the
record can be written, and a payload change on the trust boundary); hash of the answer
(collides on identical answers, leaks nothing useful).

### Decision 2: `POST /feedback` on the gateway, scoped to the consumer

**Choice**: `POST /feedback` with `{response_id, score, comment?}`; the same edge-auth
dependency as `/query`. Score is `"up"|"down"` or an integer 1–5. Unknown fields → 422.
Returns 201 `{response_id, status: "recorded"}`.

**Justification**:
1. The ownership record stores `consumer_id` (the non-secret key id from F-024). A rating
   whose consumer differs from the record's is answered 404, the same as an unknown id, so
   one API key can neither rate nor probe another key's responses.
2. Within one consumer the `user_id` is client-supplied (not a security boundary), so the
   check is on the consumer only — the same boundary as history.
3. Two scales (thumbs and stars) because both kinds of clients exist; they are normalised to
   one bucket for the metric (Decision 4).

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| First rating wins (`SET NX` → 409 on repeat) | Keeps the counter exact (counters cannot be decremented). Changing a vote is out of scope |
| Expired and unknown ids are indistinguishable (both 404) | Intended — no existence oracle |

### Decision 3: Redis with TTL, two keys, no content

**Choice**:
- `feedback:resp:<id>` → JSON `{consumer_id, user_id, session_id, agent_id, created_at}`,
  TTL `FEEDBACK_RESPONSE_TTL_SECONDS` (default 86400 = the 24 h history retention: an answer
  can be rated while its conversation still exists).
- `feedback:score:<id>` → JSON `{score, bucket, comment, agent_id, consumer_id, user_id,
  session_id, created_at}`, `SET NX`, TTL `FEEDBACK_RECORD_TTL_SECONDS` (default 604800 = 7 d).

**Justification**:
1. The trend for spec 33 comes from the metric (VictoriaMetrics keeps it); the stored record
   only has to live long enough for B-19 to consume it. Redis is already a gateway dependency.
2. No answer text and no user input is stored. The user/session ids are the scoped ids history
   already holds; with them B-19 can fetch the answer from history while it exists.
3. The only new user content is the optional comment, capped by `FEEDBACK_COMMENT_MAX_CHARS`
   (default 1000) and never logged.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| A Redis flush loses unconsumed ratings | The metric already counted them; B-19 is the only reader |
| The comment outlives the 24 h history (7 d) | Documented in SECURITY.md retention; tunable |

**When this is wrong**: if B-19 or a review needs months of records — move the record store
to the history backend (spec 46) in B-19.

### Decision 4: one counter with two bounded labels

**Choice**: `aigent.feedback.score` (Counter) labels `agent_id` and `score` ∈
`positive|neutral|negative` (`up`, 4, 5 → positive; 3 → neutral; `down`, 1, 2 → negative).
`aigent.feedback.errors` (Counter) label `reason` ∈ `unknown_response|already_rated|
store_unavailable|record_failed`.

**Justification**:
1. A histogram would use the default SDK buckets (0, 5, 10, …) that put every 1–5 score in one
   bucket (the spec 41 problem); a counter per bucket is exact.
2. `agent_id` is sanitised to `[a-z0-9_-]{1,40}` (else `other`) so a bad value cannot add series.
   Never `user_id`, `session_id`, `response_id` or `consumer_id`.

### Decision 5: fail-open for queries, fail-closed for the feedback write

Recording the response id runs after the answer exists and swallows every Redis error
(`aigent.feedback.errors{reason="record_failed"}`) — a query never fails because of feedback.
`POST /feedback` with Redis unavailable answers 503 `feedback_unavailable`: without the record
it cannot check ownership, and accepting unverified ratings would let one key rate another's
answers (invariant 6: availability for queries, closed for the security check).
`FEEDBACK_ENABLED=false` stops recording and makes `/feedback` answer 404.

## Invariants

- No feedback path changes, removes or renames an existing `/query` or `/v1` field.
- A rating is accepted only when the stored record's `consumer_id` equals the caller's.
- Metric labels are bounded; no id or free text becomes a label.
- The comment never reaches an LLM in phase 1. B-19 must pass it through the spec-14 layers
  (InputScanner + guardrail) before any model sees it.

## Verification

Unit + HTTP tests with fakeredis (`tests/test_feedback.py`): auth, cross-consumer 404,
validation, TTLs, 409 on repeat, 404 on expired, 503 on Redis error, fail-open recording,
label sets, and `/query` + `/v1` shape regression. `make test` (≥90 %), `make lint`,
`make typecheck`, `make specs-status`.

## Risks

| Risk | Mitigation |
|------|------------|
| Feedback spam inflating the counter | One rating per response (NX); only ids the caller's key received are rateable |
| Comment used as an injection vector later | Not sent to any LLM here; B-19 requirement above |
| Clients relying on the exact `/query` key set | Additive field only; called out in CHANGELOG |
