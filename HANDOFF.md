# Handoff — sessions 2026-06-16 / 2026-06-17 / 2026-06-18

Estado para retomar. O que foi feito, o que ficou pendente, e próximos
passos priorizados.

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

## Current branch state (`dev`)

All the above is committed and pushed to `dev`. **Not yet merged to `main`.**
Merging `dev → main` will:
1. Trigger `build.yml` → build Alpine image + push to Docker Hub + Trivy scan.
2. CI `test.yml` will run lint + pytest (needs `OTEL_LIBS_DEPLOY_KEY` secret).

---

## Pending / Blockers

| # | Item | Notes |
|---|------|-------|
| 1 | **Merge `dev → main`** | Triggers first Docker Hub build. Lint passes. Test job needs `OTEL_LIBS_DEPLOY_KEY` secret on `StaffOps/staffops-aigent-squad` to pass. |
| 2 | **`OTEL_LIBS_DEPLOY_KEY` secret** | SSH deploy key for private `staffops-otel-libs` repo. Must be added manually (involves credentials). |
| 3 | **Helm T15** | `helm template \| kubectl apply --dry-run=client` — needs a cluster (kind/EKS). Deferred. |
| 4 | **`ct install` on kind** | `lint-test.yaml` workflow runs this on PR — will only pass when chart installs cleanly on vanilla cluster. |
| 5 | **LibreChat end-to-end** | Bridge unit-tested only; not validated against live LibreChat instance. |
| 6 | **Spec 14 Phase 1** | Security design only — Bedrock Guardrail + fail-closed still not implemented. |

---

## Next specs (priority order)

1. **Merge `dev → main`** — unblocks Docker Hub image + CI green.
2. **Spec 03 — cache/observability fix** — Redis cache + OTel metrics gaps.
3. **Spec 04 — harden security** — rate limit, per-user budget cap.
4. **Spec 14 Phase 1** — Bedrock Guardrail implementation.
5. **Spec 28 — RCA benchmark** (OpenSRE CloudOpsBench pattern) — quality gap.

---

## Metrics gaps (still open from 2026-06-17)

- [ ] `aigent.tokens.total` / `aigent.cost.estimated`: add `model` label.
- [ ] `aigent.prompt.size_tokens` histogram — detect bloated prompts.
- [ ] `aigent.investigation.rounds` histogram — real rounds vs cap.
- [ ] `aigent.llm.duration` separate from `aigent.collect.duration`.
- [ ] `aigent.cache.tokens_saved` counter.
- [ ] `docs/METRICS.md`: reorganize by purpose (RED / efficiency / quality).

---

## What may have been missed

- **Deploy to AWS**: nothing applied. All Terraform is `validate`-only.
  The jump from "code" to "running in prod" is the biggest remaining work.
- **Spec 14 is design only** — easy to forget security "is done" when only
  the plan exists.
- **Historical specs 01-25 remain in PT** (frozen record, low priority).
- **`staffops-agent-config` agents** have `datasources: []` and keyword
  placeholders — need real tuning before production use.
