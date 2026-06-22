# Design: OpenAI-Compatible Bridge

**Spec**: `29-openai-compat-bridge`

## Architecture

```
LibreChat ──OpenAI /v1/chat/completions──> Supervisor (:8000)
                                              │
   model = "aigent-squad"        ────────────┤ classifier auto-routes
   model = "aigent-squad-<agent>" ───────────┤ direct to specialist
                                              ▼
                                   supervisor.process_request(...)
                                   → {agent, response, confidence, ...}
                                              │
                          openai_compat re-encodes ↓
              ┌───────────────────────────────────────────────┐
              │ stream=false → ChatCompletionResponse (JSON)   │
              │ stream=true  → SSE chunks + [DONE]             │
              └───────────────────────────────────────────────┘
```

The bridge is a **thin translation layer** in `src/supervisor/openai_compat.py`,
plus two routes in `src/supervisor/server.py`. It calls the **existing**
`supervisor.process_request(...)` — no change to orchestration logic.

## Components

| Component | Responsibility |
|-----------|----------------|
| Pydantic models | OpenAI request/response/chunk/model shapes |
| `messages_to_user_input()` | OpenAI `messages[]` → supervisor `user_input` |
| `resolve_target(model)` | Map `model` → (auto-route \| specific agent) or raise |
| `build_completion()` | `{agent,response}` → `ChatCompletionResponse` |
| `sse_stream()` | `{agent,response}` → OpenAI SSE chunk generator + `[DONE]` |
| routes in `server.py` | `/v1/models`, `/v1/chat/completions` (+ `require_token`) |

## Rationale (decisions and trade-offs)

### Decision 1: Translate in a dedicated module, call the existing supervisor

**Choice**: A separate `openai_compat.py` that adapts to/from OpenAI shapes and
delegates to `supervisor.process_request(...)`; the supervisor stays untouched.

**Justification, in order of strength**:
1. The supervisor already encodes routing, fan-out, investigation, history. Re-
   using it means the OpenAI surface and the native `/query` surface can never
   diverge in behavior.
2. Keeps the OpenAI contract isolated — if LibreChat/OpenAI shapes change, only
   this module changes (`server.py` stays transport-only, per `project.md`).
3. Testable in isolation with a mocked supervisor (no Bedrock needed).

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| Two HTTP surfaces (`/query` + `/v1/*`) | Both thin; both delegate to one core. |
| OpenAI `messages[]` → single `user_input` loses some structure | The squad is turn-oriented; we pass the last user turn + system context. History lives server-side in DynamoDB keyed by session. |

**When this would be wrong**: if LibreChat needed real multi-turn array
semantics the squad can't model from a single `user_input` — then the supervisor
contract itself would need to accept `messages[]`.

### Decision 2: Expose both an auto-routing model and per-agent models

**Choice**: `aigent-squad` (classifier decides) **and** `aigent-squad-<agent>`
(force a specialist), listed by `/v1/models`.

**Justification**:
1. LibreChat shows models in a dropdown — users get "let the squad decide" plus
   "ask AWS directly" without any squad-side config.
2. Per-agent models make the squad's structure legible to the user and are
   trivial to derive from the registry.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| More entries in the model list | Bounded by agent count (~6). |
| Per-agent path bypasses the classifier | Intentional — it's the point. Falls back to 404 if the agent name is unknown. |

### Decision 3: Pseudo-streaming over the SSE protocol

**Choice**: When `stream: true`, run `process_request` to completion, then emit
the answer as OpenAI SSE chunks (role prelude → one content delta → finish →
`[DONE]`).

**Justification, in order of strength**:
1. The supervisor returns a full string today (no token streaming until spec
   06). LibreChat **requires** the SSE protocol when `stream:true` — returning
   JSON breaks the client. So we must speak SSE even without true streaming.
2. The chunk format is forward-compatible: when real streaming lands, only the
   generator changes to yield many deltas instead of one.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| No early-token UX (user waits for full answer, then it appears) | Honest given current backend; documented as a known limitation, not hidden. |

**When this would be wrong**: once spec 06 gives `AsyncIterable` token streaming,
this should switch to per-token deltas.

### Decision 4: Reuse `require_token`; map identity from request

**Choice**: Both `/v1/*` routes depend on `require_token` (same
`X-Internal-Token`). `user_id`/`session_id` are derived (header/`user` field →
default), since OpenAI requests don't carry the squad's identity fields.

**Justification**:
1. `project.md` forbids unauthenticated `/process`/`/query`-style endpoints. The
   bridge is the same class of surface → same gate, fail-closed.
2. LibreChat sends a configured API key header; mapping it to `X-Internal-Token`
   is a one-line LibreChat config (`headers`).

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| Self-declared identity (no per-user authn yet) | Same limitation the whole API has today (SEC-D12 in ANALYSIS); not made worse. Documented. |

## Invariants

- `server.py` stays transport-only — all shaping in `openai_compat.py`.
- The bridge never bypasses `require_token`.
- Unknown model → 404 OpenAI error, never a 500.
- `stream:true` always ends with exactly one `data: [DONE]`.

## Sequence (non-stream)

```
LibreChat → POST /v1/chat/completions {model, messages, stream:false}
server.py: require_token → resolve_target(model)
         → messages_to_user_input(messages)
         → supervisor.process_request(user_input, user_id, session_id, mode)
         ← {agent, response, confidence}
         → build_completion() → 200 ChatCompletionResponse
```

## External dependencies

| Service | Purpose |
|---------|---------|
| (none new) | Reuses supervisor, registry, auth already present. |
