"""Tests for src/core/skills.py (spec 26)."""
import pytest

from src.core.skills import Skill, SkillRegistry, parse_skill


# --- parse_skill ---


def test_parse_skill_with_frontmatter():
    text = """---
name: oomkill
description: How to investigate OOMKilled pods
keywords: [oomkill, oom, memory]
---

# OOMKill
Body content here.
"""
    skill = parse_skill(text, fallback_name="dir-name")
    assert skill.name == "oomkill"
    assert skill.description == "How to investigate OOMKilled pods"
    assert skill.keywords == ["oomkill", "oom", "memory"]
    assert "Body content here." in skill.body
    assert "---" not in skill.body


def test_parse_skill_without_frontmatter_uses_fallback_name():
    skill = parse_skill("# Just a body\nno frontmatter", fallback_name="my-skill")
    assert skill.name == "my-skill"
    assert skill.keywords == []
    assert "Just a body" in skill.body


def test_parse_skill_single_string_keyword_normalized_to_list():
    text = "---\nname: x\nkeywords: solo\n---\nbody"
    skill = parse_skill(text, fallback_name="x")
    assert skill.keywords == ["solo"]


def test_parse_skill_malformed_frontmatter_meta_not_dict():
    # frontmatter parses to a list, not a dict -> treated as empty meta
    text = "---\n- a\n- b\n---\nbody"
    skill = parse_skill(text, fallback_name="fb")
    assert skill.name == "fb"
    assert skill.body == "body"


# --- Skill.matches (token-aware) ---


def test_matches_single_word_on_boundary():
    skill = Skill(name="s", keywords=["oom"])
    assert skill.matches("pod was OOM killed") is True
    # must not match inside another word
    assert skill.matches("there is room here") is False


def test_matches_multiword_substring():
    skill = Skill(name="s", keywords=["memory leak"])
    assert skill.matches("investigating a memory leak today") is True
    assert skill.matches("memory is fine") is False


def test_matches_empty_keywords_never_matches():
    assert Skill(name="s", keywords=[]).matches("anything") is False


def test_matches_is_case_insensitive():
    skill = Skill(name="s", keywords=["TraceQL"])
    assert skill.matches("how do I write a traceql query") is True


def test_matches_skips_blank_keyword():
    # a blank/whitespace keyword is ignored, real one still matches
    skill = Skill(name="s", keywords=["  ", "oom"])
    assert skill.matches("oom event") is True
    assert skill.matches("nothing here") is False


# --- SkillRegistry.discover ---


def _write_skill(dirpath, name, body="body", frontmatter=True, keywords="[oom]"):
    d = dirpath / name
    d.mkdir()
    if frontmatter:
        content = f"---\nname: {name}\nkeywords: {keywords}\n---\n{body}"
    else:
        content = body
    (d / "SKILL.md").write_text(content)
    return d


def test_discover_loads_skills(tmp_path):
    _write_skill(tmp_path, "oomkill")
    _write_skill(tmp_path, "traceql", keywords="[traceql]")
    reg = SkillRegistry(skills_dir=str(tmp_path))
    reg.discover()
    assert set(reg.skills.keys()) == {"oomkill", "traceql"}


def test_discover_missing_dir_is_fail_open(tmp_path):
    reg = SkillRegistry(skills_dir=str(tmp_path / "does-not-exist"))
    reg.discover()  # must not raise
    assert reg.skills == {}


def test_discover_ignores_dir_without_skill_md(tmp_path):
    (tmp_path / "empty-dir").mkdir()
    reg = SkillRegistry(skills_dir=str(tmp_path))
    reg.discover()
    assert reg.skills == {}


def test_discover_one_bad_skill_does_not_break_others(tmp_path, monkeypatch):
    _write_skill(tmp_path, "good")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("ok")

    # Force read_text to blow up only for the bad skill
    import pathlib
    real_read = pathlib.Path.read_text

    def flaky(self, *a, **k):
        if str(self).endswith("bad/SKILL.md"):
            raise IOError("boom")
        return real_read(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", flaky)
    reg = SkillRegistry(skills_dir=str(tmp_path))
    reg.discover()
    assert "good" in reg.skills
    assert "bad" not in reg.skills


# --- SkillRegistry.select (lazy) ---


def test_select_returns_only_matching_allowlisted(tmp_path):
    _write_skill(tmp_path, "oomkill", keywords="[oom, memory]")
    _write_skill(tmp_path, "traceql", keywords="[traceql, trace]")
    reg = SkillRegistry(skills_dir=str(tmp_path))
    reg.discover()

    hit = reg.select(["oomkill", "traceql"], "pod got OOM killed")
    assert [s.name for s in hit] == ["oomkill"]


def test_select_no_match_returns_empty(tmp_path):
    _write_skill(tmp_path, "oomkill", keywords="[oom]")
    reg = SkillRegistry(skills_dir=str(tmp_path))
    reg.discover()
    assert reg.select(["oomkill"], "what is the weather") == []


def test_select_unknown_skill_name_ignored(tmp_path):
    _write_skill(tmp_path, "oomkill", keywords="[oom]")
    reg = SkillRegistry(skills_dir=str(tmp_path))
    reg.discover()
    # 'ghost' not loaded -> ignored, no crash
    assert reg.select(["ghost"], "oom") == []


def test_select_respects_allowlist(tmp_path):
    _write_skill(tmp_path, "oomkill", keywords="[oom]")
    reg = SkillRegistry(skills_dir=str(tmp_path))
    reg.discover()
    # query matches oomkill, but it's not in this agent's allowlist
    assert reg.select([], "oom killed") == []


# --- SkillRegistry.render ---


def test_render_empty_is_empty_string():
    assert SkillRegistry.render([]) == ""


def test_render_formats_blocks():
    skills = [Skill(name="a", body="AAA"), Skill(name="b", body="BBB")]
    out = SkillRegistry.render(skills)
    assert "## Skill: a" in out
    assert "AAA" in out
    assert "## Skill: b" in out
    assert "BBB" in out
