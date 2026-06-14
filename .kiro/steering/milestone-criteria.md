# Milestone Completion Criteria

Every milestone (spec, feature, or significant change) MUST meet ALL of these before commit.

## CRITICAL: No commit without validation

```
1. Tests written by a DIFFERENT agent (verification independence)
2. Coverage ≥ 80% (enforced via --cov-fail-under=80)
3. Tests passing (all green)
4. Documentation updated (CHANGES.md, ROADMAP.md, relevant docs/)
5. Only then: commit
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

### Documentation
- `CHANGES.md`: entry describing what changed
- `.kiro/specs/ROADMAP.md`: mark completed specs
- `docs/`: update relevant docs if behavior/API changed (METRICS.md, SECURITY.md, etc)
- Spec `tasks.md`: mark tasks as done with completion date

### Metrics documentation
- Any new metric created MUST be documented in `docs/METRICS.md`
- Include: name, type, labels, description, cardinality

## Anti-patterns
- ❌ "Commit now, tests later"
- ❌ Same agent writes code AND tests
- ❌ Coverage below 80% accepted as "good enough"
- ❌ Docs not updated (especially CHANGES.md)
- ❌ New metrics without METRICS.md entry
