#!/usr/bin/env bash
# Regenerate the .claude/ directory from the single source of truth.
#
# Source of truth:
#   .kiro/steering/     → .claude/rules/   (symlink — markdown is identical)
#   skills/             → .claude/skills/  (symlink — SKILL.md format is identical)
#   agents/<name>/      → .claude/agents/<name>.md  (CONVERTED: yaml + prompt → md)
#
# .claude/settings.json and CLAUDE.md are maintained by hand (no equivalent in
# .kiro/). Run this after changing steering, skills, or agent definitions.
#
# Usage: ./scripts/sync-claude.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Syncing .claude/ from .kiro/ + agents/ ..."

mkdir -p .claude/agents

# 1) Symlinks for format-identical content (relative, portable)
ln -snf ../.kiro/steering .claude/rules
ln -snf ../skills .claude/skills
echo "  symlinked .claude/rules  -> .kiro/steering"
echo "  symlinked .claude/skills -> skills"

# 2) Convert config-driven agents (agent.yaml + prompt.md) → Claude subagent .md
docker run --rm -v "$ROOT:/app" -w /app python:3.11-slim sh -c \
  "pip install pyyaml -q >/dev/null 2>&1 && python3 - <<'PY'
import pathlib, yaml
out = pathlib.Path('.claude/agents')
out.mkdir(parents=True, exist_ok=True)
# Clear stale generated files
for f in out.glob('*.md'):
    f.unlink()
n = 0
for d in sorted(pathlib.Path('agents').iterdir()):
    ay, pm = d / 'agent.yaml', d / 'prompt.md'
    if not (d.is_dir() and ay.exists() and pm.exists()):
        continue
    cfg = yaml.safe_load(ay.read_text()) or {}
    name = cfg.get('name', d.name)
    desc = (cfg.get('description') or '').strip().replace(chr(10), ' ')
    prompt = pm.read_text().strip()
    (out / f'{name}.md').write_text(
        f'---\nname: {name}\ndescription: {desc}\n---\n\n'
        f'<!-- GENERATED from agents/{d.name}/. Edit the source and re-run scripts/sync-claude.sh. -->\n\n'
        f'{prompt}\n'
    )
    n += 1
    print(f'  converted agents/{d.name}/ -> .claude/agents/{name}.md')
print(f'==> Done. {n} subagent(s) generated.')
PY"

# 3) Fix ownership (Docker writes as root)
docker run --rm -v "$ROOT:/app" -w /app python:3.11-slim \
  chown -R "$(id -u):$(id -g)" .claude/agents 2>/dev/null || true

echo "==> .claude/ is in sync. (settings.json and CLAUDE.md are hand-maintained.)"
