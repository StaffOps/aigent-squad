# Agents Overview

Agents are the specialists. Each agent is responsible for one domain, collects read-only context from its configured datasources, and uses Claude on Bedrock to synthesize a structured answer.

## Built-in agents

| Agent | Domain | Datasources |
|-------|--------|-------------|
| `aws` | EC2, RDS, S3, IAM | `boto3` (describe/list/get) |
| `kubernetes` | Pods, namespaces, nodes, Helm, Istio mesh (via Kiali) | `mcp` (kube-mcp, full read-only tool catalog) |
| `finops` | Cost analysis (Cost Explorer spend only, no per-namespace/Kubecost breakdown) | `boto3` (Cost Explorer) |
| `devops` | CI/CD, GitLab, pipelines, deployments | `http` (GitLab API, docs portal) |
| `observability` | Target-up status only today (routing keywords cover more than the datasource does — see `evals/golden/observability.yaml`) | `http` (single Prometheus `up` query) |

Datasources above reflect what each agent can ACTUALLY answer today, not
the aspirational domain description — see `evals/golden/<agent>.yaml` for
the per-agent capability probes this is checked against (spec 35).

## Routing

The supervisor classifier decides which agent to invoke:

1. **Keyword fast-path** — if the query contains an agent's `routing_keywords`, route immediately (no LLM call)
2. **LLM classification** — for ambiguous queries, a lightweight Bedrock call picks the best agent
3. **Fan-out** — if the query spans multiple domains, N agents run in parallel and results are synthesized

You can also force a specific agent by passing `agent_id` in the request.

## Agent structure

```
agents/
└── aws/
    ├── agent.yaml    ← config (datasources, keywords, model tier, skills)
    └── prompt.md     ← system prompt
```

Every agent is a `GenericAgent` instance loaded from config. No Python code needed to create a new one.

## Read-only guarantee

All agents enforce read-only at four layers — no agent can mutate infrastructure:

1. **System prompt** — explicit read-only instructions in every `prompt.md`
2. **Adapter code** — datasource adapters only implement read operations
3. **IRSA policy** — IAM role has explicit Deny on all write actions (production)
4. **Kubernetes RBAC** — ServiceAccount restricted to `get`/`list`/`watch` (production)

See [Read-Only Policy](read-only-policy.md) for details.

## Adding a new agent

[How to Create a New Agent →](how-to-new-agent.md){ .md-button .md-button--primary }
