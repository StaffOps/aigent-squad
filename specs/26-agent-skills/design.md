# Design: Agent Skills (lazy-loaded knowledge)

## Architecture

```
skills/                              ← global, shared across agents
└── <skill-name>/
    └── SKILL.md                     ← YAML frontmatter + markdown body

agents/<agent>/agent.yaml
└── skills: [<skill-name>, ...]      ← per-agent allowlist

Flow (lazy, in process_request):
  query ──▶ SkillRegistry.select(allowlist, query)
              │  (keyword match against frontmatter)
              ▼
        relevant skills ──▶ inject <skills> into the system prompt ──▶ Bedrock
```

## Components

| Component | Responsibility |
|-----------|----------------|
| `SKILL.md` | Knowledge + frontmatter (`name`, `description`, `keywords`) |
| `SkillRegistry` | Discovers/parses skills from `skills/` at startup (once) |
| `AgentConfig.skills` | Allowlist: which skills the agent may use |
| `GenericAgent` | In `process_request`: select (lazy) + inject into the prompt |

## SKILL.md format

```markdown
---
name: oomkill-investigation
description: How to investigate OOMKilled pods
keywords: [oomkill, oom, memory, killed, evicted, restart]
---

# Investigating OOMKilled Pods
... knowledge ...
```

## Rationale (decisions)

### Decision 1: A skill is static knowledge, not a tool

**Choice**: a skill injects text into the prompt; it executes nothing.

**Justification, in order of strength**:
1. Keeps the read-only/consultative invariant (ADR-001). Skill-as-tool would
   reintroduce the tool-loop we decided NOT to adopt.
2. Action/capability already has a mechanism: `datasources`/adapters (boto3,
   mcp, etc). Creating a second path to "do things" would be redundant and confusing.
3. Static knowledge is trivially testable and cacheable.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| A skill cannot "fetch" new data | That's what adapter/RAG is for — clear separation of roles |

**When it would be wrong**: if skills need dynamic parameters or to call APIs —
then it becomes an adapter, not a skill.

### Decision 2: Lazy selection by keyword match (not eager, not embeddings)

**Choice**: a skill enters the prompt only when the query matches `keywords`;
simple keyword match (not semantic/embeddings) in v1.

**Justification, in order of strength**:
1. **Token economy**: the explicit request was lazy. Injecting all skills always
   would inflate the prompt (cost per query) — exactly what to avoid.
2. Keyword match is deterministic, no inference cost, no new dependency.
   Embeddings would add latency + the `kb/` stack for an uncertain gain in v1.
3. Reuses the classifier's existing `routing_keywords` pattern — same mental
   model for the operator.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Keyword match misses unlisted synonyms | Operator curates `keywords`; cheap to adjust. Semantic match is a Phase 2 promotion trigger |
| A query with no known keyword brings no skill | Acceptable: fail-open, the agent answers without the skill (as today) |

**When it would be wrong** (Phase 2 signal): if the rate of "relevant skill not
injected due to a missing keyword" is high in real use → migrate to embeddings
(reusing `kb/embedder.py`).

**Known limitation (validated in implementation)**: the match is by **exact
token**, so inflections are not covered automatically (`oomkill` does not match
`oomkilled`). The operator should list the relevant forms in `keywords` (e.g.
`[oomkill, oomkilled, oom]`). This is cheap to adjust and keeps the match
deterministic; stemming/embeddings are left for Phase 2 if manual curation
proves insufficient.

### Decision 3: Global skills + per-agent allowlist

**Choice**: files in a global `skills/`; each `agent.yaml` lists which it may use.

**Justification**:
1. Reuse (explicit request): one `oomkill.md` serves kubernetes + observability
   without duplication.
2. Per-agent allowlist avoids polluting an agent with another domain's skill
   (finops doesn't need TraceQL).

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Two sources (global skill + local allowlist) | Identical pattern to datasources; operator already knows it |

## Invariants

- A skill never executes code — it only injects text.
- Skill content enters as DATA in the prompt (inside a tag), under the same
  prompt-injection defense as `<infra_data>`.
- Fail-open: a missing/invalid skill → warning, proceed without it.

## Integration with the current flow

`SkillRegistry` loads at startup (alongside `AgentRegistry`).
`GenericAgent.process_request` gains a step before building the context: it
selects allowlisted skills that match the query and concatenates them to the
`system_prompt`. Zero change to `bedrock.py`.
