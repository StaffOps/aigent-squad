# Handoff — sessions 2026-06-16 → 2026-07-11

Estado para retomar. O que foi feito, o que ficou pendente, e próximos
passos priorizados.

---

## Done — session 2026-07-11 (spec 14 Phase 6: entry-point findings A/B/C/D CLOSED)

**The 0.4.0 gate work.** Pipeline dev→test→security completed for the four
homologation findings. Detail in `specs/14-security-hardening/tasks.md` +
`design.md` (new "Phase 6 — Entry-point hardening" section).

### Shipped (uncommitted, on `dev` working tree)
- **Fix A+C** — `classifier.classify` accepts + forwards `user_id`/`session_id`
  to `bedrock.invoke`: classifier-stage guardrail blocks attributable, Haiku
  classifier tokens now budget-counted. Defaults preserved.
- **Fix B** — `InputScanner` at `supervisor.process_request` (after budget
  check, before force_agent/investigate/classify; `agent_id="supervisor"`).
  Normalized text replaces `user_input` downstream incl. saved history.
- **Fix D** — oversized `ValueError` removed from `generic_agent`;
  `scanner:oversized` fail-closed 403 is the single enforcement point.
- **Tests** — 11 new (`tests/test_spec14_entrypoint.py`, independent author) +
  `test_generic_agent.py` updated to the new oversized contract.
- **Security review** — independent, **APPROVE-WITH-NITS**; A/B/C/D confirmed
  CLOSED with code-path evidence.
- **Docs** — `docs/SECURITY.md` §S4 "Known gap" → "Entry-point hardening
  (closed)"; spec 14 tasks.md findings marked CLOSED + review record.

### New follow-up findings from the review (OPEN, in spec 14 tasks.md)
- **E (MEDIUM)** — synthesizer + `_synthesize_rca` invoke Bedrock with empty
  `session_id` → unattributable OUTPUT blocks + Sonnet synthesis tokens escape
  the budget; investigation books evidence to a `-inv-` bucket `check_budget`
  never reads.
- **F (MEDIUM)** — `/alerts/incoming` → `run_investigation` bypasses entry L2;
  raw alert-derived symptom reaches RCA synthesis (only raw L1). Fix: scan the
  symptom at the top of `run_investigation`.
- Decide: fold E/F into the `0.4.0` gate or ship 0.4.0 with A/B/D closed and
  track E/F for 0.4.x.

### Also this session
- Pushed the 4 pending commits (`9bda015`→`63f1d5c`) to GitHub `dev` — CI green
  (Test + SAST). The GitLab overlay commit `c1c9195` push status: still pending
  (not in this repo).
- Full suite before fixes: 674 passed / 93.91% cov. Re-run with the 11 new
  tests: pending at handoff-write time (background).

### Next
1. ✅ Committed + pushed (`7a37808` → GitHub `dev`, CI green: Test 1m30s + SAST).
2. ✅ Re-homologated in devops-core (2026-07-11). Rebuilt multi-arch image with the
   Phase-6 fixes → Harbor `labs/aigent-squad:0.3.0-dev` digest `sha256:657d9a35`;
   rolled out gateway+supervisor (2/2). Attack battery via `/query`: **8/8 vectors
   block, all attributable** (real session_id) — homoglyph/zero-width now blocked
   pre-route (folded at supervisor entry → classifier guardrail), oversized now 403
   (`scanner:oversized`). A/B/C/D confirmed CLOSED in-cluster. Evidence table in
   `specs/14-security-hardening/tasks.md` ("Cluster re-homologation"). Repro script:
   scratchpad `homolog.py`.
3. ✅ **Findings E1 + F CLOSED** (2026-07-11, uncommitted on `dev` working tree).
   - **F** — `_scanner.scan(symptom, agent_id="investigation")` at the top of
     `run_investigation` (`investigation.py`): single choke point covering
     `/alerts/incoming` (was un-normalized → only raw L1) + the investigate path
     (idempotent re-scan) + future callers. Fail-closed → 403; normalized symptom
     propagates. The `/alerts` batch handler's `except Exception` turns a block into
     "no RCA for that alert" (audit already emitted) — correct fail-closed for a
     batch webhook.
   - **E1** — `synthesizer.synthesize` + `_synthesize_rca` now forward
     `agent_id`/`user_id`/`session_id` to `bedrock.invoke` (attribution +
     synthesis-token budget). Call sites `agent.py:_fan_out` + `run_investigation`
     pass the real session.
   - **E2** carved to **0.4.1** (evidence fan-out books to the `-inv-` budget bucket;
     needs decoupling budget-session from history-session — not mechanical). Detail
     in `specs/14-security-hardening/tasks.md`.
   - Tests: `tests/test_spec14_ef.py` (6, independent author). Suite **691 passed /
     94.24%**, lint clean. Self-reviewed for security; **independent security review
     + cluster re-homologation of E1/F still pending** before the tag.
4. ✅ **E1/F committed + pushed + cluster-homologated** (2026-07-12).
   - Commit `be32491` → GitHub `dev`, CI green (Test + SAST).
   - Image rebuilt with E1/F → Harbor `labs/aigent-squad:0.3.0-dev` digest
     `sha256:e3e5948`; rolled out gateway+supervisor (2/2).
   - Re-homologation: base 8-vector battery still 8/8 (no regression). `/alerts/incoming`
     with base64+homoglyph symptoms → `input_scanner_block agent=investigation` (base64) +
     folded-then-guardrail-blocked (homoglyph); webhook `HTTP 200 triggered:0`, no RCA,
     all audited. **Fix F proven: the alert path now has entry-stage L2.** Evidence in
     `specs/14-security-hardening/tasks.md`.
5. **0.4.0 gate FULLY MET** — A/B/C/D + E1/F closed and cluster-homologated; E2 carved to
   0.4.1. **Next: cut `0.4.0`** (via the `release` skill — dev→main→tag→chart→cluster;
   needs go-ahead). Then: aws-agent `<use_mcp_tool>` XML leak, finops↔Athena, finding E2.

> Local-access note: this machine's `aws` cli is 2.6.1 (2022) — emits `v1alpha1`
> ExecCredential that kubectl 1.34 rejects. A shim in scratchpad rewrites it to
> `v1beta1`; cluster access also required mapping the SSO admin role (done by user
> mid-session). Upgrading the aws cli removes the need for the shim.

---

## Done — session 2026-07-02 → 2026-07-03 (spec 14 complete + cluster homologation)

**Milestone: spec 14 (anti-prompt-injection defense-in-depth) is functionally
complete (Phases 1–5) and homologated in the real cluster.** Plus Redis
StatefulSet, ElastiCache validate-and-destroy, spec 11, and budget TOCTOU.

### Shipped + committed + CI green (on `dev`)
- **Spec 11** — model tiering (Haiku classifier / Sonnet agents) + prompt caching
  + token budget; Haiku 4.5 pricing corrected ($1/$5/$0.10).
- **Budget TOCTOU (T19d)** — `check_budget` now atomic via Lua `EVAL`
  check-and-reserve; `fakeredis[lua]` added to CI.
- **Redis in-cluster** — Deployment → **StatefulSet + PVC** (chart 0.9.2).
  Homologated: AOF persistence survives pod delete (keys + budget counter
  reloaded from PVC). Running as `aigent-squad-redis-0`.
- **ElastiCache** — module applied (Valkey 8.0.1, tested PING/SET/GET from
  cluster) → **destroyed** (zero cost, confirmed via AWS). Module stays
  validate-only in `infra/terraform/elasticache/`.
- **Spec 14 Phase 2** — canary tokens (inject into infra_data + fuzzy detect) +
  output filter (PII/secret/canary, Luhn on credit cards).
- **Spec 14 Phase 3** (`59386e8`) — `InputScanner` L2 (NFKC + zero-width/RTL-bidi
  strip + homoglyph fold + cheap heuristics), wired in `generic_agent`. Task 9
  reused the spec-31 `AdmissionGuard` (rate+budget). 88 tests, 100% cov.
- **Spec 14 Phase 4** (`c7b7b61`) — `tests/test_attack_suite.py` deterministic CI
  gate: 5 languages × obfuscations, 134 pass + 19 xfail-by-design. L3 reviewed
  (no change — sound). RTL/bidi strip added (security LOW-1).
- **Spec 14 Phase 5** (`fea17d2`) — `docs/SECURITY.md` §S4 rewritten (L1–L6 +
  STRIDE + fail-closed/open); `READ_ONLY_POLICY.md` cross-ref; ROADMAP updated.

### Cluster homologation (0.3.0-dev, digest `fbe381fb`, devops-core)
- Rebuilt image with InputScanner; rollout of supervisor + gateway (2/2 each).
- **Tag mismatch fixed**: chart appVersion is now the stable `0.3.0`, but local
  dev runs `0.3.0-dev` on Harbor → overlay pinned `tag: 0.3.0-dev` for
  gateway+supervisor (committed in `k8s-setup`, `c1c9195`, local only).
- Attack battery via `/query` — confirmed real flow: `/query` → supervisor →
  `classifier.classify` (raw, L1 only) → if routed, `generic_agent` (L2+L1).
  base64→L2 block ✅; homoglyph/zero-width→routed, caught at worker L1 post-L2 ✅
  (proves L2 value); fullwidth/plain→classifier L1 ✅; repeated-char→L2 ✅.

### 4 OPEN findings from homologation (deferred — security-critical path)
Documented in `specs/14-security-hardening/tasks.md` (attribution table + detail):
- **A (MEDIUM)** — `classifier.classify` drops `user_id`/`session_id` → classifier
  guardrail blocks log empty session (unattributable, breaks audit invariant).
- **B (MEDIUM)** — homoglyph evades the classifier L1 (routed through, only caught
  at worker after L2 folded). The supervisor-entry L2 gap, empirically proven.
- **C (LOW)** — same root as A: classifier invoke tokens not counted vs budget
  (`bedrock.py:165` skips on empty session).
- **D (MEDIUM)** — oversized input raises `ValueError` at `generic_agent.py:52`
  BEFORE the scanner → not a 403, degrades to HTTP 200 fallback; `scanner:oversized`
  is dead code.
- Proposed fixes: A+C (propagate ids), B (wire InputScanner at supervisor entry),
  D (drop redundant ValueError). Pipeline dev→test→security when picked up.
- **Side observation (not spec-14)**: aws agent echoes raw `<use_mcp_tool>` XML in
  the response instead of executing — track separately.

### NOT pushed yet
Two local commits await push approval:
- app `fea17d2` (docs/specs, → `dev` on GitHub, will run CI)
- overlay `c1c9195` (→ `main` on GitLab)
The `k8s-setup` `staffops/anomaly-detection/values.yaml.gotmpl` shows as modified
but is **not mine** (pre-existing drift) — left untouched.

---

## Done — session 2026-07-01 (spec 31/29 cluster-validated + 0.3.0-dev)

**The squad is running in a real cluster (devops-core) end-to-end.** First time
the code left dry-run/docker-compose. Everything below was validated with real
queries hitting Bedrock + real AWS inventory via IRSA.

### Deployed to devops-core (namespace `staffops`, inProcess topology)
- **Helmfile install** under `02-KUBE/00-CONFIG/k8s-setup/staffops/` (sibling of
  the other add-ons): two-doc `helmfile.yaml.gotmpl` (environments + releases) +
  `aigent-squad/values.yaml.gotmpl`. Follows the `dependency-track` pattern.
- **Image**: built locally (multi-arch amd64+arm64) with the gateway, pushed to
  Harbor `harbor.bigdatacorp.com.br/labs/aigent-squad:0.3.0-dev`. The overlay
  sets `global.image.registry` + `repository` with **no per-service tag** — the
  version comes from `Chart.appVersion` (0.3.0-dev).
- **Terraform applied** (12 resources): IRSA role `aigent-squad-irsa` (Bedrock
  invoke + ApplyGuardrail + DynamoDB + read-only inventory), DynamoDB
  `agent-sessions`, Bedrock Guardrail (`w11piaof9jp1` v1, PROMPT_ATTACK MEDIUM),
  2× Bedrock VPC endpoints, 1 Application Inference Profile (cost attribution).
- **Secrets**: `STAFFOPS_AIGENT_SQUAD` in Secrets Manager (INTERNAL_API_TOKEN +
  SUPERVISOR_INTERNAL_TOKEN) via ExternalSecret → ClusterSecretStore `aws`.
- **Routing**: Istio Gateway API (HTTPRoute on `istio-dvps-internal`, listener
  `https-bdc-app-br`) — TLS terminated at the gateway (Let's Encrypt, verified).
  The chart supports `routing.type: ingress|gatewayapi` in the same values.
- **agentsSource**: validated BOTH `configmap` (inline) and `git` (initContainer
  clones the GitLab agents repo `.../devops/aigent-squad.git`, tokenSecret).

### Fixes found while validating in-cluster
Chart (helm-charts, bumped to **0.9.0**, first cluster-validated release):
- ExternalSecret `v1beta1` → `v1` (ESO ≥0.10 dropped v1beta1).
- In-cluster Redis: disable persistence + emptyDir `/data` (readOnly rootfs
  tripped `stop-writes-on-bgsave-error`).
- git-sync clones into `/agents/.repo` (was `/tmp/repo` — readOnly rootfs).
- `agents[]` default config updated to the spec-22 schema (domain/capabilities/
  datasources objects) — old string list crashed the supervisor (Pydantic).
- CostCenter default neutralized (`CHANGE-ME`); real value lives only in the
  private overlay (org tag policy rejects arbitrary values).

App (staffops-aigent-squad):
- `Boto3Adapter` builds clients with explicit `region_name=settings.aws_region`
  (botocore reads AWS_DEFAULT_REGION, not AWS_REGION → NoRegionError). EC2/RDS/CE
  now return real data.
- `Classifier._extract_json`: strips ```json fences / preamble before json.loads
  → structured parse instead of the low-confidence "Fallback parsing" path.
  Confirmed live: confidence 0.98, correct routing.
- Terraform: `bedrock-aip` output `arn`, IAM ApplyGuardrail + DescribeTable,
  example parametrized (ns/SA), CostProject uppercase. `*.auto.tfvars` gitignored
  (was leaking real VPC/subnets/cost_center).

### Homologated (real queries via the public endpoint)
- "AWS services I use most" → 695 EC2 / 670 S3 / 100 IAM / 10 RDS (real).
- "cost trend 30d" → $191,225.98 from Cost Explorer; confidence 0.98.
- Bumped gateway `FIRST_BYTE_TIMEOUT` 15→30s (finops Athena+Bedrock ≈17s).

### NOT committed yet (single milestone commit pending)
All the above is on disk, uncommitted (user wants one big commit). Commit-time
TODO: publish chart 0.9.0 + revert the local-path override in the helmfile;
neutralize `gateway.image.repository` (still a personal Docker Hub repo in the
public values); revert overlay `pullPolicy` Always→IfNotPresent; **revoke the
two PATs pasted in chat** (GitHub ghp_… + GitLab glpat-…).

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
| 3 | **Helm install on a real cluster** | ✅ DONE (2026-07-01) — deployed to devops-core via helmfile (`k8s-setup/staffops/`), image from Harbor labs. `ct install` on kind still not wired, but a real EKS install is validated. |
| 4 | **LibreChat end-to-end** | Bridge unit-tested + `/v1/models` validated via the public Istio endpoint (HTTP 200, 6 models). Not yet wired to a live LibreChat instance. |
| 5 | **Spec 31 gateway not cluster-validated** | ✅ DONE (2026-07-01) — gateway + supervisor running in devops-core, end-to-end queries hitting Bedrock. Image `0.3.0-dev` on Harbor. Chart bumped to 0.9.0 (uncommitted). |
| 6 | **Budget TOCTOU (spec 31 L3)** | ✅ DONE (2026-07-02) — atomic Lua `EVAL` check-and-reserve; `fakeredis[lua]` in CI. |
| 7 | **Spec 14 Phases 2–5** | ✅ DONE (2026-07-02→03) — L1–L6 all shipped, multilingual attack-suite CI gate, SECURITY.md defense-in-depth, cluster-homologated. |
| 8 | **Distributed topology (code)** | Chart renders it but supervisor only routes in-process — needs a RemoteAgent HTTP client. Deferred to backlog (ADR-001 favors in-process). See ROADMAP backlog. |
| 9 | **finops ↔ Athena** | finops agent declares an `athena` datasource but IRSA has `enable_athena_finops=false` → AccessDenied. Enable Athena or drop the datasource. See ROADMAP backlog. |
| 10 | **Spec 14 entry-point findings A/B/D** | Homologation (2026-07-03) found the supervisor/classifier entry is under-instrumented + under-protected: A (classifier drops user_id/session_id → unattributable audit), B (homoglyph evades classifier L1 — worker-only L2), D (oversized ValueError pre-empts scanner → HTTP 200). Fixes proposed, deferred. Detail in spec 14 tasks.md. **Next security work.** |
| 11 | **Push pending** | app `fea17d2` + overlay `c1c9195` committed locally, awaiting push approval (GitHub `dev` + GitLab `main`). |

---

## Next specs (priority order)

> Done since last handoff: **spec 14 complete (Phases 1–5, L1–L6 + attack-suite
> CI gate + docs)** and cluster-homologated on devops-core; spec 11 (model
> tiering); budget TOCTOU (Lua atomic); Redis StatefulSet+PVC (chart 0.9.2);
> ElastiCache validate-and-destroy. Image `0.3.0-dev` rebuilt with InputScanner.

1. **Push the two local commits** — app `fea17d2` (→ GitHub `dev`, runs CI) +
   overlay `c1c9195` (→ GitLab `main`). Confirm CI green after.
2. **Spec 14 entry-point fixes A/B/D** (Pending #10) — the next security work.
   Pipeline dev→test→security: propagate `user_id`/`session_id` through the
   classifier (A+C), wire `InputScanner` at `supervisor.process_request` (B),
   drop the redundant oversized `ValueError` (D). Re-homologate after.
3. **Cut `0.4.0`** — gated on closing A/B/D (anti-injection validated end-to-end
   at the entry point). Don't bump before.
4. **Side issue** — aws agent leaks `<use_mcp_tool>` XML in the response instead
   of executing the tool. Investigate separately.
5. **Distributed topology (code)** + **finops↔Athena** — backlog (deferred).
6. **Spec 28 — RCA benchmark** (OpenSRE CloudOpsBench pattern).
7. **Branch protection** — once the GitHub plan allows (Pending #1).

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
