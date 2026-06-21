# LibreChat Integration

AIgent-squad exposes an **OpenAI-compatible API** (`/v1/models`,
`/v1/chat/completions`), so [LibreChat](https://www.librechat.ai/) — or any
OpenAI-compatible client — can talk to the squad **directly**, with no extra
gateway. (Spec: [`.kiro/specs/29-openai-compat-bridge/`](https://github.com/StaffOps/staffops-aigent-squad/tree/main/.kiro/specs/29-openai-compat-bridge).)

## How it works

```
LibreChat ──OpenAI /v1/chat/completions──> Supervisor (:8000)
                                              └─ classifier → agents → synthesis
```

The bridge is a thin translation layer over the existing supervisor — same
routing, fan-out, and RCA investigation as the native `/query` endpoint.

## Models

`GET /v1/models` returns:

| Model id | Behavior |
|----------|----------|
| `aigent-squad` | Classifier auto-routes (fan-out / investigation as needed) |
| `aigent-squad-aws` | Force the AWS specialist (bypass classifier) |
| `aigent-squad-kubernetes` | Force the Kubernetes specialist |
| `aigent-squad-finops` | Force the FinOps specialist |
| `aigent-squad-devops` | Force the DevOps specialist |
| `aigent-squad-observability` | Force the Observability specialist |

(Per-agent models are derived from the registry — they match your enabled agents.)

## Setup

1. **Run the squad** with an `INTERNAL_API_TOKEN` set (the bridge is
   authenticated — fail-closed, no token = 401).

2. **Point LibreChat at the bridge.** Use
   [`infra/librechat/librechat.yaml`](https://github.com/StaffOps/staffops-aigent-squad/blob/main/infra/librechat/librechat.yaml) as a
   starting point — it registers the squad as a custom endpoint and forwards the
   token in the `X-Internal-Token` header. Set `AIGENT_SQUAD_API_KEY` to the
   squad's `INTERNAL_API_TOKEN`.

3. **Pick a model** in the LibreChat UI: `aigent-squad` to let the squad decide,
   or `aigent-squad-<agent>` to ask one specialist directly.

## Try it without LibreChat (curl)

```bash
# List models
curl -s http://localhost:8000/v1/models \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN" | jq

# Non-streaming chat completion (auto-routed)
curl -s http://localhost:8000/v1/chat/completions \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"aigent-squad","messages":[{"role":"user","content":"how many EC2 instances are running?"}]}' | jq

# Streaming (SSE)
curl -N http://localhost:8000/v1/chat/completions \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"aigent-squad-aws","messages":[{"role":"user","content":"list S3 buckets"}],"stream":true}'
```

## Known limitations (current)

- **Pseudo-streaming**: with `stream: true` the squad runs to completion, then
  emits the full answer over the SSE protocol (one content delta). True
  token-by-token streaming arrives with spec 06. The frame format is already
  OpenAI-compatible, so the client works correctly either way.
- **`usage` is zero**: token accounting is deferred (spec 10/27).
- **Identity is self-declared**: `user`/session come from the request; there is
  no per-user authn yet (same limitation as the rest of the API — see
  `ANALYSIS.md` SEC-D12). Put the squad behind your own authn for multi-user use.
- **Read-only**: the squad is consultative — it answers, it does not execute.
