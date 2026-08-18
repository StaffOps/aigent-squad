---
spec: 29-openai-compat-bridge
status: done
completed: 2026-07-01
superseded_by: null
depends_on: []
deferred: []
---

# Feature: OpenAI-Compatible Bridge (LibreChat-direct)

**Spec**: `29-openai-compat-bridge`
**Severity**: 🟢 Feature (integration)
**Related**: `02-unify-agent-architecture`, `17-multi-agent-collaboration`,
`06-resilience-patterns` (streaming), `04-harden-security` (auth).

Expose the AIgent-squad supervisor through an **OpenAI-compatible HTTP API**
(`/v1/models`, `/v1/chat/completions`) so that **LibreChat** — or any
OpenAI-compatible client — can consume the squad directly, with **no extra
gateway** (no chaitops in the path).

This is "Option A": LibreChat talks OpenAI → the squad's own supervisor. The
squad keeps its native `/query` endpoint; the bridge is an additional surface.

## Why

LibreChat configures providers as OpenAI "custom endpoints" (`baseURL` +
`models`). To plug the squad in, the squad must speak the OpenAI Chat
Completions contract. Today the supervisor only exposes `POST /query` with a
bespotke `{user_input, user_id, session_id}` body and a `{agent, response, ...}`
reply — not OpenAI-shaped.

## User Stories

WHEN LibreChat calls `GET /v1/models` THEN the system SHALL return the squad as
one or more OpenAI `model` objects.

WHEN a client calls `POST /v1/chat/completions` with `model: "aigent-squad"`
THEN the system SHALL route the last user message through the supervisor
(classifier auto-routing + fan-out + investigation) and return an OpenAI
`chat.completion`.

WHEN a client calls `POST /v1/chat/completions` with `model:
"aigent-squad-<agent>"` (e.g. `aigent-squad-aws`) THEN the system SHALL bypass
the classifier and route directly to that specialist agent.

WHEN the request has `stream: true` THEN the system SHALL respond with
`text/event-stream` emitting OpenAI `chat.completion.chunk` events and a final
`[DONE]` sentinel.

WHEN the request has `stream: false` (or omitted) THEN the system SHALL return a
single `chat.completion` JSON object.

WHEN the `model` is unknown THEN the system SHALL return HTTP 404 with an
OpenAI-shaped error body.

WHEN the bridge endpoints are called THEN they SHALL require the same
authentication as the rest of the API (`require_token`, `X-Internal-Token`),
fail-closed.

## Acceptance Criteria

- [ ] `GET /v1/models` lists `aigent-squad` plus `aigent-squad-<agent>` per
  registered agent, in OpenAI `{object:"list", data:[{id,object,owned_by}]}` form.
- [ ] `POST /v1/chat/completions` (non-stream) returns a valid `chat.completion`
  with `choices[0].message.content` = the supervisor/agent response.
- [ ] `POST /v1/chat/completions` (stream) returns SSE `chat.completion.chunk`
  frames: a role prelude, content delta(s), a finish frame, then `data: [DONE]`.
- [ ] Unknown model → 404 with `{"error":{"type":...,"message":...}}`.
- [ ] Both endpoints enforce `require_token` (401 without the header).
- [ ] The message translator maps the OpenAI `messages[]` array to the
  supervisor's single `user_input` (last user turn; system messages preserved as
  context), and derives a stable `session_id`.
- [ ] ≥90% test coverage on the new module (Docker-measured).
- [ ] A `librechat.yaml` example + docs show how to wire it.

## Out of scope

- True token-by-token streaming from Bedrock — the supervisor returns a full
  response today (see spec 06). The bridge emits the completed answer over the
  SSE protocol ("pseudo-streaming"); real streaming is a later enhancement.
- `usage` token accounting (returned as zeros for now; real token metrics live
  in spec 10/27).
- Tool-calling / function-calling OpenAI features.
- Rate limiting (handled at the ingress/API layer, not the bridge).
