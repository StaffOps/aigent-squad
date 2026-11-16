---
name: verify
description: Bring the local aigent-squad stack up and prove it works end-to-end (gateway → supervisor → Bedrock). Use after any change to src/, agents/ or docker-compose.
---

# Verify the squad locally

1. `make up` — starts the two-tier stack, waits for gateway `/ready` (60s max).
2. `make smoke` — health + one real `/query` (Bedrock round-trip) + `/v1/models`.
3. If code changed, also: `make test` (full gate) and `make lint`.

## When it fails

Every smoke failure prints a playbook pointer. Diagnose with the symptom →
cause → fix table in **AGENTS.md → Playbook**. Steps:

1. Read the failing step's output and find the matching Playbook row.
2. `docker compose logs gateway supervisor` for the server-side error.
3. Apply the row's fix, then re-run from step 1 of this skill.

## Definition of verified

`make smoke` green AND (if code changed) `make test` green. A stubbed test run
prints a warning — then CI is the real verdict: `gh run list -L 3`.
