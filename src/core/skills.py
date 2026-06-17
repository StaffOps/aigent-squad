"""Agent skills — lazy-loaded markdown knowledge (spec 26).

A skill is on-demand knowledge (a `SKILL.md` with YAML frontmatter), NOT a
tool/action (those are datasources/adapters) and NOT dynamic RAG (that is
src/core/kb/). Skills live in a global `skills/` directory and are referenced
per-agent via an allowlist in agent.yaml. Selection is lazy: a skill is only
injected into the prompt when the query matches its keywords.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.core.logger import logger

_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class Skill:
    """A unit of on-demand knowledge parsed from SKILL.md."""

    name: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    body: str = ""

    def matches(self, query: str) -> bool:
        """Keyword match: True if any keyword appears in the query (token-aware).

        Single-word keywords match on word boundaries (avoids 'oom' matching
        'room'); multi-word keywords match as a substring.
        """
        if not self.keywords:
            return False
        q = query.lower()
        q_tokens = set(_WORD_RE.findall(q))
        for kw in self.keywords:
            k = kw.lower().strip()
            if not k:
                continue
            if " " in k:
                if k in q:
                    return True
            elif k in q_tokens:
                return True
        return False


def parse_skill(text: str, fallback_name: str) -> Skill:
    """Parse SKILL.md text (frontmatter + body) into a Skill.

    Frontmatter is optional; if absent, the whole text is the body and the
    fallback name (directory name) is used.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return Skill(name=fallback_name, body=text.strip())

    raw_meta, body = m.group(1), m.group(2)
    meta = yaml.safe_load(raw_meta) or {}
    if not isinstance(meta, dict):
        meta = {}

    keywords = meta.get("keywords", []) or []
    if isinstance(keywords, str):
        keywords = [keywords]

    return Skill(
        name=str(meta.get("name") or fallback_name),
        description=str(meta.get("description") or ""),
        keywords=[str(k) for k in keywords],
        body=body.strip(),
    )


class SkillRegistry:
    """Discovers and parses skills from a global skills directory (once)."""

    def __init__(self, skills_dir: str | None = None):
        self.skills_dir = Path(skills_dir or os.getenv("SKILLS_DIR", "skills"))
        self.skills: dict[str, Skill] = {}

    def discover(self) -> None:
        """Scan skills_dir; each subdir with SKILL.md = 1 skill. Fail-open."""
        if not self.skills_dir.exists():
            logger.info(f"Skills dir not found, skipping: {self.skills_dir}")
            return

        for skill_dir in sorted(self.skills_dir.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if not skill_dir.is_dir() or not skill_file.exists():
                continue
            try:
                skill = parse_skill(skill_file.read_text(), fallback_name=skill_dir.name)
                self.skills[skill.name] = skill
                logger.info(f"Loaded skill: {skill.name} ({len(skill.keywords)} keywords)")
            except Exception as e:  # one bad skill must not break the rest
                logger.warning(f"Failed to load skill '{skill_dir.name}' (fail-open)", extra={"error": str(e)})

    def select(self, allowed: list[str], query: str) -> list[Skill]:
        """Lazy selection: skills in the agent's allowlist that match the query."""
        selected: list[Skill] = []
        for name in allowed:
            skill = self.skills.get(name)
            if skill is None:
                logger.warning(f"Agent references unknown skill '{name}' (ignored)")
                continue
            if skill.matches(query):
                selected.append(skill)
        return selected

    @staticmethod
    def render(skills: list[Skill]) -> str:
        """Render selected skills into a prompt block (empty string if none)."""
        if not skills:
            return ""
        parts = [f"## Skill: {s.name}\n{s.body}" for s in skills]
        return "\n\n".join(parts)
