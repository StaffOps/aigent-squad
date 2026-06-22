# Quickstart

After [installing locally](installation.md), send your first query:

## Send a query

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: dev-secret-token" \
  -d '{
    "user_input": "Which EC2 instances have CPU usage below 5% for the last 7 days?",
    "user_id": "alice",
    "session_id": "session-001"
  }'
```

The supervisor classifies the query and routes it to the **AWS agent**, which queries Cost Explorer and EC2 describe APIs and returns a structured response.

## Force a specific agent

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: dev-secret-token" \
  -d '{
    "user_input": "Are there any pods in CrashLoopBackOff?",
    "user_id": "alice",
    "session_id": "session-001",
    "agent_id": "kubernetes"
  }'
```

## Run an RCA investigation

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: dev-secret-token" \
  -d '{
    "user_input": "API latency spiked to 3s at 14:20. What happened?",
    "user_id": "alice",
    "session_id": "session-001",
    "mode": "investigate"
  }'
```

RCA mode triggers a structured investigation: symptom → parallel evidence collection → synthesis with confidence scoring.

## Via LibreChat (OpenAI-compatible bridge)

The supervisor exposes an OpenAI-compatible API at `/v1/`:

```bash
# List available models
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer dev-secret-token"

# Chat completions (routes automatically)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dev-secret-token" \
  -d '{
    "model": "aigent-squad",
    "messages": [{"role": "user", "content": "What is my monthly AWS spend?"}]
  }'
```

Configure LibreChat to use `http://localhost:8000` as a custom endpoint. See `docs/LIBRECHAT.md` in the repository.

## Next steps

- [Architecture](../architecture.md) — understand how routing and agents work
- [Add an Agent](../agents/how-to-new-agent.md) — create a new specialist in 30 seconds
- [Helm Reference](../reference/helm.md) — deploy to Kubernetes
