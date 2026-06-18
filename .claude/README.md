# `.claude/` — Claude Code integration

This directory makes the project work with **Claude Code**. Most of it is
**generated** from the single source of truth — do not edit generated parts by hand.

| Path | Origin | Editable? |
|------|--------|-----------|
| `rules/` | symlink → `.kiro/steering/` | No — edit `.kiro/steering/` |
| `skills/` | symlink → `skills/` | No — edit `skills/` (SKILL.md format is shared) |
| `agents/*.md` | generated from `agents/<name>/` (yaml + prompt) | No — edit `agents/<name>/`, then re-sync |
| `settings.json` | hand-maintained (no `.kiro/` equivalent) | Yes |
| `../CLAUDE.md` | hand-maintained entrypoint; imports `.kiro/steering/*` | Yes |

## Regenerate after changing steering, skills, or agents

```bash
./scripts/sync-claude.sh
```

## Why this layout

`.kiro/` is the project's source of truth (Kiro CLI). Claude Code reads `CLAUDE.md`,
`.claude/rules/`, `.claude/skills/`, and `.claude/agents/`. Where the formats are
identical (steering↔rules, skills↔skills) we **symlink** to avoid drift; where they
differ (config-driven `agent.yaml`+`prompt.md` vs Claude subagent `.md`) we
**convert**. `CLAUDE.md` additionally `@import`s the steering files so the rules are
loaded into context the same way Kiro loads them.
