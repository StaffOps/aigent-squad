# MCP integration

AIgent-squad participates in the Model Context Protocol ecosystem in **two opposite
directions**. Understanding which direction applies to your use case is the first step.

| Direction | Role | Component | Port |
|-----------|------|-----------|------|
| **Inbound** | Squad is the MCP **server**; Kiro CLI is the client | `mcp-server/` | 8006 |
| **Outbound** | An agent is an MCP **client**; consumes an external MCP server | `McpAdapter` (`type: mcp`) | external |

---

## Inbound — squad as MCP server

### Architecture

```
Kiro CLI ──[HTTP/SSE]──▶ MCP Server (:8006) ──[X-Internal-Token]──▶ Supervisor (:8000)
```

The MCP server exposes AIgent-squad capabilities as MCP tools. Every inbound
request is forwarded to the supervisor with an internal service token; the
supervisor applies normal routing and authentication from that point on.

### Local setup

**Step 1 — start the stack**

```bash
docker compose up -d
```

This starts both the supervisor (`:8000`) and the MCP server (`:8006`).

**Step 2 — configure Kiro CLI**

Add the following to `~/.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "aigent-squad": {
      "url": "http://localhost:8006/mcp"
    }
  }
}
```

**Step 3 — verify**

```bash
curl http://localhost:8006/healthz
# {"status": "ok", ...}
```

Once the MCP server responds, queries from Kiro CLI are automatically routed through
MCP → supervisor → specialist agents.

### Authentication

The MCP server authenticates to the supervisor using the `X-Internal-Token` header.
The token value is shared between the two containers via the `INTERNAL_API_TOKEN`
environment variable. Set it in your `.env` file or Helm values — never hardcode it.

!!! info "Probe endpoints are open"
    `/healthz` and `/ready` on both services (`:8000` and `:8006`) are
    unauthenticated. All other endpoints require the internal token.

### Endpoints

| Endpoint | Port | Auth | Transport | Purpose |
|----------|------|------|-----------|---------|
| `/mcp` | 8006 | None (client-facing) | SSE | MCP protocol |
| `/healthz` | 8006 | None | HTTP | Liveness probe |
| `/ready` | 8006 | None | HTTP | Readiness check (calls supervisor `/healthz`) |

### Multi-turn sessions

The MCP server maintains session context via `session_id`. Follow-up queries within
a session are routed to the same agent when the classifier detects continuity,
preserving conversation history stored in DynamoDB.

---

## Outbound — agents as MCP clients

An agent can pull read-only context from an **external** MCP server by declaring a
`type: mcp` datasource in its `agent.yaml`. This follows the adapter pattern from
ADR-001: the collector calls the allowlisted tools and injects results into the
prompt — the model never selects tools autonomously.

```yaml
# agents/kubernetes/agent.yaml
datasources:
  - type: mcp
    name: k8s-mcp
    url: ${K8S_MCP_URL}                            # e.g. http://k8s-mcp.aigent-squad:8080/sse
    tools: [list_pods, get_pod_metrics, list_events]  # read-only allowlist
    tool_arguments:                                # static args merged into every call
      namespace: devops
```

### Security model

!!! warning "Allowlist is fail-closed"
    Only tools listed in `tools` may be invoked. An empty `tools` list means
    nothing is called — the adapter will not connect at all.

- **Operator-curated list** — only include read-only tools. Because the model cannot
  select outside the allowlist, a server that exposes mutating tools cannot be
  exploited through this path.
- **Fail-open at runtime** — a broken server or a failing tool call degrades to an
  `[mcp:tool-name] error: ...` string inserted into the prompt. The agent
  continues with partial context; it never crashes.

### Transport

`McpAdapter` uses MCP **SSE** transport (`mcp==1.0.0`). The `url` field supports
`${ENV_VAR}` interpolation so the same `agent.yaml` works across local and EKS
environments without modification.

### Choosing between adapter types

| Use `type: mcp` when... | Use `boto3` / `http` / `athena` when... |
|-------------------------|-----------------------------------------|
| Capability is already packaged as an MCP server (e.g. Kubernetes MCP, Prometheus MCP) | You need direct SDK access you control |
| You want to standardize tool discovery via MCP | Latency budget is tight (fewer hops) |
| Multiple agents share the same external server | The external service has no MCP wrapper |

Both paths write into the same prompt-injection-guarded `<infra_data>` block.

---

## Troubleshooting

```bash
# Check both services are healthy
curl http://localhost:8000/healthz
curl http://localhost:8006/healthz

# Stream MCP server logs
docker compose logs -f mcp-server

# Verify supervisor receives the internal token
docker compose logs supervisor | grep "X-Internal-Token"
```

!!! info "Port conflicts"
    If `:8006` is already in use locally, override with
    `MCP_SERVER_PORT=<port>` in your `.env` and update the Kiro CLI URL
    accordingly.
