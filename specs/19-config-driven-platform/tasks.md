# Tasks: Config-Driven Platform

> Can start early (no dependency). Enables specs 17 (max_agents), 18 (datasources/limits), 11 (model by role).

- [ ] T1: Define schema `AppConfig` (Pydantic) — sections `bedrock.models`, `agents[]`, `datasources`, `limits`, `storage`, `cache`
- [ ] T2: `load_config()` with sources env>file>default (`pydantic-settings` v2 + `YamlConfigSettingsSource`, nested delimiter `__`) (depends on: T1)
- [ ] T3: Secret validation — reject inline secret value in the YAML; support env and `*_FILE` (depends on: T1)
- [ ] T4: `AgentRegistry` derived from `AppConfig.agents` (lookup + only `enabled`); remove `AGENT_URLS` from supervisor (depends on: T2)
- [ ] T5: Migrate datasources — `PROMETHEUS_URL` and the like → `config.datasources`; remove hardcoding from the observability agent (depends on: T2)
- [ ] T6: Migrate `mcp_servers`, GitLab groups ("Company"), Athena, docs portal → config (no hardcoding) (depends on: T2)
- [ ] T7: Bedrock model by role (`classifier`/`agent`/`synthesis`) read from config (aligns specs 11/17) (depends on: T2)
- [ ] T8: `limits` (`max_agents`, timeouts, `investigation_evidence_cap`) read from config (depends on: T2)
- [ ] T9: Startup failure with invalid config — error message per missing/invalid key (depends on: T2, T3)
- [ ] T10: `config/aigent.example.yaml` + `.env.example` consistent + docs (README config section) (depends on: T1–T8)
- [ ] T11 (test-author DIFFERENT from author): pytest ≥90% — precedence env>file>default, inline secret rejection, startup failure, enabled-only registry, `*_FILE` (depends on: T9)
- [ ] T12: Independent review (`code-review`): zero remaining hardcoding, secrets outside YAML, correct precedence (depends on: T11)

## Suggested order
T1→T2→T3; T4/T5/T6/T7/T8 in parallel (after T2); T9; T10; T11→T12.

## Notes
- Precedence env>file>default is an invariant — test explicitly.
- Secret in versioned YAML = failure (fail-closed).
- This spec does NOT do hot-reload or remote config (out of scope).
- Verification pipeline (`verification-independence.md`): T1–T10 author; T11 test-author in a different session; T12 code-review.
- Per `documentation-sync`: update the README with the configuration section in the same change.
