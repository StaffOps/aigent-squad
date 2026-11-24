# Design: Portable Deployment Profiles

**Spec**: `46-portable-deployment-profiles` · see `requirements.md`

---

## Architecture

```
                 HISTORY_BACKEND (env, default redis)
                          │
   SupervisorAgent ──► HistoryStore (Protocol)
                          ├── DynamoDBHistoryStore   (current ChatStorage, unchanged behavior)
                          ├── RedisHistoryStore      (sorted set per user#session#agent, EXPIRE 24h)
                          └── PostgresHistoryStore   (plain table, expires_at, bounded purge)

   agents/<name>/agent.yaml ──► AgentRegistry
        url: ${K8S_MCP_URL}         │ 1. yaml.safe_load
                                    │ 2. expand ${VAR} in allowed datasource fields only
                                    │ 3. unset var → datasource disabled (+WARNING, /ready detail)
                                    │ 4. apply AGENT_<NAME>_ENABLED override
                                    │ 5. AgentConfig(**raw)  (allowlist untouched)
                                    ▼
                               registered agents

   Chart values-<profile>.yaml
        agentOverrides:
          aws:        {enabled: false}            → AGENT_AWS_ENABLED=false
          kubernetes: {env: {K8S_MCP_URL: …}}     → env var on supervisor
```

## Decisions

1. **History is key/value, not vector.** The access pattern is "append message" and "last N for
   user#session (optionally per agent)", with 24 h expiry. pgvector adds nothing; the KB (spec 21)
   keeps owning pgvector. *Trade-off*: none — keeps the two concerns separate.

2. **Redis is the recommended off-AWS backend.** It is already a hard dependency (cache,
   rate/budget, jobs), TTL is native, and a sorted set scored by timestamp gives ordered last-N reads.
   *Trade-off*: history durability equals Redis durability (in-cluster chart Redis has AOF on a PVC;
   a managed Redis without persistence loses history on restart). Acceptable: history is 24 h and
   already fail-open.

3. **Postgres is the durable/auditable option, opt-in.** Plain table
   `(pk text, sk text, payload jsonb, expires_at timestamptz)`, PK `(pk, sk)`, index on
   `expires_at`; expired rows are excluded on read and purged in bounded `DELETE … LIMIT`-style
   batches — on every write, up to 10 batches once the store connects, and one batch on read at most
   every 5 minutes (F-025: a read-only or restarted replica still cleans up) — so no cron is
   required. DDL runs only when the table is missing, so a DML-only runtime role works once a
   migration role has created it; TLS via `POSTGRES_SSLMODE`. *Trade-off*: Postgres becomes required
   when selected (today it is optional for the KB).

4. **Redis is the default; DynamoDB is opt-in for AWS** (user decision 2026-11-24). `HISTORY_BACKEND`
   unset = `redis`, which works in every environment because Redis is already required. The AWS
   deployment keeps DynamoDB by setting `HISTORY_BACKEND=dynamodb` explicitly in `values-aws.yaml`
   and the AWS overlay; the current `ChatStorage` becomes `DynamoDBHistoryStore` behind the protocol
   with identical keys and TTL. *Trade-off*: an AWS deployment that upgrades without setting the
   variable silently moves history to Redis (24 h data, fail-open — no loss of function, only of
   in-flight history). Mitigations: startup INFO log `history_backend=<name>`, `/ready` detail names
   the backend, CHANGELOG/upgrade note, and the release runbook checks the AWS overlay sets it.

5. **Provider-by-env, same as spec 28.** One env var selects the backend at startup through a small
   factory; misconfiguration fails loudly. No per-request switching.

6. **`${VAR}` expansion is restricted to address fields.** Allowed: datasource `url`, `name`,
   `headers` values, `tool_arguments` values. Not allowed anywhere else — in particular never in
   `tools`, `read_only`, `capability_tier`, `required_env`, `skills`, `model`. Syntax `${VAR}` only
   (no defaults, no nesting, no shell semantics); a literal `$` elsewhere is untouched.
   *Rationale*: environments change where, never what (Thesis).

7. **Unset variable disables the datasource, not the agent.** Matches fail-open for availability:
   the agent still answers (and calibrated honesty already makes it say it could not verify, as seen
   live in the 2026-11-24 off-AWS cluster test). Not silent: startup WARNING `datasource_unconfigured agent=… datasource=… var=…`
   and a `/ready` `checks.agents.degraded` list. `/ready` stays 200 — a missing optional MCP is a
   degraded capability, not an outage. *Trade-off*: a typo in a profile is a warning, not a crash;
   mitigated by the chart render test asserting every `${VAR}` used by enabled agents is set.

8. **Agent enable/disable override by env.** `AGENT_<NAME>_ENABLED=false` (upper-cased, `-`→`_`)
   overrides `enabled:` in `agent.yaml`. Can only turn an agent off or back on; cannot change
   anything else.

9. **Chart `agentOverrides` renders env vars only.** No ConfigMap copies of prompts. `agentsSource`
   (configmap/git) keeps working for teams that do want full custom agents; overrides apply on top.

10. **Secrets in headers come from env, never from values.** A header like
    `Authorization: Bearer ${GRAFANA_MCP_TOKEN}` expands from an env var fed by a Secret
    (`existingSecrets`). Expanded header values are never logged or exposed in `/ready`.

## Invariants

- Read-only posture (invariant 5): the tool allowlist is only ever read from the versioned
  `agent.yaml`; no env, override or profile can add a tool. Test-proven (AC 7).
- Fail-open for availability / fail-closed for security (invariant 6): history backend loss → empty
  history; missing MCP → datasource off; security layers unchanged.
- Per-agent isolated history (invariant 2) holds on every backend.
- Deterministic keys (invariant 7): any derived Redis/Postgres key uses explicit string composition
  or `hashlib.sha256`, never `hash()`.
- No hardcoded endpoints that have env vars (Prohibitions): default MCP URLs move to env.

## Verification

- Unit: each backend against the same contract test suite (save, last-N per agent, last-N all
  agents, isolation, expiry), Redis via fakeredis, Postgres via a test container in `make test`
  only if already available, otherwise a separate `make test-postgres` target.
- Unit: expansion (allowed fields, unset var, literal `$`, forbidden field attempt → startup error,
  secret not in logs).
- Chart: `helm lint` + render tests for both profiles; assertion that every `${VAR}` referenced by
  enabled agents is set in the profile.
- Live (a Kubernetes cluster outside AWS, off-AWS profile with its placeholders filled by the
  deploying team): `/ready` 200 with no DynamoDB, two-turn conversation shows history, one
  agentic query calls a real MCP tool through an OpenAI-compatible LLM (closes the gap left by the
  2026-11-24 test).

## Risks

| Risk | Mitigation |
|------|------------|
| Expansion becomes a way to widen capabilities | Field allowlist + startup rejection + security review task |
| Profile typo silently disables an MCP | WARNING + `/ready` degraded list + chart render assertion |
| Redis memory growth from history | 24 h EXPIRE per key + last-N cap on write (trim sorted set) |
| AWS upgrade without `HISTORY_BACKEND=dynamodb` moves history to Redis | Startup log + `/ready` backend name + upgrade note + RELEASE.md check |
| Behavior drift between backends | One shared contract test suite run against all three |
| Secret header value leaks in logs | Never log expanded values; test asserts absence |
