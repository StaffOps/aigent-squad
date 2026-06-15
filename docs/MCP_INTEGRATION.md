# MCP Integration

## Architecture

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
