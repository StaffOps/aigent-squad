# MCP Integration

AIgent-squad touches MCP in **two opposite directions** — don't confuse them:

| Direction | Who is server / client | Component | Doc section |
|-----------|------------------------|-----------|-------------|
| **Inbound** | squad **is** the MCP server; Kiro CLI is the client | `mcp-server/` (:8006) | "Inbound" below |
| **Outbound** | an agent **is** an MCP client; consumes external tools | `McpAdapter` (`type: mcp`) | "Outbound" below |

---

## Inbound — squad as MCP server (Kiro CLI → squad)

### Architecture

```
Kiro CLI ──[HTTP/SSE]──▶ MCP Server (:8006) ──[X-Internal-Token]──▶ Supervisor (:8000)
```

The MCP server exposes AIgent-squad capabilities as MCP tools. It authenticates to the supervisor using `X-Internal-Token`.

## Local setup

### 1. Start the stack

```bash
docker compose up -d
```

Both supervisor (`:8000`) and MCP server (`:8006`) start together.

### 2. Configure Kiro CLI

Add to `~/.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "aigent-squad": {
      "url": "http://localhost:8006/mcp"
    }
  }
}
```

### 3. Use

```bash
kiro-cli chat
# Queries are automatically routed through MCP → supervisor → agents
```

## Authentication

The MCP server passes `X-Internal-Token` to the supervisor on every request. Token is set via `INTERNAL_API_TOKEN` env var (shared between MCP server and supervisor containers).

`/health` endpoint on both services is unauthenticated (for probes).

## Endpoints

| Endpoint | Port | Auth | Purpose |
|----------|------|------|---------|
| `/mcp` | 8006 | None (client-facing) | MCP protocol (SSE transport) |
| `/health` | 8006 | None | Liveness probe |

## Multi-turn conversations

The MCP server maintains session context via `session_id`. Follow-up queries are routed to the same agent when the classifier detects continuity.

## Troubleshooting

```bash
# Verify both services are up
curl http://localhost:8000/health
curl http://localhost:8006/health

# Check MCP server logs
docker compose logs mcp-server
```

---

## Outbound — agents as MCP clients (`type: mcp` datasource)

An agent can pull read-only context from an external MCP server by adding an
`mcp` datasource to its `agent.yaml`. This is **Caminho A** of ADR-001:
MCP-as-adapter, *not* autonomous tool-calling. The collector connects, calls
the allowlisted tools, and injects the result into the prompt — the model
never selects tools itself.

```yaml
# agents/kubernetes/agent.yaml
datasources:
  - type: mcp
    name: k8s-mcp
    url: ${K8S_MCP_URL}              # e.g. http://k8s-mcp.aigent-squad:8080/sse
    tools: [list_pods, get_pod_metrics, list_events]   # read-only allowlist
    tool_arguments:                  # static args merged into every call
      namespace: devops
```

### Security model (read-only is law)

- **Fail-closed allowlist**: only tools in `tools` may be invoked. Empty list
  ⇒ nothing is called (the adapter doesn't even connect).
- **Operator-curated**: list only read-only tools. The model cannot pick a
  tool outside the allowlist, so a server exposing mutating tools can't be
  abused via this path.
- **Fail-open at runtime**: a broken server or a failing tool degrades to an
  `[mcp:...] error: ...` string in the context — it never crashes the agent.

### Transport

Uses MCP **SSE** transport (`mcp==1.0.0`). `url` supports `${ENV_VAR}`
interpolation so it differs per environment (local vs EKS). For stdio-based
servers, a `type: mcp` over stdio variant is a future extension.

### When to use this vs. an `http`/`boto3` adapter

Use `mcp` when a capability is already packaged as an MCP server (e.g. a
Kubernetes or Prometheus MCP server). Use `boto3`/`http`/`athena` for direct
SDK/API access you control. Both feed the same prompt-injection-guarded
`<infra_data>` block.
