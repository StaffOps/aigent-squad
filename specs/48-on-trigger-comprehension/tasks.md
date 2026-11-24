# Tasks: On-trigger comprehension

Legend: `[ ]` pending · `[~]` partial · `[x]` done. `(D#)` = design decision, `(AC#)` = acceptance
criterion in `requirements.md`.

> **Status (not-started):** writing this plan is not a decision to build. The draft's open
> questions were answered by owner decision 2026-11-24 (see `design.md` "Owner decisions").

---

## Phase 1 — Entity registry + metric verdicts in chat (B-21, B-22 core, B-24)

- [ ] T1.1: Registry schema + loader (`src/core/comprehension/registry.py`): Pydantic v1 schema,
  `ENTITY_REGISTRY_FILE`, validation rules, `comprehension_disabled` log when unset; invalid or
  unreadable → comprehension off + `comprehension_registry_invalid` WARNING (no values) + `/ready`
  informational detail (200) + `registry_load{result}`; squad starts normally; `registry_hash`.
  (D1, D2 · AC1)
- [ ] T1.2: Generic example `config/entity-registry.example.yaml` — placeholders only; entry #1 =
  the squad itself with its `aigent_*` metric names verified per TRIGGERS.md naming conventions.
  Repo-scrub check: no environment-specific names. (D1 · AC2)
- [ ] T1.3: Catalog `catalog.py` with EVIDENCE-MODEL ids + B-24 extension (M14, M15, I9). (D15 · AC11)
- [ ] T1.4: Deterministic resolution `resolve.py` (text alias + alert labels, ambiguity, caps).
  (D3 · AC3)
- [ ] T1.5: Query rendering `query.py` — registry values only, safe charset. (D4 · AC5)
- [ ] T1.6: `MetricsSource` Protocol + `PrometheusHttpSource` (GET `query_range`, env address +
  token file, timeouts, step selection ≤ 2 000 points). (D5, D6 · AC6)
- [ ] T1.7: Verdict methods `verdict.py` (static, robust_z, seasonal, derivative, MAD floor,
  sustain, since, min history, all `no-baseline` reasons). Thresholds/sustain resolved per signal >
  registry `defaults` > code defaults (z 2 / 3, 3 of 5). Pure functions. (D6, D7 · AC4)
- [ ] T1.8: Engine `engine.py`: concurrency, per-request budget, Redis baseline cache (sha256 keys,
  method TTLs), fail-open paths, `registry_signal_no_data` rate-limited log. (D9, D10 · AC7)
- [ ] T1.9: Supervisor wiring: run resolution + verdicts concurrently with the classifier; pass
  `Verdict[]` to chosen agents (auto-route, fan-out, force_agent paths). (D3, D10)
- [ ] T1.10: `GenericAgent` rendering: `<baseline_verdicts>` block in the tagged data region, char
  cap, `COMPREHENSION_INSTRUCTION` (env-overridable), block included in `effective_infra_data`;
  `x_aigent.comprehension` on non-streaming responses. (D8 · AC8)
- [ ] T1.11: Metrics `aigent.comprehension.*` (D18 table) in `metrics.py` + `docs/METRICS.md`.
  (AC12)
- [ ] T1.12: `scripts/registry_check.py` (read-only drift check) + `make registry-check`. (D12)
- [ ] T1.13: Eval: `evals/comprehension/prom_stub.py` + fixture registry + compose profile `eval`;
  golden questions with known verdicts (abnormal, normal, no_data, insufficient_history, ambiguous).
  (D11 · AC13)
- [ ] T1.14: Docs: `docs/BEHAVIOR-BASELINES.md` status, `docs/ARCHITECTURE.md`, AGENTS.md env table
  (`ENTITY_REGISTRY_FILE`, `COMPREHENSION_*`), `docs/HOW-TO-ENTITY-REGISTRY.md`. (AC16)
- [ ] T1.15: Chart (sibling repo): `entityRegistry.existingConfigMap` mount + env; render test;
  values comment says the registry content lives in the deploying team's config repo. (D1 · AC2)

## Phase 2 — Alerts, investigations, snapshot (B-22 alert part, B-17)

- [ ] T2.1: `alert_handler`: resolve entity + signal from labels, compute current verdict, pass to
  `run_investigation(verdicts=…)`, report it in the result; never suppress. (D13 · AC9)
- [ ] T2.2: `run_investigation`: verdicts → `Evidence` per the D14 table; no-baseline listed as
  unverified in the RCA output. (D14 · AC10)
- [ ] T2.3: `snapshot.py`: lazy, TTL-cached, size-capped, built from cached verdicts only; injected
  into investigation + alert flows. (D16 · AC14)
- [ ] T2.4: Eval: replayed-alert golden case (verdict before investigation) + one RCA scenario
  whose expected confidence depends on a pre-filled verdict. (D11 · AC13)

## Phase 3 — Diff primitive + review loop (B-18, B-23 review)

- [ ] T3.1: `collect_diff` in the engine + block rendering; used by investigation for
  before/after windows. (D17 · AC15)
- [ ] T3.2: TRIGGERS.md rows: coverage, honesty (eval), freshness, no_data rate; precision as GAP
  until B-03 feedback exists. (D18 · AC12)
- [ ] T3.3: Precision measurement wired to the feedback signal — **blocked on B-03**; if B-03 has
  not landed when Phases 1–2 are done, move this to `deferred:` and the BACKLOG Deferred register.

## Phase 4 — Verification gates

- [ ] T4.1: **Independent test authorship** — a different session/agent than the implementer
  writes or extends the tests for `src/core/comprehension/` (invalid registry → disabled + WARNING +
  `/ready` 200 detail + metric with the rest of the squad unaffected, threshold precedence,
  query-injection proofs, verdict fixtures, fail-open paths); ≥ 90 % coverage via `make test`.
- [ ] T4.2: **Independent code review** — fresh context, against AGENTS.md invariants, this
  design and the acceptance criteria.
- [ ] T4.3: **Security review** (mandatory: new datasource / egress destination, alert labels as an
  input surface, new content in the model context, new response field) — focus: query injection
  from alert labels and user text, token-file handling and log redaction, registry cannot widen
  capabilities, data-region tagging of the verdict block, `x_aigent.comprehension` content.
- [ ] T4.4: Live verification: entity #1 (the squad) answers "is the gateway healthy?" with a
  computed verdict on a real cluster; one replayed alert shows the verdict before investigation;
  one `no-baseline` case surfaced in the answer. (AC17)

## Order

- T1.1 → T1.4/T1.5 (need the schema). T1.3, T1.6 and T1.7 are independent and can run in parallel
  with T1.1. T1.8 needs T1.5–T1.7. T1.9/T1.10 need T1.8. T1.11 lands with T1.8–T1.10.
  T1.12, T1.13 can start once T1.1 + T1.6 exist. T1.14/T1.15 last in Phase 1.
- Phase 2 starts after Phase 1 passes T1.13. T2.1 and T2.3 are independent; T2.2 after T2.1.
- Phase 3 after Phase 2. T4.1–T4.3 run at the end of each phase that ships code (per phase, not
  once); T4.4 after Phase 2.

## Future work (after Phase 1, not planned)

- Incident-window exclusion from baseline history via a KB lookup (BEHAVIOR-BASELINES §4) —
  deferred by owner decision 2026-11-24. Needs its own design pass (which KB records count as an
  incident window, cost of the lookup) before it becomes a task.
