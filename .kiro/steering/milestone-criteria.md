# Milestone Completion Criteria

Every milestone (spec, feature, or significant change) MUST meet ALL of these before commit.

## CRITICAL: No commit without validation

```
1. Tests written by a DIFFERENT agent (verification independence)
2. Coverage ≥ 80% (enforced via --cov-fail-under=80)
3. Tests passing (all green)
4. NEW METRICS instrumented + documented in docs/METRICS.md
5. Documentation updated (CHANGES.md, ROADMAP.md, spec tasks.md, relevant docs/)
6. Only then: commit
```

## Details

### Tests (verification independence)
- Test-author ≠ implementation-author (separate subagent session)
- Tests target the CONTRACT/SPEC, not the implementation details
- Priority: branches, error handling, edge cases, public API
- Use pytest + pytest-asyncio; run via Docker

### Coverage
- Minimum: 80% line coverage
- Configured in `.coveragerc` with `fail_under = 80`
- Entry-point thin wrappers (server.py with only FastAPI app init) excluded from coverage
- `pytest --cov --cov-fail-under=80` must pass

### Metrics (equal weight to docs)
- ANY new feature/component MUST emit at least one custom metric (counter, histogram, or gauge) covering: invocations, errors, duration, or domain-specific events
- All metrics MUST be:
  - Defined in `src/core/metrics.py` (centralized)
  - Documented in `docs/METRICS.md` (name, type, labels, description, cardinality bound)
  - Named `aigent.<domain>.<name>` (e.g., `aigent.kb.rag.hits`)
  - BOUNDED cardinality only — no `user_id`, `trace_id`, raw error messages, or unbounded paths in labels

### Documentation
- `CHANGES.md`: entry describing what changed
- `.kiro/specs/ROADMAP.md`: mark completed specs
- `.kiro/specs/<spec>/tasks.md`: **mark each task `[x]` with completion date**; explicitly mark deferred tasks as `NOT IMPLEMENTED` or `deferred to Phase X`
- `docs/`: update relevant docs if behavior/API changed (METRICS.md, SECURITY.md, KNOWLEDGE-BASE.md, etc)

### Pre-commit checklist
Before running `git commit`, verify:
- [ ] Tests pass and coverage ≥ 80%
- [ ] New code emits metrics (instrumented + defined in metrics.py)
- [ ] `docs/METRICS.md` lists the new metrics
- [ ] Spec `tasks.md` reflects what was done vs deferred (with dates)
- [ ] `ROADMAP.md` aligned with reality
- [ ] `CHANGES.md` entry written

## Anti-patterns
- ❌ "Commit now, tests later"
- ❌ Same agent writes code AND tests
- ❌ Coverage below 80% accepted as "good enough"
- ❌ "Closes spec X" in commit message but `tasks.md` still has `[ ]` everywhere
- ❌ New code with zero metrics ("can't measure what isn't observed")
- ❌ New metrics without `docs/METRICS.md` entry
- ❌ High-cardinality labels (user_id, trace_id, error message text)
- ❌ Saying a spec is "done" when only Phase 1 is done — be explicit about what's deferred
