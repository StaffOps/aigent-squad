---
spec: 26-agent-skills
status: done
completed: null
superseded_by: null
depends_on: ["02-unify-agent-architecture"]
deferred: []
---

# Feature: Agent Skills (lazy-loaded knowledge)

## Context

Today, an agent's specialized knowledge lives **inline in `prompt.md`** (the
kubernetes one is already ~8KB). That doesn't scale and doesn't allow **reuse
across agents** (an "investigate OOMKill" guide serves kubernetes AND observability).

"Skill" here = **on-demand markdown knowledge** (the `staffops_agent_definition`
SKILL.md sense), NOT an action/tool (those are the `datasources`/adapters) nor
dynamic RAG (that's the `kb/` module).

## User Stories

WHEN an agent processes a query whose topic matches an available skill
THEN the system SHALL inject that skill's content into the system prompt before
calling Bedrock.

WHEN no skill matches the query
THEN the system SHALL NOT inject any skill (lazy — saves tokens).

WHEN a skill is referenced by multiple agents
THEN it SHALL live in a shared global directory (`skills/`), not duplicated per
agent.

WHEN a referenced skill file does not exist or is corrupted
THEN loading SHALL degrade gracefully (fail-open: ignore the skill, log a
warning, don't take the agent down).

## Acceptance Criteria

- [ ] Global directory `skills/<name>/SKILL.md` with YAML frontmatter
      (`name`, `description`, `keywords`).
- [ ] `skills: [<name>, ...]` field in `agent.yaml` declares which skills the
      agent MAY use (per-agent allowlist).
- [ ] **Lazy** selection: a skill only enters the prompt when the query matches
      its `keywords` (or description). No match ⇒ no injection.
- [ ] Skills injected in a `<skills>` block of the system prompt, treated as
      knowledge (not executable instruction — preserves the existing prompt
      injection defense).
- [ ] Fail-open: a missing/invalid skill does not break the agent.
- [ ] ≥90% test coverage on new code.

## Out of scope

- Tool-loop / a skill that executes code (that's datasource/adapter — see ADR-001).
- Dynamic RAG (already covered by `src/core/kb/`).
- Skill with embeddings/semantic match (Phase 2 — start with keyword match).
- A skill-management UI.
