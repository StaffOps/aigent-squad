---
spec: 22-agent-capability-manifest
status: done
completed: null
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Config-Driven Agent Platform

**Spec**: `22-agent-capability-manifest`
**Severity**: 🟠 High (architectural redesign — enables customizable product)
**Supersedes**: spec 19 (`config-driven-platform`)
**Product vision**: 1 generic image + N agents defined by directory (YAML + prompt.md). The deployer chooses how many and which agents they want via Helm values, without writing code.

---

## Concept

An agent **is not code** — it is a **configuration directory**:

```
agents/
├── aws/
│   ├── agent.yaml      # datasources, capabilities, cache, model
│   └── prompt.md       # system prompt (can be long)
├── finops/
│   ├── agent.yaml
│   ├── prompt.md
│   └── examples/       # few-shot, RAG docs, references
│       └── cost-patterns.md
└── custom-team-x/
    ├── agent.yaml
    └── prompt.md
```

The generic image **auto-discovers** the directory at startup: each subdir with `agent.yaml` becomes a functional agent.

---

## User Stories

WHEN the operator points `AGENTS_DIR` to a directory THEN the system SHALL discover all subdirs with `agent.yaml` and register one agent per each.

WHEN the operator adds a new subdir with `agent.yaml` + `prompt.md` and restarts THEN the system SHALL make the new agent available to the classifier **with no code change or image rebuild**.

WHEN the Helm chart is deployed with `agents[]` in values.yaml THEN each entry SHALL generate a Deployment + Service + ConfigMap with the agent config.

WHEN the `agent.yaml` declares `datasources` THEN the runtime SHALL instantiate the corresponding adapters and inject them into the agent.

WHEN the `prompt.md` exceeds 100 lines THEN it SHALL be isolated in a file (not inline in the YAML), avoiding pollution.

WHEN the classifier routes a query THEN it SHALL use `name` + `description` + `capabilities` + `routing_keywords` from the manifests (not a hardcoded list).

WHEN the `agent.yaml` is invalid (missing required field, unknown datasource type) THEN the system SHALL fail at **startup** with an actionable error.

WHEN two agents declare the same capability THEN the classifier SHALL break the tie by `routing_keywords` and `domain`.

WHEN `read_only: true` THEN the system SHALL preserve that invariant in all paths.

WHEN `enabled: false` THEN the agent SHALL be ignored in discovery.

---

## Acceptance Criteria

- [ ] 1 generic Docker image that runs any agent based on config.
- [ ] Auto-discovery of `AGENTS_DIR/<name>/agent.yaml` at startup.
- [ ] Pydantic schema for `agent.yaml` with strict validation.
- [ ] Registry of datasource adapters: `boto3`, `kubernetes`, `http`, `prometheus`, `athena`, `gitlab`.
- [ ] `prompt.md` loaded from the same directory as `agent.yaml`.
- [ ] Classifier consumes the registry (agent list is dynamic, not hardcoded).
- [ ] Supervisor/coordinator discovers agents from the registry (not by fixed URLs).
- [ ] Helm chart generates N deployments from `agents[]` in values.
- [ ] Working example: 5 current agents migrated to config format + 1 new agent demonstrating extensibility.
- [ ] Startup failure with invalid config (schema, unknown datasource, missing env).
- [ ] `read_only` honored as a security invariant.
- [ ] Tests ≥90%: discovery, selection by capability, startup failure, adapter instantiation.

---

## Out of scope

- Hot-reload without restart (future — restart is acceptable for MVP).
- Automatic git-sync in the cluster (initContainer is sufficient for now).
- Marketplace/versioning of manifests.
- Datasource adapters beyond the 6 listed (pluggable via interface, but not implemented now).
- Multi-tenant (each tenant with different agents) — future.

---

## Directory source (deploy-time, not runtime)

| Source | How to mount | When to use |
|--------|-------------|-------------|
| Local (dev) | `docker run -v ./agents:/config/agents` | Local development |
| ConfigMap | Helm generates ConfigMap per agent, mounts as volume | Simple deploy, everything in Helm |
| Git repo | initContainer + git clone + shared volume | Production, GitOps-native |
| S3/bucket | initContainer downloads .tar.gz | Agents updatable without redeploy |

The platform only knows how to read from a directory. The **source** is deploy configuration (Helm values), not code.
