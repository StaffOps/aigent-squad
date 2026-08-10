# MCP Server for Agent Squad

Exposes Agent Squad as an HTTP API for integration with Kiro CLI.

## Endpoints

- `GET /health` - Health check
- `POST /query` - Query Agent Squad

## Usage

```bash
# Via docker-compose
docker-compoif up -d mcp-server

# Test
curl http://localhost:8006/health
curl -X POST http://localhost:8006/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "How many instances EC2?", "user_id": "test"}'
```

## Configuration Kiro CLI

Add to `~/.kiro/mcp.json`:

```json
{
  "mcpServers": {
    "agent-squad": {
      "url": "http://localhost:8006/query"
    }
  }
}
```
