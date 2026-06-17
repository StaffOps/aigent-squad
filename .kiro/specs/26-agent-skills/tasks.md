# Tasks: Agent Skills

- [x] Task 1: `Skill` model + `SkillRegistry` (discover/parse `skills/<name>/SKILL.md` with YAML frontmatter)
- [x] Task 2: Add `skills: list[str]` allowlist to `AgentConfig`
- [x] Task 3: Lazy selection (keyword match) + inject `<skills>` block in `GenericAgent.process_request`
- [x] Task 4: Wire `SkillRegistry` into supervisor startup; pass to `GenericAgent`
- [x] Task 5: Tests ≥90% (`tests/test_skills.py`, 19 tests, 100% coverage on skills.py; + 3 injection tests in test_generic_agent, CI-only due to otel_helper import)
- [x] Task 6: Example skill `skills/oomkill-investigation/` + docs (HOW-TO-NEW-AGENT) + wired into kubernetes agent

## Notes / known limitations
- Keyword match is exact-token: inflections aren't auto-covered (`oomkill` ≠
  `oomkilled`). Operator lists forms in `keywords`. Semantic match = Phase 2.
- Injection tests in test_generic_agent run only in CI (local env lacks the
  git+ssh `otel_helper` dep); all files byte-compile locally.
