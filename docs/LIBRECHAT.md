# LibreChat Integration

AIgent-squad exposes an **OpenAI-compatible API** (`/v1/models`,
`/v1/chat/completions`) on the edge gateway, so [LibreChat](https://www.librechat.ai/)
— or any OpenAI-compatible client — can talk to the squad **directly**, with
no separate translation service to run or maintain. (Spec:
[`specs/29-openai-compat-bridge/`](https://github.com/StaffOps/staffops-aigent-squad/tree/main/specs/29-openai-compat-bridge).)

## How it works

```
LibreChat ──OpenAI /v1/chat/completions──> Gateway (:8000) ──/internal/process──> Supervisor (:8001)
                                                                                     └─ classifier → agents → synthesis
```

Since spec 31 (edge gateway), the OpenAI-compatible `/v1` routes live on the
**gateway** (`src/gateway/main.py`), not the supervisor — the supervisor is
backend-only (`/internal/*`, reachable only from the gateway). The bridge
itself is a thin translation layer (`src/supervisor/openai_compat.py`) over
the same routing, fan-out, and RCA investigation as the native `/query`
endpoint; the gateway just adds edge auth, admission control, and worker-pool
backpressure in front of it.

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

## Setup — in-cluster LibreChat (Helm chart, optional)

The chart ships an **optional** in-cluster LibreChat + single-pod MongoDB
(`librechat.enabled`, default `false`; requires chart **≥ 0.9.5**). Enabled on
devops-core (`staffops` namespace) for homologation.

Enable it in the release values:

```yaml
librechat:
  enabled: true
  route:                      # expose the UI via an Istio GatewayAPI HTTPRoute
    enabled: true
    host: librechat-ais.<org>.app.br
    annotations:
      external-dns.alpha.kubernetes.io/hostname: librechat-ais.<org>.app.br
    parentRef:                # omit to inherit routing.gatewayapi.parentRef
      name: istio-dvps-internal
      namespace: istio-gateway
      sectionName: https-<org>-app-br
```

- **baseURL** auto-computes to this release's own gateway (`http://<release>-gateway:8000/v1`).
- **API key** auto-wires: with `librechat.apiKey`/`apiKeySecretName` empty and
  `externalSecrets.enabled: true`, the chart injects `AIGENT_SQUAD_API_KEY` from
  the gateway's `INTERNAL_API_TOKEN` — the token LibreChat sends as `X-Internal-Token`.
- **route** is optional — omit it (or `route.enabled: false`) for ClusterIP-only
  access via `kubectl -n staffops port-forward svc/<release>-librechat 3080:3080`.
  `route.annotations`/`labels` follow the same convention as the gateway route
  (`routing.gatewayapi.annotations`); set the `external-dns` hostname there.

**Chart requirement (≥ 0.9.5):** the LibreChat container runs with a read-only
root filesystem, so the chart mounts `emptyDir` scratch dirs (`/app/logs`,
`/app/uploads`, `/app/client/public/images`). Without them LibreChat CrashLoops
on `EROFS: read-only file system`.

### First user (registration is OFF by default)

`librechat.allowRegistration` defaults to `false` (internal tool, not a public
signup) — so there is **no default user/password**; you create the first account:

1. Temporarily enable registration and redeploy:
   `--set librechat.allowRegistration=true` (or set it in values).
2. Open the UI (`https://<route.host>` or the port-forward above) → **Sign up**
   (you choose email + password).
3. No SMTP is configured, so mark the account verified directly:
   ```bash
   kubectl -n staffops exec -it <release>-librechat-mongo-0 -- \
     mongosh LibreChat --eval 'db.users.updateOne({email:"you@x.com"},{$set:{emailVerified:true}})'
   ```
4. Turn registration back **off** once your account exists
   (`--set librechat.allowRegistration=false`, redeploy).

## Setup — local LibreChat against the real cluster (default, recommended)

The squad itself doesn't need to run anywhere near LibreChat — only
`mongo` + `librechat` run locally, pointed at the real devops-core gateway
(`https://aigent-squad.<org>.app.br`). No Helm chart, no in-cluster LibreChat
deployment: same call `staffops-chaitops` made for its own LibreChat
(docker-compose only; a K8s migration is explicitly deferred there until a
real trigger — `TODO.md` §1). `infra/librechat/librechat.yaml`'s `baseURL` is
hardcoded to the real cluster (LibreChat doesn't template that particular
field — only `apiKey`/`headers` values get `${VAR}` interpolation).

1. Fetch the real gateway token (never the local `dev-secret-token`) and bring
   the two services up:
   ```bash
   export LIBRECHAT_AIGENT_SQUAD_API_KEY=$(aws secretsmanager get-secret-value \
     --secret-id STAFFOPS_AIGENT_SQUAD --query SecretString --output text \
     --region us-east-1 | python3 -c "import json,sys; print(json.load(sys.stdin)['internal-api-token'])")
   docker compose up -d mongo librechat
   ```
2. Open `http://localhost:3080`, register the first account (becomes admin —
   LibreChat's own bootstrap; if no SMTP is configured the verification email
   never arrives — mark it verified directly: `docker compose exec mongo
   mongosh LibreChat --eval 'db.users.updateOne({email:"you@x.com"},
   {$set:{emailVerified:true}})'`).
3. Pick a model: `aigent-squad` to let the squad decide, or
   `aigent-squad-<agent>` to ask one specialist directly. `GET /api/models`
   (LibreChat's own API) confirms the `AIgent-Squad` custom endpoint fetched
   the live model list from the real gateway.

   The gateway accepts **any** `model` value — an unknown id (`base`, `large`, …) auto-routes
   via the classifier, so OpenAI-style clients that can't set a model still work (G-1). It also
   accepts `Authorization: Bearer <token>` in addition to `X-Internal-Token`/`X-API-Key` (G-2).

To point LibreChat at a **fully-local** squad instead (`make up` running
gateway+supervisor too), edit `baseURL` in `infra/librechat/librechat.yaml` to
`http://gateway:8000/v1` and use `INTERNAL_API_TOKEN`'s local default instead
of the real secret.

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
