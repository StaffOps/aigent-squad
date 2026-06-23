# Handoff — sessions 2026-06-16 → 2026-06-23

Estado para retomar. O que foi feito, o que ficou pendente, e próximos
passos priorizados.

---

## Done — session 2026-06-22 / 2026-06-23

### Root doc cleanup (spec 24)
- Deleted stale `VERSIONS.md` + `GENERIC_VERSION.md`; archived
  `IMPLEMENTATION_HISTORY.md` → `archive/`. Refs updated in README + ROADMAP.

### Spec 14 Phase 1 — Bedrock Guardrail + fail-closed (DONE ✅)
- `infra/terraform/guardrail/`: `aws_bedrock_guardrail` (PROMPT_ATTACK HIGH
  multi-language, PII BLOCK, content filters, optional denied topics) + published
  version; outputs id/version.
- `src/core/guardrail.py`: `GuardrailClient.apply()` input+output via
  `apply_guardrail` API; **fail-closed** (block OR unavailable → 403, never
  bypass); structured audit log (no cleartext payload, sha256 digest).
- Wired into `bedrock.invoke` (chokepoint for all LLM calls); does NOT trip the
  circuit breaker. Propagated through classifier/supervisor/investigation → 403
  at `/internal/process`. 99% cov on guardrail. Independent author + review.
- Phases 2–5 (canary, output filter, rate/budget L6, input scanner,
  multi-language suite) still pending.

### Spec 31 — Edge gateway + worker pool (L1–L4 DONE ✅, L5 partial)
- **L1/L2**: `src/gateway/` — thin FastAPI front door (edge auth, `WorkerPool`
  backpressure, OpenAI /v1 + /query + /jobs/{id}/cancel). Supervisor became
  backend-only (`:8001`, `/internal/process` + `/internal/agents`, gateway-only
  via `SUPERVISOR_INTERNAL_TOKEN`); public routes moved to the gateway.
- **L3**: `src/core/rate_limiter.py` — `AdmissionGuard` (per-user rate + global
  daily budget, Redis, **fail-open**) + `estimate_cost`. Enforced at the gateway
  before forward (429/503 with headers). Budget TOCTOU → hardening T19d.
- **L4**: chart in `StaffOps/helm-charts` (`aigent-squad` 0.8.0) — gateway +
  supervisor in the `services` map (KEDA per tier), supervisor NetworkPolicy
  locked to gateway-only, gateway `networkPolicy.allowFrom` opens it to in-cluster
  callers (Alertmanager, anomaly-detection, Falco). CostCenter `devops-team`.
  docker-compose two-tier + mcp-server repointed to the gateway. Both topologies
  (inProcess + distributed) aligned.
- **L5 partial**: `docs/site/architecture.md` + `metrics.md` rewritten two-tier.
  Pending: T21 (k6 load test), T23 (final independent review).
- Round-table sign-off (dev+security+sre+gitops) settled the 3 design questions.
- Concurrency model: per-replica pool (fail-open-to-reject) + global Redis
  rate/budget (fail-open-to-allow), distinct from spec-14 guardrail (fail-closed).

### Shipped (2026-06-23)
- PR #17 `dev → main` merged → `build.yml` built scan-gated multi-arch image with
  `src/gateway` → Docker Hub tags `latest` + `ba13399` (the `0.2.0` tag does NOT
  contain the gateway). helm-charts `main` pushed (chart 0.8.0), CI green.
- CI fixes found by watching pipelines: ruff F401 in L3 tests; added
  `fakeredis`+`respx` to `test.yml` (gateway test deps); ct-values pinned both
  tiers to `autoscaling.kind=none` (no CRD on bare kind).

### Release note
- No version bump yet. The gateway is a **new tier → MINOR `0.3.0`** (not a
  PATCH), to be cut once validated in a cluster (per `version-management`).
  Until then the image `latest`/`sha` carries the gateway; chart `appVersion`
  stays `0.2.0` with a ⚠️ in the chart README.

---

## Done — session 2026-06-21 / 2026-06-22

### Release v0.2.0 (first tagged release)
- App tag `v0.2.0` → `release.yml` built scan-gated multi-arch image
  `karlipegomes/aigent-squad:0.2.0` (+ `latest`) + SBOM + GitHub Release.
- Chart `helm-charts/aigent-squad` → `version 0.7.0`, `appVersion 0.2.0`;
  `image.tag` removed so it inherits `appVersion` (renders `:0.2.0`, never
  `latest` in prod). chart-releaser published `aigent-squad-0.7.0`.
- `CHANGES.md` cut `[0.2.0]`; new work accrues under `[Unreleased]`.

### CI/CD — Model A (`docs/CI-CD.md`)
- **Scan-before-publish** on `build.yml` AND `release.yml`: build local →
  Trivy gate → push only if clean. Vulnerable image never reaches the registry.
- `release.yml` rewritten: tag-driven (`v*`)/manual, Docker Hub, SBOM, Release
  (replaced legacy ECR/SSH).
- `test.yml`: `guard` (main accepts PRs only from `dev`) + `dep_scan` (Trivy fs).
- `sast.yml`: **Bandit** (CodeQL needs GHAS — unavailable on private repo). 4
  reviewed B104 (`0.0.0.0` bind) suppressed with `# nosec`.
- `docs.yml`: deploy ONLY from `main` (was overwriting prod from `dev`);
  `mkdocs build --strict` on PRs. Fixed broken Architecture page (case collision)
  + LIBRECHAT external links → strict-clean.

### Specs
- **Spec 10 Phase 1** — efficiency/quality metrics (collect/llm duration,
  prompt size, investigation rounds). 246 tests.
- **Spec 30** — datasource cache wired into adapters (sha256 TTL, fail-open at
  the adapter boundary); `aigent.cache.hits/misses` now emitted; `tokens_saved`
  reassigned to spec 11. 266 tests, 92.62% coverage. Tests by an independent agent.

### Workflow change — solo dev
- `dev` is now committed to **directly** (no `feature → dev` PRs). Every push to
  `dev` runs lint + test+coverage + dep_scan + bandit. `main` keeps the PR gate.
  Documented in `docs/CI-CD.md` + README.

### Misc
- Apache 2.0 migration; MkDocs site live at `staffops.github.io/aigent-squad/`;
  CI on `DOCS_DEPLOY_TOKEN` (HTTPS private dep + BuildKit secret); CVE cleanup
  + `.trivyignore`.

> **Sync note:** as of 2026-06-22, `dev` is 2 docs-only commits ahead of `main`
> (the direct-commit-flow docs). They reach the published site on the next
> `dev → main`. Not urgent.

---

## Done — session 2026-06-16 / 2026-06-17 (historical)

- **Migration**: code moved from `AIgent-squad` (public, frozen) → private
  repo `staffops-aigent-squad` with full history.
- **Terraform**: `iam/`, `dynamodb/`, `bedrock/`, `bedrock-aip/`. All validated.
- **Fix Bedrock**: `BEDROCK_MODEL_ID` needs inference profile `us.` prefix.
- **MCP adapter** (`type: mcp`): agents as MCP clients, read-only allowlist.
- **Skills** (spec 26): lazy-loaded markdown knowledge. 100% cov.
- **Cost attribution** (spec 27): AIP per model + metrics with `agent_id`.
- **Spec 14 (security)**: written, NOT implemented.
- **Efficiency/cost steering**: `efficiency-cost.md` new pillar.
- **Competitive analysis**: `docs/COMPETITIVE-ANALYSIS.md`.
- **Helm chart** (`helm-charts/charts/aigent-squad` 0.4.0): `services` map,
  two topologies (inProcess/distributed), HPA/KEDA/none, Ingress/GatewayAPI/none.
- **Claude Code compatibility**: `CLAUDE.md` + `.claude/` mirror.
- **OpenAI-compatible bridge** (spec 29): `/v1/models` + `/v1/chat/completions`
  on supervisor; `docs/LIBRECHAT.md`.
- **i18n**: all active docs/steering/prompts translated to English.

---

## Done — session 2026-06-18

### Spec 07 — Readiness probes (COMPLETE ✅)
- `src/core/health.py`: `DependencyChecker` — async checks (Redis, DynamoDB,
  Bedrock creds, HTTP) with per-dep TTL cache (5 s) + timeout (2 s).
- `src/supervisor/server.py`: `/healthz` (liveness), `/ready` (readiness),
  `/health` legacy alias.
- `mcp-server/mcp-server.py`: same probe set; `/ready` checks supervisor.
- `docker-compose.yaml`: healthchecks → `/ready`.
- `tests/test_health.py`: 237 tests total, 92.46% coverage.
- `Dockerfile.test`: reproducible test image (python:3.11-slim + SSH);
  run via volume mount, no rebuild on code change.

### CI/CD — Docker Hub (COMPLETE ✅)
- `.github/workflows/build.yml`: `build` job (multi-arch `linux/amd64,linux/arm64`
  → `karlipegomes/aigent-squad:latest` + `:sha-<short>`) + `scan` job (Trivy
  CRITICAL/HIGH + CycloneDX SBOM).
- ECR removed — Docker Hub is the only registry.
- `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN` secrets configured on
  `StaffOps/staffops-aigent-squad`.

### Helm chart 0.6.0 (COMPLETE ✅)
- Default image: `karlipegomes/aigent-squad:latest` (Docker Hub, `registry: ""`).
- `Chart.yaml` home/sources → `StaffOps/` org.
- All READMEs updated (root + aigent-squad + staffops-anomaly-detection):
  `staffops.github.io`, `StaffOps/` org, chart badge 0.6.0.
- GitHub Pages configured: `staffops.github.io/helm-charts` (source: `gh-pages`
  branch, legacy mode). Branch `gh-pages` created.
- Helm repo: `helm repo add staffops https://staffops.github.io/helm-charts`.

### Spec 05 tasks.md cleanup
- T5 (Argo Rollouts) and T14 (per-env values files) removed — out of scope.
- "provider-agnostic" language removed from all chart files.
- Completed tasks marked `[x]`.

### Dockerfile → Alpine multi-stage (COMPLETE ✅)
- Builder: `python:3.11-alpine` + gcc/libffi/openssl/git/ssh (build-only).
- Runtime: clean `python:3.11-alpine` — no perl, no ncurses, no build tools.
- `wheel>=0.46.2` + `setuptools>=79.0.1` upgraded to fix CVE-2026-24049 and
  CVE-2026-23949.
- Remaining OS CVEs (perl, ncurses, sqlite) have `status=affected` in Debian 13
  — irrelevant now since Alpine has none of those packages.
- `Dockerfile.test` stays on `python:3.11-slim` (OTel/pkg_resources constraint).

### Org migration
- `staffops-aigent-squad` remote → `git@github.com:StaffOps/staffops-aigent-squad.git`
- `helm-charts` remote → `git@github.com:StaffOps/helm-charts.git`

---

## Current branch state

`dev` and `main` are in sync as of 2026-06-23 (PR #17 merged). The gateway image
is on Docker Hub (`latest` + `ba13399`); helm-charts `main` has chart 0.8.0.
Next work resumes on `dev`.

---

## Pending / Blockers

| # | Item | Notes |
|---|------|-------|
| 1 | **Branch protection NOT enforced** | Needs GitHub Pro/Team or a public repo (private Free → 403). `guard` job + CI checks run, but a direct push to `main` is technically possible. Deferred. |
| 2 | **`build.yml` uses personal Docker Hub** | Image is `karlipegomes/aigent-squad`; migrate to a StaffOps org namespace. |
| 3 | **Helm install on a real cluster** | `ct install` on kind/EKS needs a cluster + a real image in `ct-values` (currently the placeholder `aigent-squad/supervisor:0.1.0`). Validated only via `ct lint`/template/parse locally. Deferred. |
| 4 | **LibreChat end-to-end** | Bridge unit-tested only; now behind the gateway — not validated against a live LibreChat. |
| 5 | **Spec 31 gateway not cluster-validated** | Code + chart done; never deployed. The `0.3.0` release is gated on a real-cluster run. |
| 6 | **Budget TOCTOU (spec 31 L3)** | `check_budget` GET→compare→INCR not atomic; tracked as T19d (needs EVAL/Lua + real-Redis test; test fakeredis lacks `eval`). |
| 7 | **Spec 14 Phases 2–5** | Only L1 (guardrail + fail-closed) done; canary/output-filter/rate-budget/input-scanner/multi-language suite pending. |

---

## Next specs (priority order)

> Done since last handoff: **root doc cleanup**; **spec 14 Phase 1** (Bedrock
> Guardrail + fail-closed + audit); **spec 31 L1–L4** (edge gateway + worker pool
> + admission + two-tier Helm) + L5 docs partial; PR #17 shipped (gateway image on
> Docker Hub, chart 0.8.0).

1. **Spec 31 L5 finish** — k6 load test (T21) + final independent review (T23) + real-cluster `ct install`.
2. **Spec 31 release** — cut `0.3.0` once the gateway runs in a cluster (app image + chart appVersion).
3. **Spec 14 Phase 2** — canary tokens + output filter (exfiltration defense).
4. **Spec 11 — bedrock-resilience-cost** — Haiku classifier, prompt caching, model tiering.
5. **Spec 28 — RCA benchmark** (OpenSRE CloudOpsBench pattern).
6. **Branch protection** — once the GitHub plan allows (Pending #1).

---

## Metrics gaps — closed by spec 10 Phase 1 (2026-06-18)

- [x] `aigent.tokens.total` / `aigent.cost.estimated`: `model` label — already present in `bedrock.py` (spec 27).
- [x] `aigent.prompt.size_tokens` histogram — detect bloated prompts (bedrock.py).
- [x] `aigent.investigation.rounds` histogram — real rounds vs cap (investigation.py).
- [x] `aigent.llm.duration` separate from `aigent.collect.duration` — both shipped (bedrock.py + generic_agent.py).
- [x] `docs/METRICS.md`: reorganized by purpose (RED / Efficiency / Quality / Domain) + "Known gaps" section.
- [ ] `aigent.cache.hits/misses`: **defined but never emitted** — datasource cache not wired into adapters. Deferred.
- [ ] `aigent.cache.tokens_saved` counter: not defined; depends on datasource cache. Deferred to a future `datasource-cache-layer` spec.

---

## What may have been missed

- **Deploy to AWS**: nothing applied. All Terraform is `validate`-only.
  The jump from "code" to "running in prod" is the biggest remaining work.
- **Spec 14 is design only** — easy to forget security "is done" when only
  the plan exists.
- **Historical specs 01-25 remain in PT** (frozen record, low priority).
- **`staffops-agent-config` agents** have `datasources: []` and keyword
  placeholders — need real tuning before production use.
