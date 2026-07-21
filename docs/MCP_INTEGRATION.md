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

An agent consumes an MCP server by adding an `mcp` datasource to its
`agent.yaml` (a read-only tool allowlist). **As of spec 37 / ADR-0008 this is
AGENTIC**: the LLM selects the tool AND its arguments via the Bedrock Converse
API tool-use loop — superseding the old "Caminho A" (MCP-as-adapter, which
pre-called fixed tools and injected the result; the model never chose). The
read-only invariant holds via the allowlist (fail-closed) + the MCP server's own
ServiceAccount RBAC + the guardrail (input, tool args, tool results). Transport
is streamable-http by default (`/mcp`), SSE opt-in (`transport: sse`).

```yaml
# agents/kubernetes/agent.yaml
datasources:
  - type: mcp
    name: k8s-mcp
    url: ${K8S_MCP_URL}              # e.g. http://kube-mcp.mcp-servers.svc.cluster.local:8080/sse
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

---

## REQUIRED: RBAC Audit Gate (spec 37 security boundary)

Every MCP server consumed by aigent-squad (`type: mcp` datasource) MUST have a
Kubernetes ServiceAccount that is **strictly read-only**. This is the
non-negotiable security boundary for zero-code MCP onboarding.

### What it proves

The audit script enumerates the ServiceAccount's EFFECTIVE permissions via
`kubectl auth can-i --list --as=system:serviceaccount:<ns>:<sa>` and:

- **PASSES** if all granted rules use only `get`, `list`, `watch` verbs.
- **FAILS** (exit 1) if ANY rule grants `create`, `update`, `patch`, `delete`,
  `deletecollection`, or the wildcard verb `*`.
- A wildcard **resource** (`*`) with only read verbs is OK; a wildcard **verb**
  is always a FAIL.

### Running the gate

```bash
# Single ServiceAccount
make mcp-rbac-audit SA=kube-mcp NS=mcp-servers

# With explicit context
make mcp-rbac-audit SA=kube-mcp NS=mcp-servers CTX=bdc-workloads-dev-nv

# Direct invocation
python3 scripts/mcp_rbac_audit.py \
  --serviceaccount kube-mcp \
  --namespace mcp-servers \
  --context bdc-workloads-dev-nv
```

### CI integration

The gate runs in CI where cluster credentials exist (IRSA / kubeconfig from
GitLab CI variables). Add to your pipeline:

```yaml
rbac-audit:
  stage: pre-build
  script:
    - make mcp-rbac-audit SA=$MCP_SA NS=$MCP_NS CTX=$KUBE_CONTEXT
  rules:
    - changes:
        - agents/*/agent.yaml
        - infra/terraform/mcp-*/**
```

### Onboarding a new MCP datasource — checklist

1. **Create the ServiceAccount** with a Role/ClusterRole binding that grants
   ONLY `get`, `list`, `watch` on the resources the MCP server needs.
2. **Run the audit gate** and confirm PASS:
   ```bash
   make mcp-rbac-audit SA=<new-sa> NS=<ns>
   ```
3. **Add the datasource** to the agent's `agent.yaml`:
   ```yaml
   datasources:
     - type: mcp
       name: <name>
       url: ${MY_MCP_URL}
       tools: [<read-only-tools-only>]
   ```
4. **The gate MUST pass before the MR is merged.** No exceptions — a mutating
   SA breaks the read-only invariant that makes zero-code onboarding safe.

### Output example

```
======================================================================
MCP ServiceAccount RBAC Audit
======================================================================
  ServiceAccount: kube-mcp
  Namespace:      mcp-servers
  Parse format:   json
  Total rules:    5
----------------------------------------------------------------------
  [✅ OK  ] Rule 1: pods → [get, list, watch]
  [✅ OK  ] Rule 2: services → [get, list, watch]
  [✅ OK  ] Rule 3: events → [get, list, watch]
  [✅ OK  ] Rule 4: nodes → [get, list, watch]
  [✅ OK  ] Rule 5: (non-resource) [/healthz, /version] → [get]
----------------------------------------------------------------------

✅ PASS: All 5 rules are read-only (get/list/watch).
The ServiceAccount meets the read-only requirement for MCP onboarding.
======================================================================
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | PASS — all permissions are read-only |
| 1 | FAIL — mutating verbs detected |
| 2 | ERROR — kubectl not found, SA not found, timeout, etc. |
